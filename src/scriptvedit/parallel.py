# -*- coding: utf-8 -*-
"""時間分割並列レンダ（render(parallel=N)）。

原理: 最終レンダのフィルタ式は全て絶対タイムライン時刻 t 基準
（tpad/enable='between(t,..)'/u=clip((t-start)/dur,..)/drawtext）なので、
チャンク側で「背景のPTSを+t0し、全フィルタ評価後に-t0で戻す」だけで
各チャンクのフレームは全編レンダと同一になる（フィルタ文字列も同一）。
各オブジェクトはtpad整列直後に trim=start=t0-margin で頭を破棄し、
チャンク外フレームが重いフィルタへ流れないようにする（＝ここが高速化の本体）。
音声は loudnorm/duck_under が全尺依存のため分割せず、全編1本を並列レンダして
concat(-c copy)結果へmuxする。チャプター(FFMETADATA)もmux時に付与する。

audit.py と同じ方式で、Project インスタンスを第1引数に受ける自由関数を
提供する（Project は import しない＝循環 import を起こさない）。
"""

import os
import math as _math
import builtins as _builtins
import time as _time
import shutil as _shutil
import concurrent.futures as _futures

from scriptvedit.chapters import _chapters_metadata_path, _write_chapters_metadata
from scriptvedit.ffmpeg import _atomic_write_text, _run_ffmpeg, _unique_tmp_path


def _parallel_chunk_count(project, parallel, output_path):
    """時間分割並列レンダの実チャンク数を決定する（適用不可なら1=従来経路）。

    フォールバック条件は例外にせず通知して従来レンダを行う
    （parallelは高速化ヒントであり、結果の意味を変えないため）。
    """
    if parallel is None or parallel <= 1:
        return 1
    fmt = project._resolve_output_format(output_path)
    if fmt["kind"] != "h264" or fmt["alpha"]:
        print(f"parallel={parallel}: この出力形式({fmt['kind']}"
              f"{'/alpha' if fmt['alpha'] else ''})は時間分割並列に未対応のため"
              f"従来レンダで実行します")
        return 1
    if project._render_window is not None:
        print(f"parallel={parallel}: start/end 部分レンダとは併用できないため"
              f"従来レンダで実行します")
        return 1
    n_total, bounds = _parallel_chunk_bounds(
        project.duration, project.fps, parallel)
    if len(bounds) - 1 <= 1:
        print(f"parallel={parallel}: 総フレーム数({n_total})が少なく分割の"
              f"意味がないため従来レンダで実行します")
        return 1
    return len(bounds) - 1


def _parallel_chunk_bounds(duration, fps, n):
    """総尺をフレーム境界でn分割する。戻り値: (総フレーム数, 境界フレーム番号リスト)

    境界は k/fps に正確に一致させる（チャンク開始時刻がフレーム格子に
    載っていないと、concat結合時にフレームの重複/欠落が起きるため）。
    総フレーム数は「pts < duration のフレーム数」= ceil(duration*fps)
    （浮動小数の丸め誤差はepsで吸収）。nが総フレーム数を超える場合は
    縮退させ、全チャンクが1フレーム以上になることを保証する。
    """
    n_total = _builtins.max(1, int(_math.ceil(duration * fps - 1e-6)))
    n_eff = _builtins.max(1, _builtins.min(int(n), n_total))
    # round(i*n_total/n_eff): 刻み幅>=1 なので単調増加が保証される
    return n_total, [_builtins.round(i * n_total / n_eff)
                     for i in range(n_eff + 1)]


def _render_parallel(project, output_path, n, timeout):
    """映像をNチャンクへ時間分割して並列レンダし、concatで結合する。

    戻り値: 実際に使ったチャンク数（統計行の表示用）。
    出力の確定は従来経路と同じく一時パス→os.replaceの原子的作法に従う。
    """
    import tempfile
    fmt = project._resolve_output_format(output_path)
    final_path = fmt["output_path"]
    fps = project.fps
    n_total, bounds = _parallel_chunk_bounds(project.duration, fps, n)
    n_eff = len(bounds) - 1
    renderable = [o for o in project.objects if isinstance(o, Object)]
    use_audio = any(o.has_audio for o in renderable)
    # x264スレッドの過剰予約を防ぐ: 全チャンク合計で概ねCPU数に収める
    # （フィルタ評価は各プロセスでほぼ単一スレッドのため、そちらが並列化の本体）
    cpu = os.cpu_count() or 4
    threads = _builtins.max(1, (cpu + n_eff - 1) // n_eff)
    work_dir = tempfile.mkdtemp(prefix="svpar_")
    try:
        chunk_paths = []
        chunk_cmds = []
        for i in range(n_eff):
            cpath = os.path.join(work_dir, f"chunk_{i:03d}.mp4")
            chunk_paths.append(cpath)
            chunk_cmds.append(_build_chunk_ffmpeg_cmd(
                project, cpath, bounds[i], bounds[i + 1], threads))
        audio_path = None
        audio_cmd = None
        if use_audio:
            audio_path = os.path.join(work_dir, "audio.m4a")
            audio_cmd = _build_audio_leg_cmd(project, audio_path)
        print(f"時間分割並列レンダ: {n_eff}チャンク "
              f"(総{n_total}フレーム / {project.duration}s / "
              f"チャンク毎 -threads {threads}"
              f"{' / 音声は全編1本を並行レンダ' if use_audio else ''})")
        for i in range(n_eff):
            print(f"  chunk{i}: フレーム[{bounds[i]}, {bounds[i + 1]}) "
                  f"t=[{bounds[i] / fps:.3f}s, {bounds[i + 1] / fps:.3f}s)")

        times = {}
        errors = []

        def _run_job(name, cmd):
            jt0 = _time.perf_counter()
            _run_ffmpeg(cmd, timeout=timeout)
            times[name] = _time.perf_counter() - jt0

        workers = n_eff + (1 if audio_cmd is not None else 0)
        with _futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {}
            if audio_cmd is not None:
                futs[ex.submit(_run_job, "audio", audio_cmd)] = "audio"
            for i, ccmd in enumerate(chunk_cmds):
                futs[ex.submit(_run_job, f"chunk{i}", ccmd)] = f"chunk{i}"
            for fut in _futures.as_completed(futs):
                try:
                    fut.result()
                except Exception as e:  # 全ジョブの完了を待ってから報告
                    errors.append((futs[fut], e))
        if errors:
            name, e = errors[0]
            raise RuntimeError(
                f"並列レンダの {name} が失敗しました: {e}") from e
        for name in sorted(times):
            print(f"  {name}: {times[name]:.2f}s")

        # concatリスト（クォート内の ' はエスケープ。区切りは/でOS差を吸収）
        list_path = os.path.join(work_dir, "concat.txt")
        _atomic_write_text(list_path, "".join(
            "file '{}'\n".format(p.replace("\\", "/").replace("'", "'\\''"))
            for p in chunk_paths))
        meta_path = None
        if project._markers:
            meta_path = _chapters_metadata_path(project)
            _write_chapters_metadata(project, meta_path)
        mux_cmd = _build_concat_mux_cmd(
            list_path, audio_path, meta_path, final_path)
        tmp_path = _unique_tmp_path(final_path)
        run_cmd = list(mux_cmd)
        run_cmd[-1] = tmp_path
        try:
            _run_ffmpeg(run_cmd, timeout=timeout)
            os.replace(tmp_path, final_path)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    finally:
        _shutil.rmtree(work_dir, ignore_errors=True)
    return n_eff


def _build_chunk_ffmpeg_cmd(project, chunk_path, k0, k1, threads):
    """フレーム区間[k0, k1)の映像チャンクをレンダするffmpegコマンドを構築する。

    フィルタ式は全編レンダと同一（絶対時刻t基準）。背景PTSの+t0シフトと
    末尾の-t0戻し、各オブジェクトのhead_trimだけがチャンク固有の差分。
    チャンクは常に音声なしのh264（音声は_build_audio_leg_cmdで別レンダ）。
    """
    fps = project.fps
    nf = k1 - k0
    t0 = k0 / fps
    w_end = k1 / fps
    dur = nf / fps
    inputs = ["-f", "lavfi", "-i",
              f"color=c={project.background_color}"
              f":s={project.width}x{project.height}:d={dur}:r={fps}"]

    renderable = [o for o in project.objects if isinstance(o, Object)]
    sorted_objects = sorted(renderable, key=lambda o: o.priority)
    chunk_objs = []
    for o in sorted_objects:
        if not o.has_video:
            continue
        if o.start_time >= w_end:
            continue  # チャンク終了後に始まる → 不可視
        if o.duration is not None and o.start_time + o.duration <= t0:
            continue  # チャンク開始前に終わる（duration未確定は安全側で残す）
        chunk_objs.append(o)

    filter_parts = []
    input_map = {}
    for i, obj in enumerate(chunk_objs):
        input_map[id(obj)] = i + 1
        inputs.extend(_build_input_args(obj, fps))

    current_base = "[0:v]"
    head_trim = None
    if k0 > 0:
        # 背景PTSを絶対時刻へシフト → 全t依存式が全編レンダと同一になる
        filter_parts.append(f"[0:v]setpts=PTS+{t0!r}/TB[chbase]")
        current_base = "[chbase]"
        # 頭破棄はフレーム境界の判定誤差を避けるため2フレームぶん手前から残す
        # （overlayのframesyncは「主入力pts以下の最新フレーム」を選ぶので、
        #   余分に残った先行フレームは正しさに影響しない）
        head_trim = _builtins.max(0.0, t0 - 2.0 / fps)
    for obj in chunk_objs:
        input_idx = input_map[id(obj)]
        dur_o = project._resolve_obj_duration(obj)
        parts, out_label = _build_video_overlay_parts(
            obj, input_idx, current_base, dur_o, head_trim=head_trim)
        filter_parts.extend(parts)
        current_base = out_label
    video_map = current_base
    if k0 > 0:
        # 出力PTSをチャンク先頭=0へ戻す（concatで連続再生になる）
        filter_parts.append(f"{video_map}setpts=PTS-{t0!r}/TB[chout]")
        video_map = "[chout]"
    if getattr(project, "_draft", False):
        # ドラフト縮小は従来経路（_build_ffmpeg_cmd）と同一の式（定数で強制）
        filter_parts.append(f"{video_map}{_DRAFT_SCALE_FILTER}[chdraft]")
        video_map = "[chdraft]"

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    cmd.extend(inputs)
    if filter_parts:
        cmd.extend(["-filter_complex", ";".join(filter_parts)])
        # 生入力参照はブラケットを外す（逐次レンダと共通ヘルパ）
        video_map = _unwrap_raw_stream_ref(video_map, "v")
        cmd.extend(["-map", video_map])
    else:
        cmd.extend(["-map", "0:v"])
    fmt_chunk = {"kind": "h264", "alpha": False, "has_audio": False,
                 "output_path": chunk_path}
    cmd.extend(project._encode_args(fmt_chunk, False))
    cmd.extend(["-threads", str(threads)])
    # -t に加えて -frames:v で正確なフレーム数を保証（浮動小数の防波堤）
    cmd.extend(["-t", str(dur), "-frames:v", str(nf), chunk_path])
    return cmd


def _build_audio_leg_cmd(project, audio_path):
    """音声だけを全編1本でレンダするコマンドを構築する（並列レンダ用）。

    loudnorm/duck_under等は全尺依存のためチャンク分割できない。
    既存の音声チェーン（_build_ffmpeg_cmd）を_audio_only_renderフラグで
    映像枝なしに切り替えて再利用し、逐次レンダとの乖離を防ぐ。
    adelay等は絶対時刻基準なので出力音声は逐次レンダと同一になる。
    """
    saved_objects = project.objects
    project._audio_only_render = True
    try:
        project.objects = [o for o in saved_objects
                           if isinstance(o, Object) and o.has_audio]
        return project._build_ffmpeg_cmd(audio_path)
    finally:
        project.objects = saved_objects
        project._audio_only_render = False


def _build_concat_mux_cmd(list_path, audio_path, meta_path, out_path):
    """チャンクconcat + 音声mux + チャプター付与の最終コマンドを構築する。

    映像・音声とも再エンコードなし（-c copy）。各チャンクはIDRフレーム
    始まりのMP4なのでconcat demuxerで無劣化結合できる。
    """
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "concat", "-safe", "0", "-i", list_path]
    next_idx = 1
    a_idx = None
    m_idx = None
    if audio_path is not None:
        cmd.extend(["-i", audio_path])
        a_idx = next_idx
        next_idx += 1
    if meta_path is not None:
        cmd.extend(["-f", "ffmetadata", "-i", meta_path])
        m_idx = next_idx
    cmd.extend(["-map", "0:v"])
    if a_idx is not None:
        cmd.extend(["-map", f"{a_idx}:a"])
    cmd.extend(["-c", "copy"])
    if m_idx is not None:
        cmd.extend(["-map_metadata", str(m_idx),
                    "-map_chapters", str(m_idx)])
    cmd.append(out_path)
    return cmd


# --- 遅延解決の相互参照（関数本体からのみ使用: 循環importを避けるため末尾で束縛）---
from scriptvedit.filters.video import _build_input_args, _build_video_overlay_parts
from scriptvedit.objects import Object
from scriptvedit.project import _DRAFT_SCALE_FILTER, _unwrap_raw_stream_ref
