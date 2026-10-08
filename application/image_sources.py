"""Where a picture comes from besides a file (IMAGE-SOURCE-001).

Image search runs in the user's own browser: an embedded search needs a paid
API or our own server. The program's part is to open the search with useful
filters and to take the found picture back in one step — a drop from the
browser, a copied picture or a copied link. Qt-free and without network.
"""
from __future__ import annotations

import base64
import binascii
import re
from html import unescape
from urllib.parse import quote_plus, urlsplit

# Filters: "large" is always applied (small pictures draw badly); kinds match
# what drawing needs: any picture, clipart, outlines, transparent background.
KINDS = (
    ("any", "Любые"),
    ("clipart", "Рисунки"),
    ("lineart", "Контуры"),
    ("transparent", "Без фона"),
)

SEARCH_SERVICES = (
    dict(id="yandex", label="Яндекс Картинки",
         url="https://yandex.ru/images/search?text={q}&isize=large",
         kinds={"clipart": "&type=clipart", "lineart": "&type=lineart", "transparent": "&itype=png"}),
    dict(id="google", label="Google Картинки",
         url="https://www.google.com/search?tbm=isch&q={q}&tbs=isz:l",
         kinds={"clipart": ",itp:clipart", "lineart": ",itp:lineart", "transparent": ",ic:trans"}),
    dict(id="bing", label="Bing",
         url="https://www.bing.com/images/search?q={q}&qft=+filterui:imagesize-large",
         kinds={"clipart": "+filterui:photo-clipart", "lineart": "+filterui:photo-linedrawing",
                "transparent": "+filterui:photo-transparent"}),
    dict(id="duckduckgo", label="DuckDuckGo",
         url="https://duckduckgo.com/?q={q}&iax=images&ia=images&iaf=size:Large",
         kinds={"clipart": ",type:clipart", "lineart": ",type:line", "transparent": ",type:transparent"}),
    dict(id="pinterest", label="Pinterest",
         url="https://www.pinterest.com/search/pins/?q={q}", kinds={}),
    dict(id="openverse", label="Openverse (свободные лицензии)",
         url="https://openverse.org/search/image?q={q}&size=large", kinds={}),
)

# A service without the kind filter gets these words added to the query, in the
# interface language: Russian words in an English search found Russian pages.
_KIND_WORDS = {"ru": {"clipart": "рисунок", "lineart": "контур раскраска", "transparent": "png без фона"},
               "en": {"clipart": "clipart", "lineart": "line art coloring page", "transparent": "transparent png"}}

MAX_QUERY = 200


def service(service_id: str) -> dict:
    found = next((s for s in SEARCH_SERVICES if s["id"] == service_id), None)
    if found is None:
        raise ValueError("Неизвестный сервис поиска картинок.")
    return found


def search_url(service_id: str, query: str, kind: str = "any", language: str = "ru") -> str:
    """The results page of `service_id` for `query`, large pictures of `kind`."""
    entry = service(service_id)
    if kind not in dict(KINDS):
        raise ValueError("Неизвестный вид картинок.")
    query = " ".join(str(query or "").split())[:MAX_QUERY]
    if not query:
        raise ValueError("Напишите, что хотите нарисовать.")
    suffix = entry["kinds"].get(kind, "")
    if kind != "any" and not suffix:
        query = f"{query} {_KIND_WORDS.get(language, _KIND_WORDS['ru'])[kind]}"
    return entry["url"].format(q=quote_plus(query)) + suffix


def view() -> dict:
    return dict(services=[dict(id=s["id"], label=s["label"]) for s in SEARCH_SERVICES],
                kinds=[dict(id=k, label=label) for k, label in KINDS])


def image_url_from_text(text: str) -> str | None:
    """A copied or dropped link to a picture (or a page with one), else None."""
    text = str(text or "").strip()
    if text.startswith("data:image/"):
        return text
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1:  # several lines are a copied page fragment, not a link
        return None
    text = lines[0]
    if re.fullmatch(r"https?://\S+", text, flags=re.IGNORECASE) and urlsplit(text).netloc:
        return text
    return None


def image_url_from_html(html: str) -> str | None:
    """The first <img src> of a fragment dragged out of a browser."""
    match = re.search(r"<img\b[^>]*?\bsrc\s*=\s*(\"([^\"]+)\"|'([^']+)'|([^\s>]+))", str(html or ""), re.IGNORECASE)
    if not match:
        return None
    url = unescape(match.group(2) or match.group(3) or match.group(4) or "").strip()
    return image_url_from_text(url)


def decode_data_url(url: str) -> bytes:
    """Bytes of a data:image/...;base64,... link (browsers drag small pictures so)."""
    header, _, payload = str(url).partition(",")
    if not header.startswith("data:image/") or not payload:
        raise ValueError("Это не картинка.")
    if header.endswith(";base64"):
        try:
            return base64.b64decode(payload, validate=False)
        except (binascii.Error, ValueError) as error:
            raise ValueError("Картинка в ссылке повреждена.") from error
    from urllib.parse import unquote_to_bytes
    return unquote_to_bytes(payload)


def origin_label(url: str) -> str:
    """Short source name for the session: the site the picture came from."""
    if str(url).startswith("data:"):
        return "Картинка из браузера"
    host = urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host or "Картинка из интернета"

