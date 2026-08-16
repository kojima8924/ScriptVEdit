# -*- coding: utf-8 -*-

import warnings

# context は scriptvedit 内 import を持たない葉なので先頭で import できる。
from scriptvedit.context import current_project

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.expr import _resolve_param
from scriptvedit.objects import Effect, Object
from scriptvedit.validate import _reject_unknown_keys


def _validate_terminal_input_object(func, name, obj):
    """morph_to(target)/assemble_from(source) に渡すObjectの早期検証。

    生成処理はPILへ obj.source だけを渡すため、Transform/Effect付きObjectを
    受理すると加工が黙って無視される（Objectは消費されるのに）。transition()
    と同じく構築時点で明示拒否する。media typeも画像のみ（動画は最終フレーム
    抽出等の暗黙変換をせず明示エラー）。監査 issue #15 P1。
    """
    if obj.transforms or obj.effects or obj.audio_effects:
        raise ValueError(
            f"{func}: {name} に Transform/Effect が適用されています"
            f"（'{obj.source}'。生成処理は元素材しか読まないため、"
            f"加工が黙って無視されます）。"
            f"先に compute() で素材化してから渡してください。")
    if obj.media_type != "image":
        raise ValueError(
            f"{func}: {name} は画像のみ対応です"
            f"（'{obj.source}' は {obj.media_type}）。"
            f"動画等は compute() やフレーム抽出で静止画にしてから渡してください。")


def morph_to(target, blend=None, **morph_params):
    """モーフィングEffect: 画像→画像の最適輸送モーフ動画を生成"""
    if not isinstance(target, Object):
        raise TypeError(f"morph_to の target は Object のみ: {type(target)}")
    _validate_terminal_input_object("morph_to", "target", target)
    # パラメータのタイポはレンダ深部（チェックポイント生成後）ではなく
    # 構築時点で検出する。morph モジュールが無い環境ではレンダ時に検出される
    try:
        from scriptvedit.morph import MORPH_PARAM_KEYS
    except ImportError:
        pass
    else:
        _reject_unknown_keys("morph_to", morph_params, MORPH_PARAM_KEYS)
    # ターゲットObjectをProjectから除外（morphに消費される）
    proj = current_project()
    if proj is not None and target in proj.objects:
        proj.objects.remove(target)
        warnings.warn(
            f"morph_to: ターゲット '{target.source}' はモーフィングに消費されるため"
            f"Projectから自動的に除外されました。"
        )
    # ターゲット素材をレイヤー依存として記録（objectsから除外されるため
    # _layer_sourcesの通常記録に載らず、キャッシュ鮮度検証から漏れるのを防ぐ）
    if proj is not None and proj._current_layer_file:
        tgt_deps = getattr(target, '_origin_sources', None) or [target.source]
        proj._extra_layer_deps.setdefault(
            proj._current_layer_file, []).extend(tgt_deps)
    if blend is None:
        blend = _resolve_param(lambda u: u * u * (3 - 2 * u))
    else:
        blend = _resolve_param(blend)
    eff = Effect("morph_to", blend=blend, **morph_params)
    eff._morph_target = target
    return eff


def _check_particle_params(func, params):
    """explode_to/assemble_from のパラメータのタイポを構築時に検出"""
    try:
        from scriptvedit.morph import PARTICLE_PARAM_KEYS
    except ImportError:
        return
    _reject_unknown_keys(func, params, PARTICLE_PARAM_KEYS)


def explode_to(blend=None, **particle_params):
    """パーティクル飛散Effect: 適用対象自身が粒子化して飛散する。

    morph_to と同じ機構でベイクされる（中間フレーム抽出→mkvキャッシュ）。
    bakeable opsの末尾に配置する必要がある。blend で進行カーブを指定できる。
    particle_params: max_pixels, speed, gravity, spread, swirl,
      particle_size, seed, dissolve, expand。
    """
    _check_particle_params("explode_to", particle_params)
    if blend is None:
        blend = _resolve_param(lambda u: u)
    else:
        blend = _resolve_param(blend)
    return Effect("explode_to", blend=blend, **particle_params)


def assemble_from(source, blend=None, **particle_params):
    """パーティクル集合Effect: source の粒子が集合して画像になる。

    適用したObjectは「source が集合していくアニメーション」に置き換わる
    （source はモーフ同様Projectから消費される）。morph_to と同じベイク機構。
    bakeable opsの末尾に配置する。
    """
    if not isinstance(source, Object):
        raise TypeError(f"assemble_from の source は Object のみ: {type(source)}")
    _validate_terminal_input_object("assemble_from", "source", source)
    _check_particle_params("assemble_from", particle_params)
    proj = current_project()
    if proj is not None and source in proj.objects:
        proj.objects.remove(source)
        warnings.warn(
            f"assemble_from: source '{source.source}' は集合アニメに消費されるため"
            f"Projectから自動的に除外されました。")
    # source素材をレイヤー依存として記録（objectsから外れるため鮮度検証に載せる）
    if proj is not None and proj._current_layer_file:
        src_deps = getattr(source, '_origin_sources', None) or [source.source]
        proj._extra_layer_deps.setdefault(
            proj._current_layer_file, []).extend(src_deps)
    if blend is None:
        blend = _resolve_param(lambda u: u)
    else:
        blend = _resolve_param(blend)
    eff = Effect("assemble_from", blend=blend, **particle_params)
    eff._assemble_source = source
    return eff
