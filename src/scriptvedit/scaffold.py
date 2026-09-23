# -*- coding: utf-8 -*-
"""`scriptvedit new <path>` — 動画プロジェクトの雛形生成

生成物はそのまま `python main.py` でレンダできる（minimal は素材ゼロ・追加依存ゼロ。
explainer テンプレートの formula() だけは Playwright + Chromium が必要:
`pip install "scriptvedit[web]" && playwright install chromium`）。

  <path>/
    main.py            構成定義（configure / layer / render）
    layers/            1ファイル = 1レイヤー
    assets/            素材（images/ audio/ …。_imported/ は共有ライブラリの自動コピー先）
    plugins/           カスタムエフェクト（@effect_plugin、自動読込）
    output/            出力
    README.md / .gitignore
"""
import os

_MAIN_PY = '''# -*- coding: utf-8 -*-
"""{name} — 構成定義（レイヤーの読み込み順と出力設定だけを書く）

    python main.py            # output/{name}.mp4 を生成
    python main.py out.mp4    # 出力先を指定
"""
import os
import sys

from scriptvedit import *

if __name__ == "__main__":
    # cwd 非依存にする（どこから起動しても assets/ と layers/ を正しく解決する）
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    p = Project()
    p.configure(width={width}, height={height}, fps={fps}, background_color="{bg}")

{layers}
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join("output", "{name}.mp4")
    p.render(out)
'''

_INTRO_PY = '''from scriptvedit import *
# 1ファイル = 1レイヤー。先頭は必ず `from scriptvedit import *`。
# ここで作った Object は Project へ自動登録される。
#
# 文字には必ず border（縁取り）・shadow（影）・box（下地）のどれかを付ける。
# 無いと背景に溶けて読めず、p.audit() が text-no-decoration の warning を出す。

title = text("{name}", x=0.5, y=0.45, size={title_size}, color="white",
             border={big_border}, border_color="black")
title.time(3) <= fade(lambda u: clip(u * 3, 0, 1) * clip((1 - u) * 3, 0, 1))

sub = text("scriptvedit で作った動画", x=0.5, y=0.6, size={sub_size}, color="#9fd0ff",
           border={small_border}, border_color="black")
sub.time(3) <= fade(lambda u: clip(u * 2 - 0.4, 0, 1))
'''

_EXPLAINER_INTRO_PY = '''from scriptvedit import *
# タイトル（解説動画のオープニング）
#
# 文字には必ず border（縁取り）・shadow（影）・box（下地）のどれかを付ける。
# 無いと背景に溶けて読めず、p.audit() が text-no-decoration の warning を出す。

title = text("{name}", x=0.5, y=0.42, size={title_size}, color="white",
             border={big_border}, border_color="black")
title.time(3) <= fade(lambda u: clip(u * 3, 0, 1) * clip((1 - u) * 3, 0, 1))

# time(..., name="intro") で "intro.start" / "intro.end" のアンカーが張られる。
# **タイムラインのカーソルはレイヤーが変わると 0 秒に戻る**ので、後続レイヤー
# （body.py）はこのアンカーを使って「イントロの後ろ」へ自分を置く。
sub = text("3分でわかる解説", x=0.5, y=0.58, size={sub_size}, color="#9fd0ff",
           border={small_border}, border_color="black")
sub.time(3, name="intro") <= fade(lambda u: clip(u * 2 - 0.4, 0, 1))
'''

_EXPLAINER_BODY_PY = '''from scriptvedit import *
# 本編: 数式（formula）+ 字幕（text）。formula は KaTeX 同梱でオフライン動作する。
# （formula には playwright が必要: pip install "scriptvedit[web]" && playwright install chromium）
#
# **レイヤーが変わるとタイムラインのカーソルは 0 秒に戻る。**
# この1行が無いと本編が 0 秒から始まり、intro レイヤーのタイトルと
# 同じ時刻・同じ位置に重なって表示される。intro.py が張ったアンカー
# "intro.end" までカーソルを進めてから本編を置く。
pause.until("intro.end")

eq = formula(r"P(A \\cup B) = P(A) + P(B) - P(A \\cap B)", size={eq_size}, color="white")
eq.time(4) <= fade(lambda u: clip(u * 4, 0, 1)) & move(x=0.5, y=0.4, anchor="center")

# こちらにも "body.end" のアンカーを張る（bgm.py が尺合わせに使う）
cap = text("包除原理: 重なりを引く", x=0.5, y=0.75, size={cap_size}, color="#ffd166",
           border={big_border}, border_color="black")
cap.time(4, name="body") <= fade(lambda u: clip(u * 4, 0, 1) * clip((1 - u) * 4, 0, 1))
'''

_EXPLAINER_BGM_PY = '''from scriptvedit import *
# BGM レイヤー。assets/audio/bgm.mp3 を置くと自動で乗る（無ければ無音のまま）。
# 環境変数 SCRIPTVEDIT_ASSETS（共有素材ライブラリ）にあれば、asset() が
# assets/_imported/ へ取り込んでそのパスを返す。
# ※ 取り込みが走るのは must_exist=True（既定）のときだけなので、
#    「無ければ無音」は must_exist=False ではなく例外で判定する。

try:
    bgm_path = asset("audio/bgm.mp3")
except FileNotFoundError:
    bgm_path = None  # プロジェクトにも共有ライブラリにも無い → BGM 無しでレンダする

if bgm_path:
    bgm = Object(bgm_path)
    # avolume は定数なら固定音量、式ならフェード（音量とフェードは同じ1段）
    # until("body.end") で本編の終わりまで鳴らす（ここでもカーソルは 0 秒から）
    bgm.until("body.end") <= loop() & avolume(lambda u: 0.5 * clip(u * 8, 0, 1))
'''

_README = '''# {name}

scriptvedit（Python DSL → FFmpeg）で作る動画プロジェクト。

## レンダ

```bash
cd {name}
python main.py                 # output/{name}.mp4
python main.py out.mp4         # 出力先を指定
python -m scriptvedit watch main.py   # 変更を監視して自動再レンダ
```

## 依存

- コアは Python 標準ライブラリ + FFmpeg のみ（minimal テンプレートは追加依存ゼロ）。
- `formula()`（explainer テンプレートの数式レンダ）は Playwright と Chromium が必要:

```bash
pip install "scriptvedit[web]"
playwright install chromium
```

## 構成

```
main.py      構成定義（画面設定・レイヤー順・出力）
layers/      1ファイル = 1レイヤー（priority の数字が小さいほど奥）
assets/      素材（images/ audio/ …）
plugins/     カスタムエフェクト（@effect_plugin。自動読込）
output/      出力（git 管理外）
```

## 素材の置き方

`assets/` 配下に置き、レイヤーからは `asset("images/logo.png")` で参照する
（cwd 非依存の絶対パスになる）。

共有素材ライブラリを使う場合は環境変数 `SCRIPTVEDIT_ASSETS` に探索パスを設定する
（複数可・`;` 区切り）:

```
set SCRIPTVEDIT_ASSETS=C:\\path\\to\\shared\\_media
```

`asset("bgm/xxx.mp3")` の解決順は

1. `assets/bgm/xxx.mp3`（手で置いた素材が最優先）
2. `assets/_imported/bgm/xxx.mp3`（過去に共有ライブラリから自動コピーしたもの）
3. `SCRIPTVEDIT_ASSETS` の各パス → 見つかれば **2 の場所へコピー**してそのパスを返す

コピーが残るのでプロジェクトは自己完結する（共有ライブラリが無い環境でもレンダできる）。
キャッシュ鍵は内容ハッシュなので、コピーでパスが変わっても再レンダは起きない。

## カスタムエフェクト

`plugins/*.py` に `@effect_plugin` で書くと自動読込され、レイヤーで使える。
雛形は `python -m scriptvedit describe --format md` の「プラグイン」節にある。
'''

_GITIGNORE = '''output/
__cache__/
assets/_imported/
__pycache__/
*.pyc
'''

_PLUGINS_README = '''# plugins/

このディレクトリの `*.py` は自動読込され、レイヤーから `from scriptvedit import *`
だけで使えるようになる（コアを編集せずにエフェクトを足す場所）。

```python
# plugins/my_glow.py
from scriptvedit import effect_plugin

@effect_plugin("my_glow", bakeable=True, category="視覚効果",
               params={"radius": {"type": "number", "default": 10, "min": 0, "max": 200}})
def build_my_glow(params, ctx):
    """自作グロー（この1行目が describe の要約になる）"""
    return [f"gblur=sigma={params['radius']}"]
```

→ レイヤーで `obj <= my_glow(radius=20)`
'''

TEMPLATES = ("minimal", "explainer")

# 雛形の文字サイズ・縁取りを決めた基準解像度（この比率で実解像度へ合わせる）
_BASE_WIDTH, _BASE_HEIGHT = 1280, 720


def _text_metrics(width, height, texts=None):
    """雛形の文字サイズ・縁取り太さを出力解像度に合わせて決める。

    雛形は「生成直後に `p.audit()` の warning がゼロ」を満たす必要があるが、
    audit の2つのルールは**別々の軸**を見るので、単純な1つの比率では両立しない:

    - `text-too-small` … 下限は **高さ基準**（`_TEXT_MIN_PX_1080 * height/1080`）
    - `text-overflow`  … 上限は **幅基準**（推定描画幅が safe area を超えないこと）

    短辺比率だけで縮めていた頃は、16:9 より縦長（ショート/リール/正方形）で
    「下限は伸びるのに文字は縮む」ので必ず衝突した
    （実測: `--width 1080 --height 1920` の雛形が text-too-small で warning）。

    そこで基準比率で出した値を **audit の下限へ引き上げ、上限へ引き下げる**。
    上限の計算には audit 自身の推定器を使うので、閾値が変わっても勝手に追従する。
    下限が上限を上回る（文字列が長すぎて読めるサイズでは収まらない）ときは
    **上限を優先**する — はみ出して読めないより小さい方がまだ読める。
    """
    import math as _math

    from scriptvedit.audit import (
        _OVERFLOW_TOLERANCE, _SAFE_AREA_RATIO, _TEXT_MIN_PX_1080,
        _estimated_text_width)

    scale = min(width / float(_BASE_WIDTH), height / float(_BASE_HEIGHT))
    # audit の判定は `size < min_px` / `est > safe_w` の厳密比較なので、
    # 下限は切り上げ・上限は切り捨てにしないと 1px 差で warning になる。
    lo_bound = _math.ceil(_TEXT_MIN_PX_1080 * (height / 1080.0))
    safe_w = width * _SAFE_AREA_RATIO * _OVERFLOW_TOLERANCE
    texts = texts or {}

    def px(base, lo, key=None):
        low = max(lo, lo_bound)
        high = None
        content = texts.get(key)
        if content:
            # size=100 での推定幅から、safe area に収まる最大 size を逆算する
            est100 = _estimated_text_width(content, 100.0)
            if est100 > 0:
                high = int(safe_w / est100 * 100.0)
        size = max(low, int(round(base * scale)))
        if high is not None:
            # 下限が上限を上回る（文字列が長すぎる）ときは上限を優先する
            size = min(size, high)
        # lo は「潰れて読めない/ffmpeg が受け付けない」を避けるための絶対下限
        return max(lo, size)

    return {
        "title_size": px(72, 12, "title"), "sub_size": px(32, 8, "sub"),
        "eq_size": px(56, 10, "eq"), "cap_size": px(36, 8, "cap"),
        "big_border": max(1, int(round(3 * scale))),
        "small_border": max(1, int(round(2 * scale))),
    }


def _write(path, content, backups=None):
    """雛形ファイルを書き出す。既存ファイルは .bak へ退避してから上書きする。

    force=True での再生成時にユーザーの編集済みファイルを黙って消さないための保護。
    内容が同一なら何もしない。退避したパスは backups リストへ追記する。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8", newline="") as f:
                old = f.read()
        except (OSError, UnicodeDecodeError):
            old = None
        if old is not None and old.replace("\r\n", "\n") == content:
            return  # 同一内容 → 触らない（.bak も作らない）
        bak = path + ".bak"
        os.replace(path, bak)  # 既存を退避（前回の .bak は上書き）
        if backups is not None:
            backups.append(bak)
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(content)


def new_project(path, *, template="minimal", force=False, width=1280, height=720,
                fps=30, quiet=False):
    """動画プロジェクトの雛形を生成し、生成したディレクトリの絶対パスを返す。

    path: 生成先ディレクトリ（既存で空でなければエラー。force=True で許可）
    template: "minimal" / "explainer"（数式・字幕・BGM 入り）
    """
    if template not in TEMPLATES:
        raise ValueError(
            f"scriptvedit new: 未知のテンプレート {template!r}"
            f"（使えるのは {', '.join(TEMPLATES)}）")
    # width/height/fps: 正の整数（0や負で生成した main.py はレンダ時に FFmpeg が失敗する）
    for key, v in (("width", width), ("height", height), ("fps", fps)):
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise ValueError(
                f"scriptvedit new: --{key} は正の整数で指定してください: {v!r}")
    root = os.path.abspath(path)
    if os.path.exists(root) and not os.path.isdir(root):
        raise ValueError(f"scriptvedit new: ディレクトリではありません: {root}")
    if os.path.isdir(root) and os.listdir(root) and not force:
        raise ValueError(
            f"scriptvedit new: 生成先が空ではありません: {root}\n"
            "上書き事故を防ぐため中断しました。--force で強制生成できます。")
    name = os.path.basename(root.rstrip("\\/")) or "project"

    # 実際に雛形へ埋め込む文字列を渡す（幅の上限は文字列長で決まるため）。
    # 文言を変えたらここも合わせること。
    metrics = _text_metrics(width, height, texts={
        "title": name,
        "sub": ("3分でわかる解説" if template == "explainer"
                else "scriptvedit で作った動画"),
        "cap": "包除原理: 重なりを引く",
    })

    if template == "explainer":
        layer_files = {
            "intro.py": _EXPLAINER_INTRO_PY.format(name=name, **metrics),
            "body.py": _EXPLAINER_BODY_PY.format(**metrics),
            "bgm.py": _EXPLAINER_BGM_PY,
        }
        layers_src = (
            '    p.layer(os.path.join("layers", "intro.py"), priority=1)\n'
            '    p.layer(os.path.join("layers", "body.py"), priority=2)\n'
            '    p.layer(os.path.join("layers", "bgm.py"), priority=0)  # 数字が小さいほど奥\n'
            "\n")
        bg = "#0d1b2a"
    else:
        layer_files = {"intro.py": _INTRO_PY.format(name=name, **metrics)}
        layers_src = (
            '    p.layer(os.path.join("layers", "intro.py"), priority=1)  # 数字が小さいほど奥\n'
            "\n")
        bg = "black"

    backups = []
    _write(os.path.join(root, "main.py"),
           _MAIN_PY.format(name=name, width=width, height=height, fps=fps,
                           bg=bg, layers=layers_src), backups)
    for fname, src in layer_files.items():
        _write(os.path.join(root, "layers", fname), src, backups)
    _write(os.path.join(root, "README.md"), _README.format(name=name), backups)
    _write(os.path.join(root, ".gitignore"), _GITIGNORE, backups)
    _write(os.path.join(root, "plugins", "README.md"), _PLUGINS_README, backups)
    for d in (os.path.join("assets", "images"), os.path.join("assets", "audio"),
              "output"):
        full = os.path.join(root, d)
        os.makedirs(full, exist_ok=True)
        gk = os.path.join(full, ".gitkeep")
        if not os.path.exists(gk):
            with open(gk, "w", encoding="utf-8") as f:
                f.write("")

    if backups and not quiet:
        print("既存ファイルを .bak に退避してから上書きしました:")
        for bak in backups:
            print(f"  {bak}")
    if not quiet:
        print(f"プロジェクトを作成しました: {root} (template={template})")
        print("次にやること:")
        print(f"  cd {root}")
        print("  python main.py                      # output/ にレンダ")
        print("  python -m scriptvedit watch main.py # 変更を監視して自動再レンダ")
        if template == "explainer":
            print('  ※ formula() には playwright が必要: '
                  'pip install "scriptvedit[web]" && playwright install chromium')
            print("  ※ assets/audio/bgm.mp3 を置くと BGM が乗ります")
    return root
