# issue #17: scan_bgm の誤認修正のテスト
# （showcase の fresh clone 再現性のテストは examples/showcase の削除とともに廃止）
import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")


def _load_module(name, path):
    """パス指定でモジュールを読み込む（scripts/ はパッケージではないため）"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# scan_bgm: ID抽出・利用条件の出し分け・repo フォールバック廃止
# ============================================================

@pytest.fixture()
def scan_bgm():
    return _load_module("scan_bgm_issue17", os.path.join(SCRIPTS, "scan_bgm.py"))


def test_nc_id_extraction(scan_bgm):
    assert scan_bgm._nc_id("nc342423_song.mp3") == "nc342423"
    assert scan_bgm._nc_id("prefix_NC12345.wav") == "nc12345"   # 大文字も拾い小文字へ正規化
    assert scan_bgm._nc_id("free_bgm.mp3") is None
    assert scan_bgm._nc_id("nc12.mp3") is None                  # 4桁未満はIDとみなさない


def test_license_note_commons_track(scan_bgm):
    """コモンズIDがあり commons から情報が取れた曲は出典（作品ページ）を示す"""
    note = scan_bgm._license_note(
        {"nc_id": "nc342423", "title": "サンプル曲"})
    assert "nc342423" in note
    assert "https://commons.nicovideo.jp/works/nc342423" in note
    assert "未確認" not in note


def test_license_note_unknown_track(scan_bgm):
    """それ以外の曲は「未確認」と明記し、特定レーベルの条件を推測で付けない"""
    for track in (
        {"nc_id": None, "title": None},            # IDなし
        {"nc_id": "nc342423", "title": None},      # IDはあるが commons 情報が取れていない
    ):
        note = scan_bgm._license_note(track)
        assert "未確認" in note
        assert "Nash" not in note


def test_default_bgm_dir_no_repo_fallback(scan_bgm, monkeypatch):
    """SCRIPTVEDIT_ASSETS 不在時はリポジトリへフォールバックせずエラー停止する"""
    monkeypatch.delenv("SCRIPTVEDIT_ASSETS", raising=False)
    with pytest.raises(SystemExit) as ei:
        scan_bgm._default_bgm_dir()
    assert "SCRIPTVEDIT_ASSETS" in str(ei.value)


def test_default_bgm_dir_uses_shared_library(scan_bgm, monkeypatch, tmp_path):
    """共有ライブラリ配下に bgm/ があればそれを返す"""
    bgm = tmp_path / "bgm"
    bgm.mkdir()
    monkeypatch.setenv("SCRIPTVEDIT_ASSETS", str(tmp_path))
    assert os.path.normpath(scan_bgm._default_bgm_dir()) == os.path.normpath(str(bgm))


def test_scan_bgm_no_gitignore_claim(scan_bgm):
    """「.gitignore 済み」という誤った説明が残っていないこと"""
    with open(os.path.join(SCRIPTS, "scan_bgm.py"), encoding="utf-8") as f:
        src = f.read()
    assert ".gitignore 済み" not in src
    assert ".gitignore済み" not in src
