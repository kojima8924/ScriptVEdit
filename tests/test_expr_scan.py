# -*- coding: utf-8 -*-
"""時間だけの式を表示区間の上で速く・漏れなく調べる道具（scriptvedit.expr_scan）と、
それを使う scale の pad 見積もり・native fade の判定の手間。

scale の pad 見積もりと native fade の判定は、多点の keyframes の細い山・谷を取りこぼさない
ように描くコマのすべてで式を確かめる（CLAUDE.md §4.13）。以前は全コマで eval_at を呼び、
eval_at は if の両方の枝を評価するので、手間が「コマ数 × 点の数」に比例した（実測: 60fps・
128 点の scale で 600 秒の Object はフィルタを組むたびに 17 秒、3600 秒は 107 秒）。
  (a) _compile_u_eval は eval_at と同じ値を返し、if は選んだ枝だけを評価する
  (b) _pl_pieces は区分線形の式を区分ごとの1次式へ展開する（区分の内側で式と一致）。
      イージング・振動系・画素ごとの変数・中身を辿れない派生は None
  (c) _expr_frame_max は「全コマ + 頂点」を評価したのと同じ最大を返す
  (d) 評価の回数・時間が点の数とコマ数に比例しない（区分線形の式）
"""
import random as _random
import time
import types

import pytest

import scriptvedit as sv
from scriptvedit import (Expr, PI, abs as sv_abs, between, ease_in_out_sine, ease_out_back,
                         ease_out_cubic, fade, gt, if_, keyframes, keyframes_sec, lerp, lt, max as sv_max,
                         min as sv_min, mod, not_, pow as sv_pow, ramp, random, scale,
                         sequence_param, sin)
from scriptvedit.context import _exec_stack, activate, current_project
from scriptvedit.expr import Const, Var, _UValue
from scriptvedit.expr_scan import _compile_u_eval, _pl_ends, _pl_pieces
from scriptvedit.filters import video as video_mod
from scriptvedit.filters.video import (_build_effect_filters, _expr_breakpoint_us, _expr_frame_max,
                                       _frame_us, _frame_us_near, _try_native_fade)

U = Var("u")


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


def _project(fps=30):
    p = sv.Project()
    p.configure(width=320, height=240, fps=fps)
    activate(p)
    return p


def _tri(n, dur, lo=1.0, hi=1.5):
    """0..dur 秒に lo / hi を交互に置いた n 点（三角の山を並べた形）"""
    return [(round(dur * i / (n - 1), 6), hi if i % 2 else lo) for i in range(n)]


def _random_exprs(seed, count, dur):
    """区分線形の式と、そうでない式（イージング・振動系・pow）を混ぜて作る"""
    rnd = _random.Random(seed)
    out = []
    for _ in range(count):
        kind = rnd.randrange(7)
        n = rnd.randint(2, 30)
        if kind <= 1:
            ts = sorted(rnd.uniform(0, dur) for _ in range(n))
            pts = [(t, rnd.uniform(0.2, 2.5)) for t in ts]
            easing = rnd.choice([None, None, ease_in_out_sine, ease_out_back, ease_out_cubic])
            expr = keyframes_sec(*pts, easing=easing)
        elif kind == 2:
            ts = sorted(rnd.uniform(-0.1, 1.1) for _ in range(n))
            expr = keyframes(*[(t, rnd.uniform(0.2, 2.5)) for t in ts])(U)
        elif kind == 3:
            a = rnd.uniform(0, dur * 0.8)
            b = a + rnd.uniform(0.01, dur - a)
            expr = lerp(rnd.uniform(0.3, 1), rnd.uniform(1, 2.2), ramp(a, b))
            expr = expr * (1 + 0.3 * ramp(rnd.uniform(0, dur / 2), dur / 2 + 0.1))
        elif kind == 4:
            c = rnd.uniform(0.1, 0.9)
            expr = sequence_param((0, c, lambda t: 0.5 + t),
                                  (c, 1.0, rnd.uniform(0.4, 1.8)))(U)
        elif kind == 5:
            # min / max / abs / 比較 / between / not の組み合わせ（区分線形）
            expr = (sv_max(U * 2 - 0.3, sv_min(1 - U, 0.4)) + sv_abs(U - rnd.random())
                    + between(U, 0.2, rnd.uniform(0.3, 0.9)) * 0.5
                    + not_(gt(U, rnd.random())) * 0.25)
        else:
            # 区分線形でない式
            expr = rnd.choice([
                1 + 0.3 * sin(U * PI * rnd.randint(1, 9)),
                sv_pow(U, 2) + 0.5,
                mod(U * 3, 1) + 0.2,
                if_(lt(U, 0.5), U * U, 1 - U),
            ])
        out.append(expr)
    return out


# --- (a) 枝を辿る評価 ---------------------------------------------------------------

def test_compiled_eval_matches_eval_at():
    for dur in (1.0, 6.0, 37.3):
        for expr in _random_exprs(1, 120, dur):
            ev = _compile_u_eval(expr, dur)
            for i in range(0, 201):
                u = i / 200
                assert ev(u) == expr.eval_at(_UValue(u, dur)), (expr.to_ffmpeg("u"), u)


def test_compiled_eval_if_takes_only_the_chosen_branch():
    # 選ばれない枝の 0 除算は評価しない（ffmpeg の式評価器と同じ。eval_at は両方の枝を評価する）
    expr = if_(lt(U, 0.5), U, Const(1) / (U - U))
    ev = _compile_u_eval(expr, 1.0)
    assert ev(0.25) == 0.25
    with pytest.raises(ZeroDivisionError):
        expr.eval_at(_UValue(0.25, 1.0))
    with pytest.raises(ZeroDivisionError):
        ev(0.75)


class _OpaqueExpr(Expr):
    """中身を辿れない Expr の派生（テスト用）"""

    def to_ffmpeg(self, u_expr):
        return f"(2*{u_expr})"

    def eval_at(self, u_value):
        return 2 * float(u_value)


def test_compiled_eval_falls_back_to_eval_at():
    ev = _compile_u_eval(_OpaqueExpr() + 1, 1.0)
    assert ev(0.25) == 1.5
    # u 以外の変数・表示秒の分からない秒の式は eval_at と同じ例外
    with pytest.raises(ValueError):
        _compile_u_eval(Var("X"), 1.0)(0.5)
    with pytest.raises(ValueError):
        _compile_u_eval(keyframes_sec((0, 0), (1, 1)), None)(0.5)


# --- (b) 区分線形の展開 -------------------------------------------------------------

def test_pl_pieces_of_keyframes():
    # 6 秒で 0 → 1（0〜1.5 秒）→ 1（〜4.5 秒）→ 0（〜6 秒）
    pieces = _pl_pieces(keyframes_sec((0, 0), (1.5, 1), (4.5, 1), (6, 0)), 6.0)
    assert [(round(x0, 9), round(x1, 9)) for x0, x1, _, _ in pieces] == [
        (0, 0.25), (0.25, 0.75), (0.75, 1)]
    (_, _, a0, b0), (_, _, a1, b1), (_, _, a2, b2) = pieces
    assert (round(a0, 9), round(b0, 9)) == (4, 0)
    assert (a1, b1) == (0, 1)
    assert (round(a2, 9), round(b2, 9)) == (-4, 4)
    assert _pl_ends(pieces)[0] == 0.0 and _pl_ends(pieces)[-1] == 1.0


def test_pl_pieces_rejects_non_linear_and_pixel_exprs():
    assert _pl_pieces(keyframes_sec((0, 1), (1, 2), easing=ease_out_cubic), 2.0) is None
    assert _pl_pieces(1 + 0.3 * sin(U * PI), 1.0) is None
    assert _pl_pieces(U * U, 1.0) is None
    assert _pl_pieces(mod(U * 3, 1), 1.0) is None
    assert _pl_pieces(U * random(1), 1.0) is None
    assert _pl_pieces(Var("X") / 100, 1.0) is None
    assert _pl_pieces(_OpaqueExpr(), 1.0) is None
    # 秒の式は表示秒が分からなければ展開しない
    assert _pl_pieces(ramp(0.2, 0.4), None) is None


def test_pl_pieces_match_the_expression_inside_each_piece():
    """各区分の内側（端から少し離れた点）で、1次式の値が式の値と一致する"""
    count = 0
    for dur in (1.0, 6.0, 37.3):
        for expr in _random_exprs(2, 150, dur):
            pieces = _pl_pieces(expr, dur)
            if pieces is None:
                continue
            count += 1
            for x0, x1, a, b in pieces:
                assert x0 < x1
                for frac in (0.25, 0.5, 0.75):
                    u = x0 + (x1 - x0) * frac
                    want = float(expr.eval_at(_UValue(u, dur)))
                    assert abs((a * u + b) - want) <= 1e-9 * max(1.0, abs(want)), (u, want)
    assert count > 200   # 区分線形の式が十分に含まれていること


# --- (c) 描くコマでの最大 -----------------------------------------------------------

def test_frame_max_equals_brute_force_over_all_frames():
    """区分線形なら「区分の端の前後のコマ + 端」だけで、全コマ + 端を評価したのと同じ最大"""
    _project(fps=30)
    for dur in (1.0, 6.0, 37.3):
        for start in (0, 0.5, 1.2345):
            for expr in _random_exprs(3, 60, dur):
                got = _expr_frame_max(expr, start, dur)
                us = _frame_us(start, dur) + _expr_breakpoint_us(expr, dur)
                pieces = _pl_pieces(expr, dur)
                if pieces is not None:
                    us += _pl_ends(pieces)
                # 全コマの評価（eval_at と同じ値になることは test_compiled_eval_matches_eval_at）
                ev = _compile_u_eval(expr, dur)
                want = max(ev(u) for u in us)
                assert got == want, (expr.to_ffmpeg("u"), start, dur, got, want)


def test_frame_us_near_is_a_subset_of_frame_us():
    _project(fps=30)
    allus = set(_frame_us(0.5, 6.0))
    near = _frame_us_near(0.5, 6.0, [0.0, 0.123456, 0.5, 1.0])
    assert near and set(near) <= allus
    # 端の前後のコマ（0.123456 → 0.5 + 0.740736 秒 = 37.72 コマ目の前後）が入る
    assert any(abs(u - (37 / 30 - 0.5) / 6.0) < 1e-12 for u in near)
    assert any(abs(u - (38 / 30 - 0.5) / 6.0) < 1e-12 for u in near)
    assert _frame_us_near(0, 0, [0.5]) == []


# --- (d) 手間が点の数・コマ数に比例しない ----------------------------------------------

def _scale_chain(expr, dur, base=(1920, 1080)):
    obj = types.SimpleNamespace(effects=[scale(expr)], transforms=[], media_type="image",
                                source="x.png")
    return _build_effect_filters(obj, 0, dur, base_dims=base)


def _counting(monkeypatch):
    """_compile_u_eval が作る評価関数の呼び出し回数を数える"""
    calls = [0]
    real = video_mod._compile_u_eval

    def wrapper(expr, dur):
        ev = real(expr, dur)

        def counted(u):
            calls[0] += 1
            return ev(u)
        return counted
    monkeypatch.setattr(video_mod, "_compile_u_eval", wrapper)
    return calls


def test_piecewise_linear_scale_evaluates_points_not_frames(monkeypatch):
    """60fps・3600 秒（21.6 万コマ）でも、評価の回数は点の数に比例する（以前は全コマ）"""
    _project(fps=60)
    calls = _counting(monkeypatch)
    _, pad = _scale_chain(keyframes_sec(*_tri(128, 3600.0)), 3600.0)
    assert pad == (2880, 1620)
    # 格子 101 + 頂点 127 + 区分の端（128）とその前後のコマ（4 × 128）程度
    assert calls[0] < 2000, calls[0]


def test_native_fade_check_evaluates_points_not_frames(monkeypatch):
    _project(fps=60)
    calls = _counting(monkeypatch)
    dur = 3600.0
    kf = keyframes_sec((0, 0), (dur * 0.1, 1), (dur * 0.9, 1), (dur, 0))
    got = _try_native_fade(kf, 0, dur)
    assert got is not None and len(got) == 2
    assert got[0].startswith("fade=t=in:st=0:d=360") and got[1].startswith("fade=t=out:st=3240")
    # 格子 101 + 区分の端とその前後のコマ（以前は 21.6 万コマすべて）
    assert calls[0] < 200, calls[0]
    # 格子の間の細い谷は、長い Object でも native fade へ近似しない
    dip = keyframes_sec((0, 0), (360, 1), (1800, 1), (1800.01, 0), (1800.02, 1),
                        (3240, 1), (3600, 0))
    assert _try_native_fade(dip, 0, dur) is None


def test_filter_build_time_does_not_grow_with_points_and_frames():
    """60fps・3600 秒の Object にフィルタを組む時間（以前: 128 点の scale で 107 秒、8 点で 6.4 秒）。

    上限は遅い CI でも余裕のある値（実測は区分線形で 0.01 秒前後、イージング付きの
    600 秒で 0.1〜0.2 秒）。
    """
    _project(fps=60)
    for n in (8, 128):
        t0 = time.perf_counter()
        _scale_chain(keyframes_sec(*_tri(n, 3600.0)), 3600.0)
        assert time.perf_counter() - t0 < 1.0, n
        obj = types.SimpleNamespace(
            effects=[fade(keyframes_sec(*_tri(n, 3600.0, 0.0, 1.0)))],
            transforms=[], media_type="image", source="x.png")
        t0 = time.perf_counter()
        _build_effect_filters(obj, 0, 3600.0)
        assert time.perf_counter() - t0 < 1.0, n
    # 区分線形でない式（イージング）は描くコマをすべて評価するが、1回の評価は木の深さで済む
    t0 = time.perf_counter()
    _scale_chain(keyframes_sec(*_tri(128, 600.0), easing=ease_out_back), 600.0)
    assert time.perf_counter() - t0 < 4.0
