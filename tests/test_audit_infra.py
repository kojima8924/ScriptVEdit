# -*- coding: utf-8 -*-
"""監査issue #16/#17 のインフラ系回帰テスト

- #16: cache確定(os.replace)失敗のfail-closed化
- #16: プラグイン部分登録のロールバックとautoload再試行
- #17: FFmpegメジャーバージョン検証
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import scriptvedit.ffmpeg as ffmpeg_mod  # noqa: E402
from scriptvedit.ffmpeg import _run_ffmpeg_to_cache  # noqa: E402
from scriptvedit.plugins import (  # noqa: E402
    _AUTOLOADED_PLUGIN_DIRS, _EFFECT_PLUGINS, PluginError, _autoload_plugins,
    load_plugin,
)
from scriptvedit.state import _BAKEABLE_EFFECTS  # noqa: E402


# --- #16: os.replace 失敗の扱い ---------------------------------------------

def _fake_ffmpeg_writer(monkeypatch):
    """_run_ffmpeg を「出力パスへ書くだけ」の偽物に差し替える"""
    def fake_run(cmd, timeout=600, context=None):
        out = cmd[-1]
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "wb") as f:
            f.write(b"new-content")
    monkeypatch.setattr(ffmpeg_mod, "_run_ffmpeg", fake_run)


def test_replace_failure_with_stale_dest_raises(tmp_path, monkeypatch):
    """古いキャッシュが残ったままreplace失敗 → 成功扱いにせずエラー"""
    _fake_ffmpeg_writer(monkeypatch)
    dest = tmp_path / "c.mkv"
    dest.write_bytes(b"OLD")           # 実行前から存在する古いキャッシュ

    real_replace = os.replace
    def deny_replace(src, dst):
        if str(dst) == str(dest):
            raise PermissionError("locked")
        return real_replace(src, dst)
    monkeypatch.setattr(os, "replace", deny_replace)

    with pytest.raises(RuntimeError, match="確定.*失敗"):
        _run_ffmpeg_to_cache(["ffmpeg", "-y", str(dest)], str(dest))
    assert dest.read_bytes() == b"OLD"  # 旧内容が「成功」を装って使われない


def test_replace_failure_after_concurrent_win_is_tolerated(tmp_path, monkeypatch):
    """実行中に他ワーカが同一鍵を確定した場合(宛先が変化)は譲歩して成功"""
    _fake_ffmpeg_writer(monkeypatch)
    dest = tmp_path / "c.mkv"          # 実行前は存在しない

    real_replace = os.replace
    def racing_replace(src, dst):
        if str(dst) == str(dest):
            # 他ワーカが先に確定した状況を再現してから失敗する
            with open(dest, "wb") as f:
                f.write(b"OTHER-WORKER")
            raise PermissionError("sharing violation")
        return real_replace(src, dst)
    monkeypatch.setattr(os, "replace", racing_replace)

    _run_ffmpeg_to_cache(["ffmpeg", "-y", str(dest)], str(dest))  # 例外にならない
    assert dest.read_bytes() == b"OTHER-WORKER"


def test_replace_success_normal_path(tmp_path, monkeypatch):
    """通常経路: 生成→replace成功→新内容"""
    _fake_ffmpeg_writer(monkeypatch)
    dest = tmp_path / "c.mkv"
    _run_ffmpeg_to_cache(["ffmpeg", "-y", str(dest)], str(dest))
    assert dest.read_bytes() == b"new-content"


# --- #16: プラグイン部分登録のロールバック ----------------------------------

_BROKEN_PLUGIN = '''
from scriptvedit import effect_plugin

@effect_plugin("half_loaded_audit", bakeable=True,
               params={"x": {"type": "number", "default": 1}})
def build(params, ctx):
    """登録後に例外を起こす壊れたプラグイン"""
    return ["hflip"]

raise RuntimeError("after registration")
'''


def test_broken_plugin_rolls_back_registry(tmp_path):
    """登録後に例外が起きたファイルは、レジストリ・名前空間に何も残さない"""
    import scriptvedit
    plug = tmp_path / "broken_plugin.py"
    plug.write_text(_BROKEN_PLUGIN, encoding="utf-8")

    assert "half_loaded_audit" not in _EFFECT_PLUGINS
    with pytest.raises(PluginError):
        load_plugin(str(plug))
    assert "half_loaded_audit" not in _EFFECT_PLUGINS
    assert "half_loaded_audit" not in _BAKEABLE_EFFECTS
    assert not hasattr(scriptvedit, "half_loaded_audit")
    assert "half_loaded_audit" not in scriptvedit.__all__


def test_autoload_retries_dir_after_failure(tmp_path):
    """失敗ファイルがあったディレクトリは、修正後の再autoloadで読み込める"""
    pdir = tmp_path / "plugins"
    pdir.mkdir()
    plug = pdir / "fixable.py"
    plug.write_text(_BROKEN_PLUGIN.replace("half_loaded_audit",
                                           "fixable_audit"), encoding="utf-8")

    with pytest.warns(UserWarning, match="スキップ"):
        _autoload_plugins(str(tmp_path))
    key = str(pdir).replace("\\", "/")
    assert key not in _AUTOLOADED_PLUGIN_DIRS  # 失敗ディレクトリは再試行可能

    # 修正して再autoload → 読み込める
    plug.write_text(
        "from scriptvedit import effect_plugin\n"
        "@effect_plugin('fixable_audit', bakeable=True,\n"
        "               params={'x': {'type': 'number', 'default': 1}})\n"
        "def build(params, ctx):\n"
        "    \"\"\"修正済み\"\"\"\n"
        "    return ['hflip']\n",
        encoding="utf-8")
    loaded = _autoload_plugins(str(tmp_path))
    try:
        assert loaded and "fixable_audit" in _EFFECT_PLUGINS
    finally:
        from scriptvedit import unregister_plugin
        unregister_plugin("fixable_audit")
        _AUTOLOADED_PLUGIN_DIRS.discard(key)


# --- #17: FFmpeg メジャーバージョン検証 -------------------------------------

def test_old_ffmpeg_version_rejected(monkeypatch):
    """FFmpeg 7以下は日本語エラー(1回だけ検証)"""
    import subprocess

    monkeypatch.setattr(ffmpeg_mod, "_FFMPEG_VERSION_CHECKED", [False])
    class R:
        stdout = "ffmpeg version 6.1.1 Copyright..."
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    with pytest.raises(RuntimeError, match="FFmpeg 8"):
        ffmpeg_mod._check_ffmpeg_version()


def test_dev_build_version_accepted(monkeypatch):
    """数値で始まらない開発ビルド(N-xxxxx)は検証を通す"""
    import subprocess

    monkeypatch.setattr(ffmpeg_mod, "_FFMPEG_VERSION_CHECKED", [False])
    class R:
        stdout = "ffmpeg version N-125705-gc23123630e Copyright..."
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    ffmpeg_mod._check_ffmpeg_version()  # 例外なし


# --- #16: キャッシュ鍵のProject解像度とrenderer identity ---------------------

def test_checkpoint_key_includes_project_resolution():
    """同一素材・同一opでもProject解像度が違えばcheckpointパスが分かれる"""
    from scriptvedit import Project, asset, resize
    from scriptvedit.cache import _checkpoint_cache_path
    from scriptvedit.objects import Transform

    src = asset("images/shape_badge.png")
    ops = [("transform", Transform("resize", sx=0.5, sy=0.5))]
    p1 = Project(); p1.configure(width=320, height=180, fps=30)
    path_320 = _checkpoint_cache_path(src, ops)
    p2 = Project(); p2.configure(width=1920, height=1080, fps=30)
    path_1080 = _checkpoint_cache_path(src, ops)
    assert path_320 != path_1080, "解像度が違うのに同一checkpointパス"


def test_web_and_formula_keys_include_renderer(monkeypatch):
    """renderer identityが変わればweb/formulaの鍵も変わる"""
    import scriptvedit.cache as cache_mod
    from importlib import import_module
    formula_mod = import_module("scriptvedit.formula")

    monkeypatch.setattr(cache_mod, "_RENDERER_IDENTITY_MEMO", ["playwright-1.0"])
    fp_a = formula_mod._katex_fingerprint()
    monkeypatch.setattr(cache_mod, "_RENDERER_IDENTITY_MEMO", ["playwright-2.0"])
    fp_b = formula_mod._katex_fingerprint()
    assert fp_a != fp_b, "rendererが変わってもformula鍵が同一"
