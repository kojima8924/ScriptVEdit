"""scriptvedit パッケージ内 import の強連結成分（SCC）を測る。

`from scriptvedit.X import ...` / `import scriptvedit.X` を AST で拾い、
モジュール間の有向グラフに対して Tarjan の SCC 分解を行う。
サイズ2以上の SCC は循環 import であり、末尾 import という不文律で
回避されている箇所である。CI で規模の再増加を検出する用途。
"""

from __future__ import annotations

import argparse
import ast
import os
import sys

_PKG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "scriptvedit")

# これを超える SCC があれば失敗にする（現状の実測値に合わせて調整する）
_MAX_SCC_SIZE = 7


def _module_name(path: str) -> str:
    """ファイルパスを 'scriptvedit.filters.video' 形式へ。"""
    rel = os.path.relpath(path, _PKG_DIR).replace(os.sep, ".")
    if rel.endswith(".py"):
        rel = rel[:-3]
    if rel.endswith(".__init__"):
        rel = rel[: -len(".__init__")]
    if rel == "__init__":
        return "scriptvedit"
    return "scriptvedit." + rel


def _collect_modules() -> dict[str, str]:
    """モジュール名 -> ファイルパス。"""
    mods: dict[str, str] = {}
    for root, dirs, files in os.walk(_PKG_DIR):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in files:
            if fn.endswith(".py"):
                p = os.path.join(root, fn)
                mods[_module_name(p)] = p
    return mods


def _edges(mod: str, path: str, known: set[str]) -> set[str]:
    """mod が import している scriptvedit 内モジュールの集合。"""
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    out: set[str] = set()
    pkg_parts = mod.split(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in known:
                    out.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # 相対 import を絶対名へ
                base = pkg_parts[: len(pkg_parts) - node.level + 1]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            if target in known:
                out.add(target)
            # from scriptvedit.pkg import module 形式
            for a in node.names:
                cand = f"{target}.{a.name}"
                if cand in known:
                    out.add(cand)
    out.discard(mod)
    return out


def _tarjan(graph: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan の SCC 分解（再帰なし）。"""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    result: list[list[str]] = []
    counter = 0

    for root in graph:
        if root in index:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, pi = work[-1]
            if pi == 0:
                index[node] = counter
                low[node] = counter
                counter += 1
                stack.append(node)
                on_stack[node] = True
            recurse = False
            succs = sorted(graph.get(node, ()))
            for i in range(pi, len(succs)):
                nxt = succs[i]
                if nxt not in index:
                    work[-1] = (node, i + 1)
                    work.append((nxt, 0))
                    recurse = True
                    break
                if on_stack.get(nxt):
                    low[node] = min(low[node], index[nxt])
            if recurse:
                continue
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    comp.append(w)
                    if w == node:
                        break
                result.append(sorted(comp))
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return result


def analyze() -> list[list[str]]:
    mods = _collect_modules()
    known = set(mods)
    graph = {m: _edges(m, p, known) for m, p in mods.items()}
    # パッケージ本体（__init__）は全モジュールを束ねるので必ず巨大 SCC を作る。
    # 循環の実体を見るために除外する。
    graph.pop("scriptvedit", None)
    for v in graph.values():
        v.discard("scriptvedit")
    return _tarjan(graph)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-size", type=int, default=_MAX_SCC_SIZE,
                    help="許容する最大 SCC サイズ（既定: %(default)s）")
    ap.add_argument("--verbose", action="store_true", help="全 SCC を表示する")
    args = ap.parse_args()

    sccs = [s for s in analyze() if len(s) > 1]
    sccs.sort(key=len, reverse=True)
    if not sccs:
        print("循環 import はありません。")
        return 0

    biggest = len(sccs[0])
    print(f"循環 import: {len(sccs)}個の SCC（最大サイズ {biggest}）")
    for s in sccs if args.verbose else sccs[:5]:
        print(f"  [{len(s)}] " + ", ".join(x.replace("scriptvedit.", "") for x in s))

    if biggest > args.max_size:
        print(f"NG: 最大 SCC サイズ {biggest} が上限 {args.max_size} を超えています。", file=sys.stderr)
        return 1
    print(f"OK: 最大 SCC サイズ {biggest} <= {args.max_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
