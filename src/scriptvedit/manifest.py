# -*- coding: utf-8 -*-

import re
import builtins as _builtins
import inspect as _inspect

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.effects.composite import _BLEND_MODES, _BLEND_MODE_ALIASES
from scriptvedit.cache import _LAYER_CACHE_QUALITY, _respects_fast_hint
from scriptvedit.expr import Expr
from scriptvedit.manifest_data import (
    MANIFEST_VERSION,
    _MANIFEST_CATEGORIES,
    _MANIFEST_COLOR_PARAMS,
    _MANIFEST_CONSTANTS,
    _MANIFEST_CONSTRAINTS,
    _MANIFEST_DETAILS,
    _MANIFEST_EASE_CURVES,
    _MANIFEST_EASE_DIRS,
    _MANIFEST_ENTRY_SECTIONS,
    _MANIFEST_EXAMPLES,
    _MANIFEST_EXPR_GROUPS,
    _MANIFEST_INTERNAL_OPS,
    _MANIFEST_KIND_SECTIONS,
    _MANIFEST_MD_CHOICES_MAX,
    _MANIFEST_META_NAMES,
    _MANIFEST_NAME_PARTIAL_MIN,
    _MANIFEST_NOTES,
    _MANIFEST_PARAM_ENUM_KEY,
    _MANIFEST_PARAM_META,
    _MANIFEST_SUMMARIES,
    _MANIFEST_USAGE,
)
from scriptvedit.media import _XFADE_TRANSITIONS
from scriptvedit.objects import Object
from scriptvedit.plugins import _EFFECT_PLUGINS, _PLUGIN_PARAM_TYPES
from scriptvedit.project import Project
from scriptvedit.state import _BAKEABLE_EFFECTS, _ENCODER_MAP, _PLACEMENT_ANCHORS, _PRESETS, _pkg_all, _pkg_ns, _suggest_hint


# --- ケイパビリティ・マニフェスト（describe） ---
# 目的: コーディングAIが本体（数千行）を読まずに、使える機能・シグネチャ・制約を
# 機械可読な形で発見できるようにする。
#
# 設計方針（二重管理の最小化）:
#   - シグネチャ / 既定値 / 必須か / 要約(docstring 1行目) / bakeable / 内部Effect名は
#     inspect と実装コードから「自動導出」する。新しいファクトリを足せば自動で載る。
#   - 自動導出できない知識（型の細かい区別・choices・落とし穴の notes・例）だけを
#     manifest_data.py の補助テーブルで宣言する。
#   - 出力スキーマはプラグイン（@effect_plugin の params）と同一形式に揃える。
#     型: number/int/expr/color/ffcolor/string/bool/choice/any


def _brackets_balanced(text):
    """全角/半角の丸括弧・鉤括弧が閉じているか（説明文の途中切断の検出用）"""
    for open_c, close_c in (("（", "）"), ("(", ")"), ("「", "」")):
        if text.count(open_c) != text.count(close_c):
            return False
    return True


def _manifest_doc_param_descs(doc):
    """docstring から `param: 説明` 形式のパラメータ説明を best-effort で抽出する"""
    out = {}
    if not doc:
        return out
    # 行・句読点で区切って「名前: 説明」を拾う（例: "direction: left/right/up/down"）
    segments = []
    for line in doc.splitlines():
        # 「。」で切ると括弧の途中で切れることがある
        # （"縁取りの色（ffcolor形式。例 'black'）" → "縁取りの色（ffcolor形式"）。
        # 括弧が閉じるまで「。」を戻しながら連結する。
        buf = ""
        for frag in re.split(r"。", line):
            buf = f"{buf}。{frag}" if buf else frag
            if _brackets_balanced(buf):
                if buf.strip():
                    segments.append(buf)
                buf = ""
        if buf.strip():
            segments.append(buf)
    for seg in segments:
        m = re.match(r"^\s*([a-z_][a-z0-9_]*(?:\s*[,/]\s*[a-z_][a-z0-9_]*)*)\s*[:：]\s*(.+)$",
                     seg)
        if not m:
            continue
        desc = m.group(2).strip()
        for key in re.split(r"[,/]", m.group(1)):
            key = key.strip()
            if key:
                out.setdefault(key, desc)
    return out


def _manifest_param_type(default, pname, src, meta):
    """パラメータの型を自動推定する（補助テーブル > 実装コード > 既定値の型）"""
    if meta and "type" in meta:
        return meta["type"]
    # _resolve_param() を通す引数は Expr/lambda を受け取れる（=liveアニメ可）
    if src and re.search(r"_resolve_param\(\s*%s\b" % re.escape(pname), src):
        return "expr"
    if src and re.search(r"_resolve_param\(\s*kwargs\[[\"']%s[\"']\]" % re.escape(pname), src):
        return "expr"
    if isinstance(default, bool):
        return "bool"
    if isinstance(default, int):
        return "int"
    if isinstance(default, float):
        return "number"
    if isinstance(default, str):
        if pname in _MANIFEST_COLOR_PARAMS:
            # ffmpeg 色文字列（0xRRGGBBAA / name@alpha）か、色名/#RRGGBB か
            if default.startswith("0x") or "@" in default:
                return "ffcolor"
            return "color"
        return "string"
    if default is None and pname in _MANIFEST_COLOR_PARAMS:
        return "color"
    return "any"


def _manifest_choices(fn_name, pname, meta):
    """choices を解決する（補助テーブルの None は実装側の集合から埋める）"""
    if meta and "choices" in meta:
        ch = meta["choices"]
        if ch is not None:
            return list(ch)
        # None → 実装側の集合を参照
        if pname in ("mode",):
            return sorted(_BLEND_MODES)
        if pname in ("transition", "kind"):
            return sorted(_XFADE_TRANSITIONS)
        if pname == "anchor":
            # 実装が実際に区別できる基準点だけを公称する（state.py が正）
            return list(_PLACEMENT_ANCHORS)
        if pname == "preset":
            return sorted(_PRESETS)
        if pname == "encoder":
            return sorted(_ENCODER_MAP)
    return None


def _manifest_params(fn, fn_name):
    """inspect.signature から params スキーマを自動導出する（プラグインと同じ形式）"""
    params = {}
    try:
        sig = _inspect.signature(fn)
    except (TypeError, ValueError):
        sig = None
    try:
        src = _inspect.getsource(fn)
    except (OSError, TypeError):
        src = ""
    doc_descs = _manifest_doc_param_descs(_inspect.getdoc(fn) if fn else None)

    ordered = []
    if sig is not None:
        for pname, p in sig.parameters.items():
            if pname in ("self", "cls"):
                continue
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                # *args / **kwargs はシグネチャから導出できない → 補助テーブル頼み
                continue
            ordered.append((pname, p))

    for pname, p in ordered:
        meta = _MANIFEST_PARAM_META.get((fn_name, pname), {})
        has_default = p.default is not p.empty
        # 補助テーブルが default を宣言していればそちらを優先する。
        # move(x=None, ...) のように「未指定を表す番兵 None」をシグネチャに
        # 置く関数では、実効既定値（x=0.5 / anchor='center'）は宣言側にしかない。
        if "default" in meta:
            default = meta["default"]
        else:
            default = p.default if has_default else None
        entry = {
            "type": _manifest_param_type(default, pname, src, meta),
            "default": default if _manifest_jsonable(default) else repr(default),
            "required": meta.get("required", not has_default),
        }
        desc = meta.get("desc") or doc_descs.get(pname)
        if desc:
            entry["desc"] = desc
        for k in ("min", "max"):
            if meta.get(k) is not None:
                entry[k] = meta[k]
        choices = _manifest_choices(fn_name, pname, meta)
        if choices:
            entry["choices"] = choices
            entry["type"] = "choice"
        params[pname] = entry

    # 補助テーブルにしかない引数（**kwargs で受けるもの）を追加
    for (mfn, mp), meta in _MANIFEST_PARAM_META.items():
        if mfn != fn_name or mp in params:
            continue
        if sig is not None and not any(
                p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL)
                for p in sig.parameters.values()):
            continue
        entry = {
            "type": meta.get("type", "any"),
            "default": meta.get("default"),
            "required": meta.get("required", False),
        }
        if meta.get("desc"):
            entry["desc"] = meta["desc"]
        for k in ("min", "max"):
            if meta.get(k) is not None:
                entry[k] = meta[k]
        choices = _manifest_choices(fn_name, mp, meta)
        if choices:
            entry["choices"] = choices
            entry["type"] = "choice"
        params[mp] = entry
    return params


def _manifest_jsonable(v):
    """JSON にそのまま載せられる値か"""
    return v is None or isinstance(v, (bool, int, float, str, list, dict))


def _manifest_summary(name, fn):
    """要約を導出: docstring 1行目 > 補助テーブル > イージング名からの自動生成"""
    doc = _inspect.getdoc(fn) if fn is not None else None
    if doc:
        first = doc.strip().split("\n")[0].strip()
        if first:
            return first
    if name in _MANIFEST_SUMMARIES:
        return _MANIFEST_SUMMARIES[name]
    m = re.match(r"^ease_(in_out|in|out)_([a-z]+)$", name)
    if m:
        d = _MANIFEST_EASE_DIRS.get(m.group(1), m.group(1))
        c = _MANIFEST_EASE_CURVES.get(m.group(2), m.group(2))
        return f"イージング関数: {c}カーブの{d}"
    return ""


def _manifest_details(name, fn):
    """docstring の2行目以降（要約に載らない本文）を返す。

    要約は1行目だけなので、単位・前提・落とし穴といった「実際に必要な情報」が
    describe から丸ごと消えていた（監査項目10）。ここで details として拾う。
    """
    doc = _inspect.getdoc(fn) if fn is not None else None
    if not doc:
        return ""
    rest = doc.split("\n", 1)
    if len(rest) < 2:
        return ""
    return rest[1].strip()


def _manifest_signature(name, fn):
    """`fade(alpha=1.0)` 形式のシグネチャ文字列"""
    try:
        return name + str(_inspect.signature(fn))
    except (TypeError, ValueError):
        return name + "(...)"


def _manifest_constructed_names(fn):
    """ファクトリ関数が構築する内部 Effect/Transform/AudioEffect 名を実装から抽出する。

    zoom() → Effect("scale") のように公開名と内部名が異なるケースがあるため、
    網羅性検証はこの内部名で突き合わせる。
    """
    try:
        src = _inspect.getsource(fn)
    except (OSError, TypeError):
        return [], None
    kinds = set()
    names = set()
    for m in re.finditer(
            r"\b(AudioEffect|Effect|Transform)\(\s*[\"']([a-z_0-9]+)[\"']", src):
        kinds.add(m.group(1))
        names.add(m.group(2))
    if not kinds:
        # 引数なしの Effect("delete") 等以外に、名前を取れないケース
        for m in re.finditer(r"\b(AudioEffect|Effect|Transform)\(", src):
            kinds.add(m.group(1))
    if "AudioEffect" in kinds:
        kind = "audio_effect"
    elif "Effect" in kinds:
        kind = "effect"
    elif "Transform" in kinds:
        kind = "transform"
    else:
        kind = None
    return sorted(names), kind


def _manifest_expr_group(name):
    if re.match(r"^ease_(in|out|in_out)_", name):
        return "イージング"
    for g, members in _MANIFEST_EXPR_GROUPS.items():
        if name in members:
            return g
    return None


def _manifest_entry(name, fn, kind, *, category=None, bakeable=None,
                    effect_names=None):
    """1エントリ（プラグインの params スキーマと同じ形式）を組み立てる"""
    entry = {
        "name": name,
        "kind": kind,
        "category": category or _MANIFEST_CATEGORIES.get(name, "その他"),
        "summary": _manifest_summary(name, fn),
        "signature": _manifest_signature(name, fn) if fn is not None else name,
        "params": _manifest_params(fn, name) if fn is not None else {},
    }
    details = _manifest_details(name, fn) or _MANIFEST_DETAILS.get(name, "")
    if details:
        entry["details"] = details
    if bakeable is not None:
        entry["bakeable"] = bakeable
    if effect_names:
        entry["effect_names"] = effect_names
    if kind in ("effect", "transform", "audio_effect"):
        internal_names = effect_names or [name]
        entry["respects_fast_hint"] = any(
            _respects_fast_hint(n) for n in internal_names)
    if name in _MANIFEST_EXAMPLES:
        entry["example"] = _MANIFEST_EXAMPLES[name]
    notes = list(_MANIFEST_NOTES.get(name, []))
    # constraints からも該当エントリの注記を引き当てる（二重管理を避ける）
    for c in _MANIFEST_CONSTRAINTS:
        if name in c["applies_to"] and c["text"] not in notes:
            notes.append(f"[{c['id']}] {c['text']}")
    if notes:
        entry["notes"] = notes
    return entry


def _manifest_class_entry(name, cls):
    """クラス（Project/Object 等）のエントリ。公開メソッドを自動列挙する"""
    methods = []
    for mname, m in _inspect.getmembers(cls, predicate=_inspect.isroutine):
        if mname.startswith("_"):
            continue
        method = {
            "name": mname,
            "signature": _manifest_signature(mname, m),
            "summary": _manifest_summary(mname, m),
        }
        mdetails = (_manifest_details(mname, m)
                    or _MANIFEST_DETAILS.get(f"{name}.{mname}", ""))
        if mdetails:
            method["details"] = mdetails
        methods.append(method)
    methods.sort(key=lambda d: d["name"])
    entry = {
        "name": name,
        "kind": "class",
        "category": _MANIFEST_CATEGORIES.get(name, "コア"),
        "summary": _manifest_summary(name, cls),
        "signature": _manifest_signature(name, cls),
        "methods": methods,
    }
    details = _manifest_details(name, cls)
    if details:
        entry["details"] = details
    if name in _MANIFEST_EXAMPLES:
        entry["example"] = _MANIFEST_EXAMPLES[name]
    return entry


def _manifest_enums():
    """choices の元になる列挙（実装側の集合をそのまま公開する）"""
    return {
        "blend_mode": sorted(_BLEND_MODES),
        "blend_mode_aliases": {k: v for k, v in sorted(_BLEND_MODE_ALIASES.items())},
        "xfade_transition": sorted(_XFADE_TRANSITIONS),
        "preset": sorted(_PRESETS),
        "encoder": sorted(_ENCODER_MAP),
        "wipe_direction": ["left", "right", "up", "down"],
        # 実装が区別できる基準点（state.py の _PLACEMENT_ANCHORS が正）。
        # left/right/top/bottom は未実装で構築時に ValueError になる
        "anchor": list(_PLACEMENT_ANCHORS),
        # Project.layer(cache=...) の検証タプル（project.py）と一致させること。
        # 整合は tests/test_issue17_docs.py が実装側の許可値と突き合わせて検証する
        "layer_cache": ["off", "auto", "use", "make"],
        # Project.layer(cache_quality=...) の許可値（cache.py の
        # _LAYER_CACHE_QUALITY が正）。中間ファイルの品質/サイズを選ぶ
        "layer_cache_quality": sorted(_LAYER_CACHE_QUALITY),
        "quality": ["final", "fast"],
        "policy": ["auto", "force", "off"],
        "media_type": ["image", "video", "audio", "web"],
        "param_type": list(_PLUGIN_PARAM_TYPES),
        "easing": sorted(
            n for n in _pkg_all()
            if n == "linear" or re.match(r"^ease_", n) or n == "steps"),
    }


def describe(kind=None, name=None):
    """全機能の機械可読マニフェスト（JSON シリアライズ可能な dict）を返す。

    コーディングAIが本体を読まずに「使える機能・シグネチャ・制約」を発見するための入口。
    シグネチャ/型/既定値/bakeable は実装から自動導出されるため、機能追加は自動で載る。

    kind: "effect"/"transform"/"audio_effect"/"factory"/"class"/"project_method"/
          "expr"/"plugin" で絞り込む
    name: 単一エントリ名で絞り込む
    """
    effects, transforms, audio_effects, factories = [], [], [], []
    classes, exprs, metas = [], [], []

    plugin_names = set(_EFFECT_PLUGINS)
    for pname in _pkg_all():
        if pname in plugin_names:
            continue  # プラグインは plugins セクションへ
        obj = _pkg_ns().get(pname)
        if obj is None:
            continue
        if _inspect.isclass(obj):
            classes.append(_manifest_class_entry(pname, obj))
            continue
        if not callable(obj):
            continue  # PI/E/P などの定数
        if pname in _MANIFEST_META_NAMES:
            metas.append(_manifest_entry(pname, obj, "meta", category="メタAPI"))
            continue
        inames, ikind = _manifest_constructed_names(obj)
        group = _manifest_expr_group(pname)
        if ikind == "effect":
            # 内部Effect名のいずれかが bakeable なら bakeable 扱い
            bk = _builtins.bool(inames) and all(
                n in _BAKEABLE_EFFECTS for n in inames)
            effects.append(_manifest_entry(
                pname, obj, "effect", bakeable=bk, effect_names=inames))
        elif ikind == "transform":
            transforms.append(_manifest_entry(
                pname, obj, "transform", effect_names=inames))
        elif ikind == "audio_effect":
            audio_effects.append(_manifest_entry(
                pname, obj, "audio_effect", effect_names=inames))
        elif group is not None:
            exprs.append(_manifest_entry(pname, obj, "expr", category=group))
        else:
            factories.append(_manifest_entry(pname, obj, "factory"))

    # __all__ にある非callableの公開定数（pause / PI / E / P）。
    # callable 判定のループから漏れて、全 example が使うのに未収載だった。
    for cname, spec in _MANIFEST_CONSTANTS.items():
        entry = {
            "name": cname,
            "kind": "meta",
            "category": spec.get("category", "メタAPI"),
            "summary": spec["summary"],
            "signature": spec.get("signature", cname),
            "params": {},
        }
        for key in ("details", "example"):
            if spec.get(key):
                entry[key] = spec[key]
        metas.append(entry)

    # 公開ファクトリを持たない内部操作（grid 等）
    for iname, spec in sorted(_MANIFEST_INTERNAL_OPS.items()):
        entry = {
            "name": iname,
            "kind": spec["kind"],
            "category": _MANIFEST_CATEGORIES.get(iname, "その他"),
            "summary": spec["summary"],
            "signature": spec.get("signature", f"{iname}(...)"),
            "params": _manifest_params(None, iname),
            "effect_names": [iname],
        }
        if spec.get("example"):
            entry["example"] = spec["example"]
        if spec["kind"] == "transform":
            entry["respects_fast_hint"] = _respects_fast_hint(iname)
            transforms.append(entry)
        elif spec["kind"] == "effect":
            entry["bakeable"] = iname in _BAKEABLE_EFFECTS
            entry["respects_fast_hint"] = _respects_fast_hint(iname)
            effects.append(entry)
        else:
            entry["respects_fast_hint"] = _respects_fast_hint(iname)
            audio_effects.append(entry)

    # Expr のチェーンメソッド（.smooth() / .invert() ...）
    for mname, m in _inspect.getmembers(Expr, predicate=_inspect.isroutine):
        if mname.startswith("_") or mname in ("to_ffmpeg", "eval_at"):
            continue
        exprs.append({
            "name": f"Expr.{mname}",
            "kind": "expr",
            "category": "Exprチェーンメソッド",
            "summary": _manifest_summary(mname, m),
            "signature": _manifest_signature(mname, m),
            "params": _manifest_params(m, mname),
        })

    # Project のメソッド（自動列挙）
    project_methods = []
    for mname, m in _inspect.getmembers(Project, predicate=_inspect.isroutine):
        if mname.startswith("_"):
            continue
        project_methods.append(_manifest_entry(
            f"Project.{mname}", m, "project_method",
            category=_MANIFEST_CATEGORIES.get(mname, "プロジェクト")))

    # Object のメソッド（自動列挙）
    object_methods = []
    for mname, m in _inspect.getmembers(Object, predicate=_inspect.isroutine):
        if mname.startswith("_"):
            continue
        object_methods.append(_manifest_entry(
            f"Object.{mname}", m, "object_method",
            category=_MANIFEST_CATEGORIES.get(mname, "オブジェクト")))

    # プラグイン（登録済み。plugin_manifest と同じ情報 + マニフェスト共通形式）
    plugins = []
    for pname in sorted(_EFFECT_PLUGINS):
        s = _EFFECT_PLUGINS[pname]
        params = {}
        for k, v in s.params.items():
            pv = dict(v)
            pv.setdefault("type", "number")
            pv.setdefault("required", "default" not in v)
            params[k] = pv
        plugins.append({
            "name": pname,
            "kind": "plugin",
            "category": s.category,
            "bakeable": s.bakeable,
            "respects_fast_hint": False,
            "summary": s.doc,
            "signature": _manifest_signature(pname, _pkg_ns().get(pname)),
            "params": params,
            "source": s.source_file,
        })

    for lst in (effects, transforms, audio_effects, factories, classes,
                exprs, project_methods, object_methods, metas):
        lst.sort(key=lambda d: d["name"])

    manifest = {
        "library": "scriptvedit",
        "manifest_version": MANIFEST_VERSION,
        "summary": "FFmpeg を駆動する Python DSL の動画編集ライブラリ。"
                   "Project + レイヤー .py で動画を合成する。",
        "usage": _MANIFEST_USAGE,
        "constraints": _MANIFEST_CONSTRAINTS,
        "enums": _manifest_enums(),
        "effects": effects,
        "transforms": transforms,
        "audio_effects": audio_effects,
        "factories": factories,
        "objects": classes,
        "object_methods": object_methods,
        "project_methods": project_methods,
        "expr": exprs,
        "plugins": plugins,
        "meta": metas,
    }
    manifest["stats"] = {
        "effects": len(effects),
        "transforms": len(transforms),
        "audio_effects": len(audio_effects),
        "factories": len(factories),
        "objects": len(classes),
        "object_methods": len(object_methods),
        "project_methods": len(project_methods),
        "expr": len(exprs),
        "plugins": len(plugins),
        "meta": len(metas),
        "constraints": len(_MANIFEST_CONSTRAINTS),
    }

    if kind:
        manifest = _manifest_filter_kind(manifest, kind)
    if name:
        manifest = _manifest_filter_name(manifest, name)
    return manifest


def _manifest_filter_kind(manifest, kind):
    """--kind でセクションを絞る（usage は落とす: 前置きが本体を埋もれさせる）"""
    section = _MANIFEST_KIND_SECTIONS.get(kind)
    if section is None:
        hint = _suggest_hint(str(kind), _MANIFEST_KIND_SECTIONS.keys())
        raise ValueError(
            f"describe: 未知の kind '{kind}'。{hint}\n"
            f"有効な kind: {', '.join(sorted(_MANIFEST_KIND_SECTIONS))}")
    out = {k: v for k, v in manifest.items()
           if k not in _MANIFEST_ENTRY_SECTIONS and k != "usage"}
    out[section] = manifest[section]
    out["stats"] = {section: len(manifest[section])}
    return out


def _manifest_name_matches(entry_name, query):
    """--name のマッチ判定（完全一致 > 末尾要素一致 > 部分一致）"""
    if entry_name == query or entry_name.split(".")[-1] == query:
        return True
    if len(query) < _MANIFEST_NAME_PARTIAL_MIN:
        return False
    return query.lower() in entry_name.lower()


def _manifest_filter_name(manifest, name):
    """--name でエントリを絞る（部分一致・カンマ区切りの複数指定に対応）。

    usage（約10,000文字の前置き）は落とし、constraints も該当エントリに
    紐づくものだけに絞る。`--name throw` が 10,753 文字のうち約 10,000 文字を
    前置きに費やしていた（監査項目10）。
    """
    queries = [q.strip() for q in str(name).split(",") if q.strip()]
    if not queries:
        raise ValueError("describe: --name が空です（例: --name fade,scale）")
    found = []
    matched_names = set()
    all_names = []
    for section in _MANIFEST_ENTRY_SECTIONS:
        for e in manifest.get(section, []):
            all_names.append(e["name"])
            if any(_manifest_name_matches(e["name"], q) for q in queries):
                found.append((section, e))
                matched_names.add(e["name"])
    missing = [q for q in queries
               if not any(_manifest_name_matches(n, q) for n in all_names)]
    if missing:
        hint = _suggest_hint(missing[0], all_names)
        raise ValueError(
            f"describe: '{missing[0]}' という機能はありません。{hint}")
    out = {k: v for k, v in manifest.items()
           if k not in _MANIFEST_ENTRY_SECTIONS and k != "usage"}

    # 該当エントリに紐づく制約だけ残す（AI が地雷を踏まないように）
    def _applies(c):
        if not c["applies_to"]:
            return True
        return any(a in matched_names or a.split(".")[-1] in
                   {n.split(".")[-1] for n in matched_names}
                   for a in c["applies_to"])
    out["constraints"] = [c for c in manifest["constraints"] if _applies(c)]
    for section, e in found:
        out.setdefault(section, []).append(e)
    # enums も該当エントリが実際に使うものだけへ絞る（全列挙は数千文字ある）
    used_enum_keys = set()
    for _section, e in found:
        for pname, pmeta in e.get("params", {}).items():
            if pmeta.get("choices") and pname in _MANIFEST_PARAM_ENUM_KEY:
                used_enum_keys.add(_MANIFEST_PARAM_ENUM_KEY[pname])
    out["enums"] = {k: v for k, v in manifest.get("enums", {}).items()
                    if k in used_enum_keys}
    out["stats"] = {"matched": len(found)}
    return out


def _manifest_md_choices(pname, choices, default=None):
    """md 用に choices を整形する（打ち切りを明示し、既定値は必ず載せる）。

    以前は先頭8件で無言に打ち切っていたため、slideshow(transition=) の
    57値が8値に見え、しかも既定の 'fade' が候補から漏れていた（監査項目10）。
    """
    shown = [str(c) for c in choices[:_MANIFEST_MD_CHOICES_MAX]]
    rest = len(choices) - len(shown)
    if rest <= 0:
        return "/".join(shown)
    # 既定値が打ち切りで消えると「候補に無い既定値」に見えるので必ず含める
    if default is not None and str(default) not in shown:
        shown = shown[:-1] + [str(default)]
        rest += 1
    key = _MANIFEST_PARAM_ENUM_KEY.get(pname)
    where = f"enums.{key} 参照" if key else "describe の enums 参照"
    return "/".join(shown) + f"/… 他 {rest} 件（{where}）"


def _manifest_md_entry(e, lines):
    """1エントリを Markdown 化する"""
    head = f"### `{e.get('signature', e['name'])}`"
    lines.append(head)
    meta = [f"kind: {e['kind']}", f"category: {e.get('category', '-')}"]
    if "bakeable" in e:
        meta.append("bakeable: " + ("yes" if e["bakeable"] else "no（live）"))
    if "respects_fast_hint" in e:
        meta.append("fast hint: " + (
            "respected" if e["respects_fast_hint"] else "ignored（通常と同一）"))
    lines.append("*" + " / ".join(meta) + "*")
    if e.get("summary"):
        lines.append("")
        lines.append(e["summary"])
    if e.get("details"):
        lines.append("")
        lines.append(e["details"])
    if e.get("params"):
        lines.append("")
        lines.append("| 引数 | 型 | 既定 | 必須 | 説明 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for k, v in e["params"].items():
            t = v.get("type", "any")
            if v.get("choices"):
                t += " (%s)" % _manifest_md_choices(k, v["choices"], v.get("default"))
            lines.append("| `%s` | %s | `%r` | %s | %s |" % (
                k, t, v.get("default"), "○" if v.get("required") else "",
                v.get("desc", "")))
    if e.get("methods"):
        lines.append("")
        for m in e["methods"]:
            lines.append(f"- `{m['signature']}` — {m.get('summary', '')}")
            if m.get("details"):
                for dl in m["details"].splitlines():
                    lines.append(f"  {dl}")
    if e.get("example"):
        lines.append("")
        lines.append("```python")
        lines.append(e["example"])
        lines.append("```")
    if e.get("notes"):
        lines.append("")
        for n in e["notes"]:
            lines.append(f"- 注意: {n}")
    lines.append("")


def describe_markdown(manifest=None):
    """describe() の出力を人間可読な Markdown に整形する"""
    m = manifest if manifest is not None else describe()
    lines = ["# scriptvedit ケイパビリティ・マニフェスト",
             "",
             f"manifest_version: {m.get('manifest_version', MANIFEST_VERSION)}",
             "",
             m.get("summary", ""), ""]

    usage = m.get("usage")
    if usage:
        lines += ["## 使い方（AI向け）", "", usage["overview"], "", "### 概念", ""]
        lines += [f"- {c}" for c in usage["concepts"]]
        lines += ["", "### main スクリプト", "", "```python", usage["main_script"],
                  "```", "", "### レイヤーファイル", "", "```python",
                  usage["layer_file"], "```", "", "### DSL", ""]
        lines += [f"- **{k}**: `{v}`" for k, v in usage["dsl"].items()]
        lines += ["", "### プラグインの書き方", "", "```python",
                  usage["plugin_template"], "```", "", "### ワークフロー", ""]
        lines += [f"{w}" for w in usage["workflow"]]
        lines += ["", "### CLI", "", "```"] + usage["cli"] + ["```", ""]

    if m.get("constraints"):
        lines += ["## 既知の制約・落とし穴", ""]
        for c in m["constraints"]:
            lines.append(f"- **[{c['severity']}] {c['id']}**（{c['topic']}）: {c['text']}")
        lines.append("")

    if m.get("enums"):
        lines += ["## 列挙（choices）", ""]
        for k, v in m["enums"].items():
            if isinstance(v, dict):
                v = [f"{a}→{b}" for a, b in v.items()]
            lines.append(f"- **{k}**: {', '.join(str(x) for x in v)}")
        lines.append("")

    titles = {
        "effects": "Effect（時間依存効果）",
        "transforms": "Transform（静的変形）",
        "audio_effects": "AudioEffect（音声効果）",
        "factories": "ファクトリ（オブジェクト生成）",
        "objects": "クラス",
        "object_methods": "Object のメソッド",
        "project_methods": "Project のメソッド",
        "expr": "式・イージング・シーケンス",
        "plugins": "プラグイン（登録済み）",
        "meta": "メタAPI（イントロスペクション・プラグイン登録）",
    }
    for section in _MANIFEST_ENTRY_SECTIONS:
        items = m.get(section)
        if not items:
            continue
        lines += [f"## {titles.get(section, section)}（{len(items)}件）", ""]
        for e in items:
            _manifest_md_entry(e, lines)
    return "\n".join(lines)


# --- キャッシュ管理 CLI / watch モード ---
