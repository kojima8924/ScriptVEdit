# -*- coding: utf-8 -*-
"""issue #13 P2-8: audio_sequence / video_sequence の原子的消費と加工済みObject拒否

- 検証途中で例外になっても Project.objects から先行 Object が消えないこと
- audio_sequence が効果付き Object を明示拒否すること（video_sequence と整合）
- 正常系（効果なし Object 2つの連結）が従来どおり動くこと（dry_run コマンド検証）
"""
import os
import pytest
from scriptvedit import Project, Object, asset, avolume, audio_sequence, video_sequence


def _require_asset(relpath):
    """gitignore 対象を含む素材が無い環境では正直に skip する。"""
    try:
        return asset(relpath)
    except FileNotFoundError:
        pytest.skip(f"素材 assets/{relpath} が無い環境")


def _mk_project():
    p = Project()
    p.configure(width=640, height=360, fps=30, background_color="black")
    return p


# --- 原子的消費: 途中失敗で Project が壊れないこと ---

def test_audio_sequence_atomic_on_missing_path():
    """audio_sequence: 2番目が不存在パス → 例外後も先行 Object が残る"""
    audio_src = _require_asset("audio/bgm_loop.mp3")
    p = _mk_project()
    o1 = Object(audio_src)
    assert o1 in p.objects
    with pytest.raises(FileNotFoundError):
        audio_sequence(o1, "C:/no/such/audio_issue13.mp3")
    assert o1 in p.objects, "例外後に先行 Object が Project から消えている（原子性違反）"


def test_video_sequence_atomic_on_missing_path():
    """video_sequence: 2番目が不存在パス → 例外後も先行 Object が残る"""
    video_src = _require_asset("video/clip_with_audio.mp4")
    p = _mk_project()
    o1 = Object(video_src)
    assert o1 in p.objects
    with pytest.raises(FileNotFoundError):
        video_sequence(o1, "C:/no/such/video_issue13.mp4")
    assert o1 in p.objects, "例外後に先行 Object が Project から消えている（原子性違反）"


def test_audio_sequence_atomic_on_short_crossfade():
    """audio_sequence: crossfade 過大（後段の尺検証で失敗）でも Object が残る"""
    audio_src = _require_asset("audio/bgm_loop.mp3")
    p = _mk_project()
    o1 = Object(audio_src)
    o2 = Object(audio_src)
    with pytest.raises(ValueError, match="crossfade"):
        audio_sequence(o1, o2, crossfade=10000)
    assert o1 in p.objects and o2 in p.objects, \
        "尺検証で失敗した際に Object が Project から消えている（原子性違反）"


# --- 加工済み Object の明示拒否 ---

def test_audio_sequence_rejects_effected_object():
    """audio_sequence: 音声効果付き Object → ValueError（黙って捨てない）"""
    audio_src = _require_asset("audio/bgm_loop.mp3")
    p = _mk_project()
    o1 = Object(audio_src)
    o1 <= avolume(0.5)
    o2 = Object(audio_src)
    with pytest.raises(ValueError, match="連結後"):
        audio_sequence(o1, o2)
    # 拒否時も Project は無傷であること
    assert o1 in p.objects and o2 in p.objects


# --- 正常系: 効果なし Object 2つの連結が従来どおり動く ---

def test_audio_sequence_normal_consumes_and_builds_cmd():
    """audio_sequence: 正常系は両 Object を消費し acrossfade コマンドを生成する"""
    src1 = _require_asset("audio/bgm_loop.mp3")
    src2 = _require_asset("audio/効果音.mp3")
    p = _mk_project()
    p._dry_run = True
    p._pending_compute_cmds = {}
    o1 = Object(src1)
    o2 = Object(src2)
    seq = audio_sequence(o1, o2, crossfade=0.5)
    # 入力 Object は消費され、生成 Object が登録される
    assert o1 not in p.objects and o2 not in p.objects
    assert seq in p.objects
    assert seq._origin_sources == [src1, src2]
    # dry_run コマンド検証（キャッシュ命中時は生成済み実体の存在を確認）
    cmd = p._pending_compute_cmds.get(seq.source)
    if cmd is None:
        assert os.path.exists(seq.source), "コマンド未登録かつキャッシュ実体も無い"
    else:
        joined = " ".join(cmd)
        assert "acrossfade=d=0.5" in joined
        assert src1 in cmd and src2 in cmd
