from scriptvedit import *
clip = Object(asset("video/clip_with_audio.mp4"))
v, a = clip.split()
# clip_with_audio.mp4 は音声を持つので a も AudioView（None ではない）。
# v 側の Transform は映像にだけ効き、音声はそのまま amix へ乗る
# （生成コマンドにも [N:a]...amix が出る）。
v <= resize(sx=0.5, sy=0.5)
clip.time(3) <= move(x=0.5, y=0.5, anchor="center") & scale(lambda u: lerp(0.8, 1, u))
