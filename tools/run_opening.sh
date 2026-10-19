#!/bin/sh
# Restart PPSSPP on test_game, load save slot 2 (made right before the opening),
# pick difficulty and capture the opening cutscene into work/shots/<prefix>N.png + <prefix>sheet.png
P=${1:-r}
N=${2:-24}
T=/c/Users/HP/Downloads/psp/MGSPO_Arabic/tools
cd $T
powershell -Command "Get-Process PPSSPPWindows64 -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep 1; Start-Process -FilePath 'C:\Users\HP\Downloads\psp\PPSSPP\PPSSPPWindows64.exe' -ArgumentList '\"${GAME:-C:\Users\HP\Downloads\psp\MGSPO_Arabic\project\test_game}\"'"
sleep 22
python ppctl.py seq "start:8 wait:200" >/dev/null
python ppctl.py seq "circle:8 wait:200" >/dev/null   # LOAD GAME
python ppctl.py seq "circle:8 wait:300" >/dev/null   # pick save
python ppctl.py seq "circle:8 wait:150" >/dev/null   # load completed
sleep 2
python ppctl.py seq "circle:8 wait:60" >/dev/null
[ -n "$NODIFF" ] && exit 0
python ppctl.py seq "circle:8" >/dev/null            # difficulty NORMAL
sleep 12
L=""
for i in $(seq 1 $N); do sleep 1.5; python ppctl.py cap $P$i.png >/dev/null; L="$L $P$i.png"; done
cd ../work/shots && python ../../tools/sheet.py ${P}sheet.png $L
