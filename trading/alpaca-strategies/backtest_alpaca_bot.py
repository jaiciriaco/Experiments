import os
from datetime import datetime, timedelta

import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import TimeFrame
import pandas as pd
import numpy as np
import pytz

# ==============================
# CONFIGURACIÓN GENERAL
# ==============================

# Credenciales (mejor desde variables de entorno)
API_KEY = os.getenv("ALPACA_API_KEY", "")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

# Símbolos a escanear cada día (ejemplo; cámbialo por tu watchlist real)
CANDIDATE_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "META", "AMZN",
    "TSLA", "GOOGL", "AMD", "NFLX", "INTC"
]

# Rango de fechas del backtest (formato YYYY-MM-DD)
START_DATE = "2022-01-03"
END_DATE = "2024-03-29"

# Parámetros de la estrategia
TRADE_AMOUNT = 250.0       # Inversión fija en USD
TP_PCT = 0.025             # Take Profit +2.5%
SL_PCT = 0.012             # Stop Loss -1.2%

MIN_RSI = 50.0             # Umbral de RSI
USE_VOLUME_FILTER = True   # Volumen de la vela > media 20
MIN_VOLUME = 40000         # Volumen diario mínimo
MIN_PRICE = 5.0
MAX_PRICE = 200.0
MIN_GAP_PCT = 2.0          # Gap mínimo (en %)

# Horario de sesión (hora New York)
SESSION_START = (9, 30)
SESSION_END = (10, 30)

# Guardar resultados a CSV (o None para no guardar)
RESULTS_CSV_PATH = "backtest_results.csv"

# ==============================
# FUNCIONES AUXILIARES
# ==============================

ny_tz = pytz.timezone("America/New_York")


def parse_date(d: str) -> datetime:
    return datetime.strptime(d, "%Y-%m-%d")


def ny_session_start(date: datetime) -> datetime:
    return ny_tz.localize(datetime(date.year, date.month, date.day,
                                   SESSION_START[0], SESSION_START[1]))


def ny_session_end(date: datetime) -> datetime:
    return ny_tz.localize(datetime(date.year, date.month, date.day,
                                   SESSION_END[0], SESSION_END[1]))


# ==============================
# BACKTESTER
# ==============================

class JaimeGapVWAPBacktester:
    def __init__(self,
                 api_key: str,
                 secret_key: str,
                 base_url: str,
                 symbols,
                 start_date: str,
                 end_date: str):

        self.api = tradeapi.REST(
            api_key, secret_key, base_url, api_version="v2")
        self.symbols = symbols
        self.start_date = parse_date(start_date)
        self.end_date = parse_date(end_date)

        # Datos diarios precargados: {symbol: DataFrame}
        self.daily_data = {}
        # Resultados de trades
        self.trades = []

    # ---------- Carga de datos ----------

    def load_daily_data(self):
        """
        Carga datos diarios de todos los símbolos para todo el rango del backtest
        (con unos días de margen por seguridad).
        """
        print("Cargando datos diarios...")

        start = self.start_date - timedelta(days=10)
        end = self.end_date + timedelta(days=2)

        for symbol in self.symbols:
            try:
                # Alpaca para TimeFrame.Day quiere fechas tipo "YYYY-MM-DD"
                start_str = start.strftime("%Y-%m-%d")
                end_str = end.strftime("%Y-%m-%d")

                bars = self.api.get_bars(
                    symbol,
                    TimeFrame.Day,
                    start=start_str,
                    end=end_str
                ).df

                if bars.empty:
                    print(f"[{symbol}] sin datos diarios, se omite.")
                    continue

                # Aseguramos orden y nos quedamos solo con columnas que nos interesan
                bars = bars.sort_index()
                self.daily_data[symbol] = bars

            except Exception as e:
                print(f"Error cargando datos diarios para {symbol}: {e}")

        print(f"Datos diarios cargados para {len(self.daily_data)} símbolos.")

    # ---------- Scanner diario (gap) ----------

    def pick_symbol_for_day(self, date: datetime):
        """
        Replica tu lógica de scanner pero limitada a la lista CANDIDATE_SYMBOLS:
        - Gap > MIN_GAP_PCT
        - Volumen del día anterior >= MIN_VOLUME
        - Precio de apertura entre MIN_PRICE y MAX_PRICE
        Devuelve el símbolo con mayor gap para ese día o None.
        """
        candidates = []

        for symbol, bars in self.daily_data.items():
            # Buscamos la barra del día y la barra anterior
            # Las fechas de índice están en UTC, pero .date() funciona igual
            day_rows = bars[bars.index.date == date.date()]
            if day_rows.empty:
                continue

            day_bar = day_rows.iloc[0]

            # Barra previa (última anterior a la fecha)
            prev_bars = bars[bars.index < day_rows.index[0]]
            if prev_bars.empty:
                continue

            prev_bar = prev_bars.iloc[-1]

            prev_close = prev_bar["close"]
            open_price = day_bar["open"]
            close_price = open_price  # Precio conocido en la apertura
            day_volume = prev_bar["volume"]  # Liquidez del día anterior

            if prev_close == 0:
                continue

            gap_pct = (open_price - prev_close) / prev_close * 100.0

            # Filtros iguales a tu modelo
            if gap_pct <= MIN_GAP_PCT:
                continue
            if day_volume < MIN_VOLUME:
                continue
            if not (MIN_PRICE <= close_price <= MAX_PRICE):
                continue

            candidates.append({
                "symbol": symbol,
                "gap_pct": gap_pct,
                "volume": day_volume,
                "close_price": close_price
            })

        if not candidates:
            return None, None

        df = pd.DataFrame(candidates)
        best = df.sort_values("gap_pct", ascending=False).iloc[0]
        return best["symbol"], best

    # ---------- Simulación intradía para un día y un símbolo ----------

    def simulate_day_for_symbol(self, date: datetime, symbol: str):
        """
        Simula la sesión 9:30–10:30 para un símbolo ya elegido.
        Aplica tus reglas de entrada y salida.
        Devuelve un dict con el resultado de la operación (o None si no hubo trade).
        """
        session_start = ny_session_start(date)
        session_end = ny_session_end(date)

        try:
            bars = self.api.get_bars(
                symbol,
                TimeFrame.Minute,
                start=session_start.isoformat(),
                end=session_end.isoformat()
            ).df
        except Exception as e:
            print(
                f"Error cargando minutos para {symbol} el {date.date()}: {e}")
            return None

        if bars.empty:
            return None

        # A veces viene multi-símbolo, filtramos y ordenamos
        if "symbol" in bars.columns:
            bars = bars[bars["symbol"] == symbol]

        bars = bars.sort_index()

        # VWAP intradía, RSI(14), volumen medio 20
        bars["vwap"] = (bars["close"] * bars["volume"]
                        ).cumsum() / bars["volume"].cumsum()

        delta = bars["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(window=14, min_periods=14).mean()
        avg_loss = loss.rolling(window=14, min_periods=14).mean()
        rs = avg_gain / avg_loss
        bars["rsi"] = 100 - (100 / (1 + rs))
        bars["avg_vol"] = bars["volume"].rolling(
            window=20, min_periods=20).mean()

        position_open = False
        entry_price = None
        entry_time = None
        exit_price = None
        exit_time = None
        exit_reason = None

        for ts, row in bars.iterrows():
            price = row["close"]
            vwap = row["vwap"]
            rsi = row["rsi"]
            vol = row["volume"]
            avg_vol = row["avg_vol"]

            # Hasta que rsi y avg_vol estén definidos, no podemos tomar decisiones
            if np.isnan(vwap) or np.isnan(rsi) or (USE_VOLUME_FILTER and np.isnan(avg_vol)):
                continue

            if not position_open:
                cond_vwap = price > vwap
                cond_rsi = rsi > MIN_RSI
                cond_vol = (not USE_VOLUME_FILTER) or (vol > avg_vol)

                if cond_vwap and cond_rsi and cond_vol:
                    # Entrada
                    position_open = True
                    entry_price = price
                    entry_time = ts
            else:
                # Gestión de salida
                pct_change = (price - entry_price) / entry_price

                if pct_change >= TP_PCT:
                    exit_price = price
                    exit_time = ts
                    exit_reason = "TP"
                    break
                elif pct_change <= -SL_PCT:
                    exit_price = price
                    exit_time = ts
                    exit_reason = "SL"
                    break

        # Si llegamos al final y sigue abierta, cerramos por time-stop
        if position_open and exit_price is None:
            last_ts = bars.index[-1]
            last_price = bars["close"].iloc[-1]
            exit_price = last_price
            exit_time = last_ts
            exit_reason = "TIME_STOP"

        # Si nunca se abrió posición, no hubo trade
        if not position_open:
            return None

        pct_change = (exit_price - entry_price) / entry_price
        pnl_usd = pct_change * TRADE_AMOUNT

        trade_result = {
            "date": date.date(),
            "symbol": symbol,
            "entry_time": entry_time,
            "entry_price": entry_price,
            "exit_time": exit_time,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "pct_change": pct_change,
            "pnl_usd": pnl_usd
        }

        return trade_result

    # ---------- Bucle principal del backtest ----------

    def run(self):
        self.load_daily_data()

        current_date = self.start_date
        while current_date <= self.end_date:
            # Saltamos fines de semana
            if current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue

            symbol, scanner_info = self.pick_symbol_for_day(current_date)

            if symbol is None:
                print(
                    f"{current_date.date()} -> sin candidato que cumpla criterios.")
                current_date += timedelta(days=1)
                continue

            print(
                f"{current_date.date()} -> candidato: {symbol} (gap {scanner_info['gap_pct']:.2f}%)")

            trade = self.simulate_day_for_symbol(current_date, symbol)
            if trade is not None:
                self.trades.append(trade)
                print(
                    f"  Trade: {trade['entry_price']:.2f} -> {trade['exit_price']:.2f} "
                    f"({trade['pct_change']*100:.2f}%, {trade['exit_reason']}, "
                    f"{trade['pnl_usd']:.2f} USD)"
                )
            else:
                print("  No hubo entrada intradía.")

            current_date += timedelta(days=1)

        self.show_stats()

        if RESULTS_CSV_PATH and self.trades:
            df = pd.DataFrame(self.trades)
            df.to_csv(RESULTS_CSV_PATH, index=False)
            print(f"\nResultados guardados en: {RESULTS_CSV_PATH}")

    # ---------- Estadísticas ----------

    def show_stats(self):
        if not self.trades:
            print("\nNo hubo trades en el período.")
            return

        df = pd.DataFrame(self.trades)
        total_trades = len(df)
        wins = (df["pnl_usd"] > 0).sum()
        losses = (df["pnl_usd"] < 0).sum()
        breakeven = total_trades - wins - losses

        total_pnl = df["pnl_usd"].sum()
        avg_pnl = df["pnl_usd"].mean()
        winrate = wins / total_trades * 100

        max_drawdown = self._max_drawdown(df["pnl_usd"].cumsum())

        print("\n===== ESTADÍSTICAS BACKTEST =====")
        print(f"Nº trades:       {total_trades}")
        print(f"Ganadores:       {wins}")
        print(f"Perdedores:      {losses}")
        print(f"Break-even:      {breakeven}")
        print(f"Winrate:         {winrate:.2f}%")
        print(f"P&L total (USD): {total_pnl:.2f}")
        print(f"P&L medio (USD): {avg_pnl:.2f}")
        print(f"Max drawdown:    {max_drawdown:.2f} USD")

    @staticmethod
    def _max_drawdown(equity_curve: pd.Series) -> float:
        """
        Cálculo simple del máximo drawdown sobre una curva de equity.
        """
        equity_curve = pd.concat([pd.Series([0.0]), equity_curve], ignore_index=True)
        running_max = equity_curve.cummax()
        drawdown = equity_curve - running_max
        return drawdown.min()


# ==============================
# MAIN
# ==============================

if __name__ == "__main__":
    if "TU_API_KEY" in API_KEY or "TU_SECRET_KEY" in SECRET_KEY:
        print(
            "⚠️ Configura ALPACA_API_KEY y ALPACA_SECRET_KEY antes de ejecutar el backtest.")
    else:
        bt = JaimeGapVWAPBacktester(
            API_KEY,
            SECRET_KEY,
            BASE_URL,
            CANDIDATE_SYMBOLS,
            START_DATE,
            END_DATE
        )
        bt.run()
