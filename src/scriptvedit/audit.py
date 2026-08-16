# -*- coding: utf-8 -*-
"""p.audit(): 動画の品質lint（レンダ前チェック）。

過去の人間レビューで繰り返し指摘された品質問題と、`~` 品質ヒントの契約
（未対応opは通常処理・実行時警告は出さない→報告はaudit側に集約する）を
静的に検査する。エラーにはせず、findings のリストを返す。

ルール一覧（code / severity）:
  quality-hint-ignored (info)    … `~` を付けたが軽量代替の無いop（通常処理される）
  text-too-small (warning/info)  … 文字が小さい（1080p換算 32px未満=warning, 44px未満=info）
  text-no-decoration (warning)   … 縁取り・影・下地のいずれも無い文字（背景に溶ける）
  offscreen-placement (warning)  … x/y が 0..1 の比率の外（画面外で描画されない）
  text-overflow (warning)        … 推定描画幅がフレーム幅（safe area差引）を超える
  outside-duration (warning)     … 表示区間が動画の総尺と交差しない（一切映らない）
  font-missing-glyph (warning)   … 解決したフォントに日本語グリフが無い（豆腐になる）
  audio-overlap-no-duck (warning)… 音声が重なるのに duck_under が無い（ナレーションが埋もれる）
  bgm-loop (info)                … loop() 使用（短い曲のループは人間に気付かれやすい）
  bgm-too-short (warning)        … BGM（duck_underを持つ音声）の実尺が表示区間より短い
  no-normalize-audio (info)      … 音声があるのに normalize_audio() 未設定
  web-content-uninspected (info) … Canvas/DOM内部は静的lint対象外。storyboard確認を促す

severity の使い分け: warning=過去に人間レビューで実際に差し戻された類、
info=判断が分かれる・意図的な場合もある注意喚起。

過検出を避ける方針: 判定不能（Expr が数値評価できない・フォントが解析できない等）は
「報告しない」側へ倒す。位置は 6 点サンプルの全点が画面外のときだけ報告する
（スライドイン/アウトの一部が画面外なのは正常なため）。
"""

import bisect
import os
import struct
import unicodedata

from scriptvedit.cache import _respects_fast_hint


# 文字サイズの目安（1080p基準。人間レビュー由来: 本文44px以上・注釈32px以上）
_TEXT_MIN_PX_1080 = 32
_TEXT_BODY_PX_1080 = 44

# 音声の重なり判定のしきい値[秒]（SFXの一瞬の重なりまで警告しない）
_OVERLAP_MIN_SEC = 1.0

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


def _audit_text_readability(project, objects, findings):
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


# --- 表示区間 ---------------------------------------------------------------

def _project_total_duration(project):
    """audit 時点で参照できる総尺（未確定なら構成から算出する）"""
    total = getattr(project, "duration", None)
    if total:
        return float(total)
    total = getattr(project, "_configured_duration", None)
    if total:
        return float(total)
    try:
        return float(project._calc_total_duration())
    except Exception:
        return 0.0


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


def _audit_audio(project, objects, findings):
    """音声構成: duck_under・ループ・BGM尺・normalize_audio"""
    audio_objs = [o for o in objects if getattr(o, "has_audio", False)]
    if not audio_objs:
        return

    has_duck = any(
        any(getattr(e, "name", None) == "duck_under"
            for e in getattr(o, "audio_effects", []))
        for o in audio_objs)

    # 重なり判定（duck_under がどこにも無い場合のみ）
    if len(audio_objs) >= 2 and not has_duck:
        windows = [_audio_window(project, o) for o in audio_objs]
        for i in range(len(audio_objs)):
            for j in range(i + 1, len(audio_objs)):
                s = max(windows[i][0], windows[j][0])
                e = min(windows[i][1], windows[j][1])
                if e - s >= _OVERLAP_MIN_SEC:
                    findings.append(_finding(
                        "warning", "audio-overlap-no-duck",
                        f"{_obj_label(audio_objs[i])} と "
                        f"{_obj_label(audio_objs[j])} が {e - s:.1f}秒 重なるのに"
                        f" duck_under がありません（ナレーションが BGM に埋もれます。"
                        f"例: bgm <= duck_under(narration_audio)）"))
                    break
            else:
                continue
            break

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


def audit_project(project):
    """Project を検査して findings のリストを返す（本体実装）。

    呼び出し時点で objects が未解決（layer登録のみ）の場合、呼び出し側の
    Project.audit() が dry_run で解決してから渡す。
    """
    findings = []
    objects = [o for o in project.objects
               if getattr(o, "media_type", None) is not None]
    _audit_quality_hints(objects, findings)
    _audit_text_readability(project, objects, findings)
    _audit_placement(project, objects, findings)
    _audit_text_overflow(project, objects, findings)
    _audit_outside_duration(project, objects, findings)
    _audit_font_glyphs(objects, findings)
    _audit_audio(project, objects, findings)
    _audit_web(objects, findings)
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
