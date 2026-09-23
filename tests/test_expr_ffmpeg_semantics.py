# -*- coding: utf-8 -*-
"""Expr の定数畳み込みが ffmpeg (libavutil/eval.c) の意味論と一致することの回帰テスト。

畳み込みは「同じ式を Python で先に計算する」最適化なので、Python 側の意味論が
ffmpeg とずれていると **同じ関数が定数か動的かで結果が変わる**。
round がその代表（C は絶対値方向、Python は偶数丸め）。監査 項目22(a)。
"""
import math
import shutil
import subprocess

import pytest

import scriptvedit as sv
from scriptvedit import Var, round as sv_round
from scriptvedit.expr import Const, _FuncCall


def _fold(x):
    """定数を畳み込んだ結果の値（畳み込まれなければテスト失敗）"""
    r = sv_round(Const(x))
    assert isinstance(r, Const), f"round({x}) が定数畳み込みされていない: {r!r}"
    return r.value


@pytest.mark.parametrize("value,expected", [
    (2.5, 3),     # Python の round は偶数丸めで 2 になる（ffmpeg は 3）
    (3.5, 4),
    (0.5, 1),     # 同上（Python は 0）
    (-0.5, -1),   # 同上（Python は 0）
    (-2.5, -3),   # 同上（Python は -2）
    (1.4, 1),
    (-1.4, -1),
    (1.6, 2),
    (-1.6, -2),
    (2.0, 2),
])
def test_round_folds_with_ffmpeg_semantics(value, expected):
    assert _fold(value) == expected


def test_round_eval_at_matches_folding():
    """動的な式（eval_at）と定数畳み込みが同じ丸め規則を使う"""
    u = Var("u")
    dynamic = sv_round(u * 5)  # 畳み込まれない
    assert not isinstance(dynamic, Const)
    for uv, expected in ((0.1, 1), (0.3, 2), (0.5, 3), (0.7, 4)):
        assert dynamic.eval_at(uv) == expected
        assert _fold(uv * 5) == expected


def _ffmpeg_eval_luma(expr):
    """ffmpeg の式評価器で expr を実際に評価し、1画素の輝度値として取り出す。

    format=gray を挟んでから geq で書くので、レンジ変換を経ずに式の値が
    そのまま画素値になる（実測で確認）。
    """
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=black:s=2x2:d=1:r=1",
         "-vf", f"format=gray,geq=lum='{expr}'",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return out.stdout[0]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が必要")
@pytest.mark.parametrize("expr,folded,pixel", [
    ("round(2.5)*50", 2.5, 150),
    ("round(0.5)*50", 0.5, 50),
    ("round(-2.5)*(-50)", -2.5, 150),
])
def test_round_folding_matches_real_ffmpeg(expr, folded, pixel):
    """畳み込み結果と、実際に ffmpeg へ通した1画素の値が一致する"""
    assert _ffmpeg_eval_luma(expr) == pixel
    assert _fold(folded) * (50 if folded > 0 else -50) == pixel


def test_eval_funcs_only_contains_ffmpeg_functions():
    """ffmpeg に存在しない名前を評価表へ置かない（_make_func してよいと誤解される）"""
    funcs = _FuncCall._get_eval_funcs()
    for absent in ("sign", "log10", "cbrt"):
        assert absent not in funcs, (
            f"'{absent}' は ffmpeg の式評価器に無い関数名。"
            f"公開の {absent}() は if/gt・log・pow の組み合わせへ展開される")


def test_sign_log10_cbrt_do_not_emit_bare_ffmpeg_calls():
    """公開ファクトリが ffmpeg 非対応の関数名をそのまま出力しないこと"""
    from scriptvedit import cbrt, log10, sign
    u = Var("u")
    for fn, name in ((sign, "sign"), (log10, "log10"), (cbrt, "cbrt")):
        out = fn(u).to_ffmpeg("T")
        assert f"{name}(" not in out, f"{name}() が ffmpeg 式へ素通ししている: {out}"


# --- NaN / ±Infinity を式へ入れない ---------------------------------------
# ffmpeg 側の挙動は実測で割れている:
#   inf … scale の eval=frame 式では通ってしまい、意図しない絵になる
#   nan … "Error when evaluating the expression" → "Failed to configure
#         output pad"。geq の alpha に入ると
#         "A luminance or RGB expression is mandatory" になり原因が分からない
# どちらも「利用者が意図して書く値」ではないので構築時に拒否する。


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_const_rejects_non_finite(value):
    with pytest.raises(ValueError, match="NaN/Infinity"):
        Const(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_effect_param_rejects_non_finite_constant(value):
    """定数パラメータ（_resolve_param の float 経路）"""
    with pytest.raises(ValueError, match="NaN/Infinity"):
        sv.scale(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_effect_param_rejects_non_finite_from_lambda(value):
    """lambda の戻り値経路（_resolve_param → _to_expr → Const）"""
    with pytest.raises(ValueError, match="NaN/Infinity"):
        sv.fade(lambda u: value)


def test_constant_folding_rejects_overflow_to_infinity():
    """定数伝播が overflow したら黙って inf を埋めずに止まる"""
    with pytest.raises(ValueError, match="NaN/Infinity"):
        Const(1e308) + Const(1e308)


def test_constant_folding_keeps_node_when_python_cannot_evaluate():
    """Python が計算を拒むだけの式（ffmpeg は走る）は畳み込みを諦めるだけ"""
    e = sv.sqrt(Const(-1.0))       # Python: math domain error / ffmpeg: nan
    assert not isinstance(e, Const)
    assert e.to_ffmpeg("T") == "sqrt(-1.0)"


# --- lambda 内の TypeError を原因別に言い換える ----------------------------


@pytest.mark.parametrize("build,op,alternative", [
    (lambda u: u < 0.5, "<", "lt(a, b)"),
    (lambda u: u >= 0.5, ">=", "gte(a, b)"),
    (lambda u: u % 2, "%", "mod(a, b)"),
    (lambda u: u // 2, "//", "floor(a / b)"),
    (lambda u: u & 1, "&", "and_(a, b)"),
    (lambda u: ~u, "~", "not_(a)"),
])
def test_unsupported_operator_message_names_the_operator(build, op, alternative):
    """`u < 0.5` を「math関数は使えません」に丸めない（原因が特定できないため）"""
    with pytest.raises(TypeError) as excinfo:
        sv.scale(build)
    text = str(excinfo.value)
    assert f"演算子 '{op}'" in text
    assert alternative in text
    assert "math関数" not in text
    # 元の例外情報を落とさない
    assert "元のエラー:" in text
    assert excinfo.value.__cause__ is not None


def test_math_function_message_is_kept_for_math_type_errors():
    """math.sin(u) は従来どおり「scriptvedit の関数を使え」と案内する"""
    with pytest.raises(TypeError, match="math関数は使えません") as excinfo:
        sv.scale(lambda u: math.sin(u))
    assert "元のエラー:" in str(excinfo.value)


# --- easing の定義域 -------------------------------------------------------


@pytest.mark.parametrize("name", ["ease_in_circ", "ease_out_circ", "ease_in_out_circ"])
@pytest.mark.parametrize("t", [-0.5, -0.001, 0.0, 0.5, 1.0, 1.2, 3.0])
def test_circ_easings_stay_finite_outside_unit_range(name, t):
    """circ 系は sqrt の定義域外でも落ちない（3本で挙動を揃えてある）"""
    value = getattr(sv, name)(Var("u")).eval_at(t)
    assert math.isfinite(value)


def test_ease_spring_hits_both_endpoints_exactly():
    """終端が 1.0 を超えたまま終わると alpha/scale が規定値を僅かに外れる"""
    spring = sv.ease_spring()(Var("u"))
    assert spring.eval_at(0.0) == pytest.approx(0.0, abs=1e-12)
    assert spring.eval_at(1.0) == pytest.approx(1.0, abs=1e-12)
    # 途中のオーバーシュートは仕様なので残る
    assert spring.eval_at(0.33) > 1.1


@pytest.mark.parametrize("kwargs", [
    {"damping": float("nan")},
    {"stiffness": float("inf")},
    {"damping": -10000},     # exp(10000) が overflow する
])
def test_ease_spring_rejects_unusable_params(kwargs):
    with pytest.raises(ValueError, match="有限の数値"):
        sv.ease_spring(**kwargs)
