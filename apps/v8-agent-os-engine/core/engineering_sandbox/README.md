# Engineering Execution and Optional Git Isolation

This package keeps direct execution and optional managed isolation explicit. Git is not an Engineering prerequisite, and V8OS does not initialize a repository merely because a trusted workspace receives an Engineering task.

| Layer | Lifetime | Owns | Must not own |
| --- | --- | --- | --- |
| Workspace | Long-lived, user managed | Project identity, trust, scope and user-visible files | Temporary execution state |
| Engineering Task Capsule | One delegated task | Read/write set, outputs, acceptance, proof and shell dialect | Workspace trust or implicit write authority |
| Git repository | Optional, user-enabled or already present | Version, diff and merge truth for isolated execution | User authorization or ordinary serial execution |
| Worktree | One isolated run/task/delegation | A checkout for parallel, risky or durable-recovery writes | Trust, credentials or final delivery |
| Sandbox lease | One execution attempt | Process tree, resource/environment policy, write set and evidence | Source files or Git refs |

For a workspace inside a monorepo, the topology is preserved as:

```text
originalWorkspaceRoot = <repo>/apps/example
repositoryRoot        = <repo>
workspaceRelativePath = apps/example
worktreeRoot          = <repository-parent>/.v8os-worktrees/<repo>/<run>/<task>
executionWorkspace    = <worktreeRoot>/apps/example
```

The original workspace always remains the authority root. A serial low-risk task uses that bound workspace directly: mutations must use Capsule-aware native file tools and shell access is limited to read/validation commands. When an eligible task selects a worktree, a valid sandbox policy temporarily replaces only the active execution root with `executionWorkspace`; absolute paths back to the original workspace are outside that lease.

## Lifecycle

1. Dispatch must first produce a valid write Capsule with an explicit `writeSet`, `expectedOutputs` and acceptance contract. An incomplete contract is blocked before any execution checkout is selected.
2. A serial low-risk write stays in the trusted bound workspace. No Git probe, repository initialization, baseline commit or worktree is part of this path.
3. A complete write task selects a worktree only for concurrent writes, explicit risk isolation, or durable recovery. Opaque external CLI writers always require this boundary because native file tools cannot constrain their process writes.
4. If optional isolation is needed but unavailable, the task is reported as blocked. The Supervisor may serialize one low-risk native write or ask the user to enable Git parallel isolation; the Agent must not initialize Git itself.
5. Enabling Git parallel isolation is an explicit user action. It creates `.git` when needed and one V8OS baseline; an existing repository is reused and its branch is never silently moved.
6. Isolated dispatch snapshots tracked and untracked state through an alternate Git index. Each managed lease is immutable and bound to one worktree, base commit, write set and network profile.
7. A completed isolated task becomes a candidate commit. Nested candidates merge into their parent worktree; sibling candidates are combined in a separate integration worktree. Only a validated Supervisor delivery applies the patch to the original workspace.
8. After acceptance, V8OS rebinds Agent-written artifacts to delivered files, writes a durable recovery ref and removes the physical checkout. The janitor removes eligible terminal or abandoned worktrees only when no active/finalizing lease remains.

Files over 20 MiB cannot enter a managed change set. While explicitly enabling Git parallel isolation, pre-existing untracked files above that limit are recorded in `.git/info/exclude`; this does not delete or move them.

## Enforcement truth

The native host is intentionally small and cross-platform. Current capabilities are reported as `partial`:

| Capability | Windows | Linux | macOS |
| --- | --- | --- | --- |
| Process-tree lifetime | Job Object | isolated process group | isolated process group |
| Wall/process/memory limits | Yes, platform semantics apply | Yes, rlimits | Yes, rlimits |
| Environment allowlist | Yes | Yes | Yes |
| Hard filesystem namespace | No | No | No |
| Hard offline network namespace | No | No | No |

Direct execution combines trusted workspace binding, Capsule-scoped native writes and a read/validation-only shell boundary. Managed isolation additionally combines worktree-relative resolution, command path preflight, immutable write sets, final Git diff validation and a 20 MiB gate. This blocks native-tool bypasses and detects out-of-contract managed changes, but it is not a kernel filesystem jail for arbitrary third-party binaries. Network mode is therefore named `networked_partial`; unsupported `offline_enforced` and `brokered` leases fail closed.

Future hard filesystem/network drivers must extend `SandboxCapabilities`; they must not silently upgrade `partial` to `enforced` without platform-specific live tests.

The helper source is checked on Windows ARM64 in CI, but this does not make the current full desktop distribution ARM64-ready: portable Python and other native desktop dependencies still need an ARM64 release profile. Linux and macOS helper builds are contract-tested independently of their future desktop installers.
