# -*- coding: utf-8 -*-

import os
import re
import sys
import hashlib
import builtins as _builtins

# context は scriptvedit 内 import を持たない葉なので先頭で import できる。
from scriptvedit.context import current_project

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.expr import Const, Var, _resolve_param, round, sign
from scriptvedit.state import _ARTIFACT_DIR, _TEXT_ANCHORS
from scriptvedit.validate import _require_number, _validate_ffmpeg_color


# --- テキスト/字幕（drawtext・subtitles）ヘルパー ---

# 日本語表示用フォントの既定候補（OS別）。実行OSのグループを先頭に並べ替えて
# 先頭から存在するものを採用する（他OSの候補も残す: コンテナ等で判定が
# 実態とずれても救済できるように）。
_FONT_CANDIDATES_BY_OS = {
    "windows": [
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/YuGothM.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
        "C:/Windows/Fonts/msmincho.ttc",
    ],
    "linux": [
        # Noto Sans CJK（Debian/Ubuntu: fonts-noto-cjk）
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        # Fedora / RHEL 系（google-noto-sans-cjk-jp-fonts）
        "/usr/share/fonts/google-noto-sans-cjk-fonts/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
        # Arch（noto-fonts-cjk）
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        # openSUSE
        "/usr/share/fonts/truetype/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        # IPAゴシック（Debian/Ubuntu: fonts-ipafont-gothic）
        "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    ],
    "darwin": [
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ],
}

# フォントが見つからないときの OS 別導入例（エラーメッセージ用）
_FONT_INSTALL_HINTS = {
    "windows": "Windows: 通常 C:/Windows/Fonts/meiryo.ttc が標準で存在します",
    "linux": ("Linux: sudo apt install fonts-noto-cjk (Debian/Ubuntu) / "
              "sudo dnf install google-noto-sans-cjk-jp-fonts (Fedora) / "
              "sudo pacman -S noto-fonts-cjk (Arch)"),
    "darwin": "macOS: ヒラギノが標準搭載です（/System/Library/Fonts/ 配下）",
}


def _platform_key():
    """実行OSを windows / linux / darwin のいずれかに分類する"""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def _ordered_font_candidates(os_key=None):
    """既定フォント候補を、指定OS（省略時は実行OS）のグループを先頭に並べて返す"""
    key = os_key if os_key is not None else _platform_key()
    ordered = list(_FONT_CANDIDATES_BY_OS.get(key, []))
    for k, cands in _FONT_CANDIDATES_BY_OS.items():
        if k != key:
            ordered.extend(cands)
    return ordered


# 後方参照用のフラットな候補リスト（実行OSの候補が先頭）
_DEFAULT_FONT_CANDIDATES = _ordered_font_candidates()


def _resolve_font(font):
    """フォントパスを解決。font省略時は環境変数 SCRIPTVEDIT_FONT →
    既定候補（実行OSの候補を優先）の順で存在するものを返す。
    見つからない場合は OS 別の導入例を含む日本語エラーで案内する。"""
    if font is not None:
        path = font.replace("\\", "/")
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"指定フォントが見つかりません: {font}\n"
                f"日本語表示には .ttc/.ttf の実在パスを指定してください "
                f"(例: Windows 'C:/Windows/Fonts/meiryo.ttc' / "
                f"Linux '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc' / "
                f"macOS '/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc')")
        return path
    env_font = os.environ.get("SCRIPTVEDIT_FONT")
    if env_font:
        path = env_font.replace("\\", "/")
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"環境変数 SCRIPTVEDIT_FONT のフォントが見つかりません: {env_font}\n"
                f"実在する .ttc/.ttf のパスを設定するか、変数を解除してください。")
        return path
    for cand in _DEFAULT_FONT_CANDIDATES:
        if os.path.exists(cand):
            return cand
    raise FileNotFoundError(
        "既定の日本語フォントが見つかりませんでした。\n"
        "システムに日本語フォントを導入するか、font= または環境変数 "
        "SCRIPTVEDIT_FONT で実在するフォントパスを明示してください。\n"
        f"導入例 — {_FONT_INSTALL_HINTS['linux']}\n"
        f"       — {_FONT_INSTALL_HINTS['windows']}\n"
        f"       — {_FONT_INSTALL_HINTS['darwin']}\n"
        f"探索した候補: {', '.join(_DEFAULT_FONT_CANDIDATES)}")


def _escape_ffpath(path):
    """フィルタグラフのファイルパスをエスケープしてクォート。

    パス区切りとドライブレターに加え、単一引用符は一度クォートを閉じて
    filtergraph/AVOption の2段階を越える形でエスケープする。
    fontfile / subtitles=filename / lut3d=file 用。
    """
    p = path.replace("\\", "/").replace(":", "\\:")
    p = p.replace("'", r"'\\\''")
    return f"'{p}'"


def _escape_textfile_content(s):
    """drawtext textfile の中身用エスケープ。ファイル内容には filtergraph の
    引用符/区切りは作用せず、drawtext のテキスト展開(% と \\)のみ効くため、
    \\→\\\\ と %→\\% だけをエスケープすればよい（:や'はそのまま literal 表示）。
    実測で確認済み（単一引用符 inline は ' の literal 化が描画されず不可）。"""
    return s.replace("\\", "\\\\").replace("%", "\\%")


def _ensure_textfile(content):
    """テキスト内容を content-addressed なキャッシュファイルに書き出しパスを返す。
    drawtext の textfile= で参照する。任意の文字（'、:、% 等）を確実に表示できる。

    書き込みは必ず _atomic_write_text（tmp→os.replace）経由で行い、
    「存在すればスキップ」ガードは置かない。ファイル名が内容ハッシュなので、
    中断/ディスクフルで切り詰められた残骸が一度でも出来ると以後どのレンダでも
    再生成されず、drawtext が途中までのテキストを黙って描き続けるため
    （並列レイヤーからの同時到達も原子的書き込みで無害化する）。"""
    body = _escape_textfile_content(content)
    key = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    path = os.path.join(_ARTIFACT_DIR, "text", f"{key}.txt")
    _atomic_write_text(path, body)
    return path


def _escape_counter_literal(s):
    """counter の format 前後リテラル（inline text= 内）用エスケープ。
    inline 値は単一引用符で包むが、FFmpeg 8.0 では引用符内でも : , が区切り
    扱いになるためエスケープする。' は inline では確実に描画できないため拒否。"""
    if "'" in s:
        raise ValueError(
            "counter: format のリテラル部分にアポストロフィ(')は使用できません。"
            "アポストロフィを含む固定文字は text() を併用してください。")
    return (s.replace("\\", "\\\\").replace("%", "\\%")
             .replace(":", "\\:").replace(",", "\\,"))


def _validate_text_size(func, size_expr):
    """size は定数のみ許可。FFmpeg 8.0 の drawtext は fontsize を式にすると
    SEGV(0xC0000005)する（copy/setsar バリアでも回避不可・実測）。
    x/y/alpha のアニメーションは安全に利用できる。"""
    if not isinstance(size_expr, Const):
        raise ValueError(
            f"{func}: size は定数のみ対応です（アニメーション不可）。\n"
            f"FFmpeg 8.0 の drawtext は fontsize を式にすると SEGV するため、"
            f"サイズ変化は非対応です。x/y/alpha はアニメーション可能です。")
    return size_expr


def _validate_text_border_shadow(func, border, shadow):
    """縁取り/影パラメータの検証。(border_int, (shadow_x, shadow_y)) を返す。
    border: 縁取り太さpx（0以上の定数）。shadow: (x, y) の影オフセットpx
    （負値可・定数のみ）。drawtext の borderw/shadowx/shadowy は整数pxのため
    int に丸めて返す。"""
    _require_number(func, "border", border, 0, None)
    if not isinstance(shadow, (tuple, list)) or len(shadow) != 2:
        raise ValueError(
            f"{func}: shadow は (x, y) の2要素タプルで指定してください "
            f"(例: shadow=(2, 2)): {shadow!r}")
    sx, sy = shadow
    _require_number(func, "shadow[0]", sx)
    _require_number(func, "shadow[1]", sy)
    return int(border), (int(sx), int(sy))


def _text_deco_spec(func, border, border_color, shadow, shadow_color):
    """縁取り/影の spec 断片と synthetic_source 鍵の断片を返す。
    既定値（border=0 かつ shadow=(0,0)）では鍵断片は空文字列
    （既存スペックの合成ソースハッシュを変えない＝キャッシュ互換維持）。"""
    b, sh = _validate_text_border_shadow(func, border, shadow)
    border_color = _validate_ffmpeg_color(func, border_color)
    shadow_color = _validate_ffmpeg_color(func, shadow_color)
    frag = {"border": b, "border_color": border_color,
            "shadow": sh, "shadow_color": shadow_color}
    if b == 0 and sh == (0, 0):
        return frag, ""
    return frag, f"|bd{b}|{border_color}|sh{sh[0]},{sh[1]}|{shadow_color}"


def _text_size_opt(size_expr, u_expr):
    """fontsize オプション文字列を返す（size は定数のみ・_validate_text_size で担保）"""
    return f"fontsize={int(size_expr.value)}"


def _text_anchor_xy(x_expr, y_expr, u_expr, anchor, *, safe_area=None,
                    safe_padding=(0, 0, 0, 0)):
    """テキスト配置の x/y drawtext 式を返す。
    anchor='center': (frac*W - text_w/2, frac*H - text_h/2)
    anchor='left'  : (frac*W, frac*H)   ※左上基準
    x/y は 0..1 のキャンバス比率。"""
    xf = x_expr.to_ffmpeg(u_expr)
    yf = y_expr.to_ffmpeg(u_expr)
    if anchor == "left":
        raw_x, raw_y = f"({xf})*W", f"({yf})*H"
    else:
        raw_x = f"({xf})*W-text_w/2"
        raw_y = f"({yf})*H-text_h/2"

    if safe_area is not None:
        left, top, right, bottom = safe_area
        pad_left, pad_top, pad_right, pad_bottom = safe_padding
        # drawtextが実際に確定したtext_w/text_hでクランプするため、フォントや
        # 日本語/英数字の幅差に依存しない。カンマはfiltergraph用にescapeする。
        raw_x = (f"max({left}*W+{pad_left}\\,min({raw_x}\\,"
                 f"(1-{right})*W-text_w-{pad_right}))")
        raw_y = (f"max({top}*H+{pad_top}\\,min({raw_y}\\,"
                 f"(1-{bottom})*H-text_h-{pad_bottom}))")
    return f"x='{raw_x}'", f"y='{raw_y}'"


def _build_drawtext_filter(spec, text_opt, start, dur, *, enable=None):
    """1個の drawtext フィルタ文字列を構築（text/typewriter/counter 共通）。
    text_opt: 完成済みの "textfile=..." または "text=..." オプション文字列。"""
    u_expr = _u_expr(start, dur)
    font = _escape_ffpath(spec["font"])
    safe_padding = (0, 0, 0, 0)
    if spec.get("safe_area") is not None:
        box_pad = int(spec.get("box_border", 0)) if spec.get("box") else 0
        border_pad = int(spec.get("border", 0))
        sh_x, sh_y = spec.get("shadow", (0, 0))
        safe_padding = (
            box_pad + border_pad + _builtins.max(0, -sh_x),
            box_pad + border_pad + _builtins.max(0, -sh_y),
            box_pad + border_pad + _builtins.max(0, sh_x),
            box_pad + border_pad + _builtins.max(0, sh_y),
        )
    x_opt, y_opt = _text_anchor_xy(
        spec["x"], spec["y"], u_expr, spec["anchor"],
        safe_area=spec.get("safe_area"), safe_padding=safe_padding)
    opts = [f"fontfile={font}"]
    opts.append(text_opt)
    opts.append(_text_size_opt(spec["size"], u_expr))
    opts.append(f"fontcolor={spec['color']}")
    opts.append(x_opt)
    opts.append(y_opt)
    alpha_expr = spec["alpha"]
    if not (isinstance(alpha_expr, Const) and alpha_expr.value == 1.0):
        opts.append(f"alpha='clip({alpha_expr.to_ffmpeg(u_expr)}\\,0\\,1)'")
    if spec.get("box"):
        opts.append("box=1")
        opts.append(f"boxcolor={spec['box_color']}")
        opts.append(f"boxborderw={spec['box_border']}")
    # 縁取り（アウトライン）: border=0 では一切出力しない（既存出力と不変）
    if spec.get("border", 0):
        opts.append(f"borderw={spec['border']}")
        opts.append(f"bordercolor={spec['border_color']}")
    # 影: (0,0) では一切出力しない（既存出力と不変）
    sh_x, sh_y = spec.get("shadow", (0, 0))
    if sh_x or sh_y:
        opts.append(f"shadowx={sh_x}")
        opts.append(f"shadowy={sh_y}")
        opts.append(f"shadowcolor={spec['shadow_color']}")
    if enable is not None:
        opts.append(f"enable='{enable}'")
    return "drawtext=" + ":".join(opts)


def _build_text_filters(obj, start, dur):
    """media_type=='text' Object の映像フィルタ（drawtext/subtitles）を返す。
    start/dur はタイムライン上の表示開始時刻/尺（u 正規化に使用）。"""
    spec = obj._text_spec
    kind = spec["kind"]

    if kind == "progress_bar":
        # 動画全体の進行バー: 透明キャンバスに geq で帯を描画。
        # 進行は t/総尺（clip(T/total, 0, 1)）で毎フレーム更新する。
        proj = current_project()
        total = proj.duration if proj and proj.duration else dur
        h = spec["height"]
        yfrac = spec["y"]
        br, bgc, bb, ba = spec["bar_rgba"]
        tr, tg, tb, ta = spec["track_rgba"]
        top = f"({yfrac}*(H-{h}))"
        prog = f"clip(T/{total}\\,0\\,1)"
        bar = f"lte(X\\,W*{prog})"
        band = f"gte(Y\\,{top})*lt(Y\\,{top}+{h})"
        return [
            "format=rgba",
            f"geq=r='if({bar}\\,{br}\\,{tr})'"
            f":g='if({bar}\\,{bgc}\\,{tg})'"
            f":b='if({bar}\\,{bb}\\,{tb})'"
            f":a='({band})*if({bar}\\,{ba}\\,{ta})'",
        ]

    if kind == "subtitles":
        parts = [f"subtitles=filename={_escape_ffpath(spec['srt'])}"]
        if spec.get("style"):
            raw = spec["style"]
            # force_style は単一引用符で囲むため、'→\' ではクォートが閉じて
            # filtergraph が壊れる。アポストロフィを含む style は早期に拒否する。
            if "'" in raw:
                raise ValueError(
                    "subtitles: style にアポストロフィ(')は使用できません "
                    f"(force_style のクォートが壊れます): {raw!r}")
            style = raw.replace("\\", "\\\\")
            parts[0] += f":force_style='{style}'"
        return parts

    if kind == "text":
        text_opt = f"textfile={_escape_ffpath(_ensure_textfile(spec['content']))}"
        return [_build_drawtext_filter(spec, text_opt, start, dur)]

    if kind == "typewriter":
        content = spec["content"]
        n = len(content)
        if n == 0:
            return []
        cps = spec["cps"]
        filters = []
        for i in range(n):
            prefix = content[:i + 1]
            t_on = start + i / cps
            if i < n - 1:
                # 右端 exclusive の半開区間（隣接窓の境界フレーム二重描画を防ぐ）
                t_off = start + (i + 1) / cps
                enable = f"gte(t\\,{t_on:.4f})*lt(t\\,{t_off:.4f})"
            else:
                # 最後の全文は終了まで保持（上限はオーバーレイ側のenableで制御）
                enable = f"gte(t\\,{t_on:.4f})"
            text_opt = f"textfile={_escape_ffpath(_ensure_textfile(prefix))}"
            filters.append(
                _build_drawtext_filter(spec, text_opt, start, dur, enable=enable))
        return filters

    if kind == "counter":
        # value = from_ + (to-from_)*u を drawtext の %{eif} で整数表示（inline展開）
        # %{eif} は切り捨てのため、四捨五入相当に +0.5*sign(to-from_) を加えて
        # 目標値 to に到達させる（u<1 でも to まで表示されるように）。
        u_expr = _u_expr(start, dur)
        val_expr = (spec["from_"] + (spec["to"] - spec["from_"]) * Var("u")
                    + 0.5 * sign(spec["to"] - spec["from_"]))
        val_ff = val_expr.to_ffmpeg(u_expr)
        eif = f"%{{eif\\:{val_ff}\\:d"
        if spec["width"] is not None:
            eif += f"\\:{spec['width']}"
        eif += "}"
        prefix = _escape_counter_literal(spec["prefix"])
        suffix = _escape_counter_literal(spec["suffix"])
        text_opt = "text='" + prefix + eif + suffix + "'"
        return [_build_drawtext_filter(spec, text_opt, start, dur)]

    raise ValueError(f"未知のテキスト種別: {kind}")


def _new_text_object(spec):
    """media_type=='text' の Object を生成して現在のProjectに登録する。
    実体ファイルを持たず、レンダ時に透明lavfi + drawtext/subtitles で描画する。"""
    obj = Object.__new__(Object)
    obj.source = spec["synthetic_source"]
    obj.transforms = []
    obj.effects = []
    obj.audio_effects = []
    obj.duration = None
    obj._duration_auto = False
    obj.start_time = 0
    obj.priority = 0
    obj.media_type = "text"
    obj._until_anchor = None
    obj._until_offset = 0.0
    obj._anchor_name = None
    obj._advance = True
    obj._priority_override = None
    obj._video_deleted = False
    obj._audio_deleted = False
    obj._web_source = None
    obj._web_size = None
    obj._web_fps = None
    obj._web_data = {}
    obj._web_name = None
    obj._web_debug_frames = False
    obj._web_deps = []
    obj._has_video = True
    obj._has_audio = False
    obj._text_spec = spec
    if current_project() is not None:
        current_project().objects.append(obj)
    return obj


# --- テキスト系ファクトリ（映像Object, drawtext/subtitlesベース） ---

# anchor の語彙は state.py に一本化（move 系の _PLACEMENT_ANCHORS と併記）。
# ここは再エクスポートのみ（__init__.py が scriptvedit.text から取り出す）。


def _param_key(value):
    """パラメータを決定的な文字列にする（合成ソースID・キャッシュ鍵用）。

    lambda をそのまま f-string へ入れると `<function <lambda> at 0x...>` の
    **メモリアドレス**が混ざる。レイヤーは Plan/Render で複数回 exec されるので、
    アドレスが変わるとテキスト Object の同一性が崩れて
    「構造が Plan と Render で一致しません」になる（Python 3.13 で実際に発生）。
    解決済み Expr の to_ffmpeg("u") は同じ式なら必ず同じ文字列を返す。
    """
    resolved = _resolve_param(value)
    to_ffmpeg = getattr(resolved, "to_ffmpeg", None)
    if to_ffmpeg is not None:
        return to_ffmpeg("u")
    return str(resolved)


def _text_synthetic_source(spec_key):
    """テキストObject用の合成ソースパス（実体なし・署名/一意化用）"""
    h = hashlib.sha256(spec_key.encode("utf-8")).hexdigest()[:12]
    return f"text://{h}.txt"


def text(content, *, x=0.5, y=0.5, size=48, color="white", font=None,
         box=False, box_color="black@0.5", box_border=10,
         border=0, border_color="black", shadow=(0, 0),
         shadow_color="black@0.6", alpha=1.0, anchor="center"):
    """drawtextでテキストを直接描画する映像Object（透明キャンバス全面）。

    x/y/alpha は 0..1 のキャンバス比率で Expr/lambda 可（liveアニメ）。
    size は定数のみ（FFmpeg 8.0 drawtext の fontsize 式は SEGV のため）。
    border: 縁取り（アウトライン）の太さpx（0で無効。読みやすさ向上に推奨）
    border_color: 縁取りの色（ffcolor形式。例 'black', 'black@0.8'）
    shadow: 影のオフセット (x, y) px（(0,0)で無効。例 shadow=(2, 2)）
    shadow_color: 影の色（ffcolor形式。既定 'black@0.6'）
    日本語表示にはfont指定を推奨（未指定はmeiryo等の既定候補を自動探索）。
    タイムラインには image と同様 .time(秒) で配置する。
    """
    if anchor not in _TEXT_ANCHORS:
        raise ValueError(f"text: anchor は {_TEXT_ANCHORS} のいずれか: {anchor!r}")
    color = _validate_ffmpeg_color("text", color)
    box_color = _validate_ffmpeg_color("text", box_color)
    deco, deco_key = _text_deco_spec(
        "text", border, border_color, shadow, shadow_color)
    spec = {
        "kind": "text",
        "content": str(content),
        "x": _resolve_param(x), "y": _resolve_param(y),
        "size": _validate_text_size("text", _resolve_param(size)),
        "alpha": _resolve_param(alpha),
        "color": color, "font": _resolve_font(font),
        "box": bool(box), "box_color": box_color, "box_border": box_border,
        "anchor": anchor, **deco,
    }
    spec["synthetic_source"] = _text_synthetic_source(
        f"text|{content}|{_param_key(x)}|{_param_key(y)}"
        f"|{_param_key(size)}|{color}|{anchor}{deco_key}")
    return _new_text_object(spec)


def typewriter(content, *, cps=10, x=0.5, y=0.5, size=48, color="white",
               font=None, box=False, box_color="black@0.5", box_border=10,
               border=0, border_color="black", shadow=(0, 0),
               shadow_color="black@0.6", alpha=1.0, anchor="left"):
    """textの派生。1文字ずつ表示（n個のdrawtextを各文字の表示時刻でenable）。

    cps: 1秒あたりの表示文字数。既定anchorは左上（左揃えで打ち出す）。
    border/border_color/shadow/shadow_color は text() と同じ縁取り・影指定。"""
    if anchor not in _TEXT_ANCHORS:
        raise ValueError(f"typewriter: anchor は {_TEXT_ANCHORS} のいずれか: {anchor!r}")
    if cps <= 0:
        raise ValueError(f"typewriter: cps は正の数を指定してください: {cps}")
    color = _validate_ffmpeg_color("typewriter", color)
    box_color = _validate_ffmpeg_color("typewriter", box_color)
    deco, deco_key = _text_deco_spec(
        "typewriter", border, border_color, shadow, shadow_color)
    spec = {
        "kind": "typewriter",
        "content": str(content), "cps": float(cps),
        "x": _resolve_param(x), "y": _resolve_param(y),
        "size": _validate_text_size("typewriter", _resolve_param(size)),
        "alpha": _resolve_param(alpha),
        "color": color, "font": _resolve_font(font),
        "box": bool(box), "box_color": box_color, "box_border": box_border,
        "anchor": anchor, **deco,
    }
    spec["synthetic_source"] = _text_synthetic_source(
        f"tw|{content}|{cps}|{_param_key(x)}|{_param_key(y)}"
        f"|{_param_key(size)}|{color}|{anchor}{deco_key}")
    return _new_text_object(spec)


def _parse_counter_format(fmt):
    """counterのformatを (prefix, suffix, width) に分解。整数指定のみ対応。"""
    i = fmt.find("%")
    if i < 0:
        raise ValueError(
            f"counter: format に数値プレースホルダ(%d 等)が必要です: {fmt!r}")
    j = i + 1
    zero = False
    if j < len(fmt) and fmt[j] == "0":
        zero = True
        j += 1
    digits = ""
    while j < len(fmt) and fmt[j].isdigit():
        digits += fmt[j]
        j += 1
    if j >= len(fmt):
        raise ValueError(f"counter: format の変換指定が不完全です: {fmt!r}")
    conv = fmt[j]
    if conv not in ("d", "i"):
        raise ValueError(
            f"counter: format は整数指定(%d, %03d 等)のみ対応です: {fmt!r}\n"
            f"小数表示は未対応です。")
    width = int(digits) if (zero and digits) else None
    return fmt[:i], fmt[j + 1:], width


def counter(from_, to, *, format="%d", x=0.5, y=0.5, size=48, color="white",
            font=None, box=False, box_color="black@0.5", box_border=10,
            border=0, border_color="black", shadow=(0, 0),
            shadow_color="black@0.6", alpha=1.0, anchor="center"):
    """数値カウントアップ映像Object。drawtextの%{eif}式で from_→to を補間表示。

    format は整数指定(%d, %03d 等)。前後のリテラル文字も表示可能。
    border/border_color/shadow/shadow_color は text() と同じ縁取り・影指定。"""
    if anchor not in _TEXT_ANCHORS:
        raise ValueError(f"counter: anchor は {_TEXT_ANCHORS} のいずれか: {anchor!r}")
    prefix, suffix, width = _parse_counter_format(format)
    _escape_counter_literal(prefix)  # アポストロフィ等の早期検証（inline不可文字）
    _escape_counter_literal(suffix)
    color = _validate_ffmpeg_color("counter", color)
    box_color = _validate_ffmpeg_color("counter", box_color)
    deco, deco_key = _text_deco_spec(
        "counter", border, border_color, shadow, shadow_color)
    spec = {
        "kind": "counter",
        "from_": _resolve_param(from_), "to": _resolve_param(to),
        "prefix": prefix, "suffix": suffix, "width": width,
        "x": _resolve_param(x), "y": _resolve_param(y),
        "size": _validate_text_size("counter", _resolve_param(size)),
        "alpha": _resolve_param(alpha),
        "color": color, "font": _resolve_font(font),
        "box": bool(box), "box_color": box_color, "box_border": box_border,
        "anchor": anchor, **deco,
    }
    spec["synthetic_source"] = _text_synthetic_source(
        f"counter|{from_}|{to}|{format}|{_param_key(x)}|{_param_key(y)}"
        f"|{_param_key(size)}|{color}|{anchor}{deco_key}")
    return _new_text_object(spec)


def subtitles(srt_file, *, style=None):
    """SRT字幕ファイルをsubtitlesフィルタで合成する映像Object。

    style: ASSのforce_styleスタイル文字列（例 "FontName=Meiryo,FontSize=28"）。
    SRTは自身のタイムコードで表示されるため .time(全体尺) で開始0に配置する想定。"""
    if not isinstance(srt_file, str):
        raise TypeError(f"subtitles: srt_file はパス文字列で指定してください: {srt_file!r}")
    if not os.path.exists(srt_file):
        raise FileNotFoundError(f"subtitles: 字幕ファイルが見つかりません: {srt_file}")
    ext = os.path.splitext(srt_file)[1].lower()
    if ext not in (".srt", ".ass", ".vtt"):
        raise ValueError(
            f"subtitles: 対応拡張子は .srt/.ass/.vtt です: {srt_file}")
    spec = {
        "kind": "subtitles",
        "srt": srt_file,
        "style": style,
        # drawtext系オプションは未使用だが _new_text_object の一貫性のため保持
        "x": Const(0.5), "y": Const(0.5), "size": Const(48), "alpha": Const(1.0),
        "color": "white", "font": None, "box": False,
        "box_color": "black@0.5", "box_border": 10, "anchor": "center",
        "border": 0, "border_color": "black",
        "shadow": (0, 0), "shadow_color": "black@0.6",
    }
    try:
        ffp = _file_fingerprint(srt_file)
        spec["synthetic_source"] = _text_synthetic_source(f"subs|{ffp}|{style}")
    except OSError:
        spec["synthetic_source"] = _text_synthetic_source(f"subs|{srt_file}|{style}")
    obj = _new_text_object(spec)
    # SRTをレイヤー依存として登録（cache鮮度検証で字幕変更を検知）
    proj = current_project()
    if proj is not None and proj._current_layer_file:
        proj._extra_layer_deps.setdefault(
            proj._current_layer_file, []).append(srt_file)
    return obj


# --- karaoke（ASS \k タグによるカラオケ風ハイライト字幕） ---

# 既知の色名 -> #RRGGBB（karaoke styleの簡易色指定用）
_ASS_NAMED_COLORS = {
    "white": "FFFFFF", "black": "000000", "red": "FF0000", "green": "00FF00",
    "blue": "0000FF", "yellow": "FFFF00", "cyan": "00FFFF", "magenta": "FF00FF",
    "orange": "FFA500", "gray": "808080", "grey": "808080", "pink": "FFC0CB",
}


def _color_to_ass(color, alpha=0):
    """色指定をASSの &HAABBGGRR 16進文字列に変換する。
    'white'等の既知色名 / '#RRGGBB' / 既にASS形式('&H..'始まり)のいずれかを受け付ける。"""
    if isinstance(color, str) and color.upper().startswith("&H"):
        return color
    if not isinstance(color, str):
        raise ValueError(f"karaoke: 色指定は文字列で指定してください: {color!r}")
    name = color.lower()
    hexrgb = _ASS_NAMED_COLORS.get(name, color.lstrip("#"))
    if len(hexrgb) != 6 or any(c not in "0123456789abcdefABCDEF" for c in hexrgb):
        raise ValueError(
            f"karaoke: 未対応の色指定です: {color!r}"
            f"（既知色名 {sorted(_ASS_NAMED_COLORS)} か #RRGGBB を指定してください）")
    rr, gg, bb = hexrgb[0:2], hexrgb[2:4], hexrgb[4:6]
    return f"&H{alpha:02X}{bb}{gg}{rr}".upper()


def _fmt_ass_time(t):
    """秒 -> ASSタイムコード（H:MM:SS.cc、センチ秒単位）"""
    cs_total = int(round(float(t) * 100))
    h, rem = divmod(cs_total, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape_ass_text(s):
    """ASSダイアログテキスト用エスケープ（\\、{}、改行）"""
    return (s.replace("\\", "\\\\").replace("{", "\\{")
             .replace("}", "\\}").replace("\r\n", "\\N").replace("\n", "\\N"))


def _karaoke_tokenize(text):
    """カラオケ行を\\kタグの単位（語）に分割する。
    空白を含む場合は空白区切り（末尾の空白を保持）、無ければ1文字ずつに分割
    （日本語歌詞のように分かち書きが無い場合の既定挙動）。"""
    if any(c.isspace() for c in text):
        toks = re.findall(r"\S+\s*", text)
        return toks if toks else [text]
    return list(text)


def karaoke(lines, *, style=None):
    """カラオケ風ハイライト字幕（ASSの\\kタグ）を生成する映像Object。

    lines: [(start, end, "歌詞"), ...] または
           [(start, end, "歌詞", [word_durations...]), ...] のリスト。
    - word_durations省略時: 行内の語（分割規約は_karaoke_tokenize参照）に
      (end-start)を均等割りして\\kタグを割り当てる。
    - word_durations指定時: 分割された語の数と同じ長さの秒数リストを指定する
      （語ごとのハイライト時間、\\kタグの単位はセンチ秒に変換）。
    style: {"font", "size", "primary"（既に発音済みの色）, "secondary"（未発音の色）,
            "outline_color", "back_color", "alignment", "margin_v", "outline",
            "shadow", "bold"} を上書きする辞書（省略キーは既定値）。

    生成したASSは歌詞+タイミング+styleのSHA256でcontent-addressedキャッシュに
    書き出し、subtitles()経由でsubtitlesフィルタとして合成する。
    SRT/ASSと同様に .time(全体尺) で開始0に配置する想定。
    """
    if not isinstance(lines, (list, tuple)) or len(lines) == 0:
        raise ValueError("karaoke: lines には1行以上指定してください")
    st = dict(style or {})
    font = st.get("font", "Meiryo")
    size = int(st.get("size", 48))
    primary = _color_to_ass(st.get("primary", "yellow"))
    secondary = _color_to_ass(st.get("secondary", "white"))
    outline_color = _color_to_ass(st.get("outline_color", "black"))
    back_color = _color_to_ass(st.get("back_color", "black"), alpha=0x80)
    alignment = int(st.get("alignment", 2))
    margin_v = int(st.get("margin_v", 60))
    outline_w = st.get("outline", 2)
    shadow = st.get("shadow", 0)
    bold = -1 if st.get("bold", True) else 0

    body_lines = []
    for idx, line in enumerate(lines):
        if not isinstance(line, (list, tuple)) or len(line) not in (3, 4):
            raise ValueError(
                f"karaoke: lines[{idx}] は (start,end,text) または "
                f"(start,end,text,word_durations) を指定してください: {line!r}")
        if len(line) == 3:
            t0, t1, txt = line
            word_durs = None
        else:
            t0, t1, txt, word_durs = line
        t0 = float(t0)
        t1 = float(t1)
        if t1 <= t0:
            raise ValueError(
                f"karaoke: lines[{idx}] の end は start より後が必要です: {line!r}")
        tokens = _karaoke_tokenize(str(txt))
        if not tokens:
            continue
        if word_durs is not None:
            word_durs = list(word_durs)
            if len(word_durs) != len(tokens):
                raise ValueError(
                    f"karaoke: lines[{idx}] の word_durations 数({len(word_durs)})が"
                    f"分割語数({len(tokens)})と一致しません。分割結果: {tokens}\n"
                    f"（分割規約: 空白を含む行は空白区切り、無ければ1文字ずつ）")
            for d in word_durs:
                _require_number("karaoke", "word_durations要素", d, 0.001, None)
        else:
            each = (t1 - t0) / len(tokens)
            word_durs = [each] * len(tokens)
        # \k はセンチ秒単位。各語独立に round(d*100) すると丸め誤差が累積して
        # 総和が行尺とずれる。累積器方式で
        # \k_i = round(cumsum_i*100) - round(cumsum_{i-1}*100) とし、
        # 総和を round(cumsum_n*100) に一致させる。
        k_parts = []
        cum = 0.0
        prev_cs = 0
        for tok, d in zip(tokens, word_durs):
            cum += float(d)
            cs = round(cum * 100)
            k = cs - prev_cs
            prev_cs = cs
            k_parts.append(f"{{\\k{k}}}{_escape_ass_text(tok)}")
        k_text = "".join(k_parts)
        body_lines.append(
            f"Dialogue: 0,{_fmt_ass_time(t0)},{_fmt_ass_time(t1)},Karaoke,,0,0,0,,{k_text}")

    if not body_lines:
        raise ValueError("karaoke: 有効な行がありません（全行が空テキストでした）")

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Karaoke,{font},{size},{primary},{secondary},{outline_color},"
        f"{back_color},{bold},0,0,0,100,100,0,0,1,{outline_w},{shadow},"
        f"{alignment},20,20,{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    content = header + "\n".join(body_lines) + "\n"

    key = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    ass_path = os.path.join(_ARTIFACT_DIR, "karaoke", f"{key}.ass")
    if not os.path.exists(ass_path):
        # 原子的書き込み（一時パスは pid+uuid でユニーク化。同一内容の並列生成でも
        # 相互に壊さず、os.replace の後勝ちでも成果物は等価。issue #13 P2-15）
        _atomic_write_text(ass_path, content)

    return subtitles(ass_path)


# --- 循環 import の回避（同一 SCC のモジュールのみ末尾で束縛。scripts/check_import_cycles.py で計測）---
from scriptvedit.cache import _file_fingerprint
from scriptvedit.ffmpeg import _atomic_write_text
from scriptvedit.filters.video import _u_expr
from scriptvedit.objects import Object
