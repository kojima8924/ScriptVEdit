# -*- coding: utf-8 -*-

import os
import json
import hashlib
import math as _math
import builtins as _builtins
from importlib import import_module as _import_module


# --- オーディオ系ファクトリ ---

def duck_under(other, *, ratio=8, threshold=0.05, attack=20, release=250):
    """sidechaincompress で other（ナレーション等）再生中に自音量を下げるAudioEffect。
    other は同じProjectに存在する音声Objectを指定する。"""
    if not isinstance(other, Object):
        raise TypeError(f"duck_under: other は音声Objectを指定してください: {type(other)}")
    return AudioEffect("duck_under", other=other, ratio=ratio,
                       threshold=threshold, attack=attack, release=release)


def loop(until=None):
    """aloop で音声を until 時刻までループさせるAudioEffect。
    until 省略時は Project.duration までループする。"""
    return AudioEffect("loop", until=until)


def _probe_audio_length(path):
    """音声/動画の長さを取得（取得不能時はNone）"""
    proj = Project._current
    if proj is not None:
        info = proj._probe_media(path)
        if info:
            # 動画コンテナは映像の方が長いことがある。自動設定した
            # audio_sequence.duration が実際の連結音声より伸びないよう、
            # 音声ストリーム尺を優先する。
            return info.get("audio_duration") or info.get("duration")
    return None


def _validate_audio_source(func, path):
    if not isinstance(path, str):
        raise TypeError(f"{func}: ソースはパス文字列で指定してください: {path!r}")
    if not os.path.exists(path):
        raise FileNotFoundError(f"{func}: 音声ファイルが見つかりません: {path}")
    media_type = _detect_media_type(path)
    if media_type not in ("audio", "video"):
        raise ValueError(f"{func}: 音声(または動画音声)のみ指定できます: {path}")
    if media_type == "video" and Project._current is not None:
        info = Project._current._probe_media(path)
        if info is not None and not info.get("has_audio", False):
            raise ValueError(
                f"{func}: 動画に音声ストリームがありません: {path}")


def audio_sequence(*objs, crossfade=1.0):
    """複数の音声を acrossfade で連結した1つの音声Objectを生成（キャッシュ生成物）。
    objs は音声Object、Narration、または音声パス文字列（2つ以上）。
    Narrationを渡すと字幕もcrossfade込みの相対時刻へ配置し、返却Objectを
    数値時刻へ ``@`` した際に字幕も一緒に移動する。"""
    if len(objs) < 2:
        raise ValueError("audio_sequence: 2つ以上の音声を指定してください")
    _require_number("audio_sequence", "crossfade", crossfade, 0.01, None)
    proj = Project._current
    sources = []
    input_volumes = []
    linked_subtitles = []
    consumed = []  # 全検証を通過してからまとめて消費する（途中失敗で Project を壊さない）
    for o in objs:
        narration = o if isinstance(o, Narration) else None
        if narration is not None:
            o = narration.audio
        if isinstance(o, Object):
            if o.media_type != "audio":
                raise ValueError(
                    f"audio_sequence: 音声Objectのみ連結できます: {o.source}")
            if o.transforms or o.effects:
                raise ValueError(
                    f"audio_sequence: '{o.source}' に Effect が適用されています。\n"
                    f"連結時は素のObjectを渡し、効果は連結後の生成Objectに付けてください"
                    f"（例: seq <= again(0.5)）。")
            input_volume = 1.0
            if o.audio_effects:
                # narrate(volume=...) は voice() が音声Objectへ again(Const) を
                # 付けて表現する。Narrationを直接受け取る公開APIなのに、この標準
                # オプションまで「加工済み」として拒否しないよう、定数音量だけを
                # 連結前の各入力へ移植する。それ以外は従来どおり明示拒否する。
                if narration is None or any(
                        effect.name != "again" for effect in o.audio_effects):
                    raise ValueError(
                        f"audio_sequence: '{o.source}' に AudioEffect が適用されています。\n"
                        f"Narrationの volume 以外の効果は連結後の生成Objectに付けてください"
                        f"（例: seq <= again(0.5)）。")
                for effect in o.audio_effects:
                    value = getattr(effect.params.get("value"), "value", None)
                    if (isinstance(value, bool)
                            or not isinstance(value, (int, float))):
                        raise ValueError(
                            "audio_sequence: Narrationの volume は定数値のみ連結できます。"
                            "動的な音量効果は連結後の生成Objectに付けてください。")
                    try:
                        value = float(value)
                    except (OverflowError, TypeError, ValueError):
                        value = float("inf")
                    if not _math.isfinite(value):
                        raise ValueError(
                            "audio_sequence: Narrationの volume は有限値で指定してください。")
                    input_volume *= value
                    if not _math.isfinite(input_volume):
                        raise ValueError(
                            "audio_sequence: Narrationの volume の積が大きすぎます。"
                            "連結後の生成Objectで音量を調整してください。")
            sources.append(o.source)
            input_volumes.append(input_volume)
            linked_subtitles.append(
                narration.subtitle if narration is not None else None)
            if proj is not None and o in proj.objects:
                consumed.append(o)
        elif isinstance(o, str):
            _validate_audio_source("audio_sequence", o)
            sources.append(o)
            input_volumes.append(1.0)
            linked_subtitles.append(None)
        else:
            raise TypeError(
                "audio_sequence: Narration、音声Object、またはパス文字列のみ: "
                f"{type(o)}")
    n = len(sources)
    lengths = [_probe_audio_length(s) or 5.0 for s in sources]
    # acrossfade は各入力が crossfade 以上の長さを要する。
    # 素材長 < crossfade だと total が 0/負値になり後続配置が破綻するため拒否。
    for s, ln in zip(sources, lengths):
        if ln < crossfade:
            raise ValueError(
                f"audio_sequence: 素材長({ln:.3f}s)が crossfade({crossfade}s)未満です: {s}\n"
                f"crossfade を短くするか、より長い素材を指定してください。")
    total = sum(lengths) - crossfade * (n - 1)

    # 全入力の検証が済んだのでここで初めてタイムラインから除外する（原子的な消費）
    for o in consumed:
        proj.objects.remove(o)

    sigs = ["audio_sequence"]
    sigs.extend(_source_signature(source) for source in sources)
    # 音量1.0だけの従来ケースは既存キャッシュ鍵を維持する。入力ごとの音量が
    # 実際に出力へ影響する場合だけ追加署名を入れ、異なる音量同士を分離する。
    if any(volume != 1.0 for volume in input_volumes):
        sigs.append(
            "input_volumes=" + ",".join(repr(volume) for volume in input_volumes))
    sigs.extend([f"cf={crossfade}", f"ev={_ENGINE_VER}"])
    key = hashlib.sha256("||".join(sigs).encode()).hexdigest()[:16]
    cache_path = os.path.join(_ARTIFACT_DIR, "aseq", f"{key}.m4a")

    cmd = ["ffmpeg", "-y"]
    for s in sources:
        cmd.extend(["-i", s])
    parts = []
    input_refs = []
    for i, volume in enumerate(input_volumes):
        ref = f"[{i}:a]"
        if volume != 1.0:
            out = f"[avol{i}]"
            parts.append(f"{ref}volume={volume!r}{out}")
            ref = out
        input_refs.append(ref)
    cur = input_refs[0]
    for i in range(1, n):
        out = f"[axf{i}]"
        parts.append(f"{cur}{input_refs[i]}acrossfade=d={crossfade}{out}")
        cur = out
    cmd.extend(["-filter_complex", ";".join(parts), "-map", cur,
                "-c:a", "aac", "-b:a", "192k", cache_path])
    obj = _finalize_generated_object(cache_path, cmd, list(sources), total)
    # 尺は全入力のプローブ時に確定済み。time(total) を要求せず
    # そのままタイムラインの総尺と進行に反映する。
    obj.duration = total
    # Narration字幕は入力audioがタイムラインから消費されても順次時刻を
    # 失わないよう、連結後Objectからの相対offsetへ固定する。
    timeline_links = []
    offset = 0.0
    for subtitle, length in zip(linked_subtitles, lengths):
        if subtitle is not None:
            # 絶対時刻へ固定せず、sequenceの実開始（順次/@/アンカー解決後）
            # からの相対配置としてProject resolverへ渡す。
            subtitle._timeline_owner = obj
            subtitle._timeline_offset = offset
            subtitle._fixed_start = None
            subtitle._start_after = None
            subtitle._advance = False
            timeline_links.append((subtitle, offset))
        offset += length - crossfade
    if timeline_links:
        obj._timeline_links = tuple(timeline_links)
    return obj


def sfx(source, at, *, volume=1.0):
    """同一音源を複数時刻(at)に配置した1つの音声Objectを生成（adelay+amix合成）。
    at は秒のリスト。生成Objectは開始0でタイムラインに配置する想定。"""
    _validate_audio_source("sfx", source)
    if not isinstance(at, (list, tuple)) or len(at) == 0:
        raise ValueError("sfx: at には配置時刻(秒)のリストを指定してください")
    for t in at:
        _require_number("sfx", "at要素", t, 0, None)
    _require_number("sfx", "volume", volume, 0, None)
    srclen = _probe_audio_length(source) or 5.0
    times = list(at)
    n = len(times)
    total = _builtins.max(times) + srclen

    sigs = ["sfx", _source_signature(source),
            "at=" + ",".join(str(t) for t in times),
            f"vol={volume}", f"ev={_ENGINE_VER}"]
    key = hashlib.sha256("||".join(sigs).encode()).hexdigest()[:16]
    cache_path = os.path.join(_ARTIFACT_DIR, "sfx", f"{key}.m4a")

    parts = ["[0:a]asplit=" + str(n) + "".join(f"[s{i}]" for i in range(n))]
    delayed = []
    for i, t in enumerate(times):
        ms = int(t * 1000)
        if ms > 0:
            parts.append(f"[s{i}]adelay={ms}:all=1[d{i}]")
        else:
            parts.append(f"[s{i}]anull[d{i}]")
        delayed.append(f"[d{i}]")
    mix_in = "".join(delayed)
    tail = f",volume={volume}" if volume != 1.0 else ""
    if n == 1:
        parts.append(f"{mix_in}anull{tail}[a]")
    else:
        parts.append(f"{mix_in}amix=inputs={n}:normalize=0{tail}[a]")
    cmd = ["ffmpeg", "-y", "-i", source,
           "-filter_complex", ";".join(parts), "-map", "[a]",
           "-c:a", "aac", "-b:a", "192k", "-t", str(total), cache_path]
    return _finalize_generated_object(cache_path, cmd, [source], total)


def voice(text, *, backend=None, speaker=None, speed=1.0, pitch=0.0, volume=1.0,
          **tts_kwargs):
    """scriptvedit.tts で text を音声合成し、その wav を素材とする音声Objectを返す。

    backend: "voicevox"（キャラボイス・オフライン。要エンジン起動）/
             "edge"（pip install edge-tts。導入が楽・オンライン必須）/
             "sapi"（Windows標準・追加導入不要）。
             None なら自動選択（環境変数 SCRIPTVEDIT_TTS_BACKEND →
             VOICEVOX 起動中なら voicevox → 無ければ edge）。
    speaker: バックエンドごとに解釈が違う（voicevox: 数値ID / edge: 音声名
             （例 "ja-JP-NanamiNeural"） / sapi: 音声名）。None で各既定。

    duration は tts_duration による実長を自動設定するため、字幕・タイムラインと
    自然に同期する。scriptvedit.tts が使えない/バックエンド未使用可なら親切なエラーを投げる。

    使用例:
        v = voice("こんにちは、世界", speaker=3)                       # VOICEVOX
        v = voice("こんにちは、世界", backend="edge")                  # edge-tts
        v.show(v.duration)
    """
    try:
        # 属性参照(`from scriptvedit import tts`)ではなくモジュール直接 import。
        # プラグインがパッケージ名前空間へ同名を注入していても影響を受けない。
        _tts_mod = _import_module("scriptvedit.tts")
    except ImportError as e:
        raise ImportError(
            "voice() には scriptvedit.tts が必要です。"
            "scriptvedit.py と同じディレクトリに配置してください。") from e
    wav = _tts_mod.tts(text, backend=backend, speaker=speaker, speed=speed,
                     pitch=pitch, **tts_kwargs)
    dur = _tts_mod.tts_duration(wav)
    obj = Object(wav)
    obj.duration = dur
    if volume != 1.0:
        _require_number("voice", "volume", volume, 0, None)
        obj.audio_effects.append(again(volume))
    return obj


class Narration:
    """narrate() の返値。(audio, subtitle) としてタプルアンパック可能な軽量ラッパー。

    audio:    音声Object（voice()相当。durationはTTS実長）
    subtitle: 字幕Object（subtitle=False指定時はNone）
    duration: 音声の実長（秒）のショートカットプロパティ
    """
    __slots__ = ("audio", "subtitle")

    def __init__(self, audio, subtitle):
        self.audio = audio
        self.subtitle = subtitle

    def __iter__(self):
        yield self.audio
        yield self.subtitle

    def __repr__(self):
        return f"Narration(audio={self.audio!r}, subtitle={self.subtitle!r})"

    def __matmul__(self, at):
        """``narration @ t`` で音声と字幕を同じ時刻へ同期配置する。"""
        self.audio @ at
        if self.subtitle is not None:
            self.subtitle @ at
        return self

    @property
    def duration(self):
        return self.audio.duration


_SUBTITLE_NO_LINE_START = frozenset(
    "、。，．・：；？！ー―…‥ヽヾゝゞ々〃仝〆〇"
    "ぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶ"
    "）〕］｝〉》」』】〙〗〟’”｠»)]},.!?:;%"
)
_SUBTITLE_NO_LINE_END = frozenset(
    "（〔［｛〈《「『【〘〖〝‘“｟«([{"
)


def _validate_subtitle_line_limit(name, value):
    """字幕の文字数/行数上限を正の整数へ正規化する。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(
            f"narrate: {name} は1以上の整数で指定してください: {value!r}")
    return value


def _wrap_subtitle_text(content, max_chars):
    """明示改行を保ち、日本語の行頭/行末禁則を避けながら固定文字数で折り返す。

    max_chars は表示幅の推測値ではなく、Unicode文字数（改行を除く）の上限。
    禁則を満たす分割点が上限内にない極端な記号列だけはハード分割する。
    """
    if max_chars is None:
        return content

    result = []
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    for paragraph in normalized.split("\n"):
        if not paragraph:
            result.append("")
            continue

        start = 0
        length = len(paragraph)
        while length - start > max_chars:
            hard_end = start + max_chars
            end = hard_end

            # 「次行が句読点」「現在行が開き括弧」で始終しない分割点を
            # 上限から後方へ探す。日本語本文は空白を持たないため、単語分割
            # ライブラリに依存せず決定的に処理する。
            while end > start + 1:
                starts_badly = paragraph[end] in _SUBTITLE_NO_LINE_START
                ends_badly = paragraph[end - 1] in _SUBTITLE_NO_LINE_END
                if not starts_badly and not ends_badly:
                    break
                end -= 1
            if end <= start or (
                    paragraph[end] in _SUBTITLE_NO_LINE_START
                    or paragraph[end - 1] in _SUBTITLE_NO_LINE_END):
                end = hard_end

            line = paragraph[start:end].rstrip()
            # 空白だけの区間でも進行を保証する。
            result.append(line if line else paragraph[start:end])
            start = end
            while start < length and paragraph[start].isspace():
                start += 1
        result.append(paragraph[start:])
    return "\n".join(result)


def _prepare_subtitle_text(text_content, *, subtitle_text=None,
                           subtitle_formatter=None,
                           subtitle_max_chars=None,
                           subtitle_max_lines=None):
    """narrate() の表示文を解決し、formatter→折り返し→行数検証を行う。"""
    max_chars = _validate_subtitle_line_limit(
        "subtitle_max_chars", subtitle_max_chars)
    max_lines = _validate_subtitle_line_limit(
        "subtitle_max_lines", subtitle_max_lines)
    if subtitle_text is not None and not isinstance(subtitle_text, str):
        raise TypeError(
            "narrate: subtitle_text は文字列またはNoneで指定してください: "
            f"{subtitle_text!r}")

    content = str(text_content) if subtitle_text is None else subtitle_text
    if subtitle_formatter is not None:
        if not callable(subtitle_formatter):
            raise TypeError(
                "narrate: subtitle_formatter は callable またはNoneを指定して"
                f"ください: {subtitle_formatter!r}")
        content = subtitle_formatter(content)
        if not isinstance(content, str):
            raise TypeError(
                "narrate: subtitle_formatter の戻り値は文字列が必要です: "
                f"{type(content).__name__}")

    content = _wrap_subtitle_text(content, max_chars)
    line_count = content.count("\n") + 1
    if max_lines is not None and line_count > max_lines:
        raise ValueError(
            "narrate: 字幕が subtitle_max_lines を超えました "
            f"({line_count}行 > {max_lines}行)。subtitle_textを短くするか、"
            "subtitle_max_chars/subtitle_max_linesを調整してください")
    return content


def _normalize_subtitle_safe_area(value):
    """字幕safe areaを(left, top, right, bottom)の比率マージンへ正規化する。

    数値は四辺共通、2要素は(horizontal, vertical)、4要素は
    (left, top, right, bottom) として扱う。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(
            f"narrate: subtitle_safe_area は比率の数値/タプルです: {value!r}")
    if isinstance(value, (int, float)):
        margins = (value, value, value, value)
    elif isinstance(value, (tuple, list)) and len(value) == 2:
        horizontal, vertical = value
        margins = (horizontal, vertical, horizontal, vertical)
    elif isinstance(value, (tuple, list)) and len(value) == 4:
        margins = tuple(value)
    else:
        raise ValueError(
            "narrate: subtitle_safe_area は数値、(horizontal, vertical)、"
            f"または(left, top, right, bottom)で指定してください: {value!r}")

    for margin in margins:
        if (isinstance(margin, bool) or not isinstance(margin, (int, float))
                or not _math.isfinite(margin) or margin < 0 or margin >= 1):
            raise ValueError(
                "narrate: subtitle_safe_area の各値は0以上1未満の有限数です: "
                f"{value!r}")
    left, top, right, bottom = (float(v) for v in margins)
    if left + right >= 1 or top + bottom >= 1:
        raise ValueError(
            "narrate: subtitle_safe_area の左右/上下マージン合計は1未満が"
            f"必要です: {value!r}")
    return left, top, right, bottom


def narrate(text_content, *, backend=None, speaker=None, speed=1.0, pitch=0.0,
            volume=1.0,
            subtitle=True, subtitle_text=None, subtitle_formatter=None,
            subtitle_max_chars=None, subtitle_max_lines=None,
            subtitle_safe_area=None, subtitle_style=None,
            x=0.5, y=0.9, size=36, color="white", font=None,
            box=True, box_color="black@0.6", box_border=10,
            border=0, border_color="black", shadow=(0, 0),
            shadow_color="black@0.6", alpha=1.0,
            anchor="center", **tts_kwargs):
    """TTSナレーション音声 + 同期字幕を1回の呼び出しで生成・配置する。

    voice()(scriptvedit.tts)でtext_contentを音声合成し、subtitle=Trueなら
    text()字幕Objectも生成する。subtitle_text で読み上げと異なる表示文を指定でき、
    subtitle_formatter は表示文を受け取って文字列を返すcallableとして、その後
    subtitle_max_charsによる日本語禁則対応の折り返しを行う。subtitle_max_linesを
    超えた場合は内容を黙って切らずValueErrorにする。字幕の表示窓は音声の実長に
    一致させ、両者は同じ開始時刻からタイムラインに配置される。
    複数回呼べば、音声の実長ぶんタイムラインが進むため順次配置される
    （字幕は各回の音声窓にだけ表示される）。

    x/y/size/color/font/box/box_color/box_border/border/border_color/
    shadow/shadow_color/alpha/anchor は text() と同じ意味の字幕スタイル引数
    （既定はナレーション向けに下部中央+半透明ボックス）。
    border=2 等の縁取りや shadow=(2, 2) の影は字幕の読みやすさ向上に有効。
    subtitle_style を渡すと、これらの既定値を辞書キー（同名）で個別に上書きできる
    （例: subtitle_style={"size": 44, "y": 0.85, "border": 2}）。
    subtitle_safe_area は画面比率の余白で、数値（四辺共通）、
    (horizontal, vertical)、(left, top, right, bottom)を受け付ける。指定時は
    drawtextが実測する文字矩形が余白領域以下なら、その位置を領域内へクランプする
    （長文はsubtitle_max_chars/max_linesも併用。subtitle_styleのsafe_areaで上書き可）。
    backend/speaker/volume/pitch/**tts_kwargs は voice() と同じ意味で音声側にのみ作用する
    （backend: "voicevox"/"edge"/"sapi"。None で自動選択）。

    戻り値: Narration(audio, subtitle) （タプルとして (a, t) = narrate(...) も可）。
    scriptvedit.tts が使えない/バックエンド未使用可時のエラーはvoice()同様に透過する。

    使用例:
        n = narrate("こんにちは、世界", speaker=3)                # VOICEVOX
        n = narrate("こんにちは、世界", backend="edge")           # edge-tts
        # n.audio / n.subtitle、または audio, sub = narrate(...)
    """
    subtitle_content = None
    st = None
    safe_area = None
    if subtitle:
        # 外部TTSを呼ぶ前に新規字幕オプションを検証し、入力ミスで合成コストを
        # 発生させない。formatterも表示文にだけ作用し、読み上げ文は変えない。
        subtitle_content = _prepare_subtitle_text(
            text_content, subtitle_text=subtitle_text,
            subtitle_formatter=subtitle_formatter,
            subtitle_max_chars=subtitle_max_chars,
            subtitle_max_lines=subtitle_max_lines)
        st = dict(subtitle_style or {})
        safe_area = _normalize_subtitle_safe_area(
            st.get("safe_area", subtitle_safe_area))

    try:
        # 属性参照ではなくモジュール直接 import（名前空間注入の影響を受けない）
        _tts_mod = _import_module("scriptvedit.tts")
    except ImportError as e:
        raise ImportError(
            "narrate() には scriptvedit.tts が必要です。"
            "scriptvedit.py と同じディレクトリに配置してください。") from e
    wav = _tts_mod.tts(text_content, backend=backend, speaker=speaker, speed=speed,
                     pitch=pitch, **tts_kwargs)
    dur = _tts_mod.tts_duration(wav)
    # dur<=0（空テキスト等）だと show(0) で current_time が進まず
    # 連続 narrate が同じ開始点に重なるため明示エラーにする。
    if dur <= 0:
        raise ValueError(
            f"narrate: 合成音声の長さが0以下でした（dur={dur}）。"
            f"text_content が空でないか確認してください: {text_content!r}")

    text_obj = None
    if subtitle:
        text_obj = text(
            subtitle_content,
            x=st.get("x", x), y=st.get("y", y), size=st.get("size", size),
            color=st.get("color", color), font=st.get("font", font),
            box=st.get("box", box), box_color=st.get("box_color", box_color),
            box_border=st.get("box_border", box_border),
            border=st.get("border", border),
            border_color=st.get("border_color", border_color),
            shadow=st.get("shadow", shadow),
            shadow_color=st.get("shadow_color", shadow_color),
            alpha=st.get("alpha", alpha), anchor=st.get("anchor", anchor))
        if safe_area is not None:
            # text()の一般APIと既存キャッシュ署名を変えず、narrate字幕だけに
            # 描画時のsafe-areaクランプを付加する。
            text_obj._text_spec["safe_area"] = safe_area
            # text()生成後に追加した描画条件も合成sourceへ含め、checkpoint等が
            # safe_area違いの字幕を同一素材として再利用しないようにする。
            text_obj.source = _text_synthetic_source(
                f"{text_obj.source}|safe_area={safe_area!r}")
            text_obj._text_spec["synthetic_source"] = text_obj.source
        # current_timeを進めず音声と同じ開始点に配置（音声側で進行させる）
        text_obj.show(dur)

    # text_objより後にaudio_objをobjects列へ追加することで、
    # 「同じ開始時刻→音声側だけがタイムラインを進める」順序を保証する
    audio_obj = Object(wav)
    audio_obj.duration = dur
    if volume != 1.0:
        _require_number("narrate", "volume", volume, 0, None)
        audio_obj.audio_effects.append(again(volume))

    return Narration(audio_obj, text_obj)


def audio_viz(source, *, kind="waves", color="white", size=None, duration=None):
    """音声を showwaves/showspectrum/showcqt で可視化した映像Objectを生成（キャッシュ生成物）。
    kind: 'waves' | 'spectrum' | 'cqt'。"""
    _validate_audio_source("audio_viz", source)
    color = _validate_ffmpeg_color("audio_viz", color)
    if kind not in ("waves", "spectrum", "cqt"):
        hint = _suggest_hint(str(kind), ("waves", "spectrum", "cqt"))
        raise ValueError(
            f"audio_viz: kind は 'waves'/'spectrum'/'cqt': {kind!r}{hint}")
    proj = Project._current
    fps = proj.fps if proj else 30
    dur = duration or _probe_audio_length(source) or 5.0
    if size is not None:
        if not isinstance(size, (tuple, list)) or len(size) != 2:
            raise ValueError(f"audio_viz: size は (w, h) タプル: {size!r}")
        w, h = int(size[0]), int(size[1])
    elif kind == "waves":
        w, h = (proj.width if proj else 1280), 240
    else:
        w, h = (proj.width if proj else 1280), (proj.height if proj else 720)

    if kind == "waves":
        viz = f"showwaves=s={w}x{h}:mode=cline:rate={fps}:colors={color}"
    elif kind == "spectrum":
        viz = f"showspectrum=s={w}x{h}:slide=scroll:fps={fps}"
    else:
        viz = f"showcqt=s={w}x{h}:fps={fps}"

    sigs = ["audio_viz", _source_signature(source),
            f"kind={kind}", f"color={color}", f"size={w}x{h}",
            f"fps={fps}", f"dur={dur}", f"ev={_ENGINE_VER}"]
    key = hashlib.sha256("||".join(sigs).encode()).hexdigest()[:16]
    cache_path = os.path.join(_ARTIFACT_DIR, "aviz", f"{key}.mkv")

    cmd = ["ffmpeg", "-y", "-i", source,
           "-filter_complex", f"[0:a]{viz}[v]", "-map", "[v]",
           "-c:v", "ffv1", "-level", "3", "-pix_fmt", "yuv420p",
           "-t", str(dur), cache_path]
    return _finalize_generated_object(cache_path, cmd, [source], dur)


def beat_sync(audio_source, *, min_bpm=60, max_bpm=200):
    """scriptvedit.beat(numpy/scipyのみのビート検出)をDSLに統合し、拍時刻を返す。

    audio_source: 音声/動画ファイルパス（scriptvedit.beat がffmpegでデコードする）
    min_bpm/max_bpm: scriptvedit.beat.detect_beats に渡すテンポ探索範囲

    戻り値: {"bpm": float, "beats": [秒,...], "onsets": [秒,...], "duration": float}
    （detect_beats() と同じ形式。そのまま snap_times()/beats_to_keyframes()
    に渡せる）

    解析結果は audio_source のFFP + min_bpm/max_bpm をキーに
    __cache__/artifacts/beats/ へJSONキャッシュし、同じ入力の再解析を避ける。
    numpy/scipy が無い場合は導入方法を含む日本語エラーにする。
    """
    if not isinstance(audio_source, str):
        raise TypeError(
            f"beat_sync: audio_source はパス文字列で指定してください: {audio_source!r}")
    if not os.path.exists(audio_source):
        raise FileNotFoundError(
            f"beat_sync: 音声/動画ファイルが見つかりません: {audio_source}")
    try:
        # 属性参照ではなくモジュール直接 import（名前空間注入の影響を受けない）
        _beat_mod = _import_module("scriptvedit.beat")
    except ImportError as e:
        raise ImportError(
            "beat_sync() には numpy/scipy が必要です。\n"
            "`pip install numpy scipy`（または `pip install scriptvedit[beat]`）"
            "を実行してください。"
            f"(元エラー: {e})") from e

    sig = _source_signature(audio_source)
    key_str = (f"{sig}||min_bpm={min_bpm}||max_bpm={max_bpm}||ev={_ENGINE_VER}")
    key = hashlib.sha256(key_str.encode("utf-8")).hexdigest()[:16]
    cache_path = os.path.join(_ARTIFACT_DIR, "beats", f"{key}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            # 破損キャッシュは無視して再解析（self-heal）
            pass

    try:
        result = _beat_mod.detect_beats(
            audio_source, min_bpm=min_bpm, max_bpm=max_bpm)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"beat_sync: ビート検出に失敗しました: {e}") from e

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    # 一時パスは pid + 乱数でユニーク化（並列実行での衝突防止）→ os.replace で原子的に確定
    tmp_path = _unique_tmp_path(cache_path)
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        os.replace(tmp_path, cache_path)
    finally:
        try:
            os.remove(tmp_path)  # 失敗時の残骸掃除（成功時は replace 済みで存在しない）
        except OSError:
            pass
    return result


# --- 遅延解決の相互参照（関数本体からのみ使用: 循環importを避けるため末尾で束縛）---
from scriptvedit.effects.basic import again
from scriptvedit.ffmpeg import _unique_tmp_path
from scriptvedit.media import _finalize_generated_object, _source_signature
from scriptvedit.objects import AudioEffect, Object
from scriptvedit.project import Project
from scriptvedit.state import _ARTIFACT_DIR, _ENGINE_VER, _detect_media_type, _suggest_hint
from scriptvedit.text import _text_synthetic_source, text
from scriptvedit.validate import _require_number, _validate_ffmpeg_color
