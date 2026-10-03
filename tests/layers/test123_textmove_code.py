from scriptvedit import *

# text_transition: 公式の正規表現 → 簡略版 \s+$と、\s+$ → \s++$。
# 字は同梱の自作フォント（tests/golden/textmove/svtm_block.ttf）で描く: システムのフォントだと
# 鍵（フォントの内容指紋）とキャンバスの寸法が環境ごとに変わり、スナップショットがずれる。
# dry_run では draw を呼ばず、生成コマンド（標準入力の生 RGBA → qtrle）だけが cache に載る。
FONT = here("../golden/textmove/svtm_block.ttf")

# 公式の式には U+200C（ゼロ幅非接合子）が入っている（見えない字なので chr で書く）
ZWNJ = chr(0x200C)
official = r"^[\s" + ZWNJ + r"]+|[\s" + ZWNJ + r"]+$"
# 要らない部品が落ち（leave='fall'）、残りが滑って大きくなり中央へ寄る
t = text_transition([official, [(r"\s+$", {"size": 96})]], unit=r"\\.|.", leave="fall",
                    anchor="center", font=FONT, size=64, hold=[0.3, 0.5], duration=1.6)
t.time(3) <= move(x=0.5, y=0.4, anchor="center")

# 赤い '+' が1つ降りる（色は後の状態の区間で決める）
t2 = text_transition([r"\s+$", [r"\s+", ("+", {"color": "#e0241b"}), "$"]],
                     duration=0.8, enter="drop", font=FONT, size=64, border=3)
t2 @ 0
t2.time(3) <= move(x=0.5, y=0.8, anchor="center")
