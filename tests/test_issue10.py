# -*- coding: utf-8 -*-
"""issue #10: 多入力グラフの storyboard 単フレーム抽出回帰テスト"""

import shutil
import wave

import pytest

import scriptvedit as sv


@pytest.mark.skipif(shutil.which("ffmpeg") is None,
                    reason="ffmpeg が無い環境")
def test_storyboard_extracts_one_frame_from_over_50_inputs(tmp_path, monkeypatch):
    """映像専用出力では50入力超でも音声枝を残さず実フレームを抽出できる"""
    image_module = pytest.importorskip("PIL.Image")
    import scriptvedit.preview as preview_module

    image_path = tmp_path / "tile.png"
    image_module.new("RGB", (8, 8), (24, 96, 192)).save(image_path)

    audio_path = tmp_path / "silence.wav"
    with wave.open(str(audio_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 1600)

    layer_path = tmp_path / "many_inputs.py"
    image_literal = repr(str(image_path))
    audio_literal = repr(str(audio_path))
    layer_path.write_text(
        "from scriptvedit import *\n"
        f"image_path = {image_literal}\n"
        "for _ in range(51):\n"
        "    Object(image_path).show(0.2)\n"
        f"Object({audio_literal}).show(0.2)\n",
        encoding="utf-8")

    monkeypatch.setattr(
        preview_module, "_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    project = sv.Project()
    project.configure(width=32, height=18, fps=5, background_color="black")
    project.normalize_audio(-14)
    project.layer(str(layer_path))

    out_path = tmp_path / "storyboard.png"
    assert project.storyboard(str(out_path), cols=1, interval=1.0) == str(out_path)
    assert out_path.is_file()
    with image_module.open(out_path) as result:
        assert result.size == (32, 18)

    # 背景 + 51画像 + 1音声の53入力を保った実グラフであること、および
    # 映像専用出力に未接続の音声終端を生成しないことを明示的に固定する。
    project._thumbnail_at = 0.0
    try:
        cmd = project._build_ffmpeg_cmd(str(tmp_path / "inspect.png"))
    finally:
        project._thumbnail_at = None
    assert cmd.count("-i") == 53
    assert "loudnorm=" not in " ".join(cmd)
