# -*- coding: utf-8 -*-

import builtins as _builtins


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
            # aloop の size はサンプル数（segment × sample_rate）。
            # sample_rate は probe で取得し、不能時は 44100 へフォールバック
            # （dry_run の未生成キャッシュ等。実レンダでは通常 probe できる）
            n = e.params["count"]
            segment = e.params["segment"]
            sr = None
            proj = Project._current
            if proj is not None:
                info = proj._probe_media(obj.source)
                sr = (info or {}).get("sample_rate")
            sr = sr or 44100
            filters.append(
                f"aloop=loop={n - 1}:size={int(round(segment * sr))}")
            filters.append("asetpts=N/SR/TB")
    return filters


def _build_audio_effect_filters(obj, dur):
    """音声エフェクトフィルタを生成（again/afade）"""
    filters = []
    for e in obj.audio_effects:
        if e.name == "again":
            value_expr = e.params.get("value", Const(1))
            u_expr = f"clip((t)/{dur}\\,0\\,1)"
            ffmpeg_str = value_expr.to_ffmpeg(u_expr)
            filters.append(f"volume=volume='{ffmpeg_str}':eval=frame")
        elif e.name == "afade":
            alpha_expr = e.params.get("alpha", Const(1.0))
            u_expr = f"clip((t)/{dur}\\,0\\,1)"
            ffmpeg_str = alpha_expr.to_ffmpeg(u_expr)
            filters.append(f"volume=volume='{ffmpeg_str}':eval=frame")
    return filters


# --- 遅延解決の相互参照（関数本体からのみ使用: 循環importを避けるため末尾で束縛）---
from scriptvedit.expr import Const
from scriptvedit.project import Project
