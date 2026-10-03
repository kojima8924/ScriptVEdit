from scriptvedit import *

# flow_graph: ロードバランサーと6台のサーバー（layered・下向き）。
# ヘルスチェックを送って戻す。遅れたサーバー s4 は戻りが途中で消え（drop）、
# 灰色と × で外され（state）、辺が破線になって薄れる（cut）。
# 生成物は frames() と同じ __cache__/artifacts/frames/<鍵>.mov（dry_run では描かない）。
# 鍵にはノードの文字のフォントの内容指紋が入る（スナップショットの比較では畳む）。
servers = [f"s{k}" for k in range(1, 7)]
nodes = {"lb": {"label": "ロードバランサー", "shape": "box"}}
for k, s in enumerate(servers, 1):
    nodes[s] = {"label": f"サーバー{k}"}
g = flow_graph(nodes, [("lb", s) for s in servers], layout="layered", label_size=30)
for k, s in enumerate(servers):
    g.send(0.2 + 0.1 * k, ["lb", s])
    g.send(1.0 + 0.1 * k, [s, "lb"], fate=("drop", 0.5) if s == "s4" else "pass")
g.state(2.2, "s4", dim=0.6, mark="x")
g.cut(2.2, ("lb", "s4"))
g.build().time(4)
