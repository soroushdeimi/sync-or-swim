#!/usr/bin/env python3
"""Fail unless every host in an ansible-playbook PLAY RECAP has changed=0, failed=0 and unreachable=0."""
import re
import sys

text = open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()
recap = text.rpartition("PLAY RECAP")[2]
hosts = re.findall(r"^(\S+)\s*:\s.*?changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)", recap, re.M)
if not hosts:
    sys.exit("no PLAY RECAP found")
bad = [f"{h} changed={c} unreachable={u} failed={f}" for h, c, u, f in hosts if (c, u, f) != ("0", "0", "0")]
if bad:
    sys.exit("not idempotent: " + ", ".join(bad))
print(f"idempotent: {len(hosts)} hosts, changed=0")
