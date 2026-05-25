#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
latest_departure.py

Compute the latest departure time that (with a chosen confidence) still
allows the user to catch a train/flight. This module exposes a function
`compute_latest_departure(...)` suitable for calling from a web frontend.

Usage (import):
  from latest_departure import compute_latest_departure

Usage (CLI):
  python latest_departure.py "2026-05-25 18:30" 30 5 --entry-buffer 30 --confidence 0.99

Parameters are simple (no external APIs) so your frontend should provide
an estimated travel time mean/std to the departure point.
"""

from datetime import datetime, timedelta
from typing import Tuple

# conservative z-scores for common confidence levels
_Z_MAP = {
    0.9: 1.282,
    0.95: 1.645,
    0.99: 2.326,
    0.999: 3.090,
}


def _z_for(confidence: float) -> float:
    if confidence >= 0.9999:
        return 4.0
    if confidence in _Z_MAP:
        return _Z_MAP[confidence]
    # fallback: linear interp between 0.95 and 0.99
    if 0.95 < confidence < 0.99:
        return 1.645 + (confidence - 0.95) * (2.326 - 1.645) / (0.99 - 0.95)
    return 3.090


def compute_latest_departure(depart_dt: datetime,
                             travel_time_mean_min: float,
                             travel_time_std_min: float = 5.0,
                             entry_buffer_min: float = 20.0,
                             safety_margin_min: float = 5.0,
                             confidence: float = 0.999) -> Tuple[datetime, dict]:
    """
    Compute the latest local datetime to depart (leave current location)
    so that with the specified confidence the user can still catch the
    departure.

    Inputs:
      - depart_dt: target departure datetime (datetime)
      - travel_time_mean_min: expected travel minutes from current location
      - travel_time_std_min: standard deviation (minutes) of travel time
      - entry_buffer_min: minutes required at station/airport before departure
      - safety_margin_min: extra headroom (minutes)
      - confidence: probability in (0,1) to be treated as guaranteed

    Returns: (latest_departure_dt, details_dict)
    """
    z = _z_for(confidence)
    travel_quantile = travel_time_mean_min + z * travel_time_std_min

    required_minutes = travel_quantile + entry_buffer_min + safety_margin_min
    latest_dt = depart_dt - timedelta(minutes=required_minutes)

    details = {
        "travel_quantile_min": travel_quantile,
        "z": z,
        "required_minutes": required_minutes,
        "confidence": confidence,
        "travel_mean_min": travel_time_mean_min,
        "travel_std_min": travel_time_std_min,
        "entry_buffer_min": entry_buffer_min,
        "safety_margin_min": safety_margin_min,
    }

    return latest_dt, details


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Compute latest departure time to guarantee catching a trip")
    p.add_argument("depart_datetime", help="Departure datetime (YYYY-MM-DD HH:MM)")
    p.add_argument("travel_mean", type=float, help="Mean travel time in minutes")
    p.add_argument("travel_std", type=float, nargs="?", default=5.0, help="Std dev of travel time in minutes")
    p.add_argument("--entry-buffer", type=float, default=20.0, help="Minutes needed at station/airport before departure")
    p.add_argument("--safety", type=float, default=5.0, help="Extra safety margin in minutes")
    p.add_argument("--confidence", type=float, default=0.999, help="Confidence level (e.g. 0.95,0.99,0.999)")

    args = p.parse_args()
    depart_dt = datetime.strptime(args.depart_datetime, "%Y-%m-%d %H:%M")

    latest_dt, details = compute_latest_departure(
        depart_dt,
        travel_time_mean_min=args.travel_mean,
        travel_time_std_min=args.travel_std,
        entry_buffer_min=args.entry_buffer,
        safety_margin_min=args.safety,
        confidence=args.confidence,
    )

    print(latest_dt.strftime("%Y-%m-%d %H:%M"))
    print(details)
