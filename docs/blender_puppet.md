# Blender 切り絵アニメーション

`puppet_clip()` は JSON のリグとモーションから、アルファ付きの動画 `Object` を作る。
Blender の GUI や MCP は不要。Blender を1素材につき1プロセスで起動し、全フレームを
CPU Cycles で描画した後、FFmpeg 8 の可逆 `qtrle argb MOV` にまとめる。

## 実装済みの範囲

- PNG 等の画像パーツ、単色の矩形・楕円、画像を持たない親グループ。
- 親子の位置・回転・拡大縮小、回転中心、重なり順、透明度の継承。
- 秒で書くキーフレーム。位置、回転、拡大縮小、透明度の linear / smooth / step 補間。
- 透過 RGBA、バッチ生成、内容キャッシュ、dry_run、最終フレームの HOLD。
- 戻り値は通常の `Object`。既存の `move` / `fade` / `resize` やレイヤー合成を利用できる。

メッシュの局所変形、ボーンウェイト、IK、物理揺れ、自動パーツ分離、音声からの口形生成は
まだ実装していない。1枚の立ち絵から隠れた部分が自動で描き足される機能もない。
専用の頭・腕・目・口の素材を用意すると、独立した仕草や瞬きが作れる。

## 必要なものと最短の実行

通常の Python に `bpy` はインストールしない。Blender 実行ファイルと FFmpeg 8 / ffprobe を
PATH に置く。Blender 4.3.2 の Linux ヘッドレス / CPU Cycles で実レンダを検証した。

```bash
pip install -e .
python -m scriptvedit describe --name puppet_clip --format md
python examples/blender_puppet/main.py --dry-run
python examples/blender_puppet/main.py
```

Windows 等で Blender が PATH に無いときは
`blender=r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe"` を指定する。
Blender がない機械でも、他の ScriptVEdit 機能と `dry_run` は使用できる。
既に生成された MOV の合成だけなら、`Object("actor.mov")` を使えば Blender は要らない。

**main.py** はプロジェクト設定とレイヤーを定義する。

```python
from scriptvedit import Project

p = Project()
p.configure(width=1280, height=720, fps=24, background_color="#e7e3db")
p.layer("actor.py")
p.render("output.mp4")
```

**actor.py** 内で素材を作る。`Object` を main.py で作って手動登録しない。

```python
from scriptvedit import puppet_clip, move

actor = puppet_clip("assets/actor/rig.json", "assets/actor/bow.json", fps=24)
actor.time(4) <= move(x=.5, y=.55)
```

## rig.json

```json
{
  "schema": 1,
  "canvas": [768, 768],
  "nodes": [
    {"id": "root", "position": [384, 500]},
    {"id": "body", "parent": "root", "size": [400, 500], "image": "parts/body.png"},
    {"id": "head", "parent": "root", "position": [0, -190],
     "size": [260, 290], "pivot": [0.5, 0.8], "image": "parts/head.png", "z": 1},
    {"id": "eyes_open", "parent": "head", "position": [0, -140],
     "size": [130, 40], "image": "parts/eyes_open.png", "z": 1},
    {"id": "eyes_closed", "parent": "head", "position": [0, -140],
     "size": [130, 40], "image": "parts/eyes_closed.png", "opacity": 0, "z": 1}
  ]
}
```

| フィールド | 既定値 | 意味 |
| --- | --- | --- |
| `canvas` | `[1280,720]` | リグの座標系。出力の `size` が違う場合もこの座標で配置する |
| `id` | 必須 | 英数字・`_`・`-` の識別子。先頭は英字か `_` |
| `parent` | `null` | 親の `id`。順番は自由。存在しない親・循環は生成前にエラー |
| `position` | `[0,0]` | 自分の pivot の位置。根は canvas 左上基準、子は親の pivot 基準。右・下が正 |
| `pivot` | `[0.5,0.5]` | 絵の中の回転中心。左上 `[0,0]`、右下 `[1,1]` |
| `size` | `[100,100]` | 絵の配置寸法。元画像のアスペクトを保つ値を指定する |
| `image` | なし | 相対パスは rig JSON の親ディレクトリ基準。dict 入力なら現在のディレクトリ基準 |
| `color` | なし | `#RRGGBB` / `#RRGGBBAA`。image と同時指定不可 |
| `shape` | `rect` | 単色ノードは `rect` / `ellipse`。画像は矩形のみ |
| `rotation` | `0` | 時計回りの角度。単位は度 |
| `scale` | `[1,1]` | 2軸の倍率。負の値で反転、0で潰す |
| `opacity` | `1` | 0〜1。親の opacity と画像自身の alpha を乗算する |
| `z` | `0` | 親に対する奥行き。大きいほど手前。同じ深さなら後のノードが手前 |

`image` と `color` が無いノードは、子をまとめるグループになる。
親の位置・回転・拡大縮小と透明度は子孫にも反映される。
各パーツの z は親から加算されるため、別の枝と重ねる場合は合計の深さを意識する。

canvas と出力の比率が違う場合は、canvas 全体が収まるように描画し、余白を透明にする。
出力 `size` の各辺は 4〜65536 px。Blender が黙って解像度を丸める範囲は事前に拒否する。

## motion.json

```json
{
  "schema": 1,
  "duration": 2.0,
  "tracks": [
    {"node": "head", "property": "rotation", "interpolation": "smooth",
     "keys": [[0, 0], [0.5, 8], [1.2, 8], [1.8, 0]]},
    {"node": "eyes_open", "property": "opacity", "interpolation": "step",
     "keys": [[0, 1], [0.8, 0], [0.92, 1]]},
    {"node": "eyes_closed", "property": "opacity", "interpolation": "step",
     "keys": [[0, 0], [0.8, 1], [0.92, 0]]}
  ]
}
```

`property` は `position` / `rotation` / `scale` / `opacity`。
position と scale は2要素配列、rotation と opacity は単一の数値で指定する。
値は初期値に足す差分ではなく、ノードのプロパティを置き換える絶対値。
同じノードの同じプロパティに複数の track を置くことはできない。

補間の既定は `smooth`（`u*u*(3-2*u)`）。`linear` は一定速度、`step` は瞬時切り替え。
キーは0秒以上の重複しない昇順。最初のキーより前と最後のキーより後は、端の値を保つ。
1コマ目は0秒、最終コマは `(N-1)/fps` 秒。尺は最も近いコマ数に丸め、半分は切り上げる。
最後のポーズを確実に映す場合、終点のキーを `duration-1/fps` 以前に置く。

## キャッシュと失敗時の扱い

出力は `__cache__/artifacts/blender/<key>.mov`。
鍵は正規化したリグ・動き、画像の内容、Blender実行ファイルの内容、レンダラ実装の内容、
解像度・fps・コマ数・samples・seed を含む。JSONの書式、ファイルの置き場所、timeout は含まない。
rig JSON、motion JSON、画像、実装ファイルもレイヤー依存へ登録される。

実行ファイルの指紋は Blender を起動せずに取得する。独自のラッパー実行ファイルや
共有ライブラリ・OCIO設定を入れ替える運用では、`renderer_id="配布版と設定の識別子"` も指定する。
別の Blender バイナリでは鍵が変わる。GPUレンダは現行APIでは選べず、CPUで固定する。

生成は一意の一時ディレクトリで行い、全フレームの存在と符号化後のフレーム数を確認してから
同じボリューム上で原子的に確定する。失敗・タイムアウトで部分MOVを完成キャッシュにしない。
Blenderの失敗は出力ログの末尾付き例外になる。

`dry_run` は実行ファイルを起動せず、JSON・画像の検査とハッシュ、予定コマンドの生成だけを行う。
BlenderとFFmpegの2段が計画へ載り、コールド・ウォームで同じ計画になる。
Blender用job JSONやPNG連番は実レンダ時だけ一時的に作る。

## ゲーム・BB素材での使い方

ゲームでは、キャラ単体の透過MOVを横長背景と合成し、配信用OGV等へ書き出す。
Blender / bpy をゲームの実行時やAPKへ組み込む必要はない。
文字送り・選択肢・停止位置の制御はゲーム側が担当する。

BB/GB素材は同じ透過MOVを編集マスターにし、必要な納品時に青・緑背景へ重ねる。
表情やポーズは別ノードのopacity切り替えで明示的に管理できる。
瞬きや首の回転が自然に見えるかは、実際の素材の重なり・ピボット・隠れた描画範囲の確認が必要。

## 検証

```bash
pytest tests/test_blender_puppet.py
pytest tests/test_blender_puppet.py --realrender-all
```

通常のテストはBlenderを起動しない。実レンダのテストは `--realrender-all` で有効化し、
Blenderのない環境では該当項目だけskipする。親子回転の位置、半透明、透明な外側、色、
コマ数、再利用時の無描画をピクセルとファイルで検証する。

## ライセンスの範囲

既存コア、JSONの検証、外部CLIを呼ぶ `src/scriptvedit/blender.py` は MIT。
Blender 内で bpy を使う `src/scriptvedit/blender_runtime.py` のみ GPL-3.0-or-later とし、
ファイル先頭に SPDX 識別子を付けている。GPL v3の全文は
`src/scriptvedit/data/licenses/GPL-3.0.txt` に同梱し、wheelにも含める。
パッケージメタデータもこの範囲を明示する。

これは、外部からCLIを呼ぶ独立プログラムとBlender Python APIを使う配布スクリプトを
区別する [Blender公式FAQ](https://www.blender.org/support/faq/) の説明に沿った構成。
ワーカーはソースのまま渡して実行し、Blender自体は同梱しない。
リグ・素材の利用条件は素材側のものを引き継ぐ。
