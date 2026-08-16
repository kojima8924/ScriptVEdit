# -*- coding: utf-8 -*-

import os
import builtins as _builtins

# context は scriptvedit 内 import を持たない葉なので先頭で import できる。
from scriptvedit.context import current_project

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.effects.basic import move
from scriptvedit.effects.visual import drop_shadow, outline
from scriptvedit.expr import Const, _resolve_param
from scriptvedit.objects import Effect, EffectChain
from scriptvedit.state import _detect_media_type, _suggest_hint
from scriptvedit.text import _new_text_object, _text_synthetic_source
from scriptvedit.validate import _parse_color_rgb, _require_number


# --- 合成・コンポジション系Effect ---

def _validate_mask_image(func, image_path):
    """マスク画像パスの検証（文字列・存在・画像形式）"""
    if not isinstance(image_path, str) or not image_path:
        raise TypeError(
            f"{func}: image_path にはマスク画像のパス文字列を指定してください: {image_path!r}")
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"{func}: マスク画像が見つかりません: {image_path}")
    if _detect_media_type(image_path) != "image":
        raise ValueError(f"{func}: マスクには画像のみ指定できます: {image_path}")


def _register_material_dep(path):
    """素材をレイヤー依存として登録（レイヤーキャッシュの鮮度検証に載せる）"""
    proj = current_project()
    if proj is not None and proj._current_layer_file:
        proj._extra_layer_deps.setdefault(
            proj._current_layer_file, []).append(path)


def mask(image_path):
    """マスクEffect: 画像の輝度をアルファとして乗算する。

    白い部分は不透明のまま、黒い部分は透明になる（グレーは半透明）。
    追加 -i 入力の配線を避けるため movie= ソースで filter chain 内に読み込み、
    scale2ref で素材サイズへ自動スケールする。元素材のアルファとは乗算合成。
    """
    _validate_mask_image("mask", image_path)
    _register_material_dep(image_path)
    return Effect("mask", image=image_path)


def mask_wipe(image_path, progress=None):
    """マスクワイプEffect: マスク画像の輝度をしきい値に使うワイプ。

    輝度 <= progress*255 の画素から順に現れる（黒い部分が先、白い部分が最後）。
    progress は 0→1 の進行（Expr/lambda可、省略時は線形 u）。
    グラデーション画像を使うと方向・形状を自由に制御できる。
    """
    _validate_mask_image("mask_wipe", image_path)
    _register_material_dep(image_path)
    if progress is None:
        progress = _resolve_param(lambda u: u)
    else:
        progress = _resolve_param(progress)
    return Effect("mask_wipe", image=image_path, progress=progress)


def opacity(value):
    """不透明度Effect。value は 0〜1（0=完全透明 / 1=不透明）。

    定数は colorchannelmixer（高速）、Expr/lambda は geq による
    live アニメーションになる。
    """
    v = _resolve_param(value)
    if isinstance(v, Const):
        _require_number("opacity", "value", v.value, 0.0, 1.0)
    return Effect("opacity", value=v)


# FFmpeg blend フィルタの合成モード（normal は通常overlayと同義のため許可のみ）
_BLEND_MODES = {
    "addition", "addition128", "grainmerge", "and", "average", "burn",
    "darken", "difference", "difference128", "grainextract", "divide",
    "dodge", "exclusion", "extremity", "freeze", "glow", "hardlight",
    "hardmix", "heat", "lighten", "linearlight", "multiply", "multiply128",
    "negation", "normal", "or", "overlay", "phoenix", "pinlight", "reflect",
    "screen", "softlight", "subtract", "vividlight", "xor", "softdifference",
    "geometric", "harmonic", "bleach", "stain", "interpolate", "hardoverlay",
}
_BLEND_MODE_ALIASES = {"add": "addition", "plus": "addition"}


def blend_mode(mode):
    """オブジェクトの合成モードを変更するEffect（screen/multiply/overlay等）。

    overlayフィルタは合成モード非対応のため、このオブジェクトのみ
    「キャンバス全面へ透明パドした入力」を blend=cN_mode=<mode> で合成し、
    アルファ領域だけ maskedmerge で採用する経路に切り替わる（live）。
    """
    if not isinstance(mode, str):
        raise TypeError(f"blend_mode: mode には合成モード名の文字列を指定してください: {mode!r}")
    m = _BLEND_MODE_ALIASES.get(mode.strip().lower(), mode.strip().lower())
    if m not in _BLEND_MODES:
        hint = _suggest_hint(m, _BLEND_MODES)
        raise ValueError(
            f"blend_mode: 未知の合成モード '{mode}'。{hint}\n"
            f"有効なモード: {', '.join(sorted(_BLEND_MODES))}")
    return Effect("blend_mode", mode=m)


def rounded(radius):
    """角丸Effect: geq でアルファに角丸矩形マスクを乗算する。

    radius: 角の半径px（0で無効）。
    """
    _require_number("rounded", "radius", radius, 0, 4096)
    return Effect("rounded", radius=int(_builtins.round(radius)))


def pip(x=0.7, y=0.7, scale=0.3, radius=12, border=2, border_color="white",
        shadow=True):
    """ピクチャインピクチャのプリセット（既存Effectの組を返すEffectChain）。

    scale縮小 → rounded角丸 → outline縁取り → drop_shadow → move配置 の合成。
    x/y: 配置位置（キャンバス比率、中央anchor）。scale: 縮小率。
    radius: 角丸半径px（0で無効）。border: 縁取り幅px（0で無効）。
    """
    _require_number("pip", "x", x, 0.0, 1.0)
    _require_number("pip", "y", y, 0.0, 1.0)
    _require_number("pip", "scale", scale, 0.01, 1.0)
    _require_number("pip", "radius", radius, 0, 4096)
    if isinstance(border, bool) or not isinstance(border, int) or not (0 <= border <= 16):
        raise ValueError(f"pip: border は 0〜16 の整数で指定してください: {border!r}")
    effs = [Effect("scale", value=Const(float(scale)))]
    if radius > 0:
        effs.append(Effect("rounded", radius=int(radius)))
    if border > 0:
        effs.append(outline(border, border_color))
    if shadow:
        effs.append(drop_shadow(dx=4, dy=4, blur=8, opacity=0.4))
    effs.append(move(x=x, y=y, anchor="center"))
    return EffectChain(effs)


def blur_background_fill(blur=20):
    """縦横変換の定番「ぼかした自分自身を背景に敷く」Effect。

    素材をキャンバス全面に cover で拡大ぼかしし、中央に本体を fit で重ねる。
    出力はキャンバスサイズ固定。live Effect（キャンバスサイズのffv1キャッシュ
    肥大とProject解像度依存を避けるためチェックポイント対象外）。
    """
    _require_number("blur_background_fill", "blur", blur, 0.1, 200)
    return Effect("blur_background_fill", blur=blur)


def _parse_color_alpha(func, color):
    """'white@0.2' 形式の色指定を (R, G, B, A0-255) に分解する"""
    if not isinstance(color, str) or not color:
        raise ValueError(
            f"{func}: color には色名か16進の文字列を指定してください: {color!r}")
    body, sep, alpha_str = color.partition("@")
    alpha = 1.0
    if sep:
        try:
            alpha = float(alpha_str)
        except ValueError:
            raise ValueError(f"{func}: 不正なアルファ指定です: '{color}'")
        if not (0.0 <= alpha <= 1.0):
            raise ValueError(f"{func}: アルファは0〜1で指定してください: '{color}'")
    r, g, b = _parse_color_rgb(body)
    return (r, g, b, int(_builtins.round(alpha * 255)))


def progress_bar(*, height=6, color="white", bg="white@0.2", y=1.0):
    """動画全体の進行バーを表示する特殊Object（透明lavfi + geq、textと同方式）。

    duration 未設定のまま動画全体に表示される（time/show は不要）。
    color: バー色、bg: 背景トラック色（'white@0.2' のようにアルファ指定可）。
    y: 縦位置（0=上端, 1=下端）。
    """
    _require_number("progress_bar", "height", height, 1, 512)
    _require_number("progress_bar", "y", y, 0.0, 1.0)
    bar_rgba = _parse_color_alpha("progress_bar", color)
    track_rgba = _parse_color_alpha("progress_bar", bg)
    spec = {
        "kind": "progress_bar",
        "height": int(height), "y": float(y),
        "bar_rgba": bar_rgba, "track_rgba": track_rgba,
    }
    spec["synthetic_source"] = _text_synthetic_source(
        f"pbar|{height}|{color}|{bg}|{y}")
    obj = _new_text_object(spec)
    obj._advance = False  # タイムラインを進めない（全体に重ねる表示専用）
    return obj
