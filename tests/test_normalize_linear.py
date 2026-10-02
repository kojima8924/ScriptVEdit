# -*- coding: utf-8 -*-
"""normalize_audio(mode="linear")（2パス線形正規化）の回帰テスト。

実素材で実測して固定すること:

- linear は「静かな BGM だけの区間」と「大きな声の区間」の音量差を正規化の前後で
  保つ（±0.5dB）。dynamic（測定値を渡さない1パス loudnorm）はその差を崩す
  （声の無い区間の BGM を目標付近まで持ち上げる＝ポンピング）。
- linear の出力は統合ラウドネスが目標 ±0.5 LU、true peak が上限以下。
- dry_run はキャッシュ状態に依存しない（main は増幅量の表記、測定パスは cache 側）。
- 並列レンダの音声レグ・部分レンダも全編の測定値による同じ増幅量を使う。
  音声を出さない経路（gif・サムネイル）は測定しない。
- 測定結果は同じ入力で決定的（JSON を消して測り直しても同じ値）で、
  2回目以降は測定キャッシュを使う。

素材はテスト内で FFmpeg の lavfi から決定的に生成する（第三者素材を使わない）:
  BGM … ピンクノイズ（seed 固定）。約 -36.5 LUFS の静かな定常音
  声  … 200/400Hz の母音的な音に 3Hz の抑揚と、0.5秒ごとの短い子音的な
        1.5kHz バースト（ピーク）。約 -20.8 LUFS。6秒目から6秒間
実測（FFmpeg 8.0 / AAC 160k）: 正規化前の区間差 15.77dB。linear は増幅 +6.86dB で
区間差 15.68dB・統合 -14.14 LUFS・true peak -1.93dBTP（子音的なピークは上限を
1.47dB 超えるのでリミッターが実際に働く）。dynamic は区間差 1.37dB
（BGM だけの区間が -36.5 → -14.7 LUFS へ持ち上がる）。
"""

import json
import os
import re
import shutil
import subprocess

import pytest

import scriptvedit.loudness as loudness_mod
import scriptvedit.parallel as parallel_mod
from scriptvedit import Project
from scriptvedit.loudness import (
    _LINEAR_GAIN_PLACEHOLDER, _decide_linear_gain, _parse_loudnorm_json,
    _read_measurement)

_NO_FFMPEG = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
needs_ffmpeg = pytest.mark.skipif(_NO_FFMPEG, reason="ffmpeg/ffprobe が無い環境")

TARGET = -14
TRUE_PEAK = -1.5
TOTAL = 12
VOICE_AT = 6
# 区間（秒）: 境界付近（声の立ち上がり・リミッターの回復）を避けて測る
SECTION_BGM = (0.5, 5.0)      # 0.5〜5.5s: BGM だけ
SECTION_VOICE = (6.5, 5.0)    # 6.5〜11.5s: 声 + BGM

_BGM_SRC = ("anoisesrc=color=pink:amplitude=0.085:seed=7"
            f":duration={TOTAL}:sample_rate=48000")
_VOICE_SRC = (
    "aevalsrc='0.2*(0.6*sin(2*PI*200*t)+0.4*sin(2*PI*400*t+1))"
    "*(0.6+0.4*sin(2*PI*3*t))"
    "+0.25*exp(-pow((mod(t\\,0.5)-0.25)*300\\,2))*sin(2*PI*1500*t)'"
    f":s=48000:d={TOTAL - VOICE_AT}:c=stereo")

_LAYER = (
    "from scriptvedit import *\n"
    "bgm = Object(here('bgm.wav'))\n"
    f"bgm.time({TOTAL})\n"
    "voice = Object(here('voice.wav'))\n"
    f"voice @ {VOICE_AT}\n"
    f"voice.time({TOTAL - VOICE_AT})\n"
)

_LOUDNORM_JSON_RE = re.compile(r'\{\s*"input_i".*?\}', re.DOTALL)


def _ffmpeg(*args):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
         *map(str, args)],
        check=True)


def _make_material(dirpath):
    """BGM / 声 の WAV とレイヤーファイルを dirpath に作る"""
    os.makedirs(dirpath, exist_ok=True)
    _ffmpeg("-f", "lavfi", "-i", _BGM_SRC, "-ac", "2",
            os.path.join(dirpath, "bgm.wav"))
    _ffmpeg("-f", "lavfi", "-i", _VOICE_SRC, os.path.join(dirpath, "voice.wav"))
    layer = os.path.join(dirpath, "layer.py")
    with open(layer, "w", encoding="utf-8") as f:
        f.write(_LAYER)
    return layer


def _project(layer, mode=None, target=TARGET, **kwargs):
    p = Project()
    p.configure(width=160, height=90, fps=8, background_color="black")
    if mode is not None:
        p.normalize_audio(target, true_peak=TRUE_PEAK, mode=mode, **kwargs)
    p.layer(layer)
    return p


def _loudness(path, section=None):
    """出力音声（区間指定可）の統合ラウドネスと true peak を測る（テスト側の独立実装）"""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-nostdin"]
    if section is not None:
        cmd += ["-ss", str(section[0]), "-t", str(section[1])]
    cmd += ["-i", str(path), "-map", "0:a:0",
            "-af", "loudnorm=print_format=json", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True)
    match = _LOUDNORM_JSON_RE.findall(proc.stderr)
    assert match, proc.stderr[-2000:]
    data = json.loads(match[-1])
    return float(data["input_i"]), float(data["input_tp"])


def _section_difference(path):
    """声区間 − BGM区間 のラウドネス差 (dB)"""
    bgm, _ = _loudness(path, SECTION_BGM)
    voice, _ = _loudness(path, SECTION_VOICE)
    return voice - bgm, bgm, voice


def _measure_path(layer, **kwargs):
    """dry_run の cache 側から測定結果 JSON のパスを1つ取り出す"""
    p = _project(layer, "linear", **kwargs)
    dry = p.render(os.path.join(os.path.dirname(layer), "dry.mp4"), dry_run=True)
    paths = [k for k in dry["cache"] if os.sep + "loudness" + os.sep in k]
    assert len(paths) == 1, dry["cache"].keys()
    return paths[0], dry


@pytest.fixture(scope="module")
def renders(tmp_path_factory):
    """正規化なし / linear / dynamic の3本を1回だけ実レンダする"""
    if _NO_FFMPEG:
        pytest.skip("ffmpeg/ffprobe が無い環境")
    base = str(tmp_path_factory.mktemp("normalize_linear"))
    layer = _make_material(base)
    measure_path, dry_cold = _measure_path(layer)
    # 前回のテスト実行の測定結果が残っていても、この回で実測パスを必ず通す
    if os.path.exists(measure_path):
        os.remove(measure_path)

    out = {"layer": layer, "base": base, "measure_path": measure_path,
           "dry_cold": dry_cold}
    for mode in (None, "linear", "dynamic"):
        p = _project(layer, mode)
        path = os.path.join(base, f"out_{mode or 'none'}.mp4")
        p.render(path, timeout=120)
        out[mode or "none"] = path
        if mode == "linear":
            out["linear_gain"] = p._linear_gain_db
    return out


# ---------------------------------------------------------------------------
# 実測: 区間の音量差・統合ラウドネス・true peak
# ---------------------------------------------------------------------------

@needs_ffmpeg
def test_linear_keeps_section_difference_and_meets_targets(renders):
    before, bgm0, voice0 = _section_difference(renders["none"])
    after, bgm1, voice1 = _section_difference(renders["linear"])
    integrated, true_peak = _loudness(renders["linear"])

    # 素材が「静かな BGM」と「大きな声」になっていること（テストの前提）
    assert before > 12, (bgm0, voice0)
    # 増幅が実際に掛かっていること（0dB 付近だと正規化の検証にならない）
    assert renders["linear_gain"] > 3, renders["linear_gain"]

    assert abs(after - before) <= 0.5, (
        f"linear で区間の音量差が変わった: 前 {before:.2f}dB → 後 {after:.2f}dB"
        f"（BGM {bgm0:.2f}→{bgm1:.2f} / 声 {voice0:.2f}→{voice1:.2f}）")
    assert abs(integrated - TARGET) <= 0.5, integrated
    assert true_peak <= TRUE_PEAK, true_peak


@needs_ffmpeg
def test_dynamic_breaks_section_difference(renders):
    """dynamic は声の無い区間の BGM を持ち上げる（linear を足した理由の実証）"""
    before, bgm0, _ = _section_difference(renders["none"])
    after, bgm_dyn, _ = _section_difference(renders["dynamic"])
    _, bgm_lin, _ = _section_difference(renders["linear"])

    assert before - after > 3, (
        f"dynamic でも区間差が保たれている: 前 {before:.2f}dB → 後 {after:.2f}dB")
    # BGM だけの区間が linear より大きく持ち上がっている
    assert bgm_dyn - bgm_lin > 5, (bgm_dyn, bgm_lin)


# ---------------------------------------------------------------------------
# dry_run・測定キャッシュ・決定性
# ---------------------------------------------------------------------------

@needs_ffmpeg
def test_linear_dry_run_shape_and_cache_independence(renders):
    """main は増幅量の表記・測定パスは cache 側。測定済み（warm）でも同じ出力"""
    _path, dry_warm = _measure_path(renders["layer"])
    assert os.path.exists(renders["measure_path"])  # 実レンダで測定済み
    assert dry_warm == renders["dry_cold"], "測定キャッシュの有無で dry_run が変わった"

    graph = dry_warm["main"][dry_warm["main"].index("-filter_complex") + 1]
    assert f"volume={_LINEAR_GAIN_PLACEHOLDER}" in graph
    assert "loudnorm" not in graph  # 動的な loudnorm は通さない
    # 増幅 → sample rate 確定 → リミッターの順
    assert (graph.index("volume=<MEASURED_GAIN>dB") < graph.index("aresample=48000")
            < graph.index("alimiter="))

    measure_cmd = dry_warm["cache"][renders["measure_path"]]
    m_graph = measure_cmd[measure_cmd.index("-filter_complex") + 1]
    assert m_graph.endswith("loudnorm=print_format=json[ameas]")
    assert "volume=<MEASURED_GAIN>" not in m_graph  # 測るのは正規化前のミックス
    assert measure_cmd[-6:] == ["-vn", "-f", "null", "-t", str(TOTAL), "-"]
    assert measure_cmd[measure_cmd.index("-loglevel") + 1] == "info"
    assert "-nostats" in measure_cmd
    # 本レンダと同じ音声グラフ（正規化の直前まで一致）
    assert m_graph.split("[aout]")[0] == graph.split("[aout]")[0]


@needs_ffmpeg
def test_linear_measurement_is_deterministic_and_cached(renders, monkeypatch):
    path = renders["measure_path"]
    with open(path, encoding="utf-8") as f:
        first = f.read()

    # 測り直しても同じ値（決定的）
    os.remove(path)
    p = _project(renders["layer"], "linear")
    p.render(os.path.join(renders["base"], "again.mp4"), timeout=120)
    with open(path, encoding="utf-8") as f:
        assert f.read() == first
    assert p._linear_gain_db == renders["linear_gain"]

    # 2回目以降は測定キャッシュを使う（測定の ffmpeg を起動しない）
    def _no_measure(*args, **kwargs):
        raise AssertionError("測定キャッシュがあるのに測定パスを実行した")
    monkeypatch.setattr(loudness_mod, "_run_ffmpeg", _no_measure)
    p2 = _project(renders["layer"], "linear")
    p2.render(os.path.join(renders["base"], "cached.mp4"), timeout=120)
    assert p2._linear_gain_db == renders["linear_gain"]

    # 目標値は測定の鍵に入らない（target を変えても測り直さない）。増幅量は目標差だけ動く
    path_16, _ = _measure_path(renders["layer"], target=TARGET - 2)
    assert path_16 == path
    p3 = _project(renders["layer"], "linear", target=TARGET - 2)
    p3.render(os.path.join(renders["base"], "target16.mp4"), timeout=120)
    assert p3._linear_gain_db == pytest.approx(renders["linear_gain"] - 2, abs=0.011)


@needs_ffmpeg
def test_broken_measurement_cache_is_measured_again(renders):
    """壊れた測定 JSON は使わず測り直す（self-heal）"""
    path = renders["measure_path"]
    with open(path, encoding="utf-8") as f:
        good = f.read()
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"integrated_lufs": "壊れた値"')
    assert _read_measurement(path) is None
    p = _project(renders["layer"], "linear")
    p.render(os.path.join(renders["base"], "healed.mp4"), timeout=120)
    with open(path, encoding="utf-8") as f:
        assert f.read() == good
    assert p._linear_gain_db == renders["linear_gain"]


# ---------------------------------------------------------------------------
# 並列レンダ・部分レンダ・音声を出さない経路
# ---------------------------------------------------------------------------

@needs_ffmpeg
def test_linear_parallel_render_uses_measured_gain(renders, monkeypatch):
    """並列レンダの音声レグにも測定済みの増幅量が入り、逐次レンダと同じ音量になる"""
    seen = []
    real = parallel_mod._run_ffmpeg

    def spy(cmd, *args, **kwargs):
        seen.append(list(cmd))
        return real(cmd, *args, **kwargs)

    monkeypatch.setattr(parallel_mod, "_run_ffmpeg", spy)
    p = _project(renders["layer"], "linear")
    out = os.path.join(renders["base"], "parallel.mp4")
    p.render(out, timeout=120, parallel=2)

    assert p._linear_gain_db == renders["linear_gain"]
    audio_legs = [c for c in seen if "-vn" in c]
    assert len(audio_legs) == 1, "並列レンダの音声レグが走っていない"
    leg_graph = audio_legs[0][audio_legs[0].index("-filter_complex") + 1]
    assert f"volume={renders['linear_gain']:.2f}dB" in leg_graph
    assert "loudnorm" not in leg_graph
    par_i, par_tp = _loudness(out)
    seq_i, _ = _loudness(renders["linear"])
    assert par_i == pytest.approx(seq_i, abs=0.1)
    assert par_tp <= TRUE_PEAK


@needs_ffmpeg
def test_linear_partial_render_uses_whole_timeline_measurement(renders):
    """部分レンダも全編の測定値で増幅する（窓だけ測ると BGM だけの窓が +20dB 以上になる）"""
    p = _project(renders["layer"], "linear")
    out = os.path.join(renders["base"], "partial.mp4")
    p.render(out, timeout=120, start=0, end=VOICE_AT)
    assert p._linear_gain_db == renders["linear_gain"]
    partial_bgm, _ = _loudness(out, SECTION_BGM)
    full_bgm, _ = _loudness(renders["linear"], SECTION_BGM)
    assert partial_bgm == pytest.approx(full_bgm, abs=0.3)


@needs_ffmpeg
def test_linear_outputs_without_audio_do_not_measure(renders, tmp_path, monkeypatch):
    """gif・サムネイル・音声の無いプロジェクトは測定パスを起動しない"""
    def _no_measure(*args, **kwargs):
        raise AssertionError("音声を出さない経路で測定パスを実行した")
    monkeypatch.setattr(loudness_mod, "_run_ffmpeg", _no_measure)

    p = _project(renders["layer"], "linear")
    dry = p.render(str(tmp_path / "anim.gif"), dry_run=True)
    assert not any("loudness" in k for k in dry["cache"])
    p.render(str(tmp_path / "anim.gif"), timeout=120)
    assert p._linear_gain_db is None

    p = _project(renders["layer"], "linear")
    p.thumbnail(3, str(tmp_path / "thumb.png"))
    assert os.path.getsize(tmp_path / "thumb.png") > 0

    # 音声 Object が1つも無い（映像だけの）プロジェクト
    silent_layer = tmp_path / "silent.py"
    silent_layer.write_text(
        "from scriptvedit import *\n"
        "Object(asset('images/shape_badge.png')).time(1)\n", encoding="utf-8")
    p = Project()
    p.configure(width=160, height=90, fps=8, background_color="black")
    p.normalize_audio(TARGET, mode="linear")
    p.layer(str(silent_layer))
    dry = p.render(str(tmp_path / "silent.mp4"), dry_run=True)
    assert not any("loudness" in k for k in dry["cache"])
    p.render(str(tmp_path / "silent.mp4"), timeout=120)
    assert p._linear_gain_db is None


# ---------------------------------------------------------------------------
# 増幅量の決め方・測定結果の読み取り（ffmpeg 不要）
# ---------------------------------------------------------------------------

def test_decide_linear_gain_rules():
    measured = {"integrated_lufs": -20.7, "true_peak_dbtp": -6.5}
    # 基本は target - 測定値（limiter=True ならピーク超過はリミッター任せ）
    assert _decide_linear_gain(-14, -1.5, True, measured) == (6.7, False)
    # limiter=False は内部上限（true_peak - 0.5 = -2.0）を超えない所で止める
    assert _decide_linear_gain(-14, -1.5, False, measured) == (4.5, True)
    # 上限に届かなければ limiter=False でも目標どおり
    assert _decide_linear_gain(-18, -1.5, False, measured) == (2.7, False)
    # 無音（統合ラウドネスを測れない）は増幅しない
    assert _decide_linear_gain(
        -14, -1.5, True, {"integrated_lufs": None, "true_peak_dbtp": None}) == (0.0, False)
    # 減衰方向もそのまま（-0.0 にならない）
    gain, _ = _decide_linear_gain(-14, -1.5, True,
                                  {"integrated_lufs": -14.0, "true_peak_dbtp": -3})
    assert gain == 0.0 and str(gain) == "0.0"


def test_parse_loudnorm_json_handles_silence_and_missing():
    lines = [
        "[Parsed_loudnorm_3 @ 000001] ",
        "{",
        '\t"input_i" : "-inf",',
        '\t"input_tp" : "-inf",',
        '\t"input_lra" : "0.00",',
        '\t"input_thresh" : "-70.00",',
        '\t"output_i" : "-inf",',
        '\t"target_offset" : "inf"',
        "}",
    ]
    assert _parse_loudnorm_json(lines) == {
        "integrated_lufs": None, "true_peak_dbtp": None,
        "lra_lu": 0.0, "threshold_lufs": -70.0}
    with pytest.raises(RuntimeError, match="見つかりません"):
        _parse_loudnorm_json(["[out#0/null] video:0KiB audio:0KiB"])


def test_read_measurement_rejects_malformed(tmp_path):
    path = tmp_path / "m.json"
    assert _read_measurement(str(path)) is None  # 無い
    path.write_text("[1, 2]", encoding="utf-8")
    assert _read_measurement(str(path)) is None  # dict でない
    path.write_text('{"integrated_lufs": -20.0}', encoding="utf-8")
    assert _read_measurement(str(path)) is None  # true_peak が無い
    path.write_text('{"integrated_lufs": true, "true_peak_dbtp": -3}', encoding="utf-8")
    assert _read_measurement(str(path)) is None  # bool は数値扱いしない
    path.write_text('{"integrated_lufs": null, "true_peak_dbtp": null}', encoding="utf-8")
    assert _read_measurement(str(path)) == {
        "integrated_lufs": None, "true_peak_dbtp": None}


def test_normalize_audio_mode_is_stored_and_validated():
    p = Project()
    p.normalize_audio(-16, mode="linear")
    assert p._loudnorm_options["mode"] == "linear"
    p.normalize_audio(-16)
    assert p._loudnorm_options["mode"] == "dynamic"
    with pytest.raises(ValueError, match="linear"):
        p.normalize_audio(-16, mode="liner")


@needs_ffmpeg
def test_linear_main_command_requires_measurement_outside_dry_run(tmp_path):
    """測定前に本レンダのコマンドを組もうとしたら表記を実行へ流さず止める"""
    wav = tmp_path / "tone.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", wav)
    layer = tmp_path / "l.py"
    layer.write_text("from scriptvedit import *\n"
                     f"Object({str(wav)!r}).time(1)\n", encoding="utf-8")
    p = _project(str(layer), "linear")
    p.render(str(tmp_path / "d.mp4"), dry_run=True)
    p._dry_run = False
    p._linear_gain_db = None
    with pytest.raises(RuntimeError, match="測定する前"):
        p._build_ffmpeg_cmd(str(tmp_path / "d.mp4"))


# ---------------------------------------------------------------------------
# from_project の署名（効く設定だけを鍵へ入れる）
# ---------------------------------------------------------------------------

@needs_ffmpeg
def test_from_project_signature_by_mode(tmp_path, monkeypatch):
    """dynamic の署名は mode 導入前と同一、linear は lra を含まず mode を含む"""
    import scriptvedit.objects as objects_mod
    png = tmp_path / "a.png"
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=4x4:d=1", "-frames:v", "1", png)
    sub_layer = tmp_path / "sub.py"
    sub_layer.write_text("from scriptvedit import *\n"
                         f"Object({str(png)!r}).time(1)\n", encoding="utf-8")
    parent_layer = tmp_path / "parent.py"
    parent_layer.write_text(
        "import os\n"
        "from scriptvedit import *\n"
        "sub = Project()\n"
        f"sub.layer({str(sub_layer)!r})\n"
        "mode, lra = os.environ['SV_TEST_LN'].split(':')\n"
        "sub.normalize_audio(-16, mode=mode, lra=int(lra))\n"
        "Object.from_project(sub).time(1)\n", encoding="utf-8")

    captured = []
    real_sig_key = objects_mod._sig_key

    def spy(sigs):
        captured.append([s for s in sigs if s.startswith("loudnorm_options=")])
        return real_sig_key(sigs)

    monkeypatch.setattr(objects_mod, "_sig_key", spy)

    def sig(mode, lra):
        captured.clear()
        monkeypatch.setenv("SV_TEST_LN", f"{mode}:{lra}")
        p = Project()
        p.layer(str(parent_layer))
        p.render(str(tmp_path / "o.mp4"), dry_run=True)
        found = [c for c in captured if c]
        assert found, "from_project の署名に loudnorm_options が無い"
        return found[-1][0]

    assert sig("dynamic", 11) == (
        "loudnorm_options={'true_peak': -1.5, 'lra': 11, 'limiter': True,"
        " 'sample_rate': 48000}")
    assert sig("dynamic", 7) != sig("dynamic", 11)
    assert sig("linear", 11) == (
        "loudnorm_options={'true_peak': -1.5, 'limiter': True,"
        " 'sample_rate': 48000, 'mode': 'linear'}")
    assert sig("linear", 7) == sig("linear", 11)  # linear では lra が効かない
