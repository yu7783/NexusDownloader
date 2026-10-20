"""Nexus Downloader の Windows 用アイコンを生成する。

Android 版のランチャーアイコン（紺色の角丸背景 + 白いダウンロード矢印 +
水色のトレイ）と同じデザインを Pillow で描画し、複数サイズ入りの
``assets/nexus.ico`` と ``assets/nexus.png`` を出力する。

使い方（Pillow が必要）:
    python tools/make_icon.py
"""

import os

from PIL import Image, ImageDraw

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

#: 論理キャンバスサイズ（この座標系で図形を定義する）
SIZE = 1024

#: スーパーサンプリング倍率（アンチエイリアスのため大きく描いて縮小する）
SS = 4

#: Android 版と同じ配色
COLOR_BG_TOP = (35, 35, 61)       # #23233D
COLOR_BG_BOTTOM = (20, 20, 37)    # #141425
COLOR_BG_BORDER = (52, 52, 82)    # #343452
COLOR_ARROW = (255, 255, 255)
COLOR_TRAY = (124, 196, 255)      # #7CC4FF

ICO_SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]


def _s(value):
    """論理座標をスーパーサンプリング後の座標へ変換する。"""
    return int(round(value * SS))


def _rounded_gradient():
    """角丸背景（上下グラデーション + 細い境界線）を返す。"""
    canvas = SIZE * SS

    # 縦方向のグラデーション
    gradient = Image.new("RGB", (1, canvas))
    for y in range(canvas):
        t = y / max(1, canvas - 1)
        color = tuple(
            int(round(COLOR_BG_TOP[i] + (COLOR_BG_BOTTOM[i] - COLOR_BG_TOP[i]) * t))
            for i in range(3)
        )
        gradient.putpixel((0, y), color)
    gradient = gradient.resize((canvas, canvas), Image.BILINEAR).convert("RGBA")

    # 角丸マスク
    mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [_s(32), _s(32), _s(992), _s(992)], radius=_s(224), fill=255)
    background = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    background.paste(gradient, (0, 0), mask)

    # 内側の細い境界線（小さいサイズでも輪郭が分かるように）
    ImageDraw.Draw(background).rounded_rectangle(
        [_s(32), _s(32), _s(992), _s(992)],
        radius=_s(224), outline=COLOR_BG_BORDER, width=max(1, _s(6)))
    return background


def _draw_foreground(image):
    """白いダウンロード矢印と水色のトレイを描画する。"""
    draw = ImageDraw.Draw(image)

    # 矢印のステム（丸い端の縦棒）
    draw.rounded_rectangle(
        [_s(470), _s(270), _s(554), _s(560)],
        radius=_s(24), fill=COLOR_ARROW)

    # 矢印の先端（下向きの三角）
    draw.polygon(
        [(_s(352), _s(520)), (_s(672), _s(520)), (_s(512), _s(756))],
        fill=COLOR_ARROW)

    # 受け皿のバー
    draw.rounded_rectangle(
        [_s(330), _s(792), _s(694), _s(858)],
        radius=_s(24), fill=COLOR_TRAY)


def build_icon():
    os.makedirs(ASSETS_DIR, exist_ok=True)

    image = _rounded_gradient()
    _draw_foreground(image)

    # スーパーサンプリング分を縮小してアンチエイリアスをかける
    icon_1024 = image.resize((SIZE, SIZE), Image.LANCZOS)

    png_path = os.path.join(ASSETS_DIR, "nexus.png")
    icon_1024.save(png_path, format="PNG")

    ico_path = os.path.join(ASSETS_DIR, "nexus.ico")
    # Pillow は sizes に合わせて LANCZOS で縮小し、複数解像度を格納する
    icon_1024.save(ico_path, format="ICO", sizes=ICO_SIZES)

    return ico_path, png_path


if __name__ == "__main__":
    ico, png = build_icon()
    print("created:", ico)
    print("created:", png)
