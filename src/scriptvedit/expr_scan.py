# -*- coding: utf-8 -*-
"""時間だけの式（Expr）を、表示区間 u ∈ [0, 1] の上で速く・漏れなく調べる道具。

フィルタを組む側（filters/video.py）は、式の最大（scale の pad 見積もり）や
「入りと出の単純なランプと一致するか」（native fade の判定）を、実際に描くコマの
すべてで確かめる必要がある（100 等分の格子だけでは、多点の keyframes の細い山・谷を
取りこぼす。CLAUDE.md §4.13）。以前は全コマで Expr.eval_at を呼んでいたが、eval_at は
if の両方の枝を評価するので、128 点の keyframes は1回の評価で木の全体を辿り、評価の
手間が「コマ数 × 点の数」に比例した（実測: 60fps・128 点の scale で、600 秒の Object は
フィルタを組むたびに 17 秒、3600 秒は 107 秒止まった）。

ここにあるのは2つ:

- _pl_pieces: 区分線形の式（keyframes / keyframes_sec / ramp / phase / lerp / clip /
  min / max / abs と、比較・if の組み合わせ）を、u の区間ごとの1次式 a*u + b の列へ
  展開する。各区間の内側では式がその1次式そのものなので、区間の端（頂点）とその前後の
  コマだけを見れば、全コマの最大・最小が分かる（片側極限を見れば区間の内側全体の上限も
  分かる）。コマを1枚ずつ評価しなくてよく、手間は点の数に比例してコマ数に依らない。
  イージング（積・pow・sin 等）や中身を辿れない Expr の派生は None（区分線形と言えない。
  呼び出し側はコマごとの評価へ戻る）。
- _compile_u_eval: eval_at と同じ値を返す評価関数を作る。if は選んだ枝だけを評価する
  （ffmpeg の式評価器と同じ）ので、区分線形でない式をコマごとに評価しても、1回の評価が
  木の深さ（点の数の対数）で済む。eval_at との違いは、選ばれない枝で起きる例外
  （0 除算など）を投げないことだけ（ffmpeg も選ばれない枝は評価しない）。

このモジュールは scriptvedit.expr だけを import する（filters/video.py から先頭で import
する。循環 import を作らない）。
"""

import math as _math

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.expr import Const, Var, _BinOp, _FuncCall, _TimeVar, _UnOp, _UValue


# 区分の数の上限。超える式は区分線形として扱わない（呼び出し側はコマごとの評価へ戻る）。
# 128 点の keyframes は 127 区分前後なので、通常の式はこの上限に届かない
_PL_MAX_PIECES = 4096

# これより細い区分（u の幅）は丸めの産物として隣の区分へ含める（_pl_norm）。
# 3600 秒の Object でも 3.6e-9 秒で、コマの間隔よりずっと短い
_PL_SLIVER = 1e-12


class _NotPiecewiseLinear(Exception):
    """区分線形として展開できない（_pl_pieces の内部でだけ使う）"""


def _pl_num(value):
    """Const の値を有限の float にする（数値でない・非有限は区分線形として扱わない）"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _NotPiecewiseLinear
    value = float(value)
    if not _math.isfinite(value):
        raise _NotPiecewiseLinear
    return value


def _pl_norm(pieces):
    """丸めでできる細い区分を除き、隣り合う同じ1次式の区分をつなぎ、上限と有限性を確かめる"""
    # clip の折れ点と lt の境目のように数学的には同じ点が、丸めで 1ulp ずれて幅 1e-16 程度の
    # 区分ができる（例: sequence_param の区間の終わり。そこでは区間の終わりの値 = 次の区間へ
    # 切り替わる手前の片側極限を取る）。どのコマも入らない幅なので前の区分へ含める
    # （そこだけの値を頂点として拾うと、丸めの向き次第で結果が変わる）
    kept = []
    for piece in pieces:
        if piece[1] - piece[0] <= _PL_SLIVER and kept:
            prev = kept[-1]
            kept[-1] = (prev[0], piece[1], prev[2], prev[3])
        else:
            kept.append(piece)
    if len(kept) > 1 and kept[0][1] - kept[0][0] <= _PL_SLIVER:
        first = kept.pop(0)
        kept[0] = (first[0], kept[0][1], kept[0][2], kept[0][3])
    out = []
    for piece in kept:
        if out and out[-1][2] == piece[2] and out[-1][3] == piece[3]:
            out[-1] = (out[-1][0], piece[1], piece[2], piece[3])
        else:
            out.append(piece)
    if len(out) > _PL_MAX_PIECES:
        raise _NotPiecewiseLinear
    for _x0, _x1, a, b in out:
        if not (_math.isfinite(a) and _math.isfinite(b)):
            raise _NotPiecewiseLinear
    return out


def _pl_align(funcs):
    """区分の列のリストを共通の細分へ揃え、(x0, x1, [(a, b), ...]) の列を返す"""
    xs = sorted({x for f in funcs for piece in f for x in (piece[0], piece[1])})
    idx = [0] * len(funcs)
    rows = []
    for x0, x1 in zip(xs, xs[1:]):
        row = []
        for k, f in enumerate(funcs):
            i = idx[k]
            while f[i][1] <= x0:
                i += 1
            idx[k] = i
            row.append((f[i][2], f[i][3]))
        rows.append((x0, x1, row))
    return rows


def _pl_split(x0, x1, da, db):
    """[x0, x1) を da*u + db = 0 の点で分けた小区間の列（内側で符号が一定になる）"""
    if da != 0:
        r = -db / da
        if x0 < r < x1:
            return ((x0, r), (r, x1))
    return ((x0, x1),)


def _pl_arith(op, f, g):
    rows = _pl_align([f, g])
    out = []
    for x0, x1, ((fa, fb), (ga, gb)) in rows:
        if op == "+":
            a, b = fa + ga, fb + gb
        elif op == "-":
            a, b = fa - ga, fb - gb
        elif op == "*":
            # 片方がこの区分で定数なら1次式のまま（両方が傾きを持つと2次式）
            if fa == 0:
                a, b = fb * ga, fb * gb
            elif ga == 0:
                a, b = gb * fa, gb * fb
            else:
                raise _NotPiecewiseLinear
        else:  # "/"
            if ga != 0 or gb == 0:
                raise _NotPiecewiseLinear
            a, b = fa / gb, fb / gb
        out.append((x0, x1, a, b))
    return _pl_norm(out)


def _pl_neg(f):
    return [(x0, x1, -a, -b) for x0, x1, a, b in f]


def _pl_minmax(f, g, want_max):
    """min / max（交わる点で区分を分け、各小区間で大きい方・小さい方を選ぶ）"""
    out = []
    for x0, x1, ((fa, fb), (ga, gb)) in _pl_align([f, g]):
        da, db = fa - ga, fb - gb
        for s0, s1 in _pl_split(x0, x1, da, db):
            d = da * (0.5 * (s0 + s1)) + db
            take_f = d >= 0 if want_max else d <= 0
            out.append((s0, s1, fa, fb) if take_f else (s0, s1, ga, gb))
    return _pl_norm(out)


# 比較（ffmpeg の lt / gt / lte / gte）。差 d = 左 - 右 の符号で 0 / 1 を決める
_PL_CMP = {
    "lt": lambda d: d < 0,
    "gt": lambda d: d > 0,
    "lte": lambda d: d <= 0,
    "gte": lambda d: d >= 0,
}


def _pl_cmp(f, g, test):
    out = []
    for x0, x1, ((fa, fb), (ga, gb)) in _pl_align([f, g]):
        da, db = fa - ga, fb - gb
        for s0, s1 in _pl_split(x0, x1, da, db):
            d = da * (0.5 * (s0 + s1)) + db
            out.append((s0, s1, 0.0, 1.0 if test(d) else 0.0))
    return _pl_norm(out)


def _pl_if(c, t, e):
    out = []
    for x0, x1, ((ca, cb), (ta, tb), (ea, eb)) in _pl_align([c, t, e]):
        if ca != 0:
            # 条件が区分の内側で 0 を横切る（傾きを持つ）なら、その1点だけ枝が変わる
            raise _NotPiecewiseLinear
        out.append((x0, x1, ta, tb) if cb != 0 else (x0, x1, ea, eb))
    return _pl_norm(out)


def _pl_build(node, dur):
    cls = type(node)
    if cls is Const:
        return [(0.0, 1.0, 0.0, _pl_num(node.value))]
    if cls is Var:
        if node.name != "u":
            raise _NotPiecewiseLinear
        return [(0.0, 1.0, 1.0, 0.0)]
    if cls is _TimeVar:
        if dur is None:
            raise _NotPiecewiseLinear
        d = _pl_num(dur)
        # eval_at と同じ: sec は u × 表示秒、それ以外（dur）は表示秒
        return [(0.0, 1.0, d, 0.0)] if node.kind == "sec" else [(0.0, 1.0, 0.0, d)]
    if cls is _BinOp:
        if node.op not in ("+", "-", "*", "/"):
            raise _NotPiecewiseLinear
        return _pl_arith(node.op, _pl_build(node.left, dur), _pl_build(node.right, dur))
    if cls is _UnOp:
        if node.op != "-":
            raise _NotPiecewiseLinear
        return _pl_neg(_pl_build(node.operand, dur))
    if cls is _FuncCall:
        name, args = node.name, node.args
        n = len(args)
        if name == "clip" and n == 3:
            # eval_at と同じ max(lo, min(hi, v))
            v, lo, hi = (_pl_build(a, dur) for a in args)
            return _pl_minmax(lo, _pl_minmax(hi, v, False), True)
        if name in ("min", "max") and n == 2:
            return _pl_minmax(_pl_build(args[0], dur), _pl_build(args[1], dur),
                              name == "max")
        if name == "abs" and n == 1:
            f = _pl_build(args[0], dur)
            return _pl_minmax(f, _pl_neg(f), True)
        if name in _PL_CMP and n == 2:
            return _pl_cmp(_pl_build(args[0], dur), _pl_build(args[1], dur), _PL_CMP[name])
        if name == "if" and n == 3:
            return _pl_if(*(_pl_build(a, dur) for a in args))
        if name == "not" and n == 1:
            out = []
            for x0, x1, a, b in _pl_build(args[0], dur):
                if a != 0:
                    raise _NotPiecewiseLinear
                out.append((x0, x1, 0.0, 1.0 if b == 0 else 0.0))
            return _pl_norm(out)
        if name == "between" and n == 3:
            x, lo, hi = (_pl_build(a, dur) for a in args)
            return _pl_arith("*", _pl_cmp(x, lo, _PL_CMP["gte"]),
                             _pl_cmp(x, hi, _PL_CMP["lte"]))
    # イージング（pow / sin / exp 等）・floor / mod・eq（1点だけ値が変わる）・
    # 中身を辿れない Expr の派生は区分線形として扱わない
    raise _NotPiecewiseLinear


def _pl_pieces(expr, dur):
    """式を u ∈ [0, 1] の区分ごとの1次式 [(x0, x1, a, b), ...] へ展開する（できなければ None）。

    区分は x0 < x1 で隙間なく並び、最初の x0 は 0.0、最後の x1 は 1.0。各区分の内側
    （x0 < u < x1）では式の値が a*u + b そのもの。区分の端ちょうどの値は、比較の向き
    （lt と lte）で左右どちらの1次式に一致するかが変わるので、端の値が要るときは
    _compile_u_eval で評価すること（_pl_max_abs は片側極限だけを見る）。
    dur は表示秒（秒で書いた式 elapsed / remaining / keyframes_sec の換算に使う）。
    """
    try:
        return _pl_build(expr, dur)
    except (_NotPiecewiseLinear, RecursionError, ZeroDivisionError, OverflowError):
        return None


def _pl_ends(pieces):
    """区分の端の u（0.0 と 1.0 を含む、昇順）"""
    return [pieces[0][0]] + [piece[1] for piece in pieces]


def _pl_max_abs(pieces):
    """区分の端での片側極限の絶対値の最大（各区分の内側の |値| の上限）"""
    return max(max(abs(a * x0 + b), abs(a * x1 + b)) for x0, x1, a, b in pieces)


def _pl_sub(f, g):
    """f - g（区分線形どうしの差。区分が上限を超えるなどで展開できなければ None）"""
    try:
        return _pl_arith("-", f, g)
    except _NotPiecewiseLinear:
        return None


def _pl_clip01(f):
    """0..1 への clip（fade / opacity の不透明度と同じ。展開できなければ None）"""
    lo = [(0.0, 1.0, 0.0, 0.0)]
    hi = [(0.0, 1.0, 0.0, 1.0)]
    try:
        return _pl_minmax(lo, _pl_minmax(hi, f, False), True)
    except _NotPiecewiseLinear:
        return None


def _compile_u_eval(expr, dur):
    """expr.eval_at(_UValue(u, dur)) と同じ値を返す関数 f(u) を作る。

    if は選んだ枝だけを評価する（ffmpeg の式評価器と同じ）。eval_at は両方の枝を評価
    するので、多点の keyframes（if の二分木）では1回の評価で木の全体を辿るが、こちらは
    木の深さだけで済む。演算・関数の意味は eval_at の表（_FuncCall._get_eval_funcs）を
    そのまま使い、未知のノード（中身を辿れない Expr の派生・未対応の演算子や関数・
    u 以外の変数）は eval_at に任せる（同じ値・同じ例外になる）。
    """
    funcs = _FuncCall._get_eval_funcs()

    def build(node):
        cls = type(node)
        if cls is Const:
            value = node.value
            return lambda u: value
        if cls is Var and node.name == "u":
            return lambda u: u
        if cls is _TimeVar and dur is not None:
            if node.kind == "sec":
                return lambda u: u * dur
            return lambda u: dur
        if cls is _BinOp and node.op in ("+", "-", "*", "/"):
            left, right = build(node.left), build(node.right)
            if node.op == "+":
                return lambda u: left(u) + right(u)
            if node.op == "-":
                return lambda u: left(u) - right(u)
            if node.op == "*":
                return lambda u: left(u) * right(u)
            return lambda u: left(u) / right(u)
        if cls is _UnOp and node.op == "-":
            operand = build(node.operand)
            return lambda u: -operand(u)
        if cls is _FuncCall and node.name in funcs:
            fn = funcs[node.name]
            args = [build(a) for a in node.args]
            if node.name == "if" and len(args) == 3:
                cond, then, other = args
                return lambda u: then(u) if cond(u) != 0 else other(u)
            if len(args) == 1:
                a0 = args[0]
                return lambda u: fn(a0(u))
            if len(args) == 2:
                a0, a1 = args
                return lambda u: fn(a0(u), a1(u))
            if len(args) == 3:
                a0, a1, a2 = args
                return lambda u: fn(a0(u), a1(u), a2(u))
            return lambda u: fn(*[a(u) for a in args])
        return lambda u: node.eval_at(_UValue(u, dur))

    return build(expr)
