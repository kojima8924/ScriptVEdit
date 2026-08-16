# -*- coding: utf-8 -*-
"""DSL糖衣（タイムライン3点セット）のテスト

obj[a:b]  … 素材時間の切り出し（trim/atrim。表示尺は切り出し長が既定）
obj @ t   … タイムライン絶対配置（秒 or アンカー名。非進行）
a >> b    … 直後連結（pause.time() を挟める。非進行）
"""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from scriptvedit import Object, Project, anchor, asset, pause, text  # noqa: E402


def _mk():
    p = Project()
    p.configure(width=320, height=180, fps=30)
    return p


def _two_color_video(tmp_path):
    """前半2秒=赤、後半2秒=青の4秒動画（イン点検証用）"""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い環境")
    out = tmp_path / "redblue.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=red:s=64x36:d=2:r=10",
         "-f", "lavfi", "-i", "color=c=blue:s=64x36:d=2:r=10",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]",
         "-map", "[v]", "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True, timeout=60)
    return str(out)


def _resolve(p):
    """直接生成したObject群をリゾルバにかける（render はレイヤー再実行で
    objects を破棄するため、単体テストでは内部リゾルバを直接使う）"""
    p._layers = [(0, len(p.objects), 0)]
    p._resolve_anchors()
    return p._calc_total_duration()


# --- obj[a:b] スライス -----------------------------------------------------

def test_slice_sets_trim_and_duration():
    """スライスは trim/atrim(start, duration) を積み、表示尺を切り出し長にする"""
    _mk()
    o = Object(asset("video/clip_with_audio.mp4"))[1:3]
    trims = [e for e in o.effects if e.name == "trim"]
    assert trims and trims[0].params == {"start": 1.0, "duration": 2.0}
    atrims = [e for e in o.audio_effects if e.name == "atrim"]
    assert atrims and atrims[0].params == {"start": 1.0, "duration": 2.0}
    assert o.duration == 2.0


def test_slice_open_end_uses_auto_duration():
    """obj[3:] はアウト点省略＝素材末尾まで（尺は自動確定）"""
    _mk()
    o = Object(asset("video/clip_with_audio.mp4"))[3:]
    assert o.duration is None and o._duration_auto
    # length() は「末尾-3秒」（fox_noaudio は約5.5秒）
    assert abs(o.length() - (5.545 - 3)) < 0.1


def test_slice_negative_indices():
    """負値は素材末尾からの相対（obj[-2:] = 末尾2秒）"""
    _mk()
    o = Object(asset("video/clip_with_audio.mp4"))[-2:]
    trims = [e for e in o.effects if e.name == "trim"]
    assert trims and abs(trims[0].params["start"] - (5.545 - 2)) < 0.1


def test_slice_errors():
    """step・非スライス・画像・逆転範囲は明示エラー"""
    _mk()
    src = asset("video/clip_with_audio.mp4")
    with pytest.raises(ValueError, match="step"):
        Object(src)[::2]
    with pytest.raises(TypeError, match="スライス"):
        Object(src)[2]
    with pytest.raises(TypeError, match="素材時間がない"):
        Object(asset("images/shape_badge.png"))[1:2]
    with pytest.raises(ValueError, match="end > start"):
        Object(src)[3:1]


def test_slice_in_point_real_render(tmp_path):
    """実レンダ: 赤2秒+青4秒の動画を[2:4]で切ると先頭フレームが青"""
    src = _two_color_video(tmp_path)
    layer = tmp_path / "slice_layer.py"
    layer.write_text(
        "from scriptvedit import *\n"
        f"o = Object(r\"{src}\")[2:4]\n"
        "o <= move(x=0.5, y=0.5, anchor='center')\n",
        encoding="utf-8")
    out = tmp_path / "out.mp4"
    p = Project()
    p.configure(width=64, height=36, fps=10, background_color="black")
    p.layer(str(layer))
    p.render(str(out), timeout=120)

    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(out), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        check=True, capture_output=True, timeout=30)
    center = ((36 // 2) * 64 + 64 // 2) * 3
    r, g, b = result.stdout[center:center + 3]
    assert b > 128 and r < 100, f"先頭フレームが青でない: RGB=({r},{g},{b})"


# --- obj @ t 絶対配置 ------------------------------------------------------

def test_at_places_absolutely_and_does_not_advance():
    """@は絶対配置かつ非進行（後続の順次配置に影響しない）"""
    p = _mk()
    src = asset("video/clip_with_audio.mp4")
    a = Object(src)[0:2]           # 順次: 0〜2
    b = Object(src)[0:1] @ 10      # 絶対: 10〜11（カーソルは進めない）
    c = Object(src)[0:2]           # 順次: 2〜4（bの影響を受けない）
    total = _resolve(p)
    assert (a.start_time, b.start_time, c.start_time) == (0, 10.0, 2.0)
    assert abs(total - 11.0) < 1e-6  # 総尺はbの終端まで


def test_at_anchor_name():
    """@ にアンカー名を渡せる（"名前.end" 等）"""
    p = _mk()
    src = asset("video/clip_with_audio.mp4")
    a = Object(src)[0:2]
    a.time(2, name="head")
    b = Object(src)[0:1] @ "head.end"
    _resolve(p)
    assert b.start_time == 2.0


def test_at_undefined_anchor_raises():
    """@ の未定義アンカーは診断付きエラー"""
    p = _mk()
    Object(asset("video/clip_with_audio.mp4"))[0:1] @ "no_such_anchor"
    with pytest.raises(RuntimeError, match="no_such_anchor"):
        _resolve(p)


def test_at_validates_operand():
    """@ の右辺は数値かアンカー名のみ・負の時刻は拒否"""
    _mk()
    src = asset("video/clip_with_audio.mp4")
    with pytest.raises(TypeError):
        Object(src) @ [1, 2]
    with pytest.raises(ValueError):
        Object(src) @ -1


# --- a >> b 連結 -----------------------------------------------------------

def test_rshift_chains_after():
    """a >> b は b を a の終了直後に開始（pause.time() を挟める）"""
    p = _mk()
    src = asset("video/clip_with_audio.mp4")
    a = Object(src)[0:2]
    b = Object(src)[0:1]
    c = Object(src)[0:1]
    a >> b                          # b: 2〜3
    b >> pause.time(0.5) >> c       # c: 3.5〜4.5
    _resolve(p)
    assert b.start_time == 2.0
    assert c.start_time == 3.5


def test_rshift_after_at():
    """スライス+@+>> の組み合わせ: src[3:8] @ 12 の直後に次クリップ"""
    p = _mk()
    src = asset("video/clip_with_audio.mp4")
    a = Object(src)[1:3] @ 12
    b = Object(src)[0:1]
    a >> b
    _resolve(p)
    assert a.start_time == 12.0
    assert b.start_time == 14.0


def test_rshift_requires_known_duration():
    """先行アイテムの尺未確定は日本語エラー"""
    p = _mk()
    src = asset("video/clip_with_audio.mp4")
    a = Object(src)          # time()もスライスもなし＝尺未確定
    b = Object(src)[0:1]
    a >> b
    with pytest.raises(RuntimeError, match="尺が確定していません"):
        _resolve(p)


def test_rshift_rejects_bad_operand():
    """>> の右辺の型チェックと自己連結の拒否"""
    _mk()
    a = Object(asset("video/clip_with_audio.mp4"))[0:1]
    with pytest.raises(TypeError):
        a >> 5
    with pytest.raises(ValueError):
        a >> a


# --- 既存機能との整合 ------------------------------------------------------

def test_until_from_floating_start():
    """浮動配置(@)のuntilは実開始時刻を基準に尺を計算する"""
    p = _mk()
    src = asset("video/clip_with_audio.mp4")
    a = Object(src)[0:2]
    a.time(2, name="head")
    b = Object(src) @ 1
    b.until("head.end")     # 1〜2 → dur=1
    _resolve(p)
    assert b.start_time == 1.0
    assert b.duration == 1.0


def test_text_slice_rejected():
    """テキストにも素材時間はない"""
    _mk()
    t = text("字幕", size=48, border=3)
    with pytest.raises(TypeError, match="素材時間がない"):
        t[1:2]


# --- obj * n リピート ------------------------------------------------------

def test_repeat_sets_effects_and_duration():
    """obj*3 は repeat/arepeat(count, segment) を積み、表示尺を3倍にする"""
    _mk()
    o = Object(asset("video/clip_with_audio.mp4"))[0:2] * 3
    reps = [e for e in o.effects if e.name == "repeat"]
    assert reps and reps[0].params["count"] == 3
    assert abs(reps[0].params["segment"] - 2.0) < 1e-6
    assert abs(o.duration - 6.0) < 1e-6
    assert abs(o.length() - 6.0) < 1e-6


def test_repeat_rmul_and_noop():
    """3 * obj も可。*1 は何もしない"""
    _mk()
    src = asset("video/clip_with_audio.mp4")
    o = 2 * Object(src)[0:1]
    assert any(e.name == "repeat" for e in o.effects)
    o2 = Object(src)[0:1] * 1
    assert not any(e.name == "repeat" for e in o2.effects)


def test_repeat_errors():
    """非整数・0以下・画像は明示エラー"""
    _mk()
    src = asset("video/clip_with_audio.mp4")
    with pytest.raises(TypeError, match="整数"):
        Object(src) * 1.5
    with pytest.raises(ValueError, match="1以上"):
        Object(src) * 0
    with pytest.raises(TypeError, match="素材尺がない"):
        Object(asset("images/shape_badge.png")) * 2


def test_repeat_real_render(tmp_path):
    """実レンダ: 赤1秒+青1秒の動画を*2すると尺4秒・2周目(t=2.5)が赤"""
    src = _two_color_video_1s(tmp_path)
    layer = tmp_path / "repeat_layer.py"
    layer.write_text(
        "from scriptvedit import *\n"
        f"o = Object(r\"{src}\") * 2\n"
        "o <= move(x=0.5, y=0.5, anchor='center')\n",
        encoding="utf-8")
    out = tmp_path / "rep.mp4"
    p = Project()
    p.configure(width=64, height=36, fps=10, background_color="black")
    p.layer(str(layer))
    p.render(str(out), timeout=120)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(out)],
        check=True, capture_output=True, text=True, timeout=30)
    assert abs(float(probe.stdout.strip()) - 4.0) < 0.3
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", "2.5", "-i", str(out),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        check=True, capture_output=True, timeout=30)
    center = ((36 // 2) * 64 + 32) * 3
    r, g, b = result.stdout[center:center + 3]
    assert r > 128 and b < 100, f"2周目が赤でない: RGB=({r},{g},{b})"


def _two_color_video_1s(tmp_path):
    """赤1秒+青1秒の2秒動画（リピート検証用）"""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い環境")
    out = tmp_path / "rb1.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=red:s=64x36:d=1:r=10",
         "-f", "lavfi", "-i", "color=c=blue:s=64x36:d=1:r=10",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]",
         "-map", "[v]", "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True, timeout=60)
    return str(out)


# --- -obj 逆再生 -----------------------------------------------------------

def test_neg_applies_reverse():
    """-obj は reverse() の糖衣"""
    _mk()
    o = -Object(asset("video/clip_with_audio.mp4"))[0:2]
    assert any(e.name == "reverse" for e in o.effects)


# --- render(strict=True) ---------------------------------------------------

def test_render_strict_blocks_on_warning(tmp_path):
    """auditのwarningがあればレンダ前にRuntimeError（dry_runにも適用）"""
    from scriptvedit import Project as _P
    layer = tmp_path / "strict_layer.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "text('裸の文字', size=60).time(2)\n",  # 縁取りなし→warning
        encoding="utf-8")
    p = _P()
    p.configure(width=640, height=360, fps=30)
    p.layer(str(layer))
    with pytest.raises(RuntimeError, match="text-no-decoration"):
        p.render(str(tmp_path / "s.mp4"), dry_run=True, strict=True)


def test_render_strict_passes_clean(tmp_path):
    """warningが無ければstrictでも通る"""
    from scriptvedit import Project as _P
    layer = tmp_path / "clean_layer.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "text('読みやすい', size=60, border=3).time(2)\n",
        encoding="utf-8")
    p = _P()
    p.configure(width=640, height=360, fps=30)
    p.layer(str(layer))
    result = p.render(str(tmp_path / "c.mp4"), dry_run=True, strict=True)
    assert result  # dry_runコマンドが返る


# --- %P パーセント記法の網羅 -----------------------------------------------

def test_percent_notation_across_factories():
    """%P は数値を取る主要な引数で一貫して使える（50%P == 0.5）"""
    from scriptvedit import P, crop, move, opacity, pad, resize
    _mk()
    assert 50 % P == 0.5 and 75 % P == 0.75

    m = move(x=50 % P, y=25 % P)
    assert m.params["x"].value == 0.5 and m.params["y"].value == 0.25
    r = resize(sx=50 % P, sy=50 % P)
    assert r.params["sx"] == 0.5
    o = opacity(50 % P)
    assert getattr(o.params["value"], "value", o.params["value"]) == 0.5
    t = text("x", x=50 % P, y=10 % P, size=48, border=3)
    assert t._text_spec["x"].value == 0.5


# --- Plan pass のレイヤー実行回数（監査 項目13）---------------------------

# b は前方参照（tail はこの後で定義される）で尺が決まる＝Plan pass が
# アンカーを解決してから初めて b の尺が確定する、という条件を作っている。
# tail は b の開始時刻と同じ 1 秒なので、offset=1 を付けて尺を正にする
# （offset 無しだと尺0になり、監査項目6 のチェックで RuntimeError）。
_ANCHOR_LAYER = """from scriptvedit import *
open(COUNTER, 'a', encoding='utf-8').write('x')
a = text('A', size=40, border=3)
a.time(1)
anchor('mid')
b = text('B', size=40, border=3)
b.show_until('tail')
c = text('C', size=40, border=3)
(a >> c).time(1)
d = text('D', size=40, border=3)
d.time(1)
anchor('tail')
"""


def _counter_layer(tmp_path, name="anchored.py"):
    counter = tmp_path / "exec_count.txt"
    layer = tmp_path / name
    layer.write_text(
        f"COUNTER = {str(counter)!r}\n" + _ANCHOR_LAYER, encoding="utf-8")
    return layer, counter


def test_plan_pass_executes_each_layer_once(tmp_path):
    """レイヤーの exec は Plan 1回 + Render 1回の計2回（以前は3回）。

    レイヤー exec 経路は _anchors を読まないため、Plan pass の外側ループは
    同じ構造を作り直すだけの空回しだった（監査 項目13）。
    """
    from scriptvedit import Project as _P
    layer, counter = _counter_layer(tmp_path)
    p = _P()
    p.configure(width=320, height=180, fps=10)
    p.layer(str(layer))
    p.render(str(tmp_path / "o.mp4"), dry_run=True)
    assert counter.read_text(encoding="utf-8") == "xx"


def test_layer_exec_never_reads_resolved_anchors(tmp_path):
    """レイヤー exec 中に _anchors を読まないこと（1パス化の前提を固定する）。

    将来「exec 中に解決済みアンカー時刻を読む API」を足すと、Plan pass を
    1回にした瞬間に静かに壊れる。読んだら失敗するようにして前提を明示する。
    """
    from scriptvedit import Project as _P

    state = {"tracking": False, "reads": []}

    class _TrackedAnchors(dict):
        """読み出しだけを記録する dict（書き込みは素通し）"""

        def _note(self, how):
            if state["tracking"]:
                state["reads"].append(how)

        def __getitem__(self, key):
            self._note(f"__getitem__({key!r})")
            return super().__getitem__(key)

        def __contains__(self, key):
            self._note(f"__contains__({key!r})")
            return super().__contains__(key)

        def get(self, key, default=None):
            self._note(f"get({key!r})")
            return super().get(key, default)

        def items(self):
            self._note("items()")
            return super().items()

        def keys(self):
            self._note("keys()")
            return super().keys()

        def values(self):
            self._note("values()")
            return super().values()

    layer, _counter = _counter_layer(tmp_path, "anchored2.py")
    p = _P()
    p.configure(width=320, height=180, fps=10)
    p.layer(str(layer))
    original_reset = _P._reset_runtime_state
    original_exec = _P._exec_layer_body

    def reset_spy(self):
        # _reset_runtime_state が _anchors を素の dict に戻すので、
        # そのたびに追跡付きへ差し替える（差し替え忘れると常に空＝空振り）
        original_reset(self)
        self._anchors = _TrackedAnchors()

    def exec_spy(self, *args, **kwargs):
        state["tracking"] = True
        try:
            return original_exec(self, *args, **kwargs)
        finally:
            state["tracking"] = False

    _P._reset_runtime_state = reset_spy
    _P._exec_layer_body = exec_spy
    try:
        p.render(str(tmp_path / "o2.mp4"), dry_run=True)
    finally:
        _P._reset_runtime_state = original_reset
        _P._exec_layer_body = original_exec
    assert isinstance(p._anchors, _TrackedAnchors)  # 追跡が効いていること
    assert p._anchors  # アンカーが実際に登録されている（空振りでない）
    assert state["reads"] == [], (
        f"レイヤー exec 中に _anchors を読みました: {state['reads']}")
