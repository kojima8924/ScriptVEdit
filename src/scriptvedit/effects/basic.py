
# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.effects.paths import look_at
from scriptvedit.expr import Const, Expr, _resolve_param, _to_expr, deg2rad, lerp
from scriptvedit.objects import AudioEffect, Effect, Transform
from scriptvedit.state import _suggest_hint
from scriptvedit.validate import _require_time, _validate_ffmpeg_color
# -*- coding: utf-8 -*-



# --- Transform関数 ---

# resize に px 指定のつもりで渡されやすいキーワード → 誘導先の対応表。
# crop/pad が px の w/h を取るため `resize(w=640, h=360)` は極めて自然な誤りで、
# 従来は **kwargs が黙って飲み込み scale=iw*1:ih*1 の完全な no-op になっていた。
_RESIZE_PIXEL_KEYS = {
    "w": "幅", "h": "高さ", "width": "幅", "height": "高さ", "size": "サイズ",
}


def resize(*, sx=1, sy=1, **unknown):
    """リサイズTransform。sx/sy は**倍率**（1.0=等倍）。px 指定ではない。

    px で出力サイズを決めたいときは crop(w=, h=) / pad(w=, h=) を使う。
    """
    if unknown:
        px = sorted(k for k in unknown if k in _RESIZE_PIXEL_KEYS)
        if px:
            raise TypeError(
                f"resize: {', '.join(px)} は px 指定のキーワードですが、resize は"
                f"倍率（sx/sy）だけを取ります。"
                f"px で出力サイズを決めるなら crop(x=, y=, w=, h=) で切り出すか、"
                f"pad(w=, h=) で余白を足してください。"
                f"倍率で縮小するなら resize(sx=0.5, sy=0.5) と書きます。")
        hint = _suggest_hint(sorted(unknown)[0], ("sx", "sy"))
        raise TypeError(
            f"resize: 未知のキーワード引数 {sorted(unknown)}"
            f"（有効なキー: sx, sy）。{hint}")
    return Transform("resize", sx=sx, sy=sy)


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


def move(*, x=None, y=None, from_x=None, from_y=None, to_x=None, to_y=None,
         anchor=None, **unknown):
    """配置・移動Effect（overlay座標）。x/y は 0..1 の画面比率（Expr/lambda可）。

    from_*/to_* を指定すると u による自動 lerp になる。anchor は座標の基準点
    （既定 'center'。有効値は state.py の _PLACEMENT_ANCHORS）。
    center=中心 / topleft=左上の角 / left・right・top・bottom=その辺の中点。
    例: anchor='right', x=1.0 で右端にぴったり寄せる（縦は中央）。
    イージングを掛けたいときは x=lambda u: lerp(0.2, 0.8, ease_out_quad(u))
    のように x/y の式で書く（move は easing 引数を取らない）。
    """
    if unknown:
        # 従来は **kwargs が未知キーを黙って捨てていた（easing= が消えて等速移動に
        # なる等）。タイポも含めて構築時に拒否する。
        valid = ("x", "y", "from_x", "from_y", "to_x", "to_y", "anchor")
        hint = _suggest_hint(sorted(unknown)[0], valid)
        extra = ""
        if "easing" in unknown:
            extra = ("\nmove に easing はありません。"
                     "x=lambda u: lerp(0.2, 0.8, ease_out_quad(u)) のように"
                     "座標式へイージングを掛けてください。")
        raise TypeError(
            f"move: 未知のキーワード引数 {sorted(unknown)}"
            f"（有効なキー: {', '.join(valid)}）。{hint}{extra}")
    resolved = {}
    # from/to アニメーション → lerp Exprに自動変換
    has_anim = any(v is not None for v in (from_x, from_y, to_x, to_y))
    if has_anim:
        fx = from_x if from_x is not None else (x if x is not None else 0.5)
        fy = from_y if from_y is not None else (y if y is not None else 0.5)
        tx = to_x if to_x is not None else (x if x is not None else 0.5)
        ty = to_y if to_y is not None else (y if y is not None else 0.5)
        resolved["x"] = _resolve_param(lambda u: lerp(fx, tx, u))
        resolved["y"] = _resolve_param(lambda u: lerp(fy, ty, u))
    else:
        if x is not None:
            resolved["x"] = _resolve_param(x)
        if y is not None:
            resolved["y"] = _resolve_param(y)
    if anchor is not None:
        # 検証は Effect.__init__（objects.py）の共通関門で行う
        resolved["anchor"] = anchor
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

def avolume(value=1.0):
    """音量倍率（定数なら固定音量、Expr/lambda ならフェード等の時間変化）。

    volume フィルタ1段に落ちるため、フェードも音量調整もこの1つで書く
    （sfx(volume=) / voice(volume=) / narrate(volume=) と同じ語彙）。
    """
    return AudioEffect("avolume", value=_resolve_param(value))


def adelete():
    """音声をミックスから除外"""
    return AudioEffect("adelete")


def delete():
    """映像をオーバーレイから除外"""
    return Effect("delete")


def _trim_params(func_name, duration, start):
    """trim/atrim 共通の引数検証と params 構築。

    start は「素材のイン点（秒）」、duration は「出力する最大尺（秒）」。
    start=2, duration=3 なら素材の 2〜5 秒。両方省略した trim は何もしないのに
    Effect スロットを消費し、以後の Transform を一律禁止するだけなので拒否する。
    """
    _require_time(func_name, "start", start, lo=0)
    if duration is not None:
        _require_time(func_name, "duration", duration, lo=0, lo_exclusive=True)
    if duration is None and not start:
        raise ValueError(
            f"{func_name}(): start も duration も指定されていません。"
            f"何も切り出さない {func_name}() は Effect スロットを消費して以後の"
            f"Transform を禁止するだけなので受理しません。"
            f"尺を切るなら {func_name}(3)、イン点を指定するなら "
            f"{func_name}(start=2, duration=3) と書いてください"
            f"（素材時間の切り出しは obj[2:5] でも書けます）。")
    # start=0 のときは params に載せない（従来の鍵・コマンドと同一に保つ）
    params = {"duration": duration}
    if start:
        params["start"] = start
    return params


def trim(duration=None, *, start=0):
    """映像トリム（時間影響あり）。duration=出力尺(秒), start=イン点(秒)。

    start はキーワード専用。既存の `trim(2)` が「イン点2秒」へ黙って意味替え
    するのを防ぐため、位置引数は従来どおり duration のまま据え置く。
    """
    return Effect("trim", **_trim_params("trim", duration, start))


def atrim(duration=None, *, start=0):
    """音声トリム（時間影響あり）。duration=出力尺(秒), start=イン点(秒)。"""
    return AudioEffect("atrim", **_trim_params("atrim", duration, start))


def atempo(rate=1.0):
    """音声テンポ変更（時間影響あり）"""
    return AudioEffect("atempo", rate=rate)
