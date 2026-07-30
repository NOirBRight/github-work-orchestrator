# GWO #109 Round 6 closure ledger

| Raw Round 6 symptom | Owning invariant / narrow API | Focused evidence |
| --- | --- | --- |
| Cutover canary fields could be substituted; rollback bound the wrong activation | Complete Writer-lineage edge table plus identity-bound `WriterTransitionRecord` | `test_rc6_1_writer_lineage_table_rejects_rekeyed_canary_substitution`; `test_rc6_1_writer_lineage_table_accepts_rollback_then_fresh_recutover` |
| Draining either blocked exact recovery or admitted new effects | `_WriterOperation` closed operation gate and recovery-only Planning readback | `test_rc6_2_writer_operation_matrix_is_closed`; prior `test_rp5_3_draining_allows_only_proven_rollforward` |
| A re-addressed compilation receipt or stale claim could survive active readback | `_ActiveAuthorityEnvelope` and complete Campaign claim ledger | `test_rp5_2_tampered_active_receipt_cannot_mutate_claims`; `test_rc6_3_active_envelope_rejects_complete_campaign_claim_ledger` |
| Host hydration mixed control-ref generations or hydrated before successor fencing | `_CampaignObservation` and staged target-only `hydrate_campaign_artifacts` | `test_rc6_4_target_hydration_stages_one_campaign_across_unrelated_ref_change`; prior `test_rp5_4_hydration_retries_one_stable_ref_and_rejects_changed_identity` and `test_rp5_7_installed_github_successor_fences_invalid_lineage` |
| Frozen Ticket contracts accepted foreign/reprojected facts or arbitrary source digests | Shared canonical Ticket-contract projection and digest verifier | `test_rc6_5_ticket_contract_matrix_fails_before_preflight` |
| PlanControl mirrored Runtime assignments and successors reparsed old Ticket overrides | Gateway-only opaque assertion identity; host never serializes/reparses assignments | `test_rp6_6_runtime_assertion_never_enters_plancontrol_state`; `test_rc6_6_successor_does_not_reparse_an_old_ticket_override` |

The matrix is intentionally adversarial and table driven where each closed
state family has adjacent-state or adjacent-field coverage.  All rejection
rows assert that Planning does not begin or that durable claim state is left
unchanged, as appropriate to the boundary being exercised.
