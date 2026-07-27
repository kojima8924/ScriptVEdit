# 動画制作ガイド — scriptvedit で動画を作る人・AI へ

scriptvedit を「使う」立場(人間・コーディングAIの両方)向けの実践ガイド。
ライブラリの改修が目的なら、代わりにリポジトリ直下の `CLAUDE.md`(開発規約)を読むこと。

## 1. 最初にやること

```bash
python -m scriptvedit describe --format md   # 全機能カタログ(Effect 40種/Expr 98種/制約/雛形)
python -m scriptvedit new myvideo            # プロジェクト雛形の生成(直後にレンダ可能)
```

- **describe が常に正**。使い方に迷ったら `describe --name <機能名>` で正確なシグネチャを引く。
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
3. **レイヤーを書く** — `p.inspect("timeline.html")` で配置をガントチャート確認
4. **ドラフト確認** — `p.render(out, draft=True)`(半解像度・Web既定8fps)や
   `start=/end=` の部分レンダ(Webも交差フレームだけ撮影)で速く回す
5. **フレームを抽出して目視** — `p.storyboard()` / `p.thumbnail()` /
   `scriptvedit.testkit.extract_frame`。機械チェックだけで完成としない
   (完成動画があれば`source=out`を渡すとProject再構築なしで高速)
6. **audit を通す** — `p.audit()` の warning をゼロに(§5)
7. **本番レンダ** — 長時間になるため、エージェント経由なら
   セッションと運命を共にしない独立プロセスで起動する(§6)

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
  仕上げに **`normalize_audio()`**
- `duck_under()`はsidechain終端を自動padするため、ナレーション末尾へ手動の無音素材を
  足す必要はない。`normalize_audio()`は既定で48kHz化後にピークリミッターも適用する
- **BGM は動画より長い曲を選ぶ**(短い曲のループはつなぎ目が視聴者に気付かれる)
- ナレーションが複数行あるなら、**先に 1 本の音声ファイルへミックスしてから**
  `duck_under()` に渡す(行ごとの Object に個別ダッキングは構造的に成立しない)
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
- キャッシュが怪しいときは `python -m scriptvedit cache --clear`(安全。作り直されるだけ)

## 7. ナレーション(TTS)

`narrate()` は音声合成 + 同期字幕を 1 行で作る。バックエンドは自動選択:

| backend | 必要なもの |
|---|---|
| `voicevox` | VOICEVOX エンジンをローカルで起動(既定 `127.0.0.1:50021`) |
| `edge` | `pip install edge-tts` とネット接続 |
| `sapi` | なし(Windows のみ) |

合成結果はキャッシュされるので、同じ台詞の 2 回目以降は一瞬で返る。
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
