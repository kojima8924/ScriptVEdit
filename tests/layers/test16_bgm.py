from scriptvedit import *
bgm = Object(asset("audio/bgm_loop.mp3"))
# avolume は多段適用で volume フィルタが直列に積まれる（0.8 倍 → フェード）
bgm.time(5) <= avolume(0.8) & avolume(lambda u: u)
