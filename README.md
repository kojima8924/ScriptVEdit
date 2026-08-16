# ScriptVEdit

**動画のタイムラインを Python コードで記述し、FFmpeg コマンドへ変換して映像を生成する DSL。**

素材の配置・変形・エフェクト・音声をすべて Python スクリプトとして書く。GUI の編集操作を経ずに
コードだけで動画を組み立てられるため、人間が書くだけでなく**コーディングAIに動画を作らせる**用途にも使える
（本プロジェクトの解説動画自体も ScriptVEdit で制作している）。

- **演算子で書く DSL** — `<=` で適用、`|` / `&` で連結。`obj[2:5]`（素材の切り出し）/ `obj @ 12`（タイムラインへ絶対配置）/
  `a >> b`（直後に連結）/ `clip * 3`（リピート）/ `-clip`（逆再生）といった糖衣を持つ（→「リファレンス」の「演算子によるDSL」「タイムライン演算子」節）
- **素材キャッシュ** — 中間結果を**内容ハッシュ**の鍵で自動保存・復元し、変えていない部分は再レンダしない（→「チェックポイントキャッシュ」節）
- **アンカーによる同期** — 「あの表示が終わってから」をレイヤーをまたいだ名前で参照でき、尺の変更が後続へ自動で波及する（→「anchor / pause / until」節）
- **時間分割並列レンダ** — 総尺をフレーム境界で N 分割し、別プロセスで並列レンダして無劣化 concat。
  実測（2分56秒・87オブジェクトの実プロジェクトを20コアPCで計測）で**逐次 1012 秒 → 並列8 で 106 秒**（→「時間分割並列レンダ」節）
- **AI が読める機能マニフェスト** — `python -m scriptvedit describe` で全機能のシグネチャ・引数レンジを JSON / Markdown 出力（→「ケイパビリティ・マニフェスト」節）

素材は画像・動画・音声のほか、HTML（Playwright 経由）・LaTeX 数式（KaTeX 同梱・オフライン）・TTS 音声も
同じ Object として扱える。出力は mp4 / gif / webp / 連番PNG / 透過webm。

## 必要なもの

| 必須 | 説明 |
|---|---|
| Python 3.10 以上 | 本体の実行 |
| **FFmpeg 8 以上** | 映像の生成。`ffmpeg` / `ffprobe` が PATH にあること |

FFmpeg は**バージョンが重要**。長大フィルタの `-/filter_complex` 構文など
FFmpeg 8 の機能を使うため、初回実行時にメジャーバージョンを検証し、7 以下は
エラーになる。手元のバージョンは `ffmpeg -version` の1行目で確認できる。

以下は**その機能を使うときだけ**必要になる（`pip install -e .[all]` で一括導入可）:

- Playwright + Chromium（テンプレート / web Object / slide / `formula` 使用時。KaTeX は同梱のためネットワーク不要）
- numpy + scipy（`beat_sync` 使用時。scipy はビート検出に必須）
- numpy + PIL（`scriptvedit.testkit` の SSIM検証。scipy は任意で高速化）
- Pillow（`storyboard` 使用時）
- TTS（`voice` / `narrate` 使用時。`scriptvedit.tts` 経由。いずれか1つ）
  - VOICEVOX エンジン（`backend="voicevox"`。オフライン・キャラボイス。別途起動が必要）
  - edge-tts（`backend="edge"`。`pip install edge-tts` または `pip install scriptvedit[tts]`。導入が楽だがオンライン必須）
  - Windows 標準音声（`backend="sapi"`。追加導入不要・オフライン。Windows 専用）

## インストール

```
git clone https://github.com/kojima8924/ScriptVEdit.git && cd ScriptVEdit
pip install -e .            # コアは標準ライブラリのみ
pip install -e .[all]       # morph / web / beat / tts(edge-tts) / tools の全機能
```

`pip install -e .` 後はどのディレクトリからでも `from scriptvedit import *` で使える。

動画を作る側(人・コーディングAI)向けの実践ノウハウは
**[docs/production_guide.md](docs/production_guide.md)**(制作ワークフロー・品質規則・
レンダ運用)にまとまっている。この README はライブラリ自体の入門とリファレンス。

## クイックスタート — はじめての動画

### 1. 雛形を生成してレンダする

```
scriptvedit new myvideo
cd myvideo
python main.py           # → output/myvideo.mp4 ができる
```

これだけで動画が1本できる。雛形の構造:

```
myvideo/
├── main.py            構成定義（configure / layer / render）
├── layers/intro.py    サンプルレイヤー（1ファイル = 1レイヤー）
├── assets/            素材置き場（images/ audio/）
├── plugins/           カスタムエフェクト置き場（@effect_plugin、自動読込）
├── output/            出力
├── README.md          レンダ方法・素材の置き方
└── .gitignore         output/ __cache__/ assets/_imported/ を除外
```

- `scriptvedit new myvideo --template explainer` … 解説動画向けの雛形（数式・字幕・BGM 入り）
- `scriptvedit new myvideo --force` … 生成先が空でなくても生成する

### 2. 何が書いてあるのか

**main.py** は「動画の設定と、レイヤーをどの順で重ねるか」だけを書く:

```python
from scriptvedit import *

p = Project()
p.configure(width=1280, height=720, fps=30, background_color="black")

p.layer("bg.py", priority=0)      # 数字が小さいほど下に重なる
p.layer("badge.py", priority=1)   # こちらが上

p.render("output.mp4")
```

**レイヤーファイル**（例: bg.py）には「素材をどう表示するか」を書く:

```python
from scriptvedit import *

bg = Object("bg_pattern_tiles.jpg")   # 素材（画像・動画・音声など）を1つ包む
bg <= resize(sx=1, sy=1)              # <= は「左の素材に右の効果を適用」
bg.time(6) <= move(x=0.5, y=0.5, anchor="center") \
              & scale(lambda u: lerp(1.5, 1, u)) \
              & fade(lambda u: u)
```

初めて見る記号の意味:

- `Object(...)` … 素材1つ。レイヤー内で作るだけで自動的に登録される（リストに追加する操作は不要）
- `bg.time(6)` … この素材を**6秒間**表示し、タイムラインを6秒進める
- `<=` … 適用。`&` … 複数の Effect をひとまとめにする
- `lambda u: ...` … アニメーション。`u` は表示開始で 0、表示終了で 1 になる**進行度**。
  `fade(lambda u: u)` なら「透明→不透明」、`scale(lambda u: lerp(1.5, 1, u))` なら「1.5倍→等倍」
- `move(x=0.5, y=0.5)` … 位置は画面比率（0〜1）。(0.5, 0.5) は画面中央

レイヤーを重ねる順序・素材の時間割りは main.py、見た目の演出はレイヤー、と役割が
分かれているので、動画が大きくなってもファイルは短いまま保てる。

### 3. 次の一歩

```python
p.inspect("timeline.html")            # 配置をガントチャートで確認（レンダ前に見る）
p.render("out.mp4", dry_run=True)     # ffmpeg を実行せずコマンドだけ確認
p.audit()                             # 品質チェック（文字が小さい等の警告）
```

```
python -m scriptvedit describe --format md   # 全機能のカタログ（40 Effect / 98 Expr）
python -m scriptvedit watch main.py          # ファイル変更を監視して自動再レンダ
```

リポジトリの `examples/basic/` にも最小サンプルがある（どのディレクトリからでも
`python examples/basic/main.py` で実行できる）。

動画・音声素材は `time()` の引数を省略すると素材の長さがそのまま表示尺になる:

```python
clip.time() <= trim(3)                # duration=3（加工後の長さ）
bgm.time() <= atrim(2) & avolume(0.6)   # duration=2
img.time()                            # TypeError（画像は「素材の長さ」を持たない）
```

## 基本概念（用語）

| 用語 | 意味 |
|---|---|
| **Project** | 動画1本。解像度・fps を `configure()` し、レイヤーを重ねて `render()` する |
| **レイヤー** | 1つの `.py` ファイル。`priority` で重ね順を持つ。中で作った Object は自動登録される |
| **Object** | 素材1つ（画像・動画・音声・テキスト・HTML・数式）。開始時刻と表示尺を持つ |
| **Transform** | 1回だけ適用される静的な変形（resize / crop / rotate …）。`\|` で連結 |
| **Effect** | 時間変化できる効果（move / fade / scale …）。`&` で連結し、`lambda u:` でアニメーション |
| **u** | Effect の進行度。表示開始 0 → 表示終了 1 |
| **アンカー** | 「この表示が終わる時刻」に名前を付け、**別のレイヤーから**参照する仕組み。尺の変更が自動で波及する |
| **キャッシュ** | 中間結果を `__cache__/` に自動保存。変えていない部分は再レンダしない。鍵は内容ハッシュ |

適用順は記述順ではなく**「全 Transform → 全 Effect」のカテゴリ順**で固定
（Effect の後に Transform を書くとエラー）。詳細は「リファレンス」の各節へ。

## 演算子早見表

| 書き方 | 意味 | 詳しい節 |
|---|---|---|
| `obj <= 効果` | 適用する | 演算子によるDSL |
| `t1 \| t2` | Transform を連結 | 〃 |
| `e1 & e2` | Effect を連結 | 〃 |
| `~効果` | 品質ヒント（軽い代替処理があれば使う。無ければ通常と同一） | チェックポイントキャッシュ |
| `+効果` | キャッシュを強制再生成 | 〃 |
| `-効果` | キャッシュ対象から除外 | 〃 |
| `obj[2:5]` | 素材の 2〜5 秒を切り出し（**素材時間**） | タイムライン演算子 |
| `obj @ 12` | タイムライン 12 秒の位置に配置（**タイムライン時間**・非進行） | 〃 |
| `a >> b` | b を a の終了直後に開始 | 〃 |
| `obj * 3` | 3回連続再生 | 〃 |
| `-obj` | 逆再生 | 〃 |
| `50%P` | 0.5（パーセント記法） | パーセント記法 |

## 素材パスの解決（asset / here）

**まずはこれだけ**: プロジェクトの `assets/` フォルダに素材を置き、`asset("images/bg.jpg")` のように読む。以下はその解決規則の詳細。

レイヤーファイルは cwd に依存せず素材を参照できる。

```python
from scriptvedit import *

bg  = Object(asset("images/bg_pattern_tiles.jpg"))  # assets/ 配下を絶対パスで解決
web = Object(here("scene.html"))                       # レイヤーファイルと同じ場所
```

- `asset(relpath)`: `assets/` を自動発見して絶対パスを返す（存在しなければ「もしかして」候補付きエラー）
- `here(relpath)`: 実行中のレイヤーファイル（無ければ呼び出し元スクリプト）と同じディレクトリ
- `p.layer("bg.py")` も同様に cwd 非依存（絶対パス / cwd相対 / 呼び出し元からの相対 の順に解決）

`asset(relpath)` の解決順:

1. `<project>/assets/<relpath>` … 手で置いた素材（**最優先**）
2. `<project>/assets/_imported/<relpath>` … 過去に共有ライブラリから自動コピーした素材
3. 環境変数 `SCRIPTVEDIT_ASSETS` の各パス（**共有素材ライブラリ**。複数可・区切りは PATH と同じ `os.pathsep`（Windows `;` / POSIX `:`。POSIX でも `;` は互換で通る））
   → 見つかったら **2 の場所へコピーして、そのコピー先のパスを返す**
4. 見つからなければ「もしかして」候補付きの `FileNotFoundError`

```
:: Windows
set SCRIPTVEDIT_ASSETS=D:\media\video-assets;E:\stock
# POSIX
export SCRIPTVEDIT_ASSETS=/srv/media/video-assets:/mnt/stock
```

- **`_imported/` の意味**: 共有ライブラリから取り込んだ素材の置き場。コピーが残る同一 checkout は、以後は共有ライブラリ無しでレンダできる。ファイル名・相対パス構造はそのまま維持する（日本語名もリネームしない）。`scriptvedit new` では git 管理から除外するため、fresh clone や別 PC では `SCRIPTVEDIT_ASSETS` の設定、または素材の別途持ち込みが必要。
- **コピーは dry_run でも常に行う**。`asset()` の戻り値は ffmpeg コマンドに埋まるため、dry_run と本レンダでパスが食い違うとスナップショットが壊れるため（一貫性が最優先）。コピーはアトミック（一時ファイル → `os.replace`）で、実行時に `素材をコピーしました: assets/_imported/bgm/xxx.mp3 (3.4MB)` とログを出す。
- **キャッシュ鍵は内容ハッシュ**なので、コピーでパスが変わっても**再レンダは起きない**（`_src_signature` / `_src_bucket` はファイル内容の指紋を使う）。
- 取り込み済みのコピーと共有ライブラリ側の内容が食い違う場合は、**警告して取り込み済みを使う**（黙って上書きするとレンダ結果が勝手に変わるため）。更新したいときは `assets/_imported/` の当該ファイルを削除して再実行する。
- `must_exist=False` は存在チェックをスキップし、コピーもしない（`<project>/assets/<relpath>` を返す）。

`<project>/assets` 自体の発見順（**利用者プロジェクト優先**。環境変数による上書きは無い）:

1. カレントディレクトリから上方向に `assets/` を探索
2. 実行中のレイヤーファイルの位置から上方向に探索
3. パッケージ位置から上方向に探索（editable インストール時のリポジトリ同梱 `assets/`）

想定運用は「自分の動画プロジェクトのフォルダで ScriptVEdit をライブラリとして使い、そのフォルダ固有の `assets/` を持つ」こと。
そのため 1・2 が 3 より先に来る（逆順にすると利用者の `assets/` が永久に無視される）。探索結果はキャッシュしないため、cwd 変更・レイヤー切替に追随する。

## リファレンス

ここから下は設計の考え方と全機能の一覧。**上から順に読む必要はなく**、必要な節だけ
引けばよい。機械可読版は `python -m scriptvedit describe`（→「ケイパビリティ・マニフェスト」節）。

### 1ファイル = 1レイヤー

動画の各レイヤーを独立したPythonファイルとして管理する。
`main.py` は構成定義のみを担い、各レイヤーファイルの読み込み順序・重ね順を宣言する。

```
main.py        ... 構成定義（設定・レイヤー順序・レンダリング）
bg.py          ... 背景レイヤー
badge.py     ... 素材レイヤー
```

### 演算子によるDSL

- `|` (パイプ) ... Transform同士を連結して TransformChain を生成
- `&` (アンド) ... Effect同士を連結して EffectChain を生成
- `<=` (適用) ... Object に TransformChain / EffectChain を適用。
  実行順は記述順ではなく**「全Transform→全Effect」のカテゴリ順**。
  Effect 適用後に Transform を適用しようとすると ValueError になる
  （Effect 適用後の静的変形は `compute()` で素材化してから行う）
- `~` (チルダ) ... 品質ヒント。軽い代替処理を持つ op だけ高速側を使い、
  持たない op は通常と同一の処理（内容を削除せず、警告も出さない）
- `+` (プラス) ... policy="force"（キャッシュを強制再生成）
- `-` (マイナス) ... policy="off"（キャッシュ対象から除外）
- 無印 ... policy="auto", quality="final"（右端のbakeable opを自動キャッシュ）

```python
obj <= resize(sx=0.3, sy=0.3)     # 無印: autoポリシーで自動キャッシュ対象
obj <= +resize(sx=0.3, sy=0.3)    # force: 常に再生成
obj <= ~resize(sx=0.3, sy=0.3)    # fastヒント（未対応なら通常と同一）
obj <= -resize(sx=0.3, sy=0.3)    # off: キャッシュ対象から除外
obj.time(6) <= move(x=0.5, y=0.5, anchor="center") \
               & scale(lambda u: lerp(0.5, 1, u)) \
               & fade(lambda u: u)
```

### タイムライン演算子（スライス / `@` / `>>`）

**軸の区別**が重要: スライスは**素材時間**（イン点・アウト点）、`@` は
**タイムライン時間**（いつから表示するか）。

- `obj[2:5]` ... 素材の2〜5秒を切り出し（trim/atrim）。表示尺は切り出し長（3秒）が
  既定になる（`time()` で上書き可）。`obj[3:]`（3秒以降）、`obj[-2:]`（末尾2秒。
  probe可能な素材のみ）。step（`obj[::2]`）は「2倍速」と曖昧なため不可（`speed(2)` を使う）
- `obj @ 12` ... タイムライン12秒に絶対配置。順次配置のカーソルは進めない
  （`show()` と同じ非進行＝周囲のレイアウトを乱さない）。
  `obj @ "intro.end"` でアンカー名も指定できる
- `a >> b` ... b を a の終了直後に開始。`a >> pause.time(0.5) >> b` で間も置ける。
  先行アイテムの尺は `time()`・スライス・`until()` のいずれかで確定している必要がある
- `obj * 3` ... 3回連続再生（映像はloop、音声はaloop。表示尺は実効尺×回数）。
  繰り返す区間は `*` を書いた時点の実効尺で確定する
- `-obj` ... 逆再生（`reverse()` の糖衣。音声は反転されない・30秒上限は reverse と同じ）

```python
clip = Object("interview.mp4")[3:8] @ 12   # 素材の3〜8秒を、タイムライン12秒に置く
clip >> pause.time(0.5) >> Object("reaction.mp4")[0:2]
jingle = Object("sting.mp4")[0:2] * 3      # 2秒ジングルを3回
back = -Object("throw.mp4")[1:3]           # 1〜3秒を逆再生
```

### パーセント記法

`P` を使って 0〜1 の正規化値をパーセントで書ける。

```python
move(x=50%P, y=75%P)  # x=0.5, y=0.75
```

### Transformは静的、Effectは定数またはアニメーション

適用順は「全Transform→全Effect」のカテゴリ順で固定される（記述順ではない）。
このため Transform は必ず Effect より先に適用すること（Effect の後に Transform を
書くと ValueError）。

- **Transform** (`|` で連結、`<=` で適用): 1回だけ適用される空間変換
  - `resize(sx, sy)` ... サイズ変更
  - `rotate(deg=N)` / `rotate(rad=N)` ... 回転（静的）
  - `crop(x, y, w, h)` ... 切り抜き
  - `pad(w, h, x, y, color)` ... パディング
  - `blur(radius)` ... ぼかし
  - `eq(brightness, contrast, saturation, gamma)` ... 色調補正

- **Effect** (`&` で連結、`<=` で適用): float で定数、lambda(u) でアニメーション
  - `move(x, y, anchor)` ... 配置位置（固定 or from/toアニメーション）
  - `scale(0.5)` ... 定数0.5倍
  - `scale(lambda u: lerp(0.5, 1, u))` ... 0.5倍 → 等倍にアニメーション
  - `fade(0.5)` ... 定数 半透明
  - `fade(lambda u: u)` ... 透明 → 不透明にアニメーション
  - `zoom(to_value=2)` ... ズーム（scale のエイリアス、from/to指定可）
  - `rotate_to(from_deg, to_deg)` ... 回転アニメーション（bakeable）
  - `wipe(direction)` ... ワイプ表示（"left"/"right"/"up"/"down"。"top"/"bottom" は up/down の別名）
  - `color_shift(hue, saturation, brightness)` ... 色相/彩度/明度シフト
  - `shake(amplitude, frequency)` ... 振動（live、overlay座標変調）
  - `trim(duration, *, start=0)` ... 素材を切り出す（時間影響あり。`start` はイン点）
  - `delete()` ... 映像をレンダリングから除外（音声のみ残す）
  - `morph_to(target_obj)` ... 画像→画像モーフィング（bakeable、重い。bakeable opsの末尾に配置必須）

`u` は正規化時間（0〜1）。Effectの表示開始から終了まで線形に変化する。

### zoom（scale エイリアス）

`zoom` は `scale` の便利ラッパー。from/to 指定でアニメーションを簡潔に書ける。

```python
zoom(to_value=2)                      # 1.0 → 2.0 ズーム
zoom(from_value=0.5, to_value=2)      # 0.5 → 2.0 ズーム
zoom(value=1.5)                       # 固定1.5倍ズーム
```

### Effect分類（bakeable / live）

checkpointで焼き込まれるか、レンダリング時にoverlay座標で解釈されるかの分類。

| 種類 | 名前 | 分類 | 備考 |
|------|------|------|------|
| Transform | resize, rotate, crop, pad, blur, eq | bakeable | 全Transform は bakeable |
| Effect | scale (zoom) | bakeable | zoom は scale のエイリアス |
| Effect | fade | bakeable | |
| Effect | trim | bakeable | 時間影響あり |
| Effect | rotate_to | bakeable | |
| Effect | wipe | bakeable | |
| Effect | color_shift | bakeable | |
| Effect | morph_to | bakeable | 生成系。bakeable ops の末尾に配置必須 |
| Effect | move | live | overlay座標で解釈 |
| Effect | delete | live | overlay除外 |
| Effect | shake | live | overlay座標にsin/cosオフセット加算 |

**重要**: live Effect は checkpoint で焼かれないため、checkpoint 生成後もレンダリング時に必ず残る。

morph_to のモーフ方式（`method`）:
- `method="sdf"`（**既定**）… 形状ベース（符号付き距離場）。中間形状が常に滑らかな
  1つのシルエットになり、位置・サイズがずれた素材や文字グリフでも破綻しない。
  追加パラメータ: `align` / `edge_softness` / `color_ease` / `color_path`
- `method="transport"` … 従来の最適輸送＋ワープ場。形の**内部パーツが実際に移動する**ため、
  複数パーツを持つ素材ではこちらが向く。反面、中間フレームの輪郭が波打ちやすい。
  追加パラメータ: `max_pixels` / `w_move` / `w_color` / `w_vanish` / `grid_step` /
  `smoothing` / `color_metric` / `color_mix` / `color_local` / `alpha_mode` / `alpha_sharp`
- `method` 未指定でも transport 専用パラメータ（`max_pixels` 等）を渡した場合は
  transport が選ばれる（既定切り替え前の呼び出しが壊れないようにするため）
- 方式に対応しないパラメータを渡すと method 名付きの ValueError になる
- 中間色は両方式とも「リニア光 × OKLCh」で作る。色相を回すため、補色寄りの2色
  （例: オレンジ→青緑）は中間で黄緑を通る。中立にフェードさせたい場合は
  `color_path="oklab"`（sdf）/ `color_mix="oklab"`（transport）を指定する

morph_to の注意点:
- bakeable ops の末尾に配置する必要がある（違反時は ValueError）
- 1つの Object に1回のみ適用可能（複数指定は ValueError。多段モーフは `compute()` で中間素材を生成して分割）
- パラメータ名のタイポは構築時（`morph_to()` 呼び出し時点）に ValueError で検出される
- morph_to 直前の未ベイク transforms/effects は中間チェックポイントに自動ベイクされる（resize 等がサイレントに消えない）
- effect や動画ソースと併用した場合は、直前結果の最終フレームを RGBA PNG に抽出してモーフ入力にする

shake は overlay 座標の変調として実装されており live 分類。将来 bakeable に変更する場合は ENGINE_VER 更新が必要。

### 音声エフェクト

動画・音声ファイルの音声トラックを制御する。`&` で連結可能。`~` は品質ヒントで、
軽い代替を持たない AudioEffect では通常と同じ処理をする。音声削除は `adelete()`。

- `avolume(value)` ... 音量倍率（デフォルト 1.0。lambda(u) でフェードも書ける）
- `atrim(duration, *, start=0)` ... 音声を切り出す（時間影響あり。`start` はイン点）
- `atempo(rate)` ... テンポ変更（時間影響あり）
- `adelete()` ... 音声をミックスから除外

```python
clip = Object("video.mp4")
clip.time(5) <= move(x=0.5, y=0.5, anchor="center") \
              & fade(lambda u: u) \
              & avolume(0.6) \
              & atrim(3)
```

### 映像/音声分離（split）

`split()` で映像と音声を個別に制御できる。

```python
clip = Object("video.mp4")
v, a = clip.split()
v <= resize(sx=0.5, sy=0.5)     # 映像のみ変換
a <= avolume(0.3)                # 音声のみ音量調整
clip.time(5) <= move(x=0.5, y=0.5, anchor="center")
```

### Expr式ビルダー

lambda内で使える数学関数を多数提供。ffmpegのフィルタ式に自動コンパイルされる。

```python
# sin波フェード（フェードイン→フェードアウト）
fade(lambda u: sin(u * PI))

# 加速するスケール
scale(lambda u: lerp(0.5, 1, smoothstep(0, 1, u)))

# 円運動
move(x=lambda u: 0.5 + 0.3 * cos(u * 2 * PI),
     y=lambda u: 0.5 + 0.3 * sin(u * 2 * PI),
     anchor="center")
```

使用可能な関数:
- 三角: `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `atan2`
- 双曲線: `sinh`, `cosh`, `tanh`
- 指数/対数: `exp`, `log`, `sqrt`, `log10`, `cbrt`
- 丸め: `floor`, `ceil`, `trunc`
- 補間: `lerp(a, b, t)`
- クランプ: `clip(x, lo, hi)`, `clamp`
- ステップ: `step(edge, x)`, `smoothstep(edge0, edge1, x)`
- その他: `mod`, `frac`, `deg2rad`, `rad2deg`
- 組み込み互換: `abs`, `min`, `max`, `round`, `pow`
- 定数: `PI`, `E`

`from scriptvedit import *` 後も、これらは通常値ならPython組み込みへ委譲する。
`min(values, key=...)` / `max(..., default=...)` / `round(x, ndigits)` /
`pow(x, y, mod)`もそのまま使え、引数に`Expr`が含まれるときだけFFmpeg式を生成する。

### イージング関数

標準的なイージング30種（10ファミリ × in/out/in_out）とジェネレータ3種を提供。
lambda内で `u` に直接適用するか、`apply_easing` で値範囲付きlambdaを生成する。

- `linear(t)` ... 線形
- `ease_in_*` / `ease_out_*` / `ease_in_out_*` × `quad` / `cubic` / `quart` / `quint` / `sine` / `expo` / `circ` / `back` / `elastic` / `bounce`
- ジェネレータ（イージング関数を返す）:
  - `ease_cubic_bezier(x1, y1, x2, y2, segments=16)` ... CSS cubic-bezier互換
  - `ease_spring(stiffness=3, damping=4)` ... バネ（オーバーシュートして1.0に収束）
  - `steps(n, jump="end")` ... CSS steps()互換ステップ関数（jump: "start"/"end"）
- `apply_easing(easing_func, from_val, to_val)` ... イージングを値範囲に適用するlambdaを返す

```python
# ease関数をlambda内で直接使う
obj.time(2) <= scale(lambda u: lerp(0.5, 1, ease_out_cubic(u)))

# apply_easing: from/to範囲付きlambdaを生成
obj.time(2) <= scale(apply_easing(ease_in_out_quad, 0.5, 1.0))

# CSS cubic-bezier互換
ease = ease_cubic_bezier(0.25, 0.1, 0.25, 1.0)  # CSS ease
obj.time(2) <= fade(lambda u: ease(u))
```

### キーフレーム補間（keyframes）

`keyframes(*args, easing=None)` は固定時点 `(u, 値)` のリストから区分線形補間のパラメータ関数を生成する。
フラット形式（`t0, v0, t1, v1, ...`）とタプル形式（`(t0, v0), (t1, v1), ...`）の両方に対応。
最低2点必要で、時刻順に自動ソートされる。`easing=` で各区間の補間カーブを指定できる。

```python
# フラット形式: 0.5倍 → 1.2倍 → 等倍
obj.time(4) <= scale(keyframes(0, 0.5, 0.5, 1.2, 1.0, 1.0))

# タプル形式 + easing指定
obj.time(4) <= fade(keyframes((0, 0), (0.2, 1), (0.8, 1), (1.0, 0)))
obj.time(4) <= scale(keyframes((0, 0.5), (1, 1.5), easing=ease_in_out_quad))
```

### シーケンス関数

エフェクトパラメータを時間区間で組み立てるヘルパー。lambdaの代わりにEffect引数へ渡す。
`fn` には lambda または定数を指定できる。

- `phase(start, end, fn)` ... fnを区間[start, end]にリマッピング（区間外はclip）
- `sequence_param(*segments, default=0)` ... `(start, end, 値orfn)` のタプル列で区間切替
- `repeat(n, fn)` ... fnをn回繰り返す
- `bounce(n, fn)` ... fnをn回往復（0→1→0の三角波）
- `alternate(n, fn_a, fn_b)` ... 2つの関数をn回交互に切り替え
- `staircase(n, fn)` ... 階段状に値を上昇

```python
# 0〜30%区間でフェードイン
obj.time(6) <= fade(phase(0, 0.3, lambda t: t))

# フェードイン → 保持 → フェードアウト
obj.time(6) <= fade(sequence_param(
    (0, 0.2, lambda t: t),
    (0.2, 0.8, 1.0),
    (0.8, 1.0, lambda t: 1 - t),
))

# 3回パルス
obj.time(6) <= scale(repeat(3, lambda t: 1 + 0.2 * sin(t * PI * 2)))
```

### 条件分岐・比較

比較・論理関数は 1.0/0.0 を返すExprを生成する。`if_` / `case` と組み合わせて使う。

- `if_(cond, then_val, else_val)` ... 条件分岐
- 比較: `lt(a, b)`, `gt(a, b)`, `lte(a, b)`, `gte(a, b)`, `eq_(a, b)`, `neq(a, b)`
- 論理: `and_(a, b)`, `or_(a, b)`, `not_(a)`, `between(x, lo, hi)`
- `case(*when_then_pairs, default=0)` ... 多岐条件分岐（ネストif_の糖衣）
- `sign(x)` ... 符号関数（x>0→1, x==0→0, x<0→-1）
- `random(seed=0)` ... 疑似乱数 [0, 1)（ffmpegランタイムで評価）

```python
# u<0.5では半分サイズ、以降は等倍
obj.time(4) <= scale(lambda u: if_(lt(u, 0.5), 0.5, 1.0))

# 多岐分岐
obj.time(6) <= fade(lambda u: case(
    (lt(u, 0.3), 0.5),   # u<0.3 → 0.5
    (lt(u, 0.7), 1.0),   # u<0.7 → 1.0
    default=0.2,          # それ以外 → 0.2
))
```

### Expr チェーンメソッド

Expr（lambda内の `u` や式の結果）に対するメソッドチェーンで式を加工できる。

- `.smooth()` ... smoothstep（3t²-2t³）
- `.invert()` ... 反転（1 - x）
- `.pingpong()` ... 三角波（0→1→0）
- `.map(lo, hi)` ... 0〜1をlo〜hiへマッピング
- `.clamped(lo=0, hi=1)` ... lo〜hiにクランプ
- `.oscillate(frequency=1, amplitude=1, offset=0)` ... 正弦波（offset + amplitude * sin(x * frequency * 2π)）
- `.sawtooth(frequency=1)` ... ノコギリ波（0→1を周期的に繰り返す）
- `.triangle(frequency=1)` ... 三角波（0→1→0を周期的に繰り返す）

```python
# 滑らかに0.5〜1.0へ
obj.time(3) <= scale(lambda u: u.smooth().map(0.5, 1.0))

# 2周期の三角波フェード
obj.time(4) <= fade(lambda u: u.triangle(2))
```

### move（位置・移動）

`move` は Effect として overlay の座標を制御する。

```python
# 固定位置
move(x=0.5, y=0.5, anchor="center")

# from/to 移動アニメーション
move(from_x=0.0, from_y=0.5, to_x=1.0, to_y=0.5, anchor="center")

# lambda 移動
move(x=lambda u: lerp(0.2, 0.8, u), y=0.5, anchor="center")
```

### チェックポイントキャッシュ（policy と品質ヒント）

bakeable ops（Transform全般 + scale/fade/trim Effect）の中間結果を自動保存・復元する仕組み。
signatureベースでキャッシュの安全性を保証。保存点はRAA+FSPで最小化。

**policy（キャッシュ制御）:**
- `auto`（無印） ... キャッシュが存在すれば再利用、なければ生成。最右のbakeable opがRAA保存点
- `force`（`+`） ... 常に再生成。FSP保存点
- `off`（`-`） ... キャッシュ対象から除外

**quality（品質ヒント）:**
- `final`（無印） ... 通常処理
- `fast`（`~`） ... 軽い代替処理を実装した op だけ、その処理を要求するヒント
- 代替処理を持たない op は通常と同一の出力になる。ヒントが無視されても正常で、
  エラーや実行時警告は出さない。ヒントが尊重されない op は `p.audit()` が
  `quality-hint-ignored`（info）として報告する（→「品質lint」節）

```python
# 無印（auto+final）: 自動的に最右bakeableとしてキャッシュ
obj <= resize(sx=0.3, sy=0.3)

# force: 常に再生成
obj <= +resize(sx=0.3, sy=0.3)

# fast品質ヒント: resizeが未対応なら通常処理と同一
obj <= ~resize(sx=0.3, sy=0.3)

# off: キャッシュ対象外
obj <= -resize(sx=0.3, sy=0.3)

# チェーン: ~chainで全opにfastヒント、+chainで末尾がforce
obj <= ~(resize(sx=0.5, sy=0.5) | resize(sx=0.3, sy=0.3))
obj <= +(resize(sx=0.5, sy=0.5) | resize(sx=0.3, sy=0.3))
```

キャッシュは `__cache__/artifacts/checkpoint/{src_hash}/{signature}.{ext}` に保存。
品質ヒントを尊重するかは `describe()` の各 op にある `respects_fast_hint` で確認できる。

**中間ベイクのピクセル形式**: 動画になる中間物（checkpoint / `compute()` / morph / 粒子 /
`slideshow()` 等の xfade 生成物）は FFV1 の `bgra`（`.mkv`）で焼く。FFV1 は可逆だが
`yuva444p` では RGBA→YUV の行列変換が入り、往復がビット完全にならない
（morph 90フレームの実測で PSNR 48.2dB / alpha 不一致 122,097 画素）。`bgra` は
色変換を挟まないため往復がビット完全（PSNR=∞・誤差0）。中間物は
morph→checkpoint→本レンダと多段に積み上がるため、微小な色ずれも累積する。
代償は中間ファイルが約 +11% 肥大することだが、中間物は最終出力に残らない。
尊重しない op では `~` をキャッシュ指紋へ混ぜないため、通常処理と同じキャッシュを再利用する。
この意味は Effect / Transform / AudioEffect で共通であり、`~AudioEffect` も音声を削除しない。
音声を消す場合は `adelete()` を明示する。

### レイヤーキャッシュ

`p.layer()` の `cache` 引数で、レイヤー単位の VP9 alpha webm キャッシュを制御する。

```python
p.layer("maku.py", cache="make")   # キャッシュ生成
p.layer("maku.py", cache="use")    # キャッシュから読み込み
p.layer("maku.py", cache="auto")   # 新鮮なキャッシュがあれば利用、なければ通常実行
p.layer("maku.py", cache="off")    # キャッシュしない（デフォルト）
```

- キャッシュに保存されるのは映像のみ。音声を含むレイヤーは生成時と再生時の両方で警告し、
  再生時には音声が脱落する。音声素材は `cache="off"` の別レイヤーへ分離する
- 素材の鮮度検証: キャッシュ生成時に素材の内容ハッシュを anchors.json に記録し、素材が更新された場合は
  - `auto` ... 古いキャッシュを使わずレイヤーを再実行（再生成）
  - `use` ... 警告を出して古いキャッシュのまま続行（`cache="make"` での再生成を促す）

#### 中間ファイルの品質（cache_quality）

レイヤーキャッシュは中間ファイルを1枚挟むため、そこでの劣化がそのまま最終出力に乗る。
用途に応じて `cache_quality` で品質を選ぶ（`cache` が `"off"` 以外のときだけ意味を持つ）。

```python
p.layer("maku.py", cache="auto")                          # 既定 = "balanced"
p.layer("maku.py", cache="auto", cache_quality="draft")    # プレビュー用（軽い・粗い）
p.layer("maku.py", cache="auto", cache_quality="lossless") # 最終版用（劣化ゼロ）
```

| 値 | 中身 | 用途 |
|---|---|---|
| `"draft"` | VP9 `yuva420p` crf30 / `.webm` | プレビュー。最小・最速。輪郭にリンギングが出る |
| `"balanced"` | VP9 `yuva420p` crf15 / `.webm` | **既定**。量子化劣化をほぼ除去。draft の約1.3倍のサイズ |
| `"lossless"` | FFV1 `bgra` / `.mkv` | 完全可逆（ビット完全）。サイズは balanced の**100倍以上** |

品質はキャッシュ鍵に含まれるため、変更すると別の中間ファイルとして再生成される
（古いキャッシュが黙って再利用されることはない）。不要になった中間ファイルは
`python -m scriptvedit cache --gc` で掃除する。

**選び方の目安**（1080p30・細い等幅文字＋高彩度のコードパネルでの実測。
「真値」= コーデックを一切通さないレイヤーのRGBA）:

| 品質 | 中間サイズ(4秒) | 生成時間 | 真値とのPSNR | alpha不一致画素 |
|---|---|---|---|---|
| draft | 0.07 MB | 6.2 s | 57.7 dB | 2117 |
| balanced | 0.09 MB | 6.6 s | 64.0 dB | 13 |
| lossless | 11.96 MB | 2.3 s | ∞（完全一致） | 0 |

- **最終出力が H.264 mp4 なら、3段階の差は最終画質にはほとんど出ない**
  （最終エンコードの量子化と 4:2:0 化に埋もれる）。既定の `balanced` で十分。
- `lossless` が効くのは、劣化を積み上げたくない場合
  — 透過付き出力（`alpha=True` の webm / 連番PNG）、キャッシュ済みレイヤーを
  さらに合成し直す構成、キャッシュ層を何枚も重ねる構成。
- `lossless` は**可逆ゆえにサイズが桁違い**（上表で130倍）。ディスクと相談すること。
  なお生成は FFV1 のほうが VP9 より速い（可逆で探索が要らないため）。

> **注意**: `"balanced"` でもクロマ間引き（4:2:0）自体は無くならない。
> alpha を保持できる非可逆コーデックは `libvpx-vp9` の `yuva420p` だけで
> （`libvpx-vp9` は `yuva444p` 非対応、ProRes 4444 は同素材で可逆FFV1より
> 2.3〜3.1倍大きく可逆の代替にならない）、**クロマ間引きを完全に無くすには
> `"lossless"` を選ぶ**しかない。量子化を止めても 4:2:0 のままでは
> PSNR は 49.97 dB で頭打ちになる（実測）。

### 方針: genchain は提供しない

生成系（morph_to等）のチェーン化は行わない。生成系Effectは単体でのみ使用する。

### anchor / pause / until（クロスレイヤー同期）

レイヤー間でタイミングを同期する仕組み。

```python
# レイヤーA: 幕を3秒表示してアンカーを打つ
maku = Object("maku.png")
maku.time(3) <= move(x=0.5, y=0.5, anchor="center")
anchor("curtain_done")

# レイヤーB: 幕が終わるまで待ってから登場
pause.until("curtain_done")
oni = Object("oni.png")
oni.time(3) <= move(x=0.5, y=0.5, anchor="center")
```

- `anchor(name)` ... 現在のタイムライン位置に名前付きマーカーを登録
- `pause.time(N)` ... N秒間の非描画待機
- `pause.until(name)` ... アンカー時刻まで非描画待機
- `pause.until(name, offset=N)` ... アンカー時刻+offset秒まで待機
- `obj.until(name)` ... durationをアンカー時刻まで伸長
- `obj.until(name, offset=N)` ... durationをアンカー時刻+offset秒まで伸長

```python
# offset例: アンカーから0.5秒後まで待機
pause.until("curtain_done", offset=0.5)

# 負offset: アンカーの0.1秒前まで
obj.until("curtain_done", offset=-0.1)
```

### time(name=...)（自動 start/end アンカー）

`time()` に `name` を指定すると `X.start` と `X.end` アンカーが自動生成される。

```python
obj.time(3, name="scene1")  # scene1.start=開始時刻, scene1.end=終了時刻
pause.until("scene1.end")    # scene1の終了を待つ
```

生成アンカー（`X.start` / `X.end`）は明示 `anchor()` と共通の重複管理に
登録される。同名アンカーを**別レイヤー**で定義するとエラー
（同一レイヤーファイルの再実行は許容）。

### show / show_until（同時表示）

`show()` と `show_until()` は `current_time` を進めずにオブジェクトを表示する。
複数素材を同一時刻から重ねて表示したい場合に使用する。

```python
bg.time(6) <= move(x=0.5, y=0.5, anchor="center")
overlay_a.show(6) <= move(x=0.3, y=0.3, anchor="center")  # current_time非進行
overlay_b.show_until("scene1.end") <= move(x=0.7, y=0.7)   # アンカーまで同時表示
overlay_c.show(3, priority=10) <= move(x=0.5, y=0.5)       # priority指定可
```

- `obj.show(duration)` ... current_timeを進めずにduration秒表示
- `obj.show(duration, priority=N)` ... priority指定付き
- `obj.show_until(name)` ... current_timeを進めずにアンカーまで表示
- `obj.show_until(name, offset=N)` ... offset秒ずらし

### compute（タイムライン外素材生成）

Transform/bakeable Effectを適用した中間素材をタイムライン外で生成する。
キャッシュ対応（checkpoint方式）。live Effect（move等）は使用不可。

`compute()` は Object 自身を「焼いた素材」へその場で変異させる
（source が生成物パスになり、焼き込み済みの transforms/effects と
音声状態はクリアされる。生成物 PNG/mkv は音声を持たない）。
Project からは一旦除外されるが、`time()` / `show()` / `until()` / `@` で
再配置すればその時点のタイムライン位置へ再登録される。

```python
processed = Object("source.png")
processed <= resize(sx=0.5, sy=0.5) | blur(radius=3)
processed.compute()  # タイムライン外でPNG生成

# 生成した素材を通常通り配置
processed.time(3) <= move(x=0.5, y=0.5, anchor="center")

# 動画生成（duration指定）
clip = Object("source.png")
clip <= resize(sx=0.5, sy=0.5)
clip.compute(duration=3)  # WebM動画として生成
```

### テンプレート機能

字幕・吹き出し・図解をPython関数1行で生成。内部でweb Object (HTML→Playwright→webm) パイプラインを利用。

```python
# 字幕（画面下部テロップ）
s = subtitle("こんにちは！", who="Alice", duration=2.5)

# 字幕ボックス（中央配置ボックス型）
sb = subtitle_box("タイトルテキスト", duration=3.0)

# 吹き出し
b = bubble("ここがポイント！", duration=1.0, tail=(0.6, 0.75))

# 図解
d = diagram([
    rect(0.05, 0.1, 0.4, 0.25, fill="none", stroke="#fff"),
    label(0.25, 0.22, "Step 1", fill="#fff"),
    circle(0.7, 0.3, 0.06, fill="#ff6644"),
    arrow(0.45, 0.22, 0.62, 0.3, stroke="#ffcc00"),
    spotlight(0.5, 0.5, 0.15),
], duration=3.0)
```

テンプレート共通オプション: `style={}`, `size=(w,h)`, `name=`, `debug_frames=`, `deps=[]`

diagram 図形要素:
- `rect(x, y, w, h, **kw)` ... 矩形
- `circle(x, y, r, **kw)` ... 円
- `arrow(x1, y1, x2, y2, **kw)` ... 矢印
- `label(x, y, text, **kw)` ... テキスト
- `spotlight(x, y, r, **kw)` ... スポットライト（暗幕くり抜き）

### web Object（HTML直接指定）

HTMLファイルをPlaywright経由でフレーム描画し、WebM動画として生成する。

```python
web_obj = Object("template.html",
                 duration=5.0,
                 size=(1280, 720),
                 data={"message": "Hello"},
                 deps=["style.css"])
web_obj.time(5) <= move(x=0.5, y=0.5, anchor="center")
```

- `duration` (必須) ... 表示秒数
- `size` (必須) ... キャンバスサイズ `(width, height)`
- `fps` ... フレームレート（デフォルト: Project.fps）
- `data` ... HTML/JSに渡すデータ辞書
- `name` ... 内部名称（自動生成）
- `debug_frames` ... フレーム出力デバッグ
- `deps` ... 依存ファイルリスト（変更検出用）

HTML内で `window.renderFrame(state)` 関数を定義する。
`state`: `{frame, t, u, fps, duration, width, height, data, seed}`

### 2パスアーキテクチャ

`render()` は2段階で実行される:

1. **Plan pass** ... アンカーを固定点反復で解決（cache は no-op）
2. **Render pass** ... アンカー確定済みの状態で本実行、ffmpegコマンドを構築・実行

### レイヤーの独立タイムライン

各レイヤーは0秒から独立したタイムラインを持つ。
動画全体のdurationは全レイヤーの最大値から自動算出される。

### priority による z-order 制御

`p.layer(filename, priority=N)` の `priority` で重ね順を制御する。
値が大きいほど手前に表示。記述順に依存しない。

### 映像エフェクト

Effectとして時間軸上に適用する映像加工。すべて bakeable（checkpoint に焼き込まれる）。
`obj.time(N) <= effect` で適用する。

```python
img.time(4) <= chroma_key(color="green", similarity=0.1, blend=0.0)  # 指定色を透明化
img.time(4) <= vignette(strength=0.6)          # 周辺減光（strength 0..1 か angle=rad）
img.time(4) <= pixelize(size=16)               # モザイク（size定数のみ）
img.time(4) <= glow(radius=10, intensity=1.0)  # 発光（split→gblur→screen合成）
img.time(4) <= lut("film.cube")                # 3D LUT（.cube/.3dl等）
img.time(4) <= glitch(strength=1.0, interval=None)  # RGBずれ+ノイズ（interval秒で間欠）
img.time(4) <= perspective_warp(0,0, 640,20, 40,360, 600,340)  # 4隅(左上/右上/左下/右下)の移動先px
img.time(4) <= lens(k1=-0.2, k2=0.0)           # レンズ歪み補正（-1〜1）
img.time(4) <= ken_burns((0,0,640,360), (200,120,320,180), easing=ease_in_out_quad)  # パン&ズーム
img.time(4) <= drop_shadow(dx=5, dy=5, blur=8, color="black", opacity=0.5)  # ドロップシャドウ
img.time(4) <= outline(width=2, color="white") # 縁取り（width 1〜16の整数）
```

- `vignette` は `angle`（rad, 0〜π/2）か `strength`（0〜1）の一方のみ指定。アルファ非対応のため全画面素材向け
- `pixelize` の `size`、`outline` の `width` は式アニメ非対応（定数のみ）
- `ken_burns` は from/to 矩形 `(x, y, w, h)`（同一アスペクト比）を指定。出力は両矩形の最大寸法に正規化される
- `lut` はファイルの存在を構築時に検証し、内容をキャッシュ署名に含める

### トランジション・スライドショー

複数素材を xfade で1本に連結した合成Objectを生成する（キャッシュ生成物、音声なし）。

```python
# 画像列をクロスフェードで連結（各3秒表示、遷移0.5秒）
show = slideshow(["a.png", "b.png", "c.png"], each=3.0, transition="fade", t_dur=0.5, size=None)
show.time(9) <= move(x=0.5, y=0.5, anchor="center")

# 2素材をxfadeで連結（Objectはこの合成に消費され、Projectのタイムラインから除外される）
a = Object("a.png"); a.time(3)
b = Object("b.png"); b.time(3)
clip = transition(a, b, kind="wiperight", duration=1.0)
clip.time(clip.duration) <= move(x=0.5, y=0.5, anchor="center")
```

- `slideshow` の合成尺は `len(images) * each` 秒、`t_dur` は `each` 未満
- `transition` の合成尺は `dur_a + dur_b - duration` 秒。画像は事前に `.time(秒)` が必要
- どちらも xfade の遷移名（fade/wiperight/circleopen 等 58種）を受け付ける。加工済み素材は先に `compute()` で素材化する

### 合成・コンポジション

ネストコンポジション・マスク・合成モードなど、素材を重ねて加工する機能。

```python
# from_project: サブProjectを透過webm素材化して1Objectとして親に配置（プリコンポーズ）
sub = Project()
sub.configure(width=640, height=360)
sub.layer("scene_sub.py")
comp = Object.from_project(sub, cache="auto")   # cache: "auto"（既存再利用）/ "force"（常に再生成）
comp.time(comp.duration) <= move(x=0.5, y=0.5, anchor="center")

# mask / mask_wipe: 画像の輝度をアルファに使う（黒=透明, 白=不透明, グレー=半透明）
oni.time(2) <= mask("mask_gradient.png") & move(x=0.5, y=0.5, anchor="center")
oni.time(3) <= mask_wipe("mask_gradient.png", progress=lambda u: u) & move(x=0.5, y=0.5, anchor="center")

# opacity / blend_mode / rounded: 不透明度・合成モード・角丸
oni.time(3) <= opacity(0.6) & move(x=0.5, y=0.5, anchor="center")      # 定数(0〜1) or Expr/lambda
oni.time(3) <= blend_mode("screen") & move(x=0.5, y=0.5, anchor="center")
oni.time(3) <= rounded(24) & move(x=0.5, y=0.5, anchor="center")       # 角丸半径px

# pip: ピクチャインピクチャのプリセット（縮小+角丸+縁取り+影+配置の合成）
clip.time(clip.duration) <= pip(x=0.75, y=0.75, scale=0.3, radius=12, border=2)

# blur_background_fill: ぼかした自分自身を背景に敷く（縦横変換の定番、出力はキャンバス固定）
fox.time(3) <= blur_background_fill(blur=24)

# progress_bar: 動画全体の進行バー（duration/time 不要・全体に重なる特殊Object）
progress_bar(height=8, color="orange", bg="white@0.15", y=1.0)
```

- `Object.from_project(sub_project, *, cache="auto")` は `layer()` 登録済みの Project を透過webmにキャッシュ生成して1Objectとして返す（キャッシュ鍵は configure+レイヤーFFP+素材FFP、素材更新で自動再生成）
- `mask` / `mask_wipe` の画像は輝度をアルファに使う。`mask_wipe(image, progress=None)` の `progress` は 0→1 の進行で Expr/lambda 可（省略時は線形）。グラデーション画像で方向・形状を制御できる
- `opacity(value)` は定数（0〜1）だと colorchannelmixer で高速、Expr/lambda だと geq によるアニメーション（どちらも bakeable）
- `blend_mode(mode)` の有効モード: addition/screen/multiply/overlay/darken/lighten/difference/hardlight/softlight/dodge/burn/negation ほか（`add`/`plus` は addition のエイリアス）。overlay フィルタは合成モード非対応のため、このObjectのみ blend + maskedmerge 経路に切り替わる（**キャンバス内合成が前提**）
- `pip(x=0.7, y=0.7, scale=0.3, radius=12, border=2, border_color="white", shadow=True)` は既存Effectの組（scale→rounded→outline→drop_shadow→move）を返すプリセット
- `blur_background_fill(blur=20)` / `blend_mode` は live Effect（checkpoint非対象）。`opacity` は式指定でも bakeable
- `progress_bar(*, height=6, color="white", bg="white@0.2", y=1.0)`: 色はアルファ指定可（`"white@0.2"`）、`y` は 0=上端 / 1=下端。タイムラインを進めない表示専用Object

### 時間操作

再生速度・逆再生・フリーズ・動画連結など、時間軸そのものを操作する Effect（すべて live、実効尺に反映される）。

```python
# speed: 再生速度変更（実効尺 = 元尺/factor。音声付き動画は atempo が自動適用）
clip.time() <= speed(2.0)          # 2倍速（尺は半分、length() に反映）

# reverse: 逆再生（実効尺30秒超は明示エラー。音声は反転されない）
clip.time() <= reverse()

# freeze_frame: 時刻 at のフレームで duration 秒静止してから続きを再生（総尺 +duration）
clip.time() <= freeze_frame(at=1.5, duration=2.0)

# video_sequence: 複数動画クリップを xfade（+全クリップ音声ありなら acrossfade）で連結
seq = video_sequence("a.mp4", "b.mp4", transition="fade", t_dur=0.5)
seq.time(seq.duration) <= move(x=0.5, y=0.5, anchor="center")
```

- `speed(factor)` は 0.01〜100。音声付き動画には対応する `atempo` が自動適用される（有効範囲0.5〜100を超える場合は多段に自動分解）
- `reverse()` は全フレームをメモリ保持するため、**実効尺が30秒を超える素材には使用不可**（明示エラー。`trim()` で短縮してから適用）。音声は反転されない
- `freeze_frame(at, duration)` の `at` は実効尺未満（**境界以上は拒否**）。音声は変化しない
- `video_sequence(*objs, transition="fade", t_dur=0.5)` は2つ以上の動画Object/パスを連結。合成尺は `sum(実長) - t_dur*(n-1)` 秒、`t_dur` は最短クリップ未満。Transform/Effect適用済みObjectは先に `compute()` で素材化してから渡す

### テキスト・字幕

drawtext / subtitles で文字を直接描画する映像Object。画像同様 `.time(秒)` で配置する。

```python
text("こんにちは", x=0.5, y=0.3, size=64, color="white", box=True).time(3)     # 静的テキスト
typewriter("1文字ずつ表示", cps=10, x=0.1, y=0.5).time(4)                      # タイプライタ
counter(0, 100, format="%03d", x=0.5, y=0.5, size=80).time(4)                 # 数値カウントアップ
subtitles("subs.srt", style="FontName=Meiryo,FontSize=28").time(30)           # SRT/ASS/VTT字幕
```

- `x` / `y` / `alpha` は 0..1 のキャンバス比率で Expr/lambda 可（liveアニメ）
- `size` は定数のみ（FFmpeg 8.0 の drawtext fontsize 式は SEGV のため）
- フォントは未指定時に OS 別の既定候補を自動探索する（Windows: メイリオ等 / Linux: Noto Sans CJK・IPAゴシック / macOS: ヒラギノ）。環境変数 `SCRIPTVEDIT_FONT` で既定フォントを上書き可能（CI・Docker での固定に便利）。見つからない場合は OS 別の導入例（`apt install fonts-noto-cjk` 等）つきのエラーで案内する
- `counter` の `format` は整数指定（`%d` / `%03d` 等）のみ。前後のリテラル文字も表示可能
- `subtitles` は SRT 自身のタイムコードで表示されるため `.time(全体尺)` で開始0に配置する

### 数式レンダリング（formula / formula_lines）

LaTeX 数式を透過PNGにして配置する。**KaTeX をリポジトリに同梱**しているため完全オフラインで動作し（CDN 参照なし・TeX 処理系不要）、
戻り値は通常の画像 Object なので `move` / `fade` / `scale` / `rotate` 等の既存アニメがそのまま効く。

```python
# 単一の数式（別行立て）
eq = formula(r"\sum_{k=1}^{n} k = \frac{n(n+1)}{2}", size=64, color="white")
eq.time(4) <= fade(lambda u: u) & move(x=0.5, y=0.4, anchor="center")

# インライン数式（display=False）+ 色・duration 指定
inl = formula(r"x^2 + y^2 = r^2", size=36, color="#ffcc00", display=False, duration=3)
inl <= move(x=0.5, y=0.75, anchor="center")

# formula_lines: 複数行を縦積み（式変形・証明の提示）
proof = formula_lines([
    r"a^2 + b^2 = c^2",
    r"c = \sqrt{a^2 + b^2}",
], size=40, gap=16, align="center")
proof.time(3) <= move(x=0.5, y=0.5, anchor="center") & scale(lambda u: lerp(0.8, 1.0, u))
```

- `formula(latex, *, size=48, color="white", display=True, duration=None, padding=4, align="left")`
- `formula_lines(latex_lines, *, size=48, color="white", display=True, duration=None, padding=4, gap=12, align="left")`
- `size` は基準フォントサイズpx（数式全体がこれに比例）。`color` は CSS カラー（`"white"` / `"#ffcc00"` / `rgba(...)`）
- `display=True` は別行立て（displayMode）、`False` はインライン
- `duration` を渡すと `.time(秒)` 相当。省略時は通常どおり `.time(秒)` / `.show(秒)` で配置する
- 数式要素だけを要素スクリーンショットで切り出すため余白がない（`padding` で調整）
- 生成物は content-addressed キャッシュ（`__cache__/artifacts/formula/*.png`）。キャッシュ鍵には KaTeX の CSS/フォント（woff2）も含まれる
- **Playwright + Chromium が必要**（web Object と同じ経路）

### オーディオ（拡張）

```python
p.normalize_audio(target=-14, true_peak=-1.5, limiter=True, sample_rate=48000)
# loudnorm → 48kHz化 → 最終ピークリミッター

bgm.time() <= loop(until=None) & duck_under(narration, ratio=8)  # ループ + 自動ダッキング

seq = audio_sequence("a.mp3", "b.mp3", crossfade=1.0)  # acrossfade連結（2つ以上）
hit = sfx("click.wav", at=[0.5, 1.5, 3.0], volume=1.0) # 同一音源を複数時刻に配置
viz = audio_viz("bgm.mp3", kind="waves", color="cyan") # 波形/スペクトルを映像化
```

- `normalize_audio` は Project メソッド。`duck_under` / `loop` は AudioEffect（`&` で連結）。
  `~` は映像系と共通の品質ヒントで、音声を消すには `adelete()` を使う
- `duck_under(other, *, ratio=8, threshold=0.05, attack=20, release=250)`: `other`（ナレーション等）再生中に自音量を下げる。sidechainは自動で無音延長されるため、ナレーション終了後もBGMは指定尺まで続く
- `loop(until=None)`: 省略時は Project.duration までループ
- `audio_sequence` は連結後の実尺を返却Objectの`duration`へ自動設定する。`Narration`を直接渡すと字幕もcrossfade込みで並び、返却Objectの数値`@`配置へ追従する。追加の`.time(total)`は不要
- `normalize_audio(target=-14, *, true_peak=-1.5, lra=11, limiter=True, sample_rate=48000)` は最終音声へloudnorm、サンプルレート確定、任意のピークリミッターを順に適用する。`true_peak`は最終lossy出力の目標で、AAC/Opus再上昇向けに内部で0.5dBの余裕を確保する。WebM/Opusの出力レートは48kHz固定
- `audio_sequence` / `sfx` / `audio_viz` はキャッシュ生成物（音声/映像Objectを返す）。`audio_viz` の `kind` は `"waves"` / `"spectrum"` / `"cqt"`

### パーティクル（explode / assemble）

`morph_to` と同じ終端フレーム機構でベイクされる生成系Effect。bakeable ops の末尾に配置する。

```python
img.time(3) <= explode_to(blend=lambda u: u)          # 自身が粒子化して飛散
img.time(3) <= assemble_from(Object("logo.png"))      # source の粒子が集合して画像になる
```

- パーティクルパラメータ（`**particle_params`）: `max_pixels`, `speed`, `gravity`, `spread`, `swirl`, `particle_size`, `seed`, `dissolve`, `expand`
- `assemble_from(source)` の `source` は集合アニメに消費され、Project のタイムラインから自動除外される
- 生成エンジンは `scriptvedit.morph`（`generate_explode_frames` / `generate_assemble_frames`）

### タイムライン構成

```python
# シーン: with 内は相対時刻、シーンは時間軸上に順次配置される
with scene("intro", 5):
    title.time(3) <= fade(lambda u: u)

# 部分レンダ: 時間窓 [start, end) のみ出力（式の t 基準は保持）
p.render("clip.mp4", start=2.0, end=5.0)

# group: 複数Objectへ Transform/Effect/time を一括適用
group(a, b, c) <= move(x=0.5, y=0.5, anchor="center")
group(a, b).time(3)

# grid / tile: 画像を cols×rows に複製配置（背景パターン）
bg.grid(4, 3, gap=8)              # または tile(bg, 4, 3, gap=8)

# marker / チャプター: mp4 に FFMETADATA 埋め込み + YouTube 目次を書き出し
p.marker(0, "オープニング"); p.marker(12, "本編")
p.export_chapters("chapters.txt")

# param: CLI / 環境変数で差し替え可能なテンプレート変数
title_text = p.param("title", "デフォルト")   # --param title=... / SCRIPTVEDIT_PARAM_title
```

- `grid(cols, rows, *, gap=0)` は画像素材のみ。`marker` は `render()` 時にチャプターとして埋め込まれる
- `param` は `default` の型（int/float/bool）に合わせて文字列値を変換する（バッチ生成用）

### パスアニメーション・Expr拡張

```python
# パス移動（いずれも move 系 Effect。x/y は画面比率 0..1）
obj.time(4) <= move_along([(0.1,0.5),(0.5,0.2),(0.9,0.5)], easing=ease_in_out_quad)  # 区分線形
obj.time(4) <= path_bezier((0.1,0.5),(0.3,0.1),(0.7,0.9),(0.9,0.5))   # 3n+1点の3次ベジェ
obj.time(4) <= throw(vx=0.4, vy=-0.6, gravity=1.0)      # 放物運動（+yが下）
obj.time(4) <= inertia(vx=0.5, vy=0.0, damping=3.0)     # 慣性減速（指数減衰）

# 進行方向追従回転（look_at / rotate_to(follow=)）
path = move_along([(0.1,0.5),(0.9,0.5)])
obj.time(4) <= path & look_at(path, offset_deg=90)      # パスの進行方向を向く
obj.time(4) <= path & rotate_to(follow=path)            # look_at と同義

# perlin: 手ブレ用の滑らかな擬似ノイズ「値式」（move/rotate_to 等に渡せる）
obj.time(4) <= move(x=lambda u: 0.5 + perlin(u, amplitude=0.02),
                    y=lambda u: 0.5 + perlin(u, seed=1, amplitude=0.02))

# デバッグ表示
(sin(Var("u") * PI)).plot()      # u=0..1 のアスキー折れ線グラフを表示（matplotlib非依存）
p.explain(obj)                   # obj のフィルタチェーンと u 正規化の分母(dur)の由来を表示
```

- `perlin(u, *, octaves=2, seed=0, frequency=1.0, amplitude=1.0)`: 非整数周波数の sin 合成で不規則な揺れを作る（shake は規則的正弦）
- `Expr.plot(samples=60, height=15, width=60)` は `u` のみに依存する式に使う

### 出力形式

`render()` の出力拡張子で形式を自動判定する。

```python
p.render("out.mp4")               # H.264 / AAC（既定）
p.render("out.gif")               # GIF（2パスパレット）
p.render("out.webp")              # アニメーション WebP
p.render("out.png")               # 連番PNG（out.png → out_%05d.png）
p.render("out.webm", alpha=True)  # 透過VP9（yuva420p）
p.render("out.mp4", draft=True)   # 半解像度・軽量エンコード。Webも既定8fpsで撮影
p.thumbnail(at=2.5, out="thumb.png")   # 指定時刻の1フレームをPNG抽出
p.thumbnail(at=92, out="thumb.png", source="out.mp4")  # 完成動画を入力seek（高速）
```

`configure` で解像度プリセット / エンコーダ / 並列度を設定する。

```python
p.configure(preset="shorts")      # shorts/reel/square/hd/720p/2k/4k 等（w/h/fps を一括設定）
p.configure(encoder="nvenc")      # nvenc/hevc_nvenc/qsv/hevc（利用不可なら libx264 へ警告付きフォールバック）
p.configure(parallel=4)           # キャッシュ並列生成のワーカ数
p.configure(draft_web_fps=8)      # draft時のCanvas screenshot上限。Noneで本番同等
```

- 透過出力（`alpha=True`）は `.webm`（VP9）を推奨。gif / h264 はアルファを保持できない
- `encoder` は `ffmpeg -encoders` で検出のみ。検出できても環境により libx264 にフォールバックし得る

### 時間分割並列レンダ（render(parallel=N)）

最終レンダの filtergraph 評価はほぼ単一スレッドで、長尺・多オブジェクトでは
エンコードよりフィルタ評価が支配的になる。`parallel=N` は総尺を**フレーム境界で**
N 分割し、各区間を別プロセスの ffmpeg で並列レンダして concat（`-c copy`）で
無劣化結合する。

```python
p.render("out.mp4", parallel=4)   # 4分割並列。未指定/1 なら従来どおり単一プロセス
```

- **仕組み**: フィルタ式は全て絶対タイムライン時刻 `t` 基準
  （`tpad` / `enable='between(t,..)'` / `u=clip((t-start)/dur,..)` / drawtext）なので、
  チャンク側では「背景PTSを +t0 シフト → 全フィルタ評価 → -t0 で戻す」だけで
  フィルタ文字列は全編レンダと同一のまま成立する。各オブジェクトは tpad 整列直後に
  `trim` で区間前のフレームを破棄し、区間に重ならないオブジェクトは入力ごと除外する
- **音声は分割しない**: `loudnorm` / `duck_under` は全尺依存のため、音声は全編1本を
  並行レンダし、concat 結果へ mux する（境界のサンプルずれも起きない）。
  チャプター（marker）も mux 時に付与される
- **出力の同一性**: フィルタ文字列が全編レンダと同一のため、エンコード前のフレームは
  一致する。最終出力は H.264 のレート制御が GOP 境界で変わるためビット同一には
  ならないが、視覚的には同一（実プロジェクト2分56秒での実測: フレーム数完全一致・
  PSNR 平均53.8dB / 最低46.0dB・SSIM 0.9996・音声はデコードPCMがMD5完全一致）
- **対応形式**: H.264系（.mp4/.mkv/.mov、draft含む）のみ。gif/webp/webm/連番PNG/
  alpha や `start`/`end` 部分レンダとの併用時は通知の上で従来レンダへフォールバック
- **配分**: 各チャンクへ `-threads ceil(CPU数/N)` を渡してエンコーダスレッドの
  過剰予約を防ぐ。`configure(parallel=N)`（キャッシュ並列生成のワーカ数）とは別物
- **向き不向き**: フィルタ評価が支配的な長尺プロジェクトほど効く
  （実測条件: 2分56秒・87オブジェクトの実プロジェクトを20コアPCで計測 —
  逐次 1012s → 並列2: 255s / 並列4: 149s / 並列8: 106s）。並列2で4倍になるのは、逐次レンダが
  「後半オブジェクトの tpad クローン区間（開始前）にも drawtext 等を評価する」
  浪費を head_trim が同時に排除するためで、分割は単なる並列化以上に効く。逐次レンダが1秒未満で終わる極小
  プロジェクト（目安5〜10秒尺以下）では、プロセス起動+concatの固定
  オーバーヘッド（計0.1秒弱）が上回り並列の方がわずかに遅い。
  区間をまたぐオブジェクトはソース先頭からのデコードが発生するため、
  超長尺の1本物ソースが多い構成では分割数を上げても伸びにくい

### ツール・開発体験（DX）

```python
# 検査ビュー（scriptvedit.viz 統合）
p.inspect("timeline.html")        # HTMLガントチャートを書き出しパスを返す
print(p.inspect())                # 省略時はテキストレポート文字列を返す

# ファイル監視（標準ライブラリのポーリング。変更時に再実行）
watch("main.py", out="out.mp4", interval=0.5, max_cycles=None)

# 品質lint（レンダ前チェック。人間レビュー由来のルール集）
findings = p.audit()              # レポートをprintし findings のリストを返す
p.audit(strict=True)              # warningが1件でもあればRuntimeError（CI向け）
```

`audit()` のルール: 文字が小さい/縁取り・影・下地なし（`text-too-small` /
`text-no-decoration`）、音声が重なるのに `duck_under` なし
（`audio-overlap-no-duck`）、BGM のループ・尺不足（`bgm-loop` / `bgm-too-short`）、
`normalize_audio()` 未設定（`no-normalize-audio`）、`~` 品質ヒントが尊重されない op
（`quality-hint-ignored`、info）、Web/Canvas内部が静的検査対象外であること
（`web-content-uninspected`、info）。エラーにはせず findings
（`{"severity", "code", "message"}` の list）を返す。

キャッシュ管理・監視は CLI からも実行できる。

```
python -m scriptvedit new myvideo               # プロジェクト雛形を生成
python -m scriptvedit cache --stats             # 種別ごとの件数・サイズ
python -m scriptvedit cache --gc --keep-days 7  # 7日より古い生成物を削除
python -m scriptvedit cache --clear             # キャッシュ全削除
python -m scriptvedit describe                  # 全機能の機械可読マニフェスト
python -m scriptvedit watch main.py --out out.mp4
```

不明な設定キー・プリセット名・エンコーダ名・`audio_viz` の kind などは、difflib による「もしかして: ...?」候補付きのエラーになる。

### プラグイン機構（@effect_plugin）

パッケージ本体（`src/scriptvedit/`）を編集せずに、`plugins/*.py` へ新しい Effect を追加できる。
cwd の `plugins/` は自動読み込みされ、登録された Effect は `from scriptvedit import *` の名前空間にファクトリ関数として注入される。

```python
# plugins/my_scanline.py
from scriptvedit import effect_plugin

@effect_plugin(
    "scanline", bakeable=True, category="視覚効果",
    params={
        "spacing":  {"type": "int",  "default": 4, "min": 2, "max": 256, "desc": "走査線の周期(px)"},
        "darkness": {"type": "expr", "default": 0.35, "min": 0, "max": 1, "desc": "濃さ(Expr可=liveアニメ)"},
    },
)
def build_scanline(params, ctx):
    """CRT風の走査線（1行要約がマニフェストに載る）"""
    d = params["darkness"].to_ffmpeg(ctx["u_T"])
    return ["format=rgba", f"geq=...{d}..."]
```

```python
# レイヤーファイル側: 組込Effectと同じように使える
img.time(4) <= scanline(spacing=6, darkness=lambda u: u)
```

- ビルダーは ffmpeg フィルタ文字列のリストを返す。`bakeable=True` でチェックポイント/compute のベイク対象になる
- `params` のスキーマ（type / default / min / max / desc）から引数検証と `describe` 用のメタデータが自動生成される。`type="expr"` は Expr/lambda によるアニメ可
- **組込の名前およびサブモジュール名（`beat` / `tts` / `viz` / `morph` / `testkit` など）は予約名で使用禁止**（衝突するとその機能が壊れるため、登録時に PluginError）
- プラグイン同士の再登録のみ `override=True` で許可。プラグインのコード指紋はキャッシュ署名に含まれる
- 同梱サンプル: `plugins/example_scanline.py` / `example_neon.py` / `example_photo_frame.py`
- **安全性の注意**: `import scriptvedit` するだけで **cwd の `plugins/*.py` が Python コードとして実行される**。
  信頼できないディレクトリ（ダウンロードした他人のプロジェクト等）で import する前に `plugins/` の中身を確認するか、
  環境変数 `SCRIPTVEDIT_NO_PLUGINS` を設定して自動読込を無効化すること（`load_plugins()` で明示的に読み込む運用も可）。

### ケイパビリティ・マニフェスト（describe）

全 Effect / Transform / 関数のシグネチャ・引数レンジ・bakeable/live 区分・制約を機械可読で出力する。
本体を読まずに「今この環境で使える機能」を列挙できるため、AI に渡すコンテキストとして使える。

```
python -m scriptvedit describe                  # JSON（全機能。プラグイン登録分も含む）
python -m scriptvedit describe --format md      # Markdown
python -m scriptvedit describe --kind effect    # 種別で絞る
python -m scriptvedit describe --name fade      # 単一エントリ
python -m scriptvedit describe -o manifest.json # ファイル出力
```

Python からは `from scriptvedit import describe, describe_markdown` で同じデータを取得できる。

### 音声合成（scriptvedit.tts / voice）— バックエンド差し替え可能

`voice()` は `scriptvedit.tts` でテキストを音声合成し、実長を `duration` に設定した音声Objectを返す。TTS バックエンドは3つから選べる。

| backend | 導入 | ネット | 特徴 | speaker の指定 |
|---|---|---|---|---|
| `"voicevox"` | VOICEVOX エンジンを別途起動（既定 `127.0.0.1:50021`） | 不要（オフライン） | キャラクターボイス。話速・音高の調整が細かい | 数値スタイルID（例 `3`） |
| `"edge"` | `pip install edge-tts`（`pip install scriptvedit[tts]`） | **必須**（Microsoft のサーバーで合成） | 導入が最も楽・APIキー不要・高品質な日本語 | 音声名（例 `"ja-JP-NanamiNeural"` / `"ja-JP-KeitaNeural"`、短縮名 `"nanami"`/`"keita"` も可） |
| `"sapi"` | 追加導入不要（Windows 標準） | 不要（オフライン） | Windows 専用。品質は低め。pitch 非対応 | インストール済み音声名の部分一致（例 `"Haruka"`） |

```python
v = voice("こんにちは、世界", speaker=3, speed=1.0, pitch=0.0, volume=1.0)   # VOICEVOX
v = voice("こんにちは、世界", backend="edge")                                 # edge-tts（既定音声）
v = voice("こんにちは、世界", backend="edge", speaker="ja-JP-KeitaNeural", speed=1.1)
v.show(v.duration)                # 合成音声の長さで配置（字幕・タイムラインと自然に同期）
```

- **バックエンドの自動選択**（`backend=None`、既定）: 環境変数 `SCRIPTVEDIT_TTS_BACKEND` があればそれ → VOICEVOX が起動していれば `voicevox` → 起動していなければ `edge`（edge-tts が入っていれば）。どれも使えなければ導入方法を示すエラー
- `speaker` は**バックエンドごとに解釈が違う**（上表）。`speaker=None` で各バックエンドの既定話者。edge に数値を渡した場合は日本語音声一覧へ写像し、警告を出す（VOICEVOX 前提のスクリプトがフォールバックしても動くようにするため）
- `speed`/`pitch` は edge では `rate="+20%"` / `pitch="+10Hz"` に写像される（`speed=1.2` → `+20%`、`pitch=0.1` → `+10Hz`）
- 出力は**どのバックエンドでも wav に統一**（edge の mp3 は ffmpeg で 24kHz/mono/pcm_s16le の wav に変換）。`scriptvedit.tts.tts_duration(wav)` で実長が取れる
- `scriptvedit.tts.speakers(backend="edge")` で各バックエンドの話者一覧を取得できる
- 合成 wav は `backend`+text+speaker+speed+pitch の sha256 を鍵に `__cache__/tts/` へキャッシュされる（**バックエンドを変えると別キャッシュ**。アトミック書き込み）
- `scriptvedit.tts` 本体は標準ライブラリのみで動作（`edge` バックエンド使用時のみ edge-tts が必要）
- CLI: `python -m scriptvedit.tts "こんにちは" --backend edge -o out.wav` / `--list-speakers --backend edge`

### ナレーション・カラオケ（narrate / karaoke）

TTS音声と字幕を1呼び出しで扱う統合機能。

```python
# narrate: TTSナレーション音声 + 同期字幕を1回で生成・配置（音声実長ぶんタイムラインが進む）
n = narrate("こんにちは、世界", speaker=3, subtitle_style={"size": 40, "y": 0.85})
# 戻り値 Narration(audio, subtitle)。audio, sub = narrate(...) も可
n @ 3.0  # 音声と字幕を一緒に同期配置
narrate("二行目のナレーション", speaker=1, subtitle=False)   # 字幕なし（音声のみ）
narrate(
    "読み上げる長い原稿です。画面表示は短くできます。",
    subtitle_text="画面表示は短く",
    subtitle_max_chars=14, subtitle_max_lines=2,
    subtitle_safe_area=(0.05, 0.08),
)

# karaoke: ASS \k タグのカラオケ風ハイライト字幕（.time(全体尺) で開始0配置）
sub = karaoke([
    (0.0, 2.0, "こんにちは世界"),
    (2.0, 4.5, "今日も良い天気ですね", [0.4, 0.3, 0.3, 0.5, 0.3, 0.3, 0.4, 0.3, 0.4, 0.2]),
], style={"primary": "yellow", "secondary": "white", "size": 44})
sub.time(5)
```

- `narrate(..., subtitle_text=None, subtitle_formatter=None, subtitle_max_chars=None, subtitle_max_lines=None, subtitle_safe_area=None)`: 読み上げ文と表示文を分離し、formatter→日本語禁則折り返しの順で整形する。行数超過は切り捨てずエラー。safe areaは数値、`(horizontal, vertical)`、`(left, top, right, bottom)`の画面比率で指定する
- 字幕窓は音声実長に一致し、音声と字幕は同じ開始時刻に配置される。x/y/size/color/font/box/... は text() と同じ字幕スタイル引数（既定は下部中央+半透明ボックス）。`backend`/`speaker` は `voice()` と同じ
- `karaoke(lines, *, style=None)`: `lines` は `(start, end, "歌詞")` または `(start, end, "歌詞", [語ごとの秒数])`。`word_durations` 省略時は行内の語へ `(end-start)` を均等割り。`style` で font/size/primary(発音済み色)/secondary(未発音色)/outline/alignment/margin_v 等を上書き。**フォント描画は libass 依存**（環境のフォント有無で見た目が変わる）

### ビート同期（beat_sync / scriptvedit.beat）

音声のビート（拍）を検出し、キーフレームやカット点に同期させる。ビート検出は librosa 非依存の `scriptvedit.beat`（numpy/scipy のみ）。

```python
# beat_sync: 音声からビート時刻を検出しDSLに統合
res = beat_sync("bgm.mp3", min_bpm=60, max_bpm=200)
# res = {"bpm": float, "beats": [秒,...], "onsets": [秒,...], "duration": float}

# 拍ごとに scale が跳ねて戻るキーフレーム（beats_to_keyframes → keyframes）
from scriptvedit.beat import beats_to_keyframes, snap_times
kf = beats_to_keyframes(res["beats"], [1.15], decay=0.12, base=1.0)
obj.time(dur) <= scale(keyframes(*kf))

# カット点を最近傍ビートへスナップ（snap_times）
cut_times = snap_times([2.0, 4.3, 6.1], res["beats"])
```

- `beat_sync(audio_source, *, min_bpm=60, max_bpm=200)`: 解析結果は 素材FFP+bpm範囲 をキーに JSON キャッシュ。**numpy/scipy が必要**（未導入時は導入手順付きの日本語エラー）
- `beats_to_keyframes(beats, values, *, offset=0.0, decay=None, base=None, t_start=None, t_end=None)` は `keyframes(*result)` に渡せるフラット列 `(t0, v0, t1, v1, ...)` を返すデータ整形ヘルパー（scriptvedit 非依存）。`decay` 指定で各拍がパルス形（跳ねてすぐ `base` に戻る）になる
- `snap_times(times, beats)` は任意の時刻列を最近傍ビートへ寄せる（カット点合わせ用）
- CLI: `python -m scriptvedit.beat song.mp3`（BPM+先頭20拍を表示）/ `--json`（全結果をJSON出力）

### スライド・絵コンテ・メタデータ（slide / storyboard / export_metadata）

```python
# slide: HTMLスライドをweb Object機構でキャプチャ（page指定で複数ページを1ファイルで切替）
s = slide("deck.html", page=1, duration=5.0)      # width/height省略時はProject解像度
s.time(5) <= move(x=0.5, y=0.5, anchor="center")

# storyboard: タイムラインの絵コンテ（サムネイル格子PNG）を1枚生成（Projectメソッド）
p.storyboard("board.png", cols=4, interval=None)  # interval省略時は 総尺/12
p.storyboard("board.png", source="out.mp4")       # 完成動画から高速生成

# export_metadata: YouTube投稿用メタデータ（章+タイトル+説明+タグ）を1ファイル出力（Projectメソッド）
p.export_metadata("meta.json", title="タイトル", tags=["tag1", "tag2"])   # .json=構造化データ
p.export_metadata("meta.txt")   # .txt=概要欄にそのまま貼れるプレーンテキスト
```

- `slide(html_file, page=None, *, duration=5.0, width=None, height=None, name=None, debug_frames=False, deps=None)`: `page` 指定時はキャプチャ前に `window.showSlide(page)` を実行、無ければ `id="page-<page>"` の要素のみ表示（他 `id^="page-"` を非表示）。`renderFrame` 未定義なら no-op を自動注入（静止スライド可）。キャッシュは web Object と同じ signature 方式
- `storyboard(out_path, *, cols=4, interval=None, source=None, timeout=600)`: 事前renderなしでもProjectグラフ準備1回・FFmpeg 1回で全コマを抽出する。`source`へ完成動画を渡すとProjectを再構築せず入力seekする。Pillowが必要
- `export_metadata(path=None, *, title=None, description=None, tags=None)`: `title` 省略時は `param("title")`、`path` 省略時は `metadata.json`。拡張子で .json（構造化）/ .txt（概要欄用）を切替。`marker()` で打った章が目次になる。`tags="foo"` は1個のタグとして扱う（複数はリスト）

### テスト・検証ツール（scriptvedit.testkit）

`scriptvedit.testkit` はレンダリング結果を SSIM で視覚検証するテスト用ユーティリティ。

```python
from scriptvedit import testkit

# assert_frame: 指定時刻のフレームが期待画像と一致(SSIM>=threshold)することを検証
score = testkit.assert_frame("out.mp4", at=2.5, expected="expected.png", threshold=0.97)

# assert_frames: 複数時刻を一括検証（全時刻を検証してから失敗をまとめて報告）
testkit.assert_frames("out.mp4", [(1.0, "f1.png"), (2.5, "f2.png")], threshold=0.95)

# 低レベルAPI: フレーム抽出 / SSIM / 差分統計
frame = testkit.extract_frame("out.mp4", 2.5, accurate=True)     # RGB numpy配列 (H,W,3)
s = testkit.ssim("a.png", "b.png")
d = testkit.frame_diff("a.png", "b.png", out_png="diff.png")     # mean_abs/max_abs/diff_ratio
```

- `assert_frame(video_path, at, expected, *, threshold=0.97, save_actual=None)`: 失敗時は実測SSIM+差分統計+ヒント付き AssertionError。`save_actual` で実フレームを保存
- `extract_frame(video_path, at, out_png=None, *, accurate=True)`: `accurate=True` は出力側シーク（start_time>0/VFRでも正確、やや低速）、False は入力側シーク（高速だが1フレームずれ得る）
- **依存は numpy + PIL + ffmpeg のみ**（scipy があれば SSIM窓に `uniform_filter` を利用、無ければ numpy フォールバック）
- CLI: `python -m scriptvedit.testkit compare a.png b.png` / `python -m scriptvedit.testkit frame video.mp4 2.5 -o out.png`

## Object メソッド一覧

| メソッド | シグネチャ | 説明 |
|---------|----------|------|
| `time` | `time(duration=None, *, name=None)` | 表示時間設定（動画/音声は省略で自動duration） |
| `until` | `until(name, offset=0.0)` | durationをアンカー時刻+offset秒まで伸長 |
| `show` | `show(duration, *, priority=None)` | current_timeを進めずに表示 |
| `show_until` | `show_until(name, offset=0.0, *, priority=None)` | current_timeを進めずにアンカーまで表示 |
| `compute` | `compute(duration=None)` | タイムライン外で素材生成（PNG or WebM） |
| `length` | `length()` | 加工後の再生時間を返す（trim/atempo反映） |
| `split` | `split()` | `(VideoView, AudioView)` を返す |

プロパティ: `has_video`, `has_audio`, `source`, `duration`, `start_time`, `priority`

## render の詳細

### render

```python
p.render(output_path, *, dry_run=False, timeout=None,
         start=None, end=None, draft=False, alpha=False, strict=False)
```

`start`/`end` は部分レンダの時間窓（秒。フィルタの t 基準を保持）。Web/Canvas Objectは
交差するフレームだけをscreenshotする。状態依存`renderFrame()`との互換性のため窓より前も
JavaScript/Canvasは順に評価するが、screenshotは省略する。`draft=True` は半解像度・軽量エンコードに加え、Web撮影を
`draft_web_fps`（既定8fps）以下へ落とす。`draft_web_fps=None`で本番同等にできる。
checkpoint/morph の中間キャッシュ鍵は**意図的に本番と共有**する（中間物の内容は
draft/本番で同一のため。分離すると draft⇄本番の切り替えで全キャッシュミスになる）。
`alpha=True` は対応形式（webm 等）で
透過付き出力にする。`strict=True` は `p.audit()` の warning が1件でもあれば
レンダ前に停止する（dry_run にも適用。CI・自動制作フロー向け）。
`timeout` は最終 ffmpeg 実行のタイムアウト秒数。既定の `None` は無制限で、長尺・
高負荷の本番レンダリングを途中で打ち切らない。実行時間を制限したい場合だけ
`timeout=3600` のように秒数を明示する。単一出力（mp4 等）は一時パスから原子的に
確定され、明示タイムアウトまたは Ctrl+C で中断した場合は書きかけだけが削除される。
同名の正常な完成品が既にあれば、中断時もそのファイルは保持される。

### dry_run（コマンド確認のみ）

```python
result = p.render("output.mp4", dry_run=True)
# ffmpegを実行せず、常に {"main": [...], "cache": {出力パス: [...]}} を返す
# main = 最終合成コマンド、cache = 事前生成される中間物のコマンド群（無ければ空dict）
```

`dry_run` 自体は数式 PNG・web webm・checkpoint 等のキャッシュ生成物を
作らない。未生成の素材は寸法不明になるため、pad による SEGV バリア等の
寸法依存経路は実レンダテスト（`tests/render_all.py`）でカバーする。一方、
実レンダで checkpoint が実体化すると、`dry_run` もそれを入力に再利用して
コマンドが変わり得る。実レンダ後は `python -m scriptvedit cache --clear` で
キャッシュを消してからスナップショットを実行する。

## 開発者向け情報

ライブラリを「使う」だけなら読まなくてよい。リポジトリ自体を触る人向け。

### ディレクトリ構成

本体は `src/scriptvedit/` の41モジュール（合計約18,600行）のパッケージ。

```
ScriptVEdit/
├── src/scriptvedit/     パッケージ本体（41モジュール）
│   ├── project.py       Project / render / チェックポイント
│   ├── parallel.py preview.py  時間分割並列レンダ / thumbnail・storyboard
│   ├── chapters.py params.py   マーカー・チャプター出力 / テンプレート変数
│   ├── objects.py       Object / Transform / Effect
│   ├── timeline.py      anchor / pause / scene / group
│   ├── effects/         basic / visual / composite / paths / time / terminal
│   ├── filters/         video / audio フィルタ生成
│   ├── expr.py easing.py  Expr式ビルダー・イージング
│   ├── cache.py ffmpeg.py media.py  キャッシュ鍵・ffmpeg実行・probe
│   ├── formula.py       数式レンダ（formula / formula_lines、KaTeX同梱）
│   ├── text.py audio.py web.py      テキスト / オーディオ / web Object・テンプレート
│   ├── morph.py         モーフィング・パーティクル生成（morph_to / explode_to）
│   ├── tts.py           音声合成（voice / narrate。VOICEVOX / edge-tts / SAPI）
│   ├── beat.py          ビート検出（beat_sync）
│   ├── viz.py           タイムライン検査・可視化（Project.inspect）
│   ├── testkit.py       SSIM によるレンダ結果の視覚検証
│   ├── plugins.py       プラグイン機構（@effect_plugin）
│   ├── manifest.py cli.py  describe（機械可読マニフェスト）/ CLI
│   ├── scaffold.py      プロジェクト雛形生成（scriptvedit new）
│   ├── assets.py        素材パス解決（asset / here / layer、共有ライブラリ取り込み）
│   └── templates/       テンプレートHTML + vendor/katex（同梱、CDN参照なし）
├── assets/              素材（images/ video/ audio/）
├── tests/               pytest（スナップショット/エラーケース/実レンダ等）
│   ├── layers/          レイヤー定義（testNN_*.py）とフィクスチャ
│   └── snapshots/       ffmpegコマンドのスナップショット
├── examples/basic/      最小サンプル
├── examples/showcase/   ショーケース動画
├── plugins/             サンプルプラグイン（cwd/plugins は自動読込）
└── scripts/             開発用スクリプト
```

### テスト

pytest で実行する（どのディレクトリからでも可。`cd tests` は不要）。

```
pytest tests/                             # 全テスト（スナップショット/エラーケース/素材解決/雛形生成/堅牢性/フォント解決。件数は pytest --collect-only 参照）
pytest tests/test_snapshot.py             # スナップショットのみ
pytest tests/test_errors.py -k plugin     # 名前で絞り込み
pytest tests/test_snapshot.py --snapshot-update   # スナップショット再生成
```

実レンダリング（dry_run では踏めない経路の検証）は既定で除外されており、明示的に有効化する。

```
pytest tests/test_real_render.py --realrender      # 選抜（CI と同じ）
pytest tests/test_real_render.py --realrender-all  # 全件（重い）
python tests/render_all.py                # 従来のランナー（全件）
python tests/render_all.py test01 test75  # 指定のみ
```

テストプロジェクトの定義は `tests/projects.py` が単一の正で、スナップショットと実レンダの両方がそこを参照する。

依存コマンド、edge-tts またはそのネットワーク、gitignore 対象の大容量素材が無い
環境では、対象テストだけを
`pytest.skip` にする（スキップを PASS 扱いにしない）。`test91` は数式 PNG の環境差が
下流の checkpoint 鍵に伝播するため、比較時だけそのハッシュ部分を正規化する。
保存するスナップショットは具体値のままで、数式パスやフィルタ文字列の差分は検出する。

ファイル指紋（キャッシュ鍵）は mtime ではなく**内容ハッシュ**で、同一バイト列の
素材なら clone 先でも安定する。改行変換による指紋ずれは `.gitattributes`（作業ツリーを
CRLF に固定、`templates/vendor/**` は無変換）で防いでいる。外部HTMLのWeb cacheは
LF/CRLFだけの違いをCRLFへ正規化してハッシュするため、改行変更だけでは再生成しない。

スナップショットを再生成したら、`scripts/tools_baseline.py` で「パス以外は変わっていない」ことを検証できる。

```
python scripts/tools_baseline.py verify baseline_snapshots.json
```

## ライセンス

[MIT License](LICENSE)。同梱の `assets/` はすべて自作のテスト用素材で（`scripts/generate_test_assets.py` が生成）、コードと同じく MIT が適用されます（[ASSETS.md](ASSETS.md)）。`src/scriptvedit/templates/vendor/katex/` の KaTeX のみ同ディレクトリのライセンスに従います。

## 作者・AI利用について

作者: 小嶋 明（[kojima8924](https://github.com/kojima8924)） / ポートフォリオ: <https://kojima8924.github.io/>

DSL の記法、素材キャッシュ、要素配置のアンカー解決、区間ごとの並列レンダリングといった仕組みは
AI と相談しながらほぼ本人が決めた。一方で、それらを含む実装全般・テストケースの生成・
スクリーンショットによる出力確認は AI エージェントへ委任している。
エフェクトの品質と、テーマだけ与えて AI に作らせた動画の出来は本人が目視で評価した。

## ロードマップ

ScriptVEdit は「Python DSL として書いていて楽しく、かつコーディングAIが駆動しやすい動画エディタ」を目指している。

### 特徴的な実装済み機能
- **プラグイン機構**: `@effect_plugin` で、コアを編集せず `plugins/*.py` に新エフェクトを登録（→「プラグイン機構」節）。
- **ケイパビリティ・マニフェスト**: `python -m scriptvedit describe` で全機能のシグネチャ・引数レンジ・bakeable/live 区分を JSON / Markdown 出力（→「ケイパビリティ・マニフェスト」節）。
- **数式レンダリング**: `formula(r"...")` / `formula_lines([...])`（KaTeX 同梱・完全オフライン、透過PNG）（→「数式レンダリング」節）。
- **タイムラインDSL糖衣**: 時間スライス `obj[2:5]`（素材切り出し）/ `obj @ 12`（絶対配置）/
  `a >> b`（直後連結）/ `clip * 3`（リピート）/ `-clip`（逆再生）
  （→「タイムライン演算子」節）。

### 今後の方向

#### AI駆動
- **JSON中間表現**: Python DSL ⇄ JSON プロジェクトの双方向変換。AIは構造化データを、人はDSLを扱う。
- **構造化エラー**: 例外に機械可読な原因・修正候補を持たせ、AIがレンダ→失敗→自動修復のループを回せるようにする。

#### 教育・解説動画向けの表現力
- **キャラクター立ち絵の口パク/まばたき** `character(sprite, voice)`: TTSナレーションに同期。
- **数学ダイアグラム拡張**: 数直線・関数グラフ・格子・幾何作図・木構造、証明の逐次リビール（`formula` と地続き）。

#### DSLの遊び（未実装の糖衣）
- 単一インデックス `obj[1.5]`（freeze）/ 逆順スライス `obj[::-1]`（reverse。現状 step は明示エラー）。
- 単位リテラル `3*s` / `500*ms` / `2*beats`（`scriptvedit.beat` 連携）。

#### 配布・エコシステム
- pip 公開、docsサイト、プラグインエコシステム（プラグイン機構と地続き）。