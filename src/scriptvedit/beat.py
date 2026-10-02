# -*- coding: utf-8 -*-
"""scriptvedit.beat — numpy/scipy だけで音楽のビート(拍)時刻を検出するモジュール

scriptvedit のキーフレーム/カット同期用。librosa 非依存。
依存: numpy, scipy, ffmpeg(サブプロセス経由でデコードのみ)

主な API:
    detect_beats(audio_path, ...)  -> {"bpm": float, "beats": [...], "onsets": [...]}
    snap_times(times, beats)       -> 任意の時刻を最近傍ビートへスナップ
    beats_to_keyframes(beats, values, ...) -> scriptvedit.keyframes 用のフラット列

アルゴリズム概要:
    1. ffmpeg で mono f32le PCM にデコード
    2. STFT のスペクトラルフラックス(対数圧縮振幅の正方向差分和)で onset 強度包絡を計算
    3. onset 包絡の自己相関から min_bpm..max_bpm 範囲でテンポ(周期)候補を生成し、
       グリッド当てはめスコアで選択(倍/半テンポの曖昧性は「同スコアなら最速」で解決)
    4. 周期・位相を総当たりで微調整(等間隔グリッドが onset に最も乗る組を探索)
    5. 各グリッド拍を近傍の onset ピークへガウス重み付きでスナップ

CLI:
    python -m scriptvedit.beat song.mp3          # BPM と最初の20ビート時刻を表示
    python -m scriptvedit.beat song.mp3 --json   # 全結果を JSON 出力
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import warnings

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import fftconvolve, find_peaks

__all__ = [
    "detect_beats",
    "onset_strength",
    "snap_times",
    "beats_to_keyframes",
]

# デコード時のデフォルトサンプリングレート
_DEFAULT_SR = 22050
# STFT 窓長
_N_FFT = 2048
# STFT を処理するフレームチャンク数(メモリ節約)
_CHUNK_FRAMES = 4096
# ffmpeg デコードの上限（他の ffmpeg 実行経路と同じ）
_DECODE_TIMEOUT = 600
# ビート解析の上限尺[秒]。長尺入力を全量デコードすると PCM 保持だけで
# メモリを圧迫し(1時間音声で約317MB)、後段の解析も肥大化する。
# BPM 推定に全曲は不要なので、超える場合は先頭のみを解析する(issue #13 P2-16)。
_MAX_ANALYSIS_SEC = 900


# ---------------------------------------------------------------------------
# 音声読み込み
# ---------------------------------------------------------------------------

def _load_mono(audio_path, sr=_DEFAULT_SR):
    """ffmpeg で任意の音声/動画ファイルを mono PCM(f32le) にデコードして読み込む。

    長尺入力のメモリ枯渇対策(issue #13 P2-16)として、デコード段階で
    `-t` により先頭 `_MAX_ANALYSIS_SEC`+1 秒までに制限する。上限を超えていた
    場合は先頭 `_MAX_ANALYSIS_SEC` 秒へ切り詰め、警告を出す(+1 秒の余分は
    「ちょうど上限尺の入力」と「上限超過の入力」を区別するための判定マージン)。

    Args:
        audio_path: 入力ファイルパス(音声・動画どちらでも可)
        sr: サンプリングレート
    Returns:
        np.ndarray (float32, mono)
    """
    max_sec = float(_MAX_ANALYSIS_SEC)
    cmd = [
        "ffmpeg", "-v", "error",
        "-i", str(audio_path),
        "-t", f"{max_sec + 1.0:.6f}",
        "-f", "f32le", "-ac", "1", "-ar", str(int(sr)),
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=_DECODE_TIMEOUT)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg が見つかりません。PATH を確認してください。")
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"ffmpeg デコードが {_DECODE_TIMEOUT} 秒でタイムアウトしました: "
            f"{audio_path}") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg デコード失敗: {audio_path}\n{err}")
    y = np.frombuffer(proc.stdout, dtype=np.float32)
    if y.size == 0:
        raise RuntimeError(f"音声データが空です: {audio_path}")
    limit = int(max_sec * sr)
    if y.size > limit:
        warnings.warn(
            f"音声が解析上限 {max_sec / 60:.1f} 分を超えるため、"
            f"先頭 {max_sec / 60:.1f} 分のみでビート解析します: {audio_path}",
            RuntimeWarning, stacklevel=2)
        y = y[:limit]
    return y


# ---------------------------------------------------------------------------
# onset 強度(スペクトラルフラックス)
# ---------------------------------------------------------------------------

def onset_strength(y, sr, hop=512, n_fft=_N_FFT):
    """スペクトラルフラックスによる onset 強度包絡を計算する。

    STFT の隣接フレーム間で対数圧縮振幅の差分の正部分を周波数方向に合計する。
    さらに移動平均を引いてトレンド除去し、0..1 に正規化する。

    Args:
        y: mono 波形 (float32)
        sr: サンプリングレート
        hop: ホップ長(サンプル)
        n_fft: FFT 窓長
    Returns:
        (times, strength): 各フレームの時刻[秒]と onset 強度 (どちらも np.ndarray)
    """
    y = np.asarray(y, dtype=np.float32)
    if y.size < n_fft:
        y = np.pad(y, (0, n_fft - y.size))
    # 中央寄せ(フレーム時刻 = フレーム中心)
    y = np.pad(y, n_fft // 2)
    n_frames = 1 + (y.size - n_fft) // hop
    win = np.hanning(n_fft).astype(np.float32)

    frames_view = np.lib.stride_tricks.sliding_window_view(y, n_fft)[::hop][:n_frames]
    flux = np.zeros(n_frames, dtype=np.float64)
    prev_last = None  # 前チャンクの最終フレームの対数振幅スペクトル
    for start in range(0, n_frames, _CHUNK_FRAMES):
        chunk = frames_view[start:start + _CHUNK_FRAMES] * win
        # scipy.fft は float32 入力なら complex64 を返しメモリ効率が良いが、
        # np.fft でも動作は同じなので互換性優先で np.fft を使用(チャンク化で節約)
        spec = np.abs(np.fft.rfft(chunk, axis=1))
        # 対数圧縮(小音量のオンセットも拾う)
        s = np.log1p(1000.0 * spec)
        if prev_last is not None:
            s_cat = np.concatenate([prev_last[None, :], s], axis=0)
        else:
            s_cat = s
        diff = np.diff(s_cat, axis=0)
        pos = np.maximum(diff, 0.0).sum(axis=1)
        if prev_last is None:
            # 最初のフレームはフラックス 0
            pos = np.concatenate([[0.0], pos])
        flux[start:start + len(pos)] = pos
        prev_last = s[-1]

    # トレンド除去: 局所移動平均(約0.4秒)を引いて正部分のみ残す
    trend_size = max(3, int(round(0.4 * sr / hop)) | 1)
    trend = uniform_filter1d(flux, size=trend_size, mode="nearest")
    env = np.maximum(flux - trend, 0.0)
    # 軽い平滑化
    env = uniform_filter1d(env, size=3, mode="nearest")
    peak = env.max()
    if peak > 0:
        env = env / peak
    times = np.arange(n_frames) * (hop / sr)
    return times, env


# ---------------------------------------------------------------------------
# テンポ推定(自己相関)
# ---------------------------------------------------------------------------

def _parabolic_refine(acf, lag):
    """自己相関ピークを放物線補間でサブフレーム精度に微調整する"""
    lag_f = float(lag)
    if 1 <= lag < len(acf) - 1:
        a, b, c = acf[lag - 1], acf[lag], acf[lag + 1]
        denom = a - 2 * b + c
        if abs(denom) > 1e-12:
            delta = 0.5 * (a - c) / denom
            if abs(delta) < 1.0:
                lag_f += delta
    return lag_f

def _tempo_candidates(env, frame_dt, min_bpm, max_bpm, max_cands=6):
    """onset 包絡の自己相関からビート周期[秒]の候補リストを返す。

    min_bpm..max_bpm に対応するラグ範囲の自己相関ピーク(上位)に加え、
    各候補の 1/2 倍・2 倍周期(範囲内のもの)も候補に含める。
    倍/半テンポの曖昧性は後段のグリッド当てはめで解決する。
    """
    x = env - env.mean()
    n = len(x)
    # 自己相関は O(n^2) の np.correlate(mode="full") だと長尺で実質停止する
    # (onset 15.5万点で約240億積和)ため、FFT ベース(O(n log n))で計算する。
    # fftconvolve(x, x[::-1]) の後半 n 点は np.correlate の full 出力の
    # lag>=0 部分と(浮動小数点誤差を除き)同一(issue #13 P2-16)。
    acf = fftconvolve(x, x[::-1], mode="full")[n - 1:]
    if acf[0] <= 0:
        raise RuntimeError("onset 包絡が無音のためテンポ推定できません")
    acf = acf / acf[0]

    lag_min = max(1, int(np.floor(60.0 / max_bpm / frame_dt)))
    lag_max = min(n - 2, int(np.ceil(60.0 / min_bpm / frame_dt)))
    if lag_max <= lag_min:
        raise RuntimeError("音声が短すぎるか BPM 範囲が不正です")

    # 範囲内の局所ピーク(自己相関値の上位)を候補にする(なければ最大値)
    seg = acf[lag_min:lag_max + 1]
    peaks, _ = find_peaks(seg)
    if len(peaks) == 0:
        base_lags = [lag_min + int(np.argmax(seg))]
    else:
        peaks = peaks[np.argsort(seg[peaks])[::-1][:max_cands]]
        base_lags = [lag_min + int(p) for p in peaks]

    # 1/2 倍・2 倍周期も候補に加える(範囲内のみ)
    lags = set()
    for lag in base_lags:
        for m in (0.5, 1.0, 2.0):
            l2 = int(round(lag * m))
            if lag_min <= l2 <= lag_max:
                # 近傍の自己相関ピークへ寄せる
                lo = max(lag_min, l2 - 2)
                hi = min(lag_max, l2 + 2)
                l2 = lo + int(np.argmax(acf[lo:hi + 1]))
                lags.add(l2)

    periods = sorted(_parabolic_refine(acf, lag) * frame_dt for lag in lags)
    # ほぼ同一の候補を統合
    out = []
    for p in periods:
        if not out or abs(p - out[-1]) > frame_dt:
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# 位相・周期の微調整(グリッド総当たり)
# ---------------------------------------------------------------------------

def _grid_score(times, env, period, phase, duration):
    """等間隔グリッド(phase + k*period)上の onset 強度合計(平均)を返す"""
    k = np.arange(int(np.floor((duration - phase) / period)) + 1)
    grid = phase + k * period
    if len(grid) == 0:
        return -np.inf
    return float(np.interp(grid, times, env).mean())

def _select_period(times, env, periods, duration):
    """周期候補ごとに最良位相のグリッド当てはめスコアを計算して 1 つ選ぶ。

    倍/半テンポの曖昧性解決:
      - 半テンポ候補(周期 2P)は真テンポ P と同じ拍位置の部分集合しか踏まない
        ため「拍あたり平均 onset 強度」がほぼ同一(スコア比≈1.0)になる。
      - 倍テンポ候補(周期 P/2)は真の拍に加えてサブディビジョン位置も踏む
        ため、そこが弱拍/無音なら平均スコアが下がる(スコア比 < 1.0)。
    そこで「最高スコアの 96% 以上」に入る実質同点候補に絞り込み、その中で
    最速(最小周期)を選ぶ。こうすると半テンポ(2P)は同点で残しても最小周期の
    P が選ばれ、倍テンポ(P/2)はスコアが僅かに劣るため足切りされる。
    閾値を 0.90 から 0.96 へ厳しくしたのは、8分ハイハット等でサブディビジョン
    にもオンセットがある曲で P/2 が 0.90〜0.95 の僅差に入り込み、最速選択に
    引っ張られて倍BPMを誤検出するのを防ぐため。
    """
    scores = []
    for period in periods:
        best = -np.inf
        for phase in np.arange(0.0, period, 0.008):
            s = _grid_score(times, env, period, phase, duration)
            if s > best:
                best = s
        scores.append(best)
    smax = max(scores)
    eligible = [p for p, s in zip(periods, scores) if s >= 0.96 * smax]
    return min(eligible)

def _refine_grid(times, env, period0, duration):
    """周期±2%・位相を総当たりして、onset に最も乗る (period, phase) を返す。

    自己相関のみの周期はフレーム量子化誤差が累積してビートがドリフトするため、
    実際のグリッド当てはめで微調整する。
    """
    best = (-np.inf, period0, 0.0)
    for period in np.linspace(period0 * 0.98, period0 * 1.02, 33):
        # 位相は 4ms 刻みで総当たり
        for phase in np.arange(0.0, period, 0.004):
            s = _grid_score(times, env, period, phase, duration)
            if s > best[0]:
                best = (s, period, phase)
    # 最良点の近傍を 1ms 刻みでさらに微調整
    _, p0, ph0 = best
    for period in np.linspace(p0 * 0.997, p0 * 1.003, 13):
        for phase in np.arange(max(0.0, ph0 - 0.006), ph0 + 0.006, 0.001):
            s = _grid_score(times, env, period, phase, duration)
            if s > best[0]:
                best = (s, period, phase)
    return best[1], best[2]


# ---------------------------------------------------------------------------
# ビート検出(メイン API)
# ---------------------------------------------------------------------------

def _validate_detect_beats_params(*, sr, hop, tightness, min_bpm, max_bpm):
    """detect_beats の数値パラメータをデコード前に検証する（日本語エラー）"""
    for name, v in (("sr", sr), ("hop", hop)):
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise ValueError(
                f"detect_beats: {name} は正の整数を指定してください: {v!r}")
    if (isinstance(tightness, bool) or not isinstance(tightness, (int, float))
            or not math.isfinite(float(tightness)) or tightness <= 0):
        raise ValueError(
            f"detect_beats: tightness は有限の正数を指定してください: "
            f"{tightness!r}")
    for name, v in (("min_bpm", min_bpm), ("max_bpm", max_bpm)):
        if (isinstance(v, bool) or not isinstance(v, (int, float))
                or not math.isfinite(float(v)) or v <= 0):
            raise ValueError(
                f"detect_beats: {name} は有限の正数を指定してください: {v!r}")
    if not min_bpm < max_bpm:
        raise ValueError(
            f"detect_beats: min_bpm({min_bpm!r}) は max_bpm({max_bpm!r}) より"
            f"小さい値を指定してください")


def detect_beats(audio_path, *, sr=_DEFAULT_SR, hop=512, tightness=12.0,
                 min_bpm=60, max_bpm=200):
    """音声/動画ファイルからビート(拍)時刻を検出する。

    Args:
        audio_path: 入力ファイル(音声・動画どちらでも可、ffmpeg でデコード)
        sr: 解析サンプリングレート
        hop: STFT ホップ長
        tightness: グリッドへの吸着度。大きいほど等間隔グリッドに忠実で、
                    小さいほど近傍 onset ピークへ大きくスナップする。
                    スナップ用ガウス幅 sigma = period / tightness
        min_bpm, max_bpm: テンポ探索範囲(倍/半テンポの曖昧性はこの範囲で解決)
    Returns:
        {"bpm": float, "beats": [秒, ...], "onsets": [秒, ...], "duration": float}
    """
    # 入口検証: 無効値はFFmpegデコードやFFT等の高コスト処理へ進む前に
    # 日本語で拒否する（tightness=0 のZeroDivisionError等の深部例外を防ぐ。
    # 監査 issue #15 P2）
    _validate_detect_beats_params(sr=sr, hop=hop, tightness=tightness,
                                  min_bpm=min_bpm, max_bpm=max_bpm)
    y = _load_mono(audio_path, sr=sr)
    duration = len(y) / sr
    times, env = onset_strength(y, sr, hop=hop)
    frame_dt = hop / sr

    # 1) テンポ(周期)推定: 自己相関で候補生成 → グリッド当てはめで選択
    candidates = _tempo_candidates(env, frame_dt, min_bpm, max_bpm)
    period0 = _select_period(times, env, candidates, duration)
    # 2) 周期・位相の微調整
    period, phase = _refine_grid(times, env, period0, duration)
    bpm = 60.0 / period

    # 3) 等間隔グリッド生成
    n_beats = int(np.floor((duration - phase) / period)) + 1
    grid = phase + period * np.arange(n_beats)

    # 4) 各拍を近傍 onset ピークへスナップ(ガウス重み付き argmax)
    sigma = period / float(tightness)
    half_win = 2.0 * sigma
    env_floor = 0.05  # これ未満しか無い区間はスナップせずグリッド位置を維持
    beats = []
    for tb in grid:
        t0 = max(0.0, tb - half_win)
        t1 = min(duration, tb + half_win)
        if t1 <= t0:
            beats.append(tb)
            continue
        tt = np.arange(t0, t1, 0.002)
        if len(tt) == 0:
            beats.append(tb)
            continue
        ee = np.interp(tt, times, env)
        w = ee * np.exp(-0.5 * ((tt - tb) / sigma) ** 2)
        if ee.max() < env_floor:
            beats.append(float(tb))
        else:
            beats.append(float(tt[int(np.argmax(w))]))

    # 5) onset ピーク時刻リスト(参考情報)
    min_dist = max(1, int(round(0.05 / frame_dt)))
    height = float(env.mean() + 0.5 * env.std())
    peak_idx, _ = find_peaks(env, height=height, distance=min_dist)
    onsets = (peak_idx * frame_dt).tolist()

    return {
        "bpm": round(float(bpm), 3),
        "beats": [round(t, 4) for t in beats],
        "onsets": [round(t, 4) for t in onsets],
        "duration": round(float(duration), 4),
    }


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def snap_times(times, beats):
    """任意の時刻リストを最近傍ビートへスナップする。

    Args:
        times: スナップしたい時刻のリスト(カット点など)
        beats: detect_beats() の "beats"
    Returns:
        list[float]: 各時刻に最も近いビート時刻
    """
    beats = np.asarray(beats, dtype=np.float64)
    if beats.size == 0:
        return [float(t) for t in times]
    order = np.argsort(beats)
    sorted_beats = beats[order]
    out = []
    for t in times:
        i = int(np.searchsorted(sorted_beats, t))
        # 前後の候補のうち近い方
        cands = []
        if i > 0:
            cands.append(sorted_beats[i - 1])
        if i < len(sorted_beats):
            cands.append(sorted_beats[i])
        out.append(float(min(cands, key=lambda b: abs(b - t))))
    return out


def beats_to_keyframes(beats, values, *, offset=0.0, decay=None, base=None,
                       t_start=None, t_end=None):
    """ビート時刻列を scriptvedit.keyframes に渡せるフラット列に整形する。

    scriptvedit を import せず、(t0, v0, t1, v1, ...) のタプルを返すだけの
    データ整形ヘルパー。

    **単位は変換しない**: beats・offset・decay・t_start/t_end がそのまま出力の時刻になる。
    keyframes の時刻は秒ではなく u（0..1。表示区間の進行度）なので、beat_sync の
    beats（秒）は表示尺で割ってから渡す（decay も同じ尺で割る）。

    Args:
        beats: ビート時刻のリスト
        values: 各ビートに割り当てる値。ビート数より短ければ循環使用
        offset: 全時刻に加えるオフセット秒(素材の開始位置合わせ用)
        decay: 指定すると各ビートで (t, value) → (t+decay, base) のパルス形になる
               (例: 拍ごとに scale が跳ねてすぐ戻る演出)
        base: decay 使用時の戻り値(省略時は values の最小値)
        t_start, t_end: この範囲 [t_start, t_end] のビートだけを使う
    Returns:
        tuple: (t0, v0, t1, v1, ...) — scriptvedit.keyframes(*result) にそのまま渡せる

    使用例（obj と音声がどちらもタイムライン 0 秒から始まる場合）:
        beats_u = [b / dur for b in res["beats"] if b < dur]   # 秒 → u
        kf = beats_to_keyframes(beats_u, [1.15], decay=0.12 / dur, base=1.0)
        obj.time(dur) <= scale(keyframes(*kf))
    """
    values = list(values)
    if len(values) == 0:
        raise ValueError("beats_to_keyframes: values が空です")
    sel = [float(b) for b in beats
           if (t_start is None or b >= t_start) and (t_end is None or b <= t_end)]
    sel.sort()
    if not sel:
        raise ValueError(
            f"beats_to_keyframes: 指定範囲 [t_start={t_start}, t_end={t_end}] "
            "にビートがありません")
    if base is None:
        base = min(values)
    flat = []
    for i, b in enumerate(sel):
        t = b + offset
        v = float(values[i % len(values)])
        flat.append(t)
        flat.append(v)
        if decay is not None:
            # 次のビートを跨がない範囲でパルスの戻り点を入れる
            t_back = t + float(decay)
            if i + 1 < len(sel):
                t_next = sel[i + 1] + offset
                t_back = min(t_back, t_next - 1e-4)
            if t_back > t:
                flat.append(t_back)
                flat.append(float(base))
    return tuple(flat)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="numpy/scipy によるビート検出 (librosa 非依存)")
    parser.add_argument("audio", help="入力音声/動画ファイル")
    parser.add_argument("--json", action="store_true",
                        help="全結果を JSON で出力する")
    parser.add_argument("--sr", type=int, default=_DEFAULT_SR,
                        help="解析サンプリングレート (default: 22050)")
    parser.add_argument("--hop", type=int, default=512,
                        help="STFT ホップ長 (default: 512)")
    parser.add_argument("--tightness", type=float, default=12.0,
                        help="グリッド吸着度。大=等間隔重視 (default: 12)")
    parser.add_argument("--min-bpm", type=float, default=60,
                        help="テンポ探索の下限 BPM (default: 60)")
    parser.add_argument("--max-bpm", type=float, default=200,
                        help="テンポ探索の上限 BPM (default: 200)")
    args = parser.parse_args(argv)

    try:
        result = detect_beats(
            args.audio, sr=args.sr, hop=args.hop, tightness=args.tightness,
            min_bpm=args.min_bpm, max_bpm=args.max_bpm)
    except RuntimeError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"BPM: {result['bpm']}")
        print(f"duration: {result['duration']} s")
        print(f"beats: {len(result['beats'])} 拍 / onsets: {len(result['onsets'])} 個")
        print("最初の20ビート時刻 [秒]:")
        for t in result["beats"][:20]:
            print(f"  {t:8.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
