# -*- coding: utf-8 -*-
"""chart(): データのグラフ（線・階段・面・ローソク足・棒）を描く動く図（framekit.build の上）

値の列を時刻に合わせて左から描き、線の先に今の値の札を付け、基準線・出来事の印・区間の帯・
縦の寄りを秒で書いて、1本の透過動画 Object にする。

    c = chart(y_format="{v:.1f}万")
    c.series(prices, kind="step", at=0.5, dur=6, tag="{v:.1f}万円", glow=True)   # (時刻, 値) の列
    c.rule(61, "公開価格 61万円", dash=True)                  # 水平の基準線
    c.mark("9:27", "9:27 誤発注", color="accent")            # 線の先が 9:27 に着く秒に出る
    c.span("9:30", "9:45", "売買停止")                        # 区間の帯
    c.zoom((56, 64), at=3.0)                                  # 縦に寄る（なめらかに）
    fig = c.build()
    fig.time() <= move(x=0.5, y=0.5, anchor="center")

    bg = chart(mode="background")                             # 字幕の後ろで薄く流れる背景
    bg.series(prices, dur=20)

要素（at はこの図の動画の先頭を 0 とした秒。x は軸と同じ種類）:
  series(data, kind='line', ...)  値の列。kind は line / step / area / candle / bar
  rule(y, label=None, ...)        水平の基準線（右端の上に札）
  mark(x, label=None, ...)        出来事の印（縦の線と、図の上の札）。at の既定は線の先が x に着く秒
  span(a, b, label=None, ...)     区間の帯（取引停止など）。at の既定は線の先が a に着く秒
  zoom(y, at=, dur=1.0)           縦の範囲を (lo, hi) へなめらかに変える（None で元へ）
  build(duration=None)            動画 Object（obj.figure.x(v)・y(v)・t_at(x)・plot が付く）
  t_at(x)                         最初の系列の線の先が x に着く秒（字幕・効果音の時刻合わせ）

配置の決まり
------------
- 縦の並び（上から）: 印と帯の札の段 → 図の枠（plot）→ 目盛りの字。縦の目盛りの字は枠の左。
  今の値の札（tag）は線の先の右に付いてくるので、枠の右に札の幅だけ余白を取る（最後の値の札が
  枠の外に収まる）。
- 印と帯の札は x の位置の上の段に、x の順に「低い段から」置く。ほかの札と重なる・印の線が
  下の段の札を貫く・下の段の札の線を覆う所は避けて、上の段へずらす（timeline の mark と同じ考え方）。
- 今の値の札は線の先の右に置き、基準線の札・値の札・ほかの系列の札と重なるときは上下へずらす。
  ずらし方はコマごとに選び直さず、前後 0.2 秒の平均でなめらかに動かす（ぱたぱた入れ替わらない）。
- 基準線の札は線の右端の上（枠の上端に近ければ下）。字はどれも黒の縁取りつき（札は下地つき）。
- 目盛りは字が重ならない間隔を自動で選ぶ（間引く）。範囲は値・基準線を含むきりのよい値。

見え方の型
----------
- mode='main'（主役）: くっきり。軸・目盛り・札あり。
- mode='background'（背景）: 字を出さず、不透明度 0.35・太めの線を半分の解像度で描いてぼかし、
  毎秒 drift px で左へ流れる（横に長く描いて、見える窓をずらす）。字幕の後ろに置く。

絵の作り
--------
- 線は framekit の polyline（被覆率の面積でアンチエイリアス）。描き終えた所は 16 点ずつの塊で
  被覆率の層に描いて使い回し、毎コマは伸びている塊だけを描き足す（塊の和は最大値で取るので、
  順に描いたコマと、そのコマだけを描いたコマが画素まで同じ）。
- 面（area）の塗りは列ごとの縦の被覆率で、上から下へ薄れる。
- ローソク足・棒は描き終えたものを層にまとめて使い回し、立ち上がっている途中のものだけ毎コマ描く。
- 進み具合が前のコマと同じコマは前のコマを返す。

numpy・opencv-python・Pillow を使う（optional 依存。framekit.need で遅延 import）。
構築（build）にも要る（目盛りの字の寸法を Pillow で測る）。
"""

import datetime as _dt
import math
import numbers
import re
import warnings
from fractions import Fraction

import scriptvedit.framekit as fk
from scriptvedit.context import current_project
from scriptvedit.stillseq import _fps_fraction, _resolve_fps, _resolve_size
from scriptvedit.validate import _require_choice, _require_number


# --- 定数 ---

# 描画の版（配置の手順・描き方・時刻の決め方を変えたら上げる。配置の結果は鍵に
# 入らないので、配置の手順を変えたときも必ず上げる）
_CHART_VER = "1"

_KINDS = ("line", "step", "area", "candle", "bar")
_MODES = ("main", "background")
_CONTINUOUS = ("line", "step", "area")
_TIME_UNITS = ("minute", "hour", "day", "month", "year")

_MAX_POINTS = 20000      # 1系列の点の上限
_MAX_SERIES = 8
_MAX_ITEMS = 200         # rule・mark・span・zoom の合計の上限
_MAX_TICKS = 60
_AUTO_TICKS_MAX = 8      # 自動の目盛りの数の上限（棒の x は 16）

# 既定の現れる秒
_DUR = {"series": 2.0, "rule": 0.6, "mark": 0.5, "span": 0.5, "zoom": 1.0}

# 日時の軸の自動の目盛りの候補（小さい順）
_TIME_STEPS = (
    ("minute", 1), ("minute", 5), ("minute", 10), ("minute", 15), ("minute", 30),
    ("hour", 1), ("hour", 2), ("hour", 3), ("hour", 6), ("hour", 12),
    ("day", 1), ("day", 2), ("day", 7), ("day", 14),
    ("month", 1), ("month", 2), ("month", 3), ("month", 6),
    ("year", 1), ("year", 2), ("year", 5), ("year", 10), ("year", 20), ("year", 50),
)
_UNIT_SEC = {"minute": 60.0, "hour": 3600.0, "day": 86400.0, "month": 2629746.0,
             "year": 31556952.0}
_WEEKDAYS = "月火水木金土日"
_CLOCK_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$")
_EPOCH = _dt.datetime(2000, 1, 1)
_EPOCH_UTC = _dt.datetime(2000, 1, 1, tzinfo=_dt.timezone.utc)

# 線と形（px。主役の 1 倍の寸法）
_LINE_W = 4.0            # 線の太さ（line / step）
_AREA_LINE_W = 3.0       # 面の上の線の太さ
_AREA_ALPHA = 0.32       # 面の塗りの上端の不透明度（下へ 0 まで薄れる）
_GRID_W = 2.0            # 横の格子
_GRID_ALPHA = 0.55       # 格子の不透明度（dim の色）
_AXIS_W = 2.0            # 下の軸の線
_TICK_LEN = 8            # 下の目盛りの線の長さ
_TICK_GAP = 6            # 目盛りの線と字の間
_YLABEL_GAP = 16         # 縦の目盛りの字と枠の間
_RULE_W = 2.0
_RULE_DASH = (12.0, 9.0)
_RULE_TEXT_GAP = 6       # 基準線と札の間
_RULE_INSET = 10         # 札の右端と枠の右端の間
_MARK_W = 2.0
_MARK_DASH = (8.0, 8.0)
_MARK_STEM_GAP = 6       # 印の線の上端と札の間
_SPAN_ALPHA = 0.16
_SPAN_HATCH_ALPHA = 0.22
_BAND_GAP = 8            # 札の段どうしの間
_BAND_BOTTOM = 14        # 一番下の段と枠の間
_BOX_GAP = 12            # 札どうしの最小の隙間（横）
_HEAD_R = 5.0            # 線の先の点
_GLOW_R = 11.0           # 線の先の光（ぼかした点）
_GLOW_SOFT = 12.0
_GLOW_ALPHA = 0.30
_TAG_GAP = 16            # 線の先と札の左端の間
_TAG_SLOT = 0.62         # 札を上下へずらす1段（札の高さの倍）
_TAG_SMOOTH = 0.2        # 札のずらしをならす前後の秒
_TAG_FADE = 0.25
_VALUE_GAP = 10          # 点と値の札の間
_VALUE_FADE = 0.3
_DOT_R = 6.0             # 点の少ない線の点（24 点以下）
_DOTS_MAX = 24
_FEW_PAD = 0.25          # 点の少ない線の左右の余白（点の間隔の倍）
_BODY = 0.62             # ローソク足の胴の幅（間隔の倍）
_BAR = 0.62              # 棒の幅（間隔の倍）
_BAR_R = 4.0             # 棒の先の角の丸み
_RISE = 0.3              # ローソク足・棒が立ち上がる秒（dur が短ければ縮む）
_INTRO = 0.4             # 枠と目盛りが現れる秒
_HALO = 3                # 文字の黒い縁取り
_CHUNK = 16              # 線を使い回す塊の点の数

# 背景の型
_BG_OPACITY = 0.35
_BG_DRIFT = 14.0         # px/秒
_BG_SCALE = 0.5          # 描く解像度
_BG_WIDTH = 1.8          # 線の太さの倍
_BG_BLUR = 1.3           # ぼかし（半分の解像度の px）

_DOWN = "#727a85"        # 下がりの既定の色（ローソク足の陰線・負の棒）

# 進み具合の比べ方（このけたで同じなら同じ絵）
_SIG_DIGITS = 5


# --- 小さな道具 ---

def _clamp01(v):
    return 0.0 if v <= 0.0 else (1.0 if v >= 1.0 else v)


def _ease_io(u):
    """ease_in_out_cubic"""
    u = _clamp01(u)
    return 4 * u * u * u if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2


def _ease_out(u):
    """ease_out_cubic"""
    u = _clamp01(u)
    return 1 - (1 - u) ** 3


def _seg(p, a, b):
    """p（0..1）のうち [a, b] の区間の進み（0..1）"""
    if b <= a:
        return 1.0 if p >= b else 0.0
    return _clamp01((p - a) / (b - a))


def _overlap(a, b, m=0.0):
    """矩形 (x0, y0, x1, y1) どうしが（隙間 m を含めて）重なるか"""
    return a[0] < b[2] + m and b[0] < a[2] + m and a[1] < b[3] + m and b[1] < a[3] + m


def _is_real(v):
    return isinstance(v, numbers.Real) and not isinstance(v, bool)


def _clean(v):
    """表示用の数（丸めの端数を落とし、整数の値は int）"""
    r = round(float(v), 9)
    if r == 0:
        r = 0.0
    return int(r) if float(r).is_integer() else r


def _decimals(step):
    """step の倍数を表すのに要る小数のけた"""
    for d in range(0, 7):
        if abs(round(step, d) - step) <= 1e-9 * max(1.0, abs(step)):
            return d
    return 6


def _check_text(fn, name, v):
    if v is None:
        return None
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{fn}: {name} は空でない文字列で指定してください: {v!r}")
    return v


def _check_at(fn, at, allow_none=False):
    if at is None and allow_none:
        return None
    _require_number(fn, "at", at, 0, None)
    return float(at)


def _check_dur(fn, dur, kind):
    if dur is None:
        return _DUR[kind]
    _require_number(fn, "dur", dur, 0, 3600)
    if dur <= 0:
        raise ValueError(f"{fn}: dur は 0 より大きくしてください: {dur!r}")
    return float(dur)


def _check_format(fn, name, fmt, fields):
    """書式の文字列を検証する（fields で1回当ててみる）"""
    if not isinstance(fmt, str) or not fmt:
        raise TypeError(f"{fn}: {name} は書式の文字列（'{{v}}万円' など）で指定してください: {fmt!r}")
    try:
        fmt.format(**fields)
    except (KeyError, IndexError, ValueError) as e:
        raise ValueError(
            f"{fn}: {name} {fmt!r} を当てられません（{e}。使える名前: "
            f"{', '.join(sorted(fields))}）") from None
    return fmt


def _nice_steps(lo_exp, hi_exp):
    for k in range(lo_exp, hi_exp + 1):
        for m in (1, 2, 5):
            yield m * 10.0 ** k


# --- 横の軸（数値・日時・時刻） ---

class _XAxis:
    """x の値（数値・日時・"HH:MM"）を数（float）にする。種類は最初の値で決まる。

    num: そのままの数。time: 2000-01-01（タイムゾーンつきは UTC）からの秒。
    clock: その日の 0 時からの秒（"9:27" → 34020）。
    """

    def __init__(self, fn):
        self.fn = fn
        self.kind = None
        self.tz = None           # time の軸がタイムゾーンつきなら最初の値の tzinfo
        self.aware = None

    def _classify(self, name, v):
        fn = self.fn
        if _is_real(v):
            f = float(v)
            if not math.isfinite(f):
                raise ValueError(f"{fn}: {name} に NaN/Infinity は使えません: {v!r}")
            return "num", f, None
        if isinstance(v, str):
            m = _CLOCK_RE.match(v)
            if m:
                h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
                if h > 47 or mi > 59 or s > 59:
                    raise ValueError(f"{fn}: {name} は 'HH:MM'（0:00〜47:59）で指定してください: {v!r}")
                return "clock", float(h * 3600 + mi * 60 + s), None
            try:
                v = _dt.datetime.fromisoformat(v.strip())
            except ValueError:
                raise ValueError(
                    f"{fn}: {name} は 'HH:MM' か ISO の日時の文字列（'2005-12-08 09:27' など）で"
                    f"指定してください: {v!r}") from None
        if isinstance(v, _dt.datetime):
            if v.tzinfo is not None:
                return "time", (v - _EPOCH_UTC).total_seconds(), v.tzinfo
            return "time", (v - _EPOCH).total_seconds(), None
        if isinstance(v, _dt.date):
            return "time", (_dt.datetime(v.year, v.month, v.day) - _EPOCH).total_seconds(), None
        raise TypeError(
            f"{fn}: {name} は数値・日時（datetime・date・ISO の文字列）・時刻（'HH:MM'）の"
            f"どれかで指定してください: {v!r}")

    def conv(self, name, v):
        kind, f, tz = self._classify(name, v)
        if self.kind is None:
            self.kind = kind
            if kind == "time":
                self.aware = tz is not None
                self.tz = tz
        elif kind != self.kind:
            names = {"num": "数値", "time": "日時", "clock": "時刻（'HH:MM'）"}
            raise TypeError(
                f"{self.fn}: {name} は{names[self.kind]}で指定してください（この図の x は"
                f"{names[self.kind]}の軸）: {v!r}")
        elif kind == "time" and (tz is not None) != self.aware:
            raise ValueError(f"{self.fn}: {name} のタイムゾーンの有無がほかの x と違います: {v!r}")
        return f

    def key(self, f):
        """鍵に入れる値（数はそのまま、日時は ISO の文字列）"""
        if self.kind in (None, "num", "clock"):
            return fk.norm(f)
        return self.at(f).isoformat()

    def at(self, f):
        """数 → datetime（time と clock）"""
        if self.kind == "time" and self.aware:
            return (_EPOCH_UTC + _dt.timedelta(seconds=f)).astimezone(self.tz)
        return _EPOCH + _dt.timedelta(seconds=f)

    def back(self, d):
        if self.kind == "time" and self.aware:
            return (d - _EPOCH_UTC).total_seconds()
        return (d - _EPOCH).total_seconds()

    def fields(self, f):
        if self.kind in (None, "num"):
            return {"v": _clean(f)}
        d = self.at(f)
        out = {"hour": d.hour if self.kind == "time" else int(f // 3600),
               "minute": d.minute, "second": d.second}
        if self.kind == "time":
            out.update(year=d.year, month=d.month, day=d.day, wd=_WEEKDAYS[d.weekday()])
        return out

    def sample_fields(self):
        if self.kind in (None, "num"):
            return {"v": 1}
        out = {"hour": 9, "minute": 27, "second": 0}
        if self.kind == "time":
            out.update(year=2005, month=12, day=8, wd="木")
        return out

    def slot(self):
        """値が1つしか無いときの棒の間隔"""
        return {"num": 1.0, "time": 86400.0, "clock": 60.0}.get(self.kind, 1.0)

    # --- 目盛り ---

    def time_ticks(self, lo, hi, unit, n):
        t0, t1 = self.at(lo), self.at(hi)
        out = []
        zero = dict(hour=0, minute=0, second=0, microsecond=0)
        if unit == "year":
            y = t0.year + (1 if t0 > t0.replace(month=1, day=1, **zero) else 0)
            y = -(-y // n) * n
            while y <= t1.year and len(out) <= _MAX_TICKS:
                d = t0.replace(year=y, month=1, day=1, **zero)
                if d > t1:
                    break
                out.append(d)
                y += n
        elif unit == "month":
            m = t0.year * 12 + t0.month - 1
            if t0 > t0.replace(day=1, **zero):
                m += 1
            m = -(-m // n) * n
            while len(out) <= _MAX_TICKS:
                d = t0.replace(year=m // 12, month=m % 12 + 1, day=1, **zero)
                if d > t1:
                    break
                out.append(d)
                m += n
        else:
            if unit == "day":
                d = t0.replace(**zero)
                if d < t0:
                    d += _dt.timedelta(days=1)
                step = _dt.timedelta(days=n)
            elif unit == "hour":
                d = t0.replace(minute=0, second=0, microsecond=0)
                if d < t0:
                    d += _dt.timedelta(hours=1)
                while d.hour % n:
                    d += _dt.timedelta(hours=1)
                step = _dt.timedelta(hours=n)
            else:
                d = t0.replace(second=0, microsecond=0)
                if d < t0:
                    d += _dt.timedelta(minutes=1)
                while d.minute % n:
                    d += _dt.timedelta(minutes=1)
                step = _dt.timedelta(minutes=n)
            while d <= t1 and len(out) <= _MAX_TICKS:
                out.append(d)
                d += step
        return [self.back(d) for d in out]

    def default_format(self, unit, vals):
        if self.kind in (None, "num"):
            return None
        if self.kind == "clock" or unit in ("minute", "hour"):
            return "{hour}:{minute:02d}"
        ds = [self.at(f) for f in vals]
        if unit == "day":
            return "{month}/{day}"
        if unit == "month":
            return "{month}月" if len({d.year for d in ds}) <= 1 else "{year}年{month}月"
        return "{year}年"

    def fmt(self, f, fmt):
        if fmt is None:
            return str(_clean(f))
        return fmt.format(**self.fields(f))


# --- 図 ---

class Chart:
    """chart() が返す図。系列と注記を足してから build() で Object にする。

    要素（at はこの図の動画の先頭を 0 とした秒。x は軸と同じ種類）:
      series(data, kind='line', *, at=0, dur=2.0, color='fg', down=None, width=None,
             tag=None, glow=False, values=None)
      rule(y, label=None, *, at=0, dur=0.6, color='muted', dash=True)
      mark(x, label=None, *, at=None, dur=0.5, color='line', text_color='fg', dash=False)
      span(a, b, label=None, *, at=None, dur=0.5, color='line')
      zoom(y, *, at, dur=1.0)
      build(duration=None)   動画 Object（obj.figure: x・y・t_at・plot・size・duration・mode）
      t_at(x)                最初の系列の線の先が x に着く秒
    どれも self を返す（t_at・build を除く）。
    """

    def __init__(self, *, x=None, y=None, mode="main", x_ticks=None, y_ticks=None,
                 x_format=None, y_format=None, size=None, padding=40, label_size=36,
                 tick_size=32, opacity=None, drift=None, font=None, weight=None, colors=None):
        fn = self._fn = "chart"
        if size is None:
            proj = current_project()
            size = (proj.width, proj.height) if proj else (1920, 1080)
        self._W, self._H = _resolve_size(fn, size)
        _require_number(fn, "padding", padding, 0, None)
        self._pad = int(round(padding))
        if 4 * self._pad >= min(self._W, self._H):
            raise ValueError(f"{fn}: padding が size に対して大きすぎます: {padding!r}")
        self._mode = _require_choice(fn, "mode", mode, _MODES)
        _require_number(fn, "label_size", label_size, 8, 400)
        _require_number(fn, "tick_size", tick_size, 8, 400)
        self._label_size = fk.norm(float(label_size))
        self._tick_size = fk.norm(float(tick_size))
        if opacity is None:
            opacity = _BG_OPACITY if self._mode == "background" else 1.0
        _require_number(fn, "opacity", opacity, 0, 1)
        if opacity <= 0:
            raise ValueError(f"{fn}: opacity は 0 より大きくしてください: {opacity!r}")
        self._opacity = float(opacity)
        if drift is None:
            drift = _BG_DRIFT if self._mode == "background" else 0.0
        _require_number(fn, "drift", drift, 0, 2000)
        if drift and self._mode != "background":
            raise ValueError(f"{fn}: drift は mode='background' のときだけ使えます")
        self._drift = float(drift)
        self._font = font
        self._weight = weight
        self._pal = fk.palette(fn, colors)
        self._ax = _XAxis(fn)
        # x の範囲
        self._xr = None
        if x is not None:
            if not isinstance(x, (list, tuple)) or len(x) != 2:
                raise ValueError(f"{fn}: x は (左端, 右端) で指定してください: {x!r}")
            a = self._ax.conv("x の左端", x[0])
            b = self._ax.conv("x の右端", x[1])
            if not b > a:
                raise ValueError(f"{fn}: x の右端は左端より後にしてください: {x!r}")
            self._xr = (a, b)
        self._yr = self._check_yrange(fn, "y", y) if y is not None else None
        # 目盛りの指定
        self._x_ticks_req = x_ticks
        if y_ticks is not None and not isinstance(y_ticks, (list, tuple)):
            _require_number(fn, "y_ticks", y_ticks, 0, None)
            if y_ticks <= 0:
                raise ValueError(f"{fn}: y_ticks の間隔は 0 より大きくしてください: {y_ticks!r}")
        elif isinstance(y_ticks, (list, tuple)):
            if len(y_ticks) > _MAX_TICKS:
                raise ValueError(f"{fn}: y_ticks が多すぎます（上限 {_MAX_TICKS}）")
            for k, v in enumerate(y_ticks):
                _require_number(fn, f"y_ticks[{k}]", v)
        self._y_ticks_req = y_ticks
        if x_format is not None and not isinstance(x_format, str):
            raise TypeError(f"{fn}: x_format は書式の文字列で指定してください: {x_format!r}")
        self._x_format = x_format
        if y_format is not None:
            _check_format(fn, "y_format", y_format, {"v": 1.5})
        self._y_format = y_format
        self._series = []
        self._items = []           # rule / mark / span / zoom
        self._sprites = {}

    # --- 検証 ---

    @staticmethod
    def _check_yrange(fn, name, y):
        if not isinstance(y, (list, tuple)) or len(y) != 2:
            raise ValueError(f"{fn}: {name} は (下端, 上端) で指定してください: {y!r}")
        _require_number(fn, f"{name} の下端", y[0])
        _require_number(fn, f"{name} の上端", y[1])
        if not y[1] > y[0]:
            raise ValueError(f"{fn}: {name} の上端は下端より大きくしてください: {y!r}")
        return (float(y[0]), float(y[1]))

    def _color(self, fn, value):
        return tuple(fk.color(fn, value, self._pal))

    def _yval(self, fn, name, v, allow_none):
        if v is None:
            if allow_none:
                return None
            raise ValueError(f"{fn}: {name} は数値で指定してください（None は不可）")
        if not _is_real(v):
            raise ValueError(f"{fn}: {name} は数値で指定してください: {v!r}")
        f = float(v)
        if math.isnan(f) and allow_none:
            return None
        if not math.isfinite(f):
            raise ValueError(f"{fn}: {name} に NaN/Infinity は使えません: {v!r}")
        return f

    def _add(self, item):
        if len(self._items) >= _MAX_ITEMS:
            raise ValueError(f"{self._fn}: 要素が多すぎます（上限 {_MAX_ITEMS}）")
        self._items.append(item)
        return self

    # --- 要素 ---

    def series(self, data, kind="line", *, at=0.0, dur=None, color="fg", down=None, width=None,
               tag=None, glow=False, values=None):
        """値の列を描く。

        data: kind が line / step / area / bar なら [(x, y), …]、candle なら
          [(x, 始値, 高値, 安値, 終値), …]。x は増える順。y が None（か NaN）の所は欠けた区間で、
          線をつながない（step は None の x まで前の値を保つ）。bar の None は棒を描かない。
        kind: 'line'（折れ線）/ 'step'（階段。約定のように値が次の点で飛ぶ）/ 'area'（線の下を
          上から下へ薄れる面で塗る）/ 'candle'（ローソク足）/ 'bar'（棒。0 から立ち上がる）。
        at / dur: 描き始める秒と、左端から右端まで描く秒。線の先は x に比例して進む（t_at(x)）。
          candle / bar は線の先が通った所から 1 本ずつ立ち上がる。
        color: 線・面・陽線・正の棒の色。down: 陰線・負の棒の色（candle / bar のとき。既定は灰）。
        width: 線の太さ px（line / step / area。既定 4、area は 3）。
        tag: 線の先に付いてくる今の値の札。書式の文字列（'{v:.1f}万円'。{v} が値）か True
          （y_format か、値と同じけたの数）。
        glow: 線の先に控えめな光を付ける。
        values: 点ごとの値の札（点の少ない line / step / area と bar 向け）。書式の文字列か True。
          線の先が通ると現れる（棒は立ち上がりと一緒に数えあがる）。重なる札は描かない。
        """
        fn = "chart.series"
        if len(self._series) >= _MAX_SERIES:
            raise ValueError(f"{fn}: 系列が多すぎます（上限 {_MAX_SERIES}）")
        kind = _require_choice(fn, "kind", kind, _KINDS)
        rows = self._parse(fn, data, kind)
        if down is not None and kind not in ("candle", "bar"):
            raise ValueError(f"{fn}: down は kind='candle' か 'bar' のときだけ使えます")
        if width is not None:
            if kind not in _CONTINUOUS:
                raise ValueError(f"{fn}: width は kind が line / step / area のときだけ使えます")
            _require_number(fn, "width", width, 0.5, 60)
        if not isinstance(glow, bool):
            raise TypeError(f"{fn}: glow は True / False で指定してください: {glow!r}")
        if glow and kind not in _CONTINUOUS:
            raise ValueError(f"{fn}: glow は kind が line / step / area のときだけ使えます")
        tag = self._value_format(fn, "tag", tag)
        values = self._value_format(fn, "values", values)
        if kind == "candle" and values is not None:
            raise ValueError(f"{fn}: values は kind='candle' では使えません（tag を使う）")
        s = {"kind": kind, "rows": rows, "at": _check_at(fn, at),
             "dur": _check_dur(fn, dur, "series"), "color": self._color(fn, color),
             "down": self._color(fn, down if down is not None else _DOWN),
             "width": None if width is None else float(width), "tag": tag, "glow": glow,
             "values": values}
        self._series.append(s)
        return self

    def _value_format(self, fn, name, v):
        if v is None or v is False:
            return None
        if v is True:
            return True
        return _check_format(fn, name, v, {"v": 1.5})

    def _parse(self, fn, data, kind):
        try:
            rows = list(data)
        except TypeError:
            raise TypeError(f"{fn}: data は [(x, y), …] の列で指定してください: {data!r}") from None
        if not rows:
            raise ValueError(f"{fn}: data が空です")
        if len(rows) > _MAX_POINTS:
            raise ValueError(f"{fn}: data が多すぎます（{len(rows)} 点。上限 {_MAX_POINTS}）")
        n = 5 if kind == "candle" else 2
        form = "(x, 始値, 高値, 安値, 終値)" if kind == "candle" else "(x, y)"
        out = []
        prev = None
        for k, r in enumerate(rows):
            try:
                r = list(r)
            except TypeError:
                r = None
            if r is None or len(r) != n:
                raise ValueError(f"{fn}: data[{k}] は {form} で指定してください: {rows[k]!r}")
            x = self._ax.conv(f"data[{k}] の x", r[0])
            if prev is not None and not x > prev:
                raise ValueError(f"{fn}: data の x は増える順に並べてください（data[{k}]）: {r[0]!r}")
            prev = x
            if kind == "candle":
                o, h, lo, c = (self._yval(fn, f"data[{k}] の{nm}", v, False)
                               for nm, v in zip(("始値", "高値", "安値", "終値"), r[1:]))
                if not (lo <= min(o, c) and max(o, c) <= h):
                    raise ValueError(
                        f"{fn}: data[{k}] は 安値 ≤ 始値・終値 ≤ 高値 にしてください: {rows[k]!r}")
                out.append((x, o, h, lo, c))
            else:
                out.append((x, self._yval(fn, f"data[{k}] の y", r[1], True)))
        if kind != "candle" and all(r[1] is None for r in out):
            raise ValueError(f"{fn}: data に値が1つもありません（全部 None）")
        return out

    def rule(self, y, label=None, *, at=0.0, dur=None, color="muted", dash=True):
        """水平の基準線（公開価格・ストップ高など）。

        label: 線の右端の上の札（枠の上端に近ければ下）。字の色は color。
        at / dur: 左から右へ引く秒。dash: 破線にする。
        """
        fn = "chart.rule"
        y = self._yval(fn, "y", y, False)
        if not isinstance(dash, bool):
            raise TypeError(f"{fn}: dash は True / False で指定してください: {dash!r}")
        return self._add({"kind": "rule", "y": y, "label": _check_text(fn, "label", label),
                          "at": _check_at(fn, at), "dur": _check_dur(fn, dur, "rule"),
                          "color": self._color(fn, color), "dash": dash})

    def mark(self, x, label=None, *, at=None, dur=None, color="line", text_color="fg",
             dash=False):
        """出来事の印（x の位置の縦の線と、図の上の札）。

        at: 現れる秒。None（既定）は最初の系列の線の先が x に着く秒（系列が無ければ 0）。
        線は下から上へ伸び、札は伸び終わりに現れる。札は x の順に低い段から置き、
        ほかの札・印の線と重なる所は上の段へずらす。
        """
        fn = "chart.mark"
        return self._add({"kind": "mark", "x": self._ax.conv("x", x),
                          "label": _check_text(fn, "label", label),
                          "at": _check_at(fn, at, True), "dur": _check_dur(fn, dur, "mark"),
                          "color": self._color(fn, color),
                          "text_color": self._color(fn, text_color),
                          "dash": self._check_bool(fn, "dash", dash)})

    def span(self, a, b, label=None, *, at=None, dur=None, color="line"):
        """区間の帯（a から b まで。売買が止まった区間など）。薄い塗りと斜線。

        label: 帯の中央の上の札（印の札と同じ段に置く）。
        at: 現れる秒。None（既定）は最初の系列の線の先が a に着く秒（系列が無ければ 0）。
        """
        fn = "chart.span"
        fa = self._ax.conv("a", a)
        fb = self._ax.conv("b", b)
        if not fb > fa:
            raise ValueError(f"{fn}: b は a より後にしてください: {a!r}, {b!r}")
        return self._add({"kind": "span", "a": fa, "b": fb,
                          "label": _check_text(fn, "label", label),
                          "at": _check_at(fn, at, True), "dur": _check_dur(fn, dur, "span"),
                          "color": self._color(fn, color)})

    def zoom(self, y, *, at, dur=None):
        """縦の範囲を y=(下端, 上端) へなめらかに変える（寄る・引く）。None で最初の範囲へ戻す。

        目盛りは前後の範囲の目盛りを入れ替えるように薄れて現れる。ほかの zoom と時間が
        重なると ValueError。
        """
        fn = "chart.zoom"
        if y is not None:
            y = self._check_yrange(fn, "y", y)
        return self._add({"kind": "zoom", "y": y, "at": _check_at(fn, at),
                          "dur": _check_dur(fn, dur, "zoom")})

    @staticmethod
    def _check_bool(fn, name, v):
        if not isinstance(v, bool):
            raise TypeError(f"{fn}: {name} は True / False で指定してください: {v!r}")
        return v

    # --- 時刻 ---

    def t_at(self, x):
        """最初の系列の線の先が x に着く秒（x は軸と同じ種類）"""
        if not self._series:
            raise ValueError(f"{self._fn}.t_at: 系列（series）がまだありません")
        return self._t_of(self._series[0], self._ax.conv("x", x))

    @staticmethod
    def _t_of(s, f):
        x0, x1 = s["rows"][0][0], s["rows"][-1][0]
        q = 0.0 if x1 <= x0 else _clamp01((f - x0) / (x1 - x0))
        return s["at"] + s["dur"] * q

    def _at_of(self, it):
        if it.get("at") is not None:
            return it["at"]
        if not self._series:
            return 0.0
        f = it["x"] if it["kind"] == "mark" else it["a"]
        return self._t_of(self._series[0], f)

    def _end_time(self):
        ends = [s["at"] + s["dur"] for s in self._series]
        ends += [self._at_of(it) + it["dur"] for it in self._items]
        return max(ends or [0.0])

    # --- 文字 ---

    def _text(self, s, size, rgba, pill=None):
        k = (s, size, tuple(rgba), None if pill is None else tuple(pill))
        sp = self._sprites.get(k)
        if sp is None:
            sp = _make_text(self._fn, s, size, rgba, pill, self._font, self._weight)
            self._sprites[k] = sp
        return sp

    # --- 範囲 ---

    def _slot(self, s):
        xs = [r[0] for r in s["rows"]]
        if len(xs) < 2:
            return self._ax.slot()
        return min(b - a for a, b in zip(xs, xs[1:]))

    def _x_range(self):
        fn = self._fn
        lo, hi = math.inf, -math.inf
        for s in self._series:
            if s["kind"] in ("candle", "bar"):
                pad = self._slot(s) / 2.0
            elif sum(1 for r in s["rows"] if r[1] is not None) <= _DOTS_MAX:
                pad = self._slot(s) * _FEW_PAD      # 点の少ない線は端の点と札を枠から離す
            else:
                pad = 0.0
            lo = min(lo, s["rows"][0][0] - pad)
            hi = max(hi, s["rows"][-1][0] + pad)
        for it in self._items:
            if it["kind"] == "mark":
                lo, hi = min(lo, it["x"]), max(hi, it["x"])
            elif it["kind"] == "span":
                lo, hi = min(lo, it["a"]), max(hi, it["b"])
        if self._xr is not None:
            a, b = self._xr
            eps = 1e-9 * max(1.0, abs(b - a))
            if lo < a - eps or hi > b + eps:
                raise ValueError(f"{fn}: 系列・印・帯の x が x の範囲の外にあります（x={self._x_key()}）")
            return a, b
        if not math.isfinite(lo):
            raise ValueError(f"{fn}: 系列（series）か x の範囲が要ります")
        if hi <= lo:
            d = self._ax.slot()
            lo, hi = lo - d, hi + d
        return lo, hi

    def _x_key(self):
        return [self._ax.key(v) for v in self._xr] if self._xr else None

    def _data_y(self):
        lo, hi = math.inf, -math.inf
        bars = False
        for s in self._series:
            if s["kind"] == "candle":
                lo = min(lo, min(r[3] for r in s["rows"]))
                hi = max(hi, max(r[2] for r in s["rows"]))
            else:
                vals = [r[1] for r in s["rows"] if r[1] is not None]
                lo, hi = min(lo, min(vals)), max(hi, max(vals))
                if s["kind"] == "bar":
                    bars = True
        for it in self._items:
            if it["kind"] == "rule":
                lo, hi = min(lo, it["y"]), max(hi, it["y"])
        return lo, hi, bars

    def _y_range(self, plot_h, tick_h):
        """最初の縦の範囲（きりのよい値に広げる）"""
        if self._yr is not None:
            return self._yr
        lo, hi, bars = self._data_y()
        if not math.isfinite(lo):
            return (0.0, 1.0)
        if bars:
            lo, hi = min(lo, 0.0), max(hi, 0.0)
        if hi - lo <= 0:
            d = abs(hi) * 0.1 or 1.0
            lo, hi = lo - d, hi + d
        pad = 0.08 * (hi - lo)
        lo2 = lo if (bars and lo == 0.0) else lo - pad
        hi2 = hi if (bars and hi == 0.0) else hi + pad
        step = _y_step(lo2, hi2, plot_h, tick_h)
        lo3 = math.floor(lo2 / step + 1e-9) * step
        hi3 = math.ceil(hi2 / step - 1e-9) * step
        if bars and lo == 0.0:
            lo3 = 0.0
        if bars and hi == 0.0:
            hi3 = 0.0
        return (round(lo3, 10), round(hi3, 10))

    # --- 目盛り ---

    def _y_ticks(self, lo, hi, plot_h, tick_h):
        req = self._y_ticks_req
        if isinstance(req, (list, tuple)):
            return sorted(float(v) for v in req if lo - 1e-9 <= v <= hi + 1e-9)
        step = float(req) if req is not None else _y_step(lo, hi, plot_h, tick_h)
        k0 = math.ceil(lo / step - 1e-9)
        k1 = math.floor(hi / step + 1e-9)
        if k1 - k0 + 1 > _MAX_TICKS:
            raise ValueError(f"{self._fn}: 縦の目盛りが {k1 - k0 + 1} 個になります"
                             f"（上限 {_MAX_TICKS}。y_ticks を広げる）")
        return [round(k * step, 10) for k in range(k0, k1 + 1)]

    def _y_text(self, v, step_dec):
        if self._y_format is not None:
            return self._y_format.format(v=_clean(v))
        return f"{_clean(v):,.{step_dec}f}" if step_dec else f"{int(round(v)):,}"

    def _x_ticks(self, lo, hi, plot_w, x_of):
        """[(数, 字), …]。字が重ならない間隔を選ぶ"""
        fn = self._fn
        ax = self._ax
        req = self._x_ticks_req
        ts = self._tick_size
        muted = self._pal["muted"]

        def fits(cands):
            sps = [self._text(s, ts, muted) for _f, s in cands]
            for k in range(1, len(cands)):
                gap = x_of(cands[k][0]) - x_of(cands[k - 1][0])
                if gap < (sps[k].w + sps[k - 1].w) / 2.0 + _BOX_GAP:
                    return False
            return True

        if isinstance(req, (list, tuple)) and not _is_unit(req):
            if len(req) > _MAX_TICKS:
                raise ValueError(f"{fn}: x_ticks が多すぎます（上限 {_MAX_TICKS}）")
            out = []
            for k, t in enumerate(req):
                if isinstance(t, (list, tuple)) and len(t) == 2 and isinstance(t[1], str):
                    out.append((ax.conv(f"x_ticks[{k}]", t[0]),
                                _check_text(fn, f"x_ticks[{k}] の字", t[1])))
                else:
                    f = ax.conv(f"x_ticks[{k}]", t)
                    out.append((f, None))
            fmt = self._x_format or ax.default_format("minute" if ax.kind == "clock" else "day",
                                                      [f for f, _s in out])
            if fmt is not None:
                _check_format(fn, "x_format", fmt, ax.sample_fields())
            out = [(f, s if s is not None else ax.fmt(f, fmt)) for f, s in out]
            out = sorted(p for p in out if lo - 1e-9 <= p[0] <= hi + 1e-9)
            if out and not fits(out):
                warnings.warn(f"{fn}: x_ticks の字が重なります（間引くか字を短くする）", stacklevel=4)
            return out
        if self._x_format is not None:
            _check_format(fn, "x_format", self._x_format, ax.sample_fields())
        bar_xs = None
        if req is None and self._series and all(s["kind"] == "bar" for s in self._series):
            bar_xs = sorted({r[0] for s in self._series for r in s["rows"]})
        if ax.kind in (None, "num"):
            if bar_xs is not None:
                cands_list = [bar_xs[::k] for k in range(1, len(bar_xs) + 1)]
            else:
                if req is not None:
                    _require_number(fn, "x_ticks", req, 0, None)
                    if req <= 0:
                        raise ValueError(f"{fn}: x_ticks の間隔は 0 より大きくしてください: {req!r}")
                    steps = [float(req)]
                else:
                    e = math.floor(math.log10(hi - lo))
                    steps = list(_nice_steps(e - 3, e + 1))
                cands_list = []
                for step in steps:
                    k0, k1 = math.ceil(lo / step - 1e-9), math.floor(hi / step + 1e-9)
                    if k1 - k0 + 1 > _MAX_TICKS:
                        continue
                    cands_list.append([round(k * step, 10) for k in range(k0, k1 + 1)])
            fmt = self._x_format
            limit = 16 if bar_xs is not None else _AUTO_TICKS_MAX
            for vals in cands_list:
                if req is None and len(vals) > limit:
                    continue
                cands = [(v, ax.fmt(v, fmt)) for v in vals]
                if req is not None or fits(cands):
                    return cands
            return []
        # 日時・時刻の軸
        if req is not None:
            if isinstance(req, str):
                unit, n = req, 1
            elif _is_unit(req):
                unit, n = req
            else:
                raise TypeError(
                    f"{fn}: 日時・時刻の軸の x_ticks は単位（{', '.join(_TIME_UNITS)}）か "
                    f"(単位, 数) か値のリストで指定してください: {req!r}")
            _require_choice(fn, "x_ticks の単位", unit, _TIME_UNITS)
            if isinstance(n, bool) or not isinstance(n, int) or n < 1:
                raise ValueError(f"{fn}: x_ticks の数は 1 以上の整数で指定してください: {req!r}")
            steps = [(unit, n)]
        elif bar_xs is not None:
            steps = None
        else:
            steps = [st for st in _TIME_STEPS if (hi - lo) / (_UNIT_SEC[st[0]] * st[1]) <= _MAX_TICKS]
        if steps is None:
            for k in range(1, len(bar_xs) + 1):
                vals = bar_xs[::k]
                if len(vals) > 16:
                    continue
                fmt = self._x_format or ax.default_format("day", vals)
                cands = [(v, ax.fmt(v, fmt)) for v in vals]
                if fits(cands):
                    return cands
            return []
        for unit, n in steps:
            vals = ax.time_ticks(lo, hi, unit, n)
            if not vals or len(vals) > _MAX_TICKS or (req is None and len(vals) > _AUTO_TICKS_MAX):
                continue
            fmt = self._x_format or ax.default_format(unit, vals)
            cands = [(v, ax.fmt(v, fmt)) for v in vals]
            if req is not None or fits(cands):
                return cands
        return []

    # --- 配置 ---

    def _tag_text(self, s, v):
        fmt = s["tag"]
        return _value_text(fmt, self._y_format, s["dec"], v)

    def _resolve(self, duration):
        """配置して、描く場面（_Renderer に渡すもの）を作る"""
        fn = self._fn
        if not self._series and not self._items:
            raise ValueError(f"{fn}: 系列も注記もありません（series を足す）")
        bg = self._mode == "background"
        W, H, pad = self._W, self._H, self._pad
        xlo, xhi = self._x_range()
        pal = self._pal
        ts, ls = self._tick_size, self._label_size
        # 値のけた（tag・values の既定の書式）
        for s in self._series:
            vals = ([r[4] for r in s["rows"]] if s["kind"] == "candle"
                    else [r[1] for r in s["rows"] if r[1] is not None])
            s["dec"] = max([_decimals(abs(v)) for v in vals] or [0])
            s["dec"] = min(s["dec"], 3)
        # 時刻を決める
        items = []
        for it in self._items:
            e = dict(it)
            e["at"] = self._at_of(it)
            items.append(e)
        zooms = sorted([e for e in items if e["kind"] == "zoom"], key=lambda e: e["at"])
        for a, b in zip(zooms, zooms[1:]):
            if b["at"] < a["at"] + a["dur"] - 1e-9:
                raise ValueError(f"{fn}: zoom の時間が重なっています（{a['at']:g} 秒と {b['at']:g} 秒）")

        fps_frac = _fps_fraction(_resolve_fps(fn, None))
        timing = {"fps": float(fps_frac), "n_frames": fk.n_frames_for(duration, fps_frac)}
        if bg:
            out = self._resolve_bg(duration, xlo, xhi, items, zooms)
            out.update(timing)
            return out

        # --- 縦の寸法（目盛りの字の高さ）---
        tick_h = self._text("0", ts, pal["muted"]).h
        # 仮の枠の高さで縦の範囲を決め、目盛りの字の幅から左の余白を決める（2回回して落ち着かせる）
        plot_h = H - 2 * pad - (tick_h + _TICK_LEN + _TICK_GAP) - _BAND_BOTTOM
        top_band = _BAND_BOTTOM
        left = 0

        def y_texts(plot_h):
            base = self._y_range(plot_h, tick_h)
            views = [base] + [z["y"] or base for z in zooms]
            ytexts = {}
            for v in views:
                vals = self._y_ticks(v[0], v[1], plot_h, tick_h)
                dec = max([_decimals(abs(t)) for t in vals] or [0])
                ytexts[v] = [(t, self._y_text(t, dec)) for t in vals]
            ysps = [self._text(s, ts, pal["muted"]) for v in views for _t, s in ytexts[v]]
            need = (max(sp.w for sp in ysps) + _YLABEL_GAP) if ysps else 0
            return base, ytexts, need

        # 左の余白は増やす向きにだけ動かす（行ったり来たりせず落ち着く）
        for _ in range(8):
            base, ytexts, need = y_texts(plot_h)
            left = max(left, need)
            # 右の余白（今の値の札）
            tag_box = self._tag_box()
            right = (tag_box[0] + _TAG_GAP + 4) if tag_box else 0
            px0, px1 = pad + left, W - pad - right
            if px1 - px0 < 40:
                raise ValueError(f"{fn}: 図の枠の幅が足りません（size を広げる・字を短くする）")

            def x_of(f, px0=px0, px1=px1):
                return px0 + (f - xlo) / (xhi - xlo) * (px1 - px0)
            # 上の札の段
            band = self._place_band(items, x_of, px0)
            top_band = band["height"]
            plot_h2 = H - 2 * pad - top_band - (tick_h + _TICK_LEN + _TICK_GAP)
            if plot_h2 < 40:
                raise ValueError(f"{fn}: 図が縦に収まりません（size を大きくする・札を減らす）")
            if abs(plot_h2 - plot_h) < 0.5 and need <= left:
                break
            plot_h = plot_h2
        else:
            # 落ち着かなかった: 目盛りだけは最後の枠の高さで決め直す（左の余白は足りる分を残す）
            base, ytexts, _need = y_texts(plot_h)
        py0 = pad + top_band
        py1 = py0 + int(plot_h)
        px0, px1 = int(px0), int(px1)
        plot = (px0, py0, px1, py1)

        # --- 横の目盛り ---
        xt = self._x_ticks(xlo, xhi, px1 - px0, x_of)
        xticks = []
        for f, s in xt:
            sp = self._text(s, ts, pal["muted"])
            x = x_of(f)
            lx = int(round(min(max(x - sp.w / 2.0, 0), W - sp.w)))
            xticks.append((x, sp, (lx, py1 + _TICK_LEN + _TICK_GAP), s))

        # --- 縦の目盛り（範囲ごと）---
        yticks = {}
        for v, lst in ytexts.items():
            yticks[_vkey(v)] = [(t, self._text(s, ts, pal["muted"]), s) for t, s in lst]

        # --- 系列 ---
        series = [self._scene_series(s, x_of) for s in self._series]
        # --- 注記 ---
        rules, marks, spans = [], [], []
        for e in items:
            if e["kind"] == "rule":
                sp = (self._text(e["label"], ts, e["color"]) if e["label"] is not None else None)
                rules.append({"y": e["y"], "sp": sp, "at": e["at"], "dur": e["dur"],
                              "color": e["color"], "dash": e["dash"]})
            elif e["kind"] == "mark":
                g = band["geo"].get(id(e))
                marks.append({"x": x_of(e["x"]), "sp": g["sp"] if g else None,
                              "pos": g["pos"] if g else None,
                              "top": (g["pos"][1] + g["sp"].h + _MARK_STEM_GAP) if g else py0,
                              "at": e["at"], "dur": e["dur"], "color": e["color"],
                              "dash": e["dash"]})
            elif e["kind"] == "span":
                g = band["geo"].get(id(e))
                spans.append({"xa": x_of(e["a"]), "xb": x_of(e["b"]),
                              "sp": g["sp"] if g else None, "pos": g["pos"] if g else None,
                              "at": e["at"], "dur": e["dur"], "color": e["color"]})
        # 印の札の y（段 → キャンバス）
        for m in marks + spans:
            if m["pos"] is not None:
                row_y = m["pos"]
                m["pos"] = (row_y[0], py0 - row_y[1])
                if "top" in m:
                    m["top"] = m["pos"][1] + m["sp"].h + _MARK_STEM_GAP
        zl = [(z["at"], z["dur"], z["y"]) for z in zooms]
        return {
            "fn": fn, "mode": "main", "size": (W, H), "render": (W, H), "k": 1.0,
            "plot": plot, "xlo": xlo, "xhi": xhi, "base": base, "zooms": zl,
            "pal": dict(pal), "xticks": xticks, "yticks": yticks,
            "series": series, "rules": rules, "marks": marks, "spans": spans,
            "opacity": self._opacity, "drift": 0.0, "shift_max": 0,
            "tag_box": self._tag_box(), "duration": duration,
            "tick_size": ts, "label_size": ls, "font": self._font, "weight": self._weight,
            "y_format": self._y_format, **timing,
        }

    def _resolve_bg(self, duration, xlo, xhi, items, zooms):
        """背景の型の場面（字なし・半分の解像度・横に長いキャンバス）"""
        W, H, pad = self._W, self._H, self._pad
        k = _BG_SCALE
        D = int(math.ceil(self._drift * duration))
        Ww = W + D
        Ww += Ww % 2
        plot_full = (pad, pad, Ww - pad, H - pad)
        rw, rh = int(math.ceil(Ww * k)), int(math.ceil(H * k))
        px0, py0, px1, py1 = (v * k for v in plot_full)
        plot = (px0, py0, px1, py1)
        base = self._y_range((py1 - py0) / k, 40)

        def x_of(f):
            return px0 + (f - xlo) / (xhi - xlo) * (px1 - px0)
        views = [base] + [z["y"] or base for z in zooms]
        yticks = {}
        for v in views:
            vals = self._y_ticks(v[0], v[1], (py1 - py0) / k, 40)
            yticks[_vkey(v)] = [(t, None, None) for t in vals]
        series = [self._scene_series(s, x_of) for s in self._series]
        rules, marks, spans = [], [], []
        for e in items:
            if e["kind"] == "rule":
                rules.append({"y": e["y"], "sp": None, "at": e["at"], "dur": e["dur"],
                              "color": e["color"], "dash": e["dash"]})
            elif e["kind"] == "mark":
                marks.append({"x": x_of(e["x"]), "sp": None, "pos": None, "top": py0,
                              "at": e["at"], "dur": e["dur"], "color": e["color"],
                              "dash": e["dash"]})
            elif e["kind"] == "span":
                spans.append({"xa": x_of(e["a"]), "xb": x_of(e["b"]), "sp": None, "pos": None,
                              "at": e["at"], "dur": e["dur"], "color": e["color"]})
        return {
            "fn": self._fn, "mode": "background", "size": (W, H), "render": (rw, rh), "k": k,
            "wide": Ww, "plot": plot, "xlo": xlo, "xhi": xhi, "base": base,
            "zooms": [(z["at"], z["dur"], z["y"]) for z in zooms],
            "pal": dict(self._pal), "xticks": [], "yticks": yticks,
            "series": series, "rules": rules, "marks": marks, "spans": spans,
            "opacity": self._opacity, "drift": self._drift, "shift_max": Ww - W,
            "tag_box": None, "duration": duration, "tick_size": self._tick_size,
            "label_size": self._label_size, "font": self._font, "weight": self._weight,
            "y_format": self._y_format,
        }

    def _tag_box(self):
        """今の値の札の最大の寸法 (w, h)（札のある系列が無ければ None）"""
        if self._mode == "background":
            return None
        w = h = 0
        for s in self._series:
            if s["tag"] is None:
                continue
            if s["kind"] == "candle":
                vals = [r[4] for r in s["rows"]] + [min(r[3] for r in s["rows"]),
                                                     max(r[2] for r in s["rows"])]
            else:
                vals = [r[1] for r in s["rows"] if r[1] is not None]
            vals = [min(vals), max(vals), vals[0], vals[-1]]
            if "dec" not in s:
                s["dec"] = min(max(_decimals(abs(v)) for v in vals), 3)
            for v in vals:
                txt = _value_text(s["tag"], self._y_format, s["dec"], v)
                # 桁が増える途中の値（9.9 → 10.0 など）も入るよう、数字を 8 に置き換えて測る
                wide = re.sub(r"\d", "8", txt)
                sp = self._text(wide, self._label_size, s["color"], pill=_pill_text(s["color"]))
                w, h = max(w, sp.w), max(h, sp.h)
        return (w, h) if w else None

    def _place_band(self, items, x_of, px0):
        """印と帯の札を段に置く: {'height', 'geo': {id(要素): {'sp', 'pos': (x, 枠の上端からの高さ)}}}"""
        W, pad = self._W, self._pad
        lab = []
        for e in items:
            if e["kind"] not in ("mark", "span") or e["label"] is None:
                continue
            col = e["text_color"] if e["kind"] == "mark" else self._pal["muted"]
            sp = self._text(e["label"], self._label_size, col)
            cx = x_of(e["x"]) if e["kind"] == "mark" else (x_of(e["a"]) + x_of(e["b"])) / 2.0
            x0 = min(max(cx - sp.w / 2.0, px0), W - pad - sp.w)
            if x0 < 0:
                raise ValueError(f"{self._fn}: 札 {e['label']!r} の幅がキャンバスに収まりません")
            x0 = int(round(x0))
            lab.append({"e": e, "sp": sp, "x0": x0, "x1": x0 + sp.w, "cx": cx,
                        "stem": e["kind"] == "mark", "row": None})
        lab.sort(key=lambda b: (b["cx"], b["e"]["at"]))
        m = _BOX_GAP / 2.0

        def covers(o, b):
            """札 o の箱が、札 b の線（印の x）に掛かるか"""
            return o is not b and b["stem"] and o["x0"] - m < b["cx"] < o["x1"] + m

        # 置く順: 札 j の箱が札 i の線に掛かるなら i が先（低い段）。ほかは x の順
        n = len(lab)
        before = [{i for i in range(n) if covers(lab[j], lab[i])} for j in range(n)]
        order, done = [], set()
        while len(order) < n:
            ready = [k for k in range(n) if k not in done and before[k] <= done]
            k = ready[0] if ready else min(k for k in range(n) if k not in done)
            done.add(k)
            order.append(lab[k])
        rows = []                  # 段ごとの [札]
        for b in order:
            for strict in (True, False):
                r = self._band_row(rows, b, covers, strict)
                if r is not None:
                    break
            if not strict:
                warnings.warn(
                    f"{self._fn}: 札 {b['e']['label']!r} の線がほかの札を横切ります"
                    f"（x の近い印を減らす・字を短くする）", stacklevel=5)
            if r == len(rows):
                rows.append([])
            rows[r].append(b)
            b["row"] = r
        if not lab:
            return {"height": _BAND_BOTTOM, "geo": {}}
        row_h = max(b["sp"].h for b in lab) + _BAND_GAP
        geo = {}
        for b in lab:
            # 枠の上端から札の上端までの高さ（_resolve で py0 から引く）
            geo[id(b["e"])] = {"sp": b["sp"],
                               "pos": (b["x0"], _BAND_BOTTOM + b["row"] * row_h + b["sp"].h)}
        return {"height": _BAND_BOTTOM + len(rows) * row_h, "geo": geo}

    @staticmethod
    def _band_row(rows, b, covers, strict):
        """札 b を置ける一番低い段（strict=False は線が札を横切るのを許す）。新しい段は len(rows)"""
        for r in range(len(rows) + 1):
            mine = rows[r] if r < len(rows) else []
            if any(b["x0"] < o["x1"] + _BOX_GAP and o["x0"] < b["x1"] + _BOX_GAP for o in mine):
                continue                    # 同じ段の札と重なる
            if strict and any(covers(o, b) for rr in range(min(r, len(rows))) for o in rows[rr]):
                continue                    # 線が下の段の札を貫く
            if strict and any(covers(b, o) for rr in range(r + 1, len(rows)) for o in rows[rr]):
                continue                    # 上の段の札の線を覆う
            return r
        return None

    def _scene_series(self, s, x_of):
        """描く系列（x は px、y はデータの値のまま。縦は範囲がコマごとに変わる）"""
        kind = s["kind"]
        rows = s["rows"]
        k = 1.0 if self._mode == "main" else _BG_SCALE
        widen = 1.0 if self._mode == "main" else _BG_WIDTH
        out = {"kind": kind, "at": s["at"], "dur": s["dur"], "color": s["color"],
               "down": s["down"], "glow": s["glow"], "x0": x_of(rows[0][0]),
               "x1": x_of(rows[-1][0])}
        main = self._mode == "main"
        out["tag"] = s["tag"] if main else None
        out["dec"] = s.get("dec", 0)
        if kind in _CONTINUOUS:
            w = s["width"] if s["width"] is not None else (_AREA_LINE_W if kind == "area" else _LINE_W)
            out["width"] = w * k * widen
            segs = []
            cur = []
            for i, r in enumerate(rows):
                if r[1] is None:
                    if cur:
                        if kind == "step":
                            cur.append((x_of(r[0]), cur[-1][1]))
                        segs.append(cur)
                    cur = []
                    continue
                if kind == "step" and cur:
                    cur.append((x_of(r[0]), cur[-1][1]))
                cur.append((x_of(r[0]), r[1]))
            if cur:
                segs.append(cur)
            out["segs"] = segs
            pts = [(x_of(r[0]), r[1]) for r in rows if r[1] is not None]
            out["points"] = pts
            out["dots"] = len(pts) <= _DOTS_MAX and kind != "step"
        else:
            slot = self._slot(s)
            px_slot = abs(x_of(rows[0][0] + slot) - x_of(rows[0][0]))
            frac = _BODY if kind == "candle" else _BAR
            out["half"] = max(0.5 * k, px_slot * frac / 2.0)
            out["items"] = [(x_of(r[0]),) + tuple(r[1:]) for r in rows]
            n = len(rows)
            rise = min(_RISE, s["dur"] * 0.5)
            out["rise"] = rise
            x0, x1 = out["x0"], out["x1"]
            out["starts"] = [s["at"] + (s["dur"] - rise) * (0.0 if x1 <= x0 else
                                                          (r[0] - x0) / (x1 - x0))
                             for r in out["items"]]
            out["n"] = n
        # 点ごとの値の札
        vals = []
        if main and s["values"] is not None:
            if kind == "bar":
                vals = [(i, it[0], it[1], "above") for i, it in enumerate(out["items"])
                        if it[1] is not None]
            else:
                pts = out["points"]
                for i, (x, y) in enumerate(pts):
                    vals.append((i, x, y, _value_side(pts, i)))
        out["values"] = s["values"] if main else None
        out["value_list"] = vals
        out["value_size"] = self._label_size if kind == "bar" else self._tick_size
        return out

    # --- 鍵 ---

    def _params(self, duration):
        ax = self._ax
        main = self._mode == "main"
        series = []
        used = set()
        for s in self._series:
            rows = []
            for r in s["rows"]:
                rows.append([ax.key(r[0])] + [None if v is None else fk.norm(v) for v in r[1:]])
            d = {"kind": s["kind"], "rows": rows, "at": fk.norm(s["at"]),
                 "dur": fk.norm(s["dur"]), "color": list(s["color"])}
            if s["kind"] in ("candle", "bar"):
                d["down"] = list(s["down"])
            if s["kind"] in _CONTINUOUS:
                d["width"] = None if s["width"] is None else fk.norm(s["width"])
                d["glow"] = s["glow"]
            if main:
                d["tag"] = s["tag"]
                d["values"] = s["values"]
                n_pts = sum(1 for r in s["rows"] if r[1] is not None)
                if s["kind"] in ("line", "area") and n_pts <= _DOTS_MAX:
                    used.add("panel")              # 点の少ない線の点の中
                if s["values"] is not None:
                    used.add("fg")                 # 値の札の字
            series.append(d)
        items = []
        for it in self._items:
            d = {}
            for k, v in it.items():
                if k in ("x", "a", "b"):
                    d[k] = ax.key(v)
                elif k in ("color", "text_color"):
                    d[k] = list(v)
                elif k in ("y", "at", "dur"):
                    d[k] = fk.norm(v)
                else:
                    d[k] = v
            if it["kind"] in ("mark", "span", "rule") and (it.get("label") is None or not main):
                d.pop("label", None)
                d.pop("text_color", None)
            items.append(d)
        used.add("dim")
        if main:
            used.update(["line", "muted"])
        p = {
            "mode": self._mode, "size": [self._W, self._H], "padding": self._pad,
            "x": self._x_key(), "y": None if self._yr is None else [fk.norm(v) for v in self._yr],
            "series": series, "items": items,
            "colors": {k: list(self._pal[k]) for k in sorted(used)},
            "opacity": fk.norm(self._opacity),
            "duration": fk.norm(duration),
        }
        req = self._y_ticks_req
        p["y_ticks"] = fk.norm(list(req)) if isinstance(req, (list, tuple)) else fk.norm(req)
        if main:
            xr = self._x_ticks_req
            if isinstance(xr, (list, tuple)) and not _is_unit(xr):
                xr = [[ax.key(ax.conv("x_ticks", t[0])), t[1]] if isinstance(t, (list, tuple))
                      else ax.key(ax.conv("x_ticks", t)) for t in xr]
            p["x_ticks"] = fk.norm(xr) if not isinstance(xr, str) else xr
            p["x_format"] = self._x_format
            p["y_format"] = self._y_format
            p["label_size"] = self._label_size
            p["tick_size"] = self._tick_size
        else:
            p["drift"] = fk.norm(self._drift)
        return p

    # --- 組み立て ---

    def build(self, duration=None):
        """動画 Object にする（frames() と同じ qtrle・alpha つきの .mov）。

        duration: 秒。None は最後の要素が描き終わる秒 + 1.0 秒。それより短いと ValueError。
        戻り値の obj.figure: x(v)・y(v)（値 → キャンバスの px。y は最初の縦の範囲）・
        t_at(x)・plot（枠の (x0, y0, x1, y1)）・size・duration・mode。
        build の後に足した要素は、この Object には入らない。
        """
        fn = "chart.build"
        end = self._end_time()
        if duration is None:
            duration = end + 1.0
        else:
            _require_number(fn, "duration", duration, 0, None)
            if duration <= 0:
                raise ValueError(f"{fn}: duration は 0 より大きくしてください: {duration!r}")
            duration = float(duration)
            if end > duration + 1e-9:
                raise ValueError(
                    f"{fn}: 要素が描き終わる秒（{end:g} 秒）が duration（{duration:g} 秒）より"
                    f"後です（duration を延ばすか at・dur を縮める）")
        scene = self._resolve(duration)
        fps = _resolve_fps(fn, None)
        fps_frac = _fps_fraction(fps)
        n_frames = scene["n_frames"]
        holder = {}

        def draw(i):
            r = holder.get("r")
            if r is None:
                r = holder["r"] = _Renderer(scene)
            img = r.frame(float(Fraction(i) / fps_frac))
            if i == n_frames - 1:
                holder.pop("r", None)      # 作業領域（層・使い回しの線）を手放す
            return img

        main = self._mode == "main"
        sprites = [self._sprites[k] for k in sorted(self._sprites, key=repr)] if main else []
        # 今の値の札・値の札は描くときに作るので、フォントは FontRef でも鍵に入れる
        fonts = list(sprites)
        if main and any(s["tag"] is not None or s["values"] is not None for s in self._series):
            fonts.append(fk.font_ref(fn, self._font, 0, self._weight))
        text = None
        if sprites:
            text = fk.text_meta([sp for sp in sprites], width=self._W, height=self._H)
        plot = scene["plot"]
        k = scene["k"]
        xlo, xhi = scene["xlo"], scene["xhi"]
        base = scene["base"]
        ax = self._ax
        first = self._series[0] if self._series else None
        t_of = self._t_of

        def x_of(v):
            f = ax.conv("v", v)
            return (plot[0] + (f - xlo) / (xhi - xlo) * (plot[2] - plot[0])) / k

        def y_of(v):
            _require_number("chart.figure.y", "v", v)
            return (plot[3] - (v - base[0]) / (base[1] - base[0]) * (plot[3] - plot[1])) / k

        def t_at(v):
            if first is None:
                raise ValueError("chart.figure.t_at: 系列がありません")
            return t_of(first, ax.conv("v", v))

        info = {"x": x_of, "y": y_of, "t_at": t_at,
                "plot": tuple(v / k for v in plot), "size": (self._W, self._H),
                "duration": duration, "mode": self._mode,
                "y_range": base}
        return fk.build(fn, kind="chart", ver=_CHART_VER, params=self._params(duration),
                        draw=draw, n_frames=n_frames, size=(self._W, self._H), fps=fps,
                        fonts=fonts, text=text, info=info)


def _is_unit(v):
    return (isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], str)
            and v[0] in _TIME_UNITS)


def _vkey(v):
    return (round(v[0], 9), round(v[1], 9))


def _y_step(lo, hi, plot_h, tick_h):
    """縦の目盛りの間隔（4〜7 本ほど。字の札の高さの 1.6 倍より詰めない）"""
    rng = hi - lo
    e = math.floor(math.log10(rng))
    min_px = tick_h * 1.6          # tick_h は縁取りと行の余白込みの字の高さ
    for step in _nice_steps(e - 2, e + 2):
        n = rng / step
        if n <= 7 and plot_h / max(n, 1e-9) >= min_px:
            return step
    return 10.0 ** (e + 2)


def _value_text(fmt, y_format, dec, v):
    """値の札の字（fmt が True なら y_format か、値のけたの数）"""
    if fmt is True:
        fmt = y_format
    if fmt is not None:
        return fmt.format(v=_clean(round(v, 9)))
    return f"{v:,.{dec}f}" if dec else f"{int(round(v)):,}"


def _pill_text(rgba):
    """下地の色に合う字の色（明るい下地には暗い字）"""
    r, g, b = rgba[:3]
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return (0x14, 0x16, 0x1A, 255) if lum > 140 else (255, 255, 255, 255)


def _make_text(fn, s, size, rgba, pill, font, weight):
    """図の字（黒の縁取りつき）か、下地つきの札（pill は字の色。下地は rgba）"""
    if pill is None:
        pad = (_HALO + int(math.ceil(float(size) * 0.15)), _HALO + 1)
        return fk.label(fn, s, size=size, font=font, weight=weight, color=tuple(rgba),
                        border=_HALO, border_color=(0, 0, 0, 255), padding=pad)
    pad = (int(round(float(size) * 0.38)), int(round(float(size) * 0.14)))
    return fk.label(fn, s, size=size, font=font, weight=weight, color=tuple(pill),
                    background=tuple(rgba), background_radius=int(round(float(size) * 0.28)),
                    padding=pad)


# --- 描画 ---

class _Renderer:
    """場面（Chart._resolve の戻り値）から t 秒のコマを描く"""

    def __init__(self, scene):
        self.s = scene
        self.np = fk.need("chart").np
        self.cv2 = fk.need("chart").cv2
        self._base = {}            # 縦の範囲の鍵 → 動かない層
        self._line = {}            # 系列の番号 → 使い回しの線の被覆率
        self._items = {}           # 系列の番号 → 描き終えたローソク足・棒の層
        self._track = None         # 今の値の札の上下のずらし（コマごと）
        self._last_sig = None
        self._last_img = None
        self._wide = None          # 背景: (内容の署名, 横に長い uint8)
        self._u8 = {}              # 下の層の鍵 → その uint8
        self._rb = {}              # 範囲 → 基準線の札の箱
        self._vk = {}              # (系列, 範囲) → 値の札を描く点の番号
        self._dirty = None
        self._last_shift = None
        rw, rh = scene["render"]
        px0, py0, px1, py1 = scene["plot"]
        m = int(math.ceil(max([s.get("width", 2.0) for s in scene["series"]] + [2.0]) / 2 + 3))
        self.clip = (max(0, int(math.floor(px0)) - m), max(0, int(math.floor(py0)) - m),
                     min(rw, int(math.ceil(px1)) + m), min(rh, int(math.ceil(py1)) + m))

    # --- 範囲と位置 ---

    def view(self, t):
        """(lo, hi), (前の範囲, 次の範囲, 進み) か None"""
        s = self.s
        cur = s["base"]
        for at, dur, y in s["zooms"]:
            target = y if y is not None else s["base"]
            if t >= at + dur:
                cur = target
                continue
            if t > at:
                u = _ease_io((t - at) / dur)
                return ((cur[0] + (target[0] - cur[0]) * u, cur[1] + (target[1] - cur[1]) * u),
                        (cur, target, u))
            break
        return cur, None

    def py(self, v, view):
        _px0, py0, _px1, py1 = self.s["plot"]
        lo, hi = view
        return py1 - (v - lo) / (hi - lo) * (py1 - py0)

    def head(self, ser, t):
        """系列の線の先: (x px, 値) か None（描き始める前・欠けた区間の中は直前の点）"""
        q = 1.0 if ser["dur"] <= 0 else _clamp01((t - ser["at"]) / ser["dur"])
        if t < ser["at"]:
            return None, q
        hx = ser["x0"] + (ser["x1"] - ser["x0"]) * q
        if ser["kind"] in ("candle", "bar"):
            last = None
            for st, it in zip(ser["starts"], ser["items"]):
                if st <= t + 1e-9 and (ser["kind"] == "candle" or it[1] is not None):
                    last = it
            if last is None:
                return None, q
            v = last[4] if ser["kind"] == "candle" else last[1]
            return (last[0], v), q
        best = None
        for seg in ser["segs"]:
            if seg[0][0] > hx + 1e-9:
                break
            if seg[-1][0] <= hx:
                best = seg[-1]
                continue
            for a, b in zip(seg, seg[1:]):
                if a[0] <= hx <= b[0]:
                    if ser["kind"] == "step" or b[0] <= a[0]:
                        v = a[1] if hx < b[0] else b[1]
                    else:
                        v = a[1] + (b[1] - a[1]) * (hx - a[0]) / (b[0] - a[0])
                    return (hx, v), q
            best = seg[-1]
        return best, q

    # --- 今の値の札の位置（全コマを先に決めてならす）---

    def _tag_track(self):
        if self._track is not None:
            return self._track
        s = self.s
        n = s.get("n_frames") or 1
        fps = s.get("fps") or 30.0
        tb = s["tag_box"]
        tags = [i for i, ser in enumerate(s["series"]) if ser["tag"] is not None]
        if tb is None or not tags:
            self._track = {}
            return self._track
        tw, th = tb
        px0, py0, px1, py1 = s["plot"]
        slot = th * _TAG_SLOT
        cands = [0.0]
        for j in range(1, 5):
            cands += [-j * slot, j * slot]
        raw = {i: [0.0] * n for i in tags}
        for f in range(n):
            t = f / fps
            view, _tr = self.view(t)
            avoid = self._fixed_boxes(t, view)
            for i in tags:
                h, _q = self.head(s["series"][i], t)
                if h is None:
                    continue
                hy = self.py(h[1], view)
                x0 = h[0] + _TAG_GAP
                # 重ならない一番近いずらし。どれも重なるなら重なりの面積が一番小さいもの
                best = None
                for dy in cands:
                    y0 = min(max(hy + dy - th / 2.0, py0), py1 - th)
                    box = (x0, y0, x0 + tw, y0 + th)
                    area = sum(_overlap_area(box, b, 4) for b in avoid)
                    if best is None or area < best[0] - 1e-9:
                        best = (area, dy, box)
                    if area <= 0:
                        break
                raw[i][f] = best[1]          # 枠への切り詰めは描くときに行う（ならさない）
                avoid.append(best[2])
        r = max(1, int(round(_TAG_SMOOTH * fps)))
        out = {}
        for i in tags:
            arr = raw[i]
            sm = []
            for f in range(n):
                lo, hi = max(0, f - r), min(n, f + r + 1)
                sm.append(sum(arr[lo:hi]) / (hi - lo))
            out[i] = sm
        self._track = out
        return out

    def _fixed_boxes(self, t, view):
        """札がよける箱: 見えている基準線の札・値の札"""
        out = []
        for ru in self.s["rules"]:
            p = _clamp01((t - ru["at"]) / ru["dur"])
            if ru["sp"] is None or p <= 0:
                continue
            b = self._rule_label_box(ru, view)
            if b is not None:
                out.append(b)
        for ser in self.s["series"]:
            for b, _a, _sp, _x, _y in self._value_boxes(ser, t, view):
                out.append(b)
        return out

    def _rule_label_box(self, ru, view):
        """基準線の札の箱（枠の外の線・置き場所の無い札は None）"""
        return self._rule_boxes(view).get(id(ru))

    def _rule_boxes(self, view):
        """範囲 view での基準線の札の箱 {id(基準線): 箱 か None}。

        札は線の右端の上（入らなければ下）。先に置いた札と重なる側は避け、どちらも重なるなら
        描かない（見えているかどうかに依らず全部の札で決めるので、現れる順で位置が変わらない）。
        """
        vk = _vkey(view)
        hit = self._rb.get(vk)
        if hit is not None:
            return hit
        px0, py0, px1, py1 = self.s["plot"]
        out = {}
        placed = []
        for ru in self.s["rules"]:
            sp = ru["sp"]
            out[id(ru)] = None
            if sp is None:
                continue
            y = self.py(ru["y"], view)
            if y < py0 - 1 or y > py1 + 1:
                continue
            x1 = px1 - _RULE_INSET
            for y0 in (y - _RULE_TEXT_GAP - sp.h, y + _RULE_TEXT_GAP):
                box = (x1 - sp.w, y0, x1, y0 + sp.h)
                if y0 < py0 - 0.5 or y0 + sp.h > py1 + 0.5:
                    continue
                if any(_overlap(box, b, 2) for b in placed):
                    continue
                placed.append(box)
                out[id(ru)] = box
                break
        if len(self._rb) > 64:
            self._rb.clear()
        self._rb[vk] = out
        return out

    def _value_boxes(self, ser, t, view):
        """見えている値の札: [(箱, 不透明度, Sprite か None, 値, 立ち上がり)]"""
        if not ser["value_list"]:
            return []
        out = []
        for i, x, v, side in self._value_keep(ser, view):
            if ser["kind"] == "bar":
                st = ser["starts"][i]
                rise = _clamp01((t - st) / ser["rise"]) if ser["rise"] > 0 else float(t >= st)
                if rise <= 0:
                    continue
                e = _ease_out(rise)
                shown = round(v * e, ser["dec"])      # 数えあがる値も値のけたで
                a = e
            else:
                tx = self._t_of_px(ser, x)
                a = _clamp01((t - tx) / _VALUE_FADE)
                if a <= 0:
                    continue
                e = None
                shown = v
            sp = self._value_sprite(ser, shown)
            box = self._value_box(ser, sp, x, v, side, view, e)
            out.append((box, a, sp, x, v))
        return out

    def _value_sprite(self, ser, v):
        sprites = ser.setdefault("_vsp", {})
        txt = _value_text(ser["values"], self.s["y_format"], ser["dec"], v)
        sp = sprites.get(txt)
        if sp is None:
            sp = _make_text(self.s["fn"], txt, ser["value_size"], self.s["pal"]["fg"], None,
                            self.s["font"], self.s["weight"])
            sprites[txt] = sp
        return sp

    def _value_box(self, ser, sp, x, v, side, view, rise=None):
        """値の札の箱（rise は棒の立ち上がりの進み。None は描き終えた位置）"""
        px0, py0, px1, py1 = self.s["plot"]
        if ser["kind"] == "bar":
            e = 1.0 if rise is None else rise
            top = self.py(max(v, 0.0) * e, view) if v >= 0 else self.py(0.0, view)
            y0 = top - _VALUE_GAP - sp.h
            x0 = x - sp.w / 2.0
        else:
            yv = self.py(v, view)
            g = _VALUE_GAP * self.s["k"]
            if side == "below" or yv - g - sp.h < py0:
                y0 = yv + g
            else:
                y0 = yv - g - sp.h
            # 斜めに通り過ぎる点は、線の無い側（上り坂なら左上・下り坂なら右上）に寄せる
            x0 = {"ul": x - sp.w + g * 0.3, "ur": x - g * 0.3}.get(side, x - sp.w / 2.0)
        x0 = int(round(min(max(x0, px0), px1 - sp.w)))
        return (x0, int(round(y0)), x0 + sp.w, int(round(y0)) + sp.h)

    def _value_keep(self, ser, view):
        """値の札を描く点（描き終えた位置で重なる札は前の札を残して外す。範囲ごとに1回）"""
        key = (id(ser), _vkey(view))
        hit = self._vk.get(key)
        if hit is not None:
            return hit
        placed = []
        keep = []
        for i, x, v, side in ser["value_list"]:
            box = self._value_box(ser, self._value_sprite(ser, v), x, v, side, view)
            if any(_overlap(box, b, 2) for b in placed):
                continue
            placed.append(box)
            keep.append((i, x, v, side))
        if len(self._vk) > 64:
            self._vk.clear()
        self._vk[key] = keep
        return keep

    @staticmethod
    def _t_of_px(ser, x):
        x0, x1 = ser["x0"], ser["x1"]
        q = 0.0 if x1 <= x0 else _clamp01((x - x0) / (x1 - x0))
        return ser["at"] + ser["dur"] * q

    # --- 1コマ ---

    def frame(self, t):
        s = self.s
        view, trans = self.view(t)
        fps = s.get("fps") or 30.0
        fi = min(max(int(round(t * fps)), 0), (s.get("n_frames") or 1) - 1)
        track = self._tag_track()
        state = [round(view[0], 9), round(view[1], 9), round(_clamp01(t / _INTRO), _SIG_DIGITS)]
        for ser in s["series"]:
            end = ser["at"] + ser["dur"] + max(_VALUE_FADE, _TAG_FADE) + 0.01
            state.append(round(min(max(t, ser["at"] - 1e-3), end), _SIG_DIGITS))
        for e in s["rules"] + s["marks"] + s["spans"]:
            state.append(round(_clamp01((t - e["at"]) / e["dur"]), _SIG_DIGITS))
        for i in sorted(track):
            state.append(round(track[i][fi], 3))
        sig = tuple(state)
        shift = 0
        if s["mode"] == "background":
            shift = min(s["shift_max"], int(math.floor(s["drift"] * t + 1e-9)))
        if sig == self._last_sig and shift == self._last_shift:
            return self._last_img
        if s["mode"] == "background":
            if self._wide is None or self._wide[0] != sig:
                dst, _key, _dirty = self._draw(t, view, trans, fi)
                self._wide = (sig, self._bg_wide(dst))
            W = s["size"][0]
            img = self._wide[1][:, shift:shift + W].copy()
        else:
            img = self._to_u8(*self._draw(t, view, trans, fi))
        img.flags.writeable = False
        self._last_sig, self._last_img, self._last_shift = sig, img, shift
        return img

    def _to_u8(self, dst, key, dirty):
        """uint8 にする。下の層（key）の変換を使い回し、上に描いた範囲（dirty）だけ変換する
        （to_rgba8 は画素ごとの変換なので、範囲に分けても全体を変換したのと同じ画素になる）"""
        np = self.np
        op = np.float32(self.s["opacity"])
        if key is None or dirty is None:
            if op < 1.0:
                dst *= op
            return fk.to_rgba8(dst)
        hit = self._u8.get(key)
        if hit is None:
            under = self._base[key]
            under = under * op if op < 1.0 else under
            hit = fk.to_rgba8(np.ascontiguousarray(under, dtype=np.float32))
            hit.flags.writeable = False
            self._u8[key] = hit
        img = hit.copy()
        H, W = dst.shape[:2]
        pix = [d for d in dirty if len(d) == 2]
        if pix:
            # 細い線: 触れた画素だけを (N, 1, 4) に集めて変換する
            ys = np.concatenate([d[0] for d in pix])
            xs = np.concatenate([d[1] for d in pix])
            g = dst[ys, xs][:, None, :]
            if op < 1.0:
                g = g * op
            img[ys, xs] = fk.to_rgba8(np.ascontiguousarray(g, dtype=np.float32))[:, 0, :]
        boxes = [d for d in dirty if len(d) == 4]
        for x0, y0, x1, y1 in _merge_boxes(boxes, W, H):
            sub = dst[y0:y1, x0:x1]
            if op < 1.0:
                sub = sub * op
            img[y0:y1, x0:x1] = fk.to_rgba8(np.ascontiguousarray(sub, dtype=np.float32))
        return img

    def _bg_wide(self, dst):
        """背景: ぼかして元の解像度へ広げ、不透明度を掛けて uint8 にする"""
        np, cv2 = self.np, self.cv2
        s = self.s
        img = cv2.GaussianBlur(dst, (0, 0), sigmaX=_BG_BLUR)
        rw, rh = s["render"]
        big = cv2.resize(img, (int(round(rw / s["k"])), int(round(rh / s["k"]))),
                         interpolation=cv2.INTER_LINEAR)
        Ww, H = s["wide"], s["size"][1]
        big = np.ascontiguousarray(big[:H, :Ww])
        big *= np.float32(s["opacity"])
        return fk.to_rgba8(big)

    # --- 描く ---

    def _d(self, x0, y0, x1, y1, m=3):
        """上に描いた範囲を控える（uint8 への変換をこの範囲だけにする）"""
        if self._dirty is not None:
            self._dirty.append((int(math.floor(x0)) - m, int(math.floor(y0)) - m,
                                int(math.ceil(x1)) + m, int(math.ceil(y1)) + m))

    def _draw(self, t, view, trans, fi):
        """(描いた canvas, 下の層の鍵 か None, 上に描いた範囲 か None)"""
        np = self.np
        s = self.s
        intro = _ease_out(t / _INTRO)
        k = s["k"]
        px0, py0, px1, py1 = s["plot"]
        sp_p = [_clamp01((t - e["at"]) / e["dur"]) for e in s["spans"]]
        ru_p = [_clamp01((t - e["at"]) / e["dur"]) for e in s["rules"]]
        # 1. 動かない層（格子・軸・目盛り）に、出終わった帯と基準線を重ねた層（使い回す）
        key = self._decor(view, trans, tuple(p >= 1.0 for p in sp_p),
                          tuple(p >= 1.0 for p in ru_p))
        dst = self._base[key].copy()
        self._dirty = [] if (intro >= 1.0 and s["mode"] == "main") else None
        if intro < 1.0:
            dst *= np.float32(intro)
        # 2. 現れている途中の帯と基準線
        for e, p in zip(s["spans"], sp_p):
            if 0 < p < 1:
                self._span_draw(dst, e, p)
                self._d(e["xa"], py0, e["xb"], py1)
        for e, p in zip(s["rules"], ru_p):
            if 0 < p < 1:
                self._rule_draw(dst, e, p, view)
                y = self.py(e["y"], view)
                self._d(px0, y - 3, px1, y + 3)
        # 3. 面の塗り
        for si, ser in enumerate(s["series"]):
            if ser["kind"] == "area":
                self._area(dst, ser, t, view)
        # 4. 出来事の印の線（データの下）
        for e in s["marks"]:
            p = _clamp01((t - e["at"]) / e["dur"])
            if p <= 0:
                continue
            g = _ease_io(_seg(p, 0.0, 0.6))
            top = e["top"]
            y_end = py1 + (top - py1) * g
            if py1 - y_end < 0.5:
                continue
            w = _MARK_W * k * (1.4 if k < 1 else 1)
            fk.polyline(dst, [(e["x"], py1), (e["x"], y_end)], e["color"], w, cap="butt",
                        dash=tuple(d * k for d in _MARK_DASH) if e["dash"] else None)
            self._d(e["x"] - w, y_end, e["x"] + w, py1)
        # 5. ローソク足・棒
        for si, ser in enumerate(s["series"]):
            if ser["kind"] in ("candle", "bar"):
                self._discrete(dst, si, ser, t, view)
        # 6. 線
        for si, ser in enumerate(s["series"]):
            if ser["kind"] in _CONTINUOUS:
                self._line_draw(dst, si, ser, t, view)
        # 7. 線の先（点と光）
        heads = {}
        for si, ser in enumerate(s["series"]):
            if ser["kind"] not in _CONTINUOUS:
                continue
            h, _q = self.head(ser, t)
            if h is None:
                continue
            hy = self.py(h[1], view)
            if hy < py0 - 2 or hy > py1 + 2:
                continue
            heads[si] = (h, hy)
            if ser["glow"]:
                fk.dots(dst, [(h[0], hy)], _alpha(ser["color"], _GLOW_ALPHA), _GLOW_R * k,
                        soft=_GLOW_SOFT * k)
            if s["mode"] == "main" or ser["glow"]:
                fk.circle(dst, (h[0], hy), _HEAD_R * k, ser["color"])
            r = (_GLOW_R + _GLOW_SOFT) * k + 2
            self._d(h[0] - r, hy - r, h[0] + r, hy + r)
        if s["mode"] != "main":
            return dst, None, None
        # 8. 札: 基準線・印・帯・値・今の値
        for e, p in zip(s["rules"], ru_p):
            if e["sp"] is None or p <= 0:
                continue
            b = self._rule_label_box(e, view)
            if b is None:
                continue
            x, y = int(round(b[0])), int(round(b[1]))
            fk.blit(dst, e["sp"], x, y, alpha=_ease_out(_seg(p, 0.5, 1.0)))
            self._d(x, y, x + e["sp"].w, y + e["sp"].h)
        for e in s["marks"] + s["spans"]:
            p = _clamp01((t - e["at"]) / e["dur"])
            if e["sp"] is None or p <= 0:
                continue
            a = _ease_out(_seg(p, 0.45, 1.0)) if "top" in e else _ease_out(p)
            if a > 0:
                x, y = e["pos"]
                fk.blit(dst, e["sp"], x, y, alpha=a)
                self._d(x, y, x + e["sp"].w, y + e["sp"].h)
        for ser in s["series"]:
            for b, a, sp, _x, _v in self._value_boxes(ser, t, view):
                fk.blit(dst, sp, b[0], b[1], alpha=a)
                self._d(*b)
        track = self._tag_track()
        for si, ser in enumerate(s["series"]):
            if ser["tag"] is None:
                continue
            if ser["kind"] in _CONTINUOUS:
                hh = heads.get(si)
                if hh is None:
                    continue
                h, hy = hh
            else:
                h, _q = self.head(ser, t)
                if h is None:
                    continue
                hy = self.py(h[1], view)
            a = _ease_out((t - ser["at"]) / _TAG_FADE)
            txt = _value_text(ser["tag"], s["y_format"], ser["dec"], h[1])
            sp = _make_text(s["fn"], txt, s["label_size"], ser["color"], _pill_text(ser["color"]),
                            s["font"], s["weight"])
            dy = track[si][fi] if si in track else 0.0
            x0 = int(round(h[0] + _TAG_GAP))
            y0 = int(round(hy - sp.h / 2.0 + dy))
            y0 = min(max(y0, int(py0)), int(py1) - sp.h)
            if abs(y0 + sp.h / 2.0 - hy) > 6:      # 札が線の先から離れたら細い線でつなぐ
                fk.polyline(dst, [(h[0] + _HEAD_R + 1, hy), (x0, y0 + sp.h / 2.0)],
                            _alpha(ser["color"], 0.6 * a), 1.5, cap="round")
                self._d(h[0], min(hy, y0), x0, max(hy, y0 + sp.h))
            fk.blit(dst, sp, x0, y0, alpha=a)
            self._d(x0, y0, x0 + sp.w, y0 + sp.h)
        return dst, key, self._dirty

    # --- 帯と基準線 ---

    def _span_draw(self, dst, e, p):
        k = self.s["k"]
        px0, py0, px1, py1 = self.s["plot"]
        a = _ease_out(p)
        box = (max(e["xa"], px0), py0, min(e["xb"], px1), py1)
        if box[2] - box[0] < 0.5:
            return
        fk.rect(dst, box, _alpha(e["color"], _SPAN_ALPHA * a))
        fk.hatch(dst, box, _alpha(e["color"], _SPAN_HATCH_ALPHA * a), spacing=14 * k,
                 angle=45, width=1.5 * k)

    def _rule_draw(self, dst, e, p, view):
        k = self.s["k"]
        px0, py0, px1, py1 = self.s["plot"]
        y = self.py(e["y"], view)
        if y < py0 - 1 or y > py1 + 1:
            return
        x1 = px0 + (px1 - px0) * _ease_io(p)
        if x1 - px0 < 0.5:
            return
        fk.polyline(dst, [(px0, y), (x1, y)], e["color"], _RULE_W * k * (1.4 if k < 1 else 1),
                    cap="butt", dash=tuple(d * k for d in _RULE_DASH) if e["dash"] else None)

    def _decor(self, view, trans, spans_done, rules_done):
        """動かない層 + 出終わった帯と基準線を作って self._base に置き、その鍵を返す
        （範囲と出終わった組が同じなら使い回す）"""
        key = ("decor", _vkey(view), None if trans is None else round(trans[2], 6),
               spans_done, rules_done)
        if key in self._base:
            return key
        dst = self._base_layer(view, trans)
        for e, done in zip(self.s["spans"], spans_done):
            if done:
                self._span_draw(dst, e, 1.0)
        for e, done in zip(self.s["rules"], rules_done):
            if done:
                self._rule_draw(dst, e, 1.0, view)
        dst.flags.writeable = False
        if len(self._base) >= 3:
            # 寄る途中はコマごとに層が変わるので溜めない（1080p で1枚 約 33MB）
            self._base.clear()
            self._u8.clear()
        self._base[key] = dst
        return key

    # --- 動かない層（格子・軸・目盛り）---

    def _base_layer(self, view, trans):
        """格子・軸・目盛りの新しい canvas（_decor が呼ぶ。使い回しは _decor の層で行う）"""
        s = self.s
        pal = s["pal"]
        k = s["k"]
        rw, rh = s["render"]
        dst = fk.canvas(rw, rh)
        px0, py0, px1, py1 = s["plot"]
        main = s["mode"] == "main"
        # 目盛り（範囲が変わる途中は前後の目盛りを入れ替える）
        if trans is None:
            sets = [(s["yticks"].get(_vkey(view)) or self._ticks_for(view), 1.0)]
        else:
            a_t = s["yticks"].get(_vkey(trans[0])) or self._ticks_for(trans[0])
            b_t = s["yticks"].get(_vkey(trans[1])) or self._ticks_for(trans[1])
            av = {round(v, 9) for v, _sp, _s in a_t}
            bv = {round(v, 9) for v, _sp, _s in b_t}
            u = trans[2]
            # 前の目盛りは前半で消え、次の目盛りは後半で現れる（重なって読めない間を作らない）
            sets = [([x for x in a_t if round(x[0], 9) not in bv], 1.0 - _seg(u, 0.0, 0.55)),
                    ([x for x in b_t if round(x[0], 9) not in av], _seg(u, 0.45, 1.0)),
                    ([x for x in a_t if round(x[0], 9) in bv], 1.0)]
        for ticks, a in sets:
            if a <= 0.004:
                continue
            for v, sp, _txt in ticks:
                y = self.py(v, view)
                if y < py0 - 0.5 or y > py1 + 0.5:
                    continue
                if trans is None:
                    y = round(y)
                if abs(y - py1) > 1.0 or not main:
                    fk.polyline(dst, [(px0, y), (px1, y)],
                                _alpha(pal["dim"], _GRID_ALPHA * a * (0.8 if not main else 1.0)),
                                _GRID_W * k * (1.5 if not main else 1.0), cap="butt")
                if main and sp is not None:
                    fk.blit(dst, sp, int(round(px0 - _YLABEL_GAP - sp.w)),
                            int(round(y - sp.h / 2.0)), alpha=a)
        if main:
            fk.polyline(dst, [(px0, py1), (px1, py1)], pal["line"], _AXIS_W, cap="butt")
            for x, sp, (lx, ly), _txt in s["xticks"]:
                xx = round(x)
                fk.polyline(dst, [(xx, py1), (xx, py1 + _TICK_LEN)], pal["line"], _AXIS_W,
                            cap="butt")
                fk.over(dst, sp, lx, ly)
        return dst

    def _ticks_for(self, view):
        return []

    # --- 線 ---

    def _chunks(self, ser):
        """[(番号, 区間の番号, 点の列)]（区間ごとに _CHUNK 点ずつ。隣と1点を共有する）"""
        out = ser.get("_chunks")
        if out is not None:
            return out
        out = []
        for gi, seg in enumerate(ser["segs"]):
            n = len(seg)
            if n == 1:
                out.append((len(out), gi, seg))
                continue
            for a in range(0, n - 1, _CHUNK):
                out.append((len(out), gi, seg[a:min(n, a + _CHUNK + 1)]))
        ser["_chunks"] = out
        return out

    def _poly_cov(self, cov, pts_px, width):
        """被覆率の層 cov（clip の範囲）に折れ線を最大値で足す"""
        np = self.np
        cx0, cy0, cx1, cy1 = self.clip
        xs = [p[0] for p in pts_px]
        ys = [p[1] for p in pts_px]
        m = width / 2.0 + 2.0
        bx0 = max(cx0, int(math.floor(min(xs) - m)))
        by0 = max(cy0, int(math.floor(min(ys) - m)))
        bx1 = min(cx1, int(math.ceil(max(xs) + m)))
        by1 = min(cy1, int(math.ceil(max(ys) + m)))
        if bx0 >= bx1 or by0 >= by1:
            return
        sub = fk.canvas(bx1 - bx0, by1 - by0)
        if len(pts_px) == 1:
            fk.circle(sub, (xs[0] - bx0, ys[0] - by0), width / 2.0, (255, 255, 255, 255))
        else:
            fk.polyline(sub, [(x - bx0, y - by0) for x, y in pts_px], (255, 255, 255, 255),
                        width, cap="round")
        view = cov[by0 - cy0:by1 - cy0, bx0 - cx0:bx1 - cx0]
        np.maximum(view, sub[..., 3], out=view)

    def _line_draw(self, dst, si, ser, t, view):
        np = self.np
        if t < ser["at"]:
            return
        h, q = self.head(ser, t)
        hx = ser["x0"] + (ser["x1"] - ser["x0"]) * q
        cx0, cy0, cx1, cy1 = self.clip
        vk = _vkey(view)
        cache = self._line.get(si)
        if cache is None or cache["vk"] != vk:
            cache = {"vk": vk, "done": set(),
                     "cov": np.zeros((cy1 - cy0, cx1 - cx0), np.float32)}
            self._line[si] = cache
        width = ser["width"]

        def to_px(pts):
            return [(x, self.py(v, view)) for x, v in pts]
        partial = []
        for cid, _gi, pts in self._chunks(ser):
            if pts[0][0] > hx + 1e-9:
                break
            if pts[-1][0] <= hx + 1e-9:
                if cid not in cache["done"]:
                    self._poly_cov(cache["cov"], to_px(pts), width)
                    cache["done"].add(cid)
                continue
            # 伸びている塊（線の先まで）
            cut = [p for p in pts if p[0] <= hx]
            if h is not None and abs(h[0] - hx) < 1e-6:
                cut.append((hx, h[1]))
            partial.append(cut)
        cov = cache["cov"]
        if partial:
            cov = cov.copy()
            for pts in partial:
                if len(pts) >= 1:
                    self._poly_cov(cov, to_px(pts), width)
        # 枠の外（寄った後にはみ出す所）を切る
        px0, py0, px1, py1 = self.s["plot"]
        top = int(math.floor(py0)) - cy0
        bot = int(math.ceil(py1)) - cy0
        if top > 0 or bot < cov.shape[0]:
            if cov is cache["cov"]:
                cov = cov.copy()
            m = max(1, int(math.ceil(width / 2.0)))
            cov[:max(0, top - m)] = 0
            cov[bot + m:] = 0
        b = _over_cov(np, dst, cov, cx0, cy0, ser["color"])
        if b is not None and self._dirty is not None:
            if len(b) == 2:
                self._dirty.append(b)
            else:
                self._d(*b, m=1)
        # 点の少ない線は点を描く
        if ser["dots"]:
            k = self.s["k"]
            pts = [(x, self.py(v, view)) for x, v in ser["points"] if x <= hx + 1e-6]
            for p in pts:
                if not py0 - 1 <= p[1] <= py1 + 1:
                    continue
                fk.circle(dst, p, _DOT_R * k, self.s["pal"]["panel"])
                fk.circle(dst, p, _DOT_R * k - 1.25 * k, ser["color"], width=2.5 * k)
                self._d(p[0] - _DOT_R * k, p[1] - _DOT_R * k, p[0] + _DOT_R * k,
                        p[1] + _DOT_R * k)

    def _area(self, dst, ser, t, view):
        """線の下の面（列ごとの縦の被覆率 × 上から下へ薄れる）"""
        np = self.np
        if t < ser["at"]:
            return
        _h, q = self.head(ser, t)
        hx = ser["x0"] + (ser["x1"] - ser["x0"]) * q
        px0, py0, px1, py1 = self.s["plot"]
        ix0, ix1 = max(0, int(math.floor(px0))), int(math.ceil(min(px1, hx)))
        iy0, iy1 = int(math.floor(py0)), int(math.ceil(py1))
        if ix1 <= ix0:
            return
        xc = np.arange(ix0, ix1, dtype=np.float64) + 0.5
        top = np.full(xc.shape, np.inf)
        colcov = np.zeros(xc.shape)
        for seg in ser["segs"]:
            sx = np.array([p[0] for p in seg])
            sy = np.array([self.py(p[1], view) for p in seg])
            a, b = sx[0], min(sx[-1], hx)
            if b <= a:
                continue
            inside = (xc + 0.5 > a) & (xc - 0.5 < b)
            if not inside.any():
                continue
            cover = np.clip(np.minimum(xc + 0.5, b) - np.maximum(xc - 0.5, a), 0, 1)
            if ser["kind"] == "step":
                idx = np.clip(np.searchsorted(sx, xc, side="right") - 1, 0, len(sx) - 1)
                yy = sy[idx]
            else:
                yy = np.interp(xc, sx, sy)
            top = np.where(inside, np.minimum(top, yy), top)
            colcov = np.where(inside, np.maximum(colcov, cover), colcov)
        if not np.isfinite(top).any():
            return
        top = np.where(np.isfinite(top), top, py1)
        top = np.maximum(top, py0)
        yc = np.arange(iy0, iy1, dtype=np.float64) + 0.5
        # 画素 [y, y+1) のうち top より下の割合
        frac = np.clip((yc[:, None] + 0.5) - top[None, :], 0.0, 1.0)
        frac = np.minimum(frac, np.clip(py1 - (yc[:, None] - 0.5), 0.0, 1.0))
        grad = np.clip((py1 - yc) / max(1.0, py1 - py0), 0.0, 1.0) ** 1.4
        cov = (frac * colcov[None, :] * grad[:, None]).astype(np.float32)
        b = _over_cov(np, dst, cov, ix0, iy0, _alpha(ser["color"], _AREA_ALPHA))
        if b is not None and self._dirty is not None:
            if len(b) == 2:
                self._dirty.append(b)
            else:
                self._d(*b, m=1)

    # --- ローソク足・棒 ---

    def _discrete(self, dst, si, ser, t, view):
        vk = _vkey(view)
        cache = self._items.get(si)
        rw, rh = self.s["render"]
        if cache is None or cache["vk"] != vk:
            cache = {"vk": vk, "n": 0, "layer": None, "box": None}
            self._items[si] = cache
        rise = ser["rise"]
        done = 0
        for st in ser["starts"]:
            if t >= st + rise - 1e-9:
                done += 1
            else:
                break
        if done > cache["n"]:
            layer = cache["layer"]
            if layer is None:
                layer = fk.canvas(rw, rh)
            box = cache["box"]
            for i in range(cache["n"], done):
                box = _union(box, self._item(layer, ser, i, 1.0, view))
            cache["layer"], cache["n"], cache["box"] = layer, done, box
        if cache["box"] is not None:
            # 描き終えた層を、描いた範囲だけ重ねる
            H, W = dst.shape[:2]
            x0, y0, x1, y1 = cache["box"]
            x0, y0 = max(0, int(math.floor(x0)) - 2), max(0, int(math.floor(y0)) - 2)
            x1, y1 = min(W, int(math.ceil(x1)) + 2), min(H, int(math.ceil(y1)) + 2)
            if x0 < x1 and y0 < y1:
                sub = cache["layer"][y0:y1, x0:x1]
                dp = dst[y0:y1, x0:x1]
                dp *= 1.0 - sub[..., 3:4]
                dp += sub
                self._d(x0, y0, x1, y1, m=1)
        for i in range(done, ser["n"]):
            st = ser["starts"][i]
            if t <= st:
                break
            b = self._item(dst, ser, i, _clamp01((t - st) / rise) if rise > 0 else 1.0, view)
            if b is not None:
                self._d(*b)

    def _item(self, dst, ser, i, p, view):
        """ローソク足・棒を1本描いて外接矩形を返す（p は立ち上がりの進み）"""
        k = self.s["k"]
        px0, py0, px1, py1 = self.s["plot"]
        e = _ease_out(p)
        it = ser["items"][i]
        x = it[0]
        hw = ser["half"]
        if ser["kind"] == "candle":
            _x, o, h, lo, c = it
            col = ser["color"] if c >= o else ser["down"]
            yo = self.py(o, view)
            yc = self.py(o + (c - o) * e, view)
            yh = self.py(o + (h - o) * e, view)
            yl = self.py(o + (lo - o) * e, view)
            yh, yl = max(yh, py0), min(yl, py1)
            wick = max(1.0 * k, min(2.0 * k, hw * 0.5))
            col_a = _alpha(col, _ease_out(min(1.0, p * 2.0)))
            if yl - yh > 0.5:
                fk.polyline(dst, [(x, yh), (x, yl)], col_a, wick, cap="butt")
            y0, y1 = min(yo, yc), max(yo, yc)
            if y1 - y0 < 1.5 * k:
                m = (y0 + y1) / 2.0
                y0, y1 = m - 0.75 * k, m + 0.75 * k
            y0, y1 = max(y0, py0), min(y1, py1)
            if y1 > y0:
                fk.rect(dst, (x - hw, y0, x + hw, y1), col_a)
            return (x - hw, min(yh, y0), x + hw, max(yl, y1))
        v = it[1]
        if v is None:
            return None
        col = ser["color"] if v >= 0 else ser["down"]
        yb = min(max(self.py(0.0, view), py0), py1)
        yt = min(max(self.py(v * e, view), py0), py1)
        if abs(yb - yt) < 0.5:
            return None
        r = min(_BAR_R * k, hw, abs(yb - yt))
        fk.polygon(dst, _bar_path(x - hw, x + hw, yb, yt, r), col)
        return (x - hw, min(yb, yt), x + hw, max(yb, yt))


def _bar_path(x0, x1, yb, yt, r):
    """根元が平らで、先の角だけ丸い棒の輪郭"""
    sgn = -1.0 if yt < yb else 1.0          # 上向きの棒は -1
    pts = [(x0, yb)]
    n = max(3, int(math.ceil(r / 1.5)))
    cy = yt - sgn * r
    for j in range(n + 1):                  # 左の角
        a = math.pi / 2 * j / n
        pts.append((x0 + r - r * math.cos(a), cy + sgn * r * math.sin(a)))
    for j in range(n + 1):                  # 右の角
        a = math.pi / 2 * (1 - j / n)
        pts.append((x1 - r + r * math.cos(a), cy + sgn * r * math.sin(a)))
    pts.append((x1, yb))
    return pts


def _overlap_area(a, b, m=0.0):
    """矩形どうしの（隙間 m を含めた）重なりの面積"""
    w = min(a[2], b[2] + m) - max(a[0], b[0] - m)
    h = min(a[3], b[3] + m) - max(a[1], b[1] - m)
    return w * h if w > 0 and h > 0 else 0.0


def _value_side(pts, i):
    """点 i の値の札を置く側: 'above' / 'below' / 'ul'（左上）/ 'ur'（右上）。線の無い側を選ぶ"""
    y = pts[i][1]
    prev = pts[i - 1][1] if i > 0 else None
    nxt = pts[i + 1][1] if i + 1 < len(pts) else None
    if prev is None and nxt is None:
        return "above"
    if prev is None:
        return "below" if nxt > y else "above"
    if nxt is None:
        return "above" if prev <= y else "below"
    if prev <= y and nxt <= y:
        return "above"                  # 山
    if prev >= y and nxt >= y:
        return "below"                  # 谷
    return "ul" if nxt > y else "ur"    # 上り坂 / 下り坂


def _union(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _merge_boxes(boxes, W, H):
    """範囲の並びを、キャンバスに切り詰めて重なる（近い）ものどうしまとめる"""
    out = []
    for b in boxes:
        b = (max(0, b[0]), max(0, b[1]), min(W, b[2]), min(H, b[3]))
        if b[0] >= b[2] or b[1] >= b[3]:
            continue
        out.append(b)
    merged = True
    while merged and len(out) > 1:
        merged = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                if _overlap(out[i], out[j], 8):
                    out[i] = _union(out[i], out[j])
                    del out[j]
                    merged = True
                    break
            if merged:
                break
    return out


def _alpha(rgba, a):
    r, g, b, al = rgba
    return (r, g, b, al * _clamp01(a))


def _over_cov(np, dst, cov, x0, y0, rgba):
    """被覆率の配列 cov（左上 (x0, y0)）を色 rgba（0..255）で dst へ over する。
    触れた範囲を返す: 矩形 (x0, y0, x1, y1) か、触れた画素が少なければ (行の添字, 列の添字)。
    何も触れなければ None"""
    nz_r = np.flatnonzero(cov.max(axis=1) > 0)
    if not len(nz_r):
        return None
    r0, r1 = int(nz_r[0]), int(nz_r[-1]) + 1
    nz_c = np.flatnonzero(cov[r0:r1].max(axis=0) > 0)
    c0, c1 = int(nz_c[0]), int(nz_c[-1]) + 1
    H, W = dst.shape[:2]
    ya, yb = max(0, y0 + r0), min(H, y0 + r1)
    xa, xb = max(0, x0 + c0), min(W, x0 + c1)
    if ya >= yb or xa >= xb:
        return None
    c = cov[ya - y0:yb - y0, xa - x0:xb - x0]
    a = np.float32(rgba[3] / 255.0)
    col = np.array([rgba[0] / 255.0, rgba[1] / 255.0, rgba[2] / 255.0, 1.0], np.float32)
    dp = dst[ya:yb, xa:xb]
    flat = np.flatnonzero(c)
    if len(flat) < c.size // 6:
        # 細い線: 触れた画素だけを更新する（外接矩形の全部は触らない）
        idx = np.divmod(flat, c.shape[1])
        w = (c[idx] * a)[:, None]
        dp[idx] = dp[idx] * (1.0 - w) + w * col
        return (idx[0] + ya, idx[1] + xa)
    w = c[..., None] * a
    dp *= 1.0 - w
    dp += w * col
    return (xa, ya, xb, yb)


# --- 入口 ---

def chart(*, x=None, y=None, mode="main", x_ticks=None, y_ticks=None, x_format=None,
          y_format=None, size=None, padding=40, label_size=36, tick_size=32, opacity=None,
          drift=None, font=None, weight=None, colors=None):
    """データのグラフ（線・階段・面・ローソク足・棒）の動く図。

    x: 横の範囲 (左端, 右端)。None は系列・印・帯から自動。値は数値・日時（datetime・date・
      ISO の文字列）・時刻（'HH:MM'。その日の 0 時からの秒）のどれか（全部同じ種類）。
    y: 縦の範囲 (下端, 上端)。None は値・基準線を含むきりのよい範囲（棒は 0 を含む）。
    mode: 'main'（主役。軸・目盛り・札あり）/ 'background'（背景。字なし・不透明度 0.35・
      太めの線をぼかして、毎秒 drift px で左へ流れる）。
    x_ticks: 横の目盛り。None は字が重ならない間隔を自動で選ぶ。数値の軸は間隔、日時・時刻の
      軸は単位（'minute' / 'hour' / 'day' / 'month' / 'year'）か (単位, 数)。値のリスト・
      (値, 字) のリストも可（[] で目盛りなし）。
    y_ticks: 縦の目盛り。None は自動（4〜7 本）。間隔か値のリスト（[] で無し）。
    x_format: 横の目盛りの字の書式（数値は {v}、日時は {year} {month} {day} {hour} {minute}
      {second} {wd}、時刻は {hour} {minute} {second}）。
    y_format: 縦の目盛りの字の書式（{v}。例 '{v}万円'・'{v:.0f}%'）。tag=True / values=True の
      札にも使う。None は値のけたに合わせた数（3 けたごとにカンマ）。
    size: キャンバス (幅, 高さ) px（省略時は Project の解像度）。padding: 余白 px。
    label_size / tick_size: 札（印・今の値）の字・目盛りと基準線の札の字の大きさ px。
    opacity: 全体の不透明度（既定 1、背景は 0.35）。drift: 背景の流れる速さ px/秒（既定 14）。
    font / weight: フォント。colors: 図の色の差し替え {名前: 色}
      （fg / accent / muted / line / dim / panel）。
    戻り値は Chart（series / rule / mark / span / zoom を足してから build()）。
    """
    return Chart(x=x, y=y, mode=mode, x_ticks=x_ticks, y_ticks=y_ticks, x_format=x_format,
                 y_format=y_format, size=size, padding=padding, label_size=label_size,
                 tick_size=tick_size, opacity=opacity, drift=drift, font=font, weight=weight,
                 colors=colors)
