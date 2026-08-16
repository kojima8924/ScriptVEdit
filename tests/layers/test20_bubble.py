from scriptvedit import *

b1 = bubble("ここがポイント！\n要チェックです", duration=1.0,
            tail=(0.6, 0.75), pos=(0.5, 0.35), box=(0.4, 0.18),
            style={"fontSize": 30})
b1 <= scale(lambda u: lerp(0.8, 1.0, u))
