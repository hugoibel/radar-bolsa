#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Radar Bolsa — colector de datos.

Recoge datos PÚBLICOS (Nasdaq, Yahoo, Wikipedia, Google News), los puntúa y
escribe los JSON que lee la app. Solo consulta fuentes: no opera, no compra y
no toca ningún bot.

Uso:  colector.py completo|rapido --salida DIR
  completo  universo entero + precios + fundamentales + interés (1 vez al día)
  rapido    calendario de salidas a bolsa + noticias (cada pocas horas)
"""
import argparse
import bisect
import datetime as dt
import html
import json
import math
import os
import re
import statistics as st
import time
import urllib.parse
import xml.etree.ElementTree as ET

import requests

# Para probar en el PC antes de desplegar: RB_CACHE y RB_SEC cambian las rutas del VPS y
# RB_MAX=80 recorre solo ~80 valores (pasada completa de punta a punta en pocos minutos).
CACHE = os.environ.get("RB_CACHE", "/root/radar-bolsa-cache")
NAV = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
       "Accept": "application/json, text/plain, */*"}
# Wikimedia exige un User-Agent que identifique al programa.
BOT = {"User-Agent": "RadarBolsa/1.0 (+https://hugoibel.github.io/radar-bolsa/)"}
# La SEC exige además un email REAL de contacto (con uno anónimo responde 403). Vive
# en un archivo del VPS, fuera del repo público; sin él se salta la parte de la SEC.
SEC_CONTACTO = os.environ.get("RB_SEC", "/root/radar_bolsa/sec_contacto.txt")

HOY = dt.date.today()
SES = requests.Session()
CRUMB = {"v": None}

# Resultados MEDIDOS con datos reales (2026-10-04, /root/ipo_radar/). La app los
# muestra junto a cada salida a bolsa para no vender humo.
MEDIDO = {
    "ipo": {
        "muestra": 419, "periodo": "2023-2025",
        "todas": {"mediana_1a": -59.0, "ganan_1a": 25, "vs_spy": -79.1, "caida_max": -72.7, "sesion_min": 180},
        "grandes": {"mediana_1a": -7.6, "ganan_1a": 43, "vs_spy": -30.3, "caida_max": -34.5, "sesion_min": 116,
                    "lockup_mediana": 0.3, "lockup_vs_spy": -11.8},
        "pequenas": {"mediana_1a": -76.7, "ganan_1a": 16, "vs_spy": -96.5, "caida_max": -83.6, "sesion_min": 200,
                     "lockup_mediana": -30.6, "lockup_vs_spy": -42.6},
        "dia1_asignacion": 15.5,
    },
    "caidas": {"periodo": "2019-2024", "media": 26.6, "mediana": 14.8, "media_indice": 18.5,
               "mediana_indice": 15.9, "gana_meses": 57,
               "nota": "Sesgo a favor (lista actual del índice). No concluyente."},
}

SECTOR_ES = {
    # GICS (listas S&P)
    "Information Technology": "Tecnología", "Health Care": "Salud", "Financials": "Finanzas",
    "Consumer Discretionary": "Consumo discrecional", "Communication Services": "Comunicaciones",
    "Industrials": "Industria", "Consumer Staples": "Consumo básico", "Energy": "Energía",
    "Utilities": "Servicios públicos", "Real Estate": "Inmobiliario", "Materials": "Materiales",
    # Yahoo (salidas a bolsa)
    "Technology": "Tecnología", "Healthcare": "Salud", "Financial Services": "Finanzas",
    "Consumer Cyclical": "Consumo discrecional", "Consumer Defensive": "Consumo básico",
    "Basic Materials": "Materiales",
}

# Temas "importantes para el mundo". Se asignan por sub-industria GICS (listas S&P),
# por la industria de Yahoo (salidas a bolsa) y, en último caso, por el nombre.
TEMAS = {
    # 2026-10-05: el software YA NO cuenta como IA (metía a Duolingo o a programas de
    # contabilidad). Solo chips, hardware, redes y centros de datos, o "AI" en el nombre.
    "ia": {"nombre": "IA, chips y centros de datos", "ico": "🤖",
           "gics": ["Semiconductors", "Semiconductor Materials & Equipment", "Internet Services & Infrastructure",
                    "Technology Hardware, Storage & Peripherals", "Electronic Components",
                    "Communications Equipment", "Electronic Manufacturing Services"],
           "yind": ["semiconductor", "computer hardware", "electronic components", "communication equipment"],
           "nombre_kw": ["quantum", "semiconductor", "artificial intelligence", "data center", "robot"],
           "wiki": ["Artificial intelligence", "Large language model", "Data center", "Quantum computing"],
           "news": "AI stocks"},
    "energia": {"nombre": "Energía y red eléctrica", "ico": "⚡",
                "gics": ["Electric Utilities", "Independent Power Producers & Energy Traders",
                         "Renewable Electricity", "Heavy Electrical Equipment",
                         "Electrical Components & Equipment", "Multi-Utilities", "Coal & Consumable Fuels"],
                "yind": ["utilities", "uranium", "solar", "electrical equipment"],
                "nombre_kw": ["nuclear", "uranium", "solar", "fusion", "battery"],   # "energy" metía petroleras
                "wiki": ["Nuclear power", "Small modular reactor", "Electrical grid", "Solar power"],
                "news": "nuclear energy stocks"},
    "salud": {"nombre": "Salud y biotecnología", "ico": "🧬",
              "gics": ["Biotechnology", "Pharmaceuticals", "Health Care Equipment",
                       "Life Sciences Tools & Services", "Health Care Technology", "Health Care Supplies"],
              "yind": ["biotechnology", "drug manufacturers", "medical", "diagnostics", "health information"],
              "nombre_kw": ["therapeutics", "bio", "pharma", "medical", "genomic"],
              "wiki": ["Biotechnology", "GLP-1 receptor agonist", "CRISPR gene editing", "Cancer immunotherapy"],
              "news": "biotech stocks"},
    "defensa": {"nombre": "Defensa, espacio y ciberseguridad", "ico": "🛡️",
                "gics": ["Aerospace & Defense"],
                "yind": ["aerospace & defense", "security & protection"],
                "nombre_kw": ["cyber", "defense", "drone", "aerospace", "rocket", "satellite"],  # "space" metía trasteros
                "wiki": ["Computer security", "Unmanned aerial vehicle", "Satellite internet constellation",
                         "Military drone"],
                "news": "defense stocks"},
    "infra": {"nombre": "Infraestructura y automatización", "ico": "🏗️",
              "gics": ["Construction & Engineering", "Industrial Machinery & Supplies & Components",
                       "Building Products", "Construction Machinery & Heavy Transportation Equipment",
                       "Trading Companies & Distributors"],
              "yind": ["engineering & construction", "specialty industrial machinery", "building products",
                       "farm & heavy construction", "industrial distribution"],
              "nombre_kw": ["infrastructure", "automation", "engineering"],
              "wiki": ["Robotics", "Automation", "Infrastructure", "Industrial robot"],
              "news": "infrastructure stocks"},
    "recursos": {"nombre": "Agua, alimentos y materiales críticos", "ico": "💧",
                 "gics": ["Water Utilities", "Agricultural Products & Services",
                          "Fertilizers & Agricultural Chemicals", "Agricultural & Farm Machinery",
                          "Copper", "Diversified Metals & Mining", "Gold", "Silver", "Aluminum",
                          "Precious Metals & Minerals"],
                 "yind": ["water", "copper", "metals & mining", "gold", "silver", "agricultural", "aluminum"],
                 "nombre_kw": ["water", "lithium", "rare earth", "copper", "mining", "minerals"],
                 "wiki": ["Rare-earth element", "Lithium", "Water scarcity", "Copper"],
                 "news": "rare earth stocks"},
}


# ── utilidades ───────────────────────────────────────────────────────────────
def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def pedir(url, cab=NAV, tipo="json", intentos=3, timeout=30):
    for k in range(intentos):
        try:
            r = SES.get(url, headers=cab, timeout=timeout)
            if r.status_code == 404:
                return None
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(6 * (k + 1))
                continue
            r.raise_for_status()
            return r.json() if tipo == "json" else r.text
        except (requests.RequestException, ValueError) as e:
            if k == intentos - 1:
                log("  fallo", url[:100], str(e)[:80])
            time.sleep(2 * (k + 1))
    return None


def limpiar(fragmento):
    t = re.sub(r"<sup.*?</sup>", "", fragmento, flags=re.S)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).strip()


def num(s):
    try:
        return float(str(s).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def r4(x, d=4):
    """Redondeo a cifras significativas para que el JSON pese poco."""
    if x is None or isinstance(x, bool):
        return x
    if x == 0:
        return 0
    return float(f"{x:.{d}g}")


def guardar(ruta, obj):
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, ruta)


def leer(ruta, defecto=None):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return defecto


def es_spac(nombre, ticker):
    n = (nombre or "").lower()
    return "acquisition" in n or "blank check" in n or (ticker.endswith("U") and len(ticker) >= 5)


def nombre_corto(n):
    for _ in range(2):
        n = re.sub(r"[,.]?\s+(Inc|Corp|Corporation|Co|Company|Holdings?|Ltd|Limited|plc|PLC|N\.V|S\.A|LLC|"
                   r"L\.P|Group|Class [A-Z])\.?$", "", n.strip())
    return n.strip(" ,.")


# ── universo ─────────────────────────────────────────────────────────────────
def lista_sp(url, indice):
    h = pedir(url, NAV, "text", timeout=40)
    if not h:
        return []
    i = h.find('id="constituents"')
    if i < 0:
        i = h.find("wikitable")
    t = h[i:h.find("</table>", i)]
    out = []
    for fila in t.split("<tr")[1:]:
        celdas = re.split(r"<td[^>]*>", fila)[1:]
        if len(celdas) < 4:
            continue
        txt = [limpiar(c) for c in celdas]
        tick = txt[0].split()[0] if txt[0] else ""
        if not re.fullmatch(r"[A-Z][A-Z.\-]{0,6}", tick):
            continue
        m = re.search(r'href="(?:https://en\.wikipedia\.org)?/wiki/([^"#]+)"', celdas[1])
        out.append({"t": tick.replace(".", "-"), "n": txt[1], "sector": txt[2], "sub": txt[3],
                    "wiki": urllib.parse.unquote(m.group(1)) if m else None, "idx": indice})
    return out


def mes_nasdaq(fecha):
    """Calendario de salidas a bolsa de Nasdaq de un mes. Los meses cerrados se cachean."""
    clave = fecha.strftime("%Y-%m")
    ruta = f"{CACHE}/nasdaq/{clave}.json"
    cerrado = fecha.year * 12 + fecha.month + 1 < HOY.year * 12 + HOY.month   # ya no cambia
    if cerrado and os.path.exists(ruta):
        return leer(ruta, {})
    j = pedir(f"https://api.nasdaq.com/api/ipo/calendar?date={clave}")
    d = (j or {}).get("data") or {}
    if d and cerrado:
        guardar(ruta, d)
    time.sleep(0.4)
    return d


def filas(d, clave):
    v = d.get(clave) or {}
    if "upcomingTable" in v:
        v = v["upcomingTable"] or {}
    return v.get("rows") or []


def ipos_recientes(meses=18):
    out, f = [], (HOY.replace(day=1) - dt.timedelta(days=31 * meses)).replace(day=1)
    while f <= HOY:
        for r in filas(mes_nasdaq(f), "priced"):
            t = (r.get("proposedTickerSymbol") or "").strip()
            n = r.get("companyName") or ""
            if not t or es_spac(n, t):
                continue
            try:
                fecha = dt.datetime.strptime(r["pricedDate"], "%m/%d/%Y").date()
            except (KeyError, ValueError):
                continue
            out.append({"t": t, "n": n, "idx": "IPO", "ipo_fecha": fecha.isoformat(),
                        "ipo_px": num(r.get("proposedSharePrice")),
                        "ipo_usd": num(r.get("dollarValueOfSharesOffered")),
                        "bolsa": r.get("proposedExchange")})
        f = (f.replace(day=28) + dt.timedelta(days=5)).replace(day=1)
    vistos, unicos = set(), []
    for x in sorted(out, key=lambda x: x["ipo_fecha"], reverse=True):
        if x["t"] not in vistos:
            vistos.add(x["t"])
            unicos.append(x)
    return unicos


def calendario():
    """Próximas salidas a bolsa (este mes y el siguiente) y registros recientes en la SEC."""
    prox, reg = [], []
    sig = (HOY.replace(day=28) + dt.timedelta(days=5)).replace(day=1)
    ant = (HOY.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    for f in (HOY.replace(day=1), sig):
        for r in filas(mes_nasdaq(f), "upcoming"):
            t = (r.get("proposedTickerSymbol") or "").strip()
            n = r.get("companyName") or ""
            prox.append({"t": t, "n": n, "bolsa": r.get("proposedExchange"),
                         "rango": r.get("proposedSharePrice"), "acciones": num(r.get("sharesOffered")),
                         "usd": num(r.get("dollarValueOfSharesOffered")),
                         "fecha": r.get("expectedPriceDate"), "spac": es_spac(n, t)})
    for f in (ant, HOY.replace(day=1)):
        for r in filas(mes_nasdaq(f), "filed"):
            t = (r.get("proposedTickerSymbol") or "").strip()
            n = r.get("companyName") or ""
            reg.append({"t": t, "n": n, "usd": num(r.get("dollarValueOfSharesOffered")),
                        "fecha": r.get("filedDate"), "spac": es_spac(n, t)})
    return prox, reg


# ── Yahoo: precios y fundamentales ───────────────────────────────────────────
def crumb():
    """Yahoo pide una cookie + 'crumb' para el resumen financiero."""
    SES.get("https://fc.yahoo.com", headers=NAV, timeout=20)
    r = SES.get("https://query1.finance.yahoo.com/v1/test/getcrumb", headers=NAV, timeout=20)
    CRUMB["v"] = r.text.strip() if r.ok else None
    return CRUMB["v"]


def fundamentales(t):
    if not CRUMB["v"]:
        crumb()
    url = (f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{t}"
           f"?modules=financialData,price,summaryProfile,earnings,summaryDetail,defaultKeyStatistics"
           f"&crumb={CRUMB['v']}")
    j = pedir(url, intentos=2)
    if j is None:                 # crumb caducado: se renueva una vez
        crumb()
        j = pedir(url.rsplit("&crumb=", 1)[0] + f"&crumb={CRUMB['v']}", intentos=1)
    try:
        r = j["quoteSummary"]["result"][0]
    except (TypeError, KeyError, IndexError):
        return {}
    fd, pr, sp = r.get("financialData") or {}, r.get("price") or {}, r.get("summaryProfile") or {}
    sd, ks = r.get("summaryDetail") or {}, r.get("defaultKeyStatistics") or {}

    def v(d, k):
        """Solo números finitos: Yahoo a veces manda "Infinity" como texto (PER con beneficio ~0)."""
        x = d.get(k)
        r = x.get("raw") if isinstance(x, dict) else None
        if isinstance(r, bool) or not isinstance(r, (int, float)):
            return None
        return r if math.isfinite(r) else None

    f = {"cr_q": v(fd, "revenueGrowth"), "mb": v(fd, "grossMargins"), "mn": v(fd, "profitMargins"),
         "mo": v(fd, "operatingMargins"),
         "caja": v(fd, "totalCash"), "deuda": v(fd, "totalDebt"), "rev": v(fd, "totalRevenue"),
         "fcf": v(fd, "freeCashflow"), "mc": v(pr, "marketCap"),
         "ysector": sp.get("sector"), "yind": sp.get("industry"),
         # valoración y riesgo (2026-10-05): sin esto la app no decía si algo es caro o barato
         "pe": v(sd, "trailingPE"), "fpe": v(sd, "forwardPE"), "ps": v(sd, "priceToSalesTrailing12Months"),
         "eveb": v(ks, "enterpriseToEbitda"), "div": v(sd, "dividendYield"), "beta": v(sd, "beta"),
         "corto": v(ks, "shortPercentOfFloat"),
         # «¿por qué sí y por qué no?» de la ficha (2026-10-06): deuda frente a lo que gana y
         # lo que opinan los analistas (la app avisa de que sus objetivos pecan de optimistas)
         "ebitda": v(fd, "ebitda"), "de": v(fd, "debtToEquity"), "cur": v(fd, "currentRatio"),
         "roe": v(fd, "returnOnEquity"), "rec": v(fd, "recommendationMean"),
         "nan": v(fd, "numberOfAnalystOpinions"), "obj": v(fd, "targetMeanPrice")}
    if f["fcf"] is not None and f["rev"]:
        f["mfcf"] = f["fcf"] / f["rev"]          # caja libre por cada dólar vendido
    # Crecimiento ANUAL (último ejercicio vs el anterior). El de un solo trimestre
    # engaña con cobros puntuales (licencias de biotecnológicas: +3.749 %).
    anual = (((r.get("earnings") or {}).get("financialsChart") or {}).get("yearly") or [])
    rv = [v(y, "revenue") for y in anual[-2:]]
    if len(rv) == 2 and rv[0] and rv[0] > 0 and rv[1] is not None:
        f["cr_a"] = rv[1] / rv[0] - 1
    cands = [x for x in (f.get("cr_a"), f["cr_q"]) if x is not None]
    if cands:
        f["cr"] = min(cands)                     # solo cuenta el crecimiento sostenido
    if f.get("cr_a") is not None and f["cr_q"] is not None and f["cr_q"] - f["cr_a"] > 1.0:
        f["irreg"] = True                        # el trimestre se dispara frente al año
    if f["fcf"] is not None and f["fcf"] < 0 and f["caja"]:
        f["run"] = f["caja"] / -f["fcf"] * 4     # trimestres de caja al ritmo de quema actual
    return {k: x for k, x in f.items() if x is not None}


def precios(t, rango="1y"):
    j = pedir(f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?range={rango}&interval=1d&events=split,div")
    try:
        r = j["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
        ts = r.get("timestamp") or []
        adj = ((r["indicators"].get("adjclose") or [{}])[0].get("adjclose")) or [None] * len(ts)
    except (TypeError, KeyError, IndexError):
        return None
    fil = [(ts[i], q["open"][i], q["close"][i], q["volume"][i] or 0, adj[i] or q["close"][i])
           for i in range(len(ts)) if q["close"][i]]
    if len(fil) < 3:
        return None
    c = [x[2] for x in fil]          # precio de cierre (lo que se ve en el mercado)
    a = [x[4] for x in fil]          # cierre ajustado por dividendos: la rentabilidad REAL

    def ret(n, s=a):
        if len(s) > n:
            return s[-1] / s[-1 - n] - 1
        if n == 251 and len(s) >= 240:          # "1 año" de Yahoo trae ~250 sesiones
            return s[-1] / s[0] - 1
        return None

    ult = c[-252:]
    idx = list(range(len(c) - 1, -1, -5))[::-1]
    splits = []
    for s in ((r.get("events") or {}).get("splits") or {}).values():
        if s.get("numerator") and s.get("denominator"):
            splits.append((s["date"], s["denominator"] / s["numerator"]))
    return {
        # r1d con el precio (es lo que marca el mercado hoy); el resto CON dividendos (2026-10-05)
        "px": c[-1], "r1d": ret(1, c), "r1m": ret(21), "r3m": ret(63), "r6m": ret(126), "r1a": ret(251),
        "hi": max(ult), "lo": min(ult), "dd": c[-1] / max(ult) - 1,
        "dv": st.mean(x[2] * x[3] for x in fil[-20:]),
        "ses": len(fil), "o1": fil[0][1],
        "sem": [r4(a[i], 5) for i in idx],    # la gráfica también con dividendos: comparación justa con el SPY
        "d0": time.strftime("%Y-%m-%d", time.gmtime(fil[idx[0]][0])),
        "d1": time.strftime("%Y-%m-%d", time.gmtime(fil[-1][0])),
        "splits": splits,
    }


# ── fondos índice (2026-10-05): el núcleo con el que empieza un inversor ─────
# La comisión, el nombre y la rentabilidad salen de Yahoo cada día (no se escriben a mano).
# Tickers comprobados el 2026-10-05: SPLG ya no existe (ahora SPYM).
FONDOS = [
    ("nucleo", "VOO", "Las 500 empresas grandes de EE.UU. El clásico para empezar."),
    ("nucleo", "IVV", "Lo mismo que VOO (S&P 500), de iShares."),
    ("nucleo", "SPYM", "Lo mismo (S&P 500), de State Street; precio por acción más bajo."),
    ("nucleo", "SPY", "El S&P 500 más famoso y el listón de la app, pero cobra 3 veces más que VOO."),
    ("nucleo", "FXAIX", "S&P 500 de Fidelity (fondo, no ETF: se compra en Fidelity)."),
    ("nucleo", "SWPPX", "S&P 500 de Schwab (fondo, no ETF: se compra en Schwab)."),
    ("total", "VTI", "Todo el mercado de EE.UU.: grandes, medianas y pequeñas (~3.500 empresas)."),
    ("total", "ITOT", "Todo el mercado de EE.UU., de iShares."),
    ("total", "SCHB", "Todo el mercado de EE.UU., de Schwab."),
    ("total", "FSKAX", "Todo el mercado de EE.UU., de Fidelity (fondo)."),
    ("mundo", "VT", "Todo el mundo en un solo fondo (~60 % EE.UU., ~40 % resto)."),
    ("mundo", "VXUS", "Todo el mundo MENOS EE.UU.: para complementar a VOO o VTI."),
    ("tec", "QQQM", "Las 100 mayores del Nasdaq (mucha tecnología). Más barato que QQQ."),
    ("tec", "QQQ", "Lo mismo que QQQM, más caro (pensado para operar mucho)."),
    ("tec", "VUG", "Empresas grandes de crecimiento."),
    ("peq", "IJR", "Las 600 pequeñas del S&P 600 (exige beneficios para entrar)."),
    ("peq", "VB", "Empresas pequeñas de EE.UU., de Vanguard."),
    ("peq", "AVUV", "Pequeñas y baratas (valor), gestión activa con reglas."),
    ("peq", "IWM", "Russell 2000: pequeñas, incluidas las que pierden dinero."),
    ("div", "SCHD", "Empresas que pagan dividendos altos y estables."),
    ("div", "VYM", "Dividendos altos, más diversificado que SCHD."),
    ("renta", "BND", "Bonos de EE.UU. (renta fija): sube menos, cae menos."),
    ("renta", "SGOV", "Letras del Tesoro a 0-3 meses: casi como una cuenta remunerada."),
]
# Respaldo SOLO para cuando Yahoo no da la comisión (comprobado en la web de la gestora).
TER_VERIFICADA = {"SPYM": 0.0002}    # ssga.com, 2026-10-05 (antes SPLG)


def fondos():
    """Comisión, tamaño y rentabilidad CON dividendos de los fondos índice de referencia."""
    out = []
    for grupo, t, desc in FONDOS:
        # OJO: con "range=max" Yahoo devuelve velas MENSUALES aunque se pida 1d (406 puntos
        # en vez de 8.477 para el SPY). Con period1/period2 explícitos sí da las diarias.
        j = pedir(f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?period1=0&period2=9999999999&interval=1d&events=div")
        time.sleep(0.15)
        try:
            r = j["chart"]["result"][0]
            m = r["meta"]
            adj = r["indicators"]["adjclose"][0]["adjclose"]
            ts = r["timestamp"]
        except (TypeError, KeyError, IndexError):
            log("  fondo sin datos:", t)
            continue
        serie = [(ts[i], adj[i]) for i in range(len(ts)) if adj[i]]
        if len(serie) < 260:
            continue
        tss = [x[0] for x in serie]
        fin_ts, fin_v = serie[-1]

        def anual(anos):
            """Rentabilidad anual compuesta CON dividendos entre el cierre de hace `anos`
            años exactos (la última sesión de esa fecha o antes) y el último cierre."""
            obj = fin_ts - anos * 365.25 * 86400
            i = bisect.bisect_right(tss, obj) - 1
            if i < 0:
                return None
            return (fin_v / serie[i][1]) ** (1 / anos) - 1 if anos != 1 else fin_v / serie[i][1] - 1
        f = {"g": grupo, "t": t, "d": desc, "n": m.get("longName") or m.get("shortName") or t,
             "tipo": m.get("instrumentType"), "px": m.get("regularMarketPrice"),
             "r1a": anual(1), "r3a": anual(3), "r5a": anual(5), "r10a": anual(10),
             "desde": time.strftime("%Y-%m-%d", time.gmtime(serie[0][0])),
             "rmax": (fin_v / serie[0][1]) ** (365.25 * 86400 / (fin_ts - serie[0][0])) - 1}
        # peor caída de su historia (de máximo a mínimo, cierre diario con dividendos)
        pico, peor = serie[0][1], 0.0
        for _, v in serie:
            pico = max(pico, v)
            peor = min(peor, v / pico - 1)
        f["peor"] = peor
        if not CRUMB["v"]:
            crumb()
        q = pedir(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{t}"
                  f"?modules=fundProfile,summaryDetail&crumb={CRUMB['v']}", intentos=2)
        time.sleep(0.15)
        try:
            res = q["quoteSummary"]["result"][0]
            fe = (res.get("fundProfile") or {}).get("feesExpensesInvestment") or {}
            sd = res.get("summaryDetail") or {}
            er = (fe.get("annualReportExpenseRatio") or {}).get("raw")
            f.update(ter=er, rdiv=(sd.get("yield") or {}).get("raw"),
                     tam=(sd.get("totalAssets") or {}).get("raw"))
        except (TypeError, KeyError, IndexError):
            pass
        if f.get("ter") is None and t in TER_VERIFICADA:
            f["ter"] = TER_VERIFICADA[t]
        out.append({k: (r4(x) if isinstance(x, float) else x) for k, x in f.items() if x is not None})
    return out


def indice_diario():
    """S&P 500 (SPY) día a día CON dividendos desde 1993: para comparar tu cartera con el índice."""
    j = pedir("https://query1.finance.yahoo.com/v8/finance/chart/SPY?period1=0&period2=9999999999&interval=1d&events=div")
    try:
        r = j["chart"]["result"][0]
        adj = r["indicators"]["adjclose"][0]["adjclose"]
        ts = r["timestamp"]
    except (TypeError, KeyError, IndexError):
        return None
    d0 = dt.date(1970, 1, 1)
    dias, vals = [], []
    for i in range(len(ts)):
        if adj[i]:
            dias.append((dt.datetime.fromtimestamp(ts[i], dt.timezone.utc).date() - d0).days)
            vals.append(r4(adj[i], 6))
    # días como diferencias (pesa poco); la app los reconstruye
    return {"t": "SPY", "d0": dias[0], "dd": [b - a for a, b in zip(dias, dias[1:])], "v": vals, "dias": dias,
            "nota": "Cierre ajustado por dividendos (Yahoo). Día 0 = días desde 1970-01-01."}


def caidas_diarias(ind, umbral=-0.20):
    """Caídas del SPY (con dividendos, cierre diario) de más del 20 %: de máximo a mínimo y
    cuánto tardó en volver al máximo. Con datos diarios salen 2020 y 2022, que con medias
    mensuales (la serie de Shiller) se quedan por debajo del 20 %."""
    d0 = dt.date(1970, 1, 1)
    f = lambda k: (d0 + dt.timedelta(days=ind["dias"][k])).isoformat()
    v, out, pico, ep = ind["v"], [], 0, None
    for k in range(1, len(v)):
        if v[k] >= v[pico]:
            if ep:
                ep["rec"] = f(k)
                out.append(ep)
                ep = None
            pico = k
            continue
        dd = v[k] / v[pico] - 1
        if ep is None and dd <= umbral:
            ep = {"pico": f(pico), "fondo": f(k), "dd": dd}
        if ep and dd < ep["dd"]:
            ep.update(fondo=f(k), dd=dd)
    if ep:
        out.append(ep)
    return [{k: (r4(x) if isinstance(x, float) else x) for k, x in e.items()} for e in out]


# ── SEC: compras y ventas de directivos (formulario 4) ───────────────────────
def cab_sec():
    try:
        with open(SEC_CONTACTO, encoding="utf-8") as f:
            email = f.read().strip()
    except OSError:
        return None
    return {"User-Agent": f"RadarBolsa {email}"} if "@" in email else None


def si(x):
    return (x or "").strip().lower() in ("1", "true")


def form4(cik, acc, doc, cab):
    """Lee un formulario 4 (cacheado para siempre: un formulario presentado no cambia)."""
    os.makedirs(f"{CACHE}/form4", exist_ok=True)
    ruta = f"{CACHE}/form4/{acc}.json"
    d = leer(ruta)
    if d is not None and "do" in d:
        return d
    crudo = re.sub(r"^xslF345X\d+/", "", doc)       # el XML original, no la versión maquetada
    xml = pedir(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{crudo}",
                cab, "text", intentos=2)
    time.sleep(0.12)                              # la SEC permite 10 consultas/segundo
    if xml is None:
        return None
    try:
        r = ET.fromstring(xml)
    except ET.ParseError:
        return None
    quien, dir_o_ejec = [], False
    for o in r.findall("reportingOwner"):
        rel = o.find("reportingOwnerRelationship")
        if rel is not None and (si(rel.findtext("isDirector")) or si(rel.findtext("isOfficer"))):
            dir_o_ejec = True
        cargo = (rel.findtext("officerTitle") or "").strip() if rel is not None else ""
        if not cargo and rel is not None:
            cargo = ("Consejero" if si(rel.findtext("isDirector")) else
                     "Accionista >10 %" if si(rel.findtext("isTenPercentOwner")) else "")
        quien.append({"n": (o.findtext("reportingOwnerId/rptOwnerName") or "").strip().title(), "c": cargo})
    compra = venta = 0.0
    for t in r.findall("nonDerivativeTable/nonDerivativeTransaction"):
        cod = t.findtext("transactionCoding/transactionCode")
        acc_ = num(t.findtext("transactionAmounts/transactionShares/value")) or 0
        px = num(t.findtext("transactionAmounts/transactionPricePerShare/value")) or 0
        if cod == "P":                            # compra en el mercado con su dinero
            compra += acc_ * px
        elif cod == "S":                          # venta en el mercado
            venta += acc_ * px
    # "do": lo presenta un consejero o ejecutivo. Si solo es un accionista >10 % suele
    # ser un fondo o la propia matriz moviendo acciones: no es un directivo apostando.
    d = {"q": quien, "c": round(compra), "v": round(venta), "do": dir_o_ejec}
    guardar(ruta, d)
    return d


# Señales de alarma en las presentaciones a la SEC (2026-10-06, para el «¿por qué sí y por
# qué no?» de la ficha). Del formulario 8-K solo estos apartados; el resto (resultados,
# fichajes, acuerdos) es rutina. NO se usa el 2.04 («adelanta una deuda»): leídos los
# informes, empresas sanas lo presentan para devolver bonos antes de tiempo (Albertsons,
# Moog). El 3.01 y el 4.01 se leen (ver mala_8k) porque casi siempre son rutina.
ITEMS_8K = {"4.02": "rehace",      # sus cuentas anteriores ya no son fiables (las rehace)
            "1.03": "quiebra",     # quiebra o administración judicial
            "3.01": "cotiza",      # aviso de exclusión de bolsa / incumple las normas (o cambio de bolsa)
            "4.01": "auditor",     # cambio de auditor
            "2.06": "deterioro"}   # deterioro importante de activos
FORMAS_TARDE = ("NT 10-K", "NT 10-Q", "NT 10-K/A", "NT 10-Q/A")   # avisa de que presentará tarde sus cuentas
# Lo que convierte un 3.01 o un 4.01 en mala señal. Sin esto, un simple cambio de bolsa
# (NYSE -> Nasdaq) o de auditor saldría «en contra». No se busca «disagreement»: la frase
# estándar es «there were no disagreements».
MALO_8K = {"3.01": ("not in compliance", "noncompliance", "non-compliance", "deficiency", "minimum bid",
                    "regain compliance", "delisting determination", "below the minimum"),
           "4.01": ("resign", "declined to stand", "material weakness")}


def mala_8k(cik, a, item, cab):
    """¿El 8-K cuenta un problema? Lee el texto del apartado (cacheado para siempre)."""
    os.makedirs(f"{CACHE}/8k", exist_ok=True)
    ruta = f"{CACHE}/8k/{a[1]}_{item}.json"
    d = leer(ruta)
    if d is not None:
        return d["mal"]
    txt = pedir(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{a[1].replace('-', '')}/{a[2]}", cab, "text", intentos=2)
    time.sleep(0.12)
    if txt is None:
        return True                       # sin poder leerlo, se avisa (la app enlaza el informe)
    txt = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", txt))).lower()
    i = txt.find("item " + item)
    tramo = txt[i + 160: i + 3000] if i >= 0 else txt   # sin el título del apartado («failure to satisfy...»)
    mal = any(k in tramo for k in MALO_8K[item])
    guardar(ruta, {"mal": mal})
    return mal


def alertas_sub(rec, desde):
    """{tipo: [[fecha, nº de registro, documento], ...]} de las presentaciones desde `desde`."""
    out = {}
    items = rec.get("items") or []
    for i, forma in enumerate(rec.get("form", [])):
        f = rec["filingDate"][i]
        if f < desde:
            continue
        tipos = []
        if forma in FORMAS_TARDE:
            tipos.append("tarde")
        elif forma in ("8-K", "8-K/A") and i < len(items):
            tipos += [ITEMS_8K[x.strip()] for x in (items[i] or "").split(",") if x.strip() in ITEMS_8K]
        for k in dict.fromkeys(tipos):
            L = out.setdefault(k, [])
            if len(L) < 3:
                L.append([f, rec["accessionNumber"][i], rec["primaryDocument"][i]])
    return out


def frames_sec(cab, tax, tag, unidad, instante, n):
    """La API «frames» de la SEC trae en UNA consulta el dato de todas las empresas en un
    trimestre natural. Devuelve {cik: {(año, trimestre): valor}} de los últimos n trimestres
    (sin contar el actual, que aún no tiene datos). Los datos de periodo (no instante) salen
    del último informe presentado: el trimestre de hace un año viene ya ajustado por splits."""
    out, y, q = {}, HOY.year, (HOY.month - 1) // 3 + 1
    for _ in range(n):
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        j = pedir(f"https://data.sec.gov/api/xbrl/frames/{tax}/{tag}/{unidad}/CY{y}Q{q}{'I' if instante else ''}.json",
                  cab, timeout=90, intentos=2)
        time.sleep(0.12)
        for d in (j or {}).get("data", []):
            if isinstance(d.get("val"), (int, float)):
                out.setdefault(int(d["cik"]), {})[(y, q)] = d["val"]
    return out


def datos_sec(empresas, dias=90):
    """Todo lo que sale de la SEC: alarmas en sus presentaciones (2 años), acciones emitidas
    o recompradas en un año, impuestos inciertos y, para las que más interesan (castigadas y
    top de potencial), compras y ventas de directivos de los últimos `dias`.
    Devuelve cuántas empresas se han revisado."""
    cab = cab_sec()
    if not cab:
        log("  SEC: sin contacto en", SEC_CONTACTO, "-> se salta")
        return 0
    tick = pedir("https://www.sec.gov/files/company_tickers.json", cab, timeout=60) or {}
    cik = {v["ticker"].replace(".", "-"): v["cik_str"] for v in tick.values()}
    for e in empresas:
        if cik.get(e["t"]):
            e["cik"] = cik[e["t"]]

    # Acciones en circulación (diluidas, media del trimestre) frente al mismo trimestre de hace
    # un año: >0 emite acciones (diluye al accionista), <0 recompra.
    acc = frames_sec(cab, "us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding", "shares", False, 7)
    # Impuestos inciertos: lo que la empresa dedujo y Hacienda podría no aceptarle (y cobrarle).
    utb = frames_sec(cab, "us-gaap", "UnrecognizedTaxBenefits", "USD", True, 6)
    for e in empresas:
        c = int(e.get("cik") or 0)
        d = acc.get(c) or {}
        for yq in sorted(d, reverse=True):
            prev = d.get((yq[0] - 1, yq[1]))
            if prev and prev > 0 and d[yq] > 0 and 0.1 < d[yq] / prev < 10:   # fuera de eso: error de unidades
                e["dil"] = d[yq] / prev - 1
                break
        u = utb.get(c) or {}
        if u:
            e["utb"] = u[max(u)]
    log(f"  frames: acciones de {len(acc)} empresas, impuestos inciertos de {len(utb)}")

    desde_al = (HOY - dt.timedelta(days=730)).isoformat()
    desde = (HOY - dt.timedelta(days=dias)).isoformat()
    obj = {e["t"] for e in empresas if e.get("cast")}
    obj |= {e["t"] for e in sorted([e for e in empresas if e.get("sc") is not None and not e.get("cast")],
                                   key=lambda e: -e["sc"])[:200]}
    n = 0
    for e in empresas:
        c = e.get("cik")
        if not c:
            continue
        j = pedir(f"https://data.sec.gov/submissions/CIK{int(c):010d}.json", cab, timeout=40)
        time.sleep(0.12)
        if not j:
            continue
        n += 1
        rec = j.get("filings", {}).get("recent", {})
        al = alertas_sub(rec, desde_al)
        for k, item in (("cotiza", "3.01"), ("auditor", "4.01")):
            if k in al:
                al[k] = [a for a in al[k] if mala_8k(c, a, item, cab)]
                if not al[k]:
                    del al[k]
        if al:
            e["sec"] = al
        if e["t"] not in obj:
            continue
        compras, ventas, compradores, ult = 0.0, 0.0, {}, None
        for i, forma in enumerate(rec.get("form", [])):
            if forma != "4" or rec["filingDate"][i] < desde:
                continue
            d = form4(c, rec["accessionNumber"][i], rec["primaryDocument"][i], cab)
            if not d or not d["do"]:
                continue
            ventas += d["v"]
            if d["c"] > 0:
                compras += d["c"]
                ult = max(ult or "", rec["filingDate"][i])
                for q in d["q"]:
                    compradores[q["n"]] = q["c"]
        if compras or ventas:
            e["ins"] = {"c": round(compras), "v": round(ventas), "n": len(compradores), "ult": ult,
                        "quien": [{"n": a, "c": b} for a, b in list(compradores.items())[:4]]}
    return n


# ── interés (Wikipedia) y noticias (Google News) ─────────────────────────────
def vistas_wiki(titulo):
    fin = HOY - dt.timedelta(days=1)
    ini = fin - dt.timedelta(days=120)
    url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
           f"{urllib.parse.quote(titulo.replace(' ', '_'), safe='')}/daily/{ini:%Y%m%d}00/{fin:%Y%m%d}00")
    j = pedir(url, BOT, intentos=2)
    v = [x["views"] for x in (j or {}).get("items", [])]
    if len(v) < 60:
        return None
    rec, prev = st.mean(v[-30:]), st.mean(v[:-30]) or 1
    return {"dia": round(rec), "tend": rec / prev - 1}


def noticias(q, n=5, es=False, dias=30):
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(f"{q} when:{dias}d")
           + ("&hl=es-419&gl=US&ceid=US:es-419" if es else "&hl=en-US&gl=US&ceid=US:en"))
    x = pedir(url, NAV, "text", intentos=2)
    if not x:
        return [], 0
    try:
        items = ET.fromstring(x).findall("./channel/item")
    except ET.ParseError:
        return [], 0
    out = []
    for it in items:
        ti = it.findtext("title") or ""
        fu = it.findtext("source") or ""
        if fu and ti.endswith(" - " + fu):
            ti = ti[: -len(fu) - 3]
        try:
            f = dt.datetime.strptime(it.findtext("pubDate"), "%a, %d %b %Y %H:%M:%S %Z")
        except (TypeError, ValueError):
            continue
        out.append({"ti": ti, "fu": fu, "url": it.findtext("link"), "f": f.strftime("%Y-%m-%dT%H:%MZ")})
    out.sort(key=lambda x: x["f"], reverse=True)
    time.sleep(1.0)
    return out[:n], len(items)


# ── temas y puntuación ───────────────────────────────────────────────────────
def tema_de(e):
    sub = e.get("sub") or ""
    yind = (e.get("yind") or "").lower()
    for k, T in TEMAS.items():
        if sub in T["gics"] or (yind and any(s in yind for s in T["yind"])):
            return k
    n = f" {(e.get('n') or '').lower()} "
    if re.search(r"\b(ai|a\.i\.)\b", n):          # "SoundHound AI", "C3.ai" (con límites de palabra)
        return "ia"
    for k, T in TEMAS.items():
        if any(w in n for w in T["nombre_kw"]):
            return k
    return "otros"


def rango_pct(valores):
    """Percentil (0-100) de cada valor dentro de la lista; None se queda en None."""
    v = sorted(x for x in valores if x is not None)
    if not v:
        return lambda x: None

    def f(x):
        if x is None:
            return None
        return 100.0 * bisect.bisect_left(v, x) / max(1, len(v) - 1)
    return f


def colchon(e):
    """Caja frente a deuda; sin deuda cuenta como muy sólido."""
    if e.get("caja") is None:
        return None
    return e["caja"] / max(e.get("deuda") or 0, 1e6)


def puntuar(empresas):
    """Ranking de 'pequeñas con potencial'. Es una REGLA TRANSPARENTE, no una
    predicción: la app enseña cada componente y avisa de que no está probada."""
    eleg = [e for e in empresas
            if e["idx"] in ("600", "400", "IPO") and e.get("mc") and e["mc"] <= 10e9
            and (e.get("dv") or 0) >= 2e6 and e.get("cr") is not None and (e.get("rev") or 0) >= 20e6
            and e.get("sector") not in ("Finanzas", "Inmobiliario")]
    # 2026-10-05: el "beneficio" usa el margen OPERATIVO (antes el neto). El neto se infla con
    # cosas puntuales (Duolingo: 36 % neto por una devolución de impuestos, 12 % operativo).
    # Medido 2012-2025 (backtest/medir.py), ni una ni otra versión se distinguen del azar.
    for e in eleg:
        e["mben"] = e.get("mo") if e.get("mo") is not None else e.get("mn")
    rc = rango_pct([e["cr"] for e in eleg])
    rmb = rango_pct([e.get("mb") for e in eleg])
    rmn = rango_pct([e.get("mben") for e in eleg])
    rcol = rango_pct([colchon(e) for e in eleg])
    rint = rango_pct([(e.get("wv") or {}).get("tend") for e in eleg])
    pesos = {"crec": .35, "margen": .15, "benef": .15, "solidez": .15, "tema": .10, "interes": .10}
    for e in eleg:
        sol = rcol(colchon(e))
        sol = 50 if sol is None else sol
        if e.get("run") is not None and e["run"] < 6:
            sol = min(sol, 15)                 # quema la caja en menos de año y medio
        comp = {
            "crec": rc(e["cr"]),
            "margen": rmb(e["mb"]) if e.get("mb") is not None else 50,
            "benef": rmn(e["mben"]) if e.get("mben") is not None else 50,
            "solidez": sol,
            "tema": 100 if e["tema"] != "otros" else 0,
            "interes": rint(e["wv"]["tend"]) if e.get("wv") else 50,
        }
        e["sc"] = round(sum(comp[k] * p for k, p in pesos.items()))
        e["comp"] = {k: round(v) for k, v in comp.items()}
    return len(eleg)


def salud_castigada(e):
    """Señales de que una caída NO es un hundimiento del negocio (0-4)."""
    s = []
    if e.get("cr") is not None and e["cr"] >= 0:
        s.append("ventas crecen")
    if e.get("mn") is not None and e["mn"] > 0:
        s.append("gana dinero")
    c = colchon(e)
    if c is not None and c >= 0.2:
        s.append("tiene caja")
    if e.get("r1m") is not None and e["r1m"] > -0.10:
        s.append("ha dejado de desplomarse")
    return s


# ── modos ────────────────────────────────────────────────────────────────────
def ahora():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def modo_rapido(salida):
    log("calendario de salidas a bolsa")
    prox, reg = calendario()
    ipos = leer(f"{salida}/ipos.json", {}) or {}
    ipos.update(proximas=prox, registradas=reg, medido=MEDIDO["ipo"], act=ahora())
    guardar(f"{salida}/ipos.json", ipos)

    log("noticias")
    lista = (leer(f"{salida}/empresas.json", {}) or {}).get("e", [])
    top = sorted([e for e in lista if e.get("sc") is not None], key=lambda e: -e["sc"])[:30]
    cast = sorted([e for e in lista if e.get("cast")],
                  key=lambda e: (-len(e.get("salud", [])), e.get("dd", 0)))[:15]
    rec = [e for e in lista if e["idx"] == "IPO"][:15]
    obj = {"general": noticias('IPO (Nasdaq OR NYSE)', 8, dias=3)[0],     # "IPO" a secas trae bolsas de todo el mundo
           "general_es": noticias("bolsa de valores Wall Street", 6, es=True, dias=3)[0],
           "temas": {k: noticias(T["news"], 4, dias=7)[0] for k, T in TEMAS.items()},
           "emp": {}}
    for x in prox:
        if x["n"] and not x["spac"]:
            obj["emp"][x["t"] or x["n"]] = noticias(f'"{nombre_corto(x["n"])}" IPO', 4)[0]
    for e in top + cast + rec:
        if e["t"] not in obj["emp"]:
            obj["emp"][e["t"]] = noticias(f'"{nombre_corto(e["n"])}" stock', 4)[0]
    obj["act"] = ahora()
    guardar(f"{salida}/noticias.json", obj)
    log("rapido OK:", len(prox), "proximas,", len(reg), "registradas,", len(obj["emp"]), "empresas con noticias")


def modo_completo(salida):
    t0 = time.time()
    log("universo: listas S&P y salidas a bolsa recientes")
    uni = []
    for idx, url in (("500", "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"),
                     ("400", "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"),
                     ("600", "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies")):
        L = lista_sp(url, idx)
        log(f"  S&P {idx}: {len(L)}")
        uni += L
    # Wikipedia a veces tiene una empresa en dos listas mientras cambia de índice (CORT y
    # EAT salían dos veces el 2026-10-04): se queda la primera (la del índice mayor).
    vistos, unicos = set(), []
    for e in uni:
        if e["t"] not in vistos:
            vistos.add(e["t"])
            unicos.append(e)
    uni = unicos
    recientes = [x for x in ipos_recientes() if x["t"] not in vistos]
    log(f"  salidas a bolsa recientes (18 meses, sin SPACs): {len(recientes)}")
    uni += recientes
    n_max = int(os.environ.get("RB_MAX") or 0)
    if n_max:                                   # solo para pruebas: una muestra de todo tipo de valores
        uni = uni[:: max(1, len(uni) // n_max)]
        log(f"  PRUEBA: solo {len(uni)} valores")

    log(f"precios y fundamentales de {len(uni)} valores + SPY")
    crumb()
    spy = precios("SPY")
    hist, empresas = {}, []
    for i, e in enumerate(uni):
        if i and i % 200 == 0:
            log(f"  {i}/{len(uni)}")
        p = precios(e["t"], "2y" if e["idx"] == "IPO" else "1y")
        time.sleep(0.12)
        if not p:
            continue
        e.update(fundamentales(e["t"]))
        time.sleep(0.12)
        if (e.get("yind") or "") == "Shell Companies":
            continue                                  # SPAC disfrazado
        e["sector"] = SECTOR_ES.get(e.get("sector") or e.get("ysector"), e.get("sector") or e.get("ysector") or "—")
        e.update({k: p[k] for k in ("px", "r1d", "r1m", "r3m", "r6m", "r1a", "hi", "lo", "dd", "dv")})
        e["fin"] = e["sector"] in ("Finanzas", "Inmobiliario")   # bancos/REIT: margen y ventas no comparables
        if e["idx"] == "IPO":
            ajuste, ncs = 1.0, 0
            ini = dt.datetime.fromisoformat(e["ipo_fecha"]).timestamp() - 86400
            for fecha, k in p["splits"]:
                if fecha > ini:
                    ajuste *= k
                    ncs += k > 1                       # contrasplit: junta N acciones en 1
            e["ses"] = p["ses"]
            if ajuste != 1:
                e["aj"], e["ncs"] = ajuste, ncs
            if e.get("ipo_px"):
                e["ipo_px_aj"] = e["ipo_px"] * ajuste    # precio de salida en las acciones de HOY
                e["r_ipo"] = p["px"] / e["ipo_px_aj"] - 1
            e["r_dia1"] = p["px"] / p["o1"] - 1 if p["o1"] else None
            e["grande"] = (e.get("ipo_usd") or 0) >= 1e8
            e["lockup"] = (dt.date.fromisoformat(e["ipo_fecha"]) + dt.timedelta(days=180)).isoformat()
        e["tema"] = tema_de(e)
        hist[e["t"]] = {"d0": p["d0"], "d1": p["d1"], "w": p["sem"]}
        empresas.append(e)
    log(f"  con precio: {len(empresas)}  con fundamentales: {sum(1 for e in empresas if e.get('cr') is not None)}")

    log("interés en Wikipedia (empresas y temas)")
    for e in empresas:
        if e.get("wiki"):
            e["wv"] = vistas_wiki(e["wiki"])
            time.sleep(0.05)
    temas = {}
    for k, T in TEMAS.items():
        vs = [v for v in (vistas_wiki(a) for a in T["wiki"]) if v]
        _, n7 = noticias(T["news"], 1, dias=7)
        miembros = [e for e in empresas if e["tema"] == k]
        temas[k] = {"nombre": T["nombre"], "ico": T["ico"],
                    "vistas_dia": sum(v["dia"] for v in vs),
                    "tend": st.mean(v["tend"] for v in vs) if vs else None,
                    "noticias_7d": n7, "n": len(miembros),
                    "r1m": st.median([e["r1m"] for e in miembros if e.get("r1m") is not None] or [0]),
                    "r1a": st.median([e["r1a"] for e in miembros if e.get("r1a") is not None] or [0])}

    log("puntuación")
    n_eleg = puntuar(empresas)
    for e in empresas:
        if e["idx"] in ("500", "400", "600") and e.get("dd") is not None and e["dd"] <= -0.30:
            e["cast"] = True
            e["salud"] = salud_castigada(e)

    log("SEC: alarmas en sus presentaciones, acciones, impuestos y directivos")
    n_sec = datos_sec(empresas)
    log(f"  revisadas {n_sec}, con alarmas: {sum(1 for e in empresas if e.get('sec'))}, "
        f"con compras de directivos: {sum(1 for e in empresas if (e.get('ins') or {}).get('c'))}")

    log("fondos índice y S&P 500 día a día")
    lista_fondos = fondos()
    ind = indice_diario()
    guardar(f"{salida}/fondos.json", {"act": ahora(), "f": lista_fondos,
                                      "caidas_spy": caidas_diarias(ind) if ind else []})
    if ind:
        guardar(f"{salida}/indice.json", {k: x for k, x in ind.items() if k != "dias"})
    log(f"  fondos: {len(lista_fondos)}  índice diario: {len(ind['v']) if ind else 0} sesiones")

    # Valoración típica de cada sector, para que la ficha diga si algo es caro o barato
    # frente a sus parecidas (mediana; el PER y el EV/EBITDA solo cuando son positivos).
    med_sector = {}
    for sec in {e["sector"] for e in empresas}:
        g = [e for e in empresas if e["sector"] == sec]
        m = {}
        for k, pos in (("pe", True), ("fpe", True), ("ps", False), ("eveb", True), ("div", False)):
            vals = [e[k] for e in g if isinstance(e.get(k), (int, float)) and math.isfinite(e[k]) and (e[k] > 0 or not pos)]
            if len(vals) >= 8:
                m[k] = r4(st.median(vals))
        if m:
            med_sector[sec] = m

    CAMPOS = ["t", "n", "idx", "sector", "tema", "fin", "px", "mc", "r1d", "r1m", "r3m", "r6m", "r1a",
              "hi", "dd", "cr", "cr_a", "cr_q", "irreg", "rev", "mb", "mo", "mn", "mfcf", "caja", "deuda", "run",
              "pe", "fpe", "ps", "eveb", "div", "beta", "corto",
              "wv", "sc", "comp", "cast", "salud", "ipo_fecha", "ipo_px", "ipo_px_aj", "aj", "ncs", "ipo_usd",
              "bolsa", "ses", "r_ipo", "r_dia1", "grande", "lockup", "cik", "ins",
              "ebitda", "de", "cur", "roe", "rec", "nan", "obj", "dil", "utb", "sec"]
    salida_e = []
    for e in empresas:
        o = {}
        for k in CAMPOS:
            v = e.get(k)
            if v is None or v is False or v == "" or v == []:     # lo que falta o es "no" no se escribe
                continue
            if isinstance(v, float):
                v = r4(v)
            elif isinstance(v, dict):
                v = {a: (r4(b) if isinstance(b, float) else b) for a, b in v.items()}
            o[k] = v
        salida_e.append(o)

    guardar(f"{salida}/empresas.json", {"act": ahora(), "e": salida_e})
    guardar(f"{salida}/hist.json", hist)
    guardar(f"{salida}/resumen.json", {
        "act": ahora(), "n_total": len(empresas), "n_puntuadas": n_eleg, "n_sec": n_sec,
        "spy": {k: r4(spy[k]) for k in ("px", "r1d", "r1m", "r3m", "r6m", "r1a", "dd")} if spy else None,
        "spy_hist": {"d0": spy["d0"], "d1": spy["d1"], "w": spy["sem"]} if spy else None,
        "temas": {k: {a: (r4(b) if isinstance(b, float) else b) for a, b in v.items()} for k, v in temas.items()},
        "med_sector": med_sector,
        "medido": MEDIDO, "duracion_min": round((time.time() - t0) / 60, 1)})
    log(f"completo OK: {len(empresas)} empresas, {n_eleg} puntuadas, {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("modo", choices=["completo", "rapido"])
    ap.add_argument("--salida", required=True)
    a = ap.parse_args()
    os.makedirs(a.salida, exist_ok=True)
    os.makedirs(f"{CACHE}/nasdaq", exist_ok=True)
    if a.modo == "completo":
        modo_completo(a.salida)
    modo_rapido(a.salida)          # el completo también refresca calendario y noticias
