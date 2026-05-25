from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRANSFER_JSON = REPO_ROOT / "data" / "manual_transfers.json"


@dataclass(frozen=True)
class TransferEntry:
    place: str
    target: str
    p50_min: float
    p90_min: float
    advice: str
    source: str
    confidence: str

    def to_dict(self) -> dict[str, object]:
        return {
            "place": self.place,
            "target": self.target,
            "p50_min": self.p50_min,
            "p90_min": self.p90_min,
            "advice": self.advice,
            "source": self.source,
            "confidence": self.confidence,
        }


class ManualTransferStore:
    def __init__(self, json_path: str | Path = DEFAULT_TRANSFER_JSON) -> None:
        self.json_path = Path(json_path)
        self.entries = self._load_entries()

    def _load_entries(self) -> list[TransferEntry]:
        if not self.json_path.exists():
            return []
        with self.json_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        return [
            TransferEntry(
                place=item["place"],
                target=item["target"],
                p50_min=float(item["p50_min"]),
                p90_min=float(item["p90_min"]),
                advice=item.get("advice", ""),
                source=item.get("source", "manual_label"),
                confidence=item.get("confidence", "medium"),
            )
            for item in payload.get("entries", [])
        ]

    def find(self, place: str, target_contains: str | None = None) -> TransferEntry | None:
        place_key = normalize_place(place)
        target_key = normalize_text(target_contains or "")
        candidates = [entry for entry in self.entries if normalize_place(entry.place) == place_key]
        if target_key:
            for entry in candidates:
                if target_key in normalize_text(entry.target):
                    return entry
        return candidates[0] if candidates else None

    def station_access_buffer(self, station: str, mode_hint: str = "") -> dict[str, object]:
        entry = self.find(station, "进")
        if entry is not None:
            return {
                "p50_min": entry.p50_min,
                "p90_min": entry.p90_min,
                "source": "manual_label",
                "entry": entry.to_dict(),
            }

        default = 18 if mode_hint == "driving" else 15
        return {
            "p50_min": max(8, default - 5),
            "p90_min": default,
            "source": "default_station_buffer",
            "entry": None,
        }

    def transfer_buffer(self, station: str, target_contains: str | None = None) -> dict[str, object]:
        entry = self.find(station, target_contains)
        if entry is not None:
            return {
                "p50_min": entry.p50_min,
                "p90_min": entry.p90_min,
                "source": "manual_label",
                "entry": entry.to_dict(),
            }
        return {
            "p50_min": 8,
            "p90_min": 15,
            "source": "default_same_station_transfer",
            "entry": None,
        }


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def normalize_place(value: str) -> str:
    text = normalize_text(value)
    text = re.sub(r"^\d+", "", text)
    text = text.replace("火车站", "站")
    if text.endswith("站"):
        text = text[:-1]
    return text
