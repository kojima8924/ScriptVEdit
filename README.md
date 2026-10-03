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
- **図解アニメ** — 正規表現の照合の後戻り（`regex_view`）・番号つきの箱の列（`slots`）・文字列の組み替え（`text_transition` / `odometer`）・
  点と線の上を流れるパケット（`flow_graph` / `flow_tree`）・点で描いた地球（`globe`）を、コマごとに Python で描いた透過動画の
  Object として1行で置ける。共通部品 `scriptvedit.framekit` で自作の図も描ける（→「図を描く部品（framekit）」節）

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
- numpy + opencv-python + Pillow（図解アニメ `regex_view` / `slots` / `text_transition` / `odometer` /
  `flow_graph` / `flow_tree` / `globe` と、その部品 `scriptvedit.framekit`。`pip install "scriptvedit[figures]"`）
- numpy + scipy + opencv-python + Pillow + tqdm（`morph_to` / `explode_to` / `assemble_from` / `fly_to`。`pip install "scriptvedit[morph]"`）
- Pillow（`storyboard` 使用時）
- TTS（`voice` / `narrate` 使用時。`scriptvedit.tts` 経由。いずれか1つ）
  - VOICEVOX エンジン（`backend="voicevox"`。オフライン・キャラボイス。別途起動が必要）
  - edge-tts（`backend="edge"`。`pip install edge-tts` または `pip install scriptvedit[tts]`。導入が楽だがオンライン必須）
  - Windows 標準音声（`backend="sapi"`。追加導入不要・オフライン。Windows 専用）

## インストール

```
git clone https://github.com/kojima8924/ScriptVEdit.git && cd ScriptVEdit
pip install -e .            # コアは標準ライブラリのみ
pip install -e .[all]       # morph / figures / web / beat / tts(edge-tts) / tools の全機能
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

- `scriptvedit new myvideo --template explainer` … 解説動画向けの雛形（数式・字幕・BGM 入り）。
  数式に `formula()` を使うため、レンダには **Playwright + Chromium** が要る
  （`pip install "scriptvedit[web]" && playwright install chromium`）。
  既定の minimal 雛形は追加導入なしでそのままレンダできる
- `scriptvedit new myvideo --force` … 生成先が空でなくても生成する

生成される雛形は、そのまま `p.audit()` の warning がゼロになるよう作ってある
（文字には縁取りが付き、文字サイズと縁取り幅は `--width` / `--height` に比例する。基準は 1280x720）。
explainer 雛形はレイヤーをまたぐ時間合わせを `time(name=...)` + `pause.until("名前.end")` で
書いており、**カーソルがレイヤーごとに0秒へ戻る**ことを前提にした正しい書き方の手本になっている。

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

- `Object(...)` … 素材1つ。レイヤー内で作るだけで自動的に登録される（リストに追加する操作は不要）。
  順次配置の順番は**作った順**で決まる（→「レイヤーの独立タイムライン」節）
- `bg.time(6)` … この素材を**6秒間**表示し、タイムラインを6秒進める
- `<=` … 適用。`&` … 複数の Effect をひとまとめにする
- `lambda u: ...` … アニメーション。`u` は表示開始で 0、表示終了で 1 になる**進行度**。
  `fade(lambda u: u)` なら「透明→不透明」、`scale(lambda u: lerp(1.5, 1, u))` なら「1.5倍→等倍」
- `move(x=0.5, y=0.5)` … 位置は画面比率（0〜1）。(0.5, 0.5) は画面中央

レイヤーを重ねる順序・素材の時間割りは main.py、見た目の演出はレイヤー、と役割が
分かれているので、動画が大きくなってもファイルは短いまま保てる。

### 3. 次の一歩

```python
p.render("out.mp4", dry_run=True)     # ffmpeg を実行せずコマンドだけ確認（タイムラインもここで解決される）
p.inspect("timeline.html")            # 配置をガントチャートで確認（dry_run の後に呼ぶ）
p.audit()                             # 品質チェック（文字が小さい等の警告）
```

`p.inspect()` 自身はレイヤーを実行しない。`render()`（`dry_run=True` でよい）か
`p.audit()` より前に呼ぶと、ガントチャートではなく `p.layer()` の登録情報だけの表になる。

```
python -m scriptvedit describe --format md   # 全機能のカタログ（41 Effect / 102 Expr）
python -m scriptvedit watch main.py          # ファイル変更を監視して自動再レンダ
```

新しいプロジェクトの雛形は `python -m scriptvedit new <path>` で作れる（生成直後に
`python main.py` でレンダできる最小構成）。

動画・音声素材は `time()` の引数を省略すると素材の長さがそのまま表示尺になる:

```python
clip.time() <= trim(3)                # duration=3（加工後の長さ）
bgm.time() <= atrim(2) & avolume(0.6)   # duration=2
img.time()                            # TypeError（画像は「素材の長さ」を持たない）
```

このため `loop()` と組み合わせると尺が素材の長さで確定してしまい、ループしない
（`bgm.time() <= loop()` は1回再生で終わる。→「オーディオ（拡張）」節）。

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
| `-効果` | キャッシュ対象から除外（Object に付けると下の `-obj`＝逆再生） | 〃 |
| `obj[2:5]` | 素材の 2〜5 秒を切り出し（**素材時間**） | タイムライン演算子 |
| `obj @ 12` | タイムライン 12 秒の位置に配置（**タイムライン時間**・非進行） | 〃 |
| `a >> b` | b を a の終了直後に開始（同じレイヤーの中だけ） | 〃 |
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
- `must_exist=False` は存在チェックをスキップし、コピーもしない（`<project>/assets/<relpath>` を返す）。**共有ライブラリの探索も行わない**ので、「素材があれば使う」という分岐には**使ってはいけない**（共有ライブラリにしか無い素材が永久に見つからない）。その用途は既定で呼んで `FileNotFoundError` を捕まえる:

  ```python
  try:
      bgm = Object(asset("audio/bgm.mp3"))
  except FileNotFoundError:
      bgm = None   # BGM 無しで続行
  ```

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
- `-` (マイナス) ... policy="off"（キャッシュ対象から除外）。**被演算子で意味が変わる**:
  Transform / Effect（とそのチェーン）に付けると policy="off"、Object に付けた `-obj` は
  逆再生（→「タイムライン演算子」節）
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
  先行アイテムの尺は `time()`・スライス・`until()` のいずれかで確定している必要がある。
  a は変数で参照するので**同じレイヤーの中だけ**で使える（別レイヤーの後ろへ置くなら
  `time(name=...)` + `pause.until("名前.end")` か `@ "名前.end"`）
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
  - `flip(horizontal=None, vertical=False)` ... 反転（左右=hflip / 上下=vflip）。`flip()` は左右、`flip(vertical=True)` は上下だけ、両方は `flip(horizontal=True, vertical=True)`（`horizontal` を省略すると「`vertical` を指定していなければ左右反転」）

- **Effect** (`&` で連結、`<=` で適用): float で定数、lambda(u) でアニメーション
  - `move(x, y, anchor)` ... 配置位置（固定 or from/toアニメーション）。
    `anchor` は素材のどこを (x, y) に合わせるかの基準点で、
    `center`（既定）/ `topleft` / `left` / `right` / `top` / `bottom`。
    未知の値・綴り誤りは ValueError（黙って topleft にずらさない）
  - `scale(0.5)` ... 定数0.5倍
  - `scale(lambda u: lerp(0.5, 1, u))` ... 0.5倍 → 等倍にアニメーション
  - `fade(0.5)` ... 定数 半透明
  - `fade(lambda u: u)` ... 透明 → 不透明にアニメーション
  - `zoom(to_value=2)` ... ズーム（scale のエイリアス、from/to指定可）
  - `rotate_to(from_deg, to_deg)` ... 回転アニメーション（bakeable）
  - `wipe(direction)` ... ワイプ表示（"left"/"right"/"up"/"down"。"top"/"bottom" は up/down の別名）
  - `color_shift(hue, saturation, brightness)` ... 色相/彩度/明度シフト
  - `tint(color, amount=1.0, *, mode="multiply")` ... 色の塗り替え（アルファは変えない）。白い線画を任意の色にする。`"multiply"` は元の色に `color` を掛ける（白 → color、黒は黒のまま、濃淡は保たれる。`tint("black", 0.5)` で暗くする）、`"fill"` は元の色に関係なく `color` へ寄せる（黒い線画も塗れる）。`amount` は 0〜1 で Expr/lambda 可（定数は lutrgb、式は geq）
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
| Transform | resize, rotate, crop, pad, blur, eq, flip（と `obj.grid()` が足す grid） | bakeable | 全Transform は bakeable |
| Effect | scale (zoom), fade, rotate_to (look_at), wipe, color_shift | bakeable | zoom は scale、look_at は rotate_to の別名 |
| Effect | trim | bakeable | 時間影響あり（ベイク尺に反映される唯一の例外） |
| Effect | chroma_key, vignette, pixelize, glow, lut, glitch, perspective_warp, lens, ken_burns, drop_shadow, outline | bakeable | 「映像エフェクト」節 |
| Effect | mask, mask_wipe, opacity, rounded, tint | bakeable | 「合成・コンポジション」節（tint は色の塗り替え） |
| Effect | morph_to, explode_to, assemble_from, fly_to | bakeable | 終端フレーム生成。bakeable ops の末尾に1つだけ置ける |
| Effect | move（move_along / path_bezier / throw / inertia も内部は move） | live | overlay座標で解釈 |
| Effect | shake | live | overlay座標にsin/cosオフセット加算 |
| Effect | delete | live | overlay除外 |
| Effect | speed, reverse, freeze_frame, repeat（`obj * n`） | live | 時間軸を変える（「時間操作」節） |
| Effect | blend_mode, blur_background_fill | live | 合成経路の切り替え / キャンバス固定 |

bakeable な Effect の正は `src/scriptvedit/state.py` の `_BAKEABLE_EFFECTS`（26種）で、
Transform は全て bakeable。`describe` の各エントリの `bakeable` でも確認できる。
`pip()` は scale → rounded → outline → drop_shadow → move の組を返すプリセットなので、
配置（move）の部分は live のまま残る。

**重要**: live Effect は checkpoint で焼かれないため、checkpoint 生成後もレンダリング時に必ず残る。

morph_to のモーフ方式（`method`）:
- `method="sdf"`（**既定**）… 形状ベース（符号付き距離場）。中間形状が常に滑らかな
  1つのシルエットになり、位置・サイズがずれた素材や文字グリフでも破綻しない。
  追加パラメータ: `align` / `fit` / `edge_softness` / `color_ease` / `color_path`
  - `fit`（既定 `None` = 自動）: 2枚の不透明部の外接矩形（位置と大きさ）を合わせながら補間する。
    距離場の補間は「相手の形から遠い部分ほど早く消え、遅く現れる」ので、幅の違う文字列どうしでは
    端の文字が動き出した直後に欠ける。自動では、幅か高さが 1.15 倍を超えて違う組だけ合わせる
    （同じ大きさの組は従来どおりその場で溶けて入れ替わる）。`fit=False` で常に合わせない
  - 整列（`align` / `fit`）で絵が動く分だけ、キャンバスに透明の余白を足す（端が切れない。
    余白は左右・上下で対称なので絵の位置は変わらない）
  - **輪郭（アルファ）で形を補間する**ので、背景が透明な画像が前提。全面不透明・全体が半透明の
    素材や、2枚の不透明部が重ならない組（`align=False` で離れた位置にある等）は、形が動かず
    クロスフェードになる。生成時に警告し、`p.audit()` は `morph-sdf-crossfade`（warning）を出す
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
- `delay=秒` / `duration=秒`（`explode_to` / `assemble_from` と共通）: `a.time(3) <= morph_to(b, delay=0.5, duration=1.5)` は
  「0.5 秒待ち、1.5 秒で変形し、残りは b を保持」。待つ間と終わった後は最初・最後のコマを複製するだけで、
  その分のコマは生成しない。前後に同じ絵の静止画 Object を別に置かなくてよい。`delay + duration` が
  Object の尺を超えると ValueError。動く区間は最低2コマ（元の絵と到達点）を作るので、`duration` が
  1コマ以下でも・`delay` が尺の終わりぎりぎりでも、最後は必ず到達点の絵になる（その場合は次のコマで切り替わる）
- **余白と `move` の anchor**: sdf の整列の余白・粒子の `expand` は左右・上下それぞれ対称に付き、
  `anchor` は余白を除いた元の絵の箱（`morph_to` は2枚を中央で重ねた共通キャンバス）を基準にする。
  `topleft` / `left` / `right` / `top` / `bottom` でも、静止画として置いたときと同じ位置に映る

shake は overlay 座標の変調として実装されており live 分類。将来 bakeable に変更する場合は ENGINE_VER 更新が必要。

### 音声エフェクト

動画・音声ファイルの音声トラックを制御する。AudioEffect 同士は `&` で連結できる。
**Effect（映像）と AudioEffect（音声）は `&` で混ぜられない**（`fade(...) & avolume(0.6)` は
TypeError）ので、別々の `<=` で適用する。`~` は品質ヒントで、
軽い代替を持たない AudioEffect では通常と同じ処理をする。音声削除は `adelete()`。

- `avolume(value)` ... 音量倍率（デフォルト 1.0。lambda(u) でフェードも書ける）
- `atrim(duration, *, start=0)` ... 音声を切り出す（時間影響あり。`start` はイン点）
- `atempo(rate)` ... テンポ変更（時間影響あり）
- `adelete()` ... 音声をミックスから除外

```python
clip = Object("video.mp4")
clip.time(5) <= move(x=0.5, y=0.5, anchor="center") & fade(lambda u: u)   # 映像（Effect）
clip <= avolume(0.6) & atrim(3)                                            # 音声（AudioEffect）
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
  - `ease_spring(stiffness=3, damping=4)` ... バネ（途中でオーバーシュートしつつ、`t=0→0.0` / `t=1→1.0` を厳密に満たす。終端は正規化済みなので alpha や scale の最終値が規定を超えない）
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
最低2点・最大128点で、時刻順に自動ソートされる。`easing=` で各区間の補間カーブを指定できる。

**時刻は秒ではなく u（0..1。表示区間の進行度）**。`obj.time(4)` なら u=0.5 は表示開始から2秒後。
秒で考えたいときは表示尺で割って渡す（範囲外の時刻は端の値で止まるだけでエラーにならないので、
秒のまま渡すと u=1 より後のキーは黙って届かない）。

```python
# フラット形式: 0.5倍 → 1.2倍 → 等倍
obj.time(4) <= scale(keyframes(0, 0.5, 0.5, 1.2, 1.0, 1.0))

# タプル形式 + easing指定
obj.time(4) <= fade(keyframes((0, 0), (0.2, 1), (0.8, 1), (1.0, 0)))
obj.time(4) <= scale(keyframes((0, 0.5), (1, 1.5), easing=ease_in_out_quad))
```

### 秒で書く（ramp / keyframes_sec / elapsed / remaining）

u（0..1）で書くと、表示区間の一部でだけ動かすたびに「秒 ÷ 表示秒」を手で書くことになり、
表示秒を変えると式も全部変わる。次の4つは **Object の表示開始からの秒**で書け、表示秒を変えても動く時刻が変わらない。
どれも Expr を返すので、Effect の引数にそのまま渡しても、lambda の中の式に混ぜてもよい。

- `ramp(a, b, easing=None, *, from_end=False)` ... a 秒から b 秒の間に 0→1（前は 0、後は 1）。`from_end=True` なら a, b を「表示終了の何秒前か」で数える（`ramp(0.5, 0, from_end=True)` は最後の 0.5 秒で 0→1）
- `keyframes_sec(*args, easing=None)` ... `keyframes` の時刻を秒にしたもの（最低2点・最大128点）
- `elapsed()` ... 表示開始からの経過秒（0 〜 表示秒）
- `remaining()` ... 表示終了までの残り秒

```python
obj.time(6) <= fade(ramp(0, 0.4) * (1 - ramp(0.4, 0, from_end=True)))   # 0.4 秒で現れ、最後の 0.4 秒で消える
obj.time(6) <= wipe("left", progress=ramp(1.2, 1.6, ease_out_cubic))    # 1.2〜1.6 秒で拭って現れる
obj.time(6) <= scale(lambda u: lerp(1.0, 1.3, ramp(2, 3)))              # 2〜3 秒で 1.3 倍へ
obj.time(6) <= fade(keyframes_sec((0, 0), (0.25, 1), (5.5, 1), (6, 0)))
bgm.time(30) <= avolume(lambda u: clip(remaining() / 2, 0, 1))          # 最後の 2 秒でフェードアウト
```

秒が確定するのは Object の尺が決まった後（フィルタ生成時）で、式には `clip(t-開始, 0, 表示秒)` がそのまま入る。
表示秒が決まる前の数値評価（`Expr.plot()` / `eval_at()`）はできない（ValueError）。
時間変化しない Transform（`rotate()` など）には渡せない。

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

bakeable ops（全 Transform と bakeable Effect。→「Effect分類（bakeable / live）」節）の中間結果を自動保存・復元する仕組み。
signatureベースでキャッシュの安全性を保証。保存点はRAA+FSPで最小化。

**policy（キャッシュ制御）:**
- `auto`（無印） ... キャッシュが存在すれば再利用、なければ生成。最右のbakeable opがRAA保存点
- `force`（`+`） ... 常に再生成。FSP保存点
- `off`（`-`） ... キャッシュ対象から除外

**表示の窓と境目の1枚:** Object が映るのは「開始〜開始+尺」の**閉区間**で、尺がフレームの整数倍なら
「開始 + 尺」ちょうどのコマも窓に入る（30fps の `time(1)` は 31 枚）。焼いた Effect（チェックポイント）の
動画もこの最後の1枚まで映る（以前は1枚短く、その1枚だけ背景が見えた。隠すために同じ絵の静止画を
敷く必要はもう無い）。そのため **`time()` で順に並べた境目の1枚には、前の Object と次の Object の
両方が映る**（焼かない Effect・静止画は以前からこの挙動。後の Object が上に重なる）。
後の絵が前の絵を覆わない並び（小さい絵・透過のある絵が続く）で前の絵を1枚も残したくないときは、
前の Object の尺を1フレーム縮める（`time(d - 1/fps)`）。

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
  - `auto` ... 古いキャッシュを使わずレイヤーを再実行する。**キャッシュの再生成はしない**（生成するのは `make` だけ）
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
（同一レイヤーファイルの再実行は許容）。**同じレイヤーの中**で同名アンカーを
2回定義する（`anchor('x')` の2回、`time(name='x')` の2回、`anchor('x.start')` と
`time(name='x')` の混在、同名 `scene()` の2回）と、定義した時点で
「アンカー名 'x' はこのレイヤーで既に定義されています（3行目: …）」の ValueError になる。
`anchor('x')` と `time(name='x')`（`x.start` / `x.end`）はキーが違うので共存できる。

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
clip.compute(duration=3)  # 動画として生成（FFV1 bgra の .mkv。可逆・透過あり）
```

生成物は `duration` なしなら PNG、ありなら FFV1 `bgra` の `.mkv`（checkpoint と同じ中間ベイク形式。
→「チェックポイントキャッシュ」節の「中間ベイクのピクセル形式」）で、
`__cache__/artifacts/compute/` に置かれる。

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

### 開始時刻の精度（tpad とタイムベース）

開始が 0 秒より後の映像素材は、`tpad` で先頭のフレームを複製して開始位置まで埋めてから重ねる。
`tpad` は複製1枚ごとの長さを**入力のタイムベースへ丸めて**積み上げるため、そのままだと
Matroska / WebM（タイムベース 1/1000）の素材は 30fps の 1/30 秒が 33ms に丸まり、
中身が開始時刻の約1%早く届く（60秒開始で0.6秒、600秒開始で6秒）。checkpoint・compute・
web・from_project・レイヤーキャッシュの生成物はすべてこの形なので、長尺の後半ほど大きくずれていた。

現在は `tpad` の直前で `settb=1/120000` に揃えている（24 / 25 / 30 / 48 / 50 / 60 / 120fps と、
NTSC の 24000/1001・30000/1001・60000/1001 の1フレームが整数 tick になる）。Project の fps が
割り切れない 90 / 144fps や、`fps=29.97` のように小数で書いた fps では、分母を fps との
最小公倍数へ広げる（90fps → `settb=1/360000`）。本レンダ・レイヤーキャッシュ・時間分割並列レンダの
どの経路でも、長尺の後半に置いた素材が1フレーム未満の精度で出る。
値は fps だけで決まる（素材を probe しない）ので、dry_run と実レンダで同じコマンドになる。
テキスト（drawtext）の入力はタイムベースが 1/fps で丸めが起きないため対象外で、画像入力には `tpad` 自体が無い。

### レイヤーの独立タイムライン

各レイヤーは0秒から独立したタイムラインを持つ。
動画全体のdurationは全レイヤーの最大値から自動算出される。

**順次配置のカーソルはレイヤーごとに0秒へ戻る。** 同じレイヤー内の `a.time(3); b.time(3)`
は 0-3秒 / 3-6秒 と順に並ぶが、**別レイヤーの先頭はまた0秒から始まる**
（レイヤー内は順次・レイヤー間は並行）。別レイヤーのものを後ろに置きたいときは:

| 手段 | 使いどころ |
|---|---|
| `pause.time(6)` | レイヤー先頭を固定秒だけ空ける |
| `obj @ 6` / `obj @ "intro.end"` | タイムラインの絶対時刻（またはアンカー）へ置く |
| `time(name="intro")` + `pause.until("intro.end")` | **別レイヤーの尺に自動追従させる**（推奨） |

アンカー方式（`pause.until` / `@ "intro.end"`）だけが、先行レイヤーの尺を変えたときに後続が自動で追従する。
`a >> b` は**同じレイヤーの中でしか使えない**（先行の Object を変数で参照する必要があるが、
レイヤー .py はそれぞれ別の名前空間で実行されるため、別レイヤーの Object は見えない）。
`describe` の constraints にも `layer_timeline_independent` として載っている。

**Object は作った時点のカーソル位置で登録される。** 順次配置の順番は `Object(...)` / `text(...)` 等を
**呼んだ順**で決まり、後から `time()` / `show()` を呼んだ順ではない。たとえば
「音声 `v` を作って `v.time(4)` → 字幕 `t = text(...)` を作って `t.show(4)`」と書くと、
`v.time(4)` がカーソルを進めた後に `t` が作られるので、字幕は音声の**後ろ**（4秒から）に出る。
音声と字幕を同時に出したいときは、**字幕の `text()` を先に作ってから音声 Object を作る**
（または `narrate()` を使う。音声と字幕を同じ開始時刻に置く）。

### priority による z-order 制御

`p.layer(filename, priority=N)` の `priority` で重ね順を制御する。
値が大きいほど手前に表示。個別の Object は `show(..., priority=N)` / `show_until(..., priority=N)` で上書きできる。
**priority が同じときは登録順**で、後に登録したものが上に重なる（`p.layer()` を呼んだ順、
同じレイヤーの中では Object を作った順）。

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

### 絵の列を1本の動画にする（stills / frames）

字幕ページや図のように「全面の絵を、ばらばらの尺で順に出す」ときは、1枚ずつ `Object` にしない。
どの入力も 0 秒から流れて出番まで捨てられるので、レンダの手間が「枚数 × 尺」に比例し
（実測: 全面 PNG 100枚×6秒で ffmpeg のメモリ 31GB）、数百枚ではコマンド長が Windows の上限を超える。
`stills()` / `frames()` は絵の列を先に1本の動画へまとめ、**入力1本の Object を1個**返す
（同じ 100枚×6秒が 68 秒・メモリ 1.2GB・中間ファイル 4.6MB）。

```python
# stills: 時刻表つきの静止画列。形1 = (画像, 表示秒)
pages = stills([("p1.png", 6.4), ("p2.png", 7.1), ("p3.png", 5.0)])
pages.time(20)                      # 総尺 18.5 秒より長い分は、最後の絵が残る
v2 = Object("v2.wav")
v2 @ pages.starts[1]                # 2枚目の開始（フレーム格子上の秒）に音声を合わせる

# 形2 = (画像, 開始秒) + total（総尺）。音声の時刻表をそのまま渡すとき
stills([("a.png", 0), ("b.png", 3.2), ("c.png", 9)], total=12).time()

# frames: コマを描く関数から動画を作る（PIL.Image か RGBA の numpy 配列を返す）
def draw(i):                        # i は 0 始まりのコマ番号（時刻は i / fps 秒）
    im = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle([100, 500, 100 + i * 20, 560], fill="white")
    return im
bar = frames(draw, duration=2.0, key=["bar", 1])   # または frames(draw, 60, key=...)
bar.time(5)                         # 2 秒より後は最後のコマが残る
```

- **切り替わりの丸め**: 境目の時刻（形1 は表示秒の累積、形2 は開始秒）を最も近いフレームへ丸める（半分ちょうどは切り上げ）。1枚ずつ丸めて足さないので誤差は積もらない。結果は `obj.starts`（各絵の開始秒）/ `obj.frame_counts`（各絵のフレーム数）/ `obj.length()`（総尺）で読める。**音声は自分で秒を足し上げず `obj.starts[i]` に合わせる**（列を `@ t` で置いたら `t + obj.starts[i]`）
- **最後の絵の保持**: `time(秒)` で素材より長く表示すると、最後の絵（コマ）が残る（普通の動画は素材が終わると背景が見える）。`fade` などを掛けても、伸ばした区間で Effect は進む。引数なしの `time()` は総尺ぶん
- **alpha を保つ**: 透過 PNG はそのまま下のレイヤーが透ける。音声は無い
- `stills` の画像は**全部同じ寸法・同じ形式**であること（違えば `ValueError`。PNG と JPEG は混ぜられない）。同じ形式の中での違い（RGB の PNG と RGBA の PNG、パレット、グレースケール）は混ぜてよい。`size=(w, h)` は列全体の出力寸法（縦横比を保って収め、余白は透明）。省略時は画像の寸法そのまま
- **`frames` の `key` は必須**で、`draw` のコードは鍵に入らない。同じ `key`（とコマ数・fps・size）なら `draw` を呼ばずに前回の動画を使う。**描き方や元データを変えたら `key` を変える**（版番号や元データを `key` に入れる。文字列か JSON にできる値）。`size` 省略時は Project の解像度で、`draw` はその寸法で描く。`draw` は実レンダでキャッシュが無いときだけ呼ばれる（dry_run では呼ばれない）
- 生成物は `__cache__/artifacts/stills/<鍵>.mov` / `frames/<鍵>.mov`（可逆の QuickTime Animation（qtrle）・alpha つき）。前のコマと同じ画素は書かないので、同じ絵が続く区間はほぼ 0 バイト（文字ページ 20枚×6秒 = 1.0MB。FFV1 だと 353MB）。写真のように圧縮の効かない絵は1枚あたり 6〜8MB（1080p）になるが、枚数に比例するだけで尺には比例しない。`stills` の鍵は各画像の内容指紋・各絵のフレーム数・fps・size（パスは入らない。`__cache__` の下に自分で書き出した画像も内容で見る）。生成した動画はコマ数を確かめてからキャッシュへ確定する（`draw` が例外を出した・途中の画像を読めなかった場合は何も残さない）

#### 図を描く部品（framekit）

図解アニメの関数（`regex_view` ほか）が共通で使う内部モジュール `scriptvedit.framekit`（`from scriptvedit import *` には入らない）。
レイヤーで `import scriptvedit.framekit as fk` すれば、自作の図にも使える。numpy・opencv-python・Pillow が要る（`pip install "scriptvedit[figures]"`）。

```python
import scriptvedit.framekit as fk
pal = fk.palette("myfig")                          # 白・灰・赤（fg / accent / muted / line / dim / panel）
title = fk.label("myfig", "後戻り", size=64, border=4)  # text_image と同じ書式・同じ画素の Sprite
ease, ease_key = fk.easing("myfig", "ease_out_cubic")
n = fk.n_frames_for(3.0, 30)
static = fk.layer_cache()                          # 動かない層は1回だけ描く

def draw(i):
    d = static(0, lambda: fk.blit(fk.canvas(1920, 1080), title, 120, 80)).copy()
    u = ease(i / (n - 1))
    fk.arrow(d, (200, 500), (200 + 1400 * u, 500), pal["accent"], 4, head=20, curve=0.1)
    fk.dots(d, [(960 + 0.4 * i, 700)], pal["fg"], 3)      # 端数位置でも滑らかに動く
    return d

fig = fk.build("myfig", kind="myfig", ver=1, params={"ease": ease_key}, draw=draw,
               n_frames=n, size=(1920, 1080), fonts=[title],   # Sprite の文字・書式も鍵に入る
               text=fk.text_meta([title], width=1920, height=1080))
fig.time(5)                                        # 3 秒より後は最後のコマが残る
```

- 鍵は `['fig', kind, ver, framekit の版, params, size, フォントの内容指紋, Sprite の署名, files の内容指紋, PIL の版]`。`draw` のコードは入らないので、**描き方を変えたら `ver` を上げる**。図に描く文字は **`fonts=` に Sprite（`label` の戻り値）を渡す**（文字・書式・色・縁取りの署名が鍵に入る）か、`params` に入れる。`draw` の中で作る文字や `fonts=` に渡さない文字を直しても、鍵が変わらず古い図が使われる
- `tests/framekit_golden.py` の金型テストが見張るのは本体の図（framekit と図解ファクトリ）の版定数で、`--golden-update` でも版を上げずに絵だけ変えたものは書き換えずに失敗させる。レイヤーで自作する図の `ver` は見張れないので、描き方を変えたら自分で上げる
- 生成物の .mov はフォント・`files=` と一緒にレイヤーの依存に載るので、`params` が環境変数や import したデータから来ても、`cache='auto'` のレイヤーキャッシュは鍵が変われば作り直す
- 座標は SVG と同じ連続座標（画素 (i, j) の中心は (i+0.5, j+0.5)）。線・円・多角形は縁までの距離と向きから画素の面積の被覆率を求めるので、端数位置・端数の幅でも幅どおりに描け、まっすぐな縁はどの角度でも α の量が揺れない（45° の線を 0.05px ずつ動かして 0.02%）。円・丸い端は半径 2px 以上で揺れ 0.4% 以下（もっと小さい点は `dots`）。`dots` は端数位相のスタンプを混ぜて α の量を保ったまま連続に動く。`blit` の縮小はどの倍率でも面積の平均で縮めてから置くので、ズームアウトの途中で急にぼけない
- `dash=(0, 隙間)` は点線（丸い端なら点・`cap="square"` なら正方形。SVG と同じ）
- 合成の前の絵だけが欲しいとき（何チャンネルでもよい）は `fk.warp_patch(src, x, y, scale=, angle=, pivot=)` が `blit` と同じ変換で写した `(x0, y0, 絵)` を返す（`text_transition` の字もこれで写す）
- 時刻は `fk.sec_frame(秒, fps)`（stills と同じ丸め）、拍の並びは `fk.pace(n, first=, ratio=, total=)`（だんだん速くなる数え上げ）

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
- `opacity(value)` / `fade(alpha)` は定数（0〜1）だと colorchannelmixer。時間だけで決まる Expr/lambda（`u`・`elapsed()`・`ramp()`・`keyframes_sec()` 等）はコマごとに1回だけ評価して colorchannelmixer で掛けるので、点の多い keyframes でも速い（1080p・10 秒の書き出しが 8 / 56 / 128 点で 4.1 / 4.3 / 4.3 秒。式はコマごとに解析し直すので、点が多いほど 1 コマあたり少し（128 点で約 0.5ms）増える）。`fade` の入りと出だけの単純なランプは native の fade、`random()` 等の画素ごとに変わる式だけ geq になる（どちらも bakeable）
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
seq <= move(x=0.5, y=0.5, anchor="center")     # 合成尺は自動で入る（time() は不要。seq.time() と書いてもよい）
```

- `speed(factor)` は 0.01〜100。音声付き動画には対応する `atempo` が自動適用される（有効範囲0.5〜100を超える場合は多段に自動分解）
- `reverse()` は全フレームをメモリ保持するため、**実効尺が30秒を超える素材には使用不可**（明示エラー。`trim()` で短縮してから適用）。音声は反転されない
- `freeze_frame(at, duration)` の `at` は実効尺未満（**境界以上は拒否**）。音声は変化しない
- `video_sequence(*objs, transition="fade", t_dur=0.5)` は2つ以上の動画Object/パスを連結。合成尺は `sum(実長) - t_dur*(n-1)` 秒、`t_dur` は最短クリップ未満。返す Object の `duration` には合成尺が入る（`audio_sequence` と同じ。`time()` は不要で、引数なしの `time()` も生成前の初回レンダで通る）。この `duration` は仮の値で、後から `speed()` / `trim()` を足したり `compute(duration=d)` で焼き直したりすると、レイヤーの実行後に加工後の尺へ入れ直される（`time(d)` / `show(d)` で明示した尺は変えない）。Transform/Effect適用済みObjectは先に `compute()` で素材化してから渡す
- 動画の尺（`length()`）は映像と音声の**長い方**で決まる。音声の方が長い動画（AAC は 1024 サンプル単位なので、scriptvedit が書き出す mp4 も含めて多くの動画は音声が数十 ms 長い）は、音声が終わるまで**映像の最後のフレームを保持**する。`Object("a.mp4").time()` と並べても、つなぎ目に背景の黒が挟まらない。`time(d)` で素材より長く伸ばした分は保持しない（従来どおり背景が見える）
- Object が映り始めるのは、開始時刻に**最も近いフレーム**から（映像の中身もそのフレームに届く）。開始時刻がフレームの格子から外れていても、中身の1枚目は欠けない

### テキスト・字幕

drawtext / subtitles で文字を直接描画する映像Object。画像同様 `.time(秒)` で配置する。

```python
text("こんにちは", x=0.5, y=0.3, size=64, color="white", box=True).time(3)     # 静的テキスト
typewriter("1文字ずつ表示", cps=10, x=0.1, y=0.5).time(4)                      # タイプライタ
counter(0, 100, format="%03d", x=0.5, y=0.5, size=80).time(4)                 # 数値カウントアップ
counter(0, 1234567, format="¥%,d", easing="ease_out_cubic", border=3).time(3) # 桁区切り + イージング
subtitles("subs.srt", style="FontName=Meiryo,FontSize=28").time(30)           # SRT/ASS/VTT字幕
subtitles(here("pages.ass"), fontsdir=here("fonts")).time(30)                 # 同梱フォントを名前で使う
text("1行目\n中央そろえの2行目", text_align="center", line_spacing=12, border=3).time(3)
```

- `x` / `y` / `alpha` は 0..1 のキャンバス比率で Expr/lambda 可（liveアニメ）
- `size` は定数のみ（FFmpeg 8.0 の drawtext fontsize 式は SEGV のため）
- フォントは未指定時に OS 別の既定候補を自動探索する（Windows: メイリオ等 / Linux: Noto Sans CJK・IPAゴシック / macOS: ヒラギノ）。環境変数 `SCRIPTVEDIT_FONT` で既定フォントを上書き可能（CI・Docker での固定に便利）。見つからない場合は OS 別の導入例（`apt install fonts-noto-cjk` 等）つきのエラーで案内する
- `text` の複数行: `line_spacing`（行間に足す px。負で詰める）、`text_align`（行ごとの揃え `left` / `center` / `right`。ブロック全体の位置は `anchor` と `x` / `y`）、`y_align`（y の縦の基準。`text`＝いちばん背の高い字の上端（既定）/ `baseline`＝1行目のベースライン / `font`＝フォントの行の上端。語ごとに `text()` を分けて横に並べるなら `font` か `baseline` で行がそろう）
- `counter(from_, to, *, format="%d", easing=None, ...)`: 最初のコマは `from_`、**最後のコマは必ず `to`**（途中は四捨五入。総尺がフレーム格子に乗らない動画の末尾でも、出力される最後のコマが `to`。`configure(duration=)` / `render(end=)` で途中を切った場合だけは切った時点の値）
  - `format` は printf 風で、変換指定は1個: `%d` / `%05d`（ゼロ埋め）/ `%,d`（3桁ごとのカンマ）/ `%.2f`（小数。9桁まで）/ `%,.1f`。前後に固定の文字（接頭辞・接尾辞）を書ける（`"¥%,d円"`）。文字の `%` は `%%`。アポストロフィは不可。ゼロ埋めは桁区切り・小数と併用不可
  - `easing` は `None`（等速）/ イージング名（`"ease_out_cubic"` 等）/ `u` を受け取る関数・Expr（`ease_spring(...)`、`lambda u: u ** 2`）。行き過ぎる系は途中で `to` を超えた値も表示する
  - 精度: 「|値| × 10^小数桁」が 2^53（約 9.007×10^15）未満なら全桁が正しい（定数の `from_` / `to` がこれを超えると `ValueError`）。32ビット（±2,147,483,647）を超える整数・桁区切り・小数は、桁数と符号ごとの drawtext を切り替えて表示する（drawtext の `%{eif}` が 32ビットの整数しか印字できないため。フィルタが数個に増える）。桁数が変わるコマで文字列の幅が変わるので、`anchor="center"` ではその瞬間に全体が少し動く
- `subtitles(file, *, style=None, fontsdir=None)` は SRT 自身のタイムコードで表示されるため `.time(全体尺)` で開始0に配置する。フォントは**名前**で探す（libass）。システムに入っていないフォント（同梱フォント・可変フォントから切り出した静的フォント）は `fontsdir` にフォルダを渡す（`karaoke(..., fontsdir=)` も同じ）
- `glow()` は `text()` にも掛けられる（白い文字は白く光る）
- `text` / `typewriter` / `counter` は実体の画像を持たない（drawtext で描く）ので、`morph_to` / `explode_to` / `assemble_from` は掛けられず、`compute()` もできない（どちらも `ValueError`。以前は黙って無視されていた）。文字を粒子化・モーフするときは下の `text_image()` を使う

### 文字を画像に焼く（text_image）

文字を PIL で**透過 PNG** に描き、**画像 Object** を返す。drawtext の `text()` では出来ない
「1行の中の一部だけ色・太さ・書体を変える」「可変フォントの太さ」「行送り・行ごとの揃え・自動折り返し」を受け持つ。
画像なので `morph_to` / `explode_to` / `assemble_from` の入力にも target / source にも使える。**Pillow 9.1 以上が必要**（`pip install "Pillow>=9.1"`）。

```python
# 区間のリスト: 一部だけ色・太さを変える（エスケープ不要。コードや正規表現はこちらで）
page = text_image([("犯人は、", {}), ("正規表現が1本", {"color": "#E60012", "weight": 900}), ("だった。", {})],
                  size=64, font="C:/Windows/Fonts/NotoSerifJP-VF.ttf", weight=700,   # 可変フォントの wght 軸
                  border=4, max_width=1500, line_spacing=1.6)
page.time(3) <= move(x=0.5, y=0.4, anchor="center")

# 簡易マークアップ: {書式|文字}。styles= に名前を登録しておくと短く書ける
text_image("犯人は、{r|正規表現が1本}だった。", markup=True,
           styles={"r": {"color": "red"}}, size=64, border=3).time(3)

# 文字を粒子化・モーフする（morph の2枚は canvas= で同じ寸法にする）
boom = text_image("崩壊", size=160, border=6, padding=60)
boom.time(2) <= explode_to(max_pixels=8000, expand=300)
a = text_image("2038年", size=180, border=6, canvas=(900, 320), align="center")
b = text_image("桁あふれ", size=180, border=6, canvas=(900, 320), align="center", color="red")
a.time(1.5) <= morph_to(b)
```

- `text_image(content, *, size=64, font=None, font_index=0, weight=None, color="white", markup=False, styles=None, line_spacing=1.5, align="left", max_width=None, border=0, border_color="black", shadow=(0, 0), shadow_color="black@0.6", shadow_blur=0, background=None, background_radius=0, padding=None, canvas=None, missing="error", duration=None)`
- **書式**: `content` は文字列か区間のリスト。区間は `"文字"` / `("文字", {書式})` / `("文字", "styles の名前")`。書式のキーは `color` / `size` / `font` / `font_index` / `weight`。区間が `font` を変えたとき、`weight` と `font_index` は基本書式から引き継がない
- **マークアップ**（`markup=True` のときだけ解釈する）: `{書式|文字}`。書式はカンマ区切りで、`キー=値` か裸の語（`styles` の名前、無ければ色）。入れ子は不可。区間の中の `|` は文字。**エスケープは `\{` `\}` `\\` の3つだけ**で、それ以外の `\` はそのまま出る
- **太さ**: `weight=700` は可変フォントの wght 軸、`weight="Bold"` は名前つきインスタンス（同じ軸の値になる指定は同じキャッシュになる）。可変フォントでないファイルに指定すると `ValueError`（黙って無視しない）。`.ttc` の書体は `font_index=`
- **行**: 縦位置は基本書式のフォントのメトリクス（ascent / descent）で決めるので、`ー・、` だけの行や英小文字だけの行でも位置と画像の高さは変わらない。`line_spacing` は行送り ÷ 文字サイズ。`align` は行ごとの揃え
- **折り返し**: `max_width`（px）を渡すと、全角はどこでも・欧文は語の切れ目で折り返す。行頭禁則は句読点（`、。！？` 等）と閉じ括弧の類だけで、直前の字ごと次の行へ送る（`だ！？` のように2つ並ぶところまで。`！！！！！` のような長い連続は送らずその位置で折る。長音 `ー` や `…` は禁則にしない）。`.env` のように `.` で始まる半角の語は行頭に置ける。行末禁則・ぶら下げ・ルビは無い
- **豆腐**: フォントに無い字（フォントの cmap に割り当てが無い字）があると既定で `ValueError`（どの字かを示す）。`missing="warn"` / `"ignore"` で豆腐のまま描ける
- **装飾**: `border`（縁取り）/ `shadow` + `shadow_blur`（影・ぼかし）/ `background` + `background_radius`（下地）。文字や装飾がキャンバスからはみ出すときは警告するので `padding` を増やす
- 色は `text()` と同じ ffmpeg 形式（色名 / `色名@alpha` / `#RRGGBB[AA]`）
- 生成物は content-addressed キャッシュ（`__cache__/artifacts/textimage/*.png`）。鍵は文字列・書式・**フォントファイルの内容指紋**・Pillow の版で、フォントのパスには依らない。PNG は構築時（レイヤー実行時）に描く
- `p.audit()` は `text_image` の文字を `text()` と同じ基準（`text-too-small` / `text-no-decoration` / `text-overflow` / `font-missing-glyph`）で検査する。大きさと幅は **`resize` / `scale` の倍率を掛けた画面上の実寸**（区間ごとに大きさが違うときは最小の区間。`scale` がアニメーションのときは最大の時点）。`zoom` / `crop` と `compute()` で素材化した後の倍率は見ない。`rotate`（0 / 180 度以外）・`rotate_to` で回した文字は幅を求められないので `text-overflow` を出さない。自前の PNG・動画・HTML の中の文字は見ない

### 文字列の組み替え（text_transition / odometer）

文字列の状態 A→B→… を**字単位で組み替える**透過の動画 Object。残る字は滑り、入れ替わる組は上下に離れて運ばれ、
変わる数字は字の窓の中で回り（roll）、消える字は落ち、新しい字は現れる。各状態のコマは `text_image` と**画素が一致**する。
numpy・opencv-python・Pillow が要る（dry_run は Pillow とフォントだけ）。

```python
# 公式の式から要らない部品が落ち、残りが滑って大きな \s+$ になる（エスケープを1トークンに）
t = text_transition([official, [(r"\s+$", {"size": 150})]], unit=r"\\.|.", leave="fall",
                    anchor="center", font=mono, size=96, hold=[0.5, 0.8], duration=1.6)
t.time(3) <= move(x=0.5, y=0.5, anchor="center")

# 赤い '+' が1つ降りる（色は後の状態の区間で決める。残る字の色は oklab で補間）
text_transition([r"\s+$", [r"\s+", ("+", {"color": "#e0241b"}), "$"]], duration=0.8, enter="drop")
text_transition(["Fundation", "Foundation"], enter="drop", size=140)
text_transition(["[1, 2, 10]", "[1, 10, 2]"], unit="code")      # '10' は上、'2' は下へ離れて入れ替わる

# 32ビットの数え盤（変わる桁を右から 0.04 秒ずつ遅らせて回す）と、日時の巻き戻し
odometer(2**31 - 1, 2**31, base=2, digits=32, signed="twos", group=8, sep=" ", font=mono)
odometer(2147483647, -2147483648, group=3)                        # 10進の桁区切り
odometer.text("2038-01-19 03:14:07", "1901-12-13 20:45:52", roll_dir="down")
```

- `text_transition(states, *, duration=1.2, hold=0.0, unit="char", match="auto", prefer=("replace", "insert", "delete"), move="slide", replace="auto", leave="fade", enter="fade", stagger=0.02, easing="ease_in_out_cubic", anchor="left", roll_dir="auto", motion_blur=True, swing=1.0, **fmt)`
- **状態**: 文字列か `text_image` と同じ区間のリスト。状態ごとの大きさ・色は区間の書式で変える（対応した字は動きながら拡大・縮小する。縁取りの太さは途中でも `border` px のまま）。`fmt` は `text_image` の書式（`max_width` は受けない）。1状態 400 トークン・3行まで
- **時間**: `duration` は遷移ごと、`hold` は状態ごと（数かリスト）。コマ数 = (Σhold + Σduration) × fps。`obj.figure.starts` が各状態に着いた秒、`obj.figure.state_frames` がそのコマ
- **トークン**: `unit` は `char` / `word`（空白もトークン）/ `code`（識別子・数・文字列リテラル・空白・1字の記号）/ 正規表現の文字列
- **対応**: `match="auto"` は LCS（自前の DP。タイは左を残す）→ 残りの同じ字を近い順に `move` → LCS の隙間で位置のそろう同じ種類の字を `replace` → 残りは `leave` / `enter`。`"edit"`（レーベンシュタイン。タイは `prefer` の順）/ `"position"`（`anchor` 側から位置で）/ `[(i, j), …]`（手で）。結果は `obj.figure.pairs`（番号は `obj.figure.tokens` の添字）
- **動き**: 消える字が先に去り、残る字が詰め、空いた所へ新しい字が入る。入れ替わる組（`move`）は上下に離れ（左へ行く字が上）、運ばれ、置かれる。残る字は組が離れきっている間だけ滑り、離れる高さはほかの字に重ならないよう構築時に選ぶ。離れる量は `swing` × 行の字の高さ（ascent + descent。既定 1 倍）まで（行の字をちょうど避ける量より浅くはしない。0 で、いつもちょうど避ける高さ）。キャンバスの高さもこれで決まる（以前は `'[1, 2, 10]'` の組が字の 2.2〜2.5 倍振れ、`size=110` で画面の下で切れた）。`replace="auto"`（既定）は数字どうしだけ回し（roll）、ほかは薄れて入れ替わる。`"roll"` は字の窓で縦横とも切り抜いて縦に流す（窓の横は字の送り幅、縦は数字の帯 = 縁取りを含む `0`〜`9` のインク + size × 0.08。字のマスまで流すと ascent の大きいフォントで行の上下へ流れ出た。1字 0.25 秒以上。`roll_dir="auto"` は増えるなら上・減るなら下）。消える字は下の層。`leave="fall"` は行の高さの 0.6 倍落ちて 6 度まで傾く、`"scatter"` は上向きの扇（-135〜-45 度）へ行の高さの 0.5 倍飛んで 20 度まで回り 1.1 倍まで膨らむ（キャンバスの中に収める）、`enter="drop"` / `"rise"` は行の高さの 0.45 倍から入る。`stagger`（既定 0.02 秒）の広がりは `duration` の 30% まで。途中のコマを騒がしくしない大きさにしてある。1コマで字幅の 0.5 倍以上動く字はモーションブラー
- **揃え**: `anchor`（`left` / `right` / `center`）は共通キャンバスの中の横の揃え。全体の幅が変わっても変わらない字は止まったまま。各状態のコマは `text_image(状態, **fmt, **obj.figure.text_image_kwargs)` と一致する（`text_image_kwargs` は `canvas` と、`fmt` に無ければ `align` と `padding`。`align` / `padding` に `None` は渡さない）。鍵にはフォントの内容指紋と Pillow の版が入る
- `odometer(from_, to, *, base=10, digits=None, signed=False, group=None, sep=None, duration=1.5, carry="ripple", ripple=0.04, roll_dir="auto", **fmt)` は糖衣（位置で対応・`anchor="right"`）。`carry="together"` で全部同時。`odometer.text(a, b)` は書式つきの文字列の数字の位置だけを回す
- 使いすぎると忙しくなる。「答えが変わる」瞬間だけに置く。長い日本語の字幕には向かない

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
# loudnorm → 48kHz化 → 最終ピークリミッター（mode="dynamic"。既定）
p.normalize_audio(-14, mode="linear")
# 音声だけ全編を1回測る → 一定の増幅 → 48kHz化 → 最終ピークリミッター（BGM がポンピングしない）

bgm <= loop() & duck_under(narration, ratio=8)   # ループ + 自動ダッキング（time() は呼ばない）
bgm2.time(30) <= loop()                          # 尺を決めてループするなら time(秒) を付ける

seq = audio_sequence("a.mp3", "b.mp3", crossfade=1.0)  # acrossfade連結（2つ以上）
hit = sfx("click.wav", at=[0.5, 1.5, 3.0], volume=1.0) # 同一音源を複数時刻に配置
pop = sfx("pop.wav", at=2.5)                           # 1回だけなら数値1つでよい（at=[2.5] と同じ）
viz = audio_viz("bgm.mp3", kind="waves", color="cyan") # 波形/スペクトルを映像化
```

- `normalize_audio` は Project メソッド。`duck_under` / `loop` は AudioEffect（`&` で連結）。
  `~` は映像系と共通の品質ヒントで、音声を消すには `adelete()` を使う
- `duck_under(*others, ratio=8, threshold=0.05, attack=20, release=250, hold=0)`: `others`（ナレーション等）再生中に自音量を下げる。`hold`（ms）は相手が止んでから戻り始めるまでの保持時間。`release` だけだと読点や文の間（0.3〜0.6 秒）のたびに BGM が戻りかけ、`release` を長くすると声の無い場面でもなかなか戻らない。`hold=600` のように指定すると、相手の検出レベルが `threshold` を下回ってから hold の間は直前の発声の平均的な検出レベルを保ち、その後 `release` で戻る（実測: 0.35 秒の間で、hold なしは元の音量まで戻り、`hold=600` は約 9dB 下がったまま。発声中の下げ幅は hold なしより約 1dB 深い）。`hold > 0` のときは検出用の枝を 48kHz モノラルへまとめ、保持つきの包絡（`aeval`）にしてから `sidechaincompress` へ渡す。相手は複数指定できる（`duck_under(n1, n2, n3)` / `duck_under([n1, n2, n3])`。`Narration` は `.audio` が使われる）。複数のときはどれか1つでも鳴っている間は下がる（サイドチェーンは各 other を `amix=normalize=0` で合算した1本）。検出は各 other の**形式統一（下記の 48kHz・ステレオ化）より前**の音声で行うので、モノラルのナレーションも元の音量のまま `threshold` と比べられる。1つの Object に `duck_under` は1回だけなので、相手が複数なら1回の呼び出しにまとめる。sidechainは自動で無音延長されるため、ナレーション終了後もBGMは指定尺まで続く
- `loop(until=None)`: 尺は「`time(秒)` / `until()` / `show()` 等で決まった尺 → `loop(until=秒)`
  （タイムラインの絶対時刻）→ Project の総尺」の順で決まり、そこまでループする。
  **引数なしの `time()` と組み合わせてはいけない**: `time()` は尺を素材の長さで確定させるので、
  `bgm.time() <= loop()` は1回再生で終わる（`loop(until=)` も効かない）。`bgm <= loop()` と
  `time()` を呼ばずに書けば総尺まで、`bgm.time(30) <= loop()` / `bgm.until("outro.end") <= loop()` なら
  その尺までループする
- `audio_sequence` は連結後の実尺を返却Objectの`duration`へ自動設定する。`Narration`を直接渡すと字幕もcrossfade込みで並び、返却Objectの数値`@`配置へ追従する。追加の`.time(total)`は不要
- `normalize_audio(target=-14, *, true_peak=-1.5, lra=11, limiter=True, sample_rate=48000, mode="dynamic")` は最終音声へ正規化、サンプルレート確定、任意のピークリミッター（`alimiter`。look-ahead 5ms）を順に適用する。`true_peak`は最終lossy出力の目標で、AAC/Opus再上昇向けに内部で0.5dBの余裕を確保する。WebM/Opusの出力レートは48kHz固定
- **`mode` は正規化の方式**:
  - `"dynamic"`（既定）… 測定値を渡さない1パスの `loudnorm`。3秒窓の短期ラウドネスを目標へ寄せ続けるので、**声の無い区間の BGM が膨らみ、声が入ると沈む（ポンピング）**。実測（静かな BGM -36.5 LUFS の区間 + 声 -20.8 LUFS の区間、目標 -14）: 区間の音量差が 15.8dB → 1.4dB に潰れ、BGM だけの区間が -14.7 LUFS まで持ち上がる
  - `"linear"` … 本レンダの前に、同じ音声グラフを**音声だけ・全編・null 出力で1回流して**統合ラウドネスと true peak を測り（`loudnorm=print_format=json`）、`volume=<target − 測定値>dB` → `aresample` → `alimiter` で仕上げる。動的な `loudnorm` は通さないので、**区間どうしの音量差がそのまま保たれる**（同じ素材で 15.8dB → 15.7dB、統合 -14.1 LUFS、true peak -1.9dBTP）。`lra` は使わない。ナレーション＋BGM の動画はこちらを推奨
- `mode="linear"` の補足:
  - 測定結果は `__cache__/artifacts/loudness/<鍵>.json` に保存し、**音声グラフと音声素材が同じなら2回目以降は測り直さない**（映像だけ直した再レンダで全編を流さない）。鍵は測定コマンドから作る（素材は内容指紋）ので、`target` / `true_peak` / `limiter` / `sample_rate` を変えても測り直さない
  - **部分レンダ（`start` / `end`）・並列レンダ（`parallel=N`）も全編の測定値で増幅する**（BGM だけの窓を書き出しても、その窓だけで測って +20dB 持ち上げたりしない）。gif / webp / 連番PNG / サムネイル / 絵コンテなど音声を出さない出力と、音声の無いプロジェクトでは測定しない
  - `render(dry_run=True)` は測定しない。測定コマンドは戻り値の `cache` 側に「実行予定」として載り、`main` の増幅量は `volume=<MEASURED_GAIN>dB` と表記される（dry_run はキャッシュの有無に依存しない）
  - ピークの多い素材を大きく持ち上げると、上限を超えるピークをリミッターが削る分だけ統合ラウドネスが目標よりやや低くなり、声の区間がわずかに下がる（実測: TTS の声＋BGM を +8.8dB 増幅、ピークを最大 5dB 抑えたとき、統合 -14.46 LUFS・区間差の変化 0.5dB）。レンダ時に「上限を超えるピークはリミッターが最大 X dB 抑えます」と表示される
  - `limiter=False` のときは、目標どおり増幅すると true peak が上限を超える場合に限り、超えない所で増幅を止める（統合ラウドネスは目標より低くなり、警告を出す）
- **音声は混ぜる前に 48kHz・ステレオへ揃う**: 各音声の加工チェーン（atrim / atempo / avolume / adelay 等）の末尾に `aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo` が付く。`amix` / `sidechaincompress`（`duck_under`）/ `acrossfade`（`audio_sequence` / `video_sequence`）の出力形式は**先頭入力に従う**ため、揃えないとモノラル 24kHz の TTS が先頭に来ただけで全体が 24kHz・モノラルになり、ステレオの BGM が L/R 平均に潰れる（入力の並びは priority 順＋生成順で、書き手からは見えない）。素材側で形式を合わせる必要はない。ミックス結果は 48kHz・ステレオで、`normalize_audio` の既定 `sample_rate=48000` と一致する
- **モノラル素材はステレオへの自動変換で中央定位になり、各チャンネルの振幅は約 -3dB になる**（パンの法則。左右の合計パワーは元のモノラルと同じ）。これは仕様。ただし**入力がすべてモノラルのプロジェクト**（TTS ナレーションだけの動画など）は、以前はモノラルで出力され、ステレオ環境では左右とも元の音量で鳴っていたので、`normalize_audio()` を使わないと再生音量が約 3dB 下がる（実測 -18.8 → -21.9 LUFS）。音量を揃えるには `normalize_audio()` を使う（目標値どおりになる）
- **`duck_under` のサイドチェーン検出は揃える前の音声で行う**: 検出用の枝は各 other の加工チェーンの `aformat` の直前から `asplit` で取り出す。揃えた後から取ると、モノラルのナレーションが各チャンネル -3dB のステレオとして検出され（`sidechaincompress` の既定 `link=average`）、既定値でダッキングが約 2.6dB 浅くなるため。相手が1つなら検出用枝はチャンネル構成を変えず周波数だけ 48kHz へ揃える。相手が複数なら合算のため各枝を 48kHz モノラルへダウンミックスする（`aresample=48000:ochl=mono:rematrix_maxval=1`。モノラルはそのまま、ステレオは (L+R)/2 で、左右が同相なら相手1つのときと同じ検出レベル）。揃えずに合算すると結果が先頭の相手の形式に従い、並び順で検出レベルが変わる
- `audio_sequence` / `sfx` / `audio_viz` はキャッシュ生成物（音声/映像Objectを返す）。`audio_viz` の `kind` は `"waves"` / `"spectrum"` / `"cqt"`。**`color` が効くのは `kind="waves"` のときだけ**（`showspectrum` / `showcqt` は ffmpeg カラー形式の色指定を持たないため）

### パーティクル（explode / assemble）

`morph_to` と同じ終端フレーム機構でベイクされる生成系Effect。bakeable ops の末尾に配置する。

```python
img.time(3) <= explode_to(blend=lambda u: u)          # 自身が粒子化して飛散
img.time(3) <= assemble_from(Object("logo.png"))      # source の粒子が集合して画像になる
```

- パーティクルパラメータ（`**particle_params`）: `max_pixels`, `speed`, `gravity`, `spread`, `swirl`, `particle_size`, `seed`, `dissolve`, `expand`, `fade`, `delay`, `duration`、`explode_to` は `toward`、`assemble_from` は `from_point`。既定値・単位・推奨値は `python -m scriptvedit describe --name explode_to --format md`
- `expand`（素材の周りに足す透明の余白 px）は既定 `None` = **自動**。粒が実際に飛ぶ範囲から決めるので、素材の矩形で箱型に切れない（左右と上下で別の幅。ほぼ消えた粒は数えず、画面の外になる分は足さない）。数値を渡すと四方に同じ幅。余白は対称に付き、`move` の `anchor` は余白を除いた元の絵の箱を基準にするので、`topleft` 等でも絵の位置は変わらない
- `fade=False` … 粒が薄れず、散った位置に残る（既定 `True` は進行に合わせて消える）
- `delay=秒` … 動き出すまで元の絵（assemble は最初のコマ）を出す。静止の間を `blend` で作るより速い（その間のコマを粒子計算で焼かない）。`duration=秒` … 動く秒数。終わった後は最後のコマを Object の尺の終わりまで保持する

```python
# 1 秒見せてから 1.5 秒で散り、散ったまま尺の終わりまで残る
img.time(4) <= explode_to(max_pixels=12000, speed=380, gravity=0, fade=False,
                          delay=1.0, duration=1.5)
# 右上の1点（素材の中心から右へ 700px・上へ 380px）へ吸い込まれて消える
img.time(2) <= explode_to(max_pixels=10000, toward=(700, -380), swirl=0.6)
# 左下の1点から出て集まり、2 秒で絵になる。残りの 1 秒は絵を保持
logo.time(3) <= assemble_from(Object("logo.png"), from_point=(-600, 300), duration=2.0)
```

- `toward` / `from_point` は (dx, dy)（素材の中心からのずれ px。右と下が正）。このとき `speed` は横ぶれの大きさ、`gravity` は道すじのたるみ、`spread` は粒の出発のばらつきになる
- 重さは「余白込みのキャンバス面積 × 動くコマ数」。目安は 12000 粒・1080p・2 秒で 15 秒前後（2回目からはキャッシュ）
- `assemble_from(source)` の `source` は集合アニメに消費され、Project のタイムラインから自動除外される
- 生成エンジンは `scriptvedit.morph`（`generate_explode_frames` / `generate_assemble_frames`）
- `python -m scriptvedit.morph a.png b.png -o out.mp4` という CLI もあるが（実体は `morph_cli.py`）、こちらは **scriptvedit の ffmpeg パイプラインを通らない**（OpenCV が mp4v で直接書き出す＝アルファ無し・品質指定不可）。プレビュー用途で、本番は `morph_to()` を使う

### 粒子の輸送モーフ（fly_to）

絵 A（fly_to を掛けた Object）の粒が飛んで、離れた所に置いた絵 B（`target`）になる終端フレーム Effect。
重なる形どうしの変形は `morph_to`（sdf。離れた形だとクロスフェードになる）、離れた形どうしは `fly_to`。

```python
# 一覧表の赤い「今の状態」（白い部分は透明にした絵）が飛んで、締めの一文になる
red = text_image([("Stack Overflow　", {"color": "white@0"}), ("34分、止まった", {"color": "#e0241b"}),
                  "\n", ("Cloudflare　", {"color": "white@0"}), ("今も、書く人しだい", {"color": "#e0241b"})],
                 size=44, align="right")
close = text_image("事件は、まだ、終わっていない。", size=96, color="#e0241b")
red.time(3) <= fly_to(close, offset=(-420, -160), arc=0.2, stagger=0.35, delay=0.3, duration=2.2)
red <= move(x=0.68, y=0.62, anchor="center")
```

- `offset=(dx, dy)` は A の中心から B の中心までのずれ（A の絵の px。fly_to の前の Transform を掛けた後）。B の左上は A の左上から `(⌈Wa/2⌉ + ⌊dx − Wb/2⌋, ⌈Ha/2⌉ + ⌊dy − Hb/2⌋)`（A の中心を整数の画素に置いたとき、中心を「A の中心 + offset」に置いた静止画の B と同じ丸め）
- **最初のコマは A、最後のコマは offset の位置の B と画素一致**する。`delay` の間は A、`duration` の後は B を Object の尺の終わりまで保持（前後に同じ絵の静止画を置かなくてよい）
- 着いた B を出し続けるなら、別の Object へ引き継がず **fly_to の Object の `time()` を延ばす**（B を保持するだけなので軽い。消すときは `-fade(...)` を後ろに置ける）。別の静止画の B（`anchor="center"`・中心 = A の中心 + offset）へ引き継ぐときは、A の中心を整数の画素に置き、`⌈Wa/2⌉ + ⌊dx − Wb/2⌋` と `⌈Ha/2⌉ + ⌊dy − Hb/2⌋` が偶数になるよう offset を 1px 調整すると画素一致する（overlay は左上を 4:2:0 の 2px 格子へ切り捨てるので、静止画どうしのずれは常に偶数。奇数だと 1px 跳ねる）
- 粒: A と B の不透明な画素（α>0.1）を α の重みで N 粒ずつ取る（N = min(`max_pixels`, 多い方の画素数)。少ない側は複製して ±0.35px 揺らし、α を分け合う）
- 対応 `match`: `"ot"`（既定。スライスした最適輸送: 64 方向への射影のソートで 40 回流してから Hilbert 曲線の順で組む。移動の総量はハンガリアン法とほぼ同じで、1.2 万粒でも 1〜2 秒）/ `"angle"` / `"random"`
- 道すじ: 2次ベジェ。`arc` は制御点を中点から 距離×arc だけ進む向きの右手側へずらす（全粒で同じ側なので交差しない。負で左手側）。`swirl` は中点のまわりに回す角度 rad
- 時間: `stagger`（出発の遅れの幅）と `stagger_by`（`"x"` / `"y"` は進む向きの先頭の粒から先に出る、`"distance"` は遠くへ行く粒から、`"random"`）。各粒は残りの時間で smoothstep で加減速する
- `dissolve=(a, b)`: 最初の a の区間で A の絵から粒へ、最後の b の区間で粒から B の絵へ移る。色は `color_path`（`"oklab"` / `"oklch"`）
- 粒はサブピクセルの位置の円（縁 1px のアンチエイリアス）を、リニア光 × 事前乗算で足し合わせて描く（ゆっくり動く粒も 1px 単位で跳ねない）。A と B が同じ色なら、どのコマのどの画素もその色のまま（α だけが変わる）
- **キャンバス**は A の箱・B の箱・全粒子の道すじを覆い、**A の中心に対して左右・上下それぞれ対称**に広がる。`move` の anchor は余白を除いた A の箱が基準なので、`topleft` 等でも A は静止画と同じ画素に映る（キャンバスが画面の外へ出ても 1px もずれない）。4096px を超えると ValueError
- 制約: 終端フレーム Effect の規則どおり（1つの Object に1回・bakeable の末尾・後ろは live だけ）。`target` は加工していない画像 Object（`text_image` 可。加工つき・`text()` 系・動画は ValueError）。不透明な画素が無い絵は ValueError
- 重さ: 1.2 万粒・1080p の文字どうしで前処理 1〜2 秒 + 1コマ 35〜95ms（CPU の空き具合で倍ほど変わる。1 秒ぶんで 2〜5 秒。2回目からはキャッシュ）。粒の描画は「粒の数 × `particle_size`²」に比例する（1.2 万粒で半径 32 は 1コマ約 1.5 秒。メモリは分けて描くので頭打ち）。生成エンジンは `scriptvedit.morph_flight`（`generate_flight_frames`）
- 粒子は最も目を引く道具。`explode_to` / `assemble_from` と合わせて1本に2〜3回まで。粒の数に意味を持たせない（お金が均等に分かれたように見える）。道すじが字幕を横切らないよう、`arc` の向きと重ね順は呼び出し側で決める

### 点で描いた地球と世界地図（globe）

正射影の地球（`projection="ortho"`）か正距円筒の世界地図（`"plate"`）を等間隔の点で描き、回転・都市の点・大円の弧・波紋・昼夜の境・札を時刻つきで積んで、透過動画 Object にする（framekit.build。`frames()` と同じ qtrle の .mov）。

```python
from datetime import datetime, timezone

# 世界地図。陸は同梱の地球（Natural Earth 1:110m）、中心の経度は弧に合わせて自動（view="auto"）
g = globe(projection="plate", size=(1400, 700))
g.points(sites)                                      # 拠点（出典を書くか「模式図」と明記する）
g.arc((37.8, -122.4), (35.7, 139.7), t=1.0)          # 太平洋を渡る弧も端で切れず1本につながる
g.ripple((35.7, 139.7), t=1.8)
g.night(datetime(2024, 7, 19, 4, 9, tzinfo=timezone.utc))   # 夜の側の点を暗く
g.label((35.7, 139.7), "東京 13:09", t=2.0)
obj = g.build()                                      # obj.figure.xy(座標, 秒)・obj.figure.view
obj.time(5) <= move(x=0.5, y=0.5, anchor="center")

e = globe(size=900)                                  # 地球（既定で陸・縁の淡い光あり）
e.turn(0.5, (35.7, 139.7), dur=1.5)                  # 四元数の slerp（等角速度・遠回りしない）
e.points(cities, appear=("staged", [(0.5, 1), (0.8, 300)]))   # 1点 → 300点が一斉に
```

- 座標は `(緯度, 経度)` の度。`land` の多角形だけ `(経度, 緯度)`。|緯度| が 90 を超えると ValueError
- **陸地は同梱の地球が既定**（`land=True`）。Natural Earth（パブリックドメイン）の 1:110m の陸を 1440×720・1bit の PNG（約 12KB）にして `src/scriptvedit/data/` に入れてある。同梱の素材・データ（`assets/` と `data/`）は全部自作・第三者素材ゼロの方針で、これが唯一の例外（ライブラリとして同梱している `formula()` 用の KaTeX は別扱い。自作の大陸の形は不正確な地図が実データに見え、同梱しないと既定が陸の無い地球になるため。出典・利用条件・元データの SHA-256 は同じフォルダの `NOTICE.md` と PNG の tEXt）。地図を出すときは「地図: Natural Earth」と添えることを勧める（義務ではない）。`land=False` は陸を描かず 15 度の経緯線だけ、`land="<PNG のパス>"` は自前の正距円筒の白黒マスク（`python scripts/make_land_mask.py <出力> --width 2880` で細かいものも作れる）、`land=[[(経度, 緯度), …], …]` は手作りの模式図。`land=None` は ValueError（以前の「陸なし」と取り違えないよう `False` と書く）。鍵には陸地の内容指紋が入る（パスは入らない）
- 既定の見た目: 陸の点は不透明度 0.55 で、拠点の点（`points`）はまわりの陸の点を抜いて（堀）淡い光の輪を敷くので、既定の白のままでも陸に埋もれない。点が密なとき（plate 1400×700 に数千点で密度を見せる、など）は `points(..., halo=False)`（堀も光の輪も無しで芯だけ。付けたままだと堀が陸をほとんど消し、光の輪がつながって霞になる。900px の地球に 300 点ほどなら既定のままで陸が読める）。堀と光の輪は点の数に比例しないメモリで描く（円を分けて計算する。plate 1400×700 に 2 万点で 1 コマのピークは halo=False と同じ約 170MB、時間は約 1 秒で halo=False の約 2.5 倍）。ortho は縁の外に淡い光（`atmosphere=0.25`。0 で無し。光のぶんキャンバスが広がる）
- plate の中心の経度: `view` の既定（`None`）は ortho で `(20, 0)`、plate で `"auto"`。`"auto"` は build のときに、弧が地図の左右の端をまたがず、点・弧・波紋・札が端の近くで切れず、端の経線がなるべく大陸を切らない経度を選ぶ（何も無ければ 0。選んだ向きは `obj.figure.view`。手で同じ経度を書いたのと同じ鍵）。`view=(0, 150)` のように書けば固定。日付変更線をまたぐ弧を端で分けて描く動き（右端から出て左端から続く）は、端をまたがせたときにだけ起きる
- 点: ortho は Fibonacci 球、plate は画面上の格子。`step=None`（既定）は画面上の点の間隔 ≒ 3.9×`dot` px になる角度（ortho は地球の中心で。`size=900` で 1.19 度＝全体約 2.9 万点・陸に約 8 千点、`size=300` で 3.56 度）なので、小さい地球でも点がつぶれて面にならない（間隔が点の直径を下回る `step` は警告）。明るさ = `limb + (1 − limb)·√z`、縁で詰まった点は不透明度を下げて白い筋にしない。`dot`（半径 px）は 2 以上（2 未満は回転で瞬く）
- 隠れ: 地表の点は z ≤ 0 で裏。持ち上げた弧は「z < 0 かつ投影が円の内側」のときだけ隠れ、縁の外へ出た部分は描く（境目は二分法）。plate の弧は日付変更線で分けて描く。plate の弧の反りは弦に垂直で上向き（弦が縦に近いときは地図の内側へ）で、反りで日付変更線をまたいで反対の端に描かれるときは反りを縮める
- 出来事: `turn(t, (lat, lon), dur, easing)`・`spin(t0, t1, 度/秒)`・`points(coords, appear="at" | ("wave", 起点, 度/秒) | ("staged", [(秒, 件数), …]), halo=True)`・`arc(src, dst, t=, height, width, head, trail)`・`ripple(coord, t=, max_deg)`・`night(when, dim, twilight)`（when は UTC の datetime。naive は ValueError。太陽の真下は NOAA の簡略式）・`label(coord, text, side)`（裏へ回ると消える。重なり・はみ出しは警告）
- キャンバスは絵の外接矩形: 地球（地図）の箱に、縁の外へ出る弧・大気の光・札が切れないだけの余白を上下・左右に対称に足す（地球は真ん中のまま。`obj.figure.center` / `obj.figure.margin` で分かる）
- `spin` は既定では使わない。毎コマ全面が変わり qtrle が効かない（900×900 で 5 秒回すと約 80MB）。3 度/秒を超えると警告
- 重さ（1コマ。900×900・同梱の地球）: 回転なし 約 22ms（地球の点の層を使い回す）、spin / turn 中 約 60ms（毎コマ点の層を描き直す）。plate 1400×700・night で 約 30ms。1 秒ぶんで 1.5〜2.5 秒
- 使い方の推奨: 1回 10 秒以内、1本の動画に 2 回まで。色は白・灰・赤だけ、国境と国旗は描かない。都市の点は実データに見えるので、出典を書くか「模式図」と明記する

### 後戻り型の正規表現の照合を描く（regex_trace / regex_count / regex_view）

正規表現の後戻り（ReDoS）を、**実際に記録した手順から**描く。手で数えたコマ列は要らない。

```python
tr = regex_trace(r"\s+$", "x" + " " * 10 + "x")       # 照合を1手ずつ記録（教育用のモデル）
tr.span, tr.counts["tests"]                            # → None, 123（= n²+2n+3）
big = regex_count(r"\s+$", lambda n: "x" + " " * n + "x", 20000, count="matches")
big.value, big.method, big.formula                     # → 200010000, 'poly', '(n^2 + n)/2'

# 開始位置ごとに1行。帯が最後の x で赤く止まり、行が積もって三角形になる。
# 最後にカウンタを regex_count の値まで回す（count_label の数は「このモデルの回数」）。
# 書式は (拍と回転の間, 最後) の組: 回している間は桁が動き、回し終えたら「約2億回」
fig = regex_view(tr, view="rows", count="matches", at=[1.2], count_to=big.value,
                 count_label=("{n:,} 回", "約{oku:.0f}億回（模式）"))
fig @ 3.0
fig.time() <= move(x=0.5, y=0.6, anchor="center")
# 三角形ができた所から回転だけを見せる場面は、同じ図を切り出す（素材時間のスライス）
# roll = regex_view(…同じ引数…); roll[roll.figure.beats_end:] @ 9.0

# 2つの .* が xxxxx を分け合う位置を、= の判定ごとに1拍で（56 拍がちょうど 12 秒を埋める）
fig2 = regex_view(regex_trace(r".*(?:.*=.*)", "xxxxx"), view="tape",
                  beats="literal:=", duration=12)
```

- `regex_trace(pattern, text, mode='search'|'match'|'fullmatch', max_steps=200000)` → `RegexTrace`（`span`・`groups` は `re` と同じ。`counts`（steps / tests / matches / backtracks / attempts）・`nodes`（式の部品）・`quantifiers`（greedy / lazy / possessive）・`events`（`(kind, pos, node, ok, spans)`。kind は start / test / assert / backtrack / match / fail、spans は量指定子の取り分 `(番号, 開始, 終了)`）・`attempts`）
- 対応する構文: リテラル・エスケープ・`.`・`\s \S \d \D \w \W`（Python の str パターンと同じ）・`[...]`・`* + ? {m} {m,} {,n} {m,n}`（n は 1000 まで）とその最小・所有（`*+` など）・`( )` `(?: )` `(?> )` `|`・`^ $ \A \Z`・先読み・後読み（固定幅）。後方参照・名前つきグループ・フラグ・`\b`・条件分岐・`\p` は「regex_trace が対応しない構文」の ValueError（黙って違う動きはしない）
- エンジンは標準ライブラリだけの後戻り型 VM（`scriptvedit.regex_vm`）。**再帰を使わない**（空白 5 万個でも RecursionError にならない）。search は開始位置を 0 から全部試し、処理系の最適化（必須文字の先読み・自動所有化）はしない。`re` との一致は seed 固定の差分ファジング（3,000 組 × 3 モード）で確かめている
- 1手の数え方: `tests`（1字の判定と ^ $ 先読み等の assert）/ `matches`（成功した1字の判定）/ `backtracks` / `steps`（命令数）/ `attempts`。x＋空白 n 個＋x に `\s+$` は tests = n²+2n+3・matches = n(n+1)/2
- `regex_count(pattern, make_text, n, count=, direct_limit=3000000, fit_ns=None)`: 手数が direct_limit 以内なら直接数え、超えるなら小さい n（既定 8〜64）で数えて次数1〜4の多項式を Fraction で当て、使っていない2点で検算してから外挿する（`^(a+)+$` のような指数型は ValueError）。純 Python で約 300〜600 万手/秒
  - 上限のある量指定子（`\s{1,100}$` など）は、取り分が上限に届くと増え方が変わる（空白 100 個までは2次、その先は1次）。式の中の有限の回数・幅の最大が fit_ns の最小以上なら、既定の fit_ns を「最大 + 8, 12, …」へずらし（明示した fit_ns が上限以下なら ValueError）、`{m,n}` 型の量指定子がある式は fit_ns の最大の2倍の n でも検算する（外れたら ValueError）
- `regex_view(trace, view='tape'|'rows'|'both', beats='test'|'backtrack'|'attempt'|'literal:<字>'|'step', pace=, at=, duration=, window=, show=, count=, count_label=, count_to=, ...)`:
  - tape: 式の箱・文字のマス（空白は ␣）・取り分の下線（1本目 実線・2本目 破線・3本目 点線。式の箱の下にも同じ線）・照合位置 ▼・開始位置 ▲ と縦線・失敗の赤い ×（0.25 秒）・後戻りの弧の矢印（0.3 秒）
  - rows: 開始位置ごとに1行。成功した判定は白い帯、失敗は赤の縦棒、assert の失敗は赤の点、同じマスを1つの試行の中で何度も（成功して）判定すると明るく（熱。一番熱い帯が白）。1判定で終わった試行は `trivial='mark'` で点だけ
  - 拍: 最初の3拍は 0.6 秒、以降 0.82 倍ずつ速くなり、最後は1コマに4拍（カウンタだけが走る）。拍と拍の間の event はまとめて反映。`at=[秒]` で先頭の拍を語の時刻に合わせる。`duration` を渡すと拍がちょうどその長さを埋める（最後の拍 + hold_end + count_roll = duration。多ければ速く、少なければ加速を緩め、それでも余れば1拍を最大 1.2 秒まで延ばして残りは最後の絵）。`pace` を明示したときの `duration` は長さだけを決める
  - × と矢印は次の拍で消える（重ならない）。間隔の短い拍は照合位置が滑り終わるのを待たずに × を出すので、中くらいの速さでも × が見える。1コマに3拍を超える区間では描かない（点滅させない）
  - `count_label` は書式1つか (拍と回転の間, 最後) の組。`'約{oku:.0f}億回'` のような粗い書式1つだけだと拍の間「約0億回」のまま動かない（警告する）ので組にする。三角形ができた所から回転だけを見せるなら `fig[fig.figure.beats_end:]`
  - 戻り値の `obj.figure`: `beat_times`・`beats_end`・`count_final`・`n_frames`・`size`・`count_at(i)`・`count_text(i)`（コマ i のカウンタの文字列。字幕との突き合わせに）・`cell_box(p)`（文字 p のマスの矩形。注記を置く位置に）
  - 長い文字列は `window=(開始, 終了)` の範囲だけをマスで描き、外は「…」に畳む（48 字を超えて window を省くと ValueError）。拍は 5,000 まで。図は幅・高さとも 4096px まで（超えると ValueError）、Project の画面より大きいと警告（cell を小さくするか window で切る）
  - 色は framekit の PALETTE（赤は失敗と停止だけ）。図の下に panel の面を敷く。文字は cell × 0.61（64 で 39px）で `p.audit()` に申告する
- 重さ（1コマの描画。平均・括弧は最悪）: xxxxx の tape 約 3.5ms（8ms）、空白 10 個の rows 約 1ms（6ms）・both 約 2ms（12ms）、空白 20 個・cell=64 の both（1652×970）約 6ms（27ms）、空白 30 個・cell=48 の both（1812×914）約 10ms（29ms）。最悪は開始位置が変わってマスの列を描き直すコマ。1 秒ぶんの生成は ffmpeg の符号化込みで約 0.1〜0.3 秒
- 画面の数は「このモデルの回数」で、処理系の手順数とも実演の秒とも別物。count_label に「模式」など分かる言葉を添える

### 番号つきの箱の列（slots）

配列・バッファ・設定の項目のような「番号つきの箱の列」に、塊が流れ込んで上限を超えてあふれる・針が範囲外を読みにいく・矢印の先に相手がいない・文字として比べて入れ替える、を時刻つきで積んで透過動画 Object にする（framekit.build。`frames()` と同じ qtrle の .mov）。

```python
# 「200」の棚から、倍に膨らんだファイルがあふれる
s = slots()
s.row("shelf", 200, label="項目の上限", capacity=200, elide=(6, 3))   # 1 2 3 4 5 6 … 198 199 200
s.fill(1.0, "shelf", 400, dur=2.5)    # 上限の線に当たった分は赤くなって外へ落ちる（間隔を空けて描ける分だけ描く）。「上限 200」の右に入った数「400件」
s.halt(3.8)                           # 全体を不透明度 0.45 へ（style="freeze" は以後動かない）
s.build().time() <= move(x=0.5, y=0.5, anchor="center")

# 21 個と 20 個。21 個目には相手がいない
t = slots()
t.row("rule", 21, label="ルールの型:\n受け取る項目 21個")
t.row("input", 20, label="プログラムが\n渡す項目: 20個", ghost=[21])   # 21 は点線の箱
t.put(0.5, "rule", 21, "条件")                     # 札が上から降りて収まる
t.link(1.2, ("rule", 21), ("input", 21))           # 先が ghost なので矢印の先と点線の箱が赤く
t.read(2.0, "input", 21, dur=1.2)                  # 針が等速で歩き、斜線の区画「?」に入って赤く
fig = t.build()
fig.figure.cell_xy("input", 21)                    # 箱の中心の px（注記を置く位置に）

# [1, 2, 10].sort() は文字として並べる
u = slots(cell=96)
u.row("arr", 3, values=[1, 2, 10], index_base=0)
u.to_str(0.4, "arr")                               # 数の両側に引用符が現れる
u.compare(1.0, "arr", 1, 2, by="str")              # 最初に違う字（'2' と '1'）を赤で囲み、不等号
u.swap(2.2, "arr", 1, 2)                           # 上下に分かれた弧で入れ替わる
```

- 出来事: `fill(t, row, count, dur=1.0, order='left', source='right', spill=True, spill_visible=24)` / `read(t, row, index, dur=0.3)` / `link(t, (row_a, i), (row_b, j), dur=0.5, label=None)` / `put(t, row, index, text, dur=0.4)` / `set(t, row, index, value, dur=0.3)` / `mark(t, row, index, color='accent', style='frame'|'fill')` / `unmark` / `to_str(t, row)` / `compare(t, row, i, j, by='str'|'num', dur=0.6)` / `swap(t, row, i, j, dur=0.5)` / `halt(t, style='dim'|'freeze', dur=0.3)`。時刻は Object の先頭からの秒
- 行: `row(name, n, label=, values=, index=True, index_base=1, capacity=, elide=(先頭, 末尾), ghost=[番号], offset=, overflow_label='total')`。elide なしは 64 箱まで（n は 100,000 まで）。capacity・ghost・出来事が触る番号の前後は畳んでも見せる。番号の列は全行で揃う（offset は cell 単位のずれ）
- あふれの札（`overflow_label`。fill があふれたら「上限 N」の右、線の外にも箱があれば番号の帯の下に accent で出し、あふれが線に着くたびに数が増える）: `'total'`（既定。入った数の合計「400件」）/ `'over'`（上限を超えた数「+200」）/ `'undrawn'`（描かなかった塊の数「+190」。描かない塊があるときだけ）/ `None`（出さない）か、書式の文字列（名前は `total` / `over` / `undrawn` / `limit`。例 `'{total:,}件（上限{limit}）'`。数として `{undrawn}` だけを使う書式は描かない塊があるときだけ出し、`{limit}` だけの書式は最初のあふれから出す）。以前の「+N」は描かなかった数だけを数え、400 を入れて「+190」と出て数として誤解された
- 範囲外の番号を受けるのは read と link だけ（行の外の斜線の区画「?」へ。区画は一番外の ghost のさらに外。ghost の番号はその点線の箱）。put / set / mark などの範囲外・負の時刻・無い行・values の長さ違い・`build(duration=)` より後に終わる出来事は ValueError
- fill のあふれは、描く塊どうしが線に着く間隔を 0.12 秒以上に保つ（足りなければあふれの時間を fill の 65% まで延ばし、それでも入らない分は描かない。`spill_visible` は描く数の上限。数はあふれの札が示す）。塊は縁取りつきで、重なっても 1 つずつ見分けられる。count は 1,000 万でもよい（1 個ずつの表を作らない）
- compare の直後に swap / set / put を続けてよい（持ち上げた箱を下ろしながら動かすので、字は箱から外れない。赤い枠と不等号は字が動き出す前に消える）
- 箱の中の字は行の中で大きさを揃える（数字・ASCII は等幅の mono_font）。ただし 1 つの長い字のために行全体を 32px（value_size がそれより小さければ value_size）未満へは縮めず、その字だけを縮める。elide で隠れた箱の values は大きさの計算にも入らない。札（put）は行の端なら外側へ広がり、左端の札のために箱の列を右へ寄せる（ラベルを隠さない）
- 行をまたぐ矢印の札は行の間に置き（その高さを空ける）、矢印の線と鏃に掛からない所へ寄せる。複数行のラベルも上下の行と重ならないよう行の間を空ける
- 色は framekit の PALETTE（白・灰・赤）に fill / pointer / card / halo / bg を足したもの。`colors={"fill": "#5b8bd6"}` で上書き。ghost の番号も muted（読ませる字）
- 文字は `p.audit()` に申告する（size で縮めた倍率込み）。既定（label 36・番号 32・箱の字 cell の半分）で audit の warning は出ない。左右に並べるときは size を縮めず `cell=48, gap=12, value_size=32` にする（3 桁の番号も 32px のまま入る）
- 戻り値の `obj.figure`: `cell_xy(row, index)`・`rows`・`size`・`scale`・`duration`・`frame(t)`（1コマの RGBA 配列）・`state(t)`（箱の中の塊の数・あふれの数）
- 重さ: 1コマの描画は平均 1〜3ms（1080p 全面でも最大 15ms）。1 秒ぶんの生成は ffmpeg 込みで 0.2〜0.3 秒
- 描かないもの: 番地・16進のダンプ・命令、特定の OS の停止画面の意匠、点滅・ノイズ

### 点と線の図の上を流れるパケット（flow_graph / flow_tree）

ネットワーク・配信・送金・感染の図（ノードと辺）を描き、パケットを流す・一斉に配る・ノードを灰色にする・辺を切る、を秒で書いて透過動画 Object にする（framekit.build。`frames()` と同じ qtrle の .mov）。**模式図であって実際の経路ではない**（「模式図」の注記は呼び出し側が付ける）。

```python
# ロードバランサーのヘルスチェック。遅れたサーバーを外す
g = flow_graph({"lb": {"label": "ロードバランサー", "shape": "box"},
                "s1": {"label": "サーバー1"}, "s2": {"label": "サーバー2"}},
               [("lb", "s1"), ("lb", "s2")], layout="layered")
g.send(0.5, ["lb", "s1"]); g.send(0.9, ["s1", "lb"])     # 送って戻す
g.send(0.5, ["lb", "s2"]); g.send(0.9, ["s2", "lb"], fate=("drop", 0.5))   # 戻りが途中で消える
g.state(2.0, "s2", dim=0.6, mark="x")                    # 灰色にして ×
g.cut(2.0, ("lb", "s2"))                                 # 辺が dim の破線になって薄れる
fig = g.build()                                          # 最後の出来事の終わり + 1 秒
fig.time() <= move(x=0.5, y=0.45, anchor="center")

# 35 件のうち 30 件が NY の手前で止まって並び、5 件が通る
h = flow_graph({"bb": {"pos": (200, 420)}, "ny": {"pos": (1150, 420)}, "ph": {"pos": (1560, 220)}},
               [("bb", "ny", {"curve": 0.18}), ("ny", "ph")])
h.send(0.3, ["bb", "ny", "ph"], n=30, every=0.09, fate=("stop", "ny"))
h.send(3.2, ["bb", "ny", "ph"], n=5, every=0.09)

# 1 → 50 → 3000 へ一斉に散る（葉が 500 を超える段は点の塊。amount で直径が √(量)）
t = flow_tree([1, 50, 3000], t=0.5, amount=400000)
fig3 = t.build()
fig3.figure.arrival["L2_0"]      # 葉に届く秒（字幕・効果音の時刻合わせに）
```

- `flow_graph(nodes, edges=(), *, layout='given'|'layered'|'radial'|'rings', direction='down', size=None, padding=40, node='dot'|'box', node_radius=10, box=(240, 72), edge_width=3, curve=0.0, colors=None, font=None, weight=None, label_size=36, label_pos='auto', seed=0)`。nodes は `{名前: {'pos', 'label', 'shape', 'layer'}}` か名前のリスト。`nodes()` と `edges()` を持つグラフ（networkx など）も duck typing で読む（networkx は import しない）。edges は `(a, b)` か `(a, b, {'delay': 秒, 'curve': 0.2})`
- `curve`（図全体の曲がり）は、辺の集まるノードで自動で弱める: ノードから出る辺の組・入る辺の組ごとに、弦の向きの角の間（中央値 g）の半分までしか端で傾かないよう、curve を tan(g/2)/2 までにする。上限は辺の本数ではなく隣の辺との角の間で決まる: 周りに均等に散った辺（radial の `flow_tree`）なら 3〜4 本の組で 0.3 はそのまま、40 本なら約 0.04。片側へ開く扇（layered や given で子が同じ側に並ぶ）は角の間が狭いので少なくても弱まる（layered の根から子へ 2 本で約 0.28、3 本で約 0.16、4 本で約 0.11）。どの辺も同じ向きに曲がって隣の辺へ倒れ込み、渦（風車）に見えるのを防ぐ（`flow_tree([1, 40, 3000], curve=0.3)` で起きた）。辺ごとの `'curve'` は指定どおり
- 配置: given は pos の通り。layered は BFS の段（`'layer'` で上書き）で、段の中の順は重心法を2往復して交差を減らす。radial は根が中心で、角度は部分木の葉の数に比例。rings は段ごとの同心円。layered / radial で根から辿れないノードがあると ValueError。配置も経路（BFS・Dijkstra）も自前なので、ライブラリの版で結果が変わらない
- 出来事（秒は Object の先頭から）:
  - `send(t, path, n=1, every=0.12, speed=700, color='fg', size=7, trail=0.25, fate='pass', label=None)` → 各パケットが止まる秒のリスト。パケットは辺を細かい折れ線にした道の上を弧長で等速に進む（曲がった辺でも辺から外れず、隣り合うコマの移動量がそろう）。size は直径。`fate=('stop', 名前)` はそのノードの手前で止まって accent になり、前のパケットとの中心の間隔 1.6 ×（2つの直径の平均）で手前へ並ぶ（大きさが違っても重ならない。列が手前のノードに届いたら、そのノードを跨いでさらに手前の辺へ並ぶ）。`('drop', 0.6)` は最初の辺の 60% で止まり、0.4 秒で薄れて消える。`label` は先頭のパケットと一緒に端数の位置で動く文字。ノードの枠・点・ノードの文字・先に出た札には重ねない: 重なるコマでは隠し、0.15 秒手前から薄れて、離れてから 0.15 秒で現れる（出るときも薄く現れ、pass は着く手前で薄れる。見える間が 0.3 秒に満たなければ出さない。どのコマでも出せない札は警告する）。置き場所は画面の上・進行方向の左右・画面の下から、パケットが見えている間（止まったパケットは終わりまで）に隠れる間・辺が下を通る間・キャンバスの外へのはみ出しが最も少ないものを札ごとに選び、動く間は保つ（ぱたぱた入れ替わらない。進行方向の左右に置く札は曲がり角で回り込む。斜めの辺でも辺が札を貫かない）。側が変わりうるのは、途中のノードの先（次の辺へ移る所）で1回と、`fate=('stop', …)` で止まる所で1回だけで、変わる所では札が一度消えてから現れる（止まった札は、パケットより後ろの進行方向の左右に、札の前の端をパケットの前の端にそろえて残る。中央のままだと止まる先のノードに掛かる）。途中の box の中（文字の上）ではパケットを描かない（手前で薄れて消え、出る辺の端から現れる）。途中の点を通る間はパケットの暗い縁を消す
  - `broadcast(t, root, hop=0.35, color='accent', packets=True, ripple=True)` → `{名前: 届く秒}`。辺の delay（無ければ hop）で Dijkstra。届いた順に色が変わり、波紋が出る
  - `state(t, node, color=None, dim=None, mark='x'|'check'|'none'|None, dur=0.25)`・`cut(t, (a, b), dur=0.3)`
- `flow_tree(levels, *, t=0, hop=0.4, layout='radial', amount=None, **kw)`: 名前は `'L段_番号'`。葉が 500 を超える段は点の塊（直径 5px・seed で散らす）で、そこへの辺は細く薄い扇、broadcast のパケットは親1つあたり 4 個に束ねる（量は束ねた分の合計）。子が 64 を超える親からの辺も扇にする（何百本も 3px で描くと根の周りが白く潰れる）。`curve=` を渡すと扇の辺も曲線で描く（パケットは同じ曲線の上を走る）。子の多い親からの辺の曲がりは自動で弱まる（下の `curve` の項）
- 絵: 辺は line 色の 3px、ノードは fg の点か角丸の枠（文字は中）、パケットは panel の縁つきの点と、trail 秒ぶんの α が下がる点3つ。届く・止まる・外す瞬間に波紋（半径 1.0 → 2.2 倍、0.4 秒）。文字は 30px 以上（下回ると ValueError）で、重なり・はみ出しは警告する。点の文字（`label_pos='auto'`）は、layered では葉だけを流れの向きに置き、根は逆の側、途中の点は横（down / up は右、right / left は上）。radial / rings は外向き、given は下が第一候補で、配置の後に辺・ほかのノードと重ならない側を選び直す。どの側も辺が通るとき（radial の根など）は、文字の下に panel 色の板を敷いて警告する。色は framekit の PALETTE（`colors=` で上書き）。同時に動くのは白のパケット1系統と赤1色を推奨
- 戻り値の `obj.figure`: `pos`（{名前: (x, y)} キャンバスの px）・`arrival`（broadcast で最初に届く秒）・`size`・`duration`・`levels`・`amount`
- 上限: ノード 5,000・辺 10,000・同時に見えるパケット 2,000（止まって残るものを含む）。超えたら ValueError
- 鍵: ノード・辺・layout の引数・出来事・使う色（fg・accent・line・dim・panel）・寸法（文字があるときだけフォントの内容指紋）と版 `_FLOW_VER`。配置の結果は鍵に入らない。効かない値（n=1 の every）は入れない
- 依存: 構築（`flow_graph()` の呼び出し）にも numpy・opencv-python・Pillow が要る（`pip install "scriptvedit[figures]"`）
- 重さ（1920x1080・1コマの描画。ffmpeg の符号化は別）: 葉 3000＋パケット 200 の broadcast で平均 約 7ms・最大 約 25ms、LB＋サーバー6台で平均 約 3ms（cut の間も 12ms 以下）、35 個の送金で 約 3ms、上限の同時 2,000 個で平均 約 70ms（最初の1コマだけ辺の層を描くので 0.04〜0.9 秒）。1 秒ぶんの書き出しは ffmpeg 込みで LB の図が約 0.3 秒、葉 3000 の木が約 0.9 秒（ほかの重い処理と並行すると 2〜3 倍）。生成が終わると描画の作業領域（1080p で約 100MB）を手放す

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
p.marker("q2.start", "問題2")   # アンカー名はレンダ時に時刻へ解決
p.export_chapters("chapters.txt")

# param: CLI / 環境変数で差し替え可能なテンプレート変数
title_text = p.param("title", "デフォルト")   # --param title=... / SCRIPTVEDIT_PARAM_title
```

- `grid(cols, rows, *, gap=0)` は画像素材のみ。`marker` は `render()` 時にチャプターとして埋め込まれる。`marker` の time にはアンカー名（`"q2.start"` / `"scene:導入"` 等）も渡せ、レンダ時（タイムライン解決の後）に解決される。存在しない名前は候補つきの ValueError
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
p.render("out.png")               # 連番PNG（out.png → out_%05d.png）。常に透過（background_color は無視）
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

- 透過出力（`alpha=True`）は `.webm`（VP9）を推奨。gif / h264 はアルファを保持できない（h264 に `alpha=True` を付けると ValueError）
- 連番PNG は `alpha` の指定に関係なく**常に透過**で書き出す（背景は `color=black@0` で、`configure(background_color=...)` は効かない）。背景色が要るなら全面の背景素材をレイヤーに置く
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
  チャプター（marker）も mux 時に付与される。`normalize_audio(mode="linear")` の
  増幅量はチャンクを起動する前に全編を測って決め、音声レグにも同じ値が入る
- **出力の同一性**: フィルタ文字列が全編レンダと同一のため、エンコード前のフレームは
  一致する。最終出力は H.264 のレート制御が GOP 境界で変わるためビット同一には
  ならないが、視覚的には同一（実プロジェクト2分56秒での実測: フレーム数完全一致・
  PSNR 平均53.8dB / 最低46.0dB・SSIM 0.9996・音声はデコードPCMがMD5完全一致）
- **対応形式**: H.264系（.mp4/.mkv/.mov、draft含む）のみ。gif/webp/webm/連番PNG/
  alpha や `start`/`end` 部分レンダとの併用時は通知の上で従来レンダへフォールバック。
  `draft=True` の縮小はチャンク（映像）だけに掛かり、音声レグには掛からない
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
# 検査ビュー（scriptvedit.viz 統合）。レイヤーは実行しないので render / dry_run / audit の後に呼ぶ
p.render("out.mp4", dry_run=True)
p.inspect("timeline.html")        # HTMLガントチャートを書き出しパスを返す
print(p.inspect())                # 省略時はテキストレポート文字列を返す

# ファイル監視（標準ライブラリのポーリング。変更時に再実行）
watch("main.py", out="out.mp4", interval=0.5, max_cycles=None)

# 品質lint（レンダ前チェック。人間レビュー由来のルール集）
findings = p.audit()              # レポートをprintし findings のリストを返す
p.audit(strict=True)              # warningが1件でもあればRuntimeError（CI向け）
```

- `p.inspect()` はレイヤーを実行しない。`render()`（`dry_run=True` でよい）か `p.audit()` の前に
  呼ぶと、ガントチャートではなく `p.layer()` の登録情報だけの表になる
- `watch(script, out=...)` は起動時に1回、以後は変更のたびに `python <script> <out>` を
  スクリプトのディレクトリで実行する。`out` は**スクリプトの第1引数として渡るだけ**なので、
  スクリプト側が `sys.argv[1]` を読んで `render()` に渡す必要がある（`scriptvedit new` の
  main.py はそうなっている）。監視するのはスクリプトのディレクトリ以下（サブディレクトリを含む）の
  `.py` と素材（画像・音声・動画・フォント・`.html` / `.css` / `.js`・字幕・`.cube`）で、
  `__cache__` / `__pycache__` / `.git` / `output` ディレクトリは見ない。
  `out` の出力ファイルとその一時ファイル（連番PNGなら各フレーム）も監視しないので、
  監視ディレクトリ内へ書き出しても出力の更新で再実行が連鎖しない
  （相対パスの `out` はスクリプトのディレクトリ基準で解決する）

`audit()` はエラーにはせず findings（`{"severity", "code", "message"}` の list）を返す。
`render()` も strict でなくても最後に audit を回し、指摘があれば `[audit] warning N / info M` の
1行サマリを出す。ルール:

| code | severity | 内容 |
|---|---|---|
| `text-too-small` | warning / info | 文字が小さい（1080p 換算で 32px 未満は warning、44px 未満は info） |
| `text-no-decoration` | warning / info | 縁取り・影・下地のいずれも無い文字（背景に溶ける）。`configure(background_color=)` で明示した単色の背景だけの上にあり（同じ時間に画像・動画・web が無い）、文字色とのコントラスト比が 4.5 以上なら info。透過出力（`render(alpha=True)`・連番 PNG）は背景色を使わないので、`render()` が回す audit（`strict=True` とレンダ後のサマリ）では warning のまま。単独の `p.audit()` は出力先を知らないので不透明な出力を仮定する |
| `offscreen-placement` | warning | x / y が 0..1 の比率の外で、画面に映らない（Expr は6点サンプルの全点が外のときだけ。px を渡した疑いも案内） |
| `text-overflow` | warning | 推定描画幅がフレーム幅（safe area 5% 差引）を超える |
| `outside-duration` | warning | 表示区間が動画の総尺と交差せず、一度も映らない |
| `font-missing-glyph` | warning | 使うフォント（指定が無ければ自動選択されたもの）に日本語のグリフが無く、豆腐（□）になる |
| `audio-overlap-no-duck` | warning | BGM 役（`duck_under` か `loop` を持つ音声）と、それがダックしていない音声が1秒以上続けて重なる。組の件数と先頭3組を示す。ナレーション同士・ナレーションと効果音のような前景同士や BGM 役同士は数えず、BGM 役が1つも無いときは全ての組を調べる。`sfx()` は各発音区間で判定する |
| `bgm-loop` | info | `loop()` を使っている（つなぎ目が気付かれやすい） |
| `bgm-too-short` | warning | `duck_under` を持つ BGM の実尺が表示区間より短く、途中で無音になる |
| `no-normalize-audio` | info | 音声があるのに `normalize_audio()` が未設定 |
| `quality-hint-ignored` | info | `~` 品質ヒントを付けたが、その op に軽い代替処理が無い（通常と同じ処理になる） |
| `web-content-uninspected` | info | Web/Canvas の内部は静的検査の対象外（`storyboard()` での目視を促す） |
| `morph-sdf-crossfade` | warning | `morph_to`（sdf）の2枚の不透明部が重ならない、または輪郭が取れない（形が動かず、実質クロスフェードになる） |

文字の4項目（`text-too-small` / `text-no-decoration` / `text-overflow` / `font-missing-glyph`）は `text()` 系に加えて
`text_image()` の画像にも効く（`resize` / `scale` の倍率を掛けた画面上の実寸で見る）。自前の PNG・動画・HTML の中の文字は検査されない。

キャッシュ管理・監視は CLI からも実行できる。

```
python -m scriptvedit new myvideo               # プロジェクト雛形を生成
python -m scriptvedit cache --stats             # 種別ごとの件数・サイズ
python -m scriptvedit cache --gc --keep-days 7  # 7日より古い生成物を削除
python -m scriptvedit cache --clear             # キャッシュ全削除
python -m scriptvedit describe                  # 全機能の機械可読マニフェスト
python -m scriptvedit watch main.py --out out.mp4   # 変更のたびに python main.py out.mp4 を実行
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
- VOICEVOX は鍵に「接続先 + エンジンのバージョン」も含める。エンジンに届いたときの値を `__cache__/tts/engine_sig.json`（接続先ごと）に控えておき、**エンジンが止まっているときはその控えで鍵を作ってキャッシュ済みの音声を使う**（警告は1回だけ）。キャッシュに無い台詞の合成が要るときだけ `ConnectionError` になる。エンジンに届けば常に実測のバージョンが優先され、控えも更新される。**この控えが効くのは `backend="voicevox"` を明示したときだけ**: 既定の `backend=None` はエンジン停止中は自動選択で `edge` に切り替わる（edge-tts があれば別の声で合成され、無ければ `RuntimeError`）ので、VOICEVOX のキャッシュは使われない
- **読みと間の調整**（`tts()` の引数。`voice()` / `narrate()` にもそのまま渡せる）。既定の `None` は「触らない」で、鍵も出力も指定しないときと同じ。指定した項目だけが鍵に入る
  - `readings={"金": "カネ"}`: 合成に渡す文だけ語を読み替える（画面の文字は変えない。長い語が優先・1回だけ置換。どのバックエンドでも使える）
  - 以下は **VOICEVOX 専用**（audio_query を書き換える。ほかのバックエンドに渡すと `ValueError`）: `pre_silence` / `post_silence`（文の前後の無音・秒。エンジン既定 0.1）、`pause_length`（句読点の間を固定秒に）/ `pause_scale`（間の倍率。対応していない古いエンジンでは `RuntimeError`）、`intonation`（抑揚）、`volume_scale`（音量）、`kana`（AquesTalk 風カナで読みとアクセントを丸ごと指定。例 `"アタイワ'/カラノ'/ハイレツダッタ'"`。指定すると元の文は音声にも鍵にも効かない）
- **語の時刻**: `scriptvedit.tts.tts_marks(text, ...)`（引数は `tts()` と同じ。VOICEVOX 専用）は、その wav の中で各文字が読まれ始める秒を返す。図や強調を「この語が読まれた瞬間」に合わせるのに使う

  ```python
  from scriptvedit import tts as T
  kw = dict(backend="voicevox", speaker=13, readings={"金": "カネ"})
  wav = T.tts("金は戻らなかった。答えは、まだ無い。", **kw)
  m = T.tts_marks("金は戻らなかった。答えは、まだ無い。", **kw)
  m.time_of("答えは")       # 読まれ始める秒（wav の先頭から。Object(wav) @ t0 なら t0 + これ）
  m.span_of("答えは")       # (開始, 終了)
  m.pauses                  # 句読点の間 [{"start", "end", "index": 文字位置}]
  m.precision_of("答えは")  # "pause" / "start" / "kana" / "approx"
  ```

  - audio_query のモーラごとの長さを、エンジンと同じ 93.75 フレーム/秒の丸めで積む。計算した長さは実 wav とサンプル単位で一致する（VOICEVOX 0.25.2 で実測）。間の終わりは `silencedetect`（-35dB）が検出する音の出始めより 0.01〜0.07 秒早い（次の子音の立ち上がりが静かなぶん）
  - 精度: 句読点（`"pause"`）と、文頭・間の直後の語（`"start"`）は確実。仮名（`"kana"`）はモーラ単位。漢字・英数字の途中（`"approx"`）は前後の対応点の間を文字数で按分した近似。`readings` で仮名に開くと対応点が増えて精度が上がる
  - 記号と間の対応: 間に読まれる文字の無い記号の並び（`？　` `。「` `」。`）は1つの間に対応し、`pauses` の `index` は並びの中の最初の句読点。文頭の括弧と文末の記号は間に対応させない。どの記号の間か決められないとき（記号の並びが間より多く、仮名でも決まらない）は `index` が `None` になり、その前後は `"approx"` へ落とす（`index` がすべて決まっていれば、近似の文字が間をまたぐことはない）
  - 合成に使ったクエリを wav と同じ鍵の `<鍵>.marks.json` に控えるので、**エンジンが止まっていても返せる**。控えの無い文だけ audio_query を問い合わせる（wav は合成しない）
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
- `karaoke(lines, *, style=None, fontsdir=None)`: `lines` は `(start, end, "歌詞")` または `(start, end, "歌詞", [語ごとの秒数])`。`word_durations` 省略時は行内の語へ `(end-start)` を均等割り。`style` で font/size/primary(発音済み色)/secondary(未発音色)/outline/alignment/margin_v 等を上書き。**フォント描画は libass 依存**（環境のフォント有無で見た目が変わる）

### ビート同期（beat_sync / scriptvedit.beat）

音声のビート（拍）を検出し、キーフレームやカット点に同期させる。ビート検出は librosa 非依存の `scriptvedit.beat`（numpy/scipy のみ）。

```python
# beat_sync: 音声からビート時刻を検出しDSLに統合
res = beat_sync("bgm.mp3", min_bpm=60, max_bpm=200)
# res = {"bpm": float, "beats": [秒,...], "onsets": [秒,...], "duration": float}

# 拍ごとに scale が跳ねて戻るキーフレーム（beats_to_keyframes → keyframes）
# beats は秒、keyframes の時刻は u（0..1）なので、表示尺で割ってから渡す
# （obj と BGM がどちらもタイムライン 0 秒から始まる場合の例）
from scriptvedit.beat import beats_to_keyframes, snap_times
dur = 8.0
beats_u = [b / dur for b in res["beats"] if b < dur]
kf = beats_to_keyframes(beats_u, [1.15], decay=0.12 / dur, base=1.0)
obj.time(dur) <= scale(keyframes(*kf))

# カット点を最近傍ビートへスナップ（snap_times）
cut_times = snap_times([2.0, 4.3, 6.1], res["beats"])
```

- `beat_sync(audio_source, *, min_bpm=60, max_bpm=200)`: 解析結果は 素材FFP+bpm範囲 をキーに JSON キャッシュ。**numpy/scipy が必要**（未導入時は導入手順付きの日本語エラー）
- `beats_to_keyframes(beats, values, *, offset=0.0, decay=None, base=None, t_start=None, t_end=None)` は `keyframes(*result)` に渡せるフラット列 `(t0, v0, t1, v1, ...)` を返すデータ整形ヘルパー（scriptvedit 非依存）。`decay` 指定で各拍がパルス形（跳ねてすぐ `base` に戻る）になる。**単位は変換しない**（入力の時刻・`offset`・`decay`・`t_start`/`t_end` がそのまま出る）ので、秒の `beats` をそのまま渡すと `keyframes` が u として読んで拍が合わない。上の例のように表示尺で割った値を渡す（`obj` の開始が BGM とずれているなら、割る前に開始時刻を引く）。`keyframes` は最大128点なので、`decay` 付き（1拍2点）なら64拍まで
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
| `compute` | `compute(duration=None)` | タイムライン外で素材生成（PNG、`duration` 指定時は FFV1 の .mkv） |
| `length` | `length()` | 加工後の再生時間を返す（trim/atempo反映） |
| `split` | `split()` | `(VideoView, AudioView)` を返す |
| `grid` | `grid(cols, rows, *, gap=0)` | 画像を cols×rows に複製配置する Transform を足す（`tile(obj, cols, rows, gap)` も同じ） |
| `from_project` | `Object.from_project(sub_project, *, cache="auto")` | サブ Project を透過 webm に焼いた1つの Object を返す（staticmethod。「合成・コンポジション」節） |

プロパティ: `has_video`, `has_audio`, `source`, `audio_source`, `duration`, `start_time`, `priority`
（`audio_source` は音声を取り出す素材のパス。チェックポイント等で `source` が映像専用の中間物へ
差し替わった後も、音声は元素材から取るためここを見る。差し替えが無ければ `source` と同じ）

## render の詳細

### render

```python
p.render(output_path, *, dry_run=False, timeout=None,
         start=None, end=None, draft=False, alpha=False, strict=False,
         parallel=None)
```

`parallel=N` は時間分割並列レンダ（→「時間分割並列レンダ」節）。

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
寸法依存経路は実レンダテスト（`pytest tests/test_real_render.py --realrender` /
`tests/render_all.py`）でカバーする。

`dry_run` が返すのは「**キャッシュが空の状態で何を実行するか**」で、`__cache__` の中身には
依存しない。実レンダで checkpoint 等が実体化した後でも同じコマンドを返すので、
キャッシュを消さずにスナップショットを回してよい。**例外はレイヤーキャッシュの
`cache="auto"` / `"use"`** で、これはキャッシュの有無・鮮度を見て「キャッシュを再生するか、
レイヤーを実行し直すか」を決めるため、`dry_run` の出力もキャッシュの状態で変わる
（`"use"` はキャッシュが無ければ `dry_run` でも `FileNotFoundError`）。

## 開発者向け情報

ライブラリを「使う」だけなら読まなくてよい。リポジトリ自体を触る人向け。

### ディレクトリ構成

本体は `src/scriptvedit/` の59モジュール（合計約4.7万行）のパッケージ。

```
ScriptVEdit/
├── src/scriptvedit/     パッケージ本体（59モジュール）
│   ├── project.py       Project / render / ffmpegコマンド構築
│   ├── checkpoint.py    チェックポイント計画・ベイク（project から抽出した自由関数）
│   ├── layercache.py    レイヤーキャッシュの鮮度判定・生成・再生
│   ├── parallel.py preview.py  時間分割並列レンダ / thumbnail・storyboard
│   ├── chapters.py params.py   マーカー・チャプター出力 / テンプレート変数
│   ├── objects.py       Object / Transform / Effect / group・tile（Group）
│   ├── timeline.py      anchor / pause / scene（`>>` の連結とアンカーの重複検査も）
│   ├── context.py      レンダ中の Project の参照（依存ゼロの葉。循環 import を防ぐ）
│   ├── warn.py         レンダ警告の集約（同じく依存ゼロの葉）
│   ├── effects/         basic / visual / composite / paths / time / terminal
│   ├── filters/         video / audio フィルタ生成
│   ├── expr.py easing.py  Expr式ビルダー・イージング
│   ├── expr_scan.py     Expr の解析（区分線形の展開・選んだ枝だけを辿る評価。scale の pad と native fade の判定が使う）
│   ├── cache.py ffmpeg.py media.py  キャッシュ鍵・ffmpeg実行・probe
│   ├── formula.py       数式レンダ（formula / formula_lines、KaTeX同梱）
│   ├── textimage.py     文字を透過 PNG に焼く（text_image。PIL。部分的な色・太さ、可変フォント、折り返し）
│   ├── stillseq.py      絵の列を1本の動画にまとめる（stills / frames）
│   ├── framekit.py      図解アニメの共通部品（鍵・点と線の描画・文字・時刻。frames の上。内部モジュール）
│   ├── fx_regex.py regex_vm.py  正規表現の照合を描く（regex_view） / 手順を記録する照合器（regex_trace / regex_count）
│   ├── fx_slots.py      番号つきの箱の列（slots。上限の線・あふれ・範囲外を読む針・比べて入れ替え）
│   ├── fx_textmove.py   文字列の組み替え（text_transition / odometer）
│   ├── fx_flow.py       点と線の図の上を流れるパケット（flow_graph / flow_tree）
│   ├── fx_globe.py      点で描いた地球と世界地図（globe。正射影・正距円筒・弧・昼夜）
│   ├── text.py audio.py web.py      テキスト・karaoke / オーディオ（voice・narrate・sfx・beat_sync 等） / web Object・テンプレート
│   ├── morph.py morph_cli.py  モーフィング・パーティクル生成 / その CLI 入口
│   ├── morph_flight.py  粒子の輸送モーフ（fly_to の本体）
│   ├── tts.py           音声合成エンジン層（tts() / speakers。VOICEVOX / edge-tts / SAPI。voice・narrate 本体は audio.py）
│   ├── beat.py          ビート検出エンジン（beat_sync の実体・beats_to_keyframes / snap_times）
│   ├── viz.py           タイムライン検査・可視化（Project.inspect）
│   ├── testkit.py       SSIM によるレンダ結果の視覚検証
│   ├── plugins.py       プラグイン機構（@effect_plugin）
│   ├── manifest.py manifest_data.py cli.py  describe の導出エンジン / 手書き補助テーブル / CLI
│   ├── scaffold.py      プロジェクト雛形生成（scriptvedit new）
│   ├── assets.py        素材パス解決（asset / here / layer、共有ライブラリ取り込み）
│   ├── templates/       テンプレートHTML + vendor/katex（同梱、CDN参照なし）
│   └── data/            globe の陸地の既定（Natural Earth 1:110m の陸のマスク。パブリックドメイン。出典は NOTICE.md）
├── assets/              素材（images/ video/ audio/）
├── tests/               pytest（スナップショット/エラーケース/実レンダ等）
│   ├── layers/          レイヤー定義（testNN_*.py）とフィクスチャ
│   ├── snapshots/       ffmpegコマンドのスナップショット
│   └── golden/          図解アニメ・fly_to の金型（コマの PNG と版。textmove/ に自作のテスト用フォント）
├── plugins/             サンプルプラグイン（cwd/plugins は自動読込）
└── scripts/             開発用スクリプト（素材の生成・import の検査・globe の陸のマスク作り）
```

### テスト

pytest で実行する（どのディレクトリからでも可。`cd tests` は不要）。

```
pytest tests/                             # 全テスト（スナップショット/エラーケース/素材解決/雛形生成/堅牢性/フォント解決。件数は pytest --collect-only 参照）
pytest tests/test_snapshot.py             # スナップショットのみ
pytest tests/test_errors.py -k plugin     # 名前で絞り込み
pytest tests/test_snapshot.py --snapshot-update   # スナップショット再生成
pytest tests/test_fx_slots.py -k golden --golden-update   # 図の金型（tests/golden/）の作り直し（絵を目視してから）
```

図解アニメと `fly_to` の絵は金型（`tests/golden/<種類>/*.png`）と突き合わせる。金型には描画の版定数が
記録されていて、`--golden-update` でも**版を上げずに絵だけ変わったものは書き換えずに失敗する**
（描き方を変えたら版を上げてから作り直す）。金型と別のフォントの環境では文字を含む金型だけ skip する。

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

`render(dry_run=True)` が返すのは「**キャッシュが空の状態で何を実行するか**」で、
`__cache__` の中身には依存しない。したがって実レンダの後にキャッシュを消さずに
スナップショットを回してよい（以前は消す必要があった）。例外はレイヤーキャッシュの
`cache="auto"` / `"use"` で、キャッシュの有無で再生か再実行かが変わる（→「dry_run」節）。

## ライセンス

[MIT License](LICENSE)。同梱の `assets/` はすべて自作のテスト用素材で（`scripts/generate_test_assets.py` が生成）、コードと同じく MIT が適用されます（[ASSETS.md](ASSETS.md)）。同梱している第三者のものは次の2つだけで、それぞれの条件に従います。

- `src/scriptvedit/templates/vendor/katex/` の KaTeX: `formula()` 用に同梱したライブラリ（MIT。同ディレクトリのライセンスに従います）。
- `src/scriptvedit/data/ne_110m_land_1440.png`（`globe()` の陸地の既定）: [Natural Earth](https://www.naturalearthdata.com/) の 1:110m の陸から作ったもので、パブリックドメインです（Made with Natural Earth. 出典と元データの SHA-256 は同じフォルダの `NOTICE.md`）。同梱の素材・データ（`assets/` と `data/`）の「全部自作」の唯一の例外です。

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