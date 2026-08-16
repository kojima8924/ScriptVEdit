from scriptvedit import *
# 長大フィルタの実レンダ経路（実レンダ専用テスト test95）。
#
# typewriter は1文字につき drawtext を1つ出すため、長い文字列を与えると
# -filter_complex が 4000 文字を超え、ffmpeg.py の _externalize_long_filters が
# 一時ファイル + FFmpeg 8 の `-/filter_complex <path>` 構文へ切り替える。
# この分岐は実行時にしか起きない（dry_run のコマンド列には現れない）ので、
# スナップショットでは踏めない。
tw = typewriter(
    "The quick brown fox jumps over the lazy dog 0123456789 "
    "and keeps typing until the filter graph gets long enough.",
    cps=40, x=0.05, y=0.5, size=14, color="white")
tw.time(2)
