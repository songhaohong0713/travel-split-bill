# Settlement consistency review follow-up

Approved direction: retain confirmed payments and carry differences; reject revocation while active dependent payments exist. New payments store compact frozen balances, with legacy snapshots readable. Actual debit can use another currency, but must supply payment-to-trip settlement rate. Stable IDs determine names and avatar colors.

## Execution
- [x] Regression tests for dependency revocation, compact basis and settlement edits.
- [x] Compact new settlement basis and avoid duplicate summary projection.
- [x] Stable expense draft idempotency and foreign actual-payment conversion.
- [x] Trip member nickname storage, self-only update and refreshed presentation.
- [x] Identity-based avatars and clearer carry/revocation display.
- [x] Historical confirmation authorization, cleanup fairness and invitation error handling.
- [x] Full automated regression; native compilation; document unverified device and production boundaries.

No production financial edits or deployment. Legacy payment records are not rewritten. User subsequently approved fixed creation UTC offsets (legacy owners set once) and preview-first 30-day orphan retention. Implemented with two incremental SQL migrations; neither has been applied to production. See docs/reviews/2026-11-01-settlement-fixes.md for evidence and manual acceptance.
