# -*- coding: utf-8 -*-
"""VOICEVOX が止まっていてもキャッシュ済みの音声を使えることのテスト

以前は tts(backend="voicevox") がキャッシュ判定の**前に** /version を問い合わせる
ため、エンジンが止まっているとキャッシュ済みの行まで全部 ConnectionError で落ちた。
今は次の契約になっている:

  * エンジンに届いたら署名（endpoint と version）を <cache_dir>/engine_sig.json
    （endpoint ごと）へ原子的に保存する
  * 届かないときはその保存値で鍵を作り、キャッシュに当たれば使う（1回だけ警告）
  * キャッシュに無く合成が要るときだけ、分かりやすい ConnectionError

HTTP は urllib.request.urlopen をモックする（実エンジン・実ネットワークは使わない）。
"""
import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import warnings
import wave

import pytest

from scriptvedit import tts as svtts

_HOST, _PORT = "127.0.0.1", 50021
_ENDPOINT = "127.0.0.1:50021"


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
    """VOICEVOX エンジンの最小モック（up=False で接続拒否、timeout=True でタイムアウト）"""

    def __init__(self, version="0.25.2"):
        self.version = version
        self.up = True
        self.timeout = False
        self.calls = []

    def urlopen(self, req, timeout=None):
        url = getattr(req, "full_url", req)
        path = urllib.parse.urlparse(url).path
        self.calls.append(path)
        if self.timeout:
            raise urllib.error.URLError(TimeoutError("timed out"))
        if not self.up:
            raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        if path == "/version":
            return _Resp(json.dumps(self.version).encode("utf-8"))
        if path == "/audio_query":
            return _Resp(b'{"speedScale": 1.0, "pitchScale": 0.0}')
        if path == "/synthesis":
            return _Resp(_wav_bytes())
        raise AssertionError(f"想定外の URL: {url}")

    def count(self, path):
        return self.calls.count(path)


def _new_process(monkeypatch):
    """プロセス内メモを空にする（＝別プロセスで実行し直した状態）"""
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


def _tts(text, cache_dir, **kw):
    return svtts.tts(text, backend="voicevox", speaker=3, cache_dir=cache_dir,
                     host=_HOST, port=_PORT, **kw)


def _sig_file(cache_dir):
    return os.path.join(cache_dir, "engine_sig.json")


# --- 保存 -------------------------------------------------------------------

def test_online_saves_engine_signature(engine, tmp_path):
    cache_dir = str(tmp_path / "tts")
    wav = _tts("こんにちは", cache_dir)
    assert os.path.getsize(wav) > 0
    with open(_sig_file(cache_dir), "rb") as f:
        raw = f.read()
    assert b"\r" not in raw  # _atomic_write_text（LF）経由
    assert json.loads(raw) == {_ENDPOINT: {"version": "0.25.2"}}
    # 原子的書き込みの一時ファイルが残っていない
    assert sorted(os.listdir(cache_dir)) == sorted(
        [os.path.basename(wav), "engine_sig.json"])


def test_save_keeps_other_endpoints(engine, tmp_path):
    cache_dir = str(tmp_path / "tts")
    _tts("こんにちは", cache_dir)
    svtts.tts("こんにちは", backend="voicevox", cache_dir=cache_dir,
              host=_HOST, port=50022)
    data = json.loads(open(_sig_file(cache_dir), encoding="utf-8").read())
    assert set(data) == {_ENDPOINT, "127.0.0.1:50022"}


def test_online_version_overwrites_saved_value(engine, tmp_path, monkeypatch):
    """届けば必ず実測が勝ち、保存値も更新される（古い保存値を優先しない）"""
    cache_dir = str(tmp_path / "tts")
    a = _tts("こんにちは", cache_dir)
    _new_process(monkeypatch)
    engine.version = "0.26.0"
    b = _tts("こんにちは", cache_dir)
    assert a != b, "エンジン更新後も旧バージョンの鍵で引いた"
    data = json.loads(open(_sig_file(cache_dir), encoding="utf-8").read())
    assert data == {_ENDPOINT: {"version": "0.26.0"}}


# --- エンジン停止中 ---------------------------------------------------------

def test_offline_uses_cache_with_saved_signature(engine, tmp_path, monkeypatch):
    cache_dir = str(tmp_path / "tts")
    lines = ["一行目です", "二行目です", "三行目です"]
    online_paths = [_tts(t, cache_dir) for t in lines]

    _new_process(monkeypatch)       # 別プロセスで
    engine.up = False               # エンジンが止まっている
    engine.calls.clear()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        offline_paths = [_tts(t, cache_dir) for t in lines]
    assert offline_paths == online_paths
    # 警告はプロセス内で1回だけ
    msgs = [str(w.message) for w in caught if "VOICEVOX に接続できません" in str(w.message)]
    assert len(msgs) == 1, msgs
    assert "0.25.2" in msgs[0]
    # 届かない問い合わせは1回だけ（Windows では1回約2秒かかるので行ごとに試さない）
    assert engine.calls == ["/version"]


def test_offline_cache_miss_raises_connection_error(engine, tmp_path, monkeypatch):
    cache_dir = str(tmp_path / "tts")
    _tts("キャッシュ済み", cache_dir)
    _new_process(monkeypatch)
    engine.up = False
    engine.calls.clear()
    with pytest.warns(UserWarning, match="VOICEVOX に接続できません"):
        _tts("キャッシュ済み", cache_dir)
    with pytest.raises(ConnectionError) as exc:
        _tts("まだ合成していない行", cache_dir)
    msg = str(exc.value)
    assert "VOICEVOX が起動していません" in msg
    assert "キャッシュに無い" in msg
    assert "まだ合成していない行" in msg
    # 合成の直前に1回だけ問い合わせ直す（途中でエンジンを起動した場合に拾うため）。
    # audio_query / synthesis までは行かない
    assert engine.calls == ["/version", "/version"]


def test_offline_without_saved_signature_raises(engine, tmp_path):
    """保存値が無ければ（一度もエンジンに届いていない）従来どおり ConnectionError"""
    engine.up = False
    with pytest.raises(ConnectionError, match="VOICEVOX が起動していません"):
        _tts("こんにちは", str(tmp_path / "tts"))


def test_offline_timeout_also_falls_back(engine, tmp_path, monkeypatch):
    """接続拒否だけでなくタイムアウトも「届かない」として保存値へ落ちる"""
    cache_dir = str(tmp_path / "tts")
    wav = _tts("こんにちは", cache_dir)
    _new_process(monkeypatch)
    engine.timeout = True
    with pytest.warns(UserWarning, match="VOICEVOX に接続できません"):
        assert _tts("こんにちは", cache_dir) == wav


def test_offline_zero_byte_cache_is_not_a_hit(engine, tmp_path, monkeypatch):
    """保存値で代用中も 0 バイトの残骸は命中扱いにしない（合成が要る → ConnectionError）"""
    cache_dir = str(tmp_path / "tts")
    wav = _tts("こんにちは", cache_dir)
    open(wav, "wb").close()
    _new_process(monkeypatch)
    engine.up = False
    with pytest.raises(ConnectionError, match="キャッシュに無い"):
        _tts("こんにちは", cache_dir)


def test_engine_started_while_offline_recovers(engine, tmp_path, monkeypatch):
    """停止中に始めたが、未キャッシュの行に来た時点でエンジンが起動していれば合成する"""
    cache_dir = str(tmp_path / "tts")
    cached = _tts("キャッシュ済み", cache_dir)
    _new_process(monkeypatch)
    engine.up = False
    with pytest.warns(UserWarning):
        assert _tts("キャッシュ済み", cache_dir) == cached
    engine.up = True          # ここでエンジンを起動した
    engine.version = "0.26.0"
    fresh = _tts("新しい行", cache_dir)
    assert os.path.getsize(fresh) > 0
    assert engine.count("/synthesis") == 2  # 最初の1回 + 新しい行
    # 起動後の実測値で鍵を作り、保存値も更新する
    expected = svtts._cache_path("voicevox", "新しい行", 3, 1.0, 0.0, cache_dir,
                                 engine=f"{_ENDPOINT}|0.26.0")
    assert fresh == expected
    data = json.loads(open(_sig_file(cache_dir), encoding="utf-8").read())
    assert data == {_ENDPOINT: {"version": "0.26.0"}}


@pytest.mark.parametrize("content", [b"{broken", b"[1, 2]", b'{"127.0.0.1:50021": 3}',
                                     b'{"127.0.0.1:50021": {"version": ""}}'])
def test_corrupt_signature_file_is_treated_as_missing(engine, tmp_path, content):
    """壊れた engine_sig.json は「保存値なし」扱い（停止中は ConnectionError、届けば上書き）"""
    cache_dir = str(tmp_path / "tts")
    os.makedirs(cache_dir)
    with open(_sig_file(cache_dir), "wb") as f:
        f.write(content)
    engine.up = False
    with pytest.raises(ConnectionError):
        _tts("こんにちは", cache_dir)
    engine.up = True
    _tts("こんにちは", cache_dir)
    data = json.loads(open(_sig_file(cache_dir), encoding="utf-8").read())
    assert data[_ENDPOINT] == {"version": "0.25.2"}


def test_signature_file_is_per_cache_dir(engine, tmp_path, monkeypatch):
    """保存値は cache_dir ごと。別の cache_dir の保存値では代用しない"""
    a_dir = str(tmp_path / "a")
    _tts("こんにちは", a_dir)
    _new_process(monkeypatch)
    engine.up = False
    with pytest.raises(ConnectionError):
        _tts("こんにちは", str(tmp_path / "b"))
