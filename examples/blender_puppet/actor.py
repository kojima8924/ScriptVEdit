# -*- coding: utf-8 -*-
"""首の回転と瞬きを持つ、外部画像なしの技術デモ。"""
from scriptvedit import here, move, puppet_clip

actor = puppet_clip(here("rig.json"), here("motion.json"), size=(384, 384))
actor.time(1.5) <= move(x=.5, y=.5)
