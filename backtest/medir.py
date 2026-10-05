#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backtest Radar Bolsa · paso 5: ¿las listas de la app ganan al índice?

CRITERIOS FIJADOS ANTES DE MIRAR LOS RESULTADOS (2026-10-05)
  Una lista "funciona" solo si, comprando sus 20 primeras a partes iguales cada fin de mes y
  aguantando 12 meses, entre 2011 y 2025:
    1) gana al S&P 500 (SPY, con dividendos) en la media Y en la mediana de las ventanas, y
    2) gana a la media de su propio universo (las empresas que podía elegir), y
    3) lo hace en al menos 9 de los 15 años (cohortes de enero),
  todo con la compra al cierre del primer día DESPUÉS de conocerse los datos.
  Si no cumple, la app lo dirá así: "medido: no gana al índice".

Qué se mide (lo mismo que hace la app, reconstruido con lo que se sabía en cada fecha):
  A) "Potencial": pequeñas y medianas (valor en bolsa $300 M-$10.000 M, ventas >= $20 M,
     liquidez >= $2 M/día, sin bancos ni inmobiliarias) ordenadas por la puntuación de la app.
     Variante V2: el margen NETO se cambia por el OPERATIVO (el neto se infla con cobros
     puntuales, p. ej. Duolingo 2026). La "interés en Wikipedia" (10 %) no existe antes de
     2015: se deja neutra para todas.
  B) "Castigadas": >= $1.000 M y >= 30 % por debajo de su máximo de 12 meses, por número de
     señales de salud (ventas crecen, gana dinero, tiene caja, ha dejado de desplomarse).
  C) "Directivos": 2 o más consejeros/ejecutivos distintos comprando con su dinero en los
     últimos 90 días (como la app), en empresas >= $300 M.

Sesgo de supervivencia: Yahoo no guarda las empresas que dejaron de cotizar. Se mide qué
parte del universo de cada fecha falta (empresas con cuentas en la SEC pero sin precio).
Uso: python medir.py DATOS
"""
import bisect
import datetime as dt
import glob
import json
import os
import pickle
import statistics as st
import sys

import numpy as np

EPOCH = dt.date(1970, 1, 1)
DIA = lambda d: (d - EPOCH).days
FECHA = lambda n: EPOCH + dt.timedelta(days=int(n))

# Temas por código SIC (aproximación a los temas GICS de la app)
TEMA_SIC = [
    ("ia", [(3570, 3579), (3661, 3669), (3670, 3679), (3825, 3825)]),
    ("energia", [(4911, 4911), (4931, 4931), (4991, 4991), (3612, 3613), (3621, 3621), (3690, 3692), (1090, 1099)]),
    ("salud", [(2833, 2836), (3841, 3845), (8071, 8071), (8731, 8731)]),
    ("defensa", [(3720, 3729), (3760, 3769), (3812, 3812), (3480, 3489)]),
    ("infra", [(1600, 1629), (1700, 1799), (3530, 3531), (3560, 3569)]),
    ("recursos", [(4940, 4941), (100, 999), (2870, 2879), (3523, 3523), (1000, 1049), (3330, 3339)]),
]


def tema(sic):
    if not sic:
        return "otros"
    for k, rangos in TEMA_SIC:
        if any(a <= sic <= b for a, b in rangos):
            return k
    return "otros"


def es_financiera(sic):
    return sic is not None and 6000 <= sic <= 6799


# ── precios ─────────────────────────────────────────────────────────────────
class Serie:
    __slots__ = ("d", "c", "a", "dv20", "dd", "fin", "_sp")

    def __init__(self, j):
        self.d = np.array(j["d"], dtype=np.int32)
        self.c = np.array(j["c"], dtype=np.float64)
        self.a = np.array(j["a"], dtype=np.float64)
        v = np.array(j["v"], dtype=np.float64)
        cv = np.concatenate([[0.0], np.cumsum(self.c * v)])
        n = len(self.c)
        self.dv20 = np.full(n, np.nan)
        if n >= 20:
            self.dv20[19:] = (cv[20:] - cv[:-20]) / 20
        self.dd = np.full(n, np.nan)
        if n >= 252:
            from numpy.lib.stride_tricks import sliding_window_view
            mx = sliding_window_view(self.c, 252).max(axis=1)
            self.dd[251:] = self.c[251:] / mx - 1
        self.fin = int(self.d[-1]) if n else 0
        # precio SIN ajustar por splits (para el valor en bolsa): c / producto de splits posteriores
        self._sp = sorted(j.get("sp") or [])

    def idx(self, dia):
        """Índice de la última sesión <= dia (o -1)."""
        return int(np.searchsorted(self.d, dia, side="right")) - 1

    def sin_ajustar(self, i):
        f = 1.0
        for dsp, k in self._sp:
            if dsp > self.d[i]:
                f *= k
        return self.c[i] / f


def cargar_precios(datos):
    S = {}
    for ruta in glob.glob(f"{datos}/precios/*.json"):
        try:
            j = json.load(open(ruta))
        except ValueError:
            continue
        if j.get("vacio") or len(j.get("d", [])) < 30:
            continue
        S[j["t"]] = Serie(j)
    return S


def ret_futuro(s, D, dias):
    """Compra al cierre de la sesión siguiente a D; vende `dias` después (o al último precio si dejó de cotizar)."""
    i = s.idx(D)
    e = i + 1
    if i < 0 or e >= len(s.d) or s.d[i] < D - 7 or s.d[e] > D + 7:
        return None, False
    x = int(np.searchsorted(s.d, s.d[e] + dias, side="right")) - 1
    trunc = s.d[x] < s.d[e] + dias - 10
    return s.a[x] / s.a[e] - 1, trunc


# ── universo y puntuación ───────────────────────────────────────────────────
def pct_rank(vals):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return lambda x: None
    return lambda x: None if x is None else 100.0 * bisect.bisect_left(v, x) / max(1, len(v) - 1)


def nz(x):
    return None if x is None or x != x else float(x)


def rasgos_en(R, k):
    g = lambda key: nz(R[key][k]) if key in R else None
    rev = g("rev_ttm")
    gp = g("gp_ttm")
    if gp is None and rev is not None and g("cogs_ttm") is not None:
        gp = rev - g("cogs_ttm")
    deuda = g("deuda")
    if deuda is None and (g("deuda_nc") is not None or g("deuda_c") is not None):
        deuda = (g("deuda_nc") or 0) + (g("deuda_c") or 0)
    caja = g("cash")
    if caja is not None and g("sti") is not None:
        caja += g("sti")
    fcf = g("ocf_ttm") - g("capex_ttm") if g("ocf_ttm") is not None and g("capex_ttm") is not None else g("ocf_ttm")
    cands = [x for x in (g("cr_a"), g("cr_q")) if x is not None]
    return {"rev": rev, "mb": gp / rev if gp is not None and rev else None,
            "mn": g("ni_ttm") / rev if g("ni_ttm") is not None and rev else None,
            "mo": g("opi_ttm") / rev if g("opi_ttm") is not None and rev else None,
            "cr": min(cands) if cands else None, "caja": caja, "deuda": deuda, "fcf": fcf,
            "acc": g("acc"), "ult": g("ult_pres")}


def colchon(f):
    if f["caja"] is None:
        return None
    return f["caja"] / max(f["deuda"] or 0, 1e6)


def puntuar(eleg, variante="V1"):
    """La misma regla que colector.puntuar() (interés neutro). V2: margen operativo en vez de neto."""
    clave_b = "mn" if variante == "V1" else "mo"
    rc = pct_rank([e["cr"] for e in eleg])
    rmb = pct_rank([e["mb"] for e in eleg])
    rmn = pct_rank([e[clave_b] for e in eleg])
    rcol = pct_rank([colchon(e) for e in eleg])
    for e in eleg:
        sol = rcol(colchon(e))
        sol = 50 if sol is None else sol
        if e["fcf"] is not None and e["fcf"] < 0 and e["caja"] is not None and e["caja"] / -e["fcf"] * 4 < 6:
            sol = min(sol, 15)
        comp = {"crec": rc(e["cr"]), "margen": rmb(e["mb"]) if e["mb"] is not None else 50,
                "benef": rmn(e[clave_b]) if e[clave_b] is not None else 50, "solidez": sol,
                "tema": 100 if e["tema"] != "otros" else 0, "interes": 50}
        e["sc_" + variante] = (.35 * comp["crec"] + .15 * comp["margen"] + .15 * comp["benef"] + .15 * comp["solidez"]
                               + .10 * comp["tema"] + .10 * comp["interes"])


def percentil_azar(rets, r, n, D, veces=400):
    """Qué percentil ocupa la cartera elegida entre `veces` carteras de `n` empresas cogidas AL AZAR
    del mismo universo y la misma fecha. Sin habilidad, sale ~50 de media. El sesgo de
    supervivencia afecta igual a las dos, así que esta comparación es la más limpia."""
    if r is None or n < 1 or len(rets) <= n:
        return None
    g = np.random.default_rng(int(D))
    medias = np.array([rets[g.choice(len(rets), n, replace=False)].mean() for _ in range(veces)])
    return float(100 * (medias < r).mean())


def t_stat(x):
    if len(x) < 3:
        return None
    s = st.stdev(x)
    return st.mean(x) / (s / len(x) ** 0.5) if s else None


# ── resumen de una estrategia ───────────────────────────────────────────────
def resumir(filas, nombre):
    """filas: [{"D", "r", "spy", "uni", "n"}] -> estadísticas y años."""
    filas = [f for f in filas if f["r"] is not None and f["spy"] is not None]
    if not filas:
        return {"nombre": nombre, "n": 0}
    ex_spy = [f["r"] - f["spy"] for f in filas]
    ex_uni = [f["r"] - f["uni"] for f in filas if f.get("uni") is not None]
    ex_ijr = [f["r"] - f["ijr"] for f in filas if f.get("ijr") is not None]
    anios = {}
    for f in filas:
        d = FECHA(f["D"])
        if d.month == 12:                         # cohorte "de enero": se forma con el cierre de diciembre
            anios[d.year + 1] = {"r": f["r"], "spy": f["spy"], "uni": f.get("uni"), "n": f["n"]}
    gana = sum(1 for a in anios.values() if a["r"] > a["spy"])
    ex_a = [a["r"] - a["spy"] for a in anios.values()]
    ex_au = [a["r"] - a["uni"] for a in anios.values() if a.get("uni") is not None]
    mitad1 = [a["r"] - a["spy"] for y, a in anios.items() if y <= 2018]
    mitad2 = [a["r"] - a["spy"] for y, a in anios.items() if y >= 2019]
    pcts = [f["pctl"] for f in filas if f.get("pctl") is not None]
    return {
        "t_spy": t_stat(ex_a), "t_uni": t_stat(ex_au),
        "ex_spy_2012_2018": st.mean(mitad1) if mitad1 else None, "ex_spy_2019_2025": st.mean(mitad2) if mitad2 else None,
        "pctl_azar": st.mean(pcts) if pcts else None,
        "pctl_azar_mitad_sup": 100 * sum(1 for p in pcts if p > 50) / len(pcts) if pcts else None,
        "nombre": nombre, "ventanas": len(filas),
        "media": st.mean(f["r"] for f in filas), "mediana": st.median(f["r"] for f in filas),
        "spy_media": st.mean(f["spy"] for f in filas), "spy_mediana": st.median(f["spy"] for f in filas),
        "ex_spy_media": st.mean(ex_spy), "ex_spy_mediana": st.median(ex_spy),
        "ex_uni_media": st.mean(ex_uni) if ex_uni else None, "ex_uni_mediana": st.median(ex_uni) if ex_uni else None,
        "ex_ijr_media": st.mean(ex_ijr) if ex_ijr else None, "ex_ijr_mediana": st.median(ex_ijr) if ex_ijr else None,
        "gana_spy_pct": 100 * sum(1 for x in ex_spy if x > 0) / len(ex_spy),
        "anios_gana_spy": gana, "anios": len(anios), "n_medio": st.mean(f["n"] for f in filas),
        "por_anio": {a: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in x.items()} for a, x in sorted(anios.items())},
    }


def veredicto(r):
    if not r.get("ventanas"):
        return "sin datos"
    ok = (r["ex_spy_media"] > 0 and r["ex_spy_mediana"] > 0 and (r["ex_uni_media"] or 0) > 0
          and r["anios_gana_spy"] >= 9)
    return "FUNCIONA (cumple los 3 criterios)" if ok else "NO gana al índice con fiabilidad"


def main(datos):
    t0 = dt.datetime.now()
    print("cargando...", flush=True)
    RG = pickle.load(open(f"{datos}/rasgos.pkl", "rb"))
    FECHAS, EMP = RG["fechas"], RG["emp"]
    sic = {int(k): v for k, v in json.load(open(f"{datos}/sic.json")).items()}
    cik_t = {int(k): v for k, v in json.load(open(f"{datos}/cik_ticker.json")).items()}
    dirs = pickle.load(open(f"{datos}/directivos.pkl", "rb"))
    S = cargar_precios(datos)
    # Símbolos antiguos de empresas que ya no cotizan. OJO: los tickers se reutilizan; solo vale
    # si Yahoo tiene precios de ese símbolo DESDE ANTES de que esa empresa lo usara en la SEC.
    primero = {}
    for c, s, fp, *_ in dirs:
        if c not in cik_t and s:
            if (c, s) not in primero or fp < primero[(c, s)]:
                primero[(c, s)] = fp
    acept = rech = 0
    for (c, s), fp in sorted(primero.items(), key=lambda x: x[1]):
        ser = S.get(s)
        if ser is None or c in cik_t:
            continue
        if ser.d[0] <= fp + 30 and ser.fin >= fp:
            cik_t[c] = s
            acept += 1
        else:
            rech += 1
    print(f"  símbolos antiguos: {acept} aceptados, {rech} descartados (el ticker es hoy de otra empresa)", flush=True)
    print(f"  {len(EMP)} empresas con cuentas, {len(S)} con precios, {len(dirs)} compras de directivos "
          f"({(dt.datetime.now() - t0).seconds}s)", flush=True)
    REF = {k: S[k] for k in ("SPY", "IJR", "IWM", "MDY") if k in S}

    # fechas de formación: fin de mes 2011-06 .. 2025-09 (hace falta un año por delante)
    formacion = [k for k, D in enumerate(FECHAS) if DIA(dt.date(2011, 5, 31)) <= D <= DIA(dt.date(2025, 9, 30))]
    res = {"potencial_V1": [], "potencial_V2": [], "potencial_univ": [],
           "cast_0_1": [], "cast_2": [], "cast_3_4": [], "cast_todas": [], "cast_univ": [],
           "dir_cluster": [], "dir_cluster_todas": [], "dir_cluster_top": [], "dir_cluster_6m": [], "cast_dir": [],
           "dir_univ": []}
    cobertura, diag = {}, {}
    # compras de directivos por empresa, ordenadas por fecha de presentación
    por_emp = {}
    for c, s, fp, fo, quien, cargo, top, imp, q, tras in dirs:
        por_emp.setdefault(c, []).append((fp, quien, top, imp))
    for c in por_emp:
        por_emp[c].sort()

    for k in formacion:
        D = FECHAS[k]
        spy12 = ret_futuro(REF["SPY"], D, 365)[0]
        ijr12 = ret_futuro(REF["IJR"], D, 365)[0] if "IJR" in REF else None
        cand, cast_u, dir_u = [], [], []
        con_cuentas = sin_precio = 0
        for cik, R in EMP.items():
            f = rasgos_en(R, k)
            if f["ult"] is None or f["ult"] < D - 200 or f["ult"] > D:
                continue                                   # no ha presentado cuentas en ~6 meses
            sc = (sic.get(cik) or [None])[0]
            if sc == 6770:
                continue                                   # SPAC (cheque en blanco)
            fin = es_financiera(sc)                        # bancos/REIT: fuera de "potencial" (como la app)
            if not fin and (f["rev"] or 0) >= 20e6:
                con_cuentas += 1
            t = cik_t.get(cik)
            s = S.get(t) if t else None
            if s is None:
                if not fin and (f["rev"] or 0) >= 20e6:
                    sin_precio += 1
                continue
            i = s.idx(D)
            if i < 260 or s.d[i] < D - 7:
                continue
            px = s.sin_ajustar(i)
            mc = px * f["acc"] if f["acc"] else None
            if not mc or mc <= 0:
                continue
            dv = s.dv20[i]
            r12, trunc = ret_futuro(s, D, 365)
            if r12 is None:
                continue
            f.update(t=t, cik=cik, mc=mc, dv=dv, dd=s.dd[i], r1m=s.a[i] / s.a[i - 21] - 1, r12=r12, trunc=trunc,
                     tema=tema(sc))
            if not fin and 300e6 <= mc <= 10e9 and dv >= 2e6 and (f["rev"] or 0) >= 20e6 and f["cr"] is not None:
                cand.append(f)
            if mc >= 1e9 and dv >= 5e6:
                cast_u.append(f)
            if mc >= 300e6 and dv >= 1e6:
                dir_u.append(f)
        cobertura[FECHA(D).isoformat()] = (con_cuentas, sin_precio)

        def fila(lista, clave="r12", uni=None):
            if not lista:
                return {"D": D, "r": None, "spy": spy12, "uni": None, "n": 0}
            r = st.mean(x[clave] for x in lista)
            return {"D": D, "r": r, "spy": spy12, "ijr": ijr12, "uni": uni, "n": len(lista),
                    "trunc": sum(1 for x in lista if x["trunc"])}

        # A) potencial
        if len(cand) >= 60:
            uni = st.mean(x["r12"] for x in cand)
            res["potencial_univ"].append(fila(cand, uni=uni))
            rets = np.array([x["r12"] for x in cand])
            for var in ("V1", "V2"):
                puntuar(cand, var)
                top = sorted(cand, key=lambda x: -x["sc_" + var])[:20]
                fl = fila(top, uni=uni)
                fl["pctl"] = percentil_azar(rets, fl["r"], 20, D)
                res["potencial_" + var].append(fl)
            # diagnóstico: quinto superior vs inferior de cada ingrediente (y de factores clásicos)
            for x in cand:
                x["col"] = colchon(x)
                x["ey"] = (x["fcf"] / x["mc"]) if x["fcf"] is not None else None          # caja libre / precio
                x["sy"] = x["rev"] / x["mc"]                                               # ventas / precio
                s = S[x["t"]]
                i = s.idx(D)
                x["mom"] = s.a[i - 21] / s.a[i - 252] - 1                                   # 12 meses sin el último
            for fac in ("cr", "mb", "mn", "mo", "col", "ey", "sy", "mom", "sc_V1", "sc_V2"):
                L = sorted([x for x in cand if x.get(fac) is not None], key=lambda x: x[fac])
                if len(L) < 50:
                    continue
                q = len(L) // 5
                diag.setdefault(fac, []).append((st.mean(x["r12"] for x in L[-q:]), st.mean(x["r12"] for x in L[:q]), uni, spy12))
        # B) castigadas
        if cast_u:
            uni = st.mean(x["r12"] for x in cast_u)
            res["cast_univ"].append(fila(cast_u, uni=uni))
            cs = [x for x in cast_u if x["dd"] == x["dd"] and x["dd"] <= -0.30]
            for x in cs:
                col = colchon(x)
                x["salud"] = sum([x["cr"] is not None and x["cr"] >= 0, x["mn"] is not None and x["mn"] > 0,
                                  col is not None and col >= 0.2, x["r1m"] > -0.10])
            res["cast_todas"].append(fila(cs, uni=uni))
            res["cast_0_1"].append(fila([x for x in cs if x["salud"] <= 1], uni=uni))
            res["cast_2"].append(fila([x for x in cs if x["salud"] == 2], uni=uni))
            res["cast_3_4"].append(fila([x for x in cs if x["salud"] >= 3], uni=uni))
        # C) directivos (2+ distintos en 90 días, presentado hasta D)
        if dir_u:
            uni = st.mean(x["r12"] for x in dir_u)
            res["dir_univ"].append(fila(dir_u, uni=uni))
            cl, cl_top = [], []
            for x in dir_u:
                L = por_emp.get(x["cik"])
                if not L:
                    continue
                j0 = bisect.bisect_right(L, (D - 90, 10 ** 12))
                j1 = bisect.bisect_right(L, (D, 10 ** 12))
                ventana = L[j0:j1]
                quienes = {q for _, q, _, _ in ventana}
                if len(quienes) >= 2:
                    x["imp_dir"] = sum(i for *_, i in ventana)
                    cl.append(x)
                    if any(top for _, _, top, _ in ventana):
                        cl_top.append(x)
            rets_d = np.array([x["r12"] for x in dir_u])
            fl = fila(sorted(cl, key=lambda x: -x["imp_dir"])[:20], uni=uni)
            if fl["r"] is not None:
                fl["pctl"] = percentil_azar(rets_d, fl["r"], min(20, fl["n"]), D)
            res["dir_cluster"].append(fl)
            fl = fila(cl, uni=uni)                                   # todas las de 2+ compradores, a partes iguales
            if fl["r"] is not None and fl["n"] >= 5:
                fl["pctl"] = percentil_azar(rets_d, fl["r"], fl["n"], D)
            res["dir_cluster_todas"].append(fl)
            res["dir_cluster_top"].append(fila(sorted(cl_top, key=lambda x: -x["imp_dir"])[:20], uni=uni))
            # a 6 meses (la literatura encuentra el efecto sobre todo en los primeros meses)
            spy6 = ret_futuro(REF["SPY"], D, 182)[0]
            r6 = [ret_futuro(S[x["t"]], D, 182)[0] for x in cl]
            r6 = [r for r in r6 if r is not None]
            u6 = [ret_futuro(S[x["t"]], D, 182)[0] for x in dir_u[::4]]       # muestra del universo (1 de cada 4)
            u6 = [r for r in u6 if r is not None]
            if r6 and u6:
                res["dir_cluster_6m"].append({"D": D, "r": st.mean(r6), "spy": spy6, "uni": st.mean(u6), "n": len(r6)})
            # castigada (>= 30 % bajo su máximo) + al menos un directivo comprando: el filtro "🏦" de la app
            cd = []
            for x in dir_u:
                if x["mc"] >= 1e9 and x["dv"] >= 5e6 and x["dd"] == x["dd"] and x["dd"] <= -0.30:
                    L = por_emp.get(x["cik"]) or []
                    j0 = bisect.bisect_right(L, (D - 90, 10 ** 12)); j1 = bisect.bisect_right(L, (D, 10 ** 12))
                    if j1 > j0:
                        cd.append(x)
            res["cast_dir"].append(fila(cd, uni=st.mean(x["r12"] for x in cast_u) if cast_u else None))
        if FECHA(D).month == 12:
            print(f"  {FECHA(D)}: potencial {len(cand)}, castigadas-univ {len(cast_u)}, directivos-univ {len(dir_u)}, "
                  f"cobertura {con_cuentas - sin_precio}/{con_cuentas} ({(dt.datetime.now() - t0).seconds}s)", flush=True)

    NOMF = {"cr": "crecimiento de ventas", "mb": "margen bruto", "mn": "margen neto", "mo": "margen operativo",
            "col": "caja/deuda", "ey": "caja libre/precio (valor)", "sy": "ventas/precio (valor)",
            "mom": "momento 12-1 meses", "sc_V1": "PUNTUACIÓN de la app", "sc_V2": "puntuación V2 (operativo)"}
    diag_out = {}
    print("\n=== DIAGNÓSTICO (pequeñas y medianas): quinto con el valor MÁS ALTO frente al MÁS BAJO, 12 meses ===")
    for fac, L in diag.items():
        alto, bajo = st.mean(a for a, b, u, s in L), st.mean(b for a, b, u, s in L)
        vs_uni = st.mean(a - u for a, b, u, s in L)
        gana = 100 * sum(1 for a, b, u, s in L if a > b) / len(L)
        diag_out[fac] = {"nombre": NOMF[fac], "alto": alto, "bajo": bajo, "alto_vs_univ": vs_uni, "alto_gana_bajo_pct": gana}
        print(f"  {NOMF[fac]:28} alto {alto:+.1%}  bajo {bajo:+.1%}  diferencia {alto - bajo:+.1%}  alto vs universo {vs_uni:+.1%}  "
              f"(alto gana al bajo en el {gana:.0f} % de los meses)")
    salida = {"criterio": __doc__.split("Qué se mide")[0].strip(), "cobertura": cobertura, "diag": diag_out, "res": {}}
    for nombre, filas in res.items():
        r = resumir(filas, nombre)
        r["veredicto"] = veredicto(r) if not nombre.endswith("univ") else "(referencia)"
        salida["res"][nombre] = r
    json.dump(salida, open(f"{datos}/resultados.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n=== RESULTADOS (12 meses, compra a partes iguales, con dividendos) ===")
    for nombre, r in salida["res"].items():
        if not r.get("ventanas"):
            print(f"{nombre:18} sin datos")
            continue
        print(f"{nombre:18} n≈{r['n_medio']:5.1f}  media {r['media']:+.1%} (SPY {r['spy_media']:+.1%})  mediana {r['mediana']:+.1%} "
              f"(SPY {r['spy_mediana']:+.1%})  vs SPY {r['ex_spy_media']:+.1%}/{r['ex_spy_mediana']:+.1%}  "
              f"vs universo {r['ex_uni_media'] if r['ex_uni_media'] is None else format(r['ex_uni_media'], '+.1%')}  "
              f"vs IJR {r['ex_ijr_media'] if r['ex_ijr_media'] is None else format(r['ex_ijr_media'], '+.1%')}  "
              f"años ganando al SPY {r['anios_gana_spy']}/{r['anios']}  -> {r['veredicto']}")
        extra = []
        if r.get("t_spy") is not None:
            extra.append(f"t vs SPY {r['t_spy']:+.2f}")
        if r.get("t_uni") is not None:
            extra.append(f"t vs universo {r['t_uni']:+.2f}")
        if r.get("ex_spy_2012_2018") is not None and r.get("ex_spy_2019_2025") is not None:
            extra.append(f"vs SPY 2012-18 {r['ex_spy_2012_2018']:+.1%} / 2019-25 {r['ex_spy_2019_2025']:+.1%}")
        if r.get("pctl_azar") is not None:
            extra.append(f"percentil frente al azar {r['pctl_azar']:.0f} (mejor que la mediana del azar el {r['pctl_azar_mitad_sup']:.0f} % de los meses)")
        if extra:
            print(" " * 20 + " · ".join(extra))
    print(f"\n({(dt.datetime.now() - t0).seconds}s)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\TechTablet\radar_datos")
