# scriptvedit ショーケース動画（ポートフォリオ用）
#
# fresh clone では slides/watermark.png（gitignore 対象の生成物）が無いため、
# レンダ前に showcase_generate.py を自動実行して決定論的に生成する。
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

# showcase_generate.py が生成するもののうち、レンダに必要なファイル
_GENERATED_REQUIRED = [
    os.path.join(_HERE, "slides", "watermark.png"),
]


def ensure_generated():
    """レンダに必要な生成物が無ければ showcase_generate.py で生成する"""
    if all(os.path.exists(p) for p in _GENERATED_REQUIRED):
        return
    try:
        import PIL  # noqa: F401
    except ImportError:
        raise SystemExit(
            "生成物（slides/watermark.png）が無く、生成には Pillow が必要です。\n"
            "  pip install Pillow\n"
            "を実行してから再度レンダしてください（showcase_generate.py が生成します）。"
        )
    spec = importlib.util.spec_from_file_location(
        "showcase_generate", os.path.join(_HERE, "showcase_generate.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.generate_all()


def build_project():
    """ショーケースの Project を構築して返す（レンダはしない）"""
    from scriptvedit import Project
    p = Project()
    p.configure(width=1920, height=1080, fps=30, background_color="#12121e")

    p.layer(os.path.join(_HERE, "showcase_slides.py"), priority=0)
    p.layer(os.path.join(_HERE, "showcase_objects.py"), priority=1)
    p.layer(os.path.join(_HERE, "showcase_watermark.py"), priority=2)
    # BGM を付けたい場合は assets/audio/bgm.mp3 を置いて次の行を有効化する
    # （詳細は examples/showcase/README.md）
    # p.layer(os.path.join(_HERE, "showcase_bgm.py"), priority=3)
    return p


if __name__ == "__main__":
    ensure_generated()
    p = build_project()
    out = os.path.join(_HERE, "output_showcase.mp4")
    p.render(out)
    print(f"出力: {out}")
