"""move の片軸補間と別軸の座標式を併用する回帰テスト。"""

import pytest

from scriptvedit import move
from scriptvedit.expr import Var


@pytest.mark.parametrize("curve_axis, linear_axis", [("x", "y"), ("y", "x")])
@pytest.mark.parametrize("endpoint", ["from", "to", "both"])
@pytest.mark.parametrize("as_expr", [False, True], ids=["lambda", "expr"])
def test_move_curve_and_other_axis_interpolation(curve_axis, linear_axis,
                                                 endpoint, as_expr):
    """from/to の無い側の lambda/Expr は、そのままの曲線を保つ。"""
    curve = 0.3 + 0.2 * Var("u") if as_expr else lambda u: 0.3 + 0.2 * u
    kwargs = {curve_axis: curve}
    if endpoint in ("from", "both"):
        kwargs[f"from_{linear_axis}"] = 0.0
    if endpoint in ("to", "both"):
        kwargs[f"to_{linear_axis}"] = 1.0
    effect = move(**kwargs)
    start = 0.0 if endpoint in ("from", "both") else 0.5
    end = 1.0 if endpoint in ("to", "both") else 0.5
    for u in (0.0, 0.25, 0.5, 1.0):
        assert effect.params[curve_axis].eval_at(u) == pytest.approx(0.3 + 0.2 * u)
        assert effect.params[linear_axis].eval_at(u) == pytest.approx(
            start + (end - start) * u)


@pytest.mark.parametrize("axis", ["x", "y"])
@pytest.mark.parametrize("endpoint", ["from", "to"])
def test_move_curve_fills_missing_endpoint(axis, endpoint):
    """同じ軸の欠けた端点にも、解決済みの座標式を使える。"""
    effect = move(**{axis: lambda u: 0.3 + 0.2 * u, f"{endpoint}_{axis}": 0.8})
    for u in (0.0, 0.25, 0.5, 1.0):
        curve = 0.3 + 0.2 * u
        start, end = (0.8, curve) if endpoint == "from" else (curve, 0.8)
        assert effect.params[axis].eval_at(u) == pytest.approx(start + (end - start) * u)
    other_axis = "y" if axis == "x" else "x"
    assert effect.params[other_axis].eval_at(0.5) == 0.5
