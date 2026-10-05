# CLAUDE.md — コーディングAI向けの作業ガイド

このリポジトリで作業する AI（Claude Code / Grok CLI 等）が、最初から正しい前提で
動けるようにするための文書。**まず「2. 最初に読むもの」を実行すること。**

## 1. プロジェクト概要

Python の DSL で動画を構成し、ffmpeg でレンダリングするライブラリ。

- **1ファイル = 1レイヤー**。`main.py` が構成（設定・レイヤー順・出力）だけを持ち、
  各レイヤー `.py` が素材とエフェクトを宣言する。
- 演算子オーバーロードによる DSL: `<=` 適用 / `&` Effect連結（AudioEffect 同士も `&`。
  Effect と AudioEffect は混ぜられず TypeError なので別々に `<=`）/ `|` Transform連結 /
  `~` 品質ヒント / `+` force / `-` cache off。`~` は内容を削除せず、軽い代替を
  持たない op では通常と同一の処理を警告なしで行う（音声削除は `adelete()`）。
  タイムライン系: `obj[2:5]` 素材切り出し（素材時間）/ `obj @ 12` 絶対配置
  （タイムライン時間・非進行）/ `a >> b` 直後連結（pause.time() を挟める）/
  `obj * n` リピート / `-obj` 逆再生（`reverse()` の糖衣）。
  **`-` は被演算子で意味が変わる**: Transform / Effect（とそのチェーン）なら policy="off"、
  Object なら逆再生。スライス・`* n`・`-obj` は Object を破壊的に変更して同じ Object を返す。
- レイヤー .py の中で作った `Object` は exec 中に `Project` へ**自動登録**される。
  `p.objects.append()` の手動追加はしない（render 時のレイヤー再実行で消える）。
- **タイムラインの順次カーソルはレイヤーごとに 0 秒へリセットされる**
  （`project.py` の `_resolve_anchors`）。**レイヤー内は順次・レイヤー間は並行**で、
  総尺は全レイヤーの最大値。別レイヤーのものを後ろに置きたいときは
  `pause.time()` / `obj @ t`（`@ "名前.end"` も可）/ `time(name=...)` + `pause.until("名前.end")` を使う。
  `a >> b` は**同じレイヤーの中だけ**（先行 Object を変数で参照するが、レイヤー .py は
  それぞれ別の名前空間で exec されるので別レイヤーの Object は見えない）。
  これは DSL で最も事故が多い規則なので `describe` の constraints
  （`layer_timeline_independent`）にも載せてある。
- **Object はコンストラクタを呼んだ時点のカーソル位置で登録される**（`Object.__init__`、
  text 系は `text.py` の `_new_text_object` が `proj.objects.append` し、
  `_resolve_anchors` はこの登録順で順次カーソルを進める）。
  後から呼んだ `time()` / `show()` では並び順は動かない。音声 `v` を作って `v.time(4)` した後に
  字幕 `text()` を作ると字幕は4秒後ろへずれるので、**字幕の text を先、音声 Object を後に作る**
  （または `narrate()`）。describe の constraints（`object_registered_at_creation`）にも載せてある。
- **priority が同じ Object は登録順に重なる**（`sorted(key=priority)` が安定ソートなので、
  `p.layer()` を呼んだ順・レイヤー内は作った順で、後が上）。
- パッケージ本体は `src/scriptvedit/`（59モジュール）。`pip install -e .` で
  どのディレクトリからでも `from scriptvedit import *`。

## 2. 最初に読むもの（最重要）

**本体ソースを全部読む必要はない。** 使える機能・シグネチャ・制約は
ケイパビリティ・マニフェストとして機械可読で取得できる。

```bash
python -m scriptvedit describe                        # 全機能を JSON で
python -m scriptvedit describe --format md            # 人間/AI が読みやすい Markdown
python -m scriptvedit describe --kind effect          # 種別で絞る
python -m scriptvedit describe --name fade            # 単一エントリだけ
```

`--kind` は audio_effect / class / effect / expr / factory / meta / object_method /
plugin / project_method / transform。

出力に含まれるもの:

- `usage` … 概念・main スクリプト雛形・レイヤー雛形・DSL・Expr・**プラグイン雛形**・CLI
- `constraints` … 守らないと壊れる制約（severity: error/warning/info）
- `effects`(42) / `transforms`(8) / `audio_effects`(7) / `factories`(44) /
  `objects`(19) / `object_methods`(9) / `project_methods`(14) / `expr`(102) / `plugins`(3)
  （件数は変動する。正は `describe` の実測で、整合は tests/test_issue17_docs.py が検証する）

各 Effect エントリには `bakeable` フィールドがあり、キャッシュに焼けるかが分かる。
Effect / Transform / AudioEffect の `respects_fast_hint` は、その op が `~` の
軽い代替処理を実装しているかを示す。**現時点で true の op は1つも無い**
（`cache.py` の `_FAST_HINT_OPS` が空集合）。§5 の `~` の契約は
「軽い代替を実装したときに何を同時に更新するか」を定めた拡張点であって、
いま `~` を付けても出力もキャッシュ鍵も一切変わらない。

describe の中身を足すときは、**手書きの補助テーブルは `manifest_data.py`**
（choices / notes / constraints / usage 等の純データ）、**導出ロジックは `manifest.py`**。
この分担を守らないと manifest.py がまた肥大化する。

その他の CLI: `python -m scriptvedit new <path>`（プロジェクト雛形生成）、
`python -m scriptvedit cache`（統計/GC/全削除）、
`python -m scriptvedit watch`（変更監視して再実行）。

補足として `README.md` に DSL 記法と設計思想がまとまっている。

## 3. 開発ワークフロー

```bash
pip install -e .[all]      # コアは標準ライブラリのみ。extras: morph/figures/web/beat/tts/tools
pytest tests/              # 全テスト（約3分）
pytest tests/test_real_render.py --realrender  # 実レンダ回帰（選抜。CIと同じ）
pytest tests/test_fx_slots.py -k golden --golden-update  # 図の金型の作り直し（§5「図解アニメ」）
python tests/render_all.py # 実レンダリング全件（重い。出力は tests/output/）
python scripts/check_unused_imports.py   # 未使用importの検出（CIでも実行）
python scripts/check_import_cycles.py    # 循環import（SCC）の規模チェック
```

未使用 import は CI で失敗にする。存在確認のためだけの import は `# noqa` を
付けて意図を示す（`__init__.py` の再エクスポートは対象外）。

### import の書き方（循環 import と「末尾 import」）

**scriptvedit 内の import は原則としてファイル先頭に書く。**
かつて全モジュールが1つの巨大な循環（SCC サイズ 19）に入っており、その回避策として
「scriptvedit 内 import はファイル末尾に書く」という不文律があったが、
`context.py` の新設（「現在の Project」を葉モジュールへ分離）で SCC は **7** まで縮んだ。
先頭 import に戻せるものは戻してある。

末尾 import が残ってよいのは、**同じ SCC に属するモジュール同士の import だけ**:

```
SCC(7) = cache / ffmpeg / filters.video / objects / plugins / text / timeline
```

この7つの相互 import のみ、ファイル末尾の
`# --- 循環 import の回避（同一 SCC のモジュールのみ末尾で束縛…）---` ブロックに置く。
それ以外（SCC 外→SCC 内、SCC 内→SCC 外、SCC 外同士）は必ず先頭 import にする。

`python scripts/check_import_cycles.py` が Tarjan で SCC を測り、
既定の上限（7）を超えたら失敗する。**モジュールを足して循環が育ったらここで落ちる。**
新しい循環を作りそうになったら、まず「共有している状態や定数を葉モジュール
（`context.py` / `state.py`）へ出せないか」を検討すること。

- `context.py` は **scriptvedit 内 import ゼロの葉**。`current_project()` /
  `activate()` / `push_exec()` / `pop_exec()` / `exec_parent()` / `in_layer_exec()` /
  `is_project()` を持つ。
  ここに依存を足すと集約した意味が消えるので、**絶対に import を増やさない**。
- `warn.py`（`_warn` / `_WARN_LOCK`）も同じ扱いの葉。レイヤーキャッシュを
  `layercache.py` へ分離したとき「project.py を import せずに警告を出す」必要が生じて
  切り出した。ここにも **import を増やさない**。
- `Project._current` / `Project._exec_stack` というクラス属性は**廃止**した。
  現在の Project は `from scriptvedit.context import current_project` で読む。
- `project.py` は純粋な sink（誰からも import されない）。例外は
  `__init__.py` の再エクスポートと `manifest.py`（`_inspect.getmembers(Project)` で
  型そのものが要る）の2つだけ。
  `objects.from_project` の型判定は `context.is_project()` 経由にしてあり、
  project.py がクラス定義直後に `register_project_class(Project)` で注入する。
  **sink であることは分割の武器でもある**: `parallel.py` / `preview.py` /
  `checkpoint.py` / `layercache.py` は「`project` を第1引数に取る自由関数」として
  project.py から抽出されており、SCC を増やさずに済んでいる。
  project.py から何かを切り出すときはこの型に従うこと。
- morph（PIL/numpy）等 **optional 依存の遅延 import は関数内のままでよい**
  （循環回避ではなく依存の遅延ロードが目的なので、この規約の対象外）。

### スナップショットテスト

`tests/test_snapshot.py` は ffmpeg コマンド列を `tests/snapshots/*.json` と突き合わせる。

```bash
pytest tests/test_snapshot.py --snapshot-update   # 再生成
```

**再生成前に必ず差分を目視確認すること。** 意図しないコマンド変化を
スナップショットごと上書きしてしまうと、退行がテストをすり抜ける。

ffprobe・フォント・gitignore 対象の大容量素材・edge-tts またはネットワークが無い
環境では、依存するテストだけを `pytest.skip` にする。スキップを
PASS として返してはいけない。`test91` の数式 PNG 内容差が下流の
checkpoint 鍵に伝播する環境差は、比較時にその鍵だけを正規化する。
数式パスやフィルタ文字列の実質差分、保存/update 時の具体ハッシュは残す。
同じ理由で、鍵にフォントの内容指紋か Pillow の版が入る生成物（text_image の PNG・
図解アニメの frames の .mov・それを入力にした fly_to の flight）も、比較時だけ鍵ハッシュを
畳む（`_TEXT_IMAGE_DEPENDENT_TESTS` / `_FIGURE_FONT_DEPENDENT_TESTS`）。畳むと同じ種類の
生成物どうしの dict のキーがぶつかるので、正規化は dict を「キーと値の組の並べ替えた列」に
してから比べる（片方が黙って消えない）。

**スナップショットの限界: dry_run は寸法を予測できない。** `formula()` の数式PNGは
dry_run 時点で未生成のため `base_dims=None` になり、`scale` の
pad（SEGVバリア, §4.1）が付かないコマンドになる。**formula + scale の pad 経路は
実レンダでしかカバーできない** ので、CI でも回る `pytest tests/test_real_render.py --realrender` の選抜（test92 を含む）で踏む。

### dry_run はキャッシュ状態に依存しない（＝旧「実レンダ後の罠」は解消済み）

**`render(dry_run=True)` が返すのは「キャッシュが空の状態で何を実行するか」**であって
「いま何が未生成か」ではない。だから `__cache__` に何があっても出力コマンドは同じで、
**実レンダの後にキャッシュを消さずスナップショットを回してよい**。

この契約を守っているのは次の6つの収集経路。**新しい中間生成物を足すときも必ず揃えること**
（1つでも「存在すればコマンドを出さない」を入れると、実レンダの有無でスナップショットが落ちる）:

| 経路 | 場所 |
|---|---|
| チェックポイント | `checkpoint.py` の `_collect_checkpoint_cmds`（全 step の `build_cmd()` を必ず呼ぶ。終端フレーム Effect の morph / particle / flight（fly_to）も step） |
| web Object | `project.py` の `_collect_web_cmds` |
| レイヤーキャッシュ | `layercache.py` の `_collect_cache_cmds`（生成は `cache='make'` のときだけ。存在は見ない） |
| compute / from_project / xfade 生成物 | `objects.py` の `compute` / `from_project`、`media.py` の `_finalize_generated_object` |
| ラウドネス測定（`normalize_audio(mode="linear")`） | `loudness.py` の `_collect_loudness_cmds`（測定結果 JSON の有無は見ない） |
| `stills()` / `frames()` の動画（図解アニメの `framekit.build` も） | `stillseq.py` の `_finalize_hold_object`（dry_run 分岐が存在チェックより前。`frames()` の `draw` は dry_run では呼ばない） |

compute / from_project / xfade の経路だけが存在チェックを dry_run 分岐より**前**に置いており、それが
「実レンダ後に test18 / test24 / test57 / test74 が落ちる」罠の正体だった
（test18 / test24 はさらに別原因も重なっていた。§5 の「中間生成物は映像専用」を参照）。
現在は全経路が揃っており、`tests/test_compute_cache_path.py` が
**cold と warm の dry_run 出力が完全一致すること**を検証している。

**`normalize_audio(mode="linear")` の増幅量だけは dry_run の main に実値が入らない。**
増幅量は測定パス（音声だけ・全編・null 出力の ffmpeg）を実行して初めて決まり、dry_run は
測定しないので、main の正規化は `volume=<MEASURED_GAIN>dB`（`loudness.py` の
`_LINEAR_GAIN_PLACEHOLDER`）と表記する。測定パスのコマンドは cache 側に
`__cache__/artifacts/loudness/<鍵>.json` → コマンド、として「実行予定」で載る。
**測定済み（warm）でも表記のまま**にしている（測定値を読んで埋めると、測定キャッシュの
有無で main が変わりスナップショットが落ちる）。表記は実行されないので、実レンダで
増幅量が未設定のまま main を組むと `_build_ffmpeg_cmd` が RuntimeError で止める
（表記を ffmpeg へ流さない）。cold / warm の一致は `tests/test_normalize_linear.py` が検証する。

**例外はレイヤーキャッシュの `cache='auto'` / `'use'`**（`layercache.py` の
`_should_use_cache`）。これは「キャッシュを再生するか、レイヤーを実行し直すか」を
キャッシュの有無・鮮度で決める機能そのものなので、dry_run の main コマンドも変わる
（auto は warm なら入力が1本のキャッシュ webm になる。use は cold だと dry_run でも
`FileNotFoundError`）。上の表の「存在は見ない」は `cache='make'` の**生成コマンド**の話。
スナップショットで auto / use を扱うときは、dry_run の前にキャッシュの状態を固定すること
（test15 = `tests/projects.py` の `_test15_factory` は、dry_run 用にダミーの webm と
anchors.json を置いてから `use` を踏む）。固定しないと実レンダの有無でスナップショットが落ちる。

キャッシュはリポジトリルートの `__cache__/` に置かれる（`tests/conftest.py` が
実行ディレクトリに依存しないようルートへ chdir する）。実レンダ出力は `tests/output/`。
`python -m scriptvedit cache --clear` は不要になったが、害も無い。

## 4. FFmpeg 8 の地雷（実装済みの回避策。壊さないこと）

以下はすべて実測で踏み抜いた既知の不具合と、その回避策。**フィルタ生成に手を入れる
ときは、これらを外さないこと。**

### 4.1 `scale(eval=frame)` + `rotate` で SEGV (0xC0000005)

`pad` や `format=rgba` **単体では防げない**。固定サイズ・中央配置の `pad` の直後に
**`copy` フィルタ**を挟んでバッファを分離するのが唯一の回避策。

- `filters/video.py` の `_fx_scale` … pad サイズを scale 式の
  固定格子サンプリングで決定する（通常 `n_grid=100` ＝ 101点。振動系関数
  sin/cos/tan/mod/random を含む式は格子とエイリアスして点間ピークを取りこぼすため、
  `_expr_has_oscillatory` 判定で `n_grid=4999` ＝ 5000点の密格子に切り替える）。
  `pad=max_w:max_h:(ow-iw)/2:(oh-ih)/2:color=0x00000000:eval=frame` → **`copy`**。
- 同じバリアが `_fx_ken_burns` にも必要。

### 4.2 overlay は全て `eof_action=pass`、start>0 の映像入力に `tpad`

overlay の既定 `eof_action=repeat` は `enable` と組み合わせると誤動作し、
enable=false の区間でも最終フレームが合成され続ける。`filters/video.py` が生成する
全 overlay で `eof_action=pass` を使う。

開始が 0 より後の映像入力には `tpad=start_duration=N:start_mode=clone` を入れる
（`_build_video_overlay_parts`）。**`tpad` は trim/setpts の「後」に挿入すること** — 前に置くと
trim がクローンフレーム込みで尺を切ってしまう。`tpad` の直前には `settb=1/120000` が要る（§4.7）。
※ `mask` / `mask_wipe` の `blend` 側は `eof_action=repeat` のままで正しい。

### 4.3 drawtext の fontsize 式アニメは SEGV

`text` / `typewriter` / `counter` の `size` は**定数のみ**。Expr/lambda は構築時に
`ValueError` で弾かれる（`text.py` の `_validate_text_size`）。
x / y / alpha のアニメーションは安全。文字サイズを変えたいときは `scale()` Effect を使う。

### 4.4 `movie=` の1フレーム入力を blend に渡すと式の `T` が約5倍速で進む

framesync のタイムベース評価が壊れるため、メイン入力と同じタイムベースへ正規化する:
`movie=filename=...,loop=loop=-1:size=1,fps={fps},setpts=N/({fps}*TB)`
（`filters/video.py` の `_fx_mask_wipe`）。無限ループは各レンダ経路の `-t` で打ち切られる。
※ T 非依存の素の `_fx_mask` は正規化不要。

### 4.5 ネイティブ VP9 デコーダは alpha 非対応

`.webm` 入力には `-c:v libvpx-vp9` を付ける。入力側の分岐は
`ffmpeg.py` の `_decoder_input_args` に一本化されている（動画→静止画の `compute()` も通す）。
**ただし無条件ではない**: `__cache__` 配下の `.webm` は拡張子で確定して強制、
**外部の `.webm` は probe して vp9/vp8 のときだけ指定**する。
一律に強制すると AV1 等が `Bitstream not supported` で落ちる（issue #15 で実際に踏んだ）。

### 4.6 長大フィルタは Windows のコマンドライン長制限に当たる

4000文字以上の `-filter_complex` / `-vf` / `-af` は一時ファイルへ書き出し、
FFmpeg 8 の `-/filter_complex <path>` 構文に自動で切り替える
（`ffmpeg.py` の `_FILTER_SCRIPT_THRESHOLD = 4000` と `_externalize_long_filters`）。
同じオプションが複数回現れても**全件**外部化する（以前は opt ごとに最初の1件で
break しており、「`-filter_complex` は1回しか出ない」という暗黙の前提に依存していた）。
**失敗時は一時ファイルを消さずパスをエラーに出す**（ffmpeg が指した式そのものが
消えると原因が追えないため）。成功時のみ削除する。

### 4.7 `tpad` のクローンが入力タイムベースに丸まり、中身が早く届く

`tpad=start_duration=N:start_mode=clone` はクローン1枚ごとに「1/フレームレート」を
**入力のタイムベースへ丸めて**積算する。Matroska/WebM のタイムベースは 1/1000 なので
30fps の 1/30 秒が 33ms に丸まり、中身が開始時刻の**約1%早く**届いて早く消える
（FFmpeg 8.0 実測: FFV1 mkv を 600 秒開始 → 実フレームが 594 秒から。60 秒開始 →
59.4 秒から届き、enable で隠れた分だけ「素材の途中から始まり最後が背景」になる。
GIF の 1/100 では約10%）。scriptvedit 自身の生成物（checkpoint の FFV1 .mkv、
web / compute / from_project / レイヤーキャッシュ）がすべてこの形なので、
長尺動画の後半ほど大きくずれる。mp4（1/15360 等、1/fps で割り切れる）はずれない。

回避策は **`tpad` の直前に `settb=1/120000`** を入れること
（`_build_video_overlay_parts` と `_tpad_timebase`。本レンダ・レイヤーキャッシュ・
時間分割並列レンダが共通で通る）。1/120000 なら 24 / 25 / 30 / 48 / 50 / 60 / 120fps と
NTSC の 24000/1001・30000/1001・60000/1001 の1フレーム長がすべて整数 tick になり、誤差は0。
Project の fps が割り切れない（90 / 144fps 等。`fps=29.97` のように小数で書いた fps も
2997/100 として扱うので割り切れない）ときは分母を fps との最小公倍数へ
広げる（90fps → 1/360000、29.97 → 1/119880000）。fps だけで決まり素材を probe しないので、
dry_run と実レンダで同じ文字列になる。

**`settb=AVTB`（1/1000000）では足りない。** 1/30 秒は 33333.3µs で割り切れず、
丸め誤差がクローン枚数ぶん積もって半フレームに達した時点で overlay が1フレームずれる
（FFmpeg 8.0 実測: 30fps の FFV1 mkv は開始が約28分を過ぎると1フレーム早く出て0枚目が飛ぶ
（1800 秒開始で確認）。60fps の mp4（1/15360）は約7分を過ぎると1フレーム遅れて0枚目が
2回出る（600 秒開始で確認）。
後者は settb 無しなら正確なので、AVTB は mp4 にとって退行だった。90fps では
1/120000 も割り切れず 600 秒開始で13フレーム早く出たので、分母を広げている）。
Project の fps と違い、かつ 1/120000 で割り切れない fps の素材だけ、
1枚あたり 1/240000 秒以下の誤差が残る。
**テキストの lavfi 入力は対象外**: タイムベースが 1/fps で丸めが起きない
（実測で 600 秒ちょうど）。画像入力には `tpad` 自体が無い。
回帰は `tests/test_tpad_timebase.py`（FFV1 mkv 30fps を 60 秒・1800 秒開始、
h264 mp4 60fps を 600 秒開始へ置いて、j 枚目が「開始 + j/fps 秒」に映ることを
画素で確かめる。後の2つは AVTB だと落ちる）。

### 4.8 `volume` の式は初期化時に `t=NaN` で評価される

`volume=volume='式':eval=frame` は、フレームごとの評価とは別に**初期化時に1回**
全変数を NaN にして式を評価する（`af_volume.c` の config_output → set_volume）。
`avolume(lambda u: ...)` の式 `clip((t)/dur,0,1)` は NaN になり、式を使う音声1本ごとに
`Invalid value NaN for volume, setting to 0` が出て、数百行で本当のエラーが埋もれた。
初期化時の値は使われない（各フレームで t を入れて評価し直す）ので実害は無い。

回避策は **式の `t` を `if(isnan(t),0,t)` で包む**こと（`filters/audio.py` の
`_VOLUME_T_EXPR`）。フレームごとの `t` は NaN にならないので出力は置き換え前と
ビット単位で同一（`tests/test_volume_nan.py` が PCM の MD5 と実レンダで確かめる。
test16 / test17 の実レンダでもデコード後の音声 MD5 が一致）。**音声側で `t` を使う式を
足すときも `_VOLUME_T_EXPR` を通すこと。**

### 4.9 `volume` の式は音声フレーム1枚に1回しか評価されない

`eval=frame` の「frame」は音声フレーム（デコーダの 1024〜4096 サンプル。44.1kHz で最大約 93ms）。
立ち上がりのフェード `clip(u/a,0,1)` は最初の1枚が丸ごと `u=0`（無音）になり、
切り出した語録の頭が最大 93ms 欠けた（「あっ」の立ち上がりが消える、など）。
回避策は **時間で変わる音量の前に `asetnsamples=n=256:p=0` を入れて約 5ms に刻む**こと
（`filters/audio.py` の `_VOLUME_EXPR_FRAMING`。定数の音量には入れない）。
回帰は `tests/test_volume_nan.py` の `test_volume_expr_fade_in_does_not_swallow_first_frame`。

### 4.10 動画のつなぎ目に黒いフレームが1枚入る（enable の開始と映像の終わり）

`time()` で動画を順に並べると、境目の1枚がどちらの映像も無い**黒フレーム**になりえた
（実測: 章ごとの mp4 を5本つないだ完成版の境目4か所）。原因は4つあり、全部に手当てがある:

1. **開始時刻の端数**: `time()` の連結で `804.9000000000001` のような端数が乗る。
2. **`t` の 1ulp 誤差**: overlay の `t` は「pts × タイムベースの double 値」なので、格子上の
   フレームの `t` が開始より小さく出る（30fps の 111 枚目は `111×(1/30)=3.6999999999999997`。
   49fps などでは整数秒でも起きる）。
3. **`tpad` と `enable` の丸めの食い違い**: `tpad=start_duration` はクローンの枚数を
   「µs に切り捨てた開始 × fps」の四捨五入（半分は切り上げ）にするので、中身の1枚目は
   **開始に最も近いフレーム**に届く。`enable` を開始時刻ちょうどで開けると、開始が格子から
   外れているとき（207.339 秒 → 中身は 207.333 秒）その1枚目が窓の外に落ちる。
4. **音声の方が長い動画**: AAC は 1024 サンプル単位なので、scriptvedit 自身が書き出す mp4 も
   含めて音声が映像より数十 ms 長い。Object の尺は長い方の stream で決まるので、次の Object は
   映像の終わりより後ろから始まる。

回避策（`filters/video.py`）:
- `enable` の開始は **`_t_enable_from`**: `_tpad_first_frame`（tpad と同じ有理数計算）で求めた
  フレームの **1µs 手前**。終了は `_t_ceil`、tpad の `start_duration` は `_t_floor`
  （ffmpeg 自身が µs へ切り捨てて読む）。どれも小数6桁までしか書かない。
  `round(x, 6)` にしないこと（2/30 秒を切り上げて開始フレームを落とす）。
- 映像が Object 自身の尺より先に終わる動画は **`_video_tail_hold`** の分だけ最後のフレームを
  保持する（`tpad` の `stop_mode=clone`。1フレーム足して必ず覆う）。保持は Object 自身の尺
  （長い方の stream）までで、`time(d)` で素材より長く伸ばした分は保持しない。
  `__cache__` の生成物・存在しない素材・`loop()` 付きは対象外（probe しない）。
  **例外は `stills()` / `frames()` の生成物**（`cache.py` の `_is_hold_artifact_path`。
  `__cache__/artifacts/stills/`・`frames/`）: 絵の列なので `time(d)` で伸ばした分も最後の絵を
  保持する。素材の尺は `Object._generated_length`（生成コマンドを組んだ時点で確定）から取り、
  probe しないので dry_run と実レンダで同じ文字列になる。Effect を焼く経路
  （checkpoint / compute）は一時 Object に尺が届かないので、`_hold_source_filters` が
  `tpad=stop=-1:stop_mode=clone` を時間系の前処理の直後に入れる（`-t` が尺を切る）。
  **判定は Object の属性ではなくパス**で行う（焼いた後は source が checkpoint へ差し替わる）。
  時間系の live Effect（speed / freeze_frame）と焼ける Effect を併用すると、チェックポイントは
  素材の尺ぶんしか焼かれない（`_checkpoint_bake_duration`）ので、`_video_tail_hold` は
  差し替え前の元素材（`Object._audio_source`）のパスも見て、`_resolved_length`
  （焼いた尺に live Effect を畳んだ値）より長い分を保持する。そのベイク尺は
  `_generated_length` があれば probe せずそれを使う（生成済みの .mov を probe すると
  46/30 秒が 1.533333 に丸まり、チェックポイントの鍵が cold / warm で食い違う）。
- **保持は開始の `tpad` と同じ `tpad` に書く**。2段に分けると、前段の `tpad` が下流へ伝える
  終端の時刻を詰め物の分ずらさないので、後段のクローンが過去の時刻に出て overlay に
  捨てられる（FFmpeg 8 実測: 0.412 秒開始の 0.2 秒素材のクローンが pts 0.2 秒）。

回帰は `tests/test_enable_float_fuzz.py`（音声の方が長い素材を並べる実レンダを含む）。

### 4.11 文字まわりの3つの罠（subtitles の alpha・glow の画素形式・drawtext の `%`）

- **`subtitles` フィルタは既定でアルファを触らない。** テキスト Object の入力は完全に透明な
  キャンバス（`color=c=black@0.0`）なので、`alpha=1` が無いと RGB だけ描かれてアルファが 0 の
  まま残り、overlay しても何も映らない（`subtitles()` / `karaoke()` が丸ごと映らなかった）。
  `text.py` の `_build_text_filters` は必ず `:alpha=1` を付ける。
- **`gblur` / `blend` はパックドの rgba を受け取れない。** `format=rgba,split→gblur→blend` と
  書くと形式は折衝まかせになり、下流が YUV 系を好む経路（live の overlay 直結。焼かれない
  `text()` など）では blend が yuva で動く。`all_mode=screen` が色差（無彩色で 0.5）にも掛かって
  白がマゼンタに化ける。`_fx_glow` の入口は **`format=gbrap`**（プレーナ RGB）で固定してある。
  チェックポイントへ焼く経路は出力が bgra なので元から gbrap に折衝されており、出力はバイト一致
  （キャッシュ鍵は据え置き）。**split → blend を組む Effect を足すときは入口を gbrap にすること。**
- **drawtext の inline `text=` に `\%` と書くと "Stray %" で文字列全体が描かれない。** AVOption の
  解釈でバックスラッシュが1段はがれるので、文字の `%` は `\\%`、バックスラッシュは `\\\\`
  （`text.py` の `_escape_counter_literal`。`textfile=` の中身は1段でよい）。
- `counter()` の値の進行度だけは `_u_expr(start, 尺 - 1フレーム - 1µs)`（`_counter_progress`）で、
  最後のコマで 1 に達する（通常の u は (N-1)/N までしか行かず `to` に届かなかった）。
  詰めるのは u の分母だけで、`_UStr` が運ぶ表示秒・経過秒は Object の尺そのもの
  （1 フレーム以下の尺で返す `"1"` も `_UStr`。素の str だと easing の秒の式が落ちる）。
  **出力コマ数は 総尺×fps の四捨五入**なので、総尺が格子に乗らない動画の末尾（30fps の
  `time(1.01)` は 30 コマ）では最後の1コマが出ない。そのときは分母を「最後に出力されるコマ」
  まで詰める（食い違いが1フレーム以内のときだけ。意図して途中を切った場合は詰めない）。
  位置・アルファの u は他と同じ定義のまま。`%{eif}` は 32ビットの int しか印字できないので、
  桁区切り・小数・32ビット超は「桁数と符号ごとの drawtext を enable で切り替える」
  （`_build_counter_filters`）。回帰は `tests/test_text_fixes.py`（画素で確認）。

### 4.12 `movie=` の RGB 画像をグレーにした枝の colorspace=gbr が下流へ伝わる

`mask` / `mask_wipe` のマスク（RGB の PNG）を `format=gray` にした枝には「colorspace=gbr」の
タグが残り、blend → alphamerge → overlay と下流のフレームへ伝わる。透明な下地へ重ねる
レイヤーキャッシュ（`cache='make'` の既定品質。VP9 yuva420p）ではエンコーダが
`SRGB color space requires profile 1 or 3` で落ちた（焼かれない `text()` に mask_wipe を
掛けたレイヤーで実測。画素は元から既定の行列で変換されていて、誤っていたのはタグだけ）。
回避策は **グレーにした直後に `setparams=colorspace=unknown`**（`filters/video.py` の `_MASK_GRAY`）。
マスクの寸法合わせは deprecated の `scale2ref` ではなく **`scale=rw:rh`**（第2入力が寸法の基準。
基準側は出力されないので、アルファは `alphaextract,split` で2本に分ける）。全フレームの画素が
scale2ref と一致する（`tests/test_mask_scale_ref.py`）。**`movie=` の画像を別の枝へ混ぜる
Effect を足すときも同じ手当てを入れること。**

### 4.13 geq は式を画素ごとに評価する（時間だけの不透明度で 1080p が毎秒6コマ前後）

`geq` は 1 コマで全画素 × r/g/b/a の式を評価する。fade / opacity の不透明度のように
**時間だけで決まる式**まで geq に落とすと、式が単純でも 1080p で毎秒6コマ前後、点の多い
`keyframes_sec` では更に遅い（実測: 1080p・10 秒に 56 点で 85 秒。113 秒の見本で 771 秒）。
回避策は **`sendcmd` の `[expr]` で式をコマごとに1回だけ評価し、`colorchannelmixer` の `aa` へ
送る**こと（`filters/video.py` の `_alpha_mul_filters`。同じ素材の書き出し全体が 8 / 56 / 128 点で
4.1 / 4.3 / 4.3 秒）。経路は native fade（入りと出の単純なランプ）> 定数 > コマごとの評価 > geq
（X / Y・random・中身を辿れない Expr の派生だけ）の順に選ぶ（`_alpha_path`）。

- **宛先はインスタンス名** `colorchannelmixer@<label_prefix>e<番号>`（グラフ中で一意。型名で
  送ると全 colorchannelmixer に届く）。引数は3段のエスケープで、式の区切り `\,` を `\\,` にする。
- **点の数に完全には依らない。** `[expr]` はコマごとに式の文字列を構文解析し直すので、手間は
  式の長さに比例する（16x16・18000 コマで 8 点 2.9 秒、128 点 12.7 秒。1 コマ約 0.5ms 増える。
  1080p では1コマの処理に埋もれて +5% 前後）。`keyframes_sec` は経過秒 `clip(T-開始,0,表示秒)` を
  区間ごとに何度も書く（128 点で約250回）ので、短くなるときは経過秒を先頭で1回だけ
  `if(gte(st(0,経過秒),0),clip(式,0,1),0)` に置き、式の中は `ld(0)`（u は `ld(0)/表示秒`）で読む
  （`_per_frame_alpha_cmd`。毎回書く形とコマごとの値はビット単位で同じなので短い方を使う。
  st を先に評価させるため if の条件に置く。u を1回しか使わない式は毎回書く形のまま）。
- **掛けるのは gbrap**（`format=rgba` の後ろに `format=gbrap`）。packed の rgba / bgra のまま
  overlay へ渡すと、手前の自動変換（→ yuva420p）が**アルファ 128〜254 を1階調上げる**
  （FFmpeg 8.0 実測。gbrap → yuva420p は全 256 値で正確）。geq の出力も gbrap だった。
  native fade・定数の opacity・チェックポイント（bgra の FFV1）を本レンダで重ねる経路には
  この1階調が元からある。
- 画素は geq と最大1階調違う（geq は切り捨て、colorchannelmixer は四捨五入。送る値は小数6桁）
  ので、チェックポイント・compute の鍵に版（`cache.py` の `_ALPHA_CMD_VER`）を混ぜる。
  経路が変わらない op（native fade・定数の opacity・geq のままの op）には付けない。
  **レイヤーキャッシュ（`cache='auto'` / `'use'`）と `from_project` の webm の鍵には意図して入れない**
  （旧 geq 経路で焼いた生成物は、レイヤー .py を変えるまで命中し続ける）。差は不透明度で 1/255
  （fade と opacity の連鎖で 2/255）以内で、既定品質の VP9 の量子化より小さい。どちらの鍵も
  レイヤーの中身（ops）を見ずに決まるので op ごとの条件付きの版を入れられず、無条件に上げると
  関係の無いレイヤー・サブプロジェクト（Web や重い合成を含む）まで作り直しになる
  （「同一出力なら同一鍵」に反する）。出力の差が見て分かる変更なら、`from_project` の
  `audio_graph` のような局所の版を足すこと。
- **式の最大・ランプ判定を格子だけで見ない。** `scale` の pad 見積もり（100 等分の格子）と
  native fade の判定は、多点 keyframes の細い山・谷（10 秒の 4.02〜4.08 秒など）を取りこぼし、
  前者は pad 不足の EINVAL、後者は谷が消えた。描くコマ（`_frame_us`）と式の頂点でも確かめる。
- **ただし全コマを eval_at で評価しない。** eval_at は if の両方の枝を評価するので、多点
  keyframes では手間が「コマ数 × 点の数」になる（60fps・128 点の scale で、600 秒の Object は
  フィルタを組むたびに 17 秒、3600 秒は 107 秒止まった。チェックポイントのコマンドを組むたび・
  `p.audit()` の内部の dry_run でも同じだけ掛かる）。`expr_scan.py` の2つを使う:
  `_pl_pieces` は区分線形の式（keyframes / keyframes_sec / ramp / phase / lerp / clip / min / max /
  abs / 比較と if）を u の区分ごとの1次式へ展開する。区分の内側は1次式なので、区分の端（頂点）と
  端の前後のコマ（`_frame_us_near`）だけで全コマと同じ最大になり（`_expr_frame_max`）、native fade
  は候補ランプとの差を全区間で確かめる。`_compile_u_eval` は eval_at と同じ値を返し、if は
  選んだ枝だけを評価する（ffmpeg と同じ。選ばれない枝の 0 除算などで例外を投げない）。
  区分線形でない式（イージング・振動系）だけ全コマを評価する。実測（60fps・128 点・3600 秒）:
  区分線形 0.01 秒、`ease_in_out_sine` 0.6 秒、`ease_out_back` 1.1 秒（コマ数に比例、点の数には
  対数）。丸めで clip の折れ点と lt の境目が 1ulp ずれてできる幅 1e-12 以下の区分は隣へ含める
  （そこだけの値＝不連続点の手前の片側極限を頂点として拾うと、丸めの向きで pad が変わる）。
  pad の大きさは topleft 等の配置にも効くので、どのコマも取らない片側極限で pad を広げない。
- 回帰は `tests/test_alpha_per_frame.py`（経路・鍵・画素・st/ld の値の一致・本レンダ・時間の上限）・
  `tests/test_scale_pad_keyframes.py`・`tests/test_expr_scan.py`（eval_at との一致・区分線形の
  展開・全コマとの一致・評価回数と時間の上限）。

## 5. 設計規約（コードを変更するときに守ること）

### bakeable / live

Effect は2種類ある。

- **bakeable** … 中間ファイル（チェックポイント）へ焼き込みキャッシュできる。
  `state.py` の `_BAKEABLE_EFFECTS` に名前を登録すると
  キャッシュ対象になる。Transform は全て bakeable。
- **live** … 毎レンダで ffmpeg フィルタとして適用する。`speed` / `reverse` /
  `freeze_frame` / `repeat`（`_TIME_LIVE_EFFECTS` の4つ。`repeat` は `obj * n` の
  内部 Effect）は時間軸を変えるためチェックポイントの尺基準と衝突する。
  `move` / `shake` は overlay 座標の変調なので焼けない。

判定は `cache.py` の `_is_bakeable`。**新しい Effect が本当に「焼いても同じ絵に
なる」ものかを確かめてから登録すること。** 時間依存の尺変更を伴うものは live のまま。
**明示された例外が1つある**: `trim` は尺を変えるが `_BAKEABLE_EFFECTS` に入っている
（`cache.py` の `_fold_time_effects` がベイク尺の計算に反映する前提）。
ただし live な時間 Effect の後の `trim` を前へ移して焼く並びは計画時エラーにする。
終端フレーム Effect は `compute()` 内・`policy="off"` で処理できないため明示拒否する。

`morph_to` / `explode_to` / `assemble_from` / `fly_to` は終端フレーム生成 Effect
（`_TERMINAL_FRAME_EFFECTS`）で、bakeable な ops の末尾に1つだけ置ける。
`fly_to` の本体は `morph_flight.py`（`morph.py` の色の道具を使うので、`morph.py` から
`morph_flight` を import しない。循環になる。キーの集合 `FLY_PARAM_KEYS` だけを
`morph.py` に置き、`morph_flight` が import 時にシグネチャと突き合わせる）。
描き方を変えたら `cache.py` の `_FLIGHT_VER` を上げ、`tests/golden/flyto/` の金型を作り直す。

**焼く枚数は「閉区間の enable 窓を覆う枚数」にする**（`checkpoint.py`）。overlay の窓は
`between(t, 開始, 開始+尺)` の閉区間で、尺がフレームの整数倍だと「開始 + 尺」ちょうどの
フレームも窓に入る。足りないと EOF（`eof_action=pass`）で背景が1フレーム見える。

- 動画チェックポイントは `_bake_frame_count` = `ceil(fps*尺 + 0.5)` 枚。`-t` には
  枚数/fps を書く（`_bake_t_arg`）。**`-t` に表示尺そのままを書かないこと**: FFmpeg 8 の
  出力の `-t` は「枚数 = t*fps の四捨五入」で切るので（実測: `-t 1.0` → 30 枚、
  `1.016667` → 31 枚、`1.036667` → 31 枚）、`time(1)` の静止画 + Effect が 30 枚になり、
  焼かない場合（31 枚）より1枚短かった。増える1枚は u=1 で評価される。
  枚数の決め方を変えたら `cache.py` の `_CHECKPOINT_TAIL_VER` を上げる。
- 終端フレーム生成 Effect は `ceil(fps*尺) + 1` 枚（`_terminal_frame_plan`）。内訳は
  「最初のコマの複製（`delay`）＋ 生成するコマ（`duration`。省略は残り全部）＋ 最後の
  コマの複製」で、PIL が作るのは生成するコマだけ。複製は `tpad` の `start` / `stop`
  （枚数指定）で行う。`delay` / `duration`（`state.py` の `_TERMINAL_TIMING_KEYS`）は
  フレーム生成（`morph.py`）へ渡さない。**`delay` / `duration` を指定した計画では、
  生成するコマを必ず2枚以上にする**（進行度 0 と 1）。1枚だと唯一のコマ（進行度 0 ＝元の絵）が
  最後のコマとして複製され、到達点に一度もならない（`duration` が1コマ以下・`delay` が
  尺の終わりぎりぎりで起きた）。
- **窓は閉区間なので、順に並べた境目の1枚は前後どちらの Object も映る。** 焼いた Effect も
  これに揃った（以前は焼いた動画が1枚短く、境目は後ろの Object だけだった。live の Effect と
  静止画は元からこの挙動）。後ろが前を覆わない並びでは前の絵が1枚重なって見える。
  README の「表示の窓と境目の1枚」に利用者向けの説明がある。
  並列チャンクの枝刈りも enable と同じ fps 丸め・閉区間で判定し、境界の1枚を落とさない。
- **`compute()` はこの対象外**（生成物の `length()` が1フレーム伸びると、`time()` の
  既定尺と後続の並びが動くため）。

粒子（`explode_to` / `assemble_from`）の `expand` は既定 `None`（自動）。余白は実際の
軌道から求め、上限に Project の画面寸法を使う（`project.py` が `expand_limit` を渡す）。
**出力が画面寸法で変わるのはこの場合だけなので、鍵（`_particle_cache_path`）にも
`expand` を省略したときだけ `pframe=` を入れる。** `morph.py` の描画結果を変えたら
`_MORPH_RENDER_VER` を上げる。

**終端フレーム生成 Effect が足す余白は対称・偶数にし、anchor からは引く。**
粒子の `expand` と sdf モーフの整列の余白は焼いた動画のキャンバスを広げるが、実レンダでしか
決まらないので `pad_size` では伝えられない。`filters/video.py` の `_terminal_inner_dims` が
「余白を除いた元の絵の箱」（`Object._terminal_bake` の入力画像。morph は2枚の共通キャンバス）を
定数で返し、`_build_move_exprs` が辺・角の anchor を `(w-元の幅)/2` で補正する
（余白を片側だけに足したり、焼いた後の寸法を別の決め方にすると位置がずれる）。
入力がキャッシュ生成物（前処理を焼いた中間物）のときは dry_run で寸法が取れず従来の式になる
（`_get_media_dimensions` の方針。実レンダでは補正が入る）。自動の余白を奇数にすると
overlay の位置が1画素ずれ、4:2:0 出力で元の絵の色差が半画素にじむので偶数にそろえる。

### 中間生成物は映像専用（音声は常に元素材から取る）

チェックポイント（`checkpoint.py` の `_build_checkpoint_video_cmd`）と
`compute()`（`objects.py` の `_build_compute_video_cmd`）は **`-an` を付けて映像だけを焼く**。

理由: 中間物に音声を残すと `Object.has_audio` の probe 先が中間物になり、
**キャッシュの有無で音声の有無が変わって dry_run と実レンダのコマンドが食い違う**
（cold は probe 失敗で `-an`、warm は probe 成功で `-map [a0] -c:a aac`）。
さらにチェックポイントの `-vf` は音声を加工しないので、`trim` を焼いたオブジェクトでは
「映像は 2s から・音声は 0s から」の中間物に `atrim=start=2` が二重に掛かって A/V がずれる。

代わりに `Object.audio_source`（差し替え前の元素材）を保持し、
`_build_ffmpeg_cmd` が**音声専用入力を末尾に1本追加**する。
入力本数が増えるので、FFMETADATA のストリーム index は
`1 + len(sorted_objects)` ではなく**実際の入力総数**で数えること（チャプターが壊れる）。

### 音声の素材選択と表示尺は別にトリムする

`_build_ffmpeg_cmd` は素材選択の `atrim` があっても、音声前処理の末尾で `.time()` の
表示尺へトリムする。前処理を `_fold_time_effects(..., audio=True)` で無限長から畳み、
上限が表示尺以内と確定するときだけ追加を省く（同一出力なら同一コマンド）。
`atempo` の実サンプル数は理論尺を超えることがあるため上限を未確定へ戻し、後続の
`atrim` があれば再確定する。素材の probe に依存させず cold/warm を揃え、
並列音声レグ・ラウドネス測定も同じ関数を通す。

### 音声は混ぜる前に 48kHz・ステレオへ揃える

`amix` / `sidechaincompress` / `acrossfade` の出力のチャンネル配置とサンプリング周波数は
**先頭入力に従う**（実測: モノラル 24kHz の TTS が先頭だと全体が 24kHz・モノラルになり、
ステレオの BGM が L/R 平均に潰れる）。入力の並びは priority 順＋生成順なので書き手は気づけない。

- `_build_ffmpeg_cmd` は各音声入力の加工チェーンの**末尾**に `filters/audio.py` の
  `_MIX_AUDIO_FORMAT`（`aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo`）を
  必ず付ける。加工の無い入力も `[N:a]aformat…[aK]` のラベル付きチェーンにし、生入力参照
  （`[N:a]` を amix へ直接・`-map N:a`）は作らない。並列レンダの音声レグも同じ関数を通る。
- **末尾であること**: `aloop` / `arepeat` の size は素材の実サンプルレートで見積もるので、
  先頭で 48kHz に変えると size が不足しうる。
- `audio_sequence` / `video_sequence` の `acrossfade` 入力にも付ける。出力の中身が変わるので
  鍵にも `afmt=` を入れた（`video_sequence` は音声を連結するときだけ）。`from_project` は
  音声のあるサブプロジェクトだけ `audio_graph=4` に上げ、映像だけなら `2` のまま据え置く
  （音声グラフに出力が左右されないので、上げると同一出力なのに webm を作り直す）。
  **混ぜ方を変えたら、音声を焼いた生成物の鍵も上げること**
  （上げないと旧形式で焼いたキャッシュが命中し続けて直らない）。
- モノラル → ステレオの自動変換は各チャンネル約 -3dB（パンの法則）。仕様として README に明記済み。
  入力がすべてモノラルのプロジェクトは、以前のモノラル出力より再生音量が約 3dB 下がる
  （`normalize_audio()` を使えば目標値どおり）。これも README に明記済み。
- **`duck_under` の検出用枝は形式統一の前から取る**（`[N:a]…,asplit[apreK][dside_src…];`
  `[apreK]aformat…[aK]`）。揃えた後から取ると、モノラルのナレーションが各チャンネル -3dB で
  検出され（`sidechaincompress` の既定 `link=average`）、既定値で下げ幅が約 2.6dB 浅くなる。
  相手1つなら `_SIDECHAIN_FORMAT`（周波数だけ揃える）、複数なら `_SIDECHAIN_MIX_FORMAT`
  （48kHz モノラルへダウンミックス）を通してから `amix`。複数を揃えずに合算すると
  結果が先頭の相手の形式に従い、並び順で検出レベルが 3dB 変わる（実測）。
- **`duck_under(hold=ms)` は検出用の枝を保持つきの包絡へ置き換える**（`filters/audio.py` の
  `_sidechain_hold_filter`。`aeval` の `st()` / `ld()` はサンプルをまたいで残る）。
  sidechaincompress に保持は無く、release だけでは読点ごとに BGM が戻る。包絡は相手が1つでも
  48kHz モノラル（`_SIDECHAIN_MIX_FORMAT`）で作り、**`apad` の後**に置く（相手が止んだ後の
  無音の間も保持を数えるため）。`hold=0` のグラフは従来と同一。回帰は `tests/test_duck_hold.py`。
- 48000 は `normalize_audio()` の `sample_rate` 既定値・libopus の固定値と同じ。

### `normalize_audio(mode="linear")` は測定パス → 一定の増幅（`loudness.py`）

`mode="dynamic"`（既定）は測定値を渡さない1パスの `loudnorm` で、短期ラウドネスを
目標へ寄せ続けるため、声の無い区間の BGM が膨らみ声が入ると沈む（ポンピング。
実測で区間差 15.8dB → 1.4dB）。`mode="linear"` は次の2段:

1. **測定パス**: `_build_ffmpeg_cmd` を `_loudness_measure_render=True` で呼び、
   本レンダと同じ音声グラフの末尾に `loudnorm=print_format=json` を付けて
   音声だけ・全編・`-f null` で流す（並列レンダの音声レグと同じ再利用の仕方。
   **音声グラフを別に組み直さないこと**。duck_under 等の変更が測定側にだけ漏れる）。
   `-loglevel info -nostats` を明示し、`_run_ffmpeg(echo=False)` の戻り値
   （stderr の末尾200行）から JSON を読む。
2. **本レンダ**: `volume=<target − 測定値>dB` → `aresample` → `alimiter`。
   リミッターは既存どおり sample rate 確定の**後**（リサンプルの補間が新しいピークを
   作るため）。`limiter=False` は true peak が内部上限を超えない所で増幅を止めて警告する。

- 測定は `render()` の `_ensure_checkpoints` の**後**（入力の source を本レンダと揃える）、
  本レンダ・並列レンダの前に `_ensure_linear_gain` が行い、`project._linear_gain_db` に置く。
  音声レグ（`parallel._build_audio_leg_cmd`）も同じ値を使う。
- **部分レンダでも全編を測る**（測定コマンドは `_render_window` を見ず `-t 総尺`）。
  窓だけ測ると BGM だけの窓が +20dB 以上持ち上がる。
- 音声を出さない出力（gif / webp / 連番PNG / サムネイル / 絵コンテ）と音声 Object の無い
  プロジェクトは測らない（判定は `_needs_measurement`。`_build_ffmpeg_cmd` が正規化チェーンを
  組む条件と同じにしてある）。
- 測定結果の鍵は**測定コマンドそのもの**（入力は `_src_signature`）。映像だけの Object は
  測定コマンドに入れない。`target` / `true_peak` / `limiter` / `sample_rate` は測定コマンドに
  現れないので、変えても測り直さない（同一出力なら同一鍵の逆で、同一測定なら同一鍵）。
- `from_project` の署名は効く設定だけ: dynamic は mode 導入前と同じ文字列、linear は
  `lra` を外して `mode` を足す（`objects.py`）。
- ピークの多い素材を大きく持ち上げると、リミッターが削る分だけ統合ラウドネスが目標より
  低くなる（実測: TTS の声＋BGM を +8.8dB、ピークを最大 5dB 抑えて -14.46 LUFS）。
  **補正の再測定はしていない**（仕様は「target − 測定値」の一定増幅）。

### 絵の列は1本の動画にまとめる（`stillseq.py` の `stills()` / `frames()`）

全面 PNG を1枚ずつ Object にすると、どの入力も 0 秒から流れて出番まで捨てられるので
手間が「枚数 × 尺」に比例する（実測: 100枚×6秒で ffmpeg のメモリ 31GB。入力 300〜600 本で
コマンド長が WinError 206）。`stills()` は concat demuxer、`frames()` は標準入力の生 RGBA から
**qtrle（argb）の .mov を1本**作り、Object を1個返す（同じ 100枚×6秒が 68 秒・1.2GB・4.6MB）。

- **符号化は qtrle 固定**（FFV1 にしない）。可逆で alpha を持ち、前のコマと同じ画素を飛ばすので
  同じ絵の続く区間がほぼ 0 バイト（実測 3600 コマ: qtrle 1.0MB / FFV1 353MB / VP9 4.4MB だが
  書き出し 40 倍遅く非可逆）。キーフレームは先頭だけ（`-g` を大きく。-g 300 で 5 倍に膨らむ）。
  .mov のタイムベースは 1/fps で割り切れる（§4.7 の丸めが起きない）。
- **ffconcat の各 `file` に `option framerate` を書く**。無いと画像のタイムベースが 1/25 になり、
  30fps で切り替わりが1コマずれる（実測 200 か所中 27 か所）。`duration` は µs へ丸めた
  開始時刻の差で書く（1件ずつ丸めた尺を足させない）。
- **切り替わりは「境目の累積秒」を最も近いフレームへ丸める**（`_stills_schedule`。秒は 10 進の
  有理数で足す）。1枚ずつ丸めて足すと誤差が積もる。丸めた結果は `obj.starts` /
  `obj.frame_counts`。鍵には秒ではなくフレーム数を入れる（同一出力なら同一鍵）。
- リスト（.ffconcat）は絶対パスを含むので、生成の直前に毎回書く（鍵は内容由来で、素材の
  置き場所が変わっても同じ。古いリストを命中させない）。動画本体は命中ガードあり
  （再生成に全コマの描画が要る。原子的に書き、0 バイトは命中扱いにしない）。
- **`stills()` の生成コマンドには `-reinit_filter 0` が要る**。画素形式の違う画像（RGB の PNG と
  RGBA の PNG、パレット、グレースケール）が混ざると、既定では形式が変わるたびにフィルタグラフが
  作り直され、fps フィルタの抱えていた前の絵のコマが捨てられる。`-frames:v` は末尾に再掲した
  最後の絵で埋まるので枚数は合い、exit 0 のまま絵の消えた動画が出来る（FFmpeg 8.0 実測:
  RGB + RGBA の 3 コマずつが 6 コマとも2枚目）。符号化の違う画像（PNG + JPEG）はデコーダが
  最初の画像のもので固定されて落ちるので、検証で拒否する（`_check_same_codec`。先頭バイトで判定）。
- **コマ数を確かめてから確定する**（`_commit_verified`。ffprobe の `nb_frames`）。ffmpeg は入力の
  途中で絵が落ちても exit 0 で終わることがあり、短い動画が一度確定すると命中し続ける。
  `frames()` で「ffmpeg が先に落ちた」と見なすのは**標準入力への書き込みの失敗だけ**
  （`draw` の呼び出しごと `except OSError` で包むと、`draw` の中の FileNotFoundError を握りつぶす）。
- `stills()` の画像の指紋は `_src_signature` ではなく **`_file_fingerprint` を直接**使う（全画像の
  実在を検証で要求しているので常に内容で見られる。`__cache__` 配下の自作ページ PNG を
  パス署名にすると、書き直しても古い動画が命中する）。fps は有理数の表記（`_fps_text`）で
  鍵とコマンドの両方に入れ、画像と同じ寸法の `size` は指定なしと同じ扱い（同一出力なら同一鍵）。
- **`frames()` の鍵は呼び出し側の `key` だけ**（+ コマ数・fps・size）。`draw` のコード指紋は
  混ぜない: バイトコード指紋（plugins.py の方式）は内側の lambda / 内包表記がメモリアドレスつきで
  現れ、レイヤーは Plan / Render で2回 exec されるので鍵が2回で食い違う。
- 最後のコマの保持は §4.10 を参照（判定はパス）。

### キャッシュ鍵（フィンガープリント）

- **素材は内容ハッシュ**（sha256 先頭16桁、`cache.py` `_file_fingerprint`）。
  パスにも mtime にも依存せず、同一バイト列なら別マシンでも鍵が変わらない。
  ただし環境ごとに生成内容が変わる素材は、その内容差が下流の鍵へ伝播する。
  高速化は最外側の1レンダ内だけメモ化し、ネストしたサブプロジェクトとは共有する。
  `_begin_render_pass` で `_FFP_MEMO` を破棄し、同じ size/mtime の差し替えも次回検出する。
  **ディスクキャッシュ（`__cache__/ffp.json`）は撤廃した**
  — (パス, サイズ, mtime) を参照キーに永続化すると、mtime を保持するコピー
  （`cp -p` / `rsync -t` / `tar -x` / `unzip -o`）で同サイズの別内容に差し替えたとき
  古いハッシュを返し「変更したのに再生成されない」。復活させないこと。
- **ソース署名は `_src_signature` に一本化**（`cache.py`）。キャッシュ生成物
  （`__cache__` 配下）は**パス署名**、素材は**内容指紋**。鍵本体もバケット（`_src_bucket`）も
  同じ方針にすること。片方だけ内容指紋にすると、上流キャッシュ生成物を持つ
  下流アーティファクト（morph/particle/checkpoint）のパスが `__cache__` の有無で変わり、
  dry_run と実レンダのパスが食い違う（＝実レンダ後にスナップショットが落ちる）。
- **パラメータは `_op_fingerprint_str`**（`cache.py`）。
- **生パスを鍵に混ぜないこと**（リポジトリの置き場所でキャッシュ鍵が変わり移植性が壊れる）。
  パスを取るパラメータは `cache.py` の `_OP_PATH_PARAMS` で除外し、代わりに内容指紋
  （`lut_ffp` / `mask_ffp` / `tgt_ffp` / `asm_ffp`）を混ぜる。プラグインは
  ビルダー関数のコード指紋 `plugin_ffp` を混ぜる。
  素材が読めない場合のみ `_norm_src_path`（cwd相対・`/`区切りへ正規化）へフォールバックする。
- `policy` は鍵に含めない（意図的）。`quality="fast"` は、その op が実際に
  `~` の軽い代替処理を使って出力が変わる場合だけ含める。未対応 op の raw な
  品質ヒントを鍵へ混ぜると、同一出力なのにキャッシュだけ分裂するため禁止。
  **この「同一出力なら同一鍵」は品質ヒント以外にも適用される。**
  効かない条件のパラメータを無条件に鍵へ混ぜないこと（例: `audio_viz` の `color` は
  `kind="waves"` でしかフィルタに現れないので、鍵にも waves のときだけ入れる）。
- **`*_cache_path` 系に呼び出し側の raw hint を渡さない。** 鍵に使う品質は
  関数内で ops / op から再導出する（以前は使われない `quality` 引数が生えていて、
  シグネチャを見ると効くように誤解できた）。
- **レイヤーキャッシュの `anchors.json` は `_resolve_anchors` の結果を切り出す。**
  `layercache.py` の `_get_layer_data` が独自にタイムラインを走査するのではなく、
  正規リゾルバが解決した `self._anchors` から `_anchor_defined_in`（所有レイヤー）で
  そのレイヤー分だけを取る。**アンカー解決のロジックを二重実装しないこと**
  （旧実装は `time(name=)` の `X.start`/`X.end`・`@`・`>>`・`until` を落として空の
  メタを書いていた）。
- **部分レンダの `cache='make'` は計画時に拒否する。** 部分的な web 素材を全編用に保存しない。
  Object の priority 上書きでレイヤー外との重なり順が変わる場合もキャッシュを拒否する。
  外部アンカーの参照名と解決値は Plan から `external_anchors` メタへ控え、鮮度に含める。
- **`from_project` の解決済みレイヤー param は子の鍵に入れ、生成物の署名を親の依存へ渡す。**
  `video_sequence` は入力 Object の `_generated_length` と確定済み音声有無を優先し、
  未生成時の probe と生成後の丸めで計画を変えない。
- **レイヤーキャッシュのパスは必ず `Project._layer_cache_paths_for(spec)` 経由で取る。**
  `_layer_cache_paths(filename, project)` を直呼びすると `spec` の `cache_quality` が落ち、
  品質は鍵と拡張子の両方に効くので**静かに別物のパス**を指す。

### `~` 品質ヒントの契約

> **現状これは未実装の拡張点。** `cache.py` の `_FAST_HINT_OPS` は空集合で、
> 軽い代替処理を持つ op は1つも無い。`~` を付けても出力もキャッシュ鍵も変わらない。
> 以下は「実装するときに何を守るか」の定義。

- Effect / Transform / AudioEffect で共通。内容を削除・無効化する演算子ではない。
- 軽い代替処理を持つ op はそれを使い、持たない op は通常と同一の処理を行う。
- 未対応ヒントは正常動作なので、エラーや実行時警告を出さない。報告は
  `p.audit()`（品質lint。`quality-hint-ignored` として info 級で列挙）に集約する。
  厳格運用は `p.audit(strict=True)`（warning があれば RuntimeError）。
- 明示的な映像・音声削除はそれぞれ `delete()` / `adelete()` を使う。
- 対応 op を増やすときは実処理、`cache.py` の `_FAST_HINT_OPS`、マニフェスト、
  出力差とキャッシュ鍵のテストを同時に更新する。

### キャッシュ書き込みは原子的に

`ffmpeg.py` の `_run_ffmpeg_to_cache` を使う。`_unique_tmp_path` が返す
同一ディレクトリ・同一拡張子の PID + UUID 付き一時パスへ書いて
成功時に `os.replace` する。**最終パスへ直接書かない**（中断すると壊れたファイルが
キャッシュとして残り、次回以降ずっと使われてしまう）。コマンド中に cache_path が
現れなければ `ValueError` で即失敗する（置換漏れの検出）。

**`__cache__` 配下は ffmpeg 出力だけでなくテキストも必ず `_atomic_write_*` 経由**
（`text.py` の `_ensure_textfile` と karaoke の ASS、`chapters.py` の
`_write_chapters_metadata` 等）。
ファイル名が内容ハッシュなので、切り詰められた残骸が一度でも残ると以後どのレンダでも
再生成されず黙って使われ続ける。「存在すればスキップ」ガードも置かない
（残骸を永久に温存する）。
**`_atomic_write_text` は改行を変換せず LF で書く**（`newline="\n"`）。Windows 既定の
CRLF で書くと FFmpeg 8 の drawtext が textfile の `\r\n` を改行2回として描き、
複数行 `text()` の行間が倍になる（実測）。`_ensure_textfile` は内容中の CR も LF へ
正規化してから鍵と本文を作る。ASS・FFMETADATA・concat リスト・anchors.json・
チャプター目次も LF で問題ない（`tests/test_text_newlines.py` がバイトで検証）。

**この「ガードを置かない」規則が当てはまるのは、再生成がタダ（内容から一意に
書き直せる）な成果物だけ。** `tts.py` のように**再生成に外部エンジンやネットワークが
要る**キャッシュは、命中ガードを置いてよい。その条件は2つ:
① 書き込みが原子的であること、② 0バイト等の明らかな残骸を命中扱いにしないこと。
`loudness.py` の測定結果 JSON（再生成に音声の全編パスが要る）も同じ扱いで、
`_read_measurement` が形を検証し、壊れていれば測り直す（self-heal）。

同じ理由で、VOICEVOX の鍵に混ぜるエンジン署名（接続先 + `/version`）は
`<cache_dir>/engine_sig.json` に控え、**エンジンに届かない間だけ**その控えで鍵を作る
（キャッシュに当たれば使い、合成が要るときだけ ConnectionError）。届けば必ず実測が勝ち、
控えも上書きする。`tts_marks()` の `<鍵>.marks.json`（合成に使った audio_query の控え。
wav と同じ鍵なので、エンジンの版が変われば使われない）も同じ扱いで、`_read_marks_query` が
形を検証し、壊れていれば問い合わせ直す。**合成と時刻計算のクエリは `_voicevox_query` の
1か所で作る**（別々に組むと、調整が片方にだけ漏れて語の時刻が wav とずれる）。
ffp.json（撤廃済み）の罠は「古い永続値が実測より優先される」ことで、
これは「実測できない間だけ最後の実測値を使う」逆向きの使い方なので、罠には当たらない。
**控えを実測より優先する使い方に変えないこと。**

### Object を `__new__` で手組みする箇所は属性を全部揃える

`text.py` の `_new_text_object` と `layercache.py` の `_load_cached_layer` は
`Object.__init__` を通さず属性を手で置く。**`__init__` が設定する属性は1つ残らず
同じ初期値で置くこと。** 足りない属性は呼び出し側の `getattr(..., None)` に救われて
長く潜伏し、誰かが直接属性アクセスを1行足した瞬間に
その経路（text / progress_bar / キャッシュ再生レイヤー）だけ AttributeError になる。

### text 系（drawtext）は焼けない。文字を画像にするのは `text_image()`（`textimage.py`）

`media_type == "text"`（text / typewriter / counter / subtitles / progress_bar）は実体ファイルを
持たず、`_plan_object_checkpoints` はベイク対象外として None を返す。ベイクでしか実現できない
終端フレーム Effect（`_TERMINAL_FRAME_EFFECTS`）と `compute()` は、**黙って無視せず ValueError で
`text_image()` を案内する**（構築時の `Object._append_effect` と計画時の両方。文言は
`objects.py` の `_text_terminal_effect_message` の1か所）。

`text_image()` の規則:

- **PNG は構築時（レイヤー exec 中）に描く**。dry_run でも描く（`asset()` の取り込みと同じ扱い。
  source が常に実在するので寸法を probe でき、dry_run と実レンダでコマンドが食い違わない）。
  `formula()` のように実レンダ直前まで遅らせない。
- 鍵は「折り返し後の配置・書式・フォントファイルの**内容指紋**・Pillow の版・`_TEXT_IMAGE_VER`」。
  配置に現れない引数（折り返しが起きない `max_width`、1行のときの `line_spacing`、
  `border=0` のときの `border_color` など）は鍵に入らない（同一出力なら同一鍵）。
  字の無い区間（空文字・改行だけ）は書式を登録しない。太さは `_variation_coords` が
  **軸の値**へ正規化してから鍵に入れる（`weight="Bold"` と `weight=700` は同じ鍵。
  既定の軸の値と同じ指定は指定なしと同じ鍵）。
  描き方を変えたら `_TEXT_IMAGE_VER` を上げる。
- **スナップショット（`tests/projects.py` の `_SPECS`）には載せない。** 鍵がフォントファイルの
  内容指紋と Pillow の版を含み、PNG のパスも、それを入力にした morph / particle /
  checkpoint の鍵も環境ごとに変わるため。コマンドの形は `tests/test_text_image.py` の
  dry_run テスト（PNG が画像入力になる・粒子の生成物へ差し替わる・繰り返して同じ出力）で、
  エラーケースは `tests/test_errors.py` の `check_text_*` で守る。
- 行頭禁則の追い出しは `_KINSOKU_MAX_PULL` 単位まで。禁則字でない単位に届かなければ
  送らずに元の位置で折る（上限が無いと「！！！！！」で1行1字の行を量産する）。
  半角の語は全部の字が禁則字のときだけ禁則扱い（`.env` は語）。長音・リーダは禁則に入れない。
- 豆腐の判定は **cmap が正**（`_cmap_lookup`。format 4 / 12 を自前で読む）。画素が
  `.notdef` と同じかどうかは cmap を読めないフォントだけのフォールバック
  （MS ゴシックの `□` U+25A1 は cmap に在るのに絵が `.notdef` と同じで、画素だけだと誤判定する）。
- 命中判定は「存在すれば」ではなく `_cached_png_ok`（PNG として開けて・寸法が合い・`verify()` が通る）。
  切り詰められた残骸は書き直される。書き込みは `_write_png_atomic`。
- レイアウトは `ImageFont.Layout.BASIC` に固定（raqm の有無で字送りが変わると同じ鍵で違う絵になる）。
- 色つきの字を透明な RGBA へ `ImageDraw.text` で直接描かない（縁の画素の RGB が黒へ寄る）。
  色ごとの L マスクへ描いてから `_tint` で着色して重ねる。
- 可変フォントの wght は fvar を自前で読んでタグで探す（PIL の `get_variation_axes()` は
  表示名しか返さない）。
- `p.audit()` への申告は `Object._text_image`（dict）。チェックポイントが resize / scale を焼くと
  op が Object から外れるので、`_apply_checkpoint_final_state` が焼く前の op を
  `Object._pre_checkpoint_ops` に控え、audit はそこから画面上の倍率を求める。

### 図解アニメ（`framekit.py` とその上の図）

`regex_view` / `slots` / `text_transition` / `odometer` / `flow_graph` / `flow_tree` / `globe` は、
Python で描いたコマを `framekit.build`（中身は `frames()`）へ渡して透過の .mov を1本作り、
Object を1個返す。鍵・描画・時刻・文字・金型テストの規則は `framekit.py` の1か所に置く
（内部モジュール。`from scriptvedit import *` には入らない）。

- **鍵は「kind・図の版・`_FRAMEKIT_VER`・`norm(params)`・偶数へ切り上げる前の寸法・
  `fonts=` に渡した Sprite の署名・files の内容指紋」**（フォントを使うときだけ、その内容指紋と
  Pillow の版も）。`draw` のコードは鍵に入らないので、**描き方を変えたら版を上げる**（下の表）。
  `draw` の中で作る文字や `fonts=` に渡さない文字は params に入れる（入れないと古い図が命中する）。
  配置の結果（座標）のように params から決まる値は鍵に入れない（同一出力なら同一鍵）。
- **絵は金型テスト（`tests/golden/<種類>/`）で守る**。作り直しは絵を目視してから
  `pytest tests/<その図のテスト> -k golden --golden-update`。`--golden-update` は版定数が金型と
  同じまま絵だけ変わったものを**書き換えずに失敗させる**（版を上げずに小さな変化が積もらない
  ように）。金型と別のフォントの環境では skip する（理由に「フォント」を含む）。
- **生成物の `_origin_sources` は .mov 自身を先頭に持つ**（`stillseq._finalize_hold_object`。
  stills / frames / framekit.build の共通）。外から来る params（環境変数・`p.param()`）で鍵が
  変わっても、`cache='auto'` のレイヤーキャッシュが作り直される。
- **座標は SVG と同じ連続座標**（画素 (i, j) は [i, i+1)×[j, j+1)）で、縁は画素の正方形が図形に
  入る面積（箱フィルタの被覆率）。cv2 の LINE_AA / fillPoly は使わない（幅が合わず、端数の
  位置に反応しない。実測は framekit の docstring）。
- **blit の変換（端数位置・回転・縮小は INTER_AREA → INTER_LINEAR）は `framekit.warp_patch` の
  1か所**。text_transition の字（縁取りと塗りの2ch）も同じ関数で写す。図のモジュール
  （`fx_*.py`）は framekit の私有関数（`fk._*`）を呼ばない。共有したい処理は framekit の
  公開関数にする（`tests/test_framekit.py` が見張る）。選択肢の検証は `validate._require_choice`。
- 文字を描く図は `text=fk.text_meta(...)` で `p.audit()` へ申告する（一番小さい字の大きさ・装飾）。
- 依存は numpy・opencv-python・Pillow（extra は `figures`。`framekit.need()` が遅延 import）。
  dry_run は `draw` を呼ばない。
- スナップショット（test117〜128・132〜135）では、図の版を上げると frames の .mov の鍵ハッシュ
  だけが変わる。**`_FRAMEKIT_VER` を上げたら全部の図のスナップショットを、差分を目視してから
  作り直す**（鍵にフォントか Pillow の版が入る図は `_FIGURE_FONT_DEPENDENT_TESTS` で畳まれる
  ので通るが、test127・128・132〜135 は畳まない）。
- 陸のデータ（globe の land）は **Natural Earth 1:110m の陸を同梱している**
  （`src/scriptvedit/data/ne_110m_land_1440.png`。`land=True` が既定。`False` で陸なし、`None` は
  ValueError）。同梱の素材・データ（`assets/` と `data/`）の「全部自作・第三者素材ゼロ」の
  **唯一の例外**（パブリックドメイン。自作の大陸は不正確な地図が実データに見え、同梱しないと
  既定が陸の無い地球になるため。ユーザーの承認済み）。素材ではなくライブラリとして同梱している
  formula() 用の KaTeX（`templates/vendor/katex/`。MIT。同ディレクトリのライセンスに従う）は
  この原則の外。出典・利用条件・元データの SHA-256 は同じフォルダの `NOTICE.md` と PNG の
  tEXt。作り直しは `scripts/make_land_mask.py`（ネットに出る。`--zip <zip> --check` で再現の確認）。
  PNG を作り直したら `NOTICE.md` の SHA-256 も直す（`tests/test_fx_globe.py` が突き合わせる）。
  package-data の書き漏れは editable install のテストでは分からないので、CI の build ジョブが
  wheel の中身とインストール先から読めることを確かめる。
- globe の既定の見た目（陸の点 0.55・拠点の点の堀と光の輪・ortho の `atmosphere=0.25`・plate の
  `view="auto"`）は見本動画で拠点の点が陸に埋もれたのを受けて決めた。`view="auto"` の選んだ経度は
  鍵に入る（手で同じ経度を書いたのと同じ鍵。選び方を変えても、選ぶ経度が変われば鍵が変わる）。
  堀と光の輪（`_disc_cov`）は `_fade_line` と同じく、円の数 × 窓の画素を `_WINDOW_CHUNK` ずつに
  分けて計算する（**点の数に比例してメモリを確保しない**。globe は 20 万点まで受けるので、
  全部の円の窓を一度に作ると 2 万点で 1 コマ 1.5GB になった）。`points(halo=False)` は芯だけ
  （既定の `halo=True` は鍵に入れない）。

| 図 | モジュール | 版定数 | 金型 |
|---|---|---|---|
| 共通部品 | `framekit.py` | `_FRAMEKIT_VER`（全部の図の鍵に入る） | `tests/golden/framekit/` |
| regex_view | `fx_regex.py`（照合は `regex_vm.py`） | `_REGEX_VIEW_VER`・`_REGEX_VM_VER` | `tests/golden/regex/` |
| slots | `fx_slots.py` | `_SLOTS_VER` | `tests/golden/slots/` |
| text_transition / odometer | `fx_textmove.py` | `_TEXTMOVE_VER` | `tests/golden/textmove/`（字は同梱の自作フォント `svtm_block.ttf`。作り直しは同じ所の `make_font.py`、fonttools が要る） |
| flow_graph / flow_tree | `fx_flow.py` | `_FLOW_VER` | `tests/golden/flow/` |
| globe | `fx_globe.py` | `_GLOBE_VER` | `tests/golden/globe/` |
| fly_to（終端フレーム Effect。framekit は使わない。§5「bakeable / live」） | `morph_flight.py` | `cache.py` の `_FLIGHT_VER` | `tests/golden/flyto/` |

`regex_trace` / `regex_count` / `regex_view` の規則:

- `regex_vm.py` は **scriptvedit 内 import ゼロの葉**（標準ライブラリだけ）。照合は再帰を使わない
  （後戻り点は明示のスタック、捕獲と取り分は undo ログ。再帰版は空白 1000 個で C のスタックが
  溢れた）。命令の組み方・event の記録の仕方を変えたら `_REGEX_VM_VER` を上げる（regex_view の
  鍵に入る。event の列そのものは鍵に入れない）。
- `re` との一致は `tests/test_regex_vm.py` の差分ファジング（3.10 / 3.11+ の両方）で守る。
  sre の既知の癖（3.10 の印の戻し忘れ、3.11+ のグループに付けた min≥2 の所有量指定子）は
  ファジングの文法から外し、そのテストの docstring の表に書いてある。
- `fx_regex.py` は framekit.build の上。描き方を変えたら `_REGEX_VIEW_VER` を上げ、絵を目視してから
  `pytest tests/test_fx_regex.py -k golden --golden-update` で `tests/golden/regex/` を作り直す。
  スナップショット（test117〜119）は図の寸法がフォントのメトリクスで変わらないよう `size=` を
  固定し、frames の鍵ハッシュだけを `_FIGURE_FONT_DEPENDENT_TESTS` で畳む。
- regex_view の描き直しは「帯（細帯）ごとの署名が同じなら前の画素を使い回す」。**描くのは署名に
  入れた値（丸めた座標）だけ**にし、tape の動く印は上端・下端の範囲で触れる細帯を全部描き直す
  （印を足すときは範囲を控えめに取る）。順に描いたコマと、そのコマだけを新しい図で描いたコマが
  画素まで同じことを `test_sequential_and_fresh_frames_match` が確かめる。
- regex_count の外挿は、上限のある量指定子の「上限の手前の増え方」で当てると黙って数倍ずれる。
  当てはめの n は式の中の有限の回数・幅の最大（`_Program.reach`）より先に置き、`{m,n}` 型が
  あれば当てはめの外の大きな n でも検算する（この安全域を外さないこと）。

`flow_graph` / `flow_tree`（`fx_flow.py`）の規則:

- 鍵は「ノード・辺・layout の引数・出来事（呼んだ順）・色・寸法」と、文字があるときだけ
  フォント。**配置の結果（座標）は鍵に入れない**ので、配置の手順（BFS・重心法・radial の角度・
  点の塊の散らし方）や描き方を変えたら `_FLOW_VER` を上げ、絵を目視してから
  `pytest tests/test_fx_flow.py -k golden --golden-update` で `tests/golden/flow/` を作り直す。
- 毎コマの描画は uint8 の上で「触れた画素だけ」重ねる（float の全面キャンバス＋to_rgba8 は
  1080p で毎コマ約 30ms かかり、葉 3000＋パケット 200 で 40ms に収まらない）。framekit の
  polyline / to_rgba8 は辺の層（cut の進み具合ごとに使い回す）にだけ使う。
- 経路（BFS・Dijkstra）と配置は自前（networkx は import しない。版で結果が変わらないように）。
- **描く線とパケットの道は同じ折れ線（`_edge_pts`）から作る**。扇（細く薄い辺）も各辺の
  折れ線を往復して描く。別々に作ると `curve=` を渡したときにパケットが辺から外れる（実際に
  扇だけ直線で描いていて 67px 外れた）。
- 点の文字の置き場所は `_refine_labels` が辺・ノードとの重なりで選び直す（layered の第一候補は
  `_layered` の中、余白を測る前に決める）。鍵には配置の結果と同じく入らないので、選び方を
  変えたら `_FLOW_VER` を上げる。
- パケットの札（send の label）は**ノードの枠・点・ノードの文字・先に出た札に重ねない**。
  `_place_packet_labels` が build の描くコマの秒（`Fraction(i) / fps`）ごとに札の矩形を出し、
  重なるコマを隠す区間にする（描くときは `_plabel_alpha` で区間の中を α 0、前後
  `_PLABEL_FADE` 秒で薄れる・現れる）。重なりを辺との交差と同じ秤の「量」で比べて側を1つに
  保つだけだと、着く先の文字や出たばかりの box を覆った（見本 s13）ので、量ではなく隠す。
  採点と描画は同じ `_plabel_side` / `_plabel_at` で同じ秒の位置を出す（別々に書くと、
  隠すと決めたコマと描いたコマがずれて、重なりが漏れる）。
- 札の側（`_plabel_side` の側の列）はコマごとに選び直さない（ぱたぱた入れ替わる）。変えて
  よいのは途中のノードの先で1回と、止まる所で1回（`_PLABEL_REST_SIDES` = パケットより後ろの
  左右）だけで、変わる所のコマを必ず隠す。止まった札を中央のまま残すと、止まる先のノードに
  掛かって隠れ続けた。
- 図全体の `curve` は `_curve_caps` が辺の集まるノードで弱める（辺ごとの 'curve' は弱めない）。
  上限は隣の辺との角の間で決まり、辺の本数ではない（片側へ開く layered の扇は 3 本でも約 0.16 に
  なる。文書に「何本ならそのまま」と書くときは均等に散った radial の場合だと明記する）。
- 札の置き方も curve の弱め方も鍵に入らないので、変えたら `_FLOW_VER` を上げる。

`slots` / `text_transition` の規則:

- slots のあふれの札は `row(overflow_label=)`（既定 'total' = 入った数の合計）。描かなかった数
  だけの「+N」を既定に戻さない（400 を入れて「+190」と出て、数として誤解された）。札の書式は
  あふれる行だけ鍵に入る。
- text_transition の入れ替わる組は `swing`（行の字の高さの倍。既定 1）より遠くへ振らない。
  `_route_moves` の重なりの採点で、縦にだけ動く間に横へ箱を広げないこと（隣の字と接した箱を
  縦にすり抜けるだけで重なりに数え、深く振れる候補ほど得をして、字の 2.5 倍振れていた）。
- 描画の作業領域（`_Renderer`。1080p で約 100MB）は build の draw が最後のコマで手放す
  （Object は p.objects に残り続けるので、持ったままだと図の数だけ積み上がる）。

- globe も最後のコマを描いた後に renderer を手放す。再描画時だけ作り直し、画素・鍵は変えない。

### 検査系（viz.py / p.inspect()）は本体の規則を再実装しない

`viz.py` は `Project._plan_object_checkpoints()` と
`Project._layer_cache_paths_for()` を**そのまま呼ぶ**。
かつて viz 側がチェックポイント計画を手写ししていたため、本体だけが持つ
「`media_type == "text"` はベイク対象外」のガードが viz に無く、
`p.inspect()` が実在しないチェックポイントを予告していた。

`_plan_object_checkpoints()` は **純粋な計画**（steps の `build_cmd()` を呼ばない限り
ffmpeg も PIL も走らない）なので検査から呼んで安全。**ここに副作用
（ディレクトリ作成・probe 以外の I/O）を足すと `p.inspect()` が副作用を持つ。**
契約は `tests/test_viz.py` が固定している。

### pad でキャンバスを広げたら `pad_size` を更新する

overlay の中央配置は `(W-pad_size[0])/2` で計算される
（`filters/video.py` の `_build_move_exprs`）。キャンバスを広げる Effect が
`pad_size` を更新しないと配置がずれる。既存例は3つで、更新の仕方が違う:

| Effect | 更新の仕方 |
|---|---|
| `_fx_scale` | 初期設定（pad サイズを決める） |
| `drop_shadow` / `outline` | **加算**（既存の pad_size に足す） |
| `blur_background_fill` | **上書き**（キャンバス固定なので `(cw, ch)` を入れる） |

`rounded` は pad を出さないので `pad_size` を触らない（`format=rgba` + `geq` で
アルファを削るだけ）。**手本にしないこと。**
プラグインからは `ctx["expand_pad"](dw, dh)` / `ctx["set_pad"](w, h)` を使う。
コアの寸法変更 Effect の後の `scale` は入力寸法を `_FxCtx.current_dims` で追跡する（元の `base_dims` で計算しない）。
寸法変更後も FFmpeg 8 用の pad/copy を保ち、出力が変わる連鎖だけ鍵の版を更新する。

### その他

- **u 正規化**: エフェクト進行度は `clip((t-start)/dur, 0, 1)` で 0..1 に正規化する
  （`filters/video.py` の `_u_expr`。時間変数は通常フィルタ（scale / rotate / overlay 等）が
  小文字 `t`、geq / blend 等の framesync 系だけ大文字 `T`（小文字 `t` が未定義）で、
  プラグインの `ctx["u"]` / `ctx["u_T"]` がそれぞれに当たる。コア Effect もプラグインも
  この1関数から式を得るので、定義が乖離しない）。
- **音声側の u 正規化だけは `_u_expr` を通らない**（`filters/audio.py` が
  `clip(if(isnan(t),0,t)/{dur},0,1)` を直書きする。NaN の包みは §4.8）。adelay 前のオブジェクトローカル時間なので
  `start` を引かないのが正しいが、**定義箇所が2つある**ことは意識しておくこと。
- **u の式は `_UStr`（`expr.py`。str の派生）で渡す。** 秒で書く式（`elapsed()` / `remaining()` /
  `ramp()` / `keyframes_sec()`。ノードは `_TimeVar`）は u だけでは組めないので、`_UStr` が
  表示秒 `dur` と経過秒の式 `sec`（`clip(t-start,0,dur)`。u×dur へ戻さない）を一緒に運ぶ。
  `_u_expr` と音声側の直書きはどちらも `_UStr` を返す。**u の式を自前で組んで `to_ffmpeg` へ
  渡す箇所を足すときは `_UStr(式, dur)` に包むこと**（素の str だと秒のノードは記号
  `sec(…)` を返し、ffmpeg が Unknown function で止まる。黙って別の値にはならない）。
  数値評価（`eval_at`）は `_UValue(u, dur)` を渡す（scale の pad 見積もり・native fade の判定・
  morph の blend）。素の float だと秒のノードは ValueError。
- **終端 Effect が消費した数式も生成する。** `_ensure_formula_objects` は表示対象に加え
  morph / assemble / fly の画像依存をたどる（非表示でも生成責務は消えない）。
- **生成物の尺は `Object._generated_length`**（`media.py` の `_finalize_generated_object` が置く
  合成尺。`from_project` も設定する）。`length()` はこれがあれば生成物を probe しない: Plan pass では未生成で probe できず
  （`video_sequence(...).time()` が初回レンダで落ちていた）、Render pass では在るので、probe に
  頼ると cold / warm で尺が数 ms 食い違い Plan/Render の構造差になる。
  **source を差し替える処理はこの値も差し替えること**: `compute(duration=d)` は d へ
  （静止画なら None へ）置き直す。古い合成尺が残ると `length()` が黙って元の尺を返す。
  `video_sequence` が入れる `duration` は仮の値（`Object._duration_provisional`）で、
  `_fill_auto_durations` がレイヤー実行後に `length()` で1回だけ入れ直す（後から足した
  speed / trim / compute に追従。`time(d)` / `show(d)` / スライス / `* n` で明示したら外れる）。
- **`_resolve_obj_duration`**（`project.py`）は `obj.length()` ベース。
  trim / atempo を反映した加工後の尺を返す（チェックポイントのベイクと同一基準）。
  0 は返さない（`clip((t-start)/0,…)` のゼロ除算で ffmpeg が EINVAL になるため、
  fallback=5 へ落とす）。ただし fallback が使われるのは
  ①未生成の予定パス ②dry_run ③probe 成功だが尺 0/None ④image/text の4系統だけで、
  **非 dry_run で `length()` が例外を投げた場合は fallback へ落とさず RuntimeError**。
- **Expr パラメータは NaN / ±Infinity を構築時に拒否する**（`expr.py` の `Const`）。
  定数側の `validate._require_number` と同じ方針を Expr 経路にも通してある。
  弾かないと `nan` がフィルタ式に埋まり、ffmpeg が
  `A luminance or RGB expression is mandatory` のような原因の分からないエラーを返す。
- **素材参照は `asset()` / `here()` を使う**（`src/scriptvedit/assets.py`）。cwd 依存にしない。
  - `asset("images/bg.jpg")` の解決順は
    `<project>/assets/` → `<project>/assets/_imported/` →
    **共有素材ライブラリ**（環境変数 `SCRIPTVEDIT_ASSETS`。`os.pathsep` 区切りで複数可。Windows `;` / POSIX `:`）。
    共有ライブラリで見つかった素材は `assets/_imported/<relpath>` へ**コピーしてから**
    そのコピー先のパスを返す。同一 checkout は以後そのコピーだけで動くが、
    `_imported/` は通常 gitignore 対象なので fresh clone には共有ライブラリ設定か
    素材の別途持ち込みが必要。コピーは dry_run でも
    常に行う（戻り値が ffmpeg コマンドに埋まるため、dry_run と本レンダでパスが
    食い違うとスナップショットが壊れる）。キャッシュ鍵は内容ハッシュなので
    パスが変わっても再レンダは起きない。取り込み済みと共有ライブラリの内容が違う
    場合は警告して取り込み済みを優先（黙って上書きしない）。
    存在しなければ近い名前を提案して `FileNotFoundError`。
  - `<project>/assets` 自体の発見順は **cwd から上方向** → 実行中レイヤーファイルから
    上方向 → パッケージ位置から上方向（環境変数による上書きは無い）。
    **利用者プロジェクトの `assets/` が最優先**（順序を逆にすると editable install では
    リポジトリ同梱の assets/ が常に勝ち、利用者自身の assets/ が永久に無視される）。
    結果はキャッシュしない。
  - 新規プロジェクトは `python -m scriptvedit new <path> [--template explainer]` で
    雛形生成（`src/scriptvedit/scaffold.py`）。既定の minimal 雛形は生成直後に
    `python main.py` でレンダできる。explainer 雛形は `formula()` を使うので
    Playwright + Chromium が要る（`pip install "scriptvedit[web]" && playwright install chromium`）。
  - `here("scene.html")` … 実行中のレイヤーファイルと同じディレクトリ。
  - `p.layer("bg.py")` も cwd 非依存に解決される。

### 環境変数（全7つ。これ以外は無い）

| 変数 | 効果 |
|---|---|
| `SCRIPTVEDIT_ASSETS` | 共有素材ライブラリ（`os.pathsep` 区切りで複数可） |
| `SCRIPTVEDIT_FONT` | 既定フォントの上書き |
| `SCRIPTVEDIT_NO_PLUGINS` | `plugins/` の自動読込を無効化 |
| `SCRIPTVEDIT_TTS_BACKEND` | TTS バックエンドの明示指定 |
| `SCRIPTVEDIT_PARAM_*` | `p.param()` の値を環境から与える |
| `SCRIPTVEDIT_VERBOSE` | **ffmpeg コマンド全文を表示**（`project.py` の `_verbose`） |
| `SCRIPTVEDIT_REALRENDER` | 実レンダテストの有効化（`tests/conftest.py`） |

`SCRIPTVEDIT_VERBOSE=1` はレンダの失敗を調べるとき真っ先に使う（FFmpegError の
メッセージ自身がこれを案内する）。**ただし印字されるのは実行「直前」の形**で、
`_run_ffmpeg` がこの後に `_normalize_ffmpeg_cmd`（`-hide_banner` 等の付与）と
`_externalize_long_filters`（4000字超を `-/filter_complex <一時ファイル>` へ差し替え）を
掛ける。実際に起動されたコマンドそのものは **`FFmpegError.cmd`** が保持している。

### 出力形式マトリクス（`_resolve_output_format` / `_encode_args`）

| kind | 拡張子 | alpha | 音声 | 並列レンダ |
|---|---|---|---|---|
| `h264` | .mp4 | 不可（指定すると ValueError） | あり | **可** |
| `webm` | .webm | 可 | あり | 不可 |
| `gif` | .gif | 不可 | なし | 不可 |
| `webp` | .webp | 可 | なし | 不可 |
| `pngseq` | `%0Nd.png` | **常に透過**（`alpha` 指定に関係なく背景は `black@0`。`background_color` は無視） | なし | 不可（単一パスへ確定できないので原子的出力もしない） |
| `storyboard` | .png | 不可 | なし | 不可（select フィルタで間引く） |

並列レンダの適用条件は「`kind == "h264"` かつ 非 alpha かつ 部分レンダなし かつ
フレーム数が十分」（`parallel.py`）。**`dry_run` では `parallel` は完全に無視される**ので、
並列レンダのコマンドはスナップショットで守られていない。

### 信頼境界（どこからが信頼できない入力か）

**レイヤー .py とプラグインは任意コード実行と等価。**

- `plugins/` は **import しただけで実行される**（cwd の `plugins/`、およびレイヤーファイルと
  同階層の `plugins/`）。他人のプロジェクトを clone して `from scriptvedit import *` した
  瞬間にそのコードが走る。無効化は `SCRIPTVEDIT_NO_PLUGINS`。
- レイヤー .py は `exec(compile(...))` される。
- したがって **scriptvedit は「自分が書いた（または信頼する）スクリプトを実行する道具」**であって、
  未知のプロジェクトを安全に開くサンドボックスではない。

一方、**素材やパラメータの側には防御がある**（壊さないこと）:
`diagram()` の SVG 属性 whitelist、web Object の `name` を単一ディレクトリ名成分に限る検査
（`__cache__/webclip/<name>_frames` を rmtree するため）、`cache --clear` / `--gc` の
`_guard_cache_dir`（パス要素に `__cache__` が無ければ ValueError）、
drawtext / subtitles のパス・文字列エスケープ（filtergraph インジェクション対策）。

## 6. 機能を追加するときの判断フロー

1. **まず `python -m scriptvedit describe` で既存機能を確認する。**
   42 の Effect と 102 の Expr が既にある。車輪の再発明を避ける。
2. **その動画プロジェクト固有の一発ネタ → `plugins/*.py` に `@effect_plugin`。**
   コアを汚さない。`plugins/` は自動読込され、`from scriptvedit import *` で使える。
   雛形は `describe` の `usage.plugin_template` にある。参考実装:
   `plugins/example_neon.py` / `example_scanline.py` / `example_photo_frame.py`。

   ```python
   from scriptvedit import effect_plugin

   @effect_plugin("my_glow", bakeable=True, category="視覚効果",
                  params={"radius": {"type": "number", "default": 10,
                                     "min": 0, "max": 200, "desc": "ぼかし半径"}})
   def build_my_glow(params, ctx):
       """自作グロー（この1行目が要約としてマニフェストに載る）"""
       return [f"gblur=sigma={params['radius']}"]
   ```

   `ctx` の実集合は18キー（正は `plugins.py` の `_plugin_ctx`）:
   `u` / `u_T` / `start` / `dur` / `fps` / `width` / `height` / `base_w` / `base_h` /
   `label` / `obj` / `effect` / `project` / `parse_color` / `escape_path` /
   `pad_size` / `expand_pad` / `set_pad`。ビルダーは ffmpeg フィルタ文字列の `list` を返す。

   **注意**: `ctx["pad_size"]` は ctx 構築時点のスナップショット。ビルダー内で
   `ctx["expand_pad"]()` / `ctx["set_pad"]()` を呼んでも `ctx["pad_size"]` は更新されない。
   更新後の値を前提にフィルタを組むプラグインは壊れる。
3. **汎用的な機能 → `src/scriptvedit/effects/` 等のコアへ。**
   映像 Effect の実処理は `filters/video.py` に `_fx_<名前>(e, eff_idx, ctx)` を
   足し、`_FX_BUILDERS` に登録する（`ctx` は `_FxCtx`: `filters` へ append し、
   キャンバスを広げるなら `ctx.pad_size` を更新する。プラグインと同じ契約）。
   フィルタ生成以外の段で処理する Effect は `_FX_HANDLED_ELSEWHERE`（overlay 合成 /
   前処理 / 終端フレーム生成など「他の段で処理される Effect」の全集合）へ入れる。
   **どちらの表にも無い名前は `ValueError`** になる（黙って捨てると、フィルタの
   出ない空コマンドがそのままスナップショットに焼かれ以後永久に緑になるため）。
   網羅性は `tests/test_fx_dispatch.py` が manifest の全 Effect を実構築した
   内部 Effect 名で検証する。
   必ず `tests/` にスナップショット + エラーケースを追加する:
   **レイヤーは `tests/layers/testNN_*.py`、プロジェクト定義は `tests/projects.py` の
   `_SPECS`（＝構成の単一の正）へ `ProjectSpec` を足す。**
   `test_snapshot.py` は `_SPECS` を parametrize するだけなので個別登録は要らない。
   エラーケースは `tests/test_errors.py` に `check_*` を書き、末尾の `ALL_TESTS` へ登録する
   （未登録はメタテストが落とす）。
   **ffmpeg のフィルタで描けない図（コマごとに Python で描く図解アニメ）は
   `framekit.build` の上に作る**（§5「図解アニメ」。鍵・dry_run・最後のコマの保持・
   金型テストを受け継ぐ）。テストは図ごとのファイル（`tests/test_fx_<名前>.py`。
   画素・決定性・鍵・エラーケース・金型）に置く。
4. **マニフェストへの掲載は自動。** 網羅性テストが載せ忘れを検出するので、
   `manifest.py` を手で書き足す必要は基本的にない。
   手書きが要るのは `**kwargs` 経由の引数・単位・choices・notes・constraints だけで、
   それらの置き場は **`manifest_data.py`**（`manifest.py` ではない）。

## 7. コーディング規約

- **UTF-8 / CRLF**。
- **コメント・docstring・コミットメッセージは日本語。**
- コミットには `Co-Authored-By: Claude <noreply@anthropic.com>` 相当を含める。
- Expr の中で Python の `math.sin` 等を使わない。scriptvedit の `sin`/`cos`/`lerp`/`clip`
  （Expr を返す）を使う。
- **Expr は比較演算子・`%`・`//`・ビット演算を持たない。**
  `lt` / `gt` / `lte` / `gte` / `eq_` / `neq` / `mod` / `and_` / `or_` / `not_` / `if_` を使う
  （`u < 0.5` は TypeError。エラーメッセージが代替 API を案内する）。

## 8. やってはいけないこと

- **勝手に `git push` しない。** push は明示的に指示されたときだけ行う。
- **差分を目視確認せずに `--snapshot-update` しない。**
- **後方互換のための互換シムを増やさない。** このプロジェクトは後方互換不要の方針。
  古い API を残すのではなく、呼び出し側を新しい形に直す。
- `p.objects.append()` でオブジェクトを手動追加しない（レイヤー再実行で消える）。
