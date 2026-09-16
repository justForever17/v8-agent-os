# Research runtime

Research gathers sources, records what was actually read, prepares an answer, and requests independent review. Supervisor owns task selection and final delivery.

| Responsibility | Implementation |
| --- | --- |
| Research actions and answer revision | `agent.py` |
| Immutable source bodies and read receipts | `evidence.py` |
| Source access, authentication and cancellation | Existing source router and browser provider |
| Receipt validation and partial/complete status | `core/tools/research_quality.py` |
| Saved answers, revision conflicts and archive/delete | `core/tools/research_ledger.py` |
| Episode, handoff and client projection | Engine runtime and shared session contracts |

The writer can search, read saved sources, and submit an answer. Long answers may be assembled from saved sections. Submission must cite observed sources; hashes prove provenance, not semantic correctness or completeness. The configured Verification Engineer reviews the question, answer, and sources. Corrections return to the writer.

Time, action, review, and model budgets remain binding. Incomplete tool JSON is not executable. An unreviewed draft cannot be reported as successful; an accepted partial answer retains its limitations when handed back to Supervisor.

Saved answers retain original source dates and bindings. Related questions need their own review; archive/delete and concurrent revision checks prevent a stale run from overwriting the user's current library state.

## Verification

From the Engine directory, run focused offline tests:

```sh
python -m pytest tests/core/test_research_agent.py tests/core/test_research_model_call.py tests/core/test_research_review_retirement.py -q
```

Use the [test map](../../tests/README.md) and [manual harness guide](../../tests/scripts/README.md) for acquisition, citations, saved-answer lifecycle, and live tests. Live harnesses require an explicit `--live` switch and configured provider access. Unit tests do not establish provider, browser login, Web/Phone, installer, or physical-device acceptance.
