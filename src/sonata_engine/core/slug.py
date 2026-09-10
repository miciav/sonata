"""Turning a task title into the addressable slug used by `Selection`."""

import re

_SLUG_INVALID_CHARS = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    """Return `title` as a lowercase, hyphen-only slug.

    Lowercases first, then collapses each run of characters outside `[a-z0-9]`
    into a single hyphen and trims the hyphens off both ends. A title made
    entirely of non-alphanumeric characters therefore slugs to the empty string.
    """
    return _SLUG_INVALID_CHARS.sub("-", title.lower()).strip("-")
