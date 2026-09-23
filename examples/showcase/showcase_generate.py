"""ポートフォリオ用ショーケースのウォーターマーク生成（PIL）

スライド本体は `slides/*.html` を Playwright でレンダするため（showcase_slides.py）、
ここで作るのはレンダに必要な `slides/watermark.png` だけ。
かつては同じ内容のスライドを PNG でも描いていたが、どこからも参照されない
生成物だったので削除した。
"""
import os
from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "slides")
os.makedirs(OUT, exist_ok=True)

# --- フォント ---
_FONTS = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts")


def _jp(size, bold=False):
    try:
        return ImageFont.truetype(os.path.join(_FONTS, "meiryo.ttc"), size, index=1 if bold else 0)
    except Exception:
        return ImageFont.load_default()


# ============================================================
# ウォーターマーク (透過PNG)
# ============================================================
def watermark():
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _jp(26, bold=True)
    text = "この動画はscriptveditで制作されました"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    # 右上に角丸矩形 + テキスト
    pad_x, pad_y = 18, 10
    rx = W - tw - pad_x * 2 - 24
    ry = 20
    draw.rounded_rectangle(
        [rx, ry, rx + tw + pad_x * 2, ry + th + pad_y * 2],
        radius=12, fill=(255, 100, 60, 200),
    )
    draw.text((rx + pad_x, ry + pad_y), text, fill=(255, 255, 255, 240), font=font)
    img.save(os.path.join(OUT, "watermark.png"))
    print("  watermark.png")


def generate_all():
    """レンダに必要な生成物を作る（render_showcase.py からも呼ばれる）"""
    print("ウォーターマーク生成中...")
    watermark()
    print("完了")


if __name__ == "__main__":
    generate_all()
