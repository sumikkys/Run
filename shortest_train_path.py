from __future__ import annotations

import argparse
import csv
import heapq
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


RAW_COLUMNS = [
    "车次",
    "站序",
    "车站",
    "到达时间",
    "出发时间",
    "历时",
    "到达日",
    "到达天数",
    "列车类型",
    "是否始发站",
    "始发站",
    "终到站",
]

EDGE_COLUMNS = [
    "服务ID",
    "服务始发站",
    "服务终到站",
    "起始站",
    "到站",
    "起始站车次",
    "到站车次",
    "起始站站序",
    "到站站序",
    "区间出发时间",
    "区间到达时间",
    "区间出发绝对分钟",
    "区间到达绝对分钟",
    "区间运行分钟",
    "起始站历时",
    "到站历时",
    "到达日",
    "到达天数",
    "列车类型",
]

COL_TRAIN_NO = 0
COL_STOP_ORDER = 1
COL_STATION = 2
COL_ARRIVAL_TIME = 3
COL_DEPARTURE_TIME = 4
COL_ELAPSED = 5
COL_ARRIVAL_DAY = 6
COL_ARRIVAL_DAYS = 7
COL_TRAIN_TYPE = 8
COL_SERVICE_ORIGIN = 10
COL_SERVICE_DESTINATION = 11


@dataclass(frozen=True)
class Edge:
    service_id: str
    service_origin: str
    service_destination: str
    from_station: str
    to_station: str
    from_train_no: str
    to_train_no: str
    from_order: str
    to_order: str
    depart_clock: str
    arrive_clock: str
    depart_abs: int
    arrive_abs: int
    travel_minutes: int
    from_elapsed: str
    to_elapsed: str
    arrival_day: str
    arrival_days: str
    train_type: str


def parse_time(value: str | int | float) -> int:
    """Return minutes. Accepts minutes, HH:MM, or D:HH:MM."""
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).strip()
    if not text:
        raise ValueError("time value is empty")
    if text.isdigit():
        return int(text)

    parts = text.split(":")
    if len(parts) == 2:
        hours, minutes = parts
        return int(hours) * 60 + int(minutes)
    if len(parts) == 3:
        days, hours, minutes = parts
        return int(days) * 24 * 60 + int(hours) * 60 + int(minutes)

    raise ValueError(f"unsupported time format: {value!r}")


def parse_clock(value: str) -> int | None:
    text = value.strip()
    if not text or text == "----":
        return None
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError(f"unsupported clock format: {value!r}")
    return int(parts[0]) * 60 + int(parts[1])


def format_abs_time(value: int) -> str:
    days, minute_of_day = divmod(value, 24 * 60)
    hours, minutes = divmod(minute_of_day, 60)
    suffix = f"+{days}d" if days else ""
    return f"{hours:02d}:{minutes:02d}{suffix}"


def max_cost_deadline(current_time: str, max_cost: str | int | float) -> int:
    start_abs = parse_time(current_time)
    return start_abs + parse_time(max_cost)


def default_raw_csv_path() -> Path:
    csv_files = [
        path
        for path in sorted(Path(__file__).resolve().parent.glob("*.csv"))
        if not path.name.endswith("_edges.csv")
    ]
    if not csv_files:
        raise FileNotFoundError("no raw csv file found next to shortest_train_path.py")
    return csv_files[0]


def default_edges_csv_path(raw_csv_path: str | Path | None = None) -> Path:
    raw_path = Path(raw_csv_path) if raw_csv_path else default_raw_csv_path()
    return raw_path.with_name(f"{raw_path.stem}_edges.csv")


def open_csv_reader(csv_path: str | Path):
    csv_path = Path(csv_path)
    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            file = csv_path.open("r", encoding=encoding, newline="")
            reader = csv.reader(file)
            next(reader, None)
            return file, reader
        except UnicodeDecodeError as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    raise FileNotFoundError(csv_path)


def normalize_raw_row(row: list[str]) -> list[str]:
    if len(row) < len(RAW_COLUMNS):
        row = row + [""] * (len(RAW_COLUMNS) - len(row))
    return [value.strip() for value in row[: len(RAW_COLUMNS)]]


def absolute_departure_time(row: list[str]) -> int | None:
    clock = parse_clock(row[COL_DEPARTURE_TIME])
    if clock is None:
        return None

    elapsed = parse_time(row[COL_ELAPSED])
    day = elapsed // (24 * 60)
    absolute = day * 24 * 60 + clock
    if absolute < elapsed:
        absolute += 24 * 60
    return absolute


def absolute_arrival_time(row: list[str]) -> int | None:
    clock = parse_clock(row[COL_ARRIVAL_TIME])
    if clock is None:
        return None

    elapsed = parse_time(row[COL_ELAPSED])
    day = elapsed // (24 * 60)
    absolute = day * 24 * 60 + clock
    if absolute < elapsed:
        absolute += 24 * 60
    return absolute


def row_to_edge(service_id: int, service_start: list[str], prev: list[str], cur: list[str]) -> list[str] | None:
    depart_abs = absolute_departure_time(prev)
    arrive_abs = absolute_arrival_time(cur)
    if depart_abs is None or arrive_abs is None:
        return None

    while arrive_abs <= depart_abs:
        arrive_abs += 24 * 60

    travel_minutes = arrive_abs - depart_abs
    if travel_minutes <= 0:
        return None

    return [
        str(service_id),
        service_start[COL_SERVICE_ORIGIN] or service_start[COL_STATION],
        service_start[COL_SERVICE_DESTINATION],
        prev[COL_STATION],
        cur[COL_STATION],
        prev[COL_TRAIN_NO],
        cur[COL_TRAIN_NO],
        prev[COL_STOP_ORDER],
        cur[COL_STOP_ORDER],
        prev[COL_DEPARTURE_TIME],
        cur[COL_ARRIVAL_TIME],
        str(depart_abs),
        str(arrive_abs),
        str(travel_minutes),
        prev[COL_ELAPSED],
        cur[COL_ELAPSED],
        cur[COL_ARRIVAL_DAY],
        cur[COL_ARRIVAL_DAYS],
        service_start[COL_TRAIN_TYPE],
    ]


def preprocess_edges(
    raw_csv_path: str | Path | None = None,
    output_csv_path: str | Path | None = None,
) -> Path:
    raw_path = Path(raw_csv_path) if raw_csv_path else default_raw_csv_path()
    output_path = Path(output_csv_path) if output_csv_path else default_edges_csv_path(raw_path)

    file, reader = open_csv_reader(raw_path)
    try:
        service_id = 0
        service_start: list[str] | None = None
        prev: list[str] | None = None

        with output_path.open("w", encoding="utf-8-sig", newline="") as output_file:
            writer = csv.writer(output_file)
            writer.writerow(EDGE_COLUMNS)

            for raw_row in reader:
                row = normalize_raw_row(raw_row)
                if row[COL_ARRIVAL_TIME] == "----":
                    service_id += 1
                    service_start = row
                    prev = row
                    continue

                if service_start is not None and prev is not None:
                    edge = row_to_edge(service_id, service_start, prev, row)
                    if edge is not None:
                        writer.writerow(edge)
                prev = row
    finally:
        file.close()

    return output_path


def ensure_edges_csv(
    raw_csv_path: str | Path | None = None,
    edges_csv_path: str | Path | None = None,
    rebuild: bool = False,
) -> Path:
    edge_path = Path(edges_csv_path) if edges_csv_path else default_edges_csv_path(raw_csv_path)
    if rebuild or not edge_path.exists():
        return preprocess_edges(raw_csv_path=raw_csv_path, output_csv_path=edge_path)
    return edge_path


def read_edges(edges_csv_path: str | Path) -> dict[str, list[Edge]]:
    path = Path(edges_csv_path)
    schedule: dict[str, list[Edge]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            edge = Edge(
                service_id=row["服务ID"],
                service_origin=row["服务始发站"],
                service_destination=row["服务终到站"],
                from_station=row["起始站"],
                to_station=row["到站"],
                from_train_no=row["起始站车次"],
                to_train_no=row["到站车次"],
                from_order=row["起始站站序"],
                to_order=row["到站站序"],
                depart_clock=row["区间出发时间"],
                arrive_clock=row["区间到达时间"],
                depart_abs=int(row["区间出发绝对分钟"]),
                arrive_abs=int(row["区间到达绝对分钟"]),
                travel_minutes=int(row["区间运行分钟"]),
                from_elapsed=row["起始站历时"],
                to_elapsed=row["到站历时"],
                arrival_day=row["到达日"],
                arrival_days=row["到达天数"],
                train_type=row["列车类型"],
            )
            schedule[edge.from_station].append(edge)

    for edges in schedule.values():
        edges.sort(key=lambda item: item.depart_abs)
    return dict(schedule)


def next_departure_on_or_after(edge_depart_abs: int, earliest_abs: int) -> int:
    if edge_depart_abs >= earliest_abs:
        return edge_depart_abs

    day = 24 * 60
    repeats = (earliest_abs - edge_depart_abs + day - 1) // day
    return edge_depart_abs + repeats * day


def empty_plan() -> dict[str, list[str | int]]:
    output = {column: [] for column in EDGE_COLUMNS}
    output["实际出发时间"] = []
    output["实际到达时间"] = []
    output["累计花费分钟"] = []
    return output


def edges_to_plan_table(
    current_time: str,
    path_edges: list[tuple[Edge, int, int]],
) -> dict[str, list[str | int]]:
    start_abs = parse_time(current_time)
    output = empty_plan()

    for edge, depart_abs, arrive_abs in path_edges:
        values = [
            edge.service_id,
            edge.service_origin,
            edge.service_destination,
            edge.from_station,
            edge.to_station,
            edge.from_train_no,
            edge.to_train_no,
            edge.from_order,
            edge.to_order,
            edge.depart_clock,
            edge.arrive_clock,
            edge.depart_abs,
            edge.arrive_abs,
            edge.travel_minutes,
            edge.from_elapsed,
            edge.to_elapsed,
            edge.arrival_day,
            edge.arrival_days,
            edge.train_type,
        ]
        for column, value in zip(EDGE_COLUMNS, values):
            output[column].append(value)
        output["实际出发时间"].append(format_abs_time(depart_abs))
        output["实际到达时间"].append(format_abs_time(arrive_abs))
        output["累计花费分钟"].append(arrive_abs - start_abs)

    return output


def find_earliest_arrival_plan(
    current_time: str,
    origin: str,
    destination: str,
    max_cost: str | int | float,
    raw_csv_path: str | Path | None = None,
    edges_csv_path: str | Path | None = None,
    rebuild_edges: bool = False,
) -> dict[str, list[str | int]]:
    start_abs = parse_time(current_time)
    deadline = max_cost_deadline(current_time, max_cost)
    origin = origin.strip()
    destination = destination.strip()

    edge_path = ensure_edges_csv(
        raw_csv_path=raw_csv_path,
        edges_csv_path=edges_csv_path,
        rebuild=rebuild_edges,
    )
    schedule = read_edges(edge_path)

    best_arrival: dict[str, int] = {origin: start_abs}
    previous: dict[str, tuple[str, Edge, int, int]] = {}
    heap: list[tuple[int, str]] = [(start_abs, origin)]

    while heap:
        arrived_abs, station = heapq.heappop(heap)
        if arrived_abs != best_arrival.get(station):
            continue
        if arrived_abs > deadline:
            break
        if station == destination:
            break

        for edge in schedule.get(station, []):
            depart_abs = next_departure_on_or_after(edge.depart_abs, arrived_abs)
            arrive_abs = depart_abs + edge.travel_minutes
            if arrive_abs > deadline:
                continue
            if arrive_abs < best_arrival.get(edge.to_station, float("inf")):
                best_arrival[edge.to_station] = arrive_abs
                previous[edge.to_station] = (station, edge, depart_abs, arrive_abs)
                heapq.heappush(heap, (arrive_abs, edge.to_station))

    if destination not in best_arrival:
        return empty_plan()

    reversed_edges: list[tuple[Edge, int, int]] = []
    station = destination
    while station != origin:
        prev_station, edge, depart_abs, arrive_abs = previous[station]
        reversed_edges.append((edge, depart_abs, arrive_abs))
        station = prev_station

    reversed_edges.reverse()
    return edges_to_plan_table(current_time, reversed_edges)


def find_earliest_arrival_path(
    current_time: str,
    origin: str,
    destination: str,
    max_cost: str | int | float,
    raw_csv_path: str | Path | None = None,
    edges_csv_path: str | Path | None = None,
    rebuild_edges: bool = False,
) -> list[str]:
    plan = find_earliest_arrival_plan(
        current_time=current_time,
        origin=origin,
        destination=destination,
        max_cost=max_cost,
        raw_csv_path=raw_csv_path,
        edges_csv_path=edges_csv_path,
        rebuild_edges=rebuild_edges,
    )
    if not plan["起始站"]:
        return []
    stations = [str(plan["起始站"][0])]
    stations.extend(str(station) for station in plan["到站"])
    return stations


def looks_like_time(value: str) -> bool:
    parts = value.strip().split(":")
    return len(parts) in (2, 3) and all(part.isdigit() for part in parts)


def split_query(values: Iterable[str] | None) -> tuple[str, str, str, str]:
    if not values:
        raise ValueError("missing query")

    text = " ".join(values).strip()
    normalized = text.replace("，", ",")
    if "," in normalized:
        parts = [part.strip() for part in normalized.split(",") if part.strip()]
    else:
        parts = normalized.split()

    if len(parts) != 4:
        raise ValueError(
            "input must contain 4 items: origin destination current_time max_cost"
        )

    if looks_like_time(parts[0]):
        return parts[0], parts[1], parts[2], parts[3]
    if looks_like_time(parts[2]):
        return parts[2], parts[0], parts[1], parts[3]

    raise ValueError(
        "current time must be first or third, for example: "
        "'17:00 北京 杭州 22:00' or '北京 杭州 17:00 22:00'"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--query",
        nargs="+",
        help="origin destination current_time max_cost",
    )
    parser.add_argument("--raw-csv", dest="raw_csv_path", help="path to raw timetable csv")
    parser.add_argument("--edges-csv", dest="edges_csv_path", help="path to cleaned edge csv")
    parser.add_argument(
        "--preprocess-only",
        action="store_true",
        help="only generate the cleaned edge csv",
    )
    parser.add_argument(
        "--rebuild-edges",
        action="store_true",
        help="rebuild the cleaned edge csv before searching",
    )
    parser.add_argument(
        "--stations-only",
        action="store_true",
        help="print only the ordered station list",
    )
    args = parser.parse_args()

    if args.preprocess_only:
        path = preprocess_edges(args.raw_csv_path, args.edges_csv_path)
        print(str(path))
        return

    current_time, origin, destination, max_cost = split_query(args.query)
    plan = find_earliest_arrival_plan(
        current_time=current_time,
        origin=origin,
        destination=destination,
        max_cost=max_cost,
        raw_csv_path=args.raw_csv_path,
        edges_csv_path=args.edges_csv_path,
        rebuild_edges=args.rebuild_edges,
    )
    output: object = (
        find_earliest_arrival_path(
            current_time=current_time,
            origin=origin,
            destination=destination,
            max_cost=max_cost,
            raw_csv_path=args.raw_csv_path,
            edges_csv_path=args.edges_csv_path,
            rebuild_edges=False,
        )
        if args.stations_only
        else plan
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
