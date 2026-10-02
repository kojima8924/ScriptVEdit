# -*- coding: utf-8 -*-
"""normalize_audio(mode="linear") の2パス線形正規化（測定パスと増幅量の決定）。

dynamic（既定）は測定値を渡さない1パスの loudnorm で、3秒窓の短期ラウドネスを
目標へ寄せ続ける。声の無い区間では BGM が持ち上がり、声が入ると沈む
（ポンピング）。linear は次の2段で、区間どうしの音量差を変えない:

1. 測定パス: 本レンダと同じ音声グラフ（_build_ffmpeg_cmd の音声チェーン）を
   音声だけ・全編・null 出力で1回流し、ミックス結果の統合ラウドネスと
   true peak を loudnorm の print_format=json で測る。
2. 本レンダ: 一定の増幅 volume=(target - 測定値)dB → aresample → alimiter。
   動的な loudnorm は通さない。

測定結果は ``__cache__/artifacts/loudness/<鍵>.json`` に保存する。鍵は測定
コマンドそのもの（入力は _src_signature＝素材は内容指紋・キャッシュ生成物は
パス署名）から作るので、音声グラフと音声素材が同じなら再測定しない
（映像だけを直した再レンダで毎回全編を流さない）。目標値・true_peak・
limiter・sample_rate は測定コマンドに現れない＝鍵に入らないので、
それらを変えても測り直さない。

dry_run との関係（CLAUDE.md §3「dry_run はキャッシュ状態に依存しない」）:
  - 測定パスのコマンドは dry_run の ``cache`` 側に「実行予定」として常に出す
    （JSON の有無は見ない。_collect_loudness_cmds）。
  - 増幅量は測定して初めて決まるので、dry_run の ``main`` では
    _LINEAR_GAIN_PLACEHOLDER（``volume=<MEASURED_GAIN>dB``）と表記する。
    warm（測定済み）でも同じ表記にする（キャッシュ状態で main を変えない）。

parallel.py / preview.py と同じく「project を第1引数に取る自由関数」として
project.py から切り出している（Project は import しない＝循環 import を増やさない）。
"""

import json
import math as _math
import os
import re

from scriptvedit.cache import _sig_key, _src_signature
from scriptvedit.ffmpeg import _atomic_write_text, _run_ffmpeg
from scriptvedit.objects import Object
from scriptvedit.state import _ARTIFACT_DIR, _ENGINE_VER, _GEN_COUNTER, _GEN_COUNTER_LOCK
from scriptvedit.warn import _warn


# 測定フィルタ。目標値などの引数は input_* の測定結果に影響しないので付けない
# （付けると target を変えただけで測定の鍵が変わり、全編を測り直すことになる）。
# ebur128 ではなく loudnorm を使うのは、測定値が小数2桁の JSON で得られるため
# （ebur128 の Summary は小数1桁のテキスト）。どちらも libavfilter の同じ
# EBU R128 実装（ff_ebur128）で測る。
_LOUDNESS_MEASURE_FILTER = "loudnorm=print_format=json"

# 測定パスの出力先（null muxer。何も書かない）
_LOUDNESS_MEASURE_OUTPUT = "-"

# dry_run の main で増幅量の代わりに置く表記。増幅量は測定パスを実行して
# 初めて決まる（dry_run は測定しない）。実行されるコマンドでは
# _format_gain_db が返す "3.21dB" の形になる。
_LINEAR_GAIN_PLACEHOLDER = "<MEASURED_GAIN>dB"

# loudnorm の print_format=json 出力（最後の1個を使う）
_LOUDNORM_JSON_RE = re.compile(r'\{\s*"input_i".*?\}', re.DOTALL)

# 測定 JSON に必ずある数値（None は -inf＝無音で測れない、の意味）
_MEASUREMENT_KEYS = ("integrated_lufs", "true_peak_dbtp")


def _processing_peak(true_peak):
    """loudnorm / alimiter / 線形の増幅上限に使う内部のピーク上限 (dB)。

    AAC/Opus の量子化で true peak がわずかに再上昇するため、利用者の
    true_peak は最終出力の目標とし、内部処理は 0.5dB 低くする。
    loudnorm の TP の許容下限は -9。dynamic / linear で同じ値を使う。
    """
    return max(-9.0, float(true_peak) - 0.5)


def _format_gain_db(gain):
    """増幅量を volume フィルタの値（例: "3.21dB"）へ整形する。

    av_strtod が "dB" 接尾辞を 10^(x/20) の倍率として解釈する。
    """
    return f"{gain:.2f}dB"


def _linear_mode(project):
    """normalize_audio(mode="linear") が設定されているか"""
    opts = project._loudnorm_options
    return (project._loudnorm_target is not None and bool(opts)
            and opts.get("mode") == "linear")


def _needs_measurement(project, output_path):
    """この出力で測定パスが要るか。

    _build_ffmpeg_cmd が正規化チェーンを組む条件と同じ:
    音声を持てる出力形式（h264 / webm）で、音声を持つ Object がある。
    gif / webp / png連番 / サムネイル / 絵コンテは音声枝自体を組まない。
    """
    if not _linear_mode(project):
        return False
    if not project._resolve_output_format(output_path)["has_audio"]:
        return False
    return any(isinstance(o, Object) and o.has_audio for o in project.objects)


def _build_loudness_measure_cmd(project):
    """測定パス（音声だけ・全編・null 出力）の ffmpeg コマンドを組む。

    音声グラフは本レンダと同じ _build_ffmpeg_cmd を測定モードで呼んで得る
    （並列レンダの音声レグ parallel._build_audio_leg_cmd と同じ再利用の仕方）。
    別に組み直すと、duck_under・loop・形式統一などの変更が測定側にだけ
    反映されない乖離が起きる。映像の無い Object は入力にも含めない
    （測定の鍵が映像側の変更で動かないように）。
    """
    saved_objects = project.objects
    project._loudness_measure_render = True
    try:
        project.objects = [o for o in saved_objects
                           if isinstance(o, Object) and o.has_audio]
        return project._build_ffmpeg_cmd(_LOUDNESS_MEASURE_OUTPUT)
    finally:
        project.objects = saved_objects
        project._loudness_measure_render = False


def _loudness_measure_path(cmd):
    """測定結果 JSON のキャッシュパス（測定コマンド由来の鍵）。

    入力パスは _src_signature に置き換えて鍵へ入れる（生パスを鍵に混ぜない。
    CLAUDE.md §5）。lavfi の入力（-f lavfi -i color=...）はパスではないので
    そのまま入れる。
    """
    sigs = ["loudness_measure", f"ev={_ENGINE_VER}"]
    for i, arg in enumerate(cmd):
        arg = str(arg)
        if (i >= 1 and cmd[i - 1] == "-i"
                and not (i >= 2 and cmd[i - 2] == "lavfi")):
            sigs.append(f"in:{_src_signature(arg)}")
        else:
            sigs.append(arg)
    return os.path.join(_ARTIFACT_DIR, "loudness", f"{_sig_key(sigs)}.json")


def _collect_loudness_cmds(project, output_path):
    """dry_run 用: {測定結果JSONのパス: 測定コマンド}（測定不要なら空）。

    JSON の有無は見ない（dry_run は「キャッシュが空の状態で何を実行するか」を
    返す契約。CLAUDE.md §3 の4経路と同じ扱い）。
    """
    if not _needs_measurement(project, output_path):
        return {}
    cmd = _build_loudness_measure_cmd(project)
    return {_loudness_measure_path(cmd): cmd}


def _finite_or_none(value):
    """数値へ変換し、±inf / NaN は None にする（loudnorm は無音で "-inf" を出す）"""
    v = float(value)
    return v if _math.isfinite(v) else None


def _parse_loudnorm_json(lines):
    """ffmpeg の stderr（行のリスト）から loudnorm の測定 JSON を取り出す。"""
    text = "\n".join(lines or [])
    matches = _LOUDNORM_JSON_RE.findall(text)
    if not matches:
        raise RuntimeError(
            "normalize_audio(mode='linear'): ラウドネス測定の結果（loudnorm の "
            "JSON）が ffmpeg の出力に見つかりません")
    raw = json.loads(matches[-1])
    try:
        return {
            "integrated_lufs": _finite_or_none(raw["input_i"]),
            "true_peak_dbtp": _finite_or_none(raw["input_tp"]),
            "lra_lu": _finite_or_none(raw["input_lra"]),
            "threshold_lufs": _finite_or_none(raw["input_thresh"]),
        }
    except (KeyError, TypeError, ValueError) as e:
        raise RuntimeError(
            "normalize_audio(mode='linear'): ラウドネス測定の結果を読めません: "
            f"{raw!r}") from e


def _read_measurement(path):
    """保存済みの測定結果を読む。無い・壊れている・形式違いなら None（再測定）。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for key in _MEASUREMENT_KEYS:
        if key not in data:
            return None
        v = data[key]
        if v is None:
            continue
        if (isinstance(v, bool) or not isinstance(v, (int, float))
                or not _math.isfinite(v)):
            return None
    return data


def _decide_linear_gain(target, true_peak, limiter, measured):
    """測定値から一定の増幅量 (dB) を決める。戻り値: (増幅量, 上限で抑えたか)

    - 基本は target - 統合ラウドネス。区間どうしの音量差はそのまま保たれる。
    - 無音（統合ラウドネスを測れない）なら増幅しない。
    - limiter=False のときはピークを抑える手段が無いので、測定 true peak +
      増幅量が内部上限（_processing_peak）を超えない所で増幅を止める
      （統合ラウドネスは目標より低くなる）。limiter=True なら上限を超える
      ピークだけをリミッターが抑える。
    """
    integrated = measured.get("integrated_lufs")
    if integrated is None:
        return 0.0, False
    gain = float(target) - float(integrated)
    capped = False
    peak = measured.get("true_peak_dbtp")
    if not limiter and peak is not None:
        headroom = _processing_peak(true_peak) - float(peak)
        if gain > headroom:
            gain = headroom
            capped = True
    gain = round(gain, 2)
    return (0.0 if gain == 0 else gain), capped


def _ensure_linear_gain(project, output_path, timeout):
    """実レンダ用: 測定（キャッシュ済みなら読むだけ）→ 増幅量を project へ設定する。

    本レンダ・並列レンダの音声レグは project._linear_gain_db を
    volume=<増幅>dB として使う。測定は部分レンダ（start/end）でも全編を対象にする
    （同じ動画のどの区間を書き出しても同じ増幅量になるように）。
    """
    project._linear_gain_db = None
    if not _needs_measurement(project, output_path):
        return None
    opts = project._loudnorm_options
    target = project._loudnorm_target
    cmd = _build_loudness_measure_cmd(project)
    path = _loudness_measure_path(cmd)
    project._note_planned_artifact(path)
    measured = _read_measurement(path)
    cached = measured is not None
    if not cached:
        print("ラウドネス測定（normalize_audio mode='linear'）: "
              "音声だけを全編1回流して統合ラウドネスを測ります")
        if project._verbose():  # SCRIPTVEDIT_VERBOSE=1（本レンダのコマンド表示と同じ扱い）
            print(f"  ffmpeg {' '.join(str(a) for a in cmd[1:])}")
        # -loglevel info の出力（入力ごとのストリーム情報）で端末を埋めないよう
        # 親へは流さない。失敗時は FFmpegError が stderr 末尾の原因行を載せる。
        tail = _run_ffmpeg(
            cmd, timeout=timeout, echo=False,
            context="normalize_audio(mode='linear') のラウドネス測定"
                    "（音声のみ・全編・null 出力）")
        measured = _parse_loudnorm_json(tail)
        _atomic_write_text(
            path, json.dumps(measured, ensure_ascii=False, indent=2) + "\n")
        with _GEN_COUNTER_LOCK:
            _GEN_COUNTER[0] += 1
    gain, capped = _decide_linear_gain(
        target, opts["true_peak"], opts["limiter"], measured)
    project._linear_gain_db = gain
    integrated = measured["integrated_lufs"]
    peak = measured["true_peak_dbtp"]
    src = "測定キャッシュ" if cached else "測定"
    if integrated is None:
        print(f"線形正規化: 音声が無音で統合ラウドネスを測れないため増幅しません（{src}）")
        return gain
    peak_txt = "-inf" if peak is None else f"{peak:.2f}"
    line = (f"線形正規化: 統合 {integrated:.2f} LUFS / true peak {peak_txt} dBTP"
            f" → 増幅 {gain:+.2f} dB（目標 {target} LUFS・{src}）")
    if opts["limiter"] and peak is not None:
        over = peak + gain - _processing_peak(opts["true_peak"])
        if over > 0:
            line += f"。上限を超えるピークはリミッターが最大 {over:.2f} dB 抑えます"
    print(line)
    if capped:
        _warn(project,
              f"normalize_audio(mode='linear', limiter=False): true peak を"
              f" {opts['true_peak']} dBTP 以下に保つため増幅を {gain:+.2f} dB に"
              f"抑えました。統合ラウドネスは目標 {target} LUFS より約"
              f" {float(target) - integrated - gain:.2f} LU 低くなります"
              f"（目標どおりにするには limiter=True）")
    return gain
