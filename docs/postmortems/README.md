# Postmortems

Post-incident root cause analyses for incidents encountered during development and operation.

## Template

Every postmortem covers the following sections:

- **Summary:** Concise statement of the incident, impact, and final resolution.
- **Impact:** Services, components, tests, or telemetry affected.
- **Timeline:** Sequence of events and observations.
- **Detection:** How the failure was observed or detected.
- **Root cause:** Technical explanation of why the failure occurred.
- **Resolution:** Immediate fix applied and verification steps.
- **What went well:** Systems, patterns, or behaviors that functioned as intended.
- **What went wrong:** Gaps in automation, tooling, testing, or isolation.
- **Action items:** Preventative measures and technical improvements with tracking status.

## Index

- [2026-10-10: FRR daemons fail to start (missing cap_sys_admin) and compose regression](2026-10-10-frr-cap-sys-admin.md)
- [2026-10-10: Sidecars orphaned in stale network namespace after node restart](2026-10-10-sidecar-network-namespace.md)
- [2026-10-10: Route monitor log test false-green and MySQL password leak in test argv](2026-10-10-log-test-false-green-and-password-leak.md)
- [2026-10-10: GitHub CI failures on clean runner (JSON callback, Docker < 28.1, restart race)](2026-10-10-ci-clean-machine-failures.md)
- [2026-10-12: Alerts on metrics that never had data](2026-10-12-alerts-on-metrics-that-did-not-exist.md)
