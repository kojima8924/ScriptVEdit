# 同梱データの出典と利用条件（src/scriptvedit/data/）

scriptvedit の同梱の素材・データ（`assets/` とこの `data/`）は全部自作（[ASSETS.md](../../../ASSETS.md)）だが、
このフォルダの陸地のマスクだけは**第三者のデータ（パブリックドメイン）**で、その原則の唯一の例外。
（素材ではなくライブラリとして同梱している `formula()` 用の KaTeX（`templates/vendor/katex/`。MIT）は
この原則の外で、同じディレクトリの README にあるライセンスに従う。）

## ne_110m_land_1440.png

`globe()` の陸地の既定（`land=True`）。正距円筒（経度 -180〜180 → x、緯度 90〜-90 → y）の
1440×720・1bit の白黒マスク（白が陸）。11,867 バイト。

- 出典: Natural Earth — Land（1:110m physical vectors, `ne_110m_land`, v4.1.0）
  <https://www.naturalearthdata.com/downloads/110m-physical-vectors/110m-land/>
- 取得元（Natural Earth 公式の配布 CDN）: <https://naciscdn.org/naturalearth/110m/physical/ne_110m_land.zip>
- 元データ（zip）の SHA-256: `1926c621afd6ac67c3f36639bb1236134a48d82226dc675d3e3df53d02d2a3de`
- 利用条件: パブリックドメイン（<https://www.naturalearthdata.com/about/terms-of-use/>）

  > All versions of Natural Earth raster + vector map data found on this website are in the
  > public domain. You may use the maps in any manner, including modifying the content and
  > design, electronic dissemination, and offset printing.

- クレジット（任意。推奨の表記）: **Made with Natural Earth.**
- 加工: `scripts/make_land_mask.py` が .shp の多角形（外側の環を塗り、穴を抜く）を
  1440×720 に塗って 1bit にした。手で描き足した所は無い。
- 再現の確かめ方: `python scripts/make_land_mask.py --zip ne_110m_land.zip --check`
  （同じ手順で作ったマスクと、この PNG の画素・tEXt を比べる）
- この PNG 自身の SHA-256: `6a8417322b91503cd396618aa741211eb9a120124bc5a7a46ba7384dc997aa61`
- 出典・利用条件・元データの SHA-256 は PNG の tEXt（`Source` / `License` / `SourceSHA256`）にも
  書いてある（ファイルだけを持ち出しても分かるように）。

### 同梱した理由

自作で大陸の形を描くと、不正確な地図が実データに見えてしまう。同梱しないと利用者ごとに
`make_land_mask.py` を走らせる（ネットに出る）手間がかかり、`globe()` の既定も
「陸の無い地球」になっていた。パブリックドメインなので、MIT のコードと一緒に配っても
条件はぶつからない（ユーザーの承認のうえで同梱した。2026-10-03）。

動画に地図を出すときは、字幕の出典の欄などに「地図: Natural Earth」と添えることを勧める
（義務ではない）。
