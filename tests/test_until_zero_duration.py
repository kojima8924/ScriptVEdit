# -*- coding: utf-8 -*-
"""until() の 0 尺解決を明示エラーにする（監査項目6）

`until('mark')` の解決先が開始時刻以前だと表示尺が 0 になる。0 尺は
チェックポイント経路の尺基準をすり抜け、最終的に `clip((t-start)/0,0,1)` が
filtergraph へ埋まって ffmpeg が Division by zero / EINVAL で落ちる。
利用者に届くのは原因不明の ffmpeg エラーなので、タイムライン解決の時点で
「何が悪いか + どう直すか」を出す。

判定ロジックは `scriptvedit.timeline._check_until_zero_duration` で、
`_resolve_anchors`（project.py）の収束後から呼ばれる。反復の途中は尺が
一時的に 0 になりうるため、収束前に呼ぶと誤検出する。
Pause は描画されない（0 尺でも壊れない）ので対象外。
"""
import os

import pytest

from scriptvedit import Project
from scriptvedit.timeline import Pause, _check_until_zero_duration

_LAYER_SRC = """from scriptvedit import *

a = Object(asset("images/shape_badge.png"))
a.time(3, name="m")

b = Object(asset("images/shape_dots.png"))
b @ {start}
b.until("m.end")
"""


def _resolved_project(tmp_path, start):
    """b を start 秒に絶対配置し、m.end(=3秒) まで until したプロジェクトを解決する"""
    layer = tmp_path / "until_layer.py"
    layer.write_text(_LAYER_SRC.format(start=start), encoding="utf-8")
    p = Project()
    p.configure(width=320, height=180, fps=10)
    p.layer(str(layer))
    p.render(os.path.join(str(tmp_path), "o.mp4"), dry_run=True)
    return p


def test_until_resolving_to_zero_is_detected(tmp_path):
    """開始時刻より前のアンカーへ until → render() 時点で RuntimeError"""
    with pytest.raises(RuntimeError) as e:
        _resolved_project(tmp_path, start=5)
    msg = str(e.value)
    assert "until('m.end')" in msg          # どのアンカーか
    assert "shape_dots" in msg              # どの素材か
    assert "time(" in msg                   # どう直すか


def test_until_resolving_to_positive_duration_is_ok(tmp_path):
    """通常ケース（アンカーが後ろにある）は素通り"""
    p = _resolved_project(tmp_path, start=1)
    target = [o for o in p.objects if getattr(o, "_until_anchor", None)]
    assert target and target[0].duration == 2
    _check_until_zero_duration(p.objects, p._anchors)


def test_unresolved_anchor_is_left_to_the_existing_check():
    """未定義アンカーはここでは扱わない（既存の「未定義のアンカー」検査の担当）"""
    item = Pause()
    item._until_anchor = "missing"
    item.duration = 0
    _check_until_zero_duration([item], {})       # 何も起きない


def test_negative_duration_is_also_rejected(tmp_path):
    """アンカーが開始時刻より前（＝負の尺）も同じ経路で弾かれる"""
    with pytest.raises(RuntimeError, match="until"):
        _resolved_project(tmp_path, start=10)



def test_items_without_until_are_ignored():
    """until を持たないアイテムの 0 尺には介入しない（別の意味を持つため）"""
    item = Pause()
    item.duration = 0
    _check_until_zero_duration([item], {"m.end": 3})
