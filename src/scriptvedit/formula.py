# -*- coding: utf-8 -*-
"""数式レンダリング（KaTeX + Playwright → 透過PNG）

LaTeX を外部プロセス（TeX 処理系）に投げるのではなく、**同梱した KaTeX**
（`templates/vendor/katex/`）を Playwright の Chromium でレンダリングし、
数式要素だけを **要素スクリーンショット**（透過）で切り出して PNG 化する。

- 外部ネットワーク不要（KaTeX はリポジトリ同梱・CDN 参照なし）
- 生成物は content-addressed キャッシュ（`__cache__/artifacts/formula/*.png`）
- 戻り値は通常の画像 Object なので move/fade/scale/rotate 等がそのまま効く
"""

import os
import re
import json
import hashlib

from scriptvedit.state import _ARTIFACT_DIR
from scriptvedit.cache import _file_fingerprint
from scriptvedit.ffmpeg import _unique_tmp_path
from scriptvedit.objects import Object
from scriptvedit.validate import _FFMPEG_COLOR_NAMES, _require_number
from scriptvedit.web import _TEMPLATES_DIR, _template_path


# --- パス定数 ---

_FORMULA_TEMPLATE = "formula.html"
_KATEX_DIR = os.path.join(_TEMPLATES_DIR, "vendor", "katex")
_FORMULA_CACHE_DIR = os.path.join(_ARTIFACT_DIR, "formula")

# 数式PNG生成のバージョン（テンプレート/切り出し仕様を変えたら上げる → 全キャッシュ無効化）
_FORMULA_VER = "1"

# ビューポート/出力サイズの上限（巨大な数式で Chromium を殺さない）
_FORMULA_MAX_PX = 8192

# CSS カラー関数の簡易バリデーション。16進の桁数と色名は別途厳密に検証する。
_CSS_COLOR_FUNC_RE = re.compile(
    r"^(rgb|rgba|hsl|hsla)\([0-9a-zA-Z.,%\s/+-]+\)$")
_CSS_ONLY_COLOR_NAMES = frozenset({
    "transparent", "rebeccapurple", "grey", "darkgrey", "darkslategrey",
    "dimgrey", "lightgray", "lightslategrey", "slategrey",
})


def _katex_fingerprint():
    """同梱KaTeX（vendorディレクトリ配下の**全ファイル**）+ テンプレートHTMLの内容指紋。

    css/js だけでなく **フォント(woff2 20件)も鍵に含める**。Chromium はフォントの
    読込失敗を例外にせずフォールバック字形でレンダするため、フォントが壊れても鍵が
    変わらないと、字形が崩れたPNGが焼き付いて二度と再生成されない。
    """
    for path in (_template_path(_FORMULA_TEMPLATE),
                 os.path.join(_KATEX_DIR, "katex.min.css"),
                 os.path.join(_KATEX_DIR, "katex.min.js")):
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"KaTeX の同梱ファイルが見つかりません: {path}\n"
                f"formula() は {_KATEX_DIR} に katex.min.css / katex.min.js / fonts/ が"
                f"配置されている必要があります。")
    from scriptvedit.cache import _renderer_identity
    parts = [f"renderer:{_renderer_identity()}",
             f"tpl:{_file_fingerprint(_template_path(_FORMULA_TEMPLATE))}"]
    # vendor(katex) のうち**レンダ結果に影響するファイルだけ**をハッシュ
    # （相対パス順で決定的に）。README 等のドキュメントを含めると、
    # 改行属性の履歴差（旧チェックアウトのCRLF vs fresh cloneのLF）だけで
    # 鍵が割れ、プラットフォーム間でスナップショットが食い違う（issue #13 CI）
    render_exts = (".css", ".js", ".woff2", ".woff", ".ttf", ".html")
    for root, dirs, files in os.walk(_KATEX_DIR):
        dirs.sort()
        for name in sorted(files):
            if not name.lower().endswith(render_exts):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, _KATEX_DIR).replace("\\", "/")
            parts.append(f"{rel}:{_file_fingerprint(path)}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


def _formula_cache_path(spec):
    """数式PNGの content-addressed キャッシュパス（LaTeX/サイズ/色/display等のハッシュ）"""
    key = dict(spec)
    key["katex"] = _katex_fingerprint()
    key["ver"] = _FORMULA_VER
    s = json.dumps(key, sort_keys=True, ensure_ascii=False)
    h = hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]
    return os.path.join(_FORMULA_CACHE_DIR, f"{h}.png")


# --- バリデーション ---

def _validate_latex(fn, latex):
    if not isinstance(latex, str):
        raise TypeError(f"{fn}: LaTeX は文字列で指定してください: {latex!r}")
    if not latex.strip():
        raise ValueError(f"{fn}: LaTeX が空です。数式を指定してください（例: r'x^2 + y^2 = r^2'）")
    return latex


def _validate_color(fn, color):
    if not isinstance(color, str):
        valid = False
        value = color
    else:
        value = color.strip()
        valid_hex = bool(re.fullmatch(
            r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]|[0-9a-fA-F]{3}|[0-9a-fA-F]{5})?",
            value))
        # 上の正規表現が受理するのは #RGB/#RGBA/#RRGGBB/#RRGGBBAA のみ。
        valid = (valid_hex or value.lower() in _FFMPEG_COLOR_NAMES
                 or value.lower() in _CSS_ONLY_COLOR_NAMES
                 or bool(_CSS_COLOR_FUNC_RE.fullmatch(value)))
    if not valid:
        raise ValueError(
            f"{fn}: color はCSSカラー文字列で指定してください: {color!r}\n"
            f"例: 'white' / '#ffcc00' / 'rgb(255,0,0)' / 'rgba(255,255,255,0.8)'")
    return value


def _build_formula_spec(fn, lines, size, color, display, padding, gap, align):
    """キャッシュ鍵かつブラウザへ渡すデータ（この dict の内容がPNGを一意に決める）"""
    for latex in lines:
        _validate_latex(fn, latex)
    _require_number(fn, "size", size, 1, 2000)
    _require_number(fn, "padding", padding, 0, 500)
    _require_number(fn, "gap", gap, 0, 2000)
    if not isinstance(display, bool):
        raise TypeError(f"{fn}: display は True/False で指定してください: {display!r}")
    if align not in ("left", "center", "right"):
        raise ValueError(
            f"{fn}: align は 'left'/'center'/'right' のいずれかです: {align!r}")
    return {
        "lines": list(lines),
        "size": float(size),
        "color": _validate_color(fn, color),
        "display": bool(display),
        "padding": float(padding),
        "gap": float(gap),
        "align": align,
    }


# --- Playwright レンダリング ---

def _import_playwright(fn):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            f"{fn}: 数式レンダリングには Playwright が必要です。\n"
            f"  pip install playwright\n"
            f"  playwright install chromium\n"
            f"を実行してください。") from None
    return sync_playwright


def _render_formula_png(spec, out_path, fn="formula"):
    """KaTeX + Playwright で数式を透過PNGへレンダ（要素スクリーンショット, アトミック書き込み）"""
    sync_playwright = _import_playwright(fn)
    html_path = os.path.abspath(_template_path(_FORMULA_TEMPLATE))
    url = "file:///" + html_path.replace(os.sep, "/")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # 一時パスは pid + 乱数でユニーク化（pid だけだと同一プロセス内の
    # 並列ワーカ同士で衝突しうる）。成功時のみ os.replace で確定する
    tmp_path = _unique_tmp_path(out_path)

    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as e:
                raise RuntimeError(
                    f"{fn}: Chromium の起動に失敗しました。\n"
                    f"  playwright install chromium\n"
                    f"を実行してブラウザを導入してください。\n元エラー: {e}") from None
            try:
                page = browser.new_page(viewport={"width": 1200, "height": 800})
                page.goto(url)
                # 同梱KaTeXのロード完了を待つ（CDN不使用のためオフラインでも成立する）
                page.wait_for_function(
                    "typeof globalThis.katex === 'object' "
                    "&& typeof globalThis.renderFormula === 'function'", timeout=10000)
                err = page.evaluate("d => globalThis.renderFormula(d)", spec)
                if err:
                    idx = err.get("index", 0)
                    bad = spec["lines"][idx] if idx < len(spec["lines"]) else ""
                    where = f"（{idx + 1}行目）" if len(spec["lines"]) > 1 else ""
                    raise ValueError(
                        f"{fn}: LaTeX の構文エラーです{where}: {bad!r}\n"
                        f"KaTeX: {err.get('error')}\n"
                        f"KaTeX がサポートするコマンドのみ使用できます"
                        f"（\\sum \\frac \\sqrt \\int \\begin{{cases}} 等）。")
                box = page.evaluate("() => globalThis.formulaBox()")
                w = int(box["w"] + 0.999)
                h = int(box["h"] + 0.999)
                if w < 1 or h < 1:
                    raise ValueError(
                        f"{fn}: 数式のレンダリング結果が空です: {spec['lines']!r}")
                if w > _FORMULA_MAX_PX or h > _FORMULA_MAX_PX:
                    raise ValueError(
                        f"{fn}: 数式が大きすぎます（{w}x{h}px, 上限 {_FORMULA_MAX_PX}px）。"
                        f"size={spec['size']} を小さくするか数式を分割してください。")
                # 数式全体が収まるビューポートにしてから要素スクリーンショット
                # （要素bboxぴったり = 余白トリム不要。omit_background で背景透過）
                page.set_viewport_size({"width": w, "height": h})
                # type="png" は明示指定（一時ファイル名から形式を推定させない）
                page.locator("#formula").screenshot(
                    path=tmp_path, type="png", omit_background=True)
            finally:
                browser.close()

        os.replace(tmp_path, out_path)  # アトミック置換（中断で壊れたPNGを残さない）
    finally:
        try:
            os.remove(tmp_path)  # 失敗時の残骸掃除（成功時は replace 済みで存在しない）
        except OSError:
            pass
    return out_path


# --- 公開API ---

def _new_formula_object(fn, lines, size, color, display, duration,
                        padding, gap, align):
    spec = _build_formula_spec(fn, lines, size, color, display, padding, gap, align)
    png = _formula_cache_path(spec)
    obj = Object(png)
    # 実レンダ直前に Project._ensure_formula_objects() が PNG を生成する
    # （dry_run では未生成の __cache__ 配下パス = probe 抑制対象のまま扱われる）
    obj._formula_spec = spec
    obj._formula_fn = fn
    if duration is not None:
        _require_number(fn, "duration", duration, 0.01, None)
        obj.time(float(duration))
    return obj


def formula(latex, *, size=48, color="white", display=True, duration=None,
            padding=4, align="left"):
    """LaTeX 数式を透過PNG化した画像Objectを返す（KaTeX同梱・オフライン動作）。

    戻り値は通常の画像Objectなので move/fade/scale/rotate 等がそのまま使える。

    latex: LaTeX 数式（r"..." 推奨）
    size: 基準フォントサイズpx（数式全体がこれに比例して拡大縮小する）
    color: 文字色（CSSカラー。'white' / '#ffcc00' / 'rgba(...)'）
    display: True=別行立て（displayMode）、False=インライン
    duration: 表示秒数（省略時は .time(秒) で指定する）
    padding: 数式まわりの余白px（切り出しbboxに含まれる）
    align: 複数行時の揃え（'left'/'center'/'right'）
    """
    return _new_formula_object("formula", [latex], size, color, display,
                               duration, padding, 0, align)


def formula_lines(latex_lines, *, size=48, color="white", display=True,
                  duration=None, padding=4, gap=12, align="left"):
    """複数のLaTeX数式を縦に並べた透過PNG画像Objectを返す（証明・式変形の提示向け）。

    latex_lines: LaTeX 数式のリスト
    gap: 行間px
    その他の引数は formula() と同じ。
    """
    if isinstance(latex_lines, str):
        raise TypeError(
            "formula_lines: latex_lines はリストで指定してください"
            "（1式なら formula() を使ってください）")
    lines = list(latex_lines)
    if not lines:
        raise ValueError("formula_lines: latex_lines が空です")
    return _new_formula_object("formula_lines", lines, size, color, display,
                               duration, padding, gap, align)
