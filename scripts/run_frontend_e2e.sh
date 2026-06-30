#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/frontend"
npm ci
npx playwright install --with-deps chromium

# Headless functional + visual suite (mock data; no backend required).
# VITE_MOCK_TEAM_PLANE=true is set in playwright.config.ts for the preview build.
npm run test:e2e

# After intentional UI changes, refresh committed visual baselines locally:
#   cd frontend && npm run test:e2e:update
# or:
#   cd frontend && npx playwright test --update-snapshots
# Commit the updated files under frontend/e2e/**/*-snapshots/ — first-run diffs
# without --update-snapshots are expected failures, not product regressions.
