# Blender バックエンド追加時の CI 基準点

## 検証した変更と結果

機能コミット: `1d1e0a12ac1acb92b9922ed7eee6d0824dcd758a`。
CI run: https://github.com/kojima8924/ScriptVEdit/actions/runs/44565415663

Blender 切り絵機能のローカル22項目は成功した。実レンダでは親子回転、RGBA、透明度継承、
コマ数、キャッシュ再利用をピクセルとファイルで確認している。
バックエンド＋マニフェスト／ドキュメント検証は183項目、バックエンド＋既存stills/framesは
68項目が成功（22項目は重複）。wheelビルドとライセンス同梱確認も成功した。

CI の wheel・インストール済みパッケージの import・各 extra の単独インストールも成功。
全テストの Ubuntu/Python 3.10 は **6 failed / 2778 passed / 50 skipped**、
Windows/Python 3.13 は **6 failed / 2788 passed / 40 skipped** となった。
これを成功扱いにはしていない。

## 変更前でも再現した項目

変更前のコミット `3f91a4ad6b04270575ae84352861fb81685f0c5e` を独立した worktree へ取得し、
同じ FFmpeg 8.1.3 とローカル依存で再実行した。
以下のLinux CIの6件すべては、Blender機能を含まないそのコードでも再現した。

| 項目 | 観測した差 |
| --- | --- |
| `test_fly_to.py::test_golden_frames` | `dissolve_in` の基準330×150に対し生成330×162 |
| `test_fx_glint.py::test_fit_and_box` | 等倍・無効果の画素が元画像と完全一致しない |
| `test_fx_glint.py::test_light_only_brightens_opaque_pixels` | alpha=0の画素にRGBが残る |
| `test_fx_timeline.py::test_later_mark_goes_under_an_earlier_label` | 23:00と22:35の札の上下関係が期待と異なる |
| `test_parallel_render.py::test_parallel_audio_stream_timing_matches_sequential[mkv]` | 並列MKVの音声開始が−0.021秒、逐次は0秒 |
| `test_snapshot.py::test_snapshot[test137]` | フォントに依存する素材寸法・座標が保存済みスナップショットと異なる |

Windows/Python 3.13では fly_to の代わりに
`test_framekit.py::test_label_matches_text_image_pixels[variable]` が失敗。
選択されたBahnschriftの許容weightは300〜700なのに、テストが800を指定している。
このWindows固有項目の変更前worktreeでの実行は行っていない。

変更前ソース `bfa6b5cb90d789e56574e08bcc0844bb31a624ac` の11月1日のCIは、
FFmpeg固定ダウンロード先が失効してテスト開始前に失敗していた。
今回、検証済みSHA256付きの11月19日固定版へ更新したことで全テストまで到達した。

## 再現コマンド

FFmpeg 8 / ffprobe、pytest、numpy、scipy、opencv-python、Pillow、tqdmが必要。

```bash
PYTHONPATH=src python -m pytest -q \
  tests/test_fly_to.py::test_golden_frames \
  tests/test_fx_glint.py::test_fit_and_box \
  tests/test_fx_glint.py::test_light_only_brightens_opaque_pixels \
  tests/test_fx_timeline.py::test_later_mark_goes_under_an_earlier_label \
  'tests/test_parallel_render.py::test_parallel_audio_stream_timing_matches_sequential[mkv]' \
  'tests/test_snapshot.py::test_snapshot[test137]'
```

比較したローカル環境は Python3.12、FFmpeg8.1.3、NumPy2.3.5、SciPy1.17.0、
OpenCV5.1.0、Pillow13.0.0。Linux CI/Python3.10ではNumPy2.2.6、SciPy1.15.3、
OpenCV5.1.0、Pillow12.3.0で同じ6件を観測した。

## CI の扱い

全テスト・カバレッジゲートは維持する。この基準点の記録では失敗項目をskipやxfailへ
変更せず、スナップショットの上書きや許容誤差の拡大も行っていない。
Blender実レンダは独立した `blender-puppet` jobへ移し、既存の失敗があっても
新機能のRGBA・キャッシュ・パッケージ検証が実行されるようにした。
独立jobの成功は、全ライブラリのCI成功を意味しない。

## 基準点の後に確認した原因と修正

### 透明な画素の色（glint の2件）

`framekit.to_rgba8()` は浮動小数の `cv2.divide(255.0, 0)` が0を返すと仮定していた。
OpenCV5.1では無限大となり、その後の `0×inf` の8bit変換がRGB255を返す。
ロゴの等倍比較では10,983画素にRGB255／alpha0が残っていた。
0へ丸まるアルファの逆数を明示的に0へ戻し、透明な穴と薄い有色画素の変換を回帰で固定した。

古い生成動画を再利用しないため `_FRAMEKIT_VER` を2から3へ上げる。
`test127/128/132/133/134/135` の保存値は、版2で実行すると既存ファイルと完全一致し、
版3で実行すると `frames/*.mov` のハッシュだけが変わることを確認して更新した。
`tests/golden/framekit/` は6個のJSONの版番号だけを更新し、PNGのバイト列は変更しない。
非フォントの5枚は元の厳しい画素基準を満たす（3枚は完全一致、残りの最大差は1と4）。

### 並列MKVのAAC初期遅延

FFmpeg8.1のMatroska muxerは、エンコーダの `initial_padding` を `CodecDelay` に保存する。
この動作は[公式ソースの音声トラック処理](https://ffmpeg.org/doxygen/8.1/matroskaenc_8c_source.html#l02071)
でも確認できる。M4Aには初期遅延が別の形で保存されるため、M4AからMKVへのstream copyで
このフィールドが失われていた。

48kHz・2秒の同一素材で、逐次MKVは初期遅延1024を持ち、デコード結果は96,256サンプル。
修正前の並列MKVは初期遅延0で97,280サンプルとなり、頭に1024サンプル余分に残った。
MKV出力だけ音声レグをMKAにしてCodecDelayを保つと、開始時刻とPCMのMD5が逐次出力と
完全一致する。MP4／MOVと区間結合の音声レグはM4Aのまま、最終muxは映像・音声ともcopy。
比較はMP4／MKVそれぞれ48kHz／44.1kHzへ拡張し、全4条件で開始時刻とPCMの一致を確認した。

タイムラインの札の順序については、Notoフォントで22:35の箱が23:00の柱を覆わなくなり、
元のテストが前提とした相互の重なりが成立していなかった。配置ソースの規則を変える理由は
確認できず、フォントを固定したfixtureで前提を明示する方針とした。

修正後、framekit・全 `test_fx_*.py`・parallel・segments・上記6個のスナップショットを
まとめて実行し、**645 passed / 24 skipped** を確認した（Linux/Python3.12）。
この数は全ライブラリ・全OSの成功を意味しない。未使用importと循環importのゲートも成功。
