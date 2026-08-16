# -*- coding: utf-8 -*-
"""scriptvedit.viz: Project のタイムライン検査・可視化モジュール

公開API:
- render_timeline(project, out_html, *, title=None)
    Project のタイムラインをレイヤー別ガントチャートの静的HTMLとして書き出す。
    外部依存なしの自己完結HTML（インラインCSS/JS、CDN不使用、ライト/ダーク両対応）。
    戻り値: 書き出したHTMLの絶対パス。
- report_text(project)
    dry_run 補助用のプレーンテキストレポート（オブジェクト一覧 / 区間 /
    エフェクト / 予想キャッシュ）を文字列で返す。

どちらも render 前（レイヤー未実行）の Project ではレイヤー登録情報のみを、
render / dry_run 実行後は全オブジェクト情報を表示する。
Project の属性は getattr でフォールバックする（未実行 Project でも壊れないため）が、
scriptvedit 本体の内部ヘルパーは**実体モジュールから直接 import する**。
getattr で握り潰すと、ヘルパーを改名したときにキャッシュ予測欄が静かに空になる。
"""

import os
import html as _htmllib
import numbers
import unicodedata
import warnings

from scriptvedit.cache import (
    _build_unified_ops, _checkpoint_cache_path, _compute_save_points,
    _is_cache_artifact_path, _layer_cache_paths, _split_ops,
)
from scriptvedit.state import _detect_media_type

__all__ = ["render_timeline", "report_text"]

# 検査は「壊れかけの Project でも表示できる」ことを優先するため、
# データ収集で想定内の失敗（素材が無い・属性が別型・パラメータが特殊）は
# 警告して部分表示に落とす。想定外の例外はそのまま送出して退行を隠さない。
_COLLECT_ERRORS = (OSError, ValueError, TypeError, KeyError, AttributeError)


# --- 書式ヘルパー ---

def _fmt_t(v):
    """秒数を短い文字列に整形（None → '?'）"""
    if v is None:
        return "?"
    if not isinstance(v, numbers.Real):
        return str(v)
    s = f"{float(v):.2f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _fmt_value(v, maxlen=30):
    """Transform/Effect パラメータ値を人間可読に整形"""
    if callable(v):
        s = "<関数>"
    elif hasattr(v, "to_ffmpeg"):
        s = "<Expr>"
    else:
        try:
            s = repr(v)
        except Exception:
            s = object.__repr__(v)
    if len(s) > maxlen:
        s = s[: maxlen - 1] + "…"
    return s


def _op_summary(op):
    """Transform/Effect/AudioEffect を 'name(k=v, ...)' 形式に整形"""
    name = getattr(op, "name", type(op).__name__)
    params = getattr(op, "params", None) or {}
    parts = [f"{k}={_fmt_value(v)}" for k, v in params.items()]
    policy = getattr(op, "policy", None)
    if policy and policy != "auto":
        parts.append(f"policy={policy}")
    quality = getattr(op, "quality", None)
    if quality and quality != "final":
        parts.append(f"quality_hint={quality}")
    return f"{name}({', '.join(parts)})"


_MEDIA_LABELS = {
    "image": "画像", "video": "動画", "audio": "音声",
    "web": "Web", "pause": "待機",
}


# --- データ収集 ---

def _save_point_info(project, obj):
    """Object のチェックポイント保存点と予想キャッシュパスを取得

    戻り値: (save_ops, predictions)
      save_ops:    [(op_index, op_name), ...]
      predictions: [(op_name, cache_path, exists), ...]（計算不可なら空）
    """
    try:
        bakeable, _live = _split_ops(_build_unified_ops(obj))
        if not bakeable:
            return [], []
        # 全op policy="off" は本体側と同様にスキップ
        if all(getattr(op, "policy", "auto") == "off" for _, op in bakeable):
            return [], []
        sps = sorted(_compute_save_points(bakeable))
        save_ops = [(i, getattr(bakeable[i][1], "name", "?")) for i in sps]
    except _COLLECT_ERRORS as e:
        warnings.warn(f"チェックポイント保存点を計算できませんでした"
                      f"（{getattr(obj, 'source', '?')}）: {type(e).__name__}: {e}")
        return [], []
    # 予想キャッシュパス（_process_checkpoints と同じ規則を再現。失敗しても保存点情報は返す）
    predictions = []
    try:
        source = getattr(obj, "source", None)
        dur = getattr(obj, "duration", None)
        fps = getattr(project, "fps", 30)
        is_video = _detect_media_type(source) == "video"
        for i in sps:
            segment = bakeable[: i + 1]
            has_effects = any(t == "effect" for t, _ in segment)
            cp_dur = dur if (has_effects or is_video) else None
            cp_fps = fps if cp_dur is not None else None
            quality = getattr(segment[-1][1], "quality", "final")
            path = _checkpoint_cache_path(source, segment, cp_dur, cp_fps, quality)
            predictions.append(
                (getattr(segment[-1][1], "name", "?"), path, os.path.exists(path)))
    except _COLLECT_ERRORS as e:
        warnings.warn(f"チェックポイントの予想キャッシュパスを計算できませんでした"
                      f"（{getattr(obj, 'source', '?')}）: {type(e).__name__}: {e}")
        predictions = []
    return save_ops, predictions


def _classify_item(item):
    """タイムラインアイテムの種別判定: 'object' / 'pause' / 'anchor'"""
    cls = type(item).__name__
    if cls == "_AnchorMarker":
        return "anchor"
    if getattr(item, "source", None) is not None:
        return "object"
    if getattr(item, "name", None) is not None and not hasattr(item, "transforms"):
        return "anchor"
    return "pause"


def _item_row(item, project):
    """1アイテムを表示用 dict に変換（anchor は None を返し、レイヤー側で収集）"""
    kind = _classify_item(item)
    if kind == "anchor":
        return None
    start = getattr(item, "start_time", 0) or 0
    duration = getattr(item, "duration", None)
    if kind == "pause":
        return {
            "kind": "pause",
            "label": "pause",
            "source": None,
            "media_type": "pause",
            "start": start,
            "duration": duration,
            "priority": getattr(item, "priority", 0),
            "transforms": [], "effects": [], "audio_effects": [],
            "save_ops": [], "cp_predictions": [],
            "from_cache": False,
            "anchor_name": None,
            "until": getattr(item, "_until_anchor", None),
            "advance": True,
            "video_deleted": False, "audio_deleted": False,
            "web_name": None,
        }
    src = getattr(item, "source", "?")
    try:
        from_cache = _is_cache_artifact_path(src)
    except (OSError, TypeError, ValueError):
        from_cache = False  # source が実パスでない合成ソース等
    if not from_cache:
        from_cache = "__cache__" in str(src).replace("\\", "/")
    save_ops, cp_preds = _save_point_info(project, item)
    return {
        "kind": "object",
        "label": os.path.basename(str(src)) or str(src),
        "source": str(src),
        "media_type": getattr(item, "media_type", "image"),
        "start": start,
        "duration": duration,
        "priority": getattr(item, "priority", 0),
        "transforms": [_op_summary(t) for t in (getattr(item, "transforms", None) or [])],
        "effects": [_op_summary(e) for e in (getattr(item, "effects", None) or [])],
        "audio_effects": [_op_summary(a) for a in (getattr(item, "audio_effects", None) or [])],
        "save_ops": save_ops,
        "cp_predictions": cp_preds,
        "from_cache": from_cache,
        "anchor_name": getattr(item, "_anchor_name", None),
        "until": getattr(item, "_until_anchor", None),
        "advance": getattr(item, "_advance", True),
        "video_deleted": getattr(item, "_video_deleted", False),
        "audio_deleted": getattr(item, "_audio_deleted", False),
        "web_name": getattr(item, "_web_name", None),
    }


def _layer_cache_info(project, spec):
    """レイヤーキャッシュの予定パスと存在有無を取得（取得不可なら None）"""
    if spec.get("cache", "off") == "off":
        return None
    try:
        webm_path, json_path = _layer_cache_paths(spec["filename"], project)
    except _COLLECT_ERRORS as e:
        warnings.warn(f"レイヤーキャッシュの予定パスを計算できませんでした"
                      f"（{spec.get('filename', '?')}）: {type(e).__name__}: {e}")
        return None
    return {
        "webm": webm_path, "webm_exists": os.path.exists(webm_path),
        "json": json_path, "json_exists": os.path.exists(json_path),
    }


def _collect(project):
    """Project から表示用データを収集（Project 属性は getattr フォールバック）"""
    meta = {
        "width": getattr(project, "width", None),
        "height": getattr(project, "height", None),
        "fps": getattr(project, "fps", None),
        "duration": getattr(project, "duration", None),
        "background_color": getattr(project, "background_color", None),
    }
    specs = list(getattr(project, "_layer_specs", None) or [])
    layer_ranges = list(getattr(project, "_layers", None) or [])
    objects = list(getattr(project, "objects", None) or [])
    anchors = dict(getattr(project, "_anchors", None) or {})
    anchor_files = dict(getattr(project, "_anchor_defined_in", None) or {})
    # レイヤー実行時に記録された元素材（checkpoint 差し替え前のパス）
    layer_sources = dict(getattr(project, "_layer_sources", None) or {})
    executed = bool(layer_ranges) and bool(objects)

    layers = []
    covered = [False] * len(objects)

    if executed:
        # 実行後: _layers の範囲を spec と突き合わせてオブジェクトを収集
        for i, rng in enumerate(layer_ranges):
            try:
                start_idx, end_idx, prio = rng
            except Exception:
                continue
            spec = specs[i] if i < len(specs) else {}
            fname = spec.get("filename", f"(layer {i})")
            rows, layer_anchors = [], []
            for j in range(start_idx, min(end_idx, len(objects))):
                covered[j] = True
                item = objects[j]
                if _classify_item(item) == "anchor":
                    layer_anchors.append(getattr(item, "name", "?"))
                    continue
                row = _item_row(item, project)
                if row is not None:
                    rows.append(row)
            layers.append({
                "filename": fname,
                "priority": spec.get("priority", prio),
                "cache": spec.get("cache", "off"),
                "executed": True,
                "rows": rows,
                "anchors": layer_anchors,
                "cache_info": _layer_cache_info(project, spec) if spec else None,
                "sources": list(layer_sources.get(fname) or []),
            })
    else:
        # 未実行: layer() 登録情報のみ
        for spec in specs:
            layers.append({
                "filename": spec.get("filename", "?"),
                "priority": spec.get("priority", 0),
                "cache": spec.get("cache", "off"),
                "executed": False,
                "rows": [],
                "anchors": [],
                "cache_info": _layer_cache_info(project, spec),
                "sources": list(layer_sources.get(spec.get("filename")) or []),
            })

    # どのレイヤー範囲にも属さないオブジェクト（直接登録）を擬似レイヤーに収容
    leftover_rows, leftover_anchors = [], []
    for j, item in enumerate(objects):
        if covered[j]:
            continue
        if _classify_item(item) == "anchor":
            leftover_anchors.append(getattr(item, "name", "?"))
            continue
        row = _item_row(item, project)
        if row is not None:
            leftover_rows.append(row)
    if leftover_rows or leftover_anchors:
        layers.append({
            "filename": "(直接登録)",
            "priority": leftover_rows[0]["priority"] if leftover_rows else 0,
            "cache": "off",
            "executed": True,
            "rows": leftover_rows,
            "anchors": leftover_anchors,
            "cache_info": None,
            "sources": [],
        })

    # 全体尺: project.duration → 最大終了時刻 → 5秒
    total = meta["duration"]
    if not (isinstance(total, numbers.Real) and total > 0):
        max_end = 0
        for layer in layers:
            for row in layer["rows"]:
                if isinstance(row["duration"], numbers.Real):
                    max_end = max(max_end, (row["start"] or 0) + row["duration"])
        total = max_end if max_end > 0 else 5.0
    total = float(total)

    return {
        "meta": meta,
        "executed": executed,
        "total": total,
        "layers": layers,
        "anchors": anchors,
        "anchor_files": anchor_files,
    }


# --- プレーンテキストレポート ---

def _disp_width(s):
    """全角=2, 半角=1 の表示幅"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1 for ch in s)


def _pad(s, width):
    return s + " " * max(0, width - _disp_width(s))


def _text_table(headers, rows):
    """East Asian Width を考慮したテキスト表を組む"""
    cols = len(headers)
    widths = [_disp_width(h) for h in headers]
    for row in rows:
        for c in range(cols):
            widths[c] = max(widths[c], _disp_width(row[c]))
    lines = ["  " + " | ".join(_pad(h, widths[c]) for c, h in enumerate(headers))]
    lines.append("  " + "-+-".join("-" * w for w in widths))
    for row in rows:
        lines.append("  " + " | ".join(_pad(row[c], widths[c]) for c in range(cols)))
    return lines


def report_text(project):
    """dry_run 補助用のプレーンテキストレポートを返す

    内容: プロジェクト設定 / レイヤー一覧 / オブジェクト一覧（区間・エフェクト）/
          アンカー / 予想キャッシュ
    """
    data = _collect(project)
    meta = data["meta"]
    out = []
    out.append("=== scriptvedit タイムラインレポート ===")
    out.append("")
    out.append("[プロジェクト設定]")
    out.append(f"  解像度: {meta['width']}x{meta['height']}  fps: {meta['fps']}  "
               f"尺: {_fmt_t(meta['duration'])}s (実効 {_fmt_t(data['total'])}s)  "
               f"背景: {meta['background_color']}")
    if data["executed"]:
        state = "レイヤー実行済"
    elif any(layer["rows"] for layer in data["layers"]):
        state = "レイヤー未実行（直接登録オブジェクトのみ）"
    else:
        state = "レイヤー未実行（layer() 登録情報のみ）"
    out.append(f"  状態: {state}")
    out.append("")

    # レイヤー一覧
    out.append(f"[レイヤー] ({len(data['layers'])}件, priority降順が上に描画)")
    rows = []
    for i, layer in enumerate(data["layers"]):
        n_obj = len(layer["rows"])
        state = f"実行済/{n_obj}obj" if layer["executed"] else "未実行"
        anch = ", ".join(layer["anchors"]) if layer["anchors"] else "-"
        srcs = ", ".join(os.path.basename(str(s)) for s in layer["sources"]) or "-"
        rows.append([str(i), layer["filename"], str(layer["priority"]),
                     layer["cache"], state, anch, srcs])
    out.extend(_text_table(
        ["#", "ファイル", "priority", "cache", "状態", "anchor", "元素材"], rows))
    out.append("")

    # オブジェクト一覧
    all_rows = [(layer, row) for layer in data["layers"] for row in layer["rows"]]
    if all_rows:
        out.append(f"[オブジェクト] ({len(all_rows)}件)")
        rows = []
        for layer, row in all_rows:
            end = (row["start"] + row["duration"]
                   if isinstance(row["duration"], numbers.Real) else None)
            flags = []
            if row["anchor_name"]:
                flags.append(f"name={row['anchor_name']}")
            if row["until"]:
                flags.append(f"until={row['until']}")
            if not row["advance"]:
                flags.append("show")
            if row["video_deleted"]:
                flags.append("映像削除")
            if row["audio_deleted"]:
                flags.append("音声削除")
            if row["from_cache"]:
                flags.append("キャッシュ由来")
            rows.append([
                os.path.basename(layer["filename"]),
                _MEDIA_LABELS.get(row["media_type"], row["media_type"]),
                row["label"],
                _fmt_t(row["start"]),
                _fmt_t(end),
                _fmt_t(row["duration"]),
                ", ".join(row["transforms"]) or "-",
                ", ".join(row["effects"]) or "-",
                ", ".join(row["audio_effects"]) or "-",
                ", ".join(f"#{i}:{n}" for i, n in row["save_ops"]) or "-",
                "; ".join(flags) or "-",
            ])
        out.extend(_text_table(
            ["レイヤー", "種別", "ソース", "開始", "終了", "長さ",
             "Transform", "Effect", "Audio", "保存点", "備考"], rows))
        out.append("")

    # アンカー
    if data["anchors"]:
        out.append(f"[アンカー] ({len(data['anchors'])}件)")
        rows = []
        for name, t in sorted(data["anchors"].items(), key=lambda kv: (kv[1], kv[0])):
            rows.append([name, _fmt_t(t),
                         data["anchor_files"].get(name, "-")])
        out.extend(_text_table(["名前", "時刻", "定義元"], rows))
        out.append("")

    # 予想キャッシュ
    cache_lines = []
    for layer in data["layers"]:
        info = layer["cache_info"]
        if info:
            mark = "[済]" if info["webm_exists"] else "[未]"
            cache_lines.append(
                f"  layer      {mark} {layer['filename']} (cache={layer['cache']})")
            cache_lines.append(f"               -> {info['webm']}")
        for row in layer["rows"]:
            for op_name, path, exists in row["cp_predictions"]:
                mark = "[済]" if exists else "[未]"
                cache_lines.append(
                    f"  checkpoint {mark} {row['label']} @{op_name}")
                cache_lines.append(f"               -> {path}")
            # dry_run 後は source がキャッシュ予定パスに差し替わっている
            if row["from_cache"] and row["source"]:
                mark = "[済]" if os.path.exists(row["source"]) else "[未]"
                cache_lines.append(f"  checkpoint {mark} {row['label']}（source差替済）")
                cache_lines.append(f"               -> {row['source']}")
    if cache_lines:
        out.append("[予想キャッシュ]")
        out.extend(cache_lines)
        out.append("")

    return "\n".join(out)


# --- HTML 生成 ---

_LABEL_W = 280  # 左ラベル列の幅(px)

_CSS_LIGHT = """
  --bg:#f4f5f7; --panel:#ffffff; --text:#1c2330; --muted:#67707e;
  --grid:#dfe3e9; --track:#eceef2; --line:#d0d5dc; --header:#e8ebf0;
  --anchor:#d6453c; --tipbg:#252b36; --tipfg:#f2f4f8;
"""

_CSS_DARK = """
  --bg:#14171d; --panel:#1d222b; --text:#e6e9ef; --muted:#98a1ae;
  --grid:#333a46; --track:#242a35; --line:#39414e; --header:#272e3a;
  --anchor:#ff7a70; --tipbg:#0d1015; --tipfg:#e6e9ef;
"""

_CSS_BODY = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg); color: var(--text);
  font-family: "Segoe UI", "Yu Gothic UI", "Hiragino Sans", Meiryo, sans-serif;
  font-size: 14px; line-height: 1.5; padding: 20px;
}
h1 { font-size: 19px; margin-bottom: 4px; }
.meta { color: var(--muted); font-size: 12px; margin-bottom: 14px; }
.legend { display: flex; flex-wrap: wrap; gap: 12px; font-size: 12px;
          color: var(--muted); margin-bottom: 12px; align-items: center; }
.legend .sw { display: inline-block; width: 12px; height: 12px;
              border-radius: 3px; margin-right: 4px; vertical-align: -2px; }
#themeBtn { margin-left: auto; background: var(--panel); color: var(--text);
            border: 1px solid var(--line); border-radius: 6px;
            padding: 3px 10px; cursor: pointer; font-size: 12px; }
.panel { background: var(--panel); border: 1px solid var(--line);
         border-radius: 10px; padding: 14px; margin-bottom: 16px; }
.scroll { overflow-x: auto; }
.chart { min-width: 760px; position: relative; }
.row { display: flex; align-items: stretch; height: 30px; }
.row .label { flex: 0 0 @@LABEL_W@@px; width: @@LABEL_W@@px; font-size: 12px;
              color: var(--muted); padding: 0 10px 0 4px; display: flex;
              align-items: center; overflow: hidden; white-space: nowrap;
              text-overflow: ellipsis; }
.row .track { flex: 1 1 auto; position: relative;
              border-left: 1px solid var(--grid); }
.body-rows { position: relative; }
.body-rows .row:nth-child(odd) .track { background: var(--track); }
.layer-head { display: flex; height: 26px; align-items: center;
              background: var(--header); border-radius: 5px;
              margin: 8px 0 2px; font-size: 12px; }
.layer-head .name { font-weight: 600; padding: 0 8px; white-space: nowrap; }
.layer-head .info { color: var(--muted); white-space: nowrap; }
.bar { position: absolute; top: 4px; height: 22px; border-radius: 4px;
       color: #fff; font-size: 11px; line-height: 22px; padding: 0 6px;
       overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
       min-width: 8px; cursor: default; box-shadow: 0 1px 2px rgba(0,0,0,.25); }
.bar:hover { filter: brightness(1.12); }
.bar.image { background: #3f9a4d; }
.bar.video { background: #2079c8; }
.bar.audio { background: #d08a26; }
.bar.web   { background: #9b59c9; }
.bar.cachelayer { background: #12907e; }
.bar.pause { background: repeating-linear-gradient(45deg,
             #8a93a0 0 6px, #78818e 6px 12px); color: #fff; }
.bar.unknown { border: 1px dashed rgba(255,255,255,.85); opacity: .75; }
.bar .cp { margin-left: 5px; font-size: 10px; }
.overlay { position: absolute; left: @@LABEL_W@@px; right: 0; top: 0;
           bottom: 0; pointer-events: none; }
.gline { position: absolute; top: 0; bottom: 0; width: 0;
         border-left: 1px dashed var(--grid); }
.aline { position: absolute; top: 0; bottom: 0; width: 0;
         border-left: 2px solid var(--anchor); }
.aline span { position: absolute; top: -2px; left: 3px; font-size: 10px;
              color: var(--anchor); background: var(--panel);
              padding: 0 3px; border-radius: 3px; white-space: nowrap; }
.axis { height: 22px; position: relative; font-size: 11px;
        color: var(--muted); border-bottom: 1px solid var(--grid); }
.axis .tick { position: absolute; bottom: 2px; transform: translateX(-50%);
              white-space: nowrap; }
.notice { color: var(--muted); padding: 8px 4px; }
table.speclist { border-collapse: collapse; font-size: 13px; width: 100%; }
table.speclist th, table.speclist td { border: 1px solid var(--line);
        padding: 5px 10px; text-align: left; }
table.speclist th { background: var(--header); }
#tip { position: fixed; display: none; z-index: 99; max-width: 560px;
       background: var(--tipbg); color: var(--tipfg); font-size: 12px;
       padding: 8px 10px; border-radius: 6px; white-space: pre-line;
       pointer-events: none; box-shadow: 0 3px 14px rgba(0,0,0,.4); }
details { margin-top: 4px; }
details summary { cursor: pointer; color: var(--muted); font-size: 13px; }
details pre { overflow-x: auto; font-size: 12px; padding: 10px;
              background: var(--track); border-radius: 6px; margin-top: 8px;
              font-family: Consolas, "Courier New", monospace; }
"""

_JS = """
(function () {
  // テーマ切替（prefers-color-scheme を初期値に、data-theme で上書き）
  var btn = document.getElementById('themeBtn');
  if (btn) {
    btn.addEventListener('click', function () {
      var root = document.documentElement;
      var cur = root.dataset.theme ||
        (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
      root.dataset.theme = (cur === 'dark') ? 'light' : 'dark';
    });
  }
  // ホバーツールチップ（data-tip 属性を textContent として表示: HTML注入なし）
  var tip = document.getElementById('tip');
  if (!tip) return;
  document.querySelectorAll('[data-tip]').forEach(function (el) {
    el.addEventListener('mousemove', function (e) {
      tip.textContent = el.dataset.tip;
      tip.style.display = 'block';
      var pad = 14, x = e.clientX + pad, y = e.clientY + pad;
      var r = tip.getBoundingClientRect();
      if (x + r.width > window.innerWidth - 8) x = e.clientX - r.width - pad;
      if (y + r.height > window.innerHeight - 8) y = e.clientY - r.height - pad;
      if (x < 4) x = 4;
      if (y < 4) y = 4;
      tip.style.left = x + 'px';
      tip.style.top = y + 'px';
    });
    el.addEventListener('mouseleave', function () {
      tip.style.display = 'none';
    });
  });
})();
"""


def _esc(s):
    return _htmllib.escape(str(s), quote=True)


def _tick_step(total):
    """時間軸の目盛間隔を選ぶ（10目盛前後になるように）"""
    for step in (0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800):
        if total / step <= 12:
            return step
    return total / 10.0


def _bar_tooltip(row):
    """バーのツールチップ本文（プレーンテキスト、改行区切り）"""
    lines = []
    if row["kind"] == "pause":
        lines.append("pause（非描画・時間のみ占有）")
    else:
        lines.append(f"ソース: {row['source']}")
        type_label = _MEDIA_LABELS.get(row["media_type"], row["media_type"])
        if row["from_cache"]:
            type_label += "（キャッシュ由来）"
        lines.append(f"種別: {type_label} / priority={row['priority']}")
    end = (row["start"] + row["duration"]
           if isinstance(row["duration"], numbers.Real) else None)
    lines.append(f"区間: {_fmt_t(row['start'])}s → {_fmt_t(end)}s "
                 f"(長さ {_fmt_t(row['duration'])}s)")
    if row["transforms"]:
        lines.append("Transform: " + ", ".join(row["transforms"]))
    if row["effects"]:
        lines.append("Effect: " + ", ".join(row["effects"]))
    if row["audio_effects"]:
        lines.append("Audio: " + ", ".join(row["audio_effects"]))
    if row["save_ops"]:
        lines.append("チェックポイント保存点: "
                     + ", ".join(f"op#{i} {n}" for i, n in row["save_ops"]))
    for op_name, path, exists in row["cp_predictions"]:
        lines.append(f"  {'[済]' if exists else '[未]'} {op_name} → {path}")
    flags = []
    if row["anchor_name"]:
        flags.append(f"アンカー名: {row['anchor_name']}")
    if row["until"]:
        flags.append(f"until: {row['until']}")
    if not row["advance"]:
        flags.append("show（時刻を進めない）")
    if row["video_deleted"]:
        flags.append("映像削除")
    if row["audio_deleted"]:
        flags.append("音声削除")
    if flags:
        lines.append(" / ".join(flags))
    return "\n".join(lines)


def _bar_html(row, total):
    """1オブジェクト分のバー HTML"""
    start = float(row["start"] or 0)
    left = max(0.0, min(100.0, start / total * 100.0))
    unknown = not isinstance(row["duration"], numbers.Real)
    if unknown:
        width = 3.0
    else:
        width = max(0.4, float(row["duration"]) / total * 100.0)
        width = min(width, 100.0 - left)
    cls = "pause" if row["kind"] == "pause" else row["media_type"]
    if row["kind"] == "object" and row["from_cache"]:
        cls = "cachelayer"
    classes = f"bar {_esc(cls)}" + (" unknown" if unknown else "")
    end = None if unknown else start + float(row["duration"])
    text = f"{_esc(row['label'])} <small>{_fmt_t(start)}–{_fmt_t(end)}s</small>"
    cp = f"<span class='cp'>◆{len(row['save_ops'])}</span>" if row["save_ops"] else ""
    tip = _esc(_bar_tooltip(row))
    return (f"<div class='{classes}' style='left:{left:.4f}%;width:{width:.4f}%'"
            f" data-tip=\"{tip}\">{text}{cp}</div>")


def _layer_label(row):
    """左ラベル列: オブジェクトの表示名"""
    prefix = {"pause": "⏸ ", "audio": "♪ ", "web": "🌐 "}.get(
        row["media_type"], "")
    return f"{prefix}{row['label']}"


def _build_chart_html(data):
    """ガントチャート部分の HTML を構築"""
    total = data["total"]
    parts = []

    # 時間軸
    step = _tick_step(total)
    ticks = []
    t = 0.0
    while t <= total + step * 0.001:
        ticks.append(round(t, 6))
        t += step
    axis_ticks = "".join(
        f"<span class='tick' style='left:{tv / total * 100.0:.4f}%'>{_fmt_t(tv)}s</span>"
        for tv in ticks if tv / total <= 1.0001)
    parts.append("<div class='row'><div class='label'></div>"
                 f"<div class='track axis'>{axis_ticks}</div></div>")

    # レイヤー（priority 降順 = 描画で上に重なるものを上に表示）
    layers = sorted(enumerate(data["layers"]),
                    key=lambda p: (-(p[1]["priority"] or 0), p[0]))
    body = []
    for _, layer in layers:
        info_bits = [f"priority={layer['priority']}", f"cache={layer['cache']}"]
        if not layer["executed"]:
            info_bits.append("未実行")
        if layer["anchors"]:
            info_bits.append("anchor: " + ", ".join(layer["anchors"]))
        ci = layer["cache_info"]
        if ci:
            info_bits.append("layerキャッシュ" + ("[済]" if ci["webm_exists"] else "[未]"))
        if layer["sources"]:
            names = ", ".join(os.path.basename(str(s)) for s in layer["sources"][:4])
            if len(layer["sources"]) > 4:
                names += f" 他{len(layer['sources']) - 4}件"
            info_bits.append("元素材: " + names)
        body.append(
            f"<div class='layer-head'><span class='name'>📄 {_esc(layer['filename'])}</span>"
            f"<span class='info'>（{_esc(' / '.join(info_bits))}）</span></div>")
        if not layer["rows"]:
            body.append("<div class='notice'>（オブジェクトなし）</div>")
        for row in layer["rows"]:
            body.append(
                "<div class='row'>"
                f"<div class='label' title=\"{_esc(row['source'] or row['label'])}\">"
                f"{_esc(_layer_label(row))}</div>"
                f"<div class='track'>{_bar_html(row, total)}</div></div>")

    # グリッド線 + アンカー縦線のオーバーレイ
    overlay = []
    for tv in ticks:
        if 0 < tv / total <= 1.0001:
            overlay.append(
                f"<div class='gline' style='left:{tv / total * 100.0:.4f}%'></div>")
    for name, at in sorted(data["anchors"].items(), key=lambda kv: (kv[1], kv[0])):
        if not isinstance(at, numbers.Real) or not (0 <= at <= total * 1.0001):
            continue
        overlay.append(
            f"<div class='aline' style='left:{min(at / total, 1.0) * 100.0:.4f}%'>"
            f"<span>⚓ {_esc(name)} ({_fmt_t(at)}s)</span></div>")

    parts.append("<div class='body-rows'>"
                 + "".join(body)
                 + f"<div class='overlay'>{''.join(overlay)}</div>"
                 + "</div>")
    return "".join(parts)


def _build_spec_table_html(data):
    """未実行 Project 用のレイヤー登録一覧テーブル"""
    rows = []
    for i, layer in enumerate(data["layers"]):
        ci = layer["cache_info"]
        cache_cell = "-"
        if ci:
            cache_cell = ("[済] " if ci["webm_exists"] else "[未] ") + ci["webm"]
        rows.append(
            f"<tr><td>{i}</td><td>{_esc(layer['filename'])}</td>"
            f"<td>{_esc(layer['priority'])}</td><td>{_esc(layer['cache'])}</td>"
            f"<td>{_esc(cache_cell)}</td></tr>")
    return ("<table class='speclist'><thead><tr>"
            "<th>#</th><th>レイヤーファイル</th><th>priority</th>"
            "<th>cache</th><th>予定キャッシュ</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")


def render_timeline(project, out_html, *, title=None):
    """Project のタイムラインをガントチャート HTML として書き出す

    Args:
        project: scriptvedit.Project（render/dry_run 前後どちらでも可）
        out_html: 出力 HTML パス
        title: ページタイトル（省略時 "scriptvedit タイムライン"）
    Returns:
        書き出した HTML の絶対パス
    """
    data = _collect(project)
    meta = data["meta"]
    page_title = title or "scriptvedit タイムライン"

    n_objects = sum(len(layer["rows"]) for layer in data["layers"])
    meta_line = (f"{meta['width']}×{meta['height']} / {meta['fps']}fps / "
                 f"尺 {_fmt_t(data['total'])}s / 背景 {meta['background_color']} / "
                 f"レイヤー {len(data['layers'])} / オブジェクト {n_objects} / "
                 f"{'実行済' if data['executed'] else 'レイヤー未実行（登録情報のみ）'}")

    legend_items = "".join(
        f"<span><span class='sw' style='background:{color}'></span>{label}</span>"
        for label, color in (
            ("画像", "#3f9a4d"), ("動画", "#2079c8"), ("音声", "#d08a26"),
            ("Web", "#9b59c9"), ("キャッシュ由来", "#12907e"),
            ("pause", "#8a93a0")))
    legend = (f"<div class='legend'>{legend_items}"
              "<span>◆=チェックポイント保存点 / ⚓=アンカー</span>"
              "<button id='themeBtn' type='button'>🌓 テーマ切替</button></div>")

    # バーが1本でもあればチャート表示（layer() 経由でない直接登録オブジェクトも含む）
    has_rows = any(layer["rows"] for layer in data["layers"])
    if has_rows:
        main = ("<div class='panel scroll'><div class='chart'>"
                + _build_chart_html(data) + "</div></div>")
    else:
        main = ("<div class='panel'>"
                "<div class='notice'>レイヤー未実行のため登録情報のみ表示します"
                "（render() / render(dry_run=True) 実行後はタイムラインを表示）。</div>"
                + _build_spec_table_html(data) + "</div>")

    report = _esc(report_text(project))
    css = (":root{" + _CSS_LIGHT + "}\n"
           "@media (prefers-color-scheme: dark){:root{" + _CSS_DARK + "}}\n"
           ":root[data-theme='dark']{" + _CSS_DARK + "}\n"
           ":root[data-theme='light']{" + _CSS_LIGHT + "}\n"
           + _CSS_BODY.replace("@@LABEL_W@@", str(_LABEL_W)))

    html_doc = (
        "<!DOCTYPE html>\n"
        "<html lang='ja'>\n<head>\n<meta charset='utf-8'>\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>\n"
        f"<title>{_esc(page_title)}</title>\n"
        f"<style>\n{css}\n</style>\n</head>\n<body>\n"
        f"<h1>{_esc(page_title)}</h1>\n"
        f"<div class='meta'>{_esc(meta_line)}</div>\n"
        f"{legend}\n{main}\n"
        "<details><summary>テキストレポートを表示</summary>"
        f"<pre>{report}</pre></details>\n"
        "<div id='tip'></div>\n"
        f"<script>\n{_JS}\n</script>\n"
        "</body>\n</html>\n")

    out_html = os.path.abspath(out_html)
    parent = os.path.dirname(out_html)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(out_html, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(html_doc)
    return out_html
