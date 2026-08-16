# -*- coding: utf-8 -*-
"""compute() のキャッシュ鍵が _src_signature / _src_bucket に揃っていること（監査 項目4）。

2段目の compute() は self.source が __cache__ 配下の生成物になる。
生の _file_fingerprint を使うと dry_run（未生成＝OSError→パス署名）と
実レンダ（1段目を実際に焼いた後＝内容指紋）で鍵が食い違い、
「dry_run が予告したパス」と「実レンダが実際に作るパス」がずれる
（出力は壊れないが無駄な再生成が1回起きる。CLAUDE.md §5 が潰した罠）。
"""
import os
import shutil

import pytest

from scriptvedit import Project, asset
from scriptvedit.cache import _src_bucket, _src_signature
from scriptvedit.state import _ARTIFACT_DIR

_COMPUTE_DIR = os.path.join(_ARTIFACT_DIR, "compute")

_TWO_STAGE_LAYER = (
    "from scriptvedit import *\n"
    "a = Object({src!r})\n"
    "a <= resize(sx=0.5, sy=0.5)\n"
    "a.compute(3)\n"          # 1段目: 素材 → __cache__/artifacts/compute/...
    "a <= fade(lambda u: u)\n"
    "a.compute(3)\n"          # 2段目: 入力が __cache__ 配下の生成物になる
    "a.time(3)\n"
)


def _layer(tmp_path, src):
    path = tmp_path / "compute2.py"
    path.write_text(_TWO_STAGE_LAYER.format(src=src), encoding="utf-8")
    return str(path)


def _build(tmp_path, src):
    p = Project()
    p.configure(width=64, height=36, fps=10)
    p.layer(_layer(tmp_path, src))
    return p


def _compute_keys(cache_dict):
    marker = _COMPUTE_DIR.replace("\\", "/")
    return sorted(k for k in cache_dict if k.replace("\\", "/").startswith(marker))


def test_cache_artifact_source_uses_path_signature():
    """__cache__ 配下の source は未生成でもパス署名で安定する"""
    pending = os.path.join(_COMPUTE_DIR, "aa", "bb.mkv")
    assert _src_signature(pending).startswith("src=")
    assert not _src_bucket(pending).startswith("ffp")
    # 素材（実在）は内容指紋
    assert _src_signature(asset("images/shape_dots.png")).startswith("ffp=")


def test_two_stage_compute_dry_run_paths_match_real_render(tmp_path):
    """dry_run が予告した compute パスを実レンダがそのまま生成する"""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が見つからないためスキップします")
    src = asset("images/shape_dots.png").replace("\\", "/")
    shutil.rmtree(_COMPUTE_DIR, ignore_errors=True)

    # 1) キャッシュ皆無の状態で dry_run → 2段分の生成予定パス
    p1 = _build(tmp_path, src)
    dry = p1.render(str(tmp_path / "o.mp4"), dry_run=True)
    planned = _compute_keys(dry["cache"])
    assert len(planned) == 2, f"2段の compute が予告されていない: {planned}"
    final_source = [o.source for o in p1.objects][0]

    # 2) 実レンダ: 予告どおりのパスが生成されること
    _build(tmp_path, src).render(str(tmp_path / "o.mp4"), timeout=180)
    for path in planned:
        assert os.path.exists(path), \
            f"dry_run が予告したパスを実レンダが生成していない: {path}"

    # 3) キャッシュがある状態でも鍵は動かない（＝再生成を誘発しない）
    p3 = _build(tmp_path, src)
    p3.render(str(tmp_path / "o.mp4"), dry_run=True)
    assert [o.source for o in p3.objects][0] == final_source
    assert _compute_keys(p3.render(str(tmp_path / "o.mp4"),
                                   dry_run=True)["cache"]) == [], \
        "キャッシュ済みなのに再生成コマンドが予告されている"
