from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .amap_client import AmapClient, EtaResult
from .manual_transfer import ManualTransferStore
from .rail_schedule import DirectConnection, RailSchedule, TrainStop
from .time_utils import format_abs_min, parse_clock


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RescueOrder:
    train_no: str
    origin_station: str
    destination_station: str
    current_location: str
    first_reminder_time: str
    second_reminder_time: str


DEFAULT_ORDER = RescueOrder(
    train_no="D29",
    origin_station="北京西",
    destination_station="西安",
    current_location="北京中关村学院",
    first_reminder_time="14:15",
    second_reminder_time="15:25",
)


BEIJING_HUBS = {
    "北京西": "北京西站",
    "北京丰台": "北京丰台站",
    "北京南": "北京南站",
    "北京朝阳": "北京朝阳站",
    "清河": "清河站",
}


DEMO_INVENTORY = {
    "G337": {"status": "few", "price_cny": 128.5, "risk": "少量余票，需立即购买"},
    "G317": {"status": "available", "price_cny": 128.5, "risk": "余票可用"},
    "G681": {"status": "available", "price_cny": 128.5, "risk": "北京丰台出发，需确认城市 ETA"},
    "G665": {"status": "few", "price_cny": 128.5, "risk": "到达偏晚，只能作为备选"},
}


class RescueEngine:
    def __init__(
        self,
        schedule: RailSchedule | None = None,
        transfers: ManualTransferStore | None = None,
        amap: AmapClient | None = None,
    ) -> None:
        self.schedule = schedule or RailSchedule()
        self.transfers = transfers or ManualTransferStore()
        self.amap = amap or AmapClient()

    def can_catch_original_station(
        self,
        order: RescueOrder,
        now_clock: str,
        mode: str = "driving",
        extra_eta_min: int = 0,
        extra_station_buffer_min: int = 0,
    ) -> dict[str, object]:
        stop = self.schedule.get_stop(order.train_no, order.origin_station, order.origin_station)
        if stop is None or stop.departure_abs_min is None:
            return {"can_catch": False, "reason": "本地时刻表未找到原车出发站"}

        now_abs = parse_clock(now_clock)
        if now_abs is None:
            raise ValueError("now_clock is required")
        eta = self._eta_to_hub(order.current_location, order.origin_station, mode)
        buffer = self.transfers.station_access_buffer(order.origin_station, mode_hint=mode)
        required_arrival_abs = now_abs + eta.minutes + extra_eta_min + int(buffer["p90_min"]) + extra_station_buffer_min
        margin = stop.departure_abs_min - required_arrival_abs
        return {
            "can_catch": margin >= 0,
            "now": now_clock,
            "train_no": order.train_no,
            "origin_station": order.origin_station,
            "departure_time": format_abs_min(stop.departure_abs_min),
            "eta_to_origin": eta.to_dict(),
            "station_buffer": buffer,
            "anomaly_buffer": {
                "extra_eta_min": extra_eta_min,
                "extra_station_buffer_min": extra_station_buffer_min,
            },
            "arrival_ready_time": format_abs_min(required_arrival_abs),
            "safety_margin_min": margin,
            "reason": "可以继续赶原站" if margin >= 0 else "即使最快到达北京西，也无法完成进站和检票",
        }

    def find_intercept_candidates(
        self,
        order: RescueOrder,
        now_clock: str,
        mode: str = "driving",
        extra_eta_min: int = 0,
        extra_station_buffer_min: int = 0,
        sold_out_trains: set[str] | None = None,
    ) -> dict[str, object]:
        now_abs = parse_clock(now_clock)
        if now_abs is None:
            raise ValueError("now_clock is required")

        original_service = self.schedule.find_train_service(order.train_no, order.origin_station)
        if original_service is None:
            return {"status": "not_found", "candidates": [], "filter_reasons": ["找不到原车车次"]}

        reachable_hubs = self._reachable_hubs(
            order.current_location,
            now_abs,
            mode,
            extra_eta_min=extra_eta_min,
            extra_station_buffer_min=extra_station_buffer_min,
        )
        sold_out_trains = {train.upper() for train in (sold_out_trains or set())}
        downstream = self.schedule.get_downstream_stops(order.train_no, order.origin_station)
        candidates: list[dict[str, object]] = []
        filter_reasons: list[dict[str, object]] = []

        for stop in downstream:
            if stop.departure_abs_min is None:
                filter_reasons.append({"station": stop.station, "reason": "原车该站无出发时间"})
                continue

            transfer = self.transfers.transfer_buffer(stop.station, "换乘")
            latest_arrival = stop.departure_abs_min - int(transfer["p90_min"])
            if latest_arrival <= now_abs:
                filter_reasons.append({"station": stop.station, "reason": "下游站可用换乘窗口已过"})
                continue

            for hub, hub_state in reachable_hubs.items():
                if not hub_state["reachable"]:
                    filter_reasons.append(
                        {
                            "hub": hub,
                            "station": stop.station,
                            "reason": "赶不上该北京出发枢纽",
                            "detail": hub_state,
                        }
                    )
                    continue
                earliest_depart = int(hub_state["ready_abs_min"])
                connections = self.schedule.query_direct_connections(
                    from_station=hub,
                    to_station=stop.station,
                    depart_after_abs_min=earliest_depart,
                    arrive_before_abs_min=latest_arrival,
                    exclude_train_no=order.train_no,
                    limit=8,
                )
                if not connections:
                    filter_reasons.append(
                        {
                            "hub": hub,
                            "station": stop.station,
                            "reason": "无满足时间窗口的补救车",
                            "depart_after": format_abs_min(earliest_depart),
                            "arrive_before": format_abs_min(latest_arrival),
                        }
                    )
                    continue
                for connection in connections:
                    inventory = DEMO_INVENTORY.get(
                        connection.train_no,
                        {"status": "unknown", "price_cny": None, "risk": "余票未知，Demo 中降权"},
                    )
                    if connection.train_no.upper() in sold_out_trains:
                        inventory = {
                            **inventory,
                            "status": "sold_out",
                            "risk": "异常模拟：补救车余票已消失",
                        }
                    if inventory["status"] == "sold_out":
                        filter_reasons.append(
                            {
                                "train_no": connection.train_no,
                                "station": stop.station,
                                "reason": "补救车无票",
                            }
                        )
                        continue
                    candidates.append(
                        self._candidate_to_dict(
                            order=order,
                            original_stop=stop,
                            connection=connection,
                            transfer=transfer,
                            hub_state=hub_state,
                            inventory=inventory,
                        )
                    )

        candidates.sort(key=self._rank_key)
        return {
            "status": "ok",
            "order": order.__dict__,
            "now": now_clock,
            "anomalies": {
                "extra_eta_min": extra_eta_min,
                "extra_station_buffer_min": extra_station_buffer_min,
                "sold_out_trains": sorted(sold_out_trains),
            },
            "original_train": {
                "train_no": original_service.train_no,
                "origin": original_service.origin,
                "destination": original_service.destination,
                "stops": [
                    {
                        "station": stop.station,
                        "arrival": stop.arrival_time,
                        "departure": stop.departure_time,
                    }
                    for stop in original_service.stops
                ],
            },
            "reachable_hubs": reachable_hubs,
            "candidates": candidates[:10],
            "filter_reasons": filter_reasons[:40],
            "recommendation": candidates[0] if candidates else None,
        }

    def _candidate_to_dict(
        self,
        order: RescueOrder,
        original_stop: TrainStop,
        connection: DirectConnection,
        transfer: dict[str, object],
        hub_state: dict[str, object],
        inventory: dict[str, object],
    ) -> dict[str, object]:
        original_departure = original_stop.departure_abs_min
        assert original_departure is not None
        transfer_margin = original_departure - connection.arrive_abs_min
        return {
            "plan_type": "downstream_intercept_original_train",
            "title": f"买 {connection.train_no} 到{original_stop.station}，接回原车 {order.train_no}",
            "rescue_train": connection.to_dict(),
            "original_train_rejoin": {
                "train_no": order.train_no,
                "station": original_stop.station,
                "arrival_time": original_stop.arrival_time,
                "departure_time": format_abs_min(original_departure),
                "destination_station": order.destination_station,
            },
            "hub_access": hub_state,
            "transfer_buffer": transfer,
            "transfer_margin_min": transfer_margin,
            "inventory": inventory,
            "score": self._score(connection, transfer_margin, inventory),
            "data_sources": ["rail_schedule_snapshot", hub_state["eta"]["source"], transfer["source"], "demo_inventory"],
        }

    def _reachable_hubs(
        self,
        origin: str,
        now_abs: int,
        mode: str,
        extra_eta_min: int = 0,
        extra_station_buffer_min: int = 0,
    ) -> dict[str, dict[str, object]]:
        output: dict[str, dict[str, object]] = {}
        for hub, amap_name in BEIJING_HUBS.items():
            eta = self.amap.eta_between_addresses(origin, amap_name, mode=mode, city="北京")
            buffer = self.transfers.station_access_buffer(hub, mode_hint=mode)
            ready_abs = now_abs + eta.minutes + extra_eta_min + int(buffer["p90_min"]) + extra_station_buffer_min
            output[hub] = {
                "hub": hub,
                "amap_name": amap_name,
                "reachable": True,
                "ready_time": format_abs_min(ready_abs),
                "ready_abs_min": ready_abs,
                "eta": eta.to_dict(),
                "station_buffer": buffer,
                "anomaly_buffer": {
                    "extra_eta_min": extra_eta_min,
                    "extra_station_buffer_min": extra_station_buffer_min,
                },
            }
        return output

    def _eta_to_hub(self, origin: str, hub: str, mode: str) -> EtaResult:
        return self.amap.eta_between_addresses(origin, BEIJING_HUBS[hub], mode=mode, city="北京")

    def _score(
        self,
        connection: DirectConnection,
        transfer_margin_min: int,
        inventory: dict[str, object],
    ) -> float:
        inventory_penalty = {"available": 0, "few": 8, "unknown": 90, "waitlist": 120}.get(
            str(inventory.get("status")),
            60,
        )
        tightness_penalty = max(0, 25 - transfer_margin_min)
        return connection.arrive_abs_min + inventory_penalty + tightness_penalty

    def _rank_key(self, candidate: dict[str, object]) -> tuple[float, int]:
        return (float(candidate["score"]), -int(candidate["transfer_margin_min"]))

    def run_demo(
        self,
        order: RescueOrder = DEFAULT_ORDER,
        mode: str = "driving",
        extra_eta_min: int = 0,
        extra_station_buffer_min: int = 0,
        sold_out_trains: set[str] | None = None,
    ) -> dict[str, object]:
        first = self.can_catch_original_station(order, order.first_reminder_time, mode=mode)
        second = self.can_catch_original_station(
            order,
            order.second_reminder_time,
            mode=mode,
            extra_eta_min=extra_eta_min,
            extra_station_buffer_min=extra_station_buffer_min,
        )
        rescue = self.find_intercept_candidates(
            order,
            order.second_reminder_time,
            mode=mode,
            extra_eta_min=extra_eta_min,
            extra_station_buffer_min=extra_station_buffer_min,
            sold_out_trains=sold_out_trains,
        )
        return {"first_reminder": first, "second_reminder": second, "rescue_search": rescue}


def to_pretty_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
