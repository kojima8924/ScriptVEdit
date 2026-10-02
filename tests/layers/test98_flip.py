from scriptvedit import *

# flip Transform: 左右反転（hflip）/ 上下反転（vflip）/ 両方。
# Transform は bakeable なので、静止画はどれも PNG チェックポイントへ焼かれる
# （cache 側の -vf に hflip / vflip が現れる）。寸法は変わらない。

# 既定の flip() は左右だけ。resize と | で連結して1つのチェックポイントに焼く
left = Object(asset("images/shape_badge.png"))
left <= resize(sx=0.3, sy=0.3) | flip()
left.show(2) <= move(x=0.2, y=0.5, anchor="center")

# 上下だけ反転（horizontal を省略して vertical=True だけ指定すると上下のみ）
upside = Object(asset("images/shape_badge.png"))
upside <= resize(sx=0.3, sy=0.3) | flip(vertical=True)
upside.show(2) <= move(x=0.5, y=0.5, anchor="center")

# 両方（180度回転と同じ絵）。flip の後に rotate を続けても順序どおりに焼かれる
both = Object(asset("images/shape_badge.png"))
both <= resize(sx=0.3, sy=0.3) | flip(horizontal=True, vertical=True) | rotate(deg=15)
both.time(2) <= move(x=0.8, y=0.5, anchor="center")
