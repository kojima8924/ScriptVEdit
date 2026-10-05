# -*- coding: utf-8 -*-
"""絵コンテのコマ数と OS に依存しない時刻ラベルを検証する。"""

import importlib

import pytest

from scriptvedit import Project
from scriptvedit.context import activate, current_project
import scriptvedit.preview as preview

text_module = importlib.import_module("scriptvedit.text")


@pytest.fixture(autouse=True)
def _restore_project():
    previous = current_project()
    yield
    activate(previous)


def _stub_extraction(monkeypatch, tmp_path):
    """抽出だけを小画像に置き換え、格子とラベルの生成は実行する。"""
    image = pytest.importorskip("PIL.Image", reason="Pillow が無い環境")
    monkeypatch.setattr(preview, "_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(preview, "_prepare_thumbnail_plan", lambda project: None)
    monkeypatch.setattr(preview, "_ensure_thumbnail_media", lambda project: None)

    def extract(project, indices, pattern, **kwargs):
        for i, _ in enumerate(indices):
            image.new("RGB", (80, 30)).save(pattern % i)

    monkeypatch.setattr(preview, "_extract_storyboard_frames", extract)
    return image


@pytest.mark.parametrize("font_path", [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
])
def test_storyboard_uses_non_windows_font(tmp_path, monkeypatch, font_path):
    """Windows フォントが無くても存在する OS 標準フォントを 18px で使う。"""
    _stub_extraction(monkeypatch, tmp_path)
    fonts = pytest.importorskip("PIL.ImageFont", reason="Pillow が無い環境")
    fallback = fonts.load_default()
    calls = []
    monkeypatch.delenv("SCRIPTVEDIT_FONT", raising=False)
    monkeypatch.setattr(text_module, "_DEFAULT_FONT_CANDIDATES", [font_path])
    exists = text_module.os.path.exists
    monkeypatch.setattr(text_module.os.path, "exists",
                        lambda path: path == font_path or exists(path))

    def truetype(path, size):
        calls.append((path, size))
        if path != font_path:
            raise OSError("フォントが存在しません")
        return fallback

    monkeypatch.setattr(fonts, "truetype", truetype)
    monkeypatch.setattr(fonts, "load_default", lambda: fallback)
    project = Project()
    project.configure(width=80, height=30, fps=1, duration=1)
    project.storyboard(str(tmp_path / "board.png"), cols=1)
    assert (font_path, 18) in calls


@pytest.mark.parametrize("total, interval, count", [
    (120, 1, 120),
    (120, None, 12),
    (0.0001, None, 1),
])
@pytest.mark.parametrize("source", [None, "rendered.mp4"])
def test_storyboard_bounded_grid(tmp_path, monkeypatch, total, interval, count,
                                 source):
    """上限ちょうど・既定12コマ・短尺1コマが両抽出経路で出力できる。"""
    image = _stub_extraction(monkeypatch, tmp_path)
    calls = []

    def source_frame(path, at, out, **kwargs):
        calls.append(at)
        image.new("RGB", (80, 30)).save(out)

    monkeypatch.setattr(preview, "_review_source",
                        lambda *args: (source, total))
    monkeypatch.setattr(preview, "_extract_source_frame", source_frame)
    project = Project()
    project.configure(width=80, height=30, fps=10, duration=total)
    output = tmp_path / "board.png"
    project.storyboard(str(output), cols=1, interval=interval, source=source)
    with image.open(output) as result:
        assert result.size == (80, count * 30 + (count - 1) * 4)
    if source is not None:
        assert len(calls) == count


def test_storyboard_font_uses_environment_override(monkeypatch, tmp_path):
    """文字と共通の環境変数で時刻ラベルのフォントを選べる。"""
    fonts = pytest.importorskip("PIL.ImageFont", reason="Pillow が無い環境")
    fallback = fonts.load_default()
    font_path = tmp_path / "selected.ttf"
    font_path.write_bytes(b"font")
    monkeypatch.setenv("SCRIPTVEDIT_FONT", font_path.as_posix())
    calls = []

    def truetype(path, size):
        calls.append((path, size))
        return fallback

    monkeypatch.setattr(fonts, "truetype", truetype)
    assert preview._storyboard_font() is fallback
    assert calls == [(font_path.as_posix(), 18)]


@pytest.mark.parametrize("has_dejavu", [True, False])
def test_storyboard_font_fallback(monkeypatch, has_dejavu):
    """OS フォントが無ければ DejaVu Sans、最後に Pillow 既定を使う。"""
    fonts = pytest.importorskip("PIL.ImageFont", reason="Pillow が無い環境")
    default_font = fonts.load_default()
    dejavu_font = object()
    calls = []
    monkeypatch.delenv("SCRIPTVEDIT_FONT", raising=False)
    monkeypatch.setattr(text_module, "_DEFAULT_FONT_CANDIDATES", [])

    def truetype(path, size):
        calls.append((path, size))
        if has_dejavu:
            return dejavu_font
        raise OSError("フォントが存在しません")

    monkeypatch.setattr(fonts, "truetype", truetype)
    monkeypatch.setattr(fonts, "load_default", lambda: default_font)
    assert preview._storyboard_font() is (
        dejavu_font if has_dejavu else default_font)
    assert calls == [("DejaVuSans.ttf", 18)]
