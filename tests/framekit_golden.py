# -*- coding: utf-8 -*-
"""図解アニメの金型テスト（版定数の上げ忘れを捕まえる）。

図解ファクトリ（regex_view・slots・flow_graph・globe・framekit 自身など）は、鍵を
「params と版定数」で作る。描き方を変えたのに版定数を上げ忘れると、古い鍵の動画が
キャッシュに命中し続けて直らない。そこで代表的なコマを PNG の金型と突き合わせ、
絵が変わったら「版を上げて金型を作り直す」ことを強制する。

    from framekit_golden import assert_golden
    def test_golden_xxx(request):
        img = fk.draw_frame(obj, 12)                      # uint8 の HxWx4
        assert_golden(request, "regex_view", "backtrack", img, ver=_VER,
                      font_ffp=sprite.fonts[0].ffp)        # 文字を描くなら必須

保存場所: tests/golden/<kind>/<name>.png と、同名の .json（ver・font_ffp・PIL の版・寸法）。
判定:
  - 金型が無ければ失敗（pytest --golden-update で作るよう案内する）
  - ver が金型と違えば失敗（版を上げたら金型も作り直す）
  - フォントが金型と違う環境は skip（理由は「金型と別のフォントの環境」。CI の
    skip 許可リストの「フォント」に合わせてある）
  - 事前乗算の画素の差（0..255）の平均・最大が閾値を超えたら失敗
    （意図した変更なら版定数を上げて --golden-update）
--golden-update でも、版を上げずに絵だけ変えたものは書き換えない: 保存済みの ver が
いまの ver と同じで、フォントも同じで、差が閾値を超えるなら失敗させる（「版を上げてから
--golden-update」）。版を上げ忘れたまま金型だけ作り直して通す、という抜け道を塞ぐ。
金型が無いとき・版が違うとき・フォントが違うときは書き換える。差が閾値の内のときは
書き換えない（版を上げないままの小さな変化が金型に積もっていかないように）。
これが守るのは scriptvedit 本体の図（framekit とその上の図解ファクトリ）の版定数で、
利用者がレイヤーで自作する図の ver までは見張れない（自作の図は自分で ver を上げる）。
金型は 320×180 程度。1機能あたり3〜6枚。各担当は自分の tests/golden/<kind>/ だけを作る。
失敗したときの実際の絵は tests/output/golden/<kind>/<name>.actual.png に置く。
"""
import json
import os

import pytest

_ROOT = os.path.dirname(os.path.abspath(__file__))
GOLDEN_DIR = os.path.join(_ROOT, "golden")
_FAIL_DIR = os.path.join(_ROOT, "output", "golden")


def _as_rgba8(img):
    """PIL.Image か uint8 の HxWx4 を numpy の uint8 HxWx4 にする"""
    np = pytest.importorskip("numpy", reason="numpy が無い環境")
    if hasattr(img, "convert") and not hasattr(img, "shape"):
        img = np.asarray(img.convert("RGBA"), dtype=np.uint8)
    arr = np.asarray(img)
    if arr.ndim != 3 or arr.shape[2] != 4 or arr.dtype != np.uint8:
        raise TypeError(
            f"assert_golden: 絵は uint8 の HxWx4（RGBA）で渡してください: "
            f"{arr.dtype} {arr.shape}")
    return arr


def _premul(np, arr):
    """事前乗算（0..255 の float）。α=0 の画素の色の違いを差に数えない"""
    f = arr.astype(np.float64)
    f[..., :3] *= f[..., 3:4] / 255.0
    return f


def _pil_version():
    try:
        import PIL
        return PIL.__version__
    except ImportError:
        return None


def _write(arr, png_path, meta):
    Image = pytest.importorskip("PIL.Image", reason="Pillow が無い環境")
    os.makedirs(os.path.dirname(png_path), exist_ok=True)
    Image.fromarray(arr, "RGBA").save(png_path, format="PNG")
    with open(png_path[:-len(".png")] + ".json", "w", encoding="utf-8", newline="\r\n") as f:
        f.write(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def assert_golden(request, kind, name, img, *, ver, font_ffp=None, tol_mean=1.0, tol_max=48):
    """img を金型 tests/golden/<kind>/<name>.png と突き合わせる。

    ver: その図の描画の版定数（金型の .json に控え、違えば失敗）。
    font_ffp: 文字を描く図はフォントの内容指紋（FontRef.ffp）。金型と違う環境は skip。
    tol_mean / tol_max: 事前乗算の画素の差（0..255）の平均と最大の許容値。
    pytest --golden-update で金型を作り直す（差分を目視してから使うこと）。
    """
    np = pytest.importorskip("numpy", reason="numpy が無い環境")
    Image = pytest.importorskip("PIL.Image", reason="Pillow が無い環境")
    arr = _as_rgba8(img)
    png_path = os.path.join(GOLDEN_DIR, kind, f"{name}.png")
    json_path = png_path[:-len(".png")] + ".json"
    meta = {"ver": str(ver), "font_ffp": font_ffp, "pil": _pil_version(),
            "size": [int(arr.shape[1]), int(arr.shape[0])]}
    update = request.config.getoption("--golden-update")
    exists = os.path.isfile(png_path) and os.path.isfile(json_path)
    if not exists:
        if update:
            _write(arr, png_path, meta)
            return
        pytest.fail(
            f"金型がありません: tests/golden/{kind}/{name}.png。"
            f"絵を目視してから pytest --golden-update で作ってください")
    with open(json_path, encoding="utf-8") as f:
        stored = json.load(f)
    if stored.get("ver") != str(ver) or stored.get("font_ffp") != font_ffp:
        if update:          # 版を上げた・フォントの違う環境で作り直す
            _write(arr, png_path, meta)
            return
        if stored.get("ver") != str(ver):
            pytest.fail(
                f"金型 tests/golden/{kind}/{name}.png の版（{stored.get('ver')}）が"
                f"いまの版（{ver}）と違います。絵を目視してから pytest --golden-update で"
                f"金型を作り直してください")
        pytest.skip("金型と別のフォントの環境")
    with Image.open(png_path) as im:
        want = np.asarray(im.convert("RGBA"), dtype=np.uint8)
    if want.shape != arr.shape:
        mean = peak = float("inf")
    else:
        diff = np.abs(_premul(np, arr) - _premul(np, want))
        mean, peak = float(diff.mean()), float(diff.max())
    if mean > tol_mean or peak > tol_max:
        actual = _save_actual(arr, kind, name)
        what = ("と寸法が違います（金型 "
                f"{want.shape[1]}x{want.shape[0]}、いま {arr.shape[1]}x{arr.shape[0]}）"
                if want.shape != arr.shape else
                f"と絵が違います（差の平均 {mean:.3f} > {tol_mean} か 最大 {peak:.0f} > {tol_max}）")
        if update:
            pytest.fail(
                f"金型 tests/golden/{kind}/{name}.png {what}が、版（{ver}）が金型と同じなので"
                f"書き換えません。描き方を変えたなら版定数を上げてから --golden-update。"
                f"いまの絵: {actual}")
        pytest.fail(
            f"金型 tests/golden/{kind}/{name}.png {what}。"
            f"意図した変更なら版定数を上げて --golden-update。いまの絵: {actual}")
    # 閾値の内の差（環境の揺れ）は --golden-update でも書き換えない（少しずつの変化が
    # 版を上げないまま金型に積もっていくのを防ぐ）


def _save_actual(arr, kind, name):
    path = os.path.join(_FAIL_DIR, kind, f"{name}.actual.png")
    try:
        from PIL import Image
        os.makedirs(os.path.dirname(path), exist_ok=True)
        Image.fromarray(arr, "RGBA").save(path, format="PNG")
    except Exception:
        return "(保存できませんでした)"
    return path
