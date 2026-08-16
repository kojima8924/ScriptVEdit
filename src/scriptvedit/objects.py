# -*- coding: utf-8 -*-

import os
import math as _math
import builtins as _builtins
import shutil as _shutil


class TransformChain:
    """複数のTransformをまとめたチェーン"""
    def __init__(self, transforms):
        self.transforms = list(transforms)

    def __or__(self, other):
        """TransformChain | Transform/TransformChain → TransformChain"""
        if isinstance(other, Transform):
            return TransformChain(self.transforms + [other])
        if isinstance(other, TransformChain):
            return TransformChain(self.transforms + other.transforms)
        return NotImplemented

    def __invert__(self):
        """~(tf1 | tf2 | tf3) → chain内全opに quality='fast' 付与"""
        new_list = [t._copy(quality="fast") for t in self.transforms]
        return TransformChain(new_list)

    def __pos__(self):
        """+(tf1 | tf2 | tf3) → 末尾opに policy='force'"""
        if not self.transforms:
            raise ValueError("空のTransformChainには + を適用できません")
        new_list = list(self.transforms)
        new_list[-1] = new_list[-1]._copy(policy="force")
        return TransformChain(new_list)

    def __neg__(self):
        """-(tf1 | tf2 | tf3) → 末尾opに policy='off'"""
        if not self.transforms:
            raise ValueError("空のTransformChainには - を適用できません")
        new_list = list(self.transforms)
        new_list[-1] = new_list[-1]._copy(policy="off")
        return TransformChain(new_list)

    def __repr__(self):
        return f"TransformChain({self.transforms})"


class EffectChain:
    """複数のEffectをまとめたチェーン"""
    def __init__(self, effects):
        self.effects = list(effects)

    def __and__(self, other):
        """EffectChain & Effect/EffectChain → EffectChain"""
        if isinstance(other, Effect):
            return EffectChain(self.effects + [other])
        if isinstance(other, EffectChain):
            return EffectChain(self.effects + other.effects)
        return NotImplemented

    def __invert__(self):
        """~(eff1 & eff2) → chain内全opに quality='fast' 付与"""
        new_list = [e._copy(quality="fast") for e in self.effects]
        return EffectChain(new_list)

    def __pos__(self):
        """+(eff1 & eff2) → 末尾opに policy='force'"""
        if not self.effects:
            raise ValueError("空のEffectChainには + を適用できません")
        new_list = list(self.effects)
        new_list[-1] = new_list[-1]._copy(policy="force")
        return EffectChain(new_list)

    def __neg__(self):
        """-(eff1 & eff2) → 末尾opに policy='off'"""
        if not self.effects:
            raise ValueError("空のEffectChainには - を適用できません")
        new_list = list(self.effects)
        new_list[-1] = new_list[-1]._copy(policy="off")
        return EffectChain(new_list)

    def __repr__(self):
        return f"EffectChain({self.effects})"


class AudioEffect:
    """音声エフェクト（again, afade, adelete, atrim, atempo等）"""
    def __init__(self, name, *, quality="final", **params):
        self.name = name
        self.params = params
        self.quality = quality

    def _copy(self, **overrides):
        """属性をコピーした新AudioEffectを返す"""
        kw = dict(quality=self.quality, **self.params)
        kw.update(overrides)
        return AudioEffect(self.name, **kw)

    def __and__(self, other):
        if isinstance(other, AudioEffect):
            return AudioEffectChain([self, other])
        if isinstance(other, AudioEffectChain):
            return AudioEffectChain([self] + other.effects)
        return NotImplemented

    def __invert__(self):
        """~op → 軽い代替処理を要求する品質ヒント"""
        return self._copy(quality="fast")

    def __repr__(self):
        return f"AudioEffect({self.name}, {self.params})"


class AudioEffectChain:
    """複数のAudioEffectをまとめたチェーン"""
    def __init__(self, effects):
        self.effects = list(effects)

    def __and__(self, other):
        if isinstance(other, AudioEffect):
            return AudioEffectChain(self.effects + [other])
        if isinstance(other, AudioEffectChain):
            return AudioEffectChain(self.effects + other.effects)
        return NotImplemented

    def __invert__(self):
        """~chain → 全opへ軽い代替処理を要求する品質ヒント"""
        return AudioEffectChain(
            [effect._copy(quality="fast") for effect in self.effects])

    def __repr__(self):
        return f"AudioEffectChain({self.effects})"


class Transform:
    def __init__(self, name, *, policy="auto", quality="final", **params):
        self.name = name
        self.params = params
        self.policy = policy
        self.quality = quality

    def _copy(self, **overrides):
        """属性をコピーした新Transformを返す"""
        kw = dict(policy=self.policy, quality=self.quality, **self.params)
        kw.update(overrides)
        return Transform(self.name, **kw)

    def __or__(self, other):
        """Transform | Transform/TransformChain → TransformChain"""
        if isinstance(other, Transform):
            return TransformChain([self, other])
        if isinstance(other, TransformChain):
            return TransformChain([self] + other.transforms)
        return NotImplemented

    def __invert__(self):
        """~op → quality='fast'"""
        return self._copy(quality="fast")

    def __pos__(self):
        """+op → policy='force'"""
        return self._copy(policy="force")

    def __neg__(self):
        """-op → policy='off'"""
        return self._copy(policy="off")

    def __repr__(self):
        return f"Transform({self.name}, {self.params})"


class Effect:
    def __init__(self, name, *, policy="auto", quality="final", **params):
        self.name = name
        self.params = params
        self.policy = policy
        self.quality = quality

    def _copy(self, **overrides):
        """属性をコピーした新Effectを返す"""
        kw = dict(policy=self.policy, quality=self.quality, **self.params)
        kw.update(overrides)
        new = Effect(self.name, **kw)
        # Effect ファクトリが付与した補助情報も保持する。
        # morph/assemble の素材参照や path 式が落ちると、~ の
        # 品質ヒントだけで通常処理と内容が変わってしまう。
        core = {"name", "params", "policy", "quality"}
        for key, value in self.__dict__.items():
            if key not in core:
                setattr(new, key, value)
        return new

    def __and__(self, other):
        """Effect & Effect/EffectChain → EffectChain"""
        if isinstance(other, Effect):
            return EffectChain([self, other])
        if isinstance(other, EffectChain):
            return EffectChain([self] + other.effects)
        return NotImplemented

    def __invert__(self):
        """~op → quality='fast'"""
        return self._copy(quality="fast")

    def __pos__(self):
        """+op → policy='force'"""
        return self._copy(policy="force")

    def __neg__(self):
        """-op → policy='off'"""
        return self._copy(policy="off")

    def __repr__(self):
        return f"Effect({self.name}, {self.params})"


_WEB_KWARGS = {"duration", "size", "fps", "data", "name", "debug_frames", "deps"}

# slide(): web Objectの_web_dataに埋め込むページ切替キー（内部規約・非公開API）
_SLIDE_PAGE_KEY = "__svt_slide_page__"


def _validate_web_name(name):
    """web Object の name を単一のディレクトリ名成分に制限する。

    name はキャッシュディレクトリ（__cache__/webclip/<name>_frames と
    __cache__/artifacts/web/<name>/）のパス成分になり、生成前に rmtree される。
    パス区切り・ドライブ・相対参照を許すとキャッシュ外の既存ディレクトリを
    削除・上書きできてしまうため、構築時に拒否する。
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"web Object の name は空でない文字列が必要です: {name!r}")
    bad = set('/\\:\0') | {os.sep}
    if os.altsep:
        bad.add(os.altsep)
    if any(ch in name for ch in bad) or name in (".", "..") \
            or os.path.basename(name) != name:
        raise ValueError(
            f"web Object の name にパス区切り・ドライブ・相対参照は使えません: {name!r}")
    return name


def _web_frames_dir(name, *, unique=False):
    """webフレーム作業ディレクトリを返す（__cache__/webclip 配下を保証）。

    削除(rmtree)対象になるため、realpath 解決後もキャッシュルート内に
    収まることを最終確認する（symlink 等で外へ出る経路の防御）。

    unique=True は pid+uuid 付きのディレクトリを返す。実生成では同名 web
    Object の並列レンダが相互にフレームを削除・混入しないようこちらを使う
    （issue #13 P2-15）。既定の決定的パスは dry_run のコマンド組み立て用
    （スナップショットの安定性のため）。
    """
    cache_dir = os.path.join(_CACHE_DIR, "webclip")
    suffix = ""
    if unique:
        import uuid as _uuid
        suffix = f"_{os.getpid()}_{_uuid.uuid4().hex[:8]}"
    frames_dir = os.path.join(
        cache_dir, f"{_validate_web_name(name)}_frames{suffix}")
    real_root = os.path.realpath(cache_dir)
    real = os.path.realpath(frames_dir)
    try:
        contained = os.path.commonpath([real_root, real]) == real_root
    except ValueError:
        contained = False
    if not contained or real == real_root:
        raise ValueError(f"web Object の name が不正です: {name!r}")
    return frames_dir


def _web_frame_count(duration, fps):
    """Web Objectのキャプチャフレーム数を返す（短尺でも最低1フレームを保証）。

    旧実装の int(dur * fps) は duration < 1/fps で 0 フレームになり、
    ffmpeg が入力なしで失敗していた。切り上げ + 最低1で全尺をカバーする。
    """
    if not isinstance(duration, (int, float)) or duration <= 0:
        raise ValueError(
            f"web Object の duration は正の数値が必要です: {duration!r}")
    if not isinstance(fps, (int, float)) or fps <= 0:
        raise ValueError(f"web Object の fps は正の数値が必要です: {fps!r}")
    return _builtins.max(1, int(_math.ceil(float(duration) * float(fps))))


class Object:
    # 生成元のレイヤーファイル名（Project._stamp_layer_origin が刻む）。
    # None のままレンダに入ったアイテムは「レイヤーファイルの外で作られた」＝
    # render() のレイヤー再実行で無言破棄される、という意味なので
    # Project._begin_render_pass が ValueError で弾く。
    # text() 等 __new__ 経由で組み立てるファクトリのためにクラス属性で既定を持つ。
    _defined_in_layer = None

    def __init__(self, source, **kwargs):
        self.source = source
        self.transforms = []
        self.effects = []
        self.audio_effects = []
        self.duration = None
        self._duration_auto = False
        self.start_time = 0
        self.priority = 0
        self.media_type = _detect_media_type(source)
        self._until_anchor = None
        self._until_offset = 0.0
        self._anchor_name = None
        self._advance = True
        self._fixed_start = None   # `obj @ t` の絶対配置（秒 or アンカー名）
        self._start_after = None   # `a >> b` の直後連結（先行アイテム参照）
        self._priority_override = None
        self._video_deleted = False
        self._audio_deleted = False
        # web専用属性（常に初期化）
        self._web_source = None
        self._web_size = None
        self._web_fps = None
        self._web_data = {}
        self._web_name = None
        self._web_debug_frames = False
        self._web_deps = []

        # web Object のバリデーションと属性設定
        if self.media_type == "web":
            unknown = set(kwargs.keys()) - _WEB_KWARGS
            if unknown:
                hint = _suggest_hint(sorted(unknown)[0], _WEB_KWARGS)
                raise TypeError(
                    f"不明なキーワード引数: {', '.join(sorted(unknown))}。"
                    f"使用可能: {', '.join(sorted(_WEB_KWARGS))}{hint}")
            if "duration" not in kwargs:
                raise ValueError("web Object には duration が必須です")
            if "size" not in kwargs:
                raise ValueError("web Object には size が必須です")
            self._web_source = source
            self.duration = kwargs["duration"]
            if not isinstance(self.duration, (int, float)) or \
                    isinstance(self.duration, bool) or self.duration <= 0:
                raise ValueError(
                    f"web Object の duration は正の数値が必要です: {self.duration!r}")
            self._web_size = kwargs["size"]
            self._web_fps = kwargs.get("fps")
            self._web_data = kwargs.get("data", {})
            self._web_name = _validate_web_name(
                kwargs.get("name")
                or os.path.splitext(os.path.basename(source))[0])
            self._web_debug_frames = kwargs.get("debug_frames", False)
            self._web_deps = kwargs.get("deps", [])
        elif kwargs:
            raise TypeError(
                f"キーワード引数は web Object (.html/.htm) 専用です: "
                f"{', '.join(sorted(kwargs.keys()))}")

        # has_video / has_audio のデフォルト
        if self.media_type == "image":
            self._has_video = True
            self._has_audio = False
        elif self.media_type == "audio":
            self._has_video = False
            self._has_audio = True
        elif self.media_type == "web":
            self._has_video = True
            self._has_audio = False
        else:  # video
            self._has_video = True
            self._has_audio = None  # 未判定→ffprobeで解決
        # 現在のProjectに自動登録
        if Project._current is not None:
            Project._current.objects.append(self)

    @property
    def has_video(self):
        if self._video_deleted:
            return False
        return self._has_video if self._has_video is not None else True

    @property
    def has_audio(self):
        if self._audio_deleted:
            return False
        if self._has_audio is None:
            proj = Project._current
            if proj:
                info = proj._probe_media(self.source)
                if info:
                    self._has_audio = info.get("has_audio", False)
                    return self._has_audio
            return False  # probe不可→音声なしと推定（安全側）
        return self._has_audio

    def _ensure_registered(self):
        """Project._current に未登録なら現在位置へ再登録する（重複登録はしない）。

        compute() は自身を「焼いた素材」へ変異させる際に Project.objects から
        除外する。その後 time()/show()/until()/@ で再配置されたら、呼び出し
        時点のタイムライン位置へ戻す（監査 issue #14 P1: README の
        compute()→time() の例が空タイムラインになる問題の修正）。
        """
        proj = Project._current
        if proj is None:
            return
        if not any(o is self for o in proj.objects):
            proj.objects.append(self)

    def time(self, duration=None, *, name=None):
        """表示時間を設定。省略時は加工後長(length())で自動決定（layer exec後に確定）"""
        if duration is None:
            if self.media_type in ("image", "text"):
                raise TypeError(
                    "画像/テキストには time() 省略は使えません。time(seconds) を指定してください。")
            self.duration = None
            self._duration_auto = True
        else:
            _require_time("time", "duration", duration, lo=0, lo_exclusive=True)
            self.duration = duration
            self._duration_auto = False
        if name is not None:
            # 生成アンカー（X.start / X.end）も明示 anchor() と同じ重複管理に
            # 載せる（別レイヤーでの同名定義は last-write-wins にせずエラー）
            proj = Project._current
            if proj is not None:
                _register_anchor_owner(proj, f"{name}.start")
                _register_anchor_owner(proj, f"{name}.end")
            self._anchor_name = name
        self._ensure_registered()
        return self

    def until(self, name, offset=0.0):
        """durationをアンカー時刻まで伸長"""
        _require_time("until", "offset", offset)
        self._until_anchor = name
        self._until_offset = offset
        self._ensure_registered()
        return self

    def show(self, duration, *, priority=None):
        """current_timeを進めずに表示。start=current_time, duration=指定値"""
        _require_time("show", "duration", duration, lo=0, lo_exclusive=True)
        self.duration = duration
        self._advance = False
        if priority is not None:
            self._priority_override = priority
        self._ensure_registered()
        return self

    def show_until(self, name, offset=0.0, *, priority=None):
        """current_timeを進めずにアンカーまで表示"""
        _require_time("show_until", "offset", offset)
        self._until_anchor = name
        self._until_offset = offset
        self._advance = False
        if priority is not None:
            self._priority_override = priority
        self._ensure_registered()
        return self

    # --- DSL糖衣: タイムライン3点セット（スライス / @ / >>）------------------

    def __getitem__(self, key):
        """`obj[2:5]`: 素材時間の切り出し（イン点2秒〜アウト点5秒）。

        軸の区別: スライスは**素材時間**（trim/atrim）、`@` は**タイムライン時間**。
        `obj[3:8] @ 12` は「素材の3〜8秒をタイムライン12秒に置く」。
        表示尺は切り出し長（end-start）が既定になる（time()で上書き可）。
        負値は素材末尾からの相対（length()で解決。probe可能な素材のみ）。
        step（`obj[::2]`）は「2倍速」と「間引き」で曖昧なため不可（speed(2)を使う）。
        """
        if not isinstance(key, slice):
            raise TypeError(
                f"Object のインデックスは時間スライスのみです: obj[start:end]"
                f"（例: obj[2:5] = 2〜5秒を切り出し）。指定値: {key!r}")
        if key.step is not None:
            raise ValueError(
                "obj[a:b:step] の step は未対応です（2倍速なら speed(2)、"
                "コマ落としなら pixelize 等のEffectを使ってください）")
        if self.media_type in ("image", "text"):
            raise TypeError(
                f"{self.media_type} には素材時間がないためスライスできません。"
                f"表示尺は time(seconds) で指定してください。")
        start = 0.0 if key.start is None else float(key.start)
        end = None if key.stop is None else float(key.stop)
        # 負値は素材末尾からの相対（probe が必要）
        if start < 0 or (end is not None and end < 0):
            total = self.length()
            if start < 0:
                start = _builtins.max(0.0, total + start)
            if end is not None and end < 0:
                end = total + end
        if end is not None and end <= start:
            raise ValueError(
                f"スライスの範囲が不正です: [{key.start!r}:{key.stop!r}] "
                f"→ start={start:g}s, end={end:g}s（end > start が必要）")
        dur = None if end is None else end - start
        if self._has_video is not False and not self._video_deleted:
            self._append_effect(Effect("trim", start=start, duration=dur))
        if self._has_audio is not False and not self._audio_deleted:
            self.audio_effects.append(
                AudioEffect("atrim", start=start, duration=dur))
        if dur is not None:
            self.duration = dur
            self._duration_auto = False
        else:
            # アウト点省略（obj[3:]）: 素材末尾まで → 尺は自動確定に委ねる
            self.duration = None
            self._duration_auto = True
        return self

    def __matmul__(self, at):
        """`obj @ 12` / `obj @ "intro.end"`: タイムライン上の絶対配置（浮動）。

        順次配置のカーソルは進めない（show() と同じ非進行）。数値は秒、
        文字列はアンカー名（`"名前.start"` / `"名前.end"` / `"scene:名前"` 等）。
        """
        if isinstance(at, bool) or not isinstance(at, (int, float, str)):
            raise TypeError(
                f"@ の右辺は秒（数値）かアンカー名（文字列）です: {at!r}")
        if isinstance(at, (int, float)):
            _require_time("@", "時刻", at, lo=0)
        self._fixed_start = at
        self._start_after = None
        self._advance = False
        self._ensure_registered()
        return self

    def __rshift__(self, other):
        """`a >> b`: b を a の終了直後に開始する（`a >> pause.time(0.5) >> b` 可）"""
        return _link_after(self, other)

    def __mul__(self, count):
        """`obj * 3`: 3回連続再生（映像はloopフィルタ、音声はaloop）。

        表示尺は「現時点の実効尺 × 回数」になる。繰り返す区間の尺は
        この時点で確定させて効果に焼き込む（後から time() で上書きしても
        繰り返し境界は変わらない）。
        """
        if isinstance(count, bool) or not isinstance(count, int):
            raise TypeError(
                f"* の右辺は繰り返し回数（2以上の整数）です: {count!r}"
                f"（音量なら again(), 速度なら speed() を使ってください）")
        if count < 1:
            raise ValueError(f"* の繰り返し回数は1以上が必要です: {count}")
        if count == 1:
            return self
        if self.media_type in ("image", "text"):
            raise TypeError(
                f"{self.media_type} には素材尺がないため * は使えません。"
                f"time(seconds) で表示尺を指定してください。")
        segment = self.length()  # 繰り返す区間の実尺（この時点で確定）
        if self._has_video is not False and not self._video_deleted:
            self._append_effect(
                Effect("repeat", count=count, segment=segment))
        if self._has_audio is not False and not self._audio_deleted:
            self.audio_effects.append(
                AudioEffect("arepeat", count=count, segment=segment))
        self.duration = segment * count
        self._duration_auto = False
        return self

    __rmul__ = __mul__

    def __neg__(self):
        """`-obj`: 逆再生（reverse() の糖衣。音声は反転されない）"""
        self._append_effect(Effect("reverse"))
        return self

    def _append_transform(self, t):
        """Transform追加の共通経路（Effect適用後のTransformを明示拒否）。

        適用順は「全Transform→全Effect」のカテゴリ順が正式仕様
        （transforms/effects を別配列で保持するため、記述順の交互適用は
        再現できない。監査 issue #14 P1）。Effect の後に Transform を書くと
        記述順と実行順が食い違うため、受理せずエラーにする。
        """
        if self.effects:
            names = ", ".join(e.name for e in self.effects)
            raise ValueError(
                f"Transform '{t.name}' は Effect より先に適用してください"
                f"（適用済み Effect: {names}）。適用順は「全Transform→全Effect」の"
                f"カテゴリ順です。Effect 適用後の静的変形が必要な場合は "
                f"compute() で素材化してから Transform を適用してください。")
        self.transforms.append(t)

    def _append_effect(self, e):
        """Effect追加の共通経路（delete処理・時間系の検証・speedの音声追従）"""
        if e.name == "delete":
            self._video_deleted = True
            return
        if e.name in _TIME_LIVE_EFFECTS and self.media_type in ("image", "text", "web"):
            raise ValueError(
                f"{e.name}: 時間操作Effectは動画素材にのみ適用できます"
                f"（{self.media_type} には適用不可）: {self.source}")
        self.effects.append(e)
        if e.name == "speed" and not self._audio_deleted:
            # 音声付き動画のテンポを自動追従させる（atempo）。
            # length()での二重計上を防ぐためフラグを付ける
            ae = AudioEffect("atempo", rate=e.params.get("factor", 1.0))
            ae._auto_from_speed = True
            self.audio_effects.append(ae)

    # 1つのObjectに複数適用しても2個目以降がfiltergraphへ反映されない
    # AudioEffect（構築側が next(...) で1個だけ選ぶ）。黙殺せず適用時点で拒否する
    _SINGLE_AUDIO_EFFECTS = ("duck_under", "loop")

    def _append_audio_effect(self, ae):
        """AudioEffect追加の共通経路（adelete処理・重複不可Effectの明示拒否）"""
        if ae.name == "adelete":
            self._audio_deleted = True
            return
        if ae.name in self._SINGLE_AUDIO_EFFECTS and any(
                e.name == ae.name for e in self.audio_effects):
            raise ValueError(
                f"{ae.name}: 1つのObjectに {ae.name} は1回しか適用できません"
                f"（2個目以降はフィルタに反映されず黙って無視されるため拒否します）。"
                f"適用済みの {ae.name} を1つに統合してください。")
        self.audio_effects.append(ae)

    def __le__(self, rhs):
        """<= 演算子: Transform/Effect/AudioEffect等を適用"""
        if isinstance(rhs, Transform):
            self._append_transform(rhs)
        elif isinstance(rhs, TransformChain):
            for t in rhs.transforms:
                self._append_transform(t)
        elif isinstance(rhs, Effect):
            self._append_effect(rhs)
        elif isinstance(rhs, EffectChain):
            for e in rhs.effects:
                self._append_effect(e)
        elif isinstance(rhs, AudioEffect):
            self._append_audio_effect(rhs)
        elif isinstance(rhs, AudioEffectChain):
            for e in rhs.effects:
                self._append_audio_effect(e)
        else:
            raise TypeError(f"Object <= に渡せるのは Transform/Effect/AudioEffect 等のみ: {type(rhs)}")
        return self

    def _apply_compute_result(self, cache_path):
        """compute() の生成物へ自身をその場で変異させる（「焼いた素材」化）。

        source を生成物パスへ差し替え、焼き込み済みの transforms/effects を
        クリアする。生成物（PNG / FFV1 mkv）はどちらも音声 stream を持たない
        ため、音声状態も生成物に一致させる（audio_effects を残すと実体のない
        音声を map してしまう。監査 issue #14 P1）。
        """
        self.source = cache_path
        self.media_type = _detect_media_type(cache_path)
        self.transforms = []
        self.effects = []
        self.audio_effects = []
        self._has_audio = False
        return self

    def compute(self, duration=None):
        """タイムライン外で素材を生成。PNG(静止) or WebM(動画)を返す

        自身を生成物（焼いた素材）へその場で変異させて返す。Project からは
        一旦除外されるが、time()/show()/until()/@ で再配置すればその時点の
        位置へ再登録される（README の compute()→time() の契約）。
        """
        # live effects チェック（時間系 speed/reverse/freeze_frame は
        # _build_compute_video_cmd の前処理フィルタでベイクできるため許可）
        for e in self.effects:
            if e.name in ("move", "delete", "shake", "blend_mode"):
                raise ValueError(
                    f"compute() では live Effect '{e.name}' は使用できません。"
                    f"bakeable Effect のみ使用可能です。")
        # 静止画（duration なし）の生成コマンドは Effect を適用しない
        # （u 正規化に尺が必要なため）。黙って捨てると fade 等が消えるので明示拒否
        if duration is None and self.effects:
            names = ", ".join(e.name for e in self.effects)
            raise ValueError(
                f"duration なしの compute() は静止画を生成するため "
                f"Effect ({names}) を焼き込めません。"
                f"Effect を焼くには duration を指定して動画として compute してください。")
        # Project.objects から除外
        proj = Project._current
        if proj is not None and self in proj.objects:
            proj.objects.remove(self)
        # キャッシュパス計算
        cache_path = self._compute_cache_path(duration)
        # 差し替え前の元素材パスを保持（レイヤーキャッシュの依存記録で
        # 導出キャッシュパスではなく元素材の変更を検知できるようにする）
        self._origin_sources = (getattr(self, '_origin_sources', None) or []) + [self.source]
        # ベイク尺を保持（差し替え後の未生成予定パスへのprobe fallback防止）
        if duration:
            self._resolved_length = duration
        # plan pass: source差し替えのみ
        if proj is not None and proj._mode == "plan":
            return self._apply_compute_result(cache_path)
        # キャッシュ存在チェック
        if os.path.exists(cache_path):
            return self._apply_compute_result(cache_path)
        # 生成コマンド構築
        if duration is None:
            cmd = self._build_compute_image_cmd(cache_path)
        else:
            cmd = self._build_compute_video_cmd(cache_path, duration)
        # dry_run: コマンドを記録して生成スキップ
        if proj is not None and getattr(proj, '_dry_run', False):
            proj._pending_compute_cmds[cache_path] = cmd
            return self._apply_compute_result(cache_path)
        # 実生成
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        _run_ffmpeg_to_cache(cmd, cache_path, timeout=600)
        return self._apply_compute_result(cache_path)

    @staticmethod
    def from_project(sub_project, *, cache="auto"):
        """ネストコンポジション（プリコンポーズ）: サブProjectを透過webm素材化して
        1つのObjectとして親タイムラインに配置する。

        sub_project: layer() 登録済みの Project。render(alpha=True) 機構で
        透過webmキャッシュ生成物を作り、そのwebmをsourceとするObjectを返す。
        キャッシュ鍵は configure + レイヤーファイルFFP群 + レイヤーが参照する
        素材FFP群から導出する（素材更新で自動再生成）。dry_run 対応。
        cache: 'auto'（キャッシュがあれば再利用）/ 'force'（常に再生成）。
        """
        if not isinstance(sub_project, Project):
            raise TypeError(
                f"from_project: sub_project には Project を指定してください: "
                f"{type(sub_project)}")
        if cache not in ("auto", "force"):
            hint = _suggest_hint(cache, ("auto", "force"))
            raise ValueError(
                f"from_project: cache は 'auto' か 'force' のいずれか: {cache!r}{hint}")
        # 親 = 現在レイヤーを実行中のProject（sub = Project() が _current を
        # 奪うため、_exec_stack から特定する）。レイヤー外では復元先のみ保持
        parent = Project._exec_stack[-1] if Project._exec_stack else None
        if parent is None:
            raise ValueError(
                "from_project: レイヤー実行中の親Projectがありません。"
                "Object.from_project() は Project.layer() から読み込まれる"
                "レイヤーファイル内で呼び出してください。")
        if sub_project is parent:
            raise ValueError("from_project: 親Project自身は指定できません")
        if not sub_project._layer_specs:
            raise ValueError(
                "from_project: sub_project に layer() が登録されていません。"
                "sub.layer('xxx.py') でレイヤーを登録してから渡してください。")
        # サブProjectをdry_runで解決し、総尺・依存素材を確定する
        # （Project._current が切り替わるため必ず親へ復元する）
        try:
            sub_project.render("__from_project_probe__.webm",
                               dry_run=True, alpha=True)
        finally:
            Project._current = parent
        total = sub_project.duration

        # 署名: configure + レイヤーファイルFFP群 + レイヤー参照素材FFP群
        sigs = ["from_project",
                f"cfg={sub_project.width}x{sub_project.height}"
                f"@{sub_project.fps}|bg={sub_project.background_color}"
                f"|dur={total}"]
        layer_files = []
        for spec in sub_project._layer_specs:
            sigs.append(
                f"layer={_src_signature(spec['filename'])}|p={spec['priority']}")
            layer_files.append(spec["filename"])
        dep_sources = []
        for srcs in sub_project._layer_sources.values():
            dep_sources.extend(srcs)
        dep_sources = sorted(set(dep_sources))
        for src in dep_sources:
            sigs.append(f"dep={_src_signature(src)}")
        # 登録済み全プラグインのコード指紋を混ぜる（サブレイヤーがどのプラグインを
        # 呼ぶかは静的に判別できないため全登録分。プラグインファイルだけ変更した
        # 場合も cache='auto' が再生成する。issue #16 P1）
        for pname in sorted(_EFFECT_PLUGINS):
            sigs.append(f"plugin={pname}:{_EFFECT_PLUGINS[pname].code_ffp}")
        # サブProject全体の出力へ影響する音声設定（normalize_audio）も署名対象
        sigs.append(f"loudnorm={sub_project._loudnorm_target}")
        loudnorm_options = getattr(sub_project, "_loudnorm_options", None)
        if loudnorm_options is not None:
            sigs.append(f"loudnorm_options={loudnorm_options}")
        # duck_under のsidechain padや最終音声グラフはレイヤー/素材FFPに
        # 現れない。音声グラフ変更後に旧subproject WebMを再利用しないための
        # 局所バージョン（高コストなWeb/画像キャッシュ全体は無効化しない）。
        sigs.append("audio_graph=2")
        sigs.append(f"ev={_ENGINE_VER}")
        key = _sig_key(sigs)
        cache_path = os.path.join(_ARTIFACT_DIR, "subproject", f"{key}.webm")

        parent_mode = getattr(parent, "_mode", None) if parent else None
        parent_dry = bool(getattr(parent, "_dry_run", False)) if parent else False
        if parent is not None and parent_mode == "plan":
            pass  # plan pass: 生成スキップ（尺解決のみ）
        elif cache == "auto" and os.path.exists(cache_path):
            pass  # キャッシュ命中
        elif parent_dry:
            # dry_run: サブProjectの生成コマンド（dict/list）をpendingに記録
            try:
                sub_cmd = sub_project.render(cache_path, dry_run=True, alpha=True)
            finally:
                Project._current = parent
            parent._pending_compute_cmds[cache_path] = sub_cmd
        else:
            # 実生成: 一時パスへレンダし成功時のみ確定（アトミック書き込み）。
            # 一時パスは pid+uuid でユニーク化（固定 .tmp だと同一鍵の並列生成で
            # 相互に書きかけを壊す。issue #13 P2-15）
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            tmp_path = _unique_tmp_path(cache_path)
            print(f"サブプロジェクト生成: {cache_path}")
            try:
                sub_project.render(tmp_path, alpha=True)
                os.replace(tmp_path, cache_path)
                with _GEN_COUNTER_LOCK:
                    _GEN_COUNTER[0] += 1
            finally:
                Project._current = parent
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        # 親レイヤーの依存として登録（レイヤーキャッシュの鮮度検証に載せる）
        if parent is not None and parent._current_layer_file:
            parent._extra_layer_deps.setdefault(
                parent._current_layer_file, []).extend(layer_files + dep_sources)
        obj = Object(cache_path)
        obj._origin_sources = list(layer_files) + list(dep_sources)
        obj._resolved_length = total
        # 音声有無: dry_run解決済みのサブオブジェクトから確定（未生成キャッシュの
        # probe不能でFalse固定になるのを防ぐ）
        obj._has_audio = any(
            isinstance(o, Object) and o.has_audio for o in sub_project.objects)
        return obj

    def _compute_cache_path(self, duration=None):
        """compute用キャッシュパスを計算"""
        ops = _build_unified_ops(self)
        sigs = []
        # 署名は _src_signature に一本化する（キャッシュ生成物＝パス署名 /
        # 素材＝内容指紋）。生の _file_fingerprint だと self.source が
        # __cache__ 配下の生成物（2段目の compute、from_project の subproject
        # webm）のとき、dry_run では未生成で OSError→パス署名、実レンダでは
        # 内容指紋、と鍵が食い違う（CLAUDE.md §5 が潰した罠）。
        sigs.append(_src_signature(self.source))
        sigs.append(_op_prefix_fingerprint(ops))
        quality = _ops_effective_quality(ops)
        sigs.append(f"q={quality}")
        # Project解像度/fpsを鍵に含める（blur_background_fill 等の一部Effectは
        # Projectのwidth/heightでフィルタが変わるため、解像度違いの出力が
        # 同一pathへ衝突していた。issue #16 P1。checkpoint側も同じ書式で対応）
        proj = Project._current
        if proj is not None:
            sigs.append(f"pctx={proj.width}x{proj.height}@{proj.fps}")
        else:
            sigs.append("pctx=1280x720@30")
        sigs.append(f"ev={_ENGINE_VER}")
        if duration is not None:
            fps = proj.fps if proj else 30
            sigs.append(f"dur={duration}")
            sigs.append(f"fps={fps}")
            # 中間ベイクのpix_fmt世代（.mkvを焼く動画computeのみ。静止画PNGは無関係）
            sigs.append(f"bpf={_BAKE_PIXFMT_VER}")
        key = _sig_key(sigs)
        # バケットも _src_bucket に統一（生パス由来だとリポジトリを移動しただけで
        # compute キャッシュが全ミスする＝移植性が壊れる）
        src_hash = _src_bucket(self.source)
        ext = ".mkv" if duration is not None else ".png"
        return os.path.join(_ARTIFACT_DIR, "compute", src_hash, f"{key}{ext}")

    def _build_compute_image_cmd(self, cache_path):
        """compute静止画: Transform適用→PNG"""
        temp = Object.__new__(Object)
        temp.source = self.source
        temp.transforms = list(self.transforms)
        temp.effects = []
        filters = _build_transform_filters(temp)
        cmd = ["ffmpeg", "-y", "-i", self.source]
        if filters:
            cmd.extend(["-vf", ",".join(filters)])
        cmd.extend(["-frames:v", "1", "-pix_fmt", "rgba", cache_path])
        return cmd

    def _build_compute_video_cmd(self, cache_path, duration):
        """compute動画: Transform+Effect適用→WebM VP9 alpha"""
        proj = Project._current
        fps = proj.fps if proj else 30
        temp = Object.__new__(Object)
        temp.source = self.source
        temp.transforms = list(self.transforms)
        temp.effects = list(self.effects)
        temp.media_type = self.media_type
        base_dims = _get_base_dimensions(temp)
        filters = _build_transform_filters(temp)
        pre_filters = _build_video_pre_filters(temp)
        filters = pre_filters + filters
        eff_filters, _ = _build_effect_filters(temp, 0, duration, base_dims=base_dims)
        filters.extend(eff_filters)
        cmd = ["ffmpeg", "-y"]
        cmd.extend(_decoder_input_args(self.source, self.media_type, fps))
        if filters:
            cmd.extend(["-vf", ",".join(filters)])
        cmd.extend([
            "-c:v", "ffv1", "-level", "3",
            # 色変換を挟まない中間形式（理由と実測値は state.py の _BAKE_PIX_FMT）
            "-pix_fmt", _BAKE_PIX_FMT,
            "-t", str(duration), cache_path,
        ])
        return cmd

    def length(self):
        """加工後の再生時間を返す（ffprobe + 時間影響エフェクト反映）"""
        if self.media_type in ("image", "text"):
            raise TypeError("画像/テキストにはlength()を使えません。動画/音声のみ対応です。")
        if self.media_type == "web":
            if self.duration is None:
                raise TypeError("web Objectにはduration引数が必須です")
            return self.duration
        proj = Project._current
        if proj is None:
            raise RuntimeError("length()にはアクティブなProjectが必要です")
        info = proj._probe_media(self.source)
        if info is None or info.get("duration") is None:
            raise FileNotFoundError(
                f"メディアの長さを取得できません: {self.source}")
        base_dur = info["duration"]
        # 映像側・音声側の実効尺を別々に畳み込み、有効な stream の最大を返す。
        # delete()/adelete() で消した側や存在しない stream は計算から除外する
        # （音声だけ atrim(1) しても未編集の映像尺が縮まない）。
        # 音声有無は今回の probe 結果をその場で使う。has_audio プロパティ経由で
        # _has_audio をキャッシュすると、checkpoint 差し替え後（音声なし中間
        # ファイル）にも True が残り、実体のない [1:a] を map してしまう。
        if self._audio_deleted:
            audio_active = False
        elif self._has_audio is not None:
            audio_active = self._has_audio
        else:
            audio_active = bool(info.get("has_audio", False))
        durs = []
        if self.has_video:
            # 映像 時間系（trim/speed/freeze_frame/repeat）を並び順に反映
            durs.append(_fold_time_effects(base_dur, self.effects))
        if audio_active:
            # 音声 atrim/atempo/arepeat を並び順に反映
            # （speed()由来の自動atempoも音声の実尺を実際に変えるため含める）
            durs.append(_fold_time_effects(
                base_dur, self.audio_effects, audio=True))
        if not durs:
            # 両 stream とも削除/不在 → 元尺を返す（0 は返さない）
            return base_dur
        return _builtins.max(durs)

    def _build_web_cmd(self, project, webm_path=None, frames_dir=None):
        """webクリップ用ffmpegコマンド

        frames_dir 省略時は決定的パス（dry_run のコマンド組み立て用）。
        実生成では _ensure_web_objects がユニーク化したパスを渡す。
        """
        if webm_path is None:
            webm_path = _web_cache_path(self, project)
        if frames_dir is None:
            frames_dir = _web_frames_dir(self._web_name)
        frames_pattern = os.path.join(frames_dir, "frame_%05d.png")
        capture = self._web_capture_spec(project)
        fps = capture["fps"]
        dur = self.duration
        # 通常の本番全尺キャッシュは従来コマンドを完全に維持する。
        # draft低fpsも全尺なら同じ単純な連番入力でよい。
        if capture["frame_start"] == 0 and capture["frame_end"] == capture["full_frames"]:
            return [
                "ffmpeg", "-y",
                "-framerate", str(fps),
                "-i", frames_pattern,
                "-c:v", "libvpx-vp9",
                "-pix_fmt", "yuva420p",
                "-b:v", "0", "-crf", "30",
                "-auto-alt-ref", "0",
                "-t", str(dur),
                webm_path,
            ]

        # 部分レンダ用キャッシュは必要区間のPNGだけを持つ。透明な全尺背景へ
        # 元のローカル時刻から重ね、Objectのstart/durationやEffectのt基準を
        # 変えずに通常の合成経路へ渡せるWebMへする。
        w, h = self._web_size
        offset = capture["frame_start"] / fps
        graph = (
            f"[1:v]setpts=PTS-STARTPTS+{offset}/TB[wclip];"
            f"[0:v][wclip]overlay=eof_action=pass:repeatlast=0:format=auto,"
            "format=yuva420p[wout]")
        return [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i",
            f"color=c=black@0.0:s={w}x{h}:d={dur}:r={fps},format=rgba",
            "-framerate", str(fps),
            "-i", frames_pattern,
            "-filter_complex", graph,
            "-map", "[wout]",
            "-c:v", "libvpx-vp9",
            "-pix_fmt", "yuva420p",
            "-b:v", "0", "-crf", "30",
            "-auto-alt-ref", "0",
            "-t", str(dur),
            webm_path,
        ]

    def _web_capture_spec(self, project):
        """このレンダで実際に撮影するWebフレーム範囲を返す。

        final全尺は従来どおり。draftは ``configure(draft_web_fps=...)`` で
        screenshot FPSを下げ、部分レンダは表示窓と交差するローカルフレームだけを
        撮影する。trim/terminal Effectは時刻の1:1対応を壊すため安全側で全尺へ戻す。
        """
        # 既存full-final cache keyの表記（30 と 30.0 の差を含む）を維持するため、
        # 元の数値型を保ったまま計算時だけfloat変換されるようにする。
        base_fps = self._web_fps or project.fps
        fps = base_fps
        draft_fps = getattr(project, "draft_web_fps", None)
        if getattr(project, "_draft", False) and draft_fps is not None:
            fps = _builtins.min(base_fps, draft_fps)

        duration = float(self.duration)
        full_frames = _web_frame_count(duration, fps)
        frame_start = 0
        frame_end = full_frames
        visible = True

        unsafe_window_ops = {"trim"} | set(_TERMINAL_FRAME_EFFECTS)
        can_window = not any(
            getattr(effect, "name", None) in unsafe_window_ops
            for effect in getattr(self, "effects", []))
        window = getattr(project, "_render_window", None)
        if window is not None and can_window:
            global_start = float(self.start_time or 0.0)
            global_end = global_start + duration
            window_start, window_end = window
            if window_end is None:
                window_end = project.duration
            local_start = _builtins.max(0.0, float(window_start) - global_start)
            local_end = _builtins.min(
                duration, float(window_end) - global_start)
            visible = local_end > local_start and global_end > float(window_start)
            if visible:
                frame_start = _builtins.max(
                    0, int(_math.floor(local_start * fps)))
                frame_end = _builtins.min(
                    full_frames, int(_math.ceil(local_end * fps)))
                visible = frame_end > frame_start
            else:
                frame_start = frame_end = 0

        return {
            "base_fps": base_fps,
            "fps": fps,
            "full_frames": full_frames,
            "frame_start": frame_start,
            "frame_end": frame_end,
            "visible": visible,
        }

    def _render_web_frames(self, project, frames_dir=None):
        """Playwrightで HTML を連番PNGにキャプチャ"""
        from playwright.sync_api import sync_playwright
        if frames_dir is None:
            frames_dir = _web_frames_dir(self._web_name)
        # 前回実行（debug_frames 残留や中断）の stale フレームが混入すると
        # duration/fps/data 変更後も古い絵が使われるため、生成前に必ず空にする。
        # （ユニーク化された frames_dir では通常空だが、決定的パス経由の
        #   呼び出しにも備える。__cache__/webclip 配下であることは保証済み）
        if os.path.isdir(frames_dir):
            _shutil.rmtree(frames_dir, ignore_errors=True)
        os.makedirs(frames_dir, exist_ok=True)
        w, h = self._web_size
        capture = self._web_capture_spec(project)
        fps = capture["fps"]
        dur = self.duration
        full_frames = capture["full_frames"]
        frame_start = capture["frame_start"]
        frame_end = capture["frame_end"]
        if not capture["visible"]:
            raise RuntimeError("表示窓外のWeb Objectを撮影しようとしました")
        capture_frames = frame_end - frame_start
        html_path = os.path.abspath(self._web_source)
        url = f"file:///{html_path.replace(os.sep, '/')}"

        # slide(): ページ切替規約（_SLIDE_PAGE_KEY）が指定されていれば、
        # renderFrame待機の前にページ切替JSフックを実行する
        slide_page = self._web_data.get(_SLIDE_PAGE_KEY)

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": w, "height": h})
                page.goto(url)
                if slide_page is not None:
                    # window.showSlide(n) があれば呼び出し、無ければ
                    # id="page-N" 要素のみ表示（他のid^="page-"要素は非表示）。
                    # renderFrame未定義ならno-opを注入し、静止スライドとして
                    # 通常のWeb Objectキャプチャ経路をそのまま利用できるようにする。
                    page.evaluate(
                        "(n) => {"
                        "  if (typeof window.showSlide === 'function') {"
                        "    window.showSlide(n);"
                        "  } else {"
                        "    var els = document.querySelectorAll('[id^=\"page-\"]');"
                        "    var target = document.getElementById('page-' + n);"
                        "    if (!target) {"
                        # ゼロ埋めid(page-01等)対応: 数値正規化で一致を探す
                        "      els.forEach(function(el) {"
                        "        var m = el.id.match(/^page-(\\d+)$/);"
                        "        if (m && parseInt(m[1], 10) === parseInt(n, 10)) target = el;"
                        "      });"
                        "    }"
                        "    var shown = 0;"
                        "    els.forEach(function(el) {"
                        "      var vis = (el === target);"
                        "      el.style.display = vis ? '' : 'none';"
                        "      if (vis) shown++;"
                        "    });"
                        # 1つも表示されなければサイレント失敗を防ぐため例外にする
                        "    if (shown === 0) {"
                        "      throw new Error('slide: page-' + n + ' に一致する要素が見つかりません');"
                        "    }"
                        "  }"
                        "  if (typeof window.renderFrame !== 'function') {"
                        "    window.renderFrame = function(state) {};"
                        "  }"
                        "}", slide_page)
                page.wait_for_function("typeof globalThis.renderFrame === 'function'", timeout=5000)
                progress_step = _builtins.max(1, capture_frames // 10)
                captured = 0
                # statefulなrenderFrameとの互換性を保つため、窓より前も
                # evaluateだけは順に行い、高コストなscreenshotだけを省略する。
                for i in range(frame_end):
                    t = i / fps
                    u = 1.0 if full_frames <= 1 else i / (full_frames - 1)
                    state = {
                        "frame": i, "t": t, "u": u,
                        "fps": fps, "duration": dur,
                        "width": w, "height": h,
                        "data": self._web_data, "seed": 0,
                    }
                    page.evaluate("state => globalThis.renderFrame(state)", state)
                    if i < frame_start:
                        continue
                    page.screenshot(
                        path=os.path.join(
                            frames_dir, f"frame_{captured:05d}.png"),
                        omit_background=True)
                    captured += 1
                    if (capture_frames >= 20 and (
                            captured == 1 or captured % progress_step == 0
                            or captured == capture_frames)):
                        print(
                            f"  Webフレーム {captured}/{capture_frames} "
                            f"({captured / capture_frames:.0%})")
            finally:
                # 失敗時もブラウザプロセスを残さない
                browser.close()

    def grid(self, cols, rows, *, gap=0):
        """このObjectを cols×rows のグリッドに複製配置する Transform を追加。

        出力サイズは (cols*iw + gap*(cols-1)) × (rows*ih + gap*(rows-1))。
        背景パターン生成向け。静止画（-loop 1）を tile フィルタで並べる。
        """
        if self.media_type not in ("image",):
            raise TypeError("grid() は画像素材にのみ使用できます")
        if cols < 1 or rows < 1:
            raise ValueError("grid: cols/rows は1以上が必要です")
        self._append_transform(
            Transform("grid", cols=int(cols), rows=int(rows), gap=int(gap)))
        return self

    def split(self):
        """(VideoView or None, AudioView or None) を返す"""
        v = VideoView(self) if self.has_video else None
        a = AudioView(self) if self.has_audio else None
        return v, a

    def __repr__(self):
        return f"Object({self.source}, transforms={self.transforms}, effects={self.effects}, audio_effects={self.audio_effects})"


class VideoView:
    """映像ビュー（split()で生成、参照専用）"""
    def __init__(self, clip):
        self._clip = clip

    def __le__(self, rhs):
        """映像系のみ受け入れ"""
        if isinstance(rhs, (Transform, TransformChain, Effect, EffectChain)):
            self._clip.__le__(rhs)
            return self
        raise TypeError(f"VideoView <= には映像系のみ: {type(rhs)}")

    def time(self, *args, **kwargs):
        raise TypeError("VideoView.time() は禁止です。clip.time() を使ってください。")

    def until(self, *args, **kwargs):
        raise TypeError("VideoView.until() は禁止です。clip.until() を使ってください。")


class AudioView:
    """音声ビュー（split()で生成、参照専用）"""
    def __init__(self, clip):
        self._clip = clip

    def __le__(self, rhs):
        """音声系のみ受け入れ"""
        if isinstance(rhs, (AudioEffect, AudioEffectChain)):
            self._clip.__le__(rhs)
            return self
        raise TypeError(f"AudioView <= には音声系のみ: {type(rhs)}")

    def time(self, *args, **kwargs):
        raise TypeError("AudioView.time() は禁止です。clip.time() を使ってください。")

    def until(self, *args, **kwargs):
        raise TypeError("AudioView.until() は禁止です。clip.until() を使ってください。")


def tile(obj, cols, rows, gap=0):
    """obj を cols×rows のグリッドに複製配置（Object.grid の関数版）。obj を返す。"""
    if not isinstance(obj, Object):
        raise TypeError("tile: 第1引数は Object が必要です")
    return obj.grid(cols, rows, gap=gap)


class Group:
    """複数Objectをまとめて同一Transform/Effectを一括適用するプロキシ。

    使用例:
        group(a, b, c) <= move(x=0.5, y=0.5)   # 各Objectへ委譲
        group(a, b).time(3)                     # time/until も委譲
    各適用は個々のObjectの __le__ / time / until へ転送する。
    """
    def __init__(self, *objects):
        flat = []
        for o in objects:
            if isinstance(o, Group):
                flat.extend(o.objects)
            elif isinstance(o, Object):
                flat.append(o)
            else:
                raise TypeError(f"group: Object のみ渡せます: {type(o)}")
        if not flat:
            raise ValueError("group: 最低1つのObjectが必要です")
        self.objects = flat

    def __le__(self, rhs):
        for o in self.objects:
            o.__le__(rhs)
        return self

    def time(self, duration=None, *, name=None):
        """各メンバーを time() で「順次配置」する（メンバーが順番に並ぶ）。

        注意: time() は各メンバーを直列に配置するため、グループ全体の尺は
        メンバー数 N 倍（N*duration）になる。全メンバーを同一開始時刻に
        「同時に重ねて」配置したい場合は stack() または show() を使うこと。
        """
        # name は先頭Objectにのみ付与（アンカー重複を避ける）
        for i, o in enumerate(self.objects):
            o.time(duration, name=name if i == 0 else None)
        return self

    def until(self, name, offset=0.0):
        for o in self.objects:
            o.until(name, offset=offset)
        return self

    def show(self, duration, *, priority=None):
        for o in self.objects:
            o.show(duration, priority=priority)
        return self

    def stack(self, duration, *, priority=None):
        """全メンバーを同一開始時刻に重ねて配置し、タイムラインを duration だけ進める。

        time() が各メンバーを順次配置（グループ尺 N 倍）するのに対し、stack() は
        全メンバーを同時表示する。各メンバーは show() で配置（advance しない）し、
        タイムライン全体は末尾に一度だけ pause を挟んで duration 進める。
        """
        for o in self.objects:
            o.show(duration, priority=priority)
        # メンバーは advance しないため、タイムラインを一度だけ duration 進める
        if self.objects and Project._current is not None:
            pause.time(duration)
        return self


def group(*objects):
    """複数ObjectをまとめるGroupプロキシを返す（group(a,b,c) <= move(...)）。"""
    return Group(*objects)


# --- 遅延解決の相互参照（関数本体からのみ使用: 循環importを避けるため末尾で束縛）---
from scriptvedit.cache import _build_unified_ops, _fold_time_effects, _op_prefix_fingerprint, _ops_effective_quality, _sig_key, _src_bucket, _src_signature, _web_cache_path
from scriptvedit.ffmpeg import _decoder_input_args, _run_ffmpeg_to_cache, _unique_tmp_path
from scriptvedit.filters.video import _build_effect_filters, _build_transform_filters, _build_video_pre_filters, _get_base_dimensions
from scriptvedit.plugins import _EFFECT_PLUGINS
from scriptvedit.project import Project
from scriptvedit.state import _ARTIFACT_DIR, _BAKE_PIX_FMT, _BAKE_PIXFMT_VER, _CACHE_DIR, _ENGINE_VER, _GEN_COUNTER, _GEN_COUNTER_LOCK, _TERMINAL_FRAME_EFFECTS, _TIME_LIVE_EFFECTS, _detect_media_type, _suggest_hint
from scriptvedit.timeline import _link_after, _register_anchor_owner, pause
from scriptvedit.validate import _require_time
