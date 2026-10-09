# BYD Vehicle for Haptique OS

Standalone Python community driver, distributed through the [Haptique Kitchen registry](https://github.com/Cantata-Communication-Solutions/haptique-kitchen-registry).

**Developer beta: `0.1.0-beta.1`. Real-vehicle acceptance is pending.** This repository contains no HOS core changes and is not a built-in integration.

## Supported scope

- Account vehicle discovery with stable, account-scoped Logical Device identifiers.
- Battery, range, odometer, cabin temperature, lock, charging and online status.
- Lock/unlock, start/stop climate, find car, flash lights and close windows when the vehicle reports support and a control PIN is configured.
- Optional location sharing, disabled by default.
- Confirmed command results with request correlation, duplicate-request protection, conservative polling and reconnect backoff.

The driver uses [pyBYD](https://github.com/jkaberg/pyBYD); Home Assistant is not required. Vehicle communication uses BYD's cloud service, so internet access and an eligible BYD account are required. Available data and commands depend on vehicle model, account permissions and region. The package deliberately uses the existing generic sensor Logical Device layout and typed commands; it does not add a new vehicle dashboard.

## Try the beta in HOS

1. Install Python 3.11 or newer on the controller, discoverable by the packaged HOS application. Check the application launch path as well as a terminal launch.
2. Download the ZIP and checksum from [Releases](https://github.com/Cantata-Communication-Solutions/haptique-byd-vehicle/releases).
3. Verify the ZIP against its `.sha256` file.
4. In HOS Integration Manager, choose **Upload Driver**, select the ZIP and enable Python dependency installation.
5. Add a **BYD Vehicle** instance. Enter the BYD account, password and account country (for example `IN` or `GB`). Set the control PIN only if remote controls are wanted.
6. Start/test the instance. Each discovered car appears as a Logical Device with status and its supported command catalog. Use the normal Logical Device command flow for car controls.

| Setting | Value |
| --- | --- |
| Account country | Two-letter code matching the BYD account |
| Control PIN | Six-digit PIN configured in the BYD app; optional |
| Refresh interval | 900 seconds by default; 300–28800 seconds |
| Share location | Off by default |
| Controller address | Leave empty on the controller; advanced override for SDK development |

A dedicated BYD account can reduce conflicts with the main mobile app's session. Frequent refreshes wake the vehicle and can drain its battery. Manual refresh is limited to once per 30 seconds; writes are never automatically retried. A timeout is an uncertain outcome: check the car before retrying.

## Registry status

The submission JSON is in `registry/com.haptique.community.byd-vehicle.json`, with trust level `community`. It is intentionally **source-only** until Haptique reviews and signs a final artifact. Current HOS requires an approved Ed25519 signature and checksum for one-click Kitchen installation. The public registry schema also needs its existing signature-contract reconciliation before it can accept signed artifact metadata. Local ZIP upload is the developer-testing path described above.

After review: Haptique rebuilds the allowlisted ZIP, signs its exact hash through the approved signer, publishes the exact artifact, and adds `artifact.downloadUrl`, `artifact.sha256`, `artifact.signature` and `artifact.signingKeyId` to the listing. No private signing keys are given to developers or committed here.

## Developer setup

```sh
python3.11 -m venv .venv
# Windows: use .venv\Scripts\python.exe instead of .venv/bin/python.
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python byd_vehicle.py --check
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python scripts/build_package.py
```

Developers with a HOS source checkout can also run the real installer check documented in [validation evidence](docs/VALIDATION.md).

The ZIP contains exactly one Python entry point, one driver manifest and pinned requirements, matching HOS's runtime allowlist. License notices are embedded in the Python entry point; documentation stays in this repository. Tests, environment files, credentials and build tools are excluded. Rebuilding unchanged source produces the same bytes.

The installed Python subprocess connects to HOS's existing raw WebSocket SDK at `/driver` for discovery, state, telemetry and typed command dispatch. It registers protocol version `1`, using `HAPTIQUE_DRIVER_ID` as its instance identity. This avoids requiring a BYD adapter in HOS core. The default local address uses inherited `PORT` or port `8080`; set `HOS_WS_URL` for a different address. Non-loopback connections must use `wss://`.

Environment names mirror the manifest's `envMap`: `BYD_USERNAME`, `BYD_PASSWORD`, `BYD_COUNTRY_CODE`, `BYD_CONTROL_PIN`, `BYD_POLL_SECONDS`, `BYD_INCLUDE_LOCATION`, `BYD_LANGUAGE`, `BYD_TIME_ZONE`, `HOS_WS_URL`. Configure secrets locally through HOS or your process environment; do not put them in command-line arguments, issues, commits or registry metadata.

`--transport stdio` supports direct protocol debugging with newline-delimited JSON. Normal installed operation uses WebSocket transport because HOS's typed Logical Device command dispatcher requires an adapter or SDK session. Do not start an additional external worker using an installed instance's identifier.

## Validation boundaries

Automated checks cover the real pinned dependency imports, typed pyBYD model mapping, driver behavior with test doubles, a real loopback WebSocket connection, and reproducible package boundaries. They do not prove BYD login, actual car control, packaged GUI runtime discovery, or real-device state convergence. See [user acceptance](docs/USER_ACCEPTANCE.md) and [validation evidence](docs/VALIDATION.md). Keep the package in community testing until those checks pass.

When reporting an issue, include the HOS version, Python version, country, vehicle model, command, and redacted result. Do not include VINs, location coordinates, passwords, tokens, PINs or raw BYD response dumps.
