# -*- coding: utf-8 -*-
"""Expr の定数畳み込みが ffmpeg (libavutil/eval.c) の意味論と一致することの回帰テスト。

畳み込みは「同じ式を Python で先に計算する」最適化なので、Python 側の意味論が
ffmpeg とずれていると **同じ関数が定数か動的かで結果が変わる**。
round がその代表（C は絶対値方向、Python は偶数丸め）。監査 項目22(a)。
"""
import shutil
import subprocess

import pytest

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
