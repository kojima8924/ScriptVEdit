# -*- coding: utf-8 -*-
"""scriptvedit: スクリプトから動画を組み立てる DSL / レンダラ

レイヤーファイルは `from scriptvedit import *` で全公開APIを取り込む。
実体は各サブモジュールに分割されており、本ファイルは公開名前空間の集約点。
プラグイン（@effect_plugin）はこのパッケージ名前空間へファクトリを注入する。
"""
import os
import sys

def _fix_console_encoding():
    """符号化不能文字で print が落ちないようコンソールを replace 化する。

    進捗表示は日本語のため、コンソールが日本語を符号化できないロケール
    （英語版Windowsのcp1252等）では print が UnicodeEncodeError で
    レンダ自体が落ちる。エンコーディングは変えず、符号化不能文字だけ
    置換して継続する（cp932/UTF-8環境には影響しない）。issue #13 CI で実測。

    関数に包むのはループ変数・作業変数をモジュール名前空間へ残さないため
    （以前は scriptvedit.enc / scriptvedit._stream が dir() の公開名に
    混ざっていた。監査 項目15c）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
            if enc and enc != "utf8":
                stream.reconfigure(errors="replace")
        except Exception:
            pass  # リダイレクト先やIDE組込コンソール等、reconfigure非対応は無視


_fix_console_encoding()

__all__ = [
    # コアクラス
    "Project", "Object", "Transform", "TransformChain", "Effect", "EffectChain",
    "AudioEffect", "AudioEffectChain",
    "VideoView", "AudioView",
    # ファクトリ関数
    "resize", "rotate", "crop", "pad", "blur", "eq",
    "scale", "fade", "move", "morph_to", "rotate_to",
    "move_along", "path_bezier", "throw", "inertia", "look_at", "perlin",
    "explode_to", "assemble_from", "group", "tile",
    "wipe", "zoom", "color_shift", "shake",
    "chroma_key", "vignette", "pixelize", "glow", "lut", "glitch",
    "perspective_warp", "lens", "ken_burns", "drop_shadow", "outline",
    "slideshow", "transition", "video_sequence",
    # 合成・コンポジション
    "mask", "mask_wipe", "opacity", "blend_mode", "rounded", "pip",
    "blur_background_fill", "progress_bar",
    # 時間操作（映像）
    "speed", "reverse", "freeze_frame",
    "avolume", "adelete", "delete", "trim", "atrim", "atempo",
    # テキスト・字幕（drawtext/subtitlesベース）
    "text", "typewriter", "counter", "subtitles", "karaoke",
    # オーディオ系
    "duck_under", "loop", "audio_sequence", "sfx", "audio_viz", "voice",
    # 統合サブモジュール（tts/beat/web）
    "narrate", "Narration", "beat_sync", "slide",
    # アンカー/同期
    "anchor", "pause", "scene",
    # Expr
    "Expr", "Const", "Var",
    # 数学関数
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
    "sinh", "cosh", "tanh",
    "exp", "log", "sqrt", "floor", "ceil", "trunc",
    "log10", "cbrt", "lerp", "clip", "clamp",
    "step", "smoothstep", "mod", "frac", "deg2rad", "rad2deg",
    # 条件分岐・比較
    "if_", "lt", "gt", "lte", "gte", "eq_", "neq",
    "and_", "or_", "not_", "between", "case",
    "sign", "random",
    # Python組み込み互換
    "abs", "min", "max", "round", "pow",
    # 定数
    "PI", "E",
    # DSL糖衣
    "P",
    # イージング関数
    "linear",
    "ease_in_quad", "ease_out_quad", "ease_in_out_quad",
    "ease_in_cubic", "ease_out_cubic", "ease_in_out_cubic",
    "ease_in_quart", "ease_out_quart", "ease_in_out_quart",
    "ease_in_quint", "ease_out_quint", "ease_in_out_quint",
    "ease_in_sine", "ease_out_sine", "ease_in_out_sine",
    "ease_in_expo", "ease_out_expo", "ease_in_out_expo",
    "ease_in_circ", "ease_out_circ", "ease_in_out_circ",
    "ease_in_back", "ease_out_back", "ease_in_out_back",
    "ease_in_elastic", "ease_out_elastic", "ease_in_out_elastic",
    "ease_in_bounce", "ease_out_bounce", "ease_in_out_bounce",
    "ease_cubic_bezier", "ease_spring", "steps", "apply_easing",
    # シーケンス・キーフレーム
    "phase", "sequence_param", "repeat", "bounce", "alternate", "staircase",
    "keyframes",
    # テンプレートラッパー
    "subtitle", "subtitle_box", "bubble", "diagram",
    # 数式（KaTeX同梱）
    "formula", "formula_lines",
    # 図形ビルダー
    "circle", "rect", "arrow", "label", "spotlight",
    # プラグイン機構
    "effect_plugin", "load_plugin", "load_plugins", "unregister_plugin",
    "plugin_manifest", "PluginError",
    # ケイパビリティ・マニフェスト
    "describe", "describe_markdown", "MANIFEST_VERSION",
    # 素材・パス解決（cwd 非依存）
    "asset", "assets_dir", "here",
    # ファイル監視（READMEの例が star import 前提のため公開する）
    "watch",
]


# --- 各サブモジュールから公開名を集約する ---
#
# 再エクスポートするのは **公開名（アンダースコア始まりでない名前）だけ**。
# 以前は内部ヘルパー 202 名も束ねていたが、実際に参照していたのは 25 名で、
# 「内部ヘルパーを足すたびに 26 ブロックのどれかを直す」「private だが
# package root から見えるので内部専用かテスト契約か区別できない」という
# 二重の負債になっていた。内部ヘルパーは実体モジュールから直接 import すること
#   例: from scriptvedit.cache import _checkpoint_cache_path
#
# 以下の import 順は循環 import を避けるための生命線なので並び替えないこと。


def _preload(*module_names):
    """公開名を持たない内部モジュールを読み込む（読み込み順の維持のみが目的）

    再エクスポートする名前が無いので `from ... import ...` では書けないが、
    ここで読み込まれる順序自体が循環 import 回避の前提になっている。
    `import scriptvedit.xxx` と書くとパッケージ自身への自己参照名が
    名前空間へ残るため、関数に包んで名前空間を汚さない。
    """
    for name in module_names:
        __import__(name)


from scriptvedit.assets import (  # noqa: F401
    asset, assets_dir, here, resolve_layer_path
)
_preload("scriptvedit.state")
from scriptvedit.expr import (  # noqa: F401
    Const, E, Expr, P, PI, Percent, Var, abs, acos, and_, asin, atan, atan2, between, case,
    cbrt, ceil, clamp, clip, cos, cosh, deg2rad, eq_, exp, floor, frac, gt, gte, if_, lerp,
    log, log10, lt, lte, max, min, mod, neq, not_, or_, pow, rad2deg, random, round, sign,
    sin, sinh, smoothstep, sqrt, step, tan, tanh, trunc
)
from scriptvedit.easing import (  # noqa: F401
    alternate, apply_easing, bounce, ease_cubic_bezier, ease_in_back, ease_in_bounce,
    ease_in_circ, ease_in_cubic, ease_in_elastic, ease_in_expo, ease_in_out_back,
    ease_in_out_bounce, ease_in_out_circ, ease_in_out_cubic, ease_in_out_elastic,
    ease_in_out_expo, ease_in_out_quad, ease_in_out_quart, ease_in_out_quint,
    ease_in_out_sine, ease_in_quad, ease_in_quart, ease_in_quint, ease_in_sine, ease_out_back,
    ease_out_bounce, ease_out_circ, ease_out_cubic, ease_out_elastic, ease_out_expo,
    ease_out_quad, ease_out_quart, ease_out_quint, ease_out_sine, ease_spring, keyframes,
    linear, phase, repeat, sequence_param, staircase, steps
)
_preload("scriptvedit.validate", "scriptvedit.ffmpeg")
from scriptvedit.cache import (  # noqa: F401
    cache_clear, cache_gc, cache_stats
)
from scriptvedit.text import (  # noqa: F401
    counter, karaoke, subtitles, text, typewriter
)
_preload("scriptvedit.filters.video", "scriptvedit.filters.audio")
from scriptvedit.objects import (  # noqa: F401
    AudioEffect, AudioEffectChain, AudioView, Effect, EffectChain, Group, Object, Transform,
    TransformChain, VideoView, group, tile
)
from scriptvedit.timeline import (  # noqa: F401
    Pause, Scene, anchor, pause, scene
)
from scriptvedit.project import (  # noqa: F401
    Project
)
from scriptvedit.effects.basic import (  # noqa: F401
    adelete, atempo, atrim, avolume, blur, color_shift, crop, delete, eq, fade, move, pad,
    resize, rotate, rotate_to, scale, shake, trim, wipe, zoom
)
from scriptvedit.effects.paths import (  # noqa: F401
    inertia, look_at, move_along, path_bezier, perlin, throw
)
from scriptvedit.effects.terminal import (  # noqa: F401
    assemble_from, explode_to, morph_to
)
from scriptvedit.effects.visual import (  # noqa: F401
    chroma_key, drop_shadow, glitch, glow, ken_burns, lens, lut, outline, perspective_warp,
    pixelize, vignette
)
from scriptvedit.effects.composite import (  # noqa: F401
    blend_mode, blur_background_fill, mask, mask_wipe, opacity, pip, progress_bar, rounded
)
from scriptvedit.effects.time import (  # noqa: F401
    freeze_frame, reverse, speed
)
from scriptvedit.audio import (  # noqa: F401
    Narration, audio_sequence, audio_viz, beat_sync, duck_under, loop, narrate, sfx, voice
)
from scriptvedit.media import (  # noqa: F401
    slideshow, transition, video_sequence
)
from scriptvedit.web import (  # noqa: F401
    arrow, bubble, circle, diagram, label, rect, slide, spotlight, subtitle, subtitle_box
)
from scriptvedit.formula import (  # noqa: F401
    formula, formula_lines
)
from scriptvedit.plugins import (  # noqa: F401
    PluginError, effect_plugin, load_plugin, load_plugins, plugin_manifest, unregister_plugin
)
from scriptvedit.manifest import (  # noqa: F401
    MANIFEST_VERSION, describe, describe_markdown
)
from scriptvedit.cli import (  # noqa: F401
    watch
)


# --- プラグイン自動読込（import 時: カレントディレクトリの plugins/）---
def _autoload_plugins_on_import():
    """カレントディレクトリの plugins/ を自動読込する。

    環境変数 SCRIPTVEDIT_NO_PLUGINS を設定すると自動読込を無効化できる。
    `python -m scriptvedit` でも本モジュールは正規名 "scriptvedit" で
    一度だけロードされる。関数に包むのは _autoload_plugins を
    パッケージ名前空間へ再エクスポートしないため。
    """
    from scriptvedit.plugins import _autoload_plugins
    _autoload_plugins(os.getcwd())


_autoload_plugins_on_import()
