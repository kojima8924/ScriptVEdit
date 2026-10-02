from scriptvedit import *
# subtitles / karaoke: alpha=1（透明キャンバスへ文字のアルファごと描く）+ fontsdir
s = subtitles(here("test55_subs.srt"), style="FontName=Meiryo,FontSize=28",
              fontsdir=here("test112_fonts"))
s.time(3)
k = karaoke([(0.0, 3.0, "カラオケ")], style={"size": 44, "margin_v": 200},
            fontsdir=here("test112_fonts"))
k.time(3) @ 0
