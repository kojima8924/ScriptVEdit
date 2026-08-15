# -*- coding: utf-8 -*-
"""scriptvedit.testkit — レンダリング結果の視覚検証ヘルパー

scriptvedit で生成した動画のフレームを抽出し、期待画像と SSIM で比較する
テスト用ユーティリティ。依存は numpy + PIL + ffmpeg(サブプロセス)のみ。
scipy があれば SSIM の窓フィルタに scipy.ndimage.uniform_filter を使用する
(無くても numpy 実装にフォールバック)。

主な API:
    extract_frame(video_path, at, out_png=None) -> np.ndarray
    ssim(img_a, img_b) -> float
    frame_diff(img_a, img_b, out_png=None) -> dict
    assert_frame(video_path, at, expected, *, threshold=0.97, save_actual=None)
    assert_frames(video_path, expectations, **kw)

CLI:
    python -m scriptvedit.testkit compare a.png b.png
    python -m scriptvedit.testkit frame video.mp4 2.5 -o out.png
"""

import os
import sys
import subprocess
import tempfile

import numpy as np
from PIL import Image

__all__ = [
    "extract_frame",
    "ssim",
    "frame_diff",
    "assert_frame",
    "assert_frames",
]

# scipy は任意依存(あれば高速な uniform_filter を使う)
try:
    from scipy.ndimage import uniform_filter as _scipy_uniform_filter
except ImportError:  # pragma: no cover - 環境依存
    _scipy_uniform_filter = None


# ---------------------------------------------------------------------------
# 内部ユーティリティ
# ---------------------------------------------------------------------------

def _load_image(img):
    """numpy 配列またはパスを RGB の uint8 numpy 配列 (H, W, 3) に正規化する。

    - パス(str / os.PathLike)なら PIL で読み込み RGB へ変換
    - numpy 配列ならグレースケール (H, W) / RGBA (H, W, 4) も RGB に揃える
    """
    if isinstance(img, (str, os.PathLike)):
        path = os.fspath(img)
        if not os.path.exists(path):
            raise FileNotFoundError(f"画像ファイルが見つかりません: {path}")
        with Image.open(path) as im:
            return np.asarray(im.convert("RGB"), dtype=np.uint8)
    arr = np.asarray(img)
    if arr.ndim == 2:
        # グレースケール → RGB に複製
        arr = np.stack([arr] * 3, axis=-1)
    elif arr.ndim == 3 and arr.shape[2] == 4:
        # RGBA → アルファを無視して RGB
        arr = arr[:, :, :3]
    elif arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(
            f"画像は (H, W), (H, W, 3), (H, W, 4) のいずれかである必要があります: shape={arr.shape}"
        )
    if arr.dtype != np.uint8:
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    return arr


def _to_gray(rgb):
    """RGB uint8 配列を ITU-R BT.601 の輝度(float64)に変換する。"""
    rgb = rgb.astype(np.float64)
    return 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]


def _uniform_filter(img, size):
    """2次元の均一(ボックス)フィルタ。scipy があれば委譲、無ければ積分画像で計算。

    numpy フォールバックは reflect パディング + 積分画像(cumsum)により
    scipy.ndimage.uniform_filter(mode='reflect') と同等の結果を返す。
    """
    if _scipy_uniform_filter is not None:
        return _scipy_uniform_filter(img, size=size, mode="reflect")
    # numpy 実装: 反射パディング後に積分画像で窓平均を求める
    r = size // 2
    # 奇数サイズ前提(呼び出し側で保証)。左右上下に r 画素ずつ反射パディング
    # (scipy の mode='reflect' は np.pad の 'symmetric' に相当する点に注意)
    padded = np.pad(img, r, mode="symmetric")
    # 積分画像(先頭に0行/0列を足して差分計算を単純化)
    integral = np.zeros((padded.shape[0] + 1, padded.shape[1] + 1), dtype=np.float64)
    integral[1:, 1:] = np.cumsum(np.cumsum(padded, axis=0), axis=1)
    h, w = img.shape
    # 各画素を中心とする size x size 窓の合計
    s = (
        integral[size:size + h, size:size + w]
        - integral[0:h, size:size + w]
        - integral[size:size + h, 0:w]
        + integral[0:h, 0:w]
    )
    return s / (size * size)


def _check_ffmpeg():
    """ffmpeg コマンドが見つからない場合に分かりやすいエラーを出す。"""
    import shutil
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg が見つかりません。PATH に ffmpeg を追加してください。"
        )


def _ensure_parent(path):
    """保存先の親ディレクトリを作成し、正規化したパス文字列を返す。

    親が存在しなくても作成するため、どの分岐から呼んでも
    FileNotFoundError にならない。
    """
    target = os.fspath(path)
    parent = os.path.dirname(os.path.abspath(target))
    os.makedirs(parent, exist_ok=True)
    return target


def _save_actual(actual, save_actual):
    """失敗時のデバッグ用に実フレームを PNG 保存する。

    save_actual が None の場合は何もしない。
    """
    if save_actual is None:
        return
    target = _ensure_parent(save_actual)
    Image.fromarray(actual, mode="RGB").save(target)


# ---------------------------------------------------------------------------
# フレーム抽出
# ---------------------------------------------------------------------------

def extract_frame(video_path, at, out_png=None, *, accurate=True):
    """動画の指定時刻のフレームを RGB numpy 配列 (H, W, 3) として取得する。

    Args:
        video_path: 動画ファイルのパス
        at: 抽出時刻(秒)。float 可
        out_png: 指定した場合、フレームを PNG としてこのパスにも保存する
        accurate: シーク精度と速度のトレードオフ(既定 True)。
            True  → 出力側シーク(-i の後に -ss)。ffmpeg が対象時刻まで
                    デコードするため start_time>0 や VFR でも指定時刻の
                    フレームを正確に取得できる(やや低速)。
            False → 入力側シーク(-i の前に -ss)。キーフレームへ高速に
                    シークするが、start_time>0/VFR で 1 フレームずれ得る。

    Returns:
        np.ndarray: uint8 RGB 配列 (H, W, 3)

    Raises:
        FileNotFoundError: 動画が存在しない場合
        RuntimeError: ffmpeg が失敗した場合(時刻が動画長を超えた場合など)
    """
    video_path = os.fspath(video_path)
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"動画ファイルが見つかりません: {video_path}")
    _check_ffmpeg()

    if out_png is not None:
        target = _ensure_parent(out_png)
        tmp_path = None
    else:
        # 一時 PNG を作って読み込み後に削除(Windows のファイルロックを避けるため
        # NamedTemporaryFile は使わず mkstemp で fd を即クローズする)
        fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="svtk_")
        os.close(fd)
        target = tmp_path

    if accurate:
        # 出力側シーク: 精度重視(指定時刻まで実デコード)
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", video_path,
            "-ss", f"{float(at):.6f}",
            "-frames:v", "1",
            "-y", target,
        ]
    else:
        # 入力側シーク: 速度重視(キーフレーム基準、1フレームずれ得る)
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", f"{float(at):.6f}",
            "-i", video_path,
            "-frames:v", "1",
            "-y", target,
        ]
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        if result.returncode != 0 or not os.path.exists(target) or os.path.getsize(target) == 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"フレーム抽出に失敗しました (video={video_path}, at={at}s)。"
                f"時刻が動画の長さを超えていないか確認してください。\n"
                f"ffmpeg stderr: {stderr}"
            )
        with Image.open(target) as im:
            frame = np.asarray(im.convert("RGB"), dtype=np.uint8)
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            os.remove(tmp_path)
    return frame


# ---------------------------------------------------------------------------
# SSIM
# ---------------------------------------------------------------------------

def ssim(img_a, img_b, *, win_size=7):
    """2枚の画像のグレースケール SSIM(構造的類似度)を計算する。

    入力は numpy 配列(RGB/RGBA/グレースケール)または画像ファイルのパス。
    内部で BT.601 輝度に変換し、均一窓(サイズ win_size)で局所統計を取る。

    Args:
        img_a, img_b: 比較する画像(配列またはパス)
        win_size: 局所窓のサイズ(奇数)

    Returns:
        float: SSIM 値(-1.0〜1.0、同一画像なら 1.0)

    Raises:
        ValueError: 画像サイズが一致しない場合、win_size が不正な場合
    """
    a = _to_gray(_load_image(img_a))
    b = _to_gray(_load_image(img_b))
    if a.shape != b.shape:
        raise ValueError(
            f"画像サイズが一致しません: {a.shape[1]}x{a.shape[0]} と "
            f"{b.shape[1]}x{b.shape[0]}。同じ解像度の画像を指定してください。"
        )
    if win_size % 2 == 0 or win_size < 3:
        raise ValueError(f"win_size は 3 以上の奇数を指定してください: {win_size}")
    if min(a.shape) < win_size:
        raise ValueError(
            f"画像が小さすぎます(最小 {win_size}x{win_size} 必要): {a.shape}"
        )

    # SSIM 定数 (K1=0.01, K2=0.03, L=255)
    c1 = (0.01 * 255.0) ** 2
    c2 = (0.03 * 255.0) ** 2

    mu_a = _uniform_filter(a, win_size)
    mu_b = _uniform_filter(b, win_size)
    mu_a2 = mu_a * mu_a
    mu_b2 = mu_b * mu_b
    mu_ab = mu_a * mu_b

    # 分散・共分散(標本補正なしの単純平均)
    sigma_a2 = _uniform_filter(a * a, win_size) - mu_a2
    sigma_b2 = _uniform_filter(b * b, win_size) - mu_b2
    sigma_ab = _uniform_filter(a * b, win_size) - mu_ab

    ssim_map = ((2.0 * mu_ab + c1) * (2.0 * sigma_ab + c2)) / (
        (mu_a2 + mu_b2 + c1) * (sigma_a2 + sigma_b2 + c2)
    )
    return float(ssim_map.mean())


# ---------------------------------------------------------------------------
# 差分統計・ヒートマップ
# ---------------------------------------------------------------------------

def _heatmap_colorize(norm):
    """0.0〜1.0 の強度マップを黒→赤→黄→白のヒートマップ RGB に変換する。"""
    x = np.clip(norm, 0.0, 1.0)
    r = np.clip(x * 3.0, 0.0, 1.0)
    g = np.clip(x * 3.0 - 1.0, 0.0, 1.0)
    b = np.clip(x * 3.0 - 2.0, 0.0, 1.0)
    return (np.stack([r, g, b], axis=-1) * 255.0).astype(np.uint8)


def frame_diff(img_a, img_b, out_png=None, *, change_threshold=2):
    """2枚の画像の差分統計を計算する。

    Args:
        img_a, img_b: 比較する画像(配列またはパス)
        out_png: 指定した場合、差分のヒートマップ画像(黒→赤→黄→白)を保存
        change_threshold: 「変化した」とみなす画素差の閾値(0-255、RGB最大差で判定)

    Returns:
        dict: {
            "mean_abs":  平均絶対差(全チャンネル平均、0-255),
            "max_abs":   最大絶対差(0-255),
            "diff_ratio": 変化画素率(RGB いずれかの差 > change_threshold の画素の割合),
        }

    Raises:
        ValueError: 画像サイズが一致しない場合
    """
    a = _load_image(img_a).astype(np.int16)
    b = _load_image(img_b).astype(np.int16)
    if a.shape != b.shape:
        raise ValueError(
            f"画像サイズが一致しません: {a.shape[1]}x{a.shape[0]} と "
            f"{b.shape[1]}x{b.shape[0]}。同じ解像度の画像を指定してください。"
        )
    diff = np.abs(a - b)  # (H, W, 3) int16
    per_pixel_max = diff.max(axis=2)  # 画素ごとの RGB 最大差

    stats = {
        "mean_abs": float(diff.mean()),
        "max_abs": float(diff.max()),
        "diff_ratio": float((per_pixel_max > change_threshold).mean()),
    }

    if out_png is not None:
        target = _ensure_parent(out_png)
        # 差分を 0-255 → 0-1 に正規化してヒートマップ化
        heat = _heatmap_colorize(per_pixel_max.astype(np.float64) / 255.0)
        Image.fromarray(heat, mode="RGB").save(target)

    return stats


# ---------------------------------------------------------------------------
# アサーション
# ---------------------------------------------------------------------------

def assert_frame(video_path, at, expected, *, threshold=0.97, save_actual=None):
    """動画の指定時刻のフレームが期待画像と一致(SSIM >= threshold)することを検証する。

    Args:
        video_path: 動画ファイルのパス
        at: 検証時刻(秒)
        expected: 期待画像の PNG パス(または numpy 配列)
        threshold: 合格とみなす SSIM の下限(既定 0.97)
        save_actual: 失敗時に実際のフレームを保存するパス(デバッグ用)

    Returns:
        float: 実測 SSIM 値(成功時)

    Raises:
        AssertionError: SSIM が threshold 未満の場合(実測値とヒント付き)
    """
    actual = extract_frame(video_path, at)
    expected_img = _load_image(expected)

    if actual.shape != expected_img.shape:
        _save_actual(actual, save_actual)
        raise AssertionError(
            f"フレームサイズが期待画像と一致しません "
            f"(実際: {actual.shape[1]}x{actual.shape[0]}, "
            f"期待: {expected_img.shape[1]}x{expected_img.shape[0]})。"
            f"レンダリング解像度と期待画像の解像度を確認してください。"
            + (f" 実フレームを保存しました: {save_actual}" if save_actual else "")
        )

    score = ssim(actual, expected_img)
    if score < threshold:
        _save_actual(actual, save_actual)
        d = frame_diff(actual, expected_img)
        expected_desc = (
            os.fspath(expected) if isinstance(expected, (str, os.PathLike)) else "<ndarray>"
        )
        raise AssertionError(
            f"フレーム検証に失敗しました: t={at}s の SSIM={score:.4f} < threshold={threshold}\n"
            f"  動画: {os.fspath(video_path)}\n"
            f"  期待画像: {expected_desc}\n"
            f"  差分統計: mean_abs={d['mean_abs']:.2f}, max_abs={d['max_abs']:.0f}, "
            f"diff_ratio={d['diff_ratio']:.1%}\n"
            f"  ヒント: タイミングのずれなら at を前後にずらして確認、"
            f"意図した変更なら期待画像を更新してください。"
            + (f"\n  実フレームを保存しました: {save_actual}" if save_actual else "")
        )
    return score


def assert_frames(video_path, expectations, **kw):
    """複数時刻のフレーム検証を一括で行う。

    Args:
        video_path: 動画ファイルのパス
        expectations: [(at, expected_png), ...] のリスト
        **kw: assert_frame に渡す追加引数(threshold, save_actual など)。
            save_actual を指定した場合、時刻ごとに "_1.20s" のようなサフィックスを
            付けて保存する

    Returns:
        list[float]: 各時刻の実測 SSIM 値

    Raises:
        AssertionError: いずれかの時刻で検証に失敗した場合
            (全時刻を検証してから、失敗をまとめて報告する)
    """
    save_actual_base = kw.pop("save_actual", None)
    scores = []
    failures = []
    for at, expected in expectations:
        save_actual = None
        if save_actual_base is not None:
            root, ext = os.path.splitext(os.fspath(save_actual_base))
            save_actual = f"{root}_{float(at):.2f}s{ext or '.png'}"
        try:
            scores.append(assert_frame(video_path, at, expected,
                                       save_actual=save_actual, **kw))
        except AssertionError as e:
            failures.append(str(e))
            scores.append(None)
    if failures:
        raise AssertionError(
            f"{len(failures)}/{len(expectations)} 件のフレーム検証に失敗しました:\n\n"
            + "\n\n".join(failures)
        )
    return scores


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        prog="scriptvedit.testkit",
        description="レンダリング結果の視覚検証ヘルパー(SSIM 比較・フレーム抽出)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_cmp = sub.add_parser("compare", help="2枚の画像を SSIM 比較する")
    p_cmp.add_argument("img_a", help="画像A のパス")
    p_cmp.add_argument("img_b", help="画像B のパス")
    p_cmp.add_argument("-o", "--out", default=None,
                       help="差分ヒートマップ PNG の保存先")

    p_frame = sub.add_parser("frame", help="動画からフレームを抽出する")
    p_frame.add_argument("video", help="動画ファイルのパス")
    p_frame.add_argument("at", type=float, help="抽出時刻(秒)")
    p_frame.add_argument("-o", "--out", default=None,
                         help="保存先 PNG(省略時は frame_{at}s.png)")

    args = parser.parse_args(argv)

    try:
        if args.command == "compare":
            score = ssim(args.img_a, args.img_b)
            stats = frame_diff(args.img_a, args.img_b, out_png=args.out)
            print(f"SSIM       : {score:.6f}")
            print(f"mean_abs   : {stats['mean_abs']:.4f}")
            print(f"max_abs    : {stats['max_abs']:.0f}")
            print(f"diff_ratio : {stats['diff_ratio']:.2%}")
            if args.out:
                print(f"差分ヒートマップを保存しました: {args.out}")
            return 0

        if args.command == "frame":
            out = args.out or f"frame_{args.at:g}s.png"
            frame = extract_frame(args.video, args.at, out_png=out)
            print(f"フレームを保存しました: {out} ({frame.shape[1]}x{frame.shape[0]})")
            return 0
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        # CLI ではトレースバックを出さず、整形したエラーメッセージのみ表示する
        print(f"エラー: {e}", file=sys.stderr)
        return 2

    return 1  # 到達しない


if __name__ == "__main__":
    sys.exit(_main())
