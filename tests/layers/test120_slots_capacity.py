from scriptvedit import *
# slots: 200 箱を畳んだ棚（elide=(6, 3)）に 400 個が流れ込み、上限 200 を超えた分があふれる
# 。最後に halt で全体を 0.45 の不透明度へ。
# 生成物は framekit.build → frames の .mov（dry_run は draw を呼ばず、生成コマンドだけが
# cache に載る）。size を固定し、既定フォントの字幅で寸法が変わらないようにする。
s = slots(size=(1280, 300))
s.row("shelf", 200, label="項目の上限", capacity=200, elide=(6, 3))
s.fill(1.0, "shelf", 400, dur=2.5)
s.halt(3.8)
# 表示は図の尺ぶん（time() を引数なしで呼ぶ。frames() と同じ）
fig = s.build()
fig.time() <= move(x=0.5, y=0.5, anchor="center")
