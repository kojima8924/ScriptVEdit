# -*- coding: utf-8 -*-
"""プラグイン指紋の穴埋め（監査 項目22b）の回帰テスト。

定義元ファイルを読めないプラグイン（REPL / exec(<文字列>) / zip 同梱 /
権限で読めない共有パス）の code_ffp は、以前は定数 "inline" だった。
その値はチェックポイント鍵（cache.py）と from_project 鍵（objects.py）へ
混ざるため、ビルダー本体を書き換えても鍵が変わらず古い焼き込みが黙って
再利用され、しかも全プラグインで同じ鍵成分を共有していた。
"""
import pytest

from scriptvedit.plugins import (
    _EFFECT_PLUGINS, _builder_bytecode_ffp, _plugin_code_ffp)


def _define_inline_plugin(name, body):
    """exec(<文字列>) でプラグインを定義する（co_filename が実ファイルでない）"""
    namespace = {}
    exec(compile(
        "from scriptvedit import effect_plugin\n"
        f"@effect_plugin({name!r}, bakeable=True, category='テスト',\n"
        "               params={'amount': {'type': 'number', 'default': 1}})\n"
        f"def build(params, ctx):\n"
        f"    \"\"\"インライン定義のテスト用プラグイン\"\"\"\n"
        f"    {body}\n", "<string>", "exec"), namespace)
    return _EFFECT_PLUGINS[name]


@pytest.fixture(autouse=True)
def _cleanup_plugins():
    before = set(_EFFECT_PLUGINS)
    yield
    import scriptvedit as sv
    for name in set(_EFFECT_PLUGINS) - before:
        _EFFECT_PLUGINS.pop(name, None)
        if hasattr(sv, name):
            delattr(sv, name)
        # __all__ に残すと後続の `from scriptvedit import *` が AttributeError
        if name in getattr(sv, "__all__", []):
            sv.__all__.remove(name)


def test_inline_plugin_fingerprint_is_not_a_constant():
    """ファイルを持たないプラグインでもビルダー本体で鍵が変わる"""
    a = _define_inline_plugin(
        "t22b_a", "return [f\"gblur=sigma={params['amount']}\"]")
    b = _define_inline_plugin(
        "t22b_b", "return [f\"boxblur={params['amount']}\"]")
    assert a.source_file is None and b.source_file is None
    assert a.code_ffp.startswith("bc-")   # 由来が判別できる前置き
    assert a.code_ffp != "inline"
    assert a.code_ffp != b.code_ffp       # 以前は両方 "inline" で同一だった


def test_inline_plugin_fingerprint_changes_the_checkpoint_key():
    """code_ffp はチェックポイント鍵（_op_fingerprint_str）に効く"""
    from scriptvedit.cache import _op_fingerprint_str

    a = _define_inline_plugin(
        "t22b_key1", "return [f\"gblur=sigma={params['amount']}\"]")
    b = _define_inline_plugin(
        "t22b_key2", "return [f\"boxblur={params['amount']}\"]")
    import scriptvedit as sv
    op_a = getattr(sv, "t22b_key1")(amount=2)
    op_b = getattr(sv, "t22b_key2")(amount=2)
    sig_a = _op_fingerprint_str(op_a).replace(a.name, "<name>")
    sig_b = _op_fingerprint_str(op_b).replace(b.name, "<name>")
    # 名前を伏せても（＝ビルダー本体の違いだけで）鍵が変わること
    assert sig_a != sig_b


def test_bytecode_fingerprint_follows_the_builder_body():
    """同じ名前・同じ引数でも中身が違えば指紋が変わる"""
    def build_one(params, ctx):
        return ["gblur=sigma=1"]

    def build_two(params, ctx):
        return ["gblur=sigma=2"]

    def build_one_again(params, ctx):
        return ["gblur=sigma=1"]

    assert _builder_bytecode_ffp(build_one) != _builder_bytecode_ffp(build_two)
    assert (_builder_bytecode_ffp(build_one)
            == _builder_bytecode_ffp(build_one_again))
    assert _plugin_code_ffp(build_one)[1].startswith("bc-") is False  # 実ファイル


def test_inline_plugin_joins_layer_cache_freshness_keys(tmp_path):
    """定義元ファイルが無いプラグインもレイヤーキャッシュ鮮度の依存集合に載る"""
    import scriptvedit as sv

    plug = _define_inline_plugin(
        "t22b_meta", "return [f\"gblur=sigma={params['amount']}\"]")
    layer = tmp_path / "l.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "text('あ', x=0.5, y=0.5, size=40, color='white').time(1)\n",
        encoding="utf-8")
    p = sv.Project()
    p.configure(width=64, height=36, fps=5, duration=1)
    p.layer(str(layer))
    p.render(str(tmp_path / "o.mp4"), dry_run=True)
    meta = p._current_layer_sources_meta(str(layer))
    key = f"plugin://{plug.name}"
    assert key in meta and meta[key] == plug.code_ffp
