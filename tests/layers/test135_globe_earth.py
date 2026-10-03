from scriptvedit import *

# globe: 既定の陸（同梱の地球 = Natural Earth 1:110m。パブリックドメイン）と既定の見た目。
# 左は ortho（atmosphere 既定 0.25・拠点の点の堀と光の輪）、右は plate の view 既定（"auto"）で
# 太平洋を渡る弧が1本につながる中心の経度を選ぶ。鍵には同梱の PNG の内容指紋が入る
# （パスは入らない）ので、環境に依らない。フォントは使わない。

tokyo, sf, london, sydney = (35.7, 139.7), (37.8, -122.4), (51.5, -0.1), (-33.9, 151.2)
g = globe(size=360, view=(30, -160))
g.points([tokyo, sf, london, sydney], appear=("wave", sf, 120))
g.arc(sf, tokyo, t=0.5, dur=1.0)
g.ripple(tokyo, t=1.5)
obj = g.build()
obj.time(3) <= move(x=0.25, y=0.5, anchor="center")

m = globe(projection="plate", size=(560, 280))
m.points([tokyo, sf, london, sydney])
m.arc(sf, tokyo, t=0.3, dur=1.0)
m.arc(london, tokyo, t=0.6, dur=1.0)
plate = m.build()
plate.time(3) <= move(x=0.72, y=0.5, anchor="center")
plate @ 0                                   # 左の地球と同時に（非進行の絶対配置）
