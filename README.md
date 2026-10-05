# Radar Bolsa

App web instalable (iPhone y Android) para **empezar a invertir en la bolsa de EE.UU. con datos reales y sin humo**:

- **Mi dinero**: el plan en 5 pasos (colchón, deudas, cuenta, fondo índice, automatizar), una calculadora con **100 años de historia real** del S&P 500 (1926-hoy, con dividendos y descontada la inflación: peor, típico y mejor caso según el año en que empezaste), todas las caídas de más del 20 % y cuánto tardaron en recuperarse, los fondos índice baratos con su comisión real y **tu cartera comparada con el índice** (¿le estás ganando al S&P 500?).
- **Ideas**: pequeñas con potencial (puntuación 0-100), castigadas (≥30 % bajo su máximo, con señales de salud) y directivos comprando con su dinero (SEC). Cada lista dice lo que dio **medida contra el índice** (backtest 2011-2025).
- **Salidas a bolsa**: próximas, registradas en la SEC y recientes, con su fase frente a lo medido históricamente y los contrasplits.
- **Ficha de empresa**: rentabilidades con dividendos, gráfica frente al SPY, cuentas, valoración frente a su sector (PER, precio/ventas, EV/EBITDA), dividendo, beta, acciones en corto y noticias.

Publicada en **https://hugoibel.github.io/radar-bolsa/**.

## Cómo funciona

```
VPS (cron) ── colector/colector.py ──► data/*.json ──git push──► GitHub Pages ──► la app
PC (a mano) ── colector/historia.py ──► data/historia.json   (1 vez al mes como mucho)
PC (a mano) ── backtest/*.py ──► resultados que se copian a MEDIDO en colector.py
```

- `colector/colector.py completo` — una vez al día tras el cierre: ~1.800 empresas (S&P 500/400/600 + salidas a bolsa de 18 meses), precios con dividendos, finanzas y valoración de Yahoo, interés en Wikipedia, puntuación, directivos (SEC), 23 fondos índice (`fondos.json`) y el S&P 500 día a día desde 1993 (`indice.json`, para comparar la cartera).
- `colector/colector.py rapido` — cada 4 horas: calendario de Nasdaq y titulares de Google News.
- `colector/actualizar.sh` — lo lanza el cron, ejecuta el colector con prioridad mínima (comparte máquina con los bots de trading) y publica `data/`.
- `colector/historia.py` — serie mensual del S&P 500 de Robert Shiller (shillerdata.com) desde 1926 → `data/historia.json`. Se ejecuta en el PC (`pip install xlrd requests`).
- `backtest/` — medición de las listas de Ideas con datos de la SEC (estados financieros con su fecha de presentación, sin mirar al futuro) y precios de Yahoo. Ver `backtest/medir.py` para los criterios, fijados antes de ver los resultados.

Fuentes públicas, sin claves. La SEC exige un email real de contacto: vive en `/root/radar_bolsa/sec_contacto.txt` en el VPS (y en una carpeta de datos del PC), fuera de este repo público.

## Gotchas

- Yahoo con `range=max` devuelve velas **mensuales** aunque se pidan diarias: para el histórico diario hay que pasar `period1=0&period2=9999999999`.
- La copia de Shiller en econ.yale.edu se quedó en sept-2023; la buena está en shillerdata.com.
- SPLG ya no existe (ahora SPYM, desde el 31-oct-2025). Yahoo no da su comisión: respaldo verificado en `TER_VERIFICADA`.
- Las salidas a bolsa con contrasplits: el precio de salida se enseña tal cual ($4) y aparte cuántos contrasplits ha hecho; la rentabilidad sí usa el precio ajustado.
- Al tocar la app: subir `VERSION` en `sw.js`.

## Honestidad

Lo medido con datos reales está dentro de la app: comprar en la salida a bolsa perdió −59 % de mediana a un año (419 casos, 2023-2025); las listas de Ideas llevan su resultado medido contra el índice. No es consejo de inversión.
