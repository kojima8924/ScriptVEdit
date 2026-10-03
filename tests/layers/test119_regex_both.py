from scriptvedit import *

# regex_view(view='both'): 後戻りを禁じた2つの書き方を左右に並べる。
#   左 \s++$     … 行の中の後戻りの印は消えるが、行は積もり続けて三角形のまま
#   右 (?<!\s)\s+$ … 後読みが空白の続きでの開始を断るので、一瞬で終わる
# at= で先頭の拍を語の時刻に合わせる（右は2拍ぶん指定）。右は @ 0 で左と並行に置く。
# size は固定（中身の寸法はフォントのメトリクスで変わるので、環境に依らない枠にする）。
text = "x" + " " * 10 + "x"
left = regex_view(regex_trace(r"\s++$", text), view="both", cell=40, at=[0.5],
                  size=(640, 560))
left.time() <= move(x=0.27, y=0.5, anchor="center")
right = regex_view(regex_trace(r"(?<!\s)\s+$", text), view="both", cell=40, at=[0.5, 1.1],
                   size=(640, 560))
right @ 0
right.time() <= move(x=0.73, y=0.5, anchor="center")
