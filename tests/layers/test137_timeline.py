from scriptvedit import *

# timeline: 2024年 → （途中を省く）→ 2026年8月〜10月の年表。札が下に立ち、2つの印の間の
# 赤い帯が伸び、その上に回る数字（odometer）を重ねる（位置は obj.figure.x と axis_y から取る）。
# 生成物は frames() と同じ __cache__/artifacts/frames/<鍵>.mov（dry_run では描かない）。
# 同梱の自作フォントと ASCII の札で、文字の寸法・図の自動中央寄せも OS 間で固定する。
# 鍵にはフォントの内容指紋と Pillow の版が入る（比較ではハッシュだけを畳む）。
FONT = here("../golden/textmove/svtm_block.ttf")
tl = timeline("2024-03-01", "2026-11-15",
              ticks=[("2024-06-01", "2024"), ("2026-08-01", "Aug 2026"),
                     ("2026-10-01", "Oct")],
              breaks=[("2024-09-01", "2026-07-15")], arrow=True, font=FONT)
tl.span("2024-06-01", "2026-08-11", color="dim", at=0.2, dur=1.0)
tl.mark("2024-06-01", "2024 Signs", side="below", card=True, enter="rise", at=0.2)
tl.mark("2026-08-11", "Aug 11 Report", side="below", card=True, enter="rise", at=1.2)
tl.span("2026-08-11", "2026-10-21", color="accent", at=2.0, dur=1.5)
tl.mark("2026-10-21", "Oct 21 Revision", side="below", card=True, color="accent", at=3.5)
fig = tl.build()
fig.time()

W, H = fig.figure.size
cx = (tl.x("2026-08-11") + tl.x("2026-10-21")) / 2
weeks = odometer(0, 10, duration=1.5, size=56, font=FONT, color="#e0241b", border=3, anchor="center")
weeks @ 2.0
weeks.time(3.0) <= move(x=cx / W, y=(fig.figure.axis_y - 60) / H, anchor="center")
