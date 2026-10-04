// Radar Bolsa — precios en vivo (Cloudflare Worker).
// La app es estática (GitHub Pages) y el navegador no puede leer Yahoo directamente
// (no envía cabeceras CORS). Este Worker pide los precios a Yahoo y se los devuelve a
// la app. Solo lectura y sin claves; el navegador cachea cada respuesta 20 s.
//   GET /precios?t=AAPL,MSFT,SPY   (hasta 50 tickers)
//   GET /health
const ORIGENES = ['https://hugoibel.github.io', 'http://localhost:8091', 'http://localhost:8092'];
const MAX_TICKERS = 50;
const TANDA = 20;               // tickers por consulta a Yahoo (endpoint spark)
const CACHE_SEG = 20;
const TICKER = /^[A-Z][A-Z.\-]{0,9}$/;

function cabeceras(origen) {
  const h = { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': `public, max-age=${CACHE_SEG}` };
  if (ORIGENES.includes(origen)) {
    h['Access-Control-Allow-Origin'] = origen;
    h['Vary'] = 'Origin';
  }
  return h;
}

function responder(obj, status, origen) {
  return new Response(JSON.stringify(obj), { status, headers: cabeceras(origen) });
}

async function pedirTanda(tickers) {
  const url = 'https://query2.finance.yahoo.com/v7/finance/spark?range=1d&interval=5m&symbols=' + tickers.join(',');
  const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0 (compatible; RadarBolsa/1.0)' } });
  if (!r.ok) throw new Error('yahoo ' + r.status);
  const j = await r.json();
  const out = {};
  for (const res of (j.spark && j.spark.result) || []) {
    const m = res.response && res.response[0] && res.response[0].meta;
    if (!m || m.regularMarketPrice == null) continue;
    const pc = m.previousClose ?? m.chartPreviousClose;
    out[res.symbol] = {
      p: m.regularMarketPrice,
      pc: pc ?? null,
      ch: pc ? m.regularMarketPrice / pc - 1 : null,
      h: m.regularMarketDayHigh ?? null,
      l: m.regularMarketDayLow ?? null,
      v: m.regularMarketVolume ?? null,
      ts: m.regularMarketTime ?? null,
    };
  }
  return out;
}

export default {
  async fetch(request, env, ctx) {
    const origen = request.headers.get('Origin') || '';
    const url = new URL(request.url);
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: { ...cabeceras(origen),
        'Access-Control-Allow-Methods': 'GET, OPTIONS', 'Access-Control-Max-Age': '86400' } });
    }
    if (url.pathname === '/health') return responder({ ok: true }, 200, origen);
    if (url.pathname !== '/precios' || request.method !== 'GET') return responder({ error: 'no encontrado' }, 404, origen);
    if (origen && !ORIGENES.includes(origen)) return responder({ error: 'origen no permitido' }, 403, origen);

    const tickers = [...new Set((url.searchParams.get('t') || '').toUpperCase().split(',').map(s => s.trim()).filter(Boolean))];
    if (!tickers.length || tickers.length > MAX_TICKERS || !tickers.every(t => TICKER.test(t))) {
      return responder({ error: `entre 1 y ${MAX_TICKERS} tickers válidos` }, 400, origen);
    }
    tickers.sort();

    // Sin Cache API: en workers.dev no funciona, y una clave con dominio inventado daba el
    // error 1042 de Cloudflare. El navegador ya cachea 20 s por la cabecera Cache-Control.
    try {
      const datos = {};
      for (let i = 0; i < tickers.length; i += TANDA) {
        Object.assign(datos, await pedirTanda(tickers.slice(i, i + TANDA)));
      }
      const cuerpo = JSON.stringify({ act: new Date().toISOString(), precios: datos });
      return new Response(cuerpo, { status: 200, headers: cabeceras(origen) });
    } catch (e) {
      return responder({ error: 'fuente de precios no disponible', detalle: String(e.message || e).slice(0, 80) }, 502, origen);
    }
  },
};
