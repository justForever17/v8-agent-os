# Canvas Review / Delivery Browser Matrix

This matrix is the browser acceptance contract for the output-version review surface. It keeps version proof/resource, review mutation, and delivery readiness as separate observable states. `review.revision` is the result-level aggregate selection epoch projected onto every version; successful mutations reload the authoritative graph projection before another selection can start.

| Case | Setup | Interaction | Required browser evidence | Failure boundary |
| --- | --- | --- | --- | --- |
| Version truth | Result node has two output versions with different proof/resource | Select the older version in Inspector | Provider, recipe, cost, QA, preview and review fields all change to the selected version; no latest-node values appear | Missing proof/resource is shown as unavailable, never substituted |
| Approve with note | Selected version is pending | Enter note, click Approve | One `POST .../review` carries decision, note, selected flag and current review revision; response updates the same version | A stale response after switching Session/version is ignored |
| Reject | Selected version is pending or approved | Click Reject | Review state becomes rejected and final-selection control is cleared | Rejected output cannot be selected for delivery |
| Select final | Selected version is approved | Toggle Select as final | Review projection updates; another version in the same result is deselected by the authoritative response | Concurrent revision conflict remains visible and does not overwrite local truth |
| V1/V2 selection race | V1 mutation/reload is delayed while V2 becomes the current owner/epoch | Deliver V2 authoritative snapshot first, then delayed V1 snapshot | Same-Session owner blocks overlapping local mutation; stale epoch is discarded; the final result has exactly one selected version (V2) | A delayed response must never restore V1 or leave V1/V2 both selected |
| Dry-run readiness | Approved + selected version has authorized resource and proof | Click Check readiness | One `POST .../delivery` with `dryRun: true`; status becomes Ready; no manifest artifact is presented as a user identifier | Blocked/unavailable readiness is visible with a human-safe reason |
| Confirm delivery | Dry-run status is Ready | Click Create delivery | One `POST .../delivery` with `dryRun: false`; status becomes Delivered; review projection is reloaded from response | Failed confirmation never claims delivery and keeps retryable error visible |
| Video A/B | Two authorized MP4 versions | Play, pause, seek, switch audition A/B 30 times | Both timelines remain within acceptance drift; only selected side is audible; Range requests are observed | One side load error is visible while the other remains playable |
| Audio A/B | Two authorized WAV versions | Play, pause, seek, switch audition A/B 30 times | Same synchronization and one-side failure guarantees as video | No forced mute is applied to both audio elements |
| Cleanup | Review panel is unmounted after media playback | Close Inspector or switch Session | Media elements pause, detach `src`, call `load`, and listener/resource count returns to zero | No detached media element retains a source |

The executable real-media harness is `tests/creative_canvas_media_review_real.mjs`; the harness source is part of the Web test set, while its FFmpeg MP4/WAV fixtures are created in a temporary directory and are never checked in as binary assets. The matrix is a browser contract; the executable harness and the Web test commands are the acceptance evidence.
