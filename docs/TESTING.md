# Testing plan

**Status: started.** `tests/test_identity.py` covers the camera-identity functions in `api.py`
(the frozen keying function and the MAC comparison), loaded without Home Assistant by
`tests/conftest.py`. Run with `pip install pytest aiohttp pycryptodome && pytest tests/`. CI
does not run the tests yet. The rest of this page is still a plan.

It was written up from a code review on 2026-10-23 so the work can be picked up later without
re-deriving it. **Everything here is a proposal and may be revised** — if you are returning to
this and the shape no longer fits, change it rather than following it out of obligation.

---

## Why bother

CI today is `validate` (HACS) and `validate-hacs` (hassfest). Both check manifest and
repository structure. **Neither executes a single line of the integration.** 5,300 lines,
four device protocols, zero assertions.

The concrete argument: three bugs fixed in v0.7.5b2 were each one line, and each would have
been caught by a two-line test.

| Bug | Test that would have caught it |
|---|---|
| `async_create_task(async_schedule_reload(...))` → `TypeError` | asserting the SD-card listener runs without raising |
| `proc.communicate()` with no timeout | asserting the audio converter honours a timeout |
| `int("")` risk in `number.py` value_fn | one parametrised case with `""` |

A fourth — the `errCode`-missing convention being inverted between two modules — is exactly
the kind of thing one shared test over both call sites pins down permanently.

---

## Phase 1 — pure functions, no Home Assistant at all

**Start here.** These need nothing but `pytest`. No `hass`, no event loop, no network, no
`pytest-homeassistant-custom-component`. They are also where the firmware's "returns anything"
behaviour lives, which is this project's single largest source of real bugs.

```bash
pip install pytest
pytest tests/test_parsers.py
```

| Module | Functions | What to pin down |
|---|---|---|
| `entity.py` | `_decode`, `_clean_firmware`, `_clean_model` | `"InSight%20S245"` → `InSight S245`; `"VIGI%20C350%201.0"`; `None`; `""` |
| `sensor.py` | `_parse_bytes`, `_parse_gb`, `_used_percent`, `_seconds_to_hours`, `_unix_to_datetime` | `"125403037696B"`, `"116.8GB"`, `"0B"`, `"59.2G"`, `""`, `None`, `{}`, a bare `int`. This is the firmware-returns-anything surface and it has already caused one round of "SD sensors always unknown" |
| `coordinator.py` | `_detect_sd_card` | `"Normal"`, `"normal"`, `"full"`, `"offline"`, `""`, missing key |
| `light.py` | `_cam_to_ha`, `_ha_to_cam` | round-trip property: `_ha_to_cam(_cam_to_ha(n)) == n` for n in 1..4. Also pins the documented brightness bands |
| `openapi.py` | `_err_code` / `err_code` | `errCode` present, `error_code` present, both, **neither** (must be success — this is the convention that was inverted elsewhere) |
| `onvif_events.py` | `TOPIC_KEYWORD_MAP` ordering via `_dispatch` | first-match-wins: `CellMotion` before `Motion`, `TPSmartEvent` first. **This ordering is load-bearing and currently unverified** — reordering the dict silently misroutes events |
| `openapi_events.py` | `_EVENT_TYPE_MAP` via `_dispatch` | mapped, `None`-mapped (deliberately quiet, handled by ONVIF), unknown (should log) |
| `onvif_events.py` / `onvif_ptz.py` | `_envelope` | a username containing `&`, `<`, `>` must still produce parseable XML — currently it does not, see "Known gaps" |
| `event_image.py` | `_ws_encode`, `_ws_recv`, `_parse_multipart`, `_digest_response` | `_ws_recv` takes a `StreamReader` you can feed from bytes; `_parse_multipart` takes bytes. **The hand-rolled WebSocket codec is the highest-risk code in the repo and among the easiest to test** |
| `api.py` | `_encrypt_password` | deterministic given a fixed key and nonce; pins the MD5-uppercase-prefix contract the whole login depends on |
| `__init__.py` | `_wav_duration` | header-only input, and input shorter than the header (currently returns a negative number) |

## Phase 2 — HTTP, still no Home Assistant

With `aioresponses`, no HA harness needed:

- `VIGICamera.authenticate` — both stok response shapes (see the docstring at `api.py:7`)
- `_request`'s `-40401` re-authenticate-once path
- `get_storage`'s three response shapes (list, dict, `hd_info_1` double-wrapped)
- `VIGIOpenAPI.call`'s `-10002` retry, and that a non-zero `errCode` raises

## Phase 3 — Home Assistant harness

`pytest-homeassistant-custom-component`, pinned to the HA version in CI:

- `test_init.py` — setup → unload is clean; no leaked sessions or tasks
- `test_config_flow.py` — `invalid_auth`, `cannot_connect`, `already_configured`, and the
  **missing reauth step** (see below)

---

## Known gaps this plan would expose

Found in review, **not yet fixed** as of 0.7.5b5, listed so a test written against current behaviour does
not accidentally enshrine the bug:

- **Username is interpolated into SOAP XML unescaped** (`onvif_events.py`, `onvif_ptz.py`). A
  username containing `&`, `<` or `>` produces a malformed envelope and an endless
  "ONVIF subscribe failed" loop with no hint as to the cause.
- **ONVIF XML is parsed by regex with hardcoded namespace prefixes.** Prefixes are arbitrary;
  a camera binding the same namespaces to different prefixes parses as zero events, silently.
- **`has_openapi` has two sources of truth.** `coordinator._recheck_openapi` promotes the
  coordinator's copy but leaves `entry_data["openapi"]` as `None` until a reload — which
  re-creates the exact read/write split `_openapi_writable` exists to prevent.
- **A transient poll failure permanently removes entities.** `safe_get` turns any per-key
  error into `{}`, and platforms decide whether an entity exists at all from that dict at
  setup. One failed `get_image_common` on first refresh and the image controls, night-vision
  select and spotlight are gone until the user notices and reloads. (A *whole-camera* outage
  is handled since 0.7.5b5 — entities go unavailable — but a single failed key still reads
  as "off" or "not supported".)
- **No MAC check at setup or polling.** Two cameras that swap DHCP addresses and share a
  password silently swap identities; only reauth compares the MAC.
- **`is_allowed_path` is called in the event loop** (`_fetch_audio_bytes`); HA documents it as
  blocking I/O.
- **The SD-card False→True reload can repeat** on a camera whose storage read keeps failing
  during the first refresh, each reload creating a new ONVIF subscription.
- **Frigate detection matches the IP as a substring** (`frigate.py`), so `192.168.1.1`
  matches `.10`–`.19`, and the config shape it reads may not match the real integration.
- **`number.py` coerces with bare `int()`** on a field that can be `""`.
- **Snapshot cache has no staleness limit on the failure path** (`camera.py`), so a dead
  camera serves an hours-old frame instead of going unavailable.

## The structural finding behind all of this

Per [ADR-0004](https://github.com/home-assistant/architecture/blob/master/adr/0004-webscraping.md),
device communication belongs in a PyPI library, not in the integration. ~1,500 lines of
protocol live here. That is also *why* there are no tests — and the single biggest blocker to
Phase 3 is that all 11 service handlers are closures inside `_register_services`, so they
cannot be imported, so they cannot be exercised without booting HA. Moving them to module
level taking `(hass, call)` is worth more than any individual test below.

Extracting `api.py` / `openapi.py` / `onvif_*.py` / `event_image.py` into a `pyvigi` package is
the long-term shape. **Phase 1 does not require it** and should not wait for it.
