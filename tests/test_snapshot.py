# スナップショットテスト: dry_runで生成したffmpegコマンドをスナップショットと比較
#
#   pytest tests/test_snapshot.py                    # 検証（どのディレクトリからでも実行可）
#   pytest tests/test_snapshot.py --snapshot-update  # スナップショット再生成
#   python tests/test_snapshot.py --update           # 同上（pytest 無しでも可）
import sys, os, re, json, shutil

import pytest

from scriptvedit import *
from scriptvedit.text import _resolve_font

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)               # リポジトリルート
LAYERS_DIR = os.path.join(TESTS_DIR, "layers")  # レイヤー定義（testNN_*.py）
SNAPSHOT_DIR = os.path.join(TESTS_DIR, "snapshots")
PLUGINS_DIR = os.path.join(ROOT, "plugins")     # サンプルプラグイン（明示ロード用）

# ffprobe 不在でスナップショット結果が変わるテストだけを依存対象にする。
# モジュール全体を skip すると、影響を受けないフィルタ文字列の回帰まで見逃してしまう。
_FFPROBE_TESTS = frozenset({
    "test01", "test02", "test06", "test07", "test08", "test09", "test11",
    "test12", "test13", "test14", "test15", "test17", "test18", "test20",
    "test23", "test25", "test30", "test36", "test56", "test57", "test58",
    "test79", "test80", "test82", "test84", "test89",
})

_SNAPSHOT_ASSETS = {
    "test79": ("video/flowerbg_noaudio.mp4",),
    "test82": ("video/guitar_noaudio.mp4",),
    "test83": ("video/guitar_noaudio.mp4",),
    "test84": ("video/flowerbg_noaudio.mp4",),
}

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


def L(name):
    """レイヤーファイルを絶対パスで解決（cwd 非依存）"""
    return os.path.join(LAYERS_DIR, name)


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


def _skip_missing_snapshot_assets(name):
    """gitignore 対象の大容量素材が無いテストを正直に skip する。"""
    for relpath in _SNAPSHOT_ASSETS.get(name, ()):
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


def run_test(name, setup_fn, update=False):
    """スナップショットテストを実行。update=Trueならスナップショットを更新"""
    cmd = normalize_cmd(setup_fn())
    expected = load_snapshot(name)
    if expected is None or update:
        save_snapshot(name, cmd)
        print(f"  {name}: スナップショット{'更新' if expected else '作成'}")
        return True
    actual_cmp = _normalize_snapshot_comparison(name, cmd)
    expected_cmp = _normalize_snapshot_comparison(name, expected)
    if actual_cmp != expected_cmp:
        print(f"  {name}: 不一致!")
        for i, (a, b) in enumerate(zip(actual_cmp, expected_cmp)):
            if a != b:
                print(f"    [{i}] 期待: {b}")
                print(f"    [{i}] 実際: {a}")
        if len(actual_cmp) != len(expected_cmp):
            print(f"    長さ: 期待={len(expected_cmp)}, 実際={len(actual_cmp)}")
        return False
    print(f"  {name}: OK")
    return True


# --- テスト定義 ---

def setup_test01():
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="darkred")
    p.layer(L("test01_bg.py"), priority=0)
    p.layer(L("test01_oni.py"), priority=1)
    return p.render("test01.mp4", dry_run=True)

def setup_test02():
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test02_maku.py"), priority=0)
    p.layer(L("test02_cafe.py"), priority=1)
    return p.render("test02.mp4", dry_run=True)

def setup_test03():
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test03_bg.py"), priority=0)
    p.layer(L("test03_oni.py"), priority=1)
    p.layer(L("test03_virus.py"), priority=2)
    return p.render("test03.mp4", dry_run=True)

def setup_test04():
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="white")
    p.layer(L("test04_maku.py"), priority=0)
    p.layer(L("test04_cache_layer.py"), priority=1)
    return p.render("test04.mp4", dry_run=True)

def setup_test05():
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="green")
    p.layer(L("test05_bg.py"), priority=0)
    p.layer(L("test05_pop.py"), priority=1)
    return p.render("test05.mp4", dry_run=True)

def setup_test06():
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="olive")
    p.layer(L("test06_oni.py"), priority=0)
    return p.render("test06.mp4", dry_run=True)

def setup_test07():
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="navy")
    p.layer(L("test07_oni.py"), priority=0)
    p.layer(L("test07_cafe.py"), priority=1)
    p.layer(L("test07_virus.py"), priority=2)
    return p.render("test07.mp4", dry_run=True)

def setup_test08():
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="darkgreen")
    p.layer(L("test08_bg.py"), priority=0)
    p.layer(L("test08_pop.py"), priority=1)
    return p.render("test08.mp4", dry_run=True)

def setup_test09():
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="gray")
    p.layer(L("test09_oni.py"), priority=0)
    p.layer(L("test09_cafe.py"), priority=1)
    p.layer(L("test09_virus.py"), priority=2)
    p.layer(L("test09_pop.py"), priority=3)
    return p.render("test09.mp4", dry_run=True)

def setup_test10():
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="purple")
    p.layer(L("test10_maku.py"), priority=0)
    p.layer(L("test10_cache_layer.py"), priority=1)
    return p.render("test10.mp4", dry_run=True)

def setup_test11():
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="darkblue")
    p.layer(L("test11_maku.py"), priority=0)
    p.layer(L("test11_oni.py"), priority=1)
    return p.render("test11.mp4", dry_run=True)

def setup_test12():
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="darkslategray")
    p.layer(L("test12_sin_fade.py"), priority=0)
    p.layer(L("test12_lambda_scale.py"), priority=1)
    p.layer(L("test12_lambda_move.py"), priority=2)
    return p.render("test12.mp4", dry_run=True)


def setup_test13():
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test13_percent.py"), priority=0)
    return p.render("test13.mp4", dry_run=True)

def setup_test14():
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="darkblue")
    p.layer(L("test14_maku.py"), priority=0, cache="make")
    p.layer(L("test14_oni.py"), priority=1)
    result = p.render("test14.mp4", dry_run=True)
    return result

def setup_test15():
    """cache='use' テスト: ダミーキャッシュからの読み込み"""
    from scriptvedit.cache import _layer_cache_paths
    # キャッシュ鍵は解決済み総尺を含むため、まず同構成のプロジェクトを
    # dry_runして総尺を解決してからキャッシュパスを計算する
    probe = Project()
    probe.configure(width=1280, height=720, fps=30, background_color="darkblue")
    probe.layer(L("test14_maku.py"), priority=0)
    probe.layer(L("test14_oni.py"), priority=1)
    probe.render("test15_probe.mp4", dry_run=True)
    dummy_webm, dummy_json = _layer_cache_paths(L("test14_maku.py"), probe)
    os.makedirs(os.path.dirname(dummy_webm), exist_ok=True)
    # ダミーwebmファイル（空でよい、dry_runなので実行されない）
    with open(dummy_webm, "wb") as f:
        f.write(b"\x00")
    # anchors.json
    with open(dummy_json, "w", encoding="utf-8") as f:
        json.dump({"duration": 3.0, "anchors": {"curtain_done": 3.0}}, f)
    try:
        p2 = Project()
        p2.configure(width=1280, height=720, fps=30, background_color="darkblue")
        p2.layer(L("test14_maku.py"), priority=0, cache="use")
        p2.layer(L("test14_oni.py"), priority=1)
        return p2.render("test15.mp4", dry_run=True)
    finally:
        # ダミーファイル削除
        if os.path.exists(dummy_webm):
            os.unlink(dummy_webm)
        if os.path.exists(dummy_json):
            os.unlink(dummy_json)
        # クリーンアップ
        parent = os.path.dirname(dummy_webm)
        if os.path.exists(parent) and not os.listdir(parent):
            os.rmdir(parent)


def setup_test16():
    """音声ミックステスト: BGM(mp3) + 画像+SE"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test16_bgm.py"), priority=0)
    p.layer(L("test16_oni.py"), priority=1)
    return p.render("test16.mp4", dry_run=True)

def setup_test17():
    """AV splitテスト: 音声なし動画 + 音声のみ"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="darkgreen")
    p.layer(L("test17_video_split.py"), priority=0)
    p.layer(L("test17_audio.py"), priority=1)
    return p.render("test17.mp4", dry_run=True)

def setup_test18():
    """length()テスト: ffprobeで取得した長さを使用"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="gray")
    p.layer(L("test18_length.py"), priority=0)
    return p.render("test18.mp4", dry_run=True)


def setup_test19():
    """webクリップテスト: HTML→透明webm→合成"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test19_web.py"), priority=0)
    return p.render("test19.mp4", dry_run=True)


def setup_test20():
    """字幕/吹き出しテスト: subtitle+bubble+背景合成"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test20_bg.py"), priority=0)
    p.layer(L("test20_subtitles.py"), priority=1)
    p.layer(L("test20_bubble.py"), priority=2)
    return p.render("test20.mp4", dry_run=True)


def setup_test21():
    """図解テスト: diagram SVG図形+from/toアニメ+画像合成"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="darkslategray")
    p.layer(L("test21_diagram.py"), priority=0)
    p.layer(L("test21_overlay.py"), priority=1)
    return p.render("test21.mp4", dry_run=True)


def setup_test22():
    """チェックポイントキャッシュテスト"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test22_checkpoint.py"), priority=0)
    return p.render("test22.mp4", dry_run=True)


def setup_test23():
    """move保存テスト: resize(force) + move + scaleでmoveが消えないことを確認"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test23_move_preserve.py"), priority=0)
    return p.render("test23.mp4", dry_run=True)


def setup_test24():
    """video checkpointテスト: 動画のtransform-only → .webm拡張子"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test24_video_checkpoint.py"), priority=0)
    return p.render("test24.mp4", dry_run=True)


def setup_test25():
    """time()省略 → auto duration（加工後長: trim(3)反映で duration=3）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test25_video_no_time.py"), priority=0)
    return p.render("test25.mp4", dry_run=True)


def setup_test26():
    """morph_toテスト: 画像→画像のモーフィング"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test26_morph.py"), priority=0)
    return p.render("test26.mp4", dry_run=True)


def setup_test27():
    """rotate Transformテスト: 画像を30度回転（静的）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test27_rotate.py"), priority=0)
    return p.render("test27.mp4", dry_run=True)


def setup_test28():
    """rotate_to Effectテスト: 0→180度回転アニメーション + move保持"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test28_rotate_to.py"), priority=0)
    return p.render("test28.mp4", dry_run=True)


def setup_test29():
    """web + bakeable (scale/fade) テスト: web変換後にcheckpointが正しく動作"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test29_web_bakeable.py"), priority=0)
    return p.render("test29.mp4", dry_run=True)


def setup_test30():
    """sin scale中間最大padテスト: 11点サンプリングで正しいpadサイズ"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test30_sin_scale.py"), priority=0)
    return p.render("test30.mp4", dry_run=True)


def setup_test31():
    """crop Transformテスト"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test31_crop.py"), priority=0)
    return p.render("test31.mp4", dry_run=True)


def setup_test32():
    """pad Transformテスト"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test32_pad.py"), priority=0)
    return p.render("test32.mp4", dry_run=True)


def setup_test33():
    """blur Transformテスト"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test33_blur.py"), priority=0)
    return p.render("test33.mp4", dry_run=True)


def setup_test34():
    """eq Transformテスト"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test34_eq.py"), priority=0)
    return p.render("test34.mp4", dry_run=True)


def setup_test35():
    """wipe Effectテスト"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test35_wipe.py"), priority=0)
    return p.render("test35.mp4", dry_run=True)


def setup_test36():
    """zoom Effectテスト"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test36_zoom.py"), priority=0)
    return p.render("test36.mp4", dry_run=True)


def setup_test37():
    """color_shift Effectテスト"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test37_color_shift.py"), priority=0)
    return p.render("test37.mp4", dry_run=True)


def setup_test38():
    """subtitle_box Webテンプレートテスト"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test38_subtitle_box.py"), priority=0)
    return p.render("test38.mp4", dry_run=True)


def setup_test39():
    """chroma_key Effectテスト"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test39_chroma_key.py"), priority=0)
    return p.render("test39.mp4", dry_run=True)


def setup_test40():
    """vignette Effectテスト（strength=Expr → eval=frame）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test40_vignette.py"), priority=0)
    return p.render("test40.mp4", dry_run=True)


def setup_test41():
    """pixelize Effectテスト"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test41_pixelize.py"), priority=0)
    return p.render("test41.mp4", dry_run=True)


def setup_test42():
    """glow Effectテスト（split→gblur→blend=screen）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test42_glow.py"), priority=0)
    return p.render("test42.mp4", dry_run=True)


def setup_test43():
    """lut Effectテスト（lut3d + LUTファイル）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test43_lut.py"), priority=0)
    return p.render("test43.mp4", dry_run=True)


def setup_test44():
    """glitch Effectテスト（rgbashift+noise、間欠enable）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test44_glitch.py"), priority=0)
    return p.render("test44.mp4", dry_run=True)


def setup_test45():
    """perspective_warp Effectテスト"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test45_perspective.py"), priority=0)
    return p.render("test45.mp4", dry_run=True)


def setup_test46():
    """lens Effectテスト（lenscorrection）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test46_lens.py"), priority=0)
    return p.render("test46.mp4", dry_run=True)


def setup_test47():
    """ken_burns Effectテスト（動的scale+crop）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test47_ken_burns.py"), priority=0)
    return p.render("test47.mp4", dry_run=True)


def setup_test48():
    """drop_shadow Effectテスト（split→色付け+gblur→overlay）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test48_drop_shadow.py"), priority=0)
    return p.render("test48.mp4", dry_run=True)


def setup_test49():
    """outline Effectテスト（dilationベースの縁取り）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test49_outline.py"), priority=0)
    return p.render("test49.mp4", dry_run=True)


def setup_test50():
    """slideshowテスト（xfade連結の合成Object）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test50_slideshow.py"), priority=0)
    return p.render("test50.mp4", dry_run=True)


def setup_test51():
    """transitionテスト（2Objectのxfade合成）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test51_transition.py"), priority=0)
    return p.render("test51.mp4", dry_run=True)


def setup_test52():
    """text Effect: drawtextエスケープ + x/y/size/alphaアニメ + box"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test52_text.py"), priority=0)
    return p.render("test52.mp4", dry_run=True)


def setup_test53():
    """typewriter: 1文字ずつdrawtext + enable"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test53_typewriter.py"), priority=0)
    return p.render("test53.mp4", dry_run=True)


def setup_test54():
    """counter: %{eif}数値カウントアップ + format"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test54_counter.py"), priority=0)
    return p.render("test54.mp4", dry_run=True)


def setup_test55():
    """subtitles: SRT字幕 + force_style"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test55_subtitles.py"), priority=0)
    return p.render("test55.mp4", dry_run=True)


def setup_test56():
    """audio_viz: showwaves可視化（キャッシュ生成物）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test56_audio_viz.py"), priority=0)
    return p.render("test56.mp4", dry_run=True)


def setup_test57():
    """audio_sequence + sfx（キャッシュ生成物） + normalize_audio(loudnorm)"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.normalize_audio(-14)
    p.layer(L("test57_audio_bake.py"), priority=0)
    return p.render("test57.mp4", dry_run=True)


def setup_test58():
    """loop(aloop) + duck_under(sidechaincompress)"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test58_audio_fx.py"), priority=0)
    return p.render("test58.mp4", dry_run=True)


def setup_test59():
    """move_along / path_bezier / throw / look_at のパスアニメ"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test59_paths.py"), priority=0)
    return p.render("test59.mp4", dry_run=True)


def setup_test60():
    """explode_to: 粒子飛散（morph同機構でmkvキャッシュ生成物）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test60_explode.py"), priority=0)
    return p.render("test60.mp4", dry_run=True)


def setup_test61():
    """assemble_from: 粒子集合（source消費+mkvキャッシュ生成物）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test61_assemble.py"), priority=0)
    return p.render("test61.mp4", dry_run=True)


def setup_test62():
    """group（一括適用）+ grid/tile（グリッド複製）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test62_group_grid.py"), priority=0)
    return p.render("test62.mp4", dry_run=True)


def setup_test63():
    """scene: シーンの順次配置（シーン相対時刻）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test63_scene.py"), priority=0)
    return p.render("test63.mp4", dry_run=True)


def setup_test64():
    """perlin ノイズによる手ブレ風 move"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test64_perlin.py"), priority=0)
    return p.render("test64.mp4", dry_run=True)


def setup_test65():
    """marker + チャプター（FFMETADATA埋め込み）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.marker(0, "イントロ")
    p.marker(2.0, "本編")
    p.marker(4.5, "まとめ")
    p.layer(L("test65_chapters.py"), priority=0)
    return p.render("test65.mp4", dry_run=True)


def setup_test66():
    """部分レンダ（時間窓 start=1.5, end=4.0）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test66_window.py"), priority=0)
    return p.render("test66.mp4", dry_run=True, start=1.5, end=4.0)


def setup_test67():
    """GIF出力（palettegen/paletteuse を1グラフで実行、音声なし）"""
    p = Project()
    p.configure(width=640, height=360, fps=15, background_color="black")
    p.layer(L("test67_layer.py"), priority=0)
    return p.render("test67.gif", dry_run=True)


def setup_test68():
    """ドラフトレンダ（解像度半分・ultrafast・crf28）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test67_layer.py"), priority=0)
    return p.render("test68.mp4", dry_run=True, draft=True)


def setup_test69():
    """プリセット square（1080x1080）"""
    p = Project()
    p.configure(preset="square", background_color="black")
    p.layer(L("test67_layer.py"), priority=0)
    return p.render("test69.mp4", dry_run=True)


def setup_test70():
    """透過webm出力（libvpx-vp9 yuva420p + 透明背景）"""
    p = Project()
    p.configure(width=640, height=360, fps=30, background_color="black")
    p.layer(L("test67_layer.py"), priority=0)
    return p.render("test70.webm", dry_run=True, alpha=True)


def setup_test71():
    """連番PNG出力（out_%05d.png / png rgba）"""
    p = Project()
    p.configure(width=640, height=360, fps=15, background_color="black")
    p.layer(L("test67_layer.py"), priority=0)
    return p.render("test71.png", dry_run=True)


def setup_test72():
    """アニメーションWebP出力"""
    p = Project()
    p.configure(width=640, height=360, fps=15, background_color="black")
    p.layer(L("test67_layer.py"), priority=0)
    return p.render("test72.webp", dry_run=True)


def setup_test73():
    """プリセット + 個別上書き（shorts の後に fps=24 で上書き）"""
    p = Project()
    p.configure(preset="shorts", fps=24, background_color="navy")
    p.layer(L("test67_layer.py"), priority=0)
    return p.render("test73.mp4", dry_run=True)


def setup_test74():
    """from_project: ネストコンポジション（サブProject→透過webm素材化）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test74_nested.py"), priority=0)
    return p.render("test74.mp4", dry_run=True)


def setup_test75():
    """mask: 画像輝度をアルファとして乗算（movie= + scale2ref + alphamerge）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test75_mask.py"), priority=0)
    return p.render("test75.mp4", dry_run=True)


def setup_test76():
    """mask_wipe: マスク輝度しきい値ワイプ（既定線形 + Expr進行）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test76_mask_wipe.py"), priority=0)
    return p.render("test76.mp4", dry_run=True)


def setup_test77():
    """opacity: 定数(colorchannelmixer) + Expr(geq live)"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test77_opacity.py"), priority=0)
    return p.render("test77.mp4", dry_run=True)


def setup_test78():
    """blend_mode: screen/multiply（blend+maskedmerge合成経路）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test78_blend_mode.py"), priority=0)
    return p.render("test78.mp4", dry_run=True)


def setup_test79():
    """pip プリセット + rounded 角丸"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test79_pip_rounded.py"), priority=0)
    return p.render("test79.mp4", dry_run=True)


def setup_test80():
    """blur_background_fill: ぼかし背景敷き（キャンバス全面化）"""
    p = Project()
    p.configure(width=720, height=1280, fps=30, background_color="black")
    p.layer(L("test80_blur_bg_fill.py"), priority=0)
    return p.render("test80.mp4", dry_run=True)


def setup_test81():
    """progress_bar: 動画全体の進行バー（geq + T/総尺）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test81_progress_bar.py"), priority=0)
    return p.render("test81.mp4", dry_run=True)


def setup_test82():
    """speed: 再生速度（setpts + length()反映 + atempo自動追従）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test82_speed.py"), priority=0)
    return p.render("test82.mp4", dry_run=True)


def setup_test83():
    """reverse + freeze_frame: 逆再生と一時停止（時間系liveサブグラフ）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test83_reverse_freeze.py"), priority=0)
    return p.render("test83.mp4", dry_run=True)


def setup_test84():
    """video_sequence: 動画クリップのxfade連結（キャッシュ生成物）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test84_video_sequence.py"), priority=0)
    return p.render("test84.mp4", dry_run=True)


def setup_test85():
    """narrate: TTS(scriptvedit.tts をモック化)+字幕の同時生成・タイムライン同期
    （VOICEVOX不要。tts.tts/tts_duration を差し替えて実行する）"""
    from scriptvedit import tts as svtts
    orig_tts, orig_dur = svtts.tts, svtts.tts_duration
    fake_wav = asset("audio/bgm_loop.mp3")

    def _fake_tts(text, *, speaker=1, speed=1.0, pitch=0.0, **kw):
        return fake_wav

    def _fake_dur(path):
        return 2.5

    svtts.tts = _fake_tts
    svtts.tts_duration = _fake_dur
    try:
        p = Project()
        p.configure(width=1280, height=720, fps=30, background_color="black")
        p.layer(L("test85_narrate.py"), priority=0)
        return p.render("test85.mp4", dry_run=True)
    finally:
        svtts.tts = orig_tts
        svtts.tts_duration = orig_dur


def setup_test86():
    """karaoke: ASS \\kタグ字幕（均等割り+word_durations明示）をsubtitlesフィルタで合成"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test86_karaoke.py"), priority=0)
    return p.render("test86.mp4", dry_run=True)


def setup_test87():
    """slide: HTMLスライドのページ切替規約（window.showSlide版 / id="page-N"版）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test87_slide.py"), priority=0)
    return p.render("test87.mp4", dry_run=True)


def setup_test88():
    """プラグイン: neon_glow(split+gblur+blend複合) + scanline(liveアニメ)"""
    load_plugins(PLUGINS_DIR)
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test88_plugin_neon.py"), priority=0)
    return p.render("test88.mp4", dry_run=True)


def setup_test89():
    """プラグイン: photo_frame の pad 拡張が overlay 中央配置(pad_size)に反映される"""
    load_plugins(PLUGINS_DIR)
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test89_plugin_frame.py"), priority=0)
    return p.render("test89.mp4", dry_run=True)


def setup_test90():
    """プラグイン: test/plugins/ の自動読込（bakeable / live 両方）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test90_plugin_autoload.py"), priority=0)
    return p.render("test90.mp4", dry_run=True)


def setup_test91():
    """formula: KaTeX数式の透過PNG化（画像Objectとしてfade/moveがそのまま効く）"""
    p = Project()
    p.configure(width=1280, height=720, fps=30, background_color="black")
    p.layer(L("test91_formula.py"), priority=0)
    return p.render("test91.mp4", dry_run=True)


ALL_TESTS = [
    ("test01", setup_test01),
    ("test02", setup_test02),
    ("test03", setup_test03),
    ("test04", setup_test04),
    ("test05", setup_test05),
    ("test06", setup_test06),
    ("test07", setup_test07),
    ("test08", setup_test08),
    ("test09", setup_test09),
    ("test10", setup_test10),
    ("test11", setup_test11),
    ("test12", setup_test12),
    ("test13", setup_test13),
    ("test14", setup_test14),
    ("test15", setup_test15),
    ("test16", setup_test16),
    ("test17", setup_test17),
    ("test18", setup_test18),
    ("test19", setup_test19),
    ("test20", setup_test20),
    ("test21", setup_test21),
    ("test22", setup_test22),
    ("test23", setup_test23),
    ("test24", setup_test24),
    ("test25", setup_test25),
    ("test26", setup_test26),
    ("test27", setup_test27),
    ("test28", setup_test28),
    ("test29", setup_test29),
    ("test30", setup_test30),
    ("test31", setup_test31),
    ("test32", setup_test32),
    ("test33", setup_test33),
    ("test34", setup_test34),
    ("test35", setup_test35),
    ("test36", setup_test36),
    ("test37", setup_test37),
    ("test38", setup_test38),
    ("test39", setup_test39),
    ("test40", setup_test40),
    ("test41", setup_test41),
    ("test42", setup_test42),
    ("test43", setup_test43),
    ("test44", setup_test44),
    ("test45", setup_test45),
    ("test46", setup_test46),
    ("test47", setup_test47),
    ("test48", setup_test48),
    ("test49", setup_test49),
    ("test50", setup_test50),
    ("test51", setup_test51),
    ("test52", setup_test52),
    ("test53", setup_test53),
    ("test54", setup_test54),
    ("test55", setup_test55),
    ("test56", setup_test56),
    ("test57", setup_test57),
    ("test58", setup_test58),
    ("test59", setup_test59),
    ("test60", setup_test60),
    ("test61", setup_test61),
    ("test62", setup_test62),
    ("test63", setup_test63),
    ("test64", setup_test64),
    ("test65", setup_test65),
    ("test66", setup_test66),
    ("test67", setup_test67),
    ("test68", setup_test68),
    ("test69", setup_test69),
    ("test70", setup_test70),
    ("test71", setup_test71),
    ("test72", setup_test72),
    ("test73", setup_test73),
    ("test74", setup_test74),
    ("test75", setup_test75),
    ("test76", setup_test76),
    ("test77", setup_test77),
    ("test78", setup_test78),
    ("test79", setup_test79),
    ("test80", setup_test80),
    ("test81", setup_test81),
    ("test82", setup_test82),
    ("test83", setup_test83),
    ("test84", setup_test84),
    ("test85", setup_test85),
    ("test86", setup_test86),
    ("test87", setup_test87),
    ("test88", setup_test88),
    ("test89", setup_test89),
    ("test90", setup_test90),
    ("test91", setup_test91),
]



# --- pytest 版（本物の assert で検証する） ---

def _snapshot_update_requested(request):
    """--snapshot-update が指定されていればスナップショットを再生成する"""
    try:
        return bool(request.config.getoption("--snapshot-update"))
    except Exception:
        return False


@pytest.mark.parametrize("name,setup_fn", ALL_TESTS, ids=[n for n, _ in ALL_TESTS])
def test_snapshot(name, setup_fn, request):
    """dry_run で生成した ffmpeg コマンドがスナップショットと一致すること"""
    if name in _FFPROBE_TESTS and shutil.which("ffprobe") is None:
        pytest.skip("ffprobe が見つからないためスキップします")
    _skip_missing_snapshot_assets(name)
    if name in {"test52", "test53", "test54", "test85"}:
        try:
            _resolve_font(None)
        except FileNotFoundError as exc:
            pytest.skip(str(exc))
    cmd = normalize_cmd(setup_fn())
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


if __name__ == "__main__":
    update = "--update" in sys.argv
    print("スナップショットテスト" + (" (更新モード)" if update else ""))
    passed = 0
    failed = 0
    for name, fn in ALL_TESTS:
        if run_test(name, fn, update=update):
            passed += 1
        else:
            failed += 1
    print(f"\n結果: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
