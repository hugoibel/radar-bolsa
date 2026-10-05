#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backtest Radar Bolsa · paso 4: sector (código SIC) de cada empresa, también de las que ya no cotizan.

Salida: DATOS/sic.json = {cik: [sic, descripción, [tickers], [bolsas]]}
Uso: python sectores_sec.py DATOS
"""
import json
import re
import sys
import zipfile


def main(datos):
    z = zipfile.ZipFile(f"{datos}/submissions.zip")
    out = {}
    for n in z.namelist():
        if not re.fullmatch(r"CIK\d{10}\.json", n):          # los "-submissions-001" son páginas viejas
            continue
        try:
            j = json.loads(z.read(n))
        except ValueError:
            continue
        sic = j.get("sic")
        if sic:
            out[int(n[3:13])] = [int(sic) if str(sic).isdigit() else None, j.get("sicDescription"),
                                 j.get("tickers") or [], j.get("exchanges") or []]
    json.dump(out, open(f"{datos}/sic.json", "w"))
    print("TERMINADO", len(out), "empresas con sector")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\TechTablet\radar_datos")
