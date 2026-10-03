# -*- coding: utf-8 -*-
"""時間だけで決まる不透明度（fade / opacity）を、コマごとに1回だけ評価して掛ける経路。

多点の keyframes_sec（三角の山を並べた不透明度など）は native fade（入りと出の単純な
ランプだけ）にならず、以前は geq（式を画素ごとに評価）へ落ちていた。1080p では式が
単純でも毎秒6コマ前後しか出ず、点が多いほど更に遅くなった（実測: 1080p・10 秒の静止画に
56 点で 85 秒。113 秒の見本で 771 秒）。時間だけの式は sendcmd の [expr] でコマごとに
1回だけ評価し、colorchannelmixer の aa（アルファの倍率）へ送る（同じ素材で 0.5 秒前後。
点の数に依らない。filters/video.py の _alpha_mul_filters）。
  (a) 経路の選び方: 定数 / native fade / コマごとの評価 / geq（画素ごとの変数・random）。
      100 等分の格子の間に収まる細い谷は native fade へ近似しない（以前は谷が消えた）
  (b) 中間物の鍵: 経路が変わった op だけ版が入る（native fade・定数の opacity は据え置き）
  (c) 画素: 色は変わらず、アルファは geq と最大1階調差（geq は切り捨て、
      colorchannelmixer は四捨五入）・Python で計算した値とも最大1階調差
  (d) 本レンダ: 焼く経路（チェックポイント）と live（開始が 0 より後の動画。tpad の
      クローンが先に流れる）の両方で、各コマの不透明度が時刻どおり（コマ1枚ずれると
      三角の山の傾きで約85階調ずれる）
  (e) 時間: 1080p で点の数を 8 → 128 に増やしても書き出し時間がほぼ増えない
"""
import shutil
import subprocess
import time
import types

import pytest

import scriptvedit as sv
from scriptvedit import (Expr, ease_in_out_sine, fade, keyframes_sec, opacity, ramp,
                         random)
from scriptvedit import cache as cache_mod
from scriptvedit.cache import _alpha_cmd_sigs, _checkpoint_cache_path
from scriptvedit.context import _exec_stack, activate, current_project
from scriptvedit.expr import _UValue
from scriptvedit.filters.video import _GEQ_RGB, _build_effect_filters, _expr_is_time_only, _u_expr

_NO_FFMPEG = shutil.which("ffmpeg") is None


def _need_ffmpeg():
    if _NO_FFMPEG:
        pytest.skip("ffmpeg が無い環境")


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


def _tri(n, dur):
    """0..dur 秒に三角の山を並べた n 点（0, 1, 0, 1, ...）"""
    return [(round(dur * i / (n - 1), 6), float(i % 2)) for i in range(n)]


# 2 秒に 0.1 秒ずつの山（30fps で 1 区間 3 コマ。1 コマで約85階調動く）
_TRI = _tri(21, 2.0)
# 入りと出だけの単純なフェード（native fade になる）
_IN_OUT = ((0, 0), (0.25, 1), (1.75, 1), (2, 0))


def _chain(effect, start=0, dur=2, prefix="fx"):
    obj = types.SimpleNamespace(effects=[effect], transforms=[], media_type="image",
                                source="x.png")
    filters, pad = _build_effect_filters(obj, start, dur, label_prefix=prefix)
    assert pad is None
    return filters


# --- (a) 経路の選び方 -----------------------------------------------------------

def test_multi_point_keyframes_use_per_frame_command():
    f = _chain(fade(keyframes_sec(*_TRI)), start=1.5)
    # 掛けるのは planar の gbrap（packed の rgba のまま overlay へ渡すと、自動変換が
    # アルファ 128〜254 を1階調上げる。geq の出力も gbrap だった）
    assert f[:2] == ["format=rgba", "format=gbrap"]
    # sendcmd の宛先はインスタンス名（型名で送るとグラフ中の全 colorchannelmixer に届く）。
    # 経過秒（コマの時刻 T から）は先頭で1回だけ st(0,…) に置き、式の中は ld(0) で読む
    assert f[2].startswith(
        "sendcmd=c='0 [expr] colorchannelmixer@fxe0 aa "
        "if(gte(st(0\\\\,clip(T-1.5\\\\,0\\\\,2))\\\\,0)\\\\,clip(")
    assert f[2].endswith("\\\\,0\\\\,1)\\\\,0)'")
    assert f[3] == "colorchannelmixer@fxe0=aa=0.0"
    assert len(f) == 4 and not any("geq" in x for x in f)
    # 式の区切りはオプション値の分だけ二重にエスケープする
    assert f[2].count("clip(T-") == 1 and "ld(0)" in f[2]
    assert "\\," not in f[2].replace("\\\\,", "")


def test_simple_in_out_stays_native_fade():
    assert _chain(fade(keyframes_sec(*_IN_OUT)), start=1.5) == [
        "format=rgba", "fade=t=in:st=1.5:d=0.25:alpha=1",
        "fade=t=out:st=3.25:d=0.25:alpha=1"]


# 6 秒の入りと出の間に、4.02〜4.08 秒だけ一瞬消える谷（100 等分の格子 = 0.06 秒刻みは
# 4.02 と 4.08 で 1 を拾うだけなので、以前は入りと出の native fade に近似されて谷が消えた）
_DIP = ((0, 0), (0.25, 1), (4.02, 1), (4.05, 0), (4.08, 1), (5.75, 1), (6, 0))


def test_narrow_dip_is_not_approximated_by_native_fade():
    f = _chain(fade(keyframes_sec(*_DIP)), dur=6)
    assert not any(x.startswith("fade=t=") for x in f)
    assert f[2].startswith("sendcmd=c='0 [expr] colorchannelmixer@fxe0 aa ")


def test_narrow_dip_is_drawn():
    """谷の途中のコマ（4.0333 秒。約 0.56）が実際に薄くなる"""
    _need_ffmpeg()
    frames = _render_alpha_board(_chain(fade(keyframes_sec(*_DIP)), dur=6), 6)
    k = 121                       # 121/30 = 4.0333 秒
    v = 1 - (k / _FPS - 4.02) / 0.03
    assert abs(frames[k][4 * 255 + 3] - 255 * v) <= 1, (frames[k][4 * 255 + 3], 255 * v)
    # 谷の外は不透明のまま
    assert frames[115][4 * 255 + 3] == 255


def test_other_time_only_exprs_use_per_frame_command():
    for eff in (fade(lambda u: u),                       # 全尺のフェードイン（以前は geq）
                fade(lambda u: u.triangle(3)),           # 振動系（mod）
                fade(ramp(0.2, 0.6) * (1 - ramp(0.5, 0, from_end=True))),
                fade(keyframes_sec((0, 0), (1, 1), (2, 0), easing=ease_in_out_sine)),
                opacity(lambda u: 0.3 + 0.7 * u)):
        f = _chain(eff)
        assert f[1] == "format=gbrap", f
        assert f[2].startswith("sendcmd=c='0 [expr] colorchannelmixer@fxe0 aa "), f
        assert f[3].startswith("colorchannelmixer@fxe0=aa="), f
    # 初期値は u=0 の値（0 秒より前のコマが来たときに使われる。geq も u=0 の値）
    assert _chain(opacity(lambda u: 0.3 + 0.7 * u))[3] == "colorchannelmixer@fxe0=aa=0.3"


def test_constants_use_plain_colorchannelmixer():
    # fade の定数は以前 geq（出力は gbrap）だったので gbrap で掛ける。0..1 へ clip（geq と同じ）
    assert _chain(fade(0.5)) == ["format=rgba", "format=gbrap", "colorchannelmixer=aa=0.5"]
    assert _chain(fade(1.5)) == ["format=rgba", "format=gbrap", "colorchannelmixer=aa=1.0"]
    # opacity の定数は従来どおり（rgba のまま）
    assert _chain(opacity(0.4)) == ["format=rgba", "colorchannelmixer=aa=0.4"]


class _PixelExpr(Expr):
    """画素の位置に依る式（テスト用。中身を辿れない Expr の派生）"""

    def to_ffmpeg(self, u_expr):
        return "(X/W)"

    def eval_at(self, u_value):
        return 0.5


def test_pixel_dependent_exprs_stay_geq():
    # random は geq では画素ごとに別の値（コマに1回の評価では意味が変わる）
    f = _chain(fade(lambda u: u * random(1)))
    assert f == ["format=rgba", f"{_GEQ_RGB}:a='alpha(X\\,Y)*clip((clip((T-0)/2\\,0\\,1)*random(1))\\,0\\,1)'"]
    f = _chain(opacity(_PixelExpr()))
    assert f == ["format=rgba", f"{_GEQ_RGB}:a='alpha(X\\,Y)*clip((X/W)\\,0\\,1)'"]
    assert not _expr_is_time_only(_PixelExpr())
    assert not _expr_is_time_only(sv.Var("X"))
    assert _expr_is_time_only(keyframes_sec(*_TRI))
    assert _expr_is_time_only(sv.Var("u") * 2 + sv.sin(sv.Var("u")))


def test_instance_names_unique_per_effect_and_input():
    obj = types.SimpleNamespace(
        effects=[fade(keyframes_sec(*_TRI)), opacity(lambda u: u)],
        transforms=[], media_type="image", source="x.png")
    f, _ = _build_effect_filters(obj, 0, 2, label_prefix="fx3")
    joined = ",".join(f)
    assert "colorchannelmixer@fx3e0 aa" in joined and "colorchannelmixer@fx3e0=aa=" in joined
    assert "colorchannelmixer@fx3e1 aa" in joined and "colorchannelmixer@fx3e1=aa=" in joined


# --- (b) 中間物の鍵 ---------------------------------------------------------------

def test_key_version_only_where_output_changed():
    def sigs(eff, dur=2):
        return _alpha_cmd_sigs([("effect", eff)], dur)
    on = [f"acmd={cache_mod._ALPHA_CMD_VER}"]
    # geq（切り捨て）→ colorchannelmixer（四捨五入）へ移ったもの
    assert sigs(fade(keyframes_sec(*_TRI))) == on
    assert sigs(fade(lambda u: u)) == on
    assert sigs(fade(0.5)) == on
    assert sigs(opacity(lambda u: u)) == on
    # 出力が変わらないものは据え置き（同一出力なら同一鍵）
    assert sigs(fade(keyframes_sec(*_IN_OUT))) == []
    assert sigs(opacity(0.5)) == []
    assert sigs(fade(lambda u: u * random(1))) == []
    assert sigs(fade(keyframes_sec(*_TRI)), dur=None) == []
    # 秒で書いた式は尺で経路が変わる（6 秒なら入りと出の native、8 秒なら最後の 2 秒が 0）
    kf = keyframes_sec((0, 0), (0.25, 1), (5.5, 1), (6, 0))
    assert sigs(fade(kf), dur=6) == []
    assert sigs(fade(kf), dur=8) == on


def test_checkpoint_key_moves_only_for_changed_paths(tmp_path, monkeypatch):
    src = tmp_path / "a.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)

    def key(eff):
        return _checkpoint_cache_path(str(src), [("effect", eff)], 2, 30)
    tri = fade(keyframes_sec(*_TRI))
    native = fade(keyframes_sec(*_IN_OUT))
    k_tri, k_native = key(tri), key(native)
    monkeypatch.setattr(cache_mod, "_ALPHA_CMD_VER", "next")
    assert key(tri) != k_tri
    assert key(native) == k_native


# --- (c) 画素 ---------------------------------------------------------------------

_W = 256          # x 列のアルファが x（0..255）の板
_FPS = 30


def _render_alpha_board(chain, seconds):
    """色 (200,100,50)・アルファが x 列の値の板へ chain を掛け、各コマの rgba を返す"""
    src = (f"color=c=0xC86432:s={_W}x2:r={_FPS}:d={seconds},format=rgba,"
           f"geq=r='r(X\\,Y)':g='g(X\\,Y)':b='b(X\\,Y)':a='X'")
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
         "-f", "lavfi", "-i", src, "-vf", ",".join(chain),
         "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        check=True, capture_output=True, timeout=120).stdout
    size = _W * 2 * 4
    return [out[i * size:(i + 1) * size] for i in range(len(out) // size)]


@pytest.mark.parametrize("make", [
    lambda: fade(keyframes_sec(*_TRI)),
    lambda: fade(keyframes_sec((0, 0), (1, 1), (2, 0), easing=ease_in_out_sine)),
    lambda: fade(lambda u: u.triangle(3)),
    lambda: opacity(lambda u: 0.3 + 0.7 * u),
], ids=["tri", "ease", "triangle", "opacity"])
def test_pixels_match_geq_within_one_level(make):
    _need_ffmpeg()
    start, dur = 0.5, 2.0
    eff = make()
    chain = _chain(eff, start=start, dur=dur)
    assert "sendcmd" in chain[2]
    param = eff.params.get("alpha", eff.params.get("value"))
    geq = ["format=rgba",
           f"{_GEQ_RGB}:a='alpha(X\\,Y)*clip({param.to_ffmpeg(_u_expr(start, dur, 'T'))}\\,0\\,1)'"]
    got = _render_alpha_board(chain, 3)
    ref = _render_alpha_board(geq, 3)
    assert len(got) == len(ref) == 3 * _FPS
    for k, (g, r) in enumerate(zip(got, ref)):
        t = k / _FPS
        u = min(1.0, max(0.0, (t - start) / dur))
        v = min(1.0, max(0.0, float(param.eval_at(_UValue(u, dur)))))
        for x in range(_W):
            o = 4 * x
            # 色は変えない
            assert tuple(g[o:o + 3]) == (200, 100, 50), (k, x)
            # アルファ: geq（切り捨て）と最大1階調、Python の値とも最大1階調
            assert abs(g[o + 3] - r[o + 3]) <= 1, (k, x, g[o + 3], r[o + 3])
            assert abs(g[o + 3] - x * v) <= 1, (k, x, g[o + 3], x * v)


def _inline_elapsed_chain(chain, param, start, dur):
    """経過秒を式の中で毎回 clip(T-開始,0,表示秒) と書く形（st/ld を使う前の形）に差し替える"""
    raw = f"clip({param.to_ffmpeg(_u_expr(start, dur, 'T'))},0,1)".replace("\\,", ",")
    old = list(chain)
    old[2] = f"sendcmd=c='0 [expr] colorchannelmixer@fxe0 aa {raw.replace(',', chr(92) * 2 + ',')}'"
    return old


@pytest.mark.parametrize("make", [
    lambda: fade(keyframes_sec(*_TRI)),
    lambda: fade(keyframes_sec((0, 0), (1, 1), (2, 0), easing=ease_in_out_sine)),
    lambda: opacity(lambda u: 0.3 + 0.7 * u.triangle(3)),
], ids=["tri", "ease", "triangle"])
def test_elapsed_stored_once_sends_identical_values(make):
    """経過秒を先頭で1回だけ st(0,…) に置き ld(0) で読む形は、式の中で毎回経過秒を書く形と
    コマごとにビット単位で同じ値を送る（出力のバイト列が一致する）。

    sendcmd の [expr] はコマごとに式を構文解析し直すので、手間は式の長さに比例する
    （16x16・18000 コマで 128 点の keyframes_sec が 15.7 秒 → 12.7 秒、開始 3.5 秒なら
    23.1 秒 → 12.8 秒。8 点は 2.9 秒）。短い方の形を使う（u を1回しか使わない式は
    st/ld で包むと長くなるので、毎回書く形のまま）。
    """
    _need_ffmpeg()
    start, dur = 0.5, 2.0
    eff = make()
    chain = _chain(eff, start=start, dur=dur)
    param = eff.params.get("alpha", eff.params.get("value"))
    old = _inline_elapsed_chain(chain, param, start, dur)
    stored = "ld(0)" in chain[2]
    assert stored == (old[2] != chain[2])
    assert len(chain[2]) <= len(old[2])
    # 経過秒を何度も使う keyframes は st/ld、u を1回だけ使う三角波は毎回書く形
    assert stored == (eff.name == "fade")
    st_form = list(chain)
    if not stored:
        st_form[2] = st_form[2].replace(
            "aa clip(", "aa if(gte(st(0\\\\,clip(T-0.5\\\\,0\\\\,2.0))\\\\,0)\\\\,clip(", 1)
        st_form[2] = st_form[2].replace("clip((T-0.5)/2.0\\\\,0\\\\,1)", "(ld(0)/2.0)")
        st_form[2] = st_form[2][:-1] + "\\\\,0)'"
        assert "ld(0)" in st_form[2] and "T-0.5)/" not in st_form[2]
    ref = _render_alpha_board(old, 3)
    assert _render_alpha_board(chain, 3) == ref
    assert _render_alpha_board(st_form, 3) == ref


def test_elapsed_stored_once_shortens_long_keyframes():
    # 128 点・開始 3.5 秒: 経過秒を約250回書いていた分だけ短くなる
    start, dur = 3.5, 600.0
    eff = fade(keyframes_sec(*_tri(128, dur)))
    chain = _chain(eff, start=start, dur=dur)
    old = _inline_elapsed_chain(chain, eff.params["alpha"], start, dur)
    assert old[2].count("clip(T-") > 250 and chain[2].count("clip(T-") == 1
    assert len(chain[2]) < 0.8 * len(old[2]), (len(chain[2]), len(old[2]))


# --- (d) 本レンダ（焼く経路と live） -------------------------------------------------

def _make_inputs(tmp_path):
    img = tmp_path / "gray.png"
    # 動画は AVI（タイムベース 1/30）にして各コマの時刻を k/30 ちょうどにする
    # （mkv の 1/1000 だと時刻が ms に丸まり、0.1 秒の山の傾きで最大1階調ほど動く）
    vid = tmp_path / "gray.avi"
    for args, out in ((["-frames:v", "1"], img), (["-c:v", "ffv1", "-t", "3"], vid)):
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", f"color=c=0x808080:s=32x16:r={_FPS}:d=3", *args, str(out)],
            check=True, capture_output=True, timeout=60)
    return img, vid


def test_render_alpha_follows_time_baked_and_live(tmp_path):
    """左の静止画は fade を焼き（チェックポイント）、右の動画は 0.5 秒開始の live（-fade）。

    出力は連番 PNG（背景は透明）なので、各コマの中央のアルファが不透明度そのもの。
    """
    _need_ffmpeg()
    img, vid = _make_inputs(tmp_path)
    layer = tmp_path / "l_alpha.py"
    layer.write_text(
        "from scriptvedit import *\n"
        f"TRI = {_TRI!r}\n"
        f"a = Object({str(img)!r})\n"
        "a @ 0\n"
        "a.time(2) <= move(x=0.25, y=0.5, anchor='center') & fade(keyframes_sec(*TRI))\n"
        f"b = Object({str(vid)!r})\n"
        "b @ 0.5\n"
        "b.time(2) <= move(x=0.75, y=0.5, anchor='center') & -fade(keyframes_sec(*TRI))\n",
        encoding="utf-8")
    p = sv.Project()
    p.configure(width=64, height=16, fps=_FPS, duration=3)
    p.layer(str(layer), priority=1)
    dry = p.render(str(tmp_path / "o.png"), dry_run=True)
    # 焼く側はチェックポイントのコマンド、live 側は本レンダのコマンドにコマごとの評価が入る
    assert any("colorchannelmixer@fxe0" in " ".join(c) for c in dry["cache"].values())
    assert "colorchannelmixer@fx2e1" in " ".join(dry["main"])
    p.render(str(tmp_path / "o.png"))
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
         "-i", str(tmp_path / "o_%05d.png"), "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        check=True, capture_output=True, timeout=60).stdout
    size = 64 * 16 * 4
    frames = [raw[i * size:(i + 1) * size] for i in range(len(raw) // size)]
    assert len(frames) == 3 * _FPS
    tri = keyframes_sec(*_TRI)

    def want(t):
        return 255 * min(1.0, max(0.0, float(tri.eval_at(_UValue(min(t / 2, 1.0), 2.0)))))

    def alpha(frame, x):
        return frame[(8 * 64 + x) * 4 + 3]
    # 許す差: live（右）は四捨五入の 1 階調。焼いた側（左）は中間物が packed の bgra なので、
    # 本レンダの overlay の手前の自動変換（bgra → yuva420p）がアルファ 128〜254 を更に
    # 1 階調上げる（FFmpeg 8.0 の既存の挙動。geq で焼いていた頃も同じ）ので 2 階調。
    # コマが1枚ずれると約85階調ずれるので、どちらでも十分に見分けられる
    for k, fr in enumerate(frames):
        t = k / _FPS
        # 表示区間の境目のコマは enable の端なので外す
        if 1 <= k <= 2 * _FPS - 1:
            assert abs(alpha(fr, 16) - want(t)) <= 2, (k, alpha(fr, 16), want(t))
        elif k > 2 * _FPS:
            assert alpha(fr, 16) == 0, k
        if 15 + 1 <= k <= 75 - 1:
            assert abs(alpha(fr, 48) - want(t - 0.5)) <= 1, (k, alpha(fr, 48), want(t - 0.5))
        elif k < 15 or k > 75:
            assert alpha(fr, 48) == 0, k


# --- (e) 時間 ---------------------------------------------------------------------

def _null_render_seconds(chain, seconds=3):
    t0 = time.perf_counter()
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=c=black:s=1920x1080:r={_FPS}:d={seconds},format=rgba",
         "-vf", ",".join(chain), "-f", "null", "-"],
        check=True, capture_output=True, timeout=300)
    return time.perf_counter() - t0


def test_many_points_do_not_slow_down_1080p():
    """1080p・3 秒（90 コマ）: 8 点でも 128 点でも数秒以内で、点を増やしてもほぼ変わらない。

    geq（画素ごとの式）のままだと同じ条件で 8 点が約16秒、128 点が約25秒かかった
    （実測 FFmpeg 8.0。コマごとの評価は 0.14 秒と 0.21 秒）。上限は遅い CI でも余裕のある値。
    """
    _need_ffmpeg()
    few = _chain(fade(keyframes_sec(*_tri(8, 3.0))), dur=3)
    many = _chain(fade(keyframes_sec(*_tri(128, 3.0))), dur=3)
    assert "sendcmd" in few[2] and "sendcmd" in many[2]
    t_few = _null_render_seconds(few)
    t_many = _null_render_seconds(many)
    assert t_few < 8, t_few
    assert t_many < 8, t_many
    assert t_many < 2 * t_few + 1.5, (t_few, t_many)
