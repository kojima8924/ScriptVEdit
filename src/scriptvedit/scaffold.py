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

title = text("{name}", x=0.5, y=0.45, size=72, color="white")
title.time(3) <= fade(lambda u: clip(u * 3, 0, 1) * clip((1 - u) * 3, 0, 1))

sub = text("scriptvedit で作った動画", x=0.5, y=0.6, size=32, color="#9fd0ff")
sub.time(3) <= fade(lambda u: clip(u * 2 - 0.4, 0, 1))
'''

_EXPLAINER_INTRO_PY = '''from scriptvedit import *
# タイトル（解説動画のオープニング）

title = text("{name}", x=0.5, y=0.42, size=72, color="white")
title.time(3) <= fade(lambda u: clip(u * 3, 0, 1) * clip((1 - u) * 3, 0, 1))

sub = text("3分でわかる解説", x=0.5, y=0.58, size=32, color="#9fd0ff")
sub.time(3) <= fade(lambda u: clip(u * 2 - 0.4, 0, 1))
'''

_EXPLAINER_BODY_PY = '''from scriptvedit import *
# 本編: 数式（formula）+ 字幕（text）。formula は KaTeX 同梱でオフライン動作する。
# （formula には playwright が必要: pip install "scriptvedit[web]" && playwright install chromium）

eq = formula(r"P(A \\cup B) = P(A) + P(B) - P(A \\cap B)", size=56, color="white")
eq.time(4) <= fade(lambda u: clip(u * 4, 0, 1)) & move(x=0.5, y=0.4, anchor="center")

cap = text("包除原理: 重なりを引く", x=0.5, y=0.75, size=36, color="#ffd166")
cap.time(4) <= fade(lambda u: clip(u * 4, 0, 1) * clip((1 - u) * 4, 0, 1))
'''

_EXPLAINER_BGM_PY = '''from scriptvedit import *
import os
# BGM レイヤー。assets/audio/bgm.mp3 を置くと自動で乗る（無ければ無音のまま）。
# 共有ライブラリを使う場合は環境変数 SCRIPTVEDIT_ASSETS を設定すれば
# asset("audio/bgm.mp3") がそこから assets/_imported/ へ取り込む。

bgm_path = asset("audio/bgm.mp3", must_exist=False)
if os.path.exists(bgm_path):
    bgm = Object(asset("audio/bgm.mp3"))
    # avolume は定数なら固定音量、式ならフェード（音量とフェードは同じ1段）
    bgm.time(7) <= loop() & avolume(lambda u: 0.5 * clip(u * 8, 0, 1))
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

    if template == "explainer":
        layer_files = {
            "intro.py": _EXPLAINER_INTRO_PY.format(name=name),
            "body.py": _EXPLAINER_BODY_PY,
            "bgm.py": _EXPLAINER_BGM_PY,
        }
        layers_src = (
            '    p.layer(os.path.join("layers", "intro.py"), priority=1)\n'
            '    p.layer(os.path.join("layers", "body.py"), priority=2)\n'
            '    p.layer(os.path.join("layers", "bgm.py"), priority=0)  # 数字が小さいほど奥\n'
            "\n")
        bg = "#0d1b2a"
    else:
        layer_files = {"intro.py": _INTRO_PY.format(name=name)}
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
