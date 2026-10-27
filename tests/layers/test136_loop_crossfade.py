from scriptvedit import *
# loop(crossfade=…): 各周を素材の head〜（終わり − tail）に限り、次の周を等パワーで重ねてつなぐ
# （tail は秒の指定。tail="auto" の測定は tests/test_loop_crossfade.py が合成した音で確かめる）
narr = Object(asset("audio/効果音.mp3"))
narr.time(2)
bgm = Object(asset("audio/bgm_loop.mp3"))
bgm.time(12) <= loop(crossfade=1.5, tail=24, head=0.5) & avolume(0.6) & duck_under(narr, ratio=6)
