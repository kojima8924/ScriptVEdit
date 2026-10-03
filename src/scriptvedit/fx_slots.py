# -*- coding: utf-8 -*-
"""slots(): 番号つきの箱の列・読みにいく針・上限の線（framekit.build の上に作る動く図）

配列・バッファ・設定ファイルの項目のような「番号つきの箱の列」を描き、
そこへ塊が流れ込む・上限を超えてあふれる・針が範囲外を読みにいく・
箱どうしを比べて入れ替える、を時刻つきの出来事として並べる。

    s = slots()
    s.row("shelf", 200, label="項目の上限", capacity=200, elide=(6, 3))
    s.fill(1.0, "shelf", 400, dur=2.5)     # 倍の 400 個が流れ込み、200 を超えた分があふれる
    s.halt(3.8)                            # 全体を 0.45 の不透明度へ（止まった印）
    fig = s.build()                        # → 動画 Object（obj.figure で寸法・位置が引ける）
    fig.time() <= move(x=0.5, y=0.5, anchor="center")

仕組み:
- 生成は framekit.build（stillseq._frames_object）。キャッシュ（__cache__/artifacts/
  frames/<鍵>.mov）・原子的な書き込み・dry_run の契約（draw を呼ばない）・
  time() で伸ばしたときの最後のコマの保持は frames() のものを受け継ぐ。
- 鍵は framekit の形: ['fig', 'slots', _SLOTS_VER, framekit の版, params, フォントの
  内容指紋, Pillow の版]。params は行の定義（見える番号の値だけ）・正規化した出来事の列・
  寸法・色・size・尺。生パスは混ぜない。seed はあふれ（spill）を描くときだけ入る
  （同一出力なら同一鍵）。描き方を変えたら _SLOTS_VER を上げる（金型テストが捕まえる）。
- 動かない層（ラベル・箱の枠・番号・上限の線・「…」）は framekit.layer_cache で1回だけ
  描く。毎コマは「前のコマで描いた所（32px のタイル）を動かない層から戻す → 動く物を
  描く → 描いた所だけ straight alpha へ写す」で、全面の写しと変換をしない。
  何も動いていないコマは前のコマをそのまま返す。
- 形は 4 倍で描いて面積平均で縮める（ギザギザが出ない。位置は 1/8 px 刻みで作り置く）。
  文字は Pillow（FreeType）で描き、動く文字だけ 1/4 px 単位の双一次補間でずらす
  （Pillow は小数の位置を丸めるので、そのままだと 1px ずつ跳ねる）。
- 動きは ease_in_out_cubic（put / set / swap / compare / link / halt）。針は等速。
  流れ込む塊だけは ease_out_cubic（同じ入口から続けて出る塊が入口で重ならないよう、
  出だしを速くする）。あふれる塊は線へ等速でぶつかり、跳ね返って放物線で落ちる。
- あふれの塊は線に着く間隔を _SPILL_GAP 秒以上にする（足りなければあふれの時間を延ばし、
  それでも入らない分は描かない）。重なっても縁取りで 1 つずつ見分けられる。あふれたら
  「上限 N」の右に札を出す（row の overflow_label。既定は入った数の合計「400件」）。
  あふれは 1 個ずつのリストを作らず等間隔の式で数える（count が 1,000 万でも build は約 0.1 秒）。
- compare で持ち上げた箱は、次の出来事の直前（間が短ければ次の出来事と重ねて）下ろす。
  重なった put / set / swap / fill は残りの持ち上げを持ち越して描く（字が箱から外れない）。
- 文字の申告は framekit.text_meta（役ごとの実際の大きさ・縁で label を作って渡す）。

重さの実測（Windows 11・Python 3.13・numpy 2.0・opencv 4.13。1 コマの描画）:
  - 2-24 型（200 箱を畳んだ 1 行・fill(400)・あふれ・halt、1120x290）:
    平均 2.3ms・最大 6ms。size=(1920, 1080) でも平均 2.9ms・最大 15ms（dim の間は全面を変換）
  - 21 個と 20 個の 2 行（put・link・範囲外の read、1892x428）: 平均 1.1ms・最大 5.4ms
  - 3 箱の to_str・compare・swap（cell=96、590x304）: 平均 1.5ms・最大 6.8ms
  - fill(10**7)（100,000 箱を畳んだ行）: build 約 0.1 秒・1 コマ平均 2.9ms・最大 7ms
  - 最初のコマだけ動かない層を描くので 8〜37ms。何も動いていないコマは 0.1ms 未満。
  1 秒ぶん（30 コマ）の生成は ffmpeg（qtrle）込みで 0.2〜0.3 秒。

numpy・opencv-python・Pillow を使う（optional 依存。framekit.need で遅延 import）。
"""

import bisect
import math
import os
import random
import re
import string

import scriptvedit.framekit as fk
from scriptvedit.context import current_project
from scriptvedit.stillseq import _resolve_fps
from scriptvedit.state import _suggest_hint
from scriptvedit.textimage import _load_font, _missing_glyphs
from scriptvedit.validate import _require_choice, _require_number


# --- 定数 ---

# 描画の版（絵の描き方・配置を変えたら上げる → 鍵が変わり全部作り直す）
#   2: compare の持ち上げを次の出来事へ持ち越す・字の大きさを見える字だけで決める・
#      札が左のラベルに掛からない・あふれの塊を散らす・区画を ghost の外へ・
#      矢印の札と複数行のラベルの高さを確保・番号の既定 32px ほか（レビューの修正）
#   3: あふれの札を row(overflow_label=) で選ぶ（既定は入った数の合計「400件」。以前は
#      描かなかった数だけの「+N」で、400 を入れて「+190」と出た）
#   4: {limit} だけの書式（例 '上限{limit}を超過'）の札を、最初のあふれが線に着く秒から出す
#      （以前は undrawn だけの書式と同じ扱いで、描かない塊が無いと出なかった）
_SLOTS_VER = "4"

_MAX_N = 100000          # 1 行の箱の数の上限
_MAX_PLAIN = 64          # elide なしで描ける箱の数の上限
_MAX_GHOST = 64
_MAX_COLUMNS = 400       # 全行を合わせた列の数の上限（畳んだ後）
_SS = 4                  # 形を描くときの倍率（4 倍で描いて面積平均で縮める）
_SUBPX = 4               # 動く文字の位置の刻み（1/4 px）

_BOX_STROKE = 3          # 箱の枠の太さ（px。size で縮めるときは倍率が掛かる）
_LINE_W = 4              # 上限の線の太さ
_DIM_ALPHA = 0.45        # halt(style='dim') の不透明度
_RELEASE = 0.25          # compare の持ち上げを下ろす時間（秒）
_MARK_FADE = 0.12        # compare の枠と不等号が消える時間（下ろし始めから。字が動き出す前に消す）
_SPILL_GAP = 0.12        # 描くあふれの塊どうしの最小の間隔（秒。これより詰まる分は「+N」に回す）
_LABEL_MARGIN = 8.0      # ラベルと、札・入口の塊との最小の隙間（設計 px）
_VALUE_FLOOR = 32.0      # 箱の中の字を揃えて縮める下限・札が字を縮める下限（p.audit() の推奨の
                         # 下限 = 1080p で 32px。これ未満に縮む字は、その字だけ縮める）

# 色の表（framekit の PALETTE と同じ名前は同じ既定値。fill / pointer / card / halo / bg は
# slots だけの名前）
_PALETTE = {
    "bg": None,                      # 背景（None は透明）
    "fg": fk.PALETTE["fg"],          # 箱の枠・中の字・ラベル・矢印
    "muted": fk.PALETTE["muted"],    # 番号・「…」・件数
    "dim": fk.PALETTE["dim"],        # ghost の破線・範囲外の区画の斜線
    "line": fk.PALETTE["line"],      # 上限の線と「上限 N」
    "accent": fk.PALETTE["accent"],  # 上限超え・範囲外・比べた字の枠
    "fill": "#8fa3bf",               # 流れ込む塊
    "pointer": fk.PALETTE["fg"],     # 読みにいく針
    "card": fk.PALETTE["panel"],     # put の札・swap で動く字の地
    "halo": "#000000",               # 字の縁（None で無し）
}
_COLOR_KEYS = tuple(_PALETTE)

_ORDERS = ("left", "right")
_SOURCES = ("left", "right")
_MARK_STYLES = ("frame", "fill")
_COMPARE_BY = ("str", "num")
_HALT_STYLES = ("dim", "freeze")

# あふれの札（row の overflow_label）: 名前 → 書式。書式の名前は total（入った数の合計）・
# over（上限を超えた数）・undrawn（描かなかった塊の数）・limit（上限）。
# 描かなかった数だけの「+N」は、400 を入れても「+190」と出て数として誤解された
# （見本の場面）。既定は入った数の合計（「上限 200」の右に「400件」）
_OVERFLOW_LABELS = {"total": "{total:,}件", "over": "+{over:,}", "undrawn": "+{undrawn:,}"}
_OVERFLOW_FIELDS = ("total", "over", "undrawn", "limit")

# 等幅フォントの候補（数字と ASCII の字に使う）。無ければ font を使う
_MONO_CANDIDATES = (
    "C:/Windows/Fonts/consola.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
)

# to_str の引用符と字の墨の間（字の大きさに対する比）。compare の字の枠（横の余白 0.07）が
# 引用符に掛からない幅
_QUOTE_GAP = 0.13

_NUMERIC_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


# --- 小道具 ---

def _clamp01(u):
    return 0.0 if u <= 0.0 else (1.0 if u >= 1.0 else u)


def _ease(u):
    """ease_in_out_cubic"""
    u = _clamp01(u)
    return 4.0 * u * u * u if u < 0.5 else 1.0 - (2.0 - 2.0 * u) ** 3 / 2.0


def _ease_out(u):
    """ease_out_cubic"""
    u = _clamp01(u)
    return 1.0 - (1.0 - u) ** 3


def _prog(t, t0, dur):
    """t0 から dur 秒の進み具合 0..1（dur=0 は t0 で 0→1 に切り替わる）"""
    if dur <= 0:
        return 1.0 if t >= t0 else 0.0
    return _clamp01((t - t0) / dur)


def _is_numeric(text):
    return bool(text) and _NUMERIC_RE.match(text) is not None


def _is_ascii(text):
    return all(ord(c) < 128 for c in text)


def _even_ceil(v):
    n = int(math.ceil(v - 1e-9))
    return n + (n % 2)


def _check_int(fn, name, value, lo=None, hi=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{fn}: {name} は整数で指定してください: {value!r}")
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        rng = f"{lo if lo is not None else ''}〜{hi if hi is not None else ''}"
        raise ValueError(f"{fn}: {name} は {rng} の範囲で指定してください: {value}")
    return value


def _check_time(fn, t):
    _require_number(fn, "t（Object の先頭からの秒）", t, 0, None)
    return float(t)


def _check_dur(fn, dur):
    _require_number(fn, "dur", dur, 0, 600)
    return float(dur)


class _Overflow:
    """1 回の fill のあふれ（上限を超えた count − 上限 個）の時刻表を、要素を持たずに表す。

    k 番目（0 始まり）のあふれが線に着く時刻は start + k*step（等間隔）。描く塊は
    k = j*q（j = 0..m-1。最初のあふれは必ず描く）の m 個で、残りは「+N」で数える。
    count が 1,000 万でもリストを作らない（数え上げは式で出す）。
    """

    __slots__ = ("start", "step", "n", "m", "q")

    def __init__(self, start, step, n, m, q):
        self.start, self.step = float(start), float(step)
        self.n, self.m, self.q = int(n), int(m), max(1, int(q))

    def at(self, k):
        return self.start + k * self.step

    @property
    def last(self):
        return self.at(self.n - 1)

    def arrived(self, t):
        """t までに線に着いた数"""
        if t < self.start:
            return 0
        if self.step <= 0:
            return self.n
        return min(self.n, int(math.floor((t - self.start) / self.step + 1e-9)) + 1)

    def drawn_in(self, k):
        """先頭 k 個のうち描く塊の数（j*q < k となる j の数）"""
        return min(self.m, (k - 1) // self.q + 1) if k > 0 else 0

    def is_drawn(self, k):
        return self.drawn_in(k + 1) > self.drawn_in(k)

    def drawn_indices(self):
        return [j * self.q for j in range(self.m)]

    def plus(self, t):
        """t までに着いた「+N」の N（描かない塊の数）"""
        k = self.arrived(t)
        return k - self.drawn_in(k)

    def plus_total(self):
        return self.n - self.m

    def plus_times(self):
        """最初と最後の「+N」が増える時刻（描かない塊が無ければ None）"""
        if self.plus_total() <= 0:
            return None
        k = 0
        while self.is_drawn(k):
            k += 1
        first = self.at(k)
        k = self.n - 1
        while self.is_drawn(k):
            k -= 1
        return first, self.at(k)

    def last_hit_le(self, t):
        """t 以前で最後に線に着いた時刻（無ければ None）"""
        k = self.arrived(t)
        return self.at(k - 1) if k > 0 else None


def _overflow_format(fn, v):
    """overflow_label を書式の文字列にする（None は札を出さない）"""
    if v is None:
        return None
    names = ", ".join(repr(k) for k in _OVERFLOW_LABELS)
    if not isinstance(v, str) or not v.strip():
        raise ValueError(
            f"{fn}: overflow_label は {names} / None か、{{total}} などを含む書式の文字列で"
            f"指定してください: {v!r}")
    if v in _OVERFLOW_LABELS:
        return _OVERFLOW_LABELS[v]
    if "\n" in v or "\r" in v:
        raise ValueError(f"{fn}: overflow_label は1行にしてください: {v!r}")
    if re.fullmatch(r"\w+", v):
        raise ValueError(
            f"{fn}: overflow_label={v!r} は知らない名前です（{names} / None か、"
            f"{{total}} などを含む書式）{_suggest_hint(v, list(_OVERFLOW_LABELS))}")
    try:
        fields = [f for _lit, f, _spec, _conv in string.Formatter().parse(v) if f is not None]
        bad = [f for f in fields if f not in _OVERFLOW_FIELDS]
        if not bad:
            v.format(total=1, over=1, undrawn=1, limit=1)
    except (ValueError, IndexError, KeyError) as e:
        raise ValueError(f"{fn}: overflow_label の書式が不正です: {v!r}（{e}）") from None
    if bad or not fields:
        raise ValueError(
            f"{fn}: overflow_label の書式には {{total}} / {{over}} / {{undrawn}} / {{limit}} の"
            f"どれかを入れてください（ほかの名前は使えません）: {v!r}")
    return v


def _value_text(fn, v, where):
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, str)):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError(f"{fn}: {where} に NaN/Infinity は使えません: {v!r}")
        return repr(v)
    raise ValueError(f"{fn}: {where} は文字列か数値で指定してください: {v!r}")


# --- 公開: ビルダー ---

class Slots:
    """slots() が返すビルダー（行と出来事を積んで build() で動画 Object にする）。

    時刻 t はすべて「できた Object の先頭からの秒」。メソッドは self を返すので
    つないで書ける。build() までは何も描かない。
    """

    def __init__(self, *, cell, gap, row_gap, padding, size, font, mono_font, weight,
                 label_size, value_size, index_size, colors, seed):
        fn = "slots"
        _require_number(fn, "cell", cell, 16, 600)
        _require_number(fn, "gap", gap, 0, 600)
        _require_number(fn, "row_gap", row_gap, 0, 2000)
        _require_number(fn, "padding", padding, 0, 1000)
        _require_number(fn, "label_size", label_size, 1, 400)
        _require_number(fn, "index_size", index_size, 1, 400)
        if value_size is not None:
            _require_number(fn, "value_size", value_size, 1, 400)
        if size is not None:
            if (not isinstance(size, (tuple, list)) or len(size) != 2
                    or any(isinstance(v, bool) or not isinstance(v, int) or v <= 0
                           for v in size)):
                raise ValueError(
                    f"{fn}: size は (幅, 高さ) の正の整数で指定してください: {size!r}")
            if size[0] > 8192 or size[1] > 8192:
                raise ValueError(f"{fn}: size が大きすぎます（上限 8192px）: {size!r}")
            size = (int(size[0]), int(size[1]))
        _check_int(fn, "seed", seed)
        if colors is not None and not isinstance(colors, dict):
            raise ValueError(
                f"{fn}: colors は {{'accent': '#ff0000', ...}} の dict で指定してください: "
                f"{colors!r}")
        palette = dict(_PALETTE)
        for k, v in (colors or {}).items():
            if k not in _COLOR_KEYS:
                raise ValueError(
                    f"{fn}: colors の未知のキー '{k}'（有効: {', '.join(_COLOR_KEYS)}）"
                    f"{_suggest_hint(k, _COLOR_KEYS)}")
            if v is None and k not in ("bg", "halo"):
                raise ValueError(f"{fn}: colors['{k}'] に None は使えません（bg / halo だけ）")
            palette[k] = v
        fk.need(fn)
        self._rgba = {k: (None if v is None else fk.color(fn, v, {}))
                      for k, v in palette.items()}
        if weight is not None and not isinstance(weight, str):
            _require_number(fn, "weight", weight, 1, 2000)
        if font is not None and not isinstance(font, str):
            raise ValueError(f"{fn}: font はフォントファイルのパスで指定してください: {font!r}")
        if mono_font is not None and not isinstance(mono_font, str):
            raise ValueError(
                f"{fn}: mono_font はフォントファイルのパスで指定してください: {mono_font!r}")
        self._opts = {
            "cell": float(cell), "gap": float(gap), "row_gap": float(row_gap),
            "padding": float(padding), "size": size,
            "label_size": float(label_size), "index_size": float(index_size),
            "value_size": float(value_size if value_size is not None else 0.5 * cell),
            "seed": seed,
        }
        self._font = font
        self._mono_font = mono_font
        self._weight = weight
        self._rows = []
        self._row_by_name = {}
        self._events = []
        self._built = False

    # --- 行 ---

    def row(self, name, n, *, label=None, values=None, index=True, index_base=1,
            capacity=None, elide=None, ghost=(), offset=0, overflow_label="total"):
        """箱の行を足す（呼んだ順に上から並ぶ）。

        name: 行の名前（出来事から参照する）
        n: 箱の数（1〜100,000。elide なしは 64 まで）
        label: 左に出す文字（"\\n" で改行）
        values: 箱の中の文字のリスト（長さ n。None は空）
        index: 箱の下に番号を出すか
        index_base: 最初の箱の番号（既定 1）
        capacity: capacity 番目の箱の右に上限の線を引き、上に「上限 {capacity}」を出す
        elide: (先頭, 末尾) の箱だけ見せて間を「…」に畳む
        ghost: 実在しない番号（点線の箱で描く。例 [21]）
        offset: 行の左端を cell 単位でずらす（行どうしの番号を縦に揃える）
        overflow_label: fill があふれたときの札（「上限 N」の右。線の外にも箱があれば
            番号の帯の下）。'total'（既定。入った数の合計「400件」）/ 'over'（上限を
            超えた数「+200」）/ 'undrawn'（描かなかった塊の数「+190」）/ None（出さない）か、
            書式の文字列（名前は total・over・undrawn・limit。例 '{total:,}件（上限{limit}）'）。
            数はあふれが線に着くたびに増える。数として {undrawn} だけを使う書式（'undrawn'・
            '+{undrawn}（上限{limit}）' など）は描かない塊があるときだけ出す。{limit} だけの書式は
            最初のあふれから出す
        """
        fn = "slots.row"
        self._check_open(fn)
        if not isinstance(name, str) or not name:
            raise ValueError(f"{fn}: name は空でない文字列で指定してください: {name!r}")
        if name in self._row_by_name:
            raise ValueError(f"{fn}: 行 '{name}' は既にあります")
        _check_int(fn, "n", n, 1, _MAX_N)
        if label is not None and not isinstance(label, str):
            raise ValueError(f"{fn}: label は文字列で指定してください: {label!r}")
        if not isinstance(index, bool):
            raise ValueError(f"{fn}: index は True / False で指定してください: {index!r}")
        _check_int(fn, "index_base", index_base, -10**9, 10**9)
        if capacity is not None:
            _check_int(fn, "capacity", capacity, 1, n)
        if elide is not None:
            if (not isinstance(elide, (tuple, list)) or len(elide) != 2):
                raise ValueError(
                    f"{fn}: elide は (先頭の数, 末尾の数) で指定してください: {elide!r}")
            _check_int(fn, "elide の先頭", elide[0], 0, _MAX_PLAIN)
            _check_int(fn, "elide の末尾", elide[1], 0, _MAX_PLAIN)
            if elide[0] + elide[1] < 1:
                raise ValueError(f"{fn}: elide は先頭か末尾に 1 以上を指定してください: {elide!r}")
            elide = (int(elide[0]), int(elide[1]))
        elif n > _MAX_PLAIN:
            raise ValueError(
                f"{fn}: 行 '{name}' の箱が {n} 個あります。elide なしで描けるのは "
                f"{_MAX_PLAIN} 個までです（例: elide=(6, 3) で先頭 6・末尾 3 だけ見せる）")
        if values is not None:
            if not isinstance(values, (list, tuple)):
                raise ValueError(f"{fn}: values はリストで指定してください: {values!r}")
            if len(values) != n:
                raise ValueError(
                    f"{fn}: values の長さ（{len(values)}）が n（{n}）と合いません")
            texts = [_value_text(fn, v, f"values[{k}]") for k, v in enumerate(values)]
        else:
            texts = [None] * n
        try:
            ghost_list = list(ghost)
        except TypeError:
            raise ValueError(f"{fn}: ghost は番号のリストで指定してください: {ghost!r}") from None
        if len(ghost_list) > _MAX_GHOST:
            raise ValueError(f"{fn}: ghost は {_MAX_GHOST} 個までです")
        ghosts = set()
        for g in ghost_list:
            _check_int(fn, "ghost の番号", g, -10**9, 10**9)
            p = g - index_base
            if 0 <= p < n:
                raise ValueError(
                    f"{fn}: ghost={g} は実在する番号です（{index_base}〜{index_base + n - 1}）。"
                    f"ghost には実在しない番号を指定してください")
            ghosts.add(p)
        _check_int(fn, "offset", offset, -10000, 10000)
        count_fmt = _overflow_format(fn, overflow_label)
        r = {"name": name, "n": n, "label": label, "texts": texts, "index": index,
             "base": index_base, "capacity": capacity, "elide": elide,
             "ghosts": sorted(ghosts), "offset": offset, "overflow_label": count_fmt}
        self._rows.append(r)
        self._row_by_name[name] = r
        return self

    # --- 出来事 ---

    def fill(self, t, row, count, *, dur=1.0, order="left", source="right", spill=True,
             spill_visible=24):
        """角丸の塊が source 側から流れ込み、order の側から詰まる（上限を超えた分はあふれる）。

        あふれは spill_visible 個まで描く。描く塊どうしが線に着く間隔は _SPILL_GAP 秒以上に
        保ち（あふれの時間を fill の 65% まで延ばす）、それでも入らない分は描かない。
        あふれたら札を出す（何を数えるかは row の overflow_label。既定は入った数の合計）。
        """
        fn = "slots.fill"
        r = self._event_row(fn, row)
        _check_int(fn, "count", count, 1, 10**7)
        _require_choice(fn, "order", order, _ORDERS)
        _require_choice(fn, "source", source, _SOURCES)
        if not isinstance(spill, bool):
            raise ValueError(f"{fn}: spill は True / False で指定してください: {spill!r}")
        _check_int(fn, "spill_visible", spill_visible, 0, 200)
        self._add(fn, "fill", t, dur, row=r["name"], count=count, order=order,
                  source=source, spill=spill, spill_visible=spill_visible)
        return self

    def read(self, t, row, index, *, dur=0.3):
        """三角の針が index まで等速で歩く（範囲外なら行の外の区画「?」へ出て accent になる。
        ghost の番号はその点線の箱へ。区画は一番外の ghost のさらに外に置く）。"""
        fn = "slots.read"
        r = self._event_row(fn, row)
        p = self._pos(fn, r, index, allow_out=True)
        self._add(fn, "read", t, dur, row=r["name"], p=p)
        return self

    def link(self, t, a, b, *, dur=0.5, label=None):
        """(行, 番号) から (行, 番号) へ矢印が伸びる（行き先が範囲外か ghost なら先が accent）。"""
        fn = "slots.link"
        ends = []
        for name, end in (("a", a), ("b", b)):
            if not isinstance(end, (tuple, list)) or len(end) != 2:
                raise ValueError(f"{fn}: {name} は (行の名前, 番号) で指定してください: {end!r}")
            r = self._event_row(fn, end[0])
            ends.append((r["name"], self._pos(fn, r, end[1], allow_out=True)))
        if ends[0] == ends[1]:
            raise ValueError(f"{fn}: 始点と終点が同じ箱です: {a!r}")
        if label is not None and not isinstance(label, str):
            raise ValueError(f"{fn}: label は文字列で指定してください: {label!r}")
        self._add(fn, "link", t, dur, a=list(ends[0]), b=list(ends[1]), label=label)
        return self

    def put(self, t, row, index, text, *, dur=0.4):
        """札が上から降りて箱に収まる。"""
        fn = "slots.put"
        r = self._event_row(fn, row)
        p = self._pos(fn, r, index)
        text = _value_text(fn, text, "text")
        if not text:
            raise ValueError(f"{fn}: text は空でない文字列で指定してください")
        self._add(fn, "put", t, dur, row=r["name"], p=p, text=text)
        return self

    def set(self, t, row, index, value, *, dur=0.3):
        """古い字が上へ抜け、新しい字が下から入る。"""
        fn = "slots.set"
        r = self._event_row(fn, row)
        p = self._pos(fn, r, index)
        self._add(fn, "set", t, dur, row=r["name"], p=p,
                  text=_value_text(fn, value, "value"))
        return self

    def mark(self, t, row, index, *, color="accent", style="frame"):
        """箱に印を付ける（style='frame' は枠の色、'fill' は中を塗る）。"""
        fn = "slots.mark"
        r = self._event_row(fn, row)
        p = self._pos(fn, r, index)
        _require_choice(fn, "style", style, _MARK_STYLES)
        rgba = self._mark_color(fn, color)
        self._add(fn, "mark", t, 0.0, row=r["name"], p=p, rgba=list(rgba), style=style)
        return self

    def unmark(self, t, row, index):
        """mark の印を外す。"""
        fn = "slots.unmark"
        r = self._event_row(fn, row)
        p = self._pos(fn, r, index)
        self._add(fn, "unmark", t, 0.0, row=r["name"], p=p)
        return self

    def to_str(self, t, row):
        """各箱の数の両側に引用符が現れる（数を文字列として見せる）。"""
        fn = "slots.to_str"
        r = self._event_row(fn, row)
        self._add(fn, "to_str", t, 0.0, row=r["name"])
        return self

    def compare(self, t, row, i, j, *, by="str", dur=0.6):
        """2つの箱を少し持ち上げて比べ、間に不等号を出す（by='str' は最初に違う字を囲む）。"""
        fn = "slots.compare"
        r = self._event_row(fn, row)
        pi = self._pos(fn, r, i)
        pj = self._pos(fn, r, j)
        if pi == pj:
            raise ValueError(f"{fn}: 同じ箱どうしは比べられません: {i}")
        _require_choice(fn, "by", by, _COMPARE_BY)
        self._add(fn, "compare", t, dur, row=r["name"], i=pi, j=pj, by=by)
        return self

    def swap(self, t, row, i, j, *, dur=0.5):
        """2つの箱の中身が上下に分かれた弧で入れ替わる（交差しない）。"""
        fn = "slots.swap"
        r = self._event_row(fn, row)
        pi = self._pos(fn, r, i)
        pj = self._pos(fn, r, j)
        if pi == pj:
            raise ValueError(f"{fn}: 同じ箱どうしは入れ替えられません: {i}")
        self._add(fn, "swap", t, dur, row=r["name"], i=pi, j=pj)
        return self

    def halt(self, t, *, style="dim", dur=0.3):
        """止まった印（'dim' は全体の不透明度を 0.45 に、'freeze' は以後動かない）。

        dim を何度呼んでも 0.45 より薄くはならない（掛け合わせない）。
        """
        fn = "slots.halt"
        self._check_open(fn)
        _require_choice(fn, "style", style, _HALT_STYLES)
        self._add(fn, "halt", t, dur, style=style)
        return self

    # --- 組み立て ---

    def build(self, duration=None):
        """動画 Object を作る（framekit.build の生成物。obj.figure で寸法・位置が引ける）。

        duration: 秒。None は「最後の出来事の終わり＋1.0 秒」。出来事（t + dur）が
            duration を過ぎると ValueError（映らない出来事を黙って捨てない）
        表示は frames() と同じく time() で決める（引数なしの time() は図の尺ぶん。
        長く出すと最後のコマが残る）。
        """
        fn = "slots.build"
        self._check_open(fn)
        if not self._rows:
            raise ValueError(f"{fn}: 行がありません。先に s.row(...) で行を足してください")
        if duration is not None:
            _require_number(fn, "duration", duration, 0.01, 3600)
        proj = current_project()
        fps = _resolve_fps(fn, None)
        plan = _Plan(self, duration, fps, proj)
        renderer_box = {}

        def draw(i):
            # 描画器は実際に描くときだけ作る（dry_run では draw が呼ばれない）
            rend = renderer_box.get("r")
            if rend is None:
                rend = renderer_box["r"] = _Renderer(plan)
            return rend.frame_at(i / plan.fps)

        obj = fk.build("slots", kind="slots", ver=_SLOTS_VER, params=plan.key_spec,
                       draw=draw, n_frames=plan.n_frames, size=(plan.W, plan.H),
                       fps=plan.fps, fonts=plan._fonts.refs(), text=plan.audit_meta(),
                       info=SlotsFigure(plan))
        self._built = True
        return obj

    # --- 内部 ---

    def _check_open(self, fn):
        if self._built:
            raise ValueError(
                f"{fn}: build() の後には足せません（同じ図をもう1つ作るなら slots() から）")

    def _event_row(self, fn, row):
        self._check_open(fn)
        if not isinstance(row, str):
            raise ValueError(f"{fn}: 行は名前（文字列）で指定してください: {row!r}")
        r = self._row_by_name.get(row)
        if r is None:
            names = list(self._row_by_name)
            raise ValueError(
                f"{fn}: 行 '{row}' はありません（ある行: {', '.join(names) or 'なし'}）"
                f"{_suggest_hint(row, names)}")
        return r

    def _pos(self, fn, r, index, allow_out=False):
        _check_int(fn, "番号", index, -10**9, 10**9)
        p = index - r["base"]
        if not allow_out and not 0 <= p < r["n"]:
            raise ValueError(
                f"{fn}: 行 '{r['name']}' に番号 {index} はありません"
                f"（{r['base']}〜{r['base'] + r['n'] - 1}）")
        return p

    def _mark_color(self, fn, color):
        pal = {k: v for k, v in self._rgba.items() if v is not None and k not in ("bg", "halo")}
        return fk.color(fn, color, pal)

    def _add(self, fn, kind, t, dur, **fields):
        ev = {"k": kind, "t": _check_time(fn, t), "dur": _check_dur(fn, dur),
              "seq": len(self._events)}
        ev.update(fields)
        self._events.append(ev)


# --- 公開: 図の情報 ---

class SlotsFigure:
    """build() が返す Object の .figure（寸法・箱の位置・1コマの描画）。"""

    def __init__(self, plan):
        self._plan = plan
        self.size = (plan.W, plan.H)
        self.scale = plan.s
        self.duration = plan.duration
        self.fps = plan.fps
        self.text_meta = [dict(m) for m in plan.text_meta]
        self.rows = {r.name: {"n": r.n, "label": r.label, "index_base": r.base,
                              "capacity": r.capacity, "visible": r.visible_indices(),
                              "box_top": plan.Y(r.y), "box_height": plan.L(plan.C)}
                     for r in plan.rows}

    @property
    def font_ffp(self):
        """使ったフォント（font と mono_font）の内容指紋（金型の照合用）"""
        return self._plan._fonts.ffp()

    def cell_xy(self, row, index):
        """箱の中心のキャンバス内の px (x, y)。範囲外の番号は区画（ghost はその箱）の位置。"""
        r = self._plan.row(row)
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError(f"cell_xy: 番号は整数で指定してください: {index!r}")
        return self._plan.cell_center(r, index - r.base)

    def frame(self, t):
        """時刻 t（秒）のコマを RGBA の numpy 配列 (高さ, 幅, 4)・uint8 で返す（確認用）。"""
        _require_number("figure.frame", "t", t, 0, None)
        rend = getattr(self, "_renderer", None)
        if rend is None:
            rend = self._renderer = _Renderer(self._plan)
        return rend.frame_at(float(t)).copy()

    def state(self, t):
        """時刻 t の要約（行ごとの箱の中の塊の数・描いたあふれの数・描かなかった数 plus・
        あふれの札の文字 count_label など）。"""
        return self._plan.state_at(float(t))


# --- 計画（配置と時刻表。描画に依存しない）---

class _RowPlan:
    def __init__(self, spec):
        self.name = spec["name"]
        self.n = spec["n"]
        self.label = spec["label"]
        self.texts = spec["texts"]
        self.index = spec["index"]
        self.base = spec["base"]
        self.capacity = spec["capacity"]
        self.elide = spec["elide"]
        self.ghosts = set(spec["ghosts"])
        self.offset = spec["offset"]
        self.count_fmt = spec["overflow_label"]   # あふれの札の書式（None は出さない）
        self.touched = set()       # 出来事が触る番号（畳んでも見せる）
        self.zones = {}            # 区画の位置 p -> {"appear", "accent"}
        self.ghost_accent = {}     # ghost p -> accent になる時刻
        self.col_of = {}           # p -> 列番号（見える箱・ghost・区画）
        self.ellipses = []         # [(列番号, 隠れた p の最小, 最大)]
        self.has_read = False
        self.label_w = 0.0         # この行のラベルの幅（設計座標。_layout_rows が入れる）
        self.spill_depth = 0.0
        self.above = 0.0
        self.below = 0.0
        self.y = 0.0               # 箱の上端（設計座標）

    def in_range(self, p):
        return 0 <= p < self.n

    def visible_indices(self):
        return [p + self.base for p in sorted(self.col_of) if self.in_range(p)]


class _Plan:
    """行・出来事から、配置（設計座標 → キャンバス px）と時刻表を作る。"""

    def __init__(self, b, duration, fps, proj):
        self.fn = "slots.build"
        o = b._opts
        self.C = o["cell"]
        self.G = o["gap"]
        self.RG = o["row_gap"]
        self.PAD = o["padding"]
        self.LS = o["label_size"]
        self.VS = o["value_size"]
        self.IS = o["index_size"]
        self.seed = o["seed"]
        self.req_size = o["size"]
        self.rgba = b._rgba
        self.fps = fps
        self.rows = [_RowPlan(r) for r in b._rows]
        self._row_index = {r.name: r for r in self.rows}
        self.events = sorted((dict(e) for e in b._events), key=lambda e: (e["t"], e["seq"]))
        self._fonts = _Fonts(self.fn, b._font, b._mono_font, b._weight)
        self._weight = b._weight
        self.halo = self.rgba["halo"]
        self.halo_w = 2.0 if self.halo is not None else 0.0

        self._collect_touches()
        self._layout_columns()
        self._schedule_contents()
        self._layout_rows()
        self._schedule_motion()
        self._fit()
        self._finish_duration(duration)
        self._collect_text_meta(proj)
        self._build_key()

    # --- 参照 ---

    def row(self, name):
        r = self._row_index.get(name)
        if r is None:
            names = list(self._row_index)
            raise ValueError(
                f"cell_xy: 行 '{name}' はありません（ある行: {', '.join(names)}）"
                f"{_suggest_hint(name, names)}")
        return r

    def X(self, x):
        return x * self.s + self.ox

    def Y(self, y):
        return y * self.s + self.oy

    def L(self, v):
        return v * self.s

    def col_x(self, col):
        """列の左端（設計座標）"""
        return self.X0 + col * (self.C + self.G)

    def p_col(self, r, p):
        """番号 p の列（見えない番号は「…」の列、範囲外は区画の列）"""
        col = r.col_of.get(p)
        if col is not None:
            return col
        if r.in_range(p):
            for c, lo, hi in r.ellipses:
                if lo <= p <= hi:
                    return c
        zp = self._zone_p(r, p)
        if zp in r.col_of:
            return r.col_of[zp]
        cols = [r.col_of[q] for q in r.col_of]
        return (max(cols) + 1) if p >= r.n else (min(cols) - 1)

    def _zone_p(self, r, p):
        """範囲外の番号 p が向かう位置（ghost ならその箱、それ以外は行の外の区画）。

        区画は一番外の ghost のさらに外に置く（ghost=[21] の行で 25 番を読むと、21 の
        点線の箱ではなく、その右の斜線の区画へ出る。ghost の番号と区画を取り違えない）。
        """
        if p in r.ghosts:
            return p
        if p >= r.n:
            return max([r.n] + [g + 1 for g in r.ghosts if g >= r.n])
        return min([-1] + [g - 1 for g in r.ghosts if g < 0])

    def cell_center(self, r, p):
        col = self.p_col(r, p)
        x = self.col_x(col) + self.C / 2.0
        y = r.y + self.C / 2.0
        return (self.X(x), self.Y(y))

    # --- 1. 出来事が触る番号（畳んでも見せる窓） ---

    def _collect_touches(self):
        for e in self.events:
            k = e["k"]
            if k in ("put", "set", "mark", "unmark"):
                self._row_index[e["row"]].touched.add(e["p"])
            elif k in ("compare", "swap"):
                r = self._row_index[e["row"]]
                r.touched.update((e["i"], e["j"]))
            elif k == "read":
                r = self._row_index[e["row"]]
                r.has_read = True
                self._touch_target(r, e["p"], e["t"], e["dur"], read=True)
            elif k == "link":
                for end, is_target in ((e["a"], False), (e["b"], True)):
                    r = self._row_index[end[0]]
                    self._touch_target(r, end[1], e["t"], e["dur"], read=False,
                                       mark_bad=is_target)
        for r in self.rows:
            if r.capacity is not None:
                r.touched.update((r.capacity - 1, r.capacity))
            for g in r.ghosts:
                r.touched.update((g - 1, g + 1))

    def _touch_target(self, r, p, t, dur, *, read, mark_bad=True):
        if r.in_range(p):
            r.touched.add(p)
            return
        zp = self._zone_p(r, p)
        arrive = t + dur
        if read or zp not in r.ghosts:
            z = r.zones.setdefault(zp, {"appear": t, "accent": None})
            z["appear"] = min(z["appear"], t)
            if mark_bad:
                z["accent"] = arrive if z["accent"] is None else min(z["accent"], arrive)
        if zp in r.ghosts and mark_bad:
            r.ghost_accent[zp] = min(r.ghost_accent.get(zp, arrive), arrive)
        # 区画（か ghost）の隣の箱と、行の端の箱は畳んでも見せる
        r.touched.add(zp - 1 if zp >= r.n else zp + 1)
        r.touched.add(r.n - 1 if zp >= r.n else 0)

    # --- 2. 列（全行で共通の番号の軸。畳んだ区間は「…」の列） ---

    def _layout_columns(self):
        axis = set()
        for r in self.rows:
            shown = set()
            if r.elide is None or r.elide[0] + r.elide[1] >= r.n:
                shown.update(range(r.n))
            else:
                h, tl = r.elide
                shown.update(range(min(h, r.n)))
                shown.update(range(max(0, r.n - tl), r.n))
                for p in r.touched:
                    for q in (p - 1, p, p + 1):
                        if r.in_range(q):
                            shown.add(q)
            for p in r.touched:
                if r.in_range(p):
                    shown.add(p)
            extra = set(r.ghosts) | set(r.zones)
            for p in shown | extra:
                axis.add(p + r.offset)
        # 1 つだけ隠れる隙間は「…」にせず見せる（箱 1 つぶんの「…」は畳む意味が無い）
        changed = True
        while changed:
            changed = False
            for a in sorted(axis):
                if a + 1 not in axis and a + 2 in axis:
                    if any(r.in_range(a + 1 - r.offset) for r in self.rows):
                        axis.add(a + 1)
                        changed = True
        order = sorted(axis)
        col_of_axis = {}
        ellipses = []
        col = 0
        prev = None
        for a in order:
            if prev is not None:
                if a - prev == 1:
                    col += 1
                else:
                    ellipses.append((col + 1, prev + 1, a - 1))
                    col += 2
            col_of_axis[a] = col
            prev = a
        self.n_cols = col + 1
        if self.n_cols > _MAX_COLUMNS:
            raise ValueError(
                f"{self.fn}: 列が {self.n_cols} 個になり多すぎます（上限 {_MAX_COLUMNS}）。"
                f"elide で畳むか、番号の離れた出来事を減らしてください")
        for r in self.rows:
            for a, c in col_of_axis.items():
                p = a - r.offset
                if r.in_range(p) or p in r.ghosts or p in r.zones:
                    r.col_of[p] = c
            for c, a_lo, a_hi in ellipses:
                lo = max(0, a_lo - r.offset)
                hi = min(r.n - 1, a_hi - r.offset)
                if lo <= hi:
                    r.ellipses.append((c, lo, hi))
            shown_real = sum(1 for p in r.col_of if r.in_range(p))
            if r.elide is None and shown_real > _MAX_PLAIN:
                raise ValueError(
                    f"{self.fn}: 行 '{r.name}' の箱が {shown_real} 個見えます"
                    f"（elide なしは {_MAX_PLAIN} 個まで）")

    # --- 3. 中身（字・札）の時刻表 ---

    def _schedule_contents(self):
        """put / set / swap / to_str / compare / mark を時刻順に追い、箱ごとの中身の変化を作る。"""
        cur = {}            # (row, p) -> (text, card)
        for r in self.rows:
            for p, text in enumerate(r.texts):
                if text is not None:
                    cur[(r.name, p)] = (text, False)
        self.changes = {}   # (row, p) -> [(時刻, (text, card))]
        self.anims = {r.name: [] for r in self.rows}       # 中身を動かす出来事
        self.busy = {}      # (row, p) -> [(t0, t1, 種類)]
        self.quoted_at = {}  # row -> to_str の時刻
        self.marks = {}     # (row, p) -> [(時刻, 'mark'|'unmark', rgba, style)]
        self.compares = {r.name: [] for r in self.rows}
        self.halts_dim = []
        self.freeze_t = None
        row_events = {r.name: [] for r in self.rows}

        def content(row, p):
            return cur.get((row, p), (None, False))

        def occupy(row, p, t0, t1, kind):
            for (a0, a1, other) in self.busy.get((row, p), []):
                if t0 < a1 - 1e-9 and a0 < t1 - 1e-9:
                    r = self._row_index[row]
                    raise ValueError(
                        f"{self.fn}: 行 '{row}' の番号 {p + r.base} で {other} と {kind} の"
                        f"動きが重なっています（{a0:g}〜{a1:g} 秒と {t0:g}〜{t1:g} 秒）。"
                        f"時刻をずらしてください")
            self.busy.setdefault((row, p), []).append((t0, t1, kind))

        for e in self.events:
            k = e["k"]
            t0 = e["t"]
            t1 = t0 + e["dur"]
            if k == "halt":
                if self.freeze_t is not None:
                    raise ValueError(
                        f"{self.fn}: halt(style='freeze')（{self.freeze_t:g} 秒）の後の"
                        f"出来事（{k}, {t0:g} 秒）は映りません")
                if e["style"] == "freeze":
                    self.freeze_t = t0
                else:
                    self.halts_dim.append((t0, e["dur"]))
                continue
            if self.freeze_t is not None and t0 > self.freeze_t:
                raise ValueError(
                    f"{self.fn}: halt(style='freeze')（{self.freeze_t:g} 秒）の後の"
                    f"出来事（{k}, {t0:g} 秒）は映りません")
            if k == "link":
                continue
            row = e["row"]
            row_events[row].append(e)
            if k == "put":
                p = e["p"]
                occupy(row, p, t0, t1, "put")
                self.anims[row].append({"k": "put", "t0": t0, "t1": t1, "p": p,
                                        "old": content(row, p), "new": (e["text"], True)})
                cur[(row, p)] = (e["text"], True)
                self.changes.setdefault((row, p), []).append((t1, cur[(row, p)]))
            elif k == "set":
                p = e["p"]
                occupy(row, p, t0, t1, "set")
                old = content(row, p)
                new = (e["text"], old[1] if e["text"] is not None else False)
                self.anims[row].append({"k": "set", "t0": t0, "t1": t1, "p": p,
                                        "old": old, "new": new})
                cur[(row, p)] = new
                self.changes.setdefault((row, p), []).append((t1, new))
            elif k == "swap":
                i, j = e["i"], e["j"]
                occupy(row, i, t0, t1, "swap")
                occupy(row, j, t0, t1, "swap")
                ci, cj = content(row, i), content(row, j)
                self.anims[row].append({"k": "swap", "t0": t0, "t1": t1, "i": i, "j": j,
                                        "ci": ci, "cj": cj})
                cur[(row, i)], cur[(row, j)] = cj, ci
                self.changes.setdefault((row, i), []).append((t1, cj))
                self.changes.setdefault((row, j), []).append((t1, ci))
            elif k == "to_str":
                self.quoted_at.setdefault(row, t0)
            elif k == "compare":
                i, j = e["i"], e["j"]
                ti, tj = content(row, i)[0], content(row, j)[0]
                r = self._row_index[row]
                for p, tx in ((i, ti), (j, tj)):
                    if not tx:
                        raise ValueError(
                            f"{self.fn}: compare（{t0:g} 秒）の番号 {p + r.base} の箱が空です")
                    if e["by"] == "num" and not _is_numeric(tx):
                        raise ValueError(
                            f"{self.fn}: compare(by='num')（{t0:g} 秒）の番号 {p + r.base} の"
                            f"中身 '{tx}' は数ではありません（文字として比べるなら by='str'）")
                occupy(row, i, t0, t1, "compare")
                occupy(row, j, t0, t1, "compare")
                self.compares[row].append(self._compare_info(e, r, ti, tj))
            elif k in ("mark", "unmark"):
                self.marks.setdefault((row, e["p"]), []).append(
                    (t0, k, tuple(e.get("rgba", (0, 0, 0, 0))), e.get("style")))
        # compare の持ち上げを下ろす時刻: 同じ行の次の出来事（印・針を除く）の直前
        # （間が _RELEASE より短ければ次の出来事と重なって下りる。その間の put / set /
        # swap / fill の塊は残りの持ち上げを持ち越して描くので、字が箱から外れない）
        for row, comps in self.compares.items():
            evs = [e for e in row_events[row]
                   if e["k"] in ("put", "set", "swap", "compare", "to_str", "fill")]
            for c in comps:
                later = [e["t"] for e in evs if e["t"] > c["t0"] + 1e-9 and e["seq"] != c["seq"]]
                if later:
                    rel1 = max(c["t1"], min(later))
                    c["rel0"] = max(c["t1"], rel1 - _RELEASE)
                    c["rel1"] = c["rel0"] + _RELEASE
                else:
                    c["rel0"] = c["rel1"] = None
        self.row_events = row_events

    def _compare_info(self, e, r, ti, tj):
        i, j = e["i"], e["j"]
        left, right = (i, j) if self._axis(r, i) < self._axis(r, j) else (j, i)
        tl = ti if left == i else tj
        tr = tj if left == i else ti
        frames_at = {}
        if e["by"] == "str":
            k = 0
            while k < min(len(tl), len(tr)) and tl[k] == tr[k]:
                k += 1
            if k < len(tl):
                frames_at[left] = k
            if k < len(tr):
                frames_at[right] = k
            sign = "<" if tl < tr else (">" if tl > tr else "=")
        else:
            a, bv = float(tl), float(tr)
            sign = "<" if a < bv else (">" if a > bv else "=")
        return {"t0": e["t"], "t1": e["t"] + e["dur"], "dur": e["dur"], "i": i, "j": j,
                "left": left, "right": right, "frames": frames_at, "sign": sign,
                "seq": e["seq"]}

    def _axis(self, r, p):
        return self.p_col(r, p)

    def content_at(self, row, p, t):
        r = self._row_index[row]
        ch = self.changes.get((row, p))
        if ch:
            k = bisect.bisect_right([c[0] for c in ch], t)
            if k > 0:
                return ch[k - 1][1]
        text = r.texts[p] if r.in_range(p) else None
        return (text, False) if text is not None else (None, False)

    # --- 4. 行の縦の配置 ---

    def _layout_rows(self):
        C = self.C
        fonts = self._fonts
        self.label_w = 0.0
        for r in self.rows:
            r.label_w = 0.0
            if r.label:
                for line in r.label.split("\n"):
                    r.label_w = max(r.label_w, fonts.width(line, self.LS))
            self.label_w = max(self.label_w, r.label_w)
        self.label_gap = max(16.0, 0.375 * C) if self.label_w > 0 else 0.0
        self.idx_gap = 6.0
        self.idx_h = self.IS * 1.25
        self.ptr_h = 0.34 * C
        self.ptr_w = 0.42 * C
        self.cap_ext = 0.28 * C
        self.block = 0.68 * C
        self.lift = 0.28 * C
        X0 = self.label_w + self.label_gap
        side = max(0.0, self.G - 2)
        for e in self.events:
            r = self._row_index[e["row"]] if "row" in e else None
            if r is None or not r.label:
                continue
            if e["k"] == "fill" and e["source"] == "left":
                # source='left' の入口（行の左 1.6C）はラベルに掛けない: 箱の列ごと右へ寄せる
                minc = min(r.col_of.values())
                need = (r.label_w + _LABEL_MARGIN + 1.6 * C + self.block / 2.0
                        - minc * (C + self.G))
                X0 = max(X0, need)
            elif e["k"] == "put":
                # 左隣の無い箱の札は左へ広がる。ラベルに掛けず、字も縮めずに済むよう
                # 箱の列ごと右へ寄せる（札の幅の上限は _card_room と同じ）
                col = self.p_col(r, e["p"])
                cols = set(r.col_of.values()) | {c for c, _lo, _hi in r.ellipses}
                if (col - 1) in cols:
                    continue
                w = self._card_need(r.name, e["text"])
                if (col + 1) in cols:
                    w = min(w, 2 * C + side)
                    need = r.label_w + _LABEL_MARGIN + w - C - side - col * (C + self.G)
                else:
                    w = min(w, 3 * C)
                    need = r.label_w + _LABEL_MARGIN + w / 2.0 - C / 2.0 - col * (C + self.G)
                X0 = max(X0, need)
        self.X0 = X0
        fill_rows = {e["row"] for e in self.events if e["k"] == "fill"}
        spill_rows = {e["row"] for e in self.events if e["k"] == "fill" and e["spill"]}
        put_rows = {e["row"] for e in self.events if e["k"] == "put"}
        swap_rows = {}
        for e in self.events:
            if e["k"] == "swap":
                h = self._swap_h(self._row_index[e["row"]], e["i"], e["j"])
                swap_rows[e["row"]] = max(swap_rows.get(e["row"], 0.0), h)
        # 同じ行の矢印（弧）は行の上に伸びる（札があればその上にも。複数行の札は行数ぶん）
        arc_rows = {}
        for e in self.events:
            if e["k"] == "link" and e["a"][0] == e["b"][0]:
                r0 = self._row_index[e["a"][0]]
                dx = abs(self.p_col(r0, e["a"][1]) - self.p_col(r0, e["b"][1])) * (C + self.G)
                h = 6.0 + min(1.2 * C, 0.5 * C + 0.15 * dx)
                if e["label"]:
                    h += self.link_label_h(e["label"]) + 0.35 * self.IS
                arc_rows[r0.name] = max(arc_rows.get(r0.name, 0.0), h)
        # 行をまたぐ矢印の札は、始点の行と行き先の側の隣の行の間に置く（その隙間の高さを
        # 札の高さ＋余白まで広げる。gap_extra[k] は k 行目と k+1 行目の間に足す高さ）
        order = {r.name: k for k, r in enumerate(self.rows)}
        gap_extra = [0.0] * len(self.rows)
        for e in self.events:
            if e["k"] == "link" and e["a"][0] != e["b"][0] and e["label"]:
                ia, ib = order[e["a"][0]], order[e["b"][0]]
                k = ia if ib > ia else ia - 1
                need = self.link_label_h(e["label"]) + 12.0 - self.RG
                gap_extra[k] = max(gap_extra[k], need)
        y = 0.0
        prev = None
        for k, r in enumerate(self.rows):
            above = 10.0
            if r.capacity is not None:
                above = max(above, self.cap_ext + 6 + self.idx_h)
            if r.name in spill_rows:
                # 「上限 N」の右に並べるあふれの札（capacity が無くても線の上に出す）
                above = max(above, self.cap_ext + 4 + self.idx_h / 2.0
                            + 0.7 * self.note_size() + 2)
            if r.name in fill_rows and r.ellipses:
                above = max(above, 8 + self.idx_h)
            if self.compares[r.name]:
                above = max(above, self.lift + 10 + self.note_size() * 1.3)
            if r.name in put_rows:
                above = max(above, 0.9 * C)
            if r.name in swap_rows:
                above = max(above, swap_rows[r.name] - 0.25 * C)
            if r.name in arc_rows:
                above = max(above, arc_rows[r.name])
            below = 6.0
            if r.index:
                below = self.idx_gap + self.idx_h
            if r.has_read:
                below += 6 + self.ptr_h
            if r.name in swap_rows:
                below = max(below, swap_rows[r.name] - 0.25 * C)
            if r.name in spill_rows:
                below = max(below, 1.7 * C)              # こぼれ落ちる塊（薄れながら）
            if r.name in fill_rows and r.capacity is not None:
                band = (self.idx_gap + self.idx_h) if r.index else 4.0
                below = max(below, band + 1.6 * self.note_size())   # あふれの札
            if r.label:
                # 複数行のラベルは行の中心から上下へはみ出す。その分を隣の行と空ける
                over = (r.label.count("\n") + 1) * self.LS * 1.25 / 2.0 - C / 2.0 + 4.0
                above, below = max(above, over), max(below, over)
            r.above, r.below = above, below
            if prev is None:
                y = above
            else:
                y = prev.y + C + prev.below + self.RG + gap_extra[k - 1] + above
            r.y = y
            prev = r

    def link_label_h(self, label):
        """矢印の札の高さ（設計座標。行数 × 1.25 × index_size）"""
        return (label.count("\n") + 1) * self.IS * 1.25

    def _swap_h(self, r, i, j):
        dx = abs(self.p_col(r, i) - self.p_col(r, j)) * (self.C + self.G)
        return min(1.3 * self.C, 0.62 * self.C + 0.12 * dx)

    # --- 5. 動き（塊・あふれ・針・矢印）の時刻表 ---

    def _schedule_motion(self):
        C = self.C
        self.fills = {r.name: {"landed": {}, "flights": [], "gaps": {}, "spills": [],
                               "over": [], "plus_pos": None, "entry": None,
                               "limit": None, "line_x": None, "box_times": [],
                               "count": None}
                      for r in self.rows}
        filled = {r.name: set() for r in self.rows}
        fill_no = {r.name: 0 for r in self.rows}
        rng_base = f"slots:{self.seed}"
        for e in self.events:
            if e["k"] != "fill":
                continue
            r = self._row_index[e["row"]]
            fs = self.fills[r.name]
            # あふれの乱数の種は「その行の何番目の fill か」（呼んだ順の seq は鍵に入らない。
            # events は (t, seq) で並べてあり、その並びは鍵に入る）
            rng = random.Random(f"{rng_base}:{r.name}:{fill_no[r.name]}")
            fill_no[r.name] += 1
            limit = r.capacity if r.capacity is not None else r.n
            fs["limit"] = limit
            seq = range(limit) if e["order"] == "left" else range(limit - 1, -1, -1)
            slots_ = [p for p in seq if p not in filled[r.name]][:e["count"]]
            overflow = e["count"] - len(slots_)
            filled[r.name].update(slots_)
            # 入口（source 側の行の外。左はラベルに掛からない所: _layout_rows が X0 で空ける）
            ex, ey = self._entry(r, e["source"])
            fs["entry"] = (ex, ey)
            # 時刻の重み（見える箱 1・隠れた箱はまとめて最大 4・あふれは描く数×0.6。
            # ただし描くあふれどうしの間隔は _SPILL_GAP 秒以上にする: 足りなければあふれの
            # 時間を延ばし（fill の 65% まで）、それでも足りなければ描く数を減らして「+N」へ）
            hidden = [p for p in slots_ if p not in r.col_of]
            w_hidden = (min(len(hidden), 4.0) / len(hidden)) if hidden else 0.0
            weights = [1.0 if p in r.col_of else w_hidden for p in slots_]
            w_boxes = sum(weights)
            line_x = self.line_x(r)
            out_side = 1.0  # 線の外（capacity より大きい番号の側）は右

            def dist_to(x):
                return abs(ex - x)

            far = max([dist_to(self.col_x(self.p_col(r, p)) + C / 2) for p in slots_]
                      + [dist_to(line_x)] + [C])
            tau_max = min(0.6, max(0.25, 0.3 * e["dur"]))

            def flight(x):
                return max(0.12, tau_max * (0.3 + 0.7 * dist_to(x) / far))

            t0, dur = e["t"], e["dur"]
            tau_first = flight(self.col_x(self.p_col(r, slots_[0])) + C / 2) if slots_ \
                else flight(line_x)
            tau_first = min(tau_first, 0.5 * dur) if dur > 0 else 0.0
            span = max(0.0, dur - tau_first)
            m, q, w_over_total = self._spill_plan(
                overflow, min(overflow, e["spill_visible"]) if e["spill"] else 0, w_boxes, span)
            total_w = (w_boxes + w_over_total) or 1.0

            def base(cw):
                return t0 + tau_first + span * (cw / total_w)

            cum = 0.0
            arrivals = []
            for w in weights:
                arrivals.append(base(cum))
                cum += w
            # あふれは重みが等しいので等間隔（start + k*step）。1 個ずつのリストは作らない
            over_start = base(cum)
            over_step = span * (w_over_total / overflow) / total_w if overflow else 0.0
            # 時刻の重みは「到着の間隔」に効く（最後の到着が t0+dur 付近）
            n_items = len(slots_) + overflow
            first = arrivals[0] if arrivals else over_start
            last = (over_start + (overflow - 1) * over_step) if overflow else arrivals[-1]
            end = t0 + dur
            if last < end and n_items > 1 and last > first + 1e-12:
                kk = (end - first) / (last - first)
                arrivals = [first + (a - first) * kk for a in arrivals]
                over_start = first + (over_start - first) * kk
                over_step *= kk
            fs["box_times"].extend(arrivals)
            for idx, p in enumerate(slots_):
                a = arrivals[idx]
                if p in r.col_of:
                    x = self.col_x(r.col_of[p]) + C / 2.0
                    tau = min(flight(x), a - t0) if a > t0 else 0.0
                    fs["flights"].append({"p": p, "start": a - tau, "arrive": a,
                                          "x0": ex, "x1": x, "y": ey})
                    fs["landed"][p] = a
                else:
                    col = self.p_col(r, p)
                    g = fs["gaps"].setdefault(col, {"times": [], "size": 0})
                    g["times"].append(a)
            for c, lo, hi in r.ellipses:
                if c in fs["gaps"]:
                    fs["gaps"][c]["size"] = hi - lo + 1
                    fs["gaps"][c]["times"].sort()
            # あふれ
            fs["line_x"] = line_x
            if overflow:
                ov = _Overflow(over_start, over_step, overflow, m, q)
                fs["over"].append(ov)
                hit_x = self.hit_x(r)
                golden = 0.6180339887498949
                for j, k in enumerate(ov.drawn_indices() if m else []):
                    a = ov.at(k)
                    tau = min(flight(hit_x), max(0.0, a - t0))
                    # 当たる高さと跳ね返る速さは層別（続けて当たる塊が上下・遠近に散る）に
                    # 乱数で揺らぎを足す。回転と落ちる時間も seed で決まる
                    fh = (j * golden + rng.uniform(0.0, 0.3)) % 1.0
                    fv = (j * golden * 2.0 + 0.5 + rng.uniform(0.0, 0.3)) % 1.0
                    fs["spills"].append({
                        "start": a - tau, "hit": a, "x0": ex, "y": ey,
                        "hy": ey + (fh - 0.5) * 0.64 * C, "hx": hit_x,
                        "vx": out_side * (1.0 + 1.8 * fv) * C,
                        "vy": -rng.uniform(0.4, 1.2) * C, "g": 14.0 * C,
                        "fall": rng.uniform(0.45, 0.55),
                        "omega": rng.uniform(1.5, 4.5) * (1.0 if rng.random() < 0.5 else -1.0)})
        # あふれの札（出す行だけ。出始める秒は最初のあふれが線に着く秒。数として undrawn
        # だけを使う書式（limit は添えてよい）は、描かない塊が最初に着く秒から。描かない塊が
        # 無ければ出さない。limit だけの書式は数を使わないので、total・over と同じく最初の
        # あふれから出す）
        for r in self.rows:
            fs = self.fills[r.name]
            fs["box_times"].sort()
            if r.count_fmt is None or not fs["over"]:
                continue
            fields = {f for _l, f, _s, _c in string.Formatter().parse(r.count_fmt) if f}
            if "undrawn" in fields and not fields & {"total", "over"}:
                firsts = [pt[0] for pt in (ov.plus_times() for ov in fs["over"]) if pt]
            else:
                firsts = [ov.start for ov in fs["over"]]
            if firsts:
                fs["count"] = {"fmt": r.count_fmt, "t0": min(firsts)}
                fs["plus_pos"] = self._plus_pos(r)
        # 針
        self.pointers = {}
        for e in self.events:
            if e["k"] != "read":
                continue
            r = self._row_index[e["row"]]
            segs = self.pointers.setdefault(r.name, [])
            p = e["p"]
            bad = not r.in_range(p)
            target_col = self.p_col(r, p if not bad else self._zone_p(r, p))
            x1 = self.col_x(target_col) + self.C / 2.0
            if segs:
                x0 = segs[-1]["x1"]
            else:
                x0 = self.col_x(self.p_col(r, 0)) + self.C / 2.0
            segs.append({"t0": e["t"], "t1": e["t"] + e["dur"], "x0": x0, "x1": x1,
                         "bad": bad})
        # 矢印
        self.links = []
        for e in self.events:
            if e["k"] != "link":
                continue
            ra = self._row_index[e["a"][0]]
            rb = self._row_index[e["b"][0]]
            pa, pb = e["a"][1], e["b"][1]
            pts = self._link_path(ra, pa, rb, pb)
            bad = not rb.in_range(pb)
            label_pos = None
            if e["label"] and ra is rb:
                # 同じ行の弧: 札は弧の頂点の上に中央揃え（複数行は下の行が頂点の上に来る）
                top = min(y for _x, y in pts)
                h = self.link_label_h(e["label"])
                label_pos = ((pts[0][0] + pts[-1][0]) / 2.0, top - 0.25 * self.IS - h / 2.0,
                             "center")
            elif e["label"]:
                label_pos = self._cross_label_pos(ra, rb, pts, e["label"])
            self.links.append({"t0": e["t"], "t1": e["t"] + e["dur"], "pts": pts,
                               "bad": bad, "label": e["label"], "label_pos": label_pos})

    def _cross_label_pos(self, ra, rb, pts, label):
        """行をまたぐ矢印の札の位置 (x, y, 'left')（設計座標）。

        縦は始点の行と行き先の側の隣の行の間の隙間の真ん中（_layout_rows がその高さを
        空けてある）。横は、札の高さ（上下に 8 の余白を足す）の範囲で矢印の線が通る x の
        右端より右（斜めの矢印でも線と鏃が字に掛からない。浅い矢印は鏃の側へ寄る）。
        """
        k = self.rows.index(ra)
        if rb.y > ra.y:
            upper, lower = ra, self.rows[k + 1]
        else:
            upper, lower = self.rows[k - 1], ra
        top = upper.y + self.C + upper.below
        bottom = lower.y - lower.above
        my = (top + bottom) / 2.0
        h = self.link_label_h(label)
        (x0, y0), (x1, y1) = pts[0], pts[-1]

        def x_at(y):
            if abs(y1 - y0) < 1e-9:
                return max(x0, x1)
            u = _clamp01((y - y0) / (y1 - y0))
            return x0 + (x1 - x0) * u

        x_line = max(x_at(my - h / 2.0 - 8.0), x_at(my + h / 2.0 + 8.0))
        head_half = max(12.0, 0.30 * self.C) / 2.0
        return (x_line + head_half + 8.0, my, "left")

    @staticmethod
    def _spill_plan(ov, m_req, w_boxes, span):
        """あふれの描き方 (描く数 m, 描く塊の間隔 q（あふれの個数）, あふれの時間の重み)。

        描く塊 k = j*q（j = 0..m-1）が線に着く間隔 q*step を _SPILL_GAP 秒以上にする
        （詰めると線の外で塊が重なり、赤い団子になって 1 個ずつ見分けられない）。
        それには最初から最後のあふれまでの区間 R = (ov-1)*_SPILL_GAP/q が要る。箱へ詰まる
        区間の重み w_boxes に対してあふれの重み Wd = w_boxes*R/(span-R) を与えると、
        「最後の到着を t0+dur へ伸ばす」後の区間がちょうど R になる。R は fill の 65% まで
        （箱が埋まる様子を残す）。入らなければ m を減らし、減った分は「+N」で数える。
        元の重み（描く数×0.6）の方が長ければそちらを使う。
        """
        if ov <= 0:
            return 0, 1, 0.0
        m = m_req
        if span <= 0 or ov < 2:
            m = min(m, 1)
        r_max = (0.65 * span) if w_boxes > 0 else span
        q = 1
        while m >= 2:
            q = (ov - 1) // (m - 1)
            if (ov - 1) * _SPILL_GAP / q <= r_max + 1e-9:
                break
            m -= 1
        w_old = 0.6 * max(m, 1)
        if m < 2 or w_boxes <= 0:
            return m, q if m >= 2 else 1, w_old
        wd_old = w_old * (ov - 1) / ov
        R = max((ov - 1) * _SPILL_GAP / q, span * wd_old / (w_boxes + wd_old))
        wd = w_boxes * R / (span - R)
        return m, q, wd * ov / (ov - 1)

    def line_x(self, r):
        """上限の線の x（設計座標。capacity が無ければ最後の箱の右）"""
        last = (r.capacity if r.capacity is not None else r.n) - 1
        return self.col_x(self.p_col(r, last)) + self.C + self.G / 2.0

    def hit_x(self, r):
        """あふれの塊が上限の線に当たるときの塊の中心の x（線の外側）"""
        return self.line_x(r) + self.block / 2.0 + _LINE_W / 2.0 + 1.0

    def _entry(self, r, source):
        """fill の塊が現れる入口（設計座標）。行の外の source 側"""
        C = self.C
        cols = list(r.col_of.values())
        ey = r.y + C / 2.0
        if source == "right":
            return self.col_x(max(cols)) + C + 1.6 * C, ey
        return self.col_x(min(cols)) - 1.6 * C, ey

    def cap_label_y(self, r):
        """「上限 N」（と、その右のあふれの札）の縦の中心（設計座標）"""
        return r.y - self.cap_ext - 4 - self.idx_h / 2.0

    def _plus_pos(self, r):
        """あふれの札の位置 (x, y, align)（設計座標）。

        線の外にも箱があれば、番号の帯の下・線のすぐ右。無ければ「上限 N」の右に並べる
        （こぼれ落ちる塊の扇の中に置くと、塊が字の上を通って読めない）。
        """
        C = self.C
        lx = self.line_x(r)
        beyond = [c for c in r.col_of.values() if self.col_x(c) > lx]
        if beyond:
            band = (self.idx_gap + self.idx_h) if r.index else 4.0
            return (lx + 0.3 * C, r.y + C + band + 0.75 * self.note_size(), "left")
        if r.capacity is not None:
            w = self._fonts.width(f"上限 {r.capacity}", self.IS)
            return (lx + w / 2.0 + 0.35 * self.note_size(), self.cap_label_y(r), "left")
        return (lx + 0.3 * C, self.cap_label_y(r), "left")

    def count_text(self, r, t=None):
        """あふれの札の文字（t 秒の値。t=None は最後の値）。札を出さない行は None。

        total は箱へ入った数とあふれが線に着いた数の和、over はあふれが線に着いた数、
        undrawn はそのうち描かなかった塊の数（あふれの「+N」）、limit は上限
        （capacity。無ければ n）。
        """
        fs = self.fills[r.name]
        c = fs["count"]
        if c is None:
            return None
        if t is None:
            over = sum(ov.n for ov in fs["over"])
            undrawn = sum(ov.plus_total() for ov in fs["over"])
            boxes = len(fs["box_times"])
        else:
            over = sum(ov.arrived(t) for ov in fs["over"])
            undrawn = sum(ov.plus(t) for ov in fs["over"])
            boxes = bisect.bisect_right(fs["box_times"], t + 1e-9)
        limit = r.capacity if r.capacity is not None else r.n
        return c["fmt"].format(total=boxes + over, over=over, undrawn=undrawn, limit=limit)

    def _link_path(self, ra, pa, rb, pb):
        C = self.C
        xa = self.col_x(self.p_col(ra, pa)) + C / 2.0
        xb = self.col_x(self.p_col(rb, pb)) + C / 2.0
        idx_band = (self.idx_gap + self.idx_h) if ra.index else 4.0
        if ra is rb:
            y0 = ra.y - 6.0
            h = min(1.2 * C, 0.5 * C + 0.15 * abs(xb - xa))
            pts = []
            for k in range(33):
                u = k / 32.0
                x = xa + (xb - xa) * u
                y = y0 - h * 4 * u * (1 - u)
                pts.append((x, y))
            return pts
        if rb.y > ra.y:
            y0 = ra.y + C + idx_band + 4.0
            y1 = rb.y - 6.0
        else:
            y0 = ra.y - 6.0
            band_b = (self.idx_gap + self.idx_h) if rb.index else 4.0
            y1 = rb.y + C + band_b + 4.0
        return [(xa, y0), (xb, y1)]

    # --- 6. 外接矩形と倍率 ---

    def _fit(self):
        C = self.C
        xs, ys = [], []

        def add(x0, y0, x1, y1):
            xs.extend((x0, x1))
            ys.extend((y0, y1))

        for r in self.rows:
            if r.label:
                lines = r.label.split("\n")
                h = len(lines) * self.LS * 1.25
                cy = r.y + C / 2.0
                add(0.0, cy - h / 2.0, self.label_w, cy + h / 2.0)
            for p, c in r.col_of.items():
                x = self.col_x(c)
                add(x - 2, r.y - 2, x + C + 2, r.y + C + 2)
            for c, _lo, _hi in r.ellipses:
                x = self.col_x(c)
                add(x, r.y, x + C, r.y + C)
            if r.index and r.col_of:
                for p, c in r.col_of.items():
                    if r.in_range(p) or p in r.ghosts:
                        w = self._fonts.width(str(p + r.base), self.IS, mono=True)
                        cx = self.col_x(c) + C / 2.0
                        add(cx - w / 2.0, r.y + C, cx + w / 2.0,
                            r.y + C + self.idx_gap + self.idx_h)
            if r.capacity is not None:
                lx = self.line_x(r)
                w = self._fonts.width(f"上限 {r.capacity}", self.IS)
                top = r.y - self.cap_ext - 4 - self.idx_h
                add(lx - w / 2.0, top, lx + w / 2.0, r.y + C + self.cap_ext)
            if r.has_read:
                top = r.y + C + ((self.idx_gap + self.idx_h) if r.index else 4.0) + 6
                for seg in self.pointers.get(r.name, []):
                    for x in (seg["x0"], seg["x1"]):
                        add(x - self.ptr_w / 2.0, top, x + self.ptr_w / 2.0, top + self.ptr_h)
            fs = self.fills[r.name]
            if fs["entry"] is not None:
                ex, ey = fs["entry"]
                b = self.block / 2.0
                add(ex - b, ey - b, ex + b, ey + b)
            for c in fs["gaps"]:
                x = self.col_x(c) + C / 2.0
                add(x - C / 2.0, r.y - 8 - self.idx_h, x + C / 2.0, r.y)
            for sp in fs["spills"]:
                hd = self.block * 0.75
                for k in range(13):
                    tt = sp["fall"] * k / 12.0
                    x = sp["hx"] + sp["vx"] * tt
                    y = sp["hy"] + sp["vy"] * tt + 0.5 * sp["g"] * tt * tt
                    add(x - hd, y - hd, x + hd, y + hd)
            if fs["plus_pos"] is not None:
                # 札の幅は最後の値で測る（数は増えるだけなので、途中の札はこれより狭い）
                text = self.count_text(r)
                ns = self.note_size()
                w = self._fonts.width(text, ns)
                px, py, align = fs["plus_pos"]
                x0 = px if align == "left" else px - w / 2.0
                add(x0, py - ns * 0.7, x0 + w, py + ns * 0.7)
            for an in self.anims[r.name]:
                if an["k"] == "put":
                    x = self.col_x(self.p_col(r, an["p"])) + C / 2.0
                    x0c, x1c = self.card_span(r.name, an["p"], an["new"][0])
                    add(x0c, r.y - 0.9 * C, x1c, r.y + C)
                    w = C / 2.0
                    add(x - w, r.y - 0.9 * C, x + w, r.y + C)
                elif an["k"] == "swap":
                    h = self._swap_h(r, an["i"], an["j"])
                    for p in (an["i"], an["j"]):
                        x = self.col_x(self.p_col(r, p))
                        add(x, r.y + C / 2.0 - h - C * 0.35, x + C, r.y + C / 2.0 + h + C * 0.35)
            for c in self.compares[r.name]:
                for p in (c["i"], c["j"]):
                    x = self.col_x(self.p_col(r, p))
                    add(x, r.y - self.lift - 2, x + C, r.y + C)
                xl = self.col_x(self.p_col(r, c["left"])) + C / 2.0
                xr = self.col_x(self.p_col(r, c["right"])) + C / 2.0
                ns = self.note_size()
                cy = r.y - self.lift - 10 - ns * 0.65
                add((xl + xr) / 2.0 - ns, cy - ns * 0.7, (xl + xr) / 2.0 + ns, cy + ns * 0.7)
        for lk in self.links:
            for (x, y) in lk["pts"]:
                add(x - 0.3 * C, y - 0.3 * C, x + 0.3 * C, y + 0.3 * C)
            if lk["label"]:
                lx, ly, align = lk["label_pos"]
                lines = lk["label"].split("\n")
                w = max(self._fonts.width(s, self.IS) for s in lines)
                h = len(lines) * self.IS * 1.25
                x0 = lx if align == "left" else lx - w / 2.0
                add(x0 - 2, ly - h / 2.0, x0 + w + 2, ly + h / 2.0)
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        bw, bh = max_x - min_x, max_y - min_y
        pad = self.PAD
        if self.req_size is None:
            self.s = 1.0
            self.W = _even_ceil(bw + 2 * pad)
            self.H = _even_ceil(bh + 2 * pad)
            self.ox = float(round((self.W - bw) / 2.0 - min_x))
            self.oy = float(round((self.H - bh) / 2.0 - min_y))
        else:
            self.W, self.H = self.req_size
            avail_w = max(1.0, self.W - 2 * pad)
            avail_h = max(1.0, self.H - 2 * pad)
            self.s = min(1.0, avail_w / bw, avail_h / bh)
            ox = (self.W - bw * self.s) / 2.0 - min_x * self.s
            oy = (self.H - bh * self.s) / 2.0 - min_y * self.s
            if self.s == 1.0:
                ox, oy = float(round(ox)), float(round(oy))
            self.ox, self.oy = ox, oy
        if self.W > 8192 or self.H > 8192:
            raise ValueError(
                f"{self.fn}: 図が大きすぎます（{self.W}x{self.H}px、上限 8192px）。"
                f"elide で畳むか cell を小さくしてください")

    def _card_w(self, text, row=None, p=None):
        """札の幅（設計座標）。箱と同じ大きさで、字が入らなければ横に広がる
        （隣の箱に掛からない所まで。_card_room）"""
        C = self.C
        if not text:
            return C
        size = self.value_size_for(row, text)
        tw = self._value_width(row, text, size)
        return min(max(C, tw + 2 * _BOX_STROKE + 0.12 * C), self._card_room(row, p)[0])

    def _card_room(self, row, p):
        """札を広げられる幅の上限と向き（'center' / 'right' / 'left'）。

        両隣に列（箱・「…」・ghost・区画）があれば隣の手前まで左右に等しく広げる
        （C + 2*gap - 4）。行の端の箱は、外側（隣の無い側）へ C まで余分に伸ばせる
        （21 番目の札が隣の 20 番目に掛からず、字を _VALUE_FLOOR 未満へ縮めずに済む）。
        ただし左へはその行のラベルの右端＋_LABEL_MARGIN までしか伸ばさない（ラベルの
        字を隠さない。入らない字は縮み、p.audit() が報告する）。
        """
        C = self.C
        side = max(0.0, self.G - 2)
        if row is None or p is None:
            return C + 2 * side, "center"
        r = self._row_index[row]
        col = self.p_col(r, p)
        cols = set(r.col_of.values()) | {c for c, _lo, _hi in r.ellipses}
        left, right = (col - 1) in cols, (col + 1) in cols
        x = self.col_x(col)
        left_limit = (r.label_w + _LABEL_MARGIN) if r.label else -math.inf
        if left and not right:
            return C + side + C, "right"
        if right and not left:
            return min(C + side + C, x + C + side - left_limit), "left"
        if not left and not right:
            return min(C + 2 * C, 2.0 * (x + C / 2.0 - left_limit)), "center"
        return C + 2 * side, "center"

    def card_span(self, row, p, text):
        """番号 p の箱に置く札の横の範囲 (x0, x1)（設計座標）"""
        C = self.C
        x = self.col_x(self.p_col(self._row_index[row], p))
        w = self._card_w(text, row, p)
        mode = self._card_room(row, p)[1]
        side = max(0.0, self.G - 2)
        if w <= C + 2 * side or mode == "center":
            return (x + C / 2.0 - w / 2.0, x + C / 2.0 + w / 2.0)
        if mode == "right":
            x0 = x - side
            return (x0, x0 + w)
        x1 = x + C + side
        return (x1 - w, x1)

    def zone_q_size(self):
        """範囲外の区画の「?」の大きさ（番号の字より小さくしない。箱からははみ出さない）"""
        return min(max(self.VS * 1.15, self.IS), self.C * 0.7)

    def note_size(self):
        """あふれの札・不等号の大きさ（箱の中の字と同じ。番号の字より小さくしない）"""
        return max(self.VS, self.IS)

    def _value_width(self, row, text, size):
        """箱の中の字の幅（to_str で引用符が付く行の数は、引用符込みの幅）"""
        mono = _is_ascii(text)
        if row is not None and row in self.quoted_at and _is_numeric(text):
            l, r, ql, qr = self._fonts.quote_ink(text, size, mono)
            return (r - l) + 2 * ((qr - ql) + _QUOTE_GAP * size)
        return self._fonts.width(text, size, mono=mono)

    def _drawn_plain_cards(self, row):
        """行の中で実際に描く字: (箱の字の集合, 札の (字, 番号) の集合)。

        箱の字は見える箱（col_of）の values と set の字。elide で隠れた箱の values は
        描かないので入れない（入れると、鍵に入らない隠れた値で見える字の大きさが変わり、
        同じ鍵で別の絵になる）。put / set / swap が触る箱は必ず見える箱。
        """
        r = self._row_index[row]
        plain = {r.texts[p] for p in r.col_of if r.in_range(p) and r.texts[p]}
        plain |= {e["text"] for e in self.events
                  if e["k"] == "set" and e.get("row") == row and e.get("text")}
        cards = {(e["text"], e["p"]) for e in self.events
                 if e["k"] == "put" and e.get("row") == row}
        return plain, cards

    def _drawn_texts(self, row):
        plain, cards = self._drawn_plain_cards(row)
        return plain | {t for t, _q in cards}

    def _row_sizes(self, row):
        """(行で揃える字の大きさ, 字ごとの入る大きさ)。

        行の中の字は揃えるが、1 つの長い字のために行全体を _VALUE_FLOOR（32px。value_size が
        それより小さければ value_size）未満へ縮めない。それ未満でしか入らない字は、その字
        だけを縮める（縮んだ字は p.audit() が小さすぎる字として報告する）。
        """
        memo = self.__dict__.setdefault("_vs_memo", {})
        v = memo.get(row)
        if v is None:
            plain, cards = self._drawn_plain_cards(row)
            fits = {}
            for t in plain:
                fits[t] = min(fits.get(t, math.inf), self._fit_size(row, t))
            for t, q in cards:
                fits[t] = min(fits.get(t, math.inf), self._fit_size(row, t, card=True, p=q))
            floor = min(self.VS, _VALUE_FLOOR) - 1e-9
            big = [f for f in fits.values() if f >= floor]
            v = memo[row] = (min(big) if big else self.VS, fits)
        return v

    def value_size_for(self, row, text):
        """箱の中の字の大きさ（行の中では揃える。_row_sizes を参照）"""
        if row is None:
            return self._fit_size(row, text)
        row_size, fits = self._row_sizes(row)
        f = fits.get(text)
        if f is None:
            f = self._fit_size(row, text)
        return min(row_size, f)

    def _fit_size(self, row, text, card=False, p=None):
        """1つの字が箱に入る大きさ。value_size で入らなければ縮める。
        札（put）は _VALUE_FLOOR までは字を縮め、その先は札を広げ（_card_room）、それでも
        入らなければさらに縮める。縮めた字は p.audit() が小さすぎる字として報告する"""
        inner = self.C - 2 * _BOX_STROKE - 6
        w = self._value_width(row, text, self.VS)
        if w <= inner or w <= 0:
            return self.VS
        if not card:
            return self.VS * inner / w
        size = max(min(self.VS, _VALUE_FLOOR), self.VS * inner / w)
        room = self._card_room(row, p)[0] - 2 * _BOX_STROKE - 0.12 * self.C
        w2 = self._value_width(row, text, size)
        if w2 > room:
            size = size * room / w2
        return size

    def _card_need(self, row, text):
        """札が字を縮めずに（_VALUE_FLOOR まで）収めるのに要る幅（設計座標。広げる上限は見ない）"""
        C = self.C
        inner = C - 2 * _BOX_STROKE - 6
        w = self._value_width(row, text, self.VS)
        size = self.VS if w <= inner else max(min(self.VS, _VALUE_FLOOR), self.VS * inner / w)
        return max(C, self._value_width(row, text, size) + 2 * _BOX_STROKE + 0.12 * C)

    # --- 7. 尺 ---

    def _finish_duration(self, duration):
        end = 0.0
        for e in self.events:
            k = e["k"]
            t1 = e["t"] + e["dur"]
            if k in ("mark", "unmark"):
                t1 = e["t"] + 0.2
            elif k == "to_str":
                t1 = e["t"] + 0.3
            elif k in ("read", "link"):
                t1 += 0.15
            end = max(end, t1)
        for fs in self.fills.values():
            for sp in fs["spills"]:
                end = max(end, sp["hit"] + sp["fall"])
            for ov in fs["over"]:
                end = max(end, ov.last + 0.45)
        for comps in self.compares.values():
            for c in comps:
                if c["rel1"] is not None:
                    end = max(end, c["rel1"])
        if self.freeze_t is not None:
            end = max(end, self.freeze_t)
        if duration is None:
            duration = end + 1.0
        else:
            # 尺より後に終わる出来事は映らない。黙って切らずに知らせる（freeze の後と同じ方針。
            # 余韻 — 着いた後の accent・こぼれ落ちる塊 — は切れてよい）
            for e in self.events:
                t1 = e["t"] + e["dur"]
                if e["k"] == "halt" and e["style"] == "freeze":
                    t1 = e["t"]                     # freeze は t で止まる（dur は使わない）
                if t1 > duration + 1e-9:
                    raise ValueError(
                        f"{self.fn}: 出来事（{e['k']}, {e['t']:g}〜{t1:g} 秒）が duration"
                        f"（{duration:g} 秒）を過ぎます。duration を延ばすか、"
                        f"duration=None（最後の出来事の終わり＋1 秒）にしてください")
        self.duration = float(duration)
        self.n_frames = max(1, int(math.floor(self.duration * self.fps + 0.5)))

    # --- 8. 文字の申告（p.audit() が読む）---

    def _collect_text_meta(self, proj):
        s = self.s
        metas = []

        def add(role, size, texts):
            metas.append({"role": role, "size": round(size * s, 2),
                          "decorated": self.halo is not None or self.rgba["bg"] is not None,
                          "texts": texts})

        labels = [r.label for r in self.rows if r.label]
        if labels:
            add("label", self.LS, labels)
        # 箱の中の字は描くものだけ（elide で隠れた箱の values は描かないので数えない）
        values = sorted({(r.name, t) for r in self.rows for t in self._drawn_texts(r.name)})
        if values:
            add("value", min(self.value_size_for(row, t) for row, t in values),
                sorted({t for _row, t in values}))
        idx = []
        for r in self.rows:
            if r.index:
                idx.extend(str(p + r.base) for p in r.col_of if r.in_range(p) or p in r.ghosts)
        if idx:
            add("index", self._index_size_min(idx), idx[:8])
        caps = [f"上限 {r.capacity}" for r in self.rows if r.capacity is not None]
        if caps:
            add("capacity", self.IS, caps)
        if any(fs["gaps"] for fs in self.fills.values()):
            add("count", self.IS, ["件数"])
        counts = [self.count_text(r) for r in self.rows if self.fills[r.name]["count"]]
        if counts:
            add("spill", self.note_size(), counts)
        if any(self.compares.values()):
            add("sign", self.note_size(), ["<", ">"])
        if any(lk["label"] for lk in self.links):
            add("link", self.IS, [lk["label"] for lk in self.links if lk["label"]])
        if any(r.zones for r in self.rows):
            add("zone", self.zone_q_size(), ["?"])
        self.text_meta = metas
        # 豆腐（フォントに無い字）の検出
        chars_main, chars_mono = set(), set()
        for m in metas:
            for t in m["texts"]:
                (chars_mono if _is_ascii(t) else chars_main).update(t)
        chars_main.update("上限?")
        chars_mono.update("0123456789+?<>=\"")
        missing = self._fonts.missing(sorted(chars_main), sorted(chars_mono))
        if missing:
            raise ValueError(
                f"{self.fn}: フォントに無い字があり、豆腐（□）で描かれます: "
                + " ".join(f"'{ch}'(U+{ord(ch):04X})" for ch in missing[:8])
                + "。その字を持つフォントを font= / mono_font= で指定してください")

    def _index_size_min(self, texts):
        room = self.C + self.G - 6
        widest = max(self._fonts.width(t, self.IS, mono=True) for t in texts)
        if widest <= room:
            return self.IS
        return max(min(self.IS, 22.0), self.IS * room / widest)

    def index_size_for(self, text):
        room = self.C + self.G - 6
        w = self._fonts.width(text, self.IS, mono=True)
        if w <= room:
            return self.IS
        return max(min(self.IS, 22.0), self.IS * room / w)

    def audit_meta(self):
        """p.audit() への文字の申告（framekit.text_meta。図の中で一番小さい字を申告する）。

        役ごと（ラベル・箱の中の字・番号・上限・件数・あふれの札・不等号・矢印の札・「?」）に
        実際の大きさ（size で縮めた倍率込み）・縁（halo）で label を作って渡す。
        """
        fonts = self._fonts
        halo = max(1, int(round(self.halo_w * self.s))) if self.halo is not None else 0
        sprites = []
        for m in self.text_meta:
            sample = max(m["texts"], key=len)
            mono = _is_ascii(sample)
            ref = fonts.mono if mono else fonts.main
            kw = {"size": max(1.0, m["size"]), "font": ref.path, "font_index": ref.index,
                  "missing": "ignore"}
            if not mono and fonts.main.coords is not None:
                kw["weight"] = self._weight
            if halo:
                kw["border"] = halo
                kw["border_color"] = "black"
            if self.rgba["bg"] is not None:
                kw["background"] = tuple(self.rgba["bg"])
            sprites.append(fk.label("slots", sample, **kw))
        labels = [r.label for r in self.rows if r.label]
        content = "slots: " + (labels[0].replace("\n", " ") if labels
                               else ", ".join(r.name for r in self.rows))
        return fk.text_meta(sprites, width=self.W, height=self.H, content=content)

    # --- 9. 鍵 ---

    def _build_key(self):
        rows = []
        for r in self.rows:
            vis = sorted(r.col_of)
            spec = {
                "name": r.name, "n": r.n, "label": r.label, "index": r.index,
                "base": r.base, "capacity": r.capacity, "elide": r.elide,
                "ghosts": sorted(r.ghosts), "offset": r.offset,
                "cols": [[p, r.col_of[p]] for p in vis],
                "values": [[p, r.texts[p]] for p in vis
                           if r.in_range(p) and r.texts[p] is not None],
            }
            # あふれの札の書式は、札を出す行だけ（あふれない行では効かない）
            if self.fills[r.name]["count"] is not None:
                spec["overflow_label"] = r.count_fmt
            rows.append(spec)
        events = []
        for e in self.events:
            ev = {k: v for k, v in e.items() if k != "seq"}
            events.append(ev)
        has_spill = any(fs["spills"] for fs in self.fills.values())
        self.key_spec = {
            "slots": _SLOTS_VER,
            "rows": rows,
            "events": events,
            "dims": {"cell": self.C, "gap": self.G, "row_gap": self.RG, "padding": self.PAD,
                     "label_size": self.LS, "value_size": self.VS, "index_size": self.IS},
            "colors": {k: (None if v is None else list(v)) for k, v in self.rgba.items()},
            "size": [self.W, self.H],
            "duration": self.duration,
            "seed": self.seed if has_spill else None,
        }

    # --- 10. 要約（テスト・確認用）---

    def state_at(self, t):
        if self.freeze_t is not None:
            t = min(t, self.freeze_t)
        out = {}
        for r in self.rows:
            fs = self.fills[r.name]
            landed = sum(1 for a in fs["landed"].values() if a <= t)
            hidden = sum(sum(1 for a in g["times"] if a <= t) for g in fs["gaps"].values())
            spill_drawn = sum(1 for sp in fs["spills"] if sp["start"] <= t)
            spill_alive = sum(1 for sp in fs["spills"]
                              if sp["start"] <= t < sp["hit"] + sp["fall"])
            out[r.name] = {"blocks_in_boxes": landed + hidden, "blocks_visible": landed,
                           "hidden_filled": hidden, "spill_drawn": spill_drawn,
                           "spill_alive": spill_alive,
                           "plus": sum(ov.plus(t) for ov in fs["over"]),
                           "overflow": sum(ov.arrived(t) for ov in fs["over"]),
                           "count_label": (self.count_text(r, t) if fs["count"] is not None
                                           and t >= fs["count"]["t0"] else None)}
        return out


# --- フォント ---

class _Fonts:
    """font（ラベル・字）と mono_font（数字・ASCII）の解決・計測。

    フォントは framekit.font_ref で解決する（鍵には内容指紋・書体番号・軸の値だけが入る）。
    """

    def __init__(self, fn, font, mono_font, weight):
        from scriptvedit.textimage import _import_pil
        self.fn = fn
        self.pil = _import_pil(fn)
        self.main = fk.font_ref(fn, font, 0, weight)
        mono_path = mono_font
        if mono_path is None:
            for cand in _MONO_CANDIDATES:
                if os.path.exists(cand):
                    mono_path = cand
                    break
        if mono_path is None:
            self.mono = self.main
        else:
            self.mono = fk.font_ref(fn, mono_path, 0, None)
        self.main_path = self.main.path
        self.mono_path = self.mono.path
        self._memo = {}

    def refs(self):
        return [self.main] if self.mono == self.main else [self.main, self.mono]

    def ffp(self):
        """金型の照合に使うフォントの指紋（font と mono_font）"""
        return f"{self.main.ffp}+{self.mono.ffp}"

    def font(self, size, mono=False):
        size = max(1, int(round(size)))
        key = (size, mono)
        f = self._memo.get(key)
        if f is None:
            ref = self.mono if mono else self.main
            f = _load_font(self.fn, self.pil, ref.path, ref.index, size, ref.coords)
            self._memo[key] = f
        return f

    def width(self, text, size, mono=None):
        if mono is None:
            mono = _is_ascii(text)
        return float(self.font(size, mono).getlength(text))

    def vcenter(self, size, mono):
        """中央揃えのときのベースラインの位置（中心からの下向きの距離）"""
        key = ("vc", max(1, int(round(size))), mono)
        v = self._memo.get(key)
        if v is None:
            f = self.font(size, mono)
            ref = "0123456789" if mono else "0Aあ漢"
            _l, top, _r, bottom = f.getbbox(ref, anchor="ls")
            v = self._memo[key] = -(top + bottom) / 2.0
        return v

    def quote_ink(self, text, size, mono):
        """(字の墨の左, 右, 引用符の墨の左, 右)。原点（左のベースライン）からの px"""
        key = ("q", text, max(1, int(round(size))), mono)
        v = self._memo.get(key)
        if v is None:
            f = self.font(size, mono)
            l, _t, r, _b = f.getbbox(text, anchor="ls")
            ql, _qt, qr, _qb = f.getbbox('"', anchor="ls")
            v = self._memo[key] = (float(l), float(r), float(ql), float(qr))
        return v

    def missing(self, chars_main, chars_mono):
        out = list(_missing_glyphs(self.font(32, False), chars_main, self.main.path,
                                   self.main.index))
        out += [c for c in _missing_glyphs(self.font(32, True), chars_mono,
                                           self.mono.path, self.mono.index) if c not in out]
        return out


# --- 描画 ---

class _Renderer:
    """_Plan を 1 コマずつ描く（numpy・OpenCV・Pillow）。

    動かない層（_base）は 1 回だけ描く。毎コマの描画は「前のコマで描いた所を
    動かない層から戻す → 動く物を描く → 描いた所だけ straight alpha の出力へ写す」で、
    キャンバス全体の写しと変換をしない（32px 四方のタイル単位で汚れを追う）。
    """

    _TILE = 32

    def __init__(self, plan):
        d = fk.need("slots")
        np = d.np
        self.np, self.cv2 = np, d.cv2
        self.p = plan
        self.pil = plan._fonts.pil
        self.W, self.H = plan.W, plan.H
        self._sprites = {}
        self._shapes = {}
        self._static = fk.layer_cache()   # 動かない層（premultiplied float32・書き込み不可）
        self._last = None            # (静止の鍵, 出力)
        self._intervals = self._active_intervals()
        self._iv_starts = [a for a, _b in self._intervals]
        self._iv_ends = [b for _a, b in self._intervals]
        self.col = {k: (None if v is None else
                        np.array([v[0] / 255.0, v[1] / 255.0, v[2] / 255.0], np.float32))
                    for k, v in plan.rgba.items()}
        self.alpha_of = {k: (1.0 if v is None else v[3] / 255.0) for k, v in plan.rgba.items()}
        th = (self.H + self._TILE - 1) // self._TILE
        tw = (self.W + self._TILE - 1) // self._TILE
        self._dirty = np.zeros((th, tw), bool)
        self._prev_dirty = np.zeros((th, tw), bool)
        self._work = None
        self._out = None
        self._out_dim = 1.0
        self._track = False
        # swap する箱の番号は動かない層に入れず、下の弧が通る間だけ薄くする
        self._swap_index = {(row, q) for row, ans in plan.anims.items() for an in ans
                            if an["k"] == "swap" for q in (an["i"], an["j"])
                            if plan._row_index[row].index}

    # --- 静止の判定（何も動いていないコマは前のコマを使い回す）---

    def _active_intervals(self):
        p = self.p
        iv = []
        for e in p.events:
            k = e["k"]
            if k == "fill":
                continue
            t0, t1 = e["t"], e["t"] + e["dur"]
            if k in ("mark", "unmark"):
                t1 = t0 + 0.2
            elif k == "to_str":
                t1 = t0 + 0.3
            elif k in ("read", "link"):
                t1 = t1 + 0.15
            elif k == "halt" and e["style"] == "freeze":
                continue
            iv.append((t0, t1))
        for fs in p.fills.values():
            for fl in fs["flights"]:
                iv.append((fl["start"], fl["arrive"]))
            for sp in fs["spills"]:
                iv.append((sp["start"], sp["hit"] + sp["fall"]))
            for ov in fs["over"]:
                iv.append((ov.start, ov.last + 0.45))
                pt = ov.plus_times()
                if pt is not None:
                    iv.append((pt[0], pt[1] + 0.15))
            for g in fs["gaps"].values():
                if g["times"]:
                    iv.append((g["times"][0] - 0.2, g["times"][-1]))
        for r in p.rows:
            for z in r.zones.values():
                iv.append((z["appear"], z["appear"] + 0.25))
                if z["accent"] is not None:
                    iv.append((z["accent"], z["accent"] + 0.15))
            for t in r.ghost_accent.values():
                iv.append((t, t + 0.15))
            for seg in p.pointers.get(r.name, []):
                iv.append((seg["t0"], seg["t1"] + 0.15))
        for comps in p.compares.values():
            for c in comps:
                if c["rel0"] is not None:
                    iv.append((c["rel0"], c["rel1"]))
        iv.sort()
        merged = []
        for a, b in iv:
            if merged and a <= merged[-1][1] + 1e-9:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        return merged

    def _still_key(self, t):
        """t が動きの区間の外なら、その静止区間の番号（同じ番号のコマは同じ絵）。中なら None"""
        k = bisect.bisect_right(self._iv_starts, t)
        if k > 0 and t <= self._iv_ends[k - 1] + 1e-9:
            return None
        return k

    # --- 1 コマ ---

    def frame_at(self, t):
        p = self.p
        if p.freeze_t is not None:
            t = min(t, p.freeze_t)
        key = self._still_key(t)
        if key is not None and self._last is not None and self._last[0] == key:
            return self._last[1]
        self._last = None
        base = self._base()
        if self._work is None:
            self._work = base.copy()
            self._out = self._to_straight(base, 1.0)
            self._out_dim = 1.0
        canvas = self._work
        # 前のコマで描いた所を動かない層へ戻す
        for y0, y1, x0, x1 in self._runs(self._prev_dirty):
            canvas[y0:y1, x0:x1] = base[y0:y1, x0:x1]
        self._dirty[:] = False
        self._track = True
        for r in p.rows:
            self._draw_row(canvas, r, t)
        for lk in p.links:
            self._draw_link(canvas, lk, t)
        for r in p.rows:
            self._draw_pointer(canvas, r, t)
            self._draw_spill(canvas, r, t)
        self._track = False
        # halt(dim) を2回呼んでも 0.45 より薄くしない（掛け合わせず、一番進んだものを取る）
        dim = 1.0
        for (t0, dur) in p.halts_dim:
            dim = min(dim, 1.0 - (1.0 - _DIM_ALPHA) * _ease(_prog(t, t0, dur)))
        if abs(dim - self._out_dim) > 1e-9:
            self._out = self._to_straight(canvas, dim)
            self._out_dim = dim
        else:
            both = self._dirty | self._prev_dirty
            for y0, y1, x0, x1 in self._runs(both):
                self._out[y0:y1, x0:x1] = self._to_straight(canvas[y0:y1, x0:x1], dim)
        self._prev_dirty, self._dirty = self._dirty, self._prev_dirty
        out = self._out
        if key is not None:
            out = out.copy()
            self._last = (key, out)
        return out

    def _runs(self, mask):
        """汚れたタイルの横の連なりを (y0, y1, x0, x1) の px で返す"""
        np = self.np
        T = self._TILE
        out = []
        rows = np.flatnonzero(mask.any(axis=1))
        for ty in rows:
            line = mask[ty]
            d = np.diff(np.concatenate(([0], line.view(np.int8), [0])))
            starts = np.flatnonzero(d == 1)
            ends = np.flatnonzero(d == -1)
            y0, y1 = ty * T, min(self.H, (ty + 1) * T)
            for a, b in zip(starts, ends):
                out.append((y0, y1, a * T, min(self.W, b * T)))
        return out

    def _to_straight(self, canvas, dim):
        """premultiplied float32 → straight alpha の uint8（dim は全体の不透明度）"""
        np = self.np
        a = canvas[..., 3]
        out = np.zeros(canvas.shape, np.uint8)
        nz = a > (0.25 / 255.0)
        if nz.any():
            av = a[nz]
            rgb = canvas[..., :3][nz] / av[:, None]
            out[..., :3][nz] = np.clip(rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
            out[..., 3][nz] = np.clip(av * (255.0 * dim) + 0.5, 0, 255).astype(np.uint8)
        return out

    # --- 合成の小道具 ---

    def _over(self, canvas, x0, y0, mask, color_key=None, rgb=None, alpha=1.0):
        """mask（float32, 0..1）を色で canvas（premultiplied）へ重ねる。x0, y0 は整数"""
        if alpha <= 0.0 or mask is None:
            return
        np = self.np
        if rgb is None:
            rgb = self.col[color_key]
            alpha = alpha * self.alpha_of[color_key]
        h, w = mask.shape
        cx0, cy0 = max(0, x0), max(0, y0)
        cx1, cy1 = min(self.W, x0 + w), min(self.H, y0 + h)
        if cx0 >= cx1 or cy0 >= cy1:
            return
        if self._track:
            T = self._TILE
            self._dirty[cy0 // T:(cy1 - 1) // T + 1, cx0 // T:(cx1 - 1) // T + 1] = True
        m = mask[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0]
        if alpha != 1.0:
            m = m * np.float32(alpha)
        roi = canvas[cy0:cy1, cx0:cx1]
        roi *= (1.0 - m)[..., None]
        roi[..., :3] += m[..., None] * rgb
        roi[..., 3] += m

    def _raster(self, bbox, draw):
        """bbox（キャンバス px）の範囲を 4 倍で描いて縮めたマスク (x0, y0, mask)"""
        np, cv2 = self.np, self.cv2
        x0 = int(math.floor(bbox[0])) - 1
        y0 = int(math.floor(bbox[1])) - 1
        x1 = int(math.ceil(bbox[2])) + 1
        y1 = int(math.ceil(bbox[3])) + 1
        w, h = max(1, x1 - x0), max(1, y1 - y0)
        img = np.zeros((h * _SS, w * _SS), np.uint8)
        draw(img, x0, y0)
        m = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
        return x0, y0, m.astype(np.float32) * np.float32(1.0 / 255.0)

    def _pts(self, pts, x0, y0):
        np = self.np
        a = (np.asarray(pts, np.float64) - (x0, y0)) * _SS
        return np.round(a * 16).astype(np.int32)

    def _poly_mask(self, polys, holes=()):
        cv2 = self.cv2
        allp = [pt for poly in polys for pt in poly]
        xs = [q[0] for q in allp]
        ys = [q[1] for q in allp]

        def draw(img, x0, y0):
            for poly in polys:
                cv2.fillPoly(img, [self._pts(poly, x0, y0)], 255, cv2.LINE_AA, 4)
            for poly in holes:
                cv2.fillPoly(img, [self._pts(poly, x0, y0)], 0, cv2.LINE_AA, 4)

        return self._raster((min(xs), min(ys), max(xs), max(ys)), draw)

    def _stroke_mask(self, paths, width, closed=False):
        cv2 = self.cv2
        allp = [pt for path in paths for pt in path]
        xs = [q[0] for q in allp]
        ys = [q[1] for q in allp]
        hw = width / 2.0 + 1

        def draw(img, x0, y0):
            thick = max(1, int(round(width * _SS)))
            for path in paths:
                pts = self._pts(path, x0, y0)
                if len(path) == 1:
                    c = (int(pts[0][0]), int(pts[0][1]))
                    cv2.circle(img, c, int(round(width * _SS / 2.0 * 16)), 255, -1,
                               cv2.LINE_AA, 4)
                else:
                    cv2.polylines(img, [pts], closed, 255, thick, cv2.LINE_AA, 4)

        return self._raster((min(xs) - hw, min(ys) - hw, max(xs) + hw, max(ys) + hw), draw)

    def _cached_at(self, key, X, Y, make):
        """(X, Y) の端数（1/8 px 刻み）ごとに形を作り置きし、(x0, y0, mask) を返す。

        make(fx, fy) は原点 (fx, fy) の局所座標で形を描く関数。
        """
        ix, iy = math.floor(X), math.floor(Y)
        fx = round((X - ix) * 8) / 8.0
        fy = round((Y - iy) * 8) / 8.0
        k = key + (fx, fy)
        m = self._shapes.get(k)
        if m is None:
            m = self._shapes[k] = make(fx, fy)
        return ix + m[0], iy + m[1], m[2]

    # --- 形 ---

    @staticmethod
    def _rrect(x0, y0, x1, y1, r, seg=10):
        r = max(0.0, min(r, (x1 - x0) / 2.0, (y1 - y0) / 2.0))
        pts = []
        for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0),
                           (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
            for k in range(seg + 1):
                a = math.radians(a0 + 90.0 * k / seg)
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        return pts

    def _ring(self, x0, y0, x1, y1, r, width):
        outer = self._rrect(x0, y0, x1, y1, r)
        inner = self._rrect(x0 + width, y0 + width, x1 - width, y1 - width, max(0.0, r - width))
        return self._poly_mask([outer], [inner])

    def _dashed(self, x0, y0, x1, y1, r, width):
        """角丸の破線の枠（線の中心は枠の内側 width/2）"""
        hw = width / 2.0
        path = self._rrect(x0 + hw, y0 + hw, x1 - hw, y1 - hw, max(0.0, r - hw), seg=12)
        path.append(path[0])
        seglens = [math.dist(path[k], path[k + 1]) for k in range(len(path) - 1)]
        total = sum(seglens)
        C = self.p.L(self.p.C)
        target = max(6.0, 0.17 * C)
        n = max(4, int(round(total / (target * 1.7))))
        period = total / n
        dash = period * 0.58
        dashes = []
        # 破線は角の外から始める（角に切れ目が来ないよう、位相を半分ずらす）
        start = -dash / 2.0
        for k in range(n):
            a = start + k * period
            dashes.extend(self._sub_path(path, seglens, total, a, a + dash))
        return self._stroke_mask(dashes, width)

    @staticmethod
    def _sub_path(path, seglens, total, a, b):
        """折れ線の弧長 a..b の部分（閉じた周をまたぐときは 2 本に分ける）"""
        out = []
        if a < 0:
            out.extend(_Renderer._sub_path(path, seglens, total, total + a, total))
            a = 0.0
        if b > total:
            out.extend(_Renderer._sub_path(path, seglens, total, 0.0, b - total))
            b = total
        pts = []
        acc = 0.0
        for k, L in enumerate(seglens):
            s0, s1 = acc, acc + L
            if s1 >= a and s0 <= b and L > 0:
                u0 = max(0.0, (a - s0) / L)
                u1 = min(1.0, (b - s0) / L)
                p0, p1 = path[k], path[k + 1]
                q0 = (p0[0] + (p1[0] - p0[0]) * u0, p0[1] + (p1[1] - p0[1]) * u0)
                q1 = (p0[0] + (p1[0] - p0[0]) * u1, p0[1] + (p1[1] - p0[1]) * u1)
                if not pts:
                    pts.append(q0)
                pts.append(q1)
            acc = s1
        if len(pts) >= 2:
            out.append(pts)
        return out

    # --- 文字 ---

    def _text_sprite(self, text, size, mono, fx, fy, halo):
        """文字の塗りと縁のマスク。原点（左のベースライン）の小数部 fx, fy は 1/4 px 刻み"""
        key = (text, int(round(size)), mono, fx, fy, halo)
        sp = self._sprites.get(key)
        if sp is not None:
            return sp
        np, cv2 = self.np, self.cv2
        Image, ImageDraw = self.pil["Image"], self.pil["ImageDraw"]
        font = self.p._fonts.font(size, mono)
        hw = int(round(halo))
        l, t, r, b = font.getbbox(text, anchor="ls", stroke_width=hw)
        pad = 2
        w, h = int(r - l) + 2 * pad + 1, int(b - t) + 2 * pad + 1
        ox, oy = -l + pad, -t + pad
        fill = Image.new("L", (w, h), 0)
        ImageDraw.Draw(fill).text((ox, oy), text, font=font, fill=255, anchor="ls")
        mf = np.asarray(fill, np.float32) * np.float32(1.0 / 255.0)
        mh = None
        if hw > 0:
            st = Image.new("L", (w, h), 0)
            ImageDraw.Draw(st).text((ox, oy), text, font=font, fill=255, anchor="ls",
                                    stroke_width=hw, stroke_fill=255)
            mh = np.asarray(st, np.float32) * np.float32(1.0 / 255.0)
        if fx or fy:
            # Pillow は小数の位置を丸めるので、端数は双一次補間でずらす（動く字のがたつき防止）
            M = np.float32([[1, 0, fx], [0, 1, fy]])
            mf = cv2.warpAffine(mf, M, (w, h), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            if mh is not None:
                mh = cv2.warpAffine(mh, M, (w, h), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        sp = (mf, mh, ox, oy)
        self._sprites[key] = sp
        return sp

    def _text(self, canvas, text, cx, cy, size, color_key=None, *, rgb=None, alpha=1.0,
              mono=None, align="center", clip=None):
        """文字を置く（cy は字の縦の中心）。

        align: 'center' は cx が中央、'left' は cx が左端、'origin' は cx が原点（左のベースライン）
        """
        if not text or alpha <= 0:
            return
        if mono is None:
            mono = _is_ascii(text)
        p = self.p
        size = max(1, int(round(size)))
        if align == "center":
            bx = cx - float(p._fonts.font(size, mono).getlength(text)) / 2.0
        else:
            bx = cx
        by = cy + p._fonts.vcenter(size, mono)
        ix, iy = math.floor(bx), math.floor(by)
        fx = round((bx - ix) * _SUBPX) / _SUBPX
        fy = round((by - iy) * _SUBPX) / _SUBPX
        if fx >= 1.0:
            ix, fx = ix + 1, 0.0
        if fy >= 1.0:
            iy, fy = iy + 1, 0.0
        halo = max(1, int(round(p.L(p.halo_w)))) if p.halo is not None else 0
        mf, mh, ox, oy = self._text_sprite(text, size, mono, fx, fy, halo)
        x0, y0 = ix - ox, iy - oy
        if clip is not None:
            mf, mh, x0, y0 = self._clip_sprite(mf, mh, x0, y0, clip)
            if mf is None:
                return
        if mh is not None:
            self._over(canvas, x0, y0, mh, "halo", alpha=alpha)
        self._over(canvas, x0, y0, mf, color_key, rgb=rgb, alpha=alpha)

    def _clip_sprite(self, mf, mh, x0, y0, clip):
        cx0, cy0, cx1, cy1 = [int(round(v)) for v in clip]
        h, w = mf.shape
        a0, b0 = max(x0, cx0), max(y0, cy0)
        a1, b1 = min(x0 + w, cx1), min(y0 + h, cy1)
        if a0 >= a1 or b0 >= b1:
            return None, None, 0, 0
        sl = (slice(b0 - y0, b1 - y0), slice(a0 - x0, a1 - x0))
        return mf[sl], (mh[sl] if mh is not None else None), a0, b0

    # --- 動かない層 ---

    def _base(self):
        """動かない層（framekit.layer_cache。版は図ごとに1つなので最初の1回だけ描く）"""
        return self._static("base", self._draw_base)

    def _draw_base(self):
        np = self.np
        p = self.p
        canvas = np.zeros((self.H, self.W, 4), np.float32)
        if p.rgba["bg"] is not None:
            canvas[..., :3] = self.col["bg"] * self.alpha_of["bg"]
            canvas[..., 3] = self.alpha_of["bg"]
        C = p.C
        dyn = self._dynamic_frames()
        for r in p.rows:
            # ラベル
            if r.label:
                lines = r.label.split("\n")
                lh = p.LS * 1.25
                cy0 = r.y + C / 2.0 - lh * (len(lines) - 1) / 2.0
                for k, line in enumerate(lines):
                    self._text(canvas, line, p.X(0.0), p.Y(cy0 + k * lh), p.L(p.LS), "fg",
                               align="left")
            # 箱の枠・ghost の破線・番号
            for q, c in sorted(r.col_of.items()):
                x = p.col_x(c)
                if r.in_range(q):
                    if (r.name, q) not in dyn:
                        self._box_frame(canvas, x, r.y, "fg")
                elif q in r.ghosts:
                    self._ghost_frame(canvas, x, r.y, "dim")
                if (r.index and (r.in_range(q) or q in r.ghosts)
                        and (r.name, q) not in self._swap_index):
                    self._index_label(canvas, r, q, 1.0)
            # 畳んだ区間の「…」（帯の伸びる行は毎コマ描く）
            for c, _lo, _hi in r.ellipses:
                if c not in p.fills[r.name]["gaps"]:
                    self._dots(canvas, r, c, 1.0)
            # 上限の線と「上限 N」
            if r.capacity is not None:
                self._cap_line(canvas, r, "line", 1.0)
                self._text(canvas, f"上限 {r.capacity}", p.X(p.line_x(r)),
                           p.Y(p.cap_label_y(r)), p.L(p.IS), "line")
        return canvas

    def _index_label(self, canvas, r, q, alpha):
        # ghost の番号も muted（「21 個目には相手がいない」の 21 は読ませたい字。dim では
        # 黒い縁の上で 2.5:1 ほどしかコントラストが無い。実在しないことは点線の箱で示す）
        p = self.p
        s = str(q + r.base)
        self._text(canvas, s, p.X(p.col_x(r.col_of[q]) + p.C / 2.0),
                   p.Y(r.y + p.C + p.idx_gap + p.idx_h / 2.0), p.L(p.index_size_for(s)),
                   "muted", alpha=alpha, mono=True)

    def _dynamic_frames(self):
        """毎コマ描く箱（compare で持ち上がる箱）"""
        out = set()
        for row, comps in self.p.compares.items():
            for c in comps:
                out.update(((row, c["i"]), (row, c["j"])))
        return out

    def _box_frame(self, canvas, x, y, color_key=None, *, rgb=None, alpha=1.0, extra=0.0,
                   dy=0.0):
        """箱の枠（x, y は設計座標の左上。dy はキャンバス px の持ち上げ）"""
        p = self.p
        X0, Y0 = p.X(x), p.Y(y) + dy
        size = p.L(p.C)
        w = p.L(_BOX_STROKE)
        rad = p.L(0.10 * p.C)

        def make(fx, fy):
            return self._ring(fx - extra, fy - extra, fx + size + extra, fy + size + extra,
                              rad + extra, w + 2 * extra)

        x0, y0, m = self._cached_at(("ring", round(size, 3), extra), X0, Y0, make)
        self._over(canvas, x0, y0, m, color_key, rgb=rgb, alpha=alpha)

    def _ghost_frame(self, canvas, x, y, color_key, alpha=1.0, extra=0.0):
        p = self.p
        X0, Y0 = p.X(x), p.Y(y)
        size = p.L(p.C)

        def make(fx, fy):
            return self._dashed(fx - extra, fy - extra, fx + size + extra, fy + size + extra,
                                p.L(0.10 * p.C) + extra, p.L(_BOX_STROKE) + 2 * extra)

        x0, y0, m = self._cached_at(("dash", round(size, 3), extra), X0, Y0, make)
        self._over(canvas, x0, y0, m, color_key, alpha=alpha)

    def _cap_line(self, canvas, r, color_key, alpha, extra=0.0):
        p = self.p
        C = p.C
        key = ("cap", r.name, extra)
        m = self._shapes.get(key)
        if m is None:
            lx = p.line_x(r)
            hw = p.L(_LINE_W) / 2.0 + extra
            x = p.X(lx)
            y0 = p.Y(r.y - p.cap_ext) - extra
            y1 = p.Y(r.y + C + p.cap_ext) + extra
            m = self._shapes[key] = self._poly_mask(
                [self._rrect(x - hw, y0, x + hw, y1, hw)])
        self._over(canvas, m[0], m[1], m[2], color_key, alpha=alpha)

    def _dots(self, canvas, r, c, alpha):
        """畳んだ区間の「…」（字ではなく 3 つの点。字の「…」はベースラインに乗って低く見える）"""
        p = self.p
        C = p.C
        key = ("dots", r.name, c)
        m = self._shapes.get(key)
        if m is None:
            cx, cy = p.col_x(c) + C / 2.0, r.y + C / 2.0
            rad = max(2.0, 0.05 * C)
            step = 0.2 * C
            m = self._shapes[key] = self._stroke_mask(
                [[(p.X(cx + k * step), p.Y(cy))] for k in (-1, 0, 1)], p.L(2 * rad))
        self._over(canvas, m[0], m[1], m[2], "muted", alpha=alpha)

    # --- 行の動く部分 ---

    def _draw_row(self, canvas, r, t):
        p = self.p
        C = p.C
        name = r.name
        lift = self._lifts(r, t)
        busy = self._busy_now(r, t)
        # 範囲外の区画（斜線と「?」）
        for zp, z in sorted(r.zones.items()):
            a = _ease(_prog(t, z["appear"], 0.25))
            if a <= 0:
                continue
            acc = 0.0 if z["accent"] is None else _prog(t, z["accent"], 0.12)
            self._zone(canvas, r, zp, a, acc)
        # 印（塗り）
        for q in r.col_of:
            if not r.in_range(q):
                continue
            lvl, rgba, style = self._mark_level(name, q, t)
            if lvl > 0 and style == "fill":
                x = p.col_x(r.col_of[q])
                inset = _BOX_STROKE + 1.0
                size = p.L(C - 2 * inset)

                def make(fx, fy, size=size):
                    return self._poly_mask([self._rrect(fx, fy, fx + size, fy + size,
                                                        p.L(0.07 * C))])

                x0, y0, m = self._cached_at(("mfill", round(size, 3)), p.X(x + inset),
                                            p.Y(r.y + inset + lift.get(q, 0.0)), make)
                self._over(canvas, x0, y0, m,
                           rgb=self.np.array(rgba[:3], self.np.float32) / 255.0,
                           alpha=0.45 * lvl * rgba[3] / 255.0)
        # 持ち上がる箱の枠（compare の対象は動かない層に入れず毎コマ描く）
        for q in sorted({q for (row, q) in self._dynamic_frames() if row == name}):
            self._box_frame(canvas, p.col_x(r.col_of[q]), r.y, "fg",
                            dy=p.L(lift.get(q, 0.0)))
        # 塊（着いたもの）
        fs = p.fills[name]
        for q, a in fs["landed"].items():
            if a <= t and q in r.col_of:
                x = p.col_x(r.col_of[q]) + C / 2.0
                self._block(canvas, p.X(x), p.Y(r.y + C / 2.0 + lift.get(q, 0.0)), 0.0,
                            "fill", 1.0)
        # 畳んだ区間の帯と件数
        for c in fs["gaps"]:
            self._band(canvas, r, c, t)
        # 中身（字・札）
        for q in sorted(r.col_of):
            if not r.in_range(q) or q in busy:
                continue
            content = p.content_at(name, q, t)
            if content[0] is None and not content[1]:
                continue
            x = p.col_x(r.col_of[q]) + C / 2.0
            self._content(canvas, r, content, x, r.y + C / 2.0 + lift.get(q, 0.0), t, q=q)
        # swap する箱の番号（下の弧が通る間は薄く）
        for (row, q) in self._swap_index:
            if row != name:
                continue
            a = 1.0
            for an in p.anims[name]:
                if an["k"] == "swap" and q in (an["i"], an["j"]) and an["t0"] <= t < an["t1"]:
                    a = min(a, 1.0 - self._swap_cover(r, an, t))
            self._index_label(canvas, r, q, a)
        # 中身を動かす出来事（compare の持ち上げが残っていれば、それを持ち越して描く）
        for an in p.anims[name]:
            if an["t0"] <= t < an["t1"]:
                self._anim(canvas, r, an, t, lift)
        # 印（枠）
        for q in r.col_of:
            if not r.in_range(q):
                continue
            lvl, rgba, style = self._mark_level(name, q, t)
            if lvl > 0 and style == "frame":
                self._box_frame(canvas, p.col_x(r.col_of[q]), r.y,
                                rgb=self.np.array(rgba[:3], self.np.float32) / 255.0,
                                alpha=lvl * rgba[3] / 255.0, extra=p.L(1.0),
                                dy=p.L(lift.get(q, 0.0)))
        # ghost の accent
        for g, ta in r.ghost_accent.items():
            if g in r.col_of and t >= ta:
                self._ghost_frame(canvas, p.col_x(r.col_of[g]), r.y, "accent",
                                  alpha=_prog(t, ta, 0.12), extra=p.L(0.75))
        # 上限の線の accent（超えた瞬間だけ）
        if r.capacity is not None and fs["over"]:
            hits = [h for h in (ov.last_hit_le(t) for ov in fs["over"]) if h is not None]
            if hits:
                d = t - max(hits)
                lvl = 1.0 if d <= 0.12 else max(0.0, 1.0 - (d - 0.12) / 0.3)
                if lvl > 0:
                    self._cap_line(canvas, r, "accent", lvl, extra=p.L(0.75))
        # 比べた字の枠と不等号
        for c in p.compares[name]:
            self._compare_marks(canvas, r, c, t, lift)

    def _busy_now(self, r, t):
        out = set()
        for an in self.p.anims[r.name]:
            if an["t0"] <= t < an["t1"]:
                if an["k"] == "swap":
                    out.update((an["i"], an["j"]))
                else:
                    out.add(an["p"])
        return out

    def _lifts(self, r, t):
        out = {}
        p = self.p
        for c in p.compares[r.name]:
            if t < c["t0"]:
                continue
            up = _ease(_prog(t, c["t0"], 0.35 * c["dur"]))
            if c["rel0"] is not None and t >= c["rel0"]:
                up *= 1.0 - _ease(_prog(t, c["rel0"], c["rel1"] - c["rel0"]))
            if up > 0:
                for q in (c["i"], c["j"]):
                    out[q] = min(out.get(q, 0.0), -p.lift * up)
        return out

    def _mark_level(self, row, q, t):
        evs = self.p.marks.get((row, q))
        if not evs:
            return 0.0, None, None
        last_mark = None
        state = None
        for (te, kind, rgba, style) in evs:
            if te > t:
                break
            if kind == "mark":
                last_mark = (rgba, style)
                state = ("on", te)
            else:
                state = ("off", te)
        if last_mark is None or state is None:
            return 0.0, None, None
        u = _ease(_prog(t, state[1], 0.2))
        lvl = u if state[0] == "on" else 1.0 - u
        return lvl, last_mark[0], last_mark[1]

    def _quote_level(self, row, t):
        ts = self.p.quoted_at.get(row)
        if ts is None or t < ts:
            return 0.0
        return _ease(_prog(t, ts, 0.3))

    def _card(self, canvas, X, Y, w, alpha=1.0, clip=None):
        """札（箱と同じ形の地と枠。字が長ければ横に広い）を中心 (X, Y) px に描く"""
        p = self.p
        h = p.L(p.C)
        rad = p.L(0.10 * p.C)
        bw = p.L(_BOX_STROKE)

        def make_plate(fx, fy):
            return self._poly_mask([self._rrect(fx, fy, fx + w, fy + h, rad)])

        def make_ring(fx, fy):
            return self._ring(fx, fy, fx + w, fy + h, rad, bw)

        for kind, make, color in (("cardp", make_plate, "card"), ("cardr", make_ring, "fg")):
            x0, y0, m = self._cached_at((kind, round(w, 3), round(h, 3)),
                                        X - w / 2.0, Y - h / 2.0, make)
            if clip is not None:
                m, _mh, x0, y0 = self._clip_sprite(m, None, x0, y0, clip)
                if m is None:
                    continue
            self._over(canvas, x0, y0, m, color, alpha=alpha)

    def _content(self, canvas, r, content, cx, cy, t, alpha=1.0, clip=None, plate=0.0, q=None):
        """箱の中身（札と字）を設計座標の中心 (cx, cy) に描く。

        plate は字の後ろに敷く地の不透明度（swap で番号の上を通るとき、下の字を隠す）。
        q: 札を置く箱の番号（行の端の札は外側へ伸びるので、札と字の中心がずれる）。
        """
        p = self.p
        text, card = content
        if card:
            if q is not None:
                x0, x1 = p.card_span(r.name, q, text)
                cx += (x0 + x1) / 2.0 - (p.col_x(p.p_col(r, q)) + p.C / 2.0)
                w = x1 - x0
            else:
                w = p._card_w(text, r.name)
            self._card(canvas, p.X(cx), p.Y(cy), p.L(w), alpha=alpha, clip=clip)
        X, Y = p.X(cx), p.Y(cy)
        if not card and plate > 0.0 and text:
            # 地は字の大きさ（箱の内側いっぱいにすると、着く前後に箱の枠へ掛かって枠が暗くなる）
            vs = p.value_size_for(r.name, text)
            pw = p.L(min(p.C - 2 * _BOX_STROKE - 2, p._value_width(r.name, text, vs) + 0.5 * vs))
            ph = p.L(min(p.C - 2 * _BOX_STROKE - 2, vs * 1.4))

            def make(fx, fy):
                return self._poly_mask([self._rrect(fx, fy, fx + pw, fy + ph,
                                                    p.L(0.07 * p.C))])

            x0, y0, m = self._cached_at(("plate", round(pw, 3), round(ph, 3)),
                                        X - pw / 2.0, Y - ph / 2.0, make)
            self._over(canvas, x0, y0, m, "card", alpha=alpha * plate)
        if not text:
            return
        mono = _is_ascii(text)
        size = p.L(p.value_size_for(r.name, text))
        self._text(canvas, text, X, Y, size, "fg", alpha=alpha, mono=mono, clip=clip)
        qlvl = self._quote_level(r.name, t) if _is_numeric(text) else 0.0
        if qlvl > 0:
            fonts = p._fonts
            adv = float(fonts.font(size, mono).getlength(text))
            origin = X - adv / 2.0
            l, rr, ql, qr = fonts.quote_ink(text, size, mono)
            g = _QUOTE_GAP * size
            slide = (1.0 - qlvl) * size * 0.2
            # 引用符は墨（見える形）の端どうしで並べる（等幅の字送りのままだと離れすぎる）
            left_origin = origin + l - g - qr - slide
            right_origin = origin + rr + g - ql + slide
            for qo in (left_origin, right_origin):
                self._text(canvas, '"', qo, Y, size, "fg", alpha=alpha * qlvl, mono=mono,
                           align="origin", clip=clip)

    def _anim(self, canvas, r, an, t, lift=None):
        """put / set / swap の動き。lift は箱ごとの持ち上げ（compare の下ろし途中。設計座標）"""
        p = self.p
        C = p.C
        k = an["k"]
        lift = lift if lift is not None else self._lifts(r, t)
        u = _ease(_prog(t, an["t0"], an["t1"] - an["t0"]))
        if k == "put":
            q = an["p"]
            x = p.col_x(r.col_of[q]) + C / 2.0
            cy = r.y + C / 2.0 + lift.get(q, 0.0)
            if an["old"][0] is not None or an["old"][1]:
                self._content(canvas, r, an["old"], x, cy, t, alpha=1.0 - _clamp01(u / 0.4),
                              q=q)
            y = cy - 1.1 * C * (1.0 - u)
            self._content(canvas, r, an["new"], x, y, t, alpha=_clamp01(u / 0.35), q=q)
        elif k == "set":
            q = an["p"]
            x = p.col_x(r.col_of[q]) + C / 2.0
            ly = lift.get(q, 0.0)
            cy = r.y + C / 2.0 + ly
            card = an["old"][1]
            if card:
                # 札はそのまま（字だけが入れ替わる）。札の中心は行の端なら箱からずれる
                x0c, x1c = p.card_span(r.name, q, an["new"][0] or an["old"][0])
                x = (x0c + x1c) / 2.0
                self._card(canvas, p.X(x), p.Y(cy), p.L(x1c - x0c))
                half = (x1c - x0c) / 2.0 - _BOX_STROKE - 1.0
            else:
                half = C / 2.0 - _BOX_STROKE - 1.0
            inset = _BOX_STROKE + 1.0
            # 切り抜きは箱と一緒に持ち上げる（下ろし途中の箱の下へ字が漏れない）
            clip = (p.X(x - half), p.Y(r.y + ly + inset), p.X(x + half),
                    p.Y(r.y + ly + C - inset))
            d = 0.6 * C
            if an["old"][0] is not None:
                self._content(canvas, r, (an["old"][0], False), x, cy - d * u, t,
                              alpha=1.0 - u, clip=clip)
            if an["new"][0] is not None:
                self._content(canvas, r, (an["new"][0], False), x, cy + d * (1.0 - u), t,
                              alpha=u, clip=clip)
        elif k == "swap":
            # 地は中身が行から離れている間だけ（止まっている字には地が無いので、急に出さない）
            plate = self._swap_cover(r, an, t)
            for content, (x, y) in zip((an["ci"], an["cj"]),
                                       self.swap_positions(r, an, t, lift)):
                if content[0] is None and not content[1]:
                    continue
                self._content(canvas, r, content, x, y, t, plate=plate)

    def _swap_cover(self, r, an, t):
        """swap の中身が行から離れている度合い 0..1（地の不透明度と、番号を薄くする量）。

        弧の高さが cell の 0.35 倍を超えてから 0.2 倍ぶんで 1 になる（番号の帯の上を通る間）。
        """
        p = self.p
        u = _ease(_prog(t, an["t0"], an["t1"] - an["t0"]))
        dy = p._swap_h(r, an["i"], an["j"]) * math.sin(math.pi * u)
        return _clamp01((dy - 0.35 * p.C) / (0.2 * p.C))

    def swap_positions(self, r, an, t, lift=None):
        """swap の 2 つの中身の位置（設計座標）。左の中身は上の弧、右の中身は下の弧を通る。

        lift: 箱ごとの持ち上げ（compare の下ろし途中）。出発の箱の持ち上げから行き先の箱の
        持ち上げへ、進み具合で移しながら足す（u=0 で持ち上がった箱の中、u=1 で行き先の箱の中）。
        """
        p = self.p
        C = p.C
        lift = lift if lift is not None else self._lifts(r, t)
        u = _ease(_prog(t, an["t0"], an["t1"] - an["t0"]))
        xi = p.col_x(r.col_of[an["i"]]) + C / 2.0
        xj = p.col_x(r.col_of[an["j"]]) + C / 2.0
        h = p._swap_h(r, an["i"], an["j"])
        cy = r.y + C / 2.0
        li, lj = lift.get(an["i"], 0.0), lift.get(an["j"], 0.0)
        s = math.sin(math.pi * u)
        i_top = xi < xj
        a = (xi + (xj - xi) * u, cy + li + (lj - li) * u + (-h * s if i_top else h * s))
        b = (xj + (xi - xj) * u, cy + lj + (li - lj) * u + (h * s if i_top else -h * s))
        return a, b

    def _block(self, canvas, X, Y, angle, color_key, alpha, rgb=None, outline=False):
        """角丸の塊（中心 X, Y px）。outline は字の縁（halo）の色の細い縁取り
        （動いている塊どうしが重なっても1つずつ見分けられる）"""
        p = self.p
        bs = p.L(p.block)
        qa = int(round(angle / math.radians(3))) if angle else 0
        ow = max(1.0, p.L(2.0)) if outline and p.halo is not None else 0.0

        def shape(grow):
            def make(fx, fy):
                h = bs / 2.0 + grow
                pts = self._rrect(-h, -h, h, h, 0.22 * bs + grow)
                if qa:
                    a = qa * math.radians(3)
                    ca, sa = math.cos(a), math.sin(a)
                    pts = [(x * ca - y * sa, x * sa + y * ca) for x, y in pts]
                return self._poly_mask([[(x + fx, y + fy) for x, y in pts]])
            return make

        if ow > 0:
            x0, y0, m = self._cached_at(("blko", round(bs, 3), round(ow, 3), qa), X, Y,
                                        shape(ow))
            self._over(canvas, x0, y0, m, "halo", alpha=alpha)
        x0, y0, m = self._cached_at(("blk", round(bs, 3), qa), X, Y, shape(0.0))
        self._over(canvas, x0, y0, m, color_key, rgb=rgb, alpha=alpha)

    def _band(self, canvas, r, c, t):
        """畳んだ区間に詰まった塊の帯と件数（「…」は帯が伸びると薄れる）"""
        p = self.p
        C = p.C
        g = p.fills[r.name]["gaps"][c]
        times = g["times"]
        x = p.col_x(c)
        cy = r.y + C / 2.0
        k = bisect.bisect_right(times, t)
        if k == 0:
            prog = 0.0
        elif k >= len(times):
            prog = 1.0
        else:
            a0, a1 = times[k - 1], times[k]
            prog = (k + _clamp01((t - a0) / max(1e-9, a1 - a0))) / len(times)
        prog = prog * len(times) / max(1, g["size"])
        self._dots(canvas, r, c, 1.0 - 0.8 * _clamp01(prog * 3.0))
        if t < times[0] - 0.2:
            return
        bt = max(4.0, 0.16 * C)
        x0 = x + 0.06 * C
        L = (C - 0.12 * C) * prog
        if L > 0.5:
            # 帯は 1/8 px 刻みの長さで作り置く（伸びている間は毎コマ長さが違う）
            Lq = round(p.L(L) * 8) / 8.0
            th = p.L(bt)

            def make(fx, fy):
                return self._poly_mask([self._rrect(fx, fy, fx + Lq, fy + th, th / 2.0)])

            xx0, yy0, m = self._cached_at(("band", round(th, 3), Lq), p.X(x0),
                                          p.Y(cy - bt / 2.0), make)
            self._over(canvas, xx0, yy0, m, "fill")
        a = _ease(_prog(t, times[0] - 0.2, 0.2))
        self._text(canvas, str(k), p.X(x + C / 2.0), p.Y(r.y - 8 - p.idx_h / 2.0),
                   p.L(p.IS), "muted", alpha=a, mono=True)

    def _zone(self, canvas, r, zp, a, acc):
        """範囲外の区画（斜線の箱と「?」）。acc は accent への移り具合"""
        np = self.np
        p = self.p
        C = p.C
        x = p.col_x(r.col_of[zp])
        key = ("zone", r.name, zp)
        m = self._shapes.get(key)
        if m is None:
            inset = _BOX_STROKE + 1.0
            x0, y0 = p.X(x + inset), p.Y(r.y + inset)
            x1, y1 = p.X(x + C - inset), p.Y(r.y + C - inset)
            box = self._poly_mask([self._rrect(x0, y0, x1, y1, p.L(0.07 * C))])
            step = max(6.0, p.L(0.16 * C))
            lines = []
            span = (x1 - x0) + (y1 - y0)
            k = -2
            while k * step < span + step:
                sx = x0 + k * step
                lines.append([(sx, y1 + 2), (sx + (y1 - y0) + 4, y0 - 2)])
                k += 1
            hatch = self._stroke_mask(lines, max(1.5, p.L(2.0)))
            # 「?」の後ろは斜線を抜く（字が斜線に埋もれない）
            cxp, cyp = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            disc = self._stroke_mask([[(cxp, cyp)]], p.L(0.62 * C))
            bx0, by0, bm = box
            out = bm.copy()
            for (mx0, my0, mm), mode in ((hatch, "mul"), (disc, "cut")):
                ys0, xs0 = max(by0, my0), max(bx0, mx0)
                ys1 = min(by0 + bm.shape[0], my0 + mm.shape[0])
                xs1 = min(bx0 + bm.shape[1], mx0 + mm.shape[1])
                part = np.zeros_like(bm)
                if ys0 < ys1 and xs0 < xs1:
                    part[ys0 - by0:ys1 - by0, xs0 - bx0:xs1 - bx0] = \
                        mm[ys0 - my0:ys1 - my0, xs0 - mx0:xs1 - mx0]
                out = out * part if mode == "mul" else out * (1.0 - part)
            m = self._shapes[key] = (bx0, by0, out)
        if acc < 1.0:
            self._over(canvas, m[0], m[1], m[2], "dim", alpha=a * (1.0 - acc))
        if acc > 0.0:
            self._over(canvas, m[0], m[1], m[2], "accent", alpha=a * acc * 0.75)
        # 区画の枠（ghost の箱でなければ破線を足す）
        if zp not in r.ghosts:
            self._ghost_frame(canvas, x, r.y, "dim", alpha=a * (1.0 - acc))
            if acc > 0:
                self._ghost_frame(canvas, x, r.y, "accent", alpha=a * acc, extra=p.L(0.75))
        # 「?」は font（等幅の「?」は線が細く、斜線の中で読みにくい）
        cxp, cyp = p.X(x + C / 2.0), p.Y(r.y + C / 2.0)
        qs = p.L(p.zone_q_size())
        if acc < 1.0:
            self._text(canvas, "?", cxp, cyp, qs, "muted", alpha=a * (1.0 - acc), mono=False)
        if acc > 0.0:
            self._text(canvas, "?", cxp, cyp, qs, "accent", alpha=a * acc, mono=False)

    def _compare_marks(self, canvas, r, c, t, lift):
        p = self.p
        C = p.C
        if t < c["t0"]:
            return
        dur = c["dur"]
        fade = 1.0
        if c["rel0"] is not None and t >= c["rel0"]:
            # 枠と不等号は下ろし始めの _MARK_FADE 秒で消す（続けて swap / set が来ても、
            # ease_in_out で字がほとんど動かないうちに消えるので、空の枠が浮かない）
            fade = 1.0 - _ease(_prog(t, c["rel0"], _MARK_FADE))
        if fade <= 0:
            return
        fa = _ease(_prog(t, c["t0"] + 0.35 * dur, 0.25 * dur)) * fade
        if fa > 0:
            for q, k in c["frames"].items():
                box = self.char_box(r, q, k, t, lift)
                if box is None:
                    continue
                gx0, gy0, gx1, gy1 = box
                mm = self._ring(gx0, gy0, gx1, gy1, p.L(4.0), max(2.0, p.L(3.0)))
                self._over(canvas, mm[0], mm[1], mm[2], "accent", alpha=fa)
        sa = _ease(_prog(t, c["t0"] + 0.55 * dur, 0.25 * dur)) * fade
        if sa > 0:
            xl = p.col_x(r.col_of[c["left"]]) + C / 2.0
            xr = p.col_x(r.col_of[c["right"]]) + C / 2.0
            ns = p.note_size()
            cy = r.y - p.lift - 10 - ns * 0.65
            self._text(canvas, c["sign"], p.X((xl + xr) / 2.0), p.Y(cy), p.L(ns), "fg",
                       alpha=sa, mono=True)

    def char_box(self, r, q, k, t, lift=None):
        """箱 q の中身の k 文字目を囲む枠の矩形（キャンバス px）。字が無ければ None"""
        p = self.p
        C = p.C
        text = p.content_at(r.name, q, t)[0] or ""
        if k >= len(text):
            return None
        lift = lift if lift is not None else self._lifts(r, t)
        size = p.L(p.value_size_for(r.name, text))
        mono = _is_ascii(text)
        font = p._fonts.font(size, mono)
        tw = float(font.getlength(text))
        x = p.X(p.col_x(r.col_of[q]) + C / 2.0) - tw / 2.0
        g0 = font.getbbox(text[k], anchor="ls")
        adv0 = float(font.getlength(text[:k]))
        gx0, gx1 = x + adv0 + g0[0], x + adv0 + g0[2]
        yc = p.Y(r.y + C / 2.0) + p.L(lift.get(q, 0.0))
        _l, top, _r, bottom = font.getbbox("0", anchor="ls")
        base = yc + p._fonts.vcenter(size, mono)
        # 横の余白は引用符との間（_QUOTE_GAP）より狭く、縦は字の上下に少し余裕を持たせる
        px = max(2.0, size * 0.07)
        py = max(3.0, size * 0.14)
        return (gx0 - px, base + top - py, gx1 + px, base + bottom + py)

    # --- 針・矢印・あふれ ---

    def _draw_pointer(self, canvas, r, t):
        p = self.p
        segs = p.pointers.get(r.name)
        if not segs or t < segs[0]["t0"]:
            return
        x = segs[0]["x0"]
        bad_lvl = 0.0
        for k, seg in enumerate(segs):
            if t < seg["t0"]:
                break
            u = _prog(t, seg["t0"], seg["t1"] - seg["t0"])
            x = seg["x0"] + (seg["x1"] - seg["x0"]) * u      # 等速
            if seg["bad"] and t >= seg["t1"]:
                bad_lvl = _prog(t, seg["t1"], 0.12)
            elif u < 1.0:
                prev_bad = k > 0 and segs[k - 1]["bad"]
                bad_lvl = (1.0 - _prog(t, seg["t0"], 0.12)) if prev_bad else 0.0
            else:
                bad_lvl = 0.0
        alpha = _ease(_prog(t, segs[0]["t0"], 0.15))
        X, Y0 = self.pointer_xy(r, x)
        h, w = p.L(p.ptr_h), p.L(p.ptr_w)

        def make(fx, fy):
            return self._poly_mask([[(fx, fy), (fx + w / 2.0, fy + h), (fx - w / 2.0, fy + h)]])

        x0, y0, m = self._cached_at(("ptr", round(h, 3), round(w, 3)), X, Y0, make)
        if bad_lvl < 1.0:
            self._over(canvas, x0, y0, m, "pointer", alpha=alpha * (1 - bad_lvl))
        if bad_lvl > 0.0:
            self._over(canvas, x0, y0, m, "accent", alpha=alpha * bad_lvl)

    def pointer_xy(self, r, x):
        """針の先（上の頂点）のキャンバス px。x は設計座標の横位置"""
        p = self.p
        top = r.y + p.C + ((p.idx_gap + p.idx_h) if r.index else 4.0) + 6
        return p.X(x), p.Y(top)

    def _draw_link(self, canvas, lk, t):
        p = self.p
        if t < lk["t0"]:
            return
        u = _ease(_prog(t, lk["t0"], lk["t1"] - lk["t0"]))
        pts = [(p.X(x), p.Y(y)) for x, y in lk["pts"]]
        seglens = [math.dist(pts[k], pts[k + 1]) for k in range(len(pts) - 1)]
        total = sum(seglens)
        L = total * u
        if L <= 0.5:
            return
        head = p.L(max(12.0, 0.30 * p.C))
        head_w = p.L(max(12.0, 0.30 * p.C))
        shaft_w = p.L(max(3.0, 0.0625 * p.C))
        # 先端の位置と向き
        acc = 0.0
        tip = pts[0]
        direction = (0.0, 1.0)
        for k, sl in enumerate(seglens):
            if acc + sl >= L or k == len(seglens) - 1:
                v = min(1.0, (L - acc) / sl) if sl > 0 else 1.0
                a0, a1 = pts[k], pts[k + 1]
                tip = (a0[0] + (a1[0] - a0[0]) * v, a0[1] + (a1[1] - a0[1]) * v)
                if sl > 0:
                    direction = ((a1[0] - a0[0]) / sl, (a1[1] - a0[1]) / sl)
                break
            acc += sl
        hs = min(1.0, L / head)
        hl = head * hs
        shaft_end = L - hl * 0.85
        if shaft_end > 0.5:
            sub = _Renderer._sub_path(pts, seglens, total, 0.0, shaft_end)
            if sub:
                mm = self._stroke_mask(sub, shaft_w)
                self._over(canvas, mm[0], mm[1], mm[2], "fg")
        dx, dy = direction
        nx, ny = -dy, dx
        hw = head_w * hs / 2.0
        tri = [tip, (tip[0] - dx * hl + nx * hw, tip[1] - dy * hl + ny * hw),
               (tip[0] - dx * hl - nx * hw, tip[1] - dy * hl - ny * hw)]
        mm = self._poly_mask([tri])
        bad = lk["bad"] and t >= lk["t1"]
        lvl = _prog(t, lk["t1"], 0.12) if bad else 0.0
        if lvl < 1.0:
            self._over(canvas, mm[0], mm[1], mm[2], "fg", alpha=1.0 - lvl)
        if lvl > 0.0:
            self._over(canvas, mm[0], mm[1], mm[2], "accent", alpha=lvl)
        if lk["label"]:
            a = _clamp01(u * 2.0 - 0.6)
            mx, my, align = lk["label_pos"]
            lines = lk["label"].split("\n")
            lh = p.IS * 1.25
            for k2, line in enumerate(lines):
                yy = my - lh * (len(lines) - 1) / 2.0 + k2 * lh
                self._text(canvas, line, p.X(mx), p.Y(yy), p.L(p.IS), "fg", alpha=a,
                           align=align)

    def _draw_spill(self, canvas, r, t):
        p = self.p
        fs = p.fills[r.name]
        lift = self._lifts(r, t) if p.compares[r.name] else {}
        for fl in fs["flights"]:
            if fl["start"] <= t < fl["arrive"]:
                u = (t - fl["start"]) / max(1e-9, fl["arrive"] - fl["start"])
                e = _ease_out(u)
                x = fl["x0"] + (fl["x1"] - fl["x0"]) * e
                y = fl["y"] + lift.get(fl["p"], 0.0) * e     # 持ち上がった箱へは持ち上げて入る
                self._block(canvas, p.X(x), p.Y(y), 0.0, "fill", _clamp01(u / 0.2),
                            outline=True)
        acc_rgb = self.col["accent"]
        fill_rgb = self.col["fill"]
        # 線へ向かう塊を先に、こぼれ落ちる塊（accent）を上に重ねる
        for sp in fs["spills"]:
            if sp["start"] <= t < sp["hit"]:
                # 線へは等速でぶつかる（ease_out だと線の手前で詰まって団子になる）
                u = (t - sp["start"]) / max(1e-9, sp["hit"] - sp["start"])
                x = sp["x0"] + (sp["hx"] - sp["x0"]) * u
                y = sp["y"] + (sp["hy"] - sp["y"]) * u
                self._block(canvas, p.X(x), p.Y(y), 0.0, "fill", _clamp01(u / 0.2),
                            outline=True)
        for sp in fs["spills"]:
            if t < sp["hit"] or t >= sp["hit"] + sp["fall"]:
                continue
            tt = t - sp["hit"]
            x = sp["hx"] + sp["vx"] * tt
            y = sp["hy"] + sp["vy"] * tt + 0.5 * sp["g"] * tt * tt
            # 当たった瞬間に accent（混ぜた中間色を長く見せない）。不透明のまま落ち、
            # 最後の 40% で消える（そのころには行の下へ抜けている）
            mix = _clamp01(tt / 0.05)
            rgb = fill_rgb * (1.0 - mix) + acc_rgb * mix
            a = 1.0 - _clamp01((tt / sp["fall"] - 0.6) / 0.4)
            self._block(canvas, p.X(x), p.Y(y), sp["omega"] * tt, None, a, rgb=rgb,
                        outline=True)
        cnt = fs["count"]
        if cnt is not None and t >= cnt["t0"]:
            # あふれの札（数はあふれが線に着くたびに増える）
            px, py, align = fs["plus_pos"]
            a = _ease(_prog(t, cnt["t0"], 0.15))
            self._text(canvas, p.count_text(r, t), p.X(px), p.Y(py), p.L(p.note_size()),
                       "accent", alpha=a, align=align)


# --- 公開: ファクトリ ---

def slots(*, cell=64, gap=8, row_gap=56, padding=24, size=None, font=None, mono_font=None,
          weight=None, label_size=36, value_size=None, index_size=32, colors=None, seed=0):
    """番号つきの箱の列（配列・バッファ・設定の項目）を描く動く図のビルダーを返す。

    s.row() で行を足し、fill / read / link / put / set / mark / unmark / to_str /
    compare / swap / halt で出来事を時刻つきで積み、s.build() で動画 Object にする。
    時刻はすべて「できた Object の先頭からの秒」。

    cell: 箱の一辺 px
    gap: 箱どうしの間隔 px
    row_gap: 行どうしの間隔 px（番号・針の帯の下から次の行の上の帯まで）
    padding: 図のまわりの余白 px
    size: (幅, 高さ) px。None は外接矩形（偶数に丸める）。小さいと全体を縮める（文字も縮む）
    font: ラベルと日本語の字のフォント（None は text() と同じ既定の探索）
    mono_font: 数字・ASCII の字の等幅フォント（None は Consolas / DejaVu Sans Mono ほか）
    weight: font が可変フォントのときの太さ（wght の値か名前つきインスタンス）
    label_size: 左のラベルの文字サイズ px
    value_size: 箱の中の字の大きさ px（None は cell の半分。入らない字は縮める）
    index_size: 箱の下の番号の大きさ px（既定 32 は p.audit() の推奨の下限）
    colors: 色の上書き（キー: bg / fg / muted / dim / line / accent / fill / pointer / card / halo）
    seed: あふれ（fill の spill）の動きの乱数の種

    箱の中の字は行の中で大きさを揃える。ただし 1 つの長い字のために行全体を 32px
    （value_size がそれより小さければ value_size）未満へは縮めず、その字だけを縮める。

    行: s.row(name, n, label=, values=, index=True, index_base=1, capacity=, elide=, ghost=,
      offset=, overflow_label='total')。overflow_label はあふれの札（'total' 入った数の合計
      「400件」/ 'over' 上限を超えた数 / 'undrawn' 描かなかった塊の数 / None / 書式の文字列）

    出来事（t は秒）:
      s.fill(t, row, count, dur=1.0, order='left', source='right', spill=True, spill_visible=24)
      s.read(t, row, index, dur=0.3)       針が等速で歩く。範囲外は区画「?」へ出て accent
      s.link(t, (row_a, i), (row_b, j), dur=0.5, label=None)
      s.put(t, row, index, text, dur=0.4)  札が上から降りて箱に収まる
      s.set(t, row, index, value, dur=0.3) 古い字が上へ抜け、新しい字が下から入る
      s.mark(t, row, index, color='accent', style='frame'|'fill') / s.unmark(t, row, index)
      s.to_str(t, row)                     各箱の数の両側に引用符が現れる
      s.compare(t, row, i, j, by='str'|'num', dur=0.6)
      s.swap(t, row, i, j, dur=0.5)        上下に分かれた弧で入れ替わる
      s.halt(t, style='dim'|'freeze', dur=0.3)
    obj = s.build(duration=None) の obj.figure.cell_xy(row, index) は箱の中心の px、
    obj.figure.rows は行の情報、obj.figure.frame(t) は 1 コマの RGBA 配列。
    """
    return Slots(cell=cell, gap=gap, row_gap=row_gap, padding=padding, size=size, font=font,
                 mono_font=mono_font, weight=weight, label_size=label_size,
                 value_size=value_size, index_size=index_size, colors=colors, seed=seed)
