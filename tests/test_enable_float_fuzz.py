# -*- coding: utf-8 -*-
"""overlay の enable 窓と、映像の終わりの保持（つなぎ目の黒フレーム）の回帰テスト。

Object の開始フレームが enable=between(t,開始,終了) の窓から外れる原因は3つある。
  1. time() で順に並べた Object の開始時刻は尺の足し算で決まるので、
     0.1+0.1+0.1 = 0.30000000000000004 のような端数が乗る。
  2. ffmpeg は t を「pts × タイムベースの double 値」で計算するので、端数の無い
     開始時刻でもフレームの t が 1ulp 小さく出る（30fps の 111 枚目は
     111×(1/30)=3.6999999999999997。49fps なら整数秒でも起きる）。
  3. 開始が格子から外れているとき、tpad は中身の1枚目を「最も近いフレーム」に届ける
     （207.339 秒開始 → 207.333 秒）のに、窓は開始時刻から開いていた。
さらに、音声（AAC は 1024 サンプル単位）が映像より長い動画は Object の尺が音声で決まり、
time() で並べると次の Object が映像の終わりより後ろから始まる。
どれでも境目の1枚がどちらの映像も無い（前は EOF・eof_action=pass）黒いフレームになる
（実測: 章ごとの mp4 を time() で5本つないだ完成版）。

  (a) _t_floor / _t_ceil: 1µs 格子で x 以下の最大値 / x 以上の最小値。
      _t_enable_from: 窓を「開始に最も近いフレーム」の 1µs 手前から開ける。
  (b) フィルタ文字列: enable と tpad に端数の無い時刻が入る。映像が先に終わる素材だけ
      tpad=stop_mode=clone で最後のフレームを保持する。
  (c) 実レンダ: 尺ちょうどで終わる素材・音声の方が長い素材を time() で並べても、
      境目が黒くならない。
"""
import shutil
import subprocess
from fractions import Fraction

import pytest

import scriptvedit as sv
from scriptvedit.context import _exec_stack, activate, current_project
from scriptvedit.filters import video as video_mod
from scriptvedit.filters.video import (
    _build_video_overlay_parts, _t_ceil, _t_enable_from, _t_floor, _video_tail_hold)


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


# --- (a) 丸めの向き ----------------------------------------------------------

def _cumsums():
    """time() の連結で実際に出る「尺の足し算」の値"""
    out = []
    for step in (0.1, 0.2, 0.3, 0.7, 1.1, 2.35, 4.9, 1 / 3, 2 / 30):
        t = 0.0
        for _ in range(400):
            t += step
            out.append(t)
    return out


def test_floor_ceil_bracket_value_within_1us():
    for x in _cumsums():
        lo, hi = _t_floor(x), _t_ceil(x)
        assert lo <= x <= hi, (x, lo, hi)
        assert x - lo < 1.0001e-6 and hi - x < 1.0001e-6, (x, lo, hi)
        # 小数6桁以内で書ける（フィルタ文字列に端数を出さない）
        for v in (lo, hi):
            assert len(repr(v).split(".")[1]) <= 6, (x, v)


def test_integers_and_clean_floats_keep_their_spelling():
    """既存の出力（スナップショット）を変えない: int は int、端数の無い float はそのまま"""
    for x in (0, 3, 600):
        assert _t_floor(x) is x and _t_ceil(x) is x
    for x in (0.0, 3.0, 12.5, 804.9, 0.25):
        assert repr(_t_floor(x)) == repr(x) and repr(_t_ceil(x)) == repr(x)


def test_enable_from_opens_1us_before_the_nearest_frame():
    assert _t_enable_from(0, 30) == 0 and _t_enable_from(0.0, 30) == 0.0
    assert _t_enable_from(3, 30) == 2.999999
    assert _t_enable_from(3.7, 30) == 3.699999
    assert _t_enable_from(804.9000000000001, 30) == 804.899999
    assert _t_enable_from(2 / 30, 30) == 0.066665
    # 格子から外れた開始は、tpad が中身を届ける「最も近いフレーム」へそろえる
    assert _t_enable_from(207.339, 30) == 207.333332     # 6220.17 枚目 → 6220 枚目
    assert _t_enable_from(207.35, 30) == 207.366665      # 6220.5 枚目 → 6221 枚目（半分は切り上げ）
    assert _t_enable_from(55.65, 30) == 55.666665        # 1669.5 枚目 → 1670 枚目（浮動小数だと 1669.49…）
    assert _t_enable_from(0.01, 30) == 0.0               # 0.3 枚目 → 0 枚目


@pytest.mark.parametrize("fps", [24, 25, 30, 49, 60, 120, 30000 / 1001, 60000 / 1001])
def test_enable_window_catches_the_start_frame(fps):
    """開始時刻がフレーム格子上（n/fps、または time() の足し算で端数の乗った値）なら、
    その n 枚目が窓に入り、1つ前の枚は入らない。

    overlay の t は pts*av_q2d(タイムベース) なので、ここでも同じ掛け算で比べる
    （開始時刻をそのまま書くと、30fps では 3.7 秒開始の 111 枚目が外れる）。
    """
    q = Fraction(fps).limit_denominator(1001)
    tb = float(1 / q)
    for n in range(1, 20000):
        t = n * tb
        for start in (float(n / q), float(n / q) * (1 + 2e-16), float(n / q) * (1 - 2e-16)):
            lo = _t_enable_from(start, fps)
            assert lo <= t, (fps, n, start, lo, t)
            assert (n - 1) * tb < lo, (fps, n, start, lo)
    # 整数秒（49fps では 1×49×(1/49) < 1 になる）
    for sec in range(1, 2000):
        n = sec * q
        if n.denominator == 1:
            assert _t_enable_from(sec, fps) <= int(n) * tb, (fps, sec)


@pytest.mark.parametrize("fps", [24, 30, 60, 30000 / 1001])
def test_enable_window_opens_where_tpad_delivers_the_first_frame(fps):
    """格子から外れた開始でも、窓は tpad が中身の1枚目を届けるフレームから開き、
    その1つ前のフレームは入らない。tpad は start_duration を µs の整数で読み、
    クローンの枚数を「開始×fps」の四捨五入（半分は切り上げ）にする。"""
    q = Fraction(fps).limit_denominator(1001)
    tb = float(1 / q)
    for k in range(1, 4000):
        start = k * 0.0371   # 格子とずれ続ける開始時刻（半分ちょうどの例も含む）
        us = Fraction(repr(_t_floor(start)))   # tpad が読む開始（µs）
        n = int(us * q + Fraction(1, 2))         # tpad が中身を送るフレーム
        lo = _t_enable_from(start, fps)
        assert lo <= n * tb, (fps, start, n, lo)
        assert (n - 1) * tb < lo, (fps, start, n, lo)


# --- (b) フィルタ文字列 ------------------------------------------------------

def _overlay_line(parts):
    return next(p for p in parts if "overlay=" in p)


def test_enable_and_tpad_have_no_float_fuzz(monkeypatch):
    monkeypatch.setattr(video_mod, "_get_base_dimensions", lambda obj: (64, 36))
    p = sv.Project()
    p.configure(width=320, height=180, fps=30)
    o = sv.Object("clip.mp4")
    o.start_time = 804.9000000000001
    o.duration = 1.1
    parts, _ = _build_video_overlay_parts(o, 1, "[0:v]", 1000)
    assert "tpad=start_duration=804.9:start_mode=clone" in parts[0]
    end = o.start_time + o.duration
    assert end == 806.0000000000001  # 前提: 終了側にも端数が乗っている
    assert f"enable='between(t\\,804.899999\\,{_t_ceil(end)})'" in _overlay_line(parts)
    assert _t_ceil(end) == 806.000001
    assert "0000000" not in "".join(parts)
    # 存在しない素材は probe しない（保持の tpad も付かない）
    assert "stop_mode" not in parts[0]


# --- (c) 実レンダ ------------------------------------------------------------

_W, _H, _FPS = 64, 36, 30


def _need_ffmpeg():
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg / ffprobe が無い環境")


def _make_clip(path, seconds, lum, audio_seconds=None):
    """尺ちょうど（seconds*30 枚）で映像が EOF になる一様な輝度の素材。

    audio_seconds を渡すと、それだけの長さの AAC 音声を付ける（映像より長くできる）。
    """
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"color=c=black:s={_W}x{_H}:r={_FPS}:d={seconds}"]
    if audio_seconds is not None:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={audio_seconds}"]
    cmd += ["-vf", f"format=yuv444p,geq=lum={lum}:cb=128:cr=128",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "1"]
    if audio_seconds is not None:
        cmd += ["-c:a", "aac"]
    subprocess.run(cmd + [str(path)], check=True, capture_output=True, timeout=60)


def _center_luma(video):
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True, timeout=60).stdout
    size = _W * _H
    return [out[i * size + (_H // 2) * _W + _W // 2] for i in range(len(out) // size)]


def _render_chain(tmp_path, lines, total_frames):
    layer = tmp_path / "l_chain.py"
    layer.write_text("from scriptvedit import *\n" + "".join(l + "\n" for l in lines), encoding="utf-8")
    p = sv.Project()
    p.configure(width=_W, height=_H, fps=_FPS, background_color="black")
    p.layer(str(layer), priority=1)
    out = tmp_path / "out.mp4"
    p.render(str(out), timeout=300)
    lum = _center_luma(out)
    assert len(lum) >= total_frames, lum
    return lum


def test_time_chain_boundary_frame_is_not_black(tmp_path):
    """0.1 秒の素材を3本 time() で並べた直後（開始 0.30000000000000004）の 9 枚目が黒くならない"""
    _need_ffmpeg()
    short = tmp_path / "short.mp4"
    tail = tmp_path / "tail.mp4"
    _make_clip(short, 0.1, 200)
    _make_clip(tail, 0.5, 120)
    lum = _render_chain(tmp_path, [
        f"Object({str(short)!r}).time(0.1)",
        f"Object({str(short)!r}).time(0.1)",
        f"Object({str(short)!r}).time(0.1)",
        f"Object({str(tail)!r}).time(0.5)",
    ], 24)
    assert len(lum) == 24, lum
    dark = [i for i, v in enumerate(lum) if v < 60]
    assert not dark, f"背景（黒）が素通しになったフレーム: {dark} / 輝度 {lum}"


@pytest.mark.parametrize("audio_extra", [0.006, 0.03])
def test_clips_with_longer_audio_chain_without_black_frames(tmp_path, audio_extra):
    """音声が映像より長い素材（scriptvedit が書き出す mp4 と同じ形）を time() で並べても、
    境目が黒くならず、次の素材の中身もすぐ出る。

    0.006 秒は半フレーム未満（窓を最も近いフレームへそろえるだけで直る）、
    0.03 秒は半フレーム超（最後のフレームの保持が要る）。AAC の priming で
    実際の音声の尺はさらに少し延びる。
    """
    _need_ffmpeg()
    clips = []
    for k, lum in enumerate((200, 150, 100)):
        path = tmp_path / f"c{k}.mp4"
        _make_clip(path, 0.2, lum, audio_seconds=0.2 + audio_extra)
        clips.append(path)
    tail = tmp_path / "tail.mp4"
    _make_clip(tail, 0.5, 60)
    lum = _render_chain(tmp_path, [f"Object({str(c)!r}).time()" for c in clips]
                        + [f"Object({str(tail)!r}).time(0.5)"], 30)
    body = lum[:27]   # 3本 × 約 0.2 秒 ＋ 末尾の素材の頭
    dark = [i for i, v in enumerate(body) if v < 40]
    assert not dark, f"背景（黒）が素通しになったフレーム: {dark} / 輝度 {lum}"
    # 3本とも中身が出ている（gray は (Y-16)*255/219 へ伸びる）
    for want in (200, 150, 100):
        g = (want - 16) * 255 / 219
        assert any(abs(v - g) <= 10 for v in body), (want, lum)


def test_video_tail_hold_is_zero_when_streams_match(tmp_path):
    """映像と音声の尺が同じ素材・音声の無い素材には保持を付けない"""
    _need_ffmpeg()
    silent = tmp_path / "silent.mp4"
    _make_clip(silent, 0.5, 100)
    p = sv.Project()
    p.configure(width=_W, height=_H, fps=_FPS)
    activate(p)
    o = sv.Object(str(silent))
    o.duration = o.length()
    assert _video_tail_hold(o) == 0.0
    longer = tmp_path / "longer.mp4"
    _make_clip(longer, 0.5, 100, audio_seconds=0.6)
    o2 = sv.Object(str(longer))
    o2.duration = o2.length()
    hold = _video_tail_hold(o2)
    assert 0.09 < hold < 0.15, hold
    # time(d) で素材より短く切れば、映像が尽きる前に終わるので保持しない
    o2.duration = 0.4
    assert _video_tail_hold(o2) == 0.0
