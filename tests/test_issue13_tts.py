# -*- coding: utf-8 -*-
"""issue #13 P2-17: VOICEVOX の TTS キャッシュ鍵に接続先とエンジンバージョンを含める

同じ cache_dir で host/port やエンジン本体（バージョン違い）を切り替えたとき、
別エンジンの旧音声が問い合わせ前にヒットしないことをユニットレベルで検証する。
VOICEVOX 未起動でも通るよう、ネットワークアクセスは monkeypatch で遮断する。
"""
import urllib.request

import pytest

from scriptvedit import tts as svtts


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """全テスト共通: 実ネットワークアクセスを遮断し、メモ化を空から始める"""
    def _blocked(*args, **kwargs):
        raise AssertionError("テスト中に実ネットワークへアクセスしました")
    monkeypatch.setattr(urllib.request, "urlopen", _blocked)
    monkeypatch.setattr(svtts, "_VOICEVOX_ENGINE_SIG_MEMO", {})
    monkeypatch.setattr(svtts, "_VOICEVOX_OFFLINE_SIG_MEMO", {})
    monkeypatch.setattr(svtts, "_VOICEVOX_SIG_SAVED", set())
    monkeypatch.setattr(svtts, "_VOICEVOX_OFFLINE_WARNED", set())


def _fake_version(monkeypatch, version, counter=None):
    """_request を差し替えて /version が固定バージョンを返すようにする"""
    def _fake_request(url, *, host, port, method="GET", data=None, headers=None,
                      timeout=None):
        assert url.endswith("/version")
        if counter is not None:
            counter.append(url)
        return f'"{version}"'.encode("utf-8")
    monkeypatch.setattr(svtts, "_request", _fake_request)


def _key(host, port, text="こんにちは", speaker=1, speed=1.0, pitch=0.0,
         cache_dir="c"):
    """voicevox のキャッシュパスを実装と同じ経路（engine 署名込み）で計算する"""
    engine = svtts._voicevox_engine_state(host, port)[0]
    return svtts._cache_path("voicevox", text, speaker, speed, pitch, cache_dir,
                             engine=engine)


def test_host_port_changes_key(monkeypatch):
    """host/port が違えばキャッシュ鍵が変わる（別エンジンの旧音声を返さない）"""
    _fake_version(monkeypatch, "0.14.0")
    a = _key("127.0.0.1", 50021)
    b = _key("127.0.0.1", 50022)
    c = _key("192.0.2.7", 50021)  # ドキュメント用予約帯(TEST-NET-1)の例示アドレス
    assert a != b, "port 違いで同じ鍵になった"
    assert a != c, "host 違いで同じ鍵になった"
    assert b != c


def test_engine_version_changes_key(monkeypatch):
    """エンジンバージョンが違えばキャッシュ鍵が変わる"""
    _fake_version(monkeypatch, "0.14.0")
    a = _key("127.0.0.1", 50021)
    # 同じ endpoint のままエンジンを更新した想定（メモを空にして再取得させる）
    monkeypatch.setattr(svtts, "_VOICEVOX_ENGINE_SIG_MEMO", {})
    _fake_version(monkeypatch, "0.15.0")
    b = _key("127.0.0.1", 50021)
    assert a != b, "エンジンバージョン違いで同じ鍵になった"


def test_same_conditions_key_is_stable(monkeypatch):
    """同一条件（同じ endpoint・同じバージョン）では鍵が安定する"""
    _fake_version(monkeypatch, "0.14.0")
    a = _key("127.0.0.1", 50021)
    b = _key("127.0.0.1", 50021)
    assert a == b, "同一条件で鍵が揺れた"


def test_version_request_is_memoized(monkeypatch):
    """/version の問い合わせはプロセス内でメモ化される（行ごとに叩かない）"""
    calls = []
    _fake_version(monkeypatch, "0.14.0", counter=calls)
    for _ in range(5):
        svtts._voicevox_engine_state("127.0.0.1", 50021)
    assert len(calls) == 1, f"/version が {len(calls)} 回呼ばれた（メモ化されていない）"
    # 別 endpoint は別途1回だけ問い合わせる
    svtts._voicevox_engine_state("127.0.0.1", 50022)
    svtts._voicevox_engine_state("127.0.0.1", 50022)
    assert len(calls) == 2


def test_endpoint_normalization(monkeypatch):
    """接続先の表記揺れ（大文字小文字・空白・数値/文字列 port）は同じ鍵になる"""
    _fake_version(monkeypatch, "0.14.0")
    a = _key("LocalHost", 50021)
    b = _key(" localhost ", "50021")
    assert a == b, "endpoint の正規化が効いていない"


def test_engine_sig_connection_error_message(monkeypatch):
    """接続不可（OSError）は既存の _not_running_error と同じ ConnectionError になる"""
    def _refused(*args, **kwargs):
        raise ConnectionRefusedError("refused")
    monkeypatch.setattr(urllib.request, "urlopen", _refused)
    with pytest.raises(ConnectionError) as exc:
        svtts._voicevox_engine_state("127.0.0.1", 50099)
    assert "VOICEVOX が起動していません" in str(exc.value)


def test_edge_and_sapi_key_includes_voice():
    """edge/sapi は voice（話者名）が鍵に入っていれば十分（speaker スロットで分離）"""
    a = svtts._cache_path("edge", "こんにちは", "ja-JP-NanamiNeural", 1.0, 0.0, "c")
    b = svtts._cache_path("edge", "こんにちは", "ja-JP-KeitaNeural", 1.0, 0.0, "c")
    assert a != b, "edge: voice 違いで同じ鍵になった"
    c = svtts._cache_path("sapi", "こんにちは", "Haruka", 1.0, 0.0, "c")
    d = svtts._cache_path("sapi", "こんにちは", "Ayumi", 1.0, 0.0, "c")
    assert c != d, "sapi: voice 違いで同じ鍵になった"


def test_edge_sapi_keys_unchanged_without_engine():
    """engine=None（edge/sapi）の鍵は従来と同一（既存キャッシュを無効化しない）"""
    import hashlib
    import os
    sig = ('backend=edge||こんにちは||speaker=' + repr("ja-JP-NanamiNeural") +
           '||speed=1||pitch=0')
    key = hashlib.sha256(sig.encode("utf-8")).hexdigest()[:16]
    expected = os.path.join("c", f"{key}.wav")
    got = svtts._cache_path("edge", "こんにちは", "ja-JP-NanamiNeural", 1.0, 0.0, "c")
    assert got == expected, "engine 追加で edge の既存鍵が変わってしまった"


def test_tts_voicevox_checks_engine_before_cache(monkeypatch, tmp_path):
    """tts(backend='voicevox') はキャッシュ照会の前にエンジン署名を取得する

    署名取得（=接続確認）に失敗し、cache_dir に保存済みの署名（engine_sig.json）も
    無ければ ConnectionError。保存値があるときの代用は tests/test_tts_offline.py。
    """
    def _refused(*args, **kwargs):
        raise ConnectionRefusedError("refused")
    monkeypatch.setattr(urllib.request, "urlopen", _refused)
    with pytest.raises(ConnectionError):
        svtts.tts("こんにちは", backend="voicevox", cache_dir=str(tmp_path))
