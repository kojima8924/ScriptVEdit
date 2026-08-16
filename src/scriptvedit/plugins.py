# -*- coding: utf-8 -*-

import os
import sys
import hashlib
import warnings

# context は scriptvedit 内 import を持たない葉なので先頭で import できる。
from scriptvedit.context import current_project

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.expr import Const, _resolve_param
from scriptvedit.state import _BAKEABLE_EFFECTS, _pkg_all, _pkg_ns, _suggest_hint
from scriptvedit.validate import _parse_color_rgb, _require_number, _validate_ffmpeg_color


# --- プラグイン機構（コア無編集で Effect を追加する） ---
#
# 目的: plugins/*.py に小さなファイルを置くだけで新しい Effect を追加できるようにする。
#   1. @effect_plugin(...) でビルダーを登録 → ファクトリ関数を自動生成し
#      scriptvedit 名前空間（globals + __all__）へ注入する
#      （レイヤーファイルは `from scriptvedit import *` で読まれるため）
#   2. bakeable=True なら _BAKEABLE_EFFECTS に登録 → チェックポイント/compute経路でも
#      同じビルダーが _build_effect_filters 経由で使われる
#   3. プラグインのソースコード指紋をキャッシュ鍵に含める
#      （_op_fingerprint_str）→ プラグインを書き換えたらキャッシュが再生成される

class PluginError(Exception):
    """プラグインの登録・読み込みに関するエラー"""


class _EffectPluginSpec:
    """登録済みEffectプラグインのメタデータ"""

    def __init__(self, name, builder, params, bakeable, category, doc,
                 source_file, code_ffp):
        self.name = name
        self.builder = builder
        self.params = params            # {key: {"type","default","min","max","desc","choices"}}
        self.bakeable = bakeable
        self.category = category
        self.doc = doc
        self.source_file = source_file  # 定義元 .py（不明なら None）
        self.code_ffp = code_ffp        # ソースコードの内容ハッシュ（キャッシュ鍵用）


# 登録テーブル: name -> _EffectPluginSpec
_EFFECT_PLUGINS = {}

# 許容するパラメータ型
_PLUGIN_PARAM_TYPES = ("number", "int", "expr", "color", "ffcolor", "string", "bool", "choice")

# 予約名: scriptvedit のサブモジュール名。
# beat / tts / viz / morph / testkit 等は遅延 import されるため、import 時点では
# パッケージ名前空間に存在しない。名前衝突チェックを「既に g にある名前」だけで
# 行うと、これらの名前をプラグインが横取りでき、後続の
# `import scriptvedit.beat` が注入済みファクトリ関数に隠されて壊れる。
# （例: @effect_plugin("beat") → beat_sync() が AttributeError）
_RESERVED_NAMES_CACHE = [None]


def _reserved_plugin_names():
    """プラグイン名として禁止する予約名（scriptvedit のサブモジュール名）"""
    if _RESERVED_NAMES_CACHE[0] is None:
        names = {"beat", "tts", "viz", "morph", "testkit"}  # 遅延importされる主要モジュール
        try:
            import pkgutil
            import scriptvedit as _pkg
            names |= {m.name for m in pkgutil.iter_modules(_pkg.__path__)}
        except Exception:
            pass  # 列挙できなくても既知の予約名だけは守る
        _RESERVED_NAMES_CACHE[0] = frozenset(names)
    return _RESERVED_NAMES_CACHE[0]


def _builder_bytecode_ffp(builder):
    """ビルダー関数のバイトコード指紋（"bc-" + sha256[:16]）を返す。

    定義元ファイルを読めないとき（REPL / exec(<文字列>) / zip 同梱 / 権限）の
    フォールバック。以前はここが定数 "inline" だったため、
      * プラグイン本体を書き換えても鍵が変わらず古い焼き込みが黙って再利用される
      * 全プラグインが同じ鍵成分を共有する
    という二重の穴になっていた（監査 項目22b）。co_code と co_consts を混ぜる
    ことで、ビルダーの実装が変われば鍵も変わる。由来が判別できるよう "bc-" を
    前置する（ファイル指紋と混同しないため）。
    """
    code = getattr(builder, "__code__", None)
    if code is None:
        # 関数以外（呼び出し可能オブジェクト等）は型名+qualnameで代替する
        material = f"{type(builder)!r}:{getattr(builder, '__qualname__', '')}"
    else:
        material = repr((code.co_code, code.co_consts, code.co_names,
                         code.co_varnames))
    digest = hashlib.sha256(material.encode("utf-8", "replace")).hexdigest()
    return "bc-" + digest[:16]


def _plugin_code_ffp(builder):
    """プラグイン定義ファイルの内容ハッシュ（sha256[:16]）を返す。

    素材の指紋(_file_fingerprint)と同じ内容ハッシュ方式。プラグインを書き換えた時
    だけキャッシュを無効化し、コピー/チェックアウトでmtimeが変わっただけの再生成は
    避ける。ファイルを読めない場合はビルダーのバイトコード指紋へ落とす。
    """
    path = getattr(getattr(builder, "__code__", None), "co_filename", None)
    if not path or not os.path.exists(path):
        return None, _builder_bytecode_ffp(builder)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return path, _builder_bytecode_ffp(builder)
    return path, hashlib.sha256(data).hexdigest()[:16]


def _validate_plugin_schema(name, params):
    """params スキーマ自体の妥当性を登録時に検証する"""
    if not isinstance(params, dict):
        raise PluginError(f"プラグイン '{name}': params は dict で指定してください")
    for key, spec in params.items():
        if not isinstance(key, str) or not key.isidentifier():
            raise PluginError(
                f"プラグイン '{name}': パラメータ名は Python 識別子にしてください: {key!r}")
        if not isinstance(spec, dict):
            raise PluginError(
                f"プラグイン '{name}': パラメータ '{key}' のスキーマは dict で指定してください")
        typ = spec.get("type", "number")
        if typ not in _PLUGIN_PARAM_TYPES:
            raise PluginError(
                f"プラグイン '{name}': パラメータ '{key}' の type が不正です: {typ!r}\n"
                f"使用可能な type: {', '.join(_PLUGIN_PARAM_TYPES)}"
                f"{_suggest_hint(typ, _PLUGIN_PARAM_TYPES)}")
        if typ == "choice" and not spec.get("choices"):
            raise PluginError(
                f"プラグイン '{name}': パラメータ '{key}' (type='choice') には "
                f"choices=[...] が必要です")


def _coerce_plugin_param(plugin_name, key, spec, value):
    """スキーマに従って単一パラメータを検証・正規化する"""
    typ = spec.get("type", "number")
    lo = spec.get("min")
    hi = spec.get("max")
    if typ == "number":
        _require_number(plugin_name, key, value, lo, hi)
        return value
    if typ == "int":
        _require_number(plugin_name, key, value, lo, hi)
        if isinstance(value, float) and not float(value).is_integer():
            raise ValueError(
                f"{plugin_name}: {key} は整数で指定してください: {value!r}")
        return int(value)
    if typ == "expr":
        # Expr/lambda/数値を受理 → live アニメーション可能
        try:
            resolved = _resolve_param(value)
        except TypeError as e:
            raise ValueError(
                f"{plugin_name}: {key} には数値・lambda・Expr のいずれかを指定してください: "
                f"{value!r}") from e
        # 定数なら範囲検証（Expr は実行時に決まるため検証しない）
        if isinstance(resolved, Const) and (lo is not None or hi is not None):
            _require_number(plugin_name, key, resolved.value, lo, hi)
        return resolved
    if typ == "color":
        # (r, g, b) タプルに解決（geq 等で使う）
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"{plugin_name}: {key} には色名か16進の文字列を指定してください: {value!r}")
        _parse_color_rgb(value)
        return value
    if typ == "ffcolor":
        return _validate_ffmpeg_color(plugin_name, value)
    if typ == "string":
        if not isinstance(value, str):
            raise ValueError(f"{plugin_name}: {key} は文字列で指定してください: {value!r}")
        return value
    if typ == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{plugin_name}: {key} は True/False で指定してください: {value!r}")
        return value
    if typ == "choice":
        choices = list(spec.get("choices") or [])
        if value not in choices:
            raise ValueError(
                f"{plugin_name}: {key} は {', '.join(map(str, choices))} "
                f"のいずれかを指定してください: {value!r}"
                f"{_suggest_hint(value, choices)}")
        return value
    raise PluginError(f"{plugin_name}: 未対応のパラメータ型: {typ!r}")


def _build_plugin_params(spec, kwargs):
    """ファクトリ引数をスキーマで検証し、Effect に載せる params dict を作る"""
    name = spec.name
    schema = spec.params
    unknown = [k for k in kwargs if k not in schema]
    if unknown:
        k = unknown[0]
        raise ValueError(
            f"{name}: 未知のパラメータです: '{k}'\n"
            f"使用可能: {', '.join(sorted(schema)) or '(なし)'}"
            f"{_suggest_hint(k, schema.keys())}")
    out = {}
    for key, pspec in schema.items():
        if key in kwargs:
            value = kwargs[key]
        elif "default" in pspec:
            value = pspec["default"]
        else:
            raise ValueError(
                f"{name}: 必須パラメータ '{key}' が指定されていません"
                f"（{pspec.get('desc', '')}）")
        out[key] = _coerce_plugin_param(name, key, pspec, value)
    return out


def _make_plugin_factory(spec):
    """スキーマからファクトリ関数（例: neon_glow(radius=5)）を生成する"""

    def factory(**kwargs):
        params = _build_plugin_params(spec, kwargs)
        return Effect(spec.name, **params)

    lines = []
    for key, pspec in spec.params.items():
        rng = ""
        if pspec.get("min") is not None or pspec.get("max") is not None:
            rng = f" [{pspec.get('min', '')}〜{pspec.get('max', '')}]"
        dflt = f" (既定: {pspec['default']!r})" if "default" in pspec else " (必須)"
        lines.append(
            f"  {key}: {pspec.get('type', 'number')}{rng}{dflt} "
            f"— {pspec.get('desc', '')}")
    factory.__name__ = spec.name
    factory.__qualname__ = spec.name
    factory.__doc__ = (
        f"{spec.doc}\n\n[プラグインEffect] カテゴリ: {spec.category} / "
        f"bakeable: {spec.bakeable}\nパラメータ:\n" + "\n".join(lines))
    factory._plugin_spec = spec
    return factory


def effect_plugin(name, *, bakeable=False, category="その他", params=None,
                  override=False):
    """Effectプラグイン登録デコレータ。

    使い方:
        @effect_plugin("neon_glow", bakeable=True, category="視覚効果",
                       params={"radius": {"type": "number", "default": 10,
                                          "min": 0, "max": 200, "desc": "ぼかし半径"}})
        def build_neon_glow(params, ctx):
            '''ネオン風グロー(1行要約。マニフェストに載る)'''
            return [f"gblur=sigma={params['radius']}"]

    - ビルダーは ffmpeg フィルタ文字列のリストを返す（オブジェクトのフィルタチェーンに連結）
    - ファクトリ関数 neon_glow(radius=5) が自動生成され scriptvedit 名前空間に注入される
    - bakeable=True でチェックポイント/compute 経路のベイク対象になる
    - override=True は「プラグイン同士」の再登録のみ許可（組込Effectの上書きは常に禁止）
    """
    params = dict(params or {})

    def decorator(builder):
        if not callable(builder):
            raise PluginError(f"プラグイン '{name}': ビルダーは関数である必要があります")
        if not isinstance(name, str) or not name.isidentifier():
            raise PluginError(
                f"プラグイン名は Python 識別子にしてください: {name!r}")
        _validate_plugin_schema(name, params)

        if name in _reserved_plugin_names():
            raise PluginError(
                f"プラグイン '{name}' は scriptvedit のサブモジュール名と衝突しています"
                f"（予約名・上書きは禁止）。\n"
                f"別の名前を付けてください。")

        g = _pkg_ns()
        _all = _pkg_all()
        existing = _EFFECT_PLUGINS.get(name)
        if existing is None and name in g:
            # 組込ファクトリ/クラスと同名 → 常に禁止
            raise PluginError(
                f"プラグイン '{name}' は組込の名前と衝突しています（上書きは禁止）。\n"
                f"別の名前を付けてください。")
        if existing is not None and not override:
            src = existing.source_file or "(inline)"
            raise PluginError(
                f"Effectプラグイン '{name}' は既に登録されています（定義元: {src}）。\n"
                f"意図的な再登録なら @effect_plugin(..., override=True) を指定してください。")

        source_file, code_ffp = _plugin_code_ffp(builder)
        doc = (builder.__doc__ or "").strip().split("\n")[0] or name
        spec = _EffectPluginSpec(
            name=name, builder=builder, params=params, bakeable=bool(bakeable),
            category=category, doc=doc, source_file=source_file, code_ffp=code_ffp)
        _EFFECT_PLUGINS[name] = spec

        if spec.bakeable:
            _BAKEABLE_EFFECTS.add(name)
        else:
            _BAKEABLE_EFFECTS.discard(name)

        factory = _make_plugin_factory(spec)
        g[name] = factory
        if name not in _all:
            _all.append(name)
        return builder

    return decorator


def unregister_plugin(name):
    """プラグインの登録解除（主にテスト用）。名前空間からもファクトリを取り除く"""
    spec = _EFFECT_PLUGINS.pop(name, None)
    if spec is None:
        return False
    _BAKEABLE_EFFECTS.discard(name)
    g = _pkg_ns()
    _all = _pkg_all()
    if getattr(g.get(name), "_plugin_spec", None) is not None:
        del g[name]
    if name in _all:
        _all.remove(name)
    return True


def _plugin_ctx(obj, e, eff_idx, start, dur, base_dims, label_prefix, pad_state):
    """ビルダーに渡すコンテキストを構築する"""
    proj = current_project()
    base_w, base_h = (base_dims if base_dims else (None, None))

    def expand_pad(dw, dh):
        """padでキャンバスを広げた分を overlay 中央配置基準に反映する。
        pad_size 未確定（scale未使用）なら何もしない。"""
        if pad_state[0]:
            pad_state[0] = (pad_state[0][0] + int(dw), pad_state[0][1] + int(dh))

    def set_pad(w, h):
        """出力サイズを固定した場合に overlay 中央配置基準を上書きする"""
        pad_state[0] = (int(w), int(h))

    return {
        "u": _u_expr(start, dur),        # scale/rotate 等（t基準）
        "u_T": _u_expr(start, dur, "T"),  # geq/blend 等（T基準）
        "start": start,
        "dur": dur,
        "fps": (proj.fps if proj else 30),
        "width": (proj.width if proj else 1920),
        "height": (proj.height if proj else 1080),
        "base_w": base_w,
        "base_h": base_h,
        "label": f"{label_prefix}e{eff_idx}",
        "obj": obj,
        "effect": e,
        "project": proj,
        "escape_path": _escape_ffpath,
        "parse_color": _parse_color_rgb,
        "pad_size": pad_state[0],
        "expand_pad": expand_pad,
        "set_pad": set_pad,
    }


def _build_plugin_effect_filters(obj, e, eff_idx, start, dur, base_dims,
                                 label_prefix, pad_state):
    """プラグインEffectのフィルタ列を生成する（_build_effect_filters から呼ばれる）"""
    spec = _EFFECT_PLUGINS[e.name]
    ctx = _plugin_ctx(obj, e, eff_idx, start, dur, base_dims, label_prefix, pad_state)
    try:
        result = spec.builder(dict(e.params), ctx)
    except Exception as exc:
        raise PluginError(
            f"プラグイン '{e.name}' のビルダーでエラーが発生しました "
            f"(定義元: {spec.source_file or 'inline'}): {exc}") from exc
    if result is None:
        return []
    if isinstance(result, str):
        result = [result]
    if not isinstance(result, (list, tuple)) or any(
            not isinstance(f, str) for f in result):
        raise PluginError(
            f"プラグイン '{e.name}' のビルダーは ffmpeg フィルタ文字列のリストを"
            f"返してください（実際: {type(result).__name__}）")
    return [f for f in result if f]


def _snapshot_registries():
    """プラグイン登録が触る全レジストリの復元用スナップショットを取る"""
    g = _pkg_ns()
    _all = _pkg_all()
    return {
        "plugins": dict(_EFFECT_PLUGINS),
        "bakeable": set(_BAKEABLE_EFFECTS),
        "all": list(_all),
        # 名前空間はプラグイン由来のファクトリだけ差分復元する（全コピーは重い）
        "ns_plugin_names": {k for k, v in g.items()
                            if getattr(v, "_plugin_spec", None) is not None},
    }


def _restore_registries(snap):
    """スナップショットへ完全復元する（部分登録の残留を根絶。監査 issue #16）"""
    _EFFECT_PLUGINS.clear()
    _EFFECT_PLUGINS.update(snap["plugins"])
    _BAKEABLE_EFFECTS.clear()
    _BAKEABLE_EFFECTS.update(snap["bakeable"])
    _all = _pkg_all()
    _all[:] = snap["all"]
    g = _pkg_ns()
    for k in [k for k, v in g.items()
              if getattr(v, "_plugin_spec", None) is not None
              and k not in snap["ns_plugin_names"]]:
        del g[k]


def load_plugin(path):
    """単一のプラグイン .py を読み込む（失敗時は PluginError）

    ファイル内で登録後に例外が起きても、レジストリ・名前空間・__all__ を
    読み込み前の状態へ完全復元する（半端に使える Effect を残さない）。
    """
    import importlib.util as _ilu
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        raise PluginError(f"プラグインファイルが見つかりません: {path}")
    mod_name = "_svplugin_" + hashlib.sha256(
        abs_path.replace("\\", "/").encode()).hexdigest()[:12]
    snap = _snapshot_registries()
    try:
        spec = _ilu.spec_from_file_location(mod_name, abs_path)
        if spec is None or spec.loader is None:
            raise PluginError(f"プラグインを読み込めません: {path}")
        module = _ilu.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
    except PluginError:
        sys.modules.pop(mod_name, None)
        _restore_registries(snap)
        raise
    except Exception as exc:
        sys.modules.pop(mod_name, None)
        _restore_registries(snap)
        raise PluginError(
            f"プラグインの読み込みに失敗しました: {path}\n"
            f"  {type(exc).__name__}: {exc}") from exc
    _LOADED_PLUGIN_FILES.add(abs_path.replace("\\", "/"))
    return module


# 直近の load_plugins で読込失敗があったか（autoload の再試行判定用）
_LOAD_HAD_FAILURE = [False]

# 読み込み済みプラグインファイル（二重読み込み防止）
_LOADED_PLUGIN_FILES = set()
# 自動読込済みディレクトリ
_AUTOLOADED_PLUGIN_DIRS = set()


def load_plugins(directory, quiet=False):
    """ディレクトリ内の *.py を全て読み込む。

    1ファイルの失敗は警告 + そのファイルのみスキップ（他は生かす）。
    戻り値: 読み込めたファイルパスのリスト
    """
    if not os.path.isdir(directory):
        return []
    loaded = []
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        path = os.path.join(directory, fname)
        if os.path.abspath(path).replace("\\", "/") in _LOADED_PLUGIN_FILES:
            continue
        try:
            load_plugin(path)
            loaded.append(path)
        except PluginError as exc:
            _LOAD_HAD_FAILURE[0] = True
            if not quiet:
                warnings.warn(f"プラグインをスキップしました: {exc}")
    return loaded


def _autoload_plugins(base_dir):
    """base_dir/plugins/ があれば自動読み込みする（1ディレクトリ1回）

    失敗したファイルがあったディレクトリは「読込済み」に登録しない
    （プラグインを修正した後の再読込を可能にする。監査 issue #16）。
    成功済みファイルは _LOADED_PLUGIN_FILES で個別スキップされるため、
    再訪しても二重登録にはならない。
    """
    if os.environ.get("SCRIPTVEDIT_NO_PLUGINS"):
        return []
    pdir = os.path.abspath(os.path.join(base_dir, "plugins"))
    key = pdir.replace("\\", "/")
    if key in _AUTOLOADED_PLUGIN_DIRS or not os.path.isdir(pdir):
        return []
    _LOAD_HAD_FAILURE[0] = False
    loaded = load_plugins(pdir)
    if not _LOAD_HAD_FAILURE[0]:
        _AUTOLOADED_PLUGIN_DIRS.add(key)
    return loaded


def plugin_manifest(as_text=False):
    """登録済みプラグインの一覧（AI/人間向けの機能マニフェスト）。

    as_text=True で人が読める文字列、既定は dict のリストを返す。
    """
    items = []
    for name in sorted(_EFFECT_PLUGINS):
        s = _EFFECT_PLUGINS[name]
        items.append({
            "name": name,
            "doc": s.doc,
            "category": s.category,
            "bakeable": s.bakeable,
            "source": s.source_file,
            "params": {
                k: {kk: vv for kk, vv in v.items()} for k, v in s.params.items()
            },
        })
    if not as_text:
        return items
    lines = ["登録済みEffectプラグイン: %d件" % len(items)]
    for it in items:
        lines.append(f"- {it['name']} [{it['category']}] "
                     f"bakeable={it['bakeable']}: {it['doc']}")
        for k, v in it["params"].items():
            dflt = v.get("default", "(必須)")
            lines.append(f"    {k}: {v.get('type', 'number')} 既定={dflt!r} "
                         f"— {v.get('desc', '')}")
    return "\n".join(lines)


# --- 循環 import の回避（同一 SCC のモジュールのみ末尾で束縛。scripts/check_import_cycles.py で計測）---
from scriptvedit.filters.video import _u_expr
from scriptvedit.objects import Effect
from scriptvedit.text import _escape_ffpath
