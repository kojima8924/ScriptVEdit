# -*- coding: utf-8 -*-

import math
import warnings

# context は scriptvedit 内 import を持たない葉なので先頭で import できる。
from scriptvedit.context import current_project

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.expr import _resolve_param
from scriptvedit.objects import Effect, Object
from scriptvedit.state import _FLY_COLOR_PATHS, _FLY_MATCH_MODES, _FLY_STAGGER_BY, _TERMINAL_TIMING_KEYS, _suggest_hint
from scriptvedit.validate import _reject_unknown_keys, _require_number, _require_time


def _validate_terminal_input_object(func, name, obj):
    """morph_to(target)/assemble_from(source) に渡すObjectの早期検証。

    生成処理はPILへ obj.source だけを渡すため、Transform/Effect付きObjectを
    受理すると加工が黙って無視される（Objectは消費されるのに）。transition()
    と同じく構築時点で明示拒否する。media typeも画像のみ（動画は最終フレーム
    抽出等の暗黙変換をせず明示エラー）。監査 issue #15 P1。
    """
    if obj.media_type == "text":
        raise ValueError(
            f"{func}: {name} は画像のみ対応です。text() / typewriter() / counter() などの"
            f"テキスト Object は実体の画像を持たないため渡せません。文字を {name} にするには "
            f"text_image(\"文字\", size=..., font=...) で透過 PNG の画像 Object を作って"
            f"渡してください。")
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


def _check_timing_params(func, params):
    """delay / duration（終端フレーム生成Effect 共通の時間指定）を構築時に検証する。

    Object の尺との関係（delay + duration が尺に収まるか）は尺が決まる
    チェックポイントの計画時に検査する（checkpoint.py の _terminal_frame_plan）。
    """
    if params.get("delay") is not None:
        _require_time(func, "delay", params["delay"], lo=0)
    if params.get("duration") is not None:
        _require_time(func, "duration", params["duration"],
                      lo=0, lo_exclusive=True)


def morph_to(target, blend=None, **morph_params):
    """モーフィングEffect: 画像→画像のモーフ動画を生成（既定は形状ベースの sdf）

    morph_params: method（"sdf" / "transport"）と各方式のキー、
      共通の delay（動き出すまでの秒）/ duration（動く秒数。後は最後のコマを保持）。
    """
    if not isinstance(target, Object):
        raise TypeError(f"morph_to の target は Object のみ: {type(target)}")
    _validate_terminal_input_object("morph_to", "target", target)
    _check_timing_params("morph_to", morph_params)
    # パラメータのタイポはレンダ深部（チェックポイント生成後）ではなく
    # 構築時点で検出する。morph モジュールが無い環境ではレンダ時に検出される
    try:
        from scriptvedit.morph import MORPH_PARAM_KEYS
    except ImportError:
        pass
    else:
        _reject_unknown_keys("morph_to", morph_params,
                             set(MORPH_PARAM_KEYS) | set(_TERMINAL_TIMING_KEYS))
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
    """explode_to/assemble_from のパラメータの誤りを構築時に検出"""
    _check_timing_params(func, params)
    if params.get("expand") is not None:
        _require_number(func, "expand", params["expand"], lo=0)
    if "fade" in params and not isinstance(params["fade"], bool):
        raise ValueError(
            f"{func}: fade は True / False で指定してください: {params['fade']!r}")
    try:
        from scriptvedit.morph import (ASSEMBLE_PARAM_KEYS, EXPLODE_PARAM_KEYS,
                                       _resolve_point)
    except ImportError:
        return
    valid = EXPLODE_PARAM_KEYS if func == "explode_to" else ASSEMBLE_PARAM_KEYS
    _reject_unknown_keys(func, params, set(valid) | set(_TERMINAL_TIMING_KEYS))
    for key in ("toward", "from_point"):
        if params.get(key) is not None:
            _resolve_point(params[key], f"{func}: {key}")


def explode_to(blend=None, **particle_params):
    """パーティクル飛散Effect: 適用対象自身が粒子化して飛散する。

    morph_to と同じ機構でベイクされる（中間フレーム抽出→mkvキャッシュ）。
    bakeable opsの末尾に配置する必要がある。blend で進行カーブを指定できる。
    particle_params: max_pixels, speed, gravity, spread, swirl,
      particle_size, seed, dissolve, expand（None=自動）, fade, toward,
      delay, duration。
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
    particle_params は explode_to と同じ（toward の代わりに from_point）。
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


def _fly_pair(name, value, lo=None, hi=None):
    """fly_to の (x, y) 型の引数を検証して float の組にする（Expr / lambda は不可）"""
    try:
        a, b = value
    except (TypeError, ValueError):
        raise ValueError(
            f"fly_to: {name} は数値2つの組 (x, y) で指定してください: {value!r}") from None
    out = []
    for v in (a, b):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"fly_to: {name} は数値2つの組で指定してください: {value!r}")
        if not math.isfinite(v):
            raise ValueError(f"fly_to: {name} に NaN / 無限大は使えません: {value!r}")
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            raise ValueError(
                f"fly_to: {name} の各値は {lo}〜{hi} の範囲で指定してください: {value!r}")
        out.append(float(v))
    return tuple(out)


def _fly_choice(name, value, choices):
    if value not in choices:
        raise ValueError(
            f"fly_to: {name} は {list(choices)} のいずれか: {value!r}"
            f"{_suggest_hint(value, choices)}")
    return value


def _check_fly_target_opaque(target):
    """target に不透明な画素（α>0.1）が1つも無ければ構築時に ValueError。

    Pillow が無い・読めない素材は生成時（morph_flight.py）の同じ検査に任せる。
    """
    try:
        from PIL import Image
    except ImportError:
        return
    try:
        with Image.open(target.source) as im:
            top = im.convert("RGBA").getchannel("A").getextrema()[1]
    except (OSError, ValueError):
        return
    if top <= 25:   # α>0.1 は 8bit で 26 以上
        raise ValueError(
            f"fly_to: target '{target.source}' に不透明な画素（α>0.1）がありません"
            f"（全面が透明な画像は粒にできません）")


def fly_to(target, blend=None, *, offset=(0, 0), max_pixels=12000, match="ot",
           arc=0.25, swirl=0.0, stagger=0.3, stagger_by="x", particle_size=2,
           color_path="oklab", dissolve=(0.15, 0.15), seed=0,
           delay=None, duration=None):
    """粒子の輸送モーフEffect: 絵 A の粒が飛んで、離れた所に置いた絵 B（target）になる

    適用した Object の絵（A。fly_to の前の Transform / Effect を掛けた後）の不透明な画素が
    粒になり、offset の位置に置いた target の画素へ飛んで B になる。最初のコマは A、
    最後のコマは offset の位置の B と画素一致する（終わった後は B を保持）。
    重なる形どうしの変形は morph_to（sdf）、離れた形どうしは fly_to。
    morph_to と同じ機構でベイクされ、bakeable ops の末尾に1つだけ置ける。

    target: 加工していない画像 Object（text_image も可）。消費されて Project から外れる
    blend: 全体の進行カーブ（既定は直線）。粒ごとの動きは smoothstep で加減速する
    offset: A の中心から B の中心までのずれ (dx, dy) px（A の絵の px。右と下が正）
    max_pixels: 粒の数の上限（粒の数は min(max_pixels, 多い方の画素数)）
    match: 粒の対応。ot=スライスした最適輸送 / angle=角度の順 / random=無作為
    arc: 道すじのふくらみ（距離に対する比。正で進む向きの右手側）
    swirl: 道すじを中点のまわりに回す角度 rad（道の半ばで最大。正で時計回り）
    stagger: 出発の遅れの幅（全体の進行度に対する比。0〜0.95）
    stagger_by: 出発の順番。x / y=横 / 縦の並びで進む向きの先頭から / distance=遠くへ行く粒から / random
    particle_size: 粒（円）の半径 px
    color_path: 粒の色の通り道。oklab=直線 / oklch=色相を回す
    dissolve: (a, b)。最初の a の区間で A の絵から粒へ、最後の b の区間で粒から B の絵へ
    seed: 乱数の種（同じ値なら同じ絵）
    delay / duration: 動き出すまでの秒・動く秒数（morph_to と同じ。後は B を保持）

    キャンバスは A の箱・B の箱・粒の道すじを覆い、A の中心に対して左右・上下それぞれ
    対称に広がる（move の anchor は余白を除いた A の箱が基準）。4096px を超えると ValueError。
    """
    func = "fly_to"
    if not isinstance(target, Object):
        raise TypeError(f"fly_to の target は Object のみ: {type(target)}")
    _validate_terminal_input_object(func, "target", target)
    if (isinstance(max_pixels, bool) or not isinstance(max_pixels, int)
            or not 1 <= max_pixels <= 100000):
        raise ValueError(
            f"fly_to: max_pixels は 1〜100000 の整数で指定してください: {max_pixels!r}")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError(f"fly_to: seed は整数で指定してください: {seed!r}")
    d_in, d_out = _fly_pair("dissolve", dissolve, 0.0, 1.0)
    if d_in + d_out > 1.0 + 1e-9:
        raise ValueError(
            f"fly_to: dissolve=(a, b) は a + b <= 1 にしてください: {dissolve!r}")
    params = {
        "offset": _fly_pair("offset", offset),
        "max_pixels": max_pixels,
        "match": _fly_choice("match", match, _FLY_MATCH_MODES),
        "arc": float(_require_number(func, "arc", arc, -4.0, 4.0)),
        "swirl": float(_require_number(func, "swirl", swirl, -50.0, 50.0)),
        "stagger": float(_require_number(func, "stagger", stagger, 0.0, 0.95)),
        "stagger_by": _fly_choice("stagger_by", stagger_by, _FLY_STAGGER_BY),
        "particle_size": float(_require_number(
            func, "particle_size", particle_size, 0.5, 32.0)),
        "color_path": _fly_choice("color_path", color_path, _FLY_COLOR_PATHS),
        "dissolve": (d_in, d_out),
        "seed": seed,
    }
    # 時間指定は与えたものだけを持つ（morph_to / explode_to と同じ形。鍵も同じ形になる）
    timing = {k: v for k, v in (("delay", delay), ("duration", duration))
              if v is not None}
    _check_timing_params(func, timing)
    _check_fly_target_opaque(target)
    # ターゲットObjectをProjectから除外（粒の行き先として消費される）
    proj = current_project()
    if proj is not None and target in proj.objects:
        proj.objects.remove(target)
        warnings.warn(
            f"fly_to: ターゲット '{target.source}' は粒の行き先として消費されるため"
            f"Projectから自動的に除外されました。")
    # ターゲット素材をレイヤー依存として記録（morph_to と同じ理由）
    if proj is not None and proj._current_layer_file:
        tgt_deps = getattr(target, '_origin_sources', None) or [target.source]
        proj._extra_layer_deps.setdefault(
            proj._current_layer_file, []).extend(tgt_deps)
    blend = _resolve_param(lambda u: u) if blend is None else _resolve_param(blend)
    eff = Effect("fly_to", blend=blend, **params, **timing)
    eff._fly_target = target
    return eff
