from scriptvedit import *
oni = Object(asset("images/shape_badge.png"))
oni <= resize(sx=0.4, sy=0.4)
oni.time(3) <= move(x=0.5, y=0.5, anchor="center") & fade(lambda u: u)
anchor("oni_done")

se = Object(asset("audio/効果音.mp3"))
se.time(1) <= avolume(0.5)
