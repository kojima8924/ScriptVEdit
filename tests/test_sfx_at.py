# -*- coding: utf-8 -*-
"""sfx(source, at=数値1つ) を at=[数値] と同じに扱うことのテスト

describe の例が `sfx('効果音.mp3', at=2.5, volume=0.8)` の形なのに、実装は
リスト以外を ValueError にしていた。数値1つは1要素のリストと**完全に同じ**
（生成コマンド・キャッシュ鍵とも）であることを固定する。
"""
import shutil

import pytest

from scriptvedit import Project, asset, sfx
from scriptvedit.context import _exec_stack, activate, current_project

_HAS_FFPROBE = shutil.which("ffprobe") is not None


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


def _sfx_src():
    try:
        return asset("audio/効果音.mp3")
    except FileNotFoundError:
        pytest.skip("素材 assets/audio/効果音.mp3 が無い環境")


def _dry_run_sfx(tmp_path, at_src, name):
    """レイヤー内で sfx(at=...) を作り、dry_run の sfx 生成コマンドを返す"""
    layer = tmp_path / f"{name}.py"
    layer.write_text(
        "from scriptvedit import *\n"
        f"hit = sfx({_sfx_src()!r}, at={at_src}, volume=0.8)\n"
        "hit.show(3)\n",
        encoding="utf-8")
    p = Project()
    p.configure(width=320, height=180, fps=10)
    p.layer(str(layer), priority=0)
    cmds = p.render(str(tmp_path / f"{name}.mp4"), dry_run=True)
    return {k: v for k, v in cmds["cache"].items() if "/sfx/" in k.replace("\\", "/")}


@pytest.mark.skipif(not _HAS_FFPROBE, reason="ffprobe が無い環境ではスキップ")
@pytest.mark.parametrize("scalar,listed", [
    ("2.5", "[2.5]"),
    ("0", "[0]"),
    ("3", "(3,)"),
])
def test_scalar_at_equals_single_element_list(tmp_path, scalar, listed):
    """at=数値 と at=[数値] は同じキャッシュパス・同じ生成コマンドになる"""
    a = _dry_run_sfx(tmp_path, scalar, "scalar")
    b = _dry_run_sfx(tmp_path, listed, "listed")
    assert len(a) == 1, a
    assert a == b


@pytest.mark.parametrize("bad", [True, "2.5", None, [], -1, float("nan")])
def test_invalid_at_is_rejected(bad):
    """bool / 文字列 / None / 空リスト / 負数 / NaN は ValueError のまま"""
    Project()
    with pytest.raises(ValueError, match="at"):
        sfx(_sfx_src(), at=bad)
