# -*- coding: utf-8 -*-
"""ロゴなどの画像に光が一度走る演出（ライトスイープ）: glint()

画像（企業のロゴなど）を枠に収めて出し、時刻つきの「光の帯が斜めに通り過ぎる」（sweep）と
出入り（enter / exit。フェードと控えめな拡大）を積んで、build() で1本の透過動画 Object に
する（framekit.build。frames() と同じ qtrle・alpha つきの .mov）。

    g = glint("logos/acme.png", size=(640, 240))   # 枠に収めて中央（拡大もする）
    g.enter(t=0.0)                                  # 0.5 秒で現れる（96% から等倍へ）
    g.sweep(t=0.7)                                  # 0.7 秒から光が一度走る
    g.build().time(4) <= move(x=0.5, y=0.4, anchor="center")

光の当て方
----------
- 光は **画像の不透明な画素の上だけ** に当たる。事前乗算の色 cp・不透明度 a の画素へ
  「スクリーン」で掛ける: cp + L·光の色·(a − cp)（L は帯の明るさ 0..strength）。
  加算ではないので 1 を超えず（白飛びしない）、白い画素は白のまま、透明な画素（a=0）は
  透明のまま、縁の半透明の画素は不透明度の分だけ光る。暗い色ほどよく明るくなる。
- 帯はやわらかい cos² の山（幅 width）に細い芯（幅の 3 割）を重ねた形。帯の外は 0 なので、
  帯が画像の外にある間（通り始めと通り終わり）は光が無い（ぱっと出ない）。
- 帯は画像の中身（不透明な画素の外接矩形）の外から外まで dur 秒で通る。向きは左から右。
  angle は帯の傾き（度。正で「/」、負で「\」、0 で縦）。
- 何本かの sweep が重なる所は、光どうしもスクリーンで重ねる（上限は 1）。

地の扱い（matte）
-----------------
- 'alpha': 画像のアルファのまま（透過 PNG のロゴ）。
- 'plate': 不透過の画像（白地の JPEG のロゴ）を、角を丸めた板としてそのまま出す。
  光は地の色（画像の縁の色の中央値）から離れた画素にだけ当てる（地は光らない）。
  ロゴの色は地の上で見せる前提で作られている（白地の黒い文字を暗いカードへ抜くと読めない）
  ので、不透過の画像の既定はこちら。
- 'key': 縁につながる地の色を透明にする（縁の画素は「地の色といちばん近い内側の色の混ざり」
  と見て、その割合を不透明度にする）。明るいカードの上に直に置きたいとき。地の色が一様でない画像は ValueError。
- 'auto'（既定）: 透明な画素（α < 128）が 0.5% 以上あれば 'alpha'、無ければ 'plate'。

出入り
------
- enter(t, dur, scale, easing): t 秒から dur 秒で、不透明度 0 → 1・大きさ scale → 1。
  enter を積まなければ最初のコマから出ている。
- exit(t, dur, scale, easing): t 秒から dur 秒で、不透明度 1 → 0・大きさ 1 → scale。
- 拡大縮小は framekit.warp_patch（端数位置・縮小は INTER_AREA → 双線形）。等倍の間は
  一度だけ写した絵を使い回す（止まっているロゴは毎コマ同じ画素）。

重さ（実測は README）: 光も出入りも無いコマは前のコマを返す。光の通るコマは画像の箱の中だけを
計算する。

Effect にしなかった理由: ffmpeg で画素ごとに帯を描くと geq になり、1080p で毎秒数コマまで
落ちる（CLAUDE.md §4.13）。また「地の色から離れた画素だけ」「縁の色を抜く」は画像の中身を
読んで決める処理で、ffmpeg のフィルタでは組みにくい。
"""

import math
import os
from fractions import Fraction
from types import SimpleNamespace

import scriptvedit.framekit as fk
from scriptvedit.assets import asset
from scriptvedit.stillseq import _fps_fraction, _resolve_fps
from scriptvedit.validate import _require_choice, _require_number


# --- 定数 ---

# 描画の版。描き方（光の形・合成・地の扱い・出入りの補間）を変えたら上げる（鍵に入る）
_GLINT_VER = "1"

_SIZE_MIN, _SIZE_MAX = 16, 4096    # size の一辺（px）
_MATTES = ("auto", "alpha", "plate", "key")
_ALPHA_FRAC = 0.005                # 'auto' で 'alpha' と見なす透明な画素の割合
_BG_TOL = 0.06                     # 地の色と見なす差（チャンネルごとの差の最大。0..1）
_BG_UNIFORM = 0.9                  # 縁の画素のうち地の色に入る割合がこれ以上なら「一様」
_PLATE_LO, _PLATE_HI = 0.03, 0.15  # plate で光を当て始める・当て切る地の色からの差
_KEY_EDGE = 2                      # key で地の色を抜く縁の幅（px。元の画像の画素）
_CORE = 0.3                        # 芯の幅（帯の幅に対する割合）
_CORE_MIX = 0.35                   # 芯の明るさの割合（残りがやわらかい山）


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


def _scale(fn, value):
    return _num(fn, "scale", value, 0.5, 1.0)


def _resolve_image(fn, image):
    """画像のパスを検証して (パス, RGBA の uint8 配列) を返す。見つからなければ asset() で探す"""
    if not isinstance(image, (str, os.PathLike)):
        raise TypeError(f"{fn}: image は画像ファイルのパスで指定してください: {image!r}")
    path = os.fspath(image)
    if not os.path.isfile(path):
        path = asset(path)
    deps = fk.need(fn)
    try:
        with deps.Image.open(path) as im:
            rgba = deps.np.asarray(im.convert("RGBA"), dtype=deps.np.uint8)
    except OSError:
        raise ValueError(f"{fn}: 画像として読めません: {path}") from None
    return path, rgba


# --- 地の扱い（numpy・cv2。構築時に1回）---

def _border_bg(np, rgb):
    """縁の画素の色の中央値（0..1）と、縁のうちその色に入る割合"""
    ring = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]], axis=0)
    bg = np.median(ring, axis=0)
    near = np.max(np.abs(ring - bg), axis=1) <= _BG_TOL
    return bg, float(near.mean())


def _matte_of(np, rgba, matte):
    """'auto' を実際の扱いにする"""
    if matte != "auto":
        return matte
    clear = float((rgba[..., 3] < 128).mean())
    return "alpha" if clear >= _ALPHA_FRAC else "plate"


def _smoothstep(np, lo, hi, x):
    u = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return u * u * (3.0 - 2.0 * u)


def _prepare(fn, rgba, matte):
    """元の画像 → (事前乗算の RGBA（float32）, 光の重み（float32。ストレート）, 地の色 or None)。

    alpha: 重みは 1（不透明度は合成の式で効く）。
    plate: 重みは地の色からの差の smoothstep（地の色が一様でなければ全面 1）。
    key: 縁につながる地の色の画素を透明に、そのまわり _KEY_EDGE px を地の色と内側の色の
      混ざりとして抜く（内側の色が地の色に近い所は color-to-alpha）。重みは 1。
    """
    deps = fk.need(fn)
    np, cv2 = deps.np, deps.cv2
    f = rgba.astype(np.float32) * np.float32(1.0 / 255.0)
    rgb, a = f[..., :3], f[..., 3]
    weight = np.ones(a.shape, np.float32)
    bg = None
    if matte in ("plate", "key"):
        bg, uniform = _border_bg(np, rgb)
        if matte == "key":
            if uniform < _BG_UNIFORM:
                raise ValueError(
                    f"{fn}: matte='key' は地の色が一様な画像だけに使えます（縁の画素のうち地の色"
                    f"に入るのは {uniform:.0%}）。matte='plate' を使ってください")
            dist = np.max(np.abs(rgb - bg), axis=2)
            near = (dist <= _BG_TOL).astype(np.uint8)
            _n, labels = cv2.connectedComponents(near, connectivity=4)
            edge_labels = np.unique(np.concatenate(
                [labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
            edge_labels = edge_labels[edge_labels > 0]
            region = np.isin(labels, edge_labels)
            ring = cv2.dilate(region.astype(np.uint8), np.ones((3, 3), np.uint8),
                              iterations=_KEY_EDGE).astype(bool) & ~region
            # 縁の画素は「地の色と、いちばん近い内側の画素の色の混ざり」と見て、その直線へ
            # 射影した割合を不透明度にし、色は内側の色にする（color-to-alpha だけだと縁の色が
            # 鮮やかすぎ、暗いカードの上で縁が暗く縁取られる）
            inner = ~region & ~ring
            fg = rgb
            if inner.any():
                _d, lab = cv2.distanceTransformWithLabels(
                    (~inner).astype(np.uint8), cv2.DIST_L2, 3, labelType=cv2.DIST_LABEL_PIXEL)
                iy, ix = np.nonzero(inner)
                lut = np.zeros((int(lab.max()) + 1, 3), np.float32)
                lut[lab[iy, ix]] = rgb[iy, ix]
                fg = lut[lab]
            dv = fg - bg
            dd = (dv * dv).sum(axis=2)
            proj = np.clip(((rgb - bg) * dv).sum(axis=2) / np.maximum(dd, 1e-6), 0.0, 1.0)
            # 内側の色が地の色に近すぎる所は color-to-alpha（色は地の色を抜いたもの）
            up = np.where(rgb > bg, (rgb - bg) / np.maximum(1.0 - bg, 1e-6),
                          (bg - rgb) / np.maximum(bg, 1e-6))
            ca = np.clip(up.max(axis=2), 0.0, 1.0)
            cc = np.clip(bg + (rgb - bg) / np.maximum(ca, 1e-6)[..., None], 0.0, 1.0)
            ok = dd > (2 * _BG_TOL) ** 2
            ea = np.where(ok, proj, ca)
            ecol = np.where(ok[..., None], fg, cc)
            rgb = np.where(ring[..., None], ecol, rgb).astype(np.float32)
            a = np.where(region, 0.0, np.where(ring, a * ea, a)).astype(np.float32)
        elif uniform >= _BG_UNIFORM:
            dist = np.max(np.abs(rgb - bg), axis=2)
            weight = _smoothstep(np, _PLATE_LO, _PLATE_HI, dist).astype(np.float32)
    prem = np.empty(rgba.shape, np.float32)
    prem[..., :3] = rgb * a[..., None]
    prem[..., 3] = a
    return prem, weight, (None if bg is None else tuple(float(v) for v in bg))


def _content_box(np, prem, weight):
    """光を当てる画素（不透明かつ重み > 0）の外接矩形 (x0, y0, x1, y1)（元の画像の px）"""
    m = (prem[..., 3] > 1e-3) & (weight > 1e-3)
    rows = np.flatnonzero(m.any(axis=1))
    if not len(rows):
        return None
    cols = np.flatnonzero(m.any(axis=0))
    return (int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1)


# --- 時刻（標準ライブラリだけ）---

def _show_state(spec, t):
    """t 秒の (不透明度, 大きさ)"""
    alpha, s = 1.0, 1.0
    en = spec["enter"]
    if en is not None:
        if t <= en["t"]:
            return 0.0, en["scale"]
        u = (t - en["t"]) / en["dur"]
        if u < 1.0:
            p = en["_f"](u)
            alpha = min(1.0, max(0.0, u))
            s = en["scale"] + (1.0 - en["scale"]) * p
    ex = spec["exit"]
    if ex is not None and t > ex["t"]:
        u = (t - ex["t"]) / ex["dur"]
        if u >= 1.0:
            return 0.0, ex["scale"]
        p = ex["_f"](u)
        alpha *= 1.0 - u
        s *= 1.0 + (ex["scale"] - 1.0) * p
    return alpha, s


def _sweep_pos(sw, t):
    """sweep の進み具合（0..1。イージング後）。通っていなければ None"""
    if t <= sw["t"] or t >= sw["t"] + sw["dur"]:
        return None
    return sw["_f"]((t - sw["t"]) / sw["dur"])


# --- 組み立て役 ---

class Glint:
    """glint() が返す組み立て役。光（sweep）と出入り（enter / exit）を積んで build() で
    動画 Object にする。時刻 t はすべて build() で作る動画の先頭を 0 とした秒。
    メソッドは自分を返す。"""

    def __init__(self, path, img_size, size, matte, radius, palette):
        self._path = path
        self._img = img_size
        self._size = size
        self._matte = matte
        self._radius = radius
        self._palette = palette
        self._sweeps = []
        self._enter = None
        self._exit = None

    def __repr__(self):
        w, h = self._img
        return (f"Glint({os.path.basename(self._path)} {w}x{h}, {self._size[0]}x{self._size[1]}, "
                f"matte={self._matte}, sweep={len(self._sweeps)})")

    def sweep(self, t=0, *, dur=0.9, angle=20, width=0.22, strength=0.45, color="white",
              easing="ease_in_out_sine"):
        """t 秒から dur 秒で、斜めの光の帯を画像の左から右へ一度通す。

        dur: 帯が画像の中身の外（左）から外（右）まで通る秒。
        angle: 帯の傾き（度。-60〜60。正で「/」、負で「\\」、0 で縦）。
        width: 帯の幅（帯が通る道のり＝画像の中身を帯に垂直に測った長さ に対する割合。0.02〜1）。
        strength: 帯の中心の明るさ（0〜1。スクリーンで掛けるので白い画素は変わらない）。
        color: 光の色（既定は白。図の色の名前・'#ffe8a0'・(r, g, b[, a])。a は strength に掛かる）。
        easing: 帯の速さの変化（イージングの名前・関数・Expr。None は等速）。
        何回も通すときは sweep を続けて積む（重なってもよい）。
        """
        fn = "glint.sweep"
        t = _time(fn, "t", t)
        f, key = fk.easing(fn, easing)
        self._sweeps.append({
            "t": t, "dur": _dur(fn, "dur", dur), "angle": _num(fn, "angle", angle, -60, 60),
            "width": _num(fn, "width", width, 0.02, 1.0),
            "strength": _num(fn, "strength", strength, 0, 1),
            "color": list(fk.color(fn, color, self._palette)), "easing": key, "_f": f})
        return self

    def _show(self, fn, t, dur, scale, easing):
        f, key = fk.easing(fn, easing)
        return {"t": _time(fn, "t", t), "dur": _dur(fn, "dur", dur), "scale": _scale(fn, scale),
                "easing": key, "_f": f}

    def enter(self, t=0, *, dur=0.5, scale=0.96, easing="ease_out_cubic"):
        """t 秒から dur 秒で現れる（不透明度 0 → 1、大きさ scale → 1。scale は 0.5〜1）。
        t より前のコマは透明。1回だけ積める。"""
        fn = "glint.enter"
        if self._enter is not None:
            raise ValueError(f"{fn}: enter は1回だけ積めます")
        en = self._show(fn, t, dur, scale, easing)
        if self._exit is not None and en["t"] + en["dur"] > self._exit["t"]:
            raise ValueError(f"{fn}: enter（〜{en['t'] + en['dur']:g} 秒）は exit"
                             f"（{self._exit['t']:g} 秒）より前に終えてください")
        self._enter = en
        return self

    def exit(self, t, *, dur=0.4, scale=1.0, easing="ease_in_cubic"):
        """t 秒から dur 秒で消える（不透明度 1 → 0、大きさ 1 → scale。scale は 0.5〜1）。
        消えた後のコマは透明。1回だけ積める。"""
        fn = "glint.exit"
        if self._exit is not None:
            raise ValueError(f"{fn}: exit は1回だけ積めます")
        ex = self._show(fn, t, dur, scale, easing)
        if self._enter is not None and self._enter["t"] + self._enter["dur"] > ex["t"]:
            raise ValueError(f"{fn}: exit（{ex['t']:g} 秒）は enter が終わる"
                             f"（{self._enter['t'] + self._enter['dur']:g} 秒）より後にしてください")
        self._exit = ex
        return self

    # -- 組み立て --

    def _spec(self):
        return {"size": self._size, "matte": self._matte, "radius": self._radius,
                # 光どうしはスクリーンで重ねる（順に依らない）ので、積む順は鍵に入れない
                "sweeps": sorted(self._sweeps, key=lambda s: (s["t"], repr(self._plain(s)))),
                "enter": self._enter, "exit": self._exit}

    @staticmethod
    def _plain(d):
        return None if d is None else {k: v for k, v in d.items() if k != "_f"}

    def _params(self, spec):
        """鍵に入れる値（絵に効くものだけ。同一出力なら同一鍵）"""
        return {
            "size": list(spec["size"]), "matte": spec["matte"],
            "radius": spec["radius"] if spec["matte"] == "plate" else None,
            "sweeps": [self._plain(s) for s in spec["sweeps"]],
            "enter": self._plain(spec["enter"]), "exit": self._plain(spec["exit"]),
        }

    def _end_time(self, spec):
        ends = [s["t"] + s["dur"] for s in spec["sweeps"]]
        for d in (spec["enter"], spec["exit"]):
            if d is not None:
                ends.append(d["t"] + d["dur"])
        return max(ends, default=0.0)

    def build(self, duration=None):
        """積んだ光と出入りを、1本の透過動画 Object にする（framekit.build）。

        duration: 秒。省略時は最後の光・出入りが終わった絵のコマまで（何も無ければ1コマ）。
          time() でそれより長く表示すると最後のコマが残る（exit の後なら透明）。
        戻り値の Object の obj.figure に次が付く:
          figure.size   … キャンバスの (幅, 高さ)（= size）
          figure.box    … 等倍のときに画像が来る範囲 (x0, y0, x1, y1)（キャンバスの px）
          figure.matte  … 実際の地の扱い（'auto' を解決したもの）
          figure.sweeps … 各 sweep の (始まり, 終わり) の秒
        """
        fn = "glint.build"
        spec = self._spec()
        fps_frac = _fps_fraction(_resolve_fps(fn, None))
        if duration is None:
            end = Fraction(repr(self._end_time(spec))) * fps_frac
            n_frames = math.ceil(end) + 1 if end > 0 else 1
        else:
            n_frames = fk.n_frames_for(_dur(fn, "duration", duration), fps_frac)
        W, H = spec["size"]
        renderer = None

        def draw(i):
            nonlocal renderer
            if renderer is None:
                renderer = _Renderer(_resolve_image("glint", self._path)[1], spec)
            try:
                return renderer.frame(float(Fraction(i) / fps_frac))
            finally:
                # 最後のコマの後は画像と配列を手放す。再描画時には作り直す。
                if i == n_frames - 1:
                    renderer = None

        k, ox, oy, dw, dh = _fit(spec["size"], *self._img)
        info = SimpleNamespace(size=(W, H), box=(ox, oy, ox + dw, oy + dh), matte=spec["matte"],
                               sweeps=[(s["t"], s["t"] + s["dur"]) for s in spec["sweeps"]],
                               image_size=tuple(self._img))
        return fk.build(fn, kind="glint", ver=_GLINT_VER, params=self._params(spec),
                        draw=draw, n_frames=n_frames, size=(W, H), files=[self._path],
                        info=info)


def _fit(size, w, h):
    """画像 w×h を枠 size に収めたときの (倍率, 左上 x, y, 幅, 高さ)（キャンバスの px）"""
    W, H = size
    k = min(W / w, H / h)
    dw, dh = w * k, h * k
    return k, (W - dw) / 2.0, (H - dh) / 2.0, dw, dh


# --- 描画 ---

def _band(np, d):
    """帯の明るさの形（d は帯の中心からの距離を半幅で割ったもの）。中心で 1、|d| ≥ 1 で 0"""
    ad = np.abs(d)
    soft = np.where(ad < 1.0, 0.5 + 0.5 * np.cos(np.pi * np.minimum(ad, 1.0)), 0.0)
    dc = ad / _CORE
    core = np.where(dc < 1.0, 0.5 + 0.5 * np.cos(np.pi * np.minimum(dc, 1.0)), 0.0)
    return (1.0 - _CORE_MIX) * soft + _CORE_MIX * core


class _Renderer:
    """画像の取り込み・等倍の絵・前のコマを持つ描画役（build の draw の中で作る）"""

    def __init__(self, rgba, spec):
        deps = fk.need("glint")
        self.np, self.cv2 = np, cv2 = deps.np, deps.cv2
        self.spec = spec
        self.W, self.H = spec["size"]
        ih, iw = rgba.shape[:2]
        prem, weight, _bg = _prepare("glint", rgba, spec["matte"])
        k, ox, oy, dw, dh = _fit(spec["size"], iw, ih)
        self.k, self.ox, self.oy, self.dw, self.dh = k, ox, oy, dw, dh
        if spec["matte"] == "plate":
            # 角を丸めた板: 元の画像の px で角丸の被覆率を掛ける（半径は画面の px）
            r = spec["radius"] / k
            if r > 0:
                cov = fk.canvas(iw, ih)
                fk.rect(cov, (0, 0, iw, ih), (255, 255, 255, 255), radius=min(r, iw / 2, ih / 2))
                prem *= cov[..., 3:4]
        box = _content_box(np, prem, weight)
        if box is None:
            raise ValueError("glint: 画像に不透明な画素がありません（光を当てる所が無い）")
        # 一番縮めたときでも 1 未満の倍率なら、2^-n（≥ その倍率）へ面積平均で縮めておく
        s_min = min([1.0] + [d["scale"] for d in (spec["enter"], spec["exit"]) if d is not None])
        k_min = k * s_min
        n = 0 if k_min >= 1.0 else int(math.floor(math.log2(1.0 / k_min) + 1e-9))
        step = 1 << n
        # 光の受け皿 q = 重み·(a − cp)（チャンネルごと）。光は cp + L·光の色·q で、q は画素の
        # 値に線形なので、絵と同じ変換で写してから掛けても縁で食い違わない
        q = (weight[..., None] * (prem[..., 3:4] - prem[..., :3])).astype(np.float32)
        ph, pw = (-ih) % step, (-iw) % step
        srcs = []
        for arr in (prem, q):
            if ph or pw:      # 2^n の倍数へ透明で広げる（縮めた端の被覆率がそのまま正しい）
                arr = cv2.copyMakeBorder(arr, 0, ph, 0, pw, cv2.BORDER_CONSTANT, value=0)
            if step > 1:
                arr = cv2.resize(arr, (arr.shape[1] // step, arr.shape[0] // step),
                                 interpolation=cv2.INTER_AREA)
            arr = np.ascontiguousarray(arr, dtype=np.float32)
            arr.flags.writeable = False
            srcs.append(arr)
        self.src, self.q = srcs
        self.step = step
        # 帯の道のり: 中身の外接矩形の角を、帯の進む向きへ射影した範囲（等倍のキャンバスの px。
        # 画像の中心が原点）
        cx, cy = iw / 2.0, ih / 2.0
        self.paths = []
        for sw in spec["sweeps"]:
            th = math.radians(sw["angle"])
            nx, ny = math.cos(th), math.sin(th)
            ps = [((x - cx) * nx + (y - cy) * ny) * k
                  for x in (box[0], box[2]) for y in (box[1], box[3])]
            lo, hi = min(ps), max(ps)
            hw = sw["width"] * (hi - lo) / 2.0
            col = sw["color"]
            light = [sw["strength"] * col[3] / 255.0 * col[c] / 255.0 for c in range(3)]
            self.paths.append((nx, ny, lo - hw, hi + hw, hw, light))
        self.still = None          # 等倍のときの (x0, y0, 事前乗算 RGBA, q)
        self.still_u8 = None       # 等倍・不透明・光なしの uint8
        self.last_sig = None
        self.last = None

    def _place(self, s):
        """大きさ s の絵を写す: (x0, y0, 事前乗算 RGBA, 光の受け皿 q)。キャンバスに切る"""
        np = self.np
        f = self.k * self.step
        sh, sw_ = self.src.shape[:2]
        # 元の画像の中心（縮めた絵の px）を、画像の箱の中心へ置く
        pcx, pcy = self.dw / (2.0 * f), self.dh / (2.0 * f)
        x = self.ox + self.dw / 2.0 - pcx
        y = self.oy + self.dh / 2.0 - pcy
        bx0, by0, bx1, by1 = fk.affine_box(sw_, sh, x, y, scale=f * s, pivot=(pcx, pcy))
        bx0, by0 = max(0, bx0), max(0, by0)
        bx1, by1 = min(self.W, bx1), min(self.H, by1)
        if bx0 >= bx1 or by0 >= by1:
            return 0, 0, np.zeros((1, 1, 4), np.float32), np.zeros((1, 1, 3), np.float32)
        box = (bx0, by0, bx1, by1)
        x0, y0, patch = fk.warp_patch(self.src, x, y, scale=f * s, pivot=(pcx, pcy), box=box)
        _x, _y, q = fk.warp_patch(self.q, x, y, scale=f * s, pivot=(pcx, pcy), box=box)
        return x0, y0, patch, q

    def _light(self, x0, y0, patch, q, s, poss):
        """パッチに光を当てる（スクリーン）。poss は各 sweep の帯の中心（等倍の px）か None。

        帯が触れる列の範囲 [c0, c1)（パッチの列）だけを計算し、(c0, c1, 光を当てたその列の
        事前乗算 RGBA) を返す。帯がどこにも触れなければ None。
        """
        np = self.np
        h, w = patch.shape[:2]
        cx = self.ox + self.dw / 2.0
        cy = self.oy + self.dh / 2.0
        bx, by = x0 + 0.5 - cx, y0 + 0.5 - cy          # パッチの (0, 0) の画素の中心（中心が原点）
        bands = []
        c0, c1 = w, 0
        for (nx, ny, _lo, _hi, hw, light), c in zip(self.paths, poss):
            if c is None:
                continue
            # 帯は画像の座標についていく（大きさ s で縮めた分、位置も幅も s 倍）。
            # |x·nx + y·ny − c·s| < hw·s を満たす x の範囲を、パッチの上下の行から求める
            yn = sorted((by * ny, (by + h - 1) * ny))
            xa = ((c - hw) * s - yn[1]) / nx - bx
            xb = ((c + hw) * s - yn[0]) / nx - bx
            a0, a1 = max(0, int(math.floor(xa)) - 1), min(w, int(math.ceil(xb)) + 2)
            if a0 >= a1:
                continue
            bands.append((nx, ny, c, hw, light))
            c0, c1 = min(c0, a0), max(c1, a1)
        if not bands:
            return None
        xs = np.arange(c0, c1, dtype=np.float32) + np.float32(bx)
        ys = np.arange(h, dtype=np.float32) + np.float32(by)
        keep = np.ones((h, c1 - c0, 3), np.float32)   # 光の残り＝ Π(1 − L_i·光の色)
        for nx, ny, c, hw, light in bands:
            d = (xs[None, :] * np.float32(nx) + ys[:, None] * np.float32(ny)
                 - np.float32(c * s)) / np.float32(hw * s)
            L = _band(np, d).astype(np.float32)
            for ch in range(3):
                if light[ch] > 0:
                    keep[..., ch] *= 1.0 - L * np.float32(light[ch])
        out = patch[:, c0:c1].copy()
        keep -= 1.0
        keep *= q[:, c0:c1]
        out[..., :3] -= keep              # cp + (1 − Π(1 − L·光の色))·q
        return c0, c1, out

    def frame(self, t):
        np = self.np
        alpha, s = _show_state(self.spec, t)
        poss = []
        for sw, (nx, ny, start, end, hw, light) in zip(self.spec["sweeps"], self.paths):
            p = _sweep_pos(sw, t)
            poss.append(None if p is None else start + (end - start) * p)
        sig = (round(alpha, 6), round(s, 6),
               tuple(None if c is None else round(c, 4) for c in poss))
        if sig == self.last_sig:
            return self.last
        out = np.zeros((self.H, self.W, 4), np.uint8)
        if alpha > 0.0:
            if s == 1.0:
                if self.still is None:
                    self.still = self._place(1.0)
                x0, y0, patch, q = self.still
            else:
                x0, y0, patch, q = self._place(s)
            h, w = patch.shape[:2]
            if s == 1.0 and alpha == 1.0:
                # 等倍・不透明の光の無い絵は1回だけ uint8 にして使い回す
                if self.still_u8 is None:
                    self.still_u8 = fk.to_rgba8(np.ascontiguousarray(patch))
                out[y0:y0 + h, x0:x0 + w] = self.still_u8
            else:
                base = patch * np.float32(alpha) if alpha < 1.0 else patch
                out[y0:y0 + h, x0:x0 + w] = fk.to_rgba8(np.ascontiguousarray(base))
            lit = self._light(x0, y0, patch, q, s, poss)
            if lit is not None:
                c0, c1, part = lit
                if alpha < 1.0:
                    part *= np.float32(alpha)
                out[y0:y0 + h, x0 + c0:x0 + c1] = fk.to_rgba8(np.ascontiguousarray(part))
        out.flags.writeable = False
        self.last_sig, self.last = sig, out
        return out


# --- 公開の入口 ---

def glint(image, *, size=None, matte="auto", radius=12, colors=None):
    """ロゴなどの画像に、斜めの光の帯が通り過ぎる演出（ライトスイープ）の組み立て役を返す。

    image: 画像ファイルのパス（透過 PNG・不透過の PNG / JPEG。見つからなければ asset() で探す）。
      SVG は PNG にしてから渡す。
    size: 画像を収める枠 (幅, 高さ) px（縦横比を保って収め、中央に置く。拡大もする）。
      None は画像の大きさのまま。16〜4096。キャンバスはこの大きさ（余りは透明）。
    matte: 地の扱い。'auto'（既定。透明な画素があれば 'alpha'、無ければ 'plate'）・
      'alpha'（画像のアルファのまま）・'plate'（不透過の画像を角を丸めた板として出し、
      地の色の画素には光を当てない）・'key'（縁につながる地の色を透明にする）。
    radius: 'plate' の角の丸み px（画面の px。0 で角を丸めない）。
    colors: 色の名前の差し替え（framekit の図の色。光の color に名前を使うときだけ効く）。

    光と出入り（時刻 t は動画の先頭からの秒。メソッドは自分を返す）:
      g.sweep(t=0, dur=0.9, angle=20, width=0.22, strength=0.45, color="white",
              easing="ease_in_out_sine")
      g.enter(t=0, dur=0.5, scale=0.96, easing="ease_out_cubic")
      g.exit(t, dur=0.4, scale=1.0, easing="ease_in_cubic")
      obj = g.build(duration=None)   → obj.figure.box・obj.figure.matte

    鍵（framekit.build）: 画像の内容指紋（パスは入らない）・枠の寸法・地の扱い（'auto' は
    解決した値。角の丸みは 'plate' のときだけ）・光と出入りの並び・描画の版 _GLINT_VER。
    """
    fn = "glint"
    path, rgba = _resolve_image(fn, image)
    ih, iw = rgba.shape[:2]
    if size is None:
        size = (iw, ih)
    if (not isinstance(size, (tuple, list)) or len(size) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) for v in size)):
        raise ValueError(f"{fn}: size は (幅, 高さ) の整数で指定してください: {size!r}")
    W, H = int(size[0]), int(size[1])
    for name, v in (("幅", W), ("高さ", H)):
        if not _SIZE_MIN <= v <= _SIZE_MAX:
            raise ValueError(
                f"{fn}: size の{name}は {_SIZE_MIN}〜{_SIZE_MAX} px にしてください: {v}"
                f"（size を省いたときは画像の大きさ。大きな画像は size で収める）")
    _require_choice(fn, "matte", matte, _MATTES)
    np = fk.need(fn).np
    resolved = _matte_of(np, rgba, matte)
    radius = _num(fn, "radius", radius, 0, 400)
    pal = fk.palette(fn, colors)
    # 光を当てる所が無い・地の色が一様でない（key）は構築時に ValueError（dry_run は描かない）
    prem, weight, _bg = _prepare(fn, rgba, resolved)
    if _content_box(np, prem, weight) is None:
        raise ValueError(f"{fn}: 画像に光を当てる画素がありません（全部が透明か地の色）: {path}")
    return Glint(path, (iw, ih), (W, H), resolved, radius, pal)
