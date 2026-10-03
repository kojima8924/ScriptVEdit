# -*- coding: utf-8 -*-
"""issue #13 P2-11 / P2-12 の検証

P2-11: Object.length() が映像尺と音声尺を直列に畳み込んでいたため、
音声だけ atrim(1) すると未編集の映像まで 1 秒扱いになっていた。
修正後は stream 別に実効尺を計算し、有効な stream の最大を返す。
delete()/adelete() で消した側や存在しない stream は計算から除外する。

P2-12: 静止画（duration なし）の compute() は Effect を黙って捨てていた。
修正後は日本語 ValueError で明示拒否し、duration を指定した動画 compute では
Effect が生成コマンドに焼き込まれる。
"""
import os
import shutil
import subprocess

import pytest

from scriptvedit import (
    Project, Object, asset, fade, atrim, atempo, adelete, delete, trim,
)


def _require_tools():
    """ffmpeg/ffprobe が無い環境では正直に skip する（PASS 扱いにしない）"""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い環境")
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe が無い環境")


def _mk_project():
    p = Project()
    p.configure(width=640, height=360, fps=30, background_color="black")
    return p


@pytest.fixture(scope="module")
def av_clip(tmp_path_factory):
    """映像+音声つきの合成テスト動画（3秒）を生成する"""
    _require_tools()
    path = tmp_path_factory.mktemp("issue13_av") / "av_3s.mp4"
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc=size=64x64:rate=30:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    return str(path)


# --- P2-11: length() の stream 別尺計算 ------------------------------------

def test_audio_atrim_does_not_shorten_video_length(av_clip):
    """音声だけ atrim(1) しても length() は映像尺（≒元の尺）のまま"""
    _mk_project()
    clip = Object(av_clip)
    base = clip.length()
    assert base > 2.5, f"合成素材の元尺が想定外: {base}"
    clip <= atrim(1)
    assert abs(clip.length() - base) < 0.01, (
        f"音声 atrim が映像尺を縮めた: {clip.length()} (期待 {base})")


def test_adelete_then_video_length_only(av_clip):
    """adelete() 後は音声側が除外され、映像尺のみで決まる"""
    _mk_project()
    clip = Object(av_clip)
    base = clip.length()
    clip <= atrim(0.5)      # 除外されるので影響しないはず
    clip <= adelete()
    assert abs(clip.length() - base) < 0.01


def test_delete_then_audio_length_only(av_clip):
    """delete() 後は映像側が除外され、音声尺（atrim反映）のみで決まる"""
    _mk_project()
    clip = Object(av_clip)
    clip <= atrim(1.0)
    clip <= delete()
    assert abs(clip.length() - 1.0) < 0.01, (
        f"delete() 後の length() が音声尺になっていない: {clip.length()}")


def test_video_trim_with_longer_audio(av_clip):
    """映像を trim(1) しても音声が元尺のままなら length() は音声尺（最大側）"""
    _mk_project()
    clip = Object(av_clip)
    base = clip.length()
    clip <= trim(duration=1.0)
    assert abs(clip.length() - base) < 0.01, (
        f"有効 stream の最大になっていない: {clip.length()} (期待 {base})")


def test_manual_atempo_only_shortens_audio_side(av_clip):
    """音声だけ atempo(2) しても映像尺が最大なので length() は元の尺のまま"""
    _mk_project()
    clip = Object(av_clip)
    base = clip.length()
    clip <= atempo(2.0)
    assert abs(clip.length() - base) < 0.01, (
        f"音声 atempo が全体尺を縮めた: {clip.length()} (期待 {base})")


def test_atempo_on_audio_only_source(tmp_path):
    """音声のみ素材では atempo(2) が length() に反映される（尺/rate）"""
    _require_tools()
    _mk_project()
    wav = tmp_path / "tone_2s.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=2", str(wav)],
        check=True, capture_output=True, timeout=60)
    clip = Object(str(wav))
    clip <= atempo(2.0)
    assert abs(clip.length() - 1.0) < 0.01, (
        f"音声のみ素材で atempo が反映されない: {clip.length()}")


def test_speed_auto_atempo_consistent(av_clip):
    """speed(2) は映像・音声とも 尺/2 になり length() も 尺/2（二重計上なし）"""
    from scriptvedit import speed
    _mk_project()
    clip = Object(av_clip)
    base = clip.length()
    clip <= speed(2.0)
    assert abs(clip.length() - base / 2.0) < 0.01, (
        f"speed の length() 反映が不正: {clip.length()} (期待 {base / 2.0})")


# --- P2-12: 静止画 compute() の Effect 明示拒否 ------------------------------

def test_compute_image_with_effect_rejected():
    """静止画 + fade + duration なし compute() → 日本語 ValueError で明示拒否"""
    _mk_project()
    img = Object(asset("images/shape_badge.png"))
    img <= fade(0.5)
    with pytest.raises(ValueError, match="duration を指定して動画として compute"):
        img.compute()


def test_compute_image_without_effect_still_allowed():
    """Effect の無い静止画 compute() は従来どおり許可（planモードで検証）"""
    p = _mk_project()
    p._mode = "plan"
    try:
        img = Object(asset("images/shape_badge.png"))
        result = img.compute()
        assert result.source.endswith(".png")
        assert result.effects == []
    finally:
        p._mode = "render"


def test_compute_video_bakes_fade_into_command():
    """静止画 + duration あり compute() は fade が生成コマンドに入る（dry_run検証）"""
    p = _mk_project()
    # render() を経由しない dry_run 相当の状態を作る
    p._dry_run = True
    p._pending_compute_cmds = {}
    try:
        img = Object(asset("images/shape_badge.png"))
        img <= fade(0.5)
        img.compute(duration=2.0)
        assert p._pending_compute_cmds, "dry_run で生成コマンドが記録されていない"
        cmd = next(iter(p._pending_compute_cmds.values()))
        vf = cmd[cmd.index("-vf") + 1]
        # 定数の fade はアルファの倍率（colorchannelmixer の aa）で掛ける
        # （filters/video.py の _alpha_mul_filters。以前は geq）
        assert "colorchannelmixer=aa=0.5" in vf, (
            f"fade が生成コマンドに焼き込まれていない: {vf}")
    finally:
        p._dry_run = False
