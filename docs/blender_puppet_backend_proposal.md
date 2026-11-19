# ScriptVEdit × Blender — 外部パペットレンダラ統合設計案

状態: **初版の切り絵バックエンドを実装済み**／2026-11-20
現行API・JSONの正は [blender_puppet.md](blender_puppet.md)。以下は将来案を含む設計の記録であり、
`alpha` / `backend` 引数、汎用 source_generator、メッシュ変形・IK は現行APIに含まれない。
目的: ScriptVEditを「2D/2.5Dキャラクターのパーツリグ・アニメーション・透過映像までプログラムから制作できる」動画制作基盤へ拡張する。ゲーム用サウンドノベルと、ニコニコ向けの自作BB劇場素材の両方で利用可能にする。

## 現行の資産と接続点

- `Project.layer("...py")` / `Object(path)` がレイヤーと映像素材を扱う。`Project.layer(cache=...)` はFFmpeg合成レイヤーのキャッシュ機構である。
- `stills()` / `frames()`（`src/scriptvedit/stillseq.py`）はアルファ付き可逆 `qtrle argb .mov` の素材をキャッシュに生成して `Object` を返す。`frames()` の `draw(i)` はPIL/numpyであり、Blenderの `bpy` をそのコールバックから**毎フレーム別プロセス起動する形にはしない**。
- `src/scriptvedit/cache.py` の `_file_fingerprint` / `_sig_key` 等は既に内容ハッシュを使っている。外部生成器も同じ仕様思想を維持する（外部ツール版数・依存ファイル全体・設定を鍵とする）。
- `@effect_plugin` はFFmpegフィルタを追加するプラグインで、任意外部レンダラーの**素材生成ソースプラグインではない**。まずPythonの外部ラッパーを作り、必要性が実証された場合にソース生成APIを一般化する。
- 既存のライブラリの中心依存は標準ライブラリ、ライセンスはMIT。Blender自体をインストール時の必須依存にはしない。

## 3段階の設計

### Phase A — Blenderバックエンドの独立PoC（コア変更なし）

- キャラの透明PNGレイヤーを入力し、`rig.json`、`motion.json` から構図を構築する。
- Blenderの`--background --python ... -- config.json`（OSのプロセスとして `subprocess.run([...], shell=False)`）で、1回の起動につき1つのカットの全フレームを描画。
- `bpy` 側ではオルソグラフィックカメラ、画像テクスチャを貼った平面／2Dメッシュ、親子関係・必要最小限のボーン／回転中心を生成。最初から複雑なGrease Pencil APIには依存しない。
- `scene.render.film_transparent=True`、出力`PNG`・`RGBA`、解像度/FPS/フレーム範囲固定で連番を書き出す。
- FFmpegで `qtrle argb .mov` に変換。出力が無ければ例外。正常終了後、`ffprobe`と抽出フレームでフレーム数・サイズ・アルファ・先頭／最終静止を確認して、ScriptVEditの `Object(...).time(...)` に渡す。
- `dry_run=True`ではBlenderプロセスを起動せず、出力予定・依存ファイルの検査とFFmpegコマンドの計画を表示する。欠落する外部生成物のためにdry_runが落ちないよう、現在の未生成キャッシュパスの規則に合わせる。
- キャッシュは**生成前**に内容指紋を確定し、部分レンダ中も同じキーの生成物を共有。BLENDER_VERSION、パーツ素材のSHA、rig/motion JSON、bridge実装版、FPS、サイズ、フレーム範囲、seed、色管理、レンダエンジンをすべて含める。書き込みは一時ファイル＋原子的renameとする。

### Phase B — ScriptVEdit正式API（外部ソース生成の抽象化）

将来の提案API。**現状は存在しない。**

```python
# layers/actor.py — 提案用の擬似API
from scriptvedit import *
from scriptvedit.blender import puppet_clip

actor = puppet_clip(
    rig="assets/rize/rig.json",
    motion="assets/rize/motions/talk_blink.json",
    duration=3.2, fps=24, size=(800, 800), alpha=True,
    backend="blender",
)
actor.time(3.2) <= move(x=0.42, y=0.55)
```

- API利用者はBlenderを直接意識せず、入力・出力とモーションだけ指定できる。
- 実装は `blender_clip` -> `source_generator`（汎用） -> `Object` の順に分離。Blender固有のコマンドと汎用キャッシュを混ぜない。
- 将来の描画バックエンドとしてPillow/NumPy、別のI2Vサービス、事前生成透明映像を追加できる。**Blenderを唯一の経路としない**。
- 一般的な瞬き・口の開閉・単純な2D移動だけで済む箇所は、軽量な `frames()` またはScriptVEdit既存Effectのほうがコスト効率で優る場合がある。

### Phase C — 2DアニメーションDSLの蓄積

- キャラクター素材を `parts/` に分離して、`rig.json`で親子階層・表示順・回転中心・変形領域・表情差分を管理。
- `motion.json` に`blink`、`breath`、`talk`、`bow`、`point`、`surprised`、`shake_head`等のモーション・キーフレーム／イージングを保存。
- 最初は**各パーツの回転・スケール・位置と表情の切替**だけを扱い、後からメッシュ変形、ボーンウェイト、IK、物理揺れを追加。
- カメラ・背景・特殊エフェクト・字幕・BGMは原則ScriptVEditへ残す。Blenderには主に「キャラだけをアルファ付きで動かす」責務を持たせる。
- 通常会話で再利用する短いループは透過動画／アニメパーツとして保存、カット固有の大きな動きは専用イラストや画像生成AI動画も混ぜてScriptVEditで仕上げる。

## データ契約の案

```json
{
  "schema": 1,
  "character": "rize_apprentice",
  "canvas": [768, 768],
  "parts": [
    {"id": "body", "path": "parts/body.png", "parent": null, "pivot": [0.5, 0.86], "z": 0},
    {"id": "head", "path": "parts/head.png", "parent": "body", "pivot": [0.5, 0.78], "z": 10},
    {"id": "eyes_closed", "path": "parts/eyes_closed.png", "parent": "head", "z": 20}
  ]
}
```

`pivot`はパーツ画像内の正規化座標とし、モーション時刻は秒／フレームを厳密変換する。実データではアンカー、親子座標系、マスク、目・口の差分の排他表示、境界・描画順、ループ端点、一時的な前後遮蔽を追加する。位置合わせ・ボーン・ウェイトをAIが完全自動化できるとは最初から仮定しない。

## 利用シーン

**Rune Circuit 1-0**

- 弟子入り前の私服姿のリゼの上半身モデルで「呼吸・瞬き・首を傾げる・少し頭を下げる・驚く」を試す。
- ガルドは腕組み・椅子から身を乗り出す専用ポーズ、コロは輪や結晶のパーツ制御を基本にする。
- 出力はキャラ単体透過アニメーション→ScriptVEditで2160×720等の横長背景と合成→Godot用不透明OGV＋first/HOLDフレーム。16:9安全領域への収まりとXperia実機表示を確認。
- 旧1-0台本（工房の朝）はテスト用旧素材。正式な「弟子入り面接」台本へは段階的に移行し、固定台詞ID・選択肢・セーブ進行を壊さない。

**ニコニコの自作BB劇場素材**

- 同じ`puppet_clip`と`motion.json`から、人物・小物の単体アクション、反復可能な待機、驚き、指差し、ジャンプなどをレンダ。
- 編集マスターは**RGBAのPNG連番またはqtrle MOV**で保持し、必要な納品先にだけ青／緑の単色背景（BB/GB）を合成する。最初から青を背景に焼き込まない。衣装の色と背景色の衝突、キーイングの輪郭に注意。
- 動画編集用には各動きの開始/終了アンカー、ループ端点、キャラ基準点、表情、キャンバスサイズとFPSを付随JSONに記録する。
- 実在人物の肖像や既存音源・画像の再利用には別途権利と利用条件を確認する。自作のオリジナルパーツならこの問題は抑えられる。

## 品質・性能／CI基準

1. GUI操作なしで0→成果物が生成できる。新しい機械でBlenderがない場合、明瞭なエラーか既存成果物だけの合成ができる。
2. 任意ポーズの頭・目・口・腕が切断したり不自然に伸びたりしない。髪、衣装、半透明輪郭の破綻はフレーム抽出で確認する。
3. `alpha=0`領域・部分透明・境界ハローの評価。BB/GB映像の輪郭も確認する。
4. 背景・字幕・音声を変えただけではパペット素材は再レンダしない。キャラ1体のモーション変更は他キャラへ波及させない。
5. キャッシュキー、素材依存、異常終了時の一時ファイル回収、フレーム数とfps（たとえば24fps×3秒＝72枚）、タイムラインの最後のコマHOLDをテストする。
6. `--background`環境でのEevee/レンダ安定性はOS・GPU・Blender版を固定して実測する。GPUなしCIは必ずしも同じ速度・品質を保証しない。
7. 最初の目標はリゼ1体3秒→ScriptVEdit合成→PNG/MOVと検証動画を作ること。いきなりLive2D相当の高機能メッシュ変形エディタは作らない。

## ライセンス境界（重要）

ScriptVEditはMIT、BlenderはGPL。Blender公式FAQでは、**Blender Python APIを利用する配布スクリプト／アドオンにはGPL互換の条件**が適用されると説明。一方で**Blenderの外部からJSONを生成してCLIプロセスを起動する独立ソフト**は、BlenderのGPLを継承しない例として説明されている。

そのため、`scriptvedit.blender`等のホストラッパー（Blenderコードをimportせず外部CLIを実行するだけ）はScriptVEditのMITで維持し、`bpy`をimportするBlender内部用スクリプトは**GPL互換の別配布・別ライセンス範囲**にする方針。ランタイムのBlenderやGPL素材をゲームAPKへ同梱しない。生成動画・画像はBlender自体のGPLの影響を受けない（ただし入力素材自体の著作権は別）。

ライセンスの厳密な適用範囲は配布前に確認する。法律相談ではない。

## 公式資料

- BlenderヘッドレスCLI: https://docs.blender.org/manual/en/5.0/advanced/command_line/arguments.html
- Blender透明レンダ: https://docs.blender.org/manual/en/4.5/render/eevee/render_settings/film.html
- BlenderライセンスとAPIスクリプト: https://www.blender.org/about/license/ ／ https://www.blender.org/support/faq/
- Grease Pencil 4.3のAPI刷新: https://developer.blender.org/docs/release_notes/4.3/grease_pencil_migration/
