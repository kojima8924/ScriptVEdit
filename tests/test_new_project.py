# -*- coding: utf-8 -*-
"""`scriptvedit new <path>` の雛形生成テスト（生成物が実際にレンダできること）"""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from scriptvedit.cli import _main  # noqa: E402
from scriptvedit.scaffold import new_project  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO, "src")


def _has_ffmpeg():
    from shutil import which
    return which("ffmpeg") is not None


def _has_japanese_font():
    """日本語フォントが解決できるか（無い環境では text() の生成自体ができない）"""
    from scriptvedit.text import _resolve_font
    try:
        return bool(_resolve_font(None))
    except FileNotFoundError:
        return False


def _load_scaffold_project(root):
    """生成された main.py を exec して Project を返す（末尾の p.render(out) は切る）。

    雛形そのもの（configure の値・レイヤーの登録順）を検査したいので、
    テスト側で Project を組み立て直さず生成物をそのまま実行する。
    main.py は自分でプロジェクト直下へ chdir するため、呼び出し側は
    monkeypatch.chdir() で cwd を戻せるようにしておくこと。
    """
    main_path = os.path.join(root, "main.py")
    with open(main_path, encoding="utf-8") as f:
        src = f.read()
    head, sep, _tail = src.partition("    p.render(out)")
    assert sep, "main.py の雛形が変わった（p.render(out) が見つからない）"
    ns = {"__name__": "__main__", "__file__": main_path}
    exec(compile(head, main_path, "exec"), ns)
    return ns["p"]


def test_scaffold_structure(tmp_path):
    """生成される構造（main.py / layers / assets / plugins / output / README / .gitignore）"""
    root = new_project(str(tmp_path / "myvideo"), quiet=True)
    for rel in ("main.py", "layers/intro.py", "README.md", ".gitignore",
                "plugins/README.md", "assets/images", "assets/audio", "output"):
        assert os.path.exists(os.path.join(root, *rel.split("/"))), rel
    gi = open(os.path.join(root, ".gitignore"), encoding="utf-8").read()
    for pat in ("output/", "__cache__/", "assets/_imported/"):
        assert pat in gi
    readme = open(os.path.join(root, "README.md"), encoding="utf-8").read()
    assert "SCRIPTVEDIT_ASSETS" in readme and "_imported" in readme


def test_scaffold_refuses_nonempty_dir(tmp_path):
    """既存ディレクトリが空でなければエラー（--force で許可）"""
    d = tmp_path / "used"
    d.mkdir()
    (d / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="空ではありません"):
        new_project(str(d), quiet=True)
    new_project(str(d), force=True, quiet=True)  # --force なら生成できる
    assert os.path.exists(d / "main.py")
    assert os.path.exists(d / "keep.txt")  # 既存ファイルは消さない


def test_cli_new(tmp_path, capsys):
    """CLI 経由: scriptvedit new <path> / 未知テンプレートはエラー / 案内メッセージ"""
    rc = _main(["new", str(tmp_path / "cliproj")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "次にやること" in out and "python main.py" in out
    assert os.path.exists(tmp_path / "cliproj" / "main.py")
    rc2 = _main(["new", str(tmp_path / "cliproj")])  # 空でない → エラー
    assert rc2 == 2


def test_scaffold_explainer(tmp_path):
    """--template explainer: 数式・字幕・BGM のレイヤーが入る"""
    root = new_project(str(tmp_path / "exp"), template="explainer", quiet=True)
    body = open(os.path.join(root, "layers", "body.py"), encoding="utf-8").read()
    bgm = open(os.path.join(root, "layers", "bgm.py"), encoding="utf-8").read()
    assert "formula(" in body and "text(" in body
    assert 'asset("audio/bgm.mp3"' in bgm
    main = open(os.path.join(root, "main.py"), encoding="utf-8").read()
    for f in ("intro.py", "body.py", "bgm.py"):
        assert f in main
    with pytest.raises(ValueError, match="未知のテンプレート"):
        new_project(str(tmp_path / "bad"), template="nope", quiet=True)


@pytest.mark.skipif(not _has_japanese_font(), reason="日本語フォントが無い環境")
@pytest.mark.parametrize("template", ["minimal", "explainer"])
def test_scaffold_passes_audit(tmp_path, monkeypatch, template):
    """生成した雛形は p.audit() が warning 0 で、render(strict=True) も通る。

    雛形が自分の品質lintに落ちると、利用者は「まず warning を消す」作業から
    始めることになる（文字の縁取り無し = text-no-decoration で実際に落ちていた）。
    """
    monkeypatch.chdir(tmp_path)   # main.py 自身の chdir をテスト後に戻すため
    root = new_project(str(tmp_path / f"audit_{template}"), template=template,
                       quiet=True)
    p = _load_scaffold_project(root)
    warns = [f for f in p.audit(quiet=True) if f["severity"] == "warning"]
    assert not warns, "\n".join(f"[{f['code']}] {f['message']}" for f in warns)
    # strict=True の dry_run も通る（雛形をそのまま CI の厳格モードに載せられる）
    p.render(os.path.join("output", "audit.mp4"), dry_run=True, strict=True)


@pytest.mark.skipif(not _has_japanese_font(), reason="日本語フォントが無い環境")
@pytest.mark.parametrize("template", ["minimal", "explainer"])
def test_scaffold_small_output_passes_audit(tmp_path, monkeypatch, template):
    """小さい --width/--height で生成しても warning 0（文字サイズを比例縮小する）。

    雛形の px は 1280x720 基準なので、固定のままだと 320x180 では
    文字が画面幅を超えて text-overflow の warning が出る。
    """
    monkeypatch.chdir(tmp_path)
    root = new_project(str(tmp_path / f"small_{template}"), template=template,
                       quiet=True, width=320, height=180, fps=10)
    p = _load_scaffold_project(root)
    warns = [f for f in p.audit(quiet=True) if f["severity"] == "warning"]
    assert not warns, "\n".join(f"[{f['code']}] {f['message']}" for f in warns)


@pytest.mark.skipif(not _has_japanese_font(), reason="日本語フォントが無い環境")
@pytest.mark.parametrize("template", ["minimal", "explainer"])
@pytest.mark.parametrize("size", [(1080, 1920), (720, 1280), (1080, 1080)])
def test_scaffold_vertical_output_passes_audit(tmp_path, monkeypatch,
                                               template, size):
    """縦型・正方形（ショート/リール）でも warning 0。

    audit の2つのルールは別々の軸を見る:
      text-too-small … 下限は**高さ基準**（height/1080 で伸びる）
      text-overflow  … 上限は**幅基準**（width で縮む）
    雛形が短辺比率だけで縮めていた頃は、16:9 より縦長だと
    「下限は伸びるのに文字は縮む」ので必ず衝突した
    （--width 1080 --height 1920 で text-too-small の warning が実際に出ていた）。
    アスペクト比 w/h < 1280/1080 ≒ 1.185 の全てが該当するので、
    1:1 を含む縦型を固定しておく。
    """
    width, height = size
    monkeypatch.chdir(tmp_path)
    root = new_project(str(tmp_path / f"v_{template}_{width}x{height}"),
                       template=template, quiet=True,
                       width=width, height=height, fps=30)
    p = _load_scaffold_project(root)
    warns = [f for f in p.audit(quiet=True) if f["severity"] == "warning"]
    assert not warns, "\n".join(f"[{f['code']}] {f['message']}" for f in warns)


@pytest.mark.skipif(not _has_japanese_font(), reason="日本語フォントが無い環境")
def test_scaffold_explainer_has_no_time_overlap(tmp_path, monkeypatch):
    """explainer の視覚要素が同時刻に重ならない（カーソルはレイヤー毎に0へ戻る）。

    body.py が pause.until("intro.end") を落とすと、本編が 0 秒から始まって
    intro のタイトルとほぼ同位置で重なる。雛形は「レイヤーをまたぐ待ち方」の
    手本でもあるため、時刻の非重複をテストで固定する。
    """
    monkeypatch.chdir(tmp_path)
    root = new_project(str(tmp_path / "exp_time"), template="explainer", quiet=True)
    p = _load_scaffold_project(root)
    p.audit(quiet=True)   # dry_run 経由でレイヤーとアンカーを解決させる
    # 音声(BGM)は全編に重ねるのが正しいので視覚要素だけを見る
    spans = sorted((o.start_time, o.start_time + o.duration) for o in p.objects
                   if getattr(o, "media_type", None) not in (None, "audio"))
    assert len(spans) == 4, spans   # タイトル / サブ / 数式 / 字幕
    for (_s1, e1), (s2, _e2) in zip(spans, spans[1:]):
        assert s2 >= e1 - 1e-9, f"時刻が重なっています: {spans}"


def test_scaffold_bgm_does_not_use_must_exist_false(tmp_path):
    """bgm 雛形は素材の有無を例外で判定する（must_exist=False では取り込まれない）。

    asset(..., must_exist=False) は共有ライブラリ探索より前に返るため、
    SCRIPTVEDIT_ASSETS にしか BGM が無い環境では取り込みが起きず、
    os.path.exists が永久に False になって BGM レイヤーが無効化される。
    """
    root = new_project(str(tmp_path / "bgmsrc"), template="explainer", quiet=True)
    with open(os.path.join(root, "layers", "bgm.py"), encoding="utf-8") as f:
        bgm = f.read()
    # コメント行は落とす（雛形のコメント自体が must_exist に言及しているため）
    code = "\n".join(ln for ln in bgm.splitlines() if not ln.lstrip().startswith("#"))
    assert "must_exist" not in code
    assert "except FileNotFoundError" in code


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg が無い")
@pytest.mark.skipif(not _has_japanese_font(), reason="日本語フォントが無い環境")
def test_scaffold_bgm_imports_from_shared_library(tmp_path, monkeypatch):
    """共有ライブラリにしか無い BGM が assets/_imported/ へ取り込まれて実際に乗る"""
    src_audio = os.path.join(_REPO, "assets", "audio", "bgm_loop.mp3")
    if not os.path.exists(src_audio):
        pytest.skip("素材 assets/audio/bgm_loop.mp3 が無い環境")
    lib = tmp_path / "shared" / "audio"
    lib.mkdir(parents=True)
    shutil.copyfile(src_audio, str(lib / "bgm.mp3"))
    monkeypatch.setenv("SCRIPTVEDIT_ASSETS", str(tmp_path / "shared"))
    monkeypatch.chdir(tmp_path)
    root = new_project(str(tmp_path / "bgmlib"), template="explainer", quiet=True)
    p = _load_scaffold_project(root)
    p.audit(quiet=True)
    assert os.path.exists(os.path.join(root, "assets", "_imported", "audio",
                                       "bgm.mp3"))
    assert any(getattr(o, "has_audio", False) for o in p.objects), "BGM が乗っていない"


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg が無い")
def test_scaffold_renders(tmp_path):
    """生成した雛形が実際にレンダできる（短尺・小サイズ）"""
    root = new_project(str(tmp_path / "rendertest"), quiet=True, width=320, height=180,
                       fps=10)
    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("SCRIPTVEDIT_ASSETS", None)
    out = os.path.join(root, "output", "t.mp4")
    r = subprocess.run([sys.executable, "main.py", out], cwd=root, env=env,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert os.path.getsize(out) > 0
