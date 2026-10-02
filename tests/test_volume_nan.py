# -*- coding: utf-8 -*-
"""avolume の Expr（volume='式':eval=frame）が初期化時の NaN 警告を出さないこと。

volume フィルタは eval=frame でも初期化時に1回 t=NaN で式を評価する
（FFmpeg 8 の af_volume.c）。以前の式 clip((t)/dur,0,1) は NaN になり、式を使う
音声1本ごとに "Invalid value NaN for volume, setting to 0" がログへ出て、数百行で
本当のエラーが埋もれた。filters/audio.py の _VOLUME_T_EXPR で t を
if(isnan(t),0,t) に包んで解消した。初期化時の値は使われない（各フレームで
評価し直す）ので、出力音声は置き換え前とビット単位で同一であることを実測で固定する。
"""

import os
import shutil
import subprocess
import types

import pytest

import scriptvedit.filters.audio as audio_filters
from scriptvedit import Project, asset, avolume, clip, sin
from scriptvedit.filters.audio import _build_audio_effect_filters

_NO_FFMPEG = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
needs_ffmpeg = pytest.mark.skipif(_NO_FFMPEG, reason="ffmpeg/ffprobe が無い環境")

_NAN_WARNING = "Invalid value NaN for volume"
_OLD_T_EXPR = "(t)"  # 置き換え前の式の t（clip((t)/dur,0,1)）


def _filters(expr_fn, dur=2):
    obj = types.SimpleNamespace(audio_effects=[avolume(expr_fn)])
    return _build_audio_effect_filters(obj, dur)


def _run(filter_str, *, loglevel="warning"):
    """2秒のステレオ正弦波へ filter_str を掛け、(PCMのmd5行, stderr) を返す"""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", loglevel,
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=48000",
         "-filter_complex", f"[0:a]{filter_str},aformat=channel_layouts=stereo[a]",
         "-map", "[a]", "-c:a", "pcm_f32le", "-f", "md5", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=True)
    return proc.stdout.strip(), proc.stderr


def test_volume_expr_wraps_t_with_isnan():
    """式の中の t は if(isnan(t),0,t) に包まれる。定数の音量は式を使わない"""
    assert _filters(0.8) == ["volume=volume='0.8':eval=frame"]
    framing, flt = _filters(lambda u: u, dur=5)
    assert framing == audio_filters._VOLUME_EXPR_FRAMING
    assert flt == "volume=volume='clip(if(isnan(t)\\,0\\,t)/5\\,0\\,1)':eval=frame"
    assert "clip((t)/" not in flt


@needs_ffmpeg
@pytest.mark.parametrize("expr_fn", [
    lambda u: u,                      # フェードイン
    lambda u: 1 - u,                  # フェードアウト
    lambda u: u * u,                  # 加速フェード
    lambda u: 0.5 + 0.5 * sin(u * 20),  # 揺れ
], ids=["fade_in", "fade_out", "ease", "wobble"])
def test_volume_expr_has_no_nan_warning_and_same_output(expr_fn):
    """新しい式は警告を出さず、置き換え前の式と出力 PCM が完全一致する"""
    new = ",".join(_filters(expr_fn))
    old = new.replace(audio_filters._VOLUME_T_EXPR, _OLD_T_EXPR)
    assert old != new

    new_md5, new_err = _run(new)
    old_md5, old_err = _run(old)
    assert _NAN_WARNING in old_err  # 置き換え前は警告が出ていた（修正の根拠）
    assert _NAN_WARNING not in new_err, new_err
    assert new_md5.startswith("MD5=") and new_md5 == old_md5


@needs_ffmpeg
def test_project_render_audio_is_unchanged_and_log_is_clean(tmp_path, monkeypatch, capfd):
    """実レンダ: avolume のフェードを持つ音声2本で、出力音声が置き換え前と同一・警告なし"""
    try:
        bgm = asset("audio/bgm_loop.mp3")
    except FileNotFoundError:
        pytest.skip("テスト素材 assets/audio/bgm_loop.mp3 がありません")
    layer = tmp_path / "fade.py"
    layer.write_text(
        "from scriptvedit import *\n"
        f"a = Object({bgm!r})\n"
        "a.time(3) <= avolume(lambda u: u)\n"
        f"b = Object({bgm!r})\n"
        "b @ 1\n"
        "b.time(2) <= avolume(lambda u: 1 - u) & avolume(0.5)\n",
        encoding="utf-8")

    def render(name):
        p = Project()
        p.configure(width=160, height=90, fps=8, background_color="black")
        p.layer(str(layer))
        out = tmp_path / name
        p.render(str(out), timeout=120)
        return out

    def pcm_md5(path):
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
             "-i", str(path), "-map", "0:a:0", "-c:a", "pcm_f32le",
             "-f", "md5", "-"],
            capture_output=True, text=True, check=True)
        return proc.stdout.strip()

    capfd.readouterr()
    new_out = render("new.mp4")
    new_log = capfd.readouterr().err
    monkeypatch.setattr(audio_filters, "_VOLUME_T_EXPR", _OLD_T_EXPR)
    old_out = render("old.mp4")
    old_log = capfd.readouterr().err

    assert _NAN_WARNING in old_log
    assert _NAN_WARNING not in new_log
    assert pcm_md5(new_out) == pcm_md5(old_out)
    assert os.path.getsize(new_out) > 0


@needs_ffmpeg
def test_volume_expr_fade_in_does_not_swallow_first_frame():
    """時間で変わる音量は 256 サンプルに刻んでから評価するので、立ち上がりのフェードで
    頭のフレーム（デコーダの 1024〜4096 サンプル）が丸ごと無音にならない"""
    def first_50ms_peak(filter_str):
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "info",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=1:sample_rate=48000:samples_per_frame=4096",
             "-filter_complex", f"[0:a]{filter_str},atrim=start=0.010:end=0.050,volumedetect[a]",
             "-map", "[a]", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
        line = [ln for ln in proc.stderr.splitlines() if "max_volume" in ln][-1]
        return float(line.split("max_volume:")[1].split("dB")[0])

    # 5ms で立ち上がるフェード（語録の切り出しに掛けているのと同じ形）
    new = ",".join(_filters(lambda u: clip(u / 0.005, 0, 1), dur=1))
    no_framing = new.replace(audio_filters._VOLUME_EXPR_FRAMING + ",", "")
    # sine の振幅は既定で 1/8（約 -18dBFS）
    assert first_50ms_peak(new) > -20.0            # 10〜50ms に音が出ている
    assert first_50ms_peak(no_framing) < -60.0     # 刻まないと最初の 4096 サンプルが無音
