# -*- coding: utf-8 -*-
"""gitignore 対象の素材に依存するテストが、素材の無い環境で skip されること。

fresh clone や CI は `assets/` の大容量素材（.gitignore 済み）を持たない。
その環境でテストが「失敗」すると、本物の退行と区別できずゲートが機能しなくなる。
逆に、素材を使うのに宣言しないと skip されず落ちる。

このテストは `tests/projects.py` の ProjectSpec について
「レイヤーが参照する gitignore 対象の素材」と「spec の assets 宣言」が
一致していることを機械的に固定する。宣言漏れは実際に CI を落としたことがある。
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from projects import PROJECTS  # noqa: E402

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_TESTS_DIR)
_ASSET_CALL = re.compile(r'asset\(\s*["\']([^"\']+)["\']')


def _untracked_assets():
    """assets/ 配下で git 管理外＝gitignore 対象の素材（相対パス）を返す。"""
    tracked = set(subprocess.run(
        ["git", "ls-files", "assets"], cwd=_ROOT,
        capture_output=True).stdout.decode("utf-8").split())
    out = set()
    for root, _, files in os.walk(os.path.join(_ROOT, "assets")):
        for name in files:
            rel = os.path.relpath(
                os.path.join(root, name), _ROOT).replace(os.sep, "/")
            if rel not in tracked:
                out.add(rel[len("assets/"):])
    return out


def _layer_names(spec):
    return [lay[0] if isinstance(lay, tuple) else lay for lay in spec.layers]


def test_specs_declare_the_gitignored_assets_they_use():
    """レイヤーが使う gitignore 対象素材は spec の assets= に宣言されていること"""
    big = _untracked_assets()
    if not big:
        # 素材が1つも欠けていない環境（＝全部 tracked）では検査対象が無い
        return
    missing = []
    for name, spec in PROJECTS.items():
        used = set()
        for layer in _layer_names(spec):
            path = os.path.join(_TESTS_DIR, "layers", layer)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    used |= set(_ASSET_CALL.findall(f.read()))
        need = {u for u in used if u in big} - set(spec.assets)
        if need:
            missing.append(f"{name}: assets={sorted(need)} を宣言してください")
    assert not missing, (
        "gitignore 対象素材の宣言漏れ（素材の無い環境で skip されず落ちます）:\n"
        + "\n".join(missing))
