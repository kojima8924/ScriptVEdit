from scriptvedit import *

# odometer: 32ビットの数え盤（2147483647 → 2147483648 で 0111… が 1000… へ裏返る。
# 変わる桁を右から 0.04 秒ずつ遅らせて回す）と、日時の odometer.text（巻き戻すと下へ回る）。
# 同梱の自作フォントで描く（理由は test123 と同じ）。
FONT = here("../golden/textmove/svtm_block.ttf")

bits = odometer(2**31 - 1, 2**31, base=2, digits=32, signed="twos", group=8, sep=" ",
                font=FONT, size=32, hold=[0.2, 0.3])
bits.time(2.5) <= move(x=0.5, y=0.3, anchor="center")

clock = odometer.text("2038-01-19 03:14:07", "1901-12-13 20:45:52", roll_dir="down",
                      font=FONT, size=48)
clock @ 0
clock.time(2.5) <= move(x=0.5, y=0.7, anchor="center")
