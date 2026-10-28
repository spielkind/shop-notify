"""The web UI in ui/ must keep up with the poller: its texts and its version."""

import dataclasses
import json
import re
import tomllib
from pathlib import Path

from shop_notify.db import _MISSING_STATES, Poll
from shop_notify.models import EVENTS

ROOT = Path(__file__).parent.parent
UI = ROOT / "ui"
TYPES = (UI / "src" / "lib" / "i18n" / "types.ts").read_text(encoding="utf-8")
DB = (ROOT / "src" / "shop_notify" / "db.py").read_text(encoding="utf-8")


def _ui_list(name: str) -> set[str]:
    """A ``const NAME = ['a', 'b'] as const`` list of ui/src/lib/i18n/types.ts."""
    match = re.search(rf"export const {name} = \[(.*?)\] as const;", TYPES, re.S)
    assert match, f"{name} not found"
    return set(re.findall(r"'([a-z_]+)'", match[1]))


def _recorded_events() -> set[str]:
    """The event names db.py writes into listing_events."""
    literal = set(re.findall(r'self\._event\(\s*[^,]+,\s*[^,]+,\s*"([a-z_]+)"', DB, re.S))
    conditional = set(re.findall(r'"([a-z_]+)" if sent else "([a-z_]+)"', DB)[0])
    imported = set(re.findall(r"SELECT listing_id, first_seen, '([a-z_]+)' FROM listings", DB))
    return literal | conditional | imported | set(_MISSING_STATES)


def test_the_ui_names_every_recorded_event() -> None:
    recorded = _recorded_events()
    # the search above still finds the events (and isn't silently broken)
    assert {"new", "missing", "gone", "notified", "recorded", "imported", "sold_out"} <= recorded
    assert recorded == _ui_list("EVENT_NAMES")


def test_the_ui_names_every_notified_event() -> None:
    assert _ui_list("NOTICE_KINDS") == set(EVENTS)


def test_the_ui_counts_are_fields_of_a_poll() -> None:
    fields = {field.name for field in dataclasses.fields(Poll)}
    assert _ui_list("COUNT_KEYS") <= fields


def test_the_ui_has_the_version_of_the_package() -> None:
    package = json.loads((UI / "package.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert package["version"] == pyproject["project"]["version"], (
        "Set the same version in ui/package.json (npm version --no-git-tag-version X.Y.Z)"
    )
