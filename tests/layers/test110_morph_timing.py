# morph_to: delay / duration（変形の前後を最初・最後のコマで保持）と fit の明示
from scriptvedit import *

img1 = Object(asset("images/shape_badge.png"))
img2 = Object(asset("images/banner_wide.png"))
img1.time(3) <= morph_to(img2, delay=0.5, duration=1.5, fit=True)
