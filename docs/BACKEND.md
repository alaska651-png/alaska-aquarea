# Backend setup

HACS manages only the frontend. The coordinator publishes `sensor.coordinatore_climatico` and `coordinatore_climatico_status` events; the card sends authenticated `coordinatore_climatico_command` events. The browser contains no MQTT or HA token.

## New Linux Docker installation

1. Clone this repository. Create a private `data/` directory.
2. Copy `examples/entity-map.example.json` to `data/entity-map.json`. Replace every example entity and admin ID. Preserve shared relay aliases. Configure floor IDs `night`, `day`, `basement`, legacy climates and competing automations.
3. Copy `examples/commissioning.example.json` to `data/commissioning.json`. Keep it disarmed while checking mapping and hydraulics. Never copy approvals from another installation.
4. Copy `examples/runtime.env.example` to private `runtime.env`; configure the reachable HA URL and private token. Restrict permissions and never commit it.
5. Build/start `docker compose up -d --build`. The engine creates settings with MASTER OFF; commissioning remains disabled. The Dockerfile supplies Python, aiohttp and timezone data.
6. Verify blockers, legacy control exclusion, sensor freshness, flow path, shared valves and Panasonic behavior before enabling physical control. Cooling requires separate verified commissioning.

Token provisioning and commissioning are manual. No public installer reads HA authentication storage, grants credentials, removes old thermostats or automatically arms control.

## Existing installation

Do not overwrite private mapping, settings, commissioning, manual-state or runtime.env with examples. Never run duplicate coordinators against the same relays. Back up private state and use fresh readback/state handoff for deliberate upgrades. A cold start without a usable recent snapshot may impose a minimum-off pause. A timer preserves its absolute deadline after restart, not a new duration.

`manual-state.json` stores timer deadlines and deduplication receipts. `settings.json` stores profiles and floor settings. `engine-state.json` restores recent memory only when configuration and HA relay readback agree.

## Tests

```sh
python -m pip install aiohttp==3.14.5
cd coordinator
python -m unittest discover -p 'test_*.py'
```

The confirmed synthetic test map is not intended for deployment. HACS does not install Docker services.
