from scriptvedit import *

# regex_view(view='rows'): x＋空白10個＋x に \s+$。開始位置ごとに1行、帯が最後の x で
# 赤く止まり、行が積もって三角形になる。カウンタは成功した1字の判定（count='matches'）で、
# 最後の拍の後に regex_count の外挿値（空白 20,000 個 → 200,010,000）まで回す。
# count_label は (拍と回転の間の書式, 最後の書式) の組: 回している間は桁が動き、
# 回し終えたら字幕と同じ「約2億回」になる（粗い書式1つだけだと拍の間「約0億回」のまま）。
tr = regex_trace(r"\s+$", "x" + " " * 10 + "x")
big = regex_count(r"\s+$", lambda n: "x" + " " * n + "x", 20000, count="matches").value
fig = regex_view(tr, view="rows", count="matches", count_to=big,
                 count_label=("{n:,} 回", "約{oku:.0f}億回（模式）"), size=(1040, 700))
fig.time() <= move(x=0.5, y=0.5, anchor="center")
