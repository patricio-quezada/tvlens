"""The public changelog, read from CHANGELOG.md at the repo root.

CHANGELOG.md uses a small, fixed slice of Markdown, so it is rendered here
rather than through a Markdown library (one less dependency to keep patched):
"## " starts a release, a line that is only **bold** starts a section, "- "
is a bullet, and inside text **bold**, *italic*, `code` and bare https://
links. Everything is HTML-escaped before any of that is applied, so nothing
in the file can inject markup.
"""

import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.utils.html import escape
from django.utils.safestring import mark_safe

CHANGELOG_PATH = Path(settings.BASE_DIR) / "CHANGELOG.md"

_BOLD = re.compile(r"\*\*(.+?)\*\*")
# Italic text never holds a tag, so a stray * can't pair across a </strong>.
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)([^*<>]+?)(?<!\s)\*(?![\w*])")
# Code spans and links are cut out of the raw line first, so the bold and
# italic rules never reach inside them.
_TOKEN = re.compile(r"`([^`]+)`|(https://[^\s<>\"'`]+)")
_SECTION = re.compile(r"^\*\*([^*]+)\*\*$")


def _text(raw):
    html = escape(raw)
    html = _BOLD.sub(r"<strong>\1</strong>", html)
    return _ITALIC.sub(r"<em>\1</em>", html)


def _link(url):
    """The link, and any trailing punctuation that belongs to the sentence."""
    tail = ""
    while url and (url[-1] in ".,;:!?" or (url[-1] == ")" and url.count(")") > url.count("("))):
        url, tail = url[:-1], url[-1] + tail
    return f'<a href="{escape(url)}">{escape(url)}</a>' + escape(tail)


def _inline(text):
    parts, last = [], 0
    for match in _TOKEN.finditer(text):
        parts.append(_text(text[last : match.start()]))
        if match[1] is not None:
            parts.append(f"<code>{escape(match[1])}</code>")
        else:
            parts.append(_link(match[2]))
        last = match.end()
    parts.append(_text(text[last:]))
    return "".join(parts)


def parse(text):
    """Releases, newest first: [{"title", "sections": [{"name", "items"}]}].

    Lines before the first "## " (the file's own heading and intro) are not
    part of any release and are skipped.
    """
    releases = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            releases.append({"title": line[3:].strip(), "sections": []})
        elif not releases or not line:
            continue
        elif match := _SECTION.match(line):
            releases[-1]["sections"].append({"name": match[1], "items": []})
        elif line.startswith("- ") and releases[-1]["sections"]:
            item = mark_safe(_inline(line[2:].strip()))
            releases[-1]["sections"][-1]["items"].append(item)
    return releases


@lru_cache(maxsize=1)
def releases():
    """The parsed file. It only changes with a deploy, so read it once."""
    return parse(CHANGELOG_PATH.read_text(encoding="utf-8"))
