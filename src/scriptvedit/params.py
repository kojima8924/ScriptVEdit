# -*- coding: utf-8 -*-
"""テンプレート変数（p.param）: CLI/環境変数による上書き解決。

audit.py と同じ方式で、Project インスタンスを第1引数に受ける自由関数を
提供する（Project は import しない＝循環 import を起こさない）。
project.py 側には manifest 掲載用の薄い委譲メソッドだけを残す。
"""

import os
import sys


def _parse_param_sources():
    """CLI(--param name=value)と環境変数(SCRIPTVEDIT_PARAM_<name>)を収集"""
    overrides = {}
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--param" and i + 1 < len(argv):
            kv = argv[i + 1]
            i += 2
        elif tok.startswith("--param="):
            kv = tok[len("--param="):]
            i += 1
        else:
            i += 1
            continue
        if "=" in kv:
            k, v = kv.split("=", 1)
            overrides[k] = v
    # 環境変数は CLI を上書きしない（CLI 優先）
    for key, val in os.environ.items():
        if key.startswith("SCRIPTVEDIT_PARAM_"):
            name = key[len("SCRIPTVEDIT_PARAM_"):]
            overrides.setdefault(name, val)
    return overrides


def param(project, name, default=None):
    """CLI/環境変数から差し替え可能なテンプレート変数を返す。

    `--param name=値` または環境変数 SCRIPTVEDIT_PARAM_<name> で上書きできる。
    default の型（int/float/bool）に合わせて文字列値を変換する。バッチ生成用。
    """
    if project._param_overrides is None:
        project._param_overrides = _parse_param_sources()
    if name in project._param_overrides:
        raw = project._param_overrides[name]
    else:
        # 大文字小文字を無視して再検索（Windowsの環境変数は大文字化されるため）
        raw = next((v for k, v in project._param_overrides.items()
                    if k.lower() == name.lower()), None)
        if raw is None:
            return _record_layer_param(project, name, default)
    if isinstance(default, bool):
        value = raw.strip().lower() in ("1", "true", "yes", "on")
    elif isinstance(default, int):
        try:
            value = int(raw)
        except ValueError:
            value = default
    elif isinstance(default, float):
        try:
            value = float(raw)
        except ValueError:
            value = default
    else:
        value = raw
    return _record_layer_param(project, name, value)


def _record_layer_param(project, name, value):
    """レイヤー実行中に解決された param をキャッシュ鮮度検証用に記録する。

    レイヤーキャッシュのメタへ保存し、--param/環境変数の値が変わったのに
    旧キャッシュを使い続ける取りこぼしを防ぐ（issue #13 P2-7）。
    """
    layer_file = project._current_layer_file
    if layer_file:
        project._layer_params.setdefault(layer_file, {})[name] = value
    return value
