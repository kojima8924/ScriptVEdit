# -*- coding: utf-8 -*-
"""overlay 前段の可視区間 trim（監査 項目7）のテスト。

開始時刻 start のオブジェクトは、trim が無いと毎レンダで `start*fps` 枚の
不可視フレームを drawtext/geq/scale/fade に通す。N 個並べると総フレーム処理量が
O(N^2) になるため、`_build_video_overlay_parts` は tpad 直後（tpad の無い画像
入力ではチェーン先頭）で可視区間の外を捨てる。

ここで固定する不変条件:

1. trim 区間は overlay の enable 区間と**同一**（前後2フレームの余白付き）。
   狭めると絵が消え、広げると O(N^2) が戻る。
2. duration 未確定（enable が付かない）オブジェクトは trim しない。
   enable が無いオブジェクトはタイムライン全体へ合成されうる（progress_bar 等）。
3. trim を除いたコマンドは trim 導入前と完全一致する（＝差分は trim だけ）。
4. 時間分割並列レンダは「可視区間 ∩ チャンク区間」を渡す。
"""
import re

import pytest

import scriptvedit as sv
from scriptvedit import Object, Project, asset, fade, progress_bar, text
from scriptvedit.context import _exec_stack, activate, current_project
from scriptvedit.filters.video import _build_video_overlay_parts, _visible_window


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


def _write_layer(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return str(path)


def _filter_complex(cmd):
    """コマンド列から -filter_complex の値を取り出す"""
    return cmd[cmd.index("-filter_complex") + 1]


_TRIM_RE = re.compile(
    r"trim=(?:start=[0-9.e+-]+(?::end=[0-9.e+-]+)?|end=[0-9.e+-]+)")


# --- 1. 区間そのもの ---

def test_visible_window_matches_enable_window():
    """可視区間は enable 区間 ± 2フレームであること"""
    o = Object(asset("images/shape_badge.png"))
    o.start_time = 5.0
    o.duration = 2.0
    t_from, t_to = _visible_window(o, 30)
    assert t_from == pytest.approx(5.0 - 2 / 30)
    assert t_to == pytest.approx(7.0 + 2 / 30)
    # 余白は fps 依存（フレーム境界の判定誤差を吸収する2フレームぶん）
    t_from15, t_to15 = _visible_window(o, 15)
    assert t_from15 == pytest.approx(5.0 - 2 / 15)
    assert t_to15 == pytest.approx(7.0 + 2 / 15)


def test_visible_window_clamps_to_zero():
    """開始が 0 付近でも負の trim を出さない"""
    o = Object(asset("images/shape_badge.png"))
    o.start_time = 0.0
    o.duration = 2.0
    assert _visible_window(o, 30)[0] == 0.0


def test_visible_window_is_open_when_duration_unknown():
    """duration 未確定（enable なし）は区間を絞らない"""
    o = Object(asset("images/shape_badge.png"))
    o.start_time = 4.0
    o.duration = None
    assert _visible_window(o, 30) == (0.0, None)


# --- 2. フィルタチェーンへの反映 ---

def _parts_for(obj, window):
    p = Project()
    p.configure(width=320, height=180, fps=30)
    activate(p)
    try:
        parts, _label = _build_video_overlay_parts(
            obj, 1, "[0:v]", obj.duration or 5, visible_window=window)
    finally:
        activate(None)
    return ";".join(parts)


def test_trim_inserted_after_tpad():
    """tpad（タイムライン整列）の直後に trim が入ること"""
    o = Object(asset("video/flowerbg_noaudio.mp4"))
    o.start_time = 5.0
    o.duration = 2.0
    graph = _parts_for(o, _visible_window(o, 30))
    m = re.search(r"tpad=start_duration=5\.0:start_mode=clone,"
                  r"trim=start=([0-9.]+):end=([0-9.]+)", graph)
    assert m, graph
    assert float(m.group(1)) == pytest.approx(5.0 - 2 / 30)
    assert float(m.group(2)) == pytest.approx(7.0 + 2 / 30)


def test_trim_at_chain_head_for_image():
    """tpad の無い画像入力ではチェーン先頭に trim が入ること"""
    o = Object(asset("images/shape_badge.png"))
    o.start_time = 2.0
    o.duration = 1.0
    graph = _parts_for(o, _visible_window(o, 30))
    assert graph.startswith("[1:v]trim=start="), graph


def test_no_trim_when_window_is_open():
    """(0.0, None) を渡したら trim を一切出さないこと"""
    o = Object(asset("images/shape_badge.png"))
    o.start_time = 0.0
    o.duration = None
    graph = _parts_for(o, (0.0, None))
    assert "trim=" not in graph


# --- 3. 実レンダコマンドでの回帰 ---

def test_render_cmd_trims_offset_object(tmp_path):
    """開始が遅いオブジェクトのチェーンに可視区間 trim が入ること"""
    p = Project()
    p.configure(width=320, height=180, fps=30, background_color="black")
    p.layer(_write_layer(tmp_path, "l_win.py",
                         "from scriptvedit import *\n"
                         "a = text('AAA', size=24)\n"
                         "a.time(2)\n"
                         "b = text('BBB', size=24)\n"
                         "b.time(2)\n"), priority=0)
    cmd = p.render(str(tmp_path / "o.mp4"), dry_run=True)["main"]
    fc = _filter_complex(cmd)
    # 2本目は 2..4 秒 → trim=start=(2-2/30):end=(4+2/30)
    assert f"trim=start={2 - 2 / 30}:end={4 + 2 / 30}" in fc, fc
    # 1本目は 0..2 秒 → 先頭は落とさず終端だけ
    assert f"trim=end={2 + 2 / 30}" in fc, fc


def test_render_cmd_keeps_duration_less_object_intact(tmp_path):
    """duration 未確定のオブジェクト（progress_bar）を trim しないこと。

    progress_bar は start_time が総尺（タイムライン末尾）でも enable が付かず、
    tpad のクローンフレームで t=0 から見えている。ここを可視区間として
    絞ると全編で絵が消える（実レンダの framemd5 が壊れた実際の回帰）。
    """
    p = Project()
    p.configure(width=320, height=180, fps=30, background_color="black")
    p.layer(_write_layer(tmp_path, "l_pb.py",
                         "from scriptvedit import *\n"
                         "a = text('AAA', size=24)\n"
                         "a.time(2)\n"
                         "progress_bar(height=8, color='orange')\n"), priority=0)
    cmd = p.render(str(tmp_path / "o.mp4"), dry_run=True)["main"]
    fc = _filter_complex(cmd)
    # progress_bar のチェーン（tpad=start_duration=2 で始まる）に trim が無いこと
    chain = [c for c in fc.split(";") if "tpad=start_duration=2" in c]
    assert chain, fc
    assert "trim=" not in chain[0], chain[0]


def test_trim_removal_restores_pre_trim_command(tmp_path, monkeypatch):
    """trim を取り除いたコマンドが「trim 導入前」と完全一致すること。

    可視区間 trim 以外の差分をコマンドへ持ち込んでいないことの証明。
    """
    def build():
        p = Project()
        p.configure(width=320, height=180, fps=30, background_color="black")
        p.layer(_write_layer(tmp_path, "l_eq.py",
                             "from scriptvedit import *\n"
                             "a = Object(asset('images/shape_badge.png'))\n"
                             "a.time(2) <= fade(1)\n"
                             "b = text('BBB', size=24)\n"
                             "b.time(2)\n"), priority=0)
        return _filter_complex(p.render(str(tmp_path / "o.mp4"), dry_run=True)["main"])

    with_trim = build()
    monkeypatch.setattr("scriptvedit.project._visible_window",
                        lambda obj, fps: (0.0, None))
    without_trim = build()
    assert _TRIM_RE.search(with_trim), with_trim
    assert _strip_trims(with_trim) == without_trim


def _strip_trims(graph):
    """filtergraph から trim フィルタを外し、素通しになったチェーンを畳む。

    trim を1つだけ持つチェーンは除去後「[1:v][obj1]」という素通し行になる。
    trim 無しのコードはその行自体を作らない（フィルタが空なら入力ラベルを
    直接 overlay へ渡す）ので、ここで畳んで形を揃える。
    """
    kept = []
    alias = {}
    for chain in graph.split(";"):
        m_head = re.match(r"^(?:\[[^\[\]]*\])+", chain)
        head = m_head.group(0) if m_head else ""
        rest = chain[len(head):]
        m_tail = re.search(r"(?:\[[^\[\]]*\])+$", rest)
        tail = m_tail.group(0) if m_tail else ""
        body = rest[:len(rest) - len(tail)]
        filters = [f for f in re.split(r"(?<!\\),", body)
                   if f and not _TRIM_RE.fullmatch(f)]
        if not filters and head.count("[") == 1 and tail.count("[") == 1:
            alias[tail] = head
            continue
        kept.append(head + ",".join(filters) + tail)
    out = ";".join(kept)
    for dst, src in alias.items():
        out = out.replace(dst, src)
    return out


# --- 4. 時間分割並列レンダ ---

def test_parallel_chunk_window_is_intersection(tmp_path):
    """チャンクは「可視区間 ∩ チャンク区間」で trim すること"""
    from scriptvedit.parallel import _build_chunk_ffmpeg_cmd

    p = Project()
    p.configure(width=320, height=180, fps=10, background_color="black")
    p.layer(_write_layer(tmp_path, "l_par.py",
                         "from scriptvedit import *\n"
                         "a = text('AAA', size=24)\n"
                         "a.time(3)\n"
                         "b = text('BBB', size=24)\n"
                         "b.time(3)\n"), priority=0)
    p.render(str(tmp_path / "o.mp4"), dry_run=True)  # プラン解決＋レイヤーexec

    # チャンク0 = 0.0〜3.0s: 先頭は落とさず、チャンク終端(3.0+2/10)で切る
    fc0 = _filter_complex(_build_chunk_ffmpeg_cmd(p, str(tmp_path / "c0.mp4"), 0, 30, 2))
    assert "trim=start=" not in fc0, fc0
    assert f"trim=end={3.0 + 2 / 10}" in fc0, fc0

    # チャンク1 = 3.0〜6.0s: チャンク開始の2フレーム手前から
    fc1 = _filter_complex(_build_chunk_ffmpeg_cmd(p, str(tmp_path / "c1.mp4"), 30, 60, 2))
    assert f"trim=start={3.0 - 2 / 10}" in fc1, fc1
