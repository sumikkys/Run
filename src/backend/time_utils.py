from __future__ import annotations


MINUTES_PER_DAY = 24 * 60


def parse_clock(value: str) -> int | None:
    text = str(value or "").strip()
    if not text or text == "----":
        return None
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError(f"unsupported clock: {value!r}")
    return int(parts[0]) * 60 + int(parts[1])


def parse_duration(value: str | int | float) -> int:
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)

    parts = text.split(":")
    if len(parts) == 2:
        hours, minutes = parts
        return int(hours) * 60 + int(minutes)
    if len(parts) == 3:
        days, hours, minutes = parts
        return int(days) * MINUTES_PER_DAY + int(hours) * 60 + int(minutes)
    raise ValueError(f"unsupported duration: {value!r}")


def absolute_from_clock_and_elapsed(clock_text: str, elapsed_text: str) -> int | None:
    clock = parse_clock(clock_text)
    if clock is None:
        return None

    elapsed = parse_duration(elapsed_text)
    day = elapsed // MINUTES_PER_DAY
    absolute = day * MINUTES_PER_DAY + clock
    if absolute < elapsed:
        absolute += MINUTES_PER_DAY
    return absolute


def next_daily_time_on_or_after(service_abs_min: int, earliest_abs_min: int) -> int:
    if service_abs_min >= earliest_abs_min:
        return service_abs_min
    repeats = (earliest_abs_min - service_abs_min + MINUTES_PER_DAY - 1) // MINUTES_PER_DAY
    return service_abs_min + repeats * MINUTES_PER_DAY


def format_abs_min(value: int) -> str:
    days, minute_of_day = divmod(value, MINUTES_PER_DAY)
    hours, minutes = divmod(minute_of_day, 60)
    suffix = f"+{days}d" if days else ""
    return f"{hours:02d}:{minutes:02d}{suffix}"

