#!/bin/bash
# Чистый старт демо: журнал событий пуст, камеры начинают ролики с начала и за первый круг (~1,5 мин)
# заново записывают нарушения. Старый журнал сохраняется рядом с датой в имени.
#   ssh -p PORT root@HOST 'bash /workspace/linq/app/tools/demo_reset.sh'
set -e
D=/workspace/linq/data
supervisorctl stop linqguard >/dev/null
cp $D/events.db $D/events_$(date +%m%d_%H%M).db
python3 -c "import sqlite3; c = sqlite3.connect('$D/events.db'); c.execute('delete from events'); c.commit()"
supervisorctl start linqguard
echo "журнал очищен, копия: $D/events_$(date +%m%d_%H%M).db"
