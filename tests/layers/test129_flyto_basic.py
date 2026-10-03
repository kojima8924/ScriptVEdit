# fly_to: 小さな画像の粒が飛んで、右に 300px ずれた所の別の画像になる
#   resize（fly_to の前の Transform）は前処理として PNG のチェックポイントへ焼かれ、
#   offset の単位はその後の絵（A）の px
from scriptvedit import *

dots = Object(asset("images/shape_dots.png"))
badge = Object(asset("images/shape_badge.png"))     # 行き先（消費される）
dots.time(2) <= resize(sx=0.5, sy=0.5)
dots <= fly_to(badge, offset=(300, 0), max_pixels=4000, seed=1)
dots <= move(x=0.4, y=0.5, anchor="center")
