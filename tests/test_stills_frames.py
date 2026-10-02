# -*- coding: utf-8 -*-
"""stills()（時刻表つきの静止画列）と frames()（コマを描く関数）の回帰テスト。

どちらも絵の列を1本の動画（qtrle・argb・.mov）にして Object を1個返す
（src/scriptvedit/stillseq.py）。確かめること:

  (a) 丸めの規則: 境目の時刻を最も近いフレームへ丸め、誤差が後ろへ積もらない
  (b) 鍵: 画像の内容・各絵のフレーム数・fps・size で決まり、置き場所では変わらない
  (c) dry_run: 入力1本・生成コマンドが cache に載る・cold と warm で同じ出力
  (d) 実レンダ: 切り替わりがフレーム単位で一致する（生成物と最終出力を画素で見る）
  (e) alpha が保たれる
  (f) time() で素材より長く表示すると最後の絵が残る（Effect を焼く経路も同じ）
  (g) frames(): draw の呼ばれ方・原子的な書き込み・key
  (h) エラー
  (i) 画素形式の違う画像の混在・コマ数の検証（壊れた動画をキャッシュへ確定しない）

ffmpeg / ffprobe が無い環境では実レンダの項目だけ skip する。
"""
import json
import os
import shutil
import struct
import subprocess
import zlib

import pytest

import scriptvedit as sv
from scriptvedit import stillseq
from scriptvedit.cache import _is_hold_artifact_path
from scriptvedit.context import _exec_stack, activate, current_project
from scriptvedit.filters.video import _hold_source_filters, _video_tail_hold
from scriptvedit.stillseq import _fps_fraction, _stills_schedule

_W, _H = 32, 16


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """各テストを tmp_path で動かす（__cache__ をテストごとに空から始める）"""
    old_current = current_project()
    old_stack = list(_exec_stack)
    activate(None)
    _exec_stack[:] = []
    monkeypatch.chdir(tmp_path)
    try:
        yield
    finally:
        activate(old_current)
        _exec_stack[:] = old_stack


def _need_ffmpeg():
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg / ffprobe が無い環境")


def _png(path, rgba, w=_W, h=_H, color_type=6):
    """単色の PNG を書く（PIL に頼らない）。

    color_type: 6 = RGBA（既定）、2 = RGB（rgba の先頭3つ）、0 = グレースケール（先頭1つ）
    """
    px = bytes(rgba)[:{6: 4, 2: 3, 0: 1}[color_type]]
    raw = b"".join(b"\x00" + px * w for _ in range(h))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    os.makedirs(os.path.dirname(str(path)) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw))
                + chunk(b"IEND", b""))
    return str(path)


def _pages(tmp_path, n, sub="img"):
    """赤の濃さが 40, 80, … と変わる不透明の絵を n 枚作る"""
    return [_png(tmp_path / sub / f"s{i}.png", (40 + 40 * i, 0, 0, 255))
            for i in range(n)]


def _center_pixels(video, w=_W, h=_H):
    """動画の全コマの中央の画素 (r, g, b, a) を返す"""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        check=True, capture_output=True, timeout=120).stdout
    size = w * h * 4
    c = ((h // 2) * w + w // 2) * 4
    return [tuple(out[i * size + c:i * size + c + 4]) for i in range(len(out) // size)]


def _write_layer(tmp_path, name, body):
    path = tmp_path / name
    path.write_text("from scriptvedit import *\n" + body, encoding="utf-8")
    return str(path)


def _project(tmp_path, body, *, fps=30, bg="blue", name="layer.py"):
    p = sv.Project()
    p.configure(width=_W, height=_H, fps=fps, background_color=bg)
    p.layer(_write_layer(tmp_path, name, body), priority=1)
    return p


class _Arr:
    """numpy 配列の代わり（frames() は shape / dtype / tobytes だけを見る）"""

    def __init__(self, rgba, w=_W, h=_H, dtype="uint8"):
        self.shape = (h, w, 4)
        self.dtype = dtype
        self._data = bytes(rgba) * (w * h)

    def tobytes(self):
        return self._data


# --- (a) 丸めの規則 ----------------------------------------------------------

def test_schedule_rounds_boundaries_not_each_duration(tmp_path):
    """境目（累積秒）を丸める。1枚ずつ丸めて足すのと違い誤差が積もらない"""
    paths = _pages(tmp_path, 1) * 100
    # 0.11 秒 = 3.3 フレーム。1枚ずつ丸めると 3×100 = 300 枚で 1 秒ぶん短くなる
    _, starts, total = _stills_schedule([(p, 0.11) for p in paths], None, _fps_fraction(30))
    assert total == 330
    for k, s in enumerate(starts):
        assert abs(s - 0.11 * k * 30) <= 0.5 + 1e-9, (k, s)


def test_schedule_mixed_durations(tmp_path):
    paths = _pages(tmp_path, 6)
    secs = [0.5, 0.31, 0.1, 0.72, 0.05, 0.4]
    _, starts, total = _stills_schedule(list(zip(paths, secs)), None, _fps_fraction(30))
    # 累積 0.5 / 0.81 / 0.91 / 1.63 / 1.68 / 2.08 秒 → 15 / 24.3 / 27.3 / 48.9 / 50.4 / 62.4
    assert starts == [0, 15, 24, 27, 49, 50]
    assert total == 62


def test_schedule_half_frame_rounds_up_and_decimal_sum_is_exact(tmp_path):
    """半フレームちょうどは切り上げ。float の足し算の端数で境目が動かない"""
    paths = _pages(tmp_path, 3)
    # 0.05 秒 = 1.5 フレーム → 2。0.1×3 は float だと 0.30000000000000004 だが 9 フレーム
    _, starts, total = _stills_schedule(
        [(paths[0], 0.05), (paths[1], 0.05), (paths[2], 0.2)], None, _fps_fraction(30))
    assert starts == [0, 2, 3] and total == 9


def test_schedule_start_form_matches_duration_form(tmp_path):
    paths = _pages(tmp_path, 3)
    a = _stills_schedule([(paths[0], 0.4), (paths[1], 0.6), (paths[2], 0.5)],
                         None, _fps_fraction(30))
    b = _stills_schedule([(paths[0], 0), (paths[1], 0.4), (paths[2], 1.0)],
                         1.5, _fps_fraction(30))
    assert a == b


def test_schedule_ntsc_fps(tmp_path):
    """29.97（30000/1001）でも有理数で丸める"""
    paths = _pages(tmp_path, 2)
    _, starts, total = _stills_schedule(
        [(paths[0], 1001 / 30000 * 10), (paths[1], 1.0)], None,
        _fps_fraction(30000 / 1001))
    assert starts == [0, 10]
    assert total == 40          # 10 + 29.97 → 39.97 → 40


def test_object_reports_schedule(tmp_path):
    """戻り値の Object から丸めた時刻表を読める（dry_run でも同じ）"""
    paths = _pages(tmp_path, 3)
    p = sv.Project()
    p.configure(width=_W, height=_H, fps=30)
    p._dry_run = True
    s = sv.stills([(paths[0], 0.5), (paths[1], 0.31), (paths[2], 0.1)])
    assert s.frame_counts == [15, 9, 3]
    assert s.starts == [0.0, 0.5, 24 / 30]
    assert s.length() == pytest.approx(27 / 30)
    assert s.media_type == "video" and s.has_audio is False
    assert _is_hold_artifact_path(s.source)


# --- (b) 鍵 ------------------------------------------------------------------

def _dry_stills(*args, fps=30, **kwargs):
    p = sv.Project()
    p.configure(width=_W, height=_H, fps=fps)
    p._dry_run = True
    return sv.stills(*args, **kwargs).source


def test_key_ignores_location_and_follows_content(tmp_path):
    a = _pages(tmp_path, 2, "a")
    b = _pages(tmp_path, 2, "somewhere/else")
    base = _dry_stills([(a[0], 1), (a[1], 2)])
    assert _dry_stills([(b[0], 1), (b[1], 2)]) == base          # 置き場所は鍵に入らない
    assert _dry_stills([(a[0], 1), (a[1], 2.5)]) != base         # 尺
    assert _dry_stills([(a[1], 1), (a[0], 2)]) != base           # 並び
    assert _dry_stills([(a[0], 1), (a[1], 2)], fps=60) != base   # fps
    assert _dry_stills([(a[0], 1), (a[1], 2)], size=(64, 32)) != base
    _rewrite_png(a[1], (1, 2, 3, 255))
    assert _dry_stills([(a[0], 1), (a[1], 2)]) != base           # 画像の中身


def _rewrite_png(path, rgba):
    """同じパスの PNG を別の中身で書き直す。

    _file_fingerprint のプロセス内メモは (パス, サイズ, mtime_ns) が参照キーで、
    同じサイズの書き直しが mtime の分解能の中に収まると古い指紋を返す
    （単一レンダの中で素材が差し替わる想定は無いので本体は許容している）。
    テストでは mtime を明示的に進めて、時計の粒度に左右されないようにする。
    """
    before = os.stat(path).st_mtime_ns
    _png(path, rgba)
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, max(st.st_mtime_ns, before) + 2 * 10**9))


def test_key_is_same_for_same_output(tmp_path):
    """fps の書き方（30 / 30.0）と、画像と同じ寸法の size で鍵が分かれない"""
    a = _pages(tmp_path, 1)
    base = _dry_stills([(a[0], 1)], fps=30)
    assert _dry_stills([(a[0], 1)], fps=30.0) == base
    assert _dry_stills([(a[0], 1)], size=(_W, _H)) == base
    assert _dry_stills([(a[0], 1)], size=(_W * 2, _H * 2)) != base
    # コマンドも同じ（同じ鍵のコマンドが書き方で変わらない）
    cmds = []
    for fps in (30, 30.0):
        p = sv.Project()
        p.configure(width=_W, height=_H, fps=fps)
        p._dry_run = True
        s = sv.stills([(a[0], 1)], size=(_W, _H))
        f = sv.frames(lambda i: None, 3, key="k")
        cmds.append((p._pending_compute_cmds[s.source], p._pending_compute_cmds[f.source]))
    assert cmds[0] == cmds[1]
    assert cmds[0][0][cmds[0][0].index("-vf") + 1] == "format=argb,fps=30"
    assert cmds[0][1][cmds[0][1].index("-framerate") + 1] == "30"
    # 割り切れない fps は有理数で書く
    p = sv.Project()
    p.configure(width=_W, height=_H, fps=29.97)
    p._dry_run = True
    s = sv.stills([(a[0], 1)])
    cmd = p._pending_compute_cmds[s.source]
    assert cmd[cmd.index("-vf") + 1] == "format=argb,fps=2997/100"


def test_key_follows_content_of_images_under_cache(tmp_path):
    """__cache__ の下に自分で書き出した画像も内容で鍵を作る（書き直せば作り直す）"""
    _need_ffmpeg()
    page = _png(os.path.join("__cache__", "pages", "p.png"), (250, 0, 0, 255))
    a = sv.stills([(page, 0.1)], fps=30)
    assert _center_pixels(a.source) == [(250, 0, 0, 255)] * 3
    _rewrite_png(page, (0, 0, 250, 255))
    b = sv.stills([(page, 0.1)], fps=30)
    assert b.source != a.source
    assert _center_pixels(b.source) == [(0, 0, 250, 255)] * 3


def test_key_same_frames_same_key(tmp_path):
    """丸めた結果のフレーム数が同じなら同じ鍵（同一出力なら同一鍵）"""
    a = _pages(tmp_path, 2)
    assert (_dry_stills([(a[0], 1.0), (a[1], 2.0)])
            == _dry_stills([(a[0], 1.001), (a[1], 2.0)])
            == _dry_stills([(a[0], 0), (a[1], 1.0)], total=3.0))


def test_generated_path_is_under_cache(tmp_path):
    a = _pages(tmp_path, 1)
    src = _dry_stills([(a[0], 1)])
    assert src.replace("\\", "/").startswith("__cache__/artifacts/stills/")
    assert src.endswith(".mov")
    assert not os.path.exists(src), "dry_run では生成しない"


# --- (c) dry_run --------------------------------------------------------------

_STILLS_LAYER = (
    "import os\n"
    "d = os.path.join(os.path.dirname(here('x')), 'img')\n"
    "secs = [0.5, 0.31, 0.1, 0.72, 0.05, 0.4]\n"
    "pause.time(1.017)\n"
    "s = stills([(os.path.join(d, f's{i}.png'), secs[i]) for i in range(6)])\n"
    "s.time(3)\n"
    "pause.time(0.5)\n")


def test_dry_run_single_input_and_cache_cmd(tmp_path):
    _pages(tmp_path, 6)
    p = _project(tmp_path, _STILLS_LAYER)
    r = p.render(str(tmp_path / "out.mp4"), dry_run=True)
    main = r["main"]
    assert main.count("-i") == 2, "背景 + stills の1本だけ"
    (path, cmd), = r["cache"].items()
    assert _is_hold_artifact_path(path)
    assert cmd[-1] == path
    i = cmd.index("-i")
    assert cmd[i - 4:i] == ["-f", "concat", "-safe", "0"]
    # 画素形式の混在でフィルタグラフを作り直させない（入力オプション = -i より前）
    assert cmd[i - 6:i - 4] == ["-reinit_filter", "0"]
    assert cmd[i + 1] == path[:-len(".mov")] + ".ffconcat"
    assert cmd[cmd.index("-vf") + 1] == "format=argb,fps=30"
    assert cmd[cmd.index("-frames:v") + 1] == "62"
    assert cmd[cmd.index("-c:v") + 1] == "qtrle" and "-an" in cmd
    # 6枚あっても画像のパスはコマンドに現れない（リストの中）
    assert not any(str(a).endswith(".png") for a in cmd)
    assert not os.path.exists(path) and not os.path.exists(cmd[i + 1])


def test_dry_run_holds_last_frame_only_beyond_material(tmp_path):
    """time() が素材（62 枚 = 2.0667 秒）より長い分だけ tpad で最後のコマを保持する"""
    _pages(tmp_path, 6)
    fc = _project(tmp_path, _STILLS_LAYER).render(
        str(tmp_path / "o.mp4"), dry_run=True)["main"]
    fc = fc[fc.index("-filter_complex") + 1]
    # 3 − 62/30 = 0.933333 秒 + 1 フレーム。開始の tpad と同じ tpad に書く（§4.10）
    assert ("tpad=start_duration=1.017:start_mode=clone:"
            "stop_duration=0.966667:stop_mode=clone") in fc
    # 素材より短い・同じ尺なら保持しない
    for d in ("1.5", ""):
        body = _STILLS_LAYER.replace("s.time(3)", f"s.time({d})")
        fc2 = _project(tmp_path, body, name=f"l{len(d)}.py").render(
            str(tmp_path / "o.mp4"), dry_run=True)["main"]
        assert "stop_mode=clone" not in fc2[fc2.index("-filter_complex") + 1], d


def test_tail_hold_follows_time_effects(tmp_path):
    """保持の長さは時間系 Effect を畳んだ素材の尺から決まる（probe しない）"""
    a = _pages(tmp_path, 1)
    p = sv.Project()
    p.configure(width=_W, height=_H, fps=30)
    p._dry_run = True
    s = sv.stills([(a[0], 2.0)])
    assert _video_tail_hold(s) == 0.0                      # time() なし
    s.time(5)
    assert _video_tail_hold(s) == pytest.approx(3.0)
    s <= sv.speed(2.0)
    assert _video_tail_hold(s) == pytest.approx(4.0)        # 素材は 1 秒に縮む
    # 普通の __cache__ 生成物は対象外のまま
    o = sv.Object("__cache__/artifacts/checkpoint/x/y.mkv")
    o.duration = 9
    assert _video_tail_hold(o) == 0.0


def test_bake_paths_hold_last_frame(tmp_path):
    """Effect を焼く経路（checkpoint / compute）は入力の最後のコマを保持してから焼く"""
    _pages(tmp_path, 6)
    assert _hold_source_filters("__cache__/artifacts/stills/k.mov") == [
        "tpad=stop=-1:stop_mode=clone"]
    assert _hold_source_filters("__cache__/artifacts/frames/k.mov") == [
        "tpad=stop=-1:stop_mode=clone"]
    assert _hold_source_filters("__cache__/artifacts/xfade/k.mkv") == []
    assert _hold_source_filters("clip.mp4") == []
    body = _STILLS_LAYER.replace("s.time(3)", "s.time(3) <= fade(lambda u: 1 - u)")
    r = _project(tmp_path, body).render(str(tmp_path / "o.mp4"), dry_run=True)
    cp = [c for path, c in r["cache"].items() if "checkpoint" in path]
    assert len(cp) == 1
    vf = cp[0][cp[0].index("-vf") + 1]
    assert vf.startswith("tpad=stop=-1:stop_mode=clone,")
    assert "-t" in cp[0]
    # compute() も同じ
    p = sv.Project()
    p.configure(width=_W, height=_H, fps=30)
    p._dry_run = True
    s = sv.stills([(str(tmp_path / "img" / "s0.png"), 1.0)])
    s <= sv.fade(lambda u: u)
    s.compute(duration=2)
    cmd = next(c for path, c in p._pending_compute_cmds.items() if "compute" in path)
    assert cmd[cmd.index("-vf") + 1].startswith("tpad=stop=-1:stop_mode=clone,")


_MIXED_LAYER = (
    "import os\n"
    "d = os.path.join(os.path.dirname(here('x')), 'img')\n"
    "s = stills([(os.path.join(d, 's0.png'), 0), (os.path.join(d, 's1.png'), 0.4),\n"
    "            (os.path.join(d, 's2.png'), 1.0)], total=1.5)\n"
    "s.time(2) <= fade(lambda u: 1 - u)\n"
    "class A:\n"
    "    shape = (16, 32, 4); dtype = 'uint8'\n"
    "    def __init__(self, i): self.i = i\n"
    "    def tobytes(self): return bytes((0, 10 * self.i, 0, 255)) * (32 * 16)\n"
    "f = frames(A, 12, key='green-ramp', size=(32, 16))\n"
    "f @ 0.5\n"
    "f.time(1)\n")


def test_dry_run_is_same_cold_and_warm(tmp_path):
    """実レンダの前後で dry_run の出力が変わらない（CLAUDE.md §3 の契約）"""
    _need_ffmpeg()
    _pages(tmp_path, 3)
    out = str(tmp_path / "out.mp4")
    cold = _project(tmp_path, _MIXED_LAYER).render(out, dry_run=True)
    assert sorted(k.replace("\\", "/").split("/")[2] for k in cold["cache"]) == [
        "checkpoint", "frames", "stills"]
    _project(tmp_path, _MIXED_LAYER).render(out, timeout=300)
    for path in cold["cache"]:
        assert os.path.getsize(path) > 0, path
    warm = _project(tmp_path, _MIXED_LAYER).render(out, dry_run=True)
    assert json.dumps(cold, sort_keys=True) == json.dumps(warm, sort_keys=True)


def test_dry_run_is_same_cold_and_warm_with_time_live_effect(tmp_path):
    """speed（live）+ fade（焼く）でも cold / warm が一致する。

    ベイク尺は素材の尺。生成済みの .mov を probe すると 46/30 秒が 1.533333 に丸まり、
    チェックポイントの鍵が実レンダの前後で変わっていた。
    """
    _need_ffmpeg()
    _pages(tmp_path, 3)
    body = ("import os\n"
            "d = os.path.join(os.path.dirname(here('x')), 'img')\n"
            "s = stills([(os.path.join(d, 's0.png'), 0.5), (os.path.join(d, 's1.png'), 0.31),\n"
            "            (os.path.join(d, 's2.png'), 0.72)])\n"
            "s.time(2.5) <= speed(2.0) & fade(lambda u: 1)\n")
    out = str(tmp_path / "out.mp4")
    cold = _project(tmp_path, body).render(out, dry_run=True)
    assert any("checkpoint" in k for k in cold["cache"])
    _project(tmp_path, body).render(out, timeout=300)
    warm = _project(tmp_path, body).render(out, dry_run=True)
    assert json.dumps(cold, sort_keys=True) == json.dumps(warm, sort_keys=True)


# --- (d) 実レンダ: 切り替わりのフレーム ----------------------------------------

def test_generated_video_switches_on_exact_frames(tmp_path):
    """生成物そのもの: 各絵が決めたフレーム数だけ、可逆で入っている"""
    _need_ffmpeg()
    paths = _pages(tmp_path, 6)
    secs = [0.5, 0.31, 0.1, 0.72, 0.05, 0.4]
    s = sv.stills(list(zip(paths, secs)), fps=30)
    assert s.frame_counts == [15, 9, 3, 22, 1, 12]
    want = []
    for i, n in enumerate(s.frame_counts):
        want.extend([(40 + 40 * i, 0, 0, 255)] * n)
    assert _center_pixels(s.source) == want
    # タイムベースは 1/fps で割り切れる（tpad のクローンが丸まらない）
    tb = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=time_base,codec_name,pix_fmt", "-of", "csv=p=0", s.source],
        check=True, capture_output=True, text=True, timeout=30).stdout.strip()
    codec, pix_fmt, time_base = tb.split(",")
    assert (codec, pix_fmt) == ("qtrle", "argb")
    assert int(time_base.split("/")[1]) % 30 == 0


def test_many_stills_do_not_drift(tmp_path):
    """200 枚・端数つきの尺でも、k 枚目の切り替わりが round(累積秒×fps) のフレームに載る"""
    _need_ffmpeg()
    paths = _pages(tmp_path, 5)
    items = [(paths[k % 5], 0.11 + 0.01 * (k % 3)) for k in range(200)]
    s = sv.stills(items, fps=30)
    px = _center_pixels(s.source)
    assert len(px) == sum(s.frame_counts)
    acc, f0 = 0.0, 0
    for k, (_, sec) in enumerate(items):
        acc += sec
        f1 = int(acc * 30 + 0.5 + 1e-9)
        want = (40 + 40 * (k % 5), 0, 0, 255)
        assert px[f0:f1] == [want] * (f1 - f0), (k, f0, f1)
        f0 = f1
    assert f0 == len(px)


@pytest.mark.parametrize("fps", [30, 60, 24])
def test_final_output_switches_on_exact_frames(tmp_path, fps):
    """最終出力: 端数の開始時刻に置いても、切り替わりが計算どおりのフレームに映る。

    列は 1.017 秒開始 → 最も近いフレームから始まる。素材（round 済み）の後は
    time(3) の終わりまで最後の絵が残り、その後は背景（青）に戻る。
    """
    _need_ffmpeg()
    _pages(tmp_path, 6)
    p = _project(tmp_path, _STILLS_LAYER, fps=fps)
    out = tmp_path / "out.mp4"
    p.render(str(out), timeout=300)
    px = _center_pixels(out)

    secs = [0.5, 0.31, 0.1, 0.72, 0.05, 0.4]
    start = int(1.017 * fps + 0.5)
    bounds, acc = [0], 0.0
    for sec in secs:
        acc += sec
        bounds.append(int(acc * fps + 0.5 + 1e-9))
    last = int((1.017 + 3) * fps + 1e-9)       # enable の窓（閉区間）の最後のフレーム
    want_red = {}
    for i in range(6):
        for f in range(bounds[i], bounds[i + 1]):
            want_red[start + f] = 40 + 40 * i
    for f in range(start + bounds[-1], last + 1):
        want_red[f] = 240                         # 最後の絵が残る
    assert len(px) >= last + 2
    for f, (r, g, b, _) in enumerate(px):
        if f in want_red:
            assert abs(r - want_red[f]) <= 6 and b < 30, (
                f"{f} 枚目: 赤 {r}（期待 {want_red[f]}）青 {b}")
        else:
            assert r < 30 and b > 200, f"{f} 枚目は背景のはず: {(r, g, b)}"


# --- (e) alpha ----------------------------------------------------------------

def test_alpha_is_preserved(tmp_path):
    """半透明・全透明の画素が生成物にそのまま入り、最終出力で下が透ける"""
    _need_ffmpeg()
    a = _png(tmp_path / "img" / "half.png", (255, 0, 0, 128))
    b = _png(tmp_path / "img" / "clear.png", (0, 0, 0, 0))
    c = _png(tmp_path / "img" / "solid.png", (0, 255, 0, 255))
    s = sv.stills([(a, 0.1), (b, 0.1), (c, 0.1)], fps=30)
    assert _center_pixels(s.source) == (
        [(255, 0, 0, 128)] * 3 + [(0, 0, 0, 0)] * 3 + [(0, 255, 0, 255)] * 3)

    body = ("import os\n"
            "d = os.path.join(os.path.dirname(here('x')), 'img')\n"
            "stills([(os.path.join(d, 'half.png'), 0.2), (os.path.join(d, 'clear.png'), 0.2),\n"
            "        (os.path.join(d, 'solid.png'), 0.2)]).time()\n"
            "pause.time(0.2)\n")
    out = tmp_path / "out.mp4"
    _project(tmp_path, body).render(str(out), timeout=300)
    px = _center_pixels(out)
    r, g, b_, _ = px[2]          # 半透明の赤 × 青の背景 → 紫
    assert 90 < r < 165 and 90 < b_ < 165 and g < 40, px[2]
    r, g, b_, _ = px[8]          # 全透明 → 背景の青
    assert r < 30 and b_ > 200, px[8]
    r, g, b_, _ = px[14]         # 不透明の緑
    assert g > 200 and r < 40 and b_ < 40, px[14]


def test_size_fits_and_pads_transparent(tmp_path):
    """size= は縦横比を保って収め、余白は透明"""
    _need_ffmpeg()
    a = _png(tmp_path / "img" / "sq.png", (200, 0, 0, 255), 16, 16)
    s = sv.stills([(a, 0.1)], size=(64, 32), fps=30)
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", s.source,
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        check=True, capture_output=True, timeout=60).stdout
    assert len(out) == 64 * 32 * 4

    def at(x, y):
        return tuple(out[(y * 64 + x) * 4:(y * 64 + x) * 4 + 4])
    assert at(32, 16) == (200, 0, 0, 255)      # 中央は絵（32x32 に拡大）
    assert at(4, 16)[3] == 0 and at(60, 16)[3] == 0   # 左右の余白は透明


# --- (g) frames() --------------------------------------------------------------

def test_frames_writes_each_frame_and_caches(tmp_path):
    _need_ffmpeg()
    calls = []

    def draw(i):
        calls.append(i)
        return _Arr((10 * i, 0, 255 - 10 * i, 128 if i % 2 else 255))

    f = sv.frames(draw, 8, key=["ramp", 1], size=(_W, _H), fps=30)
    assert calls == list(range(8))
    assert _is_hold_artifact_path(f.source) and f.source.endswith(".mov")
    assert _center_pixels(f.source) == [
        (10 * i, 0, 255 - 10 * i, 128 if i % 2 else 255) for i in range(8)]
    # 一時ファイルが残っていない
    assert os.listdir(os.path.dirname(f.source)) == [os.path.basename(f.source)]
    # 同じ key なら draw を呼ばない
    g = sv.frames(draw, 8, key=["ramp", 1], size=(_W, _H), fps=30)
    assert g.source == f.source and calls == list(range(8))
    # key・コマ数・fps・size が変われば別の生成物
    assert sv.frames(draw, 8, key=["ramp", 2], size=(_W, _H), fps=30).source != f.source
    assert sv.frames(draw, 7, key=["ramp", 1], size=(_W, _H), fps=30).source != f.source
    assert sv.frames(draw, 8, key=["ramp", 1], size=(_W, _H), fps=60).source != f.source


def test_frames_accepts_pil_image(tmp_path):
    _need_ffmpeg()
    Image = pytest.importorskip("PIL.Image")

    def draw(i):
        return Image.new("RGB" if i else "RGBA", (_W, _H),
                         (0, 200, 0) if i else (9, 8, 7, 6))

    f = sv.frames(draw, duration=0.1, key="pil", size=(_W, _H), fps=30)
    assert _center_pixels(f.source) == [(9, 8, 7, 6), (0, 200, 0, 255), (0, 200, 0, 255)]


def test_frames_duration_rounds_to_frames_and_reports_length(tmp_path):
    p = sv.Project()
    p.configure(width=_W, height=_H, fps=30)
    p._dry_run = True
    f = sv.frames(lambda i: None, duration=0.52, key="k")     # 15.6 → 16
    assert f.length() == pytest.approx(16 / 30)
    cmd = p._pending_compute_cmds[f.source]
    assert cmd[cmd.index("-frames:v") + 1] == "16"
    assert cmd[cmd.index("-s") + 1] == f"{_W}x{_H}"            # size 省略時は Project の解像度
    assert cmd[cmd.index("-i") + 1] == "-"
    assert sv.frames(lambda i: None, duration=0.001, key="k2").length() == pytest.approx(1 / 30)


def test_frames_dry_run_and_plan_do_not_call_draw(tmp_path):
    def draw(i):
        raise AssertionError("dry_run で draw が呼ばれた")
    body = ("def draw(i):\n"
            "    raise AssertionError('dry_run で draw が呼ばれた')\n"
            "frames(draw, 5, key='never').time(1)\n")
    r = _project(tmp_path, body).render(str(tmp_path / "o.mp4"), dry_run=True)
    (path, cmd), = r["cache"].items()
    assert "frames" in path and not os.path.exists(path)


def test_frames_draw_failure_leaves_nothing(tmp_path):
    """draw が途中で失敗したら、キャッシュにも一時ファイルにも何も残さない"""
    _need_ffmpeg()

    def draw(i):
        if i == 3:
            raise RuntimeError("描けない")
        return _Arr((1, 2, 3, 255))

    with pytest.raises(RuntimeError, match="描けない"):
        sv.frames(draw, 6, key="boom", size=(_W, _H), fps=30)
    d = os.path.join("__cache__", "artifacts", "frames")
    assert os.listdir(d) == []


def test_frames_rejects_wrong_frame(tmp_path):
    _need_ffmpeg()
    with pytest.raises(ValueError, match=r"\(高さ, 幅, 4\)"):
        sv.frames(lambda i: _Arr((0, 0, 0, 0), w=8, h=8), 2, key="a", size=(_W, _H), fps=30)
    with pytest.raises(ValueError, match="uint8"):
        sv.frames(lambda i: _Arr((0, 0, 0, 0), dtype="float32"), 2, key="b",
                  size=(_W, _H), fps=30)
    with pytest.raises(TypeError, match="PIL.Image か RGBA の numpy 配列"):
        sv.frames(lambda i: b"raw", 2, key="c", size=(_W, _H), fps=30)
    assert os.listdir(os.path.join("__cache__", "artifacts", "frames")) == []


def test_frames_hold_in_real_render(tmp_path):
    """frames() も time() で伸ばすと最後のコマが残る"""
    _need_ffmpeg()
    body = ("class A:\n"
            "    shape = (16, 32, 4); dtype = 'uint8'\n"
            "    def __init__(self, i): self.i = i\n"
            "    def tobytes(self): return bytes((40 + 40 * self.i, 0, 0, 255)) * (32 * 16)\n"
            "frames(A, 5, key='steps', size=(32, 16)).time(0.5)\n"
            "pause.time(0.3)\n")
    out = tmp_path / "out.mp4"
    _project(tmp_path, body).render(str(out), timeout=300)
    px = _center_pixels(out)
    for f in range(5):
        assert abs(px[f][0] - (40 + 40 * f)) <= 6, (f, px[f])
    for f in range(5, 16):                      # 0.5 秒（閉区間で 15 枚目まで）は最後のコマ
        assert abs(px[f][0] - 200) <= 6, (f, px[f])
    for f in range(17, len(px)):
        assert px[f][0] < 30 and px[f][2] > 200, (f, px[f])


def test_baked_effect_keeps_running_while_holding(tmp_path):
    """素材より長く表示した Object に fade を焼いても、伸ばした区間で fade が進む"""
    _need_ffmpeg()
    _png(tmp_path / "img" / "s0.png", (255, 255, 255, 255))
    body = ("import os\n"
            "p = os.path.join(os.path.dirname(here('x')), 'img', 's0.png')\n"
            "stills([(p, 0.2)]).time(2) <= fade(lambda u: 1 - u)\n")
    out = tmp_path / "out.mp4"
    _project(tmp_path, body, bg="black").render(str(out), timeout=300)
    px = _center_pixels(out)
    lum = [p[0] for p in px]
    assert lum[0] > 235
    assert 100 < lum[30] < 150          # 1 秒（素材の後）で半分
    assert lum[54] < 40                 # 1.8 秒でほぼ消える
    assert all(a >= b - 2 for a, b in zip(lum, lum[1:])), lum


def _runs(px):
    """最終出力の中央画素を R / G / B / k（背景の黒）の連なり [(色, 枚数), …] にする"""
    out = []
    for r, g, b, _ in px:
        c = "R" if r > 150 else "G" if g > 150 else "B" if b > 150 else "k"
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return [tuple(x) for x in out]


@pytest.mark.parametrize("ops, want", [
    # speed だけ（焼かない）: 0.75 秒ぶんの中身の後、2.5 秒の終わりまで最後の絵
    ("speed(2.0)", "RGB"),
    # speed + 焼ける Effect: チェックポイントは素材の尺ぶんしか無いが、保持される
    ("speed(2.0) & fade(lambda u: 1)", "RGB"),
    ("fade(lambda u: 1) & freeze_frame(0.25, 0.5)", "RGB"),
])
def test_hold_survives_time_live_effect_with_baked_effect(tmp_path, ops, want):
    """時間系の live Effect と焼ける Effect を併用しても、伸ばした区間に最後の絵が残る"""
    _need_ffmpeg()
    for i, c in enumerate([(250, 0, 0), (0, 250, 0), (0, 0, 250)]):
        _png(tmp_path / "img" / f"c{i}.png", c + (255,))
    body = ("import os\n"
            "d = os.path.join(os.path.dirname(here('x')), 'img')\n"
            "s = stills([(os.path.join(d, f'c{i}.png'), 0.5) for i in range(3)])\n"
            f"s.time(2.5) <= {ops}\n"
            "pause.time(0.3)\n")
    out = tmp_path / "out.mp4"
    _project(tmp_path, body, bg="black").render(str(out), timeout=300)
    runs = _runs(_center_pixels(out))
    assert "".join(c for c, _ in runs) == want + "k", runs
    assert sum(n for c, n in runs[:-1]) == 76, runs       # 2.5 秒の閉区間 = 76 枚
    assert runs[-2][1] >= 30, f"最後の絵が残っていない: {runs}"
    assert runs[-1][1] >= 6, runs                           # その後は背景


# --- (i) 画素形式の混在・コマ数の検証 -------------------------------------------

def test_mixed_pixel_formats_keep_every_still(tmp_path):
    """RGB / RGBA / グレースケールの PNG を混ぜても、どの絵も消えない。

    -reinit_filter 0 が無いと、形式が変わるたびにフィルタグラフが作り直されて
    fps フィルタの抱えていたコマが捨てられ、exit 0 のまま前の絵が消える
    （FFmpeg 8.0 実測: RGB + RGBA が 6 コマとも2枚目の絵）。
    """
    _need_ffmpeg()
    rgb = _png(tmp_path / "img" / "rgb.png", (200, 0, 0), color_type=2)
    rgba = _png(tmp_path / "img" / "rgba.png", (0, 200, 0, 128))
    gray = _png(tmp_path / "img" / "gray.png", (90,), color_type=0)
    s = sv.stills([(rgb, 0.1), (rgba, 0.1), (gray, 0.1), (rgb, 0.1), (rgba, 0.1)], fps=30)
    assert _center_pixels(s.source) == (
        [(200, 0, 0, 255)] * 3 + [(0, 200, 0, 128)] * 3 + [(90, 90, 90, 255)] * 3
        + [(200, 0, 0, 255)] * 3 + [(0, 200, 0, 128)] * 3)
    # 先頭が RGBA でも同じ（alpha が保たれる）
    s2 = sv.stills([(rgba, 0.1), (rgb, 0.1)], fps=30)
    assert _center_pixels(s2.source) == [(0, 200, 0, 128)] * 3 + [(200, 0, 0, 255)] * 3
    # size= つき（明示の scale を通る経路）
    s3 = sv.stills([(rgb, 0.1), (rgba, 0.1), (gray, 0.1)], fps=30, size=(_W * 2, _H * 2))
    px = _center_pixels(s3.source, _W * 2, _H * 2)
    assert len(px) == 9
    assert px[0] == (200, 0, 0, 255) and px[8] == (90, 90, 90, 255)
    assert px[4][1] >= 198 and px[4][0] == 0 and 126 <= px[4][3] <= 130, px[4]


def test_mixed_codecs_are_rejected(tmp_path):
    """PNG と JPEG の混在は検証で止める（デコーダが最初の画像のもので固定され、
    後ろの絵が 'Invalid data' で落ちて1コマだけの動画が exit 0 で出来るため）"""
    a = _pages(tmp_path, 1)
    jpg = tmp_path / "img" / "y.jpg"
    jpg.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    with pytest.raises(ValueError, match="形式が揃っていません.*PNG.*JPEG"):
        sv.stills([(a[0], 0.1), (str(jpg), 0.1)], fps=30)
    with pytest.raises(ValueError, match="形式が揃っていません.*JPEG.*PNG"):
        sv.stills([(str(jpg), 0.1), (a[0], 0.1)], fps=30)
    # 拡張子ではなく中身で見る（.png という名前の JPEG）
    fake = tmp_path / "img" / "fake.png"
    fake.write_bytes(jpg.read_bytes())
    with pytest.raises(ValueError, match="形式が揃っていません"):
        sv.stills([(a[0], 0.1), (str(fake), 0.1)], fps=30)
    assert not os.path.exists("__cache__")


def test_mixed_jpeg_subsampling(tmp_path):
    """同じ形式の中での違い（JPEG の 4:2:0 / 4:4:4 / グレースケール）は混ぜられる"""
    _need_ffmpeg()
    Image = pytest.importorskip("PIL.Image")
    os.makedirs("img", exist_ok=True)
    Image.new("RGB", (_W, _H), (0, 0, 200)).save("img/a.jpg", quality=95)
    Image.new("RGB", (_W, _H), (200, 0, 0)).save("img/b.jpg", quality=95, subsampling=0)
    Image.new("L", (_W, _H), 77).save("img/c.jpg", quality=95)
    s = sv.stills([("img/a.jpg", 0.1), ("img/b.jpg", 0.1), ("img/c.jpg", 0.1)], fps=30)
    px = _center_pixels(s.source)
    assert len(px) == 9
    for i, want in enumerate([(0, 0, 200)] * 3 + [(200, 0, 0)] * 3 + [(77, 77, 77)] * 3):
        assert all(abs(a - b) <= 4 for a, b in zip(px[i][:3], want)), (i, px[i])


def test_short_video_is_not_committed(tmp_path, monkeypatch):
    """コマ数の足りない動画（ffmpeg が exit 0 でも）はキャッシュへ確定しない"""
    _need_ffmpeg()
    a = _pages(tmp_path, 2)
    real = stillseq._count_video_frames
    monkeypatch.setattr(stillseq, "_count_video_frames", lambda path: real(path) - 1)
    with pytest.raises(RuntimeError, match=r"stills.*コマ数が合いません（6 コマのはずが 5 コマ）"):
        sv.stills([(a[0], 0.1), (a[1], 0.1)], fps=30)
    d = os.path.join("__cache__", "artifacts", "stills")
    assert [f for f in os.listdir(d) if f.endswith(".mov")] == []
    with pytest.raises(RuntimeError, match=r"frames.*コマ数が合いません（4 コマのはずが 3 コマ）"):
        sv.frames(lambda i: _Arr((1, 2, 3, 255)), 4, key="short", size=(_W, _H), fps=30)
    assert os.listdir(os.path.join("__cache__", "artifacts", "frames")) == []
    # 検証が通れば確定する（数え方そのものが合っている）
    monkeypatch.setattr(stillseq, "_count_video_frames", real)
    s = sv.stills([(a[0], 0.1), (a[1], 0.1)], fps=30)
    assert real(s.source) == 6


@pytest.mark.parametrize("exc", [FileNotFoundError, PermissionError, BrokenPipeError, OSError])
def test_frames_draw_oserror_is_not_swallowed(tmp_path, exc):
    """draw が OSError 系（素材やフォントを開けない等）を出しても握りつぶさない。

    「ffmpeg が先に落ちた」と見なして握ると、途中までの動画が exit 0 で
    キャッシュに確定し、以後同じ key で命中し続ける（draw は二度と呼ばれない）。
    """
    _need_ffmpeg()
    calls = []

    def draw(i):
        calls.append(i)
        if i == 3:
            raise exc("素材を開けない")
        return _Arr((1, 2, 3, 255))

    with pytest.raises(exc, match="素材を開けない"):
        sv.frames(draw, 6, key="oserr", size=(_W, _H), fps=30)
    assert calls == [0, 1, 2, 3]
    assert os.listdir(os.path.join("__cache__", "artifacts", "frames")) == []
    # 実際の open() の失敗でも同じ
    def draw2(i):
        if i == 2:
            open(os.path.join(str(tmp_path), "no_such_file.bin"), "rb")
        return _Arr((1, 2, 3, 255))
    with pytest.raises(FileNotFoundError):
        sv.frames(draw2, 6, key="oserr2", size=(_W, _H), fps=30)
    assert os.listdir(os.path.join("__cache__", "artifacts", "frames")) == []


# --- (h) エラー ---------------------------------------------------------------

def test_stills_errors(tmp_path):
    a = _pages(tmp_path, 2)
    big = _png(tmp_path / "img" / "big.png", (0, 0, 0, 255), 64, 32)
    with pytest.raises(ValueError, match="1つ以上"):
        sv.stills([])
    with pytest.raises(ValueError, match="1つ以上"):
        sv.stills("a.png")
    with pytest.raises(ValueError, match=r"items\[1\] は \(画像パス, 秒\)"):
        sv.stills([(a[0], 1), a[1]])
    with pytest.raises(ValueError, match=r"items\[0\] の表示秒"):
        sv.stills([(a[0], -1)])
    with pytest.raises(ValueError, match="0 より大きく"):
        sv.stills([(a[0], 1), (a[1], 0)])
    with pytest.raises(ValueError, match="数値で指定"):
        sv.stills([(a[0], "1")])
    with pytest.raises(ValueError, match="NaN"):
        sv.stills([(a[0], float("nan"))])
    with pytest.raises(FileNotFoundError, match="画像が見つかりません"):
        sv.stills([(a[0], 1), (str(tmp_path / "none.png"), 1)])
    with pytest.raises(ValueError, match="画像のみ"):
        open(tmp_path / "clip.mp4", "wb").close()
        sv.stills([(str(tmp_path / "clip.mp4"), 1)])
    with pytest.raises(ValueError, match="寸法が揃っていません.*32x16.*64x32"):
        sv.stills([(a[0], 1), (big, 1)])
    with pytest.raises(ValueError, match="1フレームに満ちません"):
        sv.stills([(a[0], 1), (a[1], 0.01)], fps=30)
    with pytest.raises(ValueError, match="size は"):
        sv.stills([(a[0], 1)], size=(64,))
    with pytest.raises(ValueError, match="size は"):
        sv.stills([(a[0], 1)], size=(64, 0))
    with pytest.raises(ValueError, match="fps"):
        sv.stills([(a[0], 1)], fps=0)
    # 開始秒の形
    with pytest.raises(ValueError, match="最初の絵の開始秒は 0"):
        sv.stills([(a[0], 0.5), (a[1], 1)], total=2)
    with pytest.raises(ValueError, match="昇順"):
        sv.stills([(a[0], 0), (a[1], 1), (a[0], 1)], total=2)
    with pytest.raises(ValueError, match="最後の絵の開始秒"):
        sv.stills([(a[0], 0), (a[1], 1)], total=1)
    with pytest.raises(ValueError, match=r"items\[1\] の開始秒"):
        sv.stills([(a[0], 0), (a[1], -1)], total=1)
    assert not os.path.exists("__cache__"), "検証で落ちたら何も作らない"


def test_frames_errors(tmp_path):
    def draw(i):
        raise AssertionError("検証で落ちるので呼ばれない")
    with pytest.raises(TypeError, match="draw は"):
        sv.frames("not callable", 3, key="k")
    with pytest.raises(TypeError):
        sv.frames(draw, 3)                       # key は必須（キーワード専用）
    with pytest.raises(ValueError, match="key（キャッシュ鍵）は必須"):
        sv.frames(draw, 3, key="")
    with pytest.raises(ValueError, match="key（キャッシュ鍵）は必須"):
        sv.frames(draw, 3, key=None)
    with pytest.raises(TypeError, match="JSON にできる値"):
        sv.frames(draw, 3, key=object())
    with pytest.raises(ValueError, match="どちらか一方"):
        sv.frames(draw, key="k")
    with pytest.raises(ValueError, match="どちらか一方"):
        sv.frames(draw, 3, duration=1, key="k")
    for bad in (0, -1, 2.5, True):
        with pytest.raises(ValueError, match="n_frames は 1 以上の整数"):
            sv.frames(draw, bad, key="k")
    with pytest.raises(ValueError, match="duration"):
        sv.frames(draw, duration=0, key="k")
    with pytest.raises(ValueError, match="duration"):
        sv.frames(draw, duration=-1, key="k")
    with pytest.raises(ValueError, match="size は"):
        sv.frames(draw, 3, key="k", size="hd")
    assert not os.path.exists("__cache__")


def test_ffconcat_list_has_framerate_option_and_exact_durations(tmp_path):
    """リストの各 file に option framerate があり、duration の合計が総尺と一致する"""
    paths = [str(tmp_path / "it's.png"), str(tmp_path / "b.png")]
    text = stillseq._ffconcat_text(paths, [0, 10], 17, _fps_fraction(30.0))
    lines = text.splitlines()
    assert lines[0] == "ffconcat version 1.0"
    assert lines.count("option framerate 30") == 3           # 2枚 + 終端の再掲
    assert "\r" not in text
    assert any(l.endswith("it'\\''s.png'") for l in lines)   # ' のエスケープ
    durs = [float(l.split()[1]) for l in lines if l.startswith("duration")]
    assert durs == [0.333333, 0.233334]                       # 合計 0.566667 = 17/30 の µs 丸め
    assert lines[-2] == "file '" + str(tmp_path / "b.png").replace("\\", "/") + "'", (
        "絶対パス・/ 区切りで書く")
