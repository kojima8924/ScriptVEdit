# -*- coding: utf-8 -*-

import warnings


class _AnchorMarker:
    """アンカー位置マーカー（タイムライン上の位置を記録、レンダリングなし）"""
    def __init__(self, name):
        self.name = name
        self.duration = None
        self.start_time = 0
        self.priority = 0


def _link_after(prev, nxt):
    """DSL糖衣 `prev >> nxt`: nxt を prev の終了直後に開始する（浮動配置）。

    nxt は順次配置のカーソルを進めない（_advance=False）。prev の尺は
    リゾルバ時点で確定している必要がある（time()/スライス/until のいずれか）。
    戻り値は nxt（`a >> b >> c` と連結できる）。
    """
    from scriptvedit.objects import Object as _Object
    if not isinstance(nxt, (_Object, Pause)):
        raise TypeError(
            f">> の右辺は Object か pause.time(...) を指定してください: "
            f"{type(nxt).__name__}")
    if nxt is prev:
        raise ValueError(">> に自分自身は連結できません")
    nxt._start_after = prev
    nxt._fixed_start = None
    nxt._advance = False
    return nxt


class Pause:
    """非描画タイムラインアイテム（時間のみ占有、レンダリングなし）"""
    def __init__(self):
        self.duration = None
        self.start_time = 0
        self.priority = 0
        self._until_anchor = None
        self._until_offset = 0.0
        self._fixed_start = None   # @ による絶対配置（Objectと共通の浮動配置属性）
        self._start_after = None   # >> による直後連結

    def time(self, duration):
        _require_time("pause.time", "duration", duration, lo=0)
        self.duration = duration
        return self

    def until(self, name, offset=0.0):
        _require_time("pause.until", "offset", offset)
        self._until_anchor = name
        self._until_offset = offset
        return self

    def __rshift__(self, other):
        """`a >> pause.time(0.5) >> b` の中継（次を自分の直後に配置）"""
        return _link_after(self, other)


class _ScenePad:
    """シーン末尾の遅延パディングマーカー（レンダリングなし）。

    _resolve_anchors 実行時に「シーン開始時刻 + 目標尺」まで current_time を進める。
    自動尺(.time()省略/until)の尺確定後に実 used が反映されるため、
    exec 時点で即 pad するより正確（pad 過大で後続シーンがずれるのを防ぐ）。
    """
    def __init__(self, scene_name, target_duration):
        self.scene_name = scene_name
        self.target_duration = target_duration
        self.duration = None
        self.start_time = 0
        self.priority = 0


class Scene:
    """シーンのコンテキストマネージャ（p.scene() が返す）。

    with 内で定義したObjectはシーン相対の時刻になり、シーン終端を duration まで
    パディングすることで、複数シーンが時間軸上に順次配置される。開始位置に
    `scene:<name>` アンカーを張り、他レイヤーから参照できる。
    """
    def __init__(self, project, name, duration):
        if duration is None:
            raise ValueError(f"scene '{name}': duration は正の値が必要です")
        _require_time(f"scene '{name}'", "duration", duration,
                      lo=0, lo_exclusive=True)
        self.project = project
        self.name = name
        self.duration = duration
        self._start_index = None

    def __enter__(self):
        # 開始位置にアンカー（他レイヤー/シーンからの参照点）
        anchor(f"scene:{self.name}")
        self._start_index = len(self.project.objects)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            return False
        # 目安の used（確定尺のみ）で明らかな超過だけ警告する。
        # 実パディングは _ScenePad による遅延解決（自動尺/until 確定後に正確化）。
        est_used = 0.0
        for o in self.project.objects[self._start_index:]:
            if isinstance(o, (_AnchorMarker, _ScenePad)):
                continue
            if getattr(o, "_advance", True) and getattr(o, "duration", None):
                est_used += o.duration
        if est_used - self.duration > 1e-6:
            warnings.warn(
                f"scene '{self.name}': 内容尺 {est_used:.3f}s が duration "
                f"{self.duration}s を超えています（シーンが重なります）")
        # 遅延パディングマーカー（_resolve_anchors でシーン開始+duration まで進める）
        self.project.objects.append(_ScenePad(self.name, self.duration))
        # 終端アンカー（pad 後の時刻 = シーン開始 + duration に解決される）
        anchor(f"scene:{self.name}.end")
        return False


# --- アンカー/同期 ---

def _register_anchor_owner(proj, name):
    """アンカー名の定義元レイヤーを登録し、別レイヤーでの再定義を拒否する。

    明示 anchor() と time(name=...) の生成アンカー（X.start / X.end）の
    共通経路。同一レイヤーファイルの再実行（Plan/Renderの複数pass）は許容し、
    別レイヤーでの同名定義は last-write-wins にせずエラーにする
    （監査 issue #14 P1）。
    """
    current_file = proj._current_layer_file or "(unknown)"
    if name in proj._anchor_defined_in:
        existing_file = proj._anchor_defined_in[name]
        if existing_file != current_file:
            raise RuntimeError(
                f"アンカー '{name}' は既に '{existing_file}' で定義されています "
                f"('{current_file}' で再定義は禁止)"
            )
    proj._anchor_defined_in[name] = current_file


def anchor(name):
    """現在のレイヤー位置にアンカーを登録"""
    proj = Project._current
    if proj is None:
        raise RuntimeError("anchor()にはアクティブなProjectが必要です")
    _register_anchor_owner(proj, name)
    marker = _AnchorMarker(name)
    proj.objects.append(marker)


class _PauseFactory:
    """pause.time(N) / pause.until(name) でPauseを生成・登録するファクトリ"""
    def time(self, duration):
        p = Pause().time(duration)
        if Project._current is not None:
            Project._current.objects.append(p)
        return p

    def until(self, name, offset=0.0):
        p = Pause().until(name, offset)
        if Project._current is not None:
            Project._current.objects.append(p)
        return p


pause = _PauseFactory()


def scene(name, duration):
    """シーンのコンテキストマネージャ（アクティブProjectに対して動作）。

    レイヤーファイル内で `with scene("intro", 5): ...` のように使う。
    p.scene() と同義だが Project._current を暗黙に使う（anchor/pause と同様）。
    """
    proj = Project._current
    if proj is None:
        raise RuntimeError("scene()にはアクティブなProjectが必要です")
    return Scene(proj, name, duration)


# --- 遅延解決の相互参照（関数本体からのみ使用: 循環importを避けるため末尾で束縛）---
from scriptvedit.project import Project
from scriptvedit.validate import _require_time
