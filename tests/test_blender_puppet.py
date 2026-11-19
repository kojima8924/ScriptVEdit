# -*- coding: utf-8 -*-
"""Blender 切り絵の鍵・計画・親子変形・RGBA を検証する。"""
import json
import os
import shutil
import struct
import subprocess
import zlib

import pytest

import scriptvedit as sv
from scriptvedit import blender as backend
from scriptvedit.blender_runtime import _value
from scriptvedit.cache import _is_hold_artifact_path
from scriptvedit.context import activate, current_project, _exec_stack


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    old, stack = current_project(), list(_exec_stack)
    activate(None)
    _exec_stack[:] = []
    monkeypatch.chdir(tmp_path)
    yield
    activate(old)
    _exec_stack[:] = stack


def _rig():
    return {"schema": 1, "canvas": [64, 64], "nodes": [
        {"id": "root", "position": [32, 32]},
        {"id": "body", "parent": "root", "position": [0, 0], "size": [20, 20], "color": "#f02010"},
        {"id": "hand", "parent": "root", "position": [18, 0], "size": [8, 8], "color": "#10e030", "z": 1},
    ]}


def _plan(rig=None, motion=None, **kwargs):
    p = sv.Project()
    p._mode = "plan"
    try:
        return sv.puppet_clip(_rig() if rig is None else rig, motion, duration=.5, fps=8, **kwargs)
    finally:
        activate(None)


def _png(path, color):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" +
                     chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 6, 0, 0, 0)) +
                     chunk(b"IDAT", zlib.compress((b"\0" + bytes(color) * 4) * 4)) + chunk(b"IEND", b""))


def test_factory_is_discoverable_without_bpy():
    manifest = sv.describe(name="puppet_clip")
    entry = manifest["factories"][0]
    assert entry["name"] == "puppet_clip"
    assert "bpy" not in __import__("sys").modules


def test_content_cache_is_portable_and_invalidates_image(tmp_path):
    paths = []
    for folder in (tmp_path / "one", tmp_path / "two"):
        folder.mkdir()
        _png(folder / "art.png", (200, 100, 50, 128))
        data = _rig()
        data["nodes"][1].pop("color")
        data["nodes"][1]["image"] = "art.png"
        path = folder / "rig.json"
        path.write_text(json.dumps(data, indent=2 if folder.name == "one" else None))
        paths.append(path)
    a, b = _plan(paths[0]), _plan(paths[1])
    assert a.source == b.source
    _png(paths[1].parent / "art.png", (50, 100, 200, 128))
    assert _plan(paths[1]).source != a.source
    assert _plan(paths[0], seed=1).source != a.source
    assert _plan(paths[0], samples=2).source != a.source
    assert _plan(paths[0], timeout=2).source == a.source
    assert _plan(paths[0], renderer_id="different-ocio").source != a.source


def test_dry_run_has_two_steps_no_generation_and_warm_matches(tmp_path, monkeypatch):
    p = sv.Project()
    p.configure(width=64, height=64, fps=8)
    layer = tmp_path / "layer.py"
    layer.write_text("from scriptvedit import *\n" +
                     f"puppet_clip({repr(_rig())}, duration=.5, blender='absent-blender').time(1)\n")
    p.layer(str(layer))
    monkeypatch.setattr(backend, "_render_batch", lambda *a: pytest.fail("dry_run が Blender を起動した"))
    cold = p.render(str(tmp_path / "out.mp4"), dry_run=True)
    commands = cold["cache"]
    assert sum("--background" in cmd for cmd in commands.values()) == 1
    assert sum("qtrle" in cmd for cmd in commands.values()) == 1
    assert not list(tmp_path.rglob("*.mov"))
    output = next(path for path in commands if path.endswith(".mov"))
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "wb") as stream:
        stream.write(b"warm-cache")
    assert p.render(str(tmp_path / "out.mp4"), dry_run=True) == cold
    assert _is_hold_artifact_path(output)
    assert "stop_mode=clone" in " ".join(cold["main"])


def test_missing_engine_errors_only_for_generation():
    assert _plan(blender="does-not-exist-blender")._generated_length == .5
    with pytest.raises(FileNotFoundError, match="Blender"):
        sv.puppet_clip(_rig(), duration=.5, blender="does-not-exist-blender")


@pytest.mark.parametrize("change", [
    lambda r: r["nodes"][0].update(parent="hand"),
    lambda r: r["nodes"][0].update(parent="missing"),
    lambda r: r["nodes"][1].update(id="root"),
    lambda r: r["nodes"][1].update(position=[float("nan"), 0]),
    lambda r: r["nodes"][1].update(opacity=2),
    lambda r: r["nodes"][1].update(rotate=90),
])
def test_invalid_rig_fails_before_renderer(change, monkeypatch):
    rig = _rig()
    change(rig)
    monkeypatch.setattr(backend, "_render_batch", lambda *a: pytest.fail("不正入力で起動した"))
    with pytest.raises(ValueError):
        sv.puppet_clip(rig, duration=.5)


@pytest.mark.parametrize("track", [
    {"node": "missing", "property": "rotation", "keys": [[0, 2]]},
    {"node": "root", "property": "rotation", "keys": [[1, 2], [0, 3]]},
    {"node": "root", "property": "rotation", "keys": [[0, 2], [0, 3]]},
    {"node": "root", "property": "scale", "keys": [[0, 2]]},
    {"node": "root", "property": "opacity", "keys": [[0, -1]]},
])
def test_invalid_tracks_fail_before_renderer(track):
    with pytest.raises(ValueError):
        _plan(motion={"tracks": [track]})


def test_track_boundaries_and_interpolation():
    track = {"keys": [[.25, [0, 10]], [.75, [10, 20]]], "interpolation": "linear"}
    assert _value(track, 0) == [0, 10]
    assert _value(track, .5) == [5, 15]
    assert _value(track, 1) == [10, 20]
    track["interpolation"] = "step"
    assert _value(track, .74) == [0, 10]
    assert _value(track, .75) == [10, 20]
    track["interpolation"] = "smooth"
    assert _value(track, .375) == [1.5625, 11.5625]


@pytest.mark.realrender
def test_real_blender_alpha_parent_rotation_and_cache(tmp_path, monkeypatch):
    if shutil.which("blender") is None:
        pytest.skip("Blender 実行ファイルが無い環境（通常機能は任意依存）")
    motion = {"tracks": [
        {"node": "root", "property": "rotation", "keys": [[0, 0], [.25, 90]], "interpolation": "step"},
        {"node": "root", "property": "opacity", "keys": [[0, 1], [.375, .5]], "interpolation": "step"},
    ]}
    obj = sv.puppet_clip(_rig(), motion, duration=.5, fps=8, samples=1)
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", obj.source, "-f", "rawvideo",
                           "-pix_fmt", "rgba", "-"], capture_output=True, check=True).stdout
    stride = 64 * 64 * 4
    assert len(data) == stride * 4
    def pixel(frame, x, y):
        start = frame * stride + (y * 64 + x) * 4
        return tuple(data[start:start + 4])
    assert pixel(0, 2, 2)[3] == 0
    assert pixel(0, 32, 32) == (240, 32, 16, 255)
    assert pixel(0, 50, 32) == (16, 224, 48, 255)
    assert pixel(2, 50, 32)[3] == 0
    assert pixel(2, 32, 50) == (16, 224, 48, 255)
    assert 120 <= pixel(3, 32, 50)[3] <= 136
    assert obj._generated_length == .5
    assert obj.has_audio is False
    before = os.stat(obj.source).st_mtime_ns
    monkeypatch.setattr(backend, "_render_batch", lambda *a: pytest.fail("命中時に再描画した"))
    assert sv.puppet_clip(_rig(), motion, duration=.5, fps=8, samples=1).source == obj.source
    assert os.stat(obj.source).st_mtime_ns == before


def test_failed_generation_does_not_publish_cache(tmp_path, monkeypatch):
    class Failure:
        returncode = 1
    def fail(cmd, **kwargs):
        kwargs["stdout"].write(b"worker failed after a partial frame")
        return Failure()
    monkeypatch.setattr(backend.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="worker failed"):
        backend._render_batch({"n_frames": 2}, "blender", str(tmp_path / "clip.mov"), [], 10)
    assert not list(tmp_path.rglob("*.mov"))
    assert not list(tmp_path.glob("puppet_*"))


@pytest.mark.parametrize("size", [(1, 64), (64, 3), (65537, 64), (64, 65537)])
def test_blender_resolution_clamping_is_rejected(size):
    with pytest.raises(ValueError, match="4〜65536"):
        _plan(size=size)
