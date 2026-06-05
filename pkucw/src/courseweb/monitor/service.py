from __future__ import annotations

import time
from typing import Callable

from .config import database_path, load_config
from .diff import DiffEngine, DiffOptions
from .models import CourseSnapshot
from .notifier import Notifier
from .snapshot import collect_snapshots
from .store import MonitorStore
from .subscriptions import SubscriptionEngine


SnapshotProvider = Callable[[], list[CourseSnapshot]]


class MonitorService:
    def __init__(
        self,
        *,
        store: MonitorStore | None = None,
        config: dict | None = None,
        snapshot_provider: SnapshotProvider | None = None,
    ):
        self.config = config or load_config()
        self.store = store or MonitorStore(database_path())
        self.snapshot_provider = snapshot_provider or (lambda: collect_snapshots())
        self.diff_engine = DiffEngine()
        self.subscription_engine = SubscriptionEngine(self.config.get("subscriptions"))
        self.notifier = Notifier(self.config.get("notifiers"))

    def scan(self, *, notify: bool = True) -> dict:
        first_scan_notify = bool(self.config.get("monitor", {}).get("first_scan_notify", False))
        snapshots = self.snapshot_provider()
        event_count = 0
        delivery_count = 0
        course_results = []
        for snapshot in snapshots:
            try:
                old_snapshot = self.store.latest_snapshot(snapshot.course_id)
                events = self.diff_engine.diff(
                    old_snapshot,
                    snapshot,
                    options=DiffOptions(first_scan_notify=first_scan_notify),
                )
                self.store.save_snapshot(snapshot)
                self.store.add_events(events)
                event_count += len(events)
                if notify:
                    delivery_count += self._deliver(events)
                self.store.set_course_status(snapshot.course_id, ok=True)
                course_results.append(
                    {
                        "course_id": snapshot.course_id,
                        "course_name": snapshot.course_name,
                        "events": len(events),
                        "baseline": old_snapshot is None,
                    }
                )
            except Exception as exc:
                self.store.set_course_status(snapshot.course_id, ok=False, error=str(exc))
                course_results.append({"course_id": snapshot.course_id, "ok": False, "error": str(exc)})
        return {"courses": course_results, "events": event_count, "deliveries": delivery_count}

    def run_forever(self) -> None:
        interval = int(self.config.get("monitor", {}).get("interval_seconds", 300))
        while True:
            self.scan(notify=True)
            time.sleep(max(60, interval))

    def _deliver(self, events) -> int:
        count = 0
        for event in events:
            plans = self.subscription_engine.match(event)
            for plan in plans:
                status = self.notifier.deliver(plan)
                self.store.add_delivery(plan.to_dict(), status=status)
                if status == "delivered":
                    count += 1
        return count
