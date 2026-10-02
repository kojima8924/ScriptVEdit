# -*- coding: utf-8 -*-
"""終端フレーム生成 Effect（morph_to / explode_to / assemble_from）と、
焼いた Effect の「最後のコマ」の回帰テスト。

  (a) 焼く枚数: 動画チェックポイントは閉区間の enable 窓を覆う枚数
      （ceil(fps*尺 + 0.5)）を焼く。以前は round(fps*尺) 枚で、窓の最後の1枚
      （開始 + 尺ちょうどのフレーム）が背景になった。
  (b) delay / duration: 動く区間のコマだけを作り、前後は最初・最後のコマを複製する。
  (c) 粒子: expand の自動・fade=False・toward / from_point。
  (d) sdf モーフ: 大きさの違う2枚の fit、実質クロスフェードになる組の診断。
  (e) describe: **params の各キーが実装と同じ既定値で載る。
  (f) 実レンダ: 画素で確かめる（tests/test_enable_float_fuzz.py の流儀）。
"""
import inspect
import os
import shutil
import subprocess

import pytest

import scriptvedit as sv
from scriptvedit import cache as cache_mod
from scriptvedit.checkpoint import (
    _bake_frame_count, _bake_t_arg, _build_checkpoint_video_cmd,
    _build_morph_webm_cmd, _morph_frame_count, _terminal_frame_plan)
from scriptvedit.context import _exec_stack, activate, current_project
from scriptvedit.manifest import describe
from scriptvedit.state import _TERMINAL_TIMING_KEYS


@pytest.fixture(autouse=True)
def _restore_project_globals():
    """各テスト後に Project の暗黙登録先と実行スタックを戻す"""
    old_current = current_project()
    old_stack = list(_exec_stack)
    activate(None)
    _exec_stack[:] = []
    try:
        yield
    finally:
        activate(old_current)
        _exec_stack[:] = old_stack


class _Op:
    """params だけを持つ Effect の代役"""

    def __init__(self, name="explode_to", **params):
        self.name = name
        self.params = params


# --- (a) 焼く枚数 ------------------------------------------------------------

def _window_frames(start, dur, fps):
    """閉区間の窓 between(t, 開始, 開始+尺) に入る枚数（開始は最も近いフレームへ）"""
    import math
    first = math.floor(start * fps + 0.5)
    last = math.floor((start + dur) * fps + 1e-9)
    return last - first + 1


@pytest.mark.parametrize("dur", [0.05, 0.5, 1.0, 1.0166, 1.02, 1.5, 2.35, 7.0])
def test_bake_frame_count_covers_closed_window(dur):
    """開始がどこでも（格子上・半フレーム未満のずれ・半フレーム超のずれ）窓を覆う"""
    fps = 30
    n = _bake_frame_count(fps, dur)
    for k in range(0, 300):
        start = k / 97.0   # 格子から外れた開始を含む
        assert n >= _window_frames(start, dur, fps), (dur, start, n)
    # 余分に焼くのは高々1枚（尺を伸ばして覆っているわけではない）
    assert n <= _morph_frame_count(fps, dur) + 1


def test_bake_frame_count_values():
    assert _bake_frame_count(30, 1) == 31        # 窓 [0, 1] は 0..30 の 31 枚
    assert _bake_frame_count(30, 1.02) == 32
    assert _bake_frame_count(30, 0.1) == 4       # 0.1*30 = 3.0000000000000004 でも 4
    assert _bake_frame_count(30, 0.01) == 1
    assert _bake_t_arg(1, 30) == "1.033333"      # 端数を書かない
    assert _bake_t_arg(2, 60) == "2.016667"


def test_checkpoint_video_cmd_uses_bake_t(tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(b"x")
    cmd = _build_checkpoint_video_cmd(str(src), "image", [], [], "out.mkv", 1.5, 30)
    assert cmd[cmd.index("-t") + 1] == _bake_t_arg(1.5, 30) == "1.533333"


def test_checkpoint_key_carries_tail_version(monkeypatch):
    """焼く枚数の決め方を変えたので、旧形式（1枚短い）の中間物を命中させない"""
    a = cache_mod._checkpoint_cache_path("nosuch.png", [], 1.0, 30)
    monkeypatch.setattr(cache_mod, "_CHECKPOINT_TAIL_VER", "0")
    b = cache_mod._checkpoint_cache_path("nosuch.png", [], 1.0, 30)
    assert a != b
    # 静止画チェックポイント（尺なし）は枚数と無関係なので鍵は同じ
    c = cache_mod._checkpoint_cache_path("nosuch.png", [], None, None)
    monkeypatch.setattr(cache_mod, "_CHECKPOINT_TAIL_VER", "1")
    assert c == cache_mod._checkpoint_cache_path("nosuch.png", [], None, None)


# --- (b) delay / duration ----------------------------------------------------

def test_terminal_frame_plan_default_is_unchanged():
    assert _terminal_frame_plan(_Op(), 2, 30) == (0, 60, 1)
    assert _terminal_frame_plan(None, 1.05, 30) == (0, 32, 1)
    cmd = _build_morph_webm_cmd("f_%05d.png", "o.mkv", 2, 30, _Op())
    assert cmd[cmd.index("-vf") + 1] == "tpad=stop_mode=clone:stop=1"
    assert cmd[cmd.index("-frames:v") + 1] == "61"


def test_terminal_frame_plan_delay_and_duration():
    # 4 秒の Object: 1 秒待ち、1.5 秒動き、残りは最後のコマ
    plan = _terminal_frame_plan(_Op(delay=1.0, duration=1.5), 4, 30)
    assert plan == (30, 45, 46)
    assert sum(plan) == _morph_frame_count(30, 4) + 1    # 合計は変わらない
    cmd = _build_morph_webm_cmd("f_%05d.png", "o.mkv", 4, 30,
                                _Op(delay=1.0, duration=1.5))
    assert cmd[cmd.index("-vf") + 1] == (
        "tpad=start=30:start_mode=clone:stop=46:stop_mode=clone")
    assert cmd[cmd.index("-frames:v") + 1] == "121"
    # delay だけ: 残り全部で動く
    assert _terminal_frame_plan(_Op(delay=0.5), 2, 30) == (15, 45, 1)
    # duration だけ
    assert _terminal_frame_plan(_Op(duration=0.1), 2, 30) == (0, 3, 58)


@pytest.mark.parametrize("params, dur, expect", [
    (dict(duration=1 / 30), 1, (0, 2, 29)),    # 1 コマぶんの duration
    (dict(duration=0.02), 1, (0, 2, 29)),      # 1 コマ未満の duration
    (dict(delay=0.99), 1, (29, 2, 0)),         # 尺の終わりぎりぎりの delay（待ちを詰める）
    (dict(delay=0.97, duration=0.01), 1, (29, 2, 0)),
    (dict(duration=2 / 30), 1, (0, 2, 29)),
    (dict(duration=0.01), 0.01, (0, 2, 0)),    # 尺そのものが 1 コマ
])
def test_terminal_frame_plan_always_reaches_the_end(params, dur, expect):
    """生成するコマは必ず2枚以上（進行度 0 と 1）。

    以前は動く区間が1コマになる指定で、唯一のコマ（進行度 0 ＝元の絵）が
    最後のコマとして尺の終わりまで複製され、到達点に一度もならなかった。
    """
    plan = _terminal_frame_plan(_Op(**params), dur, 30)
    assert plan == expect
    assert plan[1] >= 2
    assert sum(plan) == _morph_frame_count(30, dur) + 1     # 合計は変わらない
    cmd = _build_morph_webm_cmd("f_%05d.png", "o.mkv", dur, 30, _Op(**params))
    assert cmd[cmd.index("-frames:v") + 1] == str(sum(plan))


def test_terminal_frame_plan_rejects_out_of_range():
    with pytest.raises(ValueError, match="delay"):
        _terminal_frame_plan(_Op(delay=2.0), 2, 30)
    with pytest.raises(ValueError, match="delay \\+ duration"):
        _terminal_frame_plan(_Op(delay=1.0, duration=1.5), 2, 30)


def test_timing_overflow_stops_dry_run(tmp_path):
    """尺に収まらない delay は dry_run でも同じ所で止まる"""
    from PIL import Image
    img = tmp_path / "a.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(img)
    layer = tmp_path / "l.py"
    layer.write_text(
        "from scriptvedit import *\n"
        f"Object({str(img)!r}).time(1) <= explode_to(delay=1.5)\n", encoding="utf-8")
    p = sv.Project()
    p.configure(width=64, height=36, fps=30)
    p.layer(str(layer), priority=0)
    with pytest.raises(ValueError, match="delay=1.5"):
        p.render(str(tmp_path / "o.mp4"), dry_run=True)


def test_timing_dry_run_command(tmp_path):
    """delay / duration が dry_run の粒子コマンドに現れ、鍵にも効く"""
    from PIL import Image
    img = tmp_path / "a.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(img)

    def plan(extra):
        layer = tmp_path / "l.py"
        layer.write_text(
            "from scriptvedit import *\n"
            f"Object({str(img)!r}).time(2) <= explode_to(seed=1{extra})\n",
            encoding="utf-8")
        p = sv.Project()
        p.configure(width=64, height=36, fps=30)
        p.layer(str(layer), priority=0)
        res = p.render(str(tmp_path / "o.mp4"), dry_run=True)
        (path, cmd), = [(k, v) for k, v in res["cache"].items() if "particle" in k]
        return path, cmd[cmd.index("-vf") + 1]

    base_path, base_vf = plan("")
    path, vf = plan(", delay=0.5, duration=1.0")
    assert base_vf == "tpad=stop_mode=clone:stop=1"
    assert vf == "tpad=start=15:start_mode=clone:stop=16:stop_mode=clone"
    assert path != base_path


# --- 構築時の検証 -------------------------------------------------------------

def _img(tmp_path, name="a.png", size=(8, 8), color=(255, 0, 0, 255)):
    from PIL import Image
    path = tmp_path / name
    Image.new("RGBA", size, color).save(path)
    return str(path)


@pytest.mark.parametrize("kwargs, match", [
    (dict(delay=-1), "delay"),
    (dict(delay=float("nan")), "delay"),
    (dict(duration=0), "duration"),
    (dict(duration="1"), "duration"),
    (dict(expand=-5), "expand"),
    (dict(fade=1), "fade"),
    (dict(toward=3), "toward"),
    (dict(toward=(1, float("inf"))), "toward"),
    (dict(from_point=(0, 0)), "未知のパラメータ"),     # assemble 専用のキー
    (dict(hold=True), "未知のパラメータ"),
])
def test_explode_to_rejects_bad_params(kwargs, match):
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    with pytest.raises(ValueError, match=match):
        sv.explode_to(**kwargs)


def test_assemble_from_param_keys(tmp_path):
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    p = sv.Project()
    activate(p)
    with pytest.raises(ValueError, match="未知のパラメータ"):
        sv.assemble_from(sv.Object(_img(tmp_path)), toward=(0, 0))
    eff = sv.assemble_from(sv.Object(_img(tmp_path)), from_point=(10, -20),
                           fade=False, duration=1.0)
    assert eff.params["from_point"] == (10, -20)


def test_morph_to_accepts_timing_and_fit(tmp_path):
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    p = sv.Project()
    activate(p)
    eff = sv.morph_to(sv.Object(_img(tmp_path)), delay=0.5, duration=1.0, fit=True)
    assert eff.params["delay"] == 0.5 and eff.params["fit"] is True
    with pytest.raises(ValueError, match="duration"):
        sv.morph_to(sv.Object(_img(tmp_path, "b.png")), duration=-1)
    # fit は sdf 専用（transport と混ぜると生成時にエラー。キー集合で確かめる）
    from scriptvedit import morph
    assert "fit" in morph.SDF_PARAM_KEYS and "fit" not in morph.TRANSPORT_PARAM_KEYS
    assert "et_range" not in morph.MORPH_PARAM_KEYS


def test_particle_key_depends_on_frame_size_only_when_expand_is_auto(tmp_path):
    """同一出力なら同一鍵: 画面寸法が出力に効くのは expand を省略したときだけ"""
    img = _img(tmp_path)

    def path(width, **params):
        p = sv.Project()
        p.configure(width=width, height=360, fps=30)
        activate(p)
        return cache_mod._particle_cache_path(img, _Op(**params), 2, 30)

    assert path(640) != path(1280)
    assert path(640, expand=100) == path(1280, expand=100)


# --- (c) 粒子 ----------------------------------------------------------------

def _frames(out_dir):
    np = pytest.importorskip("numpy")
    from PIL import Image
    names = sorted(n for n in os.listdir(out_dir) if n.endswith(".png"))
    return [np.array(Image.open(os.path.join(out_dir, n))) for n in names]


def _text_like(tmp_path, name="t.png", size=(120, 40)):
    """文字のような、透明の背景に不透明な帯が並ぶ画像"""
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for x in range(8, size[0] - 12, 18):
        d.rectangle((x, 8, x + 10, size[1] - 8), fill=(255, 255, 255, 255))
    path = tmp_path / name
    im.save(path)
    return str(path)


def test_auto_expand_keeps_every_visible_particle(tmp_path):
    """expand を省略すると、見えている粒が切れない広さになる（広い余白と同じ絵）"""
    np = pytest.importorskip("numpy")
    morph = pytest.importorskip("scriptvedit.morph")
    src = _text_like(tmp_path)
    kw = dict(max_pixels=600, speed=150, gravity=200, seed=5, dissolve=0.1)
    morph.generate_explode_frames(src, str(tmp_path / "auto"), 9,
                                  blend_fn=lambda t: t, **kw)
    morph.generate_explode_frames(src, str(tmp_path / "wide"), 9,
                                  blend_fn=lambda t: t, expand=600, **kw)
    morph.generate_explode_frames(src, str(tmp_path / "none"), 9,
                                  blend_fn=lambda t: t, expand=0, **kw)
    auto, wide, none = (_frames(tmp_path / d) for d in ("auto", "wide", "none"))
    assert auto[0].shape[0] > 40 and auto[0].shape[1] > 120      # 広がっている
    assert auto[0].shape[0] < wide[0].shape[0]                   # 必要な分だけ
    # 0 枚目は元の絵。粒の不透明度が閾値以上の間（p <= 0.95）は総量が一致する
    for i in range(0, 8):
        a = int(auto[i][:, :, 3].astype(np.int64).sum())
        w = int(wide[i][:, :, 3].astype(np.int64).sum())
        assert a == w, (i, a, w)
    # 余白なしだと途中で粒が切れている（＝このテストが意味を持つ条件）
    mid = 5
    assert int(none[mid][:, :, 3].astype(np.int64).sum()) < int(
        wide[mid][:, :, 3].astype(np.int64).sum())
    # 余白は左右・上下で対称（絵の中心が動かない）
    h, w_ = auto[0].shape[:2]
    assert (w_ - 120) % 2 == 0 and (h - 40) % 2 == 0


def test_auto_expand_respects_limit(tmp_path):
    morph = pytest.importorskip("scriptvedit.morph")
    src = _text_like(tmp_path)
    morph.generate_explode_frames(src, str(tmp_path / "lim"), 5, blend_fn=lambda t: t,
                                  max_pixels=300, speed=900, gravity=0, seed=1,
                                  expand_limit=(100, 60))
    f = _frames(tmp_path / "lim")[0]
    # キャンバスの半分が上限（画面寸法）を超えない: 幅 <= 2*100、高さ <= 2*60
    assert f.shape[1] <= 200 and f.shape[0] <= 120
    assert f.shape[1] > 120


def test_fade_false_keeps_particles(tmp_path):
    np = pytest.importorskip("numpy")
    morph = pytest.importorskip("scriptvedit.morph")
    src = _text_like(tmp_path)
    kw = dict(max_pixels=400, speed=60, gravity=0, seed=2, expand=80)
    morph.generate_explode_frames(src, str(tmp_path / "fade"), 5,
                                  blend_fn=lambda t: t, **kw)
    morph.generate_explode_frames(src, str(tmp_path / "keep"), 5,
                                  blend_fn=lambda t: t, fade=False, **kw)
    fade, keep = _frames(tmp_path / "fade"), _frames(tmp_path / "keep")
    assert int(fade[-1][:, :, 3].max()) == 0          # 既定は消えて終わる
    assert int(keep[-1][:, :, 3].max()) == 255        # 残す指定は濃いまま
    assert int((keep[-1][:, :, 3] > 0).sum()) > 400   # 散った粒が見えている
    assert np.array_equal(fade[0], keep[0])           # 0 枚目は元の絵


def test_toward_and_from_point_converge(tmp_path):
    np = pytest.importorskip("numpy")
    morph = pytest.importorskip("scriptvedit.morph")
    src = _text_like(tmp_path)
    kw = dict(max_pixels=400, speed=40, gravity=30, seed=2, fade=False,
              expand=150, particle_size=1)
    morph.generate_explode_frames(src, str(tmp_path / "to"), 6,
                                  blend_fn=lambda t: t, toward=(100, -60), **kw)
    last = _frames(tmp_path / "to")[-1]
    ys, xs = np.nonzero(last[:, :, 3])
    # 行き先 = 素材の中心 (59.5, 19.5) + (100, -60) + 余白 150
    assert abs(xs.mean() - (59.5 + 100 + 150)) < 2 and abs(ys.mean() - (19.5 - 60 + 150)) < 2
    assert xs.max() - xs.min() <= 4 and ys.max() - ys.min() <= 4   # 1点に集まっている
    # assemble は時間反転: 最初のコマが出発点、最後のコマが元の絵
    morph.generate_assemble_frames(src, str(tmp_path / "from"), 6,
                                   blend_fn=lambda t: t, from_point=(100, -60), **kw)
    frames = _frames(tmp_path / "from")
    ys, xs = np.nonzero(frames[0][:, :, 3])
    assert abs(xs.mean() - 309.5) < 2 and abs(ys.mean() - 109.5) < 2
    from PIL import Image
    orig = np.array(Image.open(src))
    assert np.array_equal(frames[-1][150:-150, 150:-150], orig)


def test_explicit_expand_output_is_unchanged_by_fast_path(tmp_path):
    """粒子レイヤーだけの区間で省いたリニア光の往復は、画素を変えない"""
    np = pytest.importorskip("numpy")
    morph = pytest.importorskip("scriptvedit.morph")
    src = _text_like(tmp_path)
    morph.generate_explode_frames(src, str(tmp_path / "o"), 6, blend_fn=lambda t: t,
                                  max_pixels=300, speed=50, seed=3, expand=40,
                                  dissolve=0.3)
    frames = _frames(tmp_path / "o")
    # dissolve の間（p=0.2）は元の絵と粒が混ざり、余白の粒も同じ割合で薄い
    inner = frames[1][40:-40, 40:-40, 3]
    assert 0 < int(inner.max())
    outer = frames[1].copy()
    outer[40:-40, 40:-40] = 0
    if outer[:, :, 3].any():
        assert int(outer[:, :, 3].max()) <= int(round(255 * 0.8 * (0.2 / 0.3))) + 1
    # 粒の不透明度は 1-p（p=0.6 → 0.4*255=102）
    assert int(frames[3][:, :, 3].max()) == int(255 * (1 - 0.6))
    assert frames[3].dtype == np.uint8


# --- (d) sdf モーフ ----------------------------------------------------------

def _bar(tmp_path, name, canvas, box, color=(255, 255, 255, 255)):
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", canvas, (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle(box, fill=color)
    path = tmp_path / name
    im.save(path)
    return str(path)


def _opaque_width(frame):
    import numpy as np
    cols = np.flatnonzero((frame[:, :, 3] >= 128).any(axis=0))
    return 0 if len(cols) == 0 else int(cols[-1] - cols[0] + 1)


def test_sdf_fit_keeps_ends_of_wider_shape(tmp_path):
    """幅の違う2枚: 自動の fit で、広い方の端が動き出した直後に消えない"""
    morph = pytest.importorskip("scriptvedit.morph")
    wide = _bar(tmp_path, "wide.png", (400, 60), (10, 20, 389, 39))     # 幅 380
    narrow = _bar(tmp_path, "narrow.png", (400, 60), (150, 20, 249, 39))  # 幅 100
    lin = lambda t: t
    assert morph.generate_rgba_frames(wide, narrow, str(tmp_path / "auto"), 5,
                                      blend_fn=lin) is None
    morph.generate_rgba_frames(wide, narrow, str(tmp_path / "off"), 5,
                               blend_fn=lin, fit=False)
    auto, off = _frames(tmp_path / "auto"), _frames(tmp_path / "off")
    # 進行度 0.25 / 0.5 / 0.75 で幅が 380 → 100 を直線で結ぶ
    for i, want in ((1, 310), (2, 240), (3, 170)):
        assert abs(_opaque_width(auto[i]) - want) <= 4, (i, _opaque_width(auto[i]))
    # fit なし: 端は相手の形から遠いので、進行度 0.25 で既に狭い方の幅に近い
    assert _opaque_width(off[1]) < 200
    # 両端は元の絵そのもの
    assert _opaque_width(auto[0]) == 380 and _opaque_width(auto[-1]) == 100


def test_sdf_fit_is_off_for_same_size_pair(tmp_path):
    """同じ大きさの組は合わせない（その場で溶けて入れ替わる従来の動き）"""
    morph = pytest.importorskip("scriptvedit.morph")
    a = _bar(tmp_path, "a.png", (200, 60), (20, 20, 119, 39))
    b = _bar(tmp_path, "b.png", (200, 60), (80, 20, 179, 39))
    ctx = morph._prepare_sdf_morph(a, b)
    assert ctx["fit"] is False and ctx["align"] is True
    # 重心の差 60px の分だけ余白が付く（整列で動いた先が切れない）
    assert ctx["canvas"][0] == 200 + 2 * 62 and ctx["canvas"][1] == 60
    assert morph._prepare_sdf_morph(a, b, fit=True)["fit"] is True
    assert morph._prepare_sdf_morph(a, b, align=False)["canvas"] == (200, 60)


def test_diagnose_sdf_morph(tmp_path):
    morph = pytest.importorskip("scriptvedit.morph")
    left = _bar(tmp_path, "l.png", (400, 60), (10, 20, 109, 39))
    right = _bar(tmp_path, "r.png", (400, 60), (290, 20, 389, 39))
    full = _bar(tmp_path, "f.png", (400, 60), (0, 0, 399, 59))
    # 離れた2つ: 位置を合わせなければ重ならない / 合わせれば重なる
    assert "重なりません" in morph.diagnose_sdf_morph(left, right, align=False)
    assert morph.diagnose_sdf_morph(left, right, align=True) is None
    # 全面不透明は輪郭が無い
    assert "輪郭が取れない" in morph.diagnose_sdf_morph(full, left)
    # 生成側も同じ理由を返す
    reason = morph.generate_rgba_frames(left, right, str(tmp_path / "o"), 2,
                                        align=False)
    assert reason and "重なりません" in reason


def test_audit_reports_sdf_crossfade(tmp_path):
    pytest.importorskip("scriptvedit.morph")
    left = _bar(tmp_path, "l.png", (400, 60), (10, 20, 109, 39))
    right = _bar(tmp_path, "r.png", (400, 60), (290, 20, 389, 39))
    layer = tmp_path / "l.py"

    def findings(extra):
        layer.write_text(
            "from scriptvedit import *\n"
            f"a = Object({left!r})\n"
            f"a.time(1) <= morph_to(Object({right!r}){extra})\n", encoding="utf-8")
        p = sv.Project()
        p.configure(width=640, height=360, fps=30)
        p.layer(str(layer), priority=0)
        with pytest.warns(UserWarning):
            return [f for f in p.audit(quiet=True)
                    if f["code"] == "morph-sdf-crossfade"]

    found = findings(", align=False")
    assert len(found) == 1 and found[0]["severity"] == "warning"
    assert "l.png" in found[0]["message"] and "r.png" in found[0]["message"]
    assert findings("") == []


# --- (e) describe ------------------------------------------------------------

def _entry(name):
    return next(e for e in describe()["effects"] if e["name"] == name)


def test_manifest_lists_particle_params_with_real_defaults():
    morph = pytest.importorskip("scriptvedit.morph")
    for fn, keys in (("explode_to", morph.EXPLODE_PARAM_KEYS),
                     ("assemble_from", morph.ASSEMBLE_PARAM_KEYS)):
        params = _entry(fn)["params"]
        for key in set(keys) | set(_TERMINAL_TIMING_KEYS):
            assert key in params, (fn, key)
            assert params[key].get("desc"), (fn, key)
        for key, default in morph.PARTICLE_PARAM_DEFAULTS.items():
            assert params[key]["default"] == default, (fn, key)
    assert "toward" not in _entry("assemble_from")["params"]
    assert "from_point" not in _entry("explode_to")["params"]


def test_manifest_lists_morph_params_with_real_defaults():
    morph = pytest.importorskip("scriptvedit.morph")
    params = _entry("morph_to")["params"]
    sig = inspect.signature(morph._prepare_sdf_morph).parameters
    for key in morph.SDF_PARAM_KEYS:
        assert params[key]["default"] == sig[key].default, key
    assert params["method"]["default"] == morph.DEFAULT_MORPH_METHOD
    assert set(params["method"]["choices"]) == set(morph.MORPH_METHODS)
    assert params["max_pixels"]["default"] == inspect.signature(
        morph._prepare_morph).parameters["max_pixels"].default
    for key in _TERMINAL_TIMING_KEYS:
        assert key in params


# --- (f) 実レンダ ------------------------------------------------------------

_W, _H, _FPS = 64, 36, 30


def _need_ffmpeg():
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg / ffprobe が無い環境")


def _rgb_frames(video):
    np = pytest.importorskip("numpy")
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True, timeout=120).stdout
    return np.frombuffer(out, np.uint8).reshape(-1, _H, _W, 3).astype(int)


def _render(tmp_path, lines, name="out.mp4"):
    layer = tmp_path / f"l_{name}.py"
    layer.write_text("from scriptvedit import *\n" + "".join(l + "\n" for l in lines),
                     encoding="utf-8")
    p = sv.Project()
    p.configure(width=_W, height=_H, fps=_FPS, background_color="white")
    p.layer(str(layer), priority=1)
    out = tmp_path / name
    p.render(str(out), timeout=300)
    return _rgb_frames(out)


def _red_frames(frames):
    """画面の中央が赤い（素材が映っている）フレーム番号"""
    px = frames[:, _H // 2, _W // 2]
    return [i for i, (r, g, b) in enumerate(px) if r > 170 and g < 100 and b < 100]


@pytest.mark.parametrize("start, dur", [(0, 1), (0.5, 1), (0.412, 1), (0.416, 1.02)])
def test_baked_effect_fills_its_window(tmp_path, start, dur):
    """焼いた Effect の動画が、表示の窓の最後の1枚まで映る（焼かない場合と同じ枚数）。

    以前は窓より1フレーム短く、最後の1枚だけ背景（ここでは白）が見えた。
    """
    _need_ffmpeg()
    red = _img(tmp_path, "red.png", (20, 20), (255, 0, 0, 255))
    pad = _img(tmp_path, "pad.png", (2, 2), (0, 0, 255, 255))
    tail = f"Object({pad!r}).time(2.5) <= move(x=0.02, y=0.05, anchor='center')"

    def run(effect, name):
        return _red_frames(_render(tmp_path, [
            f"o = Object({red!r})",
            f"(o @ {start}).time({dur}) <= {effect}",
            tail], name))

    baked = run("fade(lambda u: 1)", "baked.mp4")
    live = run("-fade(lambda u: 1)", "live.mp4")
    assert baked == live, (baked, live)
    assert len(baked) == _window_frames(start, dur, _FPS), baked
    assert baked == list(range(baked[0], baked[-1] + 1))      # 途中に穴が無い


def test_explode_delay_duration_and_hold_render(tmp_path):
    """delay の間は元の絵、duration の後は散ったままの最後のコマが窓の終わりまで残る"""
    _need_ffmpeg()
    pytest.importorskip("scriptvedit.morph")
    red = _img(tmp_path, "red.png", (16, 16), (255, 0, 0, 255))
    pad = _img(tmp_path, "pad.png", (2, 2), (0, 0, 255, 255))
    frames = _render(tmp_path, [
        f"o = Object({red!r})",
        "o.time(1.5) <= explode_to(max_pixels=200, speed=10, gravity=0, spread=0.3,"
        " particle_size=1, dissolve=0.05, seed=4, fade=False, delay=0.5, duration=0.5)",
        f"Object({pad!r}).time(2.2) <= move(x=0.02, y=0.05, anchor='center')",
    ])

    def red_count(i):
        f = frames[i]
        return int(((f[:, :, 0] > 150) & (f[:, :, 1] < 110)).sum())

    still = red_count(0)
    assert still >= 150            # 16x16 の赤い四角（縁は 4:2:0 の色差でにじむ）
    assert all(red_count(i) == still for i in range(0, 15))   # delay の間は元の絵
    assert red_count(22) != still                         # 動いている
    held = red_count(31)
    assert held > 0
    # duration の後（1.0 秒〜）は最後のコマのまま、窓の最後の1枚（45 枚目）まで
    assert all(red_count(i) == held for i in range(31, 46)), [
        red_count(i) for i in range(29, 47)]
    assert red_count(46) == 0                             # 窓の外は背景


@pytest.mark.parametrize("timing", ["duration=1/30", "duration=0.02", "delay=0.99"])
def test_terminal_effect_with_one_frame_of_motion_reaches_the_end(tmp_path, timing):
    """動く区間が1コマしか無い指定でも、最後は到達点の絵になる（元の絵のまま残らない）"""
    _need_ffmpeg()
    pytest.importorskip("scriptvedit.morph")
    from PIL import Image, ImageDraw
    red = _img(tmp_path, "red.png", (16, 16), (255, 0, 0, 255))
    green = tmp_path / "green.png"
    im = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse([2, 2, 13, 13], fill=(0, 200, 0, 255))
    im.save(green)
    pad = _img(tmp_path, "pad.png", (2, 2), (0, 0, 255, 255))
    tail = f"(Object({pad!r}) @ 0).time(1.5) <= move(x=0.02, y=0.05, anchor='center')"

    def count(frame, kind):
        r, g, b = frame[:, :, 0], frame[:, :, 1], frame[:, :, 2]
        if kind == "red":
            return int(((r > 150) & (g < 110) & (b < 110)).sum())
        return int(((g > 110) & (r < 110) & (b < 110)).sum())

    morph = _render(tmp_path, [
        f"o = Object({red!r})",
        f"o.time(1) <= morph_to(Object({str(green)!r}), {timing})", tail], "morph.mp4")
    assert count(morph[0], "red") >= 150                 # 最初は元の絵
    assert count(morph[30], "red") == 0                  # 窓の最後の1枚は到達点
    assert count(morph[30], "green") >= 60

    explode = _render(tmp_path, [
        f"o = Object({red!r})",
        f"o.time(1) <= explode_to(max_pixels=200, speed=10, gravity=0, {timing})",
        tail], "explode.mp4")
    assert count(explode[0], "red") >= 150
    assert count(explode[30], "red") == 0                # fade 既定: 散り終えて消えている


# --- 余白と anchor ------------------------------------------------------------

def _box(frame, kind="red"):
    """色のついた画素の外接矩形 (x0, y0, x1, y1)。無ければ None"""
    np = pytest.importorskip("numpy")
    r, g, b = frame[:, :, 0], frame[:, :, 1], frame[:, :, 2]
    mask = ((r > 150) & (g < 110) & (b < 110) if kind == "red"
            else (g > 110) & (r < 110) & (b < 110))
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def test_move_exprs_subtract_terminal_margin(tmp_path):
    """辺・角の anchor は、余白を除いた元の絵の箱（定数）と overlay の w/h から位置を出す"""
    pytest.importorskip("PIL")
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe が無い環境")
    from scriptvedit.filters.video import _build_move_exprs, _terminal_inner_dims
    a = _img(tmp_path, "a.png", (40, 20))
    b = _img(tmp_path, "b.png", (30, 50))

    def obj_with(anchor, bake):
        o = sv.Object(a)
        o <= sv.move(x=0.25, y=0.5, anchor=anchor)
        o._terminal_bake = bake
        return o

    explode = _Op("explode_to")
    assert _terminal_inner_dims(obj_with("topleft", (explode, a))) == (40, 20)
    morph = _Op("morph_to")
    morph._morph_target = sv.Object(b)
    # morph は2枚を中央で重ねた共通キャンバス（幅・高さそれぞれ大きい方）
    assert _terminal_inner_dims(obj_with("topleft", (morph, a))) == (40, 50)
    assert _terminal_inner_dims(obj_with("topleft", None)) == (None, None)

    x, y = _build_move_exprs(obj_with("topleft", (explode, a)), 0, 1)
    assert x.endswith("-(w-40)/2)") and y.endswith("-(h-20)/2)")
    x, y = _build_move_exprs(obj_with("right", (explode, a)), 0, 1)
    assert x.endswith("-(w+40)/2)") and y.endswith("-h/2)")
    x, y = _build_move_exprs(obj_with("bottom", (explode, a)), 0, 1)
    assert x.endswith("-w/2)") and y.endswith("-(h+20)/2)")
    # 中心基準と、終端フレーム Effect の無い Object は従来の式のまま
    plain = _build_move_exprs(obj_with("topleft", None), 0, 1)
    assert "(w-" not in plain[0] and "(h-" not in plain[1]
    center = _build_move_exprs(obj_with("center", (explode, a)), 0, 1)
    assert center == _build_move_exprs(obj_with("center", None), 0, 1)


@pytest.mark.parametrize("anchor", ["topleft", "right", "bottom", "center"])
def test_explode_margin_does_not_shift_anchored_object(tmp_path, anchor):
    """自動 expand の余白があっても、anchor で置いた絵は静止画と同じ位置に映る。

    以前は余白込みのキャンバスの角・辺を anchor に合わせたため、中心以外の anchor で
    絵が余白ぶんずれた（画面外へ消えることもあった）。
    """
    _need_ffmpeg()
    pytest.importorskip("scriptvedit.morph")
    red = _img(tmp_path, "red.png", (12, 12), (255, 0, 0, 255))
    pad = _img(tmp_path, "pad.png", (2, 2), (0, 0, 255, 255))
    tail = f"(Object({pad!r}) @ 0).time(1.5) <= move(x=0.02, y=0.05, anchor='center')"
    mv = f"o <= move(x=0.5, y=0.5, anchor={anchor!r})"
    still = _render(tmp_path, [f"o = Object({red!r})", "o.time(1)", mv, tail], "still.mp4")
    baked = _render(tmp_path, [
        f"o = Object({red!r})",
        "o.time(1) <= explode_to(max_pixels=150, speed=40, seed=1, delay=0.5)",
        mv, tail], "baked.mp4")
    assert _box(still[0]) is not None
    assert _box(baked[0]) == _box(still[0])


@pytest.mark.parametrize("anchor", ["topleft", "right"])
def test_morph_margin_does_not_shift_anchored_object(tmp_path, anchor):
    """sdf モーフの整列の余白があっても、最初は A・最後は B が静止画と同じ位置に映る"""
    _need_ffmpeg()
    pytest.importorskip("scriptvedit.morph")
    a = _bar(tmp_path, "a.png", (40, 16), (2, 3, 13, 12), (255, 0, 0, 255))
    b = _bar(tmp_path, "b.png", (40, 16), (26, 3, 37, 12), (0, 200, 0, 255))
    pad = _img(tmp_path, "pad.png", (2, 2), (0, 0, 255, 255))
    tail = f"(Object({pad!r}) @ 0).time(1.5) <= move(x=0.02, y=0.05, anchor='center')"
    mv = f"o <= move(x=0.5, y=0.5, anchor={anchor!r})"
    still_a = _render(tmp_path, [f"o = Object({a!r})", "o.time(1)", mv, tail], "sa.mp4")
    still_b = _render(tmp_path, [f"o = Object({b!r})", "o.time(1)", mv, tail], "sb.mp4")
    baked = _render(tmp_path, [
        f"o = Object({a!r})",
        f"o.time(1) <= morph_to(Object({b!r}), delay=0.3, duration=0.4)", mv, tail],
        "baked.mp4")
    assert _box(still_a[0]) is not None and _box(still_b[0], "green") is not None
    assert _box(baked[0]) == _box(still_a[0])
    got, want = _box(baked[30], "green"), _box(still_b[0], "green")
    assert got is not None
    assert all(abs(g - w) <= 1 for g, w in zip(got, want)), (got, want)   # 縁の AA ぶん
