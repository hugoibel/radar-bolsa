#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Radar Bolsa — historia larga del mercado para la calculadora de "Mi dinero".

Descarga la serie mensual del S&P 500 de Robert Shiller (Yale; desde 2023 la publica en
shillerdata.com, la copia de econ.yale.edu se quedó en sept-2023) y guarda, desde 1926:
  - acciones: índice REAL (descontada la inflación) con dividendos reinvertidos
  - bonos: índice REAL de bonos del Tesoro a 10 años con cupones reinvertidos
  - IPC (inflación), para pasar a dólares nominales si hace falta

Cambia poco (una vez al mes): basta con ejecutarlo de vez en cuando en el PC.
Uso: python historia.py ../data/historia.json      (necesita: pip install xlrd requests)
"""
import json
import re
import sys

import requests
import xlrd

NAV = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"}
DESDE = 1926          # estándar académico (CRSP/Ibbotson); antes de 1926 los datos son de peor calidad


def enlace_actual():
    h = requests.get("https://shillerdata.com/", headers=NAV, timeout=60).text
    m = re.search(r'href="(//[^"]*/ie_data\.xls[^"]*)"', h)
    if not m:
        raise SystemExit("no encuentro ie_data.xls en shillerdata.com")
    return "https:" + m.group(1)


def main(salida):
    url = enlace_actual()
    xls = requests.get(url, headers=NAV, timeout=120).content
    s = xlrd.open_workbook(file_contents=xls).sheet_by_name("Data")
    # columnas (fila de cabecera 7): 0 Date · 4 CPI · 9 Real Total Return Price · 18 Real Total Bond Returns
    cab = [str(s.cell_value(7, c)).strip() for c in range(s.ncols)]
    assert cab[0] == "Date" and cab[4] == "CPI", cab[:5]
    acc, bon, ipc, fechas = [], [], [], []
    for r in range(8, s.nrows):
        f = s.cell_value(r, 0)
        if not isinstance(f, float) or f < DESDE:
            continue
        a, b, i = s.cell_value(r, 9), s.cell_value(r, 18), s.cell_value(r, 4)
        if not all(isinstance(x, float) and x > 0 for x in (a, b, i)):
            break                                  # los últimos meses a veces vienen incompletos
        anio, mes = int(f), round((f - int(f)) * 100)
        fechas.append(f"{anio}-{mes:02d}")
        acc.append(a), bon.append(b), ipc.append(i)
    # normalizado a 1 en el primer mes y con 6 cifras significativas (pesa poco)
    n = lambda L: [float(f"{x / L[0]:.6g}") for x in L]
    out = {"fuente": "Robert Shiller, shillerdata.com (S&P Composite, datos mensuales)",
           "url": url.split("?")[0], "desde": fechas[0], "hasta": fechas[-1], "meses": len(fechas),
           "nota": ("Precio = media mensual de los cierres diarios (suaviza un poco las caídas). "
                    "Acciones y bonos en términos REALES (descontada la inflación) con dividendos/cupones reinvertidos."),
           "acc": n(acc), "bon": n(bon), "ipc": n(ipc)}
    with open(salida, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    anios = len(fechas) / 12
    print(f"{fechas[0]} -> {fechas[-1]} ({len(fechas)} meses) | acciones real {acc[-1] / acc[0]:.0f}x "
          f"= {(acc[-1] / acc[0]) ** (1 / anios) - 1:.2%}/año | bonos real {(bon[-1] / bon[0]) ** (1 / anios) - 1:.2%}/año "
          f"| inflación {(ipc[-1] / ipc[0]) ** (1 / anios) - 1:.2%}/año")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "historia.json")
