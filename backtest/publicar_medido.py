#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backtest Radar Bolsa · paso 6: pasa los resultados de medir.py a data/medido.json (lo lee la app).

Las cifras salen del archivo de resultados: no se escriben a mano.
"Funciona" exige los criterios fijados antes de medir (ver medir.py) Y, además, ganar con
claridad a las carteras cogidas al azar (percentil medio >= 60) y una t >= 1,5 frente al SPY.
Uso: python publicar_medido.py DATOS ../data/medido.json
"""
import datetime as dt
import json
import sys


def p(x, d=1):
    """+12,3 % (estilo español)"""
    return f"{x * 100:+.{d}f} %".replace(".", ",").replace("-", "−")


def pts(x):
    v = x * 100
    return (f"{v:+.1f}".replace(".", ",") if abs(v) < 1 else f"{v:+.0f}").replace("-", "−") + " puntos"


def anios_txt(r):
    n, m = r["anios_gana_spy"], r["anios"]
    return f"{'solo ' if n < m / 2 else ''}le ganó {n} de {m} años"


def mitades_txt(r):
    h1, h2 = r.get("ex_spy_2012_2018"), r.get("ex_spy_2019_2025")
    if h1 is None or h2 is None:
        return ""
    if h1 > 0 > h2:
        return f"; toda la ventaja vino de 2012-2018 ({pts(h1)} al año) y en 2019-2025 perdió ({pts(h2)} al año)"
    if h2 > 0 > h1:
        return f"; perdió en 2012-2018 ({pts(h1)} al año) y ganó en 2019-2025 ({pts(h2)} al año)"
    return f"; en 2012-2018 {pts(h1)} al año y en 2019-2025 {pts(h2)}"


def main(datos, salida):
    R = json.load(open(f"{datos}/resultados.json", encoding="utf-8"))
    res, cob = R["res"], R["cobertura"]

    def funciona(r):
        return (r.get("veredicto", "").startswith("FUNCIONA") and (r.get("pctl_azar") or 0) >= 60
                and (r.get("t_spy") or 0) >= 1.5)

    def corto(r):
        if funciona(r):
            return "Le ganó al índice"
        if (r.get("pctl_azar") or 50) < 60 and r["ex_spy_media"] > -0.01:
            return "No se distingue del azar"
        return "Pierde contra el índice"

    pot, c34, c01, dc, d6 = (res["potencial_V2"], res["cast_3_4"], res["cast_0_1"], res["dir_cluster"],
                             res.get("dir_cluster_6m"))
    a12 = sorted(cob)[:12]                                   # 2011-06 .. 2012-05: la cobertura más baja
    c2012 = cob.get("2012-12-31") or cob[a12[-1]]
    pct_cob = 100 * (c2012[0] - c2012[1]) / c2012[0]
    out = {
        "act": dt.date.today().isoformat(),
        "como": ("Medido con las cuentas presentadas a la SEC (lo que se sabía en cada fecha, sin mirar al futuro) "
                 "y precios con dividendos: comprar cada mes las 20 primeras de la lista y aguantarlas un año, "
                 "de 2011 a 2025, frente al S&P 500."),
        "potencial": {
            "funciona": funciona(pot), "corto": corto(pot),
            "texto": (f"Comprando cada mes las 20 mejor puntuadas y aguantando un año: {p(pot['media'])} al año de media "
                      f"frente al {p(pot['spy_media'])} del S&P 500; {anios_txt(pot)}{mitades_txt(pot)}. "
                      f"Frente a 20 empresas elegidas al azar quedó en el percentil {pot['pctl_azar']:.0f} (50 = azar)."),
        },
        "castigadas": {
            "funciona": funciona(c34), "corto": corto(c34),
            "texto": (f"Las castigadas con 3-4 señales de salud dieron {p(c34['media'])} al año: algo mejor que las demás "
                      f"castigadas ({p(min(c01['media'], res['cast_2']['media']))} a {p(max(c01['media'], res['cast_2']['media']))}), "
                      f"pero menos que el S&P 500 ({p(c34['spy_media'])}); {anios_txt(c34).replace('ganó', 'ganaron')}."),
        },
        "directivos": {
            "funciona": funciona(dc), "corto": corto(dc),
            "texto": (f"Empresas con 2 o más directivos comprando en 90 días (las 20 de mayor importe): {p(dc['media'])} al año "
                      f"frente al {p(dc['spy_media'])} del S&P 500, con una mediana de {p(dc['mediana'])} frente a "
                      f"{p(dc['spy_mediana'])}; frente al azar, percentil {dc['pctl_azar']:.0f}."
                      + (f" A 6 meses tampoco: {pts(d6['ex_spy_media'])} frente al índice." if d6 and d6.get("ventanas") else "")),
        },
        "cobertura": (f"Ojo: de las empresas que había en 2012 solo hay precios de un {pct_cob:.0f} % (muchas dejaron de "
                      f"cotizar). Eso favorece a las listas, así que en la realidad lo habrían hecho algo peor."),
    }
    json.dump(out, open(salida, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for k in ("potencial", "castigadas", "directivos"):
        print(f"{k}: {out[k]['corto']}\n   {out[k]['texto']}")
    print(out["cobertura"])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
