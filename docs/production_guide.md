# 動画制作ガイド — scriptvedit で動画を作る人・AI へ

scriptvedit を「使う」立場(人間・コーディングAIの両方)向けの実践ガイド。
ライブラリの改修が目的なら、代わりにリポジトリ直下の `CLAUDE.md`(開発規約)を読むこと。

## 1. 最初にやること

```bash
python -m scriptvedit describe --format md   # 全機能カタログ(Effect 40種/Expr 98種/制約/雛形)
python -m scriptvedit new myvideo            # プロジェクト雛形の生成(minimal は直後にレンダ可能)
```

- **describe が常に正**。使い方に迷ったら `describe --name <機能名>` で正確なシグネチャを引く。
- `--template explainer`(数式・字幕・BGM 入り)は `formula()` を使うので Playwright + Chromium が要る
  (`pip install "scriptvedit[web]" && playwright install chromium`)。
- ライブラリ本体の編集は不要。機能が足りなければプロジェクトの `plugins/*.py` に
  `@effect_plugin` で足す(§8)。

## 2. 推奨プロジェクト構成

```
myvideo/
├── main.py        # 構成だけ: configure + p.layer(...)×N + marker + render
├── scenes.py      # シーン表: 各シーンの開始秒・尺を定数で一元管理(複数レイヤー同期の要)
├── layers/        # 1ファイル = 1レイヤー(1関心事)
│   ├── audio.py   #   音声(ナレーション+BGM+効果音)は独立レイヤーに
│   ├── bg.py
│   └── subtitles.py  # 字幕は最前面(priority 最大)
├── plugins/       # この動画だけの自作エフェクト
├── assets/        # 素材(共有ライブラリからは自動コピーされてくる)
└── output/        # 動画・確認用フレーム・ログ
```

素材の共有ライブラリは環境変数 `SCRIPTVEDIT_ASSETS`(`os.pathsep` 区切りの
ディレクトリ列)に置き、`asset("bgm/曲.mp3")` で参照する。見つかった素材は
プロジェクト内へ自動コピーされ、フォルダごと持ち運べる自己完結形になる。

## 3. 制作ワークフロー

1. **台本とシーン表を先に固める** — シーン尺はナレーションの実長から決める
   (固定尺だと音声が切れる。`voice()` で先に合成して長さを測る)
2. **素材を集める・作る** — §4 の品質規則に従う
3. **レイヤーを書く** — `p.render(out, dry_run=True)` の後に `p.inspect("timeline.html")` で
   配置をガントチャート確認(inspect はレイヤーを実行しないので、先に dry_run か audit を回す)。
   時間の置き方は §3.1 の2つの規則に従う
4. **ドラフト確認** — `p.render(out, draft=True)`(半解像度・Web既定8fps)や
   `start=/end=` の部分レンダ(Webも交差フレームだけ撮影)で速く回す
5. **フレームを抽出して目視** — `p.storyboard()` / `p.thumbnail()` /
   `scriptvedit.testkit.extract_frame`。機械チェックだけで完成としない
   (完成動画があれば`source=out`を渡すとProject再構築なしで高速)
6. **audit を通す** — `p.audit()` の warning をゼロに(§5)
7. **本番レンダ** — 長時間になるため、エージェント経由なら
   セッションと運命を共にしない独立プロセスで起動する(§6)

### 3.1 タイムラインの2つの規則(黙って重なる・ずれる。エラーにならない)

- **順次カーソルはレイヤーごとに 0 秒から始まる**。`a.py` で `obj.time(5)` と書いても、
  別レイヤー `b.py` の先頭はまた 0 秒(レイヤー内は順次・レイヤー間は並行)。
  別レイヤーの後ろに置くときは `obj.time(3, name="intro")` + `pause.until("intro.end")`
  (尺の変更に自動追従する)か、`obj @ 12` / `obj @ "intro.end"` を使う。
  `a >> b` は同じレイヤーの中でしか使えない。複数レイヤーの開始秒は `scenes.py` の
  定数か、アンカー名で揃える
- **Object は作った時点の位置で登録される**。並び順は `Object(...)` / `text(...)` を
  呼んだ順で決まり、後から呼ぶ `time()` / `show()` では動かない。音声を作って `time()` で
  カーソルを進めてから字幕の `text()` を作ると、字幕は音声の**後ろ**に出る。
  **字幕の `text()` を先に作ってから音声 Object を作る**(`narrate()` はそうなっている)

## 4. 品質規則(実制作のレビューで確立された経験則)

### 文字
- **太く・大きく・縁取り(または影・下地)**。1080p 目安: 本文 44px 以上、
  結論 80px 以上、注釈 32px 以上。`text(..., border=3, border_color="black")` を基本形に
- 一画面 2〜3 行まで。入らないなら文字を小さくせず**文章を分割**する
- 日本語フォントは環境変数 `SCRIPTVEDIT_FONT` か `text(font=...)` で明示できる

### 画像素材
- **AI 生成画像は使う前に 1 枚ずつ必ず目視する**(未確認の 1 枚が壊滅的な品質で
  完成動画に載る事故は実際に起きる)
- **構造が厳密な図(カレンダー・表・グラフ・盤面・回路)は AI 画像生成に頼らない**。
  web Object(HTML/Canvas)や `diagram()` でコード描画する
- AI 画像が向くのは抽象背景・写実風景・イラスト

### グラフ・図解
- **ラベルと目盛りの重なりを、フレームを抽出して目視**する
- 重要点は目盛り・基準線・交点マーク・直接ラベルで強調する

### 音声
- **BGM には必ず `duck_under()`**(ナレーション中だけ自動で音量が下がる)、
  仕上げに **`normalize_audio(mode="linear")`**
- `normalize_audio()` の既定(`mode="dynamic"`)は 1 パスの loudnorm で、声の無い区間の
  BGM を目標付近まで持ち上げ、声が入ると沈める(ポンピング)。ナレーション＋BGM の動画は
  `mode="linear"`(音声だけ全編を 1 回測ってから一定の増幅＋リミッター。区間どうしの
  音量差が変わらない)を使う。測定結果はキャッシュされるので、映像だけ直した再レンダでは
  測り直さない
- `duck_under()`はsidechain終端を自動padするため、ナレーション末尾へ手動の無音素材を
  足す必要はない。`normalize_audio()`は既定で48kHz化後にピークリミッターも適用する
- 素材のサンプリング周波数・チャンネル数は揃えなくてよい(混ぜる前に全音声が
  48kHz・ステレオへ自動で揃う)。モノラルの TTS は中央定位になり、各チャンネルは
  約 -3dB(パンの法則。左右の合計パワーは元と同じ)。これは仕様
- 入力がすべてモノラル(TTS ナレーションだけの動画など)だと、以前はモノラルで
  出力されていたので、`normalize_audio()` なしでは再生音量が約 3dB 下がる。
  音量を揃えるには `normalize_audio()` を使う(目標値どおりになる)
- `duck_under()` の検出はステレオ化の前の音声で行うので、モノラルのナレーションでも
  `threshold` は元の音量と比べられる(下げ幅はナレーションのチャンネル数に左右されない)
- **BGM は動画より長い曲を選ぶ**(短い曲のループはつなぎ目が視聴者に気付かれる)
- BGM のループは `bgm <= loop()` と書き、**引数なしの `bgm.time()` を付けない**
  (`time()` は尺を素材の長さで確定させるので、`bgm.time() <= loop()` はループしない)。
  尺を決めるなら `bgm.time(秒) <= loop()` か `bgm.until("outro.end") <= loop()`
- ナレーションが複数行(複数の音声 Object)なら、**全部を 1 回の `duck_under()` に渡す**
  (`bgm <= duck_under(n1, n2, n3)`。1 つの BGM に `duck_under` は 1 回しか付けられない)
- 楽曲のライセンス(利用可能なプラットフォーム・クレジット表記義務)は必ず確認する

### 構成
- 冒頭 5 秒で問いを出す(フック)。結論の数字は最大サイズ + 効果音
- 数式は `formula_lines` で段階的にリビール
- 前提条件の注記を画面隅に。エンディングに一般化した学びを一文

## 5. audit — 機械チェックを先に通す

```python
p.audit()                    # §4 の規則の自動化版。warning をゼロにする
p.render(out, strict=True)   # warning があればレンダ前に停止(自動フロー向け)
```

audit が通ることは下限であって完成ではない。§3-5 の目視が本番。

## 6. レンダ運用

- 高解像度の本番レンダは長い(1080p×2分で数十分)。**AI エージェントが起動する場合は、
  エージェント停止と同時に殺されない独立プロセスで実行する**こと
  (エージェントの子プロセスとして流すと、途中終了で壊れた mp4 が残る):

```powershell
# Windows の例
Start-Process python -ArgumentList "main.py" -WorkingDirectory <プロジェクト> `
  -RedirectStandardOutput <プロジェクト>\output\render.log
```

```bash
# POSIX の例
nohup python main.py > output/render.log 2>&1 &
```

- 完了はログかプロセス監視で確認する
- **長尺は Windows のコマンドライン長に注意**。本レンダは素材ごとに `-i` を1本足すので、
  素材数が数百になるとコマンドラインが Windows の上限(32,767 文字)を超え、ffmpeg を起動する
  前に `FileNotFoundError: [WinError 206]`(ファイル名または拡張子が長すぎます)で落ちる
  (4000 字超のフィルタは自動で一時ファイルへ逃がすが、`-i` の列は逃がせない)。
  `p.render(out, parallel=N)`(H.264 出力のみ。各区間は重なる素材だけ、音声は音声を持つ素材だけを
  入力にする)で1本あたりの入力を減らすか、
  章ごとに別の Project でレンダして後で連結する
- キャッシュが怪しいときは `python -m scriptvedit cache --clear`(安全。作り直されるだけ)

## 7. ナレーション(TTS)

`narrate()` は音声合成 + 同期字幕を 1 行で作る。バックエンドは自動選択:

| backend | 必要なもの |
|---|---|
| `voicevox` | VOICEVOX エンジンをローカルで起動(既定 `127.0.0.1:50021`) |
| `edge` | `pip install edge-tts` とネット接続 |
| `sapi` | なし(Windows のみ) |

合成結果はキャッシュされるので、同じ台詞の 2 回目以降は一瞬で返る。
`backend="voicevox"` を明示していれば、VOICEVOX を止めていても一度合成済みの台詞は
キャッシュから使える(警告が1回出る)。新しい台詞や変更した台詞があるときだけ
エンジンの起動が必要になる。既定の `backend=None` ではエンジン停止中は `edge` が自動で
選ばれ(edge-tts が無ければエラー)、VOICEVOX のキャッシュは使われないので、
VOICEVOX の声で作るプロジェクトは `backend="voicevox"` を書いておく。
表示文を短くする場合は`subtitle_text`、日本語折り返しは`subtitle_max_chars`、
行数上限は`subtitle_max_lines`、画面端保護は`subtitle_safe_area`を使う。

## 8. 機能が足りないとき — プラグイン

1. まず `describe` で既存機能を確認する(大抵ある)
2. 無ければプロジェクトの `plugins/*.py` に書く(自動読込):

```python
from scriptvedit import effect_plugin

@effect_plugin("my_glow", bakeable=True,
               params={"radius": {"type": "number", "default": 10}})
def build_my_glow(params, ctx):
    """自作グロー(1行目が describe に載る)"""
    return [f"gblur=sigma={params['radius']}"]
```

雛形は `describe` の `usage.plugin_template`、参考実装はリポジトリの
`plugins/example_*.py`。

3. ライブラリ本体の不具合・限界と確信したら、自分で本体を直さず issue として報告する
   (本体にはスナップショットテスト等の別の開発フローがある。`CLAUDE.md` 参照)

## 9. やってはいけないこと

- ライブラリ本体を制作作業の中で書き換えない(プラグインで足す)
- AI 生成素材を目視せずに使わない
- `p.objects.append()` で手動登録しない(作るだけで自動登録される)
- 文字サイズに lambda を渡さない(FFmpeg クラッシュ回避のため構築時エラーになる。
  `scale()` Effect で代用)
- lambda の中で Python の `math.sin` を使わない(scriptvedit の `sin`/`lerp`/`clip` を使う)
