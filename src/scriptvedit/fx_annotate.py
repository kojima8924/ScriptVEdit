# -*- coding: utf-8 -*-
"""画像（スクリーンショット）の中の矩形に印を足す: annotate()

画像を枠の中に収めて出し、時刻つきの印（囲み・下線・まわりを暗く）と寄り（切り出しの
矩形から矩形へ）を積んで、build() で1本の透過動画 Object にする（framekit.build。
frames() と同じ qtrle・alpha つきの .mov）。

    a = annotate("shots/page.png", size=(1600, 900), crop=(0, 120, 1920, 1200))
    a.underline((212, 388, 1180, 426), t=1.2)       # 引用の一文に 1.2 秒で下線（左から引く）
    a.box((640, 520, 812, 560), t=2.0)              # 2.0 秒で別の語を赤で囲む
    a.dim((600, 500, 1300, 580), t=2.0)             # 同じ時刻にまわりを暗くする
    a.zoom((500, 380, 1400, 886), t=3.0, dur=1.2)   # 3.0 秒から寄る（印もいっしょに動く）
    a.build().time(6) <= move(x=0.5, y=0.5, anchor="center")

矩形はどれも **元の画像の画素座標** (x0, y0, x1, y1)（左上と右下。画像を開いて読んだ値を
そのまま書ける）。ken_burns の (x, y, w, h) とは形が違う（x1 ≤ x0 などは ValueError で案内する）。
線の太さ・囲みのあき・角の丸みは **画面の px**（寄っても太さは変わらない）。

絵の作り
--------
- キャンバス = size（画像を収める枠）の上下左右に margin（既定 24px）の透明な余白を足したもの。
  画像は切り出し（crop）の縦横比を保って枠に収め、枠の中央に置く（拡大もする）。印は余白へ
  はみ出せる。move(anchor="center") で置けば枠が画面の中央に来る。
- 囲み（box）: 矩形の外側に pad px あけた角丸の枠。dur 秒で現れる（不透明度）。
- 下線（underline）: 矩形の下端から gap px 下に、左から右へ dur 秒で引く線（丸い端）。
- 暗く（dim）: 画像の見えている範囲のうち、矩形（pad px 広げる）の外を黒で暗くする。
  dim を重ねると穴が次の矩形へ dur 秒で移る。dim(None, t=) で明るさを戻す。
  印は暗くする層の上に描く（印は暗くならない）。
- 寄り（zoom）: いまの切り出しから rect へ dur 秒で切り出しを動かす（寄る・引く・横へ動く）。
  印と暗くする穴は画像の座標についていく。矩形が見えている範囲の外へ出た印は描かない
  （掛かっている印は、見えている範囲を margin だけ広げた所で切る）。
- until: 囲み・下線を消す秒（_OUT_DUR 秒で薄れる）。None なら最後まで残る。
- 画像の取り込み: 切り出しの全部を覆う範囲だけを切り出してから（縦長のページでも重くならない）、
  一番寄ったときの倍率が 1 未満なら 2 のべき乗の分の 1 へ面積平均で縮める（倍率がちょうど
  2^-n なので位置がずれない）。描く変換は framekit.blit（端数位置・縮小は INTER_AREA → 双線形）。
- 何も変わらないコマ（印が出そろって止まっている区間）は前のコマを使い回す
  （qtrle も同じ画素を飛ばすので、長く出しても軽い）。

既存の部品との違い: ken_burns は動画全体の切り出しを整数の画素で動かす Effect で、
印を画像の座標へついていかせられない。spotlight は diagram（SVG）の円の暗幕、mask は
画像のアルファで切り抜く Effect。annotate は「1枚の画像の上に、元の画素座標で印を時刻つきで
足す」ことだけを受け持つ（文字の札・矢印・ぼかしは持たない。札は text_image を重ねる）。

重さ（実測。Windows・Python 3.10・numpy 2.0・opencv 4.13。size=(1600, 900)・1920x1080 の画像）:
1コマの描画の中央値で、止まっている区間は前のコマの使い回しでほぼ 0、下線が伸びるコマは約 35ms、
暗くするのが現れるコマは約 65ms、寄っている間は約 150ms（大半は framekit.blit）。1 秒の寄りで
生成に約 5 秒かかる（初回だけ。2 回目からはキャッシュ）。
"""

import math
import os
from fractions import Fraction
from types import SimpleNamespace

import scriptvedit.framekit as fk
from scriptvedit.assets import asset
from scriptvedit.context import current_project
from scriptvedit.stillseq import _fps_fraction, _resolve_fps
from scriptvedit.validate import _require_number


# --- 定数 ---

# 描画の版。描き方（画像の取り込み・印の形・暗くする合成・寄りの補間）を変えたら上げる（鍵に入る）
_ANNOTATE_VER = "1"

_SIZE_MIN, _SIZE_MAX = 16, 4096    # size の一辺（px）。キャンバスは margin の分だけ大きい
_MARGIN = 24                       # 枠の外の透明な余白（印がはみ出せる幅）
_OUT_DUR = 0.25                    # until の後に消えるまでの秒
_DIM_AMOUNT = 0.6                  # dim の既定の暗さ（黒の不透明度）
_RECT_TOL = 0.5                    # 画像の外へはみ出してよい量（px。端の読み取りの誤差）


# --- 値の検証 ---

def _num(fn, name, value, lo=None, hi=None):
    return float(_require_number(fn, name, value, lo, hi))


def _time(fn, name, value):
    return _num(fn, name, value, 0, 36000)


def _dur(fn, name, value):
    v = _num(fn, name, value, 0, 600)
    if v <= 0:
        raise ValueError(f"{fn}: {name} は 0 より大きくしてください: {value!r}")
    return v


def _rect(fn, name, value, img_size):
    """(x0, y0, x1, y1)（元の画像の px）を検証して float の tuple にする"""
    if (isinstance(value, (str, bytes)) or not hasattr(value, "__len__") or len(value) != 4
            or any(isinstance(v, (tuple, list)) for v in value)):
        raise ValueError(
            f"{fn}: {name} は元の画像の px の (x0, y0, x1, y1)（左上と右下）で指定してください: "
            f"{value!r}")
    x0, y0, x1, y1 = (_num(fn, f"{name}[{i}]", v) for i, v in enumerate(value))
    if x1 <= x0 or y1 <= y0:
        raise ValueError(
            f"{fn}: {name} の右下 (x1, y1) は左上 (x0, y0) より右下にしてください: {value!r}"
            f"（形は (x0, y0, x1, y1)。ken_burns の (x, y, w, h) とは違う）")
    w, h = img_size
    if x0 < -_RECT_TOL or y0 < -_RECT_TOL or x1 > w + _RECT_TOL or y1 > h + _RECT_TOL:
        raise ValueError(
            f"{fn}: {name} {value!r} が画像（{w}x{h}）の外に出ています")
    return (x0, y0, x1, y1)


def _resolve_image(fn, image):
    """画像のパスを検証し、(パス, (幅, 高さ)) を返す。見つからなければ asset() で探す"""
    if not isinstance(image, (str, os.PathLike)):
        raise TypeError(f"{fn}: image は画像ファイルのパスで指定してください: {image!r}")
    path = os.fspath(image)
    if not os.path.isfile(path):
        path = asset(path)
    Image = fk.need(fn).Image
    try:
        with Image.open(path) as im:          # ヘッダだけ読む
            w, h = im.size
    except OSError:
        raise ValueError(f"{fn}: 画像として読めません: {path}") from None
    return path, (int(w), int(h))


# --- 時刻と配置（標準ライブラリだけ。build / figure / 描画で共通）---

def _ease_out_cubic(u):
    """下線を引く速さの変化（終わりで減速）"""
    u = min(1.0, max(0.0, u))
    return 1.0 - (1.0 - u) ** 3


def _smooth(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3.0 - 2.0 * u)


def _lerp_rect(a, b, p):
    return tuple(x + (y - x) * p for x, y in zip(a, b))


def _crop_at(spec, t):
    """t 秒の切り出し（元の画像の px）"""
    crop = spec["crop"]
    for z in spec["zooms"]:
        if t <= z["t"]:
            break
        p = (t - z["t"]) / z["dur"]
        if p < 1.0 - 1e-9:          # 終わりの秒ちょうどのコマ（浮動小数の誤差）は寄り終えた絵
            return _lerp_rect(crop, z["rect"], z["_f"](p))
        crop = z["rect"]
    return crop


def _fit(spec, crop):
    """crop を枠に収めたときの (倍率 k, 左上 ox, oy, 幅 dw, 高さ dh)（キャンバスの px）"""
    W, H = spec["size"]
    m = spec["margin"]
    cw, ch = crop[2] - crop[0], crop[3] - crop[1]
    k = min(W / cw, H / ch)
    dw, dh = cw * k, ch * k
    return k, m + (W - dw) / 2.0, m + (H - dh) / 2.0, dw, dh


def _to_canvas(spec, crop, rect):
    """元の画像の矩形 → キャンバスの矩形（切り出し crop のとき）"""
    k, ox, oy, _dw, _dh = _fit(spec, crop)
    return (ox + (rect[0] - crop[0]) * k, oy + (rect[1] - crop[1]) * k,
            ox + (rect[2] - crop[0]) * k, oy + (rect[3] - crop[1]) * k)


def _mark_state(ev, t):
    """印の (不透明度, 進み具合)。不透明度 0 は描かない"""
    if t <= ev["t"]:
        return 0.0, 0.0
    u = (t - ev["t"]) / ev["dur"]
    if ev["kind"] == "box":
        alpha, prog = min(1.0, u), 1.0
    else:
        alpha, prog = (1.0 if u > 0 else 0.0), _ease_out_cubic(u)
    if ev["until"] is not None and t > ev["until"]:
        alpha *= max(0.0, 1.0 - (t - ev["until"]) / _OUT_DUR)
    return alpha, prog


def _dim_at(spec, t):
    """t 秒の暗くする穴と暗さ: (穴の矩形（元の画像の px）or None, 穴の広げ幅 px, 暗さ 0..1)。

    暗くしていない所から: 穴は次の矩形のまま暗さだけが増える。暗くしている所から別の矩形へ:
    穴の矩形・広げ幅・暗さがなめらかに移る。dim(None) へ: 穴はそのまま暗さだけが減る。
    """
    rect, pad, amount = None, 0.0, 0.0
    for d in spec["dims"]:
        if t <= d["t"]:
            break
        p = _smooth((t - d["t"]) / d["dur"])
        if d["rect"] is None:
            if rect is not None:
                amount *= 1.0 - p
                if p >= 1.0:
                    rect, pad, amount = None, 0.0, 0.0
        elif rect is None:
            rect, pad, amount = tuple(d["rect"]), d["pad"], d["amount"] * p
        else:
            rect = _lerp_rect(rect, d["rect"], p)
            pad += (d["pad"] - pad) * p
            amount += (d["amount"] - amount) * p
        if p < 1.0:
            break
    return rect, pad, amount


def _mark_end(ev):
    return ev["until"] + _OUT_DUR if ev["until"] is not None else ev["t"] + ev["dur"]


# --- 組み立て役 ---

class Annotate:
    """annotate() が返す組み立て役。印と寄りを積んで build() で動画 Object にする。

    時刻 t はすべて build() で作る動画の先頭を 0 とした秒。メソッドは自分を返す。
    """

    def __init__(self, path, img_size, size, margin, crop, frame, frame_width, palette):
        self._path = path
        self._img = img_size
        self._size = size
        self._margin = margin
        self._crop = crop
        self._frame = frame
        self._frame_width = frame_width
        self._palette = palette
        self._marks = []
        self._dims = []
        self._zooms = []

    def __repr__(self):
        return (f"Annotate({os.path.basename(self._path)}, {self._size[0]}x{self._size[1]}, "
                f"印={len(self._marks)}, dim={len(self._dims)}, zoom={len(self._zooms)})")

    # -- 印 --

    def _until(self, fn, t, until):
        if until is None:
            return None
        until = _time(fn, "until", until)
        if until <= t:
            raise ValueError(f"{fn}: until（{until:g}）は t（{t:g}）より後にしてください")
        return until

    def box(self, rect, t=0, *, color="accent", width=5, pad=8, radius=6, dur=0.25, until=None):
        """rect を外側から囲む枠を、t 秒から dur 秒で現す。

        rect: 囲む矩形（元の画像の px の (x0, y0, x1, y1)）。
        color: 枠の色（図の色の名前 accent / fg / muted …・'#e0241b'・(r, g, b[, a])）。
        width: 線の太さ px。pad: 矩形と枠の内側のあき px。radius: 角の丸み px（線の中心で）。
        dur: 現れるのにかける秒（不透明度 0 → 1）。until: 消す秒（None は最後まで）。
        字間の狭い所を囲むときは pad を小さく（隣の字に線が掛かるなら underline にする）。
        """
        fn = "annotate.box"
        r = _rect(fn, "rect", rect, self._img)
        t = _time(fn, "t", t)
        self._marks.append({
            "kind": "box", "rect": list(r), "t": t, "color": list(fk.color(fn, color, self._palette)),
            "width": _num(fn, "width", width, 0.5, 64), "pad": _num(fn, "pad", pad, 0, 200),
            "radius": _num(fn, "radius", radius, 0, 200), "dur": _dur(fn, "dur", dur),
            "until": self._until(fn, t, until)})
        return self

    def underline(self, rect, t=0, *, color="accent", width=5, gap=6, dur=0.4, until=None):
        """rect の下に、t 秒から dur 秒で左から右へ線を引く。

        rect: 下線を引く矩形（元の画像の px の (x0, y0, x1, y1)。文字の行の上下を含める）。
        color: 線の色。width: 線の太さ px（端は丸い）。gap: 矩形の下端と線のあき px。
        dur: 引き終わるまでの秒（終わりで減速する）。until: 消す秒（None は最後まで）。
        """
        fn = "annotate.underline"
        r = _rect(fn, "rect", rect, self._img)
        t = _time(fn, "t", t)
        self._marks.append({
            "kind": "underline", "rect": list(r), "t": t,
            "color": list(fk.color(fn, color, self._palette)),
            "width": _num(fn, "width", width, 0.5, 64), "gap": _num(fn, "gap", gap, 0, 200),
            "dur": _dur(fn, "dur", dur), "until": self._until(fn, t, until)})
        return self

    def dim(self, rect, t=0, *, amount=_DIM_AMOUNT, pad=8, dur=0.3):
        """rect の外を t 秒から dur 秒で暗くする（スポットライト）。None で明るさを戻す。

        rect: 明るいまま残す矩形（元の画像の px の (x0, y0, x1, y1)）か None。
          すでに暗くしているときは、穴が次の矩形へ dur 秒で移る。
        amount: 暗さ（黒の不透明度。0〜1）。pad: 穴を矩形より広げる px（囲みの枠が穴に入る）。
        暗くするのは画像の見えている範囲だけ（余白は透明のまま）。
        """
        fn = "annotate.dim"
        r = None if rect is None else _rect(fn, "rect", rect, self._img)
        t = _time(fn, "t", t)
        dur = _dur(fn, "dur", dur)
        amount = _num(fn, "amount", amount, 0, 1)
        if amount <= 0:
            raise ValueError(f"{fn}: amount は 0 より大きくしてください（戻すときは dim(None)）")
        for d in self._dims:
            if t < d["t"] + d["dur"] and d["t"] < t + dur:
                raise ValueError(
                    f"{fn}: {t:g}〜{t + dur:g} 秒が、先に積んだ dim（{d['t']:g}〜"
                    f"{d['t'] + d['dur']:g} 秒）と重なっています。dim は1つずつ順に積んでください")
        self._dims.append({"rect": None if r is None else list(r), "t": t, "dur": dur,
                           "amount": amount if r is not None else None,
                           "pad": _num(fn, "pad", pad, 0, 200) if r is not None else None})
        return self

    def zoom(self, rect, t=0, *, dur=1.0, easing="ease_in_out_cubic"):
        """t 秒から dur 秒で、切り出しを rect へ動かす（寄る・引く・横へ動く）。

        rect: 次に見せる範囲（元の画像の px の (x0, y0, x1, y1)。縦横比を保って枠に収める）。
        easing: 動きの速さの変化（イージングの名前・関数・Expr。None は等速）。
        印と暗くする穴は画像の座標についていく。zoom は区間が重ならないように順に積む。
        """
        fn = "annotate.zoom"
        r = _rect(fn, "rect", rect, self._img)
        t = _time(fn, "t", t)
        dur = _dur(fn, "dur", dur)
        f, key = fk.easing(fn, easing)
        for z in self._zooms:
            if t < z["t"] + z["dur"] and z["t"] < t + dur:
                raise ValueError(
                    f"{fn}: {t:g}〜{t + dur:g} 秒が、先に積んだ zoom（{z['t']:g}〜"
                    f"{z['t'] + z['dur']:g} 秒）と重なっています。zoom は1つずつ順に積んでください")
        self._zooms.append({"rect": list(r), "t": t, "dur": dur, "easing": key, "_f": f})
        return self

    # -- 組み立て --

    def _spec(self):
        zooms = sorted(self._zooms, key=lambda z: z["t"])
        dims = sorted(self._dims, key=lambda d: d["t"])
        return {
            "size": self._size, "margin": self._margin, "crop": self._crop,
            "zooms": zooms,
            "dims": dims, "marks": list(self._marks),
            "frame": self._frame, "frame_width": self._frame_width,
        }

    def _params(self, spec):
        """鍵に入れる値（絵に効くものだけ。同一出力なら同一鍵）"""
        return {
            "size": list(spec["size"]), "margin": spec["margin"], "crop": list(spec["crop"]),
            "frame": None if spec["frame"] is None else list(spec["frame"]),
            "frame_width": spec["frame_width"] if spec["frame"] is not None else None,
            "marks": spec["marks"],
            "dims": spec["dims"],
            "zooms": [{k: v for k, v in z.items() if k != "_f"} for z in spec["zooms"]],
        }

    def _end_time(self, spec):
        ends = [_mark_end(m) for m in spec["marks"]]
        ends += [d["t"] + d["dur"] for d in spec["dims"]]
        ends += [z["t"] + z["dur"] for z in spec["zooms"]]
        return max(ends, default=0.0)

    def build(self, duration=None):
        """印と寄りを積んだ画像を、1本の透過動画 Object にする（framekit.build）。

        duration: 秒。省略時は最後の印・寄りが終わった絵のコマまで（何も無ければ1コマ）。
          コマ i は i/fps 秒の絵なので、終わりの秒以降のコマを1枚含める（含めないと、
          time() で長く出したときに残る最後のコマが、下線の引き終わる手前の絵になる）。
          time() でそれより長く表示すると最後のコマが残る。
        戻り値の Object の obj.figure に次が付く:
          figure.size        … キャンバスの (幅, 高さ)（size の上下左右に margin を足したもの）
          figure.margin      … 余白 px
          figure.area(t)     … t 秒に画像が見えている範囲 (x0, y0, x1, y1)（キャンバスの px）
          figure.rect(r, t)  … 元の画像の矩形 r が t 秒にキャンバスのどこに来るか
        """
        fn = "annotate.build"
        spec = self._spec()
        fps_frac = _fps_fraction(_resolve_fps(fn, None))
        if duration is None:
            # 終わりの秒以降の最初のコマ（10 進で正確に読んで切り上げ）までを描く
            end = Fraction(repr(self._end_time(spec))) * fps_frac
            n_frames = math.ceil(end) + 1 if end > 0 else 1
        else:
            n_frames = fk.n_frames_for(_dur(fn, "duration", duration), fps_frac)
        W, H = spec["size"]
        m = spec["margin"]
        CW, CH = W + 2 * m, H + 2 * m
        renderer = None

        def draw(i):
            nonlocal renderer
            if renderer is None:
                renderer = _Renderer(self._path, spec, (CW, CH))
            try:
                return renderer.frame(float(Fraction(i) / fps_frac))
            finally:
                # 最後のコマの後は画像と全面配列を手放す。再描画時には作り直す。
                if i == n_frames - 1:
                    renderer = None

        def area(t):
            _k, ox, oy, dw, dh = _fit(spec, _crop_at(spec, float(t)))
            return (ox, oy, ox + dw, oy + dh)

        def rect(r, t):
            return _to_canvas(spec, _crop_at(spec, float(t)), _rect("figure.rect", "r", r, self._img))

        info = SimpleNamespace(size=(CW, CH), margin=m, area=area, rect=rect,
                               image_size=tuple(self._img))
        return fk.build(fn, kind="annotate", ver=_ANNOTATE_VER, params=self._params(spec),
                        draw=draw, n_frames=n_frames, size=(CW, CH), files=[self._path],
                        info=info)


# --- 描画 ---

def _axis_cov(np, lo, hi, n):
    """画素 0..n-1 が区間 [lo, hi) に占める割合（連続座標）"""
    idx = np.arange(n, dtype=np.float64)
    return np.clip(np.minimum(idx + 1.0, hi) - np.maximum(idx, lo), 0.0, 1.0).astype(np.float32)


class _Renderer:
    """画像の取り込み・動かない層・前のコマを持つ描画役（build の draw の中で作る）"""

    def __init__(self, path, spec, canvas_size):
        self.spec = spec
        self.CW, self.CH = canvas_size
        deps = fk.need("annotate")
        self.np, self.cv2, Image = deps.np, deps.cv2, deps.Image
        np, cv2 = self.np, self.cv2
        crops = [spec["crop"]] + [z["rect"] for z in spec["zooms"]]
        k_max = max(_fit(spec, c)[0] for c in crops)
        # 一番寄ったときでも 1 未満の倍率なら、2^-n（≥ k_max）へ面積平均で縮めておく
        n = 0 if k_max >= 1.0 else int(math.floor(math.log2(1.0 / k_max) + 1e-9))
        step = 1 << n
        with Image.open(path) as im:
            iw, ih = im.size
            # 切り出しの全部を覆う範囲（端の補間のため 2px 広げる）。原点は 2^n の倍数にそろえる
            x0 = max(0, int(math.floor(min(c[0] for c in crops))) - 2)
            y0 = max(0, int(math.floor(min(c[1] for c in crops))) - 2)
            x0, y0 = x0 - x0 % step, y0 - y0 % step
            x1 = min(iw, int(math.ceil(max(c[2] for c in crops))) + 2)
            y1 = min(ih, int(math.ceil(max(c[3] for c in crops))) + 2)
            rgba = np.asarray(im.crop((x0, y0, x1, y1)).convert("RGBA"), dtype=np.uint8)
        ph, pw = (-rgba.shape[0]) % step, (-rgba.shape[1]) % step
        if ph or pw:          # 2^n の倍数へ、端の画素を複製して広げる（縮めた端が透けない）
            rgba = cv2.copyMakeBorder(rgba, 0, ph, 0, pw, cv2.BORDER_REPLICATE)
        src = rgba.astype(np.float32) * np.float32(1.0 / 255.0)
        src[..., :3] *= src[..., 3:4]
        if step > 1:
            src = cv2.resize(src, (src.shape[1] // step, src.shape[0] // step),
                             interpolation=cv2.INTER_AREA)
        src.flags.writeable = False
        self.src, self.origin, self.f = src, (x0, y0), 1.0 / step
        self.frame_rgba = None if spec["frame"] is None else tuple(spec["frame"])
        self.base = fk.layer_cache()
        self.last_sig = None
        self.last = None

    def _base(self, crop):
        """画像（と枠）だけの層。切り出しが変わったときだけ描き直す"""
        def paint():
            spec = self.spec
            dst = fk.canvas(self.CW, self.CH)
            k, ox, oy, dw, dh = _fit(spec, crop)
            rx, ry = self.origin
            # 縮めた絵の画素 s' は元の画像の rx + s'/f にあたる → 画面では k/f 倍で置く
            fk.blit(dst, self.src, ox + (rx - crop[0]) * k, oy + (ry - crop[1]) * k,
                    scale=k / self.f, pivot=(0.0, 0.0), clip=(ox, oy, ox + dw, oy + dh))
            if self.frame_rgba is not None:
                hw = spec["frame_width"] / 2.0
                fk.rect(dst, (ox - hw, oy - hw, ox + dw + hw, oy + dh + hw), self.frame_rgba,
                        width=spec["frame_width"])
            return dst
        return self.base(tuple(round(v, 6) for v in crop), paint)

    def _dim(self, dst, crop, hole, amount):
        """見えている範囲のうち、穴の外を黒で暗くする（被覆率は「範囲 − 穴∩範囲」で正確に）"""
        np = self.np
        _k, ox, oy, dw, dh = _fit(self.spec, crop)
        ax0, ay0, ax1, ay1 = ox, oy, ox + dw, oy + dh
        ix0, iy0 = max(0, int(math.floor(ax0))), max(0, int(math.floor(ay0)))
        ix1, iy1 = min(self.CW, int(math.ceil(ax1))), min(self.CH, int(math.ceil(ay1)))
        if ix0 >= ix1 or iy0 >= iy1:
            return
        cx = _axis_cov(np, ax0 - ix0, ax1 - ix0, ix1 - ix0)
        cy = _axis_cov(np, ay0 - iy0, ay1 - iy0, iy1 - iy0)
        a = np.outer(cy, cx)                       # 範囲の被覆率
        hx0, hy0 = max(hole[0], ax0), max(hole[1], ay0)
        hx1, hy1 = min(hole[2], ax1), min(hole[3], ay1)
        if hx0 < hx1 and hy0 < hy1:                # 穴∩範囲 ⊂ 範囲なので差は 0 以上
            a -= np.outer(_axis_cov(np, hy0 - iy0, hy1 - iy0, iy1 - iy0),
                          _axis_cov(np, hx0 - ix0, hx1 - ix0, ix1 - ix0))
            np.maximum(a, 0.0, out=a)
        a *= np.float32(amount)                    # 黒の不透明度（事前乗算の over）
        part = dst[iy0:iy1, ix0:ix1]
        part *= (np.float32(1.0) - a)[..., None]
        part[..., 3] += a

    def _marks(self, dst, crop, states):
        """囲みと下線。矩形が見えている範囲に掛からない印は描かず、掛かる印も
        見えている範囲を margin だけ広げた所で切る（寄った先の外の印が余白に浮かない）"""
        np = self.np
        spec = self.spec
        _k, ox, oy, dw, dh = _fit(spec, crop)
        m = spec["margin"]
        # 描いてよい範囲: 見えている範囲を margin だけ広げたもの（キャンバスの内側）
        lim = (max(0.0, ox - m), max(0.0, oy - m), min(float(self.CW), ox + dw + m),
               min(float(self.CH), oy + dh + m))
        todo = []
        for ev, (alpha, prog) in zip(spec["marks"], states):
            if alpha <= 0.0 or prog <= 0.0:
                continue
            x0, y0, x1, y1 = _to_canvas(spec, crop, ev["rect"])
            if x1 <= ox or x0 >= ox + dw or y1 <= oy or y0 >= oy + dh:
                continue
            col = list(ev["color"])
            col[3] = col[3] * alpha
            w = ev["width"]
            if ev["kind"] == "box":
                e = ev["pad"] + w / 2.0
                box = (x0 - e, y0 - e, x1 + e, y1 + e)
                ext = (box[0] - w, box[1] - w, box[2] + w, box[3] + w)
                todo.append((ext, "box", box, col, ev))
            else:
                y = y1 + ev["gap"] + w / 2.0
                xe = x0 + (x1 - x0) * prog
                if xe - x0 < 1e-3:
                    continue
                ext = (x0 - w, y - w, xe + w, y + w)
                todo.append((ext, "line", ((x0, y), (xe, y)), col, ev))
        if not todo:
            return dst
        inside = all(e[0] >= lim[0] and e[1] >= lim[1] and e[2] <= lim[2] and e[3] <= lim[3]
                     for e, *_r in todo)
        layer = dst if inside else fk.canvas(self.CW, self.CH)
        for _ext, kind, geo, col, ev in todo:
            if kind == "box":
                fk.rect(layer, geo, col, width=ev["width"], radius=ev["radius"])
            else:
                fk.polyline(layer, list(geo), col, ev["width"], cap="round")
        if not inside:
            # 印の外接矩形の中だけを切って重ねる（全面を掛け算しない）
            bx0 = max(0, int(math.floor(min(e[0] for e, *_r in todo))) - 1)
            by0 = max(0, int(math.floor(min(e[1] for e, *_r in todo))) - 1)
            bx1 = min(self.CW, int(math.ceil(max(e[2] for e, *_r in todo))) + 1)
            by1 = min(self.CH, int(math.ceil(max(e[3] for e, *_r in todo))) + 1)
            if bx0 < bx1 and by0 < by1:
                sub = layer[by0:by1, bx0:bx1]
                sub *= np.outer(_axis_cov(np, lim[1] - by0, lim[3] - by0, by1 - by0),
                                _axis_cov(np, lim[0] - bx0, lim[2] - bx0, bx1 - bx0))[..., None]
                fk.over(dst, sub, bx0, by0)
        return dst

    def frame(self, t):
        spec = self.spec
        crop = _crop_at(spec, t)
        hole, pad, amount = _dim_at(spec, t)
        states = [_mark_state(ev, t) for ev in spec["marks"]]
        sig = (tuple(round(v, 6) for v in crop),
               None if hole is None else tuple(round(v, 6) for v in hole + (pad,)),
               round(amount, 6), tuple((round(a, 6), round(p, 6)) for a, p in states))
        if sig == self.last_sig:
            return self.last
        dst = self._base(crop).copy()
        if hole is not None and amount > 0:
            hx0, hy0, hx1, hy1 = _to_canvas(spec, crop, hole)
            self._dim(dst, crop, (hx0 - pad, hy0 - pad, hx1 + pad, hy1 + pad), amount)
        self._marks(dst, crop, states)
        out = fk.to_rgba8(dst)
        out.flags.writeable = False
        self.last_sig, self.last = sig, out
        return out


# --- 公開の入口 ---

def annotate(image, *, size=None, crop=None, margin=_MARGIN, frame=None, frame_width=3,
             colors=None):
    """画像（スクリーンショット）に、時刻つきの囲み・下線・暗くする・寄りを足す組み立て役を返す。

    image: 画像ファイルのパス（PNG / JPEG。見つからなければ asset() で探す）。
    size: 画像を収める枠 (幅, 高さ) px。None は Project の画面の大きさ。16〜4096。
      キャンバスはその上下左右に margin を足した大きさ（背景は透明）。
    crop: 最初に見せる範囲（元の画像の px の (x0, y0, x1, y1)）。None は画像の全体。
      縦横比を保って枠に収め、枠の中央に置く（拡大もする）。
    margin: 枠の外の透明な余白 px（囲みの枠・下線・frame がはみ出せる幅。0〜400）。
    frame: 画像のまわりの細い枠の色（None は描かない）。
    frame_width: 画像のまわりの枠の太さ px（frame を描くときだけ効く）。
    colors: 色の名前の差し替え（framekit の図の色 fg / accent / muted / line / dim / panel）。
      印の色の既定は accent（赤 #e0241b）。

    印と寄り（時刻 t は動画の先頭からの秒。矩形は元の画像の px の (x0, y0, x1, y1)。
    メソッドは自分を返すので続けて書ける）:
      a.box(rect, t=0, color="accent", width=5, pad=8, radius=6, dur=0.25, until=None)
      a.underline(rect, t=0, color="accent", width=5, gap=6, dur=0.4, until=None)
      a.dim(rect, t=0, amount=0.6, pad=8, dur=0.3)     rect=None で明るさを戻す
      a.zoom(rect, t=0, dur=1.0, easing="ease_in_out_cubic")
      obj = a.build(duration=None)   → obj.figure.area(t)・obj.figure.rect(r, t)

    鍵（framekit.build）: 画像の内容指紋（パスは入らない）・枠の寸法・余白・切り出し・
    frame（描くときだけ太さも）・印と寄りの並び・描画の版 _ANNOTATE_VER。
    """
    fn = "annotate"
    path, img_size = _resolve_image(fn, image)
    if size is None:
        proj = current_project()
        if proj is None:
            raise ValueError(f"{fn}: size を省くときは Project を先に作ってください（size=(幅, 高さ)）")
        size = (int(proj.width), int(proj.height))
    if (not isinstance(size, (tuple, list)) or len(size) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) for v in size)):
        raise ValueError(f"{fn}: size は (幅, 高さ) の整数で指定してください: {size!r}")
    W, H = int(size[0]), int(size[1])
    for name, v in (("幅", W), ("高さ", H)):
        if not _SIZE_MIN <= v <= _SIZE_MAX:
            raise ValueError(f"{fn}: size の{name}は {_SIZE_MIN}〜{_SIZE_MAX} px にしてください: {v}")
    if isinstance(margin, bool) or not isinstance(margin, int) or not 0 <= margin <= 400:
        raise ValueError(f"{fn}: margin は 0〜400 の整数 px で指定してください: {margin!r}")
    crop = (0.0, 0.0, float(img_size[0]), float(img_size[1])) if crop is None \
        else _rect(fn, "crop", crop, img_size)
    pal = fk.palette(fn, colors)
    frame_rgba = None if frame is None else fk.color(fn, frame, pal)
    frame_width = _num(fn, "frame_width", frame_width, 0.5, 64)
    return Annotate(path, img_size, (W, H), margin, crop, frame_rgba, frame_width, pal)
