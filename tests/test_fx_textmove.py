# -*- coding: utf-8 -*-
"""text_transition() / odometer()（文字列を字単位で組み替える）

固定すること:
- 対応表（keep・move・replace・leave・enter とトークンの番号）を等式で
- 時間: コマ数 = (Σhold + Σduration) × fps、各状態に着く秒、最後のコマは最後の状態
- 画素: 各状態のコマが text_image と一致する / 止まっている字は途中のコマでも不変 /
  回る字は窓（縦横）の外に出ない / 入れ替わる組はほかのどの字とも重ならない /
  消える字は下の層 / 大きさの変わる字の縁取りの太さが飛ばない・0.5 倍の前後で揺れない /
  目に見える U+200C が遷移の最初のコマで消えない / scatter がキャンバスの縁に掛からない
- 鍵の安定（フォントの置き場所に依らない・効く引数で変わる・効かない引数では変わらない・
  Pillow の版で変わる）と決定性
- エラーケース
- 実レンダ（ffmpeg が書いた qtrle のコマが draw と一致する）と金型3枚

字は同梱の自作フォント tests/golden/textmove/svtm_block.ttf で描く（環境に依らない）。
システムの日本語フォントを使うテストだけ、無ければ skip する。
"""
import math
import os
import random
import shutil
import subprocess
import time

import pytest

pytest.importorskip("PIL", reason="Pillow が無い環境")
from PIL import Image  # noqa: E402

import scriptvedit as sv  # noqa: E402
import scriptvedit.framekit as fk  # noqa: E402
from scriptvedit import fx_textmove as tm  # noqa: E402
from scriptvedit.cache import _file_fingerprint  # noqa: E402
from scriptvedit.context import _exec_stack, activate, current_project  # noqa: E402
from scriptvedit.text import _resolve_font  # noqa: E402

from framekit_golden import assert_golden  # noqa: E402

_TESTS = os.path.dirname(os.path.abspath(__file__))
BLOCK = os.path.join(_TESTS, "golden", "textmove", "svtm_block.ttf")
_HAS_FFMPEG = (shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None)


@pytest.fixture(autouse=True)
def _restore_project_globals():
    """各テスト後に Project の暗黙登録先と実行スタックを戻す"""
    old_current = current_project()
    old_stack = list(_exec_stack)
    activate(None)
    _exec_stack[:] = []
    try:
        yield
    finally:
        activate(old_current)
        _exec_stack[:] = old_stack


@pytest.fixture
def dry():
    """dry_run の Project（生成はせず、draw は framekit.draw_frame で直接呼ぶ）"""
    p = sv.Project()
    p.configure(width=1280, height=720, fps=30)
    p._dry_run = True
    return p


@pytest.fixture
def np():
    return pytest.importorskip("numpy", reason="numpy が無い環境")


@pytest.fixture
def draws(np):
    """コマを描くテスト（numpy・opencv が要る）"""
    pytest.importorskip("cv2", reason="numpy・opencv が無い環境")
    return np


@pytest.fixture
def jp_font():
    try:
        return _resolve_font(None)
    except FileNotFoundError as e:
        pytest.skip(f"日本語フォントが無い環境: {str(e).splitlines()[0]}")


def tt(states, **kw):
    kw.setdefault("font", BLOCK)
    return sv.text_transition(states, **kw)


def _ref(np, state, fig, **fmt):
    """その状態の text_image（端のコマと一致するはずの絵）"""
    fmt.setdefault("font", BLOCK)
    obj = sv.text_image(state, **fig.text_image_kwargs, **fmt)
    with Image.open(obj.source) as im:
        return np.asarray(im.convert("RGBA"), dtype=np.uint8)


def _texts(fig, k, kind):
    pr = fig.pairs[k][kind]
    if kind in ("leave", "enter"):
        side = 0 if kind == "leave" else 1
        return [fig.tokens[k + side][i] for i in pr]
    return [(fig.tokens[k][i], fig.tokens[k + 1][j]) for i, j in pr]


# --- 対応表 ---------------------------------------------------------------

def test_pairs_fundation_to_foundation(dry):
    fig = tt(["Fundation", "Foundation"]).figure
    pr = fig.pairs[0]
    assert pr["enter"] == [1] and fig.tokens[1][1] == "o"
    assert len(pr["keep"]) == 9
    assert pr["move"] == [] and pr["replace"] == [] and pr["leave"] == []
    assert all(a == b for a, b in _texts(fig, 0, "keep"))


def test_pairs_parseint_keeps_the_five(dry):
    fig = tt(["0.0000005", "5e-7"]).figure
    pr = fig.pairs[0]
    assert pr["keep"] == [(8, 0)]
    assert fig.tokens[0][8] == "5" and fig.tokens[1][0] == "5"
    assert pr["replace"] == [] and pr["move"] == []
    assert pr["leave"] == list(range(8)) and pr["enter"] == [1, 2, 3]


def test_pairs_sort_code_unit_moves_ten_and_two(dry):
    fig = tt(["[1, 2, 10]", "[1, 10, 2]"], unit="code").figure
    assert fig.tokens[0] == ["[", "1", ",", " ", "2", ",", " ", "10", "]"]
    pr = fig.pairs[0]
    assert sorted(_texts(fig, 0, "move")) == [("10", "10"), ("2", "2")]
    assert pr["replace"] == [] and pr["leave"] == [] and pr["enter"] == []
    assert len(pr["keep"]) == 7


def test_pairs_position_right_anchor(dry):
    fig = tt(["2,147,483,647", "-2,147,483,648"], match="position", anchor="right").figure
    pr = fig.pairs[0]
    assert pr["enter"] == [0] and fig.tokens[1][0] == "-"
    assert pr["replace"] == [(12, 13)]
    assert _texts(fig, 0, "replace") == [("7", "8")]
    assert len(pr["keep"]) == 12 and pr["leave"] == [] and pr["move"] == []


def test_odometer_binary_rolls_all_32_digits_from_the_right(dry):
    fig = sv.odometer(2**31 - 1, 2**31, base=2, digits=32, font=BLOCK, size=24).figure
    pr = fig.pairs[0]
    assert len(pr["replace"]) == 32
    assert pr["keep"] == [] and pr["enter"] == [] and pr["leave"] == []
    rolls = [s for s in fig.schedule if s["kind"] == "replace"]
    assert len(rolls) == 32 and all(s["mode"] == "roll" for s in rolls)
    # 開始は右（b の番号が大きい桁）から単調に遅れる
    starts = [s["start"] for s in sorted(rolls, key=lambda s: -s["b"])]
    assert all(b > a for a, b in zip(starts, starts[1:]))
    assert starts[0] == 0.0


def test_odometer_grouped_binary_keeps_separators(dry):
    fig = sv.odometer(2**31 - 1, 2**31, base=2, digits=32, signed="twos", group=8, sep=" ",
                      font=BLOCK, size=24).figure
    assert "".join(fig.tokens[0]) == "01111111 11111111 11111111 11111111"
    assert "".join(fig.tokens[1]) == "10000000 00000000 00000000 00000000"
    assert len(fig.pairs[0]["replace"]) == 32 and len(fig.pairs[0]["keep"]) == 3


def test_edit_prefer_changes_alignment(dry):
    a = tt(["Fandation", "Foundation"], match="edit",
           prefer=("replace", "insert", "delete")).figure
    b = tt(["Fandation", "Foundation"], match="edit",
           prefer=("insert", "replace", "delete")).figure
    assert _texts(a, 0, "replace") == [("a", "u")] and _texts(a, 0, "enter") == ["o"]
    assert _texts(b, 0, "replace") == [("a", "o")] and _texts(b, 0, "enter") == ["u"]
    assert a.pairs[0]["replace"] != b.pairs[0]["replace"]
    assert len(a.pairs[0]["keep"]) == len(b.pairs[0]["keep"]) == 8


def test_lcs_tie_keeps_the_left_token_and_moves_the_other(dry):
    fig = tt(["ab", "ba"]).figure
    assert fig.pairs[0]["keep"] == [(0, 1)]
    assert fig.pairs[0]["move"] == [(1, 0)]


def test_lcs_is_optimal_on_random_strings():
    """自前の DP が最長の共通部分列を返す（difflib は LCS ではない）"""
    rng = random.Random(7)

    def lcs_len(a, b):
        L = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
        for i in range(len(a)):
            for j in range(len(b)):
                L[i + 1][j + 1] = (L[i][j] + 1 if a[i] == b[j]
                                   else max(L[i][j + 1], L[i + 1][j]))
        return L[-1][-1]

    for _ in range(200):
        a = [rng.choice("abc") for _ in range(rng.randint(0, 9))]
        b = [rng.choice("abc") for _ in range(rng.randint(0, 9))]
        pairs = tm._lcs(a, b)
        assert len(pairs) == lcs_len(a, b)
        assert all(a[i] == b[j] for i, j in pairs)
        assert all(i1 < i2 and j1 < j2 for (i1, j1), (i2, j2) in zip(pairs, pairs[1:]))


def test_auto_replace_needs_the_same_kind(dry):
    """隙間で位置がそろっても、数字と記号は入れ替えない（'0' が "'" へ回らない）"""
    fig = tt(["0.0000005", "'5e-7'"]).figure
    assert fig.pairs[0]["replace"] == []
    assert fig.pairs[0]["keep"] == [(8, 1)]


def test_kind_separates_cjk_from_latin():
    assert tm._kind("正") == "cjk" and tm._kind("表現") == "cjk" and tm._kind("か") == "cjk"
    assert tm._kind("C") == "alpha" and tm._kind("x_1") == "alpha"
    assert tm._kind("7") == "digit" and tm._kind(" ") == "space" and tm._kind("、") == "symbol"


def test_auto_does_not_replace_cjk_with_latin(dry, jp_font):
    """漢字が英字へ入れ替わる組は replace にしない（消える字と現れる字になる）"""
    fig = sv.text_transition(["犯人は、正規表現",
                              [("犯人は、", {}), ("Cloudflare", {"color": "#e0241b"})]],
                             font=jp_font, size=48).figure
    pr = fig.pairs[0]
    assert pr["replace"] == [] and len(pr["keep"]) == 4
    assert len(pr["leave"]) == 4 and len(pr["enter"]) == 10


def test_default_replace_rolls_only_digits(dry):
    """replace='auto'（既定）は数字どうしだけ roll、ほかは fade。'roll' を渡せば全部回す"""
    modes = {s["mode"] for s in tt(["WWWW", "iiii"]).figure.schedule}
    assert modes == {"fade"}
    assert {s["mode"] for s in tt(["20", "21"]).figure.schedule if s["kind"] == "replace"} \
        == {"roll"}
    assert {s["mode"] for s in tt(["WWWW", "iiii"], replace="roll").figure.schedule} \
        == {"roll"}
    assert {s["mode"] for s in tt(["a1", "b2"], match="position").figure.schedule} \
        == {"fade", "roll"}


def test_units_word_code_and_regex(dry):
    assert tt(["ab  cd", "x"], unit="word").figure.tokens[0] == ["ab", "  ", "cd"]
    code = tt(['f(x1, 2.5e-3, "a b")', "x"], unit="code").figure.tokens[0]
    assert code == ["f", "(", "x1", ",", " ", "2.5e-3", ",", " ", '"a b"', ")"]
    rx = tt([r"^\s+$", "x"], unit=r"\\.|.").figure.tokens[0]
    assert rx == ["^", r"\s", "+", "$"]


def test_manual_pairs_crossing_become_move(dry):
    fig = tt(["ab", "ba"], match=[(0, 1), (1, 0)]).figure
    assert fig.pairs[0]["move"] == [(0, 1), (1, 0)] and fig.pairs[0]["keep"] == []
    fig = tt(["ab", "ax"], match=[(0, 0), (1, 1)]).figure
    assert fig.pairs[0]["keep"] == [(0, 0)] and fig.pairs[0]["replace"] == [(1, 1)]


def test_pairs_per_transition_for_three_states(dry):
    fig = tt(["0.0000005", "'5e-7'", "5"], hold=0.2).figure
    assert len(fig.pairs) == 2
    assert fig.pairs[1]["keep"] == [(1, 0)]
    assert fig.pairs[1]["leave"] == [0, 2, 3, 4, 5]


# --- 時間 ---------------------------------------------------------------

def test_frame_count_is_sum_of_hold_and_duration(dry):
    o = tt(["0.0000005", "'5e-7'", "5"], hold=[0.4, 0.6, 0.5], duration=[1.0, 0.8])
    fig = o.figure
    assert fig.n_frames == round((0.4 + 0.6 + 0.5 + 1.0 + 0.8) * 30) == 99
    assert fig.starts == pytest.approx([0.0, 1.4, 2.8])
    assert fig.state_frames == [0, 42, 84]
    assert o.length() == pytest.approx(99 / 30)


def test_last_state_is_on_the_last_frame_when_hold_is_zero(dry):
    fig = tt(["20", "21"]).figure
    assert fig.n_frames == 36
    assert fig.state_frames == [0, 35]
    assert fig.starts[-1] == pytest.approx(35 / 30)


def test_schedule_stagger_and_layers(dry):
    fig = tt(["abc", "xbz yy"], leave="fall", replace="fade").figure
    kinds = {s["kind"] for s in fig.schedule}
    assert "leave" not in kinds or all(
        s["layer"] == "below" for s in fig.schedule if s["kind"] == "leave")
    fig = tt(["a b c d", "x"], leave="fall").figure
    leaves = [s for s in fig.schedule if s["kind"] == "leave"]
    assert leaves and all(s["layer"] == "below" for s in leaves)
    starts = [s["start"] for s in sorted(leaves, key=lambda s: s["a"])]
    assert all(b > a for a, b in zip(starts, starts[1:]))      # 左から遅れる


# --- 画素 ---------------------------------------------------------------

_EQ_CASES = [
    (["Fundation", "Foundation"], {}),
    (["[1, 2, 10]", "[1, 10, 2]"], {"unit": "code"}),
    (["2,147,483,647", "-2,147,483,648"], {"match": "position", "anchor": "right"}),
    ([r"\s+$", [r"\s+", ("+", {"color": "#e0241b"}), "$"]], {"enter": "drop"}),
    (["0.0000005", [("'5e-7'", {"size": 96, "color": "#9aa0a8"})], "5"],
     {"hold": [0.2, 0.3, 0.2], "anchor": "center", "leave": "scatter"}),
    (["AV To", "To AV\nok"], {"leave": "fall"}),
]


@pytest.mark.parametrize("border,shadow", [(0, (0, 0)), (4, (3, 3))])
@pytest.mark.parametrize("states,kw", _EQ_CASES)
def test_state_frames_match_text_image(dry, draws, states, kw, border, shadow):
    np = draws
    fmt = {"size": 64, "border": border, "shadow": shadow}
    o = tt(states, **kw, **fmt)
    fig = o.figure
    for s, state in enumerate(states):
        got = fk.draw_frame(o, fig.state_frames[s])
        want = _ref(np, state, fig, **fmt)
        assert got.shape == want.shape
        assert np.array_equal(got, want), f"状態 {s} のコマが text_image と違う"


def test_state_frames_match_text_image_with_system_font(dry, draws, jp_font):
    np = draws
    fmt = {"font": jp_font, "size": 72, "border": 4}
    states = ["犯人は、正規表現", [("犯人は、", {}), ("Cloudflare", {"color": "red"})]]
    o = sv.text_transition(states, anchor="center", **fmt)
    fig = o.figure
    for s, state in enumerate(states):
        assert np.array_equal(fk.draw_frame(o, fig.state_frames[s]), _ref(np, state, fig, **fmt))


def test_text_image_kwargs_combine_with_fmt(dry, draws):
    """fmt に padding / align を渡しても text_image(状態, **fmt, **text_image_kwargs) が
    そのまま呼べ（同じ名前が2回渡らない）、各状態のコマと一致する"""
    np = draws
    fmt = {"font": BLOCK, "size": 48, "padding": (20, 12), "align": "center", "border": 2}
    states = ["ab", "xyz"]
    o = sv.text_transition(states, anchor="center", **fmt)
    kw = o.figure.text_image_kwargs
    assert "padding" not in kw and "align" not in kw and kw["canvas"] == o.figure.canvas
    for s, state in enumerate(states):
        img = sv.text_image(state, **fmt, **kw)
        with Image.open(img.source) as im:
            want = np.asarray(im.convert("RGBA"), dtype=np.uint8)
        assert np.array_equal(fk.draw_frame(o, o.figure.state_frames[s]), want)
    kw = tt(states).figure.text_image_kwargs
    assert set(kw) == {"canvas", "align", "padding"}


def test_glyph_ink_is_judged_by_the_glyph(dry):
    """インクの有無は字形で決める（U+200C でも目に見える字形のフォントがある）"""
    from PIL import ImageFont
    f = ImageFont.truetype(BLOCK, 64)
    zwnj = chr(0x200C)
    assert not tm._glyph_has_ink(("blk", 0, 64, None), f, zwnj)     # 同梱フォントは空の字形
    assert not tm._glyph_has_ink(("blk", 0, 64, None), f, " ")
    assert tm._glyph_has_ink(("blk", 0, 64, None), f, "a")
    path = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", "consolab.ttf")
    if not os.path.isfile(path):
        pytest.skip("フォント consolab.ttf が無い環境")
    fc = ImageFont.truetype(path, 96)
    assert tm._glyph_has_ink((path, 0, 96, None), fc, zwnj)


def test_visible_zwnj_does_not_vanish_on_the_first_moving_frame(dry, draws):
    """Consolas Bold の U+200C（縦棒の字形）は、端のコマ（text_image が描く）にあるなら、
    遷移の最初のコマでも消えない（以前はカテゴリ Cf を「インク無し」として捨てていた）"""
    np = draws
    path = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", "consolab.ttf")
    if not os.path.isfile(path):
        pytest.skip("フォント consolab.ttf が無い環境")
    zwnj = chr(0x200C)
    official = r"^[\s" + zwnj + r"]+|[\s" + zwnj + r"]+$"
    o = sv.text_transition([official, [(r"\s+$", {"size": 170})]], unit=r"\\.|.",
                           leave="fall", anchor="center", font=path, size=96, hold=[0.6, 0.8],
                           duration=1.6, border=4)
    plan = o._textmove_plan
    zs = [c for c in plan.chars[0] if c.ch == zwnj]
    assert zs and all(c.ink for c in zs)
    f0 = o.figure.state_frames[0] + round(0.6 * 30) - 1         # hold の最後のコマ
    a, b = fk.draw_frame(o, f0 + 1), fk.draw_frame(o, f0 + 2)    # 遷移の 0 コマ目と 1 コマ目
    for c in zs:
        box = (slice(int(c.Y - 110), int(c.Y + 30)), slice(c.X - 8, c.X + 48))
        sa = int(a[box][..., 3].astype(np.int64).sum())
        sb = int(b[box][..., 3].astype(np.int64).sum())
        assert sa > 0 and abs(sb - sa) < 0.1 * sa, f"ZWNJ の周りの α {sa} → {sb}"


def test_glyphs_use_basic_layout(dry):
    """字の整形は text_image と同じ BASIC（合字を作らない）。1字ずつ描いても区間を
    まとめて描いても字形が同じ（RAQM だと区間には合字が入りうる）"""
    from PIL import ImageFont
    plan = tt(["office", "offline"])._textmove_plan
    assert plan.fonts
    assert all(f.layout_engine == ImageFont.Layout.BASIC for f in plan.fonts.values())


def test_hold_frames_are_all_the_state(dry, draws):
    np = draws
    o = tt(["20", "21", "22"], hold=[0.2, 0.3, 0.2], duration=0.5, size=48)
    fig = o.figure
    first = fk.draw_frame(o, 0)
    for i in range(0, 6):
        assert np.array_equal(fk.draw_frame(o, i), first)
    second = fk.draw_frame(o, fig.state_frames[1])
    for i in range(fig.state_frames[1], fig.state_frames[1] + 9):
        assert np.array_equal(fk.draw_frame(o, i), second)


@pytest.mark.parametrize("anchor", ["left", "right"])
@pytest.mark.parametrize("border,shadow", [(0, (0, 0)), (4, (0, 0)), (4, (3, 3))])
def test_static_tokens_do_not_change_in_mid_frames(dry, draws, border, shadow, anchor):
    """止まっている字の画素は、途中のコマでも端のコマと同じ。

    anchor='left' なら先頭の F、'right' なら末尾の X が止まっている（幅が変わっても動かない）。
    隣の字の縁取りが届く範囲（border + 1px）は除いて比べる。
    """
    np = draws
    o = tt(["Fundation is X", "Foundation is X"], size=64, border=border, shadow=shadow,
           anchor=anchor, hold=[0.2, 0.2])
    fig = o.figure
    plan = o._textmove_plan
    assert plan.statics[0], "止まっている字が無い"
    frames = [fk.draw_frame(o, i) for i in range(fig.n_frames)]
    if anchor == "left":
        tok = plan.toks[0][0]
        c = tok.chars[0]
        x0, x1 = 0, c.X + int(c.adv) - border - 1
    else:
        tok = plan.toks[0][-1]
        c = tok.chars[0]
        x0, x1 = c.X + border + 1, fig.canvas[0]
    assert tok.text == ("F" if anchor == "left" else "X")
    assert (plan.toks[0].index(tok), plan.toks[1].index(plan.toks[1][0 if anchor == "left"
                                                                       else -1])) \
        in plan.statics[0]
    ref = frames[0][:, x0:x1]
    assert ref[..., 3].any()
    for i, fr in enumerate(frames):
        assert np.array_equal(fr[:, x0:x1], ref), f"コマ {i} で {tok.text} が変わった"


def test_roll_stays_inside_the_cell(dry, draws):
    np = draws
    o = tt(["20", "21"], size=64, border=2, motion_blur=True)
    fig = o.figure
    plan = o._textmove_plan
    tok = plan.toks[0][1]
    c = tok.chars[0]
    pad = 2 + tm._ROLL_PAD
    top, bottom = c.Y - tok.asc - pad, c.Y + tok.desc + pad
    x0, x1 = c.X - 4, c.X + int(c.adv) + 4
    moved = False
    for i in range(1, fig.n_frames - 1):
        fr = fk.draw_frame(o, i)
        col = fr[:, x0:x1, 3]
        assert not col[:max(0, int(math.floor(top)))].any(), f"コマ {i}: マスの上に出た"
        assert not col[int(math.ceil(bottom)):].any(), f"コマ {i}: マスの下に出た"
        moved = moved or not np.array_equal(fr, fk.draw_frame(o, 0))
    assert moved


def _max_overlap(o, steps=200):
    """遷移の途中で、動くもの・止まっている字のすべての組の字の箱（Pillow の getbbox。
    縁取りを除く）の重なりの最大（px）と、その時の (u, 組)"""
    plan = o._textmove_plan
    geom = tm._Geom(plan)
    worst = (0.0, None)
    for k in range(len(plan.items)):
        ta, tb = plan.toks[k], plan.toks[k + 1]
        for q in range(1, steps):
            u = q / steps
            groups = []
            for it in plan.items[k]:
                name = ta[it.a].text if it.a is not None else tb[it.b].text
                boxes = [geom.box(d, stroke=False)
                         for d in tm._item_draws(plan, geom, it, ta, tb, u)]
                groups.append((f"{it.kind}:{name}", [b for b in boxes if b]))
            for i, _j in plan.statics[k]:
                boxes = [geom.box(d, stroke=False) for d in tm._exact_draws(ta[i], "hi")]
                groups.append((f"static:{ta[i].text}", [b for b in boxes if b]))
            for x in range(len(groups)):
                for y in range(x + 1, len(groups)):
                    for a in groups[x][1]:
                        for b in groups[y][1]:
                            ox = min(a[2], b[2]) - max(a[0], b[0])
                            oy = min(a[3], b[3]) - max(a[1], b[1])
                            if ox > 0 and oy > 0 and min(ox, oy) > worst[0]:
                                worst = (min(ox, oy), (u, groups[x][0], groups[y][0]))
    return worst


@pytest.mark.parametrize("states", [["8", "1"], ["1", "8"], ["88", "11"]])
def test_roll_stays_inside_the_window_horizontally(dry, draws, states):
    """回る字は窓で横にも切る（幅の違う字どうしで、古い字が狭まる窓からはみ出さない）。
    同梱フォントの '1' は '8' より狭い（プロポーショナル）"""
    np = draws
    o = tt(states, replace="roll", unit="code", size=64, border=3)
    plan = o._textmove_plan
    (it,) = plan.items[0]
    assert it.mode == "roll"
    for f in range(1, o.figure.n_frames - 1):
        _pr, e = tm._eased(plan, it, plan.table[f][2])
        x0, _y0, x1, _y1 = tm._lerp4(it.win[0], it.win[1], e)
        cols = fk.draw_frame(o, f)[..., 3].any(axis=0)
        xs = np.nonzero(cols)[0]
        if xs.size:
            assert xs.min() >= math.floor(x0) and xs.max() < math.ceil(x1), \
                f"コマ {f}: 窓 {x0:.1f}〜{x1:.1f} の外 {xs.min()}〜{xs.max()}"


def _alpha_sum(np, img):
    return float(img[..., 3].astype(np.float64).sum()) / 255.0


@pytest.mark.parametrize("grow", [True, False])
@pytest.mark.parametrize("border", [4, 8])
def test_scaled_glyph_keeps_border_width(dry, draws, grow, border):
    """大きさの変わる字の縁取りは途中でも border px のまま。端のコマと隣のコマで α の面積が
    飛ばない（以前は大きい字形を縁取りごと縮めたので、最初のコマで約 −25%）"""
    np = draws
    small, big = "ab", [("ab", {"size": 128})]
    o = tt([small, big] if grow else [big, small], size=64, border=border, duration=2.0,
           stagger=0)
    n = o.figure.n_frames
    pairs = [(0, 1)] if grow else [(n - 1, n - 2)]
    for i, j in pairs:
        a, b = _alpha_sum(np, fk.draw_frame(o, i)), _alpha_sum(np, fk.draw_frame(o, j))
        assert abs(b - a) / a < 0.03, f"コマ {i}→{j}: α の面積 {a:.0f} → {b:.0f}"


@pytest.mark.parametrize("border", [0, 4])
def test_scaling_is_smooth_across_half_size(dry, draws, border):
    """一定の速さで大きくなる字の √(α の面積) の2階差が小さい（0.5 倍の前後で縮小の補間が
    切り替わらない。以前は 0.6〜0.7 の揺れ）"""
    np = draws
    o = tt(["W", [("W", {"size": 192})]], size=64, border=border, duration=2.0,
           easing="linear", motion_blur=False)
    n = o.figure.n_frames
    s = [math.sqrt(_alpha_sum(np, fk.draw_frame(o, i))) for i in range(n)]
    d2 = [s[i + 1] - 2 * s[i] + s[i - 1] for i in range(2, n - 2)]
    assert max(abs(v) for v in d2) < 0.2


def test_scatter_stays_inside_the_canvas(dry, draws):
    """scatter は拡大・回転した後の外接（縁取りを含む）でキャンバスに収める"""
    o = tt(["Wjgy Wjgy", "x"], size=64, border=6, leave="scatter")
    for i in range(o.figure.n_frames):
        a = fk.draw_frame(o, i)[..., 3]
        assert not (a[0].any() or a[-1].any() or a[:, 0].any() or a[:, -1].any()), \
            f"コマ {i}: キャンバスの縁に字が掛かった"


_SWAP_CASES = [
    (["[1, 2, 10]", "[1, 10, 2]"], {"unit": "code"}),
    (["[10, 2]", "[2, 10]"], {"unit": "code"}),
    (["[3, 1, 2]", "[1, 2, 3]"], {"unit": "code"}),
    (["alpha beta gamma", "gamma beta alpha"], {"unit": "word"}),
    (["x = [1, 2, 10]", "y = [1, 10, 2]"], {"unit": "code", "leave": "fall", "enter": "drop"}),
    (["[1, 2, 10]", "[1, 10, 2]"], {"unit": "code", "move": "arc"}),
]


@pytest.mark.parametrize("size,border", [(64, 0), (150, 5)])
@pytest.mark.parametrize("states,kw", _SWAP_CASES)
def test_moves_do_not_touch_anything(dry, states, kw, size, border):
    """入れ替わる組は、ほかの字（止まっている字・滑る読点・消える字・現れる字・相手）の
    どれとも重ならない（以前は '2' が下の弧の降り始めに横へ速く動き、滑る ',' を突き抜けた）"""
    o = tt(states, size=size, border=border, **kw)
    worst = _max_overlap(o)
    assert worst[0] == 0.0, f"重なり {worst[0]:.2f}px: {worst[1]}"


@pytest.mark.parametrize("font,size", [("consolab.ttf", 150), ("arialbd.ttf", 120),
                                       ("consolab.ttf", 64)])
def test_moves_do_not_touch_anything_with_system_fonts(dry, font, size):
    """レビューで重なりを測ったフォント（Consolas Bold 150px で最大 45px 重なっていた）"""
    path = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", font)
    if not os.path.isfile(path):
        pytest.skip(f"フォント {font} が無い環境")
    o = sv.text_transition(["[1, 2, 10]", "[1, 10, 2]"], unit="code", font=path, size=size,
                           border=5)
    worst = _max_overlap(o)
    assert worst[0] == 0.0, f"重なり {worst[0]:.2f}px: {worst[1]}"


def test_swap_pair_lifts_apart_and_keeps_slide_while_lifted(dry):
    """左へ行く '10' は上、右へ行く '2' は下へ離れ、残る字は組が離れきっている間だけ滑る"""
    o = tt(["[1, 2, 10]", "[1, 10, 2]"], unit="code", size=64, border=3)
    plan = o._textmove_plan
    ta, tb = plan.toks
    items = {ta[it.a].text: it for it in plan.items[0] if it.kind == "move"}
    assert set(items) == {"10", "2"}
    base = ta[0].chars[0].Y
    assert items["10"].lev < base < items["2"].lev
    for it in items.values():
        # 離れる高さで、行の字（縁取りを含む）と重ならない
        mt, mb = it.ext
        line = [tm._Geom(plan).box(d) for t in ta for d in tm._exact_draws(t, "hi")]
        line = [b for b in line if b]
        if it.lev > base:
            assert it.lev + mt > max(b[3] for b in line)
        else:
            assert it.lev + mb < min(b[1] for b in line)
    t0 = min(it.t0 for it in items.values())
    t1 = max(it.t1 for it in items.values())
    for it in plan.items[0]:
        if it.kind == "keep":
            assert it.t0 >= t0 + tm._LIFT * (t1 - t0) - 1e-9
            assert it.t1 <= t1 - tm._LIFT * (t1 - t0) + 1e-9


def _lifts(o):
    """入れ替わる組ごとの (離れる量, 行の字をちょうど避ける量, 行の字の高さ)"""
    plan = o._textmove_plan
    geom = tm._Geom(plan)
    out = []
    for k, items in enumerate(plan.items):
        ta, tb = plan.toks[k], plan.toks[k + 1]
        for it in items:
            if it.kind != "move" or it.lev is None:
                continue
            a, b = ta[it.a], tb[it.b]
            cs, _ext = tm._route_candidates(geom, it, a, b, ta, tb, 0.0)
            ya, yb = a.chars[0].Y, b.chars[0].Y
            ref = max(ya, yb) if it.arc > 0 else min(ya, yb)
            row_lh = max(t.lh for ts, ln in ((ta, a.line), (tb, b.line)) for t in ts
                         if t.line == ln)
            out.append((abs(it.lev - ref), abs(cs[0] - ref), row_lh))
    return out


@pytest.mark.parametrize("states,kw", _SWAP_CASES)
def test_swap_lift_stays_within_swing(dry, states, kw):
    """入れ替わる組は swing × 行の字の高さ（既定 1 倍）より遠くへ振れない（行の字を
    ちょうど避ける量より浅くはしない）。以前は '[1, 2, 10]' の組が字の 2.2〜2.5 倍も
    上下に振れ、size=110 で画面の下で切れた"""
    o = tt(states, size=64, border=3, **kw)
    lifts = _lifts(o)
    assert lifts
    for lift, clear, row_lh in lifts:
        assert clear - 1e-6 <= lift <= max(clear, tm._SWING * row_lh) + 1e-6


def test_swap_takes_the_shallowest_clear_route_and_keeps_the_canvas_small(dry):
    """隣の字と接したまま縦に離れるだけの間は重なりに数えない。行の字をちょうど避ける
    いちばん浅い高さで足りるので、そこを選ぶ（キャンバスも低くなる）"""
    for size in (80, 110):
        o = tt(["[1, 2, 10]", "[1, 10, 2]"], unit="code", anchor="center", size=size,
               border=4, hold=[0.2, 0.2], duration=1.0)
        for lift, clear, _lh in _lifts(o):
            assert lift == pytest.approx(clear)
        assert o.figure.canvas[1] < 3.6 * size, o.figure.canvas
        assert _max_overlap(o)[0] == 0.0


def test_swing_bounds_the_route_candidates(dry):
    o = tt(["[1, 2, 10]", "[1, 10, 2]"], unit="code", size=64, border=3)
    plan = o._textmove_plan
    geom = tm._Geom(plan)
    ta, tb = plan.toks
    it = next(i for i in plan.items[0] if i.kind == "move")
    a, b = ta[it.a], tb[it.b]
    ref = max(a.chars[0].Y, b.chars[0].Y) if it.arc > 0 else min(a.chars[0].Y, b.chars[0].Y)
    depth = [abs(c - ref) for c in tm._route_candidates(geom, it, a, b, ta, tb, 0.0)[0]]
    assert len(depth) == 1                                     # 0 はちょうど避ける高さだけ
    allc = [abs(c - ref) for c in tm._route_candidates(geom, it, a, b, ta, tb, 10.0)[0]]
    assert allc == pytest.approx([f * depth[0] for f in tm._ROUTE_DEPTHS])
    row_lh = max(t.lh for t in ta)
    mid = (1.5 * depth[0]) / row_lh                            # 1.3 倍と 1.7 倍の間
    cs = [abs(c - ref) for c in tm._route_candidates(geom, it, a, b, ta, tb, mid)[0]]
    assert cs == pytest.approx([depth[0], 1.3 * depth[0], 1.5 * depth[0]])


def test_swing_is_in_the_key_only_with_swaps(dry):
    assert tt(["20", "21"], swing=2.0).source == tt(["20", "21"]).source
    s = ["[1, 2, 10]", "[1, 10, 2]"]
    assert tt(s, unit="code", swing=2.0).source != tt(s, unit="code").source


def test_stagger_spread_is_at_most_30_percent(dry):
    """字が多くても、遅れの広がりは duration の 30% まで（既定 0.02 秒ずつ）"""
    fig = tt(["abcdefghijklmnopqrstuvwxyz", "x"], leave="fall", duration=1.0).figure
    starts = sorted(s["start"] for s in fig.schedule if s["kind"] == "leave")
    assert len(starts) > 20
    assert starts[-1] - starts[0] <= 0.3 * 1.0 + 1e-6
    fig = tt(["abc", "x"], leave="fall", duration=1.0).figure
    starts = sorted(s["start"] for s in fig.schedule if s["kind"] == "leave")
    assert starts[1] - starts[0] == pytest.approx(tm._STAGGER, abs=1 / 30)


def test_roll_stays_inside_the_digit_band(dry, draws):
    """回る字の窓の縦は数字の帯（縁取りを含む '0'〜'9' のインク + size × 0.08）。字のマス
    （ascent + descent）まで流すと、ascent の大きいフォントで行の上下へ大きく流れ出た"""
    np = draws
    o = tt(["20", "21"], size=64, border=2, motion_blur=True)
    plan = o._textmove_plan
    tok = plan.toks[0][1]
    c = tok.chars[0]
    band = tm._Geom(plan).digit_band(c.fkey)
    pad = tm._ROLL_BAND_PAD * 64 + tm._ROLL_PAD + 2
    top, bottom = c.Y + band[0] - pad, c.Y + band[1] + pad
    x0, x1 = c.X - 4, c.X + int(c.adv) + 4
    rolled = False
    for i in range(1, o.figure.n_frames - 1):
        col = fk.draw_frame(o, i)[:, x0:x1, 3]
        assert not col[:max(0, int(math.floor(top)))].any(), f"コマ {i}: 帯の上に出た"
        assert not col[int(math.ceil(bottom)):].any(), f"コマ {i}: 帯の下に出た"
        rolled = rolled or not np.array_equal(fk.draw_frame(o, i), fk.draw_frame(o, 0))
    assert rolled
    assert bottom - top < tok.asc + tok.desc


def test_leave_draws_are_calm(dry):
    """fall は行の高さの 0.6 倍・6 度まで、scatter は 20 度・1.1 倍まで（途中のコマを
    騒がしくしない）"""
    o = tt(["abcd", "x"], leave="fall", size=64)
    plan = o._textmove_plan
    geom = tm._Geom(plan)
    ta, tb = plan.toks
    for it in plan.items[0]:
        if it.kind != "leave":
            continue
        for u in (0.5, 0.9, 0.99):
            for d in tm._item_draws(plan, geom, it, ta, tb, u):
                assert abs(d.angle) <= tm._FALL_TILT + 1e-9
                assert d.Y - ta[it.a].chars[0].Y <= tm._FALL_RATIO * ta[it.a].lh + 1e-6
    o = tt(["abcd", "x"], leave="scatter", size=64)
    plan = o._textmove_plan
    geom = tm._Geom(plan)
    ta, tb = plan.toks
    for it in plan.items[0]:
        if it.kind != "leave":
            continue
        for d in tm._item_draws(plan, geom, it, ta, tb, it.t1 - 1e-6):
            assert abs(d.angle) <= tm._SCATTER_SPIN + 1e-9
            assert d.scale <= 1.0 + tm._SCATTER_GROW + 1e-9


def test_leave_tokens_are_drawn_below(dry, draws):
    """消える字は下の層: 残る字の縁取りより下に置く（重なった所は残る字の縁取りの色）"""
    np = draws
    o = tt(["AB", "B"], size=64, border=6, border_color="#ff0000", leave="fade",
           duration=2.0)
    fig = o.figure
    assert all(s["layer"] == "below" for s in fig.schedule if s["kind"] == "leave")
    # 'A' が薄れ始めたコマ: 'A' の塗り（白）と 'B' の縁取り（赤）が重なる所は赤が上
    plan = o._textmove_plan
    b = plan.toks[0][1].chars[0]
    fr = fk.draw_frame(o, 3).astype(int)
    band = fr[:, b.X - 3:b.X + 1]
    a = band[..., 3] > 200
    assert a.any()
    assert (band[a][:, 0] >= band[a][:, 1]).all()        # 赤みが勝つ（白の塗りが上に来ない）


def test_motion_blur_only_on_fast_motion(dry, draws):
    np = draws
    slow = [tt(["Fundation", "Foundation"], duration=3.0, motion_blur=mb) for mb in (True, False)]
    for i in range(slow[0].figure.n_frames):
        assert np.array_equal(fk.draw_frame(slow[0], i), fk.draw_frame(slow[1], i))
    fast = [tt(["[1, 2, 10]", "[10, 2, 1]"], unit="code", duration=0.3, motion_blur=mb)
            for mb in (True, False)]
    n = fast[0].figure.n_frames
    assert any(not np.array_equal(fk.draw_frame(fast[0], i), fk.draw_frame(fast[1], i))
               for i in range(1, n - 1))
    assert np.array_equal(fk.draw_frame(fast[0], n - 1), fk.draw_frame(fast[1], n - 1))


def test_frames_are_deterministic(dry, draws):
    np = draws
    kw = dict(leave="scatter", enter="rise", unit="code", size=48, border=2)
    a = tt(["x = [1, 2, 10]", "y = sorted(x)"], **kw)
    b = tt(["x = [1, 2, 10]", "y = sorted(x)"], **kw)
    assert a.source == b.source
    for i in range(0, a.figure.n_frames, 5):
        assert np.array_equal(fk.draw_frame(a, i), fk.draw_frame(b, i))


def test_100_tokens_render_fast_enough(dry, draws):
    """トークン100個で1コマ 30ms 以下（実測 10〜23ms。CI の揺れを見て 2 倍まで許す）"""
    a = "const total = items.map(x => x.price * x.qty)\n.filter(v => v > 0).reduce((s, v) => s + v, 0);"
    b = "let sum = items.filter(x => x.qty > 0)\n.map(x => x.qty * x.price).reduce((a, b) => a + b);"
    o = tt([a, b], size=40, border=3)
    assert len(o.figure.tokens[0]) >= 90
    n = o.figure.n_frames
    best = float("inf")
    # 1回目は字の絵の準備を含む。ほかの処理と CPU を取り合うと壁時計が伸びるので、
    # このプロセスが使った CPU 時間の3回の最良で見る
    for _rep in range(3):
        t0 = time.process_time()
        for i in range(1, n - 1):
            fk.draw_frame(o, i)
        best = min(best, (time.process_time() - t0) / (n - 2) * 1000)
    assert best < 60, f"1コマ {best:.1f}ms（CPU 時間）"


# --- 鍵 -----------------------------------------------------------------

def test_key_is_stable_and_follows_what_matters(dry, tmp_path):
    base = tt(["20", "21"]).source
    assert tt(["20", "21"]).source == base
    moved_font = tmp_path / "somewhere" / "block.ttf"
    moved_font.parent.mkdir()
    shutil.copyfile(BLOCK, moved_font)
    assert tt(["20", "21"], font=str(moved_font)).source == base      # 置き場所は鍵に入らない
    for kw in ({"duration": 1.0}, {"hold": 0.1}, {"easing": "linear"}, {"unit": "word"},
               {"replace": "fade"}, {"roll_dir": "down"}, {"color": "red"},
               {"motion_blur": False}, {"size": 65}, {"anchor": "right"}):
        assert tt(["20", "21"], **kw).source != base, kw
    assert tt(["20", "22"]).source != base
    assert tt(["20", [("21", {"color": "red"})]]).source != base


def test_key_leaves_out_what_does_not_matter(dry):
    """効かない引数は鍵に入れない（同一出力なら同一鍵）: prefer は match='edit' のときだけ、
    replace / leave / enter / roll_dir はその種類の字があるときだけ、move は位置の変わる
    残る字があるときだけ、stagger は遅らせる字が2つ以上あるときだけ"""
    base = tt(["20", "21"]).source
    assert tt(["20", "21"], prefer=("insert", "replace", "delete")).source == base
    assert tt(["20", "21"], leave="fall", enter="drop", move="arc", stagger=0.1).source == base
    e1 = tt(["Fandation", "Foundation"], match="edit").source
    e2 = tt(["Fandation", "Foundation"], match="edit",
            prefer=("insert", "replace", "delete")).source
    assert e1 != e2
    grow = tt(["ab", "abc"]).source                   # 現れる字だけ（replace・leave は無い）
    assert tt(["ab", "abc"], replace="swap", leave="fall", roll_dir="down").source == grow
    assert tt(["ab", "abc"], enter="drop").source != grow


def test_key_has_the_pillow_version(dry, monkeypatch):
    """字の描画を Pillow / FreeType に任せているので、Pillow を更新したら作り直す
    （text_image の鍵と同じ。framekit.build(fonts=) が版を入れる）"""
    import PIL
    base = tt(["20", "21"]).source
    monkeypatch.setattr(PIL, "__version__", PIL.__version__ + ".test")
    assert tt(["20", "21"]).source != base


def test_dry_run_does_not_draw(dry, monkeypatch):
    def boom(self, i):
        raise AssertionError("dry_run で draw が呼ばれた")
    monkeypatch.setattr(tm._Renderer, "__call__", boom)
    o = tt(["20", "21"])
    assert o.source in dry._pending_compute_cmds
    assert o._text_image["size"] == 64 and o._text_image["width"] == o.figure.canvas[0]


# --- エラー -------------------------------------------------------------

@pytest.mark.parametrize("states,kw,exc,msg", [
    (["only"], {}, ValueError, "2つ以上"),
    (["a", "b"], {"max_width": 300}, ValueError, "max_width"),
    (["a", "b"], {"canvas": (100, 100)}, ValueError, "canvas"),
    (["a", "b"], {"colour": "red"}, TypeError, "知らない引数"),
    (["a", "b"], {"align": "right"}, ValueError, "anchor"),
    (["a", "b"], {"align": None}, ValueError, "省略"),
    (["a", "b"], {"padding": None}, ValueError, "省略"),
    (["a", "b"], {"replace": "auto2"}, ValueError, "replace"),
    (["a", "b"], {"unit": "chr"}, ValueError, "unit"),
    (["a", "b"], {"unit": "(["}, ValueError, "正規表現"),
    (["a", "b"], {"match": "lcs"}, ValueError, "match"),
    (["a", "b"], {"match": [(0, 5)]}, ValueError, "範囲外"),
    (["a", "b"], {"prefer": ("replace", "insert")}, ValueError, "prefer"),
    (["a", "b"], {"leave": "explode"}, ValueError, "leave"),
    (["a", "b"], {"enter": "zoom"}, ValueError, "enter"),
    (["a", "b"], {"replace": "flip"}, ValueError, "replace"),
    (["a", "b"], {"anchor": "middle"}, ValueError, "anchor"),
    (["a", "b"], {"roll_dir": "left"}, ValueError, "roll_dir"),
    (["a", "b"], {"motion_blur": 1}, TypeError, "motion_blur"),
    (["a", "b"], {"duration": [1, 2]}, ValueError, "遷移"),
    (["a", "b"], {"hold": [0.1]}, ValueError, "状態"),
    (["a", "b"], {"duration": 0}, ValueError, "duration"),
    (["a", "b"], {"duration": 0.03}, ValueError, "コマ"),
    (["20", "21"], {"duration": 0.2}, ValueError, "0.25"),
    (["a\nb\nc\nd", "x"], {}, ValueError, "行"),
    (["x" * 401, "y"], {"size": 4}, ValueError, "400"),
    (["a", "b"], {"easing": "ease_wobble"}, ValueError, "イージング"),
    (["a", "b"], {"swing": -0.5}, ValueError, "swing"),
    (["a", "b"], {"swing": "big"}, ValueError, "swing"),
])
def test_errors(dry, states, kw, exc, msg):
    with pytest.raises(exc, match=msg):
        tt(states, **kw)


@pytest.mark.parametrize("args,kw,exc,msg", [
    ((1.5, 2), {}, TypeError, "整数"),
    ((1, 2), {"base": 1}, ValueError, "base"),
    ((1, 2), {"digits": 0}, ValueError, "digits"),
    ((1, 2), {"signed": "twos"}, ValueError, "digits"),
    ((1, 2), {"signed": "twos", "digits": 8, "base": 10}, ValueError, "2の累乗"),
    ((1, 2), {"signed": "yes"}, ValueError, "signed"),
    ((1, 2), {"sep": " "}, ValueError, "group"),
    ((1, 2), {"group": 0}, ValueError, "group"),
    ((123, 4567), {"digits": 3}, ValueError, "digits"),
    ((2**31 - 1, 2**31), {"base": 2, "digits": 32, "ripple": 0.05}, ValueError, "ripple"),
    ((1, 2), {"carry": "chain"}, ValueError, "carry"),
    ((1, 2), {"stagger": 0.1}, TypeError, "知らない引数"),
])
def test_odometer_errors(dry, args, kw, exc, msg):
    kw.setdefault("font", BLOCK)
    with pytest.raises(exc, match=msg):
        sv.odometer(*args, **kw)


def test_odometer_text_errors(dry):
    with pytest.raises(TypeError, match="文字列"):
        sv.odometer.text("", "1", font=BLOCK)
    with pytest.raises(ValueError, match="roll_dir"):
        sv.odometer.text("1", "2", roll_dir="sideways", font=BLOCK)


def test_odometer_formats(dry):
    fig = sv.odometer(2147483647, -2147483648, group=3, font=BLOCK).figure
    assert "".join(fig.tokens[0]) == "2,147,483,647"
    assert "".join(fig.tokens[1]) == "-2,147,483,648"
    fig = sv.odometer(-1, 0, base=16, digits=4, signed="twos", font=BLOCK).figure
    assert "".join(fig.tokens[0]) == "FFFF" and "".join(fig.tokens[1]) == "0000"
    fig = sv.odometer(5, 12, signed=True, digits=3, font=BLOCK).figure
    assert "".join(fig.tokens[0]) == "+005" and "".join(fig.tokens[1]) == "+012"


def test_odometer_text_rolls_digits_and_rewinds_down(dry):
    fig = sv.odometer.text("2038-01-19 03:14:07", "1901-12-13 20:45:52", font=BLOCK).figure
    rolls = [s for s in fig.schedule if s["kind"] == "replace"]
    assert len(rolls) == 13 and all(s["mode"] == "roll" for s in rolls)
    # 数字でない字（- : 空白）は残る。プロポーショナルの数字（同梱フォントの '1'）の幅の差で
    # 押される字だけが滑る（回っている桁の間に）
    assert {s["mode"] for s in fig.schedule if s["kind"] != "replace"} <= {"slide"}
    assert len(fig.pairs[0]["keep"]) == 6
    assert tm._auto_roll_dir("2038-01-19 03:14:07", "1901-12-13 20:45:52") < 0
    assert tm._auto_roll_dir("20", "21") > 0
    assert tm._auto_roll_dir("2,147,483,647", "-2,147,483,648") < 0


# --- 実レンダ（ffmpeg が書いた qtrle のコマが draw と一致する）-------------------

@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg / ffprobe が無い環境")
def test_real_render_frames_match_draw(draws, tmp_path):
    np = draws
    kw = dict(unit="code", size=48, border=2, duration=0.6, hold=[0.1, 0.2])
    # 前の実行の生成物が残っていれば消す（いまの描画で作り直したものを比べる）
    p = sv.Project()
    p._dry_run = True
    stale = tt(["[1, 2, 10]", "[1, 10, 2]"], **kw).source
    if os.path.isfile(stale):
        os.remove(stale)
    activate(None)                   # Project なし → その場で .mov を生成する
    o = tt(["[1, 2, 10]", "[1, 10, 2]"], **kw)
    assert o.source == stale and os.path.isfile(o.source)
    w, h = o.figure.canvas
    n = o.figure.n_frames
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", o.source, "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        capture_output=True, check=True).stdout
    got = np.frombuffer(raw, np.uint8).reshape(-1, h, w, 4)
    assert got.shape[0] == n
    for i in (0, n // 3, n // 2, 2 * n // 3, n - 1):
        want = fk.draw_frame(o, i)
        # qtrle の argb は可逆。α=0 の画素の色は問わない
        assert np.array_equal(got[i][..., 3], want[..., 3])
        m = want[..., 3] > 0
        assert np.array_equal(got[i][m], want[m]), f"コマ {i}"


# --- 金型（版定数の上げ忘れを捕まえる）--------------------------------------

def _golden(request, name, obj, i):
    assert_golden(request, "textmove", name, fk.draw_frame(obj, i), ver=tm._TEXTMOVE_VER,
                  font_ffp=_file_fingerprint(BLOCK))


def test_golden_formula_fall(request, dry, draws):
    official = "^[\\s" + chr(0x200C) + "]+|[\\s" + chr(0x200C) + "]+$"
    o = tt([official, [(r"\s+$", {"size": 48})]], unit=r"\\.|.", leave="fall",
           anchor="center", size=24, duration=1.6)
    _golden(request, "formula_fall", o, 20)


def test_golden_sort_arc(request, dry, draws):
    o = tt(["[1, 2, 10]", "[1, 10, 2]"], unit="code", size=40, border=2)
    _golden(request, "sort_arc", o, 18)


def test_golden_odometer_roll(request, dry, draws):
    o = sv.odometer(2**15 - 1, 2**15, base=2, digits=16, signed="twos", group=8, sep=" ",
                    font=BLOCK, size=24, ripple=0.06)
    _golden(request, "odometer_roll", o, 20)
