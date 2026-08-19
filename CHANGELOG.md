# Changelog

## 8.0.0

- Accepted root Canary receipt `ea642b5606efc10adaf3671174b10e3df2f1a5f2dfc8b60a86b251db5845c938`.
- Evidence base `7b6a1616a32b03d8bcf2b36417741534877fee9e` and Canary target `d31d5787df8ff53f081ed45df42389ef2e505ffb` were read back.
- Repository release verification is Local Verification Only (`local-only-v1`); the pre-tag receipt binds the exact subject SHA/tree and successful full pytest readback.
- Product Hosted-CI delivery remains separate and is not satisfied by repository release verification.
- Local Root Canary evidence is explicitly bridged to the external Production Canary package, Production Activation, and the exact default-writer readback; their Campaign/activation identities are not treated as interchangeable.
