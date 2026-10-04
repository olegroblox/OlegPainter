"""«Авто» for the colour count: antialiasing and transparency must not add colours."""
from PIL import Image, ImageDraw, ImageFilter

from engine.olegpainter.core import OlegPainter


def _count(img):
    e = OlegPainter(status_callback=lambda m: None)
    e.set_image_from_pil(img.convert("RGBA"), "x")
    return e.recommend_color_count()["count"]


def _lines(background):
    img = Image.new("RGBA", (600, 600), background)
    d = ImageDraw.Draw(img)
    for i in range(0, 600, 40):
        d.line((i, 0, 600 - i, 600), fill=(0, 0, 0, 255), width=4)
        d.ellipse((i, i, i + 90, i + 60), outline=(0, 0, 0, 255), width=3)
    return img


def test_black_lines_count_as_one_or_two_colours():
    assert _count(_lines((0, 0, 0, 0))) == 1                                     # transparent background
    assert _count(_lines((255, 255, 255, 255))) == 2                             # black + white
    assert _count(_lines((255, 255, 255, 255)).filter(ImageFilter.GaussianBlur(0.7))) == 2  # antialiased


def test_flat_colour_areas_are_all_counted():
    img = Image.new("RGB", (300, 200), "white")
    d = ImageDraw.Draw(img)
    for i, colour in enumerate(("red", "green", "blue", "yellow", "black")):
        d.rectangle((10 + i * 55, 20, 55 + i * 55, 180), fill=colour)
    assert _count(img) == 6


def test_a_real_grey_band_between_black_and_white_is_a_colour():
    img = Image.new("RGB", (300, 200), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 100, 200), fill="black")
    d.rectangle((100, 0, 200, 200), fill=(128, 128, 128))
    assert _count(img) == 3


def _mode(img):
    e = OlegPainter(status_callback=lambda m: None)
    e.set_image_from_pil(img.convert("RGBA"), "x")
    return e.recommend_color_count()["mode"]


def test_auto_picks_black_and_white_mode_for_line_art_only():
    assert _mode(_lines((0, 0, 0, 0))) == "bw"
    assert _mode(_lines((255, 255, 255, 255)).filter(ImageFilter.GaussianBlur(0.7))) == "bw"
    grey = Image.new("RGB", (300, 200), "white")
    d = ImageDraw.Draw(grey)
    d.rectangle((0, 0, 100, 200), fill="black")
    d.rectangle((100, 0, 200, 200), fill=(128, 128, 128))
    assert _mode(grey) == "color"                 # a real grey needs the colour mode
    red = _lines((255, 255, 255, 255))
    ImageDraw.Draw(red).rectangle((200, 200, 400, 400), fill="red")
    assert _mode(red) == "color"
