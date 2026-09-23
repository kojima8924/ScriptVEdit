# -*- coding: utf-8 -*-

import subprocess
import os
import json
import math as _math
import builtins as _builtins
import time as _time

# context は scriptvedit 内 import を持たない葉なので、末尾ではなく先頭で import
# できる（循環しない）。「現在の Project」の実体はこちらが持つ。
from scriptvedit.context import (
    activate as _activate,
    in_layer_exec as _in_layer_exec,
    pop_exec as _pop_exec,
    push_exec as _push_exec,
    register_project_class as _register_project_class,
)

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.audio import _probe_audio_length
from scriptvedit.cache import _is_pending_cache_path, _resolve_layer_cache_quality, _web_cache_path
from scriptvedit.expr import Expr, max, min
from scriptvedit.ffmpeg import FFmpegError, _atomic_write_text, _decoder_input_args, _ffmpeg_available_encoders, _normalize_ffmpeg_cmd, _run_ffmpeg, _run_ffmpeg_to_cache, _unique_tmp_path
from scriptvedit.filters.audio import _build_audio_effect_filters, _build_audio_pre_filters
from scriptvedit.filters.video import _DRAFT_SCALE_FILTER, _build_effect_filters, _build_input_args, _build_move_exprs, _build_transform_filters, _build_video_overlay_parts, _get_base_dimensions, _optimize_filter_chain, _unwrap_raw_stream_ref, _visible_window
from scriptvedit.objects import Object, _web_frames_dir
from scriptvedit.assets import resolve_layer_path
from scriptvedit.plugins import _autoload_plugins
from scriptvedit.state import _CONFIGURE_KEYS, _ENCODER_MAP, _GEN_COUNTER, _PRESETS, _detect_media_type, _suggest_hint
from scriptvedit.timeline import (
    Pause, Scene, _AnchorMarker, _ScenePad, _check_until_zero_duration)
from scriptvedit.validate import _require_number, _require_time, _validate_ffmpeg_color
from scriptvedit.warn import _warn
# 分割サブシステム（audit.py と同じ「project を第1引数に受ける自由関数」方式）
from scriptvedit.params import (
    check_unconsumed_params, param as _param_impl)
from scriptvedit.chapters import _chapters_metadata_path, _write_chapters_metadata, export_chapters as _export_chapters_impl, export_metadata as _export_metadata_impl, marker as _marker_impl
from scriptvedit.preview import storyboard as _storyboard_impl, thumbnail as _thumbnail_impl
from scriptvedit.parallel import _parallel_chunk_bounds, _parallel_chunk_count, _render_parallel
from scriptvedit.checkpoint import (
    _build_morph_webm_cmd, _collect_checkpoint_cmds, _morph_frame_count,
    _step_context,
    _apply_checkpoint_final_state as _apply_checkpoint_final_state_impl,
    _ensure_checkpoints as _ensure_checkpoints_impl,
    _plan_object_checkpoints as _plan_object_checkpoints_impl,
    _process_checkpoints as _process_checkpoints_impl)
from scriptvedit.layercache import (
    _collect_cache_cmds, _generate_pending_caches, _validate_cache_specs,
    _build_layer_cache_cmd as _build_layer_cache_cmd_impl,
    _current_layer_sources_meta as _current_layer_sources_meta_impl,
    _get_layer_data as _get_layer_data_impl,
    _layer_cache_paths_for as _layer_cache_paths_for_impl,
    _load_cached_layer as _load_cached_layer_impl,
    _should_use_cache as _should_use_cache_impl)

# _probe_media のメモ化辞書は None（probe失敗）も正規の格納値なので、
# 「未登録」を dict.get の既定値で表すためのセンチネル。
_PROBE_MISS = object()


def _normalize_extra_cmds(value):
    """中間生成物コマンド辞書へ再帰的に共通の ffmpeg 診断フラグを付ける。

    from_project のサブレンダは値が {"main": [...], "cache": {...}} の
    入れ子になるため、コマンドのリストだけを見分けて正規化する
    （dict をそのまま _normalize_ffmpeg_cmd へ渡すとキーのリストに潰れる）。
    """
    if isinstance(value, dict):
        return {k: _normalize_extra_cmds(v) for k, v in value.items()}
    if isinstance(value, list):
        return _normalize_ffmpeg_cmd(value)
    return value



class Project:
    def __init__(self):
        self.width = 1920
        self.height = 1080
        self.fps = 30
        self.duration = None
        self._configured_duration = None
        self.background_color = "black"
        self.objects = []
        self._layers = []  # [(start_idx, end_idx, priority)]
        self._anchors = {}  # anchor name → time
        self._anchor_defined_in = {}  # anchor name → filename（診断用）
        self._layer_specs = []  # [{"filename": str, "priority": int, "cache": str}]
        self._mode = "render"  # "plan" or "render"
        self._current_layer_file = None  # 現在実行中のレイヤーファイル
        self._probe_cache = {}  # (path, size, mtime_ns) → {"duration": float, ...}
        self._layer_sources = {}  # layer filename → [参照ソースパス]（キャッシュ鮮度検証用）
        self._layer_params = {}  # layer filename → {param名: 解決値}（キャッシュ鮮度検証用）
        self._layer_audio_sources = {}  # layer filename → [音声ソース]（再生時の脱落警告用）
        self._layer_unknown_audio_sources = {}  # ffprobe失敗で音声有無が不明な動画
        self._extra_layer_deps = {}  # layer filename → [追加依存パス]（morph_toターゲット等）
        # anchors.jsonパス → パース済みメタ（二重読み防止）。書き込みは
        # _layer_cache_is_fresh のみで、呼び出し元（_should_use_cache →
        # _execute_render_pass）はレンダのメインスレッドに限られるため
        # ロックは不要（並列化されるのは _generate_pending_caches の
        # 「生成」側だけで、そちらはこの辞書を読み書きしない）。
        self._layer_meta_cache = {}
        self._loudnorm_target = None  # normalize_audio() 設定時のLUFS目標
        self._loudnorm_options = None  # TP/LRA/limiter/sample_rate の実用出力設定
        self._markers = []  # [(time, label)] チャプターマーカー
        self._param_overrides = None  # p.param() 用の遅延パース済み上書き値
        self._render_window = None  # 部分レンダの (start, end)
        self.encoder = "libx264"    # 映像エンコーダ（configure(encoder=...)で変更）
        self._encoder_cv = "libx264"  # 解決済み -c:v の値（フォールバック反映後）
        self._encoder_args = list(_ENCODER_MAP["libx264"]["args"])       # []
        self._encoder_draft_args = list(_ENCODER_MAP["libx264"]["draft"])
        self._parallel = None       # キャッシュ並列生成のワーカ数（None=自動）
        self.draft_web_fps = 8      # draft時のWeb screenshot上限（None=本番同等）
        self._draft = False         # ドラフトレンダ中フラグ
        self._render_quality = "final"
        self._storyboard_frame_indices = None  # storyboard一括抽出中のframe番号
        # --- レンダパス中にだけ意味を持つフラグ（_begin_render_pass が上書きする）---
        # 「常に存在する属性」と「レンダ中にしか存在しない属性」の境界を
        # コードから読めるよう、既定値をここで明示する（getattr 防御を減らす）。
        self._dry_run = False           # dry_run（コマンド収集のみ）
        self._alpha = False             # 透過出力（from_project のサブレンダ等）
        self._audio_only_render = False  # 並列レンダの音声専用パス
        self._pending_compute_cmds = {}  # dry_run 中の compute 生成コマンド
        self._plan_structure = None     # Plan pass のレイヤー構造署名
        self._generated = 0             # レンダ開始時の生成カウンタ基準値
        self._render_planned = {}       # 中間生成物パス → レンダ開始前に存在したか
        self._render_warnings = []      # このレンダパスで出た警告（末尾で再掲）
        self._sticky_warnings = []      # レンダパス外（configure等）で出た警告
        # レイヤー実行中に作られた Project は現在の Project を奪わない。
        # 奪うと `sub = Project()` 以降のレイヤー内 Object がサブへ吸われ、
        # 親のタイムラインから無言で消える（監査 項目2）。
        if not _in_layer_exec():
            _activate(self)

    def configure(self, **kwargs):
        unknown = set(kwargs.keys()) - _CONFIGURE_KEYS
        if unknown:
            hint = _suggest_hint(sorted(unknown)[0], _CONFIGURE_KEYS)
            raise ValueError(
                f"不明な設定キー: {', '.join(sorted(unknown))}。"
                f"使用可能: {', '.join(sorted(_CONFIGURE_KEYS))}{hint}"
            )
        if "background_color" in kwargs:
            kwargs["background_color"] = _validate_ffmpeg_color(
                "configure", kwargs["background_color"])
        # width/height: 正の整数（0や負はFFmpegの s=0x720 等で失敗するため構築時に弾く）
        for key in ("width", "height"):
            if key in kwargs:
                v = kwargs[key]
                if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                    raise ValueError(
                        f"configure: {key} は正の整数で指定してください: {v!r}")
        # fps: 正の有限数（NaN/Infinityはフィルタ式やタイムベースを壊す）
        if "fps" in kwargs:
            v = kwargs["fps"]
            if (isinstance(v, bool) or not isinstance(v, (int, float))
                    or not _math.isfinite(v) or v <= 0):
                raise ValueError(
                    f"configure: fps は正の有限数で指定してください: {v!r}")
        if "draft_web_fps" in kwargs and kwargs["draft_web_fps"] is not None:
            v = kwargs["draft_web_fps"]
            if (isinstance(v, bool) or not isinstance(v, (int, float))
                    or not _math.isfinite(v) or v <= 0):
                raise ValueError(
                    "configure: draft_web_fps は正の有限数またはNoneで"
                    f"指定してください: {v!r}")
        # duration: None（自動）または正の有限数
        if "duration" in kwargs and kwargs["duration"] is not None:
            v = kwargs["duration"]
            if (isinstance(v, bool) or not isinstance(v, (int, float))
                    or not _math.isfinite(v) or v <= 0):
                raise ValueError(
                    f"configure: duration は正の有限数で指定してください: {v!r}")
        # preset: width/height/fps をまとめて設定（個別指定で上書き可能なので先に適用）
        if "preset" in kwargs:
            name = kwargs.pop("preset")
            if name not in _PRESETS:
                hint = _suggest_hint(str(name), _PRESETS.keys())
                raise ValueError(
                    f"不明なプリセット: {name}。"
                    f"使用可能: {', '.join(sorted(_PRESETS))}{hint}")
            pw, ph, pfps = _PRESETS[name]
            self.width, self.height, self.fps = pw, ph, pfps
        # encoder: 利用可能性を検出し、不可なら libx264 にフォールバック
        if "encoder" in kwargs:
            self._set_encoder(kwargs.pop("encoder"))
        # parallel: キャッシュ並列生成のワーカ数
        if "parallel" in kwargs:
            pval = kwargs.pop("parallel")
            if pval is not None:
                pval = int(pval)
                if pval < 1:
                    raise ValueError(f"parallel は1以上が必要です: {pval}")
            self._parallel = pval
        for key, value in kwargs.items():
            setattr(self, key, value)
        if "duration" in kwargs:
            self._configured_duration = kwargs["duration"]

    def _set_encoder(self, encoder):
        """エンコーダを設定。ffmpegで利用不可なら libx264 へフォールバック（警告）。"""
        if encoder not in _ENCODER_MAP:
            hint = _suggest_hint(str(encoder), _ENCODER_MAP.keys())
            raise ValueError(
                f"不明なエンコーダ: {encoder}。"
                f"使用可能: {', '.join(sorted(_ENCODER_MAP))}{hint}")
        info = _ENCODER_MAP[encoder]
        cv = info["cv"]
        available = _ffmpeg_available_encoders()
        # available が空（検出失敗）の場合は指定を尊重（検出不能≠利用不可）
        if available and cv not in available and encoder != "libx264":
            _warn(self,
                  f"エンコーダ '{encoder}' ({cv}) はこのffmpegで利用できません。"
                  f"libx264 にフォールバックします。", sticky=True)
            encoder = "libx264"
            info = _ENCODER_MAP["libx264"]
            cv = info["cv"]
        self.encoder = encoder
        self._encoder_cv = cv
        self._encoder_args = list(info["args"])
        self._encoder_draft_args = list(info["draft"])

    def normalize_audio(self, target=-14, *, true_peak=-1.5, lra=11,
                        limiter=True, sample_rate=48000):
        """最終音声を EBU R128 準拠で正規化し、出力ピークを保護する。

        target:      目標ラウドネス (LUFS)。既定 -14。
        true_peak:   最終lossy音声の目標上限 (dBTP)。エンコード時の
                     再上昇に備え、内部処理は0.5dB低く設定する。
        lra:         目標ラウドネスレンジ (LU)。
        limiter:     True なら loudnorm 後に look-ahead limiter を適用。
        sample_rate: 最終音声のサンプルレート。None で自動。

        従来の ``normalize_audio(-14)`` はそのまま使える。
        """
        # 従来契約（-70〜0 LUFS）を維持する。実用値は通常-24〜-9付近だが、
        # 互換性のためAPI側で狭めない。
        _require_number("normalize_audio", "target", target, -70, 0)
        _require_number("normalize_audio", "true_peak", true_peak, -9, 0)
        _require_number("normalize_audio", "lra", lra, 1, 50)
        if not isinstance(limiter, bool):
            raise ValueError(
                f"normalize_audio: limiter は bool で指定してください: {limiter!r}")
        if sample_rate is not None:
            if isinstance(sample_rate, bool) or not isinstance(sample_rate, int):
                raise ValueError(
                    "normalize_audio: sample_rate は整数または None で"
                    f"指定してください: {sample_rate!r}")
            if not 8000 <= sample_rate <= 384000:
                raise ValueError(
                    "normalize_audio: sample_rate は 8000〜384000 の範囲で"
                    f"指定してください: {sample_rate}")
        self._loudnorm_target = target
        self._loudnorm_options = {
            "true_peak": true_peak,
            "lra": lra,
            "limiter": limiter,
            "sample_rate": sample_rate,
        }

    # --- テンプレート変数（実装は params.py。委譲メソッドは manifest 掲載用）---

    def param(self, name, default=None):
        """CLI/環境変数から差し替え可能なテンプレート変数を返す。

        `--param name=値` または環境変数 SCRIPTVEDIT_PARAM_<name> で上書きできる。
        default の型（int/float/bool）に合わせて文字列値を変換する。バッチ生成用。
        """
        return _param_impl(self, name, default)

    # --- チャプターマーカー（実装は chapters.py。委譲メソッドは manifest 掲載用）---

    def marker(self, time, label):
        """タイムライン上のマーカーを記録（mp4チャプター/YouTube目次用）"""
        return _marker_impl(self, time, label)

    def export_chapters(self, path):
        """YouTube用のチャプター目次テキスト（0:00 ラベル形式）を出力する"""
        return _export_chapters_impl(self, path)

    def export_metadata(self, path=None, *, title=None, description=None, tags=None):
        """YouTube投稿用メタデータ（チャプター+タイトル+説明+タグ）を1ファイルに出力する。

        title省略時は self.param("title") があればそれを使う（無ければNone）。
        path省略時は "metadata.json"（カレントディレクトリ）に書き出す。
        拡張子で出力形式を切替: .json ならJSON（構造化データ）、
        .txt ならYouTube概要欄にそのまま貼れるプレーンテキスト
        （タイトル→説明→チャプター目次→#タグ の順）。

        戻り値: 書き出したパス。
        """
        return _export_metadata_impl(
            self, path, title=title, description=description, tags=tags)

    # --- シーン ---

    def scene(self, name, duration):
        """シーンのコンテキストマネージャを返す（with p.scene("intro", 5): ...）。

        with 内で定義したObjectはシーン相対の時刻になり、シーンは時間軸上に
        順次配置される（既存の time anchor / pause 機構を土台に、シーン末尾を
        duration までパディングする）。
        """
        return Scene(self, name, duration)

    # --- デバッグ ---

    def explain(self, obj):
        """objに最終適用されるフィルタチェーンと u 正規化の分母(dur)を表示する。

        「dur がどこ由来か」を明示し、u=(t-start)/dur の分母の出所を一目で
        分かるようにする（デバッグ用）。表示文字列を返す。
        """
        if not isinstance(obj, Object):
            raise TypeError("explain: 対象は Object が必要です")
        start = getattr(obj, "start_time", 0)
        # dur の出所を判定
        if obj.duration:
            dur = obj.duration
            dur_src = "obj.duration（time()で明示指定）"
        elif getattr(obj, "_resolved_length", None):
            dur = obj._resolved_length
            dur_src = "obj._resolved_length（ベイク時に確定）"
        else:
            try:
                dur = self._resolve_obj_duration(obj)
                dur_src = "length()/フォールバック（time()未指定）"
            except Exception:
                dur = None
                dur_src = "未解決"
        lines = []
        lines.append(f"=== explain: {obj.source} ===")
        lines.append(f"  media_type : {obj.media_type}")
        lines.append(f"  start_time : {start}")
        lines.append(f"  duration   : {obj.duration}")
        lines.append(f"  u 正規化分母 dur = {dur}  ← {dur_src}")
        lines.append(f"  u = clip((t-{start})/{dur}, 0, 1)")
        # transform / effect フィルタ
        try:
            tfs = _build_transform_filters(obj)
        except Exception as e:
            tfs = [f"<transform構築エラー: {e}>"]
        lines.append("  Transforms:")
        if obj.transforms:
            for t in obj.transforms:
                lines.append(f"    - {t.name}: {t.params}")
        else:
            lines.append("    (なし)")
        lines.append("  Effects:")
        if obj.effects:
            for e in obj.effects:
                pd = {}
                for k, v in e.params.items():
                    pd[k] = (v.to_ffmpeg("u")[:40] + "…") if isinstance(v, Expr) else v
                lines.append(f"    - {e.name}: {pd}")
        else:
            lines.append("    (なし)")
        lines.append("  映像フィルタチェーン:")
        try:
            base_dims = _get_base_dimensions(obj)
            eff_filters, pad_size = _build_effect_filters(
                obj, start, dur or 5, base_dims=base_dims)
            chain = _optimize_filter_chain(list(tfs) + list(eff_filters))
            for f in chain:
                lines.append(f"    {f}")
            x_expr, y_expr = _build_move_exprs(obj, start, dur or 5, pad_size=pad_size)
            lines.append(f"  overlay位置: x={x_expr}")
            lines.append(f"               y={y_expr}")
        except Exception as e:
            lines.append(f"    <フィルタ構築エラー: {e}>")
        out = "\n".join(lines)
        print(out)
        return out

    def _reset_runtime_state(self):
        """レンダ間で持ち越さない「構造状態」をリセットする。

        タイムライン（objects/layers/anchors）とレイヤー exec で再構築される
        メタ情報が対象。レンダパスごとのフラグ（_dry_run 等）は
        _begin_render_pass の担当で、ここでは触らない。
        """
        self.duration = self._configured_duration
        self.objects = []
        self._layers = []
        self._anchors = {}
        self._anchor_defined_in = {}
        # probe失敗(None)エントリのみ破棄（renderをまたいだ再試行を許す）
        self._probe_cache = {k: v for k, v in self._probe_cache.items()
                             if v is not None}
        self._layer_meta_cache = {}
        self._layer_audio_sources = {}
        self._layer_unknown_audio_sources = {}
        self._layer_params = {}
        self._plan_structure = None

    @staticmethod
    def _describe_timeline_item(item):
        """診断メッセージ用に、タイムラインアイテムを短い呼び出し形で表す。"""
        spec = getattr(item, "_text_spec", None)
        if spec:
            body = str(spec.get("content", ""))
            if len(body) > 20:
                body = body[:20] + "…"
            return f"{spec.get('kind', 'text')}('{body}')"
        kind = type(item).__name__
        if kind == "_AnchorMarker":
            return f"anchor('{getattr(item, 'name', '')}')"
        if kind == "Pause":
            return "pause"
        if kind == "_ScenePad":
            return f"scene('{getattr(item, 'scene_name', '')}')"
        source = getattr(item, "source", None)
        if source is not None:
            return f"Object('{source}')"
        return type(item).__name__

    def _check_layer_defined_objects(self):
        """レイヤーファイルの外で作られたアイテムを検出して拒否する。

        render() は Plan/Render の各パスでレイヤー .py を再実行し、その前に
        objects を空へ戻す（_reset_runtime_state）。したがって main.py 等
        レイヤー exec の外で作った Object は無言で破棄され、ffmpeg は正常
        終了して「完了」とだけ表示される。CLAUDE.md §1 の中核契約
        （レイヤー内 Object の自動登録）が静かに破れる唯一の経路なので、
        レンダ開始前に明示エラーへ変える。

        前回レンダの残骸は _stamp_layer_origin 済み（_defined_in_layer が
        非 None）なので、2回目以降の render() は誤検出しない。
        """
        stray = [o for o in self.objects
                 if getattr(o, "_defined_in_layer", None) is None]
        if not stray:
            return
        shown = ", ".join(self._describe_timeline_item(o) for o in stray[:5])
        if len(stray) > 5:
            shown += f" ほか{len(stray) - 5}件"
        raise ValueError(
            f"Object がレイヤーファイルの外（main.py 等）で作られています: "
            f"{shown}。\n"
            f"render() はレイヤー .py を再実行する前にタイムラインを空へ戻すため、"
            f"これらは破棄され動画に出ません。\n"
            f"p.layer('xxx.py') に登録した .py の中へ移してください。")

    def _note_planned_artifact(self, path):
        """このレンダが必要とする中間生成物を統計へ記録する（生成する前に呼ぶ）。

        値は「このレンダを始める前から存在したか」＝キャッシュヒット。
        複数のObjectが同じ生成物を共有する場合は最初の観測（＝生成前の状態）を
        残すため、二重計上も生成後の誤ヒットも起きない。
        """
        if path not in self._render_planned:
            self._render_planned[path] = os.path.exists(path)

    def _stamp_layer_origin(self, start_idx, end_idx, filename):
        """レイヤー由来のアイテムに生成元レイヤーファイル名を刻む。

        構築側（Object/text()/pause/anchor/scene）ではなくレイヤー実行側で
        一括して刻むことで、登録経路が増えても刻み漏れが起きない。
        """
        for item in self.objects[start_idx:end_idx]:
            item._defined_in_layer = filename

    def _begin_render_pass(self, *, dry_run=False, draft=False, alpha=False):
        """render/thumbnail 共通のレンダ状態初期化（preview.py と共有）。

        1回のレンダパスに固有のフラグ（dry_run/draft/alpha/部分レンダ窓・
        保留 compute コマンド）を既定へ設定し、構造状態は
        _reset_runtime_state に委ねる。
        中間生成物の内容は draft でも本番でも同一なのでキャッシュ鍵は共有する
        （cache.py の方針と同じ。draft で鍵を分離すると同じ絵を二度焼く）。
        """
        # objects を捨てる前に、レイヤー外で作られたアイテムを検出する
        self._check_layer_defined_objects()
        self._reset_runtime_state()
        self._dry_run = dry_run
        self._draft = bool(draft)
        self._alpha = bool(alpha)
        self._render_quality = "draft" if self._draft else "final"
        self._pending_compute_cmds = {}
        self._render_window = None
        self._render_planned = {}
        self._render_warnings = []

    def _resolve_plan_duration(self):
        """Plan pass（アンカー解決。cache模擬、objects破棄）→総尺確定。

        総尺はplan pass（常にライブ実行）の結果から確定する。
        レイヤーキャッシュ鍵が総尺を含むため、キャッシュ判定・検証より
        前に確定していなければならない（issue #13 P1-4）。
        """
        self._plan_resolve()
        if self.duration is None:
            self.duration = self._calc_total_duration()

    def _execute_render_pass(self):
        """Render pass: 本実行（anchors確定済み）→構造一致検証。

        cache="use" の事前検証（鍵に総尺を含むため総尺確定後に行う）→
        レイヤー実行（キャッシュ再生 or exec）→アンカー解決→
        Plan/Render の構造一致検証（非決定的レイヤーの黙った尺ずれ防止）。
        preview.py の thumbnail/storyboard 準備と共有する。

        戻り値: キャッシュ再生に使ったレイヤーファイル名の集合。
        """
        _validate_cache_specs(self)
        self.objects = []
        self._layers = []
        self._mode = "render"
        used_cache_files = set()
        for spec in self._layer_specs:
            if self._should_use_cache(spec):
                used_cache_files.add(spec["filename"])
                self._load_cached_layer(spec)
            else:
                self._exec_layer(spec["filename"], spec["priority"])
        self._resolve_anchors()
        self._verify_plan_structure(used_cache_files)
        # 全レイヤーの実行が終わった時点で、CLI/環境変数から渡されたのに
        # どのレイヤーからも参照されなかった param を報告する（綴り間違いの検出）
        check_unconsumed_params(self)
        return used_cache_files

    def _probe_media(self, path):
        """ffprobeでメディア情報を取得（キャッシュあり）

        **_probe_cache をロックで守らない理由**（_WARN_LOCK 等と違う扱いの記録）:
        この dict はレイヤーキャッシュの並列生成（_generate_pending_caches の
        ThreadPoolExecutor → _build_layer_cache_cmd → _resolve_obj_duration →
        obj.length()）からワーカスレッド経由でも書かれるが、
        (a) 操作は dict の単一の get / 代入だけで CPython の GIL 下では原子的、
        (b) キーが (path, size, mtime_ns) なので**同じ鍵なら素材の実体も同じ**＝
            競合して二重に probe しても書き込まれる値は一致する（メモ化であって
            共有状態の更新ではない）、
        (c) dict を作り直す _reset_runtime_state はレンダ開始時だけで、
            ワーカは ThreadPoolExecutor の with を抜ける時点で必ず合流済み、
        の3点からロックは不要。get は既定値をセンチネルにして
        「in 判定 → 取り出し」の2操作に割らない（None も正規の格納値のため）。
        """
        # メモ化キーに stat 署名（サイズ+mtime）を含める。パスのみをキーにすると
        # 同一パスへ素材を差し替えたとき旧情報を返し続ける（issue #13 P2-9）。
        # プロセス内メモ化のみでディスクへは永続化しない（CLAUDE.md の
        # ffp.json 撤廃と同じ理由で、stat ベースの永続キャッシュは禁止）。
        try:
            st = os.stat(path)
            cache_key = (path, st.st_size, st.st_mtime_ns)
        except OSError:
            cache_key = (path, None, None)
        cached = self._probe_cache.get(cache_key, _PROBE_MISS)
        if cached is not _PROBE_MISS:
            return cached
        if _is_pending_cache_path(path):
            # dry_run中の未生成キャッシュ予定パス。probeせず警告なしでNoneを返す
            # （キャッシュはしない: 実レンダで生成された後は通常probeに進む）
            return None
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_streams", "-show_format", path],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                # ffprobe失敗（ファイル欠損等）。空JSONを成功扱いしない
                raise ValueError(f"ffprobe exit code {result.returncode}")
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            has_video = any(s.get("codec_type") == "video" for s in streams)
            has_audio = any(s.get("codec_type") == "audio" for s in streams)
            duration_str = data.get("format", {}).get("duration")
            duration = float(duration_str) if duration_str else None
            # 音声ストリームのサンプルレート（aloop の size 算出に使用）
            sample_rate = None
            for s in streams:
                if s.get("codec_type") == "audio" and s.get("sample_rate"):
                    try:
                        sample_rate = int(s["sample_rate"])
                    except (ValueError, TypeError):
                        sample_rate = None
                    break
            # ストリーム個別の尺（映像/音声でコンテナ尺と食い違う素材向け）。
            # video_sequence 等が A/V ドリフトを避けるために使う。
            def _stream_dur(s):
                sd = s.get("duration")
                try:
                    return float(sd) if sd else None
                except (ValueError, TypeError):
                    return None
            video_duration = None
            audio_duration = None
            for s in streams:
                ct = s.get("codec_type")
                if ct == "video" and video_duration is None:
                    video_duration = _stream_dur(s)
                elif ct == "audio" and audio_duration is None:
                    audio_duration = _stream_dur(s)
            info = {"has_video": has_video, "has_audio": has_audio,
                    "duration": duration,
                    "sample_rate": sample_rate,
                    "video_duration": video_duration,
                    "audio_duration": audio_duration}
            self._probe_cache[cache_key] = info
            return info
        except FileNotFoundError:
            # 失敗もrender内ではキャッシュ（_reset_runtime_stateでNoneのみ破棄され、
            # renderをまたげば再試行される）
            _warn(self,
                  f"ffprobeが見つかりません ({path})。PATHを確認してください。")
            self._probe_cache[cache_key] = None
            return None
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError,
                json.JSONDecodeError, ValueError) as e:
            _warn(self, f"メディア情報の取得に失敗 ({path}): {e}")
            self._probe_cache[cache_key] = None
            return None

    def layer(self, filename, priority=0, cache="off", cache_quality=None):
        """レイヤーファイルを登録（実行はrender時に遅延）

        filename は cwd 相対でも、呼び出し元スクリプト/実行中レイヤーからの
        相対でも解決される（どのディレクトリから実行しても動く）。

        cache_quality: レイヤーキャッシュ中間ファイルの品質。cache != "off" のときのみ意味を持つ。
            "draft"    … プレビュー用。VP9 yuva420p crf30（最小・最速）
            "balanced" … 既定。VP9 yuva420p crf15（量子化劣化をほぼ除去）
            "lossless" … FFV1 bgra（完全可逆。クロマ間引きも色変換も無い）
            None を渡すと既定（"balanced"）。品質はキャッシュ鍵に含まれるため、
            変更すると別の中間ファイルとして再生成される。
        """
        if cache not in ("off", "auto", "use", "make"):
            raise ValueError(f"cache引数は 'off','auto','use','make' のいずれか: {cache!r}")
        cache_quality = _resolve_layer_cache_quality(cache_quality)
        filename = resolve_layer_path(filename, self)
        self._layer_specs.append({"filename": filename, "priority": priority,
                                  "cache": cache, "cache_quality": cache_quality})

    def _exec_layer(self, filename, priority):
        """レイヤーファイルを実行してobjectsに登録"""
        start_idx = len(self.objects)
        # 例外時も current layer context を必ず元値へ戻す（残留すると
        # 例外回復後の asset()/相対パス解決が失敗レイヤー基準で汚染される。
        # 監査 issue #14 P2）
        prev_layer_file = self._current_layer_file
        self._current_layer_file = filename
        try:
            self._exec_layer_body(filename, priority, start_idx)
        finally:
            self._current_layer_file = prev_layer_file

    def _exec_layer_body(self, filename, priority, start_idx):
        """_exec_layer の本体（current layer context 設定済みで呼ばれる）"""
        # exec中にmorph_to等が積む追加依存をリセット（plan/renderの再実行で重複させない）
        self._extra_layer_deps[filename] = []
        _activate(self)
        _push_exec(self)
        try:
            # レイヤーファイルと同階層の plugins/ を自動読込（cwd と異なる場合の保険）
            _autoload_plugins(os.path.dirname(os.path.abspath(filename)))
            with open(filename, encoding="utf-8") as f:
                code = f.read()
            namespace = {}
            exec(compile(code, filename, "exec"), namespace)
        finally:
            _pop_exec()
            # レイヤー内で sub = Project() された場合に現在の Project を奪還する
            _activate(self)
        end_idx = len(self.objects)
        self._layers.append((start_idx, end_idx, priority))
        self._stamp_layer_origin(start_idx, end_idx, filename)
        for obj in self.objects[start_idx:end_idx]:
            override = getattr(obj, '_priority_override', None)
            obj.priority = override if override is not None else priority
        self._fill_auto_durations(start_idx, end_idx)
        # レイヤーが参照する素材ソースを記録（checkpoint等で差し替わる前の値）
        sources = []
        for o in self.objects[start_idx:end_idx]:
            if not isinstance(o, Object):
                continue
            # compute()済みは導出キャッシュパスではなく元素材を記録
            sources.extend(getattr(o, '_origin_sources', None) or [o.source])
            # web Objectの依存素材（deps=）も鮮度検証の対象にする
            if getattr(o, '_web_deps', None):
                sources.extend(o._web_deps)
            # text系Objectの解決済みフォント実ファイルも依存に含める
            # （sourceは実体のない text:// のため、TTF差し替えが
            # 鮮度検証から漏れて旧字形のキャッシュがfresh扱いになる。issue #16 P2）
            tspec = getattr(o, '_text_spec', None)
            if tspec:
                font_path = tspec.get("font")
                if font_path and os.path.isfile(font_path):
                    sources.append(font_path)
        # morph_toターゲット等、objectsから除外された依存を併合
        sources.extend(self._extra_layer_deps.get(filename, []))
        self._layer_sources[filename] = sources
        cache_mode = next(
            (spec["cache"] for spec in self._layer_specs
             if spec["filename"] == filename), "off")
        audio_sources = []
        unknown_audio_sources = []
        if cache_mode != "off":
            for o in self.objects[start_idx:end_idx]:
                if not isinstance(o, Object) or o._audio_deleted:
                    continue
                source_text = str(os.fspath(o.source)).replace("\\", "/")
                has_audio = o._has_audio
                if has_audio is None:
                    info = self._probe_media(o.source)
                    if info is None:
                        if o.media_type == "video":
                            unknown_audio_sources.append(source_text)
                        continue
                    has_audio = bool(info.get("has_audio"))
                if has_audio:
                    audio_sources.append(source_text)
        self._layer_audio_sources[filename] = audio_sources
        self._layer_unknown_audio_sources[filename] = unknown_audio_sources

    def _fill_auto_durations(self, start_idx, end_idx):
        """duration_auto=Trueのオブジェクトにlength()でdurationを確定"""
        for obj in self.objects[start_idx:end_idx]:
            if (isinstance(obj, Object)
                    and obj._duration_auto
                    and obj.duration is None
                    and obj._until_anchor is None):
                obj.duration = obj.length()

    def _calc_total_duration(self):
        """各レイヤーの最大終了時刻を返す（show含む）"""
        max_dur = 0
        for start_idx, end_idx, _ in self._layers:
            for item in self.objects[start_idx:end_idx]:
                if isinstance(item, _AnchorMarker):
                    continue
                # _ScenePad は resolve 後に start_time/duration を持つため通常計上
                if item.duration is not None:
                    end = item.start_time + item.duration
                    max_dur = max(max_dur, end)
        return max_dur if max_dur > 0 else 5

    def _layer_structure_signature(self):
        """レイヤーごとのタイムライン構造署名を返す（Plan/Render差の検出用）。

        レイヤー .py は Plan pass で1回・Render pass でさらに1回の計2回
        実行される（Plan の外側ループは _plan_resolve で廃止済み。同メソッドの
        docstring 参照）。
        外部カウンタ・乱数・現在時刻などで実行ごとに構造が変わると、Plan で
        確定した総尺と Render の実構造がずれ、末尾が黙って切り詰められる
        （監査 issue #14 P0）。開始時刻は構造から決定されるため含めず、
        「何が・どの順で・どの尺で並ぶか」を層別に写し取る。
        """
        sig = {}
        for (start_idx, end_idx, _), spec in zip(self._layers, self._layer_specs):
            items = []
            for item in self.objects[start_idx:end_idx]:
                items.append((
                    type(item).__name__,
                    str(getattr(item, "source", getattr(item, "name", ""))),
                    getattr(item, "duration", None),
                    getattr(item, "_advance", True),
                    getattr(item, "_until_anchor", None),
                    getattr(item, "_anchor_name", None),
                ))
            sig[spec["filename"]] = items
        return sig

    def _verify_plan_structure(self, used_cache_files):
        """Render passの構造がPlanと一致するか検証する（不一致は明示エラー）。

        キャッシュ再生されたレイヤーは構造が変わって当然なので比較しない。
        """
        plan = self._plan_structure
        if plan is None:
            return
        current = self._layer_structure_signature()
        for filename, plan_items in plan.items():
            if filename in used_cache_files or filename not in current:
                continue
            cur_items = current[filename]
            if cur_items == plan_items:
                continue
            details = []
            if len(cur_items) != len(plan_items):
                details.append(
                    f"  アイテム数: Plan={len(plan_items)} → Render={len(cur_items)}")
            for i, (pi, ci) in enumerate(zip(plan_items, cur_items)):
                if pi != ci:
                    details.append(f"  [{i}] Plan={pi}")
                    details.append(f"  [{i}] Render={ci}")
                    break
            raise RuntimeError(
                f"レイヤー '{filename}' の構造が Plan と Render で一致しません。\n"
                + "\n".join(details) + "\n"
                "レイヤー .py はアンカー解決のため Plan/Render の計2回実行されます。"
                "外部カウンタ・乱数・現在時刻・環境の変化など、実行のたびに"
                "結果が変わる記述を避け、決定的（何度実行しても同じ構造）に"
                "してください。乱数が必要な場合は固定シードを使ってください。")

    def _detect_start_after_cycles(self):
        """`>>` (_start_after) の依存に循環がないか検査する。

        循環があると固定点反復は収束せず、反復回数依存の開始時刻で
        受理されてしまう(監査 issue #14)。各ノードから先行チェーンを
        辿り、経路内で再訪したら関係Objectを列挙してエラーにする。
        """
        done = set()
        for item in self.objects:
            node, path, path_ids = item, [], set()
            while node is not None and id(node) not in done:
                if id(node) in path_ids:
                    start = next(i for i, n in enumerate(path) if n is node)
                    names = " >> ".join(
                        str(getattr(n, "source", type(n).__name__))
                        for n in reversed(path[start:]))
                    raise RuntimeError(
                        f">> の依存が循環しています: {names} >> …\n"
                        f"連結の向きを見直してください（a >> b は"
                        f"「b を a の直後に開始」の意味です）。")
                path.append(node)
                path_ids.add(id(node))
                node = getattr(node, "_start_after", None)
            done.update(path_ids)

    def _resolve_anchors(self, check_unresolved=True):
        """反復走査でアンカーとuntilを解決"""
        self._detect_start_after_cycles()
        # 反復上限は依存の連鎖長に比例する。`>>` の逆順連結や until の
        # 多段参照は「1反復で1段」しか伝播しないため、レイヤー数基準では
        # 長い連鎖で収束前に打ち切られ、誤った開始時刻のまま受理されていた
        # (監査 issue #14)。アイテム総数まで引き上げ、非収束は末尾で検出する
        max_iter = len(self.objects) + len(self._layers) + 2
        changed = False
        for iteration in range(max_iter):
            changed = False
            for start_idx, end_idx, _ in self._layers:
                current_time = 0
                for item in self.objects[start_idx:end_idx]:
                    if isinstance(item, _AnchorMarker):
                        old_val = self._anchors.get(item.name)
                        self._anchors[item.name] = current_time
                        if old_val != current_time:
                            changed = True
                        continue
                    if isinstance(item, _ScenePad):
                        # シーン開始+目標尺まで current_time を進める（遅延パディング）。
                        # pad量を duration として保持し、末尾シーンのパディングも
                        # 総尺(_calc_total_duration)に反映されるようにする。
                        scene_start = self._anchors.get(
                            f"scene:{item.scene_name}", 0)
                        target_time = scene_start + item.target_duration
                        item.start_time = current_time
                        pad_amt = float(max(0.0, target_time - current_time))
                        if item.duration != pad_amt:
                            item.duration = pad_amt
                            changed = True
                        current_time += pad_amt
                        continue
                    # DSL糖衣の浮動配置（@ 絶対配置 / >> 直後連結）。
                    # どちらも _advance=False で順次カーソルは進めない。
                    # 依存先が未確定の反復では前回値を据え置き、確定時に
                    # changed で再収束させる（anchor と同じ固定点反復に乗せる）
                    after = getattr(item, '_start_after', None)
                    fixed = getattr(item, '_fixed_start', None)
                    relative_owner = getattr(item, '_timeline_owner', None)
                    new_start = current_time
                    if relative_owner is not None:
                        new_start = (
                            relative_owner.start_time
                            + float(getattr(item, '_timeline_offset', 0.0)))
                    elif after is not None:
                        if after.duration is not None:
                            new_start = after.start_time + after.duration
                        else:
                            new_start = item.start_time  # 未確定: 据え置き
                    elif fixed is not None:
                        if isinstance(fixed, str):
                            at = self._anchors.get(fixed)
                            new_start = (item.start_time if at is None
                                         else at)  # 未定義アンカーは据え置き
                        else:
                            new_start = float(fixed)
                    if item.start_time != new_start:
                        item.start_time = new_start
                        changed = True
                    # name anchor: X.start 登録（浮動配置でも実開始時刻を張る）
                    anchor_name = getattr(item, '_anchor_name', None)
                    if anchor_name:
                        start_key = f"{anchor_name}.start"
                        old_val = self._anchors.get(start_key)
                        self._anchors[start_key] = item.start_time
                        if old_val != item.start_time:
                            changed = True
                    # until解決（offset対応。基準は実開始時刻＝浮動配置と整合）
                    until_name = getattr(item, '_until_anchor', None)
                    if until_name:
                        anchor_time = self._anchors.get(until_name)
                        if anchor_time is not None:
                            offset = getattr(item, '_until_offset', 0.0)
                            target_time = anchor_time + offset
                            new_dur = max(0, target_time - item.start_time)
                            if item.duration != new_dur:
                                item.duration = new_dur
                                changed = True
                    # 時刻進行（advance=False なら進めない）
                    advance = getattr(item, '_advance', True)
                    if item.duration is not None:
                        if advance:
                            current_time += item.duration
                        # name anchor: X.end 登録
                        if anchor_name:
                            end_key = f"{anchor_name}.end"
                            end_time = item.start_time + item.duration
                            old_val = self._anchors.get(end_key)
                            self._anchors[end_key] = end_time
                            if old_val != end_time:
                                changed = True
            if not changed:
                break
        if changed:
            # 上限まで反復しても値が動き続けた＝依存が収束していない。
            # 誤った時刻のまま受理せず明示エラーにする（循環は事前検出済みの
            # ため、ここに来るのは until とアンカーの相互依存等の異常系）
            raise RuntimeError(
                "タイムライン解決が収束しませんでした。"
                "until・アンカー・>> の相互依存に矛盾がないか確認してください。")
        if check_unresolved:
            for item in self.objects:
                until_name = getattr(item, '_until_anchor', None)
                if until_name and until_name not in self._anchors:
                    raise RuntimeError(f"未定義のアンカー: '{until_name}'")
                after = getattr(item, '_start_after', None)
                if after is not None and after.duration is None:
                    src = getattr(after, 'source', type(after).__name__)
                    raise RuntimeError(
                        f">> の先行アイテム（{src}）の尺が確定していません。\n"
                        f"time(seconds)・スライス（obj[a:b]）・until() の"
                        f"いずれかで尺を確定させてください。")
                fixed = getattr(item, '_fixed_start', None)
                if isinstance(fixed, str) and fixed not in self._anchors:
                    defined = ", ".join(f"'{n}'" for n in sorted(self._anchors)) \
                        or "(なし)"
                    raise RuntimeError(
                        f"@ に指定されたアンカーが未定義です: '{fixed}'\n"
                        f"定義済みアンカー: {defined}")
            # until() が尺0に解決されたものを検出する。**収束後にだけ**呼ぶこと
            # （固定点反復の途中は尺が一時的に0になりうるため、ループ内だと誤検出する）
            _check_until_zero_duration(self.objects, self._anchors)

    def render(self, output_path, *, dry_run=False, timeout=None,
               start=None, end=None, draft=False, alpha=False, strict=False,
               parallel=None):
        # 以前はここに _ACTIVE_QUALITY の退避/復元だけを行う薄いラッパがあったが、
        # そのグローバルは書き込み専用（読み出しが存在しない＝効かないつまみ）
        # だったため撤去し、実装をこのメソッドへ統合した（監査 項目15a）。
        # 時間分割並列レンダのチャンク数。configure(parallel=N)は「キャッシュ
        # 並列生成のワーカ数」で別物（キャッシュ側の意味は従来どおり維持）
        if parallel is not None and (
                isinstance(parallel, bool) or not isinstance(parallel, int)
                or parallel < 1):
            raise ValueError(
                f"render: parallel は1以上の整数で指定してください: {parallel!r}")
        output_path = os.fsdecode(output_path)
        self._begin_render_pass(dry_run=dry_run, draft=draft, alpha=alpha)
        # 奇数解像度の事前拒否: yuv420p系（h264/webm）はクロマサブサンプリングの
        # 制約で偶数寸法が必須。受理するとチェックポイント等の重い処理の後に
        # libx264 が "width not divisible by 2" で失敗するため、レンダ開始前に
        # 日本語で拒否する（PNG連番/GIF/webp/サムネイルは奇数のまま出力できる）
        _fmt_kind = self._resolve_output_format(output_path)["kind"]
        if _fmt_kind in ("h264", "webm") and (
                int(self.width) % 2 or int(self.height) % 2):
            raise ValueError(
                f"render: この出力形式（{_fmt_kind}, yuv420p系）は偶数解像度が"
                f"必要です（現在 {self.width}x{self.height}）。"
                f"configure() で幅・高さを偶数にするか、"
                f"pngシーケンス（.png）や .gif など奇数解像度を扱える形式で"
                f"出力してください。")
        # 生成数はこのレンダ開始時点を基準にした増分で数える（0リセットしない）
        self._generated = _GEN_COUNTER[0]
        _t0 = _time.perf_counter()
        # 部分レンダの時間窓を検証・保持（式のt基準は保ちつつ窓外を出力しない）
        if start is not None or end is not None:
            if start is not None:
                _require_time("render", "start", start, lo=0)
            if end is not None:
                _require_time("render", "end", end, lo=0)
            s = 0.0 if start is None else float(start)
            e = end if end is None else float(end)
            if e is not None and e <= s:
                raise ValueError(f"render: end({end}) は start({start}) より後が必要です")
            self._render_window = (s, e)
        # Plan pass: アンカー解決 + 総尺確定
        self._resolve_plan_duration()
        # 部分レンダ窓の実効出力長を検証する。start が総尺以上だと -ss/-t 0 の
        # 空MP4が成功扱いで確定してしまう（監査 issue #14 P1）。
        # end > 総尺は従来どおり総尺へ clamp する。
        if self._render_window is not None:
            w_start, w_end = self._render_window
            eff_end = self.duration if w_end is None \
                else min(w_end, self.duration)
            if eff_end - w_start <= 0:
                raise ValueError(
                    f"render: 出力区間が空です（start={w_start}, "
                    f"end={w_end if w_end is not None else '総尺'}, "
                    f"総尺={self.duration}s）。start は総尺より小さい値を"
                    f"指定してください。")
        # Render pass: 本実行（cache検証→レイヤー実行→anchors→構造検証）
        self._execute_render_pass()

        # strict: p.audit() の warning が1件でもあればレンダ前に停止する
        # （品質lintの厳格モード。dry_run にも適用してCI等で早期検出できるように）
        if strict:
            from importlib import import_module as _import_module
            _sva = _import_module("scriptvedit.audit")
            warns = [f for f in _sva.audit_project(self)
                     if f["severity"] == "warning"]
            if warns:
                raise RuntimeError(
                    f"render(strict=True): audit の warning が {len(warns)} 件"
                    f"あります:\n" + "\n".join(
                    f"  [{f['code']}] {f['message']}" for f in warns))

        # 部分レンダ窓と交差しないWeb Objectは入力自体が不要。未生成cacheを
        # 要求しないよう、plan/render構造検証とauditの後でレンダ対象から外す。
        self._prune_window_invisible_web_objects()

        if dry_run:
            all_extra = self._collect_all_extra_cmds()
            cmd = self._build_ffmpeg_cmd(output_path)
            # 実行時に _run_ffmpeg が付ける共通の診断フラグ
            # （-hide_banner / -loglevel / -nostdin）をここでも同じ規則で付ける。
            # dry_run の戻り値＝実際に実行されるコマンド、を保つための正規化で、
            # スナップショットが「実行されない形」を守る事故を防ぐ（監査 項目9）。
            # 常に {"main": 最終コマンド, "cache": {出力パス: 生成コマンド}} を返す
            # （cache が空でも形は同じ。呼び出し側の分岐を不要にする）
            return {"main": _normalize_ffmpeg_cmd(cmd),
                    "cache": _normalize_extra_cmds(all_extra)}

        # 統計（ヒット/ミス）は生成側の各段が _note_planned_artifact で記録する。
        # 以前はここで _collect_* を空回しして数え、その破壊的な副作用を
        # 手動スナップショットで巻き戻していたが、巻き戻し漏れが起きると
        # Object が壊れたまま本レンダへ進む脆い構造だった（監査 項目14）。
        self._ensure_formula_objects()
        self._ensure_web_objects()
        for path in self._pending_compute_cmds:
            self._note_planned_artifact(path)
        self._ensure_checkpoints()
        n_chunks = _parallel_chunk_count(self, parallel, output_path)
        if n_chunks >= 2:
            n_chunks = _render_parallel(self, output_path, n_chunks, timeout)
        else:
            cmd = self._build_ffmpeg_cmd(output_path)
            self._print_render_command(cmd, output_path)
            fmt = self._resolve_output_format(output_path)
            if fmt["kind"] == "pngseq":
                # 連番は単一パスへ原子的に確定できないため従来どおり直接出力する。
                self._run_main_ffmpeg(cmd, output_path, timeout)
            else:
                # 最終単一出力もキャッシュと同じく、同拡張子の一時パスへ書いてから
                # 原子的に確定する。timeout/Ctrl+C/ffmpeg失敗では一時ファイルだけを
                # 消すため、壊れた新規出力も、既存の正常な完成品の消失も防げる。
                final_path = fmt["output_path"]
                tmp_path = _unique_tmp_path(final_path)
                run_cmd = list(cmd)
                if not run_cmd or os.fsdecode(run_cmd[-1]) != final_path:
                    raise ValueError(
                        "render: ffmpegコマンドの最終出力パスを一時パスへ置換できません")
                run_cmd[-1] = tmp_path
                try:
                    self._run_main_ffmpeg(run_cmd, output_path, timeout)
                    os.replace(tmp_path, final_path)
                finally:
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
        _generate_pending_caches(self)
        elapsed = _time.perf_counter() - _t0
        # ネストrender（from_project）は同じグローバルカウンタを共有するため、
        # 0へリセットせず「このレンダ開始時からの増分」を数える
        # （リセットするとネストより前に親が生成した分が消える。監査 項目15d）
        generated = _GEN_COUNTER[0] - self._generated
        cache_hits = sum(1 for existed in self._render_planned.values() if existed)
        cache_misses = len(self._render_planned) - cache_hits
        # strict でなくても audit を回して1行サマリを出す（既定の render が
        # 品質lintを一切実行しないと、出力末尾しか読まない利用者・AIに
        # 「文字が画面外」「尺外に配置」等の沈黙する失敗が届かないため）。
        # サマリは補助機能なので、audit 自体の失敗で本レンダを失敗させない。
        audit_line = None
        try:
            from importlib import import_module as _import_module
            _findings = _import_module("scriptvedit.audit").audit_project(self)
            if _findings:
                _w = sum(1 for f in _findings if f["severity"] == "warning")
                _i = sum(1 for f in _findings if f["severity"] == "info")
                audit_line = (f"[audit] warning {_w} / info {_i}"
                              " — 詳細は p.audit()")
        except Exception:
            audit_line = None  # 品質lintの失敗はレンダ結果に影響しない
        if audit_line:
            print(audit_line)
        print(f"\n完了: {output_path}")
        mode = "ドラフト" if draft else "本番"
        if n_chunks >= 2:
            mode += f"・並列{n_chunks}"
        print(f"[統計] {mode} / 総時間 {elapsed:.2f}s / "
              f"キャッシュ ヒット{cache_hits} ミス{cache_misses} / "
              f"生成した中間ファイル {generated}件")
        self._print_render_warnings()

    def _print_render_warnings(self):
        """レンダ中に出た警告を出力末尾へまとめて再掲する（監査 項目8）。

        警告0件なら行ごと出さない。ここに出るのは「レンダは完了したが内容に
        影響する」ものだけ（音声脱落・エンコーダのフォールバック・尺の
        フォールバック等）で、出力末尾しか読まなくても異常に気付ける。
        """
        warns = list(self._sticky_warnings) + [
            w for w in self._render_warnings if w not in self._sticky_warnings]
        if not warns:
            return
        print(f"[警告] {len(warns)}件 — レンダは完了しましたが内容に影響します:")
        for message in warns:
            lines = str(message).splitlines() or [""]
            print(f"  ⚠ {lines[0]}")
            for cont in lines[1:]:
                print(f"    {cont}")

    # --- サムネイル/絵コンテ（実装は preview.py。委譲メソッドは manifest 掲載用）---

    def thumbnail(self, at, out, *, timeout=600, source=None):
        """指定時刻 at(秒) のフレームを1枚のPNGとして書き出す。

        render() と同じプラン解決・チェックポイント生成を通し、
        フィルタグラフの t 基準を保ったまま -ss + -frames:v 1 で抜き出す。
        source に既レンダ動画を指定すると、Projectグラフを再構築せず入力seekで
        高速に抽出する。
        """
        return _thumbnail_impl(self, at, out, timeout=timeout, source=source)

    def storyboard(self, out_path, *, cols=4, interval=None, source=None,
                   timeout=600):
        """タイムラインの絵コンテ（サムネイル格子画像）を1枚のPNGとして生成する。

        interval秒ごと（省略時は 総尺/12）に thumbnail() と同じ抽出経路
        （plan解決+checkpoint確保+ffmpeg単フレーム抽出）でサムネイルを取り出し、
        PILでcols列のグリッドに結合する（各コマ左上に時刻ラベルを焼き込む）。
        事前renderなしの場合も、Projectグラフの準備とFFmpeg実行は各1回だけ。
        source に既レンダ動画を指定すれば入力seekで軽量に抽出する。

        戻り値: 書き出したパス(out_path)。
        """
        return _storyboard_impl(self, out_path, cols=cols, interval=interval,
                                source=source, timeout=timeout)

    def inspect(self, out_html=None, *, title=None):
        """scriptvedit.viz による検査ビュー。

        out_html 指定時は HTML ガントチャートを書き出しそのパスを返す。
        省略時はプレーンテキストのレポート文字列を返す（遅延 import）。
        """
        try:
            # 属性参照ではなくモジュール直接 import（プラグインの名前空間注入の影響を受けない）
            from importlib import import_module as _import_module
            _svi = _import_module("scriptvedit.viz")
        except ImportError as e:
            raise ImportError(
                "inspect() に必要な scriptvedit.viz が読み込めません。"
                "`pip install -e .[all]` で再インストールしてください。") from e
        if out_html is not None:
            return _svi.render_timeline(self, out_html, title=title)
        return _svi.report_text(self)

    def audit(self, *, strict=False, quiet=False):
        """動画の品質lint。findings のリストを返す（レンダはしない）。

        過去の人間レビュー由来のチェック（文字サイズ・縁取り、BGMの
        duck_under/ループ/尺、normalize_audio）と、`~` 品質ヒントが
        尊重されない op の報告（契約どおり実行時警告は出さず、ここに集約）。

        strict=True: warning が1件でもあれば RuntimeError（CI等の厳格モード用）。
        quiet=True: レポートを print しない（戻り値だけ使う場合）。
        戻り値: [{"severity": "warning"|"info", "code": str, "message": str}, ...]
        """
        from importlib import import_module as _import_module
        _sva = _import_module("scriptvedit.audit")
        # objects 未解決（layer登録のみ）なら dry_run で解決してから検査する
        if not self.objects and self._layer_specs:
            self.render("__audit__.mp4", dry_run=True)
        findings = _sva.audit_project(self)
        if not quiet:
            print(_sva.format_report(findings))
        if strict:
            warns = [f for f in findings if f["severity"] == "warning"]
            if warns:
                raise RuntimeError(
                    f"audit: warning が {len(warns)} 件あります（strict=True）:\n"
                    + "\n".join(f"  [{f['code']}] {f['message']}" for f in warns))
        return findings

    def _plan_resolve(self):
        """Plan pass: レイヤーを1回だけ exec し、アンカーを解決する。

        以前はここに「全レイヤー exec → _resolve_anchors」の外側ループがあり、
        アンカーが1つも無いプロジェクトでも必ず2周していた（Render pass を
        含めるとレイヤー .py は計3回 exec）。しかしレイヤー exec 経路
        （_exec_layer_body）は self._anchors を一切読まない（読むのは
        _resolve_anchors と viz.py だけ）ので、2周目は同じ構造を作り直す
        だけの空回しだった（監査 項目13）。
        アンカーの固定点は _resolve_anchors 内部の反復が担う（循環は
        _detect_start_after_cycles、非収束は同メソッドの RuntimeError で検出）。
        副次的に _verify_plan_structure の比較対象が
        「1回目の exec vs Render pass」になり、初回だけ副作用で構造が変わる
        非決定的レイヤーをより確実に捕まえられる。
        """
        self.objects = []
        self._layers = []
        self._mode = "plan"
        for spec in self._layer_specs:
            # Plan passではレイヤーキャッシュを使わず常に実行
            self._exec_layer(spec["filename"], spec["priority"])
        self._resolve_anchors(check_unresolved=False)
        # 未解決のuntilチェック（診断付き）
        unresolved = []
        for item in self.objects:
            until_name = getattr(item, '_until_anchor', None)
            if until_name and until_name not in self._anchors:
                unresolved.append((until_name, item))
        if unresolved:
            names = ", ".join(f"'{n}'" for n in sorted(set(n for n, _ in unresolved)))
            defined = ", ".join(f"'{n}'" for n in sorted(self._anchors.keys())) or "(なし)"
            details = []
            for name, item in unresolved:
                offset = getattr(item, '_until_offset', 0.0)
                offset_str = f", offset={offset}" if offset != 0.0 else ""
                if isinstance(item, Pause):
                    details.append(f"  pause.until('{name}'{offset_str})")
                elif isinstance(item, Object):
                    details.append(f"  Object('{item.source}').until('{name}'{offset_str})")
                else:
                    details.append(f"  {type(item).__name__}.until('{name}'{offset_str})")
            raise RuntimeError(
                f"未定義のアンカーが参照されています: {names}\n"
                f"定義済みアンカー: {defined}\n"
                f"参照元:\n" + "\n".join(details)
            )
        # Plan確定時の構造署名を保存（Render passでの構造一致検証に使う）
        self._plan_structure = self._layer_structure_signature()

    # --- レイヤーキャッシュ（実体は layercache.py）---
    # viz.py・テストがインスタンス経由で呼ぶため Project 上に残す。薄いラッパ
    # 関数を挟まず自由関数をそのままメソッドへ束縛する（呼び出し段数を
    # 従来と同じに保ち、_warn の stacklevel をずらさないため）。
    _layer_cache_paths_for = _layer_cache_paths_for_impl
    _current_layer_sources_meta = _current_layer_sources_meta_impl
    _should_use_cache = _should_use_cache_impl
    _load_cached_layer = _load_cached_layer_impl
    _get_layer_data = _get_layer_data_impl
    _build_layer_cache_cmd = _build_layer_cache_cmd_impl

    def _collect_all_extra_cmds(self):
        """中間生成物（web/checkpoint/レイヤーキャッシュ/compute）の生成コマンド辞書。

        収集順は実レンダの実行順に一致させる:
        _ensure_web_objects → _ensure_checkpoints → _generate_pending_caches。
        _collect_checkpoint_cmds は obj.source をチェックポイント成果物へ
        破壊的に差し替えるため、cache を先に集めると「実レンダでは走らない
        コマンド」（素材を直接入力にしたレイヤーキャッシュ）を返してしまう。
        収集順は必ずこの1箇所に閉じ込めること。
        """
        extra = {}
        extra.update(self._collect_web_cmds())
        # web Objectのsourceを予定webmパスに仮差し替え
        # （layer cache / checkpoint収集より前。-i xxx.html の混入を防ぐ）
        for obj in self.objects:
            if isinstance(obj, Object) and obj.media_type == "web":
                obj.source = _web_cache_path(obj, self)
                obj.media_type = "video"
        extra.update(_collect_checkpoint_cmds(self))
        extra.update(_collect_cache_cmds(self))
        extra.update(self._pending_compute_cmds)
        return extra

    # --- チェックポイント（実体は checkpoint.py）---
    # viz.py・テストがインスタンス経由で呼ぶため Project 上に残す。薄いラッパ
    # 関数を挟まず自由関数をそのままメソッドへ束縛する（呼び出し段数を
    # 従来と同じに保つため）。第1引数 project がそのまま self になる。
    _plan_object_checkpoints = _plan_object_checkpoints_impl
    _apply_checkpoint_final_state = _apply_checkpoint_final_state_impl
    _process_checkpoints = _process_checkpoints_impl
    _ensure_checkpoints = _ensure_checkpoints_impl

    # ffmpeg を実際に起動する段だけは project.py に残す。テストが
    # scriptvedit.project._run_ffmpeg_to_cache を差し替えて生成を偽装するため、
    # この名前の解決先モジュールを変えてはいけない。
    def _execute_checkpoint_step(self, step, obj=None):
        """計画ステップ1件を実行する（実レンダ専用）。

        pre_bake/frame_extract は存在チェックのみで再利用、
        checkpoint/morph/particle は policy="force" なら再生成する（従来判定）。
        obj は失敗時の診断（どのオブジェクト起因か）にのみ使う。
        """
        path = step["path"]
        kind = step["kind"]
        if kind == "particle":
            # 従来と同じく、キャッシュ有無に関わらず粒子分岐へ到達した時点で
            # モジュールを解決する（依存欠如の検出タイミングを変えない）
            from scriptvedit.morph import (generate_explode_frames,  # noqa: F401
                               generate_assemble_frames)  # noqa: F401
        if kind in ("pre_bake", "frame_extract"):
            if os.path.exists(path):
                return
            cmd = step["build_cmd"]()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            print(f"{step['label']}: {path}")
            _run_ffmpeg_to_cache(cmd, path, timeout=600,
                                 context=_step_context(step, obj))
            return
        need_render = (step["policy"] == "force") or not os.path.exists(path)
        if not need_render:
            return
        if kind in ("morph", "particle"):
            self._execute_frames_step(step, obj)
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cmd = step["build_cmd"]()
        print(f"{step['label']}: {path}")
        _run_ffmpeg_to_cache(cmd, path, timeout=600,
                             context=_step_context(step, obj))

    def _execute_frames_step(self, step, obj=None):
        """morph/particle 共通: 一時dirへPNG連番を生成しffv1中間へエンコードする。

        フレーム生成（PIL）は実レンダでのみ行う（dry_runはプレースホルダの
        フレームパターンを持つコマンドだけを収集する）。
        """
        import tempfile
        op = step["op"]
        dur = step["dur"]
        fps = step["fps"]
        path = step["path"]
        with tempfile.TemporaryDirectory() as tmpdir:
            n_frames = _morph_frame_count(fps, dur)
            # blend Exprを数値関数に変換
            blend_expr = op.params.get("blend")
            if blend_expr is not None and isinstance(blend_expr, Expr):
                blend_fn = lambda t, _e=blend_expr: _e.eval_at(t)
            else:
                blend_fn = None
            gen_kw = {k: v for k, v in op.params.items() if k != "blend"}
            if step["kind"] == "morph":
                from scriptvedit.morph import generate_rgba_frames
                generate_rgba_frames(
                    step["src"], op._morph_target.source,
                    tmpdir, n_frames, blend_fn=blend_fn, **gen_kw)
            else:
                from scriptvedit.morph import (generate_explode_frames,
                                   generate_assemble_frames)
                gen = (generate_explode_frames if op.name == "explode_to"
                       else generate_assemble_frames)
                gen(step["src"], tmpdir, n_frames, blend_fn=blend_fn, **gen_kw)
            frame_pattern = os.path.join(tmpdir, "frame_%05d.png")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            cmd = _build_morph_webm_cmd(frame_pattern, path, dur, fps)
            print(f"{step['label']}: {path}")
            _run_ffmpeg_to_cache(cmd, path, timeout=600,
                                 context=_step_context(step, obj))

    def _collect_web_cmds(self):
        """dry_run用: web Objectのwebmエンコードコマンドを収集"""
        cmds = {}
        for obj in self.objects:
            if isinstance(obj, Object) and obj.media_type == "web":
                if not obj._web_capture_spec(self)["visible"]:
                    continue
                webm_path = _web_cache_path(obj, self)
                cmds[webm_path] = obj._build_web_cmd(self, webm_path)
        return cmds

    def _ensure_formula_objects(self):
        """formula()/formula_lines() の数式PNGを実レンダ直前に生成する。

        Objectのsourceは構築時点で content-addressed なキャッシュパス
        （__cache__/artifacts/formula/<hash>.png）に決まっているため、
        dry_run ではPlaywrightを起動せずコマンドを組み立てられる。
        """
        from scriptvedit.formula import _render_formula_png
        for obj in self.objects:
            spec = getattr(obj, "_formula_spec", None)
            if not isinstance(obj, Object) or spec is None:
                continue
            if os.path.exists(obj.source):
                continue
            print(f"数式レンダ: {spec['lines']}")
            _render_formula_png(spec, obj.source, getattr(obj, "_formula_fn", "formula"))
            print(f"  完了: {obj.source}")

    def _ensure_web_objects(self):
        """web ObjectのPlaywrightレンダ+ffmpegエンコード実行、sourceをwebmに差し替え"""
        for obj in self.objects:
            if not isinstance(obj, Object) or obj.media_type != "web":
                continue
            if not obj._web_capture_spec(self)["visible"]:
                continue
            webm_path = _web_cache_path(obj, self)
            # 旧実装は _ensure_web_objects の後で _collect_web_cmds を呼んでおり
            # （media_type が既に "video" なので常に空）、Webクリップだけが
            # 統計に一度も計上されていなかった（監査 項目14）
            self._note_planned_artifact(webm_path)

            if not os.path.exists(webm_path):
                print(f"Webクリップ生成: {obj.source}")
                # 実生成の作業ディレクトリは pid+uuid でユニーク化する。
                # 決定的な固定名だと同名 web Object の並列レンダが相互に
                # フレームを削除・混入する（issue #13 P2-15）
                frames_dir = _web_frames_dir(obj._web_name, unique=True)
                try:
                    obj._render_web_frames(self, frames_dir)
                    cmd = obj._build_web_cmd(self, webm_path, frames_dir)
                    os.makedirs(os.path.dirname(webm_path), exist_ok=True)
                    if self._verbose():
                        print(f"  ffmpeg {' '.join(cmd[1:])}")
                    _run_ffmpeg_to_cache(
                        cmd, webm_path, timeout=600,
                        context=f"Webクリップの生成: {obj._web_source}")
                    print(f"  完了: {webm_path}")
                finally:
                    # フレーム削除（失敗時も中間フレームを残さない）
                    if obj._web_debug_frames:
                        print(f"  デバッグフレーム保持: {frames_dir}")
                    elif os.path.exists(frames_dir):
                        import shutil
                        shutil.rmtree(frames_dir, ignore_errors=True)

            obj.source = webm_path
            obj.media_type = "video"

    def _prune_window_invisible_web_objects(self):
        """部分レンダ窓の外にあるWeb Objectを今回の入力から除外する。"""
        if self._render_window is None:
            return
        keep = [
            not (isinstance(obj, Object) and obj.media_type == "web"
                 and not obj._web_capture_spec(self)["visible"])
            for obj in self.objects
        ]
        # _layers はobjectsのslice indexを保持する。objectsだけを詰めると
        # 後続layer cacheが隣レイヤーを混入/欠落するため、旧indexまでの
        # keep件数で全境界を同時に写し替える。
        kept_before = [0]
        for selected in keep:
            kept_before.append(kept_before[-1] + int(selected))
        self.objects = [obj for obj, selected in zip(self.objects, keep)
                        if selected]
        self._layers = [
            (kept_before[start_idx], kept_before[end_idx], priority)
            for start_idx, end_idx, priority in self._layers
        ]

    def _parallel_workers(self):
        """キャッシュ並列生成のワーカ数を決定（configure(parallel=N)優先、既定は控えめ）"""
        if self._parallel is not None:
            return _builtins.max(1, int(self._parallel))
        cpu = os.cpu_count() or 2
        # ffmpeg自体がマルチスレッドのため控えめに（CPU数-1、上限4）
        return _builtins.max(1, _builtins.min(cpu - 1, 4))

    # 計画・鮮度判定は layercache.py だが、ffmpeg を起動するこの段だけは
    # project.py に残す（_execute_checkpoint_step と同じ理由）。
    def _render_layer_to_cache(self, spec_index):
        """レイヤーキャッシュ生成実行"""
        spec = self._layer_specs[spec_index]
        webm_path, json_path = self._layer_cache_paths_for(spec)
        os.makedirs(os.path.dirname(webm_path), exist_ok=True)

        cmd = self._build_layer_cache_cmd(spec_index, webm_path)
        print(f"キャッシュ生成: {webm_path}")
        if self._verbose():
            print(f"  ffmpeg {' '.join(cmd[1:])}")
        _run_ffmpeg_to_cache(
            cmd, webm_path, timeout=600,
            context=f"レイヤーキャッシュの生成: {spec['filename']}")
        print(f"  完了: {webm_path}")

        # anchors.json書き出し（素材FFPも記録してキャッシュ鮮度検証に使う）
        objects, anchors = self._get_layer_data(spec_index)
        dur = self.duration or self._calc_total_duration()
        # 鮮度判定と同じヘルパーで現在の依存集合を記録する（集合の完全一致比較の
        # ため、書き込みと判定で構築規則を共有する。監査 issue #16 P0）。
        # 指紋不能な依存(None)は記録しない → 判定側で常に stale になり fail-closed
        sources_meta = {
            key: ffp for key, ffp in
            self._current_layer_sources_meta(spec["filename"]).items()
            if ffp is not None
        }
        cache_meta = {
            "duration": dur,
            "anchors": anchors,
            "sources": sources_meta,
            "params": self._layer_params.get(spec["filename"], {}),
            "audio_sources": self._layer_audio_sources.get(spec["filename"], []),
            "unknown_audio_sources": self._layer_unknown_audio_sources.get(
                spec["filename"], []),
        }
        # アトミック書き込み（webmと同様、中断による壊れたメタの残留を防ぐ。
        # tmp→os.replace の作法は ffmpeg.py の共通ヘルパに集約）
        _atomic_write_text(json_path,
                           json.dumps(cache_meta, indent=2, ensure_ascii=False))
        print(f"  アンカー保存: {json_path}")

    def _loop_trim_duration(self, obj, loop_effect):
        """loop(until=...) の実効トリム尺を返す（until優先→duration→全体尺）"""
        start = obj.start_time
        until = loop_effect.params.get("until")
        if until is not None:
            return max(0.0, until - start)
        if obj.duration is not None:
            return obj.duration
        total = self.duration or self._calc_total_duration()
        return max(0.0, total - start)

    def _build_aloop_filter(self, obj, loop_effect):
        """aloop フィルタ文字列を構築（元素材長からループ用サンプル数を決定）。
        aloopは無限ループ(loop=-1)し、後段のatrim/durationで尺を確定する。

        probe 先は obj.source ではなく **obj.audio_source（元素材）**。
        source はチェックポイントで `-an` の映像専用中間物へ差し替わりうるので、
        そちらを見ると (a) 音声ストリームが無いので sample_rate が取れず、
        (b) cold（未生成）と warm（実体化済み）で probe の成否が変わって
        **dry_run の出力コマンドがキャッシュ状態に依存してしまう**
        （CLAUDE.md §3「dry_run はキャッシュ状態に依存しない」の違反）。
        音声は常に元素材から取るので、尺も SR も元素材を見るのが正しい。
        """
        audio_src = obj.audio_source
        length = _probe_audio_length(audio_src)
        # 実サンプルレートを取得（高SR素材でも1周期分を確実に確保するため）。
        info = self._probe_media(audio_src)
        sr = info.get("sample_rate") if info else None
        if length and sr:
            size = int(_math.ceil(length * sr)) + sr
        elif length:
            # SR不明時は大きめ（192kHz相当）で1周期分＋余裕を確保
            size = int(_math.ceil(length * 192000)) + 192000
        else:
            size = 192000 * 60  # 取得不能時のフォールバック（約1分・192kHz相当）
        return f"aloop=loop=-1:size={size}"

    def _resolve_obj_duration(self, obj, fallback=5):
        """objのduration未設定/0のとき実長で補完（取得不能・0のときのみfallback）

        duration=0 をそのまま返すと u正規化 clip((t-start)/0,...) のゼロ除算で
        ffmpegがEINVAL失敗するため、0はfallbackに落とす。
        """
        if obj.duration:
            return obj.duration
        # checkpoint等でsourceが予定パスに差し替わる前に解決した実長を最優先
        resolved = getattr(obj, '_resolved_length', None)
        if resolved:
            return resolved
        if obj.media_type not in ("image", "text"):
            # trim/atrim/atempoを反映した加工後長（チェックポイントベイクと同一基準）
            try:
                length = obj.length()
            except Exception as e:
                # 以前はここで全例外を握り潰して 5 秒に落としていた。捨てていたのは
                # 「メディアの長さを取得できません」「length()にはアクティブな
                # Projectが必要です」等、原因も対処も明確な例外で、壊れた素材や
                # ffprobe 不在が警告すら無く「5秒の素材」になっていた（項目8b）。
                if _is_pending_cache_path(obj.source):
                    return fallback  # 未生成の予定パス（dry_run の正常系）
                if self._dry_run:
                    # dry_run は素材が揃っていなくても構造を返せるべきなので続行
                    # するが、黙って 5 秒にはせず [警告] へ記録する
                    _warn(self,
                          f"素材の尺を取得できないため {fallback} 秒とみなします: "
                          f"{obj.source}（{e}）")
                    return fallback
                raise RuntimeError(
                    f"尺を解決できません: {obj.source}（start={obj.start_time}）。"
                    f"time() で明示的な尺を指定するか、素材と ffprobe を"
                    f"確認してください") from e
            if length:
                return length
            if not self._dry_run and not _is_pending_cache_path(obj.source):
                # probe は成功したが尺が取れない（0/None）。u正規化の分母と
                # ベイク基準の両方に効くので、黙って 5 秒にはしない
                _warn(self,
                      f"素材の尺を取得できないため {fallback} 秒とみなします: "
                      f"{obj.source}")
        return fallback

    # --- 時間分割並列レンダ（実装と設計原理は parallel.py） ---

    @staticmethod
    def _parallel_chunk_bounds(duration, fps, n):
        """総尺をフレーム境界でn分割する（parallel.py への委譲。
        テストが Project 経由で参照するため薄いスタブを残す）。"""
        return _parallel_chunk_bounds(duration, fps, n)

    def _resolve_output_format(self, output_path):
        """出力パスの拡張子・draft/alpha/絵コンテ設定から出力形式を決定する。

        "thumb"（サムネイル1枚）はここでは返さない。サムネイルは全尺グラフを
        組まずチャンク生成器で1フレームだけ作る（preview.py）ため、形式dictは
        preview 側が直接組み立てて _encode_args へ渡す（監査 項目19）。

        戻り値 dict:
          kind:  "h264" | "gif" | "webp" | "pngseq" | "webm" | "storyboard"
          alpha: 背景を透過にするか
          has_audio: この形式が音声トラックを持てるか
          output_path: 実際にffmpegへ渡す出力パス（連番PNGは %05d 化）
        """
        alpha = bool(self._alpha)
        if getattr(self, "_storyboard_frame_indices", None) is not None:
            return {"kind": "storyboard", "alpha": False, "has_audio": False,
                    "output_path": output_path}
        ext = os.path.splitext(output_path)[1].lower()
        if ext == ".gif":
            return {"kind": "gif", "alpha": False, "has_audio": False,
                    "output_path": output_path}
        if ext == ".webp":
            return {"kind": "webp", "alpha": alpha, "has_audio": False,
                    "output_path": output_path}
        if ext == ".png":
            # 連番PNG（out.png -> out_%05d.png）。既に%が含まれるなら尊重
            op = output_path
            if "%" not in op:
                base, _e = os.path.splitext(output_path)
                op = f"{base}_%05d.png"
            return {"kind": "pngseq", "alpha": True, "has_audio": False,
                    "output_path": op}
        if ext == ".webm":
            return {"kind": "webm", "alpha": alpha, "has_audio": True,
                    "output_path": output_path}
        return {"kind": "h264", "alpha": alpha, "has_audio": True,
                "output_path": output_path}

    # --- 本レンダの実行と失敗時の診断（監査 項目9）---

    @staticmethod
    def _verbose():
        """SCRIPTVEDIT_VERBOSE=1 のとき True（コマンド全文の表示など）。"""
        return os.environ.get("SCRIPTVEDIT_VERBOSE", "") not in ("", "0")

    def _print_render_command(self, cmd, output_path):
        """実行するコマンドの1行サマリを出す（全文は VERBOSE のときだけ）。

        フィルタ込みの全文は数千文字あり、毎回無条件に出すと出力末尾しか
        読まない利用者・AIの文脈を食い潰す。失敗時は FFmpegError が
        原因1行を出し、全文は例外の cmd 属性から取れる。
        """
        n_inputs = sum(1 for a in cmd if a == "-i")
        filter_len = 0
        for i, arg in enumerate(cmd[:-1]):
            if arg in ("-filter_complex", "-vf", "-af"):
                filter_len = _builtins.max(filter_len, len(cmd[i + 1]))
        print(f"実行: ffmpeg 入力{n_inputs}件 / フィルタ{filter_len}文字 "
              f"→ {output_path}  (全文は SCRIPTVEDIT_VERBOSE=1)")
        if self._verbose():
            print(f"  ffmpeg {' '.join(cmd[1:])}")
        print()

    def _render_input_table(self):
        """入力番号 → 素材/開始時刻/エフェクト の対応表（失敗時の診断用）。

        フィルタ内のラベルは [fx<入力番号>...] なので、ffmpeg のエラーに
        出たラベルから素材へ辿れる。
        """
        renderable = [o for o in self.objects if isinstance(o, Object)]
        sorted_objects = sorted(renderable, key=lambda o: o.priority)
        lines = ["入力の対応表（[fxN] の N が入力番号）:",
                 "  入力0: 背景キャンバス"]
        for i, obj in enumerate(sorted_objects, start=1):
            effects = ",".join(e.name for e in obj.effects) or "なし"
            transforms = ",".join(t.name for t in obj.transforms) or "なし"
            lines.append(
                f"  入力{i}: {obj.source} start={obj.start_time}s "
                f"dur={obj.duration} transform=[{transforms}] "
                f"effect=[{effects}]")
        # チェックポイントで source が中間物へ差し替わった Object の音声専用入力
        # （_build_ffmpeg_cmd と同じ順序・同じ条件で並べる）。音声を持てない
        # 出力形式（gif/webp/png連番等）では付かないので、その旨を添える。
        idx = 1 + len(sorted_objects)
        for obj in sorted_objects:
            if not obj.has_audio or obj.audio_source == obj.source:
                continue
            lines.append(
                f"  入力{idx}: {obj.audio_source}"
                f"（音声のみ。音声を持つ出力形式のときだけ付く）")
            idx += 1
        return lines

    def _run_main_ffmpeg(self, cmd, output_path, timeout):
        """本レンダの ffmpeg を実行し、失敗時は入力の対応表を添える。"""
        try:
            _run_ffmpeg(cmd, timeout=timeout,
                        context=f"本レンダの出力: {output_path}")
        except FFmpegError:
            for line in self._render_input_table():
                print(line)
            print()
            raise

    def _build_ffmpeg_cmd(self, output_path):
        inputs = []
        filter_parts = []
        fmt = self._resolve_output_format(output_path)
        output_path = fmt["output_path"]

        # 音声レグ（並列レンダ）: 映像は-mapしないため、全尺キャンバスの
        # 生成コストを避けてダミーの極小入力に差し替える（入力indexは維持）
        audio_only = bool(self._audio_only_render)

        # 背景入力（alpha出力時は透明キャンバス）
        if audio_only:
            bg_src = f"color=c=black:s=16x16:d=0.1:r={self.fps}"
        elif fmt["alpha"]:
            bg_src = (f"color=c=black@0.0:s={self.width}x{self.height}"
                      f":d={self.duration}:r={self.fps},format=rgba")
        else:
            bg_src = (f"color=c={self.background_color}:s={self.width}x{self.height}"
                      f":d={self.duration}:r={self.fps}")
        inputs.extend(["-f", "lavfi", "-i", bg_src])

        renderable = [o for o in self.objects if isinstance(o, Object)]
        sorted_objects = sorted(renderable, key=lambda o: o.priority)

        # 入力を追加（映像+音声共通）
        input_map = {}  # obj id → input_idx
        for i, obj in enumerate(sorted_objects):
            input_idx = i + 1
            input_map[id(obj)] = input_idx
            inputs.extend(_build_input_args(obj, self.fps))
        n_inputs = 1 + len(sorted_objects)  # 背景キャンバス + オブジェクト入力

        # --- 音声入力 ---
        # チェックポイントで source が中間物（-an の映像専用）へ差し替わった
        # Object は、音声を元素材（audio_source）から取るため専用の入力を足す。
        # こうしないと中間物に音声を焼き込む必要があり、その場合
        # 「未生成(cold)＝音声なし／生成済み(warm)＝音声あり」とキャッシュの
        # 有無で出力コマンドが変わる（_build_checkpoint_video_cmd の -an 参照）。
        audio_objects = ([o for o in sorted_objects if o.has_audio]
                         if fmt["has_audio"] else [])
        audio_input_map = dict(input_map)  # obj id → 音声入力index（既定は映像と同じ）
        for obj in audio_objects:
            if obj.audio_source == obj.source:
                continue
            audio_input_map[id(obj)] = n_inputs
            inputs.extend(_decoder_input_args(
                obj.audio_source, _detect_media_type(obj.audio_source),
                self.fps))
            n_inputs += 1

        # --- 映像チェーン ---
        current_base = "[0:v]"
        video_objects = [] if audio_only \
            else [o for o in sorted_objects if o.has_video]

        for obj in video_objects:
            input_idx = input_map[id(obj)]
            dur = self._resolve_obj_duration(obj)
            parts, out_label = _build_video_overlay_parts(
                obj, input_idx, current_base, dur,
                visible_window=_visible_window(obj, self.fps))
            filter_parts.extend(parts)
            current_base = out_label

        # --- 音声チェーン ---
        # サムネイル等の映像専用出力では音声枝を構築しない（audio_objects は
        # 入力構築の段で fmt["has_audio"] を見て決めてある）。構築だけして
        # -map しないと、loudnorm 等の終端が未接続になり ffmpeg が EINVAL で落ちる。
        audio_out = None

        if audio_objects:
            audio_labels = []
            idx_by_id = {}  # id(obj) → audio_labels内index（duck_underのother参照用）
            for ai, obj in enumerate(audio_objects):
                idx_by_id[id(obj)] = ai
                input_idx = audio_input_map[id(obj)]
                dur = self._resolve_obj_duration(obj)
                start = obj.start_time

                a_filters = []
                # loop（aloop）: atrim/adelayより前に置き、以降のトリムで尺を確定
                loop_effect = next(
                    (e for e in obj.audio_effects if e.name == "loop"), None)
                if loop_effect is not None:
                    a_filters.append(self._build_aloop_filter(obj, loop_effect))
                # atrim/atempo前処理
                a_pre = _build_audio_pre_filters(obj)
                # auto atrim: obj.durationがあり、明示atrimがなければ自動トリム
                has_explicit_atrim = any(
                    e.name == "atrim" for e in obj.audio_effects)
                if not has_explicit_atrim and obj.duration is not None:
                    # auto atrim は atempo（speed 追従含む）の後段に置く。
                    # 先頭に前置すると atrim=(base/factor)→atempo=factor の順になり
                    # 音声尺が base/factor² まで縮む不具合になるため末尾に回す。
                    a_pre = a_pre + [f"atrim=duration={obj.duration}",
                                     "asetpts=PTS-STARTPTS"]
                # loop で until 指定かつ obj.duration 未設定なら until までトリム
                if (loop_effect is not None and not has_explicit_atrim
                        and obj.duration is None):
                    lt = self._loop_trim_duration(obj, loop_effect)
                    a_pre = [f"atrim=duration={lt}", "asetpts=PTS-STARTPTS"] + a_pre
                a_filters.extend(a_pre)
                # 音声エフェクト（avolume 等）
                a_filters.extend(_build_audio_effect_filters(obj, dur))
                # adelay（タイミングシフト）: all=1 で全チャンネルに適用（2ch前提を排除）
                delay_ms = int(start * 1000)
                if delay_ms > 0:
                    a_filters.append(f"adelay={delay_ms}:all=1")

                a_label = f"[a{ai}]"
                if a_filters:
                    filter_parts.append(
                        f"[{input_idx}:a]{','.join(a_filters)}{a_label}"
                    )
                else:
                    a_label = f"[{input_idx}:a]"
                audio_labels.append(a_label)

            # duck_under（sidechaincompress）: other音声再生中に自音量を下げる。
            # otherをasplitでミックス用/サイドチェーン用に分岐して供給する。
            for ai, obj in enumerate(audio_objects):
                duck = next(
                    (e for e in obj.audio_effects if e.name == "duck_under"), None)
                if duck is None:
                    continue
                other = duck.params["other"]
                if other is obj:
                    raise ValueError("duck_under: other に自分自身は指定できません")
                if id(other) not in idx_by_id:
                    raise ValueError(
                        "duck_under: other が同じProjectの再生対象音声に含まれていません。"
                        "other 側の音声が adelete 等で除外されていないか確認してください。")
                oi = idx_by_id[id(other)]
                other_ref = audio_labels[oi]
                # sidechaincompress はサイドチェイン入力の EOF で
                # メイン(BGM)も終端しうる。検出用枝のみ無音で
                # 延長し、ナレーション終了後は原音量で継続させる。
                filter_parts.append(
                    f"{other_ref}asplit[dmix{ai}][dside_src{ai}]")
                filter_parts.append(
                    f"[dside_src{ai}]apad[dside{ai}]")
                audio_labels[oi] = f"[dmix{ai}]"
                my_ref = audio_labels[ai]
                p = duck.params
                filter_parts.append(
                    f"{my_ref}[dside{ai}]sidechaincompress="
                    f"threshold={p['threshold']}:ratio={p['ratio']}"
                    f":attack={p['attack']}:release={p['release']}[duck{ai}]")
                audio_labels[ai] = f"[duck{ai}]"

            if len(audio_labels) == 1:
                # フィルタなしの生入力参照は -map 用にブラケットを外す
                audio_out = _unwrap_raw_stream_ref(audio_labels[0], "a")
            else:
                amix_in = "".join(audio_labels)
                audio_out = "[aout]"
                filter_parts.append(
                    f"{amix_in}amix=inputs={len(audio_labels)}:normalize=0{audio_out}"
                )

            # normalize_audio: loudnorm → sample rate確定 → peak limiter。
            # リサンプルは補間により新しいピークを作り得るため、リミッターを
            # 必ず最終sample rateの後段に置く。
            if self._loudnorm_target is not None and audio_out is not None:
                ln_in = audio_out if audio_out.startswith("[") else f"[{audio_out}]"
                opts = self._loudnorm_options or {
                    "true_peak": -1.5, "lra": 11,
                    "limiter": False, "sample_rate": None,
                }
                if (fmt["kind"] == "webm"
                        and opts["sample_rate"] not in (None, 48000)):
                    raise ValueError(
                        "normalize_audio: WebMのlibopus音声は48kHz固定です。"
                        "sample_rate=48000 または None を指定してください")
                aac_sample_rates = {
                    8000, 11025, 12000, 16000, 22050, 24000,
                    32000, 44100, 48000, 64000, 88200, 96000,
                }
                if (fmt["kind"] == "h264"
                        and opts["sample_rate"] is not None
                        and opts["sample_rate"] not in aac_sample_rates):
                    raise ValueError(
                        "normalize_audio: AAC出力で未対応のsample_rateです: "
                        f"{opts['sample_rate']}。対応値: "
                        + ", ".join(str(v) for v in sorted(aac_sample_rates)))
                true_peak = opts["true_peak"]
                # AAC/Opusの量子化でtrue peakがわずかに再上昇するため、
                # ユーザー指定は最終出力目標とし、loudnorm/limiterには
                # 0.5dBのcodec headroomを確保する。loudnormの許容下限は-9。
                processing_peak = _builtins.max(-9.0, float(true_peak) - 0.5)
                filter_parts.append(
                    f"{ln_in}loudnorm=I={self._loudnorm_target}"
                    f":TP={processing_peak}:LRA={opts['lra']}[aout_ln]")
                normalized = "[aout_ln]"
                if opts["sample_rate"] is not None:
                    filter_parts.append(
                        f"{normalized}aresample={opts['sample_rate']}[aout_sr]")
                    normalized = "[aout_sr]"
                if opts["limiter"]:
                    # alimiter.limit は dB ではなく線形振幅で受け取る。
                    limit = 10 ** (processing_peak / 20.0)
                    filter_parts.append(
                        f"{normalized}alimiter=limit={limit:.9f}"
                        ":attack=5:release=50:level=0:latency=1[aout_lim]")
                    normalized = "[aout_lim]"
                audio_out = normalized

        # 出力前の映像後処理（draft縮小・GIFパレット生成）
        video_map = current_base
        if getattr(self, "_draft", False):
            # ドラフト: 解像度を半分に（式は _DRAFT_SCALE_FILTER に一元化）
            filter_parts.append(f"{video_map}{_DRAFT_SCALE_FILTER}[vdraft]")
            video_map = "[vdraft]"
        if fmt["kind"] == "gif":
            # 高品質パレット: split→palettegen→paletteuse を1グラフで実行
            filter_parts.append(
                f"{video_map}split[gsrc][gpg];"
                f"[gpg]palettegen=stats_mode=diff[gpal];"
                f"[gsrc][gpal]paletteuse=dither=bayer:bayer_scale=5"
                f":diff_mode=rectangle[vgif]")
            video_map = "[vgif]"
        elif fmt["kind"] == "storyboard":
            select_expr = "+".join(
                f"eq(n\\,{index})"
                for index in self._storyboard_frame_indices)
            filter_parts.append(f"{video_map}select='{select_expr}'[vstory]")
            video_map = "[vstory]"

        cmd = ["ffmpeg", "-y"]
        cmd.extend(inputs)

        # チャプター: FFMETADATAを追加入力にして -map_metadata で埋め込む
        # （音声レグでは付けない。並列レンダはconcat mux時に付与する）
        meta_idx = None
        emit_meta = bool(self._markers) and fmt["has_audio"] and not audio_only
        if emit_meta:
            meta_path = _chapters_metadata_path(self)
            if not self._dry_run:
                _write_chapters_metadata(self, meta_path)
            # メタ入力のストリーム index = 既存 -i 個数
            # （背景キャンバス + オブジェクト入力 + 追加した音声入力）。
            # len(sorted_objects) から数え直さないこと: 音声専用入力を足した分
            # ずれて、チャプターが別入力のメタを指す
            meta_idx = n_inputs
            cmd.extend(["-f", "ffmetadata", "-i", meta_path])

        use_audio = bool(audio_out) and fmt["has_audio"]
        if filter_parts:
            cmd.extend(["-filter_complex", ";".join(filter_parts)])
            # 映像Objectが無い（音声のみ＋音声フィルタ）場合、video_mapは
            # 生入力参照（[0:v]）のまま。音声側と同様にストリーム指定へ外す
            video_map = _unwrap_raw_stream_ref(video_map, "v")
            if not audio_only:
                cmd.extend(["-map", video_map])
            if use_audio:
                cmd.extend(["-map", audio_out])

        if meta_idx is not None:
            cmd.extend(["-map_metadata", str(meta_idx)])

        # --- 出力形式ごとのエンコード指定 ---
        if audio_only:
            # 音声レグ: 逐次レンダのh264音声設定と同一（_aac_audio_argsで強制）
            cmd.append("-vn")
            cmd.extend(self._aac_audio_args())
        else:
            cmd.extend(self._encode_args(fmt, use_audio))

        if fmt["kind"] == "storyboard":
            cmd.extend([
                "-fps_mode", "vfr", "-frames:v",
                str(len(self._storyboard_frame_indices)),
                "-start_number", "0", output_path,
            ])
            return cmd

        # 部分レンダ: 出力側 -ss/-t で窓を切り出す（フィルタのt基準は保つ）
        window = getattr(self, "_render_window", None)
        if window is not None:
            w_start, w_end = window
            w_end = self.duration if w_end is None else min(w_end, self.duration)
            out_dur = max(0.0, w_end - w_start)
            if w_start > 0:
                cmd.extend(["-ss", str(w_start)])
            cmd.extend(["-t", str(out_dur), output_path])
        else:
            cmd.extend(["-t", str(self.duration), output_path])

        return cmd

    def _encode_args(self, fmt, use_audio):
        """出力形式に応じた -c:v / -pix_fmt / -c:a 等のエンコード引数を返す"""
        kind = fmt["kind"]
        draft = bool(getattr(self, "_draft", False))
        args = []
        if kind == "thumb":
            # preview.py のサムネイル抽出だけが使う（_resolve_output_format は返さない）
            return ["-pix_fmt", "rgba", "-an"]
        if kind == "storyboard":
            return ["-c:v", "png", "-pix_fmt", "rgba", "-an"]
        if kind == "gif":
            # パレット適用済みなのでコーデック指定は不要。音声なし。
            return ["-an"]
        if kind == "webp":
            q = "60" if draft else "80"
            return ["-c:v", "libwebp", "-lossless", "0", "-q:v", q,
                    "-loop", "0", "-an"]
        if kind == "pngseq":
            return ["-c:v", "png", "-pix_fmt", "rgba", "-an"]
        if kind == "webm":
            pix = "yuva420p" if fmt["alpha"] else "yuv420p"
            crf = "34" if draft else "24"
            args = ["-c:v", "libvpx-vp9", "-pix_fmt", pix,
                    "-b:v", "0", "-crf", crf, "-auto-alt-ref", "0"]
            if use_audio:
                args.extend(["-c:a", "libopus", "-b:a", "160k"])
            else:
                args.append("-an")
            return args
        # h264 / 指定エンコーダ（yuv420p固定・透過非対応コンテナ）
        if self._alpha:
            raise ValueError(
                f"alpha=True は透過対応の出力(.webm/.webp/.png)でのみ有効です。\n"
                f"現在の出力形式({kind})では yuv420p 固定のため透明背景が黒潰れします。\n"
                f"透過が必要なら .webm / .webp / 連番.png で出力してください。")
        args = ["-c:v", self._encoder_cv]
        if draft:
            args.extend(self._encoder_draft_args)
        else:
            args.extend(self._encoder_args)
        args.extend(["-pix_fmt", "yuv420p"])
        if use_audio:
            args.extend(self._aac_audio_args())
        else:
            args.append("-an")
        return args

    def _aac_audio_args(self):
        """h264系出力のAAC音声引数（音声レグと逐次レンダの同一性をコードで強制）。

        FFmpegの低ビットレート既定AACは鋭いSEで数dBのピーク再上昇を
        起こし得る。160kを明示し、normalize_audioのcodec headroomが
        有効に働く品質へ固定する。sample_rate指定時は -ar も付ける。
        """
        args = ["-c:a", "aac", "-b:a", "160k"]
        opts = self._loudnorm_options \
            if self._loudnorm_target is not None else None
        if opts and opts["sample_rate"] is not None:
            args.extend(["-ar", str(opts["sample_rate"])])
        return args


# context に Project クラスの実体を渡す（context は project を import できないため、
# 型判定だけを逆向きに注入する。objects.from_project の引数検証で使う）。
_register_project_class(Project)
