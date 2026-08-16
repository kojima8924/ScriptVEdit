# -*- coding: utf-8 -*-

import math as _math
import difflib as _difflib

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.expr import Expr
from scriptvedit.state import _PLACEMENT_ANCHORS, _suggest_hint


# --- Effect用バリデーションヘルパー ---

_COLOR_NAME_RGB = {
    "black": (0, 0, 0), "white": (255, 255, 255),
    "red": (255, 0, 0), "green": (0, 128, 0), "lime": (0, 255, 0),
    "blue": (0, 0, 255), "yellow": (255, 255, 0), "cyan": (0, 255, 255),
    "magenta": (255, 0, 255), "gray": (128, 128, 128), "grey": (128, 128, 128),
    "orange": (255, 165, 0), "purple": (128, 0, 128), "pink": (255, 192, 203),
    "brown": (165, 42, 42), "navy": (0, 0, 128),
}

# `ffmpeg -colors` が公開する組み込み色名（大文字小文字は区別しない）。
# 実行時にffmpegへ問い合わせると最小環境で構築時検証できないため、安定した
# libavutilの色名集合を本体へ保持する。CSS専用の transparent 等は含めない。
_FFMPEG_COLOR_NAMES = frozenset("""
aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond
blue blueviolet brown burlywood cadetblue chartreuse chocolate coral
cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod darkgray
darkgreen darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred
darksalmon darkseagreen darkslateblue darkslategray darkturquoise darkviolet
deeppink deepskyblue dimgray dodgerblue firebrick floralwhite forestgreen
fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow honeydew
hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen
lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow lightgreen
lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray
lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine
mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue
mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream
mistyrose moccasin navajowhite navy oldlace olive olivedrab orange orangered
orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff
peru pink plum powderblue purple red rosybrown royalblue saddlebrown salmon
sandybrown seagreen seashell sienna silver skyblue slateblue slategray snow
springgreen steelblue tan teal thistle tomato turquoise violet wheat
white whitesmoke yellow yellowgreen
""".split())


def _parse_color_rgb(color):
    """色名/16進文字列を (R, G, B) タプルに変換（drop_shadow/outline のgeq色付け用）"""
    if not isinstance(color, str) or not color:
        raise ValueError(f"color には色名か16進(#RRGGBB)の文字列を指定してください: {color!r}")
    s = color.strip().lower()
    if s.startswith("#"):
        s = s[1:]
    elif s.startswith("0x"):
        s = s[2:]
    else:
        if s in _COLOR_NAME_RGB:
            return _COLOR_NAME_RGB[s]
        raise ValueError(
            f"未対応の色名です: '{color}'。"
            f"対応色名: {', '.join(sorted(_COLOR_NAME_RGB))} または16進(#RRGGBB)")
    if len(s) != 6 or any(c not in "0123456789abcdef" for c in s):
        raise ValueError(f"16進カラーは #RRGGBB / 0xRRGGBB 形式で指定してください: '{color}'")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _validate_ffmpeg_color(func_name, color):
    """ffmpeg色（色名[@alpha] / 0xRRGGBB[AA]）を検証して返す"""
    if not isinstance(color, str) or not color:
        raise ValueError(f"{func_name}: color には色名か16進の文字列を指定してください: {color!r}")
    s = color.strip()
    if s.startswith("#") or s.lower().startswith("0x"):
        h = s[1:] if s.startswith("#") else s[2:]
        if len(h) not in (6, 8) or any(c not in "0123456789abcdefABCDEF" for c in h):
            raise ValueError(
                f"{func_name}: 16進カラーは #RRGGBB[AA] / 0xRRGGBB[AA] 形式です: "
                f"'{color}'")
        return "0x" + h.upper()

    name, sep, alpha_text = s.partition("@")
    normalized_name = name.lower()
    if normalized_name not in _FFMPEG_COLOR_NAMES:
        hint = _difflib.get_close_matches(normalized_name, _FFMPEG_COLOR_NAMES, n=1)
        suggestion = f"（候補: {hint[0]}）" if hint else ""
        raise ValueError(f"{func_name}: 不正な色名です: '{color}'{suggestion}")
    if sep:
        try:
            alpha = float(alpha_text)
        except (TypeError, ValueError):
            raise ValueError(f"{func_name}: 不正なアルファ指定です: '{color}'") from None
        if not 0.0 <= alpha <= 1.0:
            raise ValueError(
                f"{func_name}: 色のアルファは0〜1で指定してください: '{color}'")
        return f"{normalized_name}@{alpha_text}"
    if not name:
        raise ValueError(f"{func_name}: 無効な色指定です: '{color}'")
    return normalized_name


def _require_number(func_name, param_name, value, lo=None, hi=None):
    """定数数値パラメータの型・範囲検証（Expr/lambda不可）"""
    if isinstance(value, Expr) or callable(value):
        raise ValueError(
            f"{func_name}: {param_name} には Expr/lambda は使えません（定数のみ）")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{func_name}: {param_name} は数値で指定してください: {value!r}")
    if not _math.isfinite(value):
        raise ValueError(
            f"{func_name}: {param_name} に NaN/Infinity は使えません: {value!r}")
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        rng = f"{lo if lo is not None else ''}〜{hi if hi is not None else ''}"
        raise ValueError(
            f"{func_name}: {param_name} は {rng} の範囲で指定してください: {value}")
    return value


def _reject_unknown_keys(func_name, params, valid_keys):
    """**params の未知キー（タイポの可能性大）を構築時に明示エラーにする。

    func_name が None のときはプレフィックス無しの従来文言
    （morph モジュール単体利用時の書式）を維持する。
    エラーメッセージの書式は既存テスト・利用者スクリプトが照合しているため
    変えないこと。
    """
    unknown = set(params) - set(valid_keys)
    if unknown:
        prefix = f"{func_name}: 未知のパラメータ " if func_name else "未知のパラメータ: "
        raise ValueError(
            f"{prefix}{sorted(unknown)}"
            f"（有効なキー: {sorted(valid_keys)}）")


def _validate_placement_anchor(func_name, anchor):
    """move 系 Effect の anchor を検証する（誤記を構築時に拒否）。

    anchor は overlay の配置基準点。誤記（'cetner'）を既定へ黙って戻すと
    半オブジェクト分ずれた絵になり、ffmpeg は正常終了するので気付けない。
    呼び出しは Effect 構築の1箇所（objects.py の Effect.__init__）に集約する。
    """
    if not isinstance(anchor, str):
        raise TypeError(
            f"{func_name}: anchor は文字列で指定してください: {anchor!r}"
            f"（有効な値: {', '.join(_PLACEMENT_ANCHORS)}）")
    if anchor in _PLACEMENT_ANCHORS:
        return anchor
    hint = _suggest_hint(anchor, _PLACEMENT_ANCHORS)
    raise ValueError(
        f"{func_name}: 未知の anchor '{anchor}'。{hint}\n"
        f"有効な値: {', '.join(_PLACEMENT_ANCHORS)}")


def _require_time(func_name, param_name, value, *, lo=None, lo_exclusive=False):
    """時刻・尺パラメータ共通の検証（有限・非bool・下限）。

    time()/show()/pause/scene/@ 等のタイムラインAPIは、NaN・±Infinity・負数を
    受理すると filtergraph や総尺へ黙って伝播する（監査 issue #14 P1）。
    入口で一括拒否する。値は変換せずそのまま返す（int→float 変換で
    コマンド文字列やスナップショットが揺れるのを防ぐ）。
    """
    _require_number(func_name, param_name, value, lo, None)
    if lo is not None and lo_exclusive and value == lo:
        raise ValueError(
            f"{func_name}: {param_name} は {lo} より大きい値が必要です: {value!r}")
    return value
