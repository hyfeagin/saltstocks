"""
CSV import for kanban boards — built for Notion's database export
(••• → Export → Markdown & CSV), but works with any CSV that has a header row.

Flow: parse_csv() → auto_map() suggests a header→field mapping → the user
adjusts it in the UI → build_cards() produces dicts for add_cards_batch().
"""
from __future__ import annotations

import csv
import io
import re
from typing import Optional

# Mapping targets shown in the UI dropdown, in display order.
TARGETS = {
    "": "(ignore)",
    "title": "Title",
    "company": "Company",
    "location": "Location",
    "remote_type": "Remote / hybrid / onsite",
    "url": "Listing URL",
    "salary_text": "Salary",
    "source": "Source",
    "external_id": "External job ID",
    "posted_date": "Date posted",
    "fit_score": "Fit score (1–5)",
    "description": "Description",
    "notes": "Notes",
    "_column": "Status → board column",
    "_append": "(append to notes)",
}

_ALIASES = {
    "title": {"name", "title", "job title", "job", "position", "role"},
    "company": {"company", "company name", "employer", "organization"},
    "location": {"location", "city", "where"},
    "remote_type": {"remote", "remote type", "work type", "workplace", "work arrangement", "remote/hybrid"},
    "url": {"url", "link", "job link", "job url", "listing", "posting", "apply link"},
    "salary_text": {"salary", "pay", "compensation", "salary range", "pay range"},
    "source": {"source", "site", "board", "job board", "platform"},
    "external_id": {"job id", "external id", "id"},
    "posted_date": {"posted", "date posted", "posted date", "posted on", "date"},
    "fit_score": {"fit", "fit score", "score", "rating", "match", "match score"},
    "description": {"description", "summary", "job description"},
    "notes": {"notes", "note", "comments", "ai notes"},
    "_column": {"status", "stage", "column", "state"},
}


def parse_csv(text: str) -> tuple[list[str], list[dict[str, str]]]:
    text = text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    headers = [h for h in (reader.fieldnames or []) if h is not None]
    if not headers:
        raise ValueError("CSV has no header row.")
    rows = [
        {h: (row.get(h) or "").strip() for h in headers}
        for row in reader
        if any((v or "").strip() for v in row.values() if isinstance(v, str))
    ]
    return headers, rows


def auto_map(headers: list[str]) -> dict[str, str]:
    """Suggest a target per header. Each field is used at most once;
    unrecognised headers default to '(append to notes)' so nothing is lost."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for h in headers:
        key = re.sub(r"\s+", " ", h.strip().lower())
        target = next(
            (f for f, names in _ALIASES.items() if key in names and f not in used), "_append"
        )
        mapping[h] = target
        if target != "_append":
            used.add(target)
    if "title" not in used and headers:
        # Notion's first column is always the title property.
        first = headers[0]
        if mapping[first] != "_append":
            used.discard(mapping[first])
        mapping[first] = "title"
    return mapping


def _parse_fit(raw: str) -> Optional[int]:
    if not raw:
        return None
    stars = raw.count("⭐") or raw.count("★")
    if stars:
        return min(stars, 5)
    m = re.search(r"\d+(\.\d+)?", raw)
    if not m:
        return None
    val = float(m.group())
    if "/10" in raw or val > 5:
        val = val / 2
    val = round(val)
    return val if 1 <= val <= 5 else None


def _parse_remote(raw: str) -> Optional[str]:
    low = raw.lower()
    if "hybrid" in low:
        return "hybrid"
    if "remote" in low:
        return "remote"
    if "site" in low or "office" in low or "person" in low:
        return "onsite"
    return None


def build_cards(rows: list[dict[str, str]], mapping: dict[str, str]) -> list[dict]:
    """Turn CSV rows into card dicts. Values that don't fit a typed field
    (fit score, remote type) are kept in notes rather than dropped."""
    cards = []
    for row in rows:
        card: dict = {}
        extras: list[str] = []
        for header, target in mapping.items():
            val = row.get(header, "").strip()
            if not target or not val:
                continue
            if target == "_append":
                extras.append(f"{header}: {val}")
            elif target == "fit_score":
                score = _parse_fit(val)
                if score is None:
                    extras.append(f"{header}: {val}")
                else:
                    card["fit_score"] = score
            elif target == "remote_type":
                rt = _parse_remote(val)
                if rt is None:
                    extras.append(f"{header}: {val}")
                else:
                    card["remote_type"] = rt
            elif target in card:
                card[target] = f"{card[target]}\n{val}"
            else:
                card[target] = val
        if extras:
            card["notes"] = "\n".join(filter(None, [card.get("notes"), *extras]))
        if not card.get("title"):
            card["title"] = card.get("company") and f"(untitled) — {card['company']}"
        cards.append(card)
    return cards
