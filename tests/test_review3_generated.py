# -*- coding: utf-8 -*-
"""外部レビュー3: 生成物の尺・サブパラメータ・透過デコードの回帰。"""
import shutil
import subprocess
from pathlib import Path

import pytest

from scriptvedit import Project
import scriptvedit.objects as objects_module
from scriptvedit.layercache import _layer_cache_is_fresh


@pytest.fixture
def generated_env(tmp_path, monkeypatch):
    """小さい実素材と独立したキャッシュで cold / warm を確かめる。"""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg / ffprobe が必要です")
    image = pytest.importorskip("PIL.Image", reason="Pillow が無い環境")
    monkeypatch.chdir(tmp_path)
    image.new("RGBA", (64, 36), "red").save("red.png")
    image.new("RGBA", (64, 36), "blue").save("blue.png")
    return tmp_path


def _layer(name, body):
    path = Path(name).resolve()
    path.write_text("from scriptvedit import *\n" + body,
                    encoding="utf-8", newline="\r\n")
    return str(path)


def _project(layer, *, cache="off"):
    project = Project()
    project.configure(width=64, height=36, fps=10)
    project.layer(layer, cache=cache)
    return project


def _ff(*args):
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)],
        capture_output=True, check=True, timeout=60)


def _sub_parent(*, auto_time=False, params=False):
    body = 'Object("red.png").time(2)\n'
    if params:
        body = ('from scriptvedit.context import current_project\n'
                'Object("red.png").time(2) <= move('
                'x=current_project().param("x", 0.2), y=0.5)\n')
    sub = _layer("sub.py", body)
    return _layer(
        "parent.py", 'sub=Project()\n'
        'sub.configure(width=64,height=36,fps=10)\n'
        f'sub.layer({sub!r})\n'
        + ('Object.from_project(sub).time()\n' if auto_time else
           'Object.from_project(sub).time(2)\n'))


def test_subproject_params_change_key_and_parent_cache(generated_env, monkeypatch):
    """指摘3: 子の解決値変更は子の鍵と親レイヤーの鮮度へ届く。"""
    layer = _sub_parent(params=True)
    monkeypatch.setenv("SCRIPTVEDIT_PARAM_x", "0.2")
    p = _project(layer, cache="make")
    cold = p.render("out.mp4", dry_run=True)
    p.render("out.mp4")
    assert p.render("out.mp4", dry_run=True) == cold
    original_key = next(k for k in cold["cache"] if "subproject" in k)
    warm = _project(layer, cache="auto")
    warm.render("out.mp4", dry_run=True)
    assert _layer_cache_is_fresh(warm, warm._layer_specs[0])
    monkeypatch.setenv("SCRIPTVEDIT_PARAM_x", "0.8")
    changed = _project(layer, cache="auto")
    plan = changed.render("out.mp4", dry_run=True)
    new_key = next(k for k in plan["cache"] if "subproject" in k)
    assert original_key != new_key
    assert not _layer_cache_is_fresh(changed, changed._layer_specs[0])
    # 異なる文字列表記でも解決済み float が同じなら鍵を分裂させない。
    monkeypatch.setenv("SCRIPTVEDIT_PARAM_x", "0.80")
    equivalent = _project(layer).render("out.mp4", dry_run=True)
    assert new_key in equivalent["cache"]


def test_generated_video_sequence_cold_warm_and_pixels(generated_env):
    """指摘9: compute の計画尺で連結し、cold / warm の全コマンドが一致する。"""
    layer = _layer("sequence.py", 'a=Object("red.png").compute(duration=2)\n'
                   'b=Object("blue.png").compute(duration=3)\n'
                   'video_sequence(a,b,t_dur=0.5).time()\n')
    p = _project(layer)
    cold = p.render("out.mp4", dry_run=True)
    assert p.duration == 4.5
    p.render("out.mp4")
    assert p.render("out.mp4", dry_run=True) == cold
    assert p.duration == 4.5
    # 完成映像の先頭と末尾を確認する（尺だけ合った黒フレームを見逃さない）。
    for at, channel in ((0, 0), (4, 2)):
        raw = _ff("-ss", at, "-i", "out.mp4", "-frames:v", 1,
                  "-f", "rawvideo", "-pix_fmt", "rgb24", "-").stdout
        pixel = raw[(18 * 64 + 32) * 3:(18 * 64 + 32) * 3 + 3]
        assert len(pixel) == 3 and pixel[channel] > 240
        assert sum(pixel) - pixel[channel] < 10


def test_from_project_auto_time_cold_warm(generated_env):
    """指摘11: 既知の子の総尺を使い、未生成の WebM を probe しない。"""
    p = _project(_sub_parent(auto_time=True))
    cold = p.render("out.mp4", dry_run=True)
    assert p.duration == 2
    p.render("out.mp4")
    assert p.render("out.mp4", dry_run=True) == cold
    assert p.duration == 2


@pytest.mark.parametrize("codec", ["libvpx-vp9", "libvpx"])
def test_compute_image_preserves_webm_alpha(generated_env, monkeypatch, codec):
    """指摘15: 先頭フレーム compute も共通デコーダで alpha を保持する。"""
    image = pytest.importorskip("PIL.Image", reason="Pillow が無い環境")
    image.new("RGBA", (64, 36), (255, 0, 0, 0)).save("transparent.png")
    _ff("-loop", 1, "-i", "transparent.png", "-t", 1,
        "-c:v", codec, "-pix_fmt", "yuva420p", "-auto-alt-ref", 0,
        "alpha.webm")
    p = _project(_layer("alpha.py", 'Object("alpha.webm").compute().time(1)\n'))
    with monkeypatch.context() as patch:
        patch.setattr(objects_module, "_decoder_input_args",
                      lambda source, media_type, fps: ["-i", source])
        old = p.render("out.mp4", dry_run=True)
        old_key, old_cmd = next(iter(old["cache"].items()))
        Path(old_key).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(old_cmd, capture_output=True, check=True, timeout=60)
    cold = p.render("out.mp4", dry_run=True)
    key, cmd = next(iter(cold["cache"].items()))
    assert key != old_key
    Path(key).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, capture_output=True, check=True, timeout=60)
    with image.open(key) as frame:
        assert frame.convert("RGBA").getpixel((32, 18))[3] == 0
    assert p.render("out.mp4", dry_run=True) == cold


def test_subproject_sequence_audio_metadata_cold_warm(generated_env):
    """指摘9: 子の生成動画は未生成でも音声ありと計画できる。"""
    _ff("-f", "lavfi", "-i", "color=red:s=64x36:r=10:d=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-c:v", "ffv1", "-c:a", "pcm_s16le", "av.mkv")
    sub = _layer("sub_av.py", 'Object("av.mkv").time(1)\n')
    layer = _layer("sequence_av.py", 'sub=Project()\n'
                   'sub.configure(width=64,height=36,fps=10)\n'
                   f'sub.layer({sub!r})\n'
                   'a=Object.from_project(sub)\n'
                   'video_sequence(a,a,t_dur=0.2).time()\n')
    p = _project(layer)
    cold = p.render("out.mp4", dry_run=True)
    assert p.duration == 1.8
    xfade = next(cmd for key, cmd in cold["cache"].items() if "xfade" in key)
    assert "acrossfade" in xfade[xfade.index("-filter_complex") + 1]
    p.render("out.mp4")
    assert p.render("out.mp4", dry_run=True) == cold
