# -*- coding: utf-8 -*-

import math
import os
import re
import sys
import hashlib
import builtins as _builtins

# context は scriptvedit 内 import を持たない葉なので先頭で import できる。
from scriptvedit.context import current_project

# --- scriptvedit 内モジュール（循環しないので先頭で import する）---
from scriptvedit.expr import (
    Const, Expr, Var, _resolve_param, _UStr, _UValue, abs, floor, gte, if_, mod, round)
from scriptvedit.state import _ARTIFACT_DIR, _NAMED_EASINGS, _TEXT_ANCHORS
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
    （並列レイヤーからの同時到達も原子的書き込みで無害化する）。

    改行は LF で書く（_atomic_write_text が改行を変換しない）。FFmpeg 8 の
    drawtext は "\\r\\n" を改行2回として描くため、Windows 既定の CRLF 変換や
    呼び出し側の文字列に混じった CR（CRLF ファイルをバイナリで読んだ等）が
    あると複数行の行間が倍になる。CR は LF へ正規化してから鍵と本文を作る。"""
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    body = _escape_textfile_content(content)
    key = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    path = os.path.join(_ARTIFACT_DIR, "text", f"{key}.txt")
    _atomic_write_text(path, body)
    return path


def _escape_counter_literal(s):
    """counter の format 前後リテラル（inline text= 内）用エスケープ。
    inline 値は単一引用符で包むが、FFmpeg 8.0 では引用符内でも : , が区切り
    扱いになるためエスケープする。' は inline では確実に描画できないため拒否。

    % と \ は drawtext のテキスト展開まで「\% / \\」の形で届かせる必要があり、
    その手前の AVOption の解釈でバックスラッシュが1段はがれるので、2段ぶん書く
    （FFmpeg 8.0 実測: text='…\%…' は "Stray %" になり**文字列全体が描かれない**。
    '…\\%…' で % が出る。バックスラッシュ1個は '\\\\'）。"""
    if "'" in s:
        raise ValueError(
            "counter: format のリテラル部分にアポストロフィ(')は使用できません。"
            "アポストロフィを含む固定文字は text() を併用してください。")
    return (s.replace("\\", "\\\\\\\\").replace("%", "\\\\%")
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


def _text_size_opt(size_expr):
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
    opts.append(_text_size_opt(spec["size"]))
    opts.append(f"fontcolor={spec['color']}")
    opts.append(x_opt)
    opts.append(y_opt)
    alpha_expr = spec["alpha"]
    if not (isinstance(alpha_expr, Const) and alpha_expr.value == 1.0):
        opts.append(f"alpha='clip({alpha_expr.to_ffmpeg(u_expr)}\\,0\\,1)'")
    # 行間・行揃え・縦の基準: 既定値では一切出力しない（既存出力と不変）
    if spec.get("line_spacing", 0):
        opts.append(f"line_spacing={spec['line_spacing']}")
    if spec.get("text_align", "left") != "left":
        opts.append(f"text_align={spec['text_align']}")
    if spec.get("y_align", "text") != "text":
        opts.append(f"y_align={spec['y_align']}")
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
        # alpha=1 は必須。入力は完全に透明なキャンバス（color=black@0.0）で、
        # subtitles フィルタの既定（alpha=0）は RGB だけを描いてアルファを
        # 触らないため、文字のアルファが 0 のまま残り overlay しても何も映らない
        # （FFmpeg 8.0 実測: alpha なしは全画素が透明、alpha=1 で描かれる）。
        parts = [f"subtitles=filename={_escape_ffpath(spec['srt'])}:alpha=1"]
        if spec.get("fontsdir"):
            parts[0] += f":fontsdir={_escape_ffpath(spec['fontsdir'])}"
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
        return _build_counter_filters(spec, start, dur)

    raise ValueError(f"未知のテキスト種別: {kind}")


# --- counter（drawtext の %{eif} による数値表示）---

# double が整数を正確に表せる上限。表示値×10^小数桁 がこれを超えると下の桁が狂う。
_COUNTER_EXACT_MAX = 2 ** 53
# %{eif} は式の結果を C の int（32ビット）へ変換して印字する。
_EIF_INT_MAX = 2 ** 31 - 1
# 小数桁の上限（小数部を1個の %{eif} で印字するので int に収まる桁数まで）
_COUNTER_MAX_DECIMALS = 9
# 表示する値の範囲を見積もるための u の標本数（0..1 を等分）
_COUNTER_SAMPLES = 2000

def _resolve_counter_easing(easing):
    """counter の easing を u の Expr（0→1 の進み方）へ解決する。None はそのまま返す。"""
    if easing is None:
        return None
    if isinstance(easing, str):
        # 名前の表は state._NAMED_EASINGS（easing.py が登録する）
        if easing not in _NAMED_EASINGS:
            raise ValueError(
                f"counter: easing の名前が不正です: {easing!r}\n"
                f"使える名前: {', '.join(sorted(_NAMED_EASINGS))}\n"
                f"（ease_cubic_bezier(...) / ease_spring(...) や lambda u: ... は"
                f"関数のまま渡してください）")
        easing = _NAMED_EASINGS[easing]
    if isinstance(easing, (int, float)) or not (
            callable(easing) or isinstance(easing, Expr)):
        raise TypeError(
            "counter: easing は イージング名 / u を受け取る関数 / Expr の"
            f"いずれかで指定してください: {easing!r}")
    return _resolve_param(easing)


def _counter_progress(start, dur):
    """counter の値の進行度 u の式（最後のコマで必ず 1 になる）。

    通常の u = clip((t-start)/dur, 0, 1) は、最後のコマ（t = start + dur - 1/fps）で
    (N-1)/N にしかならず、値が to に届かない（実測: 3秒・30fps の
    counter(0, 2147483647) の最後のコマが 2123622718）。分母を 1フレーム分
    （と 1µs）短くして、最後のコマで 1 に飽和させる。
    位置・アルファの u（_build_drawtext_filter）は他の Object と同じ定義のまま。

    動画の末尾で終わる counter は、出力される最後のコマが「尺 - 1フレーム」より
    手前になりうる: 出力コマ数は 総尺×fps の四捨五入なので、総尺がフレーム格子に
    乗らず端数が 0.5 未満だと最後の1コマが出ない（実測: 30fps の time(1.01) は
    30 コマで、最後は t=0.9667。分母 0.976666 では 0.99 までしか行かず「99」で
    終わった）。そのときは「最後に出力されるコマ」で 1 になるよう分母を詰める。
    詰めるのは食い違いが 1 フレーム以内のときだけ（configure(duration=) で
    counter の途中を意図して切った場合に、進み方を変えないため）。

    戻り値は _UStr。詰めるのは u の分母だけで、秒で書く式（elapsed / remaining /
    ramp / keyframes_sec）が読む表示秒と経過秒は Object の尺そのもの
    （他の Object と同じ clip(t-start,0,dur)）を運ぶ。素の文字列を返すと、秒の式が
    記号 sec(…) のまま drawtext へ流れて ffmpeg が EINVAL で落ちる。"""
    real = _u_expr(start, dur)
    proj = current_project()
    fps = float(proj.fps) if proj and proj.fps else 30.0
    span = float(dur) - 1.0 / fps
    total = getattr(proj, "duration", None) if proj else None
    if total:
        # 出力コマ数（端数ちょうど 0.5 は少ない側に見積もる。多く出ても
        # 進行度は 1 で飽和するだけなので、少ない側が安全）
        n_out = math.ceil(float(total) * fps - 0.5 - 1e-9)
        cut = (n_out - 1) / fps - float(start)
        if cut < span <= cut + 1.0 / fps + 1e-9:
            span = cut
    span = round(span - 1e-6, 6)
    if span <= 0:
        # 1フレーム以下の尺: 最初から to を出す
        return _UStr("1", real.dur, sec=real.sec)
    return _UStr(str(_u_expr(start, span)), real.dur, sec=real.sec)


def _counter_scaled_value(spec):
    """表示値×10^小数桁 を四捨五入した整数（Expr。符号つき）を返す。"""
    u = Var("u")
    from_, to, easing = spec["from_"], spec["to"], spec.get("easing")
    value = from_ + (to - from_) * (easing if easing is not None else u)
    if easing is not None or not (
            isinstance(from_, Const) and isinstance(to, Const)):
        # イージングの終点が 1 でなくても（弾む・行き過ぎる系、自作の式）
        # 最後のコマは必ず to にする
        value = if_(gte(u, 1), to, value)
    decimals = spec.get("decimals", 0)
    if decimals:
        value = value * (10 ** decimals)
    return round(value)


def _counter_sample_range(scaled, dur):
    """scaled（Expr）を u=0..1 で数値評価し、(最小, 最大, |値|の最小, |値|の最大) を返す。

    dur は表示秒（秒で書く式の数値評価に要る。_UValue で渡す）。
    数値評価できない式（random 等）は None（呼び出し側は全ての桁数・符号を用意する）。"""
    lo = hi = alo = ahi = None
    for i in range(_COUNTER_SAMPLES + 1):
        try:
            v = float(scaled.eval_at(_UValue(i / _COUNTER_SAMPLES, dur)))
        except Exception:
            return None
        if v != v or v in (float("inf"), float("-inf")):
            return None
        a = v if v >= 0 else -v
        lo = v if lo is None or v < lo else lo
        hi = v if hi is None or v > hi else hi
        alo = a if alo is None or a < alo else alo
        ahi = a if ahi is None or a > ahi else ahi
    return lo, hi, alo, ahi


def _counter_group_count(n, base, cap):
    """0 以上の整数 n を base 進の「桁のかたまり」に分けたときの個数（1..cap）"""
    n = int(n)
    count = 1
    while n >= base and count < cap:
        n //= base
        count += 1
    return count


def _eif(expr, u_expr, width=None):
    """drawtext の %{eif:式:d[:幅]}（inline の text= 用にエスケープ済み）"""
    out = f"%{{eif\\:{expr.to_ffmpeg(u_expr)}\\:d"
    if width:
        out += f"\\:{width}"
    return out + "}"


def _build_counter_filters(spec, start, dur):
    """counter の drawtext フィルタ列を返す。

    drawtext の %{eif} は「式の結果を 32ビットの int にして10進で印字する」
    ことしかできない（桁区切り・小数・条件つきの文字列は無い）。そこで
      - 整数で 32ビットに収まり、桁区切りも小数も無いとき … %{eif} 1個
      - それ以外 … 値を「3桁ずつ（桁区切り）」または「9桁ずつ」のかたまりに
        割って、かたまりごとに %{eif}（下位はゼロ埋め）で印字する。
        かたまりの個数と符号で文字列の形が変わるので、形ごとに drawtext を
        1個ずつ作り、enable で値の範囲に応じて1個だけを有効にする
    とする。用意する形は、値を u=0..1 で数値評価した範囲から決める
    （最上位と最下位の形は範囲を開けてあり、評価の合間に範囲を外れても
    文字が消えることはない）。"""
    u_expr = _counter_progress(start, dur)
    scaled = _counter_scaled_value(spec)
    decimals = spec.get("decimals", 0)
    group = spec.get("group", False)
    width = spec["width"]
    prefix = _escape_counter_literal(spec["prefix"])
    suffix = _escape_counter_literal(spec["suffix"])
    rng = _counter_sample_range(scaled, dur)

    if (not decimals and not group and rng is not None
            and rng[3] <= _EIF_INT_MAX):
        text_opt = "text='" + prefix + _eif(scaled, u_expr, width) + suffix + "'"
        return [_build_drawtext_filter(spec, text_opt, start, dur)]

    base = 1000 if group else 10 ** 9
    chunk_width = 3 if group else 9
    cap = 6 if group else 2          # base**cap > 2**53（正確に表せる上限）
    sep = "\\," if group else ""
    pow10 = 10 ** decimals
    magnitude = abs(scaled)
    int_part = floor(magnitude / pow10) if decimals else magnitude
    if rng is None:
        signs = (True, False)
        c_min, c_max = 1, cap
    else:
        lo, hi, alo, ahi = rng
        signs = tuple(neg for neg in (True, False)
                      if (lo < 0 if neg else hi >= 0))
        c_min = _counter_group_count(alo // pow10, base, cap)
        c_max = _counter_group_count(ahi // pow10, base, cap)

    filters = []
    for neg in signs:
        for c in range(c_min, c_max + 1):
            top = floor(int_part / (base ** (c - 1))) if c > 1 else int_part
            top_width = None
            if width and width > chunk_width * (c - 1):
                top_width = width - chunk_width * (c - 1)
            body = _eif(top, u_expr, top_width)
            for k in range(c - 2, -1, -1):
                part = mod(floor(int_part / (base ** k)) if k else int_part, base)
                body += sep + _eif(part, u_expr, chunk_width)
            if decimals:
                body += "." + _eif(mod(magnitude, pow10), u_expr, decimals)
            conds = []
            if len(signs) > 1:
                conds.append(f"{'lt' if neg else 'gte'}"
                             f"({scaled.to_ffmpeg(u_expr)}\\,0)")
            int_ff = int_part.to_ffmpeg(u_expr)
            if c > c_min:
                conds.append(f"gte({int_ff}\\,{base ** (c - 1)})")
            if c < c_max:
                conds.append(f"lt({int_ff}\\,{base ** c})")
            text_opt = ("text='" + prefix + ("-" if neg else "") + body
                        + suffix + "'")
            filters.append(_build_drawtext_filter(
                spec, text_opt, start, dur,
                enable="*".join(conds) if conds else None))
    return filters


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
    # `obj @ t` / `a >> b` の配置属性。Object.__init__ が設定するものと
    # 同じ初期値を必ず置く（ここだけ欠けていると、Project 側が getattr(...,
    # None) をやめて直接参照した瞬間に text / progress_bar だけ AttributeError
    # になる。__new__ で手組みしている以上、__init__ との差分は作らない）。
    obj._fixed_start = None
    obj._start_after = None
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


# text() の行ごとの揃え（drawtext の text_align）と縦の基準（y_align）
_TEXT_ALIGNS = ("left", "center", "right")
_TEXT_Y_ALIGNS = ("text", "baseline", "font")


def _text_synthetic_source(spec_key):
    """テキストObject用の合成ソースパス（実体なし・署名/一意化用）"""
    h = hashlib.sha256(spec_key.encode("utf-8")).hexdigest()[:12]
    return f"text://{h}.txt"


def text(content, *, x=0.5, y=0.5, size=48, color="white", font=None,
         box=False, box_color="black@0.5", box_border=10,
         border=0, border_color="black", shadow=(0, 0),
         shadow_color="black@0.6", alpha=1.0, anchor="center",
         line_spacing=0, text_align="left", y_align="text"):
    """drawtextでテキストを直接描画する映像Object（透明キャンバス全面）。

    x/y/alpha は 0..1 のキャンバス比率で Expr/lambda 可（liveアニメ）。
    size は定数のみ（FFmpeg 8.0 drawtext の fontsize 式は SEGV のため）。
    border: 縁取り（アウトライン）の太さpx（0で無効。読みやすさ向上に推奨）
    border_color: 縁取りの色（ffcolor形式。例 'black', 'black@0.8'）
    shadow: 影のオフセット (x, y) px（(0,0)で無効。例 shadow=(2, 2)）
    shadow_color: 影の色（ffcolor形式。既定 'black@0.6'）
    line_spacing: 複数行の行間に足す px（整数。負値で詰める。既定 0）
    text_align: 複数行の行ごとの揃え 'left' / 'center' / 'right'（既定 'left'）。
        ブロック全体の位置は anchor と x/y で決まり、これはブロックの中での
        各行の寄せ方だけを変える（anchor='center' でも行は既定で左揃え）。
    y_align: y の縦の基準 'text'（既定。その文字列でいちばん背の高い字の上端）/
        'baseline'（1行目のベースライン）/ 'font'（フォントの行の上端）。
        'text' は中身で上端が変わるので、語ごとに text() を分けて横に並べるときは
        'font' か 'baseline' にすると行がそろう。
    日本語表示にはfont指定を推奨（未指定はmeiryo等の既定候補を自動探索）。
    タイムラインには image と同様 .time(秒) で配置する。
    """
    if anchor not in _TEXT_ANCHORS:
        raise ValueError(f"text: anchor は {_TEXT_ANCHORS} のいずれか: {anchor!r}")
    _require_number("text", "line_spacing", line_spacing)
    line_spacing = int(line_spacing)
    if text_align not in _TEXT_ALIGNS:
        raise ValueError(
            f"text: text_align は {_TEXT_ALIGNS} のいずれか: {text_align!r}")
    if y_align not in _TEXT_Y_ALIGNS:
        raise ValueError(
            f"text: y_align は {_TEXT_Y_ALIGNS} のいずれか: {y_align!r}")
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
        "line_spacing": line_spacing, "text_align": text_align,
        "y_align": y_align,
    }
    # 既定値では鍵断片を足さない（既存の合成ソースIDを変えない）
    layout_key = ""
    if line_spacing or text_align != "left" or y_align != "text":
        layout_key = f"|ls{line_spacing}|ta{text_align}|ya{y_align}"
    spec["synthetic_source"] = _text_synthetic_source(
        f"text|{content}|{_param_key(x)}|{_param_key(y)}"
        f"|{_param_key(size)}|{color}|{anchor}{deco_key}{layout_key}")
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
    """counter の format を (prefix, suffix, width, group, decimals) に分解する。

    変換指定は1個だけ: %d / %i（整数）、%0Nd（N桁ゼロ埋め）、%,d（3桁ごとのカンマ）、
    %.Nf（小数 N 桁）、%,.Nf（両方）。%% は文字の % になる。"""
    if not isinstance(fmt, str):
        raise TypeError(f"counter: format は文字列で指定してください: {fmt!r}")
    # 文字としての %（%%）を番兵に退避してから、変換指定を探す
    work = fmt.replace("%%", "\x00")
    i = work.find("%")
    if i < 0:
        raise ValueError(
            f"counter: format に数値プレースホルダ(%d 等)が必要です: {fmt!r}")
    m = re.match(r"%(,?)(0?)(\d*)(,?)(?:\.(\d+))?([a-zA-Z]?)", work[i:])
    comma1, zero, digits, comma2, prec, conv = m.groups()
    if not conv:
        raise ValueError(f"counter: format の変換指定が不完全です: {fmt!r}")
    if conv not in ("d", "i", "f"):
        raise ValueError(
            f"counter: format の変換指定は %d / %0Nd / %,d / %.Nf / %,.Nf "
            f"のいずれかです: {fmt!r}")
    if conv in ("d", "i") and prec is not None:
        raise ValueError(
            f"counter: 小数桁（.N）は %f と組み合わせてください: {fmt!r}")
    group = bool(comma1 or comma2)
    # %f は printf と同じく既定6桁。%.0f は整数表示
    decimals = (int(prec) if prec is not None else 6) if conv == "f" else 0
    if decimals > _COUNTER_MAX_DECIMALS:
        raise ValueError(
            f"counter: 小数桁は {_COUNTER_MAX_DECIMALS} 桁までです: {fmt!r}")
    width = int(digits) if (zero and digits) else None
    if width is not None and (group or decimals):
        raise ValueError(
            f"counter: ゼロ埋め（%0Nd）は桁区切り・小数と併用できません: {fmt!r}")
    rest = work[i + m.end():]
    if "%" in rest:
        raise ValueError(
            f"counter: 数値プレースホルダは1個だけです"
            f"（文字の % は %% と書く）: {fmt!r}")
    return (work[:i].replace("\x00", "%"), rest.replace("\x00", "%"),
            width, group, decimals)


def counter(from_, to, *, format="%d", easing=None, x=0.5, y=0.5, size=48,
            color="white", font=None, box=False, box_color="black@0.5",
            box_border=10, border=0, border_color="black", shadow=(0, 0),
            shadow_color="black@0.6", alpha=1.0, anchor="center"):
    """数値カウントアップ映像Object。drawtextの%{eif}式で from_→to を補間表示。

    最初のコマは from_、**最後のコマは必ず to** を表示する（値の進行度は
    最後のコマで 1 に達する。イージングの終点が 1 でなくても最後は to）。
    動画の末尾で終わり、総尺がフレーム格子に乗らず最後の1コマが出力されない
    とき（30fps の time(1.01) は 30 コマ）も、出力される最後のコマが to になる。
    例外は counter の途中を意図して切った場合（configure(duration=) や
    render(end=) で 1 フレームを超えて手前で切る）で、切った時点の値で終わる。
    途中の値は四捨五入（表示する最小の桁で、0 から遠い方へ）。

    format: printf 風の書式。変換指定は1個で、前後に固定の文字（接頭辞・接尾辞）を
        書ける。文字の % は %% と書く。アポストロフィ(')は使えない。
          '%d'        整数                  → 1234567
          '%05d'      ゼロ埋め5桁           → 00042
          '%,d'       3桁ごとのカンマ       → 1,234,567
          '%.2f'      小数2桁               → 1234567.89
          '%,.1f'     カンマ + 小数1桁      → 1,234,567.9
          '¥%,d円' / '%d%%' のように前後へ文字を足せる
        ゼロ埋めは桁区切り・小数と併用できない。小数は 9 桁まで。
    easing: 値の進み方。None（等速）/ イージング名（'ease_out_cubic' 等。
        linear と、引数なしの ease_* すべて）/ u を受け取る関数や Expr
        （ease_spring(...)・lambda u: u ** 2 など。0→1 の進行度を返す）。
        行き過ぎる系（ease_out_back 等）は途中で to を超えた値も表示する。
    border/border_color/shadow/shadow_color は text() と同じ縁取り・影指定。

    精度の限界（drawtext の式は倍精度浮動小数点で計算される）:
      - 「表示値 × 10^小数桁」の絶対値が 2^53（約 9.007×10^15）未満なら
        全ての桁が正しい。from_ / to が定数でこれを超えると ValueError。
      - 32ビット（±2,147,483,647）を超える整数・桁区切り・小数は、値を
        3桁または9桁のかたまりに割り、桁数と符号ごとに drawtext を1個ずつ
        用意して切り替える（%{eif} が 32ビットの int しか印字できないため）。
        フィルタは最大で 桁のかたまりの数 × 符号の数 だけ増える。
      - 桁数が変わるコマで文字列の幅が変わるので、anchor='center' では
        その瞬間に全体が少し動く（等幅にしたいならゼロ埋めを使う）。"""
    if anchor not in _TEXT_ANCHORS:
        raise ValueError(f"counter: anchor は {_TEXT_ANCHORS} のいずれか: {anchor!r}")
    prefix, suffix, width, group, decimals = _parse_counter_format(format)
    _escape_counter_literal(prefix)  # アポストロフィ等の早期検証（inline不可文字）
    _escape_counter_literal(suffix)
    easing_expr = _resolve_counter_easing(easing)
    from_expr, to_expr = _resolve_param(from_), _resolve_param(to)
    for name, e in (("from_", from_expr), ("to", to_expr)):
        if (isinstance(e, Const)
                and _builtins.abs(e.value) * 10 ** decimals >= _COUNTER_EXACT_MAX):
            raise ValueError(
                f"counter: {name}={e.value!r} は正確に表示できる範囲を超えています"
                f"（|値|×10^小数桁 は 2^53 = {_COUNTER_EXACT_MAX:,} 未満）")
    color = _validate_ffmpeg_color("counter", color)
    box_color = _validate_ffmpeg_color("counter", box_color)
    deco, deco_key = _text_deco_spec(
        "counter", border, border_color, shadow, shadow_color)
    spec = {
        "kind": "counter",
        "from_": from_expr, "to": to_expr,
        "prefix": prefix, "suffix": suffix, "width": width,
        "group": group, "decimals": decimals, "easing": easing_expr,
        "x": _resolve_param(x), "y": _resolve_param(y),
        "size": _validate_text_size("counter", _resolve_param(size)),
        "alpha": _resolve_param(alpha),
        "color": color, "font": _resolve_font(font),
        "box": bool(box), "box_color": box_color, "box_border": box_border,
        "anchor": anchor, **deco,
    }
    # easing 未指定では鍵断片を足さない（既存の合成ソースIDを変えない）
    ease_key = "" if easing_expr is None else f"|ease:{_param_key(easing_expr)}"
    spec["synthetic_source"] = _text_synthetic_source(
        f"counter|{_param_key(from_expr)}|{_param_key(to_expr)}|{format}"
        f"|{_param_key(x)}|{_param_key(y)}"
        f"|{_param_key(size)}|{color}|{anchor}{deco_key}{ease_key}")
    return _new_text_object(spec)


# fontsdir の中のフォントをレイヤー依存へ登録する上限（これを超えるフォルダは
# システムのフォント置き場とみなして登録しない。全ファイルの内容ハッシュを取ると
# 数 GB を読むことになるため）
_FONTSDIR_DEP_LIMIT = 100
_FONT_EXTS = (".ttf", ".otf", ".ttc", ".otc")


def _fontsdir_fonts(fontsdir):
    """fontsdir 直下のフォントファイル名（ソート済み）を返す"""
    return sorted(n for n in os.listdir(fontsdir)
                  if os.path.splitext(n)[1].lower() in _FONT_EXTS
                  and os.path.isfile(os.path.join(fontsdir, n)))


def subtitles(srt_file, *, style=None, fontsdir=None):
    """SRT/ASS/VTT字幕ファイルをsubtitlesフィルタで合成する映像Object。

    style: ASSのforce_styleスタイル文字列（例 "FontName=Meiryo,FontSize=28"）。
    fontsdir: フォントを探すフォルダ（同梱フォント・可変フォントから切り出した
        静的フォントなど、システムに入っていないフォントを ASS の Fontname や
        style の FontName から**名前で**使うとき）。libass はシステムのフォントに
        加えてこのフォルダも探す。プロジェクト同梱の小さいフォルダを渡す想定で、
        直下のフォントが 100 個以下ならレイヤーキャッシュの鮮度検証にも含める。
    SRTは自身のタイムコードで表示されるため .time(全体尺) で開始0に配置する想定。"""
    if not isinstance(srt_file, str):
        raise TypeError(f"subtitles: srt_file はパス文字列で指定してください: {srt_file!r}")
    if not os.path.exists(srt_file):
        raise FileNotFoundError(f"subtitles: 字幕ファイルが見つかりません: {srt_file}")
    ext = os.path.splitext(srt_file)[1].lower()
    if ext not in (".srt", ".ass", ".vtt"):
        raise ValueError(
            f"subtitles: 対応拡張子は .srt/.ass/.vtt です: {srt_file}")
    font_names = None
    if fontsdir is not None:
        if not isinstance(fontsdir, str):
            raise TypeError(
                f"subtitles: fontsdir はフォルダのパス文字列で指定してください: "
                f"{fontsdir!r}")
        if not os.path.isdir(fontsdir):
            raise FileNotFoundError(
                f"subtitles: fontsdir のフォルダが見つかりません: {fontsdir}")
        font_names = _fontsdir_fonts(fontsdir)
    spec = {
        "kind": "subtitles",
        "srt": srt_file,
        "style": style,
        "fontsdir": fontsdir,
        # subtitlesフィルタは drawtext 系オプションを使わないが、_text_spec を
        # 種別を問わず .get() で読む側（audit の文字サイズ検査、project.py の
        # フォント依存収集など）が None/既定値を受け取れるよう形だけ揃えておく。
        "x": Const(0.5), "y": Const(0.5), "size": Const(48), "alpha": Const(1.0),
        "color": "white", "font": None, "box": False,
        "box_color": "black@0.5", "box_border": 10, "anchor": "center",
        "border": 0, "border_color": "black",
        "shadow": (0, 0), "shadow_color": "black@0.6",
    }
    # fontsdir は生パスではなく中のフォント名の並びを混ぜる（置き場所で ID を
    # 変えない）。未指定では鍵断片を足さない（既存の合成ソースIDを変えない）
    fonts_key = "" if font_names is None else "|fd:" + ",".join(font_names)
    try:
        ffp = _file_fingerprint(srt_file)
        spec["synthetic_source"] = _text_synthetic_source(
            f"subs|{ffp}|{style}{fonts_key}")
    except OSError:
        spec["synthetic_source"] = _text_synthetic_source(
            f"subs|{srt_file}|{style}{fonts_key}")
    obj = _new_text_object(spec)
    # SRTをレイヤー依存として登録（cache鮮度検証で字幕変更を検知）
    proj = current_project()
    if proj is not None and proj._current_layer_file:
        deps = proj._extra_layer_deps.setdefault(proj._current_layer_file, [])
        deps.append(srt_file)
        # 同梱フォントの差し替えも鮮度検証に掛ける（text() の font と同じ扱い）
        if font_names and len(font_names) <= _FONTSDIR_DEP_LIMIT:
            deps.extend(os.path.join(fontsdir, n) for n in font_names)
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


def karaoke(lines, *, style=None, fontsdir=None):
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
    fontsdir: style["font"] の名前を探すフォルダ（subtitles() の fontsdir と同じ）。

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
    # 原子的書き込み（一時パスは pid+uuid でユニーク化。同一内容の並列生成でも
    # 相互に壊さず、os.replace の後勝ちでも成果物は等価。issue #13 P2-15）。
    # 「存在すればスキップ」ガードは置かない — ファイル名が内容ハッシュなので、
    # 切り詰められた残骸が一度でも残ると以後どのレンダでも再生成されず、
    # 壊れた ASS が黙って使われ続ける（_ensure_textfile と同じ規約）。
    _atomic_write_text(ass_path, content)

    return subtitles(ass_path, fontsdir=fontsdir)


# --- 循環 import の回避（同一 SCC のモジュールのみ末尾で束縛。scripts/check_import_cycles.py で計測）---
from scriptvedit.cache import _file_fingerprint
from scriptvedit.ffmpeg import _atomic_write_text
from scriptvedit.filters.video import _u_expr
from scriptvedit.objects import Object
