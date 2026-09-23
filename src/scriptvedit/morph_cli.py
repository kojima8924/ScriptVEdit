"""
scriptvedit.morph_cli - morph の CLI（動画書き出し / argparse）

scriptvedit 本体のレンダ経路はこのモジュールを一切通らない。本体が使うのは
morph.generate_rgba_frames / generate_explode_frames / generate_assemble_frames
が出す RGBA PNG 連番で、それを project 側のチェックポイント段で ffmpeg に
渡している。ここにある cv2.VideoWriter("mp4v") 経由の mp4 書き出しは
コマンドラインから単体で使うときだけの経路であり、ライブラリのレンダ結果には
影響しない。

使い方（起動口は従来どおり scriptvedit.morph）:
    python -m scriptvedit.morph a.png b.png -o output.mp4
    python -m scriptvedit.morph a.png b.png -o output.mp4 --method transport
    python -m scriptvedit.morph explode in.png frames_dir/
    python -m scriptvedit.morph assemble in.png frames_dir/
"""

import argparse
import sys

import numpy as np
import cv2
from tqdm import tqdm

from scriptvedit.morph import (
    ALPHA_MODES,
    COLOR_MIX_MODES,
    DEFAULT_MORPH_METHOD,
    MORPH_METHODS,
    _blend_settings,
    _compose_morph,
    _prepare_morph,
    _prepare_sdf_morph,
    _sdf_morph_frame,
    ease_in_out,
    generate_assemble_frames,
    generate_explode_frames,
    linear_premultiply,
    linear_to_srgb,
    srgb_to_linear,
)


# ============================================================
# 動画書き出し（CLI 専用）
# ============================================================

def _create_video_sdf(path_a, path_b, output_path, *, fps, duration,
                      bg_color, sdf_params):
    """method="sdf" の動画書き出し（背景合成あり）"""
    ctx = _prepare_sdf_morph(path_a, path_b, **sdf_params)
    w, h = ctx["canvas"]
    bg = np.array(bg_color, dtype=np.float32).reshape(1, 1, 3)

    num_frames = int(fps * duration)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output_path, fourcc, fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError("VideoWriterを開けません")

    print(f"[SDF] レンダリング: {num_frames}フレーム, {w}x{h}")
    try:
        for i in tqdm(range(num_frames), desc="レンダリング"):
            t = i / max(num_frames - 1, 1)
            et = ease_in_out(t)
            rgba = _sdf_morph_frame(ctx, et, et).astype(np.float32)
            alpha = rgba[:, :, 3:4] / 255.0
            # over 合成（ストレートアルファ）
            frame = rgba[:, :, :3] * alpha + bg * (1.0 - alpha)
            frame_bgr = np.clip(frame[:, :, ::-1], 0, 255).astype(np.uint8)
            ok = writer.write(frame_bgr)
            if ok is False:
                raise RuntimeError(f"フレーム{i}の書き込みに失敗しました")
    finally:
        writer.release()
    print(f"完了: {output_path}")


def create_video(path_a, path_b, output_path, *,
                 max_pixels=2000, fps=30, duration=3.0,
                 w_move=1.0, w_color=0.3, w_vanish=1.5,
                 grid_step=8, smoothing=10.0,
                 color_metric="oklab", color_mix="oklch",
                 color_local=0.0, alpha_mode="sdf", alpha_sharp=0.0,
                 bg_color=(0, 0, 0),
                 method=DEFAULT_MORPH_METHOD, align=True, edge_softness=1.0,
                 color_ease=1, color_path="oklch"):
    """モーフィング動画を生成

    method="sdf"（既定）のときは align / edge_softness / color_ease /
    color_path が、method="transport" のときは max_pixels 以下の
    最適輸送系・色合成系パラメータが効く。
    """
    if method not in MORPH_METHODS:
        raise ValueError(
            f"未知の method: {method!r}（有効値: {list(MORPH_METHODS)}）")
    if method == "sdf":
        _create_video_sdf(
            path_a, path_b, output_path, fps=fps, duration=duration,
            bg_color=bg_color,
            sdf_params=dict(align=align, edge_softness=edge_softness,
                            color_ease=color_ease, color_path=color_path),
        )
        return

    cfg = _blend_settings(color_mix=color_mix, color_local=color_local,
                          alpha_mode=alpha_mode, alpha_sharp=alpha_sharp)

    # --- 1〜4. 読み込み→抽出→最適輸送→ワープ場構築（共通処理） ---
    arr_a, arr_b, canvas, dx_s, dy_s, dx_t, dy_t = _prepare_morph(
        path_a, path_b,
        max_pixels=max_pixels, w_move=w_move, w_color=w_color,
        w_vanish=w_vanish, grid_step=grid_step, smoothing=smoothing,
        color_metric=color_metric,
    )
    w, h = canvas

    # --- 5. 動画レンダリング ---
    # 事前計算（ワープ・合成はリニア光 × 事前乗算）
    src_pm = linear_premultiply(arr_a)
    tgt_pm = linear_premultiply(arr_b)
    ident_x, ident_y = np.meshgrid(
        np.arange(w, dtype=np.float32),
        np.arange(h, dtype=np.float32),
    )
    # 背景色もリニア光に変換してから合成する
    bg = srgb_to_linear(
        np.array(bg_color, dtype=np.float32).reshape(1, 1, 3) / 255.0)

    num_frames = int(fps * duration)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output_path, fourcc, fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError("VideoWriterを開けません")

    print(f"[5/5] レンダリング: {num_frames}フレーム, {w}x{h}")
    try:
        for i in tqdm(range(num_frames), desc="レンダリング"):
            t = i / max(num_frames - 1, 1)
            et = ease_in_out(t)

            # 注意: 後方ワープ（出力座標基準の参照）に、ソース点で評価した
            # 前方基準の変位場をそのまま流用する近似。変位が大きい場合は
            # 参照位置がずれ、にじみ・ゴーストが出ることがある。

            # ソース画像をワープ（後方写像: 出力(x,y) ← ソース(x-et*dx, y-et*dy)）
            mx_s = ident_x - et * dx_s
            my_s = ident_y - et * dy_s
            ws = cv2.remap(src_pm, mx_s, my_s, cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))

            # ターゲット画像を逆ワープ（t=0で最大変形、t=1で元に戻る）
            mx_t = ident_x - (1.0 - et) * dx_t
            my_t = ident_y - (1.0 - et) * dy_t
            wt = cv2.remap(tgt_pm, mx_t, my_t, cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))

            # 中間色の生成（リニア光 + OKLCh、非重複領域は最近傍色で補完）
            color, alpha = _compose_morph(ws, wt, et, cfg)

            # 背景合成（over合成: fg*α + bg*(1-α)）をリニア光で行い sRGB へ戻す
            frame = linear_to_srgb(color * alpha + bg * (1.0 - alpha)) * 255.0
            # RGB → BGR に変換して書き出し
            frame_bgr = np.clip(frame[:, :, ::-1] + 0.5, 0, 255).astype(np.uint8)
            # OpenCVのバージョンにより返値はNone/bool（Falseなら書き込み失敗）
            ok = writer.write(frame_bgr)
            if ok is False:
                raise RuntimeError(f"フレーム{i}の書き込みに失敗しました")
    finally:
        writer.release()
    print(f"完了: {output_path}")


def _particle_cli(mode, argv):
    """explode / assemble サブコマンドの CLI 処理"""
    p = argparse.ArgumentParser(
        prog=f"morph.py {mode}",
        description=("画像を粒子化して飛散させる連番PNG生成" if mode == "explode"
                     else "飛散状態から集合して画像になる連番PNG生成"),
    )
    p.add_argument("image", help="入力画像（PNG）")
    p.add_argument("out_dir", help="出力ディレクトリ（frame_00000.png 〜）")
    p.add_argument("--frames", type=int, default=30,
                   help="フレーム数（デフォルト: 30）")
    p.add_argument("--max-pixels", type=int, default=2000,
                   help="粒子数の上限（デフォルト: 2000）")
    p.add_argument("--speed", type=float, default=200.0,
                   help="放射方向の初速度スケール（デフォルト: 200）")
    p.add_argument("--gravity", type=float, default=300.0,
                   help="重力加速度（デフォルト: 300、+yが下方向）")
    p.add_argument("--spread", type=float, default=1.0,
                   help="初速度のランダム散らばり係数（デフォルト: 1.0）")
    p.add_argument("--swirl", type=float, default=0.0,
                   help="重心まわりの回転角速度 [rad]（デフォルト: 0）")
    p.add_argument("--particle-size", type=int, default=2,
                   help="粒子の半径 [px]（デフォルト: 2）")
    p.add_argument("--seed", type=int, default=42,
                   help="乱数シード（デフォルト: 42）")
    p.add_argument("--dissolve", type=float, default=0.25,
                   help="元画像→粒子のクロスフェード区間（デフォルト: 0.25）")
    p.add_argument("--expand", type=int, default=0,
                   help="キャンバスの透明マージン [px]（デフォルト: 0）")
    a = p.parse_args(argv)

    fn = generate_explode_frames if mode == "explode" else generate_assemble_frames
    fn(a.image, a.out_dir, a.frames,
       max_pixels=a.max_pixels, speed=a.speed, gravity=a.gravity,
       spread=a.spread, swirl=a.swirl, particle_size=a.particle_size,
       seed=a.seed, dissolve=a.dissolve, expand=a.expand)


def main():
    # explode / assemble サブコマンド（既存のモーフィングCLIとは独立）
    if len(sys.argv) > 1 and sys.argv[1] in ("explode", "assemble"):
        _particle_cli(sys.argv[1], sys.argv[2:])
        return

    p = argparse.ArgumentParser(
        description="最適輸送 + ワープ場によるモーフィング動画生成"
    )
    p.add_argument("image_a", help="入力画像A（PNG）")
    p.add_argument("image_b", help="入力画像B（PNG）")
    p.add_argument("-o", "--output", default="morph.mp4",
                   help="出力動画パス（デフォルト: morph.mp4）")
    p.add_argument("--max-pixels", type=int, default=2000,
                   help="OT計算の最大ピクセル数（デフォルト: 2000）")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--duration", type=float, default=3.0,
                   help="秒数（デフォルト: 3.0）")
    p.add_argument("--w-move", type=float, default=1.0,
                   help="移動コストの重み")
    p.add_argument("--w-color", type=float, default=0.3,
                   help="色変化コストの重み")
    p.add_argument("--w-vanish", type=float, default=1.5,
                   help="消滅/出現コストの重み")
    p.add_argument("--grid-step", type=int, default=8,
                   help="ワープ場グリッド間隔（小さいほど精密、デフォルト: 8）")
    p.add_argument("--smoothing", type=float, default=10.0,
                   help="RBF補間の滑らかさ（大きいほど滑らか、デフォルト: 10）")
    p.add_argument("--color-metric", choices=("oklab", "rgba"), default="oklab",
                   help="[transport] 輸送コストの色距離（デフォルト: oklab）")
    p.add_argument("--color-mix", choices=COLOR_MIX_MODES, default="oklch",
                   help="[transport] 中間色の作り方（デフォルト: oklch）")
    p.add_argument("--color-local", type=float, default=0.0,
                   help="[transport] 色の混合比を被覆率で重み付けする度合い 0〜1")
    p.add_argument("--alpha-mode", choices=ALPHA_MODES, default="sdf",
                   help="[transport] アルファの混ぜ方（デフォルト: sdf）")
    p.add_argument("--alpha-sharp", type=float, default=0.0,
                   help="[transport] dissolve時にアルファを硬くする度合い 0〜0.9")
    p.add_argument("--bg", type=int, nargs=3, default=[0, 0, 0],
                   metavar=("R", "G", "B"), help="背景色")
    p.add_argument("--method", choices=list(MORPH_METHODS),
                   default=DEFAULT_MORPH_METHOD,
                   help=f"モーフ方式（sdf=形状ベース / transport=最適輸送。"
                        f"デフォルト: {DEFAULT_MORPH_METHOD}）")
    p.add_argument("--no-align", dest="align", action="store_false",
                   help="[sdf] 重心整列をしない")
    p.add_argument("--edge-softness", type=float, default=1.0,
                   help="[sdf] 輪郭のアンチエイリアス幅 px（デフォルト: 1.0）")
    p.add_argument("--color-ease", type=int, default=1,
                   help="[sdf] 色の進行に smoothstep をかける回数 0〜3"
                        "（デフォルト: 1。大きいほど両端の色を長く保つ）")
    p.add_argument("--color-path", choices=["oklch", "oklab"], default="oklch",
                   help="[sdf] 色補間の経路（デフォルト: oklch）")
    a = p.parse_args()

    create_video(
        a.image_a, a.image_b, a.output,
        max_pixels=a.max_pixels, fps=a.fps, duration=a.duration,
        w_move=a.w_move, w_color=a.w_color, w_vanish=a.w_vanish,
        grid_step=a.grid_step, smoothing=a.smoothing,
        color_metric=a.color_metric, color_mix=a.color_mix,
        color_local=a.color_local, alpha_mode=a.alpha_mode,
        alpha_sharp=a.alpha_sharp,
        bg_color=tuple(a.bg),
        method=a.method, align=a.align, edge_softness=a.edge_softness,
        color_ease=a.color_ease, color_path=a.color_path,
    )


if __name__ == "__main__":
    main()
