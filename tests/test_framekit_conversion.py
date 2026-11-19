# -*- coding: utf-8 -*-
"""透過画素を含む事前乗算 RGBA の変換回帰。OpenCV の 0 除算の挙動に依存しない。"""

import pytest

np = pytest.importorskip("numpy", reason="numpy が無い環境")
pytest.importorskip("cv2", reason="numpy・opencv が無い環境")
pytest.importorskip("PIL", reason="Pillow が無い環境")

from scriptvedit import framekit as fk  # noqa: E402


def test_to_rgba8_clears_holes_and_subpixel_alpha_without_changing_colors():
    """描画範囲の内側の透明な穴・8bit で消える薄い画素は RGBA 全部が 0。"""
    straight = np.array([
        [[20, 40, 60, 255], [0, 0, 0, 0], [240, 120, 30, 128]],
        [[255, 255, 255, 255], [0, 0, 0, 0], [80, 160, 240, 64]],
    ], dtype=np.uint8)
    prem = straight.astype(np.float32) / np.float32(255.0)
    prem[..., :3] *= prem[..., 3:4]
    # 8bit のアルファが 0 に丸まる有色の画素。完全な 0 除算以外の境界も確認する。
    prem[1, 1] = np.array([1.0, 0.5, 0.25, 1.0], np.float32) * np.float32(0.25 / 255.0)
    before = prem.copy()

    actual = fk.to_rgba8(prem)

    assert np.array_equal(actual, straight)
    assert np.array_equal(prem, before)


def test_to_rgba8_keeps_the_lowest_visible_alpha():
    """透明な画素を消す処理が、アルファ 1/255 の有色画素まで消さない。"""
    straight = np.array([[[255, 128, 0, 1], [0, 0, 0, 0], [0, 64, 255, 2]]], dtype=np.uint8)
    prem = straight.astype(np.float32) / np.float32(255.0)
    prem[..., :3] *= prem[..., 3:4]

    assert np.array_equal(fk.to_rgba8(prem), straight)
