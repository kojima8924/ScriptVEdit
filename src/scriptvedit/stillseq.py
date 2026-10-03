# -*- coding: utf-8 -*-
"""時刻表つきの静止画列 stills() と、コマを描く関数から動画を作る frames()。

どちらも「絵の列を1本の動画（入力1本）にして Object を1個返す」ファクトリ。

なぜ要るか（実測）: 全面 PNG 100枚を1枚ずつ Object にすると、どの入力も 0 秒から
流れて出番まで捨てられるので、手間が「枚数 × 尺」に比例し ffmpeg のメモリが 31GB に
達した。入力が 300〜600 本になるとコマンド長が Windows の上限（32,767字）を超えて
WinError 206 になる。同じ絵の列を先に1本の動画へまとめると 100 ページ・600 秒で
63 秒 / 1.3GB で済む。

符号化は QuickTime Animation（qtrle・argb・.mov）に固定する。実測
（1920x1080・30fps・20枚×6秒＝3600コマ）:

    | 中身                     | 符号化              | 大きさ   | 書き出し | 読み出し |
    |--------------------------|---------------------|----------|----------|----------|
    | 透過 PNG の文字ページ    | qtrle argb          | 1.0MB    | 5.0秒    | 0.1秒    |
    |                          | FFV1 bgra（mkv）    | 353MB    | —        | —        |
    |                          | PNG（mov）          | 461MB    | —        | —        |
    |                          | VP9 yuva420p（webm）| 4.4MB    | 192.6秒  | 1.0秒    |
    | 不透明のノイズ画像（最悪）| qtrle argb          | 163MB    | 3.9秒    | 0.1秒    |
    |                          | qtrle rgb24         | 123MB    | 1.8秒    | 0.1秒    |
    |                          | x264 yuv444p crf12  | 19.6MB   | 11.8秒   | 2.9秒    |
    |                          | x264 yuv420p -g 30  | 117MB    | 12.6秒   | 9.3秒    |

- qtrle は可逆（RGBA をそのまま持つ。色変換が無い）で、前のコマと同じ画素を
  「飛ばす」と書けるので、同じ絵が続く区間はほぼ 0 バイトになる。FFV1 / PNG は
  コマごとに全画素を書くので尺に比例して膨らむ（中間ベイクの FFV1 をここで
  使わない理由）。VP9 は小さいが非可逆で、書き出しが 40 倍遅い。
- alpha が要らないときも qtrle argb のまま（rgb24 にしても 25% しか減らない）。
  写真のように圧縮の効かない絵は1枚あたり非圧縮に近い大きさ（1080p で約 6〜8MB）に
  なるが、枚数に比例するだけで尺には比例しない。x264 は小さいが非可逆で読み出しが
  30 倍遅く、alpha の有無で経路が2本になるので採らなかった。
- キーフレームは先頭の1枚だけ（-g を大きく取る）。-g 300 だと同じ絵の続く区間にも
  10 秒ごとに全画素が入り、5 倍に膨らむ（実測 4.9MB）。scriptvedit は入力を
  先頭から流して trim するので、途中へのシークは起きない。
- .mov のタイムベースは 1/fps で割り切れる（Matroska の 1/1000 と違い、tpad の
  クローンが丸まらない。CLAUDE.md §4.7）。

生成物は __cache__/artifacts/stills/ と __cache__/artifacts/frames/ に置く。
この2つのディレクトリの動画は「表示が素材より長いとき最後のコマを保持する」
（cache.py の _is_hold_artifact_path と filters/video.py の _video_tail_hold）。
"""

import os
import json
import math as _math
import struct as _struct
import subprocess
import threading as _threading
from collections import deque as _deque
from fractions import Fraction

# context は scriptvedit 内 import を持たない葉なので先頭で import できる。
from scriptvedit.context import current_project

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.cache import _HOLD_ARTIFACT_KINDS, _file_fingerprint, _sig_key
from scriptvedit.ffmpeg import (
    FFmpegError, _atomic_write_text, _check_ffmpeg_version, _format_ffmpeg_failure,
    _normalize_ffmpeg_cmd, _run_ffmpeg, _signed_exit_code, _tee_stderr,
    _unique_tmp_path)
from scriptvedit.filters.video import _fps_fraction, _get_media_dimensions
from scriptvedit.objects import Object
from scriptvedit.state import _ARTIFACT_DIR, _ENGINE_VER, _GEN_COUNTER, _GEN_COUNTER_LOCK, _detect_media_type
from scriptvedit.validate import _require_number

# 生成物のディレクトリ名（最後のコマを保持する対象。cache.py と同じ集合を見る）
_STILLS_KIND, _FRAMES_KIND = _HOLD_ARTIFACT_KINDS

# 生成コマンドの世代。符号化やフィルタの組み方を変えたら上げる（鍵に入る）。
# 2: stills に -reinit_filter 0（画素形式の混在で絵が消える不具合の修正）。
#    1 の鍵で作った動画は壊れている可能性があるので命中させない。
_STILLSEQ_VER = "2"

# qtrle のキーフレーム間隔（コマ数）。先頭の1枚だけをキーフレームにする
# （理由はモジュール docstring）。
_QTRLE_GOP = "1000000"

# 符号化の引数（stills / frames 共通）。-an は「中間生成物は映像専用」の方針どおり。
_ENCODE_ARGS = ["-c:v", "qtrle", "-g", _QTRLE_GOP, "-an"]


def _fps_text(fps_frac):
    """fps の正規化した表記（30 / 30.0 → "30"、29.97 → "2997/100"）。

    鍵にも生成コマンド（-framerate・fps フィルタ・ffconcat の option framerate）にも
    この表記を使う。書き方（int か float か）で鍵が分かれない（同一出力なら同一鍵）し、
    同じ鍵のコマンドが書き方で変わることもない。ffmpeg はどこでも有理数表記を読む。
    """
    if fps_frac.denominator == 1:
        return str(fps_frac.numerator)
    return f"{fps_frac.numerator}/{fps_frac.denominator}"


def _sec_fraction(sec):
    """秒を有理数にする。float は書いたとおりの10進数として読む（6.4 → 32/5）。

    float のまま足し上げると 0.1×3 が 0.30000000000000004 になり、半フレームちょうどの
    境目で丸めが食い違う。10進で正確に足してからフレームへ丸める。
    """
    if isinstance(sec, int):
        return Fraction(sec)
    return Fraction(repr(float(sec)))


def _round_frame(sec_frac, fps_frac):
    """時刻 → フレーム番号（最も近いフレーム。半分ちょうどは切り上げ）。"""
    return _math.floor(sec_frac * fps_frac + Fraction(1, 2))


def _resolve_fps(func_name, fps):
    if fps is None:
        proj = current_project()
        return proj.fps if proj else 30
    _require_number(func_name, "fps", fps, 1, 1000)
    return fps


def _resolve_size(func_name, size):
    """size=(w, h) を検証して (w, h) の int を返す。"""
    if (not isinstance(size, (tuple, list)) or len(size) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) for v in size)
            or any(v <= 0 for v in size)):
        raise ValueError(
            f"{func_name}: size は (幅, 高さ) の正の整数で指定してください: {size!r}")
    return int(size[0]), int(size[1])


def _image_dimensions(path):
    """画像の (幅, 高さ)。PNG はヘッダだけ読む（数百枚でも ffprobe を起動しない）。

    PNG 以外は ffprobe（プロセス内メモつき）。取得できなければ (None, None)。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None, None
    if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        w, h = _struct.unpack(">II", head[16:24])
        return w, h
    return _get_media_dimensions(path)


def _grid_seconds(frame, fps_frac):
    """フレーム番号 → 秒（float）。"""
    return float(Fraction(frame) / fps_frac)


def _stills_schedule(items, total, fps_frac):
    """items を検証し、(パスのリスト, 各絵の開始フレーム, 総フレーム数) を返す。

    丸めの規則: **境目の時刻（累積秒）を最も近いフレームへ丸める**（半分ちょうどは
    切り上げ）。各絵の枚数は「次の境目 − 自分の境目」。尺を1枚ずつ丸めて足すのと
    違い、丸め誤差が後ろへ積もらない（k 枚目の開始は常に、指定した累積秒から
    半フレーム以内）。
    """
    if not isinstance(items, (list, tuple)) or len(items) == 0:
        raise ValueError(
            "stills: items には (画像パス, 秒) の組を1つ以上入れたリストを指定してください")
    paths, secs = [], []
    for i, it in enumerate(items):
        if not isinstance(it, (list, tuple)) or len(it) != 2:
            raise ValueError(
                f"stills: items[{i}] は (画像パス, 秒) の組で指定してください: {it!r}")
        path, sec = it
        if not isinstance(path, (str, os.PathLike)):
            raise ValueError(
                f"stills: items[{i}] の1つ目は画像パス（文字列）で指定してください: {path!r}")
        path = os.fspath(path)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"stills: 画像が見つかりません（items[{i}]）: {path}")
        if _detect_media_type(path) != "image":
            raise ValueError(f"stills: 画像のみ指定できます（items[{i}]）: {path}")
        paths.append(path)
        secs.append(sec)

    if total is None:
        # 形1: (画像パス, 表示秒)
        acc = Fraction(0)
        bounds = [0]
        for i, sec in enumerate(secs):
            _require_number("stills", f"items[{i}] の表示秒", sec, 0, None)
            if sec <= 0:
                raise ValueError(
                    f"stills: items[{i}] の表示秒は 0 より大きくしてください: {sec!r}")
            acc += _sec_fraction(sec)
            bounds.append(_round_frame(acc, fps_frac))
    else:
        # 形2: (画像パス, 開始秒) + total（総尺）
        _require_number("stills", "total", total, 0, None)
        for i, sec in enumerate(secs):
            _require_number("stills", f"items[{i}] の開始秒", sec, 0, None)
        if secs[0] != 0:
            raise ValueError(
                f"stills: total を指定したとき、最初の絵の開始秒は 0 にしてください: "
                f"{secs[0]!r}（列全体を後ろへ置くには stills(...) @ 秒 か pause.time() を使う）")
        for i in range(1, len(secs)):
            if secs[i] <= secs[i - 1]:
                raise ValueError(
                    f"stills: 開始秒は昇順で指定してください: items[{i - 1}]={secs[i - 1]!r}, "
                    f"items[{i}]={secs[i]!r}")
        if total <= secs[-1]:
            raise ValueError(
                f"stills: total ({total!r}) は最後の絵の開始秒 ({secs[-1]!r}) より"
                f"大きくしてください")
        bounds = [_round_frame(_sec_fraction(s), fps_frac) for s in secs]
        bounds.append(_round_frame(_sec_fraction(total), fps_frac))

    for i in range(len(paths)):
        if bounds[i + 1] - bounds[i] < 1:
            raise ValueError(
                f"stills: items[{i}]（{paths[i]}）の表示が1フレームに満ちません"
                f"（フレームへ丸めると 0 枚）。尺を 1/fps 秒以上にしてください")
    return paths, bounds[:-1], bounds[-1]


def _image_codec_tag(path):
    """画像の符号化の種類（先頭バイトで見分ける。分からなければ拡張子）。"""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        head = b""
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    if head[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if head[:2] == b"BM":
        return "BMP"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WebP"
    return os.path.splitext(path)[1].lower() or "(拡張子なし)"


def _check_same_codec(paths):
    """全画像の符号化の種類が同じことを確かめる。

    concat demuxer は列全体を1本のストリームとして扱い、デコーダは最初の画像の
    もので固定される。PNG の後ろに JPEG を置くと 'Invalid data' でその絵が落ち、
    ffmpeg は exit 0 のまま1コマだけの動画を書く（FFmpeg 8.0 実測）。
    同じ符号化の中での画素形式の違い（RGB と RGBA の PNG など）は混ぜてよい
    （生成コマンドの -reinit_filter 0 が受ける）。
    """
    first = _image_codec_tag(paths[0])
    for p in paths[1:]:
        tag = _image_codec_tag(p)
        if tag != first:
            raise ValueError(
                f"stills: 画像の形式が揃っていません: {paths[0]} は {first}、{p} は {tag}。\n"
                f"全部を同じ形式（PNG なら全部 PNG）にしてください"
                f"（RGB と RGBA の PNG のように、同じ形式の中での違いは混ぜてよい）")


def _count_video_frames(path):
    """動画のコマ数（ffprobe。取得できなければ None）。

    .mov はコマ数をヘッダに持つので nb_frames で読める（デコードしない）。
    """
    for extra, field in (([], "nb_frames"), (["-count_packets"], "nb_read_packets")):
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", *extra,
                 "-show_entries", f"stream={field}", "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=120).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        if out.isdigit():
            return int(out)
    return None


def _commit_verified(tmp_path, cache_path, n_frames, what, hint=""):
    """一時ファイルのコマ数を確かめてから cache_path へ確定する（os.replace）。

    ffmpeg は入力の途中で絵が落ちても exit 0 で終わることがある（stills: 途中の画像を
    デコードできない。frames: 標準入力が途中で閉じた）。枚数の足りない動画を
    キャッシュへ確定すると、以後ずっと命中して壊れたまま使われるので、ここで止める。
    """
    got = _count_video_frames(tmp_path)
    if got != n_frames:
        raise RuntimeError(
            f"{what}: 生成した動画のコマ数が合いません（{n_frames} コマのはずが "
            f"{'不明' if got is None else got} コマ）。キャッシュには書きませんでした。{hint}")
    os.replace(tmp_path, cache_path)
    with _GEN_COUNTER_LOCK:
        _GEN_COUNTER[0] += 1


def _check_same_size(paths):
    """全画像の寸法が同じことを確かめ、その寸法を返す（取得できなければ None）。

    concat demuxer は途中で寸法を変えられない。
    """
    first = None
    for p in paths:
        dims = _image_dimensions(p)
        if dims[0] is None:
            continue  # 取得できない画像は検査しない（ffmpeg 側のエラーに任せる）
        if first is None:
            first = (dims, p)
        elif dims != first[0]:
            raise ValueError(
                f"stills: 画像の寸法が揃っていません: {first[1]} は "
                f"{first[0][0]}x{first[0][1]}、{p} は {dims[0]}x{dims[1]}。\n"
                f"全部を同じ寸法で作ってください（size= は列全体の出力寸法で、"
                f"寸法の違う画像を混ぜることはできません）")
    return tuple(first[0]) if first else None


def _ffconcat_text(paths, starts, n_total, fps_frac):
    """concat demuxer のリスト（ffconcat）の本文。

    - 各 file に **option framerate** を書く。無いと画像のタイムベースが 1/25 になり、
      切り替わりが 40ms 格子へ丸まって 30fps では1コマずれる絵が出る
      （実測: 99 か所中 16 か所。書けば 0）。
    - duration は「次の開始 − 自分の開始」を µs へ丸めた開始時刻の差で書く。
      1件ずつ丸めた尺を並べると、demuxer が足し上げるときに µs 未満の誤差が積もる。
    - 最後の duration は demuxer に無視されるので、最後の絵をもう一度並べて
      終端（総尺）のコマを作る。生成コマンドの -frames:v が総フレーム数で切る。
    """
    def esc(p):
        return os.path.abspath(p).replace("\\", "/").replace("'", "'\\''")

    def us(frame):
        return round(Fraction(frame) / fps_frac * 1000000)

    fps = _fps_text(fps_frac)
    bounds = list(starts) + [n_total]
    lines = ["ffconcat version 1.0"]
    for i, p in enumerate(paths):
        d = (us(bounds[i + 1]) - us(bounds[i])) / 1000000.0
        lines.append(f"file '{esc(p)}'")
        lines.append(f"option framerate {fps}")
        lines.append(f"duration {d:.6f}")
    lines.append(f"file '{esc(paths[-1])}'")
    lines.append(f"option framerate {fps}")
    return "\n".join(lines) + "\n"


def _finalize_hold_object(cache_path, cmd, origin_sources, n_frames, fps_frac, generate):
    """生成物の Object 化（plan / dry_run / 実生成の分岐）。

    media.py の _finalize_generated_object と同じ分岐。dry_run は存在チェックより
    **先**に置く（「キャッシュが空の状態で何を実行するか」を返す契約。CLAUDE.md §3）。
    generate は実際に生成する関数（引数なし）で、cache_path へ原子的に書く。
    """
    proj = current_project()
    if proj is not None and proj._current_layer_file and origin_sources:
        # レイヤー依存として元素材を記録（キャッシュ鮮度検証から漏れるのを防ぐ）
        proj._extra_layer_deps.setdefault(
            proj._current_layer_file, []).extend(origin_sources)
    if proj is not None and getattr(proj, "_mode", None) == "plan":
        pass  # plan pass: 生成しない
    elif proj is not None and getattr(proj, "_dry_run", False):
        proj._pending_compute_cmds[cache_path] = cmd
    elif os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        # 命中。再生成には全コマの描画が要る（タダではない）ので命中ガードを置く。
        # 書き込みは原子的で、0 バイトの残骸は命中扱いにしない。
        pass
    else:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        generate()
    total = _grid_seconds(n_frames, fps_frac)
    obj = Object(cache_path)
    if origin_sources:
        # 生成物自身のパスも残す。鍵（＝パス）は元素材の外から来る値（時刻表・params。
        # 環境変数や import したデータでも決まる）も含むので、元素材だけを依存にすると
        # その値が変わっても cache='auto' のレイヤーキャッシュが古い絵を再生し続ける。
        # パスが変われば未生成の .mov の指紋は取れず、鮮度の検証は安全側（作り直し）に倒れる
        obj._origin_sources = [cache_path] + [p for p in origin_sources if p != cache_path]
    obj._resolved_length = total
    # 素材の尺。length() と「最後のコマの保持」（_video_tail_hold）が、生成物を
    # probe せずにこの値を使う（未生成の dry_run でも同じ値になる）。
    obj._generated_length = total
    obj._has_audio = False
    return obj


def stills(items, *, total=None, size=None, fps=None):
    """時刻表つきの静止画列を1本の動画（入力1本）にした Object を返す。

    たくさんの全面 PNG（字幕ページ・図・表）を尺ばらばらで順に出すときに使う。
    1枚ずつ Object にするとレンダの手間が「枚数 × 尺」に比例し、数百枚では
    コマンド長の上限も超える。stills は何枚あっても ffmpeg への入力が1本で済む。

    items の形は2つ（total の有無で決まる）:
      形1  stills([(画像パス, 表示秒), …])
           各絵を表示秒だけ順に出す。総尺は表示秒の合計。
      形2  stills([(画像パス, 開始秒), …], total=総尺)
           各絵を開始秒（列の先頭を 0 とした秒。昇順・最初は 0）から次の開始まで出す。
           最後の絵は total まで。音声の時刻表をそのまま渡すときはこちら。

    切り替わりの丸め: 境目の時刻（形1 は表示秒の累積、形2 は開始秒そのもの）を
    最も近いフレームへ丸める（半分ちょうどは切り上げ）。1枚ずつ丸めて足すわけでは
    ないので誤差は積もらない。丸めた結果は戻り値の Object から読める:
      obj.starts        各絵の開始秒（列の先頭を 0 とした秒。フレーム格子上）
      obj.frame_counts  各絵のフレーム数
      obj.length()      総尺（フレーム格子上）
    **音声は obj.starts[i] に合わせて置く**（自分で秒を足し上げると少しずつずれる）。

    size: (幅, 高さ)。省略時は画像の寸法そのまま。指定すると縦横比を保って収め、
      余白は透明にする。**画像は全部同じ寸法であること**（違えば ValueError）。
    **形式も全部同じにする**（PNG と JPEG を混ぜると ValueError）。同じ形式の中での
    違い（RGB の PNG と RGBA の PNG、パレット・グレースケール）は混ぜてよい。
    fps: 省略時は Project の fps。
    alpha は保たれる（透過 PNG はそのまま下のレイヤーが透ける）。音声は無い。

    表示時間は普通の動画と同じく time() で決める（引数なしの time() は総尺ぶん）。
    time(秒) で総尺より長く表示すると、**最後の絵が残る**（普通の動画は背景が見える）。

    生成物は __cache__/artifacts/stills/<鍵>.mov（可逆の qtrle・alpha つき。同じ絵が
    続く区間はほぼ 0 バイト）。鍵は各画像の内容指紋・各絵のフレーム数・fps・size
    （パスは入らない。同じ絵・同じ時刻表なら置き場所を変えても作り直さない。
    __cache__ の下に自分で書き出した画像も内容で見るので、同じ名前で書き直せば
    作り直す）。
    """
    fps = _resolve_fps("stills", fps)
    fps_frac = _fps_fraction(fps)
    if size is not None:
        size = _resolve_size("stills", size)
    paths, starts_f, n_total = _stills_schedule(items, total, fps_frac)
    _check_same_codec(paths)
    if _check_same_size(paths) == size:
        size = None     # 画像と同じ寸法の size は何もしない（同一出力なら同一鍵）
    counts = [b - a for a, b in zip(starts_f, starts_f[1:] + [n_total])]
    fps_text = _fps_text(fps_frac)

    # 鍵: 各画像の内容指紋 + 各絵のフレーム数 + fps + size（生パスは混ぜない）。
    # _src_signature ではなく _file_fingerprint を直接使う: _src_signature は
    # __cache__ 配下をパス署名にする（未生成の中間物を dry_run で指紋化できないため）が、
    # stills は検証の段階で全画像の実在を要求しているので、いつでも内容で見られる。
    # パス署名だと、__cache__ の下へ自分で書き出したページ PNG を同じ名前で
    # 書き直しても鍵が変わらず、古い動画が命中し続ける。
    sigs = ["stills"]
    for p, n in zip(paths, counts):
        sigs.append(f"{_file_fingerprint(p)}*{n}")
    sigs.extend([f"fps={fps_text}", f"size={size[0]}x{size[1]}" if size else "size=src",
                 f"sv={_STILLSEQ_VER}", f"ev={_ENGINE_VER}"])
    key = _sig_key(sigs)
    out_dir = os.path.join(_ARTIFACT_DIR, _STILLS_KIND)
    cache_path = os.path.join(out_dir, f"{key}.mov")
    list_path = os.path.join(out_dir, f"{key}.ffconcat")

    # 拡大縮小は fps より前（絵1枚につき1回だけ掛かる。fps の後だと全コマに掛かる）
    vf = []
    if size:
        w, h = size
        vf.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos")
        vf.append(f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x00000000")
        vf.append("setsar=1")
    vf.extend(["format=argb", f"fps={fps_text}"])
    # -reinit_filter 0: 画素形式の違う画像（RGB の PNG と RGBA の PNG、パレット、
    # グレースケール）が混ざると、既定では形式が変わるたびにフィルタグラフが
    # 作り直され、fps フィルタが抱えていた前の絵のコマが捨てられる。-frames:v は
    # 末尾に再掲した最後の絵で埋まるので枚数は合い、exit 0 のまま「1枚目が消えた
    # 動画」がキャッシュに確定した（FFmpeg 8.0 実測: RGB + RGBA の 3 コマずつが
    # 6 コマとも2枚目の絵）。作り直しを止めると、形式の違うコマは自動挿入の
    # 変換（scale）がコマごとに受けて format=argb へ揃える（alpha も保たれる）。
    cmd = ["ffmpeg", "-y", "-reinit_filter", "0",
           "-f", "concat", "-safe", "0", "-i", list_path,
           "-vf", ",".join(vf), "-frames:v", str(n_total)]
    cmd.extend(_ENCODE_ARGS)
    cmd.append(cache_path)

    def generate():
        # リストは生成の直前に毎回書く（存在ガードを置かない）。鍵は内容由来だが
        # リストの中身は絶対パスなので、素材の置き場所が変わると古いリストは使えない。
        _atomic_write_text(list_path, _ffconcat_text(paths, starts_f, n_total, fps_frac))
        # _run_ffmpeg_to_cache と同じ原子性（同じディレクトリ・同じ拡張子の一時パスへ
        # 書いて os.replace）。確定の前にコマ数の検証を挟むので自前で組む。
        tmp_path = _unique_tmp_path(cache_path)
        try:
            _run_ffmpeg([tmp_path if a == cache_path else a for a in cmd], timeout=3600,
                        context=f"stills の生成（{len(paths)} 枚）: {cache_path}")
            _commit_verified(
                tmp_path, cache_path, n_total, f"stills（{len(paths)} 枚）",
                "\n途中の画像をデコードできなかった可能性があります"
                "（壊れた画像・形式の違う画像が混ざっていないか確認してください）。")
        finally:
            try:
                os.remove(tmp_path)  # 失敗時の残骸掃除（成功時は replace 済み）
            except OSError:
                pass

    obj = _finalize_hold_object(cache_path, cmd, paths, n_total, fps_frac, generate)
    obj.starts = [_grid_seconds(f, fps_frac) for f in starts_f]
    obj.frame_counts = counts
    return obj


def _frame_bytes(img, w, h, i):
    """draw() の戻り値を RGBA の生バイト列（w×h×4）にする。

    PIL.Image（どのモードでも可。RGBA へ変換する）か、形が (h, w, 4)・uint8 の
    numpy 配列を受ける。PIL / numpy は import しない（属性で見分ける）。
    """
    shape = getattr(img, "shape", None)
    if shape is not None and hasattr(img, "tobytes"):
        if tuple(shape) != (h, w, 4):
            raise ValueError(
                f"frames: draw({i}) の配列の形が {tuple(shape)} です。"
                f"(高さ, 幅, 4) = ({h}, {w}, 4) の RGBA にしてください")
        if str(getattr(img, "dtype", "")) != "uint8":
            raise ValueError(
                f"frames: draw({i}) の配列の dtype が {getattr(img, 'dtype', None)} です。"
                f"uint8（0〜255）にしてください")
        return img.tobytes()
    if hasattr(img, "convert") and hasattr(img, "size"):
        if tuple(img.size) != (w, h):
            raise ValueError(
                f"frames: draw({i}) の画像の寸法が {img.size[0]}x{img.size[1]} です。"
                f"size（{w}x{h}）と同じ寸法で描いてください")
        return img.convert("RGBA").tobytes()
    raise TypeError(
        f"frames: draw({i}) は PIL.Image か RGBA の numpy 配列を返してください: "
        f"{type(img)}")


def _pipe_frames_to_cache(cmd, cache_path, draw, n_frames, w, h, timeout=3600):
    """draw(i) のコマを ffmpeg の標準入力へ流し、成功時だけ cache_path へ確定する。

    _run_ffmpeg_to_cache と同じ原子性（同じディレクトリ・同じ拡張子の一時パスへ
    書いて os.replace）。標準入力を使うので _spawn_ffmpeg は通せず、ここで起動する。
    draw が例外を出したら（種類を問わず）ffmpeg を止めて一時ファイルを消し、
    例外をそのまま上げる。「ffmpeg が先に落ちた」と見なすのは標準入力への書き込みの
    失敗だけ: draw の中の FileNotFoundError（素材やフォントを開く）も OSError なので、
    draw の呼び出しごと except OSError で包むと握りつぶし、途中までの動画が exit 0 で
    キャッシュに確定してしまう。
    """
    _check_ffmpeg_version()
    tmp_path = _unique_tmp_path(cache_path)
    run_cmd = _normalize_ffmpeg_cmd([tmp_path if a == cache_path else a for a in cmd])
    tail = _deque(maxlen=200)
    context = f"frames の生成（{n_frames} コマ）: {cache_path}"
    proc = subprocess.Popen(run_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    reader = _threading.Thread(
        target=_tee_stderr, args=(proc.stderr, tail, True), daemon=True)
    reader.start()
    try:
        try:
            for i in range(n_frames):
                data = _frame_bytes(draw(i), w, h, i)   # 例外は外の節が受けて上げる
                try:
                    proc.stdin.write(data)
                except OSError:
                    # ffmpeg が先に落ちた（BrokenPipeError も OSError）。原因は stderr に
                    # あるので終了コードの判定へ進む。exit 0 でもコマ数の検証で止まる
                    break
            try:
                proc.stdin.close()
            except OSError:
                pass
            returncode = proc.wait(timeout=timeout)
        except BaseException:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            reader.join(timeout=5)
            raise
        reader.join(timeout=30)
        if returncode != 0:
            raise FFmpegError(
                _format_ffmpeg_failure(returncode, list(tail), context, []),
                cmd=run_cmd, returncode=_signed_exit_code(returncode),
                stderr_tail=list(tail), context=context)
        _commit_verified(tmp_path, cache_path, n_frames, f"frames（{n_frames} コマ）")
    finally:
        try:
            os.remove(tmp_path)  # 失敗時の残骸掃除（成功時は replace 済みで存在しない）
        except OSError:
            pass


def frames(draw, n_frames=None, *, key, duration=None, size=None, fps=None):
    """コマを描く関数 draw(i) から、キャッシュつきの動画 Object を作る。

    PIL / numpy で描いたアニメーション（グラフが伸びる・図が組み上がる等）を、
    連番 PNG を自分で書き出さずに1本の動画（入力1本）として置ける。

    draw: draw(i) → PIL.Image か、形 (高さ, 幅, 4)・uint8 の RGBA numpy 配列。
      i は 0 始まりのコマ番号（時刻は i / fps 秒）。寸法は size と同じにする。
    n_frames / duration: コマ数か秒数のどちらか一方。duration は最も近いコマ数へ
      丸める（半分ちょうどは切り上げ。最低1コマ）。
    key（必須）: キャッシュ鍵。文字列か、JSON にできる値（数値・リスト・dict）。
      **draw のコードは鍵に入らない**（関数は鍵にできるほど安定に指紋化できない:
      内側の lambda / 内包表記はメモリアドレスつきで現れ、draw が読む外の変数や
      呼び出す先の関数は拾えない）。同じ key なら draw を呼ばずに前回の動画を使う。
      描き方や元データを変えたら key を変えること（例: key=["graph", 版番号, データ]）。
    size: (幅, 高さ)。省略時は Project の解像度。
    fps: 省略時は Project の fps。

    alpha は保たれる。音声は無い。表示時間は time() で決める（引数なしなら尺ぶん）。
    time(秒) で尺より長く表示すると最後のコマが残る。
    生成物は __cache__/artifacts/frames/<鍵>.mov（可逆の qtrle・alpha つき。前のコマと
    同じ画素は書かないので、動かない部分の多い絵ほど小さい）。鍵は key・コマ数・
    fps・size。draw は実レンダのときだけ（キャッシュが無ければ）呼ばれ、
    dry_run では呼ばれない。
    """
    if not callable(draw):
        raise TypeError(f"frames: draw は draw(i) の形の関数で指定してください: {draw!r}")
    fps = _resolve_fps("frames", fps)
    fps_frac = _fps_fraction(fps)
    if (n_frames is None) == (duration is None):
        raise ValueError(
            "frames: n_frames（コマ数）か duration（秒）のどちらか一方を指定してください")
    if n_frames is not None:
        if isinstance(n_frames, bool) or not isinstance(n_frames, int) or n_frames < 1:
            raise ValueError(
                f"frames: n_frames は 1 以上の整数で指定してください: {n_frames!r}")
    else:
        _require_number("frames", "duration", duration, 0, None)
        if duration <= 0:
            raise ValueError(f"frames: duration は 0 より大きくしてください: {duration!r}")
        n_frames = max(1, _round_frame(_sec_fraction(duration), fps_frac))
    if key is None or key == "":
        raise ValueError(
            "frames: key（キャッシュ鍵）は必須です。draw のコードは鍵に入らないので、"
            "描く内容を表す値（名前・版番号・元データ）を渡してください")
    try:
        key_text = json.dumps(key, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        raise TypeError(
            f"frames: key は文字列か JSON にできる値（数値・リスト・dict）で"
            f"指定してください: {e}") from e
    if size is None:
        proj = current_project()
        w, h = (proj.width, proj.height) if proj else (1280, 720)
    else:
        w, h = _resolve_size("frames", size)
    return _frames_object(draw, n_frames, key_text, w, h, fps_frac)


def _frames_object(draw, n_frames, key_text, w, h, fps_frac, origin_sources=()):
    """frames() の後半（鍵 → 生成物のパス → 生成コマンド → Object 化）。

    framekit.build（図解アニメの共通部品）もここを通るので、qtrle argb の .mov・
    原子的な書き込み・dry_run の契約・最後のコマの保持（__cache__/artifacts/frames/）を
    そのまま受け継ぐ。引数は検証済みであること（frames() / framekit.build が検証する）。

    key_text: 鍵の本文（JSON の文字列）。draw のコードは鍵に入らない。
    origin_sources: 生成物の元になったファイル（フォント・データ）。生成物の .mov 自身と
      一緒にレイヤー依存に載せる（キャッシュ鮮度の検証用。_finalize_hold_object）。
      鍵には入れない（鍵は key_text が内容指紋で持つ）。
    """
    fps_text = _fps_text(fps_frac)
    sigs = ["frames", f"key={key_text}", f"n={n_frames}", f"fps={fps_text}",
            f"size={w}x{h}", f"sv={_STILLSEQ_VER}", f"ev={_ENGINE_VER}"]
    cache_path = os.path.join(_ARTIFACT_DIR, _FRAMES_KIND, f"{_sig_key(sigs)}.mov")
    # 入力は標準入力の生 RGBA（"-i -"）。dry_run の cache にもこの形で載る
    cmd = ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
           "-s", f"{w}x{h}", "-framerate", fps_text, "-i", "-",
           "-vf", "format=argb", "-frames:v", str(n_frames)]
    cmd.extend(_ENCODE_ARGS)
    cmd.append(cache_path)

    def generate():
        _pipe_frames_to_cache(cmd, cache_path, draw, n_frames, w, h)

    return _finalize_hold_object(
        cache_path, cmd, list(origin_sources), n_frames, fps_frac, generate)
