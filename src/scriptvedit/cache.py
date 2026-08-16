# -*- coding: utf-8 -*-

import os
import json
import hashlib
import warnings
import time as _time
import shutil as _shutil
import threading as _threading
import builtins as _builtins

# context は scriptvedit 内 import を持たない葉なので先頭で import できる。
from scriptvedit.context import current_project

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.expr import Expr, max
from scriptvedit.state import _ARTIFACT_DIR, _BAKE_PIXFMT_VER, _BAKEABLE_EFFECTS, _CACHE_DIR, _ENGINE_VER, _TERMINAL_FRAME_EFFECTS, _detect_media_type


# --- ファイル指紋（内容ハッシュ方式）---
#
# 指紋は「ファイル内容の sha256 先頭16桁」。パスにも mtime にも依存しないため、
# 別マシンへの clone・ファイルのコピー・CRLF変換なしの touch ではキャッシュ鍵が
# 変わらない（＝スナップショットが環境をまたいで一致する＝移植性）。
#
# 性能対策はプロセス内メモ化（_FFP_MEMO）のみ。
#   同一 render 中に同じファイルを再ハッシュしないためのメモ化で、
#   参照キーは (絶対パス, サイズ, mtime_ns)。
#
# ディスクキャッシュ（かつての __cache__/ffp.json）は**意図的に廃止**した。
#   (絶対パス, サイズ, mtime_ns) を参照キーにディスクへ永続化すると、
#   `cp -p` / `rsync -t` / `tar -x` / `unzip -o` など mtime を保持するツールで
#   同サイズの別内容へ差し替えたときに古い内容ハッシュを返し、
#   「素材を変えたのに再生成されない」= 内容ハッシュ化の目的そのものが破れる。
#   実測でコールド 14.5ms（素材17件11.3MB）しかかからず、正しさとのトレードオフに
#   見合わない。
#   プロセス内メモ化も理屈は同じだが、単一レンダの実行中にファイルが差し替わる想定は
#   非現実的なので許容する。
_FFP_MEMO = {}
_FFP_LOCK = _threading.Lock()


def _hash_file_content(path):
    """ファイル全内容の sha256 先頭16桁（1MBずつのチャンク読み）"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _file_fingerprint(path):
    """ファイル内容の指紋（sha256 先頭16桁の文字列）を返す。

    パス・mtime に依存しないので、同一内容のファイルはどこに置いても同じ指紋になる。
    ファイルが無ければ従来通り OSError を送出する。
    """
    st = os.stat(path)  # 不在なら OSError（呼び出し側が捕捉している）
    key = f"{os.path.abspath(path).replace(chr(92), '/')}|{st.st_size}|{st.st_mtime_ns}"
    with _FFP_LOCK:
        h = _FFP_MEMO.get(key)
        if h is not None:
            return h
    h = _hash_file_content(path)  # I/O はロック外で
    with _FFP_LOCK:
        _FFP_MEMO[key] = h
    return h


def _web_source_fingerprint(path):
    """HTML sourceの改行だけをCRLFへ正規化した内容指紋。

    ブラウザのHTML入力ではLF/CRLFの差に意味がない一方、共有フォルダやeditorで
    改行だけが変わると長尺Web cacheが全再生成される。repo規約のCRLFへ寄せるため、
    現在の追跡HTMLの既存fingerprintを維持しつつ外部LF版も同じキーにする。
    一般素材とdepsは引き続き厳密なraw fingerprintを使う。
    """
    with open(path, "rb") as source:
        content = source.read()
    normalized = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    normalized = normalized.replace(b"\n", b"\r\n")
    return hashlib.sha256(normalized).hexdigest()[:16]


def _is_cache_artifact_path(path):
    """パスがキャッシュディレクトリ(__cache__)配下の生成物かどうか判定"""
    abs_path = os.path.abspath(path).replace("\\", "/")
    cache_root = os.path.abspath(_CACHE_DIR).replace("\\", "/")
    return abs_path.startswith(cache_root + "/")


def _is_pending_cache_path(path):
    """未生成のキャッシュ予定パスかどうか判定（dry_run中のprobe抑制用）

    dry_runではチェックポイント等のsourceが「これから生成される予定のパス」に
    差し替わるため、存在しないキャッシュ配下パスへのffprobeは警告スパムになる。
    """
    return (not os.path.exists(path)) and _is_cache_artifact_path(path)


# ファイルパスを値に持つパラメータ（指紋には生パスではなく内容指紋を使う）。
# 生パスを混ぜるとリポジトリの置き場所でキャッシュ鍵が変わり移植性が失われるため、
# ここに挙げたキーはパラメータ列挙から除外し、下で *_ffp として内容指紋を足す。
_OP_PATH_PARAMS = {"lut": ("file",), "mask": ("image",), "mask_wipe": ("image",)}

# `~` の軽量代替を実装済みの内部op名。未対応opの品質ヒントは出力を変えず、
# 同一出力のキャッシュ鍵を分裂させない。対応を追加するときは、実処理と同時に
# ここへ内部op名を登録する。
_FAST_HINT_OPS = frozenset()


def _respects_fast_hint(op):
    """op（または内部op名）が `~` の軽量代替を実装しているか返す"""
    name = op if isinstance(op, str) else getattr(op, "name", None)
    return name in _FAST_HINT_OPS


def _effective_quality(op):
    """実際の出力へ影響する品質値だけを返す"""
    if _respects_fast_hint(op) and getattr(op, "quality", "final") == "fast":
        return "fast"
    return "final"


def _ops_effective_quality(ops):
    """op列に軽量代替を使うものが1つでもあればfast、それ以外はfinal"""
    return "fast" if any(_effective_quality(op) == "fast" for _, op in ops) else "final"


def _op_fingerprint_str(op):
    """単一opのフィンガープリント文字列を生成"""
    parts = [op.name]
    skip = _OP_PATH_PARAMS.get(op.name, ())
    for k in sorted(op.params):
        if k in skip:
            continue
        v = op.params[k]
        parts.append(f"{k}={v.to_ffmpeg('u') if isinstance(v, Expr) else repr(v)}")
    # policy はレンダ結果に影響しないためフィンガープリントに含めない
    quality = _effective_quality(op)
    parts.append(f"q={quality}")
    # morph_to: ターゲット画像のFFPをsignatureに含める
    if op.name == "morph_to" and hasattr(op, '_morph_target'):
        try:
            tgt_ffp = _file_fingerprint(op._morph_target.source)
            parts.append(f"tgt_ffp={tgt_ffp}")
        except OSError:
            parts.append(f"tgt_src={_norm_src_path(str(op._morph_target.source))}")
    # assemble_from: 集合元画像のFFPをsignatureに含める
    if op.name == "assemble_from" and hasattr(op, '_assemble_source'):
        try:
            parts.append(f"asm_ffp={_file_fingerprint(op._assemble_source.source)}")
        except OSError:
            parts.append(f"asm_src={_norm_src_path(str(op._assemble_source.source))}")
    # lut: LUTファイルのFFPをsignatureに含める（内容変更でキャッシュ無効化）
    if op.name == "lut":
        lut_file = op.params.get("file")
        try:
            parts.append(f"lut_ffp={_file_fingerprint(lut_file)}")
        except (OSError, TypeError):
            parts.append(f"lut_src={_norm_src_path(str(lut_file))}")
    # mask/mask_wipe: マスク画像のFFPをsignatureに含める（内容変更でキャッシュ無効化）
    if op.name in ("mask", "mask_wipe"):
        mask_img = op.params.get("image")
        try:
            parts.append(f"mask_ffp={_file_fingerprint(mask_img)}")
        except (OSError, TypeError):
            parts.append(f"mask_src={_norm_src_path(str(mask_img))}")
    # プラグインEffect: ソースコードの内容ハッシュを鍵に含める
    # （プラグインを書き換えたらキャッシュが再生成されるように）
    plug = _EFFECT_PLUGINS.get(op.name)
    if plug is not None:
        parts.append(f"plugin_ffp={plug.code_ffp}")
    return "|".join(parts)


def _sig_key(sigs):
    """署名リストからキャッシュ鍵（"||" 結合 → sha256 → 先頭16桁）を作る。

    "||" 結合・sha256・[:16] は既存キャッシュ資産とスナップショットに焼き込まれた
    不変条件。1バイトでも変えると全キャッシュ無効化＋dry_runコマンド崩壊になる。
    """
    return hashlib.sha256("||".join(sigs).encode()).hexdigest()[:16]


def _op_prefix_fingerprint(ops_list):
    """ops列のSHA256[:16]フィンガープリントを計算"""
    sigs = []
    for typ, op in ops_list:
        sigs.append(f"{typ}:{_op_fingerprint_str(op)}")
    return _sig_key(sigs)


def _is_bakeable(op_type, op):
    """opがbakeable（チェックポイント保存対象）かどうか判定"""
    if op_type == "transform":
        return True
    if op_type == "effect" and op.name in _BAKEABLE_EFFECTS:
        return True
    return False


def _compute_save_points(ops):
    """保存点を計算: FSP(forceの全位置) + RAA(最右auto、force以降になければ)
    ops: [(type, op), ...]
    戻り値: set of indices
    """
    save_points = set()
    # FSP: policy="force" の全位置（bakeableかつforce）
    force_indices = []
    for i, (typ, op) in enumerate(ops):
        if getattr(op, 'policy', 'auto') == "force" and _is_bakeable(typ, op):
            save_points.add(i)
            force_indices.append(i)

    # RAA: bakeable ops中の最右 policy="auto"（最後のFSP以降にforceがなければ）
    last_force = max(force_indices) if force_indices else -1
    raa_candidate = None
    for i, (typ, op) in enumerate(ops):
        policy = getattr(op, 'policy', 'auto')
        if policy == "auto" and _is_bakeable(typ, op):
            raa_candidate = i
    # RAAはFSP以降にforceがない場合のみ有効（= 最後のforce以降にautoがある場合）
    if raa_candidate is not None and raa_candidate > last_force:
        save_points.add(raa_candidate)

    return save_points


def _src_signature(path):
    """ソース(素材/中間生成物)の署名。鍵本体・バケットで共通に使う唯一の方針。

    - **キャッシュ生成物（__cache__配下）はパス署名**。パス自体が内容由来の鍵を
      含むうえ、dry_run 時点では未生成でFFPが取れないため、内容指紋にすると
      「実レンダ後だけ鍵が変わる」= dry_run と実レンダのパスが食い違う。
    - **素材は内容指紋**（置き場所に依存しない＝移植性）。
    - 読めない素材だけパス署名へフォールバック。
    """
    if _is_cache_artifact_path(path):
        return f"src={_norm_src_path(path)}"
    try:
        return f"ffp={_file_fingerprint(path)}"
    except (OSError, TypeError):
        return f"src={_norm_src_path(path)}"


def _src_bucket(path):
    """キャッシュ生成物を仕分けるサブディレクトリ名（8桁）

    素材は内容指紋から導出するため、リポジトリを別の場所へ置いても同じバケットに
    なる（移植性）。ソースがキャッシュ生成物ならパスベース（_src_signature と同方針）。
    バケットだけ方針を変えると、上流キャッシュ生成物を持つ下流アーティファクト
    （morph/particle/checkpoint）のパスが __cache__ の有無で変わってしまう。
    """
    if _is_cache_artifact_path(path):
        return hashlib.sha256(_norm_src_path(path).encode()).hexdigest()[:8]
    try:
        return _file_fingerprint(path)[:8]
    except OSError:
        return hashlib.sha256(_norm_src_path(path).encode()).hexdigest()[:8]


def _norm_src_path(path):
    """パス文字列を正規化（cwd配下なら相対化・区切りは / ）"""
    try:
        rel = os.path.relpath(path, os.getcwd())
        if not rel.startswith(".."):
            path = rel
    except ValueError:
        pass  # 別ドライブ等（Windows）はそのまま
    return path.replace("\\", "/")


def _checkpoint_cache_path(original_source, ops, duration=None, fps=None, quality="final"):
    """チェックポイントのキャッシュファイルパスを計算（signature方式）"""
    # 素材=内容指紋 / キャッシュ生成物(web webm 等)=パス署名（dry_runと実レンダで鍵一致）
    sigs = [_src_signature(original_source)]
    opfp = _op_prefix_fingerprint(ops)
    sigs.append(opfp)
    # 呼び出し側のraw hintではなく、prefix全体で出力に効く品質だけを鍵へ入れる。
    quality = _ops_effective_quality(ops)
    sigs.append(f"q={quality}")
    # 注: 生成される中間物の内容は draft/本番で同一のため、_ACTIVE_QUALITY(rq)は
    # 鍵に含めない（含めると本番↔draft で全キャッシュミスになり無駄な再生成が起きる）
    sigs.append(f"ev={_ENGINE_VER}")
    # 中間ベイクのpix_fmt世代。.mkv を焼く経路のみ鍵に入れる
    # （静止画チェックポイントはPNGで pix_fmt 変更の影響を受けないため）
    is_video = _detect_media_type(original_source) in ("video",)
    if duration is not None or is_video:
        sigs.append(f"bpf={_BAKE_PIXFMT_VER}")
    if duration is not None:
        sigs.append(f"dur={duration}")
    if fps is not None:
        sigs.append(f"fps={fps}")
    # Project解像度も鍵に含める。blur_background_fill 等、Project寸法に依存する
    # フィルタを焼くopがあり、320pと1080pで異なる出力が同一パスへ衝突していた
    # （監査 issue #16 P1）。解像度非依存opでも分かれるが正しさを優先する
    proj = current_project()
    if proj is not None:
        sigs.append(f"pctx={proj.width}x{proj.height}@{proj.fps}")
    key = _sig_key(sigs)
    # video入力 + transform-only でも動画ならmkv (ffv1)
    ext = ".mkv" if (duration is not None or is_video) else ".png"
    cache_dir = os.path.join(_ARTIFACT_DIR, "checkpoint", _src_bucket(original_source))
    return os.path.join(cache_dir, f"{key}{ext}")


# モーフ/粒子レンダラの版。morph.py の描画結果が変わったら上げる
# （_ENGINE_VER を上げると全キャッシュが飛ぶため、morph 系だけを無効化する）
# "3": 中間ベイクの pix_fmt を yuva444p → bgra に変更（色変換由来の劣化を除去）。
#      旧 yuva444p の中間物を再利用させないため版を上げる
_MORPH_RENDER_VER = "3"


def _morph_cache_path(src_path, morph_op, duration, fps, quality="final"):
    """morph WebMのキャッシュパスを計算"""
    # キャッシュ生成物はパス署名、素材は内容指紋（_src_signature に一本化）
    sigs = [_src_signature(src_path)]
    # ターゲットFFP
    if hasattr(morph_op, '_morph_target'):
        try:
            sigs.append(f"tgt_ffp={_file_fingerprint(morph_op._morph_target.source)}")
        except OSError:
            sigs.append(f"tgt_src={morph_op._morph_target.source}")
    sigs.append(f"op={_op_fingerprint_str(morph_op)}")
    sigs.append(f"dur={duration}")
    sigs.append(f"fps={fps}")
    quality = _effective_quality(morph_op)
    sigs.append(f"q={quality}")
    # 中間物は draft/本番で同一内容のため rq(_ACTIVE_QUALITY)は鍵に含めない
    sigs.append(f"ev={_ENGINE_VER}")
    sigs.append(f"mv={_MORPH_RENDER_VER}")
    key = _sig_key(sigs)
    cache_dir = os.path.join(_ARTIFACT_DIR, "morph", _src_bucket(src_path))
    return os.path.join(cache_dir, f"{key}.mkv")


def _particle_cache_path(img_path, particle_op, duration, fps, quality="final"):
    """explode_to/assemble_from の粒子アニメmkvキャッシュパスを計算

    img_path: 粒子化する単一画像（explode=直前ソース, assemble=集合元）
    """
    sigs = [_src_signature(img_path)]
    sigs.append(f"op={_op_fingerprint_str(particle_op)}")
    sigs.append(f"dur={duration}")
    sigs.append(f"fps={fps}")
    quality = _effective_quality(particle_op)
    sigs.append(f"q={quality}")
    # 中間物は draft/本番で同一内容のため rq(_ACTIVE_QUALITY)は鍵に含めない
    sigs.append(f"ev={_ENGINE_VER}")
    sigs.append(f"mv={_MORPH_RENDER_VER}")
    key = _sig_key(sigs)
    cache_dir = os.path.join(_ARTIFACT_DIR, "particle", _src_bucket(img_path))
    return os.path.join(cache_dir, f"{key}.mkv")


def _morph_input_frame_path(src_path):
    """morph入力用の最終フレームPNGの置き場所を導出

    morph（PIL）は画像しか読めないため、動画ソース（前ベイクの.mkv等）は
    最終フレームをRGBA PNGに抽出してからmorphの入力にする。
    """
    if _is_cache_artifact_path(src_path):
        # キャッシュ生成物: 拡張子差し替え（パス自体が内容由来の鍵を含む）
        return os.path.splitext(src_path)[0] + ".morphsrc.png"
    # 元素材が動画: キャッシュ配下に内容由来の鍵で生成
    key = hashlib.sha256(_src_signature(src_path).encode()).hexdigest()[:16]
    return os.path.join(_ARTIFACT_DIR, "morph", "src", f"{key}.png")


def _build_morph_frame_extract_cmd(src_path, frame_path):
    """動画の最終フレームをRGBA PNGに抽出するffmpegコマンド（morph入力用）

    -sseof -0.5: 終端0.5秒前からデコード
    -update 1: 残り全フレームを同一ファイルへ上書き → 最終フレームが残る
    -pix_fmt rgba: alpha維持（前ベイクのffv1 bgra等の透過を保つ）
    """
    cmd = ["ffmpeg", "-y", "-sseof", "-0.5"]
    cmd.extend(_decoder_input_args(src_path, "video", None))
    cmd.extend(["-update", "1", "-pix_fmt", "rgba", frame_path])
    return cmd


def _validate_morph_position(bakeable_ops):
    """終端フレーム生成Effect(morph_to/explode_to/assemble_from)が
    bakeable opsの末尾に1つだけあることを検証"""
    term_indices = [i for i, (typ, op) in enumerate(bakeable_ops)
                    if typ == "effect" and op.name in _TERMINAL_FRAME_EFFECTS]
    if not term_indices:
        return
    if len(term_indices) > 1:
        names = [bakeable_ops[i][1].name for i in term_indices]
        raise ValueError(
            f"morph_to/explode_to/assemble_from は1つのObjectに1回しか適用できません"
            f"（{len(term_indices)}個指定: {names}, idx={term_indices}）。\n"
            f"複数段には compute() 等で中間素材を生成して分割してください。")
    term_idx = term_indices[0]
    term_name = bakeable_ops[term_idx][1].name
    # 終端Effectの後に他のbakeable opがあればエラー（policy='off'は実質ライブなのでスキップ）
    for i in range(term_idx + 1, len(bakeable_ops)):
        after_op = bakeable_ops[i][1]
        if getattr(after_op, 'policy', 'auto') == "off":
            continue
        raise ValueError(
            f"{term_name} はbakeable opsの末尾に配置してください。"
            f"{term_name}(idx={term_idx})の後に "
            f"{after_op.name}(idx={i})があります。\n"
            f"回避策: {after_op.name} を {term_name} の前に移動するか、"
            f"-{after_op.name}(...) で checkpoint対象から除外してください。")


def _build_unified_ops(obj):
    """transforms + effects を統合ops列に変換（2-tuple: type, op）"""
    ops = []
    for t in obj.transforms:
        ops.append(("transform", t))
    for e in obj.effects:
        ops.append(("effect", e))
    return ops


def _split_ops(ops):
    """ops列をbakeable/liveに分離"""
    bakeable = [(t, op) for t, op in ops if _is_bakeable(t, op)]
    live = [(t, op) for t, op in ops if not _is_bakeable(t, op)]
    return bakeable, live


def _fold_time_effects(duration, effects, upto=None, *, audio=False):
    """時間系エフェクトを並び順に尺へ畳み込む（映像/音声共通ヘルパ）。

    映像: trim / speed / freeze_frame / repeat、
    音声(audio=True): atrim / atempo / arepeat（freeze_frame は音声に無い）。
    upto 指定時はその Effect の直前まで畳み込む（reverse/freeze_frame の
    「このEffectの入力実効尺」推定用）。
    Object.length()（objects.py）と _estimate_effect_input_length
    （filters/video.py）の共通実装。
    """
    trim_name = "atrim" if audio else "trim"
    tempo_name = "atempo" if audio else "speed"
    tempo_key = "rate" if audio else "factor"
    repeat_name = "arepeat" if audio else "repeat"
    cur = duration
    for e in effects:
        if e is upto:
            break
        if e.name == trim_name:
            s = e.params.get("start") or 0
            if s:
                cur = _builtins.max(0.0, cur - s)
            d = e.params.get("duration")
            if d is not None:
                cur = _builtins.min(cur, d)
        elif e.name == tempo_name:
            f = e.params.get(tempo_key, 1.0)
            if f > 0:
                cur = cur / f
        elif not audio and e.name == "freeze_frame":
            # at がその時点の実効尺以上なら静止区間は成立しないため加算しない
            # （_build_video_pre_filters 側では ValueError になるが、length()は
            #   実尺との整合を保つため at>=尺 では +duration を計上しない）
            at = e.params.get("at", 0.0)
            if at < cur:
                cur = cur + e.params.get("duration", 0.0)
        elif e.name == repeat_name:
            cur = cur * e.params.get("count", 1)
    return cur


def _apply_time_effects_to_duration(dur, effects):
    """時間系 live Effect（speed/freeze_frame）を尺に反映した表示尺を返す。

    speed: 尺 / factor、freeze_frame: 尺 + duration、reverse: 変化なし。
    effects の並び順に適用する。

    ※ _fold_time_effects と似ているが統合しない: こちらは checkpoint 後の
    live op 列専用で trim を意図的に畳まない（trim は bakeable でベイク側の
    尺に反映済み）。また要素が Effect 以外でも落ちないよう getattr で名前を
    取る。speed のガードも `if f:`（truthy）のままにしてある。
    """
    cur = dur
    for e in effects:
        name = getattr(e, "name", None)
        if name == "speed":
            f = e.params.get("factor", 1.0)
            if f:
                cur = cur / f
        elif name == "freeze_frame":
            # at がその時点の実効尺以上なら静止区間は成立しない（length()と整合）
            at = e.params.get("at", 0.0)
            if at < cur:
                cur = cur + e.params.get("duration", 0.0)
        elif name == "repeat":
            # obj * n（DSL糖衣）: n回連続再生で尺は n 倍
            cur = cur * e.params.get("count", 1)
    return cur


_RENDERER_IDENTITY_MEMO = [None]


def _renderer_identity():
    """HTML→画素を実際に生成するレンダラ(Playwright同梱Chromium)の識別子。

    pip の playwright バージョンは同梱 Chromium リビジョンと1対1のため、
    ブラウザを起動せずに取れる軽量な代理識別子として使う。Chromium 更新で
    フォントラスタライズ等が変わっても旧PNG/WebMを使い続けない
    （監査 issue #16 P3）。未導入なら "none"（そもそも生成できない）。
    """
    if _RENDERER_IDENTITY_MEMO[0] is None:
        try:
            from importlib.metadata import version
            _RENDERER_IDENTITY_MEMO[0] = f"playwright-{version('playwright')}"
        except Exception:
            _RENDERER_IDENTITY_MEMO[0] = "playwright-none"
    return _RENDERER_IDENTITY_MEMO[0]


def _web_cache_path(obj, project):
    """Web Objectのsignatureベースキャッシュパスを計算"""
    sigs = [f"renderer={_renderer_identity()}"]
    # テンプレートファイルのフィンガープリント
    try:
        ffp = _web_source_fingerprint(obj._web_source)
        sigs.append(f"ffp={ffp}")
    except (OSError, TypeError):
        sigs.append(f"src={obj._web_source}")
    # データハッシュ
    data_str = json.dumps(obj._web_data, sort_keys=True, default=str)
    sigs.append(f"data={hashlib.sha256(data_str.encode()).hexdigest()[:12]}")
    sigs.append(f"dur={obj.duration}")
    capture = obj._web_capture_spec(project)
    fps = capture["fps"]
    sigs.append(f"fps={fps}")
    # full finalの従来keyは維持し、draft低fps/部分captureだけを別cacheにする。
    if (capture["fps"] != capture["base_fps"]
            or capture["frame_start"] != 0
            or capture["frame_end"] != capture["full_frames"]):
        sigs.append(
            f"capture={capture['frame_start']}:{capture['frame_end']}"
            f"/{capture['full_frames']}")
    if obj._web_size:
        sigs.append(f"size={obj._web_size[0]}x{obj._web_size[1]}")
    if obj._web_deps:
        deps_fps = []
        for dep in sorted(obj._web_deps):
            try:
                deps_fps.append(str(_file_fingerprint(dep)))
            except OSError:
                deps_fps.append(dep)
        sigs.append(f"deps={hashlib.sha256('|'.join(deps_fps).encode()).hexdigest()[:12]}")
    sigs.append(f"ev={_ENGINE_VER}")
    key = _sig_key(sigs)
    name = obj._web_name or "web"
    return os.path.join(_ARTIFACT_DIR, "web", name, f"{key}.webm")


# --- レイヤーキャッシュの品質段階 -------------------------------------------
# 値 → (拡張子, ffmpegエンコード引数)。中間ファイルの品質/サイズを選ぶ。
#
# 拡張子とデコーダ選択は結合している（_decoder_input_args と対で維持すること）:
#   .webm … VP9+alpha。再利用時に libvpx-vp9 を強制する（ネイティブVP9デコーダは
#           alpha非対応で、透過が黒背景化して下層レイヤーを覆う。issue #13 P1-3）
#   .mkv  … FFV1。webmコンテナはFFV1を格納できないため別コンテナにする。
#           FFV1はネイティブデコーダがbgraのalphaを正しく復号するので強制不要。
#
# クロマ間引きについての実測メモ（1080p30・細い文字＋高彩度のコードパネル素材）:
#   量子化を完全に止めても yuva420p である限り PSNR は 49.97dB で頭打ち
#   （＝クロマ間引き固有の誤差下限）。draft(crf30)=47.60dB, balanced(crf15)=49.13dB。
#   alpha を持てる非可逆コーデックは 4:2:0 の libvpx-vp9 しか無く（libvpx-vp9 は
#   yuva444p 非対応、prores_ks 4444 は同素材で可逆FFV1の2.3〜3.1倍に肥大）、
#   **クロマ間引きを完全に無くしたい場合は lossless を選ぶ**しかない。
_LAYER_CACHE_QUALITY = {
    # プレビュー用。従来の既定値と同一設定（既存キャッシュ資産と同じ絵）
    "draft": (".webm", [
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
        "-b:v", "0", "-crf", "30", "-auto-alt-ref", "0",
    ]),
    # 折衷（既定）。二重劣化のうち量子化分をほぼ除去し、可逆の約1/8サイズ
    "balanced": (".webm", [
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
        "-b:v", "0", "-crf", "15", "-auto-alt-ref", "0",
    ]),
    # 品質最優先。完全可逆（ビット完全）。クロマ間引きも色変換も起きない
    #
    # pix_fmt が bgra であって yuva444p でないのは実測に基づく:
    # FFV1 yuva444p は RGBA→YUV の行列変換を挟むため、
    #   (a) 生成プロセスと再利用プロセスで colorspace タグが unknown のまま
    #       別々に推定され（601/709 の食い違い）、高彩度の文字で最大68/255 の
    #       色ずれが出た（文字部PSNR 27.9dB＝draft より悪い）
    #   (b) colorspace/color_range を明示しても往復はビット完全にならない
    # bgra はチャンネル並べ替えのみで色変換が無く、実測でビット完全（PSNR=inf,
    # 最大誤差0）。サイズは yuva444p 比 +5% 程度に収まる。
    "lossless": (".mkv", [
        "-c:v", "ffv1", "-level", "3", "-pix_fmt", "bgra",
    ]),
}

_DEFAULT_LAYER_CACHE_QUALITY = "balanced"


def _resolve_layer_cache_quality(quality):
    """レイヤーキャッシュ品質を検証して正規化する（None は既定値）"""
    if quality is None:
        return _DEFAULT_LAYER_CACHE_QUALITY
    if quality not in _LAYER_CACHE_QUALITY:
        raise ValueError(
            f"cache_quality引数は "
            f"{', '.join(repr(k) for k in _LAYER_CACHE_QUALITY)} "
            f"のいずれか: {quality!r}")
    return quality


def _layer_cache_encode_args(quality):
    """品質に対応するffmpegエンコード引数（コピーを返す）"""
    return list(_LAYER_CACHE_QUALITY[_resolve_layer_cache_quality(quality)][1])


def _layer_cache_paths(filename, project, quality=None):
    """レイヤーキャッシュパスを計算（signature方式）

    quality: レイヤーキャッシュ品質（None で既定）。**鍵と拡張子の両方**に効く。
    鍵に含めないと品質を変えても古い中間ファイルが再利用される。
    """
    quality = _resolve_layer_cache_quality(quality)
    ext = _LAYER_CACHE_QUALITY[quality][0]
    basename = os.path.splitext(os.path.basename(filename))[0]
    sigs = []
    try:
        ffp = _file_fingerprint(filename)
        sigs.append(f"ffp={ffp}")
    except (OSError, TypeError):
        sigs.append(f"src={filename}")
    sigs.append(f"ev={_ENGINE_VER}")
    sigs.append(f"w={project.width}")
    sigs.append(f"h={project.height}")
    sigs.append(f"fps={project.fps}")
    sigs.append(f"bg={project.background_color}")
    # 出力尺も鍵に含める（キャッシュは -t dur で尺を焼き込むため、
    # 総尺変更後に旧キャッシュを再利用すると短尺切れ/古い尺が戻る。issue #13 P1-4）
    # render経路ではplan pass直後に総尺が確定済み（_render_impl参照）
    sigs.append(f"dur={project.duration}")
    # 品質も鍵の一部（同じ素材でも draft と lossless は別成果物）。
    # 名前だけでなく**実際のエンコード引数**を含めることで、将来 crf/pix_fmt を
    # 調整したときに古い中間ファイルが黙って再利用されるのを防ぐ。
    sigs.append(f"q={quality}:{' '.join(_LAYER_CACHE_QUALITY[quality][1])}")
    key = _sig_key(sigs)
    layer_dir = os.path.join(_ARTIFACT_DIR, "layer", basename)
    return (os.path.join(layer_dir, f"{key}{ext}"),
            os.path.join(layer_dir, f"{key}.anchors.json"))


def _iter_cache_files(cache_dir=_CACHE_DIR):
    """__cache__ 配下の全ファイルを (絶対パス, カテゴリ, サイズ, mtime) で列挙する"""
    if not os.path.isdir(cache_dir):
        return
    root_abs = os.path.abspath(cache_dir)
    for dirpath, _dirs, files in os.walk(cache_dir):
        for name in files:
            path = os.path.join(dirpath, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            rel = os.path.relpath(path, root_abs)
            parts = rel.replace("\\", "/").split("/")
            # artifacts/<種別>/... は種別を、それ以外は先頭ディレクトリをカテゴリに
            if parts[0] == "artifacts" and len(parts) > 1:
                category = parts[1]
            elif len(parts) > 1:
                category = parts[0]
            else:
                category = "(直下)"
            yield path, category, st.st_size, st.st_mtime


def _fmt_size(n):
    """バイト数を人間可読な単位で整形"""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024.0


def cache_stats(cache_dir=_CACHE_DIR):
    """種別ごとの件数・合計サイズを集計して表示する"""
    stats = {}
    total_n = 0
    total_sz = 0
    for _path, category, size, _mtime in _iter_cache_files(cache_dir):
        c = stats.setdefault(category, [0, 0])
        c[0] += 1
        c[1] += size
        total_n += 1
        total_sz += size
    print(f"=== キャッシュ統計: {os.path.abspath(cache_dir)} ===")
    if total_n == 0:
        print("  (キャッシュはありません)")
        return
    print(f"  {'種別':<16} {'件数':>8} {'サイズ':>12}")
    print("  " + "-" * 38)
    for category in sorted(stats):
        n, sz = stats[category]
        print(f"  {category:<16} {n:>8} {_fmt_size(sz):>12}")
    print("  " + "-" * 38)
    print(f"  {'合計':<16} {total_n:>8} {_fmt_size(total_sz):>12}")


def _guard_cache_dir(cache_dir, force, op):
    """cache --clear/--gc の削除対象が __cache__ らしいことを検証する。

    `--dir` に任意パスを渡すと再帰削除になるため、パス要素に __cache__ を
    含まないディレクトリは既定で拒否する（--yes / force=True で明示的に許可）。
    """
    abs_dir = os.path.abspath(cache_dir)
    parts = os.path.normpath(abs_dir).replace("\\", "/").split("/")
    if "__cache__" in parts:
        return
    if force:
        warnings.warn(
            f"cache {op}: __cache__ 配下ではないディレクトリを削除対象にしています: "
            f"{abs_dir}", stacklevel=3)
        return
    raise ValueError(
        f"cache {op}: 指定ディレクトリが __cache__ 配下ではありません: {abs_dir}\n"
        "誤指定による大量削除を防ぐため中断しました。"
        "本当に対象にする場合は --yes を付けてください。")


def cache_gc(keep_days, cache_dir=_CACHE_DIR, *, force=False):
    """keep_days 日より古い（mtime基準）キャッシュファイルを削除する"""
    _guard_cache_dir(cache_dir, force, "--gc")
    cutoff = _time.time() - float(keep_days) * 86400.0
    removed_n = 0
    removed_sz = 0
    for path, _category, size, mtime in list(_iter_cache_files(cache_dir)):
        if mtime < cutoff:
            try:
                os.remove(path)
                removed_n += 1
                removed_sz += size
            except OSError:
                pass
    # 空ディレクトリを掃除
    _prune_empty_dirs(cache_dir)
    print(f"GC完了: {keep_days}日より古い {removed_n}件 "
          f"({_fmt_size(removed_sz)}) を削除しました")
    return removed_n


def _prune_empty_dirs(root):
    """空ディレクトリを再帰的に削除する（bottom-up）"""
    if not os.path.isdir(root):
        return
    for dirpath, dirs, files in os.walk(root, topdown=False):
        if dirpath == root:
            continue
        try:
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
        except OSError:
            pass


def cache_clear(cache_dir=_CACHE_DIR, *, force=False):
    """__cache__ を丸ごと削除する（__cache__ 配下以外は force 必須）"""
    _guard_cache_dir(cache_dir, force, "--clear")
    if os.path.isdir(cache_dir):
        # ignore_errors=False: ロック中ファイル等の削除失敗を黙殺しない
        _shutil.rmtree(cache_dir)
        print(f"キャッシュ全削除: {os.path.abspath(cache_dir)}")
    else:
        print(f"キャッシュディレクトリはありません: {cache_dir}")


# watch が監視する拡張子（レイヤー.py + 画像/音声/フォント/字幕/HTML等の素材）


# --- 循環 import の回避（同一 SCC のモジュールのみ末尾で束縛。scripts/check_import_cycles.py で計測）---
from scriptvedit.ffmpeg import _decoder_input_args
from scriptvedit.plugins import _EFFECT_PLUGINS
