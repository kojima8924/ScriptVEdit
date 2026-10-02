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

エンジンに届いたときの署名は <cache_dir>/engine_sig.json（endpoint ごと）へ
原子的に保存する。エンジンが止まっていて届かないときは、その保存値で鍵を作り、
キャッシュに当たればそのまま使う（プロセス内で1回だけ警告）。キャッシュに無く
合成が要るときだけ、従来どおりの ConnectionError になる。

VOICEVOX だけの調整（audio_query を書き換える。既定 None は「触らない」）:
    pre_silence / post_silence : 文の前後の無音（秒。エンジン既定 0.1）
    pause_length / pause_scale : 句読点の間を固定秒に／倍率で
    intonation / volume_scale  : 抑揚／音量
    kana                       : AquesTalk 風カナで読みとアクセントを指定
    readings={"金": "カネ"}    : 合成に渡す文だけ語を読み替える（全バックエンド可）
語の時刻（字幕や図を「この語が読まれた瞬間」に合わせる）:
    m = tts_marks("金は戻らなかった。答えは、まだ無い。", backend="voicevox", speaker=13)
    m.time_of("答えは")   # → wav の先頭から数えた秒
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import warnings
import wave

from scriptvedit.ffmpeg import _atomic_write_bytes, _atomic_write_text, _unique_tmp_path

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


def _cache_path(backend, text, speaker, speed, pitch, cache_dir, engine=None,
                adjust=None):
    """キャッシュファイルのパスを決定する（backend+text+speaker+speed+pitch の sha256）

    backend を鍵に含めるのは、同じテキスト・話者でもバックエンドが違えば
    まったく別の音声になるため（キャッシュ衝突で意図しない声が使われるのを防ぐ）。

    engine はエンジン識別署名（voicevox のみ。_voicevox_engine_state の戻り値の署名）。
    同じ cache_dir で host/port やエンジン本体を切り替えても、別エンジンの
    旧音声がヒットしないよう鍵に混ぜる。None のバックエンド（edge/sapi）では
    鍵に含めない（既存キャッシュを無駄に無効化しないため）。

    adjust は VOICEVOX の調整（_normalize_adjust の戻り値）。**指定された項目だけ**を
    固定順で鍵に足すので、何も指定しなければ鍵は調整機能の導入前と同じ文字列になる。
    kana を指定したときは text を鍵から外す（アクセント句がカナで丸ごと置き換わり、
    元の文は出力に効かないため。同一出力なら同一鍵）。
    text には読み替え（readings）を適用した後の「合成に渡す文」を渡すこと。
    """
    adjust = adjust or {}
    if adjust.get("kana") is not None:
        text = ""
    sig = (f"backend={backend}||{text}||speaker={speaker!r}"
           f"||speed={float(speed):g}||pitch={float(pitch):g}")
    if engine is not None:
        sig += f"||engine={engine}"
    for name, _field, tag in _VV_ADJUST_FIELDS:
        if adjust.get(name) is not None:
            sig += f"||{tag}={adjust[name]!r}"
    if adjust.get("kana") is not None:
        sig += f"||kana={adjust['kana']}"
    key = hashlib.sha256(sig.encode("utf-8")).hexdigest()[:16]
    return os.path.join(cache_dir, f"{key}.wav")


def _is_cache_hit(cache_path):
    """キャッシュ命中か（0 バイトの残骸は命中扱いにしない。tts() のコメント参照）"""
    return os.path.exists(cache_path) and os.path.getsize(cache_path) > 0


# VOICEVOX の audio_query を調整する引数: (引数名, クエリのフィールド名, 鍵のタグ)。
# 鍵へ足す順番はこの並びで固定する。
_VV_ADJUST_FIELDS = (
    ("pre_silence", "prePhonemeLength", "pre"),
    ("post_silence", "postPhonemeLength", "post"),
    ("pause_length", "pauseLength", "pause"),
    ("pause_scale", "pauseLengthScale", "pause_scale"),
    ("intonation", "intonationScale", "intonation"),
    ("volume_scale", "volumeScale", "volume"),
)


def _normalize_adjust(func, backend, *, pre_silence=None, post_silence=None,
                      pause_length=None, pause_scale=None, intonation=None,
                      volume_scale=None, kana=None):
    """VOICEVOX 専用の調整引数を検査して「指定された項目だけ」の dict にする

    None は「エンジンの既定のまま触らない」。指定された値は float に揃えて
    鍵にもクエリにも入れる（エンジン既定と同じ値を明示しても別の鍵になる。
    既定値はエンジンが決めるもので、エンジンに届かないときは分からないため）。
    VOICEVOX 以外のバックエンドに1つでも渡されたら ValueError。
    """
    given = {"pre_silence": pre_silence, "post_silence": post_silence,
             "pause_length": pause_length, "pause_scale": pause_scale,
             "intonation": intonation, "volume_scale": volume_scale, "kana": kana}
    given = {k: v for k, v in given.items() if v is not None}
    if not given:
        return {}
    if backend != "voicevox":
        raise ValueError(
            f'{func}(backend="{backend}"): {" / ".join(given)} は VOICEVOX 専用です'
            '（audio_query を書き換える調整。backend="voicevox" を明示するか、'
            "これらの引数を外してください。語の読み替え readings はどのバックエンドでも使えます）")
    out = {}
    for name, _field, _tag in _VV_ADJUST_FIELDS:
        if name not in given:
            continue
        v = given[name]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{func}: {name} は数値です: {v!r}")
        v = float(v)
        if v != v or v in (float("inf"), float("-inf")) or v < 0:
            raise ValueError(f"{func}: {name} は 0 以上の有限の数です: {v!r}")
        out[name] = v
    if "kana" in given:
        k = given["kana"]
        if not isinstance(k, str) or not k.strip():
            raise ValueError(
                f"{func}: kana は AquesTalk 風カナの文字列です"
                f"（例 \"アタイワ'/カラノ'/ハイレツダッタ'\"）: {k!r}")
        out["kana"] = k.strip()
    return out


def _apply_readings(func, text, readings):
    """語の読み替えを適用し、(合成に渡す文, 合成文の各文字 → 元の文の文字位置) を返す

    readings は {"金": "カネ"} の形。文を左から走査し、その位置で当たる語のうち
    **最も長いもの**を1回だけ置き換える（置き換えた結果をもう一度置き換えない。
    dict の並び順にも依らない）。置き換え後の文字はすべて元の語の先頭位置へ対応づける。
    """
    if readings is None:
        return text, list(range(len(text)))
    if not isinstance(readings, dict):
        raise ValueError(
            f'{func}: readings は {{"語": "読み"}} の dict です: {readings!r}')
    for k, v in readings.items():
        if not isinstance(k, str) or not k or not isinstance(v, str):
            raise ValueError(
                f"{func}: readings のキーは空でない文字列、値は文字列です: {k!r}: {v!r}")
    keys = sorted(readings, key=lambda k: (-len(k), k))
    out, origin, i = [], [], 0
    while i < len(text):
        for k in keys:
            if text.startswith(k, i):
                out.append(readings[k])
                origin.extend([i] * len(readings[k]))
                i += len(k)
                break
        else:
            out.append(text[i])
            origin.append(i)
            i += 1
    synth_text = "".join(out)
    if not synth_text:
        raise ValueError(f"{func}: readings を適用した結果、読み上げる文が空になりました")
    return synth_text, origin


def tts(text, *, backend=None, speaker=None, speed=1.0, pitch=0.0,
        cache_dir=_DEFAULT_CACHE_DIR, host=_DEFAULT_HOST, port=_DEFAULT_PORT,
        pre_silence=None, post_silence=None, pause_length=None, pause_scale=None,
        intonation=None, volume_scale=None, kana=None, readings=None):
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
        readings:  合成に渡す文だけに掛ける語の読み替え（{"金": "カネ"}）。
                   どのバックエンドでも使える。鍵には読み替え後の文が入るので、
                   tts("カネは") と tts("金は", readings={"金": "カネ"}) は同じ wav
        以下は **VOICEVOX 専用**（ほかのバックエンドに渡すと ValueError）。
        None は「エンジンの既定のまま」で、鍵も出力も指定しないときと同じ:
        pre_silence / post_silence: 文の前後の無音（秒。エンジン既定 0.1）
        pause_length: 句読点の間をすべてこの秒数に固定する
        pause_scale:  句読点の間の倍率（pause_length と併用すると固定値に掛かる）
                      ※ pause_length / pause_scale の無い古いエンジンでは RuntimeError
                      ※ 無音と間はどれも speed で割られる（speed=1.25 なら 0.8 倍）
        intonation:   抑揚（intonationScale。1.0 が標準）
        volume_scale: 音量（volumeScale。1.0 が標準）
        kana:         AquesTalk 風カナで読みとアクセントを丸ごと指定する
                      （句は「/」、間つきの句切りは「、」、各句にアクセント「'」が1つ必須。
                      例 "アタイワ'/カラノ'/ハイレツダッタ'"）。指定すると text は
                      音声に効かなくなる（鍵にも入らない）

    Returns:
        生成された wav ファイルのパス（キャッシュ済みなら合成せず即返す）

    Raises:
        ConnectionError: VOICEVOX 未起動（キャッシュに無く合成が要るとき。
                         キャッシュ済みなら保存済みの署名で命中させて返す） /
                         edge のネットワーク不通
        ImportError:     edge-tts 未導入
        ValueError:      パラメータ不正
    """
    if not text:
        raise ValueError("tts: text が空です")
    backend = _resolve_backend(backend, host=host, port=port)
    adjust = _normalize_adjust(
        "tts", backend, pre_silence=pre_silence, post_silence=post_silence,
        pause_length=pause_length, pause_scale=pause_scale, intonation=intonation,
        volume_scale=volume_scale, kana=kana)
    # ここから下の text は「合成に渡す文」（読み替え後）
    text, _origin = _apply_readings("tts", text, readings)

    # キャッシュ鍵に使う speaker は「解決後の値」にする。
    # （speaker=None と speaker=1 が voicevox では同じ音声なので同じ鍵にしたい）
    engine = None
    offline = False
    if backend == "voicevox":
        resolved = _voicevox_speaker(speaker)
        # 接続先とエンジンバージョンを鍵に含める（/version 取得は接続確認を兼ねる）。
        # エンジンに届かないときは cache_dir に保存した前回の署名で代用する
        # （offline=True）。保存値も無ければここで既存どおりの ConnectionError。
        engine, online = _voicevox_engine_state(host, port, cache_dir)
        offline = not online
    elif backend == "edge":
        resolved = _edge_voice(speaker)
    else:
        resolved = _sapi_voice(speaker)

    cache_path = _cache_path(backend, text, resolved, speed, pitch, cache_dir,
                             engine=engine, adjust=adjust)
    # キャッシュ命中判定。CLAUDE.md §5 の「__cache__ 配下に『存在すればスキップ』
    # ガードを置かない」は、再生成がタダ（内容から一意に書き直せる）テキスト成果物
    # の話で、ここは当てはまらない: TTS の再生成には VOICEVOX エンジンの起動や
    # ネットワークが要るため、命中を捨てると合成できない環境でレンダが落ちる。
    # 代わりに、そのガードが防ごうとしている「切り詰められた残骸を以後ずっと
    # 使い続ける」方だけを潰す:
    #   * 書き込みは3バックエンドとも原子的（voicevox=_atomic_write_bytes /
    #     edge=_run_ffmpeg_to_cache / sapi=tmp→os.replace）なので、中断で
    #     半端な wav が最終パスに残ることは無い。
    #   * それでも 0 バイトの残骸（旧版が残したもの・ディスクフル等）は
    #     命中扱いにせず作り直す。空 wav は tts_duration が例外にするだけで、
    #     黙って無音のナレーションになる余地を残さない。
    if _is_cache_hit(cache_path):
        if offline:
            _warn_voicevox_offline_once(host, port, engine)
        return cache_path
    if offline:
        # 合成が要るのにエンジンへ届かない。途中で起動した可能性もあるので
        # 1回だけ問い合わせ直し、届いたらその署名で鍵を作り直して続ける
        engine, online = _voicevox_engine_state(host, port, cache_dir, retry=True)
        if not online:
            raise _needs_synth_error(host, port, text, engine)
        cache_path = _cache_path(backend, text, resolved, speed, pitch, cache_dir,
                                 engine=engine, adjust=adjust)
        if _is_cache_hit(cache_path):
            return cache_path
    os.makedirs(cache_dir, exist_ok=True)

    if backend == "voicevox":
        _synth_voicevox(text, resolved, speed, pitch, cache_path, host, port,
                        adjust=adjust)
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
# ナレーション行ごとに /version を問い合わせないためのメモ化。
_VOICEVOX_ENGINE_SIG_MEMO = {}

# エンジンに届いたときの署名の保存先（cache_dir 直下。endpoint → {"version": ...}）。
# **保存値を使うのはエンジンに届かないときだけ**で、届けば必ず /version の実測が
# 勝ち、保存値も上書きされる。CLAUDE.md が禁じた ffp.json 型の罠（古い永続値が
# 実測より優先されて「変えたのに反映されない」）とは逆向きの使い方であることに注意:
# ここは「実測できない間だけ、最後に実測した値で既存の音声を引く」ための控え。
_ENGINE_SIG_FILE = "engine_sig.json"

# 保存値で代用した（＝エンジンに届かなかった）署名のメモ
# （(endpoint, cache_dir の絶対パス) → 署名）。届かない接続の再試行は Windows では
# 1回あたり約2秒（localhost 名なら約4秒）かかるため、行ごとに試さない。
_VOICEVOX_OFFLINE_SIG_MEMO = {}
# 保存済みの (endpoint, cache_dir の絶対パス)（プロセス内で1回だけ書く）
_VOICEVOX_SIG_SAVED = set()
# 「保存値で代用している」警告を出した endpoint（プロセス内で1回だけ警告する）
_VOICEVOX_OFFLINE_WARNED = set()


def _voicevox_endpoint(host, port):
    """接続先を正規化した endpoint 文字列にする（大文字小文字・型の揺れを吸収）"""
    return f"{str(host).strip().lower()}:{int(port)}"


def _engine_sig_str(endpoint, version):
    """署名文字列「endpoint|バージョン」（実測・保存値の復元で同じ式を使う）"""
    return f"{endpoint}|{version}"


def _voicevox_engine_state(host, port, cache_dir=None, *, retry=False):
    """VOICEVOX エンジンの識別署名「endpoint|バージョン」と「エンジンに届いたか」を
    返す: (署名, online)。実測はプロセス内でメモ化する。

    署名をキャッシュ鍵に混ぜることで、同じ cache_dir のまま接続先(host/port)や
    エンジン本体（バージョン違い）を切り替えても旧エンジンの音声がヒットしない。
    /version の取得は合成前の接続確認を兼ねる。

    * 届いた（online=True）: /version の実測で署名を作り、cache_dir があれば
      engine_sig.json へ原子的に保存する（プロセス内で endpoint×cache_dir ごとに1回）。
    * 届かない（ConnectionError / TimeoutError）: cache_dir の保存値から署名を
      復元して (保存値, False) を返す。保存値が無ければ例外をそのまま投げる。
    retry=True は「保存値で代用中」のメモを捨てて問い合わせ直す（合成が要る直前用）。
    """
    endpoint = _voicevox_endpoint(host, port)
    offkey = (endpoint, os.path.abspath(cache_dir)) if cache_dir else None
    sig = _VOICEVOX_ENGINE_SIG_MEMO.get(endpoint)
    if sig is None:
        if offkey is not None and not retry and offkey in _VOICEVOX_OFFLINE_SIG_MEMO:
            return _VOICEVOX_OFFLINE_SIG_MEMO[offkey], False
        try:
            raw = _request(f"{_base_url(host, port)}/version", host=host, port=port,
                           timeout=_CONNECT_TIMEOUT)
        except (ConnectionError, TimeoutError):
            saved = _load_engine_sig(cache_dir, endpoint) if cache_dir else None
            if saved is None:
                raise
            _VOICEVOX_OFFLINE_SIG_MEMO[offkey] = saved
            return saved, False
        # /version は JSON 文字列（例: "0.14.0"）を返すため引用符を剥がす
        version = raw.decode("utf-8", errors="replace").strip().strip('"')
        sig = _engine_sig_str(endpoint, version)
        _VOICEVOX_ENGINE_SIG_MEMO[endpoint] = sig
        if offkey is not None:
            _VOICEVOX_OFFLINE_SIG_MEMO.pop(offkey, None)
    if offkey is not None and offkey not in _VOICEVOX_SIG_SAVED:
        _save_engine_sig(cache_dir, endpoint, sig.split("|", 1)[1])
        _VOICEVOX_SIG_SAVED.add(offkey)
    return sig, True


def _engine_sig_path(cache_dir):
    """engine_sig.json のパス"""
    return os.path.join(cache_dir, _ENGINE_SIG_FILE)


def _load_engine_sigs(cache_dir):
    """engine_sig.json を dict で読む（無い・壊れている・形が違うときは空 dict）

    壊れていても例外にしない: 次にエンジンへ届いたとき丸ごと書き直される。
    """
    try:
        with open(_engine_sig_path(cache_dir), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_engine_sig(cache_dir, endpoint):
    """保存済みの署名を返す（endpoint の記録が無ければ None）"""
    entry = _load_engine_sigs(cache_dir).get(endpoint)
    if not isinstance(entry, dict):
        return None
    version = entry.get("version")
    if not isinstance(version, str) or not version:
        return None
    return _engine_sig_str(endpoint, version)


def _save_engine_sig(cache_dir, endpoint, version):
    """署名を engine_sig.json（endpoint ごと）へ原子的に保存する

    保存に失敗してもレンダは止めない（失うのは「エンジン停止中にキャッシュを
    引ける」ことだけ）。他の endpoint の記録は残す。
    """
    data = _load_engine_sigs(cache_dir)
    data[endpoint] = {"version": version}
    try:
        _atomic_write_text(
            _engine_sig_path(cache_dir),
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    except OSError as e:
        warnings.warn(
            f"VOICEVOX のエンジン署名を保存できませんでした（{_engine_sig_path(cache_dir)}）: "
            f"{e}。エンジン停止中のキャッシュ再利用ができなくなるだけで、合成は続けます",
            stacklevel=3)


def _warn_voicevox_offline_once(host, port, engine):
    """保存値の署名でキャッシュを引いたことをプロセス内で1回だけ警告する"""
    endpoint = _voicevox_endpoint(host, port)
    if endpoint in _VOICEVOX_OFFLINE_WARNED:
        return
    _VOICEVOX_OFFLINE_WARNED.add(endpoint)
    warnings.warn(
        f"VOICEVOX に接続できません（{_base_url(host, port)}）。"
        f"前回エンジンに届いたときの署名（{engine}）でキャッシュ済みの音声を使います。"
        "キャッシュに無いテキストが出てきた時点で ConnectionError になります",
        stacklevel=3)


def _needs_synth_error(host, port, text, engine, need="合成"):
    """保存値で代用中に未キャッシュのテキストが来たときの ConnectionError"""
    snippet = text if len(text) <= 40 else text[:40] + "…"
    return ConnectionError(
        f"{_not_running_error(host, port)}\n"
        f"  - このテキストはキャッシュに無いため{need}が必要です: {snippet!r}\n"
        f"    （キャッシュ済みのテキストは、前回エンジンに届いたときの署名 {engine} で"
        "再利用しています）")


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


def _voicevox_query(text, speaker, speed, pitch, host, port, adjust=None):
    """合成に渡す audio_query（調整を全部適用した後の dict）を作る

    tts() の合成と tts_marks() の時刻計算が**同じクエリ**を使うための唯一の入口。
    """
    base = _base_url(host, port)
    adjust = adjust or {}

    # 1) audio_query: テキストから合成用クエリ(JSON)を生成
    #    長文ではクエリ生成にも時間がかかるため synthesis と同じ上限を使う
    query_qs = urllib.parse.urlencode({"text": text, "speaker": int(speaker)})
    raw = _request(f"{base}/audio_query?{query_qs}", host=host, port=port,
                   method="POST", timeout=_SYNTH_TIMEOUT)
    query = json.loads(raw)

    # 2) 読みの指定（AquesTalk 風カナ）: アクセント句を丸ごと差し替える
    kana = adjust.get("kana")
    if kana is not None:
        kana_qs = urllib.parse.urlencode(
            {"text": kana, "speaker": int(speaker), "is_kana": "true"})
        try:
            raw = _request(f"{base}/accent_phrases?{kana_qs}", host=host, port=port,
                           method="POST", timeout=_SYNTH_TIMEOUT)
        except RuntimeError as e:
            raise RuntimeError(
                f"tts: kana を VOICEVOX が解釈できませんでした: {kana!r}\n"
                "  AquesTalk 風カナは、句を「/」、間つきの句切りを「、」で区切り、"
                "各句にアクセント「'」を1つ置きます"
                "（例 \"アタイワ'/カラノ'/ハイレツダッタ'\"）\n"
                f"{e}") from e
        query["accent_phrases"] = json.loads(raw)
        query["kana"] = kana

    # 3) 話速・音高と、指定された調整だけを書き込む
    query["speedScale"] = float(speed)
    query["pitchScale"] = float(pitch)
    for name, field, _tag in _VV_ADJUST_FIELDS:
        if name not in adjust:
            continue
        if name in ("pause_length", "pause_scale") and field not in query:
            # 黙って無視すると「鍵は違うのに同じ音声」になり、間が変わらない理由も
            # 分からないので止める
            raise RuntimeError(
                f"tts: この VOICEVOX エンジンは {field} に対応していません"
                f"（{name} は使えません。エンジンを更新するか、文を分けて合成し"
                "無音を自分で挟んでください）")
        query[field] = adjust[name]
    return query


def _synth_voicevox(text, speaker, speed, pitch, cache_path, host, port, adjust=None):
    """VOICEVOX で合成して cache_path へ wav を書き出す

    合成に使ったクエリは wav と同じ鍵の <鍵>.marks.json にも控える
    （tts_marks() がエンジン無しで語の時刻を返せるようにするため）。
    """
    query = _voicevox_query(text, speaker, speed, pitch, host, port, adjust)

    # synthesis: クエリを渡して wav を取得
    wav_bytes = _request(
        f"{_base_url(host, port)}/synthesis?speaker={int(speaker)}", host=host, port=port,
        method="POST", data=json.dumps(query).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "audio/wav"},
        timeout=_SYNTH_TIMEOUT)

    _atomic_write_bytes(cache_path, wav_bytes)
    if _valid_marks_query(query):
        _write_marks_query(_marks_path(cache_path), query)


# =============================================================================
# 語の時刻（tts_marks）
# =============================================================================

# VOICEVOX は音素の長さを「24000Hz / 256 サンプル = 93.75 フレーム/秒」の整数フレームへ
# 丸めてから波形を作る（子音と母音は別々に丸める）。秒の合計ではなくフレームの合計で
# 数えると wav の長さとサンプル単位で一致する（0.25.2 で実測。秒の合計だと最大 40ms ずれた）。
_VV_FRAME_RATE = 93.75
# 疑問形（is_interrogative）の句は、合成のとき語尾に 0.15 秒のモーラが1つ足される
_VV_UPSPEAK_LENGTH = 0.15
_MARKS_FORMAT = 1


def _marks_path(wav_path):
    """wav のキャッシュパス → 同じ鍵の marks.json"""
    return os.path.splitext(wav_path)[0] + ".marks.json"


def _valid_marks_query(query):
    """時刻を計算できる形の audio_query か（壊れた控えを命中扱いにしないための検査）"""
    if not isinstance(query, dict):
        return False
    phrases = query.get("accent_phrases")
    if not isinstance(phrases, list) or not phrases:
        return False

    def num(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    for ap in phrases:
        if not isinstance(ap, dict) or not isinstance(ap.get("moras"), list):
            return False
        moras = list(ap["moras"])
        if ap.get("pause_mora") is not None:
            moras.append(ap["pause_mora"])
        for m in moras:
            if not isinstance(m, dict) or not num(m.get("vowel_length")):
                return False
            if m.get("consonant_length") is not None and not num(m["consonant_length"]):
                return False
    speed = query.get("speedScale", 1.0)
    return num(speed) and speed > 0


def _write_marks_query(path, query):
    """marks.json を原子的に書く（失敗しても合成は止めない。失うのは控えだけ）"""
    try:
        _atomic_write_text(path, json.dumps(
            {"format": _MARKS_FORMAT, "query": query}, ensure_ascii=False) + "\n")
    except OSError as e:
        warnings.warn(f"TTS の語の時刻の控えを保存できませんでした（{path}）: {e}",
                      stacklevel=3)


def _read_marks_query(path):
    """marks.json からクエリを読む（無い・壊れている・形が違うときは None ＝ 作り直す）"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("format") != _MARKS_FORMAT:
        return None
    query = data.get("query")
    return query if _valid_marks_query(query) else None


def _query_timeline(query):
    """audio_query（調整後）から、モーラと間の開始・終了秒を出す純粋関数

    Returns:
        {"elems": [{"kind": "mora" | "pause", "text", "start", "end", "phrase"}, ...],
         "phrases": [{"kana", "start", "end", "pause": (開始, 終了) | None}, ...],
         "speech_start": 前の無音の終わり, "speech_end": 後ろの無音の始まり,
         "duration": wav の長さ}
    秒は wav の先頭から。合成と同じく音素ごとにフレームへ丸めて積む。
    """
    speed = float(query.get("speedScale") or 1.0)
    pause_fixed = query.get("pauseLength")
    pause_scale = query.get("pauseLengthScale")
    pause_scale = 1.0 if pause_scale is None else float(pause_scale)

    def frames(sec):
        return int(round(float(sec or 0.0) / speed * _VV_FRAME_RATE))

    def sec(n):
        return n / _VV_FRAME_RATE

    n = frames(query.get("prePhonemeLength"))
    speech_start = sec(n)
    elems, phrases = [], []
    for pi, ap in enumerate(query["accent_phrases"]):
        p_start = n
        moras = ap["moras"]
        for m in moras:
            m_start = n
            if m.get("consonant_length") is not None:
                n += frames(m["consonant_length"])
            n += frames(m["vowel_length"])
            elems.append({"kind": "mora", "text": str(m.get("text", "")),
                          "start": sec(m_start), "end": sec(n), "phrase": pi})
        if ap.get("is_interrogative") and moras and (moras[-1].get("pitch") or 0) != 0:
            # 疑問形の語尾の伸び。最後のモーラの続きとして数える
            n += frames(_VV_UPSPEAK_LENGTH)
            elems[-1]["end"] = sec(n)
        p_end = n
        pause = None
        pm = ap.get("pause_mora")
        if pm is not None:
            length = pm["vowel_length"] if pause_fixed is None else pause_fixed
            n += frames(float(length) * pause_scale)
            pause = (sec(p_end), sec(n))
            elems.append({"kind": "pause", "text": str(pm.get("text", "、")),
                          "start": pause[0], "end": pause[1], "phrase": pi})
        phrases.append({"kana": "".join(str(m.get("text", "")) for m in moras),
                        "start": sec(p_start), "end": sec(p_end), "pause": pause})
    speech_end = sec(n)
    n += frames(query.get("postPhonemeLength"))
    return {"elems": elems, "phrases": phrases, "speech_start": speech_start,
            "speech_end": speech_end, "duration": sec(n)}


_SMALL_KANA = frozenset("ァィゥェォャュョヮ")
# 文字と読みが違うカナ（助詞の は・へ・を、長音になる う・い、ぢ・づ）
_KANA_ALT = {"ハ": ("ハ", "ワ"), "ヘ": ("ヘ", "エ"), "ヲ": ("ヲ", "オ"),
             "ウ": ("ウ", "オ"), "イ": ("イ", "エ"),
             "ヂ": ("ヂ", "ジ"), "ヅ": ("ヅ", "ズ")}
# 開き・閉じの括弧類（Unicode の Ps / Pe / Pi / Pf）。これだけの並びは「弱い」間の候補
_BRACKET_CATEGORIES = frozenset(("Ps", "Pe", "Pi", "Pf"))


def _is_strong_pause_char(ch):
    """間になりやすい記号か（「、。！？・…：」などの約物と空白。括弧類と改行・制御文字は弱い）

    VOICEVOX 0.25.2 の実測: 読まれる文字に挟まれた約物・空白・括弧はどれも間になるが、
    改行は間にならない。括弧は句読点と隣り合えば1つの間にまとまる。
    """
    cat = unicodedata.category(ch)
    return not (cat in _BRACKET_CATEGORIES or cat[0] == "C" or ch in "  ")


def _tokenize_for_marks(text):
    """文をトークン列にする: [{"idx", "len", "kind": "kana" | "pause" | "other", "cands"}]

    kana はモーラと突き合わせられる仮名（拗音の小書きは前の仮名とまとめて1つ）、
    other は漢字・英数字・長音符など。pause は間になりうる記号・空白の **並び**
    （「？　」「」。」のように、間に読まれる文字の無い連続は1トークン。エンジンは
    並び全体で間を1つしか作らないため）。pause のトークンは次も持つ:
        "rep":    代表の文字位置（並びの中の最初の句読点。無ければ最初の空白、
                  それも無ければ並びの先頭）
        "strong": 句読点・空白を含む並びか（括弧・改行だけなら False）
        "before" / "after": 並びの前 / 後ろに読まれる文字（kana / other）があるか
    """
    tokens = []
    for i, ch in enumerate(text):
        k = chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ゖ" else ch
        prev = tokens[-1] if tokens else None
        if "ァ" <= k <= "ヺ":
            if (k in _SMALL_KANA and prev is not None and prev["kind"] == "kana"
                    and prev["idx"] + prev["len"] == i and prev["len"] == 1):
                prev["cands"] = (prev["cands"][0] + k,)
                prev["len"] = 2
                continue
            tokens.append({"idx": i, "len": 1, "kind": "kana",
                           "cands": _KANA_ALT.get(k, (k,))})
        elif unicodedata.category(ch)[0] in "PZSC":
            # 代表の順位: 2 = 句読点など / 1 = 空白 / 0 = 括弧・改行
            rank = 0 if not _is_strong_pause_char(ch) else (1 if ch.isspace() else 2)
            if prev is not None and prev["kind"] == "pause":
                prev["len"] += 1
                if rank > prev["rank"]:
                    prev["rank"], prev["rep"] = rank, i
                prev["strong"] = prev["rank"] > 0
                continue
            tokens.append({"idx": i, "len": 1, "kind": "pause", "cands": (),
                           "rep": i, "rank": rank, "strong": rank > 0,
                           "before": bool(tokens), "after": False})
        else:
            tokens.append({"idx": i, "len": 1, "kind": "other", "cands": ()})
    # 後ろに読まれる文字があるか（最後のトークンが記号の並びなら、それだけが False）
    for t in tokens[:-1]:
        if t["kind"] == "pause":
            t["after"] = True
    return tokens


def _align_tokens(tokens, elems):
    """トークン列とモーラ・間の列を、順序を保って対応づける: [(トークン番号, 要素番号)]

    重みつきの最長共通部分列。対応できるのは次の2種類だけ:
      * 記号の並び ↔ 間。ただし間は必ずモーラの後ろに来るので、前に読まれる文字の無い
        並び（文頭の「「」など）は対応させない。後ろにモーラが続く間は、後ろに読まれる
        文字がある並びとだけ対応させる（文末の「。」「！」は文中の間を取らない）
      * 仮名 ↔ 同じ読みのモーラ
    優先は「間の対応数 ＞ 仮名の対応数 ＞ 句読点・空白を含む並び（括弧・改行だけの
    並びより優先）」。

    間の対応は、**最適な対応づけのすべてで同じ並びに決まるものだけ**を返す。
    並びの数が間の数より多く、仮名の対応点でも優先でも決まらない間は返さない
    （確実と言えない対応を「確実」として返さないため。呼び出し側は近似へ落とす）。
    """
    n, m = len(tokens), len(elems)
    last_mora = max((j for j, e in enumerate(elems) if e["kind"] == "mora"), default=-1)
    w_kana = n + 1
    w_pause = (n + 2) * w_kana

    def score(i, j):
        t, e = tokens[i], elems[j]
        if t["kind"] == "pause":
            if e["kind"] != "pause" or not t["before"] or t["after"] != (j < last_mora):
                return 0
            return w_pause + (1 if t["strong"] else 0)
        if t["kind"] == "kana" and e["kind"] == "mora" and e["text"] in t["cands"]:
            return w_kana
        return 0

    sc = [[score(i, j) for j in range(m)] for i in range(n)]
    # back[i][j]: tokens[i:] と elems[j:] の最良 / fwd[i][j]: tokens[:i] と elems[:j] の最良
    back = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        row, below = back[i], back[i + 1]
        for j in range(m - 1, -1, -1):
            best = below[j] if below[j] >= row[j + 1] else row[j + 1]
            s = sc[i][j]
            if s and s + below[j + 1] > best:
                best = s + below[j + 1]
            row[j] = best
    fwd = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        row, above = fwd[i], fwd[i - 1]
        for j in range(1, m + 1):
            best = above[j] if above[j] >= row[j - 1] else row[j - 1]
            s = sc[i - 1][j - 1]
            if s and s + above[j - 1] > best:
                best = s + above[j - 1]
            row[j] = best
    opt = back[0][0]

    def unique(i, j):
        """間 j が、どの最適な対応づけでも並び i に対応するか"""
        for i2 in range(n):
            if i2 != i and sc[i2][j] and fwd[i2][j] + sc[i2][j] + back[i2 + 1][j + 1] == opt:
                return False
        # 間 j を対応させないままでも最適になるなら決まっていない
        return all(fwd[i2][j] + back[i2][j + 1] < opt for i2 in range(n + 1))

    pairs, i, j = [], 0, 0
    while i < n and j < m:
        s = sc[i][j]
        if s and back[i][j] == s + back[i + 1][j + 1]:
            if elems[j]["kind"] != "pause" or unique(i, j):
                pairs.append((i, j))
            i += 1
            j += 1
        elif back[i][j] == back[i][j + 1]:
            j += 1
        else:
            i += 1
    return pairs


def _char_marks(text, timeline):
    """文の各文字の (読まれ始める秒, 精度) と、間 → 代表の記号の文字位置 を返す

    戻り値: (chars, pause_index)。pause_index は {elems の番号: 文字位置}
    （対応が決まった間だけ）。精度は TtsMarks の docstring 参照。
    """
    tokens = _tokenize_for_marks(text)
    elems = timeline["elems"]
    n, m = len(tokens), len(elems)
    tok = [None] * n   # トークンごとの (秒, 精度)
    pause_index = {}
    anchors = [(-1, -1)] + _align_tokens(tokens, elems) + [(n, m)]
    for (ai, aj), (bi, bj) in zip(anchors, anchors[1:]):
        if bi < n:
            if elems[bj]["kind"] == "pause":
                tok[bi] = (elems[bj]["start"], "pause")
                pause_index[bj] = tokens[bi]["rep"]
            else:
                tok[bi] = (elems[bj]["start"], "kana")
        next_time = elems[bj]["start"] if bj < m else timeline["speech_end"]
        free = [j for j in range(aj + 1, bj) if elems[j]["kind"] == "mora"]
        content = [i for i in range(ai + 1, bi) if tokens[i]["kind"] != "pause"]
        for k, i in enumerate(content):
            if not free:
                tok[i] = (next_time, "approx")
                continue
            j = free[k * len(free) // len(content)]
            if k == 0 and j == aj + 1:
                # 直前の対応点のすぐ次のモーラ。対応点が間（または文頭）なら確実
                prec = "start" if aj < 0 or elems[aj]["kind"] == "pause" else "kana"
            else:
                prec = "approx"
            tok[i] = (elems[j]["start"], prec)
        # 対応の付かなかった記号は、次に読まれる文字の時刻
        later = next_time
        for i in range(bi - 1, ai, -1):
            if tokens[i]["kind"] == "pause":
                tok[i] = (later, "approx")
            else:
                later = tok[i][0]
    chars = [None] * len(text)
    for t, mark in zip(tokens, tok):
        for c in range(t["idx"], t["idx"] + t["len"]):
            chars[c] = mark
    return chars, pause_index


class TtsMarks:
    """合成した wav の中で、文の各文字が読まれ始める秒（tts_marks() の戻り値）

    秒はすべて **wav の先頭から**（頭の無音 pre_silence を含む）。タイムラインへ
    `Object(wav) @ t0` で置いたなら、語の時刻は `t0 + marks.time_of("語")`。

    属性:
        text:         元の文（readings を適用する前）
        duration:     クエリから計算した wav の長さ（VOICEVOX 0.25.2 では実 wav と
                      サンプル単位で一致。tts_duration(wav) と突き合わせて確かめられる）
        speech_start: 最初のモーラの開始（＝頭の無音の長さ）
        speech_end:   最後のモーラの終わり（この後ろは尻の無音）
        kana:         エンジンが返した読み（AquesTalk 風カナ。読みの点検に使える）
        phrases:      アクセント句 [{"kana", "start", "end", "pause": (開始, 終了) | None}]
        pauses:       間 [{"start", "end", "index": 対応する記号の文字位置 | None}]
                      index は、記号が並んでいれば（「」。」「？　」）その中の最初の
                      句読点。どの記号の間か決められないときは None
        moras:        モーラ [{"text", "start", "end", "phrase": 句の番号}]

    記号と間の対応: 間に読まれる文字の無い記号の並び（「？　」「。「」「」。」）は
    1つの間に対応し、並びの文字はどれもその間の開始の時刻になる。文頭の記号
    （前に読まれる文字が無い「「」など）と文末の記号は文中の間に対応させない。
    VOICEVOX 0.25.2 では、読まれる文字に挟まれた約物・空白・括弧の並びはどれも
    間を1つ作り、改行は作らない（実測）。並びの数と間の数が合わないときは、
    仮名の対応点、次に「句読点・空白を含む並びを括弧・改行だけの並びより優先」で
    決め、それでも一意に決まらない間は対応させない（index は None、その前後は "approx"）。

    精度（precision_at / precision_of が返す文字列）:
        "pause"  間に対応した記号（の並び）。時刻は間の開始。確実
        "start"  文頭か、対応の決まった間の直後に読まれる文字。確実
                 （「。」「、」の次の語はこれ）
        "kana"   モーラに対応した仮名、またはその直後の文字。モーラ単位で正しいが、
                 同じ仮名が近くに複数あって漢字の読みと紛れると隣の同じ音へずれうる
        "approx" 近似。漢字・英数字はカナとの対応が取れないので、前後の対応点の間の
                 モーラへ文字数で按分している（誤差は最大でその区間の長さ）。
                 pauses の index がすべて決まっていれば、間をまたぐことはない
                 （index が None の間があるときだけ、その間の前後の "approx" の
                 文字が間の反対側の時刻になりうる）。readings で読み替えた語の
                 2文字目以降は語の先頭と同じ時刻。対応する間の無い記号
                 （文頭の括弧・文末の句点など）は次に読まれる文字の時刻
    時刻そのものの誤差: 実測（0.25.2・話者13）で、計算した間の終わりは silencedetect
    （-35dB）の「音の出始め」より 0.01〜0.07 秒早い（次の子音の立ち上がりが静かなぶん。
    は行のような弱い子音で大きい）。計算した duration は実 wav とサンプル単位で一致した。
    ほかの版のエンジンでフレームの丸め方が違う場合は duration が実 wav とずれるので、
    tts_duration(wav) と比べれば気づける。
    """

    def __init__(self, text, query, origin=None, synth_text=None):
        synth_text = text if synth_text is None else synth_text
        origin = list(range(len(text))) if origin is None else origin
        tl = _query_timeline(query)
        self.text = text
        self.duration = tl["duration"]
        self.speech_start = tl["speech_start"]
        self.speech_end = tl["speech_end"]
        self.kana = query.get("kana")
        self.phrases = tl["phrases"]
        self.moras = [{"text": e["text"], "start": e["start"], "end": e["end"],
                       "phrase": e["phrase"]}
                      for e in tl["elems"] if e["kind"] == "mora"]
        synth_chars, pause_index = _char_marks(synth_text, tl)
        # 合成文の文字 → 元の文の文字。元の1文字に複数対応するときは最初のもの
        chars = [None] * len(text)
        for c, o in enumerate(origin):
            if chars[o] is None:
                chars[o] = synth_chars[c]
        # 読み替えた語の2文字目以降（対応する合成文字が無い）は直前の文字と同じ時刻
        for o in range(len(text)):
            if chars[o] is None and o > 0 and chars[o - 1] is not None:
                chars[o] = (chars[o - 1][0], "approx")
        later = (self.speech_end, "approx")
        for o in range(len(text) - 1, -1, -1):
            if chars[o] is None:
                chars[o] = (later[0], "approx")
            else:
                later = chars[o]
        self._chars = chars
        # 間 → 対応した記号の文字位置（元の文の位置。決まらなかった間は None）
        self.pauses = [{"start": e["start"], "end": e["end"],
                        "index": (origin[pause_index[j]] if j in pause_index else None)}
                       for j, e in enumerate(tl["elems"]) if e["kind"] == "pause"]

    def _find(self, word, nth):
        if not isinstance(word, str) or not word:
            raise ValueError(f"TtsMarks: 語は空でない文字列です: {word!r}")
        if isinstance(nth, bool) or not isinstance(nth, int) or nth < 0:
            raise ValueError(
                f"TtsMarks: nth は 0 以上の整数です（0 が最初の出現）: {nth!r}")
        pos, start = -1, 0
        for _ in range(nth + 1):
            pos = self.text.find(word, start)
            if pos < 0:
                break
            start = pos + 1
        if pos < 0:
            which = "" if not nth else f"（{nth + 1} 個目）"
            raise ValueError(
                f"TtsMarks: 文の中に {word!r}{which} がありません: {self.text!r}"
                "（readings で読み替える前の、元の文の語で引きます）")
        return pos

    def time_at(self, index):
        """文字位置 index の文字が読まれ始める秒（index == len(text) は話し終わり）"""
        if index == len(self.text):
            return self.speech_end
        if not 0 <= index < len(self.text):
            raise IndexError(f"TtsMarks: 文字位置が範囲外です: {index}")
        return self._chars[index][0]

    def precision_at(self, index):
        """文字位置 index の時刻の精度（"pause" / "start" / "kana" / "approx"）"""
        if not 0 <= index < len(self.text):
            raise IndexError(f"TtsMarks: 文字位置が範囲外です: {index}")
        return self._chars[index][1]

    def time_of(self, word, nth=0):
        """語 word（nth 個目。0 始まり）が読まれ始める秒。文に無ければ ValueError"""
        return self.time_at(self._find(word, nth))

    def span_of(self, word, nth=0):
        """語 word が読まれる区間 (開始秒, 終了秒)。終了は次の文字の開始（文末なら話し終わり）"""
        pos = self._find(word, nth)
        return self.time_at(pos), self.time_at(pos + len(word))

    def precision_of(self, word, nth=0):
        """time_of(word) の精度"""
        return self.precision_at(self._find(word, nth))

    def __repr__(self):
        return (f"TtsMarks({self.text!r}, duration={self.duration:.3f}, "
                f"kana={self.kana!r})")


def tts_marks(text, *, backend=None, speaker=None, speed=1.0, pitch=0.0,
              cache_dir=_DEFAULT_CACHE_DIR, host=_DEFAULT_HOST, port=_DEFAULT_PORT,
              pre_silence=None, post_silence=None, pause_length=None, pause_scale=None,
              intonation=None, volume_scale=None, kana=None, readings=None):
    """tts() と同じ条件で合成した wav の「文字位置 → 秒」の対応（TtsMarks）を返す

    引数は tts() と同じ。**同じ引数で呼べば、tts() が返す wav の中の時刻**になる
    （wav と同じ鍵の `<鍵>.marks.json` に、合成に使った audio_query を控える）。
    VOICEVOX 専用（モーラごとの長さを返すのが VOICEVOX だけのため。
    ほかのバックエンドでは ValueError）。

    使用例:
        kw = dict(backend="voicevox", speaker=13, readings={"金": "カネ"})
        wav = tts("金は戻らなかった。答えは、まだ無い。", **kw)
        m = tts_marks("金は戻らなかった。答えは、まだ無い。", **kw)
        m.time_of("答えは")        # 「答えは」が読まれ始める秒（wav の先頭から）
        m.pauses                   # 句読点の間の [開始, 終了]
        m.precision_of("答えは")   # "start"（間の直後なので確実）

    控えの扱い（tts() の engine_sig.json と同じ流儀）:
      * tts() で合成したときに控えも書くので、その後はエンジンが止まっていても返せる
      * 控えが無い（この機能より前に合成した wav・控えが壊れている）ときは
        audio_query だけを問い合わせて控えを作る（wav は合成しない）。
        エンジンに届かなければ ConnectionError
      * エンジンのバージョンが変われば鍵が変わるので、古い控えは使われない

    精度は TtsMarks の docstring を参照（句読点とその直後の語は確実、
    漢字の途中は近似。どの記号の間か決められない所は近似へ落とす）。
    """
    if not text:
        raise ValueError("tts_marks: text が空です")
    backend = _resolve_backend(backend, host=host, port=port)
    if backend != "voicevox":
        raise ValueError(
            f'tts_marks(backend="{backend}"): 語の時刻を出せるのは VOICEVOX だけです'
            '（audio_query のモーラの長さから計算するため。backend="voicevox" を'
            "明示してください）")
    adjust = _normalize_adjust(
        "tts_marks", backend, pre_silence=pre_silence, post_silence=post_silence,
        pause_length=pause_length, pause_scale=pause_scale, intonation=intonation,
        volume_scale=volume_scale, kana=kana)
    synth_text, origin = _apply_readings("tts_marks", text, readings)
    resolved = _voicevox_speaker(speaker)
    engine, online = _voicevox_engine_state(host, port, cache_dir)

    def path_for(engine_sig):
        return _marks_path(_cache_path(backend, synth_text, resolved, speed, pitch,
                                       cache_dir, engine=engine_sig, adjust=adjust))

    path = path_for(engine)
    query = _read_marks_query(path)
    if query is not None and not online:
        _warn_voicevox_offline_once(host, port, engine)
    if query is None and not online:
        # 控えが無いのにエンジンへ届かない。途中で起動した可能性があるので1回だけ試す
        engine, online = _voicevox_engine_state(host, port, cache_dir, retry=True)
        if not online:
            raise _needs_synth_error(host, port, synth_text, engine,
                                     need="audio_query の問い合わせ")
        path = path_for(engine)
        query = _read_marks_query(path)
    if query is None:
        query = _voicevox_query(synth_text, resolved, speed, pitch, host, port, adjust)
        if not _valid_marks_query(query):
            raise RuntimeError(
                "tts_marks: VOICEVOX の audio_query に accent_phrases がありません"
                f"（読み上げる音の無い文かもしれません）: {synth_text!r}")
        os.makedirs(cache_dir, exist_ok=True)
        _write_marks_query(path, query)
    return TtsMarks(text, query, origin=origin, synth_text=synth_text)


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
