# scriptvedit ショーケース

ポートフォリオ用のショーケース動画（約27秒・1920x1080）をレンダするサンプル。

## 依存

- `pip install -e .[all]`（リポジトリルートで実行。ffmpeg / ffprobe が PATH に必要）
- **Pillow**（ウォーターマーク画像の生成に必要）: `pip install Pillow`
- **Playwright + Chromium**（HTML スライドのレンダリングに必要）:
  `pip install playwright && playwright install chromium`
- 任意: BGM 用の音声ファイル（後述）

## レンダ手順

```bash
cd examples/showcase
python render_showcase.py
```

`slides/watermark.png`（gitignore 対象の生成物）が無ければ、
`showcase_generate.py` が自動実行されて決定論的に生成される。
手動で再生成したい場合は次を実行する。

```bash
python showcase_generate.py
```

出力は `examples/showcase/output_showcase.mp4`。

## 構成ファイル

| ファイル | 役割 |
|---|---|
| `render_showcase.py` | main。構成（レイヤー順・出力）だけを持つ |
| `showcase_generate.py` | ウォーターマーク PNG（`slides/watermark.png`）を PIL で生成 |
| `showcase_slides.py` | HTML スライド4枚のレイヤー（`slides/*.html` は追跡済み） |
| `showcase_objects.py` | 移動・イージングのデモオブジェクト |
| `showcase_watermark.py` | ウォーターマーク（`slides/watermark.png` を参照） |
| `showcase_bgm.py` | 任意の BGM レイヤー（既定では未登録） |

## BGM を付ける（任意）

1. 任意の楽曲を `assets/audio/bgm.mp3` に置く
   （リポジトリの `assets/audio/`、または共有素材ライブラリ
   `SCRIPTVEDIT_ASSETS` 配下の `audio/`。ライセンスは各自で確認すること）。
2. `render_showcase.py` の `build_project()` にあるコメントアウトを外す:

   ```python
   p.layer(os.path.join(_HERE, "showcase_bgm.py"), priority=3)
   ```

別のファイル名を使う場合は `showcase_bgm.py` の `asset("audio/bgm.mp3")` を
合わせて書き換える。
