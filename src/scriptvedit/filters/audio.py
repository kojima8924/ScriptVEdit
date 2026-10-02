# -*- coding: utf-8 -*-

import builtins as _builtins
import math as _math

# context は scriptvedit 内 import を持たない葉なので先頭で import できる。
from scriptvedit.context import current_project

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.expr import Const, _UStr


# 音声を混ぜる前に全入力を揃える共通形式（48kHz・ステレオ・float planar）。
# amix / sidechaincompress / acrossfade の出力のチャンネル配置と
# サンプリング周波数は「先頭入力」に従う（FFmpeg 8 で実測）。入力の並びは
# priority 順＋生成順なので、モノラル 24kHz の TTS が先頭に来ると、章全体が
# 24kHz・モノラルになりステレオの BGM が L/R 平均に潰れる。これを防ぐため、
# 各入力の加工チェーンの**末尾**にこれを付けてから混ぜる。
# - 末尾に置くこと: aloop / arepeat の size は素材の実サンプルレートで
#   見積もっているので、先頭で 48kHz へ変えると size が不足しうる。
# - モノラル → ステレオの自動変換は各チャンネル約 -3dB（パンの法則。
#   libswresample の再マトリクスが中央を左右へ 1/√2 ずつ振る。実測 -3.01dB）。
# - 48000 は normalize_audio() の sample_rate 既定値・libopus の固定値と同じ。
_MIX_AUDIO_FORMAT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"

# duck_under（sidechaincompress）の検出用枝の形式。検出用枝は相手の加工チェーンの
# _MIX_AUDIO_FORMAT より**前**から asplit で取り出す（project.py の _build_ffmpeg_cmd）。
# 揃えた後から取ると、モノラルのナレーションは各チャンネル -3dB のステレオになり、
# sidechaincompress の既定 link=average では検出レベルがそのまま 3dB 下がる
# （既定値でダッキングが約 2.6dB 浅くなることを実測）。
# - 相手が1つ: チャンネル構成は素材のまま、サンプリング周波数だけ本線（48kHz）へ
#   明示的に揃える（sidechaincompress は全入出力で同じ周波数を要求する。自動の
#   交渉に任せると、どちらの枝がリサンプルされるかがグラフの並びに左右されうる）。
_SIDECHAIN_FORMAT = "aformat=sample_fmts=fltp:sample_rates=48000"
# - 相手が複数: amix で合算するので全枝の形式を揃える必要がある（揃えないと
#   合算結果が先頭入力の形式に従い、並び順で検出レベルが 3dB 変わる）。
#   48kHz モノラルへダウンミックスする。rematrix_maxval=1 で係数を正規化するので
#   ステレオは (L+R)/2 になり、link=average の検出（|L| と |R| の平均）と
#   左右が同相の音で一致する。モノラルは変換されず元の音量のまま。
_SIDECHAIN_MIX_FORMAT = "aresample=48000:ochl=mono:rematrix_maxval=1"


# duck_under(hold=…) の包絡を作るときのサンプリング周波数（_SIDECHAIN_MIX_FORMAT の出力）
_SIDECHAIN_HOLD_RATE = 48000

# hold 中に保つ「直前の発声の平均的な検出レベル」の時定数（秒）
_SIDECHAIN_HOLD_AVG_SEC = 0.2


def _sidechain_hold_filter(threshold, attack, release, hold):
    """duck_under の検出用枝（48kHz モノラル・apad 済み）に保持つきの包絡を作る aeval。

    sidechaincompress には保持（hold）が無く、release だけでは読点や文の間
    （0.3〜0.6 秒）のたびに BGM が戻りかける。ここで検出用の枝そのものを
    「保持つきの包絡」（正の直流に近い信号）へ置き換えてから渡す。
    aeval の st() / ld() の変数はサンプルをまたいで残る（FFmpeg 8 で実測）ので、
    1サンプルごとに次の3つを更新する:
      ld(0) … 包絡 s（二乗値）。sidechaincompress（detection=rms）と同じ一次の追従
              s += (x² − s) × (x² > s ? ka : kr)、ka / kr = 1/(ms × rate / 4000)
      ld(1) … 保持の残りサンプル数。s が threshold² を超えている間は hold へ巻き戻す
      ld(2) … 発声中の s の平均 m（時定数 _SIDECHAIN_HOLD_AVG_SEC）。保持が切れたら 0
    出力は sqrt(max(s, m))。発声中は s がそのまま出るので下げ幅は hold 無しと
    ほぼ同じ（谷が m まで埋まる分だけ実測で約 1dB 深い）。相手が止むと m を
    hold ms だけ保ち、その後 0 へ落ちて sidechaincompress の release で戻る。
    式の中の「*0+」は、st() の戻り値を捨てて順に評価させるための書き方。
    """
    rate = _SIDECHAIN_HOLD_RATE
    ka = _builtins.min(1.0, 1.0 / (attack * rate / 4000.0)) if attack > 0 else 1.0
    kr = _builtins.min(1.0, 1.0 / (release * rate / 4000.0)) if release > 0 else 1.0
    km = 1.0 / (_SIDECHAIN_HOLD_AVG_SEC * rate)
    thr2 = threshold * threshold
    n = int(_builtins.round(hold * rate / 1000.0))
    expr = (
        f"st(0\\,ld(0)+(val(0)*val(0)-ld(0))*if(gt(val(0)*val(0)\\,ld(0))\\,{ka!r}\\,{kr!r}))*0"
        f"+st(1\\,if(gt(ld(0)\\,{thr2!r})\\,{n}\\,ld(1)-1))*0"
        f"+st(2\\,if(gt(ld(1)\\,0)\\,ld(2)+(ld(0)-ld(2))*if(gt(ld(0)\\,{thr2!r})\\,{km!r}\\,0)\\,0))*0"
        f"+sqrt(max(ld(0)\\,ld(2)))")
    return f"aeval='{expr}':c=same"


def _atempo_chain_rates(rate):
    """atempoの有効範囲(0.5〜100)を超えるレートを複数段に分解する。
    範囲内はそのまま1段で返す（既存出力との互換維持）。"""
    try:
        r = float(rate)
    except (TypeError, ValueError):
        return [rate]
    if r <= 0 or 0.5 <= r <= 100.0:
        return [rate]  # 範囲内（or 不正値はffmpegに検出させる）
    rates = []
    while r < 0.5:
        rates.append(0.5)
        r /= 0.5
    while r > 100.0:
        rates.append(100.0)
        r /= 100.0
    rates.append(_builtins.round(r, 6))
    return rates


def _build_audio_pre_filters(obj):
    """atrim/atempo等の前処理フィルタ"""
    filters = []
    for e in obj.audio_effects:
        if e.name == "atrim":
            d = e.params.get("duration")
            s = e.params.get("start") or 0
            parts = ([f"start={s}"] if s else []) \
                + ([f"duration={d}"] if d is not None else [])
            if parts:
                # atrim の duration は「出力の最大尺」（start=2:duration=3 → 2〜5秒）
                filters.append("atrim=" + ":".join(parts))
                filters.append("asetpts=PTS-STARTPTS")
        elif e.name == "atempo":
            rate = e.params.get("rate", 1.0)
            for r in _atempo_chain_rates(rate):
                filters.append(f"atempo={r}")
        elif e.name == "arepeat":
            # obj * n（DSL糖衣）の音声側: 区間全体を n 回連続再生。
            # aloop の size は「ループ対象としてバッファするサンプル数」
            # （segment × sample_rate）。aloop に入る時点の音声は直前の
            # atrim/atempo 適用後＝ちょうど segment 秒なので、size が実サンプル数
            # を上回っても全区間をバッファして繰り返すだけで無害。逆に足りないと
            # 各周回の末尾が黙って欠ける。**必ず多めに見積もる**こと。
            # sample_rate は probe で取得し、不能時（素材が読めない等）は
            # 192kHz 相当で見積もる。ここを 44100 固定にしていると、48kHz 素材で
            # size が約8%不足し毎周ぶん末尾が落ちる（project.py の
            # _build_aloop_filter も同じ理由で 192000 を使っている）。
            #
            # probe 先は obj.source ではなく **obj.audio_source（元素材）**。
            # source はチェックポイントで `-an` の映像専用中間物へ差し替わりうるので、
            # そちらを見ると sample_rate が取れないうえ、cold/warm で probe の成否が
            # 変わって dry_run の出力がキャッシュ状態に依存してしまう。
            n = e.params["count"]
            segment = e.params["segment"]
            sr = None
            proj = current_project()
            if proj is not None:
                info = proj._probe_media(obj.audio_source)
                sr = (info or {}).get("sample_rate")
            sr = sr or 192000
            size = int(_math.ceil(segment * sr))
            filters.append(f"aloop=loop={n - 1}:size={size}")
            filters.append("asetpts=N/SR/TB")
    return filters


# volume フィルタの式で使う時刻 t（NaN を 0 に読み替える）。
# volume は eval=frame でも**初期化時に1回** t=NaN で式を評価する（FFmpeg 8 の
# af_volume.c: config_output が変数を NAN で埋めて set_volume を呼ぶ）。
# clip(NaN/dur,0,1) は NaN なので、式を使う音声1本ごとに
# "Invalid value NaN for volume, setting to 0" の警告が出て、数百行で本当の
# エラーが埋もれる。初期化時の値は使われず（各フレームで t を入れて評価し直す）
# 実害は無いが、ログを汚さないよう NaN の間だけ 0 として評価させる。
# フレームごとの t は NaN にならないので、出力音声は置き換え前と同一。
_VOLUME_T_EXPR = "if(isnan(t)\\,0\\,t)"

# 時間で変わる volume の前に入れるフレームの刻み（_build_audio_effect_filters の説明を参照）
_VOLUME_EXPR_FRAMING = "asetnsamples=n=256:p=0"


def _build_audio_effect_filters(obj, dur):
    """音声エフェクトフィルタを生成（avolume）。

    旧 again / afade は引数名（value / alpha）が違うだけの同一実装だったため
    avolume に一本化した（定数なら固定音量、Expr ならフェード）。
    """
    filters = []
    for e in obj.audio_effects:
        if e.name == "avolume":
            value_expr = e.params.get("value", Const(1))
            # _UStr: 秒で書く式（elapsed / ramp / keyframes_sec）用に経過秒も渡す
            u_expr = _UStr(f"clip({_VOLUME_T_EXPR}/{dur}\\,0\\,1)", dur,
                           sec=f"clip({_VOLUME_T_EXPR}\\,0\\,{dur})")
            ffmpeg_str = value_expr.to_ffmpeg(u_expr)
            if not isinstance(value_expr, Const):
                # 時間で変わる音量は、フレームを細かく刻んでから評価する。
                # volume（eval=frame）は音声フレーム1枚につき式を1回しか評価しない。
                # デコーダのフレームは 1024〜4096 サンプル（44.1kHz で最大約 93ms）あり、
                # 立ち上がりのフェード clip(u/a,0,1) は最初の1枚が丸ごと u=0（無音）になって
                # 語の頭が最大 93ms 欠ける。256 サンプル（48kHz で約 5ms）に刻めば、
                # フェードや音量の変化が 5ms 単位で効く。定数の音量は刻む必要が無い。
                filters.append(_VOLUME_EXPR_FRAMING)
            filters.append(f"volume=volume='{ffmpeg_str}':eval=frame")
    return filters
