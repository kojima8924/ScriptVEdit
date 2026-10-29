# -*- coding: utf-8 -*-
"""glint()（ロゴなどの画像に光の帯が一度走るライトスイープ）のテスト。

固定すること:
  - 配置: 画像は枠 size に縦横比を保って収まり、中央に来る（figure.box）。size 省略は画像の大きさ
  - 光: 透明な画素は光らない（α も色も 0 のまま）。光は明るくするだけで、α は変わらず、
    白い画素は白のまま、255 を超えない。帯の明るさは解析式（cos² の山＋芯）どおり
  - 帯は左から右へ動き、通る前と通った後のコマは光の無い絵と同じ画素
  - 地の扱い: 不透過の画像は 'auto' で 'plate'（角が丸く、地の色の画素は光らない）。
    'key' は縁につながる地の色を透明にし、地の色が一様でなければ ValueError
  - 出入り: enter の前は透明、途中は薄く小さい。exit の後は透明
  - 止まっている区間は前のコマを使い回す
  - 決定性（同じ指定なら同じ画素・同じ鍵）。画像の中身で鍵が変わり、パスでは変わらない。
    効かない値（plate でない radius・sweep を積む順・'auto' と解決した値）は鍵に入らない
  - 範囲外・取り違えの ValueError / TypeError
  - 実レンダ（ffmpeg）の生成物のコマが draw_frame と画素で一致する
  - 金型 4 枚（tests/golden/glint/。描き方を変えたら _GLINT_VER を上げる）

見本のロゴはテストの中で Pillow の図形だけで描く（フォントに依らない。実在のロゴは使わない）。
"""
import math
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
from scriptvedit import fx_glint as G  # noqa: E402
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


def _logo_alpha(path="logo.png", w=300, h=100):
    """透過のロゴ: 紺の角丸の紋章（白い三角）と、濃い灰の「字」の棒（図形だけ）"""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((4, 8, 88, 92), 16, fill=(24, 52, 120, 255))
    d.polygon([(24, 76), (46, 24), (68, 76)], fill=(255, 255, 255, 255))
    for i, x in enumerate(range(104, 290, 38)):
        d.rectangle((x, 30 + (i % 2) * 6, x + 26, 74), fill=(40, 44, 52, 255))
    im.save(path)
    return os.path.abspath(path)


def _logo_white(path="white.png", w=300, h=120, bg=(255, 255, 255)):
    """不透過の白地のロゴ: 赤い輪と黒い棒"""
    im = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(im)
    d.ellipse((16, 16, 104, 104), fill=(210, 30, 40))
    d.ellipse((42, 42, 78, 78), fill=bg)
    d.rectangle((124, 36, 284, 84), fill=(20, 20, 20))
    im.save(path)
    return os.path.abspath(path)


def _flat(path="flat.png", w=200, h=100, rgb=(40, 60, 100)):
    """一色の不透明な四角（透明な余白つき）。光の値を解析式と比べる"""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle((10, 10, w - 11, h - 11), fill=rgb + (255,))
    im.save(path)
    return os.path.abspath(path)


def _frames(o, n=None):
    n = n or _count_frames(o)
    return [fk.draw_frame(o, i) for i in range(n)]


def _count_frames(o):
    """build の draw が受ける最後のコマ番号 + 1"""
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


# --- 配置 ---------------------------------------------------------------------------------

def test_fit_and_box():
    _dry()
    src = _logo_alpha()
    o = G.glint(src, size=(600, 300)).build()
    assert o.figure.size == (600, 300)
    assert o.figure.box == (0.0, 50.0, 600.0, 250.0)          # 300x100 → 2 倍で上下に余る
    assert o.figure.matte == "alpha"
    a = fk.draw_frame(o, 0)
    assert a.shape == (300, 600, 4)
    assert a[:49, :, 3].max() == 0 and a[251:, :, 3].max() == 0
    # size 省略は画像の大きさのまま（等倍・整数位置なので画素そのまま）
    b = G.glint(src).build()
    assert b.figure.size == (300, 100)
    im = np.asarray(Image.open(src).convert("RGBA"))
    assert np.array_equal(fk.draw_frame(b, 0), im)


# --- 光 -----------------------------------------------------------------------------------

def test_light_only_brightens_opaque_pixels():
    _dry()
    src = _logo_alpha()
    plain = G.glint(src, size=(300, 100)).build(duration=1.5)
    g = G.glint(src, size=(300, 100))
    g.sweep(t=0.2, dur=1.0, strength=0.8)
    o = g.build(duration=1.5)
    base = fk.draw_frame(plain, 0).astype(int)
    clear = base[..., 3] == 0
    white = (base[..., 3] == 255) & (base[..., :3].min(axis=2) == 255)
    lit_any = False
    for i in range(0, 45, 2):
        f = fk.draw_frame(o, i).astype(int)
        assert np.array_equal(f[..., 3], base[..., 3]), i         # α は変わらない
        assert f[clear].max() == 0, i                              # 透明な画素は光らない
        assert np.array_equal(f[white], base[white]), i            # 白は白のまま
        assert (f[..., :3] >= base[..., :3] - 1).all(), i          # 明るくするだけ
        lit_any |= bool((f[..., :3] > base[..., :3] + 20).any())
    assert lit_any


def test_band_moves_left_to_right_and_is_off_outside():
    _dry()
    src = _flat()
    plain = fk.draw_frame(G.glint(src).build(), 0).astype(int)
    g = G.glint(src)
    g.sweep(t=0.5, dur=1.0, easing=None)
    o = g.build(duration=2.0)
    for i in (0, 10, 15, 45, 50, 59):                               # 通る前・通った後
        assert np.array_equal(fk.draw_frame(o, i).astype(int), plain), i
    xs = []
    for i in (20, 26, 32, 38):
        diff = (fk.draw_frame(o, i).astype(int) - plain)[..., :3].sum(axis=2)
        cols = diff.sum(axis=0)
        assert cols.sum() > 0
        xs.append((cols * np.arange(len(cols))).sum() / cols.sum())
    assert all(b > a + 5 for a, b in zip(xs, xs[1:]))


def test_light_matches_band_formula():
    """一色の四角で、画素の値が「c + L·(1 − c)」（L は帯の解析式）と一致する（列の切り詰めも含む）"""
    _dry()
    rgb = (40, 60, 100)
    src = _flat(rgb=rgb)
    g = G.glint(src)
    g.sweep(t=0.0, dur=1.0, angle=25, width=0.3, strength=0.6, easing=None)
    o = g.build()
    i = 12
    f = fk.draw_frame(o, i).astype(float)
    # 中身（不透明な四角）は (10, 10)〜(190, 90)。帯の道のりは角の射影
    th = math.radians(25)
    nx, ny = math.cos(th), math.sin(th)
    cx, cy = 100.0, 50.0
    ps = [(x - cx) * nx + (y - cy) * ny for x in (10, 190) for y in (10, 90)]
    lo, hi = min(ps), max(ps)
    hw = 0.3 * (hi - lo) / 2
    c = (lo - hw) + (hi - lo + 2 * hw) * (i / 30)
    ys, xs = np.mgrid[12:88, 12:188]
    d = ((xs + 0.5 - cx) * nx + (ys + 0.5 - cy) * ny - c) / hw
    L = np.asarray(G._band(np, d)) * 0.6
    for ch in range(3):
        cc = rgb[ch] / 255.0
        want = (cc + L * (1 - cc)) * 255
        assert np.abs(f[12:88, 12:188, ch] - want).max() <= 1.01
    assert L.max() > 0.5                                         # 帯が中にある


def test_multiple_sweeps_and_color():
    _dry()
    src = _flat()
    g = G.glint(src)
    g.sweep(t=0.0, dur=0.5).sweep(t=1.0, dur=0.5, color="#ffd060")
    o = g.build()
    plain = fk.draw_frame(o, 0).astype(int)
    a = fk.draw_frame(o, 8).astype(int) - plain
    b = fk.draw_frame(o, 38).astype(int) - plain
    assert a[..., :3].sum() > 0 and b[..., :3].sum() > 0
    assert np.array_equal(fk.draw_frame(o, 20), fk.draw_frame(o, 0))     # 間は光なし
    # 黄の光は青のチャンネルをあまり上げない
    assert b[..., 2].sum() < b[..., 0].sum()


# --- 地の扱い -----------------------------------------------------------------------------

def test_plate_rounds_corners_and_keeps_ground_unlit():
    _dry()
    src = _logo_white()
    g = G.glint(src, radius=16)
    assert g._matte == "plate"
    g.sweep(t=0.0, dur=1.0, strength=1.0)
    o = g.build()
    plain = fk.draw_frame(G.glint(src, radius=16).build(), 0).astype(int)
    assert plain[0, 0, 3] == 0 and plain[60, 150, 3] == 255           # 角は丸く抜ける
    ground = (plain[..., 3] == 255) & (plain[..., :3].min(axis=2) == 255)
    logo = plain[..., :3].max(axis=2) < 60                            # 黒い棒
    grew = np.zeros(plain.shape[:2], bool)
    for i in range(0, 30, 3):
        f = fk.draw_frame(o, i).astype(int)
        assert np.array_equal(f[ground], plain[ground]), i
        grew |= f[..., :3].sum(axis=2) > plain[..., :3].sum(axis=2) + 30
    assert grew[logo].mean() > 0.5
    # 地が薄い灰でも地は光らない（白でなくても地の色の画素には当てない）
    gsrc = _logo_white("gray.png", bg=(236, 236, 236))
    h = G.glint(gsrc)
    h.sweep(t=0.0, dur=1.0, strength=1.0)
    oh = h.build()
    p2 = fk.draw_frame(G.glint(gsrc).build(), 0).astype(int)
    for i in range(0, 30, 3):
        f = fk.draw_frame(oh, i).astype(int)
        assert np.abs(f[5:115, 120:124] - p2[5:115, 120:124]).max() <= 1, i


def test_key_makes_ground_transparent():
    _dry()
    src = _logo_white()
    o = G.glint(src, matte="key").build()
    assert o.figure.matte == "key"
    f = fk.draw_frame(o, 0)
    assert f[2, 2, 3] == 0 and f[60, 118, 3] == 0                    # 地は透明
    assert f[60, 200, 3] == 255 and f[20, 60, 3] == 255              # 棒と輪は不透明
    assert f[60, 60, 3] == 255                                       # 輪の中の白（縁につながらない）は残る
    # 地の色が一様でない画像は key にできない
    im = Image.new("RGB", (100, 60))
    ImageDraw.Draw(im).rectangle((0, 0, 49, 59), fill=(255, 0, 0))
    ImageDraw.Draw(im).rectangle((50, 0, 99, 59), fill=(0, 0, 255))
    im.save("two.png")
    with pytest.raises(ValueError, match="一様"):
        G.glint("two.png", matte="key")
    assert G.glint("two.png")._matte == "plate"                       # plate は使える（全面に当てる）


# --- 出入り -------------------------------------------------------------------------------

def test_enter_and_exit():
    _dry()
    src = _logo_alpha()
    g = G.glint(src, size=(300, 100))
    g.enter(t=0.2, dur=0.6, scale=0.8)
    g.exit(t=1.4, dur=0.4)
    o = g.build()
    n = _count_frames(o)
    assert n == 55                                     # 1.8 秒 → 54 コマ目が終わりの絵
    assert fk.draw_frame(o, 0)[..., 3].max() == 0
    assert fk.draw_frame(o, 6)[..., 3].max() == 0       # t=0.2 ちょうどもまだ
    full = fk.draw_frame(o, 30)
    mid = fk.draw_frame(o, 12)
    assert 0 < mid[..., 3].max() < 255
    ys, xs = np.nonzero(mid[..., 3] > 0)
    yf, xf = np.nonzero(full[..., 3] > 0)
    assert xs.max() - xs.min() < xf.max() - xf.min()   # 小さい
    assert fk.draw_frame(o, n - 1)[..., 3].max() == 0   # 消えた後は透明
    assert 0 < fk.draw_frame(o, 48)[..., 3].max() < 255


def test_static_frames_reuse_previous_frame():
    _dry()
    g = G.glint(_logo_alpha())
    g.sweep(t=0.5, dur=0.3)
    r = G._Renderer(np.asarray(Image.open("logo.png").convert("RGBA")), g._spec())
    f1 = r.frame(0.1)
    assert r.frame(0.2) is f1
    assert r.frame(0.6) is not f1


# --- 鍵・決定性 ---------------------------------------------------------------------------

def _scene(g):
    g.enter(t=0.0, dur=0.3)
    g.sweep(t=0.4, dur=0.6)
    g.sweep(t=0.4, dur=0.6, angle=-15, strength=0.2)
    return g


def test_determinism_same_pixels_and_same_key():
    _dry()
    src = _logo_alpha()
    o1 = _scene(G.glint(src, size=(320, 120))).build()
    o2 = _scene(G.glint(src, size=(320, 120))).build()
    assert o1.source == o2.source
    for i in (0, 5, 18, 25):
        assert np.array_equal(fk.draw_frame(o1, i), fk.draw_frame(o2, i))
    os.makedirs("other", exist_ok=True)
    shutil.copyfile(src, os.path.join("other", "copy.png"))
    assert _scene(G.glint(os.path.join("other", "copy.png"), size=(320, 120))).build().source \
        == o1.source
    _logo_alpha("logo2.png", w=302)
    assert _scene(G.glint("logo2.png", size=(320, 120))).build().source != o1.source
    # 効かない値は鍵に入らない: plate でない radius・sweep を積む順・'auto' と解決した値
    assert _scene(G.glint(src, size=(320, 120), radius=3)).build().source == o1.source
    assert _scene(G.glint(src, size=(320, 120), matte="alpha")).build().source == o1.source
    b = G.glint(src, size=(320, 120))
    b.sweep(t=0.4, dur=0.6, angle=-15, strength=0.2)
    b.sweep(t=0.4, dur=0.6)
    b.enter(t=0.0, dur=0.3)
    assert b.build().source == o1.source
    # 効く値は入る
    assert _scene(G.glint(src, size=(320, 122))).build().source != o1.source
    c = _scene(G.glint(src, size=(320, 120)))
    c.exit(1.2)
    assert c.build().source != o1.source
    d = G.glint(src, size=(320, 120)).enter(t=0.0, dur=0.3).sweep(t=0.4, dur=0.6, strength=0.5)
    d.sweep(t=0.4, dur=0.6, angle=-15, strength=0.2)
    assert d.build().source != o1.source
    w = _logo_white()
    assert G.glint(w, radius=3).build().source != G.glint(w, radius=12).build().source


def test_range_errors():
    _dry()
    src = _logo_alpha()
    for kw, msg in (({"size": (8, 100)}, "size"), ({"size": 300}, "size"),
                    ({"size": (100.5, 50)}, "size"), ({"matte": "white"}, "matte"),
                    ({"radius": -1}, "radius"), ({"colors": {"nope": "#fff"}}, "nope")):
        with pytest.raises(ValueError, match=msg):
            G.glint(src, **kw)
    with pytest.raises(TypeError, match="パス"):
        G.glint(123)
    with pytest.raises(FileNotFoundError):
        G.glint("no_such_logo.png")
    with open("bad.png", "wb") as f:
        f.write(b"not an image")
    with pytest.raises(ValueError, match="読めません"):
        G.glint("bad.png")
    Image.new("RGBA", (40, 40), (0, 0, 0, 0)).save("empty.png")
    with pytest.raises(ValueError, match="光を当てる画素"):
        G.glint("empty.png")
    g = G.glint(src)
    for kw, msg in (({"angle": 70}, "angle"), ({"width": 0}, "width"), ({"strength": 1.5}, "strength"),
                    ({"dur": 0}, "dur"), ({"t": -1}, "t"), ({"color": "nocolor"}, "色"),
                    ({"easing": "bouncy"}, "easing")):
        with pytest.raises(ValueError, match=msg):
            g.sweep(**kw)
    with pytest.raises(ValueError, match="scale"):
        g.enter(scale=0.3)
    with pytest.raises(ValueError, match="scale"):
        g.exit(2.0, scale=1.2)
    g.enter(t=0.0, dur=1.0)
    with pytest.raises(ValueError, match="1回だけ"):
        g.enter(t=0.5)
    with pytest.raises(ValueError, match="enter が終わる"):
        g.exit(0.5)
    g.exit(2.0)
    with pytest.raises(ValueError, match="1回だけ"):
        g.exit(3.0)
    h = G.glint(src).exit(0.5)
    with pytest.raises(ValueError, match="exit"):
        h.enter(t=0.2, dur=0.5)
    with pytest.raises(ValueError, match="duration"):
        G.glint(src).build(duration=0)


def test_describe_lists_glint_and_choices_match():
    entry = sv.describe(name="glint")["factories"][0]
    assert entry["name"] == "glint" and entry["category"] == "ロゴの演出"
    assert entry["params"]["matte"]["choices"] == list(G._MATTES)
    assert sv.glint is G.glint


def test_does_not_depend_on_framekit_internals():
    with open(G.__file__, encoding="utf-8") as f:
        src = f.read()
    assert not re.findall(r"\bfk\._\w+", src)
    assert not re.findall(r"framekit\._\w+", src)


# --- 実レンダ -----------------------------------------------------------------------------

def test_real_render_frames_match_draw_frame():
    if not _HAS_FFMPEG:
        pytest.skip("ffmpeg / ffprobe が無い環境")
    p = sv.Project()
    p.configure(width=320, height=180, fps=30)
    g = G.glint(_logo_alpha(), size=(240, 80))
    g.enter(t=0.0, dur=0.1)
    g.sweep(t=0.1, dur=0.2)
    obj = g.build(duration=0.4)                # Project が dry_run でないので実際に作る
    assert os.path.getsize(obj.source) > 0
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", obj.source,
         "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        check=True, capture_output=True, timeout=120).stdout
    w, h = obj.figure.size
    frames = np.frombuffer(raw, np.uint8).reshape(-1, h, w, 4)
    assert len(frames) == 12
    for i in (0, 2, 6, 11):
        assert np.array_equal(frames[i], fk.draw_frame(obj, i)), i
    del p


# --- 金型 -------------------------------------------------------------------------------

def test_golden_alpha_mid(request):
    _dry()
    g = G.glint(_logo_alpha(), size=(320, 120))
    g.sweep(t=0.0, dur=0.6)
    assert_golden(request, "glint", "alpha_mid", fk.draw_frame(g.build(), 9), ver=G._GLINT_VER)


def test_golden_plate_mid(request):
    _dry()
    g = G.glint(_logo_white(), size=(320, 140))
    g.sweep(t=0.0, dur=0.6, angle=-20)
    assert_golden(request, "glint", "plate_mid", fk.draw_frame(g.build(), 11), ver=G._GLINT_VER)


def test_golden_key_mid(request):
    _dry()
    g = G.glint(_logo_white(), size=(320, 140), matte="key")
    g.sweep(t=0.0, dur=0.6, width=0.4, color="#ffe0a0")
    assert_golden(request, "glint", "key_mid", fk.draw_frame(g.build(), 8), ver=G._GLINT_VER)


def test_golden_enter_mid(request):
    _dry()
    g = G.glint(_logo_alpha(), size=(320, 120))
    g.enter(t=0.0, dur=0.6, scale=0.8)
    g.sweep(t=0.1, dur=0.6)
    assert_golden(request, "glint", "enter_mid", fk.draw_frame(g.build(), 9), ver=G._GLINT_VER)
