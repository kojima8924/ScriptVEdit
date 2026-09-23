# -*- coding: utf-8 -*-
"""レイヤーキャッシュ（p.layer(cache=...)）の鮮度判定・再生・生成コマンド構築。

`cache="make"` で1レイヤーを透過中間動画へ焼き、`"use"` / `"auto"` で
それを1つの Object として差し込む。鮮度判定は anchors.json に記録した依存集合
（素材の内容指紋・解決済み param）と**現在の依存集合の完全一致**で行い、
欠損・破損・未知形式はすべて fail-closed（陳腐扱い）にする。

parallel.py / preview.py と同じ方式で、Project インスタンスを第1引数に受ける
自由関数を提供する（Project は import しない＝循環 import を起こさない）。
ffmpeg を実際に起動する段（`_render_layer_to_cache`）だけは project.py に
残してあり、ここは「何を作るか」「使ってよいか」を決めるだけ。

project.py 側は、外部（viz.py・テスト）がインスタンス経由で呼ぶ関数を
クラス本体でそのままメソッドへ束縛する（薄いラッパ関数を挟まないのは、
スタックフレームを増やさず `_warn` の stacklevel を従来どおりに保つため）。
"""

import os
import json
import builtins as _builtins
import concurrent.futures as _futures

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.cache import _file_fingerprint, _layer_cache_encode_args, _layer_cache_paths
from scriptvedit.filters.video import _build_input_args, _build_video_overlay_parts, _visible_window
from scriptvedit.objects import Object
from scriptvedit.plugins import _EFFECT_PLUGINS
from scriptvedit.state import _detect_media_type
from scriptvedit.warn import _warn


def _layer_cache_paths_for(project, spec):
    """spec の品質を反映したレイヤーキャッシュパスを返す（呼び出し側の取り違え防止）"""
    return _layer_cache_paths(spec["filename"], project, spec.get("cache_quality"))


def _validate_cache_specs(project):
    """cache='use' のファイル存在チェック"""
    for spec in project._layer_specs:
        if spec["cache"] == "use":
            webm_path, json_path = project._layer_cache_paths_for(spec)
            if not os.path.exists(webm_path):
                raise FileNotFoundError(
                    f"キャッシュファイルが見つかりません: {webm_path}\n"
                    f"レイヤー '{spec['filename']}' に cache='use' が指定されていますが、"
                    f"先に cache='make' でキャッシュを生成してください。"
                )


def _current_layer_sources_meta(project, filename):
    """レイヤーの「現在の」依存集合（正規化パス → 内容指紋）を構築する。

    キャッシュ書き込み時のメタと同じ規則で作り、鮮度判定はこの現在集合と
    メタの**完全一致**で行う。旧メタに載っている依存だけを再検査する方式だと、
    環境変数等でレイヤーが参照する素材を a→b へ切り替えたとき、a が無変化な
    だけで fresh と誤判定し旧映像を使い続ける（監査 issue #16 P0）。
    指紋を取得できない依存は None（呼び出し側で stale 扱い＝fail-closed）。
    """
    meta = {}
    for src in project._layer_sources.get(filename, []):
        key = str(src).replace("\\", "/")
        if key.startswith("text://"):
            # text系の合成ソースは実体ファイルを持たないが、名前自体が
            # テキスト内容+スタイルのハッシュで一意（_text_synthetic_source）。
            # 名前を指紋として記録する。None のままだと fail-closed により
            # text を含むレイヤーが永遠に fresh にならない。テキスト内容の
            # 変更は名前（キー）の変化として完全一致比較で検出される。
            # フォント実ファイルは別途 _layer_sources に登録済み（issue #16 P2）
            meta[key] = key
            continue
        try:
            meta[key] = _file_fingerprint(src)
        except (OSError, TypeError):
            meta[key] = None
    # 登録済みプラグインのソース（書き込み側と同じ粗い粒度・安全側）
    for plug in _EFFECT_PLUGINS.values():
        src_file = getattr(plug, "source_file", None)
        if not src_file:
            # 定義元ファイルを持たないプラグイン（REPL / exec(<文字列>) /
            # zip 同梱）。以前は continue で依存集合から丸ごと外れ、
            # ビルダーを書き換えてもレイヤーキャッシュが陳腐化しなかった
            # （監査 項目22b）。バイトコード指紋を鍵として載せる。
            meta[f"plugin://{plug.name}"] = getattr(plug, "code_ffp", None)
            continue
        key = str(src_file).replace("\\", "/")
        try:
            meta[key] = _file_fingerprint(src_file)
        except (OSError, TypeError):
            meta[key] = None
    return meta


def _layer_cache_is_fresh(project, spec):
    """anchors.jsonに記録された依存（素材FFP・解決済みparam）と現状を比較して鮮度判定

    メタの欠損・破損・未知形式は fail-closed（=陳腐扱い）。fail-open にすると
    書きかけ・欠損メタの成果物が「常に新鮮」と誤判定され、依存変更を
    取りこぼす（issue #13 P2-7）。後方互換の旧形式救済はしない（方針どおり）。
    比較は「現在の依存集合とメタの完全一致」（キー集合の増減・差し替えも検出。
    監査 issue #16 P0）。
    """
    _, json_path = project._layer_cache_paths_for(spec)
    if not os.path.exists(json_path):
        return False  # 成果物と完了メタの整合を必須化（メタ欠損=不完全）
    try:
        with open(json_path, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False  # 破損メタ
    if not isinstance(meta, dict) or "sources" not in meta:
        return False  # 未知形式・sources無し
    # パース済みメタを保持し、_load_cached_layerでの再読込をスキップする
    project._layer_meta_cache[json_path] = meta
    current = project._current_layer_sources_meta(spec["filename"])
    if any(ffp is None for ffp in current.values()):
        return False  # 現在の依存に指紋不能なものがある（fail-closed）
    if current != (meta["sources"] or {}):
        return False  # 依存の追加・削除・差し替え・内容変化のいずれか
    # 解決済み param の比較（plan passが常にライブ実行するため、
    # 現在値は project._layer_params に揃っている）
    if meta.get("params", None) != project._layer_params.get(
            spec["filename"], {}):
        return False
    return True


def _should_use_cache(project, spec):
    """キャッシュ利用判定"""
    cache = spec["cache"]
    if cache == "use":
        if not _layer_cache_is_fresh(project, spec):
            _warn(project,
                  f"レイヤーキャッシュの素材が更新されています: {spec['filename']}。"
                  f"cache='make' で再生成してください"
                  f"（cache='use' 指定のため続行します）。")
        return True
    if cache == "auto":
        webm_path, _ = project._layer_cache_paths_for(spec)
        # 素材更新済みの古いキャッシュは使わず再実行
        return os.path.exists(webm_path) and _layer_cache_is_fresh(project, spec)
    return False  # off, make


def _load_cached_layer(project, spec):
    """キャッシュからObject生成 + anchors.jsonマージ"""
    webm_path, json_path = project._layer_cache_paths_for(spec)
    start_idx = len(project.objects)
    # キャッシュwebmをObjectとして生成。
    # __init__ を通さない（自動登録・media_type 判定・web 検証を避けるため）ので、
    # **Object.__init__ が設定する属性は1つ残らず同じ初期値で置くこと**。
    # これはタイムラインに載る本物の Object であり、欠けた属性は Project 側が
    # getattr(..., None) をやめて直接参照した瞬間に AttributeError になる。
    cached_obj = Object.__new__(Object)
    cached_obj.source = webm_path
    cached_obj.transforms = []
    cached_obj.effects = []
    cached_obj.audio_effects = []
    cached_obj.duration = None
    cached_obj._duration_auto = False
    cached_obj.start_time = 0
    cached_obj.priority = spec["priority"]
    cached_obj.media_type = "video"
    cached_obj._until_anchor = None
    cached_obj._until_offset = 0.0
    cached_obj._anchor_name = None
    cached_obj._advance = True
    cached_obj._fixed_start = None
    cached_obj._start_after = None
    cached_obj._priority_override = None
    cached_obj._video_deleted = False
    cached_obj._audio_deleted = False
    cached_obj._has_video = True
    cached_obj._has_audio = False
    cached_obj._web_source = None
    cached_obj._web_size = None
    cached_obj._web_fps = None
    cached_obj._web_data = {}
    cached_obj._web_name = None
    cached_obj._web_debug_frames = False
    cached_obj._web_deps = []
    # anchors.jsonからduration/anchorsを読み込み
    # （_layer_cache_is_freshでパース済みならそのメタを流用し二重読みを避ける）
    cache_meta = project._layer_meta_cache.get(json_path)
    if cache_meta is None and os.path.exists(json_path):
        with open(json_path, encoding="utf-8") as f:
            cache_meta = json.load(f)
    if cache_meta is not None:
        cached_obj.duration = cache_meta.get("duration")
        for name, time_val in cache_meta.get("anchors", {}).items():
            project._anchors[name] = time_val
            project._anchor_defined_in[name] = spec["filename"]
    filename = spec["filename"]
    has_runtime_audio_info = filename in project._layer_audio_sources
    audio_sources = project._layer_audio_sources.get(filename, [])
    unknown_audio_sources = project._layer_unknown_audio_sources.get(filename, [])
    if not has_runtime_audio_info and cache_meta is not None:
        audio_sources = cache_meta.get("audio_sources", [])
        unknown_audio_sources = cache_meta.get("unknown_audio_sources", [])
    legacy_audio_sources = []
    if (not has_runtime_audio_info and not audio_sources
            and not unknown_audio_sources and cache_meta is not None
            and "audio_sources" not in cache_meta):
        # issue #8以前のメタには音声情報がない。旧キャッシュをcache='use'で
        # 再生しても無言脱落を見逃さないよう、記録済み素材をprobeして補う。
        using_legacy_audio_info = True
        for source in cache_meta.get("sources", {}):
            media_type = _detect_media_type(source)
            if media_type == "audio":
                legacy_audio_sources.append(source)
                continue
            info = project._probe_media(source)
            if info and info.get("has_audio"):
                legacy_audio_sources.append(source)
            elif info is None and media_type == "video":
                unknown_audio_sources.append(source)
        audio_sources = legacy_audio_sources
    else:
        using_legacy_audio_info = False
    if audio_sources or unknown_audio_sources:
        if using_legacy_audio_info:
            details = list(audio_sources) + list(unknown_audio_sources)
            _warn(project,
                f"旧形式のレイヤーキャッシュを再生するため音声が脱落する"
                f"可能性があります (cache='{spec['cache']}', "
                f"{spec['filename']}): {', '.join(details)}。"
                f"cache='make' で再生成するか、音声素材を cache='off' の"
                f"別レイヤーへ分離してください。")
        elif unknown_audio_sources:
            details = list(audio_sources) + list(unknown_audio_sources)
            _warn(project,
                f"レイヤーキャッシュを再生しますが、ffprobeで音声の有無を"
                f"確認できない動画があるため音声が脱落する可能性があります "
                f"(cache='{spec['cache']}', {spec['filename']}): "
                f"{', '.join(details)}。"
                f"音声素材を cache='off' の別レイヤーへ分離してください。")
        else:
            _warn(project,
                f"レイヤーキャッシュを再生するため音声が脱落します "
                f"(cache='{spec['cache']}', {spec['filename']}): "
                f"{', '.join(audio_sources)}。"
                f"音声素材を cache='off' の別レイヤーへ分離してください。")
    project.objects.append(cached_obj)
    end_idx = len(project.objects)
    project._layers.append((start_idx, end_idx, spec["priority"]))
    project._stamp_layer_origin(start_idx, end_idx, spec["filename"])


def _get_layer_data(project, spec_index):
    """指定レイヤーのオブジェクト群と、そのレイヤーが定義したアンカーを返す。

    アンカーは**正規リゾルバ(_resolve_anchors)が解決済みの project._anchors**
    から「このレイヤーが定義した名前」だけを切り出す。所有レイヤーは
    timeline.py の _register_anchor_owner が _anchor_defined_in に記録済みで、
    明示 anchor() も time(name=) の生成アンカー（X.start / X.end）も
    scene:X / scene:X.end も同じ経路を通る。他レイヤーのアンカーを混ぜると、
    キャッシュ再生時に「そのレイヤーが定義していない時刻」まで復元してしまう。

    以前はここに独自の時刻走査があり、_AnchorMarker / _ScenePad / _advance
    しか解釈しなかった。time(name=) が生む X.start・X.end、`@`
    (_fixed_start)、`>>`(_start_after)、until() を一切扱わないため、
    正規リゾルバが {'A.start': 0, 'A.end': 2} を出す構成でも anchors.json へ
    空の辞書を書いていた。その結果 cache='make' したレイヤーを cache='use'
    で再生すると、別レイヤーの until('A.end') が「未定義のアンカー」で落ちるか
    時刻が静かにずれる。二重実装をやめ、正規リゾルバの結果だけを正とする。

    呼び出し経路は _collect_cache_cmds（dry_run）と _render_layer_to_cache /
    _build_layer_cache_cmd（実レンダ）だけで、どちらも
    _execute_render_pass の _resolve_anchors 完了後に走るため
    project._anchors は確定済み。
    """
    # _layersのインデックスはspec_indexに対応
    if spec_index >= len(project._layers):
        return [], {}
    start_idx, end_idx, _ = project._layers[spec_index]
    objects = project.objects[start_idx:end_idx]
    filename = project._layer_specs[spec_index]["filename"]
    anchors = {name: time_val for name, time_val in project._anchors.items()
               if project._anchor_defined_in.get(name) == filename}
    return objects, anchors


def _collect_cache_cmds(project):
    """dry_run用のキャッシュ生成コマンド辞書構築。

    必ず _collect_checkpoint_cmds の後に呼ぶこと（実レンダの
    _generate_pending_caches と同じオブジェクト状態＝チェックポイント
    適用後を見るため）。順序は _collect_all_extra_cmds が保証する。
    """
    cache_cmds = {}
    for i, spec in enumerate(project._layer_specs):
        # "make" は常に生成（"auto" はキャッシュ有無に関わらず生成コマンドを持たない）
        if spec["cache"] == "make":
            webm_path, _ = project._layer_cache_paths_for(spec)
            cmd = project._build_layer_cache_cmd(i, webm_path)
            cache_cmds[webm_path] = cmd
    return cache_cmds


def _generate_pending_caches(project):
    """レイヤーキャッシュ生成を実行（独立レイヤーは ThreadPoolExecutor で並列）"""
    pending = [i for i, spec in enumerate(project._layer_specs)
               if spec["cache"] == "make"]
    if not pending:
        return
    for i in pending:
        project._note_planned_artifact(
            project._layer_cache_paths_for(project._layer_specs[i])[0])
    workers = _builtins.min(project._parallel_workers(), len(pending))
    if workers <= 1 or len(pending) == 1:
        for i in pending:
            project._render_layer_to_cache(i)
        return
    # 各レイヤーキャッシュは独立（相互に入力参照しない）ため並列化して差し支えない
    print(f"レイヤーキャッシュを並列生成: {len(pending)}件 (workers={workers})")
    errors = []
    with _futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(project._render_layer_to_cache, i): i for i in pending}
        for fut in _futures.as_completed(futs):
            try:
                fut.result()
            except Exception as e:  # 1件失敗しても他の結果は確定させる
                errors.append((futs[fut], e))
    if errors:
        i, e = errors[0]
        raise RuntimeError(
            f"レイヤーキャッシュ生成に失敗しました "
            f"({project._layer_specs[i]['filename']}): {e}") from e


def _build_layer_cache_cmd(project, spec_index, webm_path):
    """レイヤーキャッシュ用ffmpegコマンド（透過を保つ中間ファイル）

    エンコード設定は spec の cache_quality で決まる（_LAYER_CACHE_QUALITY）。
    webm_path: 出力先パス。呼び出し側で計算して渡す
    （_layer_cache_pathsはFFP依存のため、二重計算するとレイヤーファイルの
    mtime変化等で構築時と実行時のパスが食い違うおそれがある）。
    拡張子は品質ごとに異なる（draft/balanced=.webm, lossless=.mkv）。
    """
    spec = project._layer_specs[spec_index]
    objects, anchors = project._get_layer_data(spec_index)
    # 本レンダと同じく priority ソート + 映像を持つオブジェクトのみ合成
    renderable = sorted(
        [o for o in objects if isinstance(o, Object) and o.has_video],
        key=lambda o: o.priority)
    # レイヤーキャッシュは映像のみ保存するため、既知の音声と判定不能動画を警告
    audio_sources = project._layer_audio_sources.get(spec["filename"], [])
    unknown_audio_sources = project._layer_unknown_audio_sources.get(
        spec["filename"], [])
    if audio_sources or unknown_audio_sources:
        details = list(audio_sources) + list(unknown_audio_sources)
        status = ("音声はキャッシュ再生時に脱落します" if not unknown_audio_sources
                  else "音声がキャッシュ再生時に脱落する可能性があります")
        _warn(project,
            f"レイヤーキャッシュ ({spec['filename']}) は映像のみ保存します。"
            f"{status}: {', '.join(details)}\n"
            f"回避策: 音声を持つ素材は cache を付けない別レイヤーに分離してください"
            f"（透過VP9への音声多重化はレイヤー内amix/adelay/duck_underの"
            f"再現が必要で本ウェーブでは見送り）。")

    dur = project.duration or project._calc_total_duration()

    inputs = []
    filter_parts = []

    # 入力0: 透明キャンバス
    inputs.extend([
        "-f", "lavfi",
        "-i", f"color=c=black@0.0:s={project.width}x{project.height}:d={dur}:r={project.fps},format=rgba",
    ])

    current_base = "[0:v]"

    for i, obj in enumerate(renderable):
        input_idx = i + 1
        inputs.extend(_build_input_args(obj, project.fps))
        # 本レンダと同じ解決ロジックでu正規化の分母を統一
        # （レイヤー全体尺fallbackだとcache有無でアニメ速度が変わる）
        obj_dur = project._resolve_obj_duration(obj)
        parts, out_label = _build_video_overlay_parts(
            obj, input_idx, current_base, obj_dur,
            visible_window=_visible_window(obj, project.fps))
        filter_parts.extend(parts)
        current_base = out_label

    cmd = ["ffmpeg", "-y"]
    cmd.extend(inputs)

    if filter_parts:
        cmd.extend(["-filter_complex", ";".join(filter_parts)])
        cmd.extend(["-map", current_base])

    # 品質段階に応じたエンコード引数（draft/balanced=VP9 alpha, lossless=FFV1）
    cmd.extend(_layer_cache_encode_args(spec.get("cache_quality")))
    cmd.extend(["-t", str(dur), webm_path])
    return cmd
