# -*- coding: utf-8 -*-
"""
scriptvedit.morph_flight - 粒子の輸送モーフ（fly_to）のフレーム生成

絵 A の不透明な画素を粒にして、離れた所に置いた絵 B の画素まで飛ばす。
重なる形どうしの変形は sdf モーフ（morph_to）、離れた形どうしは fly_to、という分担
（sdf は離れた形だとクロスフェードにしかならない。morph.py の _SDF_OVERLAP_WARN）。

手順:
  1. 標本: A と B の不透明な画素（α>0.1）を α の重みで N 粒ずつ取る。
     N = min(max_pixels, max(A の画素数, B の画素数))。画素の多い側は重みつきで
     間引き（重複なし）、少ない側は全画素に重みつきの複製を足して N にそろえる
     （複製は ±0.35px 揺らし、同じ画素から出た粒は α を分け合う）。
  2. 対応: match="ot" はスライスした最適輸送。A の粒の雲を、64方向への射影を
     ソートして合わせる更新を 40 回くり返して B の雲へ流し（sliced Wasserstein flow）、
     流れ着いた位置と B の粒を Hilbert 曲線の順で1対1に組む。
     流れ着いた位置は B の粒のすぐ近くなので、組み方の誤差は粒の間隔程度に収まる。
     実測（1,000〜2,000 粒、離れていない形どうし）で移動の総量はハンガリアン法の
     1.006〜1.015 倍、時間は 1.2万粒で 0.9〜1.9 秒（ハンガリアン法は 2,000 粒で 3〜6 秒、
     コスト行列が N² なので 1.2万粒は現実的でない）。
  3. 道すじ: 2次ベジェ。制御点は中点から「距離×arc」だけ進む向きの右手側へずらす
     （全粒で同じ側なので、平行に飛ぶ粒どうしの道すじが交差しない）。swirl は道すじを
     中点のまわりに回す（道の半ばで最大、両端で 0）。
  4. 時間: 粒ごとに stagger ぶん出発をずらし（stagger_by の順。x / y は進む向きの
     先頭の粒から出るので、粒の列は伸びる一方で途中で詰まらない）、残りの時間で
     smoothstep で加減速して飛ぶ。
  5. 描画: 粒はサブピクセルの位置に置いた半径 particle_size の円（縁 1px の
     アンチエイリアス）。リニア光 × 事前乗算で足し合わせ、α が 1 を超えた所は
     割り戻す（重なった粒は色を平均する）。色は OKLab（または OKLCh）で A→B。
     粒の色はストレートのまま _splat に渡し、事前乗算（被覆率 × α）はそこ1か所だけ
     （A と B が同じ色なら、どのコマのどの画素もその色のまま。α だけが変わる）。
     止まっている粒（まだ出ていない・もう着いた）は元の絵の α で切り抜くので、
     止まっている間の粒の絵は元の絵と同じ太さになる（_particle_planes）。
  6. つなぎ: 最初の dissolve[0] の区間は A の絵から粒へ、最後の dissolve[1] の区間は
     粒から B の絵へクロスフェードする。絵の重みは画素ごとで、止まっている粒が
     覆っている所だけ早く切り替わる（粒が出て行った跡に A が残らず、粒が着く前に
     B が浮かばない。_image_weight）。最初のコマは A、最後のコマは offset の位置の
     B と画素一致する（どちらも元の PNG をそのまま貼る）。

キャンバス:
  A の箱・offset の位置に置いた B の箱・全粒子の道すじ（粒の半径込み）をすべて覆う
  最小の矩形を、A の中心に対して左右・上下それぞれ対称に広げたもの（余白は偶数）。
  対称なので overlay の中央配置で A の位置は変わらず、filters/video.py の
  _terminal_inner_dims（元の箱 = A）と move の anchor の補正がそのまま効く。
  4096px を超えたら ValueError。

重さ（実測。Windows 11・Python 3.10・numpy 2.0・20 スレッドの CPU）:
  1080p の ED の形（A = 5行の赤い字 1498x550 → B = 締めの一文 1470x170、粒 1.2万、
  キャンバス 1574x674）で、前処理（標本・対応）0.9〜1.9 秒、1コマ 35〜95 ms
  （粒の描画 約 20 ms・合成 約 7 ms。PNG の書き出しは別スレッドで重ねる。幅は CPU の
  空き具合で、ほかの重い処理と並ぶと倍になる）。1秒（30 コマ）ぶんで 2〜5 秒。
  3.4万粒では前処理 約 6 秒・1コマ 約 80 ms。2回目からはキャッシュ（生成しない）。
  粒の描画は「粒の数 × 半径²」に比例する（メモリは _SPLAT_CHUNK で頭打ち）: 1.2万粒で
  半径 2 なら 1コマ 約 50 ms、半径 32 なら約 1.5 秒（3 秒・30fps で約 2 分）。
  5万粒・半径 8 で約 0.65 秒。大きな粒は粒の数を減らして使う。
"""

import collections
import inspect
import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image
from tqdm import tqdm

from scriptvedit.morph import (FLY_PARAM_KEYS, linear_premultiply, linear_to_srgb,
                               mix_oklab, oklab_to_linear_rgb, oklch_polar,
                               srgb8_to_oklab)
from scriptvedit.state import _FLY_COLOR_PATHS, _FLY_MATCH_MODES, _FLY_STAGGER_BY


# ============================================================
# 定数
# ============================================================

# 粒にする画素の α の下限（これを超える画素だけを標本にする。縁のごく薄い
# アンチエイリアスや影を粒にすると、薄い粒が雲のように広がって汚く見える）
_OPAQUE_ALPHA = 0.1

# 少ない側の複製を散らす幅 [px]（同じ位置に重ねると1粒にしか見えない）
_DUP_JITTER = 0.35

# スライスした最適輸送: 1回の更新で使う方向の数と更新の回数
_OT_DIRECTIONS = 64
_OT_ITERATIONS = 40

# Hilbert 曲線の量子化のビット数（2^16 段。粒の雲の外接矩形を割る）
_HILBERT_BITS = 16

# キャンバスの上限 [px]（幅・高さそれぞれ）
_CANVAS_MAX = 4096

# max_pixels の上限（標本・対応・描画のメモリが粒の数に比例するため）
_MAX_PIXELS_LIMIT = 100000

# 粒の半径の範囲 [px]
_PARTICLE_SIZE_RANGE = (0.5, 32.0)

# リニア光（0〜1）→ sRGB 8bit の表の段数（中間のコマの書き出しを速くする。
# 暗部でも 1 段が sRGB の 0.2 階調未満なので、表を引いた誤差は丸めの範囲に収まる）
_LIN_LUT_STEPS = 16384
_LIN_TO_SRGB8 = np.clip(
    np.rint(linear_to_srgb(np.linspace(0.0, 1.0, _LIN_LUT_STEPS + 1,
                                       dtype=np.float32)) * 255.0),
    0, 255).astype(np.uint8)

# 事前乗算を解くときの下限アルファ（0除算の防止）
_UNPREMUL_EPS = 1e-6

# PNG の書き出し待ちの上限（枚）。計算と書き出しを重ねる間に溜めるコマの数
_WRITE_AHEAD = 4

# 出発の順番（stagger_by）に混ぜる揺らぎの割合（_stagger_lag）
_STAGGER_JITTER = 0.15

# 止まっている粒を絵の α で切り抜く層へ移す幅（粒ごとの進みに対する比。_particle_planes）
_SETTLE_WIDTH = 0.12

# _splat が1回に扱う要素数（粒の数 × K²）の上限。一時配列（被覆率・番地・重み）が
# 1回あたり約 30 MB に収まる。既定（粒 1.2万・半径 2 → K=6）は1回で済む。
# 実測（粒 1.2万・半径 32）: 分けないと 1コマの山が 1.28 GB、分けると 36 MB で、速さは同じ
_SPLAT_CHUNK = 1 << 20


# ============================================================
# 引数の検証
# ============================================================

def _pair(name, value):
    """(x, y) の有限の数2つを float の組にする"""
    try:
        a, b = value
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        raise ValueError(f"fly_to: {name} は数値2つの組 (x, y) で指定してください: "
                         f"{value!r}") from None
    if not (np.isfinite(a) and np.isfinite(b)):
        raise ValueError(f"fly_to: {name} に NaN / 無限大は使えません: {value!r}")
    return a, b


def _check_params(offset, max_pixels, match, arc, swirl, stagger, stagger_by,
                  particle_size, color_path, dissolve, seed):
    """generate_flight_frames の引数を検証して正規化した dict を返す。

    DSL 経由（effects/terminal.py の fly_to）では構築時に同じ検査が済んでいるが、
    このモジュールを単体で使う呼び出しのためにここでも確かめる。
    """
    def num(name, v, lo=None, hi=None):
        if isinstance(v, bool) or not isinstance(v, (int, float, np.floating, np.integer)):
            raise ValueError(f"fly_to: {name} は数値で指定してください: {v!r}")
        v = float(v)
        if not np.isfinite(v):
            raise ValueError(f"fly_to: {name} に NaN / 無限大は使えません: {v!r}")
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            raise ValueError(f"fly_to: {name} は {lo}〜{hi} の範囲で指定してください: {v}")
        return v

    if match not in _FLY_MATCH_MODES:
        raise ValueError(f"fly_to: match は {list(_FLY_MATCH_MODES)} のいずれか: {match!r}")
    if stagger_by not in _FLY_STAGGER_BY:
        raise ValueError(
            f"fly_to: stagger_by は {list(_FLY_STAGGER_BY)} のいずれか: {stagger_by!r}")
    if color_path not in _FLY_COLOR_PATHS:
        raise ValueError(
            f"fly_to: color_path は {list(_FLY_COLOR_PATHS)} のいずれか: {color_path!r}")
    if (isinstance(max_pixels, bool) or not isinstance(max_pixels, (int, np.integer))
            or not 1 <= int(max_pixels) <= _MAX_PIXELS_LIMIT):
        raise ValueError(
            f"fly_to: max_pixels は 1〜{_MAX_PIXELS_LIMIT} の整数で指定してください: "
            f"{max_pixels!r}")
    if isinstance(seed, bool) or not isinstance(seed, (int, np.integer)):
        raise ValueError(f"fly_to: seed は整数で指定してください: {seed!r}")
    d_in, d_out = _pair("dissolve", dissolve)
    if not (0.0 <= d_in <= 1.0 and 0.0 <= d_out <= 1.0 and d_in + d_out <= 1.0 + 1e-9):
        raise ValueError(
            f"fly_to: dissolve=(a, b) は 0 以上で a + b <= 1 にしてください: {dissolve!r}")
    return {
        "offset": _pair("offset", offset),
        "max_pixels": int(max_pixels),
        "match": match,
        "arc": num("arc", arc, -4.0, 4.0),
        "swirl": num("swirl", swirl, -50.0, 50.0),
        "stagger": num("stagger", stagger, 0.0, 0.95),
        "stagger_by": stagger_by,
        "particle_size": num("particle_size", particle_size, *_PARTICLE_SIZE_RANGE),
        "color_path": color_path,
        "dissolve": (d_in, d_out),
        "seed": int(seed),
    }


# ============================================================
# 標本（A・B の不透明な画素 → N 粒）
# ============================================================

def _load_rgba(path):
    """画像を RGBA の uint8 配列として読む"""
    with Image.open(path) as im:
        return np.array(im.convert("RGBA"))


def _opaque_pixels(arr, label, path):
    """不透明な画素（α>_OPAQUE_ALPHA）の位置 (x, y)・色・α を返す。無ければ ValueError"""
    a = arr[:, :, 3]
    ys, xs = np.nonzero(a > _OPAQUE_ALPHA * 255.0)
    if len(xs) == 0:
        raise ValueError(
            f"fly_to: {label} に不透明な画素（α>{_OPAQUE_ALPHA}）がありません: {path}"
            f"（全面が透明な画像は粒にできません）")
    pos = np.column_stack([xs, ys]).astype(np.float64)
    return pos, arr[ys, xs, :3], a[ys, xs].astype(np.float64) / 255.0


def _sample(pos, rgb, alpha, n, rng):
    """α の重みで n 粒を取る。返値: (位置, 色 uint8, 粒の α)

    画素が n 以上: 重みつきで重複なしに間引く（Efraimidis–Spirakis。鍵 log(u)/w の
    大きい順に n 個）。粒の α は画素の α のまま。
    画素が n 未満: 全画素に、重みつきで選んだ複製を足して n にする。複製は
    ±_DUP_JITTER px 揺らし、同じ画素から出た粒は α を等分する（重なった所が
    元の絵より濃くならない）。
    """
    m = len(pos)
    if m >= n:
        if m == n:
            idx = np.arange(m)
        else:
            keys = np.log(np.maximum(rng.random(m), 1e-300)) / alpha
            idx = np.sort(np.argpartition(-keys, n - 1)[:n])
        return pos[idx], rgb[idx], alpha[idx]
    extra = rng.choice(m, size=n - m, replace=True, p=alpha / alpha.sum())
    idx = np.concatenate([np.arange(m), extra])
    share = np.bincount(idx, minlength=m)[idx]
    out = pos[idx].copy()
    out[m:] += rng.uniform(-_DUP_JITTER, _DUP_JITTER, size=(n - m, 2))
    return out, rgb[idx], alpha[idx] / share


# ============================================================
# 対応（どの粒がどこへ行くか）
# ============================================================

def _sliced_ot_flow(src, dst, rng, iterations=_OT_ITERATIONS,
                    directions=_OT_DIRECTIONS):
    """src の粒の雲を dst の雲へ流す（スライスした最適輸送の勾配流）。

    各回、方向を少しずつ回した directions 本の向きに両方の雲を射影してソートし、
    同じ順位どうしの差（1次元の最適輸送）を全方向で平均した分だけ src を動かす
    （半円に一様な方向の平均 Σθθᵀ/K = I/2 なので 2 倍して1回分の歩幅にする）。
    最適輸送は平行移動で対応が変わらないので、両方の重心を原点に寄せてから流す。
    返値: 流れ着いた位置（dst の座標系）
    """
    c_dst = dst.mean(axis=0)
    # (2, N) の float32 で持つ（射影 (K, N) が行優先で並び、行ごとのソートが速い）
    z = np.ascontiguousarray((src - src.mean(axis=0)).T, dtype=np.float32)
    y = np.ascontiguousarray((dst - c_dst).T, dtype=np.float32)
    base = np.arange(directions) * (np.pi / directions)
    step = np.float32(2.0 / directions)
    for _ in range(iterations):
        ang = base + rng.uniform(0.0, np.pi / directions)
        dirs = np.stack([np.cos(ang), np.sin(ang)], axis=1).astype(np.float32)  # (K, 2)
        proj = dirs @ z                                         # (K, N)
        target = np.sort(dirs @ y, axis=1)
        # 射影が同値になるのは同じ位置の粒どうしだけ（どちらへ組んでも同じ）なので、
        # 安定ソートでなくてよい（安定ソートは 3 倍遅い）
        order = np.argsort(proj, axis=1)
        disp = np.empty_like(proj)
        np.put_along_axis(disp, order,
                          target - np.take_along_axis(proj, order, axis=1), axis=1)
        z += step * (dirs.T @ disp)
    return z.T.astype(np.float64) + c_dst


def _hilbert_index(pts, lo, span, bits=_HILBERT_BITS):
    """点 (N, 2) の Hilbert 曲線上の番号（lo / span で量子化する）"""
    side = (1 << bits) - 1
    q = np.clip(np.rint((pts - lo) / span * side), 0, side).astype(np.int64)
    x, y = q[:, 0], q[:, 1]
    d = np.zeros(len(pts), dtype=np.int64)
    s = 1 << (bits - 1)
    while s > 0:
        rx = (x & s) > 0
        ry = (y & s) > 0
        d += s * s * ((3 * rx.astype(np.int64)) ^ ry.astype(np.int64))
        # 象限に合わせて回す（標準の xy2d）
        flip = (~ry) & rx
        x = np.where(flip, s - 1 - x, x)
        y = np.where(flip, s - 1 - y, y)
        x, y = np.where(ry, x, y), np.where(ry, y, x)
        s >>= 1
    return d


def _hilbert_pairing(z, dst):
    """流れ着いた位置 z と dst の粒を Hilbert 曲線の順で1対1に組む（σ: z_i → dst_σ(i)）"""
    both = np.vstack([z, dst])
    lo = both.min(axis=0)
    span = max(float((both.max(axis=0) - lo).max()), 1e-9)
    order_z = np.argsort(_hilbert_index(z, lo, span), kind="stable")
    order_d = np.argsort(_hilbert_index(dst, lo, span), kind="stable")
    sigma = np.empty(len(z), dtype=np.int64)
    sigma[order_z] = order_d
    return sigma


def _angle_pairing(src, dst):
    """各雲の重心まわりの角度の順で組む"""
    def key(p):
        c = p - p.mean(axis=0)
        return np.arctan2(c[:, 1], c[:, 0])
    sigma = np.empty(len(src), dtype=np.int64)
    sigma[np.argsort(key(src), kind="stable")] = np.argsort(key(dst), kind="stable")
    return sigma


def _match(src, dst, mode, rng):
    """粒の対応 σ（src_i は dst_σ(i) へ行く）"""
    if mode == "random":
        return rng.permutation(len(src))
    if mode == "angle":
        return _angle_pairing(src, dst)
    return _hilbert_pairing(_sliced_ot_flow(src, dst, rng), dst)


# ============================================================
# 道すじと時間
# ============================================================

def _stagger_lag(key, stagger, rng):
    """出発の遅れ（0〜stagger）。key を 0〜1 へ正規化して掛ける。

    順番に _STAGGER_JITTER の揺らぎを足す。key だけで決めると、出発の境目が
    一直線（stagger_by="x" なら縦の線）になって絵が刃物で切ったように動き出す。
    少し揺らすと境目が砂のようにほぐれる（揺らぎの後も 0〜stagger に収める）。
    """
    n = len(key)
    if stagger <= 0.0:
        return np.zeros(n)
    jitter = rng.random(n)
    lo, hi = float(key.min()), float(key.max())
    norm = np.zeros(n) if hi - lo <= 1e-12 else (key - lo) / (hi - lo)
    return ((1.0 - _STAGGER_JITTER) * norm + _STAGGER_JITTER * jitter) * stagger


def _local_progress(ctx, p):
    """全体の進行度 p での粒ごとの進み（0〜1。smoothstep で加減速）"""
    s = ctx["stagger"]
    loc = np.clip((p - ctx["lag"]) / (1.0 - s), 0.0, 1.0)
    return loc * loc * (3.0 - 2.0 * loc)


def _positions(ctx, e):
    """粒ごとの進み e (N,) での位置 (N, 2)（キャンバスの画素座標。画素の中心が整数）"""
    e1 = e[:, None]
    pos = ((1.0 - e1) ** 2 * ctx["S"] + 2.0 * (1.0 - e1) * e1 * ctx["C"]
           + e1 ** 2 * ctx["E"])
    if ctx["swirl"] != 0.0:
        ang = ctx["swirl"] * np.sin(np.pi * e)
        c, s = np.cos(ang), np.sin(ang)
        rel = pos - ctx["M"]
        pos = ctx["M"] + np.column_stack([rel[:, 0] * c - rel[:, 1] * s,
                                          rel[:, 0] * s + rel[:, 1] * c])
    return pos


# ============================================================
# 描画
# ============================================================

def _splat_box(pos, radius):
    """粒の円が触れる画素の外接矩形 (x0, y0, x1, y1)（x1, y1 は端の外）"""
    reach = radius + 1.0
    return (int(np.floor(pos[:, 0].min() - reach)), int(np.floor(pos[:, 1].min() - reach)),
            int(np.ceil(pos[:, 0].max() + reach)) + 1,
            int(np.ceil(pos[:, 1].max() + reach)) + 1)


def _splat(pos, color, alpha, radius):
    """粒（サブピクセルの位置の円）をリニア光 × 事前乗算で足し合わせる。

    color はストレート（α を掛けていない）のリニア光の色 (N, 3)、alpha は粒の α (N,)。
    事前乗算は被覆率 × α を色に掛けるここ1か所だけで行う（呼び出し側で色に α を
    掛けると α が2回掛かり、α<1 の粒が暗くなる。複製で α を分け合った粒・縁の
    アンチエイリアス・半透明の素材がすべて沈む）。

    返値: (box, planes)。box は粒が触れる画素の外接矩形（_splat_box）、planes は
    その範囲の (4, h, w) float32（R, G, B, α。チャンネルごとに連続した面）。
    円の被覆率は「中心からの距離が 半径+0.5 から 半径-0.5 へ 1px で立ち上がる」
    ランプ（縁 1px のアンチエイリアス。面積は πr² に一致する）。位置を整数へ
    丸めないので、ゆっくり動く粒も 1px 単位で跳ねない。α が 1 を超えた画素は
    色ごと割り戻す（重なった粒の色の平均になる）。

    一時配列は「粒の数 × K²」（K = 粒の円を覆う一辺の画素数）に比例するので、
    _SPLAT_CHUNK 要素ずつに分けて足し込む（大きな粒・多い粒でもメモリが頭打ちになる。
    時間は粒の数 × 半径² に比例するまま）。
    """
    box = _splat_box(pos, radius)
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    lo = -int(np.floor(radius + 0.5))
    offs = np.arange(lo, int(np.ceil(radius + 0.5)) + 1, dtype=np.float32)
    rr = np.float32(radius + 0.5)
    planes = np.zeros((4, h * w), dtype=np.float32)
    step = max(1, _SPLAT_CHUNK // (len(offs) * len(offs)))
    for s in range(0, len(pos), step):
        px = pos[s:s + step, 0].astype(np.float32)
        py = pos[s:s + step, 1].astype(np.float32)
        gx = np.floor(px)[:, None] + offs[None, :]             # (n, K)
        gy = np.floor(py)[:, None] + offs[None, :]
        dx2 = (gx - px[:, None]) ** 2
        dy2 = (gy - py[:, None]) ** 2
        cov = rr - np.sqrt(dy2[:, :, None] + dx2[:, None, :])
        np.clip(cov, 0.0, 1.0, out=cov)                         # (n, K, K)
        idx = ((gy.astype(np.int64) - y0)[:, :, None] * w
               + (gx.astype(np.int64) - x0)[:, None, :]).ravel()
        cov *= alpha[s:s + step].astype(np.float32)[:, None, None]
        planes[3] += np.bincount(idx, weights=cov.ravel(), minlength=h * w)
        col = color[s:s + step].astype(np.float32)
        for c in range(3):
            planes[c] += np.bincount(
                idx, weights=(cov * col[:, c][:, None, None]).ravel(), minlength=h * w)
    over = planes[3] > 1.0
    if over.any():
        planes[:, over] /= planes[3, over]
    return box, planes.reshape(4, h, w)


def _pm_to_rgba8(planes):
    """リニア光 × 事前乗算の (4, h, w) → ストレートアルファの sRGB uint8 RGBA (h, w, 4)

    色は α そのもので割り戻す（α が丸め誤差で 1 をわずかに超えても色が明るくならない）。
    """
    h, w = planes.shape[1:]
    out = np.zeros((h, w, 4), dtype=np.uint8)
    a = planes[3]
    nz = a > (0.5 / 255.0)
    if not nz.any():
        return out
    av = np.minimum(a[nz], 1.0)
    col = planes[:3, nz] / np.maximum(a[nz], _UNPREMUL_EPS)
    np.clip(col, 0.0, 1.0, out=col)
    out[nz, :3] = _LIN_TO_SRGB8[np.rint(col * _LIN_LUT_STEPS).astype(np.int32)].T
    out[nz, 3] = np.rint(av * 255.0).astype(np.uint8)
    return out


# ============================================================
# 計画（標本・対応・道すじ・キャンバス）
# ============================================================

def _b_offset(size_a, size_b, d):
    """A の左上から B の左上までのずれ（1軸、整数 px）: ⌈Wa/2⌉ + ⌊d − Wb/2⌋。

    A の中心を整数の画素に置いたとき、中心を A の中心 + d に置いた静止画の B と
    同じ丸め（move の anchor="center" は左上を trunc(中心 − 幅/2) にする）。
    """
    return (int(size_a) + 1) // 2 + int(np.floor(float(d) - size_b / 2.0))


def _plan_flight(path_a, path_b, progress, *, offset, max_pixels, match, arc, swirl,
                 stagger, stagger_by, particle_size, color_path, dissolve, seed):
    """フレームを描く前の全部（返値 ctx を _render_frame に渡す）。

    progress は各コマの全体の進行度（キャンバスを道すじから決めるのに使う）。
    """
    arr_a = _load_rgba(path_a)
    arr_b = _load_rgba(path_b)
    ha, wa = arr_a.shape[:2]
    hb, wb = arr_b.shape[:2]
    pos_a, rgb_a, al_a = _opaque_pixels(arr_a, "A（fly_to を掛けた絵）", path_a)
    pos_b, rgb_b, al_b = _opaque_pixels(arr_b, "target", path_b)
    n = min(int(max_pixels), max(len(pos_a), len(pos_b)))
    rng = np.random.default_rng(seed)
    xa, ca, aa = _sample(pos_a, rgb_a, al_a, n, rng)
    xb, cb, ab = _sample(pos_b, rgb_b, al_b, n, rng)

    # B の左上（A の左上からのずれ）。静止画の配置と同じ丸めにする: move(anchor="center")
    # は左上を trunc(中心 − 幅/2) に置くので、A の中心を整数の画素 X に置くと A の左上は
    # X − ⌈Wa/2⌉、中心を X + dx に置いた静止画の B の左上は X + ⌊dx − Wb/2⌋。その差を使えば、
    # 最後のコマの B は「中心を A の中心 + offset に置いた静止画の B」と同じ画素に来る
    # （四捨五入 ⌊(Wa−Wb)/2 + dx + 0.5⌋ だと、Wa が偶数・Wb が奇数のとき 1px 右・下に来た）
    bx = _b_offset(wa, wb, offset[0])
    by = _b_offset(ha, hb, offset[1])
    xb = xb + np.array([bx, by], dtype=np.float64)

    t0 = time.perf_counter()
    sigma = _match(xa, xb, match, rng)
    t_match = time.perf_counter() - t0
    xb, cb, ab = xb[sigma], cb[sigma], ab[sigma]

    # 道すじ（A の左上が原点の座標。キャンバスが決まってから余白ぶんずらす）
    d = xb - xa
    dist = np.hypot(d[:, 0], d[:, 1])
    unit = np.where(dist[:, None] > 1e-9, d / np.maximum(dist, 1e-9)[:, None], 0.0)
    normal = np.column_stack([-unit[:, 1], unit[:, 0]])   # 進む向きの右手側（画面で）
    mid = (xa + xb) / 2.0
    ctrl = mid + normal * (dist * arc)[:, None]

    # x / y は「進む向きの先頭の粒から先に出る」順（右へ飛ぶなら右端から、
    # 左へ飛ぶなら左端から。横へ動かないなら左・上から）。後ろの粒から出すと、
    # 先に出た粒が前の粒に追いついて道の途中で1本の柱に詰まる（実測: 右へ 120px の
    # 輪 → 棒で、進行度 0.5 の粒が幅 10px の縦の帯に重なった）。先頭から出せば
    # 粒の列は伸びる一方で、流れになる
    if stagger_by in ("x", "y"):
        axis = 0 if stagger_by == "x" else 1
        ahead = float(np.mean(d[:, axis]))
        key = -xa[:, axis] if ahead > 1e-9 else xa[:, axis]
    elif stagger_by == "distance":
        key = -dist                     # 遠くへ行く粒から先に出る
    else:
        key = rng.random(n)
    ctx = {
        "S": xa, "E": xb, "C": ctrl, "M": mid, "swirl": float(swirl),
        "stagger": float(stagger), "lag": _stagger_lag(key, float(stagger), rng),
    }

    # キャンバス: A の箱・B の箱・全コマの粒（半径 + 縁 1px）を覆い、A の中心に対称
    r = float(particle_size)
    reach = r + 1.0
    lo = np.array([min(0.0, bx), min(0.0, by)])
    hi = np.array([max(wa, bx + wb), max(ha, by + hb)], dtype=np.float64)
    for p in sorted(set(float(v) for v in progress)):
        if p <= 0.0 or p >= 1.0:
            continue
        cur = _positions(ctx, _local_progress(ctx, p))
        lo = np.minimum(lo, cur.min(axis=0) - reach)
        hi = np.maximum(hi, cur.max(axis=0) + 1.0 + reach)
    margins = []
    for over_lo, over_hi, size in ((lo[0], hi[0], wa), (lo[1], hi[1], ha)):
        m = int(np.ceil(max(-over_lo, over_hi - size, 0.0)))
        margins.append(m + (m & 1))   # 偶数（奇数だと 4:2:0 出力で A が半画素ずれる）
    mx, my = margins
    cw, ch = wa + 2 * mx, ha + 2 * my
    if cw > _CANVAS_MAX or ch > _CANVAS_MAX:
        raise ValueError(
            f"fly_to: キャンバスが {cw}x{ch}px になり、上限 {_CANVAS_MAX}px を超えます"
            f"（A の中心に対して対称に、B の位置 offset={tuple(offset)} と粒の道すじを"
            f"覆う大きさ）。offset を小さくする（A を B の近くに置き、move で動かす）か、"
            f"arc / swirl を小さくしてください")
    shift = np.array([mx, my], dtype=np.float64)
    for k in ("S", "E", "C", "M"):
        ctx[k] = ctx[k] + shift

    # 両端の絵（元の PNG をそのまま貼る＝最初と最後のコマは画素一致）
    canvas_a = np.zeros((ch, cw, 4), dtype=np.uint8)
    canvas_a[my:my + ha, mx:mx + wa] = arr_a
    canvas_b = np.zeros((ch, cw, 4), dtype=np.uint8)
    bx0, by0 = mx + bx, my + by
    canvas_b[by0:by0 + hb, bx0:bx0 + wb] = arr_b

    lab_a = srgb8_to_oklab(ca)
    lab_b = srgb8_to_oklab(cb)
    ctx.update({
        "n": n, "canvas": (cw, ch), "margin": (mx, my), "b_pos": (bx0, by0),
        "a_box": (mx, my, mx + wa, my + ha), "b_box": (bx0, by0, bx0 + wb, by0 + hb),
        "canvas_a": canvas_a, "canvas_b": canvas_b,
        # 両端の絵のリニア光 × 事前乗算（チャンネルごとの面 (4, h, w)。dissolve で混ぜる）
        "pm_a": np.ascontiguousarray(np.moveaxis(linear_premultiply(arr_a), 2, 0)),
        "pm_b": np.ascontiguousarray(np.moveaxis(linear_premultiply(arr_b), 2, 0)),
        # 止まっている粒を切り抜く α（_particle_planes）
        "mask_a": arr_a[:, :, 3].astype(np.float32) / 255.0,
        "mask_b": arr_b[:, :, 3].astype(np.float32) / 255.0,
        "lab_a": lab_a, "lab_b": lab_b,
        "polar": oklch_polar(lab_a, lab_b) if color_path == "oklch" else None,
        "color_path": color_path,
        "alpha_a": aa, "alpha_b": ab, "radius": r,
        "dissolve": (float(dissolve[0]), float(dissolve[1])),
        "t_match": t_match,
    })
    return ctx


def _add_planes(dst, region, src, box, coef):
    """region の (4, h, w) へ、box に置いた src（4, bh, bw）を coef 倍で足す。

    coef は数か、box と同じ寸法 (bh, bw) の画素ごとの重み。

    box がキャンバスの外へはみ出す分（粒の外接矩形は余白の端で切ることがある）は捨てる。
    """
    x0, y0 = max(box[0], region[0]), max(box[1], region[1])
    x1, y1 = min(box[2], region[2]), min(box[3], region[3])
    if x1 <= x0 or y1 <= y0:
        return
    part = src[:, y0 - box[1]:y1 - box[1], x0 - box[0]:x1 - box[0]]
    view = dst[:, y0 - region[1]:y1 - region[1], x0 - region[0]:x1 - region[0]]
    if isinstance(coef, np.ndarray) and coef.ndim == 2:
        # 画素ごとの重み（box と同じ寸法）
        view += coef[y0 - box[1]:y1 - box[1], x0 - box[0]:x1 - box[0]] * part
    elif coef == 1.0:
        view += part
    else:
        view += np.float32(coef) * part


def _settle_weight(x):
    """止まっている度合い（0〜1）。x は「端からの近さ」= 1 - 端までの進み。

    進みが端から _SETTLE_WIDTH 以内で 0→1 に smoothstep で上がる（端で 1）。
    """
    w = np.clip((x - (1.0 - _SETTLE_WIDTH)) / _SETTLE_WIDTH, 0.0, 1.0)
    return w * w * (3.0 - 2.0 * w)


def _particle_planes(ctx, pos, color, alpha, e):
    """粒の層（リニア光 × 事前乗算の (4, h, w)）とその外接矩形を返す。

    止まっている粒（まだ出ていない粒・着いた粒）は、その場の絵（A / B）の α で
    切り抜く。半径 particle_size の円は絵の輪郭から 半径+0.5 px はみ出すので、
    切り抜かないと止まっている間の粒の絵は元の絵より一回り太く、dissolve で元の絵へ
    移るときに字が痩せて見える（細い書体ほど目立つ）。切り抜いた粒の層は元の絵と
    ほぼ同じ形・同じ濃さになり、粒 ⇄ 絵の切り替わりが見えない。
    動き出し・着く直前は _SETTLE_WIDTH の幅で、切り抜く層と切り抜かない層を
    なめらかに入れ替える（着いた瞬間に縁が欠けて跳ねない）。

    返値: (box, planes, settled)。settled は {"a": 被覆, "b": 被覆}（A / B の箱の
    (h, w)。止まっている粒がその画素をどれだけ覆っているか 0〜1。止まっている粒が
    無ければ None）。dissolve で絵を出す重みに使う（_render_frame）。
    """
    m_a = _settle_weight(1.0 - e)            # A の位置で止まっている度合い
    m_b = _settle_weight(e)                  # B の位置で止まっている度合い
    free = 1.0 - m_a - m_b
    layers = []
    settled = {"a": None, "b": None}
    # 2つの層への振り分けは α だけに掛ける（色はストレートのまま _splat へ渡す。
    # 色にも掛けると、入れ替わりの途中で色が f² + m² 倍に沈む）
    sel = free > 1e-6
    if sel.any():
        layers.append(_splat(pos[sel], color[sel], alpha[sel] * free[sel],
                             ctx["radius"]))
    for key, m, box, mask in (("a", m_a, ctx["a_box"], ctx["mask_a"]),
                              ("b", m_b, ctx["b_box"], ctx["mask_b"])):
        sel = m > 1e-6
        if not sel.any():
            continue
        sbox, planes = _splat(pos[sel], color[sel], alpha[sel] * m[sel], ctx["radius"])
        clipped = np.zeros((4, box[3] - box[1], box[2] - box[0]), dtype=np.float32)
        _add_planes(clipped, box, planes, sbox, 1.0)
        settled[key] = clipped[3].copy()     # 切り抜く前の α ＝ 止まっている粒の被覆
        clipped *= mask
        layers.append((box, clipped))
    if not layers:
        return (0, 0, 1, 1), np.zeros((4, 1, 1), dtype=np.float32), settled
    if len(layers) == 1:
        return layers[0][0], layers[0][1], settled
    box = (min(b[0] for b, _ in layers), min(b[1] for b, _ in layers),
           max(b[2] for b, _ in layers), max(b[3] for b, _ in layers))
    planes = np.zeros((4, box[3] - box[1], box[2] - box[0]), dtype=np.float32)
    for lbox, lplanes in layers:
        _add_planes(planes, box, lplanes, lbox, 1.0)
    over = planes[3] > 1.0
    if over.any():
        planes[:, over] /= planes[3, over]
    return box, planes, settled


def _image_weight(coef, settled):
    """dissolve で A / B の絵を出す重み（画素ごと）。

    止まっている粒が覆っている画素（settled≈1）は全体の重み coef、粒がまだ
    着いていない・もう出て行った画素（settled≈0）は coef³。全体で同じ重みにすると、
    遅れて着く粒の下に B の字が先に薄く浮かび、出て行った粒の跡に A の字が残って
    濁って見える（実測: 右端の「いない。」が粒の到着前に暗い赤で出ていた）。
    coef が 1（進行度の端）では画素によらず 1 なので、両端のコマは元の絵のまま。
    """
    if settled is None:
        return np.float32(coef ** 3)
    cov = np.clip(settled, 0.0, 1.0)
    return np.power(np.float32(coef), 3.0 - 2.0 * cov).astype(np.float32)


def _render_frame(ctx, p):
    """全体の進行度 p（0〜1）の1コマ（キャンバス全体の uint8 RGBA）"""
    if p <= 0.0:
        return ctx["canvas_a"]
    if p >= 1.0:
        return ctx["canvas_b"]
    e = _local_progress(ctx, p)
    pos = _positions(ctx, e)
    alpha = (1.0 - e) * ctx["alpha_a"] + e * ctx["alpha_b"]
    lab = mix_oklab(ctx["lab_a"], ctx["lab_b"], e.astype(np.float32),
                    ctx["color_path"], polar=ctx["polar"])
    # ストレートの色（α は _splat が被覆率と一緒に掛ける）
    color = np.clip(oklab_to_linear_rgb(lab), 0.0, 1.0)

    # dissolve: 最初の区間は A の絵 → 粒、最後の区間は粒 → B の絵（リニア光で混ぜる）
    d_in, d_out = ctx["dissolve"]
    r_in = min(p / d_in, 1.0) if d_in > 0.0 else 1.0
    r_out = max((p - (1.0 - d_out)) / d_out, 0.0) if d_out > 0.0 else 0.0
    coef_a = (1.0 - r_out) * (1.0 - r_in)
    coef_p = (1.0 - r_out) * r_in
    coef_b = r_out

    sbox, splat, settled = _particle_planes(ctx, pos, color, alpha, e)
    boxes = [sbox]
    if coef_a > 0.0:
        boxes.append(ctx["a_box"])
    if coef_b > 0.0:
        boxes.append(ctx["b_box"])
    cw, ch = ctx["canvas"]
    region = (max(0, min(b[0] for b in boxes)), max(0, min(b[1] for b in boxes)),
              min(cw, max(b[2] for b in boxes)), min(ch, max(b[3] for b in boxes)))
    planes = np.zeros((4, region[3] - region[1], region[2] - region[0]), dtype=np.float32)
    if coef_p > 0.0:
        _add_planes(planes, region, splat, sbox, coef_p)
    if coef_a > 0.0:
        _add_planes(planes, region, ctx["pm_a"], ctx["a_box"],
                    _image_weight(coef_a, settled["a"]))
    if coef_b > 0.0:
        _add_planes(planes, region, ctx["pm_b"], ctx["b_box"],
                    _image_weight(coef_b, settled["b"]))
    frame = np.zeros((ch, cw, 4), dtype=np.uint8)
    frame[region[1]:region[3], region[0]:region[2]] = _pm_to_rgba8(planes)
    return frame


# ============================================================
# 公開の入口
# ============================================================

def generate_flight_frames(path_a, path_b, out_dir, n_frames, blend_fn=None, *,
                           offset=(0.0, 0.0), max_pixels=12000, match="ot", arc=0.25,
                           swirl=0.0, stagger=0.3, stagger_by="x", particle_size=2,
                           color_path="oklab", dissolve=(0.15, 0.15), seed=0):
    """絵 A の粒が飛んで、offset の位置の絵 B になる RGBA PNG 連番を生成する

    out_dir に frame_00000.png 〜 を書く。最初のコマは A、最後のコマは B と画素一致する
    （キャンバスは A の中心に対して対称に広がる。モジュールの docstring 参照）。

    path_a / path_b: A（粒になる絵）/ B（粒が集まってできる絵）の画像
    n_frames: コマ数
    blend_fn: 全体の進行カーブ t→p（None で直線）。粒ごとの動きは smoothstep で
        加減速する（stagger の遅れを引いた残りの時間で）
    offset: A の中心から B の中心までのずれ (dx, dy) px（右と下が正）。B の左上は
        A の左上から (⌈Wa/2⌉ + ⌊dx − Wb/2⌋, ⌈Ha/2⌉ + ⌊dy − Hb/2⌋)（_b_offset。A の中心を
        整数の画素に置いたとき、中心を A の中心 + offset に置いた静止画の B と同じ丸め）
    max_pixels: 粒の数の上限。粒の数 N = min(max_pixels, max(A の画素数, B の画素数))
    match: 粒の対応。"ot"（スライスした最適輸送）/ "angle" / "random"
    arc: 道すじのふくらみ（距離に対する比。正で進む向きの右手側）
    swirl: 道すじを中点のまわりに回す角度 rad（道の半ばで最大。正で時計回り）
    stagger: 出発の遅れの幅（全体の進行度に対する比。0〜0.95）
    stagger_by: 出発の順番。"x" / "y"（横 / 縦の並びで、進む向きの先頭の粒から）/
        "distance"（遠くへ行く粒から）/ "random"
    particle_size: 粒（円）の半径 px
    color_path: 粒の色の通り道。"oklab"（直線）/ "oklch"（色相を回す）
    dissolve: (a, b)。最初の a の区間で A の絵から粒へ、最後の b の区間で粒から B の絵へ
    seed: 乱数の種（同じ値なら同じ絵）

    返値: dict（canvas=(幅, 高さ)、margin=(左右, 上下) の余白、n=粒の数、
          b_pos=キャンバスでの B の左上）
    """
    cfg = _check_params(offset, max_pixels, match, arc, swirl, stagger, stagger_by,
                        particle_size, color_path, dissolve, seed)
    if blend_fn is None:
        blend_fn = (lambda t: t)
    n_frames = max(int(n_frames), 1)
    last = n_frames - 1
    progress = [min(max(float(blend_fn(i / max(last, 1))), 0.0), 1.0)
                for i in range(n_frames)]
    t0 = time.perf_counter()
    ctx = _plan_flight(path_a, path_b, progress, **cfg)
    cw, ch = ctx["canvas"]
    print(f"[fly_to] 粒 {ctx['n']:,}（match={cfg['match']} {ctx['t_match']:.2f}s）"
          f" キャンバス {cw}x{ch}（余白 左右 {ctx['margin'][0]}px / 上下 "
          f"{ctx['margin'][1]}px） 前処理 {time.perf_counter() - t0:.2f}s")

    os.makedirs(out_dir, exist_ok=True)

    def save(rgba, path):
        # compress_level=1: 連番は直後に FFV1 へ入れて捨てる中間物なので速さを取る
        Image.fromarray(rgba, "RGBA").save(path, compress_level=1)

    # PNG の書き出し（zlib は GIL を離す）を次のコマの計算と重ねる。
    # 書き出し待ちは _WRITE_AHEAD 枚までに抑える（メモリを食いつぶさない）
    pending = collections.deque()
    with ThreadPoolExecutor(max_workers=2) as pool:
        for i in tqdm(range(n_frames), desc="fly_to フレーム生成"):
            p = progress[i]
            # 両端のコマは進行度に関係なく元の絵（前後のカット・保持と画素で繋がる）
            if i == 0 and p <= 0.0:
                rgba = ctx["canvas_a"]
            elif i == last and p >= 1.0:
                rgba = ctx["canvas_b"]
            else:
                rgba = _render_frame(ctx, p)
            pending.append(pool.submit(
                save, rgba, os.path.join(out_dir, f"frame_{i:05d}.png")))
            while len(pending) > _WRITE_AHEAD:
                pending.popleft().result()
        while pending:
            pending.popleft().result()
    print(f"完了: {out_dir} ({n_frames}フレーム)")
    return {"canvas": ctx["canvas"], "margin": ctx["margin"], "n": ctx["n"],
            "b_pos": ctx["b_pos"]}


# 既定値（describe の表・テストが effects/terminal.py の fly_to と突き合わせる）
FLY_PARAM_DEFAULTS = {
    k: v.default
    for k, v in inspect.signature(generate_flight_frames).parameters.items()
    if v.kind is inspect.Parameter.KEYWORD_ONLY}

# morph.FLY_PARAM_KEYS（DSL 側のタイポ検出に使う集合）とシグネチャのずれを止める
assert set(FLY_PARAM_DEFAULTS) == set(FLY_PARAM_KEYS), (
    "morph.FLY_PARAM_KEYS と generate_flight_frames のキーワード引数がずれています: "
    f"{sorted(set(FLY_PARAM_DEFAULTS) ^ set(FLY_PARAM_KEYS))}")
