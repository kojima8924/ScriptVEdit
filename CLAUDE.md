# CLAUDE.md — コーディングAI向けの作業ガイド

このリポジトリで作業する AI（Claude Code / Grok CLI 等）が、最初から正しい前提で
動けるようにするための文書。**まず「2. 最初に読むもの」を実行すること。**

## 1. プロジェクト概要

Python の DSL で動画を構成し、ffmpeg でレンダリングするライブラリ。

- **1ファイル = 1レイヤー**。`main.py` が構成（設定・レイヤー順・出力）だけを持ち、
  各レイヤー `.py` が素材とエフェクトを宣言する。
- 演算子オーバーロードによる DSL: `<=` 適用 / `&` Effect連結 / `|` Transform連結 /
  `~` 品質ヒント / `+` force / `-` cache off。`~` は内容を削除せず、軽い代替を
  持たない op では通常と同一の処理を警告なしで行う（音声削除は `adelete()`）。
  タイムライン系: `obj[2:5]` 素材切り出し（素材時間）/ `obj @ 12` 絶対配置
  （タイムライン時間・非進行）/ `a >> b` 直後連結（pause.time() を挟める）。
- レイヤー .py の中で作った `Object` は exec 中に `Project` へ**自動登録**される。
  `p.objects.append()` の手動追加はしない（render 時のレイヤー再実行で消える）。
- **タイムラインの順次カーソルはレイヤーごとに 0 秒へリセットされる**
  （`project.py` の `_resolve_anchors`）。**レイヤー内は順次・レイヤー間は並行**で、
  総尺は全レイヤーの最大値。別レイヤーのものを後ろに置きたいときは
  `pause.time()` / `obj @ t` / `a >> b` / `time(name=...)` + `pause.until("名前.end")` を使う。
  これは DSL で最も事故が多い規則なので `describe` の constraints
  （`layer_timeline_independent`）にも載せてある。
- パッケージ本体は `src/scriptvedit/`（47モジュール）。`pip install -e .` で
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
- `effects`(40) / `transforms`(7) / `audio_effects`(7) / `factories`(32) /
  `objects`(19) / `object_methods`(9) / `project_methods`(14) / `expr`(98) / `plugins`(3)
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
pip install -e .[all]      # コアは標準ライブラリのみ。extras: morph/web/beat/tts/tools
pytest tests/              # 全テスト（約1分）
pytest tests/test_real_render.py --realrender  # 実レンダ回帰（選抜。CIと同じ）
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
- `project.py` は純粋な sink（誰からも import されない）。唯一の例外は
  `manifest.py`（`_inspect.getmembers(Project)` で型そのものが要る）。
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

**スナップショットの限界: dry_run は寸法を予測できない。** `formula()` の数式PNGは
dry_run 時点で未生成のため `base_dims=None` になり、`scale` の
pad（SEGVバリア, §4.1）が付かないコマンドになる。**formula + scale の pad 経路は
実レンダでしかカバーできない** ので、CI でも回る `pytest tests/test_real_render.py --realrender` の選抜（test92 を含む）で踏む。

### dry_run はキャッシュ状態に依存しない（＝旧「実レンダ後の罠」は解消済み）

**`render(dry_run=True)` が返すのは「キャッシュが空の状態で何を実行するか」**であって
「いま何が未生成か」ではない。だから `__cache__` に何があっても出力コマンドは同じで、
**実レンダの後にキャッシュを消さずスナップショットを回してよい**。

この契約を守っているのは次の4つの収集経路。**新しい中間生成物を足すときも必ず揃えること**
（1つでも「存在すればコマンドを出さない」を入れると、実レンダの有無でスナップショットが落ちる）:

| 経路 | 場所 |
|---|---|
| チェックポイント | `checkpoint.py` の `_collect_checkpoint_cmds`（全 step の `build_cmd()` を必ず呼ぶ） |
| web Object | `project.py` の `_collect_web_cmds` |
| レイヤーキャッシュ | `layercache.py` の `_collect_cache_cmds`（生成は `cache='make'` のときだけ。存在は見ない） |
| compute / from_project / xfade 生成物 | `objects.py` の `compute` / `from_project`、`media.py` の `_finalize_generated_object` |

最後の1つだけが存在チェックを dry_run 分岐より**前**に置いており、それが
「実レンダ後に test18 / test24 / test57 / test74 が落ちる」罠の正体だった
（test18 / test24 はさらに別原因も重なっていた。§5 の「中間生成物は映像専用」を参照）。
現在は全経路が揃っており、`tests/test_compute_cache_path.py` が
**cold と warm の dry_run 出力が完全一致すること**を検証している。

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
trim がクローンフレーム込みで尺を切ってしまう。
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
`ffmpeg.py` の `_decoder_input_args` に一本化されている。
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

`morph_to` / `explode_to` / `assemble_from` は終端フレーム生成 Effect
（`_TERMINAL_FRAME_EFFECTS`）で、bakeable な ops の末尾に1つだけ置ける。

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

### キャッシュ鍵（フィンガープリント）

- **素材は内容ハッシュ**（sha256 先頭16桁、`cache.py` `_file_fingerprint`）。
  パスにも mtime にも依存せず、同一バイト列なら別マシンでも鍵が変わらない。
  ただし環境ごとに生成内容が変わる素材は、その内容差が下流の鍵へ伝播する。
  高速化はプロセス内メモ化のみ。**ディスクキャッシュ（`__cache__/ffp.json`）は撤廃した**
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

**この「ガードを置かない」規則が当てはまるのは、再生成がタダ（内容から一意に
書き直せる）な成果物だけ。** `tts.py` のように**再生成に外部エンジンやネットワークが
要る**キャッシュは、命中ガードを置いてよい。その条件は2つ:
① 書き込みが原子的であること、② 0バイト等の明らかな残骸を命中扱いにしないこと。

### Object を `__new__` で手組みする箇所は属性を全部揃える

`text.py` の `_new_text_object` と `layercache.py` の `_load_cached_layer` は
`Object.__init__` を通さず属性を手で置く。**`__init__` が設定する属性は1つ残らず
同じ初期値で置くこと。** 足りない属性は呼び出し側の `getattr(..., None)` に救われて
長く潜伏し、誰かが直接属性アクセスを1行足した瞬間に
その経路（text / progress_bar / キャッシュ再生レイヤー）だけ AttributeError になる。

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

### その他

- **u 正規化**: エフェクト進行度は `clip((T-start)/dur, 0, 1)` で 0..1 に正規化する
  （`filters/video.py` の `_u_expr`。コア Effect もプラグインの `ctx["u"]` も
  この1関数から式を得るので、定義が乖離しない）。
- **音声側の u 正規化だけは `_u_expr` を通らない**（`filters/audio.py` が
  `clip((t)/{dur},0,1)` を直書きする）。adelay 前のオブジェクトローカル時間なので
  `start` を引かないのが正しいが、**定義箇所が2つある**ことは意識しておくこと。
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
    雛形生成（`src/scriptvedit/scaffold.py`）。生成直後に `python main.py` でレンダできる。
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
| `pngseq` | `%0Nd.png` | 可 | なし | 不可（単一パスへ確定できないので原子的出力もしない） |
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
   40 の Effect と 98 の Expr が既にある。車輪の再発明を避ける。
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
