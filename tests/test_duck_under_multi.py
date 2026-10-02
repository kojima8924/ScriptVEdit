# -*- coding: utf-8 -*-
"""duck_under の複数相手対応と、audit の audio-overlap-no-duck の組単位判定。

- duck_under(a, b, c) / duck_under([a, b, c]) の両方を受け、Narration は .audio。
- 検出用枝は各 other の加工チェーンの形式統一（aformat でステレオ化）より前から
  asplit で取り出す（揃えた後から取るとモノラルが -3dB で検出されダッキングが浅い）。
- 相手が1つなら検出用枝は素材のチャンネル構成のまま（HEAD 以前と同じ検出レベル）。
  複数なら各枝を 48kHz モノラルへダウンミックスしてから amix=normalize=0 で合算し
  apad する（形式を揃えないと合算結果が先頭入力に従い、並び順で検出レベルが変わる）。
- audit は「BGM 役（duck_under / loop を持つ音声）と、それがダックしていない
  音声」が1秒以上重なる組を数え、件数をメッセージに出す（以前は duck_under が
  どこかに1つでもあれば検査を丸ごと飛ばしていた）。前景同士（同じ duck_under の
  相手どうし・ナレーションと効果音）は数えない。BGM 役が1つも無いときは全ての組。
- sfx() の生成物は [0, 最後の at + 素材長] ではなく各発音区間で重なりを判定する。
- README の定番構成（ダック済み BGM＋ナレーション＋sfx）で warning が 0 件
  （p.audit(strict=True) / render(strict=True) が止まらない）。
"""
import re
import shutil
import subprocess

import pytest

from scriptvedit import Object, Project, duck_under, loop, sfx
from scriptvedit.audio import Narration, _duck_targets
from scriptvedit.context import _exec_stack, activate, current_project
from scriptvedit.filters.audio import (
    _MIX_AUDIO_FORMAT, _SIDECHAIN_FORMAT, _SIDECHAIN_MIX_FORMAT)


@pytest.fixture(autouse=True)
def _restore_project_globals():
    old_current = current_project()
    old_stack = list(_exec_stack)
    activate(None)
    _exec_stack[:] = []
    try:
        yield
    finally:
        activate(old_current)
        _exec_stack[:] = old_stack


def _mk():
    p = Project()
    p.configure(width=160, height=90, fps=10, background_color="black")
    return p


def _tone(tmp_path, name, seconds, freq=440):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い環境")
    wav = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"sine=frequency={freq}:duration={seconds}", str(wav)],
        check=True, capture_output=True, timeout=30)
    return str(wav)


def _graph(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


# --- ファクトリ ----------------------------------------------------------------

def test_single_and_multiple_forms_normalize_to_tuple():
    _mk()
    a, b, c = Object("a.wav"), Object("b.wav"), Object("c.wav")
    assert _duck_targets(duck_under(a)) == (a,)
    assert _duck_targets(duck_under(a, b, c)) == (a, b, c)
    assert _duck_targets(duck_under([a, b, c])) == (a, b, c)
    assert _duck_targets(duck_under((a, b), c)) == (a, b, c)
    e = duck_under(a, b, ratio=6, threshold=0.03)
    assert (e.params["ratio"], e.params["threshold"]) == (6, 0.03)


def test_narration_is_unwrapped_to_audio():
    _mk()
    voice = Object("n.wav")
    bgm_other = Object("x.wav")
    assert _duck_targets(duck_under(Narration(voice, None), bgm_other)) == (
        voice, bgm_other)


def test_invalid_targets_rejected():
    _mk()
    a = Object("a.wav")
    with pytest.raises(TypeError, match="other"):
        duck_under()
    with pytest.raises(TypeError, match="other"):
        duck_under([])
    with pytest.raises(TypeError, match="other"):
        duck_under(a, "b.wav")
    with pytest.raises(ValueError, match="重複"):
        duck_under(a, a)
    with pytest.raises(ValueError, match="重複"):
        duck_under(Narration(a, None), a)


# --- フィルタグラフ（dry_run）----------------------------------------------------

def _layer(tmp_path, body):
    path = tmp_path / "l_duck.py"
    path.write_text("from scriptvedit import *\n" + body, encoding="utf-8")
    return str(path)


def test_single_other_graph_taps_before_format(tmp_path):
    """相手が1つ: 形式統一の前で asplit → 周波数だけ揃えて apad → sidechaincompress。
    検出用枝はチャンネル構成を変えない（ステレオ化の -3dB が検出に乗らない）"""
    n = _tone(tmp_path, "n.wav", 1)
    bgm = _tone(tmp_path, "bgm.wav", 2, 220)
    p = _mk()
    p.layer(_layer(tmp_path,
                   f"n = Object({n!r}).show(1)\n"
                   f"bgm = Object({bgm!r}).time(2)\n"
                   "bgm <= duck_under(n, ratio=6, threshold=0.03)\n"))
    graph = _graph(p.render(str(tmp_path / "o.mp4"), dry_run=True)["main"])
    parts = graph.split(";")
    assert re.fullmatch(r"\[1:a\]atrim=duration=1,asetpts=PTS-STARTPTS,"
                        r"asplit\[apre0\]\[dside_src1\]", parts[0]), parts[0]
    assert parts[1] == f"[apre0]{_MIX_AUDIO_FORMAT}[a0]"
    assert f"[dside_src1]{_SIDECHAIN_FORMAT},apad[dside1]" in parts
    assert "channel_layouts" not in _SIDECHAIN_FORMAT
    assert ("[a1][dside1]sidechaincompress=threshold=0.03:ratio=6"
            ":attack=20:release=250[duck1]") in parts
    # ミックス側は揃えた後の [a0] をそのまま使う（dmix の分岐はもう無い）
    assert parts[-1] == "[a0][duck1]amix=inputs=2:normalize=0[aout]"
    assert "dmix" not in graph
    assert "dside_src1_" not in graph
    assert _SIDECHAIN_MIX_FORMAT not in graph


def test_multiple_others_are_mixed_into_one_sidechain(tmp_path):
    """3つの相手は形式統一の前で asplit した検出用枝を 48kHz モノラルへ揃え、
    amix(normalize=0) で合算して apad"""
    n1 = _tone(tmp_path, "n1.wav", 1)
    n2 = _tone(tmp_path, "n2.wav", 1, 550)
    n3 = _tone(tmp_path, "n3.wav", 1, 660)
    bgm = _tone(tmp_path, "bgm.wav", 3, 220)
    body = (f"bgm = Object({bgm!r}).show(3)\n"
            f"n1 = Object({n1!r}).time(1)\n"
            f"n2 = Object({n2!r}).time(1)\n"
            f"n3 = Object({n3!r}).time(1)\n")
    p = _mk()
    p.layer(_layer(tmp_path, body + "bgm <= duck_under(n1, n2, n3)\n"))
    graph = _graph(p.render(str(tmp_path / "o.mp4"), dry_run=True)["main"])
    parts = graph.split(";")
    for k in range(3):
        ai = k + 1
        head = next(x for x in parts if x.startswith(f"[{ai + 1}:a]"))
        assert head.endswith(f",asplit[apre{ai}][dside_src0_{k}]"), head
        assert f"[apre{ai}]{_MIX_AUDIO_FORMAT}[a{ai}]" in parts
        assert (f"[dside_src0_{k}]{_SIDECHAIN_MIX_FORMAT}[dside_m0_{k}]"
                in parts)
    assert ("[dside_m0_0][dside_m0_1][dside_m0_2]"
            "amix=inputs=3:normalize=0,apad[dside0]") in parts
    assert "[a0][dside0]sidechaincompress=" in graph
    assert parts[-1] == "[duck0][a1][a2][a3]amix=inputs=4:normalize=0[aout]"

    # リスト形式でも同じグラフになる
    p2 = _mk()
    p2.layer(_layer(tmp_path, body + "bgm <= duck_under([n1, n2, n3])\n"))
    graph2 = _graph(p2.render(str(tmp_path / "o2.mp4"), dry_run=True)["main"])
    assert graph2 == graph


def test_one_voice_ducked_by_two_bgms_is_split_once(tmp_path):
    """同じナレーションを2つの BGM が duck_under する: ナレーションのチェーンを
    形式統一の前で1回だけ asplit=3 し、ミックス用1本と検出用2本に分ける"""
    n = _tone(tmp_path, "n.wav", 1)
    bgm = _tone(tmp_path, "bgm.wav", 2, 220)
    amb = _tone(tmp_path, "amb.wav", 2, 330)
    p = _mk()
    p.layer(_layer(tmp_path,
                   f"n = Object({n!r}).show(1)\n"
                   f"bgm = Object({bgm!r}).show(2)\n"
                   f"amb = Object({amb!r}).time(2)\n"
                   "bgm <= duck_under(n)\n"
                   "amb <= duck_under(n)\n"))
    graph = _graph(p.render(str(tmp_path / "o.mp4"), dry_run=True)["main"])
    parts = graph.split(";")
    heads = [x for x in parts if "asplit" in x]
    assert len(heads) == 1, heads
    assert heads[0].startswith("[1:a]")
    assert heads[0].endswith(",asplit=3[apre0][dside_src1][dside_src2]"), heads[0]
    assert f"[apre0]{_MIX_AUDIO_FORMAT}[a0]" in parts
    for ai in (1, 2):
        assert f"[dside_src{ai}]{_SIDECHAIN_FORMAT},apad[dside{ai}]" in parts
        assert any(x.startswith(f"[a{ai}][dside{ai}]sidechaincompress=")
                   for x in parts)
    assert parts[-1] == "[a0][duck1][duck2]amix=inputs=3:normalize=0[aout]"


def _band_rms_db(path, t0, dur, freq):
    """t0 から dur 秒の freq Hz 付近（±50Hz）の RMS[dB]"""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-ss", str(t0), "-t", str(dur), "-i", str(path),
         "-af", f"bandpass=f={freq}:width_type=h:w=100,astats=metadata=0",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    levels = re.findall(r"RMS level dB: (-?[\d.]+)", proc.stderr)
    assert levels, proc.stderr[-500:]
    return float(levels[-1])


def test_multiple_others_duck_bgm_during_each_other(tmp_path):
    """複数相手のグラフが実際の ffmpeg で通り、どの相手の区間でも BGM が下がる。

    BGM は 440Hz、ナレーション2本は 1500Hz（0〜1秒と2〜3秒）。出力の 440Hz 帯域
    だけを測り、ナレーション区間が無音区間（1〜2秒・3〜4秒）より十分低いこと、
    BGM が総尺まで続くことを確かめる。
    """
    n1 = _tone(tmp_path, "n1.wav", 1, 1500)
    n2 = _tone(tmp_path, "n2.wav", 1, 1500)
    bgm = _tone(tmp_path, "bgm.wav", 4, 440)
    p = _mk()
    p.layer(_layer(tmp_path,
                   f"bgm = Object({bgm!r}).show(4)\n"
                   f"n1 = Object({n1!r}).time(1)\n"
                   "pause.time(1)\n"
                   f"n2 = Object({n2!r}).time(1)\n"
                   "pause.time(1)\n"
                   "bgm <= duck_under(n1, n2, ratio=20, threshold=0.02)\n"))
    out = tmp_path / "multi.mp4"
    p.render(str(out), timeout=60)
    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=duration", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True, timeout=30).stdout.strip()
    assert float(dur) >= 3.95
    during_n1 = _band_rms_db(out, 0.2, 0.6, 440)
    gap = _band_rms_db(out, 1.2, 0.6, 440)
    during_n2 = _band_rms_db(out, 2.2, 0.6, 440)
    tail = _band_rms_db(out, 3.2, 0.6, 440)
    assert during_n1 < gap - 6, (during_n1, gap)
    assert during_n2 < tail - 6, (during_n2, tail)


def _ffmpeg_wav(tmp_path, name, *args):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い環境")
    wav = tmp_path / name
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args, str(wav)],
                   check=True, capture_output=True, timeout=30)
    return str(wav)


def test_multiple_others_sidechain_level_does_not_depend_on_order(tmp_path):
    """複数相手の検出レベルは並び順に依らず、相手が1つのときと同じ。

    相手はモノラル 24kHz のナレーション（300Hz）と無音のステレオ 44.1kHz。
    検出用枝の形式を揃えずに amix すると合算結果が先頭入力の形式に従い、
    無音ステレオを先に書くとナレーションがステレオ化（各チャンネル -3dB）されて
    ダッキングが約 2.6dB 浅くなる（ffmpeg 単体で実測: -33.35dB と -30.73dB）。
    BGM（ステレオ 1kHz）の 1kHz 帯域だけを測って比べる。
    """
    voice = _ffmpeg_wav(tmp_path, "voice.wav", "-f", "lavfi", "-i",
                        "sine=frequency=300:sample_rate=24000:duration=3",
                        "-ac", "1")
    silent = _ffmpeg_wav(tmp_path, "silent.wav", "-f", "lavfi", "-i",
                         "anullsrc=r=44100:cl=stereo", "-t", "3")
    bgm = _ffmpeg_wav(tmp_path, "bgm.wav", "-f", "lavfi", "-i",
                      "sine=frequency=1000:sample_rate=44100:duration=3",
                      "-ac", "2")

    def ducked_level(name, others):
        p = _mk()
        body = (f"bgm = Object({bgm!r}).show(3)\n"
                f"v = Object({voice!r}).show(3)\n"
                f"s = Object({silent!r}).time(3)\n")
        if others:
            body += f"bgm <= duck_under({others})\n"
        path = tmp_path / f"l_{name}.py"
        path.write_text("from scriptvedit import *\n" + body, encoding="utf-8")
        p.layer(str(path))
        out = tmp_path / f"{name}.mp4"
        p.render(str(out), timeout=60)
        return _band_rms_db(out, 1.0, 1.5, 1000)

    bare = ducked_level("bare", None)
    single = ducked_level("single", "v")
    voice_first = ducked_level("vs", "v, s")
    silent_first = ducked_level("sv", "s, v")
    assert abs(voice_first - silent_first) < 0.5, (voice_first, silent_first)
    assert abs(voice_first - single) < 0.5, (voice_first, single)
    # 実際に下がっていること（一致が「どれも効いていない」ではない）。
    # -3dB で検出していた版の下げ幅は約 3.6dB、素のモノラル検出は約 6.2dB
    assert single < bare - 4.5, (single, bare)


# --- audit: audio-overlap-no-duck -------------------------------------------------

def _overlap_findings(p):
    return [f for f in p.audit(quiet=True) if f["code"] == "audio-overlap-no-duck"]


def _placed(path, start, dur):
    o = Object(path)
    o.time(dur)
    o.start_time = start
    return o


def test_audit_excludes_only_ducked_pairs(tmp_path):
    """BGM が n1 にだけダックしていれば、n2 との重なりは残る（1組）"""
    p = _mk()
    bgm = _placed(_tone(tmp_path, "bgm.wav", 6, 220), 0, 6)
    n1 = _placed(_tone(tmp_path, "n1.wav", 2), 0, 2)
    _placed(_tone(tmp_path, "n2.wav", 2, 550), 3, 2)
    bgm <= duck_under(n1)
    found = _overlap_findings(p)
    assert len(found) == 1
    assert "1組" in found[0]["message"]
    assert "n2.wav" in found[0]["message"]
    assert "n1.wav" not in found[0]["message"]


def test_audit_passes_when_all_pairs_are_ducked(tmp_path):
    p = _mk()
    bgm = _placed(_tone(tmp_path, "bgm.wav", 6, 220), 0, 6)
    n1 = _placed(_tone(tmp_path, "n1.wav", 2), 0, 2)
    n2 = _placed(_tone(tmp_path, "n2.wav", 2, 550), 3, 2)
    bgm <= duck_under(n1, n2)
    assert _overlap_findings(p) == []


def test_audit_either_direction_counts_as_ducked(tmp_path):
    """どちらがどちらの下にダックしていても、その組は除外される"""
    p = _mk()
    a = _placed(_tone(tmp_path, "a.wav", 3), 0, 3)
    b = _placed(_tone(tmp_path, "b.wav", 3, 550), 0, 3)
    b <= duck_under(a)
    assert _overlap_findings(p) == []
    p2 = _mk()
    a2 = _placed(_tone(tmp_path, "a.wav", 3), 0, 3)
    b2 = _placed(_tone(tmp_path, "b.wav", 3, 550), 0, 3)
    a2 <= duck_under(b2)
    assert _overlap_findings(p2) == []


def test_audit_ignores_overlap_between_duck_targets(tmp_path):
    """同じ duck_under の相手どうし（n1・n2）が重なっても数えない。

    BGM はどちらの間も下がっているので「ナレーションが BGM に埋もれる」問題は
    起きていない。以前は「n1.wav と n2.wav が duck_under で処理されていない」と
    warning になり、提案文が既に書いた bgm <= duck_under(n1, n2) の形だった。
    """
    p = _mk()
    bgm = _placed(_tone(tmp_path, "bgm.wav", 6, 220), 0, 6)
    n1 = _placed(_tone(tmp_path, "n1.wav", 3), 0, 3)
    n2 = _placed(_tone(tmp_path, "n2.wav", 3, 550), 1.5, 3)
    bgm <= duck_under(n1, n2)
    assert _overlap_findings(p) == []
    p.audit(strict=True, quiet=True)   # warning が無いので止まらない


def test_audit_counts_only_pairs_with_the_bgm(tmp_path):
    """BGM 役が居るときは「BGM と、それがダックしていない音」の組だけを数える。

    n2 は n1 とも BGM とも2秒重なるが、報告されるのは BGM との組だけ
    （n1 と n2 は前景同士）。提案文は BGM 側の duck_under へ相手を足す形になる。
    """
    p = _mk()
    bgm = _placed(_tone(tmp_path, "bgm.wav", 6, 220), 0, 6)
    n1 = _placed(_tone(tmp_path, "n1.wav", 3), 0, 3)
    _placed(_tone(tmp_path, "n2.wav", 4, 550), 1, 4)
    bgm <= duck_under(n1)
    found = _overlap_findings(p)
    assert len(found) == 1
    msg = found[0]["message"]
    assert "1組" in msg
    assert "bgm.wav と n2.wav（4.0秒）" in msg
    assert "n1.wav" not in msg
    assert "BGM 側の duck_under に相手を加えて" in msg


def test_audit_loop_marks_the_bgm(tmp_path):
    """duck_under が無くても loop() した音声は BGM 役。ナレーション同士は数えない"""
    p = _mk()
    bgm = _placed(_tone(tmp_path, "bgm.wav", 2, 220), 0, 6)
    bgm <= loop()
    _placed(_tone(tmp_path, "n1.wav", 3), 0, 3)
    _placed(_tone(tmp_path, "n2.wav", 3, 550), 1.5, 3)
    found = _overlap_findings(p)
    assert len(found) == 1
    msg = found[0]["message"]
    assert "2組" in msg                    # bgm×n1 と bgm×n2（n1×n2 は数えない）
    assert "bgm.wav と n1.wav" in msg
    assert "bgm.wav と n2.wav" in msg
    assert "n1.wav と n2.wav" not in msg


def test_audit_ignores_overlap_between_two_bgms(tmp_path):
    """BGM 役どうし（BGM と環境音の重ね敷き等）の重なりは数えない"""
    p = _mk()
    n = _placed(_tone(tmp_path, "n.wav", 2), 0, 2)
    bgm = _placed(_tone(tmp_path, "bgm.wav", 6, 220), 0, 6)
    amb = _placed(_tone(tmp_path, "amb.wav", 6, 330), 0, 6)
    bgm <= duck_under(n)
    amb <= duck_under(n)
    assert _overlap_findings(p) == []


# --- audit: sfx() は各発音区間で判定する -------------------------------------------

def test_audit_readme_standard_setup_has_no_warnings(tmp_path):
    """README の定番構成（loop＋duck_under した BGM・ナレーション2本・sfx の短い
    効果音）で warning が 0 件。p.audit(strict=True) でも止まらない。

    以前は sfx() の生成物（開始0・尺は最後の at＋素材長）を [0, 終端] の1区間で
    扱ったため、0.3秒の効果音2発でも「bgm.wav と sfx（5.3秒）」
    「n1.wav と sfx（2.0秒）」「n2.wav と sfx（2.0秒）」の3組が warning になっていた。
    """
    n1 = _tone(tmp_path, "n1.wav", 2, 1500)
    n2 = _tone(tmp_path, "n2.wav", 2, 1200)
    bgm = _tone(tmp_path, "bgm.wav", 10, 220)
    click = _tone(tmp_path, "click.wav", 0.3, 2000)
    p = _mk()
    p.layer(_layer(tmp_path,
                   f"bgm = Object({bgm!r}).show(6)\n"
                   f"n1 = Object({n1!r}).time(2)\n"
                   "pause.time(1)\n"
                   f"n2 = Object({n2!r}).time(2)\n"
                   "pause.time(1)\n"
                   f"hit = sfx({click!r}, at=[0.5, 5.0]) @ 0\n"
                   "bgm <= loop() & duck_under(n1, n2, ratio=8)\n"))
    p.normalize_audio()
    findings = p.audit(quiet=True)
    assert [f for f in findings if f["severity"] == "warning"] == []
    # 前提: sfx が音声として検査対象に入っている（素通りで 0 件になっていない）
    assert any(getattr(o, "_sfx_hits", None) is not None and o.has_audio
               for o in p.objects)
    p.audit(strict=True, quiet=True)


def test_audit_sfx_short_hits_do_not_overlap_bgm(tmp_path):
    """0.3秒の効果音2発（区間 [0, 7.3]）は BGM とも声とも1秒以上は重ならない"""
    p = _mk()
    bgm = _placed(_tone(tmp_path, "bgm.wav", 8, 220), 0, 8)
    n1 = _placed(_tone(tmp_path, "n1.wav", 2), 0, 2)
    bgm <= duck_under(n1)
    hit = sfx(_tone(tmp_path, "click.wav", 0.3, 2000), at=[1.0, 7.0])
    assert hit.start_time == 0 and hit._sfx_hits is not None
    assert _overlap_findings(p) == []


def test_audit_sfx_without_bgm_uses_hit_windows(tmp_path):
    """BGM 役が無い（全組を調べる）構成でも、sfx は発音区間で判定する"""
    p = _mk()
    _placed(_tone(tmp_path, "a.wav", 8), 0, 8)
    sfx(_tone(tmp_path, "click.wav", 0.3, 2000), at=[1.0, 7.0])
    assert _overlap_findings(p) == []


def test_audit_sfx_long_hit_is_reported_with_its_source_name(tmp_path):
    """1秒以上鳴る発音は BGM との重なりとして数え、元の音源名で示す。
    秒数は [0, 終端] ではなく発音区間の重なり（1.5秒）"""
    p = _mk()
    bgm = _placed(_tone(tmp_path, "bgm.wav", 8, 220), 0, 8)
    n1 = _placed(_tone(tmp_path, "n1.wav", 2), 0, 2)
    bgm <= duck_under(n1)
    sfx(_tone(tmp_path, "boom.wav", 1.5, 90), at=[4.0])
    found = _overlap_findings(p)
    assert len(found) == 1
    assert "bgm.wav と sfx(boom.wav)（1.5秒）" in found[0]["message"]


def test_audit_sfx_overlapping_hits_are_merged(tmp_path):
    """続けて鳴る発音（0.6秒を0.5秒おき）は1つの連続区間として数える"""
    p = _mk()
    bgm = _placed(_tone(tmp_path, "bgm.wav", 8, 220), 0, 8)
    n1 = _placed(_tone(tmp_path, "n1.wav", 1), 0, 1)
    bgm <= duck_under(n1)
    sfx(_tone(tmp_path, "tick.wav", 0.6, 1800), at=[2.0, 2.5, 3.0])
    found = _overlap_findings(p)
    assert len(found) == 1
    assert "sfx(tick.wav)（1.6秒）" in found[0]["message"]


def test_audit_sfx_with_time_op_falls_back_to_whole_window(tmp_path):
    """発音区間の時刻をずらす op（切り出し等）が付いた sfx は再生区間1つで判定する"""
    p = _mk()
    bgm = _placed(_tone(tmp_path, "bgm.wav", 8, 220), 0, 8)
    n1 = _placed(_tone(tmp_path, "n1.wav", 1), 0, 1)
    bgm <= duck_under(n1)
    hit = sfx(_tone(tmp_path, "click.wav", 0.3, 2000), at=[1.0, 7.0])
    hit[0.5:6.0]
    found = _overlap_findings(p)
    assert len(found) == 1
    assert "bgm.wav と sfx(click.wav)（5.5秒）" in found[0]["message"]


def test_audit_counts_all_overlapping_pairs(tmp_path):
    """重なりの組は最初の1組だけでなく件数も分かる（列挙は先頭3組まで）"""
    p = _mk()
    for i in range(4):
        _placed(_tone(tmp_path, f"s{i}.wav", 3, 300 + 100 * i), 0, 3)
    found = _overlap_findings(p)
    assert len(found) == 1
    msg = found[0]["message"]
    assert "6組" in msg          # 4本が全部重なる → C(4,2)=6 組
    assert "ほか3組" in msg      # 列挙は3組まで


def test_audit_short_overlap_is_ignored(tmp_path):
    """1秒未満の重なり（SFX の一瞬等）は数えない"""
    p = _mk()
    _placed(_tone(tmp_path, "a.wav", 2), 0, 2)
    _placed(_tone(tmp_path, "b.wav", 2, 550), 1.5, 2)
    assert _overlap_findings(p) == []


def test_bgm_too_short_still_uses_duck_detection(tmp_path):
    """bgm-too-short（duck_under を持つ音声＝BGM相当）は複数相手でも働く"""
    p = _mk()
    n1 = _placed(_tone(tmp_path, "n1.wav", 3), 0, 3)
    n2 = _placed(_tone(tmp_path, "n2.wav", 3, 550), 3, 3)
    bgm = _placed(_tone(tmp_path, "bgm.wav", 2, 220), 0, 6)
    bgm <= duck_under(n1, n2)
    assert "bgm-too-short" in [f["code"] for f in p.audit(quiet=True)]
