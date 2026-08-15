# -*- coding: utf-8 -*-
"""未使用 import の再発を検出する（CI の lint ゲート）。

このパッケージは単一ファイルから機械的に分割された経緯があり、放置すると
各モジュールへコピーされた stdlib import の残骸が再び溜まる（実際に約280件
溜まっていた）。pyflakes の "imported but unused" だけを対象に、以下は除外する:

- `src/scriptvedit/__init__.py` … 公開 API の再エクスポートなので全て未使用に見える
- `# noqa` を付けた行 … 存在確認のためだけの import（tts の edge_tts など）

使い方: python scripts/check_unused_imports.py [対象パス]
"""
import os
import re
import subprocess
import sys

_PATTERN = re.compile(r"^(?P<path>.+?):(?P<line>\d+):\d+: '.+' imported but unused$")


def main(target):
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pyflakes", target],
            capture_output=True, text=True)
    except FileNotFoundError:
        print("pyflakes が見つかりません: pip install -e .[dev] を実行してください")
        return 2

    findings = []
    for out_line in proc.stdout.splitlines():
        m = _PATTERN.match(out_line)
        if not m:
            continue
        path = m.group("path").replace("\\", "/")
        if os.path.basename(path) == "__init__.py":
            continue  # 再エクスポート専用
        try:
            with open(path, encoding="utf-8") as f:
                src_line = f.read().splitlines()[int(m.group("line")) - 1]
        except (OSError, IndexError):
            src_line = ""
        if "# noqa" in src_line:
            continue  # 意図的に残している import
        findings.append(out_line)

    if findings:
        print("未使用の import が見つかりました（削除するか # noqa を付けてください）:")
        for f in findings:
            print(f"  {f}")
        return 1
    print("未使用 import なし")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "src/scriptvedit"))
