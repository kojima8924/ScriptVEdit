from scriptvedit import *

# globe: 大円の弧3本（裏へ回る弧・縁の外へ出る弧を含む）＋ 波紋 ＋ turn（四元数の slerp）。
# 陸は描かず 15 度の経緯線（land=False）。フォントもデータファイルも使わない。

tokyo, london, sf, sydney = (35.7, 139.7), (51.5, -0.1), (37.8, -122.4), (-33.9, 151.2)
g = globe(size=480, view=(30, 100), land=False, atmosphere=0.3, back=True)
g.points([tokyo, london, sf, sydney], t=0.0)
g.arc(london, tokyo, t=0.3, dur=1.0)
g.arc(sf, tokyo, t=0.6, dur=1.0, height=0.25, trail=0.5)
g.arc(tokyo, sydney, t=0.9, dur=0.6, head=False)
g.ripple(tokyo, t=1.5, dur=0.8, max_deg=10)
g.turn(1.6, (20, 160), dur=1.2, easing="ease_in_out_sine")
obj = g.build()
obj.time(3.5) <= move(x=0.5, y=0.5, anchor="center")
