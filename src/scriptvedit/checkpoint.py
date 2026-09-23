# -*- coding: utf-8 -*-
"""チェックポイント（中間キャッシュ）の計画・コマンド構築。

`_plan_object_checkpoints` が「計画の唯一の源」で、実レンダ
（`_process_checkpoints`）と dry_run（`_collect_checkpoint_cmds`）の両方が
ここを通る。片方だけ直してキャッシュパスがずれる事故を防ぐため、この単一性は
壊さないこと。

parallel.py / preview.py と同じ方式で、Project インスタンスを第1引数に受ける
自由関数を提供する（Project は import しない＝循環 import を起こさない）。
ffmpeg を実際に起動する段（`_execute_checkpoint_step` /
`_execute_frames_step` / `_render_layer_to_cache`）だけは project.py に残して
あり、ここは「何を作るか」を決めるだけで副作用を持たない。

project.py 側は、外部（viz.py・テスト）がインスタンス経由で呼ぶ関数を
クラス本体でそのままメソッドへ束縛する（薄いラッパ関数を挟まないのは、
スタックフレームを増やさず従来と同じ呼び出し段数を保つため）。
"""

import os
import math as _math
import builtins as _builtins

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.cache import _apply_time_effects_to_duration, _build_morph_frame_extract_cmd, _build_unified_ops, _checkpoint_cache_path, _compute_save_points, _is_bakeable, _morph_cache_path, _morph_input_frame_path, _particle_cache_path, _split_ops, _validate_morph_position
from scriptvedit.ffmpeg import _decoder_input_args
from scriptvedit.filters.video import _build_effect_filters, _build_transform_filters, _build_video_pre_filters, _get_base_dimensions, _optimize_filter_chain
from scriptvedit.objects import Object
from scriptvedit.state import _BAKE_PIX_FMT, _TERMINAL_FRAME_EFFECTS, _TIME_LIVE_EFFECTS, _detect_media_type


def _morph_frame_count(fps, dur):
    """morph/particle の生成フレーム数を返す（短尺でも最低1フレームを保証）。

    旧実装の int(fps * dur) は dur < 1/fps で 0 フレームになり PNG が
    1枚も生成されず後段 ffmpeg が失敗し、非整数尺でも末尾区間を覆えなかった。
    web Object（_web_frame_count）と同じ「切り上げ + 最低1」方針に統一する。
    """
    return _builtins.max(1, int(_math.ceil(float(fps) * float(dur))))


def _build_checkpoint_image_cmd(source, transforms, cache_path):
    """画像チェックポイント: Transform適用→透過PNG"""
    # 一時Object経由で _build_transform_filters を再利用
    temp = Object.__new__(Object)
    temp.source = source
    temp.transforms = list(transforms)
    temp.effects = []
    filters = _build_transform_filters(temp)
    cmd = ["ffmpeg", "-y", "-i", source]
    if filters:
        cmd.extend(["-vf", ",".join(filters)])
    cmd.extend(["-frames:v", "1", "-pix_fmt", "rgba", cache_path])
    return cmd


def _build_checkpoint_video_cmd(source, media_type, transforms, effects,
                                cache_path, dur, fps):
    """動画チェックポイント: Transform+Effect適用→FFV1(bgra) mkv（映像専用）"""
    cmd = ["ffmpeg", "-y"]
    cmd.extend(_decoder_input_args(source, media_type, fps))

    # フィルタ構築: 一時Object経由で既存ビルダーを再利用
    temp = Object.__new__(Object)
    temp.source = source
    temp.transforms = list(transforms)
    temp.effects = list(effects)
    temp.media_type = media_type

    base_dims = _get_base_dimensions(temp)
    filters = _build_transform_filters(temp)
    pre_filters = _build_video_pre_filters(temp)
    filters = pre_filters + filters
    eff_filters, _ = _build_effect_filters(temp, 0, dur, base_dims=base_dims)
    filters.extend(eff_filters)
    filters = _optimize_filter_chain(filters)

    if filters:
        cmd.extend(["-vf", ",".join(filters)])

    cmd.extend([
        "-c:v", "ffv1", "-level", "3",
        # 色変換を挟まない中間形式（理由と実測値は state.py の _BAKE_PIX_FMT）
        "-pix_fmt", _BAKE_PIX_FMT,
        # チェックポイントは**映像専用**。音声は常に元素材から取る
        # （Object.audio_source ＝ 差し替え前のパスを専用の入力にする）。
        # 中間物に音声を入れると、Object.has_audio の probe 先が
        # 差し替え後のこのファイルになり、未生成(cold)なら「音声なし」・
        # 生成済み(warm)なら「音声あり」と**キャッシュの有無で dry_run と
        # 実レンダのコマンドが食い違う**。加えて -vf は映像しか加工しない
        # ため、trim をベイクしても音声は切られず A/V がずれていた。
        "-an",
        "-t", str(dur), cache_path,
    ])
    return cmd


def _build_morph_webm_cmd(frame_pattern, cache_path, duration, fps):
    """PNG連番 → alpha映像 のffmpegコマンドを構築

    末尾に1フレーム複製して出力する（tpad=stop_mode=clone）。

    理由: overlay の enable 窓は閉区間 between(t, start, start+dur) で、
    覆うフレーム数は floor(fps*dur)+1。一方このクリップは
    _morph_frame_count = ceil(fps*dur) フレームしか無いため、
    fps*dur が整数になる通常ケース（time(1.0) 等）でちょうど1フレーム
    足りず、その1枚だけ第2入力がEOF → eof_action=pass でベースの
    背景色（黒）が素通しして「モーフ末尾の黒落ち」になっていた。
    複製フレームは到達先画像そのものなので、伸ばしても見た目は変わらない。
    窓の外に出る余剰フレームは enable で表示されない。
    """
    n_frames = _morph_frame_count(fps, duration)
    return ["ffmpeg", "-y", "-framerate", str(fps),
            "-i", frame_pattern,
            "-vf", "tpad=stop_mode=clone:stop=1",
            "-c:v", "ffv1", "-level", "3",
            # morphのPNG連番(RGBA)をそのまま格納する。yuva444pだとここで
            # RGBA→YUV変換が入り、PILの描画結果と往復一致しなくなる
            # （理由と実測値は state.py の _BAKE_PIX_FMT）
            "-pix_fmt", _BAKE_PIX_FMT,
            "-frames:v", str(n_frames + 1), cache_path]


def _require_morph_duration(bakeable_ops, dur, source):
    """morph_toを含むObjectのduration未設定を明示エラーにする

    画像 + duration未設定のまま進むと int(fps * None) の TypeError で
    原因が分かりにくいため、ここで日本語エラーを投げる。
    """
    has_term = any(t == "effect" and op.name in _TERMINAL_FRAME_EFFECTS
                   for t, op in bakeable_ops)
    if has_term and not dur:   # None も 0 も不可（0はフレーム0枚になる）
        raise ValueError(
            f"morph_to/explode_to/assemble_from を含むObject ('{source}') には"
            f"表示時間の指定が必要です。obj.time(秒数) で duration を設定してください。")


def _checkpoint_bake_duration(project, obj, original_source):
    """チェックポイントのベイク尺を決定する。

    speed/reverse/freeze_frame 等の live 時間系Effectが残るObjectは、
    表示尺(duration)ではなくソース基準の実長(trimのみ反映)でベイクする。
    表示尺でベイクすると、後段の時間系Effect適用でソース素材が
    不足/過剰になる（例: speed(2)で表示尺5s → 元素材10sが必要）ため。
    """
    is_video = _detect_media_type(original_source) in ("video",)
    has_time_live = any(
        getattr(e, "name", None) in _TIME_LIVE_EFFECTS for e in obj.effects)
    if is_video and has_time_live:
        info = project._probe_media(original_source)
        base = info.get("duration") if info else None
        if base is None:
            base = getattr(obj, "_resolved_length", None) or obj.duration
        if base:
            cur = base
            for e in obj.effects:
                if e.name == "trim" and e.params.get("duration") is not None:
                    cur = _builtins.min(cur, e.params["duration"])
            return cur
    dur = obj.duration
    # video + duration未指定 → obj.length() で補完
    if dur is None and is_video:
        dur = obj.length()
    # 尺0はベイク尺として成立しない（clip((t-start)/0,…) がゼロ除算になる）。
    # ただし None はここで埋めてはいけない: 呼び出し側の
    # _require_morph_duration が「morph系に time() が無い」を None で判定して
    # おり、fallback を入れるとその検査をすり抜ける。救うのは 0 だけ。
    return dur if dur != 0 else project._resolve_obj_duration(obj)


def _plan_object_checkpoints(project, obj):
    """1つのObjectのチェックポイント計画を構築する（純粋計画・実行しない）。

    実レンダ(_process_checkpoints)と dry_run(_collect_checkpoint_cmds)の
    両方がこの計画を通ることで、キャッシュパス・コマンド列・Object最終状態の
    規則を一本化する（片方だけ直して両経路のパスがずれる事故の根絶）。

    戻り値: 対象外（text/bakeable無し/全off/保存点無し）なら None。
    それ以外は dict:
      steps: 実行順の計画ステップ列。各ステップは dict で
        kind: "checkpoint"|"pre_bake"|"frame_extract"|"morph"|"particle"
        sp_idx: 属する保存点の bakeable_ops インデックス（resumeスキップ用）
        path: 生成先キャッシュパス
        build_cmd: () -> ffmpegコマンド列。実レンダは直前ステップの実体化後に
            呼ぶ（動画チェックポイントは入力の実寸を probe するため、
            生成順に遅延評価しないと中間物の寸法が反映されない）。
            morph/particle はプレースホルダのフレームパターン版
            （実レンダは _execute_frames_step が一時dirで組み直す）。
        label: 進捗表示の見出し
        policy: 保存点opのpolicy（pre_bake/frame_extractは存在チェックのみ
            なので持たない＝従来挙動）
        morph/particle 追加キー: op / src（フレーム生成の入力画像）/ dur / fps
      final: _apply_checkpoint_final_state でObjectへ適用する最終状態
      resume_args: _find_resume_point へそのまま渡す引数タプル
    """
    if obj.media_type == "text":
        return None  # テキスト系は実体ファイルを持たずベイク対象外
    ops = _build_unified_ops(obj)
    bakeable_ops, live_ops = _split_ops(ops)
    if not bakeable_ops:
        return None
    # 全opがpolicy="off"ならスキップ
    if all(getattr(op, 'policy', 'auto') == "off" for _, op in bakeable_ops):
        return None

    _validate_morph_position(bakeable_ops)

    save_points = _compute_save_points(bakeable_ops)
    if not save_points:
        return None

    original_source = obj.source
    dur = _checkpoint_bake_duration(project, obj, original_source)
    fps = project.fps
    _require_morph_duration(bakeable_ops, dur, original_source)

    is_video = _detect_media_type(original_source) in ("video",)
    current_source = original_source
    current_media_type = obj.media_type
    steps = []

    def _cp_builder(src, mt, transforms, effects, path, cp_dur):
        """チェックポイントコマンドの遅延ビルダー（現在値をクロージャへ固定）"""
        if cp_dur is None:
            return lambda: _build_checkpoint_image_cmd(
                src, transforms, path)
        return lambda: _build_checkpoint_video_cmd(
            src, mt, transforms, effects, path, cp_dur, fps)

    def _plan_pre_bake(pre_ops, pre_segment, sp_idx, label):
        """morph/explode直前の未ベイクopsを中間チェックポイントへ計画する
        （破棄するとmorph前のresize等が黙って消えるため先にベイクする）"""
        nonlocal current_source, current_media_type
        pre_has_effects = any(t == "effect" for t, _ in pre_segment)
        pre_dur = dur if (pre_has_effects or is_video) else None
        pre_fps = fps if pre_dur is not None else None
        pre_path = _checkpoint_cache_path(
            original_source, pre_segment, pre_dur, pre_fps)
        steps.append({
            "kind": "pre_bake", "sp_idx": sp_idx, "path": pre_path,
            "label": label,
            "build_cmd": _cp_builder(
                current_source, current_media_type,
                [o for t, o in pre_ops if t == "transform"],
                [o for t, o in pre_ops if t == "effect"],
                pre_path, pre_dur),
        })
        current_source = pre_path
        current_media_type = _detect_media_type(pre_path)

    def _plan_frame_extract(src, sp_idx, label):
        """morph/粒子生成（PIL）は画像のみ対応: 動画ソース（前ベイクの
        .mkv等）は最終フレームをRGBA PNGへ抽出してから入力にする"""
        frame_path = _morph_input_frame_path(src)
        steps.append({
            "kind": "frame_extract", "sp_idx": sp_idx, "path": frame_path,
            "label": label,
            "build_cmd": (lambda s=src, fp=frame_path:
                          _build_morph_frame_extract_cmd(s, fp)),
        })
        return frame_path

    sorted_sps = sorted(save_points)
    prev_sp_idx = None
    for sp_idx in sorted_sps:
        segment_ops = bakeable_ops[:sp_idx + 1]
        has_effects = any(t == "effect" for t, _ in segment_ops)
        cp_dur = dur if (has_effects or is_video) else None
        cp_fps = fps if cp_dur is not None else None
        sp_typ, sp_op = bakeable_ops[sp_idx]
        policy = getattr(sp_op, 'policy', 'auto')
        seg_start = 0 if prev_sp_idx is None else prev_sp_idx + 1

        # morph_to 分岐
        if (sp_typ == "effect" and sp_op.name == "morph_to"
                and hasattr(sp_op, '_morph_target')):
            pre_ops = bakeable_ops[seg_start:sp_idx]
            if pre_ops:
                _plan_pre_bake(pre_ops, bakeable_ops[:sp_idx], sp_idx,
                               "チェックポイント保存 (morph前処理)")
            if _detect_media_type(current_source) == "video":
                current_source = _plan_frame_extract(
                    current_source, sp_idx, "モーフ入力フレーム抽出")
                current_media_type = "image"
            morph_path = _morph_cache_path(current_source, sp_op, dur, fps)
            steps.append({
                "kind": "morph", "sp_idx": sp_idx, "path": morph_path,
                "label": "モーフキャッシュ保存", "policy": policy,
                "op": sp_op, "src": current_source, "dur": dur, "fps": fps,
                "build_cmd": (lambda mp=morph_path:
                              _build_morph_webm_cmd(
                                  os.path.join("__morph_frames__",
                                               "frame_%05d.png"),
                                  mp, dur, fps)),
            })
            current_source = morph_path
            current_media_type = "video"
        # 粒子Effect分岐
        elif sp_typ == "effect" and sp_op.name in ("explode_to",
                                                   "assemble_from"):
            if sp_op.name == "explode_to":
                # explode: 直前の未ベイクopsを先にベイク（morphと同じ経路）
                pre_ops = bakeable_ops[seg_start:sp_idx]
                if pre_ops:
                    _plan_pre_bake(pre_ops, bakeable_ops[:sp_idx], sp_idx,
                                   "チェックポイント保存 (explode前処理)")
                img_path = current_source
            else:  # assemble_from: 集合元画像を入力にする
                img_path = sp_op._assemble_source.source
            if _detect_media_type(img_path) == "video":
                img_path = _plan_frame_extract(
                    img_path, sp_idx, "粒子入力フレーム抽出")
            part_path = _particle_cache_path(img_path, sp_op, dur, fps)
            steps.append({
                "kind": "particle", "sp_idx": sp_idx, "path": part_path,
                "label": "粒子キャッシュ保存", "policy": policy,
                "op": sp_op, "src": img_path, "dur": dur, "fps": fps,
                "build_cmd": (lambda pp=part_path:
                              _build_morph_webm_cmd(
                                  os.path.join("__particle_frames__",
                                               "frame_%05d.png"),
                                  pp, dur, fps)),
            })
            current_source = part_path
            current_media_type = "video"
        else:
            cache_path = _checkpoint_cache_path(
                original_source, segment_ops, cp_dur, cp_fps)
            local_ops = bakeable_ops[seg_start:sp_idx + 1]
            steps.append({
                "kind": "checkpoint", "sp_idx": sp_idx, "path": cache_path,
                "label": "チェックポイント保存", "policy": policy,
                "build_cmd": _cp_builder(
                    current_source, current_media_type,
                    [o for t, o in local_ops if t == "transform"],
                    [o for t, o in local_ops if t == "effect"],
                    cache_path, cp_dur),
            })
            current_source = cache_path
            current_media_type = _detect_media_type(cache_path)
        prev_sp_idx = sp_idx

    remaining = bakeable_ops[sorted_sps[-1] + 1:]
    final = {
        "source": current_source,
        # 音声の取り出し元（中間物は -an の映像専用なので常に元素材）
        "audio_source": original_source,
        "media_type": current_media_type,
        "transforms": [op for t, op in remaining if t == "transform"],
        "effects": ([op for t, op in remaining if t == "effect"]
                    + [op for t, op in live_ops if t == "effect"]),
        "dur": dur,
        "live_effects": [op for t, op in live_ops if t == "effect"],
    }
    return {
        "steps": steps,
        "final": final,
        "resume_args": (original_source, bakeable_ops, dur, fps,
                        save_points),
    }


def _apply_checkpoint_final_state(project, obj, final):
    """計画の最終状態をObjectへ適用する（source差し替え・残余ops再設定）。

    差し替え前に解決した実長を保持（差し替え後・未生成予定パスへの
    probe依存を排除し、dry_runと実レンダで式を一致させる）。
    live 時間系Effect（speed/freeze_frame）が残る場合は表示尺に換算する。

    音声も同じ理由で**差し替え前に確定**させる。チェックポイントは映像専用
    （-an）なので、差し替え後のパスを probe すると未生成(cold)＝音声なし /
    生成済み(warm)＝音声ありとキャッシュの有無で結論が変わる。
    元素材を probe して _has_audio を固定し、音声入力の参照先
    （_audio_source）を元素材へ張る。probe 不能時に False へ倒す安全側の
    既定は has_audio 側に残るが、その結論もキャッシュの有無では変わらない。
    """
    if final["dur"]:
        obj._resolved_length = _apply_time_effects_to_duration(
            final["dur"], final["live_effects"])
    audio_source = final["audio_source"]
    if audio_source != final["source"]:
        if obj._has_audio is None:
            info = project._probe_media(audio_source)
            obj._has_audio = bool(info.get("has_audio")) if info else False
        obj._audio_source = audio_source
    obj.source = final["source"]
    obj.media_type = final["media_type"]
    obj.transforms = list(final["transforms"])
    obj.effects = list(final["effects"])


def _process_checkpoints(project, obj):
    """1つのObjectのチェックポイント処理（実レンダ）。

    計画(_plan_object_checkpoints)を立て、復元点より後のステップだけを
    実行してからObjectへ最終状態を適用する。dry_run側
    (_collect_checkpoint_cmds)と同一の計画を通るため、キャッシュパスが
    経路間でずれることはない。
    """
    plan = project._plan_object_checkpoints(obj)
    if plan is None:
        return
    # 統計は実行の前に記録する（実行後だと自分で作った物をヒットと数える）
    for step in plan["steps"]:
        project._note_planned_artifact(step["path"])
    # 復元点チェック（bakeable_opsベース）: 復元点以前のステップは既存
    # キャッシュを使うため実行しない（後続が参照する入力パスは計画が
    # 同一規則で解決済みなので、実行の有無でコマンドは変わらない）
    resume_idx, _resume_path = _find_resume_point(*plan["resume_args"])
    for step in plan["steps"]:
        if resume_idx is not None and step["sp_idx"] <= resume_idx:
            continue
        project._execute_checkpoint_step(step, obj)
    project._apply_checkpoint_final_state(obj, plan["final"])


def _step_context(step, obj):
    """チェックポイント生成失敗時に「どのオブジェクト起因か」を示す文脈。"""
    who = "" if obj is None else             f"（素材 {obj.source} / start={obj.start_time}s）"
    return f"{step['label']}{who} → {step['path']}"


def _find_resume_point(original_source, ops, duration, fps, save_points):
    """force地点より左のauto保存点のみresume候補"""
    # 最左force位置
    first_force = None
    for i, (typ, op) in enumerate(ops):
        if getattr(op, 'policy', 'auto') == "force" and _is_bakeable(typ, op):
            first_force = i
            break
    boundary = (first_force - 1) if first_force is not None else len(ops) - 1

    # boundary以下のauto保存点を右から探索
    candidates = sorted([i for i in save_points
                        if i <= boundary and getattr(ops[i][1], 'policy', 'auto') == "auto"],
                       reverse=True)

    is_video = _detect_media_type(original_source) in ("video",)
    for idx in candidates:
        segment_ops = ops[:idx + 1]
        # 保存側と同じくセグメント（保存点までのprefix）単位でhas_effectsを計算
        # （全ops基準だとキャッシュキーが食い違い、永久にキャッシュミスする）
        has_effects = any(t == "effect" for t, _ in segment_ops)
        cp_dur = duration if (has_effects or is_video) else None
        cp_fps = fps if cp_dur is not None else None
        path = _checkpoint_cache_path(original_source, segment_ops, cp_dur, cp_fps)
        if os.path.exists(path):
            return idx, path
    return None, None


def _ensure_checkpoints(project):
    """bakeable opsを持つ全Objectのチェックポイント処理（対象判定は計画側）"""
    for obj in project.objects:
        if isinstance(obj, Object):
            project._process_checkpoints(obj)


def _collect_checkpoint_cmds(project):
    """dry_run用: 全チェックポイントコマンドを収集（計画は実レンダと共通）。

    morph/particle はフレーム生成を伴わないため、フレームパターンが
    プレースホルダのコマンドを収集する。収集後は実レンダと同じ最終状態を
    Objectへ適用する（sourceの予定パス差し替え・実長保持等）。
    """
    cmds = {}
    for obj in project.objects:
        if not isinstance(obj, Object):
            continue
        plan = project._plan_object_checkpoints(obj)
        if plan is None:
            continue
        for step in plan["steps"]:
            cmds[step["path"]] = step["build_cmd"]()
        project._apply_checkpoint_final_state(obj, plan["final"])
    return cmds
