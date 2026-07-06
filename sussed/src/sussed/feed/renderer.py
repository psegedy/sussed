"""Pure renderer for the self-contained static listing feed."""

from __future__ import annotations

import html
import json
from importlib import resources
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sussed.feed.models import FeedContext, FeedData

_SENTINELS = (
    "__PAGE_TITLE__",
    "__GENERATED_AT__",
    "__FRESH_DAYS__",
    "__STYLES__",
    "__SCRIPT__",
    "__FEED_DATA_JSON__",
)


def _asset_text(name: str) -> str:
    """Load a packaged feed asset as UTF-8 text."""
    return resources.files("sussed.feed").joinpath(name).read_text(encoding="utf-8")


def _harden_json_for_script(payload_json: str) -> str:
    """Escape JSON characters that can break out of an HTML script tag."""
    return (
        payload_json.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _replace_once(template: str, sentinel: str, value: str) -> str:
    """Replace a template sentinel after asserting it appears exactly once."""
    count = template.count(sentinel)
    if count != 1:
        msg = f"Expected sentinel {sentinel!r} exactly once, found {count}."
        raise ValueError(msg)
    return template.replace(sentinel, value)


def render_feed(feed_data: FeedData, context: FeedContext) -> str:
    """Render feed data to a single self-contained HTML document.

    Args:
        feed_data: Normalized feed posts plus ordered tab id lists.
        context: Feed metadata such as title, generation timestamp, and filters.

    Returns:
        A complete HTML document with CSS, JavaScript, and hardened JSON inlined.

    Raises:
        ValueError: If any required template sentinel is missing or duplicated.
    """
    template = _asset_text("template.html")
    css = _asset_text("feed.css")
    script = _asset_text("feed.js")

    for sentinel in _SENTINELS:
        count = template.count(sentinel)
        if count != 1:
            msg = f"Expected sentinel {sentinel!r} exactly once, found {count}."
            raise ValueError(msg)

    payload = feed_data.model_dump(mode="json")
    payload["context"] = context.model_dump(mode="json")
    payload_json = json.dumps(payload, ensure_ascii=False, default=str)
    hardened_json = _harden_json_for_script(payload_json)

    replacements = {
        "__PAGE_TITLE__": html.escape(context.title, quote=True),
        "__GENERATED_AT__": context.generated_at.isoformat(),
        "__FRESH_DAYS__": str(context.fresh_days),
        "__STYLES__": css,
        "__SCRIPT__": script,
        "__FEED_DATA_JSON__": hardened_json,
    }

    rendered = template
    for sentinel, value in replacements.items():
        rendered = _replace_once(rendered, sentinel, value)
    return rendered
