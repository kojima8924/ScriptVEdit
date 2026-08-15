# -*- coding: utf-8 -*-
"""サムネイル/絵コンテ（p.thumbnail / p.storyboard）のフレーム抽出系。

audit.py と同じ方式で、Project インスタンスを第1引数に受ける自由関数を
提供する（Project は import しない＝循環 import を起こさない）。
project.py 側には manifest 掲載用の薄い委譲メソッドだけを残す。
"""

import os
import math as _math
import shutil as _shutil
import builtins as _builtins

from scriptvedit.chapters import _fmt_timestamp
from scriptvedit.ffmpeg import _run_ffmpeg, _unique_tmp_path
from scriptvedit.state import _ARTIFACT_DIR
from scriptvedit.validate import _require_number
# project.py と同じシャドウを再現する: 素の max/min は Expr 対応版
# （builtins へ変えると挙動が変わるため、この import を外さないこと）
from scriptvedit.expr import max, min


def thumbnail(project, at, out, *, timeout=600, source=None):
    """指定時刻 at(秒) のフレームを1枚のPNGとして書き出す。

    render() と同じプラン解決・チェックポイント生成を通し、
    フィルタグラフの t 基準を保ったまま -ss + -frames:v 1 で抜き出す。
    source に既レンダ動画を指定すると、Projectグラフを再構築せず入力seekで
    高速に抽出する。
    """
    at = float(at)
    if at < 0:
        raise ValueError(f"thumbnail: at は0以上が必要です: {at}")
    if source is not None:
        source, total = _review_source(project, source, "thumbnail")
        if at >= total:
            raise ValueError(
                f"thumbnail: at({at}) は素材尺({total})未満が必要です")
        _extract_source_frame(source, at, out, timeout=timeout)
        print(f"完了: {out}")
        return out
    _prepare_thumbnail_graph(project)
    if project.duration is not None and at >= project.duration:
        raise ValueError(
            f"thumbnail: at({at}) は総尺({project.duration})未満が必要です")
    _extract_frame(project, at, out, timeout=timeout)
    print(f"完了: {out}")
    return out


def _review_source(project, source, func):
    """thumbnail/storyboardの既レンダ動画を検証し、(path, duration)を返す。"""
    path = os.path.abspath(os.fsdecode(source))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{func}: source が見つかりません: {source}")
    info = project._probe_media(path)
    if not info:
        raise ValueError(f"{func}: source のメディア情報を取得できません: {source}")
    if not info.get("has_video"):
        raise ValueError(
            f"{func}: source に映像ストリームがありません: {source}")

    def _valid_duration(value):
        return (not isinstance(value, bool)
                and isinstance(value, (int, float))
                and _math.isfinite(value) and value > 0)

    # MP4等はstream尺を持つので映像尺を優先する。
    # libvpx-vp9 WebM等のstream durationを持たないため、
    # 映像ストリームの存在を確認した上でformat尺へフォールバックする。
    video_total = info.get("video_duration")
    total = (video_total if _valid_duration(video_total)
             else info.get("duration"))
    if not _valid_duration(total):
        raise ValueError(
            f"{func}: source の映像尺を取得できません: {source}")
    return path, float(total)


def _extract_source_frame(source, at, out, *, timeout=600):
    """既レンダ動画を入力側seekし、Projectグラフなしで1フレーム抽出する。"""
    out = os.path.abspath(os.fsdecode(out))
    directory = os.path.dirname(out)
    os.makedirs(directory, exist_ok=True)
    tmp_out = _unique_tmp_path(out)
    cmd = [
        "ffmpeg", "-y", "-ss", str(float(at)), "-i", source,
        "-map", "0:v:0", "-frames:v", "1", "-update", "1",
        "-pix_fmt", "rgba", "-an", tmp_out,
    ]
    print(f"完成動画からサムネイル抽出 @{at}s: {out}")
    try:
        _run_ffmpeg(cmd, timeout=timeout)
        if not os.path.isfile(tmp_out) or os.path.getsize(tmp_out) <= 0:
            raise RuntimeError(
                f"フレーム抽出結果が生成されませんでした: {out}")
        os.replace(tmp_out, out)
    finally:
        try:
            os.remove(tmp_out)
        except OSError:
            pass
    return out


def _prepare_thumbnail_graph(project):
    """thumbnail/storyboard 共通: プラン解決+レイヤーexec+checkpoint確保を
    一度だけ行い、-ss 単フレーム抽出可能な確定済みグラフを構築する。

    準備シーケンス本体は render() と同一の共通ヘルパ
    （_begin_render_pass → _resolve_plan_duration → _execute_render_pass）を
    通し、逐語重複による手動同期を排除する。"""
    project._begin_render_pass()
    project._resolve_plan_duration()
    project._execute_render_pass()
    # render() と同じく数式PNG/Webクリップを先に実体化する
    # （formula の PNG が無いと ffmpeg が "No such file or directory" で落ちる）
    project._ensure_formula_objects()
    project._ensure_web_objects()
    project._ensure_checkpoints()


def _extract_frame(project, at, out, *, timeout=600):
    """準備済みグラフに対し -ss + -frames:v 1 で1フレームだけ抽出する。"""
    project._thumbnail_at = float(at)
    try:
        cmd = project._build_ffmpeg_cmd(out)
        print(f"サムネイル抽出 @{at}s: {out}")
        print(f"  ffmpeg {' '.join(cmd[1:])}")
        _run_ffmpeg(cmd, timeout=timeout)
    finally:
        project._thumbnail_at = None
    return out


def storyboard(project, out_path, *, cols=4, interval=None, source=None,
               timeout=600):
    """タイムラインの絵コンテ（サムネイル格子画像）を1枚のPNGとして生成する。

    interval秒ごと（省略時は 総尺/12）に thumbnail() と同じ抽出経路
    （plan解決+checkpoint確保+ffmpeg単フレーム抽出）でサムネイルを取り出し、
    PILでcols列のグリッドに結合する（各コマ左上に時刻ラベルを焼き込む）。
    事前renderなしの場合も、Projectグラフの準備とFFmpeg実行は各1回だけ。
    source に既レンダ動画を指定すれば入力seekで軽量に抽出する。

    戻り値: 書き出したパス(out_path)。
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as e:
        raise ImportError(
            "storyboard() には Pillow が必要です。"
            "`pip install Pillow` を実行してください。") from e
    if cols < 1:
        raise ValueError(f"storyboard: cols は1以上が必要です: {cols}")
    if interval is not None:
        _require_number("storyboard", "interval", interval, 0.001, None)

    # 作業ディレクトリは pid+uuid でユニーク化する。固定名 "_frames" だと
    # 複数プロジェクト/並列実行で相互にフレームを削除・混入する（issue #13 P2-15）
    import uuid as _uuid
    tmp_dir = os.path.join(
        _ARTIFACT_DIR, "storyboard",
        f"_frames_{os.getpid()}_{_uuid.uuid4().hex[:8]}")
    os.makedirs(tmp_dir, exist_ok=True)
    try:
        if source is None:
            # プラン解決・レイヤーexec・checkpoint確保は一度だけ実施する。
            _prepare_thumbnail_graph(project)
            total = project.duration
            review_source = None
        else:
            review_source, total = _review_source(project, source, "storyboard")
        if not total or total <= 0:
            raise RuntimeError("storyboard: タイムラインの総尺を確定できませんでした")
        step = interval if interval is not None else max(total / 12.0, 0.01)

        times = [0.0]
        t = step
        while t < total - 1e-6:
            times.append(t)
            t += step

        frame_paths = []
        if review_source is None:
            unique_indices, requested_indices = (
                _storyboard_frame_plan(project, times, total))
            pattern = os.path.join(tmp_dir, "frame_%03d.png")
            _extract_storyboard_frames(
                project, unique_indices, pattern, timeout=timeout)
            frame_by_index = {
                frame_index: os.path.join(tmp_dir, f"frame_{i:03d}.png")
                for i, frame_index in enumerate(unique_indices)
            }
            frame_paths = [
                (tsec, frame_by_index[frame_index])
                for tsec, frame_index in zip(times, requested_indices)
            ]
        else:
            for i, tsec in enumerate(times):
                fp = os.path.join(tmp_dir, f"frame_{i:03d}.png")
                _extract_source_frame(
                    review_source,
                    min(tsec, max(0.0, total - 0.001)),
                    fp, timeout=timeout)
                frame_paths.append((tsec, fp))

        thumbs = []
        for _, fp in frame_paths:
            with Image.open(fp) as image:
                thumbs.append(image.convert("RGB"))
        tw, th = thumbs[0].size
        n = len(thumbs)
        rows = (n + cols - 1) // cols
        gap = 4
        grid_w = cols * tw + (cols - 1) * gap
        grid_h = rows * th + (rows - 1) * gap
        canvas = Image.new("RGB", (grid_w, grid_h), (20, 20, 20))
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 18)
        except Exception:
            font = ImageFont.load_default()
        for i, ((tsec, _fp), img) in enumerate(zip(frame_paths, thumbs)):
            r, c = divmod(i, cols)
            x = c * (tw + gap)
            y = r * (th + gap)
            canvas.paste(img, (x, y))
            label = _fmt_timestamp(tsec)
            draw.rectangle([x, y, x + 68, y + 20], fill=(0, 0, 0))
            draw.text((x + 4, y + 3), label, fill=(255, 255, 0), font=font)

        d = os.path.dirname(out_path)
        if d:
            os.makedirs(d, exist_ok=True)
        # 一時ファイルも pid+uuid でユニーク化（同時実行の相互上書き防止）
        tmp_out = _unique_tmp_path(out_path)
        try:
            canvas.save(tmp_out)
            os.replace(tmp_out, out_path)
        finally:
            try:
                os.remove(tmp_out)  # 失敗時の残骸掃除（成功時は存在しない）
            except OSError:
                pass
    finally:
        _shutil.rmtree(tmp_dir, ignore_errors=True)
    return out_path


def _storyboard_frame_plan(project, times, total):
    """要求時刻を有効なCFR frame番号へ変換し、重複をまとめる。"""
    fps = float(project.fps)
    # タイムラインに存在するframeのnは n/fps < total。
    # 末尾直前の時刻をceilした結果が範囲外に出ないよう、
    # 実在する最終frameへクランプする。
    last_index = _builtins.max(
        0, int(_math.ceil(float(total) * fps - 1e-9)) - 1)
    requested = []
    unique = []
    seen = set()
    for tsec in times:
        frame_index = int(_math.ceil(float(tsec) * fps - 1e-9))
        frame_index = _builtins.min(
            _builtins.max(0, frame_index), last_index)
        requested.append(frame_index)
        if frame_index not in seen:
            seen.add(frame_index)
            unique.append(frame_index)
    return unique, requested


def _extract_storyboard_frames(project, frame_indices, pattern, *, timeout=600):
    """確定済みProjectグラフから複数frameを1回のFFmpegで抽出する。"""
    if not frame_indices:
        raise ValueError("storyboard: 抽出frameが空です")
    project._storyboard_frame_indices = tuple(int(i) for i in frame_indices)
    try:
        cmd = project._build_ffmpeg_cmd(pattern)
        print(f"絵コンテ一括抽出: {len(frame_indices)}コマ")
        print(f"  ffmpeg {' '.join(cmd[1:])}")
        _run_ffmpeg(cmd, timeout=timeout)
    finally:
        project._storyboard_frame_indices = None
