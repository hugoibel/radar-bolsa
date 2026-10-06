/* Radar Bolsa — lógica de la app.
   Lee los JSON que el colector del VPS publica en data/ y los pinta.
   No hay servidor propio: todo es estático (GitHub Pages). */
'use strict';

const S = { res: null, emp: [], ipos: null, not: null, hist: null, porT: {}, vista: 'inicio',
            filtroTema: 'todos', filtroCaida: 'sanas', filtroIpo: 'proximas', q: '', ideas: 'potencial',
            fondos: null, historia: null, indice: null, vivo: {} };
const VISTAS = ['inicio', 'dinero', 'ideas', 'ipos', 'buscar', 'guia'];

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
  return (signo && v > 0 ? '+' : '') + nf(v, Math.abs(v) >= 100 ? 0 : d) + '\u00a0%';
}
function pts(x) {                    // diferencia entre dos rentabilidades: puntos, no %
  if (x == null || isNaN(x)) return '—';
  const v = x * 100;
  return (v > 0 ? '+' : v < 0 ? '−' : '') + nf(Math.abs(v), Math.abs(v) >= 10 ? 0 : 1) + ' pts';
}
function usdExacto(x) {              // la cartera: al céntimo (hasta el millón)
  if (x == null || isNaN(x)) return '—';
  return Math.abs(x) >= 1e6 ? usd(x) : (x < 0 ? '−' : '') + '$' + nf(Math.abs(x), 2);
}
function pctN(v, d = 1) {            // ya viene en %
  if (v == null || isNaN(v)) return '—';
  return (v > 0 ? '+' : '') + nf(v, d) + '\u00a0%';
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
    const [res, emp, ipos, not, fon, med] = await Promise.all([cargar('resumen'), cargar('empresas'), cargar('ipos'),
      cargar('noticias').catch(() => null), cargar('fondos').catch(() => null), cargar('medido').catch(() => null)]);
    S.res = res; S.emp = emp.e; S.ipos = ipos; S.not = not; S.fondos = fon; S.bt = med;
    S.emp.forEach(e => { S.porT[e.t] = e; });
  } catch (err) {
    $('#cargando').innerHTML = `<div class="vacio">No se pudieron cargar los datos.<br><small>${esc(err.message)}</small><br><br><button class="btn" onclick="location.reload()">Reintentar</button></div>`;
    return;
  }
  $('#cargando').remove();
  const act = S.res.act;
  const viejo = (Date.now() - new Date(act.replace('Z', ':00Z'))) / 36e5 > 60;
  $('#actualizado').innerHTML = `${viejo ? '⚠️ ' : ''}Datos ${hace(act)} · ${S.res.n_total.toLocaleString('es-ES')} empresas`;
  const v = (location.hash || '').slice(1);
  if (v === 'potencial' || v === 'caidas' || v === 'directivos') S.ideas = v;    // enlaces antiguos
  pintarTodo();
  ir(VISTAS.includes(v) ? v : ['potencial', 'caidas', 'directivos'].includes(v) ? 'ideas' : 'inicio', false);
  actualizarEnVivo();
  conectarStream();
}

function pintarTodo() {
  pintarInicio(); pintarDinero(); pintarIdeas(); pintarIpos(); pintarBuscar(); pintarGuia();
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
      <div class="lbl">en el último año, con dividendos</div>
      <div class="kpis">
        <div class="kpi"><b class="${cls(spy.r1d)}" data-vc="SPY">${pct(spy.r1d, 2)}</b><span>hoy</span></div>
        <div class="kpi"><b class="${cls(spy.r1m)}">${pct(spy.r1m)}</b><span>1 mes</span></div>
        <div class="kpi"><b class="${cls(spy.r6m)}">${pct(spy.r6m)}</b><span>6 meses</span></div>
        <div class="kpi"><b data-vp="SPY">${precio(spy.px)}</b><span>precio SPY</span></div>
      </div>
    </div>

    <div class="card empieza" data-ir="dinero" style="margin-top:12px;cursor:pointer">
      <b>💼 ¿Empiezas a invertir?</b> El plan en 5 pasos, una calculadora con 100 años de historia real y tu cartera comparada con el índice. <span style="color:var(--acc)">Mi dinero →</span>
    </div>

    ${btResumenHTML()}

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

    <h3>Mejor puntuadas ahora</h3>
    ${S.bt?.potencial ? `<p class="sub">Para investigar, no para comprar a ciegas: medida desde 2012, la puntuación ${esc(S.bt.potencial.corto.toLowerCase())}.</p>` : ''}
    ${top.map(e => filaEmp(e)).join('')}
    <button class="btn" style="margin-top:10px;width:100%" data-ir="ideas" data-sub="potencial">Ver las ${r.n_puntuadas} puntuadas →</button>

    <h3>Próximas salidas a bolsa</h3>
    ${prox.length ? prox.map(ipoProxHTML).join('') : '<div class="card muted">No hay salidas a bolsa anunciadas para las próximas semanas (sin contar SPACs).</div>'}

    ${(() => {
      const L = S.emp.filter(e => compraDir(e) && e.ins.n >= 2).sort((a, b) => b.ins.c - a.ins.c).slice(0, 5);
      return L.length ? `<h3>Directivos comprando</h3>
        <p class="sub">Varios directivos de la misma empresa han comprado acciones con su propio dinero en los últimos 90 días (SEC).</p>
        ${L.map(e => filaEmp(e, `<b class="up">${usd(e.ins.c)}</b><span class="muted">${e.ins.n} compradores</span>`)).join('')}` : '';
    })()}

    <h3>Castigadas pero sanas</h3>
    <div class="card" data-ir="ideas" data-sub="caidas" style="cursor:pointer"><b style="font-size:22px">${sanas}</b> empresas han caído más de un 30 % desde su máximo pero siguen vendiendo más, ganando dinero y con caja. <span style="color:var(--acc)">Ver →</span></div>

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
      <div class="det muted" style="font-size:12.5px">Salió el ${fechaCorta(e.ipo_fecha)} a ${precio(e.ipo_px)} · ${e.grande ? 'grande' : 'pequeña'} · ${usd(e.mc)}</div></div>
      <div class="der" style="text-align:right"><b class="${cls(e.r_ipo)}">${pct(e.r_ipo)}</b><div class="muted" style="font-size:11.5px">vs precio de salida</div></div></div>
    <div class="fase"><i style="width:${Math.min(100, 100 * (e.ses || 0) / tope)}%"></i><u style="left:${100 * m.sesion_min / tope}%"></u></div>
    <div class="fase-l"><span>Sesión ${e.ses || 0}</span><span>mínimo típico: sesión ${m.sesion_min}</span></div>
    <div class="stat">${(e.ipo_usd || 0) < 25e6 ? '<span class="tag mal">Oferta diminuta (< $25 M): muy fácil de manipular</span>' : ''}${e.ncs ? `<span class="tag mal">🔻 ${e.ncs} contrasplit${e.ncs === 1 ? '' : 's'}: 1 acción de hoy = ${nf(e.aj, 0)} de las de la salida</span>` : ''}${(e.ipo_usd || 0) < 25e6 || e.ncs ? '<br>' : ''}${pasada ? '✅ Ya pasó la sesión en la que, de mediana, tocan fondo.' : `⏳ Le faltan ~${m.sesion_min - (e.ses || 0)} sesiones para la zona en la que, de mediana, tocan fondo.`}
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

// ── IDEAS: potencial, castigadas y directivos ────────────────────────────────
// Lo medido con datos reales (backtest 2012-2025, backtest/medir.py) viene en data/medido.json
function medidoHTML(clave) {
  const bt = S.bt?.[clave];
  if (!bt) return '';
  return `<div class="aviso ${bt.funciona ? 'aviso-ok' : ''}"><b>Medido: ${esc(bt.corto.toLowerCase())}.</b> ${esc(bt.texto)}</div>`;
}

function btResumenHTML() {
  const bt = S.bt;
  if (!bt) return '';
  const L = [['potencial', '🌱 Potencial'], ['castigadas', '📉 Castigadas sanas'], ['directivos', '🏦 Directivos comprando']].filter(([k]) => bt[k]);
  return `<h3>¿Funcionan las listas de esta app?</h3>
    <div class="card" data-ir="ideas" style="cursor:pointer"><p class="muted" style="margin:0 0 8px;font-size:13px">${esc(bt.como)}</p>
    ${L.map(([k, n]) => `<div class="bt"><span>${n}</span><b class="${bt[k].funciona ? 'up' : 'down'}">${esc(bt[k].corto)}</b></div>`).join('')}
    ${bt.cobertura ? `<p class="muted" style="margin:8px 0 0;font-size:12px">${esc(bt.cobertura)}</p>` : ''}
    ${L.some(([k]) => bt[k].funciona) ? '' : '<p style="margin:8px 0 0;font-size:13.5px">Conclusión: ninguna lista le gana al índice de forma fiable. Para tu dinero, el plan de <span style="color:var(--acc)">Mi dinero</span>; las listas, para aprender e investigar.</p>'}</div>`;
}

function pintarIdeas() {
  const tabs = [['potencial', '🌱 Potencial'], ['caidas', '📉 Castigadas'], ['directivos', '🏦 Directivos']];
  $('#v-ideas').innerHTML = `
    <h2>💡 Ideas para investigar</h2>
    <p class="sub">Listas automáticas para encontrar empresas que estudiar. Lo serio es que la base de tu dinero sea un fondo índice (pestaña <a href="#dinero" data-ir="dinero">Mi dinero</a>); esto, como mucho, para una parte pequeña.</p>
    <div class="chips seg">${tabs.map(([k, t]) => `<button class="chip ${S.ideas === k ? 'on' : ''}" data-fidea="${k}">${t}</button>`).join('')}</div>
    <div id="ideas-c"></div>`;
  ({ potencial: pintarPotencial, caidas: pintarCaidas, directivos: pintarDirectivos })[S.ideas]();
}

function pintarPotencial() {
  const temas = [['todos', 'Todos']].concat(Object.entries(S.res.temas).map(([k, t]) => [k, `${t.ico} ${t.nombre}`]), [['otros', 'Otros']]);
  let L = S.emp.filter(e => e.sc != null);
  if (S.filtroTema !== 'todos') L = L.filter(e => e.tema === S.filtroTema);
  L.sort((a, b) => b.sc - a.sc);
  $('#ideas-c').innerHTML = `
    <p class="sub">${S.res.n_puntuadas} empresas pequeñas y medianas (valor en bolsa ≤ $10.000 M, con ventas y liquidez) puntuadas de 0 a 100: crecimiento de ventas sostenido 35&nbsp;%, margen bruto 15&nbsp;%, margen operativo 15&nbsp;%, solidez 15&nbsp;%, tema importante 10&nbsp;%, interés creciente 10&nbsp;%. La cifra de la derecha es el crecimiento de ventas: el menor entre el anual y el del último trimestre, para que un cobro puntual no engañe.</p>
    ${medidoHTML('potencial') || '<div class="aviso"><b>Regla, no bola de cristal:</b> la puntuación ordena por calidad y crecimiento, pero <b>no se ha probado</b> que gane al índice. Úsala como lista para investigar.</div>'}
    <div class="chips">${temas.map(([k, t]) => `<button class="chip ${S.filtroTema === k ? 'on' : ''}" data-ftema="${k}">${esc(t)}</button>`).join('')}</div>
    ${L.length ? L.slice(0, 100).map(e => filaEmp(e, `<b class="${cls(e.cr)}">${pct(e.cr, 0)}</b><span class="muted">ventas</span>`)).join('') : '<div class="vacio">Ninguna empresa de este tema pasa los filtros.</div>'}`;
}

function pintarDirectivos() {
  const L = S.emp.filter(compraDir).sort((a, b) => (b.ins.n - a.ins.n) || (b.ins.c - a.ins.c));
  const rev = S.res.n_sec;
  $('#ideas-c').innerHTML = `
    <p class="sub">Consejeros y ejecutivos que han comprado acciones de su empresa <b>con su propio dinero</b> en los últimos 90 días (formulario 4 de la SEC). Arriba, las que tienen más compradores distintos.${rev ? ` Revisadas ${rev.toLocaleString('es-ES')} empresas.` : ''}</p>
    ${medidoHTML('directivos')}
    ${L.length ? L.slice(0, 120).map(e => filaEmp(e, `<b class="up">${usd(e.ins.c)}</b><span class="muted">${e.ins.n} comprador${e.ins.n === 1 ? '' : 'es'}</span>`)).join('') : '<div class="vacio">Ningún directivo ha comprado en los últimos 90 días en las empresas revisadas.</div>'}`;
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
  $('#ideas-c').innerHTML = `
    <p class="sub">Empresas del S&P 500, 400 y 600 que están un 30 % o más por debajo de su máximo del último año.</p>
    ${medidoHTML('castigadas') || `<div class="aviso"><b>Medido (${m.periodo}):</b> comprar las más caídas dio <b>${pctN(m.mediana)}</b> de mediana a 12 meses frente a <b>${pctN(m.mediana_indice)}</b> de la mediana del índice: <b>no le gana</b>. Una caída fuerte a veces es una ganga y a veces es un negocio que se hunde. Las señales de salud ayudan a distinguirlo.</div>`}
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
      <dt>SPY (el listón)</dt><dd>Un fondo que compra las 500 empresas grandes de EE.UU. a la vez. Si tu idea no le gana, es más fácil comprar el índice y olvidarte. Ojo: para guardarlo años, VOO, IVV o SPYM son el mismo índice y cobran menos.</dd>
      <dt>Fondo índice (ETF)</dt><dd>Una cesta que compra todas las empresas de un índice de golpe. Con una sola compra tienes cientos de empresas: si una se hunde, apenas lo notas. Se compra y vende como una acción.</dd>
      <dt>Comisión anual (TER)</dt><dd>Lo que el fondo te cobra cada año por gestionarlo, aunque pierda. 0,03 % = $3 al año por cada $10.000. Parece poco, pero un 1 % al año se come alrededor del 10 % de lo que acumulas aportando cada mes durante 20 años, y casi el 20 % si metes el dinero de golpe y lo dejas 20 años (compruébalo en la calculadora de Mi dinero).</dd>
      <dt>Roth IRA</dt><dd>Cuenta de jubilación de EE.UU.: metes dinero que ya ha pagado impuestos y todo lo que gane sale libre de impuestos a partir de los 59½ años. En 2026: hasta $7.500 al año, con límites de ingresos.</dd>
      <dt>401(k) y «match»</dt><dd>Plan de jubilación de la empresa. Si tu empresa pone dinero cuando tú pones («match»), es la mejor rentabilidad que vas a ver: aporta al menos hasta cobrarlo entero.</dd>
      <dt>Aportar cada mes</dt><dd>Meter la misma cantidad cada mes pase lo que pase. Compras más acciones cuando están baratas y menos cuando están caras, y te quita la tentación de adivinar el mejor momento.</dd>
      <dt>Con dividendos</dt><dd>Las rentabilidades de la app incluyen los dividendos que pagan las empresas, como si los reinvirtieras. Es la rentabilidad real que habrías tenido.</dd>
      <dt>Salida a bolsa (IPO)</dt><dd>El día en que una empresa empieza a vender sus acciones al público. El precio de salida casi solo lo consiguen los fondos; tú compras ya con la subida del primer día.</dd>
      <dt>Lock-up (bloqueo)</dt><dd>Los dueños y empleados no pueden vender hasta unos 180 días después de la salida. Cuando se acaba el bloqueo, muchos venden y el precio suele sufrir.</dd>
      <dt>SPAC</dt><dd>Empresa «cheque en blanco»: sale a bolsa sin negocio para comprar otra empresa más tarde. Muy arriesgadas.</dd>
      <dt>Crecimiento de ventas</dt><dd>Cuánto más vende ahora que hace un año. +40 % = vende 1,4 veces lo de antes. La app usa el menor entre el del último año y el del último trimestre: así un cobro puntual (típico de las biotecnológicas) no parece un crecimiento de verdad.</dd>
      <dt>Oferta diminuta</dt><dd>Salidas a bolsa de menos de $25 M. Con tan pocas acciones en circulación, unos pocos pueden mover el precio a su antojo: hay subidas de ×20 que luego se desploman.</dd>
      <dt>Margen bruto</dt><dd>De cada $100 que vende, cuánto le queda tras pagar lo que cuesta fabricar o dar el servicio. Más alto = negocio más fuerte.</dd>
      <dt>Margen operativo</dt><dd>De cada $100 que vende, cuánto gana con su negocio de verdad, antes de impuestos y de cosas puntuales. Es más fiable que el neto para comparar.</dd>
      <dt>Margen neto (beneficio)</dt><dd>De cada $100 que vende, cuánto le queda al final, ya pagado todo. Negativo = pierde dinero. Puede inflarse un año por algo puntual (una devolución de impuestos, vender un edificio): si es mucho mayor que el operativo, desconfía.</dd>
      <dt>PER</dt><dd>Precio de la acción dividido entre el beneficio por acción: cuántos años de beneficio pagas. 15 es normal; 40 es caro (el mercado espera mucho crecimiento); 8 es barato (o el mercado espera problemas). Compáralo siempre con su sector.</dd>
      <dt>Precio / ventas y EV / EBITDA</dt><dd>Otras formas de ver si algo es caro: lo que pagas por cada $1 que vende, o por cada $1 que gana su negocio contando su deuda. Sirven también cuando la empresa todavía no gana dinero.</dd>
      <dt>Beta</dt><dd>Cuánto se mueve frente al mercado: 1 = igual; 2 = el doble de brusca (sube y baja más); 0,5 = la mitad.</dd>
      <dt>Acciones en corto</dt><dd>Parte de las acciones que alguien ha vendido sin tenerlas, apostando a que bajen. Más del 10 % = mucha gente profesional apuesta en contra.</dd>
      <dt>Contrasplit</dt><dd>La empresa junta varias acciones en una (por ejemplo, 20 en 1) para que el precio no parezca de céntimos y no la echen de la bolsa. Casi siempre es señal de que se ha hundido: una salida a bolsa a $4 con tres contrasplits puede «costar» hoy $37 y haber perdido el 99,9 %.</dd>
      <dt>Compras de directivos (SEC)</dt><dd>Por ley, los directivos de una empresa tienen que avisar a la SEC (formulario 4) cuando compran o venden sus acciones. En estudios con datos de los años 80 a 2000, las empresas donde varios directivos compraban con su dinero lo hacían algo mejor que el mercado. Medido aquí con 2012-2025 (2 o más directivos comprando en 90 días), ya no se distingue del azar: es una pista para investigar, no una señal de compra. Vender dice poco: lo hacen por impuestos o para diversificar.</dd>
      <dt>Folleto (S-1)</dt><dd>El documento oficial que una empresa entrega a la SEC antes de salir a bolsa: cuenta su negocio, sus cifras y sus riesgos.</dd>
      <dt>¿Por qué sí y por qué no?</dt><dd>En cada ficha, los hechos de la empresa a favor y en contra, explicados. Describen la empresa; no dicen si la acción va a subir. Medido aquí, ninguna de estas señales por sí sola ha ganado al índice de forma fiable.</dd>
      <dt>Alarmas en la SEC</dt><dd>Avisos que la empresa ha tenido que presentar a la SEC en los últimos 2 años: que rehace sus cuentas porque las anteriores no eran fiables, que presenta tarde su informe, que está en quiebra o que incumple las normas de su bolsa. Son de las peores señales que puede dar una empresa. La app enlaza cada informe para que lo leas.</dd>
      <dt>Cambio de auditor con mala señal</dt><dd>El auditor revisa las cuentas de la empresa. Cambiarlo suele ser rutina; la app solo lo marca cuando el informe habla de «debilidades materiales» (fallos graves en cómo lleva las cuentas) o de que el auditor renuncia.</dd>
      <dt>Impuestos inciertos</dt><dd>Deducciones que la empresa se ha aplicado y que Hacienda (el IRS u otro fisco) podría no aceptarle y cobrarle. Casi todas las grandes tienen algo; la app avisa cuando pasan del 2 % de lo que vale en bolsa, porque ahí suele haber una disputa seria con el fisco.</dd>
      <dt>Sacar acciones nuevas / recomprar</dt><dd>Si hay más acciones que hace un año, tu trozo de la empresa encoge (se «diluye»): pasa en empresas que necesitan dinero o pagan mucho en acciones a sus empleados. Si hay menos, la empresa ha recomprado las suyas y tu trozo crece.</dd>
      <dt>Caja y deuda</dt><dd>El dinero que tiene en el banco frente a lo que debe. Mucha caja y poca deuda = aguanta mejor una mala racha.</dd>
      <dt>Meses de caja</dt><dd>Si pierde dinero, cuánto tiempo puede seguir así antes de quedarse sin caja. Menos de 18 meses = probablemente tendrá que pedir dinero (y eso suele bajar el precio).</dd>
      <dt>Valor en bolsa</dt><dd>Lo que costaría comprar la empresa entera hoy. Pequeña: menos de $2.000 M; mediana: hasta $10.000 M.</dd>
      <dt>Caída desde máximo</dt><dd>Cuánto ha bajado desde su precio más alto del último año.</dd>
      <dt>Interés (Wikipedia)</dt><dd>Cuánta gente consulta la empresa o el tema. Que crezca dice que se habla más de ello, no que vaya a subir.</dd>
      <dt>Puntuación 0-100</dt><dd>Una regla fija que premia vender cada vez más, con buenos márgenes, con caja y en un tema importante. Medida con las cuentas reales de 2012 a 2025, no se distingue de elegir al azar: ganó al índice unos años y perdió otros. Sirve para elegir qué investigar, no para comprar a ciegas.</dd>
      <dt>¿Por qué ninguna lista gana al índice?</dt><dd>Porque lo que ve esta app (ventas, márgenes, caídas, compras de directivos) lo ven también millones de inversores y programas, y el precio ya lo incluye. Ganar al índice de forma fiable es dificilísimo incluso para profesionales: por eso la base de tu dinero debería ser un fondo índice.</dd>
    </dl></div>
    <h3>De dónde salen los datos</h3>
    <div class="card" style="font-size:14px">Un programa en un servidor revisa el mercado <b>cada 4 horas</b> (calendario y noticias) y hace una pasada completa <b>cada día al cierre</b> de la bolsa: precios, valoración y finanzas de ~1.800 empresas y de los principales fondos índice (Yahoo Finance), calendario de salidas a bolsa (Nasdaq), compras y ventas de directivos (SEC), interés (Wikipedia) y titulares (Google News). La calculadora usa la serie del S&P 500 de Robert Shiller (Yale) desde 1926. Los precios en vivo llegan al segundo mientras la bolsa está abierta.<br><br>Las listas de Ideas se han medido contra el índice con las cuentas que las empresas presentaron a la SEC entre 2011 y 2025 (lo que se sabía en cada fecha, sin mirar al futuro).<br><br><b>Esto no es consejo de inversión.</b> Las cifras pueden tener errores de la fuente. Antes de invertir, compruébalas en la web de la empresa.</div>
    <button class="btn" style="margin-top:14px;width:100%" data-ir="inicio">← Volver</button>`;
}

// ── MI DINERO (2026-10-05): plan, calculadora con 100 años reales, fondos y cartera ──
let CALC = LS.get('rb_calc', { m: 500, a: 20, i: 0, b: 0, f: 0.0003 });
let CARTERA = LS.get('rb_cartera', []);          // [{t, q, px, f:'AAAA-MM-DD'}] solo en este móvil
let PASOS = LS.get('rb_pasos', {});
const mesTxt = (desde, i) => {                     // "1929-09" + i meses -> "sept 1929"
  const [y, m] = desde.split('-').map(Number), d = new Date(y, m - 1 + i, 15);
  return d.toLocaleDateString('es-ES', { month: 'short', year: 'numeric' });
};
const anioDe = (desde, i) => Number(desde.slice(0, 4)) + Math.floor((Number(desde.slice(5, 7)) - 1 + i) / 12);

async function cargarDinero() {
  if (!S.historia) { try { S.historia = await cargar('historia'); } catch { S.historia = null; } }
  if (CARTERA.length && !S.indice) {
    try {
      const j = await cargar('indice');
      const dias = [j.d0]; for (const d of j.dd) dias.push(dias[dias.length - 1] + d);
      S.indice = { dias, v: j.v };
    } catch { S.indice = null; }
  }
}

function pintarDinero() {
  const F = (S.fondos?.f || []);
  const nucleo = ['VOO', 'IVV', 'SPYM', 'FXAIX'].map(t => F.find(x => x.t === t)).filter(Boolean);
  const spy = F.find(x => x.t === 'SPY'), voo = F.find(x => x.t === 'VOO');
  const PL = [
    ['colchon', '🛟 Colchón primero', 'Guarda de 3 a 6 meses de gastos fuera de la bolsa (cuenta remunerada o letras del Tesoro como <b>SGOV</b>). Lo que vayas a necesitar en los próximos 3-5 años <b>no</b> se invierte en acciones: si la bolsa cae justo cuando lo necesitas, te obliga a vender en pérdidas.'],
    ['deudas', '💳 Deudas caras fuera', 'Si debes en tarjeta de crédito (20-30 % al año), pagarla «rinde» más que cualquier inversión y sin riesgo. Primero eso.'],
    ['cuenta', '🏦 Abre la cuenta correcta', `En un bróker sin comisiones (Fidelity, Schwab o Vanguard). En este orden: 1) si tu trabajo tiene <b>401(k)</b> y la empresa pone dinero («match»), aporta al menos hasta cobrar todo lo que te regalan; 2) <b>Roth IRA</b>: en 2026 puedes meter hasta <b>$7.500</b> al año (si ganas menos de $153.000 solo o $242.000 en pareja) y lo que gane no paga impuestos al sacarlo a partir de los 59½; 3) una cuenta normal para el resto.<br><small class="muted">Como vives en EE.UU., usa fondos de EE.UU.: los fondos europeos tienen un trato fiscal muy malo (régimen PFIC). Si piensas volver a vivir en España, consulta antes de aportar mucho a la Roth: puede que España no respete esa ventaja.</small>`],
    ['indice', '📊 Compra el índice entero', `Un solo fondo barato hace el trabajo: ${nucleo.map(x => `<b>${esc(x.t)}</b>`).join(', ') || 'VOO, IVV o SPYM'} (las 500 grandes de EE.UU.) o <b>VTI</b> (todo EE.UU.). Comisión de 0,02-0,03 % al año: $2-3 por cada $10.000. En 2025, el <b>79 %</b> de los fondos de gestión activa de grandes empresas de EE.UU. lo hizo peor que el S&P 500, y a 15 años cerca del 90 % (informe SPIVA de S&P).`],
    ['auto', '🔁 Automatiza y no toques', 'Programa una aportación automática cada mes, el mismo día, pase lo que pase. No intentes adivinar el mejor momento. Y lo más difícil: <b>no vendas cuando caiga</b>. Más abajo tienes cuánto han caído y cuánto tardaron en volver.'],
  ];
  const hechos = PL.filter(([k]) => PASOS[k]).length;
  $('#v-dinero').innerHTML = `
    <div class="hero">
      <div class="lbl">Cómo empezar a invertir sin hacer tonterías</div>
      <div class="cifra" style="font-size:26px;line-height:1.2;margin:4px 0 6px">Fondo índice barato + aportar cada mes + no vender en las caídas</div>
      <div class="lbl">Aburrido, pero es lo que la historia premia. Aquí tienes el plan, los números reales de 100 años y tu cartera comparada con el índice.</div>
    </div>
    ${CARTERA.length ? `<h3>Tu cartera</h3><div id="cartera-res"></div>` : ''}

    <h3>Tu plan en 5 pasos <span class="muted" style="text-transform:none;letter-spacing:0">· ${hechos}/5 hechos</span></h3>
    ${PL.map(([k, t, d], n) => `<label class="paso ${PASOS[k] ? 'ok' : ''}"><input type="checkbox" data-paso="${k}" ${PASOS[k] ? 'checked' : ''}>
      <div><b>${n + 1}. ${t}</b><div class="pd">${d}</div></div></label>`).join('')}

    <h3>¿Cuánto podría tener? 100 años de historia real</h3>
    <div class="card" id="calc">
      <div class="campos">
        <label>Cada mes<span><input type="number" inputmode="decimal" min="0" step="50" id="c-m" value="${CALC.m}"> $</span></label>
        <label>Durante<span><input type="number" inputmode="numeric" min="1" max="40" id="c-a" value="${CALC.a}"> años</span></label>
        <label>Para empezar<span><input type="number" inputmode="decimal" min="0" step="500" id="c-i" value="${CALC.i}"> $</span></label>
        <label>En bonos<span><input type="number" inputmode="numeric" min="0" max="100" step="10" id="c-b" value="${Math.round(CALC.b * 100)}"> %</span></label>
        <label style="grid-column:1/-1">Quiero llegar a (en dólares de hoy)<span><input type="number" inputmode="decimal" min="0" step="10000" id="c-o" value="${CALC.o ?? 500000}"> $</span></label>
      </div>
      <div class="chips envolver" style="margin-top:10px">
        ${[[0.0003, 'Fondo índice 0,03 %'], [0.005, 'Fondo de 0,5 %'], [0.01, 'Asesor o fondo caro 1 %']].map(([f, t]) =>
          `<button class="chip ${CALC.f === f ? 'on' : ''}" data-cfee="${f}">${t}</button>`).join('')}
      </div>
      <div id="calc-res"><div class="muted" style="padding:20px 0;text-align:center">Calculando con 100 años de datos…</div></div>
    </div>

    <h3>¿Aguantarías esto sin vender?</h3>
    <div id="caidas-hist" class="card"><div class="muted">Cargando…</div></div>

    <h3>Fondos índice baratos</h3>
    <p class="sub">Comisión, rentabilidad anual <b>con dividendos</b> y la peor caída desde que existe cada fondo (los que nacieron después de 2009 no vivieron la crisis de 2008: no son más seguros). Datos del cierre de cada día.</p>
    ${spy && voo ? `<div class="aviso"><b>SPY no es el más barato:</b> la app lo usa como listón porque es el más conocido, pero cobra ${pctFino(spy.ter)} al año frente al ${pctFino(voo.ter)} de VOO. Para comprar y guardar, mejor VOO, IVV o SPYM: son el mismo índice.</div>` : ''}
    ${fondosHTML(F)}

    <h3>${CARTERA.length ? 'Añadir otra compra' : 'Tu cartera'}</h3>
    <div class="card">
      ${CARTERA.length ? '' : '<p class="muted" style="margin:0 0 10px;font-size:14px">Apunta lo que compres y la app te dirá cuánto vale ahora y, sobre todo, si le estás ganando al índice. Se guarda solo en este móvil.</p>'}
      <form id="f-cartera" class="campos" autocomplete="off">
        <label>Ticker<span><input id="k-t" placeholder="VOO" maxlength="10" required style="text-transform:uppercase"></span></label>
        <label>Acciones<span><input id="k-q" type="number" inputmode="decimal" min="0" step="any" placeholder="3" required></span></label>
        <label>Precio pagado<span><input id="k-px" type="number" inputmode="decimal" min="0" step="any" placeholder="620" required> $</span></label>
        <label>Fecha<span><input id="k-f" type="date" required value="${new Date().toISOString().slice(0, 10)}"></span></label>
        <button class="btn pri" style="grid-column:1/-1">Añadir a mi cartera</button>
      </form>
      <div id="k-err" class="down" style="font-size:13px;margin-top:6px"></div>
      ${CARTERA.length ? `<div class="enlaces"><button class="btn" id="k-exp">⬇️ Copia de seguridad</button>
        <label class="btn">⬆️ Restaurar<input type="file" id="k-imp" accept="application/json" hidden></label></div>` : ''}
    </div>
    <p class="pie">Cifras históricas: S&P 500 de 1926 a ${esc(S.historia?.hasta || '2026')} (Robert Shiller, con dividendos, descontada la inflación) y Yahoo Finance.<br>El pasado no garantiza el futuro. Información, no consejo de inversión.</p>`;

  $('#f-cartera').addEventListener('submit', anadirCompra);
  ['c-m', 'c-a', 'c-i', 'c-b', 'c-o'].forEach(id => $('#' + id).addEventListener('input', leerCalc));
  if ($('#k-exp')) $('#k-exp').onclick = exportarCartera;
  if ($('#k-imp')) $('#k-imp').onchange = importarCartera;
  cargarDinero().then(() => { pintarCalc(); pintarCaidasHist(); pintarCartera(); });
}

const pctFino = x => x == null ? '—' : nf(x * 100, x * 100 < 0.1 ? 3 : 2) + '\u00a0%';

function fondosHTML(F) {
  const G = { nucleo: 'S&P 500 · el núcleo', total: 'Todo EE.UU.', mundo: 'Todo el mundo', tec: 'Tecnología y crecimiento',
              peq: 'Empresas pequeñas', div: 'Dividendos', renta: 'Renta fija y caja' };
  if (!F.length) return '<div class="card muted">Sin datos de fondos todavía.</div>';
  return Object.entries(G).map(([g, nom]) => {
    const L = F.filter(x => x.g === g);
    if (!L.length) return '';
    return `<div class="grupo-f">${esc(nom)}</div>` + L.map(x => `
      <div class="fila fondo" data-fondo="${esc(x.t)}">
        <div class="info"><div class="nom"><span class="tk">${esc(x.t)}</span>${esc(x.n)}</div>
          <div class="det" style="white-space:normal">${esc(x.d)}</div>
          <div class="det">Comisión <b class="${x.ter == null ? '' : x.ter <= 0.0005 ? 'up' : x.ter > 0.0015 ? 'warn' : ''}">${pctFino(x.ter)}</b> · 10 años <b class="${cls(x.r10a)}">${x.r10a != null ? pct(x.r10a) + '/año' : '—'}</b> · peor caída <b class="down">${pct(x.peor, 0)}</b> (desde ${esc((x.desde || '').slice(0, 4))})${x.rdiv ? ` · dividendo ${pct(x.rdiv, 1, false)}` : ''}</div></div>
        <div class="der"><b data-vp="${esc(x.t)}">${precio(x.px)}</b><span class="${cls(x.r1a)}">${pct(x.r1a)} 1 año</span></div>
      </div>`).join('');
  }).join('');
}

function leerCalc() {
  const n = (id, d) => { const v = parseFloat($('#' + id).value); return isNaN(v) ? d : v; };
  CALC = { ...CALC, m: Math.max(0, n('c-m', 0)), a: Math.min(40, Math.max(1, Math.round(n('c-a', 20)))),
           i: Math.max(0, n('c-i', 0)), b: Math.min(1, Math.max(0, n('c-b', 0) / 100)), o: Math.max(0, n('c-o', 0)) };
  LS.set('rb_calc', CALC);
  clearTimeout(S.tCalc); S.tCalc = setTimeout(pintarCalc, 120);
}

function simular(H, m, anos, ini, bonos, fee) {
  const n = anos * 12, A = H.acc, B = H.bon, fm = fee / 12, out = [];
  for (let s = 0; s + n < A.length; s++) {
    let v = ini;
    for (let t = s; t < s + n; t++) {
      const r = (1 - bonos) * (A[t + 1] / A[t] - 1) + bonos * (B[t + 1] / B[t] - 1) - fm;
      v = (v + m) * (1 + r);
    }
    out.push({ s, v });
  }
  return out;
}
const cuantil = (orden, q) => orden[Math.min(orden.length - 1, Math.max(0, Math.round(q * (orden.length - 1))))];

function pintarCalc() {
  const H = S.historia, el = document.getElementById('calc-res');
  if (!el) return;
  if (!H) { el.innerHTML = '<div class="muted">No se pudieron cargar los datos históricos.</div>'; return; }
  const { m, a, i, b, f } = CALC, puesto = i + m * a * 12;
  if (puesto <= 0) { el.innerHTML = '<div class="muted" style="padding:14px 0">Pon una cantidad para empezar.</div>'; return; }
  const R = simular(H, m, a, i, b, f);
  if (!R.length) { el.innerHTML = '<div class="muted">Demasiados años para los datos disponibles.</div>'; return; }
  const v = R.map(x => x.v).sort((x, y) => x - y);
  const med = cuantil(v, 0.5), p10 = cuantil(v, 0.1), p90 = cuantil(v, 0.9);
  const peor = R.reduce((x, y) => y.v < x.v ? y : x), mejor = R.reduce((x, y) => y.v > x.v ? y : x);
  const pierde = 100 * R.filter(x => x.v < puesto).length / R.length;
  const caro = f < 0.01 ? cuantil(simular(H, m, a, i, b, 0.01).map(x => x.v).sort((x, y) => x - y), 0.5) : null;
  // barras: una por año de inicio (empezando en enero)
  const enero = R.filter(x => (Number(H.desde.slice(5, 7)) - 1 + x.s) % 12 === 0);
  const W = 600, Hh = 150, P = 4, mx = Math.max(...enero.map(x => x.v / puesto), 1.2);
  const bw = (W - 2 * P) / enero.length, y1 = Hh - P - (Hh - 2 * P) / mx;
  const barras = enero.map((x, k) => {
    const mult = x.v / puesto, h = (Hh - 2 * P) * mult / mx;
    return `<rect x="${(P + k * bw).toFixed(1)}" y="${(Hh - P - h).toFixed(1)}" width="${Math.max(1, bw - 1).toFixed(1)}" height="${h.toFixed(1)}" fill="${mult < 1 ? 'var(--down)' : 'var(--acc)'}" opacity="${mult < 1 ? 0.9 : 0.75}"/>`;
  }).join('');
  const marcas = enero.map((x, k) => [anioDe(H.desde, x.s), k]).filter(([y]) => y % 20 === 0)
    .map(([y, k]) => `<span style="left:${(100 * (k + 0.5) / enero.length).toFixed(1)}%">${y}</span>`).join('');
  el.innerHTML = `
    <div class="res">
      <div class="res-g"><span>Lo típico al cabo de ${a} año${a === 1 ? '' : 's'}</span><b class="up">${usd(med)}</b><em>pones ${usd(puesto)} · en dólares de hoy</em></div>
      <div class="grid2" style="margin-top:10px">
        <div class="res-p"><span>Si te toca la peor época</span><b class="${peor.v < puesto ? 'down' : ''}">${usd(peor.v)}</b><em>empezando en ${mesTxt(H.desde, peor.s)}</em></div>
        <div class="res-p"><span>Si te toca la mejor</span><b class="up">${usd(mejor.v)}</b><em>empezando en ${mesTxt(H.desde, mejor.s)}</em></div>
        <div class="res-p"><span>1 de cada 10 veces, menos de</span><b>${usd(p10)}</b><em>y 1 de cada 10, más de ${usd(p90)}</em></div>
        <div class="res-p"><span>Acabaste con menos de lo que pusiste</span><b class="${pierde > 0 ? 'down' : 'up'}">${pierde > 0 ? nf(pierde, pierde < 10 ? 1 : 0) + ' %' : 'nunca'}</b><em>de ${R.length} comienzos posibles desde ${H.desde.slice(0, 4)}</em></div>
      </div>
      ${objetivoHTML(H, a, b, f)}
      ${caro != null ? `<div class="aviso" style="margin:12px 0 0"><b>Lo que cuesta la comisión:</b> con un fondo o asesor de 1 % al año, lo típico sería ${usd(caro)}: <b>${usd(med - caro)} menos</b> (−${nf(100 * (1 - caro / med), 0)} %) por pagar más cada año.</div>` : ''}
    </div>
    <div class="leyenda" style="margin-top:14px"><span>Resultado según el año en que empezaste (× lo que pusiste)</span></div>
    <svg class="grafica" viewBox="0 0 ${W} ${Hh}" preserveAspectRatio="none" style="height:150px">${barras}
      <line x1="0" x2="${W}" y1="${y1.toFixed(1)}" y2="${y1.toFixed(1)}" stroke="var(--txt)" stroke-width="1.2" stroke-dasharray="5 4" vector-effect="non-scaling-stroke"/></svg>
    <div class="ejex">${marcas}</div>
    <p class="muted" style="font-size:12px;margin:8px 0 0">Cada barra es una persona que empezó en enero de ese año. La línea discontinua es lo que puso: por debajo (en rojo), acabó perdiendo. ${b > 0 ? `Con un ${Math.round(b * 100)} % en bonos del Tesoro, reequilibrando cada mes.` : 'Todo en acciones del S&P 500.'} Con dividendos reinvertidos y descontada la inflación (${nf(100 * ((H.ipc[H.ipc.length - 1] / H.ipc[0]) ** (12 / (H.ipc.length - 1)) - 1), 1)} % al año de media).</p>`;
  document.querySelectorAll('[data-cfee]').forEach(b2 => b2.classList.toggle('on', Number(b2.dataset.cfee) === CALC.f));
}

const anosTxt = n => n >= 24 ? `${nf(n / 12, n % 12 ? 1 : 0)} años` : `${n} meses`;

// Al revés: cuánto aportar al mes para llegar al objetivo. El resultado es proporcional a la
// aportación, así que basta con simular $1 al mes (sin capital inicial).
function objetivoHTML(H, a, b, f) {
  const o = CALC.o ?? 500000;
  if (!(o > 0)) return '';
  const v = simular(H, 1, a, 0, b, f).map(x => x.v).sort((x, y) => x - y);
  if (!v.length) return '';
  const tipico = o / cuantil(v, 0.5), seguro = o / cuantil(v, 0.1), peor = o / v[0];
  return `<div class="res-p" style="margin-top:10px"><span>Para llegar a ${usd(o)} en ${a} años tendrías que aportar al mes</span>
    <b>$${nf(Math.ceil(tipico), 0)} <small class="muted" style="font-size:12px;font-weight:400">en lo típico</small></b>
    <em>$${nf(Math.ceil(seguro), 0)} para llegar 9 de cada 10 veces · $${nf(Math.ceil(peor), 0)} para llegar incluso en la peor época desde 1926${CALC.i > 0 ? ` (sin contar los ${usd(CALC.i)} iniciales)` : ''}</em></div>`;
}

function caidasDe(H) {
  const N = H.acc.map((x, k) => x * H.ipc[k]);           // lo que verías en tu cuenta: con dividendos, sin descontar inflación
  const out = []; let pico = 0, ep = null;
  for (let k = 1; k < N.length; k++) {
    if (N[k] >= N[pico]) { if (ep) { ep.rec = k; out.push(ep); ep = null; } pico = k; continue; }
    const dd = N[k] / N[pico] - 1;
    if (!ep && dd <= -0.2) ep = { pico, fondo: k, dd };
    if (ep && dd < ep.dd) { ep.fondo = k; ep.dd = dd; }
  }
  if (ep) out.push(ep);
  return out;
}

function pintarCaidasHist() {
  const el = document.getElementById('caidas-hist'), H = S.historia;
  if (!el || !H) return;
  const L = caidasDe(H);
  el.innerHTML = `<p style="margin:0 0 10px;font-size:14px">Todas las veces que el S&P 500 cayó más de un 20 % desde 1926, con medias mensuales (con dividendos, como lo verías en tu cuenta):</p>
    <div class="tabla-c">${L.map(e => `<div><b>${mesTxt(H.desde, e.pico)}</b><span class="down">${pct(e.dd, 0)}</span>
      <em>${e.rec ? `tardó ${anosTxt(e.rec - e.pico)} en volver a su máximo` : 'aún sin recuperar'}</em></div>`).join('')}</div>
    ${(() => {
      const D = S.fondos?.caidas_spy || [];
      if (!D.length) return '';
      const meses = (a, b) => Math.round((new Date(b) - new Date(a)) / (30.44 * 864e5));
      return `<p style="margin:14px 0 8px;font-size:14px">Mirando el precio de <b>cada día</b> (SPY, desde 1993) las caídas son más hondas, y aparecen dos que con medias mensuales no llegan al 20 %:</p>
        <div class="tabla-c">${D.map(e => `<div><b>${fechaCorta(e.pico)}</b><span class="down">${pct(e.dd, 0)}</span>
          <em>${e.rec ? `tardó ${anosTxt(meses(e.pico, e.rec))} en volver` : 'aún sin recuperar'}</em></div>`).join('')}</div>`;
    })()}
    <p class="muted" style="font-size:12.5px;margin:10px 0 0">Quien vendió en el fondo convirtió una bajada temporal en una pérdida para siempre; quien siguió aportando compró barato.</p>`;
}

// ── cartera ──
function precioActual(t) {
  if (S.vivo[t] != null) return S.vivo[t];
  if (S.porT[t]?.px != null) return S.porT[t].px;
  const f = (S.fondos?.f || []).find(x => x.t === t);
  return f?.px ?? null;
}
function indiceEn(diaUnix) {                            // valor del S&P 500 con dividendos ese día (o el siguiente hábil)
  const I = S.indice;
  if (!I) return null;
  let lo = 0, hi = I.dias.length - 1;
  if (diaUnix > I.dias[hi]) return null;
  while (lo < hi) { const md = (lo + hi) >> 1; if (I.dias[md] < diaUnix) lo = md + 1; else hi = md; }
  return I.v[lo];
}
function indiceHoy() {
  const I = S.indice;
  if (!I) return null;
  const ult = I.v[I.v.length - 1], vivo = S.vivo.SPY;
  return vivo ? ult * vivo / (S.spyCierre || ult) : ult;
}

function pintarCartera() {
  const el = document.getElementById('cartera-res');
  if (!el) return;
  if (!CARTERA.length) { el.innerHTML = ''; return; }
  let inv = 0, val = 0, valIdx = 0, conIdx = true, faltan = 0;
  const hoyI = indiceHoy();
  const filas = CARTERA.map((c, k) => {
    const p = precioActual(c.t), coste = c.q * c.px;
    const ahora = p != null ? c.q * p : null;
    const dia = Math.round(new Date(c.f + 'T12:00:00Z').getTime() / 864e5);
    const i0 = indiceEn(dia);
    const comoIdx = i0 && hoyI ? coste * hoyI / i0 : null;
    inv += coste;
    if (ahora != null) val += ahora; else faltan++;
    if (comoIdx != null) valIdx += comoIdx; else conIdx = false;
    const gan = ahora != null ? ahora - coste : null;
    const vsI = ahora != null && comoIdx != null ? ahora - comoIdx : null;
    return `<div class="lote">
      <div class="info"><div class="nom"><span class="tk">${esc(c.t)}</span>${nf(c.q, c.q % 1 ? 3 : 0)} × ${precio(c.px)}</div>
        <div class="det">${fechaCorta(c.f)} · ahora <span data-vp="${esc(c.t)}">${precio(p)}</span></div></div>
      <div class="der"><b class="${cls(gan)}">${gan != null ? (gan >= 0 ? '+' : '') + usdExacto(gan) : '—'}</b>
        <span class="${cls(vsI)}">${vsI != null ? `${vsI >= 0 ? '+' : ''}${usdExacto(vsI)} vs índice` : (i0 ? '' : 'sin índice esa fecha')}</span></div>
      <button class="icobtn borrar" data-borrar="${k}" aria-label="Borrar">✕</button></div>`;
  }).join('');
  const gan = val - inv, vs = conIdx && !faltan ? val - valIdx : null;
  el.innerHTML = `<div class="card">
    <div class="res-g"><span>Vale ahora</span><b>${faltan ? '…' : usdExacto(val)}</b>
      <em>invertido ${usdExacto(inv)}${faltan ? '' : ` · <span class="${cls(gan)}">${gan >= 0 ? '+' : ''}${usdExacto(gan)} (${pct(gan / inv)})</span>`}</em></div>
    ${vs != null ? `<div class="aviso ${vs >= 0 ? 'aviso-ok' : ''}" style="margin:10px 0 0">
      ${vs >= 0 ? `<b>Le vas ganando al índice por ${usdExacto(vs)}.</b>` : `<b>El índice te va ganando por ${usdExacto(-vs)}.</b>`}
      Si hubieras metido el mismo dinero los mismos días en el S&P 500 (con dividendos), ahora tendrías ${usdExacto(valIdx)}.${vs < 0 ? ' Si esto se mantiene un año o más, lo sensato es pasarse al índice.' : ' Ojo: unos meses de ventaja no prueban nada; mira cómo va dentro de un año.'}</div>` : ''}
    <div style="margin-top:10px">${filas}</div>
    <p class="muted" style="font-size:12px;margin:8px 0 0">No cuenta los dividendos de tus acciones (el índice sí): favorece un poco al índice si tienes empresas que pagan mucho.</p></div>`;
}

async function anadirCompra(ev) {
  ev.preventDefault();
  const t = $('#k-t').value.trim().toUpperCase(), q = parseFloat($('#k-q').value), px = parseFloat($('#k-px').value), f = $('#k-f').value;
  const err = $('#k-err');
  if (!/^[A-Z][A-Z.\-]{0,9}$/.test(t)) return (err.textContent = 'Ese ticker no parece válido (ej. VOO, AAPL, BRK-B).');
  if (!(q > 0) || !(px > 0)) return (err.textContent = 'Pon el número de acciones y el precio que pagaste.');
  if (!f || f > new Date().toISOString().slice(0, 10)) return (err.textContent = 'La fecha no puede ser futura.');
  err.textContent = '';
  CARTERA.push({ t, q, px, f });
  CARTERA.sort((a, b) => a.f.localeCompare(b.f));
  LS.set('rb_cartera', CARTERA);
  pintarDinero();
  actualizarEnVivo();
}
function exportarCartera() {
  const blob = new Blob([JSON.stringify({ app: 'radar-bolsa', cartera: CARTERA, fecha: new Date().toISOString() }, null, 1)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = `mi-cartera-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.appendChild(a); a.click(); a.remove();
}
function importarCartera(ev) {
  const f = ev.target.files?.[0];
  if (!f) return;
  f.text().then(txt => {
    const j = JSON.parse(txt), L = Array.isArray(j) ? j : j.cartera;
    if (!Array.isArray(L) || !L.every(c => c.t && c.q > 0 && c.px > 0 && c.f)) throw new Error('formato');
    CARTERA = L; LS.set('rb_cartera', CARTERA); pintarDinero(); actualizarEnVivo();
  }).catch(() => { $('#k-err').textContent = 'Ese archivo no es una copia de la cartera.'; });
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

// ── ¿Por qué sí y por qué no? (2026-10-06) ──────────────────────────────────
// Hechos de la empresa a favor y en contra, con su explicación, y las alarmas de la SEC
// (cuentas rehechas, informes tarde, quiebra, problemas para seguir cotizando). Describe la
// empresa; no predice la acción: lo medido dice que ninguna señal sola gana al índice.
const LEGAL = /\b(lawsuits?|class action|sued|sues|investigat\w*|probe[sd]?|subpoena\w*|fraud\w*|indict\w*|antitrust|DOJ|FTC|recalls?|bankrupt\w*|chapter 11|going concern|restat\w*|short[- ]sell\w*|whistleblower)\b/i;
const FISCAL = /\b(IRS|tax (disputes?|court|evasion|probes?|fraud|bills?|claims?)|back taxes|transfer pricing)\b/i;
const url8k = (e, a) => `https://www.sec.gov/Archives/edgar/data/${Number(e.cik)}/${a[1].replace(/-/g, '')}/${a[2]}`;
const veces = n => n > 1 ? ` (${n} veces en 2 años)` : '';
const listaY = a => a.length > 1 ? a.slice(0, -1).join(', ') + ' y ' + a[a.length - 1] : (a[0] || '');

function porQue(e) {
  const si = [], no = [], graves = [];
  const ms = S.res.med_sector?.[e.sector] || {};
  const s = e.sec || {};
  const ult = k => s[k][0];
  const sec = (k, titulo, texto, lista) => lista.push([titulo, texto, url8k(e, ult(k))]);

  // Alarmas de la SEC (2 años)
  if (s.rehace) sec('rehace', 'Ha tenido que rehacer sus cuentas', `El ${fechaCorta(ult('rehace')[0])} avisó a la SEC de que sus cifras anteriores no eran fiables${veces(s.rehace.length)}. Los números que ves pueden no ser buenos.`, graves);
  if (s.quiebra) sec('quiebra', 'Quiebra', `El ${fechaCorta(ult('quiebra')[0])} comunicó a la SEC que la empresa o una de sus filiales se acoge a la ley de quiebras. En una quiebra el accionista es el último en cobrar y suele perderlo todo.`, graves);
  if (s.tarde) sec('tarde', 'Presentó tarde sus cuentas', `El ${fechaCorta(ult('tarde')[0])} avisó a la SEC de que no llegaba a tiempo con su informe${veces(s.tarde.length)}. Suele esconder problemas contables o de dinero.`, graves);
  if (s.cotiza) sec('cotiza', 'Problemas para seguir en bolsa', `El ${fechaCorta(ult('cotiza')[0])} comunicó que incumple las normas de su bolsa (por ejemplo, precio por debajo de $1 o cuentas sin presentar a tiempo). Si no lo arregla, la pueden sacar.`, graves);
  if (s.auditor) sec('auditor', 'Cambio de auditor con mala señal', `El ${fechaCorta(ult('auditor')[0])}: el informe habla de fallos graves en sus controles contables o de que el auditor se va.`, no);
  if (s.deterioro) sec('deterioro', 'Algo vale menos de lo que pagó', `El ${fechaCorta(ult('deterioro')[0])} apuntó una pérdida importante: algo que compró o construyó vale menos de lo que creía.`, no);

  // El negocio (en bancos, aseguradoras e inmobiliarias no se puede comparar así)
  const mo = e.mo ?? e.mn;
  if (!e.fin) {
    if (e.cr != null && e.cr >= 0.10 && !e.irreg) si.push(['Vende cada vez más', `Sus ventas crecen un ${pct(e.cr, 0, false)} al año.`]);
    else if (e.cr != null && e.cr <= -0.05) no.push(['Vende menos que antes', `Sus ventas han caído un ${pct(-e.cr, 0, false)} en un año.`]);
    if (e.irreg) no.push(['Ingresos irregulares', 'El último trimestre se dispara frente al año: no es su ritmo normal.']);
    if (mo != null) {
      if (mo >= 0.15) si.push(['Gana mucho con su negocio', `Se queda ${nf(mo * 100, 0)} céntimos de cada $1 que vende.`]);
      else if (mo >= 0.05) si.push(['Gana dinero con su negocio', `Se queda ${nf(mo * 100, 0)} céntimos de cada $1 que vende.`]);
      else if (mo < 0) no.push(['Pierde dinero con su negocio', `Pierde ${-mo >= 1 ? '$' + nf(-mo, 2) : nf(-mo * 100, 0) + ' céntimos'} por cada $1 que vende.`]);
    }
    if (e.mn > 0 && e.mo != null && e.mn > e.mo + 0.1) no.push(['Beneficio inflado', 'Su beneficio final es mucho mayor que el de su negocio: hay algo puntual (impuestos, venta de algo) que no se repetirá.']);
    if (e.mfcf != null && e.mfcf >= 0.10) si.push(['Le sobra dinero de verdad', `Le quedan ${nf(e.mfcf * 100, 0)} céntimos de caja libre por cada $1 que vende.`]);
    if (e.caja != null) {
      const deuda = e.deuda || 0;
      if (e.caja >= deuda) si.push(['Más dinero que deudas', `Tiene ${usd(e.caja)} en caja frente a ${deuda ? usd(deuda) : '$0'} de deuda.`]);
      else if (e.ebitda > 0 && deuda / e.ebitda > 4) no.push(['Debe mucho', `Su deuda (${usd(deuda)}) equivale a ${nf(deuda / e.ebitda, 1)} años de lo que gana su negocio. Más de 4 ya es mucho.`]);
      else if (!(e.ebitda > 0) && deuda > e.caja) no.push(['Debe más de lo que tiene', `${usd(deuda)} de deuda frente a ${usd(e.caja)} en caja, y su negocio no da para pagarla.`]);
    }
  }
  const run = e.run != null ? Math.round(e.run * 3) : null;
  if (run != null && run < 18) no.push(['Se le acaba el dinero', `Al ritmo de pérdidas de ahora le quedan unos ${run} meses de caja: tendrá que pedir prestado o sacar acciones nuevas, que diluyen las tuyas.`]);

  // Acciones nuevas o recompradas (en una salida a bolsa reciente el año anterior no compara)
  if (e.dil != null && e.idx !== 'IPO') {
    if (e.dil >= 0.05) no.push(['Saca acciones nuevas', `Hay un ${pct(e.dil, 0, false)} más de acciones que hace un año: tu trozo de la empresa encoge${e.dil >= 0.25 ? ' (a veces es para comprar otra empresa)' : ''}.`]);
    else if (e.dil <= -0.02) si.push(['Recompra sus acciones', `Hay un ${pct(-e.dil, 0, false)} menos de acciones que hace un año: cada acción tuya es un trozo mayor.`]);
  }
  // Impuestos: lo que dedujo y Hacienda podría no aceptarle
  if (e.utb && e.mc && e.utb / e.mc >= 0.02 && e.utb < e.mc) no.push(['Posibles problemas con Hacienda', `Declara ${usd(e.utb)} en impuestos inciertos (deducciones que el fisco podría rechazarle y cobrarle): el ${pct(e.utb / e.mc, 1, false)} de lo que vale en bolsa.`]);

  // Precio
  if (e.pe > 0 && ms.pe) {
    if (e.pe < 0.75 * ms.pe && (mo ?? 0) > 0) si.push(['Más barata que su sector', `Pagas ${nf(e.pe, 1)} veces su beneficio; en su sector, ${nf(ms.pe, 1)}.`]);
    else if (e.pe > 2 * ms.pe) no.push(['Cara frente a su sector', `Pagas ${nf(e.pe, 1)} veces su beneficio; en su sector, ${nf(ms.pe, 1)}. Se espera mucho de ella: si decepciona, cae fuerte.`]);
  } else if (!(e.pe > 0) && e.ps != null && ms.ps && e.ps > 3 * ms.ps) {
    no.push(['Cara para lo que vende', `Pagas ${nf(e.ps, 1)} veces sus ventas; en su sector, ${nf(ms.ps, 1)}.`]);
  }
  if (e.div >= 0.02 && (e.mn ?? 0) > 0) si.push(['Te paga dividendo', `Un ${pct(e.div, 1, false)} al año: unos $${nf(e.div * 100, 0)} por cada $100 invertidos.`]);

  // Bolsa
  const spy = S.res.spy?.r1a;
  if (e.r1a != null && spy != null) {
    const d = e.r1a - spy;
    if (d >= 0.10) si.push(['Va mejor que el mercado', `En un año ha ganado ${pts(d)} más que el S&P 500.`]);
    else if (d <= -0.25 && !e.cast) no.push(['Va muy por detrás del mercado', `En un año, ${pts(d)} frente al S&P 500.`]);
  }
  if (e.cast) {
    const sal = e.salud || [];
    if (sal.length >= 3) si.push(['Ha caído, pero el negocio aguanta', `Está un ${pct(-e.dd, 0, false)} por debajo de su máximo, pero ${listaY(sal)}.`]);
    else no.push(['Se ha hundido', `Está un ${pct(-e.dd, 0, false)} por debajo de su máximo del año${sal.length ? '' : ' y no muestra señales de salud'}.`]);
  }
  if (e.corto >= 0.10) no.push(['Muchos apuestan a que baje', `El ${pct(e.corto, 0, false)} de sus acciones está vendido en corto.`]);
  if (e.beta >= 1.6) no.push(['Muy brusca', `Se mueve ${nf(e.beta, 1)} veces más que el mercado, al subir y al bajar.`]);
  else if (e.idx === '500' && e.beta != null && e.beta < 0.8) si.push(['Grande y tranquila', 'Está en el S&P 500 y se mueve menos que el mercado.']);
  const ins = compraDir(e);
  if (ins) si.push(['Sus directivos compran', `${ins.n} directivo${ins.n === 1 ? ' ha' : 's han'} comprado ${usd(ins.c)} con su propio dinero en 90 días.`]);

  // Salidas a bolsa
  if (e.idx === 'IPO' && S.ipos?.medido) {
    const m = S.ipos.medido[e.grande ? 'grandes' : 'pequenas'];
    no.push(['Acaba de salir a bolsa', `De mediana, las salidas a bolsa ${e.grande ? 'grandes' : 'pequeñas'} pierden un ${nf(-m.mediana_1a, 0)} % en su primer año y solo ganan el ${m.ganan_1a} % (lo medimos).`]);
    const dl = e.lockup ? diasHasta(e.lockup) : null;
    if (dl != null && dl > 0 && dl <= 60) no.push(['Se acaba el bloqueo', `El ${fechaCorta(e.lockup)} los primeros dueños podrán empezar a vender sus acciones.`]);
    if (e.ncs) no.push(['Ha hecho contrasplits', `Ha juntado acciones ${e.ncs === 1 ? 'una vez' : e.ncs + ' veces'} para que el precio no parezca hundido.`]);
  }

  // Titulares de demandas, investigaciones o problemas con Hacienda (los que hay guardados)
  const nts = S.not?.emp?.[e.t] || [];
  const fis = nts.filter(n => FISCAL.test(n.ti));
  const leg = nts.filter(n => !FISCAL.test(n.ti) && LEGAL.test(n.ti));
  if (fis.length) no.push(['Noticias de un problema con Hacienda', `«${fis[0].ti}»`, fis[0].url]);
  if (leg.length) no.push(['Noticias de demandas o investigaciones', `«${leg[0].ti}»${leg.length > 1 ? ` y ${leg.length - 1} más` : ''}. Ojo: a veces son bufetes buscando clientes tras una caída.`, leg[0].url]);
  return { si, no, graves };
}

function porQueHTML(e) {
  const { si, no, graves } = porQue(e);
  const item = ([t, d, u]) => `<div class="pq-i"><b>${esc(t)}</b><span>${esc(d)}</span>${u ? `<a href="${esc(u)}" target="_blank" rel="noopener">${u.includes('sec.gov') ? 'Ver el informe en la SEC' : 'Leer la noticia'} ↗</a>` : ''}</div>`;
  let an = '';
  if (e.nan >= 3 && e.rec) {
    const txt = e.rec <= 1.5 ? 'comprar sin dudar' : e.rec <= 2.5 ? 'comprar' : e.rec <= 3.5 ? 'mantener' : 'vender';
    const sube = e.obj && e.px ? e.obj / e.px - 1 : null;
    an = `<p class="pq-an"><b>Los analistas</b> (${e.nan}) dicen de media «${txt}»${sube != null ? ` y le ponen un precio objetivo de ${precio(e.obj)} (<span class="${cls(sube)}">${pct(sube, 0)}</span>)` : ''}. Ojo: sus objetivos suelen pecar de optimistas.</p>`;
  }
  return `<h3>¿Por qué sí y por qué no?</h3>
    ${graves.length ? `<div class="pq-graves"><div class="pq-t">🚩 Alarmas en la SEC</div>${graves.map(item).join('')}</div>` : ''}
    <div class="pq">
      <div class="pq-col si"><div class="pq-t">👍 A favor <span>${si.length}</span></div>${si.map(item).join('') || '<div class="pq-vacio">Nada destacable a favor.</div>'}</div>
      <div class="pq-col no"><div class="pq-t">👎 En contra <span>${no.length + graves.length}</span></div>${no.map(item).join('') || (graves.length ? '<div class="pq-vacio">Además de las alarmas de arriba, nada destacable.</div>' : '<div class="pq-vacio">Nada destacable en contra.</div>')}</div>
    </div>
    ${an}
    <p class="muted" style="font-size:12px;margin:8px 0 0">Son hechos de la empresa, no una predicción. Lo medimos: ninguna de estas señales, por sí sola, ha ganado al S&P 500 de forma fiable. Si no sabes explicar por qué esta acción y no un fondo índice, mejor el fondo índice.</p>`;
}

function abrirFicha(t) {
  const e = S.porT[t];
  if (!e) return;
  const fav = favs.has(e.t);
  const run = e.run != null ? Math.round(e.run * 3) : null;
  const noAplica = 'No comparable en bancos, aseguradoras e inmobiliarias';
  const met = [
    ['Valor en bolsa', usd(e.mc), e.mc == null ? 'Sin dato' : e.mc < 2e9 ? 'Pequeña' : e.mc < 10e9 ? 'Mediana' : 'Grande'],
    ['Ventas (12 meses)', usd(e.rev), e.fin ? noAplica : 'Lo que ha vendido en el último año'],
    ['Crecimiento anual', `<span class="${cls(e.cr_a)}">${pct(e.cr_a)}</span>`, e.fin ? noAplica : 'Ventas del último año fiscal frente al anterior'],
    ['Último trimestre', `<span class="${cls(e.cr_q)}">${pct(e.cr_q)}</span>`, e.irreg ? '⚠️ Se dispara frente al año: ingresos irregulares' : 'Frente al mismo trimestre del año pasado'],
    ['Margen bruto', e.fin ? '—' : pct(e.mb, 0, false), e.fin ? noAplica : 'Lo que le queda tras el coste directo'],
    ['Margen operativo', `<span class="${cls(e.mo)}">${pct(e.mo, 1)}</span>`, 'Lo que gana con su negocio, sin cobros ni impuestos puntuales'],
    ['Margen neto', `<span class="${cls(e.mn)}">${pct(e.mn, 1)}</span>`, e.mn < 0 ? 'Pierde dinero' : (e.mo != null && e.mn > e.mo + 0.1 ? '⚠️ Mucho mayor que el operativo: hay algo puntual (impuestos, ventas de activos)' : 'Lo que gana al final')],
    ['Caja / Deuda', `${usd(e.caja)} / ${usd(e.deuda)}`, 'Dinero en el banco frente a lo que debe'],
    ['Caída desde máximo', `<span class="${cls(e.dd)}">${pct(e.dd, 0)}</span>`, `Máximo 1 año: ${precio(e.hi)}`],
    ['Interés en Wikipedia', e.wv ? `<span class="${cls(e.wv.tend)}">${pct(e.wv.tend, 0)}</span>` : '—', e.wv ? `${e.wv.dia.toLocaleString('es-ES')} visitas/día` : 'Sin artículo'],
  ];
  if (run != null) met.push(['Meses de caja', `<span class="${run < 18 ? 'down' : 'up'}">${run}</span>`, run < 18 ? 'Pronto tendrá que pedir dinero' : 'Al ritmo de pérdidas actual']);
  if (e.idx === 'IPO') {
    met.unshift(['Salida a bolsa', fechaCorta(e.ipo_fecha), `a ${precio(e.ipo_px)} · oferta ${usd(e.ipo_usd)}`],
                ['Desde la salida', `<span class="${cls(e.r_ipo)}">${pct(e.r_ipo)}</span>`, `Desde el 1er día: ${pct(e.r_dia1)}`]);
    if (e.ncs) met.splice(2, 0, ['Contrasplits', `<span class="down">${e.ncs}</span>`,
      `Ha juntado acciones para que el precio no parezca hundido: 1 acción de hoy = ${nf(e.aj, 0)} de las de la salida (equivale a salir a ${precio(e.ipo_px_aj)})`]);
  }
  // Valoración frente a la mediana de su sector (2026-10-05)
  const ms = S.res.med_sector?.[e.sector] || {};
  const comp = (x, med, txt) => med ? `${txt} · sector: ${nf(med, 1)}` : txt;
  const val = [
    ['PER (12 meses)', e.pe > 0 ? nf(e.pe, 1) : (e.pe != null || e.mn < 0 ? 'pierde dinero' : '—'), comp(e.pe, ms.pe, 'Lo que pagas por cada $1 de beneficio anual')],
    ['PER previsto', e.fpe > 0 ? nf(e.fpe, 1) : '—', comp(e.fpe, ms.fpe, 'Con el beneficio que esperan los analistas')],
    ['Precio / ventas', e.ps != null ? nf(e.ps, 1) : '—', comp(e.ps, ms.ps, 'Lo que pagas por cada $1 que vende')],
    ['EV / EBITDA', e.eveb > 0 ? nf(e.eveb, 1) : '—', comp(e.eveb, ms.eveb, 'Precio con la deuda incluida frente al beneficio del negocio')],
    ['Dividendo', e.div ? pct(e.div, 2, false) : 'no paga', 'Lo que te paga al año por cada $100 invertidos'],
    ['Beta', e.beta != null ? nf(e.beta, 2) : '—', e.beta == null ? 'Sin dato' : e.beta > 1.5 ? 'Mucho más brusca que el mercado' : e.beta < 0.7 ? 'Más tranquila que el mercado' : '1 = se mueve como el mercado'],
    ['Apuestas en contra', e.corto != null ? pct(e.corto, 1, false) : '—', e.corto > 0.1 ? '⚠️ Más del 10 % apuesta a que baje (acciones en corto)' : 'Parte de las acciones vendidas en corto'],
  ];
  const NOM = { crec: 'Crecimiento de ventas', margen: 'Margen bruto', benef: 'Margen operativo', solidez: 'Solidez (caja vs deuda)', tema: 'Tema importante', interes: 'Interés creciente' };
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
      <div class="kpi"><b class="${cls((e.r1a ?? 0) - (S.res.spy?.r1a ?? 0))}">${e.r1a != null && S.res.spy ? pts(e.r1a - S.res.spy.r1a) : '—'}</b><span>vs SPY 1 año</span></div>
    </div>
    <div id="graf"><div class="muted" style="font-size:13px;padding:30px 0">Cargando gráfica…</div></div>
    ${porQueHTML(e)}
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
    <h3>¿Cara o barata?</h3><div class="met">${val.map(([a, b, c]) => `<div><span class="et">${a}</span><b>${b}</b><em>${c}</em></div>`).join('')}</div>
    <p class="muted" style="font-size:12px;margin:8px 0 0">Un PER o un precio/ventas bajos no significan «ganga»: a veces el mercado ya espera problemas. Sirven para comparar con empresas parecidas, no para decidir solos. Rentabilidades de arriba con dividendos.</p>
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
  const antes = S.vivo[t] ?? e?.px;
  const subio = antes != null && d.p > antes, bajo = antes != null && d.p < antes;
  S.vivo[t] = d.p;
  if (S.vista === 'dinero' && (t === 'SPY' || CARTERA.some(c => c.t === t))) {
    clearTimeout(S.tCart); S.tCart = setTimeout(pintarCartera, 700);      // la cartera cambia con el precio
  }
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
  if (go) {
    ev.preventDefault();
    if (go.dataset.sub) { S.ideas = go.dataset.sub; pintarIdeas(); }
    return ir(go.dataset.ir);
  }
  const tm = ev.target.closest('[data-tema]');
  if (tm) { S.filtroTema = tm.dataset.tema; S.ideas = 'potencial'; pintarIdeas(); return ir('ideas'); }
  const fid = ev.target.closest('[data-fidea]');
  if (fid) { S.ideas = fid.dataset.fidea; pintarIdeas(); suscribirStream(); return actualizarEnVivo(); }
  const ft = ev.target.closest('[data-ftema]');
  if (ft) { S.filtroTema = ft.dataset.ftema; return pintarPotencial(); }
  const fc = ev.target.closest('[data-fcaida]');
  if (fc) { S.filtroCaida = fc.dataset.fcaida; return pintarCaidas(); }
  const fee = ev.target.closest('[data-cfee]');
  if (fee) { CALC.f = Number(fee.dataset.cfee); LS.set('rb_calc', CALC); return pintarCalc(); }
  const bo = ev.target.closest('[data-borrar]');
  if (bo) {                                   // dos toques: el primero pide confirmación
    if (bo.dataset.seguro) {
      CARTERA.splice(Number(bo.dataset.borrar), 1); LS.set('rb_cartera', CARTERA); return pintarDinero();
    }
    bo.dataset.seguro = '1'; bo.textContent = '¿Borrar?'; bo.classList.add('confirmar');
    return setTimeout(() => { if (bo.isConnected) { delete bo.dataset.seguro; bo.textContent = '✕'; bo.classList.remove('confirmar'); } }, 3000);
  }
  const fi = ev.target.closest('[data-fipo]');
  if (fi) { ev.preventDefault(); S.filtroIpo = fi.dataset.fipo; return pintarIpos(); }
  const f = ev.target.closest('[data-t]');
  if (f && !ev.target.closest('.hoja')) return abrirFicha(f.dataset.t);
});
document.addEventListener('change', ev => {           // casillas del plan en 5 pasos
  const p = ev.target.closest('[data-paso]');
  if (!p) return;
  PASOS[p.dataset.paso] = p.checked; LS.set('rb_pasos', PASOS);
  p.closest('.paso').classList.toggle('ok', p.checked);
  const h = [...document.querySelectorAll('[data-paso]')].filter(x => x.checked).length;
  const lbl = p.closest('.vista').querySelector('h3 .muted');
  if (lbl) lbl.textContent = `· ${h}/5 hechos`;
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
