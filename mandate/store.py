"""The firm's records, kept as one JSON file in data/ (gitignored)."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from datetime import date
from pathlib import Path

from .seed import build_state


class StoreError(RuntimeError):
    """The records file exists but can't be read. It is never overwritten silently:
    it holds what was paid, and its run id keeps PayPal's duplicate protection working."""


class Store:
    def __init__(self, path: Path, state: dict):
        self.path = path
        self.state = state

    @classmethod
    def open(cls, path: Path, today: date) -> "Store":
        if path.exists():
            try:
                with path.open(encoding="utf-8") as fh:
                    state = json.load(fh)
                if not isinstance(state, dict) or "run_id" not in state:
                    raise ValueError("not a Mandate records file")
                return cls(path, state)
            except (OSError, ValueError) as exc:
                raise StoreError(
                    f"{path} can't be read ({exc.__class__.__name__}). It has been left as it is. "
                    "Fix it, or run `python app.py reset` to start again from the sample "
                    "(the old file is kept beside it as .bak).") from exc
        return cls.fresh(path, today)

    @classmethod
    def fresh(cls, path: Path, today: date) -> "Store":
        """Start again from the sample, keeping any existing file as a .bak."""
        if path.exists():
            path.replace(path.with_name(path.name + ".bak"))
        store = cls(path, cls._fresh(today))
        store.save()
        return store

    @staticmethod
    def _fresh(today: date) -> dict:
        state = build_state(today)
        # A fresh run tag per reset keeps PayPal's idempotency keys and
        # payout batch ids unique across repeated demo runs.
        state["run_id"] = secrets.token_hex(4)
        return state

    def reset(self, today: date) -> None:
        if self.path.exists():
            self.path.replace(self.path.with_name(self.path.name + ".bak"))
        rev = self.state.get("rev", 0)
        self.state = self._fresh(today)
        self.state["rev"] = rev  # keeps counting up, so an open page sees the reset as new
        self.save()

    def save(self) -> None:
        # A revision number, so an open dashboard can tell something changed (for example
        # a request from another agent) without comparing the whole state.
        self.state["rev"] = self.state.get("rev", 0) + 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(self.state, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
