from scriptvedit import *
bgm = Object(asset("audio/bgm_loop.mp3"))
v, a = bgm.split()
# v is None（音声のみ）
a <= avolume(0.6) & avolume(lambda u: lerp(0, 1, u))
bgm.time(4)
