# -*- coding: utf-8 -*-
"""監査 issue #14 の P1×5 + P2×1 の回帰テスト

1. Transform/Effect のカテゴリ順仕様（Effect 後の Transform は明示拒否）
2. compute() 後の Object の再配置（README の契約）と stream 状態の正規化
3. 時刻・尺 API の NaN/Infinity/負数の入口拒否
4. time(name=...) の生成アンカーの別レイヤー重複エラー
5. 総尺外の部分レンダの空出力拒否
6. レイヤー exec 例外後の _current_layer_file 復元
"""

import math
import os

import pytest

import scriptvedit as sv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(ROOT, "assets", "images", "shape_badge.png")
VID = os.path.join(ROOT, "assets", "video", "clip_with_audio.mp4")


def _make_project():
    p = sv.Project()
    p.configure(width=64, height=36, fps=10, background_color="black")
    return p


def _write_layer(tmp_path, name, body):
    path = tmp_path / name
    path.write_text("from scriptvedit import *\n" + body, encoding="utf-8")
    return str(path)


# --- 1. Transform と Effect のカテゴリ順仕様 -------------------------------

class TestTransformEffectOrder:
    def test_transform_after_effect_rejected(self):
        o = sv.Object(IMG)
        o <= sv.pixelize(8)
        with pytest.raises(ValueError, match="Transform .* Effect より先"):
            o <= sv.resize(sx=0.5, sy=0.5)

    def test_transform_chain_after_effect_rejected(self):
        o = sv.Object(IMG)
        o <= sv.fade(0.5)
        with pytest.raises(ValueError, match="compute\\(\\)"):
            o <= (sv.resize(sx=0.5, sy=0.5) | sv.blur(radius=2))

    def test_grid_after_effect_rejected(self):
        o = sv.Object(IMG)
        o <= sv.fade(0.5)
        with pytest.raises(ValueError, match="Transform 'grid'"):
            o.grid(2, 2)

    def test_transform_then_effect_still_ok(self):
        o = sv.Object(IMG)
        o <= sv.resize(sx=0.5, sy=0.5)
        o <= sv.pixelize(8)
        assert [t.name for t in o.transforms] == ["resize"]
        assert [e.name for e in o.effects] == ["pixelize"]


# --- 2. compute() 後の Object の再配置と stream 正規化 ----------------------

class TestComputeReuse:
    def test_readme_example_places_object_on_timeline(self, tmp_path):
        """README の compute()→time() の例がタイムラインへ配置される"""
        layer = _write_layer(
            tmp_path, "compute_layer.py",
            f"processed = Object({IMG!r})\n"
            "processed <= resize(sx=0.5, sy=0.5)\n"
            "processed.compute()\n"
            "processed.time(3)\n")
        p = _make_project()
        p.layer(layer)
        result = p.render(str(tmp_path / "out.mp4"), dry_run=True)
        objs = [o for o in p.objects if isinstance(o, sv.Object)]
        assert len(objs) == 1
        assert objs[0].duration == 3
        # source は生成物パス（artifacts/compute 配下の PNG）へ差し替わる
        src = objs[0].source
        assert "compute" in src.replace("\\", "/") and src.endswith(".png")
        # 生成物 source が最終コマンドの -i に現れる
        main_cmd = result["main"]
        assert src in main_cmd
        assert main_cmd[main_cmd.index(src) - 1] == "-i"
        # 未生成なら生成コマンドが記録される（キャッシュ命中済みなら実体がある）
        assert src in result["cache"] or os.path.exists(src)

    def test_compute_show_and_at_also_reregister(self, tmp_path):
        """time() 以外の再配置 API（show / @）でも Project へ戻る"""
        layer = _write_layer(
            tmp_path, "compute_show.py",
            f"a = Object({IMG!r})\n"
            "a <= resize(sx=0.5, sy=0.5)\n"
            "a.compute()\n"
            "a.show(2)\n"
            f"b = Object({IMG!r})\n"
            "b <= resize(sx=0.25, sy=0.25)\n"
            "b.compute()\n"
            "b @ 1\n"
            "b.time(1)\n")
        p = _make_project()
        p.layer(layer)
        p.render(str(tmp_path / "out.mp4"), dry_run=True)
        objs = [o for o in p.objects if isinstance(o, sv.Object)]
        assert len(objs) == 2

    def test_reregister_does_not_duplicate(self):
        """登録済み Object への time()/show() は重複登録しない"""
        p = _make_project()
        o = sv.Object(IMG)
        o.time(2)
        o.show(1)
        assert sum(1 for x in p.objects if x is o) == 1

    def test_video_compute_normalizes_audio_state(self):
        """映像 compute は音声 stream 状態も生成物（音声なし）に一致させる"""
        p = _make_project()
        p._mode = "plan"  # plan pass 相当（実生成なしで source 差し替えのみ）
        o = sv.Object(VID)
        o <= sv.resize(sx=0.5, sy=0.5)
        o <= sv.avolume(0.5)
        o._has_audio = True  # 音声付き動画を模擬
        o.compute(duration=2)
        assert o.audio_effects == []
        assert o._has_audio is False
        assert o.has_audio is False
        assert o.transforms == [] and o.effects == []
        assert o.media_type == "video"  # FFV1 mkv
        assert o.source.endswith(".mkv")
        # 除外されており、time() で再登録される
        assert not any(x is o for x in p.objects)
        o.time(2)
        assert any(x is o for x in p.objects)

    def test_image_compute_clears_audio_state(self):
        p = _make_project()
        p._mode = "plan"
        o = sv.Object(IMG)
        o <= sv.resize(sx=0.5, sy=0.5)
        o.compute()
        assert o.media_type == "image"
        assert o.audio_effects == [] and o._has_audio is False


# --- 3. 時刻・尺 API の NaN/Infinity/負数拒否 ------------------------------

NAN = float("nan")
INF = float("inf")


class TestTimeValidation:
    @pytest.mark.parametrize("bad", [NAN, -1, INF, -INF, 0, True, "3"])
    def test_object_time_rejects(self, bad):
        o = sv.Object(IMG)
        with pytest.raises(ValueError):
            o.time(bad)

    @pytest.mark.parametrize("bad", [NAN, -1, INF, 0])
    def test_object_show_rejects(self, bad):
        o = sv.Object(IMG)
        with pytest.raises(ValueError):
            o.show(bad)

    @pytest.mark.parametrize("bad", [NAN, INF, "x"])
    def test_until_offset_rejects(self, bad):
        o = sv.Object(IMG)
        with pytest.raises(ValueError):
            o.until("a", bad)
        with pytest.raises(ValueError):
            o.show_until("a", bad)

    @pytest.mark.parametrize("bad", [NAN, -2, INF, -INF])
    def test_pause_time_rejects(self, bad):
        _make_project()
        with pytest.raises(ValueError):
            sv.pause.time(bad)

    @pytest.mark.parametrize("bad", [NAN, INF])
    def test_pause_until_offset_rejects(self, bad):
        _make_project()
        with pytest.raises(ValueError):
            sv.pause.until("a", bad)

    @pytest.mark.parametrize("bad", [NAN, -1, INF, 0, None])
    def test_scene_duration_rejects(self, bad):
        _make_project()
        with pytest.raises(ValueError):
            sv.scene("bad", bad)

    @pytest.mark.parametrize("bad", [NAN, -1, INF])
    def test_matmul_rejects(self, bad):
        o = sv.Object(IMG)
        with pytest.raises(ValueError):
            o @ bad

    @pytest.mark.parametrize("bad", [NAN, INF, -1])
    def test_render_start_rejects(self, tmp_path, bad):
        p = _make_project()
        with pytest.raises(ValueError):
            p.render(str(tmp_path / "o.mp4"), dry_run=True, start=bad)

    def test_negative_offset_is_still_allowed(self):
        """until の負 offset（アンカー手前まで）は正当なので拒否しない"""
        o = sv.Object(IMG)
        o.until("a", -0.5)
        assert o._until_offset == -0.5


# --- 4. time(name=...) の同名アンカー重複管理 ------------------------------

class TestGeneratedAnchorDuplication:
    def test_same_name_in_different_layers_errors(self, tmp_path):
        la = _write_layer(tmp_path, "la.py",
                          f"Object({IMG!r}).time(1, name='dup')\n")
        lb = _write_layer(tmp_path, "lb.py",
                          f"Object({IMG!r}).time(1, name='dup')\n")
        p = _make_project()
        p.layer(la)
        p.layer(lb)
        with pytest.raises(RuntimeError, match="再定義は禁止"):
            p.render(str(tmp_path / "out.mp4"), dry_run=True)

    def test_same_layer_reexecution_is_allowed(self, tmp_path):
        """Plan/Render の同一レイヤー再実行では重複エラーにならない"""
        la = _write_layer(tmp_path, "solo.py",
                          f"Object({IMG!r}).time(1, name='solo')\n"
                          "pause.until('solo.end')\n")
        p = _make_project()
        p.layer(la)
        result = p.render(str(tmp_path / "out.mp4"), dry_run=True)
        assert result["main"]
        assert p._anchors["solo.end"] == 1

    def test_explicit_anchor_vs_generated_anchor_conflict(self, tmp_path):
        la = _write_layer(tmp_path, "la.py", "anchor('x.start')\n")
        lb = _write_layer(tmp_path, "lb.py",
                          f"Object({IMG!r}).time(1, name='x')\n")
        p = _make_project()
        p.layer(la)
        p.layer(lb)
        with pytest.raises(RuntimeError, match="x\\.start"):
            p.render(str(tmp_path / "out.mp4"), dry_run=True)


# --- 5. 総尺外の部分レンダの拒否 -------------------------------------------

class TestRenderWindowValidation:
    def _project_with_1s(self, tmp_path):
        layer = _write_layer(tmp_path, "one_sec.py",
                             f"Object({IMG!r}).time(1)\n")
        p = _make_project()
        p.layer(layer)
        return p

    def test_start_equal_to_duration_rejected(self, tmp_path):
        p = self._project_with_1s(tmp_path)
        with pytest.raises(ValueError, match="出力区間が空"):
            p.render(str(tmp_path / "out.mp4"), dry_run=True, start=1)

    def test_start_beyond_duration_rejected(self, tmp_path):
        p = self._project_with_1s(tmp_path)
        with pytest.raises(ValueError, match="出力区間が空"):
            p.render(str(tmp_path / "out.mp4"), dry_run=True, start=2)

    def test_valid_window_still_works(self, tmp_path):
        p = self._project_with_1s(tmp_path)
        result = p.render(str(tmp_path / "out.mp4"), dry_run=True,
                          start=0.25, end=0.75)
        cmd = result["main"]
        assert "-ss" in cmd and cmd[cmd.index("-ss") + 1] == "0.25"

    def test_end_beyond_duration_is_clamped(self, tmp_path):
        """end > 総尺は従来どおり総尺へ clamp（エラーにしない）"""
        p = self._project_with_1s(tmp_path)
        result = p.render(str(tmp_path / "out.mp4"), dry_run=True,
                          start=0.5, end=10)
        cmd = result["main"]
        # -t は clamp 後の実効長（1.0 - 0.5 = 0.5）
        assert cmd[cmd.index("-t") + 1] == "0.5"


# --- 6. レイヤー exec 例外後の _current_layer_file 復元 ---------------------

class TestCurrentLayerFileRestore:
    def test_restored_after_layer_exception(self, tmp_path):
        bad = _write_layer(tmp_path, "bad.py",
                           "raise RuntimeError('layer boom')\n")
        p = _make_project()
        p.layer(bad)
        with pytest.raises(RuntimeError, match="layer boom"):
            p.render(str(tmp_path / "out.mp4"), dry_run=True)
        assert p._current_layer_file is None

    def test_restored_when_later_layer_fails(self, tmp_path):
        ok = _write_layer(tmp_path, "ok.py",
                          f"Object({IMG!r}).time(1)\n")
        bad = _write_layer(tmp_path, "bad.py",
                          "raise RuntimeError('layer boom')\n")
        p = _make_project()
        p.layer(ok)
        p.layer(bad)
        with pytest.raises(RuntimeError, match="layer boom"):
            p.render(str(tmp_path / "out.mp4"), dry_run=True)
        assert p._current_layer_file is None
