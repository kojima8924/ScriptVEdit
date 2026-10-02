# -*- coding: utf-8 -*-
"""秒で書く時間の式（elapsed / remaining / ramp / keyframes_sec）。

u（0..1）しか無いと、表示区間の一部でだけ動かすたびに「秒 ÷ 表示秒」を手で書くことになり、
表示秒を変えると式も変わる。秒のノード（expr.py の _TimeVar）は、フィルタ生成時に
_u_expr が渡す _UStr（u の式 + 経過秒の式 + 表示秒）から t-start をそのまま読む。
"""

import shutil
import subprocess
import types

import pytest

import scriptvedit as sv
from scriptvedit import (
    Object, avolume, clip, elapsed, ease_out_cubic, fade, keyframes_sec, lerp,
    move, ramp, remaining, rotate, scale, wipe)
from scriptvedit.cache import _op_fingerprint_str
from scriptvedit.context import _exec_stack, activate, current_project
from scriptvedit.expr import Expr, _UStr, _UValue
from scriptvedit.filters.audio import _build_audio_effect_filters
from scriptvedit.filters.video import _build_effect_filters, _build_move_exprs, _u_expr
from scriptvedit.text import _counter_progress


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


def _at(expr, sec, dur):
    """表示秒 dur の Object の、経過 sec 秒での値"""
    return float(expr.eval_at(_UValue(sec / dur, dur)))


# --- 値 ------------------------------------------------------------------

@pytest.mark.parametrize("dur", [2.0, 4.0, 10.0])
def test_ramp_does_not_depend_on_display_seconds(dur):
    """ramp(1, 2) は表示秒が何秒でも「1〜2 秒の間に 0→1」"""
    r = ramp(1.0, 2.0)
    assert isinstance(r, Expr)
    assert _at(r, 0.0, dur) == 0.0
    assert _at(r, 1.0, dur) == pytest.approx(0.0, abs=1e-9)
    assert _at(r, 1.5, dur) == pytest.approx(0.5)
    assert _at(r, 2.0, dur) == pytest.approx(1.0)
    if dur > 2:
        assert _at(r, dur, dur) == 1.0


def test_ramp_from_end_counts_back_from_the_end():
    r = ramp(0.5, 0, from_end=True)          # 最後の 0.5 秒で 0→1
    for dur in (3.0, 7.0):
        assert _at(r, dur - 1.0, dur) == 0.0
        assert _at(r, dur - 0.25, dur) == pytest.approx(0.5)
        assert _at(r, dur, dur) == pytest.approx(1.0)


def test_ramp_easing_is_applied_to_the_local_progress():
    r = ramp(1.0, 2.0, ease_out_cubic)
    assert _at(r, 1.5, 4.0) == pytest.approx(1 - 0.5 ** 3)
    assert _at(r, 0.5, 4.0) == 0.0 and _at(r, 3.0, 4.0) == pytest.approx(1.0)


def test_elapsed_and_remaining():
    assert _at(elapsed(), 1.25, 5.0) == pytest.approx(1.25)
    assert _at(remaining(), 1.25, 5.0) == pytest.approx(3.75)
    # lambda の中でも u と混ぜて使える
    e = sv.expr._resolve_param(lambda u: u + elapsed())
    assert _at(e, 2.0, 4.0) == pytest.approx(0.5 + 2.0)


def test_keyframes_sec_interpolates_in_seconds():
    k = keyframes_sec((0, 0), (0.25, 1), (5.5, 1), (6, 0))
    assert isinstance(k, Expr)
    for dur in (6.0, 12.0):
        assert _at(k, 0.125, dur) == pytest.approx(0.5)
        assert _at(k, 3.0, dur) == pytest.approx(1.0)
        assert _at(k, 5.75, dur) == pytest.approx(0.5)
    # フラット形式・順不同でも同じ
    flat = keyframes_sec(6, 0, 0, 0, 5.5, 1, 0.25, 1)
    assert _at(flat, 5.75, 6.0) == pytest.approx(0.5)


# --- ffmpeg の式 ---------------------------------------------------------

def test_to_ffmpeg_uses_t_minus_start_directly():
    """式には u×dur ではなく clip(t-start,0,dur) がそのまま入る"""
    u = _u_expr(3, 4)
    assert isinstance(u, _UStr) and u == "clip((t-3)/4\\,0\\,1)"
    assert ramp(1.0, 2.0).to_ffmpeg(u) == "clip((clip(t-3\\,0\\,4)-1.0)\\,0\\,1)"
    assert remaining().to_ffmpeg(_u_expr(3, 4, "T")) == "(4-clip(T-3\\,0\\,4))"
    # u の式しか無い呼び出し側（_UStr を sec 省略で作る）でも (u)×dur で正しい値になる
    assert elapsed().to_ffmpeg(_UStr("U", 4)) == "((U)*4)"


def test_plain_string_u_gives_symbol_not_a_wrong_number():
    """表示秒を持たない素の文字列では記号になる（キャッシュ鍵・静的判定用）"""
    assert elapsed().to_ffmpeg("u") == "sec(u)"
    assert ramp(0, 1).to_ffmpeg("0") != ramp(0, 1).to_ffmpeg("1")
    with pytest.raises(ValueError, match="表示秒"):
        ramp(0, 1).eval_at(0.5)
    # 静的 Transform に渡すと「時間依存の式」として構築時に弾かれる
    with pytest.raises(ValueError, match="rotate_to"):
        rotate(rad=ramp(0, 1))


def test_cache_key_distinguishes_seconds_from_u():
    a = _op_fingerprint_str(fade(ramp(0, 0.5)))
    b = _op_fingerprint_str(fade(lambda u: clip(u / 0.5, 0, 1)))
    c = _op_fingerprint_str(fade(ramp(0, 0.6)))
    assert "sec(u)" in a and a != b and a != c
    assert a == _op_fingerprint_str(fade(ramp(0, 0.5)))


def _fx(effect, start=0, dur=4, base_dims=None):
    obj = types.SimpleNamespace(effects=[effect], transforms=[], media_type="image",
                                source="x.png")
    filters, _pad = _build_effect_filters(obj, start, dur, base_dims=base_dims)
    return filters


def test_effect_filters_embed_seconds():
    # wipe（geq は大文字 T）
    f = ",".join(_fx(wipe("left", progress=ramp(1.2, 1.6)), start=2, dur=6))
    assert "clip(T-2\\,0\\,6)-1.2" in f
    # fade: 区分線形なので native fade へ変換される（表示秒が変わっても 0.5 秒のまま）
    for dur in (4, 9):
        f = _fx(fade(ramp(0, 0.5)), dur=dur)
        assert f[0] == "format=rgba" and len(f) == 2, f
        head, d, tail = f[1].partition(":d=")
        assert head == "fade=t=in:st=0" and tail.endswith(":alpha=1"), f
        assert float(tail.split(":")[0]) == pytest.approx(0.5, abs=1e-6), f
    # scale: pad サイズの見積もり（eval_at の格子）にも表示秒が渡る
    f = _fx(scale(lambda u: lerp(1.0, 1.5, ramp(1, 2))), dur=4, base_dims=(100, 80))
    assert any(x.startswith("pad=150:120:") for x in f), f
    assert "clip(t-0\\,0\\,4)-1" in f[0]


def test_move_and_audio_embed_seconds():
    obj = types.SimpleNamespace(
        effects=[move(x=lambda u: lerp(0.1, 0.9, ramp(0, 2)), y=0.5, anchor="center")])
    x, _ = _build_move_exprs(obj, 5, 3)
    assert "clip(t-5\\,0\\,3)/2" in x
    a = types.SimpleNamespace(audio_effects=[avolume(1 - ramp(1.0, 0, from_end=True))])
    flt = _build_audio_effect_filters(a, 8)[-1]
    # 音声側は adelay 前のローカル時間（start を引かない）+ NaN の包み（§4.8）
    assert "clip(if(isnan(t)\\,0\\,t)\\,0\\,8)" in flt and "(t-" not in flt


# --- エラー --------------------------------------------------------------

@pytest.mark.parametrize("call, exc, match", [
    (lambda: ramp(2, 1), ValueError, "a.*<.*b"),
    (lambda: ramp(1, 1), ValueError, "a.*<.*b"),
    (lambda: ramp(-1, 1), ValueError, "0 以上の秒"),
    (lambda: ramp("0", 1), ValueError, "0 以上の秒"),
    (lambda: ramp(0, float("inf")), ValueError, "0 以上の秒"),
    (lambda: ramp(0, 1, easing="ease_out_cubic"), TypeError, "イージング関数"),
    (lambda: ramp(0, 0.5, from_end=True), ValueError, "from_end"),
    (lambda: keyframes_sec(), ValueError, "最低2つ"),
    (lambda: keyframes_sec((0, 0)), ValueError, "最低2つ"),
    (lambda: keyframes_sec(0, 0, 1), ValueError, "偶数個"),
    (lambda: keyframes_sec((0, 0), (-1, 1)), ValueError, "0 以上の秒"),
    (lambda: keyframes_sec((0, 0), (1, float("nan"))), ValueError, "有限の数値"),
    (lambda: keyframes_sec((0, 0), (1, 1, 1)), ValueError, "2要素タプル"),
    (lambda: keyframes_sec(*[(i, 0) for i in range(129)]), ValueError, "最大128点"),
    (lambda: keyframes_sec((0, 0), (1, 1), easing=3), TypeError, "イージング関数"),
])
def test_errors(call, exc, match):
    with pytest.raises(exc, match=match):
        call()


def test_describe_lists_seconds_exprs():
    names = {e["name"]: e for e in sv.describe(kind="expr")["expr"]}
    for n in ("elapsed", "remaining", "ramp", "keyframes_sec"):
        assert names[n]["category"] == "秒で書く時間", n
        assert n in sv.__all__


# --- counter の進行度（text.py の _counter_progress）-----------------------

def test_counter_progress_carries_display_seconds():
    """counter の u も _UStr（表示秒は Object の尺そのもの。分母を詰めた span ではない）"""
    p = sv.Project()
    p.configure(width=64, height=36, fps=30)
    u = _counter_progress(2, 3)
    assert isinstance(u, _UStr) and u.dur == 3
    assert u.sec == _u_expr(2, 3).sec
    assert remaining().to_ffmpeg(u) == remaining().to_ffmpeg(_u_expr(2, 3))
    # 1 フレーム以下の尺: u は "1" のまま、秒の式は記号 sec(1) にならない
    one = _counter_progress(0, 1 / 30)
    assert one == "1" and isinstance(one, _UStr)
    out = ramp(0, 0.01).to_ffmpeg(one)
    assert "sec(" not in out and "clip(t-0" in out


def test_real_render_one_frame_counter_with_seconds_easing(tmp_path):
    """1 フレームの counter の easing に秒の式を使っても ffmpeg が落ちない"""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg / ffprobe が無い環境")
    layer = tmp_path / "l_counter.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "c = counter(0, 100, size=20, color='white', easing=lambda u: ramp(0, 0.01))\n"
        "c.time(1 / 30)\n"
        "d = counter(0, 50, size=20, color='white', easing=lambda u: 1 - remaining() / 0.5)\n"
        "d.time(0.5)\n", encoding="utf-8")
    p = sv.Project()
    p.configure(width=_W, height=_H, fps=_FPS, background_color="black")
    try:
        p.layer(str(layer), priority=1)
        out = tmp_path / "out.mp4"
        p.render(str(out), timeout=300)
    except FileNotFoundError as e:
        if "フォント" in str(e):
            pytest.skip(f"フォントが無い環境: {str(e).splitlines()[0]}")
        raise
    assert len(_center_luma(out)) == 16


# --- 実レンダ ------------------------------------------------------------

_W, _H, _FPS = 64, 36, 30


def _center_luma(video):
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True, timeout=60).stdout
    size = _W * _H
    return [out[i * size + (_H // 2) * _W + _W // 2] for i in range(len(out) // size)]


@pytest.mark.parametrize("show", [2.0, 3.0])
def test_real_render_ramp_moves_at_the_same_seconds(tmp_path, show):
    """白い板を opacity の式（live の geq）で 1.0〜1.5 秒に現す。表示秒を変えても同じ時刻に動く"""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg / ffprobe が無い環境")
    white = tmp_path / "white.png"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"color=c=white:s={_W}x{_H}", "-frames:v", "1", str(white)],
                   check=True, capture_output=True, timeout=60)
    layer = tmp_path / "l_ramp.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "pause.time(0.5)\n"
        f"o = Object({str(white)!r})\n"
        f"o.time({show}) <= -opacity(ramp(1.0, 1.5))\n", encoding="utf-8")
    p = sv.Project()
    p.configure(width=_W, height=_H, fps=_FPS, background_color="black")
    p.layer(str(layer), priority=1)
    out = tmp_path / "out.mp4"
    p.render(str(out), timeout=300)
    lum = _center_luma(out)
    assert len(lum) == int((0.5 + show) * _FPS), len(lum)
    # Object は 0.5 秒から。経過 1.0 秒 = 45 枚目、1.25 秒 = 52〜53 枚目、1.5 秒 = 60 枚目
    assert max(lum[:45]) < 30, lum[:46]
    assert 90 < lum[52] < 170, lum[50:55]
    assert min(lum[61:]) > 220, lum[60:]
