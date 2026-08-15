# -*- coding: utf-8 -*-



# --- Transform関数 ---

def resize(**kwargs):
    return Transform("resize", **kwargs)


def rotate(*, deg=None, rad=None, expand=False, fill="0x00000000"):
    """固定角回転Transform。deg/radどちらか一方のみ指定。"""
    if deg is None and rad is None:
        raise ValueError("rotate: deg または rad のどちらかが必要")
    if deg is not None and rad is not None:
        raise ValueError("rotate: deg と rad は同時に指定できません")
    if deg is not None:
        rad_val = deg2rad(deg)
    else:
        rad_val = _to_expr(rad)
    # 時間依存式（uを含む式）は静的Transformでは未定義変数uがフィルタに漏れるため拒否
    if isinstance(rad_val, Expr) and rad_val.to_ffmpeg("0") != rad_val.to_ffmpeg("1"):
        raise ValueError(
            "rotate() に時間依存の式（u を含む式）は使えません。"
            "時間変化する回転には rotate_to() を使ってください。")
    fill = _validate_ffmpeg_color("rotate", fill)
    return Transform("rotate", rad=rad_val, expand=expand, fill=fill)


def crop(x=0, y=0, w=None, h=None):
    """クロップTransform。x,y: 左上起点(px)、w,h: 出力サイズ(px)。"""
    if w is None or h is None:
        raise ValueError("crop: w と h は必須です")
    return Transform("crop", x=x, y=y, w=w, h=h)


def pad(w=None, h=None, x=-1, y=-1, color="black"):
    """パディングTransform。w,h: 出力サイズ、x,y: 配置位置(-1=中央)。"""
    if w is None or h is None:
        raise ValueError("pad: w と h は必須です")
    color = _validate_ffmpeg_color("pad", color)
    return Transform("pad", w=w, h=h, x=x, y=y, color=color)


def blur(radius=5):
    """ガウスぼかしTransform。"""
    return Transform("blur", radius=radius)


def eq(*, brightness=0, contrast=1, saturation=1, gamma=1):
    """色調補正Transform（EQ）。brightness: -1..1, contrast: 0..inf, saturation: 0..inf"""
    return Transform("eq", brightness=brightness, contrast=contrast,
                     saturation=saturation, gamma=gamma)


# --- Effect関数 ---

def scale(value=1):
    return Effect("scale", value=_resolve_param(value))


def fade(alpha=1.0):
    return Effect("fade", alpha=_resolve_param(alpha))


def move(**kwargs):
    resolved = {}
    # from/to アニメーション → lerp Exprに自動変換
    has_anim = "from_x" in kwargs or "from_y" in kwargs or "to_x" in kwargs or "to_y" in kwargs
    if has_anim:
        fx = kwargs.get("from_x", kwargs.get("x", 0.5))
        fy = kwargs.get("from_y", kwargs.get("y", 0.5))
        tx = kwargs.get("to_x", kwargs.get("x", 0.5))
        ty = kwargs.get("to_y", kwargs.get("y", 0.5))
        resolved["x"] = _resolve_param(lambda u: lerp(fx, tx, u))
        resolved["y"] = _resolve_param(lambda u: lerp(fy, ty, u))
    else:
        if "x" in kwargs:
            resolved["x"] = _resolve_param(kwargs["x"])
        if "y" in kwargs:
            resolved["y"] = _resolve_param(kwargs["y"])
    if "anchor" in kwargs:
        resolved["anchor"] = kwargs["anchor"]
    return Effect("move", **resolved)


def rotate_to(deg=None, rad=None, *, from_deg=None, from_rad=None,
              to_deg=None, to_rad=None, follow=None, offset_deg=0.0,
              expand=True, fill="0x00000000"):
    """時間依存回転Effect。deg/rad直接指定 or from/to でlerp。

    follow: move系Effect（move_along/path_bezier/throw等）を渡すと、
      そのパスの進行方向を向く回転になる（look_at と同義）。offset_deg で
      向きを補正する。
    """
    fill = _validate_ffmpeg_color("rotate_to", fill)
    if follow is not None:
        return look_at(follow, offset_deg=offset_deg, expand=expand, fill=fill)
    has_from_to = any(v is not None for v in (from_deg, from_rad, to_deg, to_rad))
    if has_from_to:
        fr = _to_expr(from_rad) if from_rad is not None else (
            deg2rad(from_deg) if from_deg is not None else Const(0))
        tr = _to_expr(to_rad) if to_rad is not None else (
            deg2rad(to_deg) if to_deg is not None else Const(0))
        rad_expr = _resolve_param(lambda u: lerp(fr, tr, u))
    else:
        if deg is None and rad is None:
            raise ValueError("rotate_to: deg/rad か from/to の指定が必要")
        if rad is not None:
            rad_expr = _resolve_param(rad)
        else:
            rad_expr = deg2rad(_resolve_param(deg))
    return Effect("rotate_to", rad=rad_expr, expand=expand, fill=fill)


def wipe(direction="left", progress=None):
    """ワイプEffect。direction: left/right/up/down（top/bottomも別名として可）"""
    aliases = {"top": "up", "bottom": "down"}
    direction = aliases.get(direction, direction)
    if direction not in ("left", "right", "up", "down"):
        raise ValueError(
            f"wipe: direction は left/right/up/down"
            f"（または top/bottom）のいずれかです: {direction!r}")
    if progress is None:
        progress = _resolve_param(lambda u: u)
    else:
        progress = _resolve_param(progress)
    return Effect("wipe", direction=direction, progress=progress)


def zoom(value=None, *, from_value=1, to_value=None):
    """ズームEffect。valueまたはfrom/to指定。scaleのエイリアス。"""
    if value is not None:
        return Effect("scale", value=_resolve_param(value))
    if to_value is None:
        raise ValueError("zoom: value か to_value の指定が必要です")
    expr = _resolve_param(lambda u: lerp(from_value, to_value, u))
    return Effect("scale", value=expr)


def color_shift(*, hue=None, saturation=None, brightness=None):
    """時間依存の色調変化Effect。各パラメータはExpr/lambda/数値。"""
    params = {}
    if hue is not None:
        params["hue"] = _resolve_param(hue)
    if saturation is not None:
        params["saturation"] = _resolve_param(saturation)
    if brightness is not None:
        params["brightness"] = _resolve_param(brightness)
    if not params:
        raise ValueError("color_shift: hue/saturation/brightness のいずれかが必要です")
    return Effect("color_shift", **params)


def shake(amplitude=0.02, frequency=10):
    """振動Effect（ライブ、overlay座標でシェイク）"""
    return Effect("shake", amplitude=amplitude, frequency=frequency)


# --- 音声エフェクト関数 ---

def again(value=1.0):
    """音量倍率"""
    return AudioEffect("again", value=_resolve_param(value))


def afade(alpha=1.0):
    """音量フェード"""
    return AudioEffect("afade", alpha=_resolve_param(alpha))


def adelete():
    """音声をミックスから除外"""
    return AudioEffect("adelete")


def delete():
    """映像をオーバーレイから除外"""
    return Effect("delete")


def trim(duration=None):
    """映像トリム（時間影響あり）"""
    return Effect("trim", duration=duration)


def atrim(duration=None):
    """音声トリム（時間影響あり）"""
    return AudioEffect("atrim", duration=duration)


def atempo(rate=1.0):
    """音声テンポ変更（時間影響あり）"""
    return AudioEffect("atempo", rate=rate)


# --- 遅延解決の相互参照（関数本体からのみ使用: 循環importを避けるため末尾で束縛）---
from scriptvedit.effects.paths import look_at
from scriptvedit.expr import Const, Expr, _resolve_param, _to_expr, deg2rad, lerp
from scriptvedit.objects import AudioEffect, Effect, Transform
from scriptvedit.validate import _validate_ffmpeg_color
