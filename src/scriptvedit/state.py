# -*- coding: utf-8 -*-

import os
import sys
import difflib as _difflib


# --- media_type判定 ---

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".gif"}
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
_WEB_EXTS = {".html", ".htm"}


def _detect_media_type(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _AUDIO_EXTS:
        return "audio"
    if ext in _WEB_EXTS:
        return "web"
    return "image"  # フォールバック


# --- ffmpeg実行ヘルパー ---

# Windowsのコマンドライン長制限対策: フィルタ文字列がこの長さを超えたら一時ファイル経由で渡す


# --- configure許可キー ---

_CONFIGURE_KEYS = {"width", "height", "fps", "duration", "background_color",
                   "preset", "encoder", "parallel", "draft_web_fps"}

# 出力プリセット: name -> (width, height, fps)
_PRESETS = {
    "shorts": (1080, 1920, 30),
    "reel":   (1080, 1920, 30),
    "reels":  (1080, 1920, 30),
    "tiktok": (1080, 1920, 30),
    "vertical": (1080, 1920, 30),
    "square": (1080, 1080, 30),
    "hd":     (1920, 1080, 30),
    "fhd":    (1920, 1080, 30),
    "1080p":  (1920, 1080, 30),
    "720p":   (1280, 720, 30),
    "2k":     (2560, 1440, 30),
    "4k":     (3840, 2160, 30),
}

# エンコーダ名 -> {cv: -c:v の値, args: 追加エンコード引数, draft: ドラフト用引数}
_ENCODER_MAP = {
    # libx264 の既定は追加引数なし（従来の出力・スナップショットと一致させる）
    "libx264":    {"cv": "libx264", "args": [],
                   "draft": ["-preset", "ultrafast", "-crf", "28"]},
    "nvenc":      {"cv": "h264_nvenc", "args": ["-preset", "p5", "-cq", "23"],
                   "draft": ["-preset", "p1", "-cq", "30"]},
    "hevc_nvenc": {"cv": "hevc_nvenc", "args": ["-preset", "p5", "-cq", "25"],
                   "draft": ["-preset", "p1", "-cq", "32"]},
    "qsv":        {"cv": "h264_qsv", "args": ["-global_quality", "23"],
                   "draft": ["-global_quality", "32"]},
    "hevc":       {"cv": "libx265", "args": ["-preset", "medium", "-crf", "24"],
                   "draft": ["-preset", "ultrafast", "-crf", "30"]},
}

# 生成した中間ファイル数のカウンタ（render統計用。render開始時にリセット）
_GEN_COUNTER = [0]
# _GEN_COUNTER の並列更新保護（並列レイヤー生成での過少計上を防ぐ）
import threading as _threading
_GEN_COUNTER_LOCK = _threading.Lock()

# ffmpeg 利用可能エンコーダ集合のキャッシュ（None=未取得）
_AVAILABLE_ENCODERS = [None]


def _suggest_hint(name, candidates, prefix="\nもしかして: "):
    """未知の名前に対し近い候補を difflib で探し、'もしかして: X?' を返す。
    候補が無ければ空文字列。エラーメッセージ末尾に連結して使う。"""
    try:
        matches = _difflib.get_close_matches(
            str(name), [str(c) for c in candidates], n=3, cutoff=0.6)
    except Exception:
        matches = []
    if not matches:
        return ""
    return f"{prefix}{', '.join(matches)}?"


_CACHE_DIR = "__cache__"
_ARTIFACT_DIR = os.path.join(_CACHE_DIR, "artifacts")
_ENGINE_VER = "8"

# --- 中間ベイク(FFV1 .mkv)のピクセル形式 -------------------------------------
# checkpoint / compute / morph / particle / xfade の中間物はすべてこの形式で焼く。
#
# bgra であって yuva444p でないのは実測に基づく。FFV1 自体は可逆だが、
# yuva444p は RGBA→YUV の行列変換を挟むため往復がビット完全にならない:
#   - 実測(640x360・高彩度の細字＋半透明グラデ＋ノイズ): yuva444p は
#     不透明部PSNR 54.92dB / RGB最大誤差 2 / alpha不一致 20,064px。
#     bgra は PSNR=inf・最大誤差0・alpha不一致0（完全にビット完全）。
#   - colorspace/color_range を明示しても往復はビット完全にならない。生成側と
#     再利用側が colorspace 未タグのまま別々に BT.601/709 を推定して食い違うため。
# bgra はチャンネル並べ替えのみで色変換が無い。中間物は多段（morph→checkpoint→
# 本レンダ）で積み上がるため、ここでの微小な色ずれ・alpha ずれが累積する。
# 代償は中間ファイルの肥大（実測 +12.7%）だが、中間物は最終出力に残らない。
#
# 注: xfade は bgra を直接扱えず gbrap（プレーナRGBA）へ自動変換されるが、
# どちらも8bit RGB フルレンジのため詰め替えのみで劣化しない（YUVは経由しない）。
_BAKE_PIX_FMT = "bgra"

# 中間ベイクのピクセル形式世代。_BAKE_PIX_FMT を変えたら上げる。
# （_ENGINE_VER を上げると layer/web/audio を含む全キャッシュが飛ぶため、
#   ベイク系のキャッシュ鍵だけを無効化する。morph/particle は
#   _MORPH_RENDER_VER 側で無効化する）
_BAKE_PIXFMT_VER = "1"

_BAKEABLE_EFFECTS = {"scale", "fade", "trim", "morph_to", "rotate_to", "wipe", "color_shift",
                     "chroma_key", "vignette", "pixelize", "glow", "lut", "glitch",
                     "perspective_warp", "lens", "ken_burns", "drop_shadow", "outline",
                     "explode_to", "assemble_from",
                     "mask", "mask_wipe", "opacity", "rounded"}

# 終端フレーム生成Effect（bakeable末尾に1つだけ・映像を生成する）
_TERMINAL_FRAME_EFFECTS = {"morph_to", "explode_to", "assemble_from"}

# 時間操作系の live Effect（setpts/reverse/concat による時間変形）。
# チェックポイントベイクの表示尺基準と食い違うため bakeable にはしない
# （ベイク済みソースに対して毎レンダ live で適用する）。
_TIME_LIVE_EFFECTS = {"speed", "reverse", "freeze_frame", "repeat"}

# reverse Effect の実効尺上限（全フレームをメモリに保持するため長尺は危険）
_REVERSE_MAX_SEC = 30.0


# --- 配置基準点（anchor）の語彙 ---------------------------------------------
# move 系 Effect（move / move_along / path_bezier / throw / inertia / pip）の
# anchor。座標式を分岐するのは filters/video.py の _build_move_exprs だけで、
# 6値すべてが互いに異なる overlay 座標式を生む（辺の中点／角／中心）。
# ファクトリが複数あるため検証は Effect 構築側（objects.py の Effect.__init__）に
# 置く。ファクトリ単位で書くと必ず抜ける。
#
# 値を増やすときは filters/video.py の _ANCHOR_OFFSETS へ実装を同時に足すこと。
# 公称だけ増やすと「指定しても topleft にずれる」沈黙する失敗になる
# （tests/test_errors.py の check_anchor_choices_produce_distinct_exprs が
#  「公称値がすべて互いに異なる式を返すこと」で再発を検出する）。
_PLACEMENT_ANCHORS = ("center", "topleft", "left", "right", "top", "bottom")

# audio_viz(kind=) の可視化方式。実装（audio.py の audio_viz）と
# manifest の choices が同じ集合を見るように一本化する（layer_cache と同じ方式）。
# 以前は manifest だけが存在しない 'bars' を公称し、実在の 'spectrum' を隠していた。
_AUDIO_VIZ_KINDS = ("waves", "spectrum", "cqt")

# text / typewriter / counter の anchor（drawtext の x 起点）。
# overlay 配置の _PLACEMENT_ANCHORS とは別軸の語彙なので値も別に持つ。
_TEXT_ANCHORS = ("center", "left")


# --- パッケージ名前空間（プラグイン注入・describe のイントロスペクション用）---
# 旧単一ファイル版の globals() / __all__ に相当。分割後は公開名前空間である
# scriptvedit パッケージ本体を指す（プラグインのファクトリはここへ注入される）。
def _pkg_ns():
    """scriptvedit パッケージの名前空間 dict を返す"""
    return sys.modules["scriptvedit"].__dict__


def _pkg_all():
    """scriptvedit パッケージの __all__ リスト（実体・破壊的更新可）を返す"""
    return sys.modules["scriptvedit"].__all__
