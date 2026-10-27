# -*- coding: utf-8 -*-
"""annotate()（画像の矩形に時刻つきの囲み・下線・暗くする・寄り）のテスト。

固定すること:
  - 座標: 元の画像の px の矩形が、切り出しと枠への収め方どおりにキャンバスへ写る
    （figure.rect・figure.area）。縦横比の違う切り出しは枠の中央に置く
  - 囲み: rect の外側（pad px あけた所）にだけ描かれ、矩形の中の画素は変わらない。dur で現れる
  - 下線: 左端は動かず、右端が左から右へ伸びる（減速して止まる）
  - 暗く: 見えている範囲のうち穴の外だけが (1 − amount) 倍に暗くなり、穴の中と余白は変わらない。
    穴は次の dim の矩形へ移り、dim(None) で元の絵に戻る
  - 寄り: 寄り終えたコマは、最初からその切り出しで描いたコマとほぼ同じ。印は画像の座標に
    ついていき、矩形が見えている範囲の外へ出た印は描かない
  - 取り込み: 切り出しの全部を覆う範囲だけを持ち、倍率が小さいときは 2^-n へ縮める
  - 止まっている区間は前のコマを使い回す
  - 決定性（同じ指定なら同じ画素・同じ鍵）。画像の中身で鍵が変わり、パスでは変わらない。
    効かない値（frame の無い frame_width・dim / zoom を積む順）は鍵に入らない
  - 範囲外・形の違い（(x, y, w, h) との取り違え）・重なりの ValueError
  - 実レンダ（ffmpeg）の生成物のコマが draw_frame と画素で一致する
  - 金型 4 枚（tests/golden/annotate/。描き方を変えたら _ANNOTATE_VER を上げる）

見本の画像はテストの中で Pillow の図形だけで描く「文章ページ風」の絵（フォントに依らない）。
"""
import os
import re
import shutil
import subprocess

import pytest

np = pytest.importorskip("numpy", reason="numpy が無い環境")
pytest.importorskip("cv2", reason="numpy・opencv が無い環境")
pytest.importorskip("PIL", reason="Pillow が無い環境")
from PIL import Image, ImageDraw  # noqa: E402

import scriptvedit as sv  # noqa: E402
from scriptvedit import framekit as fk  # noqa: E402
from scriptvedit import fx_annotate as A  # noqa: E402
from scriptvedit.context import _exec_stack, activate, current_project  # noqa: E402

from framekit_golden import assert_golden  # noqa: E402

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


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


def _page(path="page.png", w=640, h=480, seed=0):
    """文章ページ風の画像: 紺の帯（見出し）と、灰色の「文字の行」の棒・枠（図形だけ）"""
    im = Image.new("RGB", (w, h), (248, 248, 244))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, w - 1, 39), fill=(36, 58, 108))
    d.rectangle((32, 60, 300, 84), fill=(30, 30, 30))                # 見出しの行
    rng = np.random.default_rng(seed)
    for i in range(9):
        y = 110 + i * 30
        x1 = 600 - int(rng.integers(0, 160))
        d.rectangle((32, y, x1, y + 14), fill=(110, 110, 116))       # 本文の行
    d.rectangle((32, 400, 360, 460), outline=(170, 170, 170), width=2)
    d.rectangle((48, 418, 220, 440), fill=(40, 110, 70))             # コードの行（印の赤と区別する）
    im.save(path)
    return os.path.abspath(path)


def _red(img):
    """赤い画素（印）の判定（RGBA の uint8）"""
    r, g, b, a = (img[..., k].astype(int) for k in range(4))
    return (a > 40) & (r > g + 80) & (r > b + 80)


def _count_frames(o):
    """build の draw が受ける最後のコマ番号 + 1（範囲外の ValueError から求める）"""
    lo, hi = 1, 1
    while True:
        try:
            fk.draw_frame(o, hi)
        except ValueError:
            break
        lo, hi = hi, hi * 2
    while hi - lo > 1:
        mid = (lo + hi) // 2
        try:
            fk.draw_frame(o, mid)
            lo = mid
        except ValueError:
            hi = mid
    return lo + 1


# --- 座標 ---------------------------------------------------------------------------------

def test_rect_and_area_map_source_pixels_to_canvas():
    _dry()
    src = _page()
    a = A.annotate(src, size=(320, 240), margin=10)            # 640x480 → 倍率 0.5
    o = a.build()
    assert o.figure.size == (340, 260)
    assert o.figure.area(0) == (10, 10, 330, 250)
    assert o.figure.rect((100, 100, 200, 140), 0) == (60, 60, 110, 80)
    # 縦横比の違う切り出し（正方形）は枠の中央に置く
    b = A.annotate(src, size=(320, 240), margin=10, crop=(0, 0, 480, 480)).build()
    assert b.figure.area(0) == (50, 10, 290, 250)
    assert b.figure.rect((0, 0, 480, 480), 0) == (50, 10, 290, 250)


def test_box_draws_only_outside_the_rect_and_fades_in():
    _dry()
    src = _page()
    a = A.annotate(src, size=(320, 240), margin=12)
    a.box((100, 100, 300, 150), t=0.2, width=4, pad=6, dur=0.2)
    o = a.build(duration=0.6)
    base = fk.draw_frame(o, 0)
    end = fk.draw_frame(o, 17)
    x0, y0, x1, y1 = o.figure.rect((100, 100, 300, 150), 0)     # (62, 62, 162, 87)
    diff = np.any(base != end, axis=2)
    ys, xs = np.nonzero(diff)
    e = 6 + 4                                                    # pad + width
    assert xs.min() >= x0 - e - 1 and xs.max() <= x1 + e
    assert ys.min() >= y0 - e - 1 and ys.max() <= y1 + e
    # 矩形の中（pad の内側）は1画素も変わらない
    assert not diff[int(y0) - 3:int(y1) + 3, int(x0) - 3:int(x1) + 3].any()
    assert _red(end).sum() > 200
    # dur の途中は薄い（不透明度が増えていく）
    mid = fk.draw_frame(o, 9)                                    # 0.3 秒 = 半分
    ring = _red(end)
    assert 0 < np.abs(mid[ring].astype(int) - base[ring].astype(int)).mean() \
        < np.abs(end[ring].astype(int) - base[ring].astype(int)).mean()


def test_underline_grows_from_left_and_decelerates():
    _dry(fps=30)
    src = _page()
    a = A.annotate(src, size=(640, 480), margin=12)
    r = (32, 110, 500, 124)
    a.underline(r, t=0.0, dur=1.0, width=4, gap=4)
    o = a.build()
    x0, _y0, x1, y1 = o.figure.rect(r, 0)
    rights, lefts = [], []
    for i in (3, 6, 15, 24, 30):
        red = _red(fk.draw_frame(o, i))
        ys, xs = np.nonzero(red)
        assert ys.min() >= y1 + 4 - 1 and ys.max() <= y1 + 4 + 4 + 1   # gap の下に幅 4
        rights.append(xs.max())
        lefts.append(xs.min())
    assert max(lefts) - min(lefts) <= 1                               # 左端は動かない
    assert all(b > c for b, c in zip(rights[1:], rights[:-1]))        # 右へ伸びる
    assert abs(rights[-1] - (x1 + 2)) <= 1                            # 引き終わり（丸い端）
    # 減速: 前半の 0.5 秒で全長の 8 割以上を引く（ease_out_cubic で 0.875）
    assert rights[2] - x0 > 0.8 * (x1 - x0)


def test_dim_darkens_outside_hole_only():
    _dry()
    src = _page()
    hole = (32, 110, 400, 124)
    a = A.annotate(src, size=(320, 240), margin=12)
    a.dim(hole, t=0.0, dur=0.1, amount=0.5, pad=4)
    o = a.build(duration=0.3)
    plain = A.annotate(src, size=(320, 240), margin=12).build(duration=0.3)
    d = fk.draw_frame(o, 8).astype(float)
    p = fk.draw_frame(plain, 8).astype(float)
    hx0, hy0, hx1, hy1 = o.figure.rect(hole, 0)
    inner = (slice(int(hy0 - 4) + 1, int(hy1 + 4) - 1), slice(int(hx0 - 4) + 1, int(hx1 + 4) - 1))
    assert np.array_equal(d[inner], p[inner])                         # 穴の中は変わらない
    # 穴の外（見えている範囲）は半分の明るさ
    out = (slice(150, 240), slice(20, 320))
    ratio = d[out][..., :3].sum() / p[out][..., :3].sum()
    assert abs(ratio - 0.5) < 0.01
    # 余白は透明のまま
    assert d[:12, :, 3].max() == 0 and d[:, :12, 3].max() == 0


def test_dim_moves_to_next_rect_and_none_restores():
    _dry()
    src = _page()
    a = A.annotate(src, size=(320, 240), margin=12)
    a.dim((32, 110, 400, 124), t=0.0, dur=0.2)
    a.dim((32, 170, 400, 184), t=0.4, dur=0.2)
    a.dim(None, t=0.8, dur=0.2)
    o = a.build()
    plain = A.annotate(src, size=(320, 240), margin=12).build(duration=1.0)
    r2 = o.figure.rect((32, 170, 400, 184), 0)
    r1 = o.figure.rect((32, 110, 400, 124), 0)
    f_first = fk.draw_frame(o, 9).astype(float)                      # 0.3 秒: 1つ目の穴
    f_second = fk.draw_frame(o, 21).astype(float)                    # 0.7 秒: 2つ目の穴
    p = fk.draw_frame(plain, 21).astype(float)
    row1 = int((r1[1] + r1[3]) / 2)
    row2 = int((r2[1] + r2[3]) / 2)
    cx = int((r1[0] + r1[2]) / 2)
    assert np.array_equal(f_first[row1, cx], p[row1, cx])
    assert f_first[row2, cx, :3].sum() < p[row2, cx, :3].sum()
    assert np.array_equal(f_second[row2, cx], p[row2, cx])
    assert f_second[row1, cx, :3].sum() < p[row1, cx, :3].sum()
    assert np.array_equal(fk.draw_frame(o, _count_frames(o) - 1), fk.draw_frame(plain, 29))


# --- 寄り ---------------------------------------------------------------------------------

def test_zoom_end_matches_static_crop_and_marks_follow():
    _dry()
    src = _page()
    c1 = (32, 100, 352, 340)                                       # 320x240 → 倍率 1.25
    a = A.annotate(src, size=(400, 300), margin=12)
    a.box((40, 106, 200, 128), t=0.0, dur=0.1)
    a.zoom(c1, t=0.2, dur=0.5)
    o = a.build(duration=1.0)
    s = A.annotate(src, size=(400, 300), margin=12, crop=c1)
    s.box((40, 106, 200, 128), t=0.0, dur=0.1)
    st = s.build(duration=1.0)
    assert o.figure.rect((40, 106, 200, 128), 0.9) == st.figure.rect((40, 106, 200, 128), 0.9)
    z = fk.draw_frame(o, 29).astype(int)
    q = fk.draw_frame(st, 29).astype(int)
    assert np.abs(z - q).mean() < 0.05 and np.abs(z - q).max() <= 3
    # 途中は寄る前と後の間の倍率
    k_mid = (o.figure.area(0.45)[2] - o.figure.area(0.45)[0])
    assert o.figure.area(0.0)[2] - o.figure.area(0.0)[0] <= k_mid


def test_marks_outside_view_are_not_drawn():
    _dry()
    src = _page()
    a = A.annotate(src, size=(320, 240), margin=24)
    a.box((48, 418, 220, 440), t=0.0, dur=0.1)                     # コードの行（下の方）
    a.zoom((32, 40, 352, 280), t=0.2, dur=0.2)                     # 上の方へ寄る
    o = a.build(duration=0.6)
    assert _red(fk.draw_frame(o, 5)).sum() > 100                   # 寄る前は見えている
    after = fk.draw_frame(o, 17)
    # 見えている範囲の外の印は余白にも描かない（コードの行の赤だけが残らない）
    assert _red(after).sum() == 0


# --- 取り込み ---------------------------------------------------------------------------

def test_source_keeps_only_crop_region_and_shrinks_by_power_of_two(tmp_path):
    _dry()
    tall = Image.new("RGB", (400, 8000), (240, 240, 240))
    tall.save("tall.png")
    a = A.annotate("tall.png", size=(400, 300), crop=(0, 4000, 400, 4300))
    o = a.build()
    r = A._Renderer(os.path.abspath("tall.png"), a._spec(), o.figure.size)
    assert r.f == 1.0 and r.src.shape[0] <= 300 + 4 and r.src.shape[1] == 400
    # 一番寄っても 0.3 倍 → 1/2 に縮めて持つ（残りの 0.6 倍は blit が縮める）
    big = Image.new("RGB", (2000, 1500), (240, 240, 240))
    big.save("big.png")
    b = A.annotate("big.png", size=(600, 450))
    r = A._Renderer(os.path.abspath("big.png"), b._spec(), (648, 498))
    assert r.f == 0.5 and r.src.shape[:2] == (750, 1000)


def test_static_frames_reuse_previous_frame():
    _dry()
    a = A.annotate(_page(), size=(320, 240))
    a.box((100, 100, 300, 150), t=0.0, dur=0.1)
    r = A._Renderer(os.path.abspath("page.png"), a._spec(), (368, 288))
    f1 = r.frame(0.5)
    assert r.frame(0.6) is f1                                      # 何も変わらない
    assert r.frame(0.05) is not f1                                 # 現れている途中


# --- 鍵・決定性 ---------------------------------------------------------------------------

def _scene(a):
    a.underline((32, 110, 400, 124), t=0.1)
    a.box((32, 140, 300, 154), t=0.3)
    a.dim((32, 140, 300, 154), t=0.3)
    a.zoom((0, 80, 480, 440), t=0.5, dur=0.4)
    return a


def test_determinism_same_pixels_and_same_key(tmp_path):
    _dry()
    src = _page()
    o1 = _scene(A.annotate(src, size=(320, 240))).build()
    o2 = _scene(A.annotate(src, size=(320, 240))).build()
    assert o1.source == o2.source
    for i in (0, 10, 20, 26):
        assert np.array_equal(fk.draw_frame(o1, i), fk.draw_frame(o2, i))
    # 同じ中身の別のパス → 同じ鍵。中身が違えば違う鍵
    os.makedirs("other", exist_ok=True)
    shutil.copyfile(src, os.path.join("other", "copy.png"))
    o3 = _scene(A.annotate(os.path.join("other", "copy.png"), size=(320, 240))).build()
    assert o3.source == o1.source
    _page("page2.png", seed=1)
    o4 = _scene(A.annotate("page2.png", size=(320, 240))).build()
    assert o4.source != o1.source
    # 効かない値は鍵に入らない: frame が無いときの frame_width・dim / zoom を積む順・until=None
    o5 = _scene(A.annotate(src, size=(320, 240), frame_width=7)).build()
    assert o5.source == o1.source
    b = A.annotate(src, size=(320, 240))
    b.zoom((0, 80, 480, 440), t=0.5, dur=0.4)
    b.dim((32, 140, 300, 154), t=0.3)
    b.underline((32, 110, 400, 124), t=0.1, until=None)
    b.box((32, 140, 300, 154), t=0.3)
    assert b.build().source == o1.source
    # 効く値は入る
    assert _scene(A.annotate(src, size=(320, 240), frame="line")).build().source != o1.source
    assert _scene(A.annotate(src, size=(320, 242))).build().source != o1.source
    c = _scene(A.annotate(src, size=(320, 240)))
    c.box((32, 200, 300, 214), t=0.6)
    assert c.build().source != o1.source


def test_size_defaults_to_project_and_needs_project():
    with pytest.raises(ValueError, match="Project"):
        A.annotate(_page())
    _dry(w=640, h=360)
    o = A.annotate("page.png").build()
    assert o.figure.size == (640 + 48, 360 + 48)


def test_range_errors():
    _dry()
    src = _page()
    for kw, msg in (({"size": (8, 100)}, "size"), ({"size": 300}, "size"),
                    ({"margin": -1}, "margin"), ({"crop": (0, 0, 700, 100)}, "外"),
                    ({"crop": (100, 100, 50, 50)}, "x, y, w, h"),
                    ({"frame": "nocolor"}, "色"), ({"colors": {"nope": "#fff"}}, "nope")):
        with pytest.raises(ValueError, match=msg):
            A.annotate(src, **({"size": (320, 240)} | kw))
    with pytest.raises(TypeError, match="パス"):
        A.annotate(123, size=(320, 240))
    with pytest.raises(FileNotFoundError):
        A.annotate("no_such_shot.png", size=(320, 240))
    a = A.annotate(src, size=(320, 240))
    with pytest.raises(ValueError, match="x, y, w, h"):
        a.box((300, 100, 120, 40))                    # (x, y, w, h) の取り違え
    with pytest.raises(ValueError, match="外"):
        a.underline((0, 0, 641, 10))
    with pytest.raises(ValueError, match="until"):
        a.box((0, 0, 10, 10), t=1.0, until=0.5)
    with pytest.raises(ValueError, match="dur"):
        a.underline((0, 0, 10, 10), dur=0)
    with pytest.raises(ValueError, match="amount"):
        a.dim((0, 0, 10, 10), amount=0)
    with pytest.raises(ValueError, match="easing"):
        a.zoom((0, 0, 100, 100), easing="bouncy")
    a.dim((0, 0, 10, 10), t=1.0, dur=0.5)
    with pytest.raises(ValueError, match="重なって"):
        a.dim((0, 0, 20, 20), t=1.2)
    a.zoom((0, 0, 100, 100), t=1.0, dur=1.0)
    with pytest.raises(ValueError, match="重なって"):
        a.zoom((0, 0, 200, 200), t=1.5)
    with pytest.raises(ValueError, match="width"):
        a.box((0, 0, 10, 10), width=0)


def test_does_not_depend_on_framekit_internals():
    with open(A.__file__, encoding="utf-8") as f:
        src = f.read()
    assert not re.findall(r"\bfk\._\w+", src)
    assert not re.findall(r"framekit\._\w+", src)


# --- 実レンダ -----------------------------------------------------------------------------

def test_real_render_frames_match_draw_frame(tmp_path):
    if not _HAS_FFMPEG:
        pytest.skip("ffmpeg / ffprobe が無い環境")
    p = sv.Project()
    p.configure(width=320, height=180, fps=30)
    a = A.annotate(_page(), size=(240, 180), margin=8)
    a.underline((32, 110, 400, 124), t=0.0, dur=0.2)
    a.box((32, 140, 300, 154), t=0.1, dur=0.1)
    a.dim((32, 140, 300, 154), t=0.1, dur=0.1)
    obj = a.build(duration=0.4)                # Project が dry_run でないので実際に作る
    assert os.path.getsize(obj.source) > 0
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", obj.source,
         "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        check=True, capture_output=True, timeout=120).stdout
    w, h = obj.figure.size
    frames = np.frombuffer(raw, np.uint8).reshape(-1, h, w, 4)
    assert len(frames) == 12
    for i in (0, 4, 11):
        assert np.array_equal(frames[i], fk.draw_frame(obj, i)), i
    assert _red(frames[11]).sum() > 100
    del p


# --- 金型 -------------------------------------------------------------------------------

def _gold():
    _dry()
    return A.annotate(_page(), size=(320, 180), margin=12, frame="line", frame_width=2)


def test_golden_marks(request):
    a = _gold()
    a.underline((32, 110, 400, 124), t=0.0, dur=0.3, width=3, gap=2)
    a.box((32, 170, 300, 184), t=0.2, width=3, pad=3)
    a.dim((32, 106, 420, 188), t=0.2, pad=3)
    assert_golden(request, "annotate", "marks", fk.draw_frame(a.build(duration=0.8), 23),
                  ver=A._ANNOTATE_VER)


def test_golden_underline_mid(request):
    a = _gold()
    a.underline((32, 110, 400, 124), t=0.0, dur=0.6)
    assert_golden(request, "annotate", "underline_mid", fk.draw_frame(a.build(), 6),
                  ver=A._ANNOTATE_VER)


def test_golden_zoom_mid(request):
    a = _gold()
    a.box((48, 418, 220, 440), t=0.0, dur=0.1)
    a.dim((48, 418, 220, 440), t=0.0, dur=0.1)
    a.zoom((20, 380, 380, 470), t=0.2, dur=0.6)
    assert_golden(request, "annotate", "zoom_mid", fk.draw_frame(a.build(), 15),
                  ver=A._ANNOTATE_VER)


def test_golden_zoom_end_letterbox(request):
    """寄った先の縦横比が枠と違う（左右に余る）。印は寄った先の座標で描く"""
    a = _gold()
    a.zoom((24, 96, 264, 256), t=0.0, dur=0.3)
    a.box((32, 110, 200, 124), t=0.3, pad=3, width=3)
    assert_golden(request, "annotate", "zoom_end", fk.draw_frame(a.build(), 17),
                  ver=A._ANNOTATE_VER)
