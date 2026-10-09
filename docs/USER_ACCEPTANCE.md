# User acceptance

Run on a user-owned HOS test instance with the actual BYD account and car. Enter credentials locally. Do not attach raw response dumps to issues.

Record HOS/Python versions, package tag, country, model, operating system and test date. Use an HOS application launched through its normal GUI when validating the packaged runtime.

| Check | Expected evidence |
| --- | --- |
| Install | Exact ZIP hash verified; manifest and requirements installed; worker imports pyBYD |
| Status only | Without a PIN, vehicle discovery and telemetry work; remote controls unavailable |
| Discovery | Correct cars; distinct stable IDs; restarting does not duplicate the car |
| Telemetry | Battery/range/odometer agree with BYD app; unknown fields remain unknown; real zeros stay zero |
| Country | Account authenticates through its correct region |
| Location off | No GPS request and no coordinates in state |
| Location on | Only explicitly opted-in location appears; compare against app |
| Lock / unlock | Car physically changes state; command result is confirmed; subsequent telemetry agrees |
| Climate | Supported temperature/duration applied; start/stop confirmed by car and next state refresh |
| Other controls | Test each advertised find/flash/close-window command individually on a parked car |
| Bad PIN | No vehicle write; failure reported without exposing the PIN |
| Connectivity | Disconnect/reconnect network and HOS; backoff occurs; registration/state recover |
| Unknown result | No automatic write retry or fake success after timeout |
| Multi-car account | A command affects only its selected car |
| Upgrade | Update ZIP, restart instance; IDs and settings preserved |
| Uninstall | Remove active instance first, uninstall package, verify worker stops |
| Rollback | Reinstall the previously tested ZIP; state and commands recover |

Do not mark a row passed on API acceptance alone. Record the final device behavior and state result. Report only sanitized diagnostics.

## Publication gate

Community listing does not imply verified status or core inclusion. Before one-click Kitchen distribution: complete real-device and packaged-app acceptance, maintainer review, deterministic rebuild, approved signing, public artifact/hash verification and registry contract validation. Do not merge this integration into protected HOS core as part of community testing.
