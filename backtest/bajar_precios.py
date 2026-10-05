#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backtest Radar Bolsa · paso 1: precios diarios (Yahoo) de todas las empresas de EE.UU.

Una por empresa (CIK) de Nasdaq/NYSE según la SEC, más los fondos de referencia y los
símbolos antiguos que aparezcan en los formularios de directivos (para cubrir empresas
que ya no cotizan). Reanudable: lo ya bajado no se repite.

Uso: python bajar_precios.py DATOS      (DATOS = carpeta con company_tickers_exchange.json)
"""
import concurrent.futures as cf
import json
import os
import re
import sys
import threading
import time

import requests

NAV = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/126.0 Safari/537.36", "Accept": "application/json"}
DESDE = 1230768000          # 2009-01-01
REF = ["SPY", "IJR", "IWM", "MDY", "IJH", "VTI", "VOO", "QQQ"]
_lock = threading.Lock()
_ult = [0.0]


def tickers_sec(datos):
    j = json.load(open(f"{datos}/company_tickers_exchange.json", encoding="utf-8"))
    por_cik = {}
    for cik, nombre, t, bolsa in j["data"]:
        if bolsa not in ("Nasdaq", "NYSE") or not t:
            continue
        if "-P" in t or re.search(r"-(W|WS|U|R|RT)$", t):      # preferentes, warrants, unidades, derechos
            continue
        if len(t) >= 5 and t[-1] in "WUR" and t[:-1].isalpha():   # SPAC: XXXXW / XXXXU / XXXXR
            continue
        por_cik.setdefault(cik, t)                                 # la primera clase de acciones
    return por_cik


def pedir(url):
    for k in range(4):
        with _lock:                                   # ~6 consultas/s entre todos los hilos
            espera = _ult[0] + 0.16 - time.time()
            if espera > 0:
                time.sleep(espera)
            _ult[0] = time.time()
        try:
            r = requests.get(url, headers=NAV, timeout=40)
            if r.status_code == 404:
                return None
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(10 * (k + 1))
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            time.sleep(3 * (k + 1))
    return None


def bajar(t, carpeta):
    ruta = f"{carpeta}/{t}.json"
    if os.path.exists(ruta):
        return "ya"
    j = pedir(f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?period1={DESDE}&period2=9999999999"
              f"&interval=1d&events=split,div")
    try:
        r = j["chart"]["result"][0]
        ts = r["timestamp"]
        q = r["indicators"]["quote"][0]
        a = r["indicators"]["adjclose"][0]["adjclose"]
    except (TypeError, KeyError, IndexError):
        with open(ruta, "w") as f:
            json.dump({"t": t, "vacio": True}, f)
        return "sin datos"
    d, c, aa, v = [], [], [], []
    for i in range(len(ts)):
        if q["close"][i] and a[i]:
            d.append(ts[i] // 86400)
            c.append(float(f"{q['close'][i]:.6g}"))
            aa.append(float(f"{a[i]:.6g}"))
            v.append(int(q["volume"][i] or 0))
    sp = [[s["date"] // 86400, s["denominator"] / s["numerator"]]
          for s in ((r.get("events") or {}).get("splits") or {}).values() if s.get("numerator")]
    with open(ruta, "w") as f:
        json.dump({"t": t, "tipo": r["meta"].get("instrumentType"), "d": d, "c": c, "a": aa, "v": v, "sp": sp}, f,
                  separators=(",", ":"))
    return "ok"


def main(datos):
    carpeta = f"{datos}/precios"
    os.makedirs(carpeta, exist_ok=True)
    por_cik = tickers_sec(datos)
    extra = []
    ruta_extra = f"{datos}/simbolos_antiguos.json"          # lo genera el paso de directivos
    if os.path.exists(ruta_extra):
        extra = json.load(open(ruta_extra))
    lista = REF + sorted(set(por_cik.values()) - set(REF)) + sorted(set(extra) - set(por_cik.values()) - set(REF))
    json.dump({str(k): v for k, v in por_cik.items()}, open(f"{datos}/cik_ticker.json", "w"))
    print(f"{len(lista)} símbolos ({len(por_cik)} empresas actuales + {len(lista) - len(por_cik) - len(REF)} antiguos)", flush=True)
    cuenta, t0 = {}, time.time()
    with cf.ThreadPoolExecutor(3) as ex:
        for i, res in enumerate(ex.map(lambda t: bajar(t, carpeta), lista), 1):
            cuenta[res] = cuenta.get(res, 0) + 1
            if i % 250 == 0:
                print(f"  {i}/{len(lista)} {cuenta} {time.time() - t0:.0f}s", flush=True)
    print("TERMINADO", cuenta, f"{time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\TechTablet\radar_datos")
