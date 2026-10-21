# Chứng khoán VN — Thu thập · Cảnh báo giá · Dự báo

Ứng dụng web chạy trên máy cá nhân (Streamlit) cho thị trường chứng khoán Việt Nam (HOSE, HNX, UPCoM):

- **Thu thập dữ liệu**: giá OHLCV ngày từ nguồn công khai **VCI (Vietcap) → DNSE → KBS** (tự chuyển nguồn khi lỗi), lưu vào SQLite.
- **Bảng giá danh mục**: giá, +/-, %, khối lượng, KL so với TB20, biểu đồ mini 30 phiên, chỉ số VNINDEX/VN30.
- **Biểu đồ kỹ thuật**: nến, SMA20/50/200, Bollinger, khối lượng, RSI(14), MACD; xuất CSV.
- **Cảnh báo giá** qua **Email (SMTP)** và **thông báo Windows**: giá vượt/thủng ngưỡng, % tăng/giảm trong phiên, khối lượng đột biến, RSI quá mua/quá bán.
- **Dự báo**: Naive (mốc chuẩn), Holt-Winters/ETS, ARIMA, Gradient Boosting, Prophet, LSTM và **Ensemble**; có khoảng dự báo và **kiểm định walk-forward**.

> ⚠️ Dữ liệu từ nguồn công khai có thể chậm hoặc sai lệch. Dự báo chỉ mang tính tham khảo, **không phải khuyến nghị đầu tư**.

## Cài đặt (Windows)

Yêu cầu Python 3.11+ (đã thử với 3.13).

```bat
setup.bat
```

Hoặc thủ công:

```bat
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-ml.txt
copy .env.example .env
```

`requirements.txt` chỉ gồm phần lõi (không có Prophet/LSTM). `requirements-ml.txt` thêm `prophet` và `torch`.

## Chạy

| Việc | Lệnh |
|---|---|
| Mở giao diện + worker cảnh báo | `run_app.bat` (= `python app.py`) → http://localhost:8501 |
| Chỉ chạy worker, không giao diện | `run_worker.bat` |
| Một vòng worker rồi thoát | `run_worker.bat --once` |
| Chạy kiểm thử | `.venv\Scripts\python -m pytest` |

Worker cập nhật giá mỗi `STOCK_POLL_SECONDS` giây trong giờ giao dịch (9:00–11:30, 13:00–15:00, thứ 2–6), chốt giá một lần sau 15:00.

**Worker chạy kèm giao diện** (mặc định, tắt bằng `STOCK_EMBED_WORKER=false`): là một luồng nền trong tiến trình Streamlit, log ở `logs/app.log`.

- `python app.py` / `run_app.bat`: worker khởi động ngay cùng máy chủ, không cần mở trình duyệt.
- `streamlit run app.py`: worker khởi động khi có phiên trình duyệt đầu tiên.

Mỗi CSDL chỉ có **một** worker hoạt động (khóa file `data/stock.db.worker.lock`), nên chạy thêm `run_worker.bat` không gây gửi cảnh báo trùng: worker đến sau chờ và tự tiếp quản khi worker trước dừng; `--once` thì bỏ qua. Để cảnh báo chạy cả khi không mở giao diện, tự chạy worker cùng Windows: **Task Scheduler → Create Task → Trigger "At log on" → Action: `run_worker.bat`**.

## Cấu hình `.env`

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `STOCK_DB_PATH` | `data/stock.db` | File SQLite |
| `STOCK_SOURCES` | `VCI,DNSE,KBS` | Thứ tự nguồn dữ liệu |
| `STOCK_POLL_SECONDS` | `60` | Chu kỳ worker (tối thiểu 15) |
| `STOCK_HISTORY_YEARS` | `10` | Số năm lịch sử tải lần đầu |
| `STOCK_EMBED_WORKER` | `true` | Chạy worker cảnh báo kèm giao diện (`app.py`) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USE_SSL` | –, `465`, `true` | Máy chủ email (`false` = STARTTLS, thường cổng 587) |
| `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | – | Tài khoản gửi. Gmail: dùng **Mật khẩu ứng dụng** |
| `SMTP_TO` | – | Người nhận, cách nhau dấu phẩy |
| `TOAST_ENABLED` | `true` | Bật/tắt thông báo Windows |

Không commit `.env` (đã có trong `.gitignore`).

## Cấu trúc

```
app.py                 # điểm vào Streamlit (điều hướng) + khởi động worker kèm
worker.py              # worker chạy riêng (dòng lệnh)
views/                 # các trang: tổng quan, biểu đồ, cảnh báo, dự báo, cài đặt
stockvn/
  config.py            # đọc .env
  market.py            # giờ VN (UTC+7), giờ giao dịch, định dạng số kiểu VN
  collector.py         # nguồn VCI/DNSE/KBS + dự phòng + đồng bộ
  db.py                # SQLite (WAL) — prices, quotes, watchlist, alerts, alert_log
  indicators.py        # SMA, EMA, RSI, MACD, Bollinger
  alerts.py            # đánh giá điều kiện, kích hoạt theo cạnh
  notifier.py          # Email + Windows toast
  service.py           # ghép thành phần, một vòng cập nhật
  worker.py            # vòng lặp worker, khóa một-worker-mỗi-CSDL, luồng nền cho giao diện
  forecast/            # mô hình dự báo, kiểm định, ensemble
tests/                 # pytest (không gọi mạng)
```

## Cách hoạt động

**Cảnh báo kích hoạt theo cạnh**: một cảnh báo chỉ gửi khi điều kiện *chuyển* từ sai sang đúng, rồi tự nạp lại khi điều kiện hết đúng — không gửi lặp mỗi phút. Tắt rồi bật lại một cảnh báo cũng nạp lại nó.

**Dự báo** (trên log giá, tầm 5–60 phiên):

| Mô hình | Cách làm | Khoảng dự báo |
|---|---|---|
| Naive | giữ giá cuối (bước ngẫu nhiên) | σ·√h |
| Holt-Winters (ETS) | xu hướng cộng tính tắt dần | giải tích (statsmodels) |
| ARIMA | chọn (p,1,q) ± drift theo AIC | giải tích |
| Gradient Boosting | dự báo trực tiếp lợi suất lũy kế từ 18 đặc trưng kỹ thuật | phân vị phần dư trên tập hiệu chỉnh (có purge) |
| Prophet | xu hướng từng đoạn + mùa vụ năm | của Prophet |
| LSTM | cửa sổ 60 phiên (lợi suất + KL), early stopping | phân vị phần dư trên tập kiểm tra |
| Ensemble | trung bình trọng số trong không gian log, trọng số ∝ 1/MAPE kiểm định | trung bình trọng số |

**Kiểm định walk-forward**: cắt chuỗi tại *k* mốc cuối dữ liệu, mỗi mốc dự báo *h* phiên rồi so với thực tế → MAPE, RMSE, tỷ lệ đúng hướng, độ phủ khoảng dự báo. Luôn so với **Naive**: mô hình không thắng Naive thì không có giá trị dự báo hơn việc giữ nguyên giá.

## Giới hạn đã biết

- API của VCI/DNSE/KBS là endpoint công khai, không có tài liệu chính thức; có thể thay đổi hoặc chặn bất cứ lúc nào. Khi đó sửa `stockvn/collector.py`.
- Giá "thời gian thực" là nến ngày hiện tại lấy theo chu kỳ, không phải luồng khớp lệnh tick-by-tick.
- Lịch giao dịch chưa trừ ngày nghỉ lễ (Tết, 30/4, 2/9…): worker vẫn chạy nhưng không có giá mới; ngày dự báo có thể rơi vào ngày lễ.
- Các nguồn có thể điều chỉnh giá (cổ tức, chia tách) khác nhau. Bấm **⟳ Tải lại** trên trang Biểu đồ để tải lại toàn bộ lịch sử từ một nguồn.
- Thông báo Windows chỉ hiện khi máy bật, đã đăng nhập và worker đang chạy.
