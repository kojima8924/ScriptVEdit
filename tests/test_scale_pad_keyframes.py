# -*- coding: utf-8 -*-
"""scale() の pad 見積もりが、多点の keyframes の細い山を取りこぼさないこと。

scale の出力は固定サイズの pad に収める（SEGV バリア。CLAUDE.md §4.1）。pad の大きさは
式を u の格子（通常 100 等分）で評価した最大から決めていたので、格子の間に収まる細い山
（例: 10 秒の 4.02〜4.08 秒だけ 2 倍）は 1.0 しか拾えず、実際に描くコマの大きさが pad を
超えて「Padded dimensions cannot be smaller than input dimensions」（EINVAL）で落ちた。
コマの時刻（タイムラインの 1/fps 刻み）と式の頂点でも評価する（filters/video.py の
_expr_frame_max。区分線形の式は頂点と頂点の前後のコマだけで全コマと同じ最大になる。
手間が点の数・コマ数に比例しないことは tests/test_expr_scan.py）。
"""
import shutil
import subprocess
import types

import pytest

import scriptvedit as sv
from scriptvedit import keyframes, keyframes_sec, scale
from scriptvedit.context import _exec_stack, activate, current_project
from scriptvedit.filters.video import _build_effect_filters, _expr_breakpoint_us, _frame_us

# 10 秒のうち 4.02〜4.08 秒だけ 2 倍になる山（100 等分の格子 = 0.1 秒刻みでは見えない）
_PEAK = ((0, 1), (4.02, 1), (4.05, 2), (4.08, 1), (10, 1))


@pytest.fixture(autouse=True)
def _restore_project_globals():
    old_current = current_project()
    old_stack = list(_exec_stack)
    activate(None)
    _exec_stack[:] = []
    try:
        yield
    finally:
        activate(old_current)
        _exec_stack[:] = old_stack


def _pad(effect, start=0, dur=10):
    obj = types.SimpleNamespace(effects=[effect], transforms=[], media_type="image",
                                source="x.png")
    filters, pad = _build_effect_filters(obj, start, dur, base_dims=(64, 64))
    return pad, [f for f in filters if f.startswith("pad=")]


def test_narrow_peak_between_grid_points_is_covered():
    pad, f = _pad(scale(keyframes_sec(*_PEAK)))
    # 頂点 2.0 倍 → 64×2 = 128（以前は格子で 1.0 しか拾えず 64）
    assert pad == (128, 128)
    assert f == ["pad=128:128:(ow-iw)/2:(oh-ih)/2:color=0x00000000:eval=frame"]
    # u で書く keyframes も同じ（境目は u のまま）
    pad, _ = _pad(scale(keyframes((0, 1), (0.402, 1), (0.405, 2), (0.408, 1), (1, 1))))
    assert pad == (128, 128)


def test_ramp_peak_between_frames_is_covered():
    """ramp() で作った細い山（clip の折れ点。lt の境目を持たない）も頂点の値で拾う。

    4.05 秒の頂点 2.0 はどのコマ（4.0333 / 4.0667 秒は約 1.44）にも当たらないが、keyframes の
    頂点と同じく pad に含める（時刻が ms に丸まる素材では、コマの値が見積もりを超えうる）。
    区分線形の式は区分の端（頂点）と端の前後のコマだけを評価する（filters/video.py の
    _expr_frame_max）。
    """
    peak = sv.lerp(1, 2, sv.ramp(4.02, 4.05)) - sv.ramp(4.05, 4.08)
    pad, _ = _pad(scale(peak))
    assert pad == (128, 128)


def test_sample_points():
    # 経過秒の境目は表示秒で割って u へ
    us = _expr_breakpoint_us(keyframes_sec(*_PEAK), 10)
    assert sorted(round(u, 6) for u in us) == [0.402, 0.405, 0.408, 1.0]
    # コマの時刻: 開始 0.5 秒・1 秒・30fps → 前後1コマ余分に取り、0..1 へ clip
    us = _frame_us(0.5, 1.0)
    assert us[0] == 0.0 and us[-1] == 1.0
    assert any(abs(u - (16 / 30 - 0.5)) < 1e-12 for u in us)
    assert _frame_us(0, 0) == []


def test_render_with_narrow_peak_does_not_fail(tmp_path):
    """細い山の scale を live（-scale）で実レンダしても pad 不足で落ちない"""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い環境")
    png = tmp_path / "red.png"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1", str(png)],
        check=True, capture_output=True, timeout=60)
    layer = tmp_path / "l_peak.py"
    layer.write_text(
        "from scriptvedit import *\n"
        f"o = Object({str(png)!r})\n"
        "o.time(10) <= move(x=0.5, y=0.5, anchor='center') & "
        f"-scale(keyframes_sec(*{_PEAK!r}))\n",
        encoding="utf-8")
    p = sv.Project()
    p.configure(width=160, height=160, fps=30, background_color="white")
    p.layer(str(layer), priority=0)
    out = tmp_path / "peak.mp4"
    p.render(str(out), start=3.9, end=4.2)
    # 4.0333 秒のコマ（山の途中、約 1.44 倍）が描かれている: 中央の横一列で赤い幅を数える
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(out),
         "-vf", "select='eq(n\\,4)',format=rgb24,crop=160:1:0:80",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True, timeout=60).stdout
    red = sum(1 for x in range(160) if raw[3 * x] > 200 and raw[3 * x + 1] < 80)
    assert 86 <= red <= 98, red
