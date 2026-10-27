# -*- coding: utf-8 -*-
"""timeline(): 年表・時刻の帯・期間の横棒（framekit.build の上に作る動く図）

数値か日時の軸に「印（点＋札）」「期間（横棒）」「括弧（2点の間の注記）」を並べ、
それぞれに現れる秒（at=）と現れ方（enter=）を持たせて、1本の透過動画 Object にする。

    tl = timeline(18, 24, ticks=1, tick_format="{v}:00")      # 時刻の帯（数値の軸）
    tl.mark(19, "19:00\\nニコニコ 停止", note="予防のため", at=0.5)
    tl.mark(22.5, "22:30過ぎ\\n公開", at=1.5)
    tl.bracket(19, 22.5, "約3時間半", at=2.5)                  # 軸の下に括弧が伸びる
    fig = tl.build()                                            # 最後の出来事の終わり + 1 秒
    fig.time() <= move(x=0.5, y=0.5, anchor="center")

    tl = timeline(datetime(2026, 10, 23), datetime(2026, 10, 28), ticks="day",
                  tick_place="between", grid=True)               # 日時の軸
    tl.span("2026-10-23 19:30", "2026-10-25 09:25", "ドコモ", row=0, note="25日 9:25", at=1)

要素（時刻 at はこの図の動画の先頭を 0 とした秒。軸の値は軸と同じ種類）:
  mark(v, label=None, ...)      軸の上の点と、柱でつないだ札（side='above' / 'below'）。
                                card=True で板つきの札。enter は fade / rise / slide / cut
  span(a, b, label=None, ...)   期間の横棒。row=None は軸の上の帯、row=0, 1, … は軸の下の段。
                                enter は grow（左から伸びる）/ fade / cut
  bracket(a, b, label=None, ...) 2点の間の括弧と注記。enter は grow / fade / cut
  build(duration=None)          動画 Object（obj.figure.x(v)・axis_y・rows・items が付く）
  x(v)                          軸の値 → キャンバスの x（px）。build の前から引ける
数字が回る見出しは odometer を重ねる（obj.figure.x と axis_y で位置を取る）。

配置の決まり（後から出る要素で前の要素が動かない。全部の要素込みで build のときに決める）
------------------------------------------------------------------------------
- 縦の並び（上から）: 軸の上の札の段 → 軸 → 目盛りの字 → 段（row）の横棒 → 軸の下の札の段。
  全体をキャンバスの縦の中央に置く（axis_y で軸の高さを決めることもできる）。
- 札（mark の札・軸の上の帯の札・括弧）は、呼んだ順に「軸に近い所から」置く。
  ほかの札と重なる・柱がほかの札を貫く・ほかの札の柱を覆う所は避けて、外側へずらす
  （段は札ごとに高さが違ってよい）。横は x を中心にし、キャンバスに収まるよう寄せる。
- 札の中は「軸に近い側から label・note」の順（note は muted の小さい字）。板つきの札
  （card=True）は上から label・note。
- 段（row）の横棒は、label を棒の左端の上（side='below' なら下）に、note を棒の右に置く。
  同じ段の字どうし・字とほかの棒が重なる、キャンバスからはみ出す、は ValueError。
- 軸の途中を省く（breaks=[(a, b)]）と、その区間は 40px の隙間（斜めの2本線）になり、
  残りは同じ縮尺で並ぶ。隙間を跨ぐ帯は隙間で切れて見える。

絵の作り
--------
- 線は framekit の polyline / rect / circle（被覆率の面積でアンチエイリアス）。文字は
  framekit.label（text_image と同じ描き方。黒の縁取り 3px）。
- 動かない層（軸・目盛り・格子・矢じり）と目盛りの字の層は1回だけ描く。毎コマは
  「動かない層の写し → 帯と棒 → 省く印 → 柱と括弧 → 点 → 目盛りの字 → 札」の順に重ねる。
- 要素の進み具合が前のコマと同じ（現れ終わった後・現れる前）コマは、前のコマをそのまま返す。

重さ（実測。Windows・Python 3.10・numpy 2.0・1920x1080・1 コマの描画）
---------------------------------------------------------------------
- 動いているコマ: 全面の写しと to_rgba8 で平均 約 20〜30ms・最大 約 50ms（4つの見本の場面）。
- 動いていないコマ: 0.01ms 未満（前のコマを返す）。時刻の帯（印 3 つと括弧）の 6 秒の図の
  生成は ffmpeg（qtrle）込みで約 2 秒。

numpy・opencv-python・Pillow を使う（optional 依存。framekit.need で遅延 import）。
構築（timeline() の呼び出し）にも要る（目盛りの字の寸法を Pillow で測る）。
"""

import datetime as _dt
import math
import warnings
from fractions import Fraction

import scriptvedit.framekit as fk
from scriptvedit.context import current_project
from scriptvedit.stillseq import _fps_fraction, _resolve_fps, _resolve_size
from scriptvedit.validate import _require_choice, _require_number


# --- 定数 ---

# 描画の版（配置の手順・描き方・時刻の決め方を変えたら上げる。配置の結果は鍵に
# 入らないので、配置の手順を変えたときも必ず上げる）
_TIMELINE_VER = "1"

_SIDES = ("above", "below")
_MARK_ENTERS = ("fade", "rise", "slide", "cut")
_SPAN_ENTERS = ("grow", "fade", "cut")
_BRACKET_ENTERS = ("grow", "fade", "cut")
_DOTS = ("fill", "ring", "none")
_TICK_PLACES = ("tick", "between")
_TIME_UNITS = ("year", "month", "day", "hour", "minute")

# 既定の現れる秒
_DUR = {"mark": 0.5, "span": 0.8, "bracket": 0.6}

# 自動の目盛り（数値の軸）の数の上限・明示の目盛りの数の上限
_AUTO_TICKS_MAX = 8
_MAX_TICKS = 200
_MAX_ITEMS = 400

# 自動の目盛り（日時の軸）の候補（小さい順）。最初に _AUTO_TICKS_MAX 以下になるもの
_TIME_STEPS = (
    ("minute", 1), ("minute", 5), ("minute", 10), ("minute", 15), ("minute", 30),
    ("hour", 1), ("hour", 2), ("hour", 3), ("hour", 6), ("hour", 12),
    ("day", 1), ("day", 2), ("day", 7),
    ("month", 1), ("month", 2), ("month", 3), ("month", 6),
    ("year", 1), ("year", 2), ("year", 5), ("year", 10), ("year", 20), ("year", 50),
    ("year", 100),
)
_UNIT_SEC = {"minute": 60.0, "hour": 3600.0, "day": 86400.0, "month": 2629746.0,
             "year": 31556952.0}
_WEEKDAYS = "月火水木金土日"

# 線と形（px）
_AXIS_W = 4              # 軸の太さ
_TICK_W = 3              # 目盛りの線の太さ
_TICK_LEN = 10           # 目盛りの線の、軸から上下へ出る長さ
_TICK_TEXT_GAP = 4       # 目盛りの線の下端と字の間
_GRID_W = 2              # 格子（段の帯の中の縦線）の太さ
_OVERHANG = 16           # 軸の線が、両端の目盛りより外へ出る長さ
_ARROW_EXT = 40          # 矢じりつきの軸が右端の目盛りより外へ出る長さ
_ARROW_HEAD = 18         # 矢じりの長さ
_BREAK_GAP = 40          # 省いた区間の幅
_BREAK_SLANT = 8         # 省く印の斜めの線の、上下の端の横のずれ（半分）
_BREAK_RISE = 18         # 省く印の斜めの線の、軸から上下へ出る長さ
_BREAK_SEP = 7           # 省く印の2本の線の、中心からの距離
_STEM_W = 3              # 柱の太さ
_STEM_GAP = 4            # 柱の先と札（板の無い札）の間
_ALIGN_INSET = 20        # 札を柱の側へ寄せるとき、柱から札の端までの距離
_DOT_R = 10              # 点の半径
_RING_W = 3              # 白抜きの点の輪の太さ
_BAND_H = 14             # 軸の上の帯の太さ（thickness=1）
_BAR_RADIUS = 6          # 段の横棒の角の丸み
_BRACKET_W = 4           # 括弧の線の太さ
_BRACKET_TICK = 14       # 括弧の両端の、軸へ向かう短い線の長さ
_BRACKET_TEXT_GAP = 4    # 括弧の線と字の間
_BASE_ABOVE = 40         # 軸の上の札の段の、軸からの最小の距離（柱が 20px 余り見える）
_BASE_BELOW = 14         # 軸の下（目盛りも段も無いとき）の柱の無い札の、軸からの最小の距離
_BASE_BARE = 16          # 軸の上の柱の無い札（帯の札・括弧）の、軸からの最小の距離
_ZONE_GAP = 14           # 目盛りの字・段の帯と、その下のものの間
_BELOW_TICKS = 28        # 目盛りの字と、その下の札の段の間（柱が字の下から少し見える）
_LANE_GAP = 16           # 段どうしの間
_LANE_TEXT_GAP = 2       # 段の label と棒の間
_NOTE_GAP = 12           # 棒の右端と note の間
_BOX_GAP = 8             # 札どうしの最小の隙間（縦横）
_CARD_PAD = (18, 10)     # 板つきの札の中の余白（横, 縦）
_CARD_R = 10             # 板の角の丸み
_CARD_EDGE = 2           # 板の枠の太さ
_HALO = 3                # 文字の黒い縁取り
_SLIDE = 120             # enter='slide' の札が滑ってくる距離
_RISE = 24               # enter='rise' の札が立ち上がる距離

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


def _is_num(v):
    return not isinstance(v, bool) and isinstance(v, (int, float))


def _is_time(v):
    return isinstance(v, (_dt.datetime, _dt.date, str))


def _as_datetime(fn, name, v):
    """datetime / date / ISO の文字列 → datetime（date は 0 時）"""
    if isinstance(v, _dt.datetime):
        return v
    if isinstance(v, _dt.date):
        return _dt.datetime(v.year, v.month, v.day)
    if isinstance(v, str):
        try:
            return _dt.datetime.fromisoformat(v.strip())
        except ValueError:
            raise ValueError(
                f"{fn}: {name} は ISO の日時の文字列（'2026-10-23 19:30' など）で"
                f"指定してください: {v!r}") from None
    raise TypeError(f"{fn}: {name} は datetime・date・ISO の日時の文字列で指定してください: {v!r}")


def _check_text(fn, name, v):
    if v is None:
        return None
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{fn}: {name} は空でない文字列で指定してください: {v!r}")
    return v


def _check_at(fn, at):
    _require_number(fn, "at", at, 0, None)
    return float(at)


def _check_dur(fn, dur, kind):
    if dur is None:
        return _DUR[kind]
    _require_number(fn, "dur", dur, 0, 600)
    if dur <= 0:
        raise ValueError(f"{fn}: dur は 0 より大きくしてください: {dur!r}")
    return float(dur)


def _overlap(a, b, m=0.0):
    """矩形 (x0, y0, x1, y1) どうしが（隙間 m を含めて）重なるか"""
    return (a[0] < b[2] + m and b[0] < a[2] + m and a[1] < b[3] + m and b[1] < a[3] + m)


# --- 軸 ---

class _Axis:
    """軸の値（数値か日時）と、キャンバスの x への写し（省いた区間つき）"""

    def __init__(self, fn, start, end, breaks):
        self.fn = fn
        if _is_num(start) and _is_num(end):
            self.kind = "num"
            _require_number(fn, "start", start)
            _require_number(fn, "end", end)
            self.t0 = None
            self.lo, self.hi = float(start), float(end)
        elif _is_time(start) and _is_time(end):
            self.kind = "time"
            s = _as_datetime(fn, "start", start)
            e = _as_datetime(fn, "end", end)
            if (s.tzinfo is None) != (e.tzinfo is None):
                raise ValueError(f"{fn}: start と end はどちらもタイムゾーンつきか、どちらも無しにしてください")
            self.t0 = s
            self.lo, self.hi = 0.0, (e - s).total_seconds()
        else:
            raise TypeError(
                f"{fn}: start と end は、どちらも数値か、どちらも日時（datetime・date・"
                f"ISO の文字列）で指定してください: {start!r}, {end!r}")
        if not self.hi > self.lo:
            raise ValueError(f"{fn}: end は start より後にしてください: {start!r}, {end!r}")
        self.breaks = []
        if breaks:
            if not isinstance(breaks, (list, tuple)):
                raise ValueError(f"{fn}: breaks は [(a, b), …] で指定してください: {breaks!r}")
            for k, br in enumerate(breaks):
                if not isinstance(br, (list, tuple)) or len(br) != 2:
                    raise ValueError(f"{fn}: breaks[{k}] は (a, b) で指定してください: {br!r}")
                a = self.num(f"breaks[{k}] の a", br[0])
                b = self.num(f"breaks[{k}] の b", br[1])
                if not (self.lo < a < b < self.hi):
                    raise ValueError(
                        f"{fn}: breaks[{k}] は start < a < b < end にしてください: {br!r}")
                self.breaks.append((a, b))
            self.breaks.sort()
            for k in range(1, len(self.breaks)):
                if self.breaks[k][0] <= self.breaks[k - 1][1]:
                    raise ValueError(f"{fn}: breaks の区間が重なっています: {breaks!r}")
        self.x0 = self.x1 = None
        self._k = None

    def num(self, name, v):
        """軸の値 → 数（日時の軸は start からの秒）。種類違い・範囲外は ValueError"""
        fn = self.fn
        if self.kind == "num":
            if not _is_num(v):
                raise TypeError(f"{fn}: {name} は数値で指定してください（数値の軸）: {v!r}")
            _require_number(fn, name, v)
            f = float(v)
        else:
            if not _is_time(v):
                raise TypeError(
                    f"{fn}: {name} は日時（datetime・date・ISO の文字列）で指定してください"
                    f"（日時の軸）: {v!r}")
            d = _as_datetime(fn, name, v)
            if (d.tzinfo is None) != (self.t0.tzinfo is None):
                raise ValueError(
                    f"{fn}: {name} のタイムゾーンの有無が軸（start）と違います: {v!r}")
            f = (d - self.t0).total_seconds()
        eps = 1e-9 * max(1.0, abs(self.hi - self.lo))
        if f < self.lo - eps or f > self.hi + eps:
            raise ValueError(f"{fn}: {name} が軸の範囲（start〜end）の外です: {v!r}")
        return min(max(f, self.lo), self.hi)

    def key(self, f):
        """鍵に入れる値（数値はそのまま、日時は ISO の文字列）"""
        if self.kind == "num":
            return fk.norm(f)
        d = self.t0 + _dt.timedelta(seconds=f)
        return d.isoformat()

    def at_time(self, f):
        return self.t0 + _dt.timedelta(seconds=f)

    def in_break(self, f):
        return any(a < f < b for a, b in self.breaks)

    def set_px(self, x0, x1):
        span = x1 - x0 - _BREAK_GAP * len(self.breaks)
        total = (self.hi - self.lo) - sum(b - a for a, b in self.breaks)
        if span <= 4:
            raise ValueError(
                f"{self.fn}: 軸の幅が足りません（size を広げるか、breaks・目盛りの字を減らす）")
        self.x0, self.x1 = float(x0), float(x1)
        self._k = span / total

    def x(self, f):
        """数 → x（省いた区間の中は隙間の中で線形）"""
        pos = self.x0
        prev = self.lo
        for a, b in self.breaks:
            if f <= a:
                return pos + (f - prev) * self._k
            pos += (a - prev) * self._k
            if f < b:
                return pos + _BREAK_GAP * (f - a) / (b - a)
            pos += _BREAK_GAP
            prev = b
        return pos + (f - prev) * self._k

    def break_xs(self):
        """省いた区間の隙間の中心の x"""
        out = []
        pos = self.x0
        prev = self.lo
        for a, b in self.breaks:
            pos += (a - prev) * self._k
            out.append(pos + _BREAK_GAP / 2.0)
            pos += _BREAK_GAP
            prev = b
        return out

    # --- 目盛り ---

    def ticks(self, ticks, tick_format):
        """[(数, 字), …] を返す（省いた区間の中の目盛りは除く）"""
        fn = self.fn
        if tick_format is not None and not isinstance(tick_format, str):
            raise TypeError(f"{fn}: tick_format は書式の文字列で指定してください: {tick_format!r}")
        if isinstance(ticks, (list, tuple)) and not self._is_unit_spec(ticks):
            out = []
            if len(ticks) > _MAX_TICKS:
                raise ValueError(f"{fn}: 目盛りが {len(ticks)} 個あります（上限 {_MAX_TICKS}）")
            for k, t in enumerate(ticks):
                if (isinstance(t, (list, tuple)) and len(t) == 2
                        and isinstance(t[1], str)):
                    f = self.num(f"ticks[{k}]", t[0])
                    out.append((f, _check_text(fn, f"ticks[{k}] の字", t[1])))
                else:
                    f = self.num(f"ticks[{k}]", t)
                    fmt = tick_format or self._default_format(None)
                    out.append((f, self._fmt(f, fmt, fmt)))
            out.sort(key=lambda p: p[0])
            return [p for p in out if not self.in_break(p[0])]
        if self.kind == "num":
            if ticks is None:
                step = self._auto_step()
            else:
                if not _is_num(ticks):
                    raise TypeError(
                        f"{fn}: 数値の軸の ticks は間隔（数）か値のリストで指定してください: {ticks!r}")
                _require_number(fn, "ticks", ticks, 0, None)
                if ticks <= 0:
                    raise ValueError(f"{fn}: ticks の間隔は 0 より大きくしてください: {ticks!r}")
                step = float(ticks)
            k0 = math.ceil(self.lo / step - 1e-9)
            k1 = math.floor(self.hi / step + 1e-9)
            if k1 - k0 + 1 > _MAX_TICKS:
                raise ValueError(
                    f"{fn}: 目盛りが {k1 - k0 + 1} 個になります（上限 {_MAX_TICKS}。ticks を広げる）")
            vals = [round(k * step, 10) for k in range(k0, k1 + 1)]
            fmt = tick_format or "{v}"
            return [(v, self._fmt(v, fmt, fmt)) for v in vals if not self.in_break(v)]
        # 日時の軸
        if ticks is None:
            unit, n = self._auto_unit()
        else:
            unit, n = self._unit_spec(ticks)
        dts = self._time_ticks(unit, n)
        if len(dts) > _MAX_TICKS:
            raise ValueError(
                f"{fn}: 目盛りが {len(dts)} 個になります（上限 {_MAX_TICKS}。ticks の単位を大きく）")
        fmt = tick_format or self._default_format(unit, dts)
        out = []
        for d in dts:
            f = (d - self.t0).total_seconds()
            if not self.in_break(f):
                out.append((f, self._fmt(f, fmt, fmt)))
        return out

    @staticmethod
    def _is_unit_spec(v):
        return (isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], str)
                and v[0] in _TIME_UNITS)

    def _unit_spec(self, ticks):
        fn = self.fn
        if isinstance(ticks, str):
            unit, n = ticks, 1
        elif self._is_unit_spec(ticks):
            unit, n = ticks
        else:
            raise TypeError(
                f"{fn}: 日時の軸の ticks は単位（{', '.join(_TIME_UNITS)}）か (単位, 数) か"
                f"値のリストで指定してください: {ticks!r}")
        _require_choice(fn, "ticks の単位", unit, _TIME_UNITS)
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError(f"{fn}: ticks の数は 1 以上の整数で指定してください: {ticks!r}")
        return unit, n

    def _auto_step(self):
        rng = self.hi - self.lo
        e = math.floor(math.log10(rng))
        for k in range(e - 2, e + 2):
            for m in (1, 2, 5):
                step = m * 10.0 ** k
                if math.floor(self.hi / step + 1e-9) - math.ceil(self.lo / step - 1e-9) + 1 \
                        <= _AUTO_TICKS_MAX:
                    return step
        return 10.0 ** (e + 1)

    def _auto_unit(self):
        rng = self.hi - self.lo
        for unit, n in _TIME_STEPS:
            if rng / (_UNIT_SEC[unit] * n) > 4 * _AUTO_TICKS_MAX:
                continue
            if len(self._time_ticks(unit, n)) <= _AUTO_TICKS_MAX:
                return unit, n
        return _TIME_STEPS[-1]

    def _time_ticks(self, unit, n):
        t0 = self.t0
        t1 = self.t0 + _dt.timedelta(seconds=self.hi)
        out = []
        if unit == "year":
            y = t0.year
            if t0 > t0.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0):
                y += 1
            y = -(-y // n) * n
            while y <= t1.year:
                d = t0.replace(year=y, month=1, day=1, hour=0, minute=0, second=0,
                               microsecond=0)
                if d > t1:
                    break
                out.append(d)
                y += n
        elif unit == "month":
            m = t0.year * 12 + t0.month - 1
            if t0 > t0.replace(day=1, hour=0, minute=0, second=0, microsecond=0):
                m += 1
            m = -(-m // n) * n
            while True:
                d = t0.replace(year=m // 12, month=m % 12 + 1, day=1, hour=0, minute=0,
                               second=0, microsecond=0)
                if d > t1:
                    break
                out.append(d)
                m += n
        else:
            if unit == "day":
                d = t0.replace(hour=0, minute=0, second=0, microsecond=0)
                step = _dt.timedelta(days=n)
                if d < t0:
                    d += _dt.timedelta(days=1)
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
            while d <= t1:
                out.append(d)
                if len(out) > _MAX_TICKS:
                    break
                d += step
        return out

    def _default_format(self, unit, dts=None):
        if self.kind == "num":
            return "{v}"
        if unit is None:
            return "{year}-{month:02d}-{day:02d} {hour}:{minute:02d}"
        dts = dts or []
        same_year = len({d.year for d in dts}) <= 1
        same_month = len({(d.year, d.month) for d in dts}) <= 1
        if unit == "year":
            return "{year}年"
        if unit == "month":
            return "{month}月" if same_year else "{year}年{month}月"
        if unit == "day":
            return "{day}日" if same_month else "{month}月{day}日"
        return "{hour}:{minute:02d}"

    def _fields(self, f):
        if self.kind == "num":
            v = round(f, 10)
            return {"v": int(v) if float(v).is_integer() else v}
        d = self.at_time(f)
        return {"year": d.year, "month": d.month, "day": d.day, "hour": d.hour,
                "minute": d.minute, "second": d.second, "wd": _WEEKDAYS[d.weekday()]}

    def _fmt(self, f, fmt, shown):
        fields = self._fields(f)
        try:
            return fmt.format(**fields)
        except (KeyError, IndexError, ValueError) as e:
            names = ", ".join(sorted(fields))
            raise ValueError(
                f"{self.fn}: tick_format {shown!r} を目盛りに当てられません（{e}。"
                f"使える名前: {names}）") from None


# --- 図 ---

class Timeline:
    """timeline() が返す図。要素を足してから build() で Object にする。

    要素（at はこの図の動画の先頭を 0 とした秒。軸の値は軸と同じ種類）:
      mark(v, label=None, *, note=None, at=0, dur=0.5, enter='fade', side='above',
           color='fg', text_color='fg', dot='fill', card=False)
      span(a, b, label=None, *, note=None, at=0, dur=0.8, enter='grow', row=None,
           side='above', color='line', text_color='fg', thickness=1.0)
      bracket(a, b, label=None, *, at=0, dur=0.6, enter='grow', side='below',
              color='fg', text_color='fg')
      build(duration=None)   動画 Object（obj.figure: x・axis_y・rows・items・ticks・size・duration）
      x(v)                   軸の値 → キャンバスの x（px）
    どれも self を返す（続けて書ける）。
    """

    def __init__(self, start, end, *, ticks=None, tick_format=None, tick_place="tick",
                 breaks=(), arrow=False, grid=False, size=None, padding=40, axis_y=None,
                 bar=40, label_size=44, tick_size=32, font=None, weight=None, colors=None):
        fn = self._fn = "timeline"
        if size is None:
            proj = current_project()
            size = (proj.width, proj.height) if proj else (1920, 1080)
        self._W, self._H = _resolve_size(fn, size)
        _require_number(fn, "padding", padding, 0, None)
        self._pad = int(round(padding))
        if 2 * self._pad >= min(self._W, self._H):
            raise ValueError(f"{fn}: padding が size に対して大きすぎます: {padding!r}")
        if axis_y is not None:
            _require_number(fn, "axis_y", axis_y, 0, self._H)
            axis_y = int(round(axis_y))
        self._axis_y_req = axis_y
        _require_number(fn, "bar", bar, 2, 400)
        self._bar = int(round(bar))
        _require_number(fn, "label_size", label_size, 8, 400)
        _require_number(fn, "tick_size", tick_size, 8, 400)
        self._label_size = fk.norm(float(label_size))
        self._tick_size = fk.norm(float(tick_size))
        self._tick_place = _require_choice(fn, "tick_place", tick_place, _TICK_PLACES)
        if not isinstance(arrow, bool):
            raise TypeError(f"{fn}: arrow は True / False で指定してください: {arrow!r}")
        if not isinstance(grid, bool):
            raise TypeError(f"{fn}: grid は True / False で指定してください: {grid!r}")
        self._arrow = arrow
        self._grid = grid
        self._font = font
        self._weight = weight
        self._pal = fk.palette(fn, colors)

        self._axis = _Axis(fn, start, end, breaks)
        self._ticks = self._axis.ticks(ticks, tick_format)
        self._sprites = {}
        # 目盛りの字（寸法で軸の両端の余白が決まる）
        self._tick_sprites = [self._text(s, self._tick_size, self._pal["muted"], "center")
                              for _f, s in self._ticks]
        left = right = _OVERHANG
        if self._tick_place == "tick" and self._ticks:
            if abs(self._ticks[0][0] - self._axis.lo) < 1e-9:
                left = max(left, self._tick_sprites[0].w / 2.0)
            if abs(self._ticks[-1][0] - self._axis.hi) < 1e-9:
                right = max(right, self._tick_sprites[-1].w / 2.0)
        if arrow:
            right = max(right, _ARROW_EXT + 2)
        self._axis.set_px(self._pad + math.ceil(left), self._W - self._pad - math.ceil(right))
        self._items = []

    # --- 文字 ---

    def _text(self, s, size, rgba, align):
        k = (s, size, tuple(rgba), align)
        sp = self._sprites.get(k)
        if sp is None:
            # 縦の余白は縁取りの分だけ（札の中で label と note を重ねるときに間が空きすぎない）。
            # 横は text_image の既定と同じ（縁取り + 字の大きさの 15%）
            pad = (_HALO + int(math.ceil(float(size) * 0.15)), _HALO + 1)
            sp = fk.label(self._fn, s, size=size, font=self._font, weight=self._weight,
                          color=tuple(rgba), align=align, border=_HALO,
                          border_color=(0, 0, 0, 255), padding=pad)
            self._sprites[k] = sp
        return sp

    def _color(self, fn, value):
        return tuple(fk.color(fn, value, self._pal))

    # --- 要素 ---

    def x(self, v):
        """軸の値 → キャンバスの x（px。端数あり）"""
        return self._axis.x(self._axis.num("v", v))

    def _add(self, item):
        if len(self._items) >= _MAX_ITEMS:
            raise ValueError(f"{self._fn}: 要素が多すぎます（上限 {_MAX_ITEMS}）")
        self._items.append(item)
        return self

    def mark(self, v, label=None, *, note=None, at=0.0, dur=None, enter="fade", side="above",
             color="fg", text_color="fg", dot="fill", card=False):
        """軸の上の点と、柱でつないだ札。

        v: 軸の値。label: 札の字（改行可。None で点だけ）。note: 札の補足（muted の小さい字。
          軸から見て label の外側。板つきの札では label の下）。
        enter: 'fade'（点 → 柱が伸びる → 札が現れる）/ 'rise'（札が軸の側から立ち上がる）/
          'slide'（右から滑り込む）/ 'cut'（at で一度に出る）。
        side: 札を軸の上（'above'）か下（'below'）に置く。
        color: 点と、板つきの札の枠の色。text_color: label の色。
        dot: 'fill'（塗り）/ 'ring'（白抜き）/ 'none'（点なし）。card: 板つきの札にする。
        """
        fn = "timeline.mark"
        f = self._axis.num("v", v)
        label = _check_text(fn, "label", label)
        note = _check_text(fn, "note", note)
        if note is not None and label is None:
            raise ValueError(f"{fn}: note は label と一緒に指定してください")
        if not isinstance(card, bool):
            raise TypeError(f"{fn}: card は True / False で指定してください: {card!r}")
        if card and label is None:
            raise ValueError(f"{fn}: card=True には label が要ります")
        return self._add({
            "kind": "mark", "v": f, "label": label, "note": note,
            "at": _check_at(fn, at), "dur": _check_dur(fn, dur, "mark"),
            "enter": _require_choice(fn, "enter", enter, _MARK_ENTERS),
            "side": _require_choice(fn, "side", side, _SIDES),
            "color": self._color(fn, color), "text_color": self._color(fn, text_color),
            "dot": _require_choice(fn, "dot", dot, _DOTS), "card": card})

    def span(self, a, b, label=None, *, note=None, at=0.0, dur=None, enter="grow", row=None,
             side="above", color="line", text_color="fg", thickness=1.0):
        """期間の横棒（a から b まで）。

        row: None は軸の上の帯（太さ 14px × thickness。label と note は帯の中央の上か下の札）。
          0, 1, … は軸の下の段（太さ bar × thickness。label は棒の左端の上か下、note は棒の右）。
          同じ段に細い棒を重ねる（thickness=0.35 など）と、太い棒の中の区間を示せる。
        enter: 'grow'（左から伸びる。label は伸び始めに、note は伸び終わりに現れる）/
          'fade' / 'cut'。
        side: label の側（'above' / 'below'）。color: 棒の色。text_color: label の色。
        """
        fn = "timeline.span"
        fa = self._axis.num("a", a)
        fb = self._axis.num("b", b)
        if not fb > fa:
            raise ValueError(f"{fn}: b は a より後にしてください: {a!r}, {b!r}")
        if row is not None and (isinstance(row, bool) or not isinstance(row, int)
                                or not 0 <= row < 32):
            raise ValueError(f"{fn}: row は None か 0〜31 の整数で指定してください: {row!r}")
        _require_number(fn, "thickness", thickness, 0.05, 1)
        return self._add({
            "kind": "span", "a": fa, "b": fb, "label": _check_text(fn, "label", label),
            "note": _check_text(fn, "note", note), "row": row,
            "at": _check_at(fn, at), "dur": _check_dur(fn, dur, "span"),
            "enter": _require_choice(fn, "enter", enter, _SPAN_ENTERS),
            "side": _require_choice(fn, "side", side, _SIDES),
            "color": self._color(fn, color), "text_color": self._color(fn, text_color),
            "thickness": fk.norm(float(thickness))})

    def bracket(self, a, b, label=None, *, at=0.0, dur=None, enter="grow", side="below",
                color="fg", text_color="fg"):
        """2点（a・b）の間の括弧と注記（「約3時間半」など）。

        side: 軸の下（'below'。└──┘ の形で字は下）か上（'above'。┌──┐ の形で字は上）。
        enter: 'grow'（左から右へ伸び、字は伸び終わりに現れる）/ 'fade' / 'cut'。
        """
        fn = "timeline.bracket"
        fa = self._axis.num("a", a)
        fb = self._axis.num("b", b)
        if not fb > fa:
            raise ValueError(f"{fn}: b は a より後にしてください: {a!r}, {b!r}")
        return self._add({
            "kind": "bracket", "a": fa, "b": fb, "label": _check_text(fn, "label", label),
            "at": _check_at(fn, at), "dur": _check_dur(fn, dur, "bracket"),
            "enter": _require_choice(fn, "enter", enter, _BRACKET_ENTERS),
            "side": _require_choice(fn, "side", side, _SIDES),
            "color": self._color(fn, color), "text_color": self._color(fn, text_color)})

    # --- 配置 ---

    def _end_time(self):
        return max([it["at"] + (0.0 if it["enter"] == "cut" else it["dur"])
                    for it in self._items] or [0.0])

    def _label_stack(self, it, card):
        """札の中の字の Sprite の並び（軸に近い側から。板つきは上から）"""
        sps = []
        align = "left" if card else "center"
        if it["label"] is not None:
            sps.append(self._text(it["label"], self._label_size, it["text_color"], align))
        if it.get("note") is not None:
            sps.append(self._text(it["note"], self._tick_size, self._pal["muted"], align))
        return sps

    def _box_size(self, sps, card):
        w = max(sp.w for sp in sps)
        h = sum(sp.h for sp in sps)
        if card:
            w += 2 * _CARD_PAD[0]
            h += 2 * _CARD_PAD[1]
        return int(w), int(h)

    def _clamp_x(self, cx, w):
        """幅 w の札を中心 cx に置いたときの左端（キャンバスに収める）"""
        lo = self._pad
        hi = self._W - self._pad - w
        if hi < lo:
            raise ValueError(
                f"{self._fn}: 札の幅 {w}px がキャンバスに収まりません（字を短くするか改行する）")
        return int(round(min(max(cx - w / 2.0, lo), hi)))

    def _layout(self):
        """全部の要素の位置を決める（軸を y=0 とした相対の座標。上が負）"""
        ax = self._axis
        items = self._items
        # --- 段（row）の高さ ---
        rows = sorted({it["row"] for it in items if it["kind"] == "span" and it["row"] is not None})
        n_rows = (rows[-1] + 1) if rows else 0
        top_h = [0] * n_rows
        bot_h = [0] * n_rows
        for it in items:
            if it["kind"] == "span" and it["row"] is not None and it["label"] is not None:
                sp = self._text(it["label"], self._label_size, it["text_color"], "left")
                if it["side"] == "above":
                    top_h[it["row"]] = max(top_h[it["row"]], sp.h + _LANE_TEXT_GAP)
                else:
                    bot_h[it["row"]] = max(bot_h[it["row"]], sp.h + _LANE_TEXT_GAP)
        tick_bottom = 0
        if self._ticks:
            tick_bottom = _TICK_LEN + _TICK_TEXT_GAP + max(sp.h for sp in self._tick_sprites)
        y = (tick_bottom + _ZONE_GAP) if self._ticks else (_DOT_R + _ZONE_GAP)
        lanes = []
        for r in range(n_rows):
            y0 = y + top_h[r]
            lanes.append((y0, y0 + self._bar))
            y = y0 + self._bar + bot_h[r] + _LANE_GAP
        # 札の段の、軸からの最小の距離（柱の無い札, 柱のある札）。柱のある札は、柱が
        # 見える長さだけ離す（目盛りの字の下からも少し見える）
        if n_rows:
            lane_end = y - _LANE_GAP + _ZONE_GAP
            below_base = (lane_end, lane_end)
        elif self._ticks:
            below_base = (tick_bottom + _ZONE_GAP, tick_bottom + _BELOW_TICKS)
        else:
            below_base = (_BASE_BELOW, _BASE_ABOVE)

        # --- 札（文字の箱）を集める ---
        geo = [{} for _ in items]
        boxes = []
        for idx, it in enumerate(items):
            g = geo[idx]
            kind = it["kind"]
            if kind == "mark":
                g["x"] = ax.x(it["v"])
                if it["label"] is None:
                    continue
                sps = self._label_stack(it, it["card"])
                w, h = self._box_size(sps, it["card"])
                g.update(sps=sps, stem_gap=0 if it["card"] else _STEM_GAP)
                boxes.append({"idx": idx, "side": it["side"], "w": w, "h": h, "x": g["x"],
                              "stem": True, "align": "center", "fixed": None,
                              "name": it["label"]})
            elif kind == "span":
                g["xa"], g["xb"] = ax.x(it["a"]), ax.x(it["b"])
                if it["row"] is not None:
                    y0, y1 = lanes[it["row"]]
                    th = self._bar * it["thickness"]
                    cy = (y0 + y1) / 2.0
                    g["bar"] = (cy - th / 2.0, cy + th / 2.0)
                    g["radius"] = min(_BAR_RADIUS, th / 2.0)
                    continue
                th = _BAND_H * it["thickness"]
                g["bar"] = (-th / 2.0, th / 2.0)
                g["radius"] = th / 2.0
                sps = self._label_stack(it, False)
                if sps:
                    w, h = self._box_size(sps, False)
                    g["sps"] = sps
                    boxes.append({"idx": idx, "side": it["side"], "w": w, "h": h,
                                  "x": (g["xa"] + g["xb"]) / 2.0, "stem": False,
                                  "align": "center", "fixed": None,
                                  "name": it["label"] or it["note"]})
            else:   # bracket
                g["xa"], g["xb"] = ax.x(it["a"]), ax.x(it["b"])
                sps = self._label_stack(it, False)
                lw, lh = self._box_size(sps, False) if sps else (0, 0)
                cx = (g["xa"] + g["xb"]) / 2.0
                lx0 = self._clamp_x(cx, lw) if sps else cx
                bx0 = min(g["xa"] - _BRACKET_W, lx0)
                bx1 = max(g["xb"] + _BRACKET_W, lx0 + lw)
                h = _BRACKET_TICK + _BRACKET_W / 2.0 + (_BRACKET_TEXT_GAP + lh if sps else 0)
                h = int(math.ceil(h))
                g.update(sps=sps, label_x=lx0, h=h)
                boxes.append({"idx": idx, "side": it["side"], "w": bx1 - bx0, "h": h,
                              "x": cx, "stem": False, "align": "center", "fixed": (bx0, bx1),
                              "name": it["label"] or "（括弧）"})
        for b in boxes:
            self._box_x(b)
        # 互いの柱を覆い合う札の組は、片方を柱の側へ寄せる（中央に置くと、どちらを外側へ
        # 出しても内側の札の柱が外側の札を貫く）
        self._unknot(boxes)

        # --- 札の段（側ごとに、柱を覆われる札から軸に近い所へ）---
        placed = {"above": [], "below": []}       # (x0, x1, d0, d1)
        stems = {"above": [], "below": []}        # (x, d0)
        base = {"above": (_BASE_BARE, _BASE_ABOVE), "below": below_base}
        for b in self._order(boxes):
            d0 = self._place(placed, stems, base, b)
            g = geo[b["idx"]]
            if items[b["idx"]]["kind"] == "bracket":
                g["d0"] = d0
            else:
                g["box"] = (int(round(b["x0"])), d0, int(b["w"]), int(b["h"]))
        return lanes, tick_bottom, geo, placed

    def _box_x(self, b):
        """札の箱の左端・右端（align に従い、キャンバスに収める）"""
        if b["fixed"] is not None:
            b["x0"], b["x1"] = b["fixed"]
            return
        w = b["w"]
        inset = min(_ALIGN_INSET, w / 2.0)
        if b["align"] == "start":
            cx = b["x"] - inset + w / 2.0
        elif b["align"] == "end":
            cx = b["x"] + inset - w / 2.0
        else:
            cx = b["x"]
        b["x0"] = self._clamp_x(cx, w)
        b["x1"] = b["x0"] + w

    @staticmethod
    def _covers(bj, bi):
        """札 bj の箱が、札 bi の柱（同じ側）に掛かるか"""
        m = _BOX_GAP
        return (bi is not bj and bi["stem"] and bj["side"] == bi["side"]
                and bj["x0"] - m < bi["x"] < bj["x1"] + m)

    def _unknot(self, boxes):
        """互いの柱に掛かる札の組をほどく（右の札を右へ、それでも掛かれば左の札を左へ）"""
        for _ in range(2 * len(boxes)):
            pair = None
            for i, bi in enumerate(boxes):
                for bj in boxes[i + 1:]:
                    if self._covers(bi, bj) and self._covers(bj, bi):
                        pair = (bi, bj) if bi["x"] <= bj["x"] else (bj, bi)
                        break
                if pair:
                    break
            if pair is None:
                return
            left, right = pair
            if right["align"] == "center":
                right["align"] = "start"            # 右の札を右へ（柱は札の左の端の近く）
                self._box_x(right)
                if not self._covers(right, left):
                    continue
            if left["align"] == "center":
                left["align"] = "end"              # 左の札を左へ
                self._box_x(left)
                continue
            return

    def _order(self, boxes):
        """置く順: 札 j の箱が札 i の柱に掛かるなら i が先（軸に近い）。ほかは呼んだ順"""
        n = len(boxes)
        before = [set() for _ in range(n)]          # before[j] = j より先に置く札
        for i in range(n):
            for j in range(n):
                if self._covers(boxes[j], boxes[i]):
                    before[j].add(i)
        done = set()
        out = []
        while len(out) < n:
            ready = [k for k in range(n) if k not in done and before[k] <= done]
            k = ready[0] if ready else min(k for k in range(n) if k not in done)
            done.add(k)
            out.append(boxes[k])
        return out

    def _place(self, placed, stems, base, b):
        """札を置く距離 d0（軸から外へ）を決めて、placed・stems に足す"""
        side = b["side"]
        mine = placed[side]
        x0, x1, h = b["x0"], b["x1"], b["h"]
        stem_x = b["x"] if b["stem"] else None
        b0 = base[side][1 if b["stem"] else 0]
        cands = sorted({b0} | {int(math.ceil(p[3] + _BOX_GAP)) for p in mine
                               if p[3] + _BOX_GAP > b0})
        m = _BOX_GAP
        for strict in (True, False):
            for d0 in cands:
                d1 = d0 + h
                ok = True
                for (px0, px1, pd0, pd1) in mine:
                    if x0 < px1 + m and px0 < x1 + m and d0 < pd1 + m and pd0 < d1 + m:
                        ok = False          # 札どうしが重なる
                        break
                    if (strict and stem_x is not None and px0 - m < stem_x < px1 + m
                            and pd0 < d0):
                        ok = False          # 柱がほかの札を貫く
                        break
                if ok:
                    for sx, sd in stems[side]:
                        if x0 - m < sx < x1 + m and d0 < sd:
                            ok = False      # ほかの札の柱を覆う
                            break
                if ok:
                    if not strict:
                        warnings.warn(
                            f"{self._fn}: 札 {b['name']!r} の柱がほかの札を横切ります"
                            f"（side を変える・値の近い札を減らす・字を短くする）", stacklevel=4)
                    mine.append((x0, x1, d0, d1))
                    if stem_x is not None:
                        stems[side].append((stem_x, d0))
                    return d0
        raise AssertionError("札の置き場所が見つからない")   # 一番外の候補は必ず空いている

    def _resolve(self):
        """配置して、キャンバスの座標の場面（_Renderer に渡すもの）を作る"""
        fn = self._fn
        lanes, tick_bottom, geo, placed = self._layout()
        top_ext = max([_DOT_R, _TICK_LEN, _BREAK_RISE if self._axis.breaks else 0]
                      + [p[3] for p in placed["above"]]) + 2
        bot_ext = max([_DOT_R, _TICK_LEN, tick_bottom, _BREAK_RISE if self._axis.breaks else 0]
                      + [y1 for _y0, y1 in lanes] + [p[3] for p in placed["below"]]) + 2
        for it, g in zip(self._items, geo):
            if it["kind"] == "span" and it["row"] is None:
                top_ext = max(top_ext, g["bar"][1] + 2)
                bot_ext = max(bot_ext, g["bar"][1] + 2)
        avail = self._H - 2 * self._pad
        if top_ext + bot_ext > avail:
            raise ValueError(
                f"{fn}: 図が縦に収まりません（要る高さ {top_ext + bot_ext}px > "
                f"{avail}px。size を大きくする・札を減らす・label_size を下げる）")
        if self._axis_y_req is None:
            ay = int(round(self._pad + (avail - top_ext - bot_ext) / 2.0 + top_ext))
        else:
            ay = self._axis_y_req
            if ay - top_ext < 0 or ay + bot_ext > self._H:
                raise ValueError(
                    f"{fn}: axis_y={ay} では図がキャンバスからはみ出します"
                    f"（軸の上に {top_ext}px・下に {bot_ext}px 要る）")
        W, H = self._W, self._H
        ax = self._axis

        # --- 目盛り ---
        ticks = []
        tick_y = ay + _TICK_LEN + _TICK_TEXT_GAP
        n = len(self._ticks)
        for k, ((f, _s), sp) in enumerate(zip(self._ticks, self._tick_sprites)):
            x = ax.x(f)
            if self._tick_place == "between":
                if k == n - 1:
                    ticks.append((x, None, None))
                    continue
                cx = (x + ax.x(self._ticks[k + 1][0])) / 2.0
            else:
                cx = x
            lx = int(round(min(max(cx - sp.w / 2.0, 0), W - sp.w)))
            ticks.append((x, sp, (lx, tick_y)))

        # --- 要素 ---
        scene_items = []
        lane_texts = {}           # row -> [(box, 何の字)]
        lane_bars = {}            # row -> [(x0, y0, x1, y1, 要素の番号)]
        for idx, (it, g) in enumerate(zip(self._items, geo)):
            e = {"kind": it["kind"], "at": it["at"], "dur": it["dur"], "enter": it["enter"],
                 "color": it["color"], "side": it["side"]}
            sgn = -1 if it["side"] == "above" else 1
            if it["kind"] == "mark":
                e.update(x=g["x"], y=float(ay), dot=it["dot"], card=it["card"], labels=[],
                         stem=None, box=None)
                if "box" in g:
                    bx0, d0, w, h = g["box"]
                    by0 = ay - d0 - h if sgn < 0 else ay + d0
                    e["box"] = (bx0, by0, bx0 + w, by0 + h)
                    e["labels"] = self._stack_pos(g["sps"], e["box"], it["card"], sgn)
                    near = by0 + h + g["stem_gap"] if sgn < 0 else by0 - g["stem_gap"]
                    start = ay + sgn * ((_DOT_R + 3) if it["dot"] != "none" else 2)
                    if (near - start) * sgn > 2:
                        # 目盛りの字を横切る所は柱を切る（字の上に線を通さない）
                        cuts = [(ty - _STEM_GAP, ty + sp.h + _STEM_GAP)
                                for _x, sp, pos in ticks if sp is not None
                                for tx, ty in (pos,)
                                if tx - _STEM_GAP < g["x"] < tx + sp.w + _STEM_GAP]
                        e["stem"] = (g["x"], float(start), float(near), cuts)
            elif it["kind"] == "span":
                y0, y1 = g["bar"]
                e.update(xa=g["xa"], xb=g["xb"], y0=ay + y0, y1=ay + y1, radius=g["radius"],
                         row=it["row"], labels=[], notes=[], box=None)
                if it["row"] is None:
                    if "box" in g:
                        bx0, d0, w, h = g["box"]
                        by0 = ay - d0 - h if sgn < 0 else ay + d0
                        e["box"] = (bx0, by0, bx0 + w, by0 + h)
                        e["notes"] = self._stack_pos(g["sps"], e["box"], False, sgn)
                else:
                    r = it["row"]
                    lane_bars.setdefault(r, []).append((g["xa"], ay + y0, g["xb"], ay + y1, idx))
                    if it["label"] is not None:
                        sp = self._text(it["label"], self._label_size, it["text_color"], "left")
                        lx = int(round(min(max(g["xa"], self._pad), W - self._pad - sp.w)))
                        ly0, ly1 = lanes[r]
                        ly = (ay + ly0 - _LANE_TEXT_GAP - sp.h if it["side"] == "above"
                              else ay + ly1 + _LANE_TEXT_GAP)
                        e["labels"] = [(sp, lx, int(ly))]
                        lane_texts.setdefault(r, []).append(
                            ((lx, ly, lx + sp.w, ly + sp.h), it["label"], idx))
                    if it["note"] is not None:
                        sp = self._text(it["note"], self._tick_size, self._pal["muted"], "left")
                        nx = int(math.ceil(g["xb"] + _NOTE_GAP))
                        ny = int(round(ay + (y0 + y1) / 2.0 - sp.h / 2.0))
                        if nx + sp.w > W - self._pad:
                            raise ValueError(
                                f"{fn}: 段 {r} の棒の note {it['note']!r} がキャンバスの右へ"
                                f"はみ出します（size を広げる・note を短くする）")
                        e["notes"] = [(sp, nx, ny)]
                        lane_texts.setdefault(r, []).append(
                            ((nx, ny, nx + sp.w, ny + sp.h), it["note"], idx))
            else:
                line_y = ay + sgn * (g["d0"] + _BRACKET_TICK)
                near_y = ay + sgn * g["d0"]
                e.update(xa=g["xa"], xb=g["xb"], line_y=float(line_y), near_y=float(near_y),
                         labels=[])
                if g["sps"]:
                    sp = g["sps"][0]
                    ly = (line_y + _BRACKET_W / 2.0 + _BRACKET_TEXT_GAP if sgn > 0
                          else line_y - _BRACKET_W / 2.0 - _BRACKET_TEXT_GAP - sp.h)
                    e["labels"] = [(sp, int(round(g["label_x"])), int(round(ly)))]
            scene_items.append(e)

        # --- 段の字の重なり（同じ段の字どうし・字とほかの棒）---
        for r, texts in lane_texts.items():
            for i in range(len(texts)):
                for j in range(i + 1, len(texts)):
                    if _overlap(texts[i][0], texts[j][0], 2):
                        raise ValueError(
                            f"{fn}: 段 {r} の字 {texts[i][1]!r} と {texts[j][1]!r} が重なります"
                            f"（row を分けるか、字を短くする）")
                for bx0, by0, bx1, by1, idx in lane_bars.get(r, []):
                    if idx == texts[i][2]:
                        continue
                    if _overlap(texts[i][0], (bx0, by0, bx1, by1), 2):
                        raise ValueError(
                            f"{fn}: 段 {r} の字 {texts[i][1]!r} がほかの棒に掛かります"
                            f"（row を分けるか、字を短くする）")

        # --- 格子（段の帯の中だけの縦線）---
        grid = []
        if self._grid and lanes:
            segs = [(ay + y0 - 8, ay + y1 + 8) for y0, y1 in lanes]
            for f, _s in self._ticks:
                grid.append((ax.x(f), segs))
        rows = [(ay + y0, ay + y1) for y0, y1 in lanes]
        return {
            "fn": fn, "size": (W, H), "axis_y": ay, "pal": dict(self._pal),
            "x0": ax.x0, "x1": ax.x1, "arrow": self._arrow, "breaks": ax.break_xs(),
            "ticks": ticks, "grid": grid, "items": scene_items, "rows": rows,
        }

    @staticmethod
    def _stack_pos(sps, box, card, sgn):
        """札の中の字の位置 [(Sprite, x, y)]（板の無い札は中央揃え・軸に近い側から）"""
        bx0, by0, bx1, by1 = box
        out = []
        if card:
            x = bx0 + _CARD_PAD[0]
            y = by0 + _CARD_PAD[1]
            for sp in sps:
                out.append((sp, x, y))
                y += sp.h
            return out
        cx = (bx0 + bx1) / 2.0
        if sgn > 0:
            y = by0
            for sp in sps:
                out.append((sp, int(round(cx - sp.w / 2.0)), y))
                y += sp.h
        else:
            y = by1
            for sp in sps:
                y -= sp.h
                out.append((sp, int(round(cx - sp.w / 2.0)), y))
        return out

    def _params(self, duration):
        ax = self._axis
        items = []
        for it in self._items:
            d = {k: v for k, v in it.items() if k not in ("v", "a", "b", "color", "text_color")}
            for k in ("v", "a", "b"):
                if k in it:
                    d[k] = ax.key(it[k])
            d["color"] = list(it["color"])
            if it.get("label") is not None:
                d["text_color"] = list(it["text_color"])
            if it["enter"] == "cut":
                d.pop("dur")
            # 効かない値は鍵に入れない（同一出力なら同一鍵）
            no_text = it.get("label") is None and it.get("note") is None
            if no_text and (it["kind"] == "mark" or it["kind"] == "span"):
                d.pop("side")
            items.append(d)
        used = ["line", "muted"]
        if self._grid and any(it["kind"] == "span" and it["row"] is not None
                              for it in self._items):
            used.append("dim")
        if any(it["kind"] == "mark" and it["card"] for it in self._items):
            used.append("panel")
        return {
            "axis": {"kind": ax.kind, "start": ax.key(ax.lo), "end": ax.key(ax.hi),
                     "ticks": [[ax.key(f), s] for f, s in self._ticks],
                     "tick_place": self._tick_place if self._ticks else None,
                     "breaks": [[ax.key(a), ax.key(b)] for a, b in ax.breaks],
                     "arrow": self._arrow,
                     "grid": self._grid and any(it["kind"] == "span" and it["row"] is not None
                                                for it in self._items)},
            "size": [self._W, self._H], "padding": self._pad, "axis_y": self._axis_y_req,
            "style": {"bar": self._bar if any(it["kind"] == "span" and it["row"] is not None
                                              for it in self._items) else None,
                      "label_size": self._label_size, "tick_size": self._tick_size},
            "colors": {k: list(self._pal[k]) for k in used},
            "items": items,
            "duration": fk.norm(duration),
        }

    def build(self, duration=None):
        """動画 Object にする（frames() と同じ qtrle・alpha つきの .mov）。

        duration: 秒。None は最後の要素が現れ終わる秒 + 1.0 秒。要素が現れ終わる秒より
        短いと ValueError。
        戻り値の obj.figure: x（軸の値 → px の関数）・axis_y・rows（段ごとの棒の (上端, 下端)）・
        items（呼んだ順の {kind, at, end, box}。box は札の外接矩形か None）・ticks
        （[(x, 字)]）・size・duration。
        build の後に足した要素は、この Object には入らない。
        """
        fn = "timeline.build"
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
                    f"{fn}: 要素が現れ終わる秒（{end:g} 秒）が duration（{duration:g} 秒）より"
                    f"後です（duration を延ばすか at を早める）")
        scene = self._resolve()
        fps = _resolve_fps(fn, None)
        fps_frac = _fps_fraction(fps)
        n_frames = fk.n_frames_for(duration, fps_frac)
        holder = {}

        def draw(i):
            r = holder.get("r")
            if r is None:
                r = holder["r"] = _Renderer(scene)
            img = r.frame(float(Fraction(i) / fps_frac))
            if i == n_frames - 1:
                holder.pop("r", None)      # 作業領域（1080p で約 35MB の層）を手放す
            return img

        sprites = [self._sprites[k] for k in sorted(self._sprites, key=repr)]
        text = fk.text_meta(sprites, width=self._W, height=self._H) if sprites else None
        ax = axis = self._axis

        def x_of(v):
            return axis.x(axis.num("v", v))

        info = {
            "x": x_of, "axis_y": scene["axis_y"], "rows": list(scene["rows"]),
            "items": [{"kind": it["kind"], "at": it["at"],
                       "end": it["at"] + (0.0 if it["enter"] == "cut" else it["dur"]),
                       "box": e.get("box")}
                      for it, e in zip(self._items, scene["items"])],
            "ticks": [(ax.x(f), s) for f, s in self._ticks],
            "size": (self._W, self._H), "duration": duration,
        }
        return fk.build(fn, kind="timeline", ver=_TIMELINE_VER, params=self._params(duration),
                        draw=draw, n_frames=n_frames, size=(self._W, self._H), fps=fps,
                        fonts=sprites, text=text, info=info)


# --- 描画 ---

class _Renderer:
    """場面（Timeline._resolve の戻り値）から t 秒のコマを描く"""

    def __init__(self, scene):
        self.s = scene
        self._static = None
        self._tick_layer = None
        self._last_sig = None
        self._last_img = None

    # --- 動かない層 ---

    def _static_layer(self):
        if self._static is not None:
            return self._static
        s = self.s
        W, H = s["size"]
        ay = float(s["axis_y"])
        pal = s["pal"]
        dst = fk.canvas(W, H)
        x_end = s["x1"] + (_ARROW_EXT if s["arrow"] else _OVERHANG)
        # 軸（省いた区間の中は描かない）
        pts = [s["x0"] - _OVERHANG]
        for bx in s["breaks"]:
            pts += [bx - _BREAK_SEP, bx + _BREAK_SEP]
        pts.append(x_end - (_ARROW_HEAD / 2.0 if s["arrow"] else 0.0))
        for k in range(0, len(pts), 2):
            fk.polyline(dst, [(pts[k], ay), (pts[k + 1], ay)], pal["line"], _AXIS_W, cap="butt")
        if s["arrow"]:
            fk.arrow(dst, (x_end - _ARROW_HEAD - 2, ay), (x_end, ay), pal["line"], _AXIS_W,
                     head=_ARROW_HEAD)
        for x, _sp, _pos in s["ticks"]:
            fk.polyline(dst, [(x, ay - _TICK_LEN), (x, ay + _TICK_LEN)], pal["line"], _TICK_W,
                        cap="butt")
        for x, segs in s["grid"]:
            for y0, y1 in segs:
                fk.polyline(dst, [(x, y0), (x, y1)], pal["dim"], _GRID_W, cap="butt")
        dst.flags.writeable = False
        self._static = dst
        return dst

    def _tick_text(self):
        """目盛りの字の層（字のある行だけを持つ）: (y0, 層) か None"""
        if self._tick_layer is not None:
            return self._tick_layer
        s = self.s
        W, _H = s["size"]
        items = [(sp, pos) for _x, sp, pos in s["ticks"] if sp is not None]
        if not items:
            self._tick_layer = (0, None)
            return self._tick_layer
        y0 = min(pos[1] for _sp, pos in items)
        y1 = max(pos[1] + sp.h for sp, pos in items)
        layer = fk.canvas(W, y1 - y0)
        for sp, (x, y) in items:
            fk.over(layer, sp, x, y - y0)
        layer.flags.writeable = False
        self._tick_layer = (y0, layer)
        return self._tick_layer

    # --- 進み具合 ---

    @staticmethod
    def _progress(e, t):
        if e["enter"] == "cut":
            return 1.0 if t >= e["at"] - 1e-9 else 0.0
        return _clamp01((t - e["at"]) / e["dur"])

    def frame(self, t):
        sig = tuple(round(self._progress(e, t), _SIG_DIGITS) for e in self.s["items"])
        if sig == self._last_sig:
            return self._last_img
        img = fk.to_rgba8(self._draw(sig))
        img.flags.writeable = False
        self._last_sig, self._last_img = sig, img
        return img

    # --- 描く ---

    @staticmethod
    def _alpha(rgba, a):
        r, g, b, al = rgba
        return (r, g, b, al * _clamp01(a))

    def _draw(self, ps):
        s = self.s
        pal = s["pal"]
        dst = self._static_layer().copy()
        items = list(zip(s["items"], ps))
        # 1. 帯と棒
        for e, p in items:
            if e["kind"] != "span" or p <= 0:
                continue
            if e["enter"] == "grow":
                xb = e["xa"] + (e["xb"] - e["xa"]) * _ease_io(p)
                a = 1.0
            else:
                xb, a = e["xb"], _ease_out(p)
            if xb - e["xa"] < 0.5:
                continue
            fk.rect(dst, (e["xa"], e["y0"], xb, e["y1"]), self._alpha(e["color"], a),
                    radius=e["radius"])
        # 2. 省く印（隙間を透明に抜いて、斜めの2本線）
        self._breaks(dst)
        # 3. 柱と括弧
        for e, p in items:
            if p <= 0:
                continue
            if e["kind"] == "mark" and e["stem"] is not None:
                x, y0, y1, cuts = e["stem"]
                if e["enter"] == "slide":
                    g, a = 1.0, _ease_out(_seg(p, 0.0, 0.7))
                elif e["enter"] == "cut":
                    g, a = 1.0, 1.0
                else:
                    g, a = _ease_io(_seg(p, 0.15, 0.65)), 1.0
                if g > 0 and abs(y1 - y0) * g > 0.5:
                    for ya, yb in _stem_pieces(y0, y0 + (y1 - y0) * g, cuts):
                        fk.polyline(dst, [(x, ya), (x, yb)], self._alpha(pal["line"], a),
                                    _STEM_W, cap="butt")
            elif e["kind"] == "bracket":
                path = [(e["xa"], e["near_y"]), (e["xa"], e["line_y"]),
                        (e["xb"], e["line_y"]), (e["xb"], e["near_y"])]
                if e["enter"] == "grow":
                    total = 2 * abs(e["line_y"] - e["near_y"]) + (e["xb"] - e["xa"])
                    path = _cut(path, total * _ease_io(_seg(p, 0.0, 0.7)))
                    a = 1.0
                else:
                    a = _ease_out(p)
                if len(path) >= 2:
                    fk.polyline(dst, path, self._alpha(e["color"], a), _BRACKET_W, cap="butt")
        # 4. 点
        for e, p in items:
            if e["kind"] != "mark" or p <= 0 or e["dot"] == "none":
                continue
            a = _ease_out(_seg(p, 0.0, 0.7 if e["enter"] == "slide" else 0.35))
            if e["dot"] == "fill":
                fk.circle(dst, (e["x"], e["y"]), _DOT_R, self._alpha(e["color"], a))
            else:
                # 白抜き: 中を下地の色で塗ってから輪（軸と目盛りの線を透かさない）
                fk.circle(dst, (e["x"], e["y"]), _DOT_R, self._alpha(pal["panel"], a))
                fk.circle(dst, (e["x"], e["y"]), _DOT_R - _RING_W / 2.0,
                          self._alpha(e["color"], a), width=_RING_W)
        # 5. 目盛りの字
        ty0, layer = self._tick_text()
        if layer is not None:
            fk.over(dst, layer, 0, ty0)
        # 6. 札
        for e, p in items:
            if p <= 0:
                continue
            if e["kind"] == "mark":
                self._mark_label(dst, e, p, pal)
            elif e["kind"] == "span":
                if e["enter"] == "grow":
                    la = _ease_out(_seg(p, 0.0, 0.3))
                    na = _ease_out(_seg(p, 0.75, 1.0))
                else:
                    la = na = _ease_out(p)
                if e["row"] is None:
                    la = na             # 軸の上の帯の札は伸び終わりに
                for sp, x, y in e["labels"]:
                    fk.blit(dst, sp, x, y, alpha=la)
                for sp, x, y in e["notes"]:
                    fk.blit(dst, sp, x, y, alpha=na)
            else:
                a = (_ease_out(_seg(p, 0.6, 1.0)) if e["enter"] == "grow" else _ease_out(p))
                for sp, x, y in e["labels"]:
                    fk.blit(dst, sp, x, y, alpha=a)
        return dst

    def _mark_label(self, dst, e, p, pal):
        if not e["labels"]:
            return
        enter = e["enter"]
        dx = dy = 0.0
        if enter == "slide":
            a = _ease_out(_seg(p, 0.0, 0.7))
            dx = _SLIDE * (1.0 - _ease_out(p))
        elif enter == "cut":
            a = 1.0
        else:
            q = _seg(p, 0.45, 1.0)
            a = _ease_out(q)
            if enter == "rise":
                sgn = -1 if e["side"] == "above" else 1
                dy = -sgn * _RISE * (1.0 - _ease_out(q))
        if a <= 0:
            return
        if e["card"]:
            bx0, by0, bx1, by1 = e["box"]
            box = (bx0 + dx, by0 + dy, bx1 + dx, by1 + dy)
            fk.rect(dst, box, self._alpha(pal["panel"], a), radius=_CARD_R)
            hw = _CARD_EDGE / 2.0
            fk.rect(dst, (box[0] + hw, box[1] + hw, box[2] - hw, box[3] - hw),
                    self._alpha(e["color"], a), width=_CARD_EDGE, radius=_CARD_R - hw)
        for sp, x, y in e["labels"]:
            fk.blit(dst, sp, x + dx, y + dy, alpha=a)

    def _breaks(self, dst):
        s = self.s
        if not s["breaks"]:
            return
        np = fk.need("timeline").np
        ay = float(s["axis_y"])
        H, W = dst.shape[:2]
        line = s["pal"]["line"]
        for bx in s["breaks"]:
            # 2本の斜めの線の間を透明に抜く（軸と、隙間を跨ぐ帯を切る）
            x0 = int(math.floor(bx - _BREAK_SEP - _BREAK_SLANT - 2))
            y0 = int(math.floor(ay - _BREAK_RISE - 2))
            x1 = int(math.ceil(bx + _BREAK_SEP + _BREAK_SLANT + 2))
            y1 = int(math.ceil(ay + _BREAK_RISE + 2))
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
            if x0 >= x1 or y0 >= y1:
                continue
            m = fk.canvas(x1 - x0, y1 - y0)
            quad = [(bx - _BREAK_SEP - _BREAK_SLANT, ay + _BREAK_RISE),
                    (bx - _BREAK_SEP + _BREAK_SLANT, ay - _BREAK_RISE),
                    (bx + _BREAK_SEP + _BREAK_SLANT, ay - _BREAK_RISE),
                    (bx + _BREAK_SEP - _BREAK_SLANT, ay + _BREAK_RISE)]
            fk.polygon(m, [(qx - x0, qy - y0) for qx, qy in quad], (255, 255, 255, 255))
            dst[y0:y1, x0:x1] *= (np.float32(1.0) - m[..., 3:4])
            for sx in (-_BREAK_SEP, _BREAK_SEP):
                fk.polyline(dst, [(bx + sx - _BREAK_SLANT, ay + _BREAK_RISE),
                                  (bx + sx + _BREAK_SLANT, ay - _BREAK_RISE)],
                            line, _TICK_W, cap="butt")


def _stem_pieces(y0, y1, cuts):
    """柱 y0→y1 から、cuts（[(上, 下)]）の区間を除いた線分の並び"""
    lo, hi = min(y0, y1), max(y0, y1)
    pieces = [(lo, hi)]
    for c0, c1 in cuts:
        nxt = []
        for a, b in pieces:
            if c1 <= a or c0 >= b:
                nxt.append((a, b))
                continue
            if c0 > a:
                nxt.append((a, c0))
            if c1 < b:
                nxt.append((c1, b))
        pieces = nxt
    return [(a, b) for a, b in pieces if b - a > 0.5]


def _cut(pts, length):
    """折れ線を始点から弧長 length で切る（0 なら点1つ）"""
    out = [pts[0]]
    acc = 0.0
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        if acc + seg >= length:
            t = 0.0 if seg <= 0 else (length - acc) / seg
            if t > 0:
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            return out
        out.append(b)
        acc += seg
    return out


# --- 入口 ---

def timeline(start, end, *, ticks=None, tick_format=None, tick_place="tick", breaks=(),
             arrow=False, grid=False, size=None, padding=40, axis_y=None, bar=40,
             label_size=44, tick_size=32, font=None, weight=None, colors=None):
    """年表・時刻の帯・期間の横棒の図（数値か日時の軸に、印・期間・括弧を並べる）。

    start / end: 軸の両端。どちらも数値か、どちらも日時（datetime・date・ISO の文字列
      '2026-10-23 19:30'）。
    ticks: 目盛り。None は自動。数値の軸は間隔（1 など）、日時の軸は単位
      （'year' / 'month' / 'day' / 'hour' / 'minute'）か (単位, 数)（('hour', 3) など）。
      値のリスト・(値, 字) のリストも可（[] で目盛りなし）。
    tick_format: 目盛りの字の書式（str.format）。数値の軸は {v}（整数の値は整数で）、
      日時の軸は {year} {month} {day} {hour} {minute} {second} {wd}（曜日 '月'〜'日'）。
      例 '{v}:00'・'{v}年'・'{month}/{day}'・'{hour}:{minute:02d}'。
    tick_place: 'tick'（字を目盛りの下に）/ 'between'（隣の目盛りとの間の中央。日の名前など）。
    breaks: 省く区間 [(a, b), …]（40px の隙間と斜めの2本線になる）。
    arrow: 軸の右端を矢じりにする。grid: 段（row）の帯の中に目盛りの縦線を引く。
    size: キャンバス (幅, 高さ) px（省略時は Project の解像度）。padding: 余白 px。
    axis_y: 軸の高さ px（省略時は全体を縦の中央に置く）。bar: 段の横棒の太さ px。
    label_size / tick_size: 札の字・目盛りと note の字の大きさ px。
    font / weight: フォント。colors: 図の色の差し替え {名前: 色}
      （fg / accent / muted / line / dim / panel）。
    戻り値は Timeline（mark / span / bracket を足してから build()）。
    """
    return Timeline(start, end, ticks=ticks, tick_format=tick_format, tick_place=tick_place,
                    breaks=breaks, arrow=arrow, grid=grid, size=size, padding=padding,
                    axis_y=axis_y, bar=bar, label_size=label_size, tick_size=tick_size,
                    font=font, weight=weight, colors=colors)
