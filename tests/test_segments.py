# -*- coding: utf-8 -*-
"""render(video_segments=[...])（segments.py）と render_key() のテスト

- 一体レンダとの一致（画素・コマ数・音声の PCM・チャプター。ffmpeg があるときだけ）
- dry_run の形（main = mux / cache = 音声レグ + 測定）と cold / warm の一致
- 作業ディレクトリの名前が出力パスに依らないこと
- render_key: cold / warm の一致・素材の1バイトで変わる・出力パスで変わらない・
  無関係な設定で変わらない
エラーケース（コマ数・寸法の不一致、映像 Object、非 h264、区間が無い）は
tests/test_errors.py の check_segments_* にある。
"""
import hashlib
import json
import os
import shutil
import subprocess

import pytest

import scriptvedit as sv
from scriptvedit.cache import _render_cmds_signature
from scriptvedit.context import _exec_stack, activate, current_project
from scriptvedit.segments import _SEGMENTS_WORK_DIR

_HAS_FFMPEG = (shutil.which("ffmpeg") is not None
               and shutil.which("ffprobe") is not None)


@pytest.fixture(autouse=True)
def _restore_project_globals():
    """各テスト後にProjectの暗黙登録先と実行スタックを戻す"""
    old_current = current_project()
    old_stack = list(_exec_stack)
    activate(None)
    _exec_stack[:] = []
    try:
        yield
    finally:
        activate(old_current)
        _exec_stack[:] = old_stack


def _write_layer(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return str(path)


def _require_font():
    from scriptvedit.text import _resolve_font
    try:
        _resolve_font(None)
    except FileNotFoundError as exc:
        pytest.skip(str(exc))


def _make_tone(tmp_path, seconds=3):
    """テスト用の音声素材（正弦波の wav。ffmpeg の lavfi で作る）"""
    wav = str(tmp_path / "tone.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}", "-ar", "48000", wav],
        check=True, timeout=60)
    return wav


def _configure(p):
    p.configure(width=160, height=90, fps=10, background_color="#101820")
    return p


def _audio_body(wav):
    return ("from scriptvedit import *\n"
            f"tone = Object({wav!r})\n"
            "tone.time(3) <= avolume(0.5)\n")


def _build_full_project(tmp_path, wav):
    """映像（動く文字2つ）＋音声の一体レンダ用 Project（3秒・10fps）"""
    p = _configure(sv.Project())
    body = (
        "from scriptvedit import *\n"
        "a = text('AAA', size=20, color='white')\n"
        "a.time(1.5) <= move(from_x=0.1, from_y=0.2, to_x=0.9, to_y=0.8)\n"
        "b = text('BBB', size=20, color='yellow')\n"
        "b.time(1.5) <= move(from_x=0.9, from_y=0.2, to_x=0.1, to_y=0.8)\n")
    p.layer(_write_layer(tmp_path, "l_video.py", body), priority=10)
    p.layer(_write_layer(tmp_path, "l_audio.py", _audio_body(wav)), priority=0)
    p.marker(0, "前半")
    p.marker(1.5, "後半")
    return p


def _build_audio_project(tmp_path, wav):
    """音声 Object と marker だけの Project（区間結合用。音声の配置は一体レンダと同じ）"""
    p = _configure(sv.Project())
    p.layer(_write_layer(tmp_path, "l_audio.py", _audio_body(wav)), priority=0)
    p.marker(0, "前半")
    p.marker(1.5, "後半")
    return p


def _build_dummy_audio_project(tmp_path, *, linear=False):
    """実ファイル不要の音声 Object だけの Project（dry_run 用。probe しない）"""
    p = _configure(sv.Project())
    body = ("from scriptvedit import *\n"
            "o = Object('dummy.wav')\n"
            "o._has_audio = True\n"
            "o.time(6)\n")
    p.layer(_write_layer(tmp_path, "l_dummy.py", body), priority=0)
    if linear:
        p.normalize_audio(mode="linear")
    return p


def _touch_segments(tmp_path, n=2):
    """dry_run 用のダミー区間（存在だけが要る）"""
    paths = []
    for i in range(n):
        path = tmp_path / f"seg_{i}.mp4"
        path.write_bytes(b"")
        paths.append(str(path))
    return paths


def _cut(src, dst, start, n_frames):
    """一体レンダの出力から n_frames 枚の区間を切り出す（再エンコード。音声なし）"""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", str(start), "-i", src, "-an",
         "-frames:v", str(n_frames), "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "12", "-pix_fmt", "yuv420p", dst],
        check=True, timeout=120)
    return dst


def _probe_json(path, *args):
    out = subprocess.run(
        ["ffprobe", "-v", "error", *args, "-of", "json", path],
        capture_output=True, text=True, timeout=120)
    return json.loads(out.stdout)


def _frame_count(path):
    return int(_probe_json(path, "-count_frames", "-select_streams", "v",
                           "-show_entries", "stream=nb_read_frames")
               ["streams"][0]["nb_read_frames"])


def _pcm_md5(path):
    """デコードした音声（s16le 48kHz ステレオ）の MD5"""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vn", "-f", "s16le",
         "-ac", "2", "-ar", "48000", "-"],
        capture_output=True, timeout=120, check=True)
    return hashlib.md5(out.stdout).hexdigest()


# --- 一体レンダとの一致 ---

@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe が無い環境ではスキップ")
def test_segments_match_sequential_render(tmp_path):
    """一体レンダの出力を3本に切って区間結合すると、画素・コマ数・音声・チャプターが一致する"""
    pytest.importorskip("numpy", reason="numpy が無い環境（tools extras）")
    pytest.importorskip("PIL", reason="Pillow が無い環境（tools extras）")
    _require_font()
    from scriptvedit import testkit

    wav = _make_tone(tmp_path)
    ref = str(tmp_path / "ref.mp4")
    _build_full_project(tmp_path, wav).render(ref)
    assert _frame_count(ref) == 30

    # 境目 1.0s / 2.0s で3本（10 + 10 + 10 コマ）
    segs = [_cut(ref, str(tmp_path / f"part{i}.mp4"), i, 10) for i in range(3)]
    assert [_frame_count(s) for s in segs] == [10, 10, 10]

    out = str(tmp_path / "joined.mp4")
    _build_audio_project(tmp_path, wav).render(out, video_segments=segs)
    assert os.path.exists(out)
    assert not os.path.exists(_SEGMENTS_WORK_DIR) or not any(
        name.startswith("segments_") for name in os.listdir(_SEGMENTS_WORK_DIR)), \
        "作業ディレクトリが消えていない"

    assert _frame_count(out) == 30
    for at in (0.0, 0.9, 1.0, 1.1, 1.5, 2.0, 2.9):
        a = testkit.extract_frame(ref, at)
        b = testkit.extract_frame(out, at)
        score = testkit.ssim(a, b)
        assert score > 0.99, f"t={at}s のフレームが一致しません (SSIM={score:.4f})"
    # 音声は同じ音声グラフを AAC で符号化したもの（デコード後の PCM が一致）
    assert _pcm_md5(out) == _pcm_md5(ref)
    chapters = _probe_json(out, "-show_chapters")["chapters"]
    assert [c.get("tags", {}).get("title") for c in chapters] == ["前半", "後半"]


# --- dry_run ---

def test_segments_dry_run_shape(tmp_path):
    segs = _touch_segments(tmp_path)
    p = _build_dummy_audio_project(tmp_path, linear=True)
    p.marker(1, "章")
    res = p.render(str(tmp_path / "out.mp4"), dry_run=True, video_segments=segs)
    assert set(res) == {"main", "cache"}
    main = res["main"]
    # main は concat demuxer の -c copy と音声・チャプターの mux
    assert main[main.index("-f") + 1] == "concat"
    assert main[main.index("-c") + 1] == "copy"
    assert "-map_chapters" in main
    assert main[-1].endswith("out.mp4")
    list_path = main[main.index("-i") + 1]
    assert os.path.basename(list_path) == "concat.txt"
    assert os.path.dirname(list_path).startswith(_SEGMENTS_WORK_DIR)
    # cache は音声レグ（全編1本・-vn）とラウドネス測定
    audio_paths = [k for k in res["cache"] if k.endswith("audio.m4a")]
    assert len(audio_paths) == 1
    ac = res["cache"][audio_paths[0]]
    assert "-vn" in ac and ac[ac.index("-c:a") + 1] == "aac"
    assert float(ac[ac.index("-t") + 1]) == 6.0
    assert "1:a" in main and audio_paths[0] in main
    measure = [k for k in res["cache"] if k.endswith(".json")]
    assert len(measure) == 1
    assert "loudnorm=print_format=json" in " ".join(res["cache"][measure[0]])
    # 区間の mp4 は main に現れない（concat リストの中。実レンダ直前に書く）
    assert not any(a.endswith("seg_0.mp4") for a in main)


def test_segments_dry_run_cold_warm_identical(tmp_path):
    """測定 JSON や作業ディレクトリの有無で dry_run が変わらない"""
    segs = _touch_segments(tmp_path)
    out = str(tmp_path / "out.mp4")
    cold = _build_dummy_audio_project(tmp_path, linear=True).render(
        out, dry_run=True, video_segments=segs)
    # warm を模す: 測定結果を置き、作業ディレクトリも作っておく
    measure_path = next(k for k in cold["cache"] if k.endswith(".json"))
    os.makedirs(os.path.dirname(measure_path), exist_ok=True)
    with open(measure_path, "w", encoding="utf-8") as f:
        json.dump({"integrated_lufs": -20.0, "true_peak_dbtp": -3.0,
                   "lra_lu": 5.0, "threshold_lufs": -30.0}, f)
    work_dir = os.path.dirname(cold["main"][cold["main"].index("-i") + 1])
    os.makedirs(work_dir, exist_ok=True)
    try:
        warm = _build_dummy_audio_project(tmp_path, linear=True).render(
            out, dry_run=True, video_segments=segs)
    finally:
        os.remove(measure_path)
        shutil.rmtree(work_dir, ignore_errors=True)
    assert cold == warm
    assert _render_cmds_signature(cold) == _render_cmds_signature(warm)


def test_segments_work_dir_independent_of_output_path(tmp_path):
    """作業ディレクトリ（音声レグ・concat リスト）の名前は出力パスに依らない"""
    segs = _touch_segments(tmp_path)
    r1 = _build_dummy_audio_project(tmp_path).render(
        str(tmp_path / "a.mp4"), dry_run=True, video_segments=segs)
    other = tmp_path / "elsewhere"
    other.mkdir()
    r2 = _build_dummy_audio_project(tmp_path).render(
        str(other / "b.mp4"), dry_run=True, video_segments=segs)
    assert r1["main"][:-1] == r2["main"][:-1]
    assert r1["cache"] == r2["cache"]
    assert _render_cmds_signature(r1) == _render_cmds_signature(r2)
    # 区間が違えば名前も変わる（同じ場所の音声レグを別のレンダと取り合わない）
    r3 = _build_dummy_audio_project(tmp_path).render(
        str(tmp_path / "a.mp4"), dry_run=True, video_segments=segs[:1])
    assert r3["main"][r3["main"].index("-i") + 1] != r1["main"][r1["main"].index("-i") + 1]


def test_segments_without_audio_objects_mux_video_only(tmp_path):
    """音声 Object が無い（marker だけの）Project では音声レグを作らない"""
    segs = _touch_segments(tmp_path)
    p = _configure(sv.Project())
    p.configure(duration=4)
    p.marker(0, "章")
    res = p.render(str(tmp_path / "out.mp4"), dry_run=True, video_segments=segs)
    assert res["cache"] == {}
    assert "-map" in res["main"] and "1:a" not in res["main"]
    assert "-map_chapters" in res["main"]


# --- render_key ---

def _build_key_project(tmp_path, image, *, parallel_workers=None):
    """画像（焼ける fade）＋文字＋音声の Project。チェックポイント・音声・文字を全部踏む"""
    p = _configure(sv.Project())
    if parallel_workers is not None:
        p.configure(parallel=parallel_workers)
    body = (
        "from scriptvedit import *\n"
        f"img = Object({image!r})\n"
        "img.time(1) <= opacity(0.5)\n"
        "o = Object('dummy.wav')\n"
        "o._has_audio = True\n"
        "o.show(1)\n")
    p.layer(_write_layer(tmp_path, "l_key.py", body), priority=0)
    return p


def _copy_image(tmp_path, name="shape.png"):
    src = sv.asset("images/shape_dots.png")
    dst = tmp_path / name
    shutil.copyfile(src, dst)
    return str(dst)


def test_render_key_is_16_hex_and_matches_signature(tmp_path):
    image = _copy_image(tmp_path)
    out = str(tmp_path / "k.mp4")
    key = _build_key_project(tmp_path, image).render_key(out)
    assert len(key) == 16 and int(key, 16) >= 0
    cmds = _build_key_project(tmp_path, image).render(out, dry_run=True)
    assert key == _render_cmds_signature(cmds)


def test_render_key_independent_of_output_path_and_unrelated_settings(tmp_path):
    image = _copy_image(tmp_path)
    k1 = _build_key_project(tmp_path, image).render_key(str(tmp_path / "a.mp4"))
    other = tmp_path / "deep" / "dir"
    other.mkdir(parents=True)
    k2 = _build_key_project(tmp_path, image).render_key(str(other / "renamed.mp4"))
    assert k1 == k2
    # キャッシュ生成のワーカ数は出力に関係しない
    k3 = _build_key_project(tmp_path, image, parallel_workers=4).render_key(
        str(tmp_path / "a.mp4"))
    assert k1 == k3
    # 出力形式（拡張子）は符号化が変わるので別の鍵
    k4 = _build_key_project(tmp_path, image).render_key(str(tmp_path / "a.webm"))
    assert k1 != k4
    # draft も別の鍵
    k5 = _build_key_project(tmp_path, image).render_key(str(tmp_path / "a.mp4"), draft=True)
    assert k1 != k5


def test_render_key_changes_with_one_byte_of_material(tmp_path):
    image = _copy_image(tmp_path)
    out = str(tmp_path / "k.mp4")
    k1 = _build_key_project(tmp_path, image).render_key(out)
    with open(image, "ab") as f:
        f.write(b"\0")  # PNG の末尾へ1バイト（画像としては読めるまま）
    k2 = _build_key_project(tmp_path, image).render_key(out)
    assert k1 != k2
    # 同じ内容なら置き場所が違っても同じ鍵（素材は内容指紋）
    moved = str(tmp_path / "moved.png")
    shutil.copyfile(image, moved)
    k3 = _build_key_project(tmp_path, moved).render_key(out)
    assert k2 == k3


def test_render_key_changes_with_layout(tmp_path):
    image = _copy_image(tmp_path)
    out = str(tmp_path / "k.mp4")
    p = _build_key_project(tmp_path, image)
    k1 = p.render_key(out)
    body = (
        "from scriptvedit import *\n"
        f"img = Object({image!r})\n"
        "img.time(2) <= opacity(0.5)\n")  # 尺が違う
    p2 = _configure(sv.Project())
    p2.layer(_write_layer(tmp_path, "l_key2.py", body), priority=0)
    assert p2.render_key(out) != k1


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe が無い環境ではスキップ")
def test_render_key_same_before_and_after_real_render(tmp_path):
    """実レンダでチェックポイント・測定 JSON が実体化しても鍵は変わらない（cold / warm）"""
    image = _copy_image(tmp_path)
    wav = _make_tone(tmp_path, 1)
    body = (
        "from scriptvedit import *\n"
        f"img = Object({image!r})\n"
        "img.time(1) <= opacity(0.5)\n"
        f"tone = Object({wav!r})\n"
        "tone.show(1)\n")

    def build():
        p = _configure(sv.Project())
        p.normalize_audio(mode="linear")
        p.layer(_write_layer(tmp_path, "l_real.py", body), priority=0)
        return p

    out = str(tmp_path / "real.mp4")
    cold = build().render_key(out)
    build().render(out)
    cmds = build().render(out, dry_run=True)
    # 焼いたチェックポイントと測定 JSON が実在する（= warm）ことを確かめてから比べる
    assert cmds["cache"], "cache 側のコマンドが無い（チェックポイントが計画されていない）"
    assert all(os.path.exists(k) for k in cmds["cache"]), "実レンダ後も生成物が無い"
    assert build().render_key(out) == cold
