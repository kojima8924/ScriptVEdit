# -*- coding: utf-8 -*-
"""ffmpeg 失敗の診断（監査 項目9）と、沈黙する失敗の集約（項目8）の回帰テスト。

  * 全 ffmpeg 起動へ -hide_banner / -loglevel warning / -nostdin が付く
  * 失敗は FFmpegError（原因1行 + 文脈つき。コマンド全文はメッセージに載せない）
  * stderr は読み切りながら親へ流す（パイプ詰まりでのデッドロックを起こさない）
  * 長大フィルタの一時ファイルは失敗時だけ残す
  * 尺解決の握り潰しをやめ、レンダ末尾に [警告] を再掲する
"""
import os
import shutil
import subprocess

import pytest

import scriptvedit as sv
from scriptvedit.ffmpeg import (
    FFmpegError, _normalize_ffmpeg_cmd, _run_ffmpeg, _signed_exit_code,
    _stderr_excerpt)

_NO_FFMPEG = shutil.which("ffmpeg") is None


# --- 共通フラグの付与（構築側ではなく実行側で一元化されていること）---

def test_normalize_adds_diagnostic_flags_once():
    """診断フラグは1回だけ付き、二重適用しても増えない（冪等）"""
    cmd = _normalize_ffmpeg_cmd(["ffmpeg", "-y", "out.mp4"])
    assert cmd[:7] == ["ffmpeg", "-hide_banner", "-loglevel", "warning",
                       "-stats", "-nostdin", "-y"]
    assert _normalize_ffmpeg_cmd(cmd) == cmd


def test_normalize_respects_explicit_loglevel_and_non_ffmpeg():
    """明示指定の -loglevel は尊重し、ffmpeg 以外のコマンドは触らない"""
    explicit = _normalize_ffmpeg_cmd(
        ["ffmpeg", "-loglevel", "error", "-y", "out.mp4"])
    assert explicit.count("-loglevel") == 1
    assert "error" in explicit and "warning" not in explicit
    other = ["ffprobe", "-v", "error", "in.mp4"]
    assert _normalize_ffmpeg_cmd(other) == other


def test_dry_run_commands_carry_diagnostic_flags(tmp_path):
    """dry_run が返すコマンド＝実際に実行される形（スナップショットの前提）"""
    layer = tmp_path / "l.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "text('あ', x=0.5, y=0.5, size=40, color='white').time(1)\n",
        encoding="utf-8")
    p = sv.Project()
    p.configure(width=64, height=36, fps=5, duration=1)
    p.layer(str(layer))
    result = p.render(str(tmp_path / "o.mp4"), dry_run=True)
    assert result["main"][:6] == [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-stats",
        "-nostdin"]


# --- 失敗時の診断 ---

def test_signed_exit_code_converts_windows_unsigned():
    """Windows の符号なし終了コードを符号付きへ直す"""
    assert _signed_exit_code(4294967274) == -22
    assert _signed_exit_code(0xC0000005) == -1073741819
    assert _signed_exit_code(1) == 1
    assert _signed_exit_code(None) is None


def test_stderr_excerpt_prefers_error_lines():
    """抜粋は Error/Invalid/No such を優先し、無ければ末尾を返す"""
    tail = ["frame=1", "No such filter: 'foo'", "frame=2"]
    assert _stderr_excerpt(tail) == ["No such filter: 'foo'"]
    plain = ["a", "b", "c"]
    assert _stderr_excerpt(plain, limit=2) == ["b", "c"]


@pytest.mark.skipif(_NO_FFMPEG, reason="ffmpeg がありません")
def test_run_ffmpeg_failure_reports_cause_without_full_command(tmp_path):
    """失敗は FFmpegError。原因1行と文脈は載るが、コマンド全文は載らない"""
    out = str(tmp_path / "x.mp4")
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i",
           "color=c=black:s=64x36:d=0.2:r=5",
           "-vf", "this_filter_does_not_exist=1", "-t", "0.2", out]
    with pytest.raises(FFmpegError) as exc:
        _run_ffmpeg(cmd, timeout=120, context="テスト用の失敗")
    message = str(exc.value)
    assert "テスト用の失敗" in message
    assert "this_filter_does_not_exist" in message  # 原因の1行
    assert "color=c=black" not in message           # コマンド全文は載せない
    assert exc.value.cmd[0] == "ffmpeg"             # 全文は属性から取れる
    assert "color=c=black:s=64x36:d=0.2:r=5" in exc.value.cmd
    assert exc.value.returncode != 0
    assert exc.value.stderr_tail


@pytest.mark.skipif(_NO_FFMPEG, reason="ffmpeg がありません")
def test_run_ffmpeg_keeps_filter_script_only_on_failure(tmp_path, monkeypatch):
    """長大フィルタの一時ファイルは失敗時だけ残し、パスをメッセージに出す"""
    import scriptvedit.ffmpeg as ffmpeg_mod
    monkeypatch.setattr(ffmpeg_mod, "_FILTER_SCRIPT_THRESHOLD", 8)
    out = str(tmp_path / "x.mp4")
    bad = ["ffmpeg", "-y", "-f", "lavfi", "-i",
           "color=c=black:s=64x36:d=0.2:r=5",
           "-vf", "this_filter_does_not_exist=1,null,null", "-t", "0.2", out]
    with pytest.raises(FFmpegError) as exc:
        _run_ffmpeg(bad, timeout=120)
    kept = [a for a in exc.value.cmd if a.endswith(".txt")]
    assert kept and os.path.exists(kept[0])
    assert kept[0] in str(exc.value)
    with open(kept[0], encoding="utf-8") as f:
        assert "this_filter_does_not_exist" in f.read()
    os.remove(kept[0])

    good = ["ffmpeg", "-y", "-f", "lavfi", "-i",
            "color=c=black:s=64x36:d=0.2:r=5",
            "-vf", "null,null,null", "-t", "0.2", out]
    seen = {}
    real = ffmpeg_mod._spawn_ffmpeg

    def spy(run_cmd, timeout):
        seen["paths"] = [a for a in run_cmd if str(a).endswith(".txt")]
        return real(run_cmd, timeout)

    monkeypatch.setattr(ffmpeg_mod, "_spawn_ffmpeg", spy)
    _run_ffmpeg(good, timeout=120)
    assert seen["paths"] and not os.path.exists(seen["paths"][0])


@pytest.mark.skipif(_NO_FFMPEG, reason="ffmpeg がありません")
def test_run_ffmpeg_drains_large_stderr_without_deadlock(tmp_path, capfd):
    """大量の stderr を出すコマンドでもブロックせず完走し、親へ流れる"""
    out = str(tmp_path / "big.mp4")
    cmd = ["ffmpeg", "-y", "-loglevel", "debug", "-f", "lavfi",
           "-i", "color=c=black:s=64x36:d=1:r=30", "-t", "1", out]
    _run_ffmpeg(cmd, timeout=180)
    assert os.path.getsize(out) > 0
    captured = capfd.readouterr()
    assert len(captured.err) > 4096  # パイプバッファを超える量を読み切っている


def test_run_ffmpeg_timeout_still_raises_timeout_expired(monkeypatch):
    """タイムアウトは従来どおり subprocess.TimeoutExpired（FFmpegErrorにしない）"""
    import scriptvedit.ffmpeg as ffmpeg_mod
    monkeypatch.setattr(ffmpeg_mod, "_check_ffmpeg_version", lambda: None)

    def fake_spawn(run_cmd, timeout):
        raise subprocess.TimeoutExpired(run_cmd, timeout)

    monkeypatch.setattr(ffmpeg_mod, "_spawn_ffmpeg", fake_spawn)
    with pytest.raises(subprocess.TimeoutExpired):
        _run_ffmpeg(["ffmpeg", "-y", "out.mp4"], timeout=1)


def test_render_failure_prints_input_table(tmp_path, monkeypatch, capsys):
    """本レンダ失敗時は入力番号→素材の対応表を出す（[fxN] から辿れるように）"""
    import scriptvedit.project as project_module

    def boom(cmd, timeout=None, context=None):
        raise FFmpegError("失敗", cmd=cmd, returncode=-22, context=context)

    monkeypatch.setattr(project_module, "_run_ffmpeg", boom)
    layer = tmp_path / "l.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "text('あ', x=0.5, y=0.5, size=40, color='white').time(1)\n",
        encoding="utf-8")
    p = sv.Project()
    p.configure(width=64, height=36, fps=5, duration=1)
    p.layer(str(layer))
    with pytest.raises(FFmpegError):
        p.render(str(tmp_path / "o.mp4"))
    out = capsys.readouterr().out
    assert "入力の対応表" in out
    assert "入力1:" in out
    # 既定の実行前表示は1行サマリ（全文は SCRIPTVEDIT_VERBOSE=1）
    assert "実行: ffmpeg 入力" in out
    assert "-filter_complex" not in out


# --- 項目8: 沈黙する失敗の集約 ---

def test_missing_duration_raises_on_real_render_but_not_dry_run(
        tmp_path, monkeypatch, capsys):
    """尺を測れない素材は実レンダで明示エラー、dry_run は警告つきで続行"""
    import scriptvedit.project as project_module

    layer = tmp_path / "l.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "Object('missing_movie.mp4')\n",
        encoding="utf-8")
    p = sv.Project()
    p.configure(width=64, height=36, fps=5, duration=1)
    p.layer(str(layer))
    monkeypatch.setattr(project_module.Project, "_probe_media",
                        lambda self, path: None)
    result = p.render(str(tmp_path / "o.mp4"), dry_run=True)
    assert result["main"]
    assert any("尺を取得できない" in w for w in p._render_warnings)

    def boom(cmd, timeout=None, context=None):  # ここまで来たら失敗（=握り潰し）
        raise AssertionError("尺不明のままレンダが始まった")

    monkeypatch.setattr(project_module, "_run_ffmpeg", boom)
    with pytest.raises(RuntimeError, match="尺を解決できません"):
        p.render(str(tmp_path / "o.mp4"))


def test_render_reprints_warnings_at_the_end(tmp_path, monkeypatch, capsys):
    """レンダ末尾に [警告] ブロックで再掲する（出力末尾しか読まなくても届く）"""
    import scriptvedit.project as project_module

    def fake_run(cmd, timeout=None, context=None):
        with open(cmd[-1], "wb") as f:
            f.write(b"x")

    monkeypatch.setattr(project_module, "_run_ffmpeg", fake_run)
    layer = tmp_path / "l.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "text('あ', x=0.5, y=0.5, size=40, color='white').time(1)\n",
        encoding="utf-8")
    p = sv.Project()
    p.configure(width=64, height=36, fps=5, duration=1)
    p.layer(str(layer))
    project_module._warn(p, "テスト用の警告です", sticky=True)
    p.render(str(tmp_path / "o.mp4"))
    out = capsys.readouterr().out
    assert "[警告] 1件" in out
    assert "テスト用の警告です" in out


def test_render_without_warnings_prints_no_warning_block(tmp_path, monkeypatch,
                                                         capsys):
    """警告0件なら [警告] 行ごと出さない"""
    import scriptvedit.project as project_module

    def fake_run(cmd, timeout=None, context=None):
        with open(cmd[-1], "wb") as f:
            f.write(b"x")

    monkeypatch.setattr(project_module, "_run_ffmpeg", fake_run)
    layer = tmp_path / "l.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "text('あ', x=0.5, y=0.5, size=40, color='white').time(1)\n",
        encoding="utf-8")
    p = sv.Project()
    p.configure(width=64, height=36, fps=5, duration=1)
    p.layer(str(layer))
    p.render(str(tmp_path / "o.mp4"))
    assert "[警告]" not in capsys.readouterr().out
