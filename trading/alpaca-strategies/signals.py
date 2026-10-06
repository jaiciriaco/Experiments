"""Experimental signal detectors. Input: closed OHLC bars in New York time."""
from datetime import time as dtime
from typing import Any, Dict, Optional
import os
import numpy as np
import pandas as pd

MIN_BOX_BARS = int(os.getenv("MIN_BOX_BARS", "12"))

def add_ha(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.assign(haO=[], haC=[], haH=[], haL=[])
    o, h, l, c = (df[x].to_numpy(float)
                  for x in ("open", "high", "low", "close"))
    hc = (o + h + l + c) / 4
    ho = np.empty_like(hc)
    ho[0] = (o[0] + c[0]) / 2
    for i in range(1, len(hc)):
        ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    out = df.copy()
    out["haO"], out["haC"] = ho, hc
    out["haH"] = np.maximum.reduce([h, ho, hc])
    out["haL"] = np.minimum.reduce([l, ho, hc])
    return out


# =========================
# DETECTORES DE SENAL (primera senal valida del dia)
# Devuelven dict(side=+1/-1, entry, sl, bar_time) o None.
# =========================
def _box(day: pd.DataFrame):
    box = day.between_time("04:00", "08:55")
    if len(box) < MIN_BOX_BARS:
        return None
    return float(box["high"].max()), float(box["low"].min())


def sig_A(day: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """NQ London breakout: primer cierre fuera de la caja entre 09:30 y 11:00."""
    lv = _box(day)
    if lv is None:
        return None
    hi, lo = lv
    for ts, r in day.iterrows():
        if not (dtime(9, 30) <= ts.time() <= dtime(10, 55)):
            continue
        if r.close > hi:
            return dict(side=1, entry=float(r.close), sl=float(r.low), bar_time=ts)
        if r.close < lo:
            return dict(side=-1, entry=float(r.close), sl=float(r.high), bar_time=ts)
    return None


def sig_B(day: pd.DataFrame, body_max=0.30, wick_min=0.25) -> Optional[Dict[str, Any]]:
    """Toque del maximo/minimo de Londres con vela doji Heikin Ashi -> entrada contra el nivel.
    (En vivo solo desde las 09:30: Alpaca no permite ordenes de mercado en premarket.)"""
    lv = _box(day)
    if lv is None:
        return None
    hi, lo = lv
    ha = add_ha(day)
    for ts, r in ha.iterrows():
        if not (dtime(9, 30) <= ts.time() <= dtime(10, 55)):
            continue
        rng = r.haH - r.haL
        if rng <= 0:
            continue
        body = abs(r.haC - r.haO)
        up = r.haH - max(r.haO, r.haC)
        dn = min(r.haO, r.haC) - r.haL
        if not (body <= body_max * rng and up >= wick_min * rng and dn >= wick_min * rng):
            continue
        t_hi, t_lo = r.high >= hi, r.low <= lo
        if t_hi == t_lo:
            continue
        if t_hi:
            return dict(side=-1, entry=float(r.close), sl=float(r.high), bar_time=ts)
        return dict(side=1, entry=float(r.close), sl=float(r.low), bar_time=ts)
    return None


def sig_D(day: pd.DataFrame, lookback=5, wait=15) -> Optional[Dict[str, Any]]:
    """Primera vela 09:30-09:35: toque del nivel + FVG de rechazo + vela envolvente al volver al vacio."""
    first = day.between_time("09:30", "09:34")
    if len(first) < 3:
        return None
    hi, lo = float(first.high.max()), float(first.low.min())
    d = day.between_time("09:30", "10:55")
    O, H, L, C = (d[x].to_numpy(float)
                  for x in ("open", "high", "low", "close"))
    n = len(d)
    for k in range(2, n):
        if d.index[k].time() < dtime(9, 35):
            continue
        for side in (-1, 1):
            if side == -1:
                ok = H[max(0, k - lookback):k +
                       1].max() >= hi and L[k - 2] > H[k]
                zone_in, zone_out = H[k], L[k - 2]
            else:
                ok = L[max(0, k - lookback):k +
                       1].min() <= lo and H[k - 2] < L[k]
                zone_in, zone_out = L[k], H[k - 2]
            if not ok:
                continue
            for m in range(k + 1, min(k + 1 + wait, n)):
                if (side == -1 and C[m] > zone_out) or (side == 1 and C[m] < zone_out):
                    break  # vacio invalidado
                in_zone = H[m] >= zone_in if side == -1 else L[m] <= zone_in
                if side == -1:
                    engulf = C[m - 1] > O[m -
                                          1] and C[m] < O[m] and O[m] >= C[m - 1] and C[m] <= O[m - 1]
                else:
                    engulf = C[m - 1] < O[m -
                                          1] and C[m] > O[m] and O[m] <= C[m - 1] and C[m] >= O[m - 1]
                if in_zone and engulf:
                    sl = H[m] if side == -1 else L[m]
                    return dict(side=side, entry=float(C[m]), sl=float(sl), bar_time=d.index[m])
    return None


DETECTORS = {"A": (sig_A, 5), "B": (sig_B, 5), "D": (
    sig_D, 1)}  # (funcion, minutos por vela)


