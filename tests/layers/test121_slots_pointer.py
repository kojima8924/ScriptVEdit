from scriptvedit import *
# slots: 21 個と 20 個の 2 行。21 個目の相手は ghost の点線の箱。
# put で札が降り、link の矢印の先が ghost に届くと accent、範囲外の read は針が
# 斜線の区画「?」へ出て accent になる。
s = slots(size=(1920, 440))
s.row("rule", 21, label="ルールの型:\n受け取る項目 21個")
s.row("input", 20, label="プログラムが\n渡す項目: 20個", ghost=[21])
s.put(0.5, "rule", 21, "条件")
s.link(1.2, ("rule", 21), ("input", 21))
s.read(2.0, "input", 21, dur=1.2)
# 表示は図の尺ぶん（time() を引数なしで呼ぶ。frames() と同じ）
fig = s.build()
fig.time() <= move(x=0.5, y=0.5, anchor="center")
