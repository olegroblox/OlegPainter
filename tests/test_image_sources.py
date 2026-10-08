"""Pictures from the internet and the screen (IMAGE-SOURCE-001)."""
import base64
import io
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image
from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QGuiApplication, QWindow
from PySide6.QtQuick import QQuickItem

from application import image_sources
from infrastructure import web_image
from tests.test_application_controller import controllers  # noqa: F401
from tests.test_desktop_overlays import desktop  # noqa: F401
from tests.test_quick_presentation import quick, pump, visual_item  # noqa: F401
from ui.quick import presenter as presenter_module


def png_bytes(size=(6, 4), color=(200, 30, 40)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


class Response:
    def __init__(self, data, content_type="image/png", url="https://example.com/a.png", length=None):
        self._data, self._url = data, url
        self.headers = {"Content-Type": content_type}
        if length is not None:
            self.headers["Content-Length"] = str(length)

    def read(self, limit):
        return self._data[:limit]

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_search_opens_large_pictures_of_the_chosen_kind():
    url = image_sources.search_url("google", "  кот   рыжий ", "clipart")
    parts = urlsplit(url)
    assert parts.netloc == "www.google.com"
    assert parse_qs(parts.query) == {"tbm": ["isch"], "q": ["кот рыжий"], "tbs": ["isz:l,itp:clipart"]}
    yandex = parse_qs(urlsplit(image_sources.search_url("yandex", "кот", "transparent")).query)
    assert yandex == {"text": ["кот"], "isize": ["large"], "itype": ["png"]}
    assert "filterui:photo-linedrawing" in image_sources.search_url("bing", "кот", "lineart")
    # A service without the filter gets the words in the query instead.
    pinterest = parse_qs(urlsplit(image_sources.search_url("pinterest", "кот", "transparent")).query)
    assert pinterest["q"] == ["кот png без фона"]
    english = parse_qs(urlsplit(image_sources.search_url("pinterest", "cat", "transparent", "en")).query)
    assert english["q"] == ["cat transparent png"]       # no Russian words in an English search
    for service in image_sources.SEARCH_SERVICES:
        assert image_sources.search_url(service["id"], "a", "any").startswith("https://")
    with pytest.raises(ValueError):
        image_sources.search_url("google", "   ", "any")
    with pytest.raises(ValueError):
        image_sources.search_url("altavista", "кот", "any")


def test_links_are_found_in_text_and_browser_html():
    assert image_sources.image_url_from_text(" https://i.example.com/cat.png \n") == "https://i.example.com/cat.png"
    assert image_sources.image_url_from_text("кот https://x.y") is None
    assert image_sources.image_url_from_text("line one\nhttps://x.y/a.png") is None
    assert image_sources.image_url_from_text("ftp://x.y/a.png") is None
    html = '<a href="/page"><img alt="x" src="https://cdn.example.com/p.jpg?a=1&amp;b=2"></a>'
    assert image_sources.image_url_from_html(html) == "https://cdn.example.com/p.jpg?a=1&b=2"
    data_url = "data:image/png;base64," + base64.b64encode(png_bytes()).decode()
    assert image_sources.image_url_from_html(f"<img src='{data_url}'>") == data_url
    assert image_sources.decode_data_url(data_url) == png_bytes()
    assert image_sources.origin_label("https://www.site.ru/a.png") == "site.ru"


def test_download_follows_the_picture_of_a_page_and_limits_size():
    requested = []

    def opener(request, timeout):
        requested.append(request.full_url)
        if request.full_url.endswith("/page"):
            return Response(b'<html><head><meta content="/img/big.png" property="og:image"></head></html>',
                            "text/html; charset=utf-8", url="https://site.ru/page")
        return Response(png_bytes(), url=request.full_url)

    data, final = web_image.fetch_image("https://site.ru/page", opener=opener)
    assert data == png_bytes() and final == "https://site.ru/img/big.png"
    assert requested == ["https://site.ru/page", "https://site.ru/img/big.png"]
    with pytest.raises(web_image.DownloadError, match="страница"):
        web_image.fetch_image("https://site.ru/x", opener=lambda r, timeout: Response(b"<html></html>", "text/html"))
    with pytest.raises(web_image.DownloadError, match="40 МБ"):
        web_image.fetch_image("https://site.ru/x", opener=lambda r, timeout: Response(b"x", length=10**9))
    with pytest.raises(web_image.DownloadError, match="40 МБ"):
        web_image.fetch_image("https://site.ru/x", max_bytes=3, opener=lambda r, timeout: Response(b"12345"))

    def forbidden(request, timeout):
        raise HTTPError(request.full_url, 403, "Forbidden", {}, None)

    with pytest.raises(web_image.DownloadError, match="ошибка 403"):
        web_image.fetch_image("https://site.ru/x", opener=forbidden)
    with pytest.raises(web_image.DownloadError):
        web_image.fetch_image("file:///C:/secret.png")
    assert web_image.ascii_url("https://пример.рф/кот 1.png?q=кот") == \
        "https://xn--e1afmkfd.xn--p1ai/%D0%BA%D0%BE%D1%82%201.png?q=%D0%BA%D0%BE%D1%82"


def test_downloaded_bytes_become_an_rgba_picture(controllers):
    controllers()  # a Qt application for the SVG path
    image = web_image.decode_image(png_bytes((5, 3)))
    assert image.mode == "RGBA" and image.size == (5, 3)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10"><rect width="20" height="10" fill="red"/></svg>'
    image = web_image.decode_image(svg)
    assert image.size == (1024, 512) and image.getpixel((500, 250))[:3] == (255, 0, 0)
    with pytest.raises(web_image.DownloadError, match="формат"):
        web_image.decode_image(b"not a picture at all")


def test_link_loads_in_background_and_becomes_the_source(controllers):
    service = controllers().service
    messages = []
    service.statusChanged.connect(messages.append)
    assert service.open_image_url("https://site.ru/cat.png", fetch=lambda url: (png_bytes((9, 7)), url))
    assert service.image_download_active
    # One download at a time: a second link waits for the first.
    assert not service.open_image_url("https://site.ru/dog.png")
    pump(lambda: not service.image_download_active)
    assert service.engine.source_pil_image.size == (9, 7)
    assert service._session_image_kind == "clipboard_cache"
    assert messages[0] == "Загружаю картинку…" and messages[-1].startswith("Картинка загружена.")

    def fail(url):
        raise web_image.DownloadError("Картинка больше 40 МБ.")

    assert service.open_image_url("https://site.ru/huge.png", fetch=fail)
    pump(lambda: not service.image_download_active)
    assert messages[-1] == "error: Картинка больше 40 МБ."
    assert service.engine.source_pil_image.size == (9, 7)
    assert not service.open_image_url("просто текст")
    assert messages[-1].startswith("warn: Это не ссылка")


def test_copied_link_is_pasted_like_a_picture(controllers, monkeypatch):
    service = controllers().service
    monkeypatch.setattr(web_image, "fetch_image", lambda url: (png_bytes((8, 8)), url))
    QGuiApplication.clipboard().setText("https://site.ru/copied.png")
    assert service.paste_from_clipboard()
    pump(lambda: not service.image_download_active)
    assert service.engine.source_pil_image.size == (8, 8)


def test_dropped_file_and_browser_picture_open(quick, tmp_path, monkeypatch):
    presenter = quick.presenter
    source = tmp_path / "dropped.png"
    source.write_bytes(png_bytes((12, 10)))
    assert presenter.dropImage([QUrl.fromLocalFile(str(source))], "", "")
    pump(lambda: quick.service.engine.source_pil_image is not None
         and quick.service.engine.source_pil_image.size == (12, 10))
    # A browser drag: the link leads to a page, the <img> is the picture the user saw.
    data_url = "data:image/png;base64," + base64.b64encode(png_bytes((3, 5))).decode()
    assert presenter.dropImage([QUrl("https://www.google.com/imgres?x=1")], f'<img src="{data_url}">', "")
    pump(lambda: quick.service.engine.source_pil_image.size == (3, 5))
    assert not presenter.dropImage([], "", "просто текст")
    assert presenter.messageError and "Перетащите" in presenter.message


def test_search_opens_the_browser_and_offers_the_copied_picture(quick, monkeypatch):
    presenter, window = quick.presenter, quick.window
    opened = []
    monkeypatch.setattr(presenter_module, "open_url", lambda url: opened.append(url) or True)
    monkeypatch.setattr(web_image, "fetch_image", lambda url: (png_bytes((7, 7)), url))
    assert presenter.imageSearch["service"] == "yandex"
    window.showNormal()
    assert presenter.searchImages("google", "котик", "clipart")
    assert opened and "google.com" in opened[0]
    assert presenter.imageSearch["service"] == "google" and presenter.imageSearch["kind"] == "clipart"
    # the pinned window steps aside for the browser and comes back with the copied picture
    assert presenter._search_return
    QGuiApplication.clipboard().setText("https://site.ru/found.png")
    pump(lambda: presenter.clipboardOffer)
    assert not presenter._search_return and window.visibility() != QWindow.Minimized
    offer = window.findChild(QQuickItem, "clipboardOffer")
    pump(lambda: offer.isVisible())
    assert presenter.acceptClipboardOffer()
    assert not presenter.clipboardOffer
    pump(lambda: quick.service.engine.source_pil_image is not None
         and quick.service.engine.source_pil_image.size == (7, 7))
    assert presenter.searchImages("google", "", "any") is False
    assert presenter.messageError


def test_search_dialog_and_buttons_are_in_the_window(quick):
    window = quick.window
    for name in ("searchImageButton", "screenSnapshotButton"):
        assert visual_item(window.contentItem(), name) is not None, name
    assert window.findChild(QObject, "imageSearchDialog") is not None
    # Without desktop tools (tests), the snapshot explains itself instead of failing silently.
    assert not quick.presenter.captureScreenImage()
    assert "Экранные инструменты" in quick.presenter.message


def test_screen_snapshot_is_cut_from_the_frozen_desktop(desktop):
    controller, _, service = desktop
    frozen = Image.new("RGB", (400, 300), "white")
    frozen.paste((255, 0, 0), (100, 50, 140, 90))
    taken = []
    # Virtual desktop starts left of and above the primary monitor.
    controller.capture_screen_image(taken.append, grab=lambda: frozen, origin=lambda: (-2000, -100))
    pump(lambda: controller.picker is not None)
    controller.picker._handle_event("down", (-1900, -50))
    controller.picker._handle_event("up", (-1860, -10))
    assert len(taken) == 1 and taken[0].size == (40, 40)
    assert taken[0].getpixel((5, 5)) == (255, 0, 0, 255)
    assert controller.picker is None


def test_screen_snapshot_cancel_and_failure_are_reported(desktop):
    controller, _, service = desktop
    messages = []
    service.statusChanged.connect(messages.append)
    taken = []
    controller.capture_screen_image(taken.append, grab=lambda: Image.new("RGB", (10, 10)), origin=lambda: (0, 0))
    pump(lambda: controller.picker is not None)
    controller.picker.cancel()
    assert not taken and messages[-1] == "Снимок экрана отменён"

    def broken():
        raise OSError("no desktop")

    controller.capture_screen_image(taken.append, grab=broken, origin=lambda: (0, 0))
    pump(lambda: bool(messages) and messages[-1].startswith("error: Не удалось сделать снимок экрана"))
    assert controller.mode == "" and not taken
