# Linux server credential backend

`LinuxServerCredentialBackend` is an explicit headless server profile backend. It
is selected by the Linux default backend when an explicit key path or systemd
credentials directory is configured, or through
`create_linux_server_credential_backend(...)`. Otherwise Linux uses Secret
Service; Windows and macOS continue to use their native OS credential stores.

- The 32-byte key is created only by `initialize_key(path)` and is never replaced
  automatically. A path may come from `V8_AGENT_OS_CREDENTIAL_KEY_FILE` or
  `CREDENTIALS_DIRECTORY`; the secret itself is never passed in an environment
  variable or CLI argument.
- `credentials/server.enc` is AES-256-GCM encrypted with a fresh nonce per write.
  AAD binds the instance id and format version. Writes use a lock file, fsync,
  and atomic replacement; the ciphertext is not changed when authentication or
  key validation fails.
- POSIX key, directory, lock, and ciphertext permissions are checked as 0600/0700.
  Missing, malformed, unreadable, or wrong keys fail closed.
- The backend stores only the encrypted target-to-value map. `CredentialRefStore`
  remains the reference owner and callers continue to use `cred:v8-*` references.

The tests cover no-D-Bus restart recovery, wrong-key preservation, explicit
initialization and permissions, concurrent updates, and missing-key failure.
Windows runs exercise format and atomicity only; POSIX permission assertions are
skipped there because Windows mode bits do not provide the same contract.
