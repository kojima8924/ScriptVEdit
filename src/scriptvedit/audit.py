# -*- coding: utf-8 -*-
"""p.audit(): 動画の品質lint（レンダ前チェック）。

過去の人間レビューで繰り返し指摘された品質問題と、`~` 品質ヒントの契約
（未対応opは通常処理・実行時警告は出さない→報告はaudit側に集約する）を
静的に検査する。エラーにはせず、findings のリストを返す。

ルール一覧（code / severity）:
  quality-hint-ignored (info)    … `~` を付けたが軽量代替の無いop（通常処理される）
  text-too-small (warning/info)  … 文字が小さい（1080p換算 32px未満=warning, 44px未満=info）
  text-no-decoration (warning/info) … 縁取り・影・下地のいずれも無い文字（背景に溶ける）。
                                   configure(background_color=) で明示した単色の背景だけの
                                   上にあり、文字色とのコントラスト比が 4.5 以上なら info
                                   （render() から呼ばれたときは、透過出力
                                   （alpha=True・連番PNG）なら格下げしない。単独の
                                   p.audit() は出力先を知らないので不透明を仮定する）
  offscreen-placement (warning)  … x/y が 0..1 の比率の外（画面外で描画されない）
  text-overflow (warning)        … 推定描画幅がフレーム幅（safe area差引）を超える
  outside-duration (warning)     … 表示区間が動画の総尺と交差しない（一切映らない）
  font-missing-glyph (warning)   … 解決したフォントに日本語グリフが無い（豆腐になる）
  ※ 文字の4項目（text-too-small / text-no-decoration / text-overflow /
    font-missing-glyph）は text_image() の画像にも効く。画像は文字サイズ・装飾・
    寸法を Object に申告しており、resize / scale の倍率を掛けた画面上の実寸で見る
  audio-overlap-no-duck (warning)… BGM 役（duck_under / loop を持つ音声）と、それが
                                   ダックしていない音声が1秒以上重なる（BGM 役が無ければ
                                   全ての組を調べる。前景同士の重なりは数えない。
                                   sfx() は各発音区間で判定。組の件数も示す）
  bgm-loop (info)                … loop() 使用（短い曲のループは人間に気付かれやすい）
  bgm-too-short (warning)        … BGM（duck_underを持つ音声）の実尺が表示区間より短い
  no-normalize-audio (info)      … 音声があるのに normalize_audio() 未設定
  web-content-uninspected (info) … Canvas/DOM内部は静的lint対象外。storyboard確認を促す
  morph-sdf-crossfade (warning)  … morph_to（sdf）の2枚が重ならない／輪郭が取れない
                                   （形が動かず、実質クロスフェードになる）

severity の使い分け: warning=過去に人間レビューで実際に差し戻された類、
info=判断が分かれる・意図的な場合もある注意喚起。

過検出を避ける方針: 判定不能（Expr が数値評価できない・フォントが解析できない等）は
「報告しない」側へ倒す。位置は 6 点サンプルの全点が画面外のときだけ報告する
（スライドイン/アウトの一部が画面外なのは正常なため）。
"""

import bisect
import math
import os
import struct
import unicodedata

from scriptvedit.audio import _duck_targets
from scriptvedit.cache import _respects_fast_hint
from scriptvedit.validate import _parse_color_rgb


# 文字サイズの目安（1080p基準。人間レビュー由来: 本文44px以上・注釈32px以上）
_TEXT_MIN_PX_1080 = 32
_TEXT_BODY_PX_1080 = 44

# 音声の重なり判定のしきい値[秒]（SFXの一瞬の重なりまで警告しない）
_OVERLAP_MIN_SEC = 1.0

# audio-overlap-no-duck のメッセージに列挙する組の上限（件数は全体を数える）
_OVERLAP_SHOW_MAX = 3

# sfx() の発音区間の時刻をずらす op。これが付いた sfx は各発音区間ではなく
# 再生区間1つで重なりを判定する（区間の計算を op ごとに再実装しないため）
_TIME_SHIFT_EFFECTS = frozenset({"trim", "speed", "reverse", "freeze_frame", "repeat"})
_TIME_SHIFT_AUDIO_EFFECTS = frozenset({"atrim", "atempo", "arepeat", "loop"})

# 位置アニメーションのサンプル点（u=0,0.2,…,1 の6点）。
# 全点が範囲外のときだけ offscreen-placement を報告する
_SAMPLE_US = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)

# 「px を比率のつもりで渡した」疑いを持つ下限（0..1 の比率としては明らかに過大）
_PX_SUSPECT_MIN = 1.5

# はみ出し判定: フレーム幅から safe area 5% を差し引いた幅の 1.05 倍超で報告
# （幅推定は size×文字数の粗い近似なので、閾値に余裕を持たせる）
_SAFE_AREA_RATIO = 0.95
_OVERFLOW_TOLERANCE = 1.05

# 文字幅の粗い推定係数（全角=size, 半角=size*0.5）
_HALFWIDTH_RATIO = 0.5

# 位置・文字幅を検査するテキスト種別（drawtext ベースで x/y/size を持つもの）
_DRAWTEXT_KINDS = ("text", "typewriter", "counter")


def _finding(severity, code, message):
    return {"severity": severity, "code": code, "message": message}


def _obj_label(obj):
    """finding 表示用のオブジェクト名（ソース名 or テキスト内容の先頭）"""
    spec = getattr(obj, "_text_spec", None)
    if spec is not None:
        content = str(spec.get("content", spec.get("format", "")))
        short = content[:20] + ("…" if len(content) > 20 else "")
        return f"{spec.get('kind', 'text')}('{short}')"
    info = getattr(obj, "_text_image", None)
    if info is not None:
        # text_image() の生成物は __cache__ のハッシュ名なので、文字の先頭で示す
        content = str(info.get("content", "")).replace("\n", " ")
        short = content[:20] + ("…" if len(content) > 20 else "")
        return f"text_image('{short}')"
    if getattr(obj, "_sfx_hits", None) is not None:
        # sfx() の生成物は __cache__ のハッシュ名なので、元の音源名で示す
        origins = getattr(obj, "_origin_sources", None) or ["?"]
        return f"sfx({os.path.basename(str(origins[0]))})"
    return os.path.basename(str(getattr(obj, "source", "?")))


def _audit_quality_hints(objects, findings):
    """`~` を付けたが軽量代替の無い op を列挙する（通常処理＝正常動作）"""
    for obj in objects:
        ops = (list(getattr(obj, "transforms", []))
               + list(getattr(obj, "effects", []))
               + list(getattr(obj, "audio_effects", [])))
        for op in ops:
            if getattr(op, "quality", "final") != "fast":
                continue
            name = getattr(op, "name", "?")
            if not _respects_fast_hint(name):
                findings.append(_finding(
                    "info", "quality-hint-ignored",
                    f"{_obj_label(obj)}: ~{name} は軽量代替が無いため通常と同一の"
                    f"処理になります（品質ヒントの契約どおり。害はありません）"))


# 単色背景の上の文字を「読める」とみなすコントラスト比の下限（WCAG の本文基準）
_SOLID_BG_MIN_CONTRAST = 4.5


def _opaque_rgb(color):
    """不透明な色指定を (R, G, B) にする。透過つき・解釈できない色は None"""
    if not isinstance(color, str) or "@" in color:
        return None
    try:
        return _parse_color_rgb(color)
    except ValueError:
        return None


def _relative_luminance(rgb):
    """sRGB の相対輝度（WCAG 2.x の定義）"""
    def lin(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _solid_background_contrast(project, objects, obj, spec, transparent=False):
    """文字が「明示された単色の背景」だけの上にあるとき、そのコントラスト比を返す。

    それ以外（背景色が未指定・透過つき、表示区間が重なる画像/動画/web がある、
    色を解釈できない、透過出力）は None（＝背景は分からないので従来どおり warning）。
    transparent: render() が渡す「この出力は透過か」。alpha=True の webm / webp と
    連番 PNG は background_color を使わず背景が透明で、別の絵に重ねる前提なので、
    背景は単色と言えない。
    重なり順は見ない: 文字の上に載る絵でも、同じ時間に絵があるなら背景は
    単色と言い切れない、という安全側の判定。"""
    if transparent or not getattr(project, "_background_color_set", False):
        return None
    bg = _opaque_rgb(project.background_color)
    fg = _opaque_rgb(spec.get("color"))
    if bg is None or fg is None:
        return None
    try:
        start = float(getattr(obj, "start_time", 0) or 0)
        end = start + float(project._resolve_obj_duration(obj))
        for other in objects:
            if other is obj or getattr(other, "_text_spec", None) is not None:
                continue
            if (getattr(other, "media_type", None) == "audio"
                    or getattr(other, "_video_deleted", False)):
                continue
            o_start = float(getattr(other, "start_time", 0) or 0)
            o_end = o_start + float(project._resolve_obj_duration(other))
            if o_start < end and start < o_end:
                return None
    except Exception:
        return None     # 尺を決められない（判定不能）→ 格下げしない
    hi, lo = sorted((_relative_luminance(bg), _relative_luminance(fg)),
                    reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _audit_text_readability(project, objects, findings, transparent=False):
    """文字サイズと縁取り/影/下地の有無（人間レビューで最多の指摘）"""
    scale = (project.height or 1080) / 1080.0
    min_px = _TEXT_MIN_PX_1080 * scale
    body_px = _TEXT_BODY_PX_1080 * scale
    for obj in objects:
        spec = getattr(obj, "_text_spec", None)
        if spec is None or spec.get("kind") not in (
                "text", "typewriter", "counter"):
            continue
        size_expr = spec.get("size")
        size = getattr(size_expr, "value", None)
        if isinstance(size, (int, float)):
            if size < min_px:
                findings.append(_finding(
                    "warning", "text-too-small",
                    f"{_obj_label(obj)}: size={size:g}px は小さすぎます"
                    f"（{project.height}p では {min_px:.0f}px 以上を推奨。"
                    f"入らないときは文章を分割してください）"))
            elif size < body_px:
                findings.append(_finding(
                    "info", "text-too-small",
                    f"{_obj_label(obj)}: size={size:g}px は本文には小さめです"
                    f"（{project.height}p の本文目安は {body_px:.0f}px 以上）"))
        border = spec.get("border", 0)
        shadow = tuple(spec.get("shadow", (0, 0)))
        box = spec.get("box", False)
        if not border and shadow == (0, 0) and not box:
            contrast = _solid_background_contrast(
                project, objects, obj, spec, transparent)
            if contrast is not None and contrast >= _SOLID_BG_MIN_CONTRAST:
                findings.append(_finding(
                    "info", "text-no-decoration",
                    f"{_obj_label(obj)}: 縁取り(border)・影(shadow)・下地(box)は"
                    f"ありませんが、単色の背景（{project.background_color}）だけの"
                    f"上にあり、コントラスト比 {contrast:.1f} で読めます"
                    f"（背景に画像や動画を敷くとき、alpha=True・連番PNG の透過出力で"
                    f"別の絵に重ねるときは border=3 等を付けてください）"))
                continue
            findings.append(_finding(
                "warning", "text-no-decoration",
                f"{_obj_label(obj)}: 縁取り(border)・影(shadow)・下地(box)の"
                f"いずれも無く、背景に溶けて読めなくなりがちです"
                f"（例: border=3, border_color='black'）"))


# --- 位置（画面外配置）-----------------------------------------------------

def _sample_param(param):
    """Expr/定数の位置パラメータを u=0,0.2,…,1 の6点で数値評価する。

    数値化できない（未対応関数・未知変数など）場合は None を返し、
    呼び出し側は「判定不能＝報告しない」に倒す。
    """
    if param is None:
        return None
    if isinstance(param, (int, float)) and not isinstance(param, bool):
        return [float(param)] * len(_SAMPLE_US)
    eval_at = getattr(param, "eval_at", None)
    if eval_at is None:
        return None
    values = []
    for u in _SAMPLE_US:
        try:
            v = eval_at(u)
        except Exception:
            return None
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return None
        v = float(v)
        if v != v or v in (float("inf"), float("-inf")):  # NaN/Inf は判定不能
            return None
        values.append(v)
    return values


def _placement_params(obj):
    """検査対象の位置パラメータを [(軸名, param), ...] で返す。

    テキストは _text_spec の x/y、それ以外は move Effect の x/y を見る
    （move が無ければ中央配置なので検査不要）。
    """
    spec = getattr(obj, "_text_spec", None)
    if spec is not None and spec.get("kind") in _DRAWTEXT_KINDS:
        return [("x", spec.get("x")), ("y", spec.get("y"))]
    move_effect = None
    for e in getattr(obj, "effects", []):
        if getattr(e, "name", None) == "move":
            move_effect = e  # 最後の move が有効（_build_move_exprs と同じ規則）
    if move_effect is None:
        return []
    params = getattr(move_effect, "params", {}) or {}
    return [(axis, params.get(axis)) for axis in ("x", "y") if axis in params]


def _px_hint(axis, values, frame_px):
    """px を比率のつもりで渡した疑いがあれば、換算値つきのヒント文を返す"""
    for v in values:
        if v >= _PX_SUSPECT_MIN and abs(v - round(v)) < 1e-6 and frame_px:
            return (f"px を渡していませんか（{axis}={v:g} → "
                    f"{axis}={v / frame_px:.3g}）。")
    return ""


def _format_samples(values):
    """サンプル値を表示用の文字列にする（定数なら1値・アニメなら範囲）"""
    lo, hi = min(values), max(values)
    if abs(hi - lo) < 1e-9:
        return f"{lo:g}"
    return f"{lo:g}〜{hi:g}"


def _audit_placement(project, objects, findings):
    """x/y が 0..1 の比率の外に出ている（＝画面に映らない）配置を検出する。

    Expr は 6 点サンプルし、全点が範囲外のときだけ報告する
    （スライドイン等で一部が画面外になるのは正常な演出のため）。
    """
    for obj in objects:
        for axis, param in _placement_params(obj):
            values = _sample_param(param)
            if not values:
                continue
            if all(v < 0 for v in values):
                side = "左" if axis == "x" else "上"
            elif all(v > 1 for v in values):
                side = "右" if axis == "x" else "下"
            else:
                continue
            frame_px = project.width if axis == "x" else project.height
            findings.append(_finding(
                "warning", "offscreen-placement",
                f"{_obj_label(obj)}: {axis}={_format_samples(values)} は"
                f"画面の{side}外で、この要素は動画に映りません。"
                f"{axis} は 0〜1 のキャンバス比率で指定してください"
                f"（0=左上、1=右下、中央は0.5）。"
                f"{_px_hint(axis, values, frame_px)}"))


# --- 文字のはみ出し ---------------------------------------------------------

def _estimated_text_width(content, size):
    """size×(全角数 + 0.5×半角数) の粗い描画幅推定（改行は最長行を採る）"""
    widest = 0.0
    for line in str(content).split("\n"):
        units = 0.0
        for ch in line:
            if unicodedata.east_asian_width(ch) in ("F", "W"):
                units += 1.0
            else:
                units += _HALFWIDTH_RATIO
        widest = max(widest, units)
    return widest * size


def _overflow_content(spec):
    """はみ出し推定に使う文字列（counter は前後リテラル＋桁数から組み立てる）"""
    kind = spec.get("kind")
    if kind in ("text", "typewriter"):
        return str(spec.get("content", ""))
    if kind == "counter":
        digits = spec.get("width") or len(str(int(
            getattr(spec.get("to"), "value", 0) or 0))) or 1
        return f"{spec.get('prefix', '')}{'0' * int(digits)}{spec.get('suffix', '')}"
    return ""


def _audit_text_overflow(project, objects, findings):
    """推定描画幅がフレーム幅（safe area 5% 差引）を超える文字を検出する"""
    frame_w = project.width or 1920
    safe_w = frame_w * _SAFE_AREA_RATIO
    for obj in objects:
        spec = getattr(obj, "_text_spec", None)
        if spec is None or spec.get("kind") not in _DRAWTEXT_KINDS:
            continue
        size = getattr(spec.get("size"), "value", None)
        if not isinstance(size, (int, float)) or isinstance(size, bool):
            continue
        content = _overflow_content(spec)
        if not content:
            continue
        est = _estimated_text_width(content, float(size))
        if est <= safe_w * _OVERFLOW_TOLERANCE:
            continue
        findings.append(_finding(
            "warning", "text-overflow",
            f"{_obj_label(obj)}: 推定描画幅 {est:.0f}px が安全域 {safe_w:.0f}px"
            f"（フレーム幅 {frame_w}px の {_SAFE_AREA_RATIO:.0%}）を超え、"
            f"左右がはみ出して読めなくなります"
            f"（改行 \\n で分割するか size を {safe_w / est * size:.0f}px 以下へ）"))


# --- 画像の中の文字（text_image）---------------------------------------------

def _text_image_scale(obj):
    """text_image の画面上の倍率 (横, 縦)。画像は等倍で置かれるので、
    resize（Transform）と scale（Effect）の倍率を掛け合わせる。

    - チェックポイントで焼かれた op は Object から外れるので、焼く前の控え
      （_pre_checkpoint_ops）があればそちらを見る
    - scale がアニメーションのときは 6 点サンプルの**最大**を採る（ポップイン等で
      一瞬小さいのは正常。いちばん大きい時点で読めるかを見る）
    - 数値化できない倍率は 1 とみなす（判定不能を warning にしない）
    - compute() / from_project で素材化した後の倍率は追わない
    """
    ops = getattr(obj, "_pre_checkpoint_ops", None)
    transforms, effects = ops if ops else (obj.transforms, obj.effects)
    sx = sy = 1.0
    for t in transforms:
        if getattr(t, "name", None) != "resize":
            continue
        params = getattr(t, "params", {}) or {}
        fx, fy = params.get("sx", 1), params.get("sy", 1)
        if all(isinstance(v, (int, float)) and not isinstance(v, bool)
               for v in (fx, fy)):
            sx *= float(fx)
            sy *= float(fy)
    for e in effects:
        if getattr(e, "name", None) != "scale":
            continue
        values = _sample_param((getattr(e, "params", {}) or {}).get("value"))
        if values:
            sx *= max(values)
            sy *= max(values)
    return sx, sy


def _text_image_is_rotated(obj):
    """text_image が回転されていて、画面上の幅を倍率だけでは求められないか。

    rotate（Transform）が 0 / 180 度の整数倍以外、または rotate_to（Effect。
    アニメーション）があれば True。数値化できない角度も True（判定不能は
    報告しない、という _text_image_scale と同じ方針）。
    """
    ops = getattr(obj, "_pre_checkpoint_ops", None)
    transforms, effects = ops if ops else (obj.transforms, obj.effects)
    for t in transforms:
        if getattr(t, "name", None) != "rotate":
            continue
        # rotate() の rad は定数の Expr（時間依存の式は構築時に拒否される）
        values = _sample_param((getattr(t, "params", {}) or {}).get("rad"))
        if not values:
            return True
        half_turns = values[0] / math.pi
        if abs(half_turns - round(half_turns)) > 1e-6:
            return True
    return any(getattr(e, "name", None) == "rotate_to" for e in effects)


def _audit_text_images(project, objects, findings):
    """text_image() の文字を、画面上の実寸で検査する。

    text() 系と同じ基準（text-too-small / text-no-decoration / text-overflow /
    font-missing-glyph）を、Object が申告した文字サイズ・装飾・寸法に当てる。
    画像の中の文字は ffmpeg からは見えないので、申告が無い画像（自前の PNG・
    動画・HTML）は従来どおり検査されない。
    """
    ratio = (project.height or 1080) / 1080.0
    min_px = _TEXT_MIN_PX_1080 * ratio
    body_px = _TEXT_BODY_PX_1080 * ratio
    frame_w = project.width or 1920
    safe_w = frame_w * _SAFE_AREA_RATIO
    for obj in objects:
        info = getattr(obj, "_text_image", None)
        if info is None:
            continue
        sx, sy = _text_image_scale(obj)
        scaled = abs(sx - 1.0) > 1e-9 or abs(sy - 1.0) > 1e-9
        size = float(info.get("size_min", info.get("size", 0))) * sy
        note = (f"（text_image の size={info.get('size_min'):g}px × 倍率 {sy:g}）"
                if scaled else "")
        if size < min_px:
            findings.append(_finding(
                "warning", "text-too-small",
                f"{_obj_label(obj)}: 画面上の文字サイズ {size:.0f}px は小さすぎます{note}"
                f"（{project.height}p では {min_px:.0f}px 以上を推奨。"
                f"入らないときは文章を分割してください）"))
        elif size < body_px:
            findings.append(_finding(
                "info", "text-too-small",
                f"{_obj_label(obj)}: 画面上の文字サイズ {size:.0f}px は本文には小さめです{note}"
                f"（{project.height}p の本文目安は {body_px:.0f}px 以上）"))
        shadow = tuple(info.get("shadow", (0, 0)))
        if (not info.get("border") and shadow == (0, 0)
                and not info.get("shadow_blur") and not info.get("background")):
            findings.append(_finding(
                "warning", "text-no-decoration",
                f"{_obj_label(obj)}: 縁取り(border)・影(shadow)・下地(background)の"
                f"いずれも無く、背景に溶けて読めなくなりがちです"
                f"（例: border=3, border_color='black'）"))
        width = float(info.get("content_width", 0)) * sx
        # 回転した文字は画面上の幅が倍率だけでは決まらないので、はみ出しは判定しない
        # （90 度回した縦長の文字に「幅が安全域を超える」と誤報しない）
        if (width > safe_w * _OVERFLOW_TOLERANCE
                and not _text_image_is_rotated(obj)):
            findings.append(_finding(
                "warning", "text-overflow",
                f"{_obj_label(obj)}: 文字の幅 {width:.0f}px が安全域 {safe_w:.0f}px"
                f"（フレーム幅 {frame_w}px の {_SAFE_AREA_RATIO:.0%}）を超え、"
                f"左右がはみ出して読めなくなります"
                f"（max_width={safe_w:.0f} で折り返すか、改行 \\n で分割するか、"
                f"size を下げてください）"))
        missing = info.get("missing") or []
        if missing:
            shown = "".join(missing[:5]) + ("…" if len(missing) > 5 else "")
            findings.append(_finding(
                "warning", "font-missing-glyph",
                f"{_obj_label(obj)}: フォントに '{shown}' のグリフが無く、"
                f"豆腐（□）で描画されています"
                f"（その字を持つフォントを font= か区間の書式で指定してください）"))


# --- 表示区間 ---------------------------------------------------------------

def _project_total_duration(project):
    """audit 時点で参照できる総尺（未確定なら構成から算出する）。

    Project の private な内部（_configured_duration / _calc_total_duration）へ
    ダックタイピングで依存するが、**例外は握り潰さない**。以前はここで全例外を
    捨てて 0.0 を返しており、0 は _audit_outside_duration の早期 return 条件
    なので、Project 側を改名した瞬間に「例外も警告も出ないまま総尺系の検査だけが
    黙って無効化される」状態になっていた。静かに間違うより爆発させる方針に合わせ、
    想定外の例外はそのまま呼び出し側へ伝播させる（private 属性に getattr の
    フォールバックを置かないのも同じ理由）。
    """
    total = getattr(project, "duration", None)
    if total:
        return float(total)
    if project._configured_duration:
        return float(project._configured_duration)
    return float(project._calc_total_duration())


def _audit_outside_duration(project, objects, findings):
    """表示区間が動画の総尺と交差しない（＝一切映らない）配置を検出する"""
    total = _project_total_duration(project)
    if not total:
        return
    for obj in objects:
        start = getattr(obj, "start_time", 0) or 0
        if start >= total:
            findings.append(_finding(
                "warning", "outside-duration",
                f"{_obj_label(obj)}: 開始 {start:g}秒 は動画の総尺 {total:g}秒 "
                f"以降なので一度も表示されません"
                f"（@ の絶対配置時刻を見直すか、configure(duration=…) で"
                f"総尺を伸ばしてください）"))
            continue
        if start < 0:
            dur = project._resolve_obj_duration(obj)
            if start + dur <= 0:
                findings.append(_finding(
                    "warning", "outside-duration",
                    f"{_obj_label(obj)}: 表示区間 {start:g}〜{start + dur:g}秒 が"
                    f"動画の開始(0秒)より前で終わるため一度も表示されません"
                    f"（開始時刻を 0 以上にしてください）"))


# --- フォントのグリフ被覆 ---------------------------------------------------

# 日本語（CJK）としてグリフ被覆を検査するコードポイント範囲
_CJK_RANGES = (
    (0x3000, 0x303F),    # CJK 記号・句読点
    (0x3040, 0x309F),    # ひらがな
    (0x30A0, 0x30FF),    # カタカナ
    (0x3400, 0x4DBF),    # CJK 統合漢字 拡張A
    (0x4E00, 0x9FFF),    # CJK 統合漢字
    (0xF900, 0xFAFF),    # CJK 互換漢字
    (0xFF00, 0xFF60),    # 全角英数・記号
    (0xFF61, 0xFF9F),    # 半角カタカナ
    (0x20000, 0x2FA1F),  # CJK 統合漢字 拡張B以降
)

# フォントパス -> 被覆範囲リスト（解析不能は None）のプロセス内キャッシュ
_CMAP_CACHE = {}

# 異常なヘッダで巨大ループに入らないための上限
_MAX_CMAP_GROUPS = 200000


def _is_cjk(ch):
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _cmap_format4_ranges(data, off):
    """cmap format 4（BMP）の被覆範囲を返す。

    グリフID=0 の穴までは追わず start..end をそのまま採る。被覆を広めに
    見積もる方向の誤差なので、過検出（本当は在るのに「無い」と言う）は起きない。
    """
    seg_x2 = struct.unpack_from(">H", data, off + 6)[0]
    seg = seg_x2 // 2
    if seg <= 0 or seg > _MAX_CMAP_GROUPS:
        return None
    end_off = off + 14
    start_off = end_off + seg_x2 + 2
    out = []
    for i in range(seg):
        end = struct.unpack_from(">H", data, end_off + 2 * i)[0]
        start = struct.unpack_from(">H", data, start_off + 2 * i)[0]
        if start == 0xFFFF or start > end:  # 末尾の番兵セグメント
            continue
        out.append((start, min(end, 0xFFFE)))
    return out


def _cmap_format12_ranges(data, off):
    """cmap format 12（BMP外を含む）の被覆範囲を返す"""
    n = struct.unpack_from(">I", data, off + 12)[0]
    if n <= 0 or n > _MAX_CMAP_GROUPS:
        return None
    out = []
    for i in range(n):
        start, end, _gid = struct.unpack_from(">III", data, off + 16 + 12 * i)
        if start > end:
            continue
        out.append((start, end))
    return out


def _parse_cmap_ranges(data):
    """TTF/OTF/TTC のバイト列から cmap の被覆範囲を読む（標準ライブラリのみ）。

    .ttc はコレクション先頭のフォントだけを見る（既定候補の meiryo.ttc /
    NotoSansCJK.ttc とも先頭が本体なので実用上これで足りる）。
    """
    if len(data) < 12:
        return None
    base = 0
    if data[:4] == b"ttcf":
        if struct.unpack_from(">I", data, 8)[0] < 1:
            return None
        base = struct.unpack_from(">I", data, 12)[0]
    num_tables = struct.unpack_from(">H", data, base + 4)[0]
    cmap_off = None
    for i in range(num_tables):
        rec = base + 12 + 16 * i
        if data[rec:rec + 4] == b"cmap":
            cmap_off = struct.unpack_from(">I", data, rec + 8)[0]
            break
    if cmap_off is None:
        return None
    best = None
    for i in range(struct.unpack_from(">H", data, cmap_off + 2)[0]):
        rec = cmap_off + 4 + 8 * i
        pid, eid = struct.unpack_from(">HH", data, rec)
        sub = cmap_off + struct.unpack_from(">I", data, rec + 4)[0]
        fmt = struct.unpack_from(">H", data, sub)[0]
        if (pid, eid) == (3, 10) and fmt == 12:
            prio = 3
        elif (pid, eid) == (3, 1) and fmt == 4:
            prio = 2
        elif pid == 0 and fmt in (4, 12):
            prio = 1
        else:
            continue
        if best is None or prio > best[0]:
            best = (prio, sub, fmt)
    if best is None:
        return None
    _prio, sub, fmt = best
    ranges = (_cmap_format4_ranges(data, sub) if fmt == 4
              else _cmap_format12_ranges(data, sub))
    if not ranges:
        return None
    return sorted(ranges)


def _font_coverage(path):
    """フォントの被覆範囲（ソート済み [(start, end), ...]）。解析不能なら None"""
    key = str(path)
    if key in _CMAP_CACHE:
        return _CMAP_CACHE[key]
    ranges = None
    try:
        with open(key, "rb") as f:
            ranges = _parse_cmap_ranges(f.read())
    except Exception:
        ranges = None
    _CMAP_CACHE[key] = ranges
    return ranges


def _covers(ranges, cp):
    """被覆範囲にコードポイントが含まれるか（ソート済み前提の二分探索）"""
    i = bisect.bisect_right(ranges, (cp, float("inf"))) - 1
    return i >= 0 and ranges[i][0] <= cp <= ranges[i][1]


def _audit_font_glyphs(objects, findings):
    """日本語を含む文字に、その文字を持たないフォントが指定されていないか。

    drawtext は cmap に無い文字を無言で豆腐（□）にするため、レンダは成功する。
    フォントを解析できない場合は報告しない（判定不能を warning にしない）。
    """
    for obj in objects:
        spec = getattr(obj, "_text_spec", None)
        if spec is None or spec.get("kind") not in _DRAWTEXT_KINDS:
            continue
        font = spec.get("font")
        if not font:
            continue
        content = str(spec.get("content", "")) + str(spec.get("prefix", "")) \
            + str(spec.get("suffix", ""))
        targets = sorted({ch for ch in content if _is_cjk(ch)})
        if not targets:
            continue
        ranges = _font_coverage(font)
        if not ranges:
            continue
        missing = [ch for ch in targets if not _covers(ranges, ord(ch))]
        if not missing:
            continue
        shown = "".join(missing[:5]) + ("…" if len(missing) > 5 else "")
        findings.append(_finding(
            "warning", "font-missing-glyph",
            f"{_obj_label(obj)}: フォント {os.path.basename(font)} に "
            f"'{shown}' のグリフが無く、豆腐（□）で描画されます"
            f"（日本語対応フォントを font= か環境変数 SCRIPTVEDIT_FONT で"
            f"指定してください。例: 'C:/Windows/Fonts/meiryo.ttc'）"))


def _audio_window(project, obj):
    """音声オブジェクトの再生区間 (start, end) を返す"""
    start = getattr(obj, "start_time", 0) or 0
    dur = project._resolve_obj_duration(obj)
    return start, start + dur


def _has_time_shift(obj):
    """発音区間の時刻をずらす時間系の op（切り出し・速度・繰り返し・ループ）を持つか"""
    return (any(getattr(e, "name", None) in _TIME_SHIFT_EFFECTS
                for e in getattr(obj, "effects", []))
            or any(getattr(e, "name", None) in _TIME_SHIFT_AUDIO_EFFECTS
                   for e in getattr(obj, "audio_effects", [])))


def _sounding_intervals(project, obj):
    """音声が実際に鳴る区間のリスト [(s, e), ...]（タイムライン絶対秒・昇順・互いに素）。

    通常の音声は再生区間そのもの1つ。sfx() が作った Object は「開始0・尺は
    最後の at + 素材長」の1本だが、実際に鳴るのは各 at から素材長ぶんだけなので、
    発音区間（重なる発音は結合）で返す。0.3秒の効果音を2発置いただけで
    「7秒重なる」と数えないため。時間系の op が付いて発音区間の時刻がずれる
    場合は、再生区間1つで返す（数え過ぎる側へ倒す）。
    """
    start, end = _audio_window(project, obj)
    hits = getattr(obj, "_sfx_hits", None)
    if hits is None or _has_time_shift(obj):
        return [(start, end)]
    merged = []
    for rel_s, rel_e in sorted(hits):
        s = start + rel_s
        e = min(start + rel_e, end)
        if e <= s:
            continue
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _longest_overlap(a, b):
    """区間リスト a・b の重なりのうち、最も長い連続区間の秒数（重ならなければ 0 以下）"""
    best = 0.0
    for s1, e1 in a:
        for s2, e2 in b:
            best = max(best, min(e1, e2) - max(s1, s2))
    return best


def _is_backdrop(obj):
    """BGM 役（duck_under か loop を持つ＝他の音の下に敷く音声）か"""
    return any(getattr(e, "name", None) in ("duck_under", "loop")
               for e in getattr(obj, "audio_effects", []))


def _ducked_pairs(audio_objs):
    """duck_under で結ばれた音声の組（順不同）を frozenset({id(a), id(b)}) の集合で返す。

    a <= duck_under(b, c) なら {a,b} と {a,c}。どちらがどちらの下にダックして
    いても「重なりは処理済み」とみなす。
    """
    pairs = set()
    for obj in audio_objs:
        for e in getattr(obj, "audio_effects", []):
            if getattr(e, "name", None) != "duck_under":
                continue
            for other in _duck_targets(e):
                pairs.add(frozenset((id(obj), id(other))))
    return pairs


def _overlap_candidates(audio_objs):
    """重なりを調べる音声の組 [(a, b), ...] と、BGM 役が見つかったかを返す。

    BGM 役（duck_under / loop を持つ音声）が1つでもあれば「BGM 役 × それ以外」の
    組だけを調べ、BGM 役が duck_under の相手に入れている組は除く。
    ナレーション同士・ナレーションと効果音のような前景同士の重なりは
    「ナレーションが BGM に埋もれる」問題ではないので数えない（同じ duck_under の
    相手に並んだ n1・n2 が重なっても、その間 BGM は下がっている）。BGM 役同士
    （BGM と環境音の重ね敷き等）も同じ理由で数えない。
    BGM 役が1つも無いときはどれが BGM か分からないので全ての組を調べる
    （duck_under を1つも書いていない＝この警告が一番拾いたい構成）。
    """
    backdrops = [o for o in audio_objs if _is_backdrop(o)]
    if not backdrops:
        return [(audio_objs[i], audio_objs[j])
                for i in range(len(audio_objs))
                for j in range(i + 1, len(audio_objs))], False
    ducked = _ducked_pairs(audio_objs)
    back_ids = {id(o) for o in backdrops}
    pairs = []
    for bgm in backdrops:
        for fg in audio_objs:
            if id(fg) in back_ids:
                continue
            if frozenset((id(bgm), id(fg))) in ducked:
                continue
            pairs.append((bgm, fg))
    return pairs, True


def _audit_audio(project, objects, findings):
    """音声構成: duck_under・ループ・BGM尺・normalize_audio"""
    audio_objs = [o for o in objects if getattr(o, "has_audio", False)]
    if not audio_objs:
        return

    # 重なり判定: 1秒以上続けて重なる「BGM 役と、それがダックしていない音声」の組を
    # 数える（組の選び方は _overlap_candidates）。以前は duck_under がどこかに
    # 1つでもあると検査を丸ごと飛ばしていたため、BGM が片方のナレーションにだけ
    # ダックしていると、もう片方との重なりが見逃された。
    if len(audio_objs) >= 2:
        pairs, has_backdrop = _overlap_candidates(audio_objs)
        intervals = {}
        overlaps = []
        for a, b in pairs:
            for o in (a, b):
                if id(o) not in intervals:
                    intervals[id(o)] = _sounding_intervals(project, o)
            d = _longest_overlap(intervals[id(a)], intervals[id(b)])
            if d >= _OVERLAP_MIN_SEC:
                overlaps.append((a, b, d))
        if overlaps:
            shown = "、".join(
                f"{_obj_label(a)} と {_obj_label(b)}（{d:.1f}秒）"
                for a, b, d in overlaps[:_OVERLAP_SHOW_MAX])
            if len(overlaps) > _OVERLAP_SHOW_MAX:
                shown += f" ほか{len(overlaps) - _OVERLAP_SHOW_MAX}組"
            if has_backdrop:
                hint = ("BGM がこの音の再生中に下がりません。BGM 側の duck_under に"
                        "相手を加えてください（無ければ付ける）。"
                        "例: bgm <= duck_under(n1, n2, n3)。duck_under は1つの音声に1回だけ")
            else:
                hint = ("ナレーションが BGM に埋もれます。例: bgm <= duck_under(narration_audio)。"
                        "相手が複数なら bgm <= duck_under(n1, n2, n3)")
            findings.append(_finding(
                "warning", "audio-overlap-no-duck",
                f"1秒以上重なるのに duck_under で処理されていない音声が"
                f" {len(overlaps)}組 あります: {shown}（{hint}）"))

    for obj in audio_objs:
        effects = list(getattr(obj, "audio_effects", []))
        looped = any(getattr(e, "name", None) == "loop" for e in effects)
        ducks = any(getattr(e, "name", None) == "duck_under" for e in effects)
        if looped:
            findings.append(_finding(
                "info", "bgm-loop",
                f"{_obj_label(obj)}: loop() はつなぎ目が人間に気付かれやすいです"
                f"（動画より長い曲を選ぶのが確実）"))
        elif ducks:
            # duck_under を持つ音声＝BGM相当。実尺が表示区間より短いと途中で切れる
            try:
                actual = obj.length()
            except Exception:
                continue
            start, end = _audio_window(project, obj)
            window = end - start
            if actual is not None and window and actual + 0.05 < window:
                findings.append(_finding(
                    "warning", "bgm-too-short",
                    f"{_obj_label(obj)}: 実尺 {actual:.1f}秒 が表示区間 "
                    f"{window:.1f}秒 より短く、途中で無音になります"
                    f"（長い曲にするか loop() を検討）"))

    if project._loudnorm_target is None:
        findings.append(_finding(
            "info", "no-normalize-audio",
            "normalize_audio() が未設定です（ラウドネス正規化。"
            "投稿先の音量基準に合わせるなら p.normalize_audio() を推奨）"))


def _audit_web(objects, findings):
    """Web/Canvas内部を静的audit済みと誤認しないための明示的なinfo。"""
    web_objects = [obj for obj in objects if getattr(obj, "_web_source", None)]
    if not web_objects:
        return
    findings.append(_finding(
        "info", "web-content-uninspected",
        f"Web/Canvas Objectが{len(web_objects)}件あります。Canvas/DOM内部の文字サイズ・"
        "重なり・safe areaは静的auditの対象外です。"
        "p.storyboard('board.png')で代表フレームを一括確認するか、"
        "完成動画がある場合はsource=を指定して高速確認してください"))


def _audit_morph(objects, findings):
    """morph_to（sdf）が実質クロスフェードになる組を報告する。

    焼いた後の Object は effects から morph_to が消えているので、チェックポイントの
    計画が刻んだ _terminal_bake（op, 入力画像）から元の2枚を読む。
    判定は morph.py の diagnose_sdf_morph（生成時の警告と同じ関数）。
    判定不能（PIL / numpy / OpenCV が無い・入力が未生成の中間物・読めない画像）は
    報告しない。
    """
    for obj in objects:
        bake = getattr(obj, "_terminal_bake", None)
        if bake is None:
            continue
        op, src = bake
        if getattr(op, "name", None) != "morph_to":
            continue
        target = getattr(getattr(op, "_morph_target", None), "source", None)
        if not (target and os.path.isfile(str(src)) and os.path.isfile(str(target))):
            continue
        try:
            from scriptvedit import morph as _morph
        except ImportError:
            return
        params = {k: v for k, v in op.params.items() if k != "blend"}
        if _morph._resolve_method(params) != "sdf":
            continue
        try:
            reason = _morph.diagnose_sdf_morph(
                str(src), str(target), align=params.get("align", True),
                fit=params.get("fit"))
        except (OSError, ValueError):
            continue
        if reason:
            findings.append(_finding(
                "warning", "morph-sdf-crossfade",
                f"morph_to（{os.path.basename(str(src))} → "
                f"{os.path.basename(str(target))}）: {reason}"))


def audit_project(project, *, transparent=False):
    """Project を検査して findings のリストを返す（本体実装）。

    呼び出し時点で objects が未解決（layer登録のみ）の場合、呼び出し側の
    Project.audit() が dry_run で解決してから渡す。

    transparent: 出力が透過（alpha=True の webm / webp・連番 PNG）か。render() が
    出力先から決めて渡す。透過出力では background_color が使われないので、
    text-no-decoration を「単色の背景の上」として info へ格下げしない。
    単独の p.audit() は出力先を知らないので False（不透明な出力を仮定）。
    """
    findings = []
    objects = [o for o in project.objects
               if getattr(o, "media_type", None) is not None]
    _audit_quality_hints(objects, findings)
    _audit_text_readability(project, objects, findings, transparent)
    _audit_placement(project, objects, findings)
    _audit_text_overflow(project, objects, findings)
    _audit_outside_duration(project, objects, findings)
    _audit_font_glyphs(objects, findings)
    _audit_text_images(project, objects, findings)
    _audit_audio(project, objects, findings)
    _audit_web(objects, findings)
    _audit_morph(objects, findings)
    return findings


def format_report(findings):
    """findings を人間可読の日本語レポート文字列にする"""
    if not findings:
        return "audit: 指摘はありません ✓"
    lines = [f"audit: {sum(1 for f in findings if f['severity'] == 'warning')} warning / "
             f"{sum(1 for f in findings if f['severity'] == 'info')} info"]
    mark = {"warning": "⚠", "info": "・"}
    for f in findings:
        lines.append(f"  {mark.get(f['severity'], '?')} [{f['code']}] {f['message']}")
    return "\n".join(lines)
