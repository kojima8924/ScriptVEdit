# -*- coding: utf-8 -*-
"""scriptvedit.tts — 日本語ナレーション音声生成モジュール（バックエンド差し替え可能）

複数の TTS バックエンドを同じ API で使えるようにし、テキストから wav を合成して
scriptvedit の素材として使えるファイルパスを返す。

バックエンド:
    "voicevox" : ローカルの VOICEVOX エンジン (http://127.0.0.1:50021) の REST API。
                 キャラクターボイス・完全オフライン。エンジンの別途起動が必要。
    "edge"     : Microsoft Edge の読み上げ音声（`pip install edge-tts`）。
                 導入が容易・APIキー不要・高品質だが**オンライン必須**。
    "sapi"     : Windows 標準の音声合成（System.Speech / PowerShell 経由）。
                 追加導入不要・オフラインだが品質は低め。Windows 専用。

使い方（Python から）:
    from scriptvedit.tts import tts, tts_duration, speakers

    wav = tts("こんにちは、ずんだもんなのだ", speaker=3)            # 自動選択
    wav = tts("こんにちは", backend="edge", speaker="ja-JP-NanamiNeural")
    dur = tts_duration(wav)   # 字幕同期用の実長（秒）

使い方（CLI から）:
    python -m scriptvedit.tts "こんにちは" --backend edge -o out.wav
    python -m scriptvedit.tts --list-speakers --backend edge

バックエンドの自動選択（backend=None のとき）:
    1. 環境変数 SCRIPTVEDIT_TTS_BACKEND があればそれを使う
    2. VOICEVOX エンジンが起動していれば "voicevox"
    3. 起動していなければ "edge"（edge-tts が import できる場合）
    4. どれも使えなければ導入方法を示す RuntimeError

speaker（話者）の指定:
    バックエンドごとに意味が違うため「同じ引数をバックエンドが解釈する」方式にした。
      voicevox: 数値スタイルID（既定 1）
      edge    : 音声名の文字列（既定 "ja-JP-NanamiNeural"）。"nanami"/"keita" の
                短縮名も可。**数値も受け付け**（フォールバック運用のため）、日本語音声の一覧
                （_EDGE_JA_VOICES）の index として解釈し warnings.warn で通知する
                （VOICEVOX 前提のスクリプトが edge へフォールバックしても動くようにするため）
      sapi    : インストール済み音声名の部分一致文字列（既定はシステム既定音声）
    speaker=None を渡せば各バックエンドの既定話者になる。

出力は常に wav（edge は mp3 を返すため ffmpeg で wav へ変換する）。
生成結果は backend+text+speaker+speed+pitch の sha256 を鍵に __cache__/tts/ へ
キャッシュされ、2回目以降は合成せずに即座にパスを返す（アトミック書き込み）。
voicevox はさらに「正規化した接続先 endpoint + エンジンの /version」を鍵に含める
（同じ cache_dir で接続先やエンジンを切り替えたとき、別エンジンの旧音声を
返さないようにするため。/version の取得は合成前の接続確認を兼ね、
プロセス内でメモ化されるためナレーション行ごとには問い合わせない）。
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import warnings
import wave

from scriptvedit.ffmpeg import _atomic_write_bytes, _unique_tmp_path

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 50021
_DEFAULT_CACHE_DIR = "__cache__/tts"

_CONNECT_TIMEOUT = 5   # 接続確認 / speakers 用
_SYNTH_TIMEOUT = 60    # audio_query / synthesis 用（長文の合成に時間がかかるため長め）

_BACKENDS = ("voicevox", "edge", "sapi")
_ENV_BACKEND = "SCRIPTVEDIT_TTS_BACKEND"

# edge バックエンドの既定音声と、数値 speaker を解釈するための一覧
_EDGE_DEFAULT_VOICE = "ja-JP-NanamiNeural"
_EDGE_JA_VOICES = ["ja-JP-NanamiNeural", "ja-JP-KeitaNeural"]
_EDGE_ALIASES = {
    "nanami": "ja-JP-NanamiNeural",
    "keita": "ja-JP-KeitaNeural",
}

# 合成音声の統一フォーマット（VOICEVOX の出力に合わせる）
_WAV_RATE = 24000
_WAV_CHANNELS = 1


# =============================================================================
# バックエンド共通
# =============================================================================

def _backend_choices_text():
    """エラーメッセージ用のバックエンド一覧文字列"""
    return " / ".join(f'"{b}"' for b in _BACKENDS)


def _resolve_backend(backend, *, host=_DEFAULT_HOST, port=_DEFAULT_PORT):
    """使用するバックエンド名を決定する

    None の場合は 環境変数 → VOICEVOX 起動判定 → edge の順に自動選択する。
    """
    if backend is None:
        backend = os.environ.get(_ENV_BACKEND) or None
    if backend is None:
        # VOICEVOX が起動していればそれを使う（キャラボイス・オフラインを優先）
        if _voicevox_running(host, port):
            return "voicevox"
        if _edge_available():
            return "edge"
        raise RuntimeError(
            "TTS バックエンドを自動選択できませんでした。"
            f"VOICEVOX が起動しておらず（{_base_url(host, port)}）、edge-tts も未導入です。\n"
            "  - VOICEVOX を使う: https://voicevox.hiroshiba.jp/ からインストールして起動\n"
            "  - edge-tts を使う: pip install edge-tts（オンライン必須）\n"
            '  - backend="sapi" で Windows 標準音声も使えます')
    backend = str(backend).lower()
    if backend not in _BACKENDS:
        raise ValueError(
            f"tts: backend は {_backend_choices_text()} のいずれかです: {backend!r}")
    return backend


def _cache_path(backend, text, speaker, speed, pitch, cache_dir, engine=None):
    """キャッシュファイルのパスを決定する（backend+text+speaker+speed+pitch の sha256）

    backend を鍵に含めるのは、同じテキスト・話者でもバックエンドが違えば
    まったく別の音声になるため（キャッシュ衝突で意図しない声が使われるのを防ぐ）。

    engine はエンジン識別署名（voicevox のみ。_voicevox_engine_sig の戻り値）。
    同じ cache_dir で host/port やエンジン本体を切り替えても、別エンジンの
    旧音声がヒットしないよう鍵に混ぜる。None のバックエンド（edge/sapi）では
    鍵に含めない（既存キャッシュを無駄に無効化しないため）。
    """
    sig = (f"backend={backend}||{text}||speaker={speaker!r}"
           f"||speed={float(speed):g}||pitch={float(pitch):g}")
    if engine is not None:
        sig += f"||engine={engine}"
    key = hashlib.sha256(sig.encode("utf-8")).hexdigest()[:16]
    return os.path.join(cache_dir, f"{key}.wav")


def tts(text, *, backend=None, speaker=None, speed=1.0, pitch=0.0,
        cache_dir=_DEFAULT_CACHE_DIR, host=_DEFAULT_HOST, port=_DEFAULT_PORT):
    """テキストを音声合成し、wav ファイルのパスを返す

    Args:
        text:      読み上げるテキスト
        backend:   "voicevox" / "edge" / "sapi"（None なら自動選択。モジュール docstring 参照）
        speaker:   話者。バックエンドごとに解釈が違う（None で各既定）
                     voicevox: 数値スタイルID / edge: 音声名 / sapi: 音声名の部分一致
        speed:     話速（1.0 が標準）
        pitch:     音高（0.0 が標準）
        cache_dir: キャッシュディレクトリ
        host/port: VOICEVOX エンジンのアドレス（backend="voicevox" のときのみ有効）

    Returns:
        生成された wav ファイルのパス（キャッシュ済みなら合成せず即返す）

    Raises:
        ConnectionError: VOICEVOX 未起動 / edge のネットワーク不通
        ImportError:     edge-tts 未導入
        ValueError:      パラメータ不正
    """
    if not text:
        raise ValueError("tts: text が空です")
    backend = _resolve_backend(backend, host=host, port=port)

    # キャッシュ鍵に使う speaker は「解決後の値」にする。
    # （speaker=None と speaker=1 が voicevox では同じ音声なので同じ鍵にしたい）
    engine = None
    if backend == "voicevox":
        resolved = _voicevox_speaker(speaker)
        # 接続先とエンジンバージョンを鍵に含める（/version 取得は接続確認を兼ねる。
        # 未起動なら合成前にここで既存どおりの ConnectionError になる）
        engine = _voicevox_engine_sig(host, port)
    elif backend == "edge":
        resolved = _edge_voice(speaker)
    else:
        resolved = _sapi_voice(speaker)

    cache_path = _cache_path(backend, text, resolved, speed, pitch, cache_dir,
                             engine=engine)
    if os.path.exists(cache_path):
        return cache_path
    os.makedirs(cache_dir, exist_ok=True)

    if backend == "voicevox":
        _synth_voicevox(text, resolved, speed, pitch, cache_path, host, port)
    elif backend == "edge":
        _synth_edge(text, resolved, speed, pitch, cache_path)
    else:
        _synth_sapi(text, resolved, speed, pitch, cache_path)
    return cache_path


def speakers(backend=None, host=_DEFAULT_HOST, port=_DEFAULT_PORT, locale="ja"):
    """バックエンドの話者一覧を取得して整形して返す

    Args:
        backend: "voicevox" / "edge" / "sapi"（None なら自動選択）
        locale:  edge のみ有効。言語コードの前方一致で絞り込む（None で全件）

    Returns:
        [{"id": 話者指定に使う値, "name": 話者名, "style": スタイル/種別}, ...]
    """
    backend = _resolve_backend(backend, host=host, port=port)
    if backend == "voicevox":
        return _speakers_voicevox(host, port)
    if backend == "edge":
        return _speakers_edge(locale)
    return _speakers_sapi()


def tts_duration(wav_path):
    """wav ファイルの実長（秒）を返す（字幕・タイムライン同期用）

    どのバックエンドでも出力は wav に統一しているため、この関数はそのまま使える
    （edge の mp3 は合成時に ffmpeg で wav へ変換済み）。
    """
    with wave.open(wav_path, "rb") as w:
        rate = w.getframerate()
        if rate <= 0:
            raise ValueError(f"tts_duration: サンプルレートが不正です: {wav_path}")
        nframes = w.getnframes()
        if nframes <= 0:
            raise ValueError(f"tts_duration: フレーム数が 0 です(空の wav): {wav_path}")
        return nframes / float(rate)


# =============================================================================
# voicevox バックエンド
# =============================================================================

def _base_url(host, port):
    """VOICEVOX エンジンのベースURLを返す"""
    return f"http://{host}:{port}"


def _not_running_error(host, port):
    """VOICEVOX 未起動時に投げる ConnectionError を生成する（代替案を提示する）"""
    return ConnectionError(
        f"VOICEVOX が起動していません({_base_url(host, port)})。\n"
        "  - VOICEVOX を起動する: https://voicevox.hiroshiba.jp/\n"
        '  - もしくは backend="edge" を使う（pip install edge-tts。オンライン必須）\n'
        '  - Windows 標準音声なら backend="sapi"（追加導入不要・オフライン）')


def _voicevox_running(host, port, timeout=1.0):
    """VOICEVOX エンジンが起動しているかを短いタイムアウトで判定する"""
    try:
        req = urllib.request.Request(f"{_base_url(host, port)}/version", method="GET")
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False


# エンジン識別署名のプロセス内メモ（endpoint → 署名文字列）。
# ナレーション行ごとに /version を問い合わせないためのメモ化で、ディスクには
# 永続化しない（CLAUDE.md のキャッシュ設計: 環境依存情報のディスク永続化は罠になる）。
_VOICEVOX_ENGINE_SIG_MEMO = {}


def _voicevox_endpoint(host, port):
    """接続先を正規化した endpoint 文字列にする（大文字小文字・型の揺れを吸収）"""
    return f"{str(host).strip().lower()}:{int(port)}"


def _voicevox_engine_sig(host, port):
    """VOICEVOX エンジンの識別署名「endpoint|バージョン」を返す（プロセス内メモ化）

    キャッシュ鍵に混ぜることで、同じ cache_dir のまま接続先(host/port)や
    エンジン本体（バージョン違い）を切り替えても旧エンジンの音声がヒットしない。
    /version の取得は合成前の接続確認を兼ねる。未起動・接続不可の場合は
    _request が既存の ConnectionError（_not_running_error）を投げる。
    """
    endpoint = _voicevox_endpoint(host, port)
    sig = _VOICEVOX_ENGINE_SIG_MEMO.get(endpoint)
    if sig is None:
        raw = _request(f"{_base_url(host, port)}/version", host=host, port=port,
                       timeout=_CONNECT_TIMEOUT)
        # /version は JSON 文字列（例: "0.14.0"）を返すため引用符を剥がす
        version = raw.decode("utf-8", errors="replace").strip().strip('"')
        sig = f"{endpoint}|{version}"
        _VOICEVOX_ENGINE_SIG_MEMO[endpoint] = sig
    return sig


def _voicevox_speaker(speaker):
    """voicevox の speaker（数値スタイルID）を解決する"""
    if speaker is None:
        return 1
    try:
        return int(speaker)
    except (TypeError, ValueError):
        raise ValueError(
            "tts(backend='voicevox'): speaker は数値スタイルIDです"
            f"（--list-speakers で確認）: {speaker!r}") from None


def _request(url, *, host, port, method="GET", data=None, headers=None,
             timeout=_CONNECT_TIMEOUT):
    """VOICEVOX API へ HTTP リクエストを送り、レスポンスボディ(bytes)を返す

    接続不可（未起動・ポート違い等）は明確なメッセージの ConnectionError、
    タイムアウトは TimeoutError、API 側のエラー応答は RuntimeError にする。
    """
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.read()
    except urllib.error.HTTPError as e:
        # サーバーは起動しているが API がエラーを返した（パラメータ不正など）
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"VOICEVOX API エラー ({e.code} {e.reason}): {url}\n{body}") from e
    except TimeoutError as e:
        raise TimeoutError(
            f"VOICEVOX API がタイムアウトしました({timeout}秒): {url}") from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise TimeoutError(
                f"VOICEVOX API がタイムアウトしました({timeout}秒): {url}") from e
        raise _not_running_error(host, port) from e
    except OSError as e:
        # ConnectionRefusedError 等が直接漏れてくるケース
        raise _not_running_error(host, port) from e


def _synth_voicevox(text, speaker, speed, pitch, cache_path, host, port):
    """VOICEVOX で合成して cache_path へ wav を書き出す"""
    base = _base_url(host, port)

    # 1) audio_query: テキストから合成用クエリ(JSON)を生成
    #    長文ではクエリ生成にも時間がかかるため synthesis と同じ上限を使う
    query_qs = urllib.parse.urlencode({"text": text, "speaker": int(speaker)})
    raw = _request(f"{base}/audio_query?{query_qs}", host=host, port=port,
                   method="POST", timeout=_SYNTH_TIMEOUT)
    query = json.loads(raw)

    # 2) 話速・音高を調整
    query["speedScale"] = float(speed)
    query["pitchScale"] = float(pitch)

    # 3) synthesis: クエリを渡して wav を取得
    wav_bytes = _request(
        f"{base}/synthesis?speaker={int(speaker)}", host=host, port=port,
        method="POST", data=json.dumps(query).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "audio/wav"},
        timeout=_SYNTH_TIMEOUT)

    _atomic_write_bytes(cache_path, wav_bytes)


def _speakers_voicevox(host, port):
    """VOICEVOX の話者一覧（ID昇順）"""
    raw = _request(f"{_base_url(host, port)}/speakers", host=host, port=port,
                   timeout=_CONNECT_TIMEOUT)
    result = []
    for sp in json.loads(raw):
        for style in sp.get("styles", []):
            result.append({
                "id": style["id"],
                "name": sp["name"],
                "style": style["name"],
            })
    result.sort(key=lambda s: s["id"])
    return result


# =============================================================================
# edge バックエンド（Microsoft Edge の読み上げ音声。pip install edge-tts）
# =============================================================================

def _edge_import():
    """edge_tts を import する（未導入なら導入コマンド付きの ImportError）"""
    try:
        import edge_tts  # noqa: F401
    except ImportError as e:
        raise ImportError(
            'tts(backend="edge") には edge-tts が必要です。'
            "次のコマンドで導入してください:\n"
            "  pip install edge-tts\n"
            "（無料・APIキー不要。ただし合成にはインターネット接続が必要）") from e
    return edge_tts


def _edge_available():
    """edge-tts が import できるか（自動選択の判定用）"""
    try:
        _edge_import()
        return True
    except ImportError:
        return False


def _edge_voice(speaker):
    """edge の speaker（音声名）を解決する

    文字列: そのまま音声名（"nanami"/"keita" の短縮名も可）
    数値  : 日本語音声一覧の index として解釈（VOICEVOX 前提のスクリプトが
            edge へフォールバックしても動くようにするための互換措置。警告を出す）
    None  : 既定音声
    """
    if speaker is None:
        return _EDGE_DEFAULT_VOICE
    if isinstance(speaker, bool):
        raise ValueError(f'tts(backend="edge"): speaker が不正です: {speaker!r}')
    if isinstance(speaker, int):
        voice = _EDGE_JA_VOICES[speaker % len(_EDGE_JA_VOICES)]
        warnings.warn(
            f'tts(backend="edge"): speaker={speaker}（数値）は VOICEVOX 用の指定です。'
            f"edge では音声名で指定します。フォールバックとして {voice} を使います"
            f"（例: speaker=\"ja-JP-KeitaNeural\"）",
            stacklevel=3)
        return voice
    name = str(speaker).strip()
    if not name:
        raise ValueError('tts(backend="edge"): speaker が空文字です')
    return _EDGE_ALIASES.get(name.lower(), name)


def _edge_rate(speed):
    """speed(1.0=標準) → edge-tts の rate 文字列（例 "+20%"）"""
    speed = float(speed)
    if speed <= 0:
        raise ValueError(f'tts(backend="edge"): speed は正の数です: {speed}')
    return f"{round((speed - 1.0) * 100):+d}%"


def _edge_pitch(pitch):
    """pitch(0.0=標準) → edge-tts の pitch 文字列（例 "+10Hz"）

    VOICEVOX の pitchScale（おおむね -0.15〜0.15）を Hz へ写像するため 100 倍する
    （pitch=0.1 → +10Hz 相当）。
    """
    hz = round(float(pitch) * 100)
    hz = max(-100, min(100, hz))
    return f"{hz:+d}Hz"


def _run_async(coro):
    """同期関数から asyncio のコルーチンを実行する

    既にイベントループが走っている場合（Jupyter 等）は asyncio.run が使えないため、
    別スレッドで新しいループを回す。
    """
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures as _futures
    with _futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


def _edge_network_error(e):
    """edge-tts の例外をネットワーク不通の ConnectionError に包む"""
    return ConnectionError(
        'tts(backend="edge") の音声合成に失敗しました。'
        "edge-tts はオンライン必須です（Microsoft のサーバーへ接続します）。"
        "インターネット接続・プロキシ設定を確認してください。\n"
        f"  原因: {type(e).__name__}: {e}")


def _synth_edge(text, voice, speed, pitch, cache_path):
    """edge-tts で合成（mp3）→ ffmpeg で wav へ変換して cache_path に確定する"""
    edge_tts = _edge_import()
    rate = _edge_rate(speed)
    pitch_s = _edge_pitch(pitch)

    base, _ = os.path.splitext(cache_path)
    tmp_mp3 = _unique_tmp_path(f"{base}.mp3")

    async def _save():
        comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch_s)
        await comm.save(tmp_mp3)

    try:
        try:
            _run_async(_save())
        except Exception as e:  # noqa: BLE001 - edge_tts は多様な例外を投げる
            name = type(e).__name__
            if name == "NoAudioReceived":
                # 音声名が不正なケースが大半（サーバーが音声を返さない）
                raise ValueError(
                    f'tts(backend="edge"): 音声が返りませんでした。voice 名が正しいか'
                    f"確認してください（speakers(backend=\"edge\") で一覧）: {voice!r}") from e
            if isinstance(e, (ImportError, ValueError)):
                raise
            raise _edge_network_error(e) from e

        if not os.path.exists(tmp_mp3) or os.path.getsize(tmp_mp3) == 0:
            raise RuntimeError(
                f'tts(backend="edge"): 空の音声が返りました（text={text!r}）')

        # tts() の契約は wav なので ffmpeg で変換する（tts_duration が wave 前提）
        from .ffmpeg import _run_ffmpeg_to_cache
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", tmp_mp3,
               "-ac", str(_WAV_CHANNELS), "-ar", str(_WAV_RATE),
               "-c:a", "pcm_s16le", cache_path]
        _run_ffmpeg_to_cache(cmd, cache_path)
    finally:
        try:
            os.remove(tmp_mp3)
        except OSError:
            pass


def _speakers_edge(locale="ja"):
    """edge-tts の音声一覧（locale の前方一致で絞り込む。None で全件）"""
    edge_tts = _edge_import()
    try:
        voices = _run_async(edge_tts.list_voices())
    except Exception as e:  # noqa: BLE001
        raise _edge_network_error(e) from e
    result = []
    for v in voices:
        short = v.get("ShortName", "")
        if locale and not short.lower().startswith(str(locale).lower()):
            continue
        result.append({
            "id": short,
            "name": v.get("FriendlyName", short),
            "style": v.get("Gender", ""),
        })
    result.sort(key=lambda s: s["id"])
    return result


# =============================================================================
# sapi バックエンド（Windows 標準音声。System.Speech / PowerShell 経由）
# =============================================================================

def _sapi_voice(speaker):
    """sapi の speaker（音声名の部分一致文字列。None でシステム既定）"""
    if speaker is None:
        return None
    if isinstance(speaker, bool) or isinstance(speaker, int):
        raise ValueError(
            'tts(backend="sapi"): speaker は音声名（部分一致）の文字列です'
            f'（例 "Haruka"）。speakers(backend="sapi") で一覧できます: {speaker!r}')
    return str(speaker)


def _sapi_check_platform():
    if sys.platform != "win32":
        raise RuntimeError(
            'tts(backend="sapi") は Windows 専用です。'
            '他の環境では backend="edge"（pip install edge-tts）または '
            '"voicevox" を使ってください')


def _ps_quote(s):
    """PowerShell のシングルクォート文字列としてエスケープする"""
    return "'" + str(s).replace("'", "''") + "'"


def _run_powershell(script, timeout=120):
    """PowerShell スクリプトを実行する（失敗時は RuntimeError）"""
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        raise RuntimeError(
            'tts(backend="sapi"): powershell が見つかりません')
    proc = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f'tts(backend="sapi"): 音声合成に失敗しました\n{err}')
    return proc.stdout.decode("utf-8", errors="replace")


def _synth_sapi(text, voice, speed, pitch, cache_path):
    """Windows の System.Speech で合成して cache_path へ wav を書き出す"""
    _sapi_check_platform()
    if float(pitch) != 0.0:
        warnings.warn(
            'tts(backend="sapi"): pitch は SAPI では調整できないため無視します',
            stacklevel=3)
    # SAPI の Rate は -10〜10（0 が標準）。speed=1.0→0, 2.0→10 程度に写像する
    rate = max(-10, min(10, round((float(speed) - 1.0) * 10)))

    tmp_path = _unique_tmp_path(cache_path)
    select = (f"$s.SelectVoice((($s.GetInstalledVoices() | "
              f"ForEach-Object {{ $_.VoiceInfo.Name }} | "
              f"Where-Object {{ $_ -like {_ps_quote('*' + str(voice) + '*')} }})"
              f" | Select-Object -First 1));") if voice else ""
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"{select}"
        f"$s.Rate = {rate}; "
        f"$s.SetOutputToWaveFile({_ps_quote(tmp_path)}); "
        f"$s.Speak({_ps_quote(text)}); "
        "$s.Dispose();")
    try:
        _run_powershell(script)
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            raise RuntimeError('tts(backend="sapi"): 空の wav が生成されました')
        os.replace(tmp_path, cache_path)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _speakers_sapi():
    """Windows にインストール済みの音声一覧"""
    _sapi_check_platform()
    script = ("Add-Type -AssemblyName System.Speech; "
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$s.GetInstalledVoices() | ForEach-Object { "
              "$i = $_.VoiceInfo; "
              "Write-Output ($i.Name + '|' + $i.Culture.Name + '|' + $i.Gender) }")
    out = _run_powershell(script, timeout=30)
    result = []
    for line in out.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 3 and parts[0]:
            result.append({"id": parts[0], "name": parts[0],
                           "style": f"{parts[1]} {parts[2]}"})
    return result


# =============================================================================
# CLI
# =============================================================================

def _main(argv=None):
    """CLI エントリポイント"""
    parser = argparse.ArgumentParser(
        description="テキストから wav を生成する（VOICEVOX / edge-tts / Windows SAPI）")
    parser.add_argument("text", nargs="?", help="読み上げるテキスト")
    parser.add_argument("--backend", choices=list(_BACKENDS), default=None,
                        help="TTS バックエンド（既定: 自動選択。"
                             f"環境変数 {_ENV_BACKEND} でも指定可）")
    parser.add_argument("--speaker", default=None,
                        help="話者（voicevox: 数値ID / edge: 音声名 / sapi: 音声名）")
    parser.add_argument("--speed", type=float, default=1.0, help="話速（既定: 1.0）")
    parser.add_argument("--pitch", type=float, default=0.0, help="音高（既定: 0.0）")
    parser.add_argument("-o", "--output", help="出力先 wav パス（省略時はキャッシュパスを表示）")
    parser.add_argument("--cache-dir", default=_DEFAULT_CACHE_DIR,
                        help=f"キャッシュディレクトリ（既定: {_DEFAULT_CACHE_DIR}）")
    parser.add_argument("--host", default=_DEFAULT_HOST, help="VOICEVOX ホスト")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT, help="VOICEVOX ポート")
    parser.add_argument("--list-speakers", action="store_true", help="話者一覧を表示して終了")
    args = parser.parse_args(argv)

    # voicevox は数値IDなので、数字だけの --speaker は int にしておく
    speaker = args.speaker
    if speaker is not None and speaker.lstrip("+-").isdigit():
        speaker = int(speaker)

    try:
        if args.list_speakers:
            for sp in speakers(backend=args.backend, host=args.host, port=args.port):
                print(f"{str(sp['id']):>24}  {sp['name']} ({sp['style']})")
            return 0

        if not args.text:
            parser.error("text を指定してください（話者一覧は --list-speakers）")

        path = tts(args.text, backend=args.backend, speaker=speaker,
                   speed=args.speed, pitch=args.pitch, cache_dir=args.cache_dir,
                   host=args.host, port=args.port)
        if args.output:
            shutil.copyfile(path, args.output)
            path = args.output
        print(f"{path} ({tts_duration(path):.2f}秒)")
        return 0
    except (ConnectionError, TimeoutError, RuntimeError, ImportError, ValueError) as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_main())
