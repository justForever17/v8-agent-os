# Managed Engineering Execution

This package keeps four different boundaries explicit. They must not be collapsed into one path or one permission flag.

| Layer | Lifetime | Owns | Must not own |
| --- | --- | --- | --- |
| Workspace | Long-lived, user managed | Project identity, trust, scope and user-visible files | Temporary execution state |
| Git repository | Long-lived, discovered from the workspace | Version, diff and merge truth | User authorization |
| Worktree | One run/task/delegation | An isolated checkout for concurrent edits | Trust, credentials or final delivery |
| Sandbox lease | One execution attempt | Process tree, resource/environment policy, write set and evidence | Source files or Git refs |

For a workspace inside a monorepo, the topology is preserved as:

```text
originalWorkspaceRoot = <repo>/apps/example
repositoryRoot        = <repo>
workspaceRelativePath = apps/example
worktreeRoot          = <repository-parent>/.v8os-worktrees/<repo>/<run>/<task>
executionWorkspace    = <worktreeRoot>/apps/example
```

The original workspace remains the authority root. A valid sandbox policy temporarily replaces only the active execution root with `executionWorkspace`. Relative file paths and command working directories therefore resolve inside the worktree; an absolute path back to the original workspace is outside the active execution boundary.

## Lifecycle

1. A newly created, effectively empty V8OS workspace receives a baseline Git repository and managed ignore block.
2. An existing non-Git workspace requires explicit adoption. An existing repository is reused; its branch is never silently replaced.
3. Engineering dispatch snapshots the current tracked and untracked state through an alternate Git index without moving the user's `HEAD` or index.
4. Supervisor, direct child, grandchild and external worker writes run in separate managed worktrees. A sandbox lease is immutable and bound to one worktree, base commit, write set and network profile.
5. A completed task becomes a candidate commit. Nested candidates merge into their parent worktree; sibling candidates are combined in a separate integration worktree.
6. Only a validated Supervisor delivery decision applies the integration patch to the original workspace. V8OS does not silently commit or move the user's current branch.
7. After Supervisor accepts delivery, V8OS writes a durable `refs/v8os/delivered/...` recovery ref and immediately removes the physical checkout. Unaccepted or interrupted worktrees remain recoverable and follow their lifecycle policy.

Files over 20 MiB cannot enter a managed change set. During explicit adoption, pre-existing untracked files above that limit are recorded in `.git/info/exclude`; this does not delete or move them.

## Enforcement truth

The native host is intentionally small and cross-platform. Current capabilities are reported as `partial`:

| Capability | Windows | Linux | macOS |
| --- | --- | --- | --- |
| Process-tree lifetime | Job Object | isolated process group | isolated process group |
| Wall/process/memory limits | Yes, platform semantics apply | Yes, rlimits | Yes, rlimits |
| Environment allowlist | Yes | Yes | Yes |
| Hard filesystem namespace | No | No | No |
| Hard offline network namespace | No | No | No |

Filesystem safety currently combines trusted workspace binding, worktree-only relative resolution, command path preflight, immutable write sets, final Git diff validation and a 20 MiB gate. This blocks Agent tool bypasses and detects out-of-contract worktree changes, but it is not a kernel filesystem jail for arbitrary third-party binaries. Network mode is therefore named `networked_partial`; unsupported `offline_enforced` and `brokered` leases fail closed.

Future hard filesystem/network drivers must extend `SandboxCapabilities`; they must not silently upgrade `partial` to `enforced` without platform-specific live tests.

The helper source is checked on Windows ARM64 in CI, but this does not make the current full desktop distribution ARM64-ready: portable Python and other native desktop dependencies still need an ARM64 release profile. Linux and macOS helper builds are contract-tested independently of their future desktop installers.
