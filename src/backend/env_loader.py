from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: str | Path | None = None, override: bool = False) -> None:
    env_path = Path(path) if path else REPO_ROOT / ".env"
    if not env_path.exists():
        return

    with env_path.open("r", encoding="utf-8") as file:
        for line in file:
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            key, value = text.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key:
                continue
            if override or key not in os.environ:
                os.environ[key] = value
