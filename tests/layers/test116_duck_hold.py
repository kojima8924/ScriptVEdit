from scriptvedit import *

# duck_under(hold=…): 相手が止んでから戻り始めるまでの保持（検出用の枝が保持つきの包絡になる）
# video_sequence(...).time(): 引数なしの time() で合成尺が自動で入る
seq = video_sequence(asset("video/clip_with_audio.mp4"), asset("video/flowerbg_noaudio.mp4"),
                     transition="fade", t_dur=0.5)
seq.time()

n1 = Object(asset("audio/効果音.mp3"))
(n1 @ 0.5).show(2)
n2 = Object(asset("audio/効果音.mp3"))
(n2 @ 3.0).show(2)

# 相手が1つ + hold
bgm = Object(asset("audio/bgm_loop.mp3"))
(bgm @ 0).show(6) <= loop() & avolume(0.6) & duck_under(n1, ratio=10, threshold=0.02, hold=600)
# 相手が複数 + hold（合算してから包絡を作る）
bgm2 = Object(asset("audio/bgm_loop.mp3"))
(bgm2 @ 6).show(4) <= avolume(0.3) & duck_under(n1, n2, release=400, hold=300)
