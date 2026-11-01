#!/bin/zsh
# usage: pair.sh <before checkout> <after checkout> <runs>   Alternates before/after runs of the three sites; prints one line per run.
BEFORE=$1; AFTER=$2; N=${3:-5}; HERE=${0:A:h}
for i in $(seq 1 $N); do
  for site in greenhouse workday successfactors; do
    for side in before after; do
      root=$BEFORE; [ $side = after ] && root=$AFTER
      line=$(CODE_ROOT=$root $HERE/go.sh $AFTER $site $side-$site-$i | head -1)
      echo "$side $site $i $(echo $line | python3 -c 'import sys,json; l=sys.stdin.read(); d=json.loads(l[l.index("{"):]); print(d["state"], d["wall"], d["received"], d["model_calls"])')"
    done
  done
done
