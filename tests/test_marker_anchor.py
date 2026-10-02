# -*- coding: utf-8 -*-
"""p.marker(time, label) の time にアンカー名（"q2.start" 等）を渡せることのテスト。

アンカーはレンダ時（_resolve_anchors の後）にしか時刻が決まらないので、
マーカーも文字列のまま保持してレンダ時に解決する。
- 解決済みの時刻がチャプター（FFMETADATA / mp4 のチャプター / YouTube 目次）に使われる
- 存在しない名前は、レンダで候補（もしかして）つきの ValueError
- 数値の挙動は変えない
"""
import json
import os
import shutil
import subprocess

import pytest

import scriptvedit as sv
from scriptvedit.chapters import _chapters_metadata_path, _sorted_markers
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


# 画像 2 枚: intro（0〜2秒）→ q2（2〜5秒）。q2.start=2 / q2.end=5
_LAYER = (
    "from scriptvedit import *\n"
    "Object(asset('images/shape_badge.png')).time(2, name='intro') "
    "<= move(x=0.5, y=0.5, anchor='center')\n"
    "Object(asset('images/shape_badge.png')).time(3, name='q2') "
    "<= move(x=0.5, y=0.5, anchor='center')\n"
    "anchor('outro')\n")


def _project(tmp_path):
    layer = tmp_path / "l_marker.py"
    layer.write_text(_LAYER, encoding="utf-8")
    p = sv.Project()
    p.configure(width=160, height=90, fps=10, background_color="black")
    p.layer(str(layer))
    return p


def test_anchor_markers_resolve_after_render(tmp_path):
    p = _project(tmp_path)
    p.marker(0, "イントロ")
    p.marker("q2.start", "問題2")
    p.marker("outro", "まとめ")
    p.render(str(tmp_path / "o.mp4"), dry_run=True)
    assert _sorted_markers(p) == [(0.0, "イントロ"), (2.0, "問題2"), (5.0, "まとめ")]


def test_resolved_time_goes_into_chapter_metadata_key(tmp_path):
    """FFMETADATA のキャッシュ鍵（=内容）は解決済みの時刻から作られる。

    アンカー名で打っても、同じ時刻を数値で打った場合と同じチャプターになる。
    """
    p1 = _project(tmp_path)
    p1.marker(0, "イントロ")
    p1.marker("q2.start", "問題2")
    cmd1 = p1.render(str(tmp_path / "a.mp4"), dry_run=True)["main"]

    p2 = _project(tmp_path)
    p2.marker(0, "イントロ")
    p2.marker(2.0, "問題2")
    cmd2 = p2.render(str(tmp_path / "b.mp4"), dry_run=True)["main"]

    meta1 = cmd1[cmd1.index("ffmetadata") + 2]
    meta2 = cmd2[cmd2.index("ffmetadata") + 2]
    assert meta1 == meta2 == _chapters_metadata_path(p2)


def test_export_chapters_uses_resolved_time_after_render(tmp_path):
    p = _project(tmp_path)
    p.marker(0, "イントロ")
    p.marker("q2.start", "問題2")
    p.render(str(tmp_path / "o.mp4"), dry_run=True)
    path = p.export_chapters(str(tmp_path / "chapters.txt"))
    with open(path, encoding="utf-8") as f:
        assert f.read() == "0:00 イントロ\n0:02 問題2\n"


def test_export_before_render_resolves_timeline(tmp_path):
    """render() 前の export_* は dry_run でタイムラインを解決してから書く"""
    p = _project(tmp_path)
    p.marker(0, "イントロ")
    p.marker("q2.end", "まとめ")
    path = p.export_chapters(str(tmp_path / "chapters.txt"))
    with open(path, encoding="utf-8") as f:
        assert f.read() == "0:00 イントロ\n0:05 まとめ\n"
    meta = p.export_metadata(str(tmp_path / "meta.json"), title="題")
    with open(meta, encoding="utf-8") as f:
        data = json.load(f)
    assert data["chapters"] == [{"time": 0.0, "label": "イントロ"},
                                {"time": 5.0, "label": "まとめ"}]


def test_unknown_anchor_name_is_valueerror_with_candidates(tmp_path):
    p = _project(tmp_path)
    p.marker("q2.strat", "問題2")          # 綴り間違い
    with pytest.raises(ValueError) as ei:
        p.render(str(tmp_path / "o.mp4"), dry_run=True)
    msg = str(ei.value)
    assert "q2.strat" in msg
    assert "もしかして: q2.start" in msg
    assert "'intro.end'" in msg              # 定義済みアンカーの一覧


def test_unknown_anchor_name_in_export_before_render(tmp_path):
    p = _project(tmp_path)
    p.marker("nope", "X")
    with pytest.raises(ValueError, match="nope"):
        p.export_chapters(str(tmp_path / "chapters.txt"))


def test_numeric_markers_unchanged():
    p = sv.Project()
    p.marker(3, "A")
    p.marker(1.5, "B")
    assert p._markers == [(3.0, "A"), (1.5, "B")]
    assert _sorted_markers(p) == [(1.5, "B"), (3.0, "A")]
    with pytest.raises(ValueError):
        p.marker(-1, "負")
    with pytest.raises(ValueError):
        p.marker("  ", "空")


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg/ffprobe が無い環境")
def test_mp4_chapters_use_resolved_anchor_time(tmp_path):
    """実レンダした mp4 のチャプター開始時刻が解決済みのアンカー時刻になる"""
    p = _project(tmp_path)
    p.marker(0, "イントロ")
    p.marker("q2.start", "問題2")
    out = str(tmp_path / "chap.mp4")
    p.render(out)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_chapters", "-of", "json", out],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    chapters = json.loads(probe.stdout)["chapters"]
    got = [(c.get("tags", {}).get("title"), round(float(c["start_time"]), 3))
           for c in chapters]
    assert got == [("イントロ", 0.0), ("問題2", 2.0)], probe.stdout
    assert os.path.getsize(out) > 0
