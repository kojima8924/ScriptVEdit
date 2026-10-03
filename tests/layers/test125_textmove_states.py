from scriptvedit import *

# text_transition: 3つの状態と hold（parseInt(0.0000005) → '5e-7' → 5）。
# 状態ごとに止まる秒（hold）と遷移ごとの秒（duration）をリストで渡す。
# コマ数 = (Σhold + Σduration) × fps = (0.4 + 0.6 + 0.5 + 1.0 + 0.8) × 30 = 99。
# 同梱の自作フォントで描く（理由は test123 と同じ）。
FONT = here("../golden/textmove/svtm_block.ttf")

t = text_transition(["0.0000005", "'5e-7'", "5"], hold=[0.4, 0.6, 0.5], duration=[1.0, 0.8],
                    leave="scatter", enter="rise", anchor="center", font=FONT, size=64)
t.time() <= move(x=0.5, y=0.5, anchor="center")

# 語の単位（空白もトークン）と move='arc'
w = text_transition(["Foundation sends", "Fundation sends"], unit="word", move="arc",
                    font=FONT, size=40, duration=0.9)
w @ 0
w.time(3.3) <= move(x=0.5, y=0.85, anchor="center")
