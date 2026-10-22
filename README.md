# Chứng khoán VN — Thu thập · Cảnh báo giá · Dự báo

Ứng dụng web chạy trên máy cá nhân (Streamlit) cho thị trường chứng khoán Việt Nam (HOSE, HNX, UPCoM):

- **Thu thập dữ liệu**: giá OHLCV ngày từ nguồn công khai **VCI (Vietcap) → DNSE → KBS** (tự chuyển nguồn khi lỗi), lưu vào SQLite.
- **Bảng giá danh mục**: giá, +/-, %, khối lượng, KL so với TB20, biểu đồ mini 30 phiên, chỉ số VNINDEX/VN30.
- **Danh mục đầu tư**: sổ giao dịch mua/bán, giá vốn bình quân (gồm phí), lãi/lỗ chưa chốt / đã chốt / trong phiên, tỷ trọng.
- **Biểu đồ kỹ thuật**: nến, SMA20/50/200, Bollinger, khối lượng, RSI(14), MACD; xuất CSV.
- **Bộ lọc cổ phiếu**: lọc theo RSI, SMA200, giao cắt SMA/MACD, khối lượng đột biến, đỉnh/đáy 52 tuần, Bollinger, thanh khoản tối thiểu; theo danh mục, mã đã lưu hoặc cả sàn.
- **Cảnh báo giá** qua **Email (SMTP)**, **thông báo Windows** và **thông báo trình duyệt (Web Push)** và **ntfy**: giá vượt/thủng ngưỡng, % tăng/giảm trong phiên, khối lượng đột biến, RSI quá mua/quá bán, giá cắt lên/xuống SMA, phá đỉnh/thủng đáy 52 tuần, trailing stop, chạm trần/sàn.
- **Báo cáo cuối phiên** qua các kênh trên: biến động danh mục theo dõi, lãi/lỗ danh mục đầu tư, cảnh báo trong ngày.
- **Dự báo**: Naive (mốc chuẩn), Holt-Winters/ETS, ARIMA, Gradient Boosting, Prophet, LSTM và **Ensemble**; có khoảng dự báo, **kiểm định walk-forward** và **sổ dự báo** chấm điểm ngoài mẫu theo thời gian thật.
- **Backtest chiến lược** (giao cắt SMA, RSI đảo chiều, MACD, phá vỡ Donchian) có phí, thuế bán và T+2, so với mua & giữ.
- **Lịch nghỉ lễ** (`holidays.txt`): worker, ngày dự báo và trục biểu đồ bỏ qua ngày nghỉ.

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
| `WEBPUSH_VAPID_PRIVATE_KEY` | tự sinh, lưu trong CSDL | Khóa VAPID cho Web Push. Nên đặt cố định khi deploy cloud |
| `WEBPUSH_SUBJECT` | `mailto:SMTP_FROM` | Liên hệ gửi kèm cho dịch vụ push |
| `NTFY_TOPIC` | – | Topic ntfy (chữ, số, `-`, `_`, ≤ 64 ký tự). Để trống = tắt kênh ntfy |
| `NTFY_SERVER`, `NTFY_TOKEN` | `https://ntfy.sh`, – | Máy chủ ntfy tự host và access token (`Authorization: Bearer`) |
| `DIGEST_CHANNELS` | – | Kênh gửi báo cáo cuối phiên (`email,toast,webpush,ntfy`). Để trống = tắt |
| `FORECAST_LOG_MODELS` | `naive,ets,arima,gbm` | Mô hình worker tự dự báo mỗi tối để ghi sổ dự báo. Để trống = tắt |
| `FORECAST_LOG_HORIZON` | `20` | Tầm dự báo (phiên) của lần dự báo tự động |
| `TRADE_FEE_PCT`, `SELL_TAX_PCT` | `0.15`, `0.1` | Phí môi giới mỗi chiều và thuế bán (% giá trị lệnh) cho Danh mục đầu tư và Backtest |

Không commit `.env` (đã có trong `.gitignore`).

## Cấu trúc

```
app.py                 # điểm vào Streamlit (điều hướng) + khởi động worker kèm
worker.py              # worker chạy riêng (dòng lệnh)
holidays.txt           # ngày nghỉ giao dịch (ngoài T7, CN)
views/                 # các trang: tổng quan, danh mục đầu tư, biểu đồ, bộ lọc, cảnh báo, dự báo, backtest, cài đặt
  webpush_ui.py        # nút bật/tắt thông báo trình duyệt (component trong webpush_frontend/)
stockvn/
  config.py            # đọc .env
  market.py            # giờ VN (UTC+7), giờ giao dịch, ngày nghỉ lễ, biên độ giá, định dạng số kiểu VN
  collector.py         # nguồn VCI/DNSE/KBS + dự phòng + đồng bộ + phát hiện điều chỉnh giá
  db.py                # SQLite (WAL) — prices, quotes, watchlist, alerts, alert_log, trades, forecast_log
  indicators.py        # SMA, EMA, RSI, MACD, Bollinger
  alerts.py            # đánh giá điều kiện, kích hoạt theo cạnh
  notifier.py          # Email + Windows toast + Web Push + ntfy
  webpush.py           # Web Push (VAPID): khóa, đăng ký trình duyệt, gửi
  portfolio.py         # vị thế, giá vốn bình quân, lãi/lỗ
  digest.py            # báo cáo cuối phiên
  screener.py          # điều kiện lọc cổ phiếu
  strategy.py          # chiến lược + mô phỏng backtest
  service.py           # ghép thành phần, một vòng cập nhật
  worker.py            # vòng lặp worker, khóa một-worker-mỗi-CSDL, luồng nền cho giao diện, việc cuối ngày
  forecast/            # mô hình dự báo, kiểm định, ensemble, sổ dự báo (ledger.py)
tests/                 # pytest (không gọi mạng)
```

## Cách hoạt động

**Cảnh báo kích hoạt theo cạnh**: một cảnh báo chỉ gửi khi điều kiện *chuyển* từ sai sang đúng, rồi tự nạp lại khi điều kiện hết đúng — không gửi lặp mỗi phút. Tắt rồi bật lại một cảnh báo cũng nạp lại nó.

**Phát hiện điều chỉnh giá**: mỗi lần lấy giá, các phiên đã qua được so với bản đã lưu *từ cùng nguồn*. Nếu từ 2 phiên trở lên lệch quá 0,5% (nguồn đã điều chỉnh lịch sử sau chia cổ tức/tách cổ phiếu), app tự tải lại toàn bộ lịch sử để chuỗi giá liền mạch. Phiên hôm nay và một phiên lẻ lưu giữa giờ không tính.

**Việc cuối ngày** (worker, sau 15:00 ngày giao dịch, một lần): chốt giá → gửi báo cáo cuối phiên (`DIGEST_CHANNELS`) → dự báo các mã đang theo dõi và ghi sổ dự báo (`FORECAST_LOG_MODELS`).

**Cảnh báo kỹ thuật**: cắt SMA dùng giá hiện tại so với SMA *n* phiên (tính cả phiên hôm nay); đỉnh/đáy 52 tuần lấy 252 phiên trước hôm nay; trailing stop so với giá đóng cửa cao nhất kể từ ngày tạo cảnh báo; chạm trần/sàn dùng biên độ HOSE ±7%, HNX ±10%, UPCoM ±15% so với giá đóng cửa phiên trước (cần danh sách mã để biết sàn niêm yết).

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

**Sổ dự báo**: mỗi lần chạy dự báo (trên trang Dự báo hoặc worker mỗi tối) được ghi lại; khi đến ngày đích, dự báo được so với giá đóng cửa thực tế → MAPE, tỷ lệ đúng hướng, độ phủ khoảng dự báo theo từng mô hình. Khác walk-forward, đây là kiểm định ngoài mẫu thật sự, số điểm tăng dần theo thời gian.

**Backtest**: tín hiệu tính trên giá đóng cửa phiên *t*, khớp ở giá mở cửa phiên *t+1*; mỗi lần mua dùng toàn bộ vốn (cho phép lẻ cổ phiếu); trừ phí mỗi chiều và thuế bán; chỉ bán được sau số phiên tối thiểu (mặc định 2, theo T+2). Chưa tính trượt giá và giới hạn thanh khoản.

**Thông báo trình duyệt (Web Push)**: trang **Cài đặt** → *Bật thông báo trình duyệt* trên mỗi trình duyệt/thiết bị muốn nhận. Máy chủ (kể cả Linux/Streamlit Cloud) đẩy tin qua dịch vụ push của trình duyệt (FCM, Mozilla, WNS, Apple) và trình duyệt hiện popup của hệ điều hành — không cần mở tab app, nhưng trình duyệt phải đang chạy. Cần HTTPS hoặc `localhost`. Đăng ký hết hạn (HTTP 404/410) tự bị xóa.

**ntfy**: đặt `NTFY_TOPIC` (chuỗi ngẫu nhiên, dài) trong `.env`/Secrets, rồi đăng ký đúng topic đó trong app ntfy (Android/iOS) hoặc web app ntfy trên Chrome/Edge để nhận popup trên Windows. Tin gửi dạng JSON tới URL gốc của máy chủ ntfy nên tiêu đề tiếng Việt không bị lỗi mã hóa header; nội dung bị cắt ở 4.000 byte.

## Giới hạn đã biết

- API của VCI/DNSE/KBS là endpoint công khai, không có tài liệu chính thức; có thể thay đổi hoặc chặn bất cứ lúc nào. Khi đó sửa `stockvn/collector.py`.
- Giá "thời gian thực" là nến ngày hiện tại lấy theo chu kỳ, không phải luồng khớp lệnh tick-by-tick.
- Ngày nghỉ lễ đọc từ `holidays.txt`: 2017–2026 lấy từ dữ liệu giá đã lưu (các ngày thứ 2–6 không có phiên), không phải từ thông báo chính thức. Năm mới cần tự điền theo thông báo của HOSE/HNX; trang **Cài đặt** gợi ý các ngày đã qua còn thiếu.
- Biên độ trần/sàn là mức chung; ngày đầu niêm yết, ngày GDKHQ, cổ phiếu bị cảnh báo có thể khác. Giá tham chiếu HNX/UPCoM có thể khác giá đóng cửa phiên trước mà app dùng.
- Danh mục đầu tư chưa tính cổ tức tiền mặt; cổ phiếu thưởng ghi là lệnh mua giá 0.
- Bộ lọc "Theo sàn niêm yết" phải tải dữ liệu từng mã (~1 giây/mã) ở lần đầu.
- Các nguồn có thể điều chỉnh giá (cổ tức, chia tách) khác nhau. Bấm **⟳ Tải lại** trên trang Biểu đồ để tải lại toàn bộ lịch sử từ một nguồn.
- Thông báo Windows chỉ hiện khi máy bật, đã đăng nhập và worker đang chạy.
- ntfy.sh công khai: ai biết topic đều đọc và gửi được tin vào topic. Trang Cài đặt chỉ hiện 4 ký tự đầu của topic. Cần riêng tư hơn thì tự host ntfy và dùng `NTFY_TOKEN`.
- Web Push: app không có đăng nhập nên **ai mở được app đều bật được thông báo** và nhận cảnh báo của bạn (tối đa 20 trình duyệt). Safari trên iPhone/iPad chỉ nhận Web Push khi trang được thêm vào màn hình chính.
