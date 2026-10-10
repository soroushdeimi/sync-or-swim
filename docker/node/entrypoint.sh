#!/bin/bash
# Kernel state (dummy0, wg interfaces, tc qdiscs) lives in the container's
# network namespace and is lost on restart, so re-apply it before anything else.
set -euo pipefail

/usr/local/bin/sos-apply

exec "$@"
