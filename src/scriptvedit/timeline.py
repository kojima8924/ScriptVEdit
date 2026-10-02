# -*- coding: utf-8 -*-

import os
import sys as _sys
import warnings

# context は scriptvedit 内 import を持たない葉なので先頭で import できる。
from scriptvedit.context import current_project

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.validate import _require_time


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
        # Object と共通の浮動配置属性。Pause は @ を持たない（pause.time(1) @ 3 は
        # TypeError）ので常に None。_resolve_anchors が Object と同じ経路で読むためだけに置く
        self._fixed_start = None
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


def _check_until_zero_duration(items, anchors):
    """until() の解決結果が 0 尺以下になったアイテムを明示エラーにする。

    `until('mark')` は「アンカー時刻まで伸ばす」だが、アンカーが開始時刻以前に
    あると尺が 0 になる。0 尺はチェックポイント経路の尺基準をすり抜け、最終的に
    `clip((t-start)/0,0,1)` という式が filtergraph に埋まって ffmpeg が
    Division by zero / EINVAL で落ちる。利用者に届くのは原因不明の ffmpeg エラー
    なので、タイムライン解決の時点で「何が悪いか + どう直すか」を出す
    （監査項目6）。time()/show() は既に 0 を lo_exclusive で弾いている。

    引数はタイムライン解決後の items（Object / Pause 等）と解決済み anchors。
    _resolve_anchors の収束後（check_unresolved のブロック）で1回だけ呼ぶこと。
    反復の途中は尺が一時的に 0 になりうるため、収束前に呼んではいけない。
    """
    for item in items:
        until_name = getattr(item, "_until_anchor", None)
        if not until_name or until_name not in anchors:
            continue
        if isinstance(item, Pause):
            # Pause はタイムライン上の間隔で、フィルタグラフに出ない。
            # 0 尺でも「間隔なし」になるだけで壊れないため対象外にする
            # （エラーにすると無害な書き方まで弾いてしまう）
            continue
        dur = getattr(item, "duration", None)
        if dur is None or dur > 0:
            continue
        offset = getattr(item, "_until_offset", 0.0)
        target = anchors[until_name] + offset
        who = getattr(item, "source", None) or type(item).__name__
        offset_str = f" + {offset}" if offset else ""
        raise RuntimeError(
            f"until('{until_name}'{offset_str}) の解決結果が開始時刻以前のため"
            f"表示尺が 0 になりました: {who}\n"
            f"  開始時刻 = {getattr(item, 'start_time', 0)} 秒 / "
            f"アンカー '{until_name}' = {anchors[until_name]} 秒"
            f"（オフセット込みの目標時刻 = {target} 秒）\n"
            f"アンカーをこのアイテムより後ろに定義するか、until() をやめて "
            f"time(秒) で明示的な尺を指定してください。")


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
        _add_anchor(self.project, f"scene:{self.name}",
                    desc=f"scene('{self.name}')")
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
        _add_anchor(self.project, f"scene:{self.name}.end",
                    desc=f"scene('{self.name}')")
        return False


# --- アンカー/同期 ---

# scriptvedit パッケージのディレクトリ（利用者コードの呼び出し位置を探すときに飛ばす）
_PKG_DIR = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))


def _user_call_site():
    """scriptvedit の外で最も内側の呼び出し位置 (ファイル名, 行番号) を返す。

    レイヤー .py は exec(compile(code, filename, ...)) されるので、そのフレームの
    co_filename はレイヤーファイルになる。scene() のようにパッケージ内部から
    anchor() を呼ぶ経路でも、利用者が書いた行（with scene(...) の行）を指す。
    """
    frame = _sys._getframe(1)
    while frame is not None:
        fn = frame.f_code.co_filename
        try:
            d = os.path.normcase(os.path.dirname(os.path.abspath(fn)))
        except (TypeError, ValueError):
            d = ""
        if d != _PKG_DIR and not d.startswith(_PKG_DIR + os.sep):
            return fn, frame.f_lineno
        frame = frame.f_back
    return None, None


def _register_anchor_owner(proj, name, *, owner=None, desc=None, shown=None):
    """アンカー名の定義元レイヤーを登録し、重複定義を拒否する。

    明示 anchor() と time(name=...) の生成アンカー（X.start / X.end）の
    共通経路。
    - 別レイヤーでの同名定義は last-write-wins にせず RuntimeError
      （監査 issue #14 P1）。同一レイヤーファイルの再実行（Plan/Render の
      複数 pass）はこちらでは許容する。
    - **同じレイヤーの1回の実行の中**での同名定義（anchor('x') の2回、
      time(name='x') の2回、anchor('x.start') と time(name='x') の混在）は、
      定義した時点で行番号つきの ValueError にする。放置すると固定点反復が
      2つの時刻の間で振動し、原因の分からない「タイムライン解決が
      収束しませんでした」になる（位置が偶然同じだと黙って通る）。

    owner: 定義したアイテム（anchor() は新しいマーカー、time(name=) は Object）。
        同じ Object が time(name=) をやり直すのは再定義ではないので許す。
    desc: エラー表示用の定義の書き方（例 "anchor('x')" / "time(name='x')"）。
    shown: エラー表示用のアンカー名（time(name='x') なら 'x'）。
    """
    current_file = proj._current_layer_file or "(unknown)"
    if name in proj._anchor_defined_in:
        existing_file = proj._anchor_defined_in[name]
        if existing_file != current_file:
            raise RuntimeError(
                f"アンカー '{name}' は既に '{existing_file}' で定義されています "
                f"('{current_file}' で再定義は禁止)"
            )
    fn, line = _user_call_site()
    desc = desc or f"anchor('{name}')"
    sites = getattr(proj, "_layer_anchor_sites", None)
    if sites is None:
        sites = proj._layer_anchor_sites = {}
    prev = sites.get(name)
    if prev is not None and prev[0] is not owner:
        _, prev_fn, prev_line, prev_desc = prev
        now_desc = desc
        if shown and prev_desc == desc:
            # time(name='x') の2回 → 利用者が書いた名前 'x' で示す
            label = f"'{shown}'"
        else:
            # anchor() の2回、または anchor('x.start') と time(name='x') の混在
            label = f"'{name}'"
            if shown and shown != name:
                now_desc += f"（'{name}' を作る）"
        where = f"{prev_line}行目" if prev_line else "行番号不明"
        if prev_fn and fn and os.path.normcase(prev_fn) != os.path.normcase(fn):
            where = f"{os.path.basename(prev_fn)} の {where}"
        now = f"{line}行目の " if line else ""
        raise ValueError(
            f"アンカー名 {label} はこのレイヤーで既に定義されています"
            f"（{where}: {prev_desc}）。{now}{now_desc} で再定義しようとしました。\n"
            f"同じレイヤーの中でアンカー名は1回しか定義できません"
            f"（2回定義するとタイムラインの時刻が1つに決まりません）。"
            f"別の名前にしてください。")
    sites[name] = (owner, fn, line, desc)
    proj._anchor_defined_in[name] = current_file


def _unregister_anchor_site(proj, name, owner):
    """owner が同じレイヤー実行中に登録したアンカー名を取り消す。

    time(name='x') の後で同じ Object に time(name='y') をやり直したとき、
    使われなくなった 'x.start' / 'x.end' が重複検査に残らないようにする。
    """
    sites = getattr(proj, "_layer_anchor_sites", None)
    if sites and name in sites and sites[name][0] is owner:
        del sites[name]


def _add_anchor(proj, name, *, desc=None):
    """アンカーマーカーを重複検査つきでタイムラインへ置く（anchor() / scene() 共通）"""
    marker = _AnchorMarker(name)
    _register_anchor_owner(proj, name, owner=marker, desc=desc)
    proj.objects.append(marker)


def anchor(name):
    """現在のレイヤー位置にアンカーを登録"""
    proj = current_project()
    if proj is None:
        raise RuntimeError("anchor()にはアクティブなProjectが必要です")
    _add_anchor(proj, name)


class _PauseFactory:
    """pause.time(N) / pause.until(name) でPauseを生成・登録するファクトリ"""
    def time(self, duration):
        p = Pause().time(duration)
        if current_project() is not None:
            current_project().objects.append(p)
        return p

    def until(self, name, offset=0.0):
        p = Pause().until(name, offset)
        if current_project() is not None:
            current_project().objects.append(p)
        return p


pause = _PauseFactory()


def scene(name, duration):
    """シーンのコンテキストマネージャ（アクティブProjectに対して動作）。

    レイヤーファイル内で `with scene("intro", 5): ...` のように使う。
    p.scene() と同義だが current_project() を暗黙に使う（anchor/pause と同様）。
    """
    proj = current_project()
    if proj is None:
        raise RuntimeError("scene()にはアクティブなProjectが必要です")
    return Scene(proj, name, duration)
