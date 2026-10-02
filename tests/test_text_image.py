# -*- coding: utf-8 -*-
"""text_image()（文字を透過 PNG に焼く）と、text 系 + 終端フレーム Effect の黙殺の解消

固定すること:
- 描画結果の画素（色の区間がその色で描かれる・太さで画素が増える・行送り・揃え・縁取り）
- 行の縦位置と画像の高さが字に依らない（書体のメトリクスで決まる）
- 書式（区間のリスト / 簡易マークアップ）の解釈とエスケープ規則
- 自動折り返しと行頭禁則
- 豆腐（フォントに無い字）の検出
- キャッシュ鍵の安定（フォントのパスに依らない・効かない引数で割れない・残骸の自己修復）
- p.audit() が画像の中の文字を画面上の実寸で検査する
- text() / counter() / typewriter() に morph_to / explode_to / assemble_from を掛けると
  ValueError（以前は黙って無視）。text().compute() も分かる ValueError

フォントは Windows と CI（Linux）で入っているものが違う。既定の日本語フォントは
text() と同じ _resolve_font で探し、無ければ該当テストだけ skip する。
"""
import glob
import os
import shutil
import subprocess
import sys
import warnings

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

pytest.importorskip("PIL", reason="Pillow が無い環境")
from PIL import Image  # noqa: E402

from scriptvedit import (  # noqa: E402
    Effect, Object, Project, asset, assemble_from, counter, explode_to, morph_to,
    resize, scale, text, text_image, typewriter,
)
from scriptvedit import textimage as ti  # noqa: E402
from scriptvedit.context import _exec_stack, activate, current_project  # noqa: E402
from scriptvedit.manifest import describe  # noqa: E402
from scriptvedit.text import _resolve_font  # noqa: E402

_HAS_FFMPEG = (shutil.which("ffmpeg") is not None
               and shutil.which("ffprobe") is not None)

_VARIABLE_FONT_CANDIDATES = (
    "C:/Windows/Fonts/NotoSerifJP-VF.ttf",
    "C:/Windows/Fonts/NotoSansJP-VF.ttf",
    "C:/Windows/Fonts/bahnschrift.ttf",
    "C:/Windows/Fonts/CascadiaMono.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-VF.ttf",
    "/System/Library/Fonts/SFNS.ttf",
)

_LATIN_ONLY_FONT_CANDIDATES = (
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
)


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


@pytest.fixture
def jp_font():
    """既定の日本語フォント（text() と同じ探し方。無ければ skip）"""
    try:
        return _resolve_font(None)
    except FileNotFoundError as e:
        pytest.skip(f"日本語フォントが無い環境: {str(e).splitlines()[0]}")


def _variable_font():
    """wght 軸を持つ可変フォント（無ければ skip）"""
    cands = list(_VARIABLE_FONT_CANDIDATES)
    for pattern in ("/usr/share/fonts/**/*VF*.ttf", "/usr/share/fonts/**/*wght*.ttf"):
        cands.extend(sorted(glob.glob(pattern, recursive=True)))
    for path in cands:
        if os.path.exists(path) and any(
                a[0] == "wght" for a in ti._fvar_axes(path, 0)):
            return path
    pytest.skip("wght 軸を持つ可変フォントが見つからない環境")


def _latin_only_font():
    """日本語グリフを持たない欧文フォント（無ければ skip）"""
    for path in _LATIN_ONLY_FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    pytest.skip("日本語グリフを持たない欧文フォントが見つからない環境")


def _rgba(obj):
    return Image.open(obj.source).convert("RGBA")


def _ink(obj):
    """不透明度の総和（描かれた量）"""
    return sum(_rgba(obj).getchannel("A").getdata())


def _columns(img, pred):
    """pred(r, g, b, a) を満たす画素がある x 座標の集合"""
    w, h = img.size
    px = img.load()
    return {x for x in range(w) for y in range(h) if pred(*px[x, y])}


def _is_red(r, g, b, a):
    return a > 200 and r > 200 and g < 60 and b < 60


def _is_white(r, g, b, a):
    return a > 200 and r > 200 and g > 200 and b > 200


def _codes(findings):
    return [f["code"] for f in findings]


# --- 戻り値と基本 ------------------------------------------------------------

def test_returns_image_object_with_png(jp_font):
    o = text_image("画像の文字", size=48)
    assert isinstance(o, Object)
    assert o.media_type == "image"
    assert o.source.replace("\\", "/").startswith("__cache__/artifacts/textimage/")
    img = Image.open(o.source)
    assert img.format == "PNG" and img.mode == "RGBA"
    assert img.size == (o._text_image["width"], o._text_image["height"])
    # 偶数寸法・四隅は透明
    assert img.size[0] % 2 == 0 and img.size[1] % 2 == 0
    assert img.getpixel((0, 0))[3] == 0
    assert _ink(o) > 0


def test_duration_sets_time(jp_font):
    o = text_image("3秒", size=40, duration=3)
    assert o.duration == 3.0


def test_registered_in_describe():
    d = describe()
    entry = next(e for e in d["factories"] if e["name"] == "text_image")
    assert entry["category"] == "テキスト・字幕"
    assert entry["params"]["align"]["choices"] == ["left", "center", "right"]
    assert entry["params"]["missing"]["choices"] == ["error", "warn", "ignore"]


# --- 画素: 色の区間 ----------------------------------------------------------

def test_span_color_is_drawn_in_that_color(jp_font):
    """赤の区間だけが赤く、白の区間の右に並ぶ"""
    o = text_image([("白白白", {}), ("赤赤赤", {"color": "#FF0000"})], size=60)
    img = _rgba(o)
    red = _columns(img, _is_red)
    white = _columns(img, _is_white)
    assert red and white
    assert min(red) > max(white) - 3, "赤の区間は白の区間の右に来る"
    # 全部白なら赤い画素は1つも無い
    plain = text_image("白白白赤赤赤", size=60)
    assert not _columns(_rgba(plain), _is_red)
    # 区間に分けても字送りは変わらない（幅が同じ）
    assert plain._text_image["width"] == o._text_image["width"]


def test_edge_pixels_keep_fill_color(jp_font):
    """縁（半透明）の画素の RGB が黒へ寄らない（合成後に黒ずまない）"""
    img = _rgba(text_image("縁", size=80, color="#FFFFFF"))
    partial = [p for p in img.getdata() if 0 < p[3] < 255]
    assert partial
    assert all(p[:3] == (255, 255, 255) for p in partial)


def test_color_alpha_and_ffmpeg_forms(jp_font):
    full = text_image("透", size=60, color="white")
    half = text_image("透", size=60, color="white@0.5")
    assert 0.4 < _ink(half) / _ink(full) < 0.6
    assert text_image("透", size=60, color="0xFFFFFF").source == full.source


# --- 画素: 太さ・縁取り・影・下地 --------------------------------------------

def test_variable_font_weight_increases_ink():
    font = _variable_font()
    _tag, lo, _default, hi = next(a for a in ti._fvar_axes(font, 0) if a[0] == "wght")
    thin = text_image("HHHH", size=80, font=font, weight=lo)
    bold = text_image("HHHH", size=80, font=font, weight=hi)
    assert thin.source != bold.source
    assert _ink(bold) > _ink(thin) * 1.2


def test_span_weight_only_changes_that_span():
    font = _variable_font()
    _tag, lo, _default, hi = next(a for a in ti._fvar_axes(font, 0) if a[0] == "wght")
    plain = text_image("HHHH", size=80, font=font, weight=lo, padding=20)
    mixed = text_image([("HH", {}), ("HH", {"weight": hi})], size=80, font=font,
                       weight=lo, padding=20)
    a = _rgba(plain).getchannel("A")
    b = _rgba(mixed).getchannel("A")
    # 左の2字ぶん（先頭 1/4 の列）は同じ画素、全体では太い方が多い
    quarter = (0, 0, plain._text_image["width"] // 4, plain._text_image["height"])
    assert list(a.crop(quarter).getdata()) == list(b.crop(quarter).getdata())
    assert sum(b.getdata()) > sum(a.getdata())


def test_variation_name(jp_font):
    font = _variable_font()
    from PIL import ImageFont
    names = [n.decode() if isinstance(n, bytes) else n
             for n in ImageFont.truetype(font, 40).get_variation_names()]
    if not names:
        pytest.skip("名前つきインスタンスの無い可変フォント")
    o = text_image("HH", size=60, font=font, weight=names[-1])
    assert _ink(o) > 0
    with pytest.raises(ValueError, match="インスタンスはありません"):
        text_image("HH", size=60, font=font, weight="NoSuchInstance")


def test_weight_on_static_font_is_error(jp_font):
    if ti._fvar_axes(jp_font, 0):
        pytest.skip("既定フォントが可変フォントの環境")
    with pytest.raises(ValueError, match="可変フォントではありません"):
        text_image("太さ", size=48, weight=700)
    with pytest.raises(ValueError, match="可変フォントではありません"):
        text_image("太さ", size=48, weight="Bold")


def test_weight_out_of_range_is_error():
    font = _variable_font()
    hi = next(a for a in ti._fvar_axes(font, 0) if a[0] == "wght")[3]
    with pytest.raises(ValueError, match="範囲外"):
        text_image("HH", size=48, font=font, weight=hi + 100)


def test_border_shadow_background_add_pixels(jp_font):
    kw = dict(size=60, padding=30)
    plain = text_image("装飾", **kw)
    bordered = text_image("装飾", border=4, border_color="#00FF00", **kw)
    shadowed = text_image("装飾", shadow=(6, 6), shadow_color="#0000FF", **kw)
    blurred = text_image("装飾", shadow_blur=4, shadow_color="#0000FF", **kw)
    plate = text_image("装飾", background="#102030", **kw)
    assert plain._text_image["width"] == bordered._text_image["width"]
    assert _ink(bordered) > _ink(plain)
    assert _columns(_rgba(bordered), lambda r, g, b, a: a > 200 and g > 200 and r < 60)
    assert _columns(_rgba(shadowed), lambda r, g, b, a: a > 200 and b > 200 and r < 60)
    assert _ink(blurred) > _ink(plain)
    # 下地はキャンバス全面
    assert _rgba(plate).getpixel((1, 1)) == (0x10, 0x20, 0x30, 255)
    # 文字は縁取りの上に乗る（中心付近の白が残る）
    assert _columns(_rgba(bordered), _is_white)


def test_clipping_warns(jp_font):
    with pytest.warns(UserWarning, match="端が切れます"):
        text_image("切れる", size=60, border=12, padding=0)


# --- 行: 縦位置・行送り・揃え ------------------------------------------------

def test_height_does_not_depend_on_glyphs(jp_font):
    """背の低い字だけでも画像の高さは同じ（drawtext の text() はここがガタつく）"""
    h = {s: text_image(s, size=64)._text_image["height"]
         for s in ("ー・、", "国語", "null", "yjgp", "Ａ")}
    assert len(set(h.values())) == 1, h


def test_baseline_is_shared_across_spans(jp_font):
    """同じ行の区間は同じベースラインに乗る（字の下端がそろう）"""
    a = _rgba(text_image("国", size=64, padding=10))
    b = _rgba(text_image([("国", {}), ("国", {"color": "#FF0000"})], size=64, padding=10))
    assert a.getbbox()[3] == b.getbbox()[3]
    assert a.getbbox()[1] == b.getbbox()[1]


def test_line_spacing_in_pixels(jp_font):
    size = 50
    one = text_image("行", size=size)._text_image["height"]
    h15 = text_image("行\n行", size=size, line_spacing=1.5)._text_image["height"]
    h30 = text_image("行\n行", size=size, line_spacing=3.0)._text_image["height"]
    # 2行目のベースラインは line_spacing × size だけ下（偶数丸めで ±1）
    assert abs((h15 - one) - 1.5 * size) <= 1
    assert abs((h30 - one) - 3.0 * size) <= 1
    # 実際の画素: 2つの「行」の上端の間隔が行送りに一致する
    img = _rgba(text_image("国\n国", size=size, line_spacing=2.0)).getchannel("A")
    rows = [y for y in range(img.size[1])
            if any(img.getpixel((x, y)) for x in range(img.size[0]))]
    gaps = [b for a, b in zip(rows, rows[1:]) if b - a > 1]
    assert len(gaps) == 1
    assert abs((gaps[0] - rows[0]) - 2.0 * size) <= 1


def test_align_moves_short_line(jp_font):
    def left_edge_of_second_line(align):
        o = text_image("長い長い長い行\n短", size=40, align=align, padding=10)
        img = _rgba(o).getchannel("A")
        w, h = img.size
        lower = img.crop((0, h // 2, w, h))
        return lower.getbbox()[0], w
    lx, w = left_edge_of_second_line("left")
    cx, _ = left_edge_of_second_line("center")
    rx, _ = left_edge_of_second_line("right")
    assert lx < cx < rx
    assert lx < w * 0.15 and rx > w * 0.7


def test_crlf_is_one_newline(jp_font):
    assert (text_image("a\r\nb", size=40).source
            == text_image("a\nb", size=40).source)


def test_canvas_fixes_size_and_rejects_overflow(jp_font):
    a = text_image("短", size=60, canvas=(600, 200), align="center")
    b = text_image("長い文字列", size=60, canvas=(600, 200), align="center")
    assert Image.open(a.source).size == Image.open(b.source).size == (600, 200)
    box = _rgba(a).getbbox()
    assert abs((box[0] + box[2]) / 2 - 300) <= 3, "横は中央"
    with pytest.raises(ValueError, match="収まりません"):
        text_image("長い文字列が入らない", size=60, canvas=(200, 200))


# --- 折り返し・行頭禁則 ------------------------------------------------------

def test_wrap_respects_max_width_and_kinsoku(jp_font):
    s = "ああああ、いいいい。うううう（ええ）おおおお、かかかか。"
    for max_width in (150, 170, 190, 210, 250, 330):
        o = text_image(s, size=40, max_width=max_width, padding=0)
        info = o._text_image
        lines = info["content"].split("\n")
        assert len(lines) > 1
        assert info["content_width"] <= max_width + 0.5
        assert "".join(lines) == s, "字は増えも減りもしない"
        for line in lines:
            assert line[0] not in "、。）", (max_width, lines)


def test_wrap_latin_words_and_spaces(jp_font):
    o = text_image("alpha beta gamma delta", size=40, max_width=230, padding=0)
    lines = o._text_image["content"].split("\n")
    assert len(lines) > 1
    for line in lines:
        assert line == line.strip(), "折り返しで生じた行頭・行末の空白は捨てる"
        for word in line.split(" "):
            assert word in ("alpha", "beta", "gamma", "delta"), "語の途中では切らない"
    # max_width より長い語は1字ずつに割る
    long = text_image("abcdefghijklmnopqrstuvwxyz", size=40, max_width=200, padding=0)
    assert len(long._text_image["content"].split("\n")) > 1
    assert long._text_image["content_width"] <= 200.5


def test_wrap_keeps_span_colors(jp_font):
    o = text_image([("ああああああ", {}), ("赤赤赤赤赤赤", {"color": "#FF0000"})],
                   size=40, max_width=200)
    assert o._text_image["lines"] >= 3
    assert _columns(_rgba(o), _is_red)


def test_single_glyph_wider_than_max_width_is_error(jp_font):
    with pytest.raises(ValueError, match="max_width"):
        text_image("幅", size=100, max_width=20)


def test_no_wrap_without_max_width(jp_font):
    o = text_image("あ" * 40, size=40)
    assert o._text_image["lines"] == 1


# --- 書式: 区間のリスト / マークアップ ---------------------------------------

def test_markup_equals_span_list(jp_font):
    spans = text_image([("犯人は、", {}), ("正規表現が1本", {"color": "red"}),
                        ("だった", {})], size=48)
    marked = text_image("犯人は、{red|正規表現が1本}だった", size=48, markup=True)
    assert spans.source == marked.source


def test_markup_parse_and_escapes():
    p = ti._parse_markup
    assert p("f", "a{red|b}c") == [("a", None), ("b", "red"), ("c", None)]
    # 区切りは最初の | だけ。区間の中の | は文字
    assert p("f", "{red|a|b}") == [("a|b", "red")]
    # エスケープは \{ \} \\ の3つ。それ以外の \ はそのまま
    assert p("f", r"\{x\} \\ \d") == [("{x} \\ \\d", None)]
    assert p("f", r"{red|\}\{}") == [("}{", "red")]
    assert p("f", "{color=red, weight=900|x}") == [("x", "color=red, weight=900")]


@pytest.mark.parametrize("src,msg", [
    ("a{red|b", "閉じていません"),
    ("a}b", "対応する"),
    ("{red}", r"\{書式\|文字\}"),
    ("{red|a{blue|b}}", "入れ子"),
    ("{|a}", "書式が空"),
])
def test_markup_errors(src, msg):
    with pytest.raises(ValueError, match=msg):
        ti._parse_markup("text_image", src)


def test_markup_is_opt_in(jp_font):
    """markup=False（既定）では波括弧はそのまま文字として出る"""
    o = text_image("{red|x}", size=40)
    assert o._text_image["content"] == "{red|x}"
    assert not _columns(_rgba(o), _is_red)


def test_markup_style_tokens_and_named_styles(jp_font):
    st = ti._markup_style("f", "red, size=80", {})
    assert st == {"color": "red", "size": 80}
    styles = {"em": {"color": "#FF0000", "size": 80}}
    assert ti._markup_style("f", "em", styles) == styles["em"]
    a = text_image("普通{em|強調}", size=40, markup=True, styles=styles)
    b = text_image([("普通", {}), ("強調", "em")], size=40, styles=styles)
    c = text_image([("普通", {}), ("強調", {"color": "#FF0000", "size": 80})], size=40)
    assert a.source == b.source == c.source
    assert a._text_image["size_min"] == 40 and a._text_image["size_max"] == 80
    with pytest.raises(ValueError, match="未知の書式キー"):
        text_image("{colour=red|x}", size=40, markup=True)
    with pytest.raises(ValueError, match="styles= にありません"):
        text_image([("x", "nope")], size=40)


def test_span_font_does_not_inherit_weight(jp_font):
    """区間が font を変えたとき、基本書式の weight は引き継がない"""
    font = _variable_font()
    hi = next(a for a in ti._fvar_axes(font, 0) if a[0] == "wght")[3]
    if ti._fvar_axes(jp_font, 0):
        pytest.skip("既定フォントが可変フォントの環境")
    o = text_image([("AB", {}), ("あい", {"font": jp_font})], size=48, font=font,
                   weight=hi)
    assert _ink(o) > 0


@pytest.mark.parametrize("kwargs,exc,msg", [
    (dict(content=""), ValueError, "空です"),
    (dict(content="   "), ValueError, "空白だけ"),
    (dict(content=123), TypeError, "区間のリスト"),
    (dict(content=[("a", {"colour": "red"})]), ValueError, "未知の書式キー"),
    (dict(content=[("a", {"size": 0})]), ValueError, "size"),
    (dict(content=[("a",)]), TypeError, "2要素"),
    (dict(content=[("a", {})], markup=True), ValueError, "markup=True"),
    (dict(content="a", align="middle"), ValueError, "align"),
    (dict(content="a", size=0), ValueError, "size"),
    (dict(content="a", size=lambda u: 40), ValueError, "size"),
    (dict(content="a", color="notacolor"), ValueError, "色名"),
    (dict(content="a", border=-1), ValueError, "border"),
    (dict(content="a", shadow=3), None, None),
    (dict(content="a", shadow=(1, 2, 3)), ValueError, "shadow"),
    (dict(content="a", missing="skip"), ValueError, "missing"),
    (dict(content="a", font_index=-1), ValueError, "font_index"),
    (dict(content="a", canvas=300), ValueError, "canvas"),
    (dict(content="a", line_spacing=0), ValueError, "line_spacing"),
])
def test_argument_validation(jp_font, kwargs, exc, msg):
    kwargs.setdefault("size", 40)
    if exc is None:
        text_image(**kwargs)
        return
    with pytest.raises(exc, match=msg):
        text_image(**kwargs)


def test_missing_font_file_is_error():
    with pytest.raises(FileNotFoundError, match="指定フォントが見つかりません"):
        text_image("a", size=40, font="/no/such/font.ttf")


def test_ttc_font_index(jp_font):
    if not jp_font.lower().endswith(".ttc"):
        pytest.skip("既定フォントが .ttc ではない環境")
    from PIL import ImageFont
    try:
        ImageFont.truetype(jp_font, 40, index=1)
    except OSError:
        pytest.skip("書体が1つだけの .ttc フォント")
    a = text_image("書体 Aa", size=48, font_index=0)
    b = text_image("書体 Aa", size=48, font_index=1)
    assert a.source != b.source
    with pytest.raises(ValueError, match="フォントを開けません"):
        text_image("書体", size=48, font_index=999)


def test_without_pillow_is_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)
    with pytest.raises(RuntimeError, match="Pillow が必要"):
        text_image("a", size=40)


# --- 豆腐（フォントに無い字）-------------------------------------------------

def test_missing_glyph_is_error_by_default():
    font = _latin_only_font()
    with pytest.raises(ValueError, match="豆腐") as ei:
        text_image("abc 漢字", size=48, font=font)
    assert "U+6F22" in str(ei.value) and os.path.basename(font) in str(ei.value)
    # 欧文だけなら通る
    assert _ink(text_image("abc", size=48, font=font)) > 0


def test_missing_glyph_warn_and_ignore():
    font = _latin_only_font()
    with pytest.warns(UserWarning, match="豆腐"):
        o = text_image("abc 漢", size=48, font=font, missing="warn")
    assert o._text_image["missing"] == ["漢"]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        o2 = text_image("abc 漢", size=48, font=font, missing="ignore")
    assert o2._text_image["missing"] == ["漢"]


def test_missing_glyph_fixed_by_span_font(jp_font):
    font = _latin_only_font()
    o = text_image([("abc ", {}), ("漢字", {"font": jp_font})], size=48, font=font)
    assert o._text_image["missing"] == []


def test_default_font_has_no_missing_glyphs(jp_font):
    o = text_image("日本語のかなカナ漢字、。「」ー！？ ABC abc 123", size=40)
    assert o._text_image["missing"] == []


# --- キャッシュ鍵 ------------------------------------------------------------

def test_cache_key_is_stable_and_content_addressed(jp_font, tmp_path):
    kw = dict(size=48, border=2)
    a = text_image("鍵の安定", **kw)
    assert text_image("鍵の安定", **kw).source == a.source
    # 文字・書式が変われば鍵も変わる
    assert text_image("鍵の安定!", **kw).source != a.source
    assert text_image("鍵の安定", size=48, border=3).source != a.source
    assert text_image("鍵の安定", color="#FFFF00", **kw).source != a.source
    # 64 と 64.0、明示した既定フォントは同じ鍵
    assert text_image("鍵の安定", size=48.0, border=2).source == a.source
    assert text_image("鍵の安定", font=jp_font, **kw).source == a.source
    # フォントは内容指紋: 別の場所へコピーしても鍵は同じ（生パスを混ぜない）
    copied = tmp_path / ("copied" + os.path.splitext(jp_font)[1])
    shutil.copyfile(jp_font, copied)
    assert text_image("鍵の安定", font=str(copied), **kw).source == a.source


def test_ineffective_args_do_not_split_key(jp_font):
    """効かない引数で鍵を割らない（同一出力なら同一鍵）"""
    a = text_image("一行", size=48)
    assert text_image("一行", size=48, max_width=4000).source == a.source
    assert text_image("一行", size=48, line_spacing=3.0).source == a.source
    assert text_image("一行", size=48, border_color="#FF0000").source == a.source
    assert text_image("一行", size=48, shadow_color="#FF0000").source == a.source
    assert text_image("一行", size=48, missing="warn").source == a.source
    assert text_image("一行", size=48, styles={"x": {"color": "red"}}).source == a.source


def test_truncated_cache_is_regenerated(jp_font):
    o = text_image("壊れたキャッシュ", size=48)
    good = open(o.source, "rb").read()
    with open(o.source, "wb") as f:
        f.write(good[:len(good) // 2])
    ti._WRITTEN_MEMO.clear()
    o2 = text_image("壊れたキャッシュ", size=48)
    assert o2.source == o.source
    assert open(o.source, "rb").read() == good
    # 0 バイトでも同じ
    open(o.source, "wb").close()
    ti._WRITTEN_MEMO.clear()
    text_image("壊れたキャッシュ", size=48)
    assert open(o.source, "rb").read() == good
    # ファイルが消えていても、プロセス内メモだけで命中扱いにしない
    os.remove(o.source)
    text_image("壊れたキャッシュ", size=48)
    assert open(o.source, "rb").read() == good


def test_no_temp_files_left(jp_font):
    o = text_image("残骸なし", size=48)
    d = os.path.dirname(o.source)
    assert not [n for n in os.listdir(d) if not n.endswith(".png") or ".tmp" in n]


# --- p.audit() ---------------------------------------------------------------

def _mk(width=1920, height=1080):
    p = Project()
    p.configure(width=width, height=height, fps=30)
    return p


def test_audit_clean_text_image_has_no_text_findings(jp_font):
    p = _mk()
    text_image("読みやすい文字", size=64, border=3).time(2)
    codes = _codes(p.audit(quiet=True))
    for code in ("text-too-small", "text-no-decoration", "text-overflow",
                 "font-missing-glyph"):
        assert code not in codes


def test_audit_small_and_undecorated(jp_font):
    p = _mk()
    text_image("小さい", size=24).time(2)
    findings = p.audit(quiet=True)
    small = [f for f in findings if f["code"] == "text-too-small"]
    assert small and small[0]["severity"] == "warning"
    assert "text_image('小さい')" in small[0]["message"]
    assert "text-no-decoration" in _codes(findings)
    # 本文には小さめ（32〜44px）は info
    p2 = _mk()
    text_image("やや小さい", size=40, shadow=(2, 2)).time(2)
    f2 = p2.audit(quiet=True)
    assert [f["severity"] for f in f2 if f["code"] == "text-too-small"] == ["info"]
    assert "text-no-decoration" not in _codes(f2)


def test_audit_uses_smallest_span(jp_font):
    p = _mk()
    text_image([("大きい", {}), ("注", {"size": 20})], size=64, border=3).time(2)
    assert "text-too-small" in _codes(p.audit(quiet=True))


def test_audit_scales_with_resize_and_scale(jp_font):
    """画面上の実寸で見る: resize / scale の倍率を掛ける"""
    p = _mk()
    text_image("縮小", size=60, border=3).time(2) <= resize(sx=0.4, sy=0.4)
    small = [f for f in p.audit(quiet=True) if f["code"] == "text-too-small"]
    assert small and small[0]["severity"] == "warning"
    assert "24px" in small[0]["message"]
    # 小さく描いて拡大すれば指摘されない
    p2 = _mk()
    text_image("拡大", size=24, border=1).time(2) <= scale(2.5)
    assert "text-too-small" not in _codes(p2.audit(quiet=True))
    # アニメーションは最大の時点で見る（0 から始まるポップインを誤検出しない）
    p3 = _mk()
    text_image("ポップ", size=60, border=3).time(2) <= scale(lambda u: u)
    assert "text-too-small" not in _codes(p3.audit(quiet=True))


def test_audit_scale_survives_checkpoint_bake(jp_font, tmp_path):
    """レイヤー経由（dry_run でチェックポイントが resize を焼く）でも倍率を見失わない"""
    layer = tmp_path / "layer_ti.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "t = text_image('焼かれる縮小', size=60, border=3)\n"
        "t.time(1) <= resize(sx=0.4, sy=0.4)\n", encoding="utf-8")
    p = _mk(640, 1080)
    p.layer(str(layer))
    findings = p.audit(quiet=True)
    small = [f for f in findings if f["code"] == "text-too-small"]
    assert small and "24px" in small[0]["message"]


def test_audit_overflow_uses_real_width(jp_font):
    p = _mk(640, 360)
    text_image("あ" * 30, size=40, border=2).time(2)
    assert "text-overflow" in _codes(p.audit(quiet=True))
    p2 = _mk(640, 360)
    text_image("あ" * 30, size=40, border=2, max_width=560).time(2)
    assert "text-overflow" not in _codes(p2.audit(quiet=True))
    # 縮小して収まるなら指摘しない
    p3 = _mk(640, 360)
    text_image("あ" * 30, size=40, border=2).time(2) <= resize(sx=0.4, sy=0.4)
    assert "text-overflow" not in _codes(p3.audit(quiet=True))


def test_audit_reports_missing_glyph():
    font = _latin_only_font()
    p = _mk()
    text_image("abc 漢", size=64, border=3, font=font, missing="ignore").time(2)
    f = [f for f in p.audit(quiet=True) if f["code"] == "font-missing-glyph"]
    assert f and "漢" in f[0]["message"]


def test_audit_ignores_plain_images():
    """申告の無い画像は従来どおり文字の検査を受けない"""
    p = _mk()
    Object(asset("images/shape_badge.png")).time(2)
    codes = _codes(p.audit(quiet=True))
    assert "text-too-small" not in codes and "text-no-decoration" not in codes


# --- text 系 + 終端フレーム Effect（以前は黙殺）------------------------------

def _text_factories():
    return [
        ("text", lambda: text("文字", size=48)),
        ("typewriter", lambda: typewriter("文字", size=48)),
        ("counter", lambda: counter(0, 10, size=48)),
    ]


@pytest.mark.parametrize("name", ["text", "typewriter", "counter"])
def test_text_with_terminal_effect_is_value_error(jp_font, name):
    make = dict(_text_factories())[name]
    Project().configure(width=640, height=360, fps=30)
    target = Object(asset("images/shape_badge.png"))
    with pytest.raises(ValueError, match="text_image") as ei:
        make().time(1) <= explode_to()
    assert "explode_to" in str(ei.value)
    with pytest.raises(ValueError, match="text_image"):
        make().time(1) <= morph_to(target)
    source = Object(asset("images/shape_badge.png"))
    with pytest.raises(ValueError, match="text_image"):
        make().time(1) <= assemble_from(source)
    # 連結（EffectChain）の中にあっても同じ
    from scriptvedit import fade
    with pytest.raises(ValueError, match="text_image"):
        make().time(1) <= fade(0.5) & explode_to()


def test_text_as_morph_target_or_assemble_source_is_value_error(jp_font):
    Project().configure(width=640, height=360, fps=30)
    with pytest.raises(ValueError, match="text_image"):
        morph_to(text("的", size=48))
    with pytest.raises(ValueError, match="text_image"):
        assemble_from(text("元", size=48))


def test_text_terminal_effect_is_caught_at_plan_time_too(jp_font):
    """effects を直接触る経路でも、計画時に黙って None を返さない"""
    p = Project()
    p.configure(width=640, height=360, fps=30)
    t = text("直接", size=48)
    t.time(1)
    t.effects.append(Effect("explode_to"))
    with pytest.raises(ValueError, match="text_image"):
        p._plan_object_checkpoints(t)
    # 終端フレーム Effect が無い text は従来どおり対象外（None）
    t2 = text("普通", size=48)
    t2.time(1)
    assert p._plan_object_checkpoints(t2) is None


def test_text_compute_is_clear_value_error(jp_font):
    Project().configure(width=640, height=360, fps=30)
    with pytest.raises(ValueError, match="text_image") as ei:
        text("素材化", size=48).compute()
    assert "compute()" in str(ei.value)
    with pytest.raises(ValueError, match="text_image"):
        counter(0, 5, size=48).compute(1.0)


def test_text_image_accepts_terminal_effects_and_is_valid_target(jp_font):
    """text_image は画像なので、入力にも target / source にも使える"""
    p = Project()
    p.configure(width=640, height=360, fps=10)
    a = text_image("前", size=80, border=3, canvas=(240, 160), align="center")
    b = text_image("後", size=80, border=3, canvas=(240, 160), align="center")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        a.time(0.5) <= morph_to(b)
        c = text_image("粒", size=80, border=3, padding=40)
        c.time(0.5) <= explode_to(max_pixels=500)
        d = text_image("集", size=80, border=3, padding=40)
        e = text_image("元", size=80, border=3, padding=40)
        d.time(0.5) <= assemble_from(e, max_pixels=500)
    for obj, kind in ((a, "morph"), (c, "particle"), (d, "particle")):
        plan = p._plan_object_checkpoints(obj)
        assert plan is not None
        assert [s["kind"] for s in plan["steps"]] == [kind]


# --- 実レンダ ----------------------------------------------------------------

def _frame(video, t, out):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", str(video),
         "-frames:v", "1", str(out)], check=True, capture_output=True, timeout=60)
    return Image.open(out).convert("RGB")


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg / ffprobe が無い環境")
def test_real_render_shows_colored_text(jp_font, tmp_path):
    """text_image を置いた動画に、区間の色が実際に映る"""
    layer = tmp_path / "layer_ti_render.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "t = text_image([('白', {}), ('赤', {'color': '#FF0000'})], size=120, padding=20)\n"
        "t.time(0.5) <= move(x=0.5, y=0.5, anchor='center')\n", encoding="utf-8")
    p = Project()
    p.configure(width=640, height=360, fps=10, background_color="black")
    p.layer(str(layer))
    out = tmp_path / "ti.mp4"
    p.render(str(out))
    img = _frame(out, 0.2, tmp_path / "f.png")
    px = img.load()
    reds = [(x, y) for x in range(0, 640, 2) for y in range(0, 360, 2)
            if px[x, y][0] > 180 and px[x, y][1] < 80 and px[x, y][2] < 80]
    whites = [(x, y) for x in range(0, 640, 2) for y in range(0, 360, 2)
              if min(px[x, y]) > 180]
    assert reds and whites
    assert min(x for x, _y in reds) > 320 - 10, "赤の区間は中央より右"
    assert max(x for x, _y in whites) < 320 + 10, "白の区間は中央より左"


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg / ffprobe が無い環境")
def test_real_render_explode_actually_moves_pixels(jp_font, tmp_path):
    """text_image <= explode_to は実際に粒子化される（text() では黙って静止していた）"""
    pytest.importorskip("numpy", reason="morph extras（numpy）が無い環境")
    pytest.importorskip("scipy", reason="morph extras（scipy）が無い環境")
    pytest.importorskip("cv2", reason="morph extras（opencv）が無い環境")
    layer = tmp_path / "layer_ti_explode.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "t = text_image('爆', size=120, border=4, padding=60)\n"
        "t.time(1.0) <= explode_to(max_pixels=1500, speed=300, expand=100, seed=1)"
        " & move(x=0.5, y=0.5, anchor='center')\n", encoding="utf-8")
    p = Project()
    p.configure(width=640, height=360, fps=10, background_color="black")
    p.layer(str(layer))
    out = tmp_path / "ti_explode.mp4"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        p.render(str(out))
    first = _frame(out, 0.0, tmp_path / "e0.png")
    late = _frame(out, 0.4, tmp_path / "e1.png")

    def lit_box(img):
        return img.convert("L").point(lambda v: 255 if v > 25 else 0).getbbox()
    b0, b1 = lit_box(first), lit_box(late)
    assert b0 is not None and b1 is not None
    w0, w1 = b0[2] - b0[0], b1[2] - b1[0]
    assert w1 > w0 + 20, f"粒子が広がっていない（静止表示のまま）: {b0} → {b1}"


# --- レビュー指摘の回帰 ------------------------------------------------------

@pytest.mark.parametrize("s,max_width", [
    ("やめろ！！！！！！！！！！", 200),
    ("やめろ！！！！！！！！！！", 130),
    ("うわああああーーーーーーーーーー", 250),
    ("なんだって…………………………", 200),
    ("そんな、、、、、、、、ばかな。。。。。。", 170),
    ("本当？！？！？！？！？！", 210),
])
def test_wrap_kinsoku_run_does_not_cascade(jp_font, s, max_width):
    """禁則字・長音の連続が1行ぶん以上あっても、1行1字の行を量産しない"""
    o = text_image(s, size=40, max_width=max_width, padding=0)
    info = o._text_image
    lines = info["content"].split("\n")
    assert "".join(lines) == s, "字は増えも減りもしない"
    assert info["content_width"] <= max_width + 0.5
    per_line = max_width // 40
    # 貪欲に詰めた行数 + 追い出しの余裕（1回ぶん）まで
    assert len(lines) <= -(-len(s) // per_line) + 1, lines
    assert sum(1 for line in lines[:-1] if len(line) == 1) == 0, lines


def test_wrap_pull_is_bounded_and_keeps_two_marks(jp_font):
    """「だ！？」までは追い出す。それより長い連続は元の位置で折る"""
    o = text_image("そうなんだ！？", size=40, max_width=240, padding=0)
    assert o._text_image["content"] == "そうなん\nだ！？"
    assert ti._KINSOKU_MAX_PULL == 3
    for ch in "ー～〜…‥":
        assert ch not in ti._KINSOKU_HEAD, "長音・リーダは禁則に入れない"


def test_wrap_keeps_spaces_between_words(jp_font):
    """折り返しても、折り目以外の空白は消えない"""
    for s, max_width in (("設定は .env に書く。", 150), ("設定は .env に書く。", 200),
                         ("edit .gitignore and .env now", 260),
                         ("aaa bbb !", 150), ("use .* here , ok", 150)):
        o = text_image(s, size=40, max_width=max_width, padding=0)
        lines = o._text_image["content"].split("\n")
        assert len(lines) > 1
        assert "".join(lines).replace(" ", "") == s.replace(" ", ""), (s, lines)
        for line in lines:
            assert line in s, f"行の中の空白が消えた・増えた: {line!r}（{s!r}）"
            assert line == line.strip()
    # 追い出された語と禁則字の間の空白も残る
    o = text_image("aaa bbb !", size=40, max_width=150, padding=0)
    assert o._text_image["content"] == "aaa\nbbb !"


def test_wrap_dot_word_is_not_kinsoku(jp_font):
    """半角の語は、先頭が '.' ',' でも行頭に置ける（直前の字を巻き添えにしない）"""
    unit = {"kind": "word", "segs": [(".env", 0)]}
    assert not ti._is_kinsoku_head(unit)
    assert ti._is_kinsoku_head({"kind": "word", "segs": [(".", 0)]})
    assert ti._is_kinsoku_head({"kind": "word", "segs": [("!", 0), ("?", 1)]})
    assert ti._is_kinsoku_head({"kind": "wide", "segs": [("。", 0)]})
    assert not ti._is_kinsoku_head({"kind": "space", "segs": [(" ", 0)]})
    o = text_image("設定は .env に書く。", size=40, max_width=150, padding=0)
    assert o._text_image["content"].split("\n")[0] == "設定は"


def test_glyph_in_cmap_is_not_missing_even_if_it_looks_like_notdef():
    """cmap に在る字は、絵が .notdef と同じでも「無い字」にしない（MS ゴシックの □）"""
    font = "C:/Windows/Fonts/msgothic.ttc"
    if not os.path.exists(font):
        pytest.skip("MS ゴシックのフォントが無い環境")
    o = text_image("チェック□を入れる", size=64, font=font)
    assert o._text_image["missing"] == []
    assert ti._cmap_lookup(font, 0) is not None
    # 本当に無い字（cmap に無い）は検出する
    assert ti._missing_glyphs(None, ["\uffff", "あ"], font, 0) == ["\uffff"]


def test_cmap_lookup_matches_freetype(jp_font):
    """自前で読む cmap が FreeType の判定（.notdef の画素）と食い違わない"""
    from PIL import ImageFont
    has = ti._cmap_lookup(jp_font, 0)
    if has is None:
        pytest.skip("既定フォントの cmap が format 4 / 12 ではない環境")
    font = ImageFont.truetype(jp_font, 40, layout_engine=ImageFont.Layout.BASIC)
    for ch in "あ漢Aa1、。「」ー！？":
        assert has(ord(ch)), ch
    assert not has(0xFFFF)
    # cmap に無い字は、画素でも .notdef と同じ
    notdef = ti._mask_signature(font, "\uffff")
    for cp in (0x0378, 0x0379, 0x10FFFF):
        if not has(cp):
            assert ti._mask_signature(font, chr(cp)) == notdef


def test_missing_glyph_falls_back_to_pixels_without_cmap(monkeypatch):
    """cmap を読めないフォントは、従来どおり画素で判定する"""
    font = _latin_only_font()
    monkeypatch.setattr(ti, "_cmap_lookup", lambda path, index: None)
    with pytest.raises(ValueError, match="豆腐"):
        text_image("abc 漢", size=48, font=font)


def test_empty_span_does_not_split_key(jp_font):
    """字の無い区間は書式を登録しない（同一出力なら同一鍵）"""
    a = text_image("a", size=40)
    assert text_image([("a", {}), ("", {"color": "red"})], size=40).source == a.source
    assert text_image([("", {"size": 90}), ("a", {})], size=40).source == a.source
    b = text_image("a\nb", size=40)
    assert text_image([("a", {}), ("\n", {"color": "red"}), ("b", {})],
                      size=40).source == b.source


def test_named_instance_and_axis_value_share_key():
    """weight="Bold" と、その軸の値（700 等）は同じ絵なので同じ鍵"""
    font = _variable_font()
    from PIL import ImageFont
    names = [n.decode() if isinstance(n, bytes) else n
             for n in ImageFont.truetype(font, 40).get_variation_names()]
    axes, instances = ti._fvar(font, 0)
    if not names or len(names) != len(instances) or len(axes) != 1:
        pytest.skip("wght 軸だけ・名前つきインスタンスありの可変フォントが無い環境")
    name, (value,) = names[-1], instances[-1]
    by_name = text_image("HH", size=80, font=font, weight=name)
    by_axis = text_image("HH", size=80, font=font, weight=value)
    assert by_name.source == by_axis.source
    # 正規化が正しいこと: PIL の set_variation_by_name と同じ画素
    ref = ImageFont.truetype(font, 80, layout_engine=ImageFont.Layout.BASIC)
    ref.set_variation_by_name(name)
    got = ti._load_font("t", ti._import_pil("t"), font, 0, 80, (value,))
    assert bytes(ref.getmask("HH")) == bytes(got.getmask("HH"))
    # 既定の軸の値を明示しても、指定なしと同じ鍵
    default = axes[0][2]
    assert (text_image("HH", size=80, font=font, weight=default).source
            == text_image("HH", size=80, font=font).source)


@pytest.mark.parametrize("src,msg", [
    ("{size=abc|x}", "size は数値"),
    ("{size=|x}", "size は数値"),
    ("{font_index=1.5|x}", "font_index は整数"),
    ("{font_index=x|x}", "font_index は整数"),
])
def test_markup_numeric_errors_name_the_key(jp_font, src, msg):
    with pytest.raises(ValueError, match=msg) as ei:
        text_image(src, size=40, markup=True)
    assert str(ei.value).startswith("text_image:")


def test_styles_must_be_dict(jp_font):
    for bad in (["x"], "r", [("r", {"color": "red"})]):
        with pytest.raises(TypeError, match="styles は") as ei:
            text_image("a", size=40, styles=bad)
        assert str(ei.value).startswith("text_image:")


def test_old_pillow_is_clear_error(monkeypatch):
    """ImageFont.Layout の無い古い Pillow（< 9.1）は AttributeError ではなく案内"""
    from PIL import ImageFont
    monkeypatch.delattr(ImageFont, "Layout")
    with pytest.raises(RuntimeError, match="Pillow 9.1 以上が必要"):
        text_image("a", size=40)


def test_pillow_lower_bound_matches_pyproject():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as f:
        toml = f.read()
    assert f'"Pillow>={ti._PIL_MIN_VERSION}"' in toml
    assert '"Pillow"' not in toml, "下限の無い Pillow が extras に残っている"


def test_audit_overflow_skips_rotated_text_image(jp_font):
    """回転した文字は幅を判定できないので text-overflow を出さない"""
    from scriptvedit import rotate, rotate_to
    long = "回転して幅が変わる" * 3
    p = _mk(640, 360)
    text_image(long, size=60, border=3).time(1) <= rotate(deg=90)
    assert "text-overflow" not in _codes(p.audit(quiet=True))
    p2 = _mk(640, 360)
    text_image(long, size=60, border=3).time(1) <= rotate_to(deg=90)
    assert "text-overflow" not in _codes(p2.audit(quiet=True))
    # 180 度は幅が変わらないので従来どおり指摘する。回転なしも同じ
    p3 = _mk(640, 360)
    text_image(long, size=60, border=3).time(1) <= rotate(deg=180)
    assert "text-overflow" in _codes(p3.audit(quiet=True))
    p4 = _mk(640, 360)
    text_image(long, size=60, border=3).time(1)
    assert "text-overflow" in _codes(p4.audit(quiet=True))


def test_dry_run_uses_text_image_png_as_image_input(jp_font, tmp_path):
    """スナップショットの代わり: text_image を入力にした dry_run のコマンドの形。

    鍵はフォントの内容指紋と Pillow の版を含み環境ごとに変わるので json には
    固定せず、PNG が画像入力になること・終端フレーム Effect の生成物が
    その PNG から作られること・繰り返しても同じ出力になることを確かめる。
    """
    import json
    layer = tmp_path / "layer_ti_dry.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "a = text_image('前', size=80, border=3, canvas=(240, 160), align='center')\n"
        "a.time(1) <= move(x=0.5, y=0.5, anchor='center')\n"
        "b = text_image('粒', size=80, border=3, padding=40)\n"
        "b.time(1) <= explode_to(max_pixels=300) & move(x=0.5, y=0.5, anchor='center')\n",
        encoding="utf-8")

    def run():
        p = Project()
        p.configure(width=640, height=360, fps=10, background_color="black")
        p.layer(str(layer))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return p.render(str(tmp_path / "out.mp4"), dry_run=True)

    first = run()
    main = [str(x).replace("\\", "/") for x in first["main"]]
    pngs = [x for x in main if "/artifacts/textimage/" in x and x.endswith(".png")]
    assert len(pngs) == 1, "素の text_image は PNG がそのまま入力になる"
    assert os.path.isfile(pngs[0]), "dry_run でも PNG は実在する"
    assert main[main.index(pngs[0]) - 1] == "-i"
    # explode_to を掛けた方は粒子の生成物（cache 側で作る）に差し替わって入力になる
    particles = [str(k).replace("\\", "/") for k in first["cache"]
                 if "/artifacts/particle/" in str(k).replace("\\", "/")]
    assert len(particles) == 1, list(first["cache"])
    assert particles[0] in main
    assert json.dumps(run(), default=str) == json.dumps(first, default=str), \
        "同じ入力なら dry_run の出力は変わらない"
