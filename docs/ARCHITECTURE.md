# Архитектура

OlegPainter — настольное приложение для Windows на Python 3.13. Основное окно сделано на Qt Quick/QML (PySide6), вычисления выполняет Python-движок с numpy, OpenCV и numba, мышь управляется через общий транспорт ввода.

```
QML (ui/quick/qml)  ──►  Presenter (ui/quick/presenter.py)  ──►  ApplicationController (application/)
                                                                      │
                     экранные инструменты (ui/overlays, ui/kalka,     ▼
                     ui/widgets: трафарет, HUD, калибровки)      PainterService (ui/services/)
                                                                      │
                                                                      ▼
                                                     движок OlegPainter (engine/olegpainter/)
                                                     ИИ по выбору (engine/ai/)
                                                                      │
                                                                      ▼
                             транспорт ввода, захват экрана, целевое окно (infrastructure/)
```

## Слои

| Папка | Что в ней | Главное правило |
| --- | --- | --- |
| `ui/quick/` | окно QML, `presenter.py` — мост между QML и контроллером, переводы `i18n/en.json` | в QML нет бизнес-логики: только отображение и команды |
| `ui/overlays/`, `ui/kalka/`, `ui/widgets/` | экранные инструменты поверх других программ: выбор области и палитры, трафарет, HUD | виджеты трогаются только из главного потока; экранные и UI-координаты различаются, учитывается DPI |
| `application/` | общий контроллер, неизменяемое состояние, каталог настроек, места (игры), профили, «Быстрый старт», ИИ-центр | не зависит от Qt там, где это возможно |
| `ui/services/` | `PainterService` и его части: фоновые операции, конфиги, жизненный цикл рисунка, обработка картинки | создаётся после приложения Qt, в главном потоке |
| `engine/olegpainter/` | подготовка картинки, цвета, маршруты, исполнение рисунка, калибровки | все движения мыши идут через `engine._input` |
| `engine/ai/` | каталог моделей с контрольными суммами, загрузка, запуск ONNX (DirectML → CPU) | ИИ выключен по умолчанию, модели ставит пользователь |
| `infrastructure/` | транспорт ввода, драйвер, целевое окно, захват экрана, сессии захватов, единственный экземпляр | единая граница с Windows |

## Как рисуется картинка

1. **Подготовка** (`engine/olegpainter/prep_pipeline.py`, `cluster_cleanup.py`, `color_spaces.py`): уменьшение до сетки клеток, квантование цветов в OKLab, подбор к палитре цели по CIEDE2000, очистка мелких областей.
2. **Порядок и маршрут** (`core.py`, `path_planning.py`, `line_cover.py`, `outline_fill.py`, `dynamic_brush.py`): порядок цветов и областей, маршрут внутри области — DFS, «Прямые отрезки», «Контур + заливка», «Только контур»; автокисть выбирает размеры.
3. **Выбор цвета в цели** (`color_picking.py`, `wheel_picker.py`, `palette_capture.py`): образцы палитры, поле HEX, цветовые круги и кольца.
4. **Исполнение** (`core.py`, `input_timing.py`, `drawing_events.py`, `ui/services/drawing_lifecycle.py`): движения и паузы, события с номером запуска, пауза и остановка.
5. **Ввод** (`infrastructure/automation_transport.py`, `input_ownership.py`): единственный путь к мыши и клавиатуре; рисование и обучение владеют вводом, пока обе кнопки мыши не отпущены.

## Где что менять

| Задача | Файлы |
| --- | --- |
| Запуск и пути | `main.py`, `quick_main.py`, `app_paths.py`, `ui/helpers/app_setup.py` |
| Окно и страницы | `ui/quick/qml/`, `ui/quick/presenter.py`; рендер страниц без ввода — `tools/preview_quick.py` |
| Новая настройка | `application/settings.py` (тип, диапазон, пояснение); QML строит группы из каталога |
| Новая игра или программа | `application/place_catalog.py` — пресет места: способ цвета, маршрут, паузы |
| Подготовка картинки | `engine/olegpainter/prep_pipeline.py`, `cluster_cleanup.py`, `application/image_filters.py` |
| Маршруты | `engine/olegpainter/core.py`, `path_planning.py`, `line_cover.py`, `outline_fill.py` |
| Выбор цвета | `engine/olegpainter/color_picking.py`, `wheel_picker.py`, `ui/overlays/palette_calibration.py` |
| Автокисть и скорость | `engine/olegpainter/dynamic_brush.py`, `brush_measurement.py`, `input_timing.py`, `application/brush_learning.py` |
| Ввод и драйвер | `infrastructure/automation_transport.py`, `input_driver.py`, `automation_target.py` |
| Захват экрана | `infrastructure/screen_capture.py`, `window_sampling.py`, `capture_work.py` |
| Трафарет, HUD, экранные инструменты | `ui/kalka/`, `ui/widgets/hud_overlay.py`, `ui/overlays/` |
| ИИ | `engine/ai/catalog.json`, `models.py`, `runtime.py`, `vision.py`, `application/ai.py`, `ui/quick/qml/AiPage.qml` |
| Переводы | `ui/quick/i18n/en.json` (QML и сообщения), `ui/i18n.py` |

## Данные пользователя

Настройки, профили, калибровки и картинки сессии лежат в `configs/`, журналы — в `logs/`, ИИ-модели — в `models/` рядом с программой. Если папка недоступна для записи, всё уходит в `%LOCALAPPDATA%\OlegPainter`. Формат конфигов меняется только вместе с миграцией старых файлов (`ui/helpers/config_migration.py`).

## Проверки

Тесты лежат в `tests/` и запускаются без видимых окон и без настоящего ввода: на временных конфигах, с записывающим транспортом вместо мыши и с симулятором холста `tests/helpers/sim_pen.py`. Как запускать — в [DEVELOPMENT.md](DEVELOPMENT.md).
