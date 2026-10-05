# -*- coding: utf-8 -*-
"""素材の切り出しとタイムラインの表示尺を音声でも別々に守る回帰。"""

import array
import math
from pathlib import Path
import re
import shutil
import subprocess
import wave

import pytest

from scriptvedit import Project
from scriptvedit.filters.audio import _MIX_AUDIO_FORMAT
from scriptvedit.loudness import _build_loudness_measure_cmd
from scriptvedit.parallel import _build_audio_leg_cmd


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe が無い環境")


@pytest.fixture
def material(tmp_path, monkeypatch):
    """キャッシュも素材も隔離し、5秒の正弦波と3秒の無音を自作する。"""
    monkeypatch.chdir(tmp_path)
    rate = 48000
    for name, duration, amplitude in [("tone", 5, 8000), ("silence", 3, 0)]:
        samples = array.array("h", (
            int(amplitude * math.sin(2 * math.pi * 440 * i / rate))
            for i in range(duration * rate)))
        with wave.open(str(tmp_path / f"{name}.wav"), "wb") as wav:
            wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            wav.writeframes(samples.tobytes())
    return tmp_path


def _project(base, body, *, normalize=False):
    layer = base / "layer.py"
    layer.write_text("from scriptvedit import *\n" + body, encoding="utf-8")
    project = Project()
    project.configure(width=64, height=36, fps=16, background_color="black")
    if normalize:
        project.normalize_audio(-16, mode="linear")
    project.layer(str(layer))
    return project


def _graph(command):
    return command[command.index("-filter_complex") + 1]


def _first_audio_chain(command):
    match = re.search(r"\[\d+:a\]([^;]+)\[a0\]", _graph(command))
    assert match is not None, _graph(command)
    return match.group(1)


@pytest.mark.parametrize(("body", "expected"), [
    pytest.param("Object('tone.wav')[0:5].time(1)",
                 "atrim=duration=5.0,asetpts=PTS-STARTPTS,"
                 "atrim=duration=1,asetpts=PTS-STARTPTS", id="slice_longer"),
    pytest.param("Object('tone.wav')[0:1].time(1)",
                 "atrim=duration=1.0,asetpts=PTS-STARTPTS", id="slice_equal"),
    pytest.param("Object('tone.wav')[0:0.5].time(1)",
                 "atrim=duration=0.5,asetpts=PTS-STARTPTS", id="slice_shorter"),
    pytest.param("a = Object('tone.wav').time(1)\na <= atrim(start=2)",
                 "atrim=start=2,asetpts=PTS-STARTPTS,"
                 "atrim=duration=1,asetpts=PTS-STARTPTS", id="start_only"),
    pytest.param("a = Object('tone.wav')[0:2]\na <= atempo(2)\na.time(1)",
                 "atrim=duration=2.0,asetpts=PTS-STARTPTS,atempo=2,"
                 "atrim=duration=1,asetpts=PTS-STARTPTS",
                 id="tempo_equal"),
    pytest.param("a = Object('tone.wav')[0:2]\na <= atempo(4)\na.time(1)",
                 "atrim=duration=2.0,asetpts=PTS-STARTPTS,atempo=4,"
                 "atrim=duration=1,asetpts=PTS-STARTPTS",
                 id="tempo_shorter"),
    pytest.param("a = Object('tone.wav')[0:1]\na <= atempo(0.5)\na.time(1)",
                 "atrim=duration=1.0,asetpts=PTS-STARTPTS,atempo=0.5,"
                 "atrim=duration=1,asetpts=PTS-STARTPTS", id="tempo_longer"),
    pytest.param("a = Object('tone.wav').time(1)\na <= atempo(2) & atrim(1)",
                 "atempo=2,atrim=duration=1,asetpts=PTS-STARTPTS",
                 id="trim_after_tempo_equal"),
    pytest.param("a = Object('tone.wav').time(1)\na <= atempo(2) & atrim(0.5)",
                 "atempo=2,atrim=duration=0.5,asetpts=PTS-STARTPTS",
                 id="trim_after_tempo_shorter"),
    pytest.param("a = Object('tone.wav').time(1)\na <= atempo(2) & atrim(2)",
                 "atempo=2,atrim=duration=2,asetpts=PTS-STARTPTS,"
                 "atrim=duration=1,asetpts=PTS-STARTPTS",
                 id="trim_after_tempo_longer"),
    pytest.param("a = Object('tone.wav')[0:0.5] * 2\na.time(1)",
                 "atrim=duration=0.5,asetpts=PTS-STARTPTS,"
                 "aloop=loop=1:size=24000,asetpts=N/SR/TB", id="repeat_equal"),
    pytest.param("a = Object('tone.wav')[0:1] * 2\na.time(1)",
                 "atrim=duration=1.0,asetpts=PTS-STARTPTS,"
                 "aloop=loop=1:size=48000,asetpts=N/SR/TB,"
                 "atrim=duration=1,asetpts=PTS-STARTPTS", id="repeat_longer"),
    pytest.param("Object('tone.wav').time(1)",
                 "atrim=duration=1,asetpts=PTS-STARTPTS", id="no_explicit_trim"),
])
def test_final_trim_only_when_required(material, body, expected):
    """既に表示尺以内の前処理にはフィルタを足さず、従来のチェーンを保つ。"""
    project = _project(material, body + "\n")
    dry = project.render(str(material / "out.mp4"), dry_run=True)
    assert _first_audio_chain(dry["main"]) == expected + "," + _MIX_AUDIO_FORMAT


def test_parallel_and_loudness_reuse_display_trim(material, monkeypatch):
    """音声レグと測定パスは共通ビルダーを呼び、配置前に同じ最終トリムを行う。"""
    project = _project(material,
                       "a = Object('tone.wav')[0:5].time(1)\na @ 0.5\n")
    dry = project.render(str(material / "out.mp4"), dry_run=True)
    expected = _first_audio_chain(dry["main"])
    assert "atrim=duration=1,asetpts=PTS-STARTPTS,adelay=500:all=1" in expected
    calls = []
    real_build = project._build_ffmpeg_cmd

    def spy(path):
        calls.append((project._audio_only_render, project._loudness_measure_render))
        return real_build(path)

    monkeypatch.setattr(project, "_build_ffmpeg_cmd", spy)
    leg = _build_audio_leg_cmd(project, str(material / "audio.m4a"))
    measure = _build_loudness_measure_cmd(project)
    assert calls == [(True, False), (False, True)]
    assert _first_audio_chain(leg) == expected
    assert _first_audio_chain(measure) == expected
    assert "loudnorm=print_format=json" in _graph(measure)


def _audio_rms(path, start, duration):
    """AAC を PCM に戻し、指定区間の RMS をフルスケール比で返す。"""
    decoded = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
         "-i", str(path), "-ss", str(start), "-t", str(duration),
         "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
        capture_output=True, check=True, timeout=30)
    samples = array.array("f")
    samples.frombytes(decoded.stdout)
    # 音声ストリーム自体が1秒で途切れた場合を無音と取り違えない。
    assert len(samples) >= int(duration * 48000) - 1
    return math.sqrt(sum(value * value for value in samples) / len(samples))


@pytest.mark.parametrize("normalize", [False, True], ids=["plain", "linear"])
def test_slice_time_real_render_is_silent_after_one_second(material, normalize):
    """全体3秒でも [0:5].time(1) は1秒で鳴り止み、実レンダ前後の dry_run が一致する。"""
    body = ("Object('tone.wav')[0:5].time(1)\n"
            "silence = Object('silence.wav').time(3)\nsilence @ 0\n")
    project = _project(material, body, normalize=normalize)
    output = material / "out.mp4"
    cold = project.render(str(output), dry_run=True)
    if normalize:
        assert cold["cache"]
        assert all(not Path(path).exists() for path in cold["cache"])
    project.render(str(output), timeout=60)
    if normalize:
        assert all(Path(path).exists() for path in cold["cache"])
    warm = _project(material, body, normalize=normalize).render(str(output), dry_run=True)
    assert warm == cold
    assert _audio_rms(output, 0.2, 0.6) > 0.03
    # AAC の境界付近の滲みを避け、1秒より後の複数区間で無音を確認する。
    assert _audio_rms(output, 1.1, 0.4) < 1e-5
    assert _audio_rms(output, 2.0, 0.8) < 1e-5


def test_tempo_audio_leg_stops_at_exact_sample_boundary(material):
    """atempo の理論尺からはみ出す端数も、表示尺のサンプル境界で打ち切る。"""
    project = _project(material,
                       "a = Object('tone.wav')[0:2]\n"
                       "a <= atempo(2)\na.time(1)\n"
                       "silence = Object('silence.wav').time(3)\nsilence @ 0\n")
    project.render(str(material / "out.mp4"), dry_run=True)
    command = _build_audio_leg_cmd(project, str(material / "audio.m4a"))
    # 生成グラフは変えず、AAC の境界の滲みを避けるため非圧縮 PCM にする。
    command[command.index("-c:a") + 1] = "pcm_f32le"
    command[-1:] = ["-f", "f32le", "-"]
    decoded = subprocess.run(command, capture_output=True, check=True, timeout=30)
    samples = array.array("f")
    samples.frombytes(decoded.stdout)
    rate, channels = 48000, 2
    assert len(samples) == 3 * rate * channels
    before_end = samples[int(0.9 * rate) * channels:rate * channels]
    assert math.sqrt(sum(value * value for value in before_end) / len(before_end)) > 0.03
    # 全体の -t で1秒に切っただけの偽成功を防ぎ、3秒までの無音を全数確認する。
    assert max(abs(value) for value in samples[rate * channels:]) < 1e-9
