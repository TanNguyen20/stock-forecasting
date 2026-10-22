"""Thu thập dữ liệu giá từ các nguồn công khai (VCI/Vietcap, DNSE, KBS) có cơ chế dự phòng.

Mọi nguồn trả về DataFrame chuẩn hóa: index = ngày (DatetimeIndex, không múi giờ),
cột open/high/low/close/volume, giá tính bằng VND (chỉ số tính bằng điểm).
"""
from __future__ import annotations

import logging
import time as _time
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .db import PRICE_COLUMNS, Repository
from .market import VN_TZ, is_index, now_vn, today_vn
from .models import Quote

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
TIMEOUT = 20
# Phát hiện điều chỉnh giá: >= 2 phiên đã qua lệch > 0,5% so với bản đã lưu từ cùng nguồn.
ADJUST_TOLERANCE = 0.005
ADJUST_MIN_SESSIONS = 2


class SourceError(RuntimeError):
    pass


def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=0.8, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset({"GET", "POST"}))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return s


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=PRICE_COLUMNS, index=pd.DatetimeIndex([], name="date"), dtype=float)


def _finalize(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if df.empty:
        return _empty()
    df = df[PRICE_COLUMNS].astype(float)
    df.index = pd.DatetimeIndex(df.index, name="date")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.loc[pd.Timestamp(start):pd.Timestamp(end)]


def _epoch_to_vn_date(values: Iterable) -> list[pd.Timestamp]:
    return [pd.Timestamp(datetime.fromtimestamp(int(v), VN_TZ).date()) for v in values]


# ---------------------------------------------------------------- Nguồn dữ liệu
class VCISource:
    """Vietcap (trading.vietcap.com.vn). Giá VND, đã điều chỉnh theo sự kiện doanh nghiệp."""

    name = "VCI"
    CHART_URL = "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart"
    SYMBOLS_URL = "https://trading.vietcap.com.vn/api/price/symbols/getAll"
    HEADERS = {"Referer": "https://trading.vietcap.com.vn/", "Origin": "https://trading.vietcap.com.vn"}

    def __init__(self, session: requests.Session | None = None):
        self.session = session or make_session()

    def history(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        count = int(np.busday_count(start, end + timedelta(days=1))) + 10
        to_ts = int(datetime.combine(end + timedelta(days=1), time(), VN_TZ).timestamp())
        payload = {"timeFrame": "ONE_DAY", "symbols": [symbol], "to": to_ts, "countBack": count}
        r = self.session.post(self.CHART_URL, json=payload, headers=self.HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return _finalize(parse_vci(r.json()), start, end)

    def symbols(self) -> pd.DataFrame:
        r = self.session.get(self.SYMBOLS_URL, headers=self.HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return parse_vci_symbols(r.json())


class DNSESource:
    """DNSE / Entrade (chuẩn TradingView). Giá cổ phiếu trả về theo nghìn VND."""

    name = "DNSE"
    URL = "https://services.entrade.com.vn/chart-api/v2/ohlcs/{kind}"

    def __init__(self, session: requests.Session | None = None):
        self.session = session or make_session()

    def history(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        kind = "index" if is_index(symbol) else "stock"
        params = {
            "symbol": symbol,
            "resolution": "1D",
            "from": int(datetime.combine(start, time(), VN_TZ).timestamp()),
            "to": int(datetime.combine(end + timedelta(days=1), time(), VN_TZ).timestamp()),
        }
        r = self.session.get(self.URL.format(kind=kind), params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return _finalize(parse_dnse(r.json(), scale=1 if kind == "index" else 1000), start, end)


class KBSSource:
    """KB Securities. Giá VND. Chỉ hỗ trợ cổ phiếu."""

    name = "KBS"
    URL = "https://kbbuddywts.kbsec.com.vn/iis-server/investment/stocks/{symbol}/data_day"

    def __init__(self, session: requests.Session | None = None):
        self.session = session or make_session()

    def history(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        if is_index(symbol):
            return _empty()
        params = {"sdate": start.strftime("%d-%m-%Y"), "edate": end.strftime("%d-%m-%Y")}
        r = self.session.get(self.URL.format(symbol=symbol), params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return _finalize(parse_kbs(r.json()), start, end)


SOURCES = {cls.name: cls for cls in (VCISource, DNSESource, KBSSource)}


# ---------------------------------------------------------------- Bộ phân tích phản hồi
def parse_vci(payload: list) -> pd.DataFrame:
    if not payload:
        return _empty()
    d = payload[0]
    return pd.DataFrame(
        {"open": d["o"], "high": d["h"], "low": d["l"], "close": d["c"], "volume": d["v"]},
        index=_epoch_to_vn_date(d["t"]),
    )


def parse_dnse(payload: dict, scale: float) -> pd.DataFrame:
    if not payload or not payload.get("t"):
        return _empty()
    df = pd.DataFrame(
        {"open": payload["o"], "high": payload["h"], "low": payload["l"], "close": payload["c"],
         "volume": payload["v"]},
        index=_epoch_to_vn_date(payload["t"]),
    )
    df[["open", "high", "low", "close"]] = (df[["open", "high", "low", "close"]] * scale).round(2)
    return df


def parse_kbs(payload: dict) -> pd.DataFrame:
    rows = (payload or {}).get("data_day") or []
    if not rows:
        return _empty()
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["t"].str[:10])
    return df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})


def parse_vci_symbols(payload: list) -> pd.DataFrame:
    df = pd.DataFrame(payload)
    if df.empty:
        return pd.DataFrame(columns=["symbol", "exchange", "type", "name"])
    name = df["organName"] if "organName" in df else pd.Series("", index=df.index)
    if "organShortName" in df:
        name = df["organShortName"].fillna(name)
    return pd.DataFrame({
        "symbol": df["symbol"], "exchange": df["board"].replace({"HSX": "HOSE"}),
        "type": df["type"], "name": name.fillna(""),
    })


# ---------------------------------------------------------------- Bộ thu thập
class Collector:
    def __init__(self, repo: Repository, source_names: Iterable[str] = ("VCI", "DNSE", "KBS"),
                 session: requests.Session | None = None):
        self.repo = repo
        self.session = session or make_session()
        self.sources = [SOURCES[n](self.session) for n in source_names if n in SOURCES]
        if not self.sources:
            raise ValueError("Không có nguồn dữ liệu hợp lệ")

    def fetch_history(self, symbol: str, start: date, end: date) -> tuple[pd.DataFrame, str]:
        """Thử lần lượt từng nguồn, trả về kết quả đầu tiên không rỗng."""
        symbol = symbol.upper()
        errors = []
        for src in self.sources:
            try:
                df = src.history(symbol, start, end)
                if not df.empty:
                    return df, src.name
                errors.append(f"{src.name}: không có dữ liệu")
            except Exception as exc:  # noqa: BLE001 - lỗi mạng/định dạng -> thử nguồn kế tiếp
                log.warning("Nguồn %s lỗi với %s: %s", src.name, symbol, exc)
                errors.append(f"{src.name}: {exc}")
        raise SourceError(f"Không lấy được dữ liệu {symbol}. " + "; ".join(errors))

    def sync_history(self, symbol: str, years: int = 10, full: bool = False) -> int:
        """Đồng bộ lịch sử vào DB. full=True: tải lại toàn bộ (cập nhật giá điều chỉnh)."""
        symbol = symbol.upper()
        end = today_vn()
        _, last, count = self.repo.price_range(symbol)
        if full or last is None or count < 30:
            df, src = self.fetch_history(symbol, end - timedelta(days=365 * years), end)
            return self.repo.replace_prices(symbol, df, src)
        df, src = self.fetch_history(symbol, last - timedelta(days=7), end)
        return self._store_recent(symbol, df, src, years)

    def history_adjusted(self, symbol: str, fresh: pd.DataFrame, source: str) -> bool:
        """Nguồn đã điều chỉnh giá các phiên đã qua (cổ tức, chia tách...) so với bản đang lưu?

        Chỉ so với các phiên đã lưu từ cùng nguồn (mỗi nguồn điều chỉnh một kiểu) và bỏ qua phiên hôm nay
        (giá còn chạy). Điều chỉnh làm lệch mọi phiên trước ngày GDKHQ, còn phiên cuối lưu giữa giờ chỉ lệch
        một phiên -> cần ít nhất ADJUST_MIN_SESSIONS phiên lệch quá ADJUST_TOLERANCE.
        """
        past = fresh.loc[fresh.index < pd.Timestamp(today_vn()), "close"]
        if past.empty:
            return False
        stored = self.repo.get_prices(symbol, start=past.index[0].date(), end=past.index[-1].date(),
                                      with_source=True)
        stored = stored.loc[stored["source"] == source, "close"]
        common = stored.index.intersection(past.index)
        drift = (past.loc[common] / stored.loc[common] - 1).abs()
        return int((drift > ADJUST_TOLERANCE).sum()) >= ADJUST_MIN_SESSIONS

    def _store_recent(self, symbol: str, df: pd.DataFrame, source: str, years: int = 10) -> int:
        """Lưu các phiên gần đây; nếu nguồn đã điều chỉnh giá quá khứ thì tải lại toàn bộ để lịch sử liền mạch."""
        if self.history_adjusted(symbol, df, source):
            log.info("%s: nguồn %s đã điều chỉnh giá lịch sử (cổ tức/chia tách), tải lại toàn bộ", symbol, source)
            try:
                return self.sync_history(symbol, years, full=True)
            except Exception as exc:  # noqa: BLE001 - lỗi mạng: lưu tạm các phiên mới, vòng sau thử lại
                log.warning("Tải lại toàn bộ %s thất bại: %s", symbol, exc)
        return self.repo.upsert_prices(symbol, df, source)

    def refresh_quote(self, symbol: str) -> Quote:
        """Lấy giá mới nhất (nến ngày hiện tại) và lưu cả vào lịch sử."""
        symbol = symbol.upper()
        end = today_vn()
        df, src = self.fetch_history(symbol, end - timedelta(days=45), end)
        self._store_recent(symbol, df, src)
        quote = build_quote(symbol, df, src)
        self.repo.upsert_quote(quote)
        return quote

    def refresh_quotes(self, symbols: Iterable[str], pause: float = 0.3) -> tuple[dict[str, Quote], dict[str, str]]:
        quotes, errors = {}, {}
        for i, sym in enumerate(dict.fromkeys(s.upper() for s in symbols)):
            if i:
                _time.sleep(pause)  # tránh gửi dồn dập tới máy chủ nguồn
            try:
                quotes[sym] = self.refresh_quote(sym)
            except Exception as exc:  # noqa: BLE001
                errors[sym] = str(exc)
        return quotes, errors

    def refresh_symbols(self) -> int:
        return self.repo.upsert_symbols(VCISource(self.session).symbols())


def build_quote(symbol: str, df: pd.DataFrame, source: str) -> Quote:
    if df.empty:
        raise SourceError(f"Không có dữ liệu cho {symbol}")
    last = df.iloc[-1]
    ref = df["close"].iloc[-2] if len(df) > 1 else last["open"]
    prev_vol = df["volume"].iloc[-21:-1]
    return Quote(
        symbol=symbol,
        date=df.index[-1].date(),
        price=float(last["close"]),
        ref_price=float(ref),
        volume=float(last["volume"]),
        avg_volume20=float(prev_vol.mean()) if len(prev_vol) else 0.0,
        updated_at=now_vn(),
        source=source,
    )
