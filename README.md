# Radar Bolsa

App web instalable (iPhone y Android) que vigila la bolsa de EE.UU.:

- **Salidas a bolsa**: próximas, registradas en la SEC y recientes, con la fase en la que está cada una frente a lo medido históricamente.
- **Pequeñas con potencial**: empresas pequeñas y medianas puntuadas por crecimiento, márgenes, solidez, tema e interés.
- **Castigadas**: empresas que han caído un 30 % o más, con señales de si el negocio sigue sano.
- **Temas** que importan al mundo (IA, energía, salud, defensa, infraestructura, recursos) con su interés público.

Publicada en **https://hugoibel.github.io/radar-bolsa/**.

## Cómo funciona

```
VPS (cron) ── colector/colector.py ──► data/*.json ──git push──► GitHub Pages ──► la app
```

- `colector/colector.py completo` — una vez al día tras el cierre: ~1.800 empresas (S&P 500/400/600 + salidas a bolsa de 18 meses), precios y finanzas de Yahoo, interés en Wikipedia, puntuación.
- `colector/colector.py rapido` — cada 4 horas: calendario de Nasdaq y titulares de Google News.
- `colector/actualizar.sh` — lo lanza el cron, ejecuta el colector con prioridad mínima (comparte máquina con los bots de trading) y publica `data/`.

Fuentes públicas, sin claves. La SEC exige un email real en cada consulta, por eso las finanzas salen de Yahoo.

## Honestidad

Lo medido con datos reales (2026-10-04) está dentro de la app: comprar en la salida a bolsa perdió −59 % de mediana a un año (419 casos, 2023-2025), y comprar lo más caído del S&P no ganó al índice en mediana. La puntuación de potencial es una regla transparente **sin probar contra el índice**. No es consejo de inversión.
