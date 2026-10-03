from scriptvedit import *

# regex_view(view='tape'): xxxxx を2つの .* が分け合う位置を、= の判定ごとに1拍（56 拍）で
# 試していく図。pace を省いて duration=12 を渡すので、拍がちょうど 12 秒を埋める
# （最後の拍 + hold_end 1 秒 = 12 秒。コマ数は round(12 × 30) = 360）。
# 生成物は framekit.build → frames の qtrle .mov（入力1本）。dry_run では描かず、
# 生成コマンド（標準入力の生 RGBA → qtrle）だけが cache に載る。
tr = regex_trace(r".*(?:.*=.*)", "xxxxx")
# size を固定する（中身の寸法はフォントのメトリクスで変わるので、環境に依らない枠にする）
fig = regex_view(tr, view="tape", beats="literal:=", duration=12, size=(560, 500))
fig.time() <= move(x=0.5, y=0.5, anchor="center")
