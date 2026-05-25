from __future__ import annotations

import argparse
import csv
import json
import re
import urllib.request
from collections import defaultdict
from pathlib import Path


CITY = "\u57ce\u5e02"
STATION = "\u8f66\u7ad9"
DEFAULT_12306_URL = (
    "https://www.12306.cn/index/script/core/common/station_name_new_v10092.js"
)


def default_map_path() -> Path:
    return Path(__file__).resolve().with_name("station_city_map.csv")


def load_station_city_map(
    map_path: str | Path | None = None,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    path = Path(map_path) if map_path else default_map_path()
    station_to_city: dict[str, str] = {}
    city_to_stations: dict[str, list[str]] = defaultdict(list)

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            city = (row.get(CITY) or "").strip()
            station = (row.get(STATION) or "").strip()
            if not city or not station:
                continue
            station_to_city[station] = city
            if station not in city_to_stations[city]:
                city_to_stations[city].append(station)

    return station_to_city, dict(city_to_stations)


def parse_12306_station_script(script_text: str) -> list[tuple[str, str]]:
    match = re.search(r"station_names\s*=\s*'([^']*)'", script_text)
    if not match:
        raise ValueError("could not find station_names in 12306 script")

    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in match.group(1).split("@"):
        if not item:
            continue
        parts = item.split("|")
        if len(parts) < 8:
            continue
        station = parts[1].strip()
        city = parts[7].strip()
        if not station or not city:
            continue
        key = (city, station)
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)
    pairs.sort(key=lambda pair: (pair[0], pair[1]))
    return pairs


def refresh_station_city_map(
    output_path: str | Path | None = None,
    source_url: str = DEFAULT_12306_URL,
) -> Path:
    path = Path(output_path) if output_path else default_map_path()
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        script_text = response.read().decode("utf-8", "ignore")

    pairs = parse_12306_station_script(script_text)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([CITY, STATION])
        writer.writerows(pairs)
    return path


def get_same_city_stations(
    station: str,
    map_path: str | Path | None = None,
    include_self: bool = False,
) -> list[str]:
    station = station.strip()
    station_to_city, city_to_stations = load_station_city_map(map_path)
    city = station_to_city.get(station)
    if city is None:
        return []

    stations = city_to_stations[city]
    if include_self:
        return stations
    return [item for item in stations if item != station]


def get_station_city(station: str, map_path: str | Path | None = None) -> str | None:
    station_to_city, _city_to_stations = load_station_city_map(map_path)
    return station_to_city.get(station.strip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("station", nargs="?", help="station name, for example: Qinghe")
    parser.add_argument("--map", dest="map_path", help="path to station_city_map.csv")
    parser.add_argument("--include-self", action="store_true")
    parser.add_argument("--city", action="store_true", help="print only the city name")
    parser.add_argument(
        "--refresh-from-12306",
        action="store_true",
        help="download official 12306 station-city data and update the map csv",
    )
    parser.add_argument("--source-url", default=DEFAULT_12306_URL)
    args = parser.parse_args()

    if args.refresh_from_12306:
        path = refresh_station_city_map(args.map_path, args.source_url)
        print(str(path))
        return

    if not args.station:
        raise ValueError("station is required unless --refresh-from-12306 is used")

    if args.city:
        output: object = get_station_city(args.station, args.map_path)
    else:
        output = get_same_city_stations(
            args.station,
            map_path=args.map_path,
            include_self=args.include_self,
        )
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
