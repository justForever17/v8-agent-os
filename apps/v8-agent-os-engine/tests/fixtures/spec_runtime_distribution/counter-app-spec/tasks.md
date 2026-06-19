# Tasks

### TASK-001: Implement counter page

- runtimeLane: Engineering
- dependsOn: []
- specRefs: REQ-001, REQ-002, DES-001, DES-002
- inputRefs: approved requirements/design/tasks
- expectedOutput: index.html
- acceptance: `index.html` contains `SPEC_DRY_RUN_COUNTER`, a Chinese button label, and inline JavaScript that increments the count.
- proofRequired: report touched file and smoke verification.

### TASK-002: Document usage

- runtimeLane: Engineering
- dependsOn: [TASK-001]
- specRefs: REQ-003, DES-003
- inputRefs: approved implementation target
- expectedOutput: README.md
- acceptance: README explains how to open `index.html` and verify the counter button.
- proofRequired: report touched file.
