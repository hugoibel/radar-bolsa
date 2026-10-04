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
import os
import re
import statistics as st
import time
import urllib.parse
import xml.etree.ElementTree as ET

import requests

CACHE = "/root/radar-bolsa-cache"
NAV = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
       "Accept": "application/json, text/plain, */*"}
# Wikimedia exige un User-Agent que identifique al programa. (La SEC exige además
# un email real de contacto: por eso los fundamentales salen de Yahoo y no de ella.)
BOT = {"User-Agent": "RadarBolsa/1.0 (+https://hugoibel.github.io/radar-bolsa/)"}

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
    "ia": {"nombre": "IA y chips", "ico": "🤖",
           "gics": ["Semiconductors", "Semiconductor Materials & Equipment", "Systems Software",
                    "Application Software", "Internet Services & Infrastructure",
                    "Technology Hardware, Storage & Peripherals", "Electronic Components",
                    "Electronic Equipment & Instruments", "Communications Equipment",
                    "Electronic Manufacturing Services"],
           "yind": ["semiconductor", "software", "computer hardware", "information technology",
                    "electronic components", "scientific & technical instruments", "communication equipment"],
           "nombre_kw": ["quantum", "semiconductor", "artificial intelligence", " ai ", "data center", "robot"],
           "wiki": ["Artificial intelligence", "Large language model", "Data center", "Quantum computing"],
           "news": "AI stocks"},
    "energia": {"nombre": "Energía y red eléctrica", "ico": "⚡",
                "gics": ["Electric Utilities", "Independent Power Producers & Energy Traders",
                         "Renewable Electricity", "Heavy Electrical Equipment",
                         "Electrical Components & Equipment", "Multi-Utilities", "Coal & Consumable Fuels"],
                "yind": ["utilities", "uranium", "solar", "electrical equipment"],
                "nombre_kw": ["nuclear", "uranium", "solar", "energy", "power"],
                "wiki": ["Nuclear power", "Small modular reactor", "Electrical grid", "Solar power"],
                "news": "nuclear energy stocks"},
    "salud": {"nombre": "Salud y biotecnología", "ico": "🧬",
              "gics": ["Biotechnology", "Pharmaceuticals", "Health Care Equipment",
                       "Life Sciences Tools & Services", "Health Care Technology", "Health Care Supplies"],
              "yind": ["biotechnology", "drug manufacturers", "medical", "diagnostics", "health information"],
              "nombre_kw": ["therapeutics", "bio", "pharma", "medical", "health"],
              "wiki": ["Biotechnology", "GLP-1 receptor agonist", "CRISPR gene editing", "Cancer immunotherapy"],
              "news": "biotech stocks"},
    "defensa": {"nombre": "Defensa, espacio y ciberseguridad", "ico": "🛡️",
                "gics": ["Aerospace & Defense"],
                "yind": ["aerospace & defense", "security & protection"],
                "nombre_kw": ["cyber", "defense", "space", "drone", "aerospace", "rocket"],
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
           f"?modules=financialData,price,summaryProfile&crumb={CRUMB['v']}")
    j = pedir(url, intentos=2)
    if j is None:                 # crumb caducado: se renueva una vez
        crumb()
        j = pedir(url.rsplit("&crumb=", 1)[0] + f"&crumb={CRUMB['v']}", intentos=1)
    try:
        r = j["quoteSummary"]["result"][0]
    except (TypeError, KeyError, IndexError):
        return {}
    fd, pr, sp = r.get("financialData") or {}, r.get("price") or {}, r.get("summaryProfile") or {}

    def v(d, k):
        x = d.get(k)
        return x.get("raw") if isinstance(x, dict) else None

    f = {"cr": v(fd, "revenueGrowth"), "mb": v(fd, "grossMargins"), "mn": v(fd, "profitMargins"),
         "caja": v(fd, "totalCash"), "deuda": v(fd, "totalDebt"), "rev": v(fd, "totalRevenue"),
         "fcf": v(fd, "freeCashflow"), "mc": v(pr, "marketCap"),
         "ysector": sp.get("sector"), "yind": sp.get("industry")}
    if f["fcf"] is not None and f["fcf"] < 0 and f["caja"]:
        f["run"] = f["caja"] / -f["fcf"] * 4     # trimestres de caja al ritmo de quema actual
    return {k: x for k, x in f.items() if x is not None}


def precios(t, rango="1y"):
    j = pedir(f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?range={rango}&interval=1d&events=split")
    try:
        r = j["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
        ts = r.get("timestamp") or []
    except (TypeError, KeyError, IndexError):
        return None
    fil = [(ts[i], q["open"][i], q["close"][i], q["volume"][i] or 0)
           for i in range(len(ts)) if q["close"][i]]
    if len(fil) < 3:
        return None
    c = [x[2] for x in fil]

    def ret(n):
        return c[-1] / c[-1 - n] - 1 if len(c) > n else None

    ult = c[-252:]
    idx = list(range(len(c) - 1, -1, -5))[::-1]
    splits = []
    for s in ((r.get("events") or {}).get("splits") or {}).values():
        if s.get("numerator") and s.get("denominator"):
            splits.append((s["date"], s["denominator"] / s["numerator"]))
    return {
        "px": c[-1], "r1d": ret(1), "r1m": ret(21), "r3m": ret(63), "r6m": ret(126), "r1a": ret(251),
        "hi": max(ult), "lo": min(ult), "dd": c[-1] / max(ult) - 1,
        "dv": st.mean(x[2] * x[3] for x in fil[-20:]),
        "ses": len(fil), "o1": fil[0][1],
        "sem": [r4(c[i]) for i in idx],
        "d0": time.strftime("%Y-%m-%d", time.gmtime(fil[idx[0]][0])),
        "d1": time.strftime("%Y-%m-%d", time.gmtime(fil[-1][0])),
        "splits": splits,
    }


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
    rc = rango_pct([e["cr"] for e in eleg])
    rmb = rango_pct([e.get("mb") for e in eleg])
    rmn = rango_pct([e.get("mn") for e in eleg])
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
            "benef": rmn(e["mn"]) if e.get("mn") is not None else 50,
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
    obj = {"general": noticias("IPO", 8, dias=3)[0],
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
    vistos = {e["t"] for e in uni}
    recientes = [x for x in ipos_recientes() if x["t"] not in vistos]
    log(f"  salidas a bolsa recientes (18 meses, sin SPACs): {len(recientes)}")
    uni += recientes

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
        if e["idx"] == "IPO":
            ajuste = 1.0
            ini = dt.datetime.fromisoformat(e["ipo_fecha"]).timestamp() - 86400
            for fecha, k in p["splits"]:
                if fecha > ini:
                    ajuste *= k
            e["ses"] = p["ses"]
            if e.get("ipo_px"):
                e["ipo_px_aj"] = e["ipo_px"] * ajuste
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

    CAMPOS = ["t", "n", "idx", "sector", "sub", "yind", "tema", "px", "mc", "r1d", "r1m", "r3m", "r6m", "r1a",
              "hi", "lo", "dd", "dv", "cr", "rev", "mb", "mn", "caja", "deuda", "fcf", "run",
              "wv", "sc", "comp", "cast", "salud", "ipo_fecha", "ipo_px", "ipo_px_aj", "ipo_usd", "bolsa",
              "ses", "r_ipo", "r_dia1", "grande", "lockup", "wiki"]
    salida_e = []
    for e in empresas:
        o = {}
        for k in CAMPOS:
            v = e.get(k)
            if v is None or v == "" or v == []:
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
        "act": ahora(), "n_total": len(empresas), "n_puntuadas": n_eleg,
        "spy": {k: r4(spy[k]) for k in ("px", "r1d", "r1m", "r3m", "r6m", "r1a", "dd")} if spy else None,
        "spy_hist": {"d0": spy["d0"], "d1": spy["d1"], "w": spy["sem"]} if spy else None,
        "temas": {k: {a: (r4(b) if isinstance(b, float) else b) for a, b in v.items()} for k, v in temas.items()},
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
