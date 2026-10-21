import numpy as np
import pandas as pd
import pytest

from stockvn.db import Repository


@pytest.fixture
def repo(tmp_path) -> Repository:
    return Repository(tmp_path / "test.db")


def make_prices(n: int = 600, seed: int = 0, start_price: float = 50_000, drift: float = 0.0003) -> pd.DataFrame:
    """Chuỗi giá giả lập (bước ngẫu nhiên hình học) với OHLCV hợp lệ."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=n, name="date")
    r = rng.normal(drift, 0.018, n)
    close = start_price * np.exp(np.cumsum(r))
    open_ = close * np.exp(rng.normal(0, 0.005, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, n))
    volume = rng.integers(500_000, 3_000_000, n).astype(float)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


@pytest.fixture
def prices() -> pd.DataFrame:
    return make_prices()
