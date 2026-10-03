# -*- coding: utf-8 -*-
"""fly_to（粒子の輸送モーフ。絵 A の粒が飛んで、離れた所の絵 B になる）の回帰テスト。

  (a) 生成（morph_flight.py）: 最初のコマ = A・最後のコマ = offset の位置の B（画素一致）、
      A と B が同じ色なら全コマの全画素がその色（α が2回掛からない）、粒を分けて描いても
      同じ絵でメモリが頭打ち、粒の数・α の分け合い、スライスした最適輸送の移動量、
      粒がキャンバスの内側、キャンバスが A の中心に対して対称、B の左上の丸めが静止画と同じ、
      seed で決定的。
  (b) DSL（effects/terminal.py・checkpoint.py・cache.py）: 鍵、構築時・計画時のエラー、
      anchor='topleft' の overlay の式（dry_run）。
  (c) 実レンダ: delay / duration の前後で A / B を保持、anchor='topleft' で A の位置が
      静止画と同じ、dry_run のコマンドが実レンダの前後で同じ、最後のコマの B が静止画の B と
      画素一致（ずれが偶数のとき）。
  (d) describe の既定値・choices が実装と同じ。
  (e) 金型 3 枚（tests/golden/flyto/。描画を変えたら cache._FLIGHT_VER を上げて作り直す）と、
      その判定が粒の入れ替わりを通し描き方の変更を落とすこと。
"""
import math
import os
import re
import shutil
import subprocess
import warnings

import pytest

import scriptvedit as sv
from scriptvedit import cache as cache_mod
from scriptvedit.context import _exec_stack, activate, current_project
from scriptvedit.manifest import describe
from scriptvedit.state import _FLY_COLOR_PATHS, _FLY_MATCH_MODES, _FLY_STAGGER_BY

np = pytest.importorskip("numpy", reason="numpy が無い環境")
Image = pytest.importorskip("PIL.Image", reason="Pillow が無い環境")


def _mf():
    """morph_flight（numpy・opencv・Pillow・tqdm が要る）。無い環境は skip"""
    return pytest.importorskip(
        "scriptvedit.morph_flight",
        reason="numpy / Pillow などの morph の依存が無い環境")


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


# --- 素材 ---------------------------------------------------------------------

def _shape(tmp_path, name, size, kind, color):
    """透明の地に図形を1つ描いた RGBA の PNG"""
    from PIL import ImageDraw
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    w, h = size
    if kind == "ellipse":
        d.ellipse((2, 2, w - 3, h - 3), fill=color)
    elif kind == "ring":
        d.ellipse((2, 2, w - 3, h - 3), outline=color, width=max(2, min(w, h) // 6))
    elif kind == "bars":
        for x in range(2, w - 4, 8):
            d.rectangle((x, 3, x + 4, h - 4), fill=color)
    elif min(w, h) <= 3:
        d.rectangle((0, 0, w - 1, h - 1), fill=color)
    else:
        d.rectangle((1, 1, w - 2, h - 2), fill=color)
    path = tmp_path / name
    im.save(path)
    return str(path)


def _frames(out_dir):
    names = sorted(n for n in os.listdir(out_dir) if n.endswith(".png"))
    return [np.array(Image.open(os.path.join(out_dir, n))) for n in names]


def _paste(canvas_wh, img, pos):
    w, h = canvas_wh
    out = np.zeros((h, w, 4), dtype=np.uint8)
    arr = np.array(Image.open(img))
    out[pos[1]:pos[1] + arr.shape[0], pos[0]:pos[0] + arr.shape[1]] = arr
    return out


# --- (a) 生成 ------------------------------------------------------------------

def test_first_frame_is_a_and_last_frame_is_b_at_offset(tmp_path):
    """最初のコマは A、最後のコマは offset の位置の B と画素一致する"""
    mf = _mf()
    a = _shape(tmp_path, "a.png", (60, 40), "ellipse", (220, 30, 30, 255))
    b = _shape(tmp_path, "b.png", (50, 30), "rect", (30, 60, 220, 255))
    out = tmp_path / "f"
    info = mf.generate_flight_frames(a, b, str(out), 9, offset=(70, -10),
                                     max_pixels=500, seed=3)
    frames = _frames(out)
    assert len(frames) == 9
    (cw, ch), (mx, my) = info["canvas"], info["margin"]
    assert frames[0].shape == (ch, cw, 4)
    # キャンバスは A の中心に対して対称（余白は偶数）
    assert cw == 60 + 2 * mx and ch == 40 + 2 * my and mx % 2 == 0 and my % 2 == 0
    assert np.array_equal(frames[0], _paste((cw, ch), a, (mx, my)))
    # B の左上 = A の左上 + ((60-50)/2 + 70, (40-30)/2 - 10) を四捨五入 = (75, -5)
    assert info["b_pos"] == (mx + 75, my - 5)
    assert np.array_equal(frames[-1], _paste((cw, ch), b, info["b_pos"]))
    # 途中のコマは A でも B でもない（動いている）
    assert not np.array_equal(frames[4], frames[0])
    assert not np.array_equal(frames[4], frames[-1])


@pytest.mark.parametrize("match", list(_FLY_MATCH_MODES))
@pytest.mark.parametrize("color_path", list(_FLY_COLOR_PATHS))
def test_every_match_and_color_path_keeps_the_ends(tmp_path, match, color_path):
    mf = _mf()
    a = _shape(tmp_path, "a.png", (30, 30), "ring", (250, 200, 30, 255))
    b = _shape(tmp_path, "b.png", (40, 20), "bars", (30, 200, 250, 255))
    out = tmp_path / "f"
    info = mf.generate_flight_frames(a, b, str(out), 5, offset=(-30, 25), match=match,
                                     color_path=color_path, swirl=1.0, stagger_by="random",
                                     max_pixels=300, seed=1)
    frames = _frames(out)
    assert np.array_equal(frames[0], _paste(info["canvas"], a, info["margin"]))
    assert np.array_equal(frames[-1], _paste(info["canvas"], b, info["b_pos"]))


def _aa_ring(tmp_path, name, size, color):
    """縁がアンチエイリアスの輪（α だけが 0〜255 で変わり、色はどの画素も color）"""
    w, h = size
    ys, xs = np.mgrid[0:h, 0:w].astype(float)
    r = np.hypot((xs - (w - 1) / 2) / (w / 2 - 2), (ys - (h - 1) / 2) / (h / 2 - 2))
    alpha = np.clip(1.0 - np.abs(r - 0.75) * min(w, h) / 6.0, 0.0, 1.0)
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[:, :, :3] = color
    arr[:, :, 3] = np.rint(alpha * 255).astype(np.uint8)
    path = tmp_path / name
    Image.fromarray(arr, "RGBA").save(path)
    return str(path)


@pytest.mark.parametrize("case", ["white_4_to_1", "white_1_to_4", "red_semi", "red_aa"])
def test_particles_keep_the_colour_when_a_and_b_share_it(tmp_path, case):
    """A と B が同じ色なら、どのコマのどの画素（α>0）もその色のまま（α だけが変わる）。

    回帰: 粒の色に α が2回掛かり、α<1 の粒（少ない側の複製・縁のアンチエイリアス・
    半透明の素材）が暗くなっていた（白 → 白 1:4 で飛んでいる粒が RGB 最小 52、
    dissolve の間に残っている A の字も #e0241b の R=224 から 126 へ沈んだ）
    """
    mf = _mf()
    red = (224, 36, 27)
    if case == "white_4_to_1":
        a = _shape(tmp_path, "a.png", (40, 40), "rect", (255, 255, 255, 255))
        b = _shape(tmp_path, "b.png", (20, 20), "rect", (255, 255, 255, 255))
        color = (255, 255, 255)
    elif case == "white_1_to_4":
        a = _shape(tmp_path, "a.png", (20, 20), "rect", (255, 255, 255, 255))
        b = _shape(tmp_path, "b.png", (40, 40), "rect", (255, 255, 255, 255))
        color = (255, 255, 255)
    elif case == "red_semi":
        a = _shape(tmp_path, "a.png", (40, 40), "rect", red + (128,))
        b = _shape(tmp_path, "b.png", (20, 20), "rect", red + (255,))
        color = red
    else:
        a = _aa_ring(tmp_path, "a.png", (36, 30), red)
        b = _aa_ring(tmp_path, "b.png", (72, 44), red)
        color = red
    out = tmp_path / "f"
    mf.generate_flight_frames(a, b, str(out), 21, offset=(90, 20), seed=2)
    frames = _frames(out)
    for i, f in enumerate(frames):
        rgb = f[f[:, :, 3] > 0][:, :3].astype(int)
        assert len(rgb) > 0, i
        assert int(np.abs(rgb - np.array(color)).max()) <= 1, (i, rgb.min(axis=0))


def test_splat_in_chunks_matches_one_pass_and_caps_memory():
    """粒の描画を _SPLAT_CHUNK 要素ずつに分けても、1回で描いたのと同じ絵になる。
    大きな粒（半径 32）でも一時配列は頭打ち（分けないと 3,000 粒で 300 MB を超える）"""
    mf = _mf()
    import tracemalloc
    rng = np.random.default_rng(0)
    pos = rng.uniform(40, 360, size=(3000, 2))
    color = rng.uniform(0, 1, size=(3000, 3)).astype(np.float32)
    alpha = rng.uniform(0.05, 1, size=3000)
    old = mf._SPLAT_CHUNK
    try:
        mf._SPLAT_CHUNK = 1 << 40
        box1, one = mf._splat(pos[:400], color[:400], alpha[:400], 3.0)
        mf._SPLAT_CHUNK = 500                     # 1回に 7 粒（K=8 → 64 要素/粒）
        box2, many = mf._splat(pos[:400], color[:400], alpha[:400], 3.0)
    finally:
        mf._SPLAT_CHUNK = old
    assert box1 == box2
    assert np.allclose(one, many, atol=1e-5)
    tracemalloc.start()
    try:
        mf._splat(pos, color, alpha, 32.0)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 120e6, peak


def test_particle_count_matches_n_even_when_pixel_counts_are_1_to_3(tmp_path):
    """A と B の画素数が 1:3 でも、粒の数は N = min(max_pixels, 多い方) にそろう。
    少ない側の複製は α を分け合う（重なった所が元の絵より濃くならない）"""
    mf = _mf()
    a = _shape(tmp_path, "a.png", (20, 20), "rect", (255, 0, 0, 255))   # 18x18 = 324
    b = _shape(tmp_path, "b.png", (56, 20), "rect", (0, 0, 255, 128))   # 54x18 = 972
    n_a = int((np.array(Image.open(a))[:, :, 3] > 25).sum())
    n_b = int((np.array(Image.open(b))[:, :, 3] > 25).sum())
    assert n_b == 3 * n_a
    cfg = mf._check_params((0, 0), 12000, "ot", 0.25, 0.0, 0.3, "x", 2, "oklab",
                           (0.15, 0.15), 0)
    ctx = mf._plan_flight(a, b, [0.0, 0.5, 1.0], **cfg)
    assert ctx["n"] == n_b
    for key in ("S", "E", "C", "M", "lab_a", "lab_b"):
        assert len(ctx[key]) == n_b, key
    assert len(ctx["alpha_a"]) == len(ctx["alpha_b"]) == n_b
    # A の複製は α を等分する: α の合計は元の画素の α の合計と同じ
    assert abs(float(ctx["alpha_a"].sum()) - n_a * 1.0) < 1e-6
    assert abs(float(ctx["alpha_b"].sum()) - n_b * 128 / 255.0) < 1e-6
    # max_pixels で頭打ち（多い側を重みつきで間引く）
    cfg = mf._check_params((0, 0), 500, "ot", 0.25, 0.0, 0.3, "x", 2, "oklab",
                           (0.15, 0.15), 0)
    assert mf._plan_flight(a, b, [0.0, 1.0], **cfg)["n"] == 500


def _movement(src, dst, sigma):
    return float(np.hypot(*(dst[sigma] - src).T).sum())


@pytest.mark.parametrize("offset", [(0, 0), (300, -40)])
def test_sliced_ot_movement_is_close_to_hungarian(tmp_path, offset):
    """1,000 粒で、スライスした最適輸送の移動の総量がハンガリアン法の 1.15 倍以内"""
    mf = _mf()
    try:
        from scipy.optimize import linear_sum_assignment
    except ImportError:
        pytest.skip("scipy が無い環境（ハンガリアン法の比較に使う）")
    a = _shape(tmp_path, "a.png", (90, 70), "ring", (255, 0, 0, 255))
    b = _shape(tmp_path, "b.png", (110, 50), "bars", (0, 0, 255, 255))
    rng = np.random.default_rng(5)
    xa = mf._sample(*mf._opaque_pixels(np.array(Image.open(a)), "A", a), 1000, rng)[0]
    xb = mf._sample(*mf._opaque_pixels(np.array(Image.open(b)), "B", b), 1000, rng)[0]
    xb = xb + np.array(offset, dtype=np.float64)
    cost = np.hypot(xa[:, None, 0] - xb[None, :, 0], xa[:, None, 1] - xb[None, :, 1])
    rows, cols = linear_sum_assignment(cost)
    best = float(cost[rows, cols].sum())
    ot = _movement(xa, xb, mf._match(xa, xb, "ot", np.random.default_rng(0)))
    assert ot <= best * 1.15, (ot / best)
    if offset == (0, 0):
        # 比較が意味を持つ条件: 無作為な対応はこの上限を大きく超える
        rnd = _movement(xa, xb, mf._match(xa, xb, "random", np.random.default_rng(0)))
        assert rnd > best * 1.15


def test_particles_stay_inside_the_canvas_in_every_frame(tmp_path):
    """全コマで、粒（半径 + 縁）がキャンバスの内側にある（大きな arc / swirl でも）"""
    mf = _mf()
    a = _shape(tmp_path, "a.png", (40, 30), "ellipse", (255, 0, 0, 255))
    b = _shape(tmp_path, "b.png", (30, 40), "rect", (0, 255, 0, 255))
    progress = [i / 23 for i in range(24)]
    cfg = mf._check_params((120, -60), 800, "ot", 1.5, 3.0, 0.5, "distance", 3, "oklch",
                           (0.1, 0.2), 4)
    ctx = mf._plan_flight(a, b, progress, **cfg)
    cw, ch = ctx["canvas"]
    r = ctx["radius"]
    for p in progress:
        pos = mf._positions(ctx, mf._local_progress(ctx, p))
        assert pos[:, 0].min() >= r and pos[:, 1].min() >= r, p
        assert pos[:, 0].max() <= cw - 1 - r and pos[:, 1].max() <= ch - 1 - r, p
    out = tmp_path / "f"
    mf.generate_flight_frames(a, b, str(out), 24, offset=(120, -60), max_pixels=800,
                              arc=1.5, swirl=3.0, stagger=0.5, stagger_by="distance",
                              particle_size=3, color_path="oklch", dissolve=(0.1, 0.2),
                              seed=4)
    for f in _frames(out):
        # 端の1列・1行に粒が掛かっていない（切れていない）
        assert f[0, :, 3].max() == 0 and f[-1, :, 3].max() == 0
        assert f[:, 0, 3].max() == 0 and f[:, -1, 3].max() == 0


def test_canvas_is_symmetric_about_the_center_of_a(tmp_path):
    """B が A の右にだけあっても、余白は左右・上下それぞれ同じ幅（A の中心が動かない）"""
    mf = _mf()
    a = _shape(tmp_path, "a.png", (31, 17), "rect", (255, 0, 0, 255))   # 奇数の寸法
    b = _shape(tmp_path, "b.png", (20, 20), "ellipse", (0, 0, 255, 255))
    cfg = mf._check_params((200, 0), 300, "ot", 0.0, 0.0, 0.0, "x", 2, "oklab",
                           (0.15, 0.15), 0)
    ctx = mf._plan_flight(a, b, [0.0, 0.5, 1.0], **cfg)
    (cw, ch), (mx, my) = ctx["canvas"], ctx["margin"]
    assert ctx["a_box"] == (mx, my, mx + 31, my + 17)
    assert cw - (mx + 31) == mx and ch - (my + 17) == my       # 対称
    # B の左上は A の左上から (floor((31-20)/2 + 200 + 0.5), floor((17-20)/2 + 0.5))
    # = (206, -1)。右端 206+20 まで覆う
    assert ctx["b_box"] == (mx + 206, my - 1, mx + 226, my + 19)
    assert mx >= 226 - 31 and mx % 2 == 0 and ctx["b_box"][2] <= cw
    assert ctx["b_box"][1] >= 0 and ctx["b_box"][3] <= ch


@pytest.mark.parametrize("wa, wb", [(40, 23), (41, 24), (40, 24), (41, 23)])
@pytest.mark.parametrize("d", [30, -30, 0, 7.5, -0.25])
def test_b_offset_uses_the_same_rounding_as_a_still_b(wa, wb, d):
    """B の左上のずれは、A の中心を整数の画素 X に置いたときの
    「中心を X + d に置いた静止画の B の左上 − A の左上」（move の anchor='center' は
    左上を trunc(中心 − 幅/2) に置く）と同じ。幅の偶奇・d の端数・X によらない。

    回帰: 四捨五入 floor((Wa−Wb)/2 + d + 0.5) は Wa が偶数・Wb が奇数のとき 1px 大きかった
    """
    mf = _mf()
    for x in range(200, 204):
        assert mf._b_offset(wa, wb, d) == (math.trunc(x + d - wb / 2)
                                           - math.trunc(x - wa / 2)), x


def test_seed_makes_frames_deterministic(tmp_path):
    mf = _mf()
    a = _shape(tmp_path, "a.png", (30, 30), "ring", (255, 0, 0, 255))
    b = _shape(tmp_path, "b.png", (30, 20), "bars", (0, 0, 255, 255))
    kw = dict(offset=(40, 10), max_pixels=300)
    mf.generate_flight_frames(a, b, str(tmp_path / "s1"), 6, seed=7, **kw)
    mf.generate_flight_frames(a, b, str(tmp_path / "s2"), 6, seed=7, **kw)
    mf.generate_flight_frames(a, b, str(tmp_path / "s3"), 6, seed=8, **kw)
    one, two, other = (_frames(tmp_path / d) for d in ("s1", "s2", "s3"))
    assert all(np.array_equal(x, y) for x, y in zip(one, two))
    assert not np.array_equal(one[3], other[3])
    # 両端は seed に関係なく元の絵
    assert np.array_equal(one[0], other[0]) and np.array_equal(one[-1], other[-1])


def test_transparent_images_are_value_errors(tmp_path):
    mf = _mf()
    a = _shape(tmp_path, "a.png", (20, 20), "rect", (255, 0, 0, 255))
    empty = tmp_path / "empty.png"
    Image.new("RGBA", (20, 20), (255, 0, 0, 20)).save(empty)    # α=20/255 <= 0.1
    with pytest.raises(ValueError, match="不透明な画素"):
        mf.generate_flight_frames(str(empty), a, str(tmp_path / "f"), 3)
    with pytest.raises(ValueError, match="不透明な画素"):
        mf.generate_flight_frames(a, str(empty), str(tmp_path / "f"), 3)
    # target は構築時に止める
    p = sv.Project()
    activate(p)
    with pytest.raises(ValueError, match="不透明な画素"):
        sv.fly_to(sv.Object(str(empty)))


def test_canvas_over_4096_is_value_error(tmp_path):
    mf = _mf()
    a = _shape(tmp_path, "a.png", (20, 20), "rect", (255, 0, 0, 255))
    b = _shape(tmp_path, "b.png", (20, 20), "rect", (0, 0, 255, 255))
    with pytest.raises(ValueError, match="4096"):
        mf.generate_flight_frames(a, b, str(tmp_path / "f"), 3, offset=(2100, 0),
                                  max_pixels=50)


# --- (b) DSL ------------------------------------------------------------------

class _Op:
    def __init__(self, target, **params):
        self.name = "fly_to"
        self.params = params
        self._fly_target = target


def test_cache_key_depends_on_target_content_and_offset(tmp_path):
    a = _shape(tmp_path, "a.png", (20, 20), "rect", (255, 0, 0, 255))
    b1 = _shape(tmp_path, "b1.png", (20, 20), "rect", (0, 0, 255, 255))
    b2 = _shape(tmp_path, "b2.png", (20, 20), "ellipse", (0, 0, 255, 255))
    b1_copy = tmp_path / "copy" / "b1.png"
    b1_copy.parent.mkdir()
    shutil.copyfile(b1, b1_copy)
    p = sv.Project()
    p.configure(width=320, height=180, fps=30)
    activate(p)

    def key(target, offset=(10.0, 0.0), dur=2, fps=30):
        op = _Op(sv.Object(target), offset=offset, seed=0)
        return cache_mod._flight_cache_path(a, op, dur, fps)

    base = key(b1)
    assert key(str(b1_copy)) == base                  # 同じ内容なら置き場所に依存しない
    assert key(b2) != base                            # target の内容で変わる
    assert key(b1, offset=(10.0, 5.0)) != base        # offset で変わる
    assert key(b1, dur=3) != base and key(b1, fps=60) != base
    # 画面寸法には依存しない（キャンバスは A・B・道すじだけで決まる）
    p.configure(width=1920, height=1080, fps=30)
    assert key(b1) == base
    # 描画の版を上げると鍵が変わる
    old = cache_mod._FLIGHT_VER
    try:
        cache_mod._FLIGHT_VER = old + "x"
        assert key(b1) != base
    finally:
        cache_mod._FLIGHT_VER = old
    assert "tgt_ffp=" in cache_mod._op_fingerprint_str(_Op(sv.Object(b1)))


def test_factory_normalizes_params_so_equal_outputs_share_a_key(tmp_path):
    """(300, 0) と (300.0, 0.0)、2 と 2.0 は同じ出力なので同じ鍵"""
    target = sv.Object(_shape(tmp_path, "b.png", (20, 20), "rect", (0, 0, 255, 255)))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        e1 = sv.fly_to(target, offset=(300, 0), particle_size=2, dissolve=(0.1, 0))
        e2 = sv.fly_to(target, offset=[300.0, 0.0], particle_size=2.0,
                       dissolve=(0.1, 0.0))
    assert cache_mod._op_fingerprint_str(e1) == cache_mod._op_fingerprint_str(e2)
    assert e1.params["offset"] == (300.0, 0.0)
    assert "delay" not in e1.params and "duration" not in e1.params


@pytest.mark.parametrize("kwargs, match", [
    (dict(offset=(1, float("nan"))), "offset"),
    (dict(offset=3), "offset"),
    (dict(match="hungarian"), "match"),
    (dict(stagger_by="z"), "stagger_by"),
    (dict(color_path="rgb"), "color_path"),
    (dict(stagger=1.0), "stagger"),
    (dict(stagger=-0.1), "stagger"),
    (dict(dissolve=(0.6, 0.6)), "dissolve"),
    (dict(dissolve=(-0.1, 0.1)), "dissolve"),
    (dict(max_pixels=0), "max_pixels"),
    (dict(max_pixels=2.5), "max_pixels"),
    (dict(particle_size=0), "particle_size"),
    (dict(arc=float("inf")), "arc"),
    (dict(seed=1.5), "seed"),
    (dict(delay=-1), "delay"),
    (dict(duration=0), "duration"),
])
def test_factory_rejects_bad_params(tmp_path, kwargs, match):
    target = sv.Object(_shape(tmp_path, "b.png", (20, 20), "rect", (0, 0, 255, 255)))
    with pytest.raises(ValueError, match=match):
        sv.fly_to(target, **kwargs)


def test_factory_rejects_unusable_targets(tmp_path):
    p = sv.Project()
    p.configure(width=320, height=180, fps=30)
    activate(p)
    png = _shape(tmp_path, "b.png", (20, 20), "rect", (0, 0, 255, 255))
    with pytest.raises(TypeError):
        sv.fly_to(png)                                   # パス文字列は不可
    with pytest.raises(ValueError, match="text_image"):
        sv.fly_to(sv.text("文字", size=40))              # text() 系は不可
    processed = sv.Object(png)
    processed <= sv.resize(sx=0.5, sy=0.5)
    with pytest.raises(ValueError, match="Transform/Effect"):
        sv.fly_to(processed)                             # 加工つきは不可
    with pytest.raises(ValueError, match="画像のみ"):
        sv.fly_to(sv.Object(sv.asset("video/clip_with_audio.mp4")))   # 動画は不可


def _layer(tmp_path, body, name="l.py"):
    path = tmp_path / name
    path.write_text("from scriptvedit import *\n" + body, encoding="utf-8")
    return str(path)


def _dry_run(tmp_path, body, width=1280, height=720, name="l.py"):
    p = sv.Project()
    p.configure(width=width, height=height, fps=30)
    p.layer(_layer(tmp_path, body, name), priority=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return p.render(str(tmp_path / "o.mp4"), dry_run=True)


def test_bakeable_after_fly_to_is_value_error(tmp_path):
    a = _shape(tmp_path, "a.png", (20, 20), "rect", (255, 0, 0, 255))
    b = _shape(tmp_path, "b.png", (20, 20), "rect", (0, 0, 255, 255))
    with pytest.raises(ValueError, match="末尾"):
        _dry_run(tmp_path, f"o = Object({a!r})\n"
                           f"o.time(1) <= fly_to(Object({b!r})) & fade(0.5)\n")
    with pytest.raises(ValueError, match="1回しか"):
        _dry_run(tmp_path, f"o = Object({a!r})\n"
                           f"o.time(1) <= fly_to(Object({b!r})) & explode_to()\n",
                 name="l2.py")
    # live（move）と、-（policy='off'）の bakeable は後ろに置ける
    res = _dry_run(tmp_path, f"o = Object({a!r})\n"
                             f"o.time(1) <= fly_to(Object({b!r})) & -fade(0.5)\n"
                             f"o <= move(x=0.5, y=0.5)\n", name="l3.py")
    assert any("/flight/" in k.replace("\\", "/") for k in res["cache"])
    with pytest.raises(ValueError, match="time"):
        _dry_run(tmp_path, f"Object({a!r}) <= fly_to(Object({b!r}))\n", name="l4.py")


def _overlay_xy(main):
    fc = main[main.index("-filter_complex") + 1]
    m = re.search(r"overlay=([^:]+):([^:]+):eof_action", fc)
    return m.group(1), m.group(2)


def _eval_ff(expr, W, H, w, h):
    return eval(expr.replace("trunc", "_t"),
                {"_t": math.trunc, "W": W, "H": H, "w": w, "h": h})


def test_topleft_anchor_overlay_puts_a_where_a_still_image_would_be(tmp_path):
    """anchor='topleft' の overlay の式（dry_run）: 余白込みのキャンバスでも、A の左上は
    fly_to の無いときと同じ画素に来る（余白は対称なので (w - A の幅)/2 を引く）"""
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe が無い環境（A の寸法を overlay 式へ入れるのに使う）")
    a = _shape(tmp_path, "a.png", (60, 40), "rect", (255, 0, 0, 255))
    b = _shape(tmp_path, "b.png", (30, 30), "ellipse", (0, 0, 255, 255))
    mv = "o <= move(x=0.25, y=0.3, anchor='topleft')\n"
    plain = _dry_run(tmp_path, f"o = Object({a!r})\no.time(1)\n" + mv, name="p.py")
    flown = _dry_run(tmp_path, f"o = Object({a!r})\n"
                               f"o.time(1) <= fly_to(Object({b!r}), offset=(200, 50))\n"
                               + mv, name="f.py")
    px, py = _overlay_xy(plain["main"])
    fx, fy = _overlay_xy(flown["main"])
    assert "(w-60)/2" in fx and "(h-40)/2" in fy
    want = (_eval_ff(px, 1280, 720, 60, 40), _eval_ff(py, 1280, 720, 60, 40))
    for mx, my in ((0, 0), (230, 50), (412, 88)):   # 余白（偶数）がいくつでも同じ
        # overlay はキャンバスの左上の位置。A はそこから余白ぶん右下にある
        assert (_eval_ff(fx, 1280, 720, 60 + 2 * mx, 40 + 2 * my) + mx,
                _eval_ff(fy, 1280, 720, 60 + 2 * mx, 40 + 2 * my) + my) == want


def test_dry_run_steps_and_tpad_for_delay_and_duration(tmp_path):
    a = _shape(tmp_path, "a.png", (20, 20), "rect", (255, 0, 0, 255))
    b = _shape(tmp_path, "b.png", (20, 20), "rect", (0, 0, 255, 255))
    res = _dry_run(tmp_path, f"o = Object({a!r})\n"
                             f"o.time(2) <= resize(sx=2, sy=2)\n"
                             f"o <= fly_to(Object({b!r}), delay=0.5, duration=1.0)\n")
    flights = {k: v for k, v in res["cache"].items() if "/flight/" in k.replace("\\", "/")}
    (path, cmd), = flights.items()
    assert cmd[cmd.index("-vf") + 1] == (
        "tpad=start=15:start_mode=clone:stop=16:stop_mode=clone")
    assert cmd[cmd.index("-frames:v") + 1] == "61"
    # resize は fly_to の前処理として PNG のチェックポイントに焼かれる
    assert any(k.endswith(".png") and "/checkpoint/" in k.replace("\\", "/")
               for k in res["cache"])
    assert any(path.replace("\\", "/") in c.replace("\\", "/") for c in res["main"])


# --- (c) 実レンダ --------------------------------------------------------------

_W, _H, _FPS = 96, 54, 30


def _need_ffmpeg():
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg / ffprobe が無い環境")


def _rgb_frames(video):
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True, timeout=120).stdout
    return np.frombuffer(out, np.uint8).reshape(-1, _H, _W, 3).astype(int)


def _box(frame, kind):
    r, g, b = frame[:, :, 0], frame[:, :, 1], frame[:, :, 2]
    mask = ((r > 150) & (g < 110) & (b < 110) if kind == "red"
            else (g > 120) & (r < 110) & (b < 110))
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _centroid(frame, kind):
    """色の濃さで重みをつけた重心 (x, y)。縁の 4:2:0・h264 のにじみに強く、
    1px のずれは確実に見分けられる（にじみで動くのは 0.1px 程度）"""
    r, g, b = (frame[:, :, c].astype(float) for c in range(3))
    wgt = np.clip(r - (g + b) / 2 if kind == "red" else g - (r + b) / 2, 0, None)
    total = float(wgt.sum())
    if total < 255.0:
        return None
    ys, xs = np.mgrid[0:frame.shape[0], 0:frame.shape[1]]
    return float((xs * wgt).sum() / total), float((ys * wgt).sum() / total)


def _near(got, want, tol=0.3):
    return got is not None and all(abs(g - w) <= tol for g, w in zip(got, want))


def _project(tmp_path, body, name):
    p = sv.Project()
    p.configure(width=_W, height=_H, fps=_FPS, background_color="white")
    p.layer(_layer(tmp_path, body, f"l_{name}.py"), priority=0)
    return p


def _png_frames(directory):
    names = sorted(n for n in os.listdir(directory) if n.endswith(".png"))
    return [np.array(Image.open(os.path.join(directory, n)).convert("RGBA"))
            for n in names]


def _same_picture(x, y, tol=40):
    """α が完全に一致し（＝同じ画素に映る）、色は 4:2:0 の色差のにじみの範囲で一致する。

    overlay は yuva420p で合成するので、縁の 1〜2px の色は入力の作り（1枚の PNG か、
    広いキャンバスの動画か）で少し変わる。位置のずれは α の不一致として必ず出る。
    """
    if not np.array_equal(x[:, :, 3], y[:, :, 3]):
        return False
    a = x[:, :, 3:4].astype(int)
    diff = np.abs(x[:, :, :3].astype(int) * a - y[:, :, :3].astype(int) * a)
    return int(diff.max()) <= tol * 255


def _render_pngs(tmp_path, body, name, dry_run=False):
    """連番 PNG（可逆・常に透過）へ書き出す。h264 のにじみが無いので画素で比べられる"""
    out_dir = tmp_path / name
    out_dir.mkdir(exist_ok=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = _project(tmp_path, body, name).render(
            str(out_dir / "f.png"), dry_run=dry_run, timeout=300)
    return res if dry_run else _png_frames(out_dir)


def test_render_holds_a_before_and_b_after_motion_with_topleft_anchor(tmp_path):
    """delay の間は A、duration の後は offset の位置の B が窓の終わりまで残る。
    anchor='topleft' でも A は静止画と同じ画素に映る（キャンバスの左端が画面の外に
    出ても 1px もずれない）。dry_run は実レンダの前後で同じ"""
    _need_ffmpeg()
    _mf()
    a = _shape(tmp_path, "a.png", (12, 12), "rect", (255, 0, 0, 255))
    b = _shape(tmp_path, "b.png", (12, 12), "ellipse", (0, 200, 0, 255))
    pad = _shape(tmp_path, "pad.png", (2, 2), "rect", (0, 0, 255, 255))
    tail = f"(Object({pad!r}) @ 0).time(1.5) <= move(x=0.02, y=0.05, anchor='center')\n"
    # x=0.2 → 19.2px（端数あり）。fly_to のキャンバスは左へ広がって画面の外に出る
    mv = "o <= move(x=0.2, y=0.4, anchor='topleft')\n"
    still_a = _render_pngs(tmp_path, f"o = Object({a!r})\no.time(1.2)\n" + mv + tail,
                           "still_a")
    # B を A の右 40px に置いた静止画（offset=(40, 0)・同じ寸法なので左上も 40px 右）
    mv_b = f"o <= move(x={(0.2 * _W + 40) / _W!r}, y=0.4, anchor='topleft')\n"
    still_b = _render_pngs(tmp_path, f"o = Object({b!r})\no.time(1.2)\n" + mv_b + tail,
                           "still_b")
    body = (f"o = Object({a!r})\n"
            f"o.time(1.2) <= fly_to(Object({b!r}), offset=(40, 0), delay=0.3, "
            f"duration=0.5, max_pixels=200, seed=1)\n" + mv + tail)
    cold = _render_pngs(tmp_path, body, "fly", dry_run=True)
    frames = _render_pngs(tmp_path, body, "fly")
    warm = _render_pngs(tmp_path, body, "fly", dry_run=True)
    assert cold == warm
    assert len(frames) == len(still_a) == 45
    # delay の間（0〜9 枚目）は静止画の A と画素一致
    for i in range(0, 10):
        assert _same_picture(frames[i], still_a[i]), i
    assert not _same_picture(frames[16], still_a[16])           # 動いている
    # duration の後（0.8 秒 = 24 枚目〜窓の最後の 36 枚目）は静止画の B と画素一致
    for i in range(24, 37):
        assert _same_picture(frames[i], still_b[i]), i
    # 窓の外（37 枚目〜）は背景だけ（pad の点を除いて透明）
    assert _same_picture(frames[37], still_a[37])


def test_render_b_lands_where_a_still_b_would_when_the_offsets_are_even(tmp_path):
    """A の中心を整数の画素に置き、⌈Wa/2⌉ + ⌊dx − Wb/2⌋（縦も同じ）が偶数なら、
    最後のコマの B は「中心を A の中心 + offset に置いた静止画の B」と画素一致する
    （別の Object の静止画へ 1px も跳ねずに引き継げる）。A の中心が奇数の画素でも同じ。

    偶数の条件: overlay は 4:2:0 の格子（2px）へ左上を切り捨てるので、静止画どうしの
    ずれは常に偶数。fly_to のキャンバスの中の B のずれが奇数だと、どこに置いた静止画とも
    1px ずれる（README の fly_to の節）。
    """
    _need_ffmpeg()
    mf = _mf()
    a = _shape(tmp_path, "a.png", (40, 16), "rect", (255, 0, 0, 255))
    b = _shape(tmp_path, "b.png", (23, 31), "rect", (0, 0, 255, 255))
    w, h = 320, 128
    # (A の中心 X, Y, offset)。2つ目は中心が奇数の画素
    cases = [(80, 64, (30, 10)), (245, 67, (-30, -10))]
    fly, still = [], []
    for k, (x, y, (dx, dy)) in enumerate(cases):
        assert mf._b_offset(40, 23, dx) % 2 == 0 and mf._b_offset(16, 31, dy) % 2 == 0
        fly.append(f"o{k} = Object({a!r}) @ 0\n"
                   f"o{k}.time(1.0) <= fly_to(Object({b!r}), offset=({dx}, {dy}), "
                   f"duration=0.5, max_pixels=300, seed=1)\n"
                   f"o{k} <= move(x={x / w!r}, y={y / h!r}, anchor='center')\n")
        still.append(f"s{k} = Object({b!r}) @ 0\ns{k}.time(1.0)\n"
                     f"s{k} <= move(x={(x + dx) / w!r}, y={(y + dy) / h!r}, anchor='center')\n")

    def render(body, name):
        p = sv.Project()
        p.configure(width=w, height=h, fps=_FPS)
        p.layer(_layer(tmp_path, body, f"l_{name}.py"), priority=0)
        out_dir = tmp_path / name
        out_dir.mkdir()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            p.render(str(out_dir / "f.png"), timeout=300)
        return _png_frames(out_dir)

    got = render("".join(fly), "fly")
    want = render("".join(still), "still")
    assert len(got) == len(want) == 30
    for i in (16, 22, 29):                       # duration（0.5 秒）の後は B を保持
        assert np.array_equal(got[i][:, :, 3], want[i][:, :, 3]), i


# --- (d) describe --------------------------------------------------------------

def test_manifest_lists_fly_to_with_real_defaults_and_choices():
    mf = _mf()
    import inspect
    entry = next(e for e in describe()["effects"] if e["name"] == "fly_to")
    assert entry["bakeable"] is True and entry["effect_names"] == ["fly_to"]
    params = entry["params"]
    sig = inspect.signature(sv.fly_to).parameters
    for key, default in mf.FLY_PARAM_DEFAULTS.items():
        assert key in params and params[key].get("desc"), key
        # 実装（morph_flight）と DSL（fly_to）とマニフェストの既定値が同じ
        assert sig[key].default == default, key
        got = params[key]["default"]
        assert (tuple(got) if isinstance(got, list) else got) == default, key
    assert params["match"]["choices"] == list(_FLY_MATCH_MODES)
    assert params["stagger_by"]["choices"] == list(_FLY_STAGGER_BY)
    assert params["color_path"]["choices"] == list(_FLY_COLOR_PATHS)
    assert params["target"]["type"] == "object" and params["target"]["required"]
    for key in ("delay", "duration"):
        assert key in params
    from scriptvedit import morph
    assert set(mf.FLY_PARAM_DEFAULTS) == set(morph.FLY_PARAM_KEYS)


# --- (e) 金型 -------------------------------------------------------------------

_GOLDEN_FRAMES = (("dissolve_in", 2), ("flight", 10), ("dissolve_out", 19))

# 金型の判定の閾値（_golden_diff の3つの値）。粒の入れ替わりの雑音と描き方の変更の
# 実測（流れの出力に σ=0.01px の雑音 → ぼかしの差 0.69 / 色 0.02 / α 0.16%。
# 粒を 5% 暗く → 色 2.0〜4.3、止まる幅 0.12→0.2 → ぼかしの差 4.6・α 4%、
# 粒の半径 +0.25 → ぼかしの差 5.1・α 4.6%、色に α が2回掛かる旧版 → ぼかしの差 5.5・色 19）。
# test_golden_check_catches_render_changes がこの線引きを確かめる
_GOLDEN_TOL = {"blur": 1.0, "color": 1.0, "alpha": 0.01}


def _golden_render(mf, tmp_path):
    """金型の3コマ（A → 粒、飛んでいる途中、粒 → B）を描く"""
    a = _shape(tmp_path, "ga.png", (70, 50), "ring", (230, 40, 30, 255))
    b = _shape(tmp_path, "gb.png", (90, 36), "bars", (40, 120, 230, 255))
    out = tmp_path / "golden_frames"
    shutil.rmtree(out, ignore_errors=True)
    mf.generate_flight_frames(a, b, str(out), 21, offset=(120, 40), max_pixels=1500,
                              arc=0.3, swirl=0.4, stagger=0.35, color_path="oklch",
                              seed=11)
    frames = _frames(out)
    return {name: frames[i] for name, i in _GOLDEN_FRAMES}


def _center_fit(img, size):
    """キャンバスを中央をそろえて (幅, 高さ) へ切り出す・透明で埋める。

    fly_to のキャンバスは A の中心に対して対称なので、中央をそろえれば A も B も粒も
    同じ位置に来る。キャンバスの大きさは一番外を飛ぶ粒で決まり、粒の入れ替わり
    （BLAS・CPU の浮動小数の差）で余白が数 px 変わることがある。
    """
    w, h = size
    out = np.zeros((h, w, 4), dtype=img.dtype)
    sy, sx = (img.shape[0] - h) // 2, (img.shape[1] - w) // 2
    ch, cw = min(h, img.shape[0]), min(w, img.shape[1])
    out[max(-sy, 0):max(-sy, 0) + ch, max(-sx, 0):max(-sx, 0) + cw] = \
        img[max(sy, 0):max(sy, 0) + ch, max(sx, 0):max(sx, 0) + cw]
    return out


def _box_blur(f, r=2):
    """幅 2r+1 の箱型ぼかしを縦・横に2回（ガウスの近似。σ≈2px）"""
    for _ in range(2):
        for axis in (0, 1):
            pad = [(r + 1, r) if i == axis else (0, 0) for i in range(f.ndim)]
            c = np.cumsum(np.pad(f, pad), axis=axis)
            n = c.shape[axis]
            f = (np.take(c, range(2 * r + 1, n), axis=axis)
                 - np.take(c, range(0, n - 2 * r - 1), axis=axis)) / (2 * r + 1)
    return f


def _golden_diff(actual, want):
    """金型との差を、粒の入れ替わりに強い3つの値で測る。

    blur: 事前乗算の絵を σ≈2px でぼかした差（0..255）の、どちらかが α>0 の画素での平均
        （画面のほとんどが透明なので全面の平均は薄まる。ぼかすと隣どうしの粒の入れ替わりが
        消え、形・太さ・濃さの変化は残る）
    color: α で重みをつけた平均の色（ストレート、0..255）の差の最大
    alpha: α の総量の相対差
    """
    if actual.shape != want.shape:
        actual = _center_fit(actual, (want.shape[1], want.shape[0]))
    pa, pw = (x.astype(np.float64) for x in (actual, want))
    for p in (pa, pw):
        p[..., :3] *= p[..., 3:4] / 255.0
    union = (actual[..., 3] > 0) | (want[..., 3] > 0)
    blur = float(np.abs(_box_blur(pa) - _box_blur(pw))[union].mean()) if union.any() else 0.0
    sum_a, sum_w = float(pa[..., 3].sum()), float(pw[..., 3].sum())
    mean_a = pa[..., :3].reshape(-1, 3).sum(axis=0) / max(sum_a, 1.0) * 255.0
    mean_w = pw[..., :3].reshape(-1, 3).sum(axis=0) / max(sum_w, 1.0) * 255.0
    return {"blur": blur, "color": float(np.abs(mean_a - mean_w).max()),
            "alpha": abs(sum_a - sum_w) / max(sum_w, 1.0)}


def _golden_violations(actual, want):
    diff = _golden_diff(actual, want)
    return {k: round(v, 4) for k, v in diff.items() if v > _GOLDEN_TOL[k]}, diff


def test_golden_frames(tmp_path, request):
    """代表的な3コマ（A → 粒、飛んでいる途中、粒 → B）を金型と突き合わせる。

    金型の有無・版・寸法は framekit_golden.assert_golden に任せ、絵の差は _golden_diff の
    3つの値で判定する（粒の入れ替わりに強く、描き方の変更は確実に拾う。全面の平均の差は
    透明な画面で薄まり、色に α が2回掛かる不具合も素通りした）。キャンバスの寸法が
    粒の入れ替わりで 8px 以内だけ違うときは、中央をそろえて比べる（_center_fit）。
    描画を変えたら cache._FLIGHT_VER を上げ、目視してから pytest --golden-update。
    """
    mf = _mf()
    from framekit_golden import GOLDEN_DIR, assert_golden
    update = request.config.getoption("--golden-update")
    for name, img in _golden_render(mf, tmp_path).items():
        path = os.path.join(GOLDEN_DIR, "flyto", f"{name}.png")
        want = None
        if not update and os.path.isfile(path):
            with Image.open(path) as im:
                want = np.array(im.convert("RGBA"))
            dh, dw = img.shape[0] - want.shape[0], img.shape[1] - want.shape[1]
            if (dh, dw) != (0, 0) and abs(dh) <= 8 and abs(dw) <= 8:
                img = _center_fit(img, (want.shape[1], want.shape[0]))
        assert_golden(request, "flyto", name, img, ver=cache_mod._FLIGHT_VER,
                      tol_mean=1.0, tol_max=255)
        if want is None:
            continue
        bad, diff = _golden_violations(img, want)
        if bad:
            pytest.fail(
                f"金型 tests/golden/flyto/{name}.png と絵が違います（{bad} が閾値 "
                f"{_GOLDEN_TOL} を超えた。全体 {diff}）。意図した変更なら cache._FLIGHT_VER を"
                f"上げ、目視してから pytest tests/test_fly_to.py --golden-update")


def test_golden_check_catches_render_changes(tmp_path, monkeypatch):
    """金型の判定の線引き: 粒の入れ替わり（流れの出力の微小な雑音）は通し、
    描き方の変更（色・α の掛け方・止まる幅・粒の大きさ・出発の揺らぎ）は落とす"""
    mf = _mf()
    base = _golden_render(mf, tmp_path)

    def caught(**patches):
        with monkeypatch.context() as m:
            for key, value in patches.items():
                m.setattr(mf, key, value)
            frames = _golden_render(mf, tmp_path)
        return {name: _golden_violations(frames[name], base[name])[0] for name in base}

    flow = mf._sliced_ot_flow
    splat = mf._splat

    def noisy_flow(src, dst, rng, *args, **kwargs):
        z = flow(src, dst, rng, *args, **kwargs)
        return z + np.random.default_rng(99).normal(0.0, 0.01, z.shape)

    assert not any(caught(_sliced_ot_flow=noisy_flow).values())
    changes = {
        "粒を 5% 暗く": dict(_splat=lambda p, c, a, r: splat(p, c * 0.95, a, r)),
        "色に α が2回掛かる（旧版）": dict(
            _splat=lambda p, c, a, r: splat(p, c * a[:, None], a, r)),
        "止まる幅 0.12→0.2": dict(_SETTLE_WIDTH=0.2),
        "粒の半径 +0.25": dict(_splat=lambda p, c, a, r: splat(p, c, a, r + 0.25)),
        "出発の揺らぎ 0.15→0.25": dict(_STAGGER_JITTER=0.25),
    }
    for label, patches in changes.items():
        assert any(caught(**patches).values()), label
