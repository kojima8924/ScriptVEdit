# -*- coding: utf-8 -*-
"""issue #11: render timeoutの既定値と不完全出力清掃の回帰テスト"""

import subprocess
from pathlib import Path

import pytest

import scriptvedit as sv


def _project():
    project = sv.Project()
    project.configure(width=16, height=16, fps=1, duration=1)
    return project


@pytest.mark.parametrize("timeout", [None, 7])
def test_render_timeout_is_unlimited_by_default_and_explicit_when_requested(
        tmp_path, monkeypatch, timeout):
    """Noneは無制限、数値指定時だけ同じ値を最終ffmpegへ渡す"""
    import scriptvedit.project as project_module

    output = tmp_path / f"success_{timeout}.mp4"
    temp_output = tmp_path / f"success_{timeout}.tmp.mp4"
    seen = []

    def fake_run(cmd, *, timeout, context=None):
        seen.append(timeout)
        Path(cmd[-1]).write_bytes(b"complete")

    monkeypatch.setattr(project_module, "_run_ffmpeg", fake_run)
    monkeypatch.setattr(
        project_module, "_unique_tmp_path", lambda final: str(temp_output))
    kwargs = {} if timeout is None else {"timeout": timeout}
    _project().render(output, **kwargs)
    assert seen == [timeout]
    assert output.read_bytes() == b"complete"
    assert not temp_output.exists()


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("interruption", ["timeout", "keyboard"])
def test_render_removes_partial_mp4_on_timeout_or_keyboard_interrupt(
        tmp_path, monkeypatch, interruption, existing):
    """中断後にpartialを残さず、既存の正常な完成品は保持する"""
    import scriptvedit.project as project_module

    output = tmp_path / f"partial_{interruption}_{existing}.mp4"
    temp_output = tmp_path / f"partial_{interruption}_{existing}.tmp.mp4"
    if existing:
        output.write_bytes(b"previous complete")

    def interrupted_run(cmd, *, timeout, context=None):
        Path(cmd[-1]).write_bytes(b"partial")
        if interruption == "timeout":
            raise subprocess.TimeoutExpired(cmd, timeout)
        raise KeyboardInterrupt

    monkeypatch.setattr(project_module, "_run_ffmpeg", interrupted_run)
    monkeypatch.setattr(
        project_module, "_unique_tmp_path", lambda final: str(temp_output))
    error = subprocess.TimeoutExpired if interruption == "timeout" else KeyboardInterrupt
    with pytest.raises(error):
        _project().render(output, timeout=3)
    if existing:
        assert output.read_bytes() == b"previous complete"
    else:
        assert not output.exists()
    assert not temp_output.exists()


def test_describe_documents_unlimited_render_timeout():
    """AI向けマニフェストにもNone既定と中断時清掃を公開する"""
    manifest = sv.describe(name="render")
    render = manifest["project_methods"][0]
    assert "timeout=None" in render["signature"]
    assert render["params"]["timeout"]["default"] is None
    constraint = next(
        item for item in manifest["constraints"] if item["id"] == "render_timeout")
    assert "無制限" in constraint["text"]
    assert "書きかけ" in constraint["text"]
