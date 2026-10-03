# fly_to: 一覧表の赤い「今の状態」だけを残した text_image（白い部分は透明）の粒が、
#   締めの一文になる（動画の結びの形）
from scriptvedit import *

RED = "#e0241b"
red = text_image([("Stack Overflow　", {"color": "white@0"}),
                  ("34分、止まった", {"color": RED}), "\n",
                  ("Cloudflare　", {"color": "white@0"}),
                  ("今も、書く人しだい", {"color": RED})],
                 size=44, align="right", line_spacing=2.0)
close = text_image("事件は、まだ、終わっていない。", size=72, color=RED)
# B の中心が画面の中央より少し上 (640, 300) に来るずれ（A の中心は (870.4, 446.4)）
red.time(3) <= fly_to(close, offset=(-230, -146), arc=0.2, stagger=0.35, duration=2.0)
red <= move(x=0.68, y=0.62, anchor="center")
