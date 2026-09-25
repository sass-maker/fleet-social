# Fleet Social

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The first user is the Fleet owner, operating social accounts for multiple Fleet products. Fleet product workflows may submit draft material through a constrained API.

## Product Purpose

Fleet Social keeps project-owned social drafts, review decisions, schedules, and per-destination publishing outcomes together. A successful first release accepts a product-attributed draft, requires explicit owner approval of its saved content and destinations, and records the outcome of one real publication accurately.

## Positioning

The product binds each social post to its originating Fleet project and approval before delivery. It runs as a private, single-admin instance in Fleet's Cloudflare account.

## Operating Context

The owner writes or reviews posts in a browser. Fleet product workflows may send draft-only requests. Finished media from Reel Pipeline may become an input later; that experiment is currently paused.

## Capabilities and Constraints

CogSend is the starting codebase. Preserve its SvelteKit on Cloudflare Workers, D1, R2, single-admin authentication, current provider adapters, and existing visual language for the initial release. Additional providers, direct video publishing, external customers, and multi-user roles are outside the approved first scope. Social credentials and Cloudflare secrets remain outside the repository.

## Evidence on Hand

The upstream baseline at commit `01ac9bed052f55d47bac084a4d6d71fd292dc743` passed 994 unit tests, Svelte checks, and a production build locally on 2026-09-25. Fleet's canonical project catalog is `../saas-maker/catalog/projects.json`. No Fleet social account, provider approval, or live post has been verified for this variant.

## Product Principles

- Preserve an operator review step before a product feed can publish.
- Show the state of each destination separately.
- Reconcile an uncertain provider outcome before retrying it.
- Prove one channel before increasing provider breadth.
