from scriptvedit import *

# frames(): コマを描く関数 draw(i) から動画 Object を作る。
# dry_run では draw を呼ばず、生成コマンド（標準入力の生 RGBA → qtrle）だけが cache に載る。
# draw のコードは鍵に入らない（鍵は key・コマ数・fps・size）。


class Bar:
    """numpy 配列の代わり（frames() は shape / dtype / tobytes だけを見る）。

    左から伸びていく白い帯。実際の制作では PIL.Image か numpy 配列を返す。
    """
    W, H = 320, 40
    dtype = "uint8"
    shape = (H, W, 4)

    def __init__(self, i):
        self.i = i

    def tobytes(self):
        n = min(self.W, 8 * (self.i + 1))
        row = bytes((255, 255, 255, 255)) * n + bytes(4) * (self.W - n)
        return row * self.H


# 40 コマ（1.33 秒）を 3 秒表示 → 伸ばした分は最後のコマが残る
bar = frames(Bar, 40, key=["bar", 1], size=(Bar.W, Bar.H))
bar.time(3) <= move(x=0.5, y=0.8, anchor="center")

# duration で指定（0.5 秒 → 15 コマ）。開始が 0 より後 → 開始の tpad と同じ tpad に保持
bar2 = frames(Bar, duration=0.5, key=["bar", 2], size=(Bar.W, Bar.H))
bar2 @ 1.0
bar2.time(1.5) <= move(x=0.5, y=0.6, anchor="center")

# Effect を焼く経路: checkpoint のコマンドが入力の最後のコマを保持してから焼く
# （-vf の先頭に tpad=stop=-1:stop_mode=clone。伸ばした区間でも fade が進む）
page = stills([(asset("images/mask_circle.png"), 0.5),
               (asset("images/mask_gradient.png"), 0.5)])
page @ 0
page.time(3) <= fade(lambda u: 1 - u)
