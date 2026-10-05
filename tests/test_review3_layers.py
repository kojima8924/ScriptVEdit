# -*- coding: utf-8 -*-
"""外部レビュー 3 のレイヤーキャッシュと並列境界の回帰。"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scriptvedit import Project
from scriptvedit.context import activate


@pytest.fixture
def scene(tmp_path, monkeypatch):
    """素材・生成物・レイヤーをテスト専用ディレクトリへ閉じ込める。"""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg / ffprobe が無い環境")
    image = pytest.importorskip("PIL.Image", reason="Pillow が無い環境")
    monkeypatch.chdir(tmp_path)
    image.new("RGBA", (64, 36), "red").save("red.png")
    image.new("RGBA", (64, 36), "blue").save("blue.png")
    activate(None)
    yield tmp_path
    activate(None)


def _layer(name, code):
    path = Path(name).resolve()
    path.write_text("from scriptvedit import *\n" + code,
                    encoding="utf-8", newline="\r\n")
    return str(path)


def _project(*layers, duration=4):
    p = Project()
    p.configure(width=64, height=36, fps=10, duration=duration)
    for filename, options in layers:
        p.layer(filename, **options)
    return p


def _pixel(path, at):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True, timeout=30).stdout
    offset = (18 * 64 + 32) * 3
    return tuple(raw[offset:offset + 3])


@pytest.mark.parametrize("body, red_at_two", [
    ('Object("red.png").until("cut")\n', True),
    ('Object("red.png").show(1) @ "cut"\n', False),
    ('pause.until("cut")\nObject("red.png").time(1)\n', False),
])
def test_external_anchor_invalidates_layer_cache(scene, monkeypatch, body, red_at_two):
    """until・絶対配置・pause の外部アンカー変更をメタと画素で確認する。"""
    control = _layer("control.py", 'from scriptvedit.context import current_project\n'
                     'pause.time(current_project().param("cut", 1))\nanchor("cut")\n')
    visual = _layer("visual.py", body)
    monkeypatch.setenv("SCRIPTVEDIT_PARAM_cut", "1")
    p = _project((control, {}), (visual, {"cache": "make"}))
    cold = p.render("first.mp4", dry_run=True)
    p.render("first.mp4")
    assert p.render("first.mp4", dry_run=True) == cold
    _, meta_path = p._layer_cache_paths_for(p._layer_specs[1])
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    assert meta["external_anchors"] == {"cut": 1}

    monkeypatch.setenv("SCRIPTVEDIT_PARAM_cut", "3")
    cached = _project((control, {}), (visual, {"cache": "auto"}))
    cached.render("cached.mp4")
    live = _project((control, {}), (visual, {}))
    live.render("live.mp4")
    assert _pixel("cached.mp4", 2) == _pixel("live.mp4", 2)
    pixel = _pixel("cached.mp4", 2)
    assert (pixel[0] > 200) == red_at_two
    assert not any("artifacts/layer/" in str(getattr(o, "source", "")).replace("\\", "/")
                   for o in cached.objects)


def test_unreferenced_anchor_does_not_invalidate_layer_cache(scene, monkeypatch):
    """出力に効かない別レイヤーのアンカーは鮮度へ混ぜない。"""
    control = _layer("control.py", 'from scriptvedit.context import current_project\n'
                     'pause.time(current_project().param("cut", 1))\nanchor("cut")\n')
    visual = _layer("visual.py", 'Object("red.png").time(4)\n')
    monkeypatch.setenv("SCRIPTVEDIT_PARAM_cut", "1")
    p = _project((control, {}), (visual, {"cache": "make"}))
    p.render("first.mp4")
    cache_path, _ = p._layer_cache_paths_for(p._layer_specs[1])
    monkeypatch.setenv("SCRIPTVEDIT_PARAM_cut", "3")
    cached = _project((control, {}), (visual, {"cache": "auto"}))
    cached.render("second.mp4", dry_run=True)
    assert any(getattr(o, "source", None) == cache_path for o in cached.objects)


@pytest.mark.parametrize("other_cache", [False, True])
def test_priority_override_preserving_external_order_can_cache(scene, other_cache):
    """片側・両側を畳んでも外部との重なり順が変わらない上書きは許す。"""
    a = _layer("a.py", 'Object("red.png").show(1, priority=0.5)\n')
    b = _layer("b.py", 'Object("blue.png").show(1, priority=1.25)\n')
    p = _project((a, {"cache": "make", "priority": 0}),
                 (b, {"cache": "make" if other_cache else "off", "priority": 1}), duration=1)
    cold = p.render("first.mp4", dry_run=True)
    p.render("first.mp4")
    assert p.render("first.mp4", dry_run=True) == cold
    cached = _project((a, {"cache": "auto", "priority": 0}),
                      (b, {"cache": "auto" if other_cache else "off", "priority": 1}), duration=1)
    cached.render("second.mp4")
    assert _pixel("first.mp4", 0.5) == _pixel("second.mp4", 0.5)


@pytest.mark.parametrize("timing", [
    'Object("red.png").time(1) <= opacity(1)',
    'Object("red.png").show(0.96) @ 0.04',
])
def test_parallel_keeps_closed_endpoint_frame(scene, timing):
    """整数境界の終端と格子外の開始でも逐次・並列の画素を揃える。"""
    layer = _layer("layer.py", timing + "\n")
    p = _project((layer, {}), duration=2)
    p.render("single.mp4")
    p.render("parallel.mp4", parallel=2)
    for at in (0.0, 0.9, 1.0, 1.1):
        single = _pixel("single.mp4", at)
        parallel = _pixel("parallel.mp4", at)
        assert max(abs(a - b) for a, b in zip(single, parallel)) <= 2
    assert _pixel("parallel.mp4", 1)[0] > 200
    assert max(_pixel("parallel.mp4", 1.1)) < 5
