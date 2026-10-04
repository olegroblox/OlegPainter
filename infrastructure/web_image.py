"""Download one picture by a link (IMAGE-SOURCE-001).

Runs in a worker thread, never in the UI thread. Limits: http/https only,
40 MB, 20 s per request. A link to a page (not to the picture itself) follows
the picture the page shares (og:image) once — search sites often copy those.
"""
from __future__ import annotations

import re
import urllib.request
from html import unescape
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

MAX_BYTES = 40 * 1024 * 1024
TIMEOUT = 20
# Sites refuse unknown clients; AVIF is left out on purpose: Pillow here cannot decode it.
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"),
    "Accept": "image/webp,image/png,image/jpeg,image/gif,image/svg+xml,image/*;q=0.8,text/html;q=0.5,*/*;q=0.3",
}
COPY_HINT = "Скопируйте саму картинку (правой кнопкой → «Копировать картинку») и нажмите «Из буфера»."


class DownloadError(RuntimeError):
    pass


def fetch_image(url: str, *, opener=None, max_bytes: int = MAX_BYTES, timeout: float = TIMEOUT,
                follow_page: bool = True) -> tuple[bytes, str]:
    """Bytes and the final address of the picture at `url`."""
    if not re.match(r"https?://", str(url), re.IGNORECASE):
        raise DownloadError("Можно загрузить только ссылку, которая начинается с http:// или https://.")
    request = urllib.request.Request(ascii_url(url), headers=_HEADERS)
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            length = response.headers.get("Content-Length") or ""
            if length.isdigit() and int(length) > max_bytes:
                raise DownloadError("Картинка больше 40 МБ.")
            data = response.read(max_bytes + 1)
            final_url = response.geturl() if hasattr(response, "geturl") else url
    except DownloadError:
        raise
    except HTTPError as error:
        raise DownloadError(f"Сайт не отдал картинку (ошибка {error.code}). {COPY_HINT}") from error
    except (URLError, TimeoutError, OSError, ValueError) as error:
        raise DownloadError("Не удалось загрузить картинку: сайт не ответил или нет интернета.") from error
    if len(data) > max_bytes:
        raise DownloadError("Картинка больше 40 МБ.")
    if content_type.startswith("text/html") or data[:512].lstrip().lower().startswith((b"<!doctype html", b"<html")):
        image_url = page_image_url(data.decode("utf-8", "replace"), final_url) if follow_page else None
        if image_url is None:
            raise DownloadError("По ссылке открывается страница, а не картинка. " + COPY_HINT)
        return fetch_image(image_url, opener=opener, max_bytes=max_bytes, timeout=timeout, follow_page=False)
    if not data:
        raise DownloadError("Сайт прислал пустой файл. " + COPY_HINT)
    return data, final_url


def ascii_url(url: str) -> str:
    """Cyrillic paths and domains (.рф) as the HTTP request needs them, without double-encoding."""
    parts = urlsplit(url)
    netloc = parts.netloc
    host = parts.hostname or ""
    if host and not host.isascii():
        netloc = netloc.replace(host, host.encode("idna").decode("ascii"))
    keep = "/%:@!$&'()*+,;=~"
    return urlunsplit((parts.scheme, netloc, quote(parts.path, safe=keep),
                       quote(parts.query, safe=keep + "?"), quote(parts.fragment, safe=keep + "?")))


def page_image_url(html: str, base_url: str) -> str | None:
    """The picture a page shares with messengers: og:image, twitter:image or image_src."""
    for tag in re.findall(r"<(?:meta|link)\b[^>]*>", html, re.IGNORECASE):
        if re.search(r"""(?:property|name)\s*=\s*["'](?:og:image(?::secure_url|:url)?|twitter:image(?::src)?)["']""",
                     tag, re.IGNORECASE):
            found = re.search(r"""content\s*=\s*["']([^"']+)["']""", tag, re.IGNORECASE)
        elif re.search(r"""rel\s*=\s*["']image_src["']""", tag, re.IGNORECASE):
            found = re.search(r"""href\s*=\s*["']([^"']+)["']""", tag, re.IGNORECASE)
        else:
            continue
        if found:
            return urljoin(base_url, unescape(found.group(1).strip()))
    return None


def decode_image(data: bytes, *, svg_size: int = 1024):
    """RGBA PIL image from downloaded bytes; Qt plugins cover SVG and rarer formats."""
    from PIL import Image
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            return image.convert("RGBA")
    except Image.DecompressionBombError as error:
        raise DownloadError("Картинка слишком большая для рисования.") from error
    except Exception:
        pass
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
    from PySide6.QtGui import QImage, QImageReader
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    if bytes(reader.format()) == b"svg":
        size = reader.size()
        if size.isValid() and max(size.width(), size.height()) > 0:
            scale = svg_size / max(size.width(), size.height())
            reader.setScaledSize(QSize(max(1, round(size.width() * scale)), max(1, round(size.height() * scale))))
    image = reader.read()
    if image.isNull():
        raise DownloadError("Этот формат картинки не поддерживается. " + COPY_HINT)
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombuffer("RGBA", (image.width(), image.height()), bytes(image.constBits()),
                            "raw", "RGBA", image.bytesPerLine(), 1).copy()
