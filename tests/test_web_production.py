# -*- coding: utf-8 -*-
"""長尺Web Objectのdraft・部分レンダ・cache改善テスト。"""

import sys
import types
import json
import shutil
import subprocess

import pytest

from scriptvedit import Effect, Object, Project
from scriptvedit.cache import (
    _file_fingerprint,
    _web_cache_path,
    _web_source_fingerprint,
)


def _html(tmp_path, content="<script>function renderFrame(s){}</script>"):
    path = tmp_path / "canvas.html"
    path.write_bytes(content.encode("utf-8"))
    return str(path)


def _project(*, fps=10, duration=10):
    project = Project()
    project.configure(width=320, height=180, fps=fps, duration=duration)
    return project


def test_web_source_fingerprint_canonicalizes_only_line_endings(tmp_path):
    crlf = tmp_path / "crlf.html"
    lf = tmp_path / "lf.html"
    changed = tmp_path / "changed.html"
    crlf.write_bytes(b"<html>\r\nA\r\n</html>\r\n")
    lf.write_bytes(b"<html>\nA\n</html>\n")
    changed.write_bytes(b"<html>\nB\n</html>\n")

    assert _web_source_fingerprint(crlf) == _file_fingerprint(crlf)
    assert _web_source_fingerprint(crlf) == _web_source_fingerprint(lf)
    assert _web_source_fingerprint(lf) != _web_source_fingerprint(changed)


def test_partial_capture_uses_only_intersecting_frames(tmp_path):
    project = _project()
    obj = Object(_html(tmp_path), duration=10, size=(320, 180), fps=10)
    project._render_window = (7.0, 8.0)

    spec = obj._web_capture_spec(project)

    assert spec == {
        "base_fps": 10,
        "fps": 10,
        "full_frames": 100,
        "frame_start": 70,
        "frame_end": 80,
        "visible": True,
    }
    command = obj._build_web_cmd(project, "partial.webm", "frames")
    graph = command[command.index("-filter_complex") + 1]
    assert "setpts=PTS-STARTPTS+7.0/TB" in graph
    assert "overlay=eof_action=pass:repeatlast=0" in graph
    assert any(
        "color=c=black@0.0:s=320x180:d=10:r=10" in arg
        for arg in command)


def test_partial_capture_warms_state_but_screenshots_window_only(
        tmp_path, monkeypatch):
    project = _project()
    obj = Object(_html(tmp_path), duration=10, size=(320, 180), fps=10)
    project._render_window = (7.0, 8.0)
    states = []
    screenshots = []

    class FakePage:
        def goto(self, url):
            self.url = url

        def wait_for_function(self, expression, timeout):
            return None

        def evaluate(self, expression, value=None):
            if isinstance(value, dict):
                states.append(value)

        def screenshot(self, *, path, omit_background):
            screenshots.append(path)

    class FakeBrowser:
        def new_page(self, viewport):
            return FakePage()

        def close(self):
            return None

    class FakeChromium:
        def launch(self):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeContext:
        def __enter__(self):
            return FakePlaywright()

        def __exit__(self, *args):
            return False

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: FakeContext()
    package = types.ModuleType("playwright")
    package.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    obj._render_web_frames(project, str(tmp_path / "frames"))

    assert len(states) == 80
    assert len(screenshots) == 10
    assert states[70]["frame"] == 70
    assert states[70]["t"] == 7.0
    assert states[70]["u"] == pytest.approx(70 / 99)
    assert screenshots[0].endswith("frame_00000.png")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpegがありません")
def test_partial_web_command_builds_full_duration_aligned_clip(tmp_path):
    image_module = pytest.importorskip("PIL.Image")
    project = _project(fps=2, duration=3)
    obj = Object(_html(tmp_path), duration=3, size=(64, 36), fps=2)
    project._render_window = (1.0, 2.0)
    frames = tmp_path / "frames"
    frames.mkdir()
    image_module.new("RGBA", (64, 36), (255, 0, 0, 255)).save(
        frames / "frame_00000.png")
    image_module.new("RGBA", (64, 36), (0, 0, 255, 255)).save(
        frames / "frame_00001.png")
    output = tmp_path / "partial.webm"

    subprocess.run(
        obj._build_web_cmd(project, str(output), str(frames)),
        check=True, capture_output=True, timeout=30)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(output)],
        check=True, capture_output=True, text=True, encoding="utf-8")

    assert float(json.loads(probe.stdout)["format"]["duration"]) == pytest.approx(3.0)

    def pixel_at(at):
        decoded = subprocess.run(
            ["ffmpeg", "-v", "error", "-c:v", "libvpx-vp9",
             "-ss", str(at), "-i", str(output), "-frames:v", "1",
             "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"],
            check=True, capture_output=True, timeout=30).stdout
        assert len(decoded) == 64 * 36 * 4
        return tuple(decoded[:4])

    # 指定窓の前後は透明、窓内は元frame。full-durationの黒画面化や
    # repeatlastによる窓外残留をduration検査だけで見逃さない。
    assert pixel_at(0.5)[3] == 0
    inside = pixel_at(1.25)
    assert max(inside[:3]) >= 240 and inside[3] >= 240
    assert pixel_at(2.5)[3] == 0


def test_draft_web_fps_has_separate_cache_and_can_be_disabled(tmp_path):
    project = _project(fps=30)
    project.configure(draft_web_fps=8)
    obj = Object(_html(tmp_path), duration=10, size=(320, 180), fps=15)

    project._draft = False
    final_path = _web_cache_path(obj, project)
    assert obj._web_capture_spec(project)["fps"] == 15

    project._draft = True
    draft_path = _web_cache_path(obj, project)
    assert obj._web_capture_spec(project)["fps"] == 8
    assert draft_path != final_path
    command = obj._build_web_cmd(project, draft_path, "frames")
    assert command[command.index("-framerate") + 1] == "8"

    project.configure(draft_web_fps=None)
    assert _web_cache_path(obj, project) == final_path


@pytest.mark.parametrize("value", [0, -1, True, float("nan")])
def test_draft_web_fps_validation(value):
    project = Project()
    with pytest.raises(ValueError, match="draft_web_fps"):
        project.configure(draft_web_fps=value)


def test_trimmed_web_falls_back_to_full_capture(tmp_path):
    project = _project()
    obj = Object(_html(tmp_path), duration=10, size=(320, 180), fps=10)
    obj.effects.append(Effect("trim", start=1, duration=5))
    project._render_window = (7.0, 8.0)

    spec = obj._web_capture_spec(project)

    assert spec["frame_start"] == 0
    assert spec["frame_end"] == spec["full_frames"]


def test_web_outside_partial_window_is_pruned(tmp_path):
    project = _project(duration=20)
    outside = Object(
        _html(tmp_path), duration=5, size=(320, 180), name="outside")
    outside.start_time = 12
    project._render_window = (0.0, 5.0)

    project._prune_window_invisible_web_objects()

    assert outside not in project.objects


def test_web_prune_keeps_layer_slice_boundaries(tmp_path):
    project = _project(duration=20)
    outside = Object(
        _html(tmp_path), duration=5, size=(320, 180), name="outside")
    outside.start_time = 12
    first_layer_image = Object(str(tmp_path / "a.png"))
    second_layer_image = Object(str(tmp_path / "b.png"))
    project._layers = [(0, 2, 10), (2, 3, 20)]
    project._render_window = (0.0, 5.0)

    project._prune_window_invisible_web_objects()

    assert project.objects == [first_layer_image, second_layer_image]
    assert project._layers == [(0, 1, 10), (1, 2, 20)]


def test_storyboard_extracts_all_project_frames_in_one_ffmpeg(
        tmp_path, monkeypatch):
    image_module = pytest.importorskip("PIL.Image")
    import scriptvedit.project as project_module

    image_path = tmp_path / "still.png"
    image_module.new("RGB", (32, 18), (10, 80, 160)).save(image_path)
    layer_path = tmp_path / "layer.py"
    layer_path.write_text(
        "from scriptvedit import *\n"
        f"Object({str(image_path)!r}).time(2)\n",
        encoding="utf-8")
    project = _project(fps=10, duration=2)
    project.layer(str(layer_path))

    calls = []
    real_run = project_module._run_ffmpeg

    def spy(command, **kwargs):
        calls.append(command)
        return real_run(command, **kwargs)

    monkeypatch.setattr(project_module, "_run_ffmpeg", spy)
    output = tmp_path / "board.png"

    project.storyboard(str(output), cols=2, interval=0.5)

    assert output.is_file()
    assert len(calls) == 1
    command_text = " ".join(calls[0])
    assert "select='eq(n\\,0)+eq(n\\,5)+eq(n\\,10)+eq(n\\,15)'" in command_text
    assert "-frames:v 4" in command_text


def test_thumbnail_can_seek_existing_render_without_rebuilding_project(
        tmp_path, monkeypatch):
    import scriptvedit.project as project_module

    source = tmp_path / "rendered.mp4"
    source.write_bytes(b"placeholder")
    project = _project()
    monkeypatch.setattr(
        project, "_probe_media", lambda path: {
            "has_video": True,
            "duration": 30.0,
            "video_duration": 10.0,
        })
    calls = []
    output = tmp_path / "thumb.png"
    output.write_bytes(b"old")

    def fake_run(command, **kwargs):
        calls.append(command)
        with open(command[-1], "wb") as frame:
            frame.write(b"new-frame")

    monkeypatch.setattr(project_module, "_run_ffmpeg", fake_run)

    project.thumbnail(7.5, str(output), source=str(source))

    assert len(calls) == 1
    command = calls[0]
    assert command.index("-ss") < command.index("-i")
    assert command[command.index("-ss") + 1] == "7.5"
    assert str(source.resolve()) in command
    assert command[-1] != str(output.resolve())
    assert command[-1].endswith(".png")
    assert output.read_bytes() == b"new-frame"


@pytest.mark.parametrize(
    ("info", "at", "message"),
    [
        ({"has_video": False, "duration": 30.0,
          "video_duration": None}, 0.0, "映像ストリーム"),
        ({"has_video": True, "duration": 30.0,
          "video_duration": 5.0}, 5.0, r"素材尺\(5.0\)未満"),
    ],
)
def test_source_thumbnail_rejects_audio_only_and_video_out_of_range_early(
        tmp_path, monkeypatch, info, at, message):
    import scriptvedit.project as project_module

    source = tmp_path / "source.mp4"
    source.write_bytes(b"placeholder")
    project = _project()
    monkeypatch.setattr(project, "_probe_media", lambda path: info)
    calls = []
    monkeypatch.setattr(
        project_module, "_run_ffmpeg",
        lambda command, **kwargs: calls.append(command))

    with pytest.raises(ValueError, match=message):
        project.thumbnail(
            at, str(tmp_path / "should_not_exist.png"), source=str(source))

    assert calls == []


def test_source_thumbnail_keeps_existing_output_when_extraction_is_empty(
        tmp_path, monkeypatch):
    import scriptvedit.project as project_module

    source = tmp_path / "source.mp4"
    source.write_bytes(b"placeholder")
    output = tmp_path / "thumb.png"
    output.write_bytes(b"previous-frame")
    project = _project()
    monkeypatch.setattr(
        project, "_probe_media", lambda path: {
            "has_video": True,
            "duration": 10.0,
            "video_duration": 10.0,
        })
    monkeypatch.setattr(
        project_module, "_run_ffmpeg", lambda command, **kwargs: None)

    with pytest.raises(RuntimeError, match="抽出結果"):
        project.thumbnail(1.0, str(output), source=str(source))

    assert output.read_bytes() == b"previous-frame"


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobeがありません")
def test_source_thumbnail_uses_format_duration_for_real_vp9_webm(tmp_path):
    image_module = pytest.importorskip("PIL.Image")

    source = tmp_path / "rendered.webm"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            "color=c=0x285080:s=64x36:r=10:d=0.6",
            "-an", "-c:v", "libvpx-vp9", "-deadline", "realtime",
            "-cpu-used", "8", str(source),
        ],
        check=True, capture_output=True, timeout=30)
    project = _project()

    info = project._probe_media(str(source))
    assert info["has_video"] is True
    # VP9 WebMはstream尺は通常Noneだが、format尺は取得できる。
    assert info["video_duration"] is None
    assert info["duration"] == pytest.approx(0.6, abs=0.1)

    output = tmp_path / "thumb.png"
    project.thumbnail(0.2, str(output), source=str(source))

    assert output.stat().st_size > 0
    with image_module.open(output) as frame:
        assert frame.size == (64, 36)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpegがありません")
def test_storyboard_reuses_duplicate_frames_and_clamps_tail_in_one_ffmpeg(
        tmp_path, monkeypatch):
    image_module = pytest.importorskip("PIL.Image")
    import scriptvedit.project as project_module

    image_path = tmp_path / "still.png"
    image_module.new("RGB", (32, 18), (30, 120, 210)).save(image_path)
    layer_path = tmp_path / "layer.py"
    layer_path.write_text(
        "from scriptvedit import *\n"
        f"Object({str(image_path)!r}).time(0.26)\n",
        encoding="utf-8")
    project = _project(fps=10, duration=0.26)
    project.layer(str(layer_path))

    calls = []
    real_run = project_module._run_ffmpeg

    def spy(command, **kwargs):
        calls.append(command)
        return real_run(command, **kwargs)

    monkeypatch.setattr(project_module, "_run_ffmpeg", spy)
    output = tmp_path / "dense-board.png"

    project.storyboard(str(output), cols=3, interval=0.05)

    assert output.is_file()
    with image_module.open(output) as board:
        # 6つの要求時刻が3つのframe PNGを再利用して全枠を構成する。
        assert board.size == (968, 364)
    assert len(calls) == 1
    command_text = " ".join(calls[0])
    assert "select='eq(n\\,0)+eq(n\\,1)+eq(n\\,2)'" in command_text
    assert "eq(n\\,3)" not in command_text
    assert "-frames:v 3" in command_text
