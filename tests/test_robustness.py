# -*- coding: utf-8 -*-
"""静的レビュー(issue #2)で見つかった asset/Web/cache/CLI の堅牢性テスト

対象:
  1. asset() のパストラバーサル拒否（..・絶対パス・ドライブレター）
  2. Web Object の短尺 duration（0フレーム化の防止）
  3. Web frames_dir の stale フレーム掃除
  4. キャッシュ生成一時パスのユニーク化（並列衝突防止）
  6. cache --clear/--gc の任意ディレクトリ削除ガード
  7. scriptvedit new --force の既存ファイル .bak 退避
"""
import os
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from scriptvedit.assets import asset  # noqa: E402
from scriptvedit.cache import cache_clear, cache_gc  # noqa: E402
from scriptvedit.cli import _main  # noqa: E402
from scriptvedit.ffmpeg import _run_ffmpeg_to_cache, _unique_tmp_path  # noqa: E402
from scriptvedit.filters.video import (  # noqa: E402
    _build_effect_filters, _try_native_fade,
)
from scriptvedit.objects import _web_frame_count  # noqa: E402
from scriptvedit.scaffold import new_project  # noqa: E402


# --- issue #9: native fade の線形判定 -------------------------------------

def test_native_fade_rejects_step_window():
    """矩形窓はランプへ近似せず、正確なgeq経路へフォールバックする"""
    from scriptvedit import Var, and_, gt, lt

    u = Var("u")
    alpha = and_(gt(u, 0.25), lt(u, 0.75))
    assert _try_native_fade(alpha, 0, 4) is None


def test_native_fade_keeps_linear_triangle():
    """真の区分線形ランプは従来どおりnative fadeを使う"""
    from scriptvedit import fade

    effect = fade(lambda u: 1 - abs(2 * u - 1))
    assert _try_native_fade(effect.params["alpha"], 0, 4) == [
        "fade=t=in:st=0:d=2.0:alpha=1",
        "fade=t=out:st=2.0:d=2.0:alpha=1",
    ]


def test_step_window_fade_real_render_has_no_alpha_leak(tmp_path):
    """実レンダ後の抽出フレームで矩形窓外のalphaが0になる"""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い環境")

    from scriptvedit import Var, and_, fade, gt, lt

    u = Var("u")
    effect = fade(lambda _u: and_(gt(u, 0.25), lt(u, 0.75)))
    obj = SimpleNamespace(effects=[effect])
    filters, _ = _build_effect_filters(obj, 0, 4)
    assert any(part.startswith("geq=") for part in filters)
    assert not any(part.startswith("fade=t=") for part in filters)

    rendered = tmp_path / "step-window.mkv"
    render_cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=white:s=4x4:r=100:d=4",
        "-vf", ",".join(filters),
        "-c:v", "ffv1", "-pix_fmt", "yuva444p", str(rendered),
    ]
    subprocess.run(render_cmd, check=True, capture_output=True, timeout=30)

    def _alpha_at(u_value):
        extract_cmd = [
            "ffmpeg", "-v", "error", "-i", str(rendered),
            "-ss", str(u_value * 4), "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1",
        ]
        result = subprocess.run(
            extract_cmd, check=True, capture_output=True, timeout=30)
        assert len(result.stdout) == 4 * 4 * 4
        return result.stdout[3]

    assert [_alpha_at(u) for u in (0.10, 0.25, 0.75, 0.90)] == [0] * 4
    assert [_alpha_at(u) for u in (0.26, 0.50, 0.74)] == [255] * 3


# --- issue #13 P1-2: 音声のみプロジェクトの -map [0:v] --------------------

def _write_audio_only_layer(tmp_path, wav_path):
    layer = tmp_path / "audio_only_layer.py"
    layer.write_text(
        "from scriptvedit import *\n"
        f"o = Object(r\"{wav_path}\")\n"
        "o.time(1) <= avolume(0.5)\n",
        encoding="utf-8")
    return layer


def test_audio_only_project_maps_raw_video_input(tmp_path):
    """音声Object+音声フィルタのみでも -map がグラフラベル扱いにならない"""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い環境")
    from scriptvedit import Project

    wav = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", str(wav)],
        check=True, capture_output=True, timeout=30)

    layer = _write_audio_only_layer(tmp_path, wav)
    p = Project()
    p.configure(width=320, height=180, fps=30)
    p.layer(str(layer))
    result = p.render(str(tmp_path / "out.mp4"), dry_run=True)
    cmd = result["main"]
    assert "-filter_complex" in cmd, "音声フィルタでfilter_complexが作られる前提"
    map_targets = [cmd[i + 1] for i, a in enumerate(cmd[:-1]) if a == "-map"]
    assert "0:v" in map_targets, f"-map の対象が不正: {map_targets}"
    assert "[0:v]" not in map_targets, "生入力参照をグラフ出力ラベルとして-mapしている"


def test_audio_only_project_real_render(tmp_path):
    """音声のみプロジェクトの実レンダが成功し、映像+音声の両streamを持つ"""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe が無い環境")
    from scriptvedit import Project

    wav = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", str(wav)],
        check=True, capture_output=True, timeout=30)

    layer = _write_audio_only_layer(tmp_path, wav)
    out = tmp_path / "out.mp4"
    p = Project()
    p.configure(width=320, height=180, fps=30)
    p.layer(str(layer))
    p.render(str(out), timeout=120)

    assert out.exists() and out.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(out)],
        check=True, capture_output=True, text=True, timeout=30)
    kinds = sorted(probe.stdout.split())
    assert kinds == ["audio", "video"], f"stream構成が不正: {kinds}"


# --- issue #13 P2-7: レイヤーキャッシュのfail-closedとparam追跡 ------------

def _make_p27_project(tmp_path, cache_mode):
    """param()を使う小レイヤーのプロジェクトを組み立てる"""
    from scriptvedit import Project

    png = tmp_path / "dot27.png"
    if not png.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", "color=c=red:s=8x8:d=1", "-frames:v", "1", str(png)],
            check=True, capture_output=True, timeout=30)
    layer = tmp_path / "param_layer.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "from scriptvedit.context import current_project\n"
        "_p = current_project()\n"
        "_msg = _p.param('msg', 'a')\n"   # 出力尺に影響しないparam
        f"o = Object(r\"{png}\")\n"
        "o.time(1)\n",
        encoding="utf-8")
    p = Project()
    p.configure(width=160, height=90, fps=30)
    p.layer(str(layer), cache=cache_mode)
    return p, layer


def test_layer_cache_fail_closed_and_param_tracking(tmp_path, monkeypatch):
    """メタ欠損/破損はstale扱い、paramの解決値変更でキャッシュを使わない"""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い環境")
    from scriptvedit.cache import _layer_cache_paths

    monkeypatch.delenv("SCRIPTVEDIT_PARAM_msg", raising=False)
    p1, layer = _make_p27_project(tmp_path, "make")
    p1.render(str(tmp_path / "m.mp4"), timeout=120)
    cache_video, cache_json = _layer_cache_paths(str(layer), p1)
    assert os.path.exists(cache_video) and os.path.exists(cache_json)

    def _auto_uses_cache():
        p, _ = _make_p27_project(tmp_path, "auto")
        result = p.render(str(tmp_path / "a.mp4"), dry_run=True)
        cmd = result["main"]
        return cache_video.replace("\\", "/") in \
            " ".join(cmd).replace("\\", "/")

    try:
        # 同一条件 → 使う
        assert _auto_uses_cache(), "同一条件でキャッシュが使われない"

        # param の解決値が変わる → 使わない
        monkeypatch.setenv("SCRIPTVEDIT_PARAM_msg", "b")
        assert not _auto_uses_cache(), "param変更後もキャッシュを使っている"
        monkeypatch.delenv("SCRIPTVEDIT_PARAM_msg")
        assert _auto_uses_cache()

        # メタ破損 → 使わない（fail-closed）
        with open(cache_json, "w", encoding="utf-8") as f:
            f.write("{broken json")
        assert not _auto_uses_cache(), "破損メタなのにキャッシュを使っている"

        # メタ欠損 → 使わない（成果物と完了メタの整合を必須化）
        os.remove(cache_json)
        assert not _auto_uses_cache(), "メタ欠損なのにキャッシュを使っている"
    finally:
        for path in (cache_video, cache_json):
            try:
                os.remove(path)
            except OSError:
                pass


# --- issue #13 P2-10: キャッシュ用アンカー計算と正規リゾルバの一致 ---------

def test_layer_data_anchor_matches_resolver_with_show(tmp_path):
    """show()（非進行）の後のanchorが、正規リゾルバとキャッシュ用メタで一致する"""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い環境")
    from scriptvedit import Project

    png = tmp_path / "dot.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=red:s=8x8:d=1", "-frames:v", "1", str(png)],
        check=True, capture_output=True, timeout=30)
    layer = tmp_path / "show_anchor_layer.py"
    layer.write_text(
        "from scriptvedit import *\n"
        f"o = Object(r\"{png}\")\n"
        "o.show(5)\n"          # 非進行（時刻を進めない）
        "anchor('A')\n"
        f"o2 = Object(r\"{png}\")\n"
        "o2.time(1)\n",        # 総尺が0にならないよう通常オブジェクトも置く
        encoding="utf-8")

    p = Project()
    p.configure(width=160, height=90, fps=30)
    p.layer(str(layer))
    p.render(str(tmp_path / "out.mp4"), dry_run=True)

    canonical = p._anchors.get("A")
    _, meta_anchors = p._get_layer_data(0)
    assert canonical == 0, f"正規リゾルバのA={canonical}（show()は非進行のはず）"
    assert meta_anchors.get("A") == canonical, (
        f"キャッシュ用メタのanchorが正規リゾルバとずれている: "
        f"meta={meta_anchors.get('A')}, canonical={canonical}")


# --- issue #13 P2-13: 固定格子サンプリングのエイリアシング -----------------

def test_scale_pad_covers_intersample_peak():
    """i/100格子とエイリアスする振動scale式でもpadが実ピークを覆う"""
    from scriptvedit import PI, sin
    from scriptvedit.filters.video import _expr_has_oscillatory
    from scriptvedit.effects.basic import scale

    # 全ての i/100 標本で値1、点間ピークは1.5
    e = scale(lambda u: 1 + 0.5 * sin(100 * PI * u))
    scale_expr = e.params["value"]
    assert _expr_has_oscillatory(scale_expr)
    # 従来の101点では max=1.0 に見える（バグの前提を固定）
    coarse = max(scale_expr.eval_at(i / 100) for i in range(101))
    assert abs(coarse - 1.0) < 1e-6
    # 密格子では実ピークを検出する
    dense = max(scale_expr.eval_at(i / 4999) for i in range(5000))
    assert dense > 1.45


def test_oscillatory_fade_never_native():
    """ランプ+微小振動のalpha式はnative fadeへ誤変換しない"""
    from scriptvedit import PI, sin
    from scriptvedit.filters.video import _try_native_fade
    from scriptvedit.effects.basic import fade

    # 各 i/100 標本ではちょうど線形ランプに一致するが、点間では振動する
    e = fade(lambda u: u + 0.2 * sin(200 * PI * u))
    assert _try_native_fade(e.params["alpha"], 0, 4) is None

    # 素の区分線形ランプ（振動なし）は引き続きnative化される
    e2 = fade(lambda u: 1 - abs(2 * u - 1))
    assert _try_native_fade(e2.params["alpha"], 0, 4) is not None


# --- issue #13 P2-19: 公開API・OS互換 -------------------------------------

def test_watch_is_public_export():
    """READMEの例がstar import前提のため、watch は __all__ に含まれる"""
    import scriptvedit
    assert "watch" in scriptvedit.__all__
    assert callable(scriptvedit.watch)


def test_library_dirs_accepts_both_separators(monkeypatch, tmp_path):
    """SCRIPTVEDIT_ASSETS は os.pathsep 区切り（`;` は互換で常に通る）"""
    from scriptvedit.assets import library_dirs

    a = str(tmp_path / "a")
    b = str(tmp_path / "b")
    monkeypatch.setenv("SCRIPTVEDIT_ASSETS", f"{a};{b}")
    assert library_dirs() == [os.path.abspath(a), os.path.abspath(b)]

    monkeypatch.setenv("SCRIPTVEDIT_ASSETS", f"{a}{os.pathsep}{b}")
    assert library_dirs() == [os.path.abspath(a), os.path.abspath(b)]


# --- issue #13 P2-9: ffprobeメモ化の素材差し替え検知 ----------------------

def test_probe_cache_detects_replaced_file(tmp_path):
    """同一パスへ素材を差し替えたら probe 結果も更新される"""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe が無い環境")
    from scriptvedit import Project

    wav = tmp_path / "swap.wav"

    def _make(duration):
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", f"sine=frequency=440:duration={duration}", str(wav)],
            check=True, capture_output=True, timeout=30)

    p = Project()
    _make(0.1)
    info1 = p._probe_media(str(wav))
    assert info1 and abs(info1["duration"] - 0.1) < 0.05, info1

    _make(0.4)  # 同一パスへ差し替え（サイズ・mtimeが変わる）
    info2 = p._probe_media(str(wav))
    assert info2 and abs(info2["duration"] - 0.4) < 0.05, \
        f"差し替え後も旧情報を返している: {info2}"

    # 同一内容の再問い合わせはメモ化が効く（同じdictを返す）
    assert p._probe_media(str(wav)) is info2


# --- issue #13 P1-4: レイヤーキャッシュ鍵の出力duration -------------------

def test_layer_cache_key_includes_duration():
    """総尺が違えばレイヤーキャッシュのパスも変わる（-t焼き込みのため）"""
    from scriptvedit import Project
    from scriptvedit.cache import _layer_cache_paths

    p3 = Project()
    p3.configure(duration=3)
    p30 = Project()
    p30.configure(duration=30)
    path3, _ = _layer_cache_paths(__file__, p3)
    path30, _ = _layer_cache_paths(__file__, p30)
    assert path3 != path30, "総尺を変えてもキャッシュパスが同一"


def test_layer_cache_duration_change_regenerates(tmp_path):
    """cache='make'後に総尺を変えると'auto'はキャッシュを再生成する"""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe が無い環境")
    from scriptvedit import Project
    from scriptvedit.cache import _layer_cache_paths

    png = tmp_path / "red.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=red:s=40x40:d=1", "-frames:v", "1", str(png)],
        check=True, capture_output=True, timeout=30)
    layer = tmp_path / "dur_layer.py"
    layer.write_text(
        "from scriptvedit import *\n"
        f"o = Object(r\"{png}\")\n"
        "o.time(1) <= move(x=0.5, y=0.5, anchor=\"center\")\n",
        encoding="utf-8")

    def _project(duration, cache_mode):
        p = Project()
        p.configure(width=160, height=90, fps=30, duration=duration)
        p.layer(str(layer), cache=cache_mode)
        return p

    made = []
    try:
        p1 = _project(1, "make")
        p1.render(str(tmp_path / "d1.mp4"), timeout=120)
        cache1, _ = _layer_cache_paths(str(layer), p1)
        made.append(cache1)
        assert os.path.exists(cache1)
        # キャッシュには総尺(1秒)が焼き込まれている
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", cache1],
            check=True, capture_output=True, text=True, timeout=30)
        assert abs(float(probe.stdout.strip()) - 1.0) < 0.2, probe.stdout

        # 同一総尺の 'auto' は既存キャッシュを使う（dry_runコマンドに現れる）
        cmd_same = _project(1, "auto").render(
            str(tmp_path / "d1b.mp4"), dry_run=True)
        flat_same = " ".join(cmd_same["main"])
        assert cache1.replace("\\", "/") in flat_same.replace("\\", "/"), \
            "同一総尺でキャッシュが使われていない"

        # 総尺を2秒へ変更 → 鍵が変わり、旧キャッシュ(1秒焼き込み)は使われない
        p2 = _project(2, "auto")
        cache2, _ = _layer_cache_paths(str(layer), p2)
        assert cache2 != cache1, "総尺変更後もキャッシュパスが同一"
        cmd_changed = p2.render(str(tmp_path / "d2.mp4"), dry_run=True)
        flat = " ".join(cmd_changed["main"])
        assert cache1.replace("\\", "/") not in flat.replace("\\", "/"), \
            "総尺変更後も旧キャッシュを入力に使っている"
    finally:
        for path in made:
            base = os.path.splitext(path)[0]
            for target in (path, base + ".anchors.json"):
                try:
                    os.remove(target)
                except OSError:
                    pass


# --- issue #13 P1-3: レイヤーキャッシュ再利用時のalpha保持 -----------------

def test_layer_cache_paths_use_webm_extension():
    """レイヤーキャッシュはVP9+alphaのため.webm（デコーダ強制が効く拡張子）"""
    from scriptvedit import Project
    from scriptvedit.cache import _layer_cache_paths

    p = Project()
    video_path, json_path = _layer_cache_paths(__file__, p)
    assert video_path.endswith(".webm"), video_path
    assert json_path.endswith(".anchors.json"), json_path


def test_layer_cache_make_use_preserves_alpha(tmp_path):
    """cache='make'→'use' の往復で透過が保持され、背景色が透けて見える"""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い環境")
    from scriptvedit import Project
    from scriptvedit.cache import _layer_cache_paths

    # 中央に置く小さな赤画像（周囲はキャンバスの透過部分になる）
    png = tmp_path / "red.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=red:s=40x40:d=1", "-frames:v", "1", str(png)],
        check=True, capture_output=True, timeout=30)

    layer = tmp_path / "cached_layer.py"
    layer.write_text(
        "from scriptvedit import *\n"
        f"o = Object(r\"{png}\")\n"
        "o.time(1) <= move(x=0.5, y=0.5, anchor=\"center\")\n",
        encoding="utf-8")

    def _render(cache_mode, out_name):
        p = Project()
        p.configure(width=160, height=90, fps=30, background_color="blue")
        p.layer(str(layer), cache=cache_mode)
        out = tmp_path / out_name
        p.render(str(out), timeout=120)
        return p, out

    p1, _ = _render("make", "out_make.mp4")
    cache_video, _ = _layer_cache_paths(str(layer), p1)
    assert os.path.exists(cache_video), "レイヤーキャッシュが生成されていない"
    try:
        _, out2 = _render("use", "out_use.mp4")

        # 左上隅（透過部分）のピクセル: alphaが保持されていれば背景の青、
        # alphaが落ちるとキャッシュの黒ベタが背景を覆う
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(out2),
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            check=True, capture_output=True, timeout=30)
        r, g, b = result.stdout[0], result.stdout[1], result.stdout[2]
        assert b > 128 and r < 100, \
            f"透過部分に背景色(青)が見えない: RGB=({r},{g},{b})"
        # 中央ピクセルは赤画像
        w, h = 160, 90
        center = ((h // 2) * w + w // 2) * 3
        cr, cb = result.stdout[center], result.stdout[center + 2]
        assert cr > 128 and cb < 100, \
            f"中央に赤画像が見えない: R={cr}, B={cb}"
    finally:
        # グローバル__cache__を汚さない（このテスト専用鍵だが掃除しておく）
        for path in (cache_video,
                     _layer_cache_paths(str(layer), p1)[1]):
            try:
                os.remove(path)
            except OSError:
                pass


# --- 1. asset() のパストラバーサル拒否 -----------------------------------

@pytest.fixture
def fake_project(tmp_path, monkeypatch):
    """assets/ を持つ最小プロジェクトを作り、そこへ chdir する"""
    proj = tmp_path / "proj"
    (proj / "assets" / "images").mkdir(parents=True)
    (proj / "assets" / "images" / "a.png").write_bytes(b"\x89PNG fake")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    monkeypatch.chdir(proj)
    return proj


def test_asset_normal_resolution(fake_project):
    """正常系: assets/ 配下の素材は絶対パスで解決される"""
    p = asset("images/a.png")
    assert os.path.isabs(p) and os.path.exists(p)
    assert os.path.basename(p) == "a.png"


def test_asset_rejects_parent_traversal(fake_project):
    """`..` を含む指定は assets/ の外に出るため拒否される"""
    with pytest.raises(ValueError, match=r"'\.\.' を含むパスは指定できません"):
        asset("../secret.txt")
    with pytest.raises(ValueError, match=r"'\.\.' を含むパスは指定できません"):
        asset("images/../../secret.txt")
    with pytest.raises(ValueError, match=r"'\.\.' を含むパスは指定できません"):
        asset("images\\..\\..\\secret.txt")
    # must_exist=False でも拒否される（出力先組み立て経路の悪用防止）
    with pytest.raises(ValueError):
        asset("../secret.txt", must_exist=False)


def test_asset_rejects_absolute_and_drive_paths(fake_project, tmp_path):
    """絶対パス・ドライブレター付きパスは拒否される"""
    with pytest.raises(ValueError, match="絶対パス"):
        asset(str(tmp_path / "secret.txt"))
    with pytest.raises(ValueError, match="絶対パス"):
        asset("C:/Windows/win.ini")
    with pytest.raises(ValueError, match="絶対パス"):
        asset("C:relative_to_drive.txt")  # ドライブ相対も拒否
    with pytest.raises(ValueError, match="絶対パス"):
        asset("/etc/passwd")


def test_asset_rejects_empty(fake_project):
    """空・`.` のみの指定は拒否される"""
    with pytest.raises(ValueError, match="空です"):
        asset("")
    with pytest.raises(ValueError, match="空です"):
        asset("./.")


def test_asset_missing_gives_filenotfound(fake_project):
    """存在しない正当な相対パスは従来どおり FileNotFoundError（候補付き）"""
    with pytest.raises(FileNotFoundError):
        asset("images/nothing.png")


# --- 2. Web Object の短尺 duration ---------------------------------------

def test_web_frame_count_min_one():
    """duration < 1/fps でも最低1フレームを保証する（旧: int(dur*fps)=0）"""
    assert _web_frame_count(0.01, 30) == 1
    assert _web_frame_count(0.001, 30) == 1


def test_web_frame_count_ceil():
    """端数は切り上げて全尺をカバーする"""
    assert _web_frame_count(1.0, 30) == 30
    assert _web_frame_count(0.5, 30) == 15
    assert _web_frame_count(0.05, 30) == 2  # 1.5 → 2


def test_web_frame_count_rejects_nonpositive():
    """duration/fps が 0 以下・非数値は明確なエラー"""
    with pytest.raises(ValueError, match="duration"):
        _web_frame_count(0, 30)
    with pytest.raises(ValueError, match="duration"):
        _web_frame_count(-1, 30)
    with pytest.raises(ValueError, match="fps"):
        _web_frame_count(1.0, 0)


def test_web_object_rejects_nonpositive_duration(tmp_path, monkeypatch):
    """web Object 構築時に duration<=0 / 非数値を拒否する"""
    from scriptvedit.objects import Object
    html = tmp_path / "scene.html"
    html.write_text("<html></html>", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="duration は正の数値"):
        Object(str(html), duration=0, size=(320, 180))
    with pytest.raises(ValueError, match="duration は正の数値"):
        Object(str(html), duration=-2.0, size=(320, 180))
    with pytest.raises(ValueError, match="duration は正の数値"):
        Object(str(html), duration="3", size=(320, 180))


# --- 3. Web frames_dir の stale フレーム掃除 -------------------------------

def test_web_frames_dir_cleared_before_render(tmp_path, monkeypatch):
    """_render_web_frames は生成前に frames_dir を空にする（stale 混入防止）。

    Playwright 起動前に到達する検証: sync_playwright を差し替え、
    掃除後の frames_dir の状態を観測してから中断する。
    """
    from scriptvedit.objects import Object
    from scriptvedit.state import _CACHE_DIR

    html = tmp_path / "scene.html"
    html.write_text("<html><script>function renderFrame(s){}</script></html>",
                    encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    obj = Object.__new__(Object)
    obj._web_source = str(html)
    obj._web_size = (320, 180)
    obj._web_fps = 30
    obj._web_data = {}
    obj._web_name = "robustness_stale"
    obj._web_debug_frames = False
    obj._web_deps = []
    obj.duration = 0.5

    frames_dir = os.path.join(_CACHE_DIR, "webclip", "robustness_stale_frames")
    os.makedirs(frames_dir, exist_ok=True)
    stale = os.path.join(frames_dir, "frame_99999.png")
    with open(stale, "wb") as f:
        f.write(b"stale")

    observed = {}

    class _Stop(Exception):
        pass

    def fake_sync_playwright():
        # rmtree + makedirs の後に呼ばれる → この時点の中身を観測して中断
        observed["listing"] = os.listdir(frames_dir)
        raise _Stop()

    import types as _types
    fake_mod = _types.ModuleType("playwright.sync_api")
    fake_mod.sync_playwright = fake_sync_playwright
    fake_pkg = _types.ModuleType("playwright")
    fake_pkg.sync_api = fake_mod
    monkeypatch.setitem(sys.modules, "playwright", fake_pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_mod)

    class _FakeProject:
        fps = 30

    with pytest.raises(_Stop):
        obj._render_web_frames(_FakeProject())
    assert observed["listing"] == []  # stale フレームは掃除済み
    assert not os.path.exists(stale)
    # 後片付け
    import shutil
    shutil.rmtree(frames_dir, ignore_errors=True)


# --- 4. キャッシュ生成一時パスのユニーク化 ---------------------------------

def test_unique_tmp_path_differs():
    """同じ最終パスから生成される一時パスは毎回異なり、拡張子は維持される"""
    p1 = _unique_tmp_path("out/final.webm")
    p2 = _unique_tmp_path("out/final.webm")
    assert p1 != p2
    assert p1.endswith(".webm") and p2.endswith(".webm")
    assert os.path.dirname(p1) == "out"  # 同一ディレクトリ（os.replace が原子的）


def test_run_ffmpeg_to_cache_parallel_no_collision(tmp_path, monkeypatch):
    """同じ cache_path へ並行到達しても一時パスが衝突しない"""
    import scriptvedit.ffmpeg as ffmpeg_mod
    cache_path = str(tmp_path / "clip.webm")
    tmp_paths = []
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=10)

    def fake_run(cmd, timeout=600, context=None):
        out = cmd[-1]
        barrier.wait()  # 2スレッドを同時に走らせて固定名なら衝突する状況を作る
        with lock:
            tmp_paths.append(out)
        with open(out, "wb") as f:
            f.write(b"webm")

    monkeypatch.setattr(ffmpeg_mod, "_run_ffmpeg", fake_run)
    cmd = ["ffmpeg", "-y", "-i", "in.png", cache_path]

    errors = []

    def worker():
        try:
            _run_ffmpeg_to_cache(list(cmd), cache_path)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(set(tmp_paths)) == 2  # 一時パスが互いに異なる
    assert os.path.exists(cache_path)
    # 一時ファイルの残骸が無い
    leftovers = [n for n in os.listdir(tmp_path) if ".tmp" in n]
    assert leftovers == []


def test_run_ffmpeg_to_cache_keeps_safety_valve(tmp_path):
    """既存の安全装置: cmd 内に cache_path が無ければ ValueError（維持されている）"""
    with pytest.raises(ValueError, match="cache_path"):
        _run_ffmpeg_to_cache(["ffmpeg", "-y", "-i", "in.png", "other.webm"],
                             str(tmp_path / "clip.webm"))


# --- 6. cache --clear/--gc の削除ガード ------------------------------------

def test_cache_clear_refuses_non_cache_dir(tmp_path):
    """__cache__ 配下でないディレクトリは既定で拒否される"""
    target = tmp_path / "not_cache"
    target.mkdir()
    (target / "important.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="__cache__"):
        cache_clear(str(target))
    assert (target / "important.txt").exists()  # 消えていない


def test_cache_gc_refuses_non_cache_dir(tmp_path):
    """--gc も同じガードが効く"""
    target = tmp_path / "not_cache2"
    target.mkdir()
    with pytest.raises(ValueError, match="__cache__"):
        cache_gc(0, str(target))


def test_cache_clear_allows_cache_dir(tmp_path):
    """__cache__ という名前のディレクトリは force なしで削除できる"""
    target = tmp_path / "__cache__"
    (target / "artifacts").mkdir(parents=True)
    (target / "artifacts" / "x.webm").write_bytes(b"x")
    cache_clear(str(target))
    assert not target.exists()


def test_cache_clear_force_allows_with_warning(tmp_path):
    """force=True なら __cache__ 外でも警告付きで削除できる（--yes 相当）"""
    target = tmp_path / "custom_cache"
    target.mkdir()
    (target / "a.bin").write_bytes(b"x")
    with pytest.warns(UserWarning, match="__cache__"):
        cache_clear(str(target), force=True)
    assert not target.exists()


def test_cli_cache_clear_guard(tmp_path, capsys):
    """CLI 経由: --dir が __cache__ 外なら rc=2 で中断、--yes で実行"""
    target = tmp_path / "victim"
    target.mkdir()
    (target / "keep.txt").write_text("x", encoding="utf-8")
    rc = _main(["cache", "--clear", "--dir", str(target)])
    assert rc == 2
    assert (target / "keep.txt").exists()
    err = capsys.readouterr().err
    assert "--yes" in err
    with pytest.warns(UserWarning):
        rc2 = _main(["cache", "--clear", "--dir", str(target), "--yes"])
    assert rc2 == 0
    assert not target.exists()


# --- 7. scriptvedit new --force の .bak 退避 --------------------------------

def test_new_project_force_backs_up_modified_files(tmp_path, capsys):
    """force=True の再生成で、編集済みファイルは .bak に退避される"""
    root = new_project(str(tmp_path / "proj"), quiet=True)
    main_py = os.path.join(root, "main.py")
    original = open(main_py, encoding="utf-8", newline="").read()
    edited = "# ユーザーの編集\n" + original
    with open(main_py, "w", encoding="utf-8", newline="") as f:
        f.write(edited)

    new_project(root, force=True)
    out = capsys.readouterr().out
    assert ".bak に退避" in out

    bak = main_py + ".bak"
    assert os.path.exists(bak)
    assert open(bak, encoding="utf-8", newline="").read() == edited  # 旧内容が残る
    assert open(main_py, encoding="utf-8", newline="").read() == original  # 雛形に戻る


def test_new_project_force_skips_identical_files(tmp_path):
    """内容が同一のファイルは .bak を作らずスキップする"""
    root = new_project(str(tmp_path / "proj2"), quiet=True)
    new_project(root, force=True, quiet=True)
    baks = []
    for dirpath, _dirs, files in os.walk(root):
        baks += [f for f in files if f.endswith(".bak")]
    assert baks == []
