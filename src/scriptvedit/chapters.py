# -*- coding: utf-8 -*-
"""チャプターマーカー/投稿用メタデータ（p.marker / export_chapters / export_metadata）。

audit.py と同じ方式で、Project インスタンスを第1引数に受ける自由関数を
提供する（Project は import しない＝循環 import を起こさない）。
project.py 側には manifest 掲載用の薄い委譲メソッドだけを残す。
"""

import os
import json
import hashlib

from scriptvedit.ffmpeg import _atomic_write_text
from scriptvedit.state import _ARTIFACT_DIR, _ENGINE_VER
from scriptvedit.validate import _require_number


def marker(project, time, label):
    """タイムライン上のマーカーを記録（mp4チャプター/YouTube目次用）"""
    _require_number("marker", "time", time, 0)
    project._markers.append((float(time), str(label)))
    return project


def _sorted_markers(project):
    """重複除去 + 時刻昇順のマーカー列を返す"""
    seen = set()
    uniq = []
    for t, label in project._markers:
        key = (t, label)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((t, label))
    uniq.sort(key=lambda m: m[0])
    return uniq


def _fmt_timestamp(sec):
    """秒 → H:MM:SS または M:SS（YouTube目次形式）"""
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def export_chapters(project, path):
    """YouTube用のチャプター目次テキスト（0:00 ラベル形式）を出力する"""
    markers = _sorted_markers(project)
    lines = []
    # YouTube仕様上、先頭は 0:00 が必要。無ければ補う
    if not markers or markers[0][0] > 0.001:
        lines.append("0:00 イントロ")
    for t, label in markers:
        lines.append(f"{_fmt_timestamp(t)} {label}")
    # tmp→os.replace の原子的書き込みは ffmpeg.py の共通ヘルパに集約
    _atomic_write_text(path, "\n".join(lines) + "\n")
    return path


def export_metadata(project, path=None, *, title=None, description=None,
                    tags=None):
    """YouTube投稿用メタデータ（チャプター+タイトル+説明+タグ）を1ファイルに出力する。

    title省略時は self.param("title") があればそれを使う（無ければNone）。
    path省略時は "metadata.json"（カレントディレクトリ）に書き出す。
    拡張子で出力形式を切替: .json ならJSON（構造化データ）、
    .txt ならYouTube概要欄にそのまま貼れるプレーンテキスト
    （タイトル→説明→チャプター目次→#タグ の順）。

    戻り値: 書き出したパス。
    """
    if title is None:
        title = project.param("title", None)
    markers = _sorted_markers(project)
    chapter_lines = []
    if not markers or markers[0][0] > 0.001:
        chapter_lines.append("0:00 イントロ")
    for t, label in markers:
        chapter_lines.append(f"{_fmt_timestamp(t)} {label}")
    # json の chapters も chapter_lines と同一ソースから生成する
    # （先頭0:00章の欠落を防ぐ）
    chapters = [{"time": t, "label": label} for t, label in markers]
    if not markers or markers[0][0] > 0.001:
        chapters.insert(0, {"time": 0.0, "label": "イントロ"})
    if isinstance(tags, str):
        tag_list = [tags] if tags else []
    else:
        tag_list = [str(t) for t in tags] if tags else []

    if path is None:
        path = "metadata.json"
    ext = os.path.splitext(path)[1].lower()
    if ext == ".txt":
        lines = []
        if title:
            lines.append(title)
            lines.append("")
        if description:
            lines.append(description)
            lines.append("")
        if chapter_lines:
            lines.extend(chapter_lines)
            lines.append("")
        if tag_list:
            lines.append(" ".join(f"#{t}" for t in tag_list))
        content = "\n".join(lines).rstrip("\n") + "\n"
    else:
        data = {
            "title": title,
            "description": description,
            "tags": tag_list,
            "chapters": chapters,
            "chapters_text": "\n".join(chapter_lines),
        }
        content = json.dumps(data, ensure_ascii=False, indent=2)
    # tmp→os.replace の原子的書き込みは ffmpeg.py の共通ヘルパに集約
    _atomic_write_text(path, content)
    return path


def _chapters_metadata_path(project):
    """FFMETADATAチャプターファイルのキャッシュパス（内容由来の鍵）"""
    total = project.duration if project.duration is not None else 0
    sig = "||".join(f"{t}:{label}" for t, label in _sorted_markers(project))
    sig += f"||dur={total}||ev={_ENGINE_VER}"
    key = hashlib.sha256(sig.encode()).hexdigest()[:16]
    return os.path.join(_ARTIFACT_DIR, "chapters", f"{key}.txt")


def _write_chapters_metadata(project, path):
    """FFMETADATA1形式のチャプターファイルを書き出す（絶対時刻）。

    部分レンダ(render(start,end))では出力側 -ss/-t により FFmpeg が
    チャプター時刻を自動でシフト/クランプするため（実測: ffmpeg 8.0）、
    ここでは常に絶対時刻で書き出す。手動で window 減算すると二重シフトになり、
    窓開始時にアクティブなチャプターも失われるため行わない。"""
    markers = _sorted_markers(project)
    total = project.duration if project.duration is not None else (
        markers[-1][0] + 1 if markers else 1)
    lines = [";FFMETADATA1"]
    for i, (t, label) in enumerate(markers):
        start_ms = int(t * 1000)
        end_ms = int((markers[i + 1][0] if i + 1 < len(markers) else total) * 1000)
        if end_ms <= start_ms:
            end_ms = start_ms + 1
        safe = label.replace("\\", "\\\\").replace("=", "\\=").replace(";", "\\;").replace("#", "\\#").replace("\r", " ").replace("\n", " ")
        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append(f"START={start_ms}")
        lines.append(f"END={end_ms}")
        lines.append(f"title={safe}")
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
