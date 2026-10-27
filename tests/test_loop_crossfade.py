# -*- coding: utf-8 -*-
"""loop(crossfade=…, tail=…, head=…): 曲の終わりの減衰を避けて、周のつなぎ目をクロスフェードする。

引数なしの loop() は素材の終わり（フェードアウトや無音まで）を鳴らしてそのまま頭へ戻るので、
周の境目で音が痩せて途切れる。crossfade > 0 のときは各周を「素材の head 秒〜減衰の始まり」に限り、
次の周を crossfade 秒の等パワー（qsin）で重ねる（filters/audio.py の _loop_crossfade_graph）。
減衰の始まり（tail="auto"）は 0.5 秒ごとの RMS で測る（audio.py の _measure_loop_decay）。
測定は dry_run でも行い、結果は素材の内容だけで決まるので、測定済みかどうかで
main のコマンドは変わらない（cold / warm 一致）。
"""

import array
import json
import math
import os
import random
import shutil
import subprocess
import wave

import pytest

from scriptvedit import Project, loop
from scriptvedit.audio import (
    _LOOP_DECAY_RATE, _LOOP_DECAY_WINDOW, _decay_start_from_pcm, _loop_decay_path)
from scriptvedit.context import _exec_stack, activate, current_project
from scriptvedit.filters.audio import _LOOP_XF_RATE, _loop_crossfade_graph

_SR = _LOOP_XF_RATE   # 素材も 48kHz で作る（aresample が恒等になり、期待値と標本単位で比べられる）

# 合成する「曲」: 頭の無音 0.3 秒 → 本体（雑音・振幅 0.4）4.5 秒まで → 1 秒で直線的に減衰 → 無音
_HEAD = 0.3
_BODY_END = 4.5
_DECAY_END = 5.5
_SONG_LEN = 6.0


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


def _song_samples():
    rnd = random.Random(20261027)
    out = []
    for i in range(int(_SONG_LEN * _SR)):
        t = i / _SR
        if t < _HEAD:
            g = 0.0
        elif t < _BODY_END:
            g = 0.4
        else:
            g = 0.4 * max(0.0, (_DECAY_END - t) / (_DECAY_END - _BODY_END))
        out.append(int(round(g * rnd.uniform(-1, 1) * 32767)))
    return out


def _write_song(path):
    data = array.array("h", _song_samples())
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_SR)
        w.writeframes(data.tobytes())
    return str(path)


@pytest.fixture
def song(tmp_path):
    path = _write_song(tmp_path / "song.wav")
    # 測定結果のキャッシュを消して cold から始める（中身が同じなら鍵も同じなので）
    cache = _loop_decay_path(path)
    if os.path.exists(cache):
        os.remove(cache)
    return path


def _project(tmp_path, body, name="l_loop.py", duration=None):
    layer = tmp_path / name
    layer.write_text("from scriptvedit import *\n" + body, encoding="utf-8")
    p = Project()
    p.configure(width=64, height=36, fps=10, background_color="black")
    if duration is not None:
        p.configure(duration=duration)
    p.layer(str(layer))
    return p


def _graph(p, tmp_path):
    cmd = p.render(str(tmp_path / "dry.mp4"), dry_run=True)["main"]
    return cmd[cmd.index("-filter_complex") + 1]


# --- 構築・検証 ----------------------------------------------------------

def test_loop_params_unchanged_without_crossfade():
    assert loop().params == {"until": None}
    assert loop(5).params == {"until": 5}
    assert loop(crossfade=0).params == {"until": None}
    assert loop(crossfade=1.5).params == {
        "until": None, "crossfade": 1.5, "tail": "auto", "head": 0}
    assert loop(8, crossfade=2, tail=3, head=0.5).params == {
        "until": 8, "crossfade": 2, "tail": 3, "head": 0.5}


@pytest.mark.parametrize("kwargs", [
    dict(tail=2),                       # crossfade なしの tail / head は拒否
    dict(head=0.5),
    dict(tail="auto", head=1),
    dict(crossfade=-1),
    dict(crossfade=float("nan")),
    dict(crossfade=True),
    dict(crossfade="1"),
    dict(crossfade=1, tail="end"),
    dict(crossfade=1, tail=-0.5),
    dict(crossfade=1, tail=float("inf")),
    dict(crossfade=1, head=-0.1),
    dict(crossfade=1, head=None),
])
def test_loop_rejects_bad_arguments(kwargs):
    with pytest.raises(ValueError):
        loop(**kwargs)


# --- 減衰の始まりの判定 --------------------------------------------------

def _windows(levels):
    """窓ごとの振幅（矩形波）を並べた s16 の列"""
    out = array.array("h")
    for lv in levels:
        out.extend([lv, -lv] * (_LOOP_DECAY_WINDOW // 2))
    return out


def test_decay_start_from_pcm():
    win_sec = _LOOP_DECAY_WINDOW / _LOOP_DECAY_RATE
    # 本体 10 窓 → -10.5dB の 2 窓（減衰）→ 本体の窓の終わりが減衰の始まり
    assert _decay_start_from_pcm(_windows([10000] * 10 + [3000] * 2)) == 10 * win_sec
    # -5dB は減衰とみなさない（閾値 6dB）。その後の無音は減衰
    assert _decay_start_from_pcm(
        _windows([10000] * 10 + [5700] * 2 + [0] * 3)) == 12 * win_sec
    # 頭の無音は判定に影響しない（最後の窓で決まる）
    assert _decay_start_from_pcm(_windows([0] * 2 + [10000] * 8 + [0])) == 10 * win_sec
    # 窓が 4 つに満たない・全部が無音なら素材の終わり
    short = _windows([10000] * 3)
    assert _decay_start_from_pcm(short) == len(short) / _LOOP_DECAY_RATE
    silent = _windows([0] * 6) + array.array("h", [0] * 100)
    assert _decay_start_from_pcm(silent) == len(silent) / _LOOP_DECAY_RATE


# --- フィルタグラフ ------------------------------------------------------

def test_graph_without_crossfade_is_unchanged(song, tmp_path):
    _need_ffmpeg()
    g = _graph(_project(tmp_path, f"b = Object({song!r})\nb.time(10) <= loop()\n"),
               tmp_path)
    assert "aloop=loop=-1:size=" in g
    assert "[lx" not in g and "afade" not in g
    # 測定はしない
    assert not os.path.exists(_loop_decay_path(song))


def _expected_samples(head, body_end, xf):
    h, e, x = (round(v * _SR) for v in (head, body_end, xf))
    return h, e, x, e - h - x


def test_graph_with_crossfade_auto(song, tmp_path):
    _need_ffmpeg()
    p = _project(tmp_path, f"b = Object({song!r})\nb.time(14) <= loop(crossfade=1, head=0.3)\n")
    g = _graph(p, tmp_path)
    # 減衰の始まりは 0.5 秒窓の境目 5.0 秒（4.5〜5.0 秒の窓は本体より約 2.5dB 小さいだけ、
    # 5.0〜5.5 秒は約 12dB 小さい）
    h, e, x, period = _expected_samples(0.3, 5.0, 1)
    assert (f"[1:a]aresample=48000,asetpts=N/SR/TB,atrim=start_sample={h}:end_sample={e},"
            f"asetpts=N/SR/TB,asplit[lx0m][lx0e]") in g
    assert f"afade=t=out:ns={x}:curve=qsin,apad=whole_len={period}[lx0t]" in g
    assert f"aloop=loop=-1:size={period}[lx0r]" in g
    assert "[lx0]atrim=duration=14,asetpts=PTS-STARTPTS," in g
    # 測定結果は __cache__ に覚える
    with open(_loop_decay_path(song), encoding="utf-8") as f:
        assert json.load(f)["decay_start"] == 5.0


def test_dry_run_is_independent_of_measurement_cache(song, tmp_path):
    _need_ffmpeg()
    body = f"b = Object({song!r})\nb.time(14) <= loop(crossfade=1)\n"
    cold = _graph(_project(tmp_path, body), tmp_path)
    cache = _loop_decay_path(song)
    assert os.path.exists(cache)
    warm = _graph(_project(tmp_path, body), tmp_path)
    assert cold == warm
    # 壊れた測定結果は測り直す（命中扱いにしない）
    with open(cache, "w", encoding="utf-8") as f:
        f.write('{"decay_start": -1')
    assert _graph(_project(tmp_path, body), tmp_path) == cold
    with open(cache, encoding="utf-8") as f:
        assert json.load(f)["decay_start"] == 5.0


def test_tail_seconds_uses_material_length(song, tmp_path):
    _need_ffmpeg()
    g = _graph(_project(
        tmp_path, f"b = Object({song!r})\nb.time(14) <= loop(crossfade=0.5, tail=1.5)\n"),
        tmp_path)
    h, e, x, period = _expected_samples(0, _SONG_LEN - 1.5, 0.5)
    assert f"[1:a]aresample=48000,asetpts=N/SR/TB,atrim=end_sample={e}," in g
    assert f"aloop=loop=-1:size={period}[lx0r]" in g
    # 秒の指定では測らない
    assert not os.path.exists(_loop_decay_path(song))


def test_crossfade_is_combined_with_duck_and_until(song, tmp_path):
    """duck_under の検出用枝・loop(until=) のトリムはクロスフェードの出力から続く"""
    _need_ffmpeg()
    g = _graph(_project(
        tmp_path,
        f"v = Object({song!r})\nv.time(3)\n"
        f"b = Object({song!r})\n"
        "(b @ 0) <= loop(9, crossfade=1, tail=1) & duck_under(v)\n",
        duration=12), tmp_path)
    assert "[lx1]atrim=duration=9,asetpts=PTS-STARTPTS," in g, g
    assert "asplit[apre0][dside_src1]" in g


def test_crossfade_longer_than_half_the_body_is_rejected(song, tmp_path):
    _need_ffmpeg()
    # 使う区間 0.3〜5.0 秒（4.7 秒）に 2.5 秒のクロスフェードは入らない
    p = _project(tmp_path, f"b = Object({song!r})\nb.time(14) <= loop(crossfade=2.5, head=0.3)\n")
    with pytest.raises(ValueError, match="クロスフェードの2倍より短い"):
        p.render(str(tmp_path / "dry.mp4"), dry_run=True)
    p = _project(tmp_path, f"b = Object({song!r})\nb.time(14) <= loop(crossfade=1, tail=7)\n",
                 name="l_loop2.py")
    with pytest.raises(ValueError, match="クロスフェードの2倍より短い"):
        p.render(str(tmp_path / "dry.mp4"), dry_run=True)


def test_auto_tail_on_generated_source_is_rejected(song, tmp_path):
    """生成物は初回の dry_run ではまだ無い＝測れないので tail='auto' は拒否。秒なら使える"""
    _need_ffmpeg()
    body = (f"s = audio_sequence({song!r}, {song!r}, crossfade=0.5)\n"
            "s <= loop(crossfade=1)\n")
    p = _project(tmp_path, body, duration=20)
    with pytest.raises(ValueError, match="tail=秒"):
        p.render(str(tmp_path / "dry.mp4"), dry_run=True)
    body = (f"s = audio_sequence({song!r}, {song!r}, crossfade=0.5)\n"
            "s <= loop(crossfade=1, tail=1)\n")
    g = _graph(_project(tmp_path, body, name="l_loop2.py", duration=20), tmp_path)
    # 合成尺 11.5 秒（6 + 6 − 0.5）から tail を引いた所まで（生成物を probe しない）
    assert f"atrim=end_sample={round(10.5 * _SR)}," in g


# --- 出力の音（PCM） ------------------------------------------------------

def _decode_mono_f32(path, extra=()):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), *extra, "-ac", "1", "-ar", str(_SR),
         "-f", "f32le", "-"], capture_output=True, check=True).stdout
    out = array.array("f")
    out.frombytes(raw)
    return out


def test_crossfade_graph_output_matches_expected_samples(song, tmp_path):
    """部分グラフの出力を標本単位で期待値（A の後に定常の1周期 Q を繰り返す）と比べる。

    A = S[0, P)、Q = fadein(A) + fadeout(S[P, L)) を無音で P まで延ばしたもの。
    期待値どおりなら、どの境目にも段差（クリック）も無音も無い。
    """
    _need_ffmpeg()
    head, body_end, xf = 0.3, 5.0, 1.0
    parts, out = _loop_crossfade_graph("[0:a]", 0, head, body_end, xf)
    dur = 16.0
    graph = ";".join(parts) + f";{out}atrim=duration={dur}[o]"
    got = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", song, "-filter_complex", graph, "-map", "[o]",
         "-f", "f32le", "-c:a", "pcm_f32le", "-"], capture_output=True, check=True).stdout
    y = array.array("f")
    y.frombytes(got)
    assert len(y) == round(dur * _SR)

    src = [v / 32768.0 for v in _song_samples()]
    h, e, x, period = _expected_samples(head, body_end, xf)
    seg = src[h:e]
    a = seg[:period]
    q = [a[i] * (math.sin(math.pi / 2 * i / x) if i < x else 1.0) for i in range(period)]
    for i in range(x):
        q[i] += seg[period + i] * math.cos(math.pi / 2 * i / x)
    worst = 0.0
    for n, v in enumerate(y):
        want = a[n] if n < period else q[(n - period) % period]
        worst = max(worst, abs(v - want))
    # afade の曲線の標本の取り方（i/x か (i+1)/x か）の差は 0.4 × π/2 / x 程度
    assert worst < 1e-3, worst


def _rms_db(y, start, n):
    s = sum(v * v for v in y[start:start + n]) / n
    return 10 * math.log10(s + 1e-20)


def test_render_has_no_gap_at_the_seams(song, tmp_path):
    """実レンダ（h264 + AAC）を PCM に戻し、20ms ごとの音量がつなぎ目でも落ちないこと。

    比較として、引数なしの loop() は減衰と無音（5.0〜6.0 秒・頭の 0.3 秒）を繰り返すので
    同じ尺の中に大きな谷ができる。
    """
    _need_ffmpeg()
    dur = 14
    for name, eff, expect_gap in (("xf", "loop(crossfade=1, head=0.3)", False),
                                  ("plain", "loop()", True)):
        p = _project(tmp_path, f"b = Object({song!r})\nb.time({dur}) <= {eff}\n",
                     name=f"l_{name}.py")
        out = tmp_path / f"{name}.mp4"
        p.render(str(out), timeout=180)
        y = _decode_mono_f32(out)
        win = _SR // 50
        # AAC の頭と尻の過渡を避けて 0.1 秒〜（尺 − 0.1 秒）を見る
        dbs = [_rms_db(y, i, win)
               for i in range(int(0.1 * _SR), int((dur - 0.1) * _SR) - win, win)]
        ref = sorted(dbs)[len(dbs) // 2]
        low = min(dbs)
        if expect_gap:
            assert low < ref - 20, (name, ref, low)
        else:
            # 雑音どうしの等パワーのクロスフェードは平らになる（20ms 窓のゆらぎ程度）
            assert low > ref - 1.5 and max(dbs) < ref + 1.5, (name, ref, low, max(dbs))
