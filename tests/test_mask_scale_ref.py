# -*- coding: utf-8 -*-
"""mask / mask_wipe のマスク寸法合わせ（scale=rw:rh）と色空間タグ。

- FFmpeg 8 で deprecated の scale2ref を `scale=rw:rh`（第2入力を寸法の基準にする）へ
  置き換えた。全フレームの画素が scale2ref と一致することを実測で固定する。
- マスク（RGB の PNG）由来の colorspace=gbr のタグが下流へ伝わり、透明な下地へ重ねる
  レイヤーキャッシュ（VP9 yuva420p）のエンコーダが
  「SRGB color space requires profile 1 or 3」で落ちていた。グレーへ直した直後に
  setparams=colorspace=unknown でタグを外す（filters/video.py の _MASK_GRAY）。
"""

import hashlib
import shutil
import subprocess
import types

import pytest

import scriptvedit as sv
from scriptvedit import asset, mask, mask_wipe
from scriptvedit.context import _exec_stack, activate, current_project
from scriptvedit.filters import video as video_mod
from scriptvedit.filters.video import _build_effect_filters

_NO_FFMPEG = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
needs_ffmpeg = pytest.mark.skipif(_NO_FFMPEG, reason="ffmpeg/ffprobe が無い環境")


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


def _asset_or_skip(rel):
    try:
        return asset(rel)
    except FileNotFoundError:
        pytest.skip(f"素材 {rel} が無い環境")


def _chain(effect, dur=1):
    obj = types.SimpleNamespace(effects=[effect], transforms=[], media_type="image",
                                source="x.png")
    filters, _pad = _build_effect_filters(obj, 0, dur)
    return ",".join(filters)


def _old_chain(new):
    """置き換え前（scale2ref・タグそのまま）の書き方へ戻す"""
    p = "fxe0"
    old = new.replace(f"alphaextract,split[{p}oa][{p}oar]", f"alphaextract[{p}oa]")
    old = old.replace(f"[{p}mi][{p}oar]scale=rw:rh[{p}ms]",
                      f"[{p}mi][{p}oa]scale2ref[{p}ms][{p}oa2]")
    old = old.replace(video_mod._MASK_GRAY, "format=gray")
    old = old.replace(f"[{p}oa][{p}mg]blend", f"[{p}oa2][{p}mg]blend")
    assert old != new and "scale2ref" in old
    return old


@pytest.mark.parametrize("factory", [
    lambda m: mask(m),
    lambda m: mask_wipe(m),
    lambda m: mask_wipe(m, progress=lambda u: u * u),
], ids=["mask", "mask_wipe", "mask_wipe_expr"])
def test_filter_uses_scale_with_ref_and_untags_colorspace(factory):
    chain = _chain(factory(_asset_or_skip("images/mask_gradient.png")))
    assert "scale2ref" not in chain
    assert "alphaextract,split[fxe0oa][fxe0oar]" in chain
    assert "[fxe0mi][fxe0oar]scale=rw:rh[fxe0ms]" in chain
    assert "[fxe0ms]format=gray,setparams=colorspace=unknown[fxe0mg]" in chain
    assert "[fxe0oa][fxe0mg]blend=" in chain


def _render_md5(src, chain, seconds=1):
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "warning",
         "-loop", "1", "-framerate", "30", "-t", str(seconds), "-i", src,
         "-filter_complex", f"[0:v]{chain}[o]", "-map", "[o]",
         "-t", str(seconds), "-f", "rawvideo", "-pix_fmt", "bgra", "-"],
        capture_output=True, timeout=120)
    err = proc.stderr.decode("utf-8", "replace")
    assert proc.returncode == 0, err[-800:]
    assert len(proc.stdout) > 0
    return hashlib.md5(proc.stdout).hexdigest(), err


@needs_ffmpeg
@pytest.mark.parametrize("factory, mask_img", [
    (lambda m: mask(m), "images/mask_gradient.png"),
    (lambda m: mask_wipe(m), "images/mask_gradient.png"),
    (lambda m: mask_wipe(m, progress=lambda u: u * u), "images/mask_circle.png"),
], ids=["mask", "mask_wipe", "mask_wipe_expr"])
def test_pixels_match_scale2ref_on_every_frame(factory, mask_img):
    """全フレームの bgra が scale2ref のときと一致し、deprecated 警告が消える"""
    src = _asset_or_skip("images/shape_badge.png")
    new = _chain(factory(_asset_or_skip(mask_img)))
    new_md5, new_err = _render_md5(src, new)
    old_md5, old_err = _render_md5(src, _old_chain(new))
    assert new_md5 == old_md5
    assert "scale2ref is deprecated" in old_err      # 置き換えの根拠
    assert "deprecated" not in new_err, new_err


@needs_ffmpeg
def test_layer_cache_make_with_live_mask_wipe(tmp_path):
    """live の mask_wipe を含むレイヤーを既定品質（VP9 yuva420p）でキャッシュ生成できる"""
    src = _asset_or_skip("images/shape_badge.png")
    msk = _asset_or_skip("images/mask_gradient.png")
    layer = tmp_path / "l_maskwipe.py"
    # -mask_wipe（policy=off）: チェックポイントへ焼かず、レイヤーキャッシュのグラフで直接掛ける
    layer.write_text(
        "from scriptvedit import *\n"
        f"o = Object({src!r})\n"
        f"o.time(0.5) <= -mask_wipe({msk!r})\n", encoding="utf-8")
    p = sv.Project()
    p.configure(width=320, height=180, fps=30, background_color="black")
    p.layer(str(layer), cache="make")
    out = tmp_path / "out.mp4"
    p.render(str(out), timeout=300)
    assert out.exists() and out.stat().st_size > 0
    # 総尺が鍵に入るので、パスはレンダの後（尺の確定後）に引く
    cache_path = p._layer_cache_paths_for(p._layer_specs[0])[0]
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=codec_name,color_space", "-of", "csv=p=0", str(cache_path)],
        capture_output=True, text=True, timeout=60).stdout.strip()
    assert probe.startswith("vp9"), probe
    assert "gbr" not in probe, probe
