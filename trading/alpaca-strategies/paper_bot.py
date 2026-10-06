"""
ganazor_daytrading_bot_v3.py

Version del bot adaptada a las nuevas tecnicas (London breakout, doji Heikin Ashi, primera vela + FVG).
Se ejecuta UNA vez al dia (Programador de tareas), opera como mucho UNA vez y se cierra solo.

Cambios respecto a v2:
- Credenciales SOLO por variables de entorno / fichero .env (nada en el codigo).
- Se elimina el scanner de gaps, VWAP y RSI. Ahora el activo es fijo (SYMBOL, por defecto QQQ = proxy de NQ).
- Entradas: caja de Londres 04:00-09:00 NY (estrategias A y B) o primera vela 09:30-09:35 (estrategia D).
- Salidas: orden BRACKET en Alpaca (stop loss y take profit 2R viviendo en el servidor) + cierre
  forzado a SESSION_END. Largos y cortos. Tamano por riesgo (RISK_PCT del equity entre distancia al stop).
- Un solo trade al dia: la primera senal valida gana. Si hay dia festivo, sale sin esperar.
- Solo admite paper trading; DRY_RUN=true por defecto evita enviar y cancelar órdenes.

La estrategia C (forex) no se incluye: Alpaca no ofrece forex ni futuros.
"""

import csv
import logging
import os
import smtplib
import sys
import time
import traceback
from datetime import datetime, timedelta, time as dtime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import pytz
import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import TimeFrame, TimeFrameUnit

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(os.path.join(BASE_DIR, ".env"))

# =========================
# CONFIG
# =========================
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "").strip()
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "").strip()
ALPACA_BASE_URL = os.getenv(
    "ALPACA_BASE_URL", "https://paper-api.alpaca.markets").strip()
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

GMAIL_USER = os.getenv("GMAIL_USER", "").strip()
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").strip()
TARGET_EMAIL = os.getenv("TARGET_EMAIL", "").strip()

NY_TZ = pytz.timezone("America/New_York")

SYMBOL = os.getenv("SYMBOL", "QQQ").strip().upper()
STRATEGIES = [s.strip().upper() for s in os.getenv(
    "STRATEGIES", "A,B,D").split(",") if s.strip()]
# 'iex' (gratis) o 'sip' (de pago)
DATA_FEED = os.getenv("DATA_FEED", "iex").strip()

# % del equity que se arriesga por trade
RISK_PCT = float(os.getenv("RISK_PCT", "0.01"))
# tope de posicion (% del equity)
MAX_NOTIONAL_PCT = float(os.getenv("MAX_NOTIONAL_PCT", "0.30"))
# take profit = RR x riesgo
RR = float(os.getenv("RR", "2.0"))
MIN_RISK_USD = float(os.getenv("MIN_RISK_USD", "0.10")
                     )       # stops menores se descartan
# precio actual no puede alejarse mas de 0.5R de la senal
MAX_DRIFT_R = float(os.getenv("MAX_DRIFT_R", "0.5"))
SIGNAL_MAX_AGE_SECONDS = int(os.getenv("SIGNAL_MAX_AGE_SECONDS", "180"))
# velas minimas en la caja (IEX premarket es ralo)
MIN_BOX_BARS = int(os.getenv("MIN_BOX_BARS", "12"))

ENTRY_END = dtime(int(os.getenv("ENTRY_END_HH", "11")),
                  int(os.getenv("ENTRY_END_MM", "0")))
SESSION_END_HH = int(os.getenv("SESSION_END_HH", "15"))
SESSION_END_MM = int(os.getenv("SESSION_END_MM", "55"))
TRADING_WEEKDAYS = {int(x) for x in os.getenv(
    "TRADING_WEEKDAYS", "0,1,2,3,4").split(",") if x.strip().isdigit()}
LOOP_SLEEP = int(os.getenv("LOOP_SLEEP", "10"))

logging.basicConfig(
    level=getattr(logging, os.getenv(
        "LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(os.path.join(BASE_DIR, "bot.log"), encoding="utf-8")],
)
logger = logging.getLogger("ganazor_v3")


# =========================
# AUX
# =========================
def ny_now() -> datetime:
    return datetime.now(NY_TZ)


def ensure_tz(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt)
    return dt.astimezone(NY_TZ)


def parse_dt(x: Any) -> Optional[datetime]:
    if x is None:
        return None
    if isinstance(x, datetime):
        return x
    try:
        return pd.Timestamp(x).to_pydatetime()
    except Exception:
        return None


from signals import DETECTORS


# =========================
# BOT
# =========================
class DayTradingBot:
    def __init__(self):
        if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
            logger.critical(
                "Faltan ALPACA_API_KEY / ALPACA_SECRET_KEY (variables de entorno o .env).")
            sys.exit(1)
        if ALPACA_BASE_URL.rstrip("/") != "https://paper-api.alpaca.markets":
            logger.critical(
                "Esta versión solo admite el endpoint paper de Alpaca.")
            sys.exit(1)

        self.api = tradeapi.REST(
            ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL, api_version="v2")
        try:
            self.start_equity = float(self.api.get_account().equity)
            logger.info(
                f"Conectado a Alpaca. Equity inicial: ${self.start_equity:.2f}")
        except Exception as e:
            logger.critical(f"Error conectando a Alpaca: {e}")
            sys.exit(1)

        self.position_open = False
        self.done = set()           # estrategias cuya unica senal del dia ya se evaluo
        self.notes = []             # lineas para el informe
        self.session_end = ny_now().replace(
            hour=SESSION_END_HH, minute=SESSION_END_MM, second=0, microsecond=0)

    # ---------- email ----------
    def _send_mail(self, subject: str, body: str) -> None:
        if not (GMAIL_USER and GMAIL_APP_PASSWORD and TARGET_EMAIL):
            logger.info(
                "Email deshabilitado (faltan credenciales). Contenido:\n" + body)
            return
        try:
            msg = MIMEMultipart()
            msg["From"], msg["To"], msg["Subject"] = GMAIL_USER, TARGET_EMAIL, subject
            msg.attach(MIMEText(body, "plain"))
            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.starttls()
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, TARGET_EMAIL, msg.as_string())
            server.quit()
            logger.info("Email enviado.")
        except Exception as e:
            logger.error(f"No se pudo enviar el email: {e}")

    def send_error_email(self, error: Exception, context: str = "") -> None:
        body = f"ERROR EN BOT\n\nContexto: {context}\nTipo: {type(error).__name__}\nMensaje: {error}\n\n{traceback.format_exc()}"
        self._send_mail(f"[ERROR] Ganazor Bot v3 - {ny_now().date()}", body)

    def send_summary(self) -> None:
        try:
            final_equity = float(self.api.get_account().equity)
            day_start = ny_now().replace(hour=0, minute=0, second=0, microsecond=0)
            lines = []
            for o in self.api.list_orders(status="all", after=day_start.isoformat(), direction="asc", limit=100):
                if o.symbol == SYMBOL:
                    lines.append(f"  {o.side} {o.order_type} qty={o.qty} llenado={o.filled_qty} "
                                 f"precio_medio={o.filled_avg_price} estado={o.status}")
            body = (
                f"RESUMEN DE SESION - {ny_now().date()}  (activo {SYMBOL}, estrategias {','.join(STRATEGIES)})\n"
                f"{'MODO DRY_RUN (sin ordenes)' if DRY_RUN else ''}\n\n"
                + "\n".join(self.notes or ["Sin operaciones hoy."])
                + "\n\nOrdenes del dia:\n" +
                ("\n".join(lines) if lines else "  (ninguna)")
                + f"\n\nEquity inicial: ${self.start_equity:.2f}\nEquity final:   ${final_equity:.2f}\n"
                f"P&L del dia:    ${final_equity - self.start_equity:.2f}\n"
            )
            self._send_mail(
                f"Reporte Ganazor Alpaca Bot v3 - {ny_now().date()}", body)
        except Exception as e:
            logger.error(f"Error generando resumen: {e}")

    # ---------- tiempo ----------
    def wait_for_open(self) -> bool:
        while True:
            now = ny_now()
            if now >= self.session_end:
                self.notes.append(
                    "Arranque despues de SESSION_END. No se opera.")
                return False
            try:
                clock = self.api.get_clock()
            except Exception as e:
                logger.error(f"Fallo get_clock(): {e}. Reintento en 30 s.")
                time.sleep(30)
                continue
            if clock.is_open:
                next_close = ensure_tz(
                    parse_dt(getattr(clock, "next_close", None)))
                if next_close:  # dias de cierre anticipado
                    self.session_end = min(
                        self.session_end, next_close - timedelta(minutes=5))
                return True
            next_open = ensure_tz(parse_dt(getattr(clock, "next_open", None)))
            if next_open is None or next_open.date() != now.date():
                self.notes.append(
                    "Mercado cerrado hoy (festivo/fin de semana).")
                return False
            wait_s = max(1, (next_open - now).total_seconds())
            logger.info(
                f"Mercado cerrado. Apertura NY {next_open.time()} (~{int(wait_s // 60)} min).")
            time.sleep(min(60, wait_s))

    # ---------- datos ----------
    def fetch_bars(self, minutes: int) -> Optional[pd.DataFrame]:
        now = ny_now()
        start = now.replace(hour=4, minute=0, second=0, microsecond=0)
        try:
            df = self.api.get_bars(SYMBOL, TimeFrame(minutes, TimeFrameUnit.Minute),
                                   start=start.isoformat(), end=now.isoformat(),
                                   adjustment="raw", feed=DATA_FEED).df
        except Exception as e:
            logger.error(f"Error get_bars({minutes}m): {e}")
            return None
        if df is None or df.empty:
            return None
        df.index = (df.index.tz_localize("UTC")
                    if df.index.tz is None else df.index).tz_convert(NY_TZ)
        df = df.sort_index()
        # solo velas cerradas
        return df[df.index + pd.Timedelta(minutes=minutes) <= pd.Timestamp(now)]

    # ---------- posicion ----------
    def has_position(self) -> bool:
        try:
            self.api.get_position(SYMBOL)
            return True
        except Exception as e:
            if getattr(e, "status_code", None) == 404 or "does not exist" in str(e).lower():
                return False
            logger.warning(
                f"get_position error ({e}); se asume posicion abierta.")
            return True

    def flatten(self, reason: str) -> None:
        if DRY_RUN:
            logger.info("[DRY_RUN] Cierre omitido: %s", reason)
            return
        logger.info(f"Cerrando posicion {SYMBOL}: {reason}")
        day_start = ny_now().replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            for o in self.api.list_orders(status="all", after=day_start.isoformat(), limit=100):
                if o.symbol == SYMBOL and o.status in ("new", "accepted", "held", "pending_new", "partially_filled"):
                    try:
                        self.api.cancel_order(o.id)
                    except Exception:
                        pass
            time.sleep(1.5)
            self.api.close_position(SYMBOL)
            self.notes.append(f"Cierre forzado: {reason}")
        except Exception as e:
            logger.error(f"Error cerrando posicion: {e}")
            try:
                # Nunca cancelar órdenes de otros símbolos.
                logger.warning("Reintentando solo el cierre del símbolo configurado.")
                time.sleep(1.5)
                self.api.close_position(SYMBOL)
            except Exception as e2:
                logger.error(f"Segundo intento de cierre fallo: {e2}")

    def _journal(self, row: Dict[str, Any]) -> None:
        path = os.path.join(BASE_DIR, "alpaca_journal.csv")
        new = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(row.keys()))
            if new:
                w.writeheader()
            w.writerow(row)

    # ---------- entradas ----------
    def open_trade(self, sig: Dict[str, Any]) -> bool:
        side, entry, sl = sig["side"], sig["entry"], sig["sl"]
        risk = abs(entry - sl)
        if risk < MIN_RISK_USD:
            self.notes.append(
                f"[{sig['strategy']}] senal descartada: stop demasiado pequeno (${risk:.2f}).")
            return False
        tp = entry + side * RR * risk

        try:
            px = float(self.api.get_latest_trade(
                SYMBOL).price)  # mejor esfuerzo
            if abs(px - entry) > MAX_DRIFT_R * risk:
                self.notes.append(
                    f"[{sig['strategy']}] senal descartada: el precio ({px:.2f}) ya se alejo de {entry:.2f}.")
                return False
        except Exception:
            px = entry

        acct = self.api.get_account()
        equity, buying_power = float(acct.equity), float(acct.buying_power)
        qty = min(int(equity * RISK_PCT / risk),
                  int(min(equity * MAX_NOTIONAL_PCT, buying_power) / entry))
        if qty < 1:
            self.notes.append(
                f"[{sig['strategy']}] senal descartada: qty<1 (riesgo {risk:.2f}, equity {equity:.0f}).")
            return False
        if side == -1 and not bool(getattr(self.api.get_asset(SYMBOL), "shortable", False)):
            self.notes.append(
                f"[{sig['strategy']}] senal descartada: {SYMBOL} no es shortable.")
            return False

        txt = (f"[{sig['strategy']}] {'LARGO' if side == 1 else 'CORTO'} {SYMBOL} qty={qty} "
               f"entrada~{entry:.2f} SL={sl:.2f} TP={tp:.2f} (riesgo/accion ${risk:.2f}, vela {sig['bar_time']})")
        self._journal(dict(date=str(ny_now().date()), strategy=sig["strategy"], side=side, qty=qty,
                           entry=round(entry, 2), sl=round(sl, 2), tp=round(tp, 2), dry_run=DRY_RUN))
        if DRY_RUN:
            self.notes.append("[DRY_RUN] " + txt)
            logger.info("[DRY_RUN] " + txt)
            return False

        logger.info("SENAL -> " + txt)
        try:
            order = self.api.submit_order(
                symbol=SYMBOL, qty=qty, side="buy" if side == 1 else "sell", type="market",
                time_in_force="day", order_class="bracket",
                take_profit={"limit_price": f"{tp:.2f}"}, stop_loss={"stop_price": f"{sl:.2f}"},
            )
        except Exception as e:
            logger.error(f"Error enviando orden bracket: {e}")
            self.notes.append(f"{txt}\n  ERROR al enviar la orden: {e}")
            return False

        for _ in range(20):
            o = self.api.get_order(order.id)
            if o.status == "filled":
                self.position_open = True
                self.notes.append(
                    f"{txt}\n  Ejecutada a {float(o.filled_avg_price):.2f}")
                return True
            time.sleep(1)
        try:
            self.api.cancel_order(order.id)
        except Exception:
            pass
        self.notes.append(
            f"{txt}\n  La orden no se lleno en 20 s y se cancelo.")
        return False

    def check_entries(self) -> bool:
        cache: Dict[int, Optional[pd.DataFrame]] = {}
        for key in STRATEGIES:
            if key in self.done or key not in DETECTORS:
                continue
            fn, mins = DETECTORS[key]
            if mins not in cache:
                cache[mins] = self.fetch_bars(mins)
            day = cache[mins]
            if day is None or day.empty:
                continue
            sig = fn(day)
            if sig is None:
                continue
            self.done.add(key)  # una unica senal por dia y estrategia
            sig["strategy"] = key
            age = (ny_now() - (sig["bar_time"] +
                   timedelta(minutes=mins))).total_seconds()
            if age > SIGNAL_MAX_AGE_SECONDS:
                self.notes.append(
                    f"[{key}] senal de {sig['bar_time'].time()} detectada tarde ({age:.0f}s). No se opera.")
                continue
            if self.open_trade(sig):
                return True
        return False

    # ---------- sesion ----------
    def run(self) -> None:
        if ny_now().weekday() not in TRADING_WEEKDAYS:
            self.notes.append(
                f"Hoy no esta en TRADING_WEEKDAYS={sorted(TRADING_WEEKDAYS)}.")
            return self.send_summary()
        if not self.wait_for_open():
            return self.send_summary()

        if self.has_position():
            self.position_open = True
            self.notes.append(
                f"Ya habia una posicion abierta en {SYMBOL} al arrancar: no se abren trades nuevos.")

        logger.info(
            f"Sesion hasta {self.session_end.time()} NY. Estrategias: {STRATEGIES}")
        while ny_now() < self.session_end:
            try:
                if self.position_open:
                    if not self.has_position():
                        self.notes.append(
                            "Posicion cerrada por el stop o el take profit.")
                        self.position_open = False
                        break
                    time.sleep(30)
                    continue
                if ny_now().time() >= ENTRY_END or len(self.done) == len([s for s in STRATEGIES if s in DETECTORS]):
                    self.notes.append(
                        "Ventana de entrada terminada sin trade.")
                    break
                self.check_entries()
            except Exception as e:
                logger.error(f"Error en el bucle: {e}")
            time.sleep(LOOP_SLEEP)

        if self.position_open and self.has_position():
            self.flatten(
                f"TIME STOP {SESSION_END_HH:02d}:{SESSION_END_MM:02d} NY")
        self.send_summary()


if __name__ == "__main__":
    bot = DayTradingBot()
    try:
        bot.run()
        sys.exit(0)
    except Exception as exc:
        logger.exception("Error no controlado.")
        bot.send_error_email(exc, "main/run")
        sys.exit(1)
