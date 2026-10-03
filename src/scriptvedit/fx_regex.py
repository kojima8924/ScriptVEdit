# -*- coding: utf-8 -*-
"""後戻り型の正規表現の照合を、記録した手順から描く図: regex_view()

regex_trace（regex_vm.py）が記録した event の列を、そのまま絵にする。手で数えた
コマ列を用意しなくても、「開始位置が1つずつ右へずれ、帯が最後の字で赤く止まり、
段が積もって三角形になる」（\\s+$）や、「2つの .* が文字列を分け合う位置を次々に
試す」（.*(?:.*=.*)）が、実際に記録した手順どおりに描ける。

    tr = regex_trace(r"\\s+$", "x" + " " * 10 + "x")
    fig = regex_view(tr, view="rows", count="matches", at=[1.2])
    fig.time() <= move(x=0.5, y=0.6, anchor="center")
    # 長い文字列の回数は regex_count で数えて、最後にカウンタを回す
    big = regex_count(r"\\s+$", lambda n: "x" + " " * n + "x", 20000, count="matches").value
    fig2 = regex_view(tr, view="rows", count="matches", count_to=big,
                      count_label=("{n:,} 回", "約{oku:.0f}億回（模式）"))

絵の種類（view）
---------------
- tape: 上段に式の部品の箱（等幅）、中段に文字列を1字1マス（空白は space の字で見せる）、
  マスの下に量指定子の取り分の下線（1本目 fg の実線・2本目 muted の破線・3本目 line の
  点線。式の箱の下にも同じ線種の短い線を付けて対応させる）。照合位置は白い三角（▼）、
  開始位置は ▲ と縦の細線（開始位置より左のマスは暗くする）、失敗した判定は accent の ×
  （0.25 秒）、後戻りは取り分の終わりから新しい終わりへの muted の弧の矢印（0.3 秒で
  薄れる）。カウンタは右下に、桁の動かない等幅の数字で。
- rows: 開始位置ごとに1行。成功した判定は fg の帯が左から右へ伸び、失敗した判定は
  accent の縦棒、assert（^ $ \\A \\Z・先読み・後読み）の失敗は accent の点、後戻りは
  muted の短い縦線。同じマスを1つの試行の中で何度も判定（成功）すると帯の明るさが対数で
  上がる（熱。帯を描く成功した判定の回数で数え、一番熱い帯が fg になる）。
  1判定で終わった試行は trivial='mark' なら muted の点だけ（'hide' なら描かない）。
  行が rows_max を超えたら行の高さを縮める（最小 2px）。
- both: 上に tape、下に rows（マスの列が揃う）。

拍（beats）と時間
---------------
1拍の単位は 'test'（判定 = test と assert の event）・'backtrack'・'attempt'（開始位置）・
'literal:<字>'（その字のリテラルを判定したときだけ。xxxxx に .*(?:.*=.*) の 'literal:=' は
56 拍）・'step'（全 event）。拍と拍の間の event はまとめて反映する（カウンタもその間の
分だけ進む）。拍の時刻は pace=None なら framekit.pace（最初の3拍は 0.6 秒ずつ・以降は
0.82 倍ずつ速くなり、最後は1コマに4拍＝カウンタだけが走る）。at=[秒, …] で先頭の拍を
語の時刻に合わせる。duration を渡すと、拍がちょうどその長さを埋めるように速さを決める
（最後の拍 + hold_end + count_roll = duration。拍が多ければ速く、少なければ加速を緩め、
それでも余れば1拍を最大 1.2 秒まで延ばし、残りは最後の絵を保持する。コマ数は
round(duration × fps)）。pace を明示したときの duration は長さだけを決める（拍の時刻は
pace のまま）。
戻り値の obj.figure.beat_times に各拍の時刻（Object の先頭からの秒）、
obj.figure.count_final に最後の拍の後のカウンタの値が入る。
× と矢印は次の拍が来たら消す（重ならない）。拍の間隔が短く、照合位置が滑り終えてから
では × が 0.1 秒も出ない拍は、滑り終わりを待たずに拍の時刻から出す。1コマに3拍を
超えて入る区間では × と矢印を描かない（点滅させない）。

カウンタの書式（count_label）は1つの書式か、(拍と回転の間の書式, 最後の書式) の組。
組にすると、カウンタが最後の値（count_to があれば回し終えた値、無ければ最後の拍の値）に
着いたときだけ2つ目の書式で出す。'約{oku:.0f}億回' のような粗い書式を1つだけ渡すと、
拍の間ずっと「約0億回」のまま動かない（そのときは警告する）ので、
count_label=('判定 {n:,} 回', '約{oku:.0f}億回（模式）') のように分ける。
拍を見せ終えた図から回転だけを見せたいときは、同じ図を fig[fig.figure.beats_end:] で
切り出す（素材時間のスライス）。

色は framekit の PALETTE（fg 白・accent 赤・muted 灰・line 枠・dim 暗い線・panel 図の面）。
**赤（accent）は失敗と停止だけに使う。** 図の下には panel の面を敷く（文字の下地。
colors={'panel': (0, 0, 0, 0)} で消せるが、そのときは p.audit() が文字の装飾の無さを
警告する）。

鍵（同一出力なら同一鍵）: 式・文字列・mode・view・beats・計算後の拍の秒の列・window・
show・色・cell・size・rows の行の高さと、フォントの内容指紋・照合の手順の版
（regex_vm._REGEX_VM_VER）・_REGEX_VIEW_VER。効くときだけ入れるもの: count・count_label・
count_to・count_roll（show に 'count' があるとき。回転の秒は拍の秒の列と尺に入る）、space
（マスか式に空白として見せる字があるとき）、accent（show に 'fail' があるとき）、trivial
（1判定で終わる行があるとき）。event の列そのものは鍵に入れない（式・文字列・mode と
手順の版から決まる）。

図の大きさ: 幅・高さとも 4096px まで（cell と window で決まる。超えると ValueError）。
Project の画面より大きいと警告する（cell を小さくするか window で範囲を切る）。

重さ（Windows・Python 3.10・numpy 2.0 の実測。描画のみ・ffmpeg の符号化は別）:
1コマの描画は変化のある帯だけを描き直す（tape は ▼・マス・下線・矢印・開始位置の細帯に
分け、マスの列は「普通」「暗い」の2枚を1回だけ描いて使い回す。式とカウンタは横の範囲も
絞る）。平均（最悪）で tape（xxxxx・2-17 の 12 秒・462x434）約 3.5ms（8ms）、
rows（x＋空白10個＋x・952x632）約 1ms（6ms）、both（同・952x730）約 2ms（12ms）、
both（x＋空白20個＋x・cell=64・1652x970）約 6ms（27ms）、both（空白30個・cell=48・
1812x914）約 10ms（29ms）。最悪は開始位置が変わってマスの列と下地を描き直すコマ。
最初のコマだけ下地の準備で約 30〜200ms。1秒ぶん（30コマ）の生成は ffmpeg の qtrle 符号化を
含めて約 0.1〜0.3 秒（rows 6.9 秒で 0.65 秒、tape 12 秒で 1.8 秒、1652x970 の both
9.6 秒で 2.7 秒）。
numpy・opencv-python・Pillow が要る（framekit と同じ optional 依存）。
"""

import bisect
import math
import os
import unicodedata
from collections import Counter

import scriptvedit.framekit as fk
from scriptvedit.context import current_project
from scriptvedit.regex_vm import _COUNT_KINDS, _REGEX_VM_VER, RegexTrace, _node_chars
from scriptvedit.state import _suggest_hint
from scriptvedit.stillseq import _fps_fraction, _resolve_fps
from scriptvedit.textimage import _cmap_lookup, _import_pil, _load_font
from scriptvedit.validate import _require_choice, _require_number
from scriptvedit.warn import _warn


# 描き方の版。描き方を変えたら上げる（鍵に入る）。
# 2: ▼ の上の余白（cell < 50 で切れていた）・文字の余白・熱の正規化・× の出し方・
#    duration を埋める拍の速さ・count_label の組
_REGEX_VIEW_VER = "2"

_VIEWS = ("tape", "rows", "both")
_SHOW_ITEMS = ("pattern", "cells", "spans", "cursor", "start", "arrow", "fail", "count")
_TRIVIAL = ("mark", "hide")
_BEAT_KINDS = ("test", "backtrack", "attempt", "step")   # と 'literal:<字>'

# 拍の数の上限（これを超える図は beats='attempt' にするか regex_count で数だけ出す）
_MAX_BEATS = 5000
# window=None で描けるマスの数（これを超える文字列は window=(開始, 終了) で範囲を切る）
_MAX_CELLS = 48
# 量指定子の下線を描く段の数（これを超える量指定子の取り分は描かない）
_MAX_LANES = 6

# 一瞬だけ出す印の長さ（秒）
_FAIL_SEC = 0.25
_FAIL_FADE = 0.08
_ARROW_SEC = 0.3
# 照合位置・取り分の終わりが次の拍へ滑る長さ（秒。拍の間隔の半分を超えない）
_SLIDE_SEC = 0.15
# 1コマにこれを超える拍が入る区間では矢印と × を描かない
_FLASH_MAX_PER_FRAME = 3
# × は照合位置が滑り終えてから出し、次の拍で消す。滑り終えてからでは出ている時間が
# これに満たない拍（間隔の短い拍）は、滑り終わりを待たずに拍の時刻から出す
# （× が一瞬しか出ずに点滅して見えるのを避け、中くらいの速さでも × を見せる）
_FLASH_MIN_SEC = 0.1

# 既定の拍の速さ（framekit.pace の引数）。min_dt は 1/(4·fps)（最後は1コマに4拍）
_PACE_DEFAULTS = {"first": 0.6, "slow": 3, "ratio": 0.82}
_PACE_KEYS = ("first", "slow", "ratio", "min_dt")
_FAST_BEATS_PER_FRAME = 4
# duration を埋めるとき、加速を止めても（ratio=1）余る場合に1拍を延ばす上限（秒）
_FILL_FIRST_MAX = 1.2

# 図の幅・高さの上限 [px]（float のキャンバスを丸ごと持つため）
_CANVAS_MAX = 4096

# 等幅フォントの既定の候補（無ければ font と同じフォント）。字が無いときは1字ずつ
# font へ落とす（Consolas には ␣ が無い）。
_MONO_CANDIDATES = [
    "C:/Windows/Fonts/consolab.ttf",
    "C:/Windows/Fonts/consola.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono-Bold.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]

# 改行・タブをマスに見せる字の候補（フォントにある最初のもの）
_NEWLINE_GLYPHS = ("↵", "¶", "/")
_TAB_GLYPHS = ("⇥", "→", ">")


# --- 小さな道具 ---------------------------------------------------------------

def _ease_out_cubic(u):
    u = 0.0 if u < 0 else (1.0 if u > 1 else u)
    return 1.0 - (1.0 - u) ** 3


def _ease_in_out_sine(u):
    u = 0.0 if u < 0 else (1.0 if u > 1 else u)
    return 0.5 - 0.5 * math.cos(math.pi * u)


def _with_alpha(rgba, k):
    """(r, g, b, a) の a に k（0..1）を掛ける"""
    r, g, b, a = rgba
    return (r, g, b, int(round(a * max(0.0, min(1.0, k)))))


def _default_mono():
    for p in _MONO_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


# --- 文字（フォントの選び分けと基準線）-----------------------------------------

class _Glyphs:
    """文字の絵（framekit.label の Sprite）を、1字ずつのフォントの選び分けつきで用意する。

    等幅で描く字（式・マス・数字）は mono を優先し、mono に無い字は main へ落とす。
    それ以外（カウンタの「判定」「回」など）は main を優先する。どちらにも無い字は
    label が ValueError にする（黙って豆腐にしない）。
    """

    def __init__(self, fn, font, mono_font, weight):
        self.fn = fn
        self.pil = _import_pil(fn)
        self.weight = weight
        self.main = fk.font_ref(fn, font, 0, weight)
        mono = mono_font if mono_font is not None else _default_mono()
        self.mono = fk.font_ref(fn, mono, 0, None) if mono is not None else self.main
        self._has = {}
        for ref in (self.main, self.mono):
            look = _cmap_lookup(ref.path, ref.index)
            self._has[ref] = look if look is not None else (lambda cp: True)
        self.used = []          # 描いた Sprite（text_meta へ申告する）
        self._used_ids = set()

    def has(self, ch):
        return self._has[self.mono](ord(ch)) or self._has[self.main](ord(ch))

    def ref_for(self, ch, mono):
        first, second = (self.mono, self.main) if mono else (self.main, self.mono)
        if self._has[first](ord(ch)):
            return first
        if self._has[second](ord(ch)):
            return second
        return first

    def font(self, ref, size):
        return _load_font(self.fn, self.pil, ref.path, ref.index, size, ref.coords)

    def runs(self, text, mono):
        """同じフォントの字をまとめた [(文字列, FontRef)]"""
        out = []
        for ch in text:
            ref = self.ref_for(ch, mono)
            if out and out[-1][1] == ref:
                out[-1] = (out[-1][0] + ch, ref)
            else:
                out.append((ch, ref))
        return out

    def advance(self, text, size, mono=True):
        return sum(self.font(ref, size).getlength(t) for t, ref in self.runs(text, mono))

    def metrics(self, size, mono=True):
        """(ascent, descent)。基準線は mono（または main）のメトリクスで決める"""
        return self.font(self.mono if mono else self.main, size).getmetrics()

    def sprite(self, text, size, rgba, mono=True):
        spans = []
        for t, ref in self.runs(text, mono):
            st = {"font": ref.path, "font_index": ref.index}
            if ref is self.main and self.weight is not None:
                st["weight"] = self.weight
            spans.append((t, st))
        base = self.mono if mono else self.main
        # 字の絵には余白を付ける（'|' や和字は字送りの箱から 1〜2px はみ出す。置く位置は
        # sp.base を引いて決めるので、余白の大きさは位置に効かない）
        pad = max(4, int(math.ceil(size * 0.12)))
        kw = {"size": size, "font": base.path, "font_index": base.index,
              "color": tuple(rgba), "padding": (pad, pad)}
        if base is self.main and self.weight is not None:
            kw["weight"] = self.weight
        sp = fk.label(self.fn, spans, **kw)
        if id(sp) not in self._used_ids:
            self._used_ids.add(id(sp))
            self.used.append(sp)
        return sp

    def refs(self):
        return [self.main] if self.mono is self.main else [self.main, self.mono]


def _cell_glyph(ch, space, glyphs):
    """マスに見せる字と色の名前"""
    if ch == " ":
        return space, "muted"
    if ch == "\n":
        return next((g for g in _NEWLINE_GLYPHS if glyphs.has(g)), "/"), "muted"
    if ch == "\t":
        return next((g for g in _TAB_GLYPHS if glyphs.has(g)), ">"), "muted"
    if ch.isspace() or unicodedata.category(ch) in ("Cc", "Cf"):
        return space, "muted"
    return ch, "fg"


# --- 拍 -----------------------------------------------------------------------

def _beat_events(fn, trace, beats):
    """拍にする event の添字の列"""
    ev = trace.events
    if beats == "test":
        idx = [k for k, e in enumerate(ev) if e[0] in ("test", "assert")]
    elif beats == "backtrack":
        idx = [k for k, e in enumerate(ev) if e[0] == "backtrack"]
    elif beats == "attempt":
        idx = [k for k, e in enumerate(ev) if e[0] == "start"]
    elif beats == "step":
        idx = list(range(len(ev)))
    else:
        ch = beats[len("literal:"):]
        lit = {pi for pi, c in _node_chars(trace.pattern).items() if c == ch}
        if not lit:
            raise ValueError(
                f"{fn}: beats='{beats}' の字 '{ch}' は式 {trace.pattern!r} のリテラルにありません")
        idx = [k for k, e in enumerate(ev) if e[0] == "test" and e[2] in lit]
    if not idx:
        raise ValueError(
            f"{fn}: beats='{beats}' に当たる event がありません（{trace.pattern!r} × "
            f"{trace.text!r}）。beats='test' などにしてください")
    if len(idx) > _MAX_BEATS:
        raise ValueError(
            f"{fn}: 拍が {len(idx):,} あります（上限 {_MAX_BEATS:,}）。beats='attempt' に"
            f"するか文字列を短くし、大きな数は regex_count で数えて count_to で見せてください")
    return idx


def _beat_times(fn, n, pace, at, duration, hold_end, roll, fps):
    """(各拍の開始秒, 拍の終わりの秒, 全体の秒)。同じ引数の計算はプロセス内で1回。"""
    key = [n, pace, at, duration, hold_end, roll, fps]
    return fk.memo("regex_view.beats", key,
                   lambda: _beat_times_calc(fn, n, pace, at, duration, hold_end, roll, fps))


def _beat_times_calc(fn, n, pace, at, duration, hold_end, roll, fps):
    """_beat_times の本体。

    duration を渡して pace を省いたときは、拍の終わり + hold_end がちょうど
    duration − count_roll になるように拍の速さを決める:
      - 既定の速さで収まらない → 加速を強める（framekit.pace が ratio を下げる。それでも
        足りなければ最後の速さと出だしの遅い拍も縮める）
      - 既定の速さで余る → 加速を緩める（ratio を 1 へ近づける）。ratio=1（0.6 秒一定）
        でも余れば、1拍の秒を _FILL_FIRST_MAX まで延ばす。それでも余る分は最後の絵を保持する
    pace を渡したときの duration は長さだけを決める（拍の時刻は pace のまま。dict の pace は
    収まらなければ framekit.pace が ratio を下げる）。
    """
    kw = dict(_PACE_DEFAULTS)
    kw["min_dt"] = 1.0 / (_FAST_BEATS_PER_FRAME * fps)
    if pace is None:
        pass
    elif isinstance(pace, dict):
        unknown = sorted(set(pace) - set(_PACE_KEYS))
        if unknown:
            raise ValueError(
                f"{fn}: pace の dict に知らないキー {unknown} があります"
                f"（使えるキー: {', '.join(_PACE_KEYS)}）{_suggest_hint(unknown[0], _PACE_KEYS)}")
        kw.update(pace)
    else:
        _require_number(fn, "pace", pace, 0, None)
        if pace <= 0:
            raise ValueError(f"{fn}: pace（1拍の秒）は 0 より大きくしてください: {pace!r}")
        kw.update(first=float(pace), slow=n, ratio=1.0)
    user_at = at
    total = None
    if duration is not None:
        total = float(duration) - roll
        if total <= 0:
            raise ValueError(
                f"{fn}: duration（{duration}）が count_roll（{roll}）より短い")

    def run(k):
        """出だしの速さを k 倍にした拍の時刻（at を渡されていなければ素の絵も k 倍）"""
        kk = dict(kw)
        kk["first"] = float(kw["first"]) * k
        a = user_at if user_at is not None else [kk["first"]]   # 最初は素の絵を1拍ぶん
        return fk.pace(n, at=a, total=total, hold_end=hold_end, fps=fps, **kk)

    try:
        starts, end = run(1.0)
    except ValueError:
        if total is None or pace is not None:
            raise
        # 拍が多すぎて収まらない: 最後の速さ（1コマあたりの拍）を上げ、それでも足りなければ
        # 出だしの遅い拍も縮めて収め直す（at で渡した時刻は動かさない）
        avail = total - hold_end - (float(user_at[-1]) if user_at else 0.0)
        if avail <= 0:
            raise ValueError(
                f"{fn}: duration（{duration} 秒）に拍が収まりません"
                f"（at の最後 {user_at[-1] if user_at else 0} 秒 + hold_end {hold_end} 秒"
                f"{' + count_roll ' + str(roll) + ' 秒' if roll else ''}）") from None
        kw["min_dt"] = min(kw["min_dt"], avail * 0.25 / n)
        best = None
        lo, hi = 0.02, 1.0
        for _ in range(40):
            mid = hi if best is None and _ == 0 else (lo + hi) / 2
            try:
                got = run(mid)
            except ValueError:
                hi = mid
                continue
            best = got
            if mid == hi:
                break
            lo = mid
        if best is None:
            try:
                best = run(lo)
            except ValueError as e:
                raise ValueError(
                    f"{fn}: duration（{duration} 秒）に {n} 拍が収まりません: {e}") from None
        starts, end = best
    if total is not None and pace is None and end < total - 1e-6:
        starts, end = _fill_duration(n, kw, user_at, total, hold_end, fps, starts, end)
    beats_end = end - hold_end
    full = beats_end + roll + hold_end
    if duration is not None:
        full = float(duration)
    return [float(t) for t in starts], beats_end, full


def _fill_duration(n, kw, user_at, total, hold_end, fps, starts, end):
    """既定の速さでは duration が余るとき、拍を遅くして埋めた (starts, end)。

    end（最後の拍 + 間隔 + hold_end）は ratio にも1拍の秒にも単調に増えるので、
    end ≤ total を保つ最大の値を二分法で探す（決定的。同じ引数なら同じ秒の列）。
    """
    if user_at is not None and len(user_at) >= n:
        return starts, end          # 全部の拍の時刻が at で決まっている

    def run(first, ratio):
        kk = dict(kw)
        kk["first"] = first
        kk["ratio"] = ratio
        a = user_at if user_at is not None else [first]
        return fk.pace(n, at=a, total=None, hold_end=hold_end, fps=fps, **kk)

    first0, ratio0 = float(kw["first"]), float(kw["ratio"])

    def search(lo, hi, make):
        """make(lo) は収まり make(hi) は収まらない区間で、収まる最大へ寄せる"""
        best = make(lo)
        for _ in range(48):
            mid = (lo + hi) / 2
            got = make(mid)
            if got[1] <= total + 1e-9:
                lo, best = mid, got
            else:
                hi = mid
            if hi - lo < 1e-10:
                break
        return best

    flat = run(first0, 1.0)
    if flat[1] > total + 1e-9:
        # 加速を緩めれば埋まる
        return search(ratio0, 1.0, lambda r: run(first0, r))
    # 加速を止めても余る: 1拍の秒を延ばす（上限 _FILL_FIRST_MAX）
    hi_first = max(first0, _FILL_FIRST_MAX)
    longest = run(hi_first, 1.0)
    if longest[1] <= total + 1e-9:
        return longest
    return search(first0, hi_first, lambda f: run(f, 1.0))


# --- 前計算（event の列から、カウンタ・行・取り分の差分）---------------------------

def _precompute(trace, count):
    """カウンタの累計・行（試行）ごとの event・熱の最大を作る（同じ照合なら1回）"""
    ev = trace.events
    steps = trace.event_steps
    cum = [0] * len(ev)
    c = 0
    for k, e in enumerate(ev):
        kind = e[0]
        if count == "tests":
            if kind == "test" or kind == "assert":
                c += 1
        elif count == "matches":
            if kind == "test" and e[3]:
                c += 1
        elif count == "backtracks":
            if kind == "backtrack":
                c += 1
        elif count == "attempts":
            if kind == "start":
                c += 1
        else:
            c = steps[k]
        cum[k] = c
    rows = []
    heat_max = 1
    for (s, e0, e1, result) in trace.attempts:
        items = []
        per_cell = Counter()
        n_tests = 0
        for k in range(e0, e1 + 1):
            e = ev[k]
            if e[0] in ("test", "assert", "backtrack"):
                items.append((k, e[0], e[1], e[3]))
                # 熱は「そのマスで成功した判定の回数」（帯を描くのは成功した判定だけなので、
                # _draw_row と同じものを数える。失敗も数えると一番熱い帯が fg に届かない）
                if e[0] == "test" and e[3]:
                    per_cell[e[1]] += 1
                if e[0] in ("test", "assert"):
                    n_tests += 1
        if per_cell:
            heat_max = max(heat_max, max(per_cell.values()))
        trivial = (result == "fail" and n_tests == 1)
        rows.append({"s": s, "e0": e0, "e1": e1, "result": result,
                     "items": items, "trivial": trivial,
                     "match_end": ev[e1][1] if result == "match" else None})
    attempt_first = [a[1] for a in trace.attempts]
    return {"cum": cum, "rows": rows, "heat_max": heat_max, "attempt_first": attempt_first}


# --- 配置 ---------------------------------------------------------------------

class _Layout:
    """図の寸法と各部の座標（px。y は上から）"""


def _columns(text, window):
    """マスの列: [('lell', None) / ('char', 添字) / ('rell', None) / ('eos', None)]"""
    n = len(text)
    a, b = window
    cols = []
    if a > 0:
        cols.append(("lell", None))
    for p in range(a, b):
        cols.append(("char", p))
    if b < n:
        cols.append(("rell", None))
    else:
        cols.append(("eos", None))
    return cols


# --- 図 -----------------------------------------------------------------------

class _RegexFigure:
    """regex_view のコマを描く（変化のある帯だけを描き直す）。"""

    def __init__(self, fn, trace, *, view, show, window, cell, space, glyphs, pal,
                 count, count_label, count_to, pre, beat_ev, times, beats_end, roll,
                 fps, rows_max, trivial, size):
        self.fn = fn
        self.trace = trace
        self.view = view
        self.show = show
        self.window = window
        self.C = cell
        self.space = space
        self.g = glyphs
        self.pal = pal
        self.count = count
        # (拍と回転の間の書式, 最後の値に着いた後の書式)
        self.count_labels = count_label
        self.count_to = count_to
        self.pre = pre
        self.beat_ev = beat_ev
        self.times = times
        self.beats_end = beats_end
        self.roll = roll
        self.fps = fps
        self.rows_max = rows_max
        self.trivial = trivial
        self.events = trace.events
        self.count_final = pre["cum"][-1] if pre["cum"] else 0
        self._layout(size)
        flash_ok = self._flash_table()
        # 拍ごとの「前の拍から滑る秒」（間隔の半分まで。1.5 コマ未満なら滑らせない）
        self._slide = []
        for j, tj in enumerate(times):
            dt = tj - (times[j - 1] if j >= 1 else 0.0)
            ta = min(_SLIDE_SEC, 0.5 * dt) if dt > 0 else 0.0
            self._slide.append(ta if ta >= 1.5 / fps else 0.0)
        # 拍ごとの × の出る区間 (始め, 終わり, 自分の時間で消えるか) と矢印の区間 (始め, 終わり)。
        # × は照合位置が滑り終えてから出す。どちらも次の拍で消す（速い区間で ×・矢印が
        # 重ならない）。滑り終えてからでは × が _FLASH_MIN_SEC も出ない拍は、拍の時刻から出す
        # （中くらいの速さでも × が見え、失敗の拍が続くあいだ × が点滅せずに次のマスへ移る）。
        # 1コマに3拍を超えて入る拍（flash_ok が False）は描かない
        self._fail_win, self._arrow_win = [], []
        for j, tj in enumerate(times):
            if not flash_ok[j]:
                self._fail_win.append(None)
                self._arrow_win.append(None)
                continue
            nxt = times[j + 1] if j + 1 < len(times) else float("inf")
            xs = tj + self._slide[j]
            if min(xs + _FAIL_SEC, nxt) - xs < _FLASH_MIN_SEC:
                xs = tj
            natural = nxt >= xs + _FAIL_SEC
            xe = xs + _FAIL_SEC if natural else nxt
            self._fail_win.append((xs, xe, natural) if xe > xs else None)
            ae = min(tj + _ARROW_SEC, nxt)
            self._arrow_win.append((tj, ae) if ae > tj else None)
        self._ready = False

    # --- 配置 ---
    def _layout(self, size):
        C = self.C
        g = self.g
        tr = self.trace
        L = _Layout()
        self.L = L
        L.fs = int(round(C * 0.61))                  # マス・式の字
        L.cfs = max(L.fs, int(round(C * 0.7)))       # カウンタの字
        L.pad = int(round(C * 0.375))
        L.gap = max(6, int(round(C * 0.09)))         # マスの間
        L.pitch = C + L.gap
        L.box_pad = int(round(C * 0.22))
        L.box_gap = max(6, int(round(C * 0.16)))
        L.lane_h = max(8, int(round(C * 0.2)))
        L.line_w = 4                                  # 下線の太さ（偶数で画素に揃う）
        self.cols = _columns(tr.text, self.window)
        cols_w = len(self.cols) * L.pitch - L.gap

        # 式の部品（箱か、箱なしの字）
        toks = []
        pat_w = 0
        for i, nd in enumerate(tr.nodes):
            text = nd.text.replace(" ", self.space)
            boxed = nd.kind in ("char", "any", "class", "assert")
            tw = g.advance(text, L.fs, mono=True)
            w = int(math.ceil(tw)) + (2 * L.box_pad if boxed else 0)
            if boxed:
                w = max(w, int(round(C * 0.75)))
            toks.append({"i": i, "text": text, "boxed": boxed, "w": w, "tw": tw,
                         "quant": nd.quant, "kind": nd.kind})
            pat_w += w + L.box_gap
        pat_w = max(0, pat_w - L.box_gap)
        self.toks = toks

        # 量指定子の段（表示する順）
        vis = [q.index for q in tr.quantifiers if q.visible]
        self.lane_of = {q: k for k, q in enumerate(vis) if k < _MAX_LANES}
        n_lanes = len(self.lane_of)

        # カウンタの最大幅（桁が増えても収まるように、両方の書式を最大値でも測る）
        L.counter_w = int(math.ceil(max(
            self._counter_width(t) for t in self.counter_texts())))

        content_w = max(pat_w if "pattern" in self.show else 0, cols_w,
                        L.counter_w if "count" in self.show else 0)
        W = content_w + 2 * L.pad
        y = L.pad
        has_tape = self.view in ("tape", "both")
        has_rows = self.view in ("rows", "both")
        sec_gap = int(round(C * 0.3))
        L.pattern_y = None
        if "pattern" in self.show:
            L.pattern_y = y
            y += C + (L.lane_h if n_lanes else 0) + sec_gap
        L.cursor_y = L.cells_y = L.lanes_y = L.arrows_y = L.start_y = None
        L.tape_top = L.tape_bot = None
        if has_tape:
            L.tape_top = y
            L.cursor_y = y
            # ▼ の先端は cells_y − 7、高さは 0.26·C。帯の上端（tape_top）で切れないよう、
            # アンチエイリアスの縁の 2px も含めて余白を取る（cell < 50 で 0.42·C では足りない）
            y += max(int(round(C * 0.42)), int(math.ceil(7 + C * 0.26)) + 2)
            L.cells_y = y
            y += C
            L.lanes_y = y + int(round(C * 0.12))
            y = L.lanes_y + n_lanes * L.lane_h
            L.arrows_y = y
            if "arrow" in self.show:
                y += int(round(C * 0.5))
            L.start_y = y
            if "start" in self.show:
                y += int(round(C * 0.42))
            L.tape_bot = y + 4
            y = L.tape_bot
        L.hdr_y = None
        if self.view == "rows" and "cells" in self.show:
            L.hdr_y = y
            L.cells_y = y
            y += C + int(round(C * 0.2))
        L.rows_y = None
        if has_rows:
            y += int(round(C * 0.15))
            # 行は窓の中で始まる試行だけ（窓の外で始まる試行はマスに何も出せない）
            wa, wb = self.window
            n_text = len(tr.text)
            self.row_list = [r for r, att in enumerate(tr.attempts)
                             if wa <= att[0] < wb or (att[0] == n_text == wb)]
            n_rows = len(self.row_list)
            row_h0 = max(2, int(round(C * 0.375)))
            if n_rows <= self.rows_max:
                row_h = row_h0
            else:
                row_h = max(2, (row_h0 * self.rows_max) // n_rows)
            L.row_h = row_h
            L.n_rows = n_rows
            L.rows_y = y
            y += row_h * n_rows
        L.counter_y = None
        if "count" in self.show:
            y += int(round(C * 0.2))
            L.counter_y = y
            asc, desc = g.metrics(L.cfs, mono=False)
            L.counter_h = asc + desc + 8
            L.counter_base = y + 4 + asc
            y += L.counter_h
        H = y + L.pad
        W += W % 2
        H += H % 2
        L.W0, L.H0 = W, H                             # 中身（panel の面）の寸法
        if W > _CANVAS_MAX or H > _CANVAS_MAX:
            raise ValueError(
                f"{self.fn}: 図が {W}x{H}px になり、上限 {_CANVAS_MAX}px を超えます。"
                f"cell を小さくするか、window=(開始, 終了) で描く範囲を切ってください"
                f"（rows は rows_max で行の高さも縮められます）")
        ox = oy = 0
        if size is not None:
            sw, sh = size
            if sw < W or sh < H:
                raise ValueError(
                    f"{self.fn}: size={sw}x{sh} に図（{W}x{H}）が入りません。"
                    f"size を広げるか、cell を小さくするか、window で範囲を切ってください")
            ox, oy = (sw - W) // 2, (sh - H) // 2
            W, H = sw, sh
        L.W, L.H = W, H
        L.ox, L.oy = ox, oy
        # 横の位置（中身を中央へ）
        L.cols_x = ox + L.pad + (content_w - cols_w) // 2
        L.pat_x = ox + L.pad + (content_w - pat_w) // 2
        L.right = ox + L.pad + content_w
        for name in ("pattern_y", "cursor_y", "cells_y", "lanes_y", "arrows_y", "start_y",
                     "tape_top", "tape_bot", "hdr_y", "rows_y", "counter_y", "counter_base"):
            v = getattr(L, name, None)
            if v is not None:
                setattr(L, name, v + oy)
        # 文字の基準線（マス・式の箱の中で行の高さを縦中央に）
        asc, desc = g.metrics(L.fs, mono=True)
        L.base_off = (C - (asc + desc)) // 2 + asc
        # 式の部品の x
        x = L.pat_x
        for t in toks:
            t["x0"] = x
            t["x1"] = x + t["w"]
            x += t["w"] + L.box_gap
        # 列の x と、文字の位置 → 列
        self.col_x = [L.cols_x + k * L.pitch for k in range(len(self.cols))]
        self.col_of_char = {}
        self.lell = self.rell = self.eos = None
        for k, (kind, p) in enumerate(self.cols):
            if kind == "char":
                self.col_of_char[p] = k
            elif kind == "lell":
                self.lell = k
            elif kind == "rell":
                self.rell = k
            else:
                self.eos = k

    # --- 座標 ---
    def cell_col(self, p):
        """文字 p（または末尾 len）のマスの列。窓の外は None"""
        if p in self.col_of_char:
            return self.col_of_char[p]
        if p == len(self.trace.text) and self.eos is not None:
            return self.eos
        return None

    def cell_cx(self, p):
        k = self.cell_col(p)
        if k is None:
            return None
        return self.col_x[k] + self.C / 2.0

    def bx(self, p):
        """境目 p の x（マスの間の中央）。窓の外は省略記号のマスの中央"""
        a, b = self.window
        L = self.L
        if p < a:
            return self.col_x[self.lell] + self.C / 2.0
        if p > b:
            return self.col_x[self.rell] + self.C / 2.0
        if p < b:
            return self.col_x[self.col_of_char[p]] - L.gap / 2.0
        k = self.eos if self.eos is not None else self.rell
        return self.col_x[k] - L.gap / 2.0

    def in_window(self, p):
        a, b = self.window
        return a <= p < b or (p == len(self.trace.text) and self.eos is not None)

    def ell_x(self, p):
        """窓の外の文字 p を畳んだ「…」のマスの中央"""
        k = self.lell if p < self.window[0] else self.rell
        return None if k is None else self.col_x[k] + self.C / 2.0

    # --- カウンタ ---
    def _counter_text(self, v, final=False):
        """値 v のカウンタの文字列（final=True なら最後の書式）"""
        v = int(v)
        label = self.count_labels[1 if final else 0]
        try:
            return label.format(n=v, man=v / 1e4, oku=v / 1e8)
        except (KeyError, IndexError, ValueError, TypeError) as e:
            raise ValueError(
                f"{self.fn}: count_label {label!r} を書式化できません"
                f"（使える名前は n・man・oku）: {e}") from None

    def counter_texts(self):
        """カウンタに出うる文字列の代表（幅・字の申告・書式の検証に使う）"""
        cmax = max(self.count_final, self.count_to or 0)
        out = [self._counter_text(v) for v in (0, self.count_final, cmax)]
        out += [self._counter_text(v, final=True) for v in (self.count_final, cmax)]
        return out

    def counter_is_final(self, state, t):
        """カウンタが最後の値に着いたか（最後の書式で出す）"""
        if state != len(self.events) - 1:
            return False
        if self.count_to is None:
            return True
        return t >= self.beats_end + self.roll - 1e-9

    def _counter_chars(self, text):
        """[(字, 等幅の枠か)]。数字と数字の間の , . は等幅の枠に入れる"""
        out = []
        for i, ch in enumerate(text):
            numeric = ch.isdigit() or (
                ch in ",." and 0 < i < len(text) - 1
                and text[i - 1].isdigit() and text[i + 1].isdigit())
            out.append((ch, numeric))
        return out

    def _digit_w(self):
        if not hasattr(self, "_dw"):
            f = self.g.font(self.g.ref_for("0", True), self.L.cfs)
            self._dw = max(f.getlength(d) for d in "0123456789")
        return self._dw

    def _counter_width(self, text):
        w = 0.0
        for ch, numeric in self._counter_chars(text):
            if numeric:
                w += self._digit_w() if ch.isdigit() else self.g.advance(ch, self.L.cfs, True)
            else:
                w += self.g.advance(ch, self.L.cfs, mono=False)
        return w

    def counter_value(self, state, t):
        v = self.pre["cum"][state] if state >= 0 else 0
        if self.count_to is not None and t > self.beats_end and state == len(self.events) - 1:
            u = (t - self.beats_end) / self.roll if self.roll > 0 else 1.0
            e = _ease_in_out_sine(u)
            a, b = float(self.count_final), float(self.count_to)
            if b <= a:
                return int(b)
            v = math.expm1(math.log1p(a) + (math.log1p(b) - math.log1p(a)) * e)
            v = int(round(min(b, max(a, v))))
        return v

    def counter_text_at(self, state, t):
        return self._counter_text(self.counter_value(state, t),
                                  final=self.counter_is_final(state, t))

    # --- 拍の検索 ---
    def _flash_table(self):
        """拍ごとに「× と矢印を描いてよいか」。

        1コマに3拍を超えて入る区間（隣の拍との間隔が 1/(3·fps) 秒より短い拍）は描かない。
        コマごとに数えると、速い区間の最後のコマ（拍が2つだけ入る）で × が一瞬出て
        点滅に見えるので、隣との間隔で決める。
        """
        lim = 1.0 / (_FLASH_MAX_PER_FRAME * self.fps) - 1e-9
        ts = self.times
        out = []
        for j, t in enumerate(ts):
            gaps = []
            if j > 0:
                gaps.append(t - ts[j - 1])
            if j + 1 < len(ts):
                gaps.append(ts[j + 1] - t)
            out.append(not gaps or min(gaps) >= lim)
        return out

    def beat_at(self, t):
        """時刻 t までに来た拍の数"""
        return bisect.bisect_right(self.times, t + 1e-9)

    def state_of(self, k):
        """k 拍が来た後の状態（反映済みの最後の event の添字。-1 は何も無い）。

        最初の拍の前は、最初の開始（event 0）だけを反映した素の絵（式と文字列と開始位置）。
        最初の拍までの event は、最初の拍でまとめて反映する。
        """
        if k <= 0:
            return min(self.beat_ev[0] - 1, 0)
        if k >= len(self.beat_ev):
            return len(self.events) - 1
        return self.beat_ev[k - 1]

    def attempt_of(self, state):
        if state < 0:
            return None
        return max(0, bisect.bisect_right(self.pre["attempt_first"], state) - 1)

    # --- 状態から見た目の値 ---
    def cursor_x(self, state):
        if state < 0:
            return None
        kind, pos = self.events[state][0], self.events[state][1]
        if kind == "fail":
            return None
        if kind == "test":
            cx = self.cell_cx(pos)
            return self.ell_x(pos) if cx is None else cx
        return self.bx(pos)

    def spans(self, state):
        if state < 0:
            return {}
        return {q: (a, b) for q, a, b in self.events[state][4] if q in self.lane_of}

    # --- 準備（最初のコマで1回）---
    def _setup(self):
        np = fk.need(self.fn).np
        L = self.L
        C = self.C
        pal = self.pal
        static = fk.canvas(L.W, L.H)
        if pal["panel"][3] > 0:
            fk.rect(static, (L.ox, L.oy, L.ox + L.W0, L.oy + L.H0),
                    pal["panel"], radius=int(round(C * 0.25)))
        # 式の部品
        if L.pattern_y is not None:
            for t in self.toks:
                self._draw_token(static, t, 0, highlight=False)
            # 量指定子の印（箱の下の短い線）
            for q, lane in self.lane_of.items():
                xs = [(t["x0"], t["x1"]) for t in self.toks if t["quant"] == q]
                if not xs:
                    continue
                x0 = min(a for a, _ in xs) + 6
                x1 = max(b for _, b in xs) - 6
                y = L.pattern_y + C + L.lane_h // 2 + 2
                self._lane_line(static, lane, x0, x1, y, 0)
        self.static = static
        # マスの絵（普通・暗い）。マスを描かない図では作らない
        self.cell_patch = {}
        if "cells" in self.show:
            for k, (kind, p) in enumerate(self.cols):
                for dim in (False, True):
                    self.cell_patch[(k, dim)] = self._make_cell(kind, p, dim)
        # tape の帯の下地（マスと開始位置。開始位置が変わったときだけ描き直す）と細帯
        self._cell_bands = {}
        self._tape_base = fk.layer_cache()
        self._tape_strips = (self._tape_strip_bounds()
                             if self.view in ("tape", "both") else [])
        self.out = fk.to_rgba8(static)
        self._sig = {}
        self._row_sig = [None] * (L.n_rows if self.view in ("rows", "both") else 0)
        self._np = np
        self._ready = True

    def _make_cell(self, kind, p, dim):
        C = self.C
        pal = self.pal
        m = 3
        cv = fk.canvas(C + 2 * m, C + 2 * m)
        box = (m + 1.5, m + 1.5, m + C - 1.5, m + C - 1.5)
        r = int(round(C * 0.14))
        if kind == "char":
            fk.rect(cv, box, pal["dim"] if dim else pal["line"], width=3, radius=r)
            ch, cname = _cell_glyph(self.trace.text[p], self.space, self.g)
            col = pal["dim"] if dim else pal[cname]
            self._glyph(cv, ch, col, m + C / 2.0, m + self.L.base_off)
        elif kind == "eos":
            fk.rect(cv, box, pal["dim"], width=3, radius=r, dash=(8, 6))
        else:
            self._glyph(cv, "…", pal["dim"] if dim else pal["muted"], m + C / 2.0,
                        m + self.L.base_off)
        return cv

    def _glyph(self, dst, text, rgba, cx, baseline, size=None, mono=True, ox=0):
        """text を中心 x = cx・基準線 baseline に置く（画素に揃える。dst は x = ox から始まる）"""
        size = size or self.L.fs
        sp = self.g.sprite(text, size, rgba, mono=mono)
        adv = self.g.advance(text, size, mono)
        pen_x = int(round(cx - adv / 2.0))
        fk.over(dst, sp, pen_x - sp.base[0] - ox, int(round(baseline)) - sp.base[1])

    def _draw_token(self, dst, t, oy, highlight, ox=0):
        L = self.L
        C = self.C
        pal = self.pal
        y0 = L.pattern_y - oy
        cx = (t["x0"] + t["x1"]) / 2.0
        if t["boxed"]:
            box = (t["x0"] - ox + 1.5, y0 + 1.5, t["x1"] - ox - 1.5, y0 + C - 1.5)
            r = int(round(C * 0.16))
            if highlight:
                fk.rect(dst, box, pal["fg"], width=4, radius=r)
            else:
                fk.rect(dst, box, pal["line"], width=3, radius=r)
            col = pal["fg"]
        else:
            col = pal["muted"]
        if not highlight:
            self._glyph(dst, t["text"], col, cx, y0 + L.base_off, ox=ox)

    def _lane_line(self, dst, lane, x0, x1, y, oy, alpha=1.0):
        """段 lane の線種で x0..x1 に横線（長さ 0 なら短い縦線）"""
        pal = self.pal
        style = lane % 3
        col = (pal["fg"], pal["muted"], pal["line"])[style]
        col = _with_alpha(col, alpha)
        w = self.L.line_w
        y = y - oy
        if x1 - x0 < 1.0:
            h = self.L.lane_h * 0.45
            fk.polyline(dst, [(x0, y - h), (x0, y + h)], col, w, cap="round")
            return
        if style == 0:
            fk.polyline(dst, [(x0, y), (x1, y)], col, w, cap="butt")
        elif style == 1:
            fk.polyline(dst, [(x0, y), (x1, y)], col, w, cap="butt", dash=(12, 7))
        else:
            fk.polyline(dst, [(x0 + w / 2, y), (x1 - w / 2, y)], col, w, cap="round",
                        dash=(0.01, 9))

    # --- 1コマ ---
    def frame(self, i):
        if not self._ready:
            self._setup()
        t = i / self.fps
        k = self.beat_at(t)
        state = self.state_of(k)
        if self.L.pattern_y is not None:
            self._render_pattern(state)
        if self.view in ("tape", "both"):
            self._render_tape(t, k, state)
        if self.L.hdr_y is not None:
            self._render_header(state)
        if self.view in ("rows", "both"):
            self._render_rows(state)
        if "count" in self.show:
            self._render_counter(state, t)
        return self.out.copy()

    def _region(self, name, y0, y1, sig, draw, x0=0, x1=None):
        """帯 [y0, y1) × [x0, x1) を、sig が前と違うときだけ描き直す（draw(cv, oy, ox)）"""
        if self._sig.get(name) == sig:
            return
        self._sig[name] = sig
        y0 = max(0, int(y0))
        y1 = min(self.L.H, int(y1))
        x0 = max(0, int(x0))
        x1 = self.L.W if x1 is None else min(self.L.W, int(x1))
        if y0 >= y1 or x0 >= x1:
            return
        cv = self.static[y0:y1, x0:x1].copy()
        draw(cv, y0, x0)
        self.out[y0:y1, x0:x1] = fk.to_rgba8(cv)

    def _render_pattern(self, state):
        L = self.L
        active = None
        if state >= 0 and self.events[state][0] in ("test", "assert", "backtrack"):
            node = self.events[state][2]
            if node is not None and self.toks[node]["boxed"] and "cursor" in self.show:
                active = node

        def draw(cv, oy, ox):
            if active is not None:
                self._draw_token(cv, self.toks[active], oy, highlight=True, ox=ox)

        # 描き直すのは式の部品の並ぶ横の範囲だけ（太枠の縁まで含める）
        xa = min(t["x0"] for t in self.toks) - 8 if self.toks else 0
        xb = max(t["x1"] for t in self.toks) + 8 if self.toks else L.W
        self._region("pattern", L.pattern_y - 4, L.pattern_y + self.C + 4, active, draw,
                     xa, xb)

    def _dim_cols(self, state):
        """開始位置より左のマス（暗くする列）の集合"""
        if "start" not in self.show or state < 0:
            return frozenset()
        r = self.attempt_of(state)
        s = self.trace.attempts[r][0]
        out = set()
        for k, (kind, p) in enumerate(self.cols):
            if kind == "char" and p < s:
                out.add(k)
            elif kind == "lell" and s > self.window[0]:
                out.add(k)
        return frozenset(out)

    def _cells_layer(self, y0, y1, dim):
        """static の帯 [y0, y1) にマスを重ねた絵（dim の列は暗いマス）。

        マスを全部重ねた「普通」と「暗い」の2枚を帯ごとに1回だけ描き、dim の列の範囲だけ
        暗い方から写す。マスの絵（マス＋周り 3px）は隣のマスの絵と重ならない（間は 6px 以上）
        ので、1枚ずつ重ねたのと画素まで同じ。開始位置が変わるたびに全部のマスを重ね直すと、
        both・cell=64・空白 20 個で1コマ 4ms ほどかかっていた。
        """
        key = (y0, y1)
        bands = self._cell_bands.get(key)
        if bands is None:
            bands = []
            for d in (False, True):
                cv = self.static[y0:y1].copy()
                for k in range(len(self.cols)):
                    fk.over(cv, self.cell_patch[(k, d)], self.col_x[k] - 3,
                            self.L.cells_y - y0 - 3)
                cv.flags.writeable = False
                bands.append(cv)
            self._cell_bands[key] = bands
        cv = bands[0].copy()
        ks = sorted(dim)
        i = 0
        while i < len(ks):
            j = i
            while j + 1 < len(ks) and ks[j + 1] == ks[j] + 1:
                j += 1
            xa = int(self.col_x[ks[i]]) - 3
            xb = int(self.col_x[ks[j]]) + self.C + 3
            cv[:, xa:xb] = bands[1][:, xa:xb]
            i = j + 1
        return cv

    def _render_header(self, state):
        L = self.L
        dim = self._dim_cols(state)

        def draw(cv, oy, ox):
            cv[:] = self._cells_layer(oy, oy + cv.shape[0], dim)

        self._region("header", L.hdr_y - 4, L.hdr_y + self.C + 4, dim, draw)

    def _render_tape(self, t, k, state):
        L = self.L
        C = self.C
        show = self.show
        dim = self._dim_cols(state)
        # 照合位置と取り分（前の拍から滑らせる）
        cur_x = self.cursor_x(state)
        spans = self.spans(state) if "spans" in show else {}
        span_x = {q: [self.bx(a), self.bx(b)] for q, (a, b) in spans.items()}
        if 1 <= k <= len(self.times):
            tj = self.times[k - 1]
            ta = self._slide[k - 1]
            if ta > 0 and t - tj < ta:
                u = _ease_out_cubic((t - tj) / ta)
                pstate = self.state_of(k - 1)
                px = self.cursor_x(pstate)
                if px is not None and cur_x is not None:
                    cur_x = px + (cur_x - px) * u
                pspans = self.spans(pstate) if "spans" in show else {}
                for q, (a, b) in spans.items():
                    if q in pspans and pspans[q][0] == a:
                        pb = self.bx(pspans[q][1])
                    else:
                        pb = self.bx(a)          # 新しく入った取り分は始まりから伸びる
                    span_x[q][1] = pb + (span_x[q][1] - pb) * u
        # 署名に入れる値で描く（丸める前の値で描くと、署名の同じ別のコマの帯を使い回した
        # ときに画素が描く順で変わる）
        if cur_x is not None:
            cur_x = round(cur_x, 2)
        span_x = {q: (round(v[0], 2), round(v[1], 2)) for q, v in span_x.items()}
        # 一瞬の印（× と矢印）。拍 j の印は次の拍が来たら消える（同時に出るのは1拍ぶんだけ）。
        # 区間の端は beat_at と同じ 1e-9 の余裕で比べる（拍の時刻の足し算の誤差で、次の拍が
        # 来たコマに前の拍の × が残らないように）
        marks = []
        tt = t + 1e-9
        j0 = bisect.bisect_left(
            self.times, t - max(_FAIL_SEC + _SLIDE_SEC, _ARROW_SEC) - 1e-9)
        for j in range(max(0, j0), k):
            e = self.events[self.beat_ev[j]]
            xw, aw = self._fail_win[j], self._arrow_win[j]
            if ("fail" in show and xw is not None and xw[0] <= tt < xw[1]
                    and e[0] in ("test", "assert") and not e[3]):
                # 自分の時間で消える × だけ薄れさせる（次の拍で切られる × は濃いまま消える）
                a = min(1.0, (xw[1] - t) / _FAIL_FADE) if xw[2] else 1.0
                if e[0] == "test" and self.cell_cx(e[1]) is not None:
                    marks.append(("x", round(self.cell_cx(e[1]), 3), round(a, 3)))
                elif e[0] == "assert" and self.window[0] <= e[1] <= self.window[1]:
                    marks.append(("xa", round(self.bx(e[1]), 3), round(a, 3)))
            if "arrow" in show and aw is not None and aw[0] <= tt < aw[1]:
                prev = self.spans(self.state_of(j))
                cur = self.spans(self.state_of(j + 1))
                for q, (a0, b0) in cur.items():
                    if q in prev and prev[q][0] == a0 and prev[q][1] > b0:
                        x_from, x_to = self.bx(prev[q][1]), self.bx(b0)
                        if abs(x_from - x_to) >= 1:
                            marks.append(("arrow", round(x_from, 3), round(x_to, 3),
                                          round((aw[1] - t) / (aw[1] - aw[0]), 3)))
        r = self.attempt_of(state)
        s = self.trace.attempts[r][0] if r is not None else None

        # 動く印を「署名に入れる値・上端 y・下端 y」の列にする（描く順: 下線 → 矢印と × → ▼）。
        # 上端・下端は線の太さ・矢じり・アンチエイリアスの縁を含めた控えめな範囲
        items = []
        for q, (x0, x1) in sorted(span_x.items()):
            y = L.lanes_y + self.lane_of[q] * L.lane_h + L.lane_h // 2
            hh = L.lane_h * 0.45 + L.line_w + 2
            items.append((("lane", q, x0, x1), y - hh, y + hh))
        for m in marks:
            if m[0] == "arrow":
                y = L.arrows_y + 4
                items.append((m, y - 16, y + C * 0.5 + 4))
            else:
                cy = L.cells_y + C / 2.0
                rr = C * (0.21 if m[0] == "x" else 0.14) + 6
                items.append((m, cy - rr, cy + rr))
        if "cursor" in show and cur_x is not None:
            tip = L.cells_y - 7
            items.append((("cursor", cur_x), tip - C * 0.26 - 3, tip + 3))

        def draw_base():
            """帯の下地: 静かな面＋マス＋開始位置（開始位置が変わったときだけ描く）"""
            pal = self.pal
            oy = int(L.tape_top)
            if "cells" in show:
                cv = self._cells_layer(oy, int(L.tape_bot), dim)
            else:
                cv = self.static[oy:int(L.tape_bot)].copy()
            # 開始位置（縦の細線と ▲）
            if "start" in show and s is not None:
                x = self.bx(s)
                y_top = L.cells_y - 6 - oy
                y_bot = L.start_y + 4 - oy
                fk.polyline(cv, [(x, y_top), (x, y_bot)], pal["muted"], 3, cap="butt")
                tw, th = C * 0.30, C * 0.24
                ty = L.start_y + 6 - oy
                fk.polygon(cv, [(x, ty), (x + tw / 2, ty + th), (x - tw / 2, ty + th)],
                           pal["muted"])
            return cv

        # 帯を横の細帯（▼・マス・下線と矢印・開始位置の ▲）に分け、触れる印か下地が
        # 変わった細帯だけを描き直す（速い区間では ▼ と下線の細帯だけが毎コマ変わる）。
        # 細帯の絵は（下地の鍵, 触れる印）だけで決まるので、描く順に依らない
        base_key = (dim, s)
        top = int(L.tape_top)
        for ya, yb in self._tape_strips:
            rel = tuple(it for it in items if it[2] > ya and it[1] < yb)
            sig = (base_key, tuple(it[0] for it in rel))
            name = ("tape", ya)
            if self._sig.get(name) == sig:
                continue
            self._sig[name] = sig
            cv = self._tape_base(base_key, draw_base)[ya - top:yb - top].copy()
            for it in rel:
                self._draw_tape_item(cv, ya, it[0])
            self.out[ya:yb] = fk.to_rgba8(cv)

    def _tape_strip_bounds(self):
        """tape の帯を分ける細帯 [(上端, 下端)]（▼ / マスと × / 下線 / 矢印 / 開始位置）。

        下線の細帯と矢印の細帯の境は arrows_y + 6（下線の最後の段の縁は arrows_y + 6 より上に
        収まる）。矢じりは下線の細帯にもかかるが、印が触れる細帯は全部描き直すので切れない。
        """
        L = self.L
        C = self.C
        cuts = [int(L.tape_top), int(L.cells_y) - 4, int(L.cells_y) + C + 1]
        if int(L.arrows_y) + 6 < int(L.start_y):
            cuts.append(int(L.arrows_y) + 6)
        cuts += [int(L.start_y), int(L.tape_bot)]
        out = []
        for a, b in zip(cuts, cuts[1:]):
            if b > a:
                out.append((a, b))
        return out

    def _draw_tape_item(self, cv, oy, item):
        """tape の動く印を1つ描く（cv は y = oy から始まる細帯）"""
        L = self.L
        C = self.C
        pal = self.pal
        kind = item[0]
        if kind == "lane":
            # 取り分の下線
            _, q, x0, x1 = item
            lane = self.lane_of[q]
            y = L.lanes_y + lane * L.lane_h + L.lane_h // 2
            if x1 - x0 >= 1:
                self._lane_line(cv, lane, x0 + 3, x1 - 3, y, oy)
            else:
                self._lane_line(cv, lane, x0, x0, y, oy)
        elif kind == "arrow":
            # 後戻りの矢印
            _, xf, xt, a = item
            span = abs(xf - xt)
            # 2次ベジェの膨らみは制御点のずれの半分（curve = 2·膨らみ / 長さ）。
            # 左向きの矢印は進行方向の左＝画面の下へ膨らむ
            bulge = min(0.3 * span, C * 0.5 - 12)
            y = L.arrows_y + 4 - oy
            fk.arrow(cv, (xf, y), (xt, y), _with_alpha(pal["muted"], a), 3,
                     head=12, curve=2.0 * bulge / span)
        elif kind == "x":
            # 失敗した1字の判定の ×
            _, cx, a = item
            self._cross(cv, cx, L.cells_y + C / 2.0 - oy, C * 0.21,
                        _with_alpha(pal["accent"], a), 5)
        elif kind == "xa":
            # 失敗した assert の小さな ×（境目の上）
            _, x, a = item
            self._cross(cv, x, L.cells_y + C / 2.0 - oy, C * 0.14,
                        _with_alpha(pal["accent"], a), 4)
        else:
            # 照合位置（▼）
            _, cur_x = item
            tw, th = C * 0.36, C * 0.26
            tip = L.cells_y - 7 - oy
            fk.polygon(cv, [(cur_x, tip), (cur_x + tw / 2, tip - th),
                            (cur_x - tw / 2, tip - th)], pal["fg"])

    def _cross(self, cv, cx, cy, r, rgba, w):
        fk.polyline(cv, [(cx - r, cy - r), (cx + r, cy + r)], rgba, w, cap="round")
        fk.polyline(cv, [(cx - r, cy + r), (cx + r, cy - r)], rgba, w, cap="round")

    # --- rows ---
    def _render_rows(self, state):
        L = self.L
        rows = self.pre["rows"]
        for slot, r in enumerate(self.row_list):
            row = rows[r]
            sig = None if state < row["e0"] else min(state, row["e1"])
            if self._row_sig[slot] == sig:
                continue
            self._row_sig[slot] = sig
            y0 = L.rows_y + slot * L.row_h
            y1 = y0 + L.row_h
            cv = self.static[y0:y1].copy()
            if sig is not None:
                self._draw_row(cv, row, sig)
            self.out[y0:y1] = fk.to_rgba8(cv)

    def _draw_row(self, cv, row, up):
        """行 row を、event の添字 up までの分だけ描く（行の帯の座標。y は 0〜row_h）"""
        L = self.L
        C = self.C
        pal = self.pal
        show = self.show
        rh = L.row_h
        y0 = 0.0
        inset = 0.0 if rh < 5 else max(1.0, round(rh * 0.18))
        # 1判定で終わる試行は、その判定が来た時点から点で描く（赤の棒を一瞬出さない）
        if row["trivial"]:
            if self.trivial == "hide":
                return
            for (k, kind, pos, ok) in row["items"]:
                if k <= up and kind in ("test", "assert") and self.in_window(pos):
                    cx = self.cell_cx(pos) if kind == "test" else self.bx(pos)
                    if cx is not None:
                        fk.circle(cv, (cx, rh / 2.0), max(1.2, min(4.0, rh * 0.18)),
                                  pal["muted"])
            return
        counts = Counter()
        fails = set()
        afails = set()
        backs = set()
        for (k, kind, pos, ok) in row["items"]:
            if k > up:
                break
            if kind == "test":
                if ok:
                    counts[pos] += 1
                else:
                    fails.add(pos)
            elif kind == "assert":
                if not ok:
                    afails.add(pos)
            else:
                backs.add(pos)
        hm = self.pre["heat_max"]
        for pos, c in counts.items():
            k = self.cell_col(pos)
            if k is None or self.cols[k][0] != "char":
                continue
            heat = 1.0 if hm <= 1 else 0.55 + 0.45 * math.log(c) / math.log(hm)
            x0 = self.bx(pos)
            x1 = self.bx(pos + 1) if pos + 1 <= self.window[1] else x0 + L.pitch
            fk.rect(cv, (x0, y0 + inset, x1, rh - inset), _with_alpha(pal["fg"], heat))
        if row["result"] == "match" and up >= row["e1"] and rh >= 8:
            xa, xb = self.bx(row["s"]), self.bx(row["match_end"])
            if xb - xa >= 1:
                fk.rect(cv, (xa + 1.5, 1.5, xb - 1.5, rh - 1.5), pal["fg"], width=3)
            else:
                fk.polyline(cv, [(xa, 1), (xa, rh - 1)], pal["fg"], 3, cap="butt")
        if "arrow" in show and rh >= 8:
            # 後戻りした境目: 帯の下半分に暗い切れ込み（赤の印がある境目には描かない）
            red = afails if "fail" in show else set()
            for pos in backs:
                if pos in red or not self.window[0] <= pos <= self.window[1]:
                    continue
                x = self.bx(pos)
                fk.polyline(cv, [(x, rh * 0.5), (x, rh - inset)], pal["dim"], 3, cap="butt")
        if "fail" in show:
            # 失敗した判定: そのマスの左端に、行の高さいっぱいの赤の縦棒（「止まった」印）
            bw = max(3.0, round(C * 0.1))
            for pos in fails:
                if self.cell_cx(pos) is None:
                    continue
                x0 = self.bx(pos) + L.gap / 2.0
                fk.rect(cv, (x0, 1.0 if rh >= 6 else 0.0, x0 + bw,
                             rh - (1.0 if rh >= 6 else 0.0)), pal["accent"])
            rad = max(1.2, min(4.0, rh * 0.14))
            for pos in afails:
                if self.window[0] <= pos <= self.window[1]:
                    fk.circle(cv, (self.bx(pos), rh / 2.0), rad, pal["accent"])

    # --- カウンタ ---
    def _render_counter(self, state, t):
        L = self.L
        text = self.counter_text_at(state, t)

        def draw(cv, oy, ox):
            pal = self.pal
            x = float(L.right)
            base = L.counter_base - oy
            for ch, numeric in reversed(self._counter_chars(text)):
                if numeric and ch.isdigit():
                    adv = self._digit_w()
                    self._glyph(cv, ch, pal["fg"], x - adv / 2.0, base, size=L.cfs, mono=True,
                                ox=ox)
                elif numeric:
                    adv = self.g.advance(ch, L.cfs, True)
                    self._glyph(cv, ch, pal["fg"], x - adv / 2.0, base, size=L.cfs, mono=True,
                                ox=ox)
                else:
                    adv = self.g.advance(ch, L.cfs, False)
                    if not ch.isspace():
                        self._glyph(cv, ch, pal["fg"], x - adv / 2.0, base, size=L.cfs,
                                    mono=False, ox=ox)
                x -= adv

        # 描き直すのは右寄せのカウンタの横の範囲だけ（一番広い文字列＋1字ぶんの余裕）
        xa = L.right - L.counter_w - L.cfs
        self._region("counter", L.counter_y, L.counter_y + L.counter_h, text, draw,
                     xa, L.right + L.cfs // 2)

    def all_sprites(self):
        """text_meta へ申告する文字（どのコマにも出うる字を先に描いておく）"""
        L = self.L
        pal = self.pal
        if "pattern" in self.show:
            for t in self.toks:
                self.g.sprite(t["text"], L.fs, pal["fg"] if t["boxed"] else pal["muted"])
        if "cells" in self.show:
            for kind, p in self.cols:
                if kind == "char":
                    ch, cname = _cell_glyph(self.trace.text[p], self.space, self.g)
                    self.g.sprite(ch, L.fs, pal[cname])
                elif kind in ("lell", "rell"):
                    self.g.sprite("…", L.fs, pal["muted"])
        if "count" in self.show:
            seen = set()
            for text in self.counter_texts():
                for ch, numeric in self._counter_chars(text):
                    if not ch.isspace() and (ch, numeric) not in seen:
                        seen.add((ch, numeric))
                        self.g.sprite(ch, L.cfs, pal["fg"], mono=numeric)
        return list(self.g.used)

    def drawn_chars(self):
        """図に出うる字の集合（フォントに無い字を先に見つけるため）"""
        chars = set()
        if "pattern" in self.show:
            for t in self.toks:
                chars.update(t["text"])
        if "cells" in self.show:
            for kind, p in self.cols:
                if kind == "char":
                    chars.update(_cell_glyph(self.trace.text[p], self.space, self.g)[0])
                elif kind in ("lell", "rell"):
                    chars.add("…")
        if "count" in self.show:
            for text in self.counter_texts():
                chars.update(text)
                chars.update("0123456789")
        return {ch for ch in chars if not ch.isspace()}


# --- 公開関数 -------------------------------------------------------------------

def _check_window(fn, window, n):
    if window is None:
        if n > _MAX_CELLS:
            raise ValueError(
                f"{fn}: 文字列が {n} 字あります（マスに描けるのは {_MAX_CELLS} 字まで）。"
                f"window=(開始, 終了) で描く範囲を指定してください（外は「…」に畳みます）")
        return (0, n)
    if (not isinstance(window, (tuple, list)) or len(window) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) for v in window)):
        raise ValueError(f"{fn}: window は (開始, 終了) の整数で指定してください: {window!r}")
    a, b = window
    if not 0 <= a < b <= n:
        raise ValueError(
            f"{fn}: window=({a}, {b}) は 0 ≤ 開始 < 終了 ≤ {n}（文字列の長さ）にしてください")
    if b - a > _MAX_CELLS:
        raise ValueError(
            f"{fn}: window の幅 {b - a} がマスの上限 {_MAX_CELLS} を超えます")
    return (a, b)


def _count_labels(fn, count_label):
    """count_label → (拍と回転の間の書式, 最後の書式)"""
    if isinstance(count_label, str):
        return (count_label, count_label)
    if (isinstance(count_label, (tuple, list)) and len(count_label) == 2
            and all(isinstance(s, str) for s in count_label)):
        return (count_label[0], count_label[1])
    raise TypeError(
        f"{fn}: count_label は書式の文字列か、(拍と回転の間の書式, 最後の書式) の"
        f"2つの文字列で指定してください: {count_label!r}")


def _check_at(fn, at, n):
    """at（先頭の拍の時刻）を検証して list にする。n は拍の数"""
    if at is None:
        return None
    if not isinstance(at, (list, tuple)) or not at:
        raise ValueError(f"{fn}: at は秒のリスト（例 [1.2]）で指定してください: {at!r}")
    for i, t in enumerate(at):
        _require_number(fn, f"at[{i}]", t, 0, None)
    for i in range(1, len(at)):
        if not at[i] > at[i - 1]:
            raise ValueError(
                f"{fn}: at は単調増加で指定してください: at[{i - 1}]={at[i - 1]!r}, "
                f"at[{i}]={at[i]!r}")
    if len(at) > n:
        raise ValueError(
            f"{fn}: at の数（{len(at)}）が拍の数（{n}）より多い。at は先頭の拍の時刻だけを"
            f"渡すか、beats= を拍の多い単位にしてください")
    return list(at)


def _check_glyphs(fn, fig, glyphs):
    """図に出うる字がフォントにあるか（無い字は豆腐にせず、regex_view の言葉で止める）"""
    missing = sorted(ch for ch in fig.drawn_chars() if not glyphs.has(ch))
    if missing:
        shown = "・".join(f"'{ch}'(U+{ord(ch):04X})" for ch in missing[:6])
        names = " / ".join(os.path.basename(r.path) for r in glyphs.refs())
        raise ValueError(
            f"{fn}: フォント（{names}）に無い字があります: {shown}"
            f"{' ほか' if len(missing) > 6 else ''}。その字を持つフォントを font= か "
            f"mono_font= に渡すか、式・文字列・count_label からその字を除いてください")


def _space_shown(fig, show):
    """space の字が画面に出るか（マスか式の部品に、空白として見せる字があるか）"""
    if "pattern" in show and any(" " in nd.text for nd in fig.trace.nodes):
        return True
    if "cells" in show:
        a, b = fig.window
        for ch in fig.trace.text[a:b]:
            if ch not in "\n\t" and _cell_glyph(ch, "\0", fig.g)[0] == "\0":
                return True
    return False


def _has_trivial_row(fig):
    """rows に描く行の中に、1判定で終わった試行（trivial の描き分けが効く行）があるか"""
    rows = fig.pre["rows"]
    return any(rows[r]["trivial"] for r in fig.row_list)


def regex_view(trace, *, view="tape", beats="test", pace=None, at=None, duration=None,
               hold_end=1.0, size=None, cell=64, window=None,
               show=("pattern", "cells", "spans", "cursor", "start", "arrow", "fail", "count"),
               count="tests", count_label="判定 {n:,} 回", count_to=None, count_roll=1.2,
               space="␣", font=None, mono_font=None, weight=None, colors=None,
               rows_max=40, trivial="mark"):
    """後戻り型の照合を、regex_trace が記録した手順から描いた透過の動画 Object を返す。

    trace: regex_trace(式, 文字列) の戻り値（RegexTrace）
    view: 'tape'（式の箱・文字のマス・取り分の下線）/ 'rows'（開始位置ごとに1行・三角形）/
        'both'（上に tape、下に rows）
    beats: 1拍の単位。'test'（判定）/ 'backtrack' / 'attempt'（開始位置）/
        'literal:<字>'（その字のリテラルの判定だけ。例 'literal:='）/ 'step'（全 event）
    pace: 拍の速さ。None は framekit.pace の既定（0.6 秒×3拍 → 0.82 倍ずつ速く → 最後は
        1コマに4拍）。数値は1拍の秒（一定）。dict は framekit.pace の first・slow・ratio・
        min_dt の上書き
    at: 先頭の拍の時刻のリスト（Object の先頭からの秒。語の時刻に合わせる。拍の数まで）
    duration: 図の長さ（秒）。pace を省くと、拍がちょうどこの長さを埋める速さにする
        （最後の拍 + hold_end + count_roll = duration。拍が少なくて1拍 1.2 秒でも余る分は
        最後の絵を保持する）。pace を渡したときは長さだけを決める
    hold_end: 最後の拍（と count_to の回転）の後に絵を保持する秒
    size: (幅, 高さ)。省略時は中身に合わせる。指定時は中身を中央に置く（入らなければ ValueError）。
        幅・高さとも 4096 まで
    cell: 1マスの大きさ px（文字は cell × 0.61。64 で 39px）。図が Project の画面より
        大きくなると警告する（cell を小さくするか window で範囲を切る）
    window: (開始, 終了)。長い文字列はこの範囲だけをマスで描き、外は「…」の1マスに畳む。
        None で 48 字を超えると ValueError
    show: 描く要素の組（'pattern'・'cells'・'spans'・'cursor'・'start'・'arrow'・'fail'・'count'）
    count: カウンタの数え方（'tests'・'matches'・'backtracks'・'steps'・'attempts'）。
        意味は regex_vm のモジュール docstring
    count_label: カウンタの書式。n（回数）・man（n/1e4）・oku（n/1e8）が使える。
        (拍と回転の間の書式, 最後の書式) の組にすると、カウンタが最後の値（count_to があれば
        回し終えた値、無ければ最後の拍の値）に着いたときだけ2つ目で出す
        （例 ('判定 {n:,} 回', '約{oku:.0f}億回（模式）')。粗い書式1つだけだと拍の間
        「約0億回」のまま動かないので警告する）。
        画面の数は「このモデルの回数」なので、実演の秒と並べるときは「模式」と分かる言葉を添える
    count_to: 最後の拍の後、count_roll 秒でカウンタをこの値まで回す（regex_count の値を見せる用）。
        拍を見せ終えた所から回転だけを見せるなら、fig[fig.figure.beats_end:] で切り出す
    count_roll: count_to まで回す秒
    space: 空白を見せる字（既定 '␣'）
    font: 文字のフォント（カウンタの「判定」「回」など。省略時は text() と同じ既定）
    mono_font: 式・マス・数字の等幅フォント（省略時は OS の等幅フォント。字が無ければ font）
    weight: font の太さ（可変フォントの wght）
    colors: 色の上書き（framekit の PALETTE の名前 fg・accent・muted・line・dim・panel）
    rows_max: rows の行がこれを超えたら行の高さを縮める（最小 2px）
    trivial: rows で1判定で終わった試行を 'mark'（点だけ）/ 'hide'（描かない）

    戻り値の Object: obj.figure.beat_times（各拍の秒）・obj.figure.count_final
    （最後の拍の後のカウンタ）・obj.figure.beats_end（拍の終わりの秒）・obj.figure.size・
    obj.figure.n_frames・obj.figure.count_at(i)（コマ i のカウンタの値）・
    obj.figure.count_text(i)（コマ i のカウンタの文字列。字幕と合っているかの確認に）・
    obj.figure.cell_box(p)（文字 p のマスの矩形。注記を置く位置に使う）。
    表示は time()（引数なしで尺ぶん）と move()。
    生成物は __cache__/artifacts/frames/<鍵>.mov（framekit.build）。dry_run では描かない。
    """
    fn = "regex_view"
    if not isinstance(trace, RegexTrace):
        raise TypeError(
            f"{fn}: trace には regex_trace(式, 文字列) の戻り値を渡してください: {trace!r}")
    _require_choice(fn, "view", view, _VIEWS)
    if not isinstance(beats, str) or not (beats in _BEAT_KINDS or (
            beats.startswith("literal:") and len(beats) == len("literal:") + 1)):
        raise ValueError(
            f"{fn}: beats は {_BEAT_KINDS} か 'literal:<1字>' で指定してください: {beats!r}"
            f"{_suggest_hint(beats, _BEAT_KINDS) if isinstance(beats, str) else ''}")
    if isinstance(show, str) or not isinstance(show, (tuple, list, set, frozenset)):
        raise ValueError(f"{fn}: show は要素の名前の組（タプル）で指定してください: {show!r}")
    for s in show:
        _require_choice(fn, "show の要素", s, _SHOW_ITEMS)
    show = frozenset(show)
    _require_choice(fn, "count", count, _COUNT_KINDS)
    _require_choice(fn, "trivial", trivial, _TRIVIAL)
    if isinstance(cell, bool) or not isinstance(cell, int) or not 24 <= cell <= 256:
        raise ValueError(f"{fn}: cell は 24〜256 の整数で指定してください: {cell!r}")
    if isinstance(rows_max, bool) or not isinstance(rows_max, int) or rows_max < 1:
        raise ValueError(f"{fn}: rows_max は 1 以上の整数で指定してください: {rows_max!r}")
    _require_number(fn, "hold_end", hold_end, 0, None)
    _require_number(fn, "count_roll", count_roll, 0, None)
    if duration is not None:
        _require_number(fn, "duration", duration, 0, None)
        if duration <= 0:
            raise ValueError(f"{fn}: duration は 0 より大きくしてください: {duration!r}")
    if not isinstance(space, str) or len(space) != 1:
        raise ValueError(f"{fn}: space は1字で指定してください: {space!r}")
    labels = _count_labels(fn, count_label)
    if size is not None:
        if (not isinstance(size, (tuple, list)) or len(size) != 2
                or any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in size)):
            raise ValueError(f"{fn}: size は (幅, 高さ) の正の整数で指定してください: {size!r}")
        if size[0] > _CANVAS_MAX or size[1] > _CANVAS_MAX:
            raise ValueError(
                f"{fn}: size={size[0]}x{size[1]} が上限 {_CANVAS_MAX}px を超えます")
        size = (int(size[0]), int(size[1]))
    window = _check_window(fn, window, len(trace.text))
    pal = fk.palette(fn, colors)
    if not trace.events:
        raise ValueError(f"{fn}: trace に event がありません")

    fps = _resolve_fps(fn, None)
    fps_frac = _fps_fraction(fps)
    fps_f = float(fps_frac)

    beat_ev = _beat_events(fn, trace, beats)
    pre = fk.memo("regex_view.pre",
                  [trace.pattern, trace.text, trace.mode, _REGEX_VM_VER, count],
                  lambda: _precompute(trace, count))
    count_final = pre["cum"][-1]
    if count_to is not None:
        if isinstance(count_to, bool) or not isinstance(count_to, int) or count_to < 0:
            raise ValueError(f"{fn}: count_to は 0 以上の整数で指定してください: {count_to!r}")
        if count_to < count_final:
            raise ValueError(
                f"{fn}: count_to（{count_to:,}）が記録した回数（{count_final:,}）より小さい")
    roll = float(count_roll) if count_to is not None else 0.0
    at = _check_at(fn, at, len(beat_ev))
    times, beats_end, total = _beat_times(
        fn, len(beat_ev), pace, at, duration, float(hold_end), roll, fps_f)
    n_frames = fk.n_frames_for(total, fps_frac)

    glyphs = _Glyphs(fn, font, mono_font, weight)
    if not glyphs.has(space):
        raise ValueError(f"{fn}: space の字 {space!r} がフォントにありません")
    fig = _RegexFigure(
        fn, trace, view=view, show=show, window=window, cell=cell, space=space,
        glyphs=glyphs, pal=pal, count=count, count_label=labels, count_to=count_to,
        pre=pre, beat_ev=beat_ev, times=times, beats_end=beats_end, roll=roll, fps=fps_f,
        rows_max=rows_max, trivial=trivial, size=size)
    L = fig.L
    _check_glyphs(fn, fig, glyphs)
    proj = current_project()
    if proj is not None and (L.W > int(proj.width) or L.H > int(proj.height)):
        _warn(proj,
              f"{fn}: 図が {L.W}x{L.H}px で、画面 {proj.width}x{proj.height} からはみ出します"
              f"（{trace.pattern!r} × {len(trace.text)} 字・cell={cell}）。cell を小さくするか、"
              f"window=(開始, 終了) で描く範囲を切ってください")
    if ("count" in show and count_to is not None and labels[0] == labels[1]
            and fig._counter_text(0) == fig._counter_text(count_final)
            != fig._counter_text(count_to)):
        _warn(proj,
              f"{fn}: count_label {labels[0]!r} では、拍の間カウンタが "
              f"{fig._counter_text(0)!r} のまま動きません。count_label="
              f"('判定 {{n:,}} 回', {labels[0]!r}) のように (拍と回転の間, 最後) の2つに"
              f"分けられます")
    sprites = fig.all_sprites()
    meta = fk.text_meta(sprites, width=L.W, height=L.H,
                        content=f"regex_view({trace.pattern!r} × {trace.text!r})")
    if meta is not None:
        # 文字は panel の面の上に描く（下地あり）。panel を透明にしたときは申告しない
        meta["background"] = pal["panel"][3] > 0

    # 鍵（同一出力なら同一鍵: 効かない条件のパラメータは入れない）
    shown_count = "count" in show
    has_rows = view in ("rows", "both")
    params = {
        "pattern": trace.pattern, "text": trace.text, "mode": trace.mode,
        "vm": _REGEX_VM_VER, "view": view, "beats": beats,
        "times": times, "n_frames": n_frames, "window": list(window),
        "show": sorted(show),
        # カウンタ（回転の秒は times・beats_end・n_frames に入る）
        "count": count if shown_count else None,
        "count_label": list(labels) if shown_count else None,
        "count_to": count_to if shown_count else None,
        "count_roll": roll if (shown_count and count_to is not None) else None,
        "beats_end": beats_end,
        "space": space if _space_shown(fig, show) else None,
        "colors": {k: list(v) for k, v in pal.items() if k != "accent" or "fail" in show},
        "cell": cell, "size": [L.W, L.H],
        "rows": ([L.row_h, trivial if _has_trivial_row(fig) else None] if has_rows else None),
    }

    def count_at(i):
        """コマ i に出るカウンタの値"""
        t = i / fps_f
        return fig.counter_value(fig.state_of(fig.beat_at(t)), t)

    def count_text(i):
        """コマ i に出るカウンタの文字列（show に 'count' が無くても書式どおりに返す）"""
        t = i / fps_f
        return fig.counter_text_at(fig.state_of(fig.beat_at(t)), t)

    def cell_box(p):
        """文字 p のマスの矩形 (x0, y0, x1, y1)（図の中の px）。窓の外・マスを描かない図は None"""
        k = fig.cell_col(p)
        if k is None or L.cells_y is None:
            return None
        x0 = fig.col_x[k]
        return (x0, L.cells_y, x0 + cell, L.cells_y + cell)

    info = {"beat_times": list(times), "count_final": count_final, "size": (L.W, L.H),
            "n_beats": len(times), "beats_end": beats_end, "n_frames": n_frames,
            "count_at": count_at, "count_text": count_text, "cell_box": cell_box}
    return fk.build(fn, kind="regex_view", ver=_REGEX_VIEW_VER, params=params,
                    draw=fig.frame, n_frames=n_frames, size=(L.W, L.H), fps=fps,
                    fonts=glyphs.refs(), text=meta, info=info)
