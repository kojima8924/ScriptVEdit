# -*- coding: utf-8 -*-
"""scriptvedit.viz（Project 検査ビュー）の契約テスト

viz は「本体の規則を**再実装しない**」ことで成り立っている。
以前は viz 側でチェックポイントの保存点とキャッシュパスを組み直しており、
本体だけが持つ規則（text Object はベイク対象外 / morph は専用パス /
前処理ベイクの中間物）が落ちて、実在しない checkpoint を予告していた。

ここで固定するのは主に次の6点:
  1. `_save_point_info` の予想キャッシュが `Project._plan_object_checkpoints`
     の steps と1対1であること（二重実装に戻ったら落ちる）
  2. text Object に phantom checkpoint を出さないこと
  3. レイヤーキャッシュの予測が spec の cache_quality を落とさないこと
  4. `_classify_item` が isinstance 判定であること（クラス改名で壊れない）
  5. `p.inspect()` がテキスト／HTML のどちらでも例外を出さないこと
  6. morph_to の予想キャッシュが morph 専用ディレクトリを指すこと

外部プロセス（ffmpeg / ffprobe）には依存しない。素材は同梱画像と text() のみで、
検査対象はすべて純粋な計画・パス計算。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import scriptvedit.viz as viz  # noqa: E402
from scriptvedit import Project  # noqa: E402
from scriptvedit.cache import _layer_cache_paths  # noqa: E402
from scriptvedit.objects import Object  # noqa: E402
from scriptvedit.state import _CACHE_DIR  # noqa: E402
from scriptvedit.timeline import Pause, _AnchorMarker, _ScenePad  # noqa: E402


# --- レイヤー雛形 -----------------------------------------------------------

# ベイク対象（画像＋Transform＋Effect2つ）とベイク対象外（text）を1枚に混ぜる。
# `+vignette()` は policy="force" なので保存点が2つ（force と最右auto）できる。
_MIXED_LAYER = """from scriptvedit import *
o = Object(asset('images/shape_badge.png'))
o.time(2) <= resize(sx=0.5, sy=0.5)
o <= +vignette() & fade(0.5)
t = text('あ', size=48)
t.time(1) <= fade(0.5)
pause.time(0.5)
with scene('intro', 3):
    inner = Object(asset('images/shape_dots.png'))
    inner.time(1)
anchor('mark')
"""

_MORPH_LAYER = """from scriptvedit import *
src = Object(asset('images/shape_badge.png'))
tgt = Object(asset('images/shape_dots.png'))
src.time(2) <= morph_to(tgt)
"""


def _write_layer(tmp_path, name, body):
    """一時ディレクトリにレイヤーファイルを書き出してパスを返す"""
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return str(path)


def _project(layer, *, cache="off", cache_quality=None, duration=8):
    p = Project()
    p.configure(width=320, height=240, fps=10)
    p.duration = duration
    p.layer(layer, cache=cache, cache_quality=cache_quality)
    return p


def _exec_only(p):
    """render せずレイヤーを1回 exec する。

    dry_run まで進めると Object.source がチェックポイント予定パスへ差し替わり、
    以後 `_plan_object_checkpoints` は None を返す（もうベイクする op が無い）。
    予測とプランの対応を見たいテストは差し替え前の状態を使う。
    """
    for spec in p._layer_specs:
        p._exec_layer(spec["filename"], spec["priority"])
    return p


def _rows(project):
    """_collect が返す全レイヤーのオブジェクト行を平坦化して返す"""
    return [row for layer in viz._collect(project)["layers"]
            for row in layer["rows"]]


def _norm(path):
    return str(path).replace("\\", "/")


# --- 1. 二重実装に戻らないガード ---------------------------------------------

def test_predictions_match_plan_steps(tmp_path):
    """予想キャッシュは _plan_object_checkpoints の steps と1対1で対応する"""
    p = _exec_only(_project(_write_layer(tmp_path, "mixed.py", _MIXED_LAYER)))
    checked = 0
    for obj in p.objects:
        if viz._classify_item(obj) != "object":
            continue
        plan = p._plan_object_checkpoints(obj)
        save_ops, predictions = viz._save_point_info(p, obj)
        if plan is None:
            assert (save_ops, predictions) == ([], [])
            continue
        checked += 1
        steps = plan["steps"]
        assert len(predictions) == len(steps), (
            f"{obj.source}: 予測 {len(predictions)} 件 / 計画 {len(steps)} 件")
        assert [pred[1] for pred in predictions] == [s["path"] for s in steps]
        # 保存点一覧も計画の resume_args から取る（独自計算していない）
        _src, bakeable, _dur, _fps, save_points = plan["resume_args"]
        assert [i for i, _name in save_ops] == sorted(save_points)
        assert [name for _i, name in save_ops] == [
            bakeable[i][1].name for i in sorted(save_points)]
    assert checked, "ベイク対象の Object が1つも無く、対応を検証できていない"


def test_forced_savepoint_produces_two_checkpoints(tmp_path):
    """雛形が意図どおり複数の保存点を持つ（1件しか無いと1対1検証が緩む）"""
    p = _exec_only(_project(_write_layer(tmp_path, "mixed.py", _MIXED_LAYER)))
    badge = next(o for o in p.objects
                 if str(getattr(o, "source", "")).endswith("shape_badge.png"))
    save_ops, predictions = viz._save_point_info(p, badge)
    assert [name for _i, name in save_ops] == ["vignette", "fade"]
    assert len(predictions) == 2
    for _name, path, exists in predictions:
        assert "/artifacts/checkpoint/" in _norm(path)
        assert exists is os.path.exists(path)


def test_save_point_info_delegates_to_project_plan(tmp_path, monkeypatch):
    """計画関数が「対象外」と言えば viz も必ず空になる

    viz が自前で保存点を組み直していると、本体が None を返しても予測が出続ける。
    """
    p = _exec_only(_project(_write_layer(tmp_path, "mixed.py", _MIXED_LAYER)))
    badge = next(o for o in p.objects
                 if str(getattr(o, "source", "")).endswith("shape_badge.png"))
    assert viz._save_point_info(p, badge) != ([], [])

    monkeypatch.setattr(Project, "_plan_object_checkpoints",
                        lambda self, obj: None)
    assert viz._save_point_info(p, badge) == ([], [])


def test_save_point_info_warns_instead_of_silently_emptying(tmp_path,
                                                            monkeypatch):
    """呼び先が消えた（AttributeError）ときは黙って空にせず警告する"""
    p = _exec_only(_project(_write_layer(tmp_path, "mixed.py", _MIXED_LAYER)))
    badge = next(o for o in p.objects
                 if str(getattr(o, "source", "")).endswith("shape_badge.png"))

    def _boom(self, obj):
        raise AttributeError("_plan_object_checkpoints は改名された")

    monkeypatch.setattr(Project, "_plan_object_checkpoints", _boom)
    with pytest.warns(UserWarning, match="チェックポイント計画"):
        assert viz._save_point_info(p, badge) == ([], [])


def test_viz_does_not_import_checkpoint_rule_helpers():
    """保存点・キャッシュパスの規則を viz へ持ち込み直さない

    ここに名前が復活するのは「本体の規則を viz で再現し始めた」印。
    """
    reimplementation_markers = [
        "_build_unified_ops", "_split_ops", "_compute_save_points",
        "_checkpoint_cache_path", "_morph_cache_path", "_particle_cache_path",
        "_layer_cache_paths",
    ]
    present = [n for n in reimplementation_markers if hasattr(viz, n)]
    assert not present, (
        "viz が本体の規則を再実装しています（本体のヘルパー／Project メソッドを"
        f"そのまま呼んでください）: {present}")


# --- 2. text の phantom checkpoint ------------------------------------------

def test_text_object_has_no_phantom_checkpoint(tmp_path):
    """text Object は実体ファイルを持たずベイク対象外 → 予測を出さない"""
    p = _exec_only(_project(_write_layer(tmp_path, "mixed.py", _MIXED_LAYER)))
    texts = [o for o in p.objects if getattr(o, "media_type", None) == "text"]
    assert texts, "text Object が登録されていない（雛形の前提崩れ）"
    for obj in texts:
        assert p._plan_object_checkpoints(obj) is None
        assert viz._save_point_info(p, obj) == ([], [])

    text_rows = [r for r in _rows(p) if r["media_type"] == "text"]
    assert text_rows
    for row in text_rows:
        assert row["cp_predictions"] == []
        assert row["save_ops"] == []
        # テキストレポートの「予想キャッシュ」にも text 由来の行が出ない
        assert f"checkpoint [未] {row['label']}" not in viz.report_text(p)


# --- 3. レイヤーキャッシュのパス予測 -----------------------------------------

def test_layer_cache_info_keeps_spec_quality(tmp_path):
    """cache_quality="lossless" の予測は _layer_cache_paths_for と一致し .mkv"""
    layer = _write_layer(tmp_path, "mixed.py", _MIXED_LAYER)
    p = _project(layer, cache="make", cache_quality="lossless")
    spec = p._layer_specs[0]
    info = viz._layer_cache_info(p, spec)

    expected_webm, expected_json = p._layer_cache_paths_for(spec)
    assert info["webm"] == expected_webm
    assert info["json"] == expected_json
    assert info["webm"].endswith(".mkv")   # FFV1 は webm コンテナに入らない
    # 既定品質のパスを流用していない（品質を落とすと別物を予告してしまう）
    assert info["webm"] != _layer_cache_paths(layer, p, "balanced")[0]


def test_layer_cache_info_is_none_when_cache_off(tmp_path):
    """cache="off" のレイヤーには予定パスを出さない"""
    p = _project(_write_layer(tmp_path, "mixed.py", _MIXED_LAYER))
    assert viz._layer_cache_info(p, p._layer_specs[0]) is None


# --- 4. _classify_item（isinstance 判定） ------------------------------------

def test_classify_item_kinds(tmp_path):
    """4種別が正しく分類される"""
    p = _exec_only(_project(_write_layer(tmp_path, "mixed.py", _MIXED_LAYER)))
    badge = next(o for o in p.objects
                 if str(getattr(o, "source", "")).endswith("shape_badge.png"))
    assert viz._classify_item(badge) == "object"
    assert viz._classify_item(Pause()) == "pause"
    assert viz._classify_item(_ScenePad("intro", 3.0)) == "scene_pad"
    assert viz._classify_item(_AnchorMarker("mark")) == "anchor"


def test_classify_item_survives_class_rename():
    """クラス名ではなく型で判定する（文字列比較に戻したら落ちる）"""
    class _RenamedPause(Pause):
        pass

    class _RenamedAnchor(_AnchorMarker):
        pass

    class _RenamedScenePad(_ScenePad):
        pass

    assert viz._classify_item(_RenamedPause()) == "pause"
    assert viz._classify_item(_RenamedAnchor("x")) == "anchor"
    assert viz._classify_item(_RenamedScenePad("intro", 1.0)) == "scene_pad"


def test_scene_pad_is_distinguished_from_pause(tmp_path):
    """_ScenePad は pause と同じ扱い（非描画）だがラベルでシーン名が分かる"""
    p = _exec_only(_project(_write_layer(tmp_path, "mixed.py", _MIXED_LAYER)))
    kinds = [r["kind"] for r in _rows(p)]
    assert kinds.count("pause") == 1
    assert kinds.count("scene_pad") == 1

    pad_row = next(r for r in _rows(p) if r["kind"] == "scene_pad")
    assert pad_row["label"].startswith("scene:intro")
    assert pad_row["media_type"] == "pause"
    assert pad_row["source"] is None
    assert viz._item_row(_AnchorMarker("mark"), p) is None


def test_scene_pad_renders_as_pause_bar(tmp_path):
    """HTML のバー／ツールチップでも scene_pad は pause として描く"""
    p = _exec_only(_project(_write_layer(tmp_path, "mixed.py", _MIXED_LAYER)))
    pad = _ScenePad("intro", 2.0)
    pad.duration = 2.0
    row = viz._item_row(pad, p)
    assert "class='bar pause" in viz._bar_html(row, 8.0)
    assert "scene:intro" in viz._bar_tooltip(row)


# --- 5. inspect() の入口 ------------------------------------------------------

def test_inspect_text_and_html_after_dry_run(tmp_path):
    """dry_run 後の Project でテキスト／HTML の両方が例外なく出る"""
    p = _project(_write_layer(tmp_path, "mixed.py", _MIXED_LAYER),
                 cache="make", cache_quality="lossless")
    p.render(str(tmp_path / "out.mp4"), dry_run=True)

    report = p.inspect()
    assert isinstance(report, str)
    assert "scriptvedit タイムライン" in report
    assert "[オブジェクト]" in report

    out = p.inspect(out_html=str(tmp_path / "timeline.html"), title="検査")
    assert os.path.isabs(out) and os.path.exists(out)
    html = open(out, encoding="utf-8").read()
    assert html.startswith("<!DOCTYPE html>")
    assert "<title>検査</title>" in html


def test_inspect_before_layer_execution(tmp_path):
    """レイヤー未実行（layer() 登録だけ）でも壊れない"""
    p = _project(_write_layer(tmp_path, "mixed.py", _MIXED_LAYER),
                 cache="make", cache_quality="lossless")
    report = p.inspect()
    assert "レイヤー未実行" in report
    out = p.inspect(out_html=str(tmp_path / "empty.html"))
    assert "レイヤー未実行" in open(out, encoding="utf-8").read()


# --- 6. morph の予想キャッシュ ------------------------------------------------

def test_morph_prediction_uses_morph_artifact_dir(tmp_path):
    """morph_to の保存点は checkpoint/ ではなく morph/ を予告する"""
    p = _project(_write_layer(tmp_path, "morph.py", _MORPH_LAYER))
    with pytest.warns(UserWarning, match="モーフィング"):
        _exec_only(p)

    src = next(o for o in p.objects
               if str(getattr(o, "source", "")).endswith("shape_badge.png"))
    save_ops, predictions = viz._save_point_info(p, src)
    assert [name for _i, name in save_ops] == ["morph_to"]
    assert len(predictions) == 1
    label, path, _exists = predictions[0]
    assert "/artifacts/morph/" in _norm(path)
    assert "/artifacts/checkpoint/" not in _norm(path)
    assert label.startswith("morph_to")
    assert viz._STEP_KIND_SUFFIX["morph"] in label


# --- キャッシュ由来判定 -------------------------------------------------------

def test_from_cache_only_for_real_cache_dir(tmp_path):
    """"__cache__" という語を含むだけのユーザーパスをキャッシュ由来にしない"""
    p = _exec_only(_project(_write_layer(tmp_path, "mixed.py", _MIXED_LAYER)))

    inside = Object(os.path.join(_CACHE_DIR, "artifacts", "checkpoint",
                                 "ab", "cd.png"))
    outside = Object(str(tmp_path / "__cache__" / "looks_like_cache.png"))
    assert viz._item_row(inside, p)["from_cache"] is True
    assert viz._item_row(outside, p)["from_cache"] is False


def test_from_cache_falls_back_to_false_for_unusable_source(tmp_path):
    """パス判定不能な合成ソースは「キャッシュ由来」と主張しない"""
    p = _exec_only(_project(_write_layer(tmp_path, "mixed.py", _MIXED_LAYER)))
    text_row = next(r for r in _rows(p) if r["media_type"] == "text")
    assert text_row["from_cache"] is False
