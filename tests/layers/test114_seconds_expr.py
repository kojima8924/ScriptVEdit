from scriptvedit import *

# 秒で書く時間: ramp(a, b) / keyframes_sec / elapsed / remaining
# 表示秒（time の値）を変えても、動く時刻（秒）は変わらない。

# 最初の 0.5 秒で現れ、最後の 0.5 秒で消える（積なので geq 経路）
badge = Object(asset("images/shape_badge.png"))
badge.time(4) <= (fade(ramp(0, 0.5) * (1 - ramp(0.5, 0, from_end=True)))
                  & move(x=0.25, y=0.5, anchor="center"))

# 1.0〜1.5 秒で拭って現れ、2.5〜3.5 秒で 1.2 倍へ（イージングつき）
fig = Object(asset("images/shape_figure.png"))
fig.show(4) <= (wipe("left", progress=ramp(1.0, 1.5, ease_out_cubic))
                & scale(lambda u: lerp(1.0, 1.2, ramp(2.5, 3.5)))
                & move(x=0.7, y=0.5, anchor="center"))

# 秒のキーフレーム + elapsed() を直接使う移動（live）
dots = Object(asset("images/shape_dots.png"))
dots.show(4) <= (fade(keyframes_sec((0, 0), (0.25, 1), (3.5, 1), (4, 0)))
                 & move(x=lambda u: 0.1 + 0.2 * clip(elapsed() / 2, 0, 1), y=0.85,
                        anchor="center"))

# 音量も秒で書ける（最後の 1 秒でフェードアウト）
bgm = Object(asset("audio/bgm_loop.mp3"))
bgm.show(4) <= avolume(lambda u: clip(remaining() / 1.0, 0, 1))
