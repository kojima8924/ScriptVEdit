# スナップショットテスト: dry_runで生成したffmpegコマンドをスナップショットと比較
#
#   pytest tests/test_snapshot.py                    # 検証（どのディレクトリからでも実行可）
#   pytest tests/test_snapshot.py --snapshot-update  # スナップショット再生成
#
# プロジェクト定義は tests/projects.py に一本化されている（実レンダ回帰
# tests/test_real_render.py と同じ定義を使う）。このファイルには
# 「dry_run のコマンドをスナップショットと比較する」処理だけを置く。
import glob
import os, re, json, shutil

import pytest

from scriptvedit import asset
from scriptvedit.text import _resolve_font

from projects import LAYERS_DIR, PROJECTS, SNAPSHOT_NAMES

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)               # リポジトリルート
SNAPSHOT_DIR = os.path.join(TESTS_DIR, "snapshots")

# ffprobe 不在でスナップショット結果が変わるテストだけを依存対象にする。
# モジュール全体を skip すると、影響を受けないフィルタ文字列の回帰まで見逃してしまう。
_FFPROBE_TESTS = frozenset({
    "test01", "test02", "test06", "test07", "test08", "test09", "test11",
    "test12", "test13", "test14", "test15", "test17", "test18", "test20",
    "test23", "test25", "test30", "test36", "test56", "test57", "test58",
    "test79", "test80", "test82", "test84", "test89",
})

_TEST91_CHECKPOINT_RE = re.compile(
    r"__cache__/artifacts/checkpoint/[0-9a-fA-F]{8}/[0-9a-fA-F]{16}\.mkv")
_TEST91_CHECKPOINT_TOKEN = (
    "__cache__/artifacts/checkpoint/<HASH8>/<HASH16>.mkv")

# web/formula のキャッシュ鍵は renderer identity(Playwright バージョン = 同梱
# Chromium リビジョンの代理)を含むため、環境ごとに鍵が変わる。鍵ハッシュだけを
# 比較時に畳む(ffmpeg コマンド構造・フィルタ文字列の検出力は落とさない)。
# 下流の checkpoint も web 生成物を入力に取ると鍵が連動して変わるため対象に含める。
_RENDERER_KEY_RES = [
    # 例: __cache__/artifacts/web/<name>/<16桁>.webm の16桁だけを <HASH16> へ
    (re.compile(r"(?<=/)[0-9a-fA-F]{16}(?=\.webm)"), "<HASH16>"),
    (re.compile(r"(?<=/formula/)[0-9a-fA-F]{16}(?=\.png)"), "<HASH16>"),
    (re.compile(r"(?<=/checkpoint/)[0-9a-fA-F]{8}/[0-9a-fA-F]{16}"),
     "<HASH8>/<HASH16>"),
]

# renderer identity を含む鍵が現れるテスト(web Object / formula を使うもの)
_RENDERER_DEPENDENT_TESTS = frozenset({
    "test19", "test20", "test21", "test29", "test38", "test87", "test91",
})


def _rel_to_root(s):
    """リポジトリ配下の絶対パスをルート相対のposixパスへ畳む（スナップショットの可搬性）

    フィルタ文字列の中に埋め込まれたパス（lut3d=file='...' / movie=filename='...' 等、
    ffmpeg がドライブレターのコロンを `C\\:` とエスケープする形）も畳む。
    """
    root_win = ROOT                                  # C:\repo
    root_posix = ROOT.replace("\\", "/")             # C:/repo
    root_ffesc = root_posix.replace(":", "\\:")      # C\:/repo（ffmpegエスケープ）
    t = (s.replace(root_win + "\\", "")
          .replace(root_ffesc + "/", "")
          .replace(root_posix + "/", ""))
    t = t.replace("\\", "/")
    # フォントパスの正規化: 既定フォントの解決結果は OS ごとに異なる絶対パスに
    # なるため、fontfile='...' の値だけを <FONT> トークンへ畳む（issue #1）。
    # 対象を fontfile パラメータに限定し、それ以外の差分の検出力は落とさない。
    return re.sub(r"fontfile='[^']*'", "fontfile='<FONT>'", t)


def normalize_cmd(cmd):
    """コマンドリスト/辞書をOS非依存に正規化（素材の絶対パスはルート相対に畳む）"""
    if isinstance(cmd, dict):
        result = {}
        for k, v in cmd.items():
            nk = _rel_to_root(k) if isinstance(k, str) else k
            result[nk] = normalize_cmd(v)
        return result
    if isinstance(cmd, list):
        return [_rel_to_root(c) for c in cmd]
    return cmd


def _normalize_snapshot_comparison(name, value):
    """環境依存のキャッシュ鍵ハッシュだけを比較時に正規化する。

    対象は (1) test91 の数式PNG由来 checkpoint、(2) renderer identity
    (Playwright/Chromium バージョン)を鍵に含む web/formula 生成物とその下流。
    フィルタ文字列・コマンド構造・素材パスはレンダ内容そのものなので保持する。
    スナップショット保存前には呼ばず、具体的なキャッシュ鍵も記録に残す。
    """
    if name != "test91" and name not in _RENDERER_DEPENDENT_TESTS:
        return value
    if isinstance(value, dict):
        return {
            _normalize_snapshot_comparison(name, key):
            _normalize_snapshot_comparison(name, item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_snapshot_comparison(name, item) for item in value]
    if isinstance(value, str):
        text = _TEST91_CHECKPOINT_RE.sub(_TEST91_CHECKPOINT_TOKEN, value)
        if name in _RENDERER_DEPENDENT_TESTS:
            for pattern, repl in _RENDERER_KEY_RES:
                text = pattern.sub(repl, text)
        return text
    return value


def _skip_missing_snapshot_assets(spec):
    """gitignore 対象の大容量素材が無いテストを正直に skip する。"""
    for relpath in spec.assets:
        try:
            asset(relpath)
        except FileNotFoundError:
            pytest.skip(f"素材 assets/{relpath} が無い環境")


def load_snapshot(name):
    path = os.path.join(SNAPSHOT_DIR, f"{name}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_snapshot(name, cmd):
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = os.path.join(SNAPSHOT_DIR, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cmd, f, indent=2, ensure_ascii=False)


def dry_run_commands(name):
    """PROJECTS の定義から dry_run の ffmpeg コマンド（正規化済み）を得る"""
    spec = PROJECTS[name]
    with spec.build("dry_run") as project:
        cmd = project.render(spec.output, dry_run=True, **spec.render_kwargs)
    return normalize_cmd(cmd)


# --- pytest 版（本物の assert で検証する） ---

def _snapshot_update_requested(request):
    """--snapshot-update が指定されていればスナップショットを再生成する"""
    try:
        return bool(request.config.getoption("--snapshot-update"))
    except Exception:
        return False


@pytest.mark.parametrize("name", SNAPSHOT_NAMES)
def test_snapshot(name, request):
    """dry_run で生成した ffmpeg コマンドがスナップショットと一致すること"""
    spec = PROJECTS[name]
    if name in _FFPROBE_TESTS and shutil.which("ffprobe") is None:
        pytest.skip("ffprobe が見つからないためスキップします")
    _skip_missing_snapshot_assets(spec)
    if "font" in spec.needs:
        try:
            _resolve_font(None)
        except FileNotFoundError as exc:
            pytest.skip(str(exc))
    cmd = dry_run_commands(name)
    if _snapshot_update_requested(request):
        save_snapshot(name, cmd)
        pytest.skip(f"{name}: スナップショットを再生成しました")
    expected = load_snapshot(name)
    assert expected is not None, (
        f"{name}: スナップショットがありません。"
        f"`pytest tests/test_snapshot.py --snapshot-update` で生成してください")
    actual_cmp = _normalize_snapshot_comparison(name, cmd)
    expected_cmp = _normalize_snapshot_comparison(name, expected)
    assert actual_cmp == expected_cmp, (
        f"{name}: ffmpegコマンドがスナップショットと一致しません")


def test_every_layer_group_has_a_project():
    """tests/layers/testNN_*.py の全番号に PROJECTS の定義があること

    レイヤーだけ足して登録を忘れると、そのレイヤーは dry_run でも実レンダでも
    一度も実行されない（旧 render_all.py は glob で自動登録していたため、
    定義の一本化で拾い漏れが生まれないよう明示的に縛る）。
    """
    groups = set()
    for path in glob.glob(os.path.join(LAYERS_DIR, "test*_*.py")):
        m = re.match(r"(test\d+)_", os.path.basename(path))
        if m:
            groups.add(m.group(1))
    missing = sorted(groups - set(PROJECTS))
    assert not missing, (
        f"tests/projects.py に定義の無いレイヤーがあります: {missing}")


def test_snapshot_files_match_project_definitions():
    """スナップショットファイルと PROJECTS の対象が過不足なく対応すること

    定義を消したのに .json が残る（＝守るものが無いのに緑）状態と、
    実レンダ専用（snapshot=False）のはずが .json を持っている状態を検出する。
    """
    files = {os.path.splitext(f)[0] for f in os.listdir(SNAPSHOT_DIR)
             if f.endswith(".json")}
    assert files - set(SNAPSHOT_NAMES) == set(), (
        "定義の無いスナップショットが残っています（削除してください）")


def test_test91_checkpoint_hash_normalization():
    """test91 は checkpoint ハッシュだけが違っても同一と判定する。"""
    actual = {
        "main": [
            "__cache__/artifacts/checkpoint/aaaaaaaa/1111111111111111.mkv",
            "__cache__/artifacts/formula/formula-a.png",
            "-filter_complex", "overlay=x=10:y=20",
        ],
        "cache": {
            "__cache__/artifacts/checkpoint/aaaaaaaa/1111111111111111.mkv": [
                "__cache__/artifacts/checkpoint/aaaaaaaa/1111111111111111.mkv"],
        },
    }
    expected = {
        "main": [
            "__cache__/artifacts/checkpoint/bbbbbbbb/2222222222222222.mkv",
            "__cache__/artifacts/formula/formula-a.png",
            "-filter_complex", "overlay=x=10:y=20",
        ],
        "cache": {
            "__cache__/artifacts/checkpoint/bbbbbbbb/2222222222222222.mkv": [
                "__cache__/artifacts/checkpoint/bbbbbbbb/2222222222222222.mkv"],
        },
    }
    assert (_normalize_snapshot_comparison("test91", actual)
            == _normalize_snapshot_comparison("test91", expected))
    # test91 以外には同じ救済を適用しない。
    assert (_normalize_snapshot_comparison("test90", actual)
            != _normalize_snapshot_comparison("test90", expected))


@pytest.mark.parametrize("index,replacement", [
    (1, "__cache__/artifacts/formula/formula-b.png"),
    (3, "overlay=x=11:y=20"),
])
def test_test91_normalization_preserves_render_differences(index, replacement):
    """formula 素材やフィルタの実質差分は正規化後も検出する。"""
    actual = [
        "__cache__/artifacts/checkpoint/aaaaaaaa/1111111111111111.mkv",
        "__cache__/artifacts/formula/formula-a.png",
        "-filter_complex", "overlay=x=10:y=20",
    ]
    expected = list(actual)
    expected[0] = "__cache__/artifacts/checkpoint/bbbbbbbb/2222222222222222.mkv"
    expected[index] = replacement
    assert (_normalize_snapshot_comparison("test91", actual)
            != _normalize_snapshot_comparison("test91", expected))
