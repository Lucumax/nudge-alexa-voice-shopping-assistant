"""User-facing interface strings live in copy/strings.json. This module loads them and fills in
their placeholders."""
from __future__ import annotations
import json
import os
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRINGS_PATH = os.path.join(ROOT, "copy", "strings.json")

_cache: dict | None = None


def load(path: str = STRINGS_PATH) -> dict:
    global _cache
    if _cache is None or path != STRINGS_PATH:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)["strings"]
        if path != STRINGS_PATH:
            return data
        _cache = data
    return _cache


def t(key: str, **kw) -> str:
    entry = load()[key]  # KeyError on a missing key is deliberate: tests catch it
    return entry["text"].format(**kw) if kw else entry["text"]


def plain(key: str) -> str:
    return load()[key]["text"]


def for_client() -> dict:
    """Raw templates for the front end (it only fills {placeholders})."""
    return {k: v["text"] for k, v in load().items()}


def money(minor: int) -> str:
    return f"${minor / 100:,.2f}"


def fdate(d: date) -> str:
    return f"{d:%a}, {d:%b} {d.day}"


def fdate_long(d: date) -> str:
    return f"{d:%b} {d.day}, {d.year}"
