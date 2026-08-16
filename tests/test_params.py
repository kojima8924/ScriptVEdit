# -*- coding: utf-8 -*-
"""p.param() の検証（監査項目21）

param はバッチ生成（同じ構成で値だけ変えて N 本作る）のための機能なので、
「指定したのに効いていない」が沈黙すると N 本すべてが既定値のまま出力される。
CLI 記法・型変換・未消費 override の検出をここで固定する。

`sys.argv` と `os.environ` は monkeypatch で復元し、プロセス内キャッシュ
（`Project._param_overrides`）はプロジェクトごとに新規なので漏れない。
"""
import pytest

from scriptvedit import Project
from scriptvedit.params import (
    _parse_param_sources, check_unconsumed_params, param)


@pytest.fixture(autouse=True)
def _clean_param_env(monkeypatch):
    """テスト間で --param / SCRIPTVEDIT_PARAM_* が漏れないようにする"""
    monkeypatch.setattr("sys.argv", ["main.py"])
    for key in [k for k in __import__("os").environ
                if k.startswith("SCRIPTVEDIT_PARAM_")]:
        monkeypatch.delenv(key, raising=False)


def _project():
    p = Project()
    p.configure(width=160, height=90, fps=10)
    return p


def _set_argv(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["main.py", *args])


# --- (a) CLI 記法 -----------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ("--param", "n=12"),
    ("--param=n=12",),
])
def test_cli_notations_are_equivalent(monkeypatch, argv):
    """`--param n=12` と `--param=n=12` は同値（インライン記法は未検証だった）"""
    _set_argv(monkeypatch, *argv)
    assert param(_project(), "n", 0) == 12


def test_param_without_equals_is_rejected(monkeypatch):
    """`--param title`（= 忘れ）は黙って無視せずエラーにする"""
    _set_argv(monkeypatch, "--param", "title")
    with pytest.raises(ValueError) as e:
        param(_project(), "title", "既定")
    assert "name=value" in str(e.value)


def test_param_without_value_is_rejected(monkeypatch):
    """`--param` が最後のトークン（値なし）もエラー"""
    _set_argv(monkeypatch, "--param")
    with pytest.raises(ValueError) as e:
        param(_project(), "title", "既定")
    assert "--param" in str(e.value)


def test_param_with_empty_name_is_rejected(monkeypatch):
    """`--param =X`（名前が空）もエラー"""
    _set_argv(monkeypatch, "--param", "=X")
    with pytest.raises(ValueError):
        param(_project(), "title", "既定")


def test_cli_beats_env(monkeypatch):
    """CLI 指定は環境変数より優先される（従来仕様の固定）"""
    _set_argv(monkeypatch, "--param", "msg=cli")
    monkeypatch.setenv("SCRIPTVEDIT_PARAM_msg", "env")
    assert param(_project(), "msg", "default") == "cli"


def test_env_is_case_insensitive(monkeypatch):
    """環境変数は大文字化されうるので名前の大小を無視して照合する"""
    monkeypatch.setenv("SCRIPTVEDIT_PARAM_MSG", "env")
    assert param(_project(), "msg", "default") == "env"


# --- (b) 型変換 -------------------------------------------------------------

@pytest.mark.parametrize("raw,default,expected", [
    ("12", 0, 12),
    ("-3", 7, -3),
    ("1.5", 0.0, 1.5),
    ("2", 0.0, 2.0),
    ("true", False, True),
    ("ON", False, True),
    ("1", False, True),
    ("no", True, False),
    ("0", True, False),
    ("こんにちは", "既定", "こんにちは"),
])
def test_type_conversion_from_default(monkeypatch, raw, default, expected):
    """default の型（int/float/bool/str）に合わせて変換する"""
    _set_argv(monkeypatch, "--param", f"v={raw}")
    got = param(_project(), "v", default)
    assert got == expected and type(got) is type(expected)


def test_bool_is_checked_before_int():
    """bool は int の派生。bool 既定値が int 変換へ落ちないこと"""
    assert isinstance(param(_project(), "flag", True), bool)


@pytest.mark.parametrize("raw,default,typename", [
    ("abc", 3, "int"),
    ("1.5", 3, "int"),        # int 既定に小数を渡すのも誤り
    ("abc", 1.5, "float"),
    ("ture", False, "bool"),  # 1文字の誤記を黙って False にしない
])
def test_conversion_failure_is_an_error(monkeypatch, raw, default, typename):
    """変換できない値は既定値へ黙って戻さずエラー（旧実装は黙って既定値）"""
    _set_argv(monkeypatch, "--param", f"v={raw}")
    with pytest.raises(ValueError) as e:
        param(_project(), "v", default)
    msg = str(e.value)
    assert typename in msg and repr(default) in msg


def test_conversion_error_reports_env_source(monkeypatch):
    """環境変数由来でも「どこで指定された値か」がメッセージに出る"""
    monkeypatch.setenv("SCRIPTVEDIT_PARAM_count", "abc")
    with pytest.raises(ValueError) as e:
        param(_project(), "count", 3)
    assert "SCRIPTVEDIT_PARAM_count" in str(e.value)


# --- (c) 未消費 override の検出 --------------------------------------------

def test_unconsumed_cli_param_is_an_error(monkeypatch):
    """どの p.param() にも読まれない --param は誤記としてエラー"""
    _set_argv(monkeypatch, "--param", "titel=X")
    p = _project()
    param(p, "title", "既定")           # 正しい名前だけが読まれる
    with pytest.raises(ValueError) as e:
        check_unconsumed_params(p)
    msg = str(e.value)
    assert "titel" in msg and "title" in msg   # 「もしかして」を含む


def test_consumed_cli_param_is_ok(monkeypatch):
    """読まれた --param は当然エラーにならない"""
    _set_argv(monkeypatch, "--param", "title=X")
    p = _project()
    assert param(p, "title", "既定") == "X"
    check_unconsumed_params(p)


def test_unconsumed_env_param_is_only_a_warning(monkeypatch):
    """環境変数は複数プロジェクトで共有されうるので警告に留める"""
    monkeypatch.setenv("SCRIPTVEDIT_PARAM_other", "X")
    p = _project()
    param(p, "title", "既定")
    # Windows は環境変数名を大文字化するので照合は大小無視
    with pytest.warns(UserWarning, match="(?i)SCRIPTVEDIT_PARAM_other"):
        check_unconsumed_params(p)


def test_unconsumed_check_is_case_insensitive(monkeypatch):
    """大文字化された環境変数を小文字名で読んでも「未消費」にしない"""
    monkeypatch.setenv("SCRIPTVEDIT_PARAM_MSG", "X")
    p = _project()
    assert param(p, "msg", "既定") == "X"
    check_unconsumed_params(p)          # 警告もエラーも出ない


def test_check_without_any_param_call_is_safe():
    """p.param() を一度も呼ばないプロジェクトでも落ちない"""
    check_unconsumed_params(_project())


# --- 型変換とキャッシュ鍵の結合部分 ----------------------------------------

def _layer_params_after_render(tmp_path, monkeypatch, raw, default_src):
    """param を1つ読むレイヤーを dry_run し、記録された解決値を返す

    レイヤーキャッシュの鮮度判定は「メタに保存した params」と
    「今回の解決値」の比較で行う（project.py の _should_use_cache）。
    その比較材料そのものを確かめる。
    """
    from scriptvedit import asset

    layer = tmp_path / "param_layer.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "from scriptvedit.context import current_project\n"
        "_p = current_project()\n"
        f"_n = _p.param('n', {default_src})\n"
        f"o = Object(r\"{asset('images/shape_badge.png')}\")\n"
        "o.time(1)\n",
        encoding="utf-8")
    _set_argv(monkeypatch, "--param", f"n={raw}")
    p = _project()
    p.layer(str(layer), cache="auto")
    p.render(str(tmp_path / "o.mp4"), dry_run=True)
    return p._layer_params[str(layer)]


def test_param_value_change_invalidates_layer_cache(tmp_path, monkeypatch):
    """int param の値が変われば鮮度判定の材料も変わる（12 → 13 で再生成）"""
    a = _layer_params_after_render(tmp_path, monkeypatch, "12", "12")
    b = _layer_params_after_render(tmp_path, monkeypatch, "13", "12")
    assert a == {"n": 12} and b == {"n": 13}
    assert a != b, "param の値を変えたのに鮮度判定が同じ"


def test_param_int_survives_meta_roundtrip(tmp_path, monkeypatch):
    """int へ変換した値が JSON メタを往復しても同値（12 → "12" で割れない）

    メタは JSON なので、型変換した値が文字列に化けると「値は変えていないのに
    毎回再生成される」。往復後も等しいことを固定する。
    """
    import json
    live = _layer_params_after_render(tmp_path, monkeypatch, "12", "12")
    assert json.loads(json.dumps(live)) == live


def test_param_default_type_changes_resolved_value(tmp_path, monkeypatch):
    """既定値の型を変えると解決値の型も変わる（12 と "12" は別物として扱う）"""
    as_int = _layer_params_after_render(tmp_path, monkeypatch, "12", "12")
    as_str = _layer_params_after_render(tmp_path, monkeypatch, "12", "'12'")
    assert as_int == {"n": 12} and as_str == {"n": "12"}
    assert as_int != as_str


# --- パース単体 -------------------------------------------------------------

def test_parse_records_origin(monkeypatch):
    """出自（cli / env）を保持する（未消費時の扱いを分けるため）"""
    _set_argv(monkeypatch, "--param", "a=1")
    monkeypatch.setenv("SCRIPTVEDIT_PARAM_b", "2")
    got = {k.lower(): v for k, v in _parse_param_sources().items()}
    assert got["a"].origin == "cli" and got["a"].value == "1"
    assert got["b"].origin == "env" and got["b"].value == "2"


def test_unrelated_argv_is_ignored(monkeypatch):
    """--param 以外の引数は素通り（他の CLI と併用できる）"""
    _set_argv(monkeypatch, "--verbose", "-o", "out.mp4")
    assert _parse_param_sources() == {}
