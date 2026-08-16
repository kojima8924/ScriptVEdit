# -*- coding: utf-8 -*-

import os
import re
import json
import hashlib


# --- テンプレートラッパー ---

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def _template_path(name):
    """テンプレートHTMLの絶対パスを返す（存在チェック付き）"""
    path = os.path.join(_TEMPLATES_DIR, name)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"テンプレートが見つかりません: {path}\n"
            f"templates/ ディレクトリにファイルを配置してください。")
    return path


def _data_hash(data):
    """dataのJSON文字列からsha1先頭8桁のハッシュを返す"""
    s = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:8]


def _resolve_size(size):
    """size省略時はProject._currentのwidth/heightを使う"""
    if size is not None:
        return size
    proj = Project._current
    if proj is None:
        raise RuntimeError(
            "size省略時はアクティブなProjectが必要です。"
            "Project()を作成してからsubtitle/bubble/diagram等を呼んでください。")
    return (proj.width, proj.height)


def subtitle(text, duration=2.5, *, who=None, style=None, size=None,
             name=None, debug_frames=False, deps=None):
    """字幕テンプレートObjectを生成。第2位置引数は duration（秒）。

    who（話者ラベル）はキーワード専用。`subtitle('セリフ', 3)` を
    who=3 と解釈して話者名に「3」を描画してしまうのを防ぐため、
    subtitle / subtitle_box / bubble の第2位置引数は duration に統一する。
    """
    size = _resolve_size(size)
    tpl = _template_path("subtitle.html")
    data = {"text": text, "who": who, "style": style or {}}
    if name is None:
        name = f"subtitle_{_data_hash(data)}"
    kw = dict(duration=duration, size=size, data=data,
              name=name, debug_frames=debug_frames)
    if deps is not None:
        kw["deps"] = deps
    return Object(tpl, **kw)


def subtitle_box(text, duration=2.5, *, style=None, size=None,
                 name=None, debug_frames=False, deps=None):
    """ボックス型字幕テンプレートObjectを生成。第2位置引数は duration（秒）。"""
    size = _resolve_size(size)
    tpl = _template_path("subtitle_box.html")
    data = {"text": text, "style": style or {}}
    if name is None:
        name = f"subtitle_box_{_data_hash(data)}"
    kw = dict(duration=duration, size=size, data=data,
              name=name, debug_frames=debug_frames)
    if deps is not None:
        kw["deps"] = deps
    return Object(tpl, **kw)


def _validate_xy_ratio(func, param, value):
    """(x, y) の 0..1 比率タプルを検証する（文字列や3要素を黙って通さない）"""
    def _is_num(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or not all(_is_num(v) for v in value)):
        raise TypeError(
            f"{func}: {param} は (x, y) の2要素タプル（0..1 の画面比率）です: "
            f"{value!r}。例: {param}=(0.6, 0.75)")
    for axis, v in zip("xy", value):
        if not (0.0 <= v <= 1.0):
            raise ValueError(
                f"{func}: {param} の {axis} は 0〜1 の画面比率で指定してください: {v!r}")
    # 値は変換せずそのまま載せる（int→float の書き換えで data ハッシュ＝
    # web キャッシュ鍵が揺れるのを防ぐ）
    return {"x": value[0], "y": value[1]}


def bubble(text, duration=2.5, *, tail=None, pos=None, box=None,
           style=None, size=None, name=None, debug_frames=False,
           deps=None, **unknown):
    """吹き出しテンプレートObjectを生成。第2位置引数は duration（秒）。

    tail: 吹き出しの尻尾が指す位置 (x, y)（0..1 の画面比率）。
      旧名 anchor は move の anchor（基準点の列挙値）と型も意味も違う同名
      だったため改名した。pos は吹き出し本体の位置、box は本体のサイズ。
    """
    if unknown:
        if "anchor" in unknown:
            raise TypeError(
                "bubble: anchor は tail へ改名しました"
                "（move の anchor＝基準点の列挙値と型も意味も違う同名だったため）。"
                "尻尾が指す位置は tail=(0.6, 0.75) のように 0..1 の比率タプルで"
                "指定してください。")
        raise TypeError(
            f"bubble: 未知のキーワード引数 {sorted(unknown)}"
            f"（有効なキー: tail, pos, box, style, size, name, "
            f"debug_frames, deps）")
    size = _resolve_size(size)
    tpl = _template_path("bubble.html")
    # JSON のキー名 "anch" はテンプレート bubble.html が読む契約なので据え置く
    # （変えると web キャッシュが全再生成される）
    anch = (_validate_xy_ratio("bubble", "tail", tail) if tail is not None
            else {"x": 0.2, "y": 0.7})
    p = (_validate_xy_ratio("bubble", "pos", pos) if pos is not None
         else {"x": 0.25, "y": 0.3})
    sz = {"w": box[0], "h": box[1]} if box else {"w": 0.45, "h": 0.2}
    data = {"text": text, "anchor": anch, "pos": p, "size": sz,
            "style": style or {}}
    if name is None:
        name = f"bubble_{_data_hash(data)}"
    kw = dict(duration=duration, size=size, data=data,
              name=name, debug_frames=debug_frames)
    if deps is not None:
        kw["deps"] = deps
    return Object(tpl, **kw)


# --- diagram() の入力検証（SVG属性注入対策: issue #13）---

# 色として許可する形式: CSS色名(英字のみ) / #hex(3,4,6,8桁) / rgb()/rgba()/hsl()/hsla()
# url(...) や式・イベントハンドラを含む文字列は通さない。
_DIAGRAM_COLOR_RE = re.compile(
    r"^(?:[A-Za-z]+"
    r"|#(?:[0-9A-Fa-f]{3,4}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})"
    r"|(?:rgb|rgba|hsl|hsla)\(\s*[0-9.,%\s/]+\s*\))$"
)

# 数値属性（数値または {"from": 数値, "to": 数値} のアニメ指定を許可）
_DIAGRAM_NUM_KEYS = {"x", "y", "r", "w", "h", "rx", "x1", "y1", "x2", "y2",
                     "strokeWidth", "fontSize", "dim", "opacity"}
# 色属性
_DIAGRAM_COLOR_KEYS = {"fill", "stroke"}
# text の align 列挙値
_DIAGRAM_ALIGNS = {"start", "left", "middle", "end", "right"}
# 図形typeごとの許可キー（"type" 自体は別扱い）
_DIAGRAM_ALLOWED_KEYS = {
    "circle": {"x", "y", "r", "fill", "stroke", "strokeWidth", "opacity"},
    "rect": {"x", "y", "w", "h", "rx", "fill", "stroke", "strokeWidth",
             "opacity"},
    "line": {"x1", "y1", "x2", "y2", "stroke", "strokeWidth", "opacity"},
    "arrow": {"x1", "y1", "x2", "y2", "stroke", "strokeWidth", "opacity"},
    "text": {"x", "y", "text", "align", "fill", "fontSize", "stroke",
             "strokeWidth", "opacity"},
    "spotlight": {"x", "y", "r", "dim", "opacity"},
}


def _validate_diagram_number(where, key, value):
    """数値または {"from": 数値, "to": 数値} のみ許可"""
    def _is_num(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if _is_num(value):
        return
    if (isinstance(value, dict) and set(value.keys()) == {"from", "to"}
            and _is_num(value["from"]) and _is_num(value["to"])):
        return
    raise ValueError(
        f"diagram: {where} の {key} は数値または"
        f" {{'from': 数値, 'to': 数値}} で指定してください: {value!r}")


def _validate_diagram_objects(objects):
    """diagram のオブジェクト定義をwhitelist検証する。

    fill 等のユーザー指定値がテンプレート(SVG)側へそのまま渡るため、
    色・数値・列挙値を型/形式で検証し、想定外の属性は拒否する。
    """
    if not isinstance(objects, (list, tuple)):
        raise TypeError(
            f"diagram: objects はリストで指定してください: {type(objects).__name__}")
    for idx, obj in enumerate(objects):
        if not isinstance(obj, dict):
            raise TypeError(
                f"diagram: objects[{idx}] は辞書で指定してください"
                f"（circle()/rect()等のビルダーを使う）: {obj!r}")
        t = obj.get("type")
        if t not in _DIAGRAM_ALLOWED_KEYS:
            raise ValueError(
                f"diagram: objects[{idx}] の type が不正です: {t!r}"
                f"（許可: {', '.join(sorted(_DIAGRAM_ALLOWED_KEYS))}）")
        allowed = _DIAGRAM_ALLOWED_KEYS[t]
        where = f"objects[{idx}]({t})"
        for key, value in obj.items():
            if key == "type":
                continue
            if key not in allowed:
                raise ValueError(
                    f"diagram: {where} に不明な属性 {key!r}"
                    f"（許可: {', '.join(sorted(allowed))}）")
            if key in _DIAGRAM_NUM_KEYS:
                _validate_diagram_number(where, key, value)
            elif key in _DIAGRAM_COLOR_KEYS:
                if not isinstance(value, str) or not _DIAGRAM_COLOR_RE.match(value):
                    raise ValueError(
                        f"diagram: {where} の {key} は色"
                        f"（CSS色名 / #hex / rgb() / hsl()）で"
                        f"指定してください: {value!r}")
            elif key == "text":
                if not isinstance(value, str):
                    raise TypeError(
                        f"diagram: {where} の text は文字列で"
                        f"指定してください: {value!r}")
            elif key == "align":
                if value not in _DIAGRAM_ALIGNS:
                    raise ValueError(
                        f"diagram: {where} の align が不正です: {value!r}"
                        f"（許可: {', '.join(sorted(_DIAGRAM_ALIGNS))}）")


def diagram(objects, duration=3.0, *, style=None, size=None,
            name=None, debug_frames=False, deps=None):
    """SVG図解テンプレートObjectを生成"""
    _validate_diagram_objects(objects)
    size = _resolve_size(size)
    tpl = _template_path("diagram_svg.html")
    data = {"objects": objects, "style": style or {}}
    if name is None:
        name = f"diagram_{_data_hash(data)}"
    kw = dict(duration=duration, size=size, data=data,
              name=name, debug_frames=debug_frames)
    if deps is not None:
        kw["deps"] = deps
    return Object(tpl, **kw)


def slide(html_file, page=None, *, duration=5.0, width=None, height=None,
          name=None, debug_frames=False, deps=None):
    """HTMLスライドを既存のweb Object機構でキャプチャする正式API。

    page省略時: html_file全体を通常のWeb Object（renderFrame(state)による
        アニメーション）としてduration秒キャプチャする（既存のObject(html,...)
        直接呼び出しと同じ）。

    page指定時（複数ページを持つ1つのHTMLをスライドデッキとして扱う規約）:
        キャプチャ直前に以下のJSフックを実行してからduration秒分キャプチャする。
          1. `window.showSlide` 関数があれば `window.showSlide(page)` を呼ぶ。
          2. 無ければ `id="page-<page>"` の要素だけを表示し、他の
             `id^="page-"` 要素は非表示（style.display='none'）にする。
          3. HTML側に `renderFrame(state)` が定義されていなければ、
             自動的にno-op実装を注入する（静止スライドはrenderFrame不要）。
        この規約に沿ったHTMLを用意すれば、1ファイルで複数ページのスライドを
        page番号違いのslide()呼び出しだけで使い分けられる。

    width/height省略時はアクティブなProjectの解像度を使う。
    キャッシュはWeb Objectと同じsignature方式（データ内容が変わればキー変化）。
    """
    if not isinstance(html_file, str):
        raise TypeError(f"slide: html_file はパス文字列で指定してください: {html_file!r}")
    if not os.path.exists(html_file):
        raise FileNotFoundError(f"slide: HTMLファイルが見つかりません: {html_file}")
    if os.path.splitext(html_file)[1].lower() not in (".html", ".htm"):
        raise ValueError(f"slide: .html/.htm ファイルを指定してください: {html_file}")
    _require_number("slide", "duration", duration, 0.01, None)
    proj = Project._current
    if width is None or height is None:
        if proj is None:
            raise RuntimeError(
                "slide: width/height省略時はアクティブなProjectが必要です。"
                "Project()を作成してからslide()を呼んでください。")
    w = int(width) if width is not None else proj.width
    h = int(height) if height is not None else proj.height

    data = {}
    if page is not None:
        if not isinstance(page, int) or isinstance(page, bool) or page < 0:
            raise ValueError(f"slide: page は0以上の整数で指定してください: {page!r}")
        data[_SLIDE_PAGE_KEY] = page

    if name is None:
        base = os.path.splitext(os.path.basename(html_file))[0]
        name = f"slide_{base}_p{page}" if page is not None else f"slide_{base}"
    kw = dict(duration=float(duration), size=(w, h), data=data, name=name,
              debug_frames=debug_frames)
    if deps is not None:
        kw["deps"] = deps
    return Object(html_file, **kw)


# --- 図形ビルダー ---

def circle(x, y, r, **kw):
    """円オブジェクト定義を返す"""
    return {"type": "circle", "x": x, "y": y, "r": r, **kw}


def rect(x, y, w, h, **kw):
    """矩形オブジェクト定義を返す"""
    return {"type": "rect", "x": x, "y": y, "w": w, "h": h, **kw}


def arrow(x1, y1, x2, y2, **kw):
    """矢印オブジェクト定義を返す"""
    return {"type": "arrow", "x1": x1, "y1": y1, "x2": x2, "y2": y2, **kw}


def label(x, y, text, **kw):
    """テキストラベルオブジェクト定義を返す"""
    return {"type": "text", "x": x, "y": y, "text": text, **kw}


def spotlight(x, y, r, **kw):
    """スポットライト（暗幕くり抜き）オブジェクト定義を返す"""
    return {"type": "spotlight", "x": x, "y": y, "r": r, **kw}


# --- 遅延解決の相互参照（関数本体からのみ使用: 循環importを避けるため末尾で束縛）---
from scriptvedit.objects import Object, _SLIDE_PAGE_KEY
from scriptvedit.project import Project
from scriptvedit.validate import _require_number
