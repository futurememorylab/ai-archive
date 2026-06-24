# 0001. Python-only stack, no Node frontend

- **Date:** 2026-05-18
- **Status:** Accepted
- **Lifespan:** Invariant

## Context

The PoC (Archive-AI) used a Node/React/TS stack. Maintaining two
package.json files, two test runners, and TS↔Python type drift consumed
significant time.

## Alternatives

React+TS SPA via Vite, Svelte SPA.

## Decision

Server-rendered Jinja2 + HTMX + Alpine.js + Tailwind standalone CLI.
The UI is forms + one video screen; React is overkill.

## Consequences

One language top to bottom, no npm/Node, no build step beyond Tailwind
CLI, smaller cognitive surface for future single-maintainer work.
