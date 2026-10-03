from scriptvedit import *

# flow_graph（layout='given'・曲がった辺）: 35 件の送金指示のうち 30 件が ny の手前で
# 止まり（accent になって 1.6×size ずつ並ぶ）、残りの 5 件が通り抜ける。文字は無い。
nodes = {"bb": {"pos": (140, 360)}, "swift": {"pos": (460, 360)},
         "ny": {"pos": (860, 360)}, "ph": {"pos": (1140, 200)}, "sl": {"pos": (1140, 520)}}
g = flow_graph(nodes, [("bb", "swift"), ("swift", "ny", {"curve": 0.2}),
                       ("ny", "ph"), ("ny", "sl", {"curve": -0.25})])
path = ["bb", "swift", "ny", "ph"]
g.send(0.2, path, n=30, every=0.08, fate=("stop", "ny"))
g.send(0.2 + 30 * 0.08, path, n=5, every=0.08)
g.build().time(6)
