"""
scriptvedit.morph - モーフィング動画生成（2方式）

method="sdf"（既定）: 形状ベース（符号付き距離場）
  1. 両画像のアルファから符号付き距離場（SDF）を作る
  2. SDF を線形補間 → その等高線 0 を中間形状のアルファとする
  3. 色は各画像の色をボロノイ分割で全画面へ拡張し、OKLCh（知覚均等・極座標）
     で補間 → 中間形状のアルファでマスクする
  透明度の平均化もワープの折り返しも起きないため、
  「中間フレームが濁る」「形の内側に暗いノイズ塊が出る」が原理的に発生しない。
  中間形状も常に滑らかな1つのシルエットになる（transport はここが破綻しやすい）。
  弱点: 形の「内部パーツ」は移動せずその場でクロスフェードする。

method="transport"（従来方式）: 最適輸送 + ワープ場
  1. 両画像の不透明ピクセルをサブサンプリング
  2. ハンガリアン法で最適輸送（ピクセルの対応関係）を計算
     （色距離は OKLab による知覚的距離。色の近い画素同士が対応しやすい）
  3. 対応関係からRBF（薄板スプライン）補間で滑らかなワープ場（変位場）を構成
  4. ワープ場で両画像を変形 → 中間色を作って合成
  内部パーツが実際に移動するため、複数パーツを持つ素材ではこちらが向く。

色の扱い（両方式共通）:
  - 合成は必ず「リニア光 × アルファ事前乗算」で行う。sRGBのガンマ値のまま
    平均すると中間フレームの輝度が両端より沈み、色が濁る
  - 中間色は OKLCh（知覚均等色空間の極座標）で L/C/h を補間する。
    RGB平均だと補色寄りの2色（例: オレンジ↔青緑）が灰色を通って退色するが、
    色相を回して繋ぐことで彩度を保ったまま遷移する
  - 非重複領域（片方にしか色が無い画素）には最近傍の有効色を充填してから
    補間する。これによりフレーム全体が同じ色相で遷移し、輪郭の外側に
    「元の色が半透明のまま取り残された汚れ」が出ない

使い方:
    python -m scriptvedit.morph a.png b.png -o output.mp4
    python -m scriptvedit.morph a.png b.png -o output.mp4 --method transport

必要ライブラリ:
    pip install numpy pillow scipy opencv-python tqdm
"""

import argparse
import inspect
import os
import sys
import numpy as np
from PIL import Image
from scipy.optimize import linear_sum_assignment
from scipy.interpolate import RBFInterpolator
import cv2
from tqdm import tqdm

from scriptvedit.validate import _reject_unknown_keys


# ============================================================
# 定数
# ============================================================

# MORPH_PARAM_KEYS（**params の既知キー）は _prepare_morph のシグネチャから
# 導出する（定義は _prepare_morph の直後）。二重管理を避けるため手書きしない

# 実効サンプル数 Na+Nb の警告閾値。コスト行列が (Na+Nb)^2 float32、
# ハンガリアン法が O(N^3) のため、これを超えるとメモリ・時間が急増する
MORPH_SAMPLES_WARN = 16000

# RBF の smoothing 下限（0 だと補間行列が特異になり得る）
MIN_SMOOTHING = 1e-6

# 利用できるモーフ方式
MORPH_METHODS = ("transport", "sdf")

# 既定のモーフ方式。
# 従来の "transport" は最適輸送の対応をRBFで均した結果、中間フレームの
# シルエットが波打った不定形（アメーバ状）になり、紹介動画で不採用になった。
# "sdf" は中間形状が常に滑らかな単一シルエットになり、位置・サイズがずれた
# 素材や文字グリフでも破綻しないため、既定をこちらに切り替える。
# 内部パーツを動かしたい素材では method="transport" を明示指定する。
DEFAULT_MORPH_METHOD = "sdf"

# 中間色の作り方（method="transport" 用）
#   "oklch"  : OKLCh で L/C/h を補間（既定・彩度を保つ）
#   "oklab"  : OKLab 直線補間（色相は保たないが輝度は正しい）
#   "linear" : リニア光 RGB の線形補間（物理的な混色。中間で彩度が落ちる）
#   "premul" : 事前乗算のままクロスディゾルブ（従来方式に最も近い。
#              非重複領域の色補完も行わないため輪郭に元の色が残る）
COLOR_MIX_MODES = ("oklch", "oklab", "linear", "premul")

# アルファ（マット）の混ぜ方（method="transport" 用）
#   "sdf"      : 符号付き距離場で形そのものを補間（既定・半透明の帯が出ない）
#   "dissolve" : 旧実装のアルファ線形ディゾルブ
ALPHA_MODES = ("sdf", "dissolve")

# 距離場マットへ完全に切り替わるまでの進行度（両端の縁を元マットのまま保つ）
_SDF_ENDPOINT_RAMP = 0.15

# 距離場マットの被覆面積が線形ディゾルブ比でこの範囲を下回ったら線形側へ戻す
# （形が大きく離れているときSDF補間が形を消してしまう事故の安全弁）
_SDF_SAFE_LO = 0.35
_SDF_SAFE_HI = 0.70

# 有効色とみなすアルファ下限（これ未満の画素は最近傍の色で埋める）
_COLOR_VALID_ALPHA = 0.35

# 事前乗算を解くときの下限アルファ（0除算・色ノイズ増幅の防止）
_UNPREMUL_EPS = 1e-4

# method="sdf" で「素材が柔らかすぎて距離場に載らない」と判定する閾値。
# 可視画素のうち半透明（0.02 < α < 0.98）が占める割合で測る。
# 実測: 硬いアイコン・図形・文字は 0.00〜0.03、ぼかしたグロー/影は 1.00。
_SDF_SOFT_LIMIT = 0.35

# 退化ケース（全透明／全不透明）で使う「無限遠」の距離。
# キャンバス寸法基準にしておくと、通常の距離値と桁が揃い補間が破綻しない
def _sdf_far(shape) -> float:
    return float(max(shape[0], shape[1]))


# ============================================================
# 色空間ユーティリティ（sRGB ⇄ リニア光 ⇄ OKLab）
# ============================================================
#
# sRGB 値はガンマ符号化されているため、そのまま算術平均すると
# 中間色の輝度が両端より沈む（＝モーフ中盤が暗く濁る主因）。
# 合成はリニア光で行い、色相の補間は知覚均等な OKLab（極座標＝OKLCh）で行う。

# リニア sRGB → LMS（OKLab 前段）
_OKLAB_M1 = np.array([
    [0.4122214708, 0.5363325363, 0.0514459929],
    [0.2119034982, 0.6806995451, 0.1073969566],
    [0.0883024619, 0.2817188376, 0.6299787005],
], dtype=np.float32)

# LMS' → OKLab
_OKLAB_M2 = np.array([
    [0.2104542553, 0.7936177850, -0.0040720468],
    [1.9779984951, -2.4285922050, 0.4505937099],
    [0.0259040371, 0.7827717662, -0.8086757660],
], dtype=np.float32)

# OKLab → LMS'
_OKLAB_M2_INV = np.array([
    [1.0, 0.3963377774, 0.2158037573],
    [1.0, -0.1055613458, -0.0638541728],
    [1.0, -0.0894841775, -1.2914855480],
], dtype=np.float32)

# LMS → リニア sRGB
_OKLAB_M1_INV = np.array([
    [4.0767416621, -3.3077115913, 0.2309699292],
    [-1.2684380046, 2.6097574011, -0.3413193965],
    [-0.0041960863, -0.7034186147, 1.7076147010],
], dtype=np.float32)

# 色相が意味を持つ最小彩度（OKLab の C）。これ未満は無彩色として直線補間する
_MIN_CHROMA = 0.012


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    """sRGB（0〜1）→ リニア光（0〜1）"""
    c = np.clip(np.asarray(c, dtype=np.float32), 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92,
                    np.power((c + 0.055) / 1.055, 2.4)).astype(np.float32)


def linear_to_srgb(c: np.ndarray) -> np.ndarray:
    """リニア光（0〜1）→ sRGB（0〜1）"""
    c = np.clip(np.asarray(c, dtype=np.float32), 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92,
                    1.055 * np.power(c, 1.0 / 2.4) - 0.055).astype(np.float32)


# uint8 sRGB → リニア光のルックアップテーブル（画像変換の高速化）
_SRGB8_TO_LINEAR = srgb_to_linear(np.arange(256, dtype=np.float32) / 255.0)


def linear_rgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
    """リニア sRGB (..., 3) → OKLab (..., 3)"""
    lms = np.asarray(rgb, dtype=np.float32) @ _OKLAB_M1.T
    return (np.cbrt(np.maximum(lms, 0.0)) @ _OKLAB_M2.T).astype(np.float32)


def oklab_to_linear_rgb(lab: np.ndarray) -> np.ndarray:
    """OKLab (..., 3) → リニア sRGB (..., 3)"""
    lms = np.asarray(lab, dtype=np.float32) @ _OKLAB_M2_INV.T
    return ((lms ** 3) @ _OKLAB_M1_INV.T).astype(np.float32)


def srgb8_to_oklab(rgb_u8: np.ndarray) -> np.ndarray:
    """uint8 sRGB (..., 3) → OKLab (..., 3)"""
    lin = _SRGB8_TO_LINEAR[np.clip(np.asarray(rgb_u8), 0, 255).astype(np.uint8)]
    return linear_rgb_to_oklab(lin)


def oklch_polar(lab_a, lab_b):
    """OKLCh 補間の frame 非依存な項（彩度・色相角・色相差）を先に計算する

    毎フレーム arctan2 を呼ばずに済ませるためのキャッシュ。
    返値: (ca, cb, ha, dh, ok)
    """
    ca = np.hypot(lab_a[..., 1], lab_a[..., 2])
    cb = np.hypot(lab_b[..., 1], lab_b[..., 2])
    ha = np.arctan2(lab_a[..., 2], lab_a[..., 1])
    hb = np.arctan2(lab_b[..., 2], lab_b[..., 1])
    # 色相は短い方の弧を回る（-π〜π に正規化）
    dh = (hb - ha + np.pi) % (2.0 * np.pi) - np.pi
    ok = np.minimum(ca, cb) > _MIN_CHROMA
    return ca, cb, ha, dh, ok


def mix_oklab(lab_a, lab_b, weight, color_path="oklch", polar=None):
    """OKLab の2つの色場を weight（0→A, 1→B）で補間する

    color_path="oklch": 明度・彩度・色相角を極座標補間する。
        補色どうし（例: オレンジ↔青緑）でも中間が無彩色（濁った灰／オリーブ）に
        ならず、色相が回り込む。
    color_path="oklab": 直線補間（従来のクロスディゾルブに近いが
        リニア光なので輝度は沈まない）。
    polar: oklch_polar() の返値（省略時は都度計算）
    """
    weight = np.asarray(weight, dtype=np.float32)
    la, aa, ba = lab_a[..., 0], lab_a[..., 1], lab_a[..., 2]
    lb, ab, bb = lab_b[..., 0], lab_b[..., 1], lab_b[..., 2]

    lum = la + (lb - la) * weight
    a_lin = aa + (ab - aa) * weight
    b_lin = ba + (bb - ba) * weight
    if color_path != "oklch":
        return np.stack([lum, a_lin, b_lin], axis=-1)

    ca, cb, ha, dh, ok = polar if polar is not None else oklch_polar(lab_a, lab_b)
    chroma = ca + (cb - ca) * weight
    hue = ha + dh * weight
    a_out = np.where(ok, chroma * np.cos(hue), a_lin)
    b_out = np.where(ok, chroma * np.sin(hue), b_lin)
    return np.stack([lum, a_out, b_out], axis=-1)


# ============================================================
# 画像読み込み・ピクセル抽出
# ============================================================

def load_images(path_a: str, path_b: str):
    """2つの画像を読み込み、同じキャンバスサイズに中央配置する"""
    img_a = Image.open(path_a).convert("RGBA")
    img_b = Image.open(path_b).convert("RGBA")

    w = max(img_a.width, img_b.width)
    h = max(img_a.height, img_b.height)

    def center_on_canvas(img, cw, ch):
        canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        ox = (cw - img.width) // 2
        oy = (ch - img.height) // 2
        canvas.paste(img, (ox, oy))
        return np.array(canvas)

    return center_on_canvas(img_a, w, h), center_on_canvas(img_b, w, h), (w, h)


def extract_pixels(img_array: np.ndarray):
    """不透明ピクセルの座標(x,y)と色(RGBA)を抽出"""
    mask = img_array[:, :, 3] > 0
    ys, xs = np.where(mask)
    return np.column_stack([xs, ys]).astype(np.float64), img_array[mask].astype(np.float64)


def subsample(positions, colors, max_n, rng):
    """ピクセル数がmax_nを超える場合、ランダムにサブサンプリング"""
    if len(positions) <= max_n:
        return positions, colors
    idx = rng.choice(len(positions), size=max_n, replace=False)
    return positions[idx], colors[idx]


# ============================================================
# 最適輸送（ハンガリアン法）
# ============================================================

def _cost_color_features(col, color_metric):
    """輸送コスト用の色特徴量（(N, 4) float32）を作る

    "oklab": 知覚均等な OKLab + アルファ。sRGB値の生の差と違い、
             「人の目に近い色の近さ」で対応付けられる。
             スケール 2.0 は白黒間の距離を旧RGBA指標と揃えるための係数
             （OKLab の L は 0〜1、旧指標は sqrt(3)≈1.73 だったため）
    "rgba" : 旧実装互換（sRGB値をそのまま 0〜1 に正規化）
    """
    col = np.asarray(col, dtype=np.float32)
    if color_metric == "rgba":
        return col / 255.0
    lab = srgb8_to_oklab(col[:, :3])
    return np.column_stack([lab * 2.0, col[:, 3:4] / 255.0]).astype(np.float32)


def solve_transport(pos_a, col_a, pos_b, col_b, canvas_size,
                    w_move=1.0, w_color=0.3, w_vanish=1.5,
                    color_metric="oklab"):
    """
    拡張コスト行列 (Na+Nb) x (Na+Nb) でハンガリアン法を解く

    コストは「移動距離 + 色距離」。色距離を知覚均等空間（OKLab）で測ることで、
    色の近い画素同士が優先的に対応し、モーフ中の色変化が小さくて済む。

    返値: src_pos, dst_pos, src_col, dst_col
      - 移動: src→dst に位置・色が変化
      - 消滅: src_pos=dst_pos, dst_col のα=0（フェードアウト）
      - 出現: src_pos=dst_pos, src_col のα=0（フェードイン）
    """
    na, nb = len(pos_a), len(pos_b)
    if na == 0 and nb == 0:
        return (np.empty((0, 2)), np.empty((0, 2)),
                np.empty((0, 4)), np.empty((0, 4)))

    max_dim = float(max(canvas_size))
    N = na + nb
    print(f"  コスト行列: {N}x{N}（{na} → {nb}）")

    cost = np.zeros((N, N), dtype=np.float32)
    if na > 0 and nb > 0:
        dx = pos_a[:, 0:1] - pos_b[:, 0:1].T
        dy = pos_a[:, 1:2] - pos_b[:, 1:2].T
        spatial = np.sqrt(dx**2 + dy**2, dtype=np.float32) / max_dim

        ca = _cost_color_features(col_a, color_metric)
        cb = _cost_color_features(col_b, color_metric)
        color_sq = np.zeros((na, nb), dtype=np.float32)
        for c in range(ca.shape[1]):
            dc = ca[:, c:c+1] - cb[:, c:c+1].T
            color_sq += dc * dc
        cost[:na, :nb] = w_move * spatial + w_color * np.sqrt(color_sq)

    cost[:na, nb:] = w_vanish
    cost[na:, :nb] = w_vanish

    print("  ハンガリアン法で計算中...")
    row_ind, col_ind = linear_sum_assignment(cost)

    out_sp, out_dp, out_sc, out_dc = [], [], [], []
    n_move = n_vanish = n_appear = 0

    for r, c in zip(row_ind, col_ind):
        if r < na and c < nb:
            out_sp.append(pos_a[r]); out_dp.append(pos_b[c])
            out_sc.append(col_a[r]); out_dc.append(col_b[c])
            n_move += 1
        elif r < na:
            out_sp.append(pos_a[r]); out_dp.append(pos_a[r])
            out_sc.append(col_a[r])
            f = col_a[r].copy(); f[3] = 0.0; out_dc.append(f)
            n_vanish += 1
        elif c < nb:
            out_sp.append(pos_b[c]); out_dp.append(pos_b[c])
            g = col_b[c].copy(); g[3] = 0.0; out_sc.append(g)
            out_dc.append(col_b[c])
            n_appear += 1

    print(f"  結果: 移動={n_move}, 消滅={n_vanish}, 出現={n_appear}")
    return (np.array(out_sp), np.array(out_dp),
            np.array(out_sc), np.array(out_dc))


# ============================================================
# ワープ場の構築（RBF 薄板スプライン補間）
# ============================================================

def build_warp_fields(src_pos, dst_pos, src_col, dst_col,
                      canvas_size, grid_step=8, smoothing=10.0):
    """
    スパースな制御点の対応関係から、画像全体の滑らかな変位場を構築する

    1. ソース側制御点（移動+消滅）→ ソース変位場 (dx_s, dy_s)
       移動点: 変位 = dst - src,  消滅点: 変位 = 0
    2. ターゲット側制御点（移動+出現）→ ターゲット変位場 (dx_t, dy_t)
       移動点: 変位 = src - dst,  出現点: 変位 = 0

    RBF補間で粗いグリッド上に変位を求め、バイリニアで全解像度に拡大
    """
    w, h = canvas_size
    delta = dst_pos - src_pos

    # ソース側: src_col の α > 0 の点（移動＋消滅）
    src_mask = src_col[:, 3] > 0
    src_ctrl = src_pos[src_mask]
    src_disp = delta[src_mask]

    # ターゲット側: dst_col の α > 0 の点（移動＋出現）
    tgt_mask = dst_col[:, 3] > 0
    tgt_ctrl = dst_pos[tgt_mask]
    tgt_disp = -delta[tgt_mask]

    # 境界アンカー（変位0で固定、ワープの発散を防止）
    n_edge = 14
    anchors = []
    for v in np.linspace(0, w - 1, n_edge):
        anchors.extend([[v, 0], [v, h - 1]])
    for v in np.linspace(0, h - 1, n_edge):
        anchors.extend([[0, v], [w - 1, v]])
    anchors = np.array(anchors)
    anchor_d = np.zeros((len(anchors), 2))

    # 評価グリッド（粗い格子点）
    gw = max(w // grid_step, 4)
    gh = max(h // grid_step, 4)
    gx, gy = np.meshgrid(np.linspace(0, w - 1, gw),
                          np.linspace(0, h - 1, gh))
    grid_pts = np.column_stack([gx.ravel(), gy.ravel()])

    # smoothing=0 は RBF の補間行列が特異になり得るため下限を設ける
    if smoothing < MIN_SMOOTHING:
        print(f"  警告: smoothing={smoothing} は小さすぎるため "
              f"{MIN_SMOOTHING} に引き上げます（特異行列の防止）")
        smoothing = MIN_SMOOTHING

    def interpolate_field(ctrl, disp, label):
        """制御点+境界アンカー → RBF補間 → フル解像度変位場"""
        ctrl_all = np.vstack([ctrl, anchors])
        disp_all = np.vstack([disp, anchor_d])

        print(f"    {label}: 制御点{len(ctrl)}個 + アンカー{len(anchors)}個")
        rbf = RBFInterpolator(
            ctrl_all, disp_all,
            kernel="thin_plate_spline",
            smoothing=smoothing,
        )
        vals = rbf(grid_pts)  # (gw*gh, 2)
        dx = vals[:, 0].reshape(gh, gw).astype(np.float32)
        dy = vals[:, 1].reshape(gh, gw).astype(np.float32)
        # バイリニア補間でフル解像度に拡大
        dx_full = cv2.resize(dx, (w, h), interpolation=cv2.INTER_LINEAR)
        dy_full = cv2.resize(dy, (w, h), interpolation=cv2.INTER_LINEAR)
        return dx_full, dy_full

    dx_s, dy_s = interpolate_field(src_ctrl, src_disp, "ソース側")
    dx_t, dy_t = interpolate_field(tgt_ctrl, tgt_disp, "ターゲット側")

    return dx_s, dy_s, dx_t, dy_t


# ============================================================
# レンダリング
# ============================================================

def linear_premultiply(rgba: np.ndarray) -> np.ndarray:
    """uint8 RGBA → 「リニア光 × アルファ事前乗算」の float32 RGBA（0〜1）

    ワープ（remap）と合成はこの空間で行う。事前乗算は境界ハロー防止、
    リニア光は中間色の輝度が沈むのを防ぐために必須。
    """
    rgba = np.asarray(rgba)
    lin = _SRGB8_TO_LINEAR[np.clip(rgba[:, :, :3], 0, 255).astype(np.uint8)]
    a = rgba[:, :, 3:4].astype(np.float32) / 255.0
    return np.dstack([lin * a, a]).astype(np.float32)


def _unpremultiply(pm: np.ndarray):
    """事前乗算済み(リニア) → (色, アルファ) に分解"""
    a = pm[:, :, 3:4]
    color = pm[:, :, :3] / np.maximum(a, _UNPREMUL_EPS)
    return np.clip(color, 0.0, 1.0), a


def _fill_from_nearest(color: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """valid=False の画素に「最も近い valid 画素の色」を複製する

    非重複領域（片方の画像にしか色が無い場所）でも色補間を成立させるための
    前処理。これを入れないと、その領域だけ元の色が半透明で取り残され、
    中間フレームの輪郭付近が茶色や暗い緑の斑になる。
    """
    if valid.all():
        return color
    if not valid.any():
        return np.zeros_like(color)
    # distanceTransform は「値0の画素までの距離」を測るので valid 側を 0 にする
    src = np.where(valid, 0, 255).astype(np.uint8)
    _, labels = cv2.distanceTransformWithLabels(
        src, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    h, w = valid.shape
    lut = np.zeros(int(labels.max()) + 1, dtype=np.int64)
    ys, xs = np.nonzero(valid)
    lut[labels[valid]] = ys.astype(np.int64) * w + xs
    flat = color.reshape(-1, color.shape[2])
    return flat[lut[labels]].reshape(color.shape)


def _matte_sdf(alpha2d: np.ndarray, level: float = 0.5):
    """アルファマット → 符号付き距離場 [px]（内側が正、外側が負）

    マットが空 or 全面のときは境界が無く距離場を作れないため None を返す
    （呼び出し側は線形ディゾルブへフォールバックする）。
    定数の番兵値を返して補間に混ぜると、実距離と桁が合わず et の全域で
    飽和してしまう（全面不透明素材のモーフが中盤で固まる不具合になる）。
    """
    inside = (alpha2d >= level).astype(np.uint8)
    if not inside.any() or inside.all():
        return None
    d_in = cv2.distanceTransform(inside, cv2.DIST_L2, 5)
    d_out = cv2.distanceTransform(1 - inside, cv2.DIST_L2, 5)
    return (d_in - d_out).astype(np.float32)


def _blend_alpha_sdf(a_s: np.ndarray, a_t: np.ndarray, et: float) -> np.ndarray:
    """2つのマットを「符号付き距離場の補間」で混ぜる

    値そのものを線形ディゾルブすると、片方にしか無い領域（縮む/伸びる縁）が
    中盤で一律 α≈0.5 の半透明になり、暗背景では汚れた縁として見える。
    また、ワープで閉じ切れなかった穴もその場で半透明のまま残る。
    距離場で補間すれば中間形状は常に不透明な1つのシルエットになり、
    半透明の帯・暗い塊が原理的に発生しない。

    ただし距離場からの再構成はアンチエイリアスを 1px の直線的な傾斜に
    作り直してしまうため、両端（et≈0/1）では元マットの線形ディゾルブへ
    戻し、静止画からモーフへ切り替わる瞬間に縁が跳ねないようにする。
    """
    linear = (1.0 - et) * a_s + et * a_t
    # 端点付近は元マットを尊重（0.15 は「半透明の帯が目立ち始める前」の経験値）
    w = min(min(et, 1.0 - et) / _SDF_ENDPOINT_RAMP, 1.0)
    if w <= 0.0:
        return linear
    d_s = _matte_sdf(a_s[:, :, 0])
    d_t = _matte_sdf(a_t[:, :, 0])
    if d_s is None or d_t is None:
        # 空／全面マットは距離場を定義できない → 従来どおりディゾルブ
        return linear
    d = (1.0 - et) * d_s + et * d_t
    # 距離0を境に約1px でアンチエイリアスする
    sdf_a = np.clip(d + 0.5, 0.0, 1.0)[:, :, None].astype(np.float32)

    # 安全弁: 2つの形が大きく離れていると距離場の補間は形を消してしまう
    # （SDF補間の既知の弱点）。被覆面積が明らかに落ちる場合は線形側へ戻す
    cov_lin = float(linear.sum())
    if cov_lin > 0.0:
        ratio = float(sdf_a.sum()) / cov_lin
        w *= min(max((ratio - _SDF_SAFE_LO) /
                     (_SDF_SAFE_HI - _SDF_SAFE_LO), 0.0), 1.0)
    return ((1.0 - w) * linear + w * sdf_a).astype(np.float32)


def _mix_linear_oklch(c0: np.ndarray, c1: np.ndarray, w) -> np.ndarray:
    """リニアsRGB 2色を OKLCh（明度L・彩度C・色相h）で補間する

    L と C は線形、h は近い側の回り方（最短弧）で補間するため、
    補色寄りの2色でも灰色を通らず彩度を保ったまま遷移する。
    （SDF方式と同じ mix_oklab() を共有し、色の経路を両方式で揃える）
    """
    lab = mix_oklab(linear_rgb_to_oklab(c0), linear_rgb_to_oklab(c1),
                    w, "oklch")
    return np.clip(oklab_to_linear_rgb(lab), 0.0, 1.0)


def _blend_settings(color_mix="oklch", color_local=0.0, alpha_sharp=0.0,
                    alpha_mode="sdf"):
    """レンダリング時の色パラメータを検証して返す（method="transport" 用）

    color_mix:   中間色の作り方（COLOR_MIX_MODES）
    color_local: 色の混合比を「時間一律(0)」から
                 「その画素の被覆率で重み付け(1)」へ寄せる度合い。
                 0 だと非重複領域も同じ色相で遷移して汚れが出にくい。
    alpha_mode:  アルファの混ぜ方（ALPHA_MODES）。
                 "sdf" は符号付き距離場で形を補間するため半透明の帯が出ない。
                 "dissolve" は旧来の線形ディゾルブ。
    alpha_sharp: alpha_mode="dissolve" のときにディゾルブを硬くする度合い
                 （0〜0.9）。大きいほど半透明で滞留する時間が短い。
    """
    if color_mix not in COLOR_MIX_MODES:
        raise ValueError(
            f"color_mix は {list(COLOR_MIX_MODES)} のいずれか: {color_mix!r}")
    if alpha_mode not in ALPHA_MODES:
        raise ValueError(
            f"alpha_mode は {list(ALPHA_MODES)} のいずれか: {alpha_mode!r}")
    return {
        "color_mix": color_mix,
        "color_local": float(min(max(color_local, 0.0), 1.0)),
        "alpha_mode": alpha_mode,
        "alpha_sharp": float(min(max(alpha_sharp, 0.0), 0.9)),
    }


def _compose_morph(ws: np.ndarray, wt: np.ndarray, et: float, cfg: dict):
    """ワープ済みの2枚（リニア事前乗算RGBA）→ 中間フレーム

    返値: (color, alpha)  color=リニア光の非事前乗算RGB, alpha=0〜1
    """
    if cfg["color_mix"] == "premul":
        # 従来方式に最も近い、事前乗算のままのクロスディゾルブ
        blended = (1.0 - et) * ws + et * wt
        return _unpremultiply(blended)[0], blended[:, :, 3:4]

    c_s, a_s = _unpremultiply(ws)
    c_t, a_t = _unpremultiply(wt)

    # 非重複領域の色を最近傍から埋め、どの画素でも色補間が成立するようにする
    valid_s = a_s[:, :, 0] >= _COLOR_VALID_ALPHA
    valid_t = a_t[:, :, 0] >= _COLOR_VALID_ALPHA
    if not valid_s.any():
        # 片方が完全に空なら相手の色で通す（黒へ引っぱられて暗くならないように）
        c_t = _fill_from_nearest(c_t, valid_t)
        c_s = c_t
    elif not valid_t.any():
        c_s = _fill_from_nearest(c_s, valid_s)
        c_t = c_s
    else:
        c_s = _fill_from_nearest(c_s, valid_s)
        c_t = _fill_from_nearest(c_t, valid_t)

    # アルファ（被覆率）
    if cfg["alpha_mode"] == "sdf":
        alpha = _blend_alpha_sdf(a_s, a_t, et)
    else:
        alpha = (1.0 - et) * a_s + et * a_t
        if cfg["alpha_sharp"] > 0.0:
            gain = 1.0 / (1.0 - cfg["alpha_sharp"])
            alpha = np.clip((alpha - 0.5) * gain + 0.5, 0.0, 1.0)

    # 色の混合比。既定（color_local=0）は時間一律 et
    w = np.float32(et)
    if cfg["color_local"] > 0.0:
        cov = (1.0 - et) * a_s + et * a_t
        w_cov = (et * a_t) / np.maximum(cov, _UNPREMUL_EPS)
        w = (1.0 - cfg["color_local"]) * et + cfg["color_local"] * w_cov

    mode = cfg["color_mix"]
    if mode == "oklch":
        # mix_oklab は (H, W) 形の重みを取る（L/C/h はチャンネルを持たない）
        color = _mix_linear_oklch(c_s, c_t, w if np.ndim(w) == 0 else w[:, :, 0])
    elif mode == "oklab":
        lab = ((1.0 - w) * linear_rgb_to_oklab(c_s)
               + w * linear_rgb_to_oklab(c_t))
        color = np.clip(oklab_to_linear_rgb(lab), 0.0, 1.0)
    else:  # "linear"
        color = (1.0 - w) * c_s + w * c_t
    return color, alpha


def _to_rgba_u8(color_lin: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """リニア光の色 + アルファ → 非事前乗算の uint8 RGBA"""
    srgb = linear_to_srgb(color_lin) * 255.0
    a = np.clip(alpha, 0.0, 1.0) * 255.0
    return np.clip(np.dstack([srgb, a]) + 0.5, 0, 255).astype(np.uint8)


def ease_in_out(t: float) -> float:
    """Hermite 補間によるスムーズなイージング"""
    return t * t * (3.0 - 2.0 * t)


def _prepare_morph(path_a, path_b, *,
                   max_pixels=2000, w_move=1.0, w_color=0.3, w_vanish=1.5,
                   grid_step=8, smoothing=10.0, color_metric="oklab"):
    """手順1〜4（画像読み込み→ピクセル抽出→最適輸送→ワープ場構築）の共通処理

    返値: arr_a, arr_b, canvas, dx_s, dy_s, dx_t, dy_t
    """
    # --- 1. 画像読み込み ---
    print("[1/5] 画像読み込み...")
    arr_a, arr_b, canvas = load_images(path_a, path_b)

    # --- 2. ピクセル抽出 + サブサンプリング ---
    print("[2/5] ピクセル抽出...")
    pos_a, col_a = extract_pixels(arr_a)
    pos_b, col_b = extract_pixels(arr_b)
    print(f"  A: {len(pos_a):,}px,  B: {len(pos_b):,}px")

    rng = np.random.default_rng(42)
    pos_a_s, col_a_s = subsample(pos_a, col_a, max_pixels, rng)
    pos_b_s, col_b_s = subsample(pos_b, col_b, max_pixels, rng)
    print(f"  サンプリング後: A={len(pos_a_s):,}, B={len(pos_b_s):,}")

    # 実効サンプル数（max_pixels の指定値でなく実際の N）で計算量を警告する。
    # ハードエラーにはしない（遅くても完走させ、既存スクリプトを壊さない）
    n_total = len(pos_a_s) + len(pos_b_s)
    if n_total > MORPH_SAMPLES_WARN:
        est_gb = (n_total * n_total * 4) / (1024 ** 3)
        print(f"  警告: サンプル数 {n_total:,} は推奨上限 {MORPH_SAMPLES_WARN:,} を超えています"
              f"（コスト行列 約{est_gb:.1f}GB + O(N^3) の最適輸送計算で数十分かかる可能性）。"
              f" max_pixels を下げることを推奨します")

    # --- 3. 最適輸送 ---
    print("[3/5] 最適輸送...")
    sp, dp, sc, dc = solve_transport(
        pos_a_s, col_a_s, pos_b_s, col_b_s, canvas,
        w_move=w_move, w_color=w_color, w_vanish=w_vanish,
        color_metric=color_metric,
    )

    # --- 4. ワープ場構築 ---
    print("[4/5] ワープ場構築（RBF補間）...")
    dx_s, dy_s, dx_t, dy_t = build_warp_fields(
        sp, dp, sc, dc, canvas,
        grid_step=grid_step, smoothing=smoothing,
    )

    return arr_a, arr_b, canvas, dx_s, dy_s, dx_t, dy_t


# ============================================================
# 形状ベースモーフ（SDF: 符号付き距離場）
# ============================================================

def alpha_to_sdf(alpha: np.ndarray) -> np.ndarray:
    """アルファ（0〜1）→ 符号付き距離場 [px]（内側が正、輪郭が0）

    ・二値化（α>=0.5）した距離変換で大域的な距離を作る
    ・アンチエイリアス画素（0<α<1）は α-0.5 でサブピクセル位置を復元する
      → 復元アルファ clip(sdf+0.5) が元のアルファとほぼ一致し、
        t=0 / t=1 で元画像に滑らかに繋がる
    """
    alpha = np.asarray(alpha, dtype=np.float32)
    mask = (alpha >= 0.5).astype(np.uint8)
    far = _sdf_far(alpha.shape)
    if not mask.any():
        return np.full(alpha.shape, -far, dtype=np.float32)
    if mask.all():
        return np.full(alpha.shape, far, dtype=np.float32)

    d_in = cv2.distanceTransform(mask, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    d_out = cv2.distanceTransform(1 - mask, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    sdf = np.where(mask > 0, d_in - 0.5, -(d_out - 0.5)).astype(np.float32)

    # 輪郭近傍のみサブピクセル補正（ソフトシャドウ等の広いグラデーションは触らない）
    edge = (alpha > 0.02) & (alpha < 0.98) & (np.abs(sdf) <= 1.5)
    sdf[edge] = alpha[edge] - 0.5
    return sdf


def _sdf_unusable(alpha: np.ndarray) -> bool:
    """このマットでは符号付き距離場による形状補間が使えないか判定する

    True になるのは次の2つ。どちらも距離場で補間すると事故になる。

    1. 輪郭が存在しない（全透明 / 全不透明）
       距離場が定義できず、定数の番兵値しか返せない。それを実距離場と
       線形補間すると値の桁が合わず、et の広い範囲で符号が飽和する。
       全面不透明の素材が中盤まで不透明のまま固まり、途中で急に抜ける
       （黒落ちより目立つポップになる）不具合の原因。
    2. ほぼ全体が半透明（ぼかしたグロー・ソフトシャドウ・グラデーション）
       距離場は「α=0.5 の等高線」しか持たないため、柔らかい階調が
       1px の硬いエッジに作り直されてしまう。
    """
    a = np.asarray(alpha, dtype=np.float32)
    inside = a >= 0.5
    if not inside.any() or inside.all():
        return True
    visible = a > 0.02
    partial = visible & (a < 0.98)
    return float(partial.sum()) / float(max(visible.sum(), 1)) > _SDF_SOFT_LIMIT


def _color_field(rgb_lin: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """画像の色を全画面に広げた「色場」を作る（形状が広がった先で使う色）

    ・不透明部（α>=0.5）は元の色をそのまま残す
    ・その外側は「輪郭から2px内側のコア画素」の最近傍色で埋める
      輪郭の1〜2px はラスタライズの都合で色が数階調ばらつくため、
      そこを種にすると広がった領域に放射状の筋が出る。コアを使うと消える。
    ・コアが取れない細い形状（線画など）は不透明部そのものを種にフォールバック
    """
    opaque = alpha >= 0.5
    if not opaque.any():
        return np.zeros_like(rgb_lin)
    core = cv2.erode(opaque.astype(np.uint8),
                     np.ones((3, 3), np.uint8), iterations=2).astype(bool)
    if not core.any():
        core = opaque
    ext = _voronoi_extend(rgb_lin, core)
    return np.where(opaque[:, :, None], rgb_lin, ext)


def _voronoi_extend(values: np.ndarray, seed_mask: np.ndarray) -> np.ndarray:
    """seed_mask=True の画素の値を、最近傍（ボロノイ分割）で全画面へ拡張する

    透明部の RGB は多くの PNG で 0 なので、そのまま補間すると輪郭に黒が滲む。
    形状が広がった先でも「いちばん近い実際の色」が使えるように前処理しておく。
    """
    h, w = seed_mask.shape
    if not seed_mask.any():
        return np.zeros_like(values)
    # distanceTransform は「0 の画素」をシードとして扱う
    src = np.where(seed_mask, 0, 255).astype(np.uint8)
    _, labels = cv2.distanceTransformWithLabels(
        src, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    ys, xs = np.nonzero(src == 0)
    lut = np.zeros(int(labels.max()) + 1, dtype=np.int64)
    lut[labels[ys, xs]] = ys.astype(np.int64) * w + xs
    flat = lut[labels].ravel()
    out = values.reshape(h * w, -1)[flat].reshape(values.shape)

    # 最近傍拡張はボロノイ境界で不連続になり、形状が広がった領域に
    # 放射状の筋（バンディング）として薄く見える。種の外だけを軽くぼかして消す。
    # 種の内側は元の色をそのまま残すので、両端フレームの色は変化しない。
    smooth = cv2.GaussianBlur(out, (0, 0), 2.0)
    return np.where(seed_mask[:, :, None], out, smooth)


def _shift_field(field: np.ndarray, off, border) -> np.ndarray:
    """平行移動（off=(dx,dy) px）。整列（centroid alignment）用"""
    dx, dy = float(off[0]), float(off[1])
    if abs(dx) < 1e-3 and abs(dy) < 1e-3:
        return field
    h, w = field.shape[:2]
    mat = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy]], dtype=np.float32)
    if border == "replicate":
        return cv2.warpAffine(field, mat, (w, h), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REPLICATE)
    return cv2.warpAffine(field, mat, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=border)


def _prepare_sdf_morph(path_a, path_b, *,
                       align=True, edge_softness=1.0,
                       color_ease=1, color_path="oklch"):
    """形状ベースモーフの前処理（画像読み込み→SDF→色場の拡張→OKLab化）

    パラメータ:
        align: True なら不透明部の重心を合わせてから形状補間する
            （位置がずれた図形が「フェードで入れ替わる」のではなく移動する）
        edge_softness: 輪郭のアンチエイリアス幅 [px]（大きいほどぼける）
        color_ease: 色の進行度に smoothstep を何回かけるか（0〜3）。
            大きいほど両端の色を保持し、中間色を通過する時間が短くなる
        color_path: "oklch"（色相を回して補間・既定）／"oklab"（直線補間）
    """
    print("[1/3] 画像読み込み...")
    arr_a, arr_b, canvas = load_images(path_a, path_b)
    w, h = canvas

    if color_path not in ("oklch", "oklab"):
        raise ValueError(
            f"未知の color_path: {color_path!r}（有効値: 'oklch', 'oklab'）")

    print("[2/3] 符号付き距離場（SDF）を構築...")
    alpha_a = arr_a[:, :, 3].astype(np.float32) / 255.0
    alpha_b = arr_b[:, :, 3].astype(np.float32) / 255.0
    sdf_a = alpha_to_sdf(alpha_a)
    sdf_b = alpha_to_sdf(alpha_b)

    # 重心（不透明度で重み付け）
    def centroid(alpha):
        total = float(alpha.sum())
        if total <= 1e-6:
            return np.array([(w - 1) / 2.0, (h - 1) / 2.0], dtype=np.float32)
        ys, xs = np.mgrid[0:h, 0:w]
        return np.array([float((xs * alpha).sum() / total),
                         float((ys * alpha).sum() / total)], dtype=np.float32)

    # 距離場が使えない素材（輪郭なし／ほぼ半透明）は形状もアルファの
    # 線形ディゾルブへ逃がす。距離場を無理に使うと形が固まって急に抜ける
    shape_dissolve = _sdf_unusable(alpha_a) or _sdf_unusable(alpha_b)
    if shape_dissolve:
        print("  注意: 距離場で扱えない素材（全透明/全不透明/ほぼ半透明）のため、"
              "形状はアルファの線形ディゾルブにフォールバックします")

    c_a = centroid(alpha_a)
    c_b = centroid(alpha_b)
    if align:
        print(f"  重心整列: A({c_a[0]:.1f}, {c_a[1]:.1f})"
              f" → B({c_b[0]:.1f}, {c_b[1]:.1f})")

    print("[3/3] 色場をボロノイ拡張して OKLab へ...")
    lin_a = _color_field(
        srgb_to_linear(arr_a[:, :, :3].astype(np.float32) / 255.0), alpha_a)
    lin_b = _color_field(
        srgb_to_linear(arr_b[:, :, :3].astype(np.float32) / 255.0), alpha_b)
    lab_a = linear_rgb_to_oklab(lin_a)
    lab_b = linear_rgb_to_oklab(lin_b)

    # 重心が一致していれば整列でのシフトは不要（色の極座標項を使い回せる）
    shift_needed = bool(align) and float(np.abs(c_b - c_a).max()) >= 1e-3

    return {
        "canvas": canvas,
        "arr_a": arr_a, "arr_b": arr_b,
        "sdf_a": sdf_a, "sdf_b": sdf_b,
        "alpha_a": alpha_a, "alpha_b": alpha_b,
        "shape_dissolve": shape_dissolve,
        "lab_a": lab_a, "lab_b": lab_b,
        "polar": None if shift_needed else oklch_polar(lab_a, lab_b),
        "c_a": c_a, "c_b": c_b,
        "align": shift_needed,
        "sdf_far": _sdf_far((h, w)),
        "edge_softness": max(float(edge_softness), 1e-3),
        "color_ease": int(min(max(color_ease, 0), 3)),
        "color_path": color_path,
    }


def _sdf_morph_frame(ctx, et_shape, et_color) -> np.ndarray:
    """SDF モーフの1フレームを RGBA(uint8, ストレートアルファ) で返す"""
    sdf_a, sdf_b = ctx["sdf_a"], ctx["sdf_b"]
    lab_a, lab_b = ctx["lab_a"], ctx["lab_b"]

    a_a, a_b = ctx["alpha_a"], ctx["alpha_b"]

    if ctx["align"]:
        c_t = (1.0 - et_shape) * ctx["c_a"] + et_shape * ctx["c_b"]
        off_a, off_b = c_t - ctx["c_a"], c_t - ctx["c_b"]
        far = -ctx["sdf_far"]
        sdf_a = _shift_field(sdf_a, off_a, far)
        sdf_b = _shift_field(sdf_b, off_b, far)
        lab_a = _shift_field(lab_a, off_a, "replicate")
        lab_b = _shift_field(lab_b, off_b, "replicate")
        if ctx["shape_dissolve"]:
            a_a = _shift_field(a_a, off_a, 0.0)
            a_b = _shift_field(a_b, off_b, 0.0)

    # --- 形状: SDF を線形補間し、等高線0を輪郭とする ---
    if ctx["shape_dissolve"]:
        # 距離場が使えない素材はアルファをそのまま線形ディゾルブする
        # （overshoot で負アルファを作らないよう進行度を [0,1] に丸める）
        w_a = min(max(et_shape, 0.0), 1.0)
        alpha = np.clip(a_a + (a_b - a_a) * w_a, 0.0, 1.0)
    else:
        sdf_t = sdf_a + (sdf_b - sdf_a) * et_shape
        alpha = np.clip(sdf_t / ctx["edge_softness"] + 0.5, 0.0, 1.0)

    # --- 色: OKLab（既定は OKLCh）で補間 ---
    # smoothstep を重ねるほど中間色を通過する時間が短くなる（濁りの滞在時間を削る）
    weight = float(et_color)
    for _ in range(ctx["color_ease"]):
        weight = ease_in_out(weight)

    lab_t = mix_oklab(lab_a, lab_b, np.float32(weight), ctx["color_path"],
                      polar=ctx["polar"])
    rgb = linear_to_srgb(oklab_to_linear_rgb(lab_t)) * 255.0

    rgba = np.dstack([rgb, alpha * 255.0])
    return np.clip(rgba + 0.5, 0, 255).astype(np.uint8)


# **params で受け付ける既知キー（タイポ検出用）。
# 各方式の前処理関数・色合成関数のシグネチャから導出し、二重管理を避ける
_PREPARE_PARAM_KEYS = frozenset(
    inspect.signature(_prepare_morph).parameters) - {"path_a", "path_b"}
_BLEND_PARAM_KEYS = frozenset(inspect.signature(_blend_settings).parameters)
TRANSPORT_PARAM_KEYS = _PREPARE_PARAM_KEYS | _BLEND_PARAM_KEYS
SDF_PARAM_KEYS = frozenset(
    inspect.signature(_prepare_sdf_morph).parameters) - {"path_a", "path_b"}
MORPH_PARAM_KEYS = TRANSPORT_PARAM_KEYS | SDF_PARAM_KEYS | {"method"}

# 両方式で名前が衝突しないことを保証する（衝突すると method 自動判定が壊れる）
assert not (TRANSPORT_PARAM_KEYS & SDF_PARAM_KEYS), \
    "transport と sdf でパラメータ名が衝突しています"


def _resolve_method(params):
    """**params から method を決める（params からは取り除く）

    既定は DEFAULT_MORPH_METHOD（="sdf"）。ただし method 未指定のまま
    transport 専用パラメータ（max_pixels 等）が渡された場合は transport を
    選ぶ。既定切り替え前に書かれた呼び出しが「使えないパラメータ」エラーで
    突然壊れるのを防ぐための後方互換措置。
    """
    method = params.pop("method", None)
    if method is not None:
        return method
    if set(params) & TRANSPORT_PARAM_KEYS:
        return "transport"
    return DEFAULT_MORPH_METHOD


def _split_method_params(method, params):
    """method に応じて有効なパラメータだけを取り出す（他方式のキーはエラー）"""
    if method not in MORPH_METHODS:
        raise ValueError(
            f"未知の method: {method!r}（有効値: {list(MORPH_METHODS)}）")
    valid = SDF_PARAM_KEYS if method == "sdf" else TRANSPORT_PARAM_KEYS
    wrong = set(params) - valid
    if wrong:
        raise ValueError(
            f"method={method!r} では使えないパラメータ: {sorted(wrong)}"
            f"（このmethodの有効キー: {sorted(valid)}）"
        )
    return {k: v for k, v in params.items() if k in valid}


def _split_transport_params(params):
    """method="transport" の **params を「前処理用」と「色合成用」に振り分ける"""
    prep = {k: v for k, v in params.items() if k in _PREPARE_PARAM_KEYS}
    blend = {k: v for k, v in params.items() if k in _BLEND_PARAM_KEYS}
    return prep, _blend_settings(**blend)


# ============================================================
# RGBA フレーム生成（scriptvedit統合用）
# ============================================================

def _generate_sdf_frames(path_a, path_b, out_dir, n_frames, blend_fn, params):
    """method="sdf" の RGBA PNG 連番生成"""
    ctx = _prepare_sdf_morph(path_a, path_b, **params)
    w, h = ctx["canvas"]

    os.makedirs(out_dir, exist_ok=True)
    print(f"[SDF] RGBAフレーム生成: {n_frames}フレーム, {w}x{h}")
    last = n_frames - 1
    for i in tqdm(range(n_frames), desc="フレーム生成"):
        t = i / max(last, 1)
        et_raw = blend_fn(t)
        # 形状は ease_out_back 等の overshoot を SDF の外挿として許すが、
        # 行き過ぎは形が壊れるため軽く制限する。色は必ず [0,1]
        et_shape = min(max(et_raw, -0.25), 1.25)
        et_color = min(max(et_raw, 0.0), 1.0)

        # 両端は元画像そのものを出す（前後のカット・静止画と完全に繋がる）
        if i == 0 and et_raw <= 0.0:
            rgba = ctx["arr_a"]
        elif i == last and et_raw >= 1.0:
            rgba = ctx["arr_b"]
        else:
            rgba = _sdf_morph_frame(ctx, et_shape, et_color)

        Image.fromarray(rgba, "RGBA").save(
            os.path.join(out_dir, f"frame_{i:05d}.png"))

    print(f"完了: {out_dir} ({n_frames}フレーム)")


def generate_rgba_frames(path_a, path_b, out_dir, n_frames, blend_fn=None, **params):
    """RGBA PNG連番を生成（背景合成なし、透明保持）

    Args:
        path_a: ソース画像パス
        path_b: ターゲット画像パス
        out_dir: 出力ディレクトリ（frame_00000.png 〜）
        n_frames: フレーム数
        blend_fn: ブレンド関数 t→et（Noneでease_in_out）
        **params: method="sdf"（既定）なら
                  align, edge_softness, color_ease, color_path。
                  method="transport" なら max_pixels, w_move, w_color,
                  w_vanish, grid_step, smoothing, color_metric, color_mix,
                  color_local, alpha_mode, alpha_sharp
    """
    if blend_fn is None:
        blend_fn = ease_in_out

    # 未知キーはタイポの可能性が高いため明示的にエラーにする
    _reject_unknown_keys(None, params, MORPH_PARAM_KEYS)

    method = _resolve_method(params)
    params = _split_method_params(method, params)
    if method == "sdf":
        _generate_sdf_frames(path_a, path_b, out_dir, n_frames, blend_fn, params)
        return

    prep_params, cfg = _split_transport_params(params)

    # --- 1〜4. 読み込み→抽出→最適輸送→ワープ場構築（共通処理） ---
    arr_a, arr_b, canvas, dx_s, dy_s, dx_t, dy_t = _prepare_morph(
        path_a, path_b, **prep_params)
    w, h = canvas

    # --- 5. RGBAフレーム生成 ---
    # ワープも合成も「リニア光 × 事前乗算」空間で行う
    # （sRGBのガンマ値のまま平均すると中間フレームの輝度が両端より沈み濁る）
    src_pm = linear_premultiply(arr_a)
    tgt_pm = linear_premultiply(arr_b)
    ident_x, ident_y = np.meshgrid(
        np.arange(w, dtype=np.float32),
        np.arange(h, dtype=np.float32),
    )

    os.makedirs(out_dir, exist_ok=True)
    print(f"[5/5] RGBAフレーム生成: {n_frames}フレーム, {w}x{h}")
    for i in tqdm(range(n_frames), desc="フレーム生成"):
        t = i / max(n_frames - 1, 1)
        # ワープ変位には未クランプの et を使い、ease_out_back / elastic 等の
        # overshoot（et>1 の行き過ぎ変形）を意図どおり表現する。
        # remap は範囲外座標を BORDER_CONSTANT で安全に扱うためクランプ不要。
        # 一方クロスディゾルブの重みは負アルファを生まないよう [0,1] にクランプする
        et_raw = blend_fn(t)
        et = min(max(et_raw, 0.0), 1.0)

        # 注意: 後方ワープ（出力座標基準の参照）に、ソース点で評価した
        # 前方基準の変位場をそのまま流用する近似。変位が大きい場合は
        # 参照位置がずれ、にじみ・ゴーストが出ることがある。

        # ソース画像をワープ
        mx_s = ident_x - et_raw * dx_s
        my_s = ident_y - et_raw * dy_s
        ws = cv2.remap(src_pm, mx_s, my_s, cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))

        # ターゲット画像を逆ワープ
        mx_t = ident_x - (1.0 - et_raw) * dx_t
        my_t = ident_y - (1.0 - et_raw) * dy_t
        wt = cv2.remap(tgt_pm, mx_t, my_t, cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))

        # 中間色の生成（リニア光 + OKLCh、非重複領域は最近傍色で補完、
        # マットは符号付き距離場で補間して半透明の暗い塊を出さない）
        color, alpha = _compose_morph(ws, wt, et, cfg)
        rgba = _to_rgba_u8(color, alpha)

        frame_path = os.path.join(out_dir, f"frame_{i:05d}.png")
        Image.fromarray(rgba, "RGBA").save(frame_path)

    print(f"完了: {out_dir} ({n_frames}フレーム)")


# ============================================================
# パーティクル分解・集合（explode / assemble）
# ============================================================

def _load_image_rgba(path: str) -> np.ndarray:
    """単一画像をRGBA配列として読み込む"""
    return np.array(Image.open(path).convert("RGBA"))


def _prepare_particles(path_a, *, max_pixels=2000, speed=200.0,
                       spread=1.0, swirl=0.0, seed=42, expand=0):
    """パーティクル前処理（画像読み込み→ピクセル抽出→初速度計算）

    extract_pixels / subsample を流用し、各粒子の初速度を
    「重心からの放射方向 + ランダムジッター + 回転（接線方向）」で決める。

    返値: arr, canvas, positions, colors, velocities
      - arr: RGBA画像（expand 指定時は透明マージン付き）
      - canvas: (w, h)
      - positions: (N, 2) 粒子の初期位置 [px]
      - colors: (N, 4) 粒子の色 RGBA（0〜255, float64）
      - velocities: (N, 2) 初速度 [px/正規化時間]
    """
    arr = _load_image_rgba(path_a)

    # 粒子が枠外で切れるのを緩和する透明マージン
    expand = int(max(expand, 0))
    if expand > 0:
        arr = np.pad(arr, ((expand, expand), (expand, expand), (0, 0)))

    h, w = arr.shape[:2]
    canvas = (w, h)

    positions, colors = extract_pixels(arr)
    rng = np.random.default_rng(seed)  # 再現性のため seed 必須
    positions, colors = subsample(positions, colors, max_pixels, rng)
    n = len(positions)
    print(f"  粒子数: {n:,}（max_pixels={max_pixels}）")

    if n == 0:
        return arr, canvas, positions, colors, np.empty((0, 2))

    # 放射方向の単位ベクトル（重心から外向き。重心直上の点はランダム方向）
    centroid = positions.mean(axis=0)
    offset = positions - centroid
    dist = np.linalg.norm(offset, axis=1, keepdims=True)
    theta = rng.uniform(0.0, 2.0 * np.pi, size=n)
    rand_unit = np.column_stack([np.cos(theta), np.sin(theta)])
    unit = np.where(dist > 1e-9, offset / np.maximum(dist, 1e-9), rand_unit)

    # 初速度 = 放射方向（大きさにばらつき） + ランダムジッター + 回転成分
    radial_mag = speed * rng.uniform(0.5, 1.5, size=(n, 1))
    velocities = unit * radial_mag
    if spread != 0.0:
        velocities = velocities + speed * spread * 0.5 * rng.normal(size=(n, 2))
    if swirl != 0.0:
        # 重心まわりの回転（接線方向速度 v_t = ω × r の線形近似）
        perp = np.column_stack([-offset[:, 1], offset[:, 0]])
        velocities = velocities + swirl * perp

    return arr, canvas, positions, colors, velocities


def _generate_particle_frames(path_a, out_dir, n_frames, blend_fn, *, reverse,
                              max_pixels=2000, speed=200.0, gravity=300.0,
                              spread=1.0, swirl=0.0, particle_size=2,
                              seed=42, dissolve=0.25, expand=0):
    """explode / assemble 共通のフレーム生成コア

    explode: 進行度 p=0 で元画像そのまま → p=1 で完全飛散＋フェードアウト。
    assemble は同じ軌道の時間反転（p を 1→0 に逆走）として実装し、
    コードを共有する。

    パラメータ:
        max_pixels: 粒子数の上限（超過分はサブサンプリング）
        speed: 放射方向の初速度スケール [px/正規化時間]
        gravity: 重力加速度 [px/正規化時間^2]（+y が下方向）
        spread: 初速度のランダム散らばり係数（0 で純粋な放射状）
        swirl: 重心まわりの回転角速度 [rad/正規化時間]（正で時計回り）
        particle_size: 粒子（円）の半径 [px]
        seed: 乱数シード（default_rng に渡す。再現性のため固定）
        dissolve: 元画像→粒子表現へクロスフェードする進行度区間（0〜dissolve）
        expand: キャンバスの透明マージン [px]（枠外に飛ぶ粒子の切れ防止）
    """
    if blend_fn is None:
        blend_fn = ease_in_out

    # --- 前処理（読み込み→抽出→初速度） ---
    arr, canvas, positions, colors, velocities = _prepare_particles(
        path_a, max_pixels=max_pixels, speed=speed,
        spread=spread, swirl=swirl, seed=seed, expand=expand,
    )
    w, h = canvas
    # 合成はリニア光 × 事前乗算（sRGB値のまま混ぜると中間が暗く濁る）
    img_pm = linear_premultiply(arr)
    r = max(int(particle_size), 1)

    os.makedirs(out_dir, exist_ok=True)
    mode = "assemble" if reverse else "explode"
    print(f"パーティクルフレーム生成（{mode}）: {n_frames}フレーム, {w}x{h}")
    for i in tqdm(range(n_frames), desc=f"{mode} フレーム生成"):
        t = i / max(n_frames - 1, 1)
        # 粒子位置は物理シミュレーションのため負値・overshoot は無意味。
        # generate_rgba_frames と異なり進行度は [0,1] にクランプする
        et = min(max(blend_fn(t), 0.0), 1.0)
        # assemble は explode の時間反転（進行度を 1→0 に逆走）
        p = 1.0 - et if reverse else et

        if p <= 0.0:
            # 進行度0 = 元画像そのまま（ピクセル一致を保証）
            rgba = arr
        else:
            # 粒子位置: pos + v*p + 0.5*g*p^2（p を正規化時間として扱う）
            cur = positions + velocities * p
            cur[:, 1] += 0.5 * gravity * p * p
            fade = 1.0 - p  # 進行度1で完全フェードアウト

            # 粒子レイヤーを描画（RGBA、円で塗りつぶし）
            layer = np.zeros((h, w, 4), dtype=np.uint8)
            xi = np.rint(cur[:, 0]).astype(np.int64)
            yi = np.rint(cur[:, 1]).astype(np.int64)
            vis = (xi >= -r) & (xi < w + r) & (yi >= -r) & (yi < h + r)
            for x, y, col in zip(xi[vis], yi[vis], colors[vis]):
                a = col[3] * fade
                if a < 1.0:
                    continue  # ほぼ透明な粒子はスキップ
                cv2.circle(layer, (int(x), int(y)), r,
                           (int(col[0]), int(col[1]), int(col[2]), int(a)),
                           thickness=-1, lineType=cv2.LINE_AA)

            # 元画像→粒子表現のクロスフェード（リニア光 × 事前乗算で合成）
            ramp = 1.0 if dissolve <= 0.0 else min(p / dissolve, 1.0)
            part_pm = linear_premultiply(layer)
            blended = (1.0 - ramp) * img_pm + ramp * part_pm

            # unpremultiply → sRGB へ戻して RGBA 保存
            color, alpha = _unpremultiply(blended)
            rgba = _to_rgba_u8(color, alpha)

        frame_path = os.path.join(out_dir, f"frame_{i:05d}.png")
        Image.fromarray(rgba, "RGBA").save(frame_path)

    print(f"完了: {out_dir} ({n_frames}フレーム)")


# **params で受け付ける既知キー（タイポ検出用）。
# _generate_particle_frames のキーワード専用引数から導出し、二重管理を避ける
PARTICLE_PARAM_KEYS = frozenset(
    inspect.signature(_generate_particle_frames).parameters) - {
        "path_a", "out_dir", "n_frames", "blend_fn", "reverse"}


def _check_particle_params(params):
    """未知キーはタイポの可能性が高いため明示的にエラーにする"""
    _reject_unknown_keys(None, params, PARTICLE_PARAM_KEYS)


def generate_explode_frames(path_a, out_dir, n_frames, blend_fn=None, **params):
    """画像を粒子化して飛散させる RGBA PNG 連番を生成

    t=0 で元画像そのまま → t=1 で完全飛散＋フェードアウト。

    Args:
        path_a: 入力画像パス
        out_dir: 出力ディレクトリ（frame_00000.png 〜）
        n_frames: フレーム数
        blend_fn: 進行カーブ t→et（None で ease_in_out）
        **params: max_pixels, speed, gravity, spread, swirl,
                  particle_size, seed, dissolve, expand
    """
    _check_particle_params(params)
    _generate_particle_frames(path_a, out_dir, n_frames, blend_fn,
                              reverse=False, **params)


def generate_assemble_frames(path_a, out_dir, n_frames, blend_fn=None, **params):
    """飛散状態の粒子が集合して画像になる RGBA PNG 連番を生成

    explode の時間反転。t=0 で完全飛散 → t=1 で元画像そのまま。
    引数は generate_explode_frames と同一。
    """
    _check_particle_params(params)
    _generate_particle_frames(path_a, out_dir, n_frames, blend_fn,
                              reverse=True, **params)


# ============================================================
# メイン処理
# ============================================================

def _create_video_sdf(path_a, path_b, output_path, *, fps, duration,
                      bg_color, sdf_params):
    """method="sdf" の動画書き出し（背景合成あり）"""
    ctx = _prepare_sdf_morph(path_a, path_b, **sdf_params)
    w, h = ctx["canvas"]
    bg = np.array(bg_color, dtype=np.float32).reshape(1, 1, 3)

    num_frames = int(fps * duration)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output_path, fourcc, fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError("VideoWriterを開けません")

    print(f"[SDF] レンダリング: {num_frames}フレーム, {w}x{h}")
    try:
        for i in tqdm(range(num_frames), desc="レンダリング"):
            t = i / max(num_frames - 1, 1)
            et = ease_in_out(t)
            rgba = _sdf_morph_frame(ctx, et, et).astype(np.float32)
            alpha = rgba[:, :, 3:4] / 255.0
            # over 合成（ストレートアルファ）
            frame = rgba[:, :, :3] * alpha + bg * (1.0 - alpha)
            frame_bgr = np.clip(frame[:, :, ::-1], 0, 255).astype(np.uint8)
            ok = writer.write(frame_bgr)
            if ok is False:
                raise RuntimeError(f"フレーム{i}の書き込みに失敗しました")
    finally:
        writer.release()
    print(f"完了: {output_path}")


def create_video(path_a, path_b, output_path, *,
                 max_pixels=2000, fps=30, duration=3.0,
                 w_move=1.0, w_color=0.3, w_vanish=1.5,
                 grid_step=8, smoothing=10.0,
                 color_metric="oklab", color_mix="oklch",
                 color_local=0.0, alpha_mode="sdf", alpha_sharp=0.0,
                 bg_color=(0, 0, 0),
                 method=DEFAULT_MORPH_METHOD, align=True, edge_softness=1.0,
                 color_ease=1, color_path="oklch"):
    """モーフィング動画を生成

    method="sdf"（既定）のときは align / edge_softness / color_ease /
    color_path が、method="transport" のときは max_pixels 以下の
    最適輸送系・色合成系パラメータが効く。
    """
    if method not in MORPH_METHODS:
        raise ValueError(
            f"未知の method: {method!r}（有効値: {list(MORPH_METHODS)}）")
    if method == "sdf":
        _create_video_sdf(
            path_a, path_b, output_path, fps=fps, duration=duration,
            bg_color=bg_color,
            sdf_params=dict(align=align, edge_softness=edge_softness,
                            color_ease=color_ease, color_path=color_path),
        )
        return

    cfg = _blend_settings(color_mix=color_mix, color_local=color_local,
                          alpha_mode=alpha_mode, alpha_sharp=alpha_sharp)

    # --- 1〜4. 読み込み→抽出→最適輸送→ワープ場構築（共通処理） ---
    arr_a, arr_b, canvas, dx_s, dy_s, dx_t, dy_t = _prepare_morph(
        path_a, path_b,
        max_pixels=max_pixels, w_move=w_move, w_color=w_color,
        w_vanish=w_vanish, grid_step=grid_step, smoothing=smoothing,
        color_metric=color_metric,
    )
    w, h = canvas

    # --- 5. 動画レンダリング ---
    # 事前計算（ワープ・合成はリニア光 × 事前乗算）
    src_pm = linear_premultiply(arr_a)
    tgt_pm = linear_premultiply(arr_b)
    ident_x, ident_y = np.meshgrid(
        np.arange(w, dtype=np.float32),
        np.arange(h, dtype=np.float32),
    )
    # 背景色もリニア光に変換してから合成する
    bg = srgb_to_linear(
        np.array(bg_color, dtype=np.float32).reshape(1, 1, 3) / 255.0)

    num_frames = int(fps * duration)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output_path, fourcc, fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError("VideoWriterを開けません")

    print(f"[5/5] レンダリング: {num_frames}フレーム, {w}x{h}")
    try:
        for i in tqdm(range(num_frames), desc="レンダリング"):
            t = i / max(num_frames - 1, 1)
            et = ease_in_out(t)

            # 注意: 後方ワープ（出力座標基準の参照）に、ソース点で評価した
            # 前方基準の変位場をそのまま流用する近似。変位が大きい場合は
            # 参照位置がずれ、にじみ・ゴーストが出ることがある。

            # ソース画像をワープ（後方写像: 出力(x,y) ← ソース(x-et*dx, y-et*dy)）
            mx_s = ident_x - et * dx_s
            my_s = ident_y - et * dy_s
            ws = cv2.remap(src_pm, mx_s, my_s, cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))

            # ターゲット画像を逆ワープ（t=0で最大変形、t=1で元に戻る）
            mx_t = ident_x - (1.0 - et) * dx_t
            my_t = ident_y - (1.0 - et) * dy_t
            wt = cv2.remap(tgt_pm, mx_t, my_t, cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))

            # 中間色の生成（リニア光 + OKLCh、非重複領域は最近傍色で補完）
            color, alpha = _compose_morph(ws, wt, et, cfg)

            # 背景合成（over合成: fg*α + bg*(1-α)）をリニア光で行い sRGB へ戻す
            frame = linear_to_srgb(color * alpha + bg * (1.0 - alpha)) * 255.0
            # RGB → BGR に変換して書き出し
            frame_bgr = np.clip(frame[:, :, ::-1] + 0.5, 0, 255).astype(np.uint8)
            # OpenCVのバージョンにより返値はNone/bool（Falseなら書き込み失敗）
            ok = writer.write(frame_bgr)
            if ok is False:
                raise RuntimeError(f"フレーム{i}の書き込みに失敗しました")
    finally:
        writer.release()
    print(f"完了: {output_path}")


def _particle_cli(mode, argv):
    """explode / assemble サブコマンドの CLI 処理"""
    p = argparse.ArgumentParser(
        prog=f"morph.py {mode}",
        description=("画像を粒子化して飛散させる連番PNG生成" if mode == "explode"
                     else "飛散状態から集合して画像になる連番PNG生成"),
    )
    p.add_argument("image", help="入力画像（PNG）")
    p.add_argument("out_dir", help="出力ディレクトリ（frame_00000.png 〜）")
    p.add_argument("--frames", type=int, default=30,
                   help="フレーム数（デフォルト: 30）")
    p.add_argument("--max-pixels", type=int, default=2000,
                   help="粒子数の上限（デフォルト: 2000）")
    p.add_argument("--speed", type=float, default=200.0,
                   help="放射方向の初速度スケール（デフォルト: 200）")
    p.add_argument("--gravity", type=float, default=300.0,
                   help="重力加速度（デフォルト: 300、+yが下方向）")
    p.add_argument("--spread", type=float, default=1.0,
                   help="初速度のランダム散らばり係数（デフォルト: 1.0）")
    p.add_argument("--swirl", type=float, default=0.0,
                   help="重心まわりの回転角速度 [rad]（デフォルト: 0）")
    p.add_argument("--particle-size", type=int, default=2,
                   help="粒子の半径 [px]（デフォルト: 2）")
    p.add_argument("--seed", type=int, default=42,
                   help="乱数シード（デフォルト: 42）")
    p.add_argument("--dissolve", type=float, default=0.25,
                   help="元画像→粒子のクロスフェード区間（デフォルト: 0.25）")
    p.add_argument("--expand", type=int, default=0,
                   help="キャンバスの透明マージン [px]（デフォルト: 0）")
    a = p.parse_args(argv)

    fn = generate_explode_frames if mode == "explode" else generate_assemble_frames
    fn(a.image, a.out_dir, a.frames,
       max_pixels=a.max_pixels, speed=a.speed, gravity=a.gravity,
       spread=a.spread, swirl=a.swirl, particle_size=a.particle_size,
       seed=a.seed, dissolve=a.dissolve, expand=a.expand)


def main():
    # explode / assemble サブコマンド（既存のモーフィングCLIとは独立）
    if len(sys.argv) > 1 and sys.argv[1] in ("explode", "assemble"):
        _particle_cli(sys.argv[1], sys.argv[2:])
        return

    p = argparse.ArgumentParser(
        description="最適輸送 + ワープ場によるモーフィング動画生成"
    )
    p.add_argument("image_a", help="入力画像A（PNG）")
    p.add_argument("image_b", help="入力画像B（PNG）")
    p.add_argument("-o", "--output", default="morph.mp4",
                   help="出力動画パス（デフォルト: morph.mp4）")
    p.add_argument("--max-pixels", type=int, default=2000,
                   help="OT計算の最大ピクセル数（デフォルト: 2000）")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--duration", type=float, default=3.0,
                   help="秒数（デフォルト: 3.0）")
    p.add_argument("--w-move", type=float, default=1.0,
                   help="移動コストの重み")
    p.add_argument("--w-color", type=float, default=0.3,
                   help="色変化コストの重み")
    p.add_argument("--w-vanish", type=float, default=1.5,
                   help="消滅/出現コストの重み")
    p.add_argument("--grid-step", type=int, default=8,
                   help="ワープ場グリッド間隔（小さいほど精密、デフォルト: 8）")
    p.add_argument("--smoothing", type=float, default=10.0,
                   help="RBF補間の滑らかさ（大きいほど滑らか、デフォルト: 10）")
    p.add_argument("--color-metric", choices=("oklab", "rgba"), default="oklab",
                   help="[transport] 輸送コストの色距離（デフォルト: oklab）")
    p.add_argument("--color-mix", choices=COLOR_MIX_MODES, default="oklch",
                   help="[transport] 中間色の作り方（デフォルト: oklch）")
    p.add_argument("--color-local", type=float, default=0.0,
                   help="[transport] 色の混合比を被覆率で重み付けする度合い 0〜1")
    p.add_argument("--alpha-mode", choices=ALPHA_MODES, default="sdf",
                   help="[transport] アルファの混ぜ方（デフォルト: sdf）")
    p.add_argument("--alpha-sharp", type=float, default=0.0,
                   help="[transport] dissolve時にアルファを硬くする度合い 0〜0.9")
    p.add_argument("--bg", type=int, nargs=3, default=[0, 0, 0],
                   metavar=("R", "G", "B"), help="背景色")
    p.add_argument("--method", choices=list(MORPH_METHODS),
                   default=DEFAULT_MORPH_METHOD,
                   help=f"モーフ方式（sdf=形状ベース / transport=最適輸送。"
                        f"デフォルト: {DEFAULT_MORPH_METHOD}）")
    p.add_argument("--no-align", dest="align", action="store_false",
                   help="[sdf] 重心整列をしない")
    p.add_argument("--edge-softness", type=float, default=1.0,
                   help="[sdf] 輪郭のアンチエイリアス幅 px（デフォルト: 1.0）")
    p.add_argument("--color-ease", type=int, default=1,
                   help="[sdf] 色の進行に smoothstep をかける回数 0〜3"
                        "（デフォルト: 1。大きいほど両端の色を長く保つ）")
    p.add_argument("--color-path", choices=["oklch", "oklab"], default="oklch",
                   help="[sdf] 色補間の経路（デフォルト: oklch）")
    a = p.parse_args()

    create_video(
        a.image_a, a.image_b, a.output,
        max_pixels=a.max_pixels, fps=a.fps, duration=a.duration,
        w_move=a.w_move, w_color=a.w_color, w_vanish=a.w_vanish,
        grid_step=a.grid_step, smoothing=a.smoothing,
        color_metric=a.color_metric, color_mix=a.color_mix,
        color_local=a.color_local, alpha_mode=a.alpha_mode,
        alpha_sharp=a.alpha_sharp,
        bg_color=tuple(a.bg),
        method=a.method, align=a.align, edge_softness=a.edge_softness,
        color_ease=a.color_ease, color_path=a.color_path,
    )


if __name__ == "__main__":
    main()
