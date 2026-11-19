#!/bin/sh
# R21: delivery-count semantics of Redis 8.10.3 (throwaway container, not the Nyx stack)
C=r21-redis-probe; docker run -d --rm --name $C redis:8.10.3 >/dev/null
r(){ docker exec -i $C redis-cli "$@"; }; until r PING >/dev/null 2>&1; do :; done
cnt(){ r XPENDING s g - + 10 | paste - - - - | awk '{printf "%s/%s ", $2, $4}'; echo; }
r XGROUP CREATE s g '$' MKSTREAM >/dev/null; for i in 1 2 3 4 5; do r XADD s '*' n $i >/dev/null; done
r XREADGROUP GROUP g c1 COUNT 5 STREAMS s '>' >/dev/null; echo "XREADGROUP >        : $(cnt)"
r XPENDING s g - + 10 >/dev/null;                      echo "XPENDING (again)    : $(cnt)"
r XAUTOCLAIM s g c2 0 0-0 COUNT 5 JUSTID >/dev/null;   echo "XAUTOCLAIM JUSTID   : $(cnt)"
r XAUTOCLAIM s g c3 0 0-0 COUNT 5 >/dev/null;          echo "XAUTOCLAIM COUNT 5  : $(cnt)"
r XAUTOCLAIM s g c3 0 0-0 COUNT 2 >/dev/null;          echo "XAUTOCLAIM COUNT 2  : $(cnt)"
r XREADGROUP GROUP g c3 COUNT 5 STREAMS s 0 >/dev/null; echo "XREADGROUP 0 (own)  : $(cnt)"
docker rm -f $C >/dev/null
