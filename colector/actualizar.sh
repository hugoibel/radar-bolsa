#!/bin/bash
# Radar Bolsa — lo lanza el cron del VPS. Recoge los datos y los publica en GitHub Pages.
#   actualizar.sh completo   (1 vez al día, tras el cierre de la bolsa)
#   actualizar.sh rapido     (cada 4 horas: calendario de salidas a bolsa + noticias)
MODO=${1:-rapido}
REPO=/root/radar-bolsa-repo
LOG=/root/radar_bolsa/colector.log

exec 9>/tmp/radar_bolsa.lock
flock -n 9 || { echo "$(date -u +%F\ %T) ya hay una pasada en marcha" >> "$LOG"; exit 0; }

cd "$REPO" || exit 1
git pull -q --rebase origin main >> "$LOG" 2>&1
echo "=== $(date -u +%F\ %T) UTC — $MODO" >> "$LOG"
# 2026-10-06: si una pasada falla, email (como mucho uno cada 12 h) para que los datos no se
# queden viejos sin que nadie se entere; la app además marca ⚠️ si tienen más de 60 h.
if ! nice -n 19 ionice -c3 python3 -u /root/radar_bolsa/colector.py "$MODO" --salida "$REPO/data" >> "$LOG" 2>&1; then
  ST=/root/radar_bolsa/.ultimo_aviso_fallo
  if [ ! -f "$ST" ] || [ $(( $(date +%s) - $(stat -c %Y "$ST") )) -gt 43200 ]; then
    touch "$ST"
    /usr/bin/python3 /root/avisar.py "Radar Bolsa: fallo en la pasada $MODO" "$(tail -25 "$LOG")" > /dev/null 2>&1
  fi
  exit 1
fi

git add data
if ! git diff --cached --quiet; then
  # se vuelve a traer lo remoto justo antes: la pasada dura ~20 min y mientras tanto
  # pueden haber llegado cambios de la app desde el PC (no tocan data/, no chocan)
  git commit -qm "datos $MODO $(date -u +%F\ %H:%M) UTC" && git pull -q --rebase origin main >> "$LOG" 2>&1 \
    && git push -q origin main >> "$LOG" 2>&1
fi
