from scriptvedit import *

# timeline: 2024年 → （途中を省く）→ 2026年8月〜10月の年表。札が下に立ち、2つの印の間の
# 赤い帯が伸び、その上に回る数字（odometer）を重ねる（位置は obj.figure.x と axis_y から取る）。
# 生成物は frames() と同じ __cache__/artifacts/frames/<鍵>.mov（dry_run では描かない）。
# 鍵には札の字のフォントの内容指紋が入る（スナップショットの比較では畳む）。
tl = timeline("2024-03-01", "2026-11-15",
              ticks=[("2024-06-01", "2024年"), ("2026-08-01", "2026年8月"),
                     ("2026-10-01", "10月")],
              breaks=[("2024-09-01", "2026-07-15")], arrow=True)
tl.span("2024-06-01", "2026-08-11", color="dim", at=0.2, dur=1.0)
tl.mark("2024-06-01", "2024年 兆し", side="below", card=True, enter="rise", at=0.2)
tl.mark("2026-08-11", "8月11日 報告", side="below", card=True, enter="rise", at=1.2)
tl.span("2026-08-11", "2026-10-21", color="accent", at=2.0, dur=1.5)
tl.mark("2026-10-21", "10月21日 修正版", side="below", card=True, color="accent", at=3.5)
fig = tl.build()
fig.time()

W, H = fig.figure.size
cx = (tl.x("2026-08-11") + tl.x("2026-10-21")) / 2
weeks = odometer(0, 10, duration=1.5, size=56, color="#e0241b", border=3, anchor="center")
weeks @ 2.0
weeks.time(3.0) <= move(x=cx / W, y=(fig.figure.axis_y - 60) / H, anchor="center")
