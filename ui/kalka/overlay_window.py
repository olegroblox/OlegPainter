"""OverlayWindow — безрамочное окно-трафарет (уровень viewport).

Отвечает за всё, что относится к ОКНУ, и НИКОГДА не трогает трансформации
изображения:
  * перемещение окна (ПКМ) и изменение размера (края) — инициируются из CanvasView,
    но геометрию меняет это окно; содержимое сцены «приклеивается» к неподвижному углу;
  * поверх всех окон, прозрачность окна, сквозные клики (режим трафарета);
  * системный трей и всплывающая панель — управление, доступное даже в режиме
    сквозных кликов (через трей и глобальную горячую клавишу);
  * сохранение/загрузка состояния.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, QRect, QRectF, QTimer
from PySide6.QtGui import (
    QPainter, QPen, QColor, QPixmap, QIcon, QAction, QKeySequence,
    QShortcut, QFont, QCursor,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QFileDialog, QSystemTrayIcon, QMenu, QMessageBox,
    QApplication,
)

from ui.overlays.theme import ACCENT, ACCENT_SOFT, MUTED, paint_card
from ui.i18n import tr

from .canvas_view import (
    CanvasView, EDGE_LEFT, EDGE_RIGHT, EDGE_TOP, EDGE_BOTTOM,
)
from .toolbar import FloatingToolbar
from .state import AppState
from .geometry import clamp
from . import win_native

MIN_WIN_W, MIN_WIN_H = 120, 90
HOTKEY_ID = 1
VK_X = 0x58           # клавиша X для глобального хоткея Ctrl+Alt+X

HELP_TEXT = (
    "<b>Калька</b> — трафарет поверх рабочего стола.<br><br>"
    "<b>Окно (viewport):</b><br>"
    "• ПКМ + перетаскивание — двигать окно<br>"
    "• край / угол окна — менять размер (обрезает видимую область, не масштабирует)<br><br>"
    "<b>Холст:</b><br>"
    "• колесо мыши — масштаб холста относительно курсора<br>"
    "• Alt + ЛКМ или средняя кнопка — панорама холста<br><br>"
    "<b>Изображение:</b><br>"
    "• ЛКМ по картинке — перемещать<br>"
    "• ЛКМ за маркер — менять размер (Shift — пропорции, Alt — от центра)<br>"
    "• верхний маркер-«антенна» — поворот (Shift — шаг 15°)<br><br>"
    "<b>Режим трафарета:</b> «Сквозные клики» — мышь проходит сквозь окно "
    "в приложение под ним. Вернуть управление: <b>Ctrl+Alt+X</b> или меню в трее."
)


class OverlayWindow(QWidget):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        from ui.overlays.capture_registration import register_screen_tool
        register_screen_tool(self)
        self._state = state
        self._win_gesture: dict = {}
        self._hotkey_hwnd = 0
        self._intro = True

        self.setWindowTitle("Калька")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(MIN_WIN_W, MIN_WIN_H)
        self._apply_window_flags(initial=True)

        # viewport
        self._view = CanvasView()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

        # панель
        self._toolbar = FloatingToolbar(self)
        self._toolbar.hide()

        self._build_tray()
        self._build_shortcuts()
        self._apply_state_to_ui()

        # сохранение с задержкой
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._do_save)

        # авто-показ панели
        self._toolbar_timer = QTimer(self)
        self._toolbar_timer.setInterval(200)
        self._toolbar_timer.timeout.connect(self._tick_toolbar)
        self._toolbar_timer.start()
        QTimer.singleShot(4000, self._end_intro)

    # ================================================================== #
    #   Флаги окна / показ
    # ================================================================== #

    def _apply_window_flags(self, initial: bool = False) -> None:
        flags = Qt.FramelessWindowHint | Qt.Window
        if self._state.window.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        geom = None if initial else self.geometry()
        self.setWindowFlags(flags)
        if geom is not None:
            self.setGeometry(geom)
            self.show()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._post_show_native()

    def _post_show_native(self) -> None:
        """Применяет платформенные стили после появления нативного окна."""
        hwnd = int(self.winId())
        win_native.set_tool_window(hwnd, True)                    # убрать из панели задач
        win_native.set_click_through(hwnd, self._state.window.click_through)
        if hwnd != self._hotkey_hwnd:
            if self._hotkey_hwnd:
                win_native.unregister_hotkey(self._hotkey_hwnd, HOTKEY_ID)
            win_native.register_hotkey(
                hwnd, HOTKEY_ID,
                win_native.MOD_CONTROL | win_native.MOD_ALT, VK_X,
            )
            self._hotkey_hwnd = hwnd

    def nativeEvent(self, eventType, message):
        if win_native.IS_WINDOWS and bytes(eventType) == b"windows_generic_MSG":
            if win_native.parse_hotkey_message(message) == HOTKEY_ID:
                self.toggle_click_through()
                return True, 0
        return super().nativeEvent(eventType, message)

    # ================================================================== #
    #   Применение / снимок состояния
    # ================================================================== #

    def _apply_state_to_ui(self) -> None:
        w = self._state.window
        self.setGeometry(w.x, w.y, w.width, w.height)
        self.setWindowOpacity(clamp(w.opacity, 0.15, 1.0))
        self._view.apply_image_state(self._state.image)
        self._view.apply_view_state(self._state.view)
        self._sync_ui()

    def _do_save(self) -> None:
        g = self.geometry()
        self._state.window.x = g.x()
        self._state.window.y = g.y()
        self._state.window.width = g.width()
        self._state.window.height = g.height()
        self._view.capture_view_state(self._state.view)
        self._view.capture_image_state(self._state.image)
        self._state.save()

    def request_save(self) -> None:
        self._save_timer.start()

    # ================================================================== #
    #   Перемещение и изменение размера ОКНА (вызывается из CanvasView)
    # ================================================================== #

    def begin_window_move(self, gpos: QPoint) -> None:
        self._win_gesture = {"mouse0": gpos, "pos0": self.pos()}

    def update_window_move(self, gpos: QPoint) -> None:
        g = self._win_gesture
        if not g:
            return
        delta = gpos - g["mouse0"]
        self.move(g["pos0"] + delta)   # окно едет целиком; сцена не сдвигается

    def begin_window_resize(self, edges: int, gpos: QPoint) -> None:
        self._win_gesture = {
            "edges": edges, "mouse0": gpos, "geom0": QRect(self.geometry()),
        }

    def update_window_resize(self, gpos: QPoint) -> None:
        g = self._win_gesture
        if not g:
            return
        edges = g["edges"]
        geom0: QRect = g["geom0"]
        dx = gpos.x() - g["mouse0"].x()
        dy = gpos.y() - g["mouse0"].y()
        left, top = geom0.left(), geom0.top()
        right, bottom = geom0.right(), geom0.bottom()

        if edges & EDGE_LEFT:
            left = min(geom0.left() + dx, geom0.right() - self.minimumWidth() + 1)
        if edges & EDGE_RIGHT:
            right = max(geom0.right() + dx, geom0.left() + self.minimumWidth() - 1)
        if edges & EDGE_TOP:
            top = min(geom0.top() + dy, geom0.bottom() - self.minimumHeight() + 1)
        if edges & EDGE_BOTTOM:
            bottom = max(geom0.bottom() + dy, geom0.top() + self.minimumHeight() - 1)

        new_geom = QRect(QPoint(left, top), QPoint(right, bottom))
        cur = self.geometry()
        d_left = new_geom.left() - cur.left()
        d_top = new_geom.top() - cur.top()
        self.setGeometry(new_geom)
        # содержимое «приклеено» к неподвижному углу: компенсируем прокрутку
        self._view.shift_content(d_left, d_top)

    def end_window_gesture(self) -> None:
        self._win_gesture = {}
        self.request_save()

    # ================================================================== #
    #   Команды
    # ================================================================== #

    def open_image_dialog(self, *_) -> None:
        path, _f = QFileDialog.getOpenFileName(
            self, "Открыть изображение", "",
            "Изображения (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff);;Все файлы (*.*)",
        )
        if path:
            if self._view.load_image(path):
                # применяем текущую прозрачность изображения к новой картинке
                self._view.set_image_opacity(self._state.image.opacity)
                self.update()
                self.request_save()
            else:
                QMessageBox.warning(self, "Калька", "Не удалось открыть изображение.")

    def reset_transform(self, *_) -> None:
        self._view.reset_transform()
        self.update()

    def rotate_image(self, delta_deg: float) -> None:
        self._view.rotate_image(delta_deg)

    def toggle_always_on_top(self, checked=None) -> None:
        on = (not self._state.window.always_on_top) if checked is None else bool(checked)
        self._state.window.always_on_top = on
        self._apply_window_flags(initial=False)
        self._sync_ui()
        self.request_save()

    def toggle_click_through(self, checked=None) -> None:
        on = (not self._state.window.click_through) if checked is None else bool(checked)
        self._state.window.click_through = on
        win_native.set_click_through(int(self.winId()), on)
        if on and self._tray.isVisible():
            self._tray.showMessage(
                "Калька — режим трафарета",
                "Сквозные клики включены. Вернуть управление: Ctrl+Alt+X "
                "или меню в системном трее.",
                QSystemTrayIcon.Information, 4000,
            )
        self._sync_ui()
        self.request_save()

    def toggle_show_border(self, checked=None) -> None:
        on = (not self._state.window.show_border) if checked is None else bool(checked)
        self._state.window.show_border = on
        self.update()
        self._view.viewport().update()
        self._sync_ui()
        self.request_save()

    def toggle_toolbar_enabled(self, checked=None) -> None:
        on = (not self._state.window.toolbar_enabled) if checked is None else bool(checked)
        self._state.window.toolbar_enabled = on
        self._intro = False
        self._sync_ui()
        self.request_save()

    def hide_toolbar(self, *_) -> None:
        self._state.window.toolbar_enabled = False
        self._intro = False
        self._toolbar.hide()
        self._sync_ui()
        self.request_save()

    def set_window_opacity_pct(self, pct: int) -> None:
        val = clamp(pct / 100.0, 0.15, 1.0)
        self._state.window.opacity = val
        self.setWindowOpacity(val)
        self.request_save()

    def set_image_opacity_pct(self, pct: int) -> None:
        val = clamp(pct / 100.0, 0.0, 1.0)
        self._state.image.opacity = val
        self._view.set_image_opacity(val)

    def deselect_image(self, *_) -> None:
        self._view.deselect()

    def show_help(self, *_) -> None:
        QMessageBox.information(self, "Калька — справка", HELP_TEXT)

    def quit_app(self, *_) -> None:
        self._do_save()
        self._tray.hide()
        QApplication.quit()

    # ================================================================== #
    #   Трей и горячие клавиши
    # ================================================================== #

    def _build_tray(self) -> None:
        self._tray = QSystemTrayIcon(self._make_tray_icon(), self)
        self._tray.setToolTip("Калька — трафарет поверх рабочего стола")
        menu = QMenu()

        menu.addAction("Открыть изображение…", self.open_image_dialog)
        menu.addSeparator()

        self._act_top = menu.addAction("Поверх всех окон")
        self._act_top.setCheckable(True)
        self._act_top.triggered.connect(self.toggle_always_on_top)

        self._act_click = menu.addAction("Сквозные клики (трафарет)")
        self._act_click.setCheckable(True)
        self._act_click.triggered.connect(self.toggle_click_through)

        self._act_border = menu.addAction("Показывать рамку")
        self._act_border.setCheckable(True)
        self._act_border.triggered.connect(self.toggle_show_border)

        self._act_toolbar = menu.addAction("Всплывающая панель")
        self._act_toolbar.setCheckable(True)
        self._act_toolbar.triggered.connect(self.toggle_toolbar_enabled)

        menu.addSeparator()
        menu.addAction("Сбросить трансформацию", self.reset_transform)
        menu.addAction("Повернуть на 90°", lambda: self.rotate_image(90))

        m_win = menu.addMenu("Прозрачность окна")
        for pct in (25, 50, 75, 100):
            m_win.addAction(f"{pct}%", lambda p=pct: self.set_window_opacity_pct(p))
        m_img = menu.addMenu("Прозрачность изображения")
        for pct in (25, 50, 75, 100):
            m_img.addAction(f"{pct}%", lambda p=pct: self.set_image_opacity_pct(p))

        menu.addSeparator()
        menu.addAction("Справка", self.show_help)
        menu.addAction("Выход", self.quit_app)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self.toggle_toolbar_enabled()

    def _build_shortcuts(self) -> None:
        def sc(seq, slot):
            s = QShortcut(QKeySequence(seq), self)
            s.activated.connect(slot)
            return s

        sc("Ctrl+O", self.open_image_dialog)
        sc("Ctrl+R", self.reset_transform)
        sc("Ctrl+T", self.toggle_always_on_top)
        sc("Ctrl+H", self.toggle_toolbar_enabled)
        sc("Ctrl+Shift+X", self.toggle_click_through)
        sc("F1", self.show_help)
        sc("Esc", self.deselect_image)
        sc("Ctrl+Q", self.quit_app)

    def _sync_ui(self) -> None:
        self._act_top.setChecked(self._state.window.always_on_top)
        self._act_click.setChecked(self._state.window.click_through)
        self._act_border.setChecked(self._state.window.show_border)
        self._act_toolbar.setChecked(self._state.window.toolbar_enabled)
        self._toolbar.sync(self._state)

    # ================================================================== #
    #   Всплывающая панель — авто-показ
    # ================================================================== #

    def _end_intro(self) -> None:
        self._intro = False

    def _tick_toolbar(self) -> None:
        w = self._state.window
        if not w.toolbar_enabled or w.click_through or not self.isVisible():
            self._toolbar.hide()
            return
        gp = QCursor.pos()
        inside = self.geometry().contains(gp)
        local = self.mapFromGlobal(gp)
        near_top = inside and 0 <= local.y() <= self._toolbar.height() + 18
        over_tb = self._toolbar.isVisible() and self._toolbar.geometry().contains(gp)
        if self._intro or near_top or over_tb:
            self._position_toolbar()
            if not self._toolbar.isVisible():
                self._toolbar.show()
            self._toolbar.raise_()
        else:
            self._toolbar.hide()

    def _position_toolbar(self) -> None:
        # верхнеуровневое окно — позиционируем в ГЛОБАЛЬНЫХ координатах,
        # по центру над верхним краем окна-трафарета
        self._toolbar.adjustSize()
        origin = self.mapToGlobal(QPoint(0, 0))
        x = origin.x() + (self.width() - self._toolbar.width()) // 2
        y = origin.y() + 8
        self._toolbar.move(x, y)

    # ================================================================== #
    #   Отрисовка рамки и подсказки для пустого окна
    # ================================================================== #

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(0, 0, -1, -1)

        if not self._view.item().has_image():
            paint_card(painter, QRectF(rect), 12)
            pen = QPen(ACCENT_SOFT, 1.5, Qt.CustomDashLine)
            pen.setDashPattern([6, 5])
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(rect).adjusted(6, 6, -6, -6), 9, 9)
            painter.setPen(MUTED)
            f = painter.font()
            f.setPointSize(10)
            painter.setFont(f)
            painter.drawText(
                self.rect().adjusted(18, 18, -18, -18), Qt.AlignCenter | Qt.TextWordWrap,
                tr("stencil_empty_hint"),
            )
        # The frame (Рамка) is drawn by the view's foreground: on top of the image.
        painter.end()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        tb = getattr(self, "_toolbar", None)
        if tb is not None and tb.isVisible():
            self._position_toolbar()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        tb = getattr(self, "_toolbar", None)
        if tb is not None and tb.isVisible():
            self._position_toolbar()

    def closeEvent(self, event) -> None:
        self._do_save()
        if self._hotkey_hwnd:
            win_native.unregister_hotkey(self._hotkey_hwnd, HOTKEY_ID)
        self._toolbar.close()
        self._tray.hide()
        super().closeEvent(event)

    # ------------------------------------------------------------------ #

    @staticmethod
    def _make_tray_icon() -> QIcon:
        pix = QPixmap(32, 32)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(ACCENT, 2))
        p.setBrush(ACCENT_SOFT)
        p.drawRoundedRect(5, 5, 22, 22, 4, 4)
        p.setBrush(QColor(230, 235, 245, 220))
        p.drawRect(11, 11, 10, 10)
        p.end()
        return QIcon(pix)
