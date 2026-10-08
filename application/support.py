"""Where users get help: the links of the Help page (HELP-001). Data only.

Edit this list to change a link; `enabled=False` shows the item as «скоро»
without opening it.
"""
from __future__ import annotations

APP_VERSION = "1.4"

LINKS = (
    dict(id="personal", title="Написать автору", detail="Личный Telegram: вопросы и ошибки",
         url="https://t.me/Andreiii_Official"),
    dict(id="chat", title="Чат пользователей", detail="Спросите других и поделитесь рисунками",
         url="https://t.me/+QDMLHcQ42L9iMzYy"),
    dict(id="channel", title="Канал в Telegram", detail="Новости и обновления программы",
         url="https://t.me/olegroblox1"),
    dict(id="youtube", title="Видео-гайды на YouTube", detail="Как настроить и рисовать",
         url="https://www.youtube.com/@olegroblox1"),
    dict(id="boosty", title="Boosty", detail="Поддержать развитие проекта",
         url="https://boosty.to/olegroblox1"),
)


# «Что нового»: shown once to people who used an earlier build of this window,
# and always on the Help page. The highlights of CHANGELOG.md against 1.3.
WHATS_NEW = (
    "OlegPainter теперь бесплатный и с открытым исходным кодом (GPL-3.0): без ключей и активации.",
    "Новое окно: тёмная и светлая темы, русский и английский языки, компактный режим. «Быстрый старт» ведёт от картинки до рисунка с подсказками для каждой игры.",
    "Готовые места: Speed Draw!, Spray Paint!, Draw & Donate, Draw Me!, Gartic Phone и любая другая программа. Свои игры и программы — в «Моих местах».",
    "Картинка откуда угодно: файл, буфер обмена, перетаскивание из браузера, ссылка, снимок экрана и поиск в интернете.",
    "«Обработка картинки»: фон автоматически, нейросетью, по цвету или кистью; фильтры, цвет и свет, поворот, отмена шагов.",
    "Пять способов выбора цвета, точные цвета в OKLab и CIEDE2000, смешивание оттенков из готовых цветов.",
    "Программа сама изучает кисть и подбирает скорость. Новые маршруты: «Прямые отрезки», «Контур + заливка» ведром, «Только контур».",
    "Проверка холста: уже нарисованное пропускается, пропуски дорисовываются. Редактор трафарета (Alt+F2) и HUD с оставшимся временем (Ctrl+Shift+P).",
    "Нейросети по желанию, прямо на компьютере: фон, объект щелчком, глубина, контуры, стирание и увеличение.",
    "Новые клавиши по умолчанию: F3 — старт и пауза, F4 — стоп, F2 — трафарет. Драйвер мыши ставится и удаляется прямо из программы.",
)


def link(link_id: str) -> dict:
    for item in LINKS:
        if item["id"] == link_id:
            return item
    raise ValueError("Неизвестная ссылка.")


def view() -> list[dict]:
    return [dict(id=item["id"], title=item["title"], detail=item["detail"],
                 enabled=item.get("enabled", True)) for item in LINKS]
