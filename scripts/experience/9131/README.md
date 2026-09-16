# UI acceptance harnesses

This directory contains reusable UI and storage checks with synthetic fixtures. The scripts do not establish that a release or physical device has passed acceptance.

- `collect.py` inventories source routes and builds a coverage matrix.
- `boundary-baseline.mjs` extracts production callbacks and exercises delayed responses and storage failures.
- `observe_admin.py` starts an isolated Admin surface with synthetic APIs on port 22824. Use a new output directory and the script's explicit `--repo` and `--out` arguments.
- `fixtures.py` provides synthetic identities, drafts, configuration, queues, and timing inputs.
- `sql-baseline.py` checks production SQL against an in-memory database.
- `verify-*-candidate*`, `verify-phone-*`, and `verify-shell-*` target a supplied candidate build or device. Read their arguments and prerequisites before running; live/native operations require explicit authorization and the applicable live switch.
- `summarize_observations.py` and `summarize-integration-review.py` summarize locally generated observations.

The [acceptance matrix](acceptance-matrix.md) lists behavior and failure scenarios. Confirm that each script still matches the current routes and build before using it. A successful HTTP response or screenshot does not prove a save, install, or device action succeeded.

Store reports, screenshots, logs, and build-specific conclusions outside the repository or in ignored output directories. The checked-in scripts and synthetic fixtures are the reproducible inputs; historical result files are not release guarantees.
