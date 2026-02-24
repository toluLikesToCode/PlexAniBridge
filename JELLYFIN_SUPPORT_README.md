# Jellyfin Support Implementation Checklist

This document tracks progress toward **full Jellyfin support parity** while keeping Plex backward compatibility.

## Current Status

- [x] Tooling installed and tests runnable locally (`uv`, `pytest`, `pnpm`)
- [x] Backend test suite passing
- [x] Frontend type checks passing
- [ ] Full Jellyfin sync parity complete

## Completed Work

### 1) Configuration and Profile Model

- [x] Added `media_server_provider` (`plex | jellyfin`) to profile config
- [x] Added Jellyfin profile fields:
  - [x] `jellyfin_token`
  - [x] `jellyfin_user`
  - [x] `jellyfin_url`
  - [x] `jellyfin_sections`
  - [x] `jellyfin_genres`
- [x] Added provider-specific credential validation
- [x] Updated implicit default-profile bootstrap logic for provider-aware credentials
- [x] Updated `.env.example` and `data/config.example.yaml` with Jellyfin fields

### 2) Provider Plumbing (Core)

- [x] Added `MediaServerProvider` enum
- [x] Added `MediaServerNotImplementedError` for non-implemented runtime paths
- [x] Added Jellyfin client scaffold (`src/core/jellyfin.py`)
- [x] Updated `BridgeClient` to instantiate provider-specific client
- [x] Updated initialization logs/status context for provider-aware user identity
- [x] Added provider fields to scheduler status payload (`media_server_provider`, `media_server_user`)
- [x] Added provider-aware scheduler account lookup helper (`get_profiles_for_server_account`)

### 3) Webhook Layer

- [x] Added Jellyfin webhook schema (`src/models/schemas/jellyfin.py`)
- [x] Added `/webhook/jellyfin` route
- [x] Included Jellyfin router in webhook aggregator
- [x] Updated Plex webhook to use provider-aware scheduler lookup

### 4) History and API Compatibility

- [x] Added provider-neutral `sync_history` model fields:
  - [x] `server_provider`
  - [x] `server_guid`
  - [x] `server_rating_key`
  - [x] `server_child_rating_key`
  - [x] `server_type`
- [x] Added Alembic migration for new `server_*` columns
- [x] Kept legacy `plex_*` fields intact for backward compatibility
- [x] Updated history DTO/service to emit both:
  - [x] legacy Plex fields
  - [x] provider-neutral fields (`provider`, `server_*`, `server`)
- [x] Added sync API alias `server_rating_keys` (legacy `rating_keys` still supported)

### 5) Frontend Compatibility Layer

- [x] Extended API types with provider-neutral history/status fields
- [x] Updated timeline page to prefer provider-neutral title/cover metadata
- [x] Updated retry flow to use `server_rating_key` fallback
- [x] Updated dashboard/about display to show provider-aware user identity

### 6) Tests and Validation

- [x] Added config tests for Jellyfin profile validation/load behavior
- [x] Backend tests passing (`python3 -m scripts.dev test` path)
- [x] Frontend type checks passing (`pnpm -C frontend run check`)
- [ ] Frontend lint fully clean (prettier formatting pending in a few files)

## Remaining Steps (Full Jellyfin Parity)

### A) Implement Full Jellyfin Sync Engine (Critical)

- [ ] Replace Jellyfin bridge guard (`MediaServerNotImplementedError`) with real sync execution
- [ ] Add provider-neutral media abstraction used by sync clients (or dedicated Jellyfin sync clients)
- [ ] Implement Jellyfin section/item traversal with watched/poll/full-scan behavior parity
- [ ] Implement Jellyfin equivalents for:
  - [ ] continue watching logic
  - [ ] watchlist/favorites logic
  - [ ] ratings mapping
  - [ ] history timestamps (`started_at`, `completed_at`)
  - [ ] notes/reviews extraction
- [ ] Ensure AniMap identifier extraction parity (imdb/tmdb/tvdb mapping paths)

### B) Batch/Performance/Resilience

- [ ] Implement Jellyfin metadata and item prefetch strategy comparable to Plex batch path
- [ ] Add robust retry/backoff behavior around Jellyfin API calls
- [ ] Add caching invalidation behavior parity with existing Plex paths

### C) History Write-Path Completeness

- [ ] Ensure all sync history creation paths write `server_*` for Jellyfin entries
- [ ] Preserve Plex mirror writes (`plex_*`) only for Plex provider
- [ ] Add/update DB indexes if query plans regress on mixed-provider datasets

### D) Webhook Completeness

- [ ] Expand Jellyfin webhook parsing to support all relevant event payload variants
- [ ] Align webhook event filtering semantics with sync expectations (added/rated/scrobbled equivalents)
- [ ] Add webhook integration tests for profile matching and targeted sync triggering

### E) Frontend UX Parity

- [ ] Add Jellyfin deep links in timeline item actions
- [ ] Add provider badge/chip in timeline rows/cards
- [ ] Ensure all timeline/diff/pins/retry interactions work for Jellyfin-only entries
- [ ] Resolve current frontend lint formatting warnings

### F) Docs and OpenAPI

- [ ] Regenerate OpenAPI schema and docs to include new provider-neutral fields/endpoints
- [ ] Add Jellyfin setup guide (token, webhook setup, profile examples)
- [ ] Update quick-start/compose examples with Jellyfin profile templates

### G) Test Coverage Expansion

- [ ] Add unit tests for Jellyfin client methods and data normalization
- [ ] Add sync behavior tests for Jellyfin movie/show flows
- [ ] Add history service tests for Jellyfin metadata enrichment and fallbacks
- [ ] Add scheduler/provider lookup tests for mixed Plex + Jellyfin profile sets
- [ ] Add API contract tests for backward-compatible responses with new fields

## Definition of Done (Full Jellyfin Support)

- [ ] Jellyfin profiles can run periodic + poll + webhook sync successfully
- [ ] All sync fields (`status`, `score`, `progress`, `repeat`, `notes`, `started_at`, `completed_at`) map correctly
- [ ] Timeline/history/retry workflows work for Jellyfin entries
- [ ] Existing Plex profiles continue to work unchanged
- [ ] Backend tests, frontend checks, and lint all pass
- [ ] Documentation and OpenAPI are up to date
