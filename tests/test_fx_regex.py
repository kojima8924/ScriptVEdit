# -*- coding: utf-8 -*-
"""regex_view（src/scriptvedit/fx_regex.py）の回帰テスト。

確かめること（framekit.draw_frame で ffmpeg を通さずにコマを描いて見る）:
  - 失敗した判定の拍のコマにだけ accent（赤）の画素が出る
  - カウンタが単調に増え、count_to まで回る
  - window の外の event はマスに出ない（× を描かず、照合位置は「…」のマス）
  - beats='literal:=' の拍が 56、コマ数が round(duration × fps)
  - 同じ入力なら画素まで同じ（描く順にも依らない）・鍵の安定
  - 金型3枚（tests/golden/regex/）
  - 実レンダ（ffmpeg で生成した .mov のコマが draw_frame と画素で一致）
  - エラー

numpy・opencv-python・Pillow（framekit の依存）とフォントが無い環境では skip する。
"""
import bisect
import json
import os
import shutil
import subprocess
import time
import warnings
from collections import Counter

import pytest

pytest.importorskip("numpy", reason="numpy が無い環境")
pytest.importorskip("cv2", reason="numpy・opencv-python（framekit の依存）が無い環境")
pytest.importorskip("PIL", reason="Pillow が無い環境")

import numpy as np  # noqa: E402

import scriptvedit as sv  # noqa: E402
from scriptvedit import framekit as fk  # noqa: E402
from scriptvedit import regex_count, regex_trace, regex_view  # noqa: E402
from scriptvedit.context import _exec_stack, activate, current_project  # noqa: E402
from scriptvedit.fx_regex import (  # noqa: E402
    _REGEX_VIEW_VER, _TRIVIAL, _VIEWS, _Glyphs, _precompute)
from scriptvedit.regex_vm import _COUNT_KINDS, _MODES  # noqa: E402
from scriptvedit.text import _resolve_font  # noqa: E402

from framekit_golden import assert_golden  # noqa: E402

_FPS = 30
_ACCENT = (224, 36, 27)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """tmp_path で動かし、dry_run の Project を有効にする（生成物を作らない）"""
    try:
        _resolve_font(None)
    except FileNotFoundError:
        pytest.skip("日本語フォントが無い環境")
    old_current = current_project()
    old_stack = list(_exec_stack)
    activate(None)
    _exec_stack[:] = []
    monkeypatch.chdir(tmp_path)
    p = sv.Project()
    p.configure(width=1920, height=1080, fps=_FPS)
    activate(p)
    p._dry_run = True
    try:
        yield p
    finally:
        activate(old_current)
        _exec_stack[:] = old_stack


def _ws(n):
    return "x" + " " * n + "x"


def _accent_px(img):
    """accent の画素の数（アンチエイリアスの縁ではなく芯の画素）"""
    r, g, b, a = (img[..., k].astype(int) for k in range(4))
    return int(np.count_nonzero((r > 190) & (g < 90) & (b < 90) & (a > 200)))


def _frame_at(fig, t):
    return int(round(t * _FPS))


# --- 拍・コマ数 -----------------------------------------------------------------

def test_literal_beats_56_and_frame_count(_isolated):
    fig = regex_view(regex_trace(r".*(?:.*=.*)", "xxxxx"), beats="literal:=", duration=12)
    assert len(fig.figure.beat_times) == 56 == fig.figure.n_beats
    assert fig.figure.n_frames == 360 == round(12 * _FPS)
    cmd = _isolated._pending_compute_cmds[fig.source]
    assert cmd[cmd.index("-frames:v") + 1] == "360"
    assert fig.figure.count_final == regex_trace(r".*(?:.*=.*)", "xxxxx").counts["tests"]


@pytest.mark.parametrize("duration", [3.3, 7.77, 12, 20.5])
def test_frame_count_is_round_duration(duration):
    fig = regex_view(regex_trace(r"\s+$", _ws(4)), duration=duration)
    assert fig.figure.n_frames == int(duration * _FPS + 0.5)
    assert fig.figure.beat_times[-1] < duration


def test_default_pace_starts_slow_then_accelerates():
    fig = regex_view(regex_trace(r"\s+$", _ws(10)))
    bt = fig.figure.beat_times
    gaps = [b - a for a, b in zip(bt, bt[1:])]
    assert bt[0] == pytest.approx(0.6)                      # 最初は素の絵を1拍ぶん
    assert gaps[:2] == pytest.approx([0.6, 0.6])            # 最初の数拍は 0.6 秒
    assert all(b <= a + 1e-9 for a, b in zip(gaps, gaps[1:]))   # 以降は速くなる一方
    assert gaps[-1] == pytest.approx(1 / (4 * _FPS))        # 最後は1コマに4拍


def test_at_aligns_first_beats():
    fig = regex_view(regex_trace(r"\s+$", _ws(3)), at=[1.7])
    assert fig.figure.beat_times[0] == pytest.approx(1.7)
    fig2 = regex_view(regex_trace(r"\s+$", _ws(3)), at=[1.0, 1.5])
    assert fig2.figure.beat_times[:2] == pytest.approx([1.0, 1.5])
    assert fig2.figure.beat_times[2] > 1.5


def test_duration_fills_the_clip():
    """duration を渡すと拍がその長さを埋める（2-17: 56 拍を 12 秒に。以前は 5.35 秒で
    拍が終わり、残りの 55% が止まった絵だった）"""
    fig = regex_view(regex_trace(r".*(?:.*=.*)", "xxxxx"), beats="literal:=", duration=12)
    f = fig.figure
    assert f.beats_end + 1.0 == pytest.approx(12.0, abs=1e-6)        # + hold_end
    gaps = [b - a for a, b in zip(f.beat_times, f.beat_times[1:])]
    assert gaps[:2] == pytest.approx([0.6, 0.6])                      # 出だしは 0.6 秒のまま
    assert all(b <= a + 1e-9 for a, b in zip(gaps, gaps[1:]))        # 加速だけが緩む
    assert f.beat_times[-1] > 10.5
    # count_to の回転があればその分だけ早く終える
    fig2 = regex_view(regex_trace(r"\s+$", _ws(10)), view="rows", count="matches",
                      count_to=10_000, duration=12)
    assert fig2.figure.beats_end + 1.2 + 1.0 == pytest.approx(12.0, abs=1e-6)
    # 拍が少なければ 1 拍 1.2 秒まで延ばし、残りは最後の絵を保持する
    few = regex_view(regex_trace(r"\s+$", "x  "), beats="attempt", duration=12)
    fb = few.figure.beat_times
    assert [b - a for a, b in zip(fb, fb[1:])] == pytest.approx([1.2] * (len(fb) - 1))
    assert few.figure.beats_end + 1.0 < 12.0 and few.figure.n_frames == 360


def test_duration_with_explicit_pace_only_sets_length():
    tr = regex_trace(r"\s+$", _ws(4))
    a = regex_view(tr, pace=0.3)
    b = regex_view(tr, pace=0.3, duration=20)
    assert b.figure.beat_times == a.figure.beat_times
    assert b.figure.n_frames == 600


def test_fail_marks_visible_at_mid_speed():
    """中くらいの速さ（1コマに1〜3拍）でも × が出る。2-17 では 56 拍のほとんどで赤が見え、
    赤は最後の拍まで続く（以前は拍 1〜7 だけ、3.83 秒以降は赤が無かった）"""
    fig = regex_view(regex_trace(r".*(?:.*=.*)", "xxxxx"), beats="literal:=", duration=12)
    f = fig.figure
    red_beats = set()
    last_red = -1
    for i in range(f.n_frames):
        if _accent_px(fk.draw_frame(fig, i)):
            red_beats.add(bisect.bisect_right(f.beat_times, i / _FPS + 1e-9))
            last_red = i
    assert len(red_beats) >= 50, sorted(red_beats)
    assert last_red / _FPS > f.beat_times[-1]


@pytest.mark.parametrize("pace", [0.6, 0.15, 0.08, 1 / (2 * _FPS)])
def test_accent_only_while_latest_beat_failed(pace):
    """どの速さでも、赤が出るコマの直近の拍は失敗した判定（× は次の拍で消える）"""
    tr = regex_trace(r"\s+$", _ws(3))
    fig = regex_view(tr, beats="test", pace=pace, at=[0.5])
    beat_ev = [e for e in tr.events if e[0] in ("test", "assert")]
    bt = fig.figure.beat_times
    n_red = 0
    for i in range(fig.figure.n_frames):
        if _accent_px(fk.draw_frame(fig, i)):
            k = bisect.bisect_right(bt, i / _FPS + 1e-9)
            assert k >= 1 and not beat_ev[k - 1][3], f"コマ {i}: 直近の拍 {beat_ev[k - 1]}"
            n_red += 1
    assert n_red > 0


# --- 画素 -----------------------------------------------------------------------

def test_accent_only_on_fail_beats():
    """失敗した判定の拍（× が出る時間）のコマにだけ accent の画素が出る（tape）"""
    tr = regex_trace(r"\s+$", _ws(3))
    fig = regex_view(tr, beats="test", pace=0.6)
    beat_ev = [e for e in tr.events if e[0] in ("test", "assert")]
    assert len(beat_ev) == len(fig.figure.beat_times)
    n_fail = 0
    for e, t in zip(beat_ev, fig.figure.beat_times):
        on = fk.draw_frame(fig, _frame_at(fig, t + 0.2))     # 滑り終えて × が出ている
        off = fk.draw_frame(fig, _frame_at(fig, t + 0.5))    # × が消えた後・次の拍の前
        if e[3]:
            assert _accent_px(on) == 0, f"成功した判定の拍に赤が出た: {e}"
        else:
            n_fail += 1
            assert _accent_px(on) > 20, f"失敗した判定の拍に赤が無い: {e}"
        assert _accent_px(off) == 0
    assert n_fail == sum(1 for e in beat_ev if not e[3]) > 5


def test_no_flash_when_beats_are_dense():
    """1コマに3拍を超えて入る区間では × を描かない（点滅させない）"""
    tr = regex_trace(r"\s+$", _ws(3))
    fig = regex_view(tr, beats="test", pace=1 / (5 * _FPS), at=[0.5])
    for i in range(fig.figure.n_frames):
        assert _accent_px(fk.draw_frame(fig, i)) == 0


def test_counter_is_monotonic_and_rolls_to_count_to():
    tr = regex_trace(r"\s+$", _ws(10))
    big = regex_count(r"\s+$", _ws, 20_000, count="matches").value
    fig = regex_view(tr, view="rows", count="matches", count_to=big,
                     count_label=("{n:,} 回", "約{oku:.0f}億回"))
    vals = [fig.figure.count_at(i) for i in range(fig.figure.n_frames)]
    assert vals[0] == 0
    assert all(b >= a for a, b in zip(vals, vals[1:]))
    assert fig.figure.count_final == 55 and 55 in vals
    assert vals[-1] == big == 200_010_000
    # カウンタの帯は、値が変わったコマで描き直されている（隣り合うコマの画素が違う）
    fig2 = regex_view(tr, view="rows", count="matches")
    vals2 = [fig2.figure.count_at(i) for i in range(fig2.figure.n_frames)]
    h = fig2.figure.size[1]
    changed = [i for i in range(1, len(vals2)) if vals2[i] != vals2[i - 1]]
    assert len(changed) > 5
    for i in changed[:5]:
        a = fk.draw_frame(fig2, i - 1)[int(h * 0.8):]
        b = fk.draw_frame(fig2, i)[int(h * 0.8):]
        assert not np.array_equal(a, b)


def test_count_label_pair_switches_only_at_the_final_value():
    """(拍と回転の間の書式, 最後の書式): 拍と回転の間は細かい数、回し終えたら粗い書式"""
    tr = regex_trace(r"\s+$", _ws(10))
    big = regex_count(r"\s+$", _ws, 20_000, count="matches").value
    fig = regex_view(tr, view="rows", count="matches", count_to=big,
                     count_label=("{n:,} 回", "約{oku:.0f}億回（模式）"))
    f = fig.figure
    texts = [f.count_text(i) for i in range(f.n_frames)]
    i20 = _frame_at(fig, f.beat_times[20] + 0.1)
    assert texts[i20] == f"{f.count_at(i20):,} 回" and f.count_at(i20) > 0
    roll_mid = _frame_at(fig, f.beats_end + 0.6)
    assert texts[roll_mid].endswith(" 回") and f.count_at(roll_mid) > 55   # 回転中も数が動く
    assert texts[-1] == "約2億回（模式）"
    first_final = texts.index("約2億回（模式）")
    assert first_final / _FPS >= f.beats_end + 1.2 - 1e-6
    assert all(t == "約2億回（模式）" for t in texts[first_final:])
    # count_to が無ければ、最後の拍で最後の書式になる
    fig2 = regex_view(tr, view="rows", count="matches", count_label=("{n} 回", "計 {n} 回"))
    assert fig2.figure.count_text(fig2.figure.n_frames - 1) == "計 55 回"
    assert fig2.figure.count_text(_frame_at(fig2, fig2.figure.beat_times[5])).endswith(" 回")


def test_coarse_single_count_label_warns():
    tr = regex_trace(r"\s+$", _ws(10))
    with pytest.warns(UserWarning, match="のまま動きません"):
        regex_view(tr, view="rows", count="matches", count_to=200_010_000,
                   count_label="約{oku:.0f}億回")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        regex_view(tr, view="rows", count="matches", count_to=200_010_000,
                   count_label=("{n:,} 回", "約{oku:.0f}億回"))


def test_slice_from_beats_end_shows_only_the_roll(_isolated):
    """拍を見せ終えた所から回転だけを見せる: fig[fig.figure.beats_end:]"""
    tr = regex_trace(r"\s+$", _ws(10))
    fig = regex_view(tr, view="rows", count="matches", count_to=200_010_000,
                     count_label=("{n:,} 回", "約{oku:.0f}億回"))
    total = fig.figure.n_frames / _FPS
    start = fig.figure.beats_end
    clip = fig[start:]
    assert clip is fig
    assert clip.length() == pytest.approx(total - start, abs=1.5 / _FPS)


@pytest.mark.parametrize("cell", [24, 32, 40, 48, 64])
def test_cursor_is_not_clipped_at_small_cells(cell):
    """▼ が tape の帯の上端で切れない（以前は cell < 44 で上が欠け、24 では 1〜3px の点だった）"""
    fig = regex_view(regex_trace(r"\s+$", "x    x"), view="tape", cell=cell, pace=0.6,
                     show=("cells", "cursor"))
    img = fk.draw_frame(fig, _frame_at(fig, fig.figure.beat_times[3] + 0.3))
    cells_y = fig.figure.cell_box(0)[1]
    top = img[:cells_y - 2]
    rows = np.nonzero(((top[..., 0] > 200) & (top[..., 3] > 200)).any(axis=1))[0]
    assert len(rows) >= int(cell * 0.26) - 1, (cell, rows)
    assert rows.min() > 0


def test_glyphs_have_room_no_overflow_warning():
    """'|' や和字が字の絵の端で切れない（textimage の「はみ出し」警告が出ない）"""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        regex_view(regex_trace(r"a|b", "ab"), cell=64)
        regex_view(regex_trace(r"[あ-ん]+。$", "これは日本語の文。です"), view="both", cell=56)
        regex_view(regex_trace(r"a|b", "a|b"), cell=24)


def test_heat_brightest_band_reaches_fg():
    """熱の正規化は帯と同じもの（成功した判定）で数える。1回ずつしか成功しないマスの帯は
    fg のまま（以前は失敗も数えた最大で割って 55% の灰色になっていた）"""
    fig = regex_view(regex_trace(r"[あ-ん]+。$", "これは日本語の文。です"), view="rows",
                     show=())
    img = fk.draw_frame(fig, fig.figure.n_frames - 1)
    rgb_min = img[..., :3].min(axis=2)
    assert int(rgb_min[img[..., 3] > 240].max()) >= 235
    tr = regex_trace(r".*(?:.*=.*)", "xxxxx")
    pre = _precompute(tr, "tests")
    succ = max(max(Counter(p for (_, kind, p, ok) in row["items"] if kind == "test" and ok)
                   .values(), default=0) for row in pre["rows"])
    assert pre["heat_max"] == succ == 6


def test_sequential_and_fresh_frames_match():
    """順に描いたコマと、そのコマだけを新しい図で描いたコマが画素まで同じ（帯の署名に入れた
    丸めた値で描くので、使い回した帯が描く順で変わらない）"""
    tr = regex_trace(r"\s+$", _ws(30))
    kw = dict(view="both", window=(5, 20), beats="test", cell=32)
    fig = regex_view(tr, **kw)
    n = fig.figure.n_frames
    seq = [fk.draw_frame(fig, i) for i in range(n)]
    for i in (139, 140, 141, 200, n - 1):
        fresh = regex_view(regex_trace(r"\s+$", _ws(30)), **kw)
        assert np.array_equal(fk.draw_frame(fresh, i), seq[i]), f"コマ {i}"


@pytest.mark.parametrize("pattern,text,kw", [
    (r".*(?:.*=.*)", "xxxx", dict(beats="literal:=", cell=24, duration=8)),
    (r".*(?:.*=.*)", "xxxxx", dict(beats="literal:=", cell=64, duration=8)),
    (r"(\s*)(\s*?)(\s+)$", "   x", dict(view="both", cell=32, beats="step")),
])
def test_tape_strips_match_single_band(monkeypatch, pattern, text, kw):
    """tape を細帯に分けて描いても、1本の帯で描いたのと画素まで同じ（動く印の上端・下端の
    範囲が控えめで、印が細帯の境で切れない）"""
    import scriptvedit.fx_regex as fr
    split = fr.regex_view(regex_trace(pattern, text), **kw)
    monkeypatch.setattr(fr._RegexFigure, "_tape_strip_bounds",
                        lambda self: [(int(self.L.tape_top), int(self.L.tape_bot))])
    single = fr.regex_view(regex_trace(pattern, text), **kw)
    for i in range(split.figure.n_frames):
        a = fk.draw_frame(split, i)
        b = fk.draw_frame(single, i)
        assert np.array_equal(a, b), f"コマ {i}"


def test_window_hides_outside_events_from_cells():
    """window の外の event はマスに出ない（× を描かず、照合位置は「…」のマスの上）"""
    tr = regex_trace(r"\s+$", _ws(30))
    fig = regex_view(tr, beats="test", pace=0.6, window=(0, 10))
    box9 = fig.figure.cell_box(9)
    assert fig.figure.cell_box(10) is None and fig.figure.cell_box(31) is None
    cells_y = box9[1]
    beat_ev = [e for e in tr.events if e[0] in ("test", "assert")]
    outside = [(e, t) for e, t in zip(beat_ev, fig.figure.beat_times)
               if (e[0] == "test" and e[1] >= 10) or (e[0] == "assert" and e[1] > 10)]
    assert len(outside) > 20
    for e, t in outside[:40]:
        if t > 30:          # 速くなって × を描かない区間は見ない
            break
        img = fk.draw_frame(fig, _frame_at(fig, t + 0.2))
        assert _accent_px(img[:, :box9[2] + 2]) == 0, f"窓の外の失敗がマスに出た: {e}"
        cur = img[cells_y - 20:cells_y - 8]
        ys, xs = np.nonzero((cur[..., 0] > 240) & (cur[..., 3] > 240))
        assert len(xs) and xs.min() > box9[2], f"照合位置が窓の中のマスの上にある: {e}"


def test_same_input_same_pixels_in_any_order():
    tr = regex_trace(r".*(?:.*=.*)", "xxxxx")
    a = regex_view(tr, view="both", beats="literal:=", duration=6)
    b = regex_view(regex_trace(r".*(?:.*=.*)", "xxxxx"), view="both", beats="literal:=",
                   duration=6)
    n = a.figure.n_frames
    picks = [0, 20, 21, 40, 77, 120, n - 1]
    fwd = {i: fk.draw_frame(a, i) for i in picks}
    rev = {i: fk.draw_frame(b, i) for i in reversed(picks)}
    for i in picks:
        assert np.array_equal(fwd[i], rev[i]), f"コマ {i} が描く順で変わった"


def test_trivial_mark_and_hide():
    tr = regex_trace(r"\s+$", _ws(6))
    mark = regex_view(tr, view="rows", trivial="mark")
    hide = regex_view(tr, view="rows", trivial="hide")
    a = fk.draw_frame(mark, mark.figure.n_frames - 1)
    b = fk.draw_frame(hide, hide.figure.n_frames - 1)
    assert a.shape == b.shape
    diff = np.any(a != b, axis=2)
    assert diff.any()
    # 違いは1判定で終わった試行の点だけ（帯・赤は同じ）
    assert _accent_px(a) == _accent_px(b)


def test_text_meta_and_figure_info():
    fig = regex_view(regex_trace(r"\s+$", _ws(3)), cell=64)
    meta = fig._text_image
    assert meta["size_min"] == 39            # cell=64 の文字は 39px
    assert meta["background"] is True        # panel の面の上に描く
    assert fig.figure.size[0] % 2 == 0 and fig.figure.size[1] % 2 == 0
    nopanel = regex_view(regex_trace(r"\s+$", _ws(3)), colors={"panel": (0, 0, 0, 0)})
    assert nopanel._text_image["background"] is False


def test_draw_speed():
    """1コマの描画は目安 15ms（CI の揺れを見て平均 60ms を上限にする）"""
    fig = regex_view(regex_trace(r".*(?:.*=.*)", "xxxxx"), beats="literal:=", duration=6)
    fk.draw_frame(fig, 0)
    t0 = time.perf_counter()
    for i in range(1, 91):
        fk.draw_frame(fig, i)
    avg = (time.perf_counter() - t0) / 90
    assert avg < 0.06, f"1コマ平均 {avg * 1000:.1f}ms"


# --- 鍵 -------------------------------------------------------------------------

def test_key_is_stable_and_follows_output():
    tr = regex_trace(r"\s+$", _ws(4))
    base = regex_view(tr).source
    assert regex_view(regex_trace(r"\s+$", _ws(4))).source == base
    # 同じ拍の時刻になる指定は同じ鍵（同一出力なら同一鍵）
    assert regex_view(tr, pace={"first": 0.6, "slow": 3}).source == base
    others = [
        regex_view(tr, count_label="{n} 回").source,
        regex_view(tr, cell=48).source,
        regex_view(tr, view="rows").source,
        regex_view(tr, beats="backtrack").source,
        regex_view(tr, count="matches").source,
        regex_view(tr, show=("cells", "count")).source,
        regex_view(tr, colors={"accent": "#ff0000"}).source,
        regex_view(tr, at=[1.0]).source,
        regex_view(regex_trace(r"\s+$", _ws(5))).source,
        regex_view(regex_trace(r"\s+$", _ws(4), mode="match")).source,
    ]
    assert base not in others and len(set(others)) == len(others)
    # count_roll は count_to があるときだけ効く
    assert regex_view(tr, count_roll=3.0).source == base
    a = regex_view(tr, count_to=10_000).source
    assert regex_view(tr, count_to=10_000, count_roll=3.0).source != a
    assert os.path.basename(os.path.dirname(base)) == "frames"


def test_key_ignores_params_that_do_not_change_pixels():
    """同一出力なら同一鍵: 効かない条件のパラメータは鍵に入れない（CLAUDE.md §5）"""
    tr = regex_trace(r"a+b", "aaac")                 # 空白の無い文字列・式
    assert regex_view(tr).source == regex_view(tr, space="_").source
    sh = ("pattern", "cells", "cursor")              # カウンタも × も描かない
    base = regex_view(tr, show=sh).source
    assert regex_view(tr, show=sh, count_label="{n}").source == base
    assert regex_view(tr, show=sh, count="matches").source == base
    assert regex_view(tr, show=sh, colors={"accent": "#00ff00"}).source == base
    # 1判定で終わる行が無ければ trivial は効かない
    tr2 = regex_trace(r"\s+$", "    ")
    assert (regex_view(tr2, view="rows", trivial="mark").source
            == regex_view(tr2, view="rows", trivial="hide").source)
    # 効くときは鍵が変わる
    tr3 = regex_trace(r"\s+$", _ws(3))
    assert regex_view(tr3).source != regex_view(tr3, space="_").source
    assert (regex_view(tr3, view="rows", trivial="mark").source
            != regex_view(tr3, view="rows", trivial="hide").source)
    assert regex_view(tr).source != regex_view(tr, colors={"accent": "#00ff00"}).source
    # 単一の書式と、同じ書式の組は同じ絵なので同じ鍵
    assert (regex_view(tr3, count_label="{n} 回").source
            == regex_view(tr3, count_label=("{n} 回", "{n} 回")).source)


def test_size_limits_and_canvas_warning():
    with pytest.raises(ValueError, match="4096"):
        regex_view(regex_trace(r"\s+$", _ws(4)), size=(5000, 600))
    with pytest.raises(ValueError, match="4096"):
        regex_view(regex_trace(r"\s+$", _ws(30)), cell=256, beats="attempt")
    with pytest.warns(UserWarning, match="はみ出します"):
        regex_view(regex_trace(r"\s+$", _ws(46)), beats="attempt")      # 3472px > 1920
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        regex_view(regex_trace(r"\s+$", _ws(10)), view="both")


def test_dry_run_does_not_draw(_isolated):
    fig = regex_view(regex_trace(r"\s+$", _ws(4)))
    assert not os.path.exists(fig.source)
    assert fig.source in _isolated._pending_compute_cmds


# --- エラー -----------------------------------------------------------------------

@pytest.mark.parametrize("kw,exc,msg", [
    ({"view": "grid"}, ValueError, "view"),
    ({"beats": "char"}, ValueError, "beats"),
    ({"beats": "literal:=="}, ValueError, "beats"),
    ({"beats": "literal:="}, ValueError, "リテラル"),
    ({"show": ("pattern", "dots")}, ValueError, "show"),
    ({"show": "pattern"}, ValueError, "show"),
    ({"count": "hits"}, ValueError, "count"),
    ({"trivial": "skip"}, ValueError, "trivial"),
    ({"cell": 10}, ValueError, "cell"),
    ({"rows_max": 0}, ValueError, "rows_max"),
    ({"count_to": 3}, ValueError, "count_to"),
    ({"size": (100, 100)}, ValueError, "size"),
    ({"pace": {"speed": 2}}, ValueError, "pace"),
    ({"pace": 0}, ValueError, "pace"),
    ({"space": "__"}, ValueError, "space"),
    ({"window": (3, 3)}, ValueError, "window"),
    ({"count_label": "{x}"}, ValueError, "count_label"),
    ({"duration": 0.5}, ValueError, "duration"),
    ({"colors": {"red": "#ff0000"}}, ValueError, "colors"),
])
def test_argument_errors(kw, exc, msg):
    with pytest.raises(exc, match=msg):
        regex_view(regex_trace(r"\s+$", _ws(4)), **kw)


def test_other_errors():
    with pytest.raises(TypeError, match="regex_trace"):
        regex_view("\\s+$")
    with pytest.raises(ValueError, match="window"):
        regex_view(regex_trace(r"\s+$", _ws(60)))                  # 48 マスを超える
    with pytest.raises(ValueError, match="window"):
        regex_view(regex_trace(r"\s+$", _ws(60)), window=(0, 60))
    with pytest.raises(ValueError, match="event がありません"):
        regex_view(regex_trace(r"abc", "xyz"), beats="backtrack")
    with pytest.raises(ValueError, match="5,000"):
        regex_view(regex_trace(r"\s+$", _ws(100)), window=(0, 20))
    with pytest.raises(TypeError, match="count_label"):
        regex_view(regex_trace(r"\s+$", _ws(4)), count_label=("{n}",))


def test_error_messages_speak_regex_view():
    """エラーの文言は regex_view の引数で案内する（pace: や text_image の引数を出さない）"""
    with pytest.raises(ValueError, match=r"^regex_view: at の数（3）が拍の数（1）") as e:
        regex_view(regex_trace("a", "a"), at=[0.1, 0.2, 0.3])
    assert "pace" not in str(e.value)
    with pytest.raises(ValueError, match="regex_view: at は単調増加"):
        regex_view(regex_trace(r"\s+$", _ws(3)), at=[1.0, 0.5])
    with pytest.raises(ValueError, match=r"regex_view: フォント.*U\+1F600") as e:
        regex_view(regex_trace(r".+", "a😀b"))
    assert "mono_font=" in str(e.value) and "missing=" not in str(e.value)
    # マスを描かなければ、マスの字はフォントに無くてもよい
    regex_view(regex_trace(r".+", "a😀b"), show=("pattern", "count"))


def test_manifest_choices_match_implementation():
    d = sv.describe(name="regex_view,regex_trace,regex_count")
    ent = {e["name"]: e["params"] for e in d["factories"]}
    assert ent["regex_view"]["view"]["choices"] == list(_VIEWS)
    assert ent["regex_view"]["trivial"]["choices"] == list(_TRIVIAL)
    assert ent["regex_view"]["count"]["choices"] == list(_COUNT_KINDS)
    assert ent["regex_count"]["count"]["choices"] == list(_COUNT_KINDS)
    assert ent["regex_trace"]["mode"]["choices"] == list(_MODES)
    assert ent["regex_count"]["mode"]["choices"] == list(_MODES)
    for name, params in ent.items():
        for p, meta in params.items():
            if "choices" in meta and meta.get("default") is not None:
                assert meta["default"] in meta["choices"], (name, p)


# --- 金型 ---------------------------------------------------------------------------

def _font_ffp():
    g = _Glyphs("test", None, None, None)
    return "+".join(r.ffp for r in g.refs())


def test_golden_tape_backtrack(request):
    """tape: 2つ目の拍（(4,1)。後戻りの矢印と、= が無い × が出ている）"""
    fig = regex_view(regex_trace(r".*(?:.*=.*)", "xxxxx"), beats="literal:=", cell=32,
                     duration=12)
    t = fig.figure.beat_times[1] + 0.2
    assert_golden(request, "regex", "tape_backtrack", fk.draw_frame(fig, _frame_at(fig, t)),
                  ver=_REGEX_VIEW_VER, font_ffp=_font_ffp())


def test_golden_rows_triangle(request):
    """rows: x＋空白6個＋x に \\s+$ の最後のコマ（三角形・赤で止まる帯・カウンタ）"""
    fig = regex_view(regex_trace(r"\s+$", _ws(6)), view="rows", count="matches", cell=32)
    assert_golden(request, "regex", "rows_triangle",
                  fk.draw_frame(fig, fig.figure.n_frames - 1),
                  ver=_REGEX_VIEW_VER, font_ffp=_font_ffp())


def test_golden_both_possessive(request):
    """both: \\s++$ の途中のコマ（下線・照合位置・開始位置・積もりかけの行）"""
    fig = regex_view(regex_trace(r"\s++$", _ws(6)), view="both", cell=32)
    t = fig.figure.beat_times[12] + 0.05
    assert_golden(request, "regex", "both_possessive", fk.draw_frame(fig, _frame_at(fig, t)),
                  ver=_REGEX_VIEW_VER, font_ffp=_font_ffp())


# --- 実レンダ（ffmpeg）---------------------------------------------------------------

def test_real_generation_matches_draw_frame(tmp_path):
    """ffmpeg で生成した .mov（qtrle・argb）のコマが draw_frame と画素で一致する"""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg / ffprobe が無い環境")
    activate(None)          # Project 無し → その場で生成する
    fig = regex_view(regex_trace(r".*(?:.*=.*)", "xxx"), beats="literal:=", cell=32,
                     duration=2)
    assert os.path.isfile(fig.source)
    w, h = fig.figure.size
    for i in (0, 25, 40, 59):
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", fig.source, "-vf", f"select=eq(n\\,{i})",
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
            capture_output=True, check=True).stdout
        got = np.frombuffer(out, np.uint8).reshape(h, w, 4)
        want = fk.draw_frame(fig, i)
        # α=0 の画素の色は保存されない（qtrle argb は事前乗算しないが、念のため α で比べる）
        assert np.array_equal(got[..., 3], want[..., 3])
        vis = want[..., 3] > 0
        assert np.array_equal(got[vis], want[vis]), f"コマ {i} が一致しない"
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=nb_frames,codec_name,pix_fmt", "-of", "json", fig.source],
        capture_output=True, text=True, check=True).stdout
    st = json.loads(probe)["streams"][0]
    assert st["codec_name"] == "qtrle" and int(st["nb_frames"]) == 60
