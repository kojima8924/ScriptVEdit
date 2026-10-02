from scriptvedit import *

# stills(): 時刻表つきの静止画列を1本の動画（入力1本）にする。
# 画像が何枚あっても main の入力は1本で、生成コマンド（concat demuxer → qtrle）は
# cache 側に載る。画像のパスはリスト（.ffconcat）の中にあり、コマンドには現れない。

a = asset("images/mask_circle.png")      # どちらも 320x240
b = asset("images/mask_gradient.png")

# 形1: (画像, 表示秒)。境目（累積秒）を最も近いフレームへ丸める:
#   0.5 / 0.81 / 0.91 / 1.63 秒 → 15 / 24 / 27 / 49 フレーム（1枚ずつ丸めて足さない）
pages = stills([(a, 0.5), (b, 0.31), (a, 0.1), (b, 0.72)])
# time(3) は素材（49 フレーム）より長い → 最後の絵を保持する tpad（stop_mode=clone）が入る
pages.time(3) <= move(x=0.25, y=0.5, anchor="center")

# 形2: (画像, 開始秒) + total。size= で出力寸法を決める（縦横比を保ち、余白は透明）。
# 開始が 0 より後なので、開始の tpad と同じ tpad に保持を書く
timed = stills([(b, 0), (a, 0.4), (b, 1.0)], total=1.5, size=(480, 270))
timed @ 1.0
timed.time(2.5) <= move(x=0.75, y=0.5, anchor="center")

# 引数なしの time() は総尺ぶん（1.5 秒）。伸ばさないので保持は入らない
exact = stills([(a, 0), (b, 0.75)], total=1.5)
exact @ 0
exact.time() <= move(x=0.5, y=0.15, anchor="center")
