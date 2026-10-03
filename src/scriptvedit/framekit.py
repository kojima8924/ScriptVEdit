# -*- coding: utf-8 -*-
"""図解アニメの共通部品（framekit）: 鍵・描画・時刻・文字・テスト用の直描き。

regex_view・slots・text_transition・flow_graph・globe などの図解ファクトリは、
どれも「Python で描いたコマを frames() に渡し、透過の動画 Object を1個返す」形になる。
鍵の作り方・文字の描き方・点と線のアンチエイリアス・時刻の丸め・audit への申告を
ここに1つだけ置き、「同一出力なら同一鍵」の規則と見た目を揃える。

内部モジュール（__init__ から再エクスポートしない）。numpy・opencv-python・Pillow は
optional 依存なので関数の中で遅延 import する（need()）。

■ 座標の約束（全描画関数で共通）
  SVG と同じ連続座標。画素 (i, j) は [i, i+1)×[j, j+1) を占め、中心は (i+0.5, j+0.5)。
  整数座標に偶数幅の線・整数座標の矩形は、にじまずに画素の境目へ揃う
  （例: y=100・幅2の横線は 99・100 行目をちょうど塗る）。
  blit(dst, sprite, x, y) の (x, y) は絵の左上の画素が来る位置（整数なら画素そのまま）。

■ 描画面
  キャンバスは float32・事前乗算（premultiplied）の HxWx4（0..1）。sRGB のまま合成する
  （ffmpeg の overlay と text_image の縁の作り方に揃える。線形化しない）。
  1つの図形（線・矢印・点の集まり）は、まず被覆率（0..1）の層へ描いてから1回だけ
  over する。重なった部分で半透明の色が二重に濃くならない。

■ アンチエイリアスの方式（実測で cv2 の LINE_AA を採らなかった理由）
  OpenCV 4.13 の太線（LINE_AA・shift=4）は幅が合わない（幅2の線の被覆の和が 3.7px、
  幅3と幅4がどちらも 5.7px）うえに上下非対称で、fillPoly(LINE_AA) は 1/4px・1/2px の
  ずらしに一切反応しなかった（3通りとも同じ画素）。どちらも動かすとがたつく。
  そこで線・円・多角形は「画素中心から図形の縁までの距離 d と、縁の向き」から、
  画素の正方形が縁の内側に入る面積（箱フィルタの被覆率。_edge_cov）を求める。
  まっすぐな縁と帯（線の胴）では面積が正確なので、どの角度・どの端数位置でも α の総量が
  保たれる（45°の幅2の線を 0.05px ずつ動かしたときの α の和の揺れ 0.02%。
  clip(0.5 - d) だった版1は 4.3%。40°の静止した線の列ごとのむらは 9% → 0.03%）。
  円・丸い端は「縁の接線の半平面」を円弧のずれ 1/(24r) だけ内側へ寄せて近似する
  （面積の偏りは半径 1.5px 以上で 0.05% 未満。位置による揺れは半径 2px で 0.4%・
  5px で 0.1%。もっと小さい点は dots を使う）。多角形の角と自己交差の点（星形の内側の
  頂点など）は一番近い縁1本の近似のままなので、その1〜2画素がわずかに薄くなる。
  多角形の内外は行ごとに辺との交点を数えて正確に決める（cv2.fillPoly は縁の画素まで
  塗るので使わない）。cv2 は blit の warpAffine（INTER_LINEAR）・縮小（INTER_AREA）と、
  広い範囲の over（blendLinear）・小さい点の畳み込み（filter2D）にだけ使う。

■ 重さ（Windows・Python 3.10・numpy 2.0・opencv 4.13 の実測。1920x1080 の1コマあたり。
  他のプロセスの負荷で 1.5 倍ほど揺れる）
  to_rgba8: 描いた範囲が全面なら約 35〜40ms（狭ければ比例して軽い）。layer_cache の層の
  .copy() 約 7ms。図形: dots 500 個（r=2.5）約 11〜19ms・5,000 個 約 39〜55ms・点ごとの色
  約 70ms・r<1.5 の 50,000 個 約 50ms、polyline 1,000px（幅3）横 約 2ms・斜め 約 6ms・
  点線 約 6ms・200 点の閉じた円 約 13ms、arrow 直線 約 4.5ms・曲線 約 12ms、
  rect 600x300 の塗り 約 3.5ms・角丸 約 8ms・枠 約 2.5ms・点線の枠 約 8ms、
  hatch 600x300 約 6ms、circle 約 4ms、polygon は縁の長さに比例（半径 400px の星形:
  頂点 16 で約 25ms、1,000 で約 250〜400ms。同じ形の閉じた polyline と同程度。版1は
  縁の画素 × 頂点の数で、頂点 1,000 に 17 秒かかった）、
  blit（文字 230x96）整数位置 約 1ms・回転 約 1.7ms・縮小 約 0.8ms。label の初回 約 2ms
  （2回目はメモ）。
  1秒ぶん（30コマ）の生成の目安（ffmpeg の qtrle 符号化を含む）: 矢印1本と点・
  文字の層だけの図 約 0.9 秒、全面の板・箱 24 個・点 90 個・回る数字の図 約 1.6 秒、
  それに回る星形・回る斜めの線 6 本・流れる点線 2 本・小さい円・縮小する文字を足して
  画面の全体が毎コマ変わる図 約 3.2 秒（描画 約 70ms/コマ。うち to_rgba8 が約 26ms）。
"""

import os
import json
import math
import hashlib
import threading
from collections import OrderedDict, namedtuple
from fractions import Fraction
from types import SimpleNamespace

# --- scriptvedit 内モジュール（SCC の外から import するので先頭に書く）---
from scriptvedit.cache import _file_fingerprint
from scriptvedit.expr import Expr, _resolve_param
from scriptvedit.state import _NAMED_EASINGS, _suggest_hint
from scriptvedit.stillseq import (
    _fps_fraction, _frames_object, _resolve_fps, _resolve_size, _round_frame,
    _sec_fraction)
from scriptvedit.text import _resolve_font
from scriptvedit.textimage import (
    _ALIGNS, _MISSING_MODES, _TEXT_IMAGE_MAX_PX, _TEXT_IMAGE_VER, _build_layout,
    _check_style, _import_pil, _norm_num, _normalize_spans, _pair, _render, _rgba,
    _variation_coords)
from scriptvedit.validate import _require_number


# --- 定数 ---

# framekit の描画（点・線・多角形の被覆率、blit の補間、文字の焼き方）の版。
# 描き方を変えたら上げる（build の鍵に入るので、全部の図が作り直される）。
#   2: 縁の被覆率を箱フィルタの面積に（斜めの縁の揺れを除く）、blit の縮小を INTER_AREA に
#      一本化（0.5 倍の前後で補間が切り替わらない）、0 長の破線を点として描く、
#      butt の端を重なった点の後の実線分に掛ける
_FRAMEKIT_VER = "2"

# 図の色（白・灰・赤だけ）。透過キャンバスに描くので背景色は持たない。
PALETTE = {
    "fg": "#ffffff",       # 文字・主な線
    "accent": "#e0241b",   # 強調（赤）
    "muted": "#9aa0a8",    # 補足の文字
    "line": "#c8ccd2",     # 枠・罫線
    "dim": "#4a4f57",      # 目立たせない線・済んだもの
    "panel": "#14161a",    # 下地の板（透過キャンバスの上に置く面）
}

# 鍵に入れるフォントの情報。生のパス（path）は鍵に入れない（ffp・index・coords だけ）。
FontRef = namedtuple("FontRef", "path index coords ffp")

# label() が受ける書式引数（text_image と同じ名前・同じ既定値）
_LABEL_DEFAULTS = {
    "size": 64, "font": None, "font_index": 0, "weight": None, "color": "white",
    "markup": False, "styles": None, "line_spacing": 1.5, "align": "left",
    "max_width": None, "border": 0, "border_color": "black",
    "shadow": (0, 0), "shadow_color": "black@0.6", "shadow_blur": 0,
    "background": None, "background_radius": 0, "padding": None,
    "canvas": None, "missing": "error",
}

# 線分を細切れにする長さ（px）。外接矩形を小さく保ち、斜めの長い線でも
# 計算する画素を「長さ × 幅」程度に抑える。
_PIECE_LEN = 32.0

# 1回の bincount / 距離計算で扱う要素数の上限（メモリを抑える）
_CHUNK = 1 << 21

# プロセス内メモの上限（件数）
_MEMO_MAX = 512
_SPRITE_MEMO_MAX = 256


# --- 依存（遅延 import）---

_DEPS = None


def need(fn):
    """numpy・cv2・PIL をまとめて遅延 import する。

    戻り値: SimpleNamespace(np, cv2, Image, ImageDraw)。
    依存が無ければ ImportError（導入方法つき）。
    """
    global _DEPS
    if _DEPS is None:
        try:
            import numpy as np
            import cv2
            from PIL import Image, ImageDraw
        except ImportError:
            raise ImportError(
                f"{fn}: numpy・opencv-python・Pillow が要ります"
                f"（pip install \"scriptvedit[figures]\"）") from None
        _DEPS = SimpleNamespace(np=np, cv2=cv2, Image=Image, ImageDraw=ImageDraw)
    return _DEPS


# --- 色 ---

def _rgba_from_seq(fn, name, value):
    """(r, g, b) / (r, g, b, a)（0〜255 の整数）を検証して (r, g, b, a) にする"""
    if len(value) not in (3, 4):
        raise ValueError(
            f"{fn}: {name} は (r, g, b) か (r, g, b, a)（0〜255）で指定してください: {value!r}")
    out = []
    for v in value:
        try:
            f = float(v) if not isinstance(v, (bool, str)) else float("nan")
        except (TypeError, ValueError):
            f = float("nan")
        if not 0 <= f <= 255:            # NaN もここで弾く
            raise ValueError(
                f"{fn}: {name} の成分は 0〜255 の数値で指定してください: {value!r}")
        out.append(int(round(f)))
    if len(out) == 3:
        out.append(255)
    return tuple(out)


def _color_value(fn, name, value, pil):
    """色表記（ffmpeg 形式）か (r, g, b[, a]) を (r, g, b, a) にする"""
    if isinstance(value, (tuple, list)) or type(value).__name__ == "ndarray":
        return _rgba_from_seq(fn, name, list(value))
    if isinstance(value, str):
        return _rgba(fn, name, value, pil)
    raise TypeError(
        f"{fn}: {name} は色の表記（'white'・'#e0241b'・'red@0.5'）か "
        f"(r, g, b[, a]) で指定してください: {value!r}")


def palette(fn, colors=None):
    """図の色の表 {名前: (r, g, b, a)} を返す。

    colors: 名前ごとの上書き（{'accent': '#ff4040'} など）。値は ffmpeg の色表記か
      (r, g, b[, a])。PALETTE に無い名前は ValueError（近い名前を案内する）。
    """
    need(fn)
    pil = _import_pil(fn)
    names = dict(PALETTE)
    if colors is not None:
        if not isinstance(colors, dict):
            raise TypeError(
                f"{fn}: colors は {{名前: 色}} の dict で指定してください: {colors!r}")
        for k, v in colors.items():
            if k not in PALETTE:
                raise ValueError(
                    f"{fn}: colors の '{k}' はパレットに無い名前です"
                    f"（使える名前: {', '.join(PALETTE)}）{_suggest_hint(k, PALETTE)}")
            names[k] = v
    return {k: _color_value(fn, f"colors['{k}']", v, pil) for k, v in names.items()}


def color(fn, value, pal):
    """パレットの名前（'accent'）・色表記（'#e0241b'）・(r, g, b[, a]) を (r, g, b, a) にする。

    パレットの名前でも色の表記でもない文字列は ValueError（パレットの近い名前を案内）。
    """
    if isinstance(value, str) and value in pal:
        return pal[value]
    need(fn)
    pil = _import_pil(fn)
    if isinstance(value, str):
        try:
            return _rgba(fn, "色", value, pil)
        except ValueError:
            raise ValueError(
                f"{fn}: 色 '{value}' はパレットの名前（{', '.join(pal)}）でも"
                f"色の表記でもありません{_suggest_hint(value, pal)}") from None
    return _color_value(fn, "色", value, pil)


# --- イージング ---

def easing(fn, param, *, name="easing"):
    """イージングの指定を (f, key) にする。

    param: None（線形）・イージングの名前（'ease_out_cubic' など）・scriptvedit の
      イージング関数（ease_out_back など）・Expr（u の式）。
    f(u): u を 0..1 に切り詰めてから式の値を返す（float）。
    key: 鍵に入れる文字列（式を u で書き下したもの）。関数そのものは鍵に入れない。
    """
    if param is None:
        expr = _resolve_param(lambda u: u)
    elif isinstance(param, bool) or isinstance(param, (int, float)):
        raise TypeError(
            f"{fn}: {name} は None・イージングの名前・イージング関数・Expr のどれかで"
            f"指定してください（数値は不可）: {param!r}")
    elif isinstance(param, str):
        if param not in _NAMED_EASINGS:
            raise ValueError(
                f"{fn}: {name} '{param}' は知らないイージングの名前です"
                f"（使える名前: {', '.join(sorted(_NAMED_EASINGS))}）"
                f"{_suggest_hint(param, _NAMED_EASINGS)}")
        expr = _resolve_param(_NAMED_EASINGS[param])
    elif isinstance(param, Expr) or callable(param):
        expr = _resolve_param(param)
    else:
        raise TypeError(
            f"{fn}: {name} は None・イージングの名前・イージング関数・Expr のどれかで"
            f"指定してください: {param!r}")
    key = expr.to_ffmpeg("u")

    def f(u):
        u = float(u)
        u = 0.0 if u < 0.0 else (1.0 if u > 1.0 else u)
        return float(expr.eval_at(u))
    return f, key


# --- 時刻 ---

def _as_fps_frac(fps_frac):
    return fps_frac if isinstance(fps_frac, Fraction) else _fps_fraction(fps_frac)


def sec_frame(sec, fps_frac):
    """秒 → フレーム番号（stillseq と同じ丸め: 10進で正確に読み、最も近いコマ。
    半分ちょうどは切り上げ）。図の中の時刻はすべて「その Object の先頭からの秒」。"""
    _require_number("sec_frame", "sec", sec)
    return _round_frame(_sec_fraction(sec), _as_fps_frac(fps_frac))


def n_frames_for(duration, fps_frac):
    """尺（秒）→ コマ数（最低1コマ）"""
    _require_number("n_frames_for", "duration", duration, 0, None)
    return max(1, sec_frame(duration, fps_frac))


def _pace_starts(n, first, slow, ratio, min_dt, at):
    """pace の本体（検証済みの引数）。(starts, 最後の拍の直前の間隔) を返す"""
    starts = [float(t) for t in at] if at else [0.0]
    if len(starts) >= 2:
        prev = starts[-1] - starts[-2]
    else:
        prev = first
    k = len(starts) - 1          # いま最後にある拍の番号
    while len(starts) < n:
        # 拍 k から拍 k+1 までの間隔（拍 k の「長さ」）
        if k < slow:
            d = max(min_dt, first)
        else:
            d = max(min_dt, prev * ratio)
        starts.append(starts[-1] + d)
        prev = d
        k += 1
    if n >= 2:
        last_dt = starts[-1] - starts[-2]
    else:
        last_dt = max(min_dt, first) if slow >= 1 else max(min_dt, first * ratio)
    return starts, last_dt


def pace(n, *, first=0.6, slow=3, ratio=0.82, min_dt=None, total=None, at=None,
         hold_end=1.0, fps=30):
    """n 個の拍（数え上げ・点灯・送信など）の開始秒を返す: (starts, end)。

    最初の slow 拍は first 秒ずつ。以降は前の間隔 × ratio で縮め、下限は min_dt
    （既定 1/fps）。min_dt を 1/fps より小さくすると、下限に張り付いた後は1コマに
    複数の拍が入る（描く側は sec_frame で「そのコマまでに来た拍」を数えて全部描く）。
    at=[秒, …]: 先頭の拍の時刻を明示する（単調増加でなければ ValueError）。
      残りの拍は同じ規則で続ける（直前の間隔は at の最後の2つの差）。
    total: end ≤ total に収まるよう ratio を二分法で下げる。0.3 まで下げても
      収まらなければ ValueError。
    end = 最後の拍の開始 + max(min_dt, 直前の間隔) + hold_end
    秒はすべて「その Object の先頭からの秒」。
    """
    fn = "pace"
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"{fn}: n は 1 以上の整数で指定してください: {n!r}")
    _require_number(fn, "fps", fps, 1, 1000)
    _require_number(fn, "first", first, 0, None)
    if first <= 0:
        raise ValueError(f"{fn}: first は 0 より大きくしてください: {first!r}")
    if isinstance(slow, bool) or not isinstance(slow, int) or slow < 0:
        raise ValueError(f"{fn}: slow は 0 以上の整数で指定してください: {slow!r}")
    _require_number(fn, "ratio", ratio, 0, 1)
    if ratio <= 0:
        raise ValueError(f"{fn}: ratio は 0 より大きく 1 以下で指定してください: {ratio!r}")
    if min_dt is None:
        min_dt = 1.0 / float(fps)
    _require_number(fn, "min_dt", min_dt, 0, None)
    if min_dt <= 0:
        raise ValueError(f"{fn}: min_dt は 0 より大きくしてください: {min_dt!r}")
    _require_number(fn, "hold_end", hold_end, 0, None)
    if at is not None:
        if not isinstance(at, (list, tuple)) or not at:
            raise ValueError(f"{fn}: at は秒のリストで指定してください: {at!r}")
        if len(at) > n:
            raise ValueError(f"{fn}: at の数（{len(at)}）が拍の数 n（{n}）より多い")
        for i, t in enumerate(at):
            _require_number(fn, f"at[{i}]", t, 0, None)
        for i in range(1, len(at)):
            if not at[i] > at[i - 1]:
                raise ValueError(
                    f"{fn}: at は単調増加で指定してください: at[{i - 1}]={at[i - 1]!r}, "
                    f"at[{i}]={at[i]!r}")
        at = list(at)

    def run(r):
        starts, last_dt = _pace_starts(n, float(first), slow, float(r), float(min_dt), at)
        return starts, starts[-1] + max(float(min_dt), last_dt) + float(hold_end)

    starts, end = run(ratio)
    if total is None:
        return starts, end
    _require_number(fn, "total", total, 0, None)
    if end <= total + 1e-9:
        return starts, end
    lo_r = 0.3
    if ratio < lo_r:
        raise ValueError(
            f"{fn}: 拍が多すぎる。total を延ばすか拍を減らす"
            f"（ratio={ratio} でも end={end:.3f}s > total={total}s）")
    starts_lo, end_lo = run(lo_r)
    if end_lo > total + 1e-9:
        raise ValueError(
            f"{fn}: 拍が多すぎる。total を延ばすか拍を減らす"
            f"（ratio を 0.3 まで下げても end={end_lo:.3f}s > total={total}s。"
            f"min_dt={min_dt:.4f}s を下げる手もある）")
    lo, hi = lo_r, float(ratio)       # lo は収まる・hi は収まらない
    best = (starts_lo, end_lo)
    for _ in range(60):
        mid = (lo + hi) / 2
        s, e = run(mid)
        if e <= total + 1e-9:
            lo, best = mid, (s, e)
        else:
            hi = mid
        if hi - lo < 1e-12:
            break
    return best


# --- 鍵 ---

def norm(value):
    """鍵に入れる値の正規形（JSON にできる値）。

    - float は round(x, 6)。-0.0 は 0.0。NaN・±inf は ValueError。整数の値になった float は
      int にする（3 と 3.0 で鍵を割らない。textimage._norm_num と同じ規則）
    - tuple は list に、dict はキーを str に限ってキー順に並べる
    - numpy のスカラーは .item()（配列は tolist()）、Fraction は float
    - Expr は u で書き下した式の文字列
    - 関数は TypeError（関数は鍵にできない。秒やキーフレームのデータで渡す）
    """
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"norm: 鍵に NaN / Infinity は入れられません: {value!r}")
        r = round(value, 6)
        if r.is_integer():
            return int(r)                  # -0.0 も 0 になる
        return r
    if isinstance(value, Fraction):
        return norm(float(value))
    if isinstance(value, Expr):
        return value.to_ffmpeg("u")
    mod = type(value).__module__
    if mod == "numpy" or mod.startswith("numpy."):
        if hasattr(value, "tolist") and getattr(value, "ndim", 0):
            return norm(value.tolist())
        if hasattr(value, "item"):
            return norm(value.item())
    if isinstance(value, FontRef):         # tuple の一種なので先に見る（生のパスは入れない）
        return [value.ffp, value.index,
                None if value.coords is None else [norm(float(c)) for c in value.coords]]
    if isinstance(value, (list, tuple)):
        return [norm(v) for v in value]
    if isinstance(value, dict):
        out = {}
        for k in value:
            if not isinstance(k, str):
                raise TypeError(f"norm: 鍵の dict のキーは文字列にしてください: {k!r}")
        for k in sorted(value):
            out[k] = norm(value[k])
        return out
    if isinstance(value, (set, frozenset)):
        items = [norm(v) for v in value]
        return sorted(items, key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False))
    if callable(value):
        raise TypeError(
            f"norm: 関数は鍵にできない。秒やキーフレームのデータで渡す: {value!r}")
    raise TypeError(f"norm: 鍵にできない型です（{type(value).__name__}）: {value!r}")


# --- フォント ---

def font_ref(fn, font=None, font_index=0, weight=None):
    """フォントを (path, index, coords, ffp) にする。

    path は text._resolve_font（font=None は text() と同じ既定の探索）、coords は
    可変フォントの軸の値（textimage._variation_coords。weight=None・既定と同じ太さは None）、
    ffp は内容指紋。鍵に入れるのは ffp・index・coords だけ（生のパスは入れない）。
    """
    if isinstance(font_index, bool) or not isinstance(font_index, int) or font_index < 0:
        raise ValueError(f"{fn}: font_index は 0 以上の整数で指定してください: {font_index!r}")
    if weight is not None and not isinstance(weight, str):
        _require_number(fn, "weight", weight, 1, 2000)
    need(fn)
    pil = _import_pil(fn)
    path = _resolve_font(font)
    coords = _variation_coords(fn, pil, path, font_index, _norm_num(weight))
    return FontRef(path, font_index, coords, _file_fingerprint(path))


# --- 文字（text_image と同じ描き方の Sprite）---

class Sprite:
    """label() が返す文字の絵。

    rgba: uint8 の HxWx4（ストレートアルファ・書き込み不可）。text_image の PNG と画素が一致する
    w, h: 寸法
    base: (行頭の x, 1行目のベースラインの y)（最初に描いた字の位置。整数）
    layout: textimage._build_layout の戻り値
    fonts: 使ったフォントの FontRef（基本書式のフォントを含む。build(fonts=) にそのまま渡せる）
    meta: 文字の申告（size・size_min・size_max・border・shadow・shadow_blur・background・
      font・content_width・content_height・padding・lines・missing・content）
    sig: 描いた絵の署名（16桁。text_image の鍵と同じ中身: 字の並びと位置・書式ごとの
      フォントの内容指紋・大きさ・軸の値・色、縁取り・影・下地、寸法、PIL の版）。
      build(fonts=) に Sprite を渡すと、これが鍵に入る（文字を直せば図も作り直される）
    premul: float32 の事前乗算（blit 用。初回に作って保持する）
    """
    __slots__ = ("rgba", "w", "h", "base", "layout", "fonts", "meta", "sig", "_premul")

    def __init__(self, rgba, base, layout, fonts, meta, sig):
        self.rgba = rgba
        self.h, self.w = int(rgba.shape[0]), int(rgba.shape[1])
        self.base = base
        self.layout = layout
        self.fonts = fonts
        self.meta = meta
        self.sig = sig
        self._premul = None

    @property
    def premul(self):
        if self._premul is None:
            self._premul = _premul_from_rgba8(self.rgba)
        return self._premul

    def __repr__(self):
        return (f"Sprite({self.meta.get('content', '')[:20]!r}, {self.w}x{self.h}, "
                f"size={self.meta.get('size')})")


_SPRITE_MEMO = OrderedDict()
_SPRITE_LOCK = threading.Lock()


def _color_text(fn, name, value):
    """(r, g, b[, a]) を ffmpeg の色表記 0xRRGGBBAA にする（label の色引数用）"""
    r, g, b, a = _rgba_from_seq(fn, name, list(value))
    return f"0x{r:02x}{g:02x}{b:02x}{a:02x}"


def label(fn, content, **fmt):
    """文字を text_image と同じ描き方で RGBA の Sprite にする（PNG は書かない）。

    fmt は text_image と同じ書式引数（size・font・font_index・weight・color・markup・
    styles・line_spacing・align・max_width・border・border_color・shadow・shadow_color・
    shadow_blur・background・background_radius・padding・canvas・missing）。既定値も同じ。
    色は ffmpeg の表記のほか (r, g, b[, a]) も受ける（palette() / color() の戻り値）。
    textimage の内部関数を text_image と同じ順に呼ぶので、画素は text_image の PNG と一致する。
    同じ引数の Sprite はプロセス内で使い回す（レイヤーは Plan と Render で2回 exec される）。
    """
    unknown = sorted(set(fmt) - set(_LABEL_DEFAULTS))
    if unknown:
        raise TypeError(
            f"{fn}: 文字の書式に知らない引数 {unknown} があります"
            f"（使える引数: {', '.join(_LABEL_DEFAULTS)}）"
            f"{_suggest_hint(unknown[0], _LABEL_DEFAULTS)}")
    args = dict(_LABEL_DEFAULTS)
    args.update(fmt)
    for k in ("color", "border_color", "shadow_color", "background"):
        v = args[k]
        if v is not None and (isinstance(v, (tuple, list)) or type(v).__name__ == "ndarray"):
            args[k] = _color_text(fn, k, v)
    np = need(fn).np
    pil = _import_pil(fn)
    font_path = _resolve_font(args["font"])
    try:
        memo_key = repr((fn, content, font_path,
                         sorted((k, v) for k, v in args.items() if k != "font")))
    except Exception:          # repr できない値は検証で弾かれる（メモしない）
        memo_key = None
    if memo_key is not None:
        with _SPRITE_LOCK:
            hit = _SPRITE_MEMO.get(memo_key)
            if hit is not None:
                _SPRITE_MEMO.move_to_end(memo_key)
                return hit

    # --- 引数の検証（text_image と同じ順・同じ文言）---
    size = args["size"]
    font_index = args["font_index"]
    weight = args["weight"]
    markup = args["markup"]
    line_spacing = args["line_spacing"]
    align = args["align"]
    max_width = args["max_width"]
    border = args["border"]
    shadow = args["shadow"]
    shadow_blur = args["shadow_blur"]
    background_radius = args["background_radius"]
    padding = args["padding"]
    canvas_ = args["canvas"]
    missing = args["missing"]
    styles = args["styles"]
    _require_number(fn, "size", size, 1, 2000)
    if isinstance(font_index, bool) or not isinstance(font_index, int) or font_index < 0:
        raise ValueError(f"{fn}: font_index は 0 以上の整数で指定してください: {font_index!r}")
    if weight is not None and not isinstance(weight, str):
        _require_number(fn, "weight", weight, 1, 2000)
    if not isinstance(markup, bool):
        raise TypeError(f"{fn}: markup は True / False で指定してください: {markup!r}")
    _require_number(fn, "line_spacing", line_spacing, 0.1, 20)
    if align not in _ALIGNS:
        raise ValueError(
            f"{fn}: align は {_ALIGNS} のいずれか: {align!r}{_suggest_hint(align, _ALIGNS)}")
    if max_width is not None:
        _require_number(fn, "max_width", max_width, 1, _TEXT_IMAGE_MAX_PX)
    _require_number(fn, "border", border, 0, 500)
    border = int(border)
    shadow = _pair(fn, "shadow", shadow)
    _require_number(fn, "shadow_blur", shadow_blur, 0, 500)
    _require_number(fn, "background_radius", background_radius, 0, None)
    if padding is not None:
        padding = _pair(fn, "padding", padding, 0)
    if canvas_ is not None:
        if isinstance(canvas_, (int, float)):
            raise ValueError(f"{fn}: canvas は (幅, 高さ) で指定してください: {canvas_!r}")
        canvas_ = _pair(fn, "canvas", canvas_, 2)
    if missing not in _MISSING_MODES:
        raise ValueError(
            f"{fn}: missing は {_MISSING_MODES} のいずれか: {missing!r}"
            f"{_suggest_hint(missing, _MISSING_MODES)}")
    if styles is not None and not isinstance(styles, dict):
        raise TypeError(
            f"{fn}: styles は 名前 → 書式 の dict で指定してください"
            f"（例: {{'r': {{'color': 'red'}}}}）: {styles!r}")
    styles = dict(styles or {})
    for name, st in styles.items():
        if not isinstance(name, str) or not name:
            raise TypeError(f"{fn}: styles のキーは文字列で指定してください: {name!r}")
        styles[name] = _check_style(fn, f"styles['{name}']", st)

    border_rgba = _rgba(fn, "border_color", args["border_color"], pil)
    shadow_rgba = _rgba(fn, "shadow_color", args["shadow_color"], pil)
    background_rgba = (None if args["background"] is None
                       else _rgba(fn, "background", args["background"], pil))
    size = _norm_num(size)
    shadow_blur = _norm_num(shadow_blur)
    background_radius = _norm_num(background_radius)
    base_style = {"color": args["color"], "size": size, "font": font_path,
                  "font_index": font_index, "weight": _norm_num(weight)}
    _rgba(fn, "color", args["color"], pil)

    spans = _normalize_spans(fn, content, markup, styles)
    layout = _build_layout(
        fn, pil, spans, base_style, line_spacing=line_spacing, align=align,
        max_width=max_width, border=border, shadow=shadow, shadow_blur=shadow_blur,
        padding=padding, canvas=canvas_, missing=missing)
    img = _render(
        pil, layout, border=border, border_rgba=border_rgba, shadow=shadow,
        shadow_rgba=shadow_rgba, shadow_blur=shadow_blur,
        background_rgba=background_rgba, background_radius=background_radius)
    rgba = np.array(img, dtype=np.uint8)
    rgba.flags.writeable = False

    # 使ったフォント（基本書式のフォントは字が無くても行の縦位置を決めるので含める）
    refs = []
    seen = set()
    base_coords = _variation_coords(fn, pil, font_path, font_index, _norm_num(weight))
    for path, index, coords in ([(font_path, font_index, base_coords)]
                                + [(s["font"], s["font_index"], s["coords"])
                                   for s in layout["styles"]]):
        k = (path, index, coords)
        if k in seen:
            continue
        seen.add(k)
        refs.append(FontRef(path, index, coords, _file_fingerprint(path)))
    placed = layout["placed"]
    base = (int(placed[0][0]), int(placed[0][1])) if placed else (0, 0)
    # 描いた絵の署名（text_image の key_spec と同じ中身。効かない引数で割らない）
    sig_spec = {
        "ver": _TEXT_IMAGE_VER,
        "pil": pil["PIL"].__version__,
        "styles": [{"font": _file_fingerprint(s["font"]), "index": s["font_index"],
                    "size": s["size"],
                    "var": None if s["coords"] is None else list(s["coords"]),
                    "rgba": list(s["rgba"])}
                   for s in layout["styles"]],
        "placed": [[x, y, text, sidx] for x, y, text, sidx in placed],
        "size": [layout["width"], layout["height"]],
        "border": [border, list(border_rgba)] if border else None,
        "shadow": ([list(shadow), list(shadow_rgba), shadow_blur]
                   if (shadow != (0, 0) or shadow_blur) else None),
        "background": ([list(background_rgba), background_radius]
                       if background_rgba is not None else None),
    }
    sig = hashlib.sha256(json.dumps(
        sig_spec, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    meta = {
        "content": layout["plain"],
        "size": size,
        "size_min": layout["size_min"],
        "size_max": layout["size_max"],
        "border": border,
        "shadow": shadow,
        "shadow_blur": shadow_blur,
        "background": background_rgba is not None,
        "font": font_path,
        "width": layout["width"],
        "height": layout["height"],
        "content_width": layout["content_width"],
        "content_height": layout["content_height"],
        "padding": layout["padding"],
        "lines": len(layout["lines"]),
        "missing": list(layout["missing"]),
    }
    sprite = Sprite(rgba, base, layout, tuple(refs), meta, sig)
    if memo_key is not None:
        with _SPRITE_LOCK:
            _SPRITE_MEMO[memo_key] = sprite
            while len(_SPRITE_MEMO) > _SPRITE_MEMO_MAX:
                _SPRITE_MEMO.popitem(last=False)
    return sprite


def text_meta(sprites, *, width, height, content=None):
    """図の中の文字を p.audit() へ申告する dict（obj._text_image に入れる形）を作る。

    audit の _audit_text_images がそのまま読む形。申告するのは図の中で一番小さい文字
    （size・size_min とその文字の縁取り・影・下地）。ただし装飾（縁取り・影・下地）は、
    **全部の文字が装飾つきのときだけ有り**として申告する（1つでも装飾の無い文字があれば
    無しにして、audit の text-no-decoration を出させる。大きな見出しが素のまま・小さな
    注記だけ縁取り、という図を見逃さない）。content_width / content_height は
    一番幅の広い文字のもの（はみ出しの検査用）。lines は全部の行数の合計。
    sprites: Sprite か (Sprite, 倍率) の並び（blit で拡大縮小して置く文字は倍率を添える）。
    content: audit の表示名に使う文字列（省略時は全部の文字を「 / 」でつないだもの。
      build が先頭に図の種類 kind を添える）。
    文字が1つも無ければ None。
    """
    items = []
    for s in sprites:
        if isinstance(s, (tuple, list)):
            sp, k = s
        else:
            sp, k = s, 1.0
        if not isinstance(sp, Sprite):
            raise TypeError(f"text_meta: Sprite（label の戻り値）を渡してください: {sp!r}")
        k = _num("text_meta", "倍率", k, 0)
        items.append((sp, float(k)))
    if not items:
        return None
    small, ks = min(items, key=lambda it: it[0].meta["size_min"] * it[1])
    wide, kw = max(items, key=lambda it: it[0].meta["content_width"] * it[1])
    sizes_min = [sp.meta["size_min"] * k for sp, k in items]
    sizes_max = [sp.meta["size_max"] * k for sp, k in items]
    m = small.meta
    if content is None:
        content = " / ".join(sp.meta["content"].replace("\n", " ") for sp, _k in items)

    def decorated(meta):
        return bool(meta["border"] or tuple(meta["shadow"]) != (0, 0)
                    or meta["shadow_blur"] or meta["background"])
    all_decorated = all(decorated(sp.meta) for sp, _k in items)
    return {
        "content": content,
        "size": m["size"] * ks,
        "size_min": min(sizes_min),
        "size_max": max(sizes_max),
        "border": m["border"] if all_decorated else 0,
        "shadow": tuple(m["shadow"]) if all_decorated else (0, 0),
        "shadow_blur": m["shadow_blur"] if all_decorated else 0,
        "background": m["background"] if all_decorated else False,
        "font": m["font"],
        "width": int(width),
        "height": int(height),
        "content_width": wide.meta["content_width"] * kw,
        "content_height": wide.meta["content_height"] * kw,
        "padding": tuple(m["padding"]),
        "lines": sum(sp.meta["lines"] for sp, _k in items),
        "missing": sorted({ch for sp, _k in items for ch in sp.meta["missing"]}),
    }


# --- 描画面 ---

def canvas(w, h):
    """空の透過キャンバス（float32・事前乗算の HxWx4、0..1）"""
    for name, v in (("w", w), ("h", h)):
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise ValueError(f"canvas: {name} は正の整数で指定してください: {v!r}")
    np = need("canvas").np
    return np.zeros((h, w, 4), np.float32)


def _premul_from_rgba8(rgba):
    """uint8 ストレートアルファ → float32 事前乗算（読み取り専用）"""
    np = need("framekit").np
    f = rgba.astype(np.float32) * np.float32(1.0 / 255.0)
    f[..., :3] *= f[..., 3:4]
    f.flags.writeable = False
    return f


def _as_premul(src):
    """Sprite / uint8 のストレートアルファ / float の事前乗算 / PIL.Image を
    float32 の事前乗算 HxWx4 にする"""
    np = need("framekit").np
    if isinstance(src, Sprite):
        return src.premul
    if hasattr(src, "convert") and hasattr(src, "size") and not hasattr(src, "shape"):
        return _premul_from_rgba8(np.asarray(src.convert("RGBA"), dtype=np.uint8))
    arr = np.asarray(src)
    if arr.ndim != 3 or arr.shape[2] != 4:
        raise ValueError(f"framekit: 絵は HxWx4 の配列で渡してください: 形 {arr.shape}")
    if arr.dtype == np.uint8:
        return _premul_from_rgba8(arr)
    if arr.dtype != np.float32:
        arr = arr.astype(np.float32)
    return arr


def _num(fn, name, v, lo=None, hi=None):
    """数値（numpy のスカラーも可）を float にする。NaN・±inf・数値でないもの・範囲外は ValueError"""
    if isinstance(v, bool) or isinstance(v, Expr) or callable(v):
        raise ValueError(f"{fn}: {name} は数値で指定してください: {v!r}")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{fn}: {name} は数値で指定してください: {v!r}") from None
    if not math.isfinite(f):
        raise ValueError(f"{fn}: {name} に NaN/Infinity は使えません: {v!r}")
    if (lo is not None and f < lo) or (hi is not None and f > hi):
        rng = f"{lo if lo is not None else ''}〜{hi if hi is not None else ''}"
        raise ValueError(f"{fn}: {name} は {rng} の範囲で指定してください: {v!r}")
    return f


def _coords(fn, name, v, n, form):
    """座標の組（点・矩形）を検証して float の tuple にする（NaN・±inf・数でないものは ValueError）"""
    if isinstance(v, (str, bytes)) or not hasattr(v, "__len__") or len(v) != n:
        raise ValueError(f"{fn}: {name} は {form} で指定してください: {v!r}")
    return tuple(_num(fn, f"{name}[{i}]", c) for i, c in enumerate(v))


def _edge_cov(np, t, a, b):
    """画素の正方形のうち、縁の内側に入る面積（箱フィルタの被覆率。0..1）。

    t: 画素中心から縁までの「内側への」距離（内側で正）。
    a, b: 縁の法線の成分の絶対値の大きい方・小さい方（a² + b² = 1、a ≥ b ≥ 0）。
    正方形を法線へ射影した長さの分布（幅 a と幅 b の一様分布のたたみ込み＝台形）の
    累積で、まっすぐな縁なら面積そのもの（軸に平行な縁では clip(0.5 + t, 0, 1)）。
    t・a・b は同じ形の配列か、a・b はスカラー（float32 で計算する）。
    """
    s = a + b
    u = np.clip(t + s * np.float32(0.5), 0.0, s)
    d2 = np.maximum(np.float32(2.0) * a * b, np.float32(1e-6))   # b → 0 でも 0 で割らない
    r1 = u * u / d2
    r3 = np.float32(1.0) - (s - u) * (s - u) / d2
    r2 = (u - b * np.float32(0.5)) / a
    return np.where(u <= b, r1, np.where(u >= a, r3, r2)).astype(np.float32, copy=False)


def _curv_shift(r):
    """半径 r の凸な円弧の縁を、接線の半平面で近似したときのずれ（px）。

    画素の正方形の接線方向の分散は向きに依らず 1/12 なので、円弧は画素の中で平均
    1/(24·r) だけ内側へ寄る。縁をこの分だけ内側へずらすと、円1周あたり π/12 px² だった
    面積の過大（半径 2px で +2%、1px で +8%）がほぼ消える（実測: 半径 1〜10px で ±0.3% 以内）。
    """
    return 1.0 / (24.0 * r)


def _band_cov(np, hw, dist, a, b, radial=None):
    """中心線から dist（≥ 0）の画素が、幅 2·hw の帯に覆われる面積（両側の縁を引き算）。

    radial: 一番近い点が線分の端（丸い端・丸い継ぎ目・点）の画素（bool の配列か True）。
      そこの縁は半径 hw の円弧なので、_curv_shift の分だけ縁を内側へずらす。
    """
    t = np.float32(hw) - dist
    if radial is not None:
        t = t - np.where(radial, np.float32(_curv_shift(hw)), np.float32(0.0))
    c = _edge_cov(np, t, a, b)
    if hw < 0.75:                 # 細い帯は反対側の縁も同じ画素を横切る
        c = c - _edge_cov(np, np.float32(-hw) - dist, a, b)
    return np.clip(c, 0.0, 1.0)


def _fill_circle_cov(np, R, d, a, b):
    """中心から d の画素が、半径 R の円（塗り）に覆われる面積（a, b は中心からの向き）"""
    if R <= 0:
        return np.zeros(np.shape(d), np.float32)
    if R < 0.75:
        return _band_cov(np, R, d, a, b, radial=True)
    return _edge_cov(np, np.float32(R - _curv_shift(R)) - d, a, b)


def _dir_ab(np, qx, qy, dist):
    """ベクトル (qx, qy)（長さ dist）の向きの成分の絶対値 → (a, b)。長さ 0 は軸の向き。

    b が 1e-5 未満（float32 の丸めで軸からわずかに傾いた向き）は軸に平行とみなす
    （軸に平行な縁の外の画素が厳密に 0・内の画素が厳密に 1 になる。誤差は 1e-6 未満）。
    """
    ax_, ay_ = np.abs(qx), np.abs(qy)
    big = np.maximum(ax_, ay_)
    small = np.minimum(ax_, ay_)
    ok = dist > np.float32(1e-6)
    inv = np.where(ok, np.float32(1.0) / np.where(ok, dist, np.float32(1.0)), np.float32(0.0))
    b = np.where(ok, small * inv, np.float32(0.0))
    axis = b < np.float32(1e-5)
    a = np.where(ok & ~axis, big * inv, np.float32(1.0))
    b = np.where(axis, np.float32(0.0), b)
    return a.astype(np.float32, copy=False), b.astype(np.float32, copy=False)


def _check_canvas(fn, dst):
    np = need(fn).np
    if (not isinstance(dst, np.ndarray) or dst.dtype != np.float32 or dst.ndim != 3
            or dst.shape[2] != 4):
        raise TypeError(
            f"{fn}: dst は canvas() が返す float32 の HxWx4 にしてください: "
            f"{getattr(dst, 'dtype', type(dst))} {getattr(dst, 'shape', '')}")
    if not dst.flags.writeable:
        raise ValueError(
            f"{fn}: dst が書き込み不可です（layer_cache の層は .copy() してから描く）")
    return np


def over(dst, src, x=0, y=0):
    """src を dst の (x, y) に重ねる（Porter-Duff の over。事前乗算）。整数位置用。

    x / y が整数でなければ blit（双線形）に回す。dst を書き換えて返す。
    """
    _check_canvas("over", dst)
    if not (float(x).is_integer() and float(y).is_integer()):
        return blit(dst, src, x, y)
    s = _as_premul(src)
    x, y = int(x), int(y)
    H, W = dst.shape[:2]
    h, w = s.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x0 >= x1 or y0 >= y1:
        return dst
    sp = s[y0 - y:y1 - y, x0 - x:x1 - x]
    dp = dst[y0:y1, x0:x1]
    dp *= 1.0 - sp[..., 3:4]
    dp += sp
    return dst


def _axis_cov(np, lo, hi, i0, i1):
    """画素 i0..i1-1 が区間 [lo, hi) に占める割合（連続座標）"""
    idx = np.arange(i0, i1, dtype=np.float64)
    return np.clip(np.minimum(idx + 1.0, hi) - np.maximum(idx, lo), 0.0, 1.0).astype(np.float32)


def _affine(w, h, x, y, scale, angle, pivot):
    """blit の変換の係数と外接矩形。

    戻り値: ((c, s, ax, ay, px, py), (x0, y0, x1, y1))。c・s は cos・sin に scale を
    掛けたもの、(ax, ay) は pivot の行き先、外接矩形は整数の画素の範囲（切り詰めない）。
    """
    px, py = (w / 2.0, h / 2.0) if pivot is None else (float(pivot[0]), float(pivot[1]))
    th = math.radians(float(angle) % 360.0)
    c, s_ = math.cos(th) * scale, math.sin(th) * scale
    ax, ay = float(x) + px, float(y) + py           # pivot の行き先
    corners = [(-px, -py), (w - px, -py), (-px, h - py), (w - px, h - py)]
    xs = [ax + c * u - s_ * v for u, v in corners]
    ys = [ay + s_ * u + c * v for u, v in corners]
    box = (int(math.floor(min(xs))), int(math.floor(min(ys))),
           int(math.ceil(max(xs))), int(math.ceil(max(ys))))
    return (c, s_, ax, ay, px, py), box


def affine_box(w, h, x, y, *, scale=1.0, angle=0.0, pivot=None):
    """w×h の絵を blit と同じ変換で (x, y) に置いたときの外接矩形 (x0, y0, x1, y1)（整数の画素）"""
    return _affine(w, h, x, y, scale, angle, pivot)[1]


def warp_patch(src, x, y, *, scale=1.0, angle=0.0, pivot=None, box=None):
    """blit の変換の核。src（HxWxC の float32。C は何チャンネルでもよい）を blit と同じ
    座標・同じ補間で写し、(x0, y0, patch) を返す（patch の左上がキャンバスの (x0, y0)）。

    box: 写す範囲 (x0, y0, x1, y1)（整数の画素）。None なら外接矩形の全体（affine_box）。
    縮小（scale < 1）は、どの倍率でも先に INTER_AREA で「元の寸法 × scale」の整数の寸法へ
    縮めてから INTER_LINEAR で写す（blit の docstring を参照）。整数位置・等倍・無回転の
    近道は持たない（画素をそのまま使いたい呼び出し側が自分で切り出す）。
    blit と text_transition の字（縁取りと塗りの2ch）がこれを共有する。描き方を変えたら
    _FRAMEKIT_VER を上げる（text_transition の図も framekit.build の鍵で作り直される）。
    """
    deps = need("warp_patch")
    np, cv2 = deps.np, deps.cv2
    h, w = src.shape[:2]
    (c, s_, ax, ay, px, py), full = _affine(w, h, x, y, scale, angle, pivot)
    bx0, by0, bx1, by1 = full if box is None else box
    img = src
    sx = sy = 1.0          # 縮めた絵の1画素が元の何画素ぶんか
    if scale < 1.0:
        nw = max(1, int(round(w * scale)))
        nh = max(1, int(round(h * scale)))
        if (nw, nh) != (w, h):
            img = cv2.resize(np.ascontiguousarray(src), (nw, nh),
                             interpolation=cv2.INTER_AREA)
            sx, sy = w / nw, h / nh
    # 連続座標: d = R·S·(s - p) + a。画素の添字（中心が整数）へ直す:
    #   d_i = A·s_i' + A·(0.5·(sx, sy) - p) + a - 0.5 - (bx0, by0)
    a11, a12 = c * sx, -s_ * sy
    a21, a22 = s_ * sx, c * sy
    ux, uy = 0.5 * sx - px, 0.5 * sy - py
    tx = c * ux - s_ * uy + ax - 0.5 - bx0
    ty = s_ * ux + c * uy + ay - 0.5 - by0
    M = np.array([[a11, a12, tx], [a21, a22, ty]], dtype=np.float64)
    patch = cv2.warpAffine(np.ascontiguousarray(img), M, (max(1, bx1 - bx0), max(1, by1 - by0)),
                           flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                           borderValue=(0, 0, 0, 0))
    return bx0, by0, patch


def blit(dst, sprite, x, y, *, alpha=1.0, scale=1.0, angle=0.0, clip=None, pivot=None):
    """絵（Sprite・uint8 の RGBA・事前乗算の float 配列・PIL.Image）を dst に置く。

    (x, y): 等倍・無回転のときに絵の左上が来る位置（端数可）。
    scale / angle: pivot（絵の中の点。既定は中心）を軸に拡大縮小・回転する。
      angle は度で時計回り（scriptvedit の rotate と同じ向き）。pivot は (x+px, y+py) に留まる。
    alpha: 不透明度を掛ける（0..1）。
    clip: (x0, y0, x1, y1) の矩形で切り抜く（連続座標。端数の縁は被覆率で落とす）。
      回る数字（窓の中を縦に流れる字）に使う。
    整数位置・等倍・無回転なら画素をそのまま置く（一致が保証される）。それ以外は
    事前乗算のまま cv2.warpAffine（INTER_LINEAR。位置は 1/32px 刻み）。縮小（scale < 1）は
    どの倍率でも、先に INTER_AREA で「元の寸法 × scale」の整数の寸法へ縮めてから置く
    （面積の平均なので細い線がちらつかない。倍率を連続に変えると縮めた寸法が 1px ずつ
    変わるだけなので、補間の方式が途中で切り替わって急にぼける、ということが無い。
    scale が 1 に近く寸法が変わらない間は INTER_LINEAR だけと同じ）。
    止まっている文字は整数位置に置くとくっきりする（端数位置は双線形で少しにじむ。
    動かす間だけ端数位置にする）。
    """
    np = _check_canvas("blit", dst)
    x, y = _num("blit", "x", x), _num("blit", "y", y)
    alpha, scale = _num("blit", "alpha", alpha), _num("blit", "scale", scale)
    angle = _num("blit", "angle", angle)
    if clip is not None:
        clip = _coords("blit", "clip", clip, 4, "(x0, y0, x1, y1)")
    if pivot is not None:
        pivot = _coords("blit", "pivot", pivot, 2, "(px, py)")
    src = _as_premul(sprite)
    if alpha <= 0.0 or scale <= 0:
        return dst
    alpha = min(alpha, 1.0)
    h, w = src.shape[:2]
    H, W = dst.shape[:2]
    angle = float(angle) % 360.0
    integral = (scale == 1 and angle == 0.0
                and float(x).is_integer() and float(y).is_integer())
    if integral and clip is None and alpha == 1.0:
        return over(dst, src, int(x), int(y))

    # 置く範囲（dst 上の外接矩形）。変換は warp_patch（text_transition の字と共有）
    if integral:
        bx0, by0, bx1, by1 = int(x), int(y), int(x) + w, int(y) + h
    else:
        bx0, by0, bx1, by1 = affine_box(w, h, x, y, scale=scale, angle=angle, pivot=pivot)
    if clip is not None:
        cx0, cy0, cx1, cy1 = clip
        bx0, by0 = max(bx0, int(math.floor(cx0))), max(by0, int(math.floor(cy0)))
        bx1, by1 = min(bx1, int(math.ceil(cx1))), min(by1, int(math.ceil(cy1)))
    bx0, by0, bx1, by1 = max(bx0, 0), max(by0, 0), min(bx1, W), min(by1, H)
    if bx0 >= bx1 or by0 >= by1:
        return dst

    if integral:
        ox, oy = int(x), int(y)
        patch = src[by0 - oy:by1 - oy, bx0 - ox:bx1 - ox].copy()
    else:
        _x0, _y0, patch = warp_patch(src, x, y, scale=scale, angle=angle, pivot=pivot,
                                     box=(bx0, by0, bx1, by1))
    if clip is not None:
        cov = (_axis_cov(np, cy0, cy1, by0, by1)[:, None]
               * _axis_cov(np, cx0, cx1, bx0, bx1)[None, :])
        patch *= cov[..., None]
    if alpha != 1.0:
        patch *= np.float32(alpha)
    dp = dst[by0:by1, bx0:bx1]
    dp *= 1.0 - patch[..., 3:4]
    dp += patch
    return dst


# --- 色の引数 ---

def _rgba_arg(fn, rgba):
    """描画関数の色（(r, g, b[, a]) の 0..255・パレットの名前・色表記）→ ストレートの 0..1"""
    np = need(fn).np
    if isinstance(rgba, str):
        rgba = color(fn, rgba, palette(fn))
    t = _rgba_from_seq(fn, "rgba", list(rgba))
    return np.array(t, dtype=np.float32) / np.float32(255.0)


def _prem(col):
    out = col.copy()
    out[:3] *= col[3]
    return out


# --- 被覆率の層 ---

class _Cov:
    """被覆率（0..1）の層。dst の部分矩形 [ix0, ix1)×[iy0, iy1) だけを持つ。"""
    __slots__ = ("np", "cov", "ix0", "iy0", "ix1", "iy1")

    def __init__(self, np, ix0, iy0, ix1, iy1):
        self.np = np
        self.ix0, self.iy0, self.ix1, self.iy1 = ix0, iy0, ix1, iy1
        self.cov = np.zeros((max(0, iy1 - iy0), max(0, ix1 - ix0)), np.float32)

    @property
    def empty(self):
        return self.ix1 <= self.ix0 or self.iy1 <= self.iy0

    def sub(self, x0, y0, x1, y1):
        """連続座標の矩形 → この層の中の添字範囲 (cx0, cy0, cx1, cy1)（空なら None）"""
        cx0 = max(self.ix0, int(math.floor(x0)))
        cy0 = max(self.iy0, int(math.floor(y0)))
        cx1 = min(self.ix1, int(math.ceil(x1)))
        cy1 = min(self.iy1, int(math.ceil(y1)))
        if cx0 >= cx1 or cy0 >= cy1:
            return None
        return cx0, cy0, cx1, cy1

    def put_max(self, c, cx0, cy0):
        h, w = c.shape
        view = self.cov[cy0 - self.iy0:cy0 - self.iy0 + h, cx0 - self.ix0:cx0 - self.ix0 + w]
        self.np.maximum(view, c, out=view)


def _cov_for(np, dst, x0, y0, x1, y1):
    H, W = dst.shape[:2]
    return _Cov(np, max(0, int(math.floor(x0))), max(0, int(math.floor(y0))),
                min(W, int(math.ceil(x1))), min(H, int(math.ceil(y1))))


def _paint(dst, layer, col):
    """被覆率の層を色 col（ストレートの (r, g, b, a)、0..1）で dst へ over する。

    over は「dst + (色の事前乗算 − dst)·(被覆率·a)」の線形補間なので、広い範囲は
    cv2.blendLinear で行う（numpy の4チャンネルの放送より約3倍速い）。blendLinear は
    分母に 1e-5 を足すので、重みを 1024 倍して誤差を 1e-8 まで落とす。
    被覆率の画素が少ない層（細い線・散った点）は、触れた画素だけを numpy で更新する。
    """
    np = layer.np
    if layer.empty or col[3] <= 0:
        return dst
    cov = layer.cov
    dp = dst[layer.iy0:layer.iy1, layer.ix0:layer.ix1]
    flat = np.flatnonzero(cov)          # count_nonzero + nonzero の2回より速い
    nz = len(flat)
    if nz == 0:
        return dst
    if cov.size > 8192 and nz < cov.size // 8:     # 実測の分かれ目（numpy の飛び飛び更新 vs blendLinear）
        prem = _prem(col)
        idx = np.divmod(flat, cov.shape[1])
        c = cov[idx][:, None]
        dp[idx] = dp[idx] * (1.0 - c * prem[3]) + c * prem
        return dst
    cv2 = need("framekit").cv2
    w2 = cov * np.float32(col[3] * 1024.0)
    w1 = np.float32(1024.0) - w2
    solid = np.empty(dp.shape, np.float32)
    solid[...] = (col[0], col[1], col[2], 1.0)
    dp[...] = cv2.blendLinear(dp, solid, w1, w2)
    return dst


def _pixel_centers(np, cx0, cy0, cx1, cy1):
    xs = np.arange(cx0, cx1, dtype=np.float32) + np.float32(0.5)
    ys = np.arange(cy0, cy1, dtype=np.float32)[:, None] + np.float32(0.5)
    return xs, ys


# 線分の端の種類（_stroke_into が小片ごとに決める）
#   "join": 折れ線の頂点（丸い継ぎ目）  "cap": 道の端の丸い端（円弧のずれを直す）
#   "butt": 道の端の平らな端            "open": 1本の線分を小片に切った切れ目
#   "open" の外側は隣の小片の胴が受け持つので、この小片からは何も描かない（丸い端を付けると、
#   接線の半平面の近似が隣の胴のまっすぐな縁の外へ少しはみ出す）。
_END_KINDS = ("join", "cap", "butt", "open")


def _capsule_into(layer, a, b, hw, start="join", end="join"):
    """線分 a-b（幅 2·hw）の被覆率を層へ max で足す。端の形は start / end（_END_KINDS）。

    長さ 0 の線分（点）は全体を円として描く（円弧のずれを直す）。
    """
    np = layer.np
    pad = hw + 1.0
    r = layer.sub(min(a[0], b[0]) - pad, min(a[1], b[1]) - pad,
                  max(a[0], b[0]) + pad, max(a[1], b[1]) + pad)
    if r is None:
        return
    cx0, cy0, cx1, cy1 = r
    xs, ys = _pixel_centers(np, cx0, cy0, cx1, cy1)
    dx, dy = float(b[0] - a[0]), float(b[1] - a[1])
    L2 = dx * dx + dy * dy
    px = xs - np.float32(a[0])
    py = ys - np.float32(a[1])
    if L2 < 1e-12:
        dist = np.sqrt(px * px + py * py)
        ca, cb = _dir_ab(np, px, py, dist)
        c = _band_cov(np, hw, dist, ca, cb, radial=True)
        layer.put_max(c.astype(np.float32, copy=False), cx0, cy0)
        return
    t = (px * np.float32(dx / L2) + py * np.float32(dy / L2))
    tc = np.clip(t, 0.0, 1.0)
    qx = px - tc * np.float32(dx)
    qy = py - tc * np.float32(dy)
    dist = np.sqrt(qx * qx + qy * qy)
    ca, cb = _dir_ab(np, qx, qy, dist)
    before = t < -1e-6
    after = t > 1.0 + 1e-6
    radial = None
    if start == "cap" or end == "cap":
        radial = (before & (start == "cap")) | (after & (end == "cap"))
    c = _band_cov(np, hw, dist, ca, cb, radial=radial)
    if start == "butt" or end == "butt":
        L = math.sqrt(L2)
        ux, uy = dx / L, dy / L
        # 胴の縁と端の縁の向き（どちらも線分の向きの成分 → 同じ a・b）
        sa = np.float32(max(abs(ux), abs(uy)))
        sb = np.float32(min(abs(ux), abs(uy)))
        along = px * np.float32(ux) + py * np.float32(uy)
        perp = np.abs(py * np.float32(ux) - px * np.float32(uy))
        cp = _band_cov(np, hw, perp, sa, sb)
        if start == "butt":
            c = np.where(along < 0.75, cp * _edge_cov(np, along, sa, sb), c)
        if end == "butt":
            c = np.where(along > L - 0.75, cp * _edge_cov(np, np.float32(L) - along, sa, sb), c)
    if start == "open":
        c = np.where(before, np.float32(0.0), c)
    if end == "open":
        c = np.where(after, np.float32(0.0), c)
    layer.put_max(c.astype(np.float32, copy=False), cx0, cy0)


class _Pieces:
    """近くに集まった線分の小片を束ねて、1回の numpy 計算（_capsules_into）で層へ描く。

    1本の折れ線の小片だけでなく、破線の全部の区間（点線の点）も同じ束に入れられる
    （点線の点を1つずつ描くと、点 150 個で 1 コマ 25ms かかった）。
    """
    __slots__ = ("layer", "hw", "group", "gbox")

    def __init__(self, layer, hw):
        self.layer, self.hw = layer, hw
        self.group, self.gbox = [], None

    def add(self, pa, pb, k0, k1):
        if k0 == "butt" or k1 == "butt":
            _capsule_into(self.layer, pa, pb, self.hw, k0, k1)
            return
        box = (min(pa[0], pb[0]), min(pa[1], pb[1]), max(pa[0], pb[0]), max(pa[1], pb[1]))
        nb = box
        if self.gbox is not None:
            g = self.gbox
            nb = (min(g[0], box[0]), min(g[1], box[1]), max(g[2], box[2]), max(g[3], box[3]))
            if (nb[2] - nb[0] > 2 * _PIECE_LEN or nb[3] - nb[1] > 2 * _PIECE_LEN
                    or len(self.group) >= 32):
                self.flush()
                nb = box
        self.group.append((pa, pb, k0, k1))
        self.gbox = nb

    def flush(self):
        if len(self.group) == 1:
            g = self.group[0]
            _capsule_into(self.layer, g[0], g[1], self.hw, g[2], g[3])
        elif self.group:
            _capsules_into(self.layer, self.group, self.hw)
        self.group, self.gbox = [], None


def _stroke_into(layer, pts, closed, width, cap_start, cap_end, pieces=None):
    """折れ線を層へ描く（継ぎ目は丸。端は cap_* = 'round' / 'butt' / 'square'）。

    長さ 0 の線分（同じ点の重なり）は飛ばし、端の形は最初と最後の「長さのある」線分に
    掛ける（先頭に同じ点が2つあっても butt の端に丸い塊が出ない）。全部が同じ点なら
    round は点、square は一辺 width の正方形、butt は何も描かない（SVG と同じ）。
    長い線分は _PIECE_LEN 以下の小片に切り、近くに集まった小片は1回の numpy 計算にまとめる
    （小片の切れ目は "open": 隣の胴が受け持つ）。
    pieces: 呼び出し側の _Pieces（破線の区間をまとめて描くとき。最後の flush は呼び出し側）。
    """
    hw = width / 2.0
    p = [(float(q[0]), float(q[1])) for q in pts]
    if closed and len(p) >= 2 and p[0] != p[-1]:
        p.append(p[0])
    segs = [(p[i], p[i + 1]) for i in range(len(p) - 1)
            if math.hypot(p[i + 1][0] - p[i][0], p[i + 1][1] - p[i][1]) > 1e-9]
    if not segs:
        if p:
            if "round" in (cap_start, cap_end):
                _capsule_into(layer, p[0], p[0], hw)
            elif "square" in (cap_start, cap_end):
                x, y = p[0]
                _capsule_into(layer, (x - hw, y), (x + hw, y), hw, "butt", "butt")
        return
    own = pieces is None
    if own:
        pieces = _Pieces(layer, hw)
    n = len(segs)

    def end_kind(cap):
        return "cap" if cap == "round" else "butt"     # square は線分を伸ばして butt

    for k, (a, b) in enumerate(segs):
        first = (not closed) and k == 0
        last = (not closed) and k == n - 1
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        ux, uy = dx / L, dy / L
        if first and cap_start == "square":
            a = (a[0] - ux * hw, a[1] - uy * hw)
        if last and cap_end == "square":
            b = (b[0] + ux * hw, b[1] + uy * hw)
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        m = max(1, int(math.ceil(L / _PIECE_LEN)))
        for j in range(m):
            pa = (a[0] + (b[0] - a[0]) * j / m, a[1] + (b[1] - a[1]) * j / m)
            pb = (a[0] + (b[0] - a[0]) * (j + 1) / m, a[1] + (b[1] - a[1]) * (j + 1) / m)
            k0 = (end_kind(cap_start) if first else "join") if j == 0 else "open"
            k1 = (end_kind(cap_end) if last else "join") if j == m - 1 else "open"
            pieces.add(pa, pb, k0, k1)
    if own:
        pieces.flush()


def _nearest(np, A, B, xs, ys, kinds=None):
    """画素中心（xs: (w,)、ys: (h, 1)）から線分の束 A→B（(K, 2) の float64）の一番近い点への
    ベクトルと距離の2乗、丸い端の画素か (qx, qy, d2, radial)（どれも (h, w)）。

    縁の被覆率は一番近い線分の向きで求めるので、距離だけでなくベクトルも返す。
    kinds: 各線分の (始点の種類, 終点の種類)（_END_KINDS。butt は扱わない）。"open" の端の
    外側の画素はその線分を候補から外す（d2 = inf。隣の小片が受け持つ）。radial は一番近い点が
    "cap" の端の外側にある画素（円弧のずれを直す）。長さ 0 の線分は全部 radial。
    """
    ax = A[:, 0].astype(np.float32)[:, None, None]
    ay = A[:, 1].astype(np.float32)[:, None, None]
    ex = (B[:, 0] - A[:, 0]).astype(np.float32)[:, None, None]
    ey = (B[:, 1] - A[:, 1]).astype(np.float32)[:, None, None]
    el2 = ex * ex + ey * ey
    zero = el2 <= 1e-12
    el2 = np.where(zero, np.float32(1.0), el2)
    px = xs[None, None, :] - ax
    py = ys[None, :, :] - ay
    traw = (px * ex + py * ey) / el2
    t = np.clip(traw, 0.0, 1.0)
    qx = px - t * ex
    qy = py - t * ey
    d2 = qx * qx + qy * qy
    radial = np.broadcast_to(zero, d2.shape)
    if kinds is not None:
        before = traw < -1e-6
        after = traw > 1.0 + 1e-6

        def flag(i, kind):
            return np.array([k[i] == kind for k in kinds], bool)[:, None, None]
        o0, o1 = flag(0, "open"), flag(1, "open")
        if o0.any() or o1.any():
            d2 = np.where((before & o0) | (after & o1), np.float32(np.inf), d2)
        c0, c1 = flag(0, "cap"), flag(1, "cap")
        if c0.any() or c1.any():
            radial = (before & c0) | (after & c1) | zero
    if len(A) == 1:
        return qx[0], qy[0], d2[0], np.broadcast_to(radial, d2.shape)[0]
    k = d2.argmin(axis=0)[None]
    return (np.take_along_axis(qx, k, 0)[0], np.take_along_axis(qy, k, 0)[0],
            np.take_along_axis(d2, k, 0)[0],
            np.take_along_axis(np.broadcast_to(radial, d2.shape), k, 0)[0])


def _capsules_into(layer, segs, hw):
    """丸い端の線分の束（近くに集まった短い線分）を1回の計算で層へ max で足す。

    segs: [(a, b)] か [(a, b, 始点の種類, 終点の種類)]（_END_KINDS。butt は不可）。
    """
    np = layer.np
    A = np.array([s[0] for s in segs], np.float64)
    B = np.array([s[1] for s in segs], np.float64)
    kinds = [(s[2], s[3]) if len(s) > 2 else ("join", "join") for s in segs]
    pad = hw + 1.0
    r = layer.sub(min(A[:, 0].min(), B[:, 0].min()) - pad, min(A[:, 1].min(), B[:, 1].min()) - pad,
                  max(A[:, 0].max(), B[:, 0].max()) + pad, max(A[:, 1].max(), B[:, 1].max()) + pad)
    if r is None:
        return
    cx0, cy0, cx1, cy1 = r
    xs, ys = _pixel_centers(np, cx0, cy0, cx1, cy1)
    qx, qy, d2, radial = _nearest(np, A, B, xs, ys, kinds)
    dist = np.sqrt(np.minimum(d2, np.float32(1e6)))        # 候補の無い画素（inf）は遠くへ
    ca, cb = _dir_ab(np, qx, qy, dist)
    c = _band_cov(np, hw, dist, ca, cb, radial=radial)
    layer.put_max(c.astype(np.float32, copy=False), cx0, cy0)


def _piece_groups(edges):
    """線分の列を _PIECE_LEN 以下の小片に切り、近くに集まった小片の束（最大 32 本・外接矩形の
    一辺が 2·_PIECE_LEN 以下）に分ける。[[(a, b), ...], ...] を返す（計算する画素を
    「縁の長さ × 数 px」に抑えるため。長い斜めの辺でも外接矩形の全画素は計算しない）"""
    groups, group, gbox = [], [], None
    for a, b in edges:
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L <= 1e-9:
            continue
        m = max(1, int(math.ceil(L / _PIECE_LEN)))
        for j in range(m):
            pa = (a[0] + (b[0] - a[0]) * j / m, a[1] + (b[1] - a[1]) * j / m)
            pb = (a[0] + (b[0] - a[0]) * (j + 1) / m, a[1] + (b[1] - a[1]) * (j + 1) / m)
            box = (min(pa[0], pb[0]), min(pa[1], pb[1]), max(pa[0], pb[0]), max(pa[1], pb[1]))
            nb = box
            if gbox is not None:
                nb = (min(gbox[0], box[0]), min(gbox[1], box[1]),
                      max(gbox[2], box[2]), max(gbox[3], box[3]))
                if (nb[2] - nb[0] > 2 * _PIECE_LEN or nb[3] - nb[1] > 2 * _PIECE_LEN
                        or len(group) >= 32):
                    groups.append(group)
                    group, nb = [], box
            group.append((pa, pb))
            gbox = nb
    if group:
        groups.append(group)
    return groups


_DOT_DASH_LEN = 1e-4   # 長さ 0 の破線を「進行方向へごく短い線分」で表す長さ（px）


def _dash_paths(pts, closed, dash, offset, keep_zero=False):
    """折れ線を弧長で破線に切る。[(点の列), ...]（各々は開いた折れ線）を返す。

    keep_zero=True: 長さ 0 の線（dash=(0, 隙間) で点線を描く SVG の定番の書き方）を、
    その位置で進行方向を向いた長さ _DOT_DASH_LEN の線分として残す（丸い端なら点、
    square なら進行方向を向いた正方形になる）。butt では捨てる（SVG と同じく何も描かない）。
    """
    pattern = [float(v) for v in dash]
    if len(pattern) % 2:
        pattern = pattern * 2
    period = sum(pattern)
    p = [(float(q[0]), float(q[1])) for q in pts]
    if closed and len(p) >= 2 and p[0] != p[-1]:
        p.append(p[0])
    cum = [0.0]
    for i in range(len(p) - 1):
        cum.append(cum[-1] + math.hypot(p[i + 1][0] - p[i][0], p[i + 1][1] - p[i][1]))
    total = cum[-1]
    if total <= 0:
        return []

    def point_at(s):
        # s の位置の点と、その点が乗る線分の番号
        lo, hi = 0, len(cum) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if cum[mid] <= s:
                lo = mid
            else:
                hi = mid
        seg_len = cum[lo + 1] - cum[lo]
        t = 0.0 if seg_len <= 0 else (s - cum[lo]) / seg_len
        a, b = p[lo], p[lo + 1]
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), lo

    def direction_at(i):
        # 線分 i の向き（長さ 0 なら前後の長さのある線分）
        for j in list(range(i, len(p) - 1)) + list(range(i - 1, -1, -1)):
            L = cum[j + 1] - cum[j]
            if L > 0:
                return (p[j + 1][0] - p[j][0]) / L, (p[j + 1][1] - p[j][1]) / L
        return 1.0, 0.0

    # 区間の列（on の区間だけ）
    intervals = []
    s = -(float(offset) % period)
    k = 0
    while s <= total:
        length = pattern[k % len(pattern)]
        if k % 2 == 0:
            s0, s1 = max(s, 0.0), min(s + length, total)
            if s1 > s0:
                intervals.append((s0, s1))
            elif keep_zero and length == 0 and s >= 0:
                intervals.append((s, s))
        if s >= total:
            break
        s += length
        k += 1
    out = []
    for s0, s1 in intervals:
        if s1 == s0:
            (x, y), i = point_at(s0)
            ux, uy = direction_at(i)
            e = _DOT_DASH_LEN / 2
            out.append([(x - ux * e, y - uy * e), (x + ux * e, y + uy * e)])
            continue
        a, ia = point_at(s0)
        b, ib = point_at(s1)
        path = [a] + [p[i] for i in range(ia + 1, ib + 1) if cum[i] > s0 and cum[i] < s1] + [b]
        out.append(path)
    return out


def _check_dash(fn, dash):
    if dash is None:
        return None
    if not isinstance(dash, (list, tuple)) or not dash:
        raise ValueError(f"{fn}: dash は (線の長さ, 隙間の長さ) で指定してください: {dash!r}")
    for i, v in enumerate(dash):
        _num(fn, f"dash[{i}]", v, 0)
    if sum(dash) <= 0:
        raise ValueError(f"{fn}: dash の長さの合計が 0 です: {dash!r}")
    return dash


def _path_array(fn, pts):
    np = need(fn).np
    arr = np.asarray(pts, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise ValueError(f"{fn}: 点の列は [(x, y), ...] で指定してください: 形 {arr.shape}")
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{fn}: 点の座標に NaN / Infinity があります")
    return arr


_CAPS = ("round", "butt", "square")


def polyline(dst, pts, rgba, width, *, closed=False, dash=None, cap="round", dash_offset=0.0):
    """折れ線を描く（幅 width px・継ぎ目は丸）。

    pts: [(x, y), ...]（連続座標）。closed=True で始点へ閉じる。
    dash: (線, 隙間[, 線, 隙間 …]) の長さ px。弧長で切る（角をまたいでも長さが保たれる）。
      dash_offset で模様をずらす（流れる破線）。線の長さ 0（dash=(0, 8) など）は、
      cap が round なら点・square なら正方形になる（SVG と同じ点線の書き方。butt では描かない）。
    cap: 'round'（既定）/ 'butt'（端で平らに切る）/ 'square'（幅の半分だけ伸ばして平ら）。
    別の層（被覆率）へ描いてから1回だけ over する。dst を書き換えて返す。
    """
    np = _check_canvas("polyline", dst)
    arr = _path_array("polyline", pts)
    width = _num("polyline", "width", width, 0)
    if cap not in _CAPS:
        raise ValueError(f"polyline: cap は {_CAPS} のいずれか: {cap!r}{_suggest_hint(cap, _CAPS)}")
    dash = _check_dash("polyline", dash)
    dash_offset = _num("polyline", "dash_offset", dash_offset)
    if width <= 0 or len(arr) == 0:
        return dst
    col = _rgba_arg("polyline", rgba)
    pad = width / 2.0 + 1.5
    layer = _cov_for(np, dst, arr[:, 0].min() - pad, arr[:, 1].min() - pad,
                     arr[:, 0].max() + pad, arr[:, 1].max() + pad)
    if layer.empty:
        return dst
    if dash is None:
        _stroke_into(layer, arr, closed, float(width), cap, cap)
    else:
        pieces = _Pieces(layer, float(width) / 2.0)      # 破線の区間はまとめて描く
        for path in _dash_paths(arr, closed, dash, dash_offset, keep_zero=cap != "butt"):
            _stroke_into(layer, path, False, float(width), cap, cap, pieces)
        pieces.flush()
    return _paint(dst, layer, col)


def _scan_inside(np, rings, cx0, cy0, w, h):
    """画素中心が偶奇規則で多角形の内側にあるか（(h, w) の bool）。

    行ごとに「画素中心の高さの水平線」と辺の交点を求め、交点より右の画素で内外を反転する
    （交点の数え方は (ay > y) != (by > y) の半開区間。頂点を通る線でも二重に数えない）。
    cv2.fillPoly は縁の画素を含めて塗る（縁から 1px 近く外の画素まで内側になる）ので使わない。
    """
    A = np.concatenate(list(rings))
    B = np.concatenate([np.roll(ring, -1, axis=0) for ring in rings])
    ax, ay, bx, by = A[:, 0], A[:, 1], B[:, 0], B[:, 1]
    ex, ey = bx - ax, by - ay
    rows = np.arange(h, dtype=np.float64) + (cy0 + 0.5)
    flat = []
    step = max(1, _CHUNK // max(1, len(A)))
    for r0 in range(0, h, step):
        py = rows[r0:r0 + step][:, None]
        ri, ei = np.nonzero((ay > py) != (by > py))
        if not len(ri):
            continue
        xint = ax[ei] + (py[ri, 0] - ay[ei]) * ex[ei] / ey[ei]
        # 中心 x + 0.5 が交点以上の画素（x ≥ xint - 0.5）から反転する
        idx = np.clip(np.ceil(xint - 0.5 - cx0), 0, w).astype(np.int64)
        flat.append((ri + r0) * (w + 1) + idx)
    if not flat:
        return np.zeros((h, w), bool)
    marks = np.bincount(np.concatenate(flat), minlength=h * (w + 1)).reshape(h, w + 1)
    return (np.cumsum(marks[:, :w], axis=1) & 1).astype(bool)


def _poly_into(layer, polys):
    """多角形（偶奇規則。輪の並び）の被覆率を層へ max で足す。

    内外は画素中心ごとに正確に数える（_scan_inside。辺の数 × 行の数）。縁から 1.5px 以内の
    画素だけ、一番近い縁までの距離と向きから面積の被覆率（_edge_cov）を求める。距離は辺を
    小片に切った束ごとに「束の外接矩形 + 2px」の画素だけで計算する（縁の長さに比例。
    頂点の数 × 縁の画素にはならない）。
    """
    np = layer.np
    rings = [np.asarray(r, dtype=np.float64) for r in polys if len(r) >= 3]
    if not rings:
        return
    allp = np.concatenate(rings)
    r = layer.sub(allp[:, 0].min() - 2, allp[:, 1].min() - 2,
                  allp[:, 0].max() + 2, allp[:, 1].max() + 2)
    if r is None:
        return
    cx0, cy0, cx1, cy1 = r
    h, w = cy1 - cy0, cx1 - cx0
    inside = _scan_inside(np, rings, cx0, cy0, w, h)
    cov = inside.astype(np.float32)
    # 一番近い縁までの距離の2乗とベクトル（縁の近くの画素だけ埋まる）
    best = np.full((h, w), np.inf, np.float32)
    bqx = np.zeros((h, w), np.float32)
    bqy = np.zeros((h, w), np.float32)
    edges = []
    for ring in rings:
        pts = [(float(q[0]), float(q[1])) for q in ring]
        edges.extend((pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts)))
    for group in _piece_groups(edges):
        A = np.array([g[0] for g in group], np.float64)
        B = np.array([g[1] for g in group], np.float64)
        gx0 = max(cx0, int(math.floor(min(A[:, 0].min(), B[:, 0].min()) - 2.0)))
        gy0 = max(cy0, int(math.floor(min(A[:, 1].min(), B[:, 1].min()) - 2.0)))
        gx1 = min(cx1, int(math.ceil(max(A[:, 0].max(), B[:, 0].max()) + 2.0)))
        gy1 = min(cy1, int(math.ceil(max(A[:, 1].max(), B[:, 1].max()) + 2.0)))
        if gx0 >= gx1 or gy0 >= gy1:
            continue
        xs, ys = _pixel_centers(np, gx0, gy0, gx1, gy1)
        qx, qy, d2, _radial = _nearest(np, A, B, xs, ys)
        sl = (slice(gy0 - cy0, gy1 - cy0), slice(gx0 - cx0, gx1 - cx0))
        m = d2 < best[sl]
        best[sl][m] = d2[m]
        bqx[sl][m] = qx[m]
        bqy[sl][m] = qy[m]
    ys, xs = np.nonzero(best < 2.25)
    if len(ys):
        dist = np.sqrt(best[ys, xs])
        a, b = _dir_ab(np, bqx[ys, xs], bqy[ys, xs], dist)
        sd = np.where(inside[ys, xs], -dist, dist)
        cov[ys, xs] = _edge_cov(np, -sd, a, b)
    layer.put_max(cov, cx0, cy0)


def polygon(dst, pts, rgba):
    """多角形を塗る（偶奇規則）。pts は [(x, y), ...] か、その輪のリスト（穴あき）。"""
    np = _check_canvas("polygon", dst)
    rings = _rings("polygon", pts)
    if not rings:
        return dst
    col = _rgba_arg("polygon", rgba)
    allp = np.concatenate(rings)
    layer = _cov_for(np, dst, allp[:, 0].min() - 2, allp[:, 1].min() - 2,
                     allp[:, 0].max() + 2, allp[:, 1].max() + 2)
    if layer.empty:
        return dst
    _poly_into(layer, rings)
    return _paint(dst, layer, col)


def _rings(fn, pts):
    np = need(fn).np
    if len(pts) == 0:
        return []
    first = pts[0]
    is_ring_list = (hasattr(first, "__len__") and len(first) > 0
                    and hasattr(first[0], "__len__"))
    rings = [_path_array(fn, r) for r in (pts if is_ring_list else [pts])]
    return [np.asarray(r) for r in rings if len(r) >= 3]


def _box_layer(np, dst, x0, y0, x1, y1):
    """軸に平行な矩形の正確な被覆率（縦横の積）"""
    layer = _cov_for(np, dst, x0, y0, x1, y1)
    if not layer.empty:
        layer.cov[...] = (_axis_cov(np, y0, y1, layer.iy0, layer.iy1)[:, None]
                          * _axis_cov(np, x0, x1, layer.ix0, layer.ix1)[None, :])
    return layer


def _round_box_layer(np, dst, x0, y0, x1, y1, radius, hw):
    """角丸矩形の被覆率（符号付き距離から直接）。hw=None で塗り、数値なら枠線（中心線は縁）"""
    ext = (hw or 0.0) + 1.5
    layer = _cov_for(np, dst, x0 - ext, y0 - ext, x1 + ext, y1 + ext)
    if layer.empty:
        return layer
    xs, ys = _pixel_centers(np, layer.ix0, layer.iy0, layer.ix1, layer.iy1)
    hx, hy = (x1 - x0) / 2.0, (y1 - y0) / 2.0
    r = max(0.0, min(float(radius), hx, hy))
    qx = np.abs(xs - np.float32((x0 + x1) / 2.0)) - np.float32(hx - r)
    qy = np.abs(ys - np.float32((y0 + y1) / 2.0)) - np.float32(hy - r)
    mx, my = np.maximum(qx, 0.0), np.maximum(qy, 0.0)
    md = np.sqrt(mx * mx + my * my)
    sd = md + np.minimum(np.maximum(qx, qy), 0.0) - np.float32(r)
    # 縁の向き: 角の弧の外側は中心からの向き、それ以外は軸に平行（a=1, b=0）
    a, b = _dir_ab(np, mx, my, md)
    if hw is None:
        layer.cov[...] = _edge_cov(np, -sd, a, b)
    else:
        layer.cov[...] = _band_cov(np, hw, np.abs(sd), a, b)
    return layer


def _round_rect_path(x0, y0, x1, y1, radius):
    """角丸矩形の中心線（時計回り。上辺の左端から）。radius=0 なら4隅だけ"""
    r = max(0.0, min(float(radius), (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    if r <= 0:
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    n = max(3, int(math.ceil(r * math.pi / 2 / 2.0)))   # 弧を約 2px ごとに刻む
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90.0), (x1 - r, y1 - r, 0.0),
                       (x0 + r, y1 - r, 90.0), (x0 + r, y0 + r, 180.0)):
        for k in range(n + 1):
            a = math.radians(a0 + 90.0 * k / n)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def rect(dst, box, rgba, *, width=None, radius=0, dash=None, dash_offset=0.0):
    """矩形。box=(x0, y0, x1, y1)（連続座標）。

    width=None で塗り（角丸 radius）。width を渡すと枠線（box の縁が線の中心）。
    角丸でも点線でもない枠は外側の矩形 − 内側の矩形で正確に描く（角は直角）。
    """
    np = _check_canvas("rect", dst)
    x0, y0, x1, y1 = _coords("rect", "box", box, 4, "(x0, y0, x1, y1)")
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    radius = _num("rect", "radius", radius, 0)
    dash = _check_dash("rect", dash)
    col = _rgba_arg("rect", rgba)
    if width is None:
        if dash is not None:
            raise ValueError("rect: dash は枠線（width を指定）のときだけ使えます")
        if radius <= 0:
            return _paint(dst, _box_layer(np, dst, x0, y0, x1, y1), col)
        return _paint(dst, _round_box_layer(np, dst, x0, y0, x1, y1, radius, None),
                      col)
    width = _num("rect", "width", width, 0)
    if width <= 0:
        return dst
    hw = width / 2.0
    if radius > 0 and dash is None:
        return _paint(dst, _round_box_layer(np, dst, x0, y0, x1, y1, radius, hw),
                      col)
    if radius <= 0 and dash is None:
        outer = _box_layer(np, dst, x0 - hw, y0 - hw, x1 + hw, y1 + hw)
        if outer.empty:
            return dst
        if x1 - x0 > width and y1 - y0 > width:
            ix0, ix1 = outer.ix0, outer.ix1
            iy0, iy1 = outer.iy0, outer.iy1
            inner = (_axis_cov(np, y0 + hw, y1 - hw, iy0, iy1)[:, None]
                     * _axis_cov(np, x0 + hw, x1 - hw, ix0, ix1)[None, :])
            outer.cov -= inner
            np.clip(outer.cov, 0.0, 1.0, out=outer.cov)
        return _paint(dst, outer, col)
    path = _round_rect_path(x0, y0, x1, y1, radius)
    return polyline(dst, path, tuple(int(round(v * 255)) for v in col), width,
                    closed=True, dash=dash, dash_offset=dash_offset)


def hatch(dst, box, rgba, *, spacing=10, angle=45, width=2, offset=0.0):
    """box の中を平行線で塗る（斜線の網掛け）。

    spacing: 線の間隔 px（線に垂直に測る）。angle: 線の向き（度。反時計回り。45 で「／」）。
    width: 線の幅 px。offset: 模様を線に垂直にずらす px（流れる網掛け）。
    模様は box の左上を原点にする（box を動かすと模様も一緒に動く）。縁は box の被覆率で切る。
    """
    np = _check_canvas("hatch", dst)
    x0, y0, x1, y1 = _coords("hatch", "box", box, 4, "(x0, y0, x1, y1)")
    spacing = _num("hatch", "spacing", spacing, 0)
    if spacing <= 0:
        raise ValueError(f"hatch: spacing は 0 より大きくしてください: {spacing!r}")
    angle = _num("hatch", "angle", angle)
    width = _num("hatch", "width", width, 0)
    offset = _num("hatch", "offset", offset)
    col = _rgba_arg("hatch", rgba)
    layer = _box_layer(np, dst, min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    if layer.empty or width <= 0:
        return dst
    xs, ys = _pixel_centers(np, layer.ix0, layer.iy0, layer.ix1, layer.iy1)
    th = math.radians(float(angle))
    # 線の向き u = (cos, -sin)（画面は y が下向き）、法線 n = (sin, cos)
    nx, ny = math.sin(th), math.cos(th)
    s = (xs - np.float32(min(x0, x1))) * np.float32(nx) + (ys - np.float32(min(y0, y1))) * np.float32(ny)
    s = s - np.float32(offset)
    sp = np.float32(spacing)
    d = np.abs(s - sp * np.floor(s / sp + np.float32(0.5)))
    a = np.float32(max(abs(nx), abs(ny)))
    b = np.float32(min(abs(nx), abs(ny)))
    stripes = _band_cov(np, width / 2.0, d, a, b)
    if width / 2.0 + 0.75 > spacing / 2.0:
        # 線の幅が間隔に近いと、隣の線の縁も同じ画素を横切る
        stripes = np.minimum(stripes + _band_cov(np, width / 2.0, sp - d, a, b), 1.0)
    layer.cov *= stripes
    return _paint(dst, layer, col)


def circle(dst, center, r, rgba, *, width=None, dash=None, dash_offset=0.0):
    """円。width=None で塗り、width を渡すと円周の線（半径 r が線の中心）。

    塗りと実線は中心からの距離と向きから直接求める（面積の被覆率。円弧のずれ 1/(24r) を直す
    ので、半径 2px の円でも面積の偏りは 0.05% 未満）。実線は外側の円 − 内側の円。
    dash は円周の折れ線で描く。
    """
    np = _check_canvas("circle", dst)
    cx, cy = _coords("circle", "center", center, 2, "(x, y)")
    r = _num("circle", "r", r, 0)
    dash = _check_dash("circle", dash)
    col = _rgba_arg("circle", rgba)
    if width is not None:
        width = _num("circle", "width", width, 0)
        if width <= 0:
            return dst
        if dash is not None:
            n = max(24, int(math.ceil(2 * math.pi * r / 2.0)))
            pts = [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n))
                   for k in range(n)]
            return polyline(dst, pts, tuple(int(round(v * 255)) for v in col), width,
                            closed=True, dash=dash, dash_offset=dash_offset)
    ext = r + (width / 2.0 if width else 0.0) + 1.5
    layer = _cov_for(np, dst, cx - ext, cy - ext, cx + ext, cy + ext)
    if layer.empty:
        return dst
    xs, ys = _pixel_centers(np, layer.ix0, layer.iy0, layer.ix1, layer.iy1)
    vx = xs - np.float32(cx)
    vy = ys - np.float32(cy)
    d = np.sqrt(vx * vx + vy * vy)
    a, b = _dir_ab(np, vx, vy, d)             # 縁の向き（中心からの向き）
    if width is None:
        layer.cov[...] = _fill_circle_cov(np, r, d, a, b)
    else:
        # 円周の線 = 外側の円 − 内側の円（どちらも円弧のずれを直す）
        ro, ri = r + width / 2.0, r - width / 2.0
        layer.cov[...] = np.clip(_fill_circle_cov(np, ro, d, a, b)
                                 - _fill_circle_cov(np, ri, d, a, b), 0.0, 1.0)
    return _paint(dst, layer, col)


def _bezier2(p0, c, p1, n):
    pts = []
    for k in range(n + 1):
        t = k / n
        a, b, d = (1 - t) ** 2, 2 * (1 - t) * t, t * t
        pts.append((a * p0[0] + b * c[0] + d * p1[0], a * p0[1] + b * c[1] + d * p1[1]))
    return pts


def _cut_path(pts, length):
    """折れ線を始点から弧長 length のところで切る"""
    out = [pts[0]]
    acc = 0.0
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        if acc + seg >= length:
            t = 0.0 if seg <= 0 else (length - acc) / seg
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            return out
        out.append(b)
        acc += seg
    return out


def arrow(dst, p0, p1, rgba, width, *, head=14, curve=0.0, dash=None, dash_offset=0.0):
    """矢印 p0 → p1（先端が p1）。

    width: 軸の幅 px。head: 矢じりの長さ px（幅も同じ。0 で矢じり無し）。
    curve: 曲がり（2次ベジェ。制御点を中点から 長さ×curve だけ進行方向の左へ出す。負で右）。
    dash: 軸を破線にする（矢じりは塗り）。軸と矢じりは同じ層へ描いてから1回だけ over する
    （半透明でも重なりが濃くならない）。軸は矢じりの中で平らに終わる（先端から突き出さない）。
    """
    np = _check_canvas("arrow", dst)
    width = _num("arrow", "width", width, 0)
    head = _num("arrow", "head", head, 0)
    curve = _num("arrow", "curve", curve)
    dash = _check_dash("arrow", dash)
    dash_offset = _num("arrow", "dash_offset", dash_offset)
    col = _rgba_arg("arrow", rgba)
    a = _coords("arrow", "p0", p0, 2, "(x, y)")
    b = _coords("arrow", "p1", p1, 2, "(x, y)")
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    if L < 1e-6:
        return dst
    if curve:
        nx, ny = (b[1] - a[1]) / L, -(b[0] - a[0]) / L        # 進行方向の左（画面）
        c = ((a[0] + b[0]) / 2 + nx * curve * L, (a[1] + b[1]) / 2 + ny * curve * L)
        n = max(8, int(math.ceil(L * (1 + 2 * abs(curve)) / 4.0)))
        path = _bezier2(a, c, b, n)
    else:
        path = [a, b]
    total = sum(math.hypot(path[i + 1][0] - path[i][0], path[i + 1][1] - path[i][1])
                for i in range(len(path) - 1))
    ext = max(width, head) + 2.0
    xs = [q[0] for q in path]
    ys = [q[1] for q in path]
    layer = _cov_for(np, dst, min(xs) - ext, min(ys) - ext, max(xs) + ext, max(ys) + ext)
    if layer.empty:
        return dst
    head = float(min(head, total))
    if head > 0:
        # 矢じりの向き: 先端から head だけ戻った点 → 先端
        back = _cut_path(path, max(0.0, total - head))[-1]
        ux, uy = b[0] - back[0], b[1] - back[1]
        ul = math.hypot(ux, uy) or 1.0
        ux, uy = ux / ul, uy / ul
        base = (b[0] - ux * head, b[1] - uy * head)
        half = head * 0.5
        tri = [b, (base[0] - uy * half, base[1] + ux * half),
               (base[0] + uy * half, base[1] - ux * half)]
        _poly_into(layer, [tri])
        shaft = _cut_path(path, max(0.0, total - head * 0.5))
    else:
        shaft = path
    if width > 0 and len(shaft) >= 2:
        if dash is None:
            _stroke_into(layer, shaft, False, float(width), "round",
                         "butt" if head > 0 else "round")
        else:
            pieces = _Pieces(layer, float(width) / 2.0)
            for piece in _dash_paths(shaft, False, dash, dash_offset, keep_zero=True):
                _stroke_into(layer, piece, False, float(width), "round", "round", pieces)
            pieces.flush()
    return _paint(dst, layer, col)


# --- 点の一括描画 ---

_STAMP_MEMO = {}
_STAMP_PHASES = 4

# r がこれ未満の点は「双線形の bincount + 小さい円の畳み込み」、以上は端数位相のスタンプ
_SMALL_DOT_R = 1.5


def _disc_cov(np, r, soft, dx, dy):
    """中心からのずれ (dx, dy)（画素の添字の座標）の画素が、縁のぼけた円に覆われる割合
    （1画素を 4×4 で標本化）。dx / dy は同じ形の配列"""
    sub = (np.arange(4, dtype=np.float64) + 0.5) / 4 - 0.5
    ddx = dx[..., None, None] + sub[None, :]
    ddy = dy[..., None, None] + sub[:, None]
    d = np.sqrt(ddx * ddx + ddy * ddy)
    return np.clip((r - d) / soft + 0.5, 0.0, 1.0).mean(axis=(-2, -1))


def _fit_mass(np, v, target):
    """被覆率 v の総和を target に合わせる。中まで塗られた画素（1）は触らず、
    縁の画素だけで合わせる（1 を超えない）"""
    full = v >= 1.0 - 1e-9
    edge = (v > 0) & ~full
    es = v[edge].sum()
    if es > 0:
        v[edge] *= (target - v[full].sum()) / es
    elif v.sum() > 0:
        v *= target / v.sum()
    np.clip(v, 0.0, 1.0, out=v)


def _stamps(np, r, soft):
    """端数位相 4×4（+ 次の画素ぶんの1列）の円のスタンプ。

    戻り値: (stamps (P+1, P+1, S*S) float32, ox (S*S,), oy (S*S,))。
    stamps[py][px] は中心が基準画素の中心から (px/P, py/P) 画素ずれた円の被覆率。
    どの位相も総和（＝α の量）を円の面積 πr² に揃える。
    """
    key = ("stamp", round(float(r), 4), round(float(soft), 4))
    hit = _STAMP_MEMO.get(key)
    if hit is not None:
        return hit
    P = _STAMP_PHASES
    R = int(math.ceil(r + soft / 2.0)) + 1
    S = 2 * R + 2
    j = np.arange(S, dtype=np.float64) - R
    stamps = np.empty((P + 1, P + 1, S * S), np.float64)
    target = math.pi * r * r
    for py in range(P + 1):
        for px in range(P + 1):
            dx = np.broadcast_to(j[None, :] - px / P, (S, S))
            dy = np.broadcast_to(j[:, None] - py / P, (S, S))
            v = _disc_cov(np, r, soft, dx, dy).ravel()
            _fit_mass(np, v, target)
            stamps[py, px] = v
    oy, ox = np.divmod(np.arange(S * S), S)
    out = (stamps.astype(np.float32), (ox - R).astype(np.int64), (oy - R).astype(np.int64))
    if len(_STAMP_MEMO) > 64:
        _STAMP_MEMO.clear()
    _STAMP_MEMO[key] = out
    return out


def _small_kernel(np, r, soft):
    """小さい点の畳み込み核（中心の画素に置いた円。総和は πr²。r が小さいと1画素だけ）"""
    key = ("kernel", round(float(r), 4), round(float(soft), 4))
    hit = _STAMP_MEMO.get(key)
    if hit is not None:
        return hit
    R = max(0, int(math.ceil(r + soft / 2.0 - 0.5)))
    j = np.arange(2 * R + 1, dtype=np.float64) - R
    dx = np.broadcast_to(j[None, :], (2 * R + 1, 2 * R + 1))
    dy = np.broadcast_to(j[:, None], (2 * R + 1, 2 * R + 1))
    v = _disc_cov(np, r, soft, dx, dy).ravel()
    _fit_mass(np, v, math.pi * r * r)
    out = (v.reshape(2 * R + 1, 2 * R + 1).astype(np.float32), R)
    if len(_STAMP_MEMO) > 64:
        _STAMP_MEMO.clear()
    _STAMP_MEMO[key] = out
    return out


def dots(dst, xy, rgba, r, *, soft=0.8):
    """N 個の点（円）を一括で描く。

    xy: (N, 2) の中心（連続座標）。rgba: 全点共通の (r, g, b[, a])（0..255）・パレットの
      名前か、点ごとの (N, 4) 配列（0..255）。r: 半径 px（全点共通）。
    soft: 縁のぼかし幅 px（被覆率が 0→1 に変わる幅）。
    どの r でも α の量は「円の面積 πr² × α」で、位置に依らず一定（半径を連続に変えても
    明るさが跳ねない）。
    r < 1.5（粒子雲向け）: 各点を双線形で4画素へ配り（bincount）、半径 r の小さい円で
      畳み込む（点の数に依らず速い）。
    r ≥ 1.5: 端数位相 4×4 の事前計算スタンプを、隣り合う位相の双線形で混ぜて bincount で
      足す（0.1px ずつ動かしても重心が連続に動く）。
    点どうしは足し合わせ（α は 1 で頭打ち）、まとめて1回 over する。
    np.add.at は遅いので使わない。
    """
    np = _check_canvas("dots", dst)
    xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
    N = len(xy)
    if N == 0:
        return dst
    r = _num("dots", "r", r, 0)
    soft = _num("dots", "soft", soft, 0, 20)
    if r <= 0:
        return dst
    if soft <= 0:
        raise ValueError(f"dots: soft は 0 より大きくしてください: {soft!r}")
    ok = np.all(np.isfinite(xy), axis=1)
    rgba_arr = np.asarray(rgba, dtype=np.float32) if not isinstance(rgba, str) else None
    if rgba_arr is not None and rgba_arr.ndim == 2:
        if rgba_arr.shape != (N, 4):
            raise ValueError(f"dots: 点ごとの色は (N, 4) で渡してください: 形 {rgba_arr.shape}")
        per = np.clip(rgba_arr, 0, 255) / np.float32(255.0)
        per = per[ok]
        col = None
    else:
        col = _rgba_arg("dots", rgba)
        per = None
    xy = xy[ok]
    if len(xy) == 0:
        return dst
    H, W = dst.shape[:2]
    fx = xy[:, 0] - 0.5        # 画素の添字の座標（中心が整数）
    fy = xy[:, 1] - 0.5
    ix = np.floor(fx).astype(np.int64)
    iy = np.floor(fy).astype(np.int64)
    small = r < _SMALL_DOT_R
    if small:
        kernel, KR = _small_kernel(np, float(r), float(soft))
        wx = (fx - ix).astype(np.float32)
        wy = (fy - iy).astype(np.float32)
        ox = np.array([0, 1, 0, 1], np.int64)
        oy = np.array([0, 0, 1, 1], np.int64)
        wts = np.stack([(1 - wx) * (1 - wy), wx * (1 - wy), (1 - wx) * wy, wx * wy], axis=1)
        ext = KR + 1
    else:
        stamps, ox, oy = _stamps(np, float(r), float(soft))
        P = _STAMP_PHASES
        qx = (fx - ix) * P
        qy = (fy - iy) * P
        kx = np.minimum(np.floor(qx).astype(np.int64), P - 1)
        ky = np.minimum(np.floor(qy).astype(np.int64), P - 1)
        tx = (qx - kx).astype(np.float32)[:, None]
        ty = (qy - ky).astype(np.float32)[:, None]
        wts = None
        ext = int(-ox.min()) + 1
    # 描く範囲（点の外接矩形 ∩ dst）
    bx0 = max(0, int(ix.min()) - ext)
    by0 = max(0, int(iy.min()) - ext)
    bx1 = min(W, int(ix.max()) + ext + 2)
    by1 = min(H, int(iy.max()) + ext + 2)
    if bx0 >= bx1 or by0 >= by1:
        return dst
    # 集計する範囲。小さい点は畳み込みで外から入ってくる分も拾うよう、核の半径だけ広げる
    pad = KR if small else 0
    gx0, gy0, gx1, gy1 = bx0 - pad, by0 - pad, bx1 + pad, by1 + pad
    bw, bh = gx1 - gx0, gy1 - gy0
    n_pix = bw * bh
    K = len(ox)
    alpha_pt = (per[:, 3] if per is not None else np.full(len(xy), col[3], np.float32))
    acc = np.zeros((4 if per is not None else 1, n_pix), np.float64)
    step = max(1, _CHUNK // K)
    for s in range(0, len(xy), step):
        e = s + step
        X = ix[s:e, None] + ox[None, :]
        Y = iy[s:e, None] + oy[None, :]
        if wts is not None:
            Wt = wts[s:e]
        else:
            Wt = ((1 - tx[s:e]) * (1 - ty[s:e]) * stamps[ky[s:e], kx[s:e]]
                  + tx[s:e] * (1 - ty[s:e]) * stamps[ky[s:e], kx[s:e] + 1]
                  + (1 - tx[s:e]) * ty[s:e] * stamps[ky[s:e] + 1, kx[s:e]]
                  + tx[s:e] * ty[s:e] * stamps[ky[s:e] + 1, kx[s:e] + 1])
        Wt = Wt * alpha_pt[s:e, None]
        valid = (X >= gx0) & (X < gx1) & (Y >= gy0) & (Y < gy1)
        idx = ((Y - gy0) * bw + (X - gx0))[valid]
        wv = Wt[valid]
        acc[0 if per is None else 3] += np.bincount(idx, weights=wv, minlength=n_pix)
        if per is not None:
            for ch in range(3):
                wc = (Wt * per[s:e, ch:ch + 1])[valid]
                acc[ch] += np.bincount(idx, weights=wc, minlength=n_pix)
    if small:
        cv2 = need("dots").cv2
        planes = acc.astype(np.float32).reshape(len(acc), bh, bw)
        planes = [cv2.filter2D(ch, -1, kernel, borderType=cv2.BORDER_CONSTANT)
                  [pad:bh - pad, pad:bw - pad] for ch in planes]
        acc = np.stack([np.ascontiguousarray(ch).ravel() for ch in planes])
        bw, bh = bx1 - bx0, by1 - by0
        gx0, gy0 = bx0, by0
    a_all = acc[0] if per is None else acc[3]
    nz = np.flatnonzero(a_all > 1e-7)
    if not len(nz):
        return dst
    if per is None and len(nz) >= n_pix // 8:
        # 全点共通の色で、触れた画素が多い: α の量を「被覆率 / 色の α」の層にして
        # _paint（blendLinear でまとめて over）に任せる
        layer = _Cov(np, gx0, gy0, gx0 + bw, gy0 + bh)
        cov = np.minimum(a_all, 1.0).astype(np.float32).reshape(bh, bw)
        cov[cov < 1e-7] = 0.0
        layer.cov = cov / np.float32(col[3])
        return _paint(dst, layer, col)
    # 触れた画素だけを over する（点が画面全体に散っても、外接矩形の全画素は触らない）
    if per is None:
        a = a_all[nz].astype(np.float32)
        vals = np.empty((len(nz), 4), np.float32)
        vals[:, :3] = a[:, None] * col[:3]
        vals[:, 3] = a
    else:
        vals = acc[:, nz].T.astype(np.float32)
    a = vals[:, 3]
    over1 = a > 1.0                       # 足し合わせで α が 1 を超えた画素は 1 で頭打ち
    if over1.any():
        vals[over1] /= a[over1][:, None]
    ys = nz // bw + gy0
    xs = nz % bw + gx0
    cur = dst[ys, xs]
    cur *= 1.0 - vals[:, 3:4]
    cur += vals
    dst[ys, xs] = cur
    return dst


# --- 変換 ---

def to_rgba8(dst):
    """float32 事前乗算 → uint8 のストレートアルファ（HxWx4）。

    α が 8bit で 0 になる画素（α < 0.5/255）は色も 0 にする。描かれた範囲（α>0 の
    外接矩形）だけを cv2 で変換する（1080p 全面で約 20ms。numpy だけだと約 55ms）。
    """
    d = need("to_rgba8")
    np, cv2 = d.np, d.cv2
    H, W = dst.shape[:2]
    out = np.zeros((H, W, 4), np.uint8)
    a = np.ascontiguousarray(dst[..., 3], dtype=np.float32)
    rows = np.flatnonzero(a.max(axis=1) > 0)
    if not len(rows):
        return out
    y0, y1 = int(rows[0]), int(rows[-1]) + 1
    cols = np.flatnonzero(a[y0:y1].max(axis=0) > 0)
    x0, x1 = int(cols[0]), int(cols[-1]) + 1
    sub = np.ascontiguousarray(dst[y0:y1, x0:x1], dtype=np.float32)
    sa = np.ascontiguousarray(a[y0:y1, x0:x1])
    sa_t = cv2.threshold(sa, 0.5 / 255.0, 0, cv2.THRESH_TOZERO)[1]
    inv = cv2.divide(255.0, sa_t)          # 0 で割ると 0（＝ α が 0 の画素の色は 0）
    mult = cv2.merge([inv, inv, inv, np.full_like(sa, 255.0)])
    out[y0:y1, x0:x1] = cv2.convertScaleAbs(cv2.multiply(sub, mult))
    return out


# --- 動かない層 ---

class _LayerCache:
    """layer_cache() の戻り値。cache(state, draw) で層を返す。"""

    def __init__(self):
        self._state = None
        self._layer = None

    def __call__(self, state, draw):
        if self._layer is None or state != self._state:
            layer = draw()
            layer = _as_premul(layer)
            if layer.flags.writeable:
                layer = layer.copy()
                layer.flags.writeable = False
            self._state = state
            self._layer = layer
        return self._layer


def layer_cache():
    """動かない層（下地・罫線・枠）を1回だけ描いて使い回す入れ物を返す。

    使い方:
        static = fk.layer_cache()
        def draw(i):
            dst = static(版, lambda: 下地を描いた canvas).copy()   # 版が変わったときだけ描き直す
            ...（動く部分を dst に描く）
    状態の版（state。== で比べられる値）が前回と違うときだけ draw() を呼び直す。
    返す層は書き込み不可（.copy() してから描くか、over / blit の src に使う）。
    """
    return _LayerCache()


# --- 構築時の重い前計算のメモ ---

_MEMO = OrderedDict()
_MEMO_LOCK = threading.Lock()


def memo(kind, key, compute):
    """構築時の重い前計算（照合の記録・配置・BFS）を、プロセス内で1回にする。

    レイヤーは Plan と Render で2回 exec されるので、同じ (kind, key) の compute() は
    1回だけ呼び、2回目は同じ値を返す。key は norm() で正規化してから比べる。
    キャッシュ（__cache__）の有無は見ない（dry_run と実レンダで同じ計画になるように）。
    返した値は共有されるので、呼び出し側で書き換えないこと。
    """
    k = (str(kind), json.dumps(norm(key), sort_keys=True, ensure_ascii=False))
    with _MEMO_LOCK:
        if k in _MEMO:
            _MEMO.move_to_end(k)
            return _MEMO[k]
    value = compute()
    with _MEMO_LOCK:
        _MEMO[k] = value
        while len(_MEMO) > _MEMO_MAX:
            _MEMO.popitem(last=False)
    return value


# --- 組み立て ---

def _font_refs(fn, fonts):
    """fonts= を (FontRef の並び, Sprite の署名の並び) にする（どちらも重複を除いて並べ替える。
    渡す順で鍵が変わらないように）"""
    out = []
    sigs = set()
    for f in fonts or ():
        if isinstance(f, Sprite):
            out.extend(f.fonts)
            sigs.add(f.sig)
        elif isinstance(f, FontRef):
            out.append(f)
        else:
            raise TypeError(f"{fn}: fonts には FontRef（font_ref）か Sprite（label）を渡してください: {f!r}")
    seen = {}
    for f in out:
        seen.setdefault((f.ffp, f.index, f.coords), f)
    refs = [seen[k] for k in sorted(seen, key=lambda k: (k[0], k[1], repr(k[2])))]
    return refs, sorted(sigs)


def build(fn, *, kind, ver, params, draw, n_frames, size, fps=None, fonts=(), files=(),
          text=None, info=None):
    """図のコマを描く draw(i) から、キャッシュつきの透過動画 Object を作る。

    kind: 図の種類（'regex_view' など）。ver: その図の描画の版（描き方を変えたら上げる）。
    params: 描く内容を決める値（norm() にかけて鍵に入る。関数は不可）。
    draw(i): i 番目のコマ。canvas()（float32 事前乗算）・uint8 の RGBA 配列・PIL.Image の
      どれかを返す。寸法は size（偶数へ切り上げる前でも後でもよい。前なら透明で埋める）。
    n_frames: コマ数（n_frames_for(秒, fps) で求める）。size: (幅, 高さ)。偶数へ切り上げる
      （切り上げる前の寸法も鍵に入る）。
    fps: 省略時は Project の fps。
    fonts: 使う文字とフォント（Sprite か FontRef）。FontRef は内容指紋・書体番号・軸の値が
      鍵に入る。**Sprite（label の戻り値）を渡すと、その絵の署名（Sprite.sig: 文字・書式・色・
      縁取り・寸法）も鍵に入る**ので、文字を直せば図も作り直される。draw の中で作る文字や
      fonts に渡さない文字は、その内容を params に入れること（入れないと古い図が使われる）。
    files: draw が読むファイル（画像・データ）。内容指紋が鍵に入る（渡す順には依らない）。
    text: p.audit() への文字の申告（text_meta の戻り値）。表示名の先頭に kind を添える
      （content が kind で始まっていなければ「kind: 」を足す）。info: obj.figure に付ける情報。

    鍵 = ['fig', kind, ver, framekit の版, norm(params), 切り上げ前の寸法, フォント,
    Sprite の署名, files の内容指紋, PIL の版（フォントを使うときだけ）]。
    生のパスは入れない。draw のコードも入れない（同じ鍵なら draw を呼ばず前回の動画を使う）。
    生成は stillseq._frames_object（qtrle argb の .mov を __cache__/artifacts/frames/ に置く
    ので、time() で尺より長く出すと最後のコマが残る）。dry_run では draw を呼ばない。
    フォントと files のパスは、生成物の .mov と一緒にレイヤーの依存に載る（cache='auto' の
    レイヤーキャッシュは、鍵が変われば .mov のパスが変わるので作り直す）。
    戻り値の Object に obj.figure（info）・obj._text_image（text）・obj._figure_draw を付ける。
    """
    if not callable(draw):
        raise TypeError(f"{fn}: draw は draw(i) の形の関数で指定してください: {draw!r}")
    if not isinstance(kind, str) or not kind:
        raise ValueError(f"{fn}: kind は図の種類の名前（文字列）で指定してください: {kind!r}")
    if isinstance(ver, bool) or not isinstance(ver, (str, int)):
        raise TypeError(f"{fn}: ver は版（文字列か整数）で指定してください: {ver!r}")
    if isinstance(n_frames, bool) or not isinstance(n_frames, int) or n_frames < 1:
        raise ValueError(f"{fn}: n_frames は 1 以上の整数で指定してください: {n_frames!r}")
    w0, h0 = _resolve_size(fn, size)
    w, h = w0 + w0 % 2, h0 + h0 % 2
    fps_frac = _fps_fraction(_resolve_fps(fn, fps))
    refs, sprite_sigs = _font_refs(fn, fonts)
    file_list = []
    for p in files or ():
        if not isinstance(p, (str, os.PathLike)):
            raise TypeError(f"{fn}: files にはファイルのパスを渡してください: {p!r}")
        p = os.fspath(p)
        if not os.path.isfile(p):
            raise FileNotFoundError(f"{fn}: ファイルが見つかりません: {p}")
        file_list.append(p)
    pil_ver = None
    if refs:
        pil_ver = _import_pil(fn)["PIL"].__version__
    key = ["fig", kind, str(ver), _FRAMEKIT_VER, norm(params), [w0, h0],
           [norm(f) for f in refs],
           sprite_sigs,
           sorted({_file_fingerprint(p) for p in file_list}),
           pil_ver]
    key_text = json.dumps(key, sort_keys=True, ensure_ascii=False)
    origin = []
    for p in [f.path for f in refs] + file_list:
        if p not in origin:
            origin.append(p)

    def frame(i):
        if not 0 <= i < n_frames:
            raise ValueError(f"{fn}: コマ番号 {i} は 0〜{n_frames - 1} の範囲で指定してください")
        np = need(fn).np
        img = draw(i)
        if hasattr(img, "convert") and hasattr(img, "size") and not hasattr(img, "shape"):
            img = np.asarray(img.convert("RGBA"), dtype=np.uint8)
        arr = np.asarray(img)
        if arr.ndim != 3 or arr.shape[2] != 4:
            raise ValueError(
                f"{fn}: draw({i}) の形が {arr.shape} です。(高さ, 幅, 4) の RGBA にしてください")
        if arr.dtype != np.uint8:
            arr = to_rgba8(arr.astype(np.float32, copy=False))
        if arr.shape[:2] == (h, w):
            return arr
        if arr.shape[:2] != (h0, w0):
            raise ValueError(
                f"{fn}: draw({i}) の寸法が {arr.shape[1]}x{arr.shape[0]} です。"
                f"size（{w0}x{h0}）と同じ寸法で描いてください")
        out = np.zeros((h, w, 4), np.uint8)
        out[:h0, :w0] = arr
        return out

    obj = _frames_object(frame, n_frames, key_text, w, h, fps_frac, origin)
    if info is None:
        info = SimpleNamespace()
    elif isinstance(info, dict):
        info = SimpleNamespace(**info)
    if text is not None:
        text = dict(text)
        content = str(text.get("content", ""))
        if not content.startswith(kind):
            text["content"] = f"{kind}: {content}"
    obj.figure = info
    obj._text_image = text
    obj._figure_draw = frame
    return obj


def draw_frame(obj, i):
    """テスト用: ffmpeg を通さずに build の draw(i) を呼び、uint8 の HxWx4 を返す。

    i は 0〜n_frames-1（範囲外は ValueError）。"""
    f = getattr(obj, "_figure_draw", None)
    if f is None:
        raise TypeError("draw_frame: framekit.build が返した Object を渡してください")
    try:
        k = i.__index__() if not isinstance(i, bool) else -1   # numpy の整数も受ける
    except AttributeError:
        k = -1
    if k < 0:
        raise ValueError(f"draw_frame: i は 0 以上の整数で指定してください: {i!r}")
    return f(int(k))
