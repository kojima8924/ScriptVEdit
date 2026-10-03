# -*- coding: utf-8 -*-
"""Natural Earth の陸地（1:110m）から、globe() の陸の白黒マスク PNG を作る。

本体に同梱している src/scriptvedit/data/ne_110m_land_1440.png（globe() の land の既定
＝「同梱の地球」）は、このスクリプトで作ったもの。作り直すときも、別の解像度のマスクを
自分のプロジェクトへ作るときも、これを使う。

出典: Natural Earth — Land（1:110m physical vectors, ne_110m_land, v4.1.0）
  https://www.naturalearthdata.com/downloads/110m-physical-vectors/110m-land/
  取得先（Natural Earth 公式の配布 CDN）: https://naciscdn.org/naturalearth/110m/physical/ne_110m_land.zip
利用条件: パブリックドメイン。
  "All versions of Natural Earth raster + vector map data found on this website are in
  the public domain. You may use the maps in any manner, including modifying the content
  and design, electronic dissemination, and offset printing."
  https://www.naturalearthdata.com/about/terms-of-use/
  （クレジットの表記は任意だが、推奨は "Made with Natural Earth."）

同梱の理由: scriptvedit の同梱の素材・データ（assets/ と data/）は「全部自作・第三者素材
ゼロ」で運用しているが、陸地のデータだけはこの原則の例外にした（ユーザーの承認済み・
2026-10-03。ライブラリとして同梱している formula() 用の KaTeX は素材ではなく別扱い）。自作で
大陸の形を描くと不正確な地図が実データに見えてしまい、利用者ごとに make_land_mask.py を
走らせる（ネットに出る）手間も globe() の既定を「陸の無い地球」にしていた。パブリック
ドメインなので MIT のコードと一緒に配っても条件はぶつからない。出典と利用条件は
同じフォルダの NOTICE.md と PNG の tEXt に書く。

    python scripts/make_land_mask.py                                   # 同梱の PNG を作り直す（ネットに出る）
    python scripts/make_land_mask.py --zip ne_110m_land.zip --check    # 同梱の PNG が再現できるか確かめる
    python scripts/make_land_mask.py <プロジェクト>/assets/geo/land_2880.png --width 2880   # 細かいマスク

出力: 正距円筒（経度 -180〜180 → x、緯度 90〜-90 → y）の 1bit の PNG（白が陸。既定 1440×720・
約 12KB）。PNG の tEXt に出典・利用条件・元データ（zip）の SHA-256 を書く。

- 同梱の PNG（出力先を省いたとき）へ書けるのは 1440 幅だけ。--width を変えるときは出力先を
  書く（省くと止まる。同梱の PNG を別の寸法で上書きすると、data/NOTICE.md の SHA-256 と
  テストが合わなくなり、globe() の既定の見た目も変わるため）。

- ダウンロードは1回だけ（zip を SHA-256 で照合し、合わなければ止める。中身が差し替わった
  データで黙ってマスクを作らない）。CI では走らない（ネットに出るため）。
- .shp は標準ライブラリ（struct）だけで読む。塗りは Pillow。
- 書き出しは原子的（同じディレクトリの一時ファイル → os.replace）。
- --check は書き出さず、作ったマスクと出力先の PNG の画素と tEXt を比べる（違えば終了コード 1）。
"""

import argparse
import hashlib
import io
import os
import struct
import sys
import uuid
import zipfile

URL = "https://naciscdn.org/naturalearth/110m/physical/ne_110m_land.zip"
SHA256 = "1926c621afd6ac67c3f36639bb1236134a48d82226dc675d3e3df53d02d2a3de"
SHP_NAME = "ne_110m_land.shp"
SOURCE = ("Natural Earth - Land 1:110m (ne_110m_land v4.1.0), "
          "https://www.naturalearthdata.com/downloads/110m-physical-vectors/110m-land/")
LICENSE = ("Public domain (Natural Earth terms of use: "
           "https://www.naturalearthdata.com/about/terms-of-use/). Made with Natural Earth.")
WIDTH, HEIGHT = 1440, 720
# 本体に同梱するマスク（globe() の land の既定。fx_globe._EARTH_LAND と同じ場所）
BUNDLED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "src", "scriptvedit", "data", "ne_110m_land_1440.png")

# .shp の形の種類（多角形だけを読む）
_SHP_NULL = 0
_SHP_POLYGON = 5


def download(url=URL, sha256=SHA256, timeout=60):
    """zip を取得して SHA-256 を照合し、中身（bytes）を返す"""
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = r.read()
    check_sha256(data, sha256)
    return data


def check_sha256(data, sha256=SHA256):
    got = hashlib.sha256(data).hexdigest()
    if got != sha256:
        raise ValueError(
            f"make_land_mask: zip の SHA-256 が合いません（期待 {sha256}、実際 {got}）。"
            f"配布元のデータが変わった可能性があります。中身を確かめてから SHA256 を更新してください")


def shp_from_zip(data, name=SHP_NAME):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read(name)


def read_shp_polygons(data):
    """.shp（多角形）の中身 → [[環（[(経度, 緯度), …]）, …], …]（レコードごと）。

    ESRI Shapefile Technical Description（1998）の形式: 100 バイトのヘッダ（ファイル長は
    16bit 語の数・ビッグエンディアン）、続いてレコード（番号と長さはビッグエンディアン、
    中身はリトルエンディアン）。多角形は境界の箱・部分の数・点の数・部分の開始位置・点の列。
    """
    if len(data) < 100:
        raise ValueError("make_land_mask: .shp が短すぎます")
    code, = struct.unpack(">i", data[0:4])
    if code != 9994:
        raise ValueError(f"make_land_mask: .shp ではありません（ファイルコード {code}）")
    file_len = struct.unpack(">i", data[24:28])[0] * 2
    shape_type, = struct.unpack("<i", data[32:36])
    if shape_type != _SHP_POLYGON:
        raise ValueError(f"make_land_mask: 多角形の .shp ではありません（形の種類 {shape_type}）")
    end = min(file_len, len(data))
    pos = 100
    records = []
    while pos + 8 <= end:
        _num, clen = struct.unpack(">ii", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + clen * 2]
        pos += 8 + clen * 2
        if len(body) < 4:
            raise ValueError("make_land_mask: レコードが途中で切れています")
        st, = struct.unpack("<i", body[0:4])
        if st == _SHP_NULL:
            continue
        if st != _SHP_POLYGON:
            raise ValueError(f"make_land_mask: 多角形でないレコードがあります（形の種類 {st}）")
        n_parts, n_points = struct.unpack("<ii", body[36:44])
        parts = list(struct.unpack(f"<{n_parts}i", body[44:44 + 4 * n_parts]))
        off = 44 + 4 * n_parts
        coords = struct.unpack(f"<{2 * n_points}d", body[off:off + 16 * n_points])
        pts = list(zip(coords[0::2], coords[1::2]))
        rings = []
        for k, start in enumerate(parts):
            stop = parts[k + 1] if k + 1 < n_parts else n_points
            rings.append(pts[start:stop])
        records.append(rings)
    return records


def ring_area(ring):
    """環の符号つき面積（経度・緯度の平面で。正 = 反時計回り）"""
    a = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        a += x0 * y1 - x1 * y0
    return a / 2.0


def rasterize(records, width=WIDTH, height=HEIGHT):
    """多角形のレコードを正距円筒の 1bit 画像（Pillow の "1"。白が陸）に塗る。

    Shapefile の約束: 外側の環は時計回り、穴は反時計回り。レコードごとに外側を白で
    塗ってから穴を黒で抜く。
    """
    from PIL import Image, ImageDraw

    img = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(img)

    def px(ring):
        return [((lon + 180.0) / 360.0 * width, (90.0 - lat) / 180.0 * height)
                for lon, lat in ring]

    for rings in records:
        outer = [r for r in rings if len(r) >= 3 and ring_area(r) <= 0]
        holes = [r for r in rings if len(r) >= 3 and ring_area(r) > 0]
        for r in outer:
            draw.polygon(px(r), fill=255)
        for r in holes:
            draw.polygon(px(r), fill=0)
    return img.point(lambda v: 255 if v >= 128 else 0).convert("1", dither=Image.Dither.NONE)


def png_text(sha256=SHA256):
    """PNG の tEXt に書く出典・利用条件・元データの SHA-256（--check もこれと比べる）"""
    return {
        "Title": "Land mask (equirectangular, white = land)",
        "Source": SOURCE,
        "License": LICENSE,
        "SourceSHA256": sha256,
        "Software": "scriptvedit scripts/make_land_mask.py",
    }


def write_png(img, path, sha256=SHA256):
    """1bit の PNG を、出典と利用条件の tEXt つきで原子的に書く"""
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    for k, v in png_text(sha256).items():
        info.add_text(k, v)
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".{os.path.basename(path)}.{os.getpid()}.{uuid.uuid4().hex}.png")
    try:
        img.save(tmp, format="PNG", pnginfo=info, optimize=True)
        os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def check_png(img, path, sha256=SHA256):
    """作ったマスク img と、path の PNG の画素・寸法・tEXt が同じなら None、違えば理由の文字列"""
    from PIL import Image

    if not os.path.isfile(path):
        return f"{path} がありません"
    with Image.open(path) as old:
        old.load()
        if old.size != img.size:
            return f"寸法が違います（{old.size[0]}x{old.size[1]} と {img.width}x{img.height}）"
        if old.mode != "1":
            return f"1bit の PNG ではありません（モード {old.mode}）"
        text = {k: v for k, v in getattr(old, "text", {}).items()}
        if text != png_text(sha256):
            return f"tEXt が違います: {text}"
        if old.tobytes() != img.tobytes():
            diff = sum(1 for a, b in zip(old.getdata(), img.getdata()) if bool(a) != bool(b))
            return f"画素が {diff} 個違います"
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out", nargs="?", default=BUNDLED,
                    help="出力する PNG のパス（省略時は本体に同梱する "
                         "src/scriptvedit/data/ne_110m_land_1440.png）")
    ap.add_argument("--zip", help="手元の ne_110m_land.zip（省略時はダウンロードする）")
    ap.add_argument("--width", type=int, default=WIDTH,
                    help="幅 px（高さは半分。既定 1440。1440 以外は出力先の指定が要る）")
    ap.add_argument("--check", action="store_true",
                    help="書き出さずに、out の PNG が同じ手順で再現できるか確かめる（違えば終了コード 1）")
    args = ap.parse_args(argv)
    if args.width < 16 or args.width % 2:
        ap.error("--width は 16 以上の偶数にしてください")
    if (not args.check and args.width != WIDTH
            and os.path.normcase(os.path.abspath(args.out)) == os.path.normcase(BUNDLED)):
        # 同梱の PNG を別の寸法で上書きしない（ダウンロードの前に止める）
        ap.error(f"--width {args.width} のマスクは同梱の PNG（{WIDTH} 幅）へは書けません。"
                 f"出力先を書いてください（例: python scripts/make_land_mask.py "
                 f"assets/geo/land_{args.width}.png --width {args.width}）")
    if args.zip:
        with open(args.zip, "rb") as f:
            data = f.read()
        check_sha256(data)
    else:
        print(f"取得: {URL}", file=sys.stderr)
        data = download()
    records = read_shp_polygons(shp_from_zip(data))
    img = rasterize(records, args.width, args.width // 2)
    if args.check:
        why = check_png(img, args.out)
        if why is not None:
            print(f"再現できません: {args.out}: {why}", file=sys.stderr)
            sys.exit(1)
        print(f"再現できました: {args.out}", file=sys.stderr)
        return
    write_png(img, args.out)
    land = sum(1 for v in img.getdata() if v)
    print(f"書き出し: {args.out}（{args.width}x{args.width // 2}、陸 {100.0 * land / (img.width * img.height):.1f}%"
          f"、{len(records)} 個の多角形、{os.path.getsize(args.out)} バイト）", file=sys.stderr)


if __name__ == "__main__":
    main()
