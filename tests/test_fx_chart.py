# -*- coding: utf-8 -*-
"""chart（データのグラフ: 線・階段・面・ローソク足・棒）の回帰テスト。

src/scriptvedit/fx_chart.py。確かめること:

  (a) 軸: 数値・日時・時刻（'HH:MM'）の写し、種類の混在・タイムゾーンの検査、範囲の自動と手動
  (b) 配置: 印と帯の札が重ならず、印の線が札を貫かない・今の値の札が枠と右の余白に収まり、
      基準線の札・ほかの系列の札と重ならない・目盛りの字が重ならない（間引き）
  (c) 動き: 線が左から伸びる・欠けた区間を線でつながない・step は次の点で値が飛ぶ・
      棒が立ち上がる・ローソク足の陽線と陰線の色・寄る（zoom）・印の既定の秒は線の先が着く秒
  (d) 決定性: 同じ入力なら鍵と画素が同じ・順に描いたコマと単独で描いたコマが同じ・
      動かないコマは前のコマを返す
  (e) 鍵: 値で変わる・効かない値（背景の書式・使わない色）では割れない・生のパスを含まない
  (f) 文字の申告（p.audit()）・obj.figure の情報・describe の choices
  (g) エラー（種類違い・範囲外・選択肢・並び・OHLC・zoom の重なり・尺）
  (h) 金型4枚（tests/golden/chart/。版 _CHART_VER と突き合わせる）: 1日の値動き（階段・基準線・
      印・帯・今の値の札）・ローソク足・棒（値の札）・背景の型
  (i) 重さ（1080p の1コマ）・dry_run のコマンド・実レンダ（ffmpeg を通した画素と最後のコマの保持）

ジェイコムの例のような値は形だけの仮のデータ（数値の正確さは問わない）。
numpy・opencv-python・Pillow が無い環境では skip する（理由に語を入れる）。
文字を使う項目は、既定のフォントが無ければ skip する（理由に「フォント」を入れる）。
"""
import json
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta, timezone

import pytest

import scriptvedit as sv
from scriptvedit.context import _exec_stack, activate, current_project

from framekit_golden import assert_golden

np = pytest.importorskip("numpy", reason="numpy が無い環境")
pytest.importorskip("cv2", reason="opencv-python（numpy と一緒に入る図の依存）が無い環境")
pytest.importorskip("PIL", reason="Pillow が無い環境")

from scriptvedit import framekit as fk  # noqa: E402
from scriptvedit import fx_chart  # noqa: E402
from scriptvedit.fx_chart import _CHART_VER, _Renderer, chart  # noqa: E402

WHITE = (255, 255, 255)
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


def _proj(w=480, h=270, fps=30):
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
    base = dict(size=(480, 270), padding=10, label_size=16, tick_size=14)
    base.update(kw)
    return base


def _day_rows():
    """1日の値動き（形だけの仮のデータ。万円。9:31〜9:44 と昼休みは欠ける）"""
    rows = [("9:00", 67.2), ("9:05", 66.4), ("9:10", 65.1), ("9:15", 65.6), ("9:20", 64.9),
            ("9:26", 64.2), ("9:27", 61.0), ("9:28", 57.2), ("9:30", 57.2), ("9:31", None)]
    v = 57.2
    for k, m in enumerate(range(45, 60 * 2 + 1, 15)):
        v = min(77.2, v + (1.6 if k % 3 else -0.4))
        rows.append((f"{9 + m // 60}:{m % 60:02d}", round(v, 1)))
    rows.append(("11:01", None))
    for k, m in enumerate(range(12 * 60 + 30, 15 * 60 + 1, 15)):
        v = min(77.2, v + (0.9 if k % 4 else -0.6))
        rows.append((f"{m // 60}:{m % 60:02d}", round(v, 1)))
    return rows


def _day_chart(mode="main", **kw):
    c = chart(y_format="{v}万", mode=mode, **_small(**kw))
    c.series(_day_rows(), kind="step", at=0.2, dur=3.0, tag="{v:.1f}万円", glow=True)
    c.rule(61, "公開価格 61万円")
    c.rule(57.2, "ストップ安 57.2万円", color="accent")
    c.rule(77.2, "ストップ高 77.2万円")
    c.mark("9:27", "9:27 誤発注", color="accent", text_color="accent")
    c.span("9:31", "9:45", "売買停止")
    return c


def _candle_rows():
    out = []
    d = date(2005, 11, 28)
    v = 80.0
    k = 0
    while d <= date(2005, 12, 16):
        if d.weekday() < 5:
            o = v
            c = o + (2.5 if k % 3 == 0 else -1.8) - (6 if d == date(2005, 12, 8) else 0)
            out.append((d, o, max(o, c) + 1.2, min(o, c) - 0.9, c))
            v = c
            k += 1
        d += timedelta(days=1)
    return out


# --- (a) 軸 -----------------------------------------------------------------------

def test_x_kinds_map_linearly():
    _proj()
    c = chart(**_small()).series([(k, k % 3) for k in range(40)])     # 点の多い線は端まで
    sc = c._resolve(3.0)
    px0, _y0, px1, _y1 = sc["plot"]
    x0, x1 = sc["series"][0]["x0"], sc["series"][0]["x1"]
    assert (x0, x1) == (pytest.approx(px0), pytest.approx(px1))
    _proj()
    c = chart(**_small()).series([(0, 1), (10, 2)])                  # 点の少ない線は端から離す
    sc = c._resolve(3.0)
    assert sc["plot"][0] < sc["series"][0]["x0"] and sc["series"][0]["x1"] < sc["plot"][2]
    _proj()
    c = chart(**_small()).series([("9:00", 1), ("10:00", 2), ("11:00", 3)])
    sc = c._resolve(3.0)
    xs = [p[0] for p in sc["series"][0]["points"]]
    assert xs[1] == pytest.approx((xs[0] + xs[2]) / 2)
    _proj()
    c = chart(**_small()).series([(date(2005, 12, 1), 1), ("2005-12-03", 2),
                                   (datetime(2005, 12, 5), 3)])
    sc = c._resolve(3.0)
    xs = [p[0] for p in sc["series"][0]["points"]]
    assert xs[1] == pytest.approx((xs[0] + xs[2]) / 2)


def test_x_kind_mixing_and_timezone_are_errors():
    _proj()
    c = chart(**_small()).series([("9:00", 1), ("9:30", 2)])
    with pytest.raises(TypeError, match="時刻"):
        c.mark(datetime(2005, 12, 8, 9, 27))
    jst = timezone(timedelta(hours=9))
    c = chart(**_small()).series([(datetime(2005, 12, 8, 9, tzinfo=jst), 1),
                                   (datetime(2005, 12, 8, 10, tzinfo=jst), 2)])
    with pytest.raises(ValueError, match="タイムゾーン"):
        c.mark(datetime(2005, 12, 8, 9, 30))


def test_auto_range_is_nice_and_includes_rules_and_zero_for_bars():
    _need_font()
    _proj()
    c = chart(**_small()).series([(0, 57.2), (1, 77.2)]).rule(50)
    lo, hi = c._resolve(3.0)["base"]
    assert lo <= 50 and hi >= 77.2
    assert lo == pytest.approx(round(lo)) and hi == pytest.approx(round(hi))
    _proj()
    c = chart(**_small()).series([(2019, 113), (2020, 321)], kind="bar")
    assert c._resolve(3.0)["base"][0] == 0
    _proj()
    c = chart(y=(50, 90), **_small()).series([(0, 60), (1, 70)])
    assert c._resolve(3.0)["base"] == (50, 90)


# --- (b) 配置 -----------------------------------------------------------------------

def test_band_labels_do_not_overlap_and_stems_do_not_pierce():
    _need_font()
    _proj(1920, 1080)
    c = chart()
    c.series([(x, 50 + (x % 7)) for x in range(0, 101)], dur=4)
    names = ["短い", "少し長めの札", "とても長い札の文字列", "札", "中くらいの札"]
    for k in range(12):
        c.mark(4 + k * 8.1, names[k % 5])
    c.span(30, 40, "区間の帯")
    sc = c._resolve(6.0)
    boxes = [(m["pos"][0], m["pos"][1], m["pos"][0] + m["sp"].w, m["pos"][1] + m["sp"].h, m)
             for m in sc["marks"] + sc["spans"] if m["sp"] is not None]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            assert not fx_chart._overlap(boxes[i][:4], boxes[j][:4]), (i, j)
    py0 = sc["plot"][1]
    for m in sc["marks"]:
        # 印の線（枠の上端から札の下まで）は、ほかの札を貫かない
        for b in boxes:
            if b[4] is m:
                continue
            if b[0] < m["x"] < b[2]:
                assert not (b[3] > m["top"] and b[1] < py0), (m["x"], b[:4])
        assert m["pos"][1] + m["sp"].h <= py0


def test_tag_stays_in_plot_and_avoids_rule_labels_and_other_tags():
    _need_font()
    _proj(1920, 1080)
    c = chart(y_format="{v}万")
    rows = [(x, 60 + 16 * x / 100) for x in range(0, 101)]
    c.series(rows, dur=3, tag="{v:.1f}万円")
    c.series([(x, 61 + 15 * x / 100) for x in range(0, 101)], dur=3, tag="{v:.1f}万円",
             color="accent")
    c.rule(76, "ストップ高 76万円")
    sc = c._resolve(4.0)
    r = _Renderer(sc)
    track = r._tag_track()
    tw, th = sc["tag_box"]
    px0, py0, px1, py1 = sc["plot"]
    W = sc["size"][0]
    for f in range(0, sc["n_frames"], 3):
        t = f / 30
        view, _tr = r.view(t)
        rule_boxes = [b for b in (r._rule_label_box(ru, view) for ru in sc["rules"]) if b]
        tags = []
        for i in (0, 1):
            h, _q = r.head(sc["series"][i], t)
            if h is None:
                continue
            hy = r.py(h[1], view)
            y0 = min(max(hy - th / 2 + track[i][f], py0), py1 - th)
            box = (h[0] + fx_chart._TAG_GAP, y0, h[0] + fx_chart._TAG_GAP + tw, y0 + th)
            assert box[2] <= W and py0 <= box[1] and box[3] <= py1
            tags.append(box)
        # ならした後（動いている途中を除く）は重ならない
        settled = all(abs(track[i][f] - track[i][min(f + 6, len(track[i]) - 1)]) < 1
                      and abs(track[i][f] - track[i][max(f - 6, 0)]) < 1 for i in (0, 1))
        if settled and len(tags) == 2:
            assert not fx_chart._overlap(tags[0], tags[1]), f
            for b in rule_boxes:
                assert not any(fx_chart._overlap(tb, b) for tb in tags), f


def test_tag_offset_moves_smoothly():
    """札のずらしはコマごとに跳ばない（前後の平均でならす）"""
    _need_font()
    _proj(1920, 1080)
    c = chart(y=(0, 100))
    c.series([(x, 60) for x in range(0, 50)], dur=3, tag=True)
    c.series([(x, 40 + 40 * x / 49) for x in range(0, 50)], dur=3, tag=True)   # 途中で交わる
    r = _Renderer(c._resolve(4.0))
    tr = r._tag_track()
    steps = [abs(a - b) for a, b in zip(tr[1], tr[1][1:])]
    assert max(tr[1]) - min(tr[1]) > 10            # ずれている
    assert max(steps) < (max(tr[1]) - min(tr[1])) / 3


def test_x_tick_labels_are_thinned_and_do_not_overlap():
    _need_font()
    _proj()
    c = chart(**_small())
    c.series([(f"{h}:{m:02d}", 1 + (h + m) % 5) for h in range(9, 15) for m in (0, 30)])
    sc = c._resolve(3.0)
    labels = [(pos[0], pos[0] + sp.w) for _x, sp, pos, _s in sc["xticks"]]
    assert 2 <= len(labels) <= fx_chart._AUTO_TICKS_MAX
    for a, b in zip(labels, labels[1:]):
        assert a[1] + 4 <= b[0]
    texts = [s for *_r, s in sc["xticks"]]
    assert all(":" in s for s in texts)


def test_y_tick_labels_do_not_overlap():
    _need_font()
    _proj()
    c = chart(**_small()).series([(0, 0), (1, 1234)])
    sc = c._resolve(3.0)
    ticks = sc["yticks"][fx_chart._vkey(sc["base"])]
    r = _Renderer(sc)
    ys = sorted(r.py(v, sc["base"]) for v, _sp, _s in ticks)
    h = ticks[0][1].h
    assert all(b - a >= h for a, b in zip(ys, ys[1:]))
    assert "1,000" in [s for _v, _sp, s in ticks]


# --- (c) 動き -----------------------------------------------------------------------

def test_line_grows_from_the_left():
    _proj()
    c = chart(**_small(), y_ticks=[])
    c.series([(0, 1), (10, 2)], at=0.0, dur=1.0, color="accent")
    obj = c.build()
    rights = []
    for i in range(0, 31, 3):
        xs = np.flatnonzero(_near(fk.draw_frame(obj, i), ACCENT).any(axis=0))
        rights.append(xs.max() if len(xs) else -1)
    assert rights == sorted(rights) and rights[0] < rights[-1]
    assert rights[-1] == pytest.approx(obj.figure.x(10), abs=8)


def test_gap_is_not_connected_and_step_jumps():
    _proj()
    c = chart(y=(0, 10), y_ticks=[], x_ticks=[], **_small())
    c.series([(0, 2), (3, 2), (4, None), (6, 8), (10, 8)], kind="step", dur=0.5,
             color="accent")
    obj = c.build()
    img = fk.draw_frame(obj, 29)
    mask = _near(img, ACCENT)
    gap = mask[:, int(obj.figure.x(4.3)):int(obj.figure.x(5.7))]
    assert not gap.any()                              # 欠けた区間は描かない
    y2, y8 = int(round(obj.figure.y(2))), int(round(obj.figure.y(8)))
    assert mask[y2, int(obj.figure.x(3.8))]           # step は None の x まで前の値を保つ
    assert mask[y8, int(obj.figure.x(7))]


def test_bars_rise_and_candles_use_up_and_down_colors():
    _proj()
    c = chart(y=(0, 10), y_ticks=[], x_ticks=[], **_small())
    c.series([(1, 8), (2, 4)], kind="bar", at=0.0, dur=1.0, color="accent")
    obj = c.build()
    x = int(obj.figure.x(1))
    tops = []
    for i in (2, 5, 9, 20):
        col = np.flatnonzero(_near(fk.draw_frame(obj, i), ACCENT)[:, x])
        tops.append(col.min() if len(col) else 10 ** 6)
    assert tops == sorted(tops, reverse=True) and tops[0] > tops[-1]
    assert tops[-1] == pytest.approx(obj.figure.y(8), abs=2)

    _proj()
    c = chart(y=(0, 10), y_ticks=[], x_ticks=[], **_small())
    c.series([(1, 2, 7, 1, 6), (2, 6, 7, 2, 3)], kind="candle", dur=0.5, color="accent",
             down="#2050ff")
    obj = c.build()
    img = fk.draw_frame(obj, 29)
    ym = int(obj.figure.y(4.5))
    assert _near(img, ACCENT)[ym, int(obj.figure.x(1))]          # 陽線
    assert _near(img, (0x20, 0x50, 0xFF))[ym, int(obj.figure.x(2))]   # 陰線


def test_zoom_changes_the_vertical_range_smoothly():
    _proj()
    c = chart(y=(0, 100), x_ticks=[], **_small())
    c.series([(0, 40), (1, 60)], dur=0.3)
    c.zoom((30, 70), at=1.0, dur=1.0)
    sc = c._resolve(3.0)
    r = _Renderer(sc)
    assert r.view(0.9)[0] == (0, 100)
    mid = r.view(1.5)[0]
    assert 0 < mid[0] < 30 and 70 < mid[1] < 100
    assert r.view(2.1)[0] == (30, 70)
    los = [r.view(1.0 + k / 30)[0][0] for k in range(31)]
    assert los == sorted(los)


def test_mark_and_span_default_at_is_when_the_line_reaches_them():
    _proj()
    c = chart(**_small())
    c.series([("9:00", 1), ("11:00", 2)], at=1.0, dur=4.0)
    c.mark("10:00")
    c.span("10:30", "10:45")
    assert c.t_at("10:00") == pytest.approx(3.0)
    sc = c._resolve(6.0)
    assert sc["marks"][0]["at"] == pytest.approx(3.0)
    assert sc["spans"][0]["at"] == pytest.approx(4.0)
    obj = c.build()
    assert obj.figure.t_at("10:00") == pytest.approx(3.0)


# --- (d) 決定性 -----------------------------------------------------------------------

def test_same_input_same_key_and_pixels():
    _need_font()
    _proj()
    o1 = _day_chart().build()
    _proj()
    o2 = _day_chart().build()
    assert o1.source == o2.source
    for i in (0, 30, 60, 100):
        assert np.array_equal(fk.draw_frame(o1, i), fk.draw_frame(o2, i))


@pytest.mark.parametrize("mode", ["main", "background"])
def test_sequential_and_fresh_frames_match_and_settled_frames_are_reused(mode):
    _need_font()
    _proj()
    c = _day_chart(mode=mode)
    c.zoom((55, 66), at=1.2, dur=0.6)
    c.series([(f"{h}:00", 60 + h) for h in range(9, 16)], kind="area", at=0.5, dur=1.5,
             color="accent")
    scene = c._resolve(4.5)
    seq = _Renderer(scene)
    idx = list(range(0, 130, 7))
    outs = [seq.frame(i / 30) for i in idx]
    for k, i in enumerate(idx):
        assert np.array_equal(outs[k], _Renderer(scene).frame(i / 30)), i
    if mode == "main":
        a = seq.frame(4.0)
        b = seq.frame(4.2)
        assert a is b                                # 動かないコマは前のコマを返す


# --- (e) 鍵 -------------------------------------------------------------------------

def test_key_changes_with_data_and_ignores_values_that_do_not_change_the_picture():
    _need_font()

    def src(mode="main", rows=None, **kw):
        _proj()
        c = chart(mode=mode, **_small(**kw))
        c.series(rows or [(0, 1), (1, 2), (2, 1.5)], dur=1.0)
        return c.build().source

    base = src()
    assert src(rows=[(0, 1), (1, 2), (2, 1.6)]) != base
    assert src(colors={"accent": "#123456"}) == base         # 使わない色
    assert src(colors={"dim": "#123456"}) != base            # 格子の色
    bg = src("background")
    assert bg != base
    assert src("background", y_format="{v}万", label_size=40) == bg   # 背景は字を描かない
    assert src("background", drift=0) != bg


def test_key_has_no_raw_paths():
    _need_font()
    _proj()
    params = _day_chart()._params(5.0)
    text = json.dumps(params, ensure_ascii=False)
    assert ":\\\\" not in text and "/Fonts" not in text


# --- (f) 申告・情報・describe -----------------------------------------------------------

def test_text_meta_and_figure_info():
    _need_font()
    _proj()
    obj = _day_chart().build()
    meta = obj._text_image
    assert meta["content"].startswith("chart")
    assert meta["size_min"] == 14
    fig = obj.figure
    assert fig.mode == "main" and fig.size == (480, 270)
    px0, py0, px1, py1 = fig.plot
    assert px0 < fig.x("9:00") + 1e-6 and fig.x("15:00") <= px1 + 1e-6
    assert py0 <= fig.y(70) <= py1
    assert fig.duration == pytest.approx(0.2 + 3.0 + 1.0)
    _proj()
    bg = _day_chart("background").build()
    assert bg._text_image is None and bg.figure.mode == "background"


def _audit(tmp_path, body):
    layer = tmp_path / "chart_audit.py"
    layer.write_text("from scriptvedit import *\n" + body, encoding="utf-8")
    p = sv.Project()
    p.configure(width=1920, height=1080, fps=30, background_color="black")
    p.layer(str(layer), priority=1)
    return [f for f in p.audit(quiet=True) if "chart" in f["message"]]


def test_audit_reads_text_meta(tmp_path):
    """既定（札 36px・目盛り 32px・縁取り 3px か下地）なら warning は出ない。小さすぎる字は出る"""
    _need_font()
    ok = _audit(tmp_path, "c = chart(y_format='{v}万')\n"
                          "c.series([('9:00', 61), ('10:00', 65)], tag='{v:.1f}万円')\n"
                          "c.rule(61, '公開価格')\nc.mark('9:30', '誤発注')\n"
                          "c.build().time()\n")
    assert ok, "申告が読まれていない"
    assert not [f for f in ok if f["severity"] == "warning"]
    small = _audit(tmp_path, "c = chart(tick_size=20)\nc.series([(0, 1), (1, 2)])\n"
                             "c.build().time()\n")
    assert any(f["code"] == "text-too-small" and f["severity"] == "warning" for f in small)


def test_describe_lists_chart_and_choices_match():
    m = sv.describe(name="chart")
    entry = [e for e in m["factories"] if e["name"] == "chart"][0]
    assert entry["category"] != "その他"
    assert entry["params"]["mode"]["choices"] == list(fx_chart._MODES)
    for word in ("series", "rule", "mark", "span", "zoom"):
        assert word in entry["details"], word


# --- (g) エラー ----------------------------------------------------------------------

@pytest.mark.parametrize("make,exc,match", [
    (lambda: chart().series([]), ValueError, "空"),
    (lambda: chart().series([(1, 1), (0, 2)]), ValueError, "増える順"),
    (lambda: chart().series([(0, 1), ("9:00", 2)]), TypeError, "数値"),
    (lambda: chart().series([(0, 1)], kind="pie"), ValueError, "line"),
    (lambda: chart().series([(0, None), (1, None)]), ValueError, "値が1つも"),
    (lambda: chart().series([(0, 1, 2)]), ValueError, r"\(x, y\)"),
    (lambda: chart().series([(0, 5, 4, 3, 4)], kind="candle"), ValueError, "安値"),
    (lambda: chart().series([(0, 1)], kind="bar", glow=True), ValueError, "glow"),
    (lambda: chart().series([(0, 1)], down="red"), ValueError, "down"),
    (lambda: chart().series([(0, 1)], tag="{w}"), ValueError, "tag"),
    (lambda: chart().series([(0, 1)], color="accnt"), ValueError, "accent"),
    (lambda: chart().series([(0, float("inf"))]), ValueError, "NaN"),
    (lambda: chart().series([("25:99", 1)]), ValueError, "HH:MM"),
    (lambda: chart().series([("2005-13-01", 1)]), ValueError, "ISO"),
    (lambda: chart().series([(0, 1)], at=-1), ValueError, "at"),
    (lambda: chart().series([(0, 1)], dur=0), ValueError, "dur"),
    (lambda: chart(mode="bg"), ValueError, "background"),
    (lambda: chart(drift=10), ValueError, "background"),
    (lambda: chart(y=(5, 5)), ValueError, "上端"),
    (lambda: chart(x=(0, 10)).series([(0, 1), (11, 2)]).build(), ValueError, "範囲の外"),
    (lambda: chart().span(3, 3), ValueError, "a より後"),
    (lambda: chart().mark(1, "  "), ValueError, "空でない"),
    (lambda: chart().rule(1, dash="yes"), TypeError, "dash"),
    (lambda: chart().series([(0, 1), (1, 2)]).zoom((0, 1), at=1, dur=1)
     .zoom((0, 2), at=1.5).build(), ValueError, "重なって"),
    (lambda: chart().series([(0, 1), (1, 2)], dur=3).build(duration=2), ValueError, "duration"),
    (lambda: chart().rule(1).build(), ValueError, "系列"),
    (lambda: chart().t_at(1), ValueError, "系列"),
    (lambda: chart(y_format="{x}"), ValueError, "y_format"),
])
def test_errors(make, exc, match):
    _proj()
    with pytest.raises(exc, match=match):
        make()


# --- (h) 金型 -------------------------------------------------------------------------

def _golden_day():
    _proj(640, 360)
    return _day_chart(size=(640, 360)).build(), 3.0


def _golden_candle():
    _proj()
    c = chart(y_format="{v}万", **_small())
    c.series(_candle_rows(), kind="candle", at=0.0, dur=1.5, tag="{v:.1f}万円")
    c.mark(date(2005, 12, 8), "12/8", color="accent", text_color="accent", dash=True)
    return c.build(), 2.0


def _golden_bars():
    _proj()
    c = chart(x_format="{v}年", **_small())
    c.series([(2019, 113), (2020, 160), (2021, 205), (2022, 248), (2023, 321)], kind="bar",
             at=0.0, dur=1.0, values="{v}件")
    return c.build(), 0.75


def _golden_background():
    _proj()
    c = _day_chart("background")
    return c.build(duration=6.0), 4.0


@pytest.mark.parametrize("name,make", [("day", _golden_day), ("candle", _golden_candle),
                                       ("bars", _golden_bars),
                                       ("background", _golden_background)])
def test_golden(request, name, make):
    _need_font()
    obj, t = make()
    ffp = fk.font_ref("test").ffp
    assert_golden(request, "chart", name, _frame_at(obj, t), ver=_CHART_VER, font_ffp=ffp)


# --- (i) 重さ・dry_run・実レンダ ------------------------------------------------------

def test_frame_cost_is_small():
    """1080p の動いているコマの描画（平均）。CI の揺れを見て閾値は緩める"""
    _need_font()
    _proj(1920, 1080)
    c = chart(y_format="{v}万")
    c.series(_day_rows() * 1, kind="step", at=0.2, dur=3.0, tag="{v:.1f}万円", glow=True)
    c.rule(61, "公開価格 61万円")
    c.mark("9:27", "9:27 誤発注", color="accent")
    r = _Renderer(c._resolve(4.0))
    r.frame(0.0)
    t0 = time.perf_counter()
    n = 0
    for i in range(1, 110):
        r.frame(i / 30)
        n += 1
    mean_ms = (time.perf_counter() - t0) * 1000.0 / n
    assert mean_ms < 120.0, mean_ms


def test_dry_run_command_uses_the_frames_movie(tmp_path):
    """レイヤーの中で作った chart は frames の .mov を入力にした Object になる（dry_run は描かない）"""
    _need_font()
    layer = tmp_path / "chart_dry.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "c = chart(size=(320, 180), padding=8, label_size=14, tick_size=12)\n"
        "c.series([(0, 1), (1, 3), (2, 2)], dur=1.0)\n"
        "c.build().time()\n", encoding="utf-8")
    p = sv.Project()
    p.configure(width=320, height=180, fps=30, background_color="black")
    p.layer(str(layer), priority=0)
    out = p.render(str(tmp_path / "x.mp4"), dry_run=True)
    text = json.dumps(out, ensure_ascii=False)
    assert "artifacts/frames/" in text.replace("\\\\", "/") and ".mov" in text
    assert not list((tmp_path / "__cache__" / "artifacts" / "frames").glob("*.mov")) \
        if (tmp_path / "__cache__").exists() else True


def _need_ffmpeg():
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg / ffprobe が無い環境")


def _decode(path, t, w, h):
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.4f}", "-i", path, "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.uint8).reshape(h, w, 3)


def test_real_render_line_and_hold(tmp_path):
    """ffmpeg を通した出力でも線が左から伸びて赤くなり、尺より長い表示で最後のコマが残る"""
    _need_ffmpeg()
    layer = tmp_path / "chart_real.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "c = chart(y=(0, 10), x_ticks=[], y_ticks=[], size=(320, 180), padding=10)\n"
        "c.series([(0, 5), (10, 5)], at=0.2, dur=0.6, color='accent', width=8)\n"
        "obj = c.build()\n"
        "obj.time(obj.figure.duration + 1.0)\n", encoding="utf-8")
    p = sv.Project()
    p.configure(width=320, height=180, fps=30, background_color="black")
    p.layer(str(layer), priority=0)
    out = str(tmp_path / "chart.mp4")
    p.render(out)
    _proj(320, 180)
    ref = chart(y=(0, 10), x_ticks=[], y_ticks=[], size=(320, 180), padding=10)
    ref.series([(0, 5), (10, 5)], at=0.2, dur=0.6, color="accent", width=8)
    fig = ref.build().figure
    y, x = int(round(fig.y(5))), int(fig.x(8))
    first = _decode(out, 0.05, 320, 180)
    assert int(first[y, x][0]) < 60                          # まだ伸びていない
    late = _decode(out, 1.2, 320, 180)
    r, g, b = (int(v) for v in late[y, x])
    assert r > 170 and g < 90 and b < 90                     # 伸びたあとは赤（accent）
    held = _decode(out, 2.4, 320, 180)                       # 尺（1.8 秒）より後
    assert int(held[y, x][0]) > 170
