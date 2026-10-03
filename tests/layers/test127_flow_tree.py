from scriptvedit import *

# flow_tree: 1 → 50 → 3000（radial。500 を超える葉の段は点の塊）。
# 0.3 秒に根から一斉に配る（broadcast）。amount でパケットの直径が √(量) に比例し、
# 点の塊へのパケットは親1つあたり 4 個に束ねる。文字は無い（フォントを使わない）。
g = flow_tree([1, 50, 3000], t=0.3, amount=400000)
g.build().time(3)
