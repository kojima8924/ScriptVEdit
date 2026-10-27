# -*- coding: utf-8 -*-
"""timeline（年表・時刻の帯・期間の横棒）の回帰テスト。

src/scriptvedit/fx_timeline.py。確かめること:

  (a) 軸: 数値と日時の写し、省いた区間（breaks）の隙間と縮尺、目盛りの自動・単位・書式・
      tick_place='between'
  (b) 配置: 札どうしが重ならない・柱がほかの札を貫かない（後から呼んだ印が先の札の下へ入る
      時刻の帯の場面）・縦の中央・axis_y・段（row）の棒と字
  (c) 動き: grow で棒が左から伸びる・cut は at で一度に出る・slide は右から滑り込む・
      省く印が帯を切る・目盛りの字を横切る柱は切れる
  (d) 決定性: 同じ入力なら鍵と画素が同じ・順に描いたコマと単独で描いたコマが同じ・
      動かないコマは前のコマを返す
  (e) 鍵: 要素で変わる・効かない値（字の無い印の side・段の無い grid・使わない色）では割れない
  (f) 文字の申告（p.audit()）・obj.figure の情報・describe の choices
  (g) エラー（範囲外・種類違い・選択肢・はみ出し・重なり・尺）
  (h) 金型4枚（tests/golden/timeline/。版 _TIMELINE_VER と突き合わせる）: 時刻の帯と括弧・
      期間の横棒（伸びている途中）・省いた年表と板つきの札・滑り込む札
  (i) 実レンダ（ffmpeg を通した出力の画素と、尺より長い表示で最後のコマが残ること）

numpy・opencv-python・Pillow が無い環境では skip する（理由に語を入れる）。
文字を使う項目は、既定のフォントが無ければ skip する（理由に「フォント」を入れる）。
"""
import shutil
import subprocess
import time
import warnings
from datetime import datetime, timedelta, timezone

import pytest

import scriptvedit as sv
from scriptvedit.context import _exec_stack, activate, current_project

from framekit_golden import assert_golden

np = pytest.importorskip("numpy", reason="numpy が無い環境")
pytest.importorskip("cv2", reason="opencv-python（numpy と一緒に入る図の依存）が無い環境")
pytest.importorskip("PIL", reason="Pillow が無い環境")

from scriptvedit import framekit as fk  # noqa: E402
from scriptvedit import fx_timeline  # noqa: E402
from scriptvedit.fx_timeline import _TIMELINE_VER, _Renderer, timeline  # noqa: E402

ACCENT = (0xE0, 0x24, 0x1B)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """各テストを tmp_path で動かす（__cache__ をテストごとに空から始める）"""
    old_current = current_project()
    old_stack = list(_exec_stack)
    activate(None)
    _exec_stack[:] = []
    monkeypatch.chdir(tmp_path)
    try:
        yield
    finally:
        activate(old_current)
        _exec_stack[:] = old_stack


def _proj(w=640, h=360, fps=30):
    """dry_run の Project（build は生成しない。fk.draw_frame でコマを直接描く）"""
    p = sv.Project()
    p.configure(width=w, height=h, fps=fps)
    p._dry_run = True
    return p


def _need_font():
    """既定の日本語フォント（text() と同じ探索）が無い環境では skip する"""
    from scriptvedit.text import _resolve_font
    try:
        _resolve_font(None)
    except (FileNotFoundError, OSError):
        pytest.skip("既定のフォントが無い環境")


def _frame_at(obj, t, fps=30):
    last = int(round(obj.figure.duration * fps)) - 1
    return fk.draw_frame(obj, min(int(round(t * fps)), last))


def _near(img, rgb, tol=40, amin=200):
    d = np.abs(img[..., :3].astype(int) - np.array(rgb)).max(axis=2)
    return (d < tol) & (img[..., 3] > amin)


def _small(**kw):
    """480x270 向けの小さい既定（金型と画素のテスト用）"""
    base = dict(size=(480, 270), padding=12, label_size=20, tick_size=16, bar=16)
    base.update(kw)
    return base


# --- (a) 軸 -----------------------------------------------------------------------

def test_numeric_axis_is_linear():
    _proj()
    tl = timeline(0, 10, ticks=[], size=(1000, 300), padding=20)
    x0, x1 = tl.x(0), tl.x(10)
    assert x0 == pytest.approx(20 + fx_timeline._OVERHANG)
    assert x1 == pytest.approx(1000 - 20 - fx_timeline._OVERHANG)
    assert tl.x(5) == pytest.approx((x0 + x1) / 2)
    assert tl.x(2.5) == pytest.approx(x0 + (x1 - x0) / 4)


def test_breaks_make_a_fixed_gap_and_keep_the_scale():
    _proj()
    tl = timeline(0, 100, ticks=[], breaks=[(20, 80)], size=(1000, 300), padding=20)
    assert tl.x(80) - tl.x(20) == pytest.approx(fx_timeline._BREAK_GAP)
    left = (tl.x(20) - tl.x(0)) / 20
    right = (tl.x(100) - tl.x(80)) / 20
    assert left == pytest.approx(right)
    assert tl.x(20) < tl.x(50) < tl.x(80)          # 隙間の中は線形


def test_time_axis_maps_datetimes_dates_and_iso_strings():
    _proj()
    tl = timeline(datetime(2026, 10, 23), datetime(2026, 10, 28), ticks=[],
                  size=(1000, 300), padding=20)
    a = tl.x(datetime(2026, 10, 25, 12))
    assert tl.x("2026-10-25 12:00") == pytest.approx(a)
    assert a == pytest.approx((tl.x("2026-10-23") + tl.x("2026-10-28")) / 2)
    from datetime import date
    assert tl.x(date(2026, 10, 24)) == pytest.approx(tl.x("2026-10-24T00:00"))


def test_auto_numeric_ticks_are_nice_and_few():
    _proj()
    _need_font()
    tl = timeline(0, 97)
    vals = [f for f, _s in tl._ticks]
    assert 2 <= len(vals) <= fx_timeline._AUTO_TICKS_MAX
    step = vals[1] - vals[0]
    assert step in (10, 20, 25, 50) or step == pytest.approx(20)
    assert [s for _f, s in tl._ticks][:2] == ["0", str(int(step))]


@pytest.mark.parametrize("start,end,ticks,fmt,want", [
    (datetime(2026, 10, 23), datetime(2026, 10, 28), "day", None,
     ["23日", "24日", "25日", "26日", "27日", "28日"]),
    (datetime(2025, 11, 1), datetime(2026, 2, 1), "month", None,
     ["2025年11月", "2025年12月", "2026年1月", "2026年2月"]),
    (datetime(2026, 1, 1, 0), datetime(2026, 1, 1, 18), ("hour", 6), None,
     ["0:00", "6:00", "12:00", "18:00"]),
    (datetime(2019, 6, 1), datetime(2026, 6, 1), ("year", 2), None,
     ["2020年", "2022年", "2024年", "2026年"]),
    (datetime(2026, 10, 23), datetime(2026, 10, 25), "day", "{month}/{day}({wd})",
     ["10/23(金)", "10/24(土)", "10/25(日)"]),
])
def test_time_ticks_units_and_formats(start, end, ticks, fmt, want):
    _proj()
    _need_font()
    tl = timeline(start, end, ticks=ticks, tick_format=fmt)
    assert [s for _f, s in tl._ticks] == want


def test_numeric_tick_format_and_explicit_ticks():
    _proj()
    _need_font()
    tl = timeline(18, 24, ticks=1, tick_format="{v}:00")
    assert [s for _f, s in tl._ticks] == [f"{h}:00" for h in range(18, 25)]
    tl = timeline(0, 1, ticks=0.5)
    assert [s for _f, s in tl._ticks] == ["0", "0.5", "1"]
    tl = timeline(0, 10, ticks=[(0, "はじめ"), 5, (10, "おわり")])
    assert [s for _f, s in tl._ticks] == ["はじめ", "5", "おわり"]


def test_ticks_inside_breaks_are_dropped():
    _proj()
    _need_font()
    tl = timeline(0, 100, ticks=10, breaks=[(25, 75)])
    assert [f for f, _s in tl._ticks] == [0, 10, 20, 80, 90, 100]


def test_tick_place_between_centers_labels_in_intervals():
    _proj()
    _need_font()
    tl = timeline(datetime(2026, 10, 23), datetime(2026, 10, 26), ticks="day",
                  tick_place="between", **_small())
    scene = tl._resolve()
    ticks = scene["ticks"]
    assert ticks[-1][1] is None                      # 最後の目盛りには字が無い
    for k in range(len(ticks) - 1):
        _x, sp, (lx, _ly) = ticks[k]
        mid = (ticks[k][0] + ticks[k + 1][0]) / 2
        assert lx + sp.w / 2 == pytest.approx(mid, abs=1)


# --- (b) 配置 -----------------------------------------------------------------------

def _band_scene(small=False):
    """時刻の帯（mp2 の 1-03）。small=True は 480x270 向けの小さい版（札を1行にする）"""
    if small:
        tl = timeline(18, 24, ticks=1, tick_format="{v}:00", **_small(size=(480, 320), padding=8))
        tl.mark(19, "19:00 停止", note="予防のため", at=0.3)
        tl.mark(22.5 + 5 / 60, "22:30過ぎ 公開", at=1.2)
        tl.mark(23, "23:00 予定", dot="ring", color="muted", text_color="muted", at=2.0)
    else:
        tl = timeline(18, 24, ticks=1, tick_format="{v}:00")
        tl.mark(19, "19:00\nニコニコ 停止", note="予防のため", at=0.3)
        tl.mark(22.5 + 5 / 60, "22:30過ぎ\n公開", at=1.2)
        tl.mark(23, "23:00 公開の予定", dot="ring", color="muted", text_color="muted", at=2.0)
    tl.bracket(19, 22.5 + 5 / 60, "約3時間半", at=2.8)
    return tl


def _check_boxes(obj, tl):
    """札どうしが重ならず、柱がほかの札を貫かない"""
    items = obj.figure.items
    ay = obj.figure.axis_y
    boxes = [(k, it["box"]) for k, it in enumerate(items) if it["box"] is not None]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i][1], boxes[j][1]
            assert not fx_timeline._overlap(a, b), (boxes[i], boxes[j])
    for k, it in enumerate(tl._items):
        if it["kind"] != "mark" or items[k]["box"] is None:
            continue
        x = tl._axis.x(it["v"])
        bk = items[k]["box"]
        for j, b in boxes:
            if j == k or not (b[0] < x < b[2]):
                continue
            # x を覆う札は、この札より軸から遠い（同じ側なら）
            if (b[1] < ay) == (bk[1] < ay):
                assert abs((b[1] + b[3]) / 2 - ay) > abs((bk[1] + bk[3]) / 2 - ay)


def test_later_mark_goes_under_an_earlier_label():
    """時刻の帯: 23:00 の札は 22:35 の札の下（軸の側）に入り、22:35 の柱を覆わない"""
    _need_font()
    _proj(1920, 1080)
    tl = _band_scene()
    obj = tl.build()
    items = obj.figure.items
    b_2235, b_23 = items[1]["box"], items[2]["box"]
    assert b_23[3] > b_2235[3]                       # 23:00 の札のほうが軸に近い（下）
    assert not (b_23[0] < tl.x(22.5 + 5 / 60) < b_23[2])
    _check_boxes(obj, tl)
    # 括弧は軸の下（目盛りの字の下）
    assert items[3]["box"] is None
    scene = tl._resolve()
    br = scene["items"][3]
    assert br["line_y"] > obj.figure.axis_y + fx_timeline._TICK_LEN


def test_many_marks_do_not_overlap():
    _need_font()
    _proj(1920, 1080)
    tl = timeline(0, 20, ticks=5)
    names = ["短い", "少し長めの札", "とても長い札の文字列", "札", "中くらいの札"]
    for k in range(14):
        tl.mark(k * 1.4 + 0.5, names[k % 5], side="above" if k % 3 else "below",
                at=0.1 * k, card=(k % 4 == 0))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        obj = tl.build()
    _check_boxes(obj, tl)


def test_figure_is_centered_vertically_and_axis_y_is_respected():
    _need_font()
    _proj(640, 360)
    tl = timeline(0, 10, ticks=5, **_small(size=(640, 360)))
    tl.mark(5, "上の札", at=0, enter="cut")
    obj = tl.build()
    img = _frame_at(obj, 0.5)
    rows = np.flatnonzero(img[..., 3].max(axis=1) > 0)
    assert abs((rows[0] + rows[-1]) / 2 - 180) <= 4
    _proj(640, 360)
    tl2 = timeline(0, 10, ticks=5, axis_y=100, **_small(size=(640, 360)))
    obj2 = tl2.build()
    assert obj2.figure.axis_y == 100


def test_rows_stack_below_the_ticks():
    _need_font()
    _proj()
    tl = timeline(datetime(2026, 10, 23), datetime(2026, 10, 28), ticks="day",
                  tick_place="between", grid=True, **_small())
    tl.span("2026-10-23 19:30", "2026-10-25 09:25", "ドコモ", row=0, note="25日 9:25")
    tl.span("2026-10-23 20:10", "2026-10-26 18:00", "ソフトバンク", row=1)
    obj = tl.build()
    rows = obj.figure.rows
    assert len(rows) == 2 and rows[0][1] < rows[1][0]
    assert rows[0][0] > obj.figure.axis_y + fx_timeline._TICK_LEN
    assert rows[0][1] - rows[0][0] == 16


# --- (c) 動き -----------------------------------------------------------------------

def test_span_grows_from_the_left():
    _proj()
    tl = timeline(0, 10, ticks=[], **_small())
    tl.span(2, 8, row=0, color="accent", at=0.0, dur=1.0)
    obj = tl.build()
    y = int(sum(obj.figure.rows[0]) / 2)
    rights = []
    for i in range(0, 31, 3):
        img = fk.draw_frame(obj, i)
        xs = np.flatnonzero(_near(img, ACCENT)[y])
        rights.append(xs.max() if len(xs) else -1)
    assert rights == sorted(rights)
    assert rights[-1] == pytest.approx(tl.x(8), abs=2)
    xs = np.flatnonzero(_near(fk.draw_frame(obj, 30), ACCENT)[y])
    assert xs.min() == pytest.approx(tl.x(2), abs=2)


def test_cut_appears_at_its_second():
    _proj()
    tl = timeline(0, 10, ticks=[], **_small())
    tl.mark(5, at=1.0, enter="cut", color="accent")
    obj = tl.build()
    x, y = int(tl.x(5)), obj.figure.axis_y - 7
    assert fk.draw_frame(obj, 29)[y, x, 3] == 0
    assert _near(fk.draw_frame(obj, 30), ACCENT)[y, x]


def test_slide_comes_from_the_right():
    _need_font()
    _proj()
    tl = timeline(0, 10, ticks=[], **_small())
    tl.mark(5, "札", card=True, enter="slide", at=0.0, dur=1.0, side="below")
    obj = tl.build()
    box = obj.figure.items[0]["box"]

    def cx(img):
        band = img[box[1]:box[3], :, 3].astype(float)
        cols = band.sum(axis=0)
        return (cols * np.arange(cols.size)).sum() / cols.sum()

    mid, end = cx(fk.draw_frame(obj, 9)), cx(fk.draw_frame(obj, 30))
    assert mid > end + 10
    assert end == pytest.approx((box[0] + box[2]) / 2, abs=2)


def test_break_cuts_the_band():
    _proj()
    tl = timeline(0, 100, ticks=[], breaks=[(30, 70)], **_small())
    tl.span(10, 90, color="accent", enter="cut")
    obj = tl.build()
    img = fk.draw_frame(obj, 0)
    bx = int(round(tl._axis.break_xs()[0]))
    ay = obj.figure.axis_y
    assert img[ay, bx, 3] == 0                      # 2本の線の間は抜ける
    assert _near(img, ACCENT)[ay, int(tl.x(20))]
    assert _near(img, ACCENT)[ay, int(tl.x(80))]


def test_stem_is_cut_where_it_crosses_a_tick_label():
    _need_font()
    _proj()
    tl = timeline(0, 10, ticks=5, **_small())
    tl.mark(5, "札", side="below", enter="cut")
    obj = tl.build()
    scene = tl._resolve()
    stem = scene["items"][0]["stem"]
    assert stem[3], "目盛りの字の所で柱を切る"
    pieces = fx_timeline._stem_pieces(stem[1], stem[2], stem[3])
    assert pieces and pieces[-1][1] == pytest.approx(stem[2])     # 札へ着く所は残る
    for a, b in pieces:
        for c0, c1 in stem[3]:
            assert b <= c0 or a >= c1


# --- (d) 決定性 -----------------------------------------------------------------------

def test_same_input_same_key_and_pixels():
    _need_font()
    _proj()
    o1 = _band_scene(small=True).build()
    _proj()
    o2 = _band_scene(small=True).build()
    assert o1.source == o2.source
    for i in (0, 30, 60, 100):
        assert np.array_equal(fk.draw_frame(o1, i), fk.draw_frame(o2, i))


def test_sequential_and_fresh_frames_match_and_settled_frames_are_reused():
    _need_font()
    _proj()
    tl = _band_scene(small=True)
    tl.span(18.5, 21, row=0, at=0.5, color="accent")
    scene = tl._resolve()
    seq = _Renderer(scene)
    outs = [seq.frame(i / 30) for i in range(0, 130, 7)]
    for k, i in enumerate(range(0, 130, 7)):
        assert np.array_equal(outs[k], _Renderer(scene).frame(i / 30))
    a = seq.frame(4.0)
    b = seq.frame(4.2)
    assert a is b                                    # 動かないコマは前のコマを返す


# --- (e) 鍵 -------------------------------------------------------------------------

def test_key_changes_with_items_and_ignores_values_that_do_not_change_the_picture():
    _need_font()

    def src(**kw):
        _proj()
        tl = timeline(0, 10, ticks=5, **_small(**kw.pop("axis", {})))
        tl.mark(3, at=0.2, side=kw.get("side", "above"))
        tl.mark(6, kw.get("label", "札"), at=0.4)
        return tl.build().source

    base = src()
    assert src(label="別の札") != base
    assert src(side="below") == base                 # 字の無い印の side は効かない
    assert src(axis={"grid": True}) == base          # 段が無ければ grid は効かない
    assert src(axis={"colors": {"dim": "#123456"}}) == base   # 使わない色
    assert src(axis={"colors": {"line": "#123456"}}) != base


def test_key_has_no_raw_paths():
    _need_font()
    _proj()
    tl = _band_scene(small=True)
    params = tl._params(5.0)
    import json
    text = json.dumps(params, ensure_ascii=False)
    assert ":\\\\" not in text and "/Fonts" not in text


# --- (f) 申告・情報・describe -----------------------------------------------------------

def test_text_meta_and_figure_info():
    _need_font()
    _proj()
    tl = _band_scene(small=True)
    obj = tl.build()
    meta = obj._text_image
    assert meta["content"].startswith("timeline")
    assert meta["border"] == fx_timeline._HALO
    assert meta["size_min"] == 16
    assert obj.figure.x(19) == pytest.approx(tl.x(19))
    assert obj.figure.duration == pytest.approx(2.8 + 0.6 + 1.0)
    assert [it["kind"] for it in obj.figure.items] == ["mark", "mark", "mark", "bracket"]
    assert [s for _x, s in obj.figure.ticks][0] == "18:00"


def _audit(tmp_path, body):
    layer = tmp_path / "tl_audit.py"
    layer.write_text("from scriptvedit import *\n" + body, encoding="utf-8")
    p = sv.Project()
    p.configure(width=1920, height=1080, fps=30, background_color="black")
    p.layer(str(layer), priority=1)
    return [f for f in p.audit(quiet=True) if "timeline" in f["message"]]


def test_audit_reads_text_meta(tmp_path):
    """既定（札 44px・目盛り 32px・縁取り 3px）なら warning は出ない。小さすぎる字は出る"""
    _need_font()
    ok = _audit(tmp_path, "tl = timeline(18, 24, ticks=1, tick_format='{v}:00')\n"
                          "tl.mark(19, '停止', note='予防のため')\n"
                          "tl.build().time()\n")
    assert ok, "申告が読まれていない"
    assert not [f for f in ok if f["severity"] == "warning"]
    small = _audit(tmp_path, "tl = timeline(0, 10, ticks=5, tick_size=20)\n"
                             "tl.build().time()\n")
    assert any(f["code"] == "text-too-small" and f["severity"] == "warning" for f in small)


def test_describe_lists_timeline_and_choices_match():
    m = sv.describe(name="timeline")
    entry = [e for e in m["factories"] if e["name"] == "timeline"][0]
    assert entry["category"] != "その他"
    assert entry["params"]["tick_place"]["choices"] == list(fx_timeline._TICK_PLACES)
    assert "mark" in entry["details"] and "bracket" in entry["details"]


# --- (g) エラー ----------------------------------------------------------------------

@pytest.mark.parametrize("make,exc,match", [
    (lambda: timeline(10, 0, ticks=[]), ValueError, "start より後"),
    (lambda: timeline(0, "2026-01-01", ticks=[]), TypeError, "どちらも数値"),
    (lambda: timeline("2026-13-01", "2026-12-01", ticks=[]), ValueError, "ISO"),
    (lambda: timeline(0, 10, ticks=[]).mark(11), ValueError, "範囲"),
    (lambda: timeline(0, 10, ticks=[]).mark("2026-01-01"), TypeError, "数値で"),
    (lambda: timeline("2026-01-01", "2026-02-01", ticks=[]).mark(3), TypeError, "日時"),
    (lambda: timeline(0, 10, ticks=[]).span(5, 5), ValueError, "a より後"),
    (lambda: timeline(0, 10, ticks=[]).mark(5, enter="slid"), ValueError, "slide"),
    (lambda: timeline(0, 10, ticks=[]).mark(5, side="top"), ValueError, "above"),
    (lambda: timeline(0, 10, ticks=[]).mark(5, note="補足"), ValueError, "label"),
    (lambda: timeline(0, 10, ticks=[]).mark(5, card=True), ValueError, "label"),
    (lambda: timeline(0, 10, ticks=[]).mark(5, color="accnt"), ValueError, "accent"),
    (lambda: timeline(0, 10, ticks=[]).span(1, 2, row=-1), ValueError, "row"),
    (lambda: timeline(0, 10, ticks=[]).mark(5, at=-1), ValueError, "at"),
    (lambda: timeline(0, 10, ticks=[]).mark(5, dur=0), ValueError, "dur"),
    (lambda: timeline(0, 10, ticks=[], breaks=[(2, 5), (4, 8)]), ValueError, "重なって"),
    (lambda: timeline(0, 10, ticks=[], breaks=[(0, 5)]), ValueError, "start < a"),
    (lambda: timeline(0, 10, ticks=1, tick_format="{year}"), ValueError, "tick_format"),
    (lambda: timeline("2026-01-01", "2026-02-01", ticks="week"), ValueError, "day"),
    (lambda: timeline(0, 10, ticks=-1), ValueError, "ticks"),
    (lambda: timeline(0, 10, ticks=[], tick_place="mid"), ValueError, "between"),
    (lambda: timeline(datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2),
                      ticks=[]), ValueError, "タイムゾーン"),
    (lambda: timeline(0, 10, ticks=[]).mark(5, at=3).build(duration=2), ValueError, "duration"),
])
def test_errors(make, exc, match):
    _proj()
    with pytest.raises(exc, match=match):
        make()


def test_lane_text_overlap_and_overflow_are_errors():
    _need_font()
    _proj()
    tl = timeline(0, 10, ticks=[], **_small())
    tl.span(1, 3, "ながいながい見出し", row=0)
    tl.span(3.2, 6, "次の見出し", row=0)
    with pytest.raises(ValueError, match="重なります"):
        tl.build()
    _proj()
    tl = timeline(0, 10, ticks=[], **_small())
    tl.span(1, 9.8, row=0, note="はみ出す注記")
    with pytest.raises(ValueError, match="はみ出します"):
        tl.build()


def test_too_tall_is_an_error():
    _need_font()
    _proj()
    tl = timeline(0, 10, ticks=[], **_small(size=(480, 120)))
    for k in range(6):
        tl.mark(5, f"札{k}", card=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")              # 同じ値の札は柱が札を横切る（警告）
        with pytest.raises(ValueError, match="縦に収まりません"):
            tl.build()


def test_time_axis_with_timezones():
    _proj()
    jst = timezone(timedelta(hours=9))
    tl = timeline(datetime(2026, 10, 23, tzinfo=jst), datetime(2026, 10, 24, tzinfo=jst),
                  ticks=[], size=(1000, 300), padding=20)
    assert tl.x(datetime(2026, 10, 23, 3, tzinfo=timezone.utc)) == pytest.approx(
        tl.x(datetime(2026, 10, 23, 12, tzinfo=jst)))
    with pytest.raises(ValueError, match="タイムゾーン"):
        tl.mark(datetime(2026, 10, 23, 12))


# --- (h) 金型 -------------------------------------------------------------------------

def _golden_band():
    _proj(480, 270)
    return _band_scene(small=True).build(), 4.0


def _golden_lanes():
    _proj(480, 270)
    tl = timeline(datetime(2026, 10, 23), datetime(2026, 10, 28), ticks="day",
                  tick_place="between", grid=True, **_small())
    tl.span("2026-10-23 19:30", "2026-10-25 09:25", "ドコモ", row=0, note="25日 9:25", at=0.0)
    tl.span("2026-10-23 20:10", "2026-10-26 18:00", "ソフトバンク", row=1, note="26日 18:00",
            at=0.3)
    tl.span("2026-10-23 20:10", "2026-10-25 12:00", "緊急地震速報も", row=1, side="below",
            thickness=0.35, color="accent", text_color="accent", at=0.6)
    return tl.build(), 1.0


def _golden_breaks():
    _proj(480, 270)
    tl = timeline("2024-03-01", "2026-11-15",
                  ticks=[("2024-06-01", "2024年"), ("2026-08-01", "8月"), ("2026-10-01", "10月")],
                  breaks=[("2024-09-01", "2026-07-15")], arrow=True, **_small())
    tl.span("2024-06-01", "2026-08-11", color="dim", at=0.0, dur=0.6)
    tl.mark("2024-06-01", "兆し", side="below", card=True, enter="rise", at=0.0)
    tl.mark("2026-08-11", "報告", side="below", card=True, enter="rise", at=0.3)
    tl.span("2026-08-11", "2026-10-21", color="accent", at=0.6, dur=0.8)
    tl.mark("2026-10-21", "修正版", side="below", card=True, color="accent", at=1.4)
    return tl.build(), 1.25


def _golden_slide():
    _proj(480, 270)
    tl = timeline(2000, 2027, ticks=10, tick_format="{v}年", arrow=True, **_small())
    for k, (y, t) in enumerate([(2005, "事件A"), (2013, "事件B"), (2024, "事件C")]):
        tl.mark(y, t, note="説明", side="below", card=True, enter="slide", at=0.3 * k,
                color="accent" if k == 2 else "line")
    return tl.build(), 0.75


@pytest.mark.parametrize("name,make", [("band", _golden_band), ("lanes", _golden_lanes),
                                       ("breaks", _golden_breaks), ("slide", _golden_slide)])
def test_golden(request, name, make):
    _need_font()
    obj, t = make()
    ffp = fk.font_ref("test").ffp
    assert_golden(request, "timeline", name, _frame_at(obj, t), ver=_TIMELINE_VER, font_ffp=ffp)


# --- 重さ --------------------------------------------------------------------------

def test_frame_cost_is_small():
    """1080p の動いているコマの描画（平均）。CI の揺れを見て閾値は緩める"""
    _need_font()
    _proj(1920, 1080)
    scene = _band_scene()._resolve()
    r = _Renderer(scene)
    r.frame(0.0)
    t0 = time.perf_counter()
    n = 0
    for i in range(1, 110):
        r.frame(i / 30)
        n += 1
    mean_ms = (time.perf_counter() - t0) * 1000.0 / n
    assert mean_ms < 80.0, mean_ms


# --- (i) 実レンダ ----------------------------------------------------------------------

def _need_ffmpeg():
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg / ffprobe が無い環境")


def _decode(path, t, w, h):
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.4f}", "-i", path, "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.uint8).reshape(h, w, 3)


def test_real_render_band_and_hold(tmp_path):
    """ffmpeg を通した出力でも、帯が伸びて赤くなり、尺より長い表示で最後のコマが残る"""
    _need_ffmpeg()
    layer = tmp_path / "tl_real.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "tl = timeline(0, 10, ticks=[], size=(320, 180), padding=10, axis_y=90)\n"
        "tl.span(1, 9, color='accent', at=0.2, dur=0.6)\n"
        "obj = tl.build()\n"
        "obj.time(obj.figure.duration + 1.0)\n", encoding="utf-8")
    p = sv.Project()
    p.configure(width=320, height=180, fps=30, background_color="black")
    p.layer(str(layer), priority=0)
    out = str(tmp_path / "tl.mp4")
    p.render(out)
    first = _decode(out, 0.05, 320, 180)
    r, g, b = (int(v) for v in first[90, 250])
    assert r - g < 40                                       # まだ伸びていない（灰色の軸だけ）
    late = _decode(out, 1.2, 320, 180)
    r, g, b = (int(v) for v in late[90, 250])
    assert r > 170 and g < 90 and b < 90                    # 伸びたあとは赤（accent）
    held = _decode(out, 2.4, 320, 180)                      # 尺（1.8 秒）より後
    assert int(held[90, 250][0]) > 170
