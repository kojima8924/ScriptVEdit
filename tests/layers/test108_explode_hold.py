# explode_to: delay（静止の間は焼かない）/ duration（散った後は最後のコマを保持）/
# fade=False（散らしたまま残す）/ toward（1点へ集まる）/ expand 省略（自動）
from scriptvedit import *

a = Object(asset("images/shape_badge.png"))
a.time(3) <= resize(sx=0.5, sy=0.5)
a <= explode_to(max_pixels=600, speed=160, gravity=0, spread=0.5, seed=7,
                fade=False, delay=0.5, duration=1.5)
a <= move(x=0.3, y=0.5, anchor="center")

b = Object(asset("images/shape_figure.png"))
(b @ 0).time(2) <= explode_to(max_pixels=600, speed=60, gravity=120, swirl=0.6,
                              seed=3, toward=(200, -120), delay=0.4)
b <= move(x=0.7, y=0.5, anchor="center")
