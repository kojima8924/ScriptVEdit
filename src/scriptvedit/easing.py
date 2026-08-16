# -*- coding: utf-8 -*-

import builtins as _builtins

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.effects.paths import _MAX_PATH_POINTS, _piecewise_scalar_expr
from scriptvedit.expr import Const, E, _to_expr, abs, clip, cos, floor, if_, lerp, lt, mod, pow, sin, sqrt


# --- イージング関数 ---

_EASE_PI = 3.141592653589793
_EASE_C1 = 1.70158
_EASE_C3 = _EASE_C1 + 1
_EASE_C4 = (2 * _EASE_PI) / 3
_EASE_C5 = (2 * _EASE_PI) / 4.5

def _power_n(expr, n):
    """expr^n を繰り返し乗算で構築"""
    result = expr
    for _ in range(n - 1):
        result = result * expr
    return result

def _ease_in_power(t, n):
    return _power_n(_to_expr(t), n)

def _ease_out_power(t, n):
    t = _to_expr(t)
    return Const(1) - _power_n(Const(1) - t, n)

def _ease_in_out_power(t, n):
    t = _to_expr(t)
    coeff = 2 ** (n - 1)
    branch1 = Const(coeff) * _power_n(t, n)
    branch2 = Const(1) - _power_n(Const(-2) * t + Const(2), n) / Const(2)
    return if_(lt(t, 0.5), branch1, branch2)

def linear(t):
    """線形イージング"""
    return _to_expr(t)

# Quad (二次)
def ease_in_quad(t): return _ease_in_power(t, 2)
def ease_out_quad(t): return _ease_out_power(t, 2)
def ease_in_out_quad(t): return _ease_in_out_power(t, 2)

# Cubic (三次)
def ease_in_cubic(t): return _ease_in_power(t, 3)
def ease_out_cubic(t): return _ease_out_power(t, 3)
def ease_in_out_cubic(t): return _ease_in_out_power(t, 3)

# Quart (四次)
def ease_in_quart(t): return _ease_in_power(t, 4)
def ease_out_quart(t): return _ease_out_power(t, 4)
def ease_in_out_quart(t): return _ease_in_out_power(t, 4)

# Quint (五次)
def ease_in_quint(t): return _ease_in_power(t, 5)
def ease_out_quint(t): return _ease_out_power(t, 5)
def ease_in_out_quint(t): return _ease_in_out_power(t, 5)

# Sine (正弦)
def ease_in_sine(t):
    t = _to_expr(t)
    return Const(1) - cos(t * Const(_EASE_PI / 2))

def ease_out_sine(t):
    t = _to_expr(t)
    return sin(t * Const(_EASE_PI / 2))

def ease_in_out_sine(t):
    t = _to_expr(t)
    return (Const(1) - cos(t * Const(_EASE_PI))) / Const(2)

# Exponential (指数)
def ease_in_expo(t):
    t = _to_expr(t)
    return if_(lt(t, Const(0.001)), Const(0), pow(2, Const(10) * t - Const(10)))

def ease_out_expo(t):
    t = _to_expr(t)
    return if_(lt(Const(0.999), t), Const(1), Const(1) - pow(2, Const(-10) * t))

def ease_in_out_expo(t):
    t = _to_expr(t)
    branch1 = pow(2, Const(20) * t - Const(10)) / Const(2)
    branch2 = (Const(2) - pow(2, Const(-20) * t + Const(10))) / Const(2)
    inner = if_(lt(t, 0.5), branch1, branch2)
    return if_(lt(t, Const(0.001)), Const(0),
           if_(lt(Const(0.999), t), Const(1), inner))

# Circular (円)
def ease_in_circ(t):
    t = _to_expr(t)
    return Const(1) - sqrt(Const(1) - t * t)

def ease_out_circ(t):
    t = _to_expr(t)
    inv = t - Const(1)
    return sqrt(Const(1) - inv * inv)

def ease_in_out_circ(t):
    t = _to_expr(t)
    t2 = Const(2) * t
    branch1 = (Const(1) - sqrt(clip(Const(1) - t2 * t2, 0, 1))) / Const(2)
    t2m2 = Const(2) * t - Const(2)
    branch2 = (sqrt(clip(Const(1) - t2m2 * t2m2, 0, 1)) + Const(1)) / Const(2)
    return if_(lt(t, 0.5), branch1, branch2)

# Back (オーバーシュート)
def ease_in_back(t):
    t = _to_expr(t)
    return Const(_EASE_C3) * t * t * t - Const(_EASE_C1) * t * t

def ease_out_back(t):
    t = _to_expr(t)
    inv = t - Const(1)
    return Const(1) + Const(_EASE_C3) * inv * inv * inv + Const(_EASE_C1) * inv * inv

def ease_in_out_back(t):
    t = _to_expr(t)
    c2 = _EASE_C1 * 1.525
    t2 = Const(2) * t
    t2m2 = Const(2) * t - Const(2)
    branch1 = (t2 * t2 * (Const(c2 + 1) * t2 - Const(c2))) / Const(2)
    branch2 = (t2m2 * t2m2 * (Const(c2 + 1) * t2m2 + Const(c2)) + Const(2)) / Const(2)
    return if_(lt(t, 0.5), branch1, branch2)

# Elastic (弾性)
def ease_in_elastic(t):
    t = _to_expr(t)
    normal = -pow(2, Const(10) * t - Const(10)) * sin((Const(10) * t - Const(10.75)) * Const(_EASE_C4))
    return if_(lt(t, Const(0.001)), Const(0),
           if_(lt(Const(0.999), t), Const(1), normal))

def ease_out_elastic(t):
    t = _to_expr(t)
    normal = pow(2, Const(-10) * t) * sin((Const(10) * t - Const(0.75)) * Const(_EASE_C4)) + Const(1)
    return if_(lt(t, Const(0.001)), Const(0),
           if_(lt(Const(0.999), t), Const(1), normal))

def ease_in_out_elastic(t):
    t = _to_expr(t)
    branch1 = -pow(2, Const(20) * t - Const(10)) * sin((Const(20) * t - Const(11.125)) * Const(_EASE_C5)) / Const(2)
    branch2 = pow(2, Const(-20) * t + Const(10)) * sin((Const(20) * t - Const(11.125)) * Const(_EASE_C5)) / Const(2) + Const(1)
    inner = if_(lt(t, 0.5), branch1, branch2)
    return if_(lt(t, Const(0.001)), Const(0),
           if_(lt(Const(0.999), t), Const(1), inner))

# Bounce (バウンス)
def ease_out_bounce(t):
    t = _to_expr(t)
    n1 = 7.5625
    d1 = 2.75
    s1 = Const(n1) * t * t
    t2 = t - Const(1.5 / d1)
    s2 = Const(n1) * t2 * t2 + Const(0.75)
    t3 = t - Const(2.25 / d1)
    s3 = Const(n1) * t3 * t3 + Const(0.9375)
    t4 = t - Const(2.625 / d1)
    s4 = Const(n1) * t4 * t4 + Const(0.984375)
    return if_(lt(t, Const(1 / d1)), s1,
           if_(lt(t, Const(2 / d1)), s2,
           if_(lt(t, Const(2.5 / d1)), s3, s4)))

def ease_in_bounce(t):
    t = _to_expr(t)
    return Const(1) - ease_out_bounce(Const(1) - t)

def ease_in_out_bounce(t):
    t = _to_expr(t)
    branch1 = (Const(1) - ease_out_bounce(Const(1) - Const(2) * t)) / Const(2)
    branch2 = (Const(1) + ease_out_bounce(Const(2) * t - Const(1))) / Const(2)
    return if_(lt(t, 0.5), branch1, branch2)

# Cubic Bezier (CSS互換)
def ease_cubic_bezier(x1, y1, x2, y2, segments=16):
    """CSS cubic-bezier互換イージング関数を生成

    使用例:
        ease = ease_cubic_bezier(0.25, 0.1, 0.25, 1.0)  # CSS ease
        obj.time(2) <= scale(lambda u: lerp(0.5, 1, ease(u)))
    """
    def _bx(s): return 3*x1*s*(1-s)**2 + 3*x2*s**2*(1-s) + s**3
    def _by(s): return 3*y1*s*(1-s)**2 + 3*y2*s**2*(1-s) + s**3
    def _dbx(s): return 3*x1 + (6*x2 - 12*x1)*s + (9*x1 - 9*x2 + 3)*s**2
    def _solve_s(t_val):
        if t_val <= 0: return 0.0
        if t_val >= 1: return 1.0
        s = t_val
        for _ in range(8):
            dx = _dbx(s)
            if _builtins.abs(dx) < 1e-12: break
            s -= (_bx(s) - t_val) / dx
            s = _builtins.max(0.0, _builtins.min(1.0, s))
        return s
    points = [(i / segments, _by(_solve_s(i / segments))) for i in range(segments + 1)]
    def _easing(u):
        u = _to_expr(u)
        result = Const(points[-1][1])
        for i in range(len(points) - 2, -1, -1):
            t0, v0 = points[i]
            t1, v1 = points[i + 1]
            seg_len = t1 - t0
            if seg_len <= 0: continue
            local_t = clip((u - Const(t0)) / Const(seg_len), 0, 1)
            seg_val = lerp(v0, v1, local_t)
            result = if_(lt(u, Const(t1)), seg_val, result)
        return result
    return _easing

# スプリング (バネ)
def ease_spring(stiffness=3, damping=4):
    """バネイージング: オーバーシュートしながら1.0に収束"""
    def _easing(t):
        t = _to_expr(t)
        decay = pow(Const(E), Const(-damping) * t)
        osc = cos(t * Const(stiffness) * Const(_EASE_PI))
        return Const(1) - decay * osc
    return _easing

# ステップ関数
def steps(n, jump="end"):
    """CSS steps()互換ステップ関数"""
    if n < 1:
        raise ValueError(f"steps: n は1以上が必要です（{n}）")
    if jump not in ("start", "end"):
        raise ValueError(f"steps: jump は 'start' または 'end': {jump}")
    def _easing(u):
        u = _to_expr(u)
        if jump == "end":
            return clip(floor(u * Const(n)) / Const(n), 0, 1)
        else:
            return clip(floor(u * Const(n) + Const(1)) / Const(n), 0, 1)
    return _easing

def apply_easing(easing_func, from_val, to_val):
    """イージング関数を値範囲に適用するlambdaを返す

    使用例:
        obj.time(2) <= scale(apply_easing(ease_in_quad, 0.5, 1.0))
    """
    def _inner(u):
        return lerp(from_val, to_val, easing_func(u))
    return _inner


# --- シーケンス・キーフレーム ---

def phase(start, end, fn):
    """エフェクトパラメータを時間区間[start, end]にリマッピング

    区間外ではtがclipされる（start前→t=0, end後→t=1）

    使用例:
        obj.time(6) <= fade(phase(0, 0.3, lambda t: t))  # 0-30%でフェードイン
    """
    if start >= end:
        raise ValueError(f"phase: start({start}) < end({end}) が必要です")
    if start < 0 or end > 1:
        raise ValueError("phase: start/end は [0, 1] の範囲が必要です")
    def _inner(u):
        u = _to_expr(u)
        t = clip((u - Const(start)) / Const(end - start), 0, 1)
        if callable(fn):
            return fn(t)
        return _to_expr(fn)
    return _inner

def sequence_param(*segments, default=0):
    """複数の時間区間でパラメータ値を切り替え

    使用例:
        obj.time(6) <= fade(sequence_param(
            (0, 0.2, lambda t: t),      # 0-20%: フェードイン
            (0.2, 0.8, 1.0),            # 20-80%: 保持
            (0.8, 1.0, lambda t: 1-t),  # 80-100%: フェードアウト
        ))
    """
    for i, (s, e, _) in enumerate(segments):
        if s >= e:
            raise ValueError(f"sequence_param: segment[{i}] の start({s}) < end({e}) が必要です")
    def _inner(u):
        u = _to_expr(u)
        result = _to_expr(default)
        for s, e, val in reversed(segments):
            t = clip((u - Const(s)) / Const(e - s), 0, 1)
            segment_val = val(t) if callable(val) else _to_expr(val)
            result = if_(lt(u, Const(e)), segment_val, result)
        return result
    return _inner

def repeat(n, fn):
    """パラメータ関数をn回繰り返す

    使用例:
        obj.time(6) <= scale(repeat(3, lambda t: 1 + 0.2 * sin(t * PI * 2)))
    """
    if n <= 0:
        raise ValueError(f"repeat: n は正の整数が必要です（{n}）")
    def _inner(u):
        u = _to_expr(u)
        local_t = clip(u * Const(n) - floor(u * Const(n)), 0, 1)
        if callable(fn):
            return fn(local_t)
        return _to_expr(fn)
    return _inner

def bounce(n, fn):
    """パラメータ関数をn回往復させる（0→1→0の三角波）

    使用例:
        obj.time(6) <= scale(bounce(2, lambda t: lerp(0.5, 1, t)))
    """
    if n <= 0:
        raise ValueError(f"bounce: n は正の整数が必要です（{n}）")
    def _inner(u):
        u = _to_expr(u)
        local = clip(u * Const(n) - floor(u * Const(n)), 0, 1)
        t = Const(1) - abs(Const(2) * local - Const(1))
        if callable(fn):
            return fn(t)
        return _to_expr(fn)
    return _inner

def alternate(n, fn_a, fn_b):
    """2つの関数をn回交互に切り替え

    使用例:
        obj.time(4) <= fade(alternate(4, lambda t: 1.0, lambda t: 0.5))
    """
    if n <= 0:
        raise ValueError(f"alternate: n は正の整数が必要です（{n}）")
    def _inner(u):
        u = _to_expr(u)
        seg = floor(u * Const(n))
        local_t = clip(u * Const(n) - seg, 0, 1)
        is_even = lt(mod(seg, Const(2)), Const(1))
        val_a = fn_a(local_t) if callable(fn_a) else _to_expr(fn_a)
        val_b = fn_b(local_t) if callable(fn_b) else _to_expr(fn_b)
        return if_(is_even, val_a, val_b)
    return _inner

def staircase(n, fn):
    """階段状に値を上昇させる

    使用例:
        obj.time(3) <= scale(staircase(3, lambda t: lerp(0.5, 1, t)))
    """
    if n <= 0:
        raise ValueError(f"staircase: n は正の整数が必要です（{n}）")
    def _inner(u):
        u = _to_expr(u)
        seg = floor(u * Const(n))
        base = seg / Const(n)
        local_t = clip(u * Const(n) - seg, 0, 1)
        if callable(fn):
            return base + fn(local_t) / Const(n)
        return base + _to_expr(fn) / Const(n)
    return _inner

def keyframes(*args, easing=None):
    """キーフレーム補間: 固定時点のパラメータ指定で自動線形補間

    使用例:
        obj.time(4) <= scale(keyframes(0, 0.5, 0.5, 1.2, 1.0, 1.0))
        obj.time(4) <= fade(keyframes((0, 0), (0.2, 1), (0.8, 1), (1.0, 0)))
        obj.time(4) <= scale(keyframes((0, 0.5), (1, 1.5), easing=ease_in_out_quad))
    """
    if len(args) == 0:
        raise ValueError("keyframes: 最低2つのキーフレームが必要です")
    if isinstance(args[0], tuple):
        points = [(float(t), float(v)) for t, v in args]
    else:
        if len(args) % 2 != 0:
            raise ValueError("keyframes: フラット形式では偶数個の引数が必要です（t0, v0, t1, v1, ...）")
        points = [(float(args[i]), float(args[i+1])) for i in range(0, len(args), 2)]
    if len(points) < 2:
        raise ValueError("keyframes: 最低2つのキーフレームが必要です")
    if len(points) > _MAX_PATH_POINTS:
        raise ValueError(
            f"keyframes: キーフレームは最大{_MAX_PATH_POINTS}点までです（指定={len(points)}）")
    points.sort(key=lambda p: p[0])
    def _inner(u):
        # 区分線形補間の実体は effects/paths の共通実装（生成される Expr は同一）
        return _piecewise_scalar_expr(u, points, easing)
    return _inner
