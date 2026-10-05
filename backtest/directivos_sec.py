#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backtest Radar Bolsa · paso 3: compras de directivos con su dinero (formulario 4, 2006-2026).

Lee los "Insider Transactions Data Sets" trimestrales de la SEC. Se queda con las COMPRAS en
el mercado (código P) de consejeros y ejecutivos (como la app: un accionista >10 % que no es
consejero ni ejecutivo suele ser un fondo, no un directivo apostando su dinero). Se descartan
las enmiendas (4/A) para no contar dos veces.

Salida: DATOS/directivos.pkl  [(cik_emisor, símbolo, día_presentación, día_operación,
         cik_directivo, cargo, es_ceo_cfo, importe_usd, acciones, acciones_tras)]
        DATOS/simbolos_antiguos.json  (símbolos de empresas que ya no están en la lista actual)
Uso: python directivos_sec.py DATOS
"""
import csv
import datetime as dt
import glob
import io
import json
import pickle
import re
import sys
import zipfile

csv.field_size_limit(10 ** 8)
EPOCH = dt.date(1970, 1, 1)
MESES = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}


def dia(s):
    """'31-JAN-2024' -> días desde 1970."""
    try:
        d, m, a = s.split("-")
        return (dt.date(int(a), MESES[m.upper()], int(d)) - EPOCH).days
    except (ValueError, KeyError, AttributeError):
        return None


def tabla(z, nombre):
    with z.open(nombre) as fh:
        r = csv.reader(io.TextIOWrapper(fh, "utf-8", errors="replace"), delimiter="\t")
        cab = next(r)
        for fila in r:
            yield dict(zip(cab, fila))


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main(datos):
    eventos, simbolos = [], {}
    for ruta in sorted(glob.glob(f"{datos}/sec_insiders/*_form345.zip")):
        z = zipfile.ZipFile(ruta)
        sub = {}
        for s in tabla(z, "SUBMISSION.tsv"):
            if s.get("DOCUMENT_TYPE") == "4":
                sub[s["ACCESSION_NUMBER"]] = (int(s["ISSUERCIK"] or 0), (s.get("ISSUERTRADINGSYMBOL") or "").strip().upper(),
                                              dia(s.get("FILING_DATE")))
        duenos = {}
        for o in tabla(z, "REPORTINGOWNER.tsv"):
            acc = o["ACCESSION_NUMBER"]
            if acc not in sub:
                continue
            rel = (o.get("RPTOWNER_RELATIONSHIP") or "").lower()
            cargo = (o.get("RPTOWNER_TITLE") or "").strip()
            duenos.setdefault(acc, []).append({
                "cik": int(o.get("RPTOWNERCIK") or 0), "dir": "director" in rel or "officer" in rel,
                "cargo": cargo or ("Consejero" if "director" in rel else ""),
                "top": bool(re.search(r"\b(ceo|cfo|chief executive|chief financial|president)\b", cargo.lower()))})
        n = 0
        for t in tabla(z, "NONDERIV_TRANS.tsv"):
            acc = t["ACCESSION_NUMBER"]
            if t.get("TRANS_CODE") != "P" or t.get("TRANS_ACQUIRED_DISP_CD") != "A" or acc not in sub:
                continue
            q, px = num(t.get("TRANS_SHARES")), num(t.get("TRANS_PRICEPERSHARE"))
            if not q or not px or q <= 0 or px <= 0 or q * px > 5e8:     # importes absurdos = errores de tecleo
                continue
            ds = [d for d in duenos.get(acc, []) if d["dir"]]
            if not ds:
                continue
            cik_e, sim, f_pres = sub[acc]
            if not f_pres:
                continue
            d0 = ds[0]                                   # presentación conjunta: se cuenta UNA vez
            eventos.append((cik_e, sim, f_pres, dia(t.get("TRANS_DATE")), d0["cik"], d0["cargo"],
                            any(d["top"] for d in ds), q * px, q, num(t.get("SHRS_OWND_FOLWNG_TRANS"))))
            if sim:
                simbolos[sim] = cik_e
            n += 1
        print(f"  {ruta[-18:]}: {len(sub)} formularios 4, {n} compras de directivos", flush=True)
    with open(f"{datos}/directivos.pkl", "wb") as f:
        pickle.dump(eventos, f, protocol=pickle.HIGHEST_PROTOCOL)
    actuales = set(json.load(open(f"{datos}/cik_ticker.json")).values()) if glob.glob(f"{datos}/cik_ticker.json") else set()
    antiguos = sorted(s for s in simbolos if s not in actuales and re.fullmatch(r"[A-Z]{1,5}", s))
    json.dump(antiguos, open(f"{datos}/simbolos_antiguos.json", "w"))
    print("TERMINADO", len(eventos), "compras;", len(antiguos), "símbolos que ya no están en la lista actual", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\TechTablet\radar_datos")
