# -*- coding: utf-8 -*-
"""slots()（番号つきの箱の列・読みにいく針・上限の線。src/scriptvedit/fx_slots.py）の回帰テスト。

  (a) 番号 → x の写像: elide の前後で番号が正しい・境目（capacity）と ghost・触る番号が窓に入る
  (b) fill の最後のコマ: 箱の中の塊は min(count, capacity) 個、あふれは count − capacity 個
      （描くのは spill_visible 個＋「+N」）
  (c) 範囲外の read（斜線の区画に入って accent）・link の先が ghost なら accent
  (d) compare('10', '2') は 0 番目の字を囲む・swap の2つの経路の距離 > cell/2・
      halt の後のコマの α は 0.45 倍・freeze の後は動かない
  (e) 同じ入力なら鍵と画素が同じ・出来事を1つ変えると鍵が変わる・効かない seed は鍵に入らない
  (f) 不正な入力の ValueError
  (g) dry_run は描かない・実レンダ（ffmpeg）の生成物のコマが figure.frame と一致する
  (h) p.audit() への文字の申告（小さすぎる指定は text-too-small）
  (i) 金型 3 枚（tests/golden/slots/。描き方を変えたら fx_slots._SLOTS_VER を上げて
      pytest --golden-update で作り直す）
  (j) レビューの修正の回帰: compare の直後の swap / set で字が持ち上がった箱から外れない・
      隠れた values は鍵にも絵にも効かない・左端の札がラベルを隠さない・呼ぶ順で
      あふれが変わらない・あふれの塊が団子にならない・区画が ghost と重ならない・
      行をまたぐ矢印の札が線に掛からない・複数行のラベルが重ならない・既定で audit が
      静か・長い字が行全体を縮めない・巨大な count・短すぎる duration・halt 2 回・
      左の入口がラベルに掛からない

字はシステムの日本語フォント（無ければ skip）と等幅フォントで描く。
"""
import os
import shutil
import subprocess
import time

import pytest

np = pytest.importorskip("numpy", reason="numpy が無い環境")
cv2 = pytest.importorskip("cv2", reason="numpy・opencv が無い環境")
pytest.importorskip("PIL", reason="Pillow が無い環境")

import scriptvedit as sv  # noqa: E402
import scriptvedit.framekit as fk  # noqa: E402
from scriptvedit import fx_slots  # noqa: E402
from scriptvedit.context import _exec_stack, activate, current_project  # noqa: E402
from scriptvedit.text import _resolve_font  # noqa: E402

from framekit_golden import assert_golden  # noqa: E402

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
_ACCENT = (224, 36, 27)
_FILL = (143, 163, 191)


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


@pytest.fixture(autouse=True)
def _jp_font():
    try:
        _resolve_font(None)
    except FileNotFoundError as e:
        pytest.skip(f"日本語フォントが無い環境: {str(e).splitlines()[0]}")


@pytest.fixture
def dry():
    """dry_run の Project（生成はせず、コマは figure.frame / framekit.draw_frame で描く）"""
    p = sv.Project()
    p.configure(width=1920, height=1080, fps=30)
    p._dry_run = True
    return p


def _color_mask(img, rgb, tol=40, min_alpha=128):
    d = np.abs(img[..., :3].astype(int) - np.array(rgb)).max(axis=2)
    return (d <= tol) & (img[..., 3] >= min_alpha)


def _region(img, cx, cy, half_w, half_h):
    h, w = img.shape[:2]
    x0, x1 = max(0, int(cx - half_w)), min(w, int(cx + half_w))
    y0, y1 = max(0, int(cy - half_h)), min(h, int(cy + half_h))
    return img[y0:y1, x0:x1]


# --- (a) 番号 → x の写像 ---------------------------------------------------------

def test_elide_maps_indices_around_the_gap(dry):
    s = sv.slots()
    s.row("shelf", 200, label="項目の上限", capacity=200, elide=(6, 3))
    fig = s.build().figure
    vis = fig.rows["shelf"]["visible"]
    assert vis == [1, 2, 3, 4, 5, 6, 198, 199, 200]
    pitch = (64 + 8) * fig.scale
    xs = [fig.cell_xy("shelf", i)[0] for i in vis]
    steps = [round(b - a, 6) for a, b in zip(xs, xs[1:])]
    # 6 と 198 の間だけ「…」の列が1つ入る
    assert steps == [round(pitch, 6)] * 5 + [round(2 * pitch, 6)] + [round(pitch, 6)] * 2
    # 隠れた番号は「…」の列（6 と 198 のちょうど中間）
    mid = fig.cell_xy("shelf", 100)[0]
    assert abs(mid - (xs[5] + xs[6]) / 2.0) < 1e-6
    ys = {fig.cell_xy("shelf", i)[1] for i in vis}
    assert len(ys) == 1


def test_window_adds_capacity_ghost_and_touched_indices(dry):
    s = sv.slots()
    s.row("r", 1000, capacity=500, elide=(2, 2), ghost=[1001])
    s.read(0.5, "r", 300)
    fig = s.build().figure
    vis = fig.rows["r"]["visible"]
    for idx in (1, 2, 299, 300, 301, 499, 500, 501, 502, 999, 1000):
        assert idx in vis, (idx, vis)
    assert 250 not in vis and 700 not in vis
    pitch = (64 + 8) * fig.scale
    # ghost（実在しない 1001 番）は最後の箱のすぐ右
    assert abs(fig.cell_xy("r", 1001)[0] - fig.cell_xy("r", 1000)[0] - pitch) < 1e-6


def test_offset_aligns_rows(dry):
    s = sv.slots()
    s.row("a", 5)
    s.row("b", 3, offset=2)
    fig = s.build().figure
    assert abs(fig.cell_xy("a", 3)[0] - fig.cell_xy("b", 1)[0]) < 1e-6
    assert fig.cell_xy("a", 3)[1] < fig.cell_xy("b", 1)[1]


def test_out_of_range_maps_to_zone(dry):
    s = sv.slots()
    s.row("r", 5)
    s.read(0.2, "r", 9)
    fig = s.build().figure
    pitch = (64 + 8) * fig.scale
    # 範囲外の番号はどれも行のすぐ右の区画（6 番の位置）
    assert fig.cell_xy("r", 9) == fig.cell_xy("r", 6)
    assert abs(fig.cell_xy("r", 6)[0] - fig.cell_xy("r", 5)[0] - pitch) < 1e-6


# --- (b) fill -------------------------------------------------------------------

def _blobs(mask):
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    return [stats[k] for k in range(1, n) if stats[k][cv2.CC_STAT_AREA] > 30]


def test_fill_last_frame_counts_blocks_and_spill(dry):
    s = sv.slots()
    s.row("r", 10, capacity=6)
    s.fill(0.2, "r", 15, dur=1.5, spill_visible=4)
    obj = s.build()
    fig = obj.figure
    end = fig.duration - 1.0 / fig.fps
    st = fig.state(end)["r"]
    assert st["blocks_in_boxes"] == 6
    assert st["overflow"] == 9
    assert st["spill_drawn"] == 4 and st["spill_alive"] == 0
    assert st["plus"] == 9 - 4
    img = fig.frame(end)
    # 箱の中の塊（fill の色の塊）は 6 個。x は 1〜6 番の箱
    y = fig.cell_xy("r", 1)[1]
    band = img[int(y - 32):int(y + 32)]
    blobs = _blobs(_color_mask(band, _FILL, tol=20))
    assert len(blobs) == 6
    xs = sorted(b[cv2.CC_STAT_LEFT] + b[cv2.CC_STAT_WIDTH] / 2.0 for b in blobs)
    for k, x in enumerate(xs):
        assert abs(x - fig.cell_xy("r", k + 1)[0]) < 2.0
    # あふれの最中は線の外に accent の塊がある
    hits = [t for t in np.arange(0.2, 2.0, 1 / 30.0)
            if fig.state(float(t))["r"]["spill_alive"] > 0]
    assert hits
    mid = fig.frame(float(hits[len(hits) // 2]))
    line_x = (fig.cell_xy("r", 6)[0] + fig.cell_xy("r", 7)[0]) / 2.0
    acc = _color_mask(mid, _ACCENT)
    assert acc[:, int(line_x) + 3:].sum() > 100


def test_fill_elided_counts_hidden_slots(dry):
    s = sv.slots()
    s.row("shelf", 200, capacity=200, elide=(6, 3))
    s.fill(1.0, "shelf", 400, dur=2.5)
    fig = s.build().figure
    st = fig.state(fig.duration)["shelf"]
    assert st["blocks_in_boxes"] == 200
    assert st["blocks_visible"] == 9 and st["hidden_filled"] == 191
    # 描くのは spill_visible（24）個まで。2.5 秒の fill では間隔（_SPILL_GAP）を保てる
    # 数に減り、残りは「+N」。描いた数と N の和はあふれの数
    assert st["overflow"] == 200
    assert 8 <= st["spill_drawn"] <= 24
    assert st["spill_drawn"] + st["plus"] == 200


def _shelf(**row_kw):
    s = sv.slots()
    s.row("shelf", 200, capacity=200, elide=(6, 3), **row_kw)
    s.fill(0.6, "shelf", 400, dur=2.5)
    return s.build()


@pytest.mark.parametrize("label,want", [
    (None, "400件"),                        # 既定は入った数の合計（数として誤解されない）
    ("total", "400件"),
    ("over", "+200"),                        # 上限を超えた数
    ("undrawn", "+{undrawn}"),               # 描かなかった塊の数（以前の「+N」）
    ("{total:,}件（上限{limit}）", "400件（上限200）"),
])
def test_overflow_label_counts(dry, label, want):
    """あふれの札は数えるものを選べる。既定は「400件」（以前は描かなかった数だけの
    「+190」で、400 を入れたのに 190 と読めた）"""
    obj = _shelf() if label is None else _shelf(overflow_label=label)
    fig = obj.figure
    st = fig.state(fig.duration)["shelf"]
    assert st["count_label"] == want.format(undrawn=st["plus"])
    assert st["plus"] + st["spill_drawn"] == 200 and st["plus"] > 0
    # 札の文字は audit へ申告する（最後の値）
    spill = [m for m in fig.text_meta if m["role"] == "spill"]
    assert spill and spill[0]["texts"] == [st["count_label"]]


def test_overflow_label_appears_with_the_first_overflow_and_counts_up(dry):
    fig = _shelf().figure
    plan = fig._plan
    ov = plan.fills["shelf"]["over"][0]
    assert fig.state(ov.start - 0.02)["shelf"]["count_label"] is None
    first = fig.state(ov.start + 1e-6)["shelf"]["count_label"]
    assert first == "201件"                  # 箱が埋まりきった後の、最初のあふれ
    vals = [int(fig.state(t)["shelf"]["count_label"].rstrip("件"))
            for t in np.arange(ov.start, ov.last + 0.1, 0.05)]
    assert vals == sorted(vals) and vals[-1] == 400
    # 札の所に accent の字が出る
    px, py, _align = plan.fills["shelf"]["plus_pos"]
    img = fig.frame(fig.duration - 0.5)
    x, y = int(plan.X(px)), int(plan.Y(py))
    assert _color_mask(img[y - 20:y + 20, x:x + 80], _ACCENT).sum() > 40


def test_overflow_label_undrawn_needs_undrawn_blocks(dry):
    """描いた塊だけであふれ終わるなら、数として undrawn だけを使う札は出さない（total・over・
    limit だけの書式は出す。レビューの回帰: '上限{limit}を超過' が undrawn だけの書式と同じ扱いで
    出なかった）"""
    expect = {"undrawn": None, "+{undrawn}（上限{limit}）": None, "total": "9件", "over": "+3",
              "上限{limit}を超過": "上限6を超過", "{over}件超過（上限{limit}）": "3件超過（上限6）"}
    for label, text in expect.items():
        s = sv.slots()
        s.row("r", 8, capacity=6, overflow_label=label)
        s.fill(0.2, "r", 9, dur=1.5)
        fig = s.build().figure
        st = fig.state(fig.duration)["r"]
        assert st["plus"] == 0 and st["overflow"] == 3
        assert st["count_label"] == text, label
    # limit だけの書式も、最初のあふれが線に着く秒から出る（total と同じ秒）
    first = {}
    for label in ("total", "上限{limit}を超過"):
        s = sv.slots()
        s.row("r", 8, capacity=6, overflow_label=label)
        s.fill(0.2, "r", 9, dur=1.5)
        fig = s.build().figure
        ts = [k / 100 for k in range(0, int(fig.duration * 100) + 1)]
        first[label] = next(t for t in ts if fig.state(t)["r"]["count_label"] is not None)
    assert first["上限{limit}を超過"] == first["total"]


def test_overflow_label_none_hides_and_key_only_when_overflowing(dry, tmp_path):
    fig = _shelf(overflow_label=None).figure
    assert fig.state(fig.duration)["shelf"]["count_label"] is None
    assert not [m for m in fig.text_meta if m["role"] == "spill"]

    def key(label, count):
        s = sv.slots()
        s.row("r", 8, capacity=8, overflow_label=label)
        s.fill(0.2, "r", count, dur=1.0)
        return s.build().source

    # あふれない行では効かない（同一出力なら同一鍵）。あふれる行では鍵が変わる
    assert key("total", 5) == key("over", 5)
    assert key("total", 12) != key("over", 12)


@pytest.mark.parametrize("label,msg", [
    ("totl", "知らない名前"),
    ("{count}件", "どれかを入れて"),
    ("件数", "知らない名前"),
    ("あふれ", "知らない名前"),
    ("{total", "書式が不正"),
    ("{total:q}", "書式が不正"),
    ("a\n{total}", "1行"),
    ("", "書式の文字列"),
    (3, "書式の文字列"),
])
def test_overflow_label_errors(dry, label, msg):
    s = sv.slots()
    with pytest.raises(ValueError, match=msg):
        s.row("r", 4, capacity=3, overflow_label=label)


def test_fill_spill_is_seeded(dry):
    def frame(seed):
        s = sv.slots(seed=seed)
        s.row("r", 4, capacity=2)
        s.fill(0.0, "r", 12, dur=1.0)
        return s.build().figure.frame(0.9)
    a, b, c = frame(1), frame(1), frame(2)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)


# --- (c) 範囲外の read / link -------------------------------------------------------

def _pointer_scene(**kw):
    s = sv.slots()
    s.row("rule", 21, label="ルールの型")
    s.row("input", 20, label="渡す項目", ghost=[21])
    return s


def test_out_of_range_read_enters_hatched_zone_and_turns_accent(dry):
    s = _pointer_scene()
    s.read(0.5, "input", 21, dur=0.6)
    fig = s.build().figure
    before = fig.frame(0.4)
    assert _color_mask(before, _ACCENT).sum() == 0
    after = fig.frame(1.5)
    cx, cy = fig.cell_xy("input", 21)
    zone = _region(after, cx, cy, 30, 30)
    assert _color_mask(zone, _ACCENT).sum() > 150          # 斜線・破線・「?」が accent
    # 針（区画の下の三角）も accent
    below = after[int(cy + 50):, int(cx - 20):int(cx + 20)]
    assert _color_mask(below, _ACCENT).sum() > 40
    # 歩いている途中は等速（0.5 秒で 0→1 の半分の位置）
    r = fx_slots._Renderer(fig._plan)
    seg = fig._plan.pointers["input"][0]
    x_mid = seg["x0"] + (seg["x1"] - seg["x0"]) * 0.5
    assert abs(r.p.X(x_mid) - (fig.cell_xy("input", 1)[0] + cx) / 2.0) < 1e-6


def test_in_range_read_stays_neutral(dry):
    s = _pointer_scene()
    s.read(0.5, "input", 7, dur=0.6)
    fig = s.build().figure
    assert _color_mask(fig.frame(1.5), _ACCENT).sum() == 0


def test_link_to_ghost_turns_accent(dry):
    s = _pointer_scene()
    s.link(0.3, ("rule", 21), ("input", 21), dur=0.5)
    fig = s.build().figure
    assert _color_mask(fig.frame(0.6), _ACCENT).sum() == 0     # 伸びている途中は fg
    img = fig.frame(1.2)
    cx, cy = fig.cell_xy("input", 21)
    assert _color_mask(_region(img, cx, cy, 36, 36), _ACCENT).sum() > 150
    # 矢印の先（ghost の箱の上）も accent
    tip = _region(img, cx, cy - 32 - 12, 16, 12)
    assert _color_mask(tip, _ACCENT).sum() > 20


def test_link_to_real_box_stays_neutral(dry):
    s = _pointer_scene()
    s.link(0.3, ("rule", 20), ("input", 20), dur=0.5)
    fig = s.build().figure
    assert _color_mask(fig.frame(1.2), _ACCENT).sum() == 0


# --- (d) compare / swap / halt ----------------------------------------------------

def test_compare_str_frames_first_char(dry):
    s = sv.slots(cell=96)
    s.row("a", 2, values=[10, 2], index_base=0)
    s.compare(0.2, "a", 0, 1, by="str", dur=0.6)
    fig = s.build().figure
    img = fig.frame(0.75)
    plan = fig._plan
    r = plan.row("a")
    assert plan.compares["a"][0]["frames"] == {0: 0, 1: 0}
    assert plan.compares["a"][0]["sign"] == "<"        # 左の '10' < 右の '2'（文字として）
    rend = fx_slots._Renderer(plan)
    acc = _color_mask(img, _ACCENT)
    for q in (0, 1):
        x0, y0, x1, y1 = rend.char_box(r, q, 0, 0.75)
        ring = acc[int(y0) - 2:int(y1) + 3, int(x0) - 2:int(x1) + 3]
        assert ring.sum() > 60
    # '10' の枠は 0 番目の字（'1'）を囲む: 枠の中心は箱の中心より左
    cx, cy = fig.cell_xy("a", 0)
    box = acc[:, int(cx - 48):int(cx + 48)]
    cols = np.flatnonzero(box.any(axis=0))
    assert (cols.min() + cols.max()) / 2.0 + int(cx - 48) < cx - 3


def test_compare_num_uses_numeric_order(dry):
    s = sv.slots(cell=96)
    s.row("a", 2, values=[10, 2])
    s.compare(0.2, "a", 1, 2, by="num")
    plan = s.build().figure._plan
    assert plan.compares["a"][0]["sign"] == ">"
    assert plan.compares["a"][0]["frames"] == {}


def test_swap_paths_keep_apart(dry):
    for i, j in ((1, 2), (1, 3)):
        s = sv.slots()
        s.row("a", 3, values=["x", "y", "z"])
        s.swap(0.2, "a", i, j, dur=0.5)
        fig = s.build().figure
        plan = fig._plan
        rend = fx_slots._Renderer(plan)
        an = plan.anims["a"][0]
        d_min = min(np.hypot(*np.subtract(*rend.swap_positions(plan.row("a"), an, t)))
                    for t in np.linspace(0.2, 0.7, 101))
        assert d_min > plan.C / 2.0
        # 入れ替わった後は中身が逆
        assert plan.content_at("a", i - 1, 1.0)[0] == ["x", "y", "z"][j - 1]


def test_halt_dims_alpha(dry):
    s = sv.slots()
    s.row("a", 3, values=[1, 2, 3], label="名前")
    s.halt(0.5, dur=0.3)
    fig = s.build().figure
    before = fig.frame(0.4).astype(float)
    after = fig.frame(1.0).astype(float)
    a0, a1 = before[..., 3], after[..., 3]
    on = a0 > 0
    ratio = a1[on].sum() / a0[on].sum()
    assert abs(ratio - 0.45) < 0.01
    assert a1.max() == round(255 * 0.45)
    # 色は変えない（不透明度だけ）
    full = a0 == 255
    assert np.abs(after[..., :3][full] - before[..., :3][full]).max() <= 1


def test_halt_freeze_stops_motion(dry):
    s = sv.slots()
    s.row("a", 6)
    s.read(0.2, "a", 6, dur=1.0)
    s.halt(0.6, style="freeze")
    fig = s.build().figure
    frozen = fig.frame(0.6)
    assert np.array_equal(fig.frame(1.0), frozen)
    assert np.array_equal(fig.frame(fig.duration), frozen)
    assert not np.array_equal(fig.frame(0.3), frozen)
    s2 = sv.slots()
    s2.row("a", 6)
    s2.halt(0.6, style="freeze")
    s2.read(1.0, "a", 3)
    with pytest.raises(ValueError, match="freeze"):
        s2.build()


def test_put_set_and_marks_draw(dry):
    s = sv.slots()
    s.row("a", 3)
    s.put(0.1, "a", 2, "条件")
    s.set(0.8, "a", 2, "x")
    s.mark(1.3, "a", 1, style="fill")
    s.unmark(1.8, "a", 1)
    fig = s.build().figure
    plan = fig._plan
    assert plan.content_at("a", 1, 0.6) == ("条件", True)
    assert plan.content_at("a", 1, 1.2) == ("x", True)
    cx, cy = fig.cell_xy("a", 1)
    img = fig.frame(1.6)
    # style='fill' は中を accent の 0.45 で塗る（字が読めるよう半透明）
    tint = _color_mask(_region(img, cx, cy, 24, 24), _ACCENT, min_alpha=100)
    assert tint.sum() > 1500
    assert _color_mask(fig.frame(2.2), _ACCENT, min_alpha=10).sum() == 0


# --- (e) 鍵と決定性 -------------------------------------------------------------

def _scene(count=400, seed=0, spill=True):
    s = sv.slots(seed=seed)
    s.row("shelf", 200, label="項目の上限", capacity=200, elide=(6, 3))
    s.fill(1.0, "shelf", count, dur=2.5, spill=spill)
    s.halt(3.8)
    return s.build()


def test_same_input_same_key_and_pixels(dry):
    a, b = _scene(), _scene()
    assert a.source == b.source
    for t in (0.5, 1.7, 3.2, 4.5):
        assert np.array_equal(a.figure.frame(t), b.figure.frame(t))
    assert _scene(count=401).source != a.source           # 出来事を1つ変えると鍵が変わる
    assert _scene(seed=5).source != a.source               # あふれを描くので seed が効く
    # あふれを描かない図では seed は鍵に入らない（同一出力なら同一鍵）
    assert _scene(count=150, seed=1).source == _scene(count=150, seed=2).source


def test_draw_frame_matches_figure_frame(dry):
    obj = _scene()
    for i in (0, 45, 80, 120):
        assert np.array_equal(fk.draw_frame(obj, i), obj.figure.frame(i / 30.0))


# --- (f) 不正な入力 -------------------------------------------------------------

def _bad_cases():
    def row(**kw):
        def f():
            s = sv.slots()
            s.row("a", **kw)
        return f

    def ev(make):
        def f():
            s = sv.slots()
            s.row("a", 5)
            make(s)
            s.build()
        return f

    return [
        ("n=0", row(n=0)),
        ("n が上限超え", row(n=100001, elide=(3, 3))),
        ("elide なしの 65 箱", row(n=65)),
        ("values の長さ", row(n=3, values=[1, 2])),
        ("capacity > n", row(n=3, capacity=4)),
        ("ghost が実在", row(n=3, ghost=[2])),
        ("elide (0, 0)", row(n=100, elide=(0, 0))),
        ("負の時刻", ev(lambda s: s.read(-0.1, "a", 1))),
        ("無い行", ev(lambda s: s.fill(0, "b", 3))),
        ("put の番号が無い", ev(lambda s: s.put(0, "a", 6, "x"))),
        ("set の番号が無い", ev(lambda s: s.set(0, "a", 0, "x"))),
        ("mark の番号が無い", ev(lambda s: s.mark(0, "a", 9))),
        ("compare の番号が無い", ev(lambda s: s.compare(0, "a", 1, 6))),
        ("swap の番号が無い", ev(lambda s: s.swap(0, "a", 0, 1))),
        ("compare の箱が空", ev(lambda s: s.compare(0, "a", 1, 2))),
        ("同じ箱の swap", ev(lambda s: s.swap(0, "a", 2, 2))),
        ("order", ev(lambda s: s.fill(0, "a", 3, order="up"))),
        ("halt の style", ev(lambda s: s.halt(0, style="blink"))),
        ("mark の色", ev(lambda s: s.mark(0, "a", 1, color="nocolor"))),
        ("動きの重なり", ev(lambda s: (s.put(0, "a", 1, "x"), s.set(0.1, "a", 1, "y")))),
    ]


@pytest.mark.parametrize("name,call", _bad_cases(), ids=[n for n, _c in _bad_cases()])
def test_invalid_input_raises(dry, name, call):
    with pytest.raises(ValueError):
        call()


def test_invalid_builder_options(dry):
    with pytest.raises(ValueError):
        sv.slots(cell=0)
    with pytest.raises(ValueError):
        sv.slots(size=(0, 100))
    with pytest.raises(ValueError):
        sv.slots(colors={"accnt": "red"})
    s = sv.slots()
    with pytest.raises(ValueError):
        s.build()                                   # 行が無い
    s.row("a", 3, values=["p", "q", "r"])
    s.compare(0, "a", 1, 2, by="num")
    with pytest.raises(ValueError, match="数ではありません"):
        s.build()


def test_out_of_range_read_and_link_are_allowed(dry):
    s = sv.slots()
    s.row("a", 5)
    s.row("b", 5)
    s.read(0, "a", 99)
    s.read(0.5, "a", -3)
    s.link(1, ("a", 1), ("b", 50))
    s.build()


# --- (g) dry_run と実レンダ ---------------------------------------------------------

def test_dry_run_does_not_draw(dry, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("dry_run で描いた")
    monkeypatch.setattr(fx_slots._Renderer, "frame_at", boom)
    obj = _scene()
    assert obj.source.replace("\\", "/").split("/")[-2] == "frames"
    assert obj.source in dry._pending_compute_cmds


def _layer(tmp_path, body):
    path = tmp_path / "slots_layer.py"
    path.write_text("from scriptvedit import *\n" + body, encoding="utf-8")
    return str(path)


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg / ffprobe が無い環境")
def test_real_render_frames_match_figure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    body = (
        "s = slots(cell=40, label_size=30, index_size=30)\n"
        "s.row('a', 6, label='棚', capacity=4, values=[None, 'x', None, None, None, None])\n"
        "s.fill(0.1, 'a', 6, dur=0.6, spill_visible=2)\n"
        "s.read(0.3, 'a', 8, dur=0.3)\n"
        "s.build().time() <= move(x=0.5, y=0.5, anchor='center')\n")
    p = sv.Project()
    p.configure(width=640, height=360, fps=30, background_color="black")
    p.layer(_layer(tmp_path, body), priority=1)
    p.render(str(tmp_path / "out.mp4"))
    obj = [o for o in p.objects if getattr(o, "figure", None) is not None][0]
    fig = obj.figure
    w, h = fig.size
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", obj.source,
         "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        check=True, capture_output=True, timeout=120).stdout
    n = len(raw) // (w * h * 4)
    assert n == round(fig.duration * fig.fps)
    frames = np.frombuffer(raw, np.uint8).reshape(n, h, w, 4)
    for i in (0, 5, 12, 20, n - 1):
        want = fig.frame(i / fig.fps)
        assert np.array_equal(frames[i], want), i
    assert os.path.getsize(tmp_path / "out.mp4") > 0
    # 生成した後も dry_run のコマンドは同じ（キャッシュの有無で変わらない）
    q = sv.Project()
    q.configure(width=640, height=360, fps=30, background_color="black")
    q.layer(_layer(tmp_path, body), priority=1)
    warm = q.render(str(tmp_path / "out2.mp4"), dry_run=True)
    shutil.rmtree(tmp_path / "__cache__")
    r = sv.Project()
    r.configure(width=640, height=360, fps=30, background_color="black")
    r.layer(_layer(tmp_path, body), priority=1)
    cold = r.render(str(tmp_path / "out2.mp4"), dry_run=True)
    assert warm == cold


# --- (h) p.audit() への申告 -------------------------------------------------------

def _audit(tmp_path, body):
    p = sv.Project()
    p.configure(width=1920, height=1080, fps=30, background_color="black")
    p.layer(_layer(tmp_path, body), priority=1)
    return [f for f in p.audit(quiet=True) if "slots" in f["message"]]


def test_audit_reads_text_meta(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # 字が全部 audit の推奨（1080p で 32px）以上なら警告しない（縁 halo も申告する）
    ok = _audit(tmp_path, "s = slots(cell=80, index_size=32)\n"
                          "s.row('a', 3, label='名前', values=[1, 2, 3])\n"
                          "s.build().time()\n")
    assert not [f for f in ok if f["severity"] == "warning"
                and f["code"] in ("text-too-small", "text-no-decoration")]
    small = _audit(tmp_path, "s = slots(label_size=20)\ns.row('a', 3, label='名前')\n"
                             "s.build().time()\n")
    assert any(f["code"] == "text-too-small" and f["severity"] == "warning" for f in small)


def test_text_meta_is_scaled_by_size(dry):
    s = sv.slots(size=(400, 200))
    s.row("a", 21, label="受け取る項目")
    obj = s.build()
    fig = obj.figure
    assert fig.scale < 1.0
    assert obj._text_image["size_min"] < 30
    assert obj._text_image["border"] > 0          # 字の縁（halo）を申告する
    for idx in (1, 21):
        x, y = fig.cell_xy("a", idx)
        assert 0 <= x <= 400 and 0 <= y <= 200


def test_describe_lists_slots():
    from scriptvedit.manifest import describe
    entry = [e for e in describe(name="slots")["factories"] if e["name"] == "slots"][0]
    assert entry["category"] != "その他"
    assert "fill" in entry["details"] and "halt" in entry["details"]


# --- 重さ ---------------------------------------------------------------------

def test_frame_cost_is_small(dry):
    """1コマの描画（平均）は 20ms 程度。CI の揺れを見て閾値は緩める"""
    fig = _scene().figure
    rend = fx_slots._Renderer(fig._plan)
    rend.frame_at(0.0)                          # 動かない層と字の絵の準備（初回だけ）
    t0 = time.perf_counter()
    n = 0
    for i in range(1, int(fig.duration * 30)):
        rend.frame_at(i / 30.0)
        n += 1
    mean_ms = (time.perf_counter() - t0) * 1000.0 / n
    assert mean_ms < 60.0, mean_ms


# --- (i) 金型 -------------------------------------------------------------------

def _golden_capacity():
    s = sv.slots(cell=40, gap=6, label_size=22, index_size=18, padding=12, seed=3)
    s.row("shelf", 200, label="上限の棚", capacity=200, elide=(3, 2))
    s.fill(0.1, "shelf", 400, dur=1.2, spill_visible=10)
    return s.build(), 1.15


def _golden_pointer():
    s = sv.slots(cell=40, gap=6, label_size=22, index_size=18, padding=12, row_gap=30)
    s.row("rule", 6, label="型")
    s.row("input", 5, label="入力", ghost=[6])
    s.put(0.0, "rule", 6, "条件", dur=0.3)
    s.link(0.4, ("rule", 6), ("input", 6), dur=0.3)
    s.read(0.8, "input", 6, dur=0.3)
    return s.build(), 1.4


def _golden_sort():
    s = sv.slots(cell=56, gap=6, label_size=22, index_size=18, padding=12)
    s.row("arr", 3, values=[1, 2, 10], index_base=0)
    s.to_str(0.0, "arr")
    s.compare(0.4, "arr", 1, 2, by="str", dur=0.6)
    return s.build(), 0.95


@pytest.mark.parametrize("name,make", [("capacity", _golden_capacity),
                                       ("pointer", _golden_pointer),
                                       ("sort", _golden_sort)])
def test_golden(dry, request, name, make):
    obj, t = make()
    img = fk.draw_frame(obj, int(round(t * 30)))
    assert_golden(request, "slots", name, img, ver=fx_slots._SLOTS_VER,
                  font_ffp=obj.figure.font_ffp)


# --- (j) レビューの修正の回帰 ---------------------------------------------------------

_FG = (255, 255, 255)


def _lifted_box(fig, row, idx, t):
    """持ち上げ込みの箱の外枠 (x0, y0, x1, y1)（キャンバス px）"""
    plan = fig._plan
    r = plan.row(row)
    rend = fx_slots._Renderer(plan)
    lift = rend._lifts(r, t).get(idx - r.base, 0.0)
    x0 = plan.X(plan.col_x(r.col_of[idx - r.base]))
    y0 = plan.Y(r.y + lift)
    size = plan.L(plan.C)
    return x0, y0, x0 + size, y0 + size


def _compare_then(kind):
    s = sv.slots(cell=96)
    s.row("arr", 3, values=[1, 2, 10], index_base=0)
    s.compare(1.0, "arr", 1, 2, by="str", dur=0.6)
    if kind == "swap":
        s.swap(1.6, "arr", 1, 2, dur=0.5)       # compare の直後（間 0 秒）
    else:
        s.set(1.6, "arr", 2, "x")
    return s.build().figure


def test_compare_then_swap_keeps_glyph_in_lifted_box(dry):
    fig = _compare_then("swap")
    t = 1.6 + 2 / 30.0
    x0, y0, x1, y1 = _lifted_box(fig, "arr", 1, t)
    assert y0 < fig._plan.Y(fig._plan.row("arr").y) - 10      # まだ持ち上がっている
    img = fig.frame(t)
    w = fx_slots._BOX_STROKE + 4
    cols = img[:, int(x0) + w:int(x1) - w]
    fg = _color_mask(cols, _FG, tol=30)
    # 枠の上下の辺を除いた行で、字（'2'）の墨の縦の中心が持ち上がった箱の中心にある
    rows = np.flatnonzero(fg.any(axis=1))
    rows = rows[(rows > y0 + w) & (rows < y1 - w)]
    assert rows.size > 10
    assert abs((rows.min() + rows.max()) / 2.0 - (y0 + y1) / 2.0) < 4.0
    # 箱の下（元の高さの箱の下端まで）に字が漏れない
    below = fg[int(y1) + 2:int(fig._plan.Y(fig._plan.row("arr").y + 96)) - 4]
    assert below.sum() == 0
    # 赤い枠（compare の印）は字が動き出す前に消える
    late = fig.frame(1.6 + 0.15)
    assert _color_mask(late, _ACCENT, min_alpha=60).sum() == 0


def test_compare_then_set_clip_follows_lifted_box(dry):
    fig = _compare_then("set")
    for t in (1.65, 1.7, 1.75):
        x0, y0, x1, y1 = _lifted_box(fig, "arr", 2, t)
        img = fig.frame(t)
        w = fx_slots._BOX_STROKE + 4
        cols = img[:, int(x0) + w:int(x1) - w]
        fg = _color_mask(cols, _FG, tol=60, min_alpha=40)
        bottom = int(fig._plan.Y(fig._plan.row("arr").y + 96))
        assert fg[int(y1) + 2:bottom].sum() == 0, t       # 新しい字が箱の下へ漏れない


def test_hidden_values_do_not_change_key_or_pixels(dry):
    def make(long_hidden):
        vals = ["1"] * 100
        if long_hidden:
            vals[50] = "123456789012"                     # 51 番は elide で隠れる
        s = sv.slots()
        s.row("a", 100, values=vals, elide=(3, 3))
        return s.build()
    a, b = make(False), make(True)
    assert a.source == b.source
    assert np.array_equal(a.figure.frame(0.5), b.figure.frame(0.5))
    assert a.figure._plan.value_size_for("a", "1") == 32.0


def test_long_value_shrinks_only_itself(dry):
    s = sv.slots()
    s.row("c", 5, values=["非常に長い文字列です", "b", "c", "d", "e"])
    plan = s.build().figure._plan
    assert plan.value_size_for("c", "b") == 32.0
    assert plan.value_size_for("c", "非常に長い文字列です") < 30.0


@pytest.mark.parametrize("label,text,full", [("ルールの型:\n受け取る項目 21個", "設定値", True),
                                             ("札", "設定値", True),
                                             ("札", "ファイル名の札", False)])
def test_left_end_card_does_not_cover_label(dry, label, text, full):
    s = sv.slots()
    s.row("rule", 21, label=label)
    s.put(0.2, "rule", 1, text)
    plan = s.build().figure._plan
    r = plan.row("rule")
    x0, _x1 = plan.card_span("rule", 0, text)
    assert x0 >= r.label_w + fx_slots._LABEL_MARGIN - 1e-6
    if full:
        # 札の幅の上限（箱 2 つ＋隙間）に入る字は縮めない（箱の列を右へ寄せて場所を空ける）
        assert plan.value_size_for("rule", text) >= fx_slots._VALUE_FLOOR - 1e-6
    else:
        # 上限を超える字は札の中で縮む（ラベルには掛けない。audit が小さすぎる字を報告する）
        assert plan.value_size_for("rule", text) < fx_slots._VALUE_FLOOR


def test_spill_does_not_depend_on_call_order(dry):
    def make(halt_first):
        s = sv.slots(seed=0, size=(1200, 600))
        s.row("r", 6, capacity=3)
        if halt_first:
            s.halt(3.0)
            s.fill(0.2, "r", 12, dur=1.0)
        else:
            s.fill(0.2, "r", 12, dur=1.0)
            s.halt(3.0)
        return s.build(duration=5.0)
    a, b = make(False), make(True)
    assert a.source == b.source
    for t in (0.0, 1.0, 1.3):
        assert np.array_equal(a.figure.frame(t), b.figure.frame(t))


def test_spill_blocks_are_spaced_and_separate(dry):
    obj = _scene()
    fig = obj.figure
    plan = fig._plan
    spills = plan.fills["shelf"]["spills"]
    hits = [sp["hit"] for sp in spills]
    assert min(b - a for a, b in zip(hits, hits[1:])) >= fx_slots._SPILL_GAP - 1e-9
    # 落ちている塊は 1 つずつ見分けられる（重なっても縁取りで分かれ、団子にならない）
    r = plan.row("shelf")
    lx = plan.X(plan.line_x(r))
    top = int(plan.Y(r.y)) - 30
    block = plan.L(plan.block) ** 2
    seen_multi = False
    for t in np.arange(2.5, 3.7, 0.1):
        img = fig.frame(float(t))
        m = _color_mask(img, _ACCENT, tol=40, min_alpha=200)
        m[:top] = False
        m[:, :int(lx) + 4] = False                       # 上限の線より外だけ
        blobs = _blobs(m)
        assert all(b[cv2.CC_STAT_AREA] <= 1.15 * block for b in blobs), t
        seen_multi |= len(blobs) >= 2
    assert seen_multi


def test_zone_sits_beyond_outer_ghost(dry):
    s = sv.slots()
    s.row("input", 20, ghost=[21])
    s.read(0.5, "input", 25)
    fig = s.build().figure
    pitch = (64 + 8) * fig.scale
    assert fig.cell_xy("input", 25) != fig.cell_xy("input", 21)
    assert abs(fig.cell_xy("input", 25)[0] - fig.cell_xy("input", 21)[0] - pitch) < 1e-6
    # 針は ghost の箱ではなく、その外の区画の下で止まる
    seg = fig._plan.pointers["input"][0]
    assert abs(fig._plan.X(seg["x1"]) - fig.cell_xy("input", 25)[0]) < 1e-6
    s = sv.slots()
    s.row("a", 8, index_base=0, ghost=[-1, 8])
    s.read(0.2, "a", 12)
    s.read(0.8, "a", -5)
    fig = s.build().figure
    assert fig.cell_xy("a", 12) == fig.cell_xy("a", 9)
    assert fig.cell_xy("a", -5) == fig.cell_xy("a", -2)
    assert fig.cell_xy("a", 12) != fig.cell_xy("a", 8)


def _seg_hits_box(p0, p1, box, pad=0.0):
    """線分 p0-p1 が矩形 box（x0, y0, x1, y1）に掛かるか（標本で調べる）"""
    x0, y0, x1, y1 = box
    for u in np.linspace(0.0, 1.0, 400):
        x = p0[0] + (p1[0] - p0[0]) * u
        y = p0[1] + (p1[1] - p0[1]) * u
        if x0 - pad <= x <= x1 + pad and y0 - pad <= y <= y1 + pad:
            return True
    return False


@pytest.mark.parametrize("a,b,label", [(("b", 3), ("a", 1), "上へ戻る\n2行の札"),
                                       (("a", 2), ("b", 7), "斜め"),
                                       (("a", 4), ("b", 4), "まっすぐ\n下へ\n3行")])
def test_cross_row_link_label_clears_arrow_and_rows(dry, a, b, label):
    s = sv.slots()
    s.row("a", 8, values=list("abcdefgh"))
    s.row("b", 6, offset=1)
    s.link(1.0, a, b, label=label)
    plan = s.build().figure._plan
    lk = plan.links[0]
    lx, ly, align = lk["label_pos"]
    assert align == "left"
    lines = label.split("\n")
    w = max(plan._fonts.width(t, plan.IS) for t in lines)
    h = plan.link_label_h(label)
    box = (lx, ly - h / 2.0, lx + w, ly + h / 2.0)
    assert not _seg_hits_box(lk["pts"][0], lk["pts"][-1], box, pad=4.0)
    # 縦は 2 行の間の隙間に収まる（上の行の番号の帯・下の行の箱に掛からない）
    ra, rb = plan.row("a"), plan.row("b")
    assert box[1] >= ra.y + plan.C + ra.below - 1e-6
    assert box[3] <= rb.y - rb.above + 1e-6


def test_multiline_labels_do_not_overlap(dry):
    s = sv.slots(row_gap=20)
    s.row("a", 5, label="一行目\n二行目\n三行目\n四行目")
    s.row("b", 5, label="一行目\n二行目\n三行目\n四行目", index=False)
    plan = s.build().figure._plan
    ra, rb = plan.row("a"), plan.row("b")
    h = 4 * plan.LS * 1.25
    a_bottom = ra.y + plan.C / 2.0 + h / 2.0
    b_top = rb.y + plan.C / 2.0 - h / 2.0
    assert a_bottom <= b_top


def test_default_slots_is_quiet_in_audit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    found = _audit(tmp_path, "s = slots()\n"
                             "s.row('rule', 21, label='ルールの型')\n"
                             "s.row('input', 20, label='渡す項目', ghost=[21])\n"
                             "s.put(0.1, 'rule', 21, '条件')\n"
                             "s.link(0.5, ('rule', 21), ('input', 21))\n"
                             "s.read(1.0, 'input', 21)\n"
                             "s.build().time()\n")
    assert not [f for f in found if f["severity"] == "warning"], found


def test_ghost_index_is_readable(dry):
    s = _pointer_scene()
    fig = s.build().figure
    plan = fig._plan
    r = plan.row("input")
    cx, _cy = fig.cell_xy("input", 21)
    iy = plan.Y(r.y + plan.C + plan.idx_gap + plan.idx_h / 2.0)
    digits = _region(fig.frame(0.5), cx, iy, 24, 18)
    muted = (0x9a, 0xa0, 0xa8)
    assert _color_mask(digits, muted, tol=12, min_alpha=200).sum() > 30


def test_huge_count_is_counted_without_lists(dry):
    s = sv.slots()
    s.row("a", 100000, capacity=100000, elide=(5, 5))
    s.fill(0.2, "a", 10**7, dur=2.0)
    t0 = time.perf_counter()
    fig = s.build().figure
    assert time.perf_counter() - t0 < 5.0
    st = fig.state(fig.duration)["a"]
    assert st["overflow"] == 10**7 - 100000
    assert st["spill_drawn"] + st["plus"] == st["overflow"]
    rend = fx_slots._Renderer(fig._plan)
    rend.frame_at(0.0)
    worst = 0.0
    for i in range(1, int(fig.duration * 30)):
        a = time.perf_counter()
        rend.frame_at(i / 30.0)
        worst = max(worst, time.perf_counter() - a)
    assert worst < 0.2, worst


def test_duration_shorter_than_events_raises(dry):
    s = sv.slots()
    s.row("a", 5)
    s.read(3.0, "a", 2)
    with pytest.raises(ValueError, match="duration"):
        s.build(duration=1.0)
    s = sv.slots()
    s.row("a", 5)
    s.read(0.5, "a", 2, dur=0.3)
    s.build(duration=0.8)                     # ちょうど終わるのはよい（余韻は切れてよい）


def test_two_dim_halts_stay_at_045(dry):
    s = sv.slots()
    s.row("a", 3, values=[1, 2, 3], label="名前")
    s.halt(1.0)
    s.halt(2.0)
    fig = s.build().figure
    a0 = fig.frame(0.5)[..., 3].astype(float)
    a1 = fig.frame(3.0)[..., 3].astype(float)
    on = a0 > 0
    assert abs(a1[on].sum() / a0[on].sum() - 0.45) < 0.01


def test_left_source_entry_clears_label(dry):
    s = sv.slots()
    s.row("a", 10, label="右から詰める", capacity=8)
    s.fill(0.2, "a", 14, order="right", source="left")
    plan = s.build().figure._plan
    r = plan.row("a")
    ex, _ey = plan.fills["a"]["entry"]
    assert ex - plan.block / 2.0 >= r.label_w + fx_slots._LABEL_MARGIN - 1e-6
