# -*- coding: utf-8 -*-
"""文字を透過 PNG に焼く（PIL）: text_image()

drawtext（text() / typewriter() / counter()）では出来ないことを受け持つ:

- 1つの文字列の一部だけ色・太さ・書体・大きさを変える（区間のリスト / 簡易マークアップ）
- 可変フォントの太さ（wght 軸）・名前つきインスタンス、.ttc の書体番号
- 行送り（line_spacing）・行ごとの揃え（align）・自動折り返し（max_width、行頭禁則）
- 行の縦位置を書体のメトリクス（ascent / descent）で決める（字によって行がガタつかない）
- 戻り値が**画像 Object** なので morph_to / explode_to / assemble_from の入力にも
  target / source にも使える（text() 系は実体ファイルが無く、終端フレーム Effect を掛けられない）

生成物は content-addressed キャッシュ（`__cache__/artifacts/textimage/<鍵>.png`）。
鍵は「文字列・書式・フォントファイルの内容指紋・版・Pillow の版」で、生パスは混ぜない。

PIL（Pillow）は optional 依存なので関数内で遅延 import する。
"""

import os
import json
import bisect
import math
import struct
import hashlib
import warnings
import unicodedata

from scriptvedit.state import _ARTIFACT_DIR, _suggest_hint
from scriptvedit.cache import _file_fingerprint
from scriptvedit.ffmpeg import _unique_tmp_path
from scriptvedit.objects import Object
from scriptvedit.text import _resolve_font
from scriptvedit.validate import _require_number, _validate_ffmpeg_color


# --- 定数 ---

_TEXT_IMAGE_DIR = os.path.join(_ARTIFACT_DIR, "textimage")

# 描画仕様の版（レイアウト・描き方を変えたら上げる → 全キャッシュ無効化）
_TEXT_IMAGE_VER = "1"

# 出力 PNG の上限（巨大な画像で PIL / ffmpeg を殺さない）
_TEXT_IMAGE_MAX_PX = 8192

# 区間ごとに上書きできる書式のキー
_STYLE_KEYS = ("color", "size", "font", "font_index", "weight")

_ALIGNS = ("left", "center", "right")
_MISSING_MODES = ("error", "warn", "ignore")

# 行頭禁則（自動折り返しで行頭に来てはいけない字）: 句読点と閉じ括弧の類だけ。
# 該当する字が行頭に来る折り返しは、直前の単位ごと次の行へ送る（追い出し）。
# 長音・波線・三点リーダ（ー ～ …）は入れない（「ーーーー」「…………」のように
# 連ねて書く字で、追い出しが連鎖するだけで得るものが無い）。
_KINSOKU_HEAD = frozenset(
    "、。，．,.!?！？:;：；・"
    "）」』】〕］｝〉》〗〙)]}＞")

# 追い出しで次の行へ送る単位の上限（空白は数えない）。
# 「だ！？」のように禁則字が2つ並ぶところまでは送り、それより長い連続
# （「！！！！！」）は追い出しをやめて元の位置で折る（禁則違反を許す）。
# 上限が無いと、禁則字の連続が1行ぶん以上あるとき1行1字の行を量産する。
_KINSOKU_MAX_PULL = 3

# 既に書き出した鍵（プロセス内メモ）。レイヤーは Plan / Render で複数回 exec されるので、
# 同じ画像を1レンダで何度も検証・書き直ししない。
_WRITTEN_MEMO = set()

# (フォントパス, 書体番号, サイズ, 軸の値) -> ImageFont（プロセス内メモ）
_FONT_MEMO = {}

# (フォントパス, 書体番号) -> fvar の (軸 [(tag, 最小, 既定, 最大), ...], インスタンスの軸の値)
_FVAR_MEMO = {}

# (フォントパス, 書体番号) -> cmap の判定関数（読めなければ None）
_CMAP_MEMO = {}

# Pillow の下限（ImageFont.Layout が入った版。pyproject.toml の extras と揃える）
_PIL_MIN_VERSION = "9.1"


# --- PIL の遅延 import ---

def _import_pil(fn):
    """PIL（Pillow）を遅延 import する。無ければ導入方法つきの RuntimeError"""
    try:
        import PIL
        from PIL import (Image, ImageChops, ImageColor, ImageDraw, ImageFilter,
                         ImageFont)
    except ImportError:
        raise RuntimeError(
            f"{fn}: 文字画像の描画には Pillow が必要です。\n"
            f"  pip install Pillow\n"
            f"（または pip install \"scriptvedit[tools]\"）を実行してください。") from None
    if not hasattr(ImageFont, "Layout"):
        raise RuntimeError(
            f"{fn}: Pillow {_PIL_MIN_VERSION} 以上が必要です"
            f"（導入済み: {getattr(PIL, '__version__', '不明')}）。\n"
            f"  pip install -U \"Pillow>={_PIL_MIN_VERSION}\"\n"
            f"を実行してください。")
    return {"PIL": PIL, "Image": Image, "ImageChops": ImageChops,
            "ImageColor": ImageColor, "ImageDraw": ImageDraw,
            "ImageFilter": ImageFilter, "ImageFont": ImageFont}


# --- 色 ---

def _rgba(fn, name, color, pil):
    """ffmpeg 形式の色（色名[@alpha] / #RRGGBB[AA] / 0xRRGGBB[AA]）を (R, G, B, A) にする"""
    norm = _validate_ffmpeg_color(f"{fn}: {name}", color)
    if norm.startswith("0x"):
        h = norm[2:]
        a = int(h[6:8], 16) if len(h) == 8 else 255
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)
    cname, sep, alpha_text = norm.partition("@")
    try:
        rgb = pil["ImageColor"].getrgb(cname)[:3]
    except ValueError:
        raise ValueError(
            f"{fn}: {name} の色名 '{cname}' を RGB に変換できません。"
            f"#RRGGBB で指定してください") from None
    a = int(round(float(alpha_text) * 255)) if sep else 255
    return (rgb[0], rgb[1], rgb[2], a)


# --- フォント（可変フォントの軸・.ttc の書体番号）---

def _sfnt_table(path, index, tag):
    """フォントファイル（.ttf / .otf / .ttc）から表を1つ読む。無ければ None。

    標準ライブラリだけで読む（fontTools に依存しない）。読めないファイルも None。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            base = 0
            if head[:4] == b"ttcf":
                n_fonts = struct.unpack(">I", head[8:12])[0]
                if not 0 <= index < n_fonts:
                    return None
                f.seek(12 + 4 * index)
                base = struct.unpack(">I", f.read(4))[0]
                f.seek(base)
                head = f.read(12)
            num_tables = struct.unpack(">H", head[4:6])[0]
            f.seek(base + 12)
            records = f.read(16 * num_tables)
            for i in range(num_tables):
                rtag, _chk, off, length = struct.unpack_from(">4sIII", records, 16 * i)
                if rtag == tag:
                    f.seek(off)
                    return f.read(length)
    except (OSError, struct.error):
        return None
    return None


def _fvar(path, index):
    """フォントファイルの fvar（可変フォントの軸と名前つきインスタンス）を読む。

    戻り値: (軸 [(tag, 最小, 既定, 最大), ...], インスタンス [(軸の値, ...), ...])。
    軸の並びは FreeType と同じ fvar 順、インスタンスの並びは PIL の
    get_variation_names() と同じ fvar 順。可変フォントでなければ ([], [])。
    PIL の get_variation_axes() は軸の「名前」（name テーブルの表示名。言語や書体で
    変わる）しか返さないので、wght を確実に見つけるためにタグを自前で読む。
    """
    key = (path, index)
    if key in _FVAR_MEMO:
        return _FVAR_MEMO[key]
    axes = []
    instances = []
    data = _sfnt_table(path, index, b"fvar")
    if data is not None:
        try:
            (axes_off, _res, n_axes, axis_size,
             n_inst, inst_size) = struct.unpack_from(">HHHHHH", data, 4)
            for a in range(n_axes):
                atag, lo, default, hi = struct.unpack_from(
                    ">4siii", data, axes_off + a * axis_size)
                axes.append((atag.decode("ascii", "replace"),
                             lo / 65536.0, default / 65536.0, hi / 65536.0))
            inst_off = axes_off + n_axes * axis_size
            for k in range(n_inst):
                coords = struct.unpack_from(
                    f">{n_axes}i", data, inst_off + k * inst_size + 4)
                instances.append(tuple(c / 65536.0 for c in coords))
        except struct.error:
            axes, instances = [], []
    _FVAR_MEMO[key] = (axes, instances)
    return _FVAR_MEMO[key]


def _fvar_axes(path, index):
    """可変フォントの軸 [(tag, 最小, 既定, 最大), ...]。可変フォントでなければ空"""
    return _fvar(path, index)[0]


def _cmap_lookup(path, index):
    """フォントの cmap を読み、「そのコードポイントにグリフが割り当てられているか」を
    返す関数にする。Unicode の cmap（format 4 / 12）が読めなければ None。

    FreeType が既定で選ぶのと同じく、Unicode 全域（platform 3 / encoding 10、
    platform 0 / encoding 4 以上）を BMP だけの表より優先する。
    """
    key = (path, index)
    if key in _CMAP_MEMO:
        return _CMAP_MEMO[key]
    lookup = None
    data = _sfnt_table(path, index, b"cmap")
    if data is not None:
        try:
            lookup = _parse_cmap(data)
        except (struct.error, IndexError):
            lookup = None
    _CMAP_MEMO[key] = lookup
    return lookup


def _parse_cmap(data):
    """cmap の表から Unicode の subtable（format 4 / 12）を1つ選び、判定関数にする"""
    n_sub = struct.unpack_from(">H", data, 2)[0]
    cands = []
    for i in range(n_sub):
        platform, encoding, off = struct.unpack_from(">HHI", data, 4 + 8 * i)
        fmt = struct.unpack_from(">H", data, off)[0]
        if fmt not in (4, 12):
            continue
        if platform == 3 and encoding == 10:
            rank = 0
        elif platform == 0 and encoding in (4, 6):
            rank = 1
        elif platform == 3 and encoding == 1:
            rank = 2
        elif platform == 0:
            rank = 3
        else:
            continue     # シンボル用（3,0）や Mac Roman は Unicode の表ではない
        cands.append((rank, i, fmt, off))
    if not cands:
        return None
    _rank, _i, fmt, off = min(cands)
    if fmt == 12:
        n_groups = struct.unpack_from(">I", data, off + 12)[0]
        groups = [struct.unpack_from(">III", data, off + 16 + 12 * g)
                  for g in range(n_groups)]
        groups.sort()
        starts = [g[0] for g in groups]

        def has12(cp):
            k = bisect.bisect_right(starts, cp) - 1
            if k < 0:
                return False
            start, end, start_gid = groups[k]
            return cp <= end and start_gid + (cp - start) != 0
        return has12

    seg_count = struct.unpack_from(">H", data, off + 6)[0] // 2
    end_off = off + 14
    start_off = end_off + 2 * seg_count + 2
    delta_off = start_off + 2 * seg_count
    range_off = delta_off + 2 * seg_count
    ends = struct.unpack_from(f">{seg_count}H", data, end_off)
    starts4 = struct.unpack_from(f">{seg_count}H", data, start_off)
    deltas = struct.unpack_from(f">{seg_count}H", data, delta_off)
    ranges = struct.unpack_from(f">{seg_count}H", data, range_off)

    def has4(cp):
        if cp > 0xFFFF:
            return False
        k = bisect.bisect_left(ends, cp)
        if k >= seg_count or starts4[k] > cp:
            return False
        if ranges[k] == 0:
            return (cp + deltas[k]) & 0xFFFF != 0
        pos = range_off + 2 * k + ranges[k] + 2 * (cp - starts4[k])
        if pos + 2 > len(data):
            return False
        gid = struct.unpack_from(">H", data, pos)[0]
        return gid != 0 and (gid + deltas[k]) & 0xFFFF != 0
    return has4


def _fmt_num(v):
    return f"{v:g}"


def _norm_num(v):
    """整数値の float を int にそろえる（64 と 64.0 で鍵とフォントのメモを割らない）"""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _open_font(fn, pil, path, index, size):
    """フォントファイルを開く（太さは未設定）"""
    ImageFont = pil["ImageFont"]
    try:
        # レイアウトは BASIC に固定する（raqm の有無で字送りが変わると、
        # 同じ鍵なのに環境ごとに違う絵になる）
        return ImageFont.truetype(path, size, index=index,
                                  layout_engine=ImageFont.Layout.BASIC)
    except (OSError, ValueError) as e:
        raise ValueError(
            f"{fn}: フォントを開けません: {path}（font_index={index}）。"
            f".ttc の書体番号が範囲外か、フォントファイルが壊れています。\n元エラー: {e}") from None


def _variation_coords(fn, pil, path, index, weight):
    """weight（数値＝wght 軸の値 / 文字列＝名前つきインスタンス）を、可変フォントの
    軸の値の並び（fvar 順のタプル）へ正規化する。weight=None は None。

    名前つきインスタンスも軸の値にするのは、キャッシュ鍵を「同一出力なら同一鍵」に
    するため（weight="Bold" と weight=700 は同じ絵なので同じ鍵）。既定の軸の値と
    同じになる指定（wght の既定値を明示した等）も None にそろえる。
    """
    if weight is None:
        return None
    base = os.path.basename(path)
    axes, instances = _fvar(path, index)
    defaults = tuple(a[2] for a in axes)
    if isinstance(weight, str):
        try:
            font = _open_font(fn, pil, path, index, 16)
            names = [n.decode("utf-8", "replace") if isinstance(n, bytes) else str(n)
                     for n in font.get_variation_names()]
        except (OSError, AttributeError):
            names = []
        if not names:
            raise ValueError(
                f"{fn}: weight='{weight}' は可変フォントの名前つきインスタンスですが、"
                f"{base} は可変フォントではありません（太さ違いは別のフォントファイルを"
                f"font= で指定してください）")
        if weight not in names:
            raise ValueError(
                f"{fn}: {base} に '{weight}' というインスタンスはありません。"
                f"有効な名前: {', '.join(names)}"
                f"{_suggest_hint(weight, names)}")
        if len(instances) != len(names):
            raise ValueError(
                f"{fn}: {base} の名前つきインスタンスを読めません"
                f"（fvar のインスタンス数 {len(instances)} と名前の数 {len(names)} が"
                f"合いません）。weight= は数値（wght 軸の値）で指定してください")
        coords = instances[names.index(weight)]
    else:
        tags = [a[0] for a in axes]
        if not axes:
            raise ValueError(
                f"{fn}: weight={_fmt_num(weight)} を指定しましたが、{base} は"
                f"可変フォントではありません（太さ違いは別のフォントファイルを"
                f"font= で指定してください。黙って無視はしません）")
        if "wght" not in tags:
            raise ValueError(
                f"{fn}: {base} は可変フォントですが太さ（wght）の軸がありません"
                f"（軸: {', '.join(tags)}）")
        _tag, lo, _default, hi = axes[tags.index("wght")]
        if not lo <= weight <= hi:
            raise ValueError(
                f"{fn}: weight={_fmt_num(weight)} は {base} の範囲外です"
                f"（{_fmt_num(lo)}〜{_fmt_num(hi)}）")
        coords = tuple(float(weight) if a[0] == "wght" else a[2] for a in axes)
    return None if coords == defaults else coords


def _load_font(fn, pil, path, index, size, coords):
    """フォントを開き、可変フォントの軸の値（_variation_coords の結果）を設定する"""
    key = (path, index, size, coords)
    font = _FONT_MEMO.get(key)
    if font is not None:
        return font
    font = _open_font(fn, pil, path, index, size)
    if coords is not None:
        try:
            font.set_variation_by_axes(list(coords))
        except (OSError, AttributeError) as e:
            raise ValueError(
                f"{fn}: {os.path.basename(path)} の太さを設定できません"
                f"（Pillow / FreeType が可変フォントに対応していない可能性）。"
                f"\n元エラー: {e}") from None
    _FONT_MEMO[key] = font
    return font


def _mask_signature(font, ch):
    """1文字の描画結果（サイズと画素）。豆腐の判定に使う"""
    mask = font.getmask(ch, mode="L")
    return (mask.size, bytes(mask))


def _missing_glyphs(font, chars, path=None, index=0):
    """フォントに無い字（.notdef＝豆腐で描かれる字）を返す。

    正は cmap（コードポイントにグリフが割り当てられているか）。画素の比較だけだと、
    cmap に在るのにグリフの絵が .notdef と同じ字（MS ゴシックの '□' U+25A1 など）を
    「無い」と誤判定する。cmap を読めないフォント（シンボル用の表しか無い等）だけ、
    どのフォントにも割り当てが無い U+FFFF の描画結果と同じ画素になる字を「無い」と
    みなす（.notdef が空白のフォントでは「何も描かれない字」）。
    空白・制御・書式文字・結合文字は対象外。
    """
    chars = [ch for ch in chars
             if not (ch.isspace()
                     or unicodedata.category(ch) in ("Cc", "Cf", "Mn", "Me"))]
    has = _cmap_lookup(path, index) if path is not None else None
    if has is not None:
        return [ch for ch in chars if not has(ord(ch))]
    notdef = _mask_signature(font, "\uffff")
    blank_notdef = not any(notdef[1])
    missing = []
    for ch in chars:
        sig = _mask_signature(font, ch)
        if blank_notdef:
            if not any(sig[1]):
                missing.append(ch)
        elif sig == notdef:
            missing.append(ch)
    return missing


# --- 書式（区間のリスト / 簡易マークアップ）---

def _parse_markup(fn, s):
    """簡易マークアップを [(文字列, 書式指定の文字列 or None), ...] へ分解する。

    書き方: `{書式|文字}`。例: "犯人は、{red|正規表現が1本}だった"
    - 書式はカンマ区切り。`キー=値`（color / size / weight / font / font_index）か、
      裸の語（styles= に登録した名前、無ければ色名・#RRGGBB）
    - 入れ子は不可。区間の中の `|` は文字として出る（区切りは最初の1つだけ）
    - エスケープ: `\\{` `\\}` `\\\\` の3つだけ。それ以外の `\\` はそのまま文字として出る
    - 波括弧を多用する文字列（コード・正規表現）は、マークアップを使わず
      区間のリスト [("文字", {...}), ...] で渡す（エスケープ不要）
    """
    out = []
    buf = []
    spec = None      # 区間の中にいるとき: 書式指定（'|' を読むまでは None のまま in_span で判定）
    in_span = False
    spec_buf = None  # '|' を読む前の書式指定を溜める
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n and s[i + 1] in "{}\\":
            (spec_buf if spec_buf is not None else buf).append(s[i + 1])
            i += 2
            continue
        if ch == "{":
            if in_span:
                raise ValueError(
                    f"{fn}: マークアップの区間は入れ子にできません（{i} 文字目の '{{'）。"
                    f"波括弧そのものを出すには \\{{ と書きます: {s!r}")
            if buf:
                out.append(("".join(buf), None))
                buf = []
            in_span = True
            spec_buf = []
        elif ch == "|" and spec_buf is not None:
            spec = "".join(spec_buf).strip()
            spec_buf = None
            if not spec:
                raise ValueError(
                    f"{fn}: マークアップの書式が空です（{i} 文字目の '|' の前）: {s!r}")
        elif ch == "}":
            if not in_span:
                raise ValueError(
                    f"{fn}: 対応する '{{' の無い '}}' があります（{i} 文字目）。"
                    f"波括弧そのものを出すには \\}} と書きます: {s!r}")
            if spec_buf is not None:
                raise ValueError(
                    f"{fn}: マークアップは {{書式|文字}} の形で書きます"
                    f"（{i} 文字目の '}}' までに '|' がありません）。"
                    f"波括弧そのものを出すには \\{{ \\}} と書きます: {s!r}")
            out.append(("".join(buf), spec))
            buf = []
            in_span = False
            spec = None
        else:
            (spec_buf if spec_buf is not None else buf).append(ch)
        i += 1
    if in_span:
        raise ValueError(
            f"{fn}: マークアップの '{{' が閉じていません。"
            f"波括弧そのものを出すには \\{{ と書きます: {s!r}")
    if buf:
        out.append(("".join(buf), None))
    return out


def _markup_style(fn, spec, styles):
    """マークアップの書式指定（'red, weight=900' 等）を書式 dict にする"""
    style = {}
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "=" in token:
            key, _sep, value = token.partition("=")
            key = key.strip()
            value = value.strip()
            if key not in _STYLE_KEYS:
                raise ValueError(
                    f"{fn}: マークアップの未知の書式キー '{key}'"
                    f"（有効なキー: {', '.join(_STYLE_KEYS)}）"
                    f"{_suggest_hint(key, _STYLE_KEYS)}")
            if key in ("size", "font_index") or (
                    key == "weight" and _is_number_text(value)):
                try:
                    if key == "font_index" or value.lstrip("+-").isdigit():
                        value = int(value)
                    else:
                        value = float(value)
                except ValueError:
                    raise ValueError(
                        f"{fn}: マークアップの {key} は"
                        f"{'整数' if key == 'font_index' else '数値'}で指定してください: "
                        f"{{{spec}|…}} の {key}={value!r}") from None
            style[key] = value
        elif token in styles:
            style.update(styles[token])
        else:
            style["color"] = token
    return style


def _is_number_text(s):
    try:
        float(s)
    except ValueError:
        return False
    return True


def _check_style(fn, where, style):
    """書式 dict の検証（未知キー・型・範囲）。検証済みのコピーを返す"""
    if not isinstance(style, dict):
        raise TypeError(
            f"{fn}: {where} の書式は dict で指定してください"
            f"（例: {{'color': 'red', 'weight': 900}}）: {style!r}")
    unknown = sorted(set(style) - set(_STYLE_KEYS))
    if unknown:
        raise ValueError(
            f"{fn}: {where} に未知の書式キー {unknown}"
            f"（有効なキー: {', '.join(_STYLE_KEYS)}）"
            f"{_suggest_hint(unknown[0], _STYLE_KEYS)}")
    out = dict(style)
    if "size" in out:
        _require_number(fn, f"{where} の size", out["size"], 1, 2000)
    if "font_index" in out:
        idx = out["font_index"]
        if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0:
            raise ValueError(
                f"{fn}: {where} の font_index は 0 以上の整数で指定してください: {idx!r}")
    if out.get("weight") is not None and not isinstance(out["weight"], str):
        _require_number(fn, f"{where} の weight", out["weight"], 1, 2000)
    if out.get("font") is not None and not isinstance(out["font"], str):
        raise TypeError(
            f"{fn}: {where} の font はフォントファイルのパスで指定してください: "
            f"{out['font']!r}")
    return out


def _normalize_spans(fn, content, markup, styles):
    """content を [(文字列, 上書き書式 dict), ...] にそろえる"""
    if isinstance(content, str):
        if not markup:
            return [(content, {})]
        return [(text, _markup_style(fn, spec, styles) if spec else {})
                for text, spec in _parse_markup(fn, content)]
    if not isinstance(content, (list, tuple)):
        raise TypeError(
            f"{fn}: content は文字列か、区間のリスト "
            f"[(\"文字\", {{書式}}), ...] で指定してください: {content!r}")
    if markup:
        raise ValueError(
            f"{fn}: markup=True は content が文字列のときだけ使えます"
            f"（区間のリストは書式を直接持つのでマークアップ不要）")
    spans = []
    for i, item in enumerate(content):
        where = f"content[{i}]"
        if isinstance(item, str):
            spans.append((item, {}))
            continue
        if (not isinstance(item, (list, tuple)) or len(item) != 2
                or not isinstance(item[0], str)):
            raise TypeError(
                f"{fn}: {where} は文字列か (\"文字\", 書式) の2要素で指定してください: "
                f"{item!r}")
        text, style = item
        if isinstance(style, str):
            if style not in styles:
                raise ValueError(
                    f"{fn}: {where} の書式名 '{style}' は styles= にありません"
                    f"（登録済み: {', '.join(sorted(styles)) or 'なし'}）"
                    f"{_suggest_hint(style, styles)}")
            style = styles[style]
        spans.append((text, style))
    return spans


def _resolve_style(fn, where, base, override):
    """基本書式に区間の上書きを重ねる。

    区間が font を指定したとき、weight と font_index は基本書式から**引き継がない**
    （別の書体に、元の書体の太さ・書体番号を当てると範囲外や「可変フォントではない」
    エラーになるため）。必要なら同じ区間で一緒に指定する。
    """
    override = _check_style(fn, where, override)
    style = dict(base)
    if override.get("font") is not None:
        style["font"] = _resolve_font(override["font"])
        style["weight"] = None
        style["font_index"] = 0
    for key in ("color", "size", "font_index", "weight"):
        if key in override:
            style[key] = override[key]
    style["size"] = _norm_num(style["size"])
    style["weight"] = _norm_num(style["weight"])
    return style


# --- レイアウト（折り返し・行頭禁則）---

def _is_wide(ch):
    """全角（どこでも折り返せる字）か"""
    return unicodedata.east_asian_width(ch) in ("W", "F")


def _split_units(segments):
    """1行ぶんの [(文字列, 書式番号)] を折り返しの単位へ分ける。

    単位: 空白 / 全角1字 / 欧文の語（空白を含まない半角の並び。書式をまたいでもよい）。
    戻り値: [{"kind": "space"|"wide"|"word", "segs": [(文字列, 書式番号), ...]}, ...]
    """
    units = []
    for text, sidx in segments:
        for ch in text:
            kind = "space" if ch.isspace() else ("wide" if _is_wide(ch) else "word")
            if kind == "word" and units and units[-1]["kind"] == "word":
                segs = units[-1]["segs"]
                if segs[-1][1] == sidx:
                    segs[-1] = (segs[-1][0] + ch, sidx)
                else:
                    segs.append((ch, sidx))
            else:
                units.append({"kind": kind, "segs": [(ch, sidx)]})
    return units


def _merge_runs(units):
    """単位の並びを、書式が同じ隣どうしをつないだ [(文字列, 書式番号)] にする"""
    runs = []
    for u in units:
        for text, sidx in u["segs"]:
            if runs and runs[-1][1] == sidx:
                runs[-1] = (runs[-1][0] + text, sidx)
            else:
                runs.append((text, sidx))
    return runs


def _is_kinsoku_head(unit):
    """行頭に置けない単位か。

    全角の字はその1字で、半角の並び（語）は**全部の字が**禁則字のときだけ該当する
    （"." / "!?" / ")" は該当、".env" / ".gitignore" / ".*" は語なので該当しない）。
    """
    if unit["kind"] == "space":
        return False
    return all(ch in _KINSOKU_HEAD for text, _sidx in unit["segs"] for ch in text)


def _wrap_units(units, max_width, measure):
    """単位の並びを max_width に収まるよう行へ分ける（貪欲法・行頭禁則は追い出し）。

    - 折り返しで生じた行の、行頭と行末の空白は捨てる（元の行頭の字下げは残す）
    - max_width より長い欧文の語は1字ずつに割って折り返す
    - 行頭禁則の字が行頭に来るときは、直前の単位ごと次の行へ送る。送るのは
      _KINSOKU_MAX_PULL 単位まで・行に実字を1つは残す。それで禁則字でない単位に
      届かないとき（禁則字の長い連続）は送らず、元の位置で折る
    - 送った単位どうしの間にあった空白は残す（捨てるのは折り目の空白だけ）
    """
    lines = []
    cur = []
    wrapped = False          # cur が折り返しで始まった行か
    queue = list(units)
    queue.reverse()          # 末尾から pop する
    while queue:
        u = queue.pop()
        solid = [x for x in cur if x["kind"] != "space"]
        if u["kind"] == "space":
            if cur or not wrapped:
                cur.append(u)   # 行末の空白ははみ出してよい（折り返すとき捨てる）
            continue
        if measure(_merge_runs(cur + [u])) <= max_width:
            cur.append(u)
            continue
        if not solid:
            # この単位だけで幅を超える: 欧文の語なら1字ずつに割る
            chars = [(ch, sidx) for text, sidx in u["segs"] for ch in text]
            if len(chars) > 1:
                for ch, sidx in reversed(chars):
                    queue.append({"kind": "wide", "segs": [(ch, sidx)]})
                continue
            cur.append(u)   # 1字でも超える → 呼び出し側が幅超過として報告する
            continue
        # 折り返す
        carry = [u]
        if _is_kinsoku_head(u):
            # 追い出し: 末尾から「禁則字でない単位」に届くまで次の行へ送る
            rest = list(cur)
            pulled = []
            ok = False
            while (len([x for x in pulled if x["kind"] != "space"]) < _KINSOKU_MAX_PULL
                   and sum(1 for x in rest if x["kind"] != "space") > 1):
                while rest[-1]["kind"] == "space":
                    pulled.insert(0, rest.pop())   # 送る単位の間の空白は残す
                x = rest.pop()
                pulled.insert(0, x)
                if not _is_kinsoku_head(x):
                    ok = True
                    break
            if ok:
                cur = rest
                carry = pulled + [u]
        while cur and cur[-1]["kind"] == "space":
            cur.pop()                               # 折り目の空白は捨てる
        lines.append(cur)
        cur = []
        wrapped = True
        for x in reversed(carry):
            queue.append(x)
    if wrapped:
        while cur and cur[-1]["kind"] == "space":
            cur.pop()
    lines.append(cur)
    return lines


# --- 本体: レイアウトと描画 ---

def _build_layout(fn, pil, spans, base_style, *, line_spacing, align, max_width,
                  border, shadow, shadow_blur, padding, canvas, missing):
    """区間の並びから、描画に必要な配置（行・字の位置・キャンバス寸法）を求める"""
    # 書式を解決し、同じ書式は1つにまとめる
    styles = []          # 解決済み書式 dict
    style_index = {}     # 書式の鍵 -> 番号
    logical_lines = [[]]  # 行ごとの [(文字列, 書式番号)]
    for i, (text, override) in enumerate(spans):
        style = _resolve_style(fn, f"content[{i}]", base_style, override)
        style["rgba"] = _rgba(fn, "color", style["color"], pil)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if text.replace("\n", ""):
            style["coords"] = _variation_coords(
                fn, pil, style["font"], style["font_index"], style["weight"])
            key = (style["font"], style["font_index"], style["size"], style["coords"],
                   style["rgba"])
            if key not in style_index:
                style_index[key] = len(styles)
                styles.append(style)
            sidx = style_index[key]
        else:
            # 字の無い区間（空文字・改行だけ）は書式を登録しない: 何も描かないのに
            # 書式の表が変わると、同じ絵でキャッシュ鍵だけが割れる
            sidx = None
        for j, part in enumerate(text.split("\n")):
            if j > 0:
                logical_lines.append([])
            if part:
                logical_lines[-1].append((part, sidx))
    if not any(logical_lines):
        raise ValueError(f"{fn}: content が空です。描く文字を指定してください")

    fonts = [_load_font(fn, pil, s["font"], s["font_index"], s["size"], s["coords"])
             for s in styles]
    base_font = _load_font(
        fn, pil, base_style["font"], base_style["font_index"], base_style["size"],
        _variation_coords(fn, pil, base_style["font"], base_style["font_index"],
                          base_style["weight"]))

    # 豆腐（フォントに無い字）の検出
    missing_found = []
    for sidx, style in enumerate(styles):
        chars = sorted({ch for line in logical_lines for text, si in line
                        if si == sidx for ch in text})
        lost = _missing_glyphs(fonts[sidx], chars, style["font"], style["font_index"])
        if lost:
            missing_found.append((os.path.basename(style["font"]), lost))
    if missing_found and missing != "ignore":
        detail = "、".join(
            f"{name} に " + " ".join(f"'{ch}'(U+{ord(ch):04X})" for ch in lost[:8])
            + (" ほか" if len(lost) > 8 else "")
            for name, lost in missing_found)
        msg = (f"{fn}: フォントに無い字があり、豆腐（□）で描かれます: {detail}。"
               f"その字を持つフォントを font= で指定するか、該当の区間だけ "
               f"(\"字\", {{'font': ...}}) で別のフォントにしてください")
        if missing == "error":
            raise ValueError(msg + "（豆腐のまま描くなら missing='warn' / 'ignore'）")
        warnings.warn(msg)

    def measure(runs):
        return sum(fonts[sidx].getlength(text) for text, sidx in runs)

    # 折り返し
    lines = []   # 各行の [(文字列, 書式番号)]
    for segments in logical_lines:
        if max_width is None or not segments:
            lines.append(segments)
            continue
        for units in _wrap_units(_split_units(segments), max_width, measure):
            lines.append(_merge_runs(units))
    widths = [measure(runs) for runs in lines]
    if max_width is not None and max(widths) > max_width + 0.5:
        worst = max(range(len(lines)), key=lambda k: widths[k])
        raise ValueError(
            f"{fn}: max_width={_fmt_num(max_width)}px に収まらない行があります"
            f"（{widths[worst]:.0f}px: '{''.join(t for t, _s in lines[worst])}'）。"
            f"1字の幅が max_width を超えています。size を下げるか max_width を広げてください")

    # 縦位置: 基本書式のフォントのメトリクスだけで決める（行に含まれる字や書体に
    # 依らない）。行の中に大きい字があれば、その比率でメトリクスと行送りを伸ばす。
    ascent, descent = base_font.getmetrics()
    base_size = base_style["size"]
    line_sizes = [max([styles[sidx]["size"] for _t, sidx in runs] or [base_size])
                  for runs in lines]
    baselines = []
    y = 0.0
    for k, size in enumerate(line_sizes):
        ratio = size / base_size
        if k == 0:
            y = ascent * ratio
        else:
            y += line_spacing * size
        baselines.append(y)
    content_h = baselines[-1] + descent * line_sizes[-1] / base_size
    content_w = max(widths)

    deco = border + max(abs(shadow[0]), abs(shadow[1])) + int(math.ceil(2 * shadow_blur))
    if padding is None:
        # canvas 固定のときは寸法を呼び出し側が決めているので、装飾ぶんだけ空ける
        pad_x = pad_y = deco + (0 if canvas is not None
                                else int(math.ceil(base_size * 0.15)))
    else:
        pad_x, pad_y = padding

    if canvas is None:
        box_w = content_w
        width = int(math.ceil(content_w)) + 2 * pad_x
        height = int(math.ceil(content_h)) + 2 * pad_y
        width += width % 2      # 偶数に丸める（yuv420p へ焼く経路・morph の入力で扱いやすい）
        height += height % 2
        top = pad_y
        left = pad_x
    else:
        width, height = canvas
        box_w = width - 2 * pad_x
        if content_w > box_w + 0.5 or content_h > height - 2 * pad_y + 0.5:
            raise ValueError(
                f"{fn}: 文字（{content_w:.0f}x{content_h:.0f}px）が canvas="
                f"{width}x{height}（padding {pad_x},{pad_y} を除いた内側）に収まりません。"
                f"canvas を広げるか size を下げてください")
        top = (height - content_h) / 2.0
        left = pad_x
    if width > _TEXT_IMAGE_MAX_PX or height > _TEXT_IMAGE_MAX_PX:
        raise ValueError(
            f"{fn}: 画像が大きすぎます（{width}x{height}px, 上限 {_TEXT_IMAGE_MAX_PX}px）。"
            f"size を下げるか、max_width で折り返すか、文字列を分けてください")

    # 字の位置（行頭の x は整数へ丸める。ベースラインも整数へ丸めて行ごとの滲みを揃える）
    placed = []   # (x, y, 文字列, 書式番号)
    ink = [float("inf"), float("inf"), float("-inf"), float("-inf")]
    for runs, line_w, baseline in zip(lines, widths, baselines):
        if align == "center":
            x = left + (box_w - line_w) / 2.0
        elif align == "right":
            x = left + (box_w - line_w)
        else:
            x = left
        x = float(round(x))
        yb = float(round(top + baseline))
        for text, sidx in runs:
            placed.append((x, yb, text, sidx))
            if text.strip():
                bx0, by0, bx1, by1 = fonts[sidx].getbbox(
                    text, anchor="ls", stroke_width=border)
                ink[0] = min(ink[0], x + bx0)
                ink[1] = min(ink[1], yb + by0)
                ink[2] = max(ink[2], x + bx1)
                ink[3] = max(ink[3], yb + by1)
            x += fonts[sidx].getlength(text)

    # 影のぶんを足して、キャンバスからはみ出す（＝端が切れる）なら知らせる
    spread = int(math.ceil(2 * shadow_blur))
    ink_all = (min(ink[0], ink[0] + shadow[0] - spread),
               min(ink[1], ink[1] + shadow[1] - spread),
               max(ink[2], ink[2] + shadow[0] + spread),
               max(ink[3], ink[3] + shadow[1] + spread))
    if any(v != v or abs(v) == float("inf") for v in ink_all):
        raise ValueError(f"{fn}: content が空白だけです。描く文字を指定してください")
    clipped = (ink_all[0] < -0.5 or ink_all[1] < -0.5
               or ink_all[2] > width + 0.5 or ink_all[3] > height + 0.5)
    if clipped:
        need = int(math.ceil(max(-ink_all[0], -ink_all[1], ink_all[2] - width,
                                 ink_all[3] - height)))
        warnings.warn(
            f"{fn}: 文字・縁取り・影がキャンバス（{width}x{height}px）から最大 {need}px "
            f"はみ出し、端が切れます。padding を {max(pad_x, pad_y) + need} 以上にしてください")

    plain = "\n".join("".join(t for t, _s in runs) for runs in lines)
    sizes = [styles[sidx]["size"] for _x, _y, text, sidx in placed if text.strip()]
    return {
        "styles": styles, "fonts": fonts, "placed": placed, "lines": lines,
        "width": width, "height": height,
        "content_width": content_w, "content_height": content_h,
        "padding": (pad_x, pad_y), "plain": plain,
        "size_min": min(sizes), "size_max": max(sizes),
        "missing": [ch for _name, lost in missing_found for ch in lost],
    }


def _tint(pil, mask, rgba):
    """L マスクを色つきの RGBA レイヤーにする（縁が暗くならないよう RGB は一様に塗る）"""
    Image = pil["Image"]
    layer = Image.new("RGBA", mask.size, (rgba[0], rgba[1], rgba[2], 0))
    if rgba[3] < 255:
        a = rgba[3]
        mask = mask.point(lambda v: v * a // 255)
    layer.putalpha(mask)
    return layer


def _render(pil, layout, *, border, border_rgba, shadow, shadow_rgba, shadow_blur,
            background_rgba, background_radius):
    """配置済みの文字を透過 RGBA 画像へ描く。

    色ごとに L マスクへ描いてから着色して重ねる。ImageDraw で透明な RGBA へ直接
    色つきの字を描くと、縁の画素の RGB が黒へ寄って暗い縁になる（合成後に黒ずむ）。
    重ね順は 下地 → 影 → 縁取り → 文字。縁取りは全区間を先に描くので、
    隣の区間の縁取りが文字の上に乗ることは無い。
    """
    Image = pil["Image"]
    ImageDraw = pil["ImageDraw"]
    size = (layout["width"], layout["height"])
    fonts = layout["fonts"]
    styles = layout["styles"]

    fill_masks = {}   # rgba -> L マスク
    for x, y, text, sidx in layout["placed"]:
        rgba = styles[sidx]["rgba"]
        mask = fill_masks.get(rgba)
        if mask is None:
            mask = fill_masks[rgba] = Image.new("L", size, 0)
        ImageDraw.Draw(mask).text((x, y), text, font=fonts[sidx], fill=255,
                                  anchor="ls")
    stroke_mask = None
    if border:
        stroke_mask = Image.new("L", size, 0)
        draw = ImageDraw.Draw(stroke_mask)
        for x, y, text, sidx in layout["placed"]:
            draw.text((x, y), text, font=fonts[sidx], fill=255, anchor="ls",
                      stroke_width=border, stroke_fill=255)

    img = Image.new("RGBA", size, (0, 0, 0, 0))
    if background_rgba is not None:
        # 角丸は4倍で描いて縮小する（PIL の rounded_rectangle はアンチエイリアス無し）
        ss = 4
        big = Image.new("L", (size[0] * ss, size[1] * ss), 0)
        ImageDraw.Draw(big).rounded_rectangle(
            [0, 0, size[0] * ss - 1, size[1] * ss - 1],
            radius=background_radius * ss, fill=255)
        plate = big.resize(size, Image.LANCZOS)
        img = Image.alpha_composite(img, _tint(pil, plate, background_rgba))

    if shadow != (0, 0) or shadow_blur:
        union = stroke_mask.copy() if stroke_mask is not None else Image.new("L", size, 0)
        for mask in fill_masks.values():
            union = pil["ImageChops"].lighter(union, mask)
        moved = Image.new("L", size, 0)
        moved.paste(union, (shadow[0], shadow[1]))
        if shadow_blur:
            moved = moved.filter(pil["ImageFilter"].GaussianBlur(shadow_blur))
        img = Image.alpha_composite(img, _tint(pil, moved, shadow_rgba))

    if stroke_mask is not None:
        img = Image.alpha_composite(img, _tint(pil, stroke_mask, border_rgba))
    for rgba, mask in fill_masks.items():
        img = Image.alpha_composite(img, _tint(pil, mask, rgba))
    return img


def _cached_png_ok(pil, path, size):
    """既存のキャッシュ PNG が使えるか（開けて・寸法が合い・壊れていない）。

    文字画像は内容から一意に書き直せるが、1ページ 0.1 秒前後かかり、レイヤーは
    1レンダで複数回 exec されるので、毎回の書き直しは避ける。その代わり
    「存在すれば命中」にはせず、PNG として検証できたものだけを命中にする
    （切り詰められた残骸は verify() が落とすので書き直される）。
    """
    if not os.path.isfile(path):
        return False
    try:
        with pil["Image"].open(path) as im:
            if im.format != "PNG" or im.size != size:
                return False
            im.verify()
    except Exception:
        return False
    return True


def _write_png_atomic(img, path):
    """PNG を原子的に書く（同じディレクトリの一時パス → os.replace）"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = _unique_tmp_path(path)
    try:
        img.save(tmp, format="PNG")
        os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)   # 失敗時の残骸掃除（成功時は replace 済みで存在しない）
        except OSError:
            pass


def _pair(fn, name, value, lo=None):
    """数値1つ、または (x, y) の2要素を (int, int) にする"""
    if isinstance(value, (tuple, list)):
        if len(value) != 2:
            raise ValueError(
                f"{fn}: {name} は数値か (x, y) の2要素で指定してください: {value!r}")
        a, b = value
    else:
        a = b = value
    _require_number(fn, name, a, lo, None)
    _require_number(fn, name, b, lo, None)
    return int(a), int(b)


# --- 公開 API ---

def text_image(content, *, size=64, font=None, font_index=0, weight=None,
               color="white", markup=False, styles=None,
               line_spacing=1.5, align="left", max_width=None,
               border=0, border_color="black",
               shadow=(0, 0), shadow_color="black@0.6", shadow_blur=0,
               background=None, background_radius=0,
               padding=None, canvas=None, missing="error", duration=None):
    """文字を PIL で透過 PNG に描き、画像 Object を返す。

    text() と違い実体が画像なので、morph_to / explode_to / assemble_from の入力にも
    target / source にも使え、scale / rotate_to / glow 等も画像として効く。
    配置は画像と同じ（.time(秒) と move(x=, y=, anchor=)）。

    content: 文字列、または区間のリスト。改行は "\\n"。
        区間のリスト: [("犯人は、", {}), ("正規表現が1本", {"color": "red", "weight": 900})]
            各要素は 文字列 / ("文字", 書式 dict) / ("文字", "styles の名前")。エスケープ不要。
            書式のキー: color / size / font / font_index / weight
            （区間が font を変えたとき weight と font_index は引き継がない）
        markup=True の文字列: "犯人は、{red|正規表現が1本}だった"
            {書式|文字}。書式はカンマ区切りで、キー=値 か 裸の語（styles の名前、
            無ければ色）。入れ子不可。エスケープは \\{ \\} \\\\ の3つだけ。
    size: 文字サイズ px（drawtext の fontsize と同じ em の大きさ）
    font: フォントファイルのパス（省略時は text() と同じ既定の探索）
    font_index: .ttc の中の書体番号（0 始まり）
    weight: 可変フォントの太さ。数値は wght 軸の値（例 700）、文字列は名前つき
        インスタンス（例 "Bold"）。可変フォントでなければ ValueError（黙って無視しない）
    color: 文字色（text() と同じ ffmpeg 形式: 色名 / 色名@alpha / #RRGGBB[AA]）
    styles: 名前つき書式の辞書（例 {"r": {"color": "red"}}）。区間とマークアップから名前で使う
    line_spacing: 行送り（ベースラインの間隔）÷ その行の文字サイズ
    align: 行ごとの揃え（'left' / 'center' / 'right'）
    max_width: 自動折り返しの幅 px（省略時は折り返さない）。全角はどこでも、欧文は
        語の切れ目で折り返す。行頭禁則は句読点と閉じ括弧の類だけ（追い出し）
    border / border_color: 縁取りの太さ px と色
    shadow / shadow_color / shadow_blur: 影のずらし (x, y) px・色・ぼかし半径 px
    background / background_radius: 下地の色（キャンバス全面）と角丸の半径 px
    padding: 文字のまわりの余白 px（数値か (横, 縦)）。省略時は
        縁取り + 影 + size の 15%（canvas 指定時は 縁取り + 影 だけ）
    canvas: キャンバスを (幅, 高さ) px に固定する（文字は縦中央・横は align）。
        morph_to の2枚を同じ寸法で作るとき用。省略時は文字に合わせる
    missing: フォントに無い字（豆腐）があったとき 'error'（既定・ValueError）/
        'warn'（警告して豆腐のまま描く）/ 'ignore'
    duration: 表示秒数（省略時は .time(秒) で指定する）

    行の縦位置は基本書式のフォントのメトリクス（ascent / descent）で決めるので、
    行に含まれる字や書体によって行の位置・画像の高さは変わらない。
    生成物は __cache__/artifacts/textimage/<鍵>.png（鍵は文字列・書式・フォントの
    内容指紋・Pillow の版）。p.audit() は文字サイズ・縁取りの有無・はみ出しを
    画面上の実寸（resize / scale の倍率込み）で検査する。
    """
    fn = "text_image"
    pil = _import_pil(fn)

    # --- 引数の検証 ---
    _require_number(fn, "size", size, 1, 2000)
    if isinstance(font_index, bool) or not isinstance(font_index, int) or font_index < 0:
        raise ValueError(f"{fn}: font_index は 0 以上の整数で指定してください: {font_index!r}")
    if weight is not None and not isinstance(weight, str):
        _require_number(fn, "weight", weight, 1, 2000)
    if not isinstance(markup, bool):
        raise TypeError(f"{fn}: markup は True / False で指定してください: {markup!r}")
    _require_number(fn, "line_spacing", line_spacing, 0.1, 20)
    if align not in _ALIGNS:
        raise ValueError(
            f"{fn}: align は {_ALIGNS} のいずれか: {align!r}{_suggest_hint(align, _ALIGNS)}")
    if max_width is not None:
        _require_number(fn, "max_width", max_width, 1, _TEXT_IMAGE_MAX_PX)
    _require_number(fn, "border", border, 0, 500)
    border = int(border)
    shadow = _pair(fn, "shadow", shadow)
    _require_number(fn, "shadow_blur", shadow_blur, 0, 500)
    _require_number(fn, "background_radius", background_radius, 0, None)
    if padding is not None:
        padding = _pair(fn, "padding", padding, 0)
    if canvas is not None:
        if isinstance(canvas, (int, float)):
            raise ValueError(f"{fn}: canvas は (幅, 高さ) で指定してください: {canvas!r}")
        canvas = _pair(fn, "canvas", canvas, 2)
    if missing not in _MISSING_MODES:
        raise ValueError(
            f"{fn}: missing は {_MISSING_MODES} のいずれか: {missing!r}"
            f"{_suggest_hint(missing, _MISSING_MODES)}")
    if duration is not None:
        _require_number(fn, "duration", duration, 0.01, None)
    if styles is not None and not isinstance(styles, dict):
        raise TypeError(
            f"{fn}: styles は 名前 → 書式 の dict で指定してください"
            f"（例: {{'r': {{'color': 'red'}}}}）: {styles!r}")
    styles = dict(styles or {})
    for name, st in styles.items():
        if not isinstance(name, str) or not name:
            raise TypeError(f"{fn}: styles のキーは文字列で指定してください: {name!r}")
        styles[name] = _check_style(fn, f"styles['{name}']", st)

    border_rgba = _rgba(fn, "border_color", border_color, pil)
    shadow_rgba = _rgba(fn, "shadow_color", shadow_color, pil)
    background_rgba = (None if background is None
                       else _rgba(fn, "background", background, pil))
    size = _norm_num(size)
    shadow_blur = _norm_num(shadow_blur)
    background_radius = _norm_num(background_radius)
    base_style = {"color": color, "size": size, "font": _resolve_font(font),
                  "font_index": font_index, "weight": _norm_num(weight)}
    _rgba(fn, "color", color, pil)

    spans = _normalize_spans(fn, content, markup, styles)
    layout = _build_layout(
        fn, pil, spans, base_style, line_spacing=line_spacing, align=align,
        max_width=max_width, border=border, shadow=shadow, shadow_blur=shadow_blur,
        padding=padding, canvas=canvas, missing=missing)

    # --- キャッシュ鍵（描画結果を一意に決めるものだけ。生パスは混ぜない）---
    key_spec = {
        "ver": _TEXT_IMAGE_VER,
        "pil": pil["PIL"].__version__,
        "styles": [{"font": _file_fingerprint(s["font"]), "index": s["font_index"],
                    "size": s["size"],
                    "var": None if s["coords"] is None else list(s["coords"]),
                    "rgba": list(s["rgba"])}
                   for s in layout["styles"]],
        # 折り返し後の配置そのもの（max_width / align / padding / canvas / line_spacing の
        # 効果はここに全部現れる。効かない引数で鍵を割らない）
        "placed": [[x, y, text, sidx] for x, y, text, sidx in layout["placed"]],
        "size": [layout["width"], layout["height"]],
        "border": [border, list(border_rgba)] if border else None,
        "shadow": ([list(shadow), list(shadow_rgba), shadow_blur]
                   if (shadow != (0, 0) or shadow_blur) else None),
        "background": ([list(background_rgba), background_radius]
                       if background_rgba is not None else None),
    }
    key = hashlib.sha256(json.dumps(
        key_spec, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    path = os.path.join(_TEXT_IMAGE_DIR, f"{key}.png")

    size_px = (layout["width"], layout["height"])
    if not (key in _WRITTEN_MEMO and os.path.isfile(path)):
        if not _cached_png_ok(pil, path, size_px):
            img = _render(
                pil, layout, border=border, border_rgba=border_rgba, shadow=shadow,
                shadow_rgba=shadow_rgba, shadow_blur=shadow_blur,
                background_rgba=background_rgba, background_radius=background_radius)
            _write_png_atomic(img, path)
        _WRITTEN_MEMO.add(key)

    obj = Object(path)
    # p.audit() への申告（画像の中の文字の大きさ・装飾・寸法）
    obj._text_image = {
        "content": layout["plain"],
        "size": size,
        "size_min": layout["size_min"],
        "size_max": layout["size_max"],
        "border": border,
        "shadow": shadow,
        "shadow_blur": shadow_blur,
        "background": background_rgba is not None,
        "font": base_style["font"],
        "width": layout["width"],
        "height": layout["height"],
        "content_width": layout["content_width"],
        "content_height": layout["content_height"],
        "padding": layout["padding"],
        "lines": len(layout["lines"]),
        "missing": list(layout["missing"]),
    }
    if duration is not None:
        obj.time(float(duration))
    return obj
