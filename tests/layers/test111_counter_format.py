from scriptvedit import *
# counter: 桁区切り（%,d）+ イージング名 / 小数（%.1f）+ 文字の % / 32ビットを超える整数
# 最後のコマは必ず to（進行度の分母は 尺 - 1フレーム）
a = counter(0, 1234567, format="¥%,d", easing="ease_out_cubic", x=0.5, y=0.25,
            size=56, border=3)
a.time(3)
b = counter(-5, 100, format="%.1f%%", x=0.5, y=0.5, size=56, border=3)
b.time(3) @ 0
c = counter(0, 5000000000, x=0.5, y=0.75, size=56, border=3)
c.time(3) @ 0
