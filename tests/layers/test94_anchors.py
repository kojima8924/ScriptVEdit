from scriptvedit import *

# anchor（配置基準点）6値の網羅。同じ x/y を指定しても基準点が違えば
# overlay の左上座標は互いに異なる式になる（filters/video.py の _ANCHOR_OFFSETS）。
# center 以外の5値は波4で実装したので、ここで生成コマンドを固定する。
# show() は current_time を進めないので6個が同じ2秒間に重なって並ぶ。
for anchor_name in ("center", "topleft", "left", "right", "top", "bottom"):
    o = Object(asset("images/shape_badge.png"))
    o.show(2) <= resize(sx=0.2, sy=0.2)   # Transform を先に（規約: 全Transform→全Effect）
    o <= move(x=0.5, y=0.5, anchor=anchor_name)
