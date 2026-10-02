from scriptvedit import *
# text: 行間（line_spacing）・行ごとの揃え（text_align）・縦の基準（y_align）
t = text("1行目\n中央そろえの2行目\n3", x=0.5, y=0.3, size=48, border=3,
         line_spacing=16, text_align="center")
t.time(2)
r = text("右そろえ\nの2行", x=0.6, y=0.7, size=40, border=3, anchor="left",
         text_align="right", y_align="baseline")
r.time(2) @ 0
# glow を text に掛ける（live 経路。format=gbrap で RGB のまま screen 合成）
g = text("GLOW", x=0.3, y=0.75, size=96, color="white")
g.time(2) @ 0
g <= glow(radius=10, intensity=0.9)
