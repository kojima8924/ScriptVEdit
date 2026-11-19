# -*- coding: utf-8 -*-
"""JSON リグの最小実行例。--dry-run では Blender は起動しない。"""
import argparse
from pathlib import Path

from scriptvedit import Project

parser = argparse.ArgumentParser()
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()
folder = Path(__file__).resolve().parent
p = Project()
p.configure(width=384, height=384, fps=24, background_color="#e5e8ec")
p.layer(str(folder / "actor.py"))
if not args.dry_run:
    (folder / "output").mkdir(exist_ok=True)
p.render(str(folder / "output" / "preview.mp4"), dry_run=args.dry_run)
