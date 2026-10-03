# -*- coding: utf-8 -*-
"""globe()（点で描いた地球と世界地図）のテスト。

固定すること:
  - 投影: view=(0, 0) で (0, 0) が中心、(0, 90) が右の縁、(0, 180) が裏（None）
  - turn の slerp: 等角速度で、日付変更線・極の近くで遠回り（回り込み）しない
  - 弧の隠れ: 地球の裏へ回った部分は描かれず、縁の外へ出た部分は描かれる
  - 太陽の真下（NOAA の簡略式）: 2024-07-19 04:09 UTC が北緯約 20.8・東経約 119.3
    （天文年鑑の略算式との差 0.5 度以内）、春分・夏至の赤緯
  - 陸を多角形で渡したとき、点の数が球面上の面積に比例する
  - plate の弧が日付変更線（キャンバスの端）で分かれて描かれる。縦の弦の弧は地図の内側へ
    反り、反りで端をまたいで反対の端に描かれない（止まった向きのすべてで）
  - 濃淡つきの線（弧の尾）は長い線分を切って描く（極を通る plate の弧でメモリが膨らまない）
  - step=None は画面上の点の間隔 ≒ 3.9×dot px（小さい地球で点がつぶれない）
  - points の appear（wave の順・staged の件数）
  - 決定性（同じ指定なら同じ画素・同じ鍵）。PNG の中身で鍵が変わり、パスでは変わらない。
    plate の turn の緯度は鍵に入らない
  - 範囲外の ValueError・spin の警告・札の重なりの警告・キャンバスの上限で切れる警告・
    札の豆腐の案内・framekit の内部関数を使わないこと
  - 実レンダ（ffmpeg）の生成物のコマが draw_frame と画素で一致する
  - 金型 6 枚（tests/golden/globe/。描き方を変えたら _GLOBE_VER を上げる）
  - scripts/make_land_mask.py: struct で書いた小さな .shp を読んで塗る（ネットに出ない）・
    --check（同じ手順で作ったマスクと PNG の画素・tEXt を比べる）
  - 同梱の地球（land=True が既定）: PNG の形と tEXt・NOTICE.md の出典と SHA-256・
    pyproject の package-data。land=False は陸なし、land=None は ValueError
  - 拠点の点は、まわりの陸の点を抜いて（堀）光の輪を敷くので、既定の色でも陸に埋もれない。
    堀と光の輪は円を分けて計算しても同じ画素で、メモリは点の数に比例しない（2 万点で 120MB 未満）。
    points(halo=False) は芯だけ（既定の halo=True は鍵に入らない）
  - docstring の堀・光の輪の数字が定数と同じ。globe() / points() の引数の説明が入れ子にならない
  - make_land_mask.py は出力先を省いたまま --width を変えると止まる（同梱の PNG を上書きしない）
  - plate の view="auto"（plate の既定）: 弧が地図の端をまたがない中心の経度を選ぶ。
    選んだ経度は手で書いた view と同じ鍵。何も無ければ 0。ortho の "auto" は ValueError
"""
import hashlib
import importlib.util
import inspect
import math
import os
import re
import shutil
import struct
import subprocess
import tracemalloc
import warnings
import zipfile
from datetime import datetime, timedelta, timezone
from fractions import Fraction

import pytest

np = pytest.importorskip("numpy", reason="numpy が無い環境")
pytest.importorskip("cv2", reason="numpy・opencv が無い環境")
pytest.importorskip("PIL", reason="Pillow が無い環境")
from PIL import Image  # noqa: E402

import scriptvedit as sv  # noqa: E402
from scriptvedit import framekit as fk  # noqa: E402
from scriptvedit import fx_globe as G  # noqa: E402
from scriptvedit.context import _exec_stack, activate, current_project  # noqa: E402
from scriptvedit.text import _resolve_font  # noqa: E402

from framekit_golden import assert_golden  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

TOKYO, LONDON, SF, SYDNEY = (35.7, 139.7), (51.5, -0.1), (37.8, -122.4), (-33.9, 151.2)


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


def _dry(w=640, h=360, fps=30):
    """dry_run の Project（build しても動画を作らない。draw_frame でコマを描ける）"""
    p = sv.Project()
    p.configure(width=w, height=h, fps=fps)
    p._dry_run = True
    return p


def _renderer(g, fps=30):
    spec = g._spec()
    spec["fps"] = Fraction(fps)
    return G._Renderer(spec, g._sprites(spec))


def _box(lon0, lon1, lat0, lat1, n=24):
    """経度・緯度の箱の多角形（緯線に沿って細かく点を打つ）"""
    top = [(lon0 + (lon1 - lon0) * k / n, lat1) for k in range(n + 1)]
    bottom = [(lon1 - (lon1 - lon0) * k / n, lat0) for k in range(n + 1)]
    return top + bottom


def _red(img):
    """赤い画素（弧・波紋）の判定（RGBA の uint8）"""
    r, g, b, a = (img[..., k].astype(int) for k in range(4))
    return (a > 40) & (r > g + 60) & (r > b + 60)


# --- 投影 ------------------------------------------------------------------------------

def test_projection_center_right_edge_and_back():
    _dry()
    g = G.globe(size=400, view=(0, 0), atmosphere=0)
    obj = g.build(duration=0.1)
    xy = obj.figure.xy
    R = 0.46 * 400
    assert obj.figure.radius == pytest.approx(R) and obj.figure.center == (200.0, 200.0)
    assert xy((0, 0)) == pytest.approx((200.0, 200.0))
    assert xy((0, 90)) == pytest.approx((200.0 + R, 200.0))       # 右の縁
    assert xy((0, -90)) == pytest.approx((200.0 - R, 200.0))      # 左の縁
    assert xy((90, 0)) == pytest.approx((200.0, 200.0 - R))       # 北極が上
    # 既定の大気（0.25）は光のぶん上下・左右に対称な余白を足す（地球は真ん中のまま）
    o2 = G.globe(size=400, view=(0, 0)).build(duration=0.1)
    mx, my = o2.figure.margin
    assert mx == my and 0.2 * R < mx + 200 - R < 0.3 * R    # 光は縁の外へ半径の約 24%
    assert o2.figure.center == (200.0 + mx, 200.0 + my) and o2.figure.size == (400 + 2 * mx,) * 2
    assert o2.figure.xy((0, 90)) == pytest.approx((200.0 + mx + R, 200.0 + my))
    assert xy((0, 180)) is None                                    # 裏
    spec = g._spec()
    x, y, z = G._project_one(spec, G._orient_at(spec, 0), 0, 90)
    assert z == pytest.approx(0.0, abs=1e-12)
    assert G._project_one(spec, G._orient_at(spec, 0), 0, 180)[2] == pytest.approx(-1.0)


def test_plate_projection_and_named_points():
    _dry()
    g = G.globe(projection="plate", size=720, view=(0, 30))
    g.points([TOKYO, SF], name="cities")
    obj = g.build(duration=0.1)
    assert obj.figure.size == (720, 360) and obj.figure.center is None
    assert obj.figure.xy((0, 30)) == pytest.approx((360.0, 180.0))
    assert obj.figure.xy((90, 30))[1] == pytest.approx(0.0)
    (x1, y1), (x2, y2) = obj.figure.xy("cities", 0)
    assert x1 == pytest.approx(360 + (139.7 - 30) * 2) and y1 == pytest.approx((90 - 35.7) * 2)
    assert x2 == pytest.approx(360 + (-122.4 - 30 + 360) * 2 - 720)
    with pytest.raises(ValueError, match="名前"):
        obj.figure.xy("nope", 0)


# --- turn（四元数の slerp）-------------------------------------------------------------

def _quat_angle(a, b):
    d = abs(sum(x * y for x, y in zip(a, b)))
    return 2 * math.acos(min(1.0, d))


def _centers(spec, ts):
    out = []
    for t in ts:
        M = G._matrix_from_quat(G._orient_at(spec, t))
        out.append(M[2])          # 画面の中心に来ている地点（地球に固定した単位ベクトル）
    return out


def test_turn_constant_angular_speed_and_short_way_across_dateline():
    g = G.globe(view=(0, 170))
    g.turn(0.0, (0, -170), dur=1.0, easing="linear")
    spec = g._spec()
    ts = [k / 20 for k in range(21)]
    qs = [G._orient_at(spec, t) for t in ts]
    steps = [_quat_angle(a, b) for a, b in zip(qs, qs[1:])]
    assert max(steps) - min(steps) < 1e-9                       # 等角速度
    assert sum(steps) == pytest.approx(math.radians(20), abs=1e-9)   # 近い向き（20 度）
    lons = [G._latlon(c)[1] for c in _centers(spec, ts)]
    # 170 → 180 → -170 と日付変更線を渡る（経度の補間なら 340 度戻る）
    unwrapped = np.degrees(np.unwrap(np.radians(lons)))
    assert np.all(np.diff(unwrapped) > 0)
    assert unwrapped[-1] - unwrapped[0] == pytest.approx(20, abs=1e-6)
    # 終わりは北が上（view=(0, -170) と同じ向き）
    end = G._quat_from_matrix(G._view_matrix(0, -170))
    assert _quat_angle(G._orient_at(spec, 1.0), end) < 1e-9


def test_turn_near_pole_does_not_go_around():
    a, b = (50.0, 10.0), (64.0, -150.0)        # 大円は北極の近くを通る
    g = G.globe(view=a)
    g.turn(0.0, b, dur=1.0, easing="linear")
    spec = g._spec()
    ts = [k / 40 for k in range(41)]
    qs = [G._orient_at(spec, t) for t in ts]
    steps = [_quat_angle(p, q) for p, q in zip(qs, qs[1:])]
    assert max(steps) - min(steps) < 1e-9
    cs = _centers(spec, ts)
    ua, ub = G._unit(*a), G._unit(*b)
    da = [G._angle(ua, c) for c in cs]
    db = [G._angle(ub, c) for c in cs]
    # 出発点からは遠ざかる一方・目的地へは近づく一方（回り込んで戻らない）
    assert all(x <= y + 1e-12 for x, y in zip(da, da[1:]))
    assert all(x >= y - 1e-12 for x, y in zip(db, db[1:]))
    path = sum(G._angle(p, q) for p, q in zip(cs, cs[1:]))
    # 緯度・経度を別々に補間したときの道のり（経度で 160 度回り込む）より短い
    euler = sum(G._angle(G._unit(a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s),
                         G._unit(a[0] + (b[0] - a[0]) * s2, a[1] + (b[1] - a[1]) * s2))
                for s, s2 in zip(np.linspace(0, 1, 400)[:-1], np.linspace(0, 1, 400)[1:]))
    assert path <= euler + 1e-6
    # 最後は北が上
    M = G._matrix_from_quat(G._orient_at(spec, 1.0))
    assert G._latlon(M[2]) == pytest.approx(b, abs=1e-6)
    assert M[1][2] > 0                         # 画面の上向きが北側


def test_spin_rotates_surface_left_to_right_and_warns():
    g = G.globe(view=(0, 0))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        g.spin(0.0, 2.0, 2.0)                  # 3 度/秒まで警告しない
    spec = g._spec()
    M = G._matrix_from_quat(G._orient_at(spec, 1.0))
    assert G._latlon(M[2])[1] == pytest.approx(-2.0)      # 中心が西へ（表面が右へ流れる）
    g2 = G.globe()
    with pytest.warns(UserWarning, match="度/秒"):
        g2.spin(0.0, 1.0, 5.0)
    with pytest.raises(ValueError, match="重なって"):
        g2.turn(0.5, TOKYO)


# --- 弧 ----------------------------------------------------------------------------------

def test_arc_hidden_part_is_not_drawn_and_outside_limb_is_drawn():
    _dry()
    src, dst = (40.0, 50.0), (-40.0, 165.0)    # 表の右上から、右の縁を越えて裏の下へ
    g = G.globe(size=400, view=(0, 0), graticule=False)
    g.arc(src, dst, t=0.0, dur=0.5, height=0.3, head=False, trail=0)
    obj = g.build(duration=0.6)
    img = fk.draw_frame(obj, 17)               # 弧は描き終わっている
    red = _red(img)
    # 縁の外へ出る弧のぶん、キャンバスは上下・左右に対称に広がる（地球は真ん中のまま）
    (cx, cy), R = obj.figure.center, obj.figure.radius
    mx, my = obj.figure.margin
    assert mx > 0 and img.shape[1] == 400 + 2 * mx and img.shape[0] == 400 + 2 * my
    assert (cx, cy) == (200.0 + mx, 200.0 + my) and R == pytest.approx(184.0)
    yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    rr = np.hypot(xx + 0.5 - cx, yy + 0.5 - cy)
    assert red[rr > R + 4].sum() > 20          # 縁の外へ出た部分は描かれる
    assert not red[:, :2].any() and not red[:, -2:].any()   # キャンバスの端で切れていない
    assert not red[:2].any() and not red[-2:].any()
    # 弧の全体を細かく取り、隠れる所（z < 0 かつ円の内側）の画素に赤が無いことを見る
    r = G._Renderer(obj.figure.xy._spec, {})
    r.np = np
    spec = obj.figure.xy._spec
    M = np.array(G._matrix_from_quat(G._orient_at(spec, 0.6)))
    ev = [e for e in spec["events"] if e["kind"] == "arc"][0]
    s = np.linspace(0, 1, 2001)
    c = r._arc_screen(ev, s, M)
    hidden = r._arc_hidden(c)
    assert hidden.any() and (~hidden).any()
    hx, hy = cx + R * c[hidden, 0], cy - R * c[hidden, 1]
    vx, vy = cx + R * c[~hidden, 0], cy - R * c[~hidden, 1]
    far = np.array([np.min(np.hypot(vx - x, vy - y)) > 6 for x, y in zip(hx, hy)])
    assert far.sum() > 100
    for x, y in zip(hx[far], hy[far]):
        assert not red[int(y), int(x)], (x, y)
    # 見える区間の端は隠れる境目まで詰めてある（二分法）
    pls = r.arc_polylines(ev, 1.0, G._orient_at(spec, 0.6), M)
    assert len(pls) == 1 and pls[0][2][-1] < 1.0
    edge = r._arc_screen(ev, np.array([pls[0][2][-1]]), M)[0]
    assert edge[0] ** 2 + edge[1] ** 2 == pytest.approx(1.0, abs=1e-4)


def test_plate_arc_splits_at_dateline():
    _dry()
    g = G.globe(projection="plate", size=720, view=(0, 0), graticule=False)
    g.arc(TOKYO, SF, t=0.0, dur=0.5, head=False, trail=0)
    obj = g.build(duration=0.6)
    img = fk.draw_frame(obj, 17)
    red = _red(img)
    cols = red.any(axis=0)
    assert cols[700:].any() and cols[:20].any()            # 右端から出て左端から続く
    assert not cols[260:460].any()                          # 地図を横切る線は無い
    # 折れ線の段階でも2本に分かれる
    spec = obj.figure.xy._spec
    r = G._Renderer(spec, {})
    r.np = np
    ev = spec["events"][0]
    (xs, ys, ss), = r.arc_polylines(ev, 1.0, 0.0, None)
    pieces = G._split_wrapped(np, xs, ys, ss, 720)
    assert len(pieces) == 2
    assert pieces[0][0][-1] == pytest.approx(720.0) and pieces[1][0][0] == pytest.approx(0.0)
    assert pieces[0][1][-1] == pytest.approx(pieces[1][1][0])


def test_plate_arc_lift_stays_inside_canvas():
    _dry()
    g = G.globe(projection="plate", size=720, graticule=False)
    g.arc(LONDON, TOKYO, t=0.0, dur=0.5, height=0.6, head=False)   # 極の近くを通る
    obj = g.build(duration=0.6)
    img = fk.draw_frame(obj, 17)
    red = _red(img)
    assert red.any() and not red[:2].any()                 # 上端で切れない


def test_plate_steep_arc_bulges_inward_and_never_wraps():
    """縦の弦の弧は地図の内側へ反り、反りで日付変更線をまたいで反対の端に描かれない"""
    _dry()
    # 右端の近く（東経 175 度 = x 710）を南へ。反りは左（内側）へ。左端には何も描かない
    g = G.globe(projection="plate", size=720, view=(0, 0), graticule=False)
    g.arc((40, 175), (-40, 175), t=0.0, dur=0.5, height=0.3, head=False, trail=0)
    obj = g.build(duration=0.6)
    red = _red(fk.draw_frame(obj, 17))
    cols = np.flatnonzero(red.any(axis=0))
    assert len(cols) and cols.min() > 600, cols[:5]       # 左端（反対の端）に出ない
    assert cols.min() < 690                                 # 反りは内側（左）へ十分ふくらむ
    r = G._Renderer(obj.figure.xy._spec, {})
    r.np = np
    nx_, ny_, amp = r._plate_lift(obj.figure.xy._spec["events"][0])
    assert nx_ < -0.99 and amp > 0.25 * 160              # 弦 160px の 0.3 倍近く反る
    # 左端の近く（西経 170〜175 度）を南へ。反りは右（内側）へ。右端には何も描かない
    g = G.globe(projection="plate", size=720, view=(0, 0), graticule=False)
    g.arc((60, -170), (-30, -175), t=0.0, dur=0.5, height=0.3, head=False, trail=0)
    red = _red(fk.draw_frame(g.build(duration=0.6), 17))
    cols = np.flatnonzero(red.any(axis=0))
    assert len(cols) and cols.max() < 120 and cols.max() > 40
    # 弧の後で地図が回り、弧が左端へ寄って止まる: 止まった向きでも反りが端をまたがない
    g = G.globe(projection="plate", size=720, view=(0, 0), graticule=False)
    g.arc((40, 100), (-40, 100), t=0.0, dur=0.3, height=0.3, head=False, trail=0)
    g.turn(0.4, (0, -90), dur=0.5)             # 東経 100 度が x = 20 へ
    obj = g.build(duration=1.0)
    red = _red(fk.draw_frame(obj, 29))
    cols = np.flatnonzero(red.any(axis=0))
    assert len(cols) and cols.max() < 60, cols[-5:]       # 右端（反対の端）に出ない
    # 描く順に依らない（反りは spec だけで決まる）
    obj2 = g.build(duration=1.0)
    assert np.array_equal(fk.draw_frame(obj2, 29), fk.draw_frame(obj, 29))
    # 日付変更線を本当に渡る弧は、これまでどおり端で分かれる（反りで消されない）
    g = G.globe(projection="plate", size=720, view=(0, 0), graticule=False)
    g.arc(TOKYO, SF, t=0.0, dur=0.5, height=0.3, head=False, trail=0)
    cols = _red(fk.draw_frame(g.build(duration=0.6), 17)).any(axis=0)
    assert cols[700:].any() and cols[:20].any()


def test_fade_line_long_segments_keep_memory_small():
    """濃淡つきの線の長い線分は細かく切って描く（窓が線分の長さの2乗で膨らまない）"""
    dst = fk.canvas(2000, 100)
    G._fade_line(np, dst, np.array([5.0, 1995.0]), np.array([50.0, 50.0]),
                 np.array([1.0, 0.3]), (255, 0, 0, 255), 3.0)      # 一度描いて import を済ませる
    dst = fk.canvas(2000, 100)
    tracemalloc.start()
    G._fade_line(np, dst, np.array([5.0, 1995.0]), np.array([50.0, 50.0]),
                 np.array([1.0, 0.3]), (255, 0, 0, 255), 3.0)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 40e6, peak                     # 切らないと 1 本の窓が 2000² で数百 MB
    a = dst[50, :, 3]
    # 線の上の不透明度は端点の値を線形に補間したもの（芯の画素は被覆率 1）
    for x in (100, 1000, 1900):
        want = 1.0 + (0.3 - 1.0) * (x + 0.5 - 5.0) / 1990.0
        assert a[x] == pytest.approx(want, abs=0.01), x
    assert np.all(np.diff(a[10:1990]) <= 1e-6)              # 尾へ向かって単調に薄れる
    assert dst[46, 1000, 3] == 0 and dst[54, 1000, 3] == 0  # 太さ 3px の外は塗らない
    # plate で極の上を通る弧（経度が 180 度飛ぶ所が画面上で長い線分になる）も重くならない
    _dry()
    g = G.globe(projection="plate", size=1920, graticule=False)
    g.arc((45, 0), (45, 180), t=0.0, dur=0.5)
    obj = g.build(duration=0.6)
    fk.draw_frame(obj, 0)
    tracemalloc.start()
    img = fk.draw_frame(obj, 17)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 400e6, peak                    # 修正前は 1.5GB
    assert _red(img)[:20].any()                   # 極を通る所は上端に沿って描かれる


# --- 太陽の真下 ------------------------------------------------------------------------

def _almanac_subsolar(when):
    """別の式（天文年鑑の略算式。赤経・赤緯とグリニッジ恒星時）"""
    n = (when - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / 86400.0
    L = (280.460 + 0.9856474 * n) % 360
    gm = math.radians((357.528 + 0.9856003 * n) % 360)
    lam = math.radians(L + 1.915 * math.sin(gm) + 0.020 * math.sin(2 * gm))
    eps = math.radians(23.439 - 0.0000004 * n)
    ra = math.degrees(math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam)))
    dec = math.degrees(math.asin(math.sin(eps) * math.sin(lam)))
    gmst = (280.46061837 + 360.98564736629 * n) % 360
    return dec, ((ra - gmst) + 180) % 360 - 180


def test_subsolar_crowdstrike_time_and_seasons():
    when = datetime(2024, 7, 19, 4, 9, tzinfo=timezone.utc)
    lat, lon = G._subsolar(when)
    alat, alon = _almanac_subsolar(when)
    assert abs(lat - alat) < 0.5 and abs(lon - alon) < 0.5
    assert lat == pytest.approx(20.8, abs=0.5) and lon == pytest.approx(119.3, abs=0.5)
    # 時差つきの datetime も UTC に直して同じ点（東京 13:09 = 04:09 UTC）
    jst = timezone(timedelta(hours=9))
    assert G._subsolar(datetime(2024, 7, 19, 13, 9, tzinfo=jst)) == pytest.approx((lat, lon))
    eq = G._subsolar(datetime(2024, 3, 20, 3, 6, tzinfo=timezone.utc))      # 春分
    assert abs(eq[0]) < 0.5
    sol = G._subsolar(datetime(2024, 6, 20, 20, 51, tzinfo=timezone.utc))   # 夏至
    assert sol[0] == pytest.approx(23.44, abs=0.2)
    _dry()
    g = G.globe()
    g.night(when)
    assert g.build(duration=0.1).figure.subsolar == pytest.approx((lat, lon))
    with pytest.raises(ValueError, match="naive"):
        G.globe().night(datetime(2024, 7, 19, 4, 9))
    with pytest.raises(ValueError, match="1回だけ"):
        g.night(when)


def test_night_dims_the_dark_side():
    _dry()
    when = datetime(2024, 7, 19, 4, 9, tzinfo=timezone.utc)
    land = [_box(-179, 179, -60, 60, n=60)]
    day = G.globe(projection="plate", size=360, land=land, view=(0, 0), step=5.0, dot=2.0)
    day.night(when)
    img = fk.draw_frame(day.build(duration=0.1), 0)
    a = img[..., 3].astype(float)
    # 昼（東経 120 度の赤道付近）と夜（西経 60 度の赤道付近）
    x_day, x_night = int(180 + 120 / 2), int(180 - 60 / 2)
    y = 90
    lit = a[y - 6:y + 6, x_day - 6:x_day + 6].mean()
    dark = a[y - 6:y + 6, x_night - 6:x_night + 6].mean()
    assert dark < 0.4 * lit


# --- 陸地 ------------------------------------------------------------------------------

def _land_count(land, step=1.2):
    g = G.globe(land=land, step=step)
    r = _renderer(g)
    r._prepare()
    return len(r.layers[-1]["v"])


def _sphere_area(lon0, lon1, lat0, lat1):
    return math.radians(lon1 - lon0) * (math.sin(math.radians(lat1)) - math.sin(math.radians(lat0)))


def test_land_polygon_point_count_is_proportional_to_area():
    boxes = [(0, 40, 0, 20), (0, 80, 0, 20), (0, 40, 60, 80), (-120, -40, -50, -10)]
    counts = [_land_count([_box(*b)]) for b in boxes]
    areas = [_sphere_area(*b) for b in boxes]
    dens = [c / a for c, a in zip(counts, areas)]
    assert max(dens) / min(dens) < 1.06, (counts, areas)
    # 全体の点の数（step 1.2 度で約 2.9 万点）
    assert G._fib_count(1.2) == pytest.approx(28650, rel=0.01)


def test_land_png_key_follows_content_not_path(tmp_path):
    def png(path, fill):
        im = Image.new("1", (72, 36), 0)
        for x in range(10, 30):
            for y in range(8, 20 + fill):
                im.putpixel((x, y), 1)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        im.save(path)
        return path

    _dry()
    a = png(str(tmp_path / "a" / "land.png"), 0)
    b = png(str(tmp_path / "b" / "other.png"), 0)      # 同じ中身・別のパス
    c = png(str(tmp_path / "c" / "land.png"), 3)       # 別の中身
    src = [G.globe(land=p).build(duration=0.1).source for p in (a, b, c)]
    assert src[0] == src[1] and src[0] != src[2]
    with pytest.raises(ValueError, match="2:1"):
        bad = tmp_path / "bad.png"
        Image.new("1", (50, 50)).save(bad)
        G.globe(land=str(bad))


# --- 点の密度（step=None）-------------------------------------------------------------

def test_default_step_follows_on_screen_spacing():
    """step=None は画面上の点の間隔 ≒ 3.9×dot px（ortho は地球の中心で）。小さい地球ほど粗い"""
    with warnings.catch_warnings():
        warnings.simplefilter("error")             # 既定の step では「点が触れる」警告は出ない
        for size in (240, 300, 480, 900, 1600):
            g = G.globe(size=size)
            spacing = 0.46 * size * math.radians(g._p["step"])
            assert spacing == pytest.approx(3.9 * 2.2, rel=0.01), size
        assert G.globe(size=900)._p["step"] == pytest.approx(1.19)
        assert G.globe(size=300)._p["step"] > 3 * G.globe(size=900)._p["step"] - 0.1
        g = G.globe(size=900, radius=150)          # radius で小さくした地球も同じ密度
        assert 150 * math.radians(g._p["step"]) == pytest.approx(3.9 * 2.2, rel=0.01)
        g = G.globe(size=600, dot=3.0)             # 大きい点は粗く
        assert 0.46 * 600 * math.radians(g._p["step"]) == pytest.approx(3.9 * 3.0, rel=0.01)
        g = G.globe(projection="plate", size=1400)
        assert 1400 / 360 * g._p["step"] == pytest.approx(3.9 * 2.2, rel=0.01)
        assert G.globe(size=4096)._p["step"] == pytest.approx(0.6)    # 下限で丸める
    # 上限（5 度）で丸めても詰まる小さな地球・明示した細かい step は警告する
    with pytest.warns(UserWarning, match="触れて"):
        assert G.globe(size=100)._p["step"] == pytest.approx(5.0)
    with pytest.warns(UserWarning, match="触れて"):
        G.globe(size=300, step=1.2)


def test_small_globe_is_dotted_not_solid():
    """300px の地球でも陸が点のまま（固定の 1.2 度では点がつぶれて白い面になっていた）"""
    _dry()
    land = [_box(-60, 60, -45, 45, n=60)]
    g = G.globe(size=300, land=land, view=(0, 0), graticule=False)
    img = fk.draw_frame(g.build(duration=0.1), 0)
    a = img[150 - 40:150 + 40, 150 - 40:150 + 40, 3].astype(float) / 255.0   # 陸の中央
    assert a.mean() < 0.35, a.mean()              # 面ならほぼ 0.8（陸の不透明度）
    assert (a < 0.04).mean() > 0.4                # 点の間に隙間がある
    assert (a > 0.5).mean() > 0.05                # 点そのものは見える


# --- 点き方 ----------------------------------------------------------------------------

def test_appear_wave_order_and_staged_counts():
    origin = (0.0, 0.0)
    coords = [(0, 80), (0, 10), (0, 40), (10, 0), (0, 160)]
    g = G.globe(seed=3)
    g.points(coords, t=1.0, appear=("wave", origin, 40))
    g.points(coords, t=2.0, appear=("staged", [(0.5, 1), (0.8, 5)]))
    spec = g._spec()
    wave, staged = (e["_appear"] for e in spec["events"])
    dist = [math.degrees(G._angle(G._unit(*origin), G._unit(*c))) for c in coords]
    assert wave == pytest.approx([1.0 + d / 40 for d in dist])
    assert sorted(range(5), key=lambda k: wave[k]) == sorted(range(5), key=lambda k: dist[k])
    assert 2.5 <= staged[0] <= 2.5 + 0.15
    assert all(2.8 <= s <= 2.8 + G._STAGED_SPREAD for s in staged[1:])
    # seed が同じなら同じ時刻、違えばばらつきが変わる
    g2 = G.globe(seed=3)
    g2.points(coords, t=2.0, appear=("staged", [(0.5, 1), (0.8, 5)]))
    assert g2._spec()["events"][0]["_appear"] == staged
    # 画素でも: staged の最初の段だけが点いているコマ
    _dry()
    g3 = G.globe(size=200, view=(0, 40), graticule=False)
    g3.points([(0, 40), (10, 30), (-10, 50)], appear=("staged", [(0.2, 1), (0.6, 3)]),
              dur=0.1, radius=3)
    obj = g3.build()
    n = int(round(obj.length() * 30))

    def lit(img):                      # 白い点の画素（縁の線は暗い灰なので数えない）
        return int(((img[..., 3] > 128) & (img[..., 0] > 230)).sum())
    one = lit(fk.draw_frame(obj, int(0.45 * 30)))
    allp = lit(fk.draw_frame(obj, n - 1))
    assert one > 0 and allp > 2.5 * one


# --- 決定性・鍵 ------------------------------------------------------------------------

def _scene(g):
    g.points([TOKYO, LONDON, SF], t=0.0)
    g.arc(LONDON, TOKYO, t=0.1, dur=0.5)
    g.ripple(TOKYO, t=0.4)
    return g


def test_determinism_same_pixels_and_same_key():
    _dry()
    land = [_box(100, 150, 20, 50), _box(-10, 40, 35, 60)]
    o1 = _scene(G.globe(size=240, land=land, view=(40, 80))).build()
    o2 = _scene(G.globe(size=240, land=land, view=(40, 80))).build()
    assert o1.source == o2.source
    for i in (0, 8, 15):
        assert np.array_equal(fk.draw_frame(o1, i), fk.draw_frame(o2, i))
    # 絵に効かない値（name）は鍵に入らず、効く値は入る
    g3 = G.globe(size=240, land=land, view=(40, 80))
    g3.points([TOKYO, LONDON, SF], t=0.0, name="x")
    g3.arc(LONDON, TOKYO, t=0.1, dur=0.5)
    g3.ripple(TOKYO, t=0.4)
    assert g3.build().source == o1.source
    o4 = _scene(G.globe(size=240, land=land, view=(40, 81))).build()
    assert o4.source != o1.source
    # 使わない色を変えても鍵は同じ（同一出力なら同一鍵）
    o5 = _scene(G.globe(size=240, land=land, view=(40, 80), colors={"panel": "#333333"})).build()
    assert o5.source == o1.source
    # plate の turn は中心の経度しか動かさない: 緯度だけ違う turn は同じ絵・同じ鍵
    p1 = G.globe(projection="plate", size=720).turn(0, (10, 50)).build()
    p2 = G.globe(projection="plate", size=720).turn(0, (40, 50)).build()
    assert p1.source == p2.source
    assert np.array_equal(fk.draw_frame(p1, 20), fk.draw_frame(p2, 20))
    p3 = G.globe(projection="plate", size=720).turn(0, (10, 51)).build()
    assert p3.source != p1.source
    # ortho の turn は緯度も効く
    q1 = G.globe(size=240).turn(0, (10, 50)).build()
    q2 = G.globe(size=240).turn(0, (40, 50)).build()
    assert q1.source != q2.source


# --- エラー ----------------------------------------------------------------------------

def test_range_errors():
    for kw, msg in (
            ({"step": 0.5}, "step"), ({"step": 5.5}, "step"), ({"size": 5000}, "size"),
            ({"size": 32}, "size"), ({"dot": 1.9}, "dot"), ({"view": (91, 0)}, "緯度"),
            ({"projection": "mercator"}, "projection"),
            ({"projection": "plate", "size": (800, 300)}, "2:1"),
            ({"projection": "plate", "radius": 100}, "radius"),
            ({"size": (400, 300)}, "正方形"), ({"land": 5}, "land"),
            ({"land": [[(0, 0), (1, 1)]]}, "3つ以上"), ({"colors": {"nope": "#ffffff"}}, "nope"),
            ({"graticule": 2}, "graticule"), ({"limb": 1.5}, "limb")):
        with pytest.raises(ValueError, match=msg):
            G.globe(**kw)
    g = G.globe()
    with pytest.raises(ValueError, match="緯度"):
        g.points([(91, 0)])
    with pytest.raises(ValueError, match="緯度"):
        g.arc((0, 0), (-90.5, 0), t=0)
    with pytest.raises(ValueError, match="真裏"):
        g.arc((10, 20), (-10, -160), t=0)
    with pytest.raises(ValueError, match="radius"):
        g.points([TOKYO], radius=1.5)
    with pytest.raises(ValueError, match="件数"):
        g.points([TOKYO, SF], appear=("staged", [(0, 1)]))
    with pytest.raises(ValueError, match="増える順"):
        g.points([TOKYO, SF], appear=("staged", [(0.5, 1), (0.2, 2)]))
    with pytest.raises(ValueError, match="appear"):
        g.points([TOKYO], appear="later")
    with pytest.raises(ValueError, match="easing"):
        g.turn(0, TOKYO, easing="bouncy")
    with pytest.raises(ValueError, match="side"):
        g.label(TOKYO, "東京", side="up")
    with pytest.raises(ValueError, match="1行"):
        g.label(TOKYO, "東\n京")
    with pytest.raises(ValueError, match="t1"):
        g.spin(2, 1, 1)
    g.turn(1.0, TOKYO, dur=1.0)
    with pytest.raises(ValueError, match="重なって"):
        g.turn(1.5, SF)
    with pytest.warns(UserWarning, match="触れて"):
        dense = G.globe(step=0.6)              # 地球の点 約 11.5 万（900px では点が触れる）
    with pytest.raises(ValueError, match="多すぎ"):
        dense.points([(0.0, float(k % 360) - 180) for k in range(90000)])


def test_canvas_limit_warns_instead_of_silently_clipping():
    """余白がキャンバスの上限（4096px）で頭打ちになると、弧と大気が切れることを警告する"""
    _dry()
    g = G.globe(size=4096, atmosphere=1.0)
    g.arc((0, -60), (0, 80), t=0, height=1.0, width=24)
    with pytest.warns(UserWarning, match="4096px"):
        obj = g.build(duration=0.3)
    assert obj.figure.size == (4096, 4096)
    g = G.globe(size=2000, atmosphere=0.3)       # 余白が収まるときは警告しない
    g.arc((0, -60), (0, 80), t=0, height=0.3)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        obj = g.build(duration=0.3)
    assert obj.figure.size[0] > 2000


def test_does_not_depend_on_framekit_internals():
    """framekit の内部関数（_ で始まる名前）を使わない（framekit が書き換わっても弧が壊れない）"""
    with open(G.__file__, encoding="utf-8") as f:
        src = f.read()
    assert not re.findall(r"\bfk\._\w+", src)
    assert not re.findall(r"framekit\._\w+", src)


# --- 札 --------------------------------------------------------------------------------

def test_label_overlap_warns_and_hides_on_back():
    try:
        _resolve_font(None)
    except FileNotFoundError:
        pytest.skip("日本語フォントが無い環境")
    _dry()
    g = G.globe(size=600, view=(35, 139))
    g.label(TOKYO, "東京 13:09", t=0.0)
    g.label((35.0, 139.5), "横浜", t=0.0)
    with pytest.warns(UserWarning, match="重なって"):
        obj = g.build(duration=0.5)
    assert obj._text_image is not None and obj._text_image["border"] >= 2
    img = fk.draw_frame(obj, 14)
    assert (img[..., 3] > 200).sum() > 100
    # 裏へ回ると消える
    g2 = G.globe(size=600, view=(35, 139), graticule=False)
    g2.label(TOKYO, "東京", t=0.0)
    g2.turn(0.2, (-35, -41), dur=0.5)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")        # 縁へ寄る途中のはみ出しの警告は見ない
        obj2 = g2.build(duration=1.0)

    def text_px(img):                          # 白い字の画素（縁の線は暗い灰）
        return int(((img[..., 3] > 200) & (img[..., 0] > 200)).sum())
    # 回り始める前（0.2 秒より前）は、回さない地球と同じだけ字が見える。白い字の画素の数は
    # 既定のフォントの太さで変わる（游ゴシックの Regular はメイリオの 1/8）ので、数の下限では
    # なく回さない図と比べる
    g3 = G.globe(size=600, view=(35, 139), graticule=False)
    g3.label(TOKYO, "東京", t=0.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        obj3 = g3.build(duration=1.0)
    shown = text_px(fk.draw_frame(obj3, 5))
    assert shown > 0 and text_px(fk.draw_frame(obj2, 5)) == shown
    assert text_px(fk.draw_frame(obj2, 29)) == 0


def test_label_missing_glyph_message_points_to_globe_font():
    """札に豆腐（フォントに無い字）があると、globe で直せる案内（globe(font=...)）で止まる"""
    try:
        _resolve_font(None)
    except FileNotFoundError:
        pytest.skip("日本語フォントが無い環境")
    _dry()
    g = G.globe(size=400)
    g.label((0, 0), "͸東京")                # U+0378 は未割り当て（どのフォントにも無い）
    with pytest.raises(ValueError) as ei:
        g.build(duration=0.2)
    msg = str(ei.value)
    assert "U+0378" in msg and "globe(font=" in msg and "豆腐" in msg
    assert "missing=" not in msg and "{'font'" not in msg     # globe.label で使えない案内は出さない
    # 豆腐以外の誤り（範囲外の大きさなど）はそのまま
    g2 = G.globe(size=400, label_size=8)
    g2.label((0, 0), "東京")
    g2.build(duration=0.2)


def test_label_near_limb_widens_canvas_instead_of_clipping():
    try:
        _resolve_font(None)
    except FileNotFoundError:
        pytest.skip("日本語フォントが無い環境")
    _dry()
    g = G.globe(size=400, view=(0, 0), graticule=False)
    g.label((0, 80), "東の端の札", t=0.0)       # 右の縁の近く。右へ出す札は箱からはみ出す
    with warnings.catch_warnings():
        warnings.simplefilter("error")         # はみ出しの警告は出ない（余白で受ける）
        obj = g.build(duration=0.4)
    mx, my = obj.figure.margin
    W, H = obj.figure.size
    assert mx > 20 and (W, H) == (400 + 2 * mx, 400 + 2 * my)
    assert obj.figure.center == (200.0 + mx, 200.0 + my)
    img = fk.draw_frame(obj, 11)
    text = (img[..., 3] > 200) & (img[..., 0] > 200)
    assert text.any()
    cols = np.flatnonzero(text.any(axis=0))
    assert cols.max() < W - 1                  # 札の右端がキャンバスの中に収まる
    assert cols.max() > 200 + mx + 184         # 地球の箱の外（余白）まで札が描かれている


# --- 実レンダ ----------------------------------------------------------------------------

def test_real_render_frames_match_draw_frame(tmp_path):
    if not _HAS_FFMPEG:
        pytest.skip("ffmpeg / ffprobe が無い環境")
    p = sv.Project()
    p.configure(width=320, height=180, fps=30)
    g = G.globe(size=160, land=[_box(100, 150, 20, 50)], view=(30, 125))
    g.points([TOKYO], t=0.0)
    g.arc((20, 100), TOKYO, t=0.0, dur=0.3)
    obj = g.build(duration=0.4)                # Project が dry_run でないので実際に作る
    assert os.path.getsize(obj.source) > 0
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", obj.source,
         "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        check=True, capture_output=True, timeout=120).stdout
    w, h = obj.figure.size                     # 既定の大気（0.25）のぶん 160 より広い
    frames = np.frombuffer(raw, np.uint8).reshape(-1, h + h % 2, w + w % 2, 4)
    assert len(frames) == 12
    for i in (0, 5, 11):
        assert np.array_equal(frames[i], fk.draw_frame(obj, i)), i
    # 最後のコマ: 東京の点（白）と弧（赤）が描かれている
    x, y = obj.figure.xy(TOKYO, 0.4)
    assert frames[11][int(y), int(x), 3] > 200
    assert _red(frames[11]).sum() > 20
    del p


# --- 金型 -------------------------------------------------------------------------------

_GOLD_LAND = [
    [(-10, 35), (40, 35), (60, 55), (140, 70), (150, 45), (120, 20), (100, 5),
     (75, 10), (50, 25), (30, 30), (0, 45)],
    [(-20, 30), (35, 30), (50, 10), (40, -35), (15, -35), (-15, 5)],
    [(115, -15), (150, -12), (153, -38), (115, -35)],
]


def test_golden_land_points(request):
    _dry()
    g = G.globe(size=320, land=_GOLD_LAND, view=(25, 70), step=2.0, atmosphere=0.2)
    g.points([(35.7, 139.7), (51.5, -0.1), (1.35, 103.8), (19.1, 72.9)], t=0.0, color="fg")
    g.points([(48.9, 2.4), (39.9, 116.4)], t=0.0, color="muted", radius=3)
    assert_golden(request, "globe", "land_points", fk.draw_frame(g.build(duration=0.4), 11),
                  ver=G._GLOBE_VER)


def test_golden_arcs_ripple_back(request):
    _dry()
    g = G.globe(size=320, view=(30, 100), land=False, back=True)
    g.points([TOKYO, LONDON, SYDNEY], t=0.0)
    g.arc(LONDON, TOKYO, t=0.0, dur=0.6)
    g.arc(TOKYO, SYDNEY, t=0.1, dur=0.8, trail=0.6)
    g.ripple(TOKYO, t=0.3, dur=0.8)
    assert_golden(request, "globe", "arcs_ripple", fk.draw_frame(g.build(), 18),
                  ver=G._GLOBE_VER)


def test_golden_small_globe(request):
    """小さい地球（240px）: step=None で点が粗くなり、陸が面につぶれない"""
    _dry()
    g = G.globe(size=240, land=_GOLD_LAND, view=(25, 70))
    g.points([(35.7, 139.7), (19.1, 72.9), (1.35, 103.8)], t=0.0, radius=3)
    g.arc((19.1, 72.9), (35.7, 139.7), t=0.0, dur=0.5, width=2)
    assert_golden(request, "globe", "small_globe", fk.draw_frame(g.build(duration=0.6), 17),
                  ver=G._GLOBE_VER)


def test_golden_plate_night(request):
    _dry()
    g = G.globe(projection="plate", size=(320, 160), land=_GOLD_LAND, view=(0, 20), dot=2.0)
    g.night(datetime(2024, 7, 19, 4, 9, tzinfo=timezone.utc))
    g.points([TOKYO, SF])
    g.arc(TOKYO, SF, t=0.0, dur=0.6)
    assert_golden(request, "globe", "plate_night", fk.draw_frame(g.build(), 14),
                  ver=G._GLOBE_VER)


def test_golden_earth_default(request):
    """既定の陸（同梱の地球）・既定の色: 拠点の点が堀と光の輪で陸の点から浮く"""
    _dry()
    g = G.globe(size=320, view=(35, 10))
    g.points([LONDON, (48.9, 2.4), (52.5, 13.4), (41.9, 12.5), (30.0, 31.2), (55.8, 37.6),
              (40.7, -74.0), (6.5, 3.4)], t=0.0)
    g.arc(LONDON, (40.7, -74.0), t=0.0, dur=0.5)
    g.arc(LONDON, (30.0, 31.2), t=0.1, dur=0.5)
    assert_golden(request, "globe", "earth_default", fk.draw_frame(g.build(), 20),
                  ver=G._GLOBE_VER)


def test_golden_plate_auto(request):
    """plate の既定（同梱の地球・view='auto'）: 太平洋を渡る弧が端で切れず1本につながる"""
    _dry()
    g = G.globe(projection="plate", size=(320, 160))
    g.points([TOKYO, SF, LONDON, SYDNEY])
    g.arc(SF, TOKYO, t=0.0, dur=0.6)
    g.arc(TOKYO, SYDNEY, t=0.1, dur=0.6)
    assert_golden(request, "globe", "plate_auto", fk.draw_frame(g.build(), 20),
                  ver=G._GLOBE_VER)


# --- 同梱の地球（land=True）・陸なし（land=False）----------------------------------------

def test_land_default_is_bundled_earth_and_false_draws_none():
    _dry()
    assert os.path.isfile(G._EARTH_LAND)
    g = G.globe()
    assert g._land == ("png", G._EARTH_LAND, "png")
    assert G.globe(land=True)._land == g._land
    assert G.globe(land=False)._land == (None, None, None)
    with pytest.raises(ValueError, match="land=False"):
        G.globe(land=None)                     # 以前の「陸なし」と取り違えない
    with pytest.raises(ValueError, match="True"):
        G.globe(land=1)
    # 陸なしは 15 度の経緯線、同梱の地球は経緯線なし（graticule=None の既定）
    assert G.globe(land=False)._p["graticule"] == 15.0 and g._p["graticule"] == 0.0
    # 同梱の地球の鍵は、同じ PNG のパスを渡したのと同じ（鍵は中身。パスは入らない）
    a = G.globe(size=240).build(duration=0.1).source
    b = G.globe(size=240, land=G._EARTH_LAND).build(duration=0.1).source
    assert a == b
    assert G.globe(size=240, land=False).build(duration=0.1).source != a
    # 陸の点の数（size=900 の既定の step で約 8 千点）
    r = _renderer(G.globe(size=900))
    r._prepare()
    assert 7000 < len(r.layers[-1]["v"]) < 10500, len(r.layers[-1]["v"])


def test_bundled_land_png_text_and_notice():
    """同梱の PNG: 正距円筒 1440×720 の 1bit・出典と利用条件の tEXt。NOTICE.md と食い違わない"""
    mlm = _load_make_land_mask()
    with Image.open(G._EARTH_LAND) as im:
        im.load()
        assert im.mode == "1" and im.size == (1440, 720)
        assert dict(im.text) == mlm.png_text()
        arr = np.asarray(im.convert("L")) > 127
    assert 0.30 < arr.mean() < 0.37                       # 正距円筒で陸 33.7%（南極が広い）
    assert arr[-1].all() and not arr[0].any()             # 南極は下端まで・北極点は海
    lat, lon = TOKYO                                      # 東京は陸、太平洋の真ん中は海
    assert arr[int((90 - lat) * 4), int((lon + 180) * 4)]
    assert not arr[int((90 - 0) * 4), int((-150 + 180) * 4)]
    assert os.path.getsize(G._EARTH_LAND) < 20000
    notice = os.path.join(os.path.dirname(G._EARTH_LAND), "NOTICE.md")
    with open(notice, encoding="utf-8") as f:
        text = f.read()
    assert "Made with Natural Earth." in text and "public domain" in text
    # 「唯一の例外」は素材・データの中での話。ライブラリとして同梱の KaTeX は別扱いと書いてある
    assert "唯一の例外" in text and "KaTeX" in text
    assert mlm.SHA256 in text                             # 元データ（zip）の SHA-256
    with open(G._EARTH_LAND, "rb") as f:
        assert hashlib.sha256(f.read()).hexdigest() in text   # PNG を作り直したら NOTICE も直す
    assert os.path.normcase(mlm.BUNDLED) == os.path.normcase(G._EARTH_LAND)
    # wheel に入るよう package-data に載っている（CI の wheel の検証が中身も見る）
    with open(os.path.join(_ROOT, "pyproject.toml"), encoding="utf-8") as f:
        toml = f.read()
    assert '"data/*.png"' in toml and '"data/NOTICE.md"' in toml


# --- 拠点の点（堀と光の輪）--------------------------------------------------------------

def test_points_knock_out_land_dots_and_glow():
    """拠点の点のまわりの陸の点は抜かれ（堀）、点の色の光の輪だけが残る（陸に埋もれない）"""
    _dry()
    land = [_box(-179, 179, -80, 80, n=60)]          # 一面の陸
    g = G.globe(projection="plate", size=720, land=land, view=(0, 0))
    g.points([(0.0, 0.0)], color="accent", radius=5, dur=0.1)
    obj = g.build(duration=0.5)
    img = fk.draw_frame(obj, 10)
    x, y = obj.figure.xy((0.0, 0.0), 0.3)
    yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    d = np.hypot(xx + 0.5 - x, yy + 0.5 - y)
    r, a = img[..., 0].astype(int), img[..., 3].astype(int)
    gray = (a > 20) & (img[..., 1].astype(int) > 100)    # 陸の点（灰）。点と光の輪は赤
    moat = (d > 5 + 1) & (d < 5 + G._POINT_MOAT * 2.2 - 0.5)
    assert moat.sum() > 20 and not gray[moat].any()      # 堀には陸の点が無い
    assert (a[moat] > 40).all() and (r[moat] > 200).all()   # 光の輪（赤）が敷かれている
    far = (d > 5 * G._POINT_GLOW_R + 3) & (d < 60)
    assert gray[far].sum() > 50                           # 光の輪の外は陸の点のまま
    assert img[int(y), int(x), 3] == 255                  # 芯は不透明
    # 点く前は堀も無い（陸の点がそのまま）
    g2 = G.globe(projection="plate", size=720, land=land, view=(0, 0))
    g2.points([(0.0, 0.0)], t=0.4, color="accent", radius=5, dur=0.1)
    img2 = fk.draw_frame(g2.build(duration=0.5), 3)
    gray2 = (img2[..., 3] > 20) & (img2[..., 1] > 100)
    assert gray2[(d < 5 + G._POINT_MOAT * 2.2)].any()


def test_knockout_and_glow_only_touch_their_discs():
    dst = fk.canvas(80, 60)
    dst[:] = 0.5                                         # 事前乗算: 灰色・不透明度 0.5
    G._knockout(np, dst, np.array([20.0, 26.0]), np.array([30.0, 30.0]),
                np.array([8.0, 8.0]), np.array([1.0, 0.5]))
    assert np.allclose(dst[30, 20], 0.0)                 # 中心は抜ける
    assert np.allclose(dst[30, 24], 0.0)                 # 重なりは max（1.0 の方）
    assert np.allclose(dst[30, 31], 0.25)                # 0.5 だけの所は半分
    assert np.allclose(dst[5, 70], 0.5)                  # 円の外は触らない
    before = dst.copy()
    G._knockout(np, dst, np.array([-50.0]), np.array([-50.0]), np.array([5.0]), np.array([1.0]))
    assert np.array_equal(dst, before)                   # キャンバスの外の円は何もしない
    dst2 = fk.canvas(80, 60)
    G._glow(np, dst2, np.array([40.0]), np.array([30.0]), np.array([4.0]), np.array([12.0]),
            np.array([0.5]), (255, 0, 0, 255))
    a = dst2[..., 3]
    assert a[30, 40] == pytest.approx(0.5) and a[30, 43] == pytest.approx(0.5)   # 縁までは一定
    assert a[30, 48] < a[30, 46] < 0.5                   # 外へ (1 − u)² で薄れる
    assert a[30, 53] == 0 and a[5, 5] == 0                # r_out の外は触らない
    assert np.allclose(dst2[30, 40, :3], [0.5, 0.0, 0.0])   # 事前乗算の赤


def _disc_reference(dst, xs, ys, cov_fn):
    """堀・光の輪の被覆率の素朴な参照: 円ごとにキャンバス全面の被覆率を出して max を取る"""
    H, W = dst.shape[:2]
    yy, xx = np.mgrid[0:H, 0:W]
    cov = np.zeros((H, W), np.float32)
    for k in range(len(xs)):
        d = np.hypot(xx + 0.5 - xs[k], yy + 0.5 - ys[k]).astype(np.float32)
        c = cov_fn(k, d)
        cov = np.maximum(cov, np.where(c > 1e-4, c, 0).astype(np.float32))
    return cov


def test_knockout_and_glow_chunks_give_same_pixels(monkeypatch):
    """堀と光の輪は円を _WINDOW_CHUNK ずつに分けて計算しても、分けないときと同じ画素
    （max は分け方によらない）。素朴な参照（円ごとに全面を計算して max）とも一致する"""
    rng = np.random.default_rng(5)
    n = 60
    xs = rng.uniform(-15, 175, n)                       # キャンバスの端をまたぐ円も含める
    ys = rng.uniform(-15, 135, n)
    rs = rng.choice([2.0, 4.0, 6.5, 11.0], n)           # 半径がばらばら（窓の一辺が違う）
    amt = rng.uniform(0.1, 1.0, n)
    base = rng.uniform(0, 1, (120, 160, 4)).astype(np.float32)
    base[..., :3] *= base[..., 3:4]                     # 事前乗算

    def run():
        a, b = base.copy(), fk.canvas(160, 120)
        G._knockout(np, a, xs, ys, rs, amt)
        G._glow(np, b, xs, ys, rs, rs * G._POINT_GLOW_R, amt * G._POINT_GLOW_A, (255, 40, 30, 255))
        return a, b

    one = run()                                         # 全部が1回に収まる経路
    monkeypatch.setattr(G, "_WINDOW_CHUNK", 64)
    many = run()                                        # 円を少しずつに分けて層へ積む経路
    assert np.array_equal(one[0], many[0]) and np.array_equal(one[1], many[1])
    k_cov = _disc_reference(base, xs, ys, lambda k, d: np.clip(
        np.float32(rs[k] + 0.5) - d, 0, 1) * np.float32(amt[k]))
    assert np.allclose(one[0], base * (1.0 - k_cov)[..., None], atol=1e-6)
    r_out = rs * G._POINT_GLOW_R
    g_cov = _disc_reference(base, xs, ys, lambda k, d: (1.0 - np.clip(
        (d - np.float32(rs[k])) / np.float32(r_out[k] - rs[k]), 0, 1)) ** 2
        * np.float32(amt[k] * G._POINT_GLOW_A))
    assert np.allclose(one[1][..., 3], g_cov, atol=1e-6)


def test_knockout_and_glow_memory_does_not_grow_with_point_count():
    """堀と光の輪のメモリは点の数に比例しない（修正前は全部の円の窓を一度に作り、
    2 万点で _knockout が約 280MB・_glow が約 890MB。globe は 20 万点まで受ける）"""
    rng = np.random.default_rng(0)
    G._knockout(np, fk.canvas(64, 64), np.array([30.0]), np.array([30.0]), np.array([6.0]),
                np.array([1.0]))                        # 一度描いて import を済ませる
    n = 20000
    xs, ys = rng.uniform(0, 1400, n), rng.uniform(0, 700, n)
    r, a = np.full(n, 4.0), np.ones(n)
    peaks = {}
    for name in ("knockout", "glow"):
        dst = fk.canvas(1400, 700)
        dst[:] = 0.3
        tracemalloc.start()
        if name == "knockout":
            G._knockout(np, dst, xs, ys, r + G._POINT_MOAT * 2.2, a)
        else:
            G._glow(np, dst, xs, ys, r, r * G._POINT_GLOW_R, a * G._POINT_GLOW_A,
                    (255, 255, 255, 255))
        peaks[name] = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        assert (dst != 0.3).any()                       # 描いてはいる
    # 窓1回ぶん（_WINDOW_CHUNK 要素）と、キャンバス大の層・触れた画素の列だけ（実測 55〜60MB）
    assert peaks["knockout"] < 120e6 and peaks["glow"] < 120e6, peaks


def test_points_halo_false_draws_core_only():
    """points(halo=False) は堀も光の輪も付けず芯だけ（何千の点で陸を消さない）。
    既定の halo=True は鍵に入らない（halo を足す前と同じ鍵）"""
    _dry()
    land = [_box(-179, 179, -80, 80, n=60)]          # 一面の陸

    def frame(**kw):
        g = G.globe(projection="plate", size=720, land=land, view=(0, 0))
        g.points([(0.0, 0.0)], color="accent", radius=5, dur=0.1, **kw)
        obj = g.build(duration=0.5)
        return g, obj, fk.draw_frame(obj, 10)

    g1, obj1, img1 = frame()
    g0, obj0, img0 = frame(halo=False)
    x, y = obj0.figure.xy((0.0, 0.0), 0.3)
    yy, xx = np.mgrid[0:img0.shape[0], 0:img0.shape[1]]
    d = np.hypot(xx + 0.5 - x, yy + 0.5 - y)
    gray = (img0[..., 3] > 20) & (img0[..., 1] > 100)   # 陸の点（灰）
    ring = (d > 5 + 1.5) & (d < 5 * G._POINT_GLOW_R)
    assert gray[ring].sum() > 5                          # 堀が無い（陸の点が点のすぐ外まで残る）
    r0, g0_ = img0[..., 0].astype(int), img0[..., 1].astype(int)
    red = (img0[..., 3] > 20) & (r0 > g0_ + 60)
    assert not red[d > 5 + 1.5].any()                    # 光の輪が無い（赤は芯の中だけ）
    r1, g1_ = img1[..., 0].astype(int), img1[..., 1].astype(int)
    assert ((img1[..., 3] > 20) & (r1 > g1_ + 60))[d > 5 + 3].any()   # 既定は光の輪あり
    assert img0[int(y), int(x), 3] == 255                # 芯は描く
    gray1 = (img1[..., 3] > 20) & (img1[..., 1] > 100)
    assert not gray1[(d > 5 + 1) & (d < 5 + G._POINT_MOAT * 2.2 - 0.5)].any()   # 既定は堀あり
    # 鍵: halo=False は鍵が変わる。既定（True）は鍵の出来事に halo を入れない
    assert obj0.source != obj1.source
    assert "halo" not in g1._params()["events"][0]
    assert g0._params()["events"][0]["halo"] is False
    with pytest.raises(ValueError, match="halo"):
        G.globe().points([(0.0, 0.0)], halo="no")


def test_docstrings_match_constants_and_params_are_not_nested():
    """モジュールの docstring の堀・光の輪の数字は定数と同じ。globe() と points() の引数の
    説明は同じ深さで並ぶ（describe --format md で別の引数の説明の中に入れ子に見えない）"""
    doc = G.__doc__
    for name in ("_POINT_MOAT", "_POINT_GLOW_R", "_POINT_GLOW_A"):
        assert f"{name} = {getattr(G, name):g}" in doc, name
    for fn in (G.globe, G.Globe.points):
        params = set(inspect.signature(fn).parameters) - {"self"}
        nested = [line for line in inspect.getdoc(fn).splitlines()
                  if line[:1] == " " and re.match(r"\s+(\w+):", line)
                  and re.match(r"\s+(\w+):", line).group(1) in params]
        assert not nested, nested


# --- plate の view="auto" --------------------------------------------------------------

def _crosses(ev, center):
    """弧の経度の範囲が、中心 center の地図の端（center ± 180）をまたぐか"""
    lo, hi = G._arc_lon_span(ev["src"], ev["dst"])
    edge = center + 180.0
    return hi - lo >= 360 or 0 < (edge - lo) % 360 < hi - lo


def test_plate_auto_center_keeps_arcs_whole():
    _dry()
    g = G.globe(projection="plate", size=720)            # 既定は view="auto"
    g.points([TOKYO, SF])
    g.arc(SF, TOKYO, t=0.0, dur=0.5, head=False, trail=0)
    obj = g.build(duration=0.6)
    lat, lon = obj.figure.view
    assert lat == 0.0 and lon % G._AUTO_STEP == 0
    assert not _crosses(g._events[1], lon)               # 太平洋を渡る弧は端をまたがない
    assert 120 < lon <= 180 or -180 <= lon < -160         # 太平洋が地図の真ん中寄り
    red = _red(fk.draw_frame(obj, 17))
    cols = np.flatnonzero(red.any(axis=0))
    assert len(cols) and np.all(np.diff(cols) <= 2)       # 弧は1本につながる（左右に分かれない）
    assert cols.min() > 20 and cols.max() < 700
    # 選んだ経度を手で書いたのと同じ鍵・同じ絵（同一出力なら同一鍵）
    g2 = G.globe(projection="plate", size=720, view=(0, lon))
    g2.points([TOKYO, SF])
    g2.arc(SF, TOKYO, t=0.0, dur=0.5, head=False, trail=0)
    obj2 = g2.build(duration=0.6)
    assert obj2.source == obj.source
    assert np.array_equal(fk.draw_frame(obj2, 17), fk.draw_frame(obj, 17))
    # 何も無ければ 0（見慣れた向き）。陸なしでも同じ
    assert G.globe(projection="plate", size=720).build(duration=0.1).figure.view == (0.0, 0.0)
    assert G.globe(projection="plate", size=720, land=False).build(
        duration=0.1).figure.view == (0.0, 0.0)
    # 端の近くの点は端から離す（地図の端で半分に切れない）
    g3 = G.globe(projection="plate", size=720)
    g3.points([(0.0, 179.0)])
    c3 = g3.build(duration=0.1).figure.view[1]
    assert abs(G._wrap_lon(179.0 - c3)) <= 180 - G._AUTO_CLEAR
    # 大西洋を渡る弧だけなら 0 のまま（端は太平洋）
    g4 = G.globe(projection="plate", size=720)
    g4.arc(LONDON, (40.7, -74.0), t=0.0)
    assert g4.build().figure.view == (0.0, 0.0)
    # 弧がいくつあっても、またがずに置ける端があればまたがない
    g5 = G.globe(projection="plate", size=720)
    for k in range(-150, 180, 60):
        g5.arc((0.0, float(k)), (0.0, float(k) + 50), t=0.0)
    c5 = g5.build().figure.view[1]
    assert not any(_crosses(e, c5) for e in g5._events)
    # 選ぶのは最初の向きだけ。出来事を足して build し直すと選び直す
    assert g._view_memo[0] == 2
    g.arc(LONDON, (40.7, -74.0), t=0.0)
    g.build()
    assert g._view_memo[0] == 3
    with pytest.raises(ValueError, match="plate だけ"):
        G.globe(view="auto")
    with pytest.raises(ValueError, match="auto"):
        G.globe(projection="plate", view="center")


def test_plate_auto_avoids_cutting_continents():
    """端の経線は大陸を切らない（SF → 東京の弧なら、端は大西洋。ヨーロッパやアフリカを切らない）"""
    _dry()
    g = G.globe(projection="plate", size=720)
    g.arc(SF, TOKYO, t=0.0)
    lon = g.build().figure.view[1]
    prof = G._land_profile(g._land)
    k = int(round((G._wrap_lon(lon + 180.0) + 180.0) / G._AUTO_STEP)) % len(prof)
    assert 0.5 * (prof[k - 1] + prof[k]) < 0.12
    assert -60 < G._wrap_lon(lon + 180.0) < 0              # 端は大西洋
    # 陸なしでは陸を見ない（経度は違ってよいが、弧はまたがない）
    g2 = G.globe(projection="plate", size=720, land=False)
    g2.arc(SF, TOKYO, t=0.0)
    lon2 = g2.build().figure.view[1]
    assert not _crosses(g2._events[0], lon2)


# --- scripts/make_land_mask.py ---------------------------------------------------------

def _load_make_land_mask():
    path = os.path.join(_ROOT, "scripts", "make_land_mask.py")
    spec = importlib.util.spec_from_file_location("make_land_mask_for_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _shp_polygon_record(num, rings):
    pts = [p for ring in rings for p in ring]
    parts, k = [], 0
    for ring in rings:
        parts.append(k)
        k += len(ring)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    body = struct.pack("<i4d2i", 5, min(xs), min(ys), max(xs), max(ys), len(parts), len(pts))
    body += struct.pack(f"<{len(parts)}i", *parts)
    body += b"".join(struct.pack("<2d", *p) for p in pts)
    return struct.pack(">2i", num, len(body) // 2) + body


def _write_shp(path, records):
    body = b"".join(_shp_polygon_record(i + 1, r) for i, r in enumerate(records))
    header = struct.pack(">7i", 9994, 0, 0, 0, 0, 0, (100 + len(body)) // 2)
    header += struct.pack("<2i4d4d", 1000, 5, -180, -90, 180, 90, 0, 0, 0, 0)
    assert len(header) == 100
    with open(path, "wb") as f:
        f.write(header + body)


def test_make_land_mask_reads_small_shp_and_writes_png(tmp_path, monkeypatch):
    mlm = _load_make_land_mask()
    # 外側は時計回り（経度・緯度の平面で y が上）、穴は反時計回り
    outer = [(-90, 45), (0, 45), (0, -45), (-90, -45), (-90, 45)]
    hole = [(-60, 15), (-60, -15), (-30, -15), (-30, 15), (-60, 15)]
    tri = [(90, 60), (150, 0), (90, 0), (90, 60)]
    shp = tmp_path / "t.shp"
    _write_shp(shp, [[outer, hole], [tri]])
    records = mlm.read_shp_polygons(shp.read_bytes())
    assert len(records) == 2 and len(records[0]) == 2 and records[1][0][1] == (150.0, 0.0)
    img = mlm.rasterize(records, 72, 36)
    assert img.mode == "1" and img.size == (72, 36)
    px = lambda lon, lat: img.getpixel((int((lon + 180) / 5), int((90 - lat) / 5)))  # noqa: E731
    assert px(-80, 30) and px(-10, -30)        # 外側の内
    assert not px(-45, 0)                      # 穴
    assert px(100, 10) and not px(140, 50)     # 三角の内と外
    assert not px(170, -70)
    out = tmp_path / "geo" / "land.png"
    mlm.write_png(img, str(out))
    with Image.open(out) as im:
        assert im.mode == "1" and im.size == (72, 36)
        assert "Natural Earth" in im.text["Source"] and "Public domain" in im.text["License"]
        assert im.text["SourceSHA256"] == mlm.SHA256
    assert [p.name for p in out.parent.iterdir()] == ["land.png"]   # 一時ファイルが残らない
    # globe がこの PNG を陸として読む
    count = _land_count(str(out), step=2.0)
    assert count > 0
    with pytest.raises(ValueError, match="SHA-256"):
        mlm.check_sha256(b"not the zip")
    with pytest.raises(ValueError, match=".shp"):
        mlm.read_shp_polygons(b"\x00" * 120)
    # --check: 同じ手順で作ったマスクなら一致、1画素でも違えば理由を返す
    assert mlm.check_png(img, str(out)) is None
    other = img.copy()
    other.putpixel((0, 0), 255 - other.getpixel((0, 0)))
    assert "画素が 1 個違います" in mlm.check_png(other, str(out))
    assert "寸法" in mlm.check_png(mlm.rasterize(records, 36, 18), str(out))
    assert "ありません" in mlm.check_png(img, str(tmp_path / "none.png"))
    # main(): 出力先を省くと同梱の PNG。--check は書き出さずに比べるだけ（違えば終了コード 1）
    zpath = tmp_path / "ne.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.write(shp, mlm.SHP_NAME)
    with open(G._EARTH_LAND, "rb") as f:
        bundled = f.read()
    with pytest.raises(ValueError, match="SHA-256"):   # zip の照合は --check より前に止まる
        mlm.main(["--zip", str(zpath), "--check"])
    monkeypatch.setattr(mlm, "check_sha256", lambda data, sha256=None: None)
    with pytest.raises(SystemExit) as ei:
        mlm.main(["--zip", str(zpath), "--width", "72", "--check"])
    assert ei.value.code == 1                        # 小さな .shp では同梱の PNG と違う
    mine = tmp_path / "mine.png"
    mlm.main([str(mine), "--zip", str(zpath), "--width", "72"])
    assert mine.is_file()
    with open(G._EARTH_LAND, "rb") as f:
        assert f.read() == bundled                   # --check は同梱の PNG を書き換えない


def test_make_land_mask_refuses_other_width_into_bundled_png(tmp_path, capsys):
    """出力先を省いた（＝同梱の PNG）まま --width を変えると、ダウンロードの前に止まる
    （修正前は 2880×1440 で同梱の PNG を上書きし、NOTICE.md の SHA-256 とテストが合わなくなった）"""
    mlm = _load_make_land_mask()
    with open(G._EARTH_LAND, "rb") as f:
        bundled = f.read()
    missing = str(tmp_path / "no_such.zip")          # 止まるのは zip を開く前（開けば FileNotFoundError）
    for argv in (["--zip", missing, "--width", "2880"],
                 [G._EARTH_LAND, "--zip", missing, "--width", "72"]):
        with pytest.raises(SystemExit) as ei:
            mlm.main(argv)
        assert ei.value.code == 2, argv
        assert "出力先を書いてください" in capsys.readouterr().err
    with open(G._EARTH_LAND, "rb") as f:
        assert f.read() == bundled
    # 1440 幅なら同梱の PNG へ書ける（ここでは zip が無いので開く所で止まる）
    with pytest.raises(FileNotFoundError):
        mlm.main(["--zip", missing])
