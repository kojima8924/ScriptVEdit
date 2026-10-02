# assemble_from: from_point（1点から出て集まる）/ duration（集まった後は保持）
from scriptvedit import *

src = Object(asset("images/shape_figure.png"))  # 集合元（消費される）
result = Object(asset("images/shape_figure.png"))
result.time(3) <= assemble_from(src, max_pixels=600, speed=80, gravity=-100, seed=3,
                                from_point=(-300, 150), duration=1.6)
result <= move(x=0.5, y=0.5, anchor="center")
