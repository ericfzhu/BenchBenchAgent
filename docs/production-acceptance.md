# BBA production acceptance record

Use this document only after the implementation passes local tests. Do not mark an item complete without the named evidence.

## Release identity

| Field | Value |
| --- | --- |
| BBA version | `0.13.0` |
| Protocol | `bba.epoch.v8` |
| Git commit | Not run |
| Epoch ID | Not run |
| Google Cloud project | Not run |
| Catalog digest | Not run |
| Evaluator root digest | Not run |
| Frozen sandbox backend | Not run |
| Frozen hard USD ceiling | Not run |

## Target-host readiness

Status: `Not run`

Required evidence:

- Complete local unit suite on the target Ubuntu host
- Bubblewrap security checks executed without skips
- `bba sandbox-status` reports `linux-bubblewrap` and `available: true`
- Application Default Credentials resolve the intended project
- Development-portal readiness shows sandbox, ADC/project, price coverage, and dependency policy ready
- Frozen price catalog covers every public route used by the epoch

## Paid preflight

Status: `Not run`

Required evidence:

- `epochs/EPOCH_ID/preflight/vertex.json`
- One passing record for each public catalog identity
- Serverless `global` route for each request
- Successful BBA tool/function contract
- Complete token-use metadata
- Returned model-version metadata, or a recorded provider-field limitation
- `deployment_created: false`
- Retry-inclusive conservative cost estimate below the frozen hard USD ceiling
- No failed catalog identity silently omitted

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
- Creator retry attempts use distinct inference reservations
- One controlled public solver interruption and resume
- Replay success for every successful public attempt
- Final calls, input tokens, output tokens, and conservative USD usage below frozen limits
- No immutable attempt or completed evidence overwritten during resume

## Human review

Status: `Not run`

Required evidence:

- One independent solvability certificate for each reviewed candidate
- Certificate type, issuer, independence basis, method, scope, and evidence digests
- Six correct reconstructed answers only when human reconstruction is selected
- A separate human adjudicator identity for each approved candidate
- Seven structured findings, including certificate adequacy
- An Ed25519 signature that verifies with the trusted public key
- A different reviewer and key for an escalated second review
- No canonical record before public closure
- The public audit population is frozen only after intended review work is complete
- A late certificate and a late review attempt after audit freeze are rejected without creating review-adjacent registry mutations

## Public freeze and closure

Status: `Not run`

Required evidence:

- `epoch freeze-audit` succeeds only with the available sandbox backend frozen in the manifest
- Public audit population contains the expected base, damage, and public-optimizer profiles
- Review input is read-only after audit freeze
- Public evaluation contains matrix, statuses, creator ranks, solver ranks, adaptation, and manifest digest
- Hidden evidence is absent from the public record
- Approved canonical promotions are appended only after public closure
- Controlled interruption after public evaluation publication but before registry append is repaired by rerunning public close

## Sealed audit

Status: `Not run`

Required evidence:

- Sealed audit uses the same available sandbox backend frozen in the manifest
- Hidden material opens only after public closure
- Fresh instances use committed hidden seeds
- Hidden attempts use committed sealed-scaffold identities
- Hidden debriefs remain outside creator feedback
- One controlled hidden solver interruption and resume
- All five matched damage classes are detected
- The public-optimizer control exposes the intended selection gap
- Both target vectors come from stored evidence
- Complete audit metric vector and threshold verdict exist
- Replay succeeds for every successful hidden attempt
- Holdout registry state is `retired`

## Final decision

Status: `Not production-verified`

Change this status only after every section has evidence digests and an independent operator has reviewed the acceptance record. Fixture tests and static inspection do not satisfy this record.
