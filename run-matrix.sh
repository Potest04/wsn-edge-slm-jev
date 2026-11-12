#!/usr/bin/env bash
set -euo pipefail

project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
out=${1:-"$project/results/cooja"}

for topology in line mesh bottleneck; do
  for seed in 17 29 41; do
    case "$seed" in
      17) rx=1 ;;
      29) rx=0.93 ;;
      41) rx=0.86 ;;
    esac
    for stride in 1 3; do
      run="$out/$topology-$seed-s$stride"
      if [[ -s "$run/network.json" && -s "$run/events.log" ]]; then
        printf 'Rechecking existing %s / %s / stride %s\n' "$topology" "$seed" "$stride"
        python3 "$project/cooja.py" parse "$run/events.log" "$topology" "$seed" "$rx" "$stride" --out "$run"
      else
        printf 'Running %s / %s / stride %s\n' "$topology" "$seed" "$stride"
        bash "$project/run-cooja.sh" "$topology" "$seed" "$rx" "$stride" "$run"
      fi
    done
  done
done
