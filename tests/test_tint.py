# -*- coding: utf-8 -*-
"""tint(color, amount, mode=): 色の塗り替え Effect（アルファは変えない）。

白い線画を任意の色にする。color_shift（色相・彩度・明度）では白 → 赤が作れないので、
色ごとに PNG を描き分ける代わりに1枚の PNG から作れるようにした。
定数は lutrgb、amount が式なら geq。bakeable。
"""

import shutil
import subprocess
import types

import pytest

import scriptvedit as sv
from scriptvedit import gte, if_, ramp, tint
from scriptvedit.cache import _is_bakeable, _op_fingerprint_str
from scriptvedit.filters.video import _build_effect_filters

_NO_FFMPEG = shutil.which("ffmpeg") is None
needs_ffmpeg = pytest.mark.skipif(_NO_FFMPEG, reason="ffmpeg が無い環境")


def _chain(effect, start=0, dur=2):
    obj = types.SimpleNamespace(effects=[effect], transforms=[], media_type="image",
                                source="x.png")
    filters, pad = _build_effect_filters(obj, start, dur)
    assert pad is None          # キャンバスは広げない
    return filters


def test_constant_multiply_uses_lutrgb():
    assert _chain(tint("red")) == [
        "format=rgba",
        "lutrgb=r='clip(round(val*1.0)\\,0\\,255)':g='clip(round(val*0.0)\\,0\\,255)'"
        ":b='clip(round(val*0.0)\\,0\\,255)'"]
    # amount=0.5: 係数は 1 - 0.5×(1 - color/255)（式の経路と同じ順序の演算）
    f = _chain(tint("#ff8000", 0.5))[1]
    assert "r='clip(round(val*1.0)\\," in f and "b='clip(round(val*0.5)\\," in f
    assert f"g='clip(round(val*{1 - 0.5 * (1 - 128 / 255)!r})\\," in f
    # アルファには触れない（lutrgb の a は既定の素通し）
    assert ":a=" not in f


def test_constant_fill_adds_color():
    f = _chain(tint("#0080ff", 0.25, mode="fill"))[1]
    assert f == ("lutrgb=r='clip(round(val*0.75+0.0)\\,0\\,255)':g='clip(round(val*0.75+32.0)\\,0\\,255)'"
                 ":b='clip(round(val*0.75+63.75)\\,0\\,255)'")


def test_expr_amount_uses_geq_with_T_and_keeps_alpha():
    f = _chain(tint("red", amount=lambda u: u), start=3, dur=2)[1]
    # geq は値を切り捨てるので round() で包む（定数の lutrgb 経路と同じ丸め）
    assert f.startswith("geq=r='round(r(X\\,Y)*(1-clip(clip((T-3)/2\\,0\\,1)\\,0\\,1)*0.0))'")
    assert "g='round(g(X\\,Y)*(1-clip(clip((T-3)/2\\,0\\,1)\\,0\\,1)*1.0))'" in f
    assert f.endswith(":a='alpha(X\\,Y)'")
    # 秒の式（ramp）も使える。fill は元の色から color へ寄せる
    f = _chain(tint("yellow", amount=ramp(1.0, 1.5), mode="fill"), start=0, dur=4)[1]
    a = "clip(clip(((clip(T-0\\,0\\,4)-1.0)/0.5)\\,0\\,1)\\,0\\,1)"
    assert f"b='round(b(X\\,Y)*(1-{a})+{a}*0)'" in f


def test_bakeable_and_key_is_color_normalized():
    assert _is_bakeable("effect", tint("red"))
    # 同じ色は書き方が違っても同じ鍵（同一出力なら同一鍵）
    assert _op_fingerprint_str(tint("red")) == _op_fingerprint_str(tint("#FF0000"))
    assert _op_fingerprint_str(tint("red")) == _op_fingerprint_str(tint("0xff0000", 1.0))
    assert _op_fingerprint_str(tint("red")) != _op_fingerprint_str(tint("red", 0.5))
    assert _op_fingerprint_str(tint("red")) != _op_fingerprint_str(tint("red", mode="fill"))


@pytest.mark.parametrize("call, match", [
    (lambda: tint("nosuchcolor"), "未対応の色名"),
    (lambda: tint("#12345"), "16進カラー"),
    (lambda: tint(None), "色名か16進"),
    (lambda: tint("red", 1.5), "amount"),
    (lambda: tint("red", -0.1), "amount"),
    (lambda: tint("red", mode="screen"), "mode"),
])
def test_errors(call, match):
    with pytest.raises(ValueError, match=match):
        call()


def test_describe_lists_tint_as_bakeable_effect():
    entry = [e for e in sv.describe(name="tint")["effects"] if e["name"] == "tint"][0]
    assert entry["bakeable"] is True
    assert entry["category"] == "視覚効果"
    assert "tint" in sv.__all__


def _pixel(chain, src_rgba, frame=0, seconds=1):
    """一様な色 src_rgba（0xRRGGBBAA）の板へ chain を掛け、frame 枚目の中央の (r,g,b,a) を返す"""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=c={src_rgba}:s=16x16:r=10:d={seconds},format=rgba",
         "-vf", ",".join(chain), "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        check=True, capture_output=True, timeout=60).stdout
    size = 16 * 16 * 4
    off = frame * size + (8 * 16 + 8) * 4
    return tuple(out[off:off + 4])


@needs_ffmpeg
def test_real_pixels_constant():
    # 白 → 赤（アルファはそのまま）
    assert _pixel(_chain(tint("red")), "0xFFFFFF80") == (255, 0, 0, 128)
    # 灰色の濃淡は保たれる / 黒は黒のまま
    assert _pixel(_chain(tint("red")), "0x808080FF") == (128, 0, 0, 255)
    assert _pixel(_chain(tint("red")), "0x000000FF") == (0, 0, 0, 255)
    # amount=0 は元のまま
    assert _pixel(_chain(tint("red", 0)), "0x4080C0FF") == (64, 128, 192, 255)
    # 暗くする: tint("black", 0.5)
    assert _pixel(_chain(tint("black", 0.5)), "0xFFFFFFFF") == (128, 128, 128, 255)
    # fill: 黒い線画も color になる
    assert _pixel(_chain(tint("#00ff00", mode="fill")), "0x00000040") == (0, 255, 0, 64)


@needs_ffmpeg
def test_real_pixels_expr_amount_changes_over_time():
    """amount=u（1 秒・10 枚）: 0 枚目は白のまま、5 枚目は半分、最後はほぼ赤"""
    chain = _chain(tint("red", amount=lambda u: u), start=0, dur=1)
    first = _pixel(chain, "0xFFFFFF80", frame=0)
    mid = _pixel(chain, "0xFFFFFF80", frame=5)
    last = _pixel(chain, "0xFFFFFF80", frame=9)
    assert first == (255, 255, 255, 128)
    assert mid[0] == 255 and 120 <= mid[1] <= 135 and mid[1] == mid[2] and mid[3] == 128
    assert last[0] == 255 and last[1] <= 30 and last[3] == 128


@needs_ffmpeg
@pytest.mark.parametrize("mode", ["multiply", "fill"])
def test_real_pixels_constant_and_expr_round_the_same_way(mode):
    """同じ amount なら、定数（lutrgb）と式（geq）で同じ画素になる。

    geq は式の値を切り捨てるので、丸めないと定数経路（round つき）と 1 階調ずれ、
    時間で変わる tint の終点が定数の tint と一致しなかった（白 × #808080 × 0.5 が
    192 と 191）。
    """
    for color, amount, src in [("#808080", 0.5, "0xFFFFFFFF"),
                               ("#808080", 1.0, "0xFFFFFFFF"),
                               ("#3399cc", 1.0, "0xFFFFFF80"),
                               ("#3399cc", 0.3, "0xC8641EFF"),
                               ("#ff8000", 0.75, "0x7F7F7FFF"),
                               ("black", 0.5, "0xFFFFFFFF")]:
        const = _pixel(_chain(tint(color, amount, mode=mode)), src)
        expr = _pixel(_chain(tint(color, amount=lambda u: if_(gte(u, 0), amount, 0), mode=mode)), src)
        assert expr == const, (color, amount, src, const, expr)
    # multiply で amount が 1 に達した白は、指定色そのものへ着地する
    end = _pixel(_chain(tint("#3399cc", amount=lambda u: if_(gte(u, 0), 1, 0), mode=mode)), "0xFFFFFFFF")
    assert end == (0x33, 0x99, 0xCC, 255)


def test_filter_sources_have_no_invalid_escape():
    """フィルタを組むモジュールに不正なエスケープ（非 raw 文字列の "\\,"）が無い。

    Python 3.12 以降は SyntaxWarning、将来は構文エラーになる。
    """
    import os
    import warnings
    import scriptvedit.filters.audio as fa
    import scriptvedit.filters.video as fv
    for mod in (fv, fa):
        path = os.path.abspath(mod.__file__)
        with open(path, encoding="utf-8") as f:
            src = f.read()
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            compile(src, path, "exec")
