# 動画レビュー用 AI サービスの調査（2026-10-10 時点）

scriptvedit で書き出した完成動画（日本語ナレーション＋焼き込み字幕、10〜20分、1920x1080・30fps、BGM あり）を
AI にレビューさせ、指摘をコーディング AI（Claude Code）へ渡す機能を作るための下調べ。

- 調査日: **2026-10-10**（出典の確認日はすべてこの日）
- 為替は **1ドル＝約150円** で概算した。
- 「事実」は公式ページ（または明記した二次情報）で確かめたもの。「推測」「概算」は本文中でそう書く。
- 料金の概算は、出力（指摘の JSON）を 1 回あたり約 3,000 トークン、プロンプト本文を約 2,000 トークンと**仮定**した。
  思考（thinking / reasoning）トークンは出力として課金されるモデルが多く、実際はこれより増えうる。

---

## 1. 結論（先に要約）

| 順位 | 候補 | 理由 |
|---|---|---|
| **第1候補** | **Google Gemini API**（日常は Gemini 3.8 Flash、最終確認は Gemini 3.1 Pro Preview） | 音声込みの動画をそのまま入力でき、`fps`・区間（`start_offset`/`end_offset`）・解像度（`media_resolution`）を指定できる。20分の全編レビューが Flash で**約0.1ドル（約15円）**と桁違いに安い。日本語の音声・文字にも強い。公開ベンチ（Video-MME-v2）でも首位。 |
| **第2候補** | **TwelveLabs Pegasus 1.5 / 1.6**（直接 API。または Amazon Bedrock） | 「時間区間ごとの構造化メタデータ」を出すことに特化しており、タイムスタンプ付きの指摘の形に最も近い。インデックス不要で最長2時間。ただし20分で**約0.6ドル（約90円）**と Gemini Flash の約6倍、日本語は公式ページで確認できず（二次情報では「部分対応」）、既定で学習利用あり（オプトアウト要）。 |
| 補助（必須に近い） | **決定的な検査＋Claude（Claude Code 自身）** | 字幕のはみ出し・重なりのような**1フレーム単位の不具合は、1fps でサンプリングする動画 LLM では原理的に見落とす**。scriptvedit はタイムラインを全部知っているので、字幕の矩形・重なり・ラウドネスはコードで検査し、怪しいコマだけ画像にして Claude（Claude Code の画像読み込みでも API でも可）に見せるのが確実。 |

ローカル（RTX 4060 8GB）での Qwen3-VL は「動く」が、長尺・音声込みのレビュー品質は API 勢に大きく劣る（後述のベンチ）。Amazon Nova は**音声を処理しない**ため不適。

---

## 2. 比較表

| サービス / モデル | 料金（公式） | 20分全編1回の概算 | 最大長 | 音声理解 | 日本語 | 区間指定 | タイムスタンプ | 性能の目安 | ローカル |
|---|---|---|---|---|---|---|---|---|---|
| TwelveLabs Pegasus 1.5 / 1.6（直接） | 入力動画 $1.75/時、出力 $7.5/100万tok | 約 $0.61（約90円） | 2時間（解析窓）、4時間までの動画の一部 | あり（映像＋音声＋画面文字） | **未確認**（公式は「12言語」とのみ。二次情報では日本語は partial） | あり（`start_time`/`end_time`、最短1秒、同期は4秒） | あり（区間ごとの start/end を float 秒で） | 自社評価でセグメント分割が Gemini 3.1 Pro を上回ると主張（0.428 vs 0.337） | 不可 |
| Pegasus 1.5（Amazon Bedrock） | 入力 $0.00049/秒（≒$1.76/時）、出力 $0.0075/1000tok | 約 $0.61 | 未確認（1.2 は 1時間・2GB） | あり | 同上 | 未確認（Bedrock の 1.5 の項目は未確認） | 同上 | 同上 | 不可 |
| Gemini 3.8 Flash | 入力 $0.75 / 出力 $3.75（100万tok、2026-12-31まで。2027年から倍） | 約 $0.10（約15円）※既定解像度 | 既定解像度で約3時間、高解像度で約1時間（100万tok） | あり（音声込み） | 強い（推測ではなく実績多数だが、日本語の数値ベンチは未確認） | あり（`start_offset`/`end_offset`、`fps` 指定） | MM:SS で参照・出力できる | Video-MME-v2: Gemini-3-Flash 42.5（音声/字幕あり） | 不可 |
| Gemini 3.1 Pro Preview | 入力 $2 / 出力 $12（≤20万tok）、$4 / $18（>20万tok） | 約 $0.3〜0.5 ※既定解像度・思考込みの幅 | 同上 | あり | 強い | あり | あり | Video-MME-v2: Gemini-3-Pro 49.4（全モデル首位、人間 90.7） | 不可 |
| OpenAI GPT-5.x | 例: GPT-5.4-mini 入力 $0.75 / 出力 $4.50、GPT-5.5 $5 / $30 | 約 $0.35（5.4-mini、2秒に1枚・960x540＋文字起こし） | 動画入力は**公式に記載なし**（画像は1リクエスト1,500枚まで） | 動画の音声は不可→別途文字起こし（$0.003〜0.006/分） | 文字起こし・画像OCRは可 | 自分で切り出す | 自分でフレームに時刻を付ける | Video-MME-v2: GPT-5 37.0 | 不可 |
| Anthropic Claude | 例: Sonnet 5.5 入力 $2 / 出力 $10、Haiku 5.5 $0.10（≤10万tok）/ $0.50（>10万tok） | 約 $0.6（Sonnet 5.5・5秒に1枚・1280x720＋文字起こし） | 動画入力なし。画像は1リクエスト600枚まで | 不可→別途文字起こし | 画像OCR・日本語は可 | 自分で切り出す | 自分でフレームに時刻を付ける | 動画ベンチの公式値なし（未確認） | 不可 |
| Amazon Nova 2 Lite | 入力 $0.30 / 出力 $2.50（二次情報） | 約 $0.11 | S3 で 1GB、推奨は1時間未満 | **なし**（映像のみ） | 多言語の画面文字は「限定的」と公式が明記 | 未確認 | 時間区間の特定は可と記載 | 672x672 へ歪めて縮小するので字幕の検査に不向き | 不可 |
| Qwen3-VL-8B / 4B（ローカル） | 無料（電気代） | 0円 | 実用はGPUメモリ次第（8GB では短い区間） | なし（別途 Whisper 等） | 中程度（未測定） | 自分で切り出す | テキストのタイムスタンプ整列を持つ | Video-MME-v2: 8B 18.2 / 4B 17.6 | **可**（GGUF 4bit） |
| Qwen3-Omni-30B-A3B / Qwen3.5-Omni | API（DashScope）またはローカル | 未確認 | Qwen3.5-Omni: 720p・1fps で約400秒 | あり | 未確認 | — | — | Video-MME-v2: Qwen3-Omni-30B 17.1 | 8GB では不可（推測。フル精度で 80GB 級） |

---

## 3. TwelveLabs Pegasus（詳細）

### 3.1 版
- **Pegasus 1.5**: 2026-04-20 公開。インデックスなしで動画を直接解析、時間区間つきの構造化メタデータ抽出（セグメント定義）、汎用の解析（`analysis_mode="general"`）。
- **Pegasus 1.6**: 2026-10-06 公開。一人称映像の理解、画像の解析（1〜20枚）、固有名の認識、セグメント内のイベント抽出などを強化。
- **Pegasus 1.2 は 2026-08-18 に削除**。`model_name` を省くと 1.5 が使われる。
- 1.5 の文脈長は入出力合わせて 261,120 トークン、最大出力 98,304 トークン（検索で得た公式リリースノートの要約。下記 URL）。
- 出典: <https://docs.twelvelabs.io/docs/get-started/release-notes> ・ <https://docs.twelvelabs.io/docs/concepts/models/pegasus> ・ <https://www.twelvelabs.io/pegasus>（いずれも 2026-10-10 確認）

### 3.2 料金（直接 API）
| 項目 | 料金 |
|---|---|
| Analyze API（Pegasus 1.6/1.5）入力動画 | **$1.75 / 時間** |
| 入力画像 | $3 / 100万トークン |
| 出力 | **$7.5 / 100万トークン** |
| インデックス作成（Search 用。レビューには不要） | $2.50 / 時間、埋め込みの基盤費 $0.09 / 時間 |
| 無料枠 | Free プランでインデックス 600 分まで、インデックスは作成から90日 |

- 出典: <https://www.twelvelabs.io/pricing>（2026-10-10 確認）
- 1.5 以降は**インデックス不要**で Analyze できるので、レビュー用途の費用は「入力動画＋出力」だけ。
- 概算（出力 3,000 トークン仮定）:
  - 1時間: $1.75 + $0.02 ≒ **$1.77（約270円）**
  - 20分全編: $0.583 + $0.023 ≒ **$0.61（約90円）**
  - 1分の区間 × 10回: ($0.029 + $0.023) × 10 ≒ **$0.52（約80円）**（出力の比重が増えるので区間を細かくしても大きくは下がらない）
- 無料枠の 600 分が Analyze に効くかは公式ページから読み取れず **未確認**。

### 3.3 入力の制限
- 長さ: 最長2時間の解析窓（2026-09-10 に、4時間までの動画の一部を解析できるよう拡張。最短1秒）。
- サイズ: 公開 URL で 4GB まで、ローカル直送は 200MB まで、マルチパートのアップロードで 10GB まで（2026-09-22）。
- 解像度: 360x360〜5184x2160、縦横比 1:1〜2.4:1（検索結果に出た公式文書の要約。1920x1080 は範囲内）。
- 形式: FFmpeg が読める形式。YouTube 等のホスティング URL やクラウドの共有リンクは不可。
- 出典: <https://docs.twelvelabs.io/v1.3/docs/guides/analyze-videos-and-images/videos>（2026-10-10 確認）

### 3.4 機能
- **音声も理解する**（映像・音声・画面上の文字を合わせて解析）。公式の製品ページは「OCR モデルを後付けせず画面の文字を読む」と主張。
- **区間指定**: `start_time` / `end_time`（同期解析は最短4秒）。
- **タイムスタンプ付き出力**: セグメント定義を与えると、重ならない区間ごとに start/end（float 秒）と任意のフィールドを JSON で返す。`response_format` で JSON スキーマを強制できる。
- **API の形**: 1時間未満は同期（ストリーミング NDJSON または一括）、1時間超は非同期タスク（作成→ポーリング）。入力は URL / アセット / base64。旧来の「アップロード→インデックス→生成」は 1.5 以降は不要。
- **Python SDK**: `pip install twelvelabs`。主なメソッドは `assets.create()` / `analyze()` / `analyze_stream()` / `analyze_async.tasks.create()`（<https://github.com/twelvelabs-io/twelvelabs-python>）。
- **待ち時間**: 公式の数値は **未確認**（1.2 の発表では「15分までは TTFT が一定」とだけある）。

### 3.5 日本語
- 公式の製品ページは「12言語」としか書いておらず、言語一覧のページは 404 で確認できなかった → **未確認**。
- 検索エンジンの要約（公式文書由来とみられる）では「英語は full、日本語を含む 11 言語は partial」。**日本語の音声・字幕の読み取り精度は実測して判断すべき**（推測）。

### 3.6 Amazon Bedrock 経由
- **Pegasus 1.5 は 2026-10-09 に Bedrock で提供開始**（US / Global のクロスリージョン推論プロファイルのみ。モデル ID `us.twelvelabs.pegasus-1-5-v1:0` / `global.twelvelabs.pegasus-1-5-v1:0`）。
- 料金: 入力 **$0.00049 / 動画秒**（＝$1.764/時。直接 API とほぼ同額）、出力 $0.0075 / 1000 トークン、最大出力 98,304。
  - 出典: LiteLLM のモデル価格表の更新 <https://github.com/BerriAI/litellm/pull/45477> と <https://futureagi.com/llm-cost-calculator/bedrock/us-twelvelabs-pegasus-1-2-v1-0/>（**二次情報**。AWS の公式料金ページは JS 描画で本文を取得できず未確認）。
- Bedrock の公式文書で確認できたのは **1.2 の仕様**だけ: 1時間・2GB 未満、base64 は 25MB まで、`inputPrompt` 最大2000トークン、`maxOutputTokens` 最大4096、`responseFormat.jsonSchema` 対応（<https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-pegasus.html>）。1.5 の区間指定が Bedrock で使えるかは **未確認**。
- 提供告知: <https://mwpro.co.uk/blog/2026/10/09/twelvelabs-pegasus-1-5-model-now-available-on-amazon-bedrock/>（二次情報）

### 3.7 データの扱い
- 利用規約: 顧客データを「サービスの提供・改善（学習・再学習・ファインチューニングを含む）」に使う。**アカウント設定でオプトアウトしない限り学習に使われる**。
- 保存期間の明記は規約に無い（DPA では契約終了時に削除、バックアップは保持スケジュールに従う）。
- 出典: <https://www.twelvelabs.io/legal/terms-of-use> ・ <https://www.twelvelabs.io/legal/dpa>（2026-10-10 確認）
- Bedrock 経由なら AWS 側のデータ方針になる（Bedrock は入出力をモデル提供者と共有しない、が AWS の一般的な説明だが、本調査では Pegasus について個別に **未確認**）。

---

## 4. Google Gemini（詳細）

### 4.1 モデルと料金（有料枠、100万トークンあたり）
| モデル | 入力 | 出力 | 備考 |
|---|---|---|---|
| Gemini 3.8 Flash（`gemini-3.8-flash`） | $0.75（2026-12-31まで）→ $1.50 | $3.75 → $7.50 | 音声の別料金の記載なし。Batch は半額 |
| Gemini 3.6 Flash | 同上 | 同上 | |
| Gemini 3.1 Pro Preview（`gemini-3.1-pro-preview`） | $2.00（≤20万）/ $4.00（>20万） | $12 / $18 | 無料枠なし |
| Gemini 3.5 Flash-Lite | $0.30（文字・画像・動画・音声とも） | $2.50 | |
| Gemini 2.5 Pro | $1.25 / $2.50 | $10 / $15 | |
| Gemini 2.5 Flash | $0.30（音声は $1.00） | $2.50 | |

- 無料枠は「入力が製品改善に使われる」、有料枠は「使われない」。
- 出典: <https://ai.google.dev/gemini-api/docs/pricing>（2026-10-10 確認）

### 4.2 動画入力
- 既定は **1秒1フレーム**。`fps` で変更可（0.5 なら2秒に1枚、上げることも可）。
- トークン: Gemini 3 系は1フレーム **70 トークン**（low/medium/既定とも同じ）、`high` で 280。音声は約 32 トークン/秒。→ 既定で約 **100 トークン/秒**、高解像度で約 300 トークン/秒。
- 最大長: 100万トークンの文脈で既定解像度なら約3時間、高解像度なら約1時間。
- 区間指定: `videoMetadata` の `start_offset` / `end_offset`。
- 音声込み（動画の音声トラックをそのまま理解する）。
- タイムスタンプ: プロンプトで MM:SS 形式で参照でき、出力にも時刻を付けさせられる。
- Files API: 1ファイル 2GB、プロジェクト合計 20GB、**48時間で自動削除**、無料（動画のページには「有料 20GB」とも書かれており、ページ間で食い違う → 上限は実測で確認）。インラインは 100MB 未満推奨。
- 出典: <https://ai.google.dev/gemini-api/docs/video-understanding> ・ <https://ai.google.dev/gemini-api/docs/media-resolution> ・ <https://ai.google.dev/gemini-api/docs/files>（2026-10-10 確認）

### 4.3 概算（出力 3,000 トークン仮定。思考トークンは別に増える）
| 条件 | 3.8 Flash | 3.1 Pro Preview |
|---|---|---|
| 20分全編・既定（1fps・70tok/コマ） 約12万tok | $0.09 + $0.011 ≒ **$0.10（約15円）** | $0.24 + $0.036 ≒ **$0.28（約42円）** |
| 20分全編・高解像度（280tok/コマ） 約37万tok | $0.28 + $0.011 ≒ $0.29（約44円） | >20万で $4/M: $1.50 + $0.054 ≒ $1.55（約230円） |
| 1分区間×10回・既定 各約6,000tok | ≒ $0.16（約24円） | ≒ $0.48（約72円） |
| 1分区間×10回・高解像度＋2fps 各約3.6万tok | ≒ $0.38（約57円） | ≒ $1.2（約180円） |

- 字幕の文字を読ませるなら、区間レビューでは `high` を使う価値がある（推測。1080p のフレームを 70 トークンに縮めると細かい文字は潰れやすい）。

---

## 5. OpenAI

- 公式の画像・ビジョンの手引きと料金表に**動画入力の記載なし**（2026-10-10 確認）。→ フレーム抽出＋音声の文字起こしの組み合わせになる。
- 画像: 1リクエスト最大1,500枚・合計512MB。GPT-5.x 系はパッチ方式（32px 角、`high` で最大 2,500 パッチ、倍率 1.2）。
  - 1920x1080 → 60×34=2,040 パッチ → **約2,448 トークン/枚**。960x540 → 510 パッチ → **約612 トークン/枚**（計算）。
- 料金（100万トークン）: GPT-5.5 $5 / $30、GPT-5.6-terra $2 / $12、GPT-5.4-mini $0.75 / $4.50、GPT-5-mini $0.25 / $2。
- 文字起こし: gpt-4o-mini-transcribe **$0.003/分**、gpt-4o-transcribe・whisper $0.006/分。
- 概算（20分全編、2秒に1枚＝600枚・960x540・文字起こし込み）:
  - GPT-5.4-mini: 約37万tok × $0.75 = $0.28 ＋文字起こし $0.06 ＋出力 $0.014 ≒ **$0.35（約53円）**
  - GPT-5.5: 約 $1.9（約285円）
  - 1分区間×10回（各30枚）: GPT-5.4-mini で約 **$0.30**。
  - 各モデルの文脈長（37万トークンが1回に入るか）は **未確認**。
- 出典: <https://developers.openai.com/api/docs/pricing> ・ <https://developers.openai.com/api/docs/guides/images-vision>（2026-10-10 確認）

## 6. Anthropic Claude

- **動画は直接入力できない**（画像は JPEG/PNG/GIF/WebP。アニメーションは1コマ目だけ）。
- 画像のトークン: 28px 角のパッチ数 `ceil(w/28)×ceil(h/28)`。Claude 4.7 以降は高解像度（長辺 2576px・最大 4,784 トークン）。
  - 1920x1080 → **2,691 トークン/枚**（公式表）。1280x720 → 46×26 = **1,196 トークン/枚**（計算）。
- 1リクエスト最大 600 枚（20万文脈のモデルは100枚）。**21枚以上を送ると1枚あたりの寸法制限が厳しくなる**（2000px 以下に縮めれば安全）。リクエストサイズ 32MB → 多数なら Files API。
- 料金（100万トークン）: Opus 5.5 $4 / $20、Sonnet 5.5 $2 / $10、Haiku 5.5 $0.10 / $0.50（10万tok 以下）・$0.50 / $2.50（10万超）。
- 画像は学習に使わない、と公式 FAQ に明記。
- 概算（20分全編、5秒に1枚＝240枚・1280x720＋文字起こし $0.06）:
  - Sonnet 5.5: 約29万tok × $2 = $0.57 ＋出力 $0.03 ＋文字起こし ≒ **$0.66（約100円）**
  - Haiku 5.5: >10万tok なので $0.50/M → 約 $0.21（約32円）
  - 1分区間×10回（各 2 秒に1枚＝30枚）: Sonnet 5.5 で約 **$1.0**（出力の比重が大きい）。
- **Claude Code 自身が PNG を読める**ので、scriptvedit が「疑わしいコマ」を書き出して Claude Code に Read させれば API 費用ゼロ（サブスクの範囲）で目視レビューができる。全編を流すのには向かないが、決定的な検査で絞ったコマの確認には最適（推測ではなく運用上の事実: Read ツールは画像を表示できる）。
- 出典: <https://platform.claude.com/docs/en/build-with-claude/vision> ・ <https://platform.claude.com/docs/en/about-claude/pricing>（2026-10-10 確認）

## 7. Amazon Nova

- Nova 2 Lite の動画理解: MP4/MOV/MKV/WebM 等、base64 25MB / S3 1GB。**すべての動画を 672x672 の正方形へ歪めて縮小**。16分以下は 1fps、それより長いと 960 枚に固定（20分で 1200 枚＝約 34.6万トークンの表もある）。
- **音声は処理しない**（「映像フレームのみで理解する」と公式に明記）。**多言語の画面文字は理解が限定的**、小さい文字は苦手とも明記。
- 料金は Nova 2 Lite 入力 $0.30 / 出力 $2.50（二次情報: <https://openrouter.ai/amazon/nova-2-lite-v1> ほか）。Nova 2 Pro の動画仕様・料金は **未確認**。
- → 日本語字幕・音声のレビューには**不適**。
- 出典: <https://docs.aws.amazon.com/nova/latest/nova2-userguide/using-multimodal-models.html>（2026-10-10 確認）

## 8. オープンモデル（ローカル、RTX 4060 8GB）

- **Qwen3-VL-8B / 4B**: 公式の GGUF（4bit/8bit/16bit）があり、8B の 4bit なら 8GB VRAM に載る見込み（推測。動画は多数のフレームでトークンが膨らむので、実用は短い区間・少ない枚数）。テキストのタイムスタンプ整列（Text–Timestamp Alignment）を持つ。
  - <https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF> ・ 技術報告 <https://arxiv.org/abs/2511.21631>
  - llama.cpp 系は動画を直接受けず、フレーム画像として渡す形になる（推測。未検証）。vLLM は Windows 非対応のため WSL2 が要るが、このPCは WSL2 を使えない（CLAUDE.md の VirtualBox の事情）。
- **音声込み**のオープンモデル（Qwen3-Omni-30B-A3B、Qwen3.5-Omni 2026-03-30 公開）は 30B MoE で、8GB には載らない（推測。公開情報ではフル精度で 80GB 級）。Qwen3.5-Omni の重みが公開されているかは **未確認**。
  - <https://github.com/QwenLM/Qwen3-Omni> ・ <https://qwen.ai/blog?id=qwen3.5-omni>
- Video-LLaMA / LLaVA-Video 等の旧世代は、下の Video-MME-v2 で Qwen3-VL よりさらに下。今回の用途には勧めない。

---

## 9. 性能（公開ベンチマーク）

### 9.1 Video-MME-v2（2026-04、グループ単位の非線形スコア。人間の専門家 90.7）
| モデル | 字幕/音声あり | 映像のみ |
|---|---|---|
| Gemini-3-Pro | **49.4** | 38.2 |
| Seed-2.0-Pro | 43.3 | 35.2 |
| Gemini-3-Flash | 42.5 | 32.9 |
| Qwen3.5-397B-Think（512枚） | 39.1 | 30.3 |
| GPT-5 | 37.0 | 26.4 |
| Kimi-K2.5 | 36.1 | 27.3 |
| Qwen3-VL-235B | 25.0 | 16.5 |
| Qwen3-VL-8B | 18.2（二次情報の要約） | — |
| Qwen3-Omni-30B | 17.1 | 10.9 |

- 音声をネイティブに扱えるモデルは音声ありで大きく伸びる（Gemini で +11.2）。フレーム数を増やすと伸びる（64→512枚で +8.5）。
- Pegasus と Claude は表に無い（**未確認**）。
- 出典: <https://arxiv.org/abs/2604.05015>（2026-10-10 確認）
- 旧 Video-MME（字幕あり）では Gemini 3.1 Pro 88.4 との集計もある（<https://benchlm.ai/benchmarks/videommewithsub>、二次情報）。LongVideoBench・MLVU・EgoSchema の 2026 年時点の横並びの値は、本調査では公式の表を確認できず **未確認**。

### 9.2 Pegasus の自社評価
- Pegasus 1.2: VideoMME-Long（30分超の部分集合）で「当時の全フラッグシップ API を上回る」と主張するが、**数値は公開ページに無い**（<https://www.twelvelabs.io/blog/introducing-pegasus-1-2>）。
- Pegasus 1.5: 時間区間の分割で 0.4279（Gemini 3.1 Pro 0.3370）、画像つき指示で 0.4555（同 0.3243）。**自社の評価セットと指標**（Temporal F1 / Segment F1、LLM 判定）であり第三者検証ではない（<https://www.twelvelabs.io/blog/introducing-pegasus-1-5>）。

### 9.3 画面の文字を読む力
- MME-VideoOCR（NeurIPS 2025）: 最上位の Gemini 2.5 Pro でも **73.7%**。1〜数フレームに出る文字は読めるが、フレームをまたぐ統合・時空間推論・言語の先入観に弱い。**高解像度入力と十分な時間方向の密度が重要**と結論（<https://arxiv.org/abs/2505.21333>）。
- 日本語の動画 OCR に特化した公開ベンチは見つからなかった（**未確認**）。

### 9.4 音声と映像の同期
- DAVE（2025）: 音声と映像の対応づけで Gemini 2.0 Flash 59.86%、1.5 Pro 54.98% 程度（<https://arxiv.org/abs/2503.09321>）。
- AV-SyncBench（2026-07）は専用の特徴抽出モデルの評価で、50〜500ms のずれの検出が 50〜66%（ほぼ偶然〜やや上）（<https://arxiv.org/abs/2607.00726>）。
- → **数百 ms 以下の「口と声のずれ」「字幕と声のずれ」を動画 LLM に任せるのは不確実**（推測を含む。LLM に対する同種の数値は未確認）。字幕と声のずれは、scriptvedit 側で「字幕の表示区間」と「音声の文字起こしの単語時刻（Whisper 等）」を突き合わせる方が確実。

### 9.5 限界（今回の用途に効くもの）
- **サンプリング**: Gemini・Nova は既定 1fps、OpenAI/Claude は自分で抜いた枚数だけ。30fps の動画で**1〜数フレームだけ出る不具合（字幕のはみ出し・重なり・黒コマ）は、サンプリングの谷に落ちれば絶対に見えない**。fps を上げると費用が線形に増える。
- **解像度**: Gemini の既定 70 トークン/コマ、Nova の 672x672 正方形化では、1080p の字幕の細部は潰れる。
- **タイムスタンプの精度**: 1fps サンプリングなら原理的に±1秒程度（推測）。コマ単位の位置特定はできない。
- **幻覚**: 「誤字がある」「重なっている」と言っても実在しないことがある。指摘はそのまま修正せず、該当時刻のコマを書き出して確認する手順が要る（推測。一般的な性質）。

---

## 10. scriptvedit への組み込みの観点

### 10.1 観点別の有利不利
| 観点 | 有利 | 備考 |
|---|---|---|
| 区間レビュー | Gemini（`start_offset`/`end_offset`）、Pegasus（`start_time`/`end_time`） | 他は ffmpeg で切り出せば同等 |
| タイムスタンプ付きの指摘 | Pegasus（区間＋JSON スキーマが本業）＞ Gemini（MM:SS＋JSON 出力） | どちらも構造化出力を使う |
| 音声込み | Gemini、Pegasus | OpenAI/Claude は文字起こし併用、Nova・Qwen3-VL は不可 |
| 日本語 | Gemini（推測ではなく広く使われているが数値は未確認）＞ Pegasus（未確認／二次情報で partial） | Nova は公式に「限定的」 |
| 費用 | Gemini 3.8 Flash ≪ Gemini 3.1 Pro ≒ GPT-5.4-mini ＜ Pegasus ≒ Claude Sonnet | ローカルは0円だが品質が低い |
| API の手軽さ | Gemini（Files API にアップして1回呼ぶだけ）≒ Pegasus（インデックス不要になった） | OpenAI/Claude はフレーム抽出・文字起こしの前処理が要る |
| 細部（1フレームの不具合） | **どれも不得手** → scriptvedit 側の決定的検査＋Claude で該当コマを見る | |

### 10.2 安く回す工夫と費用の目安（20分＝全編1回 / 1分×10回）
| 工夫 | 効果 | Gemini 3.8 Flash | Pegasus |
|---|---|---|---|
| そのまま（1080p、既定） | 基準 | $0.10 / $0.16 | $0.61 / $0.52 |
| 縮小（720p 等）して送る | 転送・アップロードが軽くなる。**Gemini・Pegasus の課金は秒数基準なので料金はほぼ変わらない**（Gemini は解像度でなく `media_resolution` で決まる） | 同左 | 同左 |
| fps を下げる（0.5fps） | Gemini は映像トークンが半減（音声分は残る） | 約 $0.07 / $0.13 | 料金は変わらない（秒課金） |
| 区間だけ送る | 変更した場面だけ再レビュー。再レンダ後の確認はこれが最も効く | 1分×1回で約 $0.016 | 1分×1回で約 $0.05 |
| 音声を別に文字起こし（Whisper ローカル or $0.003/分） | 台本との照合（誤読・読み飛ばし）を LLM の外で厳密に。映像だけを LLM に渡せば Gemini 2.5 系は音声単価（$1.00）を避けられる | — | — |
| Batch API（Gemini は半額） | 待ってよいなら半額 | $0.05 / $0.08 | なし（未確認） |
| `high` 解像度は区間レビューだけ | 字幕の可読性チェックの精度を上げる（推測） | 1分×10回で約 $0.3〜0.4 | — |

（左が 20分全編1回、右が 1分×10回。出力 3,000 トークン仮定・1ドル150円換算で、Gemini 3.8 Flash の 20分全編は約15円、Pegasus は約90円。）

### 10.3 推奨する構成（推測を含む設計案）
1. **決定的な検査（コードで、0円・全フレーム）**: scriptvedit はどの字幕がいつ・どこに・何の文字で出るかを知っている。`p.audit()` の延長で、字幕の描画矩形の画面外はみ出し・字幕同士の重なり・最小文字サイズ・ラウドネス（ffmpeg の ebur128）・声と BGM の比を検査し、問題の時刻を出す。
2. **文字起こし照合**: 書き出した音声を Whisper（ローカル）か gpt-4o-mini-transcribe で文字起こしし、台本・字幕テキストと差分を取る（誤読・字幕と声のずれ）。
3. **動画 LLM による主観レビュー**: Gemini 3.8 Flash で全編（テンポ・図の見づらさ・BGM と声の釣り合い・事実の誤りの疑い）。JSON スキーマで `{time_start, time_end, category, severity, message, suggestion}` を返させる。最終版だけ Gemini 3.1 Pro で再確認。
4. **指摘の検証**: 1〜3 の指摘時刻のコマ（または前後数コマ）を PNG で書き出し、Claude Code が Read して実在を確かめてから修正する（幻覚の指摘で直さないため）。
5. **再レビューは区間だけ**: 直したレイヤーの時間範囲だけを `start_offset`/`end_offset` で再送する。

Pegasus を第1候補にしない理由は、(a) 料金が Gemini Flash の約6倍、(b) 日本語の対応度が公式に確認できない、(c) 既定で学習利用（要オプトアウト）。一方で「区間×構造化メタデータ」の形は最も素直なので、**Gemini の指摘の時刻精度に不満が出たら、同じ JSON スキーマで Pegasus に差し替えて比較する**のがよい（抽象化層を1つ挟めば切り替えは容易）。

---

## 11. 未確認事項の一覧
- Pegasus の日本語の対応度（公式の言語一覧ページが 404）。
- Pegasus 1.5 の Bedrock での区間指定・入力上限・AWS 公式料金ページの数値（二次情報のみ）。
- TwelveLabs の無料枠が Analyze（インデックスなし）に使えるか。Pegasus の処理待ち時間の数値。
- Gemini Files API の上限（2GB と 20GB の記述がページ間で食い違う）。Gemini の日本語動画での定量評価。
- OpenAI の各モデルの文脈長、動画入力の有無（公式文書に記載が見当たらない＝無いと判断したが、明示的な「非対応」の文言は未確認）。
- Amazon Nova 2 Pro の動画仕様と料金。
- LongVideoBench / MLVU / EgoSchema の 2026 年時点の最新モデルの横並びの値。Pegasus・Claude の公開ベンチの値。
- Qwen3.5-Omni の重みの公開有無、Qwen3-VL の 8GB での動画の実用枚数（実測が要る）。

---

## 付録: 無料枠・初回クレジット（2026-10-11 調査）

本文（2026-10-10）の第1候補 Gemini・第2候補 Pegasus を含め、**お金を掛けずに動画レビューを回す**ための無料枠・無料ツール・新規クレジットを公式ページで確かめた。

- 調査日: **2026-10-11**（出典の確認日はすべてこの日。本文から引き継いだ事実は「2026-10-10 確認」と明記）
- 為替は 1ドル＝約150円。
- 「二次情報」と書いたものは公式ページで直接確認できず、検索エンジンの要約・ブログ・フォーラムに拠る。確認できなかったものは「未確認」。
- 試算の前提は本文と同じ（20分の動画、出力 3,000 トークン、Gemini は既定解像度・1fps）。

### A.1 結論（費用をかけずに始めるならこの順）

| 順 | 手段 | 無料で何ができるか | 制約・注意 |
|---|---|---|---|
| **1** | **Gemini API 無料枠（AI Studio の API キー）** | 動画ファイルを Files API に上げて（無料・2GB/ファイル・48時間保存）Gemini 3.8 Flash / Flash-Lite に音声込みで見せられる。20分全編は約12万トークンで1リクエストに収まる | カード不要。**無料枠では入力・出力が「製品改善」に使われ、人間のレビュアーが読みうる**（規約に明記）。1日のリクエスト上限は公式表が無く、フォーラムの実測で **3.8 Flash が 20 RPD・Flash-Lite が 500 RPD**（二次情報）。「20分×1回/日」は余裕、「1分×10回/日」は Flash だと上限の半分を使う |
| **2** | **TwelveLabs Free プラン（Pegasus）** | カード不要。料金表で **Analyze API（Pegasus）の入力動画・出力トークンとも Free プランは「Free」**。レート制限は Analyze が 60 RPM / 1,000 RPD / 動画 600分/時・3,000分/日。本文の「無料枠が Analyze に効くか」は**料金表の記載で肯定的に解消**（ただし「600分」の消費対象は未確認） | **既定で学習利用（オプトアウト要。本文 §3.7）**。日本語対応は依然未確認。インデックスは作成から90日 |
| **3** | **Claude Max の月次 API クレジット**（Max 5x: $100/月、20x: $200/月）＋ Claude Code | 既に Max 契約者なら、フレーム画像＋文字起こしで Claude API（Sonnet 5.5 で20分約 $0.66）を月150回分、追加費用なしで回せる。Claude Code 自身は PNG を読めるので「疑わしいコマの確認」は今のサブスクで0円 | **Pro は対象外**。クレジットは Claude Code には使えない。動画は API でも Claude Code でも直接入力不可（フレーム抽出＋Whisper 等が要る） |
| 4 | **Google Cloud 新規 $300 クレジット（90日）→ Vertex AI の Gemini** | 無料枠の「学習利用」を避けたい場合の本命。Vertex AI は「許可なく学習に使わない」。$300 なら 3.8 Flash の20分レビューが千回規模 | **カード登録が必要**（与信の保留のみ、課金なし）。**クレジットは「AI Studio の Gemini API」には使えない**（公式に除外明記。2026-03-02 以降の新規アカウント、二次情報）。Vertex の 3.8 Flash は API より高い（$1.35/M 入力、二次情報） |
| 5 | AWS 新規クレジット（$100＋最大$100、6か月） | Bedrock は Free プランで使え、Bedrock の活動で +$20 | **Pegasus（TwelveLabs）は AWS Marketplace 経由の第三者モデル**で、プロモーションクレジット規約は Marketplace を除外 → **クレジットで Pegasus を回せるかは未確認（否定的）**。Marketplace には有効な支払方法が要る。Nova はクレジット対象だが音声を見ない（本文 §7） |
| 6 | ローカル（Qwen3-VL / Qwen3.5-4B・9B） | 0円・学習利用なし。Transformers / vLLM 経由なら動画をネイティブ入力できる | **llama.cpp / Ollama / LM Studio は動画入力に非対応**（画像のみ）。RTX 4060 8GB では 4B 級を 4bit で、短い区間のみ（推測）。**音声は見ない**（Omni は 30B 級で載らない）。品質は本文 §9 のとおり API 勢に大きく劣る |
| — | OpenAI | API は動画入力なし・新規無料クレジットなし（二次情報）。ChatGPT（Free 含む）は動画を添付できるが「全編を解析しない・音声を正確に解釈しないことがある」と公式 FAQ が明記 | スクリプトから回す用途には不向き |

**おすすめの進め方**: (1) Gemini API 無料枠で実装と評価を始める（公開前の動画を送るのが嫌なら、公開済み・問題ない動画で評価する）→ (2) 本番の未公開動画は Google Cloud の $300 クレジットで Vertex AI の同じモデルへ切り替える（学習利用なし）か、有料枠（Tier 1、20分約15円）に上げる → (3) Pegasus は TwelveLabs の Free プランで並行して試し、時刻精度を比べる（オプトアウト設定を先に入れる）→ (4) 指摘の確認は Claude Code の画像読み込み（0円）。

### A.2 比較表（サービス × 無料枠）

| サービス | 無料枠・初回クレジット | 動画ファイル入力 | 音声 | 学習・製品改善への利用 | カード登録 | 20分×1回 | 1分×10回/日 | 無料枠を超えた月額の目安 |
|---|---|---|---|---|---|---|---|---|
| **Gemini API（AI Studio）無料枠** | 3.8 Flash / 3.8・3.5・3.1 Flash-Lite が「Free of charge」。**3.1 Pro は無料枠なし**。RPM/TPM/RPD は公式表が無く AI Studio の画面で確認（フォーラム実測: 3.8 Flash 20 RPD、Flash-Lite 500 RPD。二次情報） | **可**（Files API 無料。2GB/ファイル、48時間、プロジェクト 20GB） | **あり** | **使われる**（人間のレビュー含む）。有料（Tier 1 以上）にすると使われない | 不要（有料化には必要） | **収まる**（約12万tok、1リクエスト） | **収まる**（20 RPD なら半分を消費。Flash-Lite なら余裕） | 有料枠: 20分1回 約15円、毎日1回で月約450円（3.8 Flash） |
| **Gemini CLI** | **個人 Google アカウントの無料ログインは 2026-06-18 で終了**（Antigravity CLI へ移行）。API キーなら上の無料枠と同じ | 可（`read_file` は動画 MIME を base64 で渡す。**20MB 上限**） | あり | API キーの無料枠と同じ | 不要 | 20分 1080p の mp4 は 20MB を超えるので縮小・分割が要る（推測）。Files API を直接叩く方が素直 | 同上 | 同上 |
| **Antigravity CLI**（Gemini CLI の後継） | 個人向け Free ティアあり（週ごとに更新される枠。数値は非公開。モデルは Gemini 3.8 Flash / 3.1 Pro 等、二次情報） | **未確認** | — | **使われる**（規約: 「評価・開発・改善」に使い、従業員が閲覧しうる。設定でオプトアウト可とあるが、設定項目が無いというフォーラム報告あり。二次情報） | 不要 | 未確認 | 未確認 | Google AI Pro $19.99/月（二次情報） |
| **Google Cloud 新規 $300（Vertex AI）** | $300・90日。新規かつ Google Cloud / Maps / Firebase の有料利用歴なし。**AI Studio の Gemini API と「モデル as a サービス」のパートナーモデルは除外** | 可（Vertex の Gemini は同じ動画入力。GCS 経由） | あり | **使わない**（許可なく学習・微調整しない。プロンプトキャッシュは既定24時間、無効化可） | **必要**（保留のみ） | 収まる（約 $0.2。二次情報の単価で） | 収まる | 3.8 Flash $1.35/M 入力（二次情報）: 毎日1回で月約 $6（約900円） |
| Vertex AI Express モード | 新規 Google Cloud・@gmail.com なら **請求情報なしで90日**、限定クォータ（二次情報。公式ページは JS 描画で未確認） | 未確認 | — | Vertex と同じ（推測） | 不要 | 未確認 | 未確認 | — |
| **AWS 新規クレジット** | $100（登録時、6か月で失効）＋活動で最大 $100（EC2 / RDS / Lambda / **Bedrock** / Budgets 各 $20、12か月で失効）。Free プランは6か月かクレジット枯渇で閉鎖。カードは「ほとんどの新規顧客で不要」 | Bedrock の Pegasus 1.5 は可（本文 §3.6） | あり | Bedrock は AWS の方針（Pegasus 個別には未確認） | 不要（**Marketplace の第三者モデルには有効な支払方法が必要**） | **未確認**（Pegasus は Marketplace 経由、プロモクレジットは Marketplace 除外。Activate クレジットだけ Bedrock の第三者モデルの例外あり＝二次情報） | 同左 | Pegasus: 20分 約90円、毎日1回で月約2,700円 |
| **TwelveLabs Free プラン** | カード不要。**Analyze API（Pegasus）の入力動画・出力ともに「Free」**。アップロード 600分（累積、削除しても戻らない）。Analyze のレート: 60 RPM / 1,000 RPD / 600分/時 / 3,000分/日 / 出力 30k TPM・500k TPD。インデックスは90日 | **可**（URL / アセット / base64） | あり | **既定で使われる**（アカウント設定でオプトアウト） | 不要 | **収まる**（600分の累積に数えるなら30回分） | **収まる**（10分/日） | Developer: $1.75/時 → 20分毎日で月約2,700円 |
| **Anthropic Claude** | **Max 5x $100/月・Max 20x $200/月・Team 最大 $500/月の API クレジット**（Pro・Free・Enterprise は対象外。契約7日後から。繰り越しなし。Claude Code には使えない）。新規 Console の $5 は**公式ページに記載なし**（二次情報のみ） | **不可**（画像のみ。Claude Code の Read も画像のみ） | 不可（別途文字起こし） | 使わない（本文 §6） | Max クレジットなら不要 | Max 内で収まる（Sonnet 5.5 約 $0.66 ×30日 ≒ $20） | Max 内で収まる（約 $1/日 ≒ $30/月） | Pro のみなら API 従量: 20分毎日で月約3,000円（Sonnet） |
| **OpenAI** | 新規の無料クレジットは**無し**（二次情報）。API は動画入力に非対応（公式文書に無し、SDK の issue で要望中） | API: 不可（フレーム抽出）。**ChatGPT: Free 含め添付可**だが「全編を解析しない・音声を正確に解釈しないことがある」と公式 FAQ | ChatGPT は不確実 | API は規約どおり（本調査では未確認） | API は要 | 不可（API） | 不可 | GPT-5.4-mini 約53円/回 → 月約1,600円 |
| **ローカル Qwen3-VL 8B/4B・Qwen3.5-4B/9B** | 0円（Apache 2.0） | **可**（Transformers の `qwen-vl-utils` / vLLM の `video_url`。**llama.cpp・Ollama は動画非対応**） | **なし**（Omni は 30B 級） | なし | 不要 | 8GB では区間ごと・低解像度（推測） | 可（遅い） | 電気代 |
| Hugging Face | Inference Providers: **Free ユーザーは月次クレジットなし**（PRO $9/月で $2）。ZeroGPU Spaces: 無料アカウント **GPU 5分/日**（48GB） | Spaces のデモ次第 | — | Space 次第 | 不要 | 5分/日では厳しい | 不可 | PRO $9/月 |
| OpenRouter 無料モデル | `:free` は 20 RPM / **50 RPD**（$10 以上購入歴で 1,000 RPD）。NVIDIA Nemotron 3 Nano Omni (free) が text/image/video/audio 入力（二次情報） | 可（二次情報。長さ上限未確認） | あり（二次） | 無料モデルはログ・学習に使われうる（未確認） | 不要 | 未確認 | 50 RPD 内 | — |
| Groq / Together / Mistral / Meta | Groq 無料枠は画像のみ（`qwen/qwen3.8-27b`、画像3枚まで）。Together は無料枠なし（$5 前払い。二次情報）。Mistral・Llama 4 に動画入力モデルは見つからず（Llama 4 はフレーム画像。二次情報） | 不可 | — | — | — | 不可 | 不可 | — |
| Gemini アプリ（gemini.google.com） | 無料: 動画は**合計5分まで**（1ファイル 2GB）。Google AI Pro / Ultra で合計1時間 | 可（手動） | あり | Gemini アプリの方針（未確認） | 不要 | 無料では不可（5分） | 手動なら可 | AI Pro（二次情報: $19.99/月） |

### A.3 各サービスの詳細

#### A.3.1 Gemini API（Google AI Studio）の無料枠

- **無料のモデル**: 料金表で「Free of charge」が付くのは Gemini 3.8 Flash、3.8 / 3.5 / 3.1 Flash-Lite 等。**3.1 Pro Preview は無料枠なし**（2026-10-10 確認）。
- **レート制限**: 公式のレート制限ページは **無料枠のモデル別 RPM / TPM / RPD の表を載せていない**。「レート制限は使用量ティア等で異なり、Google AI Studio で確認できる」とだけ書き、`aistudio.google.com/rate-limit` へ誘導する（日本語版も同じ）。ティアは Free / Tier 1（課金設定）/ Tier 2（$100 以上＋3日）/ Tier 3（$1,000 以上＋30日）。
  - 公式フォーラムの投稿（2026-09-03）では **Gemini 3.8 Flash の無料枠が 20 RPD、Flash-Lite が 500 RPD**（投稿者の実測。Google 社員の回答なし）。別の二次情報では「Gemini 3 Flash 10 RPM / 250k TPM / 1,500 RPD」「Flash-Lite 15 RPM / 1,000 RPD」等、記事ごとに食い違う。**実際の値は API キーを作って AI Studio の画面で見るのが唯一確実**。
- **Files API**: 「Gemini API が使える全リージョンで無料」。1ファイル 2GB、プロジェクト合計 20GB、**48時間で自動削除**。動画のページには「20GB（有料）/ 2GB（無料）」とあり（本文 §4.2 の食い違いは解消せず）。無料枠では YouTube URL の入力が1日8時間まで。
- **動画のトークン**（2026-10-11 の動画理解ページ）: 1fps サンプリング、**`media_resolution=low` で 66 トークン/コマ、それ以外は 258 トークン/コマ、音声 32 トークン/秒** → 既定（low）で**約 100 トークン/秒、高解像度で約 300 トークン/秒**。1M 文脈で low なら最長約3時間、high なら約1時間。本文 §4.2 の 70 / 280（media-resolution ページ、2026-10-10）とページ間で数値が違うが、概算（20分 ≒ 12万トークン）は変わらない。
  - **20分×1回**: 約12万トークンの1リクエスト。TPM が 25万（二次情報）なら1回で収まる。
  - **1分×10回/日**: 各約6,000トークン × 10 リクエスト。3.8 Flash が 20 RPD なら半分を使う。Flash-Lite（500 RPD）なら余裕だが、品質は未測定。
- **データの扱い**: 利用規約の「Unpaid Services」は「送信したコンテンツと生成された応答を、Google の製品・サービスの提供・改善・開発に使う」「人間のレビュアーが API の入出力を読み・注釈し・処理することがある」と明記。「Paid Services」は「プロンプトや応答を製品改善に使わない」。料金表も無料枠「Content used to improve our products」／有料「not used」。**課金設定を入れた時点（Tier 1）で有料扱い**になる。
- **日本**: 利用可能リージョン一覧に Japan あり。無料枠が使えないのは EEA・スイス・英国（Paid Services のみ）。
- 出典: <https://ai.google.dev/gemini-api/docs/pricing> ・ <https://ai.google.dev/gemini-api/docs/rate-limits> ・ <https://ai.google.dev/gemini-api/docs/files> ・ <https://ai.google.dev/gemini-api/docs/video-understanding> ・ <https://ai.google.dev/gemini-api/terms> ・ <https://ai.google.dev/gemini-api/docs/available-regions>（いずれも 2026-10-11 確認）。フォーラム: <https://discuss.ai.google.dev/t/gemini-3-8-flash-free-tier-20-rpd-is-too-limited-for-practical-evaluation/180609>（二次情報、2026-10-11 確認）。

#### A.3.2 Gemini CLI と Antigravity CLI

- **個人 Google アカウントでの無料利用は終了**: 公式告知（2026-05-19）で「2026-06-18 以降、Gemini CLI は Google AI Pro / Ultra と無料利用のリクエストを受け付けない」。Gemini Code Assist の個人向けも同日に停止。残るのは **Gemini API キー（無料枠を含む）**、Vertex AI、Code Assist Standard / Enterprise。クォータのページ冒頭にも「Unpaid tier and Google One users: Gemini CLI was replaced by Antigravity CLI on June 18th, 2026」。
  - 同ページの「API キー（無料）: 250 リクエスト/日・Flash のみ」は、A.3.1 のフォーラム実測（20 RPD）と食い違う（ページの更新が遅れている可能性。推測）。
- **動画ファイル**: ドキュメント上 `read_file` は「text, images, audio, PDF」、`@` は「テキスト向け」としか書かれていないが、**ソースでは `detectFileType` が `video/*` を `'video'` と判定し、画像・PDF と同じく base64 の `inlineData` でモデルへ渡す**。ただし `MAX_FILE_SIZE_MB = 20` の上限がある → 20分の 1080p mp4 は入らない（縮小・分割が要る。推測）。
- **非対話**: `-p` / `--prompt` か非 TTY でヘッドレス。`--output-format json` / `stream-json`。終了コード 0 / 1 / 42 / 53。
- **Antigravity CLI**（後継。Go 製・クローズドソース）: 個人向け Free ティアは「週ごとに更新される枠」（数値非公開）、Google AI Pro は「5時間ごとに更新、週の上限あり」で Gemini 3.1 Pro / 3.8 Flash 等。ヘッドレスモードの項目はある。**動画を読ませられるかは未確認**。規約（個人向け）: 「Interactions（プロンプト・コード等）を Google の研究・製品・ML 技術の評価・開発・改善に使う。従業員・委託先が閲覧しうる。使われたくなければ設定で変更」。フォーラムでは「その設定項目が無い」という報告（二次情報）。Workspace / Google Cloud 経由は対象外。
- 出典: <https://github.com/google-gemini/gemini-cli/discussions/27274> ・ <https://geminicli.com/docs/resources/quota-and-pricing/> ・ <https://geminicli.com/docs/resources/tos-privacy/> ・ <https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md> ・ <https://raw.githubusercontent.com/google-gemini/gemini-cli/main/packages/core/src/utils/fileUtils.ts> ・ 同 `constants.ts` ・ <https://antigravity.google/docs/plans> ・ <https://antigravity.google/terms> ・ <https://developers.google.com/gemini-code-assist/docs/deprecations/code-assist-individuals>（いずれも 2026-10-11 確認）。モデル一覧・価格は <https://www.cloudzero.com/blog/google-antigravity-pricing/>（二次情報）。

#### A.3.3 Google Cloud（Vertex AI）

- **無料トライアル**: 「$300 の Welcome クレジットを 90 日で」。条件は Google Cloud / Google Maps Platform / Firebase の有料利用歴がなく、トライアル未経験。**クレジットカード等の支払方法が必須**（「承認リクエストは保留であり実際の課金ではない。Google は資金を引き落とさない」）。90日経過かクレジット消費で請求先アカウントが閉じ、リソースは停止（30日の猶予後に削除）。
- **除外**（公式に明記）: 「**Gemini API in AI Studio costs**」と「マネージド API として提供される生成 AI のパートナーモデル（model as a service）」。→ AI Studio の API キーで使った分は $300 から引けない。**Vertex AI の Gemini は除外に挙がっていない**（検索要約では「2026-03-02 以降の新規アカウントに適用」「Vertex 経由なら使える」。二次情報）。
- **Vertex の料金**: 公式料金ページは JS 描画で本文を取得できず **未確認**。検索要約（公式ページ由来とされる）では Gemini 3.8 Flash が入力（テキスト・画像・動画・音声）**$1.35/M（Global）、$1.485/M（非 Global）**、2027-01-01 から倍（二次情報）。API の $0.75/M より高い。
- **データ**: 「お客様の事前の許可・指示なく、データを AI/ML モデルの学習・微調整に使わない」。プロンプトキャッシュは既定で24時間（プロジェクト単位で無効化可）、リクエスト・レスポンスのログは既定で無効。
- **Express モード**: 新規 Google Cloud ユーザー（@gmail.com）が**請求情報なしで90日**、限定クォータで Vertex AI を試せる仕組み（検索要約。公式の概要ページは取得できず未確認）。
- 出典: <https://docs.cloud.google.com/free/docs/free-cloud-features> ・ <https://docs.cloud.google.com/vertex-ai/generative-ai/docs/data-governance>（2026-10-11 確認）。料金: <https://docs.cloud.google.com/vertex-ai/generative-ai/pricing>（未確認・検索要約）。Express: <https://cloud.google.com/resources/cloud-express-faqs>（未確認・検索要約）。

#### A.3.4 AWS

- **新規クレジット（2025-07-15 以降）**: 「登録時に $100、主要サービスを試すと最大 $100 追加、6か月で最大 $200」。追加分は **EC2 / RDS / Lambda / Amazon Bedrock / AWS Budgets の5つの活動、各 $20**。Free プランは「6か月後かクレジット枯渇で自動的に閉鎖」、クレジットの失効は登録時分が6か月・活動分が12か月。「ほとんどの新規顧客で支払方法の登録は不要」。Free プランは「一部のサービスを使えない」（一覧は FAQ に無い。Bedrock AgentCore は Paid プラン限定と明記）。Bedrock 自体は活動の対象なので Free プランで使える。
- **Pegasus とクレジット**: Bedrock の第三者モデルは AWS Marketplace 経由で購読される（Marketplace を介さないのは Amazon / DeepSeek / Google / Meta / MiniMax / Mistral / Moonshot / NVIDIA / Qwen / xAI / Z.AI と明記。**TwelveLabs は含まれない＝Marketplace 経由**）。Marketplace の購読には「有効な支払方法」が必要。**AWS プロモーションクレジット規約は「AWS Marketplace」を適用外**に挙げる。Free Tier クレジットがプロモーションクレジットに当たるかは明記なし。Activate クレジットだけ「Bedrock の第三者基盤モデルの Marketplace 料金に充当できる」例外がある（二次情報）。→ **新規クレジットで Pegasus を回せるかは未確認（否定的に見ておく）**。Amazon Nova はクレジットで使えるが音声を処理しない（本文 §7）。
- **Bedrock の常設無料枠は無い**（従量のみ。二次情報）。
- 出典: <https://aws.amazon.com/blogs/aws/aws-free-tier-update-new-customers-can-get-started-and-explore-aws-with-up-to-200-in-credits/> ・ <https://aws.amazon.com/free/free-tier-faqs/> ・ <https://aws.amazon.com/free/> ・ <https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html> ・ <https://aws.amazon.com/awscredits/>（いずれも 2026-10-11 確認）。Activate の例外: <https://aws.amazon.com/activate/terms/>（検索要約・二次情報）。

#### A.3.5 TwelveLabs 直接（Free プラン）

- **Free プラン**（初回ログインで自動付与、**カード不要**）: 動画のアップロード **600 分**（累積。削除しても戻らない）、インデックスは作成から **90 日**、インデックスあたり 100 本、同時インデックス 5。
- **料金表の Free プラン列**: インデックス作成は 600分まで無料、**Analyze API（Pegasus 1.6 / 1.5）の「入力動画」「出力トークン」とも「Free」**（Developer は $1.75/時、$7.5/M）。→ 本文 §3.2 の「無料枠が Analyze に効くか未確認」は**料金表上は「効く」**。ただし「600 分」が Analyze（インデックスなし）の動画秒数を消費するかは FAQ に書かれておらず **未確認**（アセットのアップロードは「Platform へのアップロード」に当たりうる。推測）。モデル版の制限も記載なし。
- **レート制限（Free / Developer Tier 1 共通）**: Analyze **60 RPM / 1,000 RPD / 動画 600 分/時・3,000 分/日 / 出力 30k TPM・500k TPD**。Index 60 RPM / 3,000 RPD / 600 分/時・3,000 分/日。Developer Tier 2（月 $200 以上）で 120 RPM、Tier 3（$400 以上）で 240 RPM。
- 「20分×1回」「1分×10回/日」はどちらもレート制限内。600 分の累積に数えるとしても 20分×30回。
- 学習利用は既定でオン（本文 §3.7）。**使う前にアカウント設定でオプトアウトする**。
- 出典: <https://www.twelvelabs.io/pricing> ・ <https://docs.twelvelabs.io/docs/get-started/rate-limits>（2026-10-11 確認）。

#### A.3.6 Anthropic / Claude

- **Max / Team の月次 API クレジット**（公式ドキュメントあり。二次情報では 2026-10-07 開始）: **Max 5x $100/月、Max 20x $200/月、Team は席ごと $20 / $100 で合計 $500 まで**。対象は Claude API・Managed Agents・Agent SDK・Playground。**Claude Code と Claude アプリの追加利用には使えない**。Free / **Pro** / Enterprise は対象外。契約7日後から Console 組織を1つ紐付けて受け取る。繰り越しなし。支払方法の登録は不要。Bedrock / Vertex / Foundry 上の Claude には使えない。
- **新規 Console の無料クレジット**: 「$5・SMS 認証・14日で失効・地域限定」という情報は複数の二次情報にあるが、**Anthropic の公式ページでは確認できず**（サポート記事の検索でも該当なし）→ 未確認。研究者向けには External Researcher Access Program（$1,000 相当）がある。
- **Claude Code の動画**: 公式の「Work with images」はドラッグ＆ドロップ・貼り付け・パス指定で画像（PNG / JPEG / GIF / WebP）を読む。**動画の記載は無い**（本文 §6 のとおり、フレームを PNG に書き出して読ませる）。`claude -p` で非対話実行可。
- Max の $100/月で回せる量（本文 §6 の試算）: Sonnet 5.5 で20分全編（5秒に1枚・720p＋文字起こし）約 $0.66 → **月150回**。Haiku 5.5 なら約 $0.21 → 月470回。
- 出典: <https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers> ・ <https://support.claude.com/en/articles/17154008-monthly-api-credits-for-max-and-team-plans> ・ <https://code.claude.com/docs/en/common-workflows>（2026-10-11 確認）。$5 クレジット: <https://www.linkmodel.ai/blog/free-anthropic-api-key> ほか（二次情報）。

#### A.3.7 OpenAI

- **API の動画入力**: 公式の画像・ビジョンの手引きと料金表に動画入力なし（2026-10-10 確認）。openai-node の issue #1778「Responses API にネイティブの動画入力を」が open（2026-10-11 確認）。公式クックブックの手順は ffmpeg でフレーム抽出（二次情報）。
- **新規の無料クレジット**: 「2025 年半ば以降の新規アカウントに自動付与は無い」（二次情報。公式ヘルプの該当記事は 404）→ 未確認（否定的）。
- **ChatGPT のアップロード**: 公式 FAQ「ChatGPT は Free プランを含め、対応するアップロード方法で動画ファイルを添付できる」「ツールを使って解析することがあるが、**全編を解析しない、または音声を正確に解釈しないことがある**」。長さ・サイズの動画固有の上限は明記なし（ファイルの上限に従う。二次情報では 512MB/ファイル、Plus 80 ファイル/3時間）。音声アップロードの記事は「動画と判定された WebM / MP4 は音声アップロードでは非対応」。
- 出典: <https://help.openai.com/en/articles/8400551-chatgpt-image-inputs-faq> ・ <https://help.openai.com/en/articles/8555545-uploading-files-and-audio-to-chatgpt>（プロキシ経由で取得、2026-10-11 確認）・ <https://github.com/openai/openai-node/issues/1778>（2026-10-11 確認）。

#### A.3.8 その他の無料手段

- **ローカル（RTX 4060 8GB）**
  - **Qwen3-VL**（2B / 4B / 8B / 32B…）は Transformers＋`qwen-vl-utils` で動画をネイティブ入力でき、`fps` / `max_pixels` / `total_pixels` でトークン量を制御する。vLLM も動画対応だが Windows 非対応（WSL2 不可の事情は本文 §8）。
  - **Qwen3.5 小型（0.8B / 2B / 4B / 9B、2026-03-02 公開、Apache 2.0）**: 「ネイティブマルチモーダル」でモデルカードに `video_url` の例あり（既定 fps=2）。**音声入力は無い**（text / image / video → text。二次情報）。音声込みは Qwen3.5-Omni（Plus / Flash / Light、重みの公開有無は未確認）で 8GB には載らない（推測）。
  - **llama.cpp（GGUF）・Ollama・LM Studio は動画入力に非対応**（画像のみ。Ollama の issue #12926 / #18151、Unsloth の手引き）。Windows で動画を入れるなら **Transformers＋CUDA（4bit 量子化）**になる（推測。VRAM 8GB で 4B 級・短い区間が現実的。未検証）。
  - 出典: <https://github.com/QwenLM/Qwen3-VL> ・ <https://huggingface.co/Qwen/Qwen3.5-4B> ・ <https://github.com/ollama/ollama/issues/12926> ・ <https://docs.unsloth.ai/models/qwen3-vl-how-to-run-and-fine-tune>（2026-10-11 確認）。
- **Hugging Face**: Inference Providers は **Free ユーザーに月次クレジットなし**（PRO $9/月で $2、Team は席ごと $2。マークアップなしの従量）。ZeroGPU Spaces は無料アカウントで **GPU 5分/日**（48GB、未ログイン 2分、PRO 40分）。Qwen3-VL のデモ Space で短い区間を試す程度なら 0 円で可能（推測）。出典: <https://huggingface.co/docs/inference-providers/pricing> ・ <https://huggingface.co/docs/hub/spaces-zerogpu>（2026-10-11 確認）。
- **Groq**: ビジョン対応は `qwen/qwen3.8-27b` のみ、**画像のみ**（1リクエスト3枚、1枚 2,048 トークン）。動画の記載なし。出典: <https://console.groq.com/docs/vision>（2026-10-11 確認）。
- **Together AI**: 無料枠なし（$5 の前払い。二次情報）。
- **OpenRouter**: `:free` モデルは **20 RPM / 50 RPD**（$10 以上の購入歴があれば 1,000 RPD）。NVIDIA Nemotron 3 Nano Omni (free) が text / image / video / audio 入力（二次情報。モデルカードは取得できず、長さ上限・日本語は未確認）。無料モデルの入力がログ・学習に使われるかは未確認。出典: <https://openrouter.ai/docs/api-reference/limits>（2026-10-11 確認）・ <https://openrouter.ai/collections/free-models>（二次情報）。
- **Mistral / Meta**: 動画入力モデルは見つからず（Pixtral は画像、Llama 4 はフレーム画像。二次情報）→ 未確認（否定的）。
- **Gemini アプリ（gemini.google.com）**: 無料は動画 **合計5分**まで（1ファイル 2GB、1プロンプト 10 ファイル）。Google AI Pro / Ultra で合計1時間。手動のアップロードなので自動化には向かないが、公開前の最終確認を1回だけ目で見るには使える。出典: <https://support.google.com/gemini/answer/14903178>（2026-10-11 確認）。

### A.4 「20分×1回」「1分×10回/日」の収まり方（まとめ）

| 手段 | 20分×1回 | 1分×10回/日 | 超えたときの月額（毎日1回の全編＋1分×10回） |
|---|---|---|---|
| Gemini API 無料枠（3.8 Flash） | 収まる（1リクエスト約12万tok） | 収まる（20 RPD の半分。二次情報の上限） | 有料枠 Tier 1: 約 $0.10 + $0.16 = $0.26/日 → **月約 $8（約1,200円）**。Batch なら半額 |
| Gemini API 無料枠（Flash-Lite） | 収まる | 収まる（500 RPD） | $0.30/M → 月約 $3 |
| Vertex AI（$300 クレジット） | 収まる（約 $0.2） | 収まる | 3.8 Flash $1.35/M（二次情報）: 月約 $14（約2,100円）。$300 で約1年半 |
| TwelveLabs Free | 収まる | 収まる | Developer: $0.61 + $0.52 = $1.13/日 → **月約 $34（約5,100円）** |
| Claude Max クレジット | 収まる（$0.66） | 収まる（約 $1） | Max 5x の $100 内（月約 $50） |
| AWS クレジット + Bedrock Pegasus | 未確認（Marketplace） | 未確認 | 月約 $34 |
| ローカル Qwen3-VL | 区間ごとなら可（遅い・音声なし） | 可 | 0円 |

### A.5 本調査で未確認のもの

- Gemini API 無料枠の公式な RPM / TPM / RPD（AI Studio の画面でしか見られない。本 PC の API キーで実測すべき）。
- Vertex AI の Gemini 3.8 Flash の公式単価（JS 描画で未取得。$1.35/M は検索要約）。Express モードの公式クォータ。
- $300 クレジットが Vertex AI の Gemini に使える旨の**肯定的な**公式記述（除外一覧に無いことしか確認していない）。
- AWS の Free Tier クレジットが Bedrock の Marketplace 経由モデル（Pegasus）に充当できるか。
- TwelveLabs の「600 分」が Analyze（インデックスなし）の動画秒数を消費するか。Free プランで使える Pegasus の版。
- Antigravity CLI の動画入力の可否、Free ティアの具体的な枠。
- Anthropic の新規 Console $5 クレジットの公式記述。
- OpenAI の新規無料クレジット廃止の公式記述。
- OpenRouter の Nemotron 3 Nano Omni の動画長・日本語・ログ方針。Qwen3.5-Omni の重み公開。
