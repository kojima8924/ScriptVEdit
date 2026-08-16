# -*- coding: utf-8 -*-
"""__cache__ 配下のテキスト成果物が原子的に書かれ、壊れた残骸から復旧することの回帰テスト。

内容ハッシュをファイル名にするキャッシュは「存在すればスキップ」しがちだが、
Ctrl+C / ディスクフル / プロセスキルで切り詰められたファイルが一度残ると、
以後どのレンダでも再生成されず drawtext が途中までのテキストを黙って描き続ける。
書き込みを _atomic_write_text（tmp→os.replace）に統一し、exists ガードを
置かないことでこの失敗モードを封じている（監査 項目5）。
"""
import json
import os

from scriptvedit import Project
from scriptvedit.chapters import _chapters_metadata_path, _write_chapters_metadata
from scriptvedit.text import _ensure_textfile

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
LAYERS_DIR = os.path.join(TESTS_DIR, "layers")

_SAMPLE = "値: 100% 'テスト'\n2行目"


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _truncate(path):
    """途中で中断した書き込みの残骸を再現する（内容を切り詰める）"""
    full = _read(path)
    assert len(full) > 2
    with open(path, "w", encoding="utf-8") as f:
        f.write(full[:2])
    return full


def test_ensure_textfile_repairs_truncated_cache():
    """切り詰められた text キャッシュがあっても _ensure_textfile が復旧する"""
    path = _ensure_textfile(_SAMPLE)
    full = _truncate(path)
    assert _read(path) != full  # 壊した状態から始めていることの確認

    again = _ensure_textfile(_SAMPLE)
    assert again == path  # パス（=内容ハッシュ）は変わらない
    assert _read(path) == full


def test_render_repairs_truncated_textfile():
    """壊れた text キャッシュを置いた状態でレンダしても正しい内容へ戻る"""
    def _dry_run():
        p = Project()
        p.configure(width=640, height=360, fps=15, background_color="black")
        p.layer(os.path.join(LAYERS_DIR, "test52_text.py"), priority=0)
        return p.render("test_cache_text_atomic.mp4", dry_run=True)

    _dry_run()  # 1回目でテキストキャッシュを実体化させる
    # コマンドから textfile= のパスを拾う（レイヤーの実テキストに依存しない）
    blob = json.dumps(_dry_run(), ensure_ascii=False, default=str)
    paths = set()
    for part in blob.split("textfile='")[1:]:
        paths.add(part.split("'")[0].replace("\\\\:", ":").replace("\\:", ":"))
    assert paths, "textfile= を含むコマンドが見つからない（レイヤーの前提が変わった）"

    fulls = {}
    for rel in paths:
        fulls[rel] = _truncate(rel)

    _dry_run()
    for rel, full in fulls.items():
        assert _read(rel) == full


def test_chapters_metadata_is_written_atomically(monkeypatch):
    """FFMETADATA チャプターも _atomic_write_text 経由（最終パスへ直書きしない）"""
    p = Project()
    p.configure(width=320, height=180, fps=15)
    p.duration = 10
    p.marker(0, "オープニング")
    p.marker(4, "本編")

    path = _chapters_metadata_path(p)
    _write_chapters_metadata(p, path)
    expected = _read(path)
    assert "[CHAPTER]" in expected

    # 直書きなら open(最終パス) が呼ばれる。原子的書き込みなら別の一時パスへ書く
    real_open = open
    opened_for_write = []

    def _spy_open(file, mode="r", *args, **kwargs):
        if "w" in mode or "a" in mode or "+" in mode:
            opened_for_write.append(os.path.abspath(str(file)))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _spy_open)
    _truncate(path)
    opened_for_write.clear()
    _write_chapters_metadata(p, path)
    monkeypatch.undo()

    assert _read(path) == expected
    assert os.path.abspath(path) not in opened_for_write
    assert opened_for_write, "書き込み自体が行われていない"
