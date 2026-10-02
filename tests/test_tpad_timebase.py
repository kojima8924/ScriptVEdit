# -*- coding: utf-8 -*-
"""tpad のタイムベース丸めによる時刻ずれ（CLAUDE.md §4.7）の回帰テスト。

開始が 0 より後の映像入力は tpad=start_duration=N:start_mode=clone で
タイムライン時刻へ整列する。tpad はクローン1枚ごとに「1/フレームレート」を
入力のタイムベースへ丸めて積算するため、Matroska/WebM（1/1000）の入力では
1/30 秒が 33ms に丸まり、中身が開始時刻の約1%早く届いて早く消えていた
（FFmpeg 8.0 実測: FFV1 mkv を 600 秒開始 → 実フレームが 594 秒から。GIF の
1/100 では約10%）。checkpoint の FFV1 .mkv・web/compute/from_project/
レイヤーキャッシュの生成物はすべてこの形なので、長尺動画の後半ほど大きくずれる。

回避策は tpad の直前に settb で「よく使うフレームレートの1フレーム長が割り切れる
タイムベース」（1/120000）へ揃えること。1/1000000（AVTB）では 1/30 秒などが
割り切れず、誤差が半フレームに積もった時点で1フレームずれた（実測: 30fps の mkv を
1800 秒開始で1フレーム早く、60fps の mp4（1/15360）を 600 秒開始で1フレーム遅く。
後者は settb 無しなら正確だったので mp4 にとっては退行だった）。
  (a) フィルタ文字列: 映像入力には settb が tpad の直前に入り、
      テキストの lavfi 入力（1/fps で正確）と画像（tpad 自体が無い）には入らない。
      本レンダ・レイヤーキャッシュ・時間分割並列レンダの全経路で効く。
      Project の fps が 1/120000 で割り切れない（90 / 144fps 等）ときは分母を広げる。
  (b) 実レンダ: 素材を遅い開始時刻に置いた動画を実際に書き出し、
      j 枚目が「開始 + j/fps 秒」ちょうどに映ることを画素で確かめる
      （FFV1 mkv 30fps を 60 秒と 1800 秒、h264 mp4 60fps を 600 秒）。
"""
import shutil
import subprocess
from fractions import Fraction

import pytest

import scriptvedit as sv
from scriptvedit.context import _exec_stack, activate, current_project
from scriptvedit.filters import video as video_mod
from scriptvedit.filters.video import (
    _build_video_overlay_parts, _tpad_timebase, _visible_window)

# fps=30 の Project で tpad の直前に入る settb
_SETTB = "settb=1/120000"


@pytest.fixture(autouse=True)
def _restore_project_globals():
    """各テスト後に Project の暗黙登録先と実行スタックを戻す"""
    old_current = current_project()
    old_stack = list(_exec_stack)
    activate(None)
    _exec_stack[:] = []
    try:
        yield
    finally:
        activate(old_current)
        _exec_stack[:] = old_stack


def _write_layer(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return str(path)


def _chain_of(parts):
    """_build_video_overlay_parts の先頭行（オブジェクトのフィルタチェーン）"""
    return parts[0].split("]", 1)[1].rsplit("[", 1)[0].split(",")


# --- (a) タイムベースの選び方 ------------------------------------------------

@pytest.mark.parametrize("fps", [
    24000 / 1001, 24, 25, 30000 / 1001, 29.97, 30, 48, 50, 60000 / 1001, 60, 120,
    10, 12, 15, 90, 144, 72, 100, 240,
])
def test_tpad_timebase_divides_one_frame(fps):
    """Project の fps の1フレーム長が、選んだタイムベースの整数 tick になる"""
    tb = _tpad_timebase(fps)
    num, den = tb.split("/")
    assert num == "1"
    frame = Fraction(fps).limit_denominator(1001)
    ticks = int(den) / frame
    assert ticks.denominator == 1, (fps, tb, ticks)
    assert int(den) <= 2 ** 31 - 1


def test_tpad_timebase_is_120000_for_common_rates():
    """よく使う fps は 1/120000 のまま（素材側の 23.976〜120fps もまとめて割り切れる）"""
    for fps in (24000 / 1001, 24, 25, 30000 / 1001, 30, 48, 50, 60000 / 1001, 60, 120):
        assert _tpad_timebase(fps) == "1/120000", fps
        frame = Fraction(fps).limit_denominator(1001)
        assert (120000 / frame).denominator == 1, fps
    # 割り切れない fps だけ分母を広げる
    assert _tpad_timebase(90) == "1/360000"
    assert _tpad_timebase(144) == "1/360000"


# --- (a) フィルタ文字列 ------------------------------------------------------

@pytest.mark.parametrize("src", ["clip.mkv", "clip.webm", "clip.gif", "clip.mp4",
                                 "__cache__/artifacts/checkpoint/x/y.mkv"])
def test_video_input_gets_settb_right_before_tpad(src, monkeypatch):
    """映像入力（コンテナを問わない）は tpad の直前に settb が入る"""
    monkeypatch.setattr(video_mod, "_get_base_dimensions", lambda obj: (64, 36))
    p = sv.Project()
    p.configure(width=320, height=180, fps=30)
    o = sv.Object(src)
    o.start_time = 600
    o.duration = 2
    parts, _ = _build_video_overlay_parts(
        o, 1, "[0:v]", 2, visible_window=_visible_window(o, 30))
    chain = _chain_of(parts)
    i = chain.index("tpad=start_duration=600:start_mode=clone")
    assert chain[i - 1] == _SETTB
    assert sum(1 for f in chain if f.startswith("settb=")) == 1
    # 可視区間の trim は tpad の後（タイムライン絶対時刻で切る）
    assert chain[i + 1].startswith("trim=start=")


def test_settb_follows_project_fps(monkeypatch):
    """settb は Project の fps から決まる（90fps なら 1/360000）"""
    monkeypatch.setattr(video_mod, "_get_base_dimensions", lambda obj: (64, 36))
    p = sv.Project()
    p.configure(width=320, height=180, fps=90)
    o = sv.Object("clip.mkv")
    o.start_time = 5
    o.duration = 2
    parts, _ = _build_video_overlay_parts(o, 1, "[0:v]", 2)
    assert _chain_of(parts) == [
        "settb=1/360000", "tpad=start_duration=5:start_mode=clone"]


def test_settb_goes_after_trim_setpts_prefilters(monkeypatch):
    """素材側の trim/setpts の後・tpad の直前に入る（trim の尺を変えない）"""
    monkeypatch.setattr(video_mod, "_get_base_dimensions", lambda obj: (64, 36))
    p = sv.Project()
    p.configure(width=320, height=180, fps=30)
    o = sv.Object("clip.mkv")
    o <= sv.trim(start=1, duration=2)
    o.start_time = 5
    o.duration = 2
    parts, _ = _build_video_overlay_parts(o, 1, "[0:v]", 2)
    assert _chain_of(parts) == [
        "trim=start=1:duration=2", "setpts=PTS-STARTPTS",
        _SETTB, "tpad=start_duration=5:start_mode=clone"]


def test_no_settb_without_tpad(monkeypatch):
    """開始 0 の映像・画像（tpad 自体が無い）には settb を足さない"""
    monkeypatch.setattr(video_mod, "_get_base_dimensions", lambda obj: (64, 36))
    p = sv.Project()
    p.configure(width=320, height=180, fps=30)
    v = sv.Object("clip.mkv")
    v.start_time = 0
    v.duration = 2
    img = sv.Object("still.png")
    img.start_time = 60
    img.duration = 2
    for o in (v, img):
        parts, _ = _build_video_overlay_parts(o, 1, "[0:v]", 2)
        joined = ";".join(parts)
        assert "settb" not in joined
        assert "tpad" not in joined


def test_text_lavfi_input_keeps_exact_timebase():
    """テキストの lavfi 入力は 1/fps で丸めが起きないため settb を付けない"""
    p = sv.Project()
    p.configure(width=320, height=180, fps=30)
    t = sv.text("字幕", size=40, border=3)
    t.start_time = 600
    t.duration = 2
    parts, _ = _build_video_overlay_parts(t, 1, "[0:v]", 2)
    chain = _chain_of(parts)
    assert chain[0] == "tpad=start_duration=600:start_mode=clone"
    assert not any(f.startswith("settb=") for f in chain)


_VIDEO_LAYER = (
    "from scriptvedit import *\n"
    "pause.time(3)\n"
    "Object(asset('video/clip_with_audio.mp4'))[0:1] <= adelete()\n")

# fps=10 の Project で入る settb（10fps も 1/120000 で割り切れる）
_SETTB_TPAD_10 = "settb=1/120000,tpad=start_duration=3:start_mode=clone"


def test_settb_in_main_and_layer_cache_commands(tmp_path):
    """本レンダとレイヤーキャッシュ生成の両コマンドに settb が入る"""
    src_layer = _write_layer(tmp_path, "l_video.py", _VIDEO_LAYER)
    p = sv.Project()
    p.configure(width=320, height=180, fps=10)
    p.layer(src_layer, priority=1, cache="make")
    result = p.render(str(tmp_path / "o.mp4"), dry_run=True)
    cache_cmds = [c for path, c in result["cache"].items()
                  if "layer" in path.replace("\\", "/")]
    assert cache_cmds, f"レイヤーキャッシュのコマンドが無い: {list(result['cache'])}"
    fc = cache_cmds[0][cache_cmds[0].index("-filter_complex") + 1]
    assert _SETTB_TPAD_10 in fc

    p2 = sv.Project()
    p2.configure(width=320, height=180, fps=10)
    p2.layer(_write_layer(tmp_path, "l_video2.py", _VIDEO_LAYER), priority=1)
    main = p2.render(str(tmp_path / "o2.mp4"), dry_run=True)["main"]
    assert _SETTB_TPAD_10 in main[main.index("-filter_complex") + 1]


def test_settb_in_parallel_chunk_commands(tmp_path, monkeypatch):
    """時間分割並列レンダ（parallel=N）のチャンクコマンドにも settb が入る"""
    calls = []

    def fake_run(cmd, timeout=None, **kwargs):
        calls.append(list(cmd))
        with open(cmd[-1], "wb"):
            pass

    monkeypatch.setattr("scriptvedit.project._run_ffmpeg", fake_run)
    monkeypatch.setattr("scriptvedit.parallel._run_ffmpeg", fake_run)
    p = sv.Project()
    p.configure(width=320, height=180, fps=10)
    p.layer(_write_layer(tmp_path, "l_par.py", _VIDEO_LAYER), priority=1)
    p.render(str(tmp_path / "par.mp4"), parallel=2)
    chunk_fcs = [c[c.index("-filter_complex") + 1] for c in calls
                 if "-filter_complex" in c and "tpad=start_duration" in
                 c[c.index("-filter_complex") + 1]]
    assert chunk_fcs, "tpad を含むチャンクコマンドが無い"
    for fc in chunk_fcs:
        assert _SETTB_TPAD_10 in fc


# --- (b) 実レンダで中身の時刻を確かめる ---------------------------------------

_W, _H = 32, 16
_STEP = 6         # 素材は 6 枚ごとに輝度が変わる（1 枚ずれると区間の境目で分かる）
# 素材の区間 k（6k 枚目〜）の輝度（5 区間で一巡）。背景は黒（limited range で約16）
_LEVELS = [60, 100, 140, 180, 220]


def _need_ffmpeg():
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg / ffprobe が無い環境")


def _make_source(path, fps, codec, expect_tb):
    """1 秒・fps 枚・6 枚ごとに輝度の変わる素材を作り、タイムベースを確かめる。

    codec="ffv1" は checkpoint と同じ FFV1 .mkv（1/1000）、"h264" は mp4。
    """
    lum = f"{_LEVELS[0]}+40*mod(trunc(N/{_STEP}),{len(_LEVELS)})"
    enc = (["-c:v", "ffv1"] if codec == "ffv1"
           else ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "1"])
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=black:s={_W}x{_H}:r={fps}:d=1",
         "-vf", f"format=yuv444p,geq=lum='{lum}':cb=128:cr=128", *enc, str(path)],
        check=True, capture_output=True, timeout=60)
    tb = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=time_base", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True, timeout=30).stdout.strip()
    assert tb == expect_tb, f"前提: {path.name} のタイムベースは {expect_tb}（実測 {tb}）"


def _center_luma(video, t_from, n_frames):
    """t_from 秒から n_frames 枚を読み、各フレーム中央の輝度を返す"""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-ss", f"{t_from}", "-i", str(video),
         "-frames:v", str(n_frames), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True, timeout=60).stdout
    size = _W * _H
    return [out[i * size + (_H // 2) * _W + _W // 2]
            for i in range(len(out) // size)]


def _assert_content_on_time(tmp_path, src, fps, start):
    """src（1 秒・fps 枚）を start 秒開始に置いて実レンダし、画素で時刻を確かめる。

    「開始前は背景」「開始後の j 枚目が素材の j 枚目」「終了後は背景」を見る。
    1 枚でも早い/遅いと、6 枚ごとの輝度の境目で期待と食い違う。
    """
    layer = _write_layer(
        tmp_path, "l_src.py",
        "from scriptvedit import *\n"
        f"pause.time({start})\n"
        f"Object({str(src)!r}).time(1)\n"
        "pause.time(1)\n")
    p = sv.Project()
    p.configure(width=_W, height=_H, fps=fps, background_color="black")
    p.layer(layer, priority=1)
    out = tmp_path / "out.mp4"
    p.render(str(out), timeout=600)

    # 開始 1 秒前〜終了 1 秒後の 3 秒を読む
    lum = _center_luma(out, start - 1, 3 * fps)
    assert len(lum) == 3 * fps, lum
    before = lum[:fps]
    during = lum[fps:2 * fps]
    # 終了ちょうどの 1 枚は enable の境界なので判定から外す
    after = lum[2 * fps + 1:]
    assert all(v < 40 for v in before), f"開始前に中身が出ている（早着）: {before}"
    assert all(v < 40 for v in after), f"終了後も中身が残っている: {after}"
    for j, got in enumerate(during):
        # 素材は limited range の Y で書いたので、gray へ読み出すと
        # (Y-16)*255/219 へ伸びる。h264 の量子化ぶんの誤差は許す
        # （区間どうしの差は約 47）
        want = (_LEVELS[(j // _STEP) % len(_LEVELS)] - 16) * 255 / 219
        assert abs(got - want) <= 12, (
            f"{start}+{j}/{fps} 秒の中身が素材の {j} 枚目と違う"
            f"（得た輝度 {got} / 期待 {want}）: {during}")


def test_ffv1_mkv_content_starts_on_time_after_60s(tmp_path):
    """FFV1 mkv（1/1000）を 60 秒開始に置くと、j 枚目が 60+j/30 秒ちょうどに映る。

    settb が無いと tpad のクローン 1800 枚が 1 枚 33ms に丸まり、中身が 59.4 秒に
    届いて 60.4 秒に消えていた（enable で 60 秒までは隠れるので、見かけは
    「素材の途中から始まり、最後の 0.6 秒が背景」になる）。
    """
    _need_ffmpeg()
    src = tmp_path / "src.mkv"
    _make_source(src, 30, "ffv1", "1/1000")
    _assert_content_on_time(tmp_path, src, 30, 60)


def test_ffv1_mkv_30fps_on_time_after_1800s(tmp_path):
    """FFV1 mkv 30fps を 1800 秒開始でもずれない。

    settb=AVTB（1/1000000）だと 1/30 秒が 33333µs に丸まり、クローン 54000 枚で
    約 18ms（半フレーム強）早く積もって中身が1フレーム早く出た（0 枚目が飛ぶ）。
    """
    _need_ffmpeg()
    src = tmp_path / "src.mkv"
    _make_source(src, 30, "ffv1", "1/1000")
    _assert_content_on_time(tmp_path, src, 30, 1800)


def test_h264_mp4_60fps_on_time_after_600s(tmp_path):
    """h264 mp4 60fps（1/15360）を 600 秒開始でもずれない。

    1/60 秒は 1/15360 では 256 tick ちょうどで、settb 無しなら正確だった。
    settb=AVTB にすると 16667µs へ丸まって約 12ms（半フレーム強）遅く積もり、
    中身が1フレーム遅れた（0 枚目が2回出る）。
    """
    _need_ffmpeg()
    src = tmp_path / "src.mp4"
    _make_source(src, 60, "h264", "1/15360")
    _assert_content_on_time(tmp_path, src, 60, 600)
