# -*- coding: utf-8 -*-
"""issue #15 残り(P1x3+P2x2) と issue #16 一部(F/G/H) の回帰テスト

A: morph/particle のフレーム数 floor → 0フレーム問題（max(1, ceil) へ統一）
B: morph_to/assemble_from が加工済みObjectを黙って素通しする問題（明示拒否）
C: 奇数解像度が h264/webm で遅く失敗する問題（レンダ開始前に日本語拒否）
D: 複数 duck_under/loop の黙殺（適用時点で拒否）
E: detect_beats の入口検証（FFmpegデコード前に日本語ValueError）
F: from_project 署名にプラグイン指紋と loudnorm 出力設定
G: text系フォント実ファイルが layer cache 依存に入る
H: compute 鍵に Project 解像度（pctx）
"""

import base64
import os
import sys
import types

import pytest

import scriptvedit as sv


# 1x1 の正しいPNG（ffprobe可能な実素材。PIL非依存でテスト素材を作るため）
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


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


# ---------------------------------------------------------------------------
# A: morph/particle のフレーム数
# ---------------------------------------------------------------------------

def test_morph_frame_count_never_floors_to_zero():
    """短尺・非整数尺でも 0 フレームにならず全尺を覆う（web Objectと同方針）"""
    from scriptvedit.project import _morph_frame_count

    assert _morph_frame_count(30, 0.01) == 1      # 旧実装は int(0.3) == 0
    assert _morph_frame_count(30, 1.05) == 32     # 旧実装は 31（末尾を欠く）
    assert _morph_frame_count(30, 2) == 60        # 整数尺は従来どおり
    assert _morph_frame_count(30, 1 / 30) == 1
    assert _morph_frame_count(10, 0.25) == 3      # ceil(2.5)


def _install_fake_morph_module(monkeypatch, captured):
    """scriptvedit.morph を numpy 非依存の偽モジュールへ差し替え、
    project._process_checkpoints から渡される n_frames を記録する"""
    fake = types.ModuleType("scriptvedit.morph")
    fake.MORPH_PARAM_KEYS = frozenset({"max_pixels", "seed"})
    fake.PARTICLE_PARAM_KEYS = frozenset({"max_pixels", "seed", "speed"})

    def _rec(kind):
        def _gen(*args, **kwargs):
            # generate_rgba_frames(a, b, out, n_frames) /
            # generate_*_frames(a, out, n_frames)
            n_frames = args[3] if kind == "morph" else args[2]
            captured.append((kind, n_frames))
        return _gen

    fake.generate_rgba_frames = _rec("morph")
    fake.generate_explode_frames = _rec("explode")
    fake.generate_assemble_frames = _rec("assemble")
    monkeypatch.setitem(sys.modules, "scriptvedit.morph", fake)
    return fake


def _fake_cache_writer(cmd, out, timeout=None):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as f:
        f.write(b"fake-cache")


@pytest.mark.parametrize("dur,expected", [(0.01, 1), (1.05, 32)])
def test_particle_and_morph_bake_use_ceil_frame_count(
        tmp_path, monkeypatch, dur, expected):
    """explode_to/assemble_from/morph_to のベイクが max(1, ceil(fps*dur)) を使う"""
    import scriptvedit.cache as cache_module
    import scriptvedit.project as project_module

    captured = []
    _install_fake_morph_module(monkeypatch, captured)
    monkeypatch.setattr(cache_module, "_ARTIFACT_DIR", str(tmp_path / "art"))
    monkeypatch.setattr(project_module, "_run_ffmpeg_to_cache",
                        _fake_cache_writer)

    src = tmp_path / "src.png"
    src.write_bytes(_PNG_1PX)
    tgt = tmp_path / "tgt.png"
    tgt.write_bytes(_PNG_1PX)

    p = sv.Project()
    sv.Project._current = p

    # explode_to
    obj = sv.Object(str(src))
    obj.time(dur)
    obj <= sv.explode_to()
    p._process_checkpoints(obj)

    # assemble_from
    obj2 = sv.Object(str(src))
    obj2.time(dur)
    obj2 <= sv.assemble_from(sv.Object(str(tgt)))

    p._process_checkpoints(obj2)

    # morph_to
    obj3 = sv.Object(str(src))
    obj3.time(dur)
    obj3 <= sv.morph_to(sv.Object(str(tgt)))
    p._process_checkpoints(obj3)

    assert [c[0] for c in captured] == ["explode", "assemble", "morph"]
    assert all(n == expected for _, n in captured), captured


def test_particle_real_generation_minimal():
    """最小の実生成1本: 0.01秒相当（1フレーム）でもPNGが生成される"""
    morph = pytest.importorskip("scriptvedit.morph")
    Image = pytest.importorskip("PIL.Image")
    import tempfile

    from scriptvedit.project import _morph_frame_count

    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "src.png")
        Image.new("RGBA", (12, 12), (255, 0, 0, 255)).save(src)
        out_dir = os.path.join(tmpdir, "frames")
        n = _morph_frame_count(30, 0.01)
        assert n == 1
        morph.generate_explode_frames(src, out_dir, n, max_pixels=50)
        frames = sorted(f for f in os.listdir(out_dir) if f.endswith(".png"))
        assert frames == ["frame_00000.png"]


# ---------------------------------------------------------------------------
# B: terminal Effect の加工済みObject・media type 早期拒否
# ---------------------------------------------------------------------------

def test_morph_to_rejects_processed_target():
    """Transform/Effect付きtargetは黙殺せず構築時に拒否（compute()を案内）"""
    target = sv.Object("target.png")
    target <= sv.resize(sx=0.5, sy=0.5)
    with pytest.raises(ValueError, match=r"compute\(\)"):
        sv.morph_to(target)

    target2 = sv.Object("target.png")
    target2 <= sv.fade(lambda u: u)
    with pytest.raises(ValueError, match="Transform/Effect"):
        sv.morph_to(target2)


def test_morph_to_rejects_video_target():
    """動画targetは暗黙変換せず明示エラー（画像のみ許可）"""
    with pytest.raises(ValueError, match="画像のみ"):
        sv.morph_to(sv.Object("target.mp4"))
    with pytest.raises(ValueError, match="画像のみ"):
        sv.morph_to(sv.text("もじ"))


def test_assemble_from_rejects_processed_or_video_source():
    """assemble_from の source も同じ契約で拒否する"""
    src = sv.Object("src.png")
    src <= sv.resize(sx=0.5, sy=0.5)
    with pytest.raises(ValueError, match=r"compute\(\)"):
        sv.assemble_from(src)
    with pytest.raises(ValueError, match="画像のみ"):
        sv.assemble_from(sv.Object("src.webm"))


def test_morph_to_still_accepts_plain_image_target():
    """素の画像targetは従来どおり受理される（退行防止）"""
    eff = sv.morph_to(sv.Object("target.png"))
    assert eff.name == "morph_to"
    assert eff._morph_target.source == "target.png"


# ---------------------------------------------------------------------------
# C: 奇数解像度の事前拒否
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("w,h", [(101, 100), (100, 99), (101, 99)])
def test_odd_resolution_rejected_for_h264_before_render(tmp_path, w, h):
    """h264（yuv420p系）は奇数解像度をレンダ開始前に日本語で拒否する"""
    p = sv.Project()
    p.configure(width=w, height=h, fps=10, duration=0.1)
    with pytest.raises(ValueError, match="偶数"):
        p.render(str(tmp_path / "odd.mp4"), dry_run=True)


def test_odd_resolution_rejected_for_webm(tmp_path):
    """webm（VP9 yuva420p）も同様に拒否する"""
    p = sv.Project()
    p.configure(width=101, height=100, fps=10, duration=0.1)
    with pytest.raises(ValueError, match="偶数"):
        p.render(str(tmp_path / "odd.webm"), dry_run=True)


def test_odd_resolution_allowed_for_gif_and_png(tmp_path):
    """GIF/PNG連番は奇数解像度を不必要に拒否しない"""
    for name in ("odd.gif", "odd.png"):
        p = sv.Project()
        p.configure(width=101, height=99, fps=10, duration=0.1)
        result = p.render(str(tmp_path / name), dry_run=True)
        assert "main" in result


def test_even_resolution_still_renders_h264(tmp_path):
    """偶数解像度のmp4は従来どおり通る（退行防止）"""
    p = sv.Project()
    p.configure(width=100, height=100, fps=10, duration=0.1)
    result = p.render(str(tmp_path / "even.mp4"), dry_run=True)
    assert "main" in result


# ---------------------------------------------------------------------------
# D: 複数 duck_under / loop の適用時点拒否
# ---------------------------------------------------------------------------

def test_multiple_duck_under_rejected_at_apply_time():
    """2個目の duck_under は黙殺せず適用時点で日本語ValueError"""
    a = sv.Object("a.wav")
    b = sv.Object("b.wav")
    bgm = sv.Object("bgm.wav")
    bgm <= sv.duck_under(a)
    with pytest.raises(ValueError, match="duck_under"):
        bgm <= sv.duck_under(b)
    # チェーン適用（& 連結）でも同様に拒否
    bgm2 = sv.Object("bgm.wav")
    with pytest.raises(ValueError, match="duck_under"):
        bgm2 <= (sv.duck_under(a) & sv.duck_under(b))
    # 1個目までは適用済み設計ではなく、適用済み分の有無に関わらず例外が出ること
    assert [e.name for e in bgm.audio_effects] == ["duck_under"]


def test_multiple_loop_rejected_at_apply_time():
    """loop も2個目を拒否する"""
    o = sv.Object("bgm.wav")
    o <= sv.loop()
    with pytest.raises(ValueError, match="loop"):
        o <= sv.loop(until=3)
    assert [e.name for e in o.audio_effects] == ["loop"]


def test_duck_under_and_loop_can_coexist():
    """異種のAudioEffect併用は従来どおり可能（退行防止）"""
    a = sv.Object("a.wav")
    bgm = sv.Object("bgm.wav")
    bgm <= sv.loop()
    bgm <= sv.duck_under(a)
    bgm <= sv.avolume(0.5)
    assert [e.name for e in bgm.audio_effects] == ["loop", "duck_under", "avolume"]


# ---------------------------------------------------------------------------
# E: detect_beats の入口検証
# ---------------------------------------------------------------------------

def test_detect_beats_validates_params_before_ffmpeg(monkeypatch):
    """無効な数値パラメータではFFmpegデコード（subprocess）を呼ばない"""
    beat = pytest.importorskip("scriptvedit.beat")

    calls = []
    monkeypatch.setattr(
        beat.subprocess, "run",
        lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(
            AssertionError("検証前にffmpegが呼ばれた")))

    bad_kwargs = [
        {"sr": 0}, {"sr": -1}, {"sr": 22050.5}, {"sr": True},
        {"hop": 0}, {"hop": -512}, {"hop": 512.0},
        {"tightness": 0}, {"tightness": -1.0},
        {"tightness": float("nan")}, {"tightness": float("inf")},
        {"min_bpm": 0}, {"min_bpm": float("nan")},
        {"max_bpm": 0}, {"max_bpm": float("inf")},
        {"min_bpm": 200, "max_bpm": 100},   # min >= max
        {"min_bpm": 120, "max_bpm": 120},
    ]
    for kwargs in bad_kwargs:
        with pytest.raises(ValueError, match="detect_beats"):
            beat.detect_beats("dummy.wav", **kwargs)
    assert calls == []


def test_detect_beats_error_messages_are_japanese():
    """入口検証エラーは日本語で原因を示す"""
    beat = pytest.importorskip("scriptvedit.beat")
    with pytest.raises(ValueError, match="正の整数"):
        beat.detect_beats("dummy.wav", hop=0)
    with pytest.raises(ValueError, match="有限の正数"):
        beat.detect_beats("dummy.wav", tightness=float("nan"))
    with pytest.raises(ValueError, match="小さい値"):
        beat.detect_beats("dummy.wav", min_bpm=180, max_bpm=90)


# ---------------------------------------------------------------------------
# F: from_project 署名（プラグイン指紋・loudnorm）
# ---------------------------------------------------------------------------

def _write_from_project_layers(tmp_path):
    asset = tmp_path / "a.png"
    asset.write_bytes(_PNG_1PX)
    sub_layer = tmp_path / "sub_layer.py"
    sub_layer.write_text(
        "from scriptvedit import *\n"
        f"Object(r'{asset}').time(1)\n",
        encoding="utf-8")
    parent_layer = tmp_path / "parent_layer.py"
    parent_layer.write_text(
        "import os\n"
        "from scriptvedit import *\n"
        "sub = Project()\n"
        f"sub.layer(r'{sub_layer}')\n"
        "if os.environ.get('SV_TEST_ISSUE16_LOUDNORM'):\n"
        "    sub.normalize_audio(-16)\n"
        "o = Object.from_project(sub)\n"
        "o.time(1)\n",
        encoding="utf-8")
    return parent_layer


def _from_project_source(parent_layer, tmp_path, name):
    p = sv.Project()
    p.layer(str(parent_layer))
    p.render(str(tmp_path / name), dry_run=True)
    sources = [o.source for o in p.objects
               if isinstance(o, sv.Object) and "subproject" in o.source]
    assert len(sources) == 1
    return sources[0]


def test_from_project_signature_tracks_plugin_fingerprint(
        tmp_path, monkeypatch):
    """プラグインの code_ffp が変わると from_project のキャッシュpathが変わる
    （プラグインファイルだけ変更した場合に cache='auto' が再生成する。監査の再現）"""
    from scriptvedit.plugins import _EFFECT_PLUGINS

    parent_layer = _write_from_project_layers(tmp_path)
    path_before = _from_project_source(parent_layer, tmp_path, "out1.webm")

    # 「プラグインファイルだけ変更」を code_ffp の変化として再現
    spec = types.SimpleNamespace(code_ffp="changed-ffp", source_file=None)
    monkeypatch.setitem(_EFFECT_PLUGINS, "zz_issue16_dummy", spec)
    path_after = _from_project_source(parent_layer, tmp_path, "out2.webm")
    assert path_before != path_after

    spec.code_ffp = "changed-ffp-2"
    path_after2 = _from_project_source(parent_layer, tmp_path, "out3.webm")
    assert path_after != path_after2


def test_from_project_signature_tracks_loudnorm_target(tmp_path, monkeypatch):
    """サブProjectの normalize_audio 設定も from_project 署名に入る"""
    parent_layer = _write_from_project_layers(tmp_path)
    path_plain = _from_project_source(parent_layer, tmp_path, "ln1.webm")
    monkeypatch.setenv("SV_TEST_ISSUE16_LOUDNORM", "1")
    path_loudnorm = _from_project_source(parent_layer, tmp_path, "ln2.webm")
    assert path_plain != path_loudnorm


# ---------------------------------------------------------------------------
# G: text系フォントの layer cache 依存
# ---------------------------------------------------------------------------

def test_text_font_file_registered_as_layer_dependency(tmp_path, monkeypatch):
    """text Objectの解決済みフォント実ファイルが _layer_sources に入る"""
    font = tmp_path / "testfont.ttf"
    font.write_bytes(b"fake-font-v1")
    monkeypatch.setenv("SCRIPTVEDIT_FONT", str(font))

    layer = tmp_path / "text_layer.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "t = text('こんにちは')\n"
        "t.time(1)\n",
        encoding="utf-8")

    p = sv.Project()
    p.layer(str(layer))
    spec = p._layer_specs[0]
    p._exec_layer(spec["filename"], spec["priority"])
    normalized = [s.replace("\\", "/") for s in p._layer_sources[spec["filename"]]]
    assert str(font).replace("\\", "/") in normalized


def test_font_swap_makes_layer_cache_stale(tmp_path, monkeypatch):
    """TTF内容の差し替えで make 済みキャッシュが auto で stale になる"""
    import scriptvedit.cache as cache_module
    import scriptvedit.project as project_module

    monkeypatch.setattr(cache_module, "_ARTIFACT_DIR", str(tmp_path / "art"))
    font = tmp_path / "testfont.ttf"
    font.write_bytes(b"fake-font-v1")
    monkeypatch.setenv("SCRIPTVEDIT_FONT", str(font))

    layer = tmp_path / "text_layer.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "t = text('こんにちは')\n"
        "t.time(1)\n",
        encoding="utf-8")

    # cache='make' でキャッシュ+メタを生成（ffmpegはフェイク）
    p = sv.Project()
    p.duration = 1
    p.layer(str(layer), cache="make")
    spec = p._layer_specs[0]
    p._exec_layer(spec["filename"], spec["priority"])
    monkeypatch.setattr(p, "_build_layer_cache_cmd",
                        lambda index, out: ["ffmpeg", "-y", out])
    monkeypatch.setattr(project_module, "_run_ffmpeg_to_cache",
                        _fake_cache_writer)
    p._render_layer_to_cache(0)

    # 同一フォントのままなら fresh（フォント登録が鍵の分裂を起こさないこと）
    p_same = sv.Project()
    p_same.duration = 1
    p_same.layer(str(layer), cache="auto")
    spec_same = p_same._layer_specs[0]
    p_same._exec_layer(spec_same["filename"], spec_same["priority"])
    assert p_same._should_use_cache(spec_same) is True

    # フォント内容を差し替え → stale（旧字形のキャッシュを使わない）
    font.write_bytes(b"fake-font-v2-different-glyphs")
    p_swap = sv.Project()
    p_swap.duration = 1
    p_swap.layer(str(layer), cache="auto")
    spec_swap = p_swap._layer_specs[0]
    p_swap._exec_layer(spec_swap["filename"], spec_swap["priority"])
    assert p_swap._should_use_cache(spec_swap) is False


# ---------------------------------------------------------------------------
# H: compute 鍵の Project 解像度（pctx）
# ---------------------------------------------------------------------------

def test_compute_cache_path_separates_by_project_resolution(tmp_path):
    """Project解像度が違えば compute のキャッシュpathが分かれる"""
    src = tmp_path / "src.png"
    src.write_bytes(_PNG_1PX)

    def path_with(width, height, fps=30):
        p = sv.Project()
        p.configure(width=width, height=height, fps=fps)
        sv.Project._current = p
        return sv.Object(str(src))._compute_cache_path()

    path_320 = path_with(320, 180)
    path_1920 = path_with(1920, 1080)
    assert path_320 != path_1920

    # 同一解像度なら同一path（不要な分裂を起こさない）
    assert path_320 == path_with(320, 180)

    # fps 違いも分離
    assert path_with(320, 180, fps=30) != path_with(320, 180, fps=60)

    # Project なしは既定 1280x720@30 と同一視
    sv.Project._current = None
    path_none = sv.Object(str(src))._compute_cache_path()
    assert path_none == path_with(1280, 720)
