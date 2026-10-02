# -*- coding: utf-8 -*-
"""文字まわりの不具合と拡張の回帰テスト。

1. subtitles() / karaoke(): 透明キャンバスへ subtitles フィルタを掛けるとき
   alpha=1 が無く、FFmpeg 8 では文字のアルファが 0 のまま＝何も映らなかった。
   alpha=1 を必ず付ける。fontsdir（同梱フォントのフォルダ）も渡せる。
2. glow() を text() に掛けると白い文字がマゼンタに化けた。gblur / blend は
   パックドの rgba を受け取れず、live 経路では blend が yuva で動いて
   screen 合成が色差にも掛かっていた。入口を format=gbrap に固定する。
3. counter(): 最後のコマで to に届かなかった（3秒・30fps の
   counter(0, 2147483647) の最後が 2123622718）。値の進行度を最後のコマで 1 に
   する。桁区切り・小数・イージング・32ビットを超える値にも対応する。
4. text(): line_spacing / text_align / y_align。
5. p.audit(): 明示した単色の背景だけの上の文字は text-no-decoration を info にする。

実レンダのテストは ffmpeg とフォントが要る（無ければそのテストだけ skip）。
画素は ffmpeg の rawvideo 出力を素の bytes で読んで調べる（PIL / numpy 不要）。
"""
import os
import shutil
import subprocess

import pytest

import scriptvedit as sv
from scriptvedit.context import _exec_stack, activate, current_project
from scriptvedit.text import (
    _build_text_filters, _escape_counter_literal, _parse_counter_format,
    _resolve_font)

W, H, FPS = 640, 360, 30


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


def _need_font():
    try:
        return _resolve_font(None)
    except FileNotFoundError as exc:
        pytest.skip(f"フォントが無い環境: {str(exc).splitlines()[0]}")


def _need_render():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い環境")
    _need_font()


def _mk(**cfg):
    p = sv.Project()
    p.configure(width=W, height=H, fps=FPS, **cfg)
    return p


def _render(tmp_path, name, body, **cfg):
    """レイヤー本文 body を1レイヤーの Project として実レンダし、出力パスを返す"""
    layer = tmp_path / f"{name}.py"
    layer.write_text("from scriptvedit import *\n" + body, encoding="utf-8")
    p = _mk(**cfg)
    p.layer(str(layer))
    out = str(tmp_path / f"{name}.mp4")
    p.render(out)
    return out


def _dry_filter(tmp_path, name, body, **cfg):
    """レイヤー本文 body を dry_run し、-filter_complex の文字列を返す"""
    layer = tmp_path / f"{name}.py"
    layer.write_text("from scriptvedit import *\n" + body, encoding="utf-8")
    p = _mk(**cfg)
    p.layer(str(layer))
    cmd = p.render(str(tmp_path / f"{name}.mp4"), dry_run=True)["main"]
    return cmd[cmd.index("-filter_complex") + 1]


def _frame(path, n):
    """n 枚目（0 始まり）のコマを rgb24 の bytes（W*H*3）で返す"""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vf", f"select=eq(n\\,{n})",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert len(r.stdout) == W * H * 3, f"コマ {n} が取れない: {len(r.stdout)} bytes"
    return r.stdout


def _bright(frame, threshold=160):
    """明るい画素（R/G/B の最大がしきい値以上）の (x, y, r, g, b) の列"""
    out = []
    for i in range(0, len(frame), 3):
        r, g, b = frame[i], frame[i + 1], frame[i + 2]
        if r >= threshold or g >= threshold or b >= threshold:
            idx = i // 3
            out.append((idx % W, idx // W, r, g, b))
    return out


def _luma_diff_count(a, b, threshold=64):
    """2枚のコマで、緑チャンネルの差がしきい値を超える画素の数"""
    return sum(1 for i in range(1, len(a), 3) if abs(a[i] - b[i]) > threshold)


# --- 1. subtitles / karaoke ------------------------------------------------

_SRT = "1\n00:00:00,000 --> 00:00:02,000\n字幕テスト ABC\n"


def _write_srt(tmp_path):
    srt = tmp_path / "subs.srt"
    srt.write_text(_SRT, encoding="utf-8")
    return srt.as_posix()


def test_subtitles_filter_has_alpha(tmp_path):
    """subtitles フィルタに alpha=1 が付く（style・fontsdir より前）"""
    _mk()
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    s = sv.subtitles(_write_srt(tmp_path), style="FontSize=28",
                     fontsdir=str(fonts))
    (f,) = _build_text_filters(s, 0, 2)
    assert f.startswith("subtitles=filename=")
    assert ":alpha=1:fontsdir='" in f
    assert f.endswith(":force_style='FontSize=28'")
    plain = sv.subtitles(_write_srt(tmp_path))
    (f2,) = _build_text_filters(plain, 0, 2)
    assert f2.endswith(":alpha=1")
    assert "fontsdir" not in f2


def test_karaoke_passes_fontsdir(tmp_path):
    _mk()
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    k = sv.karaoke([(0, 1, "うた")], fontsdir=str(fonts))
    (f,) = _build_text_filters(k, 0, 1)
    assert ":alpha=1:fontsdir='" in f


def test_fontsdir_errors(tmp_path):
    _mk()
    srt = _write_srt(tmp_path)
    with pytest.raises(FileNotFoundError, match="fontsdir"):
        sv.subtitles(srt, fontsdir=str(tmp_path / "no_such_dir"))
    with pytest.raises(TypeError, match="fontsdir"):
        sv.subtitles(srt, fontsdir=123)
    with pytest.raises(FileNotFoundError, match="fontsdir"):
        sv.karaoke([(0, 1, "うた")], fontsdir=str(tmp_path / "no_such_dir"))


def test_fontsdir_identity_ignores_location(tmp_path):
    """合成ソースIDは fontsdir の置き場所ではなく中のフォント名で決まる"""
    _mk()
    srt = _write_srt(tmp_path)
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for d in (a, b, c):
        d.mkdir()
    (a / "X.ttf").write_bytes(b"x")
    (b / "X.ttf").write_bytes(b"x")
    (c / "Y.ttf").write_bytes(b"y")
    base = sv.subtitles(srt).source
    sa = sv.subtitles(srt, fontsdir=str(a)).source
    sb = sv.subtitles(srt, fontsdir=str(b)).source
    sc = sv.subtitles(srt, fontsdir=str(c)).source
    assert sa == sb
    assert len({base, sa, sc}) == 3


def test_subtitles_and_karaoke_are_visible(tmp_path):
    """実レンダ: 字幕（下）とカラオケ（中央寄り）の文字が実際に映る"""
    _need_render()
    srt = _write_srt(tmp_path)
    out = _render(
        tmp_path, "subs",
        f"subtitles({srt!r}, style='FontSize=28').time(2)\n"
        "k = karaoke([(0.0, 2.0, 'カラオケ')], style={'size': 44, 'margin_v': 200,"
        " 'primary': 'yellow', 'secondary': 'yellow'})\n"
        "k.time(2) @ 0\n")
    px = _bright(_frame(out, 30))
    low = [p for p in px if p[1] > H * 0.75]            # SRT の字幕（下端）
    mid = [p for p in px if H * 0.25 < p[1] < H * 0.6]  # カラオケ（margin_v で上げた）
    assert len(low) > 100, f"字幕が映っていない（明るい画素 {len(low)}）"
    assert len(mid) > 100, f"カラオケが映っていない（明るい画素 {len(mid)}）"
    # カラオケは黄色（青が低い）で描かれている＝色もアルファも生きている
    yellow = [p for p in mid if p[2] > 160 and p[3] > 160 and p[4] < 110]
    assert len(yellow) > 50


# --- 2. glow を text に ----------------------------------------------------

def test_glow_enters_planar_rgb(tmp_path):
    """glow の入口は format=gbrap（rgba だと live 経路で yuva に折衝される）"""
    _need_font()
    fc = _dry_filter(tmp_path, "glow_dry",
                     "t = text('GLOW', size=120)\n"
                     "t.time(1) <= glow(radius=12, intensity=0.9)\n")
    assert ",format=gbrap,split[" in fc
    assert "format=rgba,split[" not in fc


def test_glow_on_white_text_stays_white(tmp_path):
    """実レンダ: 白い文字に glow を掛けても白いまま（マゼンタに化けない）"""
    _need_render()
    out = _render(tmp_path, "glow",
                  "t = text('GLOW', size=140, color='white')\n"
                  "t.time(1) <= glow(radius=12, intensity=0.9)\n",
                  background_color="black")
    px = _bright(_frame(out, 10), threshold=200)
    assert len(px) > 500, "文字が映っていない"
    n = len(px)
    r = sum(p[2] for p in px) / n
    g = sum(p[3] for p in px) / n
    b = sum(p[4] for p in px) / n
    # 化けていたときの実測は (253, 191, 254)。白なら3成分がそろう
    assert g > 235 and abs(r - g) < 12 and abs(b - g) < 12, (r, g, b)
    # 光のにじみ（文字の外側の薄い明るさ）も無彩色
    halo = [p for p in _bright(_frame(out, 10), threshold=40) if p[3] < 160]
    assert len(halo) > 200, "にじみが出ていない"
    hr = sum(p[2] for p in halo) / len(halo)
    hg = sum(p[3] for p in halo) / len(halo)
    hb = sum(p[4] for p in halo) / len(halo)
    assert abs(hr - hg) < 12 and abs(hb - hg) < 12, (hr, hg, hb)


# --- 3. counter -------------------------------------------------------------

@pytest.mark.parametrize("fmt, expected", [
    ("%d", ("", "", None, False, 0)),
    ("%03d", ("", "", 3, False, 0)),
    ("スコア: %03d点", ("スコア: ", "点", 3, False, 0)),
    ("%,d", ("", "", None, True, 0)),
    ("¥%,d円", ("¥", "円", None, True, 0)),
    ("%.2f", ("", "", None, False, 2)),
    ("%,.1f", ("", "", None, True, 1)),
    ("%.0f", ("", "", None, False, 0)),
    ("%f", ("", "", None, False, 6)),
    ("%d%%", ("", "%", None, False, 0)),
    ("100%% の %i", ("100% の ", "", None, False, 0)),
])
def test_counter_format_parse(fmt, expected):
    assert _parse_counter_format(fmt) == expected


@pytest.mark.parametrize("fmt, key", [
    ("点", "プレースホルダ"),
    ("50%", "不完全"),
    ("%s", "変換指定"),
    ("%x", "変換指定"),
    ("%.2d", "小数桁"),
    ("%08.2f", "併用"),
    ("%05,d", "併用"),
    ("%.10f", "9 桁"),
    ("%d / %d", "1個"),
    ("it's %d", "アポストロフィ"),
])
def test_counter_format_errors(fmt, key):
    _need_font()
    _mk()
    with pytest.raises(ValueError, match=key):
        sv.counter(0, 10, format=fmt)


def test_counter_other_errors():
    _need_font()
    _mk()
    with pytest.raises(TypeError, match="format"):
        sv.counter(0, 10, format=5)
    with pytest.raises(ValueError, match="2\\^53"):
        sv.counter(0, 2 ** 53)
    with pytest.raises(ValueError, match="2\\^53"):
        sv.counter(0, 2 ** 50, format="%.2f")      # ×100 で 2^53 を超える
    with pytest.raises(ValueError, match="easing の名前"):
        sv.counter(0, 10, easing="ease_no_such")
    with pytest.raises(ValueError, match="easing の名前"):
        sv.counter(0, 10, easing="ease_spring")    # ファクトリは名前では渡せない
    with pytest.raises(TypeError, match="easing"):
        sv.counter(0, 10, easing=0.5)
    sv.counter(0, 2 ** 53 - 1)                     # 上限ちょうど未満は通る


def test_counter_literal_escape():
    """% と \\ は AVOption の解釈で1段はがれるので2段ぶん書く"""
    assert _escape_counter_literal("%") == "\\\\%"
    assert _escape_counter_literal("\\") == "\\\\\\\\"
    assert _escape_counter_literal("a:b,c") == "a\\:b\\,c"


def test_counter_simple_filter_reaches_one_on_last_frame():
    """整数・32ビット以内は %{eif} 1個。進行度の分母は 尺 - 1フレーム - 1µs"""
    _need_font()
    _mk()
    c = sv.counter(0, 2147483647)
    (f,) = _build_text_filters(c, 0, 3)
    assert "%{eif\\:round((2147483647*clip((t-0)/2.966666\\,0\\,1)))\\:d}" in f
    assert "enable=" not in f
    # 開始が 0 でなくても同じ（start を引く）
    (f2,) = _build_text_filters(c, 10, 3)
    assert "clip((t-10)/2.966666\\,0\\,1)" in f2
    # 1フレーム以下の尺は最初から to
    (f3,) = _build_text_filters(c, 0, 1 / 30)
    assert "round((2147483647*1))" in f3


def test_counter_position_progress_is_unchanged():
    """位置・アルファの u は他の Object と同じ定義（尺そのもの）のまま"""
    _need_font()
    _mk()
    c = sv.counter(0, 10, x=lambda u: u)
    (f,) = _build_text_filters(c, 0, 3)
    assert "x='(clip((t-0)/3\\,0\\,1))*W-text_w/2'" in f


def test_counter_grouped_filter_shapes():
    """桁区切りは「かたまりの個数」ごとに drawtext を作り enable で切り替える"""
    _need_font()
    _mk()
    c = sv.counter(0, 1234567, format="%,d")
    fs = _build_text_filters(c, 0, 3)
    assert len(fs) == 3                      # 1〜3 かたまり（0 / 1,000 / 1,000,000）
    assert all(f.startswith("drawtext=") for f in fs)
    assert "enable=" in fs[0] and "\\,1000)'" in fs[0]
    assert fs[0].count("%{eif") == 1
    assert fs[1].count("%{eif") == 2 and "\\:d\\:3}" in fs[1]
    assert fs[2].count("%{eif") == 3
    # 範囲は互いに重ならず隙間もない: 下限なし <1000 / [1000, 1e6) / 1e6 以上・上限なし
    assert "gte(" not in fs[0].split("enable=")[1]
    assert "gte(" in fs[1].split("enable=")[1] and "lt(" in fs[1].split("enable=")[1]
    assert "lt(" not in fs[2].split("enable=")[1]
    # 桁数が変わらない範囲なら1個で済む
    one = sv.counter(100000, 999999, format="%,d")
    assert len(_build_text_filters(one, 0, 3)) == 1


def test_counter_sign_and_decimal_shapes():
    """負の値は符号ごとの形を用意する。小数は整数部と小数部を別の %{eif} で印字"""
    _need_font()
    _mk()
    c = sv.counter(-5, 5, format="%.1f")
    fs = _build_text_filters(c, 0, 3)
    assert len(fs) == 2
    assert "text='-%{eif" in fs[0] and "enable='lt(" in fs[0]
    assert "text='%{eif" in fs[1] and "enable='gte(" in fs[1]
    assert all("}.%{eif" in f and "\\:d\\:1}" in f for f in fs)
    pos = sv.counter(0, 5, format="%.1f")
    (f,) = _build_text_filters(pos, 0, 3)
    assert "enable=" not in f


def test_counter_big_integer_uses_chunks():
    """32ビットを超える整数は 9 桁ずつに割る（%{eif} は int しか印字できない）"""
    _need_font()
    _mk()
    c = sv.counter(0, 5000000000)
    fs = _build_text_filters(c, 0, 3)
    assert len(fs) == 2
    assert fs[1].count("%{eif") == 2 and "\\:d\\:9}" in fs[1]


def test_counter_easing_forms_and_identity():
    _need_font()
    _mk()
    plain = sv.counter(0, 100)
    named = sv.counter(0, 100, easing="ease_out_cubic")
    func = sv.counter(0, 100, easing=sv.ease_out_cubic)
    lam = sv.counter(0, 100, easing=lambda u: u ** 2)
    assert named.source == func.source                  # 名前でも関数でも同じ式
    assert len({plain.source, named.source, lam.source}) == 3
    (f,) = _build_text_filters(named, 0, 3)
    # イージングつきは最後のコマを to に固定する
    assert "if(gte(clip((t-0)/2.966666\\,0\\,1)\\,1)\\,100\\," in f
    # 行き過ぎる系は、途中で負になる・to を超える形も用意される
    back = sv.counter(0, 100, format="%,d", easing="ease_in_back")
    fs = _build_text_filters(back, 0, 3)
    assert any("text='-%{eif" in f for f in fs)


def test_counter_dry_run_is_deterministic(tmp_path):
    _need_font()
    body = ("counter(0, 1234567, format='¥%,d', easing='ease_out_cubic',"
            " border=2).time(2)\n")
    assert (_dry_filter(tmp_path, "cnt_a", body)
            == _dry_filter(tmp_path, "cnt_a", body))


_COUNTERS = (
    # (from_, to, 追加引数, 最後のコマに出るはずの文字列, y)
    (0, 2147483647, "", "2147483647", 0.15),
    (0, 2147483647, ", format='%,d'", "2,147,483,647", 0.32),
    (0, 99.5, ", format='%.1f%%'", "99.5%", 0.49),
    (0, 5000000000, ", easing='ease_out_back'", "5000000000", 0.66),
    (100, -1234567, ", format='¥%,d', easing=lambda u: u ** 2", "¥-1,234,567", 0.83),
)


def test_counter_last_frame_shows_target(tmp_path):
    """実レンダ: 最後のコマが to（同じ書式の text() と画素で一致）、最初のコマは from_"""
    _need_render()
    kw = "size=36, color='white', border=2"
    body = "".join(
        f"c{i} = counter({a}, {b}{extra}, x=0.5, y={y}, {kw})\n"
        f"c{i}.time(1) @ 0\n"
        for i, (a, b, extra, _, y) in enumerate(_COUNTERS))
    ref_body = "".join(
        f"t{i} = text({shown!r}, x=0.5, y={y}, {kw})\nt{i}.time(1) @ 0\n"
        for i, (_, _, _, shown, y) in enumerate(_COUNTERS))
    out = _render(tmp_path, "cnt", body, background_color="black")
    ref = _render(tmp_path, "cnt_ref", ref_body, background_color="black")
    last = _frame(out, FPS - 1)
    want = _frame(ref, FPS - 1)
    ink = len(_bright(want))
    assert ink > 2000, "参照の文字が映っていない"
    diff = _luma_diff_count(last, want)
    assert diff < ink * 0.02, f"最後のコマが to と一致しない（差 {diff} / 字 {ink} 画素）"
    # 1つ前のコマはまだ途中（＝最後のコマだけが to。比較が甘くないことの確認）
    prev = _frame(out, FPS - 2)
    assert _luma_diff_count(prev, want) > ink * 0.1
    # 最初のコマは from_
    first_ref = _render(
        tmp_path, "cnt_first",
        "".join(f"t{i} = text({s!r}, x=0.5, y={y}, {kw})\nt{i}.time(1) @ 0\n"
                for i, (s, y) in enumerate(
                    zip(("0", "0", "0.0%", "0", "¥100"),
                        (c[4] for c in _COUNTERS)))),
        background_color="black")
    first = _frame(out, 0)
    want0 = _frame(first_ref, 0)
    assert _luma_diff_count(first, want0) < len(_bright(want0)) * 0.02


def test_counter_progress_follows_last_output_frame():
    """動画の末尾で終わる counter は、出力される最後のコマで進行度が 1 になる。

    出力コマ数は 総尺×fps の四捨五入。1.01 秒・30fps は 30 コマ（最後は t=29/30）で、
    分母が 尺 - 1フレーム（0.976666）のままだと 0.99 までしか行かない。"""
    _need_font()
    p = _mk()
    c = sv.counter(0, 100)
    p.duration = 1.01                        # 30.3 コマ → 30 コマ（最後は 29/30 秒）
    (f,) = _build_text_filters(c, 0, 1.01)
    assert "clip((t-0)/0.966666\\,0\\,1)" in f
    p.duration = 1.02                        # 30.6 コマ → 31 コマ。尺 - 1フレームのまま
    (f,) = _build_text_filters(c, 0, 1.02)
    assert "clip((t-0)/0.986666\\,0\\,1)" in f
    # 途中で終わる counter（後ろに別の物がある）は総尺の端数に左右されない
    p.duration = 5.01
    (f,) = _build_text_filters(c, 0, 1.01)
    assert "clip((t-0)/0.976666\\,0\\,1)" in f
    # configure(duration=) で途中を切った場合（食い違いが 1 フレームを超える）は
    # 進み方を変えない
    p.duration = 2.0
    (f,) = _build_text_filters(c, 0, 3)
    assert "clip((t-0)/2.966666\\,0\\,1)" in f
    # 最後の出力コマより後ろで始まる counter（開始の丸めで 1 コマだけ映る）は to
    p.duration = 1.416333                    # 42.49 コマ → 42 コマ（最後は 41/30 秒）
    (f,) = _build_text_filters(c, 1.383, 1 / 30)
    assert "round((100*1))" in f


def test_counter_last_frame_shows_target_off_grid(tmp_path):
    """実レンダ: 総尺がフレーム格子に乗らない動画の末尾でも最後のコマは to。

    time(1.01) は 30 コマしか出ない（以前は最後のコマが「99」だった）。"""
    _need_render()
    kw = "x=0.5, y=0.5, size=80, color='white', border=3"
    ref = _render(tmp_path, "og_ref", f"text('100', {kw}).time(1)\n",
                  background_color="black")
    want = _frame(ref, 0)
    ink = len(_bright(want))
    assert ink > 2000, "参照の文字が映っていない"
    for name, body, last in (
            ("og_end", f"counter(0, 100, {kw}).time(1.01)\n", 29),
            # 開始も格子の外（0.37 秒）。総尺 1.38 秒 = 41.4 コマ → 41 コマ
            ("og_shift", f"pause.time(0.37)\ncounter(0, 100, {kw}).time(1.01)\n", 40),
            # 端数が 0.5 以上（30.6 コマ → 31 コマ）は従来どおり
            ("og_up", f"counter(0, 100, {kw}).time(1.02)\n", 30)):
        out = _render(tmp_path, name, body, background_color="black")
        n = subprocess.run(
            ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v",
             "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", out],
            capture_output=True, text=True, timeout=60).stdout.strip()
        assert n == str(last + 1), (name, n)
        diff = _luma_diff_count(_frame(out, last), want)
        assert diff < ink * 0.02, f"{name}: 最後のコマが to でない（差 {diff} / 字 {ink}）"
        # 1つ前のコマはまだ途中（詰めすぎて早く to に着いていない）
        assert _luma_diff_count(_frame(out, last - 1), want) > ink * 0.1, name


# --- 4. text の行間・行揃え・縦の基準 ---------------------------------------

def test_text_layout_options_in_filter():
    _need_font()
    _mk()
    t = sv.text("a\nb", line_spacing=12, text_align="center", y_align="font")
    (f,) = _build_text_filters(t, 0, 1)
    assert ":line_spacing=12:text_align=center:y_align=font" in f
    d = sv.text("a\nb")
    (fd,) = _build_text_filters(d, 0, 1)
    for opt in ("line_spacing", "text_align", "y_align"):
        assert opt not in fd                 # 既定値では出力しない（既存出力と不変）
    assert t.source != d.source
    assert sv.text("a\nb", line_spacing=-4).source != d.source


def test_text_layout_errors():
    _need_font()
    _mk()
    with pytest.raises(ValueError, match="text_align"):
        sv.text("a", text_align="justify")
    with pytest.raises(ValueError, match="y_align"):
        sv.text("a", y_align="top")
    with pytest.raises((TypeError, ValueError), match="line_spacing"):
        sv.text("a", line_spacing="wide")


def _ink_rows(frame):
    """文字のある行の帯 [(y0, y1), ...] と、帯ごとの (最小x, 最大x)"""
    px = _bright(frame)
    rows = sorted({p[1] for p in px})
    bands = []
    for y in rows:
        if bands and y - bands[-1][1] <= 2:
            bands[-1][1] = y
        else:
            bands.append([y, y])
    spans = []
    for y0, y1 in bands:
        xs = [p[0] for p in px if y0 <= p[1] <= y1]
        spans.append((min(xs), max(xs)))
    return bands, spans


def test_text_align_and_line_spacing_render(tmp_path):
    """実レンダ: 短い1行目が text_align で右へ寄り、line_spacing で行が離れる"""
    _need_render()
    def render(name, extra):
        out = _render(
            tmp_path, name,
            f"text('I\\nHHHHHHHH', x=0.5, y=0.5, size=48, color='white'{extra})"
            ".time(0.5)\n", background_color="black")
        return _ink_rows(_frame(out, 5))

    bands_l, spans_l = render("al_left", "")
    bands_c, spans_c = render("al_center", ", text_align='center'")
    bands_r, spans_r = render("al_right", ", text_align='right', line_spacing=40")
    assert len(bands_l) == len(bands_c) == len(bands_r) == 2
    wide = spans_l[1][1] - spans_l[1][0]
    # 1行目（I）の左端: 左揃え < 中央揃え < 右揃え
    assert spans_c[0][0] > spans_l[0][0] + wide * 0.3
    assert spans_r[0][0] > spans_c[0][0] + wide * 0.3
    # 右揃えでは1行目の右端が2行目の右端にそろう
    assert abs(spans_r[0][1] - spans_r[1][1]) <= 6
    # 行間 40px ぶん、2行目の上端が離れる
    gap_l = bands_l[1][0] - bands_l[0][0]
    gap_r = bands_r[1][0] - bands_r[0][0]
    assert 34 <= gap_r - gap_l <= 46, (gap_l, gap_r)


# --- 5. audit: 単色背景の上の文字 -------------------------------------------

def _decoration(p):
    return [f for f in p.audit(quiet=True) if f["code"] == "text-no-decoration"]


def test_audit_plain_text_on_explicit_solid_background_is_info():
    _need_font()
    p = _mk(background_color="black")
    sv.text("裸の文字", size=60).time(2)
    (f,) = _decoration(p)
    assert f["severity"] == "info"
    assert "コントラスト比 21.0" in f["message"]
    p.audit(strict=True, quiet=True)         # info だけなら strict でも止まらない


def test_audit_plain_text_stays_warning_when_background_unknown():
    """背景色を明示していない（既定の黒のまま）なら従来どおり warning"""
    _need_font()
    p = _mk()
    sv.text("裸の文字", size=60).time(2)
    (f,) = _decoration(p)
    assert f["severity"] == "warning"


def test_audit_plain_text_low_contrast_or_over_image_is_warning():
    _need_font()
    p = _mk(background_color="black")
    sv.text("黒地に暗い字", size=60, color="navy").time(2)
    assert _decoration(p)[0]["severity"] == "warning"

    p = _mk(background_color="white@0.5")     # 透過つきの背景は単色と言えない
    sv.text("字", size=60, color="black").time(2)
    assert _decoration(p)[0]["severity"] == "warning"

    p = _mk(background_color="black")
    img = sv.Object(sv.asset("images/shape_badge.png"))
    img.time(2) <= sv.move(x=0.5, y=0.5)
    t = sv.text("絵と同時に出る字", size=60)
    t.time(2) @ 0
    assert _decoration(p)[0]["severity"] == "warning"


def test_audit_plain_text_after_image_is_info(tmp_path):
    """絵と時間が重ならない文字は単色背景の上（レイヤーの配置を解決して判定する）"""
    _need_font()
    layer = tmp_path / "after_image.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "img = Object(asset('images/shape_badge.png'))\n"
        "img.time(2) <= move(x=0.5, y=0.5)\n"
        "text('絵の後に出る字', size=60).time(2)\n", encoding="utf-8")
    p = _mk(background_color="black")
    p.layer(str(layer))
    assert _decoration(p)[0]["severity"] == "info"


def test_audit_transparent_output_keeps_warning(tmp_path):
    """透過出力（alpha=True・連番 PNG）は background_color を使わないので格下げしない"""
    _need_font()
    layer = tmp_path / "bare.py"
    layer.write_text("from scriptvedit import *\n"
                     "text('裸の文字', size=60).time(1)\n", encoding="utf-8")

    def project():
        p = _mk(background_color="black")
        p.layer(str(layer))
        return p

    # 不透明な出力は info なので strict でも通る
    project().render(str(tmp_path / "o.mp4"), dry_run=True, strict=True)
    project().render(str(tmp_path / "o.webm"), dry_run=True, strict=True)
    for out, alpha in (("a.webm", True), ("a.webp", True),
                       ("seq_%04d.png", False)):
        with pytest.raises(RuntimeError, match="text-no-decoration"):
            project().render(str(tmp_path / out), dry_run=True, strict=True,
                             alpha=alpha)
    # 単独の p.audit() は出力先を知らないので不透明を仮定し、制限をメッセージで示す
    (f,) = _decoration(project())
    assert f["severity"] == "info" and "透過出力" in f["message"]


def test_audit_decorated_text_reports_nothing():
    _need_font()
    p = _mk(background_color="black")
    sv.text("縁取りつき", size=60, border=3).time(2)
    assert _decoration(p) == []
    assert os.path.basename(__file__)        # （os を import した目印）
