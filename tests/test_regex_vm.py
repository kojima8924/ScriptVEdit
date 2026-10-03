# -*- coding: utf-8 -*-
"""regex_trace / regex_count（src/scriptvedit/regex_vm.py）の回帰テスト。

確かめること:
  (a) re との差分ファジング（span・groups が re.search / match / fullmatch と一致）
  (b) 回数の等式（\\s+$ の n²+2n+3・n(n+1)/2、.*(?:.*=.*) の 56 回と (s, a, b) の列）
  (c) 空白 50,000 個でも RecursionError にならない（再帰を使わない）
  (d) regex_count（多項式の当てはめ・外挿・指数型の拒否・上限のある量指定子・メモ）
  (e) 対応しない構文の ValueError
  (f) event の形（種類・部品の番号・取り分）

ファジングの文法から外したもの（sre の既知の癖。手書きの表で確かめる）:

  | 形                                   | Python | sre の結果                         | このモデル（正）              |
  |--------------------------------------|--------|------------------------------------|-------------------------------|
  | 否定の先読み・後読みの中の捕獲        | 3.10   | 失敗した中身の捕獲が漏れる         | 捨てる（None）                |
  |   (?!(.){2,3}) × '=a1 =…'            |        | ('',)                              | (None,)                       |
  | 最小の繰り返しが繰り返す捕獲グループ  | 3.10   | 試して失敗した回の開始位置が残る   | 戻す                          |
  |   ((?:\\s){1,}){1,}?(?<![^a].) × '1=\\n a' |  | ('',)                              | ('\\n',)                      |
  | 所有量指定子・原子グループ             | 3.10   | 構文が無い（re.error）             | 対応する（表で確かめる）       |
  | グループに付けた min≥2 の所有量指定子  | 3.11+  | 前の回へ戻らない（(?>…) と食い違う）| (?>(?:…){m,n}) と同じ        |
  |   (?:a{1,3}){2}+ × 'aa'              |        | None                               | (0, 2)                        |

ファジングは seed 固定で 3,000 組（深さ4までの式 × 長さ0〜12の文字列）。所有量指定子と
原子グループは 3.11 以降だけ文法に入れる（3.10 で skip にはしない）。3.10 の2つの癖は
3.11 の sre の印の戻し方の修正で直っているので、3.11 以降はその形も文法に入れる。
"""
import inspect
import random
import re
import sys

import pytest

from scriptvedit import regex_count, regex_trace
from scriptvedit.regex_vm import RegexCount, RegexTrace, _COUNT_MEMO, _compile


# --- (a) re との差分ファジング --------------------------------------------------

_POSS = sys.version_info >= (3, 11)
_ALPHA = "ab \n1="
_ATOMS = ["a", "b", "=", " ", ".", r"\s", r"\S", r"\d", r"\w", r"\W", "[ab]", "[^a]",
          "[a-b1]", r"[\s=]", r"\n"]
_ASSERTS = ["^", "$", r"\A", r"\Z"]
_QUANTS = ["*", "+", "?", "{2}", "{1,}", "{,2}", "{1,3}", "{0,1}", "{2,3}"]


def _quant(rng, poss=True):
    q = rng.choice(_QUANTS)
    r = rng.random()
    if r < 0.3:
        q += "?"
    elif r < 0.45 and _POSS and poss:
        q += "+"
    return q


def _gen(rng, depth, nocap=False):
    """深さ depth までの式。nocap=True の部分には捕獲グループを作らない。"""
    if depth <= 0:
        if rng.random() < 0.85:
            return rng.choice(_ATOMS)
        return rng.choice(_ASSERTS)
    k = rng.random()
    if k < 0.30:
        return _gen(rng, depth - 1, nocap) + _gen(rng, depth - 1, nocap)
    if k < 0.45:
        return _gen(rng, depth - 1, nocap) + "|" + _gen(rng, depth - 1, nocap)
    if k < 0.75:
        # グループ（量指定子つきが多い）。所有はグループに付けない（3.11+ の sre の癖）
        q = _quant(rng, poss=False) if rng.random() < 0.85 else ""
        lazy = q.endswith("?") and len(q) > 1
        # 3.10 の sre は最小の繰り返しの中の捕獲を戻し損ねる → 捕獲を作らない
        cap_ok = not nocap and not (lazy and not _POSS)
        inner = _gen(rng, depth - 1, not cap_ok)
        forms = ["(?:%s)", "(?:%s)"] + (["(%s)", "(%s)"] if cap_ok else [])
        if _POSS:
            forms.append("(?>%s)")
        return rng.choice(forms) % inner + q
    if k < 0.85:
        return rng.choice(_ATOMS) + _quant(rng)
    if k < 0.93:
        la = rng.choice(["(?=%s)", "(?!%s)"])
        # 3.10 の sre は否定の中の捕獲を漏らす → 3.10 では作らない
        return la % _gen(rng, depth - 1, nocap or (la.startswith("(?!") and not _POSS))
    n = rng.randrange(1, 3)
    inner = "".join(rng.choice(_ATOMS) for _ in range(n))
    if rng.random() < 0.3:
        inner = "(?:%s|%s)" % (inner, "".join(rng.choice(_ATOMS) for _ in range(n)))
    return rng.choice(["(?<=%s)", "(?<!%s)"]) % inner


def _fuzz_pairs(n=3000, seed=20261003):
    rng = random.Random(seed)
    out = []
    while len(out) < n:
        pat = _gen(rng, rng.randrange(1, 5))
        text = "".join(rng.choice(_ALPHA) for _ in range(rng.randrange(0, 13)))
        out.append((pat, text))
    return out


def test_fuzz_matches_re():
    """3,000 組で span・groups が re の search / match / fullmatch と一致する"""
    pairs = _fuzz_pairs()
    checked = limited = 0
    bad = []
    seen = {"lazy": 0, "look": 0, "behind": 0, "cap": 0, "alt": 0, "count": 0, "poss": 0}
    for pat, text in pairs:
        cre = re.compile(pat)      # 生成した式は re が必ず受ける
        seen["lazy"] += bool(re.search(r"[*+?}]\?", pat))
        seen["look"] += "(?=" in pat or "(?!" in pat
        seen["behind"] += "(?<" in pat
        seen["cap"] += bool(re.search(r"\((?!\?)", pat))
        seen["alt"] += "|" in pat
        seen["count"] += "{" in pat
        seen["poss"] += "(?>" in pat or bool(re.search(r"[*+?}]\+", pat))
        for mode in ("search", "match", "fullmatch"):
            m = getattr(cre, mode)(text)
            try:
                tr = regex_trace(pat, text, mode=mode, max_steps=3_000_000)
            except RuntimeError:
                limited += 1      # 指数的に増える式（re には手数の上限が無い）
                continue
            checked += 1
            want = (m.span(), m.groups()) if m else (None, ())
            if (tr.span, tr.groups) != want:
                bad.append((pat, text, mode, want, (tr.span, tr.groups)))
    assert not bad, f"re と食い違う {len(bad)} 件（先頭5件）: {bad[:5]}"
    assert limited <= 5, f"手数の上限に当たった組が多すぎる: {limited}"
    assert checked >= 8900
    # 文法が痩せていないこと（各構文がそれなりの数だけ出ている）
    for k, v in seen.items():
        if k == "poss" and not _POSS:
            assert v == 0
            continue
        assert v >= 150, f"ファジングで '{k}' の形が少なすぎる: {v}"


# 所有量指定子・原子グループ（3.10 の re には無い）と sre の癖の形。期待値は
# 「所有 = 原子グループで包んだ貪欲」から手で求めたもの。
# sre_ok: 3.11 以降の re も同じ答えを返すか（False は sre の癖。上の表）
_POSSESSIVE_TABLE = [
    # (式, 文字列, mode, span, groups, sre_ok)
    (r"a*+a", "aaa", "match", None, (), True),
    (r"a*+b", "aaab", "match", (0, 4), (), True),
    (r"a++", "aaa", "fullmatch", (0, 3), (), True),
    (r"x?+x", "x", "match", None, (), True),
    (r"x?+y", "xy", "match", (0, 2), (), True),
    (r"a{1,3}+a", "aaaa", "match", (0, 4), (), True),
    (r"a{1,3}+a", "aaa", "match", None, (), True),
    (r"a*?+", "aa", "match", None, None, None),      # 「*?+」は所有ではなく文法の誤り（下の表）
    (r"\s++$", "  x  ", "search", (3, 5), (), True),
    (r"\s++$", "x  x", "search", None, (), True),
    (r"(?>a*)a", "aaa", "search", None, (), True),
    (r"(?>a|ab)c", "abc", "search", None, (), True),
    (r"(?:a|ab)c", "abc", "search", (0, 3), (), True),
    (r"(?>(\d+))\.", "123.", "search", (0, 4), ("123",), True),
    (r"(?>a+)b|a", "aa", "search", (0, 1), (), True),
    (r"(?:aa|a)++a", "aaa", "match", None, (), True),
    (r"(?>(?:a{1,3}){2})", "aa", "match", (0, 2), (), True),
    (r"(?:a{1,3}){2}+", "aa", "match", (0, 2), (), False),
    (r"(?:a{1,3}){2,3}+", "aaa", "match", (0, 3), (), False),
    (r"(?=(a+))a", "aaa", "match", (0, 1), ("aaa",), True),
    (r"(?>(a)|b)*c", "abac", "match", (0, 4), ("a",), True),
]


@pytest.mark.parametrize("pat,text,mode,span,groups,sre_ok",
                         [r for r in _POSSESSIVE_TABLE if r[3:5] != (None, None)])
def test_possessive_and_atomic_table(pat, text, mode, span, groups, sre_ok):
    tr = regex_trace(pat, text, mode=mode)
    assert (tr.span, tr.groups) == (span, groups)
    if sys.version_info >= (3, 11):
        m = getattr(re.compile(pat), mode)(text)
        got = (m.span(), m.groups()) if m else (None, ())
        if sre_ok:
            assert got == (span, groups), f"3.11+ の re と食い違う: {got}"
        else:
            assert got != (span, groups), "sre の癖が直った。表の sre_ok を True にする"


def test_sre_quirks_are_documented():
    """文法から外した sre の癖が、いまの Python でも本当に癖であることを確かめる"""
    # 3.10 だけ: 否定の先読みの中の捕獲
    m = re.search(r"(?!(.){2,3})", "=a1 =1\na \n=1")
    tr = regex_trace(r"(?!(.){2,3})", "=a1 =1\na \n=1")
    assert tr.span == m.span() and tr.groups == (None,)
    assert m.groups() == (("",) if sys.version_info < (3, 11) else (None,))
    # 3.10 だけ: 最小の繰り返しの捕獲
    pat, text = r"((?:\s){1,}){1,}?(?<![^a].)", "1=\n a"
    tr = regex_trace(pat, text)
    assert tr.span == (2, 3) and tr.groups == ("\n",)
    m = re.search(pat, text)
    if sys.version_info < (3, 11):
        assert m.groups() == ("",)
    else:
        assert m.groups() == ("\n",)


# --- (b) 回数の等式 -------------------------------------------------------------

def test_whitespace_dollar_counts_n0_to_60():
    """x＋空白 n 個＋x に \\s+$: tests = n²+2n+3、matches = n(n+1)/2（n=0〜60 の全部）"""
    for n in range(61):
        tr = regex_trace(r"\s+$", "x" + " " * n + "x")
        assert tr.span is None
        assert tr.counts["tests"] == n * n + 2 * n + 3, n
        assert tr.counts["matches"] == n * (n + 1) // 2, n
        assert tr.counts["attempts"] == n + 3


def _eq_tests(tr):
    eq = {i for i, nd in enumerate(tr.nodes) if nd.kind == "char" and nd.text == "="}
    return [e for e in tr.events if e[0] == "test" and e[2] in eq]


def test_dotstar_equals_56_and_split_order():
    """xxxxx に .*(?:.*=.*): = の判定は 56 回。(s, a, b) の列が二重ループと一致する"""
    tr = regex_trace(r".*(?:.*=.*)", "xxxxx")
    eqs = _eq_tests(tr)
    assert len(eqs) == 56 == sum((m + 1) * (m + 2) // 2 for m in range(6))
    seq = []
    for e in eqs:
        sp = {q: (a, b) for q, a, b in e[4]}
        (a0, a1), (b0, b1) = sp[0], sp[1]
        assert a1 == b0 and e[1] == b1          # = を判定する位置は2つ目の取り分の終わり
        seq.append((a0, a1 - a0, b1 - b0))
    want = [(s, a, b) for s in range(6) for a in range(5 - s, -1, -1)
            for b in range(5 - s - a, -1, -1)]
    assert seq == want
    # 位置 0 で 21 回（storyboard の 1-14 の最初の 21 コマ）
    assert sum(1 for s, _a, _b in seq if s == 0) == 21
    assert all(not e[3] for e in eqs)            # = は一度も一致しない


def test_counts_agree_with_events():
    tr = regex_trace(r"(?:a|ab)*c", "abababx")
    ev = tr.events
    assert tr.counts["tests"] == sum(1 for e in ev if e[0] in ("test", "assert"))
    assert tr.counts["matches"] == sum(1 for e in ev if e[0] == "test" and e[3])
    assert tr.counts["backtracks"] == sum(1 for e in ev if e[0] == "backtrack")
    assert tr.counts["attempts"] == sum(1 for e in ev if e[0] == "start") == len(tr.attempts)
    assert list(tr.event_steps) == sorted(tr.event_steps)
    assert tr.event_steps[-1] == tr.counts["steps"]


# --- (c) 再帰を使わない -----------------------------------------------------------

def test_fifty_thousand_spaces_no_recursion():
    """空白 50,000 個でも RecursionError にならない（スタックの深さに依らない）"""
    old = sys.getrecursionlimit()
    depth = len(inspect.stack(0))
    sys.setrecursionlimit(depth + 80)     # 文字列の長さに比例する再帰があれば落ちる
    try:
        tr = regex_trace(r"\s+$", " " * 50_000)
        tr2 = regex_trace(r"(\s)*x", " " * 50_000, mode="match", max_steps=1_000_000)
        tr3 = regex_trace(r"(?:\s|y)+$", " " * 50_000 + "x", mode="match",
                          max_steps=1_000_000)
    finally:
        sys.setrecursionlimit(old)
    assert tr.span == (0, 50_000)
    assert tr2.span is None and tr2.counts["backtracks"] == 50_001   # 5万個の後戻り点
    assert tr3.span is None


# --- (d) regex_count ------------------------------------------------------------

def _ws(n):
    return "x" + " " * n + "x"


def test_count_poly_whitespace_dollar():
    r = regex_count(r"\s+$", _ws, 20_000)
    assert isinstance(r, RegexCount)
    assert r.method == "poly" and r.formula == "n^2 + 2n + 3"
    assert r.value == 400_040_003 and r.checked == (48, 64)
    m = regex_count(r"\s+$", _ws, 20_000, count="matches")
    assert m.method == "poly" and m.value == 200_010_000
    assert m.formula == "(n^2 + n)/2"


def test_count_quoted_sum_is_one_to_19999():
    """事後報告の「20,000＋…＋1＝199,990,000」は 1〜19,999 の和（1〜20,000 は 200,010,000）"""
    assert sum(range(1, 20_000)) == 199_990_000
    assert regex_count(r"\s+$", _ws, 20_000, count="matches").value == sum(range(1, 20_001))


def test_count_direct_matches_poly():
    """多項式で外挿した値と、直接数えた値が一致する（direct_limit を変えて両方を通す）"""
    poly = regex_count(r".*(?:.*=.*)", lambda n: "x" * n, 100, direct_limit=200_000)
    direct = regex_count(r".*(?:.*=.*)", lambda n: "x" * n, 100)
    assert poly.method == "poly" and direct.method == "direct"
    assert poly.value == direct.value
    assert "n^3" in poly.formula and "n^4" not in poly.formula   # 3次
    assert direct.formula == poly.formula and 100 in direct.checked
    tr = regex_trace(r".*(?:.*=.*)", "x" * 30)
    assert regex_count(r".*(?:.*=.*)", lambda n: "x" * n, 30).value == tr.counts["tests"]


def test_count_small_n_is_direct():
    r = regex_count(r"\s+$", _ws, 10, count="backtracks")
    assert r.method == "direct" and r.value == regex_trace(r"\s+$", _ws(10)).counts["backtracks"]


def test_count_exponential_raises():
    with pytest.raises(ValueError, match="指数"):
        regex_count(r"^(a+)+$", lambda n: "a" * n + "!", 30)


@pytest.mark.parametrize("pattern,mk,n", [
    (r"\s{1,100}$", _ws, 500),
    (r"\s{0,70}$", _ws, 500),
    (r"(?:\s|x){1,80}y", _ws, 500),
    (r"(?:\s\s){1,40}$", _ws, 400),              # 入れ子の上限は掛け合わせて 80
    (r"\s{1,30}\s{1,30}$", _ws, 400),           # 並びの上限は足し合わせて 60
])
def test_count_bounded_quantifier_extrapolates_past_the_bound(pattern, mk, n):
    """上限のある量指定子: 取り分が上限に届くと増え方が変わる（2次 → 1次）。当てはめの n を
    上限の先へずらすので、外挿した値が直接数えた値と一致する（以前は 2.8〜3.8 倍の値を
    method='poly' で黙って返していた）"""
    r = regex_count(pattern, mk, n, direct_limit=1000)
    direct = regex_trace(pattern, mk(n), max_steps=10_000_000).counts["tests"]
    assert r.method == "poly"
    assert r.value == direct, (r, direct)
    assert min(r.checked) > _compile(pattern).reach


def test_count_bounded_quantifier_far_check_catches_short_runs():
    """make_text が n より短い連なりを作ると、ずらした当てはめ点でもまだ上限の手前にいる。
    当てはめの外の大きな n での検算で見つけて ValueError にする（黙って違う値を返さない）"""
    with pytest.raises(ValueError, match="上限"):
        regex_count(r"\s{1,100}$", lambda n: "x" + " " * (n // 2) + "x", 1000)


def test_count_explicit_fit_ns_below_bound_raises():
    with pytest.raises(ValueError, match="fit_ns は 100 より大きい"):
        regex_count(r"\s{1,100}$", _ws, 500, fit_ns=(8, 16, 32, 64))


def test_bound_reach_of_patterns():
    """当てはめの安全域（有限の回数・幅の最大）"""
    assert _compile(r"\s+$").reach == 1
    assert _compile(r".*(?:.*=.*)").reach == 1          # '=' の1字（.* は上限なし）
    assert _compile(r"(?:\s{1,10}){1,10}").reach == 100
    assert _compile(r"\s{1,30}\s{1,30}$").reach == 60
    assert _compile(r"\s{20,}$").reach == 20
    assert _compile(r"(?<=a{3})b").reach == 3
    assert not _compile(r"\s+?x*y?").bounded and _compile(r"a{2,}").bounded


def test_count_memo_returns_same_object():
    _COUNT_MEMO.clear()
    a = regex_count(r"\s+$", _ws, 5_000)
    b = regex_count(r"\s+$", _ws, 5_000)
    assert a is b
    # 文字列の作り方が違えば別の計算
    c = regex_count(r"\s+$", lambda n: "y" + " " * n + "y", 5_000)
    assert c is not a and c.value == a.value


@pytest.mark.parametrize("kw,msg", [
    ({"count": "nope"}, "count"),
    ({"mode": "find"}, "mode"),
    ({"fit_ns": (8, 12, 16)}, "fit_ns"),
    ({"fit_ns": (12, 8, 16, 24)}, "fit_ns"),
    ({"direct_limit": 0}, "direct_limit"),
])
def test_count_argument_errors(kw, msg):
    with pytest.raises(ValueError, match=msg):
        regex_count(r"\s+$", _ws, 100, **kw)


def test_count_make_text_errors():
    with pytest.raises(TypeError):
        regex_count(r"\s+$", "xx", 10)
    with pytest.raises(TypeError):
        regex_count(r"\s+$", lambda n: n, 10)
    with pytest.raises(ValueError):
        regex_count(r"\s+$", _ws, -1)


# --- (e) 対応しない構文 -----------------------------------------------------------

@pytest.mark.parametrize("pat,what", [
    (r"(a)\1", "後方参照"),
    (r"(?P<x>a)", "名前つきグループ"),
    (r"(?P=x)", "後方参照"),
    (r"(?i)a", "フラグ"),
    (r"(?i:a)", "フラグ"),
    (r"\ba", r"\\b"),
    (r"a\B", r"\\B"),
    (r"(a)(?(1)b|c)", "条件分岐"),
    (r"\p{L}", r"\\p"),
    (r"(?#note)a", "注釈"),
    (r"(?<=a+)b", "固定幅"),
    (r"(?<=a|bc)d", "固定幅"),
    (r"(?=a)*", "量指定子"),
    (r"a{1001}", "1000"),
])
def test_unsupported_syntax(pat, what):
    with pytest.raises(ValueError, match="regex_trace が対応しない構文") as ei:
        regex_trace(pat, "abc")
    assert re.search(what, str(ei.value))
    assert re.search(r"（位置 \d+）", str(ei.value))


@pytest.mark.parametrize("pat", [
    "(a", "a)", "[a", "*a", "a**", "a{2}{3}", "[z-a]", "a{3,2}", "^*", "\\", r"\x4", r"\q",
    "a*?+",
])
def test_syntax_errors(pat):
    with pytest.raises(ValueError, match="式の誤り"):
        regex_trace(pat, "abc")


@pytest.mark.parametrize("pat,text", [
    ("a{", "a{"), ("a{,", "xa{,"), ("a{2,1x}", "a{2,1x}"), ("x{}", "x{}"),
    ("a{,}b", "aaab"), (r"\{1\}", "{1}"), ("[]a]+", "a]]a"), ("[a-]+", "-a-"),
    (r"[\b]", "\b"), (r"\101\0", "A\0"), (r"あ\N{HIRAGANA LETTER I}", "あい"),
    ("a$", "a\n"), (r"a\Z", "a\n"), ("$", "x\n"), (r"[\s\d]+", "1 2\t"), (r"\W+", " .!"),
])
def test_literal_brace_and_escapes_like_re(pat, text):
    m = re.search(pat, text)
    tr = regex_trace(pat, text)
    assert tr.span == (m.span() if m else None)


# --- (f) event の形 ---------------------------------------------------------------

def test_event_shape():
    tr = regex_trace(r"(a+)+b|c?$", "aac")
    assert isinstance(tr, RegexTrace)
    kinds = {e[0] for e in tr.events}
    assert kinds <= {"start", "test", "assert", "backtrack", "match", "fail"}
    n_nodes = len(tr.nodes)
    for e in tr.events:
        kind, pos, node, ok, spans = e
        assert 0 <= pos <= len(tr.text)
        assert node is None or 0 <= node < n_nodes
        assert isinstance(ok, bool)
        for q, a, b in spans:
            assert tr.quantifiers[q].visible and 0 <= a <= b <= len(tr.text)
    assert [n.text for n in tr.nodes] == ["(", "a+", ")+", "b", "|", "c?", "$"]
    assert [(q.mode, q.min, q.max) for q in tr.quantifiers] == [
        ("greedy", 1, None), ("greedy", 1, None), ("greedy", 0, 1)]
    # 試行は event の範囲を順に覆う
    prev = -1
    for s, e0, e1, res in tr.attempts:
        assert e0 == prev + 1 and tr.events[e0][0] == "start" and tr.events[e1][0] in (
            "match", "fail")
        prev = e1
    assert prev == len(tr.events) - 1
    assert tr.attempts[-1][3] == "match" and tr.span == (2, 3)


def test_quantifier_modes_and_lookaround_spans_hidden():
    tr = regex_trace(r"a*?b++(?=c*)d{2,3}+", "")
    assert [(q.mode, q.visible) for q in tr.quantifiers] == [
        ("lazy", True), ("possessive", True), ("greedy", False), ("possessive", True)]


def test_max_steps_runtime_error_mentions_regex_count():
    with pytest.raises(RuntimeError, match="regex_count"):
        regex_trace(r"\s+$", "x" + " " * 400 + "x", max_steps=10_000)


def test_argument_errors():
    with pytest.raises(ValueError, match="mode"):
        regex_trace("a", "a", mode="find")
    with pytest.raises(TypeError):
        regex_trace("a", b"a")
    with pytest.raises(TypeError):
        regex_trace(b"a", "a")
    with pytest.raises(ValueError, match="max_steps"):
        regex_trace("a", "a", max_steps=0)


def test_fullmatch_end_check_is_an_assert():
    tr = regex_trace(r"a|ab", "ab", mode="fullmatch")
    assert tr.span == (0, 2)
    ends = [e for e in tr.events if e[0] == "assert" and e[2] is None]
    assert [e[3] for e in ends] == [False, True]
