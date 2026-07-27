# -*- coding: utf-8 -*-
"""p.audit() 品質lintのテスト

過去の人間レビュー指摘（文字サイズ・縁取り・duck_under・BGMループ/尺・
normalize_audio）と `~` 品質ヒント報告の受け皿が正しく検出することを固定する。
"""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from scriptvedit import (  # noqa: E402
    Object, Project, asset, duck_under, fade, loop, move, text,
)


def _codes(findings):
    return [f["code"] for f in findings]


def _mk(width=1280, height=1080):
    p = Project()
    p.configure(width=width, height=height, fps=30)
    return p


def _tone(tmp_path, name, seconds):
    """テスト用の正弦波wavを生成する"""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い環境")
    wav = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}", str(wav)],
        check=True, capture_output=True, timeout=30)
    return str(wav)


# --- quality-hint-ignored -------------------------------------------------

def test_quality_hint_ignored_reported():
    """~を付けたが軽量代替の無いopはinfoで報告される（実行時警告は出さない契約）"""
    p = _mk()
    o = Object(asset("images/shape_badge.png"))
    o.time(2) <= ~fade(0.5) & move(x=0.5, y=0.5)
    findings = p.audit(quiet=True)
    assert "quality-hint-ignored" in _codes(findings)
    # severity は info（正常動作の注意喚起）
    f = next(f for f in findings if f["code"] == "quality-hint-ignored")
    assert f["severity"] == "info"


def test_normal_ops_not_reported():
    """~なしの通常opは報告されない"""
    p = _mk()
    o = Object(asset("images/shape_badge.png"))
    o.time(2) <= fade(0.5) & move(x=0.5, y=0.5)
    assert "quality-hint-ignored" not in _codes(p.audit(quiet=True))


# --- 文字の可読性 ---------------------------------------------------------

def test_small_text_warns():
    """1080p換算32px未満はwarning"""
    p = _mk(height=1080)
    text("小さい", size=20, border=3).time(2)
    findings = p.audit(quiet=True)
    f = next(f for f in findings if f["code"] == "text-too-small")
    assert f["severity"] == "warning"


def test_body_size_is_info():
    """32〜44pxはinfo（注釈なら許容・本文なら小さめ）"""
    p = _mk(height=1080)
    text("注釈", size=36, border=3).time(2)
    f = next(f for f in p.audit(quiet=True) if f["code"] == "text-too-small")
    assert f["severity"] == "info"


def test_threshold_scales_with_height():
    """しきい値はProject解像度でスケールする（720pの21pxはOK側）"""
    p = _mk(height=720)
    text("注釈", size=32, border=3).time(2)  # 1080p換算48px相当
    assert "text-too-small" not in _codes(p.audit(quiet=True))


def test_no_decoration_warns_and_any_decoration_passes():
    """縁取り・影・下地が全て無ければwarning、どれか1つあれば出ない"""
    p1 = _mk()
    text("裸の文字", size=60).time(2)
    assert "text-no-decoration" in _codes(p1.audit(quiet=True))

    for kwargs in ({"border": 3}, {"shadow": (2, 2)}, {"box": True}):
        p = _mk()
        text("装飾あり", size=60, **kwargs).time(2)
        assert "text-no-decoration" not in _codes(p.audit(quiet=True)), kwargs


# --- 音声構成 -------------------------------------------------------------

def test_audio_overlap_without_duck_warns(tmp_path):
    """音声が1秒以上重なるのにduck_underが無ければwarning"""
    p = _mk()
    a = Object(_tone(tmp_path, "a.wav", 3))
    a.time(3)
    b = Object(_tone(tmp_path, "b.wav", 3))
    b.time(3)  # 同時刻に重なる
    assert "audio-overlap-no-duck" in _codes(p.audit(quiet=True))


def test_audio_overlap_with_duck_passes(tmp_path):
    """duck_underがあれば重なり警告は出ない"""
    p = _mk()
    voice = Object(_tone(tmp_path, "v.wav", 3))
    voice.time(3)
    bgm = Object(_tone(tmp_path, "bgm.wav", 3))
    bgm.time(3) <= duck_under(voice)
    assert "audio-overlap-no-duck" not in _codes(p.audit(quiet=True))


def test_loop_is_info(tmp_path):
    """loop()はループ感のinfo"""
    p = _mk()
    bgm = Object(_tone(tmp_path, "bgm.wav", 1))
    bgm.time(3) <= loop()
    assert "bgm-loop" in _codes(p.audit(quiet=True))


def test_short_bgm_warns(tmp_path):
    """duck_under持ち音声(=BGM相当)の実尺が表示区間より短ければwarning"""
    p = _mk()
    voice = Object(_tone(tmp_path, "v.wav", 6))
    voice.time(6)
    bgm = Object(_tone(tmp_path, "bgm.wav", 2))  # 6秒区間に2秒しかない
    bgm.time(6) <= duck_under(voice)
    assert "bgm-too-short" in _codes(p.audit(quiet=True))


def test_normalize_audio_hint(tmp_path):
    """音声があればnormalize_audio未設定をinfoで示す。設定すれば消える"""
    p = _mk()
    Object(_tone(tmp_path, "a.wav", 2)).time(2)
    assert "no-normalize-audio" in _codes(p.audit(quiet=True))

    p2 = _mk()
    Object(_tone(tmp_path, "a.wav", 2)).time(2)
    p2.normalize_audio()
    assert "no-normalize-audio" not in _codes(p2.audit(quiet=True))


def test_no_audio_no_audio_findings():
    """音声が無ければ音声系findingは一切出ない"""
    p = _mk()
    o = Object(asset("images/shape_badge.png"))
    o.time(2) <= move(x=0.5, y=0.5)
    codes = _codes(p.audit(quiet=True))
    assert not any(c.startswith(("audio-", "bgm-", "no-normalize")) for c in codes)


# --- strict / レイヤー解決 -------------------------------------------------

def test_strict_raises_on_warning():
    """strict=Trueはwarningがあれば日本語RuntimeError"""
    p = _mk()
    text("裸の文字", size=60).time(2)
    with pytest.raises(RuntimeError, match="text-no-decoration"):
        p.audit(strict=True, quiet=True)


def test_strict_passes_on_info_only():
    """infoだけならstrictでも通る"""
    p = _mk()
    o = Object(asset("images/shape_badge.png"))
    o.time(2) <= ~fade(0.5) & move(x=0.5, y=0.5)
    findings = p.audit(strict=True, quiet=True)
    assert all(f["severity"] == "info" for f in findings)


def test_audit_resolves_layers(tmp_path):
    """layer登録のみのProjectでもauditが内部でdry_run解決して検査できる"""
    layer = tmp_path / "audit_layer.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "text('裸の文字', size=60).time(2)\n",
        encoding="utf-8")
    p = _mk()
    p.layer(str(layer))
    assert "text-no-decoration" in _codes(p.audit(quiet=True))


def test_clean_project_is_clean():
    """指摘対象の無いプロジェクトはfindingsが空"""
    p = _mk()
    o = Object(asset("images/shape_badge.png"))
    o.time(2) <= fade(0.5) & move(x=0.5, y=0.5)
    text("読みやすい文字", size=60, border=3).time(2)
    assert p.audit(quiet=True) == []


def test_web_content_reports_manual_storyboard_review(tmp_path):
    """Canvas内部を検査済みと誤認しないようinfoで代表フレーム確認を促す。"""
    html = tmp_path / "canvas.html"
    html.write_text(
        "<script>function renderFrame(state) {}</script>", encoding="utf-8")
    p = _mk(width=320, height=180)
    Object(str(html), duration=1, size=(320, 180))

    finding = next(
        item for item in p.audit(quiet=True)
        if item["code"] == "web-content-uninspected")

    assert finding["severity"] == "info"
    assert "storyboard" in finding["message"]
    assert p.audit(strict=True, quiet=True)
