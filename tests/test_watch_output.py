# -*- coding: utf-8 -*-
"""watch --out: 出力ファイル（と一時ファイル）を監視しないことのテスト

出力の拡張子（.mp4 / .png 等）は監視対象でもあるため、以前は --out で監視
ディレクトリ内に書くと「出力の更新 → 変更検知 → 再実行 → 出力の更新 …」が
止まらなかった。
"""
import os
import time
import types

from scriptvedit import cli
from scriptvedit.cli import _watch_output_matcher, _watch_targets, watch


def _touch(path, data=b"x"):
    with open(path, "wb") as f:
        f.write(data)
    return str(path)


# --- 判定述語 ---------------------------------------------------------------

def test_matcher_none_without_out(tmp_path):
    assert _watch_output_matcher(str(tmp_path / "main.py"), None) is None
    assert _watch_output_matcher(str(tmp_path / "main.py"), "") is None


def test_matcher_output_and_tmp_files(tmp_path):
    script = str(tmp_path / "main.py")
    is_out = _watch_output_matcher(script, "out.mp4")
    d = str(tmp_path)
    # 出力そのもの（相対パスはスクリプトのディレクトリ基準）
    assert is_out(os.path.join(d, "out.mp4"))
    # ffmpeg._unique_tmp_path の一時ファイル（中断で残った残骸も含む）
    assert is_out(os.path.join(d, "out.tmp12345_0123abcd.mp4"))
    # 別名・別拡張子・別ディレクトリは監視を続ける
    assert not is_out(os.path.join(d, "other.mp4"))
    assert not is_out(os.path.join(d, "out.webm"))
    assert not is_out(os.path.join(d, "sub", "out.mp4"))
    assert not is_out(os.path.join(d, "out.tmp_manual.mp4"))
    assert not is_out(script)


def test_matcher_uses_real_tmp_name(tmp_path):
    """一時ファイル名の規則を実物（_unique_tmp_path）で突き合わせる"""
    from scriptvedit.ffmpeg import _unique_tmp_path
    out = os.path.join(str(tmp_path), "render", "final.mp4")
    is_out = _watch_output_matcher(str(tmp_path / "main.py"), out)
    assert is_out(out)
    assert is_out(_unique_tmp_path(out))


def test_matcher_absolute_and_subdir_out(tmp_path):
    script = str(tmp_path / "main.py")
    is_out = _watch_output_matcher(script, os.path.join("build", "x.webm"))
    assert is_out(os.path.join(str(tmp_path), "build", "x.webm"))
    assert not is_out(os.path.join(str(tmp_path), "x.webm"))
    absolute = os.path.join(str(tmp_path), "elsewhere", "y.mp4")
    assert _watch_output_matcher(script, absolute)(absolute)


def test_matcher_png_sequence(tmp_path):
    """連番 PNG 出力（frames/%04d.png）の各フレームも除外する"""
    script = str(tmp_path / "main.py")
    is_out = _watch_output_matcher(script, os.path.join("frames", "%04d.png"))
    frames = os.path.join(str(tmp_path), "frames")
    assert is_out(os.path.join(frames, "0001.png"))
    assert is_out(os.path.join(frames, "12345.png"))
    assert not is_out(os.path.join(frames, "logo.png"))
    assert not is_out(os.path.join(str(tmp_path), "0001.png"))


def test_watch_targets_skip_output(tmp_path):
    script = _touch(tmp_path / "main.py", b"")
    layer = _touch(tmp_path / "bg.py", b"")
    img = _touch(tmp_path / "logo.png")
    out = _touch(tmp_path / "out.mp4")
    tmp = _touch(tmp_path / "out.tmp999_deadbeef.mp4")
    is_out = _watch_output_matcher(script, "out.mp4")
    targets = _watch_targets(script, is_out)
    assert {script, layer, img} <= targets
    assert out not in targets and tmp not in targets
    # 述語なしなら従来どおり全部入る（除外は --out 指定時だけ）
    assert {out, tmp} <= _watch_targets(script)


# --- watch() 本体: 出力の更新で再実行が連鎖しない ------------------------------

_SCRIPT = """import os, sys, time
out = sys.argv[1]
with open("runs.log", "a") as f:
    f.write("x")
n = os.path.getsize("runs.log")
# 実行のたびに出力と一時ファイルの残骸を書き換える（mtime も確実に変える）
for path in (out, "out.tmp999_deadbeef.mp4"):
    with open(path, "wb") as f:
        f.write(b"v" * n)
    t = time.time() + n * 100
    os.utime(path, (t, t))
"""


def test_watch_does_not_rerun_on_its_own_output(tmp_path, monkeypatch, capsys):
    script = tmp_path / "main.py"
    script.write_text(_SCRIPT, encoding="utf-8")
    runs = tmp_path / "runs.log"
    touched = []

    def fake_sleep(_sec):
        # 最初のポーリングの直前にスクリプトを「編集」して再実行を1回だけ起こす
        if not touched:
            st = os.stat(script).st_mtime + 10
            os.utime(script, (st, st))
            touched.append(True)

    monkeypatch.setattr(cli, "_time", types.SimpleNamespace(
        sleep=fake_sleep, perf_counter=time.perf_counter))
    watch(str(script), out="out.mp4", interval=0.01, max_cycles=5)

    # 起動時の1回 + 編集による1回 = 2回。出力の更新では再実行されない
    # （修正前は毎ポーリングで出力の変更を検知して 1 + 5 = 6 回走っていた）
    assert runs.read_text() == "xx", capsys.readouterr().out
    assert (tmp_path / "out.mp4").exists()
    out_text = capsys.readouterr().out
    assert out_text.count("[watch] 変更検知") == 1
    assert "main.py" in out_text
