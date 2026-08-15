# -*- coding: utf-8 -*-



# --- 時間操作Effect（映像・live） ---

def speed(factor):
    """再生速度Effect（setpts=PTS/factor）。映像の実効尺は 元尺/factor になる
    （length()/duration自動決定に反映される）。

    音声付き動画には <= 適用時に対応する atempo が自動適用される
    （atempo有効範囲0.5〜100を超える場合は多段に自動分解）。
    live Effect（チェックポイントベイク対象外。ベイク済みソースに毎レンダ適用）。
    """
    _require_number("speed", "factor", factor, 0.01, 100.0)
    return Effect("speed", factor=float(factor))


def reverse():
    """逆再生Effect（reverseフィルタ）。

    注意: 全フレームをメモリに保持するため、実効尺が30秒を超える素材には
    使用できません（明示エラー）。長い素材は trim() で短くしてから適用すること。
    音声は反転されません（必要なら adelete() で除外するか別素材を用意）。
    live Effect（チェックポイントベイク対象外）。
    """
    return Effect("reverse")


def freeze_frame(at, duration):
    """フリーズフレームEffect: 時刻 at のフレームで duration 秒静止してから
    続きを再生する（トータル尺は +duration。length()に反映される）。

    trim 3分割 + loop + concat のチェーン内サブグラフで実装。
    音声は変化しません（音声も止めたい場合は別途編集）。
    live Effect（チェックポイントベイク対象外）。
    """
    _require_number("freeze_frame", "at", at, 0.0, None)
    _require_number("freeze_frame", "duration", duration, 0.01, None)
    return Effect("freeze_frame", at=float(at), duration=float(duration))


# --- 遅延解決の相互参照（関数本体からのみ使用: 循環importを避けるため末尾で束縛）---
from scriptvedit.objects import Effect
from scriptvedit.validate import _require_number
