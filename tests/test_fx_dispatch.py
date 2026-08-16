# -*- coding: utf-8 -*-
"""_FX_BUILDERS / _FX_HANDLED_ELSEWHERE の網羅性メタテスト（監査 項目12）。

_build_effect_filters は「未登録の Effect 名を黙って捨てる」ことをやめ、
未知名で ValueError を投げる。ここではその前提として

  「manifest が公称する全 Effect を実際に構築して得た内部 Effect 名の集合が
    _FX_BUILDERS ∪ _FX_HANDLED_ELSEWHERE に完全に含まれる」

を固定する。zoom→scale / throw→move のようなデシュガーがあるため、
公開ファクトリ名ではなく**実構築した Effect.name**で突き合わせるのが要点。
"""
import os
import warnings

import pytest

from scriptvedit import (
    Object, Project, asset, assemble_from, blend_mode, color_shift,
    freeze_frame, inertia, ken_burns, look_at, lut, mask, mask_wipe, morph_to,
    move_along, opacity, path_bezier, perspective_warp,
    rotate_to, rounded, speed, throw, trim, zoom,
)
from scriptvedit.filters.video import (
    _FX_BUILDERS, _FX_HANDLED_ELSEWHERE, _build_effect_filters)
from scriptvedit.manifest import describe
from scriptvedit.objects import Effect

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CUBE = os.path.join(TESTS_DIR, "layers", "test43_identity.cube")

# 既定引数だけでは構築できない Effect の引数フィクスチャ。
# ここに足すのは「必須引数がある」ものだけ。フィクスチャ不要の Effect を
# 惰性でここへ足すとメタテストが実装から離れていくので入れないこと。
_FIXTURES = {
    "assemble_from": lambda: assemble_from(Object(asset("images/shape_dots.png"))),
    "blend_mode": lambda: blend_mode("screen"),
    "color_shift": lambda: color_shift(hue=30),
    "freeze_frame": lambda: freeze_frame(0.5, 0.5),
    "inertia": lambda: inertia(0.1, 0.1),
    "ken_burns": lambda: ken_burns((0, 0, 100, 100), (10, 10, 80, 80)),
    "look_at": lambda: look_at(move_along([(0, 0), (1, 1)])),
    "lut": lambda: lut(_CUBE),
    "mask": lambda: mask(asset("images/mask_circle.png")),
    "mask_wipe": lambda: mask_wipe(asset("images/mask_gradient.png")),
    "morph_to": lambda: morph_to(Object(asset("images/shape_dots.png"))),
    "move_along": lambda: move_along([(0, 0), (1, 1)]),
    "opacity": lambda: opacity(0.5),
    "path_bezier": lambda: path_bezier(
        (0, 0), (0.3, 0.2), (0.6, 0.8), (1, 1)),
    "perspective_warp": lambda: perspective_warp(0, 0, 100, 0, 0, 100, 100, 100),
    # repeat は公開ファクトリを持たない DSL 糖衣（obj * n）でのみ生成される
    "repeat": lambda: Object(asset("video/guitar_noaudio.mp4"))[0:1] * 3,
    "rotate_to": lambda: rotate_to(deg=30),
    "rounded": lambda: rounded(8),
    "speed": lambda: speed(2.0),
    "throw": lambda: throw(0.1, -0.2),
    # trim() は「何もしない trim」を拒否するので必ず尺を渡す
    "trim": lambda: trim(2),
    "zoom": lambda: zoom(1.5),
}


def _runtime_names(built):
    """ファクトリの戻り値（Effect / EffectChain / Object）から内部 Effect 名を取り出す"""
    if isinstance(built, Effect):
        return [built.name]
    if isinstance(built, Object):
        return [e.name for e in built.effects]
    return [e.name for e in built.effects]  # EffectChain


def _construct_all():
    """manifest の全 effect を構築し {公開名: [内部Effect名]} を返す"""
    import scriptvedit as sv
    result = {}
    # Project._current が無いと DSL 糖衣（obj * n）が尺を確定できない
    p = Project()
    p.configure(width=320, height=180, fps=15)
    with warnings.catch_warnings():
        # morph_to/assemble_from はターゲットの自動除外を warn するが本題ではない
        warnings.simplefilter("ignore")
        for entry in describe()["effects"]:
            name = entry["name"]
            factory = _FIXTURES.get(name) or getattr(sv, name, None)
            assert factory is not None, (
                f"{name}: 構築手段が無い（_FIXTURES へ追加してください）")
            result[name] = _runtime_names(factory())
    return result


def test_fixture_table_is_minimal_and_nonempty():
    """フィクスチャ表が空でなく、かつ不要な項目で肥大していないこと"""
    import scriptvedit as sv
    assert _FIXTURES, "フィクスチャ表が空（必須引数のある Effect を構築できていない）"
    manifest_names = {e["name"] for e in describe()["effects"]}
    assert set(_FIXTURES) <= manifest_names, (
        f"manifest に無い名前が残っている: {sorted(set(_FIXTURES) - manifest_names)}")
    # 「既定引数で構築できるのにフィクスチャを持っている」ものは肥大なので落とす
    # （repeat は公開ファクトリ自体が別物（Expr の repeat）なので対象外）
    redundant = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for name in _FIXTURES:
            if name == "repeat":
                continue
            fn = getattr(sv, name, None)
            if fn is None:
                continue
            try:
                fn()
            except Exception:
                continue
            redundant.append(name)
    assert not redundant, f"既定引数で構築できるのでフィクスチャ不要: {redundant}"


def test_fx_dispatch_tables_cover_every_effect():
    """全 Effect の内部名が _FX_BUILDERS ∪ _FX_HANDLED_ELSEWHERE に含まれる"""
    known = set(_FX_BUILDERS) | set(_FX_HANDLED_ELSEWHERE)
    missing = {}
    for public, inames in _construct_all().items():
        unknown = [n for n in inames if n not in known]
        if unknown:
            missing[public] = unknown
    assert not missing, (
        f"ディスパッチ表に未登録の Effect 名: {missing}。"
        f"_FX_BUILDERS（フィルタを出す）か _FX_HANDLED_ELSEWHERE"
        f"（他の段で処理する）へ登録してください")


def test_fx_dispatch_tables_have_no_dead_entries():
    """逆方向: 表に載っているが実際には構築されない名前が無いこと"""
    built = set()
    for inames in _construct_all().values():
        built |= set(inames)
    # delete は _append_effect が obj.effects へ載せずフラグ化するため構築名に現れない
    # （名前自体は生きているので表からは外さない）
    allowed_absent = {"delete"}
    assert allowed_absent, "許容リストが空（意図しない緩和が入っている）"
    dead = (set(_FX_BUILDERS) | set(_FX_HANDLED_ELSEWHERE)) - built - allowed_absent
    assert not dead, f"どのファクトリからも構築されない表エントリ: {sorted(dead)}"


def test_fx_tables_are_disjoint():
    """同じ名前が両方の表にあると、どちらが正か判断できなくなる"""
    both = set(_FX_BUILDERS) & set(_FX_HANDLED_ELSEWHERE)
    assert not both, f"両方の表に重複登録: {sorted(both)}"


def test_unknown_effect_name_raises():
    """未登録の Effect 名は黙って捨てず ValueError で落とす"""
    obj = Object(asset("images/shape_dots.png"))
    obj.effects.append(Effect("no_such_effect_xyz"))
    with pytest.raises(ValueError, match="未登録の Effect 名"):
        _build_effect_filters(obj, 0, 3)
