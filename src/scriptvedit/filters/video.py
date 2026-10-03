# -*- coding: utf-8 -*-

import os
import subprocess
import math as _math
import warnings
import builtins as _builtins
from fractions import Fraction

# context は scriptvedit 内 import を持たない葉なので先頭で import できる。
from scriptvedit.context import current_project

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.expr import Const, Expr, Var, _BinOp, _FuncCall, _TimeVar, _UnOp, _UStr, _UValue
from scriptvedit.expr_scan import (_compile_u_eval, _pl_clip01, _pl_ends, _pl_max_abs,
                                   _pl_pieces, _pl_sub)
from scriptvedit.state import _REVERSE_MAX_SEC
from scriptvedit.validate import _parse_color_rgb


# draft レンダの縮小フィルタ（解像度を半分に。幾何は保持、偶数寸法に丸め）。
# 逐次レンダ（project.py の _build_ffmpeg_cmd）と並列チャンク（parallel.py）で
# 同一の式を使う。フィルタ文字列なのでフィルタ生成モジュールに置く
# （置き場所が project.py だと parallel.py → project.py の循環になる）。
_DRAFT_SCALE_FILTER = "scale=trunc(iw/4)*2:trunc(ih/4)*2"

# tpad の直前で揃えるタイムベースの分母（CLAUDE.md §4.7）。23.976 / 24 / 25 /
# 29.97 / 30 / 48 / 50 / 59.94 / 60 / 120fps の1フレーム長がすべて整数 tick になり、
# tpad がクローン1枚ごとに積算しても丸め誤差が出ない。
_TPAD_TB_DEN = 120000
# settb の分母の上限（AVRational の分母は int。超える最小公倍数は使わない）
_TB_DEN_MAX = 2 ** 31 - 1


def _tpad_timebase(fps):
    """tpad の直前へ入れる settb のタイムベース（'1/N'）を返す。

    基本は 1/120000。Project の fps の1フレーム長が 1/120000 で割り切れない
    （90 / 144fps 等）ときは、分母を fps の分子との最小公倍数へ広げる。
    checkpoint / web / compute / レイヤーキャッシュの生成物は Project の fps で
    書かれるので、どの fps でも誤差0になる。Project の fps と違い、かつ一般的でない
    （1/120000 で割り切れない）fps の素材だけ、1枚あたり 1/240000 秒以下の誤差が残る。
    fps だけで決まる（素材を probe しない）ので dry_run と実レンダで同じ文字列になる。
    """
    den = _TPAD_TB_DEN
    frac = Fraction(fps).limit_denominator(1001)
    if abs(float(frac) - float(fps)) < 1e-9:
        wide = _math.lcm(_TPAD_TB_DEN, frac.numerator)
        if wide <= _TB_DEN_MAX:
            den = wide
    return f"1/{den}"


def _unwrap_raw_stream_ref(label, kind):
    """生入力参照（[N:v] / [N:a]）ならブラケットを外したストリーム指定を返す。

    フィルタなしの生入力参照はフィルタグラフの出力ラベルではないため、
    -map にブラケット付きで渡すと "Output with label ... does not exist" で
    落ちる。ストリーム指定（N:v / N:a）へ外す。
    project.py と parallel.py のチャンク側の映像で共通利用する（音声は各入力に
    必ず aformat を付けたラベル付きチェーンになるので、生入力参照は現れない）。
    """
    inner = label[1:-1]
    if label.startswith("[") and inner.endswith(f":{kind}") \
            and inner[:-2].isdigit():
        return inner
    return label


# --- 共通式ヘルパー ---

def _u_expr(start, dur, var="t"):
    """エフェクト進行度 u の正規化式文字列を返す（clip((var-start)/dur, 0, 1)）。

    var: 時間変数名。通常フィルタ（scale/rotate/overlay等）は小文字 "t"、
    geq/blend 等 framesync 系は大文字 "T"（小文字 t は未定義）。
    カンマは filtergraph 用に "\\," へエスケープ済み。
    """
    # 戻り値は _UStr（str の派生）。秒で書く式（elapsed / ramp / keyframes_sec）が
    # 読む「経過秒」は u×dur へ戻さず、t-start をそのまま 0..dur へ clip して渡す。
    return _UStr(f"clip(({var}-{start})/{dur}\\,0\\,1)", dur,
                 sec=f"clip({var}-{start}\\,0\\,{dur})")


# geq の RGB 素通し接頭辞（アルファのみ加工する geq 式の共通部分。
# fade/wipe/opacity/rounded で `f"{_GEQ_RGB}:a='...'"` の形で使う）
_GEQ_RGB = "geq=r='r(X\\,Y)':g='g(X\\,Y)':b='b(X\\,Y)'"


# --- メディア情報ヘルパー ---

# メディア寸法probeのプロセス内メモ（(path, size, mtime_ns) → (w, h)）。
# _probe_video_codec（ffmpeg.py）と同じキー方式。probe失敗の (None, None) も
# メモする（同一ファイルへの繰り返しprobeと警告スパムを防ぐ）。
# ffprobe不在（FileNotFoundError）はメモしない（PATH修正後に回復できるように）。
_MEDIA_DIMS_MEMO = {}


def _get_media_dimensions(filepath):
    """メディアの幅・高さを取得 (ffprobe)

    dry_run では **キャッシュ生成物の寸法は常に不明扱い**にする。
    生成物の寸法は生成後にしか分からないため、キャッシュの有無で dry_run の出力が
    変わると「実レンダの後はスナップショットが落ちる」罠になる（scale の pad が
    付いたり付かなかったりする）。dry_run の出力はキャッシュ状態に依存させない。
    ※ 実レンダでは通常どおり probe され、pad（SEGVバリア）が正しく入る。
    """
    # dry_run の特殊分岐はプロセス内で状態が変わるため、メモ化の前に判定する
    if _is_pending_cache_path(filepath):
        # dry_run中の未生成キャッシュ予定パスはprobeしない（警告スパム防止）
        return None, None
    proj = current_project()
    if getattr(proj, "_dry_run", False) and _is_cache_artifact_path(filepath):
        return None, None
    try:
        st = os.stat(filepath)
        key = (filepath, st.st_size, st.st_mtime_ns)
    except OSError:
        key = None  # 不在ファイル等はメモ不可（従来どおり毎回probeへ）
    if key is not None and key in _MEDIA_DIMS_MEMO:
        return _MEDIA_DIMS_MEMO[key]
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", filepath],
            capture_output=True, text=True, check=True, timeout=10)
        parts = result.stdout.strip().split(',')
        dims = (int(parts[0]), int(parts[1]))
    except FileNotFoundError:
        warnings.warn("ffprobeが見つかりません。PATHを確認してください。")
        return None, None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        warnings.warn(f"メディアサイズの取得に失敗 ({filepath}): {e}")
        dims = (None, None)
    except (ValueError, IndexError) as e:
        warnings.warn(f"ffprobe出力のパースに失敗 ({filepath}): {e}")
        dims = (None, None)
    if key is not None:
        _MEDIA_DIMS_MEMO[key] = dims
    return dims


def _get_base_dimensions(obj):
    """オブジェクトのscaleエフェクト適用前の基底サイズを取得

    resizeに加えてcrop/pad/rotate(expand)のサイズ変化も反映する
    （scaleエフェクトのpadサイズ過小による実行時エラーを防ぐ）。
    blur / eq / flip / rotate(expand=False) は寸法を変えないので素通しする。
    """
    if getattr(obj, "media_type", None) == "text":
        # テキスト系はキャンバス全面（Project解像度）を基底サイズとする
        proj = current_project()
        if proj is not None:
            return proj.width, proj.height
        return None, None
    src_w, src_h = _get_media_dimensions(obj.source)
    if src_w is None:
        return None, None
    for t in obj.transforms:
        if t.name == "resize":
            sx = t.params.get("sx", 1)
            sy = t.params.get("sy", 1)
            src_w = int(src_w * sx)
            src_h = int(src_h * sy)
        elif t.name in ("crop", "pad"):
            # w/h が式文字列等の非数値ならサイズ反映をスキップ（従来挙動へフォールバック）
            try:
                new_w = int(t.params["w"])
                new_h = int(t.params["h"])
            except (TypeError, ValueError):
                continue
            src_w, src_h = new_w, new_h
        elif t.name == "rotate" and t.params.get("expand"):
            # 静的角度なら expand 後の外接矩形サイズを反映
            ang = t.params.get("rad")
            try:
                a = ang.eval_at(0) if isinstance(ang, Expr) else float(ang)
            except Exception:
                continue
            c = _builtins.abs(_math.cos(a))
            s = _builtins.abs(_math.sin(a))
            new_w = int(_math.ceil(src_w * c + src_h * s))
            new_h = int(_math.ceil(src_w * s + src_h * c))
            src_w, src_h = new_w, new_h
        elif t.name == "grid":
            cols = t.params["cols"]
            rows = t.params["rows"]
            gap = t.params.get("gap", 0)
            src_w = src_w * cols + gap * (cols - 1)
            src_h = src_h * rows + gap * (rows - 1)
    return src_w, src_h


def _build_input_args(obj, fps):
    """メディア種別に応じたffmpeg入力引数を構築（本レンダ/レイヤーキャッシュ共通）"""
    if obj.media_type == "text":
        # テキスト系は実体ファイルを持たず、透明lavfiキャンバスを入力にする。
        # drawtext/subtitles は _build_video_overlay_parts でpre-filterとして重畳。
        proj = current_project()
        w = proj.width if proj else 1920
        h = proj.height if proj else 1080
        d = obj.duration or getattr(obj, "_resolved_length", None)
        if d is None and getattr(obj, "_text_spec", {}).get("kind") == "progress_bar":
            # progress_bar は duration 未設定で動画全体に表示するため、
            # 入力キャンバスも全体尺で生成する（5s固定だとEOF後にバーが消える）
            d = proj.duration if proj and proj.duration else None
        d = d or 5
        return ["-f", "lavfi",
                "-i", f"color=c=black@0.0:s={w}x{h}:d={d}:r={fps},format=rgba"]
    return _decoder_input_args(obj.source, obj.media_type, fps)


def _t_floor(x):
    """フィルタに書く時刻を 1µs 格子へ切り捨てる（x 以下の最大値）。

    time() で順に並べた Object の開始時刻は尺の足し算で決まるので
    804.9000000000001 のような端数が乗る。フィルタ文字列にはこれを出さない。
    単純な round(x, 6) にしないのは、2/30 秒のような格子上の時刻を
    0.066667 へ切り上げてしまうため。tpad の start_duration は ffmpeg 自身が
    µs へ切り捨てて読むので、この値を書けば読まれる値と一致する。
    int と端数の無い float は表記ごとそのまま返す。
    """
    if isinstance(x, int):
        return x
    r = round(x, 6)
    if r > x:
        r = round(r - 1e-6, 6)
    return r


# enable 窓の開始側に取る許容幅（秒）。フレーム間隔（240fps でも約 4ms）より十分小さく、
# 浮動小数の誤差（長尺でも 1e-12 秒程度）より十分大きい。
_T_ENABLE_EPS = 1e-6


def _t_enable_from(start, fps):
    """overlay の enable=between(t,開始,終了) に書く開始側の時刻。

    窓は「開始時刻に最も近い出力フレーム」から開ける。映像入力を開始位置へ送る
    tpad=start_duration も、クローンの枚数を最も近いフレーム数へ丸める
    （av_rescale_q の既定の丸め）ので、中身の1枚目はそのフレームに届く。
    開始時刻のままで窓を開けると、開始が格子から外れているとき（207.339 秒開始 →
    中身は 207.333 秒に届く）中身の1枚目が窓の外に落ちる。time() で並べた動画の
    つなぎ目では、前の動画は音声（AAC は 1024 サンプル単位）の分だけ映像より尺が長く、
    次の動画の開始が映像の終わりより数 ms 後ろになるので、境目の1枚が
    どちらの映像も無い黒いフレームになった（実測: 章の mp4 を5本つないだ完成版）。

    さらに 1µs 手前から開ける。ffmpeg は t を「pts × タイムベースの double 値」で
    計算するので、格子上のフレームの t が 1ulp 小さく出ることがあり
    （30fps の 111 枚目は 111×(1/30)=3.6999999999999997。49fps なら整数秒でも起きる）、
    time() の連結で開始時刻に 804.9000000000001 のような端数も乗る。
    fps だけで決まる（素材を probe しない）ので dry_run と実レンダで同じ文字列になる。
    0 以下はそのまま。
    """
    if start <= 0:
        return start
    n = _tpad_first_frame(start, fps)
    if n <= 0:
        return 0 if isinstance(start, int) else 0.0
    return _t_floor(n / float(fps) - _T_ENABLE_EPS)


def _fps_fraction(fps):
    """fps を有理数にする（29.97 → 2997/100、30000/1001 の float → 30000/1001）。

    _tpad_first_frame と stillseq.py（stills / frames の丸め・鍵・コマンド表記）が
    同じ読み方をするための単一の定義。
    """
    frac = Fraction(fps).limit_denominator(1001)
    if abs(float(frac) - float(fps)) >= 1e-9:
        frac = Fraction(fps)
    return frac


def _tpad_first_frame(start, fps):
    """tpad=start_duration=_t_floor(start) が中身の1枚目を送るフレーム番号。

    tpad は start_duration を µs の整数で読み（小数7桁目以降は切り捨て）、
    av_rescale_q（半分は切り上げる四捨五入）でクローンの枚数にする。同じ計算を
    有理数で行う（浮動小数で掛けると 55.65×30 のような半分ちょうどで食い違う）。
    fps は Project の fps（素材の fps が違うときは近似になる）。
    """
    us = Fraction(repr(_t_floor(start)))
    return _math.floor(us * _fps_fraction(fps) + Fraction(1, 2))


def _video_tail_hold(obj):
    """映像が Object 自身の尺より先に終わる分（秒）。この分は最後のフレームを保持する。

    Object の尺（length()）は長い方の stream で決まる。AAC は 1024 サンプル単位で
    書くので、scriptvedit 自身が書き出す mp4 を含め多くの動画は音声が映像より数十 ms
    長い。time() で順に並べると次の Object は音声の終わりから始まり、映像の終わりとの
    隙間にフレームが落ちると、前の映像は EOF（eof_action=pass）で次はまだ始まらない
    黒いフレームになる。音声の終わりまで最後の絵を出す（再生ソフトと同じ扱い）。
    保持は Object 自身の尺（長い方の stream）までで、time(d) で素材より長く伸ばした分は
    保持しない（従来どおり背景が見える）。__cache__ の生成物は対象外
    （dry_run では未生成で probe できず、キャッシュの有無でコマンドが変わるため。
    checkpoint 等は映像だけを焼くので、そもそも尺が食い違わない）。
    ループする音声（loop()）を持つ Object も対象外（尺が素材で決まらない）。

    例外は stills() / frames() の生成物（_is_hold_artifact_path）。これは「絵の列」
    なので、time(d) で素材より長く伸ばした分も最後の絵を出し続ける（字幕ページの
    最後の1枚が、音声の終わりまで残る）。__cache__ の生成物を対象外にした理由
    （probe できない）は当たらない: 素材の尺は生成コマンドを組んだ時点で確定していて
    Object._generated_length にあり、probe せずに済むので、未生成の dry_run でも
    生成後でも同じ値になる。Effect を焼く経路（checkpoint / compute）では入力が
    一時 Object になり尺が届かないので、_hold_source_filters が同じ保持を入れる。

    Effect を焼いた後（source がチェックポイントへ差し替わった後）も対象にする。
    時間系の live Effect（speed / freeze_frame 等）と焼ける Effect を併用すると、
    チェックポイントは表示尺ではなく素材の尺ぶんしか焼かれず
    （_checkpoint_bake_duration）、伸ばした区間が背景になっていた（speed だけなら
    焼かないので保持され、挙動が食い違った）。差し替え前の元素材は
    Object._audio_source に残る（_apply_checkpoint_final_state が必ず置く）ので、
    それが stills / frames の生成物なら、焼いた尺に live Effect を畳んだ
    Object._resolved_length を素材の尺として保持を出す。時間系の live Effect が
    無いときは焼いた尺 = 表示尺なので保持は 0 になる（焼く側が保持済み）。
    どちらの値も計画の段階で決まり probe しないので、dry_run と実レンダで変わらない。
    """
    if obj.media_type != "video" or obj.duration is None:
        return 0.0
    if _is_hold_artifact_path(obj.source):
        natural = getattr(obj, "_generated_length", None)
        if not natural:
            return 0.0
        hold = float(obj.duration) - _fold_time_effects(natural, obj.effects)
        return hold if hold > 1e-6 else 0.0
    origin = getattr(obj, "_audio_source", None)
    if origin and _is_hold_artifact_path(origin):
        shown = getattr(obj, "_resolved_length", None)
        if not shown:
            return 0.0
        hold = float(obj.duration) - float(shown)
        return hold if hold > 1e-6 else 0.0
    if _is_cache_artifact_path(obj.source) or not os.path.exists(obj.source):
        return 0.0
    if any(getattr(e, "name", None) == "loop" for e in obj.audio_effects):
        return 0.0
    proj = current_project()
    info = proj._probe_media(obj.source) if proj is not None else None
    if not info or not info.get("has_video"):
        return 0.0
    vd, fd = info.get("video_duration"), info.get("duration")
    if vd is None or fd is None or vd >= fd:
        return 0.0
    try:
        natural = obj.length()
    except (TypeError, RuntimeError, FileNotFoundError):
        return 0.0
    hold = _builtins.min(float(obj.duration), natural) - _fold_time_effects(vd, obj.effects)
    return hold if hold > 1e-6 else 0.0


def _hold_source_filters(source):
    """Effect を焼くコマンド（checkpoint / compute）で、入力の最後のコマを保持するフィルタ。

    stills() / frames() の生成物（_is_hold_artifact_path）を入力にするときだけ
    ["tpad=stop=-1:stop_mode=clone"] を返す（それ以外は空）。時間系の前処理
    （trim / speed 等）の後、Transform / Effect の前に入れる。保持は無限に続くが、
    焼くコマンドは必ず -t で尺を切る。こうしておくと、time(d) で素材より長く表示する
    Object に fade などを焼いても、焼いた動画が d 秒ぶんあり、伸ばした区間でも
    Effect が進む（本レンダ側で後から足すと、焼いた最後のコマが止まったまま残る）。
    パスだけで決まるので dry_run と実レンダで同じ文字列になる。
    """
    if _is_hold_artifact_path(source):
        return ["tpad=stop=-1:stop_mode=clone"]
    return []


def _t_ceil(x):
    """フィルタに書く「終了」側の時刻。1µs 格子で x 以上の最小値（_t_floor の対）。"""
    if isinstance(x, int):
        return x
    r = round(x, 6)
    if r < x:
        r = round(r + 1e-6, 6)
    return r


def _visible_window(obj, fps):
    """オブジェクトの可視区間 (t_from, t_to) をタイムライン絶対秒で返す。

    区間は overlay の enable=between(t, start, start+duration) と**同一**にし、
    フレーム境界の判定誤差を吸収する2フレームぶんの余白を前後へ付ける
    （overlayのframesyncは「主入力pts以下の最新フレーム」を選ぶので、
      余分に残した先行/後続フレームは正しさに影響しない）。

    duration が未確定（None）のオブジェクトには enable が付かない＝
    タイムライン全体へ合成されうる（progress_bar 等。start_time が総尺でも
    tpad のクローンフレームが先頭を埋めるので t=0 から見えている）。
    この場合は (0.0, None) を返して**区間を絞らない**。区間を enable より
    狭めると、その分だけ絵が消える。
    """
    if obj.duration is None:
        return 0.0, None
    margin = 2.0 / fps
    return (_builtins.max(0.0, obj.start_time - margin),
            obj.start_time + obj.duration + margin)


def _build_video_overlay_parts(obj, input_idx, current_base, dur, visible_window=None):
    """1オブジェクト分の映像フィルタチェーン + overlay行を構築
    （本レンダとレイヤーキャッシュで共通利用し、両経路の乖離を防ぐ）

    visible_window: (t_from, t_to) — このオブジェクトを実際に合成する
        タイムライン絶対秒の区間。tpadでタイムライン時刻へ整列した直後
        （tpadの無い画像入力ではチェーン先頭）に trim=start/end を挿み、
        可視区間外のフレームが後段の重いフィルタ（drawtext/geq/scale等）へ
        流れるのを防ぐ。開始時刻がバラけたN個のオブジェクトを並べたとき、
        これが無いと総フレーム処理量が O(N^2) になる。
        trimはPTSを振り直さないため、後段のt依存式
        （enable/u正規化/drawtext）は全編レンダと同一文字列のまま成立する。
        通常は `_visible_window(obj, fps)` の戻り値を渡す。時間分割並列レンダ
        （Project.render(parallel=N)）はそれとチャンク区間の積集合を渡す。
        None（既定）なら挿入しない。t_to が None なら終端を打ち切らない。

    Returns: (filter_parts, out_label)
    """
    start = obj.start_time
    base_dims = _get_base_dimensions(obj)
    obj_filters = list(_build_video_pre_filters(obj, label_prefix=f"pre{input_idx}"))
    # 映像が Object 自身の尺より先に終わる動画は、最後のフレームを尺の終わりまで保持する
    # （_video_tail_hold）。tpad は stop_duration を最も近いフレーム数へ丸めるので
    # （数 ms だと 0 枚になる）1フレーム足して必ず覆う。はみ出した分は enable と
    # 可視区間の trim が落とす。開始の tpad があるときは同じ tpad に入れる:
    # 2段に分けると、前段の tpad が下流へ伝える終端の時刻を詰め物の分ずらさないので、
    # 後段のクローンが過去の時刻（開始 0.412 秒の 0.2 秒素材なら 0.2 秒）に出て
    # overlay に捨てられる（FFmpeg 8 実測）
    hold = _video_tail_hold(obj)
    stop_opts = ""
    if hold > 0:
        proj = current_project()
        pad = hold + 1.0 / float(proj.fps if proj else 30)
        stop_opts = f"stop_duration={_t_ceil(pad)}:stop_mode=clone"
    # ビデオ入力が start_time > 0 の場合、tpad で先頭にフレームを追加
    # (overlay有効化前にフレームが消費されるのを防ぐ)
    # trim/setpts の後に挿入し、trim がクローンフレーム込みで尺を切らないようにする
    if obj.media_type != "image" and start > 0:
        # tpad はクローン1枚ごとに「1/フレームレート」を入力タイムベースへ丸めて
        # 積算する。Matroska/WebM（checkpoint の FFV1 .mkv・web/compute/
        # from_project/レイヤーキャッシュの生成物）は 1/1000 なので 1/30 秒が
        # 33ms に丸まり、開始時刻の約1%早く中身が届く（600秒開始で 594 秒。
        # GIF の 1/100 では約10%）。直前で、よく使うフレームレートの1フレーム長が
        # 割り切れるタイムベース（_tpad_timebase。通常 1/120000）へ揃えて防ぐ
        # （CLAUDE.md §4.7）。1/1000000（AVTB）では 1/30 秒などが割り切れず、
        # 誤差が半フレームに積もった時点で1フレームずれる（実測: 30fps の mkv を
        # 1800 秒開始で1フレーム早く、60fps の mp4 を 600 秒開始で1フレーム遅く）。
        # テキストの lavfi 入力はタイムベースが 1/fps で丸めが起きない
        # （実測で開始 600 秒ちょうど）ので対象外にする。
        if obj.media_type != "text":
            proj = current_project()
            obj_filters.append(f"settb={_tpad_timebase(proj.fps if proj else 30)}")
        tpad = f"tpad=start_duration={_t_floor(start)}:start_mode=clone"
        obj_filters.append(f"{tpad}:{stop_opts}" if stop_opts else tpad)
    elif stop_opts:
        obj_filters.append(f"tpad={stop_opts}")
    # 可視区間の外のフレームを早期破棄（PTSは絶対時刻のまま維持）
    if visible_window is not None:
        t_from, t_to = visible_window
        trim_params = []
        if t_from is not None and t_from > 0:
            trim_params.append(f"start={t_from}")
        if t_to is not None:
            trim_params.append(f"end={t_to}")
        if trim_params:
            obj_filters.append("trim=" + ":".join(trim_params))
    # テキスト系: tpad後（タイムライン時刻に整列した後）にdrawtext/subtitlesを重畳
    if obj.media_type == "text":
        obj_filters.extend(_build_text_filters(obj, start, dur))
    obj_filters.extend(_build_transform_filters(obj))
    eff_filters, pad_size = _build_effect_filters(
        obj, start, dur, base_dims=base_dims, label_prefix=f"fx{input_idx}")
    obj_filters.extend(eff_filters)
    obj_filters = _optimize_filter_chain(obj_filters)

    parts = []
    obj_label = f"[obj{input_idx}]"
    if obj_filters:
        parts.append(f"[{input_idx}:v]{','.join(obj_filters)}{obj_label}")
    else:
        obj_label = f"[{input_idx}:v]"

    x_expr, y_expr = _build_move_exprs(obj, start, dur, pad_size=pad_size)

    enable_expr = None
    if obj.duration is not None:
        end = start + obj.duration
        proj = current_project()
        t_from = _t_enable_from(start, proj.fps if proj else 30)
        enable_expr = f"between(t\\,{t_from}\\,{_t_ceil(end)})"
    enable_str = f":enable='{enable_expr}'" if enable_expr else ""

    out_label = f"[v{input_idx}]"
    blend_eff = next((e for e in obj.effects if e.name == "blend_mode"), None)
    if blend_eff is not None and blend_eff.params.get("mode") != "normal":
        _build_blend_mode_overlay(
            parts, blend_eff, input_idx, obj_label, current_base,
            x_expr, y_expr, enable_expr, enable_str, start, dur, out_label)
        return parts, out_label
    parts.append(
        f"{current_base}{obj_label}overlay={x_expr}:{y_expr}:eof_action=pass{enable_str}{out_label}"
    )
    return parts, out_label


def _build_blend_mode_overlay(parts, blend_eff, input_idx, obj_label, current_base,
                              x_expr, y_expr, enable_expr, enable_str, start, dur,
                              out_label):
    """blend_mode合成経路のフィルタ行を parts へ追記する。

    blend_mode: overlayフィルタは合成モード非対応のため、
    このオブジェクトのみ「透明キャンバスへ通常overlayした全面フレーム」を
    blend=cN_mode=<mode> でベースと合成し、オブジェクトのアルファ領域だけ
    maskedmerge で採用する経路に切り替える。
    （blendはアルファ非考慮のため、透明領域まで合成されるのを防ぐ）
    """
    proj = current_project()
    cw = proj.width if proj else 1920
    ch = proj.height if proj else 1080
    cfps = proj.fps if proj else 30
    cdur = (proj.duration if proj and proj.duration else None) or (start + dur)
    mode = blend_eff.params["mode"]
    q = f"bm{input_idx}"
    # 1) 透明キャンバスへ通常overlay（位置/enableは通常経路と同一）
    parts.append(f"color=c=black@0.0:s={cw}x{ch}:r={cfps}:d={cdur}[{q}c]")
    parts.append(
        f"[{q}c]{obj_label}overlay={x_expr}:{y_expr}:eof_action=pass{enable_str},"
        f"format=rgba,split[{q}o1][{q}o2]")
    # 2) アルファ抽出（maskedmergeのマスク。gbrapに揃えて全plane一致）
    parts.append(f"[{q}o2]alphaextract,format=gbrap[{q}m]")
    parts.append(f"[{q}o1]format=gbrap[{q}oc]")
    # 3) 全面blend（obj=top, base=bottom。c3=アルファは指定せずtopを透過）
    parts.append(f"{current_base}format=gbrap,split[{q}b1][{q}b2]")
    parts.append(
        f"[{q}oc][{q}b1]blend=c0_mode={mode}:c1_mode={mode}:c2_mode={mode}[{q}bl]")
    # 4) オブジェクトのアルファ領域のみ合成結果を採用（enable外はベース素通し）
    merge_enable = f"=enable='{enable_expr}'" if enable_expr else ""
    parts.append(f"[{q}b2][{q}bl][{q}m]maskedmerge{merge_enable}{out_label}")


def _build_transform_filters(obj):
    """Transform処理のフィルタリストを生成"""
    filters = []
    for t in obj.transforms:
        if t.name == "resize":
            sx = t.params.get("sx", 1)
            sy = t.params.get("sy", 1)
            filters.append(f"scale=iw*{sx}:ih*{sy}")
        elif t.name == "rotate":
            ang = t.params.get("rad")
            ang_str = ang.to_ffmpeg("u") if isinstance(ang, Expr) else str(ang)
            expand = t.params.get("expand", False)
            fill = t.params.get("fill", "0x00000000")
            filters.append("format=rgba")
            if expand:
                filters.append(
                    f"rotate=angle='{ang_str}':fillcolor={fill}"
                    f":ow='rotw({ang_str})':oh='roth({ang_str})'"
                )
            else:
                filters.append(
                    f"rotate=angle='{ang_str}':fillcolor={fill}:ow=iw:oh=ih"
                )
        elif t.name == "crop":
            x = t.params.get("x", 0)
            y = t.params.get("y", 0)
            w = t.params["w"]
            h = t.params["h"]
            filters.append(f"crop={w}:{h}:{x}:{y}")
        elif t.name == "pad":
            w = t.params["w"]
            h = t.params["h"]
            x = t.params.get("x", -1)
            y = t.params.get("y", -1)
            color = t.params.get("color", "black")
            x_str = "(ow-iw)/2" if x == -1 else str(x)
            y_str = "(oh-ih)/2" if y == -1 else str(y)
            filters.append(f"pad={w}:{h}:{x_str}:{y_str}:color={color}")
        elif t.name == "blur":
            r = t.params.get("radius", 5)
            filters.append(f"boxblur={r}:{r}")
        elif t.name == "eq":
            b = t.params.get("brightness", 0)
            c = t.params.get("contrast", 1)
            s = t.params.get("saturation", 1)
            g = t.params.get("gamma", 1)
            filters.append(f"eq=brightness={b}:contrast={c}:saturation={s}:gamma={g}")
        elif t.name == "flip":
            # 寸法を変えない（_get_base_dimensions は素通しでよい）。alpha も保持する
            if t.params.get("horizontal", True):
                filters.append("hflip")
            if t.params.get("vertical", False):
                filters.append("vflip")
        elif t.name == "grid":
            # 静止素材を cols×rows のグリッドに複製（背景パターン生成用）。
            # -loop 1 の入力は全フレームが同一なので、tile フィルタで
            # cols*rows フレームを並べると同一画像のグリッドになる。
            cols = t.params["cols"]
            rows = t.params["rows"]
            gap = t.params.get("gap", 0)
            filters.append(
                f"tile={cols}x{rows}:padding={gap}:margin=0:color=0x00000000")
        else:
            # 未知の Transform を黙って捨てない（Effect の _FX_BUILDERS と同じ方針）。
            # 捨てるとフィルタの出ないコマンドがスナップショットに焼かれ、
            # 以後ずっと緑のまま「効かない Transform」が残る
            raise ValueError(
                f"未知の Transform '{t.name}' です（_build_transform_filters に"
                f"フィルタ生成がありません）")
    return filters


# --- Effectフィルタのビルダー群（_build_effect_filters のディスパッチ先） ---
#
# 巨大な elif 連鎖を effect 名ごとの _fx_* 関数へ分解したもの。
# 各ビルダーは (e, eff_idx, ctx) を受け取り、ctx.filters へフィルタ文字列を
# 追記する（生成文字列は elif 連鎖時代と完全一致）。pad_size の更新も
# ctx.pad_size への読み書きで元の適用順どおりに行う。

class _FxCtx:
    """Effectビルダー間で共有する可変コンテキスト

    filters: 生成中のフィルタリスト（各ビルダーが追記する）
    pad_size: (max_w, max_h) or None。scale が設定し、drop_shadow/outline が
        拡張分を加算、blur_background_fill が上書き、プラグインは pad_state
        経由で更新する（Effectの並び順どおりに反映される）。
    """
    __slots__ = ("obj", "filters", "pad_size", "start", "dur",
                 "base_dims", "label_prefix")

    def __init__(self, obj, start, dur, base_dims, label_prefix):
        self.obj = obj
        self.filters = []
        self.pad_size = None
        self.start = start
        self.dur = dur
        self.base_dims = base_dims
        self.label_prefix = label_prefix


def _frame_us(start, dur):
    """表示区間 [start, start+dur] に入るコマ（タイムラインの 1/fps 刻み）の u を返す。

    フィルタの式はコマの時刻 T で評価されるので、式の最大・最小を見積もるときは
    この u で評価すれば「実際に描かれる値」を漏れなく拾える（チェックポイントは
    start=0 で 0/fps から、本レンダは tpad でタイムラインの格子へ揃えたコマ）。
    前後に1コマずつ余分に取り、u は 0..1 へ clip する（区間の外は端の値で止まる）。
    """
    if not dur or dur <= 0:
        return []
    proj = current_project()
    fps = float(proj.fps if proj else 30)
    k0 = _math.floor(start * fps) - 1
    k1 = _math.ceil((start + dur) * fps) + 1
    return [_builtins.min(1.0, _builtins.max(0.0, (k / fps - start) / dur))
            for k in range(k0, k1 + 1)]


def _expr_breakpoint_us(expr, dur):
    """式の中の「u（または経過秒）< 定数」の境目を u で返す（keyframes の頂点など）。

    区分線形の keyframes / keyframes_sec / sequence_param は lt(u, 境目) の木で書かれ、
    最大・最小は頂点（境目）で取る。コマの時刻だけで評価すると、時刻が ms に丸まる
    素材（mkv の 1/1000）では細い山の頂上付近でコマごとの値が見積もりを少し超えうるので、
    頂点そのものも評価に加える。
    """
    out = []
    stack = [expr]
    while stack:
        node = stack.pop()
        cls = type(node)
        if cls is _BinOp:
            stack.extend((node.left, node.right))
        elif cls is _UnOp:
            stack.append(node.operand)
        elif cls is _FuncCall:
            if node.name == "lt" and len(node.args) == 2 and type(node.args[1]) is Const:
                x, c = node.args[0], float(node.args[1].value)
                if type(x) is Var and x.name == "u":
                    out.append(c)
                elif type(x) is _TimeVar and x.kind == "sec" and dur:
                    out.append(c / dur)
            stack.extend(node.args)
    return [_builtins.min(1.0, _builtins.max(0.0, u)) for u in out]


def _frame_us_near(start, dur, xs):
    """u の点 xs のそれぞれの前後のコマの u を返す（_frame_us の部分集合。同じ式で計算する）。

    1次式の区分の内側では、コマの値の最大・最小は区分に入る最初か最後のコマで取るので、
    区分の端の前後のコマだけを見れば全コマを見たのと同じになる（端の位置からコマ番号を
    求める丸めの分だけ、前後に1コマずつ余分に取る）。
    """
    if not dur or dur <= 0:
        return []
    proj = current_project()
    fps = float(proj.fps if proj else 30)
    k0 = _math.floor(start * fps) - 1
    k1 = _math.ceil((start + dur) * fps) + 1
    ks = set()
    for x in xs:
        base = _math.floor((start + x * dur) * fps)
        for k in range(base - 1, base + 3):
            if k0 <= k <= k1:
                ks.add(k)
    return [_builtins.min(1.0, _builtins.max(0.0, (k / fps - start) / dur))
            for k in sorted(ks)]


def _expr_frame_max(expr, start, dur):
    """描くコマ（_frame_us）と式の頂点で、式が取る値の最大を返す（評価する点が無ければ None）。

    区分線形の式（keyframes / keyframes_sec / ramp 等。expr_scan._pl_pieces）は、区分の
    端（頂点）の値と、端の前後のコマだけを評価する（区分の内側は1次式なので、コマの最大は
    区分の最初か最後のコマで取る。全コマを評価したのと同じ値になり、手間は点の数に比例して
    コマ数に依らない）。区分線形でない式（イージング・振動系・中身を辿れない派生）だけ、
    描くコマと keyframes の頂点をすべて評価する（_compile_u_eval は if の選んだ枝だけを
    辿るので、1回の評価は点の数の対数）。以前は全コマで eval_at（if の両方の枝を評価する）を
    呼んでいて、60fps・128 点の scale で 600 秒の Object はフィルタを組むたびに 17 秒、
    3600 秒は 107 秒かかった。
    不連続点の手前の片側極限（どのコマも取らない値）は使わない（全コマを評価していた頃と
    同じ。pad の大きさは topleft 等の配置にも効くので、描かない大きさで pad を広げない）。
    """
    ev = _compile_u_eval(expr, dur)
    pieces = _pl_pieces(expr, dur)
    us = _expr_breakpoint_us(expr, dur)
    if pieces is None:
        us += _frame_us(start, dur)
    else:
        ends = _pl_ends(pieces)
        us += ends + _frame_us_near(start, dur, ends)
    return _builtins.max(ev(u) for u in us) if us else None


def _fx_scale(e, eff_idx, ctx):
    """動的スケール（+ base_dims 既知時は固定サイズpad + SEGVバリア）"""
    scale_expr = e.params.get("value", Const(1))
    u_expr = _u_expr(ctx.start, ctx.dur)
    ffmpeg_str = scale_expr.to_ffmpeg(u_expr)
    ctx.filters.append(
        f"scale=w='trunc(iw*({ffmpeg_str})/2)*2':h='trunc(ih*({ffmpeg_str})/2)*2':eval=frame"
    )
    # pad: scaleの出力を最大サイズの固定フレームに収め、overlay位置を安定化
    if ctx.base_dims and ctx.base_dims[0] is not None:
        bw, bh = ctx.base_dims
        # 定数スケールはサンプリング不要（固定点評価の短絡）
        if isinstance(scale_expr, Const):
            max_s = scale_expr.value
        else:
            # 固定格子サンプリングで最大スケールを推定。
            # 振動系関数（sin等）を含む式は i/100 の格子とエイリアスして
            # 点間ピークを取りこぼす（例: 1+0.5*sin(100*PI*u) は全標本1、
            # 実際は1.5 → pad不足でEINVAL）ため、密な素数格子で評価する
            # （issue #13 P2-13）
            n_grid = 4999 if _expr_has_oscillatory(scale_expr) else 100
            us = [i / n_grid for i in range(n_grid + 1)]
            # 多点の keyframes は格子の間の細い山も取りこぼす（実測: 10 秒の 4.02〜4.08 秒
            # だけ 2 倍の山は 100 等分の格子では 1.0 しか拾えず、pad が足りず EINVAL）。
            # 式の頂点と描くコマでの最大（_expr_frame_max。区分線形なら頂点と頂点の前後の
            # コマだけ、そうでなければ全コマを評価する）も拾う
            try:
                # eval_at と同じ値（_compile_u_eval は if の選んだ枝だけを辿るので、
                # 多点の keyframes でも1回の評価が点の数の対数で済む）
                ev = _compile_u_eval(scale_expr, ctx.dur)
                max_s = _builtins.max(ev(u) for u in us)
                peak = _expr_frame_max(scale_expr, ctx.start, ctx.dur)
                if peak is not None:
                    max_s = _builtins.max(max_s, peak)
            except Exception as exc:
                raise ValueError(
                    f"scale式を数値評価できないため、padサイズを決定できません: {exc}\n"
                    f"scale() には u のみに依存する数値評価可能な式を渡してください。"
                ) from exc
        max_w = _math.ceil(bw * max_s / 2) * 2
        max_h = _math.ceil(bh * max_s / 2) * 2
        ctx.filters.append("format=rgba")
        ctx.filters.append(
            f"pad={max_w}:{max_h}:(ow-iw)/2:(oh-ih)/2:color=0x00000000:eval=frame"
        )
        # SEGVバリア: FFmpeg 8.0では scale(eval=frame)+rotate の組み合わせで
        # SEGV(0xC0000005)が発生し、pad/format=rgba 単体では防げない。
        # copy フィルタによるバッファ分離が必要（検証済みの回避策）。
        ctx.filters.append("copy")
        ctx.pad_size = (max_w, max_h)


# --- 時間だけで決まる不透明度を、コマごとに1回だけ評価して掛ける経路 ---
#
# geq は式を画素ごとに評価する（1920x1080 なら1コマで約207万画素 × r/g/b/a の4式）。
# fade / opacity の不透明度のように画素の位置に依らない（時間だけで決まる）式まで geq に
# 落とすと、式が単純でも 1080p で毎秒6コマ前後しか出ず、式が長いほど更に遅くなる
# （実測: 1080p・10 秒の静止画に fade(keyframes_sec(56 点)) で 85 秒。113 秒の見本では
#  本レンダが 771 秒）。sendcmd の [expr] フラグは、引数の式をコマごとに1回だけ評価して
# 結果の数値を実行時コマンドで送る（変数 T はそのコマの時刻で、geq の T と同じ値）。
# これで colorchannelmixer の aa（アルファの倍率）をコマごとに変える（同じ素材の書き出し
# 全体が 8 / 56 / 128 点で 4.1 / 4.3 / 4.3 秒）。ただし点の数に全く依らないわけではない:
# [expr] はコマごとに式の文字列を構文解析し直すので、式の長さに比例した手間が残る
# （16x16・18000 コマで 8 点 2.9 秒、128 点 12.7 秒。経過秒を st/ld で1回だけ計算する形で
# 短くしている。_per_frame_alpha_cmd）。
# 入りと出が表示秒の 2% 以下（0.25 秒なら表示 12.5 秒以上）だと 100 等分の格子に中間点が
# 2つ取れず native fade にならないので、以前はこれも geq だった（入りと出だけの普通の
# フェードでも、長い Object では遅かった）。
#
# - 画素は geq と最大1階調違う: geq は「アルファ×値」を切り捨て、colorchannelmixer は
#   lrint（四捨五入。ちょうど .5 は偶数へ）で書く。sendcmd が送る値は小数6桁（%f）。
#   色（r/g/b）は変えない。チェックポイント・compute の鍵には cache.py の
#   _alpha_cmd_sigs が版を混ぜ、旧 geq 経路で焼いた中間物を命中させない（レイヤーキャッシュ・
#   from_project の鍵には意図して混ぜない。理由は cache.py の _ALPHA_CMD_VER）。
# - colorchannelmixer は planar の gbrap で掛ける（format=rgba の後ろに format=gbrap）。
#   packed の rgba のまま下流へ渡すと、overlay の手前の自動変換（rgba → yuva420p）が
#   アルファ 128〜254 を 1 階調上げる（FFmpeg 8.0 実測: 171 → 172。gbrap → yuva420p は
#   全 256 値で正確）。geq は packed を扱えず入口で gbrap へ自動変換されていたので、
#   gbrap で掛ければ下流は旧 geq 経路と同じ形になる（違いは上の丸めだけ）。
# - sendcmd の宛先はフィルタのインスタンス名（colorchannelmixer@<接頭辞>e<番号>）で、
#   1つのフィルタグラフの中で一意でなければならない（型名 colorchannelmixer で送ると、
#   グラフ中のすべての colorchannelmixer に届く）。接頭辞は複合フィルタの中間ラベルと
#   同じ label_prefix（本レンダ・レイヤーキャッシュ・並列レンダは入力ごとに fx<N>）。
# - 引数のエスケープは3段: フィルタグラフ（c='...' の引用で中身はそのまま）→
#   オプション値（\\, → \,）→ sendcmd のコマンド解析（\, → ,）。式の区切りの「\,」を
#   「\\,」へ書き換える。想定外の記号を含む式は geq に残す（_SENDCMD_EXPR_CHARS）。
# - X / Y 等の画素ごとの変数・random（geq では画素ごとに別の乱数）・中身を辿れない Expr の
#   派生は時間だけの式と証明できないので geq に残す（_expr_is_time_only）。

# 時間だけの式に使ってよい関数（ffmpeg の式評価器にあり、画素に依らないもの）
_TIME_ONLY_FUNCS = frozenset(_FuncCall._get_eval_funcs()) - {"random"}

# sendcmd の引数へそのまま入れてよい文字（式の区切りの「,」を除くと英数字と算術記号だけ）
_SENDCMD_EXPR_CHARS = frozenset(
    "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_.+-*/(),")


def _expr_is_time_only(expr):
    """式が時間（u・経過秒・表示秒）と定数だけで決まるかを返す（画素の位置に依らないか）。

    型は完全一致で見る（_LookAtExpr のような派生は中身を辿れないので False）。
    """
    stack = [expr]
    while stack:
        node = stack.pop()
        cls = type(node)
        if cls is Const or cls is _TimeVar:
            continue
        if cls is Var:
            if node.name != "u":
                return False
        elif cls is _BinOp:
            stack.extend((node.left, node.right))
        elif cls is _UnOp:
            stack.append(node.operand)
        elif cls is _FuncCall and node.name in _TIME_ONLY_FUNCS:
            stack.extend(node.args)
        else:
            return False
    return True


def _per_frame_alpha_cmd(value_expr, start, dur):
    """時間だけで決まる不透明度の (初期値, sendcmd の引数) を返す。使えないときは None。

    引数は clip(式,0,1) をフィルタグラフへ書く形にエスケープしたもの。初期値は u=0 の値
    （sendcmd の区間 0 秒より前のコマが来たときに使われる。geq も開始前は u=0 の値）。

    sendcmd の [expr] はコマごとに式の文字列を構文解析し直す（av_expr_parse_and_eval）ので、
    手間は式の長さに比例する。keyframes_sec は区間ごとに経過秒 clip(T-開始,0,表示秒) を
    何度も書く（128 点で約250回）ので、経過秒は先頭で1回だけ st(0,…) に置き、式の中では
    ld(0) で読む（u は ld(0)/表示秒。clip((T-開始)/表示秒,0,1) とビット単位で同じ値）。
    st を必ず先に評価させるため if の条件に置く（経過秒は 0 以上なので条件は常に真）。
    各コマの値はどちらの形でもビット単位で同じ（実測: 16x16・18000 コマの出力が一致）なので、
    短い方を使う（u を1回しか使わない式は、st/ld で包むと逆に長くなる）。
    """
    if not _expr_is_time_only(value_expr):
        return None
    try:
        init = float(value_expr.eval_at(_UValue(0.0, dur)))
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    if not _math.isfinite(init):
        return None
    init = round(_builtins.min(1.0, _builtins.max(0.0, init)), 6)
    inline = value_expr.to_ffmpeg(_u_expr(start, dur, "T"))
    stored = value_expr.to_ffmpeg(_UStr(f"(ld(0)/{dur})", dur, sec="ld(0)"))
    raw = _builtins.min(
        (f"clip({inline},0,1)".replace("\\,", ","),
         f"if(gte(st(0,clip(T-{start},0,{dur})),0),clip({stored},0,1),0)".replace("\\,", ",")),
        key=len)
    if not set(raw) <= _SENDCMD_EXPR_CHARS:
        return None
    return init, raw.replace(",", "\\\\,")


def _alpha_path(value_expr, start, dur, *, native):
    """不透明度 value_expr をアルファへ掛ける経路を選ぶ。

    "const"（定数。colorchannelmixer）/ "native"（native fade。native=True のときだけ試す）/
    "frame"（コマごとに1回の評価。sendcmd + colorchannelmixer）/ "geq"（画素ごとの式）。
    native fade（アルファの面だけを触る）> コマごとの評価 > geq の順に速い。
    """
    if isinstance(value_expr, Const):
        return "const", None
    if native:
        filters = _try_native_fade(value_expr, start, dur)
        if filters:
            return "native", filters
    cmd = _per_frame_alpha_cmd(value_expr, start, dur)
    if cmd is not None:
        return "frame", cmd
    return "geq", None


def _alpha_mul_filters(value_expr, eff_idx, ctx, *, native):
    """不透明度 value_expr（0..1 へ clip）をアルファへ掛けるフィルタ列（format=rgba の後ろに付ける）"""
    path, data = _alpha_path(value_expr, ctx.start, ctx.dur, native=native)
    if path == "const":
        # ここへ来る定数は fade だけ（opacity の定数は _fx_opacity が従来どおり rgba で掛ける）
        v = _builtins.min(1.0, _builtins.max(0.0, float(value_expr.value)))
        return ["format=gbrap", f"colorchannelmixer=aa={v}"]
    if path == "native":
        return data
    if path == "frame":
        init, arg = data
        target = f"colorchannelmixer@{ctx.label_prefix}e{eff_idx}"
        return ["format=gbrap", f"sendcmd=c='0 [expr] {target} aa {arg}'",
                f"{target}=aa={init}"]
    ffmpeg_str = value_expr.to_ffmpeg(_u_expr(ctx.start, ctx.dur, "T"))
    return [f"{_GEQ_RGB}:a='alpha(X\\,Y)*clip({ffmpeg_str}\\,0\\,1)'"]


def _ops_alpha_by_cmd(ops, dur):
    """ops（[(種別, op), ...]）に、旧 geq 経路から colorchannelmixer 経路へ移った
    fade / opacity があるか（画素が最大1階調変わるので中間物の鍵に版を混ぜる。
    cache.py の _alpha_cmd_sigs）。

    移ったのは fade の定数とコマごとの評価、opacity のコマごとの評価だけ（opacity の
    定数は元から colorchannelmixer、native fade と geq のままの式は出力が変わらない）。
    中間物（チェックポイント・compute）は start=0 で焼くので、フィルタを組む側と同じく
    start=0 で判定する（native fade の判定はコマの時刻 = start に依る）。
    """
    for typ, op in ops:
        if typ != "effect":
            continue
        if op.name == "fade":
            path, _ = _alpha_path(op.params.get("alpha", Const(1.0)), 0, dur, native=True)
            if path in ("const", "frame"):
                return True
        elif op.name == "opacity":
            path, _ = _alpha_path(op.params.get("value", Const(1.0)), 0, dur, native=False)
            if path == "frame":
                return True
    return False


def _fx_fade(e, eff_idx, ctx):
    """フェード（native fade → コマごとの評価 → geq の順に、使える最速の経路を選ぶ）"""
    alpha_expr = e.params.get("alpha", Const(1.0))
    ctx.filters.append("format=rgba")
    ctx.filters.extend(_alpha_mul_filters(alpha_expr, eff_idx, ctx, native=True))


def _fx_rotate_to(e, eff_idx, ctx):
    """動的回転（expand時は対角線長の固定サイズ出力）"""
    rad_expr = e.params.get("rad", Const(0))
    u_expr = _u_expr(ctx.start, ctx.dur)
    ang_str = rad_expr.to_ffmpeg(u_expr)
    expand = e.params.get("expand", True)
    fill = e.params.get("fill", "0x00000000")
    ctx.filters.append("format=rgba")
    if expand:
        # 動的回転: ow/ohは初期化時に1度だけ評価されるため
        # rotw/rothではなく対角線長で固定サイズにする
        ctx.filters.append(
            f"rotate=angle='{ang_str}':fillcolor={fill}"
            f":ow='hypot(iw,ih)':oh='hypot(iw,ih)'"
        )
    else:
        ctx.filters.append(
            f"rotate=angle='{ang_str}':fillcolor={fill}:ow=iw:oh=ih"
        )


def _fx_wipe(e, eff_idx, ctx):
    """方向ワイプ（geqでアルファを進行度に応じてカット）"""
    prog_expr = e.params.get("progress", Const(1))
    # geqの時間変数は大文字T（小文字tは未定義。fade 経路と同じ）
    u_expr = _u_expr(ctx.start, ctx.dur, "T")
    ffmpeg_str = prog_expr.to_ffmpeg(u_expr)
    direction = e.params.get("direction", "left")
    ctx.filters.append("format=rgba")
    if direction == "left":
        ctx.filters.append(f"{_GEQ_RGB}:a='if(lte(X\\,W*({ffmpeg_str}))\\,alpha(X\\,Y)\\,0)'")
    elif direction == "right":
        ctx.filters.append(f"{_GEQ_RGB}:a='if(gte(X\\,W*(1-({ffmpeg_str})))\\,alpha(X\\,Y)\\,0)'")
    elif direction == "up":
        ctx.filters.append(f"{_GEQ_RGB}:a='if(gte(Y\\,H*(1-({ffmpeg_str})))\\,alpha(X\\,Y)\\,0)'")
    elif direction == "down":
        ctx.filters.append(f"{_GEQ_RGB}:a='if(lte(Y\\,H*({ffmpeg_str}))\\,alpha(X\\,Y)\\,0)'")


def _fx_color_shift(e, eff_idx, ctx):
    """色相/彩度/明度シフト（hue + eq。動的式は eval=frame）"""
    u_expr = _u_expr(ctx.start, ctx.dur)
    if "hue" in e.params:
        h_str = e.params["hue"].to_ffmpeg(u_expr)
        ctx.filters.append(f"hue=h={h_str}")
    eq_parts = []
    eq_dynamic = False
    if "saturation" in e.params:
        s_str = e.params["saturation"].to_ffmpeg(u_expr)
        eq_parts.append(f"saturation={s_str}")
        eq_dynamic = eq_dynamic or not isinstance(e.params["saturation"], Const)
    if "brightness" in e.params:
        b_str = e.params["brightness"].to_ffmpeg(u_expr)
        eq_parts.append(f"brightness={b_str}")
        eq_dynamic = eq_dynamic or not isinstance(e.params["brightness"], Const)
    if eq_parts:
        eq_filter = "eq=" + ":".join(eq_parts)
        if eq_dynamic:
            # eqの既定はeval=init（初期化時1回のみ評価）→ 動的式は毎フレーム評価が必要
            eq_filter += ":eval=frame"
        ctx.filters.append(eq_filter)


def _fx_chroma_key(e, eff_idx, ctx):
    """クロマキー（chromakey + rgba正規化）"""
    color = e.params.get("color", "green")
    sim = e.params.get("similarity", 0.1)
    bl = e.params.get("blend", 0.0)
    ctx.filters.append(f"chromakey=color={color}:similarity={sim}:blend={bl}")
    # chromakeyはyuva出力 → 後段のgeq/overlay向けにrgbaへ正規化
    ctx.filters.append("format=rgba")


def _fx_vignette(e, eff_idx, ctx):
    """ビネット（時間依存式は eval=frame）"""
    # 注意: vignetteフィルタはアルファ非対応（透明部分は失われる）。全画面素材向け。
    ang = e.params.get("angle", Const(_math.pi / 5))
    if isinstance(ang, Const):
        ctx.filters.append(
            f"vignette=angle='clip({ang.to_ffmpeg('0')}\\,0\\,PI/2)'")
    else:
        # 時間依存式: eval=frame で毎フレーム評価（uは正規化時刻）
        u_expr = _u_expr(ctx.start, ctx.dur)
        ctx.filters.append(
            f"vignette=angle='clip({ang.to_ffmpeg(u_expr)}\\,0\\,PI/2)':eval=frame")


def _fx_pixelize(e, eff_idx, ctx):
    """モザイク"""
    s = e.params.get("size", 16)
    ctx.filters.append(f"pixelize=w={s}:h={s}")


def _fx_glow(e, eff_idx, ctx):
    """グロー（split→gblur→blend=screen の複合チェーン。発光合成）

    入口は format=gbrap（プレーナ RGB）で固定する。gblur と blend はパックド
    の rgba を受け取れないので、format=rgba だと形式の折衝に任され、下流が
    YUV 系を好む経路（live の overlay 直結。text() など焼かれない Object）では
    blend が yuva で動く。screen 合成を色差（U/V。無彩色で 0.5）にも掛けるので、
    白が 1-(1-0.5)^2=0.75 側へ寄ってマゼンタに化ける（FFmpeg 8.0 実測）。
    チェックポイントへ焼く経路は出力が bgra なので元から gbrap に折衝されており、
    出力は format=rgba のときとバイト一致（＝キャッシュ鍵は据え置き）。"""
    r = e.params.get("radius", 10)
    it = e.params.get("intensity", 1.0)
    p = f"{ctx.label_prefix}e{eff_idx}"
    ctx.filters.append("format=gbrap")
    ctx.filters.append(
        f"split[{p}a][{p}b];"
        f"[{p}b]gblur=sigma={r}[{p}c];"
        f"[{p}a][{p}c]blend=all_mode=screen:all_opacity={it}"
    )


def _fx_lut(e, eff_idx, ctx):
    """3D LUT適用"""
    # lut3d も fontfile/subtitles と同じパスエスケープを使う
    ctx.filters.append(f"lut3d=file={_escape_ffpath(e.params['file'])}")


def _fx_glitch(e, eff_idx, ctx):
    """グリッチ（rgbashift + noise のプリセット。interval指定時は間欠発動）"""
    strength = e.params.get("strength", 1.0)
    iv = e.params.get("interval")
    shift = _builtins.max(1, int(_builtins.round(4 * strength)))
    shift_v = _builtins.max(1, shift // 2)
    nstr = _builtins.min(100, _builtins.max(1, int(_builtins.round(20 * strength))))
    enable = ""
    if iv is not None:
        # 各interval周期の先頭30%区間のみ有効化
        on_dur = iv * 0.3
        enable = f":enable='lt(mod(t-{ctx.start}\\,{iv})\\,{on_dur})'"
    ctx.filters.append("format=rgba")
    ctx.filters.append(f"rgbashift=rh={shift}:bh=-{shift}:gv={shift_v}{enable}")
    ctx.filters.append(f"noise=alls={nstr}:allf=t+u{enable}")


def _fx_perspective_warp(e, eff_idx, ctx):
    """透視変形"""
    # sense=destination: 入力の4隅を指定座標へ移動（左上,右上,左下,右下）
    coords = ":".join(
        f"{k}={e.params[k]}"
        for k in ("x0", "y0", "x1", "y1", "x2", "y2", "x3", "y3"))
    ctx.filters.append(f"perspective={coords}:sense=destination")


def _fx_lens(e, eff_idx, ctx):
    """レンズ歪み補正"""
    k1 = e.params.get("k1", 0)
    k2 = e.params.get("k2", 0)
    ctx.filters.append(f"lenscorrection=k1={k1}:k2={k2}")


def _fx_ken_burns(e, eff_idx, ctx):
    """Ken Burns（動的scale + 固定サイズcrop で矩形間をパン&ズーム）"""
    u_expr = _u_expr(ctx.start, ctx.dur)
    s_str = e.params["s"].to_ffmpeg(u_expr)
    x_str = e.params["x"].to_ffmpeg(u_expr)
    y_str = e.params["y"].to_ffmpeg(u_expr)
    ow = e.params["w"]
    oh = e.params["h"]
    ctx.filters.append(
        f"scale=w='trunc(iw*({s_str})/2)*2':h='trunc(ih*({s_str})/2)*2':eval=frame")
    ctx.filters.append(f"crop={ow}:{oh}:x='{x_str}':y='{y_str}'")
    # SEGVバリア: scale(eval=frame)後のバッファ分離（既存scale実装と同じ回避策）
    ctx.filters.append("copy")


def _fx_drop_shadow(e, eff_idx, ctx):
    """ドロップシャドウ（split→色付け+ぼかし→本体を影の上にoverlay。
    キャンバスは影が収まるよう拡張）"""
    dxv = e.params.get("dx", 5)
    dyv = e.params.get("dy", 5)
    bl = e.params.get("blur", 8)
    op_ = e.params.get("opacity", 0.5)
    cr, cg, cb = _parse_color_rgb(e.params.get("color", "black"))
    m = int(_math.ceil(3 * bl))  # gblurの裾野(約3σ)
    left = _builtins.max(0, m - dxv)
    right = _builtins.max(0, m + dxv)
    top = _builtins.max(0, m - dyv)
    bottom = _builtins.max(0, m + dyv)
    p = f"{ctx.label_prefix}e{eff_idx}"
    # ぼかしは pad の後に適用（端まで不透明な素材でも影が枠外へにじむように）
    blur_part = f",gblur=sigma={bl}" if bl > 0 else ""
    ctx.filters.append("format=rgba")
    ctx.filters.append(
        f"split[{p}a][{p}b];"
        f"[{p}b]geq=r='{cr}':g='{cg}':b='{cb}':a='alpha(X\\,Y)*{op_}',"
        f"pad=iw+{left + right}:ih+{top + bottom}:{left + dxv}:{top + dyv}:color=0x00000000"
        f"{blur_part}[{p}s];"
        f"[{p}s][{p}a]overlay={left}:{top}:eof_action=pass"
    )
    # scale等で固定サイズ化済み(pad_size設定済み)なら、影の拡張分を加算して
    # overlay中央配置((W-pad_size[0])/2)のずれを防ぐ
    if ctx.pad_size:
        ctx.pad_size = (ctx.pad_size[0] + left + right,
                        ctx.pad_size[1] + top + bottom)


def _fx_tint(e, eff_idx, ctx):
    """色の塗り替え（定数は lutrgb、Expr は geq。どちらもアルファは素通し）"""
    # lutrgb も geq も式の値を切り捨てて書くので、どちらも round() で四捨五入する
    # （白 × 0.5 = 127.5 が 127 へ落ちるのを防ぐ）。定数と式で同じ amount なら
    # 同じ画素になるよう、2つの経路は同じ順序の演算にしてある:
    #   multiply … 元の色 × (1 - a × (1 - color/255))
    #   fill     … 元の色 × (1 - a) + a × color
    # （演算の順序が違うと .5 の境目で 1 階調ずれ、時間で変わる tint の終点が
    #   定数の tint と一致しなくなる。tests/test_tint.py が画素で確かめる）
    amount = e.params.get("amount", Const(1.0))
    rgb = _parse_color_rgb(e.params["color"])
    fill = e.params.get("mode", "multiply") == "fill"
    ctx.filters.append("format=rgba")
    if isinstance(amount, Const):
        a = float(amount.value)
        parts = []
        for ch, c in zip("rgb", rgb):
            if fill:
                parts.append(f"{ch}='clip(round(val*{1 - a!r}+{a * c!r})\\,0\\,255)'")
            else:
                parts.append(
                    f"{ch}='clip(round(val*{1 - a * (1 - c / 255)!r})\\,0\\,255)'")
        ctx.filters.append("lutrgb=" + ":".join(parts))
        return
    # geq の時間変数は大文字 T（fade / wipe と同じ）
    a_str = f"clip({amount.to_ffmpeg(_u_expr(ctx.start, ctx.dur, 'T'))}\\,0\\,1)"
    parts = []
    for ch, c in zip("rgb", rgb):
        src = f"{ch}(X\\,Y)"
        if fill:
            parts.append(f"{ch}='round({src}*(1-{a_str})+{a_str}*{c})'")
        else:
            parts.append(f"{ch}='round({src}*(1-{a_str}*{1 - c / 255!r}))'")
    ctx.filters.append("geq=" + ":".join(parts) + ":a='alpha(X\\,Y)'")


def _fx_outline(e, eff_idx, ctx):
    """縁取り（alpha膨張ベース。色付けした複製のalphaを膨張させ本体をoverlay）"""
    # alpha膨張（dilationをwidth回連結）ベースの縁取り。
    # 色付けした複製のalphaを膨張させ、本体をその上にoverlayする。
    wd = e.params.get("width", 2)
    cr, cg, cb = _parse_color_rgb(e.params.get("color", "white"))
    p = f"{ctx.label_prefix}e{eff_idx}"
    dil = ",".join(["dilation"] * wd)
    ctx.filters.append("format=rgba")
    ctx.filters.append(
        f"split[{p}a][{p}b];"
        f"[{p}b]pad=iw+{2 * wd}:ih+{2 * wd}:{wd}:{wd}:color=0x00000000,"
        f"geq=r='{cr}':g='{cg}':b='{cb}':a='alpha(X\\,Y)',"
        f"{dil}[{p}o];"
        f"[{p}o][{p}a]overlay={wd}:{wd}:eof_action=pass"
    )
    # scale等で固定サイズ化済みなら、縁取りの拡張分(2*wd)を加算して中央配置ずれを防ぐ
    if ctx.pad_size:
        ctx.pad_size = (ctx.pad_size[0] + 2 * wd, ctx.pad_size[1] + 2 * wd)


def _fx_mask(e, eff_idx, ctx):
    """画像マスク（輝度をアルファとして乗算）"""
    # 画像の輝度をアルファとして乗算。追加 -i 入力の配線を避けるため
    # movie= ソースをチェーン内サブグラフで読み込む。
    # マスクは scale=rw:rh（寸法の基準＝素材のアルファ）で素材サイズへ自動スケールし、
    # blend='A*B/255' で元アルファと乗算 → alphamerge で書き戻す。
    img = _escape_ffpath(e.params["image"])
    p = f"{ctx.label_prefix}e{eff_idx}"
    ctx.filters.append("format=rgba")
    ctx.filters.append(
        f"split[{p}a][{p}b];"
        f"[{p}b]alphaextract,split[{p}oa][{p}oar];"
        f"movie=filename={img}[{p}mi];"
        f"[{p}mi][{p}oar]scale=rw:rh[{p}ms];"
        f"[{p}ms]{_MASK_GRAY}[{p}mg];"
        f"[{p}oa][{p}mg]blend=all_expr='A*B/255':eof_action=repeat[{p}na];"
        f"[{p}a][{p}na]alphamerge"
    )


# mask / mask_wipe のマスク画像をグレーへ直すフィルタ。
# - マスクは `scale=rw:rh`（第2入力＝素材のアルファを寸法の基準にする）で素材の
#   寸法へ合わせてからここへ入る。FFmpeg 8 で deprecated になった scale2ref と
#   全フレームの画素が一致することを実測済み（tests/test_mask_scale_ref.py）。
#   scale2ref と違い基準側を出力しないので、アルファは alphaextract の直後に
#   split して、片方を寸法の基準・もう片方を blend へ渡す。
# - setparams=colorspace=unknown: マスク（RGB の PNG）由来の「colorspace=gbr」の
#   タグを外す。外さないと、このタグがグレーの枝 → blend → alphamerge → overlay と
#   下流へ伝わり、透明な下地へ重ねるレイヤーキャッシュ（VP9 yuva420p）の
#   エンコーダが「SRGB color space requires profile 1 or 3」で落ちる。
#   画素は変わらない（変換行列は元から既定のまま。タグだけが誤っていた。実測）。
_MASK_GRAY = "format=gray,setparams=colorspace=unknown"


def _fx_mask_wipe(e, eff_idx, ctx):
    """マスクワイプ（マスク画像の輝度をしきい値に使うワイプ）"""
    # マスク画像の輝度をしきい値に使うワイプ
    # （輝度 <= progress*255 の画素から順に現れる）。
    # 注意: movie= の1フレーム入力をそのまま blend に渡すと
    # framesync の T 評価が壊れる（実測: 約5倍速で進行）ため、
    # loop+fps+setpts でメイン入力と同じタイムベースに正規化する。
    # 無限ループは全レンダ経路の -t 指定で確実に打ち切られる。
    prog_expr = e.params.get("progress", Const(1))
    u_expr = _u_expr(ctx.start, ctx.dur, "T")
    prog_str = prog_expr.to_ffmpeg(u_expr)
    img = _escape_ffpath(e.params["image"])
    proj = current_project()
    m_fps = proj.fps if proj else 30
    p = f"{ctx.label_prefix}e{eff_idx}"
    ctx.filters.append("format=rgba")
    ctx.filters.append(
        f"split[{p}a][{p}b];"
        f"[{p}b]alphaextract,split[{p}oa][{p}oar];"
        f"movie=filename={img},loop=loop=-1:size=1,fps={m_fps},"
        f"setpts=N/({m_fps}*TB)[{p}mi];"
        f"[{p}mi][{p}oar]scale=rw:rh[{p}ms];"
        f"[{p}ms]{_MASK_GRAY}[{p}mg];"
        f"[{p}oa][{p}mg]blend="
        f"all_expr='if(lte(B\\,255*({prog_str}))\\,A\\,0)'"
        f":eof_action=repeat[{p}na];"
        f"[{p}a][{p}na]alphamerge"
    )


def _fx_opacity(e, eff_idx, ctx):
    """不透明度（定数は colorchannelmixer、時間だけの式はコマごとの評価、それ以外は geq）"""
    val = e.params.get("value", Const(1.0))
    ctx.filters.append("format=rgba")
    if isinstance(val, Const):
        # 定数は従来どおり値をそのまま書く（opacity の値は構築時に 0〜1 へ検証済み）
        ctx.filters.append(f"colorchannelmixer=aa={val.value}")
        return
    ctx.filters.extend(_alpha_mul_filters(val, eff_idx, ctx, native=False))


def _fx_rounded(e, eff_idx, ctx):
    """角丸（角の中心からの距離が radius を超える画素のアルファを0に）"""
    # clip でX/Yを内側矩形にクランプ → 中央十字帯では距離0（常に表示）
    # r を実寸の半分(min(W,H)/2)で上限クランプ。r>寸法/2 だと内側矩形の
    # クランプ範囲が反転してオブジェクト全体が透明化するため防ぐ。
    radius = e.params["radius"]
    r = f"min({radius}\\,min(W\\,H)/2)"
    corner = (f"lte(hypot(X-clip(X\\,{r}\\,W-1-{r})\\,"
              f"Y-clip(Y\\,{r}\\,H-1-{r}))\\,{r})")
    ctx.filters.append("format=rgba")
    ctx.filters.append(
        f"{_GEQ_RGB}:a='alpha(X\\,Y)*{corner}'"
    )


def _fx_blur_background_fill(e, eff_idx, ctx):
    """ぼかし背景フィル（縦動画変換の定番。出力はキャンバスサイズ固定）"""
    # ぼかした自分自身をキャンバス全面に敷き、中央に本体を fit で重ねる
    proj = current_project()
    cw = proj.width if proj else 1920
    ch = proj.height if proj else 1080
    sigma = e.params.get("blur", 20)
    p = f"{ctx.label_prefix}e{eff_idx}"
    ctx.filters.append("format=rgba")
    ctx.filters.append(
        f"split[{p}a][{p}b];"
        f"[{p}b]scale={cw}:{ch}:force_original_aspect_ratio=increase,"
        f"crop={cw}:{ch},gblur=sigma={sigma}[{p}bg];"
        f"[{p}a]scale={cw}:{ch}:force_original_aspect_ratio=decrease[{p}fg];"
        f"[{p}bg][{p}fg]overlay=(W-w)/2:(H-h)/2:eof_action=pass"
    )
    # 出力はキャンバスサイズ固定 → overlay中央配置の基準を更新
    ctx.pad_size = (cw, ch)


def _fx_plugin(e, eff_idx, ctx):
    """プラグインEffect（plugins/*.py で @effect_plugin 登録）"""
    # pad_state 経由で ctx["expand_pad"]/ctx["set_pad"] による pad_size 更新を受ける
    pad_state = [ctx.pad_size]
    ctx.filters.extend(_build_plugin_effect_filters(
        ctx.obj, e, eff_idx, ctx.start, ctx.dur, ctx.base_dims,
        ctx.label_prefix, pad_state))
    ctx.pad_size = pad_state[0]


# _build_effect_filters が扱わない Effect = 「他の段で処理される Effect」の全集合。
# _FX_BUILDERS との和が、実行時に現れうる Effect 名を過不足なく覆っていなければならない
# （tests/test_fx_dispatch.py のメタテストが manifest 由来の実構築名で検証する）。
#   move / shake        … overlay 座標の変調（_build_move_exprs）
#   trim / speed / reverse / freeze_frame / repeat … 前処理（_build_video_pre_filters）
#   delete              … 入力段で映像ごと捨てる
#   blend_mode          … overlay 合成段（_build_video_overlay_parts）
#   morph_to / explode_to / assemble_from / fly_to … 終端フレーム生成（effects/terminal.py、
#                          project.py の checkpoint 段で素材そのものを差し替える）
_FX_HANDLED_ELSEWHERE = frozenset({
    "move", "trim", "delete", "shake",
    "blend_mode", "speed", "reverse", "freeze_frame", "repeat",
    "morph_to", "explode_to", "assemble_from", "fly_to",
})

# effect名 → ビルダー関数のディスパッチテーブル（プラグインは _EFFECT_PLUGINS 参照）
_FX_BUILDERS = {
    "scale": _fx_scale,
    "fade": _fx_fade,
    "rotate_to": _fx_rotate_to,
    "wipe": _fx_wipe,
    "color_shift": _fx_color_shift,
    "chroma_key": _fx_chroma_key,
    "vignette": _fx_vignette,
    "pixelize": _fx_pixelize,
    "glow": _fx_glow,
    "lut": _fx_lut,
    "glitch": _fx_glitch,
    "perspective_warp": _fx_perspective_warp,
    "lens": _fx_lens,
    "ken_burns": _fx_ken_burns,
    "drop_shadow": _fx_drop_shadow,
    "outline": _fx_outline,
    "tint": _fx_tint,
    "mask": _fx_mask,
    "mask_wipe": _fx_mask_wipe,
    "opacity": _fx_opacity,
    "rounded": _fx_rounded,
    "blur_background_fill": _fx_blur_background_fill,
}


def _build_effect_filters(obj, start, dur, base_dims=None, label_prefix="fx"):
    """scale/fade等のeffectフィルタリストを生成（move/trim/delete以外）
    base_dims指定時、scaleエフェクトにpadを追加して固定サイズ出力にする。
    label_prefix: 複合フィルタ（split/blend等）の中間ラベル接頭辞。
    複数入力を扱う本レンダでは入力indexを含めて一意化する。
    Returns: (filters, pad_size) — pad_size は (max_w, max_h) or None

    注意: glow/drop_shadow/outline は split を含む複合サブグラフ文字列を
    1要素として返す（"split[a][b];[b]...[c];[a][c]blend=..." 形式）。
    カンマ結合されたチェーンに埋め込んでも有効な filtergraph になる。
    """
    ctx = _FxCtx(obj, start, dur, base_dims, label_prefix)
    for eff_idx, e in enumerate(obj.effects):
        if e.name in _FX_HANDLED_ELSEWHERE:
            continue
        builder = _FX_BUILDERS.get(e.name)
        if builder is not None:
            builder(e, eff_idx, ctx)
        elif e.name in _EFFECT_PLUGINS:
            _fx_plugin(e, eff_idx, ctx)
        else:
            # 未登録名を黙って捨てると「フィルタが出ないコマンド」がそのまま
            # スナップショットに焼かれ、以後永久に緑のまま絵だけが消える。
            # プラグイン未ロードの場合も同様に落とす（黙って消えるより明示的に失敗させる）
            raise ValueError(
                f"未登録の Effect 名です: {e.name}"
                f"（_FX_BUILDERS か _FX_HANDLED_ELSEWHERE へ登録してください）")
    return ctx.filters, ctx.pad_size


# anchor（配置基準点）ごとの、指定座標からオブジェクト左上へのオフセット量。
# overlay の x/y は「合成する左上座標」なので、基準点を左上へ換算する分だけ
# 引く。値は各軸で "half"（辺の半分）/ "full"（辺の長さ）/ None（引かない）。
# 語彙は state.py の _PLACEMENT_ANCHORS が正。左右上下は「その辺の中点」を
# 基準にする（例: right = 右辺の中点＝右端に合わせて縦は中央）。
_ANCHOR_OFFSETS = {
    "center":  ("half", "half"),   # 中心
    "topleft": (None,   None),     # 左上の角
    "left":    (None,   "half"),   # 左辺の中点
    "right":   ("full", "half"),   # 右辺の中点
    "top":     ("half", None),     # 上辺の中点
    "bottom":  ("half", "full"),   # 下辺の中点
}


def _terminal_inner_dims(obj):
    """終端フレーム生成Effect を焼いた Object の「余白を除いた元の絵の箱」の寸法。

    explode_to / assemble_from の expand（省略時は自動）と sdf モーフの整列の余白は、
    焼いた動画のキャンバスを左右・上下それぞれ対称に広げる。overlay の `w` / `h` は
    余白込みなので、move の anchor が中心以外（topleft / left / right / top / bottom）の
    とき、そのまま使うと絵が余白ぶんずれる（画面外へ出ることもある）。
    余白は実レンダでしか決まらないため、式には元の箱の寸法だけを定数で入れ、
    余白は `(w-元の幅)/2` として ffmpeg に求めさせる（余白 0 なら従来と同じ位置）。

    元の箱: explode_to / assemble_from は粒子にする画像、morph_to は2枚を中央で
    重ねた共通キャンバス（幅・高さそれぞれ大きい方。morph.load_images と同じ）、
    fly_to は粒になる絵 A（キャンバスは A の中心に対して対称に広がる。morph_flight.py）。
    対象外・寸法が取れないとき（dry_run 中のキャッシュ生成物は常に不明扱い。
    _get_media_dimensions 参照）は (None, None) を返し、呼び出し側は従来の式にする。
    """
    bake = getattr(obj, "_terminal_bake", None)
    if bake is None:
        return None, None
    op, src = bake
    w, h = _get_media_dimensions(src)
    if w is None:
        return None, None
    target = getattr(getattr(op, "_morph_target", None), "source", None)
    if getattr(op, "name", None) == "morph_to" and target is not None:
        tw, th = _get_media_dimensions(target)
        if tw is None:
            return None, None
        w, h = _builtins.max(w, tw), _builtins.max(h, th)
    return w, h


def _terminal_is_flight(obj):
    """焼いた終端フレーム生成Effect が fly_to か（_build_move_exprs の配置の式を分ける）"""
    bake = getattr(obj, "_terminal_bake", None)
    return bake is not None and getattr(bake[0], "name", None) == "fly_to"


def _build_move_exprs(obj, start, dur, pad_size=None):
    """objのeffectsからmoveを探し、overlay用のx_expr/y_exprを返す
    pad_size: (max_w, max_h) padで固定サイズ化済みの場合、定数で位置を計算
    """
    move_effect = None
    for e in obj.effects:
        if e.name == "move":
            move_effect = e

    # pad_size指定時は定数でhalf計算（overlayが完全固定 or move式のみで決まる）
    if pad_size:
        half_w = str(pad_size[0] // 2)
        half_h = str(pad_size[1] // 2)
        full_w = str(pad_size[0])
        full_h = str(pad_size[1])
    else:
        half_w = "w/2"
        half_h = "h/2"
        full_w = "w"
        full_h = "h"

    if move_effect is None:
        # move なしでも shake は適用できるよう、中央配置をベースにして続行する
        x_result = f"(W-{pad_size[0]})/2" if pad_size else "(W-w)/2"
        y_result = f"(H-{pad_size[1]})/2" if pad_size else "(H-h)/2"
    else:
        p = move_effect.params
        anchor_val = p.get("anchor", "center")

        x_param = p.get("x", Const(0.5))
        y_param = p.get("y", Const(0.5))

        u_expr = _u_expr(start, dur)
        base_x = f"{x_param.to_ffmpeg(u_expr)}*W"
        base_y = f"{y_param.to_ffmpeg(u_expr)}*H"

        # 未知の anchor は Effect 構築時に弾かれている（validate.py の
        # _validate_placement_anchor）。ここで KeyError になったら語彙と
        # 実装がずれた証拠なので、黙って topleft へ落とさず失敗させる。
        off_x, off_y = _ANCHOR_OFFSETS[anchor_val]
        sizes_x = {"half": half_w, "full": full_w}
        sizes_y = {"half": half_h, "full": full_h}
        # 終端フレーム生成Effect が足した余白（粒子の expand・sdf モーフの整列の余白）は
        # 対称なので、中心基準（half）は余白があっても絵の位置が変わらない。
        # 辺・角の基準は「余白を除いた元の絵の箱」に合わせる（_terminal_inner_dims）。
        inner_w, inner_h = (None, None) if pad_size else _terminal_inner_dims(obj)
        if inner_w is not None and _terminal_is_flight(obj):
            # fly_to: 元の絵の箱（A）の基準点を静止画と同じ式で丸めてから、対称な余白
            # （偶数の余白の半分＝整数）を引く。中心基準も同じ形にする。余白を trunc の
            # 中で引くと、キャンバスの左端・上端が画面の外（負の座標）に出たとき trunc が
            # 0 の向きへ丸め、静止画の A より 1px 右・下に映る（fly_to のキャンバスは
            # B と道すじを覆うので画面の外へ出やすい。静止画から切り替える瞬間にずれが見える）
            inner_x = {"half": f"{inner_w}/2", "full": f"{inner_w}"}
            inner_y = {"half": f"{inner_h}/2", "full": f"{inner_h}"}
            x_head = f"trunc({base_x}-{inner_x[off_x]})" if off_x else f"trunc({base_x})"
            y_head = f"trunc({base_y}-{inner_y[off_y]})" if off_y else f"trunc({base_y})"
            x_result = f"{x_head}-(w-{inner_w})/2"
            y_result = f"{y_head}-(h-{inner_h})/2"
        else:
            if inner_w is not None:
                sizes_x = {"half": half_w, "full": f"(w+{inner_w})/2"}
                sizes_y = {"half": half_h, "full": f"(h+{inner_h})/2"}
                edge_x, edge_y = f"-(w-{inner_w})/2", f"-(h-{inner_h})/2"
            else:
                edge_x = edge_y = ""
            x_result = f"trunc({base_x}-{sizes_x[off_x]})" if off_x \
                else f"trunc({base_x}{edge_x})"
            y_result = f"trunc({base_y}-{sizes_y[off_y]})" if off_y \
                else f"trunc({base_y}{edge_y})"

    # shake Effect: overlay座標にsin/cosオフセットを加算
    shake_effect = None
    for e in obj.effects:
        if e.name == "shake":
            shake_effect = e
    if shake_effect:
        amp = shake_effect.params.get("amplitude", 0.02)
        freq = shake_effect.params.get("frequency", 10)
        u_expr = _u_expr(start, dur)
        x_shake = f"{amp}*W*sin({freq}*2*PI*{u_expr}+0.7)"
        y_shake = f"{amp}*H*cos({freq}*2.3*PI*{u_expr}+1.3)"
        x_result = f"trunc({x_result}+{x_shake})"
        y_result = f"trunc({y_result}+{y_shake})"

    return x_result, y_result


# 固定格子サンプリングが格子間ピークを取りこぼしうる振動系関数。
# 例: 1 + 0.5*sin(100*PI*u) は i/100 の全標本で 1 だが点間で 1.5 になる。
_OSCILLATORY_FUNCS = {"sin", "cos", "tan", "mod", "random"}


def _expr_has_oscillatory(expr):
    """式に振動系ノード（sin/cos/tan/mod/random）が含まれるかを判定する。

    含まれる場合、固定格子のサンプリングは標本周期とエイリアスして
    最大値の過小評価（pad不足→FFmpeg EINVAL）や native fade への誤変換を
    起こしうるため、呼び出し側は密なサンプリング・保守的な扱いへ切り替える
    （issue #13 P2-13）。
    """
    stack = [expr]
    seen = set()
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if not isinstance(node, Expr):
            continue
        # _FuncCall は name + args を持つ（Var の name は変数名なので args で区別）
        name = getattr(node, "name", None)
        args = getattr(node, "args", None)
        if args is not None and name in _OSCILLATORY_FUNCS:
            return True
        for attr in ("left", "right", "operand"):
            child = getattr(node, attr, None)
            if child is not None:
                stack.append(child)
        if args:
            stack.extend(args)
    return False


def _try_native_fade(alpha_expr, start, dur):
    """alpha式が区分線形ランプと一致するときだけnative fadeへ変換する。

    native fadeはアルファの面だけを触るので最も速いが、矩形窓のような不連続な式を
    ランプへ近似すると透明度の漏れが生じる。全サンプルと隣接差分を候補曲線
    へ照合し、一致を証明できない式は式そのものを評価する正確な経路
    （時間だけの式はコマごとの評価、画素ごとの式は geq。_alpha_path）へ回す。
    """
    # 振動系関数を含む式は標本周期とエイリアスして「全標本が線形ランプに一致」
    # しうる（例: ランプ + sin(200*PI*u) の微小振動）。native化は諦めて
    # 正確な経路へ（issue #13 P2-13）
    if _expr_has_oscillatory(alpha_expr):
        return None
    N = 100
    value_tol = 1e-3
    jump_tol = value_tol * 2.1
    samples = []
    # eval_at と同じ値（if の選んだ枝だけを辿る。expr_scan._compile_u_eval）
    ev = _compile_u_eval(alpha_expr, dur)
    try:
        for i in range(N + 1):
            value = float(ev(i / N))
            if not _math.isfinite(value):
                return None
            # 他の経路と同じ clip 後の値を比較する。
            samples.append(_builtins.min(1.0, _builtins.max(0.0, value)))
    except (TypeError, ValueError, OverflowError):
        return None

    # 現行native最適化の対象は、中央で完全に不透明になる入出力ランプ。
    if abs(samples[N // 2] - 1.0) > value_tol:
        return None

    has_fade_in = abs(samples[0]) <= value_tol
    has_fade_out = abs(samples[-1]) <= value_tol
    if not has_fade_in and abs(samples[0] - 1.0) > value_tol:
        return None
    if not has_fade_out and abs(samples[-1] - 1.0) > value_tol:
        return None

    def _median(values):
        ordered = sorted(values)
        count = len(ordered)
        middle = count // 2
        if count % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2

    fade_in_end_u = 0.0
    if has_fade_in:
        # 線形なら alpha=u/a なので、各中間点から同じ終端aが得られる。
        candidates = [
            (i / N) / samples[i]
            for i in range(1, N // 2)
            if value_tol < samples[i] < 1.0 - value_tol
        ]
        # 0→1の一発ジャンプは中間点を持たないためnative化しない。
        if len(candidates) < 2:
            return None
        fade_in_end_u = round(_median(candidates), 12)

    fade_out_start_u = 1.0
    if has_fade_out:
        # 線形なら alpha=(1-u)/(1-b) なので、各中間点から開始bを得る。
        candidates = [
            1.0 - (1.0 - i / N) / samples[i]
            for i in range(N // 2 + 1, N)
            if value_tol < samples[i] < 1.0 - value_tol
        ]
        if len(candidates) < 2:
            return None
        fade_out_start_u = round(_median(candidates), 12)

    if not (0.0 <= fade_in_end_u <= fade_out_start_u <= 1.0):
        return None

    expected = []
    for i in range(N + 1):
        u = i / N
        fade_in_value = 1.0
        if has_fade_in:
            if fade_in_end_u <= 0:
                return None
            fade_in_value = _builtins.min(1.0, u / fade_in_end_u)
        fade_out_value = 1.0
        if has_fade_out:
            fade_out_width = 1.0 - fade_out_start_u
            if fade_out_width <= 0:
                return None
            fade_out_value = _builtins.min(1.0, (1.0 - u) / fade_out_width)
        expected.append(fade_in_value * fade_out_value)

    if any(abs(actual - ideal) > value_tol
           for actual, ideal in zip(samples, expected)):
        return None

    # 点ごとの近似だけでなく、隣接サンプル間の跳びも候補ランプと一致させる。
    # これによりサンプル境界上のステップ関数も明示的に拒否する。
    for i in range(1, N + 1):
        actual_jump = samples[i] - samples[i - 1]
        expected_jump = expected[i] - expected[i - 1]
        if abs(actual_jump - expected_jump) > jump_tol:
            return None

    # 格子（100 等分）の間に収まる細い山・谷（多点の keyframes の一瞬の明滅など）は
    # 上の照合をすり抜けてランプへ近似され、消えてしまう（実測: 6 秒の 4.02〜4.08 秒の谷が
    # native fade になって消えた）。格子の間でも候補ランプと一致するかを確かめる。
    # 区分線形の式（expr_scan._pl_pieces）は、候補ランプとの差を区分ごとの1次式にして
    # 全区間で確かめる（区分の端の片側極限だけで決まる。手間は点の数に比例し、コマ数に
    # 依らない）。区分線形でない式だけ、実際に描くコマの時刻と keyframes の頂点をすべて
    # 評価する（_frame_us / _expr_breakpoint_us。_compile_u_eval は選んだ枝だけを辿る）
    def _ideal(u):
        fin = _builtins.min(1.0, u / fade_in_end_u) if has_fade_in else 1.0
        fout = (_builtins.min(1.0, (1.0 - u) / (1.0 - fade_out_start_u))
                if has_fade_out else 1.0)
        return fin * fout

    def _ideal_pieces():
        # _ideal と同じ折れ線（入りの終わり ≦ 出の始まりなので、積は区分ごとに片方だけ）
        pieces = []
        x = 0.0
        if has_fade_in:
            pieces.append((0.0, fade_in_end_u, 1.0 / fade_in_end_u, 0.0))
            x = fade_in_end_u
        hold_end = fade_out_start_u if has_fade_out else 1.0
        if hold_end > x:
            pieces.append((x, hold_end, 0.0, 1.0))
            x = hold_end
        if has_fade_out:
            width = 1.0 - fade_out_start_u
            pieces.append((x, 1.0, -1.0 / width, 1.0 / width))
        return pieces

    us = None
    pieces = _pl_pieces(alpha_expr, dur)
    if pieces is not None:
        clipped = _pl_clip01(pieces)
        diff = _pl_sub(clipped, _ideal_pieces()) if clipped is not None else None
        if diff is not None:
            if _pl_max_abs(diff) > value_tol:
                return None
            # 区分の端ちょうどの値（比較の向きで左右どちらに一致するかが変わる）と、
            # 端の前後のコマの値も確かめる
            ends = _pl_ends(diff)
            us = ends + _frame_us_near(start, dur, ends)
    if us is None:
        us = _frame_us(start, dur) + _expr_breakpoint_us(alpha_expr, dur)
    try:
        for u in us:
            value = float(ev(u))
            if not _math.isfinite(value):
                return None
            value = _builtins.min(1.0, _builtins.max(0.0, value))
            if abs(value - _ideal(u)) > value_tol:
                return None
    except (TypeError, ValueError, OverflowError, ZeroDivisionError):
        return None

    result = []
    if has_fade_in:
        fade_in_dur = fade_in_end_u * dur
        result.append(f"fade=t=in:st={start}:d={fade_in_dur}:alpha=1")
    if has_fade_out:
        fade_out_dur = (1.0 - fade_out_start_u) * dur
        fade_out_st = start + fade_out_start_u * dur
        result.append(f"fade=t=out:st={fade_out_st}:d={fade_out_dur}:alpha=1")

    return result if result else None


def _optimize_filter_chain(filters):
    """フィルタチェーンの最適化: 連続format重複を除去"""
    if not filters:
        return filters
    result = []
    for f in filters:
        if f.startswith("format=") and result and result[-1] == f:
            continue
        result.append(f)
    return result


def _estimate_effect_input_length(obj, upto_effect):
    """時間系Effect直前の実効尺を推定する（probe不能時はNone）。

    reverse の長尺ガード用。obj.source の実長に、upto_effect より前の
    trim/speed/freeze_frame を並び順に適用した値を返す。
    """
    proj = current_project()
    base = None
    if proj is not None and getattr(obj, "media_type", None) not in ("image", "text"):
        info = proj._probe_media(obj.source)
        if info:
            base = info.get("duration")
    if base is None:
        base = getattr(obj, "_resolved_length", None)
    if not base:
        return None
    return _fold_time_effects(base, obj.effects, upto=upto_effect)


def _build_video_pre_filters(obj, label_prefix="pre"):
    """trim/speed/reverse/freeze_frame 等の時間系前処理フィルタ（記述順に適用）

    label_prefix: freeze_frame の複合サブグラフ（split/concat）の中間ラベル接頭辞。
    複数入力を扱う本レンダでは入力indexを含めて一意化する。
    """
    filters = []
    for eff_idx, e in enumerate(obj.effects):
        if e.name == "trim":
            d = e.params.get("duration")
            s = e.params.get("start") or 0
            parts = ([f"start={s}"] if s else []) \
                + ([f"duration={d}"] if d is not None else [])
            if parts:
                # trim の duration は「出力の最大尺」（start=2:duration=3 → 2〜5秒）
                filters.append("trim=" + ":".join(parts))
                filters.append("setpts=PTS-STARTPTS")
        elif e.name == "speed":
            factor = e.params.get("factor", 1.0)
            filters.append(f"setpts=PTS/{factor}")
        elif e.name == "reverse":
            # reverse は全フレームをメモリに保持するため長尺を明示エラーにする
            eff_len = _estimate_effect_input_length(obj, e)
            if eff_len is not None and eff_len > _REVERSE_MAX_SEC:
                raise ValueError(
                    f"reverse: 実効尺 {eff_len:.1f}s が上限 {_REVERSE_MAX_SEC:.0f}s "
                    f"を超えています ('{obj.source}')。\n"
                    f"reverse は全フレームをメモリに保持するため長尺には使えません。"
                    f"trim() で対象区間を短くしてから適用してください。")
            filters.append("reverse")
        elif e.name == "repeat":
            # obj * n（DSL糖衣）: 区間全体を n 回連続再生。
            # segment は構築時に確定済みの区間実尺（dry_run でも決定的）。
            # loop の size はフレーム数。素材の実フレーム数は fps や
            # speed 適用で Project fps と食い違うため、先に fps フィルタで
            # Project fps へ正規化してから size=segment×fps で全区間を掴む
            # （正規化しないと 10fps 素材の2周目以降が空になる。監査 issue #15）
            n = e.params["count"]
            segment = e.params["segment"]
            proj = current_project()
            fps = proj.fps if proj else 30
            frames = _builtins.max(1, int(_math.ceil(segment * fps)))
            filters.append(f"fps={fps}")
            filters.append(f"loop=loop={n - 1}:size={frames}:start=0")
            # loop 後の PTS はフレーム番号基準で振り直す（連続再生に整列）
            filters.append(f"setpts=N/({fps}*TB)")
        elif e.name == "freeze_frame":
            # 指定時刻のフレームで duration 秒静止 → 続きを再生（総尺 +duration）
            # trim 3分割 + loop(先頭フレーム複製) + concat のチェーン内サブグラフ
            at = e.params["at"]
            fdur = e.params["duration"]
            # at がクリップ実長以上だと trim=start=at が空ストリームになり
            # concat 失敗/末尾欠落を起こす。実効尺（前段trim/speed反映）と照合する。
            eff_len = _estimate_effect_input_length(obj, e)
            if eff_len is not None and at >= eff_len:
                raise ValueError(
                    f"freeze_frame: at={at}s がクリップ実効尺 {eff_len:.3f}s "
                    f"以上です ('{obj.source}')。\n"
                    f"素材長より前の時刻を指定してください。")
            p = f"{label_prefix}f{eff_idx}"
            filters.append(
                f"split=3[{p}a][{p}b][{p}c];"
                f"[{p}a]trim=duration={at},setpts=PTS-STARTPTS[{p}s1];"
                f"[{p}b]trim=start={at},setpts=PTS-STARTPTS,"
                f"loop=loop=-1:size=1,trim=duration={fdur},setpts=PTS-STARTPTS[{p}s2];"
                f"[{p}c]trim=start={at},setpts=PTS-STARTPTS[{p}s3];"
                f"[{p}s1][{p}s2][{p}s3]concat=n=3:v=1:a=0"
            )
    return filters


# --- 循環 import の回避（同一 SCC のモジュールのみ末尾で束縛。scripts/check_import_cycles.py で計測）---
from scriptvedit.cache import _fold_time_effects, _is_cache_artifact_path, _is_hold_artifact_path, _is_pending_cache_path
from scriptvedit.ffmpeg import _decoder_input_args
from scriptvedit.plugins import _EFFECT_PLUGINS, _build_plugin_effect_filters
from scriptvedit.text import _build_text_filters, _escape_ffpath
