# -*- coding: utf-8 -*-
"""「現在の Project」を保持する葉モジュール（循環 import 解消の土台）。

**このモジュールは scriptvedit 内の他モジュールを一切 import しない。**
その制約こそがこのモジュールの存在理由なので、依存を足さないこと
（足した瞬間に、ここへ集約した意味が消えて循環が復活する）。

## 背景

レイヤー .py の中で作った Object は「現在の Project」へ自動登録される
（CLAUDE.md §1 の中核契約）。この「現在の Project」は以前 `Project._current`
というクラス属性だったため、**参照するだけのモジュールが軒並み
`from scriptvedit.project import Project` を必要とし**、project.py が
ほぼ全モジュールを import する側でもあることから 19 モジュールの巨大な
強連結成分（循環 import）ができていた。回避策として「import は
ファイル末尾に書く」という不文律が生まれていたが、これは静的解析も
IDE も追えず、新規モジュールを普通に書くと ImportError を踏む。

状態そのものをここ（葉）へ出すことで、参照側は project.py に依存せず
`from scriptvedit.context import current_project` を**先頭で**書ける。

## スレッドについて

`contextvars.ContextVar` は使わず、素のモジュールグローバルにしている。
レイヤーキャッシュは ThreadPoolExecutor で並列生成され、そのワーカスレッドは
生成元のコンテキストを引き継がない（`ContextVar` は既定値 None に戻る）。
フィルタ構築が現在の Project を読めなくなり静かに壊れるため、
従来の `Project._current`（クラス属性＝プロセス共有）と同じ意味論を保つ。
"""

# 現在の Project（レイヤー exec 中はそのレイヤーを実行している Project）
_current = None

# レイヤー実行中の Project スタック（from_project での親特定用）。
# レイヤー内で `sub = Project()` されても親を見失わないよう _current とは別管理。
_exec_stack = []

# Project クラスの実体（project.py が定義直後に登録する）。
# context は project を import できないため、型判定だけを逆方向に注入する。
_project_class = None


def current_project():
    """現在の Project を返す（無ければ None）。"""
    return _current


def activate(project):
    """現在の Project を差し替える。"""
    global _current
    _current = project


def push_exec(project):
    """レイヤー exec の開始を記録する。"""
    _exec_stack.append(project)


def pop_exec():
    """レイヤー exec の終了を記録する。"""
    return _exec_stack.pop()


def exec_parent():
    """レイヤーを exec 中の Project（自動登録先の親）を返す。無ければ None。"""
    return _exec_stack[-1] if _exec_stack else None


def in_layer_exec():
    """レイヤーファイルの exec 中なら True。"""
    return bool(_exec_stack)


def register_project_class(cls):
    """Project クラスを登録する（project.py がクラス定義直後に呼ぶ）。"""
    global _project_class
    _project_class = cls


def is_project(obj):
    """obj が Project インスタンスかを返す。

    from_project の引数検証のためだけに必要な型判定。ここで
    `from scriptvedit.project import Project` を書くと objects.py →
    project.py の辺が復活し 18 モジュールが一つの循環に戻るため、
    project.py 側からクラスを登録してもらう向きにしている。
    """
    return _project_class is not None and isinstance(obj, _project_class)


def reset():
    """現在の Project とレイヤースタックを初期化する（主にテスト用）。"""
    global _current
    _current = None
    _exec_stack.clear()
