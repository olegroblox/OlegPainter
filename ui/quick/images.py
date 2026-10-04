"""In-memory QImage delivery without PNG encoding or temporary files."""
import threading
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider


class ImageProvider(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self._lock = threading.Lock()
        self._images = {}
        self._versions = {}

    def publish(self, channel, image):
        image = QImage(image) if image is not None else QImage()
        with self._lock:
            previous = self._images.get(channel)
            if previous is not None and previous.cacheKey() == image.cacheKey():
                return self._url(channel)
            self._images[channel] = image
            self._versions[channel] = self._versions.get(channel, 0) + 1
            return self._url(channel)

    def _url(self, channel):
        if self._images[channel].isNull():
            return ""
        return f"image://painter/{channel}/{self._versions[channel]}"

    def requestImage(self, identifier, size, requested_size):
        channel, _, version = identifier.partition("/")
        with self._lock:
            if version != str(self._versions.get(channel)):
                return QImage()
            image = QImage(self._images[channel])
        if size is not None:
            size.setWidth(image.width())
            size.setHeight(image.height())
        return image
