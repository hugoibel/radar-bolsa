/* Radar Bolsa — lógica de la app.
   Lee los JSON que el colector del VPS publica en data/ y los pinta.
   No hay servidor propio: todo es estático (GitHub Pages). */
'use strict';

const S = { res: null, emp: [], ipos: null, not: null, hist: null, porT: {}, vista: 'inicio',
            filtroTema: 'todos', filtroCaida: 'sanas', filtroIpo: 'proximas', q: '' };

// Precios en vivo (2026-10-04): Cloudflare Worker que hace de puente con Yahoo, porque el
// navegador no puede leer Yahoo directamente (CORS). Si falla, la app sigue con los datos
// del día. Solo se piden los tickers que hay en pantalla.
const PRECIOS_API = new URLSearchParams(location.search).get('api')   // solo para probar en local
  || 'https://radar-bolsa-precios.citemeai.workers.dev/precios';
const VIVO = { ts: 0, abierto: false, timer: null, fallos: 0 };
// Canal en vivo (2026-10-04): el mismo WebSocket que usa la web de Yahoo Finance. Empuja
// cada cambio de precio al momento (~1 s). No es oficial: si falla o lo cambian, la app
// vuelve sola a preguntar al Worker cada 30 s.
const STREAM_URL = 'wss://streamer.finance.yahoo.com/?version=2';
const STREAM = { ws: null, subs: new Set(), ultimo: 0, intentos: 0, sesion: null };

// ── utilidades ───────────────────────────────────────────────────────────────
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const nf = (x, d = 1) => x.toLocaleString('es-ES', { minimumFractionDigits: d, maximumFractionDigits: d });

function pct(x, d = 1, signo = true) {
  if (x == null || isNaN(x)) return '—';
  const v = x * 100;
  return (signo && v > 0 ? '+' : '') + nf(v, Math.abs(v) >= 100 ? 0 : d) + ' %';
}
function pctN(v, d = 1) {            // ya viene en %
  if (v == null || isNaN(v)) return '—';
  return (v > 0 ? '+' : '') + nf(v, d) + ' %';
}
const cls = x => x == null ? 'muted' : x > 0 ? 'up' : x < 0 ? 'down' : 'muted';
function usd(x) {
  if (x == null || isNaN(x)) return '—';
  const a = Math.abs(x), s = x < 0 ? '−' : '';
  if (a >= 1e12) return s + '$' + nf(a / 1e12, 1) + ' billones';
  if (a >= 1e9) return s + '$' + nf(a / 1e9, a >= 1e10 ? 0 : 1) + ' mil M';
  if (a >= 1e6) return s + '$' + nf(a / 1e6, a >= 1e7 ? 0 : 1) + ' M';
  if (a >= 1e3) return s + '$' + nf(a / 1e3, 0) + ' mil';
  return s + '$' + nf(a, 2);
}
function precio(x) { return x == null ? '—' : '$' + nf(x, x < 1 ? 4 : 2); }
function hace(iso) {
  if (!iso) return '';
  const m = Math.round((Date.now() - new Date(iso.replace('Z', ':00Z')).getTime()) / 60000);
  if (m < 1) return 'ahora mismo';
  if (m < 60) return `hace ${m} min`;
  if (m < 48 * 60) return `hace ${Math.round(m / 60)} h`;
  return `hace ${Math.round(m / 1440)} días`;
}
function fechaCorta(iso) {
  if (!iso) return '';
  const d = new Date(iso.length === 10 ? iso + 'T12:00:00' : iso);
  return d.toLocaleDateString('es-ES', { day: 'numeric', month: 'short', year: 'numeric' });
}
function fechaUS(mdy) {              // "10/07/2026" de Nasdaq
  if (!mdy) return null;
  const [m, d, y] = mdy.split('/').map(Number);
  return new Date(y, m - 1, d, 12);
}
const diasHasta = iso => Math.round((new Date(iso + 'T12:00:00') - new Date()) / 864e5);

const LS = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* sin almacenamiento */ } },
};
let favs = new Set(LS.get('rb_favs', []));

const INDICE = { '500': 'S&P 500 · grande', '400': 'S&P 400 · mediana', '600': 'S&P 600 · pequeña', 'IPO': 'Salida a bolsa reciente' };

// ── carga de datos ───────────────────────────────────────────────────────────
async function cargar(nombre) {
  const r = await fetch(`data/${nombre}.json`, { cache: 'no-cache' });
  if (!r.ok) throw new Error(nombre + ' ' + r.status);
  return r.json();
}

async function iniciar() {
  try {
    const [res, emp, ipos, not] = await Promise.all([cargar('resumen'), cargar('empresas'), cargar('ipos'), cargar('noticias').catch(() => null)]);
    S.res = res; S.emp = emp.e; S.ipos = ipos; S.not = not;
    S.emp.forEach(e => { S.porT[e.t] = e; });
  } catch (err) {
    $('#cargando').innerHTML = `<div class="vacio">No se pudieron cargar los datos.<br><small>${esc(err.message)}</small><br><br><button class="btn" onclick="location.reload()">Reintentar</button></div>`;
    return;
  }
  $('#cargando').remove();
  const act = S.res.act;
  const viejo = (Date.now() - new Date(act.replace('Z', ':00Z'))) / 36e5 > 60;
  $('#actualizado').innerHTML = `${viejo ? '⚠️ ' : ''}Datos ${hace(act)} · ${S.res.n_total.toLocaleString('es-ES')} empresas`;
  pintarTodo();
  const v = (location.hash || '').slice(1);
  ir(['inicio', 'ipos', 'potencial', 'caidas', 'buscar', 'guia'].includes(v) ? v : 'inicio', false);
  actualizarEnVivo();
  conectarStream();
}

function pintarTodo() {
  pintarInicio(); pintarIpos(); pintarPotencial(); pintarCaidas(); pintarBuscar(); pintarGuia();
}

function ir(v, hist = true) {
  S.vista = v;
  document.querySelectorAll('.vista').forEach(x => x.classList.toggle('on', x.id === 'v-' + v));
  document.querySelectorAll('.nav button').forEach(b => b.classList.toggle('on', b.dataset.v === v));
  if (hist) history.replaceState(null, '', '#' + v);
  window.scrollTo({ top: 0 });
  if (VIVO.ts) actualizarEnVivo();          // precios en vivo de lo que ahora hay en pantalla
  suscribirStream();
}

// ── piezas comunes ───────────────────────────────────────────────────────────
function anillo(sc) {
  const r = 19, c = 2 * Math.PI * r, f = Math.max(0, Math.min(100, sc)) / 100;
  const col = sc >= 70 ? 'var(--up)' : sc >= 50 ? 'var(--acc)' : sc >= 35 ? 'var(--warn)' : 'var(--down)';
  return `<div class="anillo"><svg viewBox="0 0 46 46"><circle cx="23" cy="23" r="${r}" fill="none" stroke="var(--chip)" stroke-width="5"/>
    <circle cx="23" cy="23" r="${r}" fill="none" stroke="${col}" stroke-width="5" stroke-linecap="round" stroke-dasharray="${c * f} ${c}"/></svg><b>${sc}</b></div>`;
}
const temaTxt = k => { const t = S.res.temas[k]; return t ? `${t.ico} ${t.nombre}` : 'Otros sectores'; };
const CORTO = { ia: 'IA y chips', energia: 'Energía', salud: 'Salud', defensa: 'Defensa', infra: 'Infraestructura', recursos: 'Recursos' };
const temaCorto = k => { const t = S.res.temas[k]; return t ? `${t.ico} ${CORTO[k] || t.nombre}` : ''; };

function filaEmp(e, der) {
  const izq = e.sc != null ? anillo(e.sc) : '';
  return `<div class="fila" data-t="${esc(e.t)}">${izq}
    <div class="info"><div class="nom"><span class="tk">${esc(e.t)}</span>${esc(e.n)}</div>
    <div class="det">${der != null ? `<span data-vp="${esc(e.t)}">${precio(e.px)}</span> · ` : ''}${esc(e.tema !== 'otros' ? temaCorto(e.tema) : e.sector)} · ${usd(e.mc)}</div></div>
    <div class="der">${der ?? `<b data-vp="${esc(e.t)}">${precio(e.px)}</b><span class="${cls(e.r1d)}" data-vc="${esc(e.t)}">${pct(e.r1d, 2)} hoy</span>`}</div></div>`;
}

// Compras de directivos (SEC, formulario 4, últimos 90 días)
const compraDir = e => (e.ins && e.ins.c > 0) ? e.ins : null;
function chipDir(e) {
  const i = compraDir(e);
  if (!i) return '';
  return `<span class="tag ${i.n >= 2 ? 'ok' : ''}">🏦 ${i.n} directivo${i.n === 1 ? '' : 's'} compr${i.n === 1 ? 'ó' : 'aron'} ${usd(i.c)}</span>`;
}

function noticiasHTML(lista, vacio = 'Sin noticias recientes.') {
  if (!lista || !lista.length) return `<div class="muted" style="font-size:13px">${vacio}</div>`;
  return lista.map(n => `<a class="noti" href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.ti)}
    <small>${esc(n.fu)} · ${hace(n.f)}</small></a>`).join('');
}

// ── INICIO ───────────────────────────────────────────────────────────────────
function pintarInicio() {
  const r = S.res, spy = r.spy || {}, m = r.medido.ipo;
  const temas = Object.entries(r.temas).sort((a, b) => (b[1].tend ?? -9) - (a[1].tend ?? -9));
  const maxV = Math.max(...temas.map(([, t]) => t.vistas_dia || 0), 1);
  const top = S.emp.filter(e => e.sc != null).sort((a, b) => b.sc - a.sc).slice(0, 5);
  const prox = (S.ipos.proximas || []).filter(x => !x.spac).slice(0, 3);
  const sanas = S.emp.filter(e => e.cast && (e.salud || []).length >= 3).length;

  $('#v-inicio').innerHTML = `
    <div class="hero">
      <div class="lbl">El listón a batir · índice S&P 500 (SPY)</div>
      <div class="cifra ${cls(spy.r1a)}">${pct(spy.r1a)}</div>
      <div class="lbl">en el último año</div>
      <div class="kpis">
        <div class="kpi"><b class="${cls(spy.r1d)}" data-vc="SPY">${pct(spy.r1d, 2)}</b><span>hoy</span></div>
        <div class="kpi"><b class="${cls(spy.r1m)}">${pct(spy.r1m)}</b><span>1 mes</span></div>
        <div class="kpi"><b class="${cls(spy.r6m)}">${pct(spy.r6m)}</b><span>6 meses</span></div>
        <div class="kpi"><b data-vp="SPY">${precio(spy.px)}</b><span>precio SPY</span></div>
      </div>
    </div>

    <h3>Lo que dicen los datos</h3>
    <div class="card">
      <p style="margin:0 0 8px">Medido con <b>${m.muestra} salidas a bolsa reales</b> de EE.UU. (${m.periodo}):</p>
      <div class="grid2">
        <div><b class="down" style="font-size:22px">${pctN(m.todas.mediana_1a, 0)}</b><div class="muted" style="font-size:12.5px">lo típico tras 1 año si compras el primer día</div></div>
        <div><b style="font-size:22px">${m.todas.ganan_1a} %</b><div class="muted" style="font-size:12.5px">de ellas acaban ganando dinero</div></div>
      </div>
      <p class="muted" style="margin:10px 0 0;font-size:13px">Comprar lo nuevo o lo que ha caído no gana al índice por sí solo. Esta app te da los datos para decidir; <b>no te dice qué comprar</b>. Si una idea no le gana al SPY, lo simple es comprar el SPY.</p>
    </div>

    <h3>Temas que importan al mundo</h3>
    <p class="sub">Interés del público (visitas diarias en Wikipedia y su tendencia del último mes) y cómo van sus empresas.</p>
    <div class="grid2">${temas.map(([k, t]) => `
      <div class="tema" data-tema="${k}">
        <div class="ico">${t.ico}</div><div class="tn">${esc(t.nombre)}</div>
        <div class="tl"><span>Interés</span><b class="${cls(t.tend)}">${pct(t.tend, 0)}</b></div>
        <div class="tl"><span>Empresas 1 año</span><b class="${cls(t.r1a)}">${pct(t.r1a, 0)}</b></div>
        <div class="barra"><i style="width:${Math.round(100 * (t.vistas_dia || 0) / maxV)}%"></i></div>
      </div>`).join('')}</div>

    <h3>Top potencial ahora</h3>
    ${top.map(e => filaEmp(e)).join('')}
    <button class="btn" style="margin-top:10px;width:100%" data-ir="potencial">Ver las ${r.n_puntuadas} puntuadas →</button>

    <h3>Próximas salidas a bolsa</h3>
    ${prox.length ? prox.map(ipoProxHTML).join('') : '<div class="card muted">No hay salidas a bolsa anunciadas para las próximas semanas (sin contar SPACs).</div>'}

    ${(() => {
      const L = S.emp.filter(e => compraDir(e) && e.ins.n >= 2).sort((a, b) => b.ins.c - a.ins.c).slice(0, 5);
      return L.length ? `<h3>Directivos comprando</h3>
        <p class="sub">Varios directivos de la misma empresa han comprado acciones con su propio dinero en los últimos 90 días (SEC).</p>
        ${L.map(e => filaEmp(e, `<b class="up">${usd(e.ins.c)}</b><span class="muted">${e.ins.n} compradores</span>`)).join('')}` : '';
    })()}

    <h3>Castigadas pero sanas</h3>
    <div class="card" data-ir="caidas" style="cursor:pointer"><b style="font-size:22px">${sanas}</b> empresas han caído más de un 30 % desde su máximo pero siguen vendiendo más, ganando dinero y con caja. <span style="color:var(--acc)">Ver →</span></div>

    <h3>Noticias del mercado</h3>
    <div class="card">${noticiasHTML([...(S.not?.general_es || []).slice(0, 3), ...(S.not?.general || []).slice(0, 4)])}</div>

    <p class="pie">Datos públicos de Nasdaq, Yahoo Finance, Wikipedia y Google News.<br>Información, no consejo de inversión. Actualizado ${hace(r.act)}.</p>`;
}

// ── SALIDAS A BOLSA ──────────────────────────────────────────────────────────
function statIPO(grande) {
  const m = S.ipos.medido[grande ? 'grandes' : 'pequenas'];
  return grande
    ? `Grande (≥ $100 M). Históricamente: <b class="${cls(m.mediana_1a)}">${pctN(m.mediana_1a)}</b> de mediana a 1 año comprando el día 1; ganan el ${m.ganan_1a} %.`
    : `Pequeña (< $100 M). Históricamente: <b class="down">${pctN(m.mediana_1a, 0)}</b> de mediana a 1 año; solo ganan el ${m.ganan_1a} %.`;
}

function ipoProxHTML(x) {
  const f = fechaUS(x.fecha);
  const grande = (x.usd || 0) >= 1e8;
  return `<div class="ipo" data-ipo="${esc(x.t || x.n)}">
    <div class="cab"><div style="min-width:0"><div class="nom" style="font-weight:650">${x.t ? `<span class="tk">${esc(x.t)}</span>` : ''}${esc(x.n)}</div>
      <div class="det muted" style="font-size:12.5px">${esc(x.bolsa || '')} · Precio previsto ${x.rango ? '$' + esc(x.rango) : '—'} · Oferta ${usd(x.usd)}</div></div>
      ${f ? `<div class="fecha"><b>${f.getDate()}</b><span>${f.toLocaleDateString('es-ES', { month: 'short' })}</span></div>` : ''}</div>
    <div class="stat">${x.spac ? '<span class="tag ojo">SPAC · empresa «cheque en blanco»</span> Todavía no tiene negocio: compra otra empresa después.' : statIPO(grande)}</div>
  </div>`;
}

function ipoRecHTML(e) {
  const m = S.ipos.medido[e.grande ? 'grandes' : 'pequenas'];
  const tope = Math.max(m.sesion_min * 1.6, e.ses || 0);
  const dl = e.lockup ? diasHasta(e.lockup) : null;
  const pasada = (e.ses || 0) >= m.sesion_min;
  return `<div class="ipo" data-t="${esc(e.t)}">
    <div class="cab"><div style="min-width:0"><div class="nom" style="font-weight:650"><span class="tk">${esc(e.t)}</span>${esc(e.n)}</div>
      <div class="det muted" style="font-size:12.5px">Salió el ${fechaCorta(e.ipo_fecha)} a ${precio(e.ipo_px_aj ?? e.ipo_px)} · ${e.grande ? 'grande' : 'pequeña'} · ${usd(e.mc)}</div></div>
      <div class="der" style="text-align:right"><b class="${cls(e.r_ipo)}">${pct(e.r_ipo)}</b><div class="muted" style="font-size:11.5px">vs precio de salida</div></div></div>
    <div class="fase"><i style="width:${Math.min(100, 100 * (e.ses || 0) / tope)}%"></i><u style="left:${100 * m.sesion_min / tope}%"></u></div>
    <div class="fase-l"><span>Sesión ${e.ses || 0}</span><span>mínimo típico: sesión ${m.sesion_min}</span></div>
    <div class="stat">${(e.ipo_usd || 0) < 25e6 ? '<span class="tag mal">Oferta diminuta (< $25 M): muy fácil de manipular</span><br>' : ''}${pasada ? '✅ Ya pasó la sesión en la que, de mediana, tocan fondo.' : `⏳ Le faltan ~${m.sesion_min - (e.ses || 0)} sesiones para la zona en la que, de mediana, tocan fondo.`}
      ${dl != null && dl > 0 && dl < 120 ? `<br>🔓 Fin del bloqueo de vendedores (lock-up) en ${dl} días: suele traer ventas.` : ''}
      ${e.dd != null ? `<br>Caída desde su máximo: <b class="${cls(e.dd)}">${pct(e.dd)}</b>` : ''}</div>
  </div>`;
}

function pintarIpos() {
  const m = S.ipos.medido;
  const rec = S.emp.filter(e => e.idx === 'IPO').sort((a, b) => b.ipo_fecha.localeCompare(a.ipo_fecha));
  const prox = (S.ipos.proximas || []);
  const reg = (S.ipos.registradas || []).slice().reverse();
  const tabs = [['proximas', `Próximas (${prox.length})`], ['recientes', `Recientes (${rec.length})`], ['registradas', `Registradas (${reg.length})`]];
  let cuerpo = '';
  if (S.filtroIpo === 'proximas') {
    cuerpo = (prox.length ? prox.map(ipoProxHTML).join('') : '<div class="vacio">No hay salidas a bolsa con fecha en las próximas semanas.</div>') +
      `<p class="muted" style="font-size:13px;margin-top:14px">Nasdaq solo pone fecha unos días antes. Las candidatas de las próximas semanas están en <a href="#" data-fipo="registradas">Registradas (${reg.length})</a>: empresas que ya han pedido permiso a la SEC.</p>`;
  } else if (S.filtroIpo === 'recientes') {
    cuerpo = rec.slice(0, 120).map(ipoRecHTML).join('');
  } else {
    cuerpo = `<p class="sub">Empresas que han pedido permiso a la SEC para salir a bolsa. Aún sin fecha ni precio.</p>` +
      (reg.length ? reg.map(x => `<div class="fila" style="cursor:default"><div class="info"><div class="nom">${x.t ? `<span class="tk">${esc(x.t)}</span>` : ''}${esc(x.n)}</div>
        <div class="det">Registrada ${esc(x.fecha || '')} · ${usd(x.usd)} ${x.spac ? '· SPAC' : ''}</div></div>
        <a class="btn" style="font-size:12.5px;padding:7px 10px" target="_blank" rel="noopener"
           href="https://www.sec.gov/edgar/search/#/q=${encodeURIComponent('"' + x.n.replace(/[,.]?\s+(Inc|Corp|Ltd|Limited|Holdings)\.?$/i, '') + '"')}&forms=S-1,F-1,S-1%2FA,F-1%2FA">Folleto SEC</a></div>`).join('') : '<div class="vacio">Sin registros recientes.</div>');
  }
  $('#v-ipos').innerHTML = `
    <h2>🚀 Salidas a bolsa</h2>
    <div class="aviso"><b>Ojo:</b> de ${m.muestra} salidas a bolsa medidas (${m.periodo}), comprar el primer día perdió <b>${pctN(m.todas.mediana_1a, 0)}</b> de mediana en un año y solo ganó el ${m.todas.ganan_1a} %. El precio suele tocar fondo hacia la sesión ${m.grandes.sesion_min} (grandes) o ${m.pequenas.sesion_min} (pequeñas). Úsalo para vigilar, no para lanzarte el primer día.</div>
    <div class="chips">${tabs.map(([k, t]) => `<button class="chip ${S.filtroIpo === k ? 'on' : ''}" data-fipo="${k}">${t}</button>`).join('')}</div>
    ${cuerpo}`;
}

// ── POTENCIAL ────────────────────────────────────────────────────────────────
function pintarPotencial() {
  const temas = [['todos', 'Todos']].concat(Object.entries(S.res.temas).map(([k, t]) => [k, `${t.ico} ${t.nombre}`]), [['otros', 'Otros']]);
  let L = S.emp.filter(e => e.sc != null);
  if (S.filtroTema !== 'todos') L = L.filter(e => e.tema === S.filtroTema);
  L.sort((a, b) => b.sc - a.sc);
  $('#v-potencial').innerHTML = `
    <h2>🌱 Pequeñas con potencial</h2>
    <p class="sub">${S.res.n_puntuadas} empresas pequeñas y medianas (valor en bolsa ≤ $10.000 M, con ventas y liquidez) puntuadas de 0 a 100: crecimiento de ventas sostenido 35 %, márgenes 30 %, solidez 15 %, tema importante 10 %, interés creciente 10 %. La cifra de la derecha es el crecimiento de ventas: el menor entre el anual y el del último trimestre, para que un cobro puntual no engañe.</p>
    <div class="aviso"><b>Regla, no bola de cristal:</b> la puntuación ordena por calidad y crecimiento, pero <b>no se ha probado</b> que gane al índice. Úsala como lista para investigar.</div>
    <div class="chips">${temas.map(([k, t]) => `<button class="chip ${S.filtroTema === k ? 'on' : ''}" data-ftema="${k}">${esc(t)}</button>`).join('')}</div>
    ${L.length ? L.slice(0, 100).map(e => filaEmp(e, `<b class="${cls(e.cr)}">${pct(e.cr, 0)}</b><span class="muted">ventas</span>`)).join('') : '<div class="vacio">Ninguna empresa de este tema pasa los filtros.</div>'}`;
}

// ── CASTIGADAS ───────────────────────────────────────────────────────────────
function pintarCaidas() {
  const m = S.res.medido.caidas;
  let L = S.emp.filter(e => e.cast);
  const n = { sanas: L.filter(e => (e.salud || []).length >= 3).length, todas: L.length,
              cayendo: L.filter(e => e.r1m != null && e.r1m <= -0.10).length,
              directivos: L.filter(compraDir).length };
  if (S.filtroCaida === 'sanas') L = L.filter(e => (e.salud || []).length >= 3);
  if (S.filtroCaida === 'cayendo') L = L.filter(e => e.r1m != null && e.r1m <= -0.10);
  if (S.filtroCaida === 'directivos') L = L.filter(compraDir).sort((a, b) => b.ins.c - a.ins.c);
  if (S.filtroCaida !== 'directivos') L.sort((a, b) => ((b.salud || []).length - (a.salud || []).length) || (a.dd - b.dd));
  $('#v-caidas').innerHTML = `
    <h2>📉 Castigadas</h2>
    <p class="sub">Empresas del S&P 500, 400 y 600 que están un 30 % o más por debajo de su máximo del último año.</p>
    <div class="aviso"><b>Medido (${m.periodo}):</b> comprar las más caídas dio <b>${pctN(m.mediana)}</b> de mediana a 12 meses frente a <b>${pctN(m.mediana_indice)}</b> de la media del índice: <b>no le gana</b>. Una caída fuerte a veces es una ganga y a veces es un negocio que se hunde. Las señales de salud ayudan a distinguirlo.</div>
    <div class="chips">
      <button class="chip ${S.filtroCaida === 'sanas' ? 'on' : ''}" data-fcaida="sanas">Sanas · 3-4 señales (${n.sanas})</button>
      <button class="chip ${S.filtroCaida === 'todas' ? 'on' : ''}" data-fcaida="todas">Todas (${n.todas})</button>
      ${n.directivos ? `<button class="chip ${S.filtroCaida === 'directivos' ? 'on' : ''}" data-fcaida="directivos">🏦 Directivos compran (${n.directivos})</button>` : ''}
      <button class="chip ${S.filtroCaida === 'cayendo' ? 'on' : ''}" data-fcaida="cayendo">Siguen cayendo (${n.cayendo})</button>
    </div>
    ${L.length ? L.slice(0, 120).map(e => `
      <div class="fila" data-t="${esc(e.t)}"><div class="info">
        <div class="nom"><span class="tk">${esc(e.t)}</span>${esc(e.n)}</div>
        <div class="det"><span data-vp="${esc(e.t)}">${precio(e.px)}</span> · ${esc(INDICE[e.idx])} · ${esc(e.sector)}</div>
        <div style="margin-top:5px">${chipDir(e)}${(e.salud || []).map(s => `<span class="tag ok">✓ ${esc(s)}</span>`).join('')}${e.r1m != null && e.r1m <= -0.10 ? '<span class="tag mal">sigue cayendo</span>' : ''}</div></div>
        <div class="der"><b class="down">${pct(e.dd, 0)}</b><span class="muted">desde máximo</span></div></div>`).join('') : '<div class="vacio">Nada con este filtro.</div>'}`;
}

// ── BUSCAR + FAVORITOS ───────────────────────────────────────────────────────
function pintarBuscar() {
  $('#v-buscar').innerHTML = `
    <h2>🔎 Buscar</h2>
    <input class="buscar" id="q" type="search" placeholder="Nombre o ticker (ej. Nvidia, CRCL)…" autocomplete="off" value="${esc(S.q)}">
    <div id="resq" style="margin-top:12px"></div>
    <h3>⭐ Tus favoritas</h3>
    <div id="favs"></div>`;
  $('#q').addEventListener('input', ev => { S.q = ev.target.value; pintarResultados(); });
  pintarResultados(); pintarFavs();
}
function pintarResultados() {
  const q = S.q.trim().toLowerCase();
  if (!q) { $('#resq').innerHTML = ''; return; }
  const L = S.emp.filter(e => e.t.toLowerCase().startsWith(q) || e.n.toLowerCase().includes(q))
    .sort((a, b) => (b.t.toLowerCase() === q) - (a.t.toLowerCase() === q) || (b.mc || 0) - (a.mc || 0)).slice(0, 40);
  $('#resq').innerHTML = L.length ? L.map(e => filaEmp(e)).join('') : '<div class="vacio">Sin resultados. La app cubre el S&P 500, 400, 600 y las salidas a bolsa de los últimos 18 meses.</div>';
}
function pintarFavs() {
  const L = [...favs].map(t => S.porT[t]).filter(Boolean);
  $('#favs').innerHTML = L.length ? L.map(e => filaEmp(e)).join('') : '<div class="muted" style="font-size:14px">Pulsa la ☆ en la ficha de una empresa para guardarla aquí.</div>';
}

// ── GUÍA ─────────────────────────────────────────────────────────────────────
function pintarGuia() {
  $('#v-guia').innerHTML = `
    <h2>📖 Cómo leer la app</h2>
    <p class="sub">Explicado sin jerga.</p>
    <div class="card"><dl class="glos">
      <dt>SPY (el listón)</dt><dd>Un fondo que compra las 500 empresas grandes de EE.UU. a la vez. Si tu idea no le gana, es más fácil comprar el SPY y olvidarte.</dd>
      <dt>Salida a bolsa (IPO)</dt><dd>El día en que una empresa empieza a vender sus acciones al público. El precio de salida casi solo lo consiguen los fondos; tú compras ya con la subida del primer día.</dd>
      <dt>Lock-up (bloqueo)</dt><dd>Los dueños y empleados no pueden vender hasta unos 180 días después de la salida. Cuando se acaba el bloqueo, muchos venden y el precio suele sufrir.</dd>
      <dt>SPAC</dt><dd>Empresa «cheque en blanco»: sale a bolsa sin negocio para comprar otra empresa más tarde. Muy arriesgadas.</dd>
      <dt>Crecimiento de ventas</dt><dd>Cuánto más vende ahora que hace un año. +40 % = vende 1,4 veces lo de antes. La app usa el menor entre el del último año y el del último trimestre: así un cobro puntual (típico de las biotecnológicas) no parece un crecimiento de verdad.</dd>
      <dt>Oferta diminuta</dt><dd>Salidas a bolsa de menos de $25 M. Con tan pocas acciones en circulación, unos pocos pueden mover el precio a su antojo: hay subidas de ×20 que luego se desploman.</dd>
      <dt>Margen bruto</dt><dd>De cada $100 que vende, cuánto le queda tras pagar lo que cuesta fabricar o dar el servicio. Más alto = negocio más fuerte.</dd>
      <dt>Margen neto (beneficio)</dt><dd>De cada $100 que vende, cuánto le queda al final, ya pagado todo. Negativo = pierde dinero.</dd>
      <dt>Compras de directivos (SEC)</dt><dd>Por ley, los directivos de una empresa tienen que avisar a la SEC (formulario 4) cuando compran o venden sus acciones. Que compren con su propio dinero, y sobre todo que lo hagan varios a la vez, es de las pocas señales con respaldo en estudios: de media, esas empresas lo han hecho algo mejor. No es una garantía. Vender dice poco: lo hacen por impuestos o para diversificar.</dd>
      <dt>Folleto (S-1)</dt><dd>El documento oficial que una empresa entrega a la SEC antes de salir a bolsa: cuenta su negocio, sus cifras y sus riesgos.</dd>
      <dt>Caja y deuda</dt><dd>El dinero que tiene en el banco frente a lo que debe. Mucha caja y poca deuda = aguanta mejor una mala racha.</dd>
      <dt>Meses de caja</dt><dd>Si pierde dinero, cuánto tiempo puede seguir así antes de quedarse sin caja. Menos de 18 meses = probablemente tendrá que pedir dinero (y eso suele bajar el precio).</dd>
      <dt>Valor en bolsa</dt><dd>Lo que costaría comprar la empresa entera hoy. Pequeña: menos de $2.000 M; mediana: hasta $10.000 M.</dd>
      <dt>Caída desde máximo</dt><dd>Cuánto ha bajado desde su precio más alto del último año.</dd>
      <dt>Interés (Wikipedia)</dt><dd>Cuánta gente consulta la empresa o el tema. Que crezca dice que se habla más de ello, no que vaya a subir.</dd>
      <dt>Puntuación 0-100</dt><dd>Una regla fija que premia vender cada vez más, con buenos márgenes, con caja y en un tema importante. No está probada contra el índice: sirve para elegir qué investigar, no para comprar a ciegas.</dd>
    </dl></div>
    <h3>De dónde salen los datos</h3>
    <div class="card" style="font-size:14px">Un programa en un servidor revisa el mercado <b>cada 4 horas</b> (calendario y noticias) y hace una pasada completa <b>cada día al cierre</b> de la bolsa: precios y finanzas de ~1.800 empresas (Yahoo Finance), calendario de salidas a bolsa (Nasdaq), compras y ventas de directivos (SEC), interés (Wikipedia) y titulares (Google News).<br><br><b>Esto no es consejo de inversión.</b> Las cifras pueden tener errores de la fuente. Antes de invertir, compruébalas en la web de la empresa.</div>
    <button class="btn" style="margin-top:14px;width:100%" data-ir="inicio">← Volver</button>`;
}

// ── FICHA DE EMPRESA ─────────────────────────────────────────────────────────
async function grafica(t) {
  if (!S.hist) {
    try { S.hist = await cargar('hist'); } catch { S.hist = {}; }
  }
  const h = S.hist[t];
  const el = document.getElementById('graf');
  if (!el) return;
  if (!h || h.w.length < 3) { el.innerHTML = '<div class="muted" style="font-size:13px">Sin histórico de precios.</div>'; return; }
  const W = 600, H = 180, P = 6;
  const w = h.w, mn = Math.min(...w), mx = Math.max(...w);
  const x = i => P + (W - 2 * P) * i / (w.length - 1);
  const y = v => H - P - (H - 2 * P) * (v - mn) / ((mx - mn) || 1);
  const pts = w.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const sube = w[w.length - 1] >= w[0];
  const col = sube ? 'var(--up)' : 'var(--down)';
  let spy = '';
  const sh = S.res.spy_hist;
  if (sh && sh.d0 === h.d0 && sh.w.length === w.length) {
    const k = w[0] / sh.w[0];
    const sw = sh.w.map(v => v * k);
    const mn2 = Math.min(mn, ...sw), mx2 = Math.max(mx, ...sw);
    const y2 = v => H - P - (H - 2 * P) * (v - mn2) / ((mx2 - mn2) || 1);
    spy = `<polyline points="${sw.map((v, i) => `${x(i).toFixed(1)},${y2(v).toFixed(1)}`).join(' ')}" fill="none" stroke="var(--txt3)" stroke-width="1.6" stroke-dasharray="4 4"/>`;
    const pts2 = w.map((v, i) => `${x(i).toFixed(1)},${y2(v).toFixed(1)}`).join(' ');
    el.innerHTML = svgGraf(W, H, pts2, col, spy) + leyendaGraf(h, true);
    return;
  }
  el.innerHTML = svgGraf(W, H, pts, col, '') + leyendaGraf(h, false);
}
function svgGraf(W, H, pts, col, extra) {
  const area = `${pts} ${W - 6},${H} 6,${H}`;
  return `<svg class="grafica" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <defs><linearGradient id="gr" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${col}" stop-opacity=".28"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>
    <polygon points="${area}" fill="url(#gr)"/>${extra}<polyline points="${pts}" fill="none" stroke="${col}" stroke-width="2.4" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>`;
}
function leyendaGraf(h, conSpy) {
  return `<div class="leyenda"><span>${fechaCorta(h.d0)} → ${fechaCorta(h.d1)}</span>${conSpy ? '<span><i style="background:var(--txt3)"></i>SPY (mismo punto de partida)</span>' : ''}</div>`;
}

function abrirFicha(t) {
  const e = S.porT[t];
  if (!e) return;
  const fav = favs.has(e.t);
  const run = e.run != null ? Math.round(e.run * 3) : null;
  const met = [
    ['Valor en bolsa', usd(e.mc), e.mc == null ? 'Sin dato' : e.mc < 2e9 ? 'Pequeña' : e.mc < 10e9 ? 'Mediana' : 'Grande'],
    ['Ventas (12 meses)', usd(e.rev), 'Lo que ha vendido en el último año'],
    ['Crecimiento anual', `<span class="${cls(e.cr_a)}">${pct(e.cr_a)}</span>`, 'Ventas del último año fiscal frente al anterior'],
    ['Último trimestre', `<span class="${cls(e.cr_q)}">${pct(e.cr_q)}</span>`, e.irreg ? '⚠️ Se dispara frente al año: ingresos irregulares' : 'Frente al mismo trimestre del año pasado'],
    ['Margen bruto', pct(e.mb, 0, false), 'Lo que le queda tras el coste directo'],
    ['Margen neto', `<span class="${cls(e.mn)}">${pct(e.mn, 1)}</span>`, e.mn < 0 ? 'Pierde dinero' : 'Lo que gana al final'],
    ['Caja / Deuda', `${usd(e.caja)} / ${usd(e.deuda)}`, 'Dinero en el banco frente a lo que debe'],
    ['Caída desde máximo', `<span class="${cls(e.dd)}">${pct(e.dd, 0)}</span>`, `Máximo 1 año: ${precio(e.hi)}`],
    ['Interés en Wikipedia', e.wv ? `<span class="${cls(e.wv.tend)}">${pct(e.wv.tend, 0)}</span>` : '—', e.wv ? `${e.wv.dia.toLocaleString('es-ES')} visitas/día` : 'Sin artículo'],
  ];
  if (run != null) met.push(['Meses de caja', `<span class="${run < 18 ? 'down' : 'up'}">${run}</span>`, run < 18 ? 'Pronto tendrá que pedir dinero' : 'Al ritmo de pérdidas actual']);
  if (e.idx === 'IPO') {
    met.unshift(['Salida a bolsa', fechaCorta(e.ipo_fecha), `a ${precio(e.ipo_px_aj ?? e.ipo_px)} · oferta ${usd(e.ipo_usd)}`],
                ['Desde la salida', `<span class="${cls(e.r_ipo)}">${pct(e.r_ipo)}</span>`, `Desde el 1er día: ${pct(e.r_dia1)}`]);
  }
  const NOM = { crec: 'Crecimiento de ventas', margen: 'Margen bruto', benef: 'Beneficio', solidez: 'Solidez (caja vs deuda)', tema: 'Tema importante', interes: 'Interés creciente' };
  const notis = S.not?.emp?.[e.t];
  const qn = encodeURIComponent(`"${e.n.replace(/,? (Inc|Corp|Corporation|Holdings|Ltd)\.?$/i, '')}" stock`);
  $('#hoja').innerHTML = `<div class="asa"></div>
    <div class="titulo"><h2><span class="tk">${esc(e.t)}</span>${esc(e.n)}</h2>
      <button class="estrella" id="fav" aria-label="Favorita">${fav ? '⭐' : '☆'}</button>
      <button class="icobtn" id="cerrar" aria-label="Cerrar">✕</button></div>
    <div class="muted" style="font-size:13px;margin-top:4px">${esc(INDICE[e.idx])} · ${esc(e.sector)}${e.tema !== 'otros' ? ' · ' + esc(temaTxt(e.tema)) : ''}</div>
    <div class="precio"><span data-vp="${esc(e.t)}">${precio(e.px)}</span> <span class="${cls(e.r1d)}" style="font-size:15px" data-vc="${esc(e.t)}">${pct(e.r1d, 2)} hoy</span></div>
    <div class="kpis" style="margin-top:2px">
      <div class="kpi"><b class="${cls(e.r1m)}">${pct(e.r1m)}</b><span>1 mes</span></div>
      <div class="kpi"><b class="${cls(e.r6m)}">${pct(e.r6m)}</b><span>6 meses</span></div>
      <div class="kpi"><b class="${cls(e.r1a)}">${pct(e.r1a)}</b><span>1 año</span></div>
      <div class="kpi"><b class="${cls((e.r1a ?? 0) - (S.res.spy?.r1a ?? 0))}">${e.r1a != null && S.res.spy ? pct(e.r1a - S.res.spy.r1a) : '—'}</b><span>vs SPY 1 año</span></div>
    </div>
    <div id="graf"><div class="muted" style="font-size:13px;padding:30px 0">Cargando gráfica…</div></div>
    ${e.sc != null ? `<h3>Puntuación de potencial: ${e.sc}/100</h3><div class="card comp">${Object.entries(e.comp).map(([k, v]) => `
      <div class="l"><span>${NOM[k]}</span><b>${v}</b></div><div class="barra"><i style="width:${v}%"></i></div>`).join('')}
      <p class="muted" style="font-size:12px;margin:10px 0 0">Cada barra compara la empresa con las otras ${S.res.n_puntuadas} puntuadas (100 = la mejor). Regla sin probar contra el índice.</p></div>` : ''}
    ${e.cast ? `<h3>Señales de salud tras la caída</h3><div>${(e.salud || []).map(s => `<span class="tag ok">✓ ${esc(s)}</span>`).join('') || '<span class="tag mal">Ninguna: cuidado, puede ser un negocio en problemas</span>'}</div>` : ''}
    ${e.ins ? `<h3>Directivos · SEC · 90 días</h3><div class="card">
      ${e.ins.c > 0 ? `<div style="font-size:15px"><b class="up">${usd(e.ins.c)}</b> comprados con su propio dinero por <b>${e.ins.n}</b> directivo${e.ins.n === 1 ? '' : 's'}${e.ins.ult ? ` (último: ${fechaCorta(e.ins.ult)})` : ''}.</div>
        <div style="margin-top:8px">${e.ins.quien.map(q => `<span class="tag">${esc(q.n)}${q.c ? ' · ' + esc(q.c) : ''}</span>`).join('')}</div>`
        : '<div style="font-size:15px">Ningún directivo ha comprado en el mercado.</div>'}
      ${e.ins.v > 0 ? `<div class="muted" style="font-size:13px;margin-top:8px">Ventas de directivos: ${usd(e.ins.v)}. Vender es normal (impuestos, diversificar) y dice poco; comprar con su dinero dice más.</div>` : ''}
      </div>` : ''}
    <h3>Las cifras</h3><div class="met">${met.map(([a, b, c]) => `<div><span class="et">${a}</span><b>${b}</b><em>${c}</em></div>`).join('')}</div>
    <h3>Noticias</h3><div class="card">${noticiasHTML(notis, 'No hay titulares guardados de esta empresa.')}</div>
    <div class="enlaces">
      <a class="btn pri" href="https://finance.yahoo.com/quote/${esc(e.t)}" target="_blank" rel="noopener">Yahoo Finance</a>
      <a class="btn" href="https://news.google.com/search?q=${qn}" target="_blank" rel="noopener">Más noticias</a>
      <a class="btn" href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${esc(e.cik || e.t)}&owner=include" target="_blank" rel="noopener">Informes SEC</a>
    </div>
    <p class="pie">Información, no consejo de inversión.</p>`;
  $('#velo').classList.add('on'); $('#hoja').classList.add('on'); $('#hoja').scrollTop = 0;
  if (VIVO.ts) actualizarEnVivo();
  suscribirStream();
  $('#cerrar').onclick = cerrarFicha;
  $('#fav').onclick = () => {
    favs.has(e.t) ? favs.delete(e.t) : favs.add(e.t);
    LS.set('rb_favs', [...favs]);
    $('#fav').textContent = favs.has(e.t) ? '⭐' : '☆';
    pintarFavs();
  };
  grafica(e.t);
}
function cerrarFicha() { $('#velo').classList.remove('on'); $('#hoja').classList.remove('on'); }

// ── PRECIOS EN VIVO ──────────────────────────────────────────────────────────
function tickersEnPantalla() {
  const vista = document.getElementById('v-' + S.vista);
  const set = new Set(['SPY']);
  document.querySelectorAll('#hoja [data-vp]').forEach(n => set.add(n.dataset.vp));        // ficha abierta
  if (vista) vista.querySelectorAll('[data-vp]').forEach(n => set.add(n.dataset.vp));
  return [...set].filter(t => /^[A-Z][A-Z.\-]{0,9}$/.test(t)).slice(0, 50);
}

function pintarVivo(t, d) {
  const e = S.porT[t];
  const subio = e && e.px != null && d.p > e.px, bajo = e && e.px != null && d.p < e.px;
  if (e) { e.px = d.p; if (d.ch != null) e.r1d = d.ch; }
  if (t === 'SPY' && S.res?.spy) { S.res.spy.px = d.p; if (d.ch != null) S.res.spy.r1d = d.ch; }
  document.querySelectorAll(`[data-vp="${t}"]`).forEach(n => {
    n.textContent = precio(d.p);
    if (subio || bajo) {
      n.classList.remove('flash-up', 'flash-down'); void n.offsetWidth;
      n.classList.add(subio ? 'flash-up' : 'flash-down');
    }
  });
  if (d.ch != null) document.querySelectorAll(`[data-vc="${t}"]`).forEach(n => {
    n.textContent = pct(d.ch, 2) + (n.closest('.kpi') ? '' : ' hoy');   // en la cabecera del SPY ya pone "hoy" debajo
    n.classList.remove('up', 'down', 'muted'); n.classList.add(cls(d.ch));
  });
}

function estadoVivo() {
  const el = document.getElementById('vivo');
  if (!el) return;
  const streaming = STREAM.ultimo && Date.now() - STREAM.ultimo < 60000;
  if (streaming) {
    const extra = STREAM.sesion === 0 ? ' · antes de la apertura' : STREAM.sesion === 2 ? ' · después del cierre' : '';
    el.style.color = 'var(--up)';
    el.innerHTML = `<span class="punto"></span> En vivo al segundo${extra}`;
    return;
  }
  if (!VIVO.ts) { el.textContent = ''; return; }
  const s = Math.round((Date.now() - VIVO.ts) / 1000);
  el.style.color = VIVO.abierto ? 'var(--up)' : 'var(--txt3)';
  el.innerHTML = VIVO.abierto
    ? `<span class="punto"></span> En vivo · ${s < 60 ? `hace ${s} s` : hace(new Date(VIVO.ts).toISOString().slice(0, 16) + 'Z')}`
    : '· Bolsa cerrada: último precio';
}

// Descifrador mínimo del mensaje protobuf "PricingData" del canal de Yahoo (sin librerías).
function decodificarYahoo(b64) {
  const bin = atob(b64), u = new Uint8Array(bin.length);
  for (let k = 0; k < bin.length; k++) u[k] = bin.charCodeAt(k);
  const dv = new DataView(u.buffer); let i = 0; const o = {};
  const varint = () => { let r = 0n, s = 0n, b; do { b = u[i++]; r |= BigInt(b & 0x7f) << s; s += 7n; } while (b & 0x80 && i < u.length); return r; };
  const zigzag = v => Number((v >> 1n) ^ -(v & 1n));
  const CAMPOS = { 1: 'id', 2: 'precio', 3: 'hora', 7: 'sesion', 8: 'cambio_pct' };
  while (i < u.length) {
    const k = Number(varint()), campo = k >> 3, tipo = k & 7, nom = CAMPOS[campo];
    if (tipo === 0) { const v = varint(); if (nom) o[nom] = campo === 3 ? zigzag(v) : Number(v); }
    else if (tipo === 5) { if (nom) o[nom] = dv.getFloat32(i, true); i += 4; }
    else if (tipo === 1) i += 8;
    else if (tipo === 2) { const n = Number(varint()); if (nom) o[nom] = new TextDecoder().decode(u.subarray(i, i + n)); i += n; }
    else break;
  }
  return o;
}

function conectarStream() {
  if (document.hidden || (STREAM.ws && STREAM.ws.readyState <= 1)) return;
  let ws;
  try { ws = new WebSocket(STREAM_URL); } catch { return; }
  STREAM.ws = ws;
  ws.onopen = () => { STREAM.intentos = 0; STREAM.subs = new Set(); suscribirStream(); };
  ws.onmessage = ev => {
    try {
      const m = JSON.parse(ev.data);
      if (m.type !== 'pricing') return;
      const d = decodificarYahoo(m.message);
      if (!d.id || !d.precio) return;
      STREAM.ultimo = Date.now(); STREAM.sesion = d.sesion ?? null;
      pintarVivo(d.id, { p: Math.round(d.precio * 10000) / 10000, ch: d.cambio_pct != null ? d.cambio_pct / 100 : null });
    } catch { /* mensaje raro: se ignora */ }
  };
  ws.onclose = () => {
    if (STREAM.ws === ws) STREAM.ws = null;
    if (!document.hidden) setTimeout(conectarStream, Math.min(60000, 2000 * 2 ** STREAM.intentos++));
  };
  ws.onerror = () => { try { ws.close(); } catch { /* ya cerrado */ } };
}

function suscribirStream() {
  const ws = STREAM.ws;
  if (!ws || ws.readyState !== 1) return;
  const quiero = new Set(tickersEnPantalla());
  const alta = [...quiero].filter(t => !STREAM.subs.has(t));
  const baja = [...STREAM.subs].filter(t => !quiero.has(t));
  if (baja.length) ws.send(JSON.stringify({ unsubscribe: baja }));
  if (alta.length) ws.send(JSON.stringify({ subscribe: alta }));
  STREAM.subs = quiero;
}

async function actualizarEnVivo() {
  clearTimeout(VIVO.timer);
  if (document.hidden) { VIVO.timer = setTimeout(actualizarEnVivo, 30000); return; }
  const tks = tickersEnPantalla();
  try {
    const r = await fetch(`${PRECIOS_API}?t=${tks.join(',')}`);
    if (!r.ok) throw new Error(r.status);
    const j = await r.json();
    const ahora = Date.now() / 1000;
    let abierto = false;
    for (const [t, d] of Object.entries(j.precios || {})) {
      pintarVivo(t, d);
      if (d.ts && ahora - d.ts < 15 * 60) abierto = true;   // ha cotizado en los ultimos 15 min
    }
    VIVO.ts = Date.now(); VIVO.abierto = abierto; VIVO.fallos = 0;
  } catch (err) {
    VIVO.fallos++;                                           // sin conexion o Worker caido: datos del dia
  }
  estadoVivo();
  suscribirStream();
  // con el canal en vivo funcionando, el Worker solo hace de foto de respaldo cada 5 min
  const streaming = STREAM.ultimo && Date.now() - STREAM.ultimo < 60000;
  const espera = VIVO.fallos ? Math.min(300000, 30000 * VIVO.fallos)
    : (streaming || !VIVO.abierto ? 300000 : 30000);
  VIVO.timer = setTimeout(actualizarEnVivo, espera);
}
setInterval(estadoVivo, 5000);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {                                    // en segundo plano: cerrar (bateria y datos)
    if (STREAM.ws) { const ws = STREAM.ws; STREAM.ws = null; try { ws.close(); } catch { /* nada */ } }
  } else {
    actualizarEnVivo();
    conectarStream();
  }
});

// ── eventos (delegados) ──────────────────────────────────────────────────────
document.addEventListener('click', ev => {
  const n = ev.target.closest('.nav button');
  if (n) return ir(n.dataset.v);
  const go = ev.target.closest('[data-ir]');
  if (go) return ir(go.dataset.ir);
  const tm = ev.target.closest('[data-tema]');
  if (tm) { S.filtroTema = tm.dataset.tema; pintarPotencial(); return ir('potencial'); }
  const ft = ev.target.closest('[data-ftema]');
  if (ft) { S.filtroTema = ft.dataset.ftema; return pintarPotencial(); }
  const fc = ev.target.closest('[data-fcaida]');
  if (fc) { S.filtroCaida = fc.dataset.fcaida; return pintarCaidas(); }
  const fi = ev.target.closest('[data-fipo]');
  if (fi) { ev.preventDefault(); S.filtroIpo = fi.dataset.fipo; return pintarIpos(); }
  const f = ev.target.closest('[data-t]');
  if (f && !ev.target.closest('.hoja')) return abrirFicha(f.dataset.t);
});
$('#velo').addEventListener('click', cerrarFicha);
document.addEventListener('keydown', ev => { if (ev.key === 'Escape') cerrarFicha(); });
$('#btn-guia').addEventListener('click', () => ir('guia'));

// Aviso de instalación en iPhone (solo si no está ya instalada)
(() => {
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const instalada = window.navigator.standalone || matchMedia('(display-mode: standalone)').matches;
  if (ios && !instalada && !LS.get('rb_sin_aviso', false)) $('#instalar').classList.add('on');
  $('#cerrar-instalar').onclick = () => { $('#instalar').classList.remove('on'); LS.set('rb_sin_aviso', true); };
})();

if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
iniciar();
