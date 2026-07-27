# -*- coding: utf-8 -*-
"""レイヤーキャッシュ品質選択（cache_quality）のテスト

対象:
  1. 品質ごとのffmpegコマンド生成（コーデック・pix_fmt・crf・拡張子）
  2. キャッシュ鍵の分離（品質を変えると別の中間ファイルになる）
  3. 後方互換（cache_quality 未指定でも従来どおり動く）
  4. 拡張子とデコーダ選択の整合（透過が黒背景化する罠の回帰ガード）
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from scriptvedit import Project, describe  # noqa: E402
from scriptvedit.cache import (  # noqa: E402
    _DEFAULT_LAYER_CACHE_QUALITY, _LAYER_CACHE_QUALITY,
    _layer_cache_encode_args, _layer_cache_paths, _resolve_layer_cache_quality)
from scriptvedit.ffmpeg import _decoder_input_args  # noqa: E402

_QUALITIES = ("draft", "balanced", "lossless")


def _layer_file(tmp_path):
    """テキスト1個だけの最小レイヤーを作る"""
    layer = tmp_path / "q_layer.py"
    layer.write_text(
        "from scriptvedit import *\n"
        "t = text('quality', x=0.5, y=0.5, size=40, color='#FF2D95')\n"
        "t.show(1)\n",
        encoding="utf-8")
    return str(layer)


def _project(layer, quality, cache="make"):
    p = Project()
    p.configure(width=320, height=240, fps=10)
    p.duration = 1
    p.layer(layer, cache=cache, cache_quality=quality)
    return p


# --- 1. 品質ごとのコマンド生成 ---------------------------------------------

def test_encode_args_per_quality():
    """3段階それぞれが意図したコーデック/pix_fmtを指定する"""
    draft = _layer_cache_encode_args("draft")
    balanced = _layer_cache_encode_args("balanced")
    lossless = _layer_cache_encode_args("lossless")

    # draft/balanced は VP9 alpha（yuva420p 以外は libvpx-vp9 が受け付けない）
    for args in (draft, balanced):
        assert args[args.index("-c:v") + 1] == "libvpx-vp9"
        assert args[args.index("-pix_fmt") + 1] == "yuva420p"
        assert args[args.index("-auto-alt-ref") + 1] == "0"
    # 量子化は draft のほうが粗い
    assert int(draft[draft.index("-crf") + 1]) > int(
        balanced[balanced.index("-crf") + 1])

    # lossless は完全可逆。RGB系pix_fmtでYUV変換自体を挟まない
    assert lossless[lossless.index("-c:v") + 1] == "ffv1"
    assert lossless[lossless.index("-level") + 1] == "3"
    assert lossless[lossless.index("-pix_fmt") + 1] in ("bgra", "gbrap")


def test_build_layer_cache_cmd_uses_quality(tmp_path):
    """_build_layer_cache_cmd が spec の品質どおりのエンコード引数を出す"""
    layer = _layer_file(tmp_path)
    for quality in _QUALITIES:
        p = _project(layer, quality)
        spec = p._layer_specs[0]
        p._exec_layer(spec["filename"], spec["priority"])
        path, _meta = p._layer_cache_paths_for(spec)
        cmd = p._build_layer_cache_cmd(0, path)
        expected = _layer_cache_encode_args(quality)
        assert cmd[-1] == path
        # エンコード引数が連続してそのまま現れる
        joined, want = " ".join(cmd), " ".join(expected)
        assert want in joined, f"{quality}: {want} not in {joined}"


def test_encode_args_are_copies():
    """呼び出し側が返り値を破壊しても定義表が汚れない"""
    args = _layer_cache_encode_args("draft")
    args.append("-broken")
    assert "-broken" not in _layer_cache_encode_args("draft")


# --- 2. 拡張子とキャッシュ鍵 -------------------------------------------------

def test_extension_per_quality():
    """VP9系は.webm、FFV1は.mkv（webmコンテナはFFV1を格納できない）"""
    p = Project()
    assert _layer_cache_paths(__file__, p, "draft")[0].endswith(".webm")
    assert _layer_cache_paths(__file__, p, "balanced")[0].endswith(".webm")
    assert _layer_cache_paths(__file__, p, "lossless")[0].endswith(".mkv")


def test_cache_key_separates_qualities():
    """品質を変えると別の中間ファイルになる（古いキャッシュを再利用しない）"""
    p = Project()
    paths = [_layer_cache_paths(__file__, p, q)[0] for q in _QUALITIES]
    assert len(set(paths)) == len(_QUALITIES), paths
    # anchors.json も品質ごとに分かれる（メタと成果物の取り違え防止）
    metas = [_layer_cache_paths(__file__, p, q)[1] for q in _QUALITIES]
    assert len(set(metas)) == len(_QUALITIES), metas


def test_cache_key_includes_encode_args(monkeypatch):
    """エンコード設定を変えると鍵も変わる（crf調整で旧中間が生き残らない）"""
    p = Project()
    before = _layer_cache_paths(__file__, p, "balanced")[0]
    ext, args = _LAYER_CACHE_QUALITY["balanced"]
    patched = dict(_LAYER_CACHE_QUALITY)
    patched["balanced"] = (ext, [a if a != "15" else "14" for a in args])
    monkeypatch.setattr("scriptvedit.cache._LAYER_CACHE_QUALITY", patched)
    after = _layer_cache_paths(__file__, p, "balanced")[0]
    assert before != after


# --- 3. 後方互換 -------------------------------------------------------------

def test_layer_accepts_no_cache_quality(tmp_path):
    """cache_quality を渡さない既存コードがそのまま動く"""
    layer = _layer_file(tmp_path)
    p = Project()
    p.layer(layer)                       # 位置引数のみ（従来の呼び方）
    p.layer(layer, 1, "make")            # priority/cache も従来どおり
    for spec in p._layer_specs:
        assert spec["cache_quality"] == _DEFAULT_LAYER_CACHE_QUALITY


def test_default_quality_is_resolved():
    """None は既定値に解決され、既定値は定義表に存在する"""
    assert _resolve_layer_cache_quality(None) == _DEFAULT_LAYER_CACHE_QUALITY
    assert _DEFAULT_LAYER_CACHE_QUALITY in _LAYER_CACHE_QUALITY


def test_missing_cache_quality_key_falls_back(tmp_path):
    """cache_quality キーを持たない spec でも既定で動く（内部辞書の後方互換）"""
    layer = _layer_file(tmp_path)
    p = _project(layer, None)
    spec = p._layer_specs[0]
    del spec["cache_quality"]
    p._exec_layer(spec["filename"], spec["priority"])
    path, _ = p._layer_cache_paths_for(spec)
    assert path == _layer_cache_paths(layer, p, _DEFAULT_LAYER_CACHE_QUALITY)[0]
    p._build_layer_cache_cmd(0, path)  # 例外を出さない


def test_invalid_cache_quality_rejected(tmp_path):
    """未知の品質は即座に弾く（レンダ直前まで気付かないのを防ぐ）"""
    layer = _layer_file(tmp_path)
    with pytest.raises(ValueError, match="cache_quality"):
        Project().layer(layer, cache="make", cache_quality="ultra")
    with pytest.raises(ValueError, match="cache_quality"):
        _resolve_layer_cache_quality("lossles")


def test_manifest_enum_matches_implementation():
    """manifest の layer_cache_quality enum == 実装の許可値"""
    assert sorted(describe()["enums"]["layer_cache_quality"]) == sorted(
        _LAYER_CACHE_QUALITY)


# --- 4. デコーダ選択（透過が黒背景化する罠の回帰ガード）----------------------

def test_decoder_args_match_cache_extension():
    """キャッシュ生成物は拡張子ごとに正しい入力引数を得る

    .webm  … libvpx-vp9 を強制しないとネイティブVP9デコーダ(alpha非対応)が
             選ばれ、透過が黒背景化して下層レイヤーを覆う（issue #13 P1-3）
    .mkv   … FFV1。libvpx-vp9 を指定すると復号できないので強制してはいけない
    """
    cache_root = os.path.join("__cache__", "artifacts", "layer", "x")
    webm = os.path.join(cache_root, "abc.webm")
    mkv = os.path.join(cache_root, "abc.mkv")
    assert _decoder_input_args(webm, "video", 30) == [
        "-c:v", "libvpx-vp9", "-i", webm]
    assert _decoder_input_args(mkv, "video", 30) == ["-i", mkv]
    # 品質表が返す拡張子と、デコーダ側の分岐が食い違っていないこと
    for quality in _QUALITIES:
        path = _layer_cache_paths(__file__, Project(), quality)[0]
        args = _decoder_input_args(path, "video", 30)
        if path.endswith(".webm"):
            assert args[:2] == ["-c:v", "libvpx-vp9"], quality
        else:
            assert args == ["-i", path], quality


def test_external_mkv_not_forced_to_libvpx(tmp_path):
    """__cache__ 外の mkv にデコーダ強制を波及させない"""
    ext = str(tmp_path / "outside.mkv")
    assert _decoder_input_args(ext, "video", 30) == ["-i", ext]
