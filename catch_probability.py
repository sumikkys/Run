#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
catch_probability.py

Estimate the real-time probability of catching a train/flight given a
probabilistic travel time model (normal distribution), required buffers,
contextual signals such as weather and rush hour, and a per-user ML profile
that improves over time.

Exposes `compute_catch_probability(...)` for frontend use and a small CLI
for quick checks.
"""

import json
from math import exp
from dataclasses import dataclass, field
from datetime import datetime
from math import erf, sqrt
from pathlib import Path
from typing import Dict, Optional


def _normal_cdf(z: float) -> float:
    """Standard normal CDF using erf"""
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


def _sigmoid(x: float) -> float:
    if x >= 0:
        exp_neg = exp(-x)
        return 1.0 / (1.0 + exp_neg)
    exp_pos = exp(x)
    return exp_pos / (1.0 + exp_pos)


STATE_FILE = Path(__file__).with_name("catch_probability_state.json")
MIN_TRAINING_SAMPLES = 8
MAX_SAMPLES_PER_USER = 200


@dataclass
class TravelContext:
    """Context signals that can affect travel time reliability."""

    weather: str = "clear"
    transport_mode: str = "taxi"
    rush_hour: bool = False
    weekend: bool = False
    luggage: str = "light"
    traffic_level: float = 0.0
    user_bias_min: float = 0.0
    user_variance_scale: float = 0.0


@dataclass
class LogisticProbabilityModel:
    """Tiny pure-Python logistic regression model for catch probability."""

    bias: float = 0.0
    weights: Dict[str, float] = field(default_factory=dict)
    feature_names: list[str] = field(default_factory=list)
    trained_samples: int = 0

    def predict_proba(self, features: Dict[str, float]) -> float:
        logit = self.bias
        for name in self.feature_names:
            logit += self.weights.get(name, 0.0) * float(features.get(name, 0.0))
        return _clamp(_sigmoid(logit), 0.0, 1.0)

    def to_dict(self) -> Dict:
        return {
            "bias": self.bias,
            "weights": dict(self.weights),
            "feature_names": list(self.feature_names),
            "trained_samples": self.trained_samples,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict]) -> "LogisticProbabilityModel":
        if not data:
            return cls()
        return cls(
            bias=float(data.get("bias", 0.0)),
            weights={k: float(v) for k, v in data.get("weights", {}).items()},
            feature_names=list(data.get("feature_names", [])),
            trained_samples=int(data.get("trained_samples", 0)),
        )

    @classmethod
    def train(cls,
              samples: list[Dict],
              epochs: int = 180,
              learning_rate: float = 0.06,
              l2: float = 0.0005) -> "LogisticProbabilityModel":
        feature_names = sorted({name for sample in samples for name in sample["features"].keys()})
        weights = {name: 0.0 for name in feature_names}
        bias = 0.0

        for _ in range(epochs):
            for sample in samples:
                features = sample["features"]
                label = float(sample["label"])

                logit = bias
                for name in feature_names:
                    logit += weights[name] * float(features.get(name, 0.0))

                prediction = _sigmoid(logit)
                error = label - prediction

                bias += learning_rate * error
                for name in feature_names:
                    value = float(features.get(name, 0.0))
                    if value:
                        weights[name] += learning_rate * (error * value - l2 * weights[name])

        return cls(bias=bias, weights=weights, feature_names=feature_names, trained_samples=len(samples))


def _default_state() -> Dict:
    return {"version": 1, "users": {}}


def _default_user_entry() -> Dict:
    return {
        "profile": {"bias_min": 0.0, "variance_scale": 0.0, "trips_seen": 0},
        "samples": [],
        "model": None,
    }


def _load_state(store_path: Optional[str] = None) -> Dict:
    path = Path(store_path) if store_path else STATE_FILE
    if not path.exists():
        return _default_state()

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return _default_state()

    if not isinstance(data, dict):
        return _default_state()
    data.setdefault("version", 1)
    data.setdefault("users", {})
    return data


def _save_state(state: Dict, store_path: Optional[str] = None) -> None:
    path = Path(store_path) if store_path else STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)


def _get_user_entry(state: Dict, user_id: str) -> Dict:
    users = state.setdefault("users", {})
    if user_id not in users:
        users[user_id] = _default_user_entry()
    entry = users[user_id]
    entry.setdefault("profile", {"bias_min": 0.0, "variance_scale": 0.0, "trips_seen": 0})
    entry.setdefault("samples", [])
    entry.setdefault("model", None)
    return entry


_WEATHER_MEAN_MULTIPLIER = {
    "clear": 1.00,
    "cloudy": 1.02,
    "rain": 1.12,
    "heavy_rain": 1.25,
    "snow": 1.30,
    "fog": 1.15,
    "storm": 1.35,
}

_TRANSPORT_MEAN_MULTIPLIER = {
    "walk": 1.18,
    "subway": 1.05,
    "bus": 1.12,
    "taxi": 1.00,
    "car": 1.00,
    "ride_hailing": 1.03,
}

_LUGGAGE_MEAN_MULTIPLIER = {
    "light": 1.00,
    "normal": 1.03,
    "heavy": 1.08,
}


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _context_multiplier(context: Optional[TravelContext]) -> Dict[str, float]:
    if context is None:
        context = TravelContext()

    weather = _WEATHER_MEAN_MULTIPLIER.get(context.weather, 1.05)
    transport = _TRANSPORT_MEAN_MULTIPLIER.get(context.transport_mode, 1.00)
    luggage = _LUGGAGE_MEAN_MULTIPLIER.get(context.luggage, 1.03)

    rush_hour = 1.15 if context.rush_hour else 1.00
    weekend = 0.98 if context.weekend else 1.00

    traffic_level = 1.0 + _clamp(context.traffic_level, 0.0, 1.0) * 0.35

    mean_multiplier = weather * transport * luggage * rush_hour * weekend * traffic_level

    std_multiplier = 1.0
    std_multiplier *= 1.0 + _clamp(context.traffic_level, 0.0, 1.0) * 0.40
    std_multiplier *= 1.0 + (mean_multiplier - 1.0) * 0.50
    std_multiplier *= 1.0 + _clamp(context.user_variance_scale, -0.5, 0.5)

    return {
        "mean_multiplier": mean_multiplier,
        "std_multiplier": max(0.1, std_multiplier),
        "user_bias_min": context.user_bias_min,
    }


def _build_feature_snapshot(depart_dt: datetime,
                            current_dt: datetime,
                            travel_time_mean_min: float,
                            travel_time_std_min: float,
                            entry_buffer_min: float,
                            context: Optional[TravelContext],
                            profile: Optional[Dict]) -> Dict[str, float]:
    context = context or TravelContext()
    profile = profile or {}
    context_factors = _context_multiplier(context)

    adjusted_mean_min = (
        travel_time_mean_min * context_factors["mean_multiplier"]
        + context_factors["user_bias_min"]
    )
    adjusted_std_min = max(0.0, travel_time_std_min * context_factors["std_multiplier"])
    remaining_min = (depart_dt - current_dt).total_seconds() / 60.0
    slack_min = remaining_min - entry_buffer_min - adjusted_mean_min

    weather = context.weather
    transport_mode = context.transport_mode
    luggage = context.luggage

    features = {
        "bias": 1.0,
        "slack_scaled": slack_min / 30.0,
        "slack_ratio": slack_min / max(remaining_min, 1.0),
        "remaining_scaled": remaining_min / 60.0,
        "travel_mean_scaled": travel_time_mean_min / 60.0,
        "travel_std_scaled": travel_time_std_min / 20.0,
        "adjusted_mean_scaled": adjusted_mean_min / 60.0,
        "adjusted_std_scaled": adjusted_std_min / 20.0,
        "entry_buffer_scaled": entry_buffer_min / 30.0,
        "rush_hour": 1.0 if context.rush_hour else 0.0,
        "weekend": 1.0 if context.weekend else 0.0,
        "traffic_level": _clamp(context.traffic_level, 0.0, 1.0),
        "user_bias_scaled": float(profile.get("bias_min", 0.0)) / 20.0,
        "user_variance_scaled": float(profile.get("variance_scale", 0.0)),
        "trips_seen_scaled": min(int(profile.get("trips_seen", 0)), 50) / 50.0,
        "weather_cloudy": 1.0 if weather == "cloudy" else 0.0,
        "weather_rain": 1.0 if weather == "rain" else 0.0,
        "weather_heavy_rain": 1.0 if weather == "heavy_rain" else 0.0,
        "weather_snow": 1.0 if weather == "snow" else 0.0,
        "weather_fog": 1.0 if weather == "fog" else 0.0,
        "weather_storm": 1.0 if weather == "storm" else 0.0,
        "transport_walk": 1.0 if transport_mode == "walk" else 0.0,
        "transport_subway": 1.0 if transport_mode == "subway" else 0.0,
        "transport_bus": 1.0 if transport_mode == "bus" else 0.0,
        "transport_taxi": 1.0 if transport_mode == "taxi" else 0.0,
        "transport_car": 1.0 if transport_mode == "car" else 0.0,
        "transport_ride_hailing": 1.0 if transport_mode == "ride_hailing" else 0.0,
        "luggage_normal": 1.0 if luggage == "normal" else 0.0,
        "luggage_heavy": 1.0 if luggage == "heavy" else 0.0,
    }

    return features


def _baseline_probability(remaining_min: float,
                          travel_time_mean_min: float,
                          travel_time_std_min: float,
                          entry_buffer_min: float,
                          context: Optional[TravelContext]) -> Dict[str, float]:
    context_factors = _context_multiplier(context)
    adjusted_mean_min = (
        travel_time_mean_min * context_factors["mean_multiplier"]
        + context_factors["user_bias_min"]
    )
    adjusted_std_min = max(0.0, travel_time_std_min * context_factors["std_multiplier"])

    if adjusted_std_min <= 0:
        success = (adjusted_mean_min + entry_buffer_min) <= remaining_min
        return {
            "probability": 1.0 if success else 0.0,
            "z": None,
            "adjusted_travel_mean_min": adjusted_mean_min,
            "adjusted_travel_std_min": adjusted_std_min,
        }

    z = (remaining_min - entry_buffer_min - adjusted_mean_min) / adjusted_std_min
    return {
        "probability": _normal_cdf(z),
        "z": z,
        "adjusted_travel_mean_min": adjusted_mean_min,
        "adjusted_travel_std_min": adjusted_std_min,
    }


def compute_catch_probability(depart_dt: datetime,
                              current_dt: datetime,
                              travel_time_mean_min: float,
                              travel_time_std_min: float,
                              entry_buffer_min: float = 20.0,
                              context: Optional[TravelContext] = None,
                              user_id: str = "default",
                              store_path: Optional[str] = None,
                              use_ml: bool = True) -> dict:
    """
    Compute P(travel_time + entry_buffer <= remaining_time).

    The optional `context` parameter allows weather, rush hour, transport mode,
    luggage, and user history to influence the estimate.

    Returns a dict with probability and intermediate values.
    """
    # remaining available minutes until departure
    remaining_min = (depart_dt - current_dt).total_seconds() / 60.0

    state = _load_state(store_path)
    user_entry = _get_user_entry(state, user_id)
    profile = user_entry.get("profile", {})
    profile_model = LogisticProbabilityModel.from_dict(user_entry.get("model"))

    features = _build_feature_snapshot(
        depart_dt,
        current_dt,
        travel_time_mean_min,
        travel_time_std_min,
        entry_buffer_min,
        context,
        profile,
    )

    baseline = _baseline_probability(
        remaining_min,
        travel_time_mean_min,
        travel_time_std_min,
        entry_buffer_min,
        context,
    )

    model_ready = bool(profile_model.feature_names) and int(profile.get("trips_seen", 0)) >= MIN_TRAINING_SAMPLES
    ml_probability = None
    blend_weight = 0.0
    if use_ml and model_ready:
        ml_probability = profile_model.predict_proba(features)
        trips_seen = int(profile.get("trips_seen", 0))
        blend_weight = _clamp((trips_seen - MIN_TRAINING_SAMPLES + 1) / 12.0, 0.0, 1.0)

    final_probability = baseline["probability"]
    if ml_probability is not None:
        final_probability = (
            baseline["probability"] * (1.0 - blend_weight)
            + ml_probability * blend_weight
        )
    final_probability = _clamp(final_probability, 0.0, 1.0)

    result = {
        "probability": final_probability,
        "baseline_probability": baseline["probability"],
        "ml_probability": ml_probability,
        "ml_blend_weight": blend_weight,
        "ml_model_ready": model_ready,
        "remaining_min": remaining_min,
        "travel_mean_min": travel_time_mean_min,
        "travel_std_min": travel_time_std_min,
        "adjusted_travel_mean_min": baseline["adjusted_travel_mean_min"],
        "adjusted_travel_std_min": baseline["adjusted_travel_std_min"],
        "entry_buffer_min": entry_buffer_min,
        "profile": {
            "bias_min": float(profile.get("bias_min", 0.0)),
            "variance_scale": float(profile.get("variance_scale", 0.0)),
            "trips_seen": int(profile.get("trips_seen", 0)),
        },
        "context": context.__dict__ if context is not None else TravelContext().__dict__,
        "feature_snapshot": features,
    }

    if baseline["z"] is not None:
        result["z"] = baseline["z"]

    return result


def update_user_context_bias(profile: Dict, trip_context: Dict, caught: bool,
                             learning_rate: float = 0.15) -> Dict:
    """
    Update a simple per-user bias profile after a trip.

    The profile stores two values:
    - bias_min: how many minutes to add/subtract from the base travel estimate
    - variance_scale: how much to widen/narrow uncertainty

    `trip_context` should contain the same contextual signals used for prediction.
    """
    profile = dict(profile or {})
    bias_min = float(profile.get("bias_min", 0.0))
    variance_scale = float(profile.get("variance_scale", 0.0))

    weather = str(trip_context.get("weather", "clear"))
    rush_hour = bool(trip_context.get("rush_hour", False))
    traffic_level = _clamp(float(trip_context.get("traffic_level", 0.0)), 0.0, 1.0)

    if caught:
        error_signal = -1.0
    else:
        error_signal = 1.0

    context_weight = 1.0
    if weather in {"rain", "heavy_rain", "snow", "fog", "storm"}:
        context_weight += 0.15
    if rush_hour:
        context_weight += 0.10
    context_weight += traffic_level * 0.20

    bias_min += learning_rate * error_signal * context_weight * 2.0
    variance_scale += learning_rate * (abs(error_signal) * context_weight - 0.5)

    profile["bias_min"] = _clamp(bias_min, -30.0, 30.0)
    profile["variance_scale"] = _clamp(variance_scale, -0.5, 1.0)
    profile["trips_seen"] = int(profile.get("trips_seen", 0)) + 1
    profile["last_trip_context"] = trip_context
    profile["last_trip_caught"] = bool(caught)
    return profile


def record_trip_outcome(user_id: str,
                        depart_dt: datetime,
                        current_dt: datetime,
                        travel_time_mean_min: float,
                        travel_time_std_min: float,
                        caught: bool,
                        entry_buffer_min: float = 20.0,
                        context: Optional[TravelContext] = None,
                        store_path: Optional[str] = None,
                        predicted_probability: Optional[float] = None,
                        actual_travel_time_min: Optional[float] = None) -> Dict:
    """
    Persist a completed trip, update the per-user profile, and retrain the
    per-user model automatically once enough samples have been collected.
    """
    state = _load_state(store_path)
    user_entry = _get_user_entry(state, user_id)
    profile = dict(user_entry.get("profile", {}))

    features = _build_feature_snapshot(
        depart_dt,
        current_dt,
        travel_time_mean_min,
        travel_time_std_min,
        entry_buffer_min,
        context,
        profile,
    )

    samples = user_entry.setdefault("samples", [])
    samples.append(
        {
            "features": features,
            "label": 1 if caught else 0,
            "created_at": datetime.utcnow().isoformat(timespec="seconds"),
            "predicted_probability": predicted_probability,
            "actual_travel_time_min": actual_travel_time_min,
            "context": context.__dict__ if context is not None else TravelContext().__dict__,
        }
    )
    if len(samples) > MAX_SAMPLES_PER_USER:
        del samples[:-MAX_SAMPLES_PER_USER]

    user_entry["profile"] = update_user_context_bias(
        profile,
        context.__dict__ if context is not None else TravelContext().__dict__,
        caught,
    )

    trained = False
    if len(samples) >= MIN_TRAINING_SAMPLES:
        model = LogisticProbabilityModel.train(samples)
        user_entry["model"] = model.to_dict()
        trained = True

    _save_state(state, store_path)

    return {
        "user_id": user_id,
        "caught": bool(caught),
        "sample_count": len(samples),
        "profile": user_entry["profile"],
        "model_trained": trained,
        "model_ready": bool(user_entry.get("model")),
        "store_path": str(Path(store_path) if store_path else STATE_FILE),
    }


def get_user_profile(user_id: str = "default", store_path: Optional[str] = None) -> Dict:
    """Load a user's current profile, including the learned model metadata."""
    state = _load_state(store_path)
    user_entry = _get_user_entry(state, user_id)
    return {
        "user_id": user_id,
        "profile": user_entry.get("profile", {}),
        "sample_count": len(user_entry.get("samples", [])),
        "model": user_entry.get("model"),
    }


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Estimate probability of catching a trip")
    p.add_argument("depart_datetime", help="Departure datetime (YYYY-MM-DD HH:MM)")
    p.add_argument("current_datetime", help="Current datetime (YYYY-MM-DD HH:MM)")
    p.add_argument("travel_mean", type=float, help="Mean travel time in minutes")
    p.add_argument("travel_std", type=float, help="Std dev of travel time in minutes")
    p.add_argument("--user-id", default="default", help="Local user id for profile storage")
    p.add_argument("--store-path", default=None, help="Optional JSON store path for user profiles")
    p.add_argument("--entry-buffer", type=float, default=20.0, help="Minutes needed at station/airport before departure")
    p.add_argument("--weather", default="clear", help="Weather: clear/cloudy/rain/heavy_rain/snow/fog/storm")
    p.add_argument("--transport-mode", default="taxi", help="Transport mode: walk/subway/bus/taxi/car/ride_hailing")
    p.add_argument("--rush-hour", action="store_true", help="Mark the trip as rush hour")
    p.add_argument("--weekend", action="store_true", help="Mark the trip as weekend")
    p.add_argument("--luggage", default="light", help="Luggage level: light/normal/heavy")
    p.add_argument("--traffic-level", type=float, default=0.0, help="Traffic level from 0.0 to 1.0")
    p.add_argument("--user-bias-min", type=float, default=0.0, help="User-specific bias in minutes")
    p.add_argument("--user-variance-scale", type=float, default=0.0, help="User-specific variance scale")
    p.add_argument("--outcome", choices=["caught", "missed"], default=None, help="Optional trip outcome to store and retrain")

    args = p.parse_args()
    depart_dt = datetime.strptime(args.depart_datetime, "%Y-%m-%d %H:%M")
    current_dt = datetime.strptime(args.current_datetime, "%Y-%m-%d %H:%M")

    context = TravelContext(
        weather=args.weather,
        transport_mode=args.transport_mode,
        rush_hour=args.rush_hour,
        weekend=args.weekend,
        luggage=args.luggage,
        traffic_level=args.traffic_level,
        user_bias_min=args.user_bias_min,
        user_variance_scale=args.user_variance_scale,
    )

    out = compute_catch_probability(
        depart_dt,
        current_dt,
        args.travel_mean,
        args.travel_std,
        args.entry_buffer,
        context,
        user_id=args.user_id,
        store_path=args.store_path,
    )
    print(out)

    if args.outcome is not None:
        update_result = record_trip_outcome(
            args.user_id,
            depart_dt,
            current_dt,
            args.travel_mean,
            args.travel_std,
            caught=(args.outcome == "caught"),
            entry_buffer_min=args.entry_buffer,
            context=context,
            store_path=args.store_path,
            predicted_probability=out["probability"],
        )
        print(update_result)
