# Developer beta validation

Version: `0.1.0-beta.1`. Local checks recorded on 2026-10-09.

| Check | Result | Scope |
| --- | --- | --- |
| Python tests | 41 passed | Discovery, measured state, command confirmation, capabilities, PIN gating, request correlation/deduplication, redaction, throttling, cancellation, real loopback WebSocket with mocked BYD client, packaging |
| Ruff | Passed | Configured error and undefined-name checks |
| Dependency check | Passed | Actual pyBYD 0.0.77 imports, Python 3.13.13, protocol version 1; no BYD calls |
| Package reproducibility | Passed | Identical bytes across two builds; three allowlisted root files; embedded license notices |
| HOS regression tests | 14 passed across 3 suites | `integration-package-installer`, `driver-package-zip-safety`, `driver-ws-server` |
| Actual ZIP through HOS installer | Passed | Real installer/catalog activation, exact installed source, actual dependency venv installation and installed `--check`, disposable temporary storage |
| Public registry validator | Passed | Two listings accepted, including the new source-only community entry |
| GitHub Actions | Passed | [Run 37911481627](https://github.com/Cantata-Communication-Solutions/haptique-byd-vehicle/actions/runs/37911481627): all six Linux/macOS/Windows × Python 3.11/3.13 jobs passed for source commit `07ba171746e94cfc3b231cb6034e2712a6765440` |

HOS source used for installer/regression checks: `c0b9aa5f47b3795596f000e3e224518699153b88`. Its source files were unchanged. Dependencies were supplied from the existing development checkout. No installed app, account, production service or user database was modified.

Reproduce the HOS installer check with a HOS checkout whose Node dependencies are installed:

```sh
python scripts/build_package.py
node scripts/verify_hos_package.cjs /absolute/path/to/hos --install-deps
```

The script overrides runtime storage with a new temporary directory and removes it afterward. It contacts PyPI only when installing dependencies, and never contacts BYD. Set `HAPTIQUE_PYTHON_BIN` locally if the default Python is unsuitable.

GitHub Actions runs the driver checks on Linux, macOS and Windows with Python 3.11 and 3.13. The run linked above confirms the original driver source; consult Actions for subsequent commits.

## Pending acceptance

- BYD authentication and vehicle discovery using a real account in its actual region.
- Measured vehicle telemetry, physical command execution and state convergence, including sleeping/offline cars and ambiguous timeouts.
- Normal packaged HOS GUI launch, Python discovery, settings entry, Logical Device rendering and control flow.
- Upgrade, uninstall and rollback in a user's normal HOS installation.
- Acceptance and release of the HOS unsigned community-install runtime policy, plus acceptance of the registry artifact listing, before existing users can install from Kitchen without a signature.

Use [USER_ACCEPTANCE.md](USER_ACCEPTANCE.md) to record these checks. The release remains a community developer beta until they pass. Automated BYD behavior tests use test doubles; they do not demonstrate real car control.

The unsigned registry checksum test exposed Windows CRLF checkout conversion: the rebuilt ZIP differed from the public LF-based release. The same checksum mismatch was reproduced locally using CRLF copies. `.gitattributes` fixes the three runtime package files to LF so Windows rebuilds preserve the existing published ZIP checksum. The beta release/tag/assets remain unchanged.

The public registry has an existing dependency-audit finding in its validator dependencies. No registry dependency or schema change is included in this driver submission.
