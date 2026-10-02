# -*- coding: utf-8 -*-
"""生成物（video_sequence 等）の time() 省略と合成尺の自動設定。

video_sequence(...).time()（引数なし）は初回の実レンダで
「メディアの長さを取得できません: __cache__\\artifacts\\xfade\\….mkv」になっていた。
Plan pass では生成物がまだ無く、length() が未生成の mkv を probe していたため。
生成コマンドを組んだ時点の合成尺（Object._generated_length）を尺の基準にして、
- 未生成でも time() 省略が通る
- 生成の前後（cold / warm）で尺が変わらない
ようにした。video_sequence は audio_sequence と同じく duration も自動で入る。
"""

import os
import shutil
import subprocess

import pytest

import scriptvedit as sv
from scriptvedit import Object, Project, video_sequence
from scriptvedit.context import _exec_stack, activate, current_project

_W, _H, _FPS = 64, 36, 30


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


def _make_clip(path, seconds, lum, audio=False):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"color=c=black:s={_W}x{_H}:r={_FPS}:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}"]
    cmd += ["-vf", f"format=yuv444p,geq=lum={lum}:cb=128:cr=128",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "1"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    subprocess.run(cmd + [str(path)], check=True, capture_output=True, timeout=60)


def _center_luma(video):
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True, timeout=60).stdout
    size = _W * _H
    return [out[i * size + (_H // 2) * _W + _W // 2] for i in range(len(out) // size)]


def _project(layer):
    p = Project()
    p.configure(width=_W, height=_H, fps=_FPS, background_color="black")
    p.layer(str(layer), priority=1)
    return p


def test_video_sequence_sets_duration_without_time(tmp_path):
    """time() を呼ばなくても合成尺が duration に入る（audio_sequence と同じ）"""
    _need_ffmpeg()
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    _make_clip(a, 1.0, 200)
    _make_clip(b, 1.0, 100)
    p = Project()
    p.configure(width=_W, height=_H, fps=_FPS)
    p._mode = "plan"          # 生成はしない（尺だけを見る）
    seq = video_sequence(str(a), str(b), t_dur=0.3)
    assert seq.duration == pytest.approx(1.7, abs=0.02)
    assert seq._generated_length == seq.duration
    if os.path.exists(seq.source):      # 以前の実行の生成物が残っていたら消す（鍵は内容で決まる）
        os.remove(seq.source)
    # 未生成の生成物でも length() / time() 省略が通る
    assert seq.length() == seq.duration
    seq.time()
    assert seq.duration is None and seq._duration_auto
    p._fill_auto_durations(0, len(p.objects))
    assert seq.duration == pytest.approx(1.7, abs=0.02)


def test_length_of_plain_missing_media_still_raises(tmp_path):
    """生成物でない素材が無いときは従来どおり FileNotFoundError"""
    p = Project()
    p.configure(width=_W, height=_H, fps=_FPS)
    o = Object(str(tmp_path / "nothing.mp4"))
    assert o._generated_length is None
    with pytest.raises(FileNotFoundError, match="メディアの長さを取得できません"):
        o.length()


@pytest.mark.parametrize("audio", [False, True], ids=["video_only", "with_audio"])
def test_real_render_time_without_argument_on_cold_cache(tmp_path, audio):
    """初回（生成物なし）の実レンダで time() 省略が通り、後続が合成尺の直後に並ぶ"""
    _need_ffmpeg()
    a, b, tail = tmp_path / "a.mp4", tmp_path / "b.mp4", tmp_path / "tail.mp4"
    _make_clip(a, 1.0, 200, audio=audio)
    _make_clip(b, 1.0, 100, audio=audio)
    _make_clip(tail, 0.5, 50)
    layer = tmp_path / "l_seq.py"
    layer.write_text(
        "from scriptvedit import *\n"
        f"seq = video_sequence({str(a)!r}, {str(b)!r}, transition='fade', t_dur=0.4)\n"
        "seq.time()\n"
        f"Object({str(tail)!r}).time(0.5)\n", encoding="utf-8")

    # cold にする: dry_run が予告する生成物を消してから実レンダする
    cold = _project(layer).render(str(tmp_path / "dry.mp4"), dry_run=True)
    xfade = [k for k in cold["cache"] if "xfade" in k.replace("\\", "/")]
    assert len(xfade) == 1, cold["cache"].keys()
    if os.path.exists(xfade[0]):
        os.remove(xfade[0])

    out = tmp_path / "out.mp4"
    _project(layer).render(str(out), timeout=300)
    assert os.path.exists(xfade[0])
    lum = _center_luma(out)
    # 合成尺 1.6 秒（48 枚）+ 後続 0.5 秒（15 枚）
    assert len(lum) == 63, len(lum)
    assert lum[5] > 170 and 70 < lum[40] < 130, lum
    assert all(v < 75 for v in lum[49:]), lum[45:]

    # warm（生成物あり）でも dry_run の出力は cold と同じ（尺が probe 値へ変わらない）
    warm = _project(layer).render(str(tmp_path / "dry.mp4"), dry_run=True)
    assert warm == cold
    # 2回目の実レンダ（キャッシュ命中）も同じ枚数
    _project(layer).render(str(out), timeout=300)
    assert len(_center_luma(out)) == 63


def _plan_project():
    p = Project()
    p.configure(width=_W, height=_H, fps=_FPS)
    p._mode = "plan"          # 生成はしない（尺だけを見る）
    return p


def test_compute_replaces_generated_length(tmp_path):
    """compute(duration=d) で焼き直した生成物の尺は d（古い合成尺を返さない）"""
    _need_ffmpeg()
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    _make_clip(a, 1.0, 200)
    _make_clip(b, 1.0, 100)
    p = _plan_project()
    seq = video_sequence(str(a), str(b), t_dur=0.4)
    assert seq.length() == pytest.approx(1.6, abs=0.02)
    seq <= sv.resize(sx=0.5, sy=0.5)
    seq.compute(duration=1.0)
    assert seq._generated_length == 1.0
    assert seq.length() == 1.0
    seq.time()
    p._fill_auto_durations(0, len(p.objects))
    assert seq.duration == 1.0
    # time() を呼ばずに @ で置き直したときも古い合成尺を持ち越さない
    seq2 = video_sequence(str(a), str(b), t_dur=0.4)
    seq2.compute(duration=1.0)
    seq2 @ 0
    p._fill_auto_durations(0, len(p.objects))
    assert seq2.duration == 1.0


def test_compute_to_still_clears_generated_length(tmp_path):
    """duration なしの compute()（静止画）は合成尺を持たない"""
    _need_ffmpeg()
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    _make_clip(a, 1.0, 200)
    _make_clip(b, 1.0, 100)
    _plan_project()
    seq = video_sequence(str(a), str(b), t_dur=0.4)
    seq.compute()
    assert seq.media_type == "image"
    assert seq._generated_length is None


def test_plain_compute_time_without_argument_in_plan(tmp_path):
    """素の動画を compute(duration=d) した後の time() 省略も、未生成のまま通る"""
    _need_ffmpeg()
    a = tmp_path / "a.mp4"
    _make_clip(a, 1.0, 200)
    p = _plan_project()
    o = Object(str(a))
    o <= sv.resize(sx=0.5, sy=0.5)
    o.compute(duration=0.5)
    if os.path.exists(o.source):
        os.remove(o.source)
    o.time()
    p._fill_auto_durations(0, len(p.objects))
    assert o.duration == 0.5


def test_time_effect_after_video_sequence_updates_duration(tmp_path):
    """time() を呼ばなくても、後から足した speed / trim が尺へ反映される"""
    _need_ffmpeg()
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    _make_clip(a, 1.0, 200)
    _make_clip(b, 1.0, 100)
    p = _plan_project()
    fast = video_sequence(str(a), str(b), t_dur=0.4)
    fast <= sv.speed(2)
    cut = video_sequence(str(a), str(b), t_dur=0.3)
    cut <= sv.trim(0.5)
    plain = video_sequence(str(a), str(b), t_dur=0.2)
    fixed = video_sequence(str(a), str(b), t_dur=0.2)
    fixed.time(3)
    fixed <= sv.speed(2)
    p._fill_auto_durations(0, len(p.objects))
    assert fast.duration == pytest.approx(0.8, abs=0.02)
    assert cut.duration == pytest.approx(0.5, abs=0.02)
    assert plain.duration == pytest.approx(1.8, abs=0.02)
    assert fixed.duration == 3          # 明示した time(d) は上書きしない


@pytest.mark.parametrize("body, frames", [
    # compute(duration=1.0) で焼き直す: 1.0 + 0.5 秒
    ("s <= resize(sx=0.5, sy=0.5)\ns.compute(duration=1.0)\ns.time()\n", 45),
    # 後から speed(2): 0.8 + 0.5 秒（time() なし）
    ("s <= speed(2)\n", 39),
], ids=["compute", "speed"])
def test_real_render_duration_follows_later_changes(tmp_path, body, frames):
    """生成物の後に尺を変える操作をしても、後続が正しい位置に並ぶ（cold / warm 一致）"""
    _need_ffmpeg()
    a, b, tail = tmp_path / "a.mp4", tmp_path / "b.mp4", tmp_path / "tail.mp4"
    _make_clip(a, 1.0, 200)
    _make_clip(b, 1.0, 100)
    _make_clip(tail, 0.5, 30)
    layer = tmp_path / "l_seq2.py"
    layer.write_text(
        "from scriptvedit import *\n"
        f"s = video_sequence({str(a)!r}, {str(b)!r}, transition='fade', t_dur=0.4)\n"
        + body +
        f"Object({str(tail)!r}).time(0.5)\n", encoding="utf-8")
    cold = _project(layer).render(str(tmp_path / "dry.mp4"), dry_run=True)
    for path in cold["cache"]:
        if os.path.exists(path):
            os.remove(path)
    out = tmp_path / "out.mp4"
    _project(layer).render(str(out), timeout=300)
    lum = _center_luma(out)
    assert len(lum) == frames, len(lum)
    # 後続（暗い板）は最後の 0.5 秒（15 枚）。その直前までは連結した動画が映っている
    assert all(v < 60 for v in lum[frames - 14:]), lum
    assert lum[frames - 17] > 70, lum
    warm = _project(layer).render(str(tmp_path / "dry.mp4"), dry_run=True)
    assert warm == cold
    _project(layer).render(str(out), timeout=300)
    assert len(_center_luma(out)) == frames


def test_describe_notes_auto_duration():
    entry = [e for e in sv.describe(name="video_sequence")["factories"]
             if e["name"] == "video_sequence"][0]
    assert any("time()" in n for n in entry.get("notes", [])), entry.get("notes")
