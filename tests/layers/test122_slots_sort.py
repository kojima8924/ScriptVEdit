from scriptvedit import *
# slots: [1, 2, 10] を文字列として並べ替える。to_str で引用符が現れ、
# compare(by='str') は最初に違う字（'2' と '10' の '1'）を囲んで不等号を出し、
# swap は上下に分かれた弧で入れ替わる。
s = slots(cell=96, size=(640, 320))
s.row("arr", 3, label="[1, 2, 10]", values=[1, 2, 10], index_base=0)
s.to_str(0.4, "arr")
s.compare(1.0, "arr", 1, 2, by="str")
s.swap(2.2, "arr", 1, 2)
# 表示は図の尺ぶん（time() を引数なしで呼ぶ。frames() と同じ）
fig = s.build()
fig.time() <= move(x=0.5, y=0.5, anchor="center")
