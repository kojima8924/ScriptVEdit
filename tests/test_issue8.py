# -*- coding: utf-8 -*-
"""issue #8 の演算子統一・堅牢性・info項目の回帰テスト"""

import json
import os
import subprocess
import warnings
from types import SimpleNamespace

import pytest

import scriptvedit as sv
# 内部ヘルパーは実体モジュールから直接 import する
# （package root からの private 再エクスポートは廃止済み）
from scriptvedit.cache import (
    _checkpoint_cache_path, _layer_cache_paths, _morph_cache_path,
    _op_fingerprint_str, _particle_cache_path,
)
from scriptvedit.filters.video import _build_effect_filters
from scriptvedit.text import _escape_ffpath


@pytest.fixture(autouse=True)
def _restore_project_globals():
    """各テスト後にProjectの暗黙登録先と実行スタックを戻す"""
    old_current = sv.Project._current
    old_stack = list(sv.Project._exec_stack)
    sv.Project._current = None
    sv.Project._exec_stack[:] = []
    try:
        yield
    finally:
        sv.Project._current = old_current
        sv.Project._exec_stack[:] = old_stack


def test_tilde_is_quality_hint_for_audio_and_never_deletes_content():
    """AudioEffectの~は処理を残し、明示delete系だけが内容を削除する"""
    from scriptvedit.filters.audio import _build_audio_effect_filters

    normal = sv.Object("normal.wav")
    hinted = sv.Object("hinted.wav")
    chain = sv.avolume(0.5) & sv.avolume(lambda u: u)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        normal <= chain
        hinted <= ~chain
    assert caught == []
    assert [e.name for e in hinted.audio_effects] == ["avolume", "avolume"]
    assert all(e.quality == "fast" for e in hinted.audio_effects)
    assert _build_audio_effect_filters(normal, 2) == _build_audio_effect_filters(hinted, 2)
    assert (~~sv.avolume(0.5)).quality == "fast"

    audio_clip = sv.Object("clip.wav")
    audio_clip <= ~sv.adelete()
    assert not audio_clip.has_audio

    video_clip = sv.Object("clip.png")
    video_clip <= ~sv.delete()
    assert not video_clip.has_video


def test_effect_copy_preserves_factory_metadata():
    """~やpolicy変更で終端素材・パス式を失わない"""
    source = sv.Object("source.png")
    assemble = sv.assemble_from(source)
    for copied in (~assemble, +assemble, -assemble):
        assert copied._assemble_source is source

    path = sv.move_along([(0, 0), (0.5, 0.25), (1, 1)])
    for copied in (~path, +path, -path):
        assert copied._path_xy == path._path_xy
        facing = sv.look_at(copied)
        assert facing.name == "rotate_to"


def test_ignored_quality_hint_does_not_split_cache_keys(tmp_path):
    """出力が同じ未対応opはraw qualityが違っても全キャッシュ鍵を共有する"""
    source = tmp_path / "source.png"
    source.write_bytes(b"same source")
    normal = sv.fade(lambda u: u)
    hinted = ~sv.fade(lambda u: u)
    ops_normal = [("effect", normal)]
    ops_hinted = [("effect", hinted)]

    assert normal.quality == "final" and hinted.quality == "fast"
    assert _op_fingerprint_str(normal) == _op_fingerprint_str(hinted)
    assert _checkpoint_cache_path(str(source), ops_normal, 2, 30) == \
        _checkpoint_cache_path(str(source), ops_hinted, 2, 30)
    assert _morph_cache_path(str(source), normal, 2, 30) == \
        _morph_cache_path(str(source), hinted, 2, 30)
    assert _particle_cache_path(str(source), normal, 2, 30) == \
        _particle_cache_path(str(source), hinted, 2, 30)

    obj_normal = sv.Object(str(source))
    obj_hinted = sv.Object(str(source))
    obj_normal.transforms.append(sv.resize(sx=0.5, sy=0.5))
    obj_hinted.transforms.append(~sv.resize(sx=0.5, sy=0.5))
    assert obj_normal._compute_cache_path() == obj_hinted._compute_cache_path()


def test_manifest_describes_fast_hint_semantics():
    """AIがopごとの対応有無と~の非削除意味論をdescribeだけで判断できる"""
    manifest = sv.describe()
    assert manifest["manifest_version"] == "1.1"
    for section in ("effects", "transforms", "audio_effects", "plugins"):
        assert all(isinstance(entry["respects_fast_hint"], bool)
                   for entry in manifest[section])
    fade_entry = sv.describe(name="fade")["effects"][0]
    assert fade_entry["respects_fast_hint"] is False
    constraint = next(c for c in manifest["constraints"]
                      if c["id"] == "fast_quality_hint")
    assert "内容を削除しない" in constraint["text"]
    assert "adelete" in constraint["text"]
    assert "fast hint: ignored（通常と同一）" in sv.describe_markdown(
        sv.describe(name="fade"))


@pytest.mark.parametrize(
    "chain,operator,message",
    [
        (sv.TransformChain([]), lambda value: +value, "空のTransformChain"),
        (sv.TransformChain([]), lambda value: -value, "空のTransformChain"),
        (sv.EffectChain([]), lambda value: +value, "空のEffectChain"),
        (sv.EffectChain([]), lambda value: -value, "空のEffectChain"),
    ],
)
def test_empty_chain_policy_operators_raise_japanese_error(chain, operator, message):
    """空チェーンの末尾参照をIndexErrorとして漏らさない"""
    with pytest.raises(ValueError, match=message):
        operator(chain)


def test_from_project_outside_layer_preserves_current_project():
    """レイヤー外の誤用でProject._currentをNoneへ破壊しない"""
    sub = sv.Project()
    with pytest.raises(ValueError, match="レイヤー実行中の親Project"):
        sv.Object.from_project(sub)
    assert sv.Project._current is sub
    following = sv.Object("following.png")
    assert following in sub.objects


def test_beat_decode_timeout_is_bounded_and_translated(monkeypatch):
    """beat解析のffmpegデコードは600秒で日本語エラーになる"""
    beat = pytest.importorskip("scriptvedit.beat")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(beat.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="600 秒でタイムアウト") as exc_info:
        beat._load_mono("slow.wav")
    assert seen["timeout"] == 600
    assert isinstance(exc_info.value.__cause__, subprocess.TimeoutExpired)


def test_voicevox_query_and_synthesis_use_long_timeout(monkeypatch, tmp_path):
    """VOICEVOXのqueryとsynthesisはともに合成用の長い上限を使う"""
    from scriptvedit import tts as svtts

    calls = []

    def fake_request(url, **kwargs):
        calls.append((url, kwargs["timeout"]))
        return "{}" if "/audio_query" in url else b"RIFF"

    monkeypatch.setattr(svtts, "_request", fake_request)
    monkeypatch.setattr(svtts, "_atomic_write_bytes", lambda path, data: None)
    svtts._synth_voicevox(
        "長い文章", 1, 1.0, 0.0, str(tmp_path / "voice.wav"), "127.0.0.1", 50021)
    assert [timeout for _, timeout in calls] == [svtts._SYNTH_TIMEOUT] * 2


def test_tts_atomic_temp_paths_are_unique_and_cleaned(monkeypatch, tmp_path):
    """TTS一時ファイルは同じ確定先でも衝突せず、例外時も残らない"""
    from scriptvedit import tts as svtts

    # _atomic_write_bytes は ffmpeg モジュールへ集約されたため、
    # 一時パス生成のフックも ffmpeg 側の _unique_tmp_path に当てる
    from scriptvedit import ffmpeg as svff

    final = tmp_path / "voice.wav"
    created = []
    original_unique = svff._unique_tmp_path

    def recording_unique(path):
        result = original_unique(path)
        created.append(result)
        return result

    monkeypatch.setattr(svff, "_unique_tmp_path", recording_unique)
    svtts._atomic_write_bytes(str(final), b"one")
    svtts._atomic_write_bytes(str(final), b"two")
    assert len(set(created)) == 2
    assert final.read_bytes() == b"two"
    assert all(not os.path.exists(path) for path in created)

    edge_tmp = tmp_path / "edge.tmp.mp3"
    edge_calls = []

    def edge_unique(path):
        edge_calls.append(path)
        edge_tmp.write_bytes(b"partial")
        return str(edge_tmp)

    monkeypatch.setattr(svtts, "_unique_tmp_path", edge_unique)
    monkeypatch.setattr(svtts, "_edge_import", lambda: object())

    def fail_async(coro):
        coro.close()
        raise OSError("stop")

    monkeypatch.setattr(svtts, "_run_async", fail_async)
    with pytest.raises(ConnectionError):
        svtts._synth_edge("x", "voice", 1.0, 0.0, str(final))
    assert edge_calls == [str(final.with_suffix(".mp3"))]
    assert not edge_tmp.exists()

    sapi_tmp = tmp_path / "sapi.tmp.wav"
    sapi_calls = []

    def sapi_unique(path):
        sapi_calls.append(path)
        sapi_tmp.write_bytes(b"partial")
        return str(sapi_tmp)

    monkeypatch.setattr(svtts, "_unique_tmp_path", sapi_unique)
    monkeypatch.setattr(svtts, "_sapi_check_platform", lambda: None)
    monkeypatch.setattr(
        svtts, "_run_powershell",
        lambda script: (_ for _ in ()).throw(OSError("stop")))
    with pytest.raises(OSError, match="stop"):
        svtts._synth_sapi("x", None, 1.0, 0.0, str(final))
    assert sapi_calls == [str(final)]
    assert not sapi_tmp.exists()


def test_cbrt_is_real_for_negative_inputs_and_matches_ffmpeg_form():
    """cbrtのeval_at/FFmpeg式をsign*pow(abs,1/3)へ統一する"""
    assert sv.cbrt(-8).eval_at(0) == pytest.approx(-2.0)
    u = sv.Var("u")
    expr = sv.cbrt(2 * u - 1)
    values = [expr.eval_at(i / 100) for i in range(101)]
    assert all(isinstance(value, (int, float)) for value in values)
    assert values[0] == pytest.approx(-1.0)
    assert values[50] == pytest.approx(0.0)
    assert values[-1] == pytest.approx(1.0)
    ffexpr = expr.to_ffmpeg("T")
    assert "pow(abs(" in ffexpr
    assert "gt(" in ffexpr and "lt(" in ffexpr


def test_exports_are_atomic_and_string_tags_are_one_tag(tmp_path, monkeypatch):
    """chapters/metadataは原子的に書き、tags文字列を1要素として扱う"""
    project = sv.Project()
    project._markers = [(2.0, "本編")]
    metadata = tmp_path / "nested" / "metadata.json"
    project.export_metadata(str(metadata), tags="scriptvedit")
    assert json.loads(metadata.read_text(encoding="utf-8"))["tags"] == ["scriptvedit"]

    chapters = tmp_path / "chapters" / "chapters.txt"
    project.export_chapters(str(chapters))
    assert chapters.read_text(encoding="utf-8").startswith("0:00 イントロ")

    import scriptvedit.project as project_module

    chapters.write_text("old", encoding="utf-8")
    monkeypatch.setattr(project_module.os, "replace",
                        lambda src, dst: (_ for _ in ()).throw(OSError("stop")))
    with pytest.raises(OSError, match="stop"):
        project.export_chapters(str(chapters))
    assert chapters.read_text(encoding="utf-8") == "old"
    assert list(chapters.parent.glob("*.tmp*")) == []


def test_probe_missing_warning_contains_target_path(monkeypatch):
    """ffprobe不在警告から失敗した素材を特定できる"""
    import scriptvedit.project as project_module

    project = sv.Project()
    monkeypatch.setattr(
        project_module.subprocess, "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()))
    with pytest.warns(UserWarning, match=r"missing\.mp4"):
        assert project._probe_media("missing.mp4") is None


def test_color_validation_accepts_rgba_hex_and_rejects_bad_names(tmp_path):
    """8桁alpha色を受理し、色名のタイプミスを構築時に止める"""
    assert sv.chroma_key("0x11223344").params["color"] == "0x11223344"
    assert sv.pad(w=10, h=10, color="#aabbcc80").params["color"] == "0xAABBCC80"
    for css_color in ("transparent", "rebeccapurple", "grey", "lightgray"):
        assert sv.formula("x", color=css_color).source.endswith(".png")
    with pytest.raises(ValueError, match="不正な色名"):
        sv.pad(w=10, h=10, color="transparent")
    with pytest.raises(ValueError, match="不正な色名"):
        sv.chroma_key("gren")
    with pytest.raises(ValueError, match="不正な色名"):
        sv.pad(w=10, h=10, color="whitte")
    with pytest.raises(ValueError, match="CSSカラー"):
        sv.formula("x", color="whitte")
    with pytest.raises(ValueError, match="16進カラー"):
        sv.chroma_key("#12345")
    project = sv.Project()
    with pytest.raises(ValueError, match="不正な色名"):
        project.configure(background_color="bleu")

    audio = tmp_path / "tone.wav"
    audio.write_bytes(b"RIFF")
    with pytest.raises(ValueError, match="不正な色名"):
        sv.audio_viz(str(audio), color="whitte", duration=1)


def test_quoted_filter_paths_are_escaped_for_lut(tmp_path):
    """アポストロフィを含むLUTパスをfiltergraph内で安全に引用する"""
    lut_path = tmp_path / "director's-look.cube"
    lut_path.write_text("LUT_3D_SIZE 2\n", encoding="ascii")
    escaped = _escape_ffpath(str(lut_path))
    assert r"'\\\''" in escaped

    effect = sv.lut(str(lut_path))
    filters, _ = _build_effect_filters(
        SimpleNamespace(effects=[effect]), 0, 1)
    assert filters == [f"lut3d=file={escaped}"]


def test_cached_layer_replay_warns_when_audio_will_be_lost(tmp_path, monkeypatch):
    """cache='use'再生時にも音声の無言脱落を警告する"""
    layer = tmp_path / "audio_layer.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "voice = Object('voice.wav')\n"
        "voice.time(1)\n",
        encoding="utf-8")
    project = sv.Project()
    project.layer(str(layer), cache="use")
    spec = project._layer_specs[0]
    project._exec_layer(spec["filename"], spec["priority"])
    project.objects = []
    project._layers = []
    with pytest.warns(UserWarning, match="音声が脱落"):
        project._load_cached_layer(spec)
    assert project.objects[0]._has_audio is False

    # issue #8以前のanchors.jsonにはaudio_sourcesが無い。新規Projectで直接
    # 読み戻しても、sourcesのprobeから移行警告を出せることを固定する。
    import scriptvedit.cache as cache_module
    import scriptvedit.project as project_module

    monkeypatch.setattr(cache_module, "_ARTIFACT_DIR", str(tmp_path / "cache"))

    persisted_layer = tmp_path / "persisted_audio_layer.py"
    persisted_layer.write_text("# metadata persistence\n", encoding="utf-8")
    persisted = sv.Project()
    persisted.layer(str(persisted_layer), cache="make")
    persisted_spec = persisted._layer_specs[0]
    persisted.duration = 1
    persisted._layer_audio_sources[persisted_spec["filename"]] = ["voice.wav"]
    persisted._layer_unknown_audio_sources[persisted_spec["filename"]] = []
    persisted._layer_sources[persisted_spec["filename"]] = []
    monkeypatch.setattr(
        persisted, "_build_layer_cache_cmd",
        lambda index, out: ["ffmpeg", "-y", out])

    def fake_cache_run(cmd, out, timeout, context=None):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(b"cache")

    monkeypatch.setattr(project_module, "_run_ffmpeg_to_cache", fake_cache_run)
    persisted._render_layer_to_cache(0)
    _, persisted_meta = _layer_cache_paths(str(persisted_layer), persisted)
    with open(persisted_meta, encoding="utf-8") as f:
        assert json.load(f)["audio_sources"] == ["voice.wav"]

    replay = sv.Project()
    replay.duration = 1  # キャッシュ鍵が総尺を含むため、persisted と揃える
    replay.layer(str(persisted_layer), cache="use")
    with pytest.warns(UserWarning, match="音声が脱落"):
        replay._load_cached_layer(replay._layer_specs[0])

    legacy = sv.Project()
    legacy.layer(str(layer), cache="use")
    legacy_spec = legacy._layer_specs[0]
    webm_path, meta_path = _layer_cache_paths(str(layer), legacy)
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "duration": 1,
            "anchors": {},
            "sources": {
                "legacy_voice.wav": "old-audio-fingerprint",
                "legacy_unknown.mp4": "old-video-fingerprint",
            },
        }, f)
    monkeypatch.setattr(
        legacy, "_probe_media",
        lambda path: None)
    with pytest.warns(UserWarning, match="旧形式.*音声が脱落"):
        legacy._load_cached_layer(legacy_spec)
    assert legacy.objects[0].source == webm_path

    path_layer = tmp_path / "path_audio_layer.py"
    path_layer.write_text(
        "from pathlib import Path\n"
        "from scriptvedit import *\n"
        "voice = Object(Path('voice.wav'))\n"
        "voice.time(1)\n",
        encoding="utf-8")
    path_project = sv.Project()
    path_project.layer(str(path_layer), cache="make")
    path_spec = path_project._layer_specs[0]
    path_project._exec_layer(path_spec["filename"], path_spec["priority"])
    assert path_project._layer_audio_sources[path_spec["filename"]] == ["voice.wav"]
    with pytest.warns(UserWarning, match="音声はキャッシュ再生時に脱落"):
        path_project._build_layer_cache_cmd(0, str(tmp_path / "path.mkv"))

    unknown_layer = tmp_path / "unknown_video_layer.py"
    unknown_layer.write_text(
        "from scriptvedit import *\n"
        "clip = Object('unknown.mp4')\n"
        "clip.time(1)\n",
        encoding="utf-8")
    unknown_project = sv.Project()
    monkeypatch.setattr(unknown_project, "_probe_media", lambda path: None)
    unknown_project.layer(str(unknown_layer), cache="use")
    unknown_spec = unknown_project._layer_specs[0]
    unknown_project._exec_layer(
        unknown_spec["filename"], unknown_spec["priority"])
    assert unknown_project._layer_unknown_audio_sources[unknown_spec["filename"]] == [
        "unknown.mp4"]
    unknown_project.objects = []
    unknown_project._layers = []
    with pytest.warns(UserWarning, match="音声が脱落する可能性"):
        unknown_project._load_cached_layer(unknown_spec)


def test_tile_delete_adelete_and_inertia_normal_paths():
    """未カバーだった公開APIの正常系を固定する"""
    image = sv.Object("image.png")
    assert sv.tile(image, 2, 3, gap=4) is image
    grid = image.transforms[-1]
    assert (grid.name, grid.params) == (
        "grid", {"cols": 2, "rows": 3, "gap": 4})

    clip = sv.Object("clip.mp4")
    clip._has_audio = True
    clip <= sv.delete()
    assert not clip.has_video and clip.has_audio

    clip2 = sv.Object("clip.mp4")
    clip2._has_audio = True
    clip2 <= sv.adelete()
    assert clip2.has_video and not clip2.has_audio

    slow_decay = sv.inertia(1.0, -0.5, damping=1.0, x0=0.2, y0=0.7)
    fast_decay = sv.inertia(1.0, -0.5, damping=4.0, x0=0.2, y0=0.7)
    assert slow_decay.name == "move"
    assert slow_decay.params["x"].eval_at(0) == pytest.approx(0.2)
    assert slow_decay.params["y"].eval_at(0) == pytest.approx(0.7)
    assert slow_decay.params["x"].eval_at(1) > fast_decay.params["x"].eval_at(1)
