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

全テスト・カバレッジゲートは維持する。失敗項目をskipやxfailへ変更せず、
スナップショットの上書きや許容誤差の拡大も行わない。
Blender実レンダは独立した `blender-puppet` jobへ移し、既存の失敗があっても
新機能のRGBA・キャッシュ・パッケージ検証が実行されるようにした。
独立jobの成功は、全ライブラリのCI成功を意味しない。
