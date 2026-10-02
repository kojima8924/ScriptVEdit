from scriptvedit import *

# tint: 色の塗り替え（アルファは変えない）

# 定数（lutrgb・静止画チェックポイントへ焼ける）: 掛け算で赤へ
a = Object(asset("images/shape_dots.png"))
a.show(3) <= tint("red") & move(x=0.2, y=0.5, anchor="center")

# 半分だけ効かせる / 暗くする
b = Object(asset("images/shape_dots.png"))
b.show(3) <= tint("#00c0ff", 0.5) & move(x=0.4, y=0.5, anchor="center")
c = Object(asset("images/shape_dots.png"))
c.show(3) <= tint("black", 0.5) & move(x=0.6, y=0.5, anchor="center")

# fill: 元の色に関係なく塗る。amount を秒の式にすると geq（時間で変わる）
d = Object(asset("images/shape_badge.png"))
d.show(3) <= (tint("yellow", amount=ramp(1.0, 2.0), mode="fill")
              & move(x=0.82, y=0.5, anchor="center"))
