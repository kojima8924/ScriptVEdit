# -*- coding: utf-8 -*-
"""文字列の組み替え: text_transition()（状態 A→B を字単位で動かす）と odometer()（回る数字）

    t = text_transition(["Fundation", "Foundation"], enter="drop", size=96)
    t.time(3) <= move(x=0.5, y=0.5, anchor="center")
    n = odometer(2**31 - 1, 2**31, base=2, digits=32, signed="twos", group=8, sep=" ")

文字列の状態を2つ以上並べると、トークン（字・語・コードの部品）を状態どうしで対応させ、
残る字は滑り、入れ替わる組は上下に離れて運ばれ、変わる数字は回り（roll）、
消える字は落ち、新しい字は現れる。odometer() は数字の桁を回す糖衣。

端のコマは text_image と画素が一致する
--------------------------------------
各状態のコマ（hold の間も）は text_image(その状態, **fmt, **obj.figure.text_image_kwargs)
と画素が一致する（text_image_kwargs は canvas と、fmt に無ければ align=anchor・padding）。
やり方:

- 各状態を textimage._build_layout で配置する。キャンバスは全状態の外接の和
  （幅は自然な寸法の最大、高さは最大＋動きの余白）。横の位置は anchor（= text_image の
  align）で決まる。余白の既定は text_image の自然な寸法と同じ（canvas 指定時の既定の
  「縁取り・影だけ」だと、張り出す字の端が 1px 切れる）。
- 字の x は「区間の x + getlength(区間の先頭からその字まで) − getlength(その字)」
  （カーニングで前の字の送り幅が詰まる分も入る。getlength(その字の前まで) だと
  メイリオ・Arial の 'AV' でずれる。実測）。Pillow は x を最も近い整数へ丸めて描く
  （x=10.5 は 11 と同じ画素。2026-10-03 実測、Pillow 10.4）。
- 止まっている字は、字ごとの L マスク（縁取りのマスク・塗りのマスク）を
  Pillow の ImageDraw と同じ整数の式（out = (out·(255−m) + 255·m) / 255 の丸め）で
  共有マスクへ順に重ねる。文字列を一括で描いたものと差 0（メイリオ・Consolas Bold・
  源真ゴシック P Bold・Arial・Times・MS 明朝、縁取り 0 / 4px、CJK を含む。実測）。
  max で重ねると縁取りつきの CJK で最大 47 階調ずれるので使わない。
- 着色と重ね順は textimage._render と同じ（影 → 縁取り → 色ごとの塗り）。
- 字の整形は text_image と同じ layout_engine=BASIC（textimage._open_font が固定する。
  合字は作らない）なので、1字ずつ描いても区間をまとめて描いても字形は同じ。
- インクの有無は字形で決める（Unicode のカテゴリでは決めない。Consolas の U+200C の
  ように、Cf でも目に見える字形を持つフォントがあり、text_image はそれを描く）。

動く字
------
動く字は、縁取りと塗りの小画像を1回だけ描き、framekit.blit と同じ座標と変換で端数位置へ
置く（縁取りと塗りを2チャンネルにまとめて、blit の変換の核 framekit.warp_patch で1回だけ
写す。縮小は blit と同じく、どの倍率でも先に INTER_AREA で縮めてから置く。縁取りのマスクの和は 1−(1−a)(1−b)、
塗りは事前乗算の over）。整数位置・等倍では画素がそのまま置かれるので、動き出す前・
止まった後の字は端のコマと同じ画素になる。

- 大きさが変わる字は、前後の状態の大きい方の字形を縮めて置く（字形が滑らかに伸び縮み
  する。そのつどの大きさで描き直すと、ヒンティングで輪郭が毎コマ揺れた）。縁取りは字形の
  上で border ÷ 倍率 の太さにして、縮めた後も border px のままにする（Pillow 10 の
  stroke_width は整数なので、隣り合う整数の太さの縁取りを端数で補間する）。大きい字形を
  縁取りごと縮めると、縁取りが細くなり端のコマで太さが飛んだ（α の面積で約 −25%）。
- 消える字は下の層（ほかの字の縁取りより下）に置く。
- 入れ替わる組（move）は、離れる → 運ぶ → 置く（左へ行く字が上、右へ行く字が下）。
  縦（行から離れる量）は窓の 0〜30% で離れきり、70〜100% で戻る。横の運びは 25〜75% で
  easing どおりに進む（角は丸い）。行の中の残る字は、組が離れきっている間（30〜70%）だけ
  滑る。離れる高さは、行の字（両方の状態の、縁取りを含むインク）を隙間をあけて避ける
  高さから始め、動く間の字の箱が同じ遷移のほかの字（止まっている字・滑る字・消える字・
  現れる字・ほかの組）に重ならない候補（浅い順）を選ぶ（構築時に字の箱を標本化して
  確かめる）。縦と横を同じ easing の1本の弧で動かすと、降りきる前に横へ速く動いて
  隣の読点を突き抜けた（'[1, 2, 10]' → '[1, 10, 2]' の '2' と ','）。
  候補は swing × 行の字の高さまで（既定 1 倍）。縦にだけ動く間は、隣の字との重なりを
  横へ広げて数えない（もとから箱が接している隣を縦にすり抜けるだけで重なりに数え、
  速く抜ける深い候補が選ばれて字の 2.5 倍も振れ、画面の下で切れた）。
- 回る字（roll）は字の窓で縦横とも切り抜く。窓は横が送り幅、縦が数字の帯（縁取りを
  含む '0'〜'9' のインク + size × 0.08）で、縁取りを含むインクの外接との和。前後の状態の
  窓を補間する（縦を字のマス ascent+descent にすると、ascent の大きいフォントで回る数字が
  行の上下へ大きく流れ出た）。
  窓の外へ出るまで縦に流す。プロポーショナルの数字は幅が変わるので、後ろの字は幅の差の
  分だけ滑る（等幅にしたいときは mono のフォント）。
- 1コマで字幅の 0.5 倍以上動く区間だけ、中間位置の平均で描く（motion_blur。
  シャッター 180 度。点は3つから、間隔が 3px を超えないよう最大 10 点まで増やす。
  3点のまま・字幅の割合の間隔だと、速い字の横の縁が離れた像に分かれて見えた）。
- 段取り: 消える字が先に去り、残る字が詰め、空いた所へ新しい字が入る（_PHASES）。
  開きかけの隙間へ入ると、滑ってくる隣の字に重なる。

重さ（実測。Windows・Python 3.10・numpy 2.0・opencv 4.13・Pillow 10.4。2026-10-03）
-----------------------------------------------------------------------------------
1コマの描画（draw）の中央値（最大）と、1秒ぶんの生成時間（draw + qtrle の符号化）。
CPU の負荷が低いとき（12%）の値。負荷が高いと 2〜3 倍になる:
- 'Fundation' → 'Foundation'（源真ゴシック 150px・縁取り 6・drop）: 8.9ms（22ms）、0.33 秒
- 公式の正規表現 → \\s+$（Consolas Bold 96px → 170px・縁取り 4・fall）: 7.6ms（14ms）、0.31 秒
- コード 92 トークン（Consolas Bold 40px・縁取り 3・move 25 / keep 53 が全部動く）:
  16ms（速い字が多くモーションブラーの点が増えるコマで 37ms）、0.72 秒
- '[1, 2, 10]' → '[1, 10, 2]'（Consolas Bold 150px・縁取り 5。組が上下に離れて合成の
  範囲がキャンバス全体になる）: 22ms（27ms）、0.71 秒
- 32ビットの odometer（Consolas Bold 64px・32 桁が roll）: 3.5ms（6.7ms）、0.25 秒
hold の間のコマは同じ絵を使い回す（qtrle は同じ画素を書かないので、ほぼ 0 バイト）。
入れ替わる組の離れる高さの計算（構築時に1回）は、move が数個なら十数 ms、
コード 92 トークン（move 25）で 0.1 秒ほど。

制約: 1状態 400 トークン・3行まで（長い字幕は text_image を切り替える）、
キャンバスは 8192px まで。max_width（折り返し）は受けない（状態ごとに折り返しが
変わると字が大きく飛ぶ）。

numpy・opencv-python・Pillow は optional 依存（framekit.need が遅延 import する）。
dry_run では draw を呼ばない（配置・対応表・道筋の計算に Pillow とフォントだけを使う）。
鍵には framekit.build(fonts=) を通して Pillow の版が入る（字の描画を Pillow / FreeType に
任せ、その内部の丸めを再現して端のコマの一致を保証しているため）。
"""

import math
import random
import re
import unicodedata
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from fractions import Fraction

import scriptvedit.framekit as fk
import scriptvedit.textimage as _ti
from scriptvedit.cache import _file_fingerprint
from scriptvedit.filters.video import _fps_fraction
from scriptvedit.state import _suggest_hint
from scriptvedit.stillseq import _resolve_fps, _round_frame, _sec_fraction
from scriptvedit.text import _resolve_font
from scriptvedit.validate import _require_choice, _require_number


# --- 定数 ---

# 描画の版。動かし方・重ね方・配置を変えたら上げる（鍵に入る）
#   2: 大きさの変わる字の縁取りを border px に保つ（端数の太さを補間）・入れ替わる組は
#      離れる → 運ぶ → 置く（残る字は離れている間に滑る）・roll の窓を縦横で切る・
#      インクの有無を字形で判定・縮小はいつも INTER_AREA・scatter をキャンバスの中に収める・
#      モーションブラーの点の間隔を 2px 以下に・replace の既定 'auto'
#   3: 入れ替わる組の離れる量に上限（swing。既定は行の字の高さの 1 倍）・縦にだけ動く間は
#      隣の字との重なりを横へ広げて数えない（深く振れる候補が選ばれていた）・stagger の既定
#      0.02 と広がり 30% まで・fall / scatter / drop / rise を小さく・回る字の窓の縦を
#      数字の帯に（途中のコマを落ち着かせる。見本の場面）
_TEXTMOVE_VER = "3"

# text_image と同じ書式引数（max_width / canvas / background / missing は受けない）
_FMT_DEFAULTS = {
    "size": 64, "font": None, "font_index": 0, "weight": None, "color": "white",
    "markup": False, "styles": None, "line_spacing": 1.5, "align": None,
    "border": 0, "border_color": "black",
    "shadow": (0, 0), "shadow_color": "black@0.6", "shadow_blur": 0,
    "padding": None,
}

_UNITS = ("char", "word", "code")
_MATCHES = ("auto", "edit", "position")
_PREFER_OPS = ("replace", "insert", "delete")
_MOVES = ("slide", "arc")
_REPLACES = ("auto", "roll", "fade", "swap")
_LEAVES = ("fade", "fall", "scatter")
_ENTERS = ("fade", "drop", "rise")
_ANCHORS = ("left", "right", "center")
_ROLL_DIRS = ("auto", "up", "down")
_CARRIES = ("ripple", "together")
_SIGNED = (False, True, "twos")

# トークンの切り方（char は1字ずつ。改行はトークンにしない）
_UNIT_RES = {
    "char": None,
    # 語と空白の連なり（空白もトークン）
    "word": re.compile(r"[^\S\n]+|\S+"),
    # 識別子・数（小数・指数）・文字列リテラル・空白の連なり・1字の記号
    "code": re.compile(
        r"[^\W\d]\w*"
        r"|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
        r"|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'"
        r"|[^\S\n]+"
        r"|."),
}

_MAX_TOKENS = 400          # 1状態あたりのトークン数の上限
_MAX_LINES = 3             # 1状態あたりの行数の上限
_MAX_STATES = 50
_ROLL_MIN_SEC = 0.25       # 回る字1つにかける時間の下限（秒）
_STAGGER = 0.02            # stagger の既定（秒。0.03 では字が多いと波打って騒がしかった）
_STAGGER_MAX_SHARE = 0.3   # stagger の広がりは duration の 30% まで（超えたら詰める）
# 動きの段取り（各字の窓のうち、どこで動くか。L に対する割合）。
# 消える字が先に去り、残る字が詰め、空いた所へ新しい字が入る。新しい字が開きかけの
# 隙間へ入ると、滑ってくる隣の字に重なる（実測: '\s+$' → '\s++$' の赤い '+' と '$'）。
# キーは (消える字があるか, 現れる字があるか)。
_PHASES = {
    (True, True): {"leave": (0.0, 0.55), "enter": (0.45, 1.0), "other": (0.2, 0.85)},
    (True, False): {"leave": (0.0, 0.6), "other": (0.25, 1.0)},
    (False, True): {"enter": (0.45, 1.0), "other": (0.0, 0.75)},
    (False, False): {"other": (0.0, 1.0)},
}
_ARC_RATIO = 0.8           # move='arc' で滑る字の弧の高さの上限 ÷ 行の高さ
_ARC_KEEP_RATIO = 0.35     # move='arc' で滑る字の弧の高さ ÷ 動く距離
# 消える字・現れる字の動きの大きさ（途中のコマを騒がしくしない程度。見本の場面 の
# fall・見本の scatter で、交互に 18 度傾いて行の高さの 0.9 倍落ちる・±50 度回りながら
# 1.25 倍に膨らんで散るのは、目が字を追えなかった）
_FALL_RATIO = 0.6          # 落ちる字の落ちる距離 ÷ 行の高さ
_FALL_TILT = 6.0           # 落ちる字の傾き（度。隣どうしで向きを変える）
_DROP_RATIO = 0.45         # drop / rise の距離 ÷ 行の高さ
_SCATTER_RATIO = 0.5       # scatter の飛ぶ距離 ÷ 行の高さ
_SCATTER_SPIN = 20.0       # scatter の回転の上限（度）
_SCATTER_FAN = (-135.0, -45.0)   # scatter の飛ぶ向きの範囲（度。-90 が真上）
_SCATTER_GROW = 0.1        # scatter の膨らみ（1 + これ × 進み）
_BLUR_RATIO = 0.5          # 1コマの移動が字幅のこの倍以上ならモーションブラー
_BLUR_SAMPLES = 3          # 中間位置の数（最少）
_BLUR_MAX_SAMPLES = 10     # 中間位置の数（最多）
_BLUR_STEP_PX = 3.0        # 中間位置の間隔の上限（px）。字の横の縁が離れた像に分かれない
_BLUR_SHUTTER = 0.5        # 中間位置を置く区間 ÷ 1コマの移動（シャッター 180 度）
_SWAP_MIN_SCALE = 0.2

# 入れ替わる組（move）の動き: 離れる → 運ぶ → 置く（時刻の割合 pr で）。
# 縦（行から離れる量）は pr = 0〜_LIFT で 0→1、1−_LIFT〜1 で 1→0（smoothstep）。
# 横（と大きさ・色）は pr = _CARRY〜1−_CARRY で easing どおりに進む（_CARRY < _LIFT なので
# 角は丸い）。行の中の残る字は、組が離れきっている間（_LIFT〜1−_LIFT）だけ滑る。
# 縦と横を同じ easing の1本の弧で動かすと、降りきる前に横へ速く動いて隣の読点を
# 突き抜けた（実測: '[1, 2, 10]' → '[1, 10, 2]' の '2' と ','、Consolas Bold 150px で 45px）
_LIFT = 0.3
_CARRY = 0.25
_ROUTE_GAP_RATIO = 0.06            # 離れた字と行のインクの隙間 ÷ 行の高さ（2px 以上）
_ROUTE_DEPTHS = (1.0, 1.3, 1.7, 2.2)   # 離れる量の候補（行のインクをちょうど避ける量の倍）
# 離れる量の上限の既定（行の字の高さ = ascent + descent の倍。text_transition(swing=)）。
# 上限なしでは '[1, 2, 10]' の組が字の大きさの 2.2〜2.5 倍ずつ上下に振れ、size=110 で
# 画面の下で切れた（見本の場面）
_SWING = 1.0
_ROUTE_REST = 0.3                  # 端から行の高さのこの倍までは重なりを数えない
_ROUTE_PAD = 2.0                   # 重なりを調べるとき、動く字の箱を広げる量（px）
_ROUTE_EST = 1.5                   # 余白の見積もり ÷ 行の高さ（合わなければ要る余白で作り直す）

# 回る数字の窓の余白（px）
_ROLL_PAD = 1
# 回る数字の窓の縦: 数字の帯の上下に足す余白（size の倍。_token_window）
_ROLL_BAND_PAD = 0.08

# 字の絵のメモの上限（バイト。大きさの変わる字は毎コマ別の字形になるので、古いものから捨てる）
_SPRITE_BUDGET = 96 << 20

# 字形のインクの有無のメモ（(フォントの鍵, 字) -> bool）
_INK_MEMO = {}
_INK_MEMO_MAX = 1 << 16


# --- 小物 ---

def _per(fn, name, value, n, what):
    """数1つか n 個のリストを、n 個の float のリストにする"""
    if isinstance(value, (list, tuple)):
        if len(value) != n:
            raise ValueError(
                f"{fn}: {name} は数か、{what}の数（{n}）だけ並べたリストで指定してください"
                f"（{len(value)} 個あります）: {value!r}")
        vals = list(value)
    else:
        vals = [value] * n
    for i, v in enumerate(vals):
        _require_number(fn, f"{name}[{i}]" if isinstance(value, (list, tuple)) else name,
                        v, 0, None)
    return [float(v) for v in vals]


def _lerp(a, b, t):
    return a + (b - a) * t


def _lerp4(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _smooth(e0, e1, x):
    if x <= e0:
        return 0.0
    if x >= e1:
        return 1.0
    t = (x - e0) / (e1 - e0)
    return t * t * (3 - 2 * t)


def _ease_out_cubic(p):
    q = 1.0 - p
    return 1.0 - q * q * q


def _ease_table(f, n=1024):
    """イージング f(u) を n 区間の表にして、線形補間で引く関数を返す（端は 0 と 1）。

    Expr の木を字ごと・コマごとに評価すると、トークン100個で1コマ数 ms かかる（実測）。
    """
    tab = [float(f(i / n)) for i in range(n + 1)]

    def g(p):
        if p <= 0.0:
            return 0.0
        if p >= 1.0:
            return 1.0
        x = p * n
        i = int(x)
        return tab[i] + (tab[i + 1] - tab[i]) * (x - i)
    return g


def _glyph_has_ink(fkey, font, ch):
    """その字が実際にインクを持つか（字形で判定する。メモする）。

    Unicode のカテゴリ（Cf・Cc・空白）では決めない: Consolas Bold の U+200C（ZWNJ）のように
    目に見える字形を持つフォントがあり、text_image（textimage._render）はそれを描く。
    カテゴリで決めると、端のコマにある字が遷移の最初のコマで消える。
    """
    if ch in "\n\r":
        return False
    key = (fkey, ch)
    v = _INK_MEMO.get(key)
    if v is None:
        try:
            v = font.getmask(ch, mode="L").getbbox() is not None
        except (OSError, ValueError):
            v = False
        if len(_INK_MEMO) >= _INK_MEMO_MAX:
            _INK_MEMO.clear()
        _INK_MEMO[key] = v
    return v


# --- 色（残る字の色は oklab で補間する）---

def _srgb_to_lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _lin_to_srgb(c):
    c = 12.92 * c if c <= 0.0031308 else 1.055 * (max(c, 0.0) ** (1 / 2.4)) - 0.055
    return min(255.0, max(0.0, c * 255.0))


def _to_oklab(rgb):
    r, g, b = (_srgb_to_lin(v) for v in rgb)
    l_ = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m_ = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s_ = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(v) ** (1 / 3), v) for v in (l_, m_, s_))
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def _from_oklab(lab):
    L, a, b = lab
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l3, m3, s3 = l_ ** 3, m_ ** 3, s_ ** 3
    return (_lin_to_srgb(4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3),
            _lin_to_srgb(-1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3),
            _lin_to_srgb(-0.0041960863 * l3 - 0.7034186147 * m3 + 1.7076147010 * s3))


def _mix_rgba(ca, cb, t):
    """(r, g, b, a)（0..255）を oklab で補間する。端はそのままの値を返す"""
    if t <= 0.0 or ca == cb:
        return tuple(float(v) for v in ca)
    if t >= 1.0:
        return tuple(float(v) for v in cb)
    la, lb = _to_oklab(ca[:3]), _to_oklab(cb[:3])
    rgb = _from_oklab(tuple(_lerp(x, y, t) for x, y in zip(la, lb)))
    return (rgb[0], rgb[1], rgb[2], _lerp(float(ca[3]), float(cb[3]), t))


# --- トークン ---

def _token_spans(line, unit_re):
    """1行の文字列をトークンの (開始, 終了) に分ける。正規表現に当たらない字は1字ずつ"""
    if unit_re is None:
        return [(k, k + 1) for k in range(len(line))]
    out = []
    pos = 0
    for m in unit_re.finditer(line):
        s, e = m.start(), m.end()
        if e <= s or s < pos:
            continue
        out.extend((k, k + 1) for k in range(pos, s))
        out.append((s, e))
        pos = e
    out.extend((k, k + 1) for k in range(pos, len(line)))
    return out


class _Ch:
    """配置済みの1字"""
    __slots__ = ("ch", "x", "y", "X", "Y", "fkey", "size", "rgba", "adv", "asc", "desc",
                 "ink", "order")

    def same_look(self, other):
        return (self.X == other.X and self.Y == other.Y and self.fkey == other.fkey
                and self.rgba == other.rgba)


class _Tok:
    """トークン（字の並び）"""
    __slots__ = ("text", "chars", "line", "index", "x0", "x1", "y", "cx", "asc", "desc",
                 "lh", "ink")


def _layout_chars(layout):
    """_build_layout の結果を字の列と、行ごとの文字列にする"""
    chars = []
    lines = []
    placed = layout["placed"]
    fonts = layout["fonts"]
    styles = layout["styles"]
    k_run = 0
    order = 0
    for li, runs in enumerate(layout["lines"]):
        line_chars = []
        for _run in runs:
            x, yb, text, sidx = placed[k_run]
            k_run += 1
            font = fonts[sidx]
            st = styles[sidx]
            asc, desc = font.getmetrics()
            fkey = (st["font"], st["font_index"], st["size"], st["coords"])
            for k, ch in enumerate(text):
                c = _Ch()
                pen = font.getlength(text[:k + 1]) - font.getlength(ch)
                c.ch = ch
                c.x = x + pen
                c.y = yb
                # Pillow は x を最も近い整数へ（半分は切り上げ）、y を半分は切り下げへ丸めて描く
                c.X = int(math.floor(c.x + 0.5))
                c.Y = int(math.ceil(c.y - 0.5))
                c.fkey = fkey
                c.size = st["size"]
                c.rgba = tuple(st["rgba"])
                c.adv = font.getlength(ch)
                c.asc, c.desc = asc, desc
                c.ink = _glyph_has_ink(fkey, font, ch)
                c.order = order
                order += 1
                line_chars.append(c)
        chars.extend(line_chars)
        lines.append(line_chars)
    return chars, lines


def _tokens_of(lines, unit_re):
    toks = []
    for li, line_chars in enumerate(lines):
        text = "".join(c.ch for c in line_chars)
        for s, e in _token_spans(text, unit_re):
            t = _Tok()
            t.text = text[s:e]
            t.chars = line_chars[s:e]
            t.line = li
            t.index = len(toks)
            t.x0 = t.chars[0].x
            t.x1 = t.chars[-1].x + t.chars[-1].adv
            t.y = t.chars[0].y
            t.cx = (t.x0 + t.x1) / 2.0
            t.asc = max(c.asc for c in t.chars)
            t.desc = max(c.desc for c in t.chars)
            t.lh = t.asc + t.desc
            t.ink = any(c.ink for c in t.chars)
            toks.append(t)
    return toks


# --- 対応のとり方 ---

def _lcs(A, B):
    """自前の O(nm) の DP で LCS を取る。

    L[i][j] = LCS(A[i:], B[j:]) を後ろから埋め、前から貪欲に対を取る。同じ字が並べば
    必ず対にする（LCS は先頭の一致を取って損をしない）ので、左にある一致が残る。
    取らない側を選ぶタイは B を飛ばす（A の左のトークンを残す）。
    difflib.SequenceMatcher は LCS ではない（最長の連続一致を再帰で探す）ので使わない。
    """
    n, m = len(A), len(B)
    L = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        Li, Ln = L[i], L[i + 1]
        ai = A[i]
        for j in range(m - 1, -1, -1):
            if ai == B[j]:
                Li[j] = Ln[j + 1] + 1
            else:
                x, y = Ln[j], Li[j + 1]
                Li[j] = x if x >= y else y
    pairs = []
    i = j = 0
    while i < n and j < m:
        if A[i] == B[j]:
            pairs.append((i, j))
            i += 1
            j += 1
        elif L[i][j + 1] >= L[i + 1][j]:
            j += 1
        else:
            i += 1
    return pairs


def _kind(text):
    """トークンの種類: 'digit' / 'alpha'（ラテン文字など）/ 'cjk'（全角の字。漢字・かな）/
    'space' / 'symbol'。match='auto' はこれが同じ組だけを replace にする（漢字が英字へ
    入れ替わる組は replace にしない）"""
    if text.isspace():
        return "space"
    if all(ch.isdigit() for ch in text):
        return "digit"
    if all(ch.isalnum() or ch == "_" for ch in text):
        if any(unicodedata.east_asian_width(ch) in ("W", "F") for ch in text):
            return "cjk"
        return "alpha"
    return "symbol"


def _match_auto(ta, tb, anchor):
    A = [t.text for t in ta]
    B = [t.text for t in tb]
    keep = _lcs(A, B)
    used_a = {i for i, _j in keep}
    used_b = {j for _i, j in keep}
    # 残りのうち同じ文字列のトークンを、位置の近い順に対にする → move
    cands = []
    for i, a in enumerate(ta):
        if i in used_a:
            continue
        for j, b in enumerate(tb):
            if j in used_b or b.text != a.text:
                continue
            d = math.hypot(b.cx - a.cx, b.y - a.y)
            cands.append((d, i, j))
    cands.sort()
    move = []
    for _d, i, j in cands:
        if i in used_a or j in used_b:
            continue
        move.append((i, j))
        used_a.add(i)
        used_b.add(j)
    # LCS の隙間で位置のそろう残り → replace
    replace = []
    bounds = [(-1, -1)] + sorted(keep) + [(len(ta), len(tb))]
    for (i0, j0), (i1, j1) in zip(bounds, bounds[1:]):
        ga = [i for i in range(i0 + 1, i1) if i not in used_a]
        gb = [j for j in range(j0 + 1, j1) if j not in used_b]
        k = min(len(ga), len(gb))
        if k == 0:
            continue
        leading = i0 == -1
        trailing = i1 == len(ta)
        if leading and trailing:
            from_right = anchor == "right"
        else:
            from_right = leading       # 先頭の隙間は後ろ（次の残る字の隣）から揃える
        if from_right:
            got = list(zip(ga[-k:], gb[-k:]))
        else:
            got = list(zip(ga[:k], gb[:k]))
        # 種類（数字・文字・全角の字・空白・記号）の違う組は入れ替えない
        # （'0' が "'" へ回ると読めない。漢字が英字へ入れ替わるのも読めない）
        got = [(i, j) for i, j in got if _kind(ta[i].text) == _kind(tb[j].text)]
        replace.extend(got)
        for i, j in got:
            used_a.add(i)
            used_b.add(j)
    return keep, move, replace


def _match_edit(ta, tb, prefer):
    """字単位のレーベンシュタインの後戻り。一致は必ず取り、タイは prefer の順で決める"""
    A = [t.text for t in ta]
    B = [t.text for t in tb]
    n, m = len(A), len(B)
    D = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        D[i][0] = i
    for j in range(m + 1):
        D[0][j] = j
    for i in range(1, n + 1):
        Di, Dp = D[i], D[i - 1]
        ai = A[i - 1]
        for j in range(1, m + 1):
            if ai == B[j - 1]:
                Di[j] = Dp[j - 1]
            else:
                Di[j] = 1 + min(Dp[j - 1], Dp[j], Di[j - 1])
    keep, replace = [], []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and A[i - 1] == B[j - 1] and D[i][j] == D[i - 1][j - 1]:
            keep.append((i - 1, j - 1))
            i, j = i - 1, j - 1
            continue
        for op in prefer:
            if op == "replace" and i > 0 and j > 0 and D[i][j] == D[i - 1][j - 1] + 1:
                replace.append((i - 1, j - 1))
                i, j = i - 1, j - 1
                break
            if op == "insert" and j > 0 and D[i][j] == D[i][j - 1] + 1:
                j -= 1
                break
            if op == "delete" and i > 0 and D[i][j] == D[i - 1][j] + 1:
                i -= 1
                break
        else:   # pragma: no cover（DP の性質上、どれかは必ず成り立つ）
            raise RuntimeError("text_transition: 編集距離の後戻りに失敗しました")
    keep.reverse()
    replace.reverse()
    return keep, [], replace


def _match_position(ta, tb, anchor):
    n, m = len(ta), len(tb)
    if anchor == "right":
        idx = [(n - 1 - k, m - 1 - k) for k in range(min(n, m))]
    elif anchor == "left":
        idx = [(k, k) for k in range(min(n, m))]
    else:
        if n <= m:
            off = (m - n) // 2
            idx = [(k, k + off) for k in range(n)]
        else:
            off = (n - m) // 2
            idx = [(k + off, k) for k in range(m)]
    idx.sort()
    keep = [(i, j) for i, j in idx if ta[i].text == tb[j].text]
    replace = [(i, j) for i, j in idx if ta[i].text != tb[j].text]
    return keep, [], replace


def _match_manual(fn, ta, tb, pairs):
    seen_a, seen_b = set(), set()
    keep, move, replace = [], [], []
    for k, p in enumerate(pairs):
        if (not isinstance(p, (list, tuple)) or len(p) != 2
                or any(isinstance(v, bool) or not isinstance(v, int) for v in p)):
            raise ValueError(
                f"{fn}: match の対は (A のトークン番号, B のトークン番号) の整数の組で"
                f"指定してください: match[{k}]={p!r}")
        i, j = p
        if not 0 <= i < len(ta) or not 0 <= j < len(tb):
            raise ValueError(
                f"{fn}: match[{k}]={p!r} のトークン番号が範囲外です"
                f"（A は 0〜{len(ta) - 1}、B は 0〜{len(tb) - 1}。番号は "
                f"obj.figure.tokens で確かめられます）")
        if i in seen_a or j in seen_b:
            raise ValueError(f"{fn}: match のトークン番号が重複しています: {p!r}")
        seen_a.add(i)
        seen_b.add(j)
        (keep if ta[i].text == tb[j].text else replace).append((i, j))
    # 同じ字の対どうしが交差するものは move（入れ替わる組。上下に離れて運ぶ）
    keep.sort()
    crossing = set()
    for x in range(len(keep)):
        for y in range(x + 1, len(keep)):
            (i1, j1), (i2, j2) = keep[x], keep[y]
            if (i1 - i2) * (j1 - j2) < 0:
                crossing.add(keep[x])
                crossing.add(keep[y])
    move = [p for p in keep if p in crossing]
    keep = [p for p in keep if p not in crossing]
    return keep, move, sorted(replace)


# --- 回る向き ---

_NUM_SEP_RE = re.compile(r"[\s,_'’]")


def _parse_number(text):
    t = _NUM_SEP_RE.sub("", text)
    if not t or not re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", t):
        return None
    try:
        return Decimal(t)
    except InvalidOperation:   # pragma: no cover
        return None


def _auto_roll_dir(text_a, text_b):
    """増えるなら上（+1）、減るなら下（-1）。

    両方が数として読めれば値で比べる（符号・小数・桁区切りも読む）。読めなければ
    数字だけを抜き出して比べる（日時の書式なら桁数が同じなので、辞書順＝時刻の順。
    巻き戻すと下）。数字が無ければ文字列の順。
    """
    va, vb = _parse_number(text_a), _parse_number(text_b)
    if va is not None and vb is not None:
        return 1 if vb >= va else -1
    da = "".join(ch for ch in text_a if ch.isdigit())
    db = "".join(ch for ch in text_b if ch.isdigit())
    if da and db:
        if len(da) == len(db):
            return 1 if db >= da else -1
        return 1 if int(db) >= int(da) else -1
    return 1 if text_b >= text_a else -1


# --- 字の置き方（構築時の道筋の計算と描画で共有する。Pillow だけを使う）---

class _Draw:
    """1字を置く指定（端数の位置・大きさ・倍率・角度・不透明度・色・切り抜き）。

    fsize: 字を置く大きさ（px。None なら fkey の大きさ）。大きさの変わる字はこれを補間する。
    bsize: 大きさの変わる字の元にする字形の大きさ（前後の状態の大きい方。None なら
      max(fsize, fkey の大きさ)）。字形はこれを縮めて置き、縁取りは border px のまま
      （_Geom.resolve）。scale: 置いた字形にさらに掛ける倍率（swap・scatter の拡大縮小。
      縁取りも一緒に縮む）。
    """
    __slots__ = ("fkey", "ch", "X", "Y", "fsize", "bsize", "scale", "angle", "alpha", "rgba",
                 "group", "clip", "exact", "pivot")

    def __init__(self, fkey, ch, X, Y, rgba, group, *, fsize=None, bsize=None, scale=1.0,
                 angle=0.0, alpha=1.0, clip=None, exact=False, pivot="pen"):
        self.fkey, self.ch, self.X, self.Y = fkey, ch, X, Y
        self.rgba, self.group = rgba, group
        self.fsize, self.bsize = fsize, bsize
        self.scale, self.angle, self.alpha = scale, angle, alpha
        self.clip, self.exact, self.pivot = clip, exact, pivot


def _big_size(c_size, s_from, s_to):
    """大きさが s_from → s_to と変わるトークンの字（大きさ c_size）の、元にする字形の大きさ"""
    if s_to <= s_from:
        return c_size
    return s_to if c_size == s_from else c_size * s_to / s_from


class _Geom:
    """字の幾何（フォント・インクの箱・字の絵の寸法）。Pillow だけを使う（numpy は使わない）"""

    def __init__(self, plan):
        self.plan = plan
        self._glyph = {}
        self._ink = {}

    def font(self, fkey):
        f = self.plan.fonts.get(fkey)
        if f is None:
            path, index, size, coords = fkey
            f = _ti._load_font(self.plan.fn, self.plan.pil, path, index, size, coords)
        return f

    def resolve(self, d):
        """置く字の (字形のフォントの鍵, 字形に掛ける倍率, 字形の上での縁取りの太さ)。

        大きさの変わる字は、前後の状態の大きい方の字形（bsize）を縮めて置く（字形が
        滑らかに伸び縮みする。そのつどの大きさで描き直すと、ヒンティングで輪郭が毎コマ
        揺れた）。縁取りは字形の上で border ÷ 倍率 の太さにして、縮めた後で border px に
        なるようにする（大きい字形の縁取りごと縮めると細くなり、端のコマで太さが飛ぶ）。
        縁取りの太さが border のままなら None。
        """
        if d.fsize is None or d.fsize == d.fkey[2]:
            return d.fkey, d.scale, None
        bs = d.bsize if d.bsize is not None else max(d.fsize, d.fkey[2])
        if bs != d.fkey[2] and float(bs) != int(bs):
            bs = int(math.ceil(bs))     # フォントは整数の大きさで開く（Pillow 10.1 未満）
        fkey = d.fkey if bs == d.fkey[2] else (d.fkey[0], d.fkey[1], bs, d.fkey[3])
        s = d.fsize / bs
        sw = None
        if self.plan.border and s != 1.0:
            sw = self.plan.border / s
        return fkey, d.scale * s, sw

    def glyph(self, fkey, ch, sw=None):
        """(l, t, r, b, ox, oy, w, h)。l..b は縁取り（太さ sw。None なら border。端数は
        切り上げ）を含むインクの箱（ペンからの相対）、ox, oy, w, h は字の絵
        （_Renderer.sprite）の原点のずれと寸法。インクが無ければ None"""
        n = self.plan.border if sw is None else int(math.ceil(sw - 1e-6))
        key = (fkey, ch, n)
        g = self._glyph.get(key, False)
        if g is not False:
            return g
        g = None
        font = self.font(fkey)
        if _glyph_has_ink(fkey, font, ch):
            l, t, r, btm = font.getbbox(ch, anchor="ls", stroke_width=n)
            self._ink[(fkey, ch)] = font.getbbox(ch, anchor="ls") if n else (l, t, r, btm)
            m = 2
            w, h = int(r - l) + 2 * m, int(btm - t) + 2 * m
            if w > 2 * m and h > 2 * m:
                g = (l, t, r, btm, int(l) - m, int(t) - m, w, h)
        self._glyph[key] = g
        return g

    def digit_band(self, fkey):
        """'0'〜'9' の縁取りを含むインクの (上端, 下端)（ベースラインからの相対）。
        数字の字形が無いフォントなら None"""
        key = ("digits", fkey)
        v = self._glyph.get(key, False)
        if v is not False:
            return v
        font = self.font(fkey)
        v = None
        if all(_glyph_has_ink(fkey, font, ch) for ch in "0123456789"):
            _l, t, _r, b = font.getbbox("0123456789", anchor="ls",
                                        stroke_width=self.plan.border)
            v = (float(t), float(b))
        self._glyph[key] = v
        return v

    def box(self, d, stroke=True):
        """置いた字のインクの外接矩形（変換後。stroke=False なら縁取りを除く）。無ければ None"""
        fkey, s, sw = self.resolve(d)
        g = self.glyph(fkey, d.ch, sw)
        if g is None:
            return None
        if stroke:
            l, t, r, b = g[:4]
        else:
            l, t, r, b = self._ink[(fkey, d.ch)]
        if s == 1.0 and d.angle == 0.0:
            return (d.X + l, d.Y + t, d.X + r, d.Y + b)
        ox, oy, w, h = g[4:]
        px, py = (0.0, 0.0) if d.pivot == "pen" else (ox + w / 2.0, oy + h / 2.0)
        th = math.radians(d.angle)
        c, sn = math.cos(th) * s, math.sin(th) * s
        xs, ys = [], []
        for u, v in ((l, t), (r, t), (l, b), (r, b)):
            uu, vv = u - px, v - py
            xs.append(d.X + px + c * uu - sn * vv)
            ys.append(d.Y + py + sn * uu + c * vv)
        return (min(xs), min(ys), max(xs), max(ys))


def _progress(it, u):
    if u <= it.t0:
        return 0.0
    if u >= it.t1:
        return 1.0
    return (u - it.t0) / (it.t1 - it.t0)


def _eased(plan, it, u):
    pr = _progress(it, u)
    return pr, (0.0 if pr <= 0.0 else (1.0 if pr >= 1.0 else plan.ease(pr)))


def _exact_draws(t, g):
    return [_Draw(c.fkey, c.ch, c.X, c.Y, c.rgba, g, exact=True) for c in t.chars if c.ink]


def _lift_carry(plan, pr):
    """入れ替わる組の (離れる量 0..1, 運ぶ進み 0..1)。離れる量は smoothstep で先に 1 になり、
    運ぶ進みは離れかけてから easing どおりに進む（角が丸い U 字の道筋になる）"""
    v = min(_smooth(0.0, _LIFT, pr), 1.0 - _smooth(1.0 - _LIFT, 1.0, pr))
    q = (pr - _CARRY) / (1.0 - 2.0 * _CARRY)
    h = 0.0 if q <= 0.0 else (1.0 if q >= 1.0 else plan.ease(q))
    return v, h


def _item_draws(plan, geom, it, ta, tb, u):
    """動くもの1つの、遷移の中の割合 u での字の置き方"""
    pr, e = _eased(plan, it, u)
    g = it.group
    if it.kind in ("keep", "move"):
        a, b = ta[it.a], tb[it.b]
        if pr <= 0.0:
            return _exact_draws(a, g)
        if pr >= 1.0:
            return _exact_draws(b, g)
        out = []
        a0, b0 = a.chars[0], b.chars[0]
        if it.lev is not None:
            # 入れ替わる組: 行から it.lev（1字目のベースラインの高さ）まで離れ、運び、置く
            v, e = _lift_carry(plan, pr)
            lift = (it.lev - _lerp(a0.Y, b0.Y, e)) * v
        else:
            dy = it.arc * math.sin(math.pi * e)
        for ca, cb in zip(a.chars, b.chars):
            if not (ca.ink or cb.ink):
                continue
            if it.lev is not None:
                X = _lerp(ca.X, cb.X, e)
                Y = _lerp(ca.Y, cb.Y, e) + lift
            else:
                X = _lerp(ca.X, cb.X, e)
                Y = _lerp(ca.Y, cb.Y, e) + dy
            rgba = _mix_rgba(ca.rgba, cb.rgba, e)
            if ca.fkey == cb.fkey:
                out.append(_Draw(ca.fkey, ca.ch, X, Y, rgba, g))
                continue
            size = _lerp(ca.size, cb.size, e)
            big = max(ca.size, cb.size)
            if ca.fkey[:2] == cb.fkey[:2] and ca.fkey[3] == cb.fkey[3]:
                out.append(_Draw(ca.fkey, ca.ch, X, Y, rgba, g, fsize=size, bsize=big))
            else:
                out.append(_Draw(ca.fkey, ca.ch, X, Y, rgba, g, fsize=size, bsize=big,
                                 alpha=1.0 - e))
                out.append(_Draw(cb.fkey, cb.ch, X, Y, rgba, g, fsize=size, bsize=big,
                                 alpha=e))
        return out
    if it.kind == "replace":
        a, b = ta[it.a], tb[it.b]
        if pr <= 0.0:
            return _exact_draws(a, g)
        if pr >= 1.0:
            return _exact_draws(b, g)
        return _replace_draws(it, a, b, e)
    if it.kind == "leave":
        a = ta[it.a]
        if pr >= 1.0:
            return []
        if pr <= 0.0:
            return _exact_draws(a, g)
        return _leave_draws(plan, geom, it, a, pr, e)
    # enter
    b = tb[it.b]
    if pr <= 0.0:
        return []
    if pr >= 1.0:
        return _exact_draws(b, g)
    return _enter_draws(it, b, pr, e)


def _replace_draws(it, a, b, e):
    g = it.group
    out = []
    la, lb = a.chars[0].X, b.chars[0].X
    L = _lerp(la, lb, e)
    base = _lerp(a.chars[0].Y, b.chars[0].Y, e)
    sa, sb = a.chars[0].size, b.chars[0].size
    size = _lerp(sa, sb, e)
    s_old, s_new = size / sa, size / sb
    if it.mode == "roll":
        clip = _lerp4(it.win[0], it.win[1], e)
        for c in a.chars:
            if c.ink:
                out.append(_Draw(c.fkey, c.ch, L + (c.X - la) * s_old,
                                 base - it.dir * e * it.hc, c.rgba, g, fsize=c.size * s_old,
                                 bsize=_big_size(c.size, sa, sb), clip=clip))
        for c in b.chars:
            if c.ink:
                out.append(_Draw(c.fkey, c.ch, L + (c.X - lb) * s_new,
                                 base + it.dir * (1.0 - e) * it.hc, c.rgba, g,
                                 fsize=c.size * s_new, bsize=_big_size(c.size, sb, sa),
                                 clip=clip))
    elif it.mode == "fade":
        for c in a.chars:
            if c.ink:
                out.append(_Draw(c.fkey, c.ch, L + (c.X - la) * s_old, base, c.rgba, g,
                                 fsize=c.size * s_old, bsize=_big_size(c.size, sa, sb),
                                 alpha=1.0 - e))
        for c in b.chars:
            if c.ink:
                out.append(_Draw(c.fkey, c.ch, L + (c.X - lb) * s_new, base, c.rgba, g,
                                 fsize=c.size * s_new, bsize=_big_size(c.size, sb, sa),
                                 alpha=e))
    else:   # swap: 古い字が縮んで消え、新しい字が膨らんで現れる（字の中心が軸）
        if e < 0.5:
            q = e * 2.0
            sc = _lerp(1.0, _SWAP_MIN_SCALE, q)
            for c in a.chars:
                if c.ink:
                    out.append(_Draw(c.fkey, c.ch, L + (c.X - la) * s_old, base, c.rgba, g,
                                     fsize=c.size * s_old, bsize=_big_size(c.size, sa, sb),
                                     scale=sc, alpha=1.0 - q,
                                     pivot="center"))
        else:
            q = e * 2.0 - 1.0
            sc = _lerp(_SWAP_MIN_SCALE, 1.0, q)
            for c in b.chars:
                if c.ink:
                    out.append(_Draw(c.fkey, c.ch, L + (c.X - lb) * s_new, base, c.rgba, g,
                                     fsize=c.size * s_new, bsize=_big_size(c.size, sb, sa),
                                     scale=sc, alpha=q,
                                     pivot="center"))
    return out


def _leave_draws(plan, geom, it, a, pr, e):
    g = it.group
    out = []
    if it.mode == "fade":
        for c in a.chars:
            if c.ink:
                out.append(_Draw(c.fkey, c.ch, c.X, c.Y, c.rgba, g, alpha=1.0 - e))
    elif it.mode == "fall":
        # 重力で加速しながら落ち、少し傾き、終わりに向けて消える
        dy = pr * pr * it.fall
        sgn = 1.0 if it.seed % 2 == 0 else -1.0
        ang = sgn * _FALL_TILT * pr * pr
        alpha = 1.0 - _smooth(0.25, 1.0, pr)
        for c in a.chars:
            if c.ink:
                out.append(_Draw(c.fkey, c.ch, c.X, c.Y + dy, c.rgba, g, angle=ang,
                                 alpha=alpha, pivot="center"))
    elif it.mode == "scatter":
        rng = random.Random(it.seed)
        th = math.radians(rng.uniform(*_SCATTER_FAN))
        spin = rng.uniform(-_SCATTER_SPIN, _SCATTER_SPIN)
        dist = it.scatter * _ease_out_cubic(pr)
        dx, dy = math.cos(th) * dist, math.sin(th) * dist
        alpha = (1.0 - pr) ** 2
        for c in a.chars:
            if c.ink:
                out.append(_Draw(c.fkey, c.ch, c.X, c.Y, c.rgba, g, angle=spin * pr,
                                 scale=1.0 + _SCATTER_GROW * pr, alpha=alpha,
                                 pivot="center"))
        # キャンバスの中に収める（拡大・回転した後の縁取りを含む外接で。字の絵の余白と
        # 補間のにじみの分 2px を空ける）
        boxes = [bx for bx in (geom.box(d) for d in out) if bx is not None]
        if boxes:
            m = 2.0
            for axis, size in ((0, plan.W), (1, plan.H)):
                lo = m - min(bx[axis] for bx in boxes)
                hi = (size - m) - max(bx[axis + 2] for bx in boxes)
                v = dx if axis == 0 else dy
                v = (lo + hi) / 2.0 if lo > hi else min(max(v, lo), hi)
                if axis == 0:
                    dx = v
                else:
                    dy = v
        for d in out:
            d.X += dx
            d.Y += dy
    else:   # roll（odometer の桁が消える）: 自分の窓の中を流れて出ていく
        clip = it.win[0]
        for c in a.chars:
            if c.ink:
                out.append(_Draw(c.fkey, c.ch, c.X, c.Y - it.dir * e * it.hc, c.rgba, g,
                                 clip=clip))
    return out


def _enter_draws(it, b, pr, e):
    g = it.group
    out = []
    if it.mode == "fade":
        for c in b.chars:
            if c.ink:
                out.append(_Draw(c.fkey, c.ch, c.X, c.Y, c.rgba, g, alpha=e))
    elif it.mode in ("drop", "rise"):
        sgn = -1.0 if it.mode == "drop" else 1.0
        dy = sgn * it.drop * (1.0 - _ease_out_cubic(pr))
        alpha = min(1.0, pr * 2.5)
        for c in b.chars:
            if c.ink:
                out.append(_Draw(c.fkey, c.ch, c.X, c.Y + dy, c.rgba, g, alpha=alpha))
    else:   # roll（odometer の桁が増える）: 自分の窓の中へ流れて入る
        clip = it.win[1]
        for c in b.chars:
            if c.ink:
                out.append(_Draw(c.fkey, c.ch, c.X, c.Y + it.dir * (1.0 - e) * it.hc, c.rgba,
                                 g, clip=clip))
    return out


# --- 計画（配置・対応・時刻・道筋）---

class _Item:
    """1つの遷移の中で動くもの（トークン1つか、対1つ）"""
    __slots__ = ("kind", "a", "b", "mode", "t0", "t1", "arc", "dir", "seed", "group",
                 "fall", "drop", "scatter", "lev", "ext", "win", "hc")


def _fmt_args(fn, fmt, anchor):
    """fmt（text_image の書式）を検証して、text_image と同じ既定値で埋める"""
    if "max_width" in fmt:
        raise ValueError(
            f"{fn}: max_width（折り返し）は受けません。状態ごとに折り返しが変わると字が"
            f"大きく飛ぶので、1〜3行の語・数字・コード向けです。長い字幕は text_image を"
            f"状態ごとに作って切り替えてください（改行は文字列の中に \\n で書けます）")
    if "canvas" in fmt:
        raise ValueError(
            f"{fn}: canvas は全状態の外接から自動で決まります（obj.figure.canvas で読めます）")
    unknown = sorted(set(fmt) - set(_FMT_DEFAULTS))
    if unknown:
        raise TypeError(
            f"{fn}: 知らない引数 {unknown} があります"
            f"（文字の書式は {', '.join(_FMT_DEFAULTS)}）"
            f"{_suggest_hint(unknown[0], list(_FMT_DEFAULTS))}")
    for name in ("align", "padding"):
        # 渡したものは text_image_kwargs に入れない（fmt と合わせて text_image へ渡せる）ので、
        # None を明示されると「省略」と「text_image の None」の意味が食い違う
        if name in fmt and fmt[name] is None:
            raise ValueError(
                f"{fn}: {name}=None は渡さず省略してください（省略すると "
                + ("anchor と同じ揃え" if name == "align" else
                   "text_image の自然な寸法と同じ余白") + "になります）")
    args = dict(_FMT_DEFAULTS)
    args.update(fmt)
    if args["align"] is None:
        args["align"] = anchor
    elif args["align"] != anchor:
        raise ValueError(
            f"{fn}: align={args['align']!r} と anchor={anchor!r} が食い違っています。"
            f"共通キャンバスの中の横の揃えは anchor で決めます（align は省略してください）")
    return args


class _Plan:
    """構築時に決まるもの（配置・トークン・対応表・時刻・道筋）。draw はこれだけを読む"""


def _token_window(geom, t, border):
    """回る字の窓（キャンバスの座標）: 横は字の送り幅、縦は数字の帯（'0'〜'9' の縁取りを
    含むインクの上端〜下端に size × _ROLL_BAND_PAD を足したもの）。これと字自身の縁取りを
    含むインクの外接の和を、_ROLL_PAD だけ広げたもの。

    縦を字のマス（ascent+descent）にすると、源真ゴシックのように ascent・descent の大きい
    フォントでは窓が数字の約 2 倍の高さになり、回る数字が行の上下へ大きく流れ出て
    （古い字と新しい字が上下 2 段に見えた。見本の場面 の日時）途中のコマが騒がしかった。
    """
    c0, c1 = t.chars[0], t.chars[-1]
    x0, x1 = float(c0.X), c1.X + c1.adv
    y0, y1 = math.inf, -math.inf
    for c in t.chars:
        band = geom.digit_band(c.fkey)
        if band is not None:
            pad = _ROLL_BAND_PAD * c.size
            y0, y1 = min(y0, c.Y + band[0] - pad), max(y1, c.Y + band[1] + pad)
    if y0 > y1:
        y0, y1 = c0.Y - t.asc - border, c0.Y + t.desc + border
    for c in t.chars:
        g = geom.glyph(c.fkey, c.ch) if c.ink else None
        if g is not None:
            x0, y0 = min(x0, c.X + g[0]), min(y0, c.Y + g[1])
            x1, y1 = max(x1, c.X + g[2]), max(y1, c.Y + g[3])
    return (x0 - _ROLL_PAD, y0 - _ROLL_PAD, x1 + _ROLL_PAD, y1 + _ROLL_PAD)


def _roll_travel(geom, a, b, win_a, win_b):
    """回る字の縦の移動量: 古い字が e=1 で窓の外へ出きり、新しい字が e=0 で窓の外にいる量"""
    ya, yb = a.chars[0].Y, b.chars[0].Y
    sa, sb = a.chars[0].size, b.chars[0].size

    def ext(t, ratio):
        top, bot = math.inf, -math.inf
        for c in t.chars:
            if not c.ink:
                continue
            for sz in (c.size, c.size * ratio):
                bx = geom.box(_Draw(c.fkey, c.ch, 0, 0, c.rgba, "hi", fsize=sz))
                if bx is not None:
                    top, bot = min(top, bx[1]), max(bot, bx[3])
        return (top, bot) if top < bot else (0.0, 0.0)

    ta_, ba_ = ext(a, sb / sa)
    tb_, bb_ = ext(b, sa / sb)
    need = max(ba_ - (win_b[1] - yb), (win_a[3] - ya) - tb_,       # 上へ回るとき
               (win_b[3] - yb) - ta_, bb_ - (win_a[1] - ya))       # 下へ回るとき
    return max(need, 1.0) + 1.0


def _route_candidates(geom, it, a, b, ta, tb, swing):
    """入れ替わる組の離れる高さ（1字目のベースラインの y）の候補（好ましい順）と、
    字のインクの縦の範囲（ペンからの相対）。

    最初の候補は、行の字（両方の状態の、縁取りを含むインク）を _ROUTE_GAP_RATIO の隙間を
    あけてちょうど避ける高さ。残りはその倍（_ROUTE_DEPTHS）のうち、離れる量が
    swing × 行の字の高さ（ascent + descent の最大）以下のもの（最初の候補より浅くはしない）。
    上限がちょうど候補の間に来るときは、上限そのものも候補にする。
    """
    ya, yb = a.chars[0].Y, b.chars[0].Y
    lower = it.arc > 0
    top, bot = math.inf, -math.inf
    row_lh = 0.0
    for ts, line in ((ta, a.line), (tb, b.line)):
        for t in ts:
            if t.line != line:
                continue
            row_lh = max(row_lh, t.lh)
            for c in t.chars:
                gl = geom.glyph(c.fkey, c.ch) if c.ink else None
                if gl is not None:
                    top, bot = min(top, c.Y + gl[1]), max(bot, c.Y + gl[3])
    mt, mb = math.inf, -math.inf
    for t in (a, b):
        c0 = t.chars[0]
        for c in t.chars:
            gl = geom.glyph(c.fkey, c.ch) if c.ink else None
            if gl is not None:
                mt, mb = min(mt, c.Y - c0.Y + gl[1]), max(mb, c.Y - c0.Y + gl[3])
    if top > bot or mt > mb:
        return [None], (0.0, 0.0)
    lh = max(a.lh, b.lh)
    gap = max(2.0, _ROUTE_GAP_RATIO * lh)
    if lower:
        dref = max(bot + gap - mt - max(ya, yb), 0.5 * lh)
    else:
        dref = max(min(ya, yb) - (top - gap - mb), 0.5 * lh)
    cap = max(dref, swing * row_lh)
    depths = [f * dref for f in _ROUTE_DEPTHS if f * dref <= cap + 1e-9]
    if cap < _ROUTE_DEPTHS[-1] * dref and cap > depths[-1] + 1.0:
        depths.append(cap)
    out = [max(ya, yb) + d if lower else min(ya, yb) - d for d in depths]
    return out, (mt, mb)


def _overlap_area(boxes, others):
    cost = 0.0
    for m in boxes:
        for o in others:
            ox = min(m[2], o[2]) - max(m[0], o[0])
            if ox <= 0.0:
                continue
            oy = min(m[3], o[3]) - max(m[1], o[1])
            if oy > 0.0:
                cost += ox * oy
    return cost


def _route_moves(plan, geom, k, den, hint=None):
    """入れ替わる組（move）の離れる高さを決める。

    候補（_route_candidates。浅い順）を順に試し、動く間の字（縁取りを含むインクの箱）が
    同じ遷移のほかの字（止まっている字・滑る字・消える字・現れる字・ほかの move）と
    重ならない最初のものを選ぶ。端の位置から行の高さの _ROUTE_REST 倍までは数えない
    （もとから隣り合う字の縁取りは重なりうる）。全部が重なるなら重なりの面積が最小のもの。
    字の箱は遷移のコマの2倍の細かさで標本化する。
    hint: 前に作った計画の結果 {(k, a, b): 離れる高さ − 1字目のベースライン}（余白だけを
    変えて作り直すときに使う。縦の平行移動に対して結果は同じなので、計算し直さない）。
    """
    ta, tb = plan.toks[k], plan.toks[k + 1]
    items = plan.items[k]
    movers = [it for it in items if it.kind == "move" and it.arc != 0.0]
    if not movers:
        return
    if hint is not None:
        for it in movers:
            _cs, it.ext = _route_candidates(geom, it, ta[it.a], tb[it.b], ta, tb, plan.swing)
            h = hint.get((k, it.a, it.b))
            it.lev = None if h is None else ta[it.a].chars[0].Y + h
        return
    statics = []
    for i, _j in plan.statics[k]:
        for d in _exact_draws(ta[i], "hi"):
            bx = geom.box(d, stroke=False)
            if bx is not None:
                statics.append(bx)
    cands = {}
    for it in movers:
        cs, it.ext = _route_candidates(geom, it, ta[it.a], tb[it.b], ta, tb, plan.swing)
        cands[id(it)] = cs
        it.lev = cs[0]
    fixed = [it for it in items if not (it.kind == "move" and it.arc != 0.0)]
    memo = {}

    def boxes_of(it, u):
        return [bx for bx in (geom.box(d, stroke=False)
                              for d in _item_draws(plan, geom, it, ta, tb, u))
                if bx is not None]

    def obstacles(u):
        v = memo.get(u)
        if v is None:
            v = list(statics)
            for o in fixed:
                v.extend(boxes_of(o, u))
            memo[u] = v
        return v

    def mover_boxes(o, u):
        key = (id(o), o.lev, u)
        v = memo.get(key)
        if v is None:
            v = memo[key] = boxes_of(o, u)
        return v

    for it in movers:
        if cands[id(it)] == [None]:
            continue
        a0, b0 = ta[it.a].chars[0], tb[it.b].chars[0]
        rest = _ROUTE_REST * max(ta[it.a].lh, tb[it.b].lh)
        n = max(24, min(160, int(math.ceil(2 * den * (it.t1 - it.t0)))))
        us = [it.t0 + (it.t1 - it.t0) * (q + 0.5) / n for q in range(n)]
        others_m = [o for o in movers if o is not it]
        best = None
        for lev in cands[id(it)]:
            it.lev = lev
            cost = 0.0
            for u in us:
                draws = _item_draws(plan, geom, it, ta, tb, u)
                if not draws:
                    continue
                d = draws[0]
                if min(math.hypot(d.X - a0.X, d.Y - a0.Y),
                       math.hypot(d.X - b0.X, d.Y - b0.Y)) < rest:
                    continue
                # 字の箱を _ROUTE_PAD px 広げて比べる（標本の間で少し近づいても触れない）。
                # ただし縦にだけ動く間（離れる・戻る。横の運びの前後）は横へ広げない:
                # 隣の字とはもとから箱が接していて、広げると「隣を縦にすり抜ける」だけで
                # 重なりに数え、速く抜ける深い候補ほど得をした（'[1, 2, 10]' の '2' が
                # 字の 2.5 倍も下へ振れ、画面の下で切れた）
                _v, h = _lift_carry(plan, _progress(it, u))
                padx = _ROUTE_PAD if 0.0 < h < 1.0 else 0.0
                mine = [(bx[0] - padx, bx[1] - _ROUTE_PAD, bx[2] + padx,
                         bx[3] + _ROUTE_PAD)
                        for bx in (geom.box(d, stroke=False) for d in draws) if bx is not None]
                cost += _overlap_area(mine, obstacles(u))
                for o in others_m:
                    cost += _overlap_area(mine, mover_boxes(o, u))
                if best is not None and cost >= best[0]:
                    break
            if best is None or cost < best[0]:
                best = (cost, lev)
            if cost == 0.0:
                break
        it.lev = best[1]


def _build_plan(fn, states, *, duration, hold, unit, match, prefer, move, replace, leave,
                enter, stagger, ease_key, ease_f, anchor, roll_dir, motion_blur, fmt, swing,
                timing=None, modes=None, move_margin=None, route_hint=None):
    pil = _ti._import_pil(fn)
    args = fmt
    size = args["size"]
    _require_number(fn, "size", size, 1, 2000)
    font_index = args["font_index"]
    if isinstance(font_index, bool) or not isinstance(font_index, int) or font_index < 0:
        raise ValueError(f"{fn}: font_index は 0 以上の整数で指定してください: {font_index!r}")
    weight = args["weight"]
    if weight is not None and not isinstance(weight, str):
        _require_number(fn, "weight", weight, 1, 2000)
    if not isinstance(args["markup"], bool):
        raise TypeError(f"{fn}: markup は True / False で指定してください: {args['markup']!r}")
    _require_number(fn, "line_spacing", args["line_spacing"], 0.1, 20)
    _require_number(fn, "border", args["border"], 0, 500)
    border = int(args["border"])
    shadow = _ti._pair(fn, "shadow", args["shadow"])
    _require_number(fn, "shadow_blur", args["shadow_blur"], 0, 500)
    shadow_blur = _ti._norm_num(args["shadow_blur"])
    padding = args["padding"]
    if padding is not None:
        padding = _ti._pair(fn, "padding", padding, 0)
    styles = args["styles"]
    if styles is not None and not isinstance(styles, dict):
        raise TypeError(
            f"{fn}: styles は 名前 → 書式 の dict で指定してください"
            f"（例: {{'r': {{'color': 'red'}}}}）: {styles!r}")
    styles = dict(styles or {})
    for name, st in styles.items():
        if not isinstance(name, str) or not name:
            raise TypeError(f"{fn}: styles のキーは文字列で指定してください: {name!r}")
        styles[name] = _ti._check_style(fn, f"styles['{name}']", st)
    border_rgba = _ti._rgba(fn, "border_color", args["border_color"], pil)
    shadow_rgba = _ti._rgba(fn, "shadow_color", args["shadow_color"], pil)
    _ti._rgba(fn, "color", args["color"], pil)
    base_style = {"color": args["color"], "size": _ti._norm_num(size),
                  "font": _resolve_font(args["font"]), "font_index": font_index,
                  "weight": _ti._norm_num(weight)}

    def layout_of(spans, canvas):
        return _ti._build_layout(
            fn, pil, spans, base_style, line_spacing=args["line_spacing"], align=anchor,
            max_width=None, border=border, shadow=shadow, shadow_blur=shadow_blur,
            padding=padding, canvas=canvas, missing="error")

    spans_all = []
    natural = []
    for s, content in enumerate(states):
        spans = _ti._normalize_spans(fn, content, args["markup"], styles)
        lay = layout_of(spans, None)
        if len(lay["lines"]) > _MAX_LINES:
            raise ValueError(
                f"{fn}: states[{s}] が {len(lay['lines'])} 行あります（{_MAX_LINES} 行まで）。"
                f"text_transition は語・数字・コード向けです。長い字幕は text_image を状態ごとに"
                f"作って切り替えてください")
        spans_all.append(spans)
        natural.append(lay)
    W = max(lay["width"] for lay in natural)
    H0 = max(lay["height"] for lay in natural)
    # 共通キャンバスの余白。省略時は text_image の自然な寸法と同じ余白（縁取り・影 + size の
    # 15%）にする。canvas 指定時の既定（縁取り・影だけ）だと、左右に張り出す字（'W'・'A'・
    # 斜体）の端が 1px 切れる（実測: メイリオ 72px で警告）。
    if padding is None:
        padding = tuple(natural[0]["padding"])

    unit_re = _UNIT_RES[unit] if unit in _UNIT_RES else re.compile(unit)

    def tokens_for(canvas):
        out_l, out_c, out_t = [], [], []
        for s, spans in enumerate(spans_all):
            lay = layout_of(spans, canvas)
            chars, lines = _layout_chars(lay)
            toks = _tokens_of(lines, unit_re)
            if len(toks) > _MAX_TOKENS:
                raise ValueError(
                    f"{fn}: states[{s}] のトークンが {len(toks)} 個あります"
                    f"（{_MAX_TOKENS} 個まで）。文字列を分けるか unit を粗くしてください")
            out_l.append(lay)
            out_c.append(chars)
            out_t.append(toks)
        return out_l, out_c, out_t

    layouts, chars, toks = tokens_for((W, H0))

    n_tr = len(states) - 1
    if isinstance(match, str):
        matches = [match] * n_tr
    elif n_tr == 1 and all(isinstance(p, (list, tuple)) and len(p) == 2
                           and not isinstance(p[0], (list, tuple)) for p in match):
        matches = [list(match)]
    else:
        if len(match) != n_tr:
            raise ValueError(
                f"{fn}: match を遷移ごとに渡すときは遷移の数（{n_tr}）だけ並べてください: "
                f"{match!r}")
        matches = list(match)
    pairs = []
    for k in range(n_tr):
        ta, tb = toks[k], toks[k + 1]
        mk = matches[k]
        if isinstance(mk, str):
            _require_choice(fn, f"match[{k}]" if not isinstance(match, str) else "match", mk, _MATCHES)
            if mk == "auto":
                kp, mv, rp = _match_auto(ta, tb, anchor)
            elif mk == "edit":
                kp, mv, rp = _match_edit(ta, tb, prefer)
            else:
                kp, mv, rp = _match_position(ta, tb, anchor)
        elif isinstance(mk, (list, tuple)):
            kp, mv, rp = _match_manual(fn, ta, tb, mk)
        else:
            raise TypeError(
                f"{fn}: match は 'auto' / 'edit' / 'position' か、(i, j) の対のリストで"
                f"指定してください: {mk!r}")
        ua = {i for i, _j in kp + mv + rp}
        ub = {j for _i, j in kp + mv + rp}
        pairs.append({
            "keep": sorted(kp), "move": sorted(mv), "replace": sorted(rp),
            "leave": [i for i in range(len(ta)) if i not in ua],
            "enter": [j for j in range(len(tb)) if j not in ub],
        })

    # 縦の動き（入れ替わる組・弧・落ちる・現れる）の余白。入れ替わる組の離れる高さは
    # 道を決めた後でないと分からないので、見積もりで作り、合わなければ move_margin
    # （組に要る余白）を渡して作り直す（_core の compute）
    lh_max = max(t.lh for ts in toks for t in ts) if any(toks) else size
    need = 0.0
    moving_keep = False
    has_move = any(pr["move"] for pr in pairs)
    for k, pr in enumerate(pairs):
        ta, tb = toks[k], toks[k + 1]
        if any(abs(tb[j].chars[0].X - ta[i].chars[0].X) >= 1 for i, j in pr["keep"]):
            moving_keep = True
            if move == "arc":
                need = max(need, _ARC_RATIO * lh_max)
        lv = [i for i in pr["leave"] if ta[i].ink]
        en = [j for j in pr["enter"] if tb[j].ink]
        md_leave = (modes or {}).get("leave", leave)
        md_enter = (modes or {}).get("enter", enter)
        if lv and md_leave == "fall":
            need = max(need, _FALL_RATIO * lh_max)
        if lv and md_leave == "scatter":
            need = max(need, _SCATTER_RATIO * lh_max)
        if en and md_enter in ("drop", "rise"):
            need = max(need, _DROP_RATIO * lh_max)
    margin = int(math.ceil(need)) + 2 if need > 0 else 0
    margin_other = margin
    if has_move:
        margin = max(margin, int(move_margin) if move_margin is not None
                     else int(math.ceil(_ROUTE_EST * lh_max)) + 2 * border + 2)
    H = H0 + 2 * margin
    if W > _ti._TEXT_IMAGE_MAX_PX or H > _ti._TEXT_IMAGE_MAX_PX:
        raise ValueError(
            f"{fn}: キャンバスが大きすぎます（{W}x{H}px, 上限 {_ti._TEXT_IMAGE_MAX_PX}px）。"
            f"size を下げるか文字列を分けてください")
    if margin:
        layouts, chars, toks = tokens_for((W, H))

    plan = _Plan()
    plan.fn = fn
    plan.pil = pil
    plan.W, plan.H = W, H
    plan.padding = tuple(padding)
    plan.margin = margin
    plan.margin_other = margin_other
    plan.layouts = layouts
    plan.chars = chars
    plan.toks = toks
    plan.pairs = pairs
    plan.border = border
    plan.border_rgba = border_rgba
    plan.shadow = shadow
    plan.shadow_rgba = shadow_rgba
    plan.shadow_blur = shadow_blur
    plan.ease = _ease_table(ease_f)
    plan.motion_blur = motion_blur
    plan.anchor = anchor
    plan.swing = float(swing)
    # フォント（字の絵を描くとき開く。キーは (path, index, size, coords)）と、鍵に入れる
    # FontRef（内容指紋・書体番号・軸の値。framekit.build が Pillow の版も鍵に入れる）
    plan.fonts = {}
    refs = {}
    for lay in layouts:
        for st, font in zip(lay["styles"], lay["fonts"]):
            plan.fonts[(st["font"], st["font_index"], st["size"], st["coords"])] = font
            refs[(st["font"], st["font_index"], st["coords"])] = None
    base_coords = _ti._variation_coords(fn, pil, base_style["font"], font_index,
                                        base_style["weight"])
    refs[(base_style["font"], font_index, base_coords)] = None
    plan.font_refs = [fk.FontRef(p, i, c, _file_fingerprint(p)) for p, i, c in refs]
    geom = _Geom(plan)

    # --- 時刻: 区切りをフレームへ丸める（stillseq と同じ規則。誤差は積もらない）---
    fps = _resolve_fps(fn, None)
    fps_frac = _fps_fraction(fps)
    segs = []
    for s in range(len(states)):
        segs.append(("hold", s, hold[s]))
        if s < n_tr:
            segs.append(("trans", s, duration[s]))
    acc = Fraction(0)
    bounds = [0]
    for _seg_kind, _idx, sec in segs:
        acc += _sec_fraction(sec)
        bounds.append(_round_frame(acc, fps_frac))
    n_frames = bounds[-1]
    last_hold_frames = bounds[-1] - bounds[-2]
    table = []          # コマごとに ("hold", s) か ("trans", k, u)
    trans_frames = {}   # k -> (最初のコマ, コマ数, 分母)
    for q, (kind, idx, sec) in enumerate(segs):
        b0, b1 = bounds[q], bounds[q + 1]
        if kind == "hold":
            table.extend(("hold", idx) for _f in range(b0, b1))
            continue
        N = b1 - b0
        if N < 2:
            raise ValueError(
                f"{fn}: duration[{idx}]={sec:g} 秒は {N} コマにしかなりません"
                f"（fps={float(fps_frac):g}）。{2 / float(fps_frac):.3f} 秒以上にしてください")
        # 最後の遷移で最後の状態の hold が 0 コマなら、最後のコマを最後の状態にする
        den = N - 1 if (idx == n_tr - 1 and last_hold_frames == 0) else N
        trans_frames[idx] = (b0, N, den)
        table.extend(("trans", idx, (f - b0) / den) for f in range(b0, b1))
    starts = [0.0]
    for k in range(n_tr):
        b0, N, den = trans_frames[k]
        starts.append(float(Fraction(b0 + den, 1) / fps_frac))
    plan.n_frames = n_frames
    plan.table = table
    plan.fps_frac = fps_frac
    plan.starts = starts
    plan.state_frames = [int(round(s * float(fps_frac))) for s in starts]

    # --- 動くものと、その時刻 ---
    plan.items = []
    plan.statics = []
    used = {"prefer": any(m == "edit" for m in matches), "move": moving_keep,
            "replace": False, "leave": False, "enter": False, "roll_dir": False,
            "stagger": False, "swing": False}
    schedule = []
    for k in range(n_tr):
        D = duration[k]
        ta, tb = toks[k], toks[k + 1]
        pr = pairs[k]
        rdir = roll_dir
        if rdir == "auto":
            rdir = "up" if _auto_roll_dir(layouts[k]["plain"], layouts[k + 1]["plain"]) > 0 \
                else "down"
        dsign = 1 if rdir == "up" else -1
        items = []
        statics = []
        for kind in ("keep", "move"):
            for i, j in pr[kind]:
                a, b = ta[i], tb[j]
                if kind == "keep" and all(ca.same_look(cb) for ca, cb in zip(a.chars, b.chars)):
                    statics.append((i, j))
                    continue
                it = _Item()
                it.kind, it.a, it.b = kind, i, j
                it.lev = it.win = it.ext = None
                it.hc = 0.0
                dx = b.chars[0].X - a.chars[0].X
                if kind == "move":
                    # 入れ替わる組: 左へ行く字が上、右へ行く字が下へ離れる（_route_moves）
                    it.mode = "arc"
                    it.arc = 0.0 if abs(dx) < 1 else (-1.0 if dx < 0 else 1.0)
                    if it.arc:
                        used["swing"] = True
                elif move == "arc" and abs(dx) >= 1:
                    it.mode = "arc"
                    hh = min(_ARC_RATIO * max(a.lh, b.lh), _ARC_KEEP_RATIO * abs(dx))
                    it.arc = -hh if dx < 0 else hh
                else:
                    it.mode = "slide"
                    it.arc = 0.0
                items.append(it)
        for i, j in pr["replace"]:
            it = _Item()
            it.kind, it.a, it.b = "replace", i, j
            it.lev = it.win = it.ext = None
            it.hc = 0.0
            if modes and "replace_fn" in modes:
                it.mode = modes["replace_fn"](ta[i].text, tb[j].text, replace)
            elif replace == "auto":
                # 数字どうしだけ回す（ほかは薄れて入れ替わる）
                it.mode = ("roll" if _kind(ta[i].text) == "digit" == _kind(tb[j].text)
                           else "fade")
            else:
                it.mode = replace
            it.arc = 0.0
            if it.mode == "roll":
                it.win = (_token_window(geom, ta[i], border), _token_window(geom, tb[j], border))
                it.hc = _roll_travel(geom, ta[i], tb[j], it.win[0], it.win[1])
            items.append(it)
            used["replace"] = True
        for i in pr["leave"]:
            if not ta[i].ink:
                continue
            it = _Item()
            it.kind, it.a, it.b = "leave", i, None
            it.lev = it.win = it.ext = None
            it.mode = (modes or {}).get("leave", leave)
            it.arc = 0.0
            it.hc = 0.0
            if it.mode == "roll":
                w = _token_window(geom, ta[i], border)
                it.win, it.hc = (w, w), w[3] - w[1]
            items.append(it)
            used["leave"] = True
        for j in pr["enter"]:
            if not tb[j].ink:
                continue
            it = _Item()
            it.kind, it.a, it.b = "enter", None, j
            it.lev = it.win = it.ext = None
            it.mode = (modes or {}).get("enter", enter)
            it.arc = 0.0
            it.hc = 0.0
            if it.mode == "roll":
                w = _token_window(geom, tb[j], border)
                it.win, it.hc = (w, w), w[3] - w[1]
            items.append(it)
            used["enter"] = True
        for n_it, it in enumerate(items):
            it.dir = dsign
            it.seed = k * 100003 + n_it
            it.group = "lo" if it.kind == "leave" else "hi"
            ref = ta[it.a] if it.a is not None else tb[it.b]
            it.fall = _FALL_RATIO * ref.lh
            it.drop = _DROP_RATIO * ref.lh
            it.scatter = _SCATTER_RATIO * ref.lh
            if it.mode == "roll":
                used["roll_dir"] = True

        # 時刻の窓（秒）。roll は anchor の反対側から、ほかは左から stagger 秒ずつ遅らせる
        def xpos(it):
            t = tb[it.b] if it.b is not None else ta[it.a]
            return (t.line, t.cx)

        rolling = [it for it in items if it.mode == "roll"]
        others = [it for it in items if it.mode != "roll"]
        if timing is not None:
            timing(k, items, ta, tb, D)
        else:
            if anchor == "right":
                rolling.sort(key=lambda it: (xpos(it)[0], xpos(it)[1]))
            else:
                rolling.sort(key=lambda it: (-xpos(it)[0], -xpos(it)[1]))
            others.sort(key=xpos)
            phase = _PHASES[(any(it.kind == "leave" for it in others),
                             any(it.kind == "enter" for it in others))]
            # 入れ替わる組がある遷移: 組と残る字は遅らせず、残る字は組が離れきっている間
            # （_LIFT〜1−_LIFT）だけ滑る。組が離れる前・降りる時に、滑ってくる字がその場所に
            # 居ると重なる（実測: '10' が降りる所に ',' がまだ居た）
            flying = any(it.kind == "move" and it.arc != 0.0 for it in others)
            together = [it for it in others if flying and it.kind in ("keep", "move")]
            for it in together:
                a, b = phase["other"]
                if it.kind == "keep":
                    a, b = _lerp(a, b, _LIFT), _lerp(a, b, 1.0 - _LIFT)
                it.t0, it.t1 = a * D, b * D
            others = [it for it in others if it not in together]
            for group, is_roll in ((others, False), (rolling, True)):
                n = len(group)
                st = float(stagger)
                if n > 1:
                    used["stagger"] = True
                    cap = _STAGGER_MAX_SHARE * D
                    if is_roll:
                        cap = min(cap, D - _ROLL_MIN_SEC)
                    st = min(st, max(cap, 0.0) / (n - 1))
                L = D - st * (n - 1) if n > 1 else D
                if is_roll and n and L < _ROLL_MIN_SEC - 1e-9:
                    raise ValueError(
                        f"{fn}: 回る字（replace='roll'）は1つ {_ROLL_MIN_SEC} 秒以上かけます。"
                        f"duration[{k}]={D:g} 秒では足りません（duration を延ばすか "
                        f"replace='fade' にしてください）")
                for r, it in enumerate(group):
                    d0 = r * st
                    if is_roll:
                        a, b = 0.0, 1.0
                    else:
                        a, b = phase.get(it.kind, phase["other"])
                    it.t0, it.t1 = d0 + a * L, d0 + b * L
        b0, N, den = trans_frames[k]
        for it in items:
            # 秒 → 遷移の中の割合（u）
            it.t0, it.t1 = it.t0 / D, it.t1 / D
            if it.t1 <= it.t0:
                it.t1 = it.t0 + 1e-6
        plan.items.append(items)
        plan.statics.append(statics)

    # --- 入れ替わる組の離れる高さ（時刻が決まった後。ほかの字の動きと重ならない高さ）---
    # move_need: 組の字（縁取りを含むインク）が上下 3px を残してキャンバスに収まる余白
    move_need = None
    plan.route = {}
    for k in range(n_tr):
        _route_moves(plan, geom, k, trans_frames[k][2], route_hint)
        for it in plan.items[k]:
            if it.lev is None or it.ext is None:
                continue
            plan.route[(k, it.a, it.b)] = it.lev - toks[k][it.a].chars[0].Y
            mt, mb = it.ext
            over = max(3.0 - (it.lev + mt), it.lev + mb + 3.0 - H)
            v = margin + int(math.ceil(over))
            move_need = v if move_need is None else max(move_need, v)
    plan.move_need = move_need

    for k in range(n_tr):
        b0, N, den = trans_frames[k]
        t_base = b0 / float(fps_frac)
        t_len = den / float(fps_frac)
        for it in plan.items[k]:
            schedule.append({
                "transition": k, "kind": it.kind, "mode": it.mode, "a": it.a, "b": it.b,
                "layer": "below" if it.group == "lo" else "above",
                "start": t_base + it.t0 * t_len, "end": t_base + it.t1 * t_len})
    plan.schedule = schedule
    plan.used = used
    return plan


# --- 描画（draw の中だけで使う。numpy・cv2・PIL）---

class _Sprite:
    """1字の絵: 縁取りのマスク・塗りのマスク（uint8）と、ペンの位置からのずれ。

    m2 は動く字用の float32 の (h, w, 2)（0..1。[..., 0] が縁取り、[..., 1] が塗り）。
    縁取りと塗りを1回の warpAffine で一緒に動かす（字ごとに2回 blit すると、
    トークン100個で1コマの半分以上が呼び出しの手間になった。実測）。
    fill32 / stroke32 は止まっている字を重ねるとき（_rest_masks）に作る。
    """
    __slots__ = ("stroke8", "fill8", "stroke32", "fill32", "ox", "oy", "w", "h", "m2",
                 "nbytes")


class _Layer:
    """動く字を重ねる層（必要になってから作る）と、描いた範囲。

    s: 縁取りのマスクの和（H×W、0..1。重ね方は 1−(1−a)(1−b)）
    f: 塗りの事前乗算の RGBA（H×W×4。重ね方は over）
    原点はキャンバスの (ox, oy)（モーションブラーの小さな作業面にも使う）。
    """
    __slots__ = ("s", "f", "ox", "oy", "w", "h", "x0", "y0", "x1", "y1")

    def __init__(self, np, w, h, ox=0, oy=0, alloc=False):
        self.w, self.h, self.ox, self.oy = w, h, ox, oy
        self.s = self.f = None
        self.x0 = self.y0 = 1 << 30
        self.x1 = self.y1 = -(1 << 30)
        if alloc:
            self.ensure(np)

    def ensure(self, np):
        if self.f is None:
            self.s = np.zeros((self.h, self.w), np.float32)
            self.f = np.zeros((self.h, self.w, 4), np.float32)

    def touch(self, x0, y0, x1, y1):
        self.x0, self.y0 = min(self.x0, x0), min(self.y0, y0)
        self.x1, self.y1 = max(self.x1, x1), max(self.y1, y1)

    def box(self):
        """描いた範囲（この層の添字）。何も描いていなければ None"""
        if self.f is None:
            return None
        x0, y0 = max(0, self.x0 - self.ox), max(0, self.y0 - self.oy)
        x1, y1 = min(self.w, self.x1 - self.ox), min(self.h, self.y1 - self.oy)
        if x0 >= x1 or y0 >= y1:
            return None
        return x0, y0, x1, y1


class _Renderer:
    """draw(i) の実体。字の絵・止まっている字のマスク・端のコマをメモする"""

    def __init__(self, plan):
        self.plan = plan
        deps = fk.need(plan.fn)
        self.np = deps.np
        self.pil = plan.pil
        self.geom = _Geom(plan)
        self.sprites = OrderedDict()
        self._sprite_bytes = 0
        self.static_frames = {}
        self._rest_key = None
        self._rest = None

    # --- 字の絵 ---

    def sprite(self, fkey, ch, sw=None):
        """1字の絵。sw は字形の上での縁取りの太さ（None なら border）。

        sw が端数なら、太さ floor(sw) と floor(sw)+1 の縁取りのマスクを端数で補間する
        （Pillow 10 の stroke_width は整数。補間した縁は、太さ sw の縁を AA で描いたものに
        近い）。補間した絵はメモしない（大きさの変わる字はコマごとに太さが違う）。
        """
        if sw is None:
            return self._sprite_n(fkey, ch, self.plan.border)
        n = int(math.floor(sw + 1e-6))
        t = sw - n
        if t < 1e-3:
            return self._sprite_n(fkey, ch, n)
        hi = self._sprite_n(fkey, ch, n + 1)
        if hi is None:
            return None
        lo = self._stroke_in(fkey, ch, n, hi)
        sp = _Sprite()
        sp.fill8 = sp.stroke8 = sp.fill32 = sp.stroke32 = None
        sp.ox, sp.oy, sp.w, sp.h = hi.ox, hi.oy, hi.w, hi.h
        m2 = hi.m2.copy()
        st = m2[..., 0]
        st -= lo
        st *= self.np.float32(t)
        st += lo
        sp.m2 = m2
        sp.nbytes = 0
        return sp

    def _memo_get(self, key):
        v = self.sprites.get(key, False)
        if v is not False:
            self.sprites.move_to_end(key)
        return v

    def _memo_put(self, key, v, nbytes):
        self.sprites[key] = v
        self._sprite_bytes += nbytes
        # 大きさの変わる字は太さの違う縁取りを使うので、古いものから捨てる
        while self._sprite_bytes > _SPRITE_BUDGET and len(self.sprites) > 1:
            _k, old = self.sprites.popitem(last=False)
            if old is not None:
                self._sprite_bytes -= old.nbytes
        return v

    def _sprite_n(self, fkey, ch, n):
        """縁取りの太さ n の字の絵（メモする）。n = border が端のコマと同じ絵"""
        key = (fkey, ch, n)
        sp = self._memo_get(key)
        if sp is not False:
            return sp
        np = self.np
        Image, ImageDraw = self.pil["Image"], self.pil["ImageDraw"]
        g = self.geom.glyph(fkey, ch, float(n))
        sp = None
        if g is not None:
            _l, _t, _r, _b, ox, oy, w, h = g
            font = self.geom.font(fkey)
            org = (-ox, -oy)                       # 整数の位置に描く（丸めが起きない）
            fill = Image.new("L", (w, h), 0)
            ImageDraw.Draw(fill).text(org, ch, font=font, fill=255, anchor="ls")
            fill8 = np.asarray(fill, dtype=np.uint8)
            if n:
                st = Image.new("L", (w, h), 0)
                ImageDraw.Draw(st).text(org, ch, font=font, fill=255, anchor="ls",
                                        stroke_width=n, stroke_fill=255)
                stroke8 = np.asarray(st, dtype=np.uint8)
            else:
                stroke8 = None
            if fill8.any() or (stroke8 is not None and stroke8.any()):
                sp = _Sprite()
                sp.fill8 = fill8
                sp.stroke8 = stroke8
                sp.fill32 = sp.stroke32 = None
                sp.ox, sp.oy = ox, oy
                sp.w, sp.h = w, h
                m2 = np.empty((h, w, 2), np.float32)
                m2[..., 0] = stroke8 if stroke8 is not None else fill8
                m2[..., 1] = fill8
                m2 *= np.float32(1.0 / 255.0)
                sp.m2 = m2
                sp.nbytes = m2.nbytes + 2 * fill8.nbytes
        return self._memo_put(key, sp, 0 if sp is None else sp.nbytes)

    def _stroke_in(self, fkey, ch, n, hi):
        """縁取りの太さ n のマスク（0..1 の float32）を、字の絵 hi と同じ寸法・原点で描く"""
        key = (fkey, ch, n, "in")
        v = self._memo_get(key)
        if v is not False:
            return v
        Image, ImageDraw = self.pil["Image"], self.pil["ImageDraw"]
        st = Image.new("L", (hi.w, hi.h), 0)
        ImageDraw.Draw(st).text((-hi.ox, -hi.oy), ch, font=self.geom.font(fkey), fill=255,
                                anchor="ls", stroke_width=n, stroke_fill=255)
        v = self.np.asarray(st, dtype=self.np.float32) * self.np.float32(1.0 / 255.0)
        return self._memo_put(key, v, v.nbytes)

    # --- 端のコマ（text_image と同じ関数で描く）---

    def static_frame(self, s):
        img = self.static_frames.get(s)
        if img is None:
            p = self.plan
            out = _ti._render(
                self.pil, p.layouts[s], border=p.border, border_rgba=p.border_rgba,
                shadow=p.shadow, shadow_rgba=p.shadow_rgba, shadow_blur=p.shadow_blur,
                background_rgba=None, background_radius=0)
            img = self.np.asarray(out, dtype=self.np.uint8)
            img.flags.writeable = False
            self.static_frames[s] = img
        return img

    # --- 1つの遷移の途中のコマ ---

    def frame(self, k, u):
        if u <= 0.0:
            return self.static_frame(k)
        if u >= 1.0:
            return self.static_frame(k + 1)
        p = self.plan
        exact = []
        moving = []       # (group, [サンプルごとの _Draw の列])
        ta, tb = p.toks[k], p.toks[k + 1]
        for i, _j in p.statics[k]:
            for c in ta[i].chars:
                if c.ink:
                    exact.append(_Draw(c.fkey, c.ch, c.X, c.Y, c.rgba, "hi", exact=True))
        _b0, _N, den = self._trans_info(k)
        du = 1.0 / den
        for it in p.items[k]:
            draws = _item_draws(p, self.geom, it, ta, tb, u)
            speed, dist = (self._speed(it, ta, tb, u, du, draws) if p.motion_blur
                           else (0.0, 0.0))
            if speed >= _BLUR_RATIO:
                # シャッター 180 度（1コマの半分の区間）に中間位置を等間隔に置く。
                # 1コマ全体に広げると、速い字が離れた像に分かれて見える。間隔が
                # _BLUR_STEP_PX を超えないよう、速いほど点を増やす（3〜_BLUR_MAX_SAMPLES。
                # 間隔を字幅の割合で決めると、大きい字の横の縁が数 px おきの像に分かれた）
                n = min(_BLUR_MAX_SAMPLES,
                        max(_BLUR_SAMPLES, int(math.ceil(dist * _BLUR_SHUTTER / _BLUR_STEP_PX)) + 1))
                samples = []
                for s in range(n):
                    off = (s / (n - 1) - 0.5) * _BLUR_SHUTTER
                    us = min(1.0, max(0.0, u + off * du))
                    samples.append(_item_draws(p, self.geom, it, ta, tb, us))
                moving.append((it.group, samples))
                continue
            rest = []
            for d in draws:
                if d.exact:
                    exact.append(d)
                else:
                    rest.append(d)
            if rest:
                moving.append((it.group, [rest]))
        return self._compose(exact, moving)

    def _trans_info(self, k):
        info = getattr(self, "_tinfo", None)
        if info is None:
            info = {}
            table = self.plan.table
            for f, ent in enumerate(table):
                if ent[0] == "trans" and ent[1] not in info:
                    info[ent[1]] = [f, 0, None]
                if ent[0] == "trans":
                    info[ent[1]][1] += 1
            for kk, v in info.items():
                b0, N = v[0], v[1]
                last = table[b0 + N - 1][2]
                v[2] = N - 1 if last >= 1.0 - 1e-12 else N
            self._tinfo = info
        return self._tinfo[k]

    def _speed(self, it, ta, tb, u, du, draws):
        """(1コマで動く距離 ÷ 字幅, 1コマで動く距離 px)（その動くものの中で一番速い字）"""
        geom = self.geom
        prev = _item_draws(self.plan, geom, it, ta, tb, max(0.0, u - du))
        if len(prev) != len(draws):
            return 0.0, 0.0
        best = 0.0
        dist = 0.0
        for d0, d1 in zip(prev, draws):
            fkey, s, sw = geom.resolve(d1)
            g = geom.glyph(fkey, d1.ch, sw)
            if g is None:
                continue
            adv = geom.font(fkey).getlength(d1.ch) * s
            if adv <= 0:
                adv = g[6] * s
            if adv > 0:
                dd = math.hypot(d1.X - d0.X, d1.Y - d0.Y)
                best = max(best, dd / adv)
                dist = max(dist, dd)
        return best, dist

    # --- 合成 ---

    def _rest_masks(self, exact):
        """止まっている字のマスク（uint8）。Pillow の ImageDraw と同じ整数の式で重ねる"""
        key = tuple((d.fkey, d.ch, d.X, d.Y, d.rgba) for d in exact)
        if key == self._rest_key:
            return self._rest
        np = self.np
        p = self.plan
        W, H = p.W, p.H
        stroke = np.zeros((H, W), np.uint8) if p.border else None
        fills = {}
        order = sorted(exact, key=lambda d: (d.Y, d.X))
        for d in order:
            sp = self.sprite(d.fkey, d.ch)
            if sp is None:
                continue
            if sp.fill32 is None:
                sp.fill32 = sp.fill8.astype(np.uint32)
                if sp.stroke8 is not None:
                    sp.stroke32 = sp.stroke8.astype(np.uint32)
            if stroke is not None:
                _blend_into(np, stroke, sp.stroke32, d.X + sp.ox, d.Y + sp.oy)
            f = fills.get(d.rgba)
            if f is None:
                f = fills[d.rgba] = np.zeros((H, W), np.uint8)
            _blend_into(np, f, sp.fill32, d.X + sp.ox, d.Y + sp.oy)
        self._rest_key = key
        self._rest = (stroke, fills)
        return self._rest

    def _warp(self, sp, d, scale):
        """1字の絵（縁取りと塗りの2ch）を、置く位置へ写す。

        戻り値: (x0, y0, patch)。patch は float32 の (h, w, 2)、左上がキャンバスの (x0, y0)。
        座標と変換は framekit.blit と同じ（SVG と同じ連続座標。pivot を軸に拡大縮小・
        回転。角度は度で時計回り）。整数位置・等倍・無回転なら絵をそのまま返す
        （動き出す前・止まった後の字は端のコマと同じ画素）。それ以外は framekit.warp_patch
        （blit の変換の核をそのまま使う。縮小は INTER_AREA で「元の寸法 × scale」へ縮めてから
        INTER_LINEAR。0.5 倍の前後で補間の方式が切り替わって面積が揺れない）。
        縁取りと塗りを1回で写す（字ごとに framekit.blit を2回呼ぶと、トークン100個で
        1コマの半分以上が呼び出しの手間になった。実測）。
        clip は (x0, y0, x1, y1) の窓で、縦横とも端数の縁を被覆率で落とす。
        """
        np = self.np
        src = sp.m2
        x = d.X + sp.ox
        y = d.Y + sp.oy
        angle = d.angle % 360.0
        if (scale == 1.0 and angle == 0.0 and float(x).is_integer()
                and float(y).is_integer()):
            x0, y0 = int(x), int(y)
            patch = src
        else:
            pivot = (-sp.ox, -sp.oy) if d.pivot == "pen" else None   # None は絵の中心
            x0, y0, patch = fk.warp_patch(src, x, y, scale=scale, angle=angle, pivot=pivot)
        if d.clip is not None:
            # 回る字の窓（縦横とも切る。端数の縁は被覆率で落とす）
            cx0, cy0, cx1, cy1 = d.clip
            ph, pw = patch.shape[:2]
            rows = np.arange(y0, y0 + ph, dtype=np.float64)
            cols = np.arange(x0, x0 + pw, dtype=np.float64)
            cov_y = np.clip(np.minimum(rows + 1.0, cy1) - np.maximum(rows, cy0), 0.0, 1.0)
            cov_x = np.clip(np.minimum(cols + 1.0, cx1) - np.maximum(cols, cx0), 0.0, 1.0)
            patch = patch * (cov_y[:, None] * cov_x[None, :]).astype(np.float32)[..., None]
        return x0, y0, patch

    def _put(self, lay, d, sp, scale):
        """1字を層へ重ねる（縁取りは和 1−(1−a)(1−b)、塗りは色をつけて over）"""
        np = self.np
        x0, y0, patch = self._warp(sp, d, scale)
        ph, pw = patch.shape[:2]
        lx0, ly0 = x0 - lay.ox, y0 - lay.oy
        a0, b0 = max(0, lx0), max(0, ly0)
        a1, b1 = min(lay.w, lx0 + pw), min(lay.h, ly0 + ph)
        if a0 >= a1 or b0 >= b1:
            return
        pp = patch[b0 - ly0:b1 - ly0, a0 - lx0:a1 - lx0]
        lay.ensure(np)
        k = d.alpha * d.rgba[3] / 255.0
        if self.plan.border:
            s = pp[..., 0] if d.alpha == 1.0 else pp[..., 0] * np.float32(d.alpha)
            S = lay.s[b0:b1, a0:a1]
            S += s - S * s
        fa = pp[..., 1] if k == 1.0 else pp[..., 1] * np.float32(k)
        F = lay.f[b0:b1, a0:a1]
        F *= (1.0 - fa)[..., None]
        F += fa[..., None] * np.array(
            (d.rgba[0] / 255.0, d.rgba[1] / 255.0, d.rgba[2] / 255.0, 1.0), np.float32)
        lay.touch(a0 + lay.ox, b0 + lay.oy, a1 + lay.ox, b1 + lay.oy)

    def _add(self, acc_s, acc_f, ox, oy, d, sp, scale):
        """1字を作業面へ足す（モーションブラーの中間位置用。和は後で平均する）"""
        np = self.np
        x0, y0, patch = self._warp(sp, d, scale)
        ph, pw = patch.shape[:2]
        h, w = acc_f.shape[:2]
        lx0, ly0 = x0 - ox, y0 - oy
        a0, b0 = max(0, lx0), max(0, ly0)
        a1, b1 = min(w, lx0 + pw), min(h, ly0 + ph)
        if a0 >= a1 or b0 >= b1:
            return
        pp = patch[b0 - ly0:b1 - ly0, a0 - lx0:a1 - lx0]
        if self.plan.border:
            acc_s[b0:b1, a0:a1] += pp[..., 0] * np.float32(d.alpha)
        k = d.alpha * d.rgba[3] / 255.0
        acc_f[b0:b1, a0:a1] += (pp[..., 1] * np.float32(k))[..., None] * np.array(
            (d.rgba[0] / 255.0, d.rgba[1] / 255.0, d.rgba[2] / 255.0, 1.0), np.float32)

    @staticmethod
    def _bbox(d, sp, scale):
        """置いた字の絵の外接矩形（整数、余白つき）"""
        x = d.X + sp.ox
        y = d.Y + sp.oy
        if scale == 1.0 and d.angle == 0.0:
            return (int(math.floor(x)) - 1, int(math.floor(y)) - 1,
                    int(math.ceil(x + sp.w)) + 1, int(math.ceil(y + sp.h)) + 1)
        px, py = (-sp.ox, -sp.oy) if d.pivot == "pen" else (sp.w / 2.0, sp.h / 2.0)
        th = math.radians(d.angle)
        c, s = math.cos(th) * scale, math.sin(th) * scale
        ax, ay = x + px, y + py
        xs, ys = [], []
        for u, v in ((-px, -py), (sp.w - px, -py), (-px, sp.h - py), (sp.w - px, sp.h - py)):
            xs.append(ax + c * u - s * v)
            ys.append(ay + s * u + c * v)
        return (int(math.floor(min(xs))) - 2, int(math.floor(min(ys))) - 2,
                int(math.ceil(max(xs))) + 2, int(math.ceil(max(ys))) + 2)

    def _resolved(self, d):
        fkey, s, sw = self.geom.resolve(d)
        return self.sprite(fkey, d.ch, sw), s

    def _draw_moving(self, layers, group, samples):
        np = self.np
        p = self.plan
        lay = layers[group]
        if len(samples) == 1:
            for d in samples[0]:
                sp, s = self._resolved(d)
                if sp is not None:
                    self._put(lay, d, sp, s)
            return
        # モーションブラー: 中間位置の絵を小さな作業面で平均し、1回だけ層へ重ねる
        resolved = []
        boxes = []
        for draws in samples:
            for d in draws:
                sp, s = self._resolved(d)
                if sp is not None:
                    resolved.append((d, sp, s))
                    boxes.append(self._bbox(d, sp, s))
        if not boxes:
            return
        bx0 = max(0, min(b[0] for b in boxes))
        by0 = max(0, min(b[1] for b in boxes))
        bx1 = min(p.W, max(b[2] for b in boxes))
        by1 = min(p.H, max(b[3] for b in boxes))
        if bx0 >= bx1 or by0 >= by1:
            return
        bw, bh = bx1 - bx0, by1 - by0
        acc_s = np.zeros((bh, bw), np.float32)
        acc_f = np.zeros((bh, bw, 4), np.float32)
        # 中間位置ごとの絵を足し合わせる（1つの中間位置の中で字が重なる所は和が 1 を
        # 超えうるので、平均した後で 1 に切り詰める。速く動く間だけの近似）
        for d, sp, s in resolved:
            self._add(acc_s, acc_f, bx0, by0, d, sp, s)
        inv = np.float32(1.0 / len(samples))
        acc_s *= inv
        acc_f *= inv
        np.minimum(acc_s, 1.0, out=acc_s)
        al = acc_f[..., 3]
        big = al > 1.0
        if big.any():
            acc_f[big] /= al[big][:, None]
        lay.ensure(np)
        S = lay.s[by0:by1, bx0:bx1]
        S += acc_s - S * acc_s
        F = lay.f[by0:by1, bx0:bx1]
        F *= 1.0 - acc_f[..., 3:4]
        F += acc_f
        lay.touch(bx0, by0, bx1, by1)

    def _compose(self, exact, moving):
        np = self.np
        p = self.plan
        pil = self.pil
        Image = pil["Image"]
        W, H = p.W, p.H
        stroke8, fills8 = self._rest_masks(exact)
        layers = {"hi": _Layer(np, W, H), "lo": _Layer(np, W, H)}
        for group, samples in moving:
            self._draw_moving(layers, group, samples)
        hi, lo = layers["hi"], layers["lo"]

        # 縁取りのマスク = 止まっている字（uint8）と動く字（float）の和 1−(1−a)(1−b)
        stroke_total = stroke8
        hi_box = hi.box()
        if p.border and hi_box is not None:
            x0, y0, x1, y1 = hi_box
            stroke_total = stroke8.copy()
            s = stroke8[y0:y1, x0:x1].astype(np.float32) * np.float32(1.0 / 255.0)
            m = hi.s[y0:y1, x0:x1]
            tot = 1.0 - (1.0 - s) * (1.0 - m)
            stroke_total[y0:y1, x0:x1] = np.clip(
                tot * np.float32(255.0) + np.float32(0.5), 0, 255).astype(np.uint8)

        # 下の層（消える字）: 縁取りの上に塗りを重ねた RGBA
        lo8 = None
        lo_box = lo.box()
        if lo_box is not None:
            x0, y0, x1, y1 = lo_box
            f = lo.f[y0:y1, x0:x1]
            if p.border:
                br = p.border_rgba
                ba = br[3] / 255.0
                col = np.array([br[0] / 255.0 * ba, br[1] / 255.0 * ba,
                                br[2] / 255.0 * ba, ba], np.float32)
                pm = lo.s[y0:y1, x0:x1, None] * col
                pm *= 1.0 - f[..., 3:4]
                pm += f
            else:
                pm = f
            lo8 = fk.to_rgba8(pm)
        hi8 = None
        if hi_box is not None:
            x0, y0, x1, y1 = hi_box
            hi8 = fk.to_rgba8(hi.f[y0:y1, x0:x1])

        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if p.shadow != (0, 0) or p.shadow_blur:
            union = stroke_total.copy() if stroke_total is not None else np.zeros((H, W), np.uint8)
            for f in fills8.values():
                np.maximum(union, f, out=union)
            for arr8, box in ((lo8, lo_box), (hi8, hi_box)):
                if arr8 is not None:
                    x0, y0, x1, y1 = box
                    np.maximum(union[y0:y1, x0:x1], arr8[..., 3], out=union[y0:y1, x0:x1])
            moved = Image.new("L", (W, H), 0)
            moved.paste(Image.fromarray(union, "L"), (p.shadow[0], p.shadow[1]))
            if p.shadow_blur:
                moved = moved.filter(pil["ImageFilter"].GaussianBlur(p.shadow_blur))
            img = Image.alpha_composite(img, _ti._tint(pil, moved, p.shadow_rgba))
        if lo8 is not None:
            img.alpha_composite(Image.fromarray(lo8, "RGBA"), dest=(lo_box[0], lo_box[1]))
        if stroke_total is not None:
            img = Image.alpha_composite(
                img, _ti._tint(pil, Image.fromarray(stroke_total, "L"), p.border_rgba))
        for rgba, f in fills8.items():
            img = Image.alpha_composite(img, _ti._tint(pil, Image.fromarray(f, "L"), rgba))
        if hi8 is not None:
            img.alpha_composite(Image.fromarray(hi8, "RGBA"), dest=(hi_box[0], hi_box[1]))
        return np.asarray(img, dtype=np.uint8)

    def __call__(self, i):
        ent = self.plan.table[i]
        if ent[0] == "hold":
            return self.static_frame(ent[1])
        return self.frame(ent[1], ent[2])


def _blend_into(np, dst8, m32, x, y):
    """Pillow の ImageDraw.text と同じ式で、マスク m を dst へ重ねる（インクは 255）。

    out = DIV255(out·(255−m) + 255·m)、DIV255(a) = ((a+128) + ((a+128) >> 8)) >> 8
    （Pillow の Paste.c の BLEND。実測で ImageDraw と差 0）
    """
    H, W = dst8.shape
    h, w = m32.shape
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x0 >= x1 or y0 >= y1:
        return
    m = m32[y0 - y:y1 - y, x0 - x:x1 - x]
    out = dst8[y0:y1, x0:x1]
    t = out.astype(np.uint32) * (255 - m) + 255 * m + 128
    out[...] = ((t >> 8) + t) >> 8


# --- 鍵 ---

def _key_style(fn, pil, st):
    """区間の書式を鍵の形にする（フォントは内容指紋、色は (r, g, b, a)）"""
    out = {}
    for k, v in st.items():
        if k == "font" and v is not None:
            out[k] = _file_fingerprint(_resolve_font(v))
        elif k == "color":
            out[k] = list(_ti._rgba(fn, "color", v, pil))
        else:
            out[k] = v
    return out


def _key_params(fn, plan_args, spans_states, extra, used):
    """鍵に入れる値。効かない引数は入れない（同一出力なら同一鍵）:
    prefer は match='edit' の遷移があるとき、move は位置の変わる残る字があるとき、
    replace / leave / enter はその種類の字があるとき、roll_dir は回る字があるとき、
    stagger は遅らせる字が2つ以上あるとき、swing は上下に離れる組があるときだけ"""
    pil = _ti._import_pil(fn)
    fmt = plan_args["fmt"]
    kfmt = {}
    for k, v in fmt.items():
        if k in ("markup", "styles"):
            continue      # 区間に展開済み（効く書式は区間の側に入る）
        if k == "font":
            kfmt[k] = _file_fingerprint(_resolve_font(v))
        elif k in ("color", "border_color", "shadow_color"):
            kfmt[k] = list(_ti._rgba(fn, k, v, pil))
        else:
            kfmt[k] = v
    out = {
        "states": [[[text, _key_style(fn, pil, st)] for text, st in spans]
                   for spans in spans_states],
        "fmt": kfmt,
        "duration": plan_args["duration"], "hold": plan_args["hold"],
        "easing": plan_args["ease_key"], "unit": plan_args["unit"],
        "match": plan_args["match"], "anchor": plan_args["anchor"],
        "motion_blur": plan_args["motion_blur"],
        "extra": extra,
    }
    if used["prefer"]:
        out["prefer"] = list(plan_args["prefer"])
    for name in ("move", "replace", "leave", "enter", "roll_dir", "stagger", "swing"):
        if used[name]:
            out[name] = plan_args[name]
    return out


# --- 本体 ---

def _core(fn, states, *, duration, hold, unit, match, prefer, move, replace, leave, enter,
          stagger, easing, anchor, roll_dir, motion_blur, fmt, swing=None, timing=None,
          modes=None, extra=None):
    if not isinstance(states, (list, tuple)) or len(states) < 2:
        raise ValueError(
            f"{fn}: states には状態（文字列か区間のリスト）を2つ以上並べてください: {states!r}")
    if len(states) > _MAX_STATES:
        raise ValueError(f"{fn}: states は {_MAX_STATES} 個までです（{len(states)} 個）")
    n_tr = len(states) - 1
    dur = _per(fn, "duration", duration, n_tr, "遷移")
    for k, d in enumerate(dur):
        if d <= 0:
            raise ValueError(f"{fn}: duration は 0 より大きくしてください: {duration!r}")
    hl = _per(fn, "hold", hold, len(states), "状態")
    if not isinstance(unit, str) or not unit:
        raise TypeError(
            f"{fn}: unit は 'char' / 'word' / 'code' か、トークンを表す正規表現の文字列で"
            f"指定してください: {unit!r}")
    if unit not in _UNIT_RES:
        if re.fullmatch(r"\w+", unit):
            raise ValueError(
                f"{fn}: unit={unit!r} は知らない単位です（'char' / 'word' / 'code' か、"
                f"正規表現の文字列）{_suggest_hint(unit, list(_UNIT_RES))}")
        try:
            re.compile(unit)
        except re.error as e:
            raise ValueError(f"{fn}: unit の正規表現が不正です: {unit!r}（{e}）") from None
    if isinstance(match, str):
        _require_choice(fn, "match", match, _MATCHES)
    elif not isinstance(match, (list, tuple)):
        raise TypeError(
            f"{fn}: match は 'auto' / 'edit' / 'position' か、(i, j) の対のリストで"
            f"指定してください: {match!r}")
    if (not isinstance(prefer, (list, tuple)) or sorted(prefer) != sorted(_PREFER_OPS)):
        raise ValueError(
            f"{fn}: prefer は {_PREFER_OPS} を並べ替えたもの（3つとも1回ずつ）で"
            f"指定してください: {prefer!r}")
    _require_choice(fn, "move", move, _MOVES)
    _require_choice(fn, "replace", replace, _REPLACES)
    _require_choice(fn, "leave", leave, _LEAVES)
    _require_choice(fn, "enter", enter, _ENTERS)
    _require_choice(fn, "anchor", anchor, _ANCHORS)
    _require_choice(fn, "roll_dir", roll_dir, _ROLL_DIRS)
    _require_number(fn, "stagger", stagger, 0, 10)
    if swing is None:
        swing = _SWING
    _require_number(fn, "swing", swing, 0, 10)
    if not isinstance(motion_blur, bool):
        raise TypeError(f"{fn}: motion_blur は True / False で指定してください: {motion_blur!r}")
    ease_f, ease_key = fk.easing(fn, easing)
    args = _fmt_args(fn, fmt, anchor)
    plan_args = {
        "duration": dur, "hold": hl, "unit": unit,
        "match": match if isinstance(match, str) else fk.norm(match),
        "prefer": tuple(prefer), "move": move, "replace": replace, "leave": leave,
        "enter": enter, "stagger": float(stagger), "ease_key": ease_key, "anchor": anchor,
        "roll_dir": roll_dir, "motion_blur": motion_blur, "fmt": args, "swing": float(swing),
    }

    def compute():
        kw = dict(duration=dur, hold=hl, unit=unit, match=match, prefer=tuple(prefer),
                  move=move, replace=replace, leave=leave, enter=enter,
                  stagger=float(stagger), ease_key=ease_key, ease_f=ease_f, anchor=anchor,
                  roll_dir=roll_dir, motion_blur=motion_blur, fmt=args, swing=float(swing),
                  timing=timing, modes=modes)
        plan = _build_plan(fn, states, **kw)
        for _pass in range(3):
            # 入れ替わる組の余白が見積もりと違えば、要る余白で作り直す（離れる高さは縦の
            # 平行移動に対して同じなので、たいてい1回で合う。足りないまま終えない）
            need = plan.move_need
            if need is None or (need <= plan.margin
                                and plan.margin <= max(need, plan.margin_other) + 4):
                break
            plan = _build_plan(fn, states, move_margin=max(need, 0), route_hint=plan.route,
                               **kw)
        return plan

    fps = _resolve_fps(fn, None)
    try:
        memo_key = {"fn": fn, "states": fk.norm(list(states)), "args": fk.norm(
            {k: v for k, v in plan_args.items() if k != "fmt"}),
            "fmt": fk.norm({k: (v if k != "font" else _resolve_font(v))
                            for k, v in args.items()}),
            "extra": fk.norm(extra), "fps": fk.norm(float(fps)), "ver": _TEXTMOVE_VER}
        plan = fk.memo("text_transition", memo_key, compute)
    except TypeError:
        plan = compute()

    spans_states =[_ti._normalize_spans(fn, s, args["markup"],
                                         {k: _ti._check_style(fn, k, v)
                                          for k, v in (args["styles"] or {}).items()})
                    for s in states]
    params = _key_params(fn, plan_args, spans_states, extra, plan.used)

    renderer = []

    def draw(i):
        if not renderer:
            renderer.append(_Renderer(plan))
        return renderer[0](i)

    toks_text = [[t.text for t in ts] for ts in plan.toks]
    lay_all = plan.layouts
    sizes = [lay["size_min"] for lay in lay_all] + [lay["size_max"] for lay in lay_all]
    widest = max(lay_all, key=lambda lay: lay["content_width"])
    text_meta = {
        "content": " → ".join(lay["plain"].replace("\n", " ") for lay in lay_all),
        "size": _ti._norm_num(args["size"]),
        "size_min": min(sizes),
        "size_max": max(sizes),
        "border": plan.border,
        "shadow": tuple(plan.shadow),
        "shadow_blur": plan.shadow_blur,
        "background": False,
        "font": _resolve_font(args["font"]),
        "width": plan.W,
        "height": plan.H,
        "content_width": widest["content_width"],
        "content_height": max(lay["content_height"] for lay in lay_all),
        "padding": tuple(widest["padding"]),
        "lines": max(len(lay["lines"]) for lay in lay_all),
        "missing": [],
    }
    # 各状態のコマと画素が一致する text_image の引数（fmt と合わせて渡す）。fmt に
    # align / padding を渡したときは、ここには入れない（同じ名前が2回渡らないように）
    ti_kwargs = {"canvas": (plan.W, plan.H)}
    if "align" not in fmt:
        ti_kwargs["align"] = anchor
    if "padding" not in fmt:
        ti_kwargs["padding"] = plan.padding
    info = {
        "starts": list(plan.starts),
        "pairs": [{k: [tuple(p) if isinstance(p, tuple) else p for p in v]
                   for k, v in pr.items()} for pr in plan.pairs],
        "tokens": toks_text,
        "canvas": (plan.W, plan.H),
        "padding": plan.padding,
        "text_image_kwargs": ti_kwargs,
        "schedule": [dict(s) for s in plan.schedule],
        "state_frames": list(plan.state_frames),
        "n_frames": plan.n_frames,
        "fps": float(plan.fps_frac),
    }
    obj = fk.build(fn, kind="text_transition", ver=_TEXTMOVE_VER, params=params, draw=draw,
                   n_frames=plan.n_frames, size=(plan.W, plan.H), fonts=plan.font_refs,
                   text=text_meta, info=info)
    obj._textmove_plan = plan     # テスト用（字の置き方を幾何で確かめる）
    return obj


def text_transition(states, *, duration=1.2, hold=0.0, unit="char", match="auto",
                    prefer=("replace", "insert", "delete"), move="slide", replace="auto",
                    leave="fade", enter="fade", stagger=_STAGGER, easing="ease_in_out_cubic",
                    anchor="left", roll_dir="auto", motion_blur=True, swing=_SWING, **fmt):
    """文字列の状態 A→B→… を字単位で組み替える動画 Object を返す（残る字は滑る・数字は回る）。

    states: 2つ以上の状態。各状態は文字列か、text_image と同じ区間のリスト
      [("文字", {書式}), …]。状態ごとに大きさ・色が違ってよい（区間の書式で指定）。
      対応した字は動きながら色（oklab で補間）と大きさを変える（縁取りの太さは変えない）。
      赤くしたい字は、後の状態の区間で赤にする。1状態 400 トークン・3行まで。
    duration: 遷移ごとの秒（数か、遷移の数のリスト）。hold: 状態ごとに止まる秒
      （数か、状態の数のリスト）。コマ数 = (Σhold + Σduration) × fps。
    unit: トークンの単位。'char'（1字）/ 'word'（語。空白もトークン）/
      'code'（識別子・数・文字列リテラル・空白・1字の記号）/ 正規表現の文字列
      （例: 正規表現の式なら r'\\\\.|.' でエスケープを1トークンにする）。
    match: 対応のとり方。
      'auto'  LCS（自前の DP。タイは左を残す）→ 残りの同じ文字列を位置の近い順に対に
              して move → LCS の隙間で位置のそろう残りのうち、種類（数字・文字・全角の字・
              空白・記号）が同じ組を replace → 残りは leave / enter
      'edit'  字単位のレーベンシュタインの後戻り（タイは prefer の順）
      'position'  anchor 側から位置で対応させる
      [(i, j), …]  手で対応を渡す（番号は obj.figure.tokens の添字）
    move: 位置の変わる残る字の道筋。'slide'（直線）/ 'arc'（弧）。左右が入れ替わる組は
      いつも上下に離れて運ばれる（左へ行く字が上、右へ行く字が下。縦に離れ、横へ運ばれ、
      縦に戻る。ほかの字に重ならない高さを構築時に選び、行の中の残る字は組が離れて
      いる間だけ滑る）。
    swing: 入れ替わる組が行から離れる量の上限（行の字の高さ = ascent + descent の倍。
      既定 1.0）。行の字をちょうど避ける量より小さくはしない（0 で、いつもちょうど避ける
      高さ）。ほかの組と重なるのを避けて深く離れるのも、この上限まで。キャンバスの高さも
      これで決まる（離れた字が収まる分だけ上下に余白を取る）。
    replace: 入れ替わる字。'auto'（数字どうしは roll、ほかは fade）/ 'roll'（字の窓の
      中を縦に流れる。窓で縦横とも切り抜く）/ 'fade' / 'swap'（縮んで消え、膨らんで
      現れる）。roll は1字 0.25 秒以上かける。
    leave: 消える字（下の層に置く）。'fade' / 'fall'（落ちる）/ 'scatter'（散る。
      キャンバスの中に収める）。
    enter: 現れる字。'fade' / 'drop'（上から）/ 'rise'（下から）。
    stagger: 字ごとの遅れ（秒。既定 0.02）。roll は anchor の反対側から、ほかは左から。
      広がりが duration の 30% を超えるときは詰める。
    easing: 動きの緩急（名前・イージング関数・Expr）。
    anchor: 'left' / 'right' / 'center'。共通キャンバスの中の横の揃え（text_image の
      align と同じ）。全体の幅が変わっても、変わらない字は止まったまま。
    roll_dir: 'auto'（増えるなら上、減るなら下。日時を巻き戻すと下）/ 'up' / 'down'。
    motion_blur: 1コマで字幅の 0.5 倍以上動く区間だけ、中間位置の平均で描く（点は
      3〜10 個。間隔が 3px 以下になるよう、速いほど増やす）。
    fmt: text_image の書式（size・font・font_index・weight・color・markup・styles・
      line_spacing・align・border・border_color・shadow・shadow_color・shadow_blur・
      padding）。max_width は受けない（長い字幕は ValueError で案内する）。
      align は省略する（横の揃えは anchor）。padding の既定は text_image の自然な寸法と
      同じ余白（縁取り + 影 + size の 15%。張り出す字の端が切れない）。
      align / padding に None は渡さない（省略する）。

    戻り値の obj.figure:
      starts  各状態に着いた秒（その Object の先頭から。フレーム格子上。最後の状態の
              hold が 0 のときは最後のコマの秒）
      pairs   遷移ごとの対応表 {'keep', 'move', 'replace': [(i, j)…], 'leave': [i…],
              'enter': [j…]}（番号は tokens の添字）
      tokens  状態ごとのトークンの文字列
      canvas  (幅, 高さ)。padding は共通キャンバスの余白
      text_image_kwargs  canvas と、fmt に無ければ align・padding。各状態のコマは
              text_image(状態, **fmt, **text_image_kwargs) と画素が一致する
      schedule  動くものごとの {transition, kind, mode, a, b, layer, start, end}（秒）
      state_frames  各状態が正確に映るコマの番号
    使いすぎると忙しくなる。「答えが変わる」瞬間だけに置く。
    """
    return _core("text_transition", states, duration=duration, hold=hold, unit=unit,
                 match=match, prefer=prefer, move=move, replace=replace, leave=leave,
                 enter=enter, stagger=stagger, easing=easing, anchor=anchor,
                 roll_dir=roll_dir, motion_blur=motion_blur, fmt=fmt, swing=swing)


# --- odometer ---

_DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# odometer が text_transition へ渡してよい引数（書式のほか）
_ODO_PASS = ("hold", "easing", "anchor", "motion_blur")


def _format_number(fn, name, v, base, digits, signed, group, sep):
    if isinstance(v, bool) or not isinstance(v, int):
        raise TypeError(f"{fn}: {name} は整数で指定してください: {v!r}")
    if signed == "twos":
        mod = base ** digits
        if not -(mod // 2) <= v < mod:
            raise ValueError(
                f"{fn}: {name}={v} は {digits} 桁の2の補数で表せません"
                f"（{-(mod // 2)}〜{mod - 1}）")
        v %= mod
        neg = False
    else:
        neg = v < 0
        v = -v if neg else v
    s = ""
    while True:
        v, r = divmod(v, base)
        s = _DIGITS[r] + s
        if v == 0:
            break
    if digits is not None:
        if len(s) > digits:
            raise ValueError(
                f"{fn}: {name} は {len(s)} 桁あり、digits={digits} に収まりません")
        s = s.rjust(digits, "0")
    if group:
        parts = []
        while len(s) > group:
            parts.insert(0, s[-group:])
            s = s[:-group]
        parts.insert(0, s)
        s = sep.join(parts)
    if neg:
        s = "-" + s
    elif signed is True:
        s = "+" + s
    return s


def _odo_split(fn, kw):
    """odometer の残りの引数を、text_transition へ渡す引数と書式に分ける"""
    passed = {k: kw.pop(k) for k in list(kw) if k in _ODO_PASS}
    return passed, kw


def _odo_run(fn, ta, tb, *, duration, carry, ripple, direction, kw, extra, is_digit):
    _require_choice(fn, "carry", carry, _CARRIES)
    _require_number(fn, "ripple", ripple, 0, 10)
    _require_number(fn, "duration", duration, 0, None)
    if duration <= 0:
        raise ValueError(f"{fn}: duration は 0 より大きくしてください: {duration!r}")
    passed, fmt = _odo_split(fn, dict(kw))
    anchor = passed.pop("anchor", "right")

    def timing(k, items, toks_a, toks_b, D):
        # 変わる桁を右から ripple 秒ずつ遅らせる（together は全部同時）
        def x_of(it):
            t = toks_b[it.b] if it.b is not None else toks_a[it.a]
            return (t.line, t.cx)
        movers = sorted([it for it in items if it.kind not in ("keep", "move")],
                        key=x_of, reverse=True)
        n = len(movers)
        rp = float(ripple) if carry == "ripple" else 0.0
        L = D - rp * (n - 1) if n > 1 else D
        if L < _ROLL_MIN_SEC - 1e-9:
            raise ValueError(
                f"{fn}: 1桁の回転が {L:.3f} 秒しかありません（{_ROLL_MIN_SEC} 秒以上）。"
                f"変わる桁が {n} 個、ripple={ripple} 秒の総時間 {rp * (n - 1):.3f} 秒が"
                f"duration={D:g} 秒に対して長すぎます。duration を延ばすか ripple を"
                f"縮めるか carry='together' にしてください")
        for r, it in enumerate(movers):
            it.t0, it.t1 = r * rp, r * rp + L
        # 幅の変わる桁（プロポーショナルの数字）に押されて滑る字（桁区切りなど）は、
        # anchor 側にある変わる桁が回っている間に滑る（先に滑って隣と重ならない）
        for it in items:
            if it.kind not in ("keep", "move"):
                continue
            x = x_of(it)
            side = [m for m in movers
                    if anchor == "center"
                    or (anchor == "right" and x_of(m) > x)
                    or (anchor == "left" and x_of(m) < x)]
            if side:
                it.t0, it.t1 = min(m.t0 for m in side), max(m.t1 for m in side)
            else:
                it.t0, it.t1 = 0.0, D

    def replace_fn(a, b, default):
        return "roll" if (is_digit(a) and is_digit(b)) else "fade"

    modes = {"leave": "roll", "enter": "roll", "replace_fn": replace_fn}
    ex = dict(extra)
    ex.update({"carry": carry, "ripple": float(ripple) if carry == "ripple" else 0.0})
    return _core(fn, [ta, tb], duration=duration, hold=passed.pop("hold", 0.0),
                 unit="char", match="position", prefer=("replace", "insert", "delete"),
                 move="slide", replace="roll", leave="fade", enter="fade", stagger=0.0,
                 easing=passed.pop("easing", "ease_in_out_cubic"), anchor=anchor,
                 roll_dir=direction, motion_blur=passed.pop("motion_blur", True), fmt=fmt,
                 timing=timing, modes=modes, extra=ex)


def odometer(from_, to, *, base=10, digits=None, signed=False, group=None, sep=None,
             duration=1.5, carry="ripple", ripple=0.04, roll_dir="auto", **fmt):
    """数字の桁が回る表示（走行距離計）。text_transition の糖衣（位置で対応・変わる桁は roll）。

    from_ / to: 整数。base: 2〜36（11 以上は A〜Z）。digits: 桁数（0 で埋める）。
    signed: False（負の数は '-'）/ True（正の数にも '+'）/ 'twos'（digits 桁の2の補数。
      2147483647 → 2147483648 は 0111… → 1000… と裏返る）。
    group / sep: group 桁ごとに sep を挟む（sep の既定は ','）。
      2進の数え盤: base=2, digits=32, signed='twos', group=8, sep=' '
      10進の桁区切り: group=3, sep=','
    duration: 秒。carry: 'ripple'（変わる桁を右から ripple 秒ずつ遅らせて回す。
      繰り上がりが走るのが見える）/ 'together'（全部同時）。1桁は 0.25 秒以上回す
      （ripple の総時間が長すぎると ValueError）。
    roll_dir: 'auto'（増えるなら上、減るなら下）/ 'up' / 'down'。
    fmt: text_image の書式と、hold・easing・anchor（既定 'right'）・motion_blur。
    odometer.text(from_text, to_text, **kw): 書式つきの文字列（日時など）の、数字の位置
      だけを回す（match='position'。数字でない字の入れ替えは fade）。roll_dir='auto' は
      数字の並びで比べる（巻き戻すと下）。
    戻り値は text_transition と同じ（obj.figure.pairs / schedule など）。
    """
    fn = "odometer"
    if isinstance(base, bool) or not isinstance(base, int) or not 2 <= base <= 36:
        raise ValueError(f"{fn}: base は 2〜36 の整数で指定してください: {base!r}")
    if digits is not None and (isinstance(digits, bool) or not isinstance(digits, int)
                               or not 1 <= digits <= 400):
        raise ValueError(f"{fn}: digits は 1〜400 の整数で指定してください: {digits!r}")
    if signed not in _SIGNED or isinstance(signed, int) and not isinstance(signed, bool):
        raise ValueError(f"{fn}: signed は False / True / 'twos' のいずれか: {signed!r}")
    if signed == "twos":
        if digits is None:
            raise ValueError(f"{fn}: signed='twos' には digits（桁数）が要ります")
        if base & (base - 1):
            raise ValueError(
                f"{fn}: signed='twos' は base が2の累乗（2・4・8・16・32）のときだけ使えます: "
                f"base={base}")
    if group is not None and (isinstance(group, bool) or not isinstance(group, int)
                              or group < 1):
        raise ValueError(f"{fn}: group は 1 以上の整数で指定してください: {group!r}")
    if sep is None:
        sep = ","
    if not isinstance(sep, str):
        raise TypeError(f"{fn}: sep は文字列で指定してください: {sep!r}")
    if group is None and sep != ",":
        raise ValueError(f"{fn}: sep は group と一緒に指定してください")
    _require_choice(fn, "roll_dir", roll_dir, _ROLL_DIRS)
    ta = _format_number(fn, "from_", from_, base, digits, signed, group, sep)
    tb = _format_number(fn, "to", to, base, digits, signed, group, sep)
    direction = roll_dir if roll_dir != "auto" else ("up" if to >= from_ else "down")
    digit_set = set(_DIGITS[:base]) | set(_DIGITS[:base].lower())

    def is_digit(s):
        return bool(s) and all(ch in digit_set for ch in s)

    extra = {"odometer": "number", "base": base, "digits": digits,
             "signed": signed, "group": group, "sep": sep}
    return _odo_run(fn, ta, tb, duration=duration, carry=carry, ripple=ripple,
                    direction=direction, kw=fmt, extra=extra, is_digit=is_digit)


def _odometer_text(from_text, to_text, *, duration=1.5, carry="ripple", ripple=0.04,
                   roll_dir="auto", **kw):
    """書式つきの文字列の、数字の位置だけを回す（odometer.text）"""
    fn = "odometer.text"
    for name, v in (("from_text", from_text), ("to_text", to_text)):
        if not isinstance(v, str) or not v:
            raise TypeError(f"{fn}: {name} は空でない文字列で指定してください: {v!r}")
    _require_choice(fn, "roll_dir", roll_dir, _ROLL_DIRS)
    direction = roll_dir
    if roll_dir == "auto":
        direction = "up" if _auto_roll_dir(from_text, to_text) > 0 else "down"

    def is_digit(s):
        return bool(s) and all(ch.isdigit() for ch in s)

    return _odo_run(fn, from_text, to_text, duration=duration, carry=carry, ripple=ripple,
                    direction=direction, kw=kw, extra={"odometer": "text"},
                    is_digit=is_digit)


odometer.text = _odometer_text
