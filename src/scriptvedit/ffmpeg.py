# -*- coding: utf-8 -*-

import subprocess
import os
import re
import uuid as _uuid


_FILTER_SCRIPT_THRESHOLD = 4000


def _unique_tmp_path(final_path):
    """final_path と同ディレクトリ・同拡張子のユニークな一時パスを返す。

    固定名（base.tmp.ext）だと同じキャッシュパスへ複数プロセス/ワーカが
    同時到達したとき一時ファイルを上書きし合って壊れるため、
    pid + 乱数で衝突しない名前にする（os.replace は同一ボリューム内で原子的）。
    """
    base, ext = os.path.splitext(final_path)
    return f"{base}.tmp{os.getpid()}_{_uuid.uuid4().hex[:8]}{ext}"


def _externalize_long_filters(cmd):
    """フィルタ文字列が閾値を超える場合、一時ファイル + FFmpeg 8 の `-/オプション` 構文に差し替える

    例: `-filter_complex <長大な文字列>` → `-/filter_complex <一時ファイルパス>`
    Returns: (実行用cmd, 一時ファイルパスのリスト)
    """
    import tempfile
    new_cmd = list(cmd)
    tmp_files = []
    for opt in ("-filter_complex", "-vf", "-af"):
        for i in range(len(new_cmd) - 1):
            if new_cmd[i] == opt and len(new_cmd[i + 1]) >= _FILTER_SCRIPT_THRESHOLD:
                fd, path = tempfile.mkstemp(suffix=".txt", prefix="svfilter_")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(new_cmd[i + 1])
                new_cmd[i] = f"-/{opt.lstrip('-')}"
                new_cmd[i + 1] = path
                tmp_files.append(path)
                break
    return new_cmd, tmp_files


# FFmpeg メジャーバージョン検証のプロセス内フラグ（1回だけ実行）
_FFMPEG_VERSION_CHECKED = [False]


def _check_ffmpeg_version():
    """FFmpeg 8 以上であることを最初の実行前に1回だけ検証する。

    長大フィルタの外部化(`-/filter_complex`)は FFmpeg 8 の構文で、旧版では
    短い動画だけ動き複雑な動画で突然失敗する（監査 issue #17）。
    バージョン文字列が数値で始まらない開発ビルド（master 等）は検証を通す。
    """
    if _FFMPEG_VERSION_CHECKED[0]:
        return
    _FFMPEG_VERSION_CHECKED[0] = True
    try:
        out = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True,
            timeout=10).stdout
        m = re.match(r"ffmpeg version n?(\d+)\.", out or "")
    except Exception:
        return  # ffmpeg 不在等は後続の実行エラーに任せる
    if m and int(m.group(1)) < 8:
        raise RuntimeError(
            f"FFmpeg 8 以上が必要です（検出: {out.splitlines()[0] if out else '不明'}）。\n"
            f"scriptvedit は FFmpeg 8 の構文（-/filter_complex 等）を使用します。"
            f"https://ffmpeg.org/ から 8 系を導入してください。")


def _run_ffmpeg(cmd, timeout=600):
    """ffmpegコマンドを実行（長大フィルタは一時ファイル経由で渡し、実行後に削除）"""
    _check_ffmpeg_version()
    run_cmd, tmp_files = _externalize_long_filters(cmd)
    try:
        subprocess.run(run_cmd, check=True, timeout=timeout)
    finally:
        for path in tmp_files:
            try:
                os.remove(path)
            except OSError:
                pass


def _run_ffmpeg_to_cache(cmd, cache_path, timeout=600):
    """ffmpegを一時パスへ出力し、成功時のみ os.replace でキャッシュパスに確定する

    タイムアウトやCtrl-Cで壊れた部分ファイルがキャッシュとして残り、
    以後 os.path.exists() 判定で恒久的に使われ続けるのを防ぐ。
    cmd 内の cache_path と一致する引数を一時パス（拡張子は維持）に差し替えて実行する。
    一時パスは pid + 乱数でユニーク化し、並列生成での衝突を防ぐ。
    """
    tmp_path = _unique_tmp_path(cache_path)
    replaced = sum(1 for arg in cmd if arg == cache_path)
    if replaced == 0:
        # 置換0件のまま実行すると非アトミック書き込み後にos.replaceが
        # FileNotFoundErrorになるため、ここで即座に検出する
        raise ValueError(
            f"_run_ffmpeg_to_cache: cmd内に出力先cache_pathが見つかりません: {cache_path}\n"
            f"コマンド構築時と実行時で出力パスが食い違っています。")
    run_cmd = [tmp_path if arg == cache_path else arg for arg in cmd]

    def _dest_stat():
        try:
            st = os.stat(cache_path)
            return (st.st_size, st.st_mtime_ns)
        except OSError:
            return None

    stat_before = _dest_stat()
    try:
        _run_ffmpeg(run_cmd, timeout=timeout)
        try:
            os.replace(tmp_path, cache_path)
        except OSError:
            # Windows では同じ cache_path へ複数ワーカが同時に replace すると
            # 一時的な共有違反(PermissionError)になり得る。ただし「宛先が
            # 存在する」だけで成功扱いにすると、実行前から置いてある古い
            # キャッシュ + 権限エラー等でも黙って旧内容を使い続けてしまう
            # （監査 issue #16）。実行中に宛先が**変化した**（=他ワーカが
            # 同一鍵を確定させた）場合のみ譲歩し、それ以外は失敗にする。
            stat_after = _dest_stat()
            if stat_after is None or stat_after == stat_before:
                raise RuntimeError(
                    f"キャッシュの確定(os.replace)に失敗しました: {cache_path}\n"
                    f"別プロセスの使用・ウイルス対策ソフトのロック・権限を"
                    f"確認してください。古いキャッシュ内容は使いません。")
            # 他ワーカ勝ち: 自分の生成分は破棄（統計にも数えない）
        else:
            with _GEN_COUNTER_LOCK:  # 並列レイヤー生成からの同時更新をアトミック化
                _GEN_COUNTER[0] += 1  # render統計用: 実際に確定できた中間ファイル数
    finally:
        # 失敗時に残った一時ファイルを削除（成功時はos.replace済みで存在しない）
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _ffmpeg_available_encoders():
    """ffmpeg -encoders を1回だけ実行し、利用可能なエンコーダ名の集合を返す"""
    if _AVAILABLE_ENCODERS[0] is not None:
        return _AVAILABLE_ENCODERS[0]
    names = set()
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=30)
        for line in out.stdout.splitlines():
            # 例: " V..... libx264   ..." 先頭にフラグ列、続いてエンコーダ名
            parts = line.split()
            if len(parts) >= 2 and parts[0] and parts[0][0] in "VAS":
                names.add(parts[1])
    except Exception:
        names = set()
    _AVAILABLE_ENCODERS[0] = names
    return names


# --- フィルタ生成ヘルパー ---

# 外部WebMのコーデック判定のプロセス内メモ（(path, size, mtime_ns) → codec_name）
_WEBM_CODEC_MEMO = {}


def _probe_video_codec(source):
    """ffprobeで先頭映像ストリームのcodec_nameを返す（取得不能ならNone）"""
    try:
        st = os.stat(source)
        key = (source, st.st_size, st.st_mtime_ns)
    except OSError:
        return None
    if key in _WEBM_CODEC_MEMO:
        return _WEBM_CODEC_MEMO[key]
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", source],
            capture_output=True, text=True, timeout=10)
        codec = result.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        codec = None
    _WEBM_CODEC_MEMO[key] = codec
    return codec


def _decoder_input_args(source, media_type, fps):
    """メディア種別に応じたffmpeg入力デコーダ引数を構築（全経路共通）

    本レンダ/レイヤーキャッシュ/チェックポイント/computeで共通利用し、
    webmデコーダ判定等の重複と乖離を防ぐ。
    """
    if media_type == "image":
        return ["-loop", "1", "-r", str(fps), "-i", source]
    if media_type != "audio" and source.lower().endswith((".webm", ".mkv")):
        # scriptvedit 自身の生成物（__cache__配下）は拡張子でコーデックが確定する
        # （_LAYER_CACHE_QUALITY 参照）ため、probe せず拡張子で分岐する。
        #   .webm … VP9+alpha 固定 → libvpx-vp9 を強制（ネイティブVP9デコーダは
        #           alpha非対応で、透過が黒背景化して下層レイヤーを覆う）
        #   .mkv  … FFV1(bgra) 等。ネイティブデコーダがalphaを正しく復号する
        #           ので強制は不要（むしろ libvpx-vp9 を指定すると復号できない）
        from scriptvedit.cache import _is_cache_artifact_path
        if _is_cache_artifact_path(source):
            if source.lower().endswith(".webm"):
                return ["-c:v", "libvpx-vp9", "-i", source]
            return ["-i", source]
        if source.lower().endswith(".mkv"):
            return ["-i", source]  # 外部mkvは従来どおりネイティブデコーダ任せ
        # 外部の WebM は VP8/AV1 もあり得るため、拡張子だけで強制すると
        # "Bitstream not supported" で落ちる（監査 issue #15）。
        # codec_name を probe して libvpx 系が必要な場合のみ指定する
        codec = _probe_video_codec(source)
        if codec == "vp9":
            return ["-c:v", "libvpx-vp9", "-i", source]  # alpha保持のため
        if codec == "vp8":
            return ["-c:v", "libvpx", "-i", source]      # 同上（VP8のalphaもlibvpx）
        return ["-i", source]  # AV1等はネイティブデコーダに任せる
    return ["-i", source]


# --- 遅延解決の相互参照（関数本体からのみ使用: 循環importを避けるため末尾で束縛）---
from scriptvedit.state import _AVAILABLE_ENCODERS, _GEN_COUNTER, _GEN_COUNTER_LOCK
