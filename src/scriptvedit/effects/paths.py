# -*- coding: utf-8 -*-

import math as _math

from scriptvedit.expr import Expr

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.expr import Const, Var, _to_expr, clip, exp, if_, lerp, lt, sin
from scriptvedit.objects import Effect
from scriptvedit.validate import _validate_ffmpeg_color


# --- パス・物理・ノイズ（move系Effect / Expr） ---

# パス/キーフレームの区分式で許容する最大点数（ネスト式の肥大化を防ぐ）
_MAX_PATH_POINTS = 128


def _piecewise_tree(u, values, bounds):
    """区分式を二分探索木で構築（ネスト深度 O(log n)、MEMORY規約準拠）。

    values[i] は境界で区切られた各区間の値 Expr（len(values)==len(bounds)+1）。
    bounds は昇順の分割点。u < bounds[mid] で左右に再帰分岐する。
    線形右畳み込み（O(n)ネスト）を避け、深い if_ ネストによる式肥大を防ぐ。
    """
    u = _to_expr(u)

    def build(vals, bnds):
        if not bnds:
            return vals[0]
        mid = len(bnds) // 2
        left = build(vals[:mid + 1], bnds[:mid])
        right = build(vals[mid + 1:], bnds[mid + 1:])
        return if_(lt(u, Const(bnds[mid])), left, right)

    return build(list(values), list(bounds))


def _piecewise_scalar_expr(u, points, easing=None):
    """(t, v) の点列から u∈[0,1] 上の区分線形補間 Expr を構築（keyframes同型）

    points は t 昇順。区間ごとに lerp し、二分探索木で連結する。easing 指定時は
    各区間のローカル進行度に適用する。
    """
    u = _to_expr(u)
    values = []
    bounds = []
    for i in range(len(points) - 1):
        t0, v0 = points[i]
        t1, v1 = points[i + 1]
        seg = t1 - t0
        if seg <= 0:
            continue
        local = clip((u - Const(t0)) / Const(seg), 0, 1)
        if easing is not None:
            local = easing(local)
        values.append(lerp(v0, v1, local))
        bounds.append(t1)
    values.append(Const(points[-1][1]))  # u>=最後の境界の既定値
    return _piecewise_tree(u, values, bounds)


def _validate_points(func, points, min_n=2):
    if not isinstance(points, (list, tuple)) or len(points) < min_n:
        raise ValueError(f"{func}: 座標列は最低{min_n}点必要です")
    for pt in points:
        if not isinstance(pt, (list, tuple)) or len(pt) != 2:
            raise ValueError(f"{func}: 各点は (x, y) の2要素タプルが必要です: {pt!r}")


def move_along(points, *, easing=None, anchor="center"):
    """座標列 [(x0,y0), ...] に沿ったパスアニメーションの move Effect。

    各点は u を等間隔に割り当てて区分線形補間する（x/y それぞれに適用）。
    x/y は画面比率（0..1）。move の拡張なので既存の overlay 経路で描画される。
    """
    _validate_points("move_along", points)
    n = len(points)
    if n > _MAX_PATH_POINTS:
        raise ValueError(
            f"move_along: 点数は最大{_MAX_PATH_POINTS}点までです（指定={n}）")
    ts = [i / (n - 1) for i in range(n)]
    x_pts = [(ts[i], float(points[i][0])) for i in range(n)]
    y_pts = [(ts[i], float(points[i][1])) for i in range(n)]
    x_expr = _piecewise_scalar_expr(Var("u"), x_pts, easing)
    y_expr = _piecewise_scalar_expr(Var("u"), y_pts, easing)
    eff = Effect("move", x=x_expr, y=y_expr, anchor=anchor)
    eff._path_xy = (x_expr, y_expr)  # look_at 用にパスを保持
    return eff


def _bezier_segment_expr(s, p0, p1, p2, p3):
    """3次ベジェの1座標式（s∈[0,1] は Expr）"""
    s = _to_expr(s)
    one = Const(1) - s
    return (Const(p0) * one * one * one
            + Const(3 * p1) * s * one * one
            + Const(3 * p2) * s * s * one
            + Const(p3) * s * s * s)


def path_bezier(*points, anchor="center"):
    """3次ベジェ曲線パスの move Effect。

    制御点は (x,y) を 3n+1 個（p0,p1,p2,p3[,p4,p5,p6...]）。各セグメントは
    直前セグメントの終点を始点に共有する。cubic_bezier イージングと同系の資産。
    """
    _validate_points("path_bezier", points, min_n=4)
    n = len(points)
    if n > _MAX_PATH_POINTS:
        raise ValueError(
            f"path_bezier: 制御点は最大{_MAX_PATH_POINTS}点までです（指定={n}）")
    if (n - 1) % 3 != 0:
        raise ValueError(
            f"path_bezier: 制御点は 3n+1 個必要です（p0..p3, +3点ずつ）。指定={n}")
    seg_count = (n - 1) // 3
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    # if_ 連結は右畳み込みのため、区間逆順で構築（_bezier_path_coord内で処理）
    x_expr = _bezier_path_coord_rev(xs, seg_count)
    y_expr = _bezier_path_coord_rev(ys, seg_count)
    eff = Effect("move", x=x_expr, y=y_expr, anchor=anchor)
    eff._path_xy = (x_expr, y_expr)
    return eff


def _bezier_path_coord_rev(ctrl, seg_count):
    """複数セグメントを二分探索木で連結（ネスト深度 O(log n)）。

    最終セグメント(u>=(seg_count-1)/seg_count)が既定区間となる。
    """
    u = Var("u")
    values = []
    bounds = []
    for k in range(seg_count):
        i0 = 3 * k
        p0, p1, p2, p3 = ctrl[i0], ctrl[i0 + 1], ctrl[i0 + 2], ctrl[i0 + 3]
        t0 = k / seg_count
        t1 = (k + 1) / seg_count
        s = clip((_to_expr(u) - Const(t0)) / Const(t1 - t0), 0, 1)
        values.append(_bezier_segment_expr(s, p0, p1, p2, p3))
        if k < seg_count - 1:
            bounds.append(t1)
    return _piecewise_tree(u, values, bounds)


def throw(vx, vy, *, gravity=1.0, x0=0.5, y0=0.5, anchor="center"):
    """物理ベース（初速+重力）の放物運動 move Effect。

    位置は画面比率。u を正規化時間として
      x(u) = x0 + vx*u,  y(u) = y0 + vy*u + 0.5*gravity*u^2
    （+y が下方向）。move に統合されるため既存 overlay 経路で描画される。
    """
    u = Var("u")
    x_expr = Const(x0) + Const(vx) * u
    y_expr = Const(y0) + Const(vy) * u + Const(0.5 * gravity) * u * u
    eff = Effect("move", x=x_expr, y=y_expr, anchor=anchor)
    eff._path_xy = (x_expr, y_expr)
    return eff


def inertia(vx, vy, *, damping=3.0, x0=0.5, y0=0.5, anchor="center"):
    """初速から指数減衰する慣性移動 move Effect（滑らかな減速）。

    x(u) = x0 + vx*(1-exp(-damping*u))/damping。damping が大きいほど早く止まる。
    """
    if damping <= 0:
        raise ValueError("inertia: damping は正の値が必要です")
    u = Var("u")
    decay = (Const(1) - exp(Const(-damping) * u)) / Const(damping)
    x_expr = Const(x0) + Const(vx) * decay
    y_expr = Const(y0) + Const(vy) * decay
    eff = Effect("move", x=x_expr, y=y_expr, anchor=anchor)
    eff._path_xy = (x_expr, y_expr)
    return eff


class _LookAtExpr(Expr):
    """パス(x_expr, y_expr)の進行方向角(rad)を有限差分で求めるExpr。

    to_ffmpeg では u を ±du ずらした座標式を atan2 に渡す。x はW比率、
    y はH比率だが、アスペクト補正は行わず比率空間での方向を返す（近似）。
    """
    def __init__(self, x_expr, y_expr, offset_rad=0.0, du=1e-3):
        self.x_expr = x_expr
        self.y_expr = y_expr
        self.offset_rad = offset_rad
        self.du = du

    def to_ffmpeg(self, u_expr):
        up = f"(({u_expr})+{self.du})"
        um = f"(({u_expr})-{self.du})"
        dx = f"(({self.x_expr.to_ffmpeg(up)})-({self.x_expr.to_ffmpeg(um)}))"
        dy = f"(({self.y_expr.to_ffmpeg(up)})-({self.y_expr.to_ffmpeg(um)}))"
        return f"(atan2({dy}\\,{dx})+{self.offset_rad})"

    def eval_at(self, u_value):
        du = self.du
        dx = self.x_expr.eval_at(u_value + du) - self.x_expr.eval_at(u_value - du)
        dy = self.y_expr.eval_at(u_value + du) - self.y_expr.eval_at(u_value - du)
        return _math.atan2(dy, dx) + self.offset_rad


def look_at(path, *, offset_deg=0.0, expand=True, fill="0x00000000"):
    """パスの進行方向を向く回転Effect（rotate_to のパス追従版）。

    path: move系Effect（move_along/path_bezier/throw/inertia）または
      (x_expr, y_expr) のタプル。パスの微分(有限差分)から角度を求める。
    """
    fill = _validate_ffmpeg_color("look_at", fill)
    if isinstance(path, Effect) and hasattr(path, "_path_xy"):
        x_expr, y_expr = path._path_xy
    elif isinstance(path, (tuple, list)) and len(path) == 2:
        x_expr, y_expr = _to_expr(path[0]), _to_expr(path[1])
    else:
        raise TypeError(
            "look_at: path は move系Effect か (x_expr, y_expr) を渡してください")
    offset_rad = offset_deg * _math.pi / 180.0
    rad_expr = _LookAtExpr(x_expr, y_expr, offset_rad)
    return Effect("rotate_to", rad=rad_expr, expand=expand, fill=fill)


def perlin(u, *, octaves=2, seed=0, frequency=1.0, amplitude=1.0):
    """sin合成による滑らかな擬似ノイズ Expr（手ブレカメラ等に使用）。

    複数オクターブの sin を重ね合わせ、u∈[0,1] で滑らかに変動する値を返す。
    出力はおおよそ [-amplitude, amplitude] に収まる。shake Effect が
    規則的な正弦振動なのに対し、perlin は非整数周波数の重ね合わせで
    不規則で自然な揺れを作る（値式なので move/rotate_to 等に渡せる）。
    """
    if octaves < 1:
        raise ValueError("perlin: octaves は1以上が必要です")
    u = _to_expr(u)
    rng = __import__("random").Random(seed)
    total = None
    norm = 0.0
    for k in range(octaves):
        freq = frequency * (2 ** k) * (1 + 0.13 * (k + 1))  # 非整数比で周期性を崩す
        phase = rng.uniform(0, 2 * _math.pi)
        amp = 0.5 ** k
        norm += amp
        wave = sin(u * Const(2 * _math.pi * freq) + Const(phase)) * Const(amp)
        total = wave if total is None else total + wave
    return total * Const(amplitude / norm)
