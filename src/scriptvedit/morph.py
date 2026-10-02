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

import inspect
import os
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

# MORPH_PARAM_KEYS（**params の既知キー）は _prepare_morph / _blend_settings /
# _prepare_sdf_morph のシグネチャから導出する。二重管理を避けるため手書きしない。
# 定義は _prepare_sdf_morph の直後（導出元の関数が全て定義済みになる位置）。

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

# method="sdf" で「2枚の形が重ならない」と判定する閾値。
# 重なり = 不透明部（α>=0.5）の共通部分の面積 / 小さい方の面積（align=True なら
# 重心を合わせた後）。距離場の補間は、重なりの無い形どうしでは A がその場で痩せて
# 消え、B がその場で太って現れるだけになる（形が動かない＝見た目はクロスフェード）。
# 実測: 離れた位置の2つの文字列（align=False）は 0.0。同じ桁数の数字どうしは 0.4 以上
_SDF_OVERLAP_WARN = 0.05

# method="sdf" の fit=None（自動）で「2枚の大きさが違う」とみなす比。
# 不透明部の外接矩形の幅か高さがこの比を超えて違えば、外接矩形を合わせながら
# 補間する（fit）。距離場の補間は「相手の形から遠い部分ほど早く消え、遅く現れる」
# ので、幅の違う文字列どうしでは、はみ出す側の端の文字が動き出した直後に消える
# （実測: 1688px の日時 → 1156px の日時で、先頭の「20」と末尾の「4:07」が
# 進行度 0.2 で既に無い）。同じ大きさの組（同じ桁数の数字など）は従来どおり
# その場で溶けて入れ替わる方が良いので、合わせない
_SDF_FIT_AUTO_RATIO = 1.15

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


def _nearest_label_fill(values: np.ndarray, seed_mask: np.ndarray) -> np.ndarray:
    """seed_mask=True の画素の値を、最近傍（ボロノイ分割）で全画面へ複製する

    distanceTransformWithLabels は「値0の画素」をシードとして扱い、各画素に
    最も近いシードのラベルを返す。ラベル → そのシード画素の平坦インデックス
    という LUT を作り、一括 gather で値を配る。

    呼び出し側（`_fill_from_nearest` / `_voronoi_extend`）は短絡条件と後処理が
    違うだけで、最近傍複製そのものは両者で同一のためここへ集約してある。
    seed_mask が全 False だとラベルが作れないので、呼び出し側で先に弾くこと。
    """
    h, w = seed_mask.shape
    # distanceTransform は「値0の画素までの距離」を測るのでシード側を 0 にする
    src = np.where(seed_mask, 0, 255).astype(np.uint8)
    _, labels = cv2.distanceTransformWithLabels(
        src, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    ys, xs = np.nonzero(seed_mask)
    lut = np.zeros(int(labels.max()) + 1, dtype=np.int64)
    lut[labels[ys, xs]] = ys.astype(np.int64) * w + xs
    flat = lut[labels].ravel()
    return values.reshape(h * w, -1)[flat].reshape(values.shape)


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
    return _nearest_label_fill(color, valid)


def _matte_sdf(alpha2d: np.ndarray, level: float = 0.5):
    """アルファマット → 符号付き距離場 [px]（内側が正、外側が負）

    マットが空 or 全面のときは境界が無く距離場を作れないため None を返す
    （呼び出し側は線形ディゾルブへフォールバックする）。
    定数の番兵値を返して補間に混ぜると、実距離と桁が合わず et の全域で
    飽和してしまう（全面不透明素材のモーフが中盤で固まる不具合になる）。

    `alpha_to_sdf`（method="sdf" 用）と似ているが**等価ではない**。
    統合すると出力画素が変わるため、意図的に別関数のままにしてある。差は4点:

    1. 距離変換の精度: こちらは近似マスク `5`、alpha_to_sdf は DIST_MASK_PRECISE。
    2. 半画素オフセット: こちらは `d_in - d_out` の素の値、alpha_to_sdf は
       内外それぞれ 0.5px 内側へ寄せた値を返す。
    3. サブピクセル補正: alpha_to_sdf だけが輪郭近傍で sdf を α-0.5 に差し替える。
    4. 退化ケース: こちらは None（呼び出し側が線形ディゾルブへ逃がす）、
       alpha_to_sdf は番兵 ±_sdf_far を返す。こちらには _sdf_unusable のような
       事前判定が無いので、None フォールバックは削れない。
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

    method="transport" 側の `_matte_sdf` とは精度・半画素オフセット・
    サブピクセル補正・退化時の返値が異なる（詳細は `_matte_sdf` の docstring）。
    見た目が近いので共通化したくなるが、寄せると出力画素が変わる。
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
    if not seed_mask.any():
        return np.zeros_like(values)
    out = _nearest_label_fill(values, seed_mask)

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


def _alpha_centroid(alpha: np.ndarray) -> np.ndarray:
    """不透明度で重み付けした重心 (x, y)。全透明ならキャンバスの中心"""
    h, w = alpha.shape
    total = float(alpha.sum())
    if total <= 1e-6:
        return np.array([(w - 1) / 2.0, (h - 1) / 2.0], dtype=np.float32)
    ys, xs = np.mgrid[0:h, 0:w]
    return np.array([float((xs * alpha).sum() / total),
                     float((ys * alpha).sum() / total)], dtype=np.float32)


def _alpha_bbox(alpha: np.ndarray):
    """不透明部（α>=0.5）の外接矩形 (x0, y0, x1, y1)（x1, y1 は端の外側）。無ければ None"""
    mask = alpha >= 0.5
    if not mask.any():
        return None
    xs = np.flatnonzero(mask.any(axis=0))
    ys = np.flatnonzero(mask.any(axis=1))
    return (float(xs[0]), float(ys[0]), float(xs[-1] + 1), float(ys[-1] + 1))


def _resolve_fit(fit, box_a, box_b, align) -> bool:
    """fit（外接矩形を合わせながら補間するか）を決める。None は自動"""
    if box_a is None or box_b is None:
        return False
    if fit is not None:
        return bool(fit)
    if not align:
        return False   # align=False は「動かさない」指定なので自動では合わせない
    for lo, hi in ((0, 2), (1, 3)):
        sa, sb = box_a[hi] - box_a[lo], box_b[hi] - box_b[lo]
        if max(sa, sb) / max(min(sa, sb), 1.0) > _SDF_FIT_AUTO_RATIO:
            return True
    return False


def _fit_box(box_from, box_to, t):
    """2つの外接矩形を進行度 t で補間した矩形（中心と大きさを線形に。大きさは 1px 以上）"""
    out = []
    for lo, hi in ((0, 2), (1, 3)):
        c = ((box_from[lo] + box_from[hi]) * (1.0 - t)
             + (box_to[lo] + box_to[hi]) * t) / 2.0
        size = max((box_from[hi] - box_from[lo]) * (1.0 - t)
                   + (box_to[hi] - box_to[lo]) * t, 1.0)
        out.append((c - size / 2.0, c + size / 2.0))
    return (out[0][0], out[1][0], out[0][1], out[1][1])


def _fit_matrix(box_from, box_now):
    """box_from を box_now へ写すアフィン行列（軸ごとの拡大縮小 + 平行移動）と、
    距離の値に掛ける倍率（拡大縮小で距離も伸び縮みするため。軸で違うときは相乗平均）"""
    sx = (box_now[2] - box_now[0]) / max(box_from[2] - box_from[0], 1.0)
    sy = (box_now[3] - box_now[1]) / max(box_from[3] - box_from[1], 1.0)
    mat = np.array([[sx, 0.0, box_now[0] - sx * box_from[0]],
                    [0.0, sy, box_now[1] - sy * box_from[1]]], dtype=np.float32)
    return mat, float(np.sqrt(sx * sy))


def _warp_field(field: np.ndarray, mat, border) -> np.ndarray:
    """アフィン変換（fit 用。_shift_field の拡大縮小つき版）"""
    h, w = field.shape[:2]
    if border == "replicate":
        return cv2.warpAffine(field, mat, (w, h), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REPLICATE)
    return cv2.warpAffine(field, mat, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=border)


def _sdf_overlap(alpha_a, alpha_b, c_a, c_b, align, fit_boxes=None) -> float:
    """2枚の不透明部の重なり（共通部分の面積 / 小さい方の面積。0〜1）

    align=True のときは B を A の重心へ寄せてから測る（整列後に重なるなら
    形は動いて見える）。fit_boxes=(box_a, box_b) のときは B の外接矩形を A の
    外接矩形へ合わせてから測る。どちらかに不透明部が無ければ 0。
    """
    mask_a = alpha_a >= 0.5
    mask_b = alpha_b >= 0.5
    if fit_boxes is not None:
        (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) = (
            tuple(int(v) for v in box) for box in fit_boxes)
        mask_a = mask_a[ay0:ay1, ax0:ax1]
        mask_b = cv2.resize(mask_b[by0:by1, bx0:bx1].astype(np.uint8),
                            (ax1 - ax0, ay1 - ay0),
                            interpolation=cv2.INTER_NEAREST).astype(bool)
        align = False
    area = min(int(mask_a.sum()), int(mask_b.sum()))
    if area == 0:
        return 0.0
    if align:
        dx, dy = (int(round(float(v))) for v in (c_a - c_b))
        h, w = mask_b.shape
        moved = np.zeros_like(mask_b)
        ys = slice(max(dy, 0), min(h + dy, h))
        xs = slice(max(dx, 0), min(w + dx, w))
        yd = slice(max(-dy, 0), min(h - dy, h))
        xd = slice(max(-dx, 0), min(w - dx, w))
        moved[ys, xs] = mask_b[yd, xd]
        mask_b = moved
    return float((mask_a & mask_b).sum()) / float(area)


def _sdf_crossfade_reason(alpha_a, alpha_b, c_a, c_b, align, fit_boxes=None):
    """sdf のモーフが実質クロスフェードになる理由（ならなければ None）"""
    if _sdf_unusable(alpha_a) or _sdf_unusable(alpha_b):
        return ("輪郭が取れない素材（全面不透明・全透明・全体が半透明）のため、"
                "形は変形せずクロスフェードになります。背景が透明な PNG を使うか、"
                "transition() / fade を使ってください")
    overlap = _sdf_overlap(alpha_a, alpha_b, c_a, c_b, align, fit_boxes)
    if overlap < _SDF_OVERLAP_WARN:
        moved = align or fit_boxes is not None
        how = "位置を合わせても" if moved else "align=False のままでは"
        fix = ("形の違いが大きすぎます。method=\"transport\" を試すか、"
               "transition() / fade を使ってください" if moved else
               "align=True にするか、2枚の位置を揃えてください")
        return (f"2枚の不透明部が{how}重なりません（重なり {overlap:.0%}）。"
                f"形は動かず、その場で消えて現れるクロスフェードになります。{fix}")
    return None


def diagnose_sdf_morph(path_a, path_b, align=True, fit=None):
    """sdf のモーフが実質クロスフェードになる組かを調べる（p.audit() 用）

    返値: 理由の文字列（問題なければ None）。画像2枚を読むだけで距離場は作らない。
    """
    arr_a, arr_b, _canvas = load_images(path_a, path_b)
    alpha_a = arr_a[:, :, 3].astype(np.float32) / 255.0
    alpha_b = arr_b[:, :, 3].astype(np.float32) / 255.0
    box_a, box_b = _alpha_bbox(alpha_a), _alpha_bbox(alpha_b)
    use_fit = _resolve_fit(fit, box_a, box_b, bool(align))
    return _sdf_crossfade_reason(
        alpha_a, alpha_b, _alpha_centroid(alpha_a), _alpha_centroid(alpha_b),
        bool(align), (box_a, box_b) if use_fit else None)


def _prepare_sdf_morph(path_a, path_b, *,
                       align=True, fit=None, edge_softness=1.0,
                       color_ease=1, color_path="oklch", et_range=(0.0, 1.0)):
    """形状ベースモーフの前処理（画像読み込み→SDF→色場の拡張→OKLab化）

    et_range は形の進行度が取る範囲（内部引数。generate 側が blend から求める）。

    大きさの違う2枚（fit）: 距離場の補間は、相手の形から遠い部分ほど早く消え、
    遅く現れる。幅の違う文字列どうしでは、はみ出す側の端の文字が途中のコマで
    欠ける（_SDF_FIT_AUTO_RATIO のコメント参照）。fit では2枚の不透明部の
    外接矩形を、中心と大きさを補間しながら互いに合わせてから距離場を補間する
    （広い方が狭い方へ縮みながら変形する）。端が欠けない。

    キャンバスの余白: 整列（align / fit）で絵が動いた先がキャンバスの外に出ると
    端が切れ、ずらした距離場の欠けた帯（番兵値）が相手の絵まで消す。
    動く分だけ透明の余白を四方に足してから補間する
    （左右・上下で対称＝絵の位置は変わらない）。

    パラメータ:
        align: True なら不透明部の重心を合わせてから形状補間する
            （位置がずれた図形が「フェードで入れ替わる」のではなく移動する）
        fit: True なら不透明部の外接矩形（位置と大きさ）を合わせながら補間する。
            None（既定）は自動: align=True で、外接矩形の幅か高さが
            _SDF_FIT_AUTO_RATIO 倍を超えて違うときだけ合わせる。False は合わせない
        edge_softness: 輪郭のアンチエイリアス幅 [px]（大きいほどぼける）
        color_ease: 色の進行度に smoothstep を何回かけるか（0〜3）。
            大きいほど両端の色を保持し、中間色を通過する時間が短くなる
        color_path: "oklch"（色相を回して補間・既定）／"oklab"（直線補間）
    """
    print("[1/3] 画像読み込み...")
    arr_a, arr_b, canvas = load_images(path_a, path_b)

    if color_path not in ("oklch", "oklab"):
        raise ValueError(
            f"未知の color_path: {color_path!r}（有効値: 'oklch', 'oklab'）")

    alpha_a = arr_a[:, :, 3].astype(np.float32) / 255.0
    alpha_b = arr_b[:, :, 3].astype(np.float32) / 255.0

    box_a, box_b = _alpha_bbox(alpha_a), _alpha_bbox(alpha_b)
    use_fit = _resolve_fit(fit, box_a, box_b, bool(align))

    # 実質クロスフェードになる組は知らせる（黙って通すと原因が分からない）
    crossfade = _sdf_crossfade_reason(
        alpha_a, alpha_b, _alpha_centroid(alpha_a), _alpha_centroid(alpha_b),
        bool(align), (box_a, box_b) if use_fit else None)
    if crossfade:
        print(f"  警告: {crossfade}")

    # 整列で絵が動く分の余白（docstring 参照）
    mx = my = 0
    if use_fit:
        # 補間した外接矩形がキャンバスからはみ出す分（進行度が 0..1 の間は2枚の
        # 矩形の間に収まるので、はみ出すのは blend が行き過ぎるときだけ）
        for et in (float(et_range[0]), float(et_range[1])):
            x0, y0, x1, y1 = _fit_box(box_a, box_b, et)
            mx = max(mx, int(np.ceil(max(-x0, x1 - canvas[0], 0.0))))
            my = max(my, int(np.ceil(max(-y0, y1 - canvas[1], 0.0))))
        mx, my = (m + 2 + (m & 1) if m else 0 for m in (mx, my))
    elif align:
        # 重心の差 × 進行度の幅。重心の差が 1px 未満なら足さない
        gap = np.abs(_alpha_centroid(alpha_b) - _alpha_centroid(alpha_a))
        span = max(float(et_range[1]) - float(et_range[0]), 1.0)
        mx, my = (int(np.ceil(float(g) * span)) + 2 if g >= 1.0 else 0
                  for g in gap)
    # 偶数にそろえる（奇数だと 4:2:0 出力で元の絵の色差が半画素ずれる。_auto_expand と同じ）
    mx, my = (m + (m & 1) for m in (mx, my))
    if mx or my:
        print(f"  整列の余白: 左右 {mx}px / 上下 {my}px")
        pad = ((my, my), (mx, mx), (0, 0))
        arr_a = np.pad(arr_a, pad)
        arr_b = np.pad(arr_b, pad)
        canvas = (canvas[0] + 2 * mx, canvas[1] + 2 * my)
        alpha_a = arr_a[:, :, 3].astype(np.float32) / 255.0
        alpha_b = arr_b[:, :, 3].astype(np.float32) / 255.0
        if use_fit:
            box_a, box_b = (
                (b[0] + mx, b[1] + my, b[2] + mx, b[3] + my)
                for b in (box_a, box_b))
    w, h = canvas

    print("[2/3] 符号付き距離場（SDF）を構築...")
    sdf_a = alpha_to_sdf(alpha_a)
    sdf_b = alpha_to_sdf(alpha_b)

    # 距離場が使えない素材（輪郭なし／ほぼ半透明）は形状もアルファの
    # 線形ディゾルブへ逃がす。距離場を無理に使うと形が固まって急に抜ける
    shape_dissolve = _sdf_unusable(alpha_a) or _sdf_unusable(alpha_b)
    if shape_dissolve:
        print("  注意: 距離場で扱えない素材（全透明/全不透明/ほぼ半透明）のため、"
              "形状はアルファの線形ディゾルブにフォールバックします")

    c_a = _alpha_centroid(alpha_a)
    c_b = _alpha_centroid(alpha_b)
    if use_fit:
        print(f"  外接矩形を合わせて補間（fit）: "
              f"A {box_a[2] - box_a[0]:.0f}x{box_a[3] - box_a[1]:.0f}"
              f" → B {box_b[2] - box_b[0]:.0f}x{box_b[3] - box_b[1]:.0f}")
    elif align:
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
    shift_needed = (not use_fit and bool(align)
                    and float(np.abs(c_b - c_a).max()) >= 1e-3)

    return {
        "canvas": canvas,
        "arr_a": arr_a, "arr_b": arr_b,
        "sdf_a": sdf_a, "sdf_b": sdf_b,
        "alpha_a": alpha_a, "alpha_b": alpha_b,
        "shape_dissolve": shape_dissolve,
        "lab_a": lab_a, "lab_b": lab_b,
        "polar": (None if (shift_needed or use_fit)
                  else oklch_polar(lab_a, lab_b)),
        "c_a": c_a, "c_b": c_b,
        "align": shift_needed,
        "fit": use_fit, "box_a": box_a, "box_b": box_b,
        "sdf_far": _sdf_far((h, w)),
        "edge_softness": max(float(edge_softness), 1e-3),
        "color_ease": int(min(max(color_ease, 0), 3)),
        "color_path": color_path,
        "crossfade": crossfade,
    }


def _sdf_morph_frame(ctx, et_shape, et_color) -> np.ndarray:
    """SDF モーフの1フレームを RGBA(uint8, ストレートアルファ) で返す"""
    sdf_a, sdf_b = ctx["sdf_a"], ctx["sdf_b"]
    lab_a, lab_b = ctx["lab_a"], ctx["lab_b"]

    a_a, a_b = ctx["alpha_a"], ctx["alpha_b"]

    if ctx["fit"]:
        # 2枚の外接矩形を、補間した矩形へそれぞれ写してから混ぜる
        box_t = _fit_box(ctx["box_a"], ctx["box_b"], et_shape)
        mat_a, k_a = _fit_matrix(ctx["box_a"], box_t)
        mat_b, k_b = _fit_matrix(ctx["box_b"], box_t)
        far = -ctx["sdf_far"]
        sdf_a = _warp_field(sdf_a, mat_a, far) * k_a
        sdf_b = _warp_field(sdf_b, mat_b, far) * k_b
        lab_a = _warp_field(lab_a, mat_a, "replicate")
        lab_b = _warp_field(lab_b, mat_b, "replicate")
        if ctx["shape_dissolve"]:
            a_a = _warp_field(a_a, mat_a, 0.0)
            a_b = _warp_field(a_b, mat_b, 0.0)
    elif ctx["align"]:
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
    inspect.signature(_prepare_sdf_morph).parameters) - {
        "path_a", "path_b", "et_range"}
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
    """method="sdf" の RGBA PNG 連番生成

    返値: 実質クロスフェードになる組ならその理由（ならなければ None）
    """
    last = n_frames - 1
    # 形の進行度が取る範囲（overshoot の分も余白に入れる）
    ets = [min(max(blend_fn(i / max(last, 1)), -0.25), 1.25)
           for i in range(n_frames)]
    ctx = _prepare_sdf_morph(
        path_a, path_b, et_range=(min(ets + [0.0]), max(ets + [1.0])), **params)
    w, h = ctx["canvas"]

    os.makedirs(out_dir, exist_ok=True)
    print(f"[SDF] RGBAフレーム生成: {n_frames}フレーム, {w}x{h}")
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
    return ctx["crossfade"]


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

    返値: method="sdf" で実質クロスフェードになる組ならその理由の文字列
          （呼び出し側が警告に出す）。それ以外は None
    """
    if blend_fn is None:
        blend_fn = ease_in_out

    # 未知キーはタイポの可能性が高いため明示的にエラーにする
    _reject_unknown_keys(None, params, MORPH_PARAM_KEYS)

    method = _resolve_method(params)
    params = _split_method_params(method, params)
    if method == "sdf":
        return _generate_sdf_frames(
            path_a, path_b, out_dir, n_frames, blend_fn, params)

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
    return None


# ============================================================
# パーティクル分解・集合（explode / assemble）
# ============================================================

def _load_image_rgba(path: str) -> np.ndarray:
    """単一画像をRGBA配列として読み込む"""
    return np.array(Image.open(path).convert("RGBA"))


# 自動 expand（expand=None）で「見える」とみなす粒の不透明度の下限。
# fade=True の粒は進行度 p で 1-p に薄くなるので、これ未満まで薄れた粒は
# 範囲の見積もりに入れない（消えかけの粒のためにキャンバスを広げると、
# 面積に比例して重くなる）
_AUTO_EXPAND_MIN_FADE = 0.05

# 自動 expand の上限 [px]（片側）。超えたら警告して頭打ちにする
_AUTO_EXPAND_MAX = 4000


def _resolve_point(point, name):
    """toward / from_point の (dx, dy) を検証して float の組にする"""
    try:
        dx, dy = point
        dx, dy = float(dx), float(dy)
    except (TypeError, ValueError):
        raise ValueError(
            f"{name} は (dx, dy) の数値2つ（素材の中心からのずれ px。"
            f"右と下が正）: {point!r}") from None
    if not (np.isfinite(dx) and np.isfinite(dy)):
        raise ValueError(f"{name} に NaN / 無限大は使えません: {point!r}")
    return dx, dy


def _prepare_particles(path_a, *, max_pixels=2000, speed=200.0,
                       spread=1.0, swirl=0.0, seed=42, point=None):
    """パーティクル前処理（画像読み込み→ピクセル抽出→初速度計算）

    extract_pixels / subsample を流用し、各粒子の初速度を
    「重心からの放射方向 + ランダムジッター + 回転（接線方向）」で決める。
    point（素材の中心からのずれ px）を指定したときは放射ではなく、その1点へ
    集まる軌道の材料（到着の遅れ・横ぶれ）を作る。

    余白（expand）はここでは付けない。座標は素材画像の画素座標のままで、
    呼び出し側が余白ぶんずらす（自動 expand は軌道が決まってから見積もるため）。

    返値: arr, sim
      - arr: RGBA画像（余白なし）
      - sim: 軌道の材料。_particle_positions(sim, p, gravity) に渡す
    """
    arr = _load_image_rgba(path_a)
    h, w = arr.shape[:2]

    positions, colors = extract_pixels(arr)
    rng = np.random.default_rng(seed)  # 再現性のため seed 必須
    positions, colors = subsample(positions, colors, max_pixels, rng)
    n = len(positions)
    print(f"  粒子数: {n:,}（max_pixels={max_pixels}）")

    sim = {"positions": positions, "colors": colors, "n": n,
           "velocities": np.empty((0, 2)), "point": None}
    if n == 0:
        return arr, sim

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
    sim["velocities"] = velocities

    if point is not None:
        # 1点へ集まる軌道（explode の toward / assemble の from_point）。
        # 乱数は放射用の後に引く（point を指定しない場合の出力を変えないため）
        target = np.array([(w - 1) / 2.0 + point[0], (h - 1) / 2.0 + point[1]])
        rel = positions - target
        rel_len = np.linalg.norm(rel, axis=1, keepdims=True)
        rel_unit = np.where(rel_len > 1e-9, rel / np.maximum(rel_len, 1e-9),
                            rand_unit)
        stagger = float(min(max(spread, 0.0), 1.0)) * 0.4
        sim["point"] = {
            "target": target,
            "rel": rel,
            # 進行方向に直交する向き（横ぶれの方向）
            "side": np.column_stack([-rel_unit[:, 1], rel_unit[:, 0]]),
            # 粒ごとの出発の遅れ（spread が大きいほど列になって流れる）
            "stagger": stagger,
            "lag": rng.uniform(0.0, 1.0, size=(n, 1)) * stagger,
            # 横ぶれの振幅 [px]（途中でふくらみ、両端で 0）
            "wobble": speed * spread * 0.5 * rng.normal(size=(n, 1)),
            "swirl": float(swirl),
        }
    return arr, sim


def _particle_positions(sim, p, gravity):
    """進行度 p（0〜1）での粒子位置 (N, 2)。素材画像の画素座標"""
    pt = sim["point"]
    if pt is None:
        # 粒子位置: pos + v*p + 0.5*g*p^2（p を正規化時間として扱う）
        cur = sim["positions"] + sim["velocities"] * p
        cur[:, 1] += 0.5 * gravity * p * p
        return cur
    # 1点へ集まる: 粒ごとの進み e（0→1）で 元の位置 → 行き先 を結ぶ。
    # p=1 で全ての粒が行き先に着く（遅れて出た粒ほど速く進む）
    e = np.clip((p - pt["lag"]) / (1.0 - pt["stagger"]), 0.0, 1.0)
    rel = pt["rel"]
    if pt["swirl"] != 0.0:
        ang = pt["swirl"] * e[:, 0]
        c, s = np.cos(ang), np.sin(ang)
        rel = np.column_stack([rel[:, 0] * c - rel[:, 1] * s,
                               rel[:, 0] * s + rel[:, 1] * c])
    cur = pt["target"] + rel * (1.0 - e)
    cur = cur + pt["side"] * pt["wobble"] * np.sin(np.pi * e)
    # 重力は道すじのたるみ（両端で 0。+ で下へふくらむ）
    cur[:, 1] += gravity * (e * (1.0 - e))[:, 0]
    return cur


def _auto_expand(sim, canvas, progress, gravity, r, fade, limit=None):
    """粒が切れない余白 (ex, ey) [px] を、実際の軌道から求める。

    各フレームの進行度での粒子位置の外接矩形が、素材の矩形からはみ出す量を取る。
    素材は overlay で中央に置かれるので余白は左右・上下で対称にする
    （片側だけ足すと絵の位置がずれる）。fade=True では、ほぼ消えた粒
    （不透明度 _AUTO_EXPAND_MIN_FADE 未満）は数えない。
    limit=(lx, ly) は余白込みのキャンバスの半分の上限 [px]。Project の画面寸法を
    渡すと、素材の中心が画面内にある限り画面の外になる範囲を焼かない。
    """
    w, h = canvas
    over_x = over_y = 0.0
    if sim["n"] > 0:
        for p in sorted(set(progress)):
            if p <= 0.0:
                continue
            if fade and (1.0 - p) < _AUTO_EXPAND_MIN_FADE:
                continue
            cur = _particle_positions(sim, p, gravity)
            over_x = max(over_x, -float(cur[:, 0].min()),
                         float(cur[:, 0].max()) - (w - 1))
            over_y = max(over_y, -float(cur[:, 1].min()),
                         float(cur[:, 1].max()) - (h - 1))
    out = []
    for over, size, lim in ((over_x, w, limit[0] if limit else None),
                            (over_y, h, limit[1] if limit else None)):
        e = int(np.ceil(over)) + r + 2 if over > 0.0 else 0
        if lim is not None:
            e = min(e, max(int(np.ceil(lim - size / 2.0)), 0))
        if e > _AUTO_EXPAND_MAX:
            print(f"  警告: 自動 expand が {e}px になるため {_AUTO_EXPAND_MAX}px で"
                  f"頭打ちにします（粒が端で切れます。speed / gravity を下げるか "
                  f"expand を明示してください）")
            e = _AUTO_EXPAND_MAX
        # 偶数にそろえる: 奇数だと overlay の位置が1画素ずれ、4:2:0 出力で元の絵の
        # 色差が静止画として置いたときと半画素ずれる（縁の1列がにじむ）
        out.append(e + (e & 1))
    return out[0], out[1]


def _generate_particle_frames(path_a, out_dir, n_frames, blend_fn, *, reverse,
                              max_pixels=2000, speed=200.0, gravity=300.0,
                              spread=1.0, swirl=0.0, particle_size=2,
                              seed=42, dissolve=0.25, expand=None, fade=True,
                              point=None, expand_limit=None):
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
        expand: キャンバスの透明マージン [px]（枠外に飛ぶ粒子の切れ防止）。
            None（既定）は軌道から自動で決める（_auto_expand。左右と上下で別の値）
        fade: True（既定）は粒が進行度に合わせて薄れて消える（不透明度 1-p）。
            False は薄れず、散った位置に残る
        point: (dx, dy)。放射ではなく、素材の中心からこれだけずれた1点へ集まる
            （公開名は explode の toward / assemble の from_point）
        expand_limit: 自動 expand の上限（_auto_expand の limit）
    """
    if blend_fn is None:
        blend_fn = ease_in_out
    if point is not None:
        point = _resolve_point(point, "toward / from_point")

    # --- 前処理（読み込み→抽出→初速度） ---
    arr, sim = _prepare_particles(
        path_a, max_pixels=max_pixels, speed=speed,
        spread=spread, swirl=swirl, seed=seed, point=point,
    )
    r = max(int(particle_size), 1)

    # 各フレームの進行度（粒子位置は物理シミュレーションのため負値・overshoot は
    # 無意味。generate_rgba_frames と異なり [0,1] にクランプする）。
    # assemble は explode の時間反転（進行度を 1→0 に逆走）
    progress = []
    for i in range(n_frames):
        et = min(max(blend_fn(i / max(n_frames - 1, 1)), 0.0), 1.0)
        progress.append(1.0 - et if reverse else et)

    # --- 余白（粒子が枠外で切れるのを防ぐ透明マージン） ---
    if expand is None:
        ex, ey = _auto_expand(sim, (arr.shape[1], arr.shape[0]), progress,
                              gravity, r, fade, limit=expand_limit)
        print(f"  自動 expand: 左右 {ex}px / 上下 {ey}px")
    else:
        ex = ey = int(max(expand, 0))
    if ex > 0 or ey > 0:
        arr = np.pad(arr, ((ey, ey), (ex, ex), (0, 0)))
    h, w = arr.shape[:2]
    shift = np.array([ex, ey], dtype=np.float64)
    colors = sim["colors"]

    # 合成はリニア光 × 事前乗算（sRGB値のまま混ぜると中間が暗く濁る）。
    # 元画像があるのは余白の内側（iy, ix）だけなので、浮動小数の合成もそこだけで行う
    iy = slice(ey, h - ey)
    ix = slice(ex, w - ex)
    img_pm = linear_premultiply(arr[iy, ix])

    os.makedirs(out_dir, exist_ok=True)
    mode = "assemble" if reverse else "explode"
    print(f"パーティクルフレーム生成（{mode}）: {n_frames}フレーム, {w}x{h}")
    for i in tqdm(range(n_frames), desc=f"{mode} フレーム生成"):
        p = progress[i]

        if p <= 0.0:
            # 進行度0 = 元画像そのまま（ピクセル一致を保証）
            rgba = arr
        else:
            cur = _particle_positions(sim, p, gravity) + shift
            # fade=True は進行度1で完全フェードアウト。False は薄れず残る
            alpha_k = (1.0 - p) if fade else 1.0

            # 粒子レイヤーを描画（RGBA、円で塗りつぶし）
            layer = np.zeros((h, w, 4), dtype=np.uint8)
            xi = np.rint(cur[:, 0]).astype(np.int64)
            yi = np.rint(cur[:, 1]).astype(np.int64)
            vis = (xi >= -r) & (xi < w + r) & (yi >= -r) & (yi < h + r)
            for x, y, col in zip(xi[vis], yi[vis], colors[vis]):
                a = col[3] * alpha_k
                if a < 1.0:
                    continue  # ほぼ透明な粒子はスキップ
                cv2.circle(layer, (int(x), int(y)), r,
                           (int(col[0]), int(col[1]), int(col[2]), int(a)),
                           thickness=-1, lineType=cv2.LINE_AA)

            # 元画像→粒子表現のクロスフェード（リニア光 × 事前乗算で合成）。
            # 元画像が混ざるのは dissolve の間・元画像の矩形の中だけで、それ以外は
            # 粒子レイヤーがそのまま出力になる（全画素をリニア光へ往復させると、
            # キャンバスの面積に比例して重くなる。自動 expand で広がっても
            # 重くならないよう、必要な範囲だけ計算する）
            ramp = 1.0 if dissolve <= 0.0 else min(p / dissolve, 1.0)
            rgba = layer
            if ramp < 1.0:
                part_pm = linear_premultiply(layer[iy, ix])
                blended = (1.0 - ramp) * img_pm + ramp * part_pm
                # unpremultiply → sRGB へ戻す
                color, alpha = _unpremultiply(blended)
                rgba[iy, ix] = _to_rgba_u8(color, alpha)
                if ex > 0 or ey > 0:
                    # 余白へ出た粒は、元画像と同じ割合（ramp）で薄く出す
                    outside = np.ones((h, w), dtype=bool)
                    outside[iy, ix] = False
                    rgba[outside, 3] = np.rint(
                        rgba[outside, 3] * ramp).astype(np.uint8)

        frame_path = os.path.join(out_dir, f"frame_{i:05d}.png")
        # compress_level=1: 連番は直後に FFV1 へ入れて捨てる中間物なので、
        # 圧縮率より書き出しの速さを取る（画素は変わらない）
        Image.fromarray(rgba, "RGBA").save(frame_path, compress_level=1)

    print(f"完了: {out_dir} ({n_frames}フレーム)")


# **params で受け付ける既知キー（タイポ検出用）。
# _generate_particle_frames のキーワード専用引数から導出し、二重管理を避ける。
# point は公開名が関数ごとに違う（explode=toward / assemble=from_point）。
# expand_limit は Project が渡す内部引数で、DSL からは指定できない
_PARTICLE_CORE_KEYS = frozenset(
    inspect.signature(_generate_particle_frames).parameters) - {
        "path_a", "out_dir", "n_frames", "blend_fn", "reverse",
        "point", "expand_limit"}
EXPLODE_PARAM_KEYS = _PARTICLE_CORE_KEYS | {"toward"}
ASSEMBLE_PARAM_KEYS = _PARTICLE_CORE_KEYS | {"from_point"}

# 各キーの既定値（describe の表・テストが実装と突き合わせる）
PARTICLE_PARAM_DEFAULTS = {
    k: v.default
    for k, v in inspect.signature(_generate_particle_frames).parameters.items()
    if k in _PARTICLE_CORE_KEYS}


def _particle_call_params(params, valid_keys, point_key):
    """公開の **params を検証し、_generate_particle_frames の引数へ直す"""
    params = dict(params)
    limit = params.pop("expand_limit", None)
    # 未知キーはタイポの可能性が高いため明示的にエラーにする
    _reject_unknown_keys(None, params, valid_keys)
    if point_key in params:
        params["point"] = params.pop(point_key)
    if limit is not None:
        params["expand_limit"] = limit
    return params


def generate_explode_frames(path_a, out_dir, n_frames, blend_fn=None, **params):
    """画像を粒子化して飛散させる RGBA PNG 連番を生成

    t=0 で元画像そのまま → t=1 で完全飛散＋フェードアウト。

    Args:
        path_a: 入力画像パス
        out_dir: 出力ディレクトリ（frame_00000.png 〜）
        n_frames: フレーム数
        blend_fn: 進行カーブ t→et（None で ease_in_out）
        **params: max_pixels, speed, gravity, spread, swirl, particle_size,
                  seed, dissolve, expand, fade, toward
    """
    kw = _particle_call_params(params, EXPLODE_PARAM_KEYS, "toward")
    _generate_particle_frames(path_a, out_dir, n_frames, blend_fn,
                              reverse=False, **kw)


def generate_assemble_frames(path_a, out_dir, n_frames, blend_fn=None, **params):
    """飛散状態の粒子が集合して画像になる RGBA PNG 連番を生成

    explode の時間反転。t=0 で完全飛散 → t=1 で元画像そのまま。
    引数は generate_explode_frames と同じ（toward の代わりに from_point）。
    """
    kw = _particle_call_params(params, ASSEMBLE_PARAM_KEYS, "from_point")
    _generate_particle_frames(path_a, out_dir, n_frames, blend_fn,
                              reverse=True, **kw)


# ============================================================
# CLI エントリポイント
# ============================================================

# 動画書き出しと argparse は morph_cli.py に分離してある
# （ライブラリのレンダ経路は連番PNGだけを使い、CLI の mp4 書き出しは通らない）。
# ここで `from scriptvedit.morph_cli import main` と静的に書くと
# scripts/check_import_cycles.py が morph ↔ morph_cli を新しい循環として
# 拾ってしまうため、文字列指定の runpy でモジュールごと実行する
# （実行時の循環 import は起きない。この分岐は -m 実行時しか通らないため）。
if __name__ == "__main__":  # pragma: no cover
    import runpy

    runpy.run_module("scriptvedit.morph_cli", run_name="__main__")
