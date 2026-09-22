"""Versioned, inspectable evidence retained across fresh game profiles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_SCHEMA_VERSION = 1
_MAX_ENTRIES = 200
DEFAULT_LEDGER_PATH = Path(__file__).resolve().parents[2] / "runs" / "planner-memory.json"


class EvidenceLedger:
    """Persist observed dead ends and the outcomes of planner hypotheses."""

    def __init__(self, path: Path = DEFAULT_LEDGER_PATH) -> None:
        self.path = path
        self._data = self._load()

    def _fresh(self) -> dict[str, Any]:
        return {"schema_version": _SCHEMA_VERSION, "entries": []}

    def _load(self) -> dict[str, Any]:
        try:
            document = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return self._fresh()
        if (
            not isinstance(document, dict)
            or document.get("schema_version") != _SCHEMA_VERSION
            or not isinstance(document.get("entries"), list)
            or not all(isinstance(entry, dict) for entry in document["entries"])
        ):
            return self._fresh()
        return document

    def _write(self) -> None:
        self._data["entries"] = self._data["entries"][-_MAX_ENTRIES:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._data, indent=2, sort_keys=True) + "\n")
        temporary.replace(self.path)

    @staticmethod
    def _encoded_map(map_id: tuple[int, int] | None) -> list[int] | None:
        return list(map_id) if map_id is not None else None

    def summary(
        self, stage: str, map_id: tuple[int, int] | None
    ) -> dict[str, list[dict[str, Any]]]:
        encoded_map = self._encoded_map(map_id)
        relevant = [
            entry for entry in self._data["entries"] if entry.get("stage") == stage
        ]
        return {
            "verified": [
                dict(entry) for entry in relevant if entry.get("status") == "verified"
            ],
            "rejected": [
                dict(entry) for entry in relevant if entry.get("status") == "rejected"
            ],
            "dead_ends": [
                dict(entry)
                for entry in relevant
                if entry.get("status") == "dead_end"
                and entry.get("map_id") == encoded_map
            ],
        }

    def record_dead_end(
        self,
        stage: str,
        map_id: tuple[int, int] | None,
        action_id: str,
        label: str,
        reason: str | None,
        count: int,
    ) -> None:
        encoded_map = self._encoded_map(map_id)
        for entry in self._data["entries"]:
            if (
                entry.get("status") == "dead_end"
                and entry.get("stage") == stage
                and entry.get("map_id") == encoded_map
                and entry.get("action_id") == action_id
            ):
                entry.update(label=label, reason=reason, count=max(entry.get("count", 0), count))
                self._write()
                return
        self._data["entries"].append(
            {
                "status": "dead_end",
                "stage": stage,
                "map_id": encoded_map,
                "action_id": action_id,
                "label": label,
                "reason": reason,
                "count": count,
            }
        )
        self._write()

    def record_hypothesis(
        self,
        stage: str,
        map_id: tuple[int, int] | None,
        hint: str,
        action_id: str,
    ) -> None:
        self._data["entries"].append(
            {
                "status": "unverified",
                "stage": stage,
                "map_id": self._encoded_map(map_id),
                "action_id": action_id,
                "hint": hint,
            }
        )
        self._write()

    def reject_hypothesis(self, stage: str, action_id: str) -> None:
        entry = self._latest_hypothesis(stage, action_id)
        if entry is not None:
            entry["status"] = "rejected"
            self._write()

    def verify_hypothesis(self, stage: str, action_id: str, evidence: str) -> None:
        entry = self._latest_hypothesis(stage, action_id)
        if entry is not None:
            entry["status"] = "verified"
            entry["evidence"] = evidence
            self._write()

    def _latest_hypothesis(self, stage: str, action_id: str) -> dict[str, Any] | None:
        for entry in reversed(self._data["entries"]):
            if (
                entry.get("status") == "unverified"
                and entry.get("stage") == stage
                and entry.get("action_id") == action_id
            ):
                return entry
        return None
