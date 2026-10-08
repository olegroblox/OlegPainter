"""Pictures by a link or from the screen (IMAGE-SOURCE-001).

A link — pasted, dropped from a browser or found on a page — downloads in a
worker thread; the result returns to the main thread and becomes the source
image exactly like a pasted picture (the session keeps its pixels).
"""
from __future__ import annotations

import logging
import threading

from application import image_sources
from infrastructure import web_image

log = logging.getLogger(__name__)


class ImageLinkMixin:
    _image_download = None

    @property
    def image_download_active(self) -> bool:
        return self._image_download is not None

    def open_image_url(self, url: str, *, fetch=None, decode=None) -> bool:
        """Start loading the picture at `url`; False when it cannot start now."""
        if not self._guard_image_mutation("opening an image link"):
            return False
        if self._image_download is not None:
            self.statusChanged.emit("warn: Картинка уже загружается. Дождитесь окончания.")
            return False
        link = image_sources.image_url_from_text(url)
        if link is None:
            self.statusChanged.emit("warn: Это не ссылка на картинку. Ссылка начинается с http:// или https://.")
            return False
        fetch = fetch or web_image.fetch_image
        decode = decode or web_image.decode_image
        token = object()
        self._image_download = token
        self.statusChanged.emit("Загружаю картинку…")
        self._emit_session_state_changed("image-download", sections=("image",))

        def work():
            try:
                if link.startswith("data:"):
                    data, final_url = image_sources.decode_data_url(link), link
                else:
                    data, final_url = fetch(link)
                result = (decode(data), image_sources.origin_label(final_url), None)
            except Exception as error:  # reported in the main thread
                result = (None, "", error)
            self._invoke_on_qt(lambda: self._image_url_loaded(token, *result))

        threading.Thread(target=work, name="image-download", daemon=True).start()
        return True

    def _image_url_loaded(self, token, image, origin, error):
        if token is not self._image_download:
            return
        self._image_download = None
        self._emit_session_state_changed("image-download", sections=("image",))
        if error is not None:
            known = isinstance(error, (web_image.DownloadError, ValueError))
            log.warning("Image link not loaded: %s", error, exc_info=not known)
            self.statusChanged.emit("error: " + (str(error) if known else "Не удалось загрузить картинку. "
                                                  + web_image.COPY_HINT))
            return
        if self.paste_image(image, origin=origin):
            self.statusChanged.emit("Картинка загружена. Фон можно убрать в «Обработке», а края обрезать в трафарете (Alt+F2).")

    def cancel_image_download(self) -> None:
        """A newer picture (file, paste) wins over a link still loading."""
        if self._image_download is not None:
            self._image_download = None
            self._emit_session_state_changed("image-download", sections=("image",))

    def set_screen_snapshot(self, image) -> bool:
        """«Снимок экрана»: a framed part of the desktop becomes the picture.

        Like any new picture it loses its background only when the user asked for
        that for new pictures (ImageEditsMixin.auto_background)."""
        if self.paste_image(image, origin="Снимок экрана"):
            if self.auto_background == "off":
                note = self._insert_note
                self.statusChanged.emit("Снимок экрана вставлен. Фон можно убрать в «Обработке», а края обрезать в трафарете (Alt+F2)."
                                        + (" " + note if note else ""))
            return True
        return False
