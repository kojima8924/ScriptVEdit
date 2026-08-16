# -*- coding: utf-8 -*-
"""pytest 共通設定

- `tests/layers/` はレイヤー定義ファイル（testNN_*.py）とテスト用プラグインの置き場であり、
  pytest のテストモジュールではないため収集対象から外す。
- スナップショット再生成用のオプション `--snapshot-update` を提供する。
- 実 FFmpeg レンダ（重い）は既定で**収集から外す**。`--realrender`（選抜）/
  `--realrender-all`（全件）/ 環境変数 `SCRIPTVEDIT_REALRENDER` で有効化する
  （tests/test_real_render.py）。skip ではなく deselect にしているのは、
  CI の「想定外 skip 検知」を大量の常設 skip で薄めないため。
"""
import os

# レイヤー定義（DSLスクリプト）は pytest のテストではない
collect_ignore = ["layers"]


def pytest_addoption(parser):
    parser.addoption(
        "--snapshot-update", action="store_true", default=False,
        help="スナップショット(tests/snapshots/*.json)を現在の出力で再生成する")
    parser.addoption(
        "--realrender", action="store_true", default=False,
        help="実FFmpegレンダの選抜（dry_runで踏めない経路）を実行する")
    parser.addoption(
        "--realrender-all", action="store_true", default=False,
        help="実FFmpegレンダを全プロジェクトで実行する（非常に重い）")


def _real_render_mode(config):
    """"off" / "selection" / "all" を決める（オプション優先、次に環境変数）"""
    if config.getoption("--realrender-all"):
        return "all"
    if config.getoption("--realrender"):
        return "selection"
    env = os.environ.get("SCRIPTVEDIT_REALRENDER", "").strip().lower()
    if env in ("all", "full"):
        return "all"
    if env in ("1", "true", "yes", "on", "selection"):
        return "selection"
    return "off"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "realrender: 実FFmpegレンダを伴う重いテスト（既定では収集しない）")
    config._realrender_mode = _real_render_mode(config)
    # 実行ディレクトリに依存せず、キャッシュ(__cache__)はリポジトリルートに置く
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root)


def pytest_collection_modifyitems(config, items):
    """実レンダテストのうち、今回のモードで対象外のものを deselect する"""
    mode = getattr(config, "_realrender_mode", "off")
    if mode == "all":
        return
    try:
        from projects import REAL_RENDER_SELECTION
    except ImportError:            # tests/ が sys.path に無い異例のケース
        REAL_RENDER_SELECTION = []
    keep, drop = [], []
    for item in items:
        if item.get_closest_marker("realrender") is None:
            keep.append(item)
        elif mode == "selection" and any(
                f"[{name}]" in item.name for name in REAL_RENDER_SELECTION):
            keep.append(item)
        else:
            drop.append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
