# Architecture and distribution boundary

HeishaMon WT32-ETH01 → MQTT / existing HA integration → coordinator → authenticated HA calls and status events → dashboard.

The coordinator is the single writer for room relay requests. Timers override only the selected physical circuit, remain subordinate to safety/master/floor/sensor gates, preserve minimum-off timing and return to automatic control at expiry. A shared kitchen/bath circuit cannot be independently commanded.

The optional Aquarea Clima iframe uses an HA-native session gateway. Its companion backend proxies telemetry and separately guarded Digital Twin plan/execute requests. Common collection and COP recording are passive. Keep private backend ports private; do not pass long-lived HA tokens to an iframe.

## Included

- `dist/`: HACS frontend, coordinator card and optional Aquarea Clima host.
- `src/`: frontend build inputs with generic room identifiers.
- `coordinator/`: engine, daemon, mode policy, supervisor, health check and synthetic tests.
- `companion/`: companion source snapshot, deployment addresses replaced by loopback and private configuration excluded.
- `examples/`: generic, disabled templates.

## Excluded

Real device/room mappings, admin IDs, credentials, bridge keys, commissioning approvals, raw history, installation photographs, databases, firmware and external dependency distributions.

Companion services need their own private configuration and provisioning. They are reference sources; this release does not claim a clean-host, one-click companion deployment. The separately provisioned HA gateway under `companion/custom_components` is not the HACS installation target.

The public source uses generic room keys. Existing installations must retain matching frontend/backend versions and mapping; do not mix an installation-specific map with renamed public keys.
