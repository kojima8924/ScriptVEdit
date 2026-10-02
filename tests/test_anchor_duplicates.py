# -*- coding: utf-8 -*-
"""同じレイヤー内で同名アンカーを2回定義したときの明示エラー。

以前は anchor('x') を同じレイヤーで2回書くと、固定点反復が2つの時刻の間で
振動して「タイムライン解決が収束しませんでした」という原因の分からない
RuntimeError になっていた（位置が偶然同じなら黙って通る）。
定義した時点で「アンカー名 'x' はこのレイヤーで既に定義されています（…行目）」の
ValueError にする。別レイヤー間の既存のエラー（RuntimeError）は変えない。
"""
import pytest

import scriptvedit as sv
from scriptvedit.context import _exec_stack, activate, current_project


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


_IMG = "Object(asset('images/shape_badge.png'))"


def _render(tmp_path, *layers):
    p = sv.Project()
    p.configure(width=160, height=90, fps=10)
    for i, body in enumerate(layers):
        path = tmp_path / f"layer{i}.py"
        path.write_text("from scriptvedit import *\n" + body, encoding="utf-8")
        p.layer(str(path), priority=i)
    return p.render(str(tmp_path / "o.mp4"), dry_run=True)


def test_anchor_twice_in_same_layer(tmp_path):
    body = (f"{_IMG}.time(1)\n"      # 2行目
            "anchor('x')\n"          # 3行目
            f"{_IMG}.time(1)\n"      # 4行目
            "anchor('x')\n")         # 5行目
    with pytest.raises(ValueError) as ei:
        _render(tmp_path, body)
    msg = str(ei.value)
    assert "アンカー名 'x' はこのレイヤーで既に定義されています（3行目" in msg
    assert "5行目" in msg
    assert "収束" not in msg


def test_time_name_twice_in_same_layer(tmp_path):
    body = (f"{_IMG}.time(1, name='x')\n"    # 2行目
            f"{_IMG}.time(1, name='x')\n")   # 3行目
    with pytest.raises(ValueError) as ei:
        _render(tmp_path, body)
    msg = str(ei.value)
    assert "アンカー名 'x' はこのレイヤーで既に定義されています（2行目" in msg
    assert "3行目の time(name='x')" in msg


@pytest.mark.parametrize("body,first_line,key", [
    # anchor('x.start') の後に time(name='x')（'x.start' を作る）
    ("anchor('x.start')\n" + f"{_IMG}.time(1, name='x')\n", 2, "x.start"),
    # time(name='x') の後に anchor('x.end')
    (f"{_IMG}.time(1, name='x')\n" + "anchor('x.end')\n", 2, "x.end"),
])
def test_anchor_and_time_name_mixed(tmp_path, body, first_line, key):
    with pytest.raises(ValueError) as ei:
        _render(tmp_path, body)
    msg = str(ei.value)
    assert f"アンカー名 '{key}' はこのレイヤーで既に定義されています（{first_line}行目" in msg


def test_scene_name_twice_in_same_layer(tmp_path):
    body = ("with scene('s', 1):\n"
            f"    {_IMG}.time(1)\n"
            "with scene('s', 1):\n"
            f"    {_IMG}.time(1)\n")
    with pytest.raises(ValueError, match="'scene:s' はこのレイヤーで既に定義されています"):
        _render(tmp_path, body)


def test_non_conflicting_definitions_still_work(tmp_path):
    """重複ではない書き方は通る（Plan/Render の2回実行も重複扱いしない）"""
    body = (
        # anchor('x') と time(name='x')（'x.start'/'x.end'）はキーが違う
        "anchor('x')\n"
        f"{_IMG}.time(1, name='x')\n"
        # 同じ Object の time(name=) のやり直しは再定義ではない
        f"o = {_IMG}\n"
        "o.time(1, name='y')\n"
        "o.time(2, name='y')\n"
        # 名前の付け直し: 旧名 'z' は解放され、後から使える
        f"q = {_IMG}\n"
        "q.time(1, name='z')\n"
        "q.time(1, name='w')\n"
        "anchor('z.start')\n")
    result = _render(tmp_path, body)
    assert result["main"]


def test_cross_layer_duplicate_error_unchanged(tmp_path):
    """別レイヤー間の同名定義は従来どおり RuntimeError（メッセージも従来のまま）"""
    with pytest.raises(RuntimeError, match="は既に .* で定義されています"):
        _render(tmp_path, "anchor('x')\n", "anchor('x')\n")


def test_duplicate_detected_in_plan_pass_before_convergence(tmp_path):
    """重複はレイヤー実行中（定義した時点）で止まり、解決まで進まない"""
    body = ("anchor('x')\n"
            f"{_IMG}.time(1)\n"
            "anchor('x')\n"
            "raise SystemExit('ここまで来てはいけない')\n")
    with pytest.raises(ValueError, match="既に定義されています"):
        _render(tmp_path, body)
