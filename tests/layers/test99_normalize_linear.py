from scriptvedit import *

# normalize_audio(mode="linear")（setup は tests/projects.py の test99）の dry_run 表記:
#   main  … 正規化は一定の増幅 volume=<MEASURED_GAIN>dB → aresample → alimiter。
#           動的な loudnorm は出ない。増幅量は測定して初めて決まるので dry_run では
#           表記（_LINEAR_GAIN_PLACEHOLDER）になる
#   cache … 同じ音声グラフを音声だけ・全編・null 出力で流す測定パス
#           （末尾が loudnorm=print_format=json。-loglevel info / -nostats）。
#           鍵は __cache__/artifacts/loudness/<鍵>.json
# 部分レンダ（render_kwargs の start=1, end=3）でも測定パスは全編（-t 4）を測り、
# main だけが -ss 1 -t 2 で窓を切る。
bgm = Object(asset("audio/bgm_loop.mp3"))
bgm.time(4) <= avolume(0.3)

# 2本目の音声（2秒目から）。amix で1本に混ぜた後に正規化が掛かる
voice = Object(asset("audio/bgm_loop.mp3"))
voice @ 2
voice.time(2)
