# Alpaca strategy experiments

Código experimental de investigación personal: detectores de señales, simulación histórica y prototipo de paper trading. No es un sistema validado para dinero real.

## Estructura

- `signals.py`: Heikin-Ashi y detectores A (ruptura), B (doji) y D (FVG).
- `paper_bot.py`: prototipo original saneado, restringido al endpoint paper; DRY_RUN activo por defecto.
- `backtest_alpaca_bot.py`: simulador histórico Gap/VWAP/RSI, estrategia distinta de A/B/D.
- `test_signals.py`: pruebas con datos sintéticos sin acceso a cuentas.

```bash
python -m pip install -r requirements.txt
python -m unittest discover -p 'test_*.py'
```

Para probar manualmente el bot, copiar `.env.example` a `.env`, completar credenciales de **paper** y ejecutar `python paper_bot.py`. DRY_RUN consulta la cuenta y datos pero no envía/cancela órdenes ni cierra posiciones. El correo solo se habilita si se completan sus tres campos. El backtest lee las claves desde variables de entorno del proceso, no desde `.env`; configurar fechas y parámetros en su cabecera antes de `python backtest_alpaca_bot.py`.

## Correcciones

Se retiraron credenciales incrustadas y valores personales. Se separaron los detectores. Se impide que DRY_RUN cierre posiciones y se eliminó la cancelación global de órdenes. El backtest selecciona por precio de apertura y volumen del día anterior, evitando usar cierre/volumen futuro. El drawdown incluye el capital relativo inicial cero para contar una primera pérdida.

## Limitaciones conocidas

El backtest utiliza cierres de velas como precios de entrada/salida, sin comisiones, spread ni deslizamiento. No modela ejecución intravela, universos históricos, acciones corporativas ni supervivencia. Sus cifras no prueban rentabilidad y no validan las estrategias A/B/D. Los CSV históricos previos no se publican porque proceden de otra versión.

El bot no es robusto a reinicios, concurrencia o fills parciales y no mantiene un estado persistente de una operación por día. En paper puede gestionar posiciones ya existentes del símbolo: utilizar una cuenta de simulación dedicada. El orden de evaluación es A/B/D, no una comparación global de la señal más temprana. Los detectores esperan un único día, índice ordenado en America/New_York y barras cerradas; A/B usan 5 minutos y D 1 minuto.

No se ha probado ninguna conexión de Alpaca, correo ni envío de órdenes. Se verificaron sintaxis, tres pruebas de señales y comprobaciones aisladas de selección sin datos futuros, drawdown y ausencia de llamadas de cierre en DRY_RUN. La compatibilidad con el servicio y suscripción de datos requiere una prueba posterior con cuenta paper.
