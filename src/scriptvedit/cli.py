# -*- coding: utf-8 -*-

import subprocess
import os
import sys
import json
import math as _math
import time as _time


_WATCH_EXTENSIONS = {
    ".py", ".html", ".htm", ".css", ".js", ".srt", ".ass", ".vtt",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac",
    ".mp4", ".mov", ".webm", ".mkv",
    ".ttf", ".otf", ".ttc",
    ".cube",
}
# 監視から除外するディレクトリ名（キャッシュ/生成物）
_WATCH_SKIP_DIRS = {"__cache__", "__pycache__", ".git", "output"}


def _watch_targets(script_path):
    """監視対象ファイル集合を返す（スクリプト自身 + サブディレクトリを含む
    .py レイヤーおよび画像/音声/フォント等の素材ファイル）。
    キャッシュ/生成物ディレクトリは除外する。"""
    script_path = os.path.abspath(script_path)
    targets = {script_path}
    d = os.path.dirname(script_path)
    try:
        for dirpath, dirs, files in os.walk(d):
            # キャッシュ/生成物ディレクトリを探索対象から除外（in-place で剪定）
            dirs[:] = [x for x in dirs if x not in _WATCH_SKIP_DIRS]
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext in _WATCH_EXTENSIONS:
                    targets.add(os.path.join(dirpath, name))
    except OSError:
        pass
    return targets


def _snapshot_mtimes(paths):
    """パス集合の mtime スナップショット dict を返す"""
    snap = {}
    for p in paths:
        try:
            snap[p] = os.stat(p).st_mtime
        except OSError:
            snap[p] = None
    return snap


def watch(script_path, *, out=None, interval=0.5, max_cycles=None):
    """script_path と同ディレクトリの .py を監視し、変更時に再実行する。

    標準ライブラリのみ（os.stat ポーリング）。チェックポイント/レイヤー
    キャッシュが効くため差分再生成は高速。Ctrl-C で停止。
    max_cycles を指定するとその回数だけポーリングして戻る（テスト用）。
    """
    script_path = os.path.abspath(script_path)
    if not os.path.isfile(script_path):
        raise FileNotFoundError(f"watch: スクリプトが見つかりません: {script_path}")
    if (isinstance(interval, bool) or not isinstance(interval, (int, float))
            or not _math.isfinite(interval) or interval <= 0):
        raise ValueError(f"watch: interval は正の有限数（秒）で指定してください: {interval!r}")
    if max_cycles is not None and (isinstance(max_cycles, bool)
                                   or not isinstance(max_cycles, int) or max_cycles < 1):
        raise ValueError(f"watch: max_cycles は1以上の整数で指定してください: {max_cycles!r}")

    def _run():
        cmd = [sys.executable, script_path]
        if out:
            cmd.append(out)
        print(f"[watch] 実行: {' '.join(cmd)}")
        t0 = _time.perf_counter()
        rc = subprocess.run(cmd, cwd=os.path.dirname(script_path)).returncode
        dt = _time.perf_counter() - t0
        status = "成功" if rc == 0 else f"失敗(rc={rc})"
        print(f"[watch] {status} ({dt:.2f}s) 変更を待機中... (Ctrl-Cで終了)")

    print(f"[watch] 監視開始: {script_path}")
    _run()  # 起動時に1回実行
    targets = _watch_targets(script_path)
    last = _snapshot_mtimes(targets)
    cycles = 0
    try:
        while True:
            _time.sleep(interval)
            cycles += 1
            targets = _watch_targets(script_path)  # 新規ファイル追加も検知
            cur = _snapshot_mtimes(targets)
            changed = [p for p in cur if cur[p] != last.get(p)]
            if changed:
                names = ", ".join(os.path.basename(p) for p in changed)
                print(f"[watch] 変更検知: {names}")
                _run()
                last = cur
            if max_cycles is not None and cycles >= max_cycles:
                print("[watch] max_cycles 到達。監視を終了します。")
                return
    except KeyboardInterrupt:
        print("\n[watch] 監視を終了しました。")


def _main(argv=None):
    """CLI エントリポイント: describe / cache 管理 / watch モード"""
    import argparse
    parser = argparse.ArgumentParser(
        prog="scriptvedit",
        description="scriptvedit: ケイパビリティ・マニフェスト / キャッシュ管理 / watch モード")
    sub = parser.add_subparsers(dest="command")

    p_desc = sub.add_parser(
        "describe", help="使える機能・シグネチャ・制約のマニフェストを出力（AI向け）")
    p_desc.add_argument("--format", choices=["json", "md"], default="json",
                        help="出力形式（既定: json）")
    p_desc.add_argument("--kind", default=None,
                        help="種別で絞る: " + "/".join(sorted(_MANIFEST_KIND_SECTIONS)))
    p_desc.add_argument("--name", default=None, help="単一エントリ名で絞る（例: fade）")
    p_desc.add_argument("-o", "--out", default=None, help="ファイルへ出力（既定: stdout）")

    p_cache = sub.add_parser("cache", help="__cache__ の統計・GC・全削除")
    p_cache.add_argument("--stats", action="store_true", help="種別ごとの件数・サイズを表示")
    p_cache.add_argument("--gc", action="store_true", help="古い生成物を削除")
    p_cache.add_argument("--keep-days", type=float, default=7.0,
                         help="--gc で残す日数（既定: 7）")
    p_cache.add_argument("--clear", action="store_true", help="キャッシュを全削除")
    p_cache.add_argument("--dir", default=_CACHE_DIR, help="キャッシュディレクトリ")
    p_cache.add_argument("--yes", action="store_true",
                         help="--dir が __cache__ 配下でなくても削除を許可する（誤指定防止の確認）")

    p_new = sub.add_parser("new", help="動画プロジェクトの雛形を生成する")
    p_new.add_argument("path", help="生成先ディレクトリ")
    p_new.add_argument("--template", choices=list(_SCAFFOLD_TEMPLATES), default="minimal",
                       help="雛形の種類（既定: minimal / explainer=数式・字幕・BGM入り）")
    p_new.add_argument("--force", action="store_true",
                       help="生成先が空でなくても生成する")
    p_new.add_argument("--width", type=int, default=1280, help="幅（既定: 1280）")
    p_new.add_argument("--height", type=int, default=720, help="高さ（既定: 720）")
    p_new.add_argument("--fps", type=int, default=30, help="fps（既定: 30）")

    p_watch = sub.add_parser("watch", help="スクリプト変更を監視して再実行")
    p_watch.add_argument("script", help="監視する Python スクリプト")
    p_watch.add_argument("--out", help="出力パス（スクリプトへ引数として渡す）")
    p_watch.add_argument("--interval", type=float, default=0.5, help="ポーリング間隔（秒）")
    p_watch.add_argument("--max-cycles", type=int, default=None,
                         help="指定回数だけポーリングして終了（テスト用）")

    args = parser.parse_args(argv)

    if args.command == "describe":
        try:
            manifest = describe(kind=args.kind, name=args.name)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if args.format == "md":
            text_out = describe_markdown(manifest)
        else:
            text_out = json.dumps(manifest, ensure_ascii=False, indent=2)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text_out)
            print(f"describe: {args.out} に書き出しました "
                  f"({len(text_out)} 文字, format={args.format})")
        else:
            print(text_out)
        return 0
    if args.command == "cache":
        if (not _math.isfinite(args.keep_days)) or args.keep_days < 0:
            print(f"cache: --keep-days は0以上の有限数で指定してください: {args.keep_days}",
                  file=sys.stderr)
            return 2
        try:
            if args.clear:
                cache_clear(args.dir, force=args.yes)
                return 0
            if args.gc:
                cache_gc(args.keep_days, args.dir, force=args.yes)
                return 0
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        cache_stats(args.dir)  # 既定は統計表示（--stats 指定時も同じ）
        return 0
    if args.command == "new":
        try:
            new_project(args.path, template=args.template, force=args.force,
                        width=args.width, height=args.height, fps=args.fps)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return 0
    if args.command == "watch":
        try:
            watch(args.script, out=args.out, interval=args.interval,
                  max_cycles=args.max_cycles)
        except (FileNotFoundError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return 0
    parser.print_help()
    return 1


# --- プラグイン自動読込（import 時: カレントディレクトリの plugins/） ---
# 環境変数 SCRIPTVEDIT_NO_PLUGINS を設定すると自動読込を無効化できる。
#


# --- 遅延解決の相互参照（関数本体からのみ使用: 循環importを避けるため末尾で束縛）---
from scriptvedit.cache import cache_clear, cache_gc, cache_stats
from scriptvedit.manifest import _MANIFEST_KIND_SECTIONS, describe, describe_markdown
from scriptvedit.scaffold import TEMPLATES as _SCAFFOLD_TEMPLATES, new_project
from scriptvedit.state import _CACHE_DIR
