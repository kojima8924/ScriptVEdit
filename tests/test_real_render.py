# -*- coding: utf-8 -*-
"""実 FFmpeg レンダの回帰テスト（重いので既定ではスキップ）

プロジェクト定義は `tests/projects.py` にスナップショットテストと共通化されている
（監査項目11）。dry_run のコマンド比較では原理的に踏めない経路——formula の寸法が
確定してからの pad/copy(SEGVバリア)、movie= のタイムベース正規化、tpad+trim、
4000字超フィルタの一時ファイル外部化——は、ここでしかカバーできない。

実行方法::

    pytest tests/test_real_render.py --realrender       # 選抜（4本・CIと同じ）
    pytest tests/test_real_render.py --realrender-all   # 全プロジェクト
    SCRIPTVEDIT_REALRENDER=1   pytest tests/test_real_render.py   # 選抜
    SCRIPTVEDIT_REALRENDER=all pytest tests/test_real_render.py   # 全件

既定（オプションも環境変数も無し）は全件 skip なので `pytest tests/` は軽いまま。
出力は tests/output/ に置かれる。

注意: 実レンダでチェックポイントが実体化すると dry_run が生成するコマンドが
変わるため、この後にスナップショットを回すときは
`python -m scriptvedit cache --clear` を先に実行すること（CLAUDE.md §3）。
"""
import contextlib
import glob
import os
import re
import shutil
import subprocess

import pytest

from scriptvedit.text import _resolve_font

from projects import PROJECTS, REAL_RENDER_SELECTION, out

# 選抜テストが「どの経路のために常時実行されるのか」。CI はこれだけを回す。
_SELECTION_REASON = {
    "test76": "mask_wipe の movie= 入力を fps/setpts で正規化する経路"
              "（FFmpeg 8 の framesync 実挙動は dry_run では確かめられない）",
    "test77": "start>0 の入力へ入る tpad と trim の順序"
              "（尺が縮まないことは実フレームでしか確認できない）",
    "test92": "formula の数式PNGは dry_run 時点で未生成のため寸法が取れず、"
              "scale の pad(SEGVバリア)+copy が実レンダでしか出ない",
    "test95": "4000字超フィルタの一時ファイル外部化（-/filter_complex）は"
              "実行時にしか起きない",
}


def _require_env(spec):
    """このプロジェクトの実レンダに必要な外部環境を確認する（無ければ skip）"""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe が無い環境")
    for relpath in spec.assets:
        from scriptvedit import asset
        try:
            asset(relpath)
        except FileNotFoundError:
            pytest.skip(f"素材 assets/{relpath} が無い環境")
    if "font" in spec.needs:
        try:
            _resolve_font(None)
        except FileNotFoundError as exc:
            pytest.skip(str(exc))
    if "web" in spec.needs:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            pytest.skip("Playwright が無い環境")
        try:
            with sync_playwright() as pw:
                pw.chromium.launch().close()
        except Exception as exc:
            pytest.skip(f"Chromium が起動できない環境: {type(exc).__name__}")
    if "morph" in spec.needs:
        for mod in ("numpy", "scipy", "cv2", "PIL"):
            pytest.importorskip(mod, reason=f"{mod} が無い環境（morph extras）")


def _assert_output(path):
    """出力が実在して中身があること（連番PNGは1枚以上あること）"""
    if path.endswith(".png"):
        stem = path[:-4]
        frames = sorted(glob.glob(f"{stem}_*.png"))
        assert frames, f"連番PNGが生成されていません: {stem}_%05d.png"
        assert all(os.path.getsize(f) > 0 for f in frames)
        return
    assert os.path.exists(path), f"出力が生成されていません: {path}"
    assert os.path.getsize(path) > 0, f"出力が空です: {path}"
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=300)
    frames = probe.stdout.strip().splitlines()
    assert frames and frames[0].strip().isdigit() and int(frames[0]) > 0, (
        f"映像フレームが読み取れません: {path} / {probe.stderr[-400:]}")


def _render(name):
    """PROJECTS の定義どおりに実レンダして出力パスを返す"""
    spec = PROJECTS[name]
    target = out(spec.output)
    with spec.build("real") as project:
        project.render(target, **spec.render_kwargs)
    return target


@contextlib.contextmanager
def _record_ffmpeg_commands():
    """実際に起動された ffmpeg コマンドを記録する（本物の実行はそのまま行う）"""
    import scriptvedit.ffmpeg as _ff
    import scriptvedit.parallel as _par
    import scriptvedit.project as _pj

    recorded = []
    originals = {}

    def _wrap(orig):
        def _rec(cmd, *args, **kwargs):
            recorded.append(list(cmd))
            return orig(cmd, *args, **kwargs)
        return _rec

    for mod in (_ff, _pj, _par):
        if hasattr(mod, "_run_ffmpeg"):
            originals[mod] = mod._run_ffmpeg
            mod._run_ffmpeg = _wrap(mod._run_ffmpeg)
    try:
        yield recorded
    finally:
        for mod, orig in originals.items():
            mod._run_ffmpeg = orig


# 選抜テストが「狙った経路を本当に通ったか」を、実行された ffmpeg コマンドで確認する。
# 対象のフィルタは中間ファイル生成コマンドにしか現れないものがあるので、
# 検証前に当該プロジェクトのキャッシュ生成物を落として cold にしてから実レンダする。
_PATH_ASSERTS = {
    "test76": [(r"movie=filename=", "mask_wipe の movie= 入力"),
               (r"setpts=N/\(", "movie= のタイムベース正規化")],
    "test77": [(r"tpad=start_duration=", "start>0 の入力への tpad")],
    "test92": [(r"pad=[^,]*eval=frame,copy",
                "scale の pad 直後の copy（SEGVバリア）")],
}

_CACHE_PATH_RE = re.compile(r"[^\s'\"]*__cache__[^\s'\",;\\]*")


def _evict_cached_artifacts(spec):
    """このプロジェクトのキャッシュ生成物を消して cold 状態にする

    生成コマンドは「まだ生成物が無いとき」しか走らないため、温まったキャッシュの
    ままでは狙った経路のフィルタが1つも実行されず、検証が空洞になる
    （CI は常に cold だが、手元では前回のレンダが残っている）。
    """
    with spec.build("dry_run") as project:
        plan = project.render(spec.output, dry_run=True, **spec.render_kwargs)
    candidates = set(plan.get("cache", {}))
    for cmd in [plan["main"], *plan.get("cache", {}).values()]:
        for arg in cmd:
            candidates.update(_CACHE_PATH_RE.findall(arg))
    for path in candidates:
        normalized = path.replace("\\:", ":")
        if os.path.isfile(normalized):
            os.remove(normalized)


def _assert_paths_were_taken(name, commands):
    """狙った FFmpeg 経路が実際のコマンドに現れたことを確認する"""
    blob = "\n".join(" ".join(c) for c in commands)
    for pattern, what in _PATH_ASSERTS.get(name, ()):
        assert re.search(pattern, blob), (
            f"{name}: {what} が実行コマンドに現れませんでした"
            f"（この経路のための選抜テストです）")


@pytest.mark.realrender
@pytest.mark.parametrize("name", list(PROJECTS))
def test_real_render(name, request):
    """実 FFmpeg でレンダが完走し、再生可能な出力が得られること

    既定では収集対象から外れる（conftest.py が deselect する）。`--realrender` は
    「dry_run で踏めない経路」の選抜だけ、`--realrender-all` は全プロジェクト。
    選抜テストでは、狙った経路のフィルタが実際のコマンドに出たことも確認する。
    """
    # deselect が効かない異例の呼び出し（別 rootdir 等）への保険
    if getattr(request.config, "_realrender_mode", "off") == "off":
        pytest.skip("実レンダは既定で無効（--realrender / --realrender-all で有効化）")
    spec = PROJECTS[name]
    _require_env(spec)
    for dep in spec.requires:
        # 例: test15(cache='use') は test14(cache='make') のレイヤーキャッシュが要る
        _require_env(PROJECTS[dep])
        _render(dep)
    if name in _PATH_ASSERTS:
        _evict_cached_artifacts(spec)
    with _record_ffmpeg_commands() as commands:
        target = _render(name)
    _assert_output(target)
    _assert_paths_were_taken(name, commands)
    if name == "test95":
        # 4000字超フィルタ = ffmpeg.py が一時ファイルへ外部化する分岐
        from scriptvedit.ffmpeg import _FILTER_SCRIPT_THRESHOLD
        longest = max((len(a) for c in commands for a in c), default=0)
        assert longest >= _FILTER_SCRIPT_THRESHOLD, (
            f"フィルタが短すぎて外部化経路を踏んでいません: {longest}文字")


def test_selection_is_documented_and_present():
    """選抜リストが実在の定義を指し、理由が書かれていること

    選抜は「dry_run で踏めない経路」に紐づく契約なので、名前を足したのに理由が
    書かれていない/定義が消えている状態を検出する（軽いので常時実行）。
    """
    assert REAL_RENDER_SELECTION, "選抜リストが空です"
    for name in REAL_RENDER_SELECTION:
        assert name in PROJECTS, f"選抜 {name} の定義がありません"
        assert _SELECTION_REASON.get(name), f"選抜 {name} の理由が未記載です"
    assert set(_SELECTION_REASON) == set(REAL_RENDER_SELECTION)
