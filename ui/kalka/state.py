"""Сохранение и загрузка состояния приложения.

Состояние делится на три независимых блока, что отражает архитектуру:
  * WindowState — уровень окна (viewport): позиция на экране, размер, флаги.
  * ImageState  — трансформации изображения внутри сцены (не зависят от окна).
  * ViewState   — состояние холста/камеры: масштаб и точка центра сцены.

Файл состояния хранится в каталоге данных пользователя (на Windows — %APPDATA%).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
from infrastructure.documents import read_document, write_document

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#   Путь к файлу состояния
# --------------------------------------------------------------------------- #

def config_dir() -> Path:
    """Каталог для хранения настроек приложения."""
    override = os.environ.get("OLEGPAINTER_CONFIG_DIR", "").strip()
    if override:
        path = Path(override) / "kalka"
        path.mkdir(parents=True, exist_ok=True)
        return path
    base = os.environ.get("APPDATA")  # Windows
    if not base:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config"
        )
    path = Path(base) / "Kalka"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_file() -> Path:
    return config_dir() / "state.json"


# --------------------------------------------------------------------------- #
#   Блоки состояния
# --------------------------------------------------------------------------- #

@dataclass
class WindowState:
    """Уровень окна / viewport."""
    x: int = 220
    y: int = 160
    width: int = 640
    height: int = 520
    always_on_top: bool = True       # поверх всех окон
    opacity: float = 1.0             # прозрачность всего окна (0.15..1.0)
    click_through: bool = False      # сквозные клики (режим трафарета)
    show_border: bool = True         # показывать рамку окна
    toolbar_enabled: bool = True     # разрешена ли всплывающая панель


@dataclass
class ImageState:
    """Трансформации изображения как элемента сцены."""
    source_path: str = ""
    center_x: float = 0.0            # положение ЦЕНТРА изображения в координатах сцены
    center_y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation: float = 0.0            # градусы
    opacity: float = 1.0            # прозрачность самого изображения (0.0..1.0)


@dataclass
class ViewState:
    """Состояние камеры/холста."""
    zoom: float = 1.0
    center_x: float = 0.0            # точка сцены, находящаяся в центре viewport
    center_y: float = 0.0


@dataclass
class AppState:
    window: WindowState = field(default_factory=WindowState)
    image: ImageState = field(default_factory=ImageState)
    view: ViewState = field(default_factory=ViewState)

    # ---- сериализация ----------------------------------------------------- #

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "window": asdict(self.window),
            "image": asdict(self.image),
            "view": asdict(self.view),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppState":
        """Терпимая к отсутствующим/лишним ключам загрузка."""
        def build(dc_cls, raw):
            allowed = {f for f in dc_cls.__dataclass_fields__}
            clean = {k: v for k, v in (raw or {}).items() if k in allowed}
            return dc_cls(**clean)

        return cls(
            window=build(WindowState, data.get("window")),
            image=build(ImageState, data.get("image")),
            view=build(ViewState, data.get("view")),
        )

    # ---- файл ------------------------------------------------------------- #

    def save(self) -> None:
        try:
            write_document(state_file(), self.to_dict())
        except (OSError, ValueError):
            log.exception("Cannot save stencil state")

    @classmethod
    def load(cls) -> "AppState":
        try:
            return cls.from_dict(read_document(state_file()))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError, TypeError, AttributeError):
            log.exception("Cannot load stencil state")
        return cls()
