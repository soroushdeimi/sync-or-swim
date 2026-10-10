#!/usr/bin/env bash
# Build, save and load the node image under the tag Ansible expects.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ansible="$root/.venv/bin/ansible"
cd "$root"

usage() { echo "usage: $0 {tag|build|save <file>|load <file>}" >&2; exit 1; }

tag() {
  # ask Ansible so the tag can never drift from inventory's node_image
  ANSIBLE_CALLBACK_RESULT_FORMAT=json \
    "$ansible" localhost -m ansible.builtin.debug -a 'var=node_image' -e "lab_root=$root" \
    | python3 -c 'import json, sys; print(json.loads(sys.stdin.read().split("=>", 1)[1])["node_image"])'
}

build() {
  local ref; ref=$(tag)
  docker image inspect "$ref" >/dev/null 2>&1 || docker build -t "$ref" "$root/docker/node"
}

save() {
  [ $# -eq 1 ] || usage
  build
  mkdir -p "$(dirname "$1")"
  docker save "$(tag)" | gzip > "$1"
}

load() {
  if [ $# -ne 1 ] || [ ! -f "$1" ]; then usage; fi
  local want got
  want=$(tag)
  got=$(gzip -dc "$1" | docker load | sed -n 's/^Loaded image: //p' | head -n1)
  if [ "$got" != "$want" ]; then
    echo "stale image artifact: got '$got', inventory expects '$want'" >&2
    exit 1
  fi
  echo "loaded $got"
}

cmd=${1:-}; shift || true
case "$cmd" in
  tag) tag ;;
  build) build ;;
  save) save "$@" ;;
  load) load "$@" ;;
  *) usage ;;
esac
