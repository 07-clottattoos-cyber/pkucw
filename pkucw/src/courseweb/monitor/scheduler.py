from __future__ import annotations

from .service import MonitorService


def run_scheduler() -> None:
    MonitorService().run_forever()
