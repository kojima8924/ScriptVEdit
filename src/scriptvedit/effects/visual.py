# -*- coding: utf-8 -*-

import os
import math as _math
import builtins as _builtins

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.expr import Const, Expr, Var, _resolve_param, clip, lerp
from scriptvedit.objects import Effect
from scriptvedit.validate import _parse_color_rgb, _require_number, _validate_ffmpeg_color


def chroma_key(color="green", similarity=0.1, blend=0.0):
    """クロマキーEffect: 指定色を透明化（chromakeyフィルタ）。

    color: 色名 or 16進（#RRGGBB / 0xRRGGBB）
    similarity: 透明化する色の類似度（1e-5〜1.0、大きいほど広範囲）
    blend: 境界のブレンド量（0〜1）
    """
    color = _validate_ffmpeg_color("chroma_key", color)
    _require_number("chroma_key", "similarity", similarity, 1e-5, 1.0)
    _require_number("chroma_key", "blend", blend, 0.0, 1.0)
    return Effect("chroma_key", color=color, similarity=similarity, blend=blend)


def vignette(angle=None, strength=None):
    """ビネットEffect（vignetteフィルタ）。

    angle: レンズ角度(rad, 0〜PI/2, Expr/lambda可) または
    strength: 強度(0〜1, Expr/lambda可)をangle=strength*PI/2に変換。
    どちらか一方のみ指定（両方省略時はffmpegデフォルトのPI/5）。
    注意: vignetteフィルタはアルファ非対応のため透明部分は失われる（全画面素材向け）。
    """
    if angle is not None and strength is not None:
        raise ValueError("vignette: angle と strength は同時に指定できません")
    if angle is None and strength is None:
        angle_expr = Const(_math.pi / 5)
    elif angle is not None:
        angle_expr = _resolve_param(angle)
    else:
        angle_expr = _resolve_param(strength) * Const(_math.pi / 2)
    return Effect("vignette", angle=angle_expr)


def pixelize(size=16):
    """モザイクEffect（pixelizeフィルタ）。size: ブロックサイズpx（1〜1024）。

    ffmpegのpixelizeフィルタはサイズの式指定に非対応のため定数のみ。
    """
    if isinstance(size, Expr) or callable(size):
        raise ValueError(
            "pixelize: size に Expr/lambda は使えません"
            "（ffmpeg pixelizeフィルタが式評価非対応のため定数のみ）")
    _require_number("pixelize", "size", size, 1, 1024)
    return Effect("pixelize", size=int(size))


def glow(radius=10, intensity=1.0):
    """発光Effect: split→gblur→blend=screen の合成チェーン。

    radius: ぼかしのシグマ（0.1〜1024）
    intensity: 発光の強さ（0〜1、blendのall_opacity）
    """
    _require_number("glow", "radius", radius, 0.1, 1024)
    _require_number("glow", "intensity", intensity, 0.0, 1.0)
    return Effect("glow", radius=radius, intensity=intensity)


_LUT_EXTS = {".cube", ".3dl", ".dat", ".m3d", ".csp"}


def lut(file):
    """3D LUT Effect（lut3dフィルタ）。file: .cube等のLUTファイルパス。

    ファイルの存在を構築時に検証し、内容(FFP)をフィンガープリントに含める。
    """
    if not isinstance(file, str) or not file:
        raise ValueError("lut: file にはLUTファイルパスの文字列を指定してください")
    if not os.path.exists(file):
        raise ValueError(f"lut: LUTファイルが見つかりません: {file}")
    ext = os.path.splitext(file)[1].lower()
    if ext not in _LUT_EXTS:
        raise ValueError(
            f"lut: 未対応のLUT形式です: '{ext}'（対応: {', '.join(sorted(_LUT_EXTS))}）")
    return Effect("lut", file=file)


def glitch(strength=1.0, interval=None):
    """グリッチEffect: rgbashift + noise のプリセット。

    strength: 強度（0〜5、RGBずらし量とノイズ量に反映）
    interval: 秒指定で間欠発動（各周期の先頭30%区間のみ有効）。Noneで常時。
    """
    _require_number("glitch", "strength", strength, 0.0, 5.0)
    if interval is not None:
        _require_number("glitch", "interval", interval, 0.01, None)
    return Effect("glitch", strength=strength, interval=interval)


def perspective_warp(x0, y0, x1, y1, x2, y2, x3, y3):
    """透視変形Effect（perspectiveフィルタ, sense=destination）。

    入力の4隅（左上, 右上, 左下, 右下）の移動先座標(px)を指定する。
    """
    for name, v in zip(("x0", "y0", "x1", "y1", "x2", "y2", "x3", "y3"),
                       (x0, y0, x1, y1, x2, y2, x3, y3)):
        _require_number("perspective_warp", name, v)
    return Effect("perspective_warp", x0=x0, y0=y0, x1=x1, y1=y1,
                  x2=x2, y2=y2, x3=x3, y3=y3)


def lens(k1=0, k2=0):
    """レンズ歪みEffect（lenscorrectionフィルタ）。

    k1: 2次歪み係数（-1〜1、負で樽型・正で糸巻き型の補正）
    k2: 4次歪み係数（-1〜1）
    """
    _require_number("lens", "k1", k1, -1.0, 1.0)
    _require_number("lens", "k2", k2, -1.0, 1.0)
    return Effect("lens", k1=k1, k2=k2)


def ken_burns(from_rect, to_rect, easing=None):
    """Ken Burns Effect: 2つの矩形 (x, y, w, h) 間を補間してパン&ズーム。

    zoompanは使わず、動的scale + 固定サイズcropで実現する。
    from_rect/to_rect: 元素材ピクセル座標の (x, y, w, h) タプル（同一アスペクト比）
    easing: イージング関数（ease_in_out_quad等）。省略時は線形。
    出力サイズは両矩形の最大寸法（偶数丸め）に正規化される。
    """
    rects = {}
    for nm, rect in (("from_rect", from_rect), ("to_rect", to_rect)):
        if not isinstance(rect, (tuple, list)) or len(rect) != 4:
            raise ValueError(
                f"ken_burns: {nm} は (x, y, w, h) の4要素タプルで指定してください: {rect!r}")
        for i, v in enumerate(rect):
            _require_number("ken_burns", f"{nm}[{i}]", v)
        if rect[2] <= 0 or rect[3] <= 0:
            raise ValueError(f"ken_burns: {nm} の w, h は正の値が必要です: {rect!r}")
        rects[nm] = tuple(rect)
    fx, fy, fw, fh = rects["from_rect"]
    tx, ty, tw, th = rects["to_rect"]
    # アスペクト比の一致検証（1%許容）
    if _builtins.abs(fw / fh - tw / th) > 0.01 * (fw / fh):
        raise ValueError(
            f"ken_burns: from_rect と to_rect のアスペクト比が一致していません"
            f"（from: {fw}x{fh} = {fw / fh:.4f}, to: {tw}x{th} = {tw / th:.4f}）。"
            f"同じアスペクト比の矩形を指定してください。")
    out_w = int(_math.ceil(_builtins.max(fw, tw) / 2) * 2)
    out_h = int(_math.ceil(_builtins.max(fh, th) / 2) * 2)
    e_expr = _resolve_param(easing) if easing is not None else Var("u")
    # overshoot 系イージング(ease_out_back/elastic 等)は e>1 になり得る。
    # e>1 だと w_expr>元幅 → scale後幅<crop幅 となり FFmpeg が
    # "Invalid too big size for width" でレンダを中断する。
    # スケール算出用の補間係数のみ [0,1] にクランプする（x/y パンには overshoot を残す）。
    e_clamped = clip(e_expr, 0, 1)
    w_expr = lerp(fw, tw, e_clamped)
    s_expr = Const(out_w) / w_expr          # 全体スケール係数
    x_expr = lerp(fx, tx, e_expr) * s_expr  # スケール後座標でのcrop位置
    y_expr = lerp(fy, ty, e_expr) * s_expr
    return Effect("ken_burns", s=s_expr, x=x_expr, y=y_expr, w=out_w, h=out_h)


def drop_shadow(dx=5, dy=5, blur=8, color="black", opacity=0.5):
    """ドロップシャドウEffect: split→色付け+gblur→本体の背後にoverlay。

    dx, dy: 影のオフセット(px)。blur: ぼかしシグマ（0でシャープな影）。
    出力キャンバスは影が収まるよう自動拡張される。
    """
    _require_number("drop_shadow", "dx", dx)
    _require_number("drop_shadow", "dy", dy)
    _require_number("drop_shadow", "blur", blur, 0.0, 100.0)
    _require_number("drop_shadow", "opacity", opacity, 0.0, 1.0)
    _parse_color_rgb(color)  # 構築時に色を検証
    return Effect("drop_shadow", dx=int(_builtins.round(dx)),
                  dy=int(_builtins.round(dy)), blur=blur,
                  color=color, opacity=opacity)


def outline(width=2, color="white"):
    """縁取りEffect: alpha膨張（dilationフィルタをwidth回連結）ベース。

    色付けした複製のalphaをdilationで膨張させ、本体をその上にoverlayする。
    width: 縁取り幅px（1〜16の整数、dilation 1回につき1px膨張）
    """
    if isinstance(width, bool) or not isinstance(width, int) or not (1 <= width <= 16):
        raise ValueError(f"outline: width は 1〜16 の整数で指定してください: {width!r}")
    _parse_color_rgb(color)  # 構築時に色を検証
    return Effect("outline", width=width, color=color)
