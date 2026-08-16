# -*- coding: utf-8 -*-
"""マニフェスト駆動の契約スモーク（監査項目23）

`describe()` は「ソースを全部読まなくてよい」ための API 契約なので、そこに
載っている名前は必ず呼べて、宣言どおりの型を返さなければならない。ところが
expr 98件のうち36件は tests/ に名前すら現れておらず、メソッドを1つ改名しても
テストは全緑のまま利用者のレイヤーだけ AttributeError になる状態だった。

このモジュールは describe() を parametrize して「呼べる・Expr が返る・ffmpeg 式
が出る」だけを固定する。中身の正しさ（数値）は easing の数値回帰テストが見る。
"""
import os

import pytest

import scriptvedit as sv
from scriptvedit import describe
from scriptvedit.expr import Const, Expr, Var

def _load_expr_entries():
    """describe() の expr セクションを取り出す（失敗しても収集は止めない）

    parametrize は収集時に評価されるため、ここで例外を投げると pytest が
    セッションごと中断してしまい、他のテストの結果まで見えなくなる。
    失敗は test_describe_is_buildable が赤くして報告する。
    """
    try:
        return describe()["expr"], None
    except Exception as exc:      # noqa: BLE001 - 報告のために握る
        return [], exc


_EXPR_ENTRIES, _MANIFEST_ERROR = _load_expr_entries()


def test_describe_is_buildable():
    """describe() が例外なく構築できること（契約テストの前提）"""
    assert _MANIFEST_ERROR is None, (
        f"describe() が失敗します: {type(_MANIFEST_ERROR).__name__}: "
        f"{_MANIFEST_ERROR}")
    assert _EXPR_ENTRIES, "describe()['expr'] が空です"


def _dummy_fn(t):
    """引数に callable を要求する op 用のダミー（恒等関数）"""
    return t


# 引数名から実引数を決める。Expr のままでは成立しない（Python 側で数値比較や
# 分岐をする）パラメータだけを明示する。ここに無い必須引数は Var("u") を渡す。
_ARG_BY_NAME = {
    "n": 3,                       # 繰り返し回数（int 必須）
    "fn": _dummy_fn,
    "fn_a": _dummy_fn,
    "fn_b": _dummy_fn,
    "easing_func": _dummy_fn,
    "x1": 0.25, "y1": 0.1, "x2": 0.25, "y2": 1.0,   # ease_cubic_bezier
    "from_val": 0.0, "to_val": 1.0,                 # apply_easing
    "start": 0.0, "end": 1.0,                       # phase の区間
}

# 可変長引数のため既定引数だけでは construct できない op。
# **この除外リストが増えないことをテストで縛る**（肥大すると契約が骨抜きになる）。
_VARARG_ARGS = {
    "case": ((sv.gt(Var("u"), Const(0.5)), 1.0),),
    "keyframes": ((0, 0.0), (1, 1.0)),
    "max": (Var("u"), 0.5),
    "min": (Var("u"), 0.5),
    "sequence_param": ((0, 0.5, 1.0), (0.5, 1.0, 0.0)),
}


def _resolve(name):
    """manifest のエントリ名から実体を取り出す（Expr.xxx はメソッド）"""
    if name.startswith("Expr."):
        return getattr(Expr, name.split(".", 1)[1])
    return getattr(sv, name)


def _call(entry):
    """manifest の宣言どおりに呼び出し、戻り値を返す"""
    name = entry["name"]
    fn = _resolve(name)
    if name in _VARARG_ARGS:
        return fn(*_VARARG_ARGS[name])
    kwargs = {p: _ARG_BY_NAME.get(p, Var("u"))
              for p, meta in entry.get("params", {}).items()
              if meta.get("required")}
    if name.startswith("Expr."):
        return fn(Var("u"), **kwargs)
    return fn(**kwargs)


@pytest.mark.parametrize("entry", _EXPR_ENTRIES,
                         ids=[e["name"] for e in _EXPR_ENTRIES])
def test_expr_entry_is_callable_and_builds_ffmpeg(entry):
    """describe の expr が実際に呼べて ffmpeg 式になること

    改名・シグネチャ変更・NameError 級の破壊を必ず検出する
    （数値の正しさは check_easing_numeric_regression 等が見る）。
    """
    name = entry["name"]
    result = _call(entry)
    if name == "Expr.plot":
        # 唯一「Expr ではなく文字列（アスキーグラフ）を返す」エントリ
        assert isinstance(result, str) and result.strip()
        return
    if callable(result) and not isinstance(result, Expr):
        # イージング/シーケンス系は「Expr を返す関数」を返すファクトリ
        result = result(Var("u"))
    assert isinstance(result, Expr), f"{name}: Expr が返りません: {type(result)}"
    ff = result.to_ffmpeg("u")
    assert isinstance(ff, str) and ff.strip(), f"{name}: ffmpeg 式が空です"
    assert "None" not in ff, f"{name}: ffmpeg 式に None が混入: {ff}"
    assert "<" not in ff and ">" not in ff, (
        f"{name}: ffmpeg 式に Python オブジェクトの repr が混入: {ff}")


def test_vararg_exclusions_stay_small():
    """個別引数を用意する op が増えすぎないこと（契約の骨抜き防止）"""
    assert len(_VARARG_ARGS) <= 10, (
        "既定引数で呼べない expr が増えています。マニフェストの params に"
        "現れない可変長引数を減らすか、契約テストの方式を見直してください")
    for name in _VARARG_ARGS:
        assert any(e["name"] == name for e in _EXPR_ENTRIES), (
            f"{name} は describe に存在しません（除外リストの残骸）")


# --- project_methods: explain / inspect ---

def _planned_project(tmp_path):
    """text を1つ持つ最小プロジェクトを dry_run まで進めた状態で返す"""
    layer = tmp_path / "l_contract.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "t = text('契約', size=40, color='white')\n"
        "t.time(2) <= move(x=0.5, y=0.5, anchor='center') & fade(lambda u: u)\n",
        encoding="utf-8")
    p = sv.Project()
    p.configure(width=320, height=180, fps=10, background_color="black")
    p.layer(str(layer), priority=0)
    p.render(str(tmp_path / "contract.mp4"), dry_run=True)
    return p


def test_explain_reports_duration_source_and_overlay(tmp_path):
    """explain(obj) が u 正規化の分母と overlay 位置を説明すること"""
    p = _planned_project(tmp_path)
    assert p.objects, "レイヤーの Object が登録されていません"
    out = p.explain(p.objects[0])
    assert "u 正規化分母 dur" in out
    assert "overlay位置" in out
    with pytest.raises(TypeError):
        p.explain("Object ではない")


def test_inspect_returns_text_and_writes_html(tmp_path):
    """inspect() がテキスト、inspect(out_html=) が HTML を書いてパスを返すこと"""
    p = _planned_project(tmp_path)
    report = p.inspect()
    assert isinstance(report, str) and report.strip()
    html_path = str(tmp_path / "timeline.html")
    returned = p.inspect(out_html=html_path)
    assert os.path.exists(returned)
    with open(returned, encoding="utf-8") as f:
        assert "<html" in f.read().lower()


# --- シーケンス系イージングの数値回帰 ---

@pytest.mark.parametrize("u,expected", [
    (0.0, 0.0), (0.32, 0.0), (0.34, 1 / 3), (0.5, 1 / 3),
    (0.67, 2 / 3), (0.99, 2 / 3), (1.0, 1.0),
])
def test_steps_numeric(u, expected):
    """steps(3) は区間の切替点で 0 → 1/3 → 2/3 → 1 と階段状に上がる"""
    assert sv.steps(3)(Var("u")).eval_at(u) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize("u,expected", [
    (0.0, 0.0), (0.25, 0.5), (0.5, 1.0), (0.75, 0.5), (1.0, 0.0),
])
def test_bounce_numeric(u, expected):
    """bounce(1, 恒等) は 0→1→0 の三角波（往復）"""
    assert (sv.bounce(1, _dummy_fn)(Var("u")).eval_at(u)
            == pytest.approx(expected, abs=1e-9))


@pytest.mark.parametrize("u,expected", [
    (0.0, 0.0), (0.5, 0.5), (0.99, 0.99), (1.0, 0.0),
])
def test_repeat_once_is_identity(u, expected):
    """repeat(1, 恒等) は恒等（u=1.0 だけは鋸波が一周して 0 に戻る）"""
    assert (sv.repeat(1, _dummy_fn)(Var("u")).eval_at(u)
            == pytest.approx(expected, abs=1e-9))


@pytest.mark.parametrize("u,expected", [
    (0.0, 0.0), (0.25, 0.25), (1 / 3, 1 / 3), (0.5, 0.5), (1.0, 1.0),
])
def test_staircase_identity_is_continuous(u, expected):
    """staircase(3, 恒等) は各段の内側で恒等（段差の総和が 1 になる）"""
    assert (sv.staircase(3, _dummy_fn)(Var("u")).eval_at(u)
            == pytest.approx(expected, abs=1e-9))


@pytest.mark.parametrize("u,expected", [
    (0.0, 0.0), (0.2, 0.0), (0.4, 0.25), (0.6, 0.75), (0.8, 1.0), (1.0, 1.0),
])
def test_phase_remaps_interval(u, expected):
    """phase(0.3, 0.7, 恒等) は区間外を 0/1 にクリップし区間内を線形に伸ばす"""
    assert (sv.phase(0.3, 0.7, _dummy_fn)(Var("u")).eval_at(u)
            == pytest.approx(expected, abs=1e-9))


@pytest.mark.parametrize("u,expected", [
    (0.0, 0.0), (0.5, 0.5), (0.75, 0.75), (1.0, 1.0),
])
def test_apply_easing_maps_range(u, expected):
    """apply_easing(linear, 0, 1) は値域そのまま（範囲マッピングの恒等ケース）"""
    assert (sv.apply_easing(sv.linear, 0.0, 1.0)(Var("u")).eval_at(u)
            == pytest.approx(expected, abs=1e-9))


@pytest.mark.parametrize("u", [0.0, 0.25, 0.5, 0.75, 1.0])
def test_ease_spring_and_cubic_bezier_are_bounded(u):
    """ease_spring / ease_cubic_bezier が有限値を返し端点が 0/1 近傍にあること"""
    spring = sv.ease_spring()(Var("u")).eval_at(u)
    bezier = sv.ease_cubic_bezier(0.25, 0.1, 0.25, 1.0)(Var("u")).eval_at(u)
    assert -1.0 <= spring <= 2.0
    assert -0.01 <= bezier <= 1.01
    if u == 0.0:
        assert spring == pytest.approx(0.0, abs=1e-9)
        assert bezier == pytest.approx(0.0, abs=1e-6)
    if u == 1.0:
        assert bezier == pytest.approx(1.0, abs=1e-6)
