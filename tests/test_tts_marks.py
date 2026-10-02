# -*- coding: utf-8 -*-
"""VOICEVOX の audio_query の調整（間・無音・抑揚・読み）と、語の時刻 tts_marks() のテスト

オフラインで確かめるもの（HTTP は urllib.request.urlopen をモックする）:
  * audio_query の json からモーラと間の時刻を出す純粋関数（_query_timeline）
  * 文字位置への対応づけと精度の表示（TtsMarks）
  * 鍵の安定: 既定値では調整機能の導入前と同じ鍵。指定した項目だけが鍵に入る
  * 合成に渡すクエリ: 指定した項目だけを書き換える
  * marks.json の控え: 合成時に書く / エンジン停止中でも返す / 壊れていたら作り直す
実エンジン（127.0.0.1:50021）が動いているときだけ確かめるもの:
  * 計算した長さが実 wav の長さと一致する
  * 計算した間の位置が ffmpeg silencedetect の無音区間と合う
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import wave

import pytest

from scriptvedit import tts as svtts

_HOST, _PORT = "127.0.0.1", 50021
_ENDPOINT = "127.0.0.1:50021"
_F = 93.75   # VOICEVOX のフレームレート（24000 / 256）


def _mora(text, consonant, vowel, pitch=5.0):
    return {"text": text, "consonant": "x" if consonant is not None else None,
            "consonant_length": consonant, "vowel": "a", "vowel_length": vowel,
            "pitch": pitch}


def _pause(length):
    return {"text": "、", "consonant": None, "consonant_length": None,
            "vowel": "pau", "vowel_length": length, "pitch": 0.0}


def _phrase(moras, pause=None, interrogative=False):
    return {"moras": moras, "accent": 1,
            "pause_mora": _pause(pause) if pause is not None else None,
            "is_interrogative": interrogative}


def _base_query():
    """「金は戻った。答えは、無い。」相当のクエリ（長さはフレームの整数倍で作ってある）"""
    f = 1 / _F
    return {
        "accent_phrases": [
            _phrase([_mora("キ", 8 * f, 8 * f), _mora("ン", None, 6 * f),
                     _mora("ワ", 5 * f, 6 * f)]),
            _phrase([_mora("モ", 6 * f, 6 * f), _mora("ド", 4 * f, 6 * f),
                     _mora("ッ", None, 13 * f, pitch=0.0), _mora("タ", 2 * f, 18 * f)],
                    pause=34 * f),
            _phrase([_mora("コ", 5 * f, 5 * f), _mora("タ", 7 * f, 9 * f),
                     _mora("エ", None, 7 * f), _mora("ワ", 6 * f, 20 * f)],
                    pause=26 * f),
            _phrase([_mora("ナ", 3 * f, 9 * f), _mora("イ", None, 14 * f)]),
        ],
        "speedScale": 1.0, "pitchScale": 0.0, "intonationScale": 1.0,
        "volumeScale": 1.0, "prePhonemeLength": 0.1, "postPhonemeLength": 0.1,
        "pauseLength": None, "pauseLengthScale": 1.0,
        "outputSamplingRate": 24000, "outputStereo": False,
        "kana": "キ'ンワ/モド'ッタ、コタ'エワ、ナ'イ",
    }


_TEXT = "金は戻った。答えは、無い。"


# =============================================================================
# 純粋関数: クエリ → 時刻
# =============================================================================

def test_timeline_counts_frames():
    tl = svtts._query_timeline(_base_query())
    # 0.1 秒は 9.375 フレーム → 9 フレームへ丸まる
    assert tl["speech_start"] == pytest.approx(9 / _F)
    # 句1: 8+8+6+5+6 = 33 フレーム / 句2: 6+6+4+6+13+2+18 = 55 フレーム
    p = tl["phrases"]
    assert [x["kana"] for x in p] == ["キンワ", "モドッタ", "コタエワ", "ナイ"]
    assert p[0]["start"] == pytest.approx(9 / _F)
    assert p[1]["start"] == pytest.approx((9 + 33) / _F)
    assert p[1]["end"] == pytest.approx((9 + 33 + 55) / _F)
    assert p[0]["pause"] is None
    assert p[1]["pause"] == pytest.approx(((9 + 88) / _F, (9 + 88 + 34) / _F))
    # 句3: 5+5+7+9+7+6+20 = 59、間 26、句4: 3+9+14 = 26、後ろの無音 9
    assert tl["speech_end"] == pytest.approx((9 + 88 + 34 + 59 + 26 + 26) / _F)
    assert tl["duration"] == pytest.approx((9 + 88 + 34 + 59 + 26 + 26 + 9) / _F)
    moras = [e for e in tl["elems"] if e["kind"] == "mora"]
    assert moras[1]["text"] == "ン"
    assert moras[1]["start"] == pytest.approx((9 + 16) / _F)
    assert moras[1]["end"] == pytest.approx((9 + 22) / _F)


def test_timeline_rounds_each_phoneme_separately():
    """子音と母音を別々に丸める（秒を足してから丸めると wav の長さと合わない）"""
    q = {"accent_phrases": [_phrase([_mora("カ", 1.4 / _F, 1.4 / _F)] * 10)],
         "speedScale": 1.0, "prePhonemeLength": 0.0, "postPhonemeLength": 0.0}
    # 1.4 フレーム → 1 フレームが 20 個。合計してから丸めると 28 フレームになってしまう
    assert svtts._query_timeline(q)["duration"] == pytest.approx(20 / _F)


def test_timeline_speed_divides_everything():
    q = _base_query()
    q["speedScale"] = 2.0
    tl = svtts._query_timeline(q)
    # 9.375/2 = 4.6875 → 5 フレーム
    assert tl["speech_start"] == pytest.approx(5 / _F)
    assert tl["phrases"][1]["pause"][1] - tl["phrases"][1]["pause"][0] == \
        pytest.approx(17 / _F)


def test_timeline_pause_length_and_scale():
    q = _base_query()
    q["pauseLengthScale"] = 0.5
    tl = svtts._query_timeline(q)
    assert [round((p["pause"][1] - p["pause"][0]) * _F)
            for p in tl["phrases"] if p["pause"]] == [17, 13]
    q["pauseLength"] = 0.64          # 60 フレーム。倍率は固定値にも掛かる
    tl = svtts._query_timeline(q)
    assert [round((p["pause"][1] - p["pause"][0]) * _F)
            for p in tl["phrases"] if p["pause"]] == [30, 30]


def test_timeline_interrogative_adds_upspeak_mora():
    q = _base_query()
    base = svtts._query_timeline(q)["duration"]
    q["accent_phrases"][-1]["is_interrogative"] = True
    tl = svtts._query_timeline(q)
    assert tl["duration"] - base == pytest.approx(14 / _F)   # 0.15 秒 → 14 フレーム
    # 語尾の伸びは最後のモーラの続きとして数える
    assert tl["elems"][-1]["end"] == pytest.approx(tl["speech_end"])
    # 最後のモーラの pitch が 0（無声）なら足されない
    q["accent_phrases"][-1]["moras"][-1]["pitch"] = 0.0
    assert svtts._query_timeline(q)["duration"] == pytest.approx(base)


# =============================================================================
# 文字位置への対応づけ
# =============================================================================

def test_marks_map_characters_to_times():
    m = svtts.TtsMarks(_TEXT, _base_query())
    assert m.text == _TEXT
    assert m.kana == "キ'ンワ/モド'ッタ、コタ'エワ、ナ'イ"
    assert m.duration == pytest.approx(251 / _F)
    # 句読点は間の開始。確実
    assert m.time_of("。") == pytest.approx(97 / _F)
    assert m.precision_of("。") == "pause"
    assert m.time_of("、") == pytest.approx((97 + 34 + 59) / _F)
    assert [p["index"] for p in m.pauses] == [5, 9]
    assert m.pauses[0]["end"] == pytest.approx(131 / _F)
    # 間の直後の語は確実（漢字で始まっていても）
    assert m.time_of("答えは") == pytest.approx(131 / _F)
    assert m.precision_of("答えは") == "start"
    assert m.time_of("金") == pytest.approx(9 / _F)
    assert m.precision_of("金") == "start"
    # 仮名はモーラ単位（助詞の「は」は ワ に当たる）
    assert m.time_of("は") == pytest.approx((9 + 22) / _F)
    assert m.precision_of("は") == "kana"
    assert m.time_of("は", nth=1) == pytest.approx((131 + 33) / _F)
    assert m.time_of("った") == pytest.approx((42 + 22) / _F)
    # 仮名の直後の漢字は、その次のモーラ
    assert m.time_of("戻") == pytest.approx(42 / _F)
    assert m.precision_of("戻") == "kana"
    # 区間: 次の文字の開始まで。文末は話し終わり
    assert m.span_of("答えは") == pytest.approx((131 / _F, 190 / _F))
    assert m.span_of("無い。")[1] == pytest.approx(m.speech_end)
    # 対応する間の無い文末の「。」は話し終わり
    assert m.time_at(len(_TEXT) - 1) == pytest.approx(m.speech_end)
    assert m.time_at(len(_TEXT)) == pytest.approx(m.speech_end)
    # 時刻は文字位置に対して後戻りしない
    times = [m.time_at(i) for i in range(len(_TEXT))]
    assert times == sorted(times)


def test_marks_kanji_run_is_approximate():
    """漢字が続く所は対応点の間を文字数で按分する（精度 approx）"""
    q = {"accent_phrases": [_phrase([_mora(t, None, 10 / _F) for t in "ハイレツダ"])],
         "speedScale": 1.0, "prePhonemeLength": 0.0, "postPhonemeLength": 0.0}
    m = svtts.TtsMarks("配列だ", q)
    assert m.time_of("配") == 0 and m.precision_of("配") == "start"
    assert m.time_of("列") == pytest.approx(20 / _F)     # ハイレツ の 2/4 から
    assert m.precision_of("列") == "approx"
    assert m.time_of("だ") == pytest.approx(40 / _F) and m.precision_of("だ") == "kana"


def test_marks_small_kana_and_long_vowel():
    q = {"accent_phrases": [_phrase([_mora(t, None, 10 / _F)
                                     for t in ["ト", "オ", "キョ", "オ"]])],
         "speedScale": 1.0, "prePhonemeLength": 0.0, "postPhonemeLength": 0.0}
    m = svtts.TtsMarks("とうきょう", q)
    assert [round(m.time_at(i) * _F) for i in range(5)] == [0, 10, 20, 20, 30]
    assert {m.precision_at(i) for i in range(5)} == {"kana"}


def test_marks_unmatched_brackets_do_not_steal_pauses():
    """鉤括弧は間になることもならないこともある。間の数だけ対応づける"""
    q = {"accent_phrases": [_phrase([_mora("ハ", None, 10 / _F), _mora("イ", None, 10 / _F)],
                                    pause=30 / _F),
                            _phrase([_mora("ト", None, 10 / _F)])],
         "speedScale": 1.0, "prePhonemeLength": 0.0, "postPhonemeLength": 0.0}
    m = svtts.TtsMarks("「はい」と", q)
    assert m.time_of("「") == 0                       # 次に読まれる文字と同じ
    assert m.time_of("」") == pytest.approx(20 / _F)
    assert m.precision_of("」") == "pause"
    assert m.time_of("と") == pytest.approx(50 / _F)
    assert [p["index"] for p in m.pauses] == [3]


def test_marks_with_readings_use_original_positions():
    text = "金は戻った。"
    synth, origin = svtts._apply_readings("tts", text, {"金": "カネ"})
    assert synth == "カネは戻った。"
    assert origin == [0, 0, 1, 2, 3, 4, 5]
    q = {"accent_phrases": [_phrase([_mora(t, None, 10 / _F)
                                     for t in ["カ", "ネ", "ワ", "モ", "ド", "ッ", "タ"]])],
         "speedScale": 1.0, "prePhonemeLength": 0.0, "postPhonemeLength": 0.0}
    m = svtts.TtsMarks(text, q, origin=origin, synth_text=synth)
    assert m.text == text
    assert m.time_of("金") == 0
    assert m.time_of("は") == pytest.approx(20 / _F)
    assert m.time_of("戻") == pytest.approx(30 / _F)
    with pytest.raises(ValueError, match="元の文"):
        m.time_of("カネ")


def test_marks_lookup_errors():
    m = svtts.TtsMarks(_TEXT, _base_query())
    with pytest.raises(ValueError, match="ありません"):
        m.time_of("存在しない語")
    with pytest.raises(ValueError, match="2 個目"):
        m.time_of("答え", nth=1)
    with pytest.raises(ValueError):
        m.time_of("")
    with pytest.raises(IndexError):
        m.time_at(len(_TEXT) + 1)
    with pytest.raises(IndexError):
        m.precision_at(len(_TEXT))


@pytest.mark.parametrize("nth", [-1, 1.0, "0", True, None])
def test_marks_nth_must_be_non_negative_int(nth):
    """負の nth を「文に無い」と取り違えない（文にある語なので原因が違う）"""
    m = svtts.TtsMarks(_TEXT, _base_query())
    with pytest.raises(ValueError, match="nth は 0 以上の整数"):
        m.time_of("答えは", nth=nth)
    with pytest.raises(ValueError, match="nth は 0 以上の整数"):
        m.span_of("答えは", nth=nth)


# =============================================================================
# 記号の並び・括弧と間の対応（漢字だけの区間は仮名の対応点が無く、順序を縛れない）
# =============================================================================

def _kq(*phrases):
    """(モーラの列, 後ろに間があるか) の並びからクエリを作る。モーラ 10 フレーム・間 30 フレーム"""
    return {"accent_phrases": [_phrase([_mora(t, None, 10 / _F) for t in moras],
                                       pause=30 / _F if pause else None)
                               for moras, pause in phrases],
            "speedScale": 1.0, "prePhonemeLength": 0.0, "postPhonemeLength": 0.0}


def _frames(m, word, nth=0):
    return round(m.time_of(word, nth) * _F)


def _starts_are_after_pauses(m):
    """精度 "start" の文字の時刻は、文頭かどれかの間の終わり"""
    ok = [m.speech_start] + [p["end"] for p in m.pauses]
    for i in range(len(m.text)):
        if m.precision_at(i) == "start":
            assert any(m.time_at(i) == pytest.approx(t) for t in ok), (i, m.text[i])


def test_marks_symbol_run_takes_one_pause():
    """「！＋全角空白」は1つの間。空白が次の間を横取りしない（VOICEVOX 0.25.2 の句の形）"""
    text = "第1位、東京！　第2位、大阪！"
    q = _kq(("ダイ", False), ("イチイ", True), (["ト", "オ", "キョ", "オ"], True),
            ("ダイ", False), ("ニイ", True), ("オオサカ", False))
    m = svtts.TtsMarks(text, q)
    # フレーム: ダイ 0-20 イチイ 20-50 間 50-80 トオキョオ 80-120 間 120-150
    #           ダイ 150-170 ニイ 170-190 間 190-220 オオサカ 220-260
    assert [p["index"] for p in m.pauses] == [3, 6, 11]
    assert [text[p["index"]] for p in m.pauses] == ["、", "！", "、"]
    assert _frames(m, "第") == 0 and m.precision_of("第") == "start"
    assert _frames(m, "東京") == 80 and m.precision_of("東京") == "start"
    assert _frames(m, "！") == 120 and m.precision_of("！") == "pause"
    assert _frames(m, "　") == 120 and m.precision_of("　") == "pause"   # 並びは同じ時刻
    assert _frames(m, "第", 1) == 150 and m.precision_of("第", 1) == "start"
    assert _frames(m, "、", 1) == 190
    assert _frames(m, "大阪") == 220 and m.precision_of("大阪") == "start"
    # 文末の「！」は文中の間を取らない（次に読まれる文字が無いので話し終わり）
    assert m.time_of("！", 1) == pytest.approx(m.speech_end)
    assert m.precision_of("！", 1) == "approx"
    # 近似の文字は間をまたがない
    assert 150 < _frames(m, "2") < 190 and m.precision_of("2") == "approx"
    assert 150 < _frames(m, "位", 1) < 190
    _starts_are_after_pauses(m)
    times = [m.time_at(i) for i in range(len(text))]
    assert times == sorted(times)


def test_marks_bracket_next_to_period_shares_its_pause():
    text = "結論。「不明」。以上。"
    q = _kq(("ケツロン", True), ("フメエ", True), (["イ", "ジョ", "オ"], False))
    m = svtts.TtsMarks(text, q)
    # ケツロン 0-40 間 40-70 フメエ 70-100 間 100-130 イジョオ 130-160
    assert [p["index"] for p in m.pauses] == [2, 7]      # どちらも並びの中の「。」
    assert _frames(m, "「") == 40 and m.precision_of("「") == "pause"
    assert _frames(m, "不明") == 70 and m.precision_of("不明") == "start"
    assert _frames(m, "」") == 100 and _frames(m, "。", 1) == 100
    assert _frames(m, "以上") == 130 and m.precision_of("以上") == "start"
    _starts_are_after_pauses(m)


def test_marks_leading_bracket_is_not_a_pause():
    """前に読まれる文字の無い記号は間にならない（間は必ずモーラの後ろ）"""
    text = "「東京」、大阪"
    q = _kq((["ト", "オ", "キョ", "オ"], True), ("オオサカ", False))
    m = svtts.TtsMarks(text, q)
    assert [p["index"] for p in m.pauses] == [4]
    assert _frames(m, "「") == 0 and m.precision_of("「") == "approx"
    assert _frames(m, "東京") == 0 and m.precision_of("東京") == "start"
    assert _frames(m, "」") == 40 and m.precision_of("」") == "pause"
    assert _frames(m, "大阪") == 70 and m.precision_of("大阪") == "start"


def test_marks_question_and_space_runs():
    text = "えっ？　本当？　嘘だ。"
    q = _kq(("エッ", True), ("ホントオ", True), ("ウソダ", False))
    m = svtts.TtsMarks(text, q)
    # エッ 0-20 間 20-50 ホントオ 50-90 間 90-120 ウソダ 120-150
    assert [p["index"] for p in m.pauses] == [2, 6]
    assert _frames(m, "本当") == 50 and m.precision_of("本当") == "start"
    assert _frames(m, "嘘") == 120 and m.precision_of("嘘") == "start"
    _starts_are_after_pauses(m)


def test_marks_bracket_only_runs_are_pauses_when_counts_agree():
    """括弧だけでも、読まれる文字に挟まれていれば間になる（数が合えば一意）"""
    text = "彼は「東京」と言った"
    q = _kq(("カレワ", True), (["ト", "オ", "キョ", "オ"], True), ("ト", False),
            ("イッタ", False))
    m = svtts.TtsMarks(text, q)
    assert [p["index"] for p in m.pauses] == [2, 5]
    assert _frames(m, "東京") == 60 and m.precision_of("東京") == "start"


def test_marks_punctuation_preferred_over_bracket_and_newline():
    """並びが間より多いとき、句読点・空白の並びを括弧・改行だけの並びより優先する"""
    q = _kq(("トオキョオオオサカ", True), (["キョ", "オ", "ト"], False))
    for text in ("東京\n大阪、京都", "東京「大阪、京都"):
        m = svtts.TtsMarks(text, q)
        assert [p["index"] for p in m.pauses] == [5], text
        assert _frames(m, "京都") == 120 and m.precision_of("京都") == "start"
        assert m.precision_at(2) == "approx"          # 間に対応しない記号
        assert m.time_at(2) == pytest.approx(m.time_of("大"))
        assert _frames(m, "大") < 90                   # 間をまたがない


def test_marks_undecidable_pause_is_not_reported_as_certain():
    """同格の並びが間より多く、仮名でも決まらないときは確実と言わない"""
    text = "東京、大阪、京都"
    q = _kq((["ト", "オ", "キョ", "オ"], True), (["オ", "オ", "サ", "カ", "キョ", "オ", "ト"], False))
    m = svtts.TtsMarks(text, q)
    assert [p["index"] for p in m.pauses] == [None]
    assert m.precision_of("東京") == "start" and m.time_of("東京") == 0
    for i in range(1, len(text)):
        assert m.precision_at(i) == "approx", i
    times = [m.time_at(i) for i in range(len(text))]
    assert times == sorted(times)


def test_marks_kana_decides_between_equal_runs():
    """同格の並びが間より多くても、仮名の対応点が順序を縛れば決まる"""
    text = "はい、そう、です"
    m = svtts.TtsMarks(text, _kq(("ハイソオ", True), ("デス", False)))
    assert [p["index"] for p in m.pauses] == [5]
    assert m.precision_at(2) == "approx"               # 1個目の「、」は間ではない
    assert _frames(m, "です") == 70
    m = svtts.TtsMarks(text, _kq(("ハイ", True), ("ソオデス", False)))
    assert [p["index"] for p in m.pauses] == [2]
    assert _frames(m, "そう") == 50 and m.precision_of("そう") == "kana"


def test_marks_pause_index_uses_original_positions_with_readings():
    text = "金、東京！　以上"
    synth, origin = svtts._apply_readings("tts", text, {"金": "オカネ"})
    q = _kq(("オカネ", True), (["ト", "オ", "キョ", "オ"], True), (["イ", "ジョ", "オ"], False))
    m = svtts.TtsMarks(text, q, origin=origin, synth_text=synth)
    assert [text[p["index"]] for p in m.pauses] == ["、", "！"]
    assert _frames(m, "以上") == 130 and m.precision_of("以上") == "start"


# =============================================================================
# 読み替え
# =============================================================================

def test_readings_longest_match_single_pass():
    r = {"金": "カネ", "金曜": "キンヨウ", "カネ": "×"}
    synth, origin = svtts._apply_readings("tts", "金曜の金", r)
    assert synth == "キンヨウのカネ"          # 長い語が先。置き換えた結果は再び置き換えない
    assert origin == [0, 0, 0, 0, 2, 3, 3]
    # dict の並び順に依らない
    assert svtts._apply_readings("tts", "金曜の金", dict(reversed(list(r.items()))))[0] == synth
    assert svtts._apply_readings("tts", "そのまま", None) == ("そのまま", [0, 1, 2, 3])


@pytest.mark.parametrize("bad", [["金", "カネ"], {"": "カネ"}, {"金": 1}, {1: "カネ"}])
def test_readings_rejects_bad_types(bad):
    with pytest.raises(ValueError, match="readings"):
        svtts._apply_readings("tts", "金は", bad)


def test_readings_empty_result_is_an_error():
    with pytest.raises(ValueError, match="空"):
        svtts._apply_readings("tts", "金", {"金": ""})


# =============================================================================
# 鍵
# =============================================================================

def test_default_key_is_unchanged_from_before():
    """既定値では、調整機能を足す前と同じ鍵（＝既存の wav がそのまま命中する）"""
    old_sig = ("backend=voicevox||こんにちは||speaker=3||speed=1||pitch=0"
               "||engine=127.0.0.1:50021|0.25.2")
    expected = hashlib.sha256(old_sig.encode("utf-8")).hexdigest()[:16] + ".wav"
    for adjust in (None, {}):
        path = svtts._cache_path("voicevox", "こんにちは", 3, 1.0, 0.0, "c",
                                 engine="127.0.0.1:50021|0.25.2", adjust=adjust)
        assert os.path.basename(path) == expected
    assert svtts._normalize_adjust("tts", "voicevox") == {}
    assert svtts._normalize_adjust("tts", "edge") == {}


def test_each_adjustment_changes_the_key():
    base = svtts._cache_path("voicevox", "文", 3, 1.0, 0.0, "c", engine="e")
    seen = {base}
    for kw in ({"pre_silence": 0.0}, {"post_silence": 0.3}, {"pause_length": 0.5},
               {"pause_scale": 0.5}, {"intonation": 1.2}, {"volume_scale": 0.8},
               {"kana": "ブ'ン"}, {"pre_silence": 0.3}, {"pause_scale": 0.3}):
        adjust = svtts._normalize_adjust("tts", "voicevox", **kw)
        path = svtts._cache_path("voicevox", "文", 3, 1.0, 0.0, "c", engine="e",
                                 adjust=adjust)
        assert path not in seen, kw
        seen.add(path)
    # 整数と小数で鍵が割れない
    a = svtts._normalize_adjust("tts", "voicevox", pause_scale=2)
    b = svtts._normalize_adjust("tts", "voicevox", pause_scale=2.0)
    assert a == b


def test_kana_drops_text_from_key():
    """kana を指定すると元の文は音声に効かないので鍵にも入れない（同一出力なら同一鍵）"""
    adjust = svtts._normalize_adjust("tts", "voicevox", kana=" ア'タイ ")
    assert adjust == {"kana": "ア'タイ"}
    a = svtts._cache_path("voicevox", "値", 3, 1.0, 0.0, "c", engine="e", adjust=adjust)
    b = svtts._cache_path("voicevox", "あたい", 3, 1.0, 0.0, "c", engine="e", adjust=adjust)
    assert a == b


@pytest.mark.parametrize("kw, match", [
    ({"pre_silence": -0.1}, "0 以上"),
    ({"pause_scale": float("nan")}, "0 以上"),
    ({"pause_length": float("inf")}, "0 以上"),
    ({"intonation": "1.0"}, "数値"),
    ({"volume_scale": True}, "数値"),
    ({"kana": ""}, "kana"),
    ({"kana": 3}, "kana"),
])
def test_adjust_validation(kw, match):
    with pytest.raises(ValueError, match=match):
        svtts._normalize_adjust("tts", "voicevox", **kw)


@pytest.mark.parametrize("backend", ["edge", "sapi"])
def test_adjust_on_other_backend_is_an_error(backend, tmp_path):
    with pytest.raises(ValueError, match="VOICEVOX 専用") as exc:
        svtts.tts("こんにちは", backend=backend, cache_dir=str(tmp_path),
                  pause_scale=0.5, kana="コ'ンニチワ")
    assert "pause_scale" in str(exc.value) and "kana" in str(exc.value)
    with pytest.raises(ValueError, match="VOICEVOX だけ"):
        svtts.tts_marks("こんにちは", backend=backend, cache_dir=str(tmp_path))


# =============================================================================
# エンジンのモック
# =============================================================================

def _wav_bytes(n=240):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * n)
    return buf.getvalue()


class _Resp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    """VOICEVOX エンジンの最小モック（audio_query は _base_query を返す）"""

    def __init__(self):
        self.version = "0.25.2"
        self.up = True
        self.calls = []
        self.queries = []        # /audio_query と /accent_phrases のクエリ文字列
        self.synth_bodies = []   # /synthesis へ届いたクエリ
        self.old_engine = False  # True: pauseLength / pauseLengthScale の無い版

    def urlopen(self, req, timeout=None):
        url = getattr(req, "full_url", req)
        parsed = urllib.parse.urlparse(url)
        path = parsed.path
        self.calls.append(path)
        if not self.up:
            raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        if path == "/version":
            return _Resp(json.dumps(self.version).encode("utf-8"))
        if path == "/audio_query":
            self.queries.append(urllib.parse.parse_qs(parsed.query))
            q = _base_query()
            if self.old_engine:
                del q["pauseLength"], q["pauseLengthScale"]
            return _Resp(json.dumps(q).encode("utf-8"))
        if path == "/accent_phrases":
            qs = urllib.parse.parse_qs(parsed.query)
            self.queries.append(qs)
            if "'" not in qs["text"][0]:
                raise urllib.error.HTTPError(url, 400, "Bad Request", {},
                                             io.BytesIO(b'{"detail": "parse error"}'))
            return _Resp(json.dumps(
                [_phrase([_mora("ア", None, 10 / _F), _mora("タ", None, 10 / _F),
                          _mora("イ", None, 10 / _F)])]).encode("utf-8"))
        if path == "/synthesis":
            self.synth_bodies.append(json.loads(req.data))
            return _Resp(_wav_bytes())
        raise AssertionError(f"想定外の URL: {url}")

    def count(self, path):
        return self.calls.count(path)


def _new_process(monkeypatch):
    monkeypatch.setattr(svtts, "_VOICEVOX_ENGINE_SIG_MEMO", {})
    monkeypatch.setattr(svtts, "_VOICEVOX_OFFLINE_SIG_MEMO", {})
    monkeypatch.setattr(svtts, "_VOICEVOX_SIG_SAVED", set())
    monkeypatch.setattr(svtts, "_VOICEVOX_OFFLINE_WARNED", set())


@pytest.fixture
def engine(monkeypatch):
    eng = _FakeEngine()
    monkeypatch.setattr(urllib.request, "urlopen", eng.urlopen)
    _new_process(monkeypatch)
    return eng


def _kw(cache_dir, **kw):
    return dict(backend="voicevox", speaker=3, cache_dir=str(cache_dir),
                host=_HOST, port=_PORT, **kw)


# --- 合成に渡すクエリ --------------------------------------------------------

def test_default_query_only_sets_speed_and_pitch(engine, tmp_path):
    wav = svtts.tts(_TEXT, **_kw(tmp_path, speed=1.1, pitch=0.02))
    expected = _base_query()
    expected["speedScale"] = 1.1
    expected["pitchScale"] = 0.02
    assert engine.synth_bodies == [expected]
    assert wav == svtts._cache_path("voicevox", _TEXT, 3, 1.1, 0.02, str(tmp_path),
                                    engine=f"{_ENDPOINT}|0.25.2")


def test_adjustments_are_written_to_query(engine, tmp_path):
    svtts.tts(_TEXT, **_kw(tmp_path, pre_silence=0, post_silence=0.3, pause_length=0.5,
                           pause_scale=2, intonation=1.2, volume_scale=0.8))
    body = engine.synth_bodies[0]
    assert (body["prePhonemeLength"], body["postPhonemeLength"]) == (0.0, 0.3)
    assert (body["pauseLength"], body["pauseLengthScale"]) == (0.5, 2.0)
    assert (body["intonationScale"], body["volumeScale"]) == (1.2, 0.8)
    assert body["accent_phrases"] == _base_query()["accent_phrases"]


def test_partial_adjustment_leaves_other_fields(engine, tmp_path):
    svtts.tts(_TEXT, **_kw(tmp_path, pause_scale=0.5))
    body = engine.synth_bodies[0]
    expected = _base_query()
    expected["pauseLengthScale"] = 0.5
    assert body == expected


def test_kana_replaces_accent_phrases(engine, tmp_path):
    a = svtts.tts("値", **_kw(tmp_path, kana="ア'タイ"))
    assert engine.count("/accent_phrases") == 1
    assert engine.queries[-1] == {"text": ["ア'タイ"], "speaker": ["3"],
                                  "is_kana": ["true"]}
    body = engine.synth_bodies[0]
    assert [m["text"] for m in body["accent_phrases"][0]["moras"]] == ["ア", "タ", "イ"]
    assert body["kana"] == "ア'タイ"
    # 文が違っても同じカナなら同じ wav（合成し直さない）
    assert svtts.tts("あたい", **_kw(tmp_path, kana="ア'タイ")) == a
    assert engine.count("/synthesis") == 1
    m = svtts.tts_marks("値", **_kw(tmp_path, kana="ア'タイ"))
    assert m.duration == pytest.approx((9 + 30 + 9) / _F)


def test_bad_kana_error_explains_format(engine, tmp_path):
    with pytest.raises(RuntimeError, match="AquesTalk") as exc:
        svtts.tts("値", **_kw(tmp_path, kana="アタイ"))
    assert "400" in str(exc.value)
    assert engine.count("/synthesis") == 0


def test_readings_change_only_synthesis_text(engine, tmp_path):
    a = svtts.tts("金は戻った。", **_kw(tmp_path, readings={"金": "カネ"}))
    assert engine.queries[-1]["text"] == ["カネは戻った。"]
    # 読み替え後の文が同じなら同じ鍵（同一出力なら同一鍵）
    assert svtts.tts("カネは戻った。", **_kw(tmp_path)) == a
    assert engine.count("/synthesis") == 1
    # 当たらない読み替えは鍵を変えない
    assert svtts.tts("カネは戻った。", **_kw(tmp_path, readings={"値": "アタイ"})) == a


def test_old_engine_without_pause_fields_raises(engine, tmp_path):
    engine.old_engine = True
    svtts.tts(_TEXT, **_kw(tmp_path, intonation=1.2))        # 間以外は通る
    for kw in ({"pause_scale": 0.5}, {"pause_length": 0.3}):
        with pytest.raises(RuntimeError, match="対応していません"):
            svtts.tts(_TEXT, **_kw(tmp_path, **kw))
    assert engine.count("/synthesis") == 1


# --- marks.json の控え -------------------------------------------------------

def test_synthesis_saves_marks_next_to_wav(engine, tmp_path):
    wav = svtts.tts(_TEXT, **_kw(tmp_path, pause_scale=0.5))
    marks_path = svtts._marks_path(wav)
    assert marks_path == wav[:-len(".wav")] + ".marks.json"
    with open(marks_path, "rb") as f:
        raw = f.read()
    assert b"\r" not in raw
    data = json.loads(raw)
    assert data["format"] == 1
    assert data["query"] == engine.synth_bodies[0]
    assert sorted(os.listdir(tmp_path)) == sorted(
        [os.path.basename(wav), os.path.basename(marks_path), "engine_sig.json"])
    # 控えがあれば問い合わせない
    engine.calls.clear()
    m = svtts.tts_marks(_TEXT, **_kw(tmp_path, pause_scale=0.5))
    assert engine.calls == []
    assert m.pauses[0]["end"] - m.pauses[0]["start"] == pytest.approx(17 / _F)


def test_marks_without_wav_queries_but_does_not_synthesize(engine, tmp_path):
    m = svtts.tts_marks(_TEXT, **_kw(tmp_path))
    assert engine.count("/audio_query") == 1 and engine.count("/synthesis") == 0
    assert m.time_of("答えは") == pytest.approx(131 / _F)
    # 2回目は控えから。後で合成する wav と同じ鍵
    svtts.tts_marks(_TEXT, **_kw(tmp_path))
    assert engine.count("/audio_query") == 1
    wav = svtts.tts(_TEXT, **_kw(tmp_path))
    assert os.path.exists(svtts._marks_path(wav))


def test_marks_offline_uses_saved_query(engine, tmp_path, monkeypatch):
    svtts.tts(_TEXT, **_kw(tmp_path))
    _new_process(monkeypatch)
    engine.up = False
    engine.calls.clear()
    with pytest.warns(UserWarning, match="VOICEVOX に接続できません"):
        m = svtts.tts_marks(_TEXT, **_kw(tmp_path))
    assert m.time_of("答えは") == pytest.approx(131 / _F)
    assert engine.calls == ["/version"]


def test_marks_offline_miss_raises_connection_error(engine, tmp_path, monkeypatch):
    svtts.tts(_TEXT, **_kw(tmp_path))
    _new_process(monkeypatch)
    engine.up = False
    with pytest.raises(ConnectionError, match="audio_query"):
        svtts.tts_marks("別の文。", **_kw(tmp_path))
    # 一度もエンジンに届いていない cache_dir では従来どおり
    with pytest.raises(ConnectionError, match="VOICEVOX が起動していません"):
        svtts.tts_marks(_TEXT, **_kw(tmp_path / "other"))


def test_saved_marks_are_not_preferred_over_new_engine(engine, tmp_path, monkeypatch):
    """エンジンが変われば鍵が変わり、古い控えは使われない（控えを実測より優先しない）"""
    svtts.tts(_TEXT, **_kw(tmp_path))
    _new_process(monkeypatch)
    engine.version = "0.26.0"
    engine.calls.clear()
    svtts.tts_marks(_TEXT, **_kw(tmp_path))
    assert engine.count("/audio_query") == 1


@pytest.mark.parametrize("content", [
    b"", b"{broken", b"[1]", b'{"format": 99, "query": {}}',
    b'{"format": 1, "query": {"accent_phrases": []}}',
    b'{"format": 1, "query": {"accent_phrases": [{"moras": [{"vowel_length": "x"}]}]}}',
])
def test_corrupt_marks_file_is_rebuilt(engine, tmp_path, content):
    wav = svtts.tts(_TEXT, **_kw(tmp_path))
    with open(svtts._marks_path(wav), "wb") as f:
        f.write(content)
    engine.calls.clear()
    m = svtts.tts_marks(_TEXT, **_kw(tmp_path))
    assert engine.count("/audio_query") == 1
    assert m.duration == pytest.approx(251 / _F)
    assert svtts._read_marks_query(svtts._marks_path(wav)) is not None


def test_query_without_phrases_is_an_error(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(svtts, "_voicevox_query",
                        lambda *a, **k: {"accent_phrases": [], "speedScale": 1.0})
    with pytest.raises(RuntimeError, match="accent_phrases"):
        svtts.tts_marks("……", **_kw(tmp_path))


def test_voice_passes_adjustments_through(engine, tmp_path):
    """voice() / narrate() は **tts_kwargs でそのまま渡せる"""
    from scriptvedit import Project, voice
    Project()
    v = voice("金は戻った。", **_kw(tmp_path, readings={"金": "カネ"}, pause_scale=0.5))
    assert engine.queries[-1]["text"] == ["カネは戻った。"]
    assert engine.synth_bodies[0]["pauseLengthScale"] == 0.5
    assert os.path.exists(svtts._marks_path(v.source))


# =============================================================================
# 実エンジン（動いているときだけ）
# =============================================================================

def _real_engine():
    try:
        with urllib.request.urlopen(f"http://{_HOST}:{_PORT}/version", timeout=1.0):
            return True
    except Exception:
        return False


def _real_speaker():
    with urllib.request.urlopen(f"http://{_HOST}:{_PORT}/speakers", timeout=5.0) as r:
        return json.loads(r.read())[0]["styles"][0]["id"]


def _silences(wav):
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", wav, "-af",
         "silencedetect=noise=-35dB:d=0.08", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", out)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", out)]
    return list(zip(starts, ends))


@pytest.mark.parametrize("kw", [
    {},
    {"speed": 1.3},
    {"pre_silence": 0.3, "post_silence": 0.5, "pause_scale": 2.0},
    {"speed": 1.2, "pause_length": 0.6, "intonation": 1.3, "volume_scale": 0.8},
    {"readings": {"金": "カネ"}},
])
def test_real_engine_marks_match_wav(tmp_path, kw):
    if not _real_engine():
        pytest.skip("VOICEVOX エンジンが起動していません")
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg がありません")
    text = "金は戻らなかった。答えは、まだ無い。本当に？"
    args = dict(backend="voicevox", speaker=_real_speaker(), cache_dir=str(tmp_path),
                host=_HOST, port=_PORT, **kw)
    wav = svtts.tts(text, **args)
    m = svtts.tts_marks(text, **args)
    # 計算した長さは実 wav と 1 フレーム（約 11ms）以内で合う
    assert m.duration == pytest.approx(svtts.tts_duration(wav), abs=1.5 / _F)
    assert [p["index"] for p in m.pauses] == [text.index("。"), text.index("、"),
                                              text.index("。", 9)]
    assert m.time_of("答えは") == pytest.approx(m.pauses[0]["end"])
    assert m.precision_of("答えは") == "start"
    # 計算した間は、検出された無音区間の中にある（無音は語尾の減衰と次の子音の
    # 立ち上がりのぶん前後に広い）
    sil = _silences(wav)
    for p in m.pauses:
        hit = [s for s in sil if s[0] <= p["start"] + 0.06 and s[1] >= p["end"] - 0.06]
        assert hit, (p, sil)
        # 音の出始め（無音の終わり）は計算した間の終わりの直後
        assert -0.03 <= hit[0][1] - p["end"] <= 0.12, (p, hit)


def test_real_engine_kana_and_default_key(tmp_path):
    if not _real_engine():
        pytest.skip("VOICEVOX エンジンが起動していません")
    args = dict(backend="voicevox", speaker=_real_speaker(), cache_dir=str(tmp_path),
                host=_HOST, port=_PORT)
    kana = "アタイワ'/カラノ'/ハイレツダッタ'"
    wav = svtts.tts("値は空の配列だった", kana=kana, **args)
    m = svtts.tts_marks("値は空の配列だった", kana=kana, **args)
    assert m.kana == kana
    assert [p["kana"] for p in m.phrases] == ["アタイワ", "カラノ", "ハイレツダッタ"]
    assert m.duration == pytest.approx(svtts.tts_duration(wav), abs=1.5 / _F)
    # pause_scale を変えると間だけが変わる
    plain = svtts.tts_marks("はい、そうです。", **args)
    half = svtts.tts_marks("はい、そうです。", pause_scale=0.5, **args)
    assert half.duration < plain.duration
    assert half.time_of("そう") < plain.time_of("そう")
    assert half.time_of("、") == pytest.approx(plain.time_of("、"))


@pytest.mark.parametrize("text, symbols", [
    ("第1位、東京！　第2位、大阪！", "、！、"),
    ("結論。「不明」。以上。", "。。"),
    ("「東京」、大阪", "、"),
    ("えっ？　本当？　嘘だ。", "？？"),
    ("彼は「東京」と言った", "「」"),
])
def test_real_engine_symbol_runs_and_brackets(tmp_path, text, symbols):
    """記号の並び・括弧つきの文で、間が正しい記号に対応する（漢字だけの区間を含む）"""
    if not _real_engine():
        pytest.skip("VOICEVOX エンジンが起動していません")
    m = svtts.tts_marks(text, backend="voicevox", speaker=_real_speaker(),
                        cache_dir=str(tmp_path), host=_HOST, port=_PORT)
    assert all(p["index"] is not None for p in m.pauses), m.pauses
    assert "".join(text[p["index"]] for p in m.pauses) == symbols
    idx = [p["index"] for p in m.pauses]
    assert idx == sorted(idx)
    # "start" の文字は文頭かどれかの間の終わり。間の記号の直後に読まれる文字は間の終わり
    ok = [m.speech_start] + [p["end"] for p in m.pauses]
    for i in range(len(text)):
        if m.precision_at(i) == "start":
            assert any(m.time_at(i) == pytest.approx(t) for t in ok), (i, text[i])
    for p in m.pauses:
        # 間に対応した記号の後ろの文字は、その間の開始より前にならない（間をまたがない）
        for i in range(len(text)):
            if i < p["index"]:
                assert m.time_at(i) <= p["start"] + 1e-9, (i, p)
            else:
                assert m.time_at(i) >= p["start"] - 1e-9, (i, p)
    times = [m.time_at(i) for i in range(len(text))]
    assert times == sorted(times)
