import threading
from types import SimpleNamespace

import pytest

from stockvn import worker
from stockvn.service import CycleResult
from stockvn.worker import InstanceLock, run_loop, serve


class FakeApp:
    """Đếm số vòng; đặt `stop` sau `cycles` vòng để vòng lặp thoát."""

    def __init__(self, stop: threading.Event, cycles: int = 1):
        self.stop, self.cycles, self.calls = stop, cycles, 0
        self.repo = SimpleNamespace(get_meta=lambda key: None, set_meta=lambda key, value: None)

    def run_cycle(self) -> CycleResult:
        self.calls += 1
        if self.calls >= self.cycles:
            self.stop.set()
        return CycleResult()


@pytest.fixture
def lock_file(tmp_path, monkeypatch):
    path = tmp_path / "stock.db.worker.lock"
    monkeypatch.setattr(worker, "lock_path", lambda: path)
    return path


def test_lock_is_exclusive_until_released(tmp_path):
    path = tmp_path / "x.lock"
    a, b = InstanceLock(path), InstanceLock(path)
    assert a.acquire()
    assert a.acquire()  # gọi lại khi đang giữ vẫn True
    assert not b.acquire()
    a.release()
    assert b.acquire()
    b.release()


def test_run_loop_force_runs_cycles_until_stopped():
    stop = threading.Event()
    app = FakeApp(stop, cycles=2)
    run_loop(app, stop, interval=1, force=True)
    assert app.calls == 2


def test_serve_runs_and_releases_lock(lock_file, monkeypatch):
    stop = threading.Event()
    app = FakeApp(stop)
    monkeypatch.setattr(worker, "create_app", lambda: app)
    states: list[str] = []
    serve(stop, interval=1, force=True, on_state=states.append)
    assert app.calls == 1
    assert states == ["running"]
    other = InstanceLock(lock_file)
    assert other.acquire()  # khóa đã được nhả
    other.release()


def test_serve_waits_when_another_worker_holds_lock(lock_file, monkeypatch):
    monkeypatch.setattr(worker, "LOCK_RETRY", 1)
    monkeypatch.setattr(worker, "create_app", lambda: pytest.fail("không được chạy khi chưa có khóa"))
    holder = InstanceLock(lock_file)
    assert holder.acquire()
    stop = threading.Event()
    states: list[str] = []
    t = threading.Thread(target=serve, args=(stop, 1), kwargs={"on_state": states.append})
    t.start()
    try:
        t.join(0.5)
        assert t.is_alive()
        assert states == ["standby"]
    finally:
        stop.set()
        t.join(5)
        holder.release()
    assert not t.is_alive()


def test_serve_takes_over_after_lock_released(lock_file, monkeypatch):
    monkeypatch.setattr(worker, "LOCK_RETRY", 1)
    stop = threading.Event()
    app = FakeApp(stop)
    monkeypatch.setattr(worker, "create_app", lambda: app)
    holder = InstanceLock(lock_file)
    assert holder.acquire()
    states: list[str] = []
    waiting = threading.Event()

    def on_state(state: str) -> None:
        states.append(state)
        if state == "standby":
            waiting.set()

    t = threading.Thread(target=serve, args=(stop, 1), kwargs={"force": True, "on_state": on_state})
    t.start()
    assert waiting.wait(5)
    holder.release()
    t.join(10)
    assert not t.is_alive()
    assert states == ["standby", "running"]
    assert app.calls == 1
