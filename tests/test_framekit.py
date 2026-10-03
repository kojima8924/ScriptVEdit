# -*- coding: utf-8 -*-
"""framekit（図解アニメの共通部品）のテスト。

固定すること:
  - norm（鍵の正規形）・easing（数値と鍵）・pace（拍の時刻）・sec_frame
  - label の画素が text_image の PNG と一致する（3書体）
  - 描画の正確さ: dots の α の量の保存と重心の連続な移動、polyline の幅と破線の長さ、
    blit の整数位置での画素の不変と端数位置での重心の移動、rect / circle / hatch、
    斜めの縁でも α の量が揺れない、円の面積の偏りが無い、多角形の内外、0 長の破線、
    縮小の連続性
  - build の鍵: params・ver・切り上げ前の寸法・フォント・Sprite の中身・files で変わり、
    渡していない値（draw・info・text）・渡す順では変わらない。dry_run で draw を呼ばない。
    cold と warm の dry_run が一致する。生成物の .mov がレイヤーの依存に残る
  - memo: レイヤーの2回の exec で1回だけ計算する
  - 金型（tests/golden/framekit/）: 描き方を変えたら _FRAMEKIT_VER を上げる。
    --golden-update でも版を上げずに絵だけ変えたものは書き換えない
"""
import glob
import json
import math
import os
import shutil
import sys
import uuid

import pytest

np = pytest.importorskip("numpy", reason="numpy が無い環境")
pytest.importorskip("cv2", reason="numpy・opencv が無い環境")
pytest.importorskip("PIL", reason="Pillow が無い環境")
from PIL import Image  # noqa: E402

import scriptvedit as sv  # noqa: E402
from scriptvedit import framekit as fk  # noqa: E402
from scriptvedit import textimage as ti  # noqa: E402
from scriptvedit.audit import _audit_text_images  # noqa: E402
from scriptvedit.context import _exec_stack, activate, current_project  # noqa: E402
from scriptvedit.text import _resolve_font  # noqa: E402

from framekit_golden import assert_golden  # noqa: E402

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

_VARIABLE_FONT_CANDIDATES = (
    "C:/Windows/Fonts/NotoSerifJP-VF.ttf",
    "C:/Windows/Fonts/NotoSansJP-VF.ttf",
    "C:/Windows/Fonts/bahnschrift.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-VF.ttf",
    "/System/Library/Fonts/SFNS.ttf",
)
_LATIN_FONT_CANDIDATES = (
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)


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


def _jp_font():
    try:
        return _resolve_font(None)
    except FileNotFoundError:
        pytest.skip("日本語フォントが無い環境")


def _latin_font():
    for path in _LATIN_FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    pytest.skip("欧文フォントが見つからない環境")


def _variable_font():
    cands = list(_VARIABLE_FONT_CANDIDATES)
    for pattern in ("/usr/share/fonts/**/*VF*.ttf", "/usr/share/fonts/**/*wght*.ttf"):
        cands.extend(sorted(glob.glob(pattern, recursive=True)))
    for path in cands:
        if os.path.exists(path) and any(a[0] == "wght" for a in ti._fvar_axes(path, 0)):
            return path
    pytest.skip("wght 軸を持つ可変フォントが見つからない環境")


def _need_ffmpeg():
    if not _HAS_FFMPEG:
        pytest.skip("ffmpeg / ffprobe が無い環境")


def _alpha(d):
    return d[..., 3].astype(np.float64)


def _centroid(d):
    a = _alpha(d)
    ys, xs = np.mgrid[0:a.shape[0], 0:a.shape[1]]
    s = a.sum()
    return (float((a * (xs + 0.5)).sum() / s), float((a * (ys + 0.5)).sum() / s))


# --- norm ---------------------------------------------------------------------

def test_norm_floats_and_containers():
    assert fk.norm(1.23456789) == 1.234568
    assert fk.norm(-0.0) == 0.0 and math.copysign(1, fk.norm(-0.0)) == 1
    assert math.copysign(1, fk.norm(-1e-9)) == 1          # 丸めて -0.0 になる値も 0.0
    assert fk.norm((1, 2.0000001, "a")) == [1, 2.0, "a"]
    assert fk.norm({"b": 1, "a": (0.5,)}) == {"a": [0.5], "b": 1}
    assert list(fk.norm({"b": 1, "a": 2})) == ["a", "b"]
    assert fk.norm(np.float32(0.25)) == 0.25 and type(fk.norm(np.float32(0.25))) is float
    assert fk.norm(np.int64(3)) == 3 and type(fk.norm(np.int64(3))) is int
    assert fk.norm(np.array([1.5, 2.5])) == [1.5, 2.5]
    assert fk.norm(True) is True and fk.norm(None) is None
    from fractions import Fraction
    assert fk.norm(Fraction(1, 3)) == 0.333333
    assert fk.norm(sv.lerp(0, 1, sv.clip(0.5, 0, 1))) == sv.lerp(
        0, 1, sv.clip(0.5, 0, 1)).to_ffmpeg("u")
    ref = fk.FontRef("C:/x.ttf", 1, (700.0,), "abc")
    assert fk.norm(ref) == ["abc", 1, [700]]        # 生のパスは入らない
    # 整数の値の float は int にそろえる（3 と 3.0 で鍵を割らない。textimage._norm_num と同じ）
    assert json.dumps(fk.norm({"w": 3})) == json.dumps(fk.norm({"w": 3.0})) == '{"w": 3}'
    assert type(fk.norm(2.0000001)) is int and type(fk.norm(np.float64(4.0))) is int
    assert json.dumps(fk.norm(-0.0)) == "0" and fk.norm(0.5) == 0.5


def test_norm_rejects():
    for bad in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ValueError, match="NaN"):
            fk.norm(bad)
    with pytest.raises(TypeError, match="関数は鍵にできない"):
        fk.norm(lambda u: u)
    with pytest.raises(TypeError, match="関数は鍵にできない"):
        fk.norm({"f": print})
    with pytest.raises(TypeError, match="文字列"):
        fk.norm({1: 2})
    with pytest.raises(TypeError, match="鍵にできない型"):
        fk.norm(object())


# --- easing ---------------------------------------------------------------------

def test_easing_values_and_keys():
    f, key = fk.easing("t", None)
    assert key == "u" and f(0.3) == pytest.approx(0.3)
    assert f(-1) == 0.0 and f(2) == 1.0                   # u は 0..1 に切り詰める
    f, key = fk.easing("t", "ease_in_quad")
    assert f(0.5) == pytest.approx(0.25)
    f2, key2 = fk.easing("t", sv.ease_in_quad)            # 関数と名前は同じ鍵
    assert key2 == key and f2(0.7) == pytest.approx(0.49)
    f3, key3 = fk.easing("t", sv.ease_out_cubic)
    assert f3(0.5) == pytest.approx(1 - 0.5 ** 3) and key3 != key
    f4, key4 = fk.easing("t", sv.lerp(0, 2, sv.clip(0.5, 0, 1)))  # Expr（u を含まない定数式）
    assert f4(0.1) == pytest.approx(1.0) and isinstance(key4, str)
    bez = sv.ease_cubic_bezier(0.25, 0.1, 0.25, 1.0)
    fb, kb = fk.easing("t", bez)
    assert 0 < fb(0.5) < 1 and fk.easing("t", bez)[1] == kb   # 同じ曲線は同じ鍵


def test_easing_errors():
    with pytest.raises(ValueError, match="もしかして"):
        fk.easing("t", "ease_in_quadd")
    with pytest.raises(TypeError, match="数値は不可"):
        fk.easing("t", 0.5)
    with pytest.raises(TypeError):
        fk.easing("t", [1, 2])


# --- 時刻 ---------------------------------------------------------------------------

def test_sec_frame_matches_stillseq_rounding():
    from fractions import Fraction
    assert fk.sec_frame(0.5, 30) == 15
    assert fk.sec_frame(0.25, 2) == 1                # 半分ちょうどは切り上げ
    assert fk.sec_frame(1 / 60, 30) == 0             # 1/60 の10進表記はわずかに半分未満
    assert fk.sec_frame(0.1 + 0.2, Fraction(30)) == 9
    assert fk.sec_frame(1.0, 29.97) == 30            # 2997/100
    assert fk.n_frames_for(0.001, 30) == 1           # 最低1コマ
    assert fk.n_frames_for(2, 30) == 60


def test_pace_rules():
    starts, end = fk.pace(8, first=0.5, slow=2, ratio=0.5, fps=30)
    d = np.diff(starts)
    assert starts[0] == 0.0
    assert d[0] == pytest.approx(0.5) and d[1] == pytest.approx(0.5)     # slow 拍
    assert d[2] == pytest.approx(0.25) and d[3] == pytest.approx(0.125)  # ×ratio
    assert min(d) >= 1 / 30 - 1e-12                                      # 下限 1/fps
    assert d[-1] == pytest.approx(1 / 30)
    assert all(b > a for a, b in zip(starts, starts[1:]))                # 単調
    assert end == pytest.approx(starts[-1] + max(1 / 30, d[-1]) + 1.0)


def test_pace_min_dt_below_frame_puts_beats_in_one_frame():
    starts, _end = fk.pace(200, first=0.2, slow=1, ratio=0.5, min_dt=0.002, fps=30)
    frames_of = [fk.sec_frame(t, 30) for t in starts]
    assert max(np.bincount(frames_of)) > 1                  # 1コマに複数の拍
    assert np.diff(starts)[-1] == pytest.approx(0.002)


def test_pace_total_lowers_ratio():
    starts, end = fk.pace(30, first=0.6, slow=3, ratio=0.95, total=8.0, fps=30)
    assert end <= 8.0 + 1e-9
    assert end > 7.9                                     # 下げすぎない（二分法の境目）
    free, free_end = fk.pace(30, first=0.6, slow=3, ratio=0.95, fps=30)
    assert free_end > 8.0
    # 収まるなら ratio は変えない
    same = fk.pace(30, first=0.6, slow=3, ratio=0.95, total=free_end + 1)
    assert same == (free, free_end)
    with pytest.raises(ValueError, match="拍が多すぎる"):
        fk.pace(500, first=0.6, slow=3, ratio=0.9, total=5.0, fps=30)


def test_pace_at():
    starts, _ = fk.pace(6, at=[0.0, 1.0, 1.5], first=0.6, slow=2, ratio=0.5, fps=30)
    assert starts[:3] == [0.0, 1.0, 1.5]
    # 拍 2 以降: slow=2 を過ぎているので「直前の間隔 0.5 × 0.5」から縮める
    assert np.diff(starts)[2:] == pytest.approx([0.25, 0.125, 0.0625])
    with pytest.raises(ValueError, match="単調増加"):
        fk.pace(4, at=[0.0, 1.0, 1.0])
    with pytest.raises(ValueError, match="単調増加"):
        fk.pace(4, at=[0.0, 2.0, 1.0])
    with pytest.raises(ValueError, match="多い"):
        fk.pace(2, at=[0.0, 1.0, 2.0])


def test_pace_errors():
    for kw in ({"first": 0}, {"ratio": 0}, {"ratio": 1.5}, {"min_dt": 0}, {"slow": -1},
               {"hold_end": -1}):
        with pytest.raises(ValueError):
            fk.pace(5, **kw)
    with pytest.raises(ValueError):
        fk.pace(0)


# --- 色 ----------------------------------------------------------------------------

def test_palette_and_color():
    pal = fk.palette("t")
    assert pal["fg"] == (255, 255, 255, 255) and pal["accent"] == (0xE0, 0x24, 0x1B, 255)
    pal2 = fk.palette("t", {"accent": "red@0.5"})
    assert pal2["accent"] == (255, 0, 0, 128) and pal2["fg"] == pal["fg"]
    assert fk.color("t", "accent", pal) == pal["accent"]
    assert fk.color("t", "#102030", pal) == (16, 32, 48, 255)
    assert fk.color("t", (1, 2, 3), pal) == (1, 2, 3, 255)
    with pytest.raises(ValueError, match="もしかして: accent"):
        fk.palette("t", {"acent": "red"})
    with pytest.raises(ValueError, match="もしかして: muted"):
        fk.color("t", "mutedd", pal)


def test_need_error_message(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake(name, *a, **k):
        if name == "cv2":
            raise ImportError("no cv2")
        return real_import(name, *a, **k)
    monkeypatch.setattr(fk, "_DEPS", None)
    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(ImportError, match=r'regex_view: numpy・opencv-python・Pillow が要ります'
                                          r'（pip install "scriptvedit\[figures\]"）'):
        fk.need("regex_view")


# --- 文字 --------------------------------------------------------------------------

def _label_cases():
    return {
        "jp": lambda: (_jp_font(), dict(
            content="犯人は {accent|正規表現} だった\n2行目 \\s+$", markup=True,
            styles={"accent": {"color": "#e0241b"}}, size=40, border=3,
            shadow=(2, 3), shadow_blur=2, align="center")),
        "latin": lambda: (_latin_font(), dict(
            content="parseInt(0.0000005) = 5", size=36, color="#c8ccd2",
            background="#14161a", background_radius=8, padding=(12, 6))),
        "variable": lambda: (_variable_font(), dict(
            content=[("Weight ", {}), ("Bold", {"weight": 800})], size=44, weight=300,
            border=2, border_color="black@0.8")),
    }


@pytest.mark.parametrize("case", ["jp", "latin", "variable"])
def test_label_matches_text_image_pixels(case):
    font, kw = _label_cases()[case]()
    content = kw.pop("content")
    sprite = fk.label("t", content, font=font, **kw)
    obj = sv.text_image(content, font=font, **kw)
    with Image.open(obj.source) as im:
        want = np.asarray(im.convert("RGBA"))
    assert sprite.rgba.shape == want.shape
    assert np.array_equal(sprite.rgba, want)
    assert (sprite.w, sprite.h) == (want.shape[1], want.shape[0])
    for k in ("size_min", "border", "lines", "missing", "content_width"):
        assert sprite.meta[k] == obj._text_image[k], k
    assert not sprite.rgba.flags.writeable
    # 同じ引数はプロセス内で使い回す
    assert fk.label("t", content, font=font, **kw) is sprite
    # フォントの情報: 生のパスは鍵に入れない（ffp・index・coords）
    assert sprite.fonts[0].path == font.replace("\\", "/")
    assert sprite.fonts[0].ffp == fk.font_ref("t", font).ffp


def test_label_accepts_tuple_colors_and_base():
    font = _jp_font()
    pal = fk.palette("t")
    a = fk.label("t", "あ", font=font, size=40, color=pal["accent"])
    b = fk.label("t", "あ", font=font, size=40, color="0xe0241bff")
    assert np.array_equal(a.rgba, b.rgba)
    x, y = a.base
    assert 0 <= x < a.w and 0 < y < a.h       # 行頭の x と1行目のベースライン
    with pytest.raises(TypeError, match="もしかして: border"):
        fk.label("t", "あ", font=font, bordr=2)


def test_font_ref_is_content_addressed(tmp_path):
    font = _latin_font()
    copy = tmp_path / "copied_font.ttf"
    shutil.copyfile(font, copy)
    a = fk.font_ref("t", font)
    b = fk.font_ref("t", str(copy))
    assert a.ffp == b.ffp and a.path != b.path and a.coords is None


def test_font_ref_variable_weight_coords():
    vf = _variable_font()
    r = fk.font_ref("t", vf, weight=700)
    assert r.coords is not None and 700.0 in r.coords


def _audit_codes(meta):
    p = sv.Project()
    p.configure(width=1920, height=1080, fps=30)
    obj = sv.Object("x.png")
    obj._text_image = meta
    findings = []
    _audit_text_images(p, [obj], findings)
    return {f["code"] for f in findings}


def test_text_meta_reports_smallest_text_and_audit_reads_it():
    font = _jp_font()
    big = fk.label("t", "大きい見出し", font=font, size=64, border=4)
    small = fk.label("t", "注", font=font, size=20)
    meta = fk.text_meta([big, (small, 1.5)], width=640, height=360)
    assert meta["size_min"] == pytest.approx(30)          # 20 × 1.5
    assert meta["size_max"] == pytest.approx(64)
    assert meta["border"] == 0                             # 一番小さい文字の装飾
    assert meta["content_width"] == pytest.approx(big.meta["content_width"])
    assert meta["lines"] == 2 and (meta["width"], meta["height"]) == (640, 360)
    assert fk.text_meta([], width=1, height=1) is None
    codes = _audit_codes(meta)
    assert "text-too-small" in codes and "text-no-decoration" in codes


def test_text_meta_decoration_only_when_every_text_has_it():
    """大きな見出しが素のまま・小さな注記だけ縁取り、の図でも text-no-decoration を出す"""
    font = _jp_font()
    big = fk.label("t", "見出し", font=font, size=64)                 # 装飾なし
    small = fk.label("t", "注", font=font, size=40, border=3)
    meta = fk.text_meta([big, small], width=1920, height=1080)
    assert meta["border"] == 0 and meta["shadow"] == (0, 0) and meta["background"] is False
    assert "text-no-decoration" in _audit_codes(meta)
    # 全部が装飾つきなら、一番小さい文字の装飾を申告する
    big2 = fk.label("t", "見出し", font=font, size=64, background="#14161a")
    meta2 = fk.text_meta([big2, small], width=1920, height=1080)
    assert meta2["border"] == 3 and meta2["background"] is False
    assert "text-no-decoration" not in _audit_codes(meta2)


# --- 描画 --------------------------------------------------------------------------

def test_canvas_over_and_to_rgba8_roundtrip():
    rs = np.random.RandomState(3)
    img = rs.randint(0, 256, (20, 30, 4)).astype(np.uint8)
    img[0, :, 3] = 0
    d = fk.canvas(40, 30)
    fk.over(d, img, 5, 4)
    out = fk.to_rgba8(d)
    region = out[4:24, 5:35]
    vis = img[..., 3] > 0
    assert np.array_equal(region[..., 3], img[..., 3])
    assert np.array_equal(region[vis][:, :3], img[vis][:, :3])
    assert not out[..., 3][:4].any() and not out[0:4].any()
    assert np.array_equal(fk.to_rgba8(fk.canvas(8, 8)), np.zeros((8, 8, 4), np.uint8))


def test_blit_integer_keeps_pixels_and_fraction_moves_centroid():
    font = _jp_font()
    s = fk.label("t", "図", font=font, size=40, border=3)
    d = fk.canvas(120, 120)
    fk.blit(d, s, 10, 7)
    out = fk.to_rgba8(d)[7:7 + s.h, 10:10 + s.w]
    vis = s.rgba[..., 3] > 0
    assert np.array_equal(out[..., 3], s.rgba[..., 3])
    assert np.array_equal(out[vis], s.rgba[vis])
    c0 = _centroid(d)
    for fx, fy in ((0.25, 0.0), (0.5, 0.75), (0.1, 0.3)):
        e = fk.canvas(120, 120)
        fk.blit(e, s, 10 + fx, 7 + fy)
        c = _centroid(e)
        assert c[0] - c0[0] == pytest.approx(fx, abs=0.02)
        assert c[1] - c0[1] == pytest.approx(fy, abs=0.02)
        assert _alpha(e).sum() == pytest.approx(_alpha(d).sum(), rel=1e-3)


def test_blit_rotate_scale_alpha_clip():
    src = np.zeros((20, 40, 4), np.uint8)
    src[...] = (255, 255, 255, 255)
    d = fk.canvas(100, 100)
    fk.blit(d, src, 30, 40, angle=90)
    a = _alpha(d)
    ys, xs = np.nonzero(a > 0.5)
    assert xs.max() - xs.min() + 1 == pytest.approx(20, abs=1)     # 縦長になる
    assert ys.max() - ys.min() + 1 == pytest.approx(40, abs=1)
    assert _centroid(d) == pytest.approx((50.0, 50.0), abs=0.05)   # 中心は動かない
    e = fk.canvas(100, 100)
    fk.blit(e, src, 30, 40, scale=0.5, alpha=0.5)
    assert _alpha(e).sum() == pytest.approx(20 * 40 * 0.25 * 0.5, rel=0.01)
    g = fk.canvas(100, 100)
    fk.blit(g, src, 30, 40, scale=0.2)                              # 先に INTER_AREA で縮める
    assert _alpha(g).sum() == pytest.approx(20 * 40 * 0.04, rel=0.05)
    h = fk.canvas(100, 100)
    fk.blit(h, src, 30, 40, clip=(30, 40, 50.5, 70))
    assert _alpha(h).sum() == pytest.approx(20.5 * 20, rel=1e-4)    # 端数の縁は被覆率
    assert _alpha(h)[:, 51:].sum() == 0


def test_dots_conserve_alpha_and_move_centroid_monotonically():
    for r in (0.6, 1.0, 2.5, 4.0):
        sums, cxs = [], []
        for k in range(21):
            d = fk.canvas(40, 30)
            fk.dots(d, [(15.3 + 0.1 * k, 14.8)], (255, 255, 255, 255), r)
            sums.append(_alpha(d).sum())
            cxs.append(_centroid(d)[0])
        assert max(sums) == pytest.approx(min(sums), rel=2e-3), r
        steps = np.diff(cxs)
        assert (steps > 0).all(), (r, steps)
        assert cxs[-1] - cxs[0] == pytest.approx(2.0, abs=0.02), r
        assert sums[0] == pytest.approx(math.pi * r * r, rel=1e-3), r     # 円の面積


def test_dots_mass_is_continuous_across_small_and_large_paths():
    """r < 1.5（畳み込み）と r ≥ 1.5（スタンプ）で、半径を連続に変えても明るさが跳ねない"""
    sums = []
    for r in (1.3, 1.45, 1.49, 1.5, 1.51, 1.6):
        d = fk.canvas(30, 30)
        fk.dots(d, [(14.7, 15.2)], (255, 255, 255, 255), r)
        sums.append(_alpha(d).sum() / (math.pi * r * r))
    assert sums == pytest.approx([1.0] * len(sums), rel=1e-3)


def test_dots_many_add_and_per_point_colors():
    d = fk.canvas(200, 50)
    pts = [(20 + 30 * k + 0.37 * k, 25.2) for k in range(6)]
    fk.dots(d, pts, (255, 0, 0, 128), 3)
    one = fk.canvas(200, 50)
    fk.dots(one, pts[:1], (255, 0, 0, 128), 3)
    assert _alpha(d).sum() == pytest.approx(6 * _alpha(one).sum(), rel=2e-3)
    cols = np.array([[255, 0, 0, 255], [0, 0, 255, 255]] * 3)
    e = fk.canvas(200, 50)
    fk.dots(e, pts, cols, 3)
    out = fk.to_rgba8(e)
    assert tuple(out[25, 20]) == (255, 0, 0, 255)
    assert tuple(out[25, 50]) == (0, 0, 255, 255)
    # 重なった点の α は 1 で頭打ち（色は保つ）
    f = fk.canvas(20, 20)
    fk.dots(f, [(10, 10)] * 5, (255, 255, 255, 200), 3)
    assert _alpha(f).max() == pytest.approx(1.0)
    assert tuple(fk.to_rgba8(f)[9, 9]) == (255, 255, 255, 255)
    # 画面外・NaN の点は捨てる
    fk.dots(f, [(-50, -50), (float("nan"), 3)], (255, 255, 255, 255), 2)


def test_polyline_width_is_exact_and_crisp():
    d = fk.canvas(60, 40)
    fk.polyline(d, [(10, 20), (50, 20)], (255, 255, 255, 255), 2, cap="butt")
    a = _alpha(d)
    assert a[19, 20] == pytest.approx(1) and a[20, 20] == pytest.approx(1)
    assert a[18, 20] == 0 and a[21, 20] == 0                    # にじまない
    assert a.sum() == pytest.approx(40 * 2, rel=1e-4)
    e = fk.canvas(60, 40)
    fk.polyline(e, [(10, 20.25), (50, 20.25)], (255, 255, 255, 255), 3, cap="butt")
    col = _alpha(e)[:, 30]
    assert col.sum() == pytest.approx(3.0, abs=1e-4)             # 端数位置でも幅どおり
    assert col[18] == pytest.approx(0.25) and col[21] == pytest.approx(0.75)
    g = fk.canvas(60, 40)
    fk.polyline(g, [(10, 20), (50, 20)], (255, 255, 255, 255), 2, cap="square")
    assert _alpha(g).sum() == pytest.approx(42 * 2, rel=1e-4)
    h = fk.canvas(60, 40)
    fk.polyline(h, [(10, 20), (50, 20)], (255, 255, 255, 255), 2)
    assert _alpha(h).sum() == pytest.approx(40 * 2 + math.pi, rel=0.02)   # 丸い端


def test_polyline_dash_lengths():
    d = fk.canvas(130, 30)
    fk.polyline(d, [(10, 15), (110, 15)], (255, 255, 255, 255), 2,
                dash=(10, 5), cap="butt")
    a = _alpha(d)
    runs = np.flatnonzero(np.diff(np.concatenate([[0], (a[14] > 0.5).astype(int), [0]])))
    lengths = runs[1::2] - runs[::2]
    assert list(lengths) == [10] * 7                       # 0-10, 15-25, … 90-100
    assert a.sum() == pytest.approx(7 * 10 * 2, rel=1e-4)
    # 角をまたいでも弧長で切る（L 字の合計の長さが保たれる）
    e = fk.canvas(130, 130)
    fk.polyline(e, [(10, 10), (70, 10), (70, 70)], (255, 255, 255, 255), 2,
                dash=(8, 4), cap="butt")
    on = sum(min(8, max(0, 120 - 12 * k)) for k in range(11))
    assert _alpha(e).sum() == pytest.approx(on * 2, rel=0.03)
    # dash_offset で模様がずれる
    f = fk.canvas(130, 30)
    fk.polyline(f, [(10, 15), (110, 15)], (255, 255, 255, 255), 2,
                dash=(10, 5), cap="butt", dash_offset=5)
    # SVG と同じ向き: 模様の 5px 先から始まる（最初の線は 5px だけ）
    assert _alpha(f)[14, 10] == pytest.approx(1) and _alpha(f)[14, 17] == 0
    assert _alpha(f)[14, 22] == pytest.approx(1)


def test_rect_circle_hatch_polygon_arrow():
    d = fk.canvas(80, 60)
    fk.rect(d, (10.5, 10, 30.25, 20), (255, 255, 255, 255))
    assert _alpha(d).sum() == pytest.approx(19.75 * 10, rel=1e-5)
    e = fk.canvas(80, 60)
    fk.rect(e, (10, 10, 50, 40), (255, 255, 255, 255), width=2)
    assert _alpha(e).sum() == pytest.approx(42 * 32 - 38 * 28, rel=1e-5)   # 角は直角
    g = fk.canvas(80, 60)
    fk.rect(g, (10, 10, 50, 40), (255, 255, 255, 255), radius=8)
    assert _alpha(g).sum() == pytest.approx(40 * 30 - (4 - math.pi) * 64, rel=0.01)
    h = fk.canvas(80, 80)
    fk.circle(h, (40.3, 39.6), 15, (255, 255, 255, 255))
    assert _alpha(h).sum() == pytest.approx(math.pi * 225, rel=0.005)
    assert _centroid(h) == pytest.approx((40.3, 39.6), abs=0.01)
    k = fk.canvas(80, 80)
    fk.circle(k, (40, 40), 20, (255, 255, 255, 255), width=2)
    assert _alpha(k).sum() == pytest.approx(2 * math.pi * 20 * 2, rel=0.01)
    m = fk.canvas(100, 100)
    fk.hatch(m, (10, 10, 90, 90), (255, 255, 255, 255), spacing=10, width=2)
    assert _alpha(m).sum() == pytest.approx(80 * 80 * 0.2, rel=0.03)
    assert _alpha(m)[:10].sum() == 0 and _alpha(m)[90:].sum() == 0
    n = fk.canvas(100, 100)
    fk.polygon(n, [(10, 10), (90, 10), (10, 90)], (255, 255, 255, 255))
    assert _alpha(n).sum() == pytest.approx(80 * 80 / 2, rel=0.01)
    hole = fk.canvas(100, 100)
    fk.polygon(hole, [[(10, 10), (90, 10), (90, 90), (10, 90)],
                      [(30, 30), (70, 30), (70, 70), (30, 70)]], (255, 255, 255, 255))
    assert _alpha(hole).sum() == pytest.approx(80 * 80 - 40 * 40, rel=1e-3)
    p = fk.canvas(120, 60)
    fk.arrow(p, (10, 30), (100, 30), (255, 255, 255, 128), 3, head=16)
    a = _alpha(p)
    assert a.max() == pytest.approx(128 / 255, abs=1e-3)    # 軸と矢じりの重なりも二重にならない
    assert a[:, 101:].sum() < 0.5                            # 先端から突き出さない
    q = fk.canvas(120, 80)
    fk.arrow(q, (10, 60), (100, 60), (255, 255, 255, 255), 3, head=14, curve=0.3)
    assert _alpha(q)[:55].sum() > 50 and _alpha(q)[66:].sum() == 0   # 左（上）へ膨らむ


def test_diagonal_edges_keep_alpha_mass():
    """縁の被覆率は箱フィルタの面積（版1の clip(0.5-d) は 45° で α の和が 4% 脈打ち、
    40° の静止した線は列ごとに 9% のむらがあった）"""
    sums = []
    for k in range(41):
        o = 0.05 * k
        d = fk.canvas(300, 300)
        fk.polyline(d, [(50 + o, 250), (250 + o, 50)], (255, 255, 255, 255), 2)
        sums.append(_alpha(d).sum())
    want = 200 * math.sqrt(2) * 2 + math.pi
    assert (max(sums) - min(sums)) / want < 1e-3
    assert np.mean(sums) == pytest.approx(want, rel=1e-3)
    d = fk.canvas(400, 400)
    th = math.radians(40)
    fk.polyline(d, [(50, 350), (50 + 300 * math.cos(th), 350 - 300 * math.sin(th))],
                (255, 255, 255, 255), 2, cap="butt")
    cols = _alpha(d).sum(axis=0)[80:250]
    assert (cols.max() - cols.min()) / cols.mean() < 5e-3          # ロープ状のむらが無い
    # 長い斜め線（小片の切れ目・束の境目をまたぐ）を動かしても量が一定
    sums = []
    for k in range(11):
        e = fk.canvas(1000, 600)
        fk.polyline(e, [(50 + 0.1 * k, 550), (950 + 0.1 * k, 60)], (255, 255, 255, 255), 3)
        sums.append(_alpha(e).sum())
    want = math.hypot(900, 490) * 3 + math.pi * 2.25
    assert max(abs(s / want - 1) for s in sums) < 2e-4
    # 小片の切れ目で隣の線の外へはみ出さない（軸に平行な線は外側の行が厳密に 0）
    g = fk.canvas(400, 40)
    fk.polyline(g, [(10, 20), (390, 20)], (255, 255, 255, 255), 2, cap="butt")
    assert _alpha(g).sum() == pytest.approx(380 * 2, abs=1e-3)
    assert _alpha(g)[18].max() == 0 and _alpha(g)[21].max() == 0


def test_circles_and_polygons_are_accurate():
    rs = np.random.RandomState(1)
    for r in (2.0, 3.0, 10.0):
        errs = []
        for _ in range(12):
            c = (50 + rs.rand(), 50 + rs.rand())
            d = fk.canvas(100, 100)
            fk.circle(d, c, r, (255, 255, 255, 255))
            errs.append(_alpha(d).sum() / (math.pi * r * r) - 1)
            e = fk.canvas(100, 100)
            fk.circle(e, c, r + 1, (255, 255, 255, 255), width=2)
            errs.append(_alpha(e).sum() / (math.pi * ((r + 2) ** 2 - r ** 2)) - 1)
        assert abs(np.mean(errs)) < 2e-3, r          # 円弧のずれを直す（版1は r=2 で +2%）
        assert max(errs) - min(errs) < 6e-3, r
    # 多角形: 縁の外の画素を内側と取り違えない（cv2.fillPoly は縁の画素まで塗る）
    sums = []
    for k in range(11):
        d = fk.canvas(200, 200)
        fk.polygon(d, [(40 + 0.1 * k, 160), (160 + 0.1 * k, 40), (170 + 0.1 * k, 170)],
                   (255, 255, 255, 255))
        sums.append(_alpha(d).sum())
    assert max(abs(s - 8400) for s in sums) < 1.0
    hole = fk.canvas(200, 200)
    fk.polygon(hole, [[(10, 10), (190, 10), (190, 190), (10, 190)],
                      [(60.3, 60.7), (140.2, 60.7), (140.2, 140.1), (60.3, 140.1)]],
               (255, 255, 255, 255))
    assert _alpha(hole).sum() == pytest.approx(180 * 180 - 79.9 * 79.4, abs=0.5)


def test_polygon_cost_follows_edge_length():
    """距離は辺の小片ごとに近くの画素だけで計算する（版1は 縁の画素 × 辺の数 で、
    頂点 1,000 の星形に 17 秒かかった）"""
    import time
    nv = 1000
    ang = np.linspace(0, 2 * np.pi, nv, endpoint=False)
    rr = np.where(np.arange(nv) % 2 == 0, 400.0, 250.0)
    pts = np.stack([960 + rr * np.cos(ang), 540 + rr * np.sin(ang)], 1)
    d = fk.canvas(1920, 1080)
    t0 = time.perf_counter()
    fk.polygon(d, pts, (255, 255, 255, 255))
    assert time.perf_counter() - t0 < 4.0
    a = _alpha(d)
    assert a[540, 960] == 1 and a[540, 1500] == 0 and 0 < a.sum() < math.pi * 400 ** 2


def test_zero_length_dashes_and_duplicate_points():
    # dash=(0, 隙間) は丸い端なら点（SVG の点線の書き方）
    d = fk.canvas(130, 30)
    fk.polyline(d, [(10, 15), (110, 15)], (255, 255, 255, 255), 4, dash=(0, 8))
    assert _alpha(d).sum() == pytest.approx(13 * math.pi * 4, rel=0.01)      # 0, 8, …, 96
    ys, xs = np.nonzero(_alpha(d) > 0.99)
    assert set(np.unique(xs // 8)) >= {1, 2, 12}
    sq = fk.canvas(130, 30)
    fk.polyline(sq, [(10, 15), (110, 15)], (255, 255, 255, 255), 4, dash=(0, 8), cap="square")
    assert _alpha(sq).sum() == pytest.approx(13 * 16, rel=1e-3)               # 一辺 4 の正方形
    bt = fk.canvas(130, 30)
    fk.polyline(bt, [(10, 15), (110, 15)], (255, 255, 255, 255), 4, dash=(0, 8), cap="butt")
    assert _alpha(bt).sum() == 0                                              # butt は描かない
    # butt の端は、重なった点の後の「長さのある」線分に掛ける（丸い塊が出ない）
    for pts in ([(10, 15), (10, 15), (110, 15)], [(10, 15), (110, 15), (110, 15)]):
        e = fk.canvas(130, 30)
        fk.polyline(e, pts, (255, 255, 255, 255), 4, cap="butt")
        assert _alpha(e)[:, :10].sum() == 0 and _alpha(e)[:, 110:].sum() == 0
        assert _alpha(e).sum() == pytest.approx(400, abs=1e-3)
    pt = fk.canvas(30, 30)
    fk.polyline(pt, [(15, 15), (15, 15)], (255, 255, 255, 255), 6)
    assert _alpha(pt).sum() == pytest.approx(math.pi * 9, rel=5e-3)          # 全部同じ点は点


def test_blit_downscale_is_continuous():
    """縮小は倍率に依らず INTER_AREA で縮めてから置く（版1は 0.5 倍の前後で補間が切り替わり、
    縁の量が1歩で 5% 落ちた）"""
    font = _jp_font()
    s = fk.label("t", "縮小の連続性 ABC", font=font, size=48, border=3)

    def edge(sc):
        d = fk.canvas(800, 200)
        fk.blit(d, s, 40.3, 50.2, scale=sc)
        a = _alpha(d)
        return ((np.abs(np.diff(a, axis=1)).sum() + np.abs(np.diff(a, axis=0)).sum())
                / a.sum() * sc), a.sum() / sc ** 2
    vals = [edge(x) for x in np.arange(0.70, 0.299, -0.005)]
    e = np.array([v[0] for v in vals])
    assert (np.abs(np.diff(e)) / e[:-1]).max() < 0.03
    m = np.array([v[1] for v in vals])
    assert (m.max() - m.min()) / m.mean() < 5e-3                 # α の量は倍率² に比例
    # 寸法が変わらないほど 1 に近い倍率では、縮めずに置くのと同じ
    near = fk.canvas(800, 200)
    fk.blit(near, s, 40.3, 50.2, scale=1 - 0.4 / s.w)
    assert _alpha(near).sum() == pytest.approx(s.premul[..., 3].sum() * (1 - 0.4 / s.w) ** 2,
                                               rel=2e-3)


def test_draw_errors():
    d = fk.canvas(10, 10)
    nan = float("nan")
    for f, msg in (
            (lambda: fk.hatch(d, (nan, 0, 10, 10), "fg"), "hatch: box"),
            (lambda: fk.hatch(d, (0, 0, 10), "fg"), "hatch: box"),
            (lambda: fk.circle(d, (nan, 3), 4, "fg"), "circle: center"),
            (lambda: fk.arrow(d, (nan, 3), (8, 8), "fg", 2), "arrow: p0"),
            (lambda: fk.arrow(d, (1, 3), (8, float("inf")), "fg", 2), "arrow: p1"),
            (lambda: fk.blit(d, np.full((4, 4, 4), 255, np.uint8), 2.5, 2,
                             clip=(nan, 0, 5, 5)), "blit: clip"),
            (lambda: fk.blit(d, np.full((4, 4, 4), 255, np.uint8), 2.5, 2,
                             pivot=(nan, 0)), "blit: pivot"),
            (lambda: fk.rect(d, (0, 0, nan, 5), "fg"), "rect: box")):
        with pytest.raises(ValueError, match=msg):
            f()
    with pytest.raises(TypeError, match="canvas"):
        fk.polyline(np.zeros((10, 10, 4), np.uint8), [(0, 0), (5, 5)], "fg", 2)
    with pytest.raises(ValueError, match="cap"):
        fk.polyline(d, [(0, 0), (5, 5)], "fg", 2, cap="flat")
    with pytest.raises(ValueError, match="dash"):
        fk.polyline(d, [(0, 0), (5, 5)], "fg", 2, dash=(0, 0))
    with pytest.raises(ValueError, match="NaN"):
        fk.polyline(d, [(0, 0), (float("nan"), 5)], "fg", 2)
    with pytest.raises(ValueError, match="もしかして"):
        fk.rect(d, (0, 0, 5, 5), "acent")
    with pytest.raises(ValueError, match="dash"):
        fk.rect(d, (0, 0, 5, 5), "fg", dash=(2, 2))
    with pytest.raises(ValueError, match="書き込み不可"):
        cache = fk.layer_cache()
        fk.rect(cache(1, lambda: fk.canvas(10, 10)), (0, 0, 5, 5), "fg")
    with pytest.raises(ValueError):
        fk.canvas(0, 10)


@pytest.mark.parametrize("scale,angle,pivot", [
    (1.0, 17.0, None), (0.43, 0.0, None), (0.73, 200.0, (3.5, 8.0)), (1.6, 90.0, None)])
def test_warp_patch_is_the_blit_kernel(scale, angle, pivot):
    """blit の端数位置・回転・縮小の経路は warp_patch と同じ画素（text_transition の字も同じ核）。

    チャンネル数に依らない（text_transition は縁取りと塗りの2ch を1回で写す）。
    """
    rng = np.random.default_rng(3)
    src = rng.random((21, 33, 4)).astype(np.float32)
    src[..., :3] *= src[..., 3:4]                         # 事前乗算
    x, y = 40.3, 34.75
    d = fk.canvas(128, 112)
    fk.blit(d, src, x, y, scale=scale, angle=angle, pivot=pivot)
    x0, y0, patch = fk.warp_patch(src, x, y, scale=scale, angle=angle, pivot=pivot)
    assert (x0, y0, x0 + patch.shape[1], y0 + patch.shape[0]) == fk.affine_box(
        33, 21, x, y, scale=scale, angle=angle, pivot=pivot)
    want = np.zeros_like(d)
    want[y0:y0 + patch.shape[0], x0:x0 + patch.shape[1]] = patch   # 枠の中に収まる位置
    assert np.array_equal(d, want)
    _x, _y, two = fk.warp_patch(np.ascontiguousarray(src[..., 2:4]), x, y, scale=scale,
                                angle=angle, pivot=pivot)
    # opencv 4.13 では画素まで同じ。5.0 は warpAffine の 2ch と 4ch で補間の経路が違い、
    # 縁の画素が最大 0.018 ずれる（量の和は 1e-4 以内）。位置と量が同じことだけを見る
    assert two.shape == patch[..., 2:4].shape
    assert float(np.abs(two - patch[..., 2:4]).max()) <= 0.03
    assert float(two.sum()) == pytest.approx(float(patch[..., 2:4].sum()), rel=1e-3)


def test_figure_modules_do_not_use_framekit_internals():
    """図のモジュール（fx_*.py）は framekit の私有関数（_ で始まる名前）を使わない。

    共有したい処理は framekit の公開関数にする（warp_patch のように）。私有関数に頼ると、
    framekit の書き換えで図が黙って壊れる。
    """
    import re
    pkg = os.path.dirname(fk.__file__)
    used = {}
    for path in sorted(glob.glob(os.path.join(pkg, "fx_*.py"))):
        with open(path, encoding="utf-8") as f:
            src = f.read()
        hits = sorted(set(re.findall(r"\b(?:fk|framekit)\._\w+", src)))
        if hits:
            used[os.path.basename(path)] = hits
    assert not used, f"framekit の私有関数を使っています: {used}"


def test_layer_cache_redraws_only_on_new_state():
    calls = []
    cache = fk.layer_cache()

    def draw():
        calls.append(1)
        d = fk.canvas(8, 8)
        fk.rect(d, (0, 0, 4, 4), "fg")
        return d
    a = cache("v1", draw)
    b = cache("v1", draw)
    assert a is b and len(calls) == 1 and not a.flags.writeable
    cache("v2", draw)
    assert len(calls) == 2
    dst = fk.canvas(8, 8)
    fk.over(dst, a)
    assert _alpha(dst).sum() == pytest.approx(16)


# --- build ---------------------------------------------------------------------------

def _draw_bar(i):
    d = fk.canvas(31, 17)
    fk.rect(d, (0, 0, 4 + 3 * i, 17), (255, 255, 255, 255))
    return d


def _key_of(**kw):
    p = sv.Project()
    p.configure(width=32, height=18, fps=30)
    p._dry_run = True
    base = dict(kind="figtest", ver=1, params={"n": 3}, draw=_draw_bar, n_frames=4,
                size=(31, 17))
    base.update(kw)
    obj = fk.build("figtest", **base)
    return obj.source, p._pending_compute_cmds[obj.source]


def test_build_key_follows_params_ver_files_only(tmp_path):
    """フォントに依らない部分（どの環境でも走る）"""
    src, cmd = _key_of()
    assert "frames" in src.replace("\\", "/") and src.endswith(".mov")
    assert cmd[cmd.index("-s") + 1] == "32x18"                  # 偶数へ切り上げる
    assert cmd[cmd.index("-frames:v") + 1] == "4"
    assert _key_of(params={"n": 4})[0] != src
    assert _key_of(params={"n": 3.0000001})[0] == _key_of(params={"n": 3.0})[0]  # norm で丸める
    assert _key_of(params={"n": 3.0})[0] == src                 # 3 と 3.0 は同じ鍵
    assert _key_of(ver=2)[0] != src
    assert _key_of(kind="other")[0] != src
    assert _key_of(n_frames=5)[0] != src
    # 切り上げる前の寸法も鍵に入る（31x17 と 32x18 は同じ寸法の動画でも中身が違う）
    full = _key_of(size=(32, 18), draw=lambda i: fk.canvas(32, 18))[0]
    assert full != src
    # 渡していない値では変わらない
    assert _key_of(draw=lambda i: _draw_bar(i))[0] == src
    assert _key_of(info={"x": 1}, text={"size_min": 9})[0] == src
    # files: 内容指紋（渡す順には依らない）
    data = tmp_path / "d.json"
    data.write_text("[1]", encoding="utf-8")
    other = tmp_path / "e.json"
    other.write_text("[3]", encoding="utf-8")
    k1 = _key_of(files=[str(data)])[0]
    assert k1 != src
    assert (_key_of(files=[str(data), str(other)])[0]
            == _key_of(files=[str(other), str(data)])[0])
    data.write_text("[22]", encoding="utf-8")       # 大きさも変える（指紋のメモは大きさと mtime で引く）
    assert _key_of(files=[str(data)])[0] != k1


def test_build_key_fonts_are_content_addressed(tmp_path):
    font = _latin_font()
    src = _key_of()[0]
    with_font = _key_of(fonts=[fk.font_ref("t", font)])[0]
    assert with_font != src
    copy = tmp_path / "f.ttf"
    shutil.copyfile(font, copy)
    assert _key_of(fonts=[fk.font_ref("t", str(copy))])[0] == with_font


def test_build_key_variable_font_weight():
    vf = _variable_font()
    assert (_key_of(fonts=[fk.font_ref("t", vf, weight=700)])[0]
            != _key_of(fonts=[fk.font_ref("t", vf, weight=400)])[0])


def test_build_key_follows_sprite_contents():
    """fonts= に渡した Sprite は、字形の指紋だけでなく描いた文字・書式も鍵に入る
    （文字を直したのに古い .mov に命中し続けない）"""
    font = _jp_font()

    def key_for(text, **fmt):
        s = fk.label("t", text, font=font, size=40, **fmt)
        return _key_of(fonts=[s], draw=lambda i, s=s: fk.blit(fk.canvas(31, 17), s, 0, 0))[0]
    a = key_for("後戻り")
    assert key_for("前進せよ") != a
    assert key_for("後戻り") == a                         # 同じ文字・同じ書式は同じ鍵
    assert key_for("後戻り", border=3) != a              # 書式も効く
    assert key_for("後戻り", color="#e0241b") != a
    s1 = fk.label("t", "甲", font=font, size=40)
    s2 = fk.label("t", "乙", font=font, size=40)
    assert _key_of(fonts=[s1, s2])[0] == _key_of(fonts=[s2, s1])[0]   # 渡す順には依らない
    assert s1.sig != s2.sig and len(s1.sig) == 16


def test_build_errors(tmp_path):
    with pytest.raises(TypeError, match="draw"):
        fk.build("x", kind="k", ver=1, params={}, draw=None, n_frames=2, size=(8, 8))
    with pytest.raises(ValueError, match="n_frames"):
        fk.build("x", kind="k", ver=1, params={}, draw=_draw_bar, n_frames=0, size=(8, 8))
    with pytest.raises(ValueError, match="size"):
        fk.build("x", kind="k", ver=1, params={}, draw=_draw_bar, n_frames=1, size=(0, 8))
    with pytest.raises(FileNotFoundError, match="見つかりません"):
        fk.build("x", kind="k", ver=1, params={}, draw=_draw_bar, n_frames=1, size=(8, 8),
                 files=[str(tmp_path / "nope.json")])
    with pytest.raises(TypeError, match="関数は鍵にできない"):
        fk.build("x", kind="k", ver=1, params={"f": print}, draw=_draw_bar, n_frames=1,
                 size=(8, 8))
    with pytest.raises(TypeError, match="fonts"):
        fk.build("x", kind="k", ver=1, params={}, draw=_draw_bar, n_frames=1, size=(8, 8),
                 fonts=["C:/Windows/Fonts/arial.ttf"])


def test_draw_frame_pads_to_even_size_and_attrs():
    p = sv.Project()
    p.configure(width=32, height=18, fps=30)
    p._dry_run = True
    obj = fk.build("figtest", kind="figtest", ver=1, params={}, draw=_draw_bar, n_frames=4,
                   size=(31, 17), info={"marks": [0.5]}, text={"size_min": 30})
    img = fk.draw_frame(obj, 2)
    assert img.dtype == np.uint8 and img.shape == (18, 32, 4)
    assert img[:17, :10, 3].min() == 255 and img[17].max() == 0 and img[:, 31].max() == 0
    # audit の表示名の先頭に図の種類を添える（渡した dict は書き換えない）
    text = {"size_min": 30, "content": "見出し"}
    obj = fk.build("figtest", kind="figtest", ver=1, params={}, draw=_draw_bar, n_frames=4,
                   size=(31, 17), info={"marks": [0.5]}, text=text)
    assert obj.figure.marks == [0.5]
    assert obj._text_image == {"size_min": 30, "content": "figtest: 見出し"}
    assert text == {"size_min": 30, "content": "見出し"}
    named = fk.build("figtest", kind="figtest", ver=1, params={}, draw=_draw_bar, n_frames=4,
                     size=(31, 17), text={"content": "figtest(a)"})
    assert named._text_image["content"] == "figtest(a)"        # すでに kind で始まるならそのまま
    assert obj.length() == pytest.approx(4 / 30)
    bad = fk.build("figtest", kind="figtest", ver=2, params={},
                   draw=lambda i: fk.canvas(10, 10), n_frames=1, size=(31, 17))
    with pytest.raises(ValueError, match="寸法"):
        fk.draw_frame(bad, 0)
    with pytest.raises(TypeError):
        fk.draw_frame(sv.Object("x.png"), 0)
    with pytest.raises(ValueError, match="0〜3"):            # コマ数の外
        fk.draw_frame(obj, 4)
    with pytest.raises(ValueError, match="0 以上"):
        fk.draw_frame(obj, -1)


_LAYER = '''
import sys
sys.path.insert(0, {dir!r})
import {mod} as counter
import scriptvedit.framekit as fk

def compute():
    counter.calls.append(1)
    return {{"n": 3}}

plan = fk.memo("test_framekit", ["memo", {tag!r}], compute)

def draw(i):
    if counter.forbid_draw:
        raise AssertionError("dry_run で draw が呼ばれた")
    d = fk.canvas(32, 16)
    fk.rect(d, (0, 0, 4 + 2 * i, 16), (255, 255, 255, 255))
    fk.dots(d, [(20.5 + 0.3 * i, 8.0)], (224, 36, 27, 255), 2.5)
    return d

obj = fk.build("figtest", kind="figtest", ver=1, params={{"n": plan["n"]}}, draw=draw,
               n_frames=6, size=(32, 16))
obj.time(0.5)
'''


def _layer_project(tmp_path, *, forbid_draw):
    mod = f"fk_counter_{uuid.uuid4().hex[:8]}"
    (tmp_path / f"{mod}.py").write_text("calls = []\nforbid_draw = False\n", encoding="utf-8")
    layer = tmp_path / "fig_layer.py"
    layer.write_text("from scriptvedit import *\n" + _LAYER.format(
        dir=str(tmp_path), mod=mod, tag=str(tmp_path)), encoding="utf-8")
    import importlib
    sys.path.insert(0, str(tmp_path))
    counter = importlib.import_module(mod)
    counter.forbid_draw = forbid_draw

    def make():
        p = sv.Project()
        p.configure(width=32, height=16, fps=30, background_color="black")
        p.layer(str(layer), priority=1)
        return p
    return make, counter


def test_dry_run_does_not_call_draw_and_memo_runs_once(tmp_path):
    make, counter = _layer_project(tmp_path, forbid_draw=True)
    r = make().render(str(tmp_path / "o.mp4"), dry_run=True)
    (path, cmd), = r["cache"].items()
    assert "frames" in path.replace("\\", "/") and not os.path.exists(path)
    assert counter.calls == [1]                  # Plan と Render の2回の exec で1回だけ
    make().render(str(tmp_path / "o.mp4"), dry_run=True)
    assert counter.calls == [1]


def test_dry_run_is_same_cold_and_warm(tmp_path):
    _need_ffmpeg()
    make, counter = _layer_project(tmp_path, forbid_draw=False)
    out = str(tmp_path / "out.mp4")
    cold = make().render(out, dry_run=True)
    make().render(out, timeout=300)
    for path in cold["cache"]:
        assert os.path.getsize(path) > 0, path
    warm = make().render(out, dry_run=True)
    assert json.dumps(cold, sort_keys=True) == json.dumps(warm, sort_keys=True)


def test_build_real_frames_match_draw_frame(tmp_path):
    _need_ffmpeg()
    import subprocess
    obj = fk.build("figtest", kind="figtest", ver=1, params={"k": "real"}, draw=_draw_bar,
                   n_frames=4, size=(31, 17), fps=30)
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", obj.source,
         "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        check=True, capture_output=True, timeout=120).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, 18, 32, 4)
    assert len(frames) == 4
    for i in range(4):
        assert np.array_equal(frames[i], fk.draw_frame(obj, i)), i


def test_build_keeps_generated_mov_in_layer_deps(tmp_path):
    """フォント・files を渡した図でも、生成物の .mov 自身がレイヤーの依存に残る
    （params が環境変数や import したデータから来ても、cache='auto' が古い図を再生しない）"""
    font = _latin_font()
    data = tmp_path / "d.json"
    data.write_text("[1]", encoding="utf-8")
    p = sv.Project()
    p.configure(width=32, height=18, fps=30)
    p._dry_run = True
    obj = fk.build("figtest", kind="figtest", ver=1, params={"n": 3}, draw=_draw_bar,
                   n_frames=2, size=(31, 17), fonts=[fk.font_ref("t", font)], files=[str(data)])
    assert obj._origin_sources[0] == obj.source
    assert str(data) in obj._origin_sources


_ENV_LAYER = '''
import os
import scriptvedit.framekit as fk
n = int(os.environ["FK_TEST_BARS"])

def draw(i):
    d = fk.canvas(80, 40)
    for k in range(n):
        fk.rect(d, (6 + 12 * k, 8, 14 + 12 * k, 32), (255, 255, 255, 255))
    return d

fig = fk.build("figtest", kind="figtest_env", ver=1, params={{"n": n}}, draw=draw,
               n_frames=3, size=(80, 40), fonts=[fk.font_ref("t", {font!r})])
fig.time(0.2)
'''


def test_layer_cache_auto_follows_params_from_outside(tmp_path, monkeypatch):
    """レビューの再現: レイヤーの外（環境変数）から来る params を変えたら、cache='auto' の
    レイヤーキャッシュは作り直す（版1は .mov が依存から消えて古い図を再生した）"""
    _need_ffmpeg()
    import subprocess
    font = _latin_font()
    layer = tmp_path / "env_fig.py"
    layer.write_text(_ENV_LAYER.format(font=font), encoding="utf-8")

    def render(n, cache):
        monkeypatch.setenv("FK_TEST_BARS", str(n))
        p = sv.Project()
        p.configure(width=80, height=40, fps=30, background_color="black")
        p.layer(str(layer), priority=1, cache=cache)
        out = str(tmp_path / f"o_{n}_{cache}.mp4")
        p.render(out, timeout=300)
        raw = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "0.05", "-i", out,
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            check=True, capture_output=True, timeout=120).stdout
        row = (np.frombuffer(raw, np.uint8).reshape(40, 80)[20] > 128).astype(int)
        return int((np.diff(row) == 1).sum() + row[0])
    assert render(3, "make") == 3
    assert render(3, "auto") == 3
    assert render(5, "auto") == 5


def test_golden_update_refuses_same_version_changes(tmp_path, monkeypatch):
    """--golden-update でも、版を上げずに絵だけ変えた金型は書き換えない"""
    import framekit_golden as fg
    monkeypatch.setattr(fg, "GOLDEN_DIR", str(tmp_path / "golden"))
    monkeypatch.setattr(fg, "_FAIL_DIR", str(tmp_path / "fail"))

    class _Req:
        def __init__(self, update):
            self.config = type("C", (), {"getoption": staticmethod(lambda name: update)})()
    a = np.zeros((10, 10, 4), np.uint8)
    a[2:8, 2:8] = 255
    b = a.copy()
    b[2:8, 2:4] = 0                                    # 描き方の変化（閾値を超える）
    png = tmp_path / "golden" / "k" / "n.png"
    with pytest.raises(pytest.fail.Exception, match="金型がありません"):
        fg.assert_golden(_Req(False), "k", "n", a, ver=1)
    fg.assert_golden(_Req(True), "k", "n", a, ver=1)            # 無ければ作る
    assert png.exists()
    fg.assert_golden(_Req(False), "k", "n", a, ver=1)
    with pytest.raises(pytest.fail.Exception, match="版定数を上げて"):
        fg.assert_golden(_Req(False), "k", "n", b, ver=1)
    with pytest.raises(pytest.fail.Exception, match="書き換えません"):
        fg.assert_golden(_Req(True), "k", "n", b, ver=1)         # 版がそのままなら拒む
    with Image.open(png) as im:
        assert np.array_equal(np.asarray(im), a)
    fg.assert_golden(_Req(True), "k", "n", b, ver=2)             # 版を上げれば作り直す
    fg.assert_golden(_Req(False), "k", "n", b, ver=2)
    with pytest.raises(pytest.fail.Exception, match="版"):
        fg.assert_golden(_Req(False), "k", "n", b, ver=3)
    with pytest.raises(pytest.skip.Exception, match="フォント"):
        fg.assert_golden(_Req(False), "k", "n", b, ver=2, font_ffp="other")


# --- 金型 -------------------------------------------------------------------------------

_W, _H = 320, 180


def _golden_shapes():
    pal = fk.palette("golden")
    d = fk.canvas(_W, _H)
    fk.rect(d, (8, 8, 150, 100), pal["panel"], radius=12)
    fk.rect(d, (8, 8, 150, 100), pal["line"], width=2, radius=12)
    fk.hatch(d, (20.5, 60.25, 90, 92), pal["dim"], spacing=7, width=2)
    fk.rect(d, (20.5, 60.25, 90, 92), pal["muted"], width=1.5)
    fk.rect(d, (100.3, 20.6, 140.1, 50.2), pal["accent"])
    fk.circle(d, (230.4, 60.3), 40, pal["line"], width=2)
    fk.circle(d, (230.4, 60.3), 24.5, pal["accent"])
    fk.circle(d, (230.4, 60.3), 48, pal["muted"], width=2, dash=(6, 5))
    fk.polygon(d, [(20, 120), (120, 130), (90, 170), (15, 165)], pal["fg"])
    fk.polygon(d, [[(170, 115), (300, 115), (300, 172), (170, 172)],
                   [(200, 130), (270, 130), (270, 160), (200, 160)]], pal["dim"])
    return fk.to_rgba8(d)


def _golden_strokes():
    pal = fk.palette("golden")
    d = fk.canvas(_W, _H)
    fk.polyline(d, [(10, 12), (310, 12)], pal["fg"], 2)
    fk.polyline(d, [(10, 24.5), (310, 30.5)], pal["line"], 3, dash=(14, 7))
    fk.polyline(d, [(10, 45), (60, 70), (110, 45), (160, 70)], pal["accent"], 4, cap="butt")
    fk.polyline(d, [(180, 45), (240, 75), (300, 45)], pal["muted"], 3, cap="square",
                dash=(10, 6), dash_offset=3)
    fk.arrow(d, (20, 100), (300, 100), pal["fg"], 3, head=14)
    fk.arrow(d, (20, 170), (300, 130), pal["accent"], 3, head=16, curve=0.2)
    fk.arrow(d, (40, 120), (160, 175), pal["line"], 2, head=10, curve=-0.3, dash=(6, 4))
    return fk.to_rgba8(d)


def _golden_dots():
    d = fk.canvas(_W, _H)
    rs = np.random.RandomState(7)
    pts = rs.rand(600, 2) * [150, 160] + [5, 10]
    fk.dots(d, pts, (255, 255, 255, 200), 1.0)
    fk.dots(d, [(170 + 14 * k + 0.13 * k, 20.4) for k in range(10)], "accent", 2.5)
    fk.dots(d, [(175 + 28 * k + 0.31 * k, 60.7) for k in range(5)], "fg", 6, soft=1.2)
    cols = np.array([[224, 36, 27, 255], [200, 204, 210, 255], [154, 160, 168, 160]] * 10)
    pts2 = np.array([(170 + 4.9 * k, 120 + 30 * math.sin(k / 4)) for k in range(30)])
    fk.dots(d, pts2, cols, 1.8)
    return fk.to_rgba8(d)


def _golden_blit():
    # フォントを使わない合成の絵（環境で変わらない）
    yy, xx = np.mgrid[0:40, 0:64]
    src = np.zeros((40, 64, 4), np.uint8)
    src[..., 0] = (xx * 4).clip(0, 255)
    src[..., 1] = (yy * 6).clip(0, 255)
    src[..., 2] = 200
    src[..., 3] = np.where(((xx - 32) / 30.0) ** 2 + ((yy - 20) / 18.0) ** 2 < 1, 255, 0)
    d = fk.canvas(_W, _H)
    fk.blit(d, src, 8, 8)
    fk.blit(d, src, 80.25, 8.5)
    fk.blit(d, src, 160, 10, angle=30)
    fk.blit(d, src, 240, 10, scale=1.4, alpha=0.6)
    fk.blit(d, src, 20, 90, scale=0.3)
    fk.blit(d, src, 90, 100, clip=(90, 110, 154, 125.5))
    fk.blit(d, src, 180, 110, angle=-75, scale=0.8, pivot=(0, 0))
    return fk.to_rgba8(d)


def _golden_edges():
    """版2で変えたところ（斜めの縁・小さい円・点線の点・星形・縮小）"""
    pal = fk.palette("golden")
    d = fk.canvas(_W, _H)
    for k, deg in enumerate((10, 30, 45, 60, 80)):
        th = math.radians(deg)
        x0 = 12 + 26 * k
        fk.polyline(d, [(x0, 90), (x0 + 60 * math.cos(th) * 0.4, 90 - 60 * math.sin(th))],
                    pal["fg"], 2.5)
    fk.polyline(d, [(10, 110.5), (150, 110.5)], pal["line"], 3, dash=(0, 9))
    fk.polyline(d, [(10, 125), (150, 140)], pal["muted"], 3, dash=(0, 8), cap="square")
    for k, r in enumerate((1.5, 2.0, 3.0, 4.5)):
        fk.circle(d, (20.3 + 22 * k, 160.6), r, pal["accent"])
    ang = np.linspace(0, 2 * np.pi, 11, endpoint=False) - np.pi / 2
    rr = np.where(np.arange(11) % 2 == 0, 70.0, 30.0)
    fk.polygon(d, np.stack([235.4 + rr * np.cos(ang), 85.7 + rr * np.sin(ang)], 1), pal["dim"])
    fk.polygon(d, [(235.4 + 70 * math.cos(2 * math.pi * k * 2 / 5 - math.pi / 2),
                    85.7 + 70 * math.sin(2 * math.pi * k * 2 / 5 - math.pi / 2)) for k in range(5)],
               pal["accent"])
    fk.hatch(d, (160, 150, 310, 175), pal["line"], spacing=6, angle=30, width=2.5)
    return fk.to_rgba8(d)


@pytest.mark.parametrize("name,make", [
    ("shapes", _golden_shapes), ("strokes", _golden_strokes),
    ("dots", _golden_dots), ("blit", _golden_blit), ("edges", _golden_edges)])
def test_golden_primitives(request, name, make):
    # フォントを使わない図形は環境で変わらない（float の丸めの差は 1〜2 段）ので閾値を締める。
    # 既定（平均 1.0）のままだと、dots の soft を 0.8 → 1.0 にした変化（平均 0.32）が通ってしまう
    assert_golden(request, "framekit", name, make(), ver=fk._FRAMEKIT_VER,
                  tol_mean=0.1, tol_max=8)


def test_golden_label(request):
    font = _jp_font()
    pal = fk.palette("golden")
    # 日本語フォントの多くは「\」を「¥」で描くので、金型の文字には使わない
    s1 = fk.label("golden", "正規表現 .*=.*", font=font, size=34, border=3, color=pal["fg"])
    s2 = fk.label("golden", "{accent|2万}個の空白", markup=True,
                  styles={"accent": {"color": "#e0241b"}}, font=font, size=26, border=2)
    d = fk.canvas(_W, _H)
    fk.blit(d, s1, 10, 10)
    fk.blit(d, s2, 12.5, 80.25)
    fk.blit(d, s2, 150, 115, angle=-15, scale=0.8)
    assert_golden(request, "framekit", "label", fk.to_rgba8(d), ver=fk._FRAMEKIT_VER,
                  font_ffp=s1.fonts[0].ffp)
