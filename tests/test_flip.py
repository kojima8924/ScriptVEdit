# -*- coding: utf-8 -*-
"""flip Transform（左右反転 hflip / 上下反転 vflip）のテスト

- ファクトリの既定値と入力検証（両方 False は ValueError、bool 以外は TypeError）
- horizontal 省略時の解釈: flip() は左右、flip(vertical=True) は上下だけ（名前どおり）
- フィルタ生成（hflip / vflip / 両方の順序）と、未知の Transform を黙って捨てないこと
- 寸法計算（_get_base_dimensions）が flip で変わらないこと
- Transform なので bakeable（静止画は PNG チェックポイント）で、キャッシュ鍵が向きで分かれること
- describe に Transform として載ること
- 実レンダで画素が実際に反転すること（ffmpeg があるときのみ）
"""
import json
import shutil
import subprocess

import pytest

import scriptvedit as sv
from scriptvedit import Object, Project, Transform, asset, flip, resize, rotate
from scriptvedit.context import _exec_stack, activate, current_project
from scriptvedit.filters.video import _build_transform_filters, _get_base_dimensions
from scriptvedit.manifest import describe

_HAS_FFMPEG = (shutil.which("ffmpeg") is not None
               and shutil.which("ffprobe") is not None)


@pytest.fixture(autouse=True)
def _restore_project_globals():
    """各テスト後に Project の暗黙登録先と実行スタックを戻す"""
    old_current = current_project()
    old_stack = list(_exec_stack)
    activate(None)
    _exec_stack[:] = []
    try:
        yield
    finally:
        activate(old_current)
        _exec_stack[:] = old_stack


def _obj(*transforms):
    """Transform を順に適用した画像 Object（レイヤー外。フィルタ生成の検査用）"""
    Project().configure(width=640, height=360, fps=30)
    o = Object(asset("images/shape_badge.png"))
    for t in transforms:
        o <= t
    return o


# --- ファクトリと入力検証 ---------------------------------------------------

def test_flip_defaults_to_horizontal_only():
    t = flip()
    assert isinstance(t, Transform)
    assert t.name == "flip"
    assert t.params == {"horizontal": True, "vertical": False}


@pytest.mark.parametrize("kwargs,expected", [
    # horizontal 省略: vertical を指定していなければ左右、vertical=True だけなら上下のみ
    ({}, (True, False)),
    ({"vertical": False}, (True, False)),
    ({"vertical": True}, (False, True)),
    ({"horizontal": None, "vertical": True}, (False, True)),
    # 明示した値はその通り
    ({"horizontal": True}, (True, False)),
    ({"horizontal": True, "vertical": True}, (True, True)),
    ({"horizontal": False, "vertical": True}, (False, True)),
])
def test_flip_resolves_omitted_horizontal(kwargs, expected):
    """params には解決後の bool が入る（省略と明示で同じ鍵になる）"""
    t = flip(**kwargs)
    assert (t.params["horizontal"], t.params["vertical"]) == expected


def test_flip_both_false_is_value_error():
    with pytest.raises(ValueError, match="両方 False"):
        flip(horizontal=False, vertical=False)
    with pytest.raises(ValueError, match=r"flip\(vertical=True\)"):
        flip(False)


@pytest.mark.parametrize("kwargs", [
    {"horizontal": 1}, {"vertical": "yes"}, {"vertical": None},
])
def test_flip_rejects_non_bool(kwargs):
    with pytest.raises(TypeError, match="True / False"):
        flip(**kwargs)


# --- フィルタ生成 -----------------------------------------------------------

@pytest.mark.parametrize("kwargs,expected", [
    ({}, ["hflip"]),
    ({"horizontal": True, "vertical": False}, ["hflip"]),
    ({"horizontal": False, "vertical": True}, ["vflip"]),
    # horizontal を省略して vertical=True → 上下だけ（名前どおり）
    ({"vertical": True}, ["vflip"]),
    # 両方は明示する（180度回転と同じ絵）
    ({"horizontal": True, "vertical": True}, ["hflip", "vflip"]),
])
def test_flip_filters(kwargs, expected):
    assert _build_transform_filters(_obj(flip(**kwargs))) == expected


def test_flip_chains_in_written_order():
    """| で連結した他の Transform と記述順どおりに並ぶ"""
    o = _obj(resize(sx=0.5, sy=0.5) | flip() | rotate(deg=90))
    filters = _build_transform_filters(o)
    assert filters[0] == "scale=iw*0.5:ih*0.5"
    assert filters[1] == "hflip"
    assert filters[2] == "format=rgba"
    assert filters[3].startswith("rotate=")


def test_unknown_transform_is_rejected():
    """未知の Transform はフィルタの出ない空振りにせず ValueError"""
    o = _obj()
    o.transforms.append(Transform("no_such_transform"))
    with pytest.raises(ValueError, match="no_such_transform"):
        _build_transform_filters(o)


# describe が公称する全 Transform を実構築して、フィルタ生成が空振りしないこと。
# 必須引数を持つものだけ引数を与える（grid は Object.grid() 経由でのみ生成される）。
_TRANSFORM_FIXTURES = {
    "crop": lambda: sv.crop(x=0, y=0, w=64, h=64),
    "pad": lambda: sv.pad(w=1000, h=1000),
    "rotate": lambda: sv.rotate(deg=30),
}


def test_every_manifest_transform_builds_filters():
    names = [e["name"] for e in describe()["transforms"]]
    assert "flip" in names
    for name in names:
        o = _obj()
        if name == "grid":
            o.grid(2, 2)
        else:
            factory = _TRANSFORM_FIXTURES.get(name) or getattr(sv, name)
            o <= factory()
        assert _build_transform_filters(o), f"{name}: フィルタが生成されない"


# --- 寸法計算 ---------------------------------------------------------------

@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffprobe が無い環境ではスキップ")
@pytest.mark.parametrize("kwargs", [
    {}, {"vertical": True}, {"horizontal": True, "vertical": True},
])
def test_flip_keeps_base_dimensions(kwargs):
    """flip は寸法を変えない（scale の pad サイズ計算が flip 無しと同じになる）"""
    plain = _get_base_dimensions(_obj(resize(sx=0.5, sy=0.5)))
    flipped = _get_base_dimensions(_obj(resize(sx=0.5, sy=0.5) | flip(**kwargs)))
    assert plain[0] is not None
    assert flipped == plain
    # flip の後ろの寸法変化（crop）もそのまま反映される
    cropped = _get_base_dimensions(
        _obj(flip(**kwargs) | sv.crop(x=0, y=0, w=100, h=80)))
    assert cropped == (100, 80)


# --- bakeable / キャッシュ鍵 --------------------------------------------------

def _dry_run_layer(tmp_path, body, name="l_flip.py"):
    layer = tmp_path / name
    layer.write_text("from scriptvedit import *\n" + body, encoding="utf-8")
    p = Project()
    p.configure(width=320, height=180, fps=10)
    p.layer(str(layer), priority=0)
    return p.render(str(tmp_path / "o.mp4"), dry_run=True)


def test_flip_bakes_into_png_checkpoint(tmp_path):
    """静止画 + flip は PNG チェックポイントへ焼かれ、-vf に hflip が載る"""
    cmds = _dry_run_layer(tmp_path, (
        "o = Object(asset('images/shape_badge.png'))\n"
        "o <= flip()\n"
        "o.time(1)\n"))
    ckpts = {k: v for k, v in cmds["cache"].items() if "checkpoint" in k}
    assert len(ckpts) == 1, cmds["cache"]
    path, cmd = next(iter(ckpts.items()))
    assert path.endswith(".png")
    assert cmd[cmd.index("-vf") + 1] == "hflip"
    # 本レンダは焼いたチェックポイントを読み、flip を二重に掛けない
    main = " ".join(cmds["main"])
    assert path in main
    assert "hflip" not in main


def test_flip_direction_changes_cache_key(tmp_path):
    """左右 / 上下 / 両方 でチェックポイントの鍵（パス）が分かれる"""
    paths = []
    for i, args in enumerate(("", "vertical=True",
                              "horizontal=True, vertical=True")):
        cmds = _dry_run_layer(tmp_path, (
            "o = Object(asset('images/shape_badge.png'))\n"
            f"o <= flip({args})\n"
            "o.time(1)\n"), name=f"l_flip{i}.py")
        paths.extend(k for k in cmds["cache"] if "checkpoint" in k)
    assert len(paths) == 3
    assert len(set(paths)) == 3, paths


def test_flip_omitted_and_explicit_share_cache_key(tmp_path):
    """省略形と明示形が同じ絵なら同じ鍵（flip(vertical=True) ≡ flip(False, True)）"""
    def ckpt(args, name):
        cmds = _dry_run_layer(tmp_path, (
            "o = Object(asset('images/shape_badge.png'))\n"
            f"o <= flip({args})\n"
            "o.time(1)\n"), name=name)
        return [k for k in cmds["cache"] if "checkpoint" in k]

    assert ckpt("vertical=True", "l_a.py") == ckpt(
        "horizontal=False, vertical=True", "l_b.py")
    assert ckpt("", "l_c.py") == ckpt("horizontal=True", "l_d.py")


# --- describe ---------------------------------------------------------------

def test_flip_in_describe():
    entry = describe(name="flip")["transforms"]
    assert len(entry) == 1
    e = entry[0]
    assert e["kind"] == "transform"
    assert e["category"] == "変形"
    assert e["effect_names"] == ["flip"]
    assert e["params"]["horizontal"]["default"] is None
    assert e["params"]["vertical"]["default"] is False
    assert e["params"]["horizontal"]["type"] == "bool"
    json.dumps(e, ensure_ascii=False)


# --- 実レンダ ---------------------------------------------------------------

def _rgb_at(path, w, h, x, y):
    """動画の先頭フレームを rgb24 で読み、(x, y) の画素を返す"""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True, timeout=60).stdout
    assert len(raw) == w * h * 3
    i = (y * w + x) * 3
    return tuple(raw[i:i + 3])


def _dominant(px):
    """画素の支配色を 'red' / 'green' / 'blue' / 'dark' で返す（圧縮誤差を吸収）"""
    r, g, b = px
    if max(px) <= 128:
        return "dark"
    return {r: "red", g: "green", b: "blue"}[max(px)]


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe が無い環境ではスキップ")
def test_flip_real_render_moves_pixels(tmp_path):
    """左上=赤 / 右上=青 / 下半分=緑 の画像を flip して、画素が入れ替わること"""
    img = str(tmp_path / "quad.png")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=red:s=32x32",
         "-f", "lavfi", "-i", "color=c=blue:s=32x32",
         "-f", "lavfi", "-i", "color=c=lime:s=64x32",
         "-filter_complex", "[0][1]hstack[top];[top][2]vstack",
         "-frames:v", "1", img],
        check=True, timeout=60)

    def render(flip_args, name):
        layer = tmp_path / f"{name}.py"
        layer.write_text(
            "from scriptvedit import *\n"
            f"o = Object({img!r})\n"
            f"o <= flip({flip_args})\n"
            "o.time(1) <= move(x=0.5, y=0.5, anchor='center')\n",
            encoding="utf-8")
        p = Project()
        p.configure(width=64, height=64, fps=10, background_color="black")
        p.layer(str(layer), priority=0)
        out = str(tmp_path / f"{name}.mp4")
        p.render(out)
        return out

    h = render("", "h")
    # 左右反転: 左上=青 / 右上=赤 / 下=緑
    assert _dominant(_rgb_at(h, 64, 64, 8, 8)) == "blue"
    assert _dominant(_rgb_at(h, 64, 64, 56, 8)) == "red"
    assert _dominant(_rgb_at(h, 64, 64, 32, 56)) == "green"

    v = render("vertical=True", "v")
    # 上下反転: 上=緑 / 左下=赤 / 右下=青
    assert _dominant(_rgb_at(v, 64, 64, 32, 8)) == "green"
    assert _dominant(_rgb_at(v, 64, 64, 8, 56)) == "red"
    assert _dominant(_rgb_at(v, 64, 64, 56, 56)) == "blue"
