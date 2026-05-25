from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE = REPO_ROOT / "data" / "amap_cache.json"


MOCK_ETA_MINUTES: dict[tuple[str, str, str], int] = {
    ("driving", "北京中关村学院", "北京西站"): 44,
    ("driving", "北京中关村学院", "北京丰台站"): 48,
    ("driving", "北京中关村学院", "北京南站"): 50,
    ("driving", "北京中关村学院", "北京朝阳站"): 55,
    ("driving", "北京中关村学院", "清河站"): 24,
    ("transit", "北京中关村学院", "北京西站"): 57,
    ("transit", "北京中关村学院", "北京丰台站"): 68,
    ("transit", "北京中关村学院", "北京南站"): 62,
    ("transit", "北京中关村学院", "北京朝阳站"): 70,
    ("transit", "北京中关村学院", "清河站"): 31,
}


@dataclass(frozen=True)
class EtaResult:
    origin: str
    destination: str
    mode: str
    minutes: int
    seconds: int
    distance_m: int | None
    source: str
    raw_summary: str

    def to_dict(self) -> dict[str, object]:
        return {
            "origin": self.origin,
            "destination": self.destination,
            "mode": self.mode,
            "minutes": self.minutes,
            "seconds": self.seconds,
            "distance_m": self.distance_m,
            "source": self.source,
            "raw_summary": self.raw_summary,
        }


class AmapClient:
    def __init__(
        self,
        api_key: str | None = None,
        cache_path: str | Path = DEFAULT_CACHE,
        timeout: int = 8,
    ) -> None:
        self.api_key = api_key or os.environ.get("AMAP_API_KEY") or ""
        self.cache_path = Path(cache_path)
        self.timeout = timeout
        self._cache = self._load_cache()

    def _load_cache(self) -> dict[str, Any]:
        if not self.cache_path.exists():
            return {}
        with self.cache_path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("w", encoding="utf-8") as file:
            json.dump(self._cache, file, ensure_ascii=False, indent=2)

    def _request(self, endpoint: str, params: dict[str, object]) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("AMAP_API_KEY is not set")
        query = urllib.parse.urlencode({**params, "key": self.api_key})
        url = f"https://restapi.amap.com/{endpoint}?{query}"
        with urllib.request.urlopen(url, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def geocode(self, address: str, city: str = "北京") -> tuple[float, float]:
        key = f"geocode:{city}:{address}"
        if key in self._cache:
            cached = self._cache[key]
            return float(cached["lng"]), float(cached["lat"])

        data = self._request("v3/geocode/geo", {"address": address, "city": city})
        if data.get("status") != "1" or not data.get("geocodes"):
            raise RuntimeError(f"Amap geocode failed for {address}: {data.get('info')}")
        lng_text, lat_text = data["geocodes"][0]["location"].split(",")
        self._cache[key] = {"lng": float(lng_text), "lat": float(lat_text)}
        self._save_cache()
        return float(lng_text), float(lat_text)

    def eta_between_addresses(
        self,
        origin: str,
        destination: str,
        mode: str = "driving",
        city: str = "北京",
    ) -> EtaResult:
        key = f"eta:{mode}:{city}:{origin}:{destination}"
        if key in self._cache:
            cached = self._cache[key]
            return EtaResult(source="amap_cache", **cached)

        try:
            origin_lng, origin_lat = self.geocode(origin, city=city)
            dest_lng, dest_lat = self.geocode(destination, city=city)
            result = self.eta_between_points(
                origin=f"{origin_lng},{origin_lat}",
                destination=f"{dest_lng},{dest_lat}",
                origin_label=origin,
                destination_label=destination,
                mode=mode,
                city=city,
            )
            self._cache[key] = {
                "origin": result.origin,
                "destination": result.destination,
                "mode": result.mode,
                "minutes": result.minutes,
                "seconds": result.seconds,
                "distance_m": result.distance_m,
                "raw_summary": result.raw_summary,
            }
            self._save_cache()
            return EtaResult(source="amap_live", **self._cache[key])
        except Exception as exc:
            return self.mock_eta(origin, destination, mode, reason=str(exc))

    def eta_between_points(
        self,
        origin: str,
        destination: str,
        origin_label: str,
        destination_label: str,
        mode: str = "driving",
        city: str = "北京",
    ) -> EtaResult:
        if mode == "transit":
            data = self._request(
                "v3/direction/transit/integrated",
                {
                    "origin": origin,
                    "destination": destination,
                    "city": city,
                    "cityd": city,
                    "extensions": "base",
                    "strategy": 0,
                },
            )
            if data.get("status") != "1":
                raise RuntimeError(f"Amap transit failed: {data.get('info')}")
            transits = data.get("route", {}).get("transits") or []
            if not transits:
                raise RuntimeError("Amap transit returned no route")
            first = transits[0]
            seconds = int(float(first["duration"]))
            distance = int(float(first.get("distance", 0)))
            return EtaResult(
                origin=origin_label,
                destination=destination_label,
                mode=mode,
                minutes=(seconds + 59) // 60,
                seconds=seconds,
                distance_m=distance,
                source="amap_live",
                raw_summary=f"transit segments={len(first.get('segments', []))}",
            )

        data = self._request(
            "v3/direction/driving",
            {
                "origin": origin,
                "destination": destination,
                "extensions": "base",
                "strategy": 2,
            },
        )
        if data.get("status") != "1":
            raise RuntimeError(f"Amap driving failed: {data.get('info')}")
        paths = data.get("route", {}).get("paths") or []
        if not paths:
            raise RuntimeError("Amap driving returned no route")
        first = paths[0]
        seconds = int(float(first["duration"]))
        distance = int(float(first.get("distance", 0)))
        return EtaResult(
            origin=origin_label,
            destination=destination_label,
            mode=mode,
            minutes=(seconds + 59) // 60,
            seconds=seconds,
            distance_m=distance,
            source="amap_live",
            raw_summary=f"driving strategy=2, tolls={first.get('tolls', '')}",
        )

    def mock_eta(self, origin: str, destination: str, mode: str, reason: str) -> EtaResult:
        minutes = MOCK_ETA_MINUTES.get((mode, origin, destination), 60)
        return EtaResult(
            origin=origin,
            destination=destination,
            mode=mode,
            minutes=minutes,
            seconds=minutes * 60,
            distance_m=None,
            source="mock_fallback",
            raw_summary=reason[:200],
        )

