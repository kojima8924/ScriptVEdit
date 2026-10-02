# -*- coding: utf-8 -*-
"""音声を混ぜる前の形式統一（48kHz・ステレオ・fltp）のテスト。

amix / sidechaincompress / acrossfade の出力のチャンネル配置とサンプリング
周波数は「先頭入力」に従う（FFmpeg 8 で実測）。入力の並びは priority 順＋
生成順なので、モノラル 24kHz の TTS が先頭に来るだけで章全体が 24kHz・
モノラルになり、ステレオの BGM が L/R 平均に潰れていた。

- 各音声入力の加工チェーン末尾に _MIX_AUDIO_FORMAT が付く（生入力参照は作らない）
- aloop / adelay より後ろ（aloop の size は素材の実サンプルレート基準のため）
- 単一音声・duck_under・normalize_audio・並列レンダの音声レグでも同じ
- ただし duck_under の検出用枝だけは揃える**前**から取る（ステレオ化の -3dB が
  sidechaincompress の検出に乗ると、モノラルのナレーションでダッキングが浅くなる）
- audio_sequence / video_sequence の acrossfade 入力も揃え、鍵にも反映する
- from_project の鍵の audio_graph は音声のあるサブプロジェクトだけ上げる
  （映像だけなら出力が変わらないので据え置く＝同一出力なら同一鍵）
- 実レンダ: モノラル 24kHz を先頭、ステレオ 44.1kHz を2番目に置いても
  出力は 48kHz・2ch で、ステレオ側の左右差が保たれる
"""
import json
import re
import shutil
import subprocess

import pytest

import scriptvedit.audio as audio_mod
import scriptvedit.media as media_mod
import scriptvedit.objects as objects_mod
import scriptvedit.parallel as parallel_mod
from scriptvedit import Project
from scriptvedit.context import _exec_stack, activate, current_project
from scriptvedit.filters.audio import _MIX_AUDIO_FORMAT, _SIDECHAIN_FORMAT

AF = _MIX_AUDIO_FORMAT

_HAS_FFMPEG = (shutil.which("ffmpeg") is not None
               and shutil.which("ffprobe") is not None)
needs_ffmpeg = pytest.mark.skipif(
    not _HAS_FFMPEG, reason="ffmpeg/ffprobe が無い環境ではスキップ")


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


# --- 素材と計測のヘルパー ------------------------------------------------------

def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args],
                   check=True, capture_output=True, timeout=60)


def _mono24k(tmp_path, seconds=2, freq=300, name="mono24k.wav"):
    """モノラル 24kHz（edge-tts / VOICEVOX の典型形式）の正弦波"""
    wav = tmp_path / name
    _ffmpeg("-f", "lavfi", "-i",
            f"sine=frequency={freq}:sample_rate=24000:duration={seconds}",
            "-ac", "1", str(wav))
    return str(wav)


def _stereo44k(tmp_path, seconds=2, freq=3000, name="stereo44k.wav"):
    """ステレオ 44.1kHz。左だけに freq Hz、右は無音（左右差の検出用）"""
    wav = tmp_path / name
    _ffmpeg("-f", "lavfi", "-i",
            f"sine=frequency={freq}:sample_rate=44100:duration={seconds}",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-filter_complex",
            f"[0:a][1:a]amerge=inputs=2,atrim=duration={seconds}[a]",
            "-map", "[a]", str(wav))
    return str(wav)


def _audio_stream(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,channels", "-of", "json", path],
        capture_output=True, text=True, check=True, timeout=30).stdout
    s = json.loads(out)["streams"][0]
    return int(s["sample_rate"]), int(s["channels"])


def _band_rms_db(path, channel, freq, t0=0.2, dur=1.0):
    """channel（0=左/1=右）の freq Hz 付近（±50Hz）の RMS[dB]。無音は -inf"""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-ss", str(t0), "-t", str(dur), "-i", path,
         "-af", f"pan=mono|c0=c{channel},"
                f"bandpass=f={freq}:width_type=h:w=100,astats=metadata=0",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    levels = re.findall(r"RMS level dB: (-inf|-?[\d.]+)", proc.stderr)
    assert levels, proc.stderr[-500:]
    return float(levels[-1])


def _mk():
    p = Project()
    p.configure(width=160, height=90, fps=10, background_color="black")
    return p


def _layer(tmp_path, body, name="l_afmt.py"):
    path = tmp_path / name
    path.write_text("from scriptvedit import *\n" + body, encoding="utf-8")
    return str(path)


def _graph(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


def _maps(cmd):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]


def _input_paths(cmd):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"]


_CHAIN_RE = re.compile(r"^\[(\d+):a\](.+)\[(a\d+)\]$")


def _audio_chains(graph):
    """{ラベル: (入力index, チェーンのフィルタ列)}"""
    chains = {}
    for part in graph.split(";"):
        m = _CHAIN_RE.match(part)
        if m:
            chains[m.group(3)] = (int(m.group(1)), m.group(2).split(","))
    return chains


def _assert_no_raw_audio_refs(graph):
    """[N:a] はチェーンの先頭にしか現れない（生入力を直接混ぜない）。
    duck_under の相手のチェーンは [N:a]…,asplit[apreK][検出用…] で終わる"""
    for part in graph.split(";"):
        refs = re.findall(r"\[\d+:a\]", part)
        if _CHAIN_RE.match(part):
            assert len(refs) == 1, part
        elif refs:
            assert len(refs) == 1 and part.startswith(refs[0]), part
            assert re.search(r",?asplit(=\d+)?\[apre\d+\](\[dside_src[\d_]+\])+$",
                             part), part


# --- フィルタグラフ（dry_run）----------------------------------------------------

@needs_ffmpeg
def test_every_input_chain_ends_with_aformat_before_amix(tmp_path):
    """モノラル 24kHz が先頭でも、amix の全入力が 48kHz・ステレオに揃う"""
    mono = _mono24k(tmp_path)
    stereo = _stereo44k(tmp_path)
    p = _mk()
    p.layer(_layer(tmp_path,
                   f"voice = Object({mono!r}).show(2)\n"
                   f"bgm = Object({stereo!r}).time(2)\n"))
    cmd = p.render(str(tmp_path / "o.mp4"), dry_run=True)["main"]
    graph = _graph(cmd)
    chains = _audio_chains(graph)
    assert set(chains) == {"a0", "a1"}
    for label, (_, filters) in chains.items():
        assert filters[-1] == AF, (label, filters)
    # 先頭（a0）が本当にモノラル 24kHz の素材であること（前提の確認）
    inputs = _input_paths(cmd)
    assert inputs[chains["a0"][0]].endswith("mono24k.wav")
    assert inputs[chains["a1"][0]].endswith("stereo44k.wav")
    assert "[a0][a1]amix=inputs=2:normalize=0[aout]" in graph
    _assert_no_raw_audio_refs(graph)
    assert _maps(cmd)[-1] == "[aout]"


@needs_ffmpeg
def test_single_raw_audio_becomes_labeled_chain(tmp_path):
    """加工の無い単一音声も生入力参照（-map N:a）ではなく aformat チェーンになる"""
    mono = _mono24k(tmp_path)
    p = _mk()
    # time() も付けない素の音声Object（atrim も adelay も付かない）。修正前は
    # フィルタグラフ自体が作られず、-map も無いまま ffmpeg の既定選択に任せていた
    p.layer(_layer(tmp_path, f"voice = Object({mono!r})\n"))
    cmd = p.render(str(tmp_path / "o.mp4"), dry_run=True)["main"]
    graph = _graph(cmd)
    assert graph == f"[1:a]{AF}[a0]"
    # 映像Objectが無いので映像は背景キャンバスのストリーム指定、音声はラベル
    assert _maps(cmd) == ["0:v", "[a0]"]
    assert not any(re.fullmatch(r"\d+:a", m) for m in _maps(cmd))


@needs_ffmpeg
def test_aformat_is_last_after_aloop_volume_and_adelay(tmp_path):
    """aloop の size は素材の実SR基準なので、aformat は必ずチェーン末尾"""
    mono = _mono24k(tmp_path, seconds=1)
    stereo = _stereo44k(tmp_path, seconds=1)
    p = _mk()
    p.layer(_layer(tmp_path,
                   f"a = Object({stereo!r}).show(1)\n"
                   f"bgm = Object({mono!r})\n"
                   "bgm <= loop(until=4)\n"
                   "bgm <= avolume(0.5)\n"
                   "bgm @ 1\n"))
    graph = _graph(p.render(str(tmp_path / "o.mp4"), dry_run=True)["main"])
    chains = _audio_chains(graph)
    looped = next(f for _, f in chains.values() if f[0].startswith("aloop="))
    names = [f.split("=", 1)[0] for f in looped]
    assert names[-1] == "aformat", looped
    assert names.index("aloop") < names.index("volume") \
        < names.index("adelay") < names.index("aformat"), looped
    # aloop の size はモノラル 24kHz 素材の実SRで見積もる（48000 基準ではない）
    size = int(re.search(r"aloop=loop=-1:size=(\d+)", looped[0]).group(1))
    assert size == 24000 * 1 + 24000


@needs_ffmpeg
def test_duck_under_sidechain_taps_before_format(tmp_path):
    """sidechaincompress の本線・ミックス・loudnorm の入力は揃えた後の音声で、
    検出用枝だけは揃える前（モノラルのまま）から取る"""
    mono = _mono24k(tmp_path)
    stereo = _stereo44k(tmp_path)
    p = _mk()
    p.normalize_audio(-14)
    p.layer(_layer(tmp_path,
                   f"voice = Object({mono!r}).show(2)\n"
                   f"bgm = Object({stereo!r}).time(2)\n"
                   "bgm <= duck_under(voice)\n"))
    cmd = p.render(str(tmp_path / "o.mp4"), dry_run=True)["main"]
    graph = _graph(cmd)
    parts = graph.split(";")
    # BGM（本線）は1本のチェーンで末尾が統一形式
    chains = _audio_chains(graph)
    assert set(chains) == {"a1"} and chains["a1"][1][-1] == AF
    # ナレーションは統一形式の直前で分岐し、ミックス用だけを揃える
    head = next(x for x in parts if x.startswith("[1:a]"))
    assert head.endswith(",asplit[apre0][dside_src1]"), head
    assert "aformat" not in head
    assert f"[apre0]{AF}[a0]" in parts
    # 検出用枝はチャンネル構成を変えず、周波数だけ本線へ揃える
    assert f"[dside_src1]{_SIDECHAIN_FORMAT},apad[dside1]" in parts
    assert "[a1][dside1]sidechaincompress=" in graph
    assert "[a0][duck1]amix=inputs=2:normalize=0[aout]" in parts
    assert "[aout]loudnorm=" in graph
    # 最終SRは normalize_audio の既定（48000）＝混ぜる前の統一形式と同じ
    assert "aresample=48000" in graph
    assert cmd[cmd.index("-ar") + 1] == "48000"
    _assert_no_raw_audio_refs(graph)


@needs_ffmpeg
def test_normalize_audio_single_input_uses_bracketed_label(tmp_path):
    """単一音声 + normalize_audio でも loudnorm の入力はラベル付きチェーン"""
    mono = _mono24k(tmp_path)
    p = _mk()
    p.normalize_audio(-16)
    p.layer(_layer(tmp_path, f"voice = Object({mono!r})\n"))
    graph = _graph(p.render(str(tmp_path / "o.mp4"), dry_run=True)["main"])
    assert graph.startswith(f"[1:a]{AF}[a0];[a0]loudnorm=I=-16")


@needs_ffmpeg
def test_audio_sequence_formats_each_input_and_keys_on_format(tmp_path, monkeypatch):
    """acrossfade の出力も先頭入力の形式に従うので、連結前に各入力を揃える。
    統一形式は鍵にも入る（旧形式で焼いたキャッシュを命中させない）"""
    mono = _mono24k(tmp_path)
    stereo = _stereo44k(tmp_path)
    p = _mk()
    p._dry_run = True
    p._pending_compute_cmds = {}
    activate(p)
    seq = audio_mod.audio_sequence(mono, stereo, crossfade=0.2)
    cmd = p._pending_compute_cmds[seq.source]
    graph = _graph(cmd)
    assert graph == (f"[0:a]{AF}[af0];[1:a]{AF}[af1];"
                     "[af0][af1]acrossfade=d=0.2[axf1]")
    monkeypatch.setattr(audio_mod, "_MIX_AUDIO_FORMAT",
                        "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo")
    other = audio_mod.audio_sequence(mono, stereo, crossfade=0.2)
    assert other.source != seq.source


@needs_ffmpeg
def test_video_sequence_formats_audio_only_when_mixing_audio(tmp_path, monkeypatch):
    """video_sequence: 音声を連結するときだけ aformat を付け、鍵にも入れる。
    映像のみのときは出力が変わらないので鍵も変えない（同一出力なら同一鍵）"""
    clips_a, clips_v = [], []
    for i, (freq, layout) in enumerate([(300, "mono"), (3000, "stereo")]):
        a = tmp_path / f"av{i}.mp4"
        _ffmpeg("-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=1",
                "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=1",
                "-ac", "1" if layout == "mono" else "2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-shortest", str(a))
        clips_a.append(str(a))
        v = tmp_path / f"v{i}.mp4"
        _ffmpeg("-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(v))
        clips_v.append(str(v))
    p = _mk()
    p._dry_run = True
    p._pending_compute_cmds = {}
    activate(p)
    with_audio = media_mod.video_sequence(*clips_a, t_dur=0.2)
    graph = _graph(p._pending_compute_cmds[with_audio.source])
    for i in range(2):
        assert re.search(
            rf"\[{i}:a\]atrim=duration=[^,]+,asetpts=PTS-STARTPTS,"
            rf"{re.escape(AF)}\[at{i}\]", graph), graph
    video_only = media_mod.video_sequence(*clips_v, t_dur=0.2)
    assert ":a]" not in _graph(p._pending_compute_cmds[video_only.source])

    monkeypatch.setattr(media_mod, "_MIX_AUDIO_FORMAT",
                        "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo")
    assert media_mod.video_sequence(*clips_a, t_dur=0.2).source != with_audio.source
    assert media_mod.video_sequence(*clips_v, t_dur=0.2).source == video_only.source


@needs_ffmpeg
def test_from_project_audio_graph_version_only_for_audio_subprojects(
        tmp_path, monkeypatch):
    """audio_graph（音声グラフの版）は音声のあるサブプロジェクトの鍵にだけ効く。

    映像だけのサブプロジェクトは音声グラフを変えても生成コマンドが同じなので、
    鍵も HEAD 時点の audio_graph=2 に据え置く（上げると Web や重い合成を含む
    サブプロジェクトの webm が、出力が同じなのに全部作り直しになる）。
    返す Object の音声有無も鍵と同じ判定で決まる。
    """
    seen = []
    real_sig_key = objects_mod._sig_key

    def spy(sigs):
        if sigs and sigs[0] == "from_project":
            seen.append([x for x in sigs if x.startswith("audio_graph=")])
        return real_sig_key(sigs)

    monkeypatch.setattr(objects_mod, "_sig_key", spy)
    mono = _mono24k(tmp_path, seconds=1)
    png = tmp_path / "still.png"
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=16x16", "-frames:v", "1", str(png))

    def subproject(name, sub_body):
        sub_layer = _layer(tmp_path, sub_body, name=f"sub_{name}.py")
        parent_layer = _layer(
            tmp_path,
            "sub = Project()\n"
            "sub.configure(width=160, height=90, fps=10)\n"
            f"sub.layer({sub_layer!r})\n"
            "comp = Object.from_project(sub)\n"
            "comp.time(1)\n",
            name=f"parent_{name}.py")
        seen.clear()
        p = _mk()
        p.layer(parent_layer)
        p.render(str(tmp_path / f"{name}.mp4"), dry_run=True)
        comps = [o for o in p.objects if "subproject" in str(o.source)]
        assert len(comps) == 1
        # plan / render の各パスで同じ鍵になる
        assert seen and all(s == seen[0] for s in seen), seen
        return seen[0], comps[0]

    video_sigs, video_obj = subproject("video", f"Object({str(png)!r}).time(1)\n")
    assert video_sigs == ["audio_graph=2"]
    assert video_obj.has_audio is False

    audio_sigs, audio_obj = subproject(
        "audio",
        f"Object({str(png)!r}).time(1)\n"
        f"Object({mono!r}).show(1)\n")
    assert audio_sigs == ["audio_graph=4"]
    assert audio_obj.has_audio is True
    assert audio_obj.source != video_obj.source


# --- 実レンダ -----------------------------------------------------------------

@needs_ffmpeg
def test_real_render_mono_narration_ducks_like_dual_mono(tmp_path):
    """duck_under の検出は形式統一（ステレオ化）の前の音声で行う。

    モノラルのナレーションと、同じ波形を左右に置いたステレオ（sidechaincompress
    の既定 link=average では検出レベルが同じ）とで、ダッキング中の BGM の音量が
    一致すること。揃えた後から検出していた版はモノラルだけが -3dB で検出され、
    既定値（ratio=8, threshold=0.05）で下げ幅が約 2.6dB 浅かった。
    """
    mono = _mono24k(tmp_path, seconds=3, freq=300)
    dual = str(tmp_path / "dual24k.wav")
    _ffmpeg("-i", mono, "-af", "pan=stereo|c0=c0|c1=c0", dual)
    bgm = str(tmp_path / "bgm1k.wav")
    _ffmpeg("-f", "lavfi", "-i",
            "sine=frequency=1000:sample_rate=44100:duration=3", "-ac", "2", bgm)

    def bgm_level(name, voice, duck=True):
        p = _mk()
        body = (f"voice = Object({voice!r}).show(3)\n"
                f"bgm = Object({bgm!r}).time(3)\n")
        if duck:
            body += "bgm <= duck_under(voice)\n"
        p.layer(_layer(tmp_path, body, name=f"l_{name}.py"))
        out = str(tmp_path / f"{name}.mp4")
        p.render(out, timeout=120)
        assert _audio_stream(out) == (48000, 2)
        return _band_rms_db(out, 0, 1000, t0=1.0, dur=1.5)

    bare = bgm_level("bare", mono, duck=False)
    from_mono = bgm_level("mono", mono)
    from_dual = bgm_level("dual", dual)
    assert abs(from_mono - from_dual) < 0.5, (from_mono, from_dual)
    # 実際に下がっていること（一致が「どちらも効いていない」ではない）。
    # -3dB で検出していた版の下げ幅は約 3.6dB、モノラルのまま検出すると約 6.2dB
    assert from_mono < bare - 4.5, (from_mono, bare)


def _assert_stereo_48k_keeps_left_right(out):
    """48kHz・2ch で、ステレオ側（3kHz は左のみ）の左右差と
    モノラル側（300Hz は中央定位）の左右均等が保たれていること"""
    assert _audio_stream(out) == (48000, 2)
    left_3k = _band_rms_db(out, 0, 3000)
    right_3k = _band_rms_db(out, 1, 3000)
    assert left_3k > right_3k + 20, (left_3k, right_3k)
    left_300 = _band_rms_db(out, 0, 300)
    right_300 = _band_rms_db(out, 1, 300)
    assert abs(left_300 - right_300) < 1.0, (left_300, right_300)


@needs_ffmpeg
def test_real_render_mono24k_first_keeps_stereo_48k(tmp_path):
    """モノラル 24kHz を先頭、ステレオ 44.1kHz を2番目に置いて書き出す。

    修正前は amix が先頭入力に従い 24000Hz・1ch になり、左だけの 3kHz が
    左右に均等に潰れていた。
    """
    mono = _mono24k(tmp_path)
    stereo = _stereo44k(tmp_path)
    p = _mk()
    p.layer(_layer(tmp_path,
                   f"voice = Object({mono!r}).show(2)\n"
                   f"bgm = Object({stereo!r}).time(2)\n"))
    out = str(tmp_path / "mix.mp4")
    p.render(out, timeout=120)
    _assert_stereo_48k_keeps_left_right(out)
    # モノラルは中央定位で各チャンネル約 -3dB（パンの法則）。
    # 素材単体の 300Hz 帯域との差で確かめる（AAC の誤差を見込んで ±1dB）
    src = _band_rms_db(mono, 0, 300)
    assert abs((_band_rms_db(out, 0, 300) - src) - (-3.01)) < 1.0


@needs_ffmpeg
def test_real_render_audio_sequence_mono_first_keeps_stereo(tmp_path):
    """audio_sequence（acrossfade）でもモノラルが先頭だと全体がモノラルに潰れていた"""
    mono = _mono24k(tmp_path, seconds=1.5, freq=300)
    stereo = _stereo44k(tmp_path, seconds=1.5, freq=3000)
    p = _mk()
    p.layer(_layer(tmp_path,
                   f"seq = audio_sequence({mono!r}, {stereo!r}, crossfade=0.2)\n"))
    out = str(tmp_path / "seq.mp4")
    p.render(out, timeout=120)
    assert _audio_stream(out) == (48000, 2)
    # ステレオ側（1.5s 以降）の 3kHz は左だけに残る
    left = _band_rms_db(out, 0, 3000, t0=1.6, dur=0.8)
    right = _band_rms_db(out, 1, 3000, t0=1.6, dur=0.8)
    assert left > right + 20, (left, right)
    # 生成物（中間 m4a）自体も 48kHz・2ch
    artifacts = [o.source for o in p.objects
                 if "/aseq/" in str(o.source).replace("\\", "/")]
    assert len(artifacts) == 1, [o.source for o in p.objects]
    assert _audio_stream(artifacts[0]) == (48000, 2)


@needs_ffmpeg
def test_real_parallel_render_audio_leg_keeps_stereo_48k(tmp_path, monkeypatch):
    """並列レンダの音声レグ（_build_ffmpeg_cmd の再利用）でも同じ形式になる"""
    calls = []
    real_run = parallel_mod._run_ffmpeg

    def spy(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(parallel_mod, "_run_ffmpeg", spy)
    mono = _mono24k(tmp_path, seconds=3)
    stereo = _stereo44k(tmp_path, seconds=3)
    p = _mk()
    p.layer(_layer(tmp_path,
                   f"voice = Object({mono!r}).show(3)\n"
                   f"bgm = Object({stereo!r}).time(3)\n"))
    out = str(tmp_path / "par.mp4")
    p.render(out, parallel=2, timeout=120)
    legs = [c for c in calls if c[-1].endswith("audio.m4a")]
    assert len(legs) == 1, [c[-1] for c in calls]
    chains = _audio_chains(_graph(legs[0]))
    assert chains and all(f[-1] == AF for _, f in chains.values())
    _assert_stereo_48k_keeps_left_right(out)
