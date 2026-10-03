# -*- coding: utf-8 -*-
"""flow_graph / flow_tree（点と線の図の上をパケットが流れて広がる）の回帰テスト。

src/scriptvedit/fx_flow.py。確かめること:

  (a) 経路: BFS の段・Dijkstra の届く秒（辺の delay と hop）
  (b) 配置: layered の決定性と重心法、radial の角度が葉の数に比例する、given の検証
  (c) 動き: 曲がった辺で隣り合うコマの移動量がそろう（5% 未満）・パケットは辺から外れない
  (d) stop で並んだパケットが重ならない・drop の時刻と消え方
  (e) flow_tree の葉の数・amount の合計の保存・直径が √(量) に比例
  (f) draw_frame で数えたパケットの連結成分が n と一致する
  (g) 同じ入力なら鍵と画素が同じ（配置の結果は鍵に入らない）
  (h) 上限とエラー
  (i) 金型5枚（tests/golden/flow/。版 _FLOW_VER と突き合わせる。パケットの札と curve を弱めた
      木を含む）
  (j) 実レンダ（ffmpeg を通した出力の画素）
  (k) レビューで見つかった不具合の回帰: 扇の辺と curve、文字と辺の重なり（auto の置き直しと板）、
      panel が透明なときのパケット、途中の box・dot を通るパケット、大きさの違う止まる列と
      手前のノードを跨ぐ列、効かない値で割れない鍵、作業領域の解放、大きなパケットの窓、
      broadcast の上限のエラー文、パケットの文字の端数の位置、パケットの札の置き場所
      （斜めの辺・box・札どうしを避け、側を保つ）、パケットの札はどのコマでもノード・ノードの
      文字・ほかの札に重ならない（重なるコマは隠して薄れる。見本 s13 の図で確かめる）・側は
      途中のノードの先と止まる所でだけ一度隠れてから変わる・止まった札は後ろの左右に残る、
      辺の集まるノードで curve を弱める（上限は隣の辺との角の間で決まり、layered の扇も弱まる）

numpy・opencv-python・Pillow が無い環境では描画の項目を skip する（理由に語を入れる）。
文字を使う項目は、既定のフォントが無ければ skip する（理由に「フォント」を入れる）。
"""
import gc
import math
import os
import shutil
import subprocess
import tracemalloc
import warnings
import weakref

import pytest

import scriptvedit as sv
from scriptvedit.context import _exec_stack, activate, current_project

from framekit_golden import assert_golden

np = pytest.importorskip("numpy", reason="numpy が無い環境")
pytest.importorskip("cv2", reason="opencv-python（numpy と一緒に入る図の依存）が無い環境")
pytest.importorskip("PIL", reason="Pillow が無い環境")

from scriptvedit import framekit as fk  # noqa: E402
from scriptvedit import fx_flow  # noqa: E402
from scriptvedit.fx_flow import _FLOW_VER, _Renderer, flow_graph, flow_tree  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """各テストを tmp_path で動かす（__cache__ をテストごとに空から始める）"""
    old_current = current_project()
    old_stack = list(_exec_stack)
    activate(None)
    _exec_stack[:] = []
    monkeypatch.chdir(tmp_path)
    try:
        yield
    finally:
        activate(old_current)
        _exec_stack[:] = old_stack


def _proj(w=640, h=360, fps=30):
    """dry_run の Project（build は生成しない。fk.draw_frame でコマを直接描く）"""
    p = sv.Project()
    p.configure(width=w, height=h, fps=fps)
    p._dry_run = True
    return p


def _red_mask(img, rgb=(0xE0, 0x24, 0x1B), tol=60):
    """accent（赤）に近く、ほぼ不透明な画素"""
    d = np.abs(img[..., :3].astype(int) - np.array(rgb)).max(axis=2)
    return (d < tol) & (img[..., 3] > 160)


def _components(mask):
    """4近傍の連結成分の数（scipy に頼らない素朴な塗りつぶし）"""
    seen = np.zeros_like(mask, bool)
    h, w = mask.shape
    count = 0
    for y0, x0 in zip(*np.nonzero(mask)):
        if seen[y0, x0]:
            continue
        count += 1
        stack = [(y0, x0)]
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            for yy, xx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= yy < h and 0 <= xx < w and mask[yy, xx] and not seen[yy, xx]:
                    seen[yy, xx] = True
                    stack.append((yy, xx))
    return count


def _frame_at(obj, t, fps=30):
    return fk.draw_frame(obj, int(round(t * fps)))


def _need_font():
    """既定の日本語フォント（text() と同じ探索）が無い環境では skip する"""
    from scriptvedit.text import _resolve_font
    try:
        _resolve_font(None)
    except (FileNotFoundError, OSError):
        pytest.skip("既定のフォントが無い環境")


# --- (a) 経路 ----------------------------------------------------------------

def test_broadcast_dijkstra_uses_delay_and_hop():
    _proj()
    g = flow_graph(["a", "b", "c", "d", "e"],
                   [("a", "b"), ("b", "d"), ("a", "c", {"delay": 0.2}),
                    ("c", "d", {"delay": 0.2}), ("d", "e")], layout="layered")
    arr = g.broadcast(1.0, "a", hop=0.5)
    assert arr == pytest.approx({"a": 1.0, "b": 1.5, "c": 1.2, "d": 1.4, "e": 1.9})
    # 辺の向きは問わない（e から配っても全体へ届く）
    back = g.broadcast(3.0, "e", hop=0.5)
    assert back["a"] == pytest.approx(3.0 + 0.5 + 0.2 + 0.2)
    # 最初に届く秒が figure.arrival に残る
    obj = g.build()
    assert obj.figure.arrival["d"] == pytest.approx(1.4)
    assert obj.figure.arrival["e"] == pytest.approx(1.9)


def test_layered_rows_follow_bfs_depth():
    _proj()
    g = flow_graph(["r", "a", "b", "c", "x"],
                   [("r", "a"), ("r", "b"), ("a", "c"), ("b", "c"), ("c", "x")],
                   layout="layered")
    ys = {k: v[1] for k, v in g.pos.items()}
    assert ys["r"] < ys["a"] == ys["b"] < ys["c"] < ys["x"]
    # 'layer' で段を上書きできる
    g2 = flow_graph({"r": {}, "a": {}, "b": {"layer": 3}}, [("r", "a"), ("r", "b")],
                    layout="layered")
    assert g2.pos["b"][1] > g2.pos["a"][1]


def test_send_returns_arrival_by_arc_length():
    _proj()
    g = flow_graph({"a": {"pos": (100, 180)}, "b": {"pos": (500, 180)}}, [("a", "b")])
    out = g.send(0.5, ["a", "b"], n=3, every=0.2, speed=400)
    L = 400 - 2 * (10 + fx_flow._GAP)       # 両端はノードの縁から 6px 手前で切る
    assert out == pytest.approx([0.5 + L / 400, 0.7 + L / 400, 0.9 + L / 400])


# --- (b) 配置 ----------------------------------------------------------------

def _crossing_graph():
    # 段1 の並び（入力順）は a, b、段2 は y, x。a-x・b-y を結ぶと交差する。
    return (["r", "a", "b", "y", "x"],
            [("r", "a"), ("r", "b"), ("a", "x"), ("b", "y")])


def test_layered_is_deterministic_and_untangles():
    _proj()
    nodes, edges = _crossing_graph()
    p1 = flow_graph(nodes, edges, layout="layered").pos
    p2 = flow_graph(nodes, edges, layout="layered").pos
    assert p1 == p2
    # 重心法: x は a の下（左）、y は b の下（右）へ並び替わり、辺が交差しない
    assert p1["a"][0] < p1["b"][0]
    assert p1["x"][0] < p1["y"][0]


def test_radial_angle_is_proportional_to_leaves():
    _proj(800, 800)
    # A の下に葉3つ、B の下に葉1つ → A の扇は 3/4・B は 1/4
    g = flow_graph(["root", "A", "B", "a1", "a2", "a3", "b1"],
                   [("root", "A"), ("root", "B"), ("A", "a1"), ("A", "a2"), ("A", "a3"),
                    ("B", "b1")], layout="radial")
    cx, cy = g.pos["root"]

    def ang(name):
        """真上（-π/2）から時計回りに測った角度"""
        x, y = g.pos[name]
        return (math.atan2(y - cy, x - cx) + math.pi / 2) % (2 * math.pi)
    # A の扇は [0, 3π/2)・B は [3π/2, 2π)。ノードは扇の真ん中
    assert ang("A") == pytest.approx(0.75 * math.pi)
    assert ang("B") == pytest.approx(1.75 * math.pi)
    leaves = [ang(n) for n in ("a1", "a2", "a3")]
    assert leaves == pytest.approx([0.25 * math.pi, 0.75 * math.pi, 1.25 * math.pi])
    assert ang("b1") == pytest.approx(1.75 * math.pi)
    # 同じ深さは同じ半径
    r = [math.hypot(g.pos[n][0] - cx, g.pos[n][1] - cy) for n in ("a1", "a2", "a3", "b1")]
    assert max(r) - min(r) < 1e-9


def test_rings_and_given():
    _proj()
    g = flow_graph(["c", "p", "q", "r"], [("c", "p"), ("c", "q"), ("c", "r")], layout="rings")
    cx, cy = g.pos["c"]
    rr = [math.hypot(g.pos[n][0] - cx, g.pos[n][1] - cy) for n in "pqr"]
    assert max(rr) - min(rr) < 1e-9 and rr[0] > 50
    with pytest.raises(ValueError, match="pos"):
        flow_graph(["a", "b"], [("a", "b")])            # given で pos が無い
    with pytest.raises(ValueError, match="キャンバス"):
        flow_graph({"a": {"pos": (-5, 10)}})


def test_unreachable_nodes_raise_for_layered_and_radial():
    _proj()
    with pytest.raises(ValueError, match="辿れない"):
        # c → d → e → c の輪は、根 a から辿れない
        flow_graph(["a", "b", "c", "d", "e"],
                   [("a", "b"), ("c", "d"), ("d", "e"), ("e", "c")], layout="layered")
    with pytest.raises(ValueError, match="辿れない"):
        # 根（入ってくる辺の無いノード）が a と c の2つ → radial の根は a で、c に届かない
        flow_graph(["a", "b", "c"], [("a", "b"), ("c", "b")], layout="radial")


def test_graph_like_input_is_read_by_duck_typing():
    _proj()

    class _NodeView(dict):
        def __call__(self):
            return list(self)

    class FakeGraph:
        def __init__(self):
            self.nodes = _NodeView({"a": {"pos": (50, 50), "weight": 3},
                                    "b": {"pos": (300, 200), "color": "x"}})

        def edges(self, data=False):
            return [("a", "b", {"delay": 0.1, "capacity": 9})] if data else [("a", "b")]

    g = flow_graph(FakeGraph())
    assert g.pos == {"a": (50.0, 50.0), "b": (300.0, 200.0)}
    assert g.broadcast(0, "a")["b"] == pytest.approx(0.1)
    with pytest.raises(ValueError, match="edges"):
        flow_graph(FakeGraph(), [("a", "b")])


# --- (c) 動き ----------------------------------------------------------------

def _curved():
    _proj()
    g = flow_graph({"a": {"pos": (60, 300)}, "b": {"pos": (580, 300)}},
                   [("a", "b", {"curve": 0.45})])
    g.send(0.0, ["a", "b"], speed=300, trail=0, color="accent", size=9)
    return g


def test_curved_edge_constant_step_and_stays_on_edge():
    g = _curved()
    r = _Renderer(g._snapshot())
    ts = [k / 30 for k in range(3, 45)]
    pts = []
    for t in ts:
        st = r.packet_state(t)
        head = st[5]
        assert head[0]
        pts.append((st[6][0], st[7][0]))
    pts = np.array(pts)
    steps = np.hypot(*np.diff(pts, axis=0).T)
    assert steps.max() / steps.min() < 1.05
    assert steps.mean() == pytest.approx(300 / 30, rel=0.01)
    # 道は描いた辺の折れ線そのもの（点から折れ線までの距離が 0.01px 未満）
    poly = np.array(g._edge_pts[0])
    for x, y in pts:
        a, b = poly[:-1], poly[1:]
        ab = b - a
        tt = np.clip(((x - a[:, 0]) * ab[:, 0] + (y - a[:, 1]) * ab[:, 1])
                     / (ab ** 2).sum(axis=1), 0, 1)
        d = np.hypot(a[:, 0] + tt * ab[:, 0] - x, a[:, 1] + tt * ab[:, 1] - y).min()
        assert d < 0.01


def test_curved_edge_pixel_centroid_moves_evenly():
    """描いたコマの赤い点の重心も、隣り合うコマで同じだけ進む（がたつかない）"""
    g = _curved()
    obj = g.build()
    cents = []
    for i in range(4, 40):
        img = fk.draw_frame(obj, i)
        m = _red_mask(img, tol=40)
        a = img[..., 3].astype(float) * m
        ys, xs = np.nonzero(a)
        w = a[ys, xs]
        cents.append((np.sum(xs * w) / w.sum(), np.sum(ys * w) / w.sum()))
    steps = np.hypot(*np.diff(np.array(cents), axis=0).T)
    assert steps.max() / steps.min() < 1.05


# --- (d) stop / drop -----------------------------------------------------------

def test_stopped_packets_queue_without_overlap():
    _proj(800, 360)
    g = flow_graph({"a": {"pos": (40, 180)}, "b": {"pos": (760, 180)}}, [("a", "b")])
    out = g.send(0.0, ["a", "b"], n=12, every=0.05, speed=900, size=8, trail=0,
                 fate=("stop", "b"))
    assert out == sorted(out)
    r = _Renderer(g._snapshot())
    st = r.packet_state(out[-1] + 0.5)
    xs = np.sort(st[6])
    gaps = np.diff(xs)
    assert gaps == pytest.approx([1.6 * 8] * 11)
    assert gaps.min() > 8            # 直径より広い（重ならない）
    # 先頭は b の縁（半径 10 + 隙間 6）より手前
    assert xs[-1] + 4 < 760 - 16
    obj = g.build()
    img = _frame_at(obj, out[-1] + 0.5)
    assert _components(_red_mask(img)) == 12     # 止まると accent になり、1個ずつ離れて並ぶ
    # 同じ所へ後から止まるパケットは、さらに手前へ並ぶ（13 個目）
    S = out[0] * 900 + (0.8 * 8 + 2)        # 列の先頭が止まる弧長 + 手前へのずらし
    more = g.send(2.0, ["a", "b"], n=2, speed=900, size=8, fate=("stop", "b"))
    assert more[0] - 2.0 == pytest.approx((S - (0.8 * 8 + 2) - 12 * 1.6 * 8) / 900)


def test_drop_time_and_fade():
    _proj()
    g = flow_graph({"a": {"pos": (60, 180)}, "b": {"pos": (580, 180)}}, [("a", "b")])
    (t_drop,) = g.send(0.2, ["a", "b"], speed=500, trail=0, color="accent",
                       fate=("drop", 0.6))
    L = 520 - 2 * 16
    assert t_drop == pytest.approx(0.2 + 0.6 * L / 500)
    obj = g.build()
    x_drop = 60 + 16 + 0.6 * L
    before = _frame_at(obj, t_drop + 0.1)
    m = _red_mask(before, tol=90)
    assert m.any()
    ys, xs = np.nonzero(m & (np.abs(np.arange(m.shape[1]) - x_drop)[None, :] < 6))
    assert xs.size, "drop した位置にパケットが無い"
    after = _frame_at(obj, t_drop + fx_flow._DROP_FADE + 0.05)
    assert not _red_mask(after, tol=90).any()      # 薄れて消えた（波紋も 0.4 秒で終わる）


# --- (e) flow_tree -------------------------------------------------------------

def test_flow_tree_counts_amounts_and_sizes():
    _proj(1280, 720)
    g = flow_tree([1, 6, 1200], t=0.5, hop=0.3, amount=400000)
    assert [len(x) for x in g.levels] == [1, 6, 1200]
    leaves = g.levels[-1]
    assert sum(g.amount[n] for n in leaves) == pytest.approx(400000, rel=1e-12)
    assert sum(g.amount[n] for n in g.levels[1]) == pytest.approx(400000, rel=1e-12)
    # 葉の段は点の塊（1200 > 500）。broadcast のパケットは親1つあたり 4 個に束ねる
    pk = g._packets
    to_leaf = [p for p in pk if p[6] < 14]
    assert len(pk) == 6 + 6 * 4 and len(to_leaf) == 24
    # 直径は √(量) に比例: 束ねたパケット（量 = 50 葉ぶん = 1/4 の枝）は枝の半分
    d_branch = max(p[6] for p in pk)
    assert d_branch == pytest.approx(fx_flow._AMOUNT_D)
    assert min(p[6] for p in pk) == pytest.approx(d_branch * math.sqrt(1 / 4))
    # 届く秒（hop ごと）
    obj = g.build()
    assert obj.figure.arrival["L0_0"] == pytest.approx(0.5)
    assert obj.figure.arrival["L2_1199"] == pytest.approx(1.1)


def test_flow_tree_many_children_use_thin_fan():
    """子が 64 を超える親からの辺は細く薄い扇（3px の辺が根の周りで白く潰れない）"""
    _proj()
    assert set(flow_tree([1, 300], t=None)._edge_kind) == {"fan"}
    assert set(flow_tree([1, 50], t=None)._edge_kind) == {"edge"}
    # flow_graph は自分で辺の幅を決めるので、子が多くても扇にしない
    g = flow_graph(["r"] + [f"c{i}" for i in range(100)],
                   [("r", f"c{i}") for i in range(100)], layout="radial")
    assert set(g._edge_kind) == {"edge"}


def test_flow_tree_without_cluster_and_no_broadcast():
    _proj()
    g = flow_tree([1, 3, 9], t=None, layout="layered")
    assert g._packets == [] and len(g.levels[-1]) == 9
    assert not any(g._cluster)
    with pytest.raises(ValueError, match="根は1つ"):
        flow_tree([2, 4], layout="radial")


# --- (f) パケットの数 -----------------------------------------------------------

def test_packet_components_match_n():
    _proj(800, 200)
    g = flow_graph({"a": {"pos": (30, 100)}, "b": {"pos": (770, 100)}}, [("a", "b")])
    g.send(0.0, ["a", "b"], n=7, every=0.1, speed=500, trail=0, color="accent", size=8)
    obj = g.build()
    img = _frame_at(obj, 0.8)          # 7 個とも走っている
    assert _components(_red_mask(img)) == 7


# --- (g) 鍵と画素 ---------------------------------------------------------------

def _scene():
    g = flow_graph(["r", "a", "b", "c"], [("r", "a"), ("r", "b"), ("b", "c", {"curve": 0.3})],
                   layout="layered", node="dot")
    g.send(0.1, ["r", "b", "c"], n=3)
    g.broadcast(0.5, "r")
    g.state(1.0, "a", dim=0.7, mark="x")
    g.cut(1.0, ("r", "a"))
    return g


def test_same_input_same_key_and_pixels():
    _proj()
    o1 = _scene().build()
    o2 = _scene().build()
    assert o1.source == o2.source
    for i in (0, 12, 25, 33, 40):
        assert np.array_equal(fk.draw_frame(o1, i), fk.draw_frame(o2, i))
    # draw は呼ぶ順番に依らない（使い回しの層があっても同じ絵）
    a = fk.draw_frame(o1, 33)
    fk.draw_frame(o1, 3)
    assert np.array_equal(a, fk.draw_frame(o1, 33))


def test_key_changes_with_events_and_ignores_layout_result():
    _proj()
    base = _scene().build().source
    g = _scene()
    g.send(1.5, ["r", "a"])
    assert g.build().source != base
    # 色を変えると鍵が変わる
    g2 = flow_graph(["r", "a", "b", "c"],
                    [("r", "a"), ("r", "b"), ("b", "c", {"curve": 0.3})],
                    layout="layered", colors={"accent": "#ff0000"})
    g2.send(0.1, ["r", "b", "c"], n=3)
    g2.broadcast(0.5, "r")
    g2.state(1.0, "a", dim=0.7, mark="x")
    g2.cut(1.0, ("r", "a"))
    assert g2.build().source != base
    # 配置の結果は鍵に入らない（params に pos が無い）
    params = _scene()._params()
    assert all(entry[3] is None for entry in params["nodes"])


def test_dry_run_does_not_draw_and_is_cold_warm_stable(tmp_path):
    layer = tmp_path / "flow_layer.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "g = flow_graph(['a', 'b'], [('a', 'b')], layout='layered')\n"
        "g.send(0.1, ['a', 'b'])\n"
        "g.build().time(2)\n", encoding="utf-8")

    def run():
        p = sv.Project()
        p.configure(width=320, height=180, fps=30)
        p.layer(str(layer), priority=0)
        return p.render(str(tmp_path / "o.mp4"), dry_run=True)
    r1 = run()
    (path, cmd), = r1["cache"].items()
    assert "frames" in path and not os.path.exists(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x")                       # 生成物があっても dry_run の出力は同じ
    assert run() == r1


# --- (h) 上限とエラー ------------------------------------------------------------

def test_limits():
    _proj()
    with pytest.raises(ValueError, match="上限 5000"):
        flow_graph(list(range(5001)), layout="layered")
    nodes = [f"n{i}" for i in range(200)]
    edges = [(nodes[i], nodes[j]) for i in range(200) for j in range(i + 1, 200)][:10001]
    with pytest.raises(ValueError, match="上限 10000"):
        flow_graph(nodes, edges, layout="layered")
    with pytest.raises(ValueError, match="1〜2000"):
        flow_graph({"a": {"pos": (10, 10)}, "b": {"pos": (300, 10)}},
                   [("a", "b")]).send(0, ["a", "b"], n=2001)
    g = flow_graph({"a": {"pos": (10, 10)}, "b": {"pos": (600, 10)}}, [("a", "b")])
    g.send(0, ["a", "b"], n=1500, every=0, speed=100)
    g.send(0, ["b", "a"], n=1500, every=0, speed=100)
    with pytest.raises(ValueError, match="同時に見えるパケット"):
        g.build()
    with pytest.raises(ValueError, match="上限 5000"):
        flow_tree([1, 100, 5000])


def test_errors():
    _proj()
    two = {"a": {"pos": (10, 10)}, "b": {"pos": (300, 10)}, "c": {"pos": (300, 200)}}
    with pytest.raises(ValueError, match="ありません"):
        flow_graph(two, [("a", "zz")])
    with pytest.raises(ValueError, match="自分自身"):
        flow_graph(two, [("a", "a")])
    with pytest.raises(ValueError, match="2本"):
        flow_graph(two, [("a", "b"), ("b", "a")])
    with pytest.raises(ValueError, match="知らない属性"):
        flow_graph({"a": {"pos": (1, 1), "colour": "red"}})
    with pytest.raises(ValueError, match="知らない属性"):
        flow_graph(two, [("a", "b", {"weight": 2})])
    with pytest.raises(ValueError, match="label_size"):
        flow_graph(two, [("a", "b")], label_size=24)
    with pytest.raises(ValueError, match="layout"):
        flow_graph(two, layout="tree")
    g = flow_graph(two, [("a", "b")])
    with pytest.raises(ValueError, match="結ぶ辺がありません"):
        g.send(0, ["a", "c"])
    with pytest.raises(ValueError, match="2番目以降"):
        g.send(0, ["a", "b"], fate=("stop", "a"))
    with pytest.raises(ValueError, match="fate"):
        g.send(0, ["a", "b"], fate="vanish")
    with pytest.raises(ValueError, match="割合"):
        g.send(0, ["a", "b"], fate=("drop", 1.0))
    with pytest.raises(ValueError, match="収まりません"):
        g.send(0, ["a", "b"], n=40, size=20, fate=("stop", "b"))
    with pytest.raises(ValueError, match="どれか"):
        g.state(0, "a")
    with pytest.raises(ValueError, match="mark"):
        g.state(0, "a", mark="star")
    with pytest.raises(ValueError, match="もしかして|ありません"):
        g.state(0, "bb", dim=1)
    g.cut(0, ("b", "a"))
    with pytest.raises(ValueError, match="既に cut"):
        g.cut(1, ("a", "b"))
    with pytest.raises(ValueError, match="duration"):
        g.build(duration=0)
    with pytest.raises(ValueError, match="levels"):
        flow_tree([5])
    with pytest.raises(TypeError, match="nodes"):
        flow_tree([1, 3], nodes={})


def test_label_overlap_warns():
    _need_font()
    _proj(400, 200)
    with pytest.warns(UserWarning, match="重なって"):
        flow_graph({f"s{k}": {"label": f"サーバー{k}"} for k in range(6)} | {"r": {}},
                   [("r", f"s{k}") for k in range(6)], layout="layered", label_size=30)


def test_manifest_choices_match_implementation():
    m = sv.describe(name="flow_graph")
    entry = m["factories"][0]
    assert entry["params"]["layout"]["choices"] == list(fx_flow._LAYOUTS)
    assert entry["params"]["direction"]["choices"] == list(fx_flow._DIRECTIONS)
    assert entry["params"]["node"]["choices"] == list(fx_flow._SHAPES)
    assert entry["params"]["label_pos"]["choices"] == list(fx_flow._LABEL_POS)


# --- (k) レビューの回帰 -----------------------------------------------------------

def test_fan_edges_follow_curve(monkeypatch):
    """flow_tree に curve を渡すと、扇の辺（細い線）もパケットの道と同じ曲線で描く。
    子が 80 あると curve は自動で弱まる（_curve_caps）ので、ずれが見えるようここでは
    弱めない（_CURVE_SPREAD を大きくする）"""
    monkeypatch.setattr(fx_flow, "_CURVE_SPREAD", 10.0)
    _proj(800, 800)
    g = flow_tree([1, 80], t=0.2, hop=1.0, curve=0.4, size=(800, 800))
    assert set(g._edge_kind) == {"fan"}
    r = _Renderer(g._snapshot())
    edges = r._edge_base_u8()
    cx, cy = g._pos[0]
    far = 0.0
    for t in (0.5, 0.7, 0.9, 1.1):
        st = r.packet_state(t)
        hx, hy = st[6][st[5]], st[7][st[5]]
        assert hx.size == 80
        ix, iy = np.round(hx).astype(int), np.round(hy).astype(int)
        near = np.zeros(ix.size, int)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                near = np.maximum(near, edges[iy + dy, ix + dx, 3])
        assert near.min() > 10, "パケットの頭の下に扇の線が無い（直線の扇と曲線の道がずれている）"
        # 根の中心と辺の端を結ぶ直線から大きく離れる（曲線を描いている）
        for k, (x, y) in enumerate(zip(hx, hy)):
            tx, ty = g._edge_pts[k][-1]
            far = max(far, abs((x - cx) * (ty - cy) - (y - cy) * (tx - cx))
                      / math.hypot(tx - cx, ty - cy))
    assert far > 30


def test_curved_fan_cut_matches_drawn_fan(monkeypatch):
    """cut した扇の辺も、cut しない扇と同じ曲線（混ざっても直線と曲線が並ばない）"""
    monkeypatch.setattr(fx_flow, "_CURVE_SPREAD", 10.0)      # 曲線のまま比べる
    _proj(800, 800)
    g = flow_tree([1, 80], t=None, curve=0.4, size=(800, 800))
    g.cut(0.0, ("L0_0", "L1_5"), dur=0.0)
    r = _Renderer(g._snapshot())
    base = r._edge_base_u8()
    layer = r.edge_layer(r.edge_progress(1.0))
    added = layer[..., 3].astype(int) - base[..., 3].astype(int)     # cut した辺の破線だけ
    ys, xs = np.nonzero(added > 0)
    poly = np.array(g._edge_pts[5])
    a, b = poly[:-1], poly[1:]
    ab = b - a
    worst = 0.0
    for x, y in zip(xs + 0.5, ys + 0.5):
        tt = np.clip(((x - a[:, 0]) * ab[:, 0] + (y - a[:, 1]) * ab[:, 1])
                     / (ab ** 2).sum(axis=1), 0, 1)
        worst = max(worst, np.hypot(a[:, 0] + tt * ab[:, 0] - x,
                                    a[:, 1] + tt * ab[:, 1] - y).min())
    assert xs.size > 50 and worst < 2.0        # 破線の画素はすべて曲線の折れ線の上


def test_global_curve_weakens_at_crowded_nodes():
    """図全体の curve は、辺の集まるノードで弱める（子 40 の根に curve=0.3 で渦に見えた）。
    辺の端の傾き atan(2·curve) は、隣の辺との角の間の半分まで。辺ごとの 'curve' はそのまま"""
    _proj(800, 800)
    g = flow_tree([1, 40], t=None, curve=0.3, size=(800, 800))
    gap = 2 * math.pi / 40
    cap = math.tan(fx_flow._CURVE_SPREAD * gap) / 2.0
    assert all(0 < c <= cap + 1e-9 for c in g._edge_curve)
    # 根から見て、どの辺も自分の角の間の半分より外へ倒れ込まない（渦にならない）
    cx, cy = g._pos[0]
    for (a, b, _d, _c), pts in zip(g._edges, g._edge_pts):
        bx, by = g._pos[b]
        chord = math.atan2(by - cy, bx - cx)
        for x, y in pts:
            d = (math.atan2(y - cy, x - cx) - chord + math.pi) % (2 * math.pi) - math.pi
            assert abs(d) <= gap / 2 + 1e-6
    # 周りに均等に散った辺の少ないノードは弱めない
    g3 = flow_tree([1, 3], t=None, curve=0.3, size=(800, 800))
    assert g3._edge_curve == pytest.approx([0.3] * 3)
    # 上限は本数ではなく隣の辺との角の間で決まる: 片側へ開く layered の扇は少なくても弱まる
    # （README・docstring の「2 本で約 0.28、3 本で約 0.16、4 本で約 0.11」）
    for n, approx in ((2, 0.275), (3, 0.164), (4, 0.107)):
        ch = [f"c{i}" for i in range(n)]
        gl = flow_graph(["r"] + ch, [("r", c) for c in ch], layout="layered", curve=0.3,
                        size=(1200, 600))
        assert gl._edge_curve == pytest.approx([approx] * n, abs=0.003), n
    # 辺ごとの curve は指定どおり（図全体の curve だけを弱める）
    spokes = {f"s{k}": {"pos": (400 + 300 * math.cos(k * math.pi / 6),
                                400 + 300 * math.sin(k * math.pi / 6))} for k in range(12)}
    nodes = {"hub": {"pos": (400, 400)}, **spokes}
    ge = flow_graph(nodes, [("hub", s, {"curve": 0.3}) for s in spokes], size=(800, 800))
    assert ge._edge_curve == pytest.approx([0.3] * 12)
    gg = flow_graph(nodes, [("hub", s) for s in spokes], size=(800, 800), curve=0.3)
    assert max(gg._edge_curve) == pytest.approx(math.tan(fx_flow._CURVE_SPREAD * math.pi / 6) / 2)


def _plabel_rects(g, r, t):
    """時刻 t の札の外接矩形（基準の字の箱 + 縁取り）"""
    top, bot = g._ref
    out = []
    for text, x_left, base_y, _a, _c in r.packet_labels_at(t):
        cw = float(g._sprites[text].meta["content_width"])
        h = fx_flow._HALO
        out.append((text, (x_left - h, base_y + top - h, x_left + cw + h, base_y + bot + h)))
    return out


def _build_times(g, fps=30):
    """build が描くコマの秒（既定の尺 = 最後の出来事の終わり + 1 秒）"""
    n = fk.n_frames_for(g._end_time() + 1.0, fps)
    return [i / fps for i in range(n)]


def _assert_labels_clear(g, r, times):
    """どのコマでも、札（縁取り込み）がノード（枠・点。_hit_index の矩形）・ノードの文字・
    ほかの札に重ならないこと。札ごとの見えたコマの数を返す"""
    _egrid, _bbox, _ngrid, nrect = g._hit_index()
    blockers = [(g.names[j], nrect[j]) for j in range(len(g.names)) if not g._cluster[j]]
    blockers += [(f"{g.names[i]} の文字", g._label_rect(i, g._label_mode[i]))
                 for i in range(len(g.names))
                 if g._label[i] is not None and g._shape[i] != "box"]
    seen = {}
    for t in times:
        rects = _plabel_rects(g, r, t)
        for text, rc in rects:
            seen[text] = seen.get(text, 0) + 1
            for name, b in blockers:
                assert fx_flow._rect_overlap(rc, b) == 0.0, (round(t, 3), text, name)
        for x in range(len(rects)):
            for y in range(x + 1, len(rects)):
                assert fx_flow._rect_overlap(rects[x][1], rects[y][1]) == 0.0, \
                    (round(t, 3), rects[x][0], rects[y][0])
    return seen


def _sides(plan):
    return [sd for _s, sd in plan]


def _drawn_sides(g, r, times, pi, text, plan):
    """札が描かれたコマの (秒, 側)"""
    t0, _rid, v, s_end = g._packets[pi][:4]
    return [(t, fx_flow._plabel_side(plan, min(max((t - t0) * v, 0.0), s_end)))
            for t in times if any(x[0] == text for x in r.packet_labels_at(t))]


def test_packet_label_stays_off_its_diagonal_edge():
    """斜めの辺では、札を進行方向の左右に置いて辺に貫かれないようにする
    （画面の上に置くと、右下がりの辺が札の下側を横切った）"""
    _need_font()
    _proj(800, 450)
    g = flow_graph({"a": {"pos": (80, 80)}, "b": {"pos": (720, 380)}}, [("a", "b")],
                   size=(800, 450))
    g.send(0.1, ["a", "b"], size=9, label="2000万ドル", speed=500)
    r = _Renderer(g._snapshot())
    (_pi, _text, plan, _blocks), = r.sc["packet_labels"]
    assert _sides(plan) in (["left"], ["right"])
    times = _build_times(g)
    for t in times:
        for _text, rc in _plabel_rects(g, r, t):
            assert not fx_flow._poly_hits_rect(g._edge_pts[0], *rc), (t, rc)
    assert _assert_labels_clear(g, r, times)["2000万ドル"] > 10


def test_packet_label_on_a_clear_horizontal_edge_stays_above():
    _need_font()
    _proj(800, 300)
    g = flow_graph({"a": {"pos": (80, 150)}, "b": {"pos": (720, 150)}}, [("a", "b")],
                   size=(800, 300))
    g.send(0.1, ["a", "b"], label="送金")
    r = _Renderer(g._snapshot())
    assert r.sc["packet_labels"][0][2] == ((0.0, "above"),)


def test_packet_label_avoids_a_box_and_keeps_its_side():
    """横の辺の真上に box があると、札は下（進行方向の右）へ。選んだ側は動く間ずっと同じ"""
    _need_font()
    _proj(900, 360)
    g = flow_graph({"a": {"pos": (60, 200)}, "b": {"pos": (840, 200)},
                    "c": {"pos": (450, 140), "label": "障害物", "shape": "box"}},
                   [("a", "b")], size=(900, 360), box=(200, 60))
    g.send(0.1, ["a", "b"], label="8100万ドル", speed=600)
    r = _Renderer(g._snapshot())
    plan = r.sc["packet_labels"][0][2]
    assert _sides(plan) in (["right"], ["below"])
    i = g._index["c"]
    (x, y), (hx, hy) = g._pos[i], g._half[i]
    box = (x - hx, y - hy, x + hx, y + hy)
    st_prev = None
    for t in _build_times(g):
        st = r.packet_state(t)
        rects = _plabel_rects(g, r, t)
        if not rects:
            continue
        rc = rects[0][1]
        assert fx_flow._rect_overlap(rc, box) == 0.0, t
        below = (rc[1] + rc[3]) / 2.0 > float(st[7][0])         # 札の中心が頭より下
        assert st_prev is None or below == st_prev
        st_prev = below
    assert st_prev is True


def test_packet_labels_on_one_edge_do_not_overlap():
    """同じ辺を続けて走る札は、互いに重ならない（以前は「送金3金2金1」と重なった）。
    後の札は先の札の見えるコマを避ける（重なる間は隠れる）"""
    _need_font()
    _proj(1400, 560)
    g = flow_graph({"a": {"pos": (150, 260), "shape": "box", "label": "A"},
                    "b": {"pos": (700, 260)}, "c": {"pos": (700, 470)},
                    "d": {"pos": (1250, 120)}, "e": {"pos": (1250, 420)}},
                   [("a", "b"), ("b", "c"), ("b", "d"), ("b", "e")], size=(1400, 560))
    for k, end in enumerate("dec"):
        g.send(0.2 + 0.1 * k, ["a", "b", end], size=9, label=f"送金{k + 1}", speed=500)
    r = _Renderer(g._snapshot())
    seen = _assert_labels_clear(g, r, _build_times(g))
    assert all(seen.get(f"送金{k}", 0) >= 5 for k in (1, 2, 3)), seen


def _s13_graph():
    """見本 s13と同じ図: box 2つと点 2つ、札つきの送金が NY を抜けて2方向へ"""
    g = flow_graph({"bb": {"pos": (210, 260), "label": "バングラデシュ中銀", "shape": "box"},
                    "ny": {"pos": (1000, 260), "label": "NY連銀", "shape": "box"},
                    "ph": {"pos": (1420, 100), "label": "フィリピン"},
                    "sl": {"pos": (1420, 420), "label": "スリランカ"}},
                   [("bb", "ny", {"curve": 0.12}), ("ny", "ph"), ("ny", "sl")],
                   size=(1600, 520), label_size=34)
    g.send(0.3, ["bb", "ny", "ph"], n=30, every=0.09, size=9, fate=("stop", "ny"))
    g.send(3.4, ["bb", "ny", "ph"], n=4, every=0.15, size=9, label="8100万ドル")
    g.send(4.3, ["bb", "ny", "sl"], size=9, label="2000万ドル")
    return g


def test_packet_labels_never_cover_nodes_or_node_labels_s13():
    """レビューの回帰: 見本 s13 で「8100万ドル」が着く手前で「フィリピン」の文字を 6 コマ
    （最大 3767px²）覆い、「2000万ドル」が出た直後の 4 コマ NY連銀 の枠を覆った（側を1つに
    保ったまま、重なりを辺との交差と同じ秤で比べていた）。どのコマでも札（縁取り込み）が
    ノードの枠・点・ノードの文字・ほかの札に重ならないこと、札が消えっぱなしにならないこと、
    札の α は隠す区間の倍率（1 コマで _PLABEL_FADE 秒ぶんより速く変わらない。出るときも 0 から）
    を超えないこと（ぱっと出たり消えたりしない）、側を変えるなら途中のノード（NY連銀）の先で
    1回だけで、変わる間に札の無いコマを挟むこと"""
    _need_font()
    _proj(1920, 1080)
    g = _s13_graph()
    r = _Renderer(g._snapshot())
    times = _build_times(g)
    seen = _assert_labels_clear(g, r, times)
    assert seen.get("8100万ドル", 0) >= 15 and seen.get("2000万ドル", 0) >= 15, seen
    step = 1.0 / (fx_flow._PLABEL_FADE * 30) + 1e-6
    for _pi, text, _plan, blocks in r.sc["packet_labels"]:
        ab = [fx_flow._plabel_alpha(blocks, t) for t in times]
        al = [next((x[3] for x in r.packet_labels_at(t) if x[0] == text), 0.0) for t in times]
        assert max(abs(b - a) for a, b in zip(ab, ab[1:])) <= step, text
        assert all(a <= b + 1e-9 for a, b in zip(al, ab)), text
        first = next(k for k, a in enumerate(al) if a > 0)
        assert al[first] <= step                         # 出るときも薄く現れる
    for pi, text, plan, _blocks in r.sc["packet_labels"]:
        assert len(plan) <= 2
        if len(plan) == 2:
            assert plan[1][0] == g._routes[g._packets[pi][1]][3][1]       # 次の辺の始まり
            drawn = _drawn_sides(g, r, times, pi, text, plan)
            switch = [k for k in range(1, len(drawn)) if drawn[k][1] != drawn[k - 1][1]]
            assert len(switch) == 1
            k = switch[0]
            assert drawn[k][0] - drawn[k - 1][0] > 1.5 / 30      # 間に札の無いコマがある


def test_packet_label_switches_side_only_while_hidden_in_a_box():
    """1本目の辺は上に、2本目の辺は下に障害物がある。札は途中の box（中では札も消える）の先で
    側を変えて、どちらの辺でも見える。側が変わる前後で、札の無いコマを挟む"""
    _need_font()
    _proj(1000, 400)
    g = flow_graph({"a": {"pos": (60, 200)},
                    "m": {"pos": (500, 200), "shape": "box", "label": "中継"},
                    "b": {"pos": (940, 200)},
                    "x": {"pos": (280, 140), "shape": "box", "label": "上"},
                    "y": {"pos": (720, 260), "shape": "box", "label": "下"}},
                   [("a", "m"), ("m", "b")], size=(1000, 400), box=(140, 50), label_size=30)
    g.send(0.1, ["a", "m", "b"], size=9, label="送金", speed=500)
    r = _Renderer(g._snapshot())
    (pi, text, plan, _blocks), = r.sc["packet_labels"]
    assert len(plan) == 2 and plan[1][0] == g._routes[g._packets[0][1]][3][1]
    assert plan[0][1] in ("below", "right") and plan[1][1] in ("above", "left")
    times = _build_times(g)
    assert _assert_labels_clear(g, r, times)["送金"] >= 10
    drawn = _drawn_sides(g, r, times, pi, text, plan)
    assert {sd for _t, sd in drawn} == {plan[0][1], plan[1][1]}           # 両方の辺で見える
    k = next(k for k in range(1, len(drawn)) if drawn[k][1] != drawn[k - 1][1])
    assert drawn[k][0] - drawn[k - 1][0] > 1.5 / 30


@pytest.mark.parametrize("shape", ["dot", "box"])
def test_stopped_packet_label_moves_behind_and_stays(shape):
    """止まったパケットの札: 中央のままだと止まる先のノードに掛かるので、止まる手前で薄れ、
    止まったら後ろの左右（札の前の端をパケットの前の端にそろえる）で現れて、終わりまで残る"""
    _need_font()
    _proj(900, 400)
    g = flow_graph({"a": {"pos": (80, 200)},
                    "b": {"pos": (700, 200), "shape": shape, "label": "銀行"}},
                   [("a", "b")], size=(900, 400), box=(160, 60))
    g.send(0.1, ["a", "b"], size=9, label="差し止め", fate=("stop", "b"))
    g.send(0.3, ["a", "b"], n=3, every=0.1, size=9, fate=("stop", "b"))
    r = _Renderer(g._snapshot())
    (_pi, _text, plan, _blocks), = r.sc["packet_labels"]
    assert plan[-1][0] == g._packets[0][3] and plan[-1][1] in fx_flow._PLABEL_REST_SIDES
    times = _build_times(g)
    _assert_labels_clear(g, r, times)
    last = _plabel_rects(g, r, times[-1])
    assert len(last) == 1                                # 終わりまで残る
    hx = float(r.packet_state(times[-1])[6][0])
    assert last[0][1][2] <= hx + 4.5 + fx_flow._HALO + 1e-6     # 前の端はパケットの前の端まで
    assert next(x[3] for x in r.packet_labels_at(times[-1])) > 0.99


def test_packet_label_blocks_and_alpha():
    """隠すコマ → 隠す区間: 現れる前・pass の着いた後・頭の見えないコマを隠し、短い見える間は
    つなぐ。α は区間の中で 0、区間から _PLABEL_FADE 秒で 1"""
    fps = 30.0
    idx = list(range(10, 31))
    hard = [20 <= k <= 22 for k in idx]
    blocks = fx_flow._plabel_blocks(idx, hard, fps, True)
    # 現れる前 (9) と、重なる 20〜22 と着いた後 (31)。22 と 31 の間の見える 8 コマ（0.27 秒）は
    # _PLABEL_MIN_SHOW（0.3 秒）より短いのでつなぐ
    assert blocks == [(8.5 / fps, 9.5 / fps), (19.5 / fps, 31.5 / fps)]
    a = [fx_flow._plabel_alpha(blocks, k / fps) for k in idx]
    assert a[0] == pytest.approx(0.5 / fps / fx_flow._PLABEL_FADE)
    assert max(a) == 1.0 and all(v == 0.0 for v in a[10:])
    # 途中の box の中（頭の見えないコマ 3〜29）も隠す。見える 3 コマは短いのでつなぐ
    blocks = fx_flow._plabel_blocks([0, 1, 2, 30, 31, 32], [False] * 6, fps, False)
    assert blocks == [(-1.5 / fps, 29.5 / fps)]
    assert fx_flow._plabel_blocks([], [], fps, True) == []


def test_packet_label_that_never_fits_warns():
    """どのコマでもノードに掛かる札は描かず、警告する"""
    _need_font()
    _proj(400, 200)
    g = flow_graph({"a": {"pos": (150, 100)}, "b": {"pos": (230, 100)}}, [("a", "b")],
                   size=(400, 200))
    g.send(0.1, ["a", "b"], size=9, label="とても長い札の文字", speed=300)
    with pytest.warns(UserWarning, match="出ません"):
        r = _Renderer(g._snapshot())
    assert all(not r.packet_labels_at(t) for t in _build_times(g))


def _label_graph():
    return ({"dns": {"label": "DNS"}, "cdn": {"label": "CDN"}, "lb": {"label": "LB"},
             "app1": {"label": "アプリ1"}, "app2": {"label": "アプリ2"},
             "db": {"label": "データベース"}},
            [("dns", "cdn"), ("dns", "lb"), ("lb", "app1"), ("lb", "app2"),
             ("app1", "db"), ("app2", "db")])


def _edge_alpha_in_label(g, r, i):
    """ノード i の文字の外接矩形（1px 内側）の中の、辺の層の α の最大"""
    edges = r._edge_base_u8()
    x0, y0, x1, y1 = g._label_rect(i, g._label_mode[i])
    xs0, ys0 = int(math.ceil(x0 + 1)), int(math.ceil(y0 + 1))
    xs1, ys1 = int(math.floor(x1 - 1)), int(math.floor(y1 - 1))
    return int(edges[max(0, ys0):ys1, max(0, xs0):xs1, 3].max(initial=0))


def test_auto_labels_avoid_edges_layered():
    """layered の auto: 流れの向きに文字を置くのは葉だけ。途中の点・根の文字を辺が貫かない"""
    _need_font()
    _proj()
    nodes, edges = _label_graph()
    with warnings.catch_warnings():
        warnings.simplefilter("error")        # 重なり・板の警告が出ない
        g = flow_graph(nodes, edges, layout="layered", size=(1400, 900), label_size=40)
    modes = dict(zip(g.names, g._label_mode))
    assert modes["dns"] == "above" and modes["db"] == "below"
    assert modes["lb"] in ("right", "left") and modes["app1"] in ("right", "left")
    assert not any(g._plate)
    r = _Renderer(g._snapshot())
    for i in range(len(g.names)):
        assert _edge_alpha_in_label(g, r, i) == 0, g.names[i]
    # right 向き: 根の文字は流れと逆（左）
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        g2 = flow_graph({"root": {"label": "ルート"}, "a": {"label": "A"}, "b": {"label": "B"}},
                        [("root", "a"), ("root", "b")], layout="layered", direction="right",
                        size=(1000, 600), label_size=40)
    assert g2._label_mode[0] == "left"
    r2 = _Renderer(g2._snapshot())
    assert _edge_alpha_in_label(g2, r2, 0) == 0


def test_auto_label_given_moves_off_edge():
    """given の auto は下が第一候補。下へ伸びる辺があれば、重ならない側へ置き直す"""
    _need_font()
    _proj()
    g = flow_graph({"a": {"pos": (320, 90), "label": "上流"}, "b": {"pos": (320, 300)}},
                   [("a", "b")], label_size=30)
    assert g._label_mode[0] != "below" and not g._plate[0]


def test_label_crossed_by_edges_gets_plate_and_warns():
    """どの側も辺が通るとき（radial の根）は、文字の下に panel 色の板を敷いて警告する"""
    _need_font()
    _proj()
    with pytest.warns(UserWarning, match="板"):
        g = flow_graph({"hub": {"label": "ハブ"}, **{f"c{k}": {} for k in range(8)}},
                       [("hub", f"c{k}") for k in range(8)], layout="radial",
                       size=(900, 900), label_size=40)
    assert g._plate[0]
    obj = g.build(duration=0.5)
    img = fk.draw_frame(obj, 0)
    # 板の中で文字（縁取り込み）の外の画素は panel 色そのもの（辺は板の下に隠れる）
    cx, cy, hx, hy, _rad = g._plate_rect[0]
    r = _Renderer(g._snapshot())
    base, halo, _fill = r.masks["ハブ"]
    lx, ly = g._label_at[0]
    text = np.zeros(img.shape[:2], bool)
    tx0, ty0 = lx - base[0], ly - base[1]
    text[ty0:ty0 + halo.shape[0], tx0:tx0 + halo.shape[1]] = halo > 0
    ys, xs = np.mgrid[int(cy - hy + 3):int(cy + hy - 3), int(cx - hx + 3):int(cx + hx - 3)]
    keep = ~text[ys, xs]
    panel = np.array(fk.palette("t")["panel"])
    assert keep.sum() > 100
    assert np.array_equal(np.unique(img[ys[keep], xs[keep]], axis=0), panel[None, :])


@pytest.mark.parametrize("panel", [(0, 0, 0, 0), (20, 22, 26, 1), None])
def test_transparent_panel_still_draws_packets_and_trails(panel):
    """panel の α が 0 や小さくても、パケット（頭と尾の3点）は描く"""
    _proj(640, 240)
    colors = {"line": (0, 0, 0, 0)}         # 辺を透明に（薄い尾の点が線で2つに割れて数えられない）
    if panel is not None:
        colors["panel"] = panel
    g = flow_graph({"a": {"pos": (40, 120)}, "b": {"pos": (600, 120)}}, [("a", "b")],
                   colors=colors)
    g.send(0, ["a", "b"], n=3, every=0.15, speed=400, color="accent", size=10, trail=0.12)
    img = _frame_at(g.build(), 0.6)
    reddish = (img[..., 0].astype(int) - img[..., 1].astype(int) > 30) & (img[..., 3] > 10)
    assert _components(reddish) == 3 * 4          # 頭 3 個と尾の点 3×3 個
    assert _red_mask(img).sum() > 150


def _box_route(send=True):
    g = flow_graph({"a": {"pos": (60, 100)}, "b": {"pos": (320, 100), "shape": "box"},
                    "c": {"pos": (580, 100)}}, [("a", "b"), ("b", "c")], box=(160, 60))
    if send:
        g.send(0, ["a", "b", "c"], n=3, every=0.1, speed=300, size=9)
    return g


def test_packets_are_hidden_inside_a_box_on_the_path():
    """途中の box の中（文字の上）ではパケットを描かず、box を出たら描く"""
    _proj(640, 200)
    g = _box_route()
    obj = g.build()
    ref = _box_route(send=False).build(duration=obj.figure.duration)
    hx, hy = g._half[1]
    x0, x1 = int(320 - hx - 2), int(320 + hx + 2)
    y0, y1 = int(100 - hy - 2), int(100 + hy + 2)
    after = False
    for i in range(int(obj.figure.duration * 30)):
        a, b = fk.draw_frame(obj, i), fk.draw_frame(ref, i)
        assert np.array_equal(a[y0:y1, x0:x1], b[y0:y1, x0:x1]), f"{i} コマ目で box の中に描いた"
        after = after or not np.array_equal(a[:, x1 + 10:540], b[:, x1 + 10:540])
    assert after, "box を出た後のパケットが無い"


def test_halo_is_not_drawn_over_a_dot_node_on_the_path():
    """途中の点を通る間、パケットの縁（暗い輪）を白いノードの上に描かない"""
    _proj(1280, 720)
    g = flow_graph({"bb": {"pos": (160, 360)}, "swift": {"pos": (460, 360)},
                    "ny": {"pos": (820, 360)}}, [("bb", "swift"), ("swift", "ny")])
    g.send(0.3, ["bb", "swift", "ny"], n=30, every=0.08, speed=600, size=9)
    obj = g.build()
    yy, xx = np.mgrid[-8:9, -8:9]
    inner = (yy ** 2 + xx ** 2) <= 49
    for i in range(10, 70, 2):
        patch = fk.draw_frame(obj, i)[352:369, 452:469]
        assert patch[..., :3].min(axis=2)[inner].min() >= 250, f"{i} コマ目"


def test_stop_queue_mixed_sizes_do_not_overlap():
    """大きさの違うパケットが同じ所に止まっても重ならない（間隔は2つの直径の平均 × 1.6）"""
    _proj(800, 200)
    g = flow_graph({"a": {"pos": (40, 100)}, "b": {"pos": (760, 100)}}, [("a", "b")])
    g.send(0, ["a", "b"], n=2, size=4, trail=0, fate=("stop", "b"))
    g.send(0.5, ["a", "b"], n=1, size=20, trail=0, fate=("stop", "b"))
    g.send(1.0, ["a", "b"], n=1, size=6, trail=0, fate=("stop", "b"))
    r = _Renderer(g._snapshot())
    st = r.packet_state(5.0)
    order = np.argsort(-st[6])
    xs, ds = st[6][order], np.array([p[6] for p in g._packets])[order]
    for k in range(len(xs) - 1):
        assert xs[k] - xs[k + 1] == pytest.approx(1.6 * (ds[k] + ds[k + 1]) / 2)
        assert xs[k] - xs[k + 1] > (ds[k] + ds[k + 1]) / 2
    assert _components(_red_mask(_frame_at(g.build(), 3.0), tol=90)) == 4


def test_stop_queue_steps_over_the_previous_node():
    """列が手前のノードに届いたら、そのノードを跨いで手前の辺へ並べる（ノードに乗せない）"""
    _proj(1280, 720)
    g = flow_graph({"bb": {"pos": (160, 360)}, "swift": {"pos": (460, 360)},
                    "ny": {"pos": (820, 360)}, "ph": {"pos": (1120, 360)}},
                   [("bb", "swift"), ("swift", "ny"), ("ny", "ph")])
    out = g.send(0.3, ["bb", "swift", "ny", "ph"], n=30, every=0.08, speed=600, size=9,
                 trail=0, fate=("stop", "ny"))
    r = _Renderer(g._snapshot())
    st = r.packet_state(max(out) + 0.5)
    d = np.hypot(st[6] - 460, st[7] - 360)
    assert d.min() > 10 + fx_flow._GAP + 4.5        # swift（半径 10）の縁より外
    assert (st[6] < 460).any() and (st[6] > 460).any()   # swift の両側に並ぶ
    img = _frame_at(g.build(), max(out) + 0.5)
    assert _components(_red_mask(img, tol=90)) == 30
    assert img[360, 460, :3].min() >= 250          # swift は白いまま


def test_key_ignores_values_that_do_not_change_the_picture():
    """n=1 の every・使わない色（muted）・7 と 7.0 で鍵を割らない"""
    _proj()

    def key(colors=None, **send):
        g = flow_graph({"a": {"pos": (40, 120)}, "b": {"pos": (600, 120)}}, [("a", "b")],
                       colors=colors)
        g.send(0, ["a", "b"], **send)
        g.state(0.5, "a", dim=1, dur=1)
        return g.build().source
    base = key(n=1, every=0.12, size=7)
    assert key(n=1, every=0.5, size=7) == base
    assert key(n=1, every=0.12, size=7.0) == base
    assert key(colors={"muted": "#123456"}, n=1, every=0.12, size=7) == base
    assert key(n=2, every=0.12, size=7) != key(n=2, every=0.5, size=7)
    assert key(colors={"line": "#123456"}, n=1, every=0.12, size=7) != base


def test_renderer_is_released_after_the_last_frame(monkeypatch):
    """最後のコマを描いたら作業領域を手放す（Object が残り続けても積み上がらない）"""
    made = []

    class Spy(fx_flow._Renderer):
        def __init__(self, sc):
            super().__init__(sc)
            made.append(weakref.ref(self))
    monkeypatch.setattr(fx_flow, "_Renderer", Spy)
    _proj()
    g = flow_graph({"a": {"pos": (40, 120)}, "b": {"pos": (600, 120)}}, [("a", "b")])
    g.send(0, ["a", "b"])
    obj = g.build()
    n = fk.n_frames_for(obj.figure.duration, 30)
    for i in range(n):
        fk.draw_frame(obj, i)
    gc.collect()
    assert len(made) == 1 and made[0]() is None
    first = fk.draw_frame(obj, 5)
    assert len(made) == 2                           # また呼ばれたら作り直す（同じ絵）
    assert np.array_equal(first, fk.draw_frame(obj, 5))


def test_big_packet_does_not_widen_the_windows_of_small_ones():
    """size=200 のパケットが1つあっても、小さな点の窓は大きくならない（メモリが膨らまない）"""
    _proj(960, 540)
    g = flow_graph({"a": {"pos": (40, 270)}, "b": {"pos": (920, 270)}}, [("a", "b")])
    g.send(0, ["a", "b"], n=500, every=0.002, size=7, speed=900)
    g.send(0, ["a", "b"], size=200, speed=900)
    obj = g.build()
    fk.draw_frame(obj, 20)
    tracemalloc.start()
    try:
        img = fk.draw_frame(obj, 21)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 150e6          # 窓を全部 406×406 にすると 1GB を超える
    assert _components(img[..., 3] > 200) >= 1


def test_broadcast_limit_message_suggests_broadcast_fixes():
    _proj()
    names = ["r"] + [f"c{i}" for i in range(30)] + [f"g{i}" for i in range(2100)]
    edges = [("r", f"c{i}") for i in range(30)] + [(f"c{i // 70}", f"g{i}") for i in range(2100)]
    g = flow_graph(names, edges, layout="layered", size=(1600, 900))
    g.broadcast(0.2, "r", ripple=False)
    with pytest.raises(ValueError, match="packets=False"):
        g.build()


def test_packet_label_moves_at_subpixel_positions():
    """遅いパケットの文字も、点と一緒に端数の位置で動く（0px / 1px の段にならない）。
    札は出発のノード a に掛かる間は隠れているので、離れて現れきった 2 秒目から測る"""
    _need_font()
    _proj(640, 240)
    g = flow_graph({"a": {"pos": (40, 160)}, "b": {"pos": (600, 160)}}, [("a", "b")],
                   label_size=30)
    g.send(0, ["a", "b"], speed=20, label="送金", trail=0)
    obj = g.build(duration=3)
    cs = []
    for i in range(60, 80):
        img = fk.draw_frame(obj, i).astype(float)[:140]          # パケットより上（文字だけ）
        w = img[..., 3] / 255.0 * (img[..., 0] / 255.0)
        ys, xs = np.nonzero(w > 0)
        cs.append((w[ys, xs] * xs).sum() / w[ys, xs].sum())
    steps = np.diff(cs)
    assert steps == pytest.approx([20 / 30] * len(steps), abs=0.02)


# --- (i) 金型 -------------------------------------------------------------------

def _golden_health():
    _proj(480, 270)
    servers = ["s1", "s2", "s3", "s4"]
    nodes = {"lb": {"shape": "box"}}
    nodes.update({s: {} for s in servers})
    g = flow_graph(nodes, [("lb", s) for s in servers], layout="layered", box=(120, 40),
                   padding=24)
    for k, s in enumerate(servers):
        g.send(0.1 * k, ["lb", s], speed=400, size=9)
        g.send(0.6 + 0.1 * k, [s, "lb"], speed=400, size=9,
               fate=("drop", 0.5) if s == "s3" else "pass")
    g.state(1.2, "s3", dim=0.6, mark="x", dur=0.2)
    g.cut(1.2, ("lb", "s3"), dur=0.2)
    return g.build(), 1.3


def _golden_tree():
    _proj(480, 270)
    g = flow_tree([1, 8, 600], t=0.1, hop=0.3, amount=1000, padding=16, seed=3)
    return g.build(), 0.62


def _golden_curve():
    _proj(480, 270)
    g = flow_graph({"a": {"pos": (40, 200)}, "b": {"pos": (240, 120)}, "c": {"pos": (440, 200)}},
                   [("a", "b", {"curve": 0.35}), ("b", "c", {"curve": -0.35})])
    g.send(0.0, ["a", "b", "c"], n=8, every=0.06, speed=500, size=8, fate=("stop", "c"))
    g.send(0.2, ["a", "b", "c"], n=2, every=0.3, speed=300, size=10, color="fg")
    g.send(1.0, ["c", "b"], speed=300, size=10, fate=("drop", 0.4))
    return g.build(), 1.5


def _golden_tree_curve():
    _proj(480, 270)
    g = flow_tree([1, 12, 40], t=0.1, hop=0.3, curve=0.3, padding=16)
    return g.build(), 0.55


@pytest.mark.parametrize("name,make", [("health", _golden_health), ("tree", _golden_tree),
                                       ("curve", _golden_curve),
                                       ("tree_curve", _golden_tree_curve)])
def test_golden(request, name, make):
    obj, t = make()
    assert_golden(request, "flow", name, _frame_at(obj, t), ver=_FLOW_VER)


def test_golden_packet_labels(request):
    """パケットの札の置き場所（斜めの辺は進行方向の左右、2つの札は重ならない側）"""
    _need_font()
    _proj(480, 270)
    g = flow_graph({"a": {"pos": (60, 135), "shape": "box"}, "b": {"pos": (240, 135)},
                    "c": {"pos": (430, 40)}, "d": {"pos": (430, 230)}},
                   [("a", "b"), ("b", "c"), ("b", "d")], box=(80, 40), label_size=30)
    g.send(0.0, ["a", "b", "c"], size=8, speed=300, label="送金A")
    g.send(0.15, ["a", "b", "d"], size=8, speed=300, label="送金B")
    obj = g.build()
    assert_golden(request, "flow", "packet_labels", _frame_at(obj, 0.9), ver=_FLOW_VER,
                  font_ffp=g._sprites["送金A"].fonts[0].ffp)


# --- (j) 実レンダ ----------------------------------------------------------------

def _need_ffmpeg():
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg / ffprobe が無い環境")


def _decode(path, t, w, h):
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.4f}", "-i", path, "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.uint8).reshape(h, w, 3)


def test_real_render_colors_and_hold(tmp_path):
    """ffmpeg を通した出力でも、届いたノードが赤くなり、尺より長い表示で最後のコマが残る"""
    _need_ffmpeg()
    layer = tmp_path / "flow_real.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "g = flow_graph({'a': {'pos': (60, 90)}, 'b': {'pos': (260, 90)}}, [('a', 'b')],"
        " node_radius=14)\n"
        "g.broadcast(0.3, 'a', hop=0.4)\n"
        "obj = g.build()\n"
        "obj.time(obj.figure.duration + 1.0)\n", encoding="utf-8")
    p = sv.Project()
    p.configure(width=320, height=180, fps=30, background_color="black")
    p.layer(str(layer), priority=0)
    out = str(tmp_path / "flow.mp4")
    p.render(out)
    first = _decode(out, 0.05, 320, 180)
    assert first[90, 260].min() > 200                      # 最初は白
    late = _decode(out, 1.9, 320, 180)
    r, g_, b = (int(v) for v in late[90, 260])
    assert r > 170 and g_ < 90 and b < 90                    # 届いたあとは赤（accent）
    held = _decode(out, 2.6, 320, 180)                      # 尺（≈1.95 秒）より後
    assert int(held[90, 260][0]) > 170
