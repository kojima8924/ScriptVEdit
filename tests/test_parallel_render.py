# -*- coding: utf-8 -*-
"""render(parallel=N) 時間分割並列レンダのテスト

- 境界計算（フレーム境界の正確さ・縮退）
- 後方互換（parallel未指定/1 は従来経路と同一コマンド）
- チャンクコマンドの構造（PTSシフト・head_trim・窓外Object除外・-frames:v）
- 音声レグ（全編1本・映像なし）と concat mux
- 非対応形式/部分レンダとの併用時のフォールバック
- 実ffmpegでの結合スモーク（ffmpeg/ffprobe があるときのみ）
"""
import json
import os
import shutil
import subprocess

import pytest

import scriptvedit as sv
from scriptvedit.project import Project


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


def _write_layer(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return str(path)


def _build_project(tmp_path, *, with_audio=False):
    """text 2つ（前半3s/後半3s）＋任意で全編音声のミニプロジェクト"""
    p = sv.Project()
    p.configure(width=320, height=180, fps=10, background_color="#000000")
    body = "from scriptvedit import *\n"
    if with_audio:
        # 実ファイル不要の音声Object（probeを避けて has_audio を直接確定）。
        # show() は current_time に置くため、テキストより前=t0に配置する
        body += (
            "o = Object('dummy.wav')\n"
            "o._has_audio = True\n"
            "o.show(6)\n")
    body += (
        "a = text('AAA', x=0.5, y=0.3, size=24, color='white')\n"
        "a.time(3)\n"
        "b = text('BBB', x=0.5, y=0.6, size=24, color='yellow')\n"
        "b.time(3)\n")
    p.layer(_write_layer(tmp_path, "l_par.py", body), priority=10)
    return p


def _mock_run(monkeypatch):
    """_run_ffmpeg を差し替えてコマンドを記録し、出力ファイルだけ作る"""
    calls = []

    def fake_run(cmd, timeout=None, **kwargs):
        # 実装側の追加キーワード(context= 等)も受ける。署名変更でモックが壊れると
        # 「実装の失敗」ではなく「モックの失敗」を報告してしまうため
        calls.append(list(cmd))
        out = cmd[-1]
        # 連番PNG等は置換不要（本テストでは単一ファイル出力のみ扱う）
        with open(out, "wb"):
            pass

    # 逐次経路は project、並列経路（チャンク/音声レグ/mux）は parallel が参照する
    monkeypatch.setattr("scriptvedit.project._run_ffmpeg", fake_run)
    monkeypatch.setattr("scriptvedit.parallel._run_ffmpeg", fake_run)
    return calls


# --- 境界計算 ---

def test_chunk_bounds_equal_split_on_frame_grid():
    n_total, bounds = Project._parallel_chunk_bounds(12.0, 30, 4)
    assert n_total == 360
    assert bounds == [0, 90, 180, 270, 360]
    # 境界は整数フレーム番号（= k/fps に正確に一致する時刻）
    assert all(isinstance(b, int) for b in bounds)


def test_chunk_bounds_uneven_duration():
    # 176.3s * 30fps = 5289フレーム（浮動小数の丸めがあっても総数が正確）
    n_total, bounds = Project._parallel_chunk_bounds(176.3, 30, 4)
    assert n_total == 5289
    assert bounds[0] == 0 and bounds[-1] == 5289
    assert len(bounds) == 5
    # 単調増加かつ全チャンク1フレーム以上
    assert all(b1 < b2 for b1, b2 in zip(bounds, bounds[1:]))


def test_chunk_bounds_degenerates_when_chunks_exceed_frames():
    # 総フレーム数(2) < 分割数(4) → 2チャンクへ縮退
    n_total, bounds = Project._parallel_chunk_bounds(0.05, 30, 4)
    assert n_total == 2
    assert bounds == [0, 1, 2]
    # 1フレームなら1チャンク
    n_total, bounds = Project._parallel_chunk_bounds(0.01, 30, 8)
    assert n_total == 1
    assert bounds == [0, 1]


def test_chunk_bounds_n1_is_whole():
    n_total, bounds = Project._parallel_chunk_bounds(5.0, 10, 1)
    assert (n_total, bounds) == (50, [0, 50])


# --- 後方互換 ---

def test_parallel_none_and_1_use_sequential_path(tmp_path, monkeypatch):
    calls = _mock_run(monkeypatch)
    out1 = str(tmp_path / "o1.mp4")
    _build_project(tmp_path).render(out1)
    cmd_default = calls[-1]

    out2 = str(tmp_path / "o2.mp4")
    _build_project(tmp_path).render(out2, parallel=1)
    cmd_par1 = calls[-1]

    # 出力パス（一時パス化されている）以外は完全一致 = 従来経路
    assert cmd_default[:-1] == cmd_par1[:-1]
    assert len(calls) == 2  # 分割・concatは走っていない


def test_parallel_dry_run_is_unchanged(tmp_path):
    d1 = _build_project(tmp_path).render(str(tmp_path / "d.mp4"), dry_run=True)
    d2 = _build_project(tmp_path).render(
        str(tmp_path / "d.mp4"), dry_run=True, parallel=4)
    assert d1 == d2
    assert set(d1.keys()) == {"main", "cache"}


def test_parallel_rejects_invalid_values(tmp_path):
    p = _build_project(tmp_path)
    with pytest.raises(ValueError):
        p.render(str(tmp_path / "x.mp4"), parallel=0)
    with pytest.raises(ValueError):
        p.render(str(tmp_path / "x.mp4"), parallel=True)
    with pytest.raises(ValueError):
        p.render(str(tmp_path / "x.mp4"), parallel="2")


# --- チャンクコマンドの構造 ---

def test_parallel_chunk_command_structure(tmp_path, monkeypatch):
    calls = _mock_run(monkeypatch)
    out = str(tmp_path / "par.mp4")
    _build_project(tmp_path).render(out, parallel=2)

    chunk_cmds = [c for c in calls if c[-1].endswith(".mp4")
                  and os.path.basename(c[-1]).startswith("chunk_")]
    mux_cmds = [c for c in calls if "concat" in c]
    assert len(chunk_cmds) == 2
    assert len(mux_cmds) == 1

    c0 = next(c for c in chunk_cmds if c[-1].endswith("chunk_000.mp4"))
    c1 = next(c for c in chunk_cmds if c[-1].endswith("chunk_001.mp4"))
    fc0 = c0[c0.index("-filter_complex") + 1]
    fc1 = c1[c1.index("-filter_complex") + 1]

    # chunk0 はPTSシフトなし＝従来レンダと同一のフィルタ文字列（前半Objectのみ）
    assert "setpts=PTS+" not in fc0
    assert "trim=start=" not in fc0
    # chunk1 は背景PTS+3.0シフト → 評価後に-3.0で戻す
    assert "[0:v]setpts=PTS+3.0/TB[chbase]" in fc1
    assert "setpts=PTS-3.0/TB[chout]" in fc1
    # chunk1 の各Objectは頭破棄（境界誤差ぶん2フレーム手前=2.8sから）
    assert "trim=start=2.8" in fc1

    # 窓外Objectの除外: a=[0,3) は chunk1(t>=3.0) に不要、b=[3,6) は chunk0 に不要
    assert fc0.count("drawtext") == 1
    assert fc1.count("drawtext") == 1
    # フィルタ式は絶対時刻基準のまま（chunk1 に b の絶対 enable が残る）
    assert "between(t\\,3\\,6)" in fc1

    # フレーム数の正確さ: 6.0s*10fps=60 → 30+30
    for c in (c0, c1):
        assert c[c.index("-frames:v") + 1] == "30"
        assert c[c.index("-t") + 1] == "3.0"
        assert c[c.index("-threads") + 1].isdigit()
        assert "-an" in c

    # mux: concat(-c copy)のみで再エンコードなし。最終出力は一時パス
    mux = mux_cmds[0]
    assert mux[mux.index("-f") + 1] == "concat"
    assert "-safe" in mux and "-c" in mux
    assert mux[mux.index("-c") + 1] == "copy"
    assert os.path.exists(out)  # os.replaceで確定済み


def test_parallel_audio_leg_and_mux(tmp_path, monkeypatch):
    calls = _mock_run(monkeypatch)
    out = str(tmp_path / "para.mp4")
    _build_project(tmp_path, with_audio=True).render(out, parallel=2)

    audio_cmds = [c for c in calls if c[-1].endswith("audio.m4a")]
    assert len(audio_cmds) == 1
    ac = audio_cmds[0]
    # 音声レグ: 全編1本（-t 6.0）・映像なし・aac 160k
    assert "-vn" in ac
    assert ac[ac.index("-c:a") + 1] == "aac"
    assert float(ac[ac.index("-t") + 1]) == 6.0
    # 映像ストリームは -map されない（音声のみ）
    maps = [ac[i + 1] for i, a in enumerate(ac) if a == "-map"]
    assert all(":v" not in m and not m.startswith("[v") for m in maps)

    # チャンクは音声を持たない
    chunk_cmds = [c for c in calls if c[-1].endswith(".mp4")
                  and os.path.basename(c[-1]).startswith("chunk_")]
    assert chunk_cmds and all("-an" in c for c in chunk_cmds)

    # mux は映像=concat / 音声=audio.m4a を -c copy で束ねる
    mux = next(c for c in calls if "concat" in c)
    assert any(a.endswith("audio.m4a") for a in mux)
    assert "1:a" in mux
    assert mux[mux.index("-c") + 1] == "copy"


# --- フォールバック ---

def test_parallel_falls_back_for_non_h264(tmp_path, monkeypatch, capsys):
    calls = _mock_run(monkeypatch)
    _build_project(tmp_path).render(str(tmp_path / "o.gif"), parallel=4)
    assert len(calls) == 1  # 単発の従来レンダ
    assert "従来レンダ" in capsys.readouterr().out


def test_parallel_falls_back_with_render_window(tmp_path, monkeypatch, capsys):
    calls = _mock_run(monkeypatch)
    _build_project(tmp_path).render(
        str(tmp_path / "o.mp4"), parallel=4, start=1.0, end=2.0)
    assert len(calls) == 1
    cmd = calls[0]
    assert "-ss" in cmd  # 従来の部分レンダ経路
    assert "従来レンダ" in capsys.readouterr().out


# --- 実ffmpegでの結合スモーク ---

@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe が無い環境ではスキップ")
def test_parallel_real_render_frame_count(tmp_path):
    """背景のみの実レンダで、逐次と並列のフレーム数・尺が一致すること"""

    def build():
        p = sv.Project()
        p.configure(width=160, height=90, fps=10, background_color="#203040")
        return p  # Objectなし → 総尺はフォールバックの5s（50フレーム）

    seq = str(tmp_path / "seq.mp4")
    par = str(tmp_path / "par.mp4")
    build().render(seq)
    build().render(par, parallel=2)

    def frames(path):
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v",
             "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=60)
        return int(out.stdout.strip())

    assert frames(seq) == frames(par) == 50


# --- 実ffmpegでの画素一致・チャプター・失敗系（監査項目24） ---
#
# parallel.py の docstring が主張する中核の性質（背景のPTSを+t0し全フィルタ
# 評価後に-t0で戻せば各チャンクは全編レンダと同一フレームになる）は、コマンド
# 文字列のモック検証では確かめられない。ここで実画素を比較して固定する。

_HAS_FFMPEG = (shutil.which("ffmpeg") is not None
               and shutil.which("ffprobe") is not None)


def _require_font():
    from scriptvedit.text import _resolve_font
    try:
        _resolve_font(None)
    except FileNotFoundError as exc:
        pytest.skip(str(exc))


def _build_moving_project(tmp_path):
    """160x90/10fps/3秒。前半・後半に1つずつ動くテキストを置く

    総尺3秒 × parallel=3 で境界は t=1.0 / t=2.0。境界をまたぐ move と、
    境界の前後で切り替わる enable の両方を1本で踏む。
    """
    p = sv.Project()
    p.configure(width=160, height=90, fps=10, background_color="#101820")
    body = (
        "from scriptvedit import *\n"
        "a = text('AAA', size=20, color='white')\n"
        "a.time(1.5) <= move(from_x=0.1, from_y=0.2, to_x=0.9, to_y=0.8)\n"
        "b = text('BBB', size=20, color='yellow')\n"
        "b.time(1.5) <= move(from_x=0.9, from_y=0.2, to_x=0.1, to_y=0.8)\n")
    p.layer(_write_layer(tmp_path, "l_pixels.py", body), priority=10)
    return p


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe が無い環境ではスキップ")
def test_parallel_matches_sequential_pixels(tmp_path):
    """逐次レンダと並列レンダのフレームが（境界前後も含めて）一致すること"""
    pytest.importorskip("numpy", reason="numpy が無い環境（tools extras）")
    pytest.importorskip("PIL", reason="Pillow が無い環境（tools extras）")
    _require_font()
    from scriptvedit import testkit

    seq = str(tmp_path / "seq.mp4")
    par = str(tmp_path / "par.mp4")
    _build_moving_project(tmp_path).render(seq)
    _build_moving_project(tmp_path).render(par, parallel=3)

    # チャンク境界(1.0/2.0)の直前・直後を必ず含む
    for at in (0.0, 0.9, 1.0, 1.1, 1.5, 2.0, 2.9):
        a = testkit.extract_frame(seq, at)
        b = testkit.extract_frame(par, at)
        score = testkit.ssim(a, b)
        assert score > 0.99, f"t={at}s のフレームが一致しません (SSIM={score:.4f})"


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe が無い環境ではスキップ")
def test_parallel_real_render_writes_chapters(tmp_path):
    """marker がある並列レンダで FFMETADATA が実際に埋め込まれること"""
    _require_font()
    out = str(tmp_path / "chap.mp4")
    p = _build_moving_project(tmp_path)
    p.marker(0, "イントロ")
    p.marker(1.5, "本編")
    p.render(out, parallel=2)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_chapters", "-of", "json", out],
        capture_output=True, text=True, timeout=120)
    chapters = json.loads(probe.stdout)["chapters"]
    titles = [c.get("tags", {}).get("title") for c in chapters]
    assert titles == ["イントロ", "本編"], probe.stdout


def test_parallel_reports_failing_chunk(tmp_path, monkeypatch):
    """チャンクの1本が失敗したら、どのジョブが落ちたかを名指しで報告する"""
    def fake_run(cmd, timeout=None, **kwargs):
        out = cmd[-1]
        if os.path.basename(out).startswith("chunk_001"):
            raise RuntimeError("わざと失敗")
        with open(out, "wb"):
            pass

    monkeypatch.setattr("scriptvedit.parallel._run_ffmpeg", fake_run)
    with pytest.raises(RuntimeError, match="並列レンダの chunk1"):
        _build_project(tmp_path).render(str(tmp_path / "o.mp4"), parallel=2)


def test_parallel_falls_back_when_too_few_frames(tmp_path, capsys):
    """総フレーム数が分割に足りないときは通知して従来レンダへ落ちる"""
    from scriptvedit.parallel import _parallel_chunk_count

    p = _build_project(tmp_path)
    p.duration = 0.05          # 10fps → 総1フレーム＝分割不能
    p.fps = 10
    assert _parallel_chunk_count(p, 4, str(tmp_path / "o.mp4")) == 1
    assert "従来レンダ" in capsys.readouterr().out
