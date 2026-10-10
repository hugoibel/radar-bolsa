#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Radar Bolsa · Hallazgos (2026-10-10, pedido del usuario: «extremadamente importante, 24/7»).

Vigila las noticias de MINERÍA (exploración, descubrimientos, nuevas minas) y de TECNOLOGÍA
(aprobaciones, ensayos, avances, contratos) de empresas que cotizan, y manda un EMAIL al momento
cuando hay algo importante. Corre cada 5 minutos en el VPS (cron, con su propio cerrojo).

Fuentes (probadas el 2026-10-10 desde el VPS):
  - TMX Newsfile: minería y tecnología (por donde publican casi todas las mineras pequeñas de
    Canadá y EE. UU.). Solo da los 10 últimos -> de ahí el cada-5-minutos.
  - PR Newswire: minería, biotecnología y defensa.
  - Google News (cada 15 min): 11 búsquedas que recogen lo de GlobeNewswire, Business Wire, etc.
    (GlobeNewswire y Accesswire bloquean al VPS: 403).

Cada noticia: empresa y tickers, tipo (descubrimiento, perforación, recursos, estudio, permiso,
producción, compra, financiación, problema | aprobación, ensayo, avance, contrato), minerales o
campo técnico, cifras clave (mejor tramo de perforación en g·m o %·m, VAN/TIR de un estudio,
importe de un contrato) y si es IMPORTANTE (reglas fijas, abajo). Publica data/hallazgos.json
(30 días) en el repo de la app.

    python3 hallazgos.py                # lo que corre el cron
    python3 hallazgos.py --sin-email --sin-git   # para probar
    python3 hallazgos.py --test         # casos de las reglas, sin red
"""
import datetime as dt
try:
    import fcntl
except ImportError:          # Windows (solo para probar en el PC)
    fcntl = None
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET

REPO = os.environ.get("RB_REPO", "/root/radar-bolsa-repo")
CACHE = os.environ.get("RB_CACHE", "/root/radar-bolsa-cache")
SALIDA = f"{REPO}/data/hallazgos.json"
ESTADO = f"{CACHE}/hallazgos_estado.json"
CERROJO = "/tmp/hallazgos.lock"
CERROJO_REPO = "/tmp/radar_bolsa.lock"          # el mismo que actualizar.sh: no tocar git a la vez
NAV = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/124 Safari/537.36"}
DIAS = 30                 # lo que se publica
MAX_EMAILS_DIA = 20
APP = "https://hugoibel.github.io/radar-bolsa/#hallazgos"

FEEDS = [
    ("Newsfile", "min", "https://feeds.newsfilecorp.com/industry/mining-metals"),
    ("Newsfile", "tec", "https://feeds.newsfilecorp.com/industry/technology"),
    ("PR Newswire", "min", "https://www.prnewswire.com/rss/heavy-industry-manufacturing-latest-news/mining-metals-list.rss"),
    ("PR Newswire", "tec", "https://www.prnewswire.com/rss/health-latest-news/biotechnology-list.rss"),
    ("PR Newswire", "tec", "https://www.prnewswire.com/rss/heavy-industry-manufacturing-latest-news/aerospace-defense-list.rss"),
]
GOOGLE = [
    ("min", '"g/t" (intersects OR intercepts OR drills OR drilled OR assays)'),
    ("min", '"drill results" OR "drilling results" OR "drill program" (gold OR copper OR silver OR lithium OR uranium)'),
    ("min", 'discovery (gold OR copper OR lithium OR uranium OR silver OR "rare earth") (TSXV OR TSX OR CSE OR NYSE OR NASDAQ OR OTCQB)'),
    ("min", '"mineral resource estimate" OR "maiden resource" OR "resource update"'),
    ("min", '"feasibility study" OR "preliminary economic assessment" OR "pre-feasibility" NPV'),
    ("min", '("rare earth" OR "critical minerals" OR antimony OR tungsten OR graphite) (drilling OR discovery OR resource OR permit)'),
    ("tec", '"FDA approves" OR "FDA approval" OR "receives FDA" (NASDAQ OR NYSE)'),
    ("tec", '"Phase 3" ("met primary endpoint" OR "topline results" OR "statistically significant")'),
    ("tec", 'breakthrough (battery OR "solid-state" OR fusion OR quantum OR semiconductor OR superconductor) (NASDAQ OR NYSE)'),
    ("tec", '("world record" OR "first-ever" OR "world\'s first") (battery OR chip OR quantum OR fusion OR solar OR reactor OR satellite)'),
    ("tec", '(awarded OR wins) contract (DoD OR "Department of Energy" OR NASA OR Army OR "Air Force") million (NASDAQ OR NYSE)'),
]

# ── reglas ────────────────────────────────────────────────────────────────────
BOLSAS = r"(TSX-?V|TSXV|TSX|CSE|CBOE CA|NEO|NYSE American|NYSE MKT|NYSE|NASDAQ|Nasdaq|OTCQX|OTCQB|OTC Pink|OTC|ASX|AIM|LSE|JSE|FSE|Frankfurt|XETRA)"
RE_TICKER = re.compile(BOLSAS + r"\s*:\s*\"?([A-Z][A-Z0-9]{0,5}(?:\.[A-Z]{1,2})?)\b")
SUFIJO_YAHOO = {"TSXV": ".V", "TSX-V": ".V", "TSX": ".TO", "CSE": ".CN", "NEO": ".NE", "CBOE CA": ".NE", "ASX": ".AX",
                "AIM": ".L", "LSE": ".L", "JSE": ".JO", "FSE": ".F", "Frankfurt": ".F", "XETRA": ".DE"}
PREFERIDA = ["NYSE", "NASDAQ", "Nasdaq", "NYSE American", "NYSE MKT", "TSX", "TSXV", "TSX-V", "CSE", "ASX", "OTCQX", "OTCQB", "OTC"]

EVENTOS_MIN = [   # en orden de prioridad: el primero que casa en el título manda
    ("problema", r"\b(suspend\w*|halt(?:s|ed)?|fatal\w*|accident|injunction|CCAA|bankrupt\w*|cease trade|default|insolven\w*|strike at)\b"),
    ("descubrimiento", r"\b(discover(?:s|y|ed|ies)?|new zone|new vein|new target)\b"),
    ("estudio", r"\b(preliminary economic assessment|PEA|pre-?feasibility|PFS|feasibility study|DFS|scoping study)\b"),
    ("recursos", r"\b(mineral resource|resource estimate|maiden resource|resource update|mineral reserve|reserves?|NI 43-101|S-K 1300)\b"),
    ("perforacion", r"\b(drill\w*|intersect\w*|intercept\w*|assays?|g/t|channel sampl\w*|trench\w*|grab sampl\w*)\b"),
    ("produccion", r"\b(commercial production|first (?:gold|silver|copper|concentrate) (?:pour|production|shipment)|construction decision|"
                   r"production decision|begins? production|start of production|pours? first|mill start)\b"),
    ("permiso", r"\b(permits?|permitting|record of decision|environmental approval|mining lease|exploitation licen[cs]e|"
                r"licen[cs]e (?:granted|approved))\b"),
    ("compra", r"\b(to acquire|acquires?|acquisition|merger|arrangement agreement|takeover|business combination|option agreement)\b"),
    ("financiacion", r"\b(private placement|bought deal|financing|flow-through|offering|warrants?|LIFE offering)\b"),
]
EVENTOS_TEC = [
    ("problema", r"\b(recall\w*|complete response letter|CRL|clinical hold|fail(?:s|ed)? to meet|did not meet|halts? (?:trial|study)|"
                 r"FDA rejects?|refuse to file)\b"),
    ("aprobacion", r"\b(FDA (?:approv\w*|grants?|clears?|clearance|accepts)|receives? (?:FDA |EMA |CE )?(?:approval|clearance|marking)|"
                   r"marketing authori[sz]ation|breakthrough (?:therapy |device )?designations?)\b"),
    ("ensayo", r"\b(phase (?:1|2|3|i|ii|iii|2b|3b)\b[^.]{0,80}(?:results?|data|topline|endpoint)|topline (?:results|data)|met (?:its )?primary endpoint)"),
    ("avance", r"\b(breakthrough|world record|first-ever|world'?s first|record[- ]breaking|achieves?|demonstrates?|milestone|prototype|unveils?)\b"),
    ("contrato", r"\b(awarded|wins?|secures?|selected by|contract|purchase order)\b"),
    ("patente", r"\b(patents?)\b"),
    ("compra", r"\b(to acquire|acquires?|acquisition|merger|business combination)\b"),
    ("financiacion", r"\b(private placement|financing|offering|warrants?)\b"),
]
MINERALES = [
    ("oro", r"\b(gold|Au|AuEq|g/t Au)\b"), ("plata", r"\b(silver|Ag|AgEq)\b"), ("cobre", r"\b(copper|Cu|CuEq)\b"),
    ("litio", r"\b(lithium|Li2O|spodumene)\b"), ("uranio", r"\b(uranium|U3O8)\b"),
    ("tierras raras", r"\b(rare earths?|REE|TREO|neodymium|NdPr|dysprosium)\b"), ("níquel", r"\b(nickel|Ni)\b"),
    ("cobalto", r"\b(cobalt)\b"), ("grafito", r"\b(graphite)\b"), ("zinc", r"\b(zinc|Zn)\b"),
    ("plomo", r"\b(Pb|lead[- ]zinc|zinc[- ]lead|silver[- ]lead|% lead|lead grades?)\b"),   # «lead agent» no es plomo
    ("antimonio", r"\b(antimony|Sb)\b"), ("wolframio", r"\b(tungsten|WO3)\b"), ("vanadio", r"\b(vanadium|V2O5)\b"),
    ("platino y paladio", r"\b(platinum|palladium|PGM|PGE)\b"), ("estaño", r"\b(tin|Sn)\b(?! can)"), ("potasa", r"\b(potash)\b"),
    ("helio", r"\b(helium)\b"), ("manganeso", r"\b(manganese)\b"), ("molibdeno", r"\b(molybdenum|Mo)\b"),
    ("hierro", r"\b(iron ore|magnetite|hematite)\b"), ("carbón", r"\b(coal|metallurgical coal)\b"),
]
CAMPOS = [
    ("biotecnología", r"\b(FDA|clinical|phase (?:1|2|3|i|ii|iii)|therapy|therapeutic|drug|oncology|vaccine|antibody|patients?)\b"),
    ("chips", r"\b(semiconductor|chip|wafer|GPU|ASIC|photonic)\b"),
    ("baterías", r"\b(batter(?:y|ies)|solid-state|lithium-ion|energy storage|anode|cathode)\b"),
    ("nuclear y fusión", r"\b(nuclear|fusion|SMR|reactor|fission)\b"),
    ("cuántica", r"\b(quantum)\b"),
    ("inteligencia artificial", r"\b(AI|artificial intelligence|machine learning|LLM)\b"),
    ("espacio", r"\b(satellite|space|launch|rocket|orbit\w*|lunar)\b"),
    ("defensa", r"\b(defen[cs]e|DoD|missile|drone|Army|Navy|Air Force|munitions?)\b"),
    ("energía", r"\b(solar|hydrogen|geothermal|wind|grid|electrolyzer)\b"),
    ("robótica", r"\b(robot\w*|autonomous)\b"),
]
# mejor tramo de perforación: umbral de "importante" (ley × metros)
UMBRAL_GM = {"oro": 100, "plata": 5000, "cobre": 150, "litio": 30, "uranio": 10, "tierras raras": 20, "níquel": 30, "zinc": 100,
             "cobalto": 5, "antimonio": 50, "wolframio": 20, "estaño": 20}
METAL_LEY = {"au": "oro", "aueq": "oro", "gold": "oro", "ag": "plata", "ageq": "plata", "silver": "plata", "cu": "cobre", "cueq": "cobre",
             "copper": "cobre", "li2o": "litio", "u3o8": "uranio", "treo": "tierras raras", "ni": "níquel", "nieq": "níquel",
             "zn": "zinc", "zneq": "zinc", "co": "cobalto", "sb": "antimonio", "wo3": "wolframio", "sn": "estaño"}
N = r"(\d{1,4}(?:[.,]\d{1,3})?)"
RE_TRAMO = [  # (texto, orden de grupos)
    (re.compile(N + r"\s*(?:m|metres|meters)\b[^.;()]{0,25}?(?:of|@|at|grading|averaging|assaying|returning|with)\s*" + N +
                r"\s*(g/t|%)\s*(AuEq|Au|gold|AgEq|Ag|silver|CuEq|Cu|copper|Li2O|U3O8|TREO|NiEq|Ni|ZnEq|Zn|Co|Sb|WO3|Sn)", re.I), "mL"),
    (re.compile(N + r"\s*(g/t|%)\s*(AuEq|Au|gold|AgEq|Ag|silver|CuEq|Cu|copper|Li2O|U3O8|TREO|NiEq|Ni|ZnEq|Zn|Co|Sb|WO3|Sn)\b"
                r"[^.;()]{0,15}?\bover\s*" + N + r"\s*(?:m|metres|meters)\b", re.I), "Lm"),
]
RE_VAN = re.compile(r"\b(?:NPV|net present value)[^$€£]{0,60}?(US|C|CA|A|AU)?\$\s?(\d[\d,.]*)\s*(billion|million|B|M)\b", re.I)
RE_TIR = re.compile(r"\b(?:IRR|internal rate of return)[^%]{0,40}?(\d{1,3}(?:\.\d)?)\s*%"
                    r"|(\d{1,3}(?:\.\d)?)\s*%\s*(?:after-tax\s+|pre-tax\s+)?(?:IRR|internal rate of return)", re.I)
# Rutina que no es noticia (fechas de resultados, juntas, dividendos, presentaciones)
RE_RUTINA = re.compile(r"\b(to announce|will announce|announces? (?:date|timing)|to report (?:third|fourth|second|first)|"
                       r"conference call|webcast|to present at|to participate|presents? at|investor (?:day|conference)|"
                       r"annual (?:general |and special )?meeting|AGM|results of (?:the )?annual|declares? (?:quarterly |monthly )?"
                       r"dividend|earnings release|shareholder letter|corporate update call|engages|appoints?|joins? the board)\b", re.I)
# Artículos de opinión de webs de bolsa (no son la empresa contando su noticia)
RE_COMENTARIO = re.compile(r"\?|^(?:how|why|what|can|is|are|should|will|does|here'?s|a path|the case)\b|investors? may|"
                           r"draws? (?:expert )?attention|stock (?:rises|gains|dips|falls|jumps|soars|surges|slides|climbs|drops)|"
                           r"shares? (?:rise|jump|fall|soar|surge|drop|climb|gain)|slideshow|analysts?|valuation|price target|"
                           r"discusses|deep dive|outlook for|could|might|builds? towards?|needs? to deliver|top \d+|weekly|roundup|"
                           r"stocks? to (?:buy|watch)|^this\b|just got|heats? up|bull run|the next\b", re.I)
RE_PROMO = re.compile(r"(USA News Group|FN Media Group|Market ?News ?Updates|Equity[- ]Insider|Investorideas|InvestorIdeas|"
                      r"Streetwise Reports|sponsored|paid (?:advertisement|promotion|for by)|(?:is|was|been) compensated|"
                      r"disclaimer:|this article is a paid)", re.I)
VERBOS_ANUNCIO = (r"announces?|reports?|intersects?|intercepts?|drills?|discovers?|receives?|completes?|signs?|wins?|awarded|secures?|"
                  r"achieves?|unveils?|launches?|expands?|confirms?|delivers?|commences?|begins?|provides?|files?|granted|obtains?|"
                  r"enters?|closes?|identifies?|extends?|returns?|hits?|encounters?|defines?|makes?|publishes?|releases?|"
                  r"approved|approves|gets?|grants?|starts?|initiates?|increases?|doubles?|upgrades?|acquires?|agrees?|"
                  r"intersected|cuts?|drilled")
RE_ANUNCIO = re.compile(r"^[A-Z0-9][\w.&'’\-, ]{1,70}?\s+(?:\([^)]*\)\s*)*(?:" + VERBOS_ANUNCIO + r")\b", re.I)
AGENCIAS = ("globenewswire", "business wire", "businesswire", "pr newswire", "newsfile", "accesswire", "cnw", "cision",
            "tmx money", "stockhouse", "junior mining network", "morningstar", "newswire")
RE_IMPORTE = re.compile(r"(US)?\$\s?(\d[\d,.]*)\s*(billion|million)\b", re.I)


def norm_num(x):
    return float(x.replace(",", "."))


def mejor_tramo(t):
    """Mejor (metal, metros, ley, unidad, ley×metros) con umbral de importancia relativo al metal."""
    mejor = None
    for rx, orden in RE_TRAMO:
        for m in rx.finditer(t):
            if orden == "mL":
                metros, ley, uni, met = norm_num(m.group(1)), norm_num(m.group(2)), m.group(3), m.group(4)
            else:
                ley, uni, met, metros = norm_num(m.group(1)), m.group(2), m.group(3), norm_num(m.group(4))
            metal = METAL_LEY.get(met.lower())
            if not metal or metros <= 0 or metros > 2000 or ley <= 0:
                continue
            if uni == "%" and metal in ("oro", "plata"):
                continue
            gm = metros * ley
            rel = gm / UMBRAL_GM.get(metal, 1e9)
            if mejor is None or rel > mejor[5]:
                mejor = (metal, metros, ley, uni, gm, rel, met)
    return mejor


def primero(rx_lista, t):
    for nombre, rx in rx_lista:
        if re.search(rx, t, re.I):
            return nombre
    return None


def todos(rx_lista, t, flags=re.I):
    return [n for n, rx in rx_lista if re.search(rx, t, flags)]


def tickers_de(t):
    vistos, out = set(), []
    for b, s in RE_TICKER.findall(t[:3000]):
        b = "NASDAQ" if b == "Nasdaq" else b
        if (b, s) not in vistos:
            vistos.add((b, s))
            out.append({"b": b, "s": s})
    out.sort(key=lambda x: PREFERIDA.index(x["b"]) if x["b"] in PREFERIDA else 99)
    return out[:4]


def empresa_de(titulo, txt, tks):
    """Nombre de la empresa: lo que va justo antes del primer «(BOLSA: TICKER)»."""
    m = re.search(r"([A-Z][\w.&,'’\- ]{2,80}?)\s*,?\s*\(\s*" + BOLSAS + r"\s*:", txt[:3000])
    if m:
        nom = re.split(r"\s[-–—]{1,2}\s|\)\s*-\s*|/PRNewswire/|/CNW/|Newsfile Corp\.?\s*-[^)]*\)\s*-?\s*", m.group(1))[-1]
        nom = re.sub(r"^(?:and|the|The|announces?|today|\W)+\s*", "", nom).strip(" ,.-")
        if 2 < len(nom) < 70:
            return nom
    m = re.match(r"([A-Z][\w.&'’\-]+(?: [A-Z][\w.&'’\-]+){0,4})", titulo)
    return m.group(1) if m else titulo[:40]


def limpia_nombre(n):
    """«Southern Cross Gold Infill Drilling» -> «Southern Cross Gold»; «Prospector Continues to» -> «Prospector»."""
    n = re.sub(r"[®™]", "", n or "").strip(" ,.-:")
    w = n.split()
    for i in range(1, len(w)):
        if re.match(r"(?:Infill|Drilling|Drill|Program|Results?|Phase|Update|Resource|Assays?|Exploration|Project|Explorer|"
                    r"Step-out|Maiden|Stock|Shares|Continues|Further|Again|Successfully|Now|Also|Expands?|Extends?)$", w[i], re.I) \
                and (i >= 2 or re.match(r"(?:Continues|Further|Again|Successfully|Now|Also)$", w[i], re.I)):
            return " ".join(w[:i])
    return n


def nombre_titulo(ti):
    m = re.match(r"^(.{2,70}?)\s+(?:\([^)]*\)\s*)*(?:" + VERBOS_ANUNCIO + r")\b", re.sub(r"[’']s\b", "", ti), re.I)
    if not m:
        return None
    n = re.sub(r"\s*\([^)]*\)\s*$", "", m.group(1)).strip(" ,.-:")
    w = n.split()
    for i in range(2, len(w)):
        if re.match(r"(?:Infill|Drilling|Drill|Program|Results?|Phase|Update|Resource|Assays?|Exploration|Project|Explorer|"
                    r"Step-out|Maiden|Stock|Shares)$", w[i], re.I):
            n = " ".join(w[:i])
            break
    return n if 2 < len(n) <= 60 and n[0].isupper() else None


def clasifica(ti, txt, grupo, fuente="", google=False):
    """Devuelve el hallazgo clasificado (dict) o None si no interesa."""
    if RE_RUTINA.search(ti):
        return None
    t = f"{ti}. {txt}"
    nombre = empresa_de(html.unescape(ti).strip(), t, tickers_de(t))
    if google or not RE_TICKER.search(t[:3000]):
        nombre = nombre_titulo(html.unescape(ti).strip()) or nombre
    nombre = limpia_nombre(nombre)
    ti_ev = re.sub(re.escape(nombre), " ", ti, flags=re.I) if len(nombre) > 3 else ti
    ti_ev = re.sub(r"\bDiscovery\s+(?:Mining|Silver|Minerals|Metals|Gold|Resources|Energy|Corp\w*|Ltd|Inc|Ventures|Harbour)\b",
                   " ", ti_ev)
    if grupo == "min" or (re.search(r"\b(g/t|drill|mineral|mining|exploration|deposit|ore|claims|property)\b", t, re.I)
                          and not re.search(r"\b(FDA|clinical|patients)\b", t)):
        grupo = "min"
        tipo = primero(EVENTOS_MIN, ti_ev) or primero(EVENTOS_MIN, re.sub(re.escape(nombre), " ", txt[:1500], flags=re.I))
        etiquetas = todos(MINERALES, t[:4000], 0) or todos(MINERALES, t[:4000])
    else:
        grupo = "tec"
        tipo = primero(EVENTOS_TEC, ti_ev) or primero(EVENTOS_TEC, re.sub(re.escape(nombre), " ", txt[:1500], flags=re.I))
        etiquetas = todos(CAMPOS, t[:3000], 0)
    if not tipo:
        return None
    h = {"g": grupo, "k": tipo, "et": etiquetas[:4], "ti": html.unescape(ti).strip()}
    tks = tickers_de(t)
    if tks:
        h["tk"] = tks
    h["em"] = nombre
    imp, clave = False, None
    if grupo == "min":
        tr = mejor_tramo(t[:20000])
        if tr and tipo in ("perforacion", "descubrimiento"):
            metal, metros, ley, uni, gm, rel, met = tr
            h["tr"] = {"m": round(metros, 1), "l": round(ley, 3), "u": uni, "x": met, "met": metal, "gm": round(gm, 1),
                       "rel": round(rel, 2)}
            imp = rel >= 1
        van, tir = RE_VAN.search(t[:20000]), RE_TIR.search(t[:20000])
        if tipo == "estudio" and (van or tir):
            h["eco"] = {}
            if van:
                v = norm_num(van.group(2).replace(",", "")) * (1000 if van.group(3).lower() in ("billion", "b") else 1)
                h["eco"]["van"] = round(v, 1)
                h["eco"]["mon"] = (van.group(1) or "US").upper()
            if tir:
                h["eco"]["tir"] = float(tir.group(1) or tir.group(2))
            imp = True
        if tipo == "descubrimiento" and re.search(dict(EVENTOS_MIN)["descubrimiento"], ti_ev, re.I) \
                and (not h.get("tr") or h["tr"]["rel"] >= 0.5):
            imp = True
        if tipo == "recursos" and re.search(r"\b(maiden|initial|first|increase[sd]?|expan\w+|doubles?|upgrade[sd]?)\b", t[:2500], re.I):
            imp = True
        if tipo == "permiso" and re.search(r"\b(record of decision|final|key|major|main|mining permit|environmental impact|"
                                          r"construction permit|exploitation)\b", t[:2500], re.I):
            imp = True
        if tipo in ("produccion", "problema"):
            imp = True
        if tipo == "compra":
            m = RE_IMPORTE.search(t[:2500])
            if m and norm_num(m.group(2).replace(",", "")) * (1000 if m.group(3).lower() == "billion" else 1) >= 50:
                imp = True
    else:
        if tipo in ("aprobacion", "problema"):
            imp = True
        if tipo == "ensayo" and re.search(r"\b(met (?:its )?primary endpoint|statistically significant|positive)\b", t[:3000], re.I) \
                and re.search(r"\bphase (?:3|iii|2b)\b", t[:3000], re.I):
            imp = True
        if tipo == "avance" and re.search(r"\b(world record|first-ever|world'?s first|breakthrough)\b", ti, re.I) and etiquetas:
            imp = True
        if tipo == "contrato":
            m = RE_IMPORTE.search(t[:2500])
            if m:
                v = norm_num(m.group(2).replace(",", "")) * (1000 if m.group(3).lower() == "billion" else 1)
                h["imp_usd"] = round(v, 1)
                imp = v >= 100
    # De Google News solo avisa lo que es un comunicado de la empresa (agencia o título «Empresa anuncia...»),
    # no los artículos de webs de bolsa sobre ella
    sin_tk = re.sub(r"\([^)]*\)", " ", h["ti"])
    if google and (RE_COMENTARIO.search(h["ti"]) or not (any(a in fuente.lower() for a in AGENCIAS) or RE_ANUNCIO.match(sin_tk))):
        h["pr"] = True                  # prensa / opinión
        imp = False
    if tipo == "recursos" and re.search(r"\b(towards?|ahead of|needs? to|plans?|path to|expected)\b", ti, re.I):
        imp = False                     # «construye hacia un recurso inicial»: todavía no lo es
    if RE_PROMO.search(t[:40000]) or RE_PROMO.search(fuente):
        h["promo"] = h["pr"] = True     # publicidad pagada de un promotor de acciones: nunca avisa
        imp = False
    if imp:
        h["imp0"] = True                # importante a falta de saber su ticker
    if imp and not tks:
        imp = False                     # solo si sabemos qué acción es
    if imp:
        h["imp"] = True
    return h


# ── red ──────────────────────────────────────────────────────────────────────
import requests  # noqa: E402

SES = requests.Session()


def pedir(url, intentos=2, timeout=30):
    for k in range(intentos):
        try:
            r = SES.get(url, headers=NAV, timeout=timeout)
            if r.status_code == 200:
                return r.text
            if r.status_code in (403, 404):
                return None
        except requests.RequestException:
            pass
        time.sleep(3 * (k + 1))
    return None


def fecha_rss(x):
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M %z"):
        try:
            d = dt.datetime.strptime((x or "").strip(), fmt)
            return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc)
        except ValueError:
            continue
    return dt.datetime.now(dt.timezone.utc)


def items_rss(xml):
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out = []
    for it in raiz.iter("item"):
        ti = it.findtext("title") or ""
        src = it.find("source")
        fuente = src.text if src is not None and src.text else ""
        if fuente and ti.endswith(" - " + fuente):
            ti = ti[: -len(fuente) - 3]
        desc = re.sub(r"<[^>]+>", " ", html.unescape(it.findtext("description") or ""))
        out.append({"ti": ti.strip(), "u": (it.findtext("link") or "").strip(), "f": fecha_rss(it.findtext("pubDate")),
                    "d": re.sub(r"\s+", " ", desc).strip(), "fu": fuente})
    return out


def texto_pagina(url):
    h = pedir(url, intentos=1, timeout=30)
    if not h:
        return ""
    h = re.sub(r"(?is)<(script|style|nav|header|footer).*?</\1>", " ", h)
    t = re.sub(r"<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", html.unescape(t))[:40000]


BOLSA_YAHOO = {"NYQ": "NYSE", "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "ASE": "NYSE American", "TOR": "TSX",
               "VAN": "TSXV", "CNQ": "CSE", "NEO": "NEO", "ASX": "ASX", "LSE": "LSE", "PNK": "OTC", "OQX": "OTCQX", "OQB": "OTCQB"}


def buscar_ticker(nombre, cache, sectores=None):
    """Ticker de una empresa por su nombre (buscador de Yahoo), probando el nombre cada vez más corto."""
    if not nombre:
        return None
    if nombre in cache:
        return cache[nombre] or None
    pal = nombre.split()
    tk = None
    for n in range(min(4, len(pal)), 0, -1):
        q = " ".join(pal[:n])
        if n == 1 and len(q) < 5:
            break
        try:
            j = SES.get("https://query1.finance.yahoo.com/v1/finance/search?q=" + urllib.parse.quote(q)
                        + "&quotesCount=6&newsCount=0", headers=NAV, timeout=20).json()
        except Exception:
            break
        time.sleep(0.4)
        def nom(x):
            return (x.get("longname") or x.get("shortname") or "").lower()
        palabras = [w.lower().strip(",.") for w in q.split()]
        cand = [x for x in j.get("quotes", []) if x.get("quoteType") == "EQUITY" and x.get("exchange") in BOLSA_YAHOO
                and (all(w in nom(x) for w in palabras) if n > 1 else nom(x).startswith(palabras[0]))]
        if sectores:
            buenos = [x for x in cand if x.get("sector") in sectores]
            if buenos:
                cand = buenos
            elif n == 1:
                cand = []               # una palabra suelta y de otro sector: no fiarse («Crescent» -> Crescent Energy)
        if cand:
            cand.sort(key=lambda x: list(BOLSA_YAHOO).index(x["exchange"]))
            x = cand[0]
            tk = {"b": BOLSA_YAHOO[x["exchange"]], "s": x["symbol"].split(".")[0], "y": x["symbol"]}
            break
    cache[nombre] = tk or ""
    return tk


def precio_yahoo(tk):
    b, s = tk["b"], tk["s"]
    sym = tk.get("y") or (s.replace(".", "-") + SUFIJO_YAHOO.get(b, "") if b not in SUFIJO_YAHOO else s + SUFIJO_YAHOO[b])
    try:
        j = SES.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=5d&interval=1d",
                    headers=NAV, timeout=20).json()
        r = j["chart"]["result"][0]
        c = [x for x in r["indicators"]["quote"][0]["close"] if x]
        if len(c) >= 2:
            return {"y": sym, "px": round(c[-1], 4), "d1": round(c[-1] / c[-2] - 1, 4), "mon": r["meta"].get("currency")}
    except Exception:
        pass
    return None


# ── estado, publicación y avisos ─────────────────────────────────────────────
def leer(ruta, defecto):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return defecto


def guardar(ruta, obj):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, ruta)


def ahora():
    return dt.datetime.now(dt.timezone.utc)


def log(*a):
    print(ahora().strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def clave(h):
    base = re.sub(r"[^a-z0-9]+", "", h["ti"].lower())[:90]
    return hashlib.sha1(base.encode()).hexdigest()[:16]


TIPO_ES = {"descubrimiento": "⛏️ Descubrimiento", "perforacion": "⛏️ Resultados de perforación", "recursos": "⛏️ Estimación de recursos",
           "estudio": "⛏️ Estudio económico", "permiso": "⛏️ Permiso", "produccion": "⛏️ Producción o construcción",
           "compra": "🤝 Compra o fusión", "financiacion": "💵 Financiación", "problema": "🚩 Problema",
           "aprobacion": "🔬 Aprobación", "ensayo": "🔬 Ensayo clínico", "avance": "🔬 Avance tecnológico",
           "contrato": "🔬 Contrato", "patente": "🔬 Patente"}
METAL_X = {"oro": "de oro", "plata": "de plata", "cobre": "de cobre", "litio": "de Li2O", "uranio": "de U3O8",
           "tierras raras": "de tierras raras", "níquel": "de níquel", "zinc": "de zinc", "cobalto": "de cobalto",
           "antimonio": "de antimonio", "wolframio": "de WO3", "estaño": "de estaño"}


def cifra_es(x, d=1):
    s = f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s.rstrip("0").rstrip(",") if "," in s else s


def resumen_es(h):
    p = []
    if h.get("tr"):
        tr = h["tr"]
        uni = "g/t" if tr["u"].lower() == "g/t" else "%"
        p.append(f"{cifra_es(tr['m'])} m con {cifra_es(tr['l'], 2)} {uni} {METAL_X.get(tr['met'], '')} "
                 f"({cifra_es(tr['gm'], 0)} {'g·m' if uni == 'g/t' else '%·m'})")
    if h.get("eco"):
        e = h["eco"]
        if "van" in e:
            p.append(f"VAN {e.get('mon', 'US')}${cifra_es(e['van'], 0)} M")
        if "tir" in e:
            p.append(f"TIR {cifra_es(e['tir'], 1)} %")
    if h.get("imp_usd"):
        p.append(f"importe ${cifra_es(h['imp_usd'], 0)} M")
    return " · ".join(p)


def email(importantes, sin_email):
    if not importantes:
        return False
    lineas = []
    for h in importantes:
        tks = " · ".join(f"{t['b']}: {t['s']}" for t in h.get("tk", []))
        px = h.get("px")
        lineas.append(
            f"{TIPO_ES.get(h['k'], h['k'])} — {h['em']} ({tks})\n"
            + (f"   {resumen_es(h)}\n" if resumen_es(h) else "")
            + f"   «{h['ti']}»\n"
            + (f"   {', '.join(h['et'])}\n" if h.get("et") else "")
            + (f"   Precio {px['px']} {px.get('mon') or ''} ({px['d1'] * 100:+.1f} % último día)\n" if px else "")
            + f"   Comunicado: {h['u']}\n"
            + f"   Traducir: https://translate.google.com/translate?sl=en&tl=es&u={urllib.parse.quote(h['u'], safe='')}\n")
    if len(importantes) == 1:
        h = importantes[0]
        asunto = f"Radar Bolsa: {TIPO_ES.get(h['k'], h['k']).split(' ', 1)[-1].lower()} — {h['em']}"
    else:
        asunto = f"Radar Bolsa: {len(importantes)} hallazgos importantes"
    cuerpo = ("\n".join(lineas) + f"\nTodos los hallazgos en la app: {APP}\n\n"
              "Ojo: una noticia buena no garantiza que la acción suba (a menudo ya ha subido cuando sale), "
              "y casi todas las exploradoras sacan acciones nuevas para financiarse. Información, no consejo de inversión.")
    if sin_email:
        print("── EMAIL (no enviado) ──\n" + asunto + "\n" + cuerpo)
        return True
    r = subprocess.run(["/usr/bin/python3", "/root/avisar.py", asunto, cuerpo], capture_output=True, text=True)
    log("email:", asunto, "|", (r.stdout or r.stderr).strip()[:80])
    return r.returncode == 0


def publicar(est, sin_git, forzar):
    lim = (ahora() - dt.timedelta(days=DIAS)).isoformat()
    items = sorted((h for h in est["items"].values() if h["f"] >= lim), key=lambda h: h["f"], reverse=True)
    guardar(SALIDA, {"act": ahora().strftime("%Y-%m-%dT%H:%MZ"), "n": len(items), "h": items})
    if sin_git:
        return
    ult = est.get("ult_push", "")
    if not forzar and ult > (ahora() - dt.timedelta(minutes=60)).isoformat():
        return
    with open(CERROJO_REPO, "w") as fl:
        try:
            if fcntl:
                fcntl.flock(fl, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log("repo ocupado (actualizar.sh): hallazgos.json irá en su commit")
            return
        g = lambda *a: subprocess.run(["git", "-C", REPO, *a], capture_output=True, text=True)
        g("add", "data/hallazgos.json")
        if g("diff", "--cached", "--quiet").returncode == 0:
            return
        g("commit", "-qm", f"hallazgos {ahora().strftime('%Y-%m-%d %H:%M')} UTC")
        p = g("pull", "-q", "--rebase", "origin", "main")
        q = g("push", "-q", "origin", "main")
        if p.returncode or q.returncode:
            log("git:", (p.stderr + q.stderr).strip()[:200])
        else:
            est["ult_push"] = ahora().isoformat()


def main():
    sin_email, sin_git = "--sin-email" in sys.argv, "--sin-git" in sys.argv
    lock = open(CERROJO if fcntl else os.devnull, "w")
    try:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return                                            # la pasada anterior sigue
    est = leer(ESTADO, {"items": {}, "vistos": {}, "emails": {}})
    primera = not est["items"]
    nuevos = []
    fuentes = [(f, g, u, False) for f, g, u in FEEDS]
    if primera or est.get("ult_google", "") < (ahora() - dt.timedelta(minutes=14)).isoformat():
        dias = "7d" if primera else "1d"
        fuentes += [("Google News", g, "https://news.google.com/rss/search?q=" + urllib.parse.quote(f"{q} when:{dias}")
                     + "&hl=en-US&gl=US&ceid=US:en", True) for g, q in GOOGLE]
        est["ult_google"] = ahora().isoformat()
    for fuente, grupo, url, es_google in fuentes:
        x = pedir(url)
        if not x:
            log("sin respuesta:", fuente, url[:80])
            continue
        for it in items_rss(x):
            k = hashlib.sha1(re.sub(r"[^a-z0-9]+", "", it["ti"].lower())[:90].encode()).hexdigest()[:16]
            if k in est["vistos"]:
                continue
            est["vistos"][k] = ahora().isoformat()
            if it["f"] < ahora() - dt.timedelta(days=DIAS):
                continue
            txt = it["d"]
            if not es_google and it["u"] and fuente in ("Newsfile", "PR Newswire"):
                txt = texto_pagina(it["u"]) or txt                # el comunicado entero: cifras y tickers
                time.sleep(0.5)
            h = clasifica(it["ti"], txt, grupo, it["fu"] or fuente, es_google)
            if not h:
                continue
            h.update(u=it["u"], f=it["f"].isoformat(timespec="seconds"), fu=it["fu"] or fuente, id=k)
            if not h.get("tk") and not h.get("pr"):
                sect = ({"Basic Materials", "Energy"} if h["g"] == "min" else
                        {"Healthcare"} if "biotecnología" in h.get("et", []) else
                        {"Technology", "Industrials", "Communication Services", "Utilities", "Energy", "Healthcare"})
                tk = buscar_ticker(h.get("em"), est.setdefault("nombres", {}), sect)
                if tk:
                    h["tk"] = [tk]
                    if h.get("imp0"):
                        h["imp"] = True
            dup = re.sub(r"[^a-z0-9]+", "", " ".join(h.get("em", "").lower().split()[:2])) + "|" + h["k"]
            previa = est.setdefault("dups", {}).get(dup)
            if previa and dup[:1] != "|" and abs((dt.datetime.fromisoformat(previa) - dt.datetime.fromisoformat(h["f"])).days) < 3:
                continue                                    # la misma noticia en otra web
            est["dups"][dup] = h["f"]
            if h.get("imp") and h.get("tk"):
                px = precio_yahoo(h["tk"][0])
                if px:
                    h["px"] = px
            est["items"][k] = h
            nuevos.append(h)
        time.sleep(1.0 if es_google else 0.3)
    # limpieza
    lim = (ahora() - dt.timedelta(days=DIAS + 5)).isoformat()
    est["items"] = {k: h for k, h in est["items"].items() if h["f"] >= lim}
    est["vistos"] = {k: v for k, v in est["vistos"].items() if v >= lim}
    est["dups"] = {k: v for k, v in est.get("dups", {}).items() if v >= lim}
    if len(est.get("nombres", {})) > 5000:
        est["nombres"] = {}
    # avisos: solo lo importante publicado en las últimas 6 h (la 1ª pasada no avisa del pasado)
    hoy = ahora().strftime("%Y-%m-%d")
    reciente = (ahora() - dt.timedelta(hours=6)).isoformat()
    imp = [h for h in nuevos if h.get("imp") and h["f"] >= reciente and not primera]
    enviado = False
    if imp and est["emails"].get(hoy, 0) < MAX_EMAILS_DIA:
        enviado = email(imp[:12], sin_email)
        if enviado:
            est["emails"] = {hoy: est["emails"].get(hoy, 0) + 1}
    publicar(est, sin_git, forzar=bool(imp) or primera)
    guardar(ESTADO, est)
    log(f"nuevos {len(nuevos)} ({sum(1 for h in nuevos if h.get('imp'))} importantes)"
        f"{' · email enviado' if enviado else ''} · en la app {len([h for h in est['items'].values()])}")


def test():
    casos = [
        ("Bayhorse Silver Intersects 12.0 Metres of 850 g/t Silver at Bayhorse", "Bayhorse Silver Inc, (TSXV: BHS) (OTCQB: BHSIF)",
         "min", "perforacion", True),
        ("Discovery Mining Reports New High-Grade Intersections", "Discovery Mining Corp. (CSE: DMC) intersected 2.0 m of 1.1 g/t Au",
         "min", "perforacion", False),
        ("Company X Makes New Copper Discovery", "Company X Ltd. (TSX: XXX) announces 350 m of 0.62% CuEq", "min", "descubrimiento", True),
        ("Acme Gold Announces Positive PFS with After-Tax NPV of US$1.2 Billion and 32% IRR", "Acme Gold (NYSE American: ACG)",
         "min", "estudio", True),
        ("Junior Co Announces $2 Million Private Placement", "Junior Co (TSXV: JNR)", "min", "financiacion", False),
        ("Teva wins FDA approval for monthly schizophrenia injection", "Teva Pharmaceutical (NYSE: TEVA)", "tec", "aprobacion", True),
        ("QuantumCo Achieves World Record Qubit Fidelity", "QuantumCo Inc. (NASDAQ: QTUM) quantum", "tec", "avance", True),
        ("DefenseCo Awarded $250 Million Army Contract", "DefenseCo (NYSE: DFN) awarded $250 million", "tec", "contrato", True),
        ("Vizsla Silver Announces Results of Annual General Meeting", "Vizsla Silver Corp. (TSX: VZLA)", "min", None, False),
        ("Titan International, Inc. to Announce Third Quarter 2026 Financial Results", "(NYSE: TWI)", "tec", None, False),
        ("Washington Is Writing Billion-Dollar Checks for Tungsten", "USA News Group (NASDAQ: MP) 21.3 m of 3.2 g/t gold discovery. "
         "Disclaimer: paid advertisement", "min", "perforacion", False),
    ]
    ok = True
    for ti, txt, grupo, k, imp in casos:
        h = clasifica(ti, txt, grupo)
        bien = (h is None and k is None) or (h is not None and h["k"] == k and bool(h.get("imp")) == imp)
        ok &= bien
        print(("OK " if bien else "MAL"), f"{ti[:60]:60} -> {h and h['k']} imp={h and h.get('imp')} "
              f"{resumen_es(h) if h else ''} tk={h and h.get('tk')}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if "--test" in sys.argv:
        test()
    main()
