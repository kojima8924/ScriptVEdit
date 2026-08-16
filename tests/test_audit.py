# -*- coding: utf-8 -*-
"""p.audit() 品質lintのテスト

過去の人間レビュー指摘（文字サイズ・縁取り・duck_under・BGMループ/尺・
normalize_audio）と `~` 品質ヒント報告の受け皿が正しく検出することを固定する。
位置・はみ出し・尺外・グリフ欠落の4ルールは「期待する code だけが出て、
他が誤検出されない」ことも併せて固定する。
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


# --- offscreen-placement --------------------------------------------------

def test_offscreen_ratio_warns():
    """x が 0..1 の外（比率なので画面外）は warning。他の指摘は出ない"""
    p = _mk(width=1280, height=720)
    text("画面外", size=60, border=3, x=3.5).time(2)
    findings = p.audit(quiet=True)
    assert set(_codes(findings)) == {"offscreen-placement"}
    f = findings[0]
    assert f["severity"] == "warning" and "0〜1" in f["message"]


def test_offscreen_px_value_gets_conversion_hint():
    """整数っぽい大きな値は「px を渡していませんか」と換算値まで示す"""
    p = _mk(width=1280, height=720)
    text("px指定", size=60, border=3, x=640).time(2)
    f = next(f for f in p.audit(quiet=True)
             if f["code"] == "offscreen-placement")
    assert "px を渡していませんか" in f["message"] and "0.5" in f["message"]


def test_offscreen_slide_in_not_reported():
    """スライドイン（6点の一部だけ画面外）は正常な演出なので報告しない"""
    p = _mk()
    o = Object(asset("images/shape_badge.png"))
    o.time(2) <= move(from_x=-0.5, to_x=0.5)
    assert "offscreen-placement" not in _codes(p.audit(quiet=True))


def test_offscreen_move_effect_reported_once_per_axis():
    """move Effect の x/y も検査する（範囲外の軸だけ1件）"""
    p = _mk()
    o = Object(asset("images/shape_badge.png"))
    o.time(2) <= move(x=1.8, y=0.5)
    codes = _codes(p.audit(quiet=True))
    assert codes.count("offscreen-placement") == 1


def test_offscreen_default_placement_not_reported():
    """move を持たない Object（中央配置）は検査対象外"""
    p = _mk()
    Object(asset("images/shape_badge.png")).time(2)
    assert "offscreen-placement" not in _codes(p.audit(quiet=True))


# --- text-overflow --------------------------------------------------------

def test_text_overflow_warns():
    """size×文字数の推定幅が安全域を超えれば warning"""
    p = _mk(width=1280, height=720)
    text("あ" * 30, size=60, border=3).time(2)  # 推定1800px > 1216px
    findings = p.audit(quiet=True)
    assert set(_codes(findings)) == {"text-overflow"}
    assert findings[0]["severity"] == "warning"


def test_text_overflow_uses_longest_line():
    """改行済みなら最長行で判定する（合計30文字でも各行10文字ならOK）"""
    p = _mk(width=1280, height=720)
    text("\n".join(["あ" * 10] * 3), size=60, border=3).time(2)
    assert "text-overflow" not in _codes(p.audit(quiet=True))


def test_text_overflow_counts_halfwidth_as_half():
    """半角は0.5文字幅で数える（全角30文字相当の半角60文字はぎりぎり範囲内）"""
    p = _mk(width=1280, height=720)
    text("a" * 38, size=60, border=3).time(2)  # 推定1140px < 1216px
    assert "text-overflow" not in _codes(p.audit(quiet=True))


# --- outside-duration -----------------------------------------------------

def _resolved(p):
    """レンダせずに start_time を解決する（@ の絶対配置を反映させる）"""
    p._layers = [(0, len(p.objects), 0)]
    p._resolve_anchors()
    return p


def test_outside_duration_warns():
    """総尺より後ろへ絶対配置した Object は一度も映らないので warning"""
    p = _mk()
    p.configure(duration=10)
    (text("尺外", size=60, border=3) @ 500).show(3)
    findings = _resolved(p).audit(quiet=True)
    assert set(_codes(findings)) == {"outside-duration"}
    assert findings[0]["severity"] == "warning" and "500" in findings[0]["message"]


def test_inside_duration_not_reported():
    """総尺内の絶対配置は報告しない"""
    p = _mk()
    p.configure(duration=10)
    (text("尺内", size=60, border=3) @ 5).show(3)
    assert "outside-duration" not in _codes(_resolved(p).audit(quiet=True))


def test_duration_boundary_not_reported():
    """開始が総尺ちょうど手前なら（わずかでも映るので）報告しない"""
    p = _mk()
    p.configure(duration=10)
    (text("境界", size=60, border=3) @ 9.9).show(3)
    assert "outside-duration" not in _codes(_resolved(p).audit(quiet=True))


# --- font-missing-glyph ---------------------------------------------------

_LATIN_ONLY_FONT_CANDIDATES = (
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/tahoma.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/Library/Fonts/Arial.ttf",
)


def _latin_only_font():
    """日本語グリフを持たない欧文フォントを探す（無ければ skip）"""
    from scriptvedit.audit import _covers, _font_coverage
    for path in _LATIN_ONLY_FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        ranges = _font_coverage(path)
        if ranges and not _covers(ranges, ord("あ")):
            return path
    pytest.skip("日本語グリフを持たない欧文フォントが見つからない環境")


def _default_font_coverage():
    """既定フォントの cmap（解析できない環境なら skip）"""
    from scriptvedit.audit import _font_coverage
    from scriptvedit.text import _resolve_font
    ranges = _font_coverage(_resolve_font(None))
    if not ranges:
        pytest.skip("既定フォントの cmap を解析できない環境")
    return ranges


def test_cmap_parser_reads_default_font():
    """標準ライブラリだけの cmap パーサが既定フォントを読める"""
    from scriptvedit.audit import _covers
    ranges = _default_font_coverage()
    assert _covers(ranges, ord("あ")) and _covers(ranges, ord("A"))


def test_covers_range_lookup():
    """被覆判定（二分探索）が範囲の内外を正しく分ける"""
    from scriptvedit.audit import _covers
    ranges = [(0x30, 0x39), (0x4E00, 0x9FFF)]
    assert _covers(ranges, 0x30) and _covers(ranges, 0x39)
    assert _covers(ranges, 0x4E00) and _covers(ranges, 0x9FFF)
    assert not _covers(ranges, 0x2F) and not _covers(ranges, 0x3A)
    assert not _covers(ranges, 0x3042) and not _covers(ranges, 0xA000)


def test_font_missing_glyph_warns():
    """日本語に欧文フォントを指定すると豆腐になるので warning"""
    font = _latin_only_font()
    p = _mk()
    text("日本語", size=60, border=3, font=font).time(2)
    findings = p.audit(quiet=True)
    assert set(_codes(findings)) == {"font-missing-glyph"}
    assert findings[0]["severity"] == "warning" and "豆腐" in findings[0]["message"]


def test_latin_text_with_latin_font_passes():
    """欧文フォント×欧文テキストは検査対象外（CJKを含まない）"""
    font = _latin_only_font()
    p = _mk()
    text("Hello", size=60, border=3, font=font).time(2)
    assert "font-missing-glyph" not in _codes(p.audit(quiet=True))


def test_default_font_covers_japanese():
    """既定の日本語フォントでは報告されない"""
    _default_font_coverage()
    p = _mk()
    text("日本語", size=60, border=3).time(2)
    assert "font-missing-glyph" not in _codes(p.audit(quiet=True))


def test_unparsable_font_is_not_reported(tmp_path):
    """cmap を読めないフォントは判定不能なので報告しない（過検出を避ける）"""
    from scriptvedit.audit import _font_coverage
    broken = tmp_path / "broken.ttf"
    broken.write_bytes(b"not a font at all")
    assert _font_coverage(str(broken)) is None
    p = _mk()
    text("日本語", size=60, border=3, font=str(broken)).time(2)
    assert "font-missing-glyph" not in _codes(p.audit(quiet=True))


# --- render の audit サマリ -------------------------------------------------

def test_render_prints_audit_summary(tmp_path, capsys):
    """strict でなくても render が audit を回して1行サマリを出す"""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い環境")
    layer = tmp_path / "summary_layer.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "text('裸の文字', size=60).time(1)\n",  # 縁取りなし→warning 1件
        encoding="utf-8")
    p = _mk(width=320, height=180)
    p.layer(str(layer))
    p.render(str(tmp_path / "summary.mp4"))
    out = capsys.readouterr().out
    assert "[audit] warning 1" in out and "p.audit()" in out


def test_render_survives_audit_failure(tmp_path, capsys, monkeypatch):
    """audit が例外を投げても本レンダは失敗させない（サマリは補助機能）"""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い環境")

    def _boom(_project):
        raise RuntimeError("audit が壊れた")

    monkeypatch.setattr("scriptvedit.audit.audit_project", _boom)
    layer = tmp_path / "boom_layer.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "text('本文', size=60, border=3).time(1)\n", encoding="utf-8")
    p = _mk(width=320, height=180)
    p.layer(str(layer))
    out_path = tmp_path / "boom.mp4"
    p.render(str(out_path))
    assert out_path.exists()
    out = capsys.readouterr().out
    assert "[audit]" not in out and "完了:" in out


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
