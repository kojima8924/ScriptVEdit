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
- パッケージ本体は `src/scriptvedit/`（41モジュール）。`pip install -e .` で
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
  `objects`(15) / `object_methods`(9) / `project_methods`(14) / `expr`(98) / `plugins`(3)
  （件数は変動する。正は `describe` の実測で、整合は tests/test_issue17_docs.py が検証する）

各 Effect エントリには `bakeable` フィールドがあり、キャッシュに焼けるかが分かる。
Effect / Transform / AudioEffect の `respects_fast_hint` は、その op が `~` の
軽い代替処理を実装しているかを示す。

その他の CLI: `python -m scriptvedit new <path>`（プロジェクト雛形生成）、
`python -m scriptvedit cache`（統計/GC/全削除）、
`python -m scriptvedit watch`（変更監視して再実行）。

補足として `README.md` に DSL 記法と設計思想がまとまっている。

## 3. 開発ワークフロー

```bash
pip install -e .[all]      # コアは標準ライブラリのみ。extras: morph/web/beat/tools
pytest tests/              # 全テスト（約1分）
python tests/render_all.py # 実レンダリング（重い。出力は tests/output/）
python scripts/check_unused_imports.py   # 未使用importの検出（CIでも実行）
```

未使用 import は CI で失敗にする。存在確認のためだけの import は `# noqa` を
付けて意図を示す（`__init__.py` の再エクスポートは対象外）。

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
実レンダでしかカバーできない** ので `tests/render_all.py` の `test92` で踏む。

### 罠: 実レンダの後はキャッシュを消してからスナップショットを回す

実レンダでチェックポイントが**実体化**すると、dry_run が生成する ffmpeg コマンドが
変わり（キャッシュ済み中間ファイルを入力に使うようになる）、スナップショットが落ちる。

```bash
python -m scriptvedit cache --clear   # __cache__ を全削除
pytest tests/
```

キャッシュはリポジトリルートの `__cache__/` に置かれる（`tests/conftest.py` が
実行ディレクトリに依存しないようルートへ chdir する）。実レンダ出力は `tests/output/`。

## 4. FFmpeg 8 の地雷（実装済みの回避策。壊さないこと）

以下はすべて実測で踏み抜いた既知の不具合と、その回避策。**フィルタ生成に手を入れる
ときは、これらを外さないこと。**

### 4.1 `scale(eval=frame)` + `rotate` で SEGV (0xC0000005)

`pad` や `format=rgba` **単体では防げない**。固定サイズ・中央配置の `pad` の直後に
**`copy` フィルタ**を挟んでバッファを分離するのが唯一の回避策。

- `filters/video.py` の `_fx_scale` … pad サイズを scale 式の
  固定格子サンプリングで決定する（通常101点。振動系関数 sin/cos/tan/mod/random を
  含む式は格子とエイリアスして点間ピークを取りこぼすため、`_expr_has_oscillatory`
  判定で4999点の密格子に切り替える）。
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

### 4.6 長大フィルタは Windows のコマンドライン長制限に当たる

4000文字以上の `-filter_complex` / `-vf` / `-af` は一時ファイルへ書き出し、
FFmpeg 8 の `-/filter_complex <path>` 構文に自動で切り替える
（`ffmpeg.py` の `_FILTER_SCRIPT_THRESHOLD = 4000` と `_externalize_long_filters`、
`_run_ffmpeg` が finally で一時ファイルを削除）。

## 5. 設計規約（コードを変更するときに守ること）

### bakeable / live

Effect は2種類ある。

- **bakeable** … 中間ファイル（チェックポイント）へ焼き込みキャッシュできる。
  `state.py` の `_BAKEABLE_EFFECTS` に名前を登録すると
  キャッシュ対象になる。Transform は全て bakeable。
- **live** … 毎レンダで ffmpeg フィルタとして適用する。`speed` / `reverse` /
  `freeze_frame`（`_TIME_LIVE_EFFECTS`）は時間軸を変えるためチェックポイントの
  尺基準と衝突する。`move` / `shake` は overlay 座標の変調なので焼けない。

判定は `cache.py` の `_is_bakeable`。**新しい Effect が本当に「焼いても同じ絵に
なる」ものかを確かめてから登録すること。** 時間依存の尺変更を伴うものは live のまま。

`morph_to` / `explode_to` / `assemble_from` は終端フレーム生成 Effect
（`_TERMINAL_FRAME_EFFECTS`）で、bakeable な ops の末尾に1つだけ置ける。

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

### `~` 品質ヒントの契約

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
（`text.py` の `_ensure_textfile`、`chapters.py` の `_write_chapters_metadata` 等）。
ファイル名が内容ハッシュなので、切り詰められた残骸が一度でも残ると以後どのレンダでも
再生成されず黙って使われ続ける。「存在すればスキップ」ガードも置かない
（残骸を永久に温存する）。

### pad でキャンバスを広げたら `pad_size` を更新する

overlay の中央配置は `(W-pad_size[0])/2` で計算される
（`filters/video.py` の `_build_move_exprs`）。キャンバスを広げる Effect が
`pad_size` を更新しないと配置がずれる。既存例: `drop_shadow` / `outline` / `rounded`。
プラグインからは `ctx["expand_pad"](dw, dh)` / `ctx["set_pad"](w, h)` を使う。

### その他

- **u 正規化**: エフェクト進行度は `clip((T-start)/dur, 0, 1)` で 0..1 に正規化する
  （`filters/video.py` の `_u_expr`。コア Effect もプラグインの `ctx["u"]` も
  この1関数から式を得るので、定義が乖離しない）。
- **`_resolve_obj_duration`**（`project.py`）は `obj.length()` ベース。
  trim / atempo を反映した加工後の尺を返す（チェックポイントのベイクと同一基準）。
  0 は返さない（`clip((t-start)/0,…)` のゼロ除算で ffmpeg が EINVAL になるため、
  fallback=5 へ落とす）。
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

   `ctx` には `u` / `u_T` / `start` / `dur` / `fps` / `width` / `height` / `label` /
   `obj` / `project` / `parse_color` / `escape_path` / `pad_size` / `expand_pad` /
   `set_pad` が入る。ビルダーは ffmpeg フィルタ文字列の `list` を返す。
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
   必ず `tests/` にスナップショット + エラーケースを追加する
   （`tests/layers/testNN_*.py` にレイヤーを置き、`test_snapshot.py` / `test_errors.py` に登録）。
4. **マニフェストへの掲載は自動。** 網羅性テストが載せ忘れを検出するので、
   `manifest.py` を手で書き足す必要は基本的にない。

## 7. コーディング規約

- **UTF-8 / CRLF**。
- **コメント・docstring・コミットメッセージは日本語。**
- コミットには `Co-Authored-By: Claude <noreply@anthropic.com>` 相当を含める。
- Expr の中で Python の `math.sin` 等を使わない。scriptvedit の `sin`/`cos`/`lerp`/`clip`
  （Expr を返す）を使う。

## 8. やってはいけないこと

- **勝手に `git push` しない。** push は明示的に指示されたときだけ行う。
- **差分を目視確認せずに `--snapshot-update` しない。**
- **後方互換のための互換シムを増やさない。** このプロジェクトは後方互換不要の方針。
  古い API を残すのではなく、呼び出し側を新しい形に直す。
- `p.objects.append()` でオブジェクトを手動追加しない（レイヤー再実行で消える）。
