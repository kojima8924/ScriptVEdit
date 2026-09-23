from scriptvedit import *

# 音声前処理（filters/audio.py の _build_audio_pre_filters）の回帰ゲート。
# atrim / atempo はここまでスナップショット対象が1件も無く、フィルタ文字列が
# 変わっても誰も気づけなかった。明示 atrim があると auto atrim（time() 由来の
# atrim=duration=... 後置）が付かないことも同時に固定する。

# 音声だけのプロジェクトにしないための映像側（1枚だけ置く）
badge = Object(asset("images/shape_badge.png"))
badge.show(8) <= resize(sx=0.3, sy=0.3)
badge <= move(x=0.5, y=0.5, anchor="center")

# atrim(duration) のみ → atrim=duration=3（start は出力に現れない）
head = Object(asset("audio/bgm_loop.mp3"))
head.time(3) <= atrim(3) & avolume(0.5)

# atrim(start=, duration=) → atrim=start=2:duration=3（素材の 2〜5 秒）。
# atempo を続けて前処理の並び順（atrim → atempo）を固定する。
# time() が current_time を進めているので adelay も付く。
mid = Object(asset("audio/bgm_loop.mp3"))
mid.time(2) <= atrim(start=2, duration=3) & atempo(1.5)

# atempo は 0.5 未満を ffmpeg が受け付けないため _atempo_chain_rates が
# 多段へ分解する（0.25 → atempo=0.5,atempo=0.5）。
slow = Object(asset("audio/bgm_loop.mp3"))
slow.time(3) <= atrim(1) & atempo(0.25)
