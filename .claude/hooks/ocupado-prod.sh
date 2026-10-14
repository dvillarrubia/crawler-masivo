#!/bin/bash
c=0
for s in pending queued running analyzing; do
  x=$(curl -s "http://127.0.0.1:8000/api/jobs?status=$s" | python3 -c "import json,sys;print(len(json.load(sys.stdin)['items']))") || x=1
  c=$((c+x))
done
l=$(docker exec crawler-masivo-postgres-1 psql -U crawler -d crawler_db -At -c "select count(*) from pg_locks where locktype='advisory'") || l=1
a=$(docker top crawler-masivo-worker -eo pid,args | grep -c "analysis.analyzer")
echo "$c $l $a"
