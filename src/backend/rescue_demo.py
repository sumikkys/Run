from __future__ import annotations

import argparse
import json
import sys

from .llm_client import generate_action_card, generate_transfer_advice
from .rescue_engine import DEFAULT_ORDER, RescueEngine, RescueOrder, to_pretty_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run! rescue pathfinding demo")
    parser.add_argument("--train-no", default=DEFAULT_ORDER.train_no)
    parser.add_argument("--origin-station", default=DEFAULT_ORDER.origin_station)
    parser.add_argument("--destination-station", default=DEFAULT_ORDER.destination_station)
    parser.add_argument("--current-location", default=DEFAULT_ORDER.current_location)
    parser.add_argument("--first-time", default=DEFAULT_ORDER.first_reminder_time)
    parser.add_argument("--second-time", default=DEFAULT_ORDER.second_reminder_time)
    parser.add_argument("--mode", choices=["driving", "transit"], default="driving")
    parser.add_argument("--eta-delay", type=int, default=0)
    parser.add_argument("--station-delay", type=int, default=0)
    parser.add_argument("--sold-out", default="", help="comma-separated rescue train numbers to mark sold out")
    parser.add_argument("--with-ai", action="store_true", help="generate transfer advice with DeepSeek if configured")
    parser.add_argument("--summary", action="store_true", help="print compact action-card summary")
    return parser


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    order = RescueOrder(
        train_no=args.train_no,
        origin_station=args.origin_station,
        destination_station=args.destination_station,
        current_location=args.current_location,
        first_reminder_time=args.first_time,
        second_reminder_time=args.second_time,
    )
    engine = RescueEngine()
    sold_out = {item.strip() for item in args.sold_out.split(",") if item.strip()}
    result = engine.run_demo(
        order,
        mode=args.mode,
        extra_eta_min=args.eta_delay,
        extra_station_buffer_min=args.station_delay,
        sold_out_trains=sold_out,
    )

    recommendation = result["rescue_search"].get("recommendation")
    if args.with_ai and recommendation:
        result["ai_action_card"] = generate_action_card(result)
        transfer_entry = recommendation.get("transfer_buffer", {}).get("entry")
        result["ai_transfer_advice"] = generate_transfer_advice(transfer_entry, recommendation)

    if args.summary:
        print(render_summary(result))
    else:
        print(to_pretty_json(result))


def render_summary(result: dict[str, object]) -> str:
    first = result["first_reminder"]
    second = result["second_reminder"]
    search = result["rescue_search"]
    rec = search.get("recommendation")

    lines = [
        "Run! pathfinding V0",
        f"第一次提醒: {'可赶上' if first['can_catch'] else '赶不上'}; 安全余量 {first['safety_margin_min']} 分钟",
        f"第二次提醒: {'可赶上' if second['can_catch'] else '赶不上'}; 安全余量 {second['safety_margin_min']} 分钟",
    ]
    if rec:
        rescue = rec["rescue_train"]
        rejoin = rec["original_train_rejoin"]
        lines.extend(
            [
                f"推荐: {rec['title']}",
                f"补救车: {rescue['from_station']} {rescue['departure_time']} -> {rescue['to_station']} {rescue['arrival_time']}",
                f"接回原车: {rejoin['station']} {rejoin['departure_time']} 开，换乘余量 {rec['transfer_margin_min']} 分钟",
                f"余票: {rec['inventory']['status']}，新增票价约 {rec['inventory']['price_cny']} 元",
                f"数据源: {', '.join(rec['data_sources'])}",
            ]
        )
    else:
        lines.append("推荐: 暂无可行追车方案，建议进入改签/退票/航班兜底。")

    if "ai_transfer_advice" in result:
        advice = result["ai_transfer_advice"]
        lines.append(f"换乘建议({advice['source']}): {advice['text']}")
    if "ai_action_card" in result:
        card = result["ai_action_card"]
        lines.append(f"行动卡({card['source']}): {card['text']}")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
