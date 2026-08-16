# 全テスト動画の実レンダリング（重い。どのディレクトリからでも実行可）
#   python tests/render_all.py            # 全件
#   python tests/render_all.py test01     # 指定のみ
# 出力先: tests/output/
#
# プロジェクト定義は tests/projects.py に一本化されている（スナップショット
# tests/test_snapshot.py と同じ定義を使う。監査項目11）。ここは pytest を
# 使わずに全件レンダしたいとき用の薄いランナーで、レンダ内容は
#   pytest tests/test_real_render.py --realrender-all
# と完全に同じ。
import os
import sys
import time

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

from projects import PROJECTS, OUTPUT_DIR, out  # noqa: E402


def render(name):
    """PROJECTS の定義どおりに1件を実レンダする"""
    spec = PROJECTS[name]
    for dep in spec.requires:      # 例: test15 は test14 のレイヤーキャッシュを使う
        render(dep)
    with spec.build("real") as project:
        project.render(out(spec.output), **spec.render_kwargs)


if __name__ == "__main__":
    # 引数で特定テストだけ実行可能: python render_all.py test19 test20 test21
    targets = sys.argv[1:] if len(sys.argv) > 1 else None
    unknown = [t for t in (targets or []) if t not in PROJECTS]
    if unknown:
        print(f"不明なテスト名: {', '.join(unknown)}")
        print(f"利用できる名前: {', '.join(PROJECTS)}")
        sys.exit(2)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"=== 動画レンダリング → {OUTPUT_DIR} ===\n")
    ok = 0
    fail = 0
    for name in PROJECTS:
        if targets and name not in targets:
            continue
        t0 = time.time()
        try:
            print(f"--- {name} ---")
            render(name)
            print(f"  OK ({time.time() - t0:.1f}s)\n")
            ok += 1
        except Exception as e:
            print(f"  FAIL ({time.time() - t0:.1f}s): {e}\n")
            fail += 1
    print(f"=== 結果: {ok} OK, {fail} FAIL ===")
    if fail:
        sys.exit(1)
