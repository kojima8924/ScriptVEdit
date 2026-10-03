# -*- coding: utf-8 -*-
"""点で描いた地球と世界地図: globe()

正射影（ortho）の地球か、正距円筒（plate）の世界地図を等間隔の点で描き、
回転（turn / spin）・都市の点（points）・大円の弧（arc）・波紋（ripple）・
昼夜の境（night）・札（label）を時刻つきの出来事として積んで、build() で
1本の透過動画（framekit.build。frames() と同じ qtrle・alpha つきの .mov）にする。

    g = globe(size=900)                               # 陸は同梱の地球（Natural Earth 1:110m）
    g.points(cities)                                  # 出典を書くか「模式図」と明記する
    g.arc((51.5, -0.1), (35.7, 139.7), t=1.0)
    g.ripple((35.7, 139.7), t=1.8)
    obj = g.build()
    obj.time(4) <= move(x=0.5, y=0.5, anchor="center")
    obj.figure.xy((35.7, 139.7), 2.0)                 # その時刻の東京の位置（キャンバスの px）

座標はすべて (緯度, 経度) の度（北緯・東経が正）。陸地の多角形だけは地図の慣習に
合わせて (経度, 緯度) の順（下の land を参照）。

使いどころの推奨: **1回 10 秒以内、1本の動画に 2 回まで**。地図は情報量が多く、
長く映すほど「実データの地図」に見える。都市の点は実データに見えるので、
出典を書くか「模式図」と明記する。色は framekit の図の色（白・灰・赤）だけ。
国境と国旗は描かない。

陸地（land）
-----------
陸地のデータは Natural Earth（パブリックドメイン）の 1:110m の陸を**同梱している**
（data/ne_110m_land_1440.png。1440×720・1bit・約 12KB。出典と利用条件は同じフォルダの
NOTICE.md と PNG の tEXt。作り直しは scripts/make_land_mask.py）。同梱の素材・データ
（assets/ と data/）は「全部自作・第三者素材ゼロ」で運用しており、これがその唯一の例外
（ユーザーの承認済み。自作の大陸の形は不正確な地図が実データに見え、同梱しないと既定が
陸の無い地球になるため）。素材ではなくライブラリとして同梱している formula() 用の KaTeX
（templates/vendor/katex/。MIT）は、この原則の外で、同じディレクトリのライセンスに従う。
指定は4通り:

- True（既定）: 同梱の地球。
- False: 陸地を描かず、15 度の経緯線の点と縁だけを描く。
- PNG のパス: 正距円筒（横:縦 = 2:1）の白黒マスク（白が陸）。見つからなければ
  asset() で探す（scripts/make_land_mask.py --width で細かいマスクも作れる）。
- 多角形のリスト [[(経度, 緯度), …], …]: テストと手作りの模式図用。内部で
  1440×720 に塗る（日付変更線をまたぐ多角形は2つに分けて渡す）。

None は受けない（以前の「陸なし」と取り違えないよう、陸なしは False と書く）。
鍵には陸地の**内容指紋**が入る（PNG は中身、多角形は座標の列）。パスは入らない。
地図を出すときは「地図: Natural Earth」と添えることを勧める（義務ではない）。

絵の作り
--------
- 地球の点は Fibonacci 球（等面積で、緯度によって疎密が出ない）。plate は画面上で等間隔の格子。
  step=None（既定）は画面上の点の間隔が ≒ 3.9×dot px になる角度（ortho は地球の中心での間隔）。
  size=900・dot 2.2 で 1.19 度（全体約 2.9 万点・陸に約 8 千点）、size=300 で 3.56 度
  （固定の角度だと小さい地球で点がつぶれて面になる）。
- 点の明るさ = limb + (1 − limb)·√z（z は視線方向の成分。縁ほど暗い）。
  縁のごく近く（z < 0.04）だけさらに薄めて、回転で縁から出入りする点が瞬かないようにする。
  陸の点は不透明度 0.55（_LAND_ALPHA）で、拠点の点（白）と弧（赤）より一段暗くする。
- 拠点の点（points）は、まわり（点の半径 + _POINT_MOAT = 1.2×dot）の陸の点を透明へ抜いて
  堀を作り（_knockout。destination-out なので背景が何でも同じ形）、点の色の淡い光の輪
  （_glow。点の縁で不透明度 _POINT_GLOW_A = 0.5、そこから点の半径の _POINT_GLOW_R = 3.2 倍まで
  (1 − u)² で薄れる）を敷いてから芯を描く。既定の色のままでも陸の点に埋もれない
  （見本の地球の場面 で、以前は land_color="dim" にして避けていた）。点が何千もあると
  堀が陸をほとんど消すので、密度を見せる点の群れは points(halo=False)（堀も光の輪も無し）にする。
  堀と光の輪は円ごとの近くの窓だけを計算し、円の数 × 窓の画素を _WINDOW_CHUNK ずつに分ける
  （_disc_cov）ので、点がいくつあってもメモリは一定。
- plate の view="auto"（plate の既定）は、地図の端（中心の反対の経線）を 0.5 度刻みで選ぶ:
  またぐ弧の数・端の近くの点や弧・端の経線が切る陸（緯度 -60〜80 度の帯）の少ないもの
  （_auto_center_lon）。太平洋を渡る弧は太平洋が真ん中の地図になる。
- 点は framekit.dots（端数位置のアンチエイリアス。半径 2px 以上）。波紋と濃淡の無い線は
  framekit.polyline。弧の尾（線に沿って不透明度が変わる線）は framekit.polyline に頂点ごとの
  不透明度の引数が無いので、このモジュールの _fade_line が描く（距離から被覆率を出す AA を
  自前で計算し、framekit の canvas の公開の形＝float32・事前乗算の HxWx4 へ直接 over する。
  framekit の内部関数には依存しない）。
- plate の弧の反りは弦に垂直で上向き（弦が縦に近いときは地図の内側へ）。反りで日付変更線
  （地図の左右の端）をまたいで反対の端に描かれないよう、振幅を縮める。
- 隠れの判定: 地表の点は z ≤ 0 なら裏。持ち上げた弧は「z < 0 かつ投影が円の内側」の
  ときだけ隠れる（縁の外へ出た部分は裏側でも見える）。境目は二分法で詰める。
- 向きは四元数で持ち、turn は slerp で補間する（等角速度。経度の補間と違い、
  日付変更線や極の近くで遠回りしない）。
- 動かない区間は、地球の点の層（陸・経緯線・縁・大気）を使い回す（framekit.layer_cache）。

重さ（実測。Windows・Python 3.10・numpy 2.0・opencv 4.13。1コマの描画時間の中央値。
framekit.build の uint8 への変換を含み、ffmpeg の符号化は別。陸は同梱の地球・既定の大気 0.25）
-----------------------------------------------------------------------------
- 900×900・回転なし・弧3本と波紋: 約 22ms（地球の点の層を使い回す）
- 900×900・spin / turn 中（毎コマ点の層を描き直す）: 約 60ms。大半は
  framekit.dots（点ごとに明るさが違うので、色を点ごとに持たせる経路になる）
- 900×900・点 40・弧 11 本が同時に飛ぶ: 約 55ms（拠点の点の堀と光の輪は、点の近くの窓だけを
  計算する _knockout / _glow で 1ms ほど。framekit.dots の大きくぼかした円で描くと、地図じゅうに
  散った点の外接矩形の全面を計算して 1回 15ms かかった）
- plate 1400×700・night・弧2本: 約 30ms
- plate 1400×700・点 3000: 約 0.2 秒（halo=False で約 0.07 秒）、点 2 万: 約 1 秒（halo=False で
  約 0.4 秒）。メモリは点の数によらず堀と光の輪で増えない（1 コマのピーク 80〜170MB は
  halo=False と同じ。分ける前は 2 万点で 1.5GB）。数千の点は絵の理由でも halo=False を勧める
1秒ぶん（30コマ）の生成は、回転なしで約 1.5 秒、回転中は約 2.5 秒（qtrle の符号化込み）。
spin は qtrle の「前のコマと同じ画素を飛ばす」が効かないので重く大きい
（実測: 900×900 で 2 秒 spin + 1.5 秒 turn が 65MB、5 秒回すと約 80MB）。
既定では回さず、3 度/秒を超えると警告する。

numpy・opencv-python・Pillow（framekit と同じ。extra は figures）は optional 依存で、
関数の中で遅延 import する。globe() の色の解決（framekit.palette）が3つを読み込むので、
build() と dry_run にも3つが要る（dry_run は draw を呼ばない）。
"""

import bisect
import datetime as _dt
import hashlib
import io
import json
import math
import os
import random
import re
import struct
from types import SimpleNamespace

import scriptvedit.easing as _easing_mod
import scriptvedit.framekit as fk
from scriptvedit.assets import asset
from scriptvedit.context import current_project
from scriptvedit.expr import Var
from scriptvedit.stillseq import _fps_fraction, _resolve_fps
from scriptvedit.validate import _require_number
from scriptvedit.warn import _warn


# --- 定数 ---

# 描画の版。描き方（点の配置・明るさ・弧の形・合成）を変えたら上げる（鍵に入る）
# 2: step=None を ortho でも画面上の密度から決める・plate の縦の弦の反り・濃淡つきの線の描き方
# 3: 陸の点の不透明度 0.8 → 0.55・拠点の点に堀（まわりの陸の点を抜く）と光の輪
_GLOBE_VER = "3"

_PROJECTIONS = ("ortho", "plate")
_DEFAULT_VIEW = (20.0, 0.0)     # ortho の view=None（plate の view=None は "auto"）
_LABEL_SIDES = ("right", "left", "top", "bottom")

# 同梱の地球（land=True）: Natural Earth 1:110m の陸の 1bit マスク（パブリックドメイン。
# 出典と利用条件は data/NOTICE.md と PNG の tEXt。作り直しは scripts/make_land_mask.py）
_EARTH_LAND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                           "ne_110m_land_1440.png")

_SIZE_MIN, _SIZE_MAX = 64, 4096
_STEP_MIN, _STEP_MAX = 0.6, 5.0
# step=None: 画面上の点の間隔（ortho は地球の中心での間隔）を dot の何倍にするか。
# 900px・dot 2.2 で 1.19 度（全体約 2.9 万点）になる密度。ortho も plate も同じ式で、
# 小さい地球ほど点を粗くする（固定の 1.2 度だと 300px で間隔 2.9px になり、点がつぶれて面に見える）
_SPACING_PER_DOT = 3.9
# 画面上の点の間隔が dot のこの倍（＝直径）を下回ると、点どうしが触れて面に見える（警告する）
_TOUCH_PER_DOT = 2.0
_MAX_POINTS = 200000
_MASK_W, _MASK_H = 1440, 720
_SPIN_WARN_DPS = 3.0
_BACK_ALPHA = 0.15
# 陸の点（と陸の無いときの経緯線）の不透明度。白い拠点の点と赤い弧が浮くように落とす
# （0.8 では既定の色の拠点の点が陸の点に埋もれた。見本の地球の場面 で比べて決めた）
_LAND_ALPHA = 0.55
_EDGE_FADE_Z = 0.04       # 地球の点: 縁のごく近くだけ薄める幅（z）
_POINT_FADE_Z = 0.08      # 都市の点: 裏へ回るときに薄める幅（z）
_POINT_MOAT = 1.2         # 拠点の点のまわりの陸の点を抜く幅（dot の倍。点の半径の外側）
_POINT_GLOW_R = 3.2       # 拠点の点の光の輪が消える半径（点の半径の倍）
_POINT_GLOW_A = 0.5       # 拠点の点の光の輪の不透明度（点の縁での値。点の色に掛ける）
_LABEL_FADE_Z = (0.05, 0.18)
_ARC_TAIL_FLOOR = 0.3     # 弧の尾の明るさの下限（着いた後も道すじが残る）
_ARC_HEAD_FADE = 0.3      # 着いた後に光の点が消えるまでの秒
_ARC_EASING = "ease_in_out_cubic"
_RIPPLE_WIDTH = 2.5
# plate の弧: 弦が縦からこの sin（0.5 = 30 度）以内なら、上ではなく地図の内側（横）へ反らせる
_PLATE_STEEP = 0.5
# 濃淡つきの線（弧の尾）は、線分をこの長さ（px）以下に切ってから描く（線分ごとの窓を小さく保つ）
_FADE_PIECE = 8.0
# 1回の numpy 計算で扱う「線分（円）の数 × 窓の画素」の上限。濃淡つきの線（_fade_line）と
# 拠点の点の堀・光の輪（_disc_cov）が、これを超えないよう分けて計算する（メモリが線の長さや
# 点の数に比例して膨らまない）
_WINDOW_CHUNK = 1 << 20
_STAGED_SPREAD = 0.12     # staged の1段の中で点く時刻のばらつき（秒）
_LIMB_WIDTH = 1.5         # 縁の線の太さ（px）
_LIMB_ALPHA = 0.85


# --- 値の検証 ---

def _num(fn, name, value, lo=None, hi=None):
    return float(_require_number(fn, name, value, lo, hi))


def _wrap_lon(lon):
    """経度を [-180, 180) へ"""
    v = (lon + 180.0) % 360.0 - 180.0
    return 0.0 if v == 0 else v   # -0.0 を鍵に出さない


def _coord(fn, name, value):
    """(緯度, 経度) を検証して (float, float) にする（経度は [-180, 180) へ）"""
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or any(isinstance(v, (tuple, list)) for v in value)):
        raise ValueError(f"{fn}: {name} は (緯度, 経度) の組で指定してください: {value!r}")
    lat = _num(fn, f"{name} の緯度", value[0], -90, 90)
    lon = _num(fn, f"{name} の経度", value[1], -360, 360)
    return (lat, _wrap_lon(lon))


def _time(fn, name, value):
    return _num(fn, name, value, 0, 36000)


def _dur(fn, name, value):
    v = _num(fn, name, value, 0, 600)
    if v <= 0:
        raise ValueError(f"{fn}: {name} は 0 より大きくしてください: {value!r}")
    return v


def _check_easing(fn, easing):
    names = sorted(n for n in dir(_easing_mod)
                   if n == "linear" or re.fullmatch(r"ease_(in|out|in_out)_[a-z]+", n))
    if not isinstance(easing, str) or easing not in names:
        raise ValueError(
            f"{fn}: easing はイージングの名前（文字列）で指定してください: {easing!r}。"
            f"有効な名前: {', '.join(names)}")
    return easing


_EASE_MEMO = {}


def _ease(name, u):
    e = _EASE_MEMO.get(name)
    if e is None:
        e = _EASE_MEMO[name] = getattr(_easing_mod, name)(Var("u"))
    return float(e.eval_at(min(1.0, max(0.0, u))))


def _smooth(e0, e1, x):
    if x <= e0:
        return 0.0
    if x >= e1:
        return 1.0
    u = (x - e0) / (e1 - e0)
    return u * u * (3 - 2 * u)


# --- ベクトル・四元数（標準ライブラリだけ。build / xy / dry_run で使う）---

_D2R = math.pi / 180.0


def _unit(lat, lon):
    """(緯度, 経度) → 地球に固定した単位ベクトル（x: 経度0, y: 東経90, z: 北極）"""
    la, lo = lat * _D2R, lon * _D2R
    c = math.cos(la)
    return (c * math.cos(lo), c * math.sin(lo), math.sin(la))


def _latlon(v):
    x, y, z = v
    n = math.sqrt(x * x + y * y + z * z)
    lat = math.degrees(math.asin(max(-1.0, min(1.0, z / n))))
    return lat, _wrap_lon(math.degrees(math.atan2(y, x)))


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _angle(a, b):
    """単位ベクトルどうしの角度（ラジアン。acos より端で安定な atan2 の形）"""
    cx = a[1] * b[2] - a[2] * b[1]
    cy = a[2] * b[0] - a[0] * b[2]
    cz = a[0] * b[1] - a[1] * b[0]
    return math.atan2(math.sqrt(cx * cx + cy * cy + cz * cz), _dot(a, b))


def _view_matrix(lat, lon):
    """(緯度, 経度) を画面の中心に、北を上に向けたときの回転（行 = 画面の右・上・手前）"""
    la, lo = lat * _D2R, lon * _D2R
    sla, cla, slo, clo = math.sin(la), math.cos(la), math.sin(lo), math.cos(lo)
    return ((-slo, clo, 0.0),
            (-sla * clo, -sla * slo, cla),
            (cla * clo, cla * slo, sla))


def _quat_from_matrix(m):
    (m00, m01, m02), (m10, m11, m12), (m20, m21, m22) = m
    tr = m00 + m11 + m22
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = (0.25 * s, (m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s)
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2
        q = ((m21 - m12) / s, 0.25 * s, (m01 + m10) / s, (m02 + m20) / s)
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2
        q = ((m02 - m20) / s, (m01 + m10) / s, 0.25 * s, (m12 + m21) / s)
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2
        q = ((m10 - m01) / s, (m02 + m20) / s, (m12 + m21) / s, 0.25 * s)
    n = math.sqrt(sum(c * c for c in q))
    q = tuple(c / n for c in q)
    return q if q[0] >= 0 else tuple(-c for c in q)


def _matrix_from_quat(q):
    w, x, y, z = q
    return ((1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
            (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
            (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)))


def _quat_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw)


def _quat_z(angle):
    """地軸（z）まわりの回転"""
    return (math.cos(angle / 2), 0.0, 0.0, math.sin(angle / 2))


def _slerp(q0, q1, s):
    d = sum(a * b for a, b in zip(q0, q1))
    if d < 0:
        q1, d = tuple(-c for c in q1), -d
    if d > 0.99999:
        q = tuple(a + (b - a) * s for a, b in zip(q0, q1))
    else:
        th = math.acos(min(1.0, d))
        st = math.sin(th)
        w0, w1 = math.sin((1 - s) * th) / st, math.sin(s * th) / st
        q = tuple(w0 * a + w1 * b for a, b in zip(q0, q1))
    n = math.sqrt(sum(c * c for c in q))
    return tuple(c / n for c in q)


def _apply(m, v):
    return (_dot(m[0], v), _dot(m[1], v), _dot(m[2], v))


# --- 太陽の真下の点（NOAA の簡略式）---

def _subsolar(when):
    """UTC の datetime → 太陽の真下の点 (緯度, 経度)（度）。

    NOAA Global Monitoring Division の "General Solar Position Calculations" の簡略式
    （年の割合 γ から赤緯と均時差を出す）。天文年鑑の略算式との差は赤緯・経度とも
    0.5 度未満（2024-07-19 04:09 UTC で北緯 20.98・東経 119.30。略算式は 20.76・119.34）。
    """
    when = when.astimezone(_dt.timezone.utc)
    y = when.year
    leap = (y % 4 == 0 and y % 100 != 0) or y % 400 == 0
    days = 366 if leap else 365
    doy = when.timetuple().tm_yday
    hour = when.hour + when.minute / 60.0 + (when.second + when.microsecond / 1e6) / 3600.0
    g = 2 * math.pi / days * (doy - 1 + (hour - 12) / 24.0)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    lon = -15.0 * (hour - 12.0 + eqtime / 60.0)
    return math.degrees(decl), _wrap_lon(lon)


# --- 点の数（build で上限を確かめる。numpy なし）---

def _fib_count(step):
    """Fibonacci 球の点の数（平均の間隔が step 度になる数）"""
    s = step * _D2R
    return max(12, int(round(4 * math.pi / (s * s))))


def _plate_grid(step):
    """plate の格子の (列数, 行数)。画面上で縦横同じ間隔になるよう列 = 2 × 行"""
    ny = max(2, int(round(180.0 / step)))
    return 2 * ny, ny


# --- 陸地の指定 ---

def _png_size(path):
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def _resolve_land(fn, land):
    """land を検証し、(種類, 中身, 鍵の値) を返す。

    種類: None（陸なし）/ "png"（中身はパス。内容指紋は framekit.build の files が鍵に入れる。
    同梱の地球もこれ）/ "poly"（中身は [[(経度, 緯度), …], …]。鍵は座標列の sha256）。
    パスは鍵に入れない。
    """
    if land is True:
        if not os.path.isfile(_EARTH_LAND):
            raise FileNotFoundError(
                f"{fn}: 同梱の陸地のデータが見つかりません: {_EARTH_LAND}"
                f"（パッケージが壊れています。scriptvedit を入れ直してください）")
        return "png", _EARTH_LAND, "png"
    if land is False:
        return None, None, None
    if land is None:
        raise ValueError(
            f"{fn}: land=None は使えません。陸を描かないなら land=False、"
            f"同梱の地球（Natural Earth 1:110m）なら land=True（既定）と書いてください")
    if isinstance(land, (str, os.PathLike)):
        path = os.fspath(land)
        if not os.path.isfile(path):
            path = asset(path)
        dims = _png_size(path)
        if dims is None:
            raise ValueError(f"{fn}: land の画像は PNG にしてください: {path}")
        w, h = dims
        if w != 2 * h:
            raise ValueError(
                f"{fn}: land の PNG は正距円筒（横:縦 = 2:1）にしてください: {path}（{w}x{h}）")
        return "png", path, "png"
    if isinstance(land, (list, tuple)):
        if not land:
            raise ValueError(f"{fn}: land の多角形のリストが空です")
        polys = []
        for i, ring in enumerate(land):
            if not isinstance(ring, (list, tuple)) or len(ring) < 3:
                raise ValueError(
                    f"{fn}: land[{i}] は (経度, 緯度) を3つ以上並べた多角形にしてください")
            pts = []
            for j, p in enumerate(ring):
                if not isinstance(p, (list, tuple)) or len(p) != 2:
                    raise ValueError(
                        f"{fn}: land[{i}][{j}] は (経度, 緯度) の組にしてください: {p!r}")
                lon = _num(fn, f"land[{i}][{j}] の経度", p[0], -180, 180)
                lat = _num(fn, f"land[{i}][{j}] の緯度", p[1], -90, 90)
                pts.append((lon, lat))
            polys.append(pts)
        text = json.dumps(polys, separators=(",", ":"))
        return "poly", polys, "poly:" + hashlib.sha256(text.encode()).hexdigest()[:16]
    raise ValueError(
        f"{fn}: land は True（同梱の地球）・False（陸なし）・PNG のパス・"
        f"多角形のリスト [[(経度, 緯度), …], …] のどれかで指定してください: {type(land).__name__}")


def _poly_image(d, polys):
    """多角形のリスト [[(経度, 緯度), …], …] → 正距円筒の "L" 画像（_MASK_W×_MASK_H。白が陸）"""
    img = d.Image.new("L", (_MASK_W, _MASK_H), 0)
    draw = d.ImageDraw.Draw(img)
    for ring in polys:
        pts = [((lon + 180.0) / 360.0 * _MASK_W, (90.0 - lat) / 180.0 * _MASK_H)
               for lon, lat in ring]
        draw.polygon(pts, fill=255)
    return img


# --- plate の中心の経度（view="auto"）---

_AUTO_STEP = 0.5            # 地図の端（中心の反対の経線）の候補の刻み（度）
_AUTO_CLEAR = 10.0          # 端からこの角度（度）より近い点・弧・波紋・札は、端で切れるとみなす
_AUTO_BAND = (-60.0, 80.0)  # 端が切る陸を測る緯度の帯（南極はどの経線でも切れるので数えない）
_AUTO_LAND_FREE = 0.05      # 帯の中の陸の割合がこれ以下の経線（島・半島の先）は切ってもよい
_AUTO_ARC_SAMPLES = 256     # 弧の経度の範囲を求める刻み（_plate_path の細かい道すじと同じ）
_LAND_PROFILE_MEMO = {}


def _land_profile(land):
    """陸地の指定 → 経度 -180 から _AUTO_STEP 度刻みの列ごとの、緯度の帯（_AUTO_BAND）の中の
    陸の割合のリスト（陸なしは None）。

    帯を切り出して BOX で 1 行へ縮める（Pillow だけ）。PNG は中身の sha256、多角形は鍵の値で
    プロセスの中でメモする（パスや更新時刻では引かない）。
    """
    kind, data, sig = land
    if kind is None:
        return None
    if kind == "png":
        with open(data, "rb") as f:
            raw = f.read()
        key = ("png", hashlib.sha256(raw).hexdigest())
    else:
        key = ("poly", sig)
    got = _LAND_PROFILE_MEMO.get(key)
    if got is not None:
        return got
    d = fk.need("globe")
    if kind == "png":
        with d.Image.open(io.BytesIO(raw)) as im:
            img = im.convert("L")
    else:
        img = _poly_image(d, data)
    w, h = img.size
    y0 = int(round((90.0 - _AUTO_BAND[1]) / 180.0 * h))
    y1 = int(round((90.0 - _AUTO_BAND[0]) / 180.0 * h))
    n = int(round(360.0 / _AUTO_STEP))
    row = img.crop((0, y0, w, y1)).resize((n, 1), d.Image.Resampling.BOX)
    prof = [v / 255.0 for v in row.getdata()]
    if len(_LAND_PROFILE_MEMO) >= 64:
        _LAND_PROFILE_MEMO.clear()
    _LAND_PROFILE_MEMO[key] = prof
    return prof


def _arc_lon_span(src, dst):
    """大円の弧の経度の範囲 (最小, 最大)（始点から連続に unwrap した度。幅は 360 未満とは限らない）"""
    a, b = _unit(*src), _unit(*dst)
    om = _angle(a, b)
    so = math.sin(om)
    lo = hi = prev = src[1]
    for k in range(1, _AUTO_ARC_SAMPLES + 1):
        s = k / _AUTO_ARC_SAMPLES
        if om < 1e-9:
            p = a
        else:
            w0, w1 = math.sin((1 - s) * om) / so, math.sin(s * om) / so
            p = tuple(w0 * u + w1 * v for u, v in zip(a, b))
        if abs(p[0]) < 1e-12 and abs(p[1]) < 1e-12:
            continue                        # 極の真上（経度が決まらない）
        lon = prev + _wrap_lon(math.degrees(math.atan2(p[1], p[0])) - prev)
        lo, hi = min(lo, lon), max(hi, lon)
        prev = lon
    return lo, hi


def _auto_center_lon(events, land):
    """plate の view="auto": 地図の左右の端（中心の反対の経線）を選び、中心の経度を返す。

    端の候補を _AUTO_STEP 度刻みに並べ、次の和が一番小さいものを選ぶ:
      - 端をまたぐ弧の数 × 100（またぐ弧は左右の端に分かれて描かれる）
      - 端の近さ: 端から _AUTO_CLEAR 度以内にある点・弧・波紋・札（端で切れる）ほど 0〜1
      - 端の経線が切る陸の割合（緯度 -60〜80 度の帯。南極は数えない）から _AUTO_LAND_FREE を
        引いたもの（大陸を左右に分けない。島や半島の先は数えない）
    同点なら中心の経度が 0 に近い方（出来事も陸も無ければ 0 = 見慣れた向き）。
    turn を積んだときは最初の向きだけを選ぶ（turn の行き先は turn の指定のまま）。
    """
    pts = []        # 幅の無い経度（点・札）
    spans = []      # 幅のある範囲 (始め, 終わり)（弧・波紋）。始め ≤ 終わり
    arcs = []
    for ev in events:
        k = ev["kind"]
        if k == "points":
            pts.extend(c[1] for c in ev["coords"])
        elif k == "label":
            pts.append(ev["coord"][1])
        elif k == "arc":
            span = _arc_lon_span(ev["src"], ev["dst"])
            arcs.append(span)
            spans.append(span)
        elif k == "ripple":
            lat, lon = ev["coord"]
            half = min(180.0, ev["max_deg"] / max(math.cos(math.radians(lat)), 0.05))
            spans.append((lon - half, lon + half))
    pts = sorted({p % 360.0 for p in pts})
    prof = _land_profile(land)
    n = int(round(360.0 / _AUTO_STEP))
    best = None
    for k in range(n):
        edge = -180.0 + k * _AUTO_STEP
        center = _wrap_lon(edge + 180.0)
        cross = sum(1 for lo, hi in arcs
                    if hi - lo >= 360.0 or 0.0 < (edge - lo) % 360.0 < hi - lo)
        clear = math.inf
        if pts:
            e = edge % 360.0
            i = bisect.bisect_left(pts, e)
            for p in (pts[i % len(pts)], pts[i - 1]):
                d = abs(e - p)
                clear = min(clear, d, 360.0 - d)
        for lo, hi in spans:
            if hi - lo >= 360.0:
                clear = 0.0
                break
            d = (edge - lo) % 360.0
            clear = min(clear, 0.0 if d <= hi - lo else min(d - (hi - lo), 360.0 - d))
        cost = 100.0 * cross + max(0.0, 1.0 - clear / _AUTO_CLEAR)
        if prof is not None:
            frac = 0.5 * (prof[k - 1] + prof[k])     # 端の経線の両側の列
            cost += max(0.0, frac - _AUTO_LAND_FREE)
        cand = (round(cost, 9), abs(center), center)
        if best is None or cand < best:
            best = cand
    return best[2]


# --- 組み立て役 ---

class Globe:
    """globe() が返す組み立て役。出来事を積んで build() で動画 Object にする。

    時刻 t はすべて build() で作る動画の先頭を 0 とした秒。メソッドは自分を返す。
    """

    def __init__(self, params, palette, land, font_spec):
        self._p = params
        self._palette = palette
        self._land = land
        self._font_spec = font_spec
        self._events = []
        self._view_memo = None

    def __repr__(self):
        kinds = {}
        for e in self._events:
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
        return (f"Globe({self._p['projection']}, {self._p['W']}x{self._p['H']}, "
                f"出来事={kinds})")

    # -- 出来事 --

    def _motion_overlap(self, fn, t0, t1):
        for ev in self._events:
            if ev["kind"] == "turn":
                a0, a1 = ev["t"], ev["t"] + ev["dur"]
            elif ev["kind"] == "spin":
                a0, a1 = ev["t0"], ev["t1"]
            else:
                continue
            if t0 < a1 and a0 < t1:
                raise ValueError(
                    f"{fn}: 回転（turn / spin）の区間 {t0:g}〜{t1:g} 秒が、"
                    f"先に積んだ {ev['kind']}（{a0:g}〜{a1:g} 秒）と重なっています。"
                    f"回転は1つずつ順に積んでください")

    def turn(self, t, to, *, dur=1.5, easing="ease_in_out_cubic"):
        """時刻 t から dur 秒で、to（(緯度, 経度)）を画面の中心へ回す（北が上）。

        向きは四元数の slerp で補間する（等角速度。経度を補間するのと違い、日付変更線や
        極の近くで遠回りしない）。plate では中心の経度だけが動く（近い向きへ回る）。
        """
        fn = "globe.turn"
        t = _time(fn, "t", t)
        dur = _dur(fn, "dur", dur)
        to = _coord(fn, "to", to)
        easing = _check_easing(fn, easing)
        self._motion_overlap(fn, t, t + dur)
        self._events.append({"kind": "turn", "t": t, "to": list(to), "dur": dur,
                             "easing": easing})
        return self

    def spin(self, t0, t1, deg_per_sec):
        """t0〜t1 秒のあいだ地軸まわりに回す（正 = 本物の地球と同じ向き。表面が左から右へ）。

        既定では回さない。回すと毎コマ全面が変わり、qtrle の「同じ画素を飛ばす」が
        効かない（実測: 900×900 で 5 秒回すと約 80MB）。3 度/秒を超えると警告する。
        """
        fn = "globe.spin"
        t0 = _time(fn, "t0", t0)
        t1 = _time(fn, "t1", t1)
        if t1 <= t0:
            raise ValueError(f"{fn}: t1（{t1:g}）は t0（{t0:g}）より後にしてください")
        dps = _num(fn, "deg_per_sec", deg_per_sec, -90, 90)
        if dps == 0:
            raise ValueError(f"{fn}: deg_per_sec は 0 以外にしてください")
        self._motion_overlap(fn, t0, t1)
        if abs(dps) > _SPIN_WARN_DPS:
            _warn(current_project(),
                  f"globe.spin: {dps:g} 度/秒は速すぎます（{_SPIN_WARN_DPS:g} 度/秒まで）。"
                  f"点が流れて読めず、動画も大きくなります（qtrle が効かない）")
        self._events.append({"kind": "spin", "t0": t0, "t1": t1, "dps": dps})
        return self

    def points(self, coords, *, t=0, color="fg", radius=4, appear="at", dur=0.3, name=None,
               halo=True):
        """都市などの点を置く。

        点のまわりの陸の点を抜いて（堀）点の色の淡い光の輪を敷くので、既定の色のままでも
        陸の点に埋もれない。
        halo: False で堀と光の輪を付けず、芯の点だけを描く。点が密で、堀が陸をほとんど消し
          光の輪がつながって霞になるとき（plate 1400×700 に数千点で密度を見せる、など）に使う。
          900px の地球に 300 点ほどなら既定のままで陸が読める。
        coords: [(緯度, 経度), …]（1点だけなら (緯度, 経度) でもよい）。
        appear: 点き方。
          "at"                          … 全部が t に点く
          ("wave", (緯度, 経度), 度/秒)   … 起点から近い順に、t + 角度の距離 ÷ 速さ に点く
          ("staged", [(秒, 件数), …])     … t + 秒 の時点で、先頭から「件数」番目までが点く
                                           （件数は累積。最後の件数は coords の件数と同じ）。
                                           1段の中では seed で決まる 0.12 秒以内のばらつきがつく
        dur: 1点が点くのにかける秒（明るくなりながら少し大きい所から縮んで落ち着く）。
        name: 点の組の名前。build() の戻り値の figure.xy(name, t) で位置の一覧を引ける。
        裏へ回った点は描かない（縁の手前で薄れる）。
        """
        fn = "globe.points"
        if (isinstance(coords, (tuple, list)) and len(coords) == 2
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in coords)):
            coords = [coords]
        if not isinstance(coords, (tuple, list)) or not coords:
            raise ValueError(f"{fn}: coords は (緯度, 経度) を1つ以上並べたリストで指定してください")
        if len(coords) + self._count_points() + self._p["n_base"] > _MAX_POINTS:
            raise ValueError(
                f"{fn}: 点が多すぎます（地球の点 {self._p['n_base']} と合わせて"
                f" {_MAX_POINTS} 点まで）")
        pts = [list(_coord(fn, f"coords[{i}]", c)) for i, c in enumerate(coords)]
        t = _time(fn, "t", t)
        col = fk.color(fn, color, self._palette)
        radius = _num(fn, "radius", radius, 2, 40)
        dur = _dur(fn, "dur", dur)
        if name is not None and (not isinstance(name, str) or not name):
            raise ValueError(f"{fn}: name は空でない文字列で指定してください: {name!r}")
        if name is not None and any(e.get("name") == name for e in self._events):
            raise ValueError(f"{fn}: name '{name}' はもう使われています")
        if not isinstance(halo, bool):
            raise ValueError(f"{fn}: halo は True か False で指定してください: {halo!r}")
        app = self._check_appear(fn, appear, len(pts))
        self._events.append({"kind": "points", "t": t, "coords": pts, "color": list(col),
                             "radius": radius, "appear": app, "dur": dur, "name": name,
                             "halo": halo})
        return self

    def _check_appear(self, fn, appear, n):
        if appear == "at":
            return ["at"]
        if isinstance(appear, (tuple, list)) and appear and appear[0] == "wave":
            if len(appear) != 3:
                raise ValueError(f"{fn}: appear=('wave', 起点, 度/秒) の形で指定してください")
            origin = _coord(fn, "appear の起点", appear[1])
            speed = _num(fn, "appear の速さ（度/秒）", appear[2], 0, 100000)
            if speed <= 0:
                raise ValueError(f"{fn}: appear の速さは 0 より大きくしてください")
            return ["wave", list(origin), speed]
        if isinstance(appear, (tuple, list)) and appear and appear[0] == "staged":
            if len(appear) != 2 or not isinstance(appear[1], (tuple, list)) or not appear[1]:
                raise ValueError(
                    f"{fn}: appear=('staged', [(秒, 件数), …]) の形で指定してください")
            stages = []
            prev_s, prev_n = -1.0, 0
            for k, st in enumerate(appear[1]):
                if not isinstance(st, (tuple, list)) or len(st) != 2:
                    raise ValueError(
                        f"{fn}: appear の段[{k}] は (秒, 件数) の組にしてください: {st!r}")
                s = _time(fn, f"appear の段[{k}] の秒", st[0])
                c = st[1]
                if isinstance(c, bool) or not isinstance(c, int) or c < 1:
                    raise ValueError(
                        f"{fn}: appear の段[{k}] の件数は 1 以上の整数にしてください: {c!r}")
                if s <= prev_s or c <= prev_n:
                    raise ValueError(
                        f"{fn}: appear の段は秒も件数（累積）も増える順に並べてください: 段[{k}]")
                stages.append([s, c])
                prev_s, prev_n = s, c
            if prev_n != n:
                raise ValueError(
                    f"{fn}: appear の最後の段の件数（{prev_n}）は coords の件数（{n}）と"
                    f"同じにしてください（件数は累積）")
            return ["staged", stages]
        raise ValueError(
            f"{fn}: appear は 'at'・('wave', 起点, 度/秒)・('staged', [(秒, 件数), …]) の"
            f"どれかで指定してください: {appear!r}")

    def _count_points(self):
        return sum(len(e["coords"]) for e in self._events if e["kind"] == "points")

    def arc(self, src, dst, *, t, dur=0.8, height=0.15, color="accent", width=3,
            head=True, trail=0.35):
        """src から dst へ大円の弧を引く（t から dur 秒で先頭が進む）。

        弧は単位ベクトルの slerp を sin(πs)×height だけ持ち上げた形（ortho は地球の
        半径の割合、plate は画面上の弦の長さの割合で、弦に垂直に上へ反らせる。plate で弦が
        縦に近い（縦から 30 度以内）ときは地図の内側へ反らせる。plate で上端・下端から出るとき、
        または反りで日付変更線をまたいで反対の端に描かれるときは、そうならないまで反りを縮める）。
        先頭に光の点（head=False で無し）。
        尾は trail（弧の長さの割合）の区間で薄れ、それより後ろは明るさ 0.3 の道すじとして残る。
        plate では日付変更線のところで線を分けて描く（右端から出て左端から続く）。
        ortho で縁の近くを通る弧は height×radius だけ縁の外へ出る（キャンバスはそのぶん広がる）。
        """
        fn = "globe.arc"
        a = _coord(fn, "src", src)
        b = _coord(fn, "dst", dst)
        if _angle(_unit(*a), _unit(*b)) > math.pi - 1e-6:
            raise ValueError(f"{fn}: src と dst が地球の真裏どうしです（大円が1つに決まりません）")
        t = _time(fn, "t", t)
        dur = _dur(fn, "dur", dur)
        height = _num(fn, "height", height, 0, 1)
        col = fk.color(fn, color, self._palette)
        width = _num(fn, "width", width, 1, 24)
        if not isinstance(head, bool):
            raise ValueError(f"{fn}: head は True / False で指定してください: {head!r}")
        trail = _num(fn, "trail", trail, 0, 1)
        self._events.append({"kind": "arc", "src": list(a), "dst": list(b), "t": t,
                             "dur": dur, "height": height, "color": list(col), "width": width,
                             "head": head, "trail": trail})
        return self

    def ripple(self, coord, *, t, dur=1.0, max_deg=8, color="accent"):
        """coord から地表に沿って広がる輪（角度の半径が max_deg 度まで広がりながら薄れる）。"""
        fn = "globe.ripple"
        c = _coord(fn, "coord", coord)
        t = _time(fn, "t", t)
        dur = _dur(fn, "dur", dur)
        max_deg = _num(fn, "max_deg", max_deg, 0.5, 60)
        col = fk.color(fn, color, self._palette)
        self._events.append({"kind": "ripple", "coord": list(c), "t": t, "dur": dur,
                             "max_deg": max_deg, "color": list(col)})
        return self

    def night(self, when, *, dim=0.25, twilight=6):
        """when（UTC の datetime）の昼夜を、夜の側の点を暗くして見せる。

        太陽の真下の点を NOAA の簡略式で求め、太陽の高度が -twilight/2〜+twilight/2 度の
        帯で明るさを 1 → dim へ滑らかに落とす。naive な datetime（tzinfo なし）は ValueError。
        1つの globe に1回だけ。
        """
        fn = "globe.night"
        if not isinstance(when, _dt.datetime):
            raise ValueError(f"{fn}: when は datetime で指定してください: {when!r}")
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError(
                f"{fn}: when に時差がありません（naive な datetime）。"
                f"datetime(2024, 7, 19, 4, 9, tzinfo=timezone.utc) のように UTC で指定してください")
        if any(e["kind"] == "night" for e in self._events):
            raise ValueError(f"{fn}: night は1つの globe に1回だけです")
        dim = _num(fn, "dim", dim, 0, 1)
        twilight = _num(fn, "twilight", twilight, 0, 30)
        utc = when.astimezone(_dt.timezone.utc)
        self._events.append({"kind": "night", "when": utc.isoformat(), "dim": dim,
                             "twilight": twilight})
        return self

    def label(self, coord, text, *, t=0, side="right", dur=0.3):
        """coord の横に札（文字）を出す。裏側へ回ると消える。札どうしが重なると警告。"""
        fn = "globe.label"
        c = _coord(fn, "coord", coord)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{fn}: text は空でない文字列で指定してください: {text!r}")
        if "\n" in text:
            raise ValueError(f"{fn}: text は1行にしてください（改行は使えません）")
        t = _time(fn, "t", t)
        if side not in _LABEL_SIDES:
            raise ValueError(
                f"{fn}: side は {', '.join(_LABEL_SIDES)} のどれかにしてください: {side!r}")
        dur = _dur(fn, "dur", dur)
        self._events.append({"kind": "label", "coord": list(c), "text": text, "t": t,
                             "side": side, "dur": dur})
        return self

    # -- 組み立て --

    def _view(self):
        """最初の向き (緯度, 経度)。plate の view="auto" は、積んだ出来事と陸から中心の経度を
        選ぶ（_auto_center_lon。出来事は足すだけなので、件数が同じ間は前の結果を使う）"""
        v = self._p["view"]
        if v != "auto":
            return v
        memo = self._view_memo
        if memo is None or memo[0] != len(self._events):
            memo = self._view_memo = (len(self._events),
                                      (0.0, _auto_center_lon(self._events, self._land)))
        return memo[1]

    def _spec(self):
        """描画に要るものを固めた辞書（build 後に出来事を足しても動画は変わらない）"""
        p = self._p
        spec = dict(p)
        spec["view"] = self._view()
        spec["palette"] = dict(self._palette)
        spec["land"] = self._land
        events = []
        rng = random.Random(p["seed"])
        for ev in self._events:
            ev = json.loads(json.dumps(ev))
            if ev["kind"] == "points":
                ev["_appear"] = _appear_times(ev, rng)
            events.append(ev)
        spec["events"] = events
        spec["motion"] = _motion_plan(spec)
        # 地球（地図）の箱のキャンバス上の位置と、キャンバスの寸法（余白は build が決める）
        spec["ox"], spec["oy"], spec["CW"], spec["CH"] = 0, 0, p["W"], p["H"]
        night = [e for e in events if e["kind"] == "night"]
        spec["subsolar"] = (_subsolar(_dt.datetime.fromisoformat(night[0]["when"]))
                            if night else None)
        return spec

    def _params(self):
        """鍵に入れる値（framekit.norm にかかる）。絵に効く値だけを入れる（同一出力なら同一鍵）"""
        p = self._p
        ortho = p["projection"] == "ortho"
        land_kind, _data, land_sig = self._land
        used = set()
        if ortho or (land_kind is not None and p["graticule"]):
            used.add("dim")                       # 縁の線・陸があるときの経緯線
        if ortho and p["atmosphere"] > 0:
            used.add("line")                      # 大気
        if any(e["kind"] == "label" or (e["kind"] == "arc" and e["head"]) for e in self._events):
            used.add("fg")                        # 札の字・弧の先頭の芯
        params = {
            "projection": p["projection"], "size": [p["W"], p["H"]],
            "step": p["step"], "dot": p["dot"], "graticule": p["graticule"],
            "colors": {k: list(self._palette[k]) for k in sorted(used)},
            "land": land_sig,
        }
        if land_kind is not None or p["graticule"]:
            params["land_color"] = list(p["land_color"])
        if ortho:
            params.update({"radius": p["R"], "view": list(p["view"]), "limb": p["limb"],
                           "back": p["back"], "atmosphere": p["atmosphere"]})
        else:
            # view="auto" は選んだ経度を入れる（同じ経度を手で書いたのと同じ鍵。選び方を
            # 変えても、選んだ経度が変われば鍵が変わる）
            params["view_lon"] = self._view()[1]
        events = []
        for ev in self._events:
            ev = {k: v for k, v in ev.items() if k != "name"}   # name は絵を変えない
            if ev["kind"] == "points" and ev["appear"][0] == "staged":
                params["seed"] = p["seed"]                     # seed は staged にだけ効く
            if ev["kind"] == "points" and ev["halo"]:
                del ev["halo"]          # 既定（堀と光の輪あり）は halo を足す前と同じ鍵
            if ev["kind"] == "turn" and not ortho:
                # plate の turn が動かすのは中心の経度だけ（緯度は絵を変えない）
                ev = {"kind": "turn", "t": ev["t"], "to_lon": ev["to"][1], "dur": ev["dur"],
                      "easing": ev["easing"]}
            events.append(ev)
        params["events"] = events
        if any(e["kind"] == "label" for e in self._events):
            params["label"] = {"size": p["label_size"]}
        return params

    def _end_time(self, spec):
        end = 0.0
        for ev in spec["events"]:
            k = ev["kind"]
            if k == "turn":
                end = max(end, ev["t"] + ev["dur"])
            elif k == "spin":
                end = max(end, ev["t1"])
            elif k == "points":
                end = max(end, max(ev["_appear"]) + ev["dur"])
            elif k == "arc":
                end = max(end, ev["t"] + ev["dur"] + (_ARC_HEAD_FADE if ev["head"] else 0))
            elif k in ("ripple", "label"):
                end = max(end, ev["t"] + ev["dur"])
        return end

    def _sprites(self, spec):
        """札の文字の絵（framekit.label。text_image と同じ描き方・縁取りつき）"""
        fs = self._font_spec
        out = {}
        border = max(2, int(round(spec["label_size"] / 10.0)))
        for k, ev in enumerate(spec["events"]):
            if ev["kind"] != "label":
                continue
            fmt = dict(size=spec["label_size"], font=fs["font"], weight=fs["weight"],
                       color=tuple(spec["palette"]["fg"]), border=border, border_color="black")
            try:
                out[k] = fk.label("globe.label", ev["text"], **fmt)
            except ValueError:
                # framekit.label の豆腐の案内（missing= や区間ごとのフォント）は globe.label では
                # 使えないので、globe で直せる形の案内に言い換える（豆腐以外の ValueError はそのまま）
                meta = fk.label("globe.label", ev["text"], missing="ignore", **fmt).meta
                if not meta["missing"]:
                    raise
                chars = "、".join(f"'{ch}'(U+{ord(ch):04X})"
                                 for ch in dict.fromkeys(meta["missing"]))
                font = os.path.basename(str(meta["font"]))
                raise ValueError(
                    f"globe.label: 札「{ev['text']}」の字 {chars} が札のフォント（{font}）に無く、"
                    f"豆腐（□）で描かれます。globe(font=...) でその字を持つフォントを指定するか、"
                    f"札の文字からその字を除いてください") from None
        return out

    def build(self, duration=None):
        """出来事を積んだ地球を、1本の透過動画 Object にする（framekit.build）。

        duration: 秒。省略時は最後の出来事が終わるまで（出来事が無ければ1コマ）。
          time() でそれより長く表示すると最後のコマが残る。
        戻り値の Object の obj.figure に次が付く:
          figure.xy(coord, t)  … t 秒の (x, y)（キャンバスの px）。裏側なら None。
                                 coord に points の name を渡すと、その組の位置の一覧
          figure.subsolar      … night を積んだときの太陽の真下の点 (緯度, 経度)。無ければ None
          figure.view          … 最初の向き (緯度, 経度)（plate の view="auto" が選んだ中心の経度）
          figure.center / figure.radius … キャンバス上の地球の中心と半径（plate は None）
          figure.size          … キャンバスの (幅, 高さ)
          figure.margin        … 地球（地図）の箱の外に足した余白 (横, 縦) px
        キャンバスは絵の外接矩形: 地球（地図）の箱に、縁の外へ出る弧・大気の光・札が
        切れないだけの余白を上下・左右に対称に足す（地球は真ん中のまま。
        move(anchor="center") で置いた位置は余白があっても変わらない）。
        """
        fn = "globe.build"
        spec = self._spec()
        fps_frac = _fps_fraction(_resolve_fps(fn, None))
        spec["fps"] = fps_frac
        total = self._end_time(spec) if duration is None else _dur(fn, "duration", duration)
        n_frames = fk.n_frames_for(total, fps_frac) if total > 0 else 1
        sprites = self._sprites(spec)
        _set_margin(spec, n_frames, sprites)
        if sprites:
            _check_labels(spec, n_frames, sprites)
        renderer = _Renderer(spec, sprites)
        info = SimpleNamespace(
            xy=_XY(spec), subsolar=spec["subsolar"], view=tuple(spec["view"]),
            size=(spec["CW"], spec["CH"]),
            center=_center(spec) if spec["projection"] == "ortho" else None,
            radius=spec["R"], projection=spec["projection"], margin=(spec["ox"], spec["oy"]))
        land_kind, land_data, _sig = self._land
        return fk.build(
            fn, kind="globe", ver=_GLOBE_VER, params=self._params(), draw=renderer.frame,
            n_frames=n_frames, size=(spec["CW"], spec["CH"]),
            fonts=list(sprites.values()),
            files=[land_data] if land_kind == "png" else (),
            text=fk.text_meta(list(sprites.values()), width=spec["CW"], height=spec["CH"])
            if sprites else None,
            info=info)


def _appear_times(ev, rng):
    """points の各点が点く時刻（秒）"""
    t = ev["t"]
    app = ev["appear"]
    if app[0] == "at":
        return [t] * len(ev["coords"])
    if app[0] == "wave":
        o = _unit(*app[1])
        speed = app[2]
        return [t + math.degrees(_angle(o, _unit(*c))) / speed for c in ev["coords"]]
    stages = app[1]
    times = []
    prev_n = 0
    for k, (sec, n) in enumerate(stages):
        nxt = stages[k + 1][0] if k + 1 < len(stages) else None
        spread = _STAGED_SPREAD if nxt is None else min(_STAGED_SPREAD, (nxt - sec) * 0.5)
        for _ in range(prev_n, n):
            times.append(t + sec + rng.random() * spread)
        prev_n = n
    return times


def _motion_plan(spec):
    """回転の出来事を時刻順に並べ、(始めの向き, [(種類, t0, t1, 始め, 終わり, 補足), …])。

    ortho の向きは四元数（地球 → 画面）、plate は中心の経度。
    """
    ortho = spec["projection"] == "ortho"
    lat0, lon0 = spec["view"]
    state = _quat_from_matrix(_view_matrix(lat0, lon0)) if ortho else lon0
    initial = state
    segs = []
    motions = [e for e in spec["events"] if e["kind"] in ("turn", "spin")]
    motions.sort(key=lambda e: e["t"] if e["kind"] == "turn" else e["t0"])
    for ev in motions:
        if ev["kind"] == "turn":
            t0, t1 = ev["t"], ev["t"] + ev["dur"]
            if ortho:
                end = _quat_from_matrix(_view_matrix(*ev["to"]))
                if sum(a * b for a, b in zip(state, end)) < 0:
                    end = tuple(-c for c in end)
            else:
                end = state + _wrap_lon(ev["to"][1] - state)
            segs.append(("turn", t0, t1, state, end, ev["easing"]))
        else:
            t0, t1 = ev["t0"], ev["t1"]
            if ortho:
                end = _quat_mul(state, _quat_z(math.radians(ev["dps"] * (t1 - t0))))
            else:
                end = state - ev["dps"] * (t1 - t0)
            segs.append(("spin", t0, t1, state, end, ev["dps"]))
        state = end
    return initial, segs


def _orient_at(spec, t):
    """t 秒の向き（ortho は四元数、plate は中心の経度）"""
    ortho = spec["projection"] == "ortho"
    cur, segs = spec["motion"]
    for kind, t0, t1, start, end, extra in segs:
        if t < t0:
            return cur
        if t >= t1:
            cur = end
            continue
        if kind == "turn":
            s = _ease(extra, (t - t0) / (t1 - t0))
            if ortho:
                return _slerp(start, end, s)
            return start + (end - start) * s
        if ortho:
            return _quat_mul(start, _quat_z(math.radians(extra * (t - t0))))
        return start - extra * (t - t0)
    return cur


def _rest_orients(spec, t):
    """t 秒以降に地図（地球）が取る「止まった」向き: t の向きと、t より後に終わる回転の
    終わりの向き（回転の途中は端が動き続けるので含めない）。plate の弧の反りを決めるのに使う"""
    out = [_orient_at(spec, t)]
    for _kind, _t0, t1, _start, end, _extra in spec["motion"][1]:
        if t1 > t:
            out.append(end)
    return out


def _center(spec):
    """地球（地図）の中心のキャンバス座標"""
    return spec["ox"] + spec["W"] / 2.0, spec["oy"] + spec["H"] / 2.0


def _project_one(spec, orient, lat, lon):
    """1点の (x, y, z)（キャンバスの px と視線方向の成分）。plate の z は常に 1"""
    cx, cy = _center(spec)
    if spec["projection"] == "ortho":
        c = _apply(_matrix_from_quat(orient), _unit(lat, lon))
        r = spec["R"]
        return cx + r * c[0], cy - r * c[1], c[2]
    w, h = spec["W"], spec["H"]
    return cx + _wrap_lon(lon - orient) * w / 360.0, spec["oy"] + (90.0 - lat) * h / 180.0, 1.0


class _XY:
    """build() の戻り値の figure.xy(coord, t)。キャンバスの px を返す（裏側なら None）"""

    def __init__(self, spec):
        self._spec = spec

    def __call__(self, coord, t=0):
        spec = self._spec
        t = _time("globe.xy", "t", t)
        orient = _orient_at(spec, t)
        if isinstance(coord, str):
            for ev in spec["events"]:
                if ev["kind"] == "points" and ev.get("name") == coord:
                    return [self._one(orient, c) for c in ev["coords"]]
            names = [e["name"] for e in spec["events"] if e["kind"] == "points" and e.get("name")]
            raise ValueError(f"globe.xy: '{coord}' という名前の points はありません"
                             f"（ある名前: {', '.join(names) or 'なし'}）")
        lat, lon = _coord("globe.xy", "coord", coord)
        return self._one(orient, (lat, lon))

    def _one(self, orient, c):
        x, y, z = _project_one(self._spec, orient, c[0], c[1])
        if z <= 0:
            return None
        return (x, y)


# --- 余白（キャンバスを絵の外接矩形まで広げる）---

def _arc_extent(spec, ev, n_frames):
    """ortho: 持ち上げた弧の見える部分の外接矩形（余白なしの座標）。無ければ None"""
    a, b = _unit(*ev["src"]), _unit(*ev["dst"])
    om = _angle(a, b)
    pts = []
    for k in range(65):
        s = k / 64.0
        if om < 1e-9:
            p = a
        else:
            w0, w1 = math.sin((1 - s) * om) / math.sin(om), math.sin(s * om) / math.sin(om)
            p = tuple(w0 * u + w1 * v for u, v in zip(a, b))
        lift = 1.0 + ev["height"] * math.sin(math.pi * s)
        pts.append(tuple(c * lift for c in p))
    R, W, H = spec["R"], spec["W"], spec["H"]
    pad = ev["width"] / 2.0 + 2.0 + (max(3.0, ev["width"] * 3.0) if ev["head"] else 0.0)
    box = None
    i0 = min(n_frames - 1, max(0, int(math.floor(ev["t"] * float(spec["fps"])))))
    step = max(1, (n_frames - i0) // 48)
    frames = list(range(i0, n_frames, step)) + [n_frames - 1]
    for i in frames:
        M = _matrix_from_quat(_orient_at(spec, float(i / spec["fps"])))
        for p in pts:
            c = _apply(M, p)
            if c[2] < 0 and c[0] * c[0] + c[1] * c[1] < 1.0:
                continue                       # 地球に隠れる
            x, y = W / 2.0 + R * c[0], H / 2.0 - R * c[1]
            b4 = (x - pad, y - pad, x + pad, y + pad)
            box = b4 if box is None else (min(box[0], b4[0]), min(box[1], b4[1]),
                                          max(box[2], b4[2]), max(box[3], b4[3]))
    return box


def _set_margin(spec, n_frames, sprites):
    """札・縁の外へ出る弧・大気がキャンバスで切れないよう、上下・左右に対称な余白を足す。

    地球（地図）の箱は真ん中のまま（move(anchor='center') で置いた位置は変わらない）。
    余白は spec の ox / oy（地球の箱の左上）と CW / CH（キャンバスの寸法）に入る。
    plate の地図は箱で切るので、余白に出るのは札だけ。キャンバスは 4096px まで。
    """
    W, H = spec["W"], spec["H"]
    x0, y0, x1, y1 = 0.0, 0.0, float(W), float(H)

    def grow(b):
        nonlocal x0, y0, x1, y1
        if b is not None:
            x0, y0, x1, y1 = min(x0, b[0]), min(y0, b[1]), max(x1, b[2]), max(y1, b[3])

    if spec["projection"] == "ortho":
        R = spec["R"]
        if spec["atmosphere"] > 0:
            # 大気の光が 0.5/255 まで落ちる距離
            ext = R + 0.05 * R * math.log(max(1.0, spec["atmosphere"] * 510.0))
            grow((W / 2.0 - ext, H / 2.0 - ext, W / 2.0 + ext, H / 2.0 + ext))
        for ev in spec["events"]:
            if ev["kind"] == "arc":
                grow(_arc_extent(spec, ev, n_frames))
    for k, sp in sprites.items():
        ev = spec["events"][k]
        step = max(1, n_frames // 96)
        for i in list(range(0, n_frames, step)) + [n_frames - 1]:
            t = float(i / spec["fps"])
            orient = _orient_at(spec, t)
            x, y, z = _project_one(spec, orient, *ev["coord"])
            if _label_alpha(spec, ev, t, z) <= 1e-3:
                continue
            bx, by = _label_box(spec, ev, sp, x, y)
            grow((bx - 1, by - 1, bx + sp.w + 1, by + sp.h + 1))
    need_x = int(math.ceil(max(0.0, -x0, x1 - W)))
    need_y = int(math.ceil(max(0.0, -y0, y1 - H)))
    mx = min(need_x, max(0, (_SIZE_MAX - W) // 2))
    my = min(need_y, max(0, (_SIZE_MAX - H) // 2))
    if (mx, my) != (need_x, need_y):
        # 黙って切らない（札は _check_labels も警告するが、弧と大気の光はここでしか分からない）
        _warn(current_project(),
              f"globe.build: 縁の外へ出る弧・大気の光・札が、キャンバスの上限 {_SIZE_MAX}px に"
              f"収まらず端で切れます（余白が横 {need_x - mx}px・縦 {need_y - my}px 足りません）。"
              f"size か radius を小さくするか、arc の height・atmosphere を下げてください")
    spec["ox"], spec["oy"] = mx, my
    spec["CW"], spec["CH"] = W + 2 * mx, H + 2 * my


# --- 札 ---

def _label_box(spec, ev, sprite, x, y):
    """札の絵の左上 (x, y)。点 (x, y) から side の向きへ少し離して置く"""
    gap = 0.3 * spec["label_size"] + 4
    w, h = sprite.w, sprite.h
    side = ev["side"]
    if side == "right":
        return x + gap, y - h / 2.0
    if side == "left":
        return x - gap - w, y - h / 2.0
    if side == "top":
        return x - w / 2.0, y - gap - h
    return x - w / 2.0, y + gap


def _label_alpha(spec, ev, t, z):
    if t < ev["t"]:
        return 0.0
    a = _ease("ease_out_cubic", (t - ev["t"]) / ev["dur"])
    if spec["projection"] == "ortho":
        a *= _smooth(_LABEL_FADE_Z[0], _LABEL_FADE_Z[1], z)
    return a


def _check_labels(spec, n_frames, sprites):
    """札の重なりとはみ出しを全コマで確かめる（build のとき。警告だけ）"""
    proj = current_project()
    warned = set()
    for i in range(n_frames):
        t = float(i / spec["fps"])
        orient = _orient_at(spec, t)
        live = []
        for k, sp in sprites.items():
            ev = spec["events"][k]
            x, y, z = _project_one(spec, orient, *ev["coord"])
            if _label_alpha(spec, ev, t, z) < 0.5:
                continue
            bx, by = _label_box(spec, ev, sp, x, y)
            live.append((k, (bx, by, bx + sp.w, by + sp.h)))
        for k, (x0, y0, x1, y1) in live:
            if (x0 < 0 or y0 < 0 or x1 > spec["CW"] or y1 > spec["CH"]) and ("out", k) not in warned:
                warned.add(("out", k))
                _warn(proj, f"globe.label: 札「{spec['events'][k]['text']}」がキャンバスから"
                            f"はみ出しています（t={t:.2f} 秒）。side= を変えるか size を"
                            f"大きくしてください")
        for a in range(len(live)):
            for b in range(a + 1, len(live)):
                ka, ra = live[a]
                kb, rb = live[b]
                if (ra[0] < rb[2] and rb[0] < ra[2] and ra[1] < rb[3] and rb[1] < ra[3]
                        and (ka, kb) not in warned):
                    warned.add((ka, kb))
                    _warn(proj, f"globe.label: 札「{spec['events'][ka]['text']}」と"
                                f"「{spec['events'][kb]['text']}」が重なっています"
                                f"（t={t:.2f} 秒）。side= を変えるか、時刻をずらしてください")


# --- 描画（numpy。framekit の部品で描く）---

def _fib_sphere(np, n):
    """Fibonacci 球の n 点（単位ベクトル (n, 3)。等面積で並ぶ）"""
    i = np.arange(n, dtype=np.float64)
    z = 1.0 - (2.0 * i + 1.0) / n
    r = np.sqrt(np.maximum(0.0, 1.0 - z * z))
    th = i * (math.pi * (3.0 - math.sqrt(5.0)))
    return np.stack([r * np.cos(th), r * np.sin(th), z], axis=1)


def _units(np, lat, lon):
    la = np.radians(lat)
    lo = np.radians(lon)
    c = np.cos(la)
    return np.stack([c * np.cos(lo), c * np.sin(lo), np.sin(la)], axis=1)


def _mask_lookup(np, mask, lat, lon):
    """正距円筒のマスク（(h, w) bool）を (緯度, 経度) の配列で引く"""
    mh, mw = mask.shape
    row = np.clip(np.floor((90.0 - lat) / 180.0 * mh).astype(np.int64), 0, mh - 1)
    col = np.floor((lon + 180.0) / 360.0 * mw).astype(np.int64) % mw
    return mask[row, col]


def _land_mask(np, land):
    """陸地の指定 → 正距円筒の bool マスク（無ければ None）"""
    kind, data, _sig = land
    if kind is None:
        return None
    d = fk.need("globe")
    if kind == "png":
        with d.Image.open(data) as im:
            arr = np.asarray(im.convert("L"))
        return arr > 127
    return np.asarray(_poly_image(d, data)) > 127


def _graticule_latlon(np, spacing, step, plate):
    """経緯線の上の点（緯度, 経度）。点の間隔はおよそ step 度"""
    lats, lons = [], []
    k = int(math.floor(90.0 / spacing))
    par = [j * spacing for j in range(-k, k + 1) if abs(j * spacing) < 90.0 - 1e-9]
    for la in par:
        c = 1.0 if plate else max(math.cos(math.radians(la)), 1e-6)
        n = max(8, int(round(360.0 * c / step)))
        lons.append(-180.0 + (np.arange(n) + 0.5) * 360.0 / n)
        lats.append(np.full(n, la))
    top = max(par) if par else 0.0
    n = max(4, int(round(2 * top / step)))
    la_m = np.linspace(-top, top, n + 1)
    if par:
        # 緯線と重なる所は緯線の点に任せる（交点だけ明るくならない）
        keep = np.min(np.abs(la_m[:, None] - np.array(par)[None, :]), axis=1) > step * 0.5
        la_m = la_m[keep]
    meridians = sorted({_wrap_lon(j * spacing) for j in range(int(round(360.0 / spacing)))})
    for lo in meridians:
        lats.append(la_m)
        lons.append(np.full(len(la_m), lo))
    if not plate:
        lats.append(np.array([90.0, -90.0]))
        lons.append(np.array([0.0, 0.0]))
    return np.concatenate(lats), np.concatenate(lons)


def _runs(mask):
    """bool 列の True が続く区間 [(開始, 終わり+1), …]"""
    runs = []
    start = None
    for i, m in enumerate(mask):
        if m and start is None:
            start = i
        elif not m and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


def _split_wrapped(np, xs, ys, als, W):
    """横につながった（経度を unwrap した）折れ線を、キャンバスの左右の端で分ける。

    日付変更線（キャンバスの端）をまたぐ所で線を切り、端の位置に補間した点を
    両側に足す（右端から出た線が左端から続く）。戻り値: [(xs, ys, als), …]
    """
    k = np.floor(xs / W).astype(np.int64)
    pieces = []
    start = 0
    n = len(xs)
    for i in range(1, n + 1):
        if i < n and k[i] == k[i - 1]:
            continue
        sx = list(xs[start:i] - k[start] * W)
        sy = list(ys[start:i])
        sa = list(als[start:i])
        if start > 0:
            xa, xb = xs[start - 1], xs[start]
            edge = max(k[start - 1], k[start]) * W
            u = (edge - xa) / (xb - xa)
            sx.insert(0, edge - k[start] * W)
            sy.insert(0, ys[start - 1] + (ys[start] - ys[start - 1]) * u)
            sa.insert(0, als[start - 1] + (als[start] - als[start - 1]) * u)
        if i < n:
            xa, xb = xs[i - 1], xs[i]
            edge = max(k[i - 1], k[i]) * W
            u = (edge - xa) / (xb - xa)
            sx.append(edge - k[start] * W)
            sy.append(ys[i - 1] + (ys[i] - ys[i - 1]) * u)
            sa.append(als[i - 1] + (als[i] - als[i - 1]) * u)
        pieces.append((np.array(sx), np.array(sy), np.array(sa)))
        start = i
    return pieces


def _rgba_arr(np, col, alpha):
    """色 (r, g, b, a)（0〜255）と点ごとの不透明度（0〜1）→ framekit.dots の (N, 4)"""
    out = np.empty((len(alpha), 4), np.float32)
    out[:, 0], out[:, 1], out[:, 2] = col[0], col[1], col[2]
    out[:, 3] = np.clip(alpha, 0.0, 1.0) * col[3]
    return out


def _paint_cov(np, dst, cov, x0, y0, col):
    """被覆率 cov（(h, w) の float32、0..1）を、dst の (x0, y0) を左上として色 col
    （ストレートの (r, g, b, a)、0..255）で over する（cov は dst の内側に収まっていること）。

    framekit の canvas の公開の形（float32・事前乗算の HxWx4、0..1）だけに依存する
    （framekit の内部関数は使わない）。塗る画素が少ない（細い線）ときは触れた画素だけを更新する。
    """
    a = col[3] / 255.0
    if a <= 0:
        return
    prem = np.array([col[0] / 255.0 * a, col[1] / 255.0 * a, col[2] / 255.0 * a, a], np.float32)
    flat = np.flatnonzero(cov)
    if len(flat) == 0:
        return
    h, w = cov.shape
    dp = dst[y0:y0 + h, x0:x0 + w]
    if len(flat) < cov.size // 4:
        iy, ix = np.divmod(flat, w)
        c = cov[iy, ix][:, None]
        dp[iy, ix] = dp[iy, ix] * (1.0 - c * prem[3]) + c * prem
    else:
        c = cov[..., None]
        dp *= 1.0 - c * prem[3]
        dp += c * prem


def _disc_cov(np, dst, xs, ys, reach, cov_of):
    """円ごとの近くの窓で被覆率を出し、画素ごとの max でまとめる。

    戻り値: (画素の通し番号（dst を (H·W, 4) に並べた行。昇順・重複なし）, 被覆率 float32)。
    キャンバスに触れなければ None。reach は円ごとの「中心から描く範囲の半径」（px）。
    cov_of(sel, dist) は sel（円の番号の配列）の円たちの窓の被覆率を返す（dist は窓の画素の
    中心からの距離 (m, K, K)、戻り値も同じ形）。窓の一辺 K は円ごとの reach で決め、同じ K の
    円をまとめて計算する（点く途中の大きい点に、ほかの点の窓が引きずられない）。

    メモリは点の数によらず抑える: 1回に計算する「円の数 × 窓の画素」は _WINDOW_CHUNK まで。
    全部が1回に収まる（ふつうの数の点）なら触れた画素だけを並べて max を取り、収まらなければ
    円たちの外接矩形の層（float32。キャンバス以下）へ分けて max で積む（以前は全部の円の窓を
    一度に作って np.unique で並べ替えており、2 万点で 1 コマ 1.5GB・1.9 秒かかった）。
    max は分け方によらないので、どちらの経路も同じ画素になる。
    """
    H, W = dst.shape[:2]
    pad = np.asarray(reach, np.float64) + 1.0
    bx0 = max(0, int(math.floor(float((xs - pad).min()))))
    by0 = max(0, int(math.floor(float((ys - pad).min()))))
    bx1 = min(W, int(math.ceil(float((xs + pad).max()))))
    by1 = min(H, int(math.ceil(float((ys + pad).max()))))
    if bx1 <= bx0 or by1 <= by0:
        return None
    ks = np.ceil(2.0 * pad).astype(np.int64) + 2      # 窓の一辺（窓の外の画素は被覆率 0）
    lw = bx1 - bx0

    def chunks():
        """(外接矩形の中の画素の通し番号, 被覆率) を、_WINDOW_CHUNK 以下の計算ごとに返す"""
        for K in np.unique(ks).tolist():
            group = np.flatnonzero(ks == K)
            off = np.arange(K, dtype=np.int64)
            step = max(1, _WINDOW_CHUNK // (K * K))
            for i0 in range(0, len(group), step):
                sel = group[i0:i0 + step]
                gx0 = np.floor(xs[sel] - pad[sel]).astype(np.int64)
                gy0 = np.floor(ys[sel] - pad[sel]).astype(np.int64)
                ix = gx0[:, None, None] + off[None, None, :]
                iy = gy0[:, None, None] + off[None, :, None]
                dx = (ix + 0.5 - xs[sel][:, None, None]).astype(np.float32)
                dy = (iy + 0.5 - ys[sel][:, None, None]).astype(np.float32)
                c = cov_of(sel, np.sqrt(dx * dx + dy * dy))
                ok = (c > 1e-4) & (ix >= bx0) & (ix < bx1) & (iy >= by0) & (iy < by1)
                yield ((iy - by0) * lw + (ix - bx0))[ok], c[ok]

    if int((ks * ks).sum()) <= _WINDOW_CHUNK:
        parts = list(chunks())
        rel = np.concatenate([p[0] for p in parts])
        if len(rel) == 0:
            return None
        rel, inv = np.unique(rel, return_inverse=True)
        cmax = np.zeros(len(rel), np.float32)
        np.maximum.at(cmax, inv.reshape(-1), np.concatenate([p[1] for p in parts]))
    else:
        layer = np.zeros((by1 - by0) * lw, np.float32)
        for rel, c in chunks():
            np.maximum.at(layer, rel, c)
        rel = np.flatnonzero(layer)
        if len(rel) == 0:
            return None
        cmax = layer[rel]
    iy, ix = np.divmod(rel, lw)
    return (iy + by0) * W + (ix + bx0), cmax


def _knockout(np, dst, xs, ys, rs, amt):
    """円（中心 (xs, ys)・半径 rs）の中を、不透明度 amt（0..1）の割合だけ透明へ抜く。

    事前乗算の canvas なので画素の4成分に (1 − 被覆率 × amt) を掛ける（destination-out）。
    被覆率は距離から出す clip(r + 0.5 − 距離, 0, 1) の AA、円どうしの重なりは max（二重に
    抜けない）。円ごとにその近くの小さな窓の画素だけを計算し（_disc_cov。円の数によらず
    メモリは一定）、触れた画素だけを書き換える（点が地図じゅうに散っても、外接矩形の全面を
    掛け直さない）。framekit の公開の形（float32・事前乗算の HxWx4）だけに依存する。
    """
    xs, ys, rs, amt = (np.asarray(v, np.float64) for v in (xs, ys, rs, amt))
    amt = np.clip(amt, 0.0, 1.0)
    ok = (amt > 1e-3) & (rs > 0)
    if not ok.any():
        return
    xs, ys, rs, amt = xs[ok], ys[ok], rs[ok], amt[ok]
    r5 = (rs + 0.5).astype(np.float32)
    a32 = amt.astype(np.float32)

    def cov_of(sel, dist):
        return np.clip(r5[sel][:, None, None] - dist, 0.0, 1.0) * a32[sel][:, None, None]

    got = _disc_cov(np, dst, xs, ys, rs, cov_of)
    if got is None:
        return
    idx, cmax = got
    flat = dst.reshape(-1, 4)
    flat[idx] *= (1.0 - cmax)[:, None]


def _glow(np, dst, xs, ys, r_in, r_out, amt, col):
    """円の光の輪: 中心から r_in までは amt、そこから r_out まで (1 − u)² で 0 へ落ちる
    被覆率で、色 col（ストレートの (r, g, b, a)、0..255）を over する。

    重なりは max（光が二重に濃くならない）。_knockout と同じく、円の近くの窓で触れた画素だけを
    書き換える（_disc_cov。framekit.dots の大きくぼかした円は点の外接矩形の全面を計算するので、
    地図じゅうに散った点では1回 15ms ほどかかった）。
    """
    xs, ys, r_in, r_out, amt = (np.asarray(v, np.float64) for v in (xs, ys, r_in, r_out, amt))
    a = col[3] / 255.0
    ok = (amt > 1e-3) & (r_out > r_in)
    if a <= 0 or not ok.any():
        return
    xs, ys, r_in, r_out, amt = xs[ok], ys[ok], r_in[ok], r_out[ok], amt[ok]
    ri = r_in.astype(np.float32)
    ro = r_out.astype(np.float32)
    a32 = amt.astype(np.float32)

    def cov_of(sel, dist):
        i, o = ri[sel][:, None, None], ro[sel][:, None, None]
        u = np.clip((dist - i) / (o - i), 0.0, 1.0)
        return (1.0 - u) ** 2 * a32[sel][:, None, None]

    got = _disc_cov(np, dst, xs, ys, r_out, cov_of)
    if got is None:
        return
    idx, cmax = got
    prem = np.array([col[0] / 255.0 * a, col[1] / 255.0 * a, col[2] / 255.0 * a, a], np.float32)
    flat = dst.reshape(-1, 4)
    c = cmax[:, None]
    flat[idx] = flat[idx] * (1.0 - c * prem[3]) + c * prem


def _fade_line(np, dst, xs, ys, als, col, width):
    """折れ線を、線に沿って不透明度が変わる形で描く（弧の尾が薄れる）。

    framekit.polyline と同じ太さ・丸い端と継ぎ目の線を、距離から出す被覆率
    clip(幅/2 + 0.5 − 距離, 0, 1) の AA で描き、線分の上での位置で頂点の不透明度を補間した値を
    掛ける。線分どうしは max で重ねる（継ぎ目が二重に濃くならず、濃淡に段がつかない）。
    長い線分（plate で極を通る弧の、経度が飛ぶ所など）は _FADE_PIECE px 以下に切り
    （頂点の不透明度は線形に補間する）、線分ごとにその近くの小さな窓の画素だけを計算する
    （窓の一辺は「切った長さ + 太さ」で抑えられ、メモリは線の長さに比例する）。
    不透明度が一定なら framekit.polyline そのもの。

    framekit.polyline に頂点ごとの不透明度の引数が無いので自前で描く。framekit の内部関数
    （被覆率の層など）は使わず、canvas の公開の形へ _paint_cov で直接 over する。
    """
    if len(xs) < 2:
        return
    xs = np.asarray(xs, np.float64)
    ys = np.asarray(ys, np.float64)
    als = np.clip(np.asarray(als, np.float64), 0.0, 1.0)
    if float(als.max()) - float(als.min()) < 1e-6:
        if als[0] > 0:
            fk.polyline(dst, np.stack([xs, ys], axis=1),
                        (col[0], col[1], col[2], col[3] * float(als[0])), width)
        return
    hw = width / 2.0
    pad = hw + 1.0
    H, W = dst.shape[:2]
    bx0 = max(0, int(math.floor(float(xs.min()) - pad)))
    by0 = max(0, int(math.floor(float(ys.min()) - pad)))
    bx1 = min(W, int(math.ceil(float(xs.max()) + pad)))
    by1 = min(H, int(math.ceil(float(ys.max()) + pad)))
    if bx1 <= bx0 or by1 <= by0:
        return
    # 長い線分を _FADE_PIECE px 以下に切る
    seg = np.hypot(np.diff(xs), np.diff(ys))
    m = np.maximum(1, np.ceil(seg / _FADE_PIECE)).astype(np.int64)
    if int(m.max()) > 1:
        idx = np.repeat(np.arange(len(seg)), m)
        frac = (np.arange(int(m.sum())) - np.repeat(np.cumsum(m) - m, m)) / np.repeat(m, m)
        xs = np.r_[xs[idx] + (xs[idx + 1] - xs[idx]) * frac, xs[-1]]
        ys = np.r_[ys[idx] + (ys[idx + 1] - ys[idx]) * frac, ys[-1]]
        als = np.r_[als[idx] + (als[idx + 1] - als[idx]) * frac, als[-1]]
        seg = np.hypot(np.diff(xs), np.diff(ys))
    lw, lh = bx1 - bx0, by1 - by0
    cov = np.zeros((lh, lw), np.float32)
    flat = cov.reshape(-1)
    K = int(math.ceil(float(seg.max()) + 2 * pad)) + 2
    off = np.arange(K, dtype=np.int64)
    n = len(seg)
    chunk = max(1, _WINDOW_CHUNK // (K * K))
    for i0 in range(0, n, chunk):
        sl = slice(i0, min(n, i0 + chunk))
        ax, ay, bx, by = xs[:-1][sl], ys[:-1][sl], xs[1:][sl], ys[1:][sl]
        gx0 = np.floor(np.minimum(ax, bx) - pad).astype(np.int64)
        gy0 = np.floor(np.minimum(ay, by) - pad).astype(np.int64)
        ix = gx0[:, None, None] + off[None, None, :]
        iy = gy0[:, None, None] + off[None, :, None]
        px = (ix + 0.5 - ax[:, None, None]).astype(np.float32)
        py = (iy + 0.5 - ay[:, None, None]).astype(np.float32)
        ex = (bx - ax).astype(np.float32)[:, None, None]
        ey = (by - ay).astype(np.float32)[:, None, None]
        el2 = ex * ex + ey * ey
        t = np.clip((px * ex + py * ey) / np.where(el2 > 1e-12, el2, np.float32(1.0)), 0.0, 1.0)
        qx, qy = px - t * ex, py - t * ey
        c = np.clip(np.float32(hw + 0.5) - np.sqrt(qx * qx + qy * qy), 0.0, 1.0)
        a0 = als[:-1][sl].astype(np.float32)[:, None, None]
        a1 = als[1:][sl].astype(np.float32)[:, None, None]
        c *= a0 + (a1 - a0) * t
        ok = (c > 1e-4) & (ix >= bx0) & (ix < bx1) & (iy >= by0) & (iy < by1)
        np.maximum.at(flat, ((iy - by0) * lw + (ix - bx0))[ok], c[ok])
    _paint_cov(np, dst, cov, bx0, by0, col)


class _Renderer:
    """spec（固めた出来事）からコマを描く。framekit.build の draw(i) になる"""

    def __init__(self, spec, sprites):
        self.spec = spec
        self.sprites = sprites
        self.W, self.H = spec["W"], spec["H"]          # 地球（地図）の箱
        self.CW, self.CH = spec["CW"], spec["CH"]      # キャンバス（余白込み）
        self.ox, self.oy = spec["ox"], spec["oy"]
        self.cx, self.cy = _center(spec)
        self._ready = False

    # -- 準備（最初のコマで1回）--

    def _prepare(self):
        np = self.np = fk.need("globe").np
        spec = self.spec
        pal = spec["palette"]
        plate = spec["projection"] == "plate"
        mask = _land_mask(np, spec["land"])
        step = spec["step"]
        layers = []    # (単位ベクトル (n,3), 緯度, 経度, 色, 不透明度)
        grat = spec["graticule"]
        if grat:
            glat, glon = _graticule_latlon(np, grat, step, plate)
            if mask is not None:
                layers.append((_units(np, glat, glon), glat, glon, pal["dim"], 1.0))
            else:
                layers.append((_units(np, glat, glon), glat, glon, spec["land_color"],
                               _LAND_ALPHA))
        if mask is not None:
            if plate:
                nx, ny = _plate_grid(step)
                gx, gy = np.meshgrid(np.arange(nx), np.arange(ny))
                lon = -180.0 + (gx.ravel() + 0.5) * 360.0 / nx
                lat = 90.0 - (gy.ravel() + 0.5) * 180.0 / ny
                sel = _mask_lookup(np, mask, lat, lon)
                lat, lon = lat[sel], lon[sel]
                v = _units(np, lat, lon)
            else:
                v = _fib_sphere(np, _fib_count(step))
                lat = np.degrees(np.arcsin(np.clip(v[:, 2], -1, 1)))
                lon = np.degrees(np.arctan2(v[:, 1], v[:, 0]))
                sel = _mask_lookup(np, mask, lat, lon)
                v, lat, lon = v[sel], lat[sel], lon[sel]
            layers.append((v, lat, lon, spec["land_color"], _LAND_ALPHA))
        # 夜の側の明るさ（地球に固定なので最初に1回だけ）
        sub = spec["subsolar"]
        night = [e for e in spec["events"] if e["kind"] == "night"]
        self.layers = []
        for v, lat, lon, col, alpha in layers:
            f = np.full(len(v), alpha)
            if sub is not None:
                ev = night[0]
                s = np.array(_unit(*sub))
                h = np.degrees(np.arcsin(np.clip(v @ s, -1, 1)))
                tw = max(ev["twilight"], 1e-6)
                u = np.clip((h + tw / 2) / tw, 0, 1)
                f = f * (ev["dim"] + (1 - ev["dim"]) * u * u * (3 - 2 * u))
            self.layers.append({"v": v, "lat": lat, "lon": lon, "col": tuple(col), "a": f})
        # 動かない層（大気・縁）
        static = fk.canvas(self.CW, self.CH)
        if not plate:
            cx, cy, R = self.cx, self.cy, spec["R"]
            if spec["atmosphere"] > 0:
                yy, xx = np.mgrid[0:self.CH, 0:self.CW]
                d = np.hypot(xx + 0.5 - cx, yy + 0.5 - cy)
                glow_w = 0.05 * R
                a = np.where(d >= R, np.exp(-(d - R) / glow_w),
                             0.6 * np.exp(-(R - d) / (0.25 * glow_w))) * spec["atmosphere"]
                _paint_cov(np, static, a.astype(np.float32), 0, 0, pal["line"])
            dim = pal["dim"]
            fk.circle(static, (cx, cy), R, (dim[0], dim[1], dim[2], int(dim[3] * _LIMB_ALPHA)),
                      width=_LIMB_WIDTH)
        static.flags.writeable = False
        self.static = static
        self.base_cache = fk.layer_cache()
        self._ready = True

    # -- 地球の点の層（向きが変わらない間は使い回す）--

    def _base_layer(self, orient, t):
        """地球の点の層（書き込める写し）。回っている間は毎コマ描き直すので、写しを作らない"""
        moving = any(t0 <= t < t1 for _k, t0, t1, *_rest in self.spec["motion"][1])
        if moving:
            dst = self._draw_base(orient)
            dst.flags.writeable = True
            return dst
        key = orient if isinstance(orient, float) else tuple(orient)
        return self.base_cache(key, lambda: self._draw_base(orient)).copy()

    def _draw_base(self, orient):
        np = self.np
        spec = self.spec
        W, H = self.W, self.H
        dst = self.static.copy()
        dot = spec["dot"]
        # 層（経緯線・陸・裏側）の点は色を点ごとに持たせて framekit.dots を1回だけ呼ぶ
        xys, cols = [], []
        if spec["projection"] == "ortho":
            M = np.array(_matrix_from_quat(orient))
            cx, cy, R = self.cx, self.cy, spec["R"]
            cams = [(L, L["v"] @ M.T) for L in self.layers]
            # 縁の近くでは点が視線方向に詰まって（間隔 × z）重なり、足し合わせで白い筋に
            # なる。点が重なり始める z（直径 ÷ 中心での間隔）より縁の側では、詰まった分だけ
            # 不透明度を下げる（球の上に描いた点を斜めから見たときの明るさに揃える）
            z_ov = min(1.0, 2.0 * dot / (R * math.radians(spec["step"])))
            if spec["back"]:
                for L, c in cams:
                    b = c[:, 2] <= 0
                    if b.any():
                        zb = -c[b, 2]
                        al = _BACK_ALPHA * L["a"][b] * np.clip(zb / z_ov, 0.0, 1.0)
                        xys.append(np.stack([cx + R * c[b, 0], cy - R * c[b, 1]], axis=1))
                        cols.append(_rgba_arr(np, L["col"], al))
            for L, c in cams:
                z = c[:, 2]
                f = z > 0
                if not f.any():
                    continue
                zf = z[f]
                br = spec["limb"] + (1 - spec["limb"]) * np.sqrt(zf)
                e = np.clip(zf / _EDGE_FADE_Z, 0, 1)
                br = br * e * e * (3 - 2 * e) * np.clip(zf / z_ov, 0.0, 1.0) * L["a"][f]
                keep = br > 0.5 / 255.0
                xys.append(np.stack([cx + R * c[f, 0][keep], cy - R * c[f, 1][keep]], axis=1))
                cols.append(_rgba_arr(np, L["col"], br[keep]))
        else:
            for L in self.layers:
                xs = self.cx + ((L["lon"] - orient + 180.0) % 360.0 - 180.0) * W / 360.0
                ys = self.oy + (90.0 - L["lat"]) * H / 180.0
                al = L["a"]
                # 端の近くの点は反対側にも描く（端で切れた点が向こうから続く）
                m = dot + 1.5
                lo, hi = xs < self.ox + m, xs > self.ox + W - m
                xs = np.concatenate([xs, xs[lo] + W, xs[hi] - W])
                ys = np.concatenate([ys, ys[lo], ys[hi]])
                al = np.concatenate([al, al[lo], al[hi]])
                xys.append(np.stack([xs, ys], axis=1))
                cols.append(_rgba_arr(np, L["col"], al))
        if xys:
            fk.dots(dst, np.concatenate(xys), np.concatenate(cols), dot)
        dst.flags.writeable = False       # layer_cache に写しを作らせない（コマごとに .copy() する）
        return dst

    # -- 1コマ --

    def frame(self, i):
        """i コマ目（t = i / fps 秒）を framekit の canvas（float32・事前乗算）で返す"""
        if not self._ready:
            self._prepare()
        np = self.np
        spec = self.spec
        t = float(i / spec["fps"])
        orient = _orient_at(spec, t)
        dst = self._base_layer(orient, t)
        M = np.array(_matrix_from_quat(orient)) if spec["projection"] == "ortho" else None
        # 拠点の点: まわりの陸の点を抜き（堀）、淡い光の輪を敷いてから芯を描く。点は半径ごとに
        # まとめて framekit.dots を1回ずつ呼ぶ（呼び出しの手間が点の数より重い）
        self._dot_jobs = {}
        marks = [self._point_marks(ev, t, orient, M)
                 for ev in spec["events"] if ev["kind"] == "points"]
        marks = [m for m in marks if m is not None]
        if marks:
            self._draw_points(dst, marks)
        for ev in spec["events"]:
            if ev["kind"] == "ripple":
                self._draw_ripple(dst, ev, t, orient, M)
        for ev in spec["events"]:
            if ev["kind"] == "arc":
                self._draw_arc(dst, ev, t, orient, M)
        self._flush_dots(dst)                  # 弧の先頭の光の点
        if M is None and (self.ox or self.oy):
            # plate の地図は箱で切る（端で複製した点・線の端が余白へ出ない）。札だけが余白に出る
            dst[:self.oy] = 0
            dst[self.oy + self.H:] = 0
            dst[:, :self.ox] = 0
            dst[:, self.ox + self.W:] = 0
        for k, ev in enumerate(spec["events"]):
            if ev["kind"] == "label":
                self._draw_label(dst, ev, self.sprites[k], t, orient)
        return dst

    def _queue_dots(self, xy, rgba, r, soft=0.8):
        """framekit.dots に渡す点を (半径, ぼかし) ごとに溜める（_flush_dots でまとめて描く）"""
        key = (round(float(r), 3), round(float(soft), 3))
        self._dot_jobs.setdefault(key, []).append((xy, rgba))

    def _flush_dots(self, dst):
        np = self.np
        for (r, soft), items in self._dot_jobs.items():
            xy = np.concatenate([np.asarray(a, np.float64).reshape(-1, 2) for a, _c in items])
            rgba = np.concatenate([np.asarray(c, np.float32).reshape(-1, 4) for _a, c in items])
            fk.dots(dst, xy, rgba, r, soft=soft)
        self._dot_jobs = {}

    def _screen(self, v, orient, M):
        """単位ベクトル (n,3) → (x, y, z)。plate の z は 1"""
        np = self.np
        spec = self.spec
        W, H = self.W, self.H
        if M is not None:
            c = v @ M.T
            R = spec["R"]
            return self.cx + R * c[:, 0], self.cy - R * c[:, 1], c[:, 2]
        lat = np.degrees(np.arcsin(np.clip(v[:, 2], -1, 1)))
        lon = np.degrees(np.arctan2(v[:, 1], v[:, 0]))
        xs = self.cx + ((lon - orient + 180.0) % 360.0 - 180.0) * W / 360.0
        return xs, self.oy + (90.0 - lat) * H / 180.0, np.ones(len(lat))

    def _point_marks(self, ev, t, orient, M):
        """points の出来事の t 秒の点: (xs, ys, 半径, 不透明度 0..1, 色, 堀と光の輪を付けるか)。
        点いた点が無ければ None。

        点く途中は少し大きい所から縮む（半径は 0.25px 刻みにまとめる）。ortho で裏へ回る点は
        縁の手前で薄れる。plate で端の近くの点は反対の端にも置く（端で切れた点が向こうから続く）。
        """
        np = self.np
        app = np.array(ev["_appear"])
        on = t >= app
        if not on.any():
            return None
        coords = np.array(ev["coords"])[on]
        u = np.clip((t - app[on]) / ev["dur"], 0.0, 1.0)
        e = 1 - (1 - u) ** 3                 # ease_out_cubic
        xs, ys, z = self._screen(_units(np, coords[:, 0], coords[:, 1]), orient, M)
        a = e.copy()
        if M is not None:
            a *= np.clip(z / _POINT_FADE_Z, 0, 1)
        r = np.round(ev["radius"] * (1.0 + 0.6 * (1.0 - e)) * 4.0) / 4.0
        if M is None:
            W = self.W
            m = r * _POINT_GLOW_R + 1.5
            lo, hi = xs < self.ox + m, xs > self.ox + W - m
            xs = np.concatenate([xs, xs[lo] + W, xs[hi] - W])
            ys = np.concatenate([ys, ys[lo], ys[hi]])
            a = np.concatenate([a, a[lo], a[hi]])
            r = np.concatenate([r, r[lo], r[hi]])
        keep = a > 1e-3
        if not keep.any():
            return None
        return xs[keep], ys[keep], r[keep], a[keep], ev["color"], ev["halo"]

    def _draw_points(self, dst, marks):
        """拠点の点を描く（marks は _point_marks の戻り値の列）。

        1. 堀: 点の半径 + _POINT_MOAT×dot の円の中の陸の点・経緯線を透明へ抜く（_knockout。
           白い点が同じ白っぽい陸の点に埋もれない。背景が何でも「点のまわりだけ地図が無い」形）
        2. 光の輪: 点の色の淡い光（_glow。点の縁から半径 _POINT_GLOW_R 倍まで (1 − u)² で薄れる）
        3. 芯: 点の色の円
        どれも点く途中の不透明度を掛ける（堀も一緒に開いていく）。points(halo=False) の点は
        1 と 2 を飛ばして芯だけを描く。
        """
        np = self.np
        gap = _POINT_MOAT * self.spec["dot"]
        halo = [m for m in marks if m[5]]
        if halo:
            _knockout(np, dst, np.concatenate([m[0] for m in halo]),
                      np.concatenate([m[1] for m in halo]),
                      np.concatenate([m[2] for m in halo]) + gap,
                      np.concatenate([m[3] for m in halo]))
        for xs, ys, r, a, col, _h in halo:
            _glow(np, dst, xs, ys, r, r * _POINT_GLOW_R, a * _POINT_GLOW_A, col)
        for xs, ys, r, a, col, _h in marks:
            for rr in np.unique(r):
                k = r == rr
                self._queue_dots(np.stack([xs[k], ys[k]], axis=1), _rgba_arr(np, col, a[k]),
                                 float(rr))
        self._flush_dots(dst)

    def _arc_vectors(self, ev, s):
        """弧の s（0〜1 の配列）での大円上の単位ベクトルと、src と dst の角度"""
        np = self.np
        got = ev.get("_ab")
        if got is None:
            a, b = _unit(*ev["src"]), _unit(*ev["dst"])
            got = ev["_ab"] = (np.array(a), np.array(b), _angle(a, b))
        a, b, om = got
        if om < 1e-9:
            return np.repeat(a[None, :], len(s), axis=0), om
        w0 = np.sin((1 - s) * om) / math.sin(om)
        w1 = np.sin(s * om) / math.sin(om)
        return w0[:, None] * a[None, :] + w1[:, None] * b[None, :], om

    def _arc_screen(self, ev, s, M):
        """ortho: 弧の s での画面座標系の位置（持ち上げ込み）(n, 3)"""
        np = self.np
        p, _ = self._arc_vectors(ev, s)
        p = p * (1.0 + ev["height"] * np.sin(math.pi * s))[:, None]
        return p @ M.T

    @staticmethod
    def _arc_hidden(c):
        """持ち上げた弧の点（画面座標系 (n,3)）が地球に隠れるか: z < 0 かつ投影が円の内側"""
        return (c[:, 2] < 0) & (c[:, 0] ** 2 + c[:, 1] ** 2 < 1.0)

    def _arc_edge(self, ev, M, s_hidden, s_vis):
        """隠れる s と見える s のあいだの境目を二分法で求める"""
        ua, ub = _unit(*ev["src"]), _unit(*ev["dst"])
        om = _angle(ua, ub)
        so = math.sin(om)
        Mt = tuple(tuple(float(v) for v in row) for row in M)
        a, b = s_hidden, s_vis
        for _ in range(24):
            m = (a + b) / 2.0
            w0, w1 = math.sin((1 - m) * om) / so, math.sin(m * om) / so
            lift = 1.0 + ev["height"] * math.sin(math.pi * m)
            c = _apply(Mt, tuple((w0 * u + w1 * v) * lift for u, v in zip(ua, ub)))
            if c[2] < 0 and c[0] * c[0] + c[1] * c[1] < 1.0:
                a = m
            else:
                b = m
        return b

    def arc_polylines(self, ev, s_head, orient, M):
        """弧の 0〜s_head の部分を、見える区間ごとの (xs, ys, s) に分けて返す。

        ortho: 地球に隠れる所（z < 0 かつ投影が円の内側）で切り、境目は二分法で詰める
        （線の端が縁や地球の面にぴったり止まる）。plate: 経度を unwrap した1本
        （キャンバスの端での分割は _split_wrapped が受け持つ）。
        """
        np = self.np
        spec = self.spec
        if s_head <= 0:
            return []
        om = self._arc_vectors(ev, np.array([0.0]))[1]
        if M is not None:
            R = spec["R"]
            n = max(16, int(math.ceil(R * om * (1 + ev["height"]) * s_head / 4.0)))
            s = np.linspace(0.0, s_head, n + 1)
            vis = ~self._arc_hidden(self._arc_screen(ev, s, M))
            out = []
            for i0, i1 in _runs(vis):
                ss = list(s[i0:i1])
                if i0 > 0:
                    ss.insert(0, self._arc_edge(ev, M, s[i0 - 1], s[i0]))
                if i1 < len(s):
                    ss.append(self._arc_edge(ev, M, s[i1], s[i1 - 1]))
                ss = np.array(ss)
                cc = self._arc_screen(ev, ss, M)
                out.append((self.cx + R * cc[:, 0], self.cy - R * cc[:, 1], ss))
            return out
        # plate: 大円を経度で unwrap し、弦に垂直に上へ反らせる
        n = max(16, int(math.ceil(om / math.pi * self.W * s_head / 4.0)))
        s = np.linspace(0.0, s_head, n + 1)
        xs, ys = self._plate_path(ev, s, orient)
        nx_, ny_, amp = self._plate_lift(ev)
        if amp > 0:
            lift = amp * np.sin(math.pi * s)
            xs = xs + nx_ * lift
            ys = ys + ny_ * lift
        return [(xs, ys, s)]

    def _plate_path(self, ev, s, orient):
        """plate: 大円の s での画面位置。経度は始点から連続に unwrap する（端をまたいでも跳ばない）"""
        np = self.np
        W, H = self.W, self.H
        # 始点から終点まで細かく取って unwrap し、s の位置を補間する
        # （s が粗い・2点だけのときも同じ道すじになる）
        fine = np.linspace(0.0, 1.0, 257)
        p, _ = self._arc_vectors(ev, fine)
        lat = np.degrees(np.arcsin(np.clip(p[:, 2], -1, 1)))
        lon = np.degrees(np.unwrap(np.arctan2(p[:, 1], p[:, 0])))
        lon = lon - lon[0] + (_wrap_lon(float(lon[0]) - orient) + orient)
        lat_s = np.interp(s, fine, lat)
        lon_s = np.interp(s, fine, lon)
        return self.cx + (lon_s - orient) * W / 360.0, self.oy + (90.0 - lat_s) * H / 180.0

    def _plate_lift(self, ev):
        """plate の弧の反り: (向き x, 向き y, 振幅 px)。

        向き: 弦（弧全体の始点 → 終点。描きかけでも反りの形が変わらない）に垂直で、画面の上向き。
        弦が縦に近い（縦から 30 度以内）と上向きの成分がほとんど無いので、地図の内側
        （弧の始まりの向きで、弦の中点から地図の中心へ向かう側）へ反らせる。
        振幅: height × 弦の長さ。次を満たすまで縮める:
          - 上端・下端から出ない（極の近くを通る弧が上で切れない）
          - 反らせても、日付変更線（地図の左右の端）をまたぐ回数と順序が反らせる前の
            道すじと同じ（またがない弧のふくらみが反対の端に描かれて、渡ったように見えるのを
            防ぐ）。弧が描かれている間に地図が止まる向き（_rest_orients）のすべてで確かめる。
        出来事ごとに1回だけ求める（spec だけから決まり、描くコマの順に依らない）。
        """
        np = self.np
        got = ev.get("_plate_lift")
        if got is not None:
            return got
        W = self.W
        fine = np.linspace(0.0, 1.0, 129)
        orients = _rest_orients(self.spec, ev["t"])
        bases = [self._plate_path(ev, fine, o) for o in orients]
        xs, ys = bases[0]
        dx, dy = float(xs[-1] - xs[0]), float(ys[-1] - ys[0])
        chord = math.hypot(dx, dy)
        if chord <= 1e-6 or ev["height"] <= 0:
            ev["_plate_lift"] = (0.0, 0.0, 0.0)
            return ev["_plate_lift"]
        nx_, ny_ = dy / chord, -dx / chord
        if ny_ > 0:
            nx_, ny_ = -nx_, -ny_     # 画面の上へ反らせる
        if -ny_ < _PLATE_STEEP:
            # 縦に近い弦: 地図の内側へ（中点が地図の右半分なら左へ）
            mid = 0.5 * (float(xs[0]) + float(xs[-1])) - self.ox
            want_left = mid - math.floor(mid / W) * W > W / 2.0
            if (nx_ < 0) != want_left:
                nx_, ny_ = -nx_, -ny_
        amp = ev["height"] * chord
        margin = ev["width"] + 2.0
        sn = np.sin(math.pi * fine)
        # 上端・下端（縦はつながっていないので、出る分だけ縮める）
        if abs(ny_) > 1e-12:
            disp = abs(ny_) * sn                             # 振幅 1 あたりの縦の動き（≥ 0）
            room = (ys - self.oy - margin) if ny_ < 0 else (self.oy + self.H - margin - ys)
            need = disp > 1e-9
            if need.any():
                k = float(np.min(np.where(need, np.clip(room, 0, None)
                                          / np.where(need, disp, 1.0), np.inf)))
                amp = max(0.0, min(amp, k))

        def tiles(x):
            k = np.floor((x - self.ox) / W).astype(np.int64)
            return k[np.r_[True, k[1:] != k[:-1]]].tolist()

        seqs = [tiles(bx) for bx, _by in bases]

        def ok(a):
            return all(tiles(bx + nx_ * a * sn) == seq for (bx, _by), seq in zip(bases, seqs))

        if amp > 0 and abs(nx_) > 1e-12 and not ok(amp):
            lo, hi = 0.0, amp                # ok(0) は常に真
            for _ in range(32):
                mid_a = (lo + hi) / 2.0
                if ok(mid_a):
                    lo = mid_a
                else:
                    hi = mid_a
            amp = lo
        ev["_plate_lift"] = (nx_, ny_, amp)
        return ev["_plate_lift"]

    def _draw_arc(self, dst, ev, t, orient, M):
        np = self.np
        if t < ev["t"]:
            return
        s_head = _ease(_ARC_EASING, (t - ev["t"]) / ev["dur"])
        if s_head <= 0:
            return
        trail = ev["trail"]
        pls = self.arc_polylines(ev, s_head, orient, M)
        for xs, ys, ss in pls:
            if trail > 0:
                al = np.maximum(_ARC_TAIL_FLOOR, 1.0 - (s_head - ss) / trail)
            else:
                al = np.ones(len(ss))
            pieces = ([(xs, ys, al)] if M is not None
                      else [(px + self.ox, py, pa) for px, py, pa
                            in _split_wrapped(np, xs - self.ox, ys, al, self.W)])
            for px, py, pa in pieces:
                _fade_line(np, dst, px, py, pa, ev["color"], ev["width"])
        if ev["head"] and pls:
            self._draw_head(dst, ev, t, s_head, pls[-1], M)

    def _draw_head(self, dst, ev, t, s_head, last, M):
        """弧の先頭の光の点（白い芯と赤い暈）。着いた後は _ARC_HEAD_FADE 秒で消える"""
        t_arrive = ev["t"] + ev["dur"]
        fade = 1.0 if t < t_arrive else 1.0 - (t - t_arrive) / _ARC_HEAD_FADE
        if fade <= 0:
            return
        xs, ys, ss = last
        if abs(float(ss[-1]) - s_head) > 1e-6:
            return    # 先頭が地球に隠れている
        x, y = float(xs[-1]), float(ys[-1])
        if M is None:
            x = (x - self.ox) % self.W + self.ox
        w = ev["width"]
        halo_r = max(3.0, w * 3.0)
        col = ev["color"]
        # 光の暈（弧の色）: 縁をぼかした大きな円を薄く重ねる。芯は白
        self._queue_dots([(x, y)], [(col[0], col[1], col[2], 0.35 * fade * col[3])],
                         halo_r, soft=min(20.0, halo_r))
        fg = self.spec["palette"]["fg"]
        self._queue_dots([(x, y)], [(fg[0], fg[1], fg[2], fade * fg[3])], max(2.0, w * 0.85))

    def _draw_ripple(self, dst, ev, t, orient, M):
        np = self.np
        if t < ev["t"] or t > ev["t"] + ev["dur"]:
            return
        u = (t - ev["t"]) / ev["dur"]
        rho = math.radians(ev["max_deg"] * (1 - (1 - u) ** 3))
        if rho <= 1e-6:
            return
        fade = (1 - u) ** 1.2
        col = ev["color"]
        rgba = (col[0], col[1], col[2], int(round(col[3] * fade)))
        if rgba[3] <= 0:
            return
        c = np.array(_unit(*ev["coord"]))
        e1 = np.cross(c, [0.0, 0.0, 1.0])
        if np.linalg.norm(e1) < 1e-9:
            e1 = np.array([1.0, 0.0, 0.0])
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(c, e1)
        n = 144
        th = np.linspace(0.0, 2 * math.pi, n + 1)
        v = (math.cos(rho) * c[None, :]
             + math.sin(rho) * (np.cos(th)[:, None] * e1[None, :]
                                + np.sin(th)[:, None] * e2[None, :]))
        xs, ys, z = self._screen(v, orient, M)
        if M is not None:
            vis = z > 0
            if vis.all():
                fk.polyline(dst, np.stack([xs[:-1], ys[:-1]], axis=1), rgba, _RIPPLE_WIDTH,
                            closed=True)
                return
            # 輪を「隠れた点」から始まるように回してから区間に分ける
            h0 = int(np.argmin(vis))
            order = np.r_[h0:len(xs) - 1, 0:h0 + 1]
            xs, ys, vis = xs[order], ys[order], vis[order]
            for i0, i1 in _runs(vis):
                if i1 - i0 >= 2:
                    fk.polyline(dst, np.stack([xs[i0:i1], ys[i0:i1]], axis=1), rgba,
                                _RIPPLE_WIDTH)
            return
        # plate: 経度を unwrap して端で分ける
        lon = np.degrees(np.unwrap(np.radians((xs - self.cx) * 360.0 / self.W)))
        xs = self.W / 2.0 + lon * self.W / 360.0
        for px, py, _pa in _split_wrapped(np, xs, ys, np.ones(len(xs)), self.W):
            if len(px) >= 2:
                fk.polyline(dst, np.stack([px + self.ox, py], axis=1), rgba, _RIPPLE_WIDTH)

    def _draw_label(self, dst, ev, sprite, t, orient):
        spec = self.spec
        x, y, z = _project_one(spec, orient, *ev["coord"])
        a = _label_alpha(spec, ev, t, z)
        if a <= 1e-3:
            return
        bx, by = _label_box(spec, ev, sprite, x, y)
        fk.blit(dst, sprite, bx, by, alpha=a)


# --- ファクトリ ---

def globe(*, size=900, projection="ortho", radius=None, view=None, land=True, step=None,
          dot=2.2, land_color="line", graticule=None, limb=0.35, back=False, atmosphere=0.25,
          colors=None, font=None, weight=None, label_size=32, seed=0):
    """点で描いた地球（ortho）か世界地図（plate）の組み立て役を返す（build() で動画にする）。

    projection: "ortho"（地球。裏側は描かない）/ "plate"（平面の世界地図。正距円筒）。
    size: ortho は正方形の一辺 px。plate は (幅, 高さ)（2:1）か幅だけ。64〜4096。
      キャンバスは絵の外接矩形（地球の箱＋縁の外へ出る弧・大気・札のぶんの対称な余白）で、
      背景は透明。
    radius: 地球の半径 px（ortho だけ）。None は 0.46×size（持ち上げた弧と大気の余白）。
    view: 最初の向き (緯度, 経度)。その点が中心・北が上。plate は中心の経度だけを使う。
      None（既定）は ortho で (20, 0)、plate で "auto"。"auto"（plate だけ）は build のときに、
      弧が地図の左右の端をまたがず、点・弧・波紋・札が端で切れず、端の経線がなるべく大陸を
      切らない中心の経度を選ぶ（_auto_center_lon。何も無ければ 0。選んだ向きは
      obj.figure.view）。太平洋を渡る弧も1本につながって描ける。
    land: True（既定。同梱の地球 = Natural Earth 1:110m の陸。パブリックドメイン。出典は
      data/NOTICE.md）/ False（陸を描かず 15 度の経緯線の点と縁だけ）/ 正距円筒の白黒 PNG の
      パス（白が陸。見つからなければ asset() で探す）/ 多角形のリスト [[(経度, 緯度), …], …]。
      None は ValueError（以前の「陸なし」と取り違えないよう False と書く）。
    step: 点の間隔（度。0.6〜5）。None は画面上の点の間隔が ≒ 3.9×dot px になる角度
      （ortho は地球の中心での間隔。size=900・dot 2.2 で 1.19 度＝Fibonacci 球で全体約 2.9 万点・
      陸に約 8 千点、size=300 で 3.56 度。plate は 1400 幅で約 2.2 度）。小さい地球ほど粗くなるので
      点がつぶれて面にならない。画面上の間隔が点の直径（2×dot）を下回ると警告する。
      点は合わせて 20 万点まで。
    dot: 点の半径 px（2 以上。2 未満は回転で瞬き、再エンコードで潰れるので ValueError）。
    land_color: 陸の点の色（不透明度 0.55 で描く。拠点の点の白と弧の赤が浮く）。
    graticule: 経緯線の間隔（度。5〜90）。None は「陸が無ければ 15・あれば描かない」、
      False / 0 で描かない。
    limb: 縁の明るさ（点の明るさ = limb + (1 − limb)·√z）。back: 裏側の点を 0.15 の明るさで描く。
    atmosphere: 縁の外の淡い光（0〜1。ortho だけ。既定 0.25。0 で無し）。0.25 で光は縁の外へ
      半径の約 24% まで届き、そのぶんキャンバスが広がる（size=900 で一辺 900 → 約 1030px。
      地球は真ん中のまま）。
    colors: 色の名前の差し替え（framekit の図の色 fg / accent / muted / line / dim / panel）。
      色の引数は名前・ffmpeg の色表記・(r, g, b[, a]) のどれでもよい。
    font / weight / label_size: 札の書体・太さ（可変フォントの wght）・大きさ px
      （framekit.label = text_image と同じ描き方。縁取り size/10 px）。
    seed: points(appear=("staged", …)) の1段の中のばらつきを決める。

    出来事（時刻 t は動画の先頭からの秒。メソッドは自分を返すので続けて書ける）:
      g.turn(t, (lat, lon), dur=1.5, easing="ease_in_out_cubic")   四元数の slerp で向ける
      g.spin(t0, t1, deg_per_sec)       地軸まわりに回す（3 度/秒を超えると警告）
      g.points(coords, t=0, color="fg", radius=4, appear="at", dur=0.3, name=None, halo=True)
                                        まわりの陸の点を抜いて（堀）光の輪を敷くので埋もれない。
                                        点が密で堀が陸を消すとき（数千点など）は halo=False（芯だけ）
      g.arc(src, dst, t=, dur=0.8, height=0.15, color="accent", width=3, head=True, trail=0.35)
      g.ripple(coord, t=, dur=1.0, max_deg=8, color="accent")
      g.night(when, dim=0.25, twilight=6)   when は UTC の datetime（naive は ValueError）
      g.label(coord, text, t=0, side="right", dur=0.3)   裏へ回ると消える。重なると警告
      obj = g.build(duration=None)   → obj.figure.xy(coord, t)・obj.figure.subsolar・
                                       obj.figure.view

    推奨: 1回 10 秒以内、1本の動画に 2 回まで。都市の点は出典を書くか「模式図」と明記する。
    重さ（1コマ）: 900×900 で約 22ms（回転なし）・約 60ms（spin / turn 中）。
    鍵（framekit.build）: 投影・寸法・向き（view="auto" は選んだ経度）・step・dot・色・limb・
      back・atmosphere・経緯線・出来事（when は ISO の文字列。points の halo は False のときだけ）・
      陸地の内容指紋（同梱の地球も PNG の中身。パスは入らない）・フォントの内容指紋
      （札があるときだけ）・描画の版 _GLOBE_VER。
    """
    fn = "globe"
    if projection not in _PROJECTIONS:
        raise ValueError(f"{fn}: projection は {', '.join(_PROJECTIONS)} のどれかにしてください: "
                         f"{projection!r}")
    plate = projection == "plate"
    if isinstance(size, (tuple, list)):
        if len(size) != 2 or any(isinstance(v, bool) or not isinstance(v, int) for v in size):
            raise ValueError(f"{fn}: size は整数か (幅, 高さ) の整数で指定してください: {size!r}")
        W, H = int(size[0]), int(size[1])
        if not plate and W != H:
            raise ValueError(f"{fn}: ortho の size は正方形（一辺の整数）にしてください: {size!r}")
        if plate and W != 2 * H:
            raise ValueError(f"{fn}: plate の size は 2:1 にしてください: {size!r}")
    else:
        if isinstance(size, bool) or not isinstance(size, int):
            raise ValueError(f"{fn}: size は整数か (幅, 高さ) の整数で指定してください: {size!r}")
        W = int(size)
        if plate and W % 2:
            raise ValueError(f"{fn}: plate の幅は偶数にしてください（高さが幅の半分）: {size!r}")
        H = W // 2 if plate else W
    if not _SIZE_MIN <= W <= _SIZE_MAX:
        raise ValueError(f"{fn}: size の幅は {_SIZE_MIN}〜{_SIZE_MAX} px にしてください: {W}")
    if radius is not None:
        if plate:
            raise ValueError(f"{fn}: radius は ortho だけで使えます（plate は size で決まる）")
        R = _num(fn, "radius", radius, 8, W / 2.0)
    else:
        R = 0.46 * W if not plate else None
    if view is None:
        view = "auto" if plate else _DEFAULT_VIEW
    if isinstance(view, str):
        if view != "auto":
            raise ValueError(f"{fn}: view は (緯度, 経度) の組か 'auto'（plate だけ）で指定してください: "
                             f"{view!r}")
        if not plate:
            raise ValueError(f"{fn}: view='auto' は plate だけで使えます"
                             f"（ortho は (緯度, 経度) で向きを指定してください）")
    else:
        view = _coord(fn, "view", view)
    dot = _num(fn, "dot", dot, None, 20)
    if dot < 2:
        raise ValueError(
            f"{fn}: dot（点の半径 px）は 2 以上にしてください: {dot:g}"
            f"（2 未満は回転で瞬き、動画の再エンコードで潰れる）")
    # 1 度が画面の何 px か（ortho は地球の中心での値。縁へ向かうほど詰まる）
    px_per_deg = W / 360.0 if plate else R * _D2R
    if step is None:
        step = min(_STEP_MAX, max(_STEP_MIN, round(_SPACING_PER_DOT * dot / px_per_deg, 2)))
    step = _num(fn, "step", step, _STEP_MIN, _STEP_MAX)
    spacing = step * px_per_deg
    if spacing < _TOUCH_PER_DOT * dot:
        _warn(current_project(),
              f"{fn}: 点の間隔が画面上で {spacing:.1f}px しかなく、点（直径 {2 * dot:g}px）"
              f"どうしが触れて面に見えます（step={step:g} 度・size={W}）。"
              f"size を大きくするか、step を大きくしてください"
              f"（step=None は間隔 ≒ {_SPACING_PER_DOT:g}×dot px。step は {_STEP_MAX:g} 度まで）")
    n_base = (_plate_grid(step)[0] * _plate_grid(step)[1]) if plate else _fib_count(step)
    if n_base > _MAX_POINTS:
        raise ValueError(f"{fn}: 点が多すぎます（step={step:g} で {n_base} 点。{_MAX_POINTS} 点まで）。"
                         f"step を大きくしてください")
    pal = fk.palette(fn, colors)
    land_col = fk.color(fn, land_color, pal)
    land = _resolve_land(fn, land)
    if graticule is None:
        grat = 15.0 if land[0] is None else 0.0
    elif graticule is False or graticule == 0:
        grat = 0.0
    else:
        grat = _num(fn, "graticule", graticule, 5, 90)
    limb = _num(fn, "limb", limb, 0, 1)
    if not isinstance(back, bool):
        raise ValueError(f"{fn}: back は True / False で指定してください: {back!r}")
    atmosphere = _num(fn, "atmosphere", atmosphere, 0, 1)
    if font is not None and not isinstance(font, (str, os.PathLike)):
        raise ValueError(f"{fn}: font はフォントファイルのパスで指定してください: {font!r}")
    label_size = _num(fn, "label_size", label_size, 8, 400)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError(f"{fn}: seed は整数で指定してください: {seed!r}")
    params = {
        "projection": projection, "W": W, "H": H, "R": R, "view": view, "step": step,
        "dot": dot, "land_color": tuple(land_col), "graticule": grat, "limb": limb, "back": back,
        "atmosphere": atmosphere, "label_size": label_size, "seed": seed, "n_base": n_base,
    }
    font_spec = {"font": os.fspath(font) if font is not None else None, "weight": weight}
    return Globe(params, pal, land, font_spec)
