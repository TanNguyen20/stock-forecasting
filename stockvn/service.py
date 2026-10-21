"""Ghép các thành phần: một vòng cập nhật giá + kiểm tra cảnh báo (dùng chung cho worker và giao diện)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache

from .alerts import AlertEngine, Triggered
from .collector import Collector
from .config import get_settings
from .db import Repository
from .market import now_vn
from .models import Quote
from .notifier import Notifier

log = logging.getLogger(__name__)

HEARTBEAT_KEY = "worker_heartbeat"


@dataclass
class CycleResult:
    quotes: dict[str, Quote] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    fired: list[Triggered] = field(default_factory=list)


@dataclass
class App:
    repo: Repository
    collector: Collector
    notifier: Notifier
    engine: AlertEngine

    def tracked_symbols(self) -> list[str]:
        symbols = set(self.repo.watchlist()) | {a.symbol for a in self.repo.list_alerts(active_only=True)}
        return sorted(symbols)

    def run_cycle(self, heartbeat: bool = True) -> CycleResult:
        symbols = self.tracked_symbols()
        quotes, errors = self.collector.refresh_quotes(symbols)
        fired = self.engine.check(quotes)
        for sym, err in errors.items():
            log.warning("Không cập nhật được %s: %s", sym, err)
        if heartbeat:
            self.repo.set_meta(HEARTBEAT_KEY, now_vn().isoformat())
        return CycleResult(quotes, errors, fired)


@lru_cache(maxsize=1)
def build_app() -> App:
    s = get_settings()
    repo = Repository(s.db_path)
    notifier = Notifier(s)
    return App(repo=repo, collector=Collector(repo, s.sources), notifier=notifier,
               engine=AlertEngine(repo, notifier))
