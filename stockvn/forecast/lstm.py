"""LSTM (PyTorch): đọc cửa sổ 60 phiên (lợi suất + khối lượng), dự báo trực tiếp lợi suất lũy kế h phiên."""
from __future__ import annotations

import copy
import os

import numpy as np
import pandas as pd

from .base import Arr, BaseForecaster, quantiles_to_prices


class LSTMForecaster(BaseForecaster):
    key = "lstm"
    label = "LSTM (học sâu)"
    requires = ("torch",)
    min_history = 300
    default_lookback = 2000

    window = 60
    hidden = 32
    max_epochs = 60
    patience = 8
    batch_size = 64
    lr = 1e-3

    @staticmethod
    def _features(df: pd.DataFrame) -> tuple[Arr, Arr]:
        logc = np.log(df["close"].to_numpy(dtype=float))
        vol = df["volume"].astype(float).replace(0, np.nan)
        vol_ratio = np.log(vol / vol.rolling(20, min_periods=1).mean()).fillna(0).to_numpy()
        r = np.diff(logc)
        return logc, np.column_stack([r, vol_ratio[1:]])

    def _predict(self, df: pd.DataFrame, horizon: int) -> tuple[Arr, Arr, Arr]:
        import torch
        from torch import nn

        torch.manual_seed(0)
        torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))

        logc, feats = self._features(df)
        W, H = self.window, horizon
        # Mẫu j: cửa sổ feats[j-W+1..j] kết thúc ở giá p=j+1; nhãn logc[p+k]-logc[p], k=1..H
        ends = np.arange(W - 1, len(feats) - H)
        if len(ends) < 150:
            raise ValueError("LSTM: không đủ dữ liệu cho cửa sổ và tầm dự báo đã chọn")
        X = np.stack([feats[j - W + 1:j + 1] for j in ends])
        Y = np.stack([logc[j + 2:j + 2 + H] - logc[j + 1] for j in ends])

        n_val = max(40, len(X) // 6)
        tr_end = len(X) - n_val - H  # purge H mẫu để nhãn train/val không chồng lấn
        x_mu, x_sd = X[:tr_end].reshape(-1, X.shape[2]).mean(0), X[:tr_end].reshape(-1, X.shape[2]).std(0) + 1e-8
        y_sd = Y[:tr_end].std(0) + 1e-8

        def tx(a):
            return torch.tensor((a - x_mu) / x_sd, dtype=torch.float32)

        Xtr, Ytr = tx(X[:tr_end]), torch.tensor(Y[:tr_end] / y_sd, dtype=torch.float32)
        Xva, Yva = tx(X[-n_val:]), torch.tensor(Y[-n_val:] / y_sd, dtype=torch.float32)

        model = _Net(X.shape[2], self.hidden, H)
        opt = torch.optim.Adam(model.parameters(), lr=self.lr, weight_decay=1e-4)
        loss_fn = nn.MSELoss()
        best, best_state, bad = float("inf"), None, 0
        gen = torch.Generator().manual_seed(0)
        for _ in range(self.max_epochs):
            model.train()
            for idx in torch.randperm(len(Xtr), generator=gen).split(self.batch_size):
                opt.zero_grad()
                loss = loss_fn(model(Xtr[idx]), Ytr[idx])
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
            model.eval()
            with torch.no_grad():
                val = loss_fn(model(Xva), Yva).item()
            if val < best - 1e-5:
                best, best_state, bad = val, copy.deepcopy(model.state_dict()), 0
            else:
                bad += 1
                if bad >= self.patience:
                    break
        model.load_state_dict(best_state)
        model.eval()

        with torch.no_grad():
            val_pred = model(Xva).numpy() * y_sd
            pred = model(tx(feats[-W:][None, ...])).numpy()[0] * y_sd
        resid = Y[-n_val:] - val_pred
        q_lo = np.quantile(resid, self.alpha / 2, axis=0)
        q_hi = np.quantile(resid, 1 - self.alpha / 2, axis=0)
        return quantiles_to_prices(float(df["close"].iloc[-1]), pred, pred + q_lo, pred + q_hi)


def _Net(n_features: int, hidden: int, horizon: int):
    from torch import nn

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.lstm = nn.LSTM(n_features, hidden, num_layers=1, batch_first=True)
            self.drop = nn.Dropout(0.2)
            self.head = nn.Linear(hidden, horizon)

        def forward(self, x):
            out, _ = self.lstm(x)
            return self.head(self.drop(out[:, -1, :]))

    return Net()
