# -*- coding: utf-8 -*-
"""star import後もPython組み込み名の通常用途を壊さないこと。"""

import pytest

import scriptvedit as sv


def test_min_max_keep_builtin_keyword_protocol():
    values = ["long", "x", "mid"]

    assert sv.min(values, key=len) == "x"
    assert sv.max(values, key=len) == "long"
    assert sv.min([], default="empty") == "empty"
    assert sv.max([], default="empty") == "empty"


def test_round_and_pow_keep_builtin_optional_arguments():
    assert sv.round(1.2345, 2) == 1.23
    assert sv.pow(2, 5, 7) == 4


def test_expr_overloads_still_build_ffmpeg_expressions():
    u = sv.Var("u")

    assert sv.min(u, 0.5).to_ffmpeg("T") == "min(T\\,0.5)"
    assert sv.max(u, 0.5).to_ffmpeg("T") == "max(T\\,0.5)"
    assert sv.round(u).to_ffmpeg("T") == "round(T)"
    assert sv.pow(u, 2).to_ffmpeg("T") == "pow(T\\,2)"


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda u: sv.min(u, 1, key=float), "key/default"),
        (lambda u: sv.max(u, 1, default=0), "key/default"),
        (lambda u: sv.round(u, 2), "ndigits"),
        (lambda u: sv.pow(u, 2, 3), "3引数"),
    ],
)
def test_expr_overloads_reject_builtin_only_options(call, message):
    with pytest.raises(TypeError, match=message):
        call(sv.Var("u"))
