# Alaska Aquarea

![Architecture](docs/architecture.svg)

Home Assistant dashboard and climate coordinator for Panasonic Aquarea, developed alongside **[HeishaMon WT32-ETH01 + CZ-TAW1](https://github.com/alaska651-png/heishamon-wt32-eth01-cz)**.

**HACS installs the frontend only.** The coordinator is a separate Docker service and must be configured before the card can operate. HACS does not flash hardware, start Docker services, create credentials or enable heating.

## Features

- Independent floor control, weekly Comfort/Eco scheduling and room setpoints.
- Optional floor-average mode, percentage band and grouped valve requests.
- Temporary manual room requests: **0 / 15 / 30 / 45 / 60 / 90 / 120 minutes**. Zero cancels; expiry returns to automatic control instead of forcing OFF.
- Persistent server timers, request deduplication, sensor validation and minimum-off guards.
- Pump, buffer, outdoor conditions, Quiet, energy and COP displays from Home Assistant.
- Links to the optional Aquarea Clima schematic, date navigation and reported HA relay states.

## Install through HACS

1. Open **HACS → menu → Custom repositories**.
2. Add `https://github.com/alaska651-png/alaska-aquarea`, category **Dashboard**.
3. Download **Alaska Aquarea** and reload your browser.
4. If HACS did not register the resource, add `/hacsfiles/alaska-aquarea/alaska-aquarea.js` as a JavaScript module.
5. Add a card to a panel view:

```yaml
type: custom:coordinatore-climatico-card
```

[Open this repository in HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=alaska651-png&repository=alaska-aquarea&category=plugin)

Remove an older manually registered `coordinatore-card.bundle.js` resource before loading the HACS resource. Register the frontend once. Existing installations must retain their private backend settings and matching mapping/version.

## Backend

See [backend setup](docs/BACKEND.md). `coordinator/` contains the engine, daemon and supervisor. Example configurations use generic identifiers and are deliberately disarmed. They are not a copy of anyone's installation.

Hardware and firmware instructions remain in the linked firmware repository. No HeishaMon firmware is redistributed here.

## Advanced companion sources

`companion/` preserves the Aquarea Clima web interface, passive common collector, COP recorder, guarded Digital Twin service and HA session gateway. These are **advanced integration sources**, not an automatically installed HACS integration. Private configuration, inventories, history, secrets and deployment files are excluded. See [architecture and boundaries](docs/ARCHITECTURE.md).

## Validation and limits

The engine was exercised with mocked tests and the original deployment. Public tests use synthetic mapping. CI checks syntax, frontend data handling, coordinator regressions and HACS repository structure. No CI job connects to a heat pump. A release is not certification of hydraulic safety, a valve end contact or a cooling installation. Commission your own installation before arming it.

[Changelog](CHANGELOG.md) · [Security](SECURITY.md) · [MIT license](LICENSE)

Panasonic and HeishaMon names identify compatibility. This is an independent project, not an official Panasonic product.
