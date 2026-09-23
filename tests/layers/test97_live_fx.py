from scriptvedit import *

# live Effect（shake / inertia / repeat / delete）と、タイムライン外で素材化する
# compute() / アンカーまで表示する show_until() の回帰ゲート。
# いずれもスナップショット対象が1件も無く、overlay 座標式やフィルタ文字列が
# 変わっても検出できなかった。

# shake: overlay 座標へ sin/cos のオフセットを足す（move が無くても効く）
badge = Object(asset("images/shape_badge.png"))
badge.show(3) <= resize(sx=0.25, sy=0.25)
badge <= shake(amplitude=0.05, frequency=8)

# inertia: 初速から指数減衰する move 系 Effect
dots = Object(asset("images/shape_dots.png"))
dots.show(3) <= resize(sx=0.25, sy=0.25)
dots <= inertia(0.4, -0.2, damping=2.5, x0=0.2, y0=0.7)

# compute(): Transform を焼いた PNG へ素材ごと差し替えてから再配置する。
# show_until() は current_time を進めず、下の anchor("fx_end") まで表示する。
stamp = Object(asset("images/shape_starburst.png"))
stamp <= resize(sx=0.2, sy=0.2)
stamp.compute()
stamp.show_until("fx_end") <= move(x=0.85, y=0.2, anchor="center")

# delete(): 映像だけをオーバーレイから外し、音声はミックスに残す
voice = Object(asset("video/clip_with_audio.mp4"))
voice.show(3) <= delete()
voice <= avolume(0.4)

# obj * n: 映像 repeat（loop フィルタ）と音声 arepeat（aloop）が対で付く。
# bakeable な op（resize / スライスの trim）は一切付けない。付けると
# チェックポイントが挟まって source が未生成の中間物になり、aloop の size が
# probe 不能時の 192kHz フォールバックへ落ちる＝キャッシュの有無で
# 生成コマンドが変わってしまう（素材そのままなら 48kHz で常に同じ）。
clip = Object(asset("video/clip_with_audio.mp4")) * 2   # 5.545s × 2 = 11.09s
clip.time(11.09) <= move(x=0.3, y=0.5, anchor="center")

anchor("fx_end")
