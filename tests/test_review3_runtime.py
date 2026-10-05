# -*- coding: utf-8 -*-
"""review3 の生成依存・指紋の寿命・描画領域の保持を検証する。"""
import importlib
import os
import shutil
import weakref
from pathlib import Path

import pytest

import scriptvedit as sv
from scriptvedit.context import _exec_stack, activate, current_project


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """素材と生成物をテストごとのフォルダへ閉じ込める。"""
    old, stack = current_project(), list(_exec_stack)
    activate(None)
    _exec_stack[:] = []
    monkeypatch.chdir(tmp_path)
    yield
    activate(old)
    _exec_stack[:] = stack


def _image(path, color):
    image = pytest.importorskip("PIL.Image", reason="Pillow が無い環境")
    image.new("RGBA", (64, 36), color).save(path)


def _project(body):
    Path("layer.py").write_text("from scriptvedit import *\n" + body, encoding="utf-8")
    p = sv.Project()
    p.configure(width=64, height=36, fps=10)
    p.layer(str(Path("layer.py").resolve()))
    return p


@pytest.mark.parametrize("effect", ["morph_to(target)",
                                   "assemble_from(target, max_pixels=20, expand=0)",
                                   "fly_to(target, max_pixels=20)"])
def test_consumed_formula_is_generated_before_terminal_effect(effect, monkeypatch):
    """表示リストから消えた数式も、画像を読む終端処理より先に生成する。"""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg / ffprobe が無い環境")
    pytest.importorskip("numpy", reason="numpy が無い環境")
    pytest.importorskip("scipy", reason="scipy が無い環境")
    _image("red.png", "red")
    calls = []

    def render_formula(spec, output, *args):
        calls.append(output)
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        _image(output, "blue")

    project_module = importlib.import_module("scriptvedit.project")
    monkeypatch.setattr(project_module, "_render_formula_png", render_formula)
    p = _project(f'target=formula("x")\nObject("red.png").time(0.3) <= {effect}\n')
    cold = p.render("out.mp4", dry_run=True)
    assert not calls
    p.render("out.mp4")
    assert len(calls) == 1
    assert Path(calls[0]).is_file()
    assert all(getattr(o, "_formula_spec", None) is None for o in p.objects)
    assert p.render("out.mp4", dry_run=True) == cold


@pytest.mark.parametrize("nested", [False, True])
def test_same_stat_replacement_is_detected_on_next_render(nested):
    """同じsize/mtimeの素材差し替えでも、次の親レンダでは新しい鍵になる。"""
    _image("asset.bmp", "red")
    body = 'Object("asset.bmp").time(1) <= resize(sx=0.5, sy=0.5)\n'
    if nested:
        Path("sub.py").write_text("from scriptvedit import *\n" + body, encoding="utf-8")
        body = ('sub=Project()\nsub.configure(width=64,height=36,fps=10)\n'
                f'sub.layer({str(Path("sub.py").resolve())!r})\n'
                'Object.from_project(sub).time(1)\n')
    p = _project(body)
    first = p.render("out.mp4", dry_run=True)
    stat = Path("asset.bmp").stat()
    _image("asset.bmp", "blue")
    os.utime("asset.bmp", ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert Path("asset.bmp").stat().st_size == stat.st_size
    assert Path("asset.bmp").stat().st_mtime_ns == stat.st_mtime_ns
    second = p.render("out.mp4", dry_run=True)
    assert first["cache"].keys() != second["cache"].keys()
    assert p.render("out.mp4", dry_run=True) == second


@pytest.mark.parametrize("dry_run", [False, True])
def test_globe_releases_renderer_and_can_redraw_identical_pixels(dry_run, monkeypatch):
    """最後のコマを描いたrendererは回収でき、再描画の画素と鍵は変わらない。"""
    np = pytest.importorskip("numpy", reason="numpy が無い環境")
    pytest.importorskip("cv2", reason="opencv が無い環境")
    if not dry_run and (not shutil.which("ffmpeg") or not shutil.which("ffprobe")):
        pytest.skip("ffmpeg / ffprobe が無い環境")
    from scriptvedit import framekit as fk
    globe_module = importlib.import_module("scriptvedit.fx_globe")
    original = globe_module._Renderer
    refs = []

    def renderer(*args):
        value = original(*args)
        refs.append(weakref.ref(value))
        return value

    monkeypatch.setattr(globe_module, "_Renderer", renderer)
    p = sv.Project()
    p.configure(width=64, height=36, fps=10)
    p._dry_run = dry_run
    obj = sv.globe(size=90, land=False).build(duration=0.2)
    source = obj.source
    if not dry_run:
        assert refs and all(ref() is None for ref in refs)
    first = fk.draw_frame(obj, 0).copy()
    assert refs[-1]() is not None
    last = fk.draw_frame(obj, 1).copy()
    assert all(ref() is None for ref in refs)
    np.testing.assert_array_equal(fk.draw_frame(obj, 0), first)
    np.testing.assert_array_equal(fk.draw_frame(obj, 1), last)
    assert all(ref() is None for ref in refs)
    assert obj.source == source
