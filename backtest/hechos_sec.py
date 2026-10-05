#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backtest Radar Bolsa · paso 2: lo que se SABÍA de cada empresa a cada fin de mes.

Lee companyfacts.zip de la SEC (todas las empresas que han presentado XBRL, también las
que ya no cotizan). Cada dato lleva su fecha de presentación ("filed"): a cada fin de mes
solo se usa lo presentado hasta ese día, así el backtest no mira al futuro.

Calcula, a cada fin de mes desde 2010-12:
  ventas, beneficio bruto, operativo y neto, flujo de caja operativo e inversión de los
  últimos 12 meses (TTM), crecimiento anual y del último trimestre, caja, deuda y acciones.

Salida: DATOS/rasgos.pkl = {"fechas": [...], "emp": {cik: {"n": nombre, rasgo: np.array(float32)}}}
Uso: python hechos_sec.py DATOS [N_MAX_EMPRESAS]
"""
import concurrent.futures as cf
import datetime as dt
import json
import pickle
import sys
import zipfile

import numpy as np

EPOCH = dt.date(1970, 1, 1)
CONCEPTOS = {   # dentro de cada grupo, el orden es la preferencia si dos etiquetas dan el mismo periodo
    "rev": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax", "RevenuesNetOfInterestExpense",
            "SalesRevenueGoodsNet", "SalesRevenueServicesNet"],
    "gp": ["GrossProfit"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices"],
    "opi": ["OperatingIncomeLoss"],
    "ni": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"],
    "sti": ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
    "deuda": ["LongTermDebt", "LongTermDebtAndCapitalLeaseObligations", "DebtInstrumentCarryingAmount"],
    "deuda_nc": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligationsNoncurrent", "ConvertibleNotesPayable"],
    "deuda_c": ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings", "LongTermDebtAndCapitalLeaseObligationsCurrent"],
    "acc": ["dei:EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding",
            "WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic"],
}
DURACION = ("rev", "gp", "cogs", "opi", "ni", "ocf", "capex")
INSTANTE = ("cash", "sti", "deuda", "deuda_nc", "deuda_c")
FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "10-KT", "10-QT", "20-F", "20-F/A", "40-F", "40-F/A"}
RASGOS = [f"{g}_ttm" for g in DURACION] + ["cr_a", "cr_q", "fy_fin", "q_fin"] + list(INSTANTE) + ["acc", "ult_pres"]


def dia(s):
    return (dt.date.fromisoformat(s) - EPOCH).days


def fines_de_mes(d0=dt.date(2010, 12, 31), d1=dt.date(2026, 9, 30)):
    out, a, m = [], d0.year, d0.month
    while dt.date(a, m, 1) <= d1:
        sig = dt.date(a + (m == 12), m % 12 + 1, 1)
        out.append((sig - dt.timedelta(days=1) - EPOCH).days)
        a, m = sig.year, sig.month
    return out


FECHAS = fines_de_mes()


def hechos(j, grupo):
    """[(presentado, inicio|None, fin, valor)] de un grupo de conceptos, ordenados por presentación."""
    facts = j.get("facts") or {}
    gaap, dei = facts.get("us-gaap") or {}, facts.get("dei") or {}
    porperiodo = {}
    for prio, concepto in enumerate(CONCEPTOS[grupo]):
        fuente = dei if concepto.startswith("dei:") else gaap
        c = fuente.get(concepto.replace("dei:", ""))
        if not c:
            continue
        for h in (c.get("units") or {}).get("shares" if grupo == "acc" else "USD") or []:
            if h.get("form") not in FORMS or h.get("val") is None or not h.get("filed"):
                continue
            clave = (h.get("start"), h["end"], h["filed"])
            if clave not in porperiodo or porperiodo[clave][0] > prio:
                porperiodo[clave] = (prio, h)
    out = []
    for (_, h) in porperiodo.values():
        try:
            out.append((dia(h["filed"]), dia(h["start"]) if h.get("start") else None, dia(h["end"]), float(h["val"])))
        except ValueError:
            continue
    out.sort(key=lambda x: (x[0], x[2]))
    return out


def ttm_y_crec(saber):
    """saber: {(inicio, fin): valor} con lo presentado hasta hoy. Devuelve (ttm, cr_a, cr_q, fy_fin, q_fin)."""
    anual, trim, por_fin = [], {}, {}
    for (s, e), v in saber.items():
        if s is None:
            continue
        dur = e - s
        por_fin.setdefault(e, []).append((s, v))
        if 330 <= dur <= 400:
            anual.append((e, s, v))
        elif 75 <= dur <= 105:
            trim[e] = v
    if not anual and not trim:
        return (None,) * 5
    fy = max(anual) if anual else None
    # Q4 derivado (casi nadie publica el 4º trimestre suelto): año - nueve meses acumulados
    for (e, s, v) in anual:
        if e not in trim:
            for e9 in range(e - 100, e - 79):
                for s9, v9 in por_fin.get(e9, []):
                    if abs(s9 - s) <= 10 and 255 <= e9 - s9 <= 285:
                        trim[e] = v - v9
                        break
                if e in trim:
                    break
    cr_a = cr_q = None
    if fy:
        prev = [v for (e, s, v) in anual if fy[0] - 380 <= e <= fy[0] - 350]
        if prev and prev[-1] > 0:
            cr_a = fy[2] / prev[-1] - 1
    q_fin = max(trim) if trim else None
    if q_fin is not None:
        prev = [trim[e] for e in trim if q_fin - 380 <= e <= q_fin - 350]
        if prev and prev[-1] > 0:
            cr_q = trim[q_fin] / prev[-1] - 1
    ttm = fy[2] if fy else None
    if fy and q_fin is not None and q_fin > fy[0] + 20:
        # TTM = año fiscal + acumulado de este año - acumulado del año pasado
        ytd = [v for s, v in por_fin.get(q_fin, []) if abs(s - (fy[0] + 1)) <= 15]
        prev_fin = [e for e in por_fin if abs(e - (q_fin - 365)) <= 10]
        ytd_prev = [v for e in prev_fin for s, v in por_fin[e] if abs(s - (fy[0] + 1 - 365)) <= 15]
        if ytd and ytd_prev:
            ttm = fy[2] + ytd[-1] - ytd_prev[-1]
        else:   # 4 trimestres seguidos
            qs = sorted(e for e in trim if e > q_fin - 350)
            if len(qs) == 4 and qs[0] >= q_fin - 300:
                ttm = sum(trim[e] for e in qs)
    elif not fy:
        qs = sorted(e for e in trim if e > q_fin - 350)
        if len(qs) == 4:
            ttm = sum(trim[e] for e in qs)
    return ttm, cr_a, cr_q, (fy[0] if fy else None), q_fin


def ultimo_instante(saber):
    """{fin: valor} -> (valor del último fin, ese fin)."""
    if not saber:
        return None, None
    e = max(saber)
    return saber[e], e


def rasgos_empresa(j):
    G = {g: hechos(j, g) for g in CONCEPTOS}
    if not G["rev"] and not G["ni"]:
        return None
    eventos = sorted({f for L in G.values() for (f, *_rest) in L})
    if not eventos:
        return None
    n = len(FECHAS)
    R = {k: np.full(n, np.nan, dtype=np.float32) for k in RASGOS}
    saber = {g: {} for g in CONCEPTOS}
    pos = {g: 0 for g in CONCEPTOS}
    i_ev, actual = 0, None
    for k, D in enumerate(FECHAS):
        cambio = False
        while i_ev < len(eventos) and eventos[i_ev] <= D:
            i_ev += 1
            cambio = True
        if cambio:
            for g, L in G.items():
                while pos[g] < len(L) and L[pos[g]][0] <= D:
                    f, s, e, v = L[pos[g]]
                    if g in INSTANTE:
                        saber[g][e] = v
                    elif g == "acc":
                        saber[g][e] = v           # acciones: instante (portada) o media del periodo
                    else:
                        saber[g][(s, e)] = v
                    pos[g] += 1
            actual = {"ult_pres": eventos[i_ev - 1]}
            for g in DURACION:
                ttm, cr_a, cr_q, fy_fin, q_fin = ttm_y_crec(saber[g])
                actual[f"{g}_ttm"] = ttm
                if g == "rev":
                    actual.update(cr_a=cr_a, cr_q=cr_q, fy_fin=fy_fin, q_fin=q_fin)
            ref = max([x for x in (actual.get("fy_fin"), actual.get("q_fin")) if x is not None] or [D])
            for g in INSTANTE:
                v, e = ultimo_instante(saber[g])
                actual[g] = v if (v is not None and e >= ref - 400) else None   # dato viejo = no vale
            v, e = ultimo_instante(saber["acc"])
            actual["acc"] = v if (v is not None and e >= ref - 400) else None
        if actual:
            for key, v in actual.items():
                if v is not None:
                    R[key][k] = v
    return R


def procesar(args):
    ruta_zip, nombres = args
    z = zipfile.ZipFile(ruta_zip)
    out = {}
    for nombre in nombres:
        try:
            j = json.loads(z.read(nombre))
        except (KeyError, ValueError):
            continue
        R = rasgos_empresa(j)
        if R is not None:
            R["n"] = j.get("entityName")
            out[int(nombre[3:13])] = R
    return out


def main(datos, n_max=None):
    ruta = f"{datos}/companyfacts.zip"
    nombres = sorted(n for n in zipfile.ZipFile(ruta).namelist() if n.startswith("CIK"))
    if n_max:
        nombres = nombres[:: max(1, len(nombres) // n_max)][:n_max]
    trozos = [nombres[i::48] for i in range(48)]
    todo = {}
    with cf.ProcessPoolExecutor(10) as ex:
        for k, parte in enumerate(ex.map(procesar, [(ruta, t) for t in trozos]), 1):
            todo.update(parte)
            if k % 6 == 0:
                print(f"  {k}/48 trozos, {len(todo)} empresas", flush=True)
    sal = f"{datos}/rasgos{'_prueba' if n_max else ''}.pkl"
    with open(sal, "wb") as f:
        pickle.dump({"fechas": FECHAS, "rasgos": RASGOS, "emp": todo}, f, protocol=pickle.HIGHEST_PROTOCOL)
    print("TERMINADO", len(todo), "empresas ->", sal, flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\TechTablet\radar_datos",
         int(sys.argv[2]) if len(sys.argv) > 2 else None)
