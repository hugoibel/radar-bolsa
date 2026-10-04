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
nice -n 19 ionice -c3 python3 -u /root/radar_bolsa/colector.py "$MODO" --salida "$REPO/data" >> "$LOG" 2>&1 || exit 1

git add data
if ! git diff --cached --quiet; then
  # se vuelve a traer lo remoto justo antes: la pasada dura ~20 min y mientras tanto
  # pueden haber llegado cambios de la app desde el PC (no tocan data/, no chocan)
  git commit -qm "datos $MODO $(date -u +%F\ %H:%M) UTC" && git pull -q --rebase origin main >> "$LOG" 2>&1 \
    && git push -q origin main >> "$LOG" 2>&1
fi
