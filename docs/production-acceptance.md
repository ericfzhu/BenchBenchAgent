# BBA production acceptance record

Use this document after the implementation passes local tests.
Do not mark an item complete without the named evidence file.

## Release identity

| Field | Value |
| --- | --- |
| BBA version | `0.12.0` |
| Protocol | `bba.epoch.v7` |
| Git commit | Not run |
| Epoch ID | Not run |
| Google Cloud project | Not run |
| Catalog digest | Not run |
| Evaluator root digest | Not run |

## Paid preflight

Status: `Not run`

Required evidence:

- `epochs/EPOCH_ID/preflight/vertex.json`
- One passing record for each public catalog identity
- Serverless `global` route for each request
- Successful BBA function call
- Complete token-use metadata
- Returned model-version metadata, or a recorded provider-field limitation
- `deployment_created: false`

## Public epoch

Status: `Not run`

Required evidence:

- Three complete creator rounds
- One post-design seed for each round
- One immutable instance for each valid design
- One complete public solver matrix
- One prediction-locked structured debrief for each successful solver attempt
- A bounded correctness-annotated debrief report in each repair-round input
- One controlled creator interruption and resume
- One controlled public solver interruption and resume
- Replay success for every successful public attempt
- Final call and token totals below the frozen limits

## Human review

Status: `Not run`

Required evidence:

- One independent solvability certificate for each reviewed candidate
- A certificate type, issuer, independence basis, method, scope, and evidence digests
- Six correct reconstructed answers only when human reconstruction is the selected certificate type
- One separate human adjudicator identity for each reviewed candidate
- Seven structured findings, including certificate adequacy
- An Ed25519 signature that verifies with the trusted public key
- A different reviewer and key for each escalated second review
- No canonical record before public closure

## Sealed audit

Status: `Not run`

Required evidence:

- Hidden material opens only after public closure
- Fresh instances use the committed hidden seeds
- Hidden attempts use the committed sealed scaffold identities
- Hidden debriefs remain outside creator feedback
- One controlled hidden solver interruption and resume
- All five matched damage classes are detected
- The public-optimizer control exposes the intended selection gap
- Both target vectors come from stored evidence
- The complete audit metric vector and threshold verdict exist
- Replay succeeds for every successful hidden attempt
- The holdout registry state is `retired`

## Final decision

Status: `Not production-verified`

Change this status only after all sections have evidence digests.
Fixture tests do not satisfy this record.
