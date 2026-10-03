# fly_to: anchor='topleft' でも A の位置が静止画と同じ（余白は A の中心に対して対称）/
#   delay・duration（前後は最初・最後のコマを tpad で複製）/ 下へ 220px / arc を左手側へ /
#   match='angle'・stagger_by='y'
from scriptvedit import *

dots = Object(asset("images/shape_dots.png"))
star = Object(asset("images/shape_starburst.png"))   # 行き先（消費される）
dots.time(3) <= fly_to(star, offset=(0, 220), delay=0.5, duration=1.5, arc=-0.3,
                       match="angle", stagger_by="y", max_pixels=3000, seed=2)
dots <= move(x=0.1, y=0.1, anchor="topleft")
