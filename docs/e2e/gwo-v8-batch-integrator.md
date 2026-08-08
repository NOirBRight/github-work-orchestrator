# GWO V8 BatchIntegrator Beta2 Evidence

## Verification Boundary

- Schema: `gwo-v8-batch-beta2-evidence.v1`.
- Mode: `Local Verification Only`.

## Publication Subject

```json
{
  "parents": [
    "514f1162fe563f27edd35b4d6683df2786b7dcc0"
  ],
  "sha": "bcc7e719ecd5176f29d496e7ec6d7c3819c96439",
  "tree": "81dca3a6296aa02182141975ae3d402ebd16c7ff"
}
```

## Merged Results

| Issue | Merged commit |
| --- | --- |
| #115 | `9bc902097487ec454d529a7c46755e1f7ec1c962` |
| #116 | `1d16ac44bc824144ed18a94defddfce3eb2a7fc4` |
| #117 | `c802171cb0262c32906c49e86403ec3567804a02` |

## Focused pytest Receipts

| Suite | Command | Command digest | Log digest | Manifest digest | Tests | Failures | Errors | Skipped |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| BatchIntegrator | `py -3.13 -m pytest tests/test_v8_batch_integrator.py -q` | `62ea428936895106685235430893a4afc88a14ec43c923fab4386b60bc82078d` | `6ebe437b685c2ef0e5789e283c099e2fac6ae994ba2116824fad3bdb20579136` | `844592e931e55bb672fbdd07edfb21450130725d6f17b9d963b3c0947392ff1e` | 47 | 0 | 0 | 0 |
| Batch recovery | `py -3.13 -m pytest tests/test_v8_batch_recovery.py -q` | `1ecb90d072f65f7177a38d10a8b17d3d9beb6bf7f879241af3969e60d00a69e1` | `8458e8a4ea2bff24fcb7f779f25c7730c4304d221531743d7f8ea5f951f37563` | `844592e931e55bb672fbdd07edfb21450130725d6f17b9d963b3c0947392ff1e` | 23 | 0 | 0 | 0 |
| Beta2 boundary | `py -3.13 -m pytest tests/test_v8_batch_beta2.py -q` | `25a3dc3161d79784912b80f18f034167722ed42d921f668afb67a7779e64b98a` | `bb44becf96252a393eea1874b5078150423a5b2f1e65cf9e24266ea72585a7f8` | `844592e931e55bb672fbdd07edfb21450130725d6f17b9d963b3c0947392ff1e` | 21 | 0 | 0 | 0 |
- `py -3.13 scripts/quick_validate.py`: exit 0.
- `py -3.13 scripts/sync_orchestrator.py`: exit 0.
- `py -3.13 scripts/sync_orchestrator.py --check`: exit 0.
- `git diff --check`: exit 0.

## Exact Git, CI, Target, Recovery, and Receipt Readbacks

```json
{
  "infrastructure_retry": {
    "batch_sha": "9f3448557d947d674aaed92cbc1c431a79c3e282",
    "retry_count": 2,
    "retry_shas": [
      "9f3448557d947d674aaed92cbc1c431a79c3e282",
      "9f3448557d947d674aaed92cbc1c431a79c3e282"
    ]
  },
  "negative_paths": {
    "ambiguous_attribution": "DeliveryAttributionAmbiguous",
    "rebase": "DeliveryIdentityMismatch",
    "squash": "DeliveryIdentityMismatch",
    "wrong_merge_target": "DeliveryIdentityMismatch",
    "wrong_receipt": "DeliveryIdentityMismatch",
    "wrong_sha": "DeliveryIdentityMismatch"
  },
  "restart_adoption": {
    "batch_sha": "9f3448557d947d674aaed92cbc1c431a79c3e282",
    "provider_rereads": 0,
    "receipt_digest": "098584e17f5c9e359b121940bc78244d5836f791e1b502df97211a141d97e194"
  },
  "schema": "gwo-v8-batch-beta2-evidence.v1",
  "singleton_fallback": {
    "resume_directives": [
      [
        "work-run:1",
        "7777777777777777777777777777777777777777777777777777777777777777"
      ]
    ],
    "singleton_candidate_shas": [
      "000000000000000000000000000000000000000b",
      "000000000000000000000000000000000000000c",
      "000000000000000000000000000000000000000d"
    ],
    "unaffected_evidence": {
      "issue:2": [
        "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
      ],
      "issue:3": [
        "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
      ]
    }
  },
  "standard_batch": {
    "batch_sha": "9f3448557d947d674aaed92cbc1c431a79c3e282",
    "candidate_shas": [
      "000000000000000000000000000000000000000b",
      "000000000000000000000000000000000000000c",
      "000000000000000000000000000000000000000d"
    ],
    "delivery_proofs": [
      {
        "batch_id": "eeec830fe1be200c2289f26433d02c7ee9eb11ac869710def0b57ff3a46f3157",
        "batch_sha": "9f3448557d947d674aaed92cbc1c431a79c3e282",
        "delivery_request_digest": "f4e863edd08a4f553f4adfe845e228f5cb4c31d9e391e16ae271f0138b947a70",
        "delivery_stable_action_id": "delivery-action:1",
        "hosted_result_receipt_digest": "098584e17f5c9e359b121940bc78244d5836f791e1b502df97211a141d97e194",
        "integration_lease_digest": "d3c66ba2f01deb62361f14d78052bf4d53a2841d99de9d17496446f6d97a8803",
        "local_check_receipt_digest": "7bc82a41d0dda8bfe15b54370056b17c2041f03f87acb286f88f2904ee952b13",
        "member_ticket_keys": [
          "issue:1",
          "issue:2",
          "issue:3"
        ],
        "merge_method": "merge",
        "proof_digest": "83b4c13ff79794b0a622033be1bbb70fd5543856d89c640bc61a8dead03a7443",
        "publication_receipt_digest": "1ae05cc1e926ecbef3f05c485ef27a25c442a0007bc0d8331b64851b8f3ed954",
        "pull_request_head_sha": "9f3448557d947d674aaed92cbc1c431a79c3e282",
        "pull_request_merge_target_sha": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "pull_request_number": 1,
        "target_branch": "main",
        "target_contains_batch_sha": true,
        "target_head_sha": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "target_readback_digest": "f2344378afe5ae691dedb18326c68dc285f19183c6d6d1475a5c76d5e836ec57"
      }
    ],
    "hosted_sha": "9f3448557d947d674aaed92cbc1c431a79c3e282",
    "local_sha": "9f3448557d947d674aaed92cbc1c431a79c3e282",
    "member_ticket_keys": [
      "issue:1",
      "issue:2",
      "issue:3"
    ],
    "pr_head_sha": "9f3448557d947d674aaed92cbc1c431a79c3e282",
    "publication_sha": "9f3448557d947d674aaed92cbc1c431a79c3e282",
    "target_readback_batch_sha": "9f3448557d947d674aaed92cbc1c431a79c3e282"
  },
  "strict_batch": {
    "batch_sha": "000000000000000000000000000000000000000e",
    "candidate_shas": [
      "000000000000000000000000000000000000000e"
    ],
    "delivery_proofs": [
      {
        "batch_id": "adf0cef35969f5ac2ef57a3821f04c435df75b1f159a2d751b074642ea0bc3fb",
        "batch_sha": "000000000000000000000000000000000000000e",
        "delivery_request_digest": "1db0a03773a200af578665e4f6b81d5e4845620d25ddd2578d81ab7529c162c3",
        "delivery_stable_action_id": "delivery-action:strict",
        "hosted_result_receipt_digest": "be66426d08835229a48902031e63b7317568e4058836619c1a425a2d7305f964",
        "integration_lease_digest": "0344ea107430af42caad4eb8f44ce5f365bb6489242342a4b0f569b7ada02f09",
        "local_check_receipt_digest": "fadf5ea039cdebdc1ad415d39019b3aa486104ddfe0a8f9162352a9e8d79e220",
        "member_ticket_keys": [
          "issue:4"
        ],
        "merge_method": "merge",
        "proof_digest": "773a5f9d65326a4239a62d53a5e5656bb06b638ce8386e3ef2cd7bf0a01d84b3",
        "publication_receipt_digest": "263d0fb26641b41142e34969e874be51770ac6f61ff701c7ad3759fe059342bb",
        "pull_request_head_sha": "000000000000000000000000000000000000000e",
        "pull_request_merge_target_sha": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "pull_request_number": 1,
        "target_branch": "main",
        "target_contains_batch_sha": true,
        "target_head_sha": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "target_readback_digest": "034413f9d8ea8bf81e49f0114070f4c418e7e15b12c3871dbe935aa8fab0563d"
      }
    ],
    "hosted_sha": "000000000000000000000000000000000000000e",
    "local_sha": "000000000000000000000000000000000000000e",
    "member_ticket_keys": [
      "issue:4"
    ],
    "pr_head_sha": "000000000000000000000000000000000000000e",
    "publication_sha": "000000000000000000000000000000000000000e",
    "target_readback_batch_sha": "000000000000000000000000000000000000000e"
  },
  "successful_fallback": {
    "delivery_proofs": [
      {
        "batch_id": "6748a06074565b948c53cd95eee5ec91994a705e835d8eed9fb77d9e49534e6b",
        "batch_sha": "000000000000000000000000000000000000000b",
        "delivery_request_digest": "97d8bd2fb9e2d0502e0fd5c3eab037b5c0c490062b4bad45fa8a7a329e0c46d6",
        "delivery_stable_action_id": "4ff868cb4b2cd55f3e1225d687f6c9307a3ccfc22210dd55962f5f2a261d8c93",
        "hosted_result_receipt_digest": "f0e57174e663c7738d1f4db1d7cd08a4f106057611c0284ce04b7f5460052303",
        "integration_lease_digest": "565e322b99cdaa6b62180e4596f3d3e574e4f86b9407f7b1601a282e3e44adf0",
        "local_check_receipt_digest": "85d9947bc18695f2e6daa3823b2ecfc7ccbe75b54b0ce5e4b0c7e21d9ee27bc7",
        "member_ticket_keys": [
          "issue:1"
        ],
        "merge_method": "merge",
        "proof_digest": "26320ed22dbae5cf2d0f0a4174ffe8e114ae9c26a30c822d411cca0f683ef97b",
        "publication_receipt_digest": "23d5e4a42cad439675cdd74e593859deb3f02e6677f292c0ed0f5ed3fb39bff9",
        "pull_request_head_sha": "000000000000000000000000000000000000000b",
        "pull_request_merge_target_sha": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "pull_request_number": 1,
        "target_branch": "main",
        "target_contains_batch_sha": true,
        "target_head_sha": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "target_readback_digest": "3f5ae9ba51c45de496140f28a49650852e59eebd862e74af0dcd7ad7afd16b92"
      },
      {
        "batch_id": "15518a4c6f0dd03517b490b9efc8f6452fdb27603565acd604390ec82b2741f9",
        "batch_sha": "000000000000000000000000000000000000000c",
        "delivery_request_digest": "95709ad16f304f6a8571f275aabb60602ffb118f9ecf7c894769aa0a56412bc6",
        "delivery_stable_action_id": "b47339a549570ec378bec8606d301ed6c838d8053e2d30665cdf5504e283b0a1",
        "hosted_result_receipt_digest": "d7806a1f49c57a572c80abd51dc63cce7fe52d9b2959a34f4e6bcdf05e6eff6a",
        "integration_lease_digest": "4b69a29c20dc0a5b88c2d36ba9839dfe3386072211c5920d365997d1a07b668a",
        "local_check_receipt_digest": "a3012c26781a7a5ce17ae0c94c641ce4d4142996af1be48f5cc002b3bff48623",
        "member_ticket_keys": [
          "issue:2"
        ],
        "merge_method": "merge",
        "proof_digest": "a4e865e462e1110b99ce697191df97d9f895d68955d96b799e534b3d57279e85",
        "publication_receipt_digest": "2b2ada874ae4b515ca68631094d055b944af8724f89395dab9e6c56ed8541766",
        "pull_request_head_sha": "000000000000000000000000000000000000000c",
        "pull_request_merge_target_sha": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "pull_request_number": 1,
        "target_branch": "main",
        "target_contains_batch_sha": true,
        "target_head_sha": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "target_readback_digest": "9714048da784ec4afbd5825cc5f80c12e38507d1d6e5365e2e435a7d9e09e0a1"
      },
      {
        "batch_id": "d326c817796404da8709607b1cfa14cca9192394495da80a1dc069a534b8c38a",
        "batch_sha": "000000000000000000000000000000000000000d",
        "delivery_request_digest": "648eac66c0c22eed606dabe7597be9ec602eb497dcadeb30a859b99f486f149d",
        "delivery_stable_action_id": "84e414b865dad37130dd9eebceda274e40621004e6babb525cc629c56aa71110",
        "hosted_result_receipt_digest": "6a56f64c987bb40f8ce52a8363b6dce128ef93000c746c8e0a2b03d1608d91d2",
        "integration_lease_digest": "fe74c80c844bb41d9377dc81920cb27a64d574ea62b3c41c6c52e9486b140051",
        "local_check_receipt_digest": "6b021b1728e3a71051bedac662bc4965109d154eec1e0b2ececef0192962e0b4",
        "member_ticket_keys": [
          "issue:3"
        ],
        "merge_method": "merge",
        "proof_digest": "256fe820ec7aee245d075e0d72c4b834303a286f4d35449b3fc6f568d89ba213",
        "publication_receipt_digest": "0de41b3fb423a5da93c9ebb749e2eee87afdb78ebf4eeb2d2a39081b48877210",
        "pull_request_head_sha": "000000000000000000000000000000000000000d",
        "pull_request_merge_target_sha": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "pull_request_number": 1,
        "target_branch": "main",
        "target_contains_batch_sha": true,
        "target_head_sha": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "target_readback_digest": "51565aa96d431b25c9e111848760de5485eba3364d717e32f1df7a3dc6d552e9"
      }
    ],
    "member_ticket_keys": [
      "issue:1",
      "issue:2",
      "issue:3"
    ],
    "parent_fallback_generation": 1,
    "parent_phase": "complete",
    "parent_receipt_digest": "5c9022c8f3350cedd29c3ae3684322dde8cd604ab853e076835472f894d13b00"
  }
}
```

## Release Train Decision

Beta2 feature-complete preview; no V3 writer cutover and no GA admission.
