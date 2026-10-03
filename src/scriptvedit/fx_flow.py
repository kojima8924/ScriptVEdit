# -*- coding: utf-8 -*-
"""点と線の図の上を、パケットや送金が流れて広がる: flow_graph() / flow_tree()

ネットワーク・配信・送金・感染の図（ノードと辺）を描き、その上で
「パケットを流す（send）・一斉に配る（broadcast）・ノードの色を変える（state）・
辺を切る（cut）」を秒で書いて、build() で1本の動画 Object（入力1本）にする。

    g = flow_graph({"lb": {"label": "ロードバランサー", "shape": "box"},
                    "s1": {"label": "サーバー1"}, "s2": {"label": "サーバー2"}},
                   [("lb", "s1"), ("lb", "s2")], layout="layered")
    g.send(0.5, ["lb", "s1"])                        # 白いパケットを1つ流す
    g.send(0.9, ["s2", "lb"], fate=("drop", 0.5))    # 戻りが途中で消える
    g.state(2.0, "s2", dim=0.6, mark="x")            # 灰色にして × を付ける
    g.cut(2.0, ("lb", "s2"))                         # 辺を破線にして薄れさせる
    obj = g.build()                                  # 最後の出来事の終わり + 1 秒
    obj.figure.pos["s1"]                             # ノードの位置（キャンバスの px）

**模式図であって、実際の経路ではない。** 「模式図」の注記は呼び出し側が付ける。
時刻 t はすべて build() で作る動画の先頭を 0 とした秒。

絵の作り
--------
- 辺: line 色の 3px（曲げるときは2次ベジェ。制御点を中点から 長さ×curve だけ
  進行方向の左へ出す。framekit.arrow と同じ向き）。辺の両端はノードの縁から 6px 手前で切る。
- ノード: fg の塗りの点（dot）か、fg の 3px の角丸の枠と中の文字（box）。
- パケット: 被覆率で塗る円（端数位置のアンチエイリアス）と、trail 秒ぶんの尾
  （α が下がる小さな点3つ）。辺を細かい折れ線にしたものを描く線と走る道の両方に使い、
  道の上を弧長で等速に進むので、曲がった辺でもパケットは辺から外れない（扇の辺も同じ
  折れ線で描く）。パケットの下には panel 色の縁を敷くが、途中の点（dot）を通り抜ける間は
  縁を消す（白い点の上に暗い輪を描かない）。途中の box は入る辺の端から出る辺の端まで
  直線でつなぐが、そこは box の中（文字の上）なので描かない（手前 size px で薄れて消え、
  出る辺の端から現れる。かかる秒は直線の長さ ÷ speed）。
- 届く・止まる・外す（drop）瞬間に波紋（半径 1.0 → 2.2 倍、0.4 秒）。
- パケットの札（send の label）: ノードの枠・点・ノードの文字・先に出た札には重ねない。
  重なるコマでは隠し、0.15 秒手前から薄れて、離れてから 0.15 秒で現れる（出るときも薄く
  現れ、pass は着く手前で薄れる）。置き場所は画面の上・進行方向の左右・画面の下から、
  隠れる間・辺が下を通る間・キャンバスの外へのはみ出しの最も少ないものを札ごとに選び、
  動く間は保つ。変えてよいのは、途中のノードの先（次の辺へ移る所）で1回と、止まった
  パケットが止まる所で1回（パケットより後ろの左右へ。止まる先のノードに掛からない）だけで、
  変わる所では札を必ず一度隠す（_place_packet_labels。上だけに置くと斜めの辺や box に重なり、
  側を1つに保ったまま重なりの量で比べると、着く先の文字や出たばかりの box を覆った）。
- 図全体の curve は、辺の集まるノードで弱める（_curve_caps。同じ向きに曲がった辺が渦に
  見えた）。辺ごとの 'curve' は指定どおり。
- 止まったパケットの列は、前のパケットとの中心の間隔を 1.6 ×（2つの直径の平均）にする
  （大きさが違っても重ならない）。列が手前のノードに届いたら、そのノードを跨いで
  さらに手前の辺へ並べる（ノードの上に乗せない）。
- 点の文字（label_pos='auto'）: layered は葉だけを流れの向きに置き、根は逆の側、途中の点は
  横（down / up は右、right / left は上）に置く。radial / rings は外向き、given は下が第一候補。
  配置の後に、文字の外接矩形が辺・ほかのノードと重ならず、キャンバスに収まる側を候補の順に
  選び直す。どの側も重なるときは重なりの最も少ない側にして、辺が下を通る文字には panel 色の
  板を敷き、警告する（label_pos を指定した文字は動かさず、板と警告だけ）。
- 葉が 500 を超える段（flow_tree）は点の塊（直径 5px・seed で決まる散らばり）で描き、
  そこへの辺は細く薄い扇にする。broadcast のパケットは親1つにつき 4 個まで（量は束ねる）。

描画の分担（重さのため）
----------------------
- 辺の層: cut の無い辺を framekit の polyline（float・事前乗算）で1回だけ描いて
  to_rgba8 で uint8 にする。cut する辺は実線と破線の被覆率を1回だけ求めておき、
  進み具合（1/256 に丸める）ごとに色と α を掛けて uint8 の写しへ重ねる。
- ノードの層: 辺の層の uint8 の写しにノード・文字・印を重ね、ノードの状態
  （色・dim・印の進み具合）ごとに使い回す（broadcast で色が変わる間だけ描き直す）。
  出来事の鎖が同じノード（flow_tree の同じ段）は1つの組にし、点の被覆率を組ごとに
  1回だけ作る（描き直しは組ごとの色で重ねるだけ）。
- 動くもの（パケット・波紋）: 毎コマ、ノードの層の写しに「触れた画素だけ」重ねる。
  同じ色の点・輪はまとめて 1 − Π(1 − a) で合成する（同色の over を何枚重ねても同じ結果で、
  順序に依らない）。float の全面キャンバス＋to_rgba8 は 1080p で毎コマ約 30ms かかるので、
  動く部分には使わない（framekit.layer_cache は float の層を持つので同じ理由で使わない）。
  点の距離の窓は半径の段（2 の冪）ごとに分けて計算する（大きなパケットが1つあっても、
  小さな点の窓は大きくならない）。

重さ（実測。Windows・Python 3.10・numpy 2.0・1920x1080・1コマの描画。ffmpeg の符号化は別。
ほかの重い処理と並行した測定なので幅がある）
-------------------------------------------------------------------------------------
- 葉 3000＋パケット 200（flow_tree([1, 50, 3000], amount=…) の broadcast）: 平均 約 7ms、
  p90 約 19ms、最大 約 25ms（パケットが点の塊へ飛ぶ間）。最初の1コマだけ辺の層（扇
  3000 本）を描くので約 0.4 秒（curve=0.3 で約 0.9 秒）。
- LB＋サーバー6台（文字つき）: 平均 約 3ms・最大 約 12ms（cut の間）。35 個の送金: 約 3ms。
- 同時 2,000 個（上限）: 平均 約 70ms・最大 約 95ms（点 8,000 個を毎コマ数える）。
- 1 秒ぶんの書き出し（frames の qtrle と本レンダの h264 の符号化込み）: LB の図 約 0.3 秒、
  35 個の送金 約 0.35 秒、葉 3000 の木 約 0.9 秒。ほかの重い処理と並行すると 2〜3 倍に延びる。

numpy・opencv-python・Pillow は optional 依存（framekit.need で遅延 import。
pip install "scriptvedit[figures]" で入る）。**構築（flow_graph() の呼び出し）にも3つとも要る**
（色の検証の framekit.palette が need を呼ぶ。文字の寸法は Pillow で測る）。dry_run は
コマを描かないが、構築は通るので同じく要る。networkx は使わない（nodes() と edges() を持つ
グラフは duck typing で読む）。配置と経路（BFS・Dijkstra）は自前で書いてあるので、
ライブラリの版で結果が変わらない。生成が終わる（最後のコマを描く）と、描画の作業領域
（1080p で約 100MB）を手放す。
"""

import bisect
import heapq
import math
import random
import warnings
from collections import OrderedDict, deque
from fractions import Fraction

import scriptvedit.framekit as fk
from scriptvedit.context import current_project
from scriptvedit.filters.video import _fps_fraction
from scriptvedit.state import _suggest_hint
from scriptvedit.stillseq import _resolve_fps, _resolve_size
from scriptvedit.validate import _require_choice, _require_number


# --- 定数 ---

# 描画の版（配置・描き方・時刻の決め方を変えたら上げる。配置の結果は鍵に入らないので、
# 配置の手順を変えたときも必ず上げる）
#   2: 扇の辺を曲線の折れ線で描く、auto の文字を辺・ノードから避ける（板の代替）、途中の box の
#      中でパケットを描かない・途中の点の上で縁を消す、止まる列の間隔を2つの直径の平均で決めて
#      手前のノードを跨ぐ、パケットの文字を端数の位置で描く、点の窓を半径の段ごとに分ける
#   3: パケットの札を画面の上・進行方向の左右・下から重なりの少ない側へ置く（札ごとに
#      1つ選んで保つ）、図全体の curve を辺の集まるノードで弱める（渦に見えない）
#   4: パケットの札をノード・ノードの文字・先に出た札に重ならせない（重なるコマは隠し、
#      手前で薄れて離れてから現れる。出るときも 0.15 秒で現れ、pass は着く手前で薄れる）。
#      側の採点を「隠れる間」で数える（文字の上に文字を載せる側を選ばない）
_FLOW_VER = "4"

# 上限（超えたら ValueError）
_MAX_NODES = 5000
_MAX_EDGES = 10000
_MAX_PACKETS = 2000          # 同時に見えるパケット（止まって残るものを含む）

_LAYOUTS = ("given", "layered", "radial", "rings")
_DIRECTIONS = ("down", "up", "right", "left")
_SHAPES = ("dot", "box")
_LABEL_POS = ("auto", "below", "above", "right", "left", "center")
_MARKS = ("x", "check", "none")
_NODE_KEYS = ("pos", "label", "shape", "layer")
_EDGE_KEYS = ("delay", "curve")

_GAP = 6.0                   # 辺の端とノードの縁の隙間 px
_LABEL_GAP = 10.0            # 点と文字の隙間 px
_BOX_RADIUS = 12.0           # box の角丸 px
_BOX_STROKE = 3.0            # box の枠の幅 px
_BOX_PAD = (24.0, 14.0)      # box の文字の左右・上下の余白 px（文字が収まらなければ広げる）
_HALO = 4                    # 文字の縁取り（panel 色）px
_RIPPLE_DUR = 0.4            # 波紋の秒
_RIPPLE_GROW = 1.2           # 半径 1.0 → 2.2 倍
_RIPPLE_WIDTH = 3.0          # 波紋の線の幅 px
_RIPPLE_ALPHA = 0.85
_ARRIVE_DUR = 0.25           # broadcast で色が変わる秒
_STOP_TINT = 0.2             # 止まったパケットが accent になる秒
_DROP_FADE = 0.4             # drop したパケットが消える秒
_STOP_PITCH = 1.6            # 止まったパケットの間隔（size の倍数）
_TRAIL = ((0.78, 0.55), (0.6, 0.34), (0.42, 0.17))   # 尾の点（直径の倍率, α）
_CUT_ALPHA = 0.75            # 切った辺（破線）の α
_DASH = (4.0, 3.0)           # 破線の線・隙間（辺の幅の倍数）
_CLUSTER_MIN = 500           # この数を超える葉の段は点の塊
_CLUSTER_PACKETS = 4         # 点の塊へ向かう broadcast のパケット（親1つあたり）
_CLUSTER_D = 5.0             # 点の塊の点の直径 px
_CLUSTER_BAND = 0.78         # radial の点の塊: 半径の 78%〜100% に散らす
_FAN_WIDTH = 1.2             # 点の塊への辺の幅 px
_FAN_ALPHA = 0.16            # 点の塊への辺の α
_FAN_CHILDREN = 64           # flow_tree で子がこの数を超える親からの辺も扇にする
_FAN_PIECE = 16.0            # 扇の線分を分ける小片の長さ px（窓の大きさを一定にする）
_AMOUNT_D = 14.0             # amount を渡したとき、最初の枝のパケットの直径 px
_AMOUNT_D_MIN = 3.0
_BROADCAST_D = 7.0           # broadcast のパケットの直径 px（amount なし）
_PACKET_HALO = 2.0           # パケットの下に敷く panel 色の縁 px
_EPS_A = 0.002               # これ未満の被覆率は捨てる（8bit で 0 になる）
_STATIC_MEMO = 3             # 使い回す層の数（辺の層・ノードの層それぞれ）
_PLATE_PAD = 2.0             # 辺が下を通る文字の板: 縁取りの外側へ広げる px
_PLATE_RADIUS = 6.0          # 板の角丸 px
_STOP_MARGIN = (0.8, 2.0)    # 止まる列の先頭: 辺の端から size×0.8 + 2px 手前に中心を置く
_HIT_CELL = 128.0            # 文字と辺・ノードの重なりを調べる格子の大きさ px
_CURVE_SPREAD = 0.5          # 図全体の curve: 辺の端の傾きを、隣の辺との角の間のこの倍までに
_PLABEL_GAP = 8.0            # パケットの縁と札（基準の字の箱）の隙間 px
_PLABEL_FADE = 0.15          # 札が重なる手前で薄れる秒・離れてから現れる秒
_PLABEL_MIN_SHOW = 0.3       # 隠す間に挟まれた見える間がこれより短ければ出さない（薄れる・現れるの和。
                             # 一度は α 1 まで出る間だけ出す。一瞬だけ薄く光らせない）
_PLABEL_HIDE_COST = 2.0      # 側の採点: 札が隠れる間の重み（札の面積・秒あたり。辺が下を通るのは 0.5）
_PLABEL_SWITCH_COST = 0.1    # 側の採点: 途中のノードの先で側を変えるときの上乗せ（隠れる秒に換算）
# パケットの札の置き場所の候補（重なりが同じなら前のもの。above が以前の置き方）
_PLABEL_SIDES = ("above", "left", "right", "below")
# 止まったパケットの札が、止まってから移れる置き場所（進行方向の左右で、パケットより後ろ。
# 中央に置くと止まる先のノードに掛かって隠れ続けた）
_PLABEL_REST_SIDES = ("left-back", "right-back")
# 色の表のうち fx_flow が使う名前（鍵には使う名前の色だけを入れる）
_USED_COLORS = ("fg", "accent", "line", "dim", "panel")
# layered の auto の文字: 向き → (葉, 根, 途中の点) の第一候補
_LAYERED_LABEL = {"down": ("below", "above", "right"), "up": ("above", "below", "right"),
                  "right": ("right", "left", "above"), "left": ("left", "right", "above")}
# 第一候補の次に試す順（layered は向きごと、ほかは共通）
_LABEL_ORDER = {"down": ("below", "right", "left", "above"),
                "up": ("above", "right", "left", "below"),
                "right": ("right", "above", "below", "left"),
                "left": ("left", "above", "below", "right"),
                None: ("below", "above", "right", "left")}


def _n(v):
    """鍵と表示用: 整数値の float を int にそろえる（64 と 64.0 で鍵を割らない）"""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _smooth(p):
    """0..1 → 0..1 の滑らかな補間（smoothstep）"""
    return p * p * (3.0 - 2.0 * p)


def _is_name(v):
    return (isinstance(v, str) and v != "") or (isinstance(v, int) and not isinstance(v, bool))


def _check_time(fn, name, t):
    _require_number(fn, name, t, 0, None)
    return float(t)


# --- 入力の正規化（ノード・辺）---

def _is_graph_like(g):
    return (not isinstance(g, (dict, list, tuple, str))
            and callable(getattr(g, "nodes", None)) and callable(getattr(g, "edges", None)))


def _plain(v):
    """グラフの属性の numpy の値（配列・スカラー）を Python の値にする（networkx の pos 等）"""
    mod = type(v).__module__
    if mod == "numpy" or mod.startswith("numpy."):
        return v.tolist() if getattr(v, "ndim", 0) else v.item()
    return v


def _read_graph(fn, nodes, edges):
    """nodes / edges を (名前のリスト, 属性のリスト, [(a, b, 属性)]) にする。

    nodes() と edges() を持つグラフ（networkx など）は duck typing で読む
    （知っている属性 pos / label / shape / layer・delay / curve だけを拾い、ほかは無視する）。
    dict / リストで渡したときは知らない属性を ValueError にする。
    """
    if _is_graph_like(nodes):
        if edges:
            raise ValueError(
                f"{fn}: nodes にグラフを渡したときは edges を渡さないでください"
                f"（辺はグラフの edges() から読みます）")
        names = list(nodes.nodes())
        view = getattr(nodes, "nodes")
        attrs = []
        for name in names:
            try:
                d = view[name]
            except Exception:
                d = None
            a = {}
            if hasattr(d, "get") and hasattr(d, "__contains__"):
                for k in _NODE_KEYS:
                    if k in d and d[k] is not None:
                        a[k] = _plain(d[k])
            attrs.append(a)
        try:
            raw = list(nodes.edges(data=True))
        except TypeError:
            raw = list(nodes.edges())
        items = []
        for e in raw:
            if len(e) >= 3 and hasattr(e[2], "get"):
                items.append((e[0], e[1], {k: _plain(e[2][k]) for k in _EDGE_KEYS
                                           if k in e[2] and e[2][k] is not None}))
            else:
                items.append((e[0], e[1], {}))
        return names, attrs, items

    if isinstance(nodes, dict):
        names = list(nodes)
        attrs = []
        for name in names:
            a = nodes[name]
            if a is None:
                a = {}
            if not isinstance(a, dict):
                raise TypeError(
                    f"{fn}: nodes['{name}'] は属性の dict（pos / label / shape / layer）か "
                    f"None で指定してください: {a!r}")
            unknown = sorted(set(a) - set(_NODE_KEYS))
            if unknown:
                raise ValueError(
                    f"{fn}: nodes['{name}'] に知らない属性 {unknown}"
                    f"（使える属性: {', '.join(_NODE_KEYS)}）{_suggest_hint(unknown[0], _NODE_KEYS)}")
            attrs.append(dict(a))
    elif isinstance(nodes, (list, tuple)):
        names = list(nodes)
        attrs = [{} for _ in names]
    else:
        raise TypeError(
            f"{fn}: nodes は {{名前: 属性}} の dict、名前のリスト、または nodes() と edges() を"
            f"持つグラフで指定してください: {type(nodes).__name__}")
    if edges is None:
        edges = ()
    if not isinstance(edges, (list, tuple)):
        raise TypeError(f"{fn}: edges は [(a, b), (a, b, {{属性}}), ...] で指定してください: {edges!r}")
    items = []
    for i, e in enumerate(edges):
        if not isinstance(e, (list, tuple)) or len(e) not in (2, 3):
            raise ValueError(
                f"{fn}: edges[{i}] は (a, b) か (a, b, {{'delay': 秒, 'curve': 0.2}}) で"
                f"指定してください: {e!r}")
        a = e[2] if len(e) == 3 else {}
        if a is None:
            a = {}
        if not isinstance(a, dict):
            raise TypeError(f"{fn}: edges[{i}] の3つ目は属性の dict にしてください: {a!r}")
        unknown = sorted(set(a) - set(_EDGE_KEYS))
        if unknown:
            raise ValueError(
                f"{fn}: edges[{i}] に知らない属性 {unknown}（使える属性: "
                f"{', '.join(_EDGE_KEYS)}）{_suggest_hint(unknown[0], _EDGE_KEYS)}")
        items.append((e[0], e[1], dict(a)))
    return names, attrs, items


# --- 幾何（純 Python。配置・辺の折れ線・弧長）---

def _bezier_pts(a, b, curve, step=4.0):
    """a → b の辺の折れ線。curve=0 は直線（2点）、それ以外は2次ベジェを約 step px ごとに刻む
    （扇の細い辺は 12px。曲率半径が辺の長さ程度なら、12px の弦と弧のずれは 0.1px 前後）"""
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    if not curve or L < 1e-9:
        return [a, b]
    nx, ny = (b[1] - a[1]) / L, -(b[0] - a[0]) / L        # 進行方向の左（画面）
    c = ((a[0] + b[0]) / 2 + nx * curve * L, (a[1] + b[1]) / 2 + ny * curve * L)
    n = max(8, int(math.ceil(L * (1 + 2 * abs(curve)) / step)))
    pts = []
    for k in range(n + 1):
        t = k / n
        p, q, r = (1 - t) ** 2, 2 * (1 - t) * t, t * t
        pts.append((p * a[0] + q * c[0] + r * b[0], p * a[1] + q * c[1] + r * b[1]))
    return pts


def _cut_front(pts, inside):
    """折れ線の始点側で、図形の内側にある部分を切り落とす（境目は二分法）"""
    if not inside(pts[0]):
        return pts
    for i in range(1, len(pts)):
        if not inside(pts[i]):
            a, b = pts[i - 1], pts[i]
            lo, hi = 0.0, 1.0
            for _ in range(40):
                mid = (lo + hi) / 2
                p = (a[0] + (b[0] - a[0]) * mid, a[1] + (b[1] - a[1]) * mid)
                if inside(p):
                    lo = mid
                else:
                    hi = mid
            p = (a[0] + (b[0] - a[0]) * hi, a[1] + (b[1] - a[1]) * hi)
            return [p] + pts[i:]
    return [pts[-1], pts[-1]]      # 全部が内側（ノードが重なっている）


def _dedupe(pts):
    out = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - out[-1][0]) > 1e-9 or abs(p[1] - out[-1][1]) > 1e-9:
            out.append(p)
    return out


def _point_at(pts, cum, s):
    """弧長 s の点"""
    if s <= 0 or len(pts) == 1:
        return pts[0]
    if s >= cum[-1]:
        return pts[-1]
    lo, hi = 0, len(cum) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if cum[mid] <= s:
            lo = mid
        else:
            hi = mid
    seg = cum[lo + 1] - cum[lo]
    t = 0.0 if seg <= 0 else (s - cum[lo]) / seg
    a, b = pts[lo], pts[lo + 1]
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _seg_hits_rect(ax, ay, bx, by, x0, y0, x1, y1):
    """線分 a→b が軸に平行な矩形 [x0, x1]×[y0, y1] に触れるか（Liang–Barsky の切り取り）"""
    dx, dy = bx - ax, by - ay
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
        if p == 0:
            if q < 0:
                return False
            continue
        r = q / p
        if p < 0:
            if r > t1:
                return False
            if r > t0:
                t0 = r
        else:
            if r < t0:
                return False
            if r < t1:
                t1 = r
    return True


def _poly_hits_rect(pts, x0, y0, x1, y1):
    """折れ線が矩形に触れるか"""
    for k in range(len(pts) - 1):
        (ax, ay), (bx, by) = pts[k], pts[k + 1]
        if (max(ax, bx) < x0 or min(ax, bx) > x1 or max(ay, by) < y0 or min(ay, by) > y1):
            continue
        if _seg_hits_rect(ax, ay, bx, by, x0, y0, x1, y1):
            return True
    return False


def _rect_overlap(a, b):
    """2つの矩形 (x0, y0, x1, y1) の重なりの面積"""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if (w > 0 and h > 0) else 0.0


def _plabel_center(side, hx, hy, r, w, h, ux, uy):
    """パケットの札（基準の字の箱 w×h）の中心。

    side: 'above' / 'below' は画面の上下、'left' / 'right' は進行方向 (ux, uy) の左右
    （札の箱の一番近い角を、進む線からパケットの縁 + _PLABEL_GAP だけ離す。斜めの辺でも
    辺が札を貫かない）。'left-back' / 'right-back' は左右の札を進行方向の後ろへずらし、
    札の箱の前の端をパケットの前の端にそろえたもの（止まったパケットの札。前のノードに
    掛からない。_PLABEL_REST_SIDES）。r はパケットの半径。
    """
    if side == "above":
        return hx, hy - r - _PLABEL_GAP - h / 2.0
    if side == "below":
        return hx, hy + r + _PLABEL_GAP + h / 2.0
    nx, ny = (uy, -ux) if side.startswith("left") else (-uy, ux)
    d = r + _PLABEL_GAP + abs(nx) * w / 2.0 + abs(ny) * h / 2.0
    cx, cy = hx + nx * d, hy + ny * d
    if side.endswith("-back"):
        b = abs(ux) * w / 2.0 + abs(uy) * h / 2.0 - r
        cx, cy = cx - ux * b, cy - uy * b
    return cx, cy


def _plabel_reach(w):
    """札の向きを決める進行方向の平均の取り方: 弧長で前後この px の2点を結ぶ向き
    （折れ曲がる点で札がぱっと回らないよう、札の幅の半分ほどで均す）"""
    return max(24.0, w / 2.0)


def _plabel_at(side, pts, cum, s, r, w, h):
    """道 (pts, cum) の弧長 s にいる半径 r のパケットの札（基準の字の箱 w×h）の中心。

    進行方向の左右に置く札は、弧長で前後 _plabel_reach(w) の2点を結ぶ向きで置く（曲がり角で
    回り込む）。build の採点（FlowGraph._place_packet_labels）と描画
    （_Renderer.packet_labels_at）の両方がこれを呼ぶ（別々に書くと、選んだ所・隠すと決めた所と
    描いた所がずれる）。
    """
    hx, hy = _point_at(pts, cum, s)
    ux, uy = 1.0, 0.0
    if side not in ("above", "below"):
        reach = _plabel_reach(w)
        a = _point_at(pts, cum, max(0.0, s - reach))
        b = _point_at(pts, cum, min(cum[-1], s + reach))
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy)
        if n > 1e-6:
            ux, uy = dx / n, dy / n
    return _plabel_center(side, hx, hy, r, w, h, ux, uy)


def _plabel_side(plan, s):
    """側の列 plan（((弧長, 側), …)。弧長の昇順で最初は 0.0）の、弧長 s での側"""
    side = plan[0][1]
    for s0, sd in plan[1:]:
        if s >= s0:
            side = sd
    return side


def _plabel_rect(cx, cy, w, h):
    """札の外接矩形（基準の字の箱 w×h + 縁取り）"""
    return (cx - w / 2.0 - _HALO, cy - h / 2.0 - _HALO,
            cx + w / 2.0 + _HALO, cy + h / 2.0 + _HALO)


def _plabel_head(pk, hide, t):
    """build のとき: 時刻 t のパケット pk の頭の (α, 弧長)。

    α は _Renderer.packet_state の頭の α と同じ式（drop の薄れ × 途中の box の手前の薄れ ×
    色の α）。見えなければ 0。札はこの α が 0 のコマでは描かれない。
    """
    t0, _rid, v, s_end, t_end, kind, d, _trail, c, sc = pk
    dt = t - t0
    if dt < 0:
        return 0.0, 0.0
    if (kind == 0 and not t < t_end) or (kind == 2 and not t < t_end + _DROP_FADE):
        return 0.0, s_end
    s = min(max(dt * v, 0.0), s_end)
    a = 1.0
    if kind == 2 and t > t_end:
        a = min(max(1.0 - (t - t_end) / _DROP_FADE, 0.0), 1.0)
    dist = math.inf
    for lo, hi in hide:
        if lo <= s < hi:
            return 0.0, s
        dist = min(dist, lo - s if s < lo else s - hi)
    if dist < math.inf:
        a *= min(max(dist / max(d, 1.0), 0.0), 1.0)
    mix = 0.0
    if kind == 1 and t > t_end:
        mix = min(max((t - t_end) / _STOP_TINT, 0.0), 1.0)
        mix = mix * mix * (3.0 - 2.0 * mix)
    return a * (c[3] + (sc[3] - c[3]) * mix) / 255.0, s


def _plabel_blocks(idx, hard, fps, fade_out_at_end):
    """札を隠すコマから、隠す区間（秒の (始め, 終わり) の列）を作る。

    idx: 頭が見えるコマの番号（昇順）。hard: そのコマで札が重なるか。
    次のコマも隠すコマに数える: 最初のコマの1つ前（出るときに _PLABEL_FADE 秒で現れる）、
    頭が見えないコマ（途中の box の中）、fade_out_at_end なら最後のコマの1つ後（pass が尺の
    中で着くとき。着く手前で薄れる）。隠すコマに挟まれた見える間が _PLABEL_MIN_SHOW 秒より
    短ければつなぐ（一瞬だけ薄く光らせない）。区間は隠すコマ k の [k − 0.5, k + 0.5] / fps をつないだもの
    なので、隠すコマの時刻は区間の中（α = 0）、隠さないコマは区間から半コマ以上離れる（α > 0）。
    """
    if not idx:
        return []
    marks = [idx[0] - 1]
    for q, (k, hd) in enumerate(zip(idx, hard)):
        if q > 0 and k > idx[q - 1] + 1:
            marks.extend(range(idx[q - 1] + 1, k))      # 頭が見えないコマ
        if hd:
            marks.append(k)
    if fade_out_at_end:
        marks.append(idx[-1] + 1)
    runs = []
    for k in marks:
        if runs and k <= runs[-1][1] + 1:
            runs[-1][1] = max(runs[-1][1], k)
        else:
            runs.append([k, k])
    min_gap = _PLABEL_MIN_SHOW * fps - 1e-9               # 見えるコマの数で比べる
    merged = []
    for a, b in runs:
        if merged and a - merged[-1][1] - 1 < min_gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [((a - 0.5) / fps, (b + 0.5) / fps) for a, b in merged]


def _plabel_alpha(blocks, t):
    """札の α の倍率: 隠す区間 blocks の中は 0、区間から _PLABEL_FADE 秒離れると 1"""
    d = math.inf
    for a, b in blocks:
        if a <= t <= b:
            return 0.0
        d = min(d, a - t if t < a else t - b)
    return min(1.0, d / _PLABEL_FADE)


class _Grid:
    """矩形（外接矩形）を格子の升へ入れて、近いものだけを取り出す道具"""

    def __init__(self, cell=_HIT_CELL):
        self.cell = cell
        self.cells = {}

    def _span(self, x0, y0, x1, y1):
        c = self.cell
        return (range(int(math.floor(x0 / c)), int(math.floor(x1 / c)) + 1),
                range(int(math.floor(y0 / c)), int(math.floor(y1 / c)) + 1))

    def add(self, k, x0, y0, x1, y1):
        xs, ys = self._span(x0, y0, x1, y1)
        for gx in xs:
            for gy in ys:
                self.cells.setdefault((gx, gy), []).append(k)

    def near(self, x0, y0, x1, y1):
        xs, ys = self._span(x0, y0, x1, y1)
        out = set()
        for gx in xs:
            for gy in ys:
                out.update(self.cells.get((gx, gy), ()))
        return sorted(out)


def _bfs(n, adj, roots):
    """有向の BFS。(深さ, 親, 訪れた順) を返す（辿れないノードの深さは None）"""
    depth = [None] * n
    parent = [None] * n
    order = []
    dq = deque()
    for r in roots:
        if depth[r] is None:
            depth[r] = 0
            dq.append(r)
    while dq:
        u = dq.popleft()
        order.append(u)
        for v in adj[u]:
            if depth[v] is None:
                depth[v] = depth[u] + 1
                parent[v] = u
                dq.append(v)
    return depth, parent, order


# --- 図 ---

class FlowGraph:
    """flow_graph() / flow_tree() が返す図。出来事を足してから build() で Object にする。

    出来事（時刻 t はこの図の動画の先頭を 0 とした秒）:
      send(t, path, ...)       パケットを経路に沿って流す → 各パケットが止まる秒のリスト
      broadcast(t, root, ...)  root から全体へ一斉に配る → {名前: 届く秒}
      state(t, node, ...)      ノードの色・灰色（dim）・印（× / ✓）を変える
      cut(t, (a, b), ...)      辺を破線にして薄れさせる
      build(duration=None)     動画 Object にする（obj.figure.pos / obj.figure.arrival つき）
    読める属性: names（ノード名の並び）、pos（{名前: (x, y)} キャンバスの px）、size、
      levels（flow_tree の段ごとの名前）、amount（flow_tree(amount=) のときの {名前: 量}）。
    模式図であって実際の経路ではない（注記は呼び出し側が付ける）。
    """

    def __init__(self, nodes, edges=(), *, layout="given", direction="down", size=None,
                 padding=40, node="dot", node_radius=10, box=(240, 72), edge_width=3,
                 curve=0.0, colors=None, font=None, weight=None, label_size=36,
                 label_pos="auto", seed=0, _tree=None, _fn="flow_graph"):
        fn = self._fn = _fn
        # --- 引数の検証 ---
        self._layout = _require_choice(fn, "layout", layout, _LAYOUTS)
        self._direction = _require_choice(fn, "direction", direction, _DIRECTIONS)
        if size is None:
            proj = current_project()
            size = (proj.width, proj.height) if proj else (1920, 1080)
        self._W, self._H = _resolve_size(fn, size)
        _require_number(fn, "padding", padding, 0, None)
        self._padding = float(padding)
        self._node = _require_choice(fn, "node", node, _SHAPES)
        _require_number(fn, "node_radius", node_radius, 1, 200)
        self._node_radius = float(node_radius)
        if not isinstance(box, (list, tuple)) or len(box) != 2:
            raise ValueError(f"{fn}: box は (幅, 高さ) px で指定してください: {box!r}")
        _require_number(fn, "box の幅", box[0], 8, 4000)
        _require_number(fn, "box の高さ", box[1], 8, 4000)
        self._box = (float(box[0]), float(box[1]))
        _require_number(fn, "edge_width", edge_width, 0.5, 50)
        self._edge_width = float(edge_width)
        _require_number(fn, "curve", curve, -2, 2)
        self._curve = float(curve)
        _require_number(fn, "label_size", label_size, 30, 400)
        self._label_size = _n(label_size)
        self._label_pos = _require_choice(fn, "label_pos", label_pos, _LABEL_POS)
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError(f"{fn}: seed は整数で指定してください: {seed!r}")
        self._seed = seed
        self._font = font
        self._weight = weight
        self._pal = fk.palette(fn, colors)

        # --- ノードと辺 ---
        names, attrs, edge_items = _read_graph(fn, nodes, edges)
        if not names:
            raise ValueError(f"{fn}: ノードが1つもありません")
        if len(names) > _MAX_NODES:
            raise ValueError(f"{fn}: ノードが {len(names)} 個あります（上限 {_MAX_NODES}）")
        if len(edge_items) > _MAX_EDGES:
            raise ValueError(f"{fn}: 辺が {len(edge_items)} 本あります（上限 {_MAX_EDGES}）")
        self.names = []
        self._index = {}
        for name in names:
            if not _is_name(name):
                raise TypeError(
                    f"{fn}: ノードの名前は文字列か整数にしてください: {name!r}")
            if name in self._index:
                raise ValueError(f"{fn}: ノードの名前が重複しています: {name!r}")
            self._index[name] = len(self.names)
            self.names.append(name)
        n = len(self.names)
        self._label = [None] * n
        self._shape = [self._node] * n
        self._given = [None] * n
        self._layer_attr = [None] * n
        for i, a in enumerate(attrs):
            name = self.names[i]
            if "label" in a and a["label"] is not None:
                lab = a["label"]
                if not isinstance(lab, str) or not lab.strip():
                    raise ValueError(f"{fn}: nodes['{name}'] の label は空でない文字列にしてください: {lab!r}")
                if "\n" in lab or "\r" in lab:
                    raise ValueError(f"{fn}: nodes['{name}'] の label は1行にしてください（改行は使えません）")
                self._label[i] = lab
            if "shape" in a:
                self._shape[i] = _require_choice(fn, f"nodes['{name}'] の shape", a["shape"], _SHAPES)
            if "pos" in a:
                p = a["pos"]
                if not isinstance(p, (list, tuple)) or len(p) != 2:
                    raise ValueError(f"{fn}: nodes['{name}'] の pos は (x, y) px で指定してください: {p!r}")
                _require_number(fn, f"nodes['{name}'] の pos の x", p[0])
                _require_number(fn, f"nodes['{name}'] の pos の y", p[1])
                self._given[i] = (float(p[0]), float(p[1]))
            if "layer" in a:
                k = a["layer"]
                if isinstance(k, bool) or not isinstance(k, int) or k < 0:
                    raise ValueError(f"{fn}: nodes['{name}'] の layer は 0 以上の整数にしてください: {k!r}")
                self._layer_attr[i] = k
        self._edges = []                 # (a, b, delay, curve)
        self._pair = {}                  # (a, b) -> (辺の番号, 向きが同じか)
        self._out = [[] for _ in range(n)]
        self._adj = [[] for _ in range(n)]   # 無向: (相手, 辺の番号)
        self._indeg = [0] * n
        for j, (ea, eb, d) in enumerate(edge_items):
            for end in (ea, eb):
                if end not in self._index:
                    raise ValueError(
                        f"{fn}: 辺 ({ea!r}, {eb!r}) のノード {end!r} がありません"
                        f"{_suggest_hint(end, [str(x) for x in self.names])}")
            a, b = self._index[ea], self._index[eb]
            if a == b:
                raise ValueError(f"{fn}: 自分自身へ戻る辺は描けません: ({ea!r}, {eb!r})")
            if (a, b) in self._pair:
                raise ValueError(f"{fn}: 同じ2点を結ぶ辺が2本あります: ({ea!r}, {eb!r})")
            delay = d.get("delay")
            if delay is not None:
                _require_number(fn, f"辺 ({ea!r}, {eb!r}) の delay", delay, 0, 3600)
                delay = float(delay)
            ecurve = d.get("curve")
            if ecurve is not None:
                _require_number(fn, f"辺 ({ea!r}, {eb!r}) の curve", ecurve, -2, 2)
                ecurve = float(ecurve)
            self._pair[(a, b)] = (j, True)
            self._pair[(b, a)] = (j, False)
            self._edges.append((a, b, delay, ecurve))
            self._out[a].append(b)
            self._adj[a].append((b, j))
            self._adj[b].append((a, j))
            self._indeg[b] += 1

        # --- flow_tree の情報 ---
        self._tree = _tree
        self._cluster = [False] * n
        if _tree is not None:
            for i in _tree["cluster"]:
                self._cluster[i] = True
        self.levels = None if _tree is None else [list(x) for x in _tree["level_names"]]
        self.amount = None if (_tree is None or _tree.get("amounts") is None) else {
            self.names[i]: a for i, a in enumerate(_tree["amounts"])}

        # --- 文字（Pillow で寸法を測る。描くのは build の後）---
        self._sprites = {}               # 文字列 -> Sprite
        self._ref = None                 # 基準の字の (上端, 下端)（ベースラインから）
        if any(self._label):
            for lab in self._label:
                if lab is not None:
                    self._sprite(lab)

        # --- 配置 ---
        self._radius = [self._node_radius] * n
        self._half = [(0.0, 0.0)] * n
        self._size_boxes()
        self._resolve_label_modes_pre()
        self._pos = self._run_layout()
        if _tree is not None:
            self._tree_radii()
        self._build_edges()
        self._resolve_label_modes_post()
        self._refine_labels()
        self._place_labels()
        self._check_labels()
        self.pos = {self.names[i]: (self._pos[i][0], self._pos[i][1]) for i in range(n)}
        self.size = (self._W, self._H)

        # --- 出来事 ---
        self._log = []                   # 鍵に入れる出来事（呼んだ順）
        self._routes = []                # [(点の列, 弧長の列, 各辺の終わりの弧長)]
        self._route_memo = {}
        self._packets = []               # (t0, 道, 速さ, 止まる弧長, 止まる秒, 種類, 直径, 尾, 色, 止まった色)
        self._bcast_packets = set()      # broadcast が出したパケットの番号（上限のエラー文用）
        self._packet_labels = []         # (パケットの番号, 文字列)
        self._ripples = []               # (t0, 種類, x, y, a, b, 角丸, 色, 幅)
        self._node_events = []           # (t, 連番, ノード, rgb|None, dim|None, mark|None, 秒)
        self._cuts = {}                  # 辺の番号 -> (t, 秒)
        self._stop_next = {}             # (止まるノード, 手前のノード) -> (最後の中心の位置, 直径)
        self._arrival = {}
        self._uses_delay = False

    # --- 文字 ---

    def _sprite(self, text):
        sp = self._sprites.get(text)
        if sp is None:
            sp = fk.label(self._fn, text, size=self._label_size, font=self._font,
                          weight=self._weight, color=(255, 255, 255, 255),
                          border=_HALO, border_color=(0, 0, 0, 255))
            self._sprites[text] = sp
            if self._ref is None:
                font = sp.layout["fonts"][0]
                b = font.getbbox("国", anchor="ls")
                self._ref = (float(b[1]), float(b[3]))
        return sp

    def _label_box(self, text):
        """文字の寸法: (字送りの幅, 基準の上端, 基準の下端)（ベースラインから）"""
        sp = self._sprites[text]
        return float(sp.meta["content_width"]), self._ref[0], self._ref[1]

    # --- 配置 ---

    def _size_boxes(self):
        n = len(self.names)
        for i in range(n):
            if self._shape[i] == "box":
                w, h = self._box
                if self._label[i] is not None:
                    cw, top, bot = self._label_box(self._label[i])
                    w = max(w, cw + 2 * _BOX_PAD[0])
                    h = max(h, (bot - top) + 2 * _BOX_PAD[1])
                self._half[i] = (w / 2.0, h / 2.0)
            else:
                self._half[i] = (self._radius[i], self._radius[i])

    def _resolve_label_modes_pre(self):
        """配置の前に決まる文字の位置。

        box の文字は中、label_pos を指定した点はその位置。auto の点は、given なら下、
        layered なら _layered の中（段の並びが決まった後・余白を測る前）、radial / rings なら
        配置の後（_resolve_label_modes_post）に第一候補を決める。
        """
        n = len(self.names)
        self._label_mode = [None] * n
        self._label_auto = [False] * n
        for i in range(n):
            if self._label[i] is None:
                continue
            if self._shape[i] == "box":
                self._label_mode[i] = "center"
            elif self._label_pos != "auto":
                self._label_mode[i] = self._label_pos
            else:
                self._label_auto[i] = True
                if self._layout == "given":
                    self._label_mode[i] = "below"

    def _layered_label_modes(self, lrank, nb):
        """layered の auto の第一候補: 流れの向きに文字を置くのは葉だけ。

        流れの向きの側には子へ出ていく辺があるので、葉でない点の文字をそこに置くと辺が
        文字を貫く。根（前の段とつながらない点）は流れと逆の側、途中の点は横
        （down / up は右、right / left は上）。辺・ノードとの重なりは配置の後に
        _refine_labels が確かめて、重なれば別の側へ置き直す。
        """
        leaf_m, root_m, mid_m = _LAYERED_LABEL[self._direction]
        for i in range(len(self.names)):
            if not self._label_auto[i] or self._shape[i] == "box":
                continue
            nxt = any(lrank[v] > lrank[i] for v in nb[i])
            prv = any(lrank[v] < lrank[i] for v in nb[i])
            self._label_mode[i] = leaf_m if not nxt else (root_m if not prv else mid_m)

    def _resolve_label_modes_post(self):
        """radial / rings の auto の第一候補: 中心から外向き"""
        if self._layout not in ("radial", "rings"):
            return
        cx, cy = self._center
        for i in range(len(self.names)):
            if self._label[i] is None or self._label_mode[i] is not None:
                continue
            dx, dy = self._pos[i][0] - cx, self._pos[i][1] - cy
            r = math.hypot(dx, dy)
            if r < 1e-6:
                self._label_mode[i] = "below"
                continue
            c, s = dx / r, dy / r
            if c > 0.6:
                self._label_mode[i] = "right"
            elif c < -0.6:
                self._label_mode[i] = "left"
            else:
                self._label_mode[i] = "below" if s > 0 else "above"

    def _label_offset(self, i, mode):
        """ノードの中心から見た文字の (行頭の x, ベースラインの y)"""
        cw, top, bot = self._label_box(self._label[i])
        hx, hy = self._half[i]
        if mode == "below":
            return -cw / 2.0, hy + _LABEL_GAP - top
        if mode == "above":
            return -cw / 2.0, -hy - _LABEL_GAP - bot
        if mode == "right":
            return hx + _LABEL_GAP, -(top + bot) / 2.0
        if mode == "left":
            return -hx - _LABEL_GAP - cw, -(top + bot) / 2.0
        return -cw / 2.0, -(top + bot) / 2.0          # center

    def _ink_rel(self, i, mode):
        """ノードの中心から見た文字の外接矩形（縁取り込み）"""
        ox, oy = self._label_offset(i, mode)
        sp = self._sprites[self._label[i]]
        font = sp.layout["fonts"][0]
        b = font.getbbox(self._label[i], anchor="ls")
        return (ox + b[0] - _HALO, oy + b[1] - _HALO, ox + b[2] + _HALO, oy + b[3] + _HALO)

    def _extents(self, i, mode=None):
        """ノード i の中心から (左, 上, 右, 下) へのはみ出し px（文字込み）"""
        hx, hy = self._half[i]
        ext = [hx, hy, hx, hy]
        if self._label[i] is not None:
            m = mode or self._label_mode[i]
            if m is None:
                # radial / rings の auto: どの向きにも出うるので一番長い向きで見積もる
                cw, top, bot = self._label_box(self._label[i])
                reach = max(hx, hy) + _LABEL_GAP + max(cw, bot - top) + _HALO
                return [reach] * 4
            x0, y0, x1, y1 = self._ink_rel(i, m)
            ext = [max(ext[0], -x0), max(ext[1], -y0), max(ext[2], x1), max(ext[3], y1)]
        return ext

    def _inner_box(self):
        """自動配置でノードの中心を置ける範囲（余白と、はみ出しの最大を除く）"""
        n = len(self.names)
        m = [0.0, 0.0, 0.0, 0.0]
        for i in range(n):
            e = self._extents(i)
            for k in range(4):
                m[k] = max(m[k], e[k])
        x0 = self._padding + m[0]
        y0 = self._padding + m[1]
        x1 = self._W - self._padding - m[2]
        y1 = self._H - self._padding - m[3]
        if x1 - x0 < 1 or y1 - y0 < 1:
            raise ValueError(
                f"{self._fn}: キャンバス {self._W}x{self._H} に図が収まりません"
                f"（余白 {self._padding:g}px と文字・box のはみ出しを除くと場所が残りません）。"
                f"size を広げるか、padding・label_size・box を小さくしてください")
        return x0, y0, x1, y1

    def _roots(self):
        return [i for i in range(len(self.names)) if self._indeg[i] == 0]

    def _unreached(self, depth, extra_ok=None):
        out = []
        for i in range(len(self.names)):
            if depth[i] is None and not (extra_ok and extra_ok[i] is not None):
                out.append(self.names[i])
        return out

    def _run_layout(self):
        fn = self._fn
        n = len(self.names)
        lay = self._layout
        self._span = None
        if lay == "given":
            missing = [self.names[i] for i in range(n) if self._given[i] is None]
            if missing:
                raise ValueError(
                    f"{fn}: layout='given' では全ノードに pos が要ります"
                    f"（無いノード: {', '.join(map(str, missing[:8]))}"
                    f"{' ほか' if len(missing) > 8 else ''}）。"
                    f"自動で並べるなら layout='layered' / 'radial' / 'rings'")
            for i in range(n):
                x, y = self._given[i]
                if not (0 <= x <= self._W and 0 <= y <= self._H):
                    raise ValueError(
                        f"{fn}: nodes['{self.names[i]}'] の pos ({x:g}, {y:g}) がキャンバス "
                        f"{self._W}x{self._H} の外です")
            self._center = (self._W / 2.0, self._H / 2.0)
            return list(self._given)
        roots = self._roots()
        if lay == "layered":
            return self._layered(roots)
        if lay == "radial":
            return self._radial(roots)
        return self._rings(roots)

    def _layered(self, roots):
        fn = self._fn
        n = len(self.names)
        depth, _parent, order = _bfs(n, self._out, roots)
        missing = self._unreached(depth, self._layer_attr)
        if missing:
            raise ValueError(
                f"{fn}: layout='layered' で、根（入ってくる辺の無いノード: "
                f"{', '.join(map(str, [self.names[r] for r in roots][:5])) or 'なし'}）から辿れない"
                f"ノードがあります: {', '.join(map(str, missing[:8]))}"
                f"{' ほか' if len(missing) > 8 else ''}。辺を足すか、"
                f"nodes の 'layer' で段を指定してください")
        layer_of = [self._layer_attr[i] if self._layer_attr[i] is not None else depth[i]
                    for i in range(n)]
        seen = {u: k for k, u in enumerate(order)}
        keys = sorted(set(layer_of))
        rank = {k: j for j, k in enumerate(keys)}
        layers = [[] for _ in keys]
        for i in sorted(range(n), key=lambda i: (seen.get(i, n + i))):
            layers[rank[layer_of[i]]].append(i)
        lrank = [rank[layer_of[i]] for i in range(n)]
        pos_in = {}
        for lay in layers:
            for j, u in enumerate(lay):
                pos_in[u] = j
        nb = [[v for v, _e in self._adj[u]] for u in range(n)]

        def sweep(k, ref):
            cur = layers[k]

            def key(u):
                ns = [pos_in[v] for v in nb[u] if lrank[v] == ref]
                return (sum(ns) / len(ns) if ns else float(pos_in[u]), pos_in[u])
            cur.sort(key=key)
            for j, u in enumerate(cur):
                pos_in[u] = j

        L = len(layers)
        for _ in range(2):          # 重心法を2往復
            for k in range(1, L):
                sweep(k, k - 1)
            for k in range(L - 2, -1, -1):
                sweep(k, k + 1)
        self._layered_label_modes(lrank, nb)      # 余白（_inner_box）を測る前に決める
        x0, y0, x1, y1 = self._inner_box()
        out = [None] * n
        for k, lay in enumerate(layers):
            v = 0.5 if L == 1 else k / (L - 1)
            m = len(lay)
            for j, u in enumerate(lay):
                w = (j + 0.5) / m
                if self._direction == "down":
                    out[u] = (x0 + w * (x1 - x0), y0 + v * (y1 - y0))
                elif self._direction == "up":
                    out[u] = (x0 + w * (x1 - x0), y1 - v * (y1 - y0))
                elif self._direction == "right":
                    out[u] = (x0 + v * (x1 - x0), y0 + w * (y1 - y0))
                else:
                    out[u] = (x1 - v * (x1 - x0), y0 + w * (y1 - y0))
        self._layers = layers
        self._center = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
        self._inner = (x0, y0, x1, y1)
        return out

    def _radial(self, roots):
        fn = self._fn
        n = len(self.names)
        if not roots:
            raise ValueError(
                f"{fn}: layout='radial' の根（入ってくる辺の無いノード）がありません"
                f"（辺が輪になっています）。layout='rings' か 'given' を使ってください")
        root = roots[0]
        depth, parent, order = _bfs(n, self._out, [root])
        missing = self._unreached(depth)
        if missing:
            raise ValueError(
                f"{fn}: layout='radial' で、根 {self.names[root]!r} から辿れないノードがあります: "
                f"{', '.join(map(str, missing[:8]))}{' ほか' if len(missing) > 8 else ''}"
                f"（radial は根が1つの木を描きます。根が複数なら layout='layered'）")
        children = [[] for _ in range(n)]
        for u in order:
            if parent[u] is not None:
                children[parent[u]].append(u)
        leaves = [0] * n
        for u in reversed(order):
            leaves[u] = 1 if not children[u] else sum(leaves[c] for c in children[u])
        span = [None] * n
        span[root] = (-math.pi / 2, 2 * math.pi)
        for u in order:
            start, width = span[u]
            acc = start
            for c in children[u]:
                w = width * leaves[c] / leaves[u]
                span[c] = (acc, w)
                acc += w
        maxd = max(depth)
        x0, y0, x1, y1 = self._inner_box()
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        R = min(x1 - x0, y1 - y0) / 2.0
        out = [None] * n
        for u in range(n):
            if u == root or maxd == 0:
                out[u] = (cx, cy)
                continue
            r = R * depth[u] / maxd
            a = span[u][0] + span[u][1] / 2.0
            out[u] = (cx + r * math.cos(a), cy + r * math.sin(a))
        self._span = span
        self._parent = parent
        self._center = (cx, cy)
        self._R = R
        return out

    def _rings(self, roots):
        fn = self._fn
        n = len(self.names)
        depth, _parent, order = _bfs(n, self._out, roots)
        missing = self._unreached(depth, self._layer_attr)
        if missing:
            raise ValueError(
                f"{fn}: layout='rings' で段の決まらないノードがあります（根から辿れず、"
                f"'layer' もありません）: {', '.join(map(str, missing[:8]))}"
                f"{' ほか' if len(missing) > 8 else ''}")
        ring_of = [self._layer_attr[i] if self._layer_attr[i] is not None else depth[i]
                   for i in range(n)]
        seen = {u: k for k, u in enumerate(order)}
        keys = sorted(set(ring_of))
        rank = {k: j for j, k in enumerate(keys)}
        rings = [[] for _ in keys]
        for i in sorted(range(n), key=lambda i: seen.get(i, n + i)):
            rings[rank[ring_of[i]]].append(i)
        x0, y0, x1, y1 = self._inner_box()
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        R = min(x1 - x0, y1 - y0) / 2.0
        K = len(rings)
        single_center = len(rings[0]) == 1
        out = [None] * n
        ang = [None] * n
        for k, ring in enumerate(rings):
            if k == 0 and single_center:
                out[ring[0]] = (cx, cy)
                continue
            if single_center:
                r = R * k / max(1, K - 1)
            else:
                r = R * (k + 1) / K
            if k > 0:
                prev = set(rings[k - 1])

                def bary(u):
                    sx = sy = 0.0
                    cnt = 0
                    for v, _e in self._adj[u]:
                        if v in prev and ang[v] is not None:
                            sx += math.cos(ang[v])
                            sy += math.sin(ang[v])
                            cnt += 1
                    if cnt == 0 or (abs(sx) < 1e-12 and abs(sy) < 1e-12):
                        return None
                    a = math.atan2(sy, sx)
                    # -π/2（真上）から時計回りの角度へ
                    return (a + math.pi / 2) % (2 * math.pi)
                keyed = []
                for j, u in enumerate(ring):
                    b = bary(u)
                    keyed.append((b if b is not None else 2 * math.pi * j / len(ring), j, u))
                ring[:] = [u for _b, _j, u in sorted(keyed)]
            m = len(ring)
            for j, u in enumerate(ring):
                a = -math.pi / 2 + 2 * math.pi * j / m
                ang[u] = a
                out[u] = (cx + r * math.cos(a), cy + r * math.sin(a))
        self._center = (cx, cy)
        self._R = R
        return out

    def _tree_radii(self):
        """flow_tree: 段ごとに点の大きさを詰め、点の塊を散らす"""
        tree = self._tree
        rng = random.Random(self._seed)
        n_levels = len(tree["levels"])
        cx, cy = self._center
        for k in range(n_levels):
            idx = tree["level_idx"][k]
            m = len(idx)
            if tree["cluster_level"] == k:
                for u in idx:
                    self._radius[u] = _CLUSTER_D / 2.0
                    self._half[u] = (_CLUSTER_D / 2.0, _CLUSTER_D / 2.0)
                continue
            if k == 0 or m <= 1:
                continue
            if self._layout in ("radial", "rings"):
                rk = sum(math.hypot(self._pos[u][0] - cx, self._pos[u][1] - cy) for u in idx) / m
                spacing = 2 * math.pi * rk / m
            else:
                x0, y0, x1, y1 = self._inner
                extent = (x1 - x0) if self._direction in ("down", "up") else (y1 - y0)
                spacing = extent / m
            r = min(self._node_radius, max(2.5, spacing * 0.32))
            for u in idx:
                if self._shape[u] == "dot":
                    self._radius[u] = r
                    self._half[u] = (r, r)
        k = tree["cluster_level"]
        if k is None:
            return
        idx = tree["level_idx"][k]
        if self._layout == "radial":
            span = self._span
            R = self._R
            for u in idx:
                p = self._parent[u]
                start, width = span[p] if p is not None else (-math.pi / 2, 2 * math.pi)
                a = start + width * rng.random()
                r = R * math.sqrt(_CLUSTER_BAND ** 2 + (1 - _CLUSTER_BAND ** 2) * rng.random())
                self._pos[u] = (cx + r * math.cos(a), cy + r * math.sin(a))
        elif self._layout == "rings":
            R = self._R
            m = len(idx)
            for j, u in enumerate(idx):
                a = -math.pi / 2 + 2 * math.pi * (j + rng.random()) / m
                r = R * math.sqrt(_CLUSTER_BAND ** 2 + (1 - _CLUSTER_BAND ** 2) * rng.random())
                self._pos[u] = (cx + r * math.cos(a), cy + r * math.sin(a))
        else:
            # layered: 最後の段の帯（段の位置から内側へ 12%）に散らす
            x0, y0, x1, y1 = self._inner
            layer = self._layers[-1]
            m = len(layer)
            vertical = self._direction in ("down", "up")
            band = 0.12 * ((y1 - y0) if vertical else (x1 - x0))
            inward = -1.0 if self._direction in ("down", "right") else 1.0
            for j, u in enumerate(layer):
                w = (j + rng.random()) / m
                off = inward * band * rng.random()
                bx, by = self._pos[u]
                if vertical:
                    self._pos[u] = (x0 + w * (x1 - x0), by + off)
                else:
                    self._pos[u] = (bx + off, y0 + w * (y1 - y0))

    def _label_rect(self, i, mode):
        """ノード i の文字を mode に置いたときの、キャンバスでの外接矩形（縁取り込み）"""
        x0, y0, x1, y1 = self._ink_rel(i, mode)
        px, py = self._pos[i]
        return px + x0, py + y0, px + x1, py + y1

    def _hit_index(self):
        """文字の置き場所を調べるための、辺とノードの格子（1回だけ作る）"""
        egrid = _Grid()
        bbox = []
        for j, pts in enumerate(self._edge_pts):
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            b = (min(xs), min(ys), max(xs), max(ys))
            bbox.append(b)
            egrid.add(j, *b)
        ngrid = _Grid()
        nrect = []
        for j in range(len(self.names)):
            x, y = self._pos[j]
            hx, hy = self._half[j]
            r = (x - hx - 2.0, y - hy - 2.0, x + hx + 2.0, y + hy + 2.0)
            nrect.append(r)
            if not self._cluster[j]:
                ngrid.add(j, *r)
        return egrid, bbox, ngrid, nrect

    def _rect_hits(self, idx, i, rect):
        """矩形 rect が重なる (ほかのノードの数, 辺の数)。辺は幅の半分だけ太らせて見る"""
        egrid, bbox, ngrid, nrect = idx
        x0, y0, x1, y1 = rect
        nodes = 0
        for j in ngrid.near(x0, y0, x1, y1):
            if j == i:
                continue
            r = nrect[j]
            if r[0] < x1 and x0 < r[2] and r[1] < y1 and y0 < r[3]:
                nodes += 1
        w = self._edge_width / 2.0
        ex0, ey0, ex1, ey1 = x0 - w, y0 - w, x1 + w, y1 + w
        edges = 0
        for j in egrid.near(ex0, ey0, ex1, ey1):
            b = bbox[j]
            if b[2] < ex0 or b[0] > ex1 or b[3] < ey0 or b[1] > ey1:
                continue
            if _poly_hits_rect(self._edge_pts[j], ex0, ey0, ex1, ey1):
                edges += 1
        return nodes, edges

    def _refine_labels(self):
        """点の文字を、辺・ほかのノードと重ならない側へ置き直す（auto のときだけ）。

        第一候補（layered は段の関係・radial / rings は外向き・given は下）から順に
        _LABEL_ORDER の側を試し、文字の外接矩形（縁取り込み）がキャンバスに収まり、
        辺の折れ線にもほかのノードにも触れない最初の側を選ぶ。どの側も触れるときは、
        収まる側のうち重なりの最も少ない側（ほかのノードとの重なりを先に比べる）にする。
        label_pos を指定した文字は動かさない。辺が下を通る文字には板を敷く
        （self._plate。描くのは node_layer、警告は _check_labels）。box の文字は中なので見ない。
        """
        n = len(self.names)
        self._plate = [False] * n
        self._label_hits = [(0, 0)] * n
        targets = [i for i in range(n) if self._label[i] is not None and self._shape[i] != "box"]
        if not targets:
            return
        idx = self._hit_index()
        order = _LABEL_ORDER[self._direction if self._layout == "layered" else None]
        W, H = self._W, self._H
        for i in targets:
            first = self._label_mode[i]
            if not self._label_auto[i]:
                cands = [first]
            else:
                cands = [first] + [m for m in order if m != first]
            best = None
            for k, mode in enumerate(cands):
                rect = self._label_rect(i, mode)
                fits = (rect[0] >= -0.5 and rect[1] >= -0.5 and rect[2] <= W + 0.5
                        and rect[3] <= H + 0.5)
                hits = self._rect_hits(idx, i, rect)
                score = (0 if fits else 1, hits[0], hits[1], k)
                if best is None or score < best[0]:
                    best = (score, mode, hits)
                if fits and hits == (0, 0):
                    break
            _score, mode, hits = best
            self._label_mode[i] = mode
            self._label_hits[i] = hits
            self._plate[i] = hits[1] > 0

    def _place_labels(self):
        """文字の置き場所（キャンバスの整数 px）と、辺が下を通る文字の板を決める"""
        n = len(self.names)
        self._label_at = [None] * n       # (行頭の x, ベースラインの y)
        self._plate_rect = [None] * n     # (中心 x, 中心 y, 横の半分, 縦の半分, 角丸)
        for i in range(n):
            if self._label[i] is None:
                continue
            ox, oy = self._label_offset(i, self._label_mode[i])
            lx, ly = int(round(self._pos[i][0] + ox)), int(round(self._pos[i][1] + oy))
            self._label_at[i] = (lx, ly)
            if self._plate[i]:
                font = self._sprites[self._label[i]].layout["fonts"][0]
                b = font.getbbox(self._label[i], anchor="ls")
                pad = _HALO + _PLATE_PAD
                x0, y0 = lx + b[0] - pad, ly + b[1] - pad
                x1, y1 = lx + b[2] + pad, ly + b[3] + pad
                self._plate_rect[i] = ((x0 + x1) / 2.0, (y0 + y1) / 2.0,
                                       (x1 - x0) / 2.0, (y1 - y0) / 2.0, _PLATE_RADIUS)

    def _check_labels(self):
        """文字の重なり・はみ出し・辺やノードとの重なりを知らせる（描きはする）"""
        crossed = [self.names[i] for i in range(len(self.names)) if self._plate[i]]
        if crossed:
            warnings.warn(
                f"{self._fn}: 辺が文字の下を通ります（文字の下に panel 色の板を敷いて描きます）: "
                f"{', '.join(map(str, crossed[:6]))}{' ほか' if len(crossed) > 6 else ''}"
                f"（label_pos を変える・size を広げる・layout='given' で置く）")
        covered = [self.names[i] for i in range(len(self.names)) if self._label_hits[i][0] > 0]
        if covered:
            warnings.warn(
                f"{self._fn}: 文字がほかのノードに重なっています: "
                f"{', '.join(map(str, covered[:6]))}{' ほか' if len(covered) > 6 else ''}"
                f"（size を広げる・label_size を下げる・label_pos を変える・layout='given' で置く）")
        rects = []
        for i in range(len(self.names)):
            if self._label[i] is None:
                continue
            x0, y0, x1, y1 = self._ink_rel(i, self._label_mode[i])
            px, py = self._pos[i]
            rects.append((px + x0, py + y0, px + x1, py + y1, self.names[i]))
        out = [r[4] for r in rects
               if r[0] < -0.5 or r[1] < -0.5 or r[2] > self._W + 0.5 or r[3] > self._H + 0.5]
        if out:
            warnings.warn(
                f"{self._fn}: 文字がキャンバスからはみ出します: {', '.join(map(str, out[:6]))}"
                f"（size を広げるか padding を増やしてください）")
        # 重なり（格子で近いものだけ比べる）
        cell = 256.0
        grid = {}
        hits = []
        for k, r in enumerate(rects):
            for gx in range(int(r[0] // cell), int(r[2] // cell) + 1):
                for gy in range(int(r[1] // cell), int(r[3] // cell) + 1):
                    for j in grid.get((gx, gy), ()):
                        o = rects[j]
                        if r[0] < o[2] and o[0] < r[2] and r[1] < o[3] and o[1] < r[3]:
                            hits.append((o[4], r[4]))
                    grid.setdefault((gx, gy), []).append(k)
        if hits:
            pairs = sorted(set(hits), key=repr)
            warnings.warn(
                f"{self._fn}: 文字が重なっています: "
                f"{', '.join(f'{a}/{b}' for a, b in pairs[:5])}"
                f"{' ほか' if len(pairs) > 5 else ''}（size を広げる・label_size を下げる・"
                f"label_pos を変える・layout='given' で置く）")

    def _curve_caps(self):
        """図全体の curve（辺ごとの 'curve' の無い辺）の、辺ごとの |curve| の上限。

        曲げた辺は端で弦から atan(2·curve) だけ傾いて出る。1つのノードに辺が多く集まると、
        どの辺も同じ向きに曲がって隣の辺の上へ倒れ込み、渦（風車）に見えた（flow_tree の
        1 → 40 に curve=0.3。見本の場面）。そこで、ノードから出る辺の組（子へ向かう辺）と
        入る辺の組ごとに、弦の向きの角の間（輪を回る間の中央値 g）を測り、その組の辺が
        g の _CURVE_SPREAD 倍までしか傾かないよう、curve を tan(_CURVE_SPREAD·g) / 2 までに
        弱める。上限は辺の本数ではなく隣の辺との角の間で決まる: 周りに均等に散った辺
        （radial の flow_tree）なら 3〜4 本の組で curve=0.3 はそのまま、40 本なら約 0.04。
        片側へ開く扇（layered や given で子が同じ側に並ぶ）は角の間が狭いので、少なくても
        弱まる（layered の根から子へ 2 本で約 0.28、3 本で約 0.16、4 本で約 0.11）。
        組ごとに1つの値にするので、点の塊のように散らばった子でも扇の曲がりがそろう。
        """
        groups = {}              # (ノード, 出る辺か) -> [(弦の角度, 辺の番号)]
        for j, (a, b, _d, _c) in enumerate(self._edges):
            (ax, ay), (bx, by) = self._pos[a], self._pos[b]
            if math.hypot(bx - ax, by - ay) < 1e-9:
                continue
            groups.setdefault((a, True), []).append((math.atan2(by - ay, bx - ax), j))
            groups.setdefault((b, False), []).append((math.atan2(ay - by, ax - bx), j))
        caps = [math.inf] * len(self._edges)
        for lst in groups.values():
            m = len(lst)
            if m < 2:
                continue
            lst.sort()
            gaps = sorted((lst[(k + 1) % m][0] - lst[k][0]) % (2 * math.pi) for k in range(m))
            g = gaps[(m - 1) // 2]
            cap = math.tan(min(_CURVE_SPREAD * g, 1.4)) / 2.0
            for _a, j in lst:
                caps[j] = min(caps[j], cap)
        return caps

    def _build_edges(self):
        """辺の折れ線（ノードの縁で切ったもの）と弧長"""
        self._edge_pts = []
        self._edge_kind = []
        self._edge_fan_parent = []       # 扇の辺の親（扇でなければ None）
        caps = self._curve_caps() if self._curve else None
        self._edge_curve = []            # 辺ごとに使った curve（確認用）
        for j, (a, b, _delay, ecurve) in enumerate(self._edges):
            # 扇（細く薄い辺を親ごとに1回で塗る）: 点の塊への辺と、flow_tree で子が
            # _FAN_CHILDREN を超える親からの辺（何百本も 3px で描くと根の周りが白く潰れる）
            if self._cluster[b] or (self._tree is not None
                                    and len(self._out[a]) > _FAN_CHILDREN):
                parent = a
            elif self._cluster[a]:
                parent = b
            else:
                parent = None
            c = self._curve if ecurve is None else ecurve
            if ecurve is None and caps is not None and abs(c) > caps[j]:
                c = math.copysign(caps[j], c)       # 辺の集まるノードでは弱める（_curve_caps）
            self._edge_curve.append(c)
            # 扇の辺も同じ折れ線を描く線とパケットの道に使う（曲げても外れない）。
            # 何千本もあるので刻みは粗く（12px）する
            pts = _bezier_pts(self._pos[a], self._pos[b], c, step=4.0 if parent is None else 12.0)
            pts = _cut_front(pts, self._inside(a))
            pts = list(reversed(_cut_front(list(reversed(pts)), self._inside(b))))
            pts = _dedupe(pts)
            if len(pts) == 1:
                pts = [pts[0], pts[0]]
            self._edge_pts.append(pts)
            self._edge_fan_parent.append(parent)
            self._edge_kind.append("edge" if parent is None else "fan")

    def _inside(self, i):
        cx, cy = self._pos[i]
        if self._shape[i] == "box":
            hx, hy = self._half[i][0] + _GAP, self._half[i][1] + _GAP
            return lambda p: abs(p[0] - cx) < hx and abs(p[1] - cy) < hy
        r = self._radius[i] + (1.0 if self._cluster[i] else _GAP)
        r2 = r * r
        return lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2 < r2

    # --- 経路 ---

    def _node_index(self, fn, name, what="ノード"):
        try:
            hash(name)
        except TypeError:
            raise TypeError(f"{fn}: {what}の名前が不正です: {name!r}") from None
        if name not in self._index:
            raise ValueError(
                f"{fn}: {what} {name!r} はありません"
                f"{_suggest_hint(name, [str(x) for x in self.names])}")
        return self._index[name]

    def _route(self, fn, path):
        """経路（ノードの番号の列）→ 道の番号。

        道は辺の折れ線をつないだもの: (点の列, 弧長の列, 各辺の終わりの弧長,
        各辺の始まりの弧長, 隠す区間, 通り抜ける点)。
        途中の点（dot）はノードの中心を通る。通り抜ける点として (中心の弧長, 半径) を持ち、
        描くときにパケットの縁（panel 色）を消す（白い点の上に暗い輪を描かない）。
        途中の box は、入る辺の端から出る辺の端までを直線でつなぐ。そこは box の中
        （文字の上）なので、隠す区間 (始まり, 終わり) としてパケットを描かない。
        """
        key = tuple(path)
        hit = self._route_memo.get(key)
        if hit is not None:
            return hit
        pts, cum = [], []
        ends, starts, hide, through = [], [], [], []

        def add(p):
            if pts:
                q = pts[-1]
                if abs(p[0] - q[0]) <= 1e-9 and abs(p[1] - q[1]) <= 1e-9:
                    return
                cum.append(cum[-1] + math.hypot(p[0] - q[0], p[1] - q[1]))
            else:
                cum.append(0.0)
            pts.append(p)

        for k in range(len(path) - 1):
            a, b = path[k], path[k + 1]
            if (a, b) not in self._pair:
                raise ValueError(
                    f"{fn}: 経路の {self.names[a]!r} → {self.names[b]!r} を結ぶ辺がありません")
            ei, fwd = self._pair[(a, b)]
            epts = self._edge_pts[ei] if fwd else list(reversed(self._edge_pts[ei]))
            if k > 0:
                s_in = cum[-1]
                if self._shape[a] == "dot":
                    add(self._pos[a])           # 途中の点はノードの中心を通る
                    through.append((cum[-1], self._radius[a]))
                add(epts[0])
                if self._shape[a] == "box" and cum[-1] > s_in:
                    hide.append((s_in, cum[-1]))
            else:
                add(epts[0])
            starts.append(cum[-1])
            for p in epts[1:]:
                add(p)
            ends.append(cum[-1])
        self._routes.append((pts, cum, ends, starts, hide, through))
        rid = len(self._routes) - 1
        self._route_memo[key] = rid
        return rid

    def _node_ripple(self, t, i, rgba):
        if self._cluster[i]:
            return
        x, y = self._pos[i]
        if self._shape[i] == "box":
            hx, hy = self._half[i]
            self._ripples.append((t, 1, x, y, hx, hy, _BOX_RADIUS, rgba, _RIPPLE_WIDTH))
        else:
            self._ripples.append((t, 0, x, y, self._radius[i], 0.0, 0.0, rgba, _RIPPLE_WIDTH))

    # --- 出来事 ---

    def send(self, t, path, *, n=1, every=0.12, speed=700, color="fg", size=7, trail=0.25,
             fate="pass", label=None):
        """パケットを経路に沿って流す。各パケットが止まる秒（pass は着く秒）のリストを返す。

        path: 通るノードの名前の列（隣どうしが辺で結ばれていること。辺の向きは問わない）。
        n / every: n 個を every 秒おきに出す。speed: 弧長で px/秒（曲がった辺でも等速）。
        color: パケットの色（'fg' などパレットの名前か色の表記）。size: 直径 px。
        trail: 尾の長さ（秒。0 で尾なし）。
        fate: 'pass'（終点で消える・終点に波紋）/ ('stop', 名前)（そのノードの手前で止まって
          accent になり、その場に残る。同じ所に止まるパケットは、前のパケットとの中心の間隔を
          1.6 ×（2つの直径の平均）にして手前へ並べる。列が手前のノードに届いたら、そのノードを
          跨いでさらに手前の辺へ並べる）/ ('drop', 0.6)（最初の辺の 60% で止まり、薄れて消える）。
        label: 先頭のパケットに添える文字（30px 以上。パケットと一緒に端数の位置で動く）。
          ノード・ノードの文字・先に出た札に重なるコマでは隠れる（手前で薄れ、離れてから
          現れる）。置き場所は画面の上・進行方向の左右・下から、隠れる間などの最も少ない
          ものを札ごとに選んで保つ（途中のノードの先で1回、止まる所で1回だけ、札が一度
          隠れてから変わりうる。止まった札はパケットより後ろの左右に残る）。
        途中の box の中ではパケットを描かない（手前で薄れて消え、出る辺の端から現れる）。
        """
        fn = "send"
        t = _check_time(fn, "t", t)
        if not isinstance(path, (list, tuple)) or len(path) < 2:
            raise ValueError(f"{fn}: path は2つ以上のノードの名前の列で指定してください: {path!r}")
        idx = [self._node_index(fn, p) for p in path]
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= _MAX_PACKETS:
            raise ValueError(f"{fn}: n は 1〜{_MAX_PACKETS} の整数で指定してください: {n!r}")
        _require_number(fn, "every", every, 0, 3600)
        _require_number(fn, "speed", speed, 1, 100000)
        _require_number(fn, "size", size, 1, 200)
        _require_number(fn, "trail", trail, 0, 10)
        rgba = fk.color(fn, color, self._pal)
        if fate == "pass":
            kind, arg = 0, None
        elif isinstance(fate, (list, tuple)) and len(fate) == 2 and fate[0] == "stop":
            kind = 1
            j = None
            for k in range(1, len(idx)):
                if path[k] == fate[1]:
                    j = k
                    break
            if j is None:
                raise ValueError(
                    f"{fn}: fate=('stop', {fate[1]!r}) のノードが path の2番目以降にありません: {list(path)!r}")
            arg = j
        elif isinstance(fate, (list, tuple)) and len(fate) == 2 and fate[0] == "drop":
            kind = 2
            _require_number(fn, "fate の drop の割合", fate[1], 0, 1)
            if not 0 < fate[1] < 1:
                raise ValueError(f"{fn}: fate=('drop', 割合) の割合は 0 より大きく 1 より小さく: {fate[1]!r}")
            arg = float(fate[1])
        else:
            raise ValueError(
                f"{fn}: fate は 'pass' / ('stop', ノードの名前) / ('drop', 0〜1 の割合) の"
                f"いずれか: {fate!r}")
        if label is not None:
            if not isinstance(label, str) or not label.strip() or "\n" in label:
                raise ValueError(f"{fn}: label は1行の空でない文字列にしてください: {label!r}")
        rid = self._route(fn, idx)
        pts, cum, ends, starts = self._routes[rid][:4]
        total = cum[-1]
        accent = self._pal["accent"]
        # 全部を求めてから図へ足す（途中で ValueError になっても図を半端に変えない）
        packets = []
        ripples = []
        stop_next = dict(self._stop_next)
        out = []
        for k in range(n):
            t0 = t + k * float(every)
            if kind == 0:
                s_end = total
            elif kind == 1:
                key = (idx[arg], idx[arg - 1])
                margin = _STOP_MARGIN[0] * size + _STOP_MARGIN[1]
                prev = stop_next.get(key)
                if prev is None:
                    off = margin
                else:
                    # 中心の間隔 = 1.6 ×（前の直径と今の直径の平均）。大きさが違っても重ならない
                    off = prev[0] + _STOP_PITCH * (prev[1] + size) / 2.0
                s_end = ends[arg - 1] - off
                # 列が手前のノード（入る辺の端〜出る辺の端の区間）に掛かるなら、そのノードを
                # 跨いで手前の辺へ送る（ノードの上に乗せない）
                for j in range(arg - 1, 0, -1):
                    zs, ze = ends[j - 1], starts[j]
                    if zs - margin < s_end < ze + margin:
                        s_end = zs - margin
                if s_end < 0:
                    raise ValueError(
                        f"{fn}: {self.names[idx[arg]]!r} の手前に止まるパケットの列が経路に"
                        f"収まりません（{k + 1} 個目）。size を小さくするか、長い経路にしてください")
                stop_next[key] = (ends[arg - 1] - s_end, float(size))
            else:
                s_end = arg * ends[0]
            t_end = t0 + s_end / float(speed)
            packets.append((t0, rid, float(speed), s_end, t_end, kind, float(size),
                            float(trail), rgba, accent if kind == 1 else rgba))
            if kind == 0:
                ripples.append(("node", t_end))
            else:
                x, y = _point_at(pts, cum, s_end)
                ripples.append((t_end, 0, x, y, max(float(size), 6.0), 0.0, 0.0,
                                accent if kind == 1 else rgba, 2.0))
            out.append(t_end)
        if label is not None:
            self._sprite(label)          # 豆腐などで ValueError になりうる（足す前に作る）
        first = len(self._packets)
        self._packets.extend(packets)
        for rp in ripples:
            if rp[0] == "node":
                self._node_ripple(rp[1], idx[-1], rgba)
            else:
                self._ripples.append(rp)
        if label is not None:
            self._packet_labels.append((first, label))
        self._stop_next = stop_next
        fate_key = ("pass" if kind == 0 else
                    ["stop", path[arg]] if kind == 1 else ["drop", arg])
        # 鍵: 効かない値は入れない（n=1 の every）。数値の 7 と 7.0 は framekit.norm がそろえる
        self._log.append(["send", t, list(path), n, every if n > 1 else None, speed, list(rgba),
                          size, trail, fate_key, label])
        return out

    def _dijkstra(self, src, t0, hop):
        dist = {src: t0}
        pred = {src: None}
        heap = [(t0, src)]
        done = set()
        while heap:
            d, u = heapq.heappop(heap)
            if u in done:
                continue
            done.add(u)
            for v, ei in self._adj[u]:
                delay = self._edges[ei][2]
                nd = d + (hop if delay is None else delay)
                if v not in dist or nd < dist[v]:
                    dist[v] = nd
                    pred[v] = u
                    heapq.heappush(heap, (nd, v))
        return dist, pred

    def broadcast(self, t, root, *, hop=0.35, color="accent", packets=True, ripple=True):
        """root から全体へ一斉に配る。{名前: 届く秒} を返す。

        辺の delay（無ければ hop 秒）を辺の長さとして Dijkstra で届く秒を決める
        （辺の向きは問わない）。届いた順にノードの色が color へ変わり（0.25 秒）、
        ripple=True なら波紋が出る（点の塊の点には出さない）。packets=True なら、
        届いた辺ごとにパケットが「出た秒 → 届く秒」で走る。cut は経路を変えない（見た目だけ）。
        """
        fn = "broadcast"
        t = _check_time(fn, "t", t)
        src = self._node_index(fn, root)
        _require_number(fn, "hop", hop, 0, 3600)
        rgba = fk.color(fn, color, self._pal)
        for flag, name in ((packets, "packets"), (ripple, "ripple")):
            if not isinstance(flag, bool):
                raise TypeError(f"{fn}: {name} は True / False で指定してください: {flag!r}")
        return self._broadcast(fn, t, src, float(hop), rgba, packets, ripple,
                               log=["broadcast", t, root, hop, list(rgba), packets, ripple])

    def _broadcast(self, fn, t, src, hop, rgba, packets, ripple, log):
        dist, pred = self._dijkstra(src, t, hop)
        if any(e[2] is not None for e in self._edges):
            self._uses_delay = True
        order = sorted(dist, key=lambda u: (dist[u], u))
        for u in order:
            self._node_events.append((dist[u], len(self._node_events), u, tuple(rgba[:3]),
                                      None, None, _ARRIVE_DUR))
            if ripple:
                self._node_ripple(dist[u], u, rgba)
            name = self.names[u]
            if name not in self._arrival or dist[u] < self._arrival[name]:
                self._arrival[name] = dist[u]
        if packets:
            kids = {}
            for u in order:
                p = pred[u]
                if p is not None:
                    kids.setdefault(p, []).append(u)
            amounts = None if self._tree is None else self._tree.get("amounts")
            ref = None
            if amounts is not None:
                ref = max((amounts[v] for v in order if pred[v] is not None), default=None)
            for p in sorted(kids, key=lambda u: (dist[u], u)):
                ks = sorted(kids[p])
                plain = [v for v in ks if not self._cluster[v]]
                clus = [v for v in ks if self._cluster[v]]
                reps = [(v, [v]) for v in plain]
                if clus:
                    m = len(clus)
                    kk = min(_CLUSTER_PACKETS, m)
                    for j in range(kk):
                        share = clus[j * m // kk:(j + 1) * m // kk]
                        reps.append((share[len(share) // 2], share))
                for v, share in reps:
                    delay = dist[v] - dist[p]
                    if delay <= 0:
                        continue
                    rid = self._route(fn, [p, v])
                    L = self._routes[rid][1][-1]      # 1本の辺なので隠す区間・通り抜けは無い
                    if L <= 0:
                        continue
                    if amounts is not None and ref:
                        a = sum(amounts[x] for x in share)
                        d = max(_AMOUNT_D_MIN, _AMOUNT_D * math.sqrt(a / ref))
                    else:
                        d = _BROADCAST_D
                    self._packets.append((dist[p], rid, L / delay, L, dist[v], 0, d,
                                          min(0.25, delay * 0.6), rgba, rgba))
                    self._bcast_packets.add(len(self._packets) - 1)
        self._log.append(log)
        return {self.names[u]: dist[u] for u in order}

    def state(self, t, node, *, color=None, dim=None, mark=None, dur=0.25):
        """ノードの見た目を t から dur 秒かけて変える。

        color: 塗りの色（'fg' / 'accent' などパレットの名前か色の表記。α は使わない）。
        dim: 0〜1。灰色（パレットの dim）へ寄せる割合（0.6 で6割。0 で元へ戻す）。
        mark: 'x'（accent の ×）/ 'check'（fg の ✓）/ 'none'（印を外す）/ None（変えない）。
        box の印は右上の角に付く。文字（label）もノードと同じ色・灰色になる。
        """
        fn = "state"
        t = _check_time(fn, "t", t)
        i = self._node_index(fn, node)
        if color is None and dim is None and mark is None:
            raise ValueError(f"{fn}: color / dim / mark のどれかを指定してください")
        rgb = None if color is None else tuple(fk.color(fn, color, self._pal)[:3])
        if dim is not None:
            _require_number(fn, "dim", dim, 0, 1)
            dim = float(dim)
        if mark is not None:
            _require_choice(fn, "mark", mark, _MARKS)
        _require_number(fn, "dur", dur, 0, 3600)
        self._node_events.append((t, len(self._node_events), i, rgb, dim, mark, float(dur)))
        self._log.append(["state", t, node, None if rgb is None else list(rgb), dim, mark, dur])

    def cut(self, t, edge, *, dur=0.3):
        """辺 (a, b)（向きは問わない）を t から dur 秒かけて dim の破線にして薄れさせる"""
        fn = "cut"
        t = _check_time(fn, "t", t)
        if not isinstance(edge, (list, tuple)) or len(edge) != 2:
            raise ValueError(f"{fn}: 辺は (a, b) で指定してください: {edge!r}")
        a = self._node_index(fn, edge[0])
        b = self._node_index(fn, edge[1])
        if (a, b) not in self._pair:
            raise ValueError(f"{fn}: 辺 ({edge[0]!r}, {edge[1]!r}) はありません")
        _require_number(fn, "dur", dur, 0, 3600)
        ei = self._pair[(a, b)][0]
        if ei in self._cuts:
            raise ValueError(f"{fn}: 辺 ({edge[0]!r}, {edge[1]!r}) は既に cut しています")
        self._cuts[ei] = (t, float(dur))
        self._log.append(["cut", t, [edge[0], edge[1]], dur])

    # --- 組み立て ---

    def _end_time(self):
        end = 0.0
        for (t0, _r, _v, _s, t_end, kind, _d, trail, _c, _sc) in self._packets:
            if kind == 0:
                end = max(end, t_end + trail)
            elif kind == 1:
                end = max(end, t_end + _STOP_TINT)
            else:
                end = max(end, t_end + _DROP_FADE)
        for rp in self._ripples:
            end = max(end, rp[0] + _RIPPLE_DUR)
        for ev in self._node_events:
            end = max(end, ev[0] + ev[6])
        for t, d in self._cuts.values():
            end = max(end, t + d)
        return end

    def _check_live(self, duration):
        """同時に見えるパケットの数を数える（止まったものは最後まで見える）"""
        marks = []
        for j, (t0, _r, _v, _s, t_end, kind, _d, trail, _c, _sc) in enumerate(self._packets):
            if kind == 0:
                t1 = t_end + trail
            elif kind == 1:
                t1 = duration
            else:
                t1 = t_end + _DROP_FADE
            t1 = min(t1, duration)
            if t0 >= duration or t1 <= t0:
                continue
            b = j in self._bcast_packets
            marks.append((t0, 1, b))
            marks.append((t1, -1, b))
        marks.sort(key=lambda m: (m[0], m[1]))     # 同じ秒は消える方を先に数える
        live = peak = 0
        live_b = peak_b = 0
        for _t, d, b in marks:
            live += d
            live_b += d if b else 0
            if live > peak:
                peak, peak_b = live, live_b
        if peak > _MAX_PACKETS:
            hints = []
            if peak - peak_b > 0:
                hints.append("send なら n を減らすか、every を広げる")
            if peak_b > 0:
                hints.append(
                    "broadcast なら packets=False にする（色と波紋だけで広がりを見せる）か、"
                    "辺ごとの delay をばらけさせて飛ぶ時間帯をずらす。木なら flow_tree で作ると、"
                    "500 を超える葉の段へのパケットは親1つあたり 4 個に束ねられる")
            raise ValueError(
                f"{self._fn}: 同時に見えるパケットが {peak} 個あります（上限 {_MAX_PACKETS}。"
                f"そのうち broadcast が {peak_b} 個）。{'。'.join(hints)}")
        return peak

    def _place_packet_labels(self, duration, fps_frac=None):
        """パケットの札の置き場所（側）と隠す区間を、札ごとに決める（build のとき1回）。

        札は、ノード（枠・点）・ノードの文字・先に出た札に重ならせない。build が描くコマの
        時刻ごとに札の外接矩形（縁取り込み）を _plabel_at で求め、どれかに重なるコマは隠す
        （_plabel_blocks で隠す区間にし、描くときは区間の中で α を 0、区間から _PLABEL_FADE 秒で
        1 に戻す。重なる手前で薄れ、離れてから現れる）。採点と描画は同じコマの同じ矩形を見るので、
        描いたコマで札がノード・ノードの文字・ほかの札に重なることは無い。
        側の候補は _PLABEL_SIDES（画面の上・進行方向の左・右・画面の下）。頭が見えている間
        （止まったパケットは終わりまで、drop は薄れる間も）の、隠れる間（_PLABEL_HIDE_COST）・
        辺（扇は除く）が下を通る間・キャンバスの外へはみ出す量を、時間と頭の α で重みを付けて
        足し、最も少ないものにする（同じなら、側を変えないもの・候補の順）。文字の上に文字を
        載せる側は、その間ずっと隠れるので選ばれにくい。
        側はコマごとには選び直さない（札がぱたぱた入れ替わる）。変えてよいのは、経路の途中の
        ノードを越えて次の辺へ移る所で1回と、止まったパケットが止まる所で1回（止まった後は
        _PLABEL_REST_SIDES の、パケットより後ろの左右へ。中央のままだと止まる先のノードに掛かって
        隠れ続ける）だけで、変える所では札を必ず一度隠す（変わる所のコマを隠すコマに数えるので、
        前の側で薄れて消え、次の側で現れる。途中の box の中では札はもともと隠れている）。
        変えるたびに、採点へ _PLABEL_SWITCH_COST 秒ぶん隠れたのと同じ量を足す。
        札は出る順（先頭のパケットが出る秒の順）に決め、後の札は先の札が見えるコマを避ける。
        どのコマでも出せない札は警告する。
        fps_frac: build が描くコマの率（None は Project の fps）。
        戻り値: [(パケットの番号, 文字列, ((弧長, 側), …), 隠す区間)]（呼んだ順）。側の列は
        「この弧長から先はこの側」（最初は 0.0。次の辺の始まり・止まる弧長で変わりうる）。
        """
        if not self._packet_labels:
            return []
        if fps_frac is None:
            fps_frac = _fps_fraction(_resolve_fps(self._fn, None))
        fps = float(fps_frac)
        n_frames = fk.n_frames_for(duration, fps_frac)
        egrid, bbox, ngrid, nrect = self._hit_index()
        lgrid = _Grid()
        node_labels = [self._label_rect(i, self._label_mode[i]) for i in range(len(self.names))
                       if self._label[i] is not None and self._shape[i] != "box"]
        for j, lr in enumerate(node_labels):
            lgrid.add(j, *lr)
        W, H = float(self._W), float(self._H)
        ew = self._edge_width / 2.0
        ref_top, ref_bot = self._ref
        h = ref_bot - ref_top
        placed = []          # [(コマ → 札の矩形（見えるコマだけ）, 全体の外接矩形)]
        chosen = {}
        never = []
        self._plabel_costs = {}          # テスト・確認用: 札の番号 -> {側の列: 採点}
        self._plabel_shown = {}          # テスト・確認用: 札の番号 -> 札が見えるコマの数
        order = sorted(range(len(self._packet_labels)),
                       key=lambda k: (self._packets[self._packet_labels[k][0]][0], k))
        for k in order:
            pi, text = self._packet_labels[k]
            pk = self._packets[pi]
            t0, rid, _v, s_end, t_end, kind, d = pk[:7]
            pts, cum, _ends, starts, hide, _through = self._routes[rid]
            cw = float(self._sprites[text].meta["content_width"])
            r = d / 2.0
            area = (cw + 2.0 * _HALO) * (h + 2.0 * _HALO)
            t_last = t_end if kind == 0 else (duration if kind == 1 else t_end + _DROP_FADE)
            i_lo = max(0, int(math.floor(Fraction(t0) * fps_frac)))
            i_hi = min(n_frames - 1, int(math.ceil(Fraction(t_last) * fps_frac)))
            frames = []                       # (コマ, 秒, 弧長, 頭の α)。頭が見えるコマだけ
            for i in range(i_lo, i_hi + 1):
                t = float(Fraction(i) / fps_frac)          # build の draw と同じ秒
                a, s = _plabel_head(pk, hide, t)
                if a > 0:
                    frames.append((i, t, s, a))
            idx = [f[0] for f in frames]
            arcs = [f[2] for f in frames]
            # 止まった後のコマ（止まる所の前後に見えるコマがあるときだけ、後ろの左右へ移れる）
            q_rest = bisect.bisect_left(arcs, s_end) if kind == 1 else len(frames)
            rest_ok = 0 < q_rest < len(frames)
            # 側ごとに、コマごとの (矩形, 重なるか, 辺・外へのはみ出しの採点) を1回だけ求める
            per = {}
            for side in _PLABEL_SIDES + (_PLABEL_REST_SIDES if rest_ok else ()):
                rects, hard, soft = [], [], []
                for i, _t, s, _a in frames:
                    rc = _plabel_rect(*_plabel_at(side, pts, cum, s, r, cw, h), cw, h)
                    x0, y0, x1, y1 = rc
                    hit = (any(_rect_overlap(rc, nrect[j]) > 0
                               for j in ngrid.near(x0, y0, x1, y1))
                           or any(_rect_overlap(rc, node_labels[j]) > 0
                                  for j in lgrid.near(x0, y0, x1, y1))
                           or any(i in shown and _rect_overlap(rc, shown[i]) > 0
                                  for shown, ub in placed if _rect_overlap(rc, ub) > 0))
                    c = 4.0 * (area - _rect_overlap(rc, (0.0, 0.0, W, H)))
                    for j in egrid.near(x0 - ew, y0 - ew, x1 + ew, y1 + ew):
                        if self._edge_kind[j] != "edge":
                            continue
                        b = bbox[j]
                        if b[2] < x0 - ew or b[0] > x1 + ew or b[3] < y0 - ew or b[1] > y1 + ew:
                            continue
                        if _poly_hits_rect(self._edge_pts[j], x0 - ew, y0 - ew, x1 + ew, y1 + ew):
                            c += 0.5 * area
                    rects.append(rc)
                    hard.append(hit)
                    soft.append(c)
                per[side] = (rects, hard, soft)
            # 側の列の候補: 変えない4つ → 途中のノードの先（次の辺の始まり）で1回変えるもの。
            # 止まるパケットは、それぞれに「止まったら後ろの左右へ移る」ものも足す
            plans = [((0.0, side),) for side in _PLABEL_SIDES]
            for s_sw in starts[1:]:
                q = bisect.bisect_left(arcs, s_sw)
                if 0 < q < min(len(frames), q_rest):   # 変える所の前後に動いて見えるコマがある
                    plans.extend(((0.0, a), (s_sw, b)) for a in _PLABEL_SIDES
                                 for b in _PLABEL_SIDES if a != b)
            if rest_ok:
                plans.extend(plan + ((s_end, b),) for plan in list(plans)
                             for b in _PLABEL_REST_SIDES)
            best = None
            for plan in plans:
                sides = [_plabel_side(plan, s) for s in arcs]
                hard = [per[sd][1][q] for q, sd in enumerate(sides)]
                for s_sw, _sd in plan[1:]:
                    hard[bisect.bisect_left(arcs, s_sw)] = True    # 変わる所では札を一度隠す
                penalty = (len(plan) - 1) * _PLABEL_SWITCH_COST * _PLABEL_HIDE_COST * area
                blocks = _plabel_blocks(idx, hard, fps, kind == 0 and t_end < duration)
                alphas = [_plabel_alpha(blocks, f[1]) for f in frames]
                cost = penalty + sum(
                    f[3] * ((1.0 - al) * _PLABEL_HIDE_COST * area + al * per[sd][2][q])
                    for q, (f, al, sd) in enumerate(zip(frames, alphas, sides))) / fps
                self._plabel_costs.setdefault(k, {})[plan] = cost
                if best is None or cost < best[0] - 1e-9:
                    best = (cost, plan, blocks, sides, alphas)
            _cost, plan, blocks, sides, alphas = best
            shown = {f[0]: per[sd][0][q]
                     for q, (f, sd, al) in enumerate(zip(frames, sides, alphas)) if al > 0}
            if shown:
                vals = list(shown.values())
                ub = (min(q[0] for q in vals), min(q[1] for q in vals),
                      max(q[2] for q in vals), max(q[3] for q in vals))
                placed.append((shown, ub))
            elif frames:
                never.append(text)
            self._plabel_shown[k] = len(shown)
            chosen[k] = (pi, text, plan, tuple(blocks))
        if never:
            warnings.warn(
                f"{self._fn}: send の label が、ノード・ノードの文字・先に出た札に重ならない時が"
                f"短く、出ません: {', '.join(map(repr, never[:6]))}{' ほか' if len(never) > 6 else ''}"
                f"（speed を下げる・label_size を下げる・label を短くする・ノードを離す）")
        return [chosen[k] for k in range(len(self._packet_labels))]

    def _params(self):
        """鍵に入れる値（配置の結果は入れない。配置の手順を変えたら _FLOW_VER を上げる）"""
        lay = {"layout": self._layout}
        if self._layout != "given":
            lay["padding"] = _n(self._padding)
        if self._layout == "layered":
            lay["direction"] = self._direction
        any_label = any(self._label) or bool(self._packet_labels)
        params = {
            "size": [self._W, self._H],
            "layout": lay,
            "nodes": [[self.names[i], self._shape[i], self._label[i],
                       None if self._layout != "given" else list(self._given[i]),
                       self._layer_attr[i]] for i in range(len(self.names))],
            "edges": [[self.names[a], self.names[b], c] + ([d] if self._uses_delay else [])
                      for a, b, d, c in self._edges],
            "style": {"node_radius": _n(self._node_radius), "edge_width": _n(self._edge_width),
                      "curve": _n(self._curve)},
            # 使う名前の色だけ（muted は使わない。出来事で名前を使った色は、その出来事の
            # rgba として events に入る）
            "colors": {k: list(self._pal[k]) for k in _USED_COLORS},
            "events": self._log,
        }
        if any(s == "box" for s in self._shape):
            params["style"]["box"] = [_n(self._box[0]), _n(self._box[1])]
        if any_label:
            params["style"]["label_size"] = self._label_size
            params["style"]["label_pos"] = self._label_pos
        if self._tree is not None:
            params["tree"] = {"levels": list(self._tree["levels"]),
                              "amount": self._tree.get("amount")}
            if self._tree["cluster_level"] is not None:
                params["seed"] = self._seed
        return params

    def build(self, duration=None):
        """動画 Object にする（frames() と同じ qtrle・alpha つきの .mov）。

        duration: 秒。None は最後の出来事の終わり + 1.0 秒。
        戻り値の obj.figure: pos（{名前: (x, y)} キャンバスの px）・arrival
        （{名前: broadcast で最初に届く秒}）・size・duration・levels・amount。
        build の後に足した出来事は、この Object には入らない。
        """
        fn = "flow_graph.build"
        end = self._end_time()
        if duration is None:
            duration = end + 1.0
        else:
            _require_number(fn, "duration", duration, 0, None)
            if duration <= 0:
                raise ValueError(f"{fn}: duration は 0 より大きくしてください: {duration!r}")
            duration = float(duration)
        self._check_live(duration)
        fps = _resolve_fps(fn, None)
        fps_frac = _fps_fraction(fps)
        n_frames = fk.n_frames_for(duration, fps_frac)
        scene = self._snapshot(self._place_packet_labels(duration, fps_frac))
        holder = {}

        def draw(i):
            r = holder.get("r")
            if r is None:
                r = holder["r"] = _Renderer(scene)
            img = r.frame(float(Fraction(i) / fps_frac))
            if i == n_frames - 1:
                # 生成は 0 → 最後の順に描くので、最後のコマで作業領域（1080p で約 100MB）を
                # 手放す（Object は p.objects に残り続ける）。また呼ばれたら作り直す
                holder.pop("r", None)
            return img

        sprites = [self._sprites[k] for k in sorted(self._sprites)]
        text = (fk.text_meta(sprites, width=self._W, height=self._H) if sprites else None)
        info = {"pos": dict(self.pos), "arrival": dict(self._arrival),
                "size": (self._W, self._H), "duration": duration,
                "levels": self.levels, "amount": self.amount}
        return fk.build(self._fn, kind="flow_graph", ver=_FLOW_VER, params=self._params(),
                        draw=draw, n_frames=n_frames, size=(self._W, self._H), fps=fps,
                        fonts=sprites, text=text, info=info)

    def _snapshot(self, packet_labels=None):
        """描画に要るものの写し（build の後に足した出来事を混ぜない）。
        packet_labels は _place_packet_labels の結果（札ごとの側。None は build の既定の尺
        = 最後の出来事の終わり + 1 秒で選ぶ）"""
        if packet_labels is None:
            packet_labels = self._place_packet_labels(self._end_time() + 1.0)
        return {
            "fn": self._fn, "size": (self._W, self._H), "pal": dict(self._pal),
            "pos": list(self._pos), "radius": list(self._radius), "half": list(self._half),
            "shape": list(self._shape), "cluster": list(self._cluster),
            "label": list(self._label), "label_at": list(self._label_at),
            "plate": list(self._plate_rect),
            "sprites": dict(self._sprites), "ref": self._ref,
            "edges": list(self._edges), "edge_pts": list(self._edge_pts),
            "edge_kind": list(self._edge_kind), "edge_width": self._edge_width,
            "edge_fan_parent": list(self._edge_fan_parent),
            "routes": list(self._routes), "packets": list(self._packets),
            "packet_labels": list(packet_labels), "ripples": list(self._ripples),
            "node_events": list(self._node_events), "cuts": dict(self._cuts),
        }


# --- 描画（numpy。build の draw からだけ呼ばれる）---

def _over_f(np, df, rows, a, rgb):
    """float のストレートアルファの画素 df[rows]（0..255 の RGBA）へ、色 rgb を被覆率 a で over"""
    sa = a.astype(np.float32, copy=False)
    sub = df[rows]
    keep = sub[:, 3] * np.float32(1.0 / 255.0) * (np.float32(1.0) - sa)
    oa = sa + keep
    inv = np.float32(1.0) / np.maximum(oa, np.float32(1e-6))
    src = np.asarray(rgb, dtype=np.float32)
    sub[:, :3] = sub[:, :3] * (keep * inv)[:, None] + src[None, :] * (sa * inv)[:, None]
    sub[:, 3] = oa * np.float32(255.0)
    df[rows] = sub


def _blend(np, flat, gidx, a, rgb):
    """flat の gidx の画素へ、色 rgb を被覆率 a で over する（ストレートアルファの uint8）。

    flat は RGBA の uint8 の画面を uint32 の1次元に見たもの（buf.view(np.uint32).reshape(-1)）。
    画素を4バイトまとめて拾って書き戻すので、(H*W, 4) の行を拾うより約2倍速い。
    gidx は重複しないこと。a は 0..1（色の α を掛け込んだもの）。
    """
    if gidx.size == 0:
        return
    df = flat[gidx].view(np.uint8).reshape(-1, 4).astype(np.float32)
    _over_f(np, df, slice(None), a, rgb)
    out = np.empty((gidx.size, 4), np.uint8)
    out[...] = np.clip(df + np.float32(0.5), 0, 255)
    flat[gidx] = out.view(np.uint32).reshape(-1)


class _Union:
    """画素の重複を含む被覆率を、画素ごとに 1 − Π(1 − a) へまとめる道具。

    log(1 − a) を画面と同じ大きさの平面へ np.add.at で足し（重複は出てきた順に足すので
    決定的）、「最後に書いた番号」の印で重複の無い画素の並びを取り出す。bincount と
    flatnonzero で画面全体を舐めるより、点が画面に散らばったとき数倍速い（1080p・25 万件で
    約 6ms 対 15ms）。使い終わった画素は 0 に戻すので、平面は使い回せる。
    """

    def __init__(self, np, n_pix):
        self.np = np
        self.plane = np.zeros(n_pix, np.float64)
        self.stamp = np.zeros(n_pix, np.int64)
        self.ar = np.arange(0, dtype=np.int64)

    def __call__(self, idx, a):
        np = self.np
        if idx.size == 0:
            return idx, a
        if self.ar.size < idx.size:
            self.ar = np.arange(max(idx.size, 2 * self.ar.size), dtype=np.int64)
        ar = self.ar[:idx.size]
        w = np.log1p(-np.minimum(a.astype(np.float64), 0.999999))
        np.add.at(self.plane, idx, w)
        self.stamp[idx] = ar
        u = idx[self.stamp[idx] == ar]
        acc = self.plane[u]
        self.plane[u] = 0.0
        return u, (-np.expm1(acc)).astype(np.float32)


def _buckets(np, ext):
    """点ごとの窓の半径 ext（px）を 2 の冪の段に分けた、点の番号の並びのリスト。

    1回の距離計算の窓（正方形）は、その段の中の最大に合わせる。全部を最大の点に
    合わせると、size=200 のパケットが1つあるだけで小さな点の窓も 406×406 になる。
    """
    lvl = np.ceil(np.log2(np.maximum(ext, 1.0))).astype(np.int64)
    return [np.flatnonzero(lvl == v) for v in np.unique(lvl)]


def _window(np, cx, cy, P):
    """中心 (cx, cy) の点ごとに、半径 P の窓の画素の (左上の x, 左上の y, 中心までの距離, 一辺)"""
    S = 2 * P + 2
    ix = np.floor(cx).astype(np.int64) - P
    iy = np.floor(cy).astype(np.int64) - P
    off = np.arange(S, dtype=np.float64) + 0.5
    dx = ((ix - cx)[:, None] + off[None, :]).astype(np.float32)
    dy = ((iy - cy)[:, None] + off[None, :]).astype(np.float32)
    d = np.sqrt(dx[:, None, :] * dx[:, None, :] + dy[:, :, None] * dy[:, :, None])
    return ix, iy, d, S


def _window_index(np, ix, iy, S, W, H):
    """窓の画素の番号（画面の1次元）と、画面の中かどうか"""
    gx = ix[:, None] + np.arange(S)
    gy = iy[:, None] + np.arange(S)
    inside = ((gx >= 0) & (gx < W))[:, None, :] & ((gy >= 0) & (gy < H))[:, :, None]
    return gy[:, :, None] * W + gx[:, None, :], inside


def _disks(np, cx, cy, r, a, W, H, ring=None):
    """円（ring を渡すと幅 ring の輪）の被覆率。戻り値 (画素の番号, 被覆率)（重複あり）。

    cx, cy, r, a は同じ長さの配列（半径・α は点ごと）。被覆率は clip(r + 0.5 − d, 0, 1)
    （d は画素の中心から円の中心までの距離。端数の位置に連続に追従する）。
    窓は半径の段ごとに分けて計算する（_buckets）。
    """
    if cx.size == 0:
        return np.empty(0, np.int64), np.empty(0, np.float32)
    extra = (0.0 if ring is None else float(ring) / 2.0) + 1.0
    idxs, covs = [], []
    for sel in _buckets(np, r + extra):
        P = int(math.ceil(float(r[sel].max()) + extra))
        ix, iy, d, S = _window(np, cx[sel], cy[sel], P)
        rr = r[sel].astype(np.float32)[:, None, None]
        if ring is None:
            cov = np.clip(rr + np.float32(0.5) - d, 0.0, 1.0)
        else:
            cov = np.clip(np.float32(ring / 2.0 + 0.5) - np.abs(d - rr), 0.0, 1.0)
        cov *= a[sel].astype(np.float32)[:, None, None]
        flat, inside = _window_index(np, ix, iy, S, W, H)
        m = (cov > _EPS_A) & inside
        idxs.append(flat[m])
        covs.append(cov[m])
    if len(idxs) == 1:
        return idxs[0], covs[0]
    return np.concatenate(idxs), np.concatenate(covs)


def _patch_grid(np, x0, y0, x1, y1, W, H):
    """連続座標の矩形を覆う画素の範囲（キャンバスで切る）と画素の中心"""
    ix0, iy0 = max(0, int(math.floor(x0))), max(0, int(math.floor(y0)))
    ix1, iy1 = min(W, int(math.ceil(x1))), min(H, int(math.ceil(y1)))
    if ix0 >= ix1 or iy0 >= iy1:
        return None
    xs = np.arange(ix0, ix1, dtype=np.float32) + np.float32(0.5)
    ys = np.arange(iy0, iy1, dtype=np.float32)[:, None] + np.float32(0.5)
    return ix0, iy0, ix1, iy1, xs, ys


def _patch_out(np, grid, cov, W):
    ix0, iy0, ix1, iy1 = grid[:4]
    m = cov > _EPS_A
    yy, xx = np.nonzero(m)
    return ((yy + iy0) * W + (xx + ix0)).astype(np.int64), cov[m]


def _rbox_cov(np, cx, cy, hx, hy, rad, hw, W, H):
    """角丸矩形の枠（中心線は縁・幅 2·hw）の被覆率（framekit の角丸と同じ符号付き距離）"""
    ext = hw + 1.5
    g = _patch_grid(np, cx - hx - ext, cy - hy - ext, cx + hx + ext, cy + hy + ext, W, H)
    if g is None:
        return np.empty(0, np.int64), np.empty(0, np.float32)
    xs, ys = g[4], g[5]
    r = max(0.0, min(rad, hx, hy))
    qx = np.abs(xs - np.float32(cx)) - np.float32(hx - r)
    qy = np.abs(ys - np.float32(cy)) - np.float32(hy - r)
    mx, my = np.maximum(qx, 0.0), np.maximum(qy, 0.0)
    sd = np.sqrt(mx * mx + my * my) + np.minimum(np.maximum(qx, qy), 0.0) - np.float32(r)
    cov = np.clip(np.float32(hw + 0.5) - np.abs(sd), 0.0, 1.0)
    return _patch_out(np, g, cov, W)


def _rbox_fill_cov(np, cx, cy, hx, hy, rad, W, H):
    """角丸矩形の塗りの被覆率（辺が下を通る文字の板）"""
    g = _patch_grid(np, cx - hx - 1.5, cy - hy - 1.5, cx + hx + 1.5, cy + hy + 1.5, W, H)
    if g is None:
        return np.empty(0, np.int64), np.empty(0, np.float32)
    xs, ys = g[4], g[5]
    r = max(0.0, min(rad, hx, hy))
    qx = np.abs(xs - np.float32(cx)) - np.float32(hx - r)
    qy = np.abs(ys - np.float32(cy)) - np.float32(hy - r)
    mx, my = np.maximum(qx, 0.0), np.maximum(qy, 0.0)
    sd = np.sqrt(mx * mx + my * my) + np.minimum(np.maximum(qx, qy), 0.0) - np.float32(r)
    cov = np.clip(np.float32(0.5) - sd, 0.0, 1.0)
    return _patch_out(np, g, cov, W)


def _thin_lines_cov(np, segs, hw, W, H):
    """細い線分の束（扇の辺）の被覆率。重なりは max（同じ色の薄い線を何百本重ねても濃くならない）。

    segs: (N, 4) の配列（ax, ay, bx, by）。長い線分は _FAN_PIECE px 以下の小片に分け、
    小片ごとに同じ大きさの窓で、画素の中心から線分までの距離 d から被覆率
    clip(hw + 0.5 − d, 0, 1) を求めて、画面の平面へ np.maximum.at で入れる
    （framekit.polyline で扇を描くと、1080p の葉 3000 本で数秒〜10 秒かかった）。
    戻り値: (画素の番号, 被覆率)（重複なし）。
    """
    if segs.size == 0:
        return np.empty(0, np.int64), np.empty(0, np.float32)
    ax, ay, bx, by = (segs[:, k] for k in range(4))
    L = np.hypot(bx - ax, by - ay)
    k = np.maximum(1, np.ceil(L / _FAN_PIECE)).astype(np.int64)
    rep = np.repeat(np.arange(segs.shape[0]), k)
    j = np.arange(rep.size) - np.repeat(np.cumsum(k) - k, k)
    t0 = j / k[rep]
    t1 = (j + 1) / k[rep]
    dx, dy = (bx - ax)[rep], (by - ay)[rep]
    px0, py0 = ax[rep] + dx * t0, ay[rep] + dy * t0
    px1, py1 = ax[rep] + dx * t1, ay[rep] + dy * t1
    ext = hw + 1.0
    S = int(math.ceil(_FAN_PIECE + 2.0 * ext)) + 2
    plane = np.zeros(W * H, np.float32)
    ar = np.arange(S, dtype=np.float64) + 0.5
    for c0 in range(0, rep.size, 4096):
        sl = slice(c0, c0 + 4096)
        x0, y0, x1, y1 = px0[sl], py0[sl], px1[sl], py1[sl]
        ix = np.floor(np.minimum(x0, x1) - ext).astype(np.int64)
        iy = np.floor(np.minimum(y0, y1) - ext).astype(np.int64)
        sx, sy = x1 - x0, y1 - y0
        L2 = np.maximum(sx * sx + sy * sy, 1e-12)
        rx = ((ix - x0)[:, None] + ar[None, :]).astype(np.float32)[:, None, :]
        ry = ((iy - y0)[:, None] + ar[None, :]).astype(np.float32)[:, :, None]
        fsx = sx.astype(np.float32)[:, None, None]
        fsy = sy.astype(np.float32)[:, None, None]
        tt = np.clip((rx * fsx + ry * fsy) / L2.astype(np.float32)[:, None, None], 0.0, 1.0)
        qx = rx - tt * fsx
        qy = ry - tt * fsy
        cov = np.clip(np.float32(hw + 0.5) - np.sqrt(qx * qx + qy * qy), 0.0, 1.0)
        flat, inside = _window_index(np, ix, iy, S, W, H)
        m = (cov > _EPS_A) & inside
        np.maximum.at(plane, flat[m], cov[m])
    u = np.flatnonzero(plane)
    return u, plane[u]


def _segs_cov(np, segs, hw, W, H):
    """線分の束（丸い端・継ぎ目）の被覆率。小さな印（× / ✓）用"""
    xs_all = [p for s in segs for p in (s[0][0], s[1][0])]
    ys_all = [p for s in segs for p in (s[0][1], s[1][1])]
    ext = hw + 1.5
    g = _patch_grid(np, min(xs_all) - ext, min(ys_all) - ext, max(xs_all) + ext,
                    max(ys_all) + ext, W, H)
    if g is None:
        return np.empty(0, np.int64), np.empty(0, np.float32)
    xs, ys = g[4], g[5]
    best = None
    for (ax, ay), (bx, by) in segs:
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        px = xs - np.float32(ax)
        py = ys - np.float32(ay)
        if L2 < 1e-12:
            d2 = px * px + py * py
        else:
            tt = np.clip(px * np.float32(dx / L2) + py * np.float32(dy / L2), 0.0, 1.0)
            qx = px - tt * np.float32(dx)
            qy = py - tt * np.float32(dy)
            d2 = qx * qx + qy * qy
        best = d2 if best is None else np.minimum(best, d2)
    cov = np.clip(np.float32(hw + 0.5) - np.sqrt(best), 0.0, 1.0)
    return _patch_out(np, g, cov, W)


class _Renderer:
    """build() の draw(i) の中身。辺の層・ノードの層を使い回し、動くものを毎コマ重ねる"""

    def __init__(self, sc):
        self.np = np = fk.need(sc["fn"]).np
        self.sc = sc
        self.W, self.H = sc["size"]
        pal = sc["pal"]
        self.pal = pal
        n = len(sc["pos"])
        self.n = n
        self.px = np.array([p[0] for p in sc["pos"]], np.float64)
        self.py = np.array([p[1] for p in sc["pos"]], np.float64)
        self.rad = np.array(sc["radius"], np.float64)
        self.is_box = np.array([s == "box" for s in sc["shape"]], bool)
        self.box_idx = [int(i) for i in np.flatnonzero(self.is_box)]
        self.union = _Union(np, self.W * self.H)
        self.posmap = np.zeros(self.W * self.H, np.int64)
        self._build_node_groups()
        self._build_labels()
        self._build_packets()
        self._build_ripples()
        # 辺
        cuts = sc["cuts"]
        self.cut_idx = sorted(cuts)
        self.cut_t = np.array([cuts[e][0] for e in self.cut_idx], np.float64)
        self.cut_d = np.array([cuts[e][1] for e in self.cut_idx], np.float64)
        self._edge_u8 = None
        self._cut_cov = {}
        self._edge_memo = OrderedDict()
        self._node_memo = OrderedDict()

    # --- ノードの状態 ---

    def _build_node_groups(self):
        """ノードの出来事を「チャンネルごとの鎖」にし、鎖が同じノードを1つの組にまとめる。

        チャンネル: 色（3）・dim（1）・×（1）・✓（1）。各出来事は、その時点の値から
        目標の値へ dur 秒で滑らかに変わる（後の出来事が始まったら、その時点の値から引き継ぐ）。
        同じ鎖のノード（flow_tree の同じ段など）はいつも同じ見た目なので、組ごとに1回だけ
        値を求め、点の被覆率も組ごとに1回だけ作って使い回す（broadcast で色が変わる間、
        ノードの層を毎コマ描き直しても、点を数え直さない）。
        """
        np = self.np
        n = self.n
        fg = tuple(float(v) for v in self.pal["fg"][:3])
        chans = {"c": [[] for _ in range(n)], "d": [[] for _ in range(n)],
                 "x": [[] for _ in range(n)], "k": [[] for _ in range(n)]}
        for (t, _seq, i, rgb, dim, mark, dur) in sorted(self.sc["node_events"],
                                                        key=lambda e: (e[0], e[1])):
            if rgb is not None:
                chans["c"][i].append((t, dur, tuple(float(v) for v in rgb)))
            if dim is not None:
                chans["d"][i].append((t, dur, (dim,)))
            if mark is not None:
                chans["x"][i].append((t, dur, (1.0 if mark == "x" else 0.0,)))
                chans["k"][i].append((t, dur, (1.0 if mark == "check" else 0.0,)))
        # 組: 形（点か box か）と4つの鎖が同じノード。番号は最初に現れたノードの順
        cls_of = {}
        cls = []
        reps = []
        for i in range(n):
            key = (bool(self.is_box[i]), tuple(chans["c"][i]), tuple(chans["d"][i]),
                   tuple(chans["x"][i]), tuple(chans["k"][i]))
            c = cls_of.get(key)
            if c is None:
                c = cls_of[key] = len(reps)
                reps.append(i)
            cls.append(c)
        self.cls = np.array(cls, np.int64)
        self.n_cls = len(reps)
        self.cls_dots = [np.flatnonzero((self.cls == c) & ~self.is_box)
                         for c in range(self.n_cls)]
        self._cls_cov = [None] * self.n_cls
        bases = {"c": fg, "d": (0.0,), "x": (0.0,), "k": (0.0,)}
        self.groups = []
        C = self.n_cls
        for ch in ("c", "d", "x", "k"):
            base = bases[ch]
            c = len(base)
            K = max((len(chans[ch][i]) for i in reps), default=0)
            T = np.full((C, max(K, 1)), np.inf)
            D = np.zeros((C, max(K, 1)))
            F = np.zeros((C, max(K, 1), c))
            TO = np.zeros((C, max(K, 1), c))
            for ci, i in enumerate(reps):
                prev = []
                for k, (t, dur, target) in enumerate(chans[ch][i]):
                    frm = self._chain_value(prev, base, t)
                    T[ci, k], D[ci, k] = t, dur
                    F[ci, k] = frm
                    TO[ci, k] = target
                    prev.append((t, dur, frm, target))
            self.groups.append((np.array(base, np.float64), T, D, F, TO, K))

    @staticmethod
    def _chain_value(evs, base, t):
        v = tuple(base)
        for te, d, frm, to in evs:
            if t >= te:
                p = 1.0 if d <= 0 else min(1.0, (t - te) / d)
                e = _smooth(p)
                v = tuple(f + (b - f) * e for f, b in zip(frm, to))
        return v

    def node_values(self, t):
        """時刻 t の組ごとの値（組の数×6 の float32: 色 RGB・dim・×・✓）"""
        np = self.np
        cols = []
        for base, T, D, F, TO, K in self.groups:
            v = np.broadcast_to(base, (self.n_cls, base.size)).copy()
            for k in range(K):
                Tk = T[:, k]
                act = t >= Tk
                if not act.any():
                    break
                Dk = D[:, k]
                with np.errstate(invalid="ignore", divide="ignore"):
                    p = np.where(Dk > 0, (t - Tk) / np.where(Dk > 0, Dk, 1.0), 1.0)
                p = np.clip(p, 0.0, 1.0)
                e = p * p * (3.0 - 2.0 * p)
                val = F[:, k] + (TO[:, k] - F[:, k]) * e[:, None]
                v = np.where(act[:, None], val, v)
            cols.append(v)
        return np.concatenate(cols, axis=1).astype(np.float32)

    def _union_blend(self, flat, idx, a, rgb):
        """同じ色の被覆率の集まり（画素の重複あり）をまとめてから over する"""
        if idx.size:
            gidx, A = self.union(idx, a)
            _blend(self.np, flat, gidx, A, rgb)

    def _class_cov(self, c):
        """組 c の点の被覆率（重なりは 1 − Π(1 − a) でまとめたもの。1回だけ作る）"""
        hit = self._cls_cov[c]
        if hit is None:
            np = self.np
            sel = self.cls_dots[c]
            if sel.size == 0:
                hit = (np.empty(0, np.int64), np.empty(0, np.float32))
            else:
                idx, a = _disks(np, self.px[sel], self.py[sel], self.rad[sel],
                                np.ones(sel.size), self.W, self.H)
                hit = self.union(idx, a)
            self._cls_cov[c] = hit
        return hit

    # --- 文字 ---

    def _build_labels(self):
        """文字の Sprite を「縁取り（panel）」と「字（ノードの色）」の被覆率に分ける。

        Sprite は白い字に黒い縁取りで描いてあるので、α が縁取りと字の和、
        R×α が字の被覆率になる（縁は字の色を引き継がないので、ノードの色で塗り直せる）。
        """
        np = self.np
        self.masks = {}
        for text, sp in self.sc["sprites"].items():
            rgba = sp.rgba.astype(np.float32) / np.float32(255.0)
            halo = rgba[..., 3]
            fill = rgba[..., 0] * rgba[..., 3]
            self.masks[text] = (sp.base, halo, fill)

    def _blit_mask(self, flat, text, x_left, base_y, rgb, alpha, part):
        """文字の被覆率を (行頭の x, ベースラインの y) に重ねる。

        位置は端数でもよい（動くパケットの文字）。端数は被覆率を双線形にずらして重ねる
        （framekit.blit の warpAffine と同じ考え方。整数 px で動かすと、遅いパケットに対して
        文字が 0px / 1px と段になって動く）。整数の位置ではずらさない（ノードの文字）。
        """
        np = self.np
        base, halo, fill = self.masks[text]
        m = halo if part == "halo" else fill
        fx0 = float(x_left) - base[0]
        fy0 = float(base_y) - base[1]
        x0, y0 = int(math.floor(fx0)), int(math.floor(fy0))
        fx, fy = fx0 - x0, fy0 - y0
        if fx > 1e-6 or fy > 1e-6:
            h, w = m.shape
            s = np.zeros((h + 1, w + 1), np.float32)
            s[:h, :w] += m * np.float32((1.0 - fx) * (1.0 - fy))
            s[:h, 1:] += m * np.float32(fx * (1.0 - fy))
            s[1:, :w] += m * np.float32((1.0 - fx) * fy)
            s[1:, 1:] += m * np.float32(fx * fy)
            m = s
        h, w = m.shape
        ix0, iy0 = max(0, x0), max(0, y0)
        ix1, iy1 = min(self.W, x0 + w), min(self.H, y0 + h)
        if ix0 >= ix1 or iy0 >= iy1:
            return
        sub = m[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] * np.float32(alpha)
        yy, xx = np.nonzero(sub > _EPS_A)
        if yy.size == 0:
            return
        gidx = ((yy + iy0) * self.W + (xx + ix0)).astype(np.int64)
        _blend(np, flat, gidx, sub[yy, xx], rgb)

    # --- 辺の層 ---

    def _poly(self, dst, pts, rgba, width, dash=None):
        fk.polyline(dst, pts, rgba, width, dash=dash)

    def _draw_edges_float(self):
        """cut の無い辺を描いた float の層（_edge_base_u8 が1回だけ呼ぶ。持ち続けない）"""
        dst = fk.canvas(self.W, self.H)
        cut = set(self.cut_idx)
        line = self.pal["line"]
        w = self.sc["edge_width"]
        fan_rgba = (line[0], line[1], line[2], int(round(line[3] * _FAN_ALPHA)))
        fans = OrderedDict()       # 親 -> 親の側から子の側へ向けた辺の折れ線
        for ei, pts in enumerate(self.sc["edge_pts"]):
            if ei in cut or len(pts) < 2 or pts[0] == pts[-1]:
                continue
            parent = self.sc["edge_fan_parent"][ei]
            if parent is not None:
                a = self.sc["edges"][ei][0]
                fans.setdefault(parent, []).append(pts if parent == a else pts[::-1])
            else:
                self._poly(dst, pts, line, w)
        # 扇: 全部の扇の辺の線分（親の中心 → 辺の始まり → 辺の折れ線）の被覆率を max で
        # 1枚にまとめて1回で塗る（親の近くで何百本も重なっても濃くならない）。各辺は描く線と
        # パケットの道が同じ折れ線（曲がった辺でもパケットが外れない）。親の中心から
        # 辺の始まりまでは、上に親の点が乗るので見えない。
        if fans:
            np = self.np
            segs = []
            for parent, polys in fans.items():
                c = self.sc["pos"][parent]
                for q in polys:
                    prev = c
                    for p in q:
                        segs.append((prev[0], prev[1], p[0], p[1]))
                        prev = p
            idx, cov = _thin_lines_cov(np, np.array(segs, np.float64), _FAN_WIDTH / 2.0,
                                       self.W, self.H)
            a = cov * np.float32(fan_rgba[3] / 255.0)
            col = np.array([fan_rgba[0] / 255.0, fan_rgba[1] / 255.0, fan_rgba[2] / 255.0, 1.0],
                           np.float32)
            flat = dst.reshape(-1, 4)
            flat[idx] = flat[idx] * (np.float32(1.0) - a[:, None]) + a[:, None] * col[None, :]
        return dst

    def edge_progress(self, t):
        """cut の進み具合（1/256 に丸める。丸めた値で描くので、同じ鍵なら同じ絵）"""
        np = self.np
        if not self.cut_idx:
            return ()
        with np.errstate(invalid="ignore", divide="ignore"):
            p = np.where(self.cut_d > 0, (t - self.cut_t) / np.where(self.cut_d > 0, self.cut_d, 1.0),
                         np.where(t >= self.cut_t, 1.0, 0.0))
        p = np.clip(p, 0.0, 1.0)
        p = p * p * (3.0 - 2.0 * p)
        return tuple(int(v) for v in np.rint(p * 256))

    def _edge_base_u8(self):
        """cut の無い辺の層（uint8。1回だけ to_rgba8 する）"""
        if self._edge_u8 is None:
            layer = fk.to_rgba8(self._draw_edges_float())     # float の層は持ち続けない
            layer.flags.writeable = False
            self._edge_u8 = layer
        return self._edge_u8

    def _stroke_cov(self, pts, width, dash=None):
        """1本の辺の被覆率を framekit の polyline で求めて、(画素の番号, 被覆率) にする。

        辺の外接矩形だけのキャンバスへ白で描き、α を被覆率として取り出す（1回だけ）。
        """
        np = self.np
        pad = width / 2.0 + 3.0
        xs = [q[0] for q in pts]
        ys = [q[1] for q in pts]
        x0 = max(0, int(math.floor(min(xs) - pad)))
        y0 = max(0, int(math.floor(min(ys) - pad)))
        x1 = min(self.W, int(math.ceil(max(xs) + pad)))
        y1 = min(self.H, int(math.ceil(max(ys) + pad)))
        if x0 >= x1 or y0 >= y1:
            return np.empty(0, np.int64), np.empty(0, np.float32)
        cv = fk.canvas(x1 - x0, y1 - y0)
        fk.polyline(cv, [(q[0] - x0, q[1] - y0) for q in pts], (255, 255, 255, 255), width,
                    dash=dash)
        a = cv[..., 3]
        yy, xx = np.nonzero(a > _EPS_A)
        return ((yy + y0) * self.W + (xx + x0)).astype(np.int64), a[yy, xx].astype(np.float32)

    def edge_layer(self, key):
        """辺の層（uint8）。cut する辺だけを、進み具合 key に合わせて毎回重ねる。

        cut する辺の実線と破線の被覆率は1回だけ求めておき、ここでは色と α を掛けて
        重ねるだけ（float の全面キャンバスを毎コマ to_rgba8 しない）。
        """
        hit = self._edge_memo.get(key)
        if hit is not None:
            self._edge_memo.move_to_end(key)
            return hit
        base = self._edge_base_u8()
        if not self.cut_idx:
            layer = base
        else:
            np = self.np
            layer = base.copy()
            flat = layer.view(np.uint32).reshape(-1)
            line = self.pal["line"]
            dimc = self.pal["dim"]
            w = self.sc["edge_width"]
            for ei, q256 in zip(self.cut_idx, key):
                pts = self.sc["edge_pts"][ei]
                if len(pts) < 2 or pts[0] == pts[-1]:
                    continue
                q = q256 / 256.0
                fan = self.sc["edge_kind"][ei] == "fan"
                ww = _FAN_WIDTH if fan else w
                covs = self._cut_cov.get(ei)
                if covs is None:
                    covs = self._cut_cov[ei] = (
                        self._stroke_cov(pts, ww),
                        self._stroke_cov(pts, ww, dash=(_DASH[0] * ww, _DASH[1] * ww)))
                (si, sa), (di, da) = covs
                if q < 1.0:
                    a0 = line[3] / 255.0 * (_FAN_ALPHA if fan else 1.0) * (1.0 - q)
                    _blend(np, flat, si, sa * np.float32(a0), line[:3])
                if q > 0.0:
                    a1 = dimc[3] / 255.0 * _CUT_ALPHA * q
                    _blend(np, flat, di, da * np.float32(a1), dimc[:3])
            layer.flags.writeable = False
        self._edge_memo[key] = layer
        while len(self._edge_memo) > _STATIC_MEMO:
            self._edge_memo.popitem(last=False)
        return layer

    # --- ノードの層 ---

    def node_layer(self, ekey, cvals):
        key = (ekey, cvals.tobytes())
        hit = self._node_memo.get(key)
        if hit is not None:
            self._node_memo.move_to_end(key)
            return hit
        np = self.np
        buf = self.edge_layer(ekey).copy()
        flat = buf.view(np.uint32).reshape(-1)
        W, H = self.W, self.H
        dimc = np.array(self.pal["dim"][:3], np.float32)
        rgb = cvals[:, :3]
        dim = cvals[:, 3:4]
        col = rgb + (dimc[None, :] - rgb) * dim
        col8 = np.clip(np.floor(col + 0.5), 0, 255).astype(np.int64)
        # 点（組ごと。同じ色の組どうしの over は 1 − Π(1 − a) と同じなので、重なっても正しい）
        for c in range(self.n_cls):
            if self.cls_dots[c].size:
                gidx, a = self._class_cov(c)
                _blend(np, flat, gidx, a, tuple(int(v) for v in col8[c]))
        # box の枠
        half = self.sc["half"]
        for i in self.box_idx:
            hx, hy = half[i]
            idx, a = _rbox_cov(np, self.px[i], self.py[i], hx, hy, _BOX_RADIUS,
                               _BOX_STROKE / 2.0, W, H)
            _blend(np, flat, idx, a, tuple(int(v) for v in col8[self.cls[i]]))
        # 文字（板 → 縁取り → 字）。板は辺が下を通る文字だけ（_refine_labels）
        panel = self.pal["panel"]
        for i, text in enumerate(self.sc["label"]):
            if text is None:
                continue
            x_left, base_y = self.sc["label_at"][i]
            plate = self.sc["plate"][i]
            if plate is not None and panel[3] > 0:
                idx, a = _rbox_fill_cov(np, *plate, W, H)
                _blend(np, flat, idx, a * np.float32(panel[3] / 255.0), panel[:3])
            if not self.is_box[i]:
                self._blit_mask(flat, text, x_left, base_y, panel[:3], panel[3] / 255.0, "halo")
            self._blit_mask(flat, text, x_left, base_y,
                            tuple(int(v) for v in col8[self.cls[i]]), 1.0, "fill")
        # 印
        marked = np.flatnonzero((cvals[:, 4] > _EPS_A) | (cvals[:, 5] > _EPS_A))
        if marked.size:
            for i in np.flatnonzero(np.isin(self.cls, marked)):
                c = self.cls[i]
                self._mark(flat, int(i), float(cvals[c, 4]), float(cvals[c, 5]))
        buf.flags.writeable = False
        self._node_memo[key] = buf
        while len(self._node_memo) > _STATIC_MEMO:
            self._node_memo.popitem(last=False)
        return buf

    def _mark(self, flat, i, mx, mk):
        np = self.np
        if self.is_box[i]:
            hx, hy = self.sc["half"][i]
            cx, cy = self.px[i] + hx, self.py[i] - hy
            s0 = max(9.0, 0.42 * hy)
        else:
            cx, cy = self.px[i], self.py[i]
            s0 = max(9.0, 1.4 * self.rad[i])
        for amount, kind in ((mx, "x"), (mk, "check")):
            if amount <= _EPS_A:
                continue
            s = s0 * (0.75 + 0.25 * amount)
            hw = max(1.6, 0.16 * s0)
            if kind == "x":
                segs = [((cx - s, cy - s), (cx + s, cy + s)), ((cx - s, cy + s), (cx + s, cy - s))]
                rgba = self.pal["accent"]
            else:
                p0 = (cx - 0.85 * s, cy + 0.05 * s)
                p1 = (cx - 0.25 * s, cy + 0.65 * s)
                p2 = (cx + 0.9 * s, cy - 0.7 * s)
                segs = [(p0, p1), (p1, p2)]
                rgba = self.pal["fg"]
            # 下に panel の縁を敷いて、点や線の上でも読めるようにする
            idx, a = _segs_cov(np, segs, hw + 2.0, self.W, self.H)
            panel = self.pal["panel"]
            _blend(np, flat, idx, a * np.float32(amount * panel[3] / 255.0), panel[:3])
            idx, a = _segs_cov(np, segs, hw, self.W, self.H)
            _blend(np, flat, idx, a * np.float32(amount * rgba[3] / 255.0), rgba[:3])

    # --- 動くもの ---

    def _build_packets(self):
        """道を1本の弧長の軸に並べ（道ごとに _route_gap だけ空ける）、パケットを配列にする。

        隠す区間（途中の box の中）と通り抜ける点（途中の dot）も同じ軸の弧長で持つ。
        道どうしの隙間は、薄れる距離（直径）と縁を消す距離より広くとるので、隣の道の
        区間がこの道の点に効くことは無い。
        """
        np = self.np
        routes = self.sc["routes"]
        pk = self.sc["packets"]
        max_d = max((p[6] for p in pk), default=0.0)
        max_r = max((r for rt in routes for _s, r in rt[5]), default=0.0)
        gap = 16.0 + 2.0 * (max_d + max_r + _PACKET_HALO + 2.0)
        xs, ys, cs, bases = [], [], [], []
        hs, he, tc, tr = [], [], [], []
        off = 0.0
        for pts, cum, _ends, _starts, hide, through in routes:
            bases.append(off)
            xs.extend(p[0] for p in pts)
            ys.extend(p[1] for p in pts)
            cs.extend(off + c for c in cum)
            for a, b in hide:
                hs.append(off + a)
                he.append(off + b)
            for s, r in through:
                tc.append(off + s)
                tr.append(r)
            off += cum[-1] + gap
        self.RX = np.array(xs, np.float64)
        self.RY = np.array(ys, np.float64)
        self.RC = np.array(cs, np.float64)
        self.HS = np.array(hs, np.float64)
        self.HE = np.array(he, np.float64)
        self.TC = np.array(tc, np.float64)
        self.TRN = np.array(tr, np.float64)
        self.P = len(pk)
        if not pk:
            return
        self.T0 = np.array([p[0] for p in pk], np.float64)
        self.PB = np.array([bases[p[1]] for p in pk], np.float64)
        self.V = np.array([p[2] for p in pk], np.float64)
        self.SE = np.array([p[3] for p in pk], np.float64)
        self.TE = np.array([p[4] for p in pk], np.float64)
        self.KIND = np.array([p[5] for p in pk], np.int64)
        self.D = np.array([p[6] for p in pk], np.float64)
        self.TR = np.array([p[7] for p in pk], np.float64)
        self.C = np.array([p[8] for p in pk], np.float64)
        self.SC = np.array([p[9] for p in pk], np.float64)

    def _build_ripples(self):
        np = self.np
        rp = self.sc["ripples"]
        self.RN = len(rp)
        if not rp:
            return
        self.R_T0 = np.array([r[0] for r in rp], np.float64)
        self.R_KIND = np.array([r[1] for r in rp], np.int64)
        self.R_X = np.array([r[2] for r in rp], np.float64)
        self.R_Y = np.array([r[3] for r in rp], np.float64)
        self.R_A = np.array([r[4] for r in rp], np.float64)
        self.R_B = np.array([r[5] for r in rp], np.float64)
        self.R_RAD = np.array([r[6] for r in rp], np.float64)
        self.R_C = np.array([r[7] for r in rp], np.float64)
        self.R_W = np.array([r[8] for r in rp], np.float64)

    def _xy(self, s_glob):
        np = self.np
        return np.interp(s_glob, self.RC, self.RX), np.interp(s_glob, self.RC, self.RY)

    def _route_factors(self, sg, D, Rdot):
        """道の上の位置 sg（全体の弧長）にある点の (見える割合, 縁の割合)。

        見える割合: 隠す区間（途中の box の中）では 0。区間の手前・後ろ D px（パケットの
        直径）で 0 → 1（box へ沈むように消え、出る辺の端から現れる）。
        縁の割合: 通り抜ける点（途中の dot）に点が重なる間は 0、離れて縁も触れなくなる所で 1
        （白いノードの上に暗い縁を描かない）。Rdot は点ごとの半径（尾の点は小さい）。
        """
        np = self.np
        vis = np.ones(sg.shape)
        if self.HS.size:
            n = self.HS.size
            k = np.searchsorted(self.HS, sg, side="right") - 1      # 始まりが sg 以下の最後の区間
            kc = np.clip(k, 0, n - 1)
            past = sg - self.HE[kc]                                  # 負なら区間の中
            inside = (k >= 0) & (past < 0)
            after = np.where((k >= 0) & (past >= 0), past, np.inf)
            k2 = np.clip(k + 1, 0, n - 1)
            before = np.where(k + 1 < n, self.HS[k2] - sg, np.inf)
            dist = np.minimum(after, before)
            vis = np.where(inside, 0.0, np.clip(dist / np.maximum(D, 1.0), 0.0, 1.0))
        halo = np.ones(sg.shape)
        if self.TC.size:
            n = self.TC.size
            k = np.searchsorted(self.TC, sg)
            best = np.full(sg.shape, np.inf)
            for kk in (k - 1, k):
                ok = (kk >= 0) & (kk < n)
                kc = np.clip(kk, 0, n - 1)
                best = np.where(ok, np.minimum(best, np.abs(sg - self.TC[kc]) - self.TRN[kc]), best)
            halo = np.clip((best - Rdot) / (_PACKET_HALO + 1.0), 0.0, 1.0)
        return vis, halo

    def packet_state(self, t):
        """時刻 t の点（頭と尾）。

        戻り値: (X, Y, 半径, α, rgb, 頭が見えるか, 頭の x, 頭の y, 頭の α の倍率, 色,
        縁の α)。点の配列（X〜rgb と縁の α）は見えない点（α が 0）を除いたもの。
        頭の x・y・α の倍率・色はパケットごとの配列（文字用）。
        """
        np = self.np
        if not self.P:
            return None
        dt = t - self.T0
        alive = dt >= 0
        k_pass, k_stop, k_drop = self.KIND == 0, self.KIND == 1, self.KIND == 2
        head = alive & ((k_pass & (t < self.TE)) | k_stop | (k_drop & (t < self.TE + _DROP_FADE)))
        s = np.clip(dt * self.V, 0.0, self.SE)
        dfade = np.where(k_drop & (t > self.TE),
                         np.clip(1.0 - (t - self.TE) / _DROP_FADE, 0.0, 1.0), 1.0)
        mix = np.where(k_stop & (t > self.TE),
                       np.clip((t - self.TE) / _STOP_TINT, 0.0, 1.0), 0.0)
        mix = mix * mix * (3.0 - 2.0 * mix)
        col = self.C + (self.SC - self.C) * mix[:, None]
        ca = col[:, 3] / 255.0
        sg = self.PB + s
        hx, hy = self._xy(sg)
        R0 = self.D / 2.0
        vis, hal = self._route_factors(sg, self.D, R0)
        fade = dfade * vis
        parts = [(head, hx, hy, R0, fade * ca, hal)]
        for k, (scale, al) in enumerate(_TRAIL, 1):
            tk = t - self.TR * (k / 3.0)
            m = (alive & (self.TR > 0) & (tk >= self.T0) & (tk < self.TE)
                 & (~k_drop | (t < self.TE + _DROP_FADE)))
            if not m.any():
                continue
            sgk = self.PB + np.clip((tk - self.T0) * self.V, 0.0, self.SE)
            x, y = self._xy(sgk)
            vk, hk = self._route_factors(sgk, self.D, R0 * scale)
            parts.append((m, x, y, R0 * scale, al * dfade * vk * ca, hk))
        sels = [p[0] & (p[4] > _EPS_A) for p in parts]
        X = np.concatenate([p[1][m] for p, m in zip(parts, sels)])
        Y = np.concatenate([p[2][m] for p, m in zip(parts, sels)])
        R = np.concatenate([p[3][m] for p, m in zip(parts, sels)])
        A = np.concatenate([p[4][m] for p, m in zip(parts, sels)])
        HA = np.concatenate([(p[4] * p[5])[m] for p, m in zip(parts, sels)])
        RGB = np.concatenate([col[m, :3] for m in sels])
        return X, Y, R, A, RGB, head, hx, hy, fade, col, HA

    def _draw_packets(self, flat, X, Y, R, A, RGB, HA):
        """パケットの点（頭と尾）を、panel の縁 → 色ごとの点 の順に重ねる。

        縁（α は HA × panel の α）と点（α は A）の被覆率は1回の距離計算から出し、画素も
        1回だけ拾って書き戻す。拾う画素は縁か点のどちらかが触れる画素（panel が透明でも、
        途中の点の上で縁を消しても、点の画素は必ず含む）。窓は半径の段ごとに分ける。
        """
        np = self.np
        W, H = self.W, self.H
        panel = self.pal["panel"]
        pa = np.float32(panel[3] / 255.0)
        rgb8 = np.clip(np.floor(RGB + 0.5), 0, 255).astype(np.int64)
        packed = (rgb8[:, 0] << 16) | (rgb8[:, 1] << 8) | rgb8[:, 2]
        keys, inv = np.unique(packed, return_inverse=True)
        fis, chs, cds, gids = [], [], [], []
        for sel in _buckets(np, R + _PACKET_HALO + 1.0):
            P = int(math.ceil(float(R[sel].max()) + _PACKET_HALO + 1.0))
            ix, iy, d, S = _window(np, X[sel], Y[sel], P)
            rr = R[sel].astype(np.float32)[:, None, None]
            c_halo = np.clip(rr + np.float32(_PACKET_HALO + 0.5) - d, 0.0, 1.0) * (
                HA[sel].astype(np.float32)[:, None, None] * pa)
            c_dot = np.clip(rr + np.float32(0.5) - d, 0.0, 1.0) * (
                A[sel].astype(np.float32)[:, None, None])
            flat_i, inside = _window_index(np, ix, iy, S, W, H)
            m = ((c_halo > _EPS_A) | (c_dot > _EPS_A)) & inside
            fis.append(flat_i[m])
            chs.append(c_halo[m])
            cds.append(c_dot[m])
            gids.append(np.broadcast_to(inv[sel][:, None, None], m.shape)[m])
        fi = np.concatenate(fis)
        if fi.size == 0:
            return
        ch = np.concatenate(chs)
        ad = np.concatenate(cds)
        gid = np.concatenate(gids)
        u, a_halo = self.union(fi, ch)
        pos = self.posmap                 # 画素 → u の並びの中の位置（使い回す）
        pos[u] = np.arange(u.size)
        df = flat[u].view(np.uint8).reshape(-1, 4).astype(np.float32)
        hs = a_halo > _EPS_A
        if hs.any():
            _over_f(np, df, hs, a_halo[hs], panel[:3])
        for g, kcol in enumerate(keys):
            sel = (gid == g) & (ad > _EPS_A)
            if not sel.any():
                continue
            ug, ag = self.union(fi[sel], ad[sel])
            _over_f(np, df, pos[ug], ag, ((kcol >> 16) & 255, (kcol >> 8) & 255, kcol & 255))
        out = np.empty((u.size, 4), np.uint8)
        out[...] = np.clip(df + np.float32(0.5), 0, 255)
        flat[u] = out.view(np.uint32).reshape(-1)

    def frame(self, t):
        np = self.np
        ekey = self.edge_progress(t)
        vals = self.node_values(t)
        out = self.node_layer(ekey, vals).copy()
        flat = out.view(np.uint32).reshape(-1)
        W, H = self.W, self.H
        batches = {}

        def add(rgb8, idx, a):
            if idx.size:
                key = (int(rgb8[0]) << 16) | (int(rgb8[1]) << 8) | int(rgb8[2])
                batches.setdefault(key, []).append((idx, a))

        # 波紋
        if self.RN:
            act = (t >= self.R_T0) & (t < self.R_T0 + _RIPPLE_DUR)
            if act.any():
                p = (t - self.R_T0[act]) / _RIPPLE_DUR
                e = 1.0 - (1.0 - p) ** 2
                grow = 1.0 + _RIPPLE_GROW * e
                alpha = _RIPPLE_ALPHA * (1.0 - p) * self.R_C[act, 3] / 255.0
                kind = self.R_KIND[act]
                rgb8 = np.clip(np.floor(self.R_C[act, :3] + 0.5), 0, 255).astype(np.int64)
                packed = (rgb8[:, 0] << 16) | (rgb8[:, 1] << 8) | rgb8[:, 2]
                ra, rb, rrad = self.R_A[act], self.R_B[act], self.R_RAD[act]
                rx, ry, rw = self.R_X[act], self.R_Y[act], self.R_W[act]
                circ = kind == 0
                for kcol in np.unique(packed[circ]):
                    for wv in np.unique(rw[circ & (packed == kcol)]):
                        sel = circ & (packed == kcol) & (rw == wv)
                        idx, a = _disks(np, rx[sel], ry[sel], ra[sel] * grow[sel], alpha[sel],
                                        W, H, ring=float(wv))
                        add(((kcol >> 16) & 255, (kcol >> 8) & 255, kcol & 255), idx, a)
                for j in np.flatnonzero(~circ):
                    k = (grow[j] - 1.0) * rb[j]       # box: 縦の半分を半径とみなして広げる
                    idx, a = _rbox_cov(np, rx[j], ry[j], ra[j] + k, rb[j] + k, rrad[j] + k,
                                       rw[j] / 2.0, W, H)
                    add(rgb8[j], idx, a * np.float32(alpha[j]))
        for key in sorted(batches):
            items = batches[key]
            idx = np.concatenate([it[0] for it in items])
            a = np.concatenate([it[1] for it in items])
            self._union_blend(flat, idx, a, ((key >> 16) & 255, (key >> 8) & 255, key & 255))
        # パケット（波紋の上。下に panel の縁を敷いて、明るい線の上でも粒として読めるようにする）
        st = self.packet_state(t)
        if st is not None and st[0].size:
            self._draw_packets(flat, *st[:5], st[10])
        # パケットの文字（端数の位置のまま重ねる。途中の box の中では頭と一緒に消える）
        if st is not None and self.sc["packet_labels"]:
            panel = self.pal["panel"]
            for text, x_left, base_y, al, c8 in self.packet_labels_at(t, st):
                self._blit_mask(flat, text, x_left, base_y, panel[:3], al * panel[3] / 255.0, "halo")
                self._blit_mask(flat, text, x_left, base_y, c8, al, "fill")
        return out

    def packet_labels_at(self, t, st=None):
        """時刻 t に見えるパケットの札: [(文字列, 行頭の x, ベースラインの y, α, 色)]。

        側の列と隠す区間は build のときに札ごとに決めてある（_place_packet_labels）。位置は採点と
        同じ _plabel_side / _plabel_at で出す（進行方向の左右に置く札は曲がり角で回り込む）。
        α は頭の α × _plabel_alpha（ノード・ノードの文字・先に出た札に重なるコマの手前で薄れ、
        離れてから現れる）。
        """
        np = self.np
        if st is None:
            st = self.packet_state(t)
        out = []
        if st is None:
            return out
        head, fade, col = st[5], st[8], st[9]
        ref_top, ref_bot = self.sc["ref"]
        h = ref_bot - ref_top
        for pi, text, plan, blocks in self.sc["packet_labels"]:
            if not head[pi]:
                continue
            al = float(fade[pi] * col[pi, 3] / 255.0) * _plabel_alpha(blocks, t)
            if al <= _EPS_A:
                continue
            pts, cum = self.sc["routes"][self.sc["packets"][pi][1]][:2]
            cw = float(self.sc["sprites"][text].meta["content_width"])
            s = min(max((t - float(self.T0[pi])) * float(self.V[pi]), 0.0), float(self.SE[pi]))
            cx, cy = _plabel_at(_plabel_side(plan, s), pts, cum, s, float(self.D[pi]) / 2.0, cw, h)
            c8 = tuple(int(v) for v in np.clip(np.floor(col[pi, :3] + 0.5), 0, 255))
            out.append((text, cx - cw / 2.0, cy - (ref_top + ref_bot) / 2.0, al, c8))
        return out


# --- 公開 API ---

def flow_graph(nodes, edges=(), *, layout="given", direction="down", size=None, padding=40,
               node="dot", node_radius=10, box=(240, 72), edge_width=3, curve=0.0,
               colors=None, font=None, weight=None, label_size=36, label_pos="auto", seed=0):
    """点と線の図を作る（パケット・配信・送金・感染の図）。出来事を足して build() で動画にする。

    模式図であって実際の経路ではない（「模式図」の注記は呼び出し側が付ける）。
    nodes: {名前: {'pos': (x, y), 'label': 文字, 'shape': 'dot'|'box', 'layer': 段}} か、
      名前のリスト。nodes() と edges() を持つグラフ（networkx など）も duck typing で読む。
    edges: (a, b) か (a, b, {'delay': 秒, 'curve': 0.2}) のリスト。
    layout: given（pos の通り）/ layered（BFS の段・段の中の順は重心法を2往復）/
      radial（根が中心。角度は葉の数に比例）/ rings（段ごとの同心円）。
    direction: layered の向き（down / up / right / left）。
    size: キャンバス (幅, 高さ) px（省略時は Project の解像度）。padding: 自動配置の余白 px。
    node: 既定の形（dot / box）。node_radius: 点の半径 px。box: 箱の (幅, 高さ) px
      （文字が収まらなければ広げる）。edge_width: 辺の幅 px。curve: 辺の曲がり
      （中点から 長さ×curve だけ進行方向の左へ。負で右）。辺の集まるノードでは、隣の辺へ
      倒れ込まないよう自動で弱める（辺ごとの 'curve' は指定どおり）。
    colors: パレットの上書き（fg / accent / muted / line / dim / panel）。
    font / weight / label_size / label_pos: ノードの文字（30px 以上）。auto は、layered では
      葉だけ流れの向き・根は逆の側・途中の点は横、radial / rings は外向き、given は下を
      第一候補にし、辺やほかのノードと重なる側は避ける（どの側も重なれば、辺が下を通る文字に
      panel 色の板を敷いて警告する）。seed: 点の塊の散らばり。
    出来事は FlowGraph の send / broadcast / state / cut（docstring を参照）。
    上限: ノード 5,000・辺 10,000・同時に見えるパケット 2,000（超えたら ValueError）。
    構築にも numpy・opencv-python・Pillow が要る（pip install "scriptvedit[figures]"）。
    """
    return FlowGraph(nodes, edges, layout=layout, direction=direction, size=size,
                     padding=padding, node=node, node_radius=node_radius, box=box,
                     edge_width=edge_width, curve=curve, colors=colors, font=font,
                     weight=weight, label_size=label_size, label_pos=label_pos, seed=seed)


def flow_tree(levels, *, t=0, hop=0.4, layout="radial", amount=None, **kw):
    """段ごとの数（[1, 50, 3000] など）から木を作り、t 秒に根から一斉に配る。

    levels: 各段のノードの数。次の段のノードは前の段へ均等に割り振る（i 番目の親は
      i × 前の段の数 ÷ この段の数 の切り捨て）。名前は 'L段_番号'（'L0_0'・'L2_2999'）。
    t: 根から broadcast する秒（None なら配らない。自分で g.broadcast を呼ぶ）。
    hop: 1段あたりの秒。layout: radial（既定）/ layered / rings。
    amount: 根の量（送金額など）。分かれるたびに等分し、パケットの直径を √(量) に
      比例させる（面積が量に比例し、合計は保たれる）。g.amount に {名前: 量}。
    葉の段が 500 を超えると点の塊（直径 5px・seed で散らす）にし、その段への broadcast の
    パケットは親1つあたり 4 個に束ねる（量は束ねた分の合計）。点の塊には波紋を出さない。
    ほかの引数（size・colors・node_radius・seed など）は flow_graph と同じ。
    """
    fn = "flow_tree"
    if not isinstance(levels, (list, tuple)) or len(levels) < 2:
        raise ValueError(f"{fn}: levels は2段以上の数の列で指定してください（例 [1, 50, 3000]）: {levels!r}")
    for k, m in enumerate(levels):
        if isinstance(m, bool) or not isinstance(m, int) or m < 1:
            raise ValueError(f"{fn}: levels[{k}] は 1 以上の整数で指定してください: {m!r}")
    total = sum(levels)
    if total > _MAX_NODES:
        raise ValueError(f"{fn}: ノードが {total} 個になります（上限 {_MAX_NODES}）")
    _require_choice(fn, "layout", layout, ("radial", "layered", "rings"))
    if t is not None:
        t = _check_time(fn, "t", t)
    _require_number(fn, "hop", hop, 0, 3600)
    if amount is not None:
        _require_number(fn, "amount", amount, 0, None)
        if amount <= 0:
            raise ValueError(f"{fn}: amount は 0 より大きくしてください: {amount!r}")
    for bad in ("nodes", "edges"):
        if bad in kw:
            raise TypeError(f"{fn}: {bad} は levels から作るので渡せません")
    if layout == "radial" and levels[0] != 1:
        raise ValueError(f"{fn}: layout='radial' の根は1つです（levels[0] を 1 に）: {levels[0]!r}")
    names = []
    level_names = []
    level_idx = []
    for k, m in enumerate(levels):
        row = [f"L{k}_{i}" for i in range(m)]
        level_idx.append(list(range(len(names), len(names) + m)))
        names.extend(row)
        level_names.append(row)
    nodes = {nm: {"layer": k} for k, row in enumerate(level_names) for nm in row}
    edges = []
    parent_of = {}
    for k in range(len(levels) - 1):
        a, b = levels[k], levels[k + 1]
        for i in range(b):
            p = i * a // b
            edges.append((level_names[k][p], level_names[k + 1][i]))
            parent_of[level_idx[k + 1][i]] = level_idx[k][p]
    amounts = None
    if amount is not None:
        amounts = [0.0] * total
        nkids = {}
        for c, p in parent_of.items():
            nkids[p] = nkids.get(p, 0) + 1
        for i in level_idx[0]:
            amounts[i] = float(amount) / levels[0]
        for k in range(1, len(levels)):
            for c in level_idx[k]:
                p = parent_of[c]
                amounts[c] = amounts[p] / nkids[p]
    cluster_level = len(levels) - 1 if levels[-1] > _CLUSTER_MIN else None
    tree = {"levels": list(levels), "level_names": level_names, "level_idx": level_idx,
            "cluster": level_idx[cluster_level] if cluster_level is not None else [],
            "cluster_level": cluster_level, "amounts": amounts,
            "amount": None if amount is None else _n(amount)}
    g = FlowGraph(nodes, edges, layout=layout, _tree=tree, _fn=fn, **kw)
    if t is not None:
        rgba = g._pal["accent"]
        for r in level_idx[0]:
            g._broadcast("flow_tree", t, r, float(hop), rgba, True, True,
                         log=["tree_broadcast", t, g.names[r], hop])
    return g
