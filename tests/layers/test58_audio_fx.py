from scriptvedit import *
# loop（aloop） + duck_under（sidechaincompress、narration再生中にBGMを下げる）
narr = Object(asset("audio/効果音.mp3"))
narr.time(2)
bgm = Object(asset("audio/bgm_loop.mp3"))
bgm.time(5) <= loop() & avolume(0.7) & duck_under(narr, ratio=6, threshold=0.03)
