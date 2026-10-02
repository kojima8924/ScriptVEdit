# -*- coding: utf-8 -*-
"""scriptvedit が書くテキスト成果物の改行が LF であることの回帰テスト。

Windows のテキストモード既定（"\\n" → "\\r\\n"）で書くと、FFmpeg 8 の drawtext が
textfile の "\\r\\n" を改行2回として描き、複数行 text() の行間が倍になっていた
（実測: 2行の "A\\nB" の描画高さ 75px が CRLF では 121px）。
_atomic_write_text を改行無変換（LF）にし、drawtext 用テキストは CR を LF へ
正規化してから書く。karaoke の ASS・FFMETADATA チャプター・チャプター目次・
長大フィルタの一時ファイルも同じく LF（どれも LF で問題なく動く）。

判定は「書き込まれたバイトに \\r が無いこと」。テキストモードで読むと
改行が正規化されて差が見えなくなるため、必ずバイナリで読む。
"""
import os

import pytest

import scriptvedit as sv
from scriptvedit.chapters import (
    _chapters_metadata_path, _write_chapters_metadata, export_chapters,
    export_metadata)
from scriptvedit.context import _exec_stack, activate, current_project
from scriptvedit.ffmpeg import (
    _FILTER_SCRIPT_THRESHOLD, _atomic_write_text, _externalize_long_filters)
from scriptvedit.text import _ensure_textfile


@pytest.fixture(autouse=True)
def _restore_project_globals():
    old_current = current_project()
    old_stack = list(_exec_stack)
    activate(None)
    _exec_stack[:] = []
    try:
        yield
    finally:
        activate(old_current)
        _exec_stack[:] = old_stack


def _bytes(path):
    with open(path, "rb") as f:
        return f.read()


def test_atomic_write_text_keeps_lf(tmp_path):
    path = tmp_path / "a.txt"
    _atomic_write_text(str(path), "1行目\n2行目\n")
    data = _bytes(path)
    assert b"\r" not in data
    assert data == "1行目\n2行目\n".encode("utf-8")


def test_drawtext_textfile_has_no_cr():
    """drawtext の textfile は LF。行数ぶんの改行だけが入る"""
    path = _ensure_textfile("複数行の\nテキスト\n3行目")
    data = _bytes(path)
    assert b"\r" not in data
    assert data.count(b"\n") == 2


def test_drawtext_textfile_normalizes_cr_in_content():
    """呼び出し側の文字列に CR が混じっていても LF に正規化される。

    CRLF / 単独 CR / LF のどれで渡しても同じ内容なので、同じ鍵（パス）になる。
    """
    lf = _ensure_textfile("上の行\n下の行")
    crlf = _ensure_textfile("上の行\r\n下の行")
    cr = _ensure_textfile("上の行\r下の行")
    assert lf == crlf == cr
    assert b"\r" not in _bytes(lf)


def test_multiline_text_render_command_uses_lf_textfile(tmp_path):
    """text() の dry_run が参照する textfile も LF で書かれている"""
    layer = tmp_path / "l_text.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "text('1行目\\n2行目\\n3行目', size=40, border=3).time(1)\n",
        encoding="utf-8")
    p = sv.Project()
    p.configure(width=320, height=180, fps=10)
    p.layer(str(layer))
    main = p.render(str(tmp_path / "o.mp4"), dry_run=True)["main"]
    fc = main[main.index("-filter_complex") + 1]
    rel = fc.split("textfile='", 1)[1].split("'", 1)[0].replace("\\:", ":")
    data = _bytes(rel)
    assert b"\r" not in data
    assert data.count(b"\n") == 2


def test_karaoke_ass_has_no_cr():
    obj = sv.karaoke([(0, 2, "あいう"), (2, 4, "えお")])
    data = _bytes(obj._text_spec["srt"])
    assert b"\r" not in data
    assert b"[Events]\n" in data


def test_chapter_outputs_have_no_cr(tmp_path):
    """FFMETADATA・YouTube 目次・メタデータ（txt/json）も LF"""
    p = sv.Project()
    p.configure(width=320, height=180, fps=15)
    p.duration = 10
    p.marker(0, "オープニング")
    p.marker(4, "本編")
    meta = _chapters_metadata_path(p)
    _write_chapters_metadata(p, meta)
    chapters = export_chapters(p, str(tmp_path / "chapters.txt"))
    txt = export_metadata(p, str(tmp_path / "meta.txt"), title="題",
                          description="説明\n2行目", tags=["a"])
    js = export_metadata(p, str(tmp_path / "meta.json"), title="題")
    for path in (meta, chapters, txt, js):
        data = _bytes(path)
        assert b"\r" not in data, path
        assert b"\n" in data, path


def test_externalized_filter_script_is_byte_identical():
    """長大フィルタの一時ファイルはコマンドライン引数と同じバイト列"""
    long_filter = "null," * (_FILTER_SCRIPT_THRESHOLD // 5) + "null\n"
    cmd = ["ffmpeg", "-i", "in.mp4", "-filter_complex", long_filter, "out.mp4"]
    new_cmd, tmp_files = _externalize_long_filters(cmd)
    try:
        assert new_cmd[3] == "-/filter_complex"
        assert _bytes(new_cmd[4]) == long_filter.encode("utf-8")
    finally:
        for path in tmp_files:
            os.remove(path)
