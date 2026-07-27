# -*- coding: utf-8 -*-
"""実制作で必要になる音声の終端・総尺・マスタリング保護の回帰テスト。"""

import json
import os
import re
import subprocess

import pytest

from scriptvedit import Object, Project, asset, audio_sequence, duck_under


def _asset(relpath):
    try:
        return asset(relpath)
    except FileNotFoundError:
        pytest.skip(f"テスト素材 assets/{relpath} がありません")


def _project():
    p = Project()
    p.configure(width=160, height=90, fps=8, background_color="black")
    return p


def _write_layer(tmp_path, source):
    path = tmp_path / "audio_layer.py"
    path.write_text(source, encoding="utf-8")
    return str(path)


def _run_quiet_ffmpeg(*args):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)],
        check=True,
    )


def _audio_stream(path):
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=duration,sample_rate", "-of", "json",
         str(path)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    streams = json.loads(proc.stdout)["streams"]
    assert streams, "出力に音声ストリームがありません"
    return streams[0]


def _decoded_true_peak(path):
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-map", "0:a:0",
         "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
         "-f", "null", "NUL" if os.name == "nt" else "/dev/null"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    match = re.search(r'\{\s*"input_i".*?\}', proc.stderr, re.DOTALL)
    assert match, proc.stderr
    return float(json.loads(match.group(0))["input_tp"])


def test_audio_sequence_sets_resolved_total_as_duration():
    """time(total) 無しでも連結尺がタイムライン尺として使える。"""
    src1 = _asset("audio/bgm_loop.mp3")
    src2 = _asset("audio/効果音.mp3")
    p = _project()
    p._dry_run = True
    p._pending_compute_cmds = {}

    seq = audio_sequence(src1, src2, crossfade=0.5)

    assert seq.duration == pytest.approx(seq._resolved_length)
    assert seq.duration > 0
    p._layers = [(0, len(p.objects), 0)]
    p._resolve_anchors()
    assert p._calc_total_duration() == pytest.approx(seq.duration)


def test_audio_sequence_uses_audio_stream_duration_for_video_input(tmp_path):
    """映像の方が長い動画でも、連結Objectの尺は音声実尺に一致する。"""
    video = tmp_path / "long_video_short_audio.mp4"
    wav = tmp_path / "one_second.wav"
    _run_quiet_ffmpeg(
        "-f", "lavfi", "-i", "color=red:s=64x64:r=10:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "2", video,
    )
    _run_quiet_ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=660:duration=1", wav,
    )
    p = _project()
    p._dry_run = True
    p._pending_compute_cmds = {}

    seq = audio_sequence(str(video), str(wav), crossfade=0.5)

    assert seq.duration == pytest.approx(1.5, abs=0.08)


def test_audio_sequence_rejects_video_without_audio(tmp_path):
    silent_video = tmp_path / "silent.mp4"
    wav = tmp_path / "one_second.wav"
    _run_quiet_ffmpeg(
        "-f", "lavfi", "-i", "color=red:s=64x64:r=10:d=1",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", silent_video,
    )
    _run_quiet_ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=660:duration=1", wav,
    )
    p = _project()
    p._dry_run = True
    p._pending_compute_cmds = {}

    with pytest.raises(ValueError, match="音声ストリームがありません"):
        audio_sequence(str(silent_video), str(wav), crossfade=0.5)


def test_duck_under_pads_sidechain_and_keeps_bgm_to_project_end(tmp_path):
    """短いナレーションが終わってもBGMの音声ストリームは切れない。"""
    narration = os.path.abspath(_asset("audio/効果音.mp3"))
    bgm = os.path.abspath(_asset("audio/bgm_loop.mp3"))
    layer = _write_layer(
        tmp_path,
        "from scriptvedit import Object, duck_under\n"
        f"narration = Object({narration!r}).show(0.5)\n"
        f"bgm = Object({bgm!r}).time(2.0)\n"
        "bgm <= duck_under(narration)\n",
    )
    p = _project()
    p.layer(layer)

    dry = p.render(str(tmp_path / "duck.mp4"), dry_run=True)
    graph = dry["main"][dry["main"].index("-filter_complex") + 1]
    assert "asplit[dmix" in graph
    assert "]apad[dside" in graph

    output = tmp_path / "duck.mp4"
    p.render(str(output), timeout=60)
    assert float(_audio_stream(output)["duration"]) >= 1.95


def test_normalize_audio_adds_limiter_and_fixed_sample_rate(tmp_path):
    """従来呼び出しでも loudnorm 後のピーク保護と48kHz化が有効。"""
    bgm = os.path.abspath(_asset("audio/bgm_loop.mp3"))
    layer = _write_layer(
        tmp_path,
        "from scriptvedit import Object\n"
        f"Object({bgm!r}).time(1)\n",
    )
    p = _project()
    p.normalize_audio(-16)
    p.layer(layer)

    command = p.render(str(tmp_path / "normalized.mp4"), dry_run=True)["main"]
    graph = command[command.index("-filter_complex") + 1]
    assert "loudnorm=I=-16:TP=-2.0:LRA=11" in graph
    assert "alimiter=limit=0.794328235:attack=5:release=50:level=0:latency=1" in graph
    assert "aresample=48000" in graph
    assert graph.index("aresample=48000") < graph.index("alimiter=")
    assert command[command.index("-ar") + 1] == "48000"
    assert command[command.index("-b:a") + 1] == "160k"

    output = tmp_path / "normalized.mp4"
    p.render(str(output), timeout=60)
    assert _audio_stream(output)["sample_rate"] == "48000"
    # コマンド存在だけでなく、AACデコード後のtrue peakが公開目標内か実測する。
    assert _decoded_true_peak(output) <= -1.5


def test_normalize_audio_custom_controls_and_validation(tmp_path):
    bgm = os.path.abspath(_asset("audio/bgm_loop.mp3"))
    layer = _write_layer(
        tmp_path,
        "from scriptvedit import Object\n"
        f"Object({bgm!r}).time(1)\n",
    )
    p = _project()
    p.normalize_audio(
        -18, true_peak=-2.5, lra=7, limiter=False, sample_rate=None)
    p.layer(layer)

    command = p.render(str(tmp_path / "custom.mp4"), dry_run=True)["main"]
    graph = command[command.index("-filter_complex") + 1]
    assert "loudnorm=I=-18:TP=-3.0:LRA=7" in graph
    assert "alimiter=" not in graph
    assert "aresample=" not in graph
    assert "-ar" not in command

    with pytest.raises(ValueError, match="true_peak"):
        p.normalize_audio(-14, true_peak=-10)
    with pytest.raises(ValueError, match="limiter"):
        p.normalize_audio(-14, limiter=1)
    with pytest.raises(ValueError, match="sample_rate"):
        p.normalize_audio(-14, sample_rate=48000.0)


def test_normalize_audio_rejects_non_48k_opus_output(tmp_path):
    bgm = os.path.abspath(_asset("audio/bgm_loop.mp3"))
    layer = _write_layer(
        tmp_path,
        "from scriptvedit import Object\n"
        f"Object({bgm!r}).time(1)\n",
    )
    p = _project()
    p.normalize_audio(-16, sample_rate=44100)
    p.layer(layer)

    with pytest.raises(ValueError, match="libopus音声は48kHz固定"):
        p.render(str(tmp_path / "normalized.webm"), dry_run=True)


def test_normalize_audio_rejects_sample_rate_unsupported_by_aac(tmp_path):
    bgm = os.path.abspath(_asset("audio/bgm_loop.mp3"))
    layer = _write_layer(
        tmp_path,
        "from scriptvedit import Object\n"
        f"Object({bgm!r}).time(1)\n",
    )
    p = _project()
    p.normalize_audio(-16, sample_rate=384000)
    p.layer(layer)

    with pytest.raises(ValueError, match="AAC出力で未対応"):
        p.render(str(tmp_path / "normalized.mp4"), dry_run=True)
