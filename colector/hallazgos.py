#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Radar Bolsa · Hallazgos (2026-10-10, pedido del usuario: «extremadamente importante, 24/7»).

Vigila las noticias de MINERÍA (exploración, descubrimientos, nuevas minas) y de TECNOLOGÍA
(aprobaciones, ensayos, avances, contratos) de empresas que cotizan, y manda un EMAIL al momento
cuando hay algo importante. Corre cada 5 minutos en el VPS (cron, con su propio cerrojo).

v2 (2026-10-12, pedido del usuario: «enumera por importancia, crecimiento histórico y FOMO; mejora la
calidad y veracidad de la noticia»):
  - Tres notas de 0 a 100 por noticia, con las que la app ordena y numera:
      IMPORTANCIA: tipo de noticia, sus cifras (tramo de perforación, VAN, importe) y lo que pesan
        frente a lo que vale la empresa, y de dónde viene (la propia empresa o un resumen).
      CRECIMIENTO HISTÓRICO de la acción: 1, 3 y 5 años (anualizado).
      FOMO: cuánto ha subido desde la noticia, cuánto volumen mueve frente a lo normal, cuántas webs
        la cuentan y cómo va el último mes.
  - Veracidad: cada ticker se comprueba con el nombre oficial de Yahoo (si es de otra empresa, fuera);
    anuncios OFICIALES de la bolsa australiana (ASX: su marca de «afecta al precio» y el PDF, del que
    se leen las cifras) y de CNW (Canadá); cada noticia dice si viene de la empresa, de una copia del
    comunicado, de un resumen automático o de prensa; las repetidas en otras webs se juntan en una
    (se queda la versión oficial y se cuentan las webs).
  - Lo medido en vivo: qué hace la acción DESPUÉS de cada aviso importante (comprando al cierre del
    día de la noticia) frente al fondo de su sector, separando las que llegaban con FOMO.

Fuentes (probadas desde el VPS):
  - TMX Newsfile: minería y tecnología (solo da los 10 últimos -> de ahí el cada-5-minutos).
  - PR Newswire: minería, biotecnología y defensa. CNW (newswire.ca): todo Canadá, se filtra.
  - ASX: todos los anuncios de la bolsa australiana que la propia bolsa marca como «afecta al precio».
  - Google News (cada 15 min): 11 búsquedas que recogen GlobeNewswire, Business Wire, etc.
    (GlobeNewswire y Accesswire bloquean al VPS: 403).

    python3 hallazgos.py                # lo que corre el cron
    python3 hallazgos.py --sin-email --sin-git   # para probar
    python3 hallazgos.py --reprocesar   # rehace los 30 días con las reglas de ahora (no avisa)
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
import math
import os
import re
import statistics
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET

REPO = os.environ.get("RB_REPO", "/root/radar-bolsa-repo")
CACHE = os.environ.get("RB_CACHE", "/root/radar-bolsa-cache")
SALIDA = f"{REPO}/data/hallazgos.json"
ESTADO = f"{CACHE}/hallazgos_estado.json"
MERCADO = f"{CACHE}/hallazgos_mercado.json"     # cotizaciones y gráficos de las acciones de las noticias
PDFS = f"{CACHE}/asx_pdf"                        # texto de los PDF de la ASX ya leídos
CERROJO = os.environ.get("RB_CERROJO", "/tmp/hallazgos.lock")
CERROJO_REPO = "/tmp/radar_bolsa.lock"          # el mismo que actualizar.sh: no tocar git a la vez
NAV = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/124 Safari/537.36"}
DIAS = 30                 # lo que se publica
MAX_EMAILS_DIA = 20
NOTA_EMAIL = 55           # importancia mínima para avisar (además de las reglas de «importante»)
APP = "https://hugoibel.github.io/radar-bolsa/#hallazgos"

FEEDS = [   # (fuente, grupo, url); grupo None = se decide por el contenido
    ("Newsfile", "min", "https://feeds.newsfilecorp.com/industry/mining-metals"),
    ("Newsfile", "tec", "https://feeds.newsfilecorp.com/industry/technology"),
    ("PR Newswire", "min", "https://www.prnewswire.com/rss/heavy-industry-manufacturing-latest-news/mining-metals-list.rss"),
    ("PR Newswire", "tec", "https://www.prnewswire.com/rss/health-latest-news/biotechnology-list.rss"),
    ("PR Newswire", "tec", "https://www.prnewswire.com/rss/heavy-industry-manufacturing-latest-news/aerospace-defense-list.rss"),
    ("CNW", None, "https://www.newswire.ca/rss/news-releases-list.rss"),
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
# Bolsa australiana: la lista de todos los anuncios y el PDF de cada uno (sin claves)
ASX_LISTA = "https://asx.api.markitdigital.com/asx-research/1.0/markets/announcements?page={p}&itemsPerPage=100"
ASX_PDF = "https://cdn-api.markitdigital.com/apiman-gateway/ASX/asx-research/1.0/file/{k}"
IND_MIN = {"Metals & Mining"}
SUB_MIN = {"Coal & Consumable Fuels"}           # ahí están las de uranio
IND_TEC = {"Pharmaceuticals", "Biotechnology", "Health Care Equipment & Supplies", "Health Care Technology",
           "Life Sciences Tools & Services", "Semiconductors & Semiconductor Equipment", "Aerospace & Defense",
           "Electrical Equipment", "Technology Hardware, Storage & Peripherals",
           "Electronic Equipment, Instruments & Components"}
# Fondos de su sector, para comparar lo que hace la acción después de la noticia
BENCH = {"min": "GDXJ", "bio": "XBI", "tec": "QQQ"}
MONEDAS = ("AUD", "CAD", "GBP", "EUR", "ZAR", "JPY", "HKD", "CHF", "SEK", "NOK", "DKK", "ILS", "NZD", "SGD", "MXN", "BRL")
MENOR = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}   # cotizan en céntimos

# ── reglas ────────────────────────────────────────────────────────────────────
BOLSAS = r"(TSX-?V|TSXV|TSX|CSE|CBOE CA|NEO|NYSE American|NYSE MKT|NYSE|NASDAQ|Nasdaq|OTCQX|OTCQB|OTC Pink|OTC|ASX|AIM|LSE|JSE|FSE|Frankfurt|XETRA)"
RE_TICKER = re.compile(BOLSAS + r"\s*:\s*\"?([A-Z][A-Z0-9]{0,5}(?:\.[A-Z]{1,2})?)\b")
SUFIJO_YAHOO = {"TSXV": ".V", "TSX-V": ".V", "TSX": ".TO", "CSE": ".CN", "NEO": ".NE", "CBOE CA": ".NE", "ASX": ".AX",
                "AIM": ".L", "LSE": ".L", "JSE": ".JO", "FSE": ".F", "Frankfurt": ".F", "XETRA": ".DE"}
PREFERIDA = ["NYSE", "NASDAQ", "Nasdaq", "NYSE American", "NYSE MKT", "TSX", "TSXV", "TSX-V", "ASX", "CSE", "LSE", "AIM", "NEO",
             "OTCQX", "OTCQB", "OTC"]

# Perforación que empieza o se planea (aún sin resultados)
PROGRAMA = (r"\b(?:(?:re-?)?commenc\w*|begins?|beginning|began|starts?|started|restart\w*|resum\w*|launch\w*|initiat\w*|"
            r"mobili[sz]\w*|outlines?|plans?|planned|planning|to (?:commence|begin|start|test|follow)|follow[- ]up|underway|"
            r"upcoming|scheduled|targeting|fully[- ]funded|permits? (?:for|to)|approval to drill|access|secured for|"
            r"rig (?:on site|arrives)|preparing|prepares?|ready to)\b[^.;]{0,60}?\b(?:drill\w*|program\w*|campaign|rig)\b"
            r"|\b(?:drill\w*|program\w*|campaign|rig)\b[^.;]{0,40}?\b(?:(?:re-?)?commenc\w*|begins?|underway|starts?|started|"
            r"restart\w*|resum\w*|mobili[sz]\w*|planned|completed|to commence|approved|permitted|funded)\b")
EVENTOS_MIN = [   # en orden de prioridad: el primero que casa en el título manda (con sus vetos, abajo)
    ("problema", r"\b(suspend\w*|halt(?:s|ed)? (?:operations|production|mining|work)|fatal\w*|accident|injunction|CCAA|"
                 r"bankrupt\w*|cease trade|default|insolven\w*|receivership|strike at|blockade|collapse)\b"),
    ("financiacion", r"\b(rais(?:es|ed|ing)|private placement|placement|bought deal|financing|flow-through|offering|"
                     r"entitlement|rights issue|share purchase plan|capital raising|LIFE offering|convertible (?:notes?|debentures?)|"
                     r"(?:credit|debt|loan) facility|refinanc\w*)\b"),
    ("compra", r"\b(to acquire|acquires?|acquired|acquisition|merger|takeover|arrangement agreement|business combination|"
               r"option agreement|earn-in|divest\w*)\b"),
    ("acuerdo", r"\b(offtake|streaming|stream agreement|royalty agreement|strategic (?:investment|partnership|alliance)|"
                r"joint venture|cornerstone investment|memorandum of understanding|binding agreement)\b"),
    ("contrato", r"\b(?:awarded|award of|wins?|won|secures?|secured)\b[^.;]{0,50}\bcontracts?\b|"
                 r"\bcontracts? (?:award|win|valued|worth)\b"),           # empresas de servicios mineros
    ("programa", PROGRAMA),
    ("descubrimiento", r"\b(discovers|discovered|discovery of|new (?:[\w\-]+ ){0,3}discovery|(?:makes?|made|announces?|confirms?) "
                       r"(?:a |an |the )?(?:new |second |third |significant |major |high-grade |maiden )*(?:[\w\-]+ )?discovery|"
                       r"new (?:[\w\-]+ )?(?:zone|vein|lode)s?)\b|^\W*(?:[\w\-]+ ){0,2}discovery (?:at|on|in|near)\b"),
    ("estudio", r"\b(preliminary economic assessment|PEA|pre-?feasibility|PFS|feasibility study|DFS|BFS|scoping study)\b"),
    ("recursos", r"\b(mineral resource|resource estimate|maiden resource|resource update|mineral reserve|ore reserves?|"
                 r"reserves? (?:update|estimate|increase)|NI 43-101|S-K 1300|JORC (?:resource|compliant)|MRE)\b"),
    ("perforacion", r"\b(drill\w*|intersect\w*|intercept\w*|assays?|channel sampl\w*|trench\w*|grab sampl\w*|hits?|"
                    r"returns? \d|(?:high-grade|assay|drill(?:ing)?|RC|diamond|aircore|sampling|exploration)(?: [\w\-]+){0,2} "
                    r"results)\b|g/t"),
    ("produccion", r"\b(commercial production|first (?:gold|silver|copper|concentrate|ore|lithium) (?:pour|production|shipment)|"
                   r"construction decision|production decision|begins? production|start of production|pours? first|"
                   r"mill start|commissioning|first ore|decline (?:underway|commenced|development)|stoping|"
                   r"(?:mine|underground) development|mining (?:commences|underway|begins)|"
                   r"record (?:\w+ )?production|(?:higher|increased) production)\b"),
    ("permiso", r"\b(permits?|permitting|record of decision|environmental approval|mining lease|exploitation licen[cs]e|"
                r"licen[cs]e (?:granted|approved))\b"),
]
APROBACION = (r"\b(FDA (?:approv\w*|grants? (?:approval|clearance)|clears?|clearance)|(?:receives?|wins?|gets?|granted) "
              r"(?:FDA |EMA |CE |Health Canada |TGA |NMPA |PMDA )?(?:approval|clearance|marking|mark)|marketing authori[sz]ation|"
              r"510\(k\) clearance|CE mark\w*|PMA approval|approv(?:es|ed) by (?:the )?(?:FDA|EMA|European Commission))\b")
EVENTOS_TEC = [
    ("problema", r"\b(recall\w*|complete response letter|CRL|clinical hold|fail(?:s|ed)? to meet|did not meet|"
                 r"miss(?:es|ed) (?:its |the )?primary endpoint|halts? (?:trial|study)|FDA rejects?|refuse to file|"
                 r"discontinu\w* (?:the |its )?(?:trial|study|program|development)|going concern|delist\w*)\b"),
    ("financiacion", r"\b(private placement|public offering|registered direct|offering|financing|at-the-market|"
                     r"convertible (?:notes?|debentures?)|warrants? (?:exercise|inducement))\b"),
    ("compra", r"\b(to acquire|acquires?|acquisition|merger|business combination)\b"),
    ("aprobacion", APROBACION),
    ("regulatorio", r"\b(breakthrough (?:therapy |device )?designations?|fast track|orphan drug|priority review|RMAT|"
                    r"(?:accepts?|accepted|acceptance of) (?:the |its )?(?:NDA|BLA|MAA|sNDA|sBLA|filing|submission|application)|"
                    r"PDUFA|IND (?:clearance|cleared|approval)|clears? (?:the |its )?IND|regulatory pathway|"
                    r"FDA (?:clearance|clears?) [^.]{0,40}(?:trial|study)|Type [ABC] meeting|end-of-phase)\b"),
    ("ensayo", r"\b(phase (?:1|2|3|i|ii|iii|1/2|2/3|2a|2b|3b)\b[^.]{0,80}?(?:results?|data|topline|endpoint|readout)|"
               r"topline (?:results|data)|met (?:its )?primary endpoint|primary endpoint (?:was )?(?:met|achieved)|"
               r"statistically significant)"),
    ("contrato", r"\b(awarded|wins?|won|secures?|secured|selected (?:by|for)|contract|task order|purchase order)\b"),
    ("avance", r"\b(breakthrough|world record|first-ever|world'?s first|record[- ]breaking|achieves?|achieved|"
               r"demonstrates?|milestone|prototype|unveils?)\b"),
    ("patente", r"\b(patents?)\b"),
]
RE_FUTURO = re.compile(r"\b(targeted|targeting|ahead of|towards?|fast-track\w*|work begins|begins|on track|scheduled|planned|"
                       r"plans?|expected|due (?:in|by)|upcoming|push(?:es|ing)?|path to|to define|to deliver|in progress|"
                       r"underway|pending|to commence|by (?:Q[1-4]|year[- ]end|mid|early|late)|needs? to|builds?)\b", re.I)
RE_LEY_TITULO = re.compile(r"\d(?:[.,]\d+)?\s*(?:g/t|%|ppm|oz/t)|\d\s*(?:m|metres|meters)\s*(?:@|at|of|grading)\s*\d", re.I)
VETO = {   # casa la palabra, pero no es eso
    "recursos": lambda s: bool(RE_FUTURO.search(s)),                     # «recurso inicial previsto para noviembre»
    "programa": lambda s: bool(RE_LEY_TITULO.search(s)) and not re.search(r"\bfollow", s, re.I),   # si da leyes, son
    # resultados (salvo «sigue a los 13,5 m con 7,67 g/t»: esas leyes son de antes)
    "estudio": lambda s: bool(RE_FUTURO.search(s)) or (bool(RE_LEY_TITULO.search(s)) and not re.search(
        r"\b(NPV|IRR|after-tax|pre-tax|payback)\b", s, re.I)),      # «sondeos… antes de la PFS»: son resultados
    "aprobacion": lambda s: bool(re.search(r"\b(trial|study|IND|investigational|pathway|alignment|feedback|"
                                           r"designation|clinical hold)\b", s, re.I)),
    "financiacion": lambda s: bool(re.search(r"\bproduct offering|service offering\b", s, re.I)),
}
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
RE_PAL_MIN = re.compile(r"\b(g/t|drill\w*|mineral\w*|mining|miners?|exploration|deposit|ore|claims|propert(?:y|ies)|gold|copper|"
                        r"silver|lithium|uranium|nickel|zinc|rare earths?|graphite|antimony|tungsten|assays?|intercepts?|"
                        r"resource estimate|feasibility|tenements?|prospect)\b", re.I)
RE_PAL_TEC = re.compile(r"\b(FDA|clinical|phase (?:1|2|3)|therap\w*|drug|patients?|oncology|vaccine|semiconductor|chips?|"
                        r"quantum|batter(?:y|ies)|fusion|nuclear|reactor|satellite|spacecraft|defen[cs]e|DoD|Army|Navy|"
                        r"Air Force|missile|drones?|artificial intelligence|robot\w*|hydrogen|biotech\w*|medical device)\b", re.I)
# mejor tramo de perforación: umbral de "importante" (ley × metros) y ley mínima para que cuente sola
UMBRAL_GM = {"oro": 100, "plata": 5000, "cobre": 150, "litio": 30, "uranio": 10, "tierras raras": 20, "níquel": 30, "zinc": 100,
             "cobalto": 5, "antimonio": 50, "wolframio": 20, "estaño": 20}
LEY_MIN = {"oro": 1.0, "plata": 60, "cobre": 0.3, "litio": 0.8, "uranio": 0.05, "tierras raras": 0.15, "níquel": 0.4,
           "zinc": 2.0, "cobalto": 0.05, "antimonio": 0.5, "wolframio": 0.15, "estaño": 0.2}
METAL_LEY = {"au": "oro", "aueq": "oro", "gold": "oro", "ag": "plata", "ageq": "plata", "silver": "plata", "cu": "cobre", "cueq": "cobre",
             "copper": "cobre", "li2o": "litio", "u3o8": "uranio", "treo": "tierras raras", "ni": "níquel", "nieq": "níquel",
             "zn": "zinc", "zneq": "zinc", "co": "cobalto", "sb": "antimonio", "wo3": "wolframio", "sn": "estaño"}
# una cifra entera (sin empezar a mitad de otra): «1,975» son miles; «2,5», decimales
N = r"(?<![\d.,])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,5}(?:[.,]\d{1,3})?)"
METALES_TRAMO = r"(AuEq|Au|gold|AgEq|Ag|silver|CuEq|Cu|copper|Li2O|U3O8|TREO|NiEq|Ni|ZnEq|Zn|Co|Sb|WO3|Sn)"
# Entre los metros y la ley NO puede haber otra cifra: en «26m @ 2.79 g/t Au from 126m depth 1m @ 53.5 g/t Au» la v1
# leía «126 m con 53,5 g/t» (la profundidad con la ley del tramo siguiente). Corregido el 2026-10-12.
RE_TRAMO = [  # (texto, orden de grupos)
    (re.compile(N + r"\s*(?:m|metres|meters)\b[^.;()\d]{0,25}?(?:of|@|at|grading|averaging|assaying|returning|with)\s*" + N +
                r"\s*(g/t|%)\s*" + METALES_TRAMO, re.I), "mL"),
    (re.compile(N + r"\s*(g/t|%)\s*" + METALES_TRAMO + r"\b[^.;()\d]{0,15}?\bover\s*" + N + r"\s*(?:m|metres|meters)\b", re.I),
     "Lm"),
]
RE_VAN = re.compile(r"\b(?:NPV|net present value)[^$€£]{0,60}?(US|C|CA|A|AU)?\$\s?(\d[\d,.]*)\s*(billion|million|B|M)\b", re.I)
RE_TIR = re.compile(r"\b(?:IRR|internal rate of return)[^%]{0,40}?(\d{1,3}(?:\.\d)?)\s*%"
                    r"|(\d{1,3}(?:\.\d)?)\s*%\s*(?:after-tax\s+|pre-tax\s+)?(?:IRR|internal rate of return)", re.I)
# Rutina que no es noticia (fechas de resultados, juntas, dividendos, presentaciones)
RE_RUTINA = re.compile(r"\b(to announce|will announce|announces? (?:date|timing)|to report (?:third|fourth|second|first)|"
                       r"conference call|webcast|to present at|to participate|presents? at|investor (?:day|conference)|"
                       r"annual (?:general |and special )?meeting|AGM|results of (?:the )?annual|declares? (?:quarterly |monthly )?"
                       r"dividend|earnings release|shareholder letter|corporate update call|engages|appoints?|joins? the board)\b", re.I)
# Tampoco son hallazgos: resultados trimestrales, avisos de la bolsa, premios, bufetes buscando accionistas
RE_NO_HALLAZGO = re.compile(r"\b(trading halt|halt lifted|reinstatement to quotation|quarterly (?:activities|report|cash ?flow)|"
                            r"appendix (?:4[a-z]|5b|2a|3[a-z])|annual report|half[- ]year|interim (?:report|results)|"
                            r"financial results|(?:first|second|third|fourth) quarter(?: \d{4})? (?:results|earnings|revenue)|"
                            r"(?:\w+ )?quarter(?:ly)? (?:update|activities|review)|"
                            r"Q[1-4] (?:\d{4} |FY\d{2,4} )?(?:results|earnings|revenue)|earnings (?:call|report)|revenue pace|"
                            r"investor presentation|corporate presentation|cleansing (?:notice|statement)|change of director|"
                            r"director'?s? interest|substantial (?:holder|shareholder)|application for quotation|"
                            r"proposed issue of securities|notice of (?:annual|general|extraordinary)|"
                            r"board (?:changes?|transformation|renewal|restructur\w*|appointments?)|"
                            r"(?:director|chair\w*|CEO|CFO) (?:appointment|resignation|transition|retirement)|"
                            r"of the year|shareholder alert|investor alert|class action|law firm|reminds investors|"
                            r"securities fraud|investigation on behalf)\b", re.I)
# Artículos de opinión de webs de bolsa (no son la empresa contando su noticia)
RE_COMENTARIO = re.compile(r"\?|^(?:how|why|what|can|is|are|should|will|does|here'?s|a path|the case)\b|investors? may|"
                           r"draws? (?:expert )?attention|stock (?:rises|gains|dips|falls|jumps|soars|surges|slides|climbs|drops)|"
                           r"shares? (?:rise|jump|fall|soar|surge|drop|climb|gain)|slideshow|analysts?|valuation|price target|"
                           r"discusses|deep dive|outlook for|could|might|builds? towards?|needs? to deliver|top \d+|weekly|roundup|"
                           r"stocks? to (?:buy|watch)|^this\b|just got|heats? up|bull run|the next\b|\bjumps?\b|\bsoars?\b|"
                           r"\bsurges?\b|\bplunges?\b|in focus|investors? (?:cheer|await|eye)|stock hits|52-week|"
                           r"all-time high|record high|hits (?:fresh|new) (?:high|record)", re.I)
RE_PROMO = re.compile(r"(USA News Group|FN Media Group|Market ?News ?Updates|Equity[- ]Insider|Investorideas|InvestorIdeas|"
                      r"Streetwise Reports|sponsored|paid (?:advertisement|promotion|for by)|(?:is|was|been) compensated|"
                      r"disclaimer:|this article is a paid)", re.I)
# Título de comunicado: «Empresa (BOLSA: X) verbo...». El verbo en 3.ª persona o participio: «drill» suelto es un sustantivo.
VERBOS_ANUNCIO = (r"announces|reports|intersects|intercepts|drills|discovers|receives|completes|signs|wins|awarded|secures|"
                  r"achieves|unveils|launches|expands|confirms|delivers|commences|begins|provides|files|granted|obtains|"
                  r"enters|closes|identifies|extends|returns|hits|encounters|defines|makes|publishes|releases|approved|"
                  r"approves|gets|grants|starts|initiates|increases|doubles|triples|upgrades|acquires|agrees|intersected|cuts|"
                  r"drilled|outlines|mobilises|mobilizes|stakes|demonstrates|selected|posts|produces|pours|declares|upsizes|"
                  r"raises|prices|executes|finalizes|finalises|lodges|submits|surpasses|exceeds|sets|returned|encountered|"
                  r"discovered|confirmed|expanded|extended|completed|received|secured|signed|won|commenced|started|initiated|"
                  r"identified|defined|published|filed|obtained|achieved|demonstrated|delivered|produced|poured|raised|"
                  r"priced|executed|lodged|submitted|unveiled|launched|reaches|reached|advances|advanced|strikes|struck|"
                  r"lifts|boosts|adds|grows|shows|yields|reveals|highlights|tests|steps|kicks|nears|inks|lands|scores|"
                  r"clinches|wraps|resumes|restarts|upgraded|added|grew|showed|yielded|revealed|highlighted|tested|"
                  r"to acquire|to raise|to begin|to commence|to start|to drill|to supply|to deliver|to develop|enters into")
# el nombre: 1.ª palabra libre («argenx»), las demás con mayúscula; el verbo, en mayúsculas o minúsculas
RE_ANUNCIO = re.compile(r"^([A-Za-z0-9][\w.&'’\-]*(?:\s+(?:[A-Z0-9&][\w.&'’\-]*|and|of|de|du|la|del|the))*?)"
                        r"\s+(?:\([^)]*\)\s*)*(?i:" + VERBOS_ANUNCIO + r")\b")
RE_IMPORTE = re.compile(r"(US|C|CA|A|AU)?\$\s?(\d[\d,.]*)\s*(billion|million|bn|m)\b", re.I)
GRANDES = re.compile(r"\b(Glencore|Rio Tinto|BHP|Anglo American|Barrick|Newmont|Teck|Freeport|South32|Vale|Fortescue|"
                     r"Albemarle|SQM|Tesla|Ford|General Motors|GM|Toyota|Panasonic|LG Energy|CATL|POSCO|Sumitomo|Mitsubishi|"
                     r"Wheaton|Franco-Nevada|Royal Gold|Triple Flag|Agnico|Kinross|Zijin|Hanwa|Trafigura|Mercuria|"
                     r"Department of (?:Energy|Defense|War)|DOE|EXIM|Pentagon|Lockheed|Boeing|Microsoft|Nvidia|Apple|Amazon)\b")

# Calidad de la fuente. of = la propia empresa (agencia o bolsa); co = copia del comunicado en otra web;
# re = resumen automático o web de bolsa que reescribe; pe = prensa u opinión
OFICIALES = ("newsfile", "pr newswire", "prnewswire", "globenewswire", "globe newswire", "business wire", "businesswire",
             "access newswire", "accesswire", "accessnewswire", "cnw", "newswire.ca", "cision", "asx", "news direct",
             "newsdirect")
RESUMENES = ("kalkine", "tipranks", "ad hoc news", "pluang", "simply wall", "simplywall", "marketbeat", "zacks", "seeking alpha",
             "motley fool", "fool.com", "investorplace", "gurufocus", "directorstalk", "discovery alert", "finimize", "invezz",
             "stock titan", "stocktitan", "quiver", "grafa", "ainvest", "investing.com", "aktiencheck", "finanznachrichten",
             "stockhead", "market index", "marketindex", "the market online", "themarketonline")
RANGO_NIVEL = {"of": 3, "co": 2, "re": 1, "pe": 0}

# nombres que no son una empresa (sitios, palabras de titular) y primeras palabras demasiado corrientes
NO_EMPRESA = set("""why how is are can what this these new the a an major significant top best high-grade first breaking update
exclusive report analysis watch nevada alaska ontario quebec yukon nunavut manitoba saskatchewan labrador newfoundland australia
australian canada canadian chile chilean peru mexico mexican china chinese brazil argentina africa african ghana mali idaho arizona
montana wyoming utah texas colorado finland sweden norway greenland us usa america american europe european india japan korea
germany france uk britain british global world gold silver copper lithium uranium nickel zinc rare critical mining miners explorer
explorers investors stocks shares market markets fda nasa army navy pentagon government state federal astronomers scientists
researchers study studies lasers western northern southern eastern queensland victoria tasmania pilbara kalgoorlie drilling drill
assays results nasdaq nyse tsx tsxv asx cse otc otcqb""".split())
COMUNES_1 = set("""first new great golden gold silver copper american canadian western northern southern eastern global united
international national royal blue black white red green pacific atlantic north south east west big rio st san santa mount mt lake
rock star sun nova alpha omega titan atlas summit pinnacle frontier pioneer patriot liberty eagle falcon phoenix orion apollo
advanced applied integrated general standard premier prime core true pure clean bright smart digital quantum nano bio gen pharma
therapeutics resources minerals metals mining energy lithium uranium critical strategic battery power tech technologies systems
solutions""".split())
GENERICAS = {"inc", "incorporated", "corp", "corporation", "ltd", "limited", "plc", "sa", "se", "ag", "nv", "nl", "co", "company",
             "llc", "lp", "holdings", "holding", "group", "the", "and", "of", "de", "la", "del", "ab", "asa", "oyj", "spa", "pty",
             "tbk", "bhd"}


def norm_num(x):
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", x):
        return float(x.replace(",", ""))                  # 1,975 = mil novecientos setenta y cinco
    return float(x.replace(",", "."))


def importe_musd(m):
    """$ de un RE_IMPORTE en millones (en su moneda)."""
    v = float(m.group(2).replace(",", ""))
    return v * (1000 if m.group(3).lower() in ("billion", "bn") else 1)


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


def tipo_de(eventos, texto):
    for nombre, rx in eventos:
        if re.search(rx, texto, re.I) and not VETO.get(nombre, lambda s: False)(texto):
            return nombre
    return None


def todos(rx_lista, t, flags=re.I):
    return [n for n, rx in rx_lista if re.search(rx, t, flags)]


def tickers_de(t):
    """Tickers en el orden en que aparecen (el de la empresa suele ir el primero, tras su nombre)."""
    vistos, out = set(), []
    for b, s in RE_TICKER.findall(t[:3000]):
        b = "NASDAQ" if b == "Nasdaq" else "TSXV" if b == "TSX-V" else b
        if (b, s) not in vistos:
            vistos.add((b, s))
            out.append({"b": b, "s": s})
    return out[:5]


def empresa_de(txt):
    """Nombre de la empresa: lo que va justo antes del primer «(BOLSA: TICKER)»."""
    m = re.search(r"([A-Z][\w.&,'’\- ]{2,80}?)\s*,?\s*\(\s*" + BOLSAS + r"\s*:", txt[:3000])
    if m:
        nom = re.split(r"\s[-–—]{1,2}\s|\)\s*-\s*|/PRNewswire/|/CNW/|Newsfile Corp\.?\s*-[^)]*\)\s*-?\s*", m.group(1))[-1]
        nom = re.split(r"(?<=[A-Za-z0-9]{3})\.\s+(?=[A-Z])", nom)[-1]   # «…for November 2026. Magnum Mining» -> «Magnum Mining»
        nom = re.sub(r"^(?:[A-Z]\.\s+)+", "", nom)                      # «…$240M. Mastermyne» -> «Mastermyne»
        nom = re.sub(r"^(?:and|the|The|announces?|today|\W)+\s*", "", nom).strip(" ,.-")
        if 2 < len(nom) < 70:
            return nom
    return None


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
    """Empresa al principio de un titular de comunicado («Pfizer’s TUKYSA Regimen Receives…» -> «Pfizer»)."""
    m = RE_ANUNCIO.match(re.sub(r"[®™]", "", ti))
    if not m:
        return None
    n = m.group(1)
    n = re.split(r"[’']s\b", n)[0]                         # el posesivo: la empresa es lo de antes
    n = re.sub(r"\s*\([^)]*\)\s*$", "", n).strip(" ,.-:")
    w = n.split()
    for i in range(2, len(w)):
        if re.match(r"(?:Infill|Drilling|Drill|Program|Results?|Phase|Update|Resource|Assays?|Exploration|Project|Explorer|"
                    r"Step-out|Maiden|Stock|Shares)$", w[i], re.I):
            n = " ".join(w[:i])
            break
    return n if 2 < len(n) <= 60 and len(n.split()) <= 6 else None


def fichas(n):
    n = unicodedata.normalize("NFKD", n or "").encode("ascii", "ignore").decode().lower()
    n = re.sub(r"['’]s\b", "", n)
    return [w for w in re.split(r"[^a-z0-9&]+", n) if w and w not in GENERICAS]


def nombre_valido(n):
    """¿Puede ser el nombre de una empresa? (no «Nevada», «Why Is…», «High-Grade Assays»)."""
    f = fichas(n)
    if not f or len(f) > 6:
        return False
    if f[0] in ("why", "how", "is", "are", "can", "what", "this", "these", "a", "an", "the"):
        return False
    return not all(x in NO_EMPRESA or x.isdigit() for x in f)


def mismo_nombre(a, b, sym=""):
    """¿El nombre de la noticia (a) y el oficial del ticker (b) son la misma empresa?"""
    A, B = fichas(a), fichas(b)
    if not A or not B:
        return False
    s = re.sub(r"[^a-z0-9]", "", (sym or "").split(".")[0].lower())
    if len(s) >= 3 and s not in COMUNES_1 and s not in NO_EMPRESA and (s in A or s == "".join(A)):
        return True                                       # «RTX Raytheon» y RTX
    if len(set(A) & set(B)) >= 2:
        return True
    raro = A[0] not in COMUNES_1 and A[0] not in NO_EMPRESA         # «Nevada» no basta para ser Nevada King Gold
    if A[0] == B[0] and raro and len(A[0]) >= 3:
        return True
    return len(A) == 1 and A[0] in B and raro and len(A[0]) >= 4


SUFIJOS = re.compile(r"(?:,?\s+(?:Inc|Incorporated|Corp|Corporation|Ltd|Limited|plc|S\.?A|SE|AG|N\.?V|NL|Co|Company|LLC|"
                     r"L\.?P|Holdings?|Group)\.?)+\s*$", re.I)


def nombre_corto(n):
    n = titulo_nombre(re.sub(r"[®™]", "", n or "").strip())
    n2 = SUFIJOS.sub("", n).strip(" ,.-")
    return n2 if len(n2) >= 2 else n


def titulo_nombre(n):
    """«HORIZON GOLD LIMITED» -> «Horizon Gold Limited» (las siglas cortas se quedan: IGO, BHP)."""
    if not n or not n.isupper():
        return n
    return " ".join(w if (len(w) <= 3 and w.isalpha() and w not in ("THE", "AND", "OF", "NEW", "BIG", "RED", "ONE", "GEO", "BIO"))
                    else w.capitalize() for w in n.split())


def nivel_fuente(fu, ti, asx=False):
    f = (fu or "").lower()
    if asx or any(a in f for a in OFICIALES):
        return "of"
    if any(a in f for a in RESUMENES):
        return "re"
    if RE_COMENTARIO.search(ti):
        return "pe"
    return "co" if RE_ANUNCIO.match(re.sub(r"\([^)]*\)", " ", ti).strip()) else "pe"


def inferir_grupo(t):
    tm = len(RE_PAL_MIN.findall(t[:2500])) + 2 * len(RE_PAL_MIN.findall(t[:300]))
    tt = len(RE_PAL_TEC.findall(t[:2500])) + 2 * len(RE_PAL_TEC.findall(t[:300]))
    if max(tm, tt) < 2:
        return None
    return "min" if tm >= tt else "tec"


def clasifica(ti, txt, grupo, fuente="", google=False, asx=None):
    """Devuelve el hallazgo clasificado (dict) o None si no interesa. asx = datos oficiales del anuncio (ASX)."""
    ti = re.sub(r"\s+", " ", html.unescape(ti or "")).strip()
    if not ti or RE_RUTINA.search(ti) or RE_NO_HALLAZGO.search(ti):
        return None
    txt = txt or ""
    t = f"{ti}. {txt}"
    if asx:
        nombre, tks = asx["nombre"], []
    else:
        tks = tickers_de(t)
        nombre = (empresa_de(t) if tks and not google else None) or nombre_titulo(ti) or (empresa_de(t) if tks else None) or ""
    nombre = limpia_nombre(nombre)
    ti_ev = re.sub(re.escape(nombre), " ", ti, flags=re.I) if len(nombre) > 3 else ti
    if asx:                                   # en la ASX el titular suele llevar solo la 1.ª palabra del nombre
        ti_ev = re.sub(r"^\s*" + re.escape(nombre.split()[0]) + r"\b", " ", ti_ev, flags=re.I) if nombre else ti_ev
    ti_ev = re.sub(r"\bDiscovery\s+(?:Mining|Silver|Minerals|Metals|Gold|Resources|Energy|Corp\w*|Ltd|Inc|Ventures|Harbour|Alert)\b",
                   " ", ti_ev)
    if grupo is None:
        grupo = inferir_grupo(t)
        if grupo is None:
            return None
    if grupo == "min" or (RE_PAL_MIN.search(t[:3000]) and re.search(r"\b(g/t|drill|mineral|mining|exploration|deposit|ore|claims|property)\b",
                                                                    t, re.I) and not re.search(r"\b(FDA|clinical|patients)\b", t)):
        grupo = "min"
        eventos = EVENTOS_MIN
        etiquetas = todos(MINERALES, t[:4000], 0) or todos(MINERALES, t[:4000])
    else:
        grupo = "tec"
        eventos = EVENTOS_TEC
        etiquetas = todos(CAMPOS, t[:3000], 0)
    resto = re.sub(re.escape(nombre), " ", txt[:1500], flags=re.I) if len(nombre) > 3 else txt[:1500]
    # un «problema» solo si lo dice el titular (el cuerpo de un informe habla de paradas por lluvia, de seguridad...)
    tipo = tipo_de(eventos, ti_ev)
    del_texto = not tipo
    if del_texto:
        tipo = tipo_de([e for e in eventos if e[0] != "problema"], resto)
    if not tipo:
        return None
    h = {"g": grupo, "k": tipo, "et": etiquetas[:4], "ti": ti, "em": nombre or ti[:40]}
    if del_texto:
        h["tx"] = 1          # el titular no dice qué es: el tipo sale del cuerpo y solo cuentan las cifras
    if tks:
        h["tk"] = tks
    imp = False
    cuerpo = t[:8000]
    importe = None
    m = RE_IMPORTE.search(ti) or RE_IMPORTE.search(cuerpo[:2500])
    if m:
        importe = importe_musd(m)
        mon = (m.group(1) or "US").upper()
        h["imp_usd"] = round(importe, 1)
        if mon not in ("US", ""):
            h["imp_mon"] = {"C": "CAD", "CA": "CAD", "A": "AUD", "AU": "AUD"}.get(mon, "USD")
    if grupo == "min":
        tr = mejor_tramo(ti) or mejor_tramo(cuerpo[:6000])
        if tr and tipo in ("perforacion", "descubrimiento"):
            metal, metros, ley, uni, gm, rel, met = tr
            h["tr"] = {"m": round(metros, 1), "l": round(ley, 3), "u": uni, "x": met, "met": metal, "gm": round(gm, 1),
                       "rel": round(rel, 2)}
            imp = rel >= 1 and (ley >= LEY_MIN.get(metal, 0) or rel >= 2)
        van, tir = RE_VAN.search(cuerpo), RE_TIR.search(cuerpo)
        if tipo == "estudio":
            h["fase"] = ("fs" if re.search(r"\b(feasibility study|DFS|BFS)\b", ti, re.I) and not re.search(r"pre-?feasibility", ti, re.I)
                         else "pfs" if re.search(r"\b(pre-?feasibility|PFS)\b", ti, re.I) else "pea")
            if van or tir:
                h["eco"] = {}
                if van:
                    v = norm_num(van.group(2).replace(",", "")) * (1000 if van.group(3).lower() in ("billion", "b") else 1)
                    h["eco"]["van"] = round(v, 1)
                    h["eco"]["mon"] = (van.group(1) or "US").upper()
                if tir:
                    h["eco"]["tir"] = float(tir.group(1) or tir.group(2))
                imp = True
        if tipo == "descubrimiento" and (not h.get("tr") or h["tr"]["rel"] >= 0.5):
            imp = True
        if tipo == "recursos":
            if re.search(r"\b(maiden|initial|first)\b", ti, re.I):
                h["rini"] = 1
                imp = True
            elif re.search(r"\b(increase[sd]?|expan\w+|doubles?|upgrade[sd]?|grows?|growth)\b", ti, re.I):
                h["rsube"] = 1
                imp = True
        if tipo == "permiso" and re.search(r"\b(record of decision|final|key|major|main|mining permit|environmental impact|"
                                          r"construction permit|exploitation|mining lease|granted)\b", ti, re.I):
            h["clave"] = 1
            imp = True
        if tipo == "produccion" and not re.search(r"\b(record|higher|increased)\b", ti, re.I):
            h["hito"] = 1                         # primera producción, decisión de construir, puesta en marcha
            imp = True
        if tipo == "problema":
            imp = True
        if tipo == "compra" and importe and importe >= 50:
            imp = True
        if tipo == "acuerdo" and GRANDES.search(t[:2500]):
            h["grande"] = 1
            imp = True
        if tipo == "contrato" and importe and importe >= 100:
            imp = True
    else:
        if tipo == "aprobacion":
            if re.search(r"\b(manufactur\w*|additional (?:sizes?|strengths?|doses?|presentations?)|new (?:formulation|presentation|"
                         r"vial|syringe|packaging)|label (?:update|change)|shelf[- ]life|packaging)\b", ti, re.I):
                h["menor"] = 1
            else:
                imp = True
        if tipo == "problema":
            imp = True
        if tipo == "ensayo" and re.search(r"\b(met (?:its )?primary endpoint|statistically significant|positive)\b", t[:3000], re.I) \
                and not re.search(r"\b(fail\w*|did not|missed)\b", ti, re.I):
            sin_planes = re.sub(r"\b(?:plans?|planned|planning|to (?:begin|start|initiate|advance|move)|initiat\w*|start\w*|"
                                r"advanc\w* (?:in)?to|mov\w* (?:in)?to|toward\w*)\b[^.,;]{0,30}?\bphase \w+", " ", ti, flags=re.I)
            fase = re.search(r"\bphase (3|iii|2b|2|ii)\b", sin_planes, re.I)   # la fase con resultados, no la que planea
            if fase:
                h["fpos"] = {"iii": "3", "ii": "2"}.get(fase.group(1).lower(), fase.group(1).lower())
                imp = h["fpos"] in ("3", "2b")
        if tipo == "avance" and re.search(r"\b(world record|first-ever|world'?s first|breakthrough)\b", ti, re.I) and etiquetas:
            h["record"] = 1
            imp = True
        if tipo == "contrato" and importe:
            imp = importe >= 100
    # De Google solo cuenta lo que es un comunicado de la empresa (agencia o título «Empresa anuncia...»),
    # no los artículos de webs de bolsa sobre ella
    h["nv"] = nivel_fuente(fuente, ti, bool(asx))
    if h["nv"] == "pe" or (google and RE_COMENTARIO.search(ti)):
        h["pr"] = True                  # prensa / opinión
        imp = False
    if RE_PROMO.search(t[:40000]) or RE_PROMO.search(fuente):
        h["promo"] = h["pr"] = True     # publicidad pagada de un promotor de acciones: nunca avisa
        imp = False
    if tipo in ("programa", "financiacion", "patente", "regulatorio"):
        imp = False
    if del_texto:   # sin el titular, «importante» solo con cifras: un buen tramo o un estudio con su valor
        for c in ("rini", "rsube", "clave", "hito", "grande", "record"):
            h.pop(c, None)
        imp = bool((h.get("tr") and h["tr"]["rel"] >= 1 and (h["tr"]["l"] >= LEY_MIN.get(h["tr"]["met"], 0) or h["tr"]["rel"] >= 2))
                   or (tipo == "estudio" and h.get("eco", {}).get("van")))
    if imp:
        h["imp0"] = True                # importante por las reglas; luego hace falta el ticker comprobado y la nota
    return h


# ── red ──────────────────────────────────────────────────────────────────────
import requests  # noqa: E402

SES = requests.Session()
CRUMB = {"v": None}


def pedir(url, intentos=2, timeout=30, cab=None):
    for k in range(intentos):
        try:
            r = SES.get(url, headers=cab or NAV, timeout=timeout)
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


def texto_pdf(clave):
    """Primeras 3 páginas del PDF de un anuncio de la ASX (ahí van los «highlights» con las cifras)."""
    ruta = f"{PDFS}/{re.sub(r'[^A-Za-z0-9-]', '', clave)}.txt"
    try:
        with open(ruta, encoding="utf-8") as f:
            return f.read()
    except OSError:
        pass
    t = ""
    try:
        r = SES.get(ASX_PDF.format(k=clave), headers=NAV, timeout=40, stream=True)
        if r.status_code == 200:
            datos = b""
            for trozo in r.iter_content(65536):
                datos += trozo
                if len(datos) > 15_000_000:
                    datos = b""
                    break
            if datos:
                os.makedirs(PDFS, exist_ok=True)
                tmp = ruta[:-4] + ".pdf"
                with open(tmp, "wb") as f:
                    f.write(datos)
                p = subprocess.run(["pdftotext", "-l", "3", tmp, "-"], capture_output=True, timeout=60)
                os.remove(tmp)
                t = re.sub(r"For personal use only", " ", p.stdout.decode("utf-8", "ignore"))
                t = re.sub(r"\s+", " ", t)[:30000]
    except (requests.RequestException, OSError, subprocess.SubprocessError):
        return ""
    try:
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(t)
    except OSError:
        pass
    return t


BOLSA_YAHOO = {"NYQ": "NYSE", "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "ASE": "NYSE American", "TOR": "TSX",
               "VAN": "TSXV", "CNQ": "CSE", "NEO": "NEO", "ASX": "ASX", "LSE": "LSE", "PNK": "OTC", "OQX": "OTCQX", "OQB": "OTCQB"}


def ysym(tk):
    """Símbolo de Yahoo de un ticker {"b": bolsa, "s": símbolo}."""
    if tk.get("y"):
        return tk["y"]
    b, s = tk["b"], tk["s"]
    if b in SUFIJO_YAHOO:
        return s.replace(".", "-") + SUFIJO_YAHOO[b]
    return s.replace(".", "-")


def buscar_ticker(nombre, cache, sectores=None):
    """Ticker de una empresa por su nombre (buscador de Yahoo), probando el nombre cada vez más corto.
    Solo se acepta si el nombre oficial casa (mismo_nombre) y, si se sabe, el sector."""
    if not nombre or not nombre_valido(nombre):
        return None
    if nombre in cache:
        return cache[nombre] or None
    pal = nombre.split()
    tk = None
    for n in range(min(4, len(pal)), 0, -1):
        q = " ".join(pal[:n])
        f = fichas(q)
        if not f or (n == 1 and (f[0] in NO_EMPRESA or f[0] in COMUNES_1 or len(f[0]) < 3)):
            break
        try:
            j = SES.get("https://query1.finance.yahoo.com/v1/finance/search?q=" + urllib.parse.quote(q)
                        + "&quotesCount=8&newsCount=0", headers=NAV, timeout=20).json()
        except Exception:
            break
        time.sleep(0.4)

        def nom(x):
            return x.get("longname") or x.get("shortname") or ""
        cand = [x for x in j.get("quotes", []) if x.get("quoteType") == "EQUITY" and x.get("exchange") in BOLSA_YAHOO]
        if n == 1:   # una sola palabra («KBR», «IonQ», «Pfizer»): la 1.ª palabra del nombre oficial, exacta
            cand = [x for x in cand if fichas(nom(x))[:1] == f[:1]
                    and (len(f[0]) >= 4 or x.get("symbol", "").split(".")[0].upper() == q.upper())]
        else:
            cand = [x for x in cand if all(w in fichas(nom(x)) for w in f) and mismo_nombre(q, nom(x), x.get("symbol", ""))]
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


def crumb():
    """Yahoo pide una cookie + 'crumb' para las cotizaciones en bloque."""
    try:
        SES.get("https://fc.yahoo.com", headers=NAV, timeout=20)
        r = SES.get("https://query1.finance.yahoo.com/v1/test/getcrumb", headers=NAV, timeout=20)
        CRUMB["v"] = r.text.strip() if r.ok and len(r.text.strip()) < 40 else None
    except requests.RequestException:
        CRUMB["v"] = None
    return CRUMB["v"]


def cotizaciones(syms):
    """Cotización, valor en bolsa, volumen y nombre oficial de muchas acciones a la vez (Yahoo v7, de 40 en 40)."""
    out = {}
    syms = [s for s in dict.fromkeys(syms) if s]
    for k in range(0, len(syms), 40):
        lote = syms[k:k + 40]
        for _ in range(2):
            if not CRUMB["v"] and not crumb():
                return out
            try:
                r = SES.get("https://query2.finance.yahoo.com/v7/finance/quote?symbols=" + urllib.parse.quote(",".join(lote))
                            + "&crumb=" + urllib.parse.quote(CRUMB["v"]), headers=NAV, timeout=25)
                if r.status_code in (401, 403):
                    CRUMB["v"] = None
                    continue
                for q in (r.json().get("quoteResponse") or {}).get("result") or []:
                    out[q.get("symbol")] = q
                break
            except (requests.RequestException, ValueError):
                time.sleep(2)
        time.sleep(0.3)
    return out


def grafico(sym, rango, intervalo):
    try:
        j = SES.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range={rango}"
                    f"&interval={intervalo}&includeAdjustedClose=true", headers=NAV, timeout=25).json()
        return j["chart"]["result"][0]
    except Exception:
        return None


# ── estado, mercado y notas ──────────────────────────────────────────────────
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


def iso(d):
    return d.isoformat(timespec="seconds")


def ts(s):
    return dt.datetime.fromisoformat(s).timestamp()


def log(*a):
    print(ahora().strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def r4(x, d=4):
    if x is None or isinstance(x, (bool, complex)) or not math.isfinite(x):
        return None
    return 0 if x == 0 else float(f"{x:.{d}g}")


def clave_titulo(ti):
    return hashlib.sha1(re.sub(r"[^a-z0-9]+", "", ti.lower())[:90].encode()).hexdigest()[:16]


def a_usd(mk, valor, mon):
    """Valor en dólares con el cambio de Yahoo (las de Londres y Johannesburgo cotizan en céntimos)."""
    if valor is None:
        return None
    mon = mon or "USD"
    if mon in MENOR:
        mon = MENOR[mon][0]
    if mon == "USD":
        return valor
    r = (mk.get("fx") or {}).get(mon)
    return valor * r if r else None


def actualizar_cotizaciones(mk, q):
    """Guarda en mk["q"] lo que hace falta de cada cotización (y los cambios de moneda)."""
    fx = mk.setdefault("fx", {})
    for s, x in q.items():
        if s.endswith("USD=X") and x.get("regularMarketPrice"):
            fx[s[:3]] = x["regularMarketPrice"]
    for s, x in q.items():
        if s.endswith("=X"):
            continue
        mon = x.get("currency") or "USD"
        div = MENOR.get(mon, (mon, 1))[1]
        mc = a_usd(mk, x.get("marketCap"), mon)
        v3, v10 = x.get("averageDailyVolume3Month"), x.get("averageDailyVolume10Day")
        mk.setdefault("q", {})[s] = {
            "n": x.get("longName") or x.get("shortName") or "", "tp": x.get("quoteType"), "ex": x.get("fullExchangeName"),
            "mon": MENOR.get(mon, (mon, 1))[0], "px": r4((x.get("regularMarketPrice") or 0) / div) or None,
            "d1": r4((x.get("regularMarketChangePercent") or 0) / 100, 3), "mc": r4(mc, 3),
            "vol": x.get("regularMarketVolume"), "v3": v3, "vr": r4(v10 / v3, 3) if v3 and v10 else None,
            "c52": r4((x["fiftyTwoWeekChangePercent"] or 0) / 100, 3) if x.get("fiftyTwoWeekChangePercent") is not None else None,
            "t": iso(ahora())}


def verificar(h, mk):
    """Deja en h solo los tickers cuyo nombre oficial casa con el de la noticia; elige el principal (ys)."""
    q = mk.get("q", {})
    if h.get("tkv") == 2:                                  # anuncio oficial de la bolsa: el ticker es seguro
        x = q.get(h["ys"])
        if x and x.get("n"):
            h["en"] = nombre_corto(x["n"])
        h["em"] = h.get("en") or h["em"]
        return
    buenos, desconocidos, malos = [], [], []
    for t in h.get("tk", []):
        s = ysym(t)
        x = q.get(s)
        if not x or x.get("tp") not in ("EQUITY", None):
            desconocidos.append(t)
        elif mismo_nombre(h["em"], x.get("n", ""), t["s"]):
            t["y"] = s
            buenos.append(t)
        else:
            malos.append(t)
    orden = lambda t: PREFERIDA.index(t["b"]) if t["b"] in PREFERIDA else 99   # noqa: E731
    if buenos:
        buenos.sort(key=orden)
        h["tk"] = (buenos + desconocidos)[:4]
        h["tkv"], h["ys"] = 1, buenos[0]["y"]
        h["en"] = nombre_corto(q[h["ys"]]["n"])
        h["em"] = h["en"]
    elif desconocidos and not malos:
        h["tk"] = sorted(desconocidos, key=orden)[:4]      # Yahoo no la conoce: queda «sin comprobar»
        h["tkv"] = 0
        h.pop("ys", None)
    else:
        h.pop("tk", None)                                  # el ticker era de otra empresa (un socio, el comprador...)
        h.pop("ys", None)
        h["tkv"] = -1


BASE_IMP = {"produccion": 70, "estudio": 55, "recursos": 45, "descubrimiento": 55, "perforacion": 30, "permiso": 38,
            "acuerdo": 40, "compra": 40, "financiacion": 15, "programa": 12, "problema": 65,
            "aprobacion": 65, "regulatorio": 40, "ensayo": 35, "avance": 28, "contrato": 35, "patente": 15}


def nota_importancia(h, mk):
    """0-100: el tipo de noticia, sus cifras, lo que pesan frente al tamaño de la empresa y la fuente."""
    k = h["k"]
    s = BASE_IMP.get(k, 20)
    q = (mk.get("q") or {}).get(h.get("ys") or "") or {}
    mc = q.get("mc")
    tr = h.get("tr")
    if tr:
        s += min(40, 20 * math.log2(1 + tr["rel"]))
        if tr["l"] < LEY_MIN.get(tr["met"], 0) and tr["rel"] < 2:
            s -= 8                                           # tramo largo de ley muy baja
    eco = h.get("eco") or {}
    if k == "estudio":
        s += {"fs": 10, "pfs": 5}.get(h.get("fase"), 0)
        if eco.get("van") and mc:
            van = a_usd(mk, eco["van"] * 1e6, {"C": "CAD", "CA": "CAD", "A": "AUD", "AU": "AUD"}.get(eco.get("mon"), "USD"))
            if van:
                r = van / mc
                s += 25 if r >= 3 else 15 if r >= 1 else 8 if r >= 0.5 else 0
        if (eco.get("tir") or 0) >= 30:
            s += 5
    if k == "recursos":
        s += 15 if h.get("rini") else 10 if h.get("rsube") else 0
    if k == "produccion" and not h.get("hito"):
        s -= 25                                              # «producción récord»: buena marcha, no un hito
    if k == "permiso" and h.get("clave"):
        s += 20
    if k == "acuerdo" and h.get("grande"):
        s += 15
    val = h.get("imp_usd")
    if val and k in ("contrato", "compra", "acuerdo", "financiacion"):
        val_usd = a_usd(mk, val * 1e6, h.get("imp_mon", "USD"))
        if val_usd and mc:
            r = val_usd / mc
            if k == "financiacion":
                s += 12 if r >= 0.15 else 5 if r >= 0.05 else 0     # mucho dinero nuevo = mucha dilución
            else:
                s += 35 if r >= 0.25 else 25 if r >= 0.1 else 12 if r >= 0.03 else 5 if r >= 0.01 else (-10 if r < 0.005 else 0)
        elif val_usd:
            s += 20 if val_usd >= 1e9 else 10 if val_usd >= 1e8 else 0
    if k in ("aprobacion", "regulatorio"):
        if h.get("menor"):
            s -= 20
        if mc:
            s += 15 if mc < 2e9 else (-20 if mc >= 50e9 else -12 if mc >= 20e9 else 0)   # a una gigante le mueve poco
    if k == "ensayo":
        s += {"3": 35, "2b": 25, "2": 15}.get(h.get("fpos"), 0)
        if mc and mc >= 50e9:
            s -= 10
    if k == "avance" and h.get("record"):
        s += 20 + (5 if mc and mc < 2e9 else 0)
    if k in ("perforacion", "descubrimiento", "programa") and mc and mc >= 5e9:
        s -= 10                                              # en una gran productora, un buen sondeo es rutina
    s += {"of": 5, "co": 3, "re": -5, "pe": -10}.get(h.get("nv"), 0)
    if h.get("tx"):
        s -= 5                                               # el titular no decía qué era
    if not h.get("tk"):
        s -= 15
    elif h.get("tkv", 0) <= 0:
        s -= 5
    if h.get("promo"):
        s = 0
    return max(0, min(100, round(s)))


def es_importante(h):
    """Aviso por email: las reglas, el ticker comprobado, no es opinión y la nota llega."""
    return bool(h.get("imp0") and h.get("tkv", 0) >= 1 and not h.get("pr") and (h.get("si") or 0) >= NOTA_EMAIL)


def barras(res):
    """[cierre (unix), precio, volumen] de cada sesión (la hora de cierre = apertura + duración de la sesión)."""
    t = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    tp = (res.get("meta") or {}).get("currentTradingPeriod", {}).get("regular", {})
    dur = (tp.get("end", 0) - tp.get("start", 0)) or 23400
    out = []
    for a, c, v in zip(t, q.get("close") or [], q.get("volume") or []):
        if c:
            out.append([a + dur, r4(c, 6), int(v or 0)])
    return out


def crecimiento(res):
    """Rentabilidad de 1 año y anualizada de 3 y 5 años (con dividendos) y la curva del último año (semanal)."""
    t = res.get("timestamp") or []
    ind = res.get("indicators") or {}
    c = ((ind.get("adjclose") or [{}])[0].get("adjclose")) or []
    if not c or any(x is not None and x <= 0 for x in c):   # Yahoo da a veces ajustados negativos: mejor el cierre
        c = ((ind.get("quote") or [{}])[0].get("close")) or []
    pares = [(a, x) for a, x in zip(t, c) if x and x > 0]
    if len(pares) < 8:
        return None
    t1, p1 = pares[-1]

    def precio_hace(anios):
        obj = t1 - anios * 365.25 * 86400
        if pares[0][0] > obj + 21 * 86400:
            return None                                    # no hay tanta historia
        return min(pares, key=lambda z: abs(z[0] - obj))[1]
    p_1, p_3, p_5 = precio_hace(1), precio_hace(3), precio_hace(5)
    anio = [x for a, x in pares if a >= t1 - 366 * 86400]
    return {"r1a": r4(p1 / p_1 - 1, 3) if p_1 else None,
            "r3a": r4((p1 / p_3) ** (1 / 3) - 1, 3) if p_3 else None,
            "r5a": r4((p1 / p_5) ** (1 / 5) - 1, 3) if p_5 else None,
            "an": round((t1 - pares[0][0]) / (365.25 * 86400), 1),
            "sp": [r4(x, 4) for x in anio[-53:]], "sp0": int(max(pares[0][0], t1 - 366 * 86400)), "sp1": int(t1)}


def refrescar_mercado(est, mk, limite):
    """Cotizaciones en bloque (cada 30 min) y gráficos por acción: diario (6 meses, para el FOMO; cada hora si la
    noticia es de los últimos 10 días) y semanal (5 años, para el crecimiento; cada día). Como mucho `limite`
    gráficos por pasada: la urgencia manda (sin datos > noticias recientes > lo demás)."""
    lim30 = iso(ahora() - dt.timedelta(days=DIAS + 1))
    usados = {}
    for h in est["items"].values():
        if h.get("ys") and h["f"] >= lim30:
            usados[h["ys"]] = max(usados.get(h["ys"], ""), h["f"])
    for b in BENCH.values():
        usados[b] = iso(ahora())
    q = mk.setdefault("q", {})
    if [s for s in usados if s not in q] or mk.get("tq", "") < iso(ahora() - dt.timedelta(minutes=30)):
        actualizar_cotizaciones(mk, cotizaciones([f"{m}USD=X" for m in MONEDAS] + list(usados)))
        mk["tq"] = iso(ahora())
    d, w = mk.setdefault("d", {}), mk.setdefault("w", {})
    reciente = iso(ahora() - dt.timedelta(days=10))
    tareas = []
    for s, f in usados.items():
        edad_d = (ahora().timestamp() - ts(d[s]["t"])) / 3600 if s in d else 1e9
        if edad_d >= (1 if f >= reciente else 24):
            tareas.append((0 if s not in d else 1 if f >= reciente else 2, -ts(f), s, "d"))
        if s not in w or (ahora().timestamp() - ts(w[s]["t"])) / 3600 >= 24:
            tareas.append((0 if s not in w else 3, -ts(f), s, "w"))
    tareas.sort()
    for _, _, s, tipo in tareas[:limite]:
        res = grafico(s, "6mo", "1d") if tipo == "d" else grafico(s, "5y", "1wk")
        time.sleep(0.25)
        try:
            if not res:
                raise ValueError("sin gráfico")
            if tipo == "d":
                d[s] = {"t": iso(ahora()), "b": barras(res)}
            else:
                w[s] = dict(crecimiento(res) or {}, t=iso(ahora()))
        except Exception:                                  # datos raros de una acción: que no tumben la pasada
            (d if tipo == "d" else w).setdefault(s, {})["t"] = iso(ahora())   # (y no reintentar en cada pasada)
    for dic in (q, d, w):                                   # limpieza: lo que ya no sale en ninguna noticia
        for s in [s for s in dic if s not in usados]:
            del dic[s]
    return len(tareas[:limite])


def precio_en(b, t_unix):
    """Índices de la última sesión cerrada antes de t (i0) y de la primera que cierra después (i1)."""
    i0 = i1 = None
    for i, x in enumerate(b):
        if x[0] < t_unix:
            i0 = i
        elif i1 is None:
            i1 = i
    return i0, i1


def bench_de(h):
    return BENCH["min"] if h["g"] == "min" else BENCH["bio"] if "biotecnología" in (h.get("et") or []) else BENCH["tec"]


def mercado_noticia(h, mk):
    """Lo que ha hecho la acción con la noticia: rx = salto del día (cierre anterior -> 1.er cierre después),
    rd = lo que hace después (comprando al 1.er cierre tras la noticia), rs = todo desde la noticia, vx = volumen
    desde la noticia frente a lo normal; bd = lo mismo que rd en el fondo de su sector."""
    s = h.get("ys")
    b = ((mk.get("d") or {}).get(s) or {}).get("b") or []
    if not b:
        return None
    tn = ts(h["f"])
    i0, i1 = precio_en(b, tn)
    if i0 is None:
        return None
    p0 = b[i0][1]
    qx = (mk.get("q") or {}).get(s) or {}
    px = qx.get("px") or b[-1][1]
    if qx.get("px") and b[-1][1] and abs(qx["px"] / b[-1][1] - 1) > 0.5:
        px = b[-1][1]                                        # cotización rara (otra moneda, céntimos): mejor el gráfico
    base = sorted(x[2] for x in b[max(0, i0 - 59):i0 + 1] if x[2])
    vmed = base[len(base) // 2] if base else 0
    o = {"p0": r4(p0, 5), "rs": r4(px / p0 - 1, 3)}
    if i1 is not None:
        p1 = b[i1][1]
        o["rx"] = r4(p1 / p0 - 1, 3)
        if vmed:
            o["vx"] = r4(max(x[2] for x in b[i1:]) / vmed, 3)
            v1 = max(x[2] for x in b[i1:i1 + 2])
            o["fd"] = r4(0.5 * min(1, max(0, o["rx"] / 0.2)) + 0.5 * min(1, max(0, math.log(max(v1 / vmed, 1)) / math.log(6))), 2)
        if i1 < len(b) - 1:
            o["rd"] = r4(px / p1 - 1, 3)
            o["ses"] = len(b) - 1 - i1
            bb = ((mk.get("d") or {}).get(bench_de(h)) or {}).get("b") or []
            j = next((i for i, x in enumerate(bb) if x[0] >= b[i1][0] - 3600), None)
            if j is not None and j < len(bb) - 1:
                o["bd"] = r4(bb[-1][1] / bb[j][1] - 1, 3)
    elif vmed and qx.get("vol") and b[-1][0] < ahora().timestamp() - 3600:
        o["vx"] = r4(qx["vol"] / vmed, 3)                    # la noticia es de hoy y la sesión sigue abierta
    return o


def notas(h, mk):
    """Importancia (si), crecimiento histórico (sg) y FOMO (sf), de 0 a 100."""
    h["si"] = nota_importancia(h, mk)
    s = h.get("ys")
    w = (mk.get("w") or {}).get(s) or {}
    q = (mk.get("q") or {}).get(s) or {}
    r1, c3, c5 = w.get("r1a"), w.get("r3a"), w.get("r5a")
    if r1 is None and q.get("c52") is not None:
        r1 = q["c52"]
    # en logaritmos y con más peso al largo plazo: un +279 % en el último año no tapa cinco años perdiendo
    ln = lambda x: math.log(max(1 + x, 0.01)) if x is not None else None   # noqa: E731
    l1, l3, l5 = ln(r1), ln(c3), ln(c5)
    if l5 is not None:
        g = 0.6 * l5 + 0.25 * (l3 if l3 is not None else l5) + 0.15 * (l1 if l1 is not None else l5)
    elif l3 is not None:
        g = 0.7 * l3 + 0.3 * (l1 if l1 is not None else l3)
    else:
        g = 0.6 * l1 if l1 is not None else None          # solo 1 año de historia: cuenta menos
    h["sg"] = None if g is None else max(0, min(100, round(50 + 100 * (math.exp(g) - 1))))
    fx = mercado_noticia(h, mk) if s else None
    if fx:
        h["fx"] = fx
    else:
        h.pop("fx", None)
    if not s:
        h["sf"] = None
        return
    db = ((mk.get("d") or {}).get(s) or {}).get("b") or []
    r1m = db[-1][1] / db[-22][1] - 1 if len(db) >= 22 else None
    f_rs = min(1, max(0, (fx or {}).get("rs", 0) / 0.30))
    vx = (fx or {}).get("vx") or q.get("vr")
    f_vx = min(1, max(0, math.log(vx) / math.log(6))) if vx and vx > 0 else 0
    f_nf = min(1, max(0, (h.get("nf", 1) - 1) / 4))
    f_m = min(1, max(0, (r1m or 0) / 0.5))
    h["sf"] = round(100 * (0.35 * f_rs + 0.35 * f_vx + 0.15 * f_nf + 0.15 * f_m))


def medido(items):
    """Lo medido en vivo: qué hace la acción después de cada aviso, comprando al 1.er cierre tras la noticia."""
    def res(L):
        r = sorted(x["rd"] for x in L)
        if len(r) < 5:
            return {"n": len(r)}
        ex = sorted(x["rd"] - x["bd"] for x in L if x.get("bd") is not None)
        return {"n": len(r), "med": r4(statistics.median(r), 3), "media": r4(statistics.mean(r), 3),
                "suben": round(100 * sum(1 for x in r if x > 0) / len(r)),
                "vs": r4(statistics.median(ex), 3) if len(ex) >= 5 else None,
                "ses": round(statistics.median(x["ses"] for x in L))}
    con = [dict(h["fx"], imp=h.get("imp")) for h in items if (h.get("fx") or {}).get("rd") is not None and h.get("tkv", 0) >= 1
           and not h.get("pr")]
    imp = [x for x in con if x["imp"]]
    rx = sorted(x["rx"] for x in imp if x.get("rx") is not None)
    return {"imp": res(imp), "todas": res(con),
            "fomo": res([x for x in imp if (x.get("fd") or 0) >= 0.5]),
            "sin": res([x for x in imp if (x.get("fd") or 0) < 0.5]),
            "rx": r4(statistics.median(rx), 3) if len(rx) >= 5 else None}


# ── duplicados: la misma noticia en otras webs ───────────────────────────────
def clave_empresa(h):
    f = fichas(h.get("en") or h.get("em") or "")
    return " ".join(f[:2]) if f else None


def parecidos(a, b):
    A = {w for w in fichas(a) if len(w) > 2}
    B = {w for w in fichas(b) if len(w) > 2}
    if not A or not B:
        return False
    nums = lambda s: set(re.findall(r"\d+(?:\.\d+)?", s))   # noqa: E731
    return len(A & B) / len(A | B) >= 0.3 or len(nums(a) & nums(b) - {"1", "2", "3", "2026", "2025"}) >= 2


CAMPOS_NOTICIA = ("g", "k", "et", "ti", "em", "en", "tk", "tkv", "ys", "u", "fu", "nv", "pr", "promo", "imp0", "tr", "eco",
                  "imp_usd", "imp_mon", "fase", "rini", "rsube", "clave", "grande", "menor", "fpos", "record", "ps", "hito", "tx")


def fusionar(est, h):
    """Guarda la noticia o, si ya estaba (misma empresa, ±48 h y mismo tipo o titular parecido), la junta con la
    anterior: cuenta la web y se queda con la versión de mejor fuente. Devuelve la noticia guardada."""
    ce = clave_empresa(h)
    h["ce"] = ce
    if ce:
        for o in est["items"].values():
            if o.get("ce") != ce or abs(ts(o["f"]) - ts(h["f"])) > 48 * 3600:
                continue
            # con una oficial, una copia o resumen de la misma empresa en ±6 h es esa misma noticia; dos oficiales,
            # solo si el titular se parece (dos sondeos distintos en 2 días son dos noticias)
            ambas_oficiales = o.get("nv") == "of" and h.get("nv") == "of"
            cerca = abs(ts(o["f"]) - ts(h["f"])) <= 6 * 3600
            if parecidos(o["ti"], h["ti"]) or (not ambas_oficiales and (o["k"] == h["k"] or cerca)):
                fs = o.setdefault("fs", [[o.get("fu", ""), o.get("u", ""), o.get("nv", "pe")]])
                if not any(x[0] == h.get("fu") for x in fs):
                    fs.append([h.get("fu", ""), h.get("u", ""), h.get("nv", "pe")])
                o["nf"] = len(fs)
                if RANGO_NIVEL.get(h.get("nv"), 0) > RANGO_NIVEL.get(o.get("nv"), 0) or \
                        (h.get("tkv", -1) > o.get("tkv", -1) and RANGO_NIVEL.get(h.get("nv"), 0) == RANGO_NIVEL.get(o.get("nv"), 0)):
                    cifras_viejas = "tr" in o and "tr" not in h
                    for c in CAMPOS_NOTICIA:          # la versión oficial manda: su titular, sus cifras y su enlace
                        if c in h:
                            o[c] = h[c]
                        elif c in ("pr", "promo") or (c == "imp0" and not cifras_viejas):
                            o.pop(c, None)            # (si la oficial no trae cifras, se quedan las de la copia)
                o["f"] = min(o["f"], h["f"])
                return o
    h["fs"] = [[h.get("fu", ""), h.get("u", ""), h.get("nv", "pe")]]
    h["nf"] = 1
    est["items"][h["id"]] = h
    return h


# ── recogida ─────────────────────────────────────────────────────────────────
def recoger_asx(est, dias=None, max_pdf=10):
    """Anuncios de la ASX que la propia bolsa marca como «afecta al precio», de minería y tecnología."""
    out, pdfs = [], 0
    ult = est.get("asx_ult", "")
    desde = iso(ahora() - dt.timedelta(days=dias)) if dias else None
    nuevo_ult = ult
    fecha = lambda a: re.sub(r"(?:\.\d+)?Z$", "+00:00", a.get("date") or "")   # noqa: E731
    fin = False
    for p in range(45 if dias else 6):
        x = pedir(ASX_LISTA.format(p=p), cab=dict(NAV, Accept="application/json"))
        try:
            items = (json.loads(x or "{}").get("data") or {}).get("items") or []
        except ValueError:
            items = []
        if not items:
            break
        for a in items:
            f = fecha(a)
            if not f:
                continue
            nuevo_ult = max(nuevo_ult, f)
            if desde and f < desde:
                fin = True
                break
            if not a.get("isPriceSensitive"):
                continue
            k = "asx:" + (a.get("documentKey") or "")
            if k in est["vistos"]:
                continue
            ci = (a.get("companyInfo") or [{}])[0] or {}
            sym = ci.get("symbol") or ((a.get("companies") or [{}])[0] or {}).get("symbolDisplay")
            if not sym:
                est["vistos"][k] = iso(ahora())
                continue
            ind, sub = ci.get("industry") or "", ci.get("subIndustry") or ""
            grupo = "min" if ind in IND_MIN or sub in SUB_MIN else "tec" if ind in IND_TEC else None
            if ind and grupo is None:                     # bancos, colegios, inmobiliarias...: fuera
                est["vistos"][k] = iso(ahora())
                continue
            nombre = nombre_corto(titulo_nombre(ci.get("displayName") or sym))
            ti = a.get("headline") or ""
            tipos = a.get("announcementTypes") or []
            pre = clasifica(ti, "", grupo, "ASX", asx={"nombre": nombre})
            # el titular a veces no dice qué es («Lynx Continues to Deliver»): si es un informe de avance, manda el PDF
            pdf = (pre is None and any(x in ("Progress Report", "Asset Acquisition", "Asset Disposal") for x in tipos)) or \
                bool(pre and pre["k"] in ("perforacion", "descubrimiento", "recursos", "estudio", "compra", "acuerdo",
                                          "contrato", "ensayo"))
            if not pre and not pdf:
                est["vistos"][k] = iso(ahora())
                continue
            txt = ""
            if pdf:
                if pdfs >= max_pdf:
                    continue                              # el PDF en la próxima pasada (no se marca como visto)
                txt = texto_pdf(a.get("documentKey") or "")   # las cifras («highlights») van en el PDF, no en el titular
                pdfs += 1
                if not pre:
                    pre = clasifica(ti, txt, grupo, "ASX", asx={"nombre": nombre})
                    if not pre:
                        est["vistos"][k] = iso(ahora())
                        continue
            est["vistos"][k] = iso(ahora())
            out.append({"k": k, "ti": ti, "u": ASX_PDF.format(k=a.get("documentKey")), "f": f, "fu": "ASX",
                        "grupo": grupo or pre["g"], "google": False, "txt": txt,
                        "asx": {"nombre": nombre, "sym": sym, "tipos": a.get("announcementTypes") or []}})
        if fin or (not desde and fecha(items[-1]) <= ult):
            break                                         # ya se ha llegado a lo visto en la pasada anterior
    est["asx_ult"] = nuevo_ult
    return out


def recoger(est, primera):
    """Noticias nuevas de todas las fuentes, sin clasificar del todo (eso lo hace procesar)."""
    crudos = []
    fuentes = [(f, g, u, False) for f, g, u in FEEDS]
    if primera or est.get("ult_google", "") < iso(ahora() - dt.timedelta(minutes=14)):
        dias = "7d" if primera else "1d"
        fuentes += [("Google News", g, "https://news.google.com/rss/search?q=" + urllib.parse.quote(f"{q} when:{dias}")
                     + "&hl=en-US&gl=US&ceid=US:en", True) for g, q in GOOGLE]
        est["ult_google"] = iso(ahora())
    lim = ahora() - dt.timedelta(days=DIAS)
    for fuente, grupo, url, es_google in fuentes:
        x = pedir(url)
        if not x:
            log("sin respuesta:", fuente, url[:80])
            continue
        for it in items_rss(x):
            k = clave_titulo(it["ti"])
            if k in est["vistos"]:
                continue
            est["vistos"][k] = iso(ahora())
            if it["f"] < lim:
                continue
            txt = it["d"]
            if not es_google and it["u"] and fuente in ("Newsfile", "PR Newswire", "CNW"):
                if grupo is None and not clasifica(it["ti"], "", None, fuente):
                    continue                              # CNW trae de todo: solo se lee lo que parece de minería o tecnología
                txt = texto_pagina(it["u"]) or txt        # el comunicado entero: cifras y tickers
                time.sleep(0.5)
            crudos.append({"k": k, "ti": it["ti"], "u": it["u"], "f": iso(it["f"]), "fu": it["fu"] or fuente,
                           "grupo": grupo, "google": es_google, "txt": txt})
        time.sleep(1.0 if es_google else 0.3)
    try:
        crudos += recoger_asx(est, dias=7 if primera else None, max_pdf=400 if primera else 10)
    except Exception as e:                                # la ASX no debe tumbar la pasada
        log("ASX:", str(e)[:120])
    return crudos


def reconstruir(est):
    """--reprocesar: las noticias guardadas vuelven a pasar por las reglas de ahora (con el texto de su comunicado)."""
    crudos = []
    for k, h in est["items"].items():
        fu = h.get("fu") or ""
        es_asx = fu == "ASX"
        es_google = "news.google." in (h.get("u") or "")
        txt = ""
        if es_asx:
            m = re.search(r"/file/([A-Za-z0-9-]+)", h.get("u") or "")
            txt = texto_pdf(m.group(1)) if m else ""
        elif not es_google and h.get("u") and fu in ("Newsfile", "PR Newswire", "CNW"):
            txt = texto_pagina(h["u"])
            time.sleep(0.3)
        c = {"k": k, "ti": h["ti"], "u": h.get("u", ""), "f": h["f"], "fu": fu, "grupo": h.get("g"), "google": es_google,
             "txt": txt, "av": h.get("av") or h.get("imp")}
        if es_asx and h.get("tk"):
            c["asx"] = {"nombre": h.get("en") or h.get("em"), "sym": h["tk"][0]["s"], "tipos": []}
        crudos.append(c)
    return crudos


def procesar(crudos, est, mk):
    """Clasifica, identifica la empresa (ticker comprobado), puntúa y junta con lo que ya había."""
    hs = []
    for c in crudos:
        h = clasifica(c["ti"], c["txt"], c["grupo"], c["fu"], c["google"], c.get("asx"))
        if not h:
            continue
        h.update(u=c["u"], f=c["f"], fu=c["fu"], id=c["k"])
        if c.get("av"):
            h["av"] = True
        if c.get("asx"):
            h["tk"] = [{"b": "ASX", "s": c["asx"]["sym"], "y": c["asx"]["sym"] + ".AX"}]
            h["tkv"], h["ys"], h["ps"] = 2, c["asx"]["sym"] + ".AX", 1
        hs.append(h)
    for h in hs:                                          # sin ticker en el texto: por su nombre
        if not h.get("tk") and not h.get("promo"):
            sect = ({"Basic Materials", "Energy"} if h["g"] == "min" else
                    {"Healthcare"} if "biotecnología" in h.get("et", []) else
                    {"Technology", "Industrials", "Communication Services", "Utilities", "Energy", "Healthcare",
                     "Consumer Cyclical"})
            tk = buscar_ticker(h.get("em"), est.setdefault("nombres", {}), sect)
            if tk:
                h["tk"] = [dict(tk)]
    syms = [ysym(t) for h in hs for t in h.get("tk", [])]
    if syms:
        actualizar_cotizaciones(mk, cotizaciones([f"{m}USD=X" for m in MONEDAS] + syms))
    out = []
    for h in sorted(hs, key=lambda h: (h["f"], -RANGO_NIVEL.get(h.get("nv"), 0))):
        verificar(h, mk)
        o = fusionar(est, h)
        if o is h or (o.get("imp0") and not o.get("av")):
            out.append(o)
    return out


# ── avisos y publicación ─────────────────────────────────────────────────────
TIPO_ES = {"descubrimiento": "⛏️ Descubrimiento", "perforacion": "⛏️ Resultados de perforación", "recursos": "⛏️ Estimación de recursos",
           "estudio": "⛏️ Estudio económico", "permiso": "⛏️ Permiso", "produccion": "⛏️ Producción o construcción",
           "programa": "⛏️ Plan de perforación", "acuerdo": "🤝 Acuerdo", "compra": "🤝 Compra o fusión",
           "financiacion": "💵 Financiación", "problema": "🚩 Problema", "aprobacion": "🔬 Aprobación",
           "regulatorio": "🔬 Paso regulatorio", "ensayo": "🔬 Ensayo clínico", "avance": "🔬 Avance tecnológico",
           "contrato": "🔬 Contrato", "patente": "🔬 Patente"}
METAL_X = {"oro": "de oro", "plata": "de plata", "cobre": "de cobre", "litio": "de Li2O", "uranio": "de U3O8",
           "tierras raras": "de tierras raras", "níquel": "de níquel", "zinc": "de zinc", "cobalto": "de cobalto",
           "antimonio": "de antimonio", "wolframio": "de WO3", "estaño": "de estaño"}
NIVEL_ES = {"of": "comunicado oficial", "co": "copia del comunicado", "re": "resumen automático: compruébalo en el original",
            "pe": "prensa u opinión"}


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
    if h.get("imp_usd") and h["k"] in ("contrato", "compra", "acuerdo", "financiacion"):
        p.append(f"importe {'' if h.get('imp_mon', 'USD') == 'USD' else h['imp_mon'] + ' '}${cifra_es(h['imp_usd'], 0 if h['imp_usd'] >= 10 else 1)} M")
    return " · ".join(p)


def email(importantes, mk, sin_email):
    if not importantes:
        return False
    lineas = []
    for h in importantes:
        tks = " · ".join(f"{t['b']}: {t['s']}" for t in h.get("tk", []))
        q = (mk.get("q") or {}).get(h.get("ys") or "") or {}
        w = (mk.get("w") or {}).get(h.get("ys") or "") or {}
        px = h.get("px")
        datos = [f"Importancia {h.get('si')}/100"]
        if q.get("mc"):
            datos.append(f"vale ${cifra_es(q['mc'] / 1e6, 0)} M en bolsa")
        if w.get("r1a") is not None:
            datos.append(f"1 año {w['r1a'] * 100:+.0f} %" + (f", 5 años {w['r5a'] * 100:+.0f} %/año" if w.get("r5a") is not None else ""))
        if q.get("vr"):
            datos.append(f"volumen 10 días ×{cifra_es(q['vr'], 1)} lo normal")
        lineas.append(
            f"{TIPO_ES.get(h['k'], h['k'])} — {h['em']} ({tks})\n"
            + (f"   {resumen_es(h)}\n" if resumen_es(h) else "")
            + f"   «{h['ti']}»\n"
            + f"   {' · '.join(datos)}\n"
            + (f"   {', '.join(h['et'])}\n" if h.get("et") else "")
            + (f"   Precio {px['px']} {px.get('mon') or ''} ({px['d1'] * 100:+.1f} % hoy)\n" if px else "")
            + f"   Fuente: {h.get('fu')} ({NIVEL_ES.get(h.get('nv'), '')})"
            + (f" · la cuentan {h['nf']} webs" if h.get("nf", 1) > 1 else "") + "\n"
            + f"   Documento: {h['u']}\n"
            + f"   Traducir: https://translate.google.com/translate?sl=en&tl=es&u={urllib.parse.quote(h['u'], safe='')}\n")
    if len(importantes) == 1:
        h = importantes[0]
        asunto = f"Radar Bolsa: {TIPO_ES.get(h['k'], h['k']).split(' ', 1)[-1].lower()} — {h['em']}"
    else:
        asunto = f"Radar Bolsa: {len(importantes)} hallazgos importantes"
    cuerpo = ("\n".join(lineas) + f"\nTodos los hallazgos, ordenados por importancia, crecimiento y FOMO: {APP}\n\n"
              "Ojo: una noticia buena no garantiza que la acción suba (a menudo ya ha subido cuando sale), "
              "y casi todas las exploradoras sacan acciones nuevas para financiarse. Información, no consejo de inversión.")
    if sin_email:
        print("── EMAIL (no enviado) ──\n" + asunto + "\n" + cuerpo)
        return True
    r = subprocess.run(["/usr/bin/python3", "/root/avisar.py", asunto, cuerpo], capture_output=True, text=True)
    log("email:", asunto, "|", (r.stdout or r.stderr).strip()[:80])
    return r.returncode == 0


PUBLICA = ("g", "k", "et", "ti", "em", "tk", "tkv", "ys", "u", "f", "fu", "nv", "nf", "pr", "promo", "imp", "tr", "eco",
           "imp_usd", "imp_mon", "px", "si", "sg", "sf", "fx", "ps", "menor", "fpos", "id")


def publicar(est, mk, sin_git, forzar):
    lim = iso(ahora() - dt.timedelta(days=DIAS))
    items = sorted((h for h in est["items"].values() if h["f"] >= lim and h.get("tk") and not h.get("promo")),
                   key=lambda h: h["f"], reverse=True)
    salida = []
    vale = lambda x: x is not None and x is not False and x != [] and x != {}   # noqa: E731  (el 0 sí vale)
    for h in items:
        o = {c: h[c] for c in PUBLICA if c in h and vale(h[c])}
        if h.get("nf", 1) > 1:
            o["fs"] = [x[:2] for x in h.get("fs", [])[:6]]
        salida.append(o)
    usados = {h["ys"] for h in items if h.get("ys")}
    m = {}
    for s in usados:
        q, w = (mk.get("q") or {}).get(s) or {}, (mk.get("w") or {}).get(s) or {}
        if not q and not w:
            continue
        db = ((mk.get("d") or {}).get(s) or {}).get("b") or []
        x = {"n": nombre_corto(q.get("n", "")), "mc": q.get("mc"), "mon": q.get("mon"), "px": q.get("px"), "d1": q.get("d1"),
             "r1m": r4(db[-1][1] / db[-22][1] - 1, 3) if len(db) >= 22 else None,
             "r1a": w.get("r1a") if w.get("r1a") is not None else q.get("c52"), "r3a": w.get("r3a"), "r5a": w.get("r5a"),
             "an": w.get("an"), "vr": q.get("vr"), "sp": w.get("sp"), "sp0": w.get("sp0"), "sp1": w.get("sp1")}
        m[s] = {k: v for k, v in x.items() if v is not None and v != ""}
    guardar(SALIDA, {"act": ahora().strftime("%Y-%m-%dT%H:%MZ"), "v": 2, "n": len(salida), "h": salida, "m": m,
                     "med": medido(items)})
    if sin_git:
        return
    ult = est.get("ult_push", "")
    if not forzar and ult > iso(ahora() - dt.timedelta(minutes=60)):
        return
    with open(CERROJO_REPO, "w") as fl:
        try:
            if fcntl:
                fcntl.flock(fl, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log("repo ocupado (actualizar.sh): hallazgos.json irá en su commit")
            return
        g = lambda *a: subprocess.run(["git", "-C", REPO, *a], capture_output=True, text=True)   # noqa: E731
        g("add", "data/hallazgos.json")
        if g("diff", "--cached", "--quiet").returncode == 0:
            return
        g("commit", "-qm", f"hallazgos {ahora().strftime('%Y-%m-%d %H:%M')} UTC")
        p = g("pull", "-q", "--rebase", "origin", "main")
        q = g("push", "-q", "origin", "main")
        if p.returncode or q.returncode:
            log("git:", (p.stderr + q.stderr).strip()[:200])
        else:
            est["ult_push"] = iso(ahora())


def main():
    sin_email, sin_git = "--sin-email" in sys.argv, "--sin-git" in sys.argv
    reprocesar = "--reprocesar" in sys.argv
    lock = open(CERROJO if fcntl else os.devnull, "w")
    try:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return                                            # la pasada anterior sigue
    est = leer(ESTADO, {"items": {}, "vistos": {}, "emails": {}})
    mk = leer(MERCADO, {})
    primera = not est["items"]
    crudos = []
    if reprocesar:
        crudos = reconstruir(est)
        est["items"] = {}
        if "--sin-borrar-nombres" not in sys.argv:
            est["nombres"] = {}                           # los tickers buscados por nombre, otra vez con las reglas de ahora
        est.pop("dups", None)
    crudos += recoger(est, primera or reprocesar)
    nuevos = procesar(crudos, est, mk)
    if reprocesar or primera:
        guardar(ESTADO, est)                              # punto de control: lo largo ya está hecho
    n_graf = 0
    try:
        n_graf = refrescar_mercado(est, mk, 10_000 if (reprocesar or primera) else 24)
    except Exception as e:                                # sin datos de bolsa nuevos se sigue: los avisos van primero
        log("mercado:", repr(e)[:160])
    for h in est["items"].values():
        try:
            notas(h, mk)
        except Exception as e:
            h["si"] = h.get("si") or nota_importancia(h, {})
            log("notas:", h.get("id"), repr(e)[:120])
        h["imp"] = es_importante(h)
    # limpieza
    lim = iso(ahora() - dt.timedelta(days=DIAS + 5))
    est["items"] = {k: h for k, h in est["items"].items() if h["f"] >= lim}
    est["vistos"] = {k: v for k, v in est["vistos"].items() if v >= lim}
    if len(est.get("nombres", {})) > 5000:
        est["nombres"] = {}
    try:
        viejos = ahora().timestamp() - 40 * 86400
        for f in os.listdir(PDFS):
            if os.path.getmtime(f"{PDFS}/{f}") < viejos:
                os.remove(f"{PDFS}/{f}")
    except OSError:
        pass
    # avisos: lo importante publicado en las últimas 6 h que aún no se avisó (ni la 1.ª pasada ni --reprocesar)
    hoy = ahora().strftime("%Y-%m-%d")
    reciente = iso(ahora() - dt.timedelta(hours=6))
    nuevos = list({h["id"]: h for h in nuevos}.values())   # una noticia juntada dos veces en la pasada, una sola vez
    imp = [h for h in nuevos if h.get("imp") and not h.get("av") and h["f"] >= reciente and not primera and not reprocesar]
    imp.sort(key=lambda h: -(h.get("si") or 0))
    for h in nuevos:
        if h.get("imp") and (primera or reprocesar):
            h["av"] = True
    enviado = False
    if imp and est["emails"].get(hoy, 0) < MAX_EMAILS_DIA:
        for h in imp:
            q = (mk.get("q") or {}).get(h.get("ys") or "") or {}
            if q.get("px"):
                h["px"] = {"y": h["ys"], "px": q["px"], "d1": q.get("d1") or 0, "mon": q.get("mon")}
        enviado = email(imp[:12], mk, sin_email)
        if enviado:
            est["emails"] = {hoy: est["emails"].get(hoy, 0) + 1}
            for h in imp[:12]:
                h["av"] = True
    publicar(est, mk, sin_git, forzar=bool(imp) or primera or reprocesar)
    guardar(ESTADO, est)
    guardar(MERCADO, mk)
    log(f"nuevos {len(nuevos)} ({sum(1 for h in nuevos if h.get('imp'))} importantes) · gráficos {n_graf}"
        f"{' · email enviado' if enviado else ''} · en la app "
        f"{sum(1 for h in est['items'].values() if h.get('tk') and not h.get('promo'))}")


def test():
    tk_ = " (TSXV: TST)"
    casos = [   # (título, texto, grupo, tipo esperado, importante por las reglas)
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
        # fallos reales de la v1 (2026-10-11)
        ("Magnum Mining Reports 119 New Assay Results at Piracanjuba North, Maiden Resource Estimate Targeted for November 2026",
         "Magnum Mining (ASX: MGU)", "min", "perforacion", False),
        ("First Au acquires Ida Valley gold project to fast-track maiden resource", "First Au (ASX: FAU)", "min", "compra", False),
        ("Athena Resources hits broad high-grade magnetite at Narryer as maiden resource work begins", "Athena (ASX: AHN)",
         "min", "perforacion", False),
        ("G50 Initiates Drill Program to Follow up High Grade Gold Intercept at White Caps, Nevada",
         "G50 Corp (ASX: G50) 13.5 m at 7.67 g/t Au", "min", "programa", False),
        ("Taruga Minerals Mobilises Drill Rig to Weioko Gold Deposit for Maiden 1,000m Diamond Drilling Program", "(ASX: TAR)",
         "min", "programa", False),
        ("Bitterroot Raises C$1.8 Million to Advance Michigan Nickel-Copper Drilling", "Bitterroot (CSE: BITE)",
         "min", "financiacion", False),
        ("Hot Chili drills shallow copper, gold at Chile discovery", "Hot Chili (ASX: HCH) 30 m at 0.2% Cu", "min", "perforacion", False),
        ("Graphano Energy Completes Prospecting Phase at Black Jack Graphite Discovery", "Graphano (TSXV: GEL)", "min", None, False),
        ("Thunder Gold drills 298 metres of 0.409 g/t gold at Ontario project", "Thunder Gold (TSXV: TGOL)", "min", "perforacion", False),
        ("Arafura enters binding offtake agreement with Hanwa", "Arafura (ASX: ARU) rare earths", "min", "acuerdo", True),
        ("Copper Discovery at Mt Chalmers", "QMines (ASX: QML) copper", "min", "descubrimiento", True),
        ("Oriole Resources Reports 32.2 Million-Tonne Maiden Resource at Wapouzé", "Oriole (AIM: ORR) gold", "min", "recursos", True),
        ("Horizon drills 44m at 5.08g/t Au from 144m at Gum Creek", "", "min", "perforacion", True),
        ("Crescent receives FDA clearance for CR-002 cancer trial", "Crescent Biopharma (NASDAQ: CBIO)", "tec", "regulatorio", False),
        ("Natera receives FDA breakthrough designation for multi-cancer blood test", "Natera (NASDAQ: NTRA)", "tec", "regulatorio", False),
        ("FDA approves additional U.S. manufacturing capacity for SIMLANDI, Alvotech's biosimilar to Humira",
         "Alvotech (NASDAQ: ALVO)", "tec", "aprobacion", False),
        ("argenx Reports Positive Phase 2 Celiac Disease Trial Results, Plans Phase 3 Development of FB102", "argenx (NASDAQ: ARGX)",
         "tec", "ensayo", False),
        ("RTX's Raytheon awarded $6.3 billion multi-year contract for SM-3 IB interceptors", "Raytheon, an RTX Business (NYSE: RTX)",
         "tec", "contrato", True),
        ("SpaceX boosted revenue but isn't profitable, first-ever earnings report shows", "", "tec", None, False),
        ("Kip McGrath: Variation of Takeover Bid", "", None, None, False),
        # fallos de la 1.ª prueba de la v2 (2026-10-12)
        ("Lynx Continues to Deliver, New Targets Emerging", "Black Cat (ASX: BC8) 9.86m at 27.38g/t Au", "min", "perforacion", True),
        ("Diamond Drilling Recommences at Arrakis", "Yandal (ASX: YRL) 12m at 3.1g/t Au", "min", "programa", False),
        ("White Caps drilling restarts, follows 13.5m @ 7.67 g/t gold", "G50 (ASX: G50)", "min", "programa", False),
        ("Land Access Secured for Petrovo Drilling", "Yugo (ASX: YUG) 20m at 2.5g/t Au", "min", "programa", False),
        ("Spanish Mountain Gold Expands Near Surface Mineralization Intersecting 1.2 g/t Au over 50 m Ahead of PFS",
         "Spanish Mountain Gold (TSXV: SPA)", "min", "perforacion", False),
        ("DALAROO HITS NEW SULPHIDE ZONE 1.25KM FROM GOLD DISCOVERY", "Dalaroo (ASX: DAL)", "min", "descubrimiento", True),
        ("45% Increase to MRE at Kelpie Tin Deposit", "Caspin (ASX: CPN) tin", "min", "recursos", True),
        ("Record Gold Production at Santa Barbara Project Colombia", "Aguia (ASX: AGR) gold", "min", "produccion", False),
        ("Fairway Decline Underway Second High Grade Mine Developing", "Meeka (ASX: MEK) gold", "min", "produccion", True),
        ("September 2026 Quarter Update", "Ramelius (ASX: RMS) gold drilling", "min", None, False),
        ("Operational Update September 2026", "Metro Mining (ASX: MMI) bauxite mining was suspended due to rain", "min", None,
         False),
        ("Board Transformation and Special Crossing to Drive Growth", "High-Tech (ASX: HTM) maiden resource growth", "min", None,
         False),
        ("Award of Tahmoor mining services contract valued up to $240M", "Mastermyne (ASX: MYE) mining", "min", "contrato", True),
        ("Muntanga East Metallurgical Testwork", "Atomic Eagle (ASX: AEU) uranium discovery of the deposit", "min",
         "descubrimiento", False),
    ]
    ok = True
    for ti, txt, grupo, k, imp in casos:
        h = clasifica(ti, txt + tk_ if "(" not in txt and txt else txt, grupo, "Newsfile", asx={"nombre": "Horizon Gold"}
                      if ti.startswith("Horizon drills") else None)
        bien = (h is None and k is None) or (h is not None and h["k"] == k and bool(h.get("imp0")) == imp)
        ok &= bien
        print(("OK " if bien else "MAL"), f"{ti[:62]:62} -> {h and h['k']} imp={h and h.get('imp0')} "
              f"{resumen_es(h) if h else ''} em={h and h.get('em')!r}")
    nombres = [   # (titular, empresa que debe salir)
        ("Pfizer’s TUKYSA Regimen Receives FDA Approval as Front-Line Maintenance Treatment", "Pfizer"),
        ("argenx Reports Positive Phase 2 Celiac Disease Trial Results", "argenx"),
        ("Taruga Minerals Mobilises Drill Rig to Weioko Gold Deposit", "Taruga Minerals"),
        ("Nevada drill hole found 13.5 meters averaging 7.67 grams of gold per tonne.", None),
        ("A short drill interval extends a gold-rich zone within RPX Gold’s open-pit area.", None),
        ("Is AbbVie (ABBV) Still Undervalued On FDA Approval And Fresh Pipeline Data?", None),
        ("Southern Cross Gold Infill Drilling Lifts Golden Dyke Grades: 2.7 m @ 178.4 g/t", "Southern Cross Gold"),
    ]
    for ti, esperado in nombres:
        n = nombre_titulo(ti)
        n = n if n and nombre_valido(n) else None
        bien = n == esperado
        ok &= bien
        print(("OK " if bien else "MAL"), f"nombre {ti[:58]:58} -> {n!r}")
    pares = [   # (nombre de la noticia, nombre oficial, ticker, ¿misma empresa?)
        ("Osisko Gold", "Osisko Gold Group Inc.", "OGG", True), ("Raytheon, an RTX", "RTX Corporation", "RTX", True),
        ("Nevada", "Nevada King Gold Corp.", "NKG", False), ("Junior X Resources", "Barrick Mining Corporation", "B", False),
        ("Taruga Minerals Mobilises", "Taruga Minerals Limited", "TAR", True), ("First Au", "First Au Limited", "FAU", True),
        ("Golden Star", "Golden Minerals Company", "AUMN", False), ("Discovery", "Discovery Mining Ltd.", "DSV", True),
        ("G50 Corp", "G50 Corp Limited", "G50", True), ("Gold X Mining", "GoldMining Inc.", "GOLD", False),
    ]
    tramos = [   # textos reales de PDF de la ASX (2026-10-12): (texto, metros y ley esperados)
        ("TKB0724 26m @ 2.79 g/t Au from 126m depth 1m @ 53.5 g/t Au from 129m depth", (26, 2.79)),
        ("SBRC26-017 25m @ 4.88 g/t Au and 0.85% Sb from 242m incl. 10m @ 8.50 g/t Au and 1.83% Sb", (25, 4.88)),
        ("119m at 4.94% TREO 10m at 12.31% TREO 2m at 2.65% MREO Surface to 119m From 41m to 51m 2m at 14.28% TREO", (119, 4.94)),
        ("MTRC089 6m at 0.52% Ni, 228ppm Co, 76ppm Cu, 64ppb Pt+Pd from 198m 1m at 1.08% Ni, 497ppm Co", (6, 0.52)),
        ("MZRC174: 7m @ 8.6 g/t Au from 8m, including 1m @ 57.1 g/t Au from 8m", (7, 8.6)),
        ("Brixton Metals Drills 4 m of 1,975 g/t Silver within 2.50 m of 48 g/t Silver", (4, 1975)),
        ("Vizsla Copper Intersects 307.5 G/T Silver Equivalent Over 21.2 Meters", (21.2, 307.5)),   # plata equivalente
        ("Heliostar Metals Drills 69 Metres Grading 10.1 g/t Gold in the High Grade Panel", (69, 10.1)),
    ]
    for txt, esperado in tramos:
        tr = mejor_tramo(txt)
        res = (round(tr[1], 2), round(tr[2], 3)) if tr else None
        bien = res == (tuple(float(x) for x in esperado) if esperado else None)
        ok &= bien
        print(("OK " if bien else "MAL"), f"tramo {txt[:60]:60} -> {res}")
    h = clasifica("TEVA Stock Hits Fresh 52-Week High As FDA Approves Monthly Schizophrenia Shot", "", "tec", "Nasdaq", True)
    bien = bool(h and h.get("pr"))
    ok &= bien
    print(("OK " if bien else "MAL"), "prensa: «TEVA Stock Hits Fresh 52-Week High…» ->", h and h.get("nv"), h and h.get("pr"))
    for a, b, s, esperado in pares:
        bien = mismo_nombre(a, b, s) == esperado
        ok &= bien
        print(("OK " if bien else "MAL"), f"mismo_nombre({a!r}, {b!r}, {s}) -> {not esperado if not bien else esperado}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if "--test" in sys.argv:
        test()
    main()
