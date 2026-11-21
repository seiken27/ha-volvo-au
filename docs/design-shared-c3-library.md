# Design: a shared C3 library for Volvo C3 and Polestar C3

Status: proposal, nothing implemented. Written 21 November 2026.

## Summary

Volvo C3 ([this repo](https://github.com/BeauGiles/ha-volvo-c3)) and Polestar C3 ([ha-polestar-c3](https://github.com/BeauGiles/ha-polestar-c3)) talk to the same backend, Volvo Cars' C3 connected-car platform. Today each carries its own copy of the code that speaks to it. This document proposes extracting the shared part into one library, keeping the brand-specific sign-in and transport separate, and moving both integrations onto it in small steps.

The two Home Assistant layers were already harmonised in November 2026 (entity names, states, attributes, behaviour). This is the matching step one level down.

## Why

- **Every backend fix has been done twice.** Command refusal reasons, the status lifecycle, Target SoC and amp limit refresh, and the tyre and service grades were each written once per repo.
- **The protocol knowledge is duplicated.** Service paths, message layouts and enum values were reverse engineered separately and can drift apart.
- **The Polestar library is a fork** of `kildahldev/unofficial-polestar-api`. Cherry-picking upstream fixes works but needs manual adaptation (for example the PCCS backend this fork removed).
- **Volvo's client is one 1,378-line file** (`volvo_api.py`) with transport, auth, protobuf encoding and endpoint logic mixed together, which makes it hard to test.

## What is the same and what is different

Evidence from the two code bases (21 November 2026):

| Area | Volvo C3 | Polestar C3 | Shared? |
| --- | --- | --- | --- |
| Services called | `chronos.services.v1.*` (TargetSoc, AmpLimit, ChargeNow), `v2.GlobalChargeTimerService`, `invocation.InvocationService`, `services.vehiclestates.*`, `dtlinternet`, `weather`, `ota_mobcache` | The same service paths (`backend.py`, `C3` profile), plus `ParkingClimateTimerService`, `ChargeLocationService`, `car_information`, `PrivacySettings` | **Yes**, same paths |
| Wire format | Hand-rolled protobuf in a gRPC frame | Hand-rolled protobuf (`codec.py`, `wire.py`) | **Yes**, a codec that can be shared |
| Command status | Chronos lifecycle and invocation status, decoded in `volvo_api.py` | `ChronosStatus`, `CHRONOS_SUCCESS_STATES`, `ResponseStatus` in `models/common.py` | **Yes**, one decoder |
| Data models | Dicts and helper modules (`powertrain.py`, `climate.py`, `journal.py`) | Typed models in `models/` (24 files) | **Yes**, Polestar's models are the starting point |
| Sign-in | Volvo ID OAuth with DPoP keys and an app installation ID | Polestar ID OIDC | **No**, separate identity systems |
| Transport | gRPC frames built by hand and sent with `httpx.Client(http2=True)` (a synchronous client); `aiohttp` for sign-in and streams | `grpclib` with an endpoint discovery step | **Not yet**, could converge |
| App identity | User agent `vca-ios/7.6.0`, Volvo client ID | `PolestarApp/5.15.1b9417 Android/14` | **No**, per brand |
| Extras | Driving journal, live update streams, polling tiers | GraphQL app-backend query | **No**, per brand |

Rough size today: Volvo about 5,500 lines in `custom_components/volvo_au`, of which `volvo_api.py` is the client; Polestar about 4,500 lines in `src/polestar_api` (codec, wire, grpc, connection, auth, discovery, 22 service clients, 24 models).

## Proposed architecture

Three layers, from the car outwards:

```
Home Assistant integrations     volvo_au          polestar
   (entities, config flow,        |                 |
    polling, translations)        |                 |
                                  v                 v
Brand packages                  c3-volvo          c3-polestar
   (sign-in, tokens, app          sign-in,          sign-in,
    identity, transport,          DPoP,             discovery,
    brand extras)                 httpx transport   grpclib transport
                                  \                 /
                                   v               v
Shared core                           c3-core
   (codec, Chronos envelope, service paths, status decoding,
    typed models, command refusal reasons, capability flags)
```

### `c3-core` (shared)

- **Codec and framing:** protobuf encode and decode, gRPC frame build and parse. Transport independent: it turns bytes into models and back.
- **Service definitions:** the C3 service paths in one profile, and request builders for each call.
- **Typed models:** start from Polestar's `models/`. Volvo's dict-based results move onto them.
- **Status handling:** one place that decodes the Chronos lifecycle, the invocation status, lock errors and refusal reasons (privacy mode, car being driven, another command in progress, timeout). This is where most duplicated fixes lived.
- **Capability flags:** one reading of the car record (`reportsTyrePressure`, window support, climate timer slots and so on).
- **A `Transport` interface:** `async def unary(path, payload, headers) -> bytes` and a stream equivalent. The core never imports `httpx`, `aiohttp` or `grpclib`. (Volvo's gRPC calls currently use a synchronous `httpx` client, so its transport would run them off the event loop, as today.)

### Brand packages

- **`c3-volvo`:** Volvo OAuth, DPoP key handling, refresh token rotation, the iOS gateway transport, Volvo-only features (driving journal, live update streams).
- **`c3-polestar`:** Polestar OIDC, endpoint discovery, the `grpclib` transport, the GraphQL query.
- Each depends on `c3-core` and exposes one client class with the same method names where the capability is the same.

### The Home Assistant integrations

Stay separate repos, stay thin. They keep their entities, config flow, translations and polling logic, and call the brand client.

## Packaging options

1. **Vendor the core into each integration** (a sync script, as Polestar already has in `scripts/sync_vendor.py`). Lowest friction. Home Assistant needs no extra install, and HACS keeps working. The cost is keeping two copies in sync.
2. **Publish `c3-core` to PyPI** and list it in each `manifest.json` `requirements`. Cleanest, but needs releases and a version contract, and a broken release affects both integrations at once.
3. **A single monorepo** for core plus both integrations. Simplest to change in one pull request, but it breaks the current "one repo per integration" HACS layout.

**Recommendation:** start with option 1 (vendored, from one source repo), and move to option 2 only if the core stabilises.

## Migration plan

Do this in order, in small PRs, with the integrations working after every step.

1. **Capture fixtures.** Record real, anonymised replies from both cars for each call (battery, availability, charge timers, climate, commands including a refusal). Store them as test files. This is the safety net for everything below, because the existing tests use synthetic replies.
2. **Extract the codec and status decoding.** Move Polestar's `codec.py`, `wire.py` and the status and refusal logic into `c3-core`. Polestar's tests must still pass unchanged.
3. **Move the service definitions and models.** The `BackendProfile` and typed models become part of the core.
4. **Introduce the `Transport` interface.** Wrap Polestar's `grpclib` code behind it, with no behaviour change.
5. **Migrate Volvo piece by piece.** Replace `volvo_api.py` endpoint by endpoint with core calls on an `httpx` transport: read-only calls first (battery, odometer, availability), then settings (Target SoC, amp limit), then commands. Keep Volvo's tests passing at every step.
6. **Converge transports if worthwhile.** Decide whether Volvo can use `grpclib` too. Not required.
7. **Delete the duplicates**, and the vendored fork divergence notes.

Do the `volvo_au` domain migration (renaming the domain to match the Volvo C3 name) **before** step 5, so two large changes do not overlap in one release.

## Risks

- **Regressions the tests cannot see.** The Volvo refusal decoder has never met a real refusal, and some Polestar paths rest on synthetic replies. Step 1 exists for this reason; do not skip it.
- **Unofficial, undocumented backends.** Volvo or Polestar can change things at any time. Shared code must tolerate unknown fields and statuses, and fail soft.
- **Divergence from upstream.** The Polestar library is a fork of `kildahldev/unofficial-polestar-api`. A shared core ends easy cherry-picking from it. Mitigation: keep a note of the upstream PRs taken (as we do in the changelog) and port by hand.
- **Coupled releases.** With a shared core, a bug can break both integrations at once. Mitigation: vendoring, and one release at a time.
- **Auth differences leaking in.** Keep sign-in strictly in the brand packages. Do not try to unify DPoP and OIDC.
- **Effort.** Roughly one to two weeks of careful work, most of it step 5. It can stop after step 3 or 4 and still have paid for itself.

## Out of scope

- Unifying the two sign-in flows.
- Merging the two Home Assistant integrations into one. They stay separate, with one domain each.
- Publishing the core as a general-purpose library with a stable public API.

## Open questions

- Is one core package worth a PyPI release, or do we stay vendored indefinitely?
- Can Volvo move to `grpclib`, or does the iOS gateway need the current `httpx` path?
- Where should the fixtures live: in the core repo, or in each integration repo?
- Do we keep the upstream Polestar library name (`polestar_api`) or rename it to match the core?
