# -*- coding: utf-8 -*-
"""p.thumbnail() のチャンク方式（監査 項目19）の回帰テスト。

旧実装は全尺グラフを組み、出力側 -ss で at までのフレームを捨てていた。
出力側 -ss はフィルタ通過後に捨てるだけなので合成は t=0 から全部走り、
終盤のサムネイルほど遅くなる（1280x720/字幕20本/40秒で実測 16.8s → 0.24s）。
ここでは「絵が変わっていないこと」と「無駄な入力を通していないこと」を固定する。
"""
import shutil

import pytest

import scriptvedit as sv
from scriptvedit.preview import _build_thumbnail_cmd, _thumbnail_frame_index

_NO_FFMPEG = shutil.which("ffmpeg") is None


def _project(tmp_path, fps=10, duration=3.0):
    layer = tmp_path / "l_thumb.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "text('まえ', x=0.3, y=0.4, size=24, color='white').time(1)\n"
        "(text('あと', x=0.7, y=0.6, size=24, color='yellow') @ 2).time(1)\n",
        encoding="utf-8")
    p = sv.Project()
    p.configure(width=64, height=36, fps=fps, duration=duration,
                background_color="black")
    p.layer(str(layer))
    return p


def test_frame_index_uses_ceil_and_clamps_to_last_frame(tmp_path):
    """丸めは storyboard と同じ ceil（旧 -ss の「at 以降の最初のフレーム」）"""
    p = _project(tmp_path, fps=10, duration=3.0)
    p.duration = 3.0
    assert _thumbnail_frame_index(p, 0.0) == 0
    assert _thumbnail_frame_index(p, 0.5) == 5
    assert _thumbnail_frame_index(p, 0.51) == 6   # 格子に載らない値は切り上げ
    assert _thumbnail_frame_index(p, 2.999) == 29  # 実在する最終フレームへ
    assert _thumbnail_frame_index(p, 99) == 29


def test_thumbnail_command_has_no_output_seek_and_skips_invisible_inputs(
        tmp_path):
    """-ss を使わず1フレームだけ出し、その時刻に見えない素材は入力にしない"""
    p = _project(tmp_path, fps=10, duration=3.0)
    p.render(str(tmp_path / "plan.mp4"), dry_run=True)
    k0, cmd = _build_thumbnail_cmd(p, 2.5, str(tmp_path / "t.png"))
    assert k0 == 25
    assert "-ss" not in cmd
    assert cmd[-5:] == ["-frames:v", "1", "-update", "1",
                        str(tmp_path / "t.png")]
    # 背景 + t=2.5 に見えるテキスト1つだけ（0〜1秒のテキストは入力にしない）
    assert cmd.count("-i") == 2


def _legacy_thumbnail_cmd(project, at, out):
    """旧実装（全尺グラフ + 出力側 -ss）のコマンドを再現する。

    thumb 形式のエンコード引数は共通のまま、末尾の `-t <総尺> <出力>` を
    旧来の `-ss <at> -frames:v 1 -update 1 <出力>` へ差し替える。
    """
    fmt = {"kind": "thumb", "alpha": False, "has_audio": False,
           "output_path": out}
    original = project._resolve_output_format
    project._resolve_output_format = lambda path: fmt
    try:
        cmd = project._build_ffmpeg_cmd(out)
    finally:
        project._resolve_output_format = original
    assert cmd[-3] == "-t", cmd[-4:]
    return cmd[:-3] + ["-ss", str(float(at)), "-frames:v", "1",
                       "-update", "1", out]


@pytest.mark.skipif(_NO_FFMPEG, reason="ffmpeg がありません")
def test_thumbnail_pixels_match_legacy_output_seek(tmp_path):
    """チャンク方式のサムネイルが旧 -ss 方式と同一の絵になること"""
    image_module = pytest.importorskip("PIL.Image")
    from scriptvedit.ffmpeg import _run_ffmpeg
    from scriptvedit.preview import _prepare_thumbnail_graph

    for at in (0.0, 1.0, 2.5):
        p_old = _project(tmp_path, fps=10, duration=3.0)
        _prepare_thumbnail_graph(p_old)
        old_out = str(tmp_path / f"old_{at}.png")
        _run_ffmpeg(_legacy_thumbnail_cmd(p_old, at, old_out), timeout=300)

        p_new = _project(tmp_path, fps=10, duration=3.0)
        new_out = str(tmp_path / f"new_{at}.png")
        p_new.thumbnail(at, new_out)

        with image_module.open(old_out) as a, image_module.open(new_out) as b:
            assert a.convert("RGBA").tobytes() == b.convert("RGBA").tobytes(), (
                f"at={at} のサムネイルが旧方式と一致しません")


@pytest.mark.skipif(_NO_FFMPEG, reason="ffmpeg がありません")
def test_thumbnail_near_end_still_writes_a_frame(tmp_path):
    """フレーム格子の隙間（最終フレームより後の時刻）でも1枚出る。

    旧 -ss 方式は「at 以降の最初のフレーム」を待つため、最終フレーム時刻
    (2.9s) と総尺(3.0s) の間を指定すると **空のファイル** を書いて成功扱いに
    なっていた。新方式は実在する最終フレームへクランプして必ず1枚返す。
    """
    image_module = pytest.importorskip("PIL.Image")
    p = _project(tmp_path, fps=10, duration=3.0)
    out = tmp_path / "tail.png"
    p.thumbnail(2.95, str(out))
    assert out.stat().st_size > 0
    with image_module.open(out) as img:
        assert img.size == (64, 36)
