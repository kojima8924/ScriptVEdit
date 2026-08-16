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


# --- 各サブモジュールから全名（内部名を含む）を集約する ---
# テスト・検証ツールが内部名を scriptvedit 名前空間から参照するため、公開名以外も束ねる
from scriptvedit.assets import (  # noqa: F401
    asset, assets_dir, here, resolve_layer_path,
)
from scriptvedit.state import (  # noqa: F401
    _ARTIFACT_DIR, _AUDIO_EXTS, _AVAILABLE_ENCODERS, _BAKEABLE_EFFECTS,
    _CACHE_DIR, _CONFIGURE_KEYS, _ENCODER_MAP, _ENGINE_VER, _GEN_COUNTER,
    _GEN_COUNTER_LOCK, _IMAGE_EXTS, _PRESETS, _REVERSE_MAX_SEC, _TERMINAL_FRAME_EFFECTS,
    _TIME_LIVE_EFFECTS, _VIDEO_EXTS, _WEB_EXTS, _detect_media_type, _suggest_hint,
)
from scriptvedit.expr import (  # noqa: F401
    Const, E, Expr, P, PI, Percent, Var, _BinOp, _FuncCall, _LN10, _NONFOLDABLE_FUNCS, _UnOp,
    _make_binop, _make_func, _make_unop, _resolve_param, _to_expr, abs, acos, and_, asin, atan,
    atan2, between, case, cbrt, ceil, clamp, clip, cos, cosh, deg2rad, eq_, exp, floor, frac,
    gt, gte, if_, lerp, log, log10, lt, lte, max, min, mod, neq, not_, or_, pow, rad2deg,
    random, round, sign, sin, sinh, smoothstep, sqrt, step, tan, tanh, trunc,
)
from scriptvedit.easing import (  # noqa: F401
    _EASE_C1, _EASE_C3, _EASE_C4, _EASE_C5, _EASE_PI, _ease_in_out_power, _ease_in_power,
    _ease_out_power, _power_n, alternate, apply_easing, bounce, ease_cubic_bezier, ease_in_back,
    ease_in_bounce, ease_in_circ, ease_in_cubic, ease_in_elastic, ease_in_expo,
    ease_in_out_back, ease_in_out_bounce, ease_in_out_circ, ease_in_out_cubic,
    ease_in_out_elastic, ease_in_out_expo, ease_in_out_quad, ease_in_out_quart,
    ease_in_out_quint, ease_in_out_sine, ease_in_quad, ease_in_quart, ease_in_quint,
    ease_in_sine, ease_out_back, ease_out_bounce, ease_out_circ, ease_out_cubic,
    ease_out_elastic, ease_out_expo, ease_out_quad, ease_out_quart, ease_out_quint,
    ease_out_sine, ease_spring, keyframes, linear, phase, repeat, sequence_param, staircase,
    steps,
)
from scriptvedit.validate import (  # noqa: F401
    _COLOR_NAME_RGB, _parse_color_rgb, _reject_unknown_keys, _require_number,
    _validate_ffmpeg_color,
)
from scriptvedit.ffmpeg import (  # noqa: F401
    _FILTER_SCRIPT_THRESHOLD, _atomic_write_bytes, _atomic_write_text, _decoder_input_args,
    _externalize_long_filters, _ffmpeg_available_encoders, _run_ffmpeg, _run_ffmpeg_to_cache,
)
from scriptvedit.cache import (  # noqa: F401
    _apply_time_effects_to_duration, _build_morph_frame_extract_cmd, _build_unified_ops,
    _checkpoint_cache_path, _compute_save_points, _file_fingerprint, _fmt_size, _is_bakeable,
    _is_cache_artifact_path, _is_pending_cache_path, _iter_cache_files, _layer_cache_paths,
    _effective_quality, _morph_cache_path, _morph_input_frame_path, _op_fingerprint_str,
    _op_prefix_fingerprint, _ops_effective_quality, _respects_fast_hint,
    _particle_cache_path, _prune_empty_dirs, _sig_key, _split_ops, _src_signature,
    _validate_morph_position, _web_cache_path, cache_clear, cache_gc, cache_stats,
)
from scriptvedit.text import (  # noqa: F401
    _ASS_NAMED_COLORS, _DEFAULT_FONT_CANDIDATES, _TEXT_ANCHORS, _build_drawtext_filter,
    _build_text_filters, _color_to_ass, _ensure_textfile, _escape_ass_text,
    _escape_counter_literal, _escape_ffpath, _escape_textfile_content, _fmt_ass_time,
    _karaoke_tokenize, _new_text_object, _parse_counter_format, _resolve_font, _text_anchor_xy,
    _text_size_opt, _text_synthetic_source, _validate_text_size, counter, karaoke, subtitles,
    text, typewriter,
)
from scriptvedit.filters.video import (  # noqa: F401
    _build_effect_filters, _build_input_args, _build_move_exprs, _build_transform_filters,
    _build_video_overlay_parts, _build_video_pre_filters, _estimate_effect_input_length,
    _get_base_dimensions, _get_media_dimensions, _optimize_filter_chain, _try_native_fade,
)
from scriptvedit.filters.audio import (  # noqa: F401
    _atempo_chain_rates, _build_audio_effect_filters, _build_audio_pre_filters,
)
from scriptvedit.objects import (  # noqa: F401
    AudioEffect, AudioEffectChain, AudioView, Effect, EffectChain, Group, Object, Transform,
    TransformChain, VideoView, _SLIDE_PAGE_KEY, _WEB_KWARGS, group, tile,
)
from scriptvedit.timeline import (  # noqa: F401
    Pause, Scene, _AnchorMarker, _PauseFactory, _ScenePad, anchor, pause, scene,
)
from scriptvedit.project import (  # noqa: F401
    Project,
)
from scriptvedit.effects.basic import (  # noqa: F401
    adelete, atempo, atrim, avolume, blur, color_shift, crop, delete, eq, fade, move, pad,
    resize, rotate, rotate_to, scale, shake, trim, wipe, zoom,
)
from scriptvedit.effects.paths import (  # noqa: F401
    _LookAtExpr, _MAX_PATH_POINTS, _bezier_path_coord_rev, _bezier_segment_expr,
    _piecewise_scalar_expr, _piecewise_tree, _validate_points, inertia, look_at, move_along,
    path_bezier, perlin, throw,
)
from scriptvedit.effects.terminal import (  # noqa: F401
    _check_particle_params, assemble_from, explode_to, morph_to,
)
from scriptvedit.effects.visual import (  # noqa: F401
    _LUT_EXTS, chroma_key, drop_shadow, glitch, glow, ken_burns, lens, lut, outline,
    perspective_warp, pixelize, vignette,
)
from scriptvedit.effects.composite import (  # noqa: F401
    _BLEND_MODES, _BLEND_MODE_ALIASES, _parse_color_alpha, _register_material_dep,
    _validate_mask_image, blend_mode, blur_background_fill, mask, mask_wipe, opacity, pip,
    progress_bar, rounded,
)
from scriptvedit.effects.time import (  # noqa: F401
    freeze_frame, reverse, speed,
)
from scriptvedit.audio import (  # noqa: F401
    Narration, _probe_audio_length, _validate_audio_source, audio_sequence, audio_viz,
    beat_sync, duck_under, loop, narrate, sfx, voice,
)
from scriptvedit.media import (  # noqa: F401
    _XFADE_TRANSITIONS, _finalize_generated_object, _validate_xfade_kind,
    _xfade_scale_chain, slideshow, transition, video_sequence,
)
from scriptvedit.web import (  # noqa: F401
    _TEMPLATES_DIR, _data_hash, _resolve_size, _template_path, arrow, bubble, circle, diagram,
    label, rect, slide, spotlight, subtitle, subtitle_box,
)
from scriptvedit.formula import (  # noqa: F401
    _FORMULA_CACHE_DIR, _FORMULA_MAX_PX, _FORMULA_TEMPLATE, _FORMULA_VER,
    _KATEX_DIR, _build_formula_spec, _formula_cache_path, _import_playwright,
    _katex_fingerprint, _new_formula_object, _render_formula_png, _validate_color,
    _validate_latex, formula, formula_lines,
)
from scriptvedit.plugins import (  # noqa: F401
    PluginError, _AUTOLOADED_PLUGIN_DIRS, _EFFECT_PLUGINS, _EffectPluginSpec,
    _LOADED_PLUGIN_FILES, _PLUGIN_PARAM_TYPES, _autoload_plugins, _build_plugin_effect_filters,
    _build_plugin_params, _coerce_plugin_param, _make_plugin_factory, _plugin_code_ffp,
    _plugin_ctx, _validate_plugin_schema, effect_plugin, load_plugin, load_plugins,
    plugin_manifest, unregister_plugin,
)
from scriptvedit.manifest import (  # noqa: F401
    MANIFEST_VERSION, _MANIFEST_CATEGORIES, _MANIFEST_CATEGORY_MEMBERS, _MANIFEST_COLOR_PARAMS,
    _MANIFEST_CONSTRAINTS, _MANIFEST_EASE_CURVES, _MANIFEST_EASE_DIRS, _MANIFEST_ENTRY_SECTIONS,
    _MANIFEST_EXAMPLES, _MANIFEST_EXPR_GROUPS, _MANIFEST_INTERNAL_OPS, _MANIFEST_KIND_SECTIONS,
    _MANIFEST_META_NAMES, _MANIFEST_NOTES, _MANIFEST_PARAM_META, _MANIFEST_SUMMARIES,
    _MANIFEST_USAGE, _manifest_choices, _manifest_class_entry, _manifest_constructed_names,
    _manifest_doc_param_descs, _manifest_entry, _manifest_enums, _manifest_expr_group,
    _manifest_filter_kind, _manifest_filter_name, _manifest_jsonable, _manifest_md_entry,
    _manifest_param_type, _manifest_params, _manifest_signature, _manifest_summary, describe,
    describe_markdown,
)
from scriptvedit.cli import (  # noqa: F401
    _WATCH_EXTENSIONS, _WATCH_SKIP_DIRS, _main, _snapshot_mtimes, _watch_targets, watch,
)


# --- プラグイン自動読込（import 時: カレントディレクトリの plugins/）---
# 環境変数 SCRIPTVEDIT_NO_PLUGINS を設定すると自動読込を無効化できる。
# `python -m scriptvedit` でも本モジュールは正規名 "scriptvedit" で一度だけロードされる。
_autoload_plugins(os.getcwd())
