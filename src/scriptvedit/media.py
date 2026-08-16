# -*- coding: utf-8 -*-

import os


# --- トランジション/スライドショー（xfade） ---

# FFmpeg 8.0 の xfade transition 名（custom は式指定用のため除外）
_XFADE_TRANSITIONS = {
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown",
    "circlecrop", "rectcrop", "distance", "fadeblack", "fadewhite", "radial",
    "smoothleft", "smoothright", "smoothup", "smoothdown",
    "circleopen", "circleclose", "vertopen", "vertclose",
    "horzopen", "horzclose", "dissolve", "pixelize",
    "diagtl", "diagtr", "diagbl", "diagbr",
    "hlslice", "hrslice", "vuslice", "vdslice",
    "hblur", "fadegrays", "wipetl", "wipetr", "wipebl", "wipebr",
    "squeezeh", "squeezev", "zoomin", "fadefast", "fadeslow",
    "hlwind", "hrwind", "vuwind", "vdwind",
    "coverleft", "coverright", "coverup", "coverdown",
    "revealleft", "revealright", "revealup", "revealdown",
}


def _validate_xfade_kind(func_name, kind):
    if kind not in _XFADE_TRANSITIONS:
        hint = _suggest_hint(str(kind), _XFADE_TRANSITIONS)
        raise ValueError(
            f"{func_name}: 未知のtransition '{kind}'。{hint}\n"
            f"有効な名前: {', '.join(sorted(_XFADE_TRANSITIONS))}")


def _xfade_scale_chain(w, h):
    """xfade入力の正規化フィルタ（共通サイズ・SAR・alpha付きフォーマット）

    format が yuva444p でなく _BAKE_PIX_FMT（bgra）なのは、ここを YUV にすると
    xfade の合成自体が YUV 空間で行われ、RGBA→YUV の行列変換で往復が
    ビット完全にならないため（理由と実測値は state.py の _BAKE_PIX_FMT 参照）。
    """
    return (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x00000000,"
            f"setsar=1,format={_BAKE_PIX_FMT}")


def _finalize_generated_object(cache_path, cmd, origin_sources, total_dur):
    """xfade生成物のObject化共通処理（plan/dry_run/実生成の分岐、compute()と同機構）"""
    proj = Project._current
    # レイヤー依存として元素材を記録（キャッシュ鮮度検証から漏れるのを防ぐ）
    if proj is not None and proj._current_layer_file:
        proj._extra_layer_deps.setdefault(
            proj._current_layer_file, []).extend(origin_sources)
    if proj is not None and getattr(proj, '_mode', None) == "plan":
        pass  # plan pass: 生成スキップ
    elif os.path.exists(cache_path):
        pass  # キャッシュ命中
    elif proj is not None and getattr(proj, '_dry_run', False):
        proj._pending_compute_cmds[cache_path] = cmd
    else:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        _run_ffmpeg_to_cache(cmd, cache_path, timeout=600)
    obj = Object(cache_path)
    obj._origin_sources = list(origin_sources)
    obj._resolved_length = total_dur
    return obj


def slideshow(images, each=3.0, transition="fade", t_dur=0.5, size=None):
    """画像列をxfadeで連結した1本の合成Objectを生成（キャッシュ生成物）。

    images: 画像パスのリスト（2枚以上）
    each: 1枚あたりの表示秒数、t_dur: トランジション秒数（each未満）
    transition: xfadeのtransition名、size: (w, h)。省略時はProjectの解像度。
    合成尺は len(images) * each 秒。音声なし。
    """
    if not isinstance(images, (list, tuple)) or len(images) < 2:
        raise ValueError("slideshow: images には2枚以上の画像パスのリストを指定してください")
    for img in images:
        if not isinstance(img, str):
            raise ValueError(f"slideshow: images の要素はパス文字列のみ: {img!r}")
        if not os.path.exists(img):
            raise ValueError(f"slideshow: 画像が見つかりません: {img}")
        if _detect_media_type(img) != "image":
            raise ValueError(f"slideshow: 画像のみ指定できます: {img}")
    _validate_xfade_kind("slideshow", transition)
    _require_number("slideshow", "each", each, 0.1, None)
    _require_number("slideshow", "t_dur", t_dur, 0.01, None)
    if t_dur >= each:
        raise ValueError(
            f"slideshow: t_dur ({t_dur}) は each ({each}) より短くしてください")
    proj = Project._current
    if size is None:
        w = proj.width if proj else 1280
        h = proj.height if proj else 720
    else:
        if not isinstance(size, (tuple, list)) or len(size) != 2:
            raise ValueError(f"slideshow: size は (w, h) タプルで指定してください: {size!r}")
        w, h = int(size[0]), int(size[1])
    fps = proj.fps if proj else 30
    n = len(images)
    total = n * each

    # キャッシュ署名
    sigs = ["slideshow"]
    sigs.extend(_src_signature(img) for img in images)
    sigs.extend([f"each={each}", f"tr={transition}", f"tdur={t_dur}",
                 f"size={w}x{h}", f"fps={fps}", f"ev={_ENGINE_VER}",
                 f"bpf={_BAKE_PIXFMT_VER}"])
    key = _sig_key(sigs)
    cache_path = os.path.join(_ARTIFACT_DIR, "xfade", f"{key}.mkv")

    # コマンド構築: 各画像を each+t_dur 秒（最後は each 秒）でループ入力し xfade 連結
    cmd = ["ffmpeg", "-y"]
    for i, img in enumerate(images):
        d = each if i == n - 1 else each + t_dur
        cmd.extend(["-loop", "1", "-framerate", str(fps), "-t", str(d), "-i", img])
    parts = []
    for i in range(n):
        parts.append(f"[{i}:v]{_xfade_scale_chain(w, h)}[s{i}]")
    cur = "[s0]"
    for i in range(1, n):
        out = f"[x{i}]"
        offset = each * i
        parts.append(
            f"{cur}[s{i}]xfade=transition={transition}:duration={t_dur}:offset={offset}{out}")
        cur = out
    cmd.extend(["-filter_complex", ";".join(parts), "-map", cur,
                "-c:v", "ffv1", "-level", "3", "-pix_fmt", _BAKE_PIX_FMT,
                "-t", str(total), cache_path])
    return _finalize_generated_object(cache_path, cmd, list(images), total)


def transition(obj_a, obj_b, kind="fade", duration=1.0):
    """2つのObjectをxfadeで1本に連結した合成Objectを生成。

    obj_a, obj_b は素のObject（Transform/Effect未適用）のみ。加工済み素材は
    先に compute() で素材化してから渡す。両Objectはこの合成に消費され、
    Projectのタイムラインからは除外される。画像は事前に .time(秒) が必要。
    合成尺は dur_a + dur_b - duration 秒。音声は含まれない。
    """
    for nm, o in (("obj_a", obj_a), ("obj_b", obj_b)):
        if not isinstance(o, Object):
            raise TypeError(f"transition: {nm} は Object のみ: {type(o)}")
        if o.transforms or o.effects or o.audio_effects:
            raise ValueError(
                f"transition: {nm} に Transform/Effect が適用されています。"
                f"先に compute() で素材化してから渡してください。")
        if o.media_type not in ("image", "video"):
            raise ValueError(f"transition: {nm} は画像/動画のみ対応: {o.media_type}")
    _validate_xfade_kind("transition", kind)
    _require_number("transition", "duration", duration, 0.01, None)
    proj = Project._current
    w = proj.width if proj else 1280
    h = proj.height if proj else 720
    fps = proj.fps if proj else 30

    def _clip_dur(o, nm):
        if o.duration is not None:
            return o.duration
        if o.media_type == "image":
            raise ValueError(
                f"transition: 画像 {nm} ('{o.source}') には事前に .time(秒) が必要です")
        rl = getattr(o, '_resolved_length', None)
        if rl:
            return rl
        return o.length()

    dur_a = _clip_dur(obj_a, "obj_a")
    dur_b = _clip_dur(obj_b, "obj_b")
    if duration >= dur_a or duration >= dur_b:
        raise ValueError(
            f"transition: duration ({duration}) は各素材の尺"
            f"（obj_a: {dur_a}, obj_b: {dur_b}）より短くしてください")
    total = dur_a + dur_b - duration

    # 両Objectはこの合成に消費される → Projectから除外
    origin_sources = []
    for o in (obj_a, obj_b):
        if proj is not None and o in proj.objects:
            proj.objects.remove(o)
        origin_sources.extend(getattr(o, '_origin_sources', None) or [o.source])

    # キャッシュ署名
    sigs = ["transition",
            _src_signature(obj_a.source), _src_signature(obj_b.source),
            f"da={dur_a}", f"db={dur_b}", f"kind={kind}", f"dur={duration}",
            f"size={w}x{h}", f"fps={fps}", f"ev={_ENGINE_VER}",
            f"bpf={_BAKE_PIXFMT_VER}"]
    key = _sig_key(sigs)
    cache_path = os.path.join(_ARTIFACT_DIR, "xfade", f"{key}.mkv")

    # コマンド構築: 両素材を共通サイズ/fpsへ正規化し xfade で連結
    cmd = ["ffmpeg", "-y"]
    for o in (obj_a, obj_b):
        if o.media_type == "image":
            cmd.extend(["-loop", "1", "-framerate", str(fps), "-i", o.source])
        else:
            cmd.extend(_decoder_input_args(o.source, o.media_type, fps))
    parts = []
    for i, (o, d) in enumerate(((obj_a, dur_a), (obj_b, dur_b))):
        parts.append(
            f"[{i}:v]trim=duration={d},setpts=PTS-STARTPTS,"
            f"{_xfade_scale_chain(w, h)},fps={fps}[t{i}]")
    offset = dur_a - duration
    parts.append(
        f"[t0][t1]xfade=transition={kind}:duration={duration}:offset={offset}[tout]")
    cmd.extend(["-filter_complex", ";".join(parts), "-map", "[tout]",
                "-c:v", "ffv1", "-level", "3", "-pix_fmt", _BAKE_PIX_FMT,
                "-t", str(total), cache_path])
    return _finalize_generated_object(cache_path, cmd, origin_sources, total)


def video_sequence(*objs, transition="fade", t_dur=0.5):
    """複数の動画クリップを xfade で連結した1本の合成Objectを生成する。

    slideshow の動画版（キャッシュ生成物）。全クリップに音声があれば
    acrossfade で音声も連結する。

    objs: 動画Object または動画パス文字列（2つ以上）。素のObjectのみ
    （Transform/Effect適用済みは先に compute() で素材化する）。
    各クリップの実長は probe で取得し、t_dur が最短クリップ以上ならエラー。
    合成尺は sum(実長) - t_dur*(n-1) 秒。
    """
    if len(objs) < 2:
        raise ValueError("video_sequence: 2つ以上の動画を指定してください")
    _validate_xfade_kind("video_sequence", transition)
    _require_number("video_sequence", "t_dur", t_dur, 0.01, None)
    proj = Project._current
    sources = []
    consumed = []  # 全検証を通過してからまとめて消費する（途中失敗で Project を壊さない）
    for o in objs:
        if isinstance(o, Object):
            if o.media_type != "video":
                raise ValueError(
                    f"video_sequence: 動画Objectのみ連結できます: {o.source}")
            if o.transforms or o.effects or o.audio_effects:
                raise ValueError(
                    f"video_sequence: '{o.source}' に Transform/Effect が適用されています。"
                    f"先に compute() で素材化してから渡してください。")
            sources.append(o.source)
            if proj is not None and o in proj.objects:
                consumed.append(o)
        elif isinstance(o, str):
            if not os.path.exists(o):
                raise FileNotFoundError(f"video_sequence: 動画が見つかりません: {o}")
            if _detect_media_type(o) != "video":
                raise ValueError(f"video_sequence: 動画のみ指定できます: {o}")
            sources.append(o)
        else:
            raise TypeError(
                f"video_sequence: 動画Objectかパス文字列のみ指定できます: {type(o)}")
    n = len(sources)

    # 映像/音声ストリーム個別の尺を取得（コンテナ尺の全用途流用による
    # A/V ドリフトを避ける）。取得不能時はコンテナ尺→5.0 にフォールバック。
    def _stream_lengths(src):
        vd = ad = None
        cont = None
        if proj is not None:
            info = proj._probe_media(src)
            if info is not None:
                cont = info.get("duration")
                vd = info.get("video_duration")
                ad = info.get("audio_duration")
        vd = vd or cont or 5.0
        ad = ad or cont or vd
        return vd, ad
    stream_lengths = [_stream_lengths(s) for s in sources]
    lengths = [v for v, _ in stream_lengths]       # 映像trim/offset/合成尺用
    a_lengths = [a for _, a in stream_lengths]      # 音声atrim用

    # xfade は重なり区間 t_dur を要するため、最短クリップ未満を保証する
    for s, ln in zip(sources, lengths):
        if ln <= t_dur:
            raise ValueError(
                f"video_sequence: クリップ実長({ln:.3f}s)が t_dur({t_dur}s)以下です: {s}\n"
                f"t_dur を短くするか、より長いクリップを指定してください。")
    total = sum(lengths) - t_dur * (n - 1)

    # 音声: 全クリップが音声を持つ場合のみ acrossfade で連結（混在は映像のみ）
    def _has_audio(src):
        if proj is not None:
            info = proj._probe_media(src)
            if info is not None:
                return bool(info.get("has_audio"))
        return False
    all_audio = all(_has_audio(s) for s in sources)
    # acrossfade も各音声が t_dur 超であることを要する
    if all_audio:
        for s, ln in zip(sources, a_lengths):
            if ln <= t_dur:
                raise ValueError(
                    f"video_sequence: 音声実長({ln:.3f}s)が t_dur({t_dur}s)以下です: {s}\n"
                    f"t_dur を短くするか、より長いクリップを指定してください。")

    # 全入力の検証が済んだのでここで初めてタイムラインから除外する（原子的な消費）
    for o in consumed:
        proj.objects.remove(o)

    w = proj.width if proj else 1280
    h = proj.height if proj else 720
    fps = proj.fps if proj else 30

    sigs = ["video_sequence"]
    sigs.extend(_src_signature(s) for s in sources)
    sigs.extend([f"tr={transition}", f"tdur={t_dur}", f"size={w}x{h}",
                 f"fps={fps}", f"audio={all_audio}", f"ev={_ENGINE_VER}",
                 f"bpf={_BAKE_PIXFMT_VER}"])
    key = _sig_key(sigs)
    cache_path = os.path.join(_ARTIFACT_DIR, "xfade", f"{key}.mkv")

    # コマンド構築: 各クリップを共通サイズ/fpsへ正規化し xfade（+acrossfade）連結
    cmd = ["ffmpeg", "-y"]
    for s in sources:
        cmd.extend(_decoder_input_args(s, "video", fps))
    parts = []
    for i, (s, ln) in enumerate(zip(sources, lengths)):
        parts.append(
            f"[{i}:v]trim=duration={ln},setpts=PTS-STARTPTS,"
            f"{_xfade_scale_chain(w, h)},fps={fps}[t{i}]")
    cur = "[t0]"
    acc = lengths[0]
    for i in range(1, n):
        out = f"[x{i}]"
        offset = acc - t_dur
        parts.append(
            f"{cur}[t{i}]xfade=transition={transition}:duration={t_dur}:offset={offset}{out}")
        cur = out
        acc = offset + lengths[i]
    maps = ["-map", cur]
    if all_audio:
        for i, ln in enumerate(a_lengths):
            parts.append(f"[{i}:a]atrim=duration={ln},asetpts=PTS-STARTPTS[at{i}]")
        acur = "[at0]"
        for i in range(1, n):
            aout = f"[ax{i}]"
            parts.append(f"{acur}[at{i}]acrossfade=d={t_dur}{aout}")
            acur = aout
        maps.extend(["-map", acur])
    cmd.extend(["-filter_complex", ";".join(parts)])
    cmd.extend(maps)
    cmd.extend(["-c:v", "ffv1", "-level", "3", "-pix_fmt", _BAKE_PIX_FMT])
    if all_audio:
        cmd.extend(["-c:a", "pcm_s16le"])
    cmd.extend(["-t", str(total), cache_path])
    obj = _finalize_generated_object(cache_path, cmd, list(sources), total)
    # dry_run では未生成キャッシュのprobeができないため音声有無を明示確定する
    obj._has_audio = all_audio
    return obj


# --- 遅延解決の相互参照（関数本体からのみ使用: 循環importを避けるため末尾で束縛）---
from scriptvedit.cache import _sig_key, _src_signature
from scriptvedit.ffmpeg import _decoder_input_args, _run_ffmpeg_to_cache
from scriptvedit.objects import Object
from scriptvedit.project import Project
from scriptvedit.state import _ARTIFACT_DIR, _BAKE_PIX_FMT, _BAKE_PIXFMT_VER, _ENGINE_VER, _detect_media_type, _suggest_hint
from scriptvedit.validate import _require_number
