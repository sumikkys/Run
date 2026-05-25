from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from .time_utils import (
    absolute_from_clock_and_elapsed,
    format_abs_min,
    next_daily_time_on_or_after,
    parse_clock,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW_CSV = REPO_ROOT / "列车时刻表20260501.csv"


@dataclass(frozen=True)
class TrainStop:
    train_no: str
    stop_order: int
    station: str
    arrival_time: str
    departure_time: str
    elapsed: str
    arrival_day: str
    arrival_days: str
    train_type: str
    service_origin: str
    service_destination: str

    @property
    def arrival_abs_min(self) -> int | None:
        return absolute_from_clock_and_elapsed(self.arrival_time, self.elapsed)

    @property
    def departure_abs_min(self) -> int | None:
        return absolute_from_clock_and_elapsed(self.departure_time, self.elapsed)


@dataclass(frozen=True)
class TrainService:
    service_id: str
    train_no: str
    origin: str
    destination: str
    train_type: str
    stops: tuple[TrainStop, ...]

    @cached_property
    def stations(self) -> tuple[str, ...]:
        return tuple(stop.station for stop in self.stops)


@dataclass(frozen=True)
class DirectConnection:
    service_id: str
    train_no: str
    train_type: str
    service_origin: str
    service_destination: str
    from_station: str
    to_station: str
    depart_abs_min: int
    arrive_abs_min: int
    from_stop_order: int
    to_stop_order: int

    @property
    def travel_minutes(self) -> int:
        return self.arrive_abs_min - self.depart_abs_min

    def to_dict(self) -> dict[str, object]:
        return {
            "service_id": self.service_id,
            "train_no": self.train_no,
            "train_type": self.train_type,
            "service_origin": self.service_origin,
            "service_destination": self.service_destination,
            "from_station": self.from_station,
            "to_station": self.to_station,
            "departure_time": format_abs_min(self.depart_abs_min),
            "arrival_time": format_abs_min(self.arrive_abs_min),
            "travel_minutes": self.travel_minutes,
            "from_stop_order": self.from_stop_order,
            "to_stop_order": self.to_stop_order,
        }


class RailSchedule:
    def __init__(self, raw_csv_path: str | Path = DEFAULT_RAW_CSV) -> None:
        self.raw_csv_path = Path(raw_csv_path)
        self.services = self._load_services()

    def _load_services(self) -> list[TrainService]:
        services: list[TrainService] = []
        current_rows: list[dict[str, str]] = []
        current_train_no: str | None = None
        occurrence = 0

        with self.raw_csv_path.open("r", encoding="gb18030", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                train_no = (row.get("车次") or "").strip()
                stop_order = (row.get("站序") or "").strip()
                starts_new = bool(current_rows) and (
                    train_no != current_train_no or stop_order == "01"
                )
                if starts_new:
                    occurrence += 1
                    services.append(self._rows_to_service(current_rows, occurrence))
                    current_rows = []
                current_train_no = train_no
                current_rows.append({key: (value or "").strip() for key, value in row.items()})

        if current_rows:
            occurrence += 1
            services.append(self._rows_to_service(current_rows, occurrence))
        return services

    def _rows_to_service(self, rows: list[dict[str, str]], occurrence: int) -> TrainService:
        first = rows[0]
        train_no = first["车次"]
        origin = first.get("始发站") or first["车站"]
        destination = first.get("终到站") or rows[-1]["车站"]
        train_type = first.get("列车类型") or ""
        stops: list[TrainStop] = []
        for row in rows:
            stops.append(
                TrainStop(
                    train_no=row["车次"],
                    stop_order=int(row["站序"]),
                    station=row["车站"],
                    arrival_time=row["到达时间"],
                    departure_time=row["出发时间"],
                    elapsed=row["历时"],
                    arrival_day=row["到达日"],
                    arrival_days=row["到达天数"],
                    train_type=row.get("列车类型") or train_type,
                    service_origin=origin,
                    service_destination=destination,
                )
            )
        return TrainService(
            service_id=f"{train_no}#{occurrence}",
            train_no=train_no,
            origin=origin,
            destination=destination,
            train_type=train_type,
            stops=tuple(stops),
        )

    def find_train_service(
        self,
        train_no: str,
        origin_station: str | None = None,
    ) -> TrainService | None:
        train_no = train_no.strip().upper()
        origin_station = origin_station.strip() if origin_station else None
        for service in self.services:
            if service.train_no.upper() != train_no:
                continue
            if origin_station and origin_station not in service.stations:
                continue
            return service
        return None

    def get_downstream_stops(self, train_no: str, origin_station: str) -> list[TrainStop]:
        service = self.find_train_service(train_no, origin_station)
        if service is None:
            return []
        stations = list(service.stations)
        try:
            origin_index = stations.index(origin_station)
        except ValueError:
            return []
        return list(service.stops[origin_index + 1 :])

    def get_stop(self, train_no: str, station: str, origin_station: str | None = None) -> TrainStop | None:
        service = self.find_train_service(train_no, origin_station)
        if service is None:
            return None
        for stop in service.stops:
            if stop.station == station:
                return stop
        return None

    def query_direct_connections(
        self,
        from_station: str,
        to_station: str,
        depart_after_abs_min: int,
        arrive_before_abs_min: int,
        exclude_train_no: str | None = None,
        limit: int = 20,
    ) -> list[DirectConnection]:
        output: list[DirectConnection] = []
        exclude = exclude_train_no.strip().upper() if exclude_train_no else None

        for service in self.services:
            if exclude and service.train_no.upper() == exclude:
                continue
            stations = service.stations
            if from_station not in stations or to_station not in stations:
                continue
            from_index = stations.index(from_station)
            to_index = stations.index(to_station)
            if from_index >= to_index:
                continue

            from_stop = service.stops[from_index]
            to_stop = service.stops[to_index]
            service_depart = from_stop.departure_abs_min
            service_arrive = to_stop.arrival_abs_min
            if service_depart is None or service_arrive is None:
                continue

            depart_abs = next_daily_time_on_or_after(service_depart, depart_after_abs_min)
            repeat_offset = depart_abs - service_depart
            arrive_abs = service_arrive + repeat_offset
            while arrive_abs <= depart_abs:
                arrive_abs += 24 * 60

            if depart_abs < depart_after_abs_min or arrive_abs > arrive_before_abs_min:
                continue

            output.append(
                DirectConnection(
                    service_id=service.service_id,
                    train_no=service.train_no,
                    train_type=service.train_type,
                    service_origin=service.origin,
                    service_destination=service.destination,
                    from_station=from_station,
                    to_station=to_station,
                    depart_abs_min=depart_abs,
                    arrive_abs_min=arrive_abs,
                    from_stop_order=from_stop.stop_order,
                    to_stop_order=to_stop.stop_order,
                )
            )

        output.sort(key=lambda item: (item.arrive_abs_min, item.depart_abs_min, item.train_no))
        return output[:limit]


def station_departure_abs(train_no: str, station: str, raw_csv_path: str | Path = DEFAULT_RAW_CSV) -> int | None:
    return RailSchedule(raw_csv_path).get_stop(train_no, station).departure_abs_min

