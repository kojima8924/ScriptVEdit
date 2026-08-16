# -*- coding: utf-8 -*-
"""レイヤー登録コンテキスト（監査 項目2）の回帰テスト。

- レイヤーファイルの外で作った Object は render() で無言破棄されるため
  ValueError で拒否する（従来は警告もエラーも出ず「完了」と表示された）。
- レイヤー内で `sub = Project()` しても以降の Object は親へ登録される
  （Project.__init__ が _current を奪わない）。
- 前回レンダの残骸は誤検出しない（同一 Project への render() 2連発が通る）。
"""
import os

import pytest

import scriptvedit as sv
from scriptvedit import Object, Project, asset, text
from scriptvedit.context import current_project


def _layer(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return str(path)


def _project():
    p = Project()
    p.configure(width=64, height=36, fps=10)
    return p


@pytest.fixture
def image_src():
    return asset("images/shape_dots.png").replace("\\", "/")


# --- レイヤー外 Object の拒否 ------------------------------------------------

def test_object_created_outside_layer_is_rejected(tmp_path, image_src):
    """main.py 相当で作った Object は無言破棄されず ValueError になる"""
    p = _project()
    p.layer(_layer(tmp_path, "l.py",
                   "from scriptvedit import *\n"
                   f"Object({image_src!r}).time(1)\n"))
    stray = Object(image_src)
    stray.time(2)
    assert stray in p.objects
    with pytest.raises(ValueError, match="レイヤーファイルの外"):
        p.render(str(tmp_path / "out.mp4"), dry_run=True)


def test_text_created_outside_layer_is_rejected_with_content(tmp_path):
    """text() も同様に拒否し、メッセージに文言を含める"""
    p = _project()
    t = text("消えるはず", size=20, border=2)
    t.time(2)
    with pytest.raises(ValueError, match="消えるはず"):
        p.render(str(tmp_path / "out.mp4"), dry_run=True)


def test_stray_detection_also_covers_layerless_project(tmp_path, image_src):
    """layer() が1つも無い Project でも黙って空動画にせず拒否する"""
    p = _project()
    Object(image_src).time(1)
    with pytest.raises(ValueError, match="レイヤーファイルの外"):
        p.render(str(tmp_path / "out.mp4"), dry_run=True)


def test_thumbnail_also_rejects_stray_object(tmp_path, image_src):
    """thumbnail/storyboard も同じ準備経路なので同じ判定になる"""
    p = _project()
    p.layer(_layer(tmp_path, "l.py",
                   "from scriptvedit import *\n"
                   f"Object({image_src!r}).time(1)\n"))
    Object(image_src).time(1)
    with pytest.raises(ValueError, match="レイヤーファイルの外"):
        p.thumbnail(0.5, str(tmp_path / "th.png"))


# --- 誤検出しないこと --------------------------------------------------------

def test_render_twice_on_same_project(tmp_path, image_src):
    """前回レンダの残骸（レイヤー由来）は誤検出しない"""
    p = _project()
    p.layer(_layer(tmp_path, "l.py",
                   "from scriptvedit import *\n"
                   f"Object({image_src!r}).time(1)\n"
                   "text('あ', size=20, border=2).time(1)\n"
                   "pause.time(0.5)\n"
                   "anchor('mk')\n"))
    first = p.render(str(tmp_path / "o.mp4"), dry_run=True)
    second = p.render(str(tmp_path / "o.mp4"), dry_run=True)
    assert first["main"] == second["main"]


def test_layer_items_are_stamped_with_their_layer_file(tmp_path, image_src):
    """レイヤー由来アイテムには生成元レイヤーファイル名が刻まれる"""
    lay = _layer(tmp_path, "l.py",
                 "from scriptvedit import *\n"
                 f"Object({image_src!r}).time(1)\n"
                 "pause.time(0.5)\n")
    p = _project()
    p.layer(lay)
    p.render(str(tmp_path / "o.mp4"), dry_run=True)
    assert p.objects
    for item in p.objects:
        assert item._defined_in_layer == lay


# --- レイヤー内 sub = Project() が _current を奪わない -----------------------

def test_sub_project_in_layer_does_not_steal_registration(tmp_path, image_src):
    """レイヤー内で Project() を作っても以降の Object は親に登録される"""
    p = _project()
    p.layer(_layer(tmp_path, "l.py",
                   "from scriptvedit import *\n"
                   "sub = Project()\n"
                   "sub.configure(width=32, height=18, fps=10)\n"
                   f"Object({image_src!r}).time(1)\n"
                   "text('あ', size=20, border=2).time(1)\n"))
    p.render(str(tmp_path / "o.mp4"), dry_run=True)
    assert len(p.objects) == 2, "レイヤー内 Project() に Object を吸われている"


def test_project_outside_layer_still_becomes_current():
    """レイヤー外の Project() は従来どおり _current を取る"""
    outer = Project()
    assert current_project() is outer
    inner = Project()
    assert current_project() is inner
    obj = Object("dummy.png")
    assert obj in inner.objects


def test_from_project_registers_composite_into_parent():
    """from_project の合成 Object は親レイヤーに登録される（test74 相当）"""
    p = Project()
    p.configure(width=640, height=360, fps=30)
    p.layer(os.path.join("tests", "layers", "test74_nested.py"))
    p.render("o.mp4", dry_run=True)
    objs = [o for o in p.objects if isinstance(o, sv.Object)]
    assert len(objs) == 1
    assert objs[0]._defined_in_layer.endswith("test74_nested.py")
