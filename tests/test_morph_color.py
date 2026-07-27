# -*- coding: utf-8 -*-
"""モーフの色補間・マット合成の回帰テスト

紹介動画で不採用になった以下の見た目の欠陥を数値で固定する。

  (a) 遷移中盤が濁った緑茶色の塊になる
      → sRGBガンマ空間の線形ブレンドで輝度が両端より沈み、
        補色寄りの2色が灰色を通って退色していた
  (b) 形の内側に暗いノイズ塊が出る
      → 片方の画像の穴・非重複領域が α≈0.5 のまま残っていた

既定方式（sdf）と従来方式（transport）の両方で検証する。

numpy / scipy / opencv / Pillow が無い環境ではスキップする。
"""
import math

import numpy as np
import pytest

pytest.importorskip("cv2")
pytest.importorskip("scipy")
from PIL import Image, ImageDraw  # noqa: E402

morph = pytest.importorskip("scriptvedit.morph")


ORANGE = (255, 140, 26, 255)
TEAL = (26, 200, 200, 255)

# 各方式の追加パラメータ（transport はサンプル数を絞って高速化）
METHOD_PARAMS = {
    "sdf": {},
    "transport": {"max_pixels": 800},
}


def _srgb_to_linear(x):
    x = np.asarray(x, dtype=np.float64) / 255.0
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def _rel_luminance(rgb):
    lin = _srgb_to_linear(rgb)
    return float(0.2126 * lin[..., 0].mean() + 0.7152 * lin[..., 1].mean()
                 + 0.0722 * lin[..., 2].mean())


def _saturation(rgb):
    """HSV の S（0〜255）の平均"""
    rgb = np.asarray(rgb, dtype=np.float64)
    mx = rgb.max(axis=-1)
    mn = rgb.min(axis=-1)
    return float(np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9) * 255.0,
                          0.0).mean())


@pytest.fixture(scope="module")
def shapes(tmp_path_factory):
    """彩度の高い異なる色の図形2枚（オレンジの円 / 青緑の星）"""
    d = tmp_path_factory.mktemp("morph_color")
    a = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    ImageDraw.Draw(a).ellipse([10, 10, 117, 117], fill=ORANGE)
    pa = d / "a.png"
    a.save(pa)

    b = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        r = 58 if i % 2 == 0 else 24
        pts.append((64 + r * math.cos(ang), 64 + r * math.sin(ang)))
    ImageDraw.Draw(b).polygon(pts, fill=TEAL)
    pb = d / "b.png"
    b.save(pb)
    return str(pa), str(pb)


@pytest.fixture(scope="module")
def frames_by_method(shapes, tmp_path_factory):
    """方式ごとに5フレーム生成してキャッシュする"""
    out = {}
    for method, extra in METHOD_PARAMS.items():
        d = tmp_path_factory.mktemp(f"morph_frames_{method}")
        morph.generate_rgba_frames(shapes[0], shapes[1], str(d), 5,
                                   method=method, **extra)
        out[method] = [
            np.array(Image.open(d / f"frame_{i:05d}.png").convert("RGBA"))
            for i in range(5)
        ]
    return out


@pytest.mark.parametrize("method", list(METHOD_PARAMS))
def test_midframe_is_not_darker_than_endpoints(frames_by_method, method):
    """(a) 中間フレームの輝度が両端より沈まない（リニア光で合成している証拠）"""
    frames = frames_by_method[method]
    lums = [_rel_luminance(f[..., :3][f[..., 3] >= 250]) for f in frames]
    lo, hi = min(lums[0], lums[-1]), max(lums[0], lums[-1])
    for i in (1, 2, 3):
        assert lo - 0.02 <= lums[i] <= hi + 0.02, (
            f"[{method}] t={i / 4:.2f} の輝度 {lums[i]:.4f} が両端 "
            f"[{lo:.4f}, {hi:.4f}] から外れている（ガンマ空間ブレンドの兆候）")


@pytest.mark.parametrize("method", list(METHOD_PARAMS))
def test_midframe_keeps_saturation(frames_by_method, method):
    """(a) 中間フレームが灰色に退色しない（OKLChで色相を回している証拠）"""
    frames = frames_by_method[method]
    ends = min(_saturation(frames[0][..., :3][frames[0][..., 3] >= 250]),
               _saturation(frames[-1][..., :3][frames[-1][..., 3] >= 250]))
    mid = _saturation(frames[2][..., :3][frames[2][..., 3] >= 250])
    assert mid > ends * 0.5, (
        f"[{method}] 中間の彩度 {mid:.1f} が両端 {ends:.1f} に対して落ちすぎ"
        f"（RGB平均による退色の兆候）")


@pytest.mark.parametrize("method", list(METHOD_PARAMS))
def test_interior_has_no_semi_transparent_blob(frames_by_method, method):
    """(b) 形の内側に半透明の暗い塊が残らない"""
    import cv2
    frames = frames_by_method[method]
    for i in (1, 2, 3):
        a = frames[i][..., 3]
        solid = (a > 0).astype(np.uint8)
        closed = cv2.morphologyEx(solid, cv2.MORPH_CLOSE,
                                  np.ones((9, 9), np.uint8))
        inner = cv2.erode(closed, np.ones((7, 7), np.uint8)) > 0
        if inner.sum() == 0:
            continue
        ratio = float((a[inner] < 200).mean())
        assert ratio < 0.02, (
            f"[{method}] t={i / 4:.2f} で内側の {ratio:.1%} が α<200（暗い塊）")


@pytest.mark.parametrize("method", list(METHOD_PARAMS))
def test_last_frame_matches_target(frames_by_method, shapes, method):
    """末尾フレームがターゲット画像とほぼ一致する（切り替わりで跳ねない）"""
    b = np.array(Image.open(shapes[1]).convert("RGBA")).astype(np.float64)
    last = frames_by_method[method][-1].astype(np.float64)

    def over_black(x):
        return x[..., :3] * (x[..., 3:4] / 255.0)

    assert np.abs(over_black(last) - over_black(b)).mean() < 1.0


@pytest.mark.parametrize("method", list(METHOD_PARAMS))
def test_full_bleed_alpha_is_monotonic(tmp_path, method):
    """全面不透明（写真・背景カード等）→ アイコンでアルファが単調に減る

    距離場マットの実装が「境界の無いマットに定数の番兵値を返して線形補間する」
    と、et の全域で飽和してキャンバス全体が不透明のまま固まり、
    途中で急に抜ける（黒落ちより目立つポップになる）。
    退化ケースを線形ディゾルブへ逃がしていることの回帰テスト。
    """
    a = Image.new("RGBA", (96, 96), (200, 60, 40, 255))  # 全面不透明
    pa = tmp_path / "full.png"
    a.save(pa)
    b = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    ImageDraw.Draw(b).ellipse([30, 30, 65, 65], fill=(40, 120, 220, 255))
    pb = tmp_path / "icon.png"
    b.save(pb)

    out = tmp_path / "frames"
    morph.generate_rgba_frames(str(pa), str(pb), str(out), 7,
                               method=method, **METHOD_PARAMS[method])
    means = [float(np.array(Image.open(out / f"frame_{i:05d}.png")
                            .convert("RGBA"))[..., 3].mean())
             for i in range(7)]
    for i in range(len(means) - 1):
        assert means[i + 1] <= means[i] + 1.0, (
            f"[{method}] アルファが増加している: {means}")
    assert means[0] > means[-1], f"[{method}] 全く減っていない: {means}"


@pytest.mark.parametrize("method", list(METHOD_PARAMS))
def test_full_bleed_alpha_has_no_plateau(tmp_path, method):
    """全面不透明 → アイコンで「途中まで不透明のまま固まる」ことがない

    距離場は輪郭を持たないマット（全透明/全不透明）には定義できない。
    番兵値を実距離場と線形補間すると符号が飽和し、中盤まで不透明のまま
    固まってから急に抜ける（黒落ちより目立つポップ）。
    """
    a = Image.new("RGBA", (96, 96), (200, 60, 40, 255))
    pa = tmp_path / "full.png"
    a.save(pa)
    b = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    ImageDraw.Draw(b).ellipse([30, 30, 65, 65], fill=(40, 120, 220, 255))
    pb = tmp_path / "icon.png"
    b.save(pb)

    out = tmp_path / "frames"
    n = 9
    morph.generate_rgba_frames(str(pa), str(pb), str(out), n,
                               method=method, **METHOD_PARAMS[method])
    means = [float(np.array(Image.open(out / f"frame_{i:05d}.png")
                            .convert("RGBA"))[..., 3].mean())
             for i in range(n)]
    span = means[0] - means[-1]
    # 1フレームの落差が全体の 4 割を超える＝どこかで固まって急落している
    drops = [means[i] - means[i + 1] for i in range(n - 1)]
    assert max(drops) < span * 0.4, (
        f"[{method}] アルファが急落している（固まってからポップ）: {means}")


def test_sdf_keeps_soft_material_soft(tmp_path):
    """ソフトシャドウ/グローが中間フレームで硬いエッジに作り直されない

    符号付き距離場は α=0.5 の等高線しか持たないため、柔らかい階調を
    そのまま扱うと 1px の硬い縁になってしまう。柔らかい素材は
    線形ディゾルブへフォールバックすることの回帰テスト。
    """
    from PIL import ImageFilter
    soft = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    ImageDraw.Draw(soft).ellipse([25, 25, 70, 70], fill=(60, 120, 255, 255))
    soft = soft.filter(ImageFilter.GaussianBlur(9))
    p = tmp_path / "soft.png"
    soft.save(p)

    out = tmp_path / "frames"
    morph.generate_rgba_frames(str(p), str(p), str(out), 5, method="sdf")
    alphas = [np.array(Image.open(out / f"frame_{i:05d}.png")
                       .convert("RGBA"))[..., 3].astype(np.float64)
              for i in range(5)]
    base = alphas[0]
    for i in (1, 2, 3):
        # 同じ画像どうしのモーフなので、中間も元のマットと一致すべき
        assert np.abs(alphas[i] - base).mean() < 2.0, (
            f"t={i / 4:.2f} でソフトなマットが硬化している "
            f"(平均差 {np.abs(alphas[i] - base).mean():.1f})")


def test_color_space_roundtrip():
    """sRGB ⇔ リニア光 ⇔ OKLab の往復で色がずれない"""
    rgb = np.array([[[0.0, 0.0, 0.0], [1.0, 1.0, 1.0],
                     [1.0, 0.55, 0.1], [0.1, 0.78, 0.78]]], dtype=np.float32)
    back = morph.linear_to_srgb(morph.srgb_to_linear(rgb))
    assert np.allclose(back, rgb, atol=1e-4)
    lin = morph.srgb_to_linear(rgb)
    assert np.allclose(
        morph.oklab_to_linear_rgb(morph.linear_rgb_to_oklab(lin)),
        lin, atol=1e-4)


def test_default_method_is_sdf():
    """既定方式は sdf（中間シルエットが滑らかな方）"""
    assert morph.DEFAULT_MORPH_METHOD == "sdf"


def test_transport_params_select_transport(shapes, tmp_path):
    """method 未指定 + transport 専用パラメータ → transport が選ばれる

    既定を sdf にしたことで、旧来の morph_to(x, max_pixels=...) が
    「使えないパラメータ」エラーで壊れないことの回帰テスト。
    """
    morph.generate_rgba_frames(shapes[0], shapes[1], str(tmp_path), 2,
                               max_pixels=400)
    assert (tmp_path / "frame_00001.png").exists()


def test_unknown_param_still_rejected(shapes, tmp_path):
    """既存どおり未知パラメータはエラー（タイポ検出）"""
    with pytest.raises(ValueError):
        morph.generate_rgba_frames(shapes[0], shapes[1], str(tmp_path), 2,
                                   colour_mix="oklch")


def test_invalid_color_mix_rejected(shapes, tmp_path):
    with pytest.raises(ValueError):
        morph.generate_rgba_frames(shapes[0], shapes[1], str(tmp_path), 2,
                                   method="transport", color_mix="hsv")


def test_wrong_method_param_rejected(shapes, tmp_path):
    """method に対応しないパラメータは method 名付きでエラーになる"""
    with pytest.raises(ValueError, match="sdf"):
        morph.generate_rgba_frames(shapes[0], shapes[1], str(tmp_path), 2,
                                   method="sdf", max_pixels=400)
