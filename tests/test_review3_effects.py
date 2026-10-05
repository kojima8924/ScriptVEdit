# -*- coding: utf-8 -*-
"""外部レビュー3の時間順序・寸法・終端入力署名の回帰。"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from scriptvedit import Object, Project, opacity, scale, speed, trim
from scriptvedit.cache import _op_fingerprint_str, _op_prefix_fingerprint, _sig_key
from scriptvedit.filters.video import _build_video_pre_filters


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """実素材とキャッシュをテスト専用の作業場所へ隔離する。"""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg / ffprobe が無い環境")
    image = pytest.importorskip("PIL.Image", reason="Pillow が無い環境")
    monkeypatch.chdir(tmp_path)
    image.new("RGBA", (64, 36), "red").save("red.png")
    image.new("RGBA", (64, 36), "blue").save("blue.png")
    return tmp_path


def _project(code):
    Path("layer.py").write_text("from scriptvedit import *\n" + code,
                                encoding="utf-8", newline="\r\n")
    project = Project()
    project.configure(width=64, height=36, fps=10)
    project.layer(str(Path("layer.py").resolve()))
    return project


def _run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, timeout=30)


def _probe(path):
    return json.loads(_run(["ffprobe", "-v", "error", "-show_streams",
                            "-of", "json", str(path)]).stdout)["streams"][0]


def test_live_time_before_trim_all_off_preserves_order(workspace):
    """全 off は分割せず、speed の後に trim する元の記述順で処理する。"""
    p = Project()
    p.configure(width=64, height=36, fps=10)
    obj = Object("clip.mkv").time(1)
    obj._generated_length = 2
    obj <= speed(2) & -trim(start=0.1, duration=0.5)
    assert p._plan_object_checkpoints(obj) is None
    filters = _build_video_pre_filters(obj)
    assert filters.index("setpts=PTS/2.0") < filters.index("trim=start=0.1:duration=0.5")


def test_trim_before_live_time_is_still_supported(workspace):
    """元から順序を保存できる trim→speed の計画は維持する。"""
    p = Project()
    p.configure(width=64, height=36, fps=10)
    obj = Object("clip.mkv").time(1)
    obj._generated_length = 2
    obj <= trim(start=0.1, duration=0.5) & speed(2)
    plan = p._plan_object_checkpoints(obj)
    assert [op.name for op in plan["final"]["effects"]] == ["speed"]
    cmd = plan["steps"][0]["build_cmd"]()
    assert "trim=start=0.1:duration=0.5" in cmd[cmd.index("-vf") + 1]


@pytest.mark.parametrize("effects,expected", [
    ("scale(2) & scale(2)", (256, 144)),
    ("scale(0.5) & scale(2)", (64, 36)),
    ("outline(width=2) & scale(2)", (136, 80)),
    ("rotate_to(deg=30) & scale(2)", (146, 146)),
    ("drop_shadow(dx=0, dy=0, blur=1) & scale(2)", (140, 84)),
])
def test_scale_uses_previous_effect_canvas(workspace, effects, expected):
    """実FFmpegの出力寸法とcold/warm計画を確かめ、pad/copyを維持する。"""
    project = _project(f'Object("red.png").time(1) <= ({effects})\n')
    cold = project.render("out.mp4", dry_run=True)
    path, command = next(iter(cold["cache"].items()))
    vf = command[command.index("-vf") + 1]
    assert f"pad={expected[0]}:{expected[1]}:" in vf
    assert ":eval=frame,copy" in vf
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _run(command)
    stream = _probe(path)
    assert (stream["width"], stream["height"]) == expected
    warm = project.render("out.mp4", dry_run=True)
    assert cold == warm
    if effects.startswith("scale"):
        raw = _run(["ffmpeg", "-v", "error", "-i", path, "-frames:v", "1",
                    "-f", "rawvideo", "-pix_fmt", "rgba", "-"]).stdout
        # 一様な赤の2段 scale は透明な余白を足さず、全画素を赤のまま保つ。
        assert raw == bytes((255, 0, 0, 255)) * (expected[0] * expected[1])


@pytest.mark.parametrize("compute", [False, True])
def test_changed_scale_canvas_invalidates_checkpoint_and_compute(
        workspace, monkeypatch, compute):
    """旧版で成功していた縮小→拡大も、旧pad寸法のキャッシュを再利用しない。"""
    suffix = '.compute(duration=1).time(1)' if compute else '.time(1)'
    code = f'(Object("red.png") <= (scale(0.5) & scale(2))){suffix}\n'
    current = _project(code).render("out.mp4", dry_run=True)
    # 旧鍵の組み方だけを再現し、同じ出力パスへ古い動画が残らないことを確かめる。
    legacy = lambda ops: _sig_key([f"{typ}:{_op_fingerprint_str(op)}"
                                   for typ, op in ops])
    monkeypatch.setattr("scriptvedit.cache._op_prefix_fingerprint", legacy)
    monkeypatch.setattr("scriptvedit.objects._op_prefix_fingerprint", legacy)
    old = _project(code).render("out.mp4", dry_run=True)
    assert list(current["cache"]) != list(old["cache"])


def test_unchanged_scale_chains_keep_existing_keys():
    """単独scale・不透明度との併用には無関係な版を足さない。"""
    for effects in ([scale(2)], [opacity(0.5), scale(2)]):
        ops = [("effect", effect) for effect in effects]
        legacy = _sig_key([f"{typ}:{_op_fingerprint_str(op)}" for typ, op in ops])
        assert _op_prefix_fingerprint(ops) == legacy


@pytest.mark.parametrize("terminal", ["morph_to", "assemble_from"])
def test_generated_terminal_target_dry_run_cold_warm_match(workspace, terminal):
    """computeで生成する終端入力は生成前後で鍵と全コマンドが一致する。"""
    code = ('target = (Object("blue.png") <= resize(sx=0.5, sy=0.5)).compute()\n'
            f'Object("red.png").time(1) <= {terminal}(target)\n')
    project = _project(code)
    cold = project.render("out.mp4", dry_run=True)
    path, command = next((path, cmd) for path, cmd in cold["cache"].items()
                         if "/compute/" in path.replace("\\", "/"))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _run(command)
    assert project.render("out.mp4", dry_run=True) == cold


def test_identity_scale_on_odd_source_updates_canvas_and_key(workspace):
    """1倍scaleでも奇数画像の偶数padは寸法を変え、次段の鍵にも影響する。"""
    image = pytest.importorskip("PIL.Image", reason="Pillow が無い環境")
    image.new("RGBA", (65, 37), "red").save("odd.png")
    project = _project('Object("odd.png").time(1) <= (scale(1) & scale(2))\n')
    cold = project.render("out.mp4", dry_run=True)
    path, command = next(iter(cold["cache"].items()))
    vf = command[command.index("-vf") + 1]
    assert "pad=66:38:" in vf
    assert "pad=132:76:" in vf
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _run(command)
    stream = _probe(path)
    assert (stream["width"], stream["height"]) == (132, 76)
    assert project.render("out.mp4", dry_run=True) == cold
    ops = [("effect", scale(1)), ("effect", scale(2))]
    legacy = _sig_key([f"{typ}:{_op_fingerprint_str(op)}" for typ, op in ops])
    assert _op_prefix_fingerprint(ops) != legacy
