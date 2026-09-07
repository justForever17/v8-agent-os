# Research Runtime Ownership

Source baseline: `7f8944988c42dd69ed7efbd2d8dadc7517a71701`.
This describes the uncommitted agent-owned redesign, not a released capability.

## Owners

| Responsibility | Owner | Invariant |
| --- | --- | --- |
| User intent, runtime selection, final delivery | Supervisor | No second conversation Planner |
| Research actions and knowledge adequacy | `agent.py` | One bounded conversation; local correction before new acquisition |
| Acquired immutable bodies and read receipts | `evidence.py` | URL, body hash, original retrieval time; no invented observations |
| Network, authentication, cancellation | Existing source router and browser provider | Existing domain, private-network and profile permissions remain in force |
| Independent answer review | Configured Verification Engineer | Concrete defects, not source/word/date-age quotas |
| Receipt validation and partial/complete truth | `core/tools/research_quality.py` | Provenance is deterministic; semantic correctness is an Agent judgment |
| Answer lifecycle | `core/tools/research_ledger.py` | One durable record per explicit revision; user archive/delete wins |
| Episode, handoff and human projection | Existing Engine surfaces | Complete, useful partial and failed remain distinct |

`claimTable` is retained in the external envelope. New rows represent actually
observed source ranges, not code-approved semantic propositions. Exact read
hashes prove provenance, not entailment or completeness. An independent review
is also fallible; live evaluation needs a source-based oracle, not its verdict.

## Action Protocol

The writer can read saved bodies, search specific missing knowledge and submit
an answer. Long answers may use `save_research_answer_section` followed by ordered
`sectionIds` on submission. The Agent chooses the sections; there is no code-owned
outline or segmented-writer pipeline. A section ID is an upsert key, preventing
duplicate append on retry. Draft storage is limited to 16 sections / 80,000 chars
for memory bounds, not answer adequacy. Only the assembled, reviewed answer can
be delivered or promoted. Submission cites source keys; Runtime binds observed passages.
The reviewer receives the question, answer, observed passages and full-source
read access. It cannot search, write, delegate or change models. Corrections
return to the same writer conversation. No deterministic prose fallback exists
in the production path.

Direct submissions are also saved privately in that same section store. Citation
repair and review correction return reusable `sectionIds`; they do not require
generating the full answer again. A reviewer quote that cannot be located is an
unverified finding for the writer to check, not verified counterevidence and not
a reason to loop on formatting. Resubmission still requires independent review.
Explicit citation typography, including grouped and labeled Chinese source
markers, is normalized before hashing and review. Unknown or unread source IDs
still fail binding; formatting never manufactures evidence or changes statements.

The loop is bounded by time, writer steps, searches, revisions and review steps.
The total default is 480 seconds, including acquisition, writing and review;
360 seconds cut off healthy review after a long automatic-budget answer in live.
This is a maximum, not a minimum delay or model capability. The last existing
writer step is reserved for explicit submission, never automatic draft acceptance.
In agent mode `architectAgentTimeoutSeconds` bounds provider transport inactivity;
the remaining Research deadline bounds the entire call. Healthy streaming output
must not be discarded merely because total generation exceeds the idle timeout.
SDK automatic retries are disabled here; callbacks retain runtime identity across
the request thread. Stream timings distinguish first chunk, gaps and total time.
Original tool JSON must be complete before execution; LangChain's partial JSON
repair is not completion proof. A reported output limit and premature EOF remain
distinct diagnostics. Each writer/reviewer can recover an incomplete output once,
within the existing deadline; no incomplete action is executed or published.
The actual request token budget is included in the Agent context and call trace.
Each call must choose a bound action. A provider refusing the action protocol,
an exhausted budget or missing proof cannot produce a successful handoff.
This requirement does not change Supervisor's ability to answer in prose.

## Answer Lifecycle

- Reviewed complete and explicitly limited partial answers auto-enter the ledger.
- A reviewed partial answer remains partial in the episode and coverage fields,
  but does not automatically force a whole-question retry. The Supervisor decides
  how to address the remaining scope. Only proof-valid partials pass this boundary.
- Before an ordered verification delegation, the Supervisor may read the exact
  terminal handoff references. Reading is not completion of the delegation, and
  does not permit new research, unrelated reads or writes under that exception.
- `search_experience` and `get_experience` expose saved answers and original dates.
- Exact complete-question reuse retains its original review. Related questions
  enter the same Research Agent with the saved answer and original source bank;
  they cannot inherit the old question's acceptance receipt.
- `run` with `experiencePackId` rechecks/revises a saved answer. The update compares
  its original bundle ID; concurrent updates, archival and deletion block library
  replacement. The newly observed run remains available for diagnosis.
- `forceRefresh=true` asks for fresh acquisition; otherwise existing sources can
  be sufficient. Dates are advisory context, not a fixed expiry rejection.
- Active/archived answers retain their source bundle and preceding revision.
  Deletion leaves an ID-only tombstone to prevent automatic resurrection by
  replay. It does not erase historical conversation evidence.

## Retired Pipeline

The private `_invoke_web_research_architect_staged`,
`_invoke_web_research_architect_agent`, and
`_merge_web_research_architect_agent_pack` had no external production callers.
Their fixed-plan/segmented-writer/merge orchestration is retired in this change.
Historical persisted review v6 remains readable and subject to its original
contract; it is not another execution fallback. New receipts use binding v7.

Their implementation-specific tests are migrated as follows:

| Retired assertions | Replacement |
| --- | --- |
| Canonical facet/claim planning, outline/section repair, fixed minima | `test_research_agent_acceptance.py`: short sufficient evidence, count advisory, partial scope, mutation counterexamples |
| Parallel writer sections and repeated semantic/adversarial passes | `test_research_agent.py`: bounded actions, one reviewer, local correction, cancellation, model budget |
| String/claim merge and source receipt projection | `test_research_agent.py`: immutable snapshots, saved source hash, broker/persistence/surface/episode parity |
| Model prose/deterministic fallback and model shopping | `test_research_agent.py`: no-tool exhaustion and unreviewed draft rejection |
| Experience reuse and refresh | `test_research_agent.py`: exact reuse, semantic recheck of related questions, revision conflicts, archive/delete replay |

Remaining private pipeline helpers in `research_broker.py` are retirement
candidates, not alternative owners. Do not add callers; retire separately with
call-graph, dynamic-registry and behavior checks. This is tracked debt for the
next iteration, not a claim that the large module is fully decomposed.

## Verification Status

Unit/contract tests do not use real credentials. Live harnesses require `--live`.
Direct real-provider passes do not establish Supervisor/Web/Phone parity,
browser authentication usability, installer behavior or physical-machine
acceptance. Current source-tree changes must pass those relevant layers before
release; this document is not a release approval.

Current 2026-09-07 findings for the 2026.09.06.1 preview candidate:

- Short direct M3 writer/reviewer live and the synthetic answer lifecycle passed:
  creation, exact reuse, revision without reacquisition, archive/restore/delete.
- The selected M3 now uses automatic output budgeting. Real writer calls produced
  8,983 and 10,326 output tokens without the old 4096-token truncation. A managed
  run delivered a reviewed complete Research answer of 15,261 characters; later
  runs delivered explicitly limited partial answers. These are Research results,
  not full-chain acceptance.
- The managed research/verification case has NOT met all requested source coverage.
  Live failures exposed URI read sets misclassified as native files, unnecessary
  full-draft regeneration, mandatory retries of accepted partials, reviewer quote
  formatting loops, and an ordered-route gate rejecting evidence preparation.
  Focused regression tests cover those fixes. A fresh joint live finished in 447s:
  Research produced a reviewed partial answer, the verification worker and parent
  retained original evidence bindings, and Web live/reload cards and events agreed.
  Only one first-party official source was retained, below this case's explicit
  five-source requirement. This is not a complete-research PASS. The user authorized
  publishing the fixes first and continuing acquisition diagnosis after the tag.
- A narrow real-provider replay of existing evidence preserved four original
  bindings in both verifier and Supervisor delivery, corrected source-carrier and
  document-status confusion, and did not start another Research episode. This is
  reuse/review evidence, not another fresh-acquisition success.
- Verification receives a bounded original ID/citation/URL index. The existing
  two-correction branch loop detects exact binding mismatches in labelled proof
  rows; it neither grades prose quality nor promotes an unparsed result to proof.
  Final-answer testing checks requested coverage and traceable sources rather
  than verbatim copying. Semantic correctness still requires source-based review.
- Delegated streaming separates meaningful-output inactivity from the configured
  total model-call budget; healthy output is not stopped at an independent fixed
  180-second wall limit. A provider stream closes in its producer Context, not
  the caller's thread. This does not remove total time, token or tool budgets.
- An earlier partial answer claimed to have supplied a checklist not actually in
  its body. Independent model acceptance was not treated as an acceptance oracle;
  writer/reviewer instructions were corrected. This remains a live regression case.
- Web production UI verification confirmed activity-card counts equal the detail
  entry counts after refresh. Admin now has one arbitrary-site authorization entry;
  UI transport fixtures verified save/read-back/open order and denied stale config.
  This is not evidence of authenticated access to every real website.
- Cross-session library reuse and generalized browser-login live acceptance are
  not demonstrated. Existing per-session scope and fetch permissions remain.
- Factory callback `requestedMaxTokens` may reflect construction kwargs rather
  than the smaller per-call override. Research's `providerTiming` records the
  actual request value; do not infer the wire budget from the factory field alone.
- Output budgets now resolve through `core/model_token_policy.py`. The new
  writer/reviewer loop no longer applies independent 7500/2400-token caps.
  Fixed user budgets remain binding; confirmed legacy estimates become auto.
  Unknown/reviewed historical values are not reclassified merely because they
  equal 4096. Passing transport or configuration tests is not end-to-end proof.

Reproduce the Admin interaction checks from `apps/v8-agent-os-admin` with
`node scripts/verify-research-agent-browser.mjs --live`. Set
`V8_ADMIN_TEST_LOGIN` / `V8_ADMIN_TEST_PASSWORD` via the environment and optionally
`V8_ADMIN_TEST_URL`. It signs in to Preview, mocks only configuration/open-browser
side effects, checks stale read-back denial and sequence, and writes screenshots
to a temporary directory. It never changes the real profile authorization.
