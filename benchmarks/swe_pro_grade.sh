#!/usr/bin/env bash
# AC-014. Grade each phase in a fresh official container without networking or host mounts.
set -euo pipefail
workdir=$(realpath "${1:?expected the pilot artifact directory}")
phase=${2:?expected baseline or prediction}
case "$phase" in
  baseline|prediction) ;;
  *) exit 2 ;;
esac
image=ghcr.io/scaleapi/swe-bench_pro-v2@sha256:03205f63224d992c930df7a2b898353d9d3dface4ab4d921eebc73dc909c1ba6
docker image inspect "$image" > /dev/null
container=$(docker create --pull=never --network none --cap-drop ALL --security-opt no-new-privileges \
  --cpus 1 --memory 2g --pids-limit 256 --entrypoint bash "$image" -c '
    set -eu
    cd /app
    echo "dfe771112955beaaa36c6079882f743169f7c88eb4815a7d6576761fac59f531  openlibrary/plugins/importapi/import_validator.py" | sha256sum -c -
    mkdir -p /logs/verifier
    if [ -s /tmp/candidate.patch ]; then git apply --check /tmp/candidate.patch; git apply /tmp/candidate.patch; fi
    bash /tests/test.sh')
trap 'docker rm -f "$container" >/dev/null' EXIT
destination="$workdir/$phase"
if [[ -e "$destination" ]]; then
  echo "Refusing to overwrite existing $phase evidence" >&2
  exit 1
fi
stage=$(mktemp -d "$workdir/$phase.inprogress.XXXXXX")
docker cp "$workdir/tests" "$container:/tests"
if [[ "$phase" == prediction ]]; then
  docker cp "$workdir/candidate.patch" "$container:/tmp/candidate.patch"
fi
# A nonzero official verifier exit is a scored failure when its reward and parsed output exist.
set +e
timeout 3000s docker start -a "$container" > "$stage/verifier.log" 2>&1
set -e
test "$(docker inspect --format '{{.State.Running}}' "$container")" = false
docker inspect --format '{{json .State}}' "$container" > "$stage/container-state.json"
docker cp "$container:/logs/verifier/." "$stage"
test -s "$stage/reward.txt"
test -s "$stage/output.json"
mv -T "$stage" "$destination"
printf '%s reward %s\n' "$phase" "$(cat "$destination/reward.txt")"
