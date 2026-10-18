#!/bin/bash
c=0
# La API pide token (decision 134): se lee del .env del servidor, donde vive.
t=$(grep -E '^API_TOKEN=' /opt/crawler-masivo/.env 2>/dev/null | head -1 | cut -d= -f2-)
for s in pending queued running analyzing; do
  x=$(curl -s -H "Authorization: Bearer $t" "http://127.0.0.1:8000/api/jobs?status=$s" | python3 -c "import json,sys;print(len(json.load(sys.stdin)['items']))") || x=1
  c=$((c+x))
done
l=$(docker exec crawler-masivo-postgres-1 psql -U crawler -d crawler_db -At -c "select count(*) from pg_locks where locktype='advisory'") || l=1
a=$(docker top crawler-masivo-worker -eo pid,args | grep -c "analysis.analyzer")
echo "$c $l $a"
