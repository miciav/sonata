import re

_SLUG_INVALID_CHARS = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    return _SLUG_INVALID_CHARS.sub("-", title.lower()).strip("-")
