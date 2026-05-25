from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any


DEFAULT_AMAP_KEY = "132c583fe949b7ee3b8d2412028e0138"
AMAP_BASE_URL = "https://restapi.amap.com"
LON_LAT_PATTERN = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*$")


class AmapError(RuntimeError):
    pass


def load_dotenv(path: str | None = None) -> None:
    env_path = Path(path) if path else Path(__file__).resolve().with_name(".env")
    if not env_path.exists():
        return
    with env_path.open("r", encoding="utf-8") as file:
        for line in file:
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            key, value = text.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def is_lon_lat(value: str) -> bool:
    return bool(LON_LAT_PATTERN.match(value))


def request_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
    url = f"{AMAP_BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "Run-MVP/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = response.read().decode("utf-8", "replace")

    data = json.loads(payload)
    if str(data.get("status")) != "1":
        info = data.get("info") or "UNKNOWN_ERROR"
        infocode = data.get("infocode") or ""
        raise AmapError(f"Amap API failed: {info} {infocode}".strip())
    return data


def geocode(address: str, key: str, city: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "key": key,
        "address": address,
        "output": "JSON",
    }
    if city:
        params["city"] = city

    data = request_json("/v3/geocode/geo", params)
    geocodes = data.get("geocodes") or []
    if not geocodes:
        raise AmapError(f"no geocode result for address: {address}")

    item = geocodes[0]
    return {
        "input": address,
        "location": item.get("location", ""),
        "formatted_address": item.get("formatted_address", ""),
        "province": item.get("province", ""),
        "city": item.get("city") or item.get("province") or "",
        "district": item.get("district", ""),
        "adcode": item.get("adcode", ""),
    }


def resolve_location(value: str, key: str, city: str | None = None) -> dict[str, Any]:
    if is_lon_lat(value):
        location = value.replace(" ", "")
        return {
            "input": value,
            "location": location,
            "formatted_address": location,
            "province": "",
            "city": city or "",
            "district": "",
            "adcode": "",
        }
    return geocode(value, key, city)


def seconds_to_minutes(seconds: str | int | float | None) -> float | None:
    if seconds in (None, ""):
        return None
    return round(float(seconds) / 60, 1)


def meters_to_km(meters: str | int | float | None) -> float | None:
    if meters in (None, ""):
        return None
    return round(float(meters) / 1000, 2)


def arrival_time(duration_seconds: str | int | float | None, start_time: datetime) -> str | None:
    if duration_seconds in (None, ""):
        return None
    return (start_time + timedelta(seconds=float(duration_seconds))).strftime("%Y-%m-%d %H:%M:%S")


def compact_step(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "instruction": step.get("instruction", ""),
        "road": step.get("road", ""),
        "distance_m": int(float(step.get("distance") or 0)),
        "duration_min": seconds_to_minutes(step.get("duration")),
        "action": step.get("action", ""),
        "assistant_action": step.get("assistant_action", ""),
    }


def driving_plans(origin: str, destination: str, key: str, start_time: datetime) -> list[dict[str, Any]]:
    data = request_json(
        "/v3/direction/driving",
        {
            "key": key,
            "origin": origin,
            "destination": destination,
            "strategy": 10,
            "extensions": "all",
            "output": "JSON",
        },
    )

    plans = []
    for index, path in enumerate((data.get("route") or {}).get("paths") or [], start=1):
        plans.append(
            {
                "mode": "taxi",
                "scheme_index": index,
                "duration_min": seconds_to_minutes(path.get("duration")),
                "arrival_time": arrival_time(path.get("duration"), start_time),
                "distance_km": meters_to_km(path.get("distance")),
                "tolls_yuan": float(path.get("tolls") or 0),
                "traffic_lights": int(float(path.get("traffic_lights") or 0)),
                "restriction": path.get("restriction", ""),
                "steps": [compact_step(step) for step in path.get("steps") or []],
            }
        )
    return plans


def walking_plans(origin: str, destination: str, key: str, start_time: datetime) -> list[dict[str, Any]]:
    data = request_json(
        "/v3/direction/walking",
        {
            "key": key,
            "origin": origin,
            "destination": destination,
            "output": "JSON",
        },
    )

    plans = []
    for index, path in enumerate((data.get("route") or {}).get("paths") or [], start=1):
        plans.append(
            {
                "mode": "walk",
                "scheme_index": index,
                "duration_min": seconds_to_minutes(path.get("duration")),
                "arrival_time": arrival_time(path.get("duration"), start_time),
                "distance_km": meters_to_km(path.get("distance")),
                "steps": [compact_step(step) for step in path.get("steps") or []],
            }
        )
    return plans


def busline_summary(busline: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": busline.get("name", ""),
        "type": busline.get("type", ""),
        "departure_stop": (busline.get("departure_stop") or {}).get("name", ""),
        "arrival_stop": (busline.get("arrival_stop") or {}).get("name", ""),
        "distance_km": meters_to_km(busline.get("distance")),
        "duration_min": seconds_to_minutes(busline.get("duration")),
        "via_num": int(float(busline.get("via_num") or 0)),
    }


def transit_segment(segment: dict[str, Any]) -> dict[str, Any]:
    walking = segment.get("walking") or {}
    bus = segment.get("bus") or {}
    buslines = bus.get("buslines") or []
    return {
        "walking": {
            "distance_km": meters_to_km(walking.get("distance")),
            "duration_min": seconds_to_minutes(walking.get("duration")),
            "steps": [compact_step(step) for step in walking.get("steps") or []],
        },
        "buslines": [busline_summary(line) for line in buslines],
        "entrance": segment.get("entrance") or {},
        "exit": segment.get("exit") or {},
    }


def transit_plans(
    origin: str,
    destination: str,
    key: str,
    city: str,
    cityd: str | None,
    start_time: datetime,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "key": key,
        "origin": origin,
        "destination": destination,
        "city": city,
        "strategy": 0,
        "extensions": "all",
        "output": "JSON",
    }
    if cityd:
        params["cityd"] = cityd

    data = request_json("/v3/direction/transit/integrated", params)
    route = data.get("route") or {}
    plans = []
    for index, transit in enumerate(route.get("transits") or [], start=1):
        segments = [transit_segment(segment) for segment in transit.get("segments") or []]
        busline_names = [
            line["name"]
            for segment in segments
            for line in segment["buslines"]
            if line.get("name")
        ]
        subway_lines = [
            line["name"]
            for segment in segments
            for line in segment["buslines"]
            if "地铁" in (line.get("type") or "") or "地铁" in (line.get("name") or "")
        ]
        plans.append(
            {
                "mode": "transit",
                "scheme_index": index,
                "duration_min": seconds_to_minutes(transit.get("duration")),
                "arrival_time": arrival_time(transit.get("duration"), start_time),
                "distance_km": meters_to_km(route.get("distance")),
                "walking_distance_km": meters_to_km(transit.get("walking_distance")),
                "cost_yuan": float(transit.get("cost") or 0),
                "nightflag": transit.get("nightflag", ""),
                "busline_names": busline_names,
                "subway_lines": subway_lines,
                "segments": segments,
            }
        )
    return plans


def parse_start_time(value: str | None) -> datetime:
    if not value:
        return datetime.now()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(value, fmt)
            if fmt.startswith("%H"):
                now = datetime.now()
                return now.replace(
                    hour=parsed.hour,
                    minute=parsed.minute,
                    second=parsed.second,
                    microsecond=0,
                )
            return parsed
        except ValueError:
            pass
    raise ValueError("start time must be 'HH:MM' or 'YYYY-MM-DD HH:MM'")


def get_eta_plans(
    origin: str,
    destination: str,
    key: str = DEFAULT_AMAP_KEY,
    city: str | None = None,
    cityd: str | None = None,
    start_time: str | None = None,
    modes: list[str] | None = None,
) -> dict[str, Any]:
    start_dt = parse_start_time(start_time)
    origin_info = resolve_location(origin, key, city)
    destination_info = resolve_location(destination, key, cityd or city)
    origin_location = origin_info["location"]
    destination_location = destination_info["location"]
    origin_city = city or origin_info.get("city") or origin_info.get("adcode")
    destination_city = cityd or destination_info.get("city") or destination_info.get("adcode")
    requested_modes = modes or ["taxi", "transit", "walk"]

    result: dict[str, Any] = {
        "origin": origin_info,
        "destination": destination_info,
        "start_time": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "schemes": [],
        "summary": {},
    }

    errors: dict[str, str] = {}
    if "taxi" in requested_modes:
        try:
            result["schemes"].extend(driving_plans(origin_location, destination_location, key, start_dt))
        except Exception as exc:
            errors["taxi"] = str(exc)
    if "transit" in requested_modes:
        try:
            result["schemes"].extend(
                transit_plans(
                    origin_location,
                    destination_location,
                    key,
                    origin_city,
                    destination_city if destination_city != origin_city else None,
                    start_dt,
                )
            )
        except Exception as exc:
            errors["transit"] = str(exc)
    if "walk" in requested_modes:
        try:
            result["schemes"].extend(walking_plans(origin_location, destination_location, key, start_dt))
        except Exception as exc:
            errors["walk"] = str(exc)

    result["schemes"].sort(
        key=lambda item: (
            item["duration_min"] if item.get("duration_min") is not None else float("inf"),
            item["mode"],
            item["scheme_index"],
        )
    )
    for mode in ("taxi", "transit", "walk"):
        mode_plans = [item for item in result["schemes"] if item["mode"] == mode]
        if mode_plans:
            result["summary"][f"{mode}_eta_min"] = mode_plans[0]["duration_min"]
            result["summary"][f"{mode}_arrival_time"] = mode_plans[0]["arrival_time"]
    if errors:
        result["errors"] = errors
    return result


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", required=True, help="lon,lat or address/POI name")
    parser.add_argument("--destination", required=True, help="lon,lat or address/POI name")
    parser.add_argument("--key")
    parser.add_argument("--city", help="origin city for geocoding/transit, e.g. 北京")
    parser.add_argument("--cityd", help="destination city for cross-city transit")
    parser.add_argument("--start-time", help="HH:MM or YYYY-MM-DD HH:MM")
    parser.add_argument(
        "--modes",
        default="taxi,transit,walk",
        help="comma-separated modes: taxi,transit,walk",
    )
    args = parser.parse_args()

    try:
        result = get_eta_plans(
            origin=args.origin,
            destination=args.destination,
            key=args.key or os.getenv("AMAP_API_KEY", DEFAULT_AMAP_KEY),
            city=args.city,
            cityd=args.cityd,
            start_time=args.start_time,
            modes=[item.strip() for item in args.modes.split(",") if item.strip()],
        )
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
