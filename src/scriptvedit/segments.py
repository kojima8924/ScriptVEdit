# -*- coding: utf-8 -*-
"""書き出し済みの映像区間を結合して本レンダにする（render(video_segments=[...])）。

長い動画を「映像は区間ごとに別の Project で書き出し、音声はまとめて1本」で作るときの
最終段。区間（h264 の .mp4。音声つきでもよいが区間の音声は使わない）を concat demuxer で
``-c copy`` し、音声は Project の音声グラフを全編1本でレンダして mux する。チャプター（marker）も
mux 時に付与する。並列レンダ（parallel.py）の「チャンク + 音声レグ + mux」と同じ
3段で、音声レグ・mux の組み立て（_build_audio_leg_cmd / _build_concat_mux_cmd）は
parallel.py のものをそのまま使う（segments → parallel の一方向の import。逆向きにしない）。

契約:
  - 出力は h264 の .mp4 だけ（他は ValueError）。start / end・draft・alpha・parallel とは
    併用できない（区間はもう符号化されている）。
  - Project に映像を持つ Object（image / video / text / web / 生成物）があれば ValueError。
    音声 Object と marker だけの Project で使う。
  - 各区間は実在する素材扱い（dry_run でも無ければ FileNotFoundError）。
  - 実レンダでは各区間を probe し、寸法・pix_fmt・codec が区間どうしで一致すること、
    fps が Project の fps と一致すること、コマ数の合計が「1本レンダが -t 総尺 で書き出す
    枚数」（µs に切り捨てた総尺 × fps の四捨五入。parallel._parallel_chunk_bounds と同じ
    数え方＝filters.video._output_frame_count。総尺は Project の尺＝音声側の尺）
    であることを確かめる（違えば ValueError）。dry_run は probe しない。
  - 音声は parallel.py と同じ _build_audio_leg_cmd（_build_ffmpeg_cmd の音声専用経路）で、
    normalize_audio(mode="linear") の増幅も _ensure_linear_gain 経由の同じ値。
  - 出力は _unique_tmp_path へ書いて os.replace（render() の単一出力と同じ作法）。

区間の取り込み（どちらも parallel.py のチャンクの結合と共通）:
  - concat リストの各区間に ``duration``（probe したコマ数 / fps）を書く
    （parallel._write_concat_list）。書かないと concat demuxer は区間の mp4 の編集リストの
    尺で次の区間の開始を決め、movie timescale 1000 でミリ秒へ切り捨てられた区間の後ろが
    0.3ms ずつ前へずれた。
  - mux は ``-copyts``（parallel._build_concat_mux_cmd）。区間に音声があると、AAC の
    priming で入力の開始時刻が -0.021333 秒になり、ffmpeg が入力全体を +0.021 秒ずらして
    映像だけが遅れた。区間の音声は -map しないので結果に入らない。
  - 出力の movie timescale を映像の時間単位にそろえる（-movie_timescale。
    parallel._mux_movie_timescale）。既定の 1000 だと結合した mp4 の映像の尺が
    ミリ秒へ切り捨てられ、この出力を次の結合の区間にしたときに尺が食い違う。

音声レグのキャッシュ:
  音声レグは ``__cache__/artifacts/audio_leg/<鍵>.m4a`` に原子的に残し
  （_run_ffmpeg_to_cache）、命中すれば音声のレンダを省く（映像の区間だけを直したときに
  毎回全編の音声をレンダし直さない）。再生成に全編のレンダが要るので命中ガードを置く
  （CLAUDE.md §5「キャッシュ書き込みは原子的に」。0 バイト・音声ストリームの無い残骸は
  命中扱いにしない）。鍵は音声レグのコマンドの入力を _src_signature にしたもの（render_key と
  同じ作法）で、増幅量は dry_run と同じ表記（<MEASURED_GAIN>dB）のまま入れ、代わりに
  normalize_audio(mode="linear") の target / true_peak / limiter を足す（増幅量はこれらと
  測定値で決まり、測定値は同じ音声グラフ・同じ素材なら同じ）。だから命中したときは
  測定パスも走らせない。

dry_run の戻り値（CLAUDE.md §3「dry_run はキャッシュ状態に依存しない」）:
  ``{"main": <mux コマンド>, "cache": {<音声レグのパス>: <音声コマンド>,
  <ラウドネス JSON>: <測定コマンド>}}``。音声レグ・測定 JSON の有無は見ない（キャッシュが
  空なら何を実行するか）。concat リストは作業ディレクトリ ``__cache__/work/segments_<鍵>/`` に
  置く。鍵は区間の署名（_src_signature）と音声レグの鍵から決まる決定的な名前で、
  出力パスには依らない（render_key が出力パスで変わらない）。作業ディレクトリは mux の
  後に消す（音声レグは消さない）。

parallel.py / loudness.py と同じく「project を第1引数に取る自由関数」として
project.py から切り出している（Project は import しない＝循環 import を増やさない）。
"""

import json
import os
import shutil as _shutil
import subprocess

from scriptvedit.cache import _sig_key, _src_signature
from scriptvedit.chapters import _chapters_metadata_path, _write_chapters_metadata
from scriptvedit.ffmpeg import _run_ffmpeg, _run_ffmpeg_to_cache, _unique_tmp_path
from scriptvedit.loudness import _collect_loudness_cmds, _ensure_linear_gain, _linear_mode
from scriptvedit.objects import Object
from scriptvedit.parallel import _build_audio_leg_cmd, _build_concat_mux_cmd, _parallel_chunk_bounds, _write_concat_list
from scriptvedit.state import _ARTIFACT_DIR, _CACHE_DIR, _ENGINE_VER

# 作業ディレクトリ（concat リスト）の置き場。__cache__ 配下なのは
# _src_signature がパス署名になり、dry_run の文字列が cwd・出力パスに依らないため
_SEGMENTS_WORK_DIR = os.path.join(_CACHE_DIR, "work")

# 音声レグのキャッシュの置き場（内容鍵の名前。mux の後も残す）
_AUDIO_LEG_DIR = os.path.join(_ARTIFACT_DIR, "audio_leg")

# 音声レグの鍵を作るコマンドの出力（鍵には入れない。実行もしない）
_AUDIO_LEG_KEY_OUTPUT = "<audio_leg>.m4a"


def _check_segments_request(project, output_path, segments, *, start, end,
                            draft, alpha, parallel):
    """render(video_segments=) の引数を、レイヤーを実行する前に検査する。

    出力形式・併用できないオプション・区間の実在はここで分かる
    （映像 Object の有無はレイヤー実行後の _check_segments_objects）。
    戻り値: 正規化した区間パスのリスト。
    """
    if start is not None or end is not None:
        raise ValueError(
            "render(video_segments=): start / end の部分レンダとは併用できません"
            "（区間はもう書き出されています）")
    if draft or alpha:
        raise ValueError(
            "render(video_segments=): draft / alpha とは併用できません"
            "（区間はもう符号化されています）")
    if parallel is not None and parallel != 1:
        raise ValueError(
            "render(video_segments=): parallel とは併用できません"
            "（映像は結合するだけで再レンダしません）")
    fmt = project._resolve_output_format(output_path)
    if fmt["kind"] != "h264" or os.path.splitext(output_path)[1].lower() != ".mp4":
        raise ValueError(
            f"render(video_segments=): 出力は h264 の .mp4 だけです: {output_path}")
    if isinstance(segments, (str, bytes, os.PathLike)) or not hasattr(segments, "__iter__"):
        raise ValueError(
            "render(video_segments=): 区間は mp4 のパスのリストで指定してください")
    paths = [os.fsdecode(s) for s in segments]
    if not paths:
        raise ValueError("render(video_segments=): 区間が空です")
    for p in paths:
        if not os.path.isfile(p):
            raise FileNotFoundError(
                f"render(video_segments=): 区間の動画が見つかりません: {p}")
    return paths


def _check_segments_objects(project):
    """映像を持つ Object があれば ValueError（区間が映像のすべてなので重ねられない）"""
    visual = [o for o in project.objects
              if isinstance(o, Object) and o.has_video]
    if visual:
        shown = ", ".join(str(o.source) for o in visual[:5])
        if len(visual) > 5:
            shown += f" ほか{len(visual) - 5}件"
        raise ValueError(
            f"render(video_segments=): 映像を持つ Object があります: {shown}。\n"
            f"映像は区間の mp4 がすべてなので、Project には音声 Object と marker だけを"
            f"置いてください（映像は区間を書き出す側の Project に置く）。")


def _segments_frame_total(project):
    """区間のコマ数の合計がこの値でなければならない（1本レンダが -t 総尺 で書き出す枚数。並列レンダと同じ数え方）"""
    return _parallel_chunk_bounds(project.duration, project.fps, 1)[0]


def _probe_segment(path):
    """区間の映像ストリームを ffprobe で読む（codec / 寸法 / pix_fmt / fps / コマ数）"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-count_packets", "-show_entries",
             "stream=codec_name,width,height,pix_fmt,r_frame_rate,nb_frames,nb_read_packets",
             "-of", "json", path],
            capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(
            f"render(video_segments=): 区間を probe できません: {path}: {e}") from e
    streams = []
    if result.returncode == 0:
        try:
            streams = json.loads(result.stdout).get("streams", [])
        except ValueError:
            streams = []
    if not streams:
        raise ValueError(
            f"render(video_segments=): 区間に映像ストリームがありません: {path}")
    s = streams[0]
    frames = s.get("nb_frames") or s.get("nb_read_packets")
    if not (isinstance(frames, str) and frames.isdigit()):
        raise ValueError(
            f"render(video_segments=): 区間のコマ数を読めません: {path}")
    num, _, den = str(s.get("r_frame_rate", "0/1")).partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return {"codec": s.get("codec_name"), "width": s.get("width"),
            "height": s.get("height"), "pix_fmt": s.get("pix_fmt"),
            "fps": fps, "frames": int(frames)}


def _validate_segments(project, paths):
    """実レンダ用: 区間どうしの形式一致・fps・コマ数の合計を確かめる（違えば ValueError）"""
    infos = [_probe_segment(p) for p in paths]
    first = infos[0]
    for p, info in zip(paths, infos):
        for key in ("codec", "width", "height", "pix_fmt"):
            if info[key] != first[key]:
                raise ValueError(
                    f"render(video_segments=): 区間の {key} が揃っていません: "
                    f"{paths[0]} は {first[key]}、{p} は {info[key]}。\n"
                    f"全部の区間を同じ設定（寸法・pix_fmt・codec）で書き出してください"
                    f"（-c copy で結合するため再エンコードしません）")
        if abs(info["fps"] - float(project.fps)) > 1e-3:
            raise ValueError(
                f"render(video_segments=): 区間 {p} の fps({info['fps']:g}) が "
                f"Project の fps({project.fps}) と違います")
    total = sum(info["frames"] for info in infos)
    expected = _segments_frame_total(project)
    if total != expected:
        detail = " + ".join(str(info["frames"]) for info in infos)
        raise ValueError(
            f"render(video_segments=): 区間のコマ数の合計が総尺と合いません: "
            f"{detail} = {total} コマ、総尺 {project.duration}s × {project.fps}fps = "
            f"{expected} コマ。\n区間の切れ目か、音声側の尺（総尺）を直してください")
    return infos


def _segments_has_audio(project):
    return any(isinstance(o, Object) and o.has_audio for o in project.objects)


def _audio_leg_key_cmd(project):
    """音声レグの鍵に使うコマンド（増幅量は dry_run と同じ表記。実行しない）。

    実レンダでは測定の前に組むので、増幅量を表記で組んでよい間だけ
    project._gain_placeholder_render を立てる（測定済みの値も一時的に外す）。
    """
    saved_gain = project._linear_gain_db
    project._linear_gain_db = None
    project._gain_placeholder_render = True
    try:
        return _build_audio_leg_cmd(project, _AUDIO_LEG_KEY_OUTPUT)
    finally:
        project._gain_placeholder_render = False
        project._linear_gain_db = saved_gain


def _audio_leg_key(project):
    """音声レグの内容鍵（16桁）。音声グラフ・素材の内容・linear の増幅の設定で決まる。

    入力は _src_signature（素材は内容指紋・__cache__ の生成物はパス署名。lavfi は除く）、
    出力は入れない（cache._render_cmds_signature / loudness._loudness_measure_path と同じ作法）。
    """
    cmd = [str(a) for a in _audio_leg_key_cmd(project)]
    sigs = ["audio_leg", f"ev={_ENGINE_VER}"]
    for i, arg in enumerate(cmd[:-1]):
        if (i >= 1 and cmd[i - 1] == "-i"
                and not (i >= 2 and cmd[i - 2] == "lavfi")):
            sigs.append(f"in:{_src_signature(arg)}")
        else:
            sigs.append(arg)
    if _linear_mode(project):
        # 増幅量はコマンドでは表記なので、増幅量を決める値（loudness._decide_linear_gain の
        # 引数のうち測定値以外）を足す。limiter=False では true_peak が増幅の上限を決めるが
        # コマンドには現れない（リミッターが無い）
        opts = project._loudnorm_options
        sigs.append(f"linear_gain=target:{project._loudnorm_target}"
                    f":true_peak:{opts['true_peak']}:limiter:{bool(opts['limiter'])}")
    return _sig_key(sigs)


def _audio_leg_path(project):
    return os.path.join(_AUDIO_LEG_DIR, f"{_audio_leg_key(project)}.m4a")


def _audio_leg_ok(path):
    """キャッシュの音声レグを使えるか（0 バイト・読めない・音声の無い残骸は使わない）"""
    try:
        if os.path.getsize(path) <= 0:
            return False
    except OSError:
        return False
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "audio"


def _segments_work_dir(project, paths, audio_path):
    """作業ディレクトリ（決定的な名前。区間の署名と音声レグの鍵から）"""
    sigs = ["segments", f"ev={_ENGINE_VER}"]
    sigs.extend(_src_signature(p) for p in paths)
    if audio_path is not None:
        sigs.append(os.path.basename(audio_path))
    return os.path.join(_SEGMENTS_WORK_DIR, f"segments_{_sig_key(sigs)}")


def _segments_plan(project, output_path, paths):
    """作業ディレクトリ・音声レグのパス・mux コマンドを決める（dry_run / 実レンダ共通）。

    音声レグのコマンドはここでは組まない（実レンダでは増幅量の測定の後に組む）。
    戻り値: (work_dir, list_path, audio_path or None, meta_path or None, mux_cmd)
    """
    audio_path = _audio_leg_path(project) if _segments_has_audio(project) else None
    work_dir = _segments_work_dir(project, paths, audio_path)
    list_path = os.path.join(work_dir, "concat.txt")
    meta_path = _chapters_metadata_path(project) if project._markers else None
    final_path = project._resolve_output_format(output_path)["output_path"]
    mux_cmd = _build_concat_mux_cmd(list_path, audio_path, meta_path, final_path,
                                    project.fps)
    return work_dir, list_path, audio_path, meta_path, mux_cmd


def _collect_segments_cmds(project, output_path, paths):
    """dry_run 用: {"main": mux, "cache": {音声レグ: コマンド, 測定 JSON: 測定コマンド}}

    音声レグの有無は Object の構成だけで決まり、__cache__ の中身（音声レグのキャッシュ）は
    見ない。ラウドネス測定は loudness._collect_loudness_cmds（JSON の有無を見ない）。
    """
    _, _, audio_path, _, mux_cmd = _segments_plan(project, output_path, paths)
    cache = {}
    if audio_path is not None:
        cache[audio_path] = _build_audio_leg_cmd(project, audio_path)
    cache.update(_collect_loudness_cmds(project, output_path))
    return {"main": mux_cmd, "cache": cache}


def _ensure_audio_leg(project, output_path, audio_path, timeout):
    """実レンダ用: 音声レグをキャッシュから使うか、測定 → レンダして原子的に残す。

    命中すれば増幅量の測定（normalize_audio(mode="linear")）も要らない
    （鍵が「同じ音声グラフ・同じ素材・同じ増幅の設定」を表すので、増幅量も同じ）。
    """
    project._note_planned_artifact(audio_path)
    if _audio_leg_ok(audio_path):
        print(f"  音声レグ: キャッシュを使います（{audio_path}）")
        return
    # normalize_audio(mode="linear") の増幅量（本レンダと同じ関数・同じ値）
    _ensure_linear_gain(project, output_path, timeout)
    audio_cmd = _build_audio_leg_cmd(project, audio_path)
    if project._verbose():
        print(f"  ffmpeg {' '.join(str(a) for a in audio_cmd[1:])}")
    os.makedirs(os.path.dirname(audio_path), exist_ok=True)
    _run_ffmpeg_to_cache(audio_cmd, audio_path, timeout,
                         context="区間結合の音声レグ（全編1本）")


def _render_with_segments(project, output_path, paths, timeout):
    """実レンダ: 区間の検証 → 音声レグ（キャッシュ）→ concat + mux（一時パス → os.replace）"""
    infos = _validate_segments(project, paths)
    work_dir, list_path, audio_path, meta_path, mux_cmd = \
        _segments_plan(project, output_path, paths)
    final_path = mux_cmd[-1]
    print(f"区間の結合: {len(paths)}本 / {sum(i['frames'] for i in infos)}コマ"
          f"（{project.duration}s × {project.fps}fps）"
          f"{' / 音声は全編1本' if audio_path is not None else ''}")
    if audio_path is not None:
        _ensure_audio_leg(project, output_path, audio_path, timeout)
    os.makedirs(work_dir, exist_ok=True)
    try:
        # 各区間の尺は probe したコマ数 / fps（編集リストの丸めた尺を使わない）
        _write_concat_list(list_path, [os.path.abspath(p) for p in paths],
                           [info["frames"] for info in infos], project.fps)
        if meta_path is not None:
            _write_chapters_metadata(project, meta_path)
        tmp_path = _unique_tmp_path(final_path)
        run_cmd = list(mux_cmd)
        run_cmd[-1] = tmp_path
        try:
            _run_ffmpeg(run_cmd, timeout=timeout,
                        context=f"区間の結合(concat)と音声mux: {final_path}")
            os.replace(tmp_path, final_path)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    finally:
        _shutil.rmtree(work_dir, ignore_errors=True)
