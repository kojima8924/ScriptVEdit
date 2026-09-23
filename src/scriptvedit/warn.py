# -*- coding: utf-8 -*-
"""レンダ警告の集約（`_warn`）。

**scriptvedit 内 import を1つも持たない葉モジュール**。context.py と同じ
位置づけで、ここに依存を足すと集約した意味が消える。

もとは project.py にあったが、レイヤーキャッシュを layercache.py へ分離した
際に「project.py を import せずに警告を出す」必要が生じたため切り出した
（CLAUDE.md の「共有している状態は葉モジュールへ出す」）。`_warn` を呼ぶ側の
スタック段数は移設前と変わらないので、warnings が報告する呼び出し位置
（stacklevel=3）も従来どおり。
"""

import threading as _threading
import warnings


# _warn の集約リストはレイヤーキャッシュの並列生成スレッドからも触られる。
# warnings.catch_warnings はプロセスグローバルでスレッドセーフでないため使わず、
# append だけをロックで保護する（監査 項目8）。
_WARN_LOCK = _threading.Lock()


def _warn(project, message, *, sticky=False):
    """警告を warnings.warn で出しつつ、レンダ末尾の [警告] ブロックへ集約する。

    warnings は stderr へ流れるが ffmpeg の出力に埋もれ、Python 既定の
    フィルタは同一箇所の再出力を抑制する。出力末尾しか読まない利用者・AIには
    「音声が脱落した」「エンコーダがフォールバックした」等の**内容に影響する
    警告**が届かないため、レンダ完了時にまとめて再掲する。

    sticky=True: configure() 等レンダパス外で出た警告。次のレンダパス開始で
    クリアせず、以後のレンダでも再掲する（設定はレンダをまたいで効くため）。
    """
    warnings.warn(message, stacklevel=3)
    if project is None:
        return
    attr = "_sticky_warnings" if sticky else "_render_warnings"
    bucket = getattr(project, attr, None)
    if bucket is None:
        return
    with _WARN_LOCK:
        if message not in bucket:  # 同一内容は1件にまとめる（再掲のため）
            bucket.append(message)
