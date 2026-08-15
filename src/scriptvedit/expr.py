# -*- coding: utf-8 -*-

import math as _math
import builtins as _builtins


# --- Expr（式ビルダー） ---

class Expr:
    """ffmpeg式ビルダー基底クラス"""
    def to_ffmpeg(self, u_expr):
        raise NotImplementedError

    def eval_at(self, u_value):
        """u=u_valueでの数値評価"""
        raise NotImplementedError

    def __add__(self, other):
        return _make_binop("+", self, _to_expr(other))

    def __radd__(self, other):
        return _make_binop("+", _to_expr(other), self)

    def __sub__(self, other):
        return _make_binop("-", self, _to_expr(other))

    def __rsub__(self, other):
        return _make_binop("-", _to_expr(other), self)

    def __mul__(self, other):
        return _make_binop("*", self, _to_expr(other))

    def __rmul__(self, other):
        return _make_binop("*", _to_expr(other), self)

    def __truediv__(self, other):
        return _make_binop("/", self, _to_expr(other))

    def __rtruediv__(self, other):
        return _make_binop("/", _to_expr(other), self)

    def __pow__(self, other):
        return _make_func("pow", [self, _to_expr(other)])

    def __rpow__(self, other):
        return _make_func("pow", [_to_expr(other), self])

    def __neg__(self):
        return _make_unop("-", self)

    def __abs__(self):
        return _make_func("abs", [self])

    def smooth(self):
        """smoothstep: 3t²-2t³"""
        return self * self * (Const(3) - Const(2) * self)

    def invert(self):
        """反転: 1 - self"""
        return Const(1) - self

    def pingpong(self):
        """三角波(0→1→0): |1 - 2*mod(self, 1)|"""
        return Const(1) - _make_func("abs", [Const(2) * _make_func("mod", [self, Const(1)]) - Const(1)])

    def map(self, lo, hi):
        """値をlo〜hiにマッピング: self * (hi - lo) + lo"""
        return self * (_to_expr(hi) - _to_expr(lo)) + _to_expr(lo)

    def clamped(self, lo=0, hi=1):
        """値をlo〜hiにクランプ"""
        return _make_func("clip", [self, _to_expr(lo), _to_expr(hi)])

    def oscillate(self, frequency=1, amplitude=1, offset=0):
        """正弦波: offset + amplitude * sin(self * frequency * 2π)"""
        f = _to_expr(frequency)
        a = _to_expr(amplitude)
        o = _to_expr(offset)
        return o + a * _make_func("sin", [self * f * Const(2 * 3.141592653589793)])

    def sawtooth(self, frequency=1):
        """ノコギリ波: 0→1を周期的に繰り返す"""
        return _make_func("mod", [self * _to_expr(frequency), Const(1)])

    def triangle(self, frequency=1):
        """三角波: 0→1→0を周期的に繰り返す"""
        phase = _make_func("mod", [self * _to_expr(frequency), Const(1)])
        return Const(1) - _make_func("abs", [Const(2) * phase - Const(1)])

    def plot(self, samples=60, height=15, width=60):
        """u=0..1でサンプルしてターミナルにアスキーグラフを表示（デバッグ用）

        matplotlib非依存。self.eval_at(u) をサンプルし、標準出力へ折れ線グラフ
        を描画する。値域は自動スケーリング。描画した文字列を返す。
        """
        if samples < 2:
            raise ValueError("plot: samples は2以上が必要です")
        us = [i / (samples - 1) for i in range(samples)]
        try:
            ys = [float(self.eval_at(u)) for u in us]
        except Exception as exc:
            raise ValueError(
                f"plot: 式を数値評価できません（uのみに依存する式が必要）: {exc}"
            ) from exc
        y_lo = _builtins.min(ys)
        y_hi = _builtins.max(ys)
        span = y_hi - y_lo
        if span < 1e-12:
            # 定数式: 中央に平坦な線を描く
            span = 1.0
            y_lo -= 0.5
        # width列にリサンプリング（samples != width でも桁を揃える）
        cols = width
        grid = [[" "] * cols for _ in range(height)]
        for cx in range(cols):
            u = cx / (cols - 1)
            # 最近傍サンプル
            idx = int(_builtins.round(u * (samples - 1)))
            y = ys[idx]
            norm = (y - y_lo) / span
            row = height - 1 - int(_builtins.round(norm * (height - 1)))
            row = _builtins.min(height - 1, _builtins.max(0, row))
            grid[row][cx] = "*"
        lines = []
        lines.append(f"  {y_hi:+.3f} +" + "-" * cols)
        for r, cells in enumerate(grid):
            lines.append("         |" + "".join(cells))
        lines.append(f"  {y_lo:+.3f} +" + "-" * cols)
        lines.append("         u=0" + " " * (cols - 6) + "u=1")
        out = "\n".join(lines)
        print(out)
        return out


class Const(Expr):
    """定数ノード"""
    def __init__(self, value):
        self.value = value

    def to_ffmpeg(self, u_expr):
        return str(self.value)

    def eval_at(self, u_value):
        return self.value


class Var(Expr):
    """変数ノード"""
    def __init__(self, name):
        self.name = name

    def to_ffmpeg(self, u_expr):
        if self.name == "u":
            return u_expr
        return self.name

    def eval_at(self, u_value):
        if self.name == "u":
            return u_value
        raise ValueError(f"変数 '{self.name}' は数値評価できません")


class _BinOp(Expr):
    """二項演算ノード"""
    def __init__(self, op, left, right):
        self.op = op
        self.left = left
        self.right = right

    def to_ffmpeg(self, u_expr):
        l = self.left.to_ffmpeg(u_expr)
        r = self.right.to_ffmpeg(u_expr)
        return f"({l}{self.op}{r})"

    def eval_at(self, u_value):
        l = self.left.eval_at(u_value)
        r = self.right.eval_at(u_value)
        if self.op == '+': return l + r
        if self.op == '-': return l - r
        if self.op == '*': return l * r
        if self.op == '/': return l / r
        raise ValueError(f"未対応の演算子: {self.op}")


class _UnOp(Expr):
    """単項演算ノード"""
    def __init__(self, op, operand):
        self.op = op
        self.operand = operand

    def to_ffmpeg(self, u_expr):
        x = self.operand.to_ffmpeg(u_expr)
        return f"({self.op}{x})"

    def eval_at(self, u_value):
        x = self.operand.eval_at(u_value)
        if self.op == '-': return -x
        raise ValueError(f"未対応の単項演算子: {self.op}")


class _FuncCall(Expr):
    """関数呼び出しノード"""
    def __init__(self, name, args):
        self.name = name
        self.args = args

    def to_ffmpeg(self, u_expr):
        arg_strs = [a.to_ffmpeg(u_expr) for a in self.args]
        sep = "\\,"
        return f"{self.name}({sep.join(arg_strs)})"

    _EVAL_FUNCS = None

    @classmethod
    def _get_eval_funcs(cls):
        if cls._EVAL_FUNCS is None:
            cls._EVAL_FUNCS = {
                'sin': _math.sin, 'cos': _math.cos, 'tan': _math.tan,
                'asin': _math.asin, 'acos': _math.acos, 'atan': _math.atan,
                'atan2': _math.atan2, 'sinh': _math.sinh, 'cosh': _math.cosh,
                'tanh': _math.tanh, 'exp': _math.exp, 'log': _math.log,
                'sqrt': _math.sqrt, 'floor': _math.floor, 'ceil': _math.ceil,
                'trunc': _math.trunc, 'round': _builtins.round,
                'abs': _builtins.abs, 'pow': _builtins.pow,
                'min': _builtins.min, 'max': _builtins.max,
                'mod': lambda a, b: a % b,
                'clip': lambda v, lo, hi: _builtins.max(lo, _builtins.min(hi, v)),
                'gte': lambda x, e: 1.0 if x >= e else 0.0,
                'lte': lambda x, e: 1.0 if x <= e else 0.0,
                'lt': lambda x, e: 1.0 if x < e else 0.0,
                'gt': lambda x, e: 1.0 if x > e else 0.0,
                'eq': lambda a, b: 1.0 if a == b else 0.0,
                'if': lambda c, t, e: t if c != 0 else e,
                # 注意: ffmpegの式評価器に and()/or() は存在しない。
                # 論理AND/ORは and_()/or_() が gt/abs の組み合わせへ変換する。
                'not': lambda a: 1.0 if a == 0 else 0.0,
                'between': lambda x, lo, hi: 1.0 if lo <= x <= hi else 0.0,
                'sign': lambda x: (1.0 if x > 0 else (-1.0 if x < 0 else 0.0)),
                'random': lambda seed: 0.5,  # eval_at用ダミー（ffmpegランタイムで評価）
                'log10': lambda x: _math.log10(x),
                'cbrt': lambda x: (
                    (1.0 if x > 0 else (-1.0 if x < 0 else 0.0))
                    * _builtins.pow(_builtins.abs(x), 1/3)),
            }
        return cls._EVAL_FUNCS

    def eval_at(self, u_value):
        args = [a.eval_at(u_value) for a in self.args]
        funcs = self._get_eval_funcs()
        if self.name not in funcs:
            raise ValueError(f"eval_at未対応の関数: {self.name}")
        return funcs[self.name](*args)


def _to_expr(x):
    """float/int→Const, Expr→そのまま, それ以外→TypeError"""
    if isinstance(x, Expr):
        return x
    if isinstance(x, (int, float)):
        return Const(x)
    raise TypeError(f"Exprに変換できません: {type(x)}")


_NONFOLDABLE_FUNCS = frozenset({'random'})

def _make_binop(op, left, right):
    """定数畳み込み・恒等式簡約付きBinOp生成"""
    if isinstance(left, Const) and isinstance(right, Const):
        l, r = left.value, right.value
        if op == '+': return Const(l + r)
        if op == '-': return Const(l - r)
        if op == '*': return Const(l * r)
        if op == '/' and r != 0: return Const(l / r)
    if op == '+':
        if isinstance(left, Const) and left.value == 0: return right
        if isinstance(right, Const) and right.value == 0: return left
    elif op == '-':
        if isinstance(right, Const) and right.value == 0: return left
    elif op == '*':
        if isinstance(left, Const) and left.value == 0: return Const(0)
        if isinstance(right, Const) and right.value == 0: return Const(0)
        if isinstance(left, Const) and left.value == 1: return right
        if isinstance(right, Const) and right.value == 1: return left
    elif op == '/':
        if isinstance(right, Const) and right.value == 1: return left
    return _BinOp(op, left, right)

def _make_unop(op, operand):
    """定数畳み込み付きUnOp生成"""
    if isinstance(operand, Const):
        if op == '-': return Const(-operand.value)
    if op == '-' and isinstance(operand, _UnOp) and operand.op == '-':
        return operand.operand
    return _UnOp(op, operand)

def _make_func(name, args):
    """定数畳み込み付きFuncCall生成"""
    if name == 'if' and len(args) == 3 and isinstance(args[0], Const):
        return args[1] if args[0].value != 0 else args[2]
    if name in _NONFOLDABLE_FUNCS:
        return _FuncCall(name, args)
    if all(isinstance(a, Const) for a in args):
        funcs = _FuncCall._get_eval_funcs()
        if name in funcs:
            try:
                vals = [a.value for a in args]
                result = funcs[name](*vals)
                if isinstance(result, (int, float)) and _math.isfinite(result):
                    return Const(result)
            except (ValueError, ZeroDivisionError, OverflowError):
                pass
    return _FuncCall(name, args)


def _resolve_param(param):
    """float→Const, callable→Var('u')で評価してExpr化, Expr→そのまま"""
    if isinstance(param, Expr):
        return param
    if isinstance(param, (int, float)):
        return Const(param)
    if callable(param):
        u = Var("u")
        try:
            result = param(u)
        except TypeError as e:
            raise TypeError(
                "lambda内でmath関数は使えません。scriptveditの関数を使ってください。\n"
                "使用可能: sin, cos, tan, exp, log, sqrt, lerp, clip, abs, min, max, "
                "floor, ceil, smoothstep, step, mod, frac, PI, E"
            ) from e
        return _to_expr(result)
    raise TypeError(f"Effect引数にはfloat, lambda, Exprのいずれかを渡してください: {type(param)}")


# --- 数学関数（Exprラッパー） ---

def sin(x):
    return _make_func("sin", [_to_expr(x)])

def cos(x):
    return _make_func("cos", [_to_expr(x)])

def tan(x):
    return _make_func("tan", [_to_expr(x)])

def asin(x):
    return _make_func("asin", [_to_expr(x)])

def acos(x):
    return _make_func("acos", [_to_expr(x)])

def atan(x):
    return _make_func("atan", [_to_expr(x)])

def atan2(y, x):
    return _make_func("atan2", [_to_expr(y), _to_expr(x)])

def sinh(x):
    return _make_func("sinh", [_to_expr(x)])

def cosh(x):
    return _make_func("cosh", [_to_expr(x)])

def tanh(x):
    return _make_func("tanh", [_to_expr(x)])

def exp(x):
    return _make_func("exp", [_to_expr(x)])

def log(x):
    return _make_func("log", [_to_expr(x)])

def sqrt(x):
    return _make_func("sqrt", [_to_expr(x)])

def floor(x):
    return _make_func("floor", [_to_expr(x)])

def ceil(x):
    return _make_func("ceil", [_to_expr(x)])

def trunc(x):
    return _make_func("trunc", [_to_expr(x)])

_LN10 = 2.302585092994046  # math.log(10)

def log10(x):
    return _make_func("log", [_to_expr(x)]) / Const(_LN10)

def cbrt(x):
    xv = _to_expr(x)
    return sign(xv) * pow(abs(xv), Const(1/3))

def lerp(a, b, t):
    a, b, t = _to_expr(a), _to_expr(b), _to_expr(t)
    return a + (b - a) * t

def clip(x, lo, hi):
    return _make_func("clip", [_to_expr(x), _to_expr(lo), _to_expr(hi)])

clamp = clip

def step(edge, x):
    return _make_func("gte", [_to_expr(x), _to_expr(edge)])

def smoothstep(edge0, edge1, x):
    e0, e1, xv = _to_expr(edge0), _to_expr(edge1), _to_expr(x)
    t = clip((xv - e0) / (e1 - e0), 0, 1)
    return t * t * (Const(3) - Const(2) * t)

def mod(a, b):
    return _make_func("mod", [_to_expr(a), _to_expr(b)])

def frac(x):
    xv = _to_expr(x)
    return xv - floor(xv)

def deg2rad(x):
    return _to_expr(x) * Const(3.141592653589793 / 180)

def rad2deg(x):
    return _to_expr(x) * Const(180 / 3.141592653589793)

# Python組み込みと衝突する関数（両方対応）
def abs(x):
    if isinstance(x, Expr):
        return _make_func("abs", [x])
    return _builtins.abs(x)

def min(*args, **kwargs):
    if any(isinstance(a, Expr) for a in args):
        if kwargs:
            raise TypeError(
                "scriptvedit.min: Expr では key/default を指定できません")
        # ffmpegのmin/maxは厳密に2引数固定 → 3引数以上は左畳み込みでネストする
        exprs = [_to_expr(a) for a in args]
        result = exprs[0]
        for e in exprs[1:]:
            result = _make_func("min", [result, e])
        return result
    # star import後もPython組み込みの完全な呼び出し規約
    # （iterable/key/default、複数引数/key）を維持する。
    return _builtins.min(*args, **kwargs)

def max(*args, **kwargs):
    if any(isinstance(a, Expr) for a in args):
        if kwargs:
            raise TypeError(
                "scriptvedit.max: Expr では key/default を指定できません")
        # ffmpegのmin/maxは厳密に2引数固定 → 3引数以上は左畳み込みでネストする
        exprs = [_to_expr(a) for a in args]
        result = exprs[0]
        for e in exprs[1:]:
            result = _make_func("max", [result, e])
        return result
    return _builtins.max(*args, **kwargs)

def round(x, ndigits=None):
    if isinstance(x, Expr):
        if ndigits is not None:
            raise TypeError(
                "scriptvedit.round: Expr では ndigits を指定できません")
        return _make_func("round", [x])
    if ndigits is None:
        return _builtins.round(x)
    return _builtins.round(x, ndigits)

def pow(x, y, mod=None):
    if isinstance(x, Expr) or isinstance(y, Expr):
        if mod is not None:
            raise TypeError(
                "scriptvedit.pow: Expr では3引数形式を指定できません")
        return _make_func("pow", [_to_expr(x), _to_expr(y)])
    if mod is None:
        return _builtins.pow(x, y)
    return _builtins.pow(x, y, mod)

# 定数
PI = 3.141592653589793
E = 2.718281828459045


# --- 条件分岐・比較 ---

def if_(cond, then_val, else_val):
    """条件分岐: cond≠0ならthen_val、そうでなければelse_val"""
    return _make_func("if", [_to_expr(cond), _to_expr(then_val), _to_expr(else_val)])

def lt(a, b):
    """a < b → 1.0, else → 0.0"""
    return _make_func("lt", [_to_expr(a), _to_expr(b)])

def gt(a, b):
    """a > b → 1.0, else → 0.0"""
    return _make_func("gt", [_to_expr(a), _to_expr(b)])

def lte(a, b):
    """a <= b → 1.0, else → 0.0"""
    return _make_func("lte", [_to_expr(a), _to_expr(b)])

def gte(a, b):
    """a >= b → 1.0, else → 0.0"""
    return _make_func("gte", [_to_expr(a), _to_expr(b)])

def eq_(a, b):
    """a == b → 1.0, else → 0.0"""
    return _make_func("eq", [_to_expr(a), _to_expr(b)])

def neq(a, b):
    """a != b → 1.0, else → 0.0"""
    return _make_func("not", [_make_func("eq", [_to_expr(a), _to_expr(b)])])

def and_(a, b):
    """論理AND（a≠0 かつ b≠0 → 1.0, else → 0.0）

    ffmpeg の式評価器（eval.c）には and()/or() が存在せず Unknown function に
    なるため、0/1 を返す比較の積で表現する。eval_at の結果は従来と同値。
    """
    av = _to_expr(a)
    bv = _to_expr(b)
    return _make_func("gt", [abs(av), Const(0)]) * _make_func("gt", [abs(bv), Const(0)])

def or_(a, b):
    """論理OR（a≠0 または b≠0 → 1.0, else → 0.0）

    ffmpeg に or() は無いため |a|+|b| > 0 の比較で表現する。
    eval_at の結果は従来と同値。
    """
    av = _to_expr(a)
    bv = _to_expr(b)
    return _make_func("gt", [abs(av) + abs(bv), Const(0)])

def not_(a):
    """論理NOT"""
    return _make_func("not", [_to_expr(a)])

def between(x, lo, hi):
    """lo <= x <= hi → 1.0"""
    return _make_func("between", [_to_expr(x), _to_expr(lo), _to_expr(hi)])

def case(*when_then_pairs, default=0):
    """多岐条件分岐: ネストif_の糖衣

    使用例:
        case(
            (lt(u, 0.3), 0.5),      # u<0.3 → 0.5
            (lt(u, 0.7), 1.0),      # u<0.7 → 1.0
            default=0.2,            # それ以外 → 0.2
        )
    """
    result = _to_expr(default)
    for cond, val in reversed(when_then_pairs):
        result = if_(cond, val, result)
    return result

def sign(x):
    """符号関数: x>0→1, x==0→0, x<0→-1"""
    xv = _to_expr(x)
    return if_(gt(xv, 0), 1, if_(lt(xv, 0), -1, 0))

def random(seed=0):
    """疑似乱数 [0, 1)（ffmpegランタイムで評価）"""
    return _make_func("random", [_to_expr(seed)])


# --- DSL糖衣: パーセント記法 ---

class Percent:
    """パーセント記法: 50%P → 0.5"""
    def __rmod__(self, other):
        if isinstance(other, (int, float)):
            return other / 100.0
        return NotImplemented
    def __repr__(self):
        return "P"

P = Percent()


# --- 遅延解決の相互参照（関数本体からのみ使用: 循環importを避けるため末尾で束縛）---
