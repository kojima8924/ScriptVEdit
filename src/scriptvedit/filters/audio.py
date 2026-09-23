# -*- coding: utf-8 -*-

import builtins as _builtins
import math as _math

# context は scriptvedit 内 import を持たない葉なので先頭で import できる。
from scriptvedit.context import current_project

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.expr import Const


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


def _build_audio_effect_filters(obj, dur):
    """音声エフェクトフィルタを生成（avolume）。

    旧 again / afade は引数名（value / alpha）が違うだけの同一実装だったため
    avolume に一本化した（定数なら固定音量、Expr ならフェード）。
    """
    filters = []
    for e in obj.audio_effects:
        if e.name == "avolume":
            value_expr = e.params.get("value", Const(1))
            u_expr = f"clip((t)/{dur}\\,0\\,1)"
            ffmpeg_str = value_expr.to_ffmpeg(u_expr)
            filters.append(f"volume=volume='{ffmpeg_str}':eval=frame")
    return filters
