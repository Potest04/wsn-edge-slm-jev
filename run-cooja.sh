#!/usr/bin/env bash
set -euo pipefail

project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export CONTIKI=${CONTIKI:-"$HOME/contiki-ng"}
export JAVA_HOME=${JAVA_HOME:-/usr/lib/jvm/java-25-openjdk-amd64}
export PATH="$JAVA_HOME/bin:$PATH"
cooja="$CONTIKI/tools/cooja"

if [[ $# -ne 5 ]]; then
  printf 'Usage: bash run-cooja.sh TOPOLOGY SEED RX_RATIO STRIDE OUTPUT_DIRECTORY\n' >&2
  exit 2
fi
topology=$1 seed=$2 rx_ratio=$3 stride=$4
mkdir -p -- "$5"
out=$(cd -- "$5" && pwd)

# Stage on a Linux filesystem for native Cooja shared-library compilation.
stage=$(mktemp -d "${TMPDIR:-/tmp}/wsn-edge-jev.XXXXXXXX")
trap 'rm -rf -- "$stage"' EXIT
cp -R -- "$project/firmware" "$stage/"
ln -s -- "$CONTIKI" "$stage/contiki-ng"
python3 "$project/cooja.py" config "$stage/experiment.csc" "$topology" "$seed" "$rx_ratio" "$stride"
cp -- "$stage/experiment.csc" "$out/experiment.csc"

(
  cd -- "$cooja"
  ./gradlew --console=plain --no-watch-fs run \
    --args="--no-gui --contiki=$CONTIKI --logdir=$stage $stage/experiment.csc"
) > "$out/cooja.log" 2>&1 || {
  tail -80 "$out/cooja.log" >&2
  exit 1
}
cp -- "$stage/COOJA.testlog" "$out/events.log"
python3 "$project/cooja.py" parse "$out/events.log" "$topology" "$seed" "$rx_ratio" "$stride" --out "$out"
