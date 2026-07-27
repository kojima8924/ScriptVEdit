# -*- coding: utf-8 -*-
"""実制作向け narrate() 字幕オプションのユニットテスト。"""

import pytest

import scriptvedit.audio as audio_mod
import scriptvedit.tts as tts_mod
from scriptvedit import Project
from scriptvedit.expr import Const
from scriptvedit.text import _build_drawtext_filter, _text_anchor_xy


class _FakeSubtitle:
    def __init__(self, content, style):
        self.content = content
        self.style = style
        self.source = "text://fake.txt"
        self._text_spec = {"synthetic_source": self.source, **style}
        self.duration = None
        self.fixed_start = None

    def show(self, duration):
        self.duration = duration
        return self

    def __matmul__(self, at):
        self.fixed_start = at
        return self


@pytest.fixture()
def narration_env(monkeypatch):
    """TTS・font・FFmpegに依存せず narrate() の組み立てだけを検証する。"""
    spoken = []
    subtitles = []

    def fake_tts(content, **kwargs):
        spoken.append((content, kwargs))
        return "narration-test.wav"

    def fake_text(content, **style):
        obj = _FakeSubtitle(content, style)
        subtitles.append(obj)
        return obj

    monkeypatch.setattr(tts_mod, "tts", fake_tts)
    monkeypatch.setattr(tts_mod, "tts_duration", lambda path: 2.25)
    monkeypatch.setattr(audio_mod, "text", fake_text)
    return spoken, subtitles


def test_subtitle_text_is_independent_from_spoken_text(narration_env):
    spoken, subtitles = narration_env

    result = audio_mod.narrate(
        "音声ではこちらを読みます",
        subtitle_text="画面には短く表示",
        subtitle_safe_area=0.05)

    assert spoken[0][0] == "音声ではこちらを読みます"
    assert subtitles[0].content == "画面には短く表示"
    assert subtitles[0].duration == result.audio.duration == 2.25
    assert subtitles[0]._text_spec["safe_area"] == (0.05, 0.05, 0.05, 0.05)


def test_formatter_runs_before_japanese_wrapping(narration_env):
    spoken, subtitles = narration_env
    seen = []

    def formatter(content):
        seen.append(content)
        return content.replace("です。", "。")

    audio_mod.narrate(
        "読み上げ原稿",
        subtitle_text="あいうえお、かきくけこです。",
        subtitle_formatter=formatter,
        subtitle_max_chars=5)

    assert seen == ["あいうえお、かきくけこです。"]
    lines = subtitles[0].content.splitlines()
    assert all(len(line) <= 5 for line in lines)
    assert all(not line.startswith("、") for line in lines)
    assert spoken[0][0] == "読み上げ原稿"


def test_japanese_wrap_obeys_opening_and_closing_kinsoku():
    wrapped = audio_mod._wrap_subtitle_text("あいうえ「おかき、くけこ", 5)
    lines = wrapped.splitlines()

    assert all(len(line) <= 5 for line in lines)
    assert all(not line.endswith("「") for line in lines[:-1])
    assert all(not line.startswith("、") for line in lines[1:])
    assert "".join(lines) == "あいうえ「おかき、くけこ"


def test_max_lines_fails_before_calling_tts(narration_env):
    spoken, subtitles = narration_env

    with pytest.raises(ValueError, match="subtitle_max_lines"):
        audio_mod.narrate(
            "読み上げ原稿", subtitle_text="あいうえおかきくけこ",
            subtitle_max_chars=3, subtitle_max_lines=2)

    assert spoken == []
    assert subtitles == []


@pytest.mark.parametrize("name,value", [
    ("subtitle_max_chars", 0),
    ("subtitle_max_chars", True),
    ("subtitle_max_chars", 3.5),
    ("subtitle_max_lines", -1),
])
def test_line_limit_validation_happens_before_tts(narration_env, name, value):
    spoken, _ = narration_env
    with pytest.raises(ValueError, match=name):
        audio_mod.narrate("原稿", **{name: value})
    assert spoken == []


def test_formatter_must_be_callable_and_return_string(narration_env):
    spoken, _ = narration_env
    with pytest.raises(TypeError, match="subtitle_formatter"):
        audio_mod.narrate("原稿", subtitle_formatter="not callable")
    with pytest.raises(TypeError, match="戻り値は文字列"):
        audio_mod.narrate("原稿", subtitle_formatter=lambda _: ["字幕"])
    assert spoken == []


def test_subtitle_false_ignores_subtitle_only_callbacks(narration_env):
    spoken, subtitles = narration_env

    result = audio_mod.narrate(
        "原稿", subtitle=False,
        subtitle_formatter=lambda _: pytest.fail("formatterが呼ばれた"),
        subtitle_max_chars=0, subtitle_safe_area="invalid")

    assert result.subtitle is None
    assert spoken[0][0] == "原稿"
    assert subtitles == []


@pytest.mark.parametrize("value,expected", [
    (0.05, (0.05, 0.05, 0.05, 0.05)),
    ((0.1, 0.2), (0.1, 0.2, 0.1, 0.2)),
    ((0.1, 0.2, 0.3, 0.4), (0.1, 0.2, 0.3, 0.4)),
])
def test_safe_area_normalization(value, expected):
    assert audio_mod._normalize_subtitle_safe_area(value) == expected


@pytest.mark.parametrize("value", [
    True, -0.1, 1.0, (0.5, 0.1, 0.5, 0.1), (0.1,), (0.1, float("nan")),
])
def test_invalid_safe_area_is_rejected(value):
    with pytest.raises(ValueError, match="subtitle_safe_area"):
        audio_mod._normalize_subtitle_safe_area(value)


def test_style_safe_area_overrides_top_level(narration_env):
    _, subtitles = narration_env
    audio_mod.narrate(
        "原稿", subtitle_safe_area=0.05,
        subtitle_style={"safe_area": (0.1, 0.2)})
    assert subtitles[0]._text_spec["safe_area"] == (0.1, 0.2, 0.1, 0.2)


def test_safe_area_participates_in_synthetic_source(monkeypatch):
    monkeypatch.setattr(tts_mod, "tts", lambda *args, **kwargs: "narration-test.wav")
    monkeypatch.setattr(tts_mod, "tts_duration", lambda path: 1.0)
    monkeypatch.setattr(audio_mod, "_text_synthetic_source", lambda value: value)

    subtitle = audio_mod.narrate(
        "原稿", subtitle_safe_area=(0.05, 0.1)).subtitle

    assert "safe_area=(0.05, 0.1, 0.05, 0.1)" in subtitle.source
    assert subtitle._text_spec["synthetic_source"] == subtitle.source


def test_safe_area_clamps_real_text_rectangle():
    x_opt, y_opt = _text_anchor_xy(
        Const(0.99), Const(0.99), "u", "center",
        safe_area=(0.05, 0.06, 0.07, 0.08),
        safe_padding=(11, 12, 13, 14))

    assert "text_w-13" in x_opt
    assert "text_h-14" in y_opt
    assert "0.05*W+11" in x_opt
    assert "0.06*H+12" in y_opt


def test_safe_area_includes_box_border_outline_and_shadow_padding():
    spec = {
        "font": "font.ttf", "x": Const(0.5), "y": Const(0.9),
        "anchor": "center", "safe_area": (0.05, 0.05, 0.05, 0.05),
        "size": Const(36), "alpha": Const(1), "color": "white",
        "box": True, "box_color": "black@0.6", "box_border": 10,
        "border": 2, "border_color": "black",
        "shadow": (-3, 4), "shadow_color": "black@0.6",
    }

    filt = _build_drawtext_filter(spec, "text='字幕'", 0, 1)

    # left=box 10 + border 2 + negative shadow 3; bottom=10+2+4
    assert "0.05*W+15" in filt
    assert "0.05*H+12" in filt
    assert "text_w-12" in filt
    assert "text_h-16" in filt


def test_defaults_preserve_original_subtitle_content_and_position(narration_env):
    _, subtitles = narration_env
    audio_mod.narrate("改行しない既存字幕")

    subtitle = subtitles[0]
    assert subtitle.content == "改行しない既存字幕"
    assert subtitle.style["x"] == 0.5
    assert subtitle.style["y"] == 0.9
    assert "safe_area" not in subtitle._text_spec


def test_narration_matmul_moves_audio_and_subtitle_together(narration_env):
    _, subtitles = narration_env
    narration = audio_mod.narrate("同期配置")

    narration @ 3.5

    assert narration.audio._fixed_start == 3.5
    assert subtitles[0].fixed_start == 3.5


def test_audio_sequence_accepts_narrations_and_keeps_subtitle_offsets(
        narration_env, monkeypatch):
    _, subtitles = narration_env
    project = Project()
    project._dry_run = True
    project._pending_compute_cmds = {}
    monkeypatch.setattr(audio_mod, "_probe_audio_length", lambda path: 0.5)
    first = audio_mod.narrate("一行目", volume=0.5)
    second = audio_mod.narrate("二行目")

    sequence = audio_mod.audio_sequence(first, second, crossfade=0.1)

    cmd = project._pending_compute_cmds[sequence.source]
    filtergraph = cmd[cmd.index("-filter_complex") + 1]
    assert "[0:a]volume=0.5[avol0]" in filtergraph
    assert "[avol0][1:a]acrossfade=d=0.1[axf1]" in filtergraph
    assert sequence.duration == pytest.approx(0.9)
    assert subtitles[0]._timeline_owner is sequence
    assert subtitles[0]._timeline_offset == 0
    assert subtitles[1]._timeline_owner is sequence
    assert subtitles[1]._timeline_offset == pytest.approx(0.4)
    assert first.audio not in project.objects
    assert second.audio not in project.objects

    different_volume = audio_mod.audio_sequence(
        audio_mod.narrate("別音量", volume=0.6),
        audio_mod.narrate("二行目"), crossfade=0.1)
    assert different_volume.source != sequence.source


def test_audio_sequence_narration_subtitles_follow_resolved_sequence_start(
        monkeypatch):
    monkeypatch.setattr(tts_mod, "tts", lambda *args, **kwargs: "fake.wav")
    monkeypatch.setattr(tts_mod, "tts_duration", lambda path: 0.5)
    monkeypatch.setattr(audio_mod, "_probe_audio_length", lambda path: 0.5)
    project = Project()
    project._dry_run = True
    project._pending_compute_cmds = {}
    lead = audio_mod.Object("lead.png").time(5, name="intro")
    first = audio_mod.narrate("一行目")
    second = audio_mod.narrate("二行目")
    sequence = audio_mod.audio_sequence(first, second, crossfade=0.1)
    project._layers = [(0, len(project.objects), 0)]

    project._resolve_anchors()
    assert sequence.start_time == 5
    assert first.subtitle.start_time == 5
    assert second.subtitle.start_time == pytest.approx(5.4)

    sequence @ "intro.end"
    project._resolve_anchors()
    assert sequence.start_time == 5
    assert first.subtitle.start_time == 5
    assert second.subtitle.start_time == pytest.approx(5.4)

    sequence @ 10
    project._resolve_anchors()
    assert sequence.start_time == 10
    assert first.subtitle.start_time == 10
    assert second.subtitle.start_time == pytest.approx(10.4)
