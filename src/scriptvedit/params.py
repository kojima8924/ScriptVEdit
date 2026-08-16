# -*- coding: utf-8 -*-
"""テンプレート変数（p.param）: CLI/環境変数による上書き解決。

audit.py と同じ方式で、Project インスタンスを第1引数に受ける自由関数を
提供する（Project は import しない＝循環 import を起こさない）。
project.py 側には manifest 掲載用の薄い委譲メソッドだけを残す。

param はバッチ生成（同じ構成で値だけ変えて N 本作る）のための機能なので、
「指定したのに効いていない」を沈黙させないことが最重要。誤りは全て
構築時／レンダ時のエラーにする（監査項目21）:

- `--param n=abc` を `p.param('n', 3)` で受ける → 既定値へ黙って戻さずエラー
- `--param title`（`=` 忘れ）→ 黙って無視せずエラー
- どの `p.param()` にも読まれなかった `--param` → 誤記としてエラー
  （環境変数 SCRIPTVEDIT_PARAM_* は複数プロジェクトで共有されうるので警告）
"""

import collections
import os
import sys
import warnings


# 上書き値1件。origin は "cli"（--param）か "env"（SCRIPTVEDIT_PARAM_*）。
# 未消費の扱い（エラー / 警告）を分けるために出自を保持する。
_Override = collections.namedtuple("_Override", ("value", "origin"))

# bool 既定値の param が受け付ける表記（大文字小文字とAA前後の空白は無視）。
# ここに無い綴りを黙って False にすると「--param debug=ture で無効のまま」が
# 沈黙する（CLI からの1文字ミスは日常的に起きる）。
_BOOL_TRUE = ("1", "true", "yes", "on")
_BOOL_FALSE = ("0", "false", "no", "off")


def _parse_param_sources():
    """CLI(--param name=value)と環境変数(SCRIPTVEDIT_PARAM_<name>)を収集

    戻り値は {name: _Override}。書式違反はここで ValueError にする
    （黙って読み飛ばすと「指定したのに効かない」が沈黙するため）。
    """
    overrides = {}
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--param":
            if i + 1 >= len(argv):
                raise ValueError(
                    "--param に値がありません。"
                    "`--param name=value` の形式で指定してください。")
            kv = argv[i + 1]
            i += 2
        elif tok.startswith("--param="):
            kv = tok[len("--param="):]
            i += 1
        else:
            i += 1
            continue
        if "=" not in kv:
            raise ValueError(
                f"--param は name=value 形式です: '--param {kv}'。"
                f"`--param {kv}=<値>` のように値まで指定してください。")
        k, v = kv.split("=", 1)
        if not k:
            raise ValueError(
                f"--param の名前が空です: '--param {kv}'。"
                f"`--param name=value` の形式で指定してください。")
        overrides[k] = _Override(v, "cli")
    # 環境変数は CLI を上書きしない（CLI 優先）
    for key, val in os.environ.items():
        if key.startswith("SCRIPTVEDIT_PARAM_"):
            name = key[len("SCRIPTVEDIT_PARAM_"):]
            overrides.setdefault(name, _Override(val, "env"))
    return overrides


def _source_label(name, override):
    """エラーメッセージ用に上書きの出自を人間が読める形で返す"""
    if override.origin == "cli":
        return f"--param {name}={override.value}"
    return f"環境変数 SCRIPTVEDIT_PARAM_{name}={override.value}"


def _convert(name, override, default):
    """上書きの生文字列を default の型へ変換する（失敗は ValueError）"""
    raw = override.value
    where = _source_label(name, override)
    if isinstance(default, bool):
        s = raw.strip().lower()
        if s in _BOOL_TRUE:
            return True
        if s in _BOOL_FALSE:
            return False
        raise ValueError(
            f"{where} を bool として解釈できません（既定値: {default!r}）。"
            f"真: {'/'.join(_BOOL_TRUE)} / 偽: {'/'.join(_BOOL_FALSE)} "
            f"のいずれかで指定してください。")
    # bool は int の派生なので必ず bool を先に判定する
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            raise ValueError(
                f"{where} を int として解釈できません（既定値: {default!r}）。"
                f"整数（例: {default!r}）で指定してください。") from None
    if isinstance(default, float):
        try:
            return float(raw)
        except ValueError:
            raise ValueError(
                f"{where} を float として解釈できません（既定値: {default!r}）。"
                f"小数（例: {default!r}）で指定してください。") from None
    return raw


def param(project, name, default=None):
    """CLI/環境変数から差し替え可能なテンプレート変数を返す。

    `--param name=値` または環境変数 SCRIPTVEDIT_PARAM_<name> で上書きできる。
    default の型（int/float/bool）に合わせて文字列値を変換する。バッチ生成用。
    変換できない値・書式違反・どの param にも読まれない --param はエラー。
    """
    if project._param_overrides is None:
        project._param_overrides = _parse_param_sources()
    overrides = project._param_overrides
    key = name
    if key not in overrides:
        # 大文字小文字を無視して再検索（Windowsの環境変数は大文字化されるため）
        key = next((k for k in overrides if k.lower() == name.lower()), None)
        if key is None:
            _mark_consumed(project, name)
            return _record_layer_param(project, name, default)
    _mark_consumed(project, key)
    value = _convert(name, overrides[key], default)
    return _record_layer_param(project, name, value)


def _mark_consumed(project, key):
    """`p.param()` が実際に参照した名前を記録する（未消費 override の検出用）"""
    consumed = getattr(project, "_param_consumed", None)
    if consumed is None:
        consumed = set()
        project._param_consumed = consumed
    consumed.add(key)


def check_unconsumed_params(project):
    """どの `p.param()` にも読まれなかった上書きを検出する。

    全レイヤーの実行が終わった時点（Render pass の末尾）で呼ぶこと。
    `--param titel=X` のような1文字の誤記は、現状どのレイヤーにも届かず
    N 本すべてが既定値のまま「成功」する。CLI 由来は ValueError、
    環境変数由来は警告（複数プロジェクトで共有されうるため）にする。
    """
    overrides = project._param_overrides
    if not overrides:
        return
    consumed = getattr(project, "_param_consumed", None) or set()
    lowered = {c.lower() for c in consumed}
    unused = [n for n in overrides
              if n not in consumed and n.lower() not in lowered]
    if not unused:
        return
    known = sorted(consumed)
    cli = sorted(n for n in unused if overrides[n].origin == "cli")
    env = sorted(n for n in unused if overrides[n].origin == "env")
    if env:
        names = ", ".join(f"SCRIPTVEDIT_PARAM_{n}" for n in env)
        warnings.warn(
            f"どの p.param() にも読まれていない環境変数があります: {names}。"
            f"このプロジェクトが参照する param: "
            f"{', '.join(known) if known else '(なし)'}")
    if cli:
        hint = _suggest_hint(cli[0], known) if known else ""
        names = ", ".join(f"--param {n}={overrides[n].value}" for n in cli)
        raise ValueError(
            f"どの p.param() にも読まれていない --param があります: {names}。"
            f"{hint}\n"
            f"このプロジェクトが参照する param: "
            f"{', '.join(known) if known else '(なし)'}。"
            f"名前の綴りを確認するか、レイヤー側で p.param('...') を読んで"
            f"ください。")


def _record_layer_param(project, name, value):
    """レイヤー実行中に解決された param をキャッシュ鮮度検証用に記録する。

    レイヤーキャッシュのメタへ保存し、--param/環境変数の値が変わったのに
    旧キャッシュを使い続ける取りこぼしを防ぐ（issue #13 P2-7）。
    """
    layer_file = project._current_layer_file
    if layer_file:
        project._layer_params.setdefault(layer_file, {})[name] = value
    return value


# --- 遅延解決の相互参照（循環importを避けるため末尾で束縛）---
from scriptvedit.state import _suggest_hint
