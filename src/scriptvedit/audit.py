# -*- coding: utf-8 -*-
"""p.audit(): 動画の品質lint（レンダ前チェック）。

過去の人間レビューで繰り返し指摘された品質問題と、`~` 品質ヒントの契約
（未対応opは通常処理・実行時警告は出さない→報告はaudit側に集約する）を
静的に検査する。エラーにはせず、findings のリストを返す。

ルール一覧（code / severity）:
  quality-hint-ignored (info)    … `~` を付けたが軽量代替の無いop（通常処理される）
  text-too-small (warning/info)  … 文字が小さい（1080p換算 32px未満=warning, 44px未満=info）
  text-no-decoration (warning)   … 縁取り・影・下地のいずれも無い文字（背景に溶ける）
  audio-overlap-no-duck (warning)… 音声が重なるのに duck_under が無い（ナレーションが埋もれる）
  bgm-loop (info)                … loop() 使用（短い曲のループは人間に気付かれやすい）
  bgm-too-short (warning)        … BGM（duck_underを持つ音声）の実尺が表示区間より短い
  no-normalize-audio (info)      … 音声があるのに normalize_audio() 未設定
  web-content-uninspected (info) … Canvas/DOM内部は静的lint対象外。storyboard確認を促す

severity の使い分け: warning=過去に人間レビューで実際に差し戻された類、
info=判断が分かれる・意図的な場合もある注意喚起。
"""

from scriptvedit.cache import _respects_fast_hint


# 文字サイズの目安（1080p基準。人間レビュー由来: 本文44px以上・注釈32px以上）
_TEXT_MIN_PX_1080 = 32
_TEXT_BODY_PX_1080 = 44

# 音声の重なり判定のしきい値[秒]（SFXの一瞬の重なりまで警告しない）
_OVERLAP_MIN_SEC = 1.0


def _finding(severity, code, message):
    return {"severity": severity, "code": code, "message": message}


def _obj_label(obj):
    """finding 表示用のオブジェクト名（ソース名 or テキスト内容の先頭）"""
    spec = getattr(obj, "_text_spec", None)
    if spec is not None:
        content = str(spec.get("content", spec.get("format", "")))
        short = content[:20] + ("…" if len(content) > 20 else "")
        return f"{spec.get('kind', 'text')}('{short}')"
    import os
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
