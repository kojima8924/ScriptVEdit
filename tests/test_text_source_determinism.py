# -*- coding: utf-8 -*-
"""テキスト Object の合成ソースIDが決定的であること。

レイヤー .py は Plan/Render で複数回 exec される。合成ソースID
（`text://<hash>.txt`）の材料に lambda をそのまま入れると
`<function <lambda> at 0x...>` の**メモリアドレス**が混ざり、
実行のたびに別 Object と判定されて
「レイヤーの構造が Plan と Render で一致しません」で落ちる。

CPython がたまたま同じ番地を再利用していたため長く潜伏し、
Python 3.13 の CI で実際に顕在化した。二度と潜伏させないため固定する。
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import pytest  # noqa: E402

from scriptvedit import Project, counter, text, typewriter  # noqa: E402


@pytest.fixture()
def project():
    p = Project()
    p.configure(width=320, height=180, fps=10)
    return p


def test_lambda_params_do_not_leak_object_identity(project):
    """同じ式を別々の lambda で渡しても同じ合成ソースIDになる"""
    a = text("値", x=0.5, y=lambda u: 0.2 + 0.6 * u, size=48).source
    b = text("値", x=0.5, y=lambda u: 0.2 + 0.6 * u, size=48).source
    assert a == b, f"lambda のアドレスが混ざっている: {a} != {b}"
    assert "0x" not in a


def test_different_expressions_still_differ(project):
    """式が違えば ID も違う（潰しすぎていないこと）"""
    a = text("値", x=0.5, y=lambda u: 0.2 + 0.6 * u, size=48).source
    b = text("値", x=0.5, y=lambda u: 0.9 * u, size=48).source
    assert a != b


def test_typewriter_and_counter_are_deterministic_too(project):
    """typewriter / counter も同じ規約（3ファクトリとも同型のIDを作る）"""
    tw_a = typewriter("あいう", cps=10, x=0.5, y=lambda u: u, size=40).source
    tw_b = typewriter("あいう", cps=10, x=0.5, y=lambda u: u, size=40).source
    assert tw_a == tw_b and "0x" not in tw_a

    c_a = counter(0, 10, x=lambda u: u, y=0.5, size=40).source
    c_b = counter(0, 10, x=lambda u: u, y=0.5, size=40).source
    assert c_a == c_b and "0x" not in c_a


def test_layer_reexecution_keeps_the_same_structure(tmp_path):
    """レイヤーを複数回 exec しても構造署名が一致する（本番の失敗経路）"""
    layer = tmp_path / "anim_text.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "t = text('値', x=0.5, y=lambda u: 0.2+0.6*u, size=48, border=3)\n"
        "t.time(3)\n",
        encoding="utf-8")
    p = Project()
    p.configure(width=320, height=180, fps=10, background_color="black")
    p.layer(str(layer))
    # Plan/Render の構造照合は render 内部で行われる。
    # 非決定なら RuntimeError（構造が一致しません）になる
    p.render(str(tmp_path / "o.mp4"), dry_run=True)
