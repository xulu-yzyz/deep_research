from __future__ import annotations

from datetime import datetime
from typing import Any


def trace(component: str, message: str, **fields: Any) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    extras = " ".join(f"{k}={v!r}" for k, v in fields.items())
    line = f"[{ts}] [{component}] {message}"
    if extras:
        line = f"{line} | {extras}"
    print(line, flush=True)