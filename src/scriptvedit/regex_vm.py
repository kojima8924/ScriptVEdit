# -*- coding: utf-8 -*-
"""後戻り型の正規表現の照合を、1手ずつ記録する教育用のモデル（regex_trace / regex_count）。

regex_view（fx_regex.py）が「実際に記録した手順」から図を描くためのエンジン。
標準ライブラリだけで書いてあり、scriptvedit の他のモジュールにも依存しない（葉）。

**これは教育用のモデルで、実際の処理系の手順そのものではない。**
- search は開始位置 0, 1, …, len(text) を順に全部試す。Python の re（sre）や
  PCRE が持つ最適化（必須文字の先読み・先頭文字での読み飛ばし・自動所有化・
  文字1つの繰り返しを数えてから戻る REPEAT_ONE 等）はしない。
- だから画面に出る回数は「このモデルの回数」であって、処理系の手順数とは別物。
  動画では count_label に「模式」と分かる言葉を添えて使う。
- 一致の結果（span と groups）は re と同じになるよう作ってある
  （tests/test_regex_vm.py が re との差分ファジングで確かめる）。

仕組み（Pike VM ではなく、後戻り点のスタックを持つ素朴な後戻り型）:
- 式を命令の列にコンパイルする: CHAR / ANY / CLASS（1字の判定）・SPLIT（優先する枝,
  他方）・JMP・SAVE（捕獲）・QENTER / QEXIT（量指定子の取り分の記録）・UNTIL /
  UNTIL_MORE（中身が2字以上になりうる繰り返しの回数と空回りの検出）・ASSERT
  （^ $ \\A \\Z）・LOOK / LOOK_END / LOOK_FAIL（先読み・後読み）・ATOMIC_BEGIN /
  ATOMIC_END（後戻り点のスタックを印まで切る）・MATCH。
- **再帰は使わない**（再帰版は空白 1000 個で C のスタックが溢れた実測がある）。
  後戻り点は明示のスタック (pc, pos, undo の長さ, 部品番号) で持ち、捕獲・取り分・
  回数などのレジスタは undo ログ（(配列, 添字, 旧値) の列）で戻す。
  先読み・後読みも同じループの中で「印つきの後戻り点」として扱う（小さな VM を
  関数呼び出しで入れ子にしない）。
- 所有量指定子（*+ ++ ?+ {m,n}+）は「原子グループで包んだ貪欲」として扱う。
- 空の繰り返し: 本体が1字も進まなかった回で、そのループを抜ける
  （sre の MAX_UNTIL / MIN_UNTIL の last_ptr と同じ規則）。

1手の数え方（RegexTrace.counts / regex_count の count=）:
  tests      test（1字の判定）と assert（^ $ \\A \\Z・先読み・後読み・fullmatch の末尾検査）の回数
  matches    成功した1字の判定の回数
  backtracks 後戻り点へ戻った回数
  steps      実行した命令の数（先読み・後読みの中身も含む）
  attempts   試した開始位置の数
先読み・後読みは全体で assert 1回と数え、中身の判定は steps にだけ入る
（中身の event も記録しない）。

速さ（純 Python・Windows・Python 3.10 実測）: 記録なしの数え上げで約 3.5〜6.5 百万手/秒
（1字の判定 tests にして約 1.4〜2.6 百万回/秒）、記録つき（regex_trace）で約 2 百万手/秒。
"""

import gc as _gc
import hashlib as _hashlib
from array import array as _array
import unicodedata as _unicodedata
from collections import namedtuple
from fractions import Fraction

__all__ = ["regex_trace", "regex_count", "RegexTrace", "RegexCount",
           "RegexNode", "RegexQuant"]

# --- 公開する小さな型 -------------------------------------------------------

# 式の部品（tape の絵で1つの箱・括弧になる単位）。
#   kind: 'char' / 'any' / 'class' / 'assert' / 'open' / 'close' / 'look' / 'alt'
#         （'open' は ( (?: (?> の開き、'look' は (?= (?! (?<= (?<! の開き、
#          'close' はその閉じ、'alt' は |）
#   start, end: 式の文字列の中の範囲（量指定子が付いた部品は量指定子まで含む）
#   quant: その部品に付いた量指定子の番号（無ければ None。グループに付いた量指定子は
#          開きと閉じの両方に付く）
#   text: pattern[start:end]
RegexNode = namedtuple("RegexNode", "kind start end quant text")

# 量指定子。index は式の中に現れた順の番号（0 始まり）。mode は 'greedy' / 'lazy' /
# 'possessive'。max は上限が無ければ None。visible は先読み・後読みの中に無いこと
# （中のものは取り分を記録しない）。
RegexQuant = namedtuple("RegexQuant", "index start end mode min max visible")

# regex_count の戻り値。method は 'direct'（直接数えた）か 'poly'（多項式で外挿）。
# formula は当てた多項式（例 'n^2 + 2n + 3'。当てられなかった direct は None）。
# checked は式を数え上げで検算した n の組。
RegexCount = namedtuple("RegexCount", "value method formula checked")

# 照合の手順（event の列）の版。命令の組み方・event の記録の仕方を変えたら上げる
# （regex_view の鍵に入る。event そのものは鍵に入れないので、手順が変わったことを
# この版で伝える）。
_REGEX_VM_VER = "1"

_MODES = ("search", "match", "fullmatch")
_COUNT_KINDS = ("tests", "matches", "backtracks", "steps", "attempts")

# {m,n} の上限（これを超える数は対応しない）
_MAX_REPEAT = 1000
# コンパイル後の命令数の上限（{m,n} の展開で膨らみすぎるのを止める）
_MAX_PROGRAM = 200_000
# 式の入れ子の深さの上限（パーサは式の構造に沿って再帰する。文字列の長さには依らない）
_MAX_NESTING = 200


# --- 命令 -------------------------------------------------------------------
# 命令は (op, a, b, c, 部品番号) のタプル。

_CHAR = 0          # a=文字
_ANY = 1           # . （\n 以外）
_CLASS = 2         # a=述語 f(ch)->bool
_SPLIT = 3         # a=優先する枝の pc, b=他方の pc（後戻り点に積む）
_JMP = 4           # a=飛び先
_SAVE = 5          # a=捕獲のスロット
_QENTER = 6        # a=量指定子の番号, b=1 なら回数レジスタも初期化（UNTIL 型）
_QEXIT = 7         # a=量指定子の番号
_UNTIL = 8         # a=本体の pc, b=後続の pc, c=量指定子の番号
_UNTIL_MORE = 9    # 最小の繰り返しが「もう1回」を試す入口（後戻り点からだけ来る）
_ASSERT = 10       # a='^' / '$' / 'A' / 'Z'
_LOOK = 11         # a=look 番号, b=後読みの幅（先読みは -1）, c=否定なら 1
_LOOK_END = 12     # a=look 番号, c=否定なら 1（中身が一致した）
_LOOK_FAIL = 13    # a=続きの pc, b=look 番号, c=否定なら 1（中身が一致しなかった。印からだけ来る）
_ATOM_BEGIN = 14   # a=原子グループの番号
_ATOM_END = 15     # a=原子グループの番号
_MATCH = 16

# 後戻り点の「部品番号」に入れる印（先読み・後読みの境目。後戻りとして数えない）
_MARK = -2


# --- 文字の分類（Python の str パターンと同じ）--------------------------------

def _is_word(ch):
    return ch.isalnum() or ch == "_"


_CATEGORY_PREDS = {
    "d": str.isdecimal,
    "D": lambda ch: not ch.isdecimal(),
    "s": str.isspace,
    "S": lambda ch: not ch.isspace(),
    "w": _is_word,
    "W": lambda ch: not _is_word(ch),
}


def _make_class_pred(negated, chars, ranges, cats):
    """[...] の述語。chars は文字の集合、ranges は (lo, hi) の列、cats は分類の字の列。"""
    chars = frozenset(chars)
    ranges = tuple(ranges)
    preds = tuple(_CATEGORY_PREDS[c] for c in cats)

    if not ranges and not preds:
        if negated:
            return lambda ch: ch not in chars
        return chars.__contains__

    def pred(ch):
        hit = ch in chars
        if not hit:
            for lo, hi in ranges:
                if lo <= ch <= hi:
                    hit = True
                    break
        if not hit:
            for f in preds:
                if f(ch):
                    hit = True
                    break
        return hit != negated
    return pred


# --- パーサ -----------------------------------------------------------------

_SIMPLE_ESCAPES = {"t": "\t", "n": "\n", "r": "\r", "f": "\f", "v": "\v", "a": "\a"}
_OCT = "01234567"
_HEX = "0123456789abcdefABCDEF"


class _Parser:
    """式の文字列 → 構文木（タプル）。部品（RegexNode）と量指定子の表も作る。

    構文木のノード:
      ('char', 文字, 部品)            ('any', 部品)        ('class', 述語, 部品)
      ('assert', 種類, 部品)          ('seq', [ノード…])   ('alt', [枝…], 部品)
      ('group', 捕獲番号 or None, 子, 種類 'cap'/'noncap'/'atomic')
      ('look', 子, 後読みか, 否定か, look 番号, 部品)
      ('repeat', 子, min, max, mode, 量指定子の番号, 部品)
    """

    def __init__(self, pattern):
        self.p = pattern
        self.i = 0
        self.ngroups = 0
        self.nlook = 0
        self.natom = 0
        self.parts = []      # [kind, start, end, quant]
        self.quants = []     # [index, start, end, mode, min, max, visible]
        self.look_depth = 0
        self.depth = 0

    # --- エラー ---
    def unsupported(self, what, pos):
        raise ValueError(f"regex_trace が対応しない構文: {what}（位置 {pos}）")

    def error(self, what, pos):
        raise ValueError(f"regex_trace: 式の誤り: {what}（位置 {pos}）")

    def add_part(self, kind, start, end):
        self.parts.append([kind, start, end, None])
        return len(self.parts) - 1

    # --- 入口 ---
    def parse(self):
        node = self.parse_alt()
        if self.i < len(self.p):
            # parse_alt は ')' で止まる。対応する '(' が無い
            self.error("対応する '(' の無い ')'", self.i)
        return node

    def parse_alt(self):
        self.depth += 1
        if self.depth > _MAX_NESTING:
            self.unsupported(f"入れ子が深すぎる式（{_MAX_NESTING} 段まで）", self.i)
        branches = [self.parse_seq()]
        alt_part = None
        while self.i < len(self.p) and self.p[self.i] == "|":
            pi = self.add_part("alt", self.i, self.i + 1)
            if alt_part is None:
                alt_part = pi
            self.i += 1
            branches.append(self.parse_seq())
        self.depth -= 1
        if len(branches) == 1:
            return branches[0]
        return ("alt", branches, alt_part)

    def parse_seq(self):
        items = []
        p = self.p
        while self.i < len(p) and p[self.i] not in "|)":
            start = self.i
            atom = self.parse_atom()
            if atom is None:
                continue
            atom = self.parse_quantifier(atom, start)
            items.append(atom)
        if len(items) == 1:
            return items[0]
        return ("seq", items)

    # --- 量指定子 ---
    def try_brace(self, i):
        """i の '{' から {m} {m,} {,n} {m,n} {,} を読む。数量でなければ None。

        Python と同じく、数量の形でない '{' は文字として読む（戻り値 None）。
        """
        p = self.p
        j = i + 1
        lo = ""
        while j < len(p) and p[j].isdigit() and p[j] in "0123456789":
            lo += p[j]
            j += 1
        if j < len(p) and p[j] == ",":
            j += 1
            hi = ""
            while j < len(p) and p[j] in "0123456789":
                hi += p[j]
                j += 1
        else:
            hi = lo
        if j >= len(p) or p[j] != "}":
            return None
        if lo == "" and hi == "" and p[i + 1] == "}":
            return None   # '{}' は文字
        mn = int(lo) if lo else 0
        mx = int(hi) if hi else None
        return mn, mx, j + 1

    def peek_quantifier(self, i):
        """i に量指定子があれば (min, max, 次の位置) を返す。"""
        p = self.p
        if i >= len(p):
            return None
        c = p[i]
        if c == "*":
            return 0, None, i + 1
        if c == "+":
            return 1, None, i + 1
        if c == "?":
            return 0, 1, i + 1
        if c == "{":
            return self.try_brace(i)
        return None

    def parse_quantifier(self, atom, start):
        q = self.peek_quantifier(self.i)
        if q is None:
            return atom
        qpos = self.i
        kind = atom[0]
        if kind in ("assert", "look"):
            if kind == "assert":
                self.error("量指定子の前に繰り返せるものが無い", qpos)
            self.unsupported("先読み・後読みに付けた量指定子", qpos)
        mn, mx, j = q
        if mx is not None and mn > mx:
            self.error(f"{{{mn},{mx}}} の下限が上限より大きい", qpos)
        if mn > _MAX_REPEAT or (mx is not None and mx > _MAX_REPEAT):
            self.unsupported(f"{_MAX_REPEAT} を超える回数の指定", qpos)
        mode = "greedy"
        if j < len(self.p) and self.p[j] == "?":
            mode = "lazy"
            j += 1
        elif j < len(self.p) and self.p[j] == "+":
            mode = "possessive"
            j += 1
        if self.peek_quantifier(j) is not None:
            self.error("量指定子が重なっている（multiple repeat）", j)
        self.i = j
        qi = len(self.quants)
        self.quants.append([qi, start, j, mode, mn, mx, self.look_depth == 0])
        # 部品の範囲を量指定子まで伸ばし、量指定子の番号を付ける
        if kind == "group":
            close_pi, open_pi = atom[4], atom[5]
            self.parts[close_pi][2] = j
            self.parts[close_pi][3] = qi
            self.parts[open_pi][3] = qi
            pi = close_pi
        else:
            pi = atom[-1]
            self.parts[pi][2] = j
            self.parts[pi][3] = qi
        return ("repeat", atom, mn, mx, mode, qi, pi)

    # --- アトム ---
    def parse_atom(self):
        p = self.p
        i = self.i
        c = p[i]
        if c == "(":
            return self.parse_group()
        if c == "[":
            return self.parse_class()
        if c == ".":
            self.i += 1
            return ("any", self.add_part("any", i, i + 1))
        if c == "^":
            self.i += 1
            return ("assert", "^", self.add_part("assert", i, i + 1))
        if c == "$":
            self.i += 1
            return ("assert", "$", self.add_part("assert", i, i + 1))
        if c == "\\":
            return self.parse_escape_atom()
        if c in "*+?":
            self.error("量指定子の前に繰り返せるものが無い", i)
        if c == "{" and self.try_brace(i) is not None:
            self.error("量指定子の前に繰り返せるものが無い", i)
        self.i += 1
        return ("char", c, self.add_part("char", i, i + 1))

    def parse_group(self):
        p = self.p
        i = self.i
        if p.startswith("(?", i):
            rest = p[i + 2:i + 4]
            if rest[:1] == ":":
                kind, head = "noncap", "(?:"
            elif rest[:1] == ">":
                kind, head = "atomic", "(?>"
            elif rest[:1] == "=":
                return self.parse_look(i, "(?=", False, False)
            elif rest[:1] == "!":
                return self.parse_look(i, "(?!", False, True)
            elif rest == "<=":
                return self.parse_look(i, "(?<=", True, False)
            elif rest == "<!":
                return self.parse_look(i, "(?<!", True, True)
            elif rest[:1] == "P":
                if rest == "P=":
                    self.unsupported("後方参照 (?P=…)", i)
                self.unsupported("名前つきグループ (?P<…>…)", i)
            elif rest[:1] == "<":
                self.unsupported("名前つきグループ (?<…>…)", i)
            elif rest[:1] == "#":
                self.unsupported("注釈 (?#…)", i)
            elif rest[:1] == "(":
                self.unsupported("条件分岐 (?(…)…)", i)
            elif rest[:1] and rest[:1] in "aiLmsux-":
                self.unsupported("フラグ (?…)", i)
            else:
                self.error("'(?' の後が読めない", i)
        else:
            kind, head = "cap", "("
        open_pi = self.add_part("open", i, i + len(head))
        self.i = i + len(head)
        idx = None
        if kind == "cap":
            self.ngroups += 1
            idx = self.ngroups
        atom_id = None
        if kind == "atomic":
            atom_id = self.natom
            self.natom += 1
        child = self.parse_alt()
        if self.i >= len(p) or p[self.i] != ")":
            self.error("閉じていない '('", i)
        close_pi = self.add_part("close", self.i, self.i + 1)
        self.i += 1
        return ("group", idx, child, kind, close_pi, open_pi, atom_id)

    def parse_look(self, i, head, behind, negated):
        open_pi = self.add_part("look", i, i + len(head))
        self.i = i + len(head)
        li = self.nlook
        self.nlook += 1
        self.look_depth += 1
        child = self.parse_alt()
        self.look_depth -= 1
        if self.i >= len(self.p) or self.p[self.i] != ")":
            self.error("閉じていない '('", i)
        close_pi = self.add_part("close", self.i, self.i + 1)
        self.i += 1
        width = None
        if behind:
            lo, hi = _width(child)
            if lo != hi:
                self.unsupported("幅の決まらない後読み（後読みは固定幅だけ）", i)
            width = lo
        return ("look", child, behind, negated, li, open_pi, close_pi, width)

    # --- エスケープ ---
    def read_escape(self, in_class):
        """'\\' の後を読み、('char', 文字) / ('cat', 分類) / ('assert', 種類) を返す。"""
        p = self.p
        i = self.i
        if i + 1 >= len(p):
            self.error("式の末尾の '\\'", i)
        c = p[i + 1]
        self.i = i + 2
        if c in "dDsSwW":
            return ("cat", c)
        if c in _SIMPLE_ESCAPES:
            return ("char", _SIMPLE_ESCAPES[c])
        if c == "b":
            if in_class:
                return ("char", "\b")
            self.unsupported("\\b（単語の境目）", i)
        if c == "B":
            self.unsupported("\\B（単語の境目でない所）", i)
        if c in "AZ":
            if in_class:
                self.error(f"[…] の中の \\{c}", i)
            return ("assert", c)
        if c in "pP":
            self.unsupported(f"\\{c}（Unicode の属性）", i)
        if c == "x":
            return ("char", self.read_hex(i, 2))
        if c == "u":
            return ("char", self.read_hex(i, 4))
        if c == "U":
            ch = self.read_hex(i, 8)
            return ("char", ch)
        if c == "N":
            if not p.startswith("{", self.i):
                self.error("\\N の後に {名前} が無い", i)
            j = p.find("}", self.i)
            if j < 0:
                self.error("\\N{…} が閉じていない", i)
            name = p[self.i + 1:j]
            try:
                ch = _unicodedata.lookup(name)
            except KeyError:
                self.error(f"\\N{{{name}}} という名前の文字は無い", i)
            self.i = j + 1
            return ("char", ch)
        if c == "0":
            j = self.i
            digits = ""
            while j < len(p) and len(digits) < 2 and p[j] in _OCT:
                digits += p[j]
                j += 1
            self.i = j
            return ("char", chr(int("0" + digits, 8)))
        if c in "123456789":
            # 3桁の8進数（\101 = 'A'）だけは文字。ほかは後方参照
            if (c in _OCT and self.i + 1 < len(p) and p[self.i] in _OCT
                    and p[self.i + 1] in _OCT):
                v = int(c + p[self.i:self.i + 2], 8)
                if v > 0o377:
                    self.error("8進数のエスケープが 0o377 を超える", i)
                self.i += 2
                return ("char", chr(v))
            self.unsupported(f"後方参照 \\{c}", i)
        if c.isascii() and c.isalpha():
            self.error(f"未知のエスケープ \\{c}", i)
        return ("char", c)

    def read_hex(self, i, n):
        p = self.p
        h = p[self.i:self.i + n]
        if len(h) != n or any(ch not in _HEX for ch in h):
            self.error(f"\\{p[i + 1]} の後に16進数 {n} 桁が無い", i)
        self.i += n
        v = int(h, 16)
        if v > 0x10FFFF:
            self.error("Unicode の範囲を超える文字", i)
        return chr(v)

    def parse_escape_atom(self):
        i = self.i
        kind, v = self.read_escape(False)
        if kind == "char":
            return ("char", v, self.add_part("char", i, self.i))
        if kind == "cat":
            return ("class", _CATEGORY_PREDS[v], self.add_part("class", i, self.i))
        return ("assert", v, self.add_part("assert", i, self.i))

    # --- [...] ---
    def parse_class(self):
        p = self.p
        start = self.i
        self.i += 1
        negated = False
        if self.i < len(p) and p[self.i] == "^":
            negated = True
            self.i += 1
        chars, ranges, cats = set(), [], []
        first = True
        while True:
            if self.i >= len(p):
                self.error("閉じていない '['", start)
            c = p[self.i]
            if c == "]" and not first:
                self.i += 1
                break
            first = False
            item = self.read_class_item()
            if (self.i + 1 < len(p) and p[self.i] == "-" and p[self.i + 1] != "]"):
                dash = self.i
                self.i += 1
                end_item = self.read_class_item()
                if item[0] != "char" or end_item[0] != "char":
                    self.error("範囲の端に文字でないもの", dash)
                if item[1] > end_item[1]:
                    self.error("範囲の始まりが終わりより大きい", dash)
                ranges.append((item[1], end_item[1]))
                continue
            if item[0] == "char":
                chars.add(item[1])
            else:
                cats.append(item[1])
        pred = _make_class_pred(negated, chars, ranges, cats)
        return ("class", pred, self.add_part("class", start, self.i))

    def read_class_item(self):
        p = self.p
        if p[self.i] == "\\":
            kind, v = self.read_escape(True)
            if kind == "assert":
                self.error("[…] の中の位置の指定", self.i)
            return (kind, v)
        c = p[self.i]
        self.i += 1
        return ("char", c)


def _width(node):
    """構文木の (最小幅, 最大幅)。最大が無限なら None。"""
    k = node[0]
    if k in ("char", "any", "class"):
        return 1, 1
    if k in ("assert", "look"):
        return 0, 0
    if k == "seq":
        lo, hi = 0, 0
        for it in node[1]:
            a, b = _width(it)
            lo += a
            hi = None if (hi is None or b is None) else hi + b
        return lo, hi
    if k == "alt":
        ws = [_width(b) for b in node[1]]
        lo = min(w[0] for w in ws)
        his = [w[1] for w in ws]
        return lo, (None if any(h is None for h in his) else max(his))
    if k == "group":
        return _width(node[2])
    if k == "repeat":
        a, b = _width(node[1])
        mn, mx = node[2], node[3]
        lo = a * mn
        hi = None if (mx is None or b is None) else b * mx
        return lo, hi
    raise AssertionError(k)


def _bound_reach(node):
    """式の中の有限の回数・幅の最大（regex_count の当てはめの安全域）。

    量指定子の回数の下限・上限と、部品（並び・選択・グループ・繰り返し・先読み等の中身）が
    食う字数の下限・上限のうち有限のものの最大を返す（入れ子は _width が掛け合わせ、並びは
    足し合わせる。\\s{1,30}\\s{1,30} は 60）。取り分がこの数に届くと手数の増え方が
    変わりうる（\\s{1,100}$ は空白 100 個までは2次、それより先は1次）ので、regex_count は
    これより大きい n だけで多項式を当てる。繰り返しの上限が無い量指定子（* + {m,}）は
    下限だけが入る（{20,} は 20）。
    """
    best = 0
    stack = [node]
    while stack:
        nd = stack.pop()
        k = nd[0]
        if k in ("char", "any", "class", "assert"):
            continue
        if k == "look":
            child = nd[1]
            lo, hi = _width(child)
        else:
            lo, hi = _width(nd)
        best = max(best, lo, hi or 0)
        if k in ("seq", "alt"):
            stack.extend(nd[1])
        elif k == "group":
            stack.append(nd[2])
        elif k == "look":
            stack.append(nd[1])
        elif k == "repeat":
            best = max(best, nd[2], nd[3] or 0)
            stack.append(nd[1])
    return best


def _has_bounded_repeat(node):
    """回数の下限か上限が 2 以上の量指定子（{m,n} など）があるか（* + ? だけなら False）"""
    stack = [node]
    while stack:
        nd = stack.pop()
        k = nd[0]
        if k in ("seq", "alt"):
            stack.extend(nd[1])
        elif k == "group":
            stack.append(nd[2])
        elif k == "look":
            stack.append(nd[1])
        elif k == "repeat":
            if nd[2] >= 2 or (nd[3] is not None and nd[3] >= 2):
                return True
            stack.append(nd[1])
    return False


def _is_single_char(node):
    """中身が必ずちょうど1字を食う（捕獲を含まない）か。SPLIT の素朴なループで書ける。"""
    k = node[0]
    if k in ("char", "any", "class"):
        return True
    if k == "group" and node[3] == "noncap":
        return _is_single_char(node[2])
    return False


# --- コンパイラ ---------------------------------------------------------------

class _Compiler:
    def __init__(self, parser):
        self.prog = []
        self.parser = parser
        self.look_info = {}   # look 番号 -> (幅 or -1, 否定か)

    def emit(self, op, a=None, b=None, c=None, pi=None):
        self.prog.append((op, a, b, c, pi))
        if len(self.prog) > _MAX_PROGRAM:
            raise ValueError(
                f"regex_trace が対応しない構文: 繰り返しの展開が大きすぎる式"
                f"（命令が {_MAX_PROGRAM} を超える）（位置 0）")
        return len(self.prog) - 1

    def patch(self, at, **kw):
        op, a, b, c, pi = self.prog[at]
        self.prog[at] = (op, kw.get("a", a), kw.get("b", b), kw.get("c", c), pi)

    def comp(self, node):
        k = node[0]
        if k == "char":
            self.emit(_CHAR, node[1], pi=node[2])
        elif k == "any":
            self.emit(_ANY, pi=node[1])
        elif k == "class":
            self.emit(_CLASS, node[1], pi=node[2])
        elif k == "assert":
            self.emit(_ASSERT, {"A": "^"}.get(node[1], node[1]), pi=node[2])
        elif k == "seq":
            for it in node[1]:
                self.comp(it)
        elif k == "alt":
            self.comp_alt(node)
        elif k == "group":
            self.comp_group(node)
        elif k == "look":
            self.comp_look(node)
        elif k == "repeat":
            self.comp_repeat(node)
        else:
            raise AssertionError(k)

    def comp_alt(self, node):
        branches, pi = node[1], node[2]
        jumps = []
        for bi, br in enumerate(branches):
            if bi < len(branches) - 1:
                sp = self.emit(_SPLIT, pi=pi)
                self.patch(sp, a=len(self.prog))
                self.comp(br)
                jumps.append(self.emit(_JMP))
                self.patch(sp, b=len(self.prog))
            else:
                self.comp(br)
        for j in jumps:
            self.patch(j, a=len(self.prog))

    def comp_group(self, node):
        idx, child, kind = node[1], node[2], node[3]
        open_pi = node[5]
        if kind == "cap":
            self.emit(_SAVE, 2 * idx, pi=open_pi)
            self.comp(child)
            self.emit(_SAVE, 2 * idx + 1, pi=node[4])
        elif kind == "atomic":
            g = node[6]
            self.emit(_ATOM_BEGIN, g, pi=open_pi)
            self.comp(child)
            self.emit(_ATOM_END, g, pi=node[4])
        else:
            self.comp(child)

    def comp_look(self, node):
        child, behind, negated, li, open_pi, close_pi, width = node[1:]
        w = width if behind else -1
        neg = 1 if negated else 0
        self.emit(_LOOK, li, w, neg, pi=open_pi)
        self.comp(child)
        self.emit(_LOOK_END, li, None, neg, pi=open_pi)
        # LOOK_FAIL は印の後戻り点からだけ来る（通常の流れは飛び越える）
        jmp = self.emit(_JMP)
        fail_pc = self.emit(_LOOK_FAIL, None, li, neg, pi=open_pi)
        self.patch(jmp, a=len(self.prog))
        self.patch(fail_pc, a=len(self.prog))
        self.look_info[li] = fail_pc

    def comp_repeat(self, node):
        body, mn, mx, mode, q, pi = node[1:]
        if mode == "possessive":
            g = self.parser.natom
            self.parser.natom += 1
            self.emit(_ATOM_BEGIN, g, pi=pi)
            self._loop(body, mn, mx, False, q, pi)
            self.emit(_ATOM_END, g, pi=pi)
        else:
            self._loop(body, mn, mx, mode == "lazy", q, pi)

    def _loop(self, body, mn, mx, lazy, q, pi):
        if _is_single_char(body):
            # 1字ずつ食う中身: SPLIT の素朴なループ（空回りは起きない）
            self.emit(_QENTER, q, 0, pi=pi)
            for _ in range(mn):
                self.comp(body)
            exits = []
            if mx is None:
                top = self.emit(_SPLIT, pi=pi)
                self.comp(body)
                self.emit(_JMP, top)
                exits.append(top)
            else:
                for _ in range(mx - mn):
                    sp = self.emit(_SPLIT, pi=pi)
                    exits.append(sp)
                    self.comp(body)
            exit_pc = len(self.prog)
            for sp in exits:
                nxt = sp + 1
                if lazy:
                    self.patch(sp, a=exit_pc, b=nxt)
                else:
                    self.patch(sp, a=nxt, b=exit_pc)
            self.emit(_QEXIT, q, pi=pi)
            return
        # 一般の中身: sre の REPEAT / MAX_UNTIL / MIN_UNTIL と同じ規則
        self.emit(_QENTER, q, 1, pi=pi)
        jmp = self.emit(_JMP, pi=pi)
        body_pc = len(self.prog)
        self.comp(body)
        until = self.emit(_UNTIL, body_pc, None, q, pi=pi)
        self.emit(_UNTIL_MORE, body_pc, None, q, pi=pi)
        tail = len(self.prog)
        self.patch(until, b=tail)
        self.patch(until + 1, b=tail)
        self.patch(jmp, a=until)
        self.emit(_QEXIT, q, pi=pi)


class _Program:
    """コンパイル済みの式（同じ式は使い回す）。"""

    def __init__(self, pattern):
        if not isinstance(pattern, str):
            raise TypeError(f"regex_trace: pattern は文字列で指定してください: {pattern!r}")
        parser = _Parser(pattern)
        ast = parser.parse()
        comp = _Compiler(parser)
        comp.comp(ast)
        comp.emit(_MATCH)
        self.pattern = pattern
        self.prog = comp.prog
        self.ngroups = parser.ngroups
        self.nlook = parser.nlook
        self.natom = parser.natom
        self.look_fail_pc = [comp.look_info[i] for i in range(parser.nlook)]
        self.nodes = [RegexNode(k, s, e, q, pattern[s:e]) for k, s, e, q in parser.parts]
        self.quants = [RegexQuant(i, s, e, m, mn, mx, vis)
                       for i, s, e, m, mn, mx, vis in parser.quants]
        self.qmin = [q.min for q in self.quants]
        self.qmax = [q.max for q in self.quants]
        self.qlazy = [q.mode == "lazy" for q in self.quants]
        self.visible_qs = tuple(q.index for q in self.quants if q.visible)
        # regex_count の当てはめ: 有限の回数・幅の最大と、{m,n} 型の量指定子の有無
        self.reach = _bound_reach(ast)
        self.bounded = _has_bounded_repeat(ast)


_PROGRAM_MEMO = {}


def _node_chars(pattern):
    """'char' の部品の添字 → その部品が表す1字（エスケープを解いたもの）。

    regex_view の beats='literal:<字>' が「その字のリテラルの判定」を見分けるのに使う。
    """
    cp = _compile(pattern)
    return {ins[4]: ins[1] for ins in cp.prog if ins[0] == _CHAR}


def _compile(pattern):
    prog = _PROGRAM_MEMO.get(pattern)
    if prog is None:
        prog = _Program(pattern)
        if len(_PROGRAM_MEMO) > 256:
            _PROGRAM_MEMO.clear()
        _PROGRAM_MEMO[pattern] = prog
    return prog


# --- VM ---------------------------------------------------------------------

class _StepLimit(Exception):
    """手数の上限を超えた（数え上げの打ち切り）。"""


def _run(cp, text, mode, limit, record):
    """照合を実行する。record=True なら event を記録する。

    戻り値: (span, caps, counts, events, attempts, event_steps)。手数が limit を超えたら
    _StepLimit。
    """
    prog = cp.prog
    n = len(text)
    nslots = 2 * (cp.ngroups + 1)
    nq = len(cp.quants)
    qmin, qmax, qlazy = cp.qmin, cp.qmax, cp.qlazy
    vis = cp.visible_qs
    look_fail_pc = cp.look_fail_pc
    fullmatch = mode == "fullmatch"
    starts = range(n + 1) if mode == "search" else range(1)

    steps = tests = matches = backtracks = attempts_n = 0
    events = [] if record else None
    ev = events.append if record else None
    # 各 event の時点までに実行した命令の数（count='steps' のカウンタ用。event と同じ添字）
    esteps = _array("Q") if record else None
    est = esteps.append if record else None
    attempts = []
    result_span = None
    result_caps = None

    for s in starts:
        attempts_n += 1
        caps = [None] * nslots
        qstart = [None] * nq
        qend = [None] * nq
        cnt = [0] * nq
        last = [None] * nq
        atom_h = [0] * cp.natom
        look_h = [0] * cp.nlook
        look_pos = [0] * cp.nlook
        sil = [0]          # 先読み・後読みの中にいる深さ（undo で戻る）
        stack = []
        undo = []
        push = stack.append
        log = undo.append
        pc = 0
        pos = s
        first_ev = len(events) if record else 0
        if record:
            est(steps)
            ev(("start", s, None, True, ()))
        matched = False

        while True:
            steps += 1
            if steps > limit:
                raise _StepLimit()
            op, a, b, c, pi = prog[pc]

            # --- 1字の判定 ---
            if op <= _CLASS:
                if pos < n:
                    ch = text[pos]
                    if op == _CHAR:
                        ok = ch == a
                    elif op == _ANY:
                        ok = ch != "\n"
                    else:
                        ok = a(ch)
                else:
                    ok = False
                if not sil[0]:
                    tests += 1
                    if record:
                        sp = tuple((q, qstart[q], pos if qend[q] is None else qend[q])
                                   for q in vis if qstart[q] is not None)
                        est(steps)
                        ev(("test", pos, pi, ok, sp))
                    if ok:
                        matches += 1
                if ok:
                    pos += 1
                    pc += 1
                    continue
            elif op == _SPLIT:
                push((b, pos, len(undo), pi))
                pc = a
                continue
            elif op == _JMP:
                pc = a
                continue
            elif op == _UNTIL:
                count = cnt[c] + 1
                if count < qmin[c]:
                    log((cnt, c, cnt[c]))
                    cnt[c] = count
                    pc = a
                    continue
                if qlazy[c]:
                    # 最小: 先に後続を試す。だめなら UNTIL_MORE で「もう1回」
                    push((pc + 1, pos, len(undo), pi))
                    pc = b
                    continue
                mx = qmax[c]
                if (mx is None or count < mx) and pos != last[c]:
                    push((b, pos, len(undo), pi))
                    log((cnt, c, cnt[c]))
                    cnt[c] = count
                    log((last, c, last[c]))
                    last[c] = pos
                    pc = a
                    continue
                pc = b
                continue
            elif op == _UNTIL_MORE:
                count = cnt[c] + 1
                mx = qmax[c]
                if not ((mx is not None and count >= mx) or pos == last[c]):
                    log((cnt, c, cnt[c]))
                    cnt[c] = count
                    log((last, c, last[c]))
                    last[c] = pos
                    pc = a
                    continue
            elif op == _QENTER:
                log((qstart, a, qstart[a]))
                qstart[a] = pos
                log((qend, a, qend[a]))
                qend[a] = None
                if b:
                    log((cnt, a, cnt[a]))
                    cnt[a] = -1
                    log((last, a, last[a]))
                    last[a] = None
                pc += 1
                continue
            elif op == _QEXIT:
                log((qend, a, qend[a]))
                qend[a] = pos
                pc += 1
                continue
            elif op == _SAVE:
                log((caps, a, caps[a]))
                caps[a] = pos
                pc += 1
                continue
            elif op == _ASSERT:
                if a == "^":
                    ok = pos == 0
                elif a == "$":
                    ok = pos == n or (pos == n - 1 and text[pos] == "\n")
                else:   # 'Z'
                    ok = pos == n
                if not sil[0]:
                    tests += 1
                    if record:
                        sp = tuple((q, qstart[q], pos if qend[q] is None else qend[q])
                                   for q in vis if qstart[q] is not None)
                        est(steps)
                        ev(("assert", pos, pi, ok, sp))
                if ok:
                    pc += 1
                    continue
            elif op == _ATOM_BEGIN:
                log((atom_h, a, atom_h[a]))
                atom_h[a] = len(stack)
                pc += 1
                continue
            elif op == _ATOM_END:
                del stack[atom_h[a]:]
                pc += 1
                continue
            elif op == _LOOK:
                push((look_fail_pc[a], pos, len(undo), _MARK))
                log((look_h, a, look_h[a]))
                look_h[a] = len(stack) - 1
                log((look_pos, a, look_pos[a]))
                look_pos[a] = pos
                log((sil, 0, sil[0]))
                sil[0] += 1
                if b < 0:
                    pc += 1
                    continue
                if pos >= b:
                    pos -= b
                    pc += 1
                    continue
                # 後読みの幅だけ戻れない → 中身は一致しない（印の後戻り点へ）
            elif op == _LOOK_END:
                h = look_h[a]
                mark = stack[h][2]
                del stack[h:]
                pos = look_pos[a]
                if not c:
                    log((sil, 0, sil[0]))
                    sil[0] -= 1
                    if not sil[0]:
                        tests += 1
                        if record:
                            sp = tuple((q, qstart[q], pos if qend[q] is None else qend[q])
                                       for q in vis if qstart[q] is not None)
                            est(steps)
                            ev(("assert", pos, pi, True, sp))
                    pc += 1
                    continue
                # 否定の先読み・後読みの中身が一致した → 失敗。中の捕獲は捨てる
                while len(undo) > mark:
                    arr, i, old = undo.pop()
                    arr[i] = old
                if not sil[0]:
                    tests += 1
                    if record:
                        sp = tuple((q, qstart[q], pos if qend[q] is None else qend[q])
                                   for q in vis if qstart[q] is not None)
                        est(steps)
                        ev(("assert", pos, pi, False, sp))
            elif op == _LOOK_FAIL:
                # 中身が一致しなかった（印の後戻り点から来た。undo は印の時点まで戻っている）
                if not sil[0]:
                    tests += 1
                    if record:
                        sp = tuple((q, qstart[q], pos if qend[q] is None else qend[q])
                                   for q in vis if qstart[q] is not None)
                        est(steps)
                        ev(("assert", pos, pi, bool(c), sp))
                if c:
                    pc = a
                    continue
            elif op == _MATCH:
                if fullmatch and pos != n:
                    tests += 1
                    if record:
                        sp = tuple((q, qstart[q], pos if qend[q] is None else qend[q])
                                   for q in vis if qstart[q] is not None)
                        est(steps)
                        ev(("assert", pos, None, False, sp))
                else:
                    if fullmatch:
                        tests += 1
                        if record:
                            sp = tuple((q, qstart[q], pos if qend[q] is None else qend[q])
                                       for q in vis if qstart[q] is not None)
                            est(steps)
                            ev(("assert", pos, None, True, sp))
                    matched = True
                    break
            else:
                raise AssertionError(op)

            # --- 失敗: 後戻り点へ戻る ---
            if not stack:
                break
            pc, pos, mark, bpi = stack.pop()
            while len(undo) > mark:
                arr, i, old = undo.pop()
                arr[i] = old
            if bpi != _MARK and not sil[0]:
                backtracks += 1
                if record:
                    sp = tuple((q, qstart[q], pos if qend[q] is None else qend[q])
                               for q in vis if qstart[q] is not None)
                    est(steps)
                    ev(("backtrack", pos, bpi, True, sp))

        if matched:
            caps[0] = s
            caps[1] = pos
            result_span = (s, pos)
            result_caps = caps
            if record:
                sp = tuple((q, qstart[q], pos if qend[q] is None else qend[q])
                           for q in vis if qstart[q] is not None)
                est(steps)
                ev(("match", pos, None, True, sp))
                attempts.append((s, first_ev, len(events) - 1, "match"))
            break
        if record:
            est(steps)
            ev(("fail", s, None, False, ()))
            attempts.append((s, first_ev, len(events) - 1, "fail"))

    counts = {"steps": steps, "tests": tests, "matches": matches,
              "backtracks": backtracks, "attempts": attempts_n}
    return result_span, result_caps, counts, events, attempts, esteps



# --- regex_trace ------------------------------------------------------------

class RegexTrace:
    """regex_trace の戻り値（記録した照合の手順）。

    属性:
      pattern / text / mode
      span        一致した範囲 (開始, 終了)。re と同じ。一致しなければ None
      groups      捕獲グループの文字列のタプル（re の m.groups() と同じ。参加しなかった
                  グループは None）。一致しなければ ()
      counts      {'steps', 'tests', 'matches', 'backtracks', 'attempts'} の dict
      nodes       式の部品 RegexNode(kind, start, end, quant, text) のリスト
      quantifiers 量指定子 RegexQuant(index, start, end, mode, min, max, visible) のリスト
      events      (kind, pos, node, ok, spans) のリスト
                    kind: 'start' / 'test' / 'assert' / 'backtrack' / 'match' / 'fail'
                    pos: test は判定した字の位置、assert は境目、backtrack は戻った位置、
                         start / fail は開始位置、match は一致の終わり
                    node: nodes の添字（start / match / fail と fullmatch の末尾検査は None）
                    ok: 判定の成否（start / backtrack / match は True、fail は False）
                    spans: その時点で有効な量指定子の取り分 ((番号, 開始, 終了), …)。
                           繰り返しの途中なら終了は今の位置
      attempts    (開始位置, 最初の event の添字, 最後の event の添字, 'match' / 'fail')
                  のリスト
      event_steps 各 event の時点までに実行した命令の数（events と同じ添字の array）
    """

    __slots__ = ("pattern", "text", "mode", "span", "groups", "counts", "nodes",
                 "quantifiers", "events", "event_steps", "attempts", "max_steps")

    def __repr__(self):
        return (f"<RegexTrace {self.pattern!r} × {len(self.text)}字 mode={self.mode} "
                f"span={self.span} tests={self.counts['tests']} "
                f"events={len(self.events)}>")


def _check_mode(fn, mode):
    if mode not in _MODES:
        raise ValueError(f"{fn}: mode は {_MODES} のいずれか: {mode!r}")


def regex_trace(pattern, text, *, mode="search", max_steps=200_000):
    """後戻り型の照合を1手ずつ記録する（regex_view で図にする元データ）。

    pattern: 正規表現（Python の str パターンの部分集合）。対応しない構文は ValueError で、
      黙って違う動きはしない。対応する構文:
        リテラルとエスケープ（\\t \\n \\xhh \\uXXXX \\N{名前} 等）/ .（\\n 以外）/
        \\s \\S \\d \\D \\w \\W（\\s は str.isspace、\\d は isdecimal、\\w は isalnum か '_'）/
        [...]（範囲・否定・中の \\s など）/ * + ? {m} {m,} {,n} {m,n}（n は 1000 まで）と
        その最小（*? 等）・所有（*+ ++ ?+ {m,n}+）/ ( ) (?: ) (?> ) | /
        ^ $（MULTILINE なし。$ は末尾か、末尾の \\n の直前）\\A \\Z /
        先読み (?= ) (?! )、後読み (?<= ) (?<! )（後読みは固定幅だけ）。
        { の後が数量でなければ文字として読む（Python と同じ）。
      対応しない: 後方参照・名前つきグループ・フラグ（(?i) 等）・\\b \\B・条件分岐・\\p・
        注釈 (?#…)・先読み/後読みに付けた量指定子。
    text: 照合する文字列
    mode: 'search'（開始位置 0..len(text) を順に試す）/ 'match' / 'fullmatch'
    max_steps: 記録する手数（命令の数）の上限。超えたら RuntimeError
      （数だけが要るときは regex_count を使う）。

    これは教育用のモデル（モジュールの docstring）。span と groups は re と同じになる。
    重さ: 記録つきで約 2 百万手/秒（x＋空白 1000 個＋x に \\s+$ は 250 万手・150 万 event で
    約 1.2 秒。既定の max_steps=200,000 なら 0.1 秒）。
    """
    _check_mode("regex_trace", mode)
    if not isinstance(text, str):
        raise TypeError(f"regex_trace: text は文字列で指定してください: {text!r}")
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
        raise ValueError(f"regex_trace: max_steps は 1 以上の整数で指定してください: {max_steps!r}")
    cp = _compile(pattern)
    # event のタプルを大量に作るので、記録の間は循環 GC を止める（event は循環しない。
    # 止めないと世代0の回収が繰り返し走り、600 万 event で3倍遅くなった実測）
    gc_was_enabled = _gc.isenabled()
    _gc.disable()
    try:
        span, caps, counts, events, attempts, esteps = _run(cp, text, mode, max_steps, True)
    except _StepLimit:
        raise RuntimeError(
            f"regex_trace: 手数が max_steps（{max_steps:,}）を超えました"
            f"（{pattern!r} × {len(text)} 字）。記録して図にするには文字列を短くするか "
            f"max_steps を上げてください。回数だけが要るときは regex_count で数だけ出してください"
        ) from None
    finally:
        if gc_was_enabled:
            _gc.enable()
    tr = RegexTrace()
    tr.pattern = pattern
    tr.text = text
    tr.mode = mode
    tr.max_steps = max_steps
    tr.span = span
    if span is None:
        tr.groups = ()
    else:
        gs = []
        for g in range(1, cp.ngroups + 1):
            a, b = caps[2 * g], caps[2 * g + 1]
            gs.append(None if a is None or b is None else text[a:b])
        tr.groups = tuple(gs)
    tr.counts = counts
    tr.nodes = list(cp.nodes)
    tr.quantifiers = list(cp.quants)
    tr.events = events
    tr.event_steps = esteps
    tr.attempts = attempts
    return tr


# --- regex_count ------------------------------------------------------------

_DEFAULT_FIT_NS = (8, 12, 16, 24, 32, 48, 64)
_MAX_DEGREE = 4
# 当てはめ用の小さい n を数えるときの手数の上限（direct_limit がこれより大きければそちら）。
# 指数型の式（^(a+)+$）はここで打ち切って ValueError にする（純 Python で約1秒）
_FIT_STEP_LIMIT = 3_000_000

# 構築時の計算を1回にするメモ（レイヤーは1回のレンダで2回実行される）
_COUNT_MEMO = {}


def _count_once(cp, text, mode, kind, limit):
    """記録なしで数える。(値, 手数)。limit を超えたら None。"""
    try:
        counts = _run(cp, text, mode, limit, False)[2]
    except _StepLimit:
        return None
    return counts[kind], counts["steps"]


def _solve_poly(points):
    """点の列 [(x, y)] を通る多項式の係数（Fraction。添字が次数）。"""
    m = len(points)
    rows = [[Fraction(x) ** j for j in range(m)] + [Fraction(y)] for x, y in points]
    for col in range(m):
        piv = next(r for r in range(col, m) if rows[r][col] != 0)
        rows[col], rows[piv] = rows[piv], rows[col]
        pv = rows[col][col]
        rows[col] = [v / pv for v in rows[col]]
        for r in range(m):
            if r != col and rows[r][col] != 0:
                f = rows[r][col]
                rows[r] = [v - f * w for v, w in zip(rows[r], rows[col])]
    coeffs = [rows[i][m] for i in range(m)]
    while len(coeffs) > 1 and coeffs[-1] == 0:
        coeffs.pop()
    return coeffs


def _poly_eval(coeffs, x):
    v = Fraction(0)
    for c in reversed(coeffs):
        v = v * x + c
    return v


def _poly_text(coeffs):
    """係数 → 'n^2 + 2n + 3' / '(n^2 + n)/2' の形。"""
    den = 1
    for c in coeffs:
        den = den * c.denominator // _gcd(den, c.denominator)
    ints = [int(c * den) for c in coeffs]
    terms = []
    for d in range(len(ints) - 1, -1, -1):
        v = ints[d]
        if v == 0:
            continue
        mono = "" if d == 0 else ("n" if d == 1 else f"n^{d}")
        mag = abs(v)
        body = (str(mag) if (mag != 1 or d == 0) else "") + mono
        terms.append(("-" if v < 0 else "+", body))
    if not terms:
        return "0"
    text = ("-" if terms[0][0] == "-" else "") + terms[0][1]
    for sign, body in terms[1:]:
        text += f" {sign} {body}"
    if den != 1:
        text = f"({text})/{den}" if len(terms) > 1 else f"{text}/{den}"
    return text


def _gcd(a, b):
    while b:
        a, b = b, a % b
    return a


def _fit(samples):
    """[(n, 値)] から次数1〜4の多項式を当てる。(係数, 検算した n) か None。

    次数 d は、検算用の最後の2点の直前の d+1 点で補間し、最後の2点で検算する
    （小さい n の端の効果を避けるため、当てには大きい側の点を使う）。
    """
    if len(samples) < 4:
        return None
    check = samples[-2:]
    base = samples[:-2]
    for d in range(1, _MAX_DEGREE + 1):
        if len(base) < d + 1:
            break
        coeffs = _solve_poly(base[-(d + 1):])
        if all(_poly_eval(coeffs, x) == y for x, y in check):
            return coeffs, tuple(x for x, _ in check)
    return None


def regex_count(pattern, make_text, n, *, count="tests", mode="search",
                direct_limit=3_000_000, fit_ns=None):
    """長い文字列での回数（記録なし）を数える。大きすぎれば多項式で外挿する。

    pattern: regex_trace と同じ式
    make_text: make_text(n) → 文字列（例 lambda n: 'x' + ' ' * n + 'x'）
    n: 数えたい大きさ
    count: 'tests'（既定）/ 'matches' / 'backtracks' / 'steps' / 'attempts'
      （意味は regex_vm のモジュール docstring）
    mode: regex_trace と同じ
    direct_limit: n を直接数えてよい手数の上限（既定 300 万手 ≒ 純 Python で 1 秒）。
      当てはめ用の小さい n は max(direct_limit, 300 万手) まで数える
    fit_ns: 外挿に使う小さい n の列（既定 (8, 12, 16, 24, 32, 48, 64)。4点以上・昇順）

    手順: n が fit_ns の最大以下なら直接数える。それより大きいときは、fit_ns の各点を
    数えて次数1〜4の多項式を Fraction の補間で当て、当てに使っていない最後の2点で
    検算する。手数の多項式から n での手数を見積もり、direct_limit 以内なら直接数える
    （method='direct'）。超えるなら多項式で外挿する（method='poly'）。
    検算が外れたら（^(a+)+$ のような指数型）ValueError。

    上限のある量指定子（\\s{1,100}$ など）: 取り分が上限に届くと回数の増え方が変わる
    （\\s{1,100}$ は空白 100 個までは2次、それより先は1次）。当てはめる n が上限より
    小さいと、2次の式が検算を通って外挿だけが 3 倍ずれる。そこで式の中の有限の回数・幅の
    最大（入れ子は掛け合わせる）が fit_ns の最小以上なら、既定の fit_ns を「最大 + 8, 12, …」へ
    ずらす（fit_ns を明示していれば ValueError）。さらに {m,n} 型の量指定子がある式は、
    fit_ns の最大の2倍の n でも数えて検算する（make_text が n より短い連なりを作る場合の
    取りこぼしを拾う。手数が当てはめの上限を超えるときは省く）。外れたら ValueError。

    戻り値 RegexCount(value, method, formula, checked)。
      formula: 当てた式（例 'n^2 + 2n + 3'）。direct で式が当たらなかったときは None
      checked: 式を数え上げで確かめた n の組
    計算は同じ引数（式・mode・count・n・各 n の文字列）なら1プロセスで1回だけ。
    """
    fn = "regex_count"
    _check_mode(fn, mode)
    if count not in _COUNT_KINDS:
        raise ValueError(f"{fn}: count は {_COUNT_KINDS} のいずれか: {count!r}")
    if not callable(make_text):
        raise TypeError(f"{fn}: make_text は make_text(n) → 文字列 の関数で指定してください")
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ValueError(f"{fn}: n は 0 以上の整数で指定してください: {n!r}")
    if (isinstance(direct_limit, bool) or not isinstance(direct_limit, int)
            or direct_limit < 1):
        raise ValueError(f"{fn}: direct_limit は 1 以上の整数で指定してください: {direct_limit!r}")
    ns = tuple(_DEFAULT_FIT_NS if fit_ns is None else fit_ns)
    if (len(ns) < 4 or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in ns)
            or list(ns) != sorted(set(ns))):
        raise ValueError(
            f"{fn}: fit_ns は 0 以上の整数を昇順に4つ以上並べてください: {fit_ns!r}")
    cp = _compile(pattern)
    # 上限のある量指定子: 当てはめの n を上限より先（増え方の変わった後）へずらす
    if cp.reach >= ns[0]:
        if fit_ns is not None:
            raise ValueError(
                f"{fn}: 式 {pattern!r} に回数・幅の上限 {cp.reach} の量指定子があり、取り分が "
                f"{cp.reach} に届くと回数の増え方が変わります。fit_ns は {cp.reach} より大きい "
                f"n だけで並べてください（省くと自動でずらします）: {fit_ns!r}")
        ns = tuple(cp.reach + k for k in _DEFAULT_FIT_NS)

    def text_of(k):
        t = make_text(k)
        if not isinstance(t, str):
            raise TypeError(f"{fn}: make_text({k}) が文字列を返しませんでした: {type(t)}")
        return t

    target = text_of(n)
    small = n <= ns[-1]
    fit_texts = [] if small else [text_of(k) for k in ns]
    # {m,n} 型の量指定子がある式は、当てはめの外の大きな n でも検算する
    far = 2 * ns[-1] if (cp.bounded and not small and n > 2 * ns[-1]) else None
    far_text = text_of(far) if far is not None else None
    h = _hashlib.sha256()
    for t in [target] + fit_texts + ([far_text] if far is not None else []):
        h.update(t.encode("utf-8", "surrogatepass"))
        h.update(b"\x00\x01")
    memo_key = (pattern, mode, count, n, direct_limit, ns, far, h.hexdigest())
    hit = _COUNT_MEMO.get(memo_key)
    if hit is not None:
        return hit

    if small:
        got = _count_once(cp, target, mode, count, direct_limit)
        if got is None:
            raise ValueError(
                f"{fn}: n={n} でも手数が direct_limit（{direct_limit:,}）を超えました"
                f"（{pattern!r}）。手数が指数的に増える式の可能性があります")
        res = RegexCount(got[0], "direct", None, (n,))
        _COUNT_MEMO[memo_key] = res
        return res

    vals, steps = [], []
    fit_limit = max(direct_limit, _FIT_STEP_LIMIT)
    for k, t in zip(ns, fit_texts):
        got = _count_once(cp, t, mode, count, fit_limit)
        if got is None:
            raise ValueError(
                f"{fn}: 当てはめ用の n={k} で手数が {fit_limit:,} を超えました"
                f"（{pattern!r}）。多項式に乗らない（指数的に増える）式です。"
                f"回数を外挿できません")
        vals.append((k, got[0]))
        steps.append((k, got[1]))
    fit = _fit(vals)
    if fit is None:
        raise ValueError(
            f"{fn}: 回数が次数4以下の多項式に乗りません（{pattern!r}、"
            f"n={', '.join(str(k) for k in ns)} で数えた値 "
            f"{', '.join(str(v) for _, v in vals)}）。指数的に増える式の可能性があります")
    coeffs, checked = fit
    formula = _poly_text(coeffs)
    value = _poly_eval(coeffs, n)
    if value.denominator != 1 or value < 0:
        raise ValueError(
            f"{fn}: 外挿した値が 0 以上の整数になりません（{formula} → {value}）")
    step_fit = _fit(steps)
    if far is not None and step_fit is not None and _poly_eval(step_fit[0], far) <= fit_limit:
        got = _count_once(cp, far_text, mode, count, fit_limit)
        if got is not None:
            want = _poly_eval(coeffs, far)
            if got[0] != want:
                raise ValueError(
                    f"{fn}: n={far} の数え上げ（{got[0]:,}）が当てた式 {formula}（{want}）と"
                    f"合いません（{pattern!r}）。式に上限のある量指定子があり、取り分が上限に"
                    f"届くと回数の増え方が変わります。fit_ns に、取り分が上限を十分超える大きさの "
                    f"n を並べてください")
            checked = checked + (far,)
    if step_fit is not None and _poly_eval(step_fit[0], n) <= direct_limit:
        got = _count_once(cp, target, mode, count, direct_limit)
        if got is not None:
            ok = got[0] == value
            res = RegexCount(got[0], "direct", formula if ok else None,
                             checked + (n,) if ok else checked)
            _COUNT_MEMO[memo_key] = res
            return res
    res = RegexCount(int(value), "poly", formula, checked)
    _COUNT_MEMO[memo_key] = res
    return res
