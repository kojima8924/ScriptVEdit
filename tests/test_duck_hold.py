# -*- coding: utf-8 -*-
"""duck_under(hold=…): 相手が止んでから戻り始めるまでの保持時間。

sidechaincompress には保持が無く、release だけでは読点や文の間（0.3〜0.6 秒）のたびに
BGM が戻りかける。hold > 0 のときは検出用の枝を「保持つきの包絡」（aeval。
filters/audio.py の _sidechain_hold_filter）へ置き換えてから sidechaincompress へ渡す。
hold=0（既定）のフィルタグラフは以前と同一。
"""

import math
import shutil
import struct
import subprocess
import wave

import pytest

import scriptvedit as sv
from scriptvedit import Object, Project, duck_under
from scriptvedit.context import _exec_stack, activate, current_project
from scriptvedit.filters.audio import _sidechain_hold_filter

_SR = 48000


@pytest.fixture(autouse=True)
def _restore_project_globals():
    old_current = current_project()
    old_stack = list(_exec_stack)
    activate(None)
    _exec_stack[:] = []
    try:
        yield
    finally:
        activate(old_current)
        _exec_stack[:] = old_stack


def _need_ffmpeg():
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg / ffprobe が無い環境")


# --- 構築・検証 ----------------------------------------------------------

def test_hold_defaults_to_zero_and_is_validated():
    p = Project()
    p.configure(width=64, height=36, fps=30)
    v = Object("v.wav")
    assert duck_under(v).params["hold"] == 0
    assert duck_under(v, hold=400).params["hold"] == 400
    for bad in (-1, "400", float("nan"), None):
        with pytest.raises((ValueError, TypeError)):
            duck_under(v, hold=bad)


def test_hold_filter_expression():
    f = _sidechain_hold_filter(0.05, 20, 250, 400)
    assert f.startswith("aeval='st(0\\,") and f.endswith("':c=same")
    # 追従の係数は sidechaincompress と同じ 1/(ms × rate / 4000)
    assert repr(1 / (20 * _SR / 4000)) in f and repr(1 / (250 * _SR / 4000)) in f
    assert f"\\,{int(0.4 * _SR)}\\,ld(1)-1" in f           # 保持のサンプル数
    assert repr(0.05 * 0.05) in f                            # threshold²
    assert "|" not in f                                      # aeval のチャンネル区切りを含めない
    # attack / release が 0 でもゼロ除算しない（係数 1 = 即時）
    assert "\\,1.0\\,1.0))" in _sidechain_hold_filter(0.05, 0, 0, 100)


# --- フィルタグラフ ------------------------------------------------------

def _write_wav(path, samples, channels=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(_SR)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32767))
                               for s in samples))


def _voice(seconds, bursts):
    """bursts の区間だけ鳴る 180Hz の「声」（モノラル）"""
    out = []
    for i in range(int(seconds * _SR)):
        t = i / _SR
        on = any(a <= t < b for a, b in bursts)
        out.append(0.3 * math.sin(2 * math.pi * 180 * t) if on else 0.0)
    return out


def _bgm_left_only(seconds):
    """左チャンネルだけの 440Hz（L−R で声を打ち消して BGM だけ測るため）"""
    out = []
    for i in range(int(seconds * _SR)):
        out.append(0.5 * math.sin(2 * math.pi * 440 * i / _SR))
        out.append(0.0)
    return out


_BURSTS = [(0.5, 1.1), (1.45, 2.05), (2.4, 3.0)]     # 間は 0.35 秒（読点・文の間に相当）


def _layer(tmp_path, hold, release=250, extra=""):
    voice, bgm = tmp_path / "voice.wav", tmp_path / "bgm.wav"
    if not voice.exists():
        _write_wav(voice, _voice(6, _BURSTS))
        _write_wav(bgm, _bgm_left_only(6), channels=2)
    layer = tmp_path / f"l_duck_{hold}_{release}{'x' if extra else ''}.py"
    layer.write_text(
        "from scriptvedit import *\n"
        f"v = Object({str(voice)!r})\n"
        "v.time(6)\n"
        f"{extra}"
        f"bg = Object({str(bgm)!r})\n"
        f"(bg @ 0).show(6) <= duck_under(v{', v2' if extra else ''}, ratio=8, "
        f"threshold=0.05, attack=20, release={release}, hold={hold})\n",
        encoding="utf-8")
    p = Project()
    p.configure(width=64, height=36, fps=30, background_color="black")
    p.layer(str(layer), priority=1)
    return p


def _audio_graph(p, tmp_path):
    cmd = p.render(str(tmp_path / "dry.mp4"), dry_run=True)["main"]
    return cmd[cmd.index("-filter_complex") + 1]


def test_graph_without_hold_is_unchanged(tmp_path):
    _need_ffmpeg()
    g = _audio_graph(_layer(tmp_path, 0), tmp_path)
    assert "aeval" not in g
    assert "[dside_src1]aformat=sample_fmts=fltp:sample_rates=48000,apad[dside1]" in g


def test_graph_with_hold_builds_envelope_after_apad(tmp_path):
    _need_ffmpeg()
    g = _audio_graph(_layer(tmp_path, 600), tmp_path)
    hold = _sidechain_hold_filter(0.05, 20, 250, 600)
    # 相手が1つでも 48kHz モノラルへまとめ、apad の後で包絡にする
    assert (f"[dside_src1]aresample=48000:ochl=mono:rematrix_maxval=1,apad,{hold}[dside1]"
            in g), g
    assert "sidechaincompress=threshold=0.05:ratio=8:attack=20:release=250[duck1]" in g


def test_graph_with_hold_and_multiple_targets(tmp_path):
    _need_ffmpeg()
    extra = "v2 = Object(str(v.source))\nv2.show(6)\n"
    g = _audio_graph(_layer(tmp_path, 300, extra=extra), tmp_path)
    hold = _sidechain_hold_filter(0.05, 20, 250, 300)
    assert f"amix=inputs=2:normalize=0,apad,{hold}[dside" in g, g


# --- 実レンダ ------------------------------------------------------------

def _bgm_levels(video):
    """L−R（声は中央なので打ち消される）の 100ms ごとの RMS（dB）"""
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-af", "pan=mono|c0=c0-c1", "-f", "s16le", "-ar", str(_SR), "-"],
        check=True, capture_output=True, timeout=120).stdout
    vals = struct.unpack(f"<{len(raw) // 2}h", raw)
    win = _SR // 10
    out = []
    for i in range(0, len(vals) - win + 1, win):
        ms = sum(v * v for v in vals[i:i + win]) / win
        out.append(20 * math.log10(math.sqrt(ms) / 32768 + 1e-9))
    return out


def test_real_render_hold_keeps_bgm_down_across_short_gaps(tmp_path):
    """0.35 秒の間で、hold なしは元の音量まで戻り、hold=600 は下がったまま。止んだ後は戻る"""
    _need_ffmpeg()
    levels = {}
    for hold in (0, 600):
        out = tmp_path / f"out_{hold}.mp4"
        _layer(tmp_path, hold).render(str(out), timeout=300)
        levels[hold] = _bgm_levels(out)
    plain, held = levels[0], levels[600]
    base = plain[2]                        # 声の前（下がっていない BGM）
    assert held[2] == pytest.approx(base, abs=0.5)
    # 発声中（0.7〜1.0 秒）はどちらも 8dB 以上下がり、下げ幅の差は 2.5dB 以内
    for i in (7, 8, 9):
        assert plain[i] < base - 8 and held[i] < base - 8, (plain[:12], held[:12])
        assert abs(plain[i] - held[i]) < 2.5, (plain[i], held[i])
    # 間（1.2〜1.4 秒 / 2.2〜2.3 秒）: hold なしは 2dB 以内まで戻る、hold ありは 7dB 以上下がったまま
    for i in (13, 23):
        assert plain[i] > base - 2, (i, plain[i], base)
        assert held[i] < base - 7, (i, held[i], base)
    # 最後の発声が 3.0 秒で止む → 0.6 秒は保持（3.4 秒は下がったまま）、4.2 秒には戻っている
    assert held[34] < base - 7, held[30:45]
    assert held[42] == pytest.approx(base, abs=0.5), held[30:45]
    assert plain[34] == pytest.approx(base, abs=1.0), plain[30:45]


def test_describe_lists_hold():
    entry = [e for e in sv.describe(name="duck_under")["audio_effects"]
             if e["name"] == "duck_under"][0]
    assert entry["params"]["hold"]["default"] == 0
    assert any("hold" in n for n in entry["notes"])
