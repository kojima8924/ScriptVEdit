# -*- coding: utf-8 -*-
"""review1 のシーケンス重複入力と親子 Project の param 消費の回帰。"""
import pytest

from scriptvedit import Object, Project, video_sequence
from scriptvedit.context import activate, current_project


@pytest.fixture(autouse=True)
def _restore_project():
    """現在の Project をテストの外へ持ち越さない。"""
    previous = current_project()
    yield
    activate(previous)


def test_video_sequence_repeated_object(monkeypatch):
    """同じ Object を再利用しても、入力の順序と回数を保って消費する。"""
    p = Project()
    p._dry_run = True
    p._pending_compute_cmds = {}
    monkeypatch.setattr(p, "_probe_media", lambda source: {
        "duration": 2.0, "video_duration": 2.0, "has_audio": False})
    first = Object("first.mp4")
    second = Object("second.mp4")
    sequence = video_sequence(first, second, first)
    assert p.objects == [sequence]
    assert sequence.duration == 5.0
    assert sequence._origin_sources == ["first.mp4", "second.mp4", "first.mp4"]
    command = p._pending_compute_cmds[sequence.source]
    assert [command[i + 1] for i, part in enumerate(command) if part == "-i"] == [
        "first.mp4", "second.mp4", "first.mp4"]


@pytest.mark.parametrize("parent_position", ["before", "after", "none"])
def test_from_project_consumes_parent_and_child_params(tmp_path, monkeypatch, parent_position):
    """親と子が別々に読む CLI 上書きは両方とも使用済みになる。"""
    argv = ["main.py", "--param", "clip_count=5"]
    if parent_position != "none":
        argv.extend(["--param", "title=X"])
    monkeypatch.setattr("sys.argv", argv)
    child = tmp_path / "child.py"
    child.write_text(
        "from scriptvedit import pause\n"
        "from scriptvedit.context import current_project\n"
        "pause.time(current_project().param('clip_count', 1))\n", encoding="utf-8")
    parent = tmp_path / "parent.py"
    read_title = "p.param('title', 'default')\n"
    parent.write_text(
        "from scriptvedit import Object, Project\n"
        "from scriptvedit.context import current_project\n"
        "p = current_project()\n"
        + (read_title if parent_position == "before" else "")
        + "sub = Project()\n"
        "sub.configure(width=160, height=90, fps=10)\n"
        f"sub.layer({str(child)!r})\n"
        "Object.from_project(sub).time(5)\n"
        + (read_title if parent_position == "after" else ""), encoding="utf-8")
    p = Project()
    p.configure(width=160, height=90, fps=10)
    p.layer(str(parent))
    command = p.render(str(tmp_path / "out.mp4"), dry_run=True)
    assert command["main"]
    assert p.duration == 5.0
    assert "clip_count" in p._param_consumed
    if parent_position != "none":
        assert "title" in p._param_consumed


@pytest.mark.parametrize("origin", ["cli", "env"])
def test_nested_and_sibling_projects_collect_consumed_params(tmp_path, monkeypatch, origin):
    """孫と後続の兄弟が読む名前も、最外側の検査へ集約される。"""
    monkeypatch.setattr("sys.argv", ["main.py"])
    if origin == "cli":
        monkeypatch.setattr("sys.argv", ["main.py", "--param", "grandchild=2",
                                        "--param", "sibling=3"])
    else:
        monkeypatch.setenv("SCRIPTVEDIT_PARAM_GRANDCHILD", "2")
        monkeypatch.setenv("SCRIPTVEDIT_PARAM_SIBLING", "3")
    grandchild = tmp_path / "grandchild.py"
    sibling = tmp_path / "sibling.py"
    for path, name in [(grandchild, "grandchild"), (sibling, "sibling")]:
        path.write_text(
            "from scriptvedit import pause\n"
            "from scriptvedit.context import current_project\n"
            f"pause.time(current_project().param({name!r}, 1))\n", encoding="utf-8")
    child = tmp_path / "child.py"
    child.write_text(
        "from scriptvedit import Object, Project\n"
        "sub = Project()\n"
        "sub.configure(width=160, height=90, fps=10)\n"
        f"sub.layer({str(grandchild)!r})\n"
        "Object.from_project(sub).time(2)\n", encoding="utf-8")
    parent = tmp_path / "parent.py"
    parent.write_text(
        "from scriptvedit import Object, Project\n"
        "sub = Project()\n"
        "sub.configure(width=160, height=90, fps=10)\n"
        f"sub.layer({str(child)!r})\n"
        "Object.from_project(sub).time(2)\n"
        "other = Project()\n"
        "other.configure(width=160, height=90, fps=10)\n"
        f"other.layer({str(sibling)!r})\n"
        "Object.from_project(other).time(3)\n", encoding="utf-8")
    p = Project()
    p.configure(width=160, height=90, fps=10)
    p.layer(str(parent))
    command = p.render(str(tmp_path / "out.mp4"), dry_run=True)
    assert command["main"]
    assert p.duration == 5.0
    assert {name.lower() for name in p._param_consumed} == {"grandchild", "sibling"}
