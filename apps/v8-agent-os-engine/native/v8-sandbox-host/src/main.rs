use serde::Deserialize;
use std::collections::{HashMap, HashSet};
use std::env;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitCode, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, Instant};

const MAX_POLICY_BYTES: u64 = 1024 * 1024;

#[derive(Debug, Deserialize)]
struct Limits {
    wall_time_seconds: u64,
    memory_bytes: u64,
    process_count: u32,
    #[allow(dead_code)]
    output_bytes: u64,
}

#[derive(Debug, Deserialize)]
struct Policy {
    version: u32,
    lease_id: String,
    worktree_root: String,
    execution_mode: String,
    write_set: Vec<String>,
    env_allowlist: Vec<String>,
    env_overrides: Vec<(String, String)>,
    limits: Limits,
}

fn fail(message: impl AsRef<str>) -> ExitCode {
    eprintln!("v8-sandbox-host: {}", message.as_ref());
    ExitCode::from(126)
}

fn parse_arguments() -> Result<(PathBuf, Vec<String>), String> {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.len() < 4 || args[0] != "--policy" {
        return Err("usage: v8-sandbox-host --policy <file> -- <program> [args...]".into());
    }
    let separator = args
        .iter()
        .position(|item| item == "--")
        .ok_or("missing -- separator")?;
    if separator != 2 || separator + 1 >= args.len() {
        return Err("invalid sandbox host arguments".into());
    }
    Ok((PathBuf::from(&args[1]), args[(separator + 1)..].to_vec()))
}

fn load_policy(path: &Path) -> Result<Policy, String> {
    let metadata = fs::metadata(path).map_err(|error| format!("cannot inspect policy: {error}"))?;
    if !metadata.is_file() || metadata.len() > MAX_POLICY_BYTES {
        return Err("policy file is invalid or too large".into());
    }
    let bytes = fs::read(path).map_err(|error| format!("cannot read policy: {error}"))?;
    let policy: Policy =
        serde_json::from_slice(&bytes).map_err(|error| format!("invalid policy json: {error}"))?;
    if policy.version != 1 || policy.lease_id.trim().is_empty() {
        return Err("unsupported or unowned sandbox policy".into());
    }
    if policy.execution_mode != "read" && policy.execution_mode != "write" {
        return Err("invalid execution_mode".into());
    }
    if policy.execution_mode == "write" && policy.write_set.is_empty() {
        return Err("write policy requires a non-empty write_set".into());
    }
    Ok(policy)
}

fn canonical_existing(path: &Path) -> Result<PathBuf, String> {
    path.canonicalize()
        .map_err(|error| format!("cannot resolve {}: {error}", path.display()))
}

fn validate_working_directory(policy: &Policy) -> Result<PathBuf, String> {
    let root = canonical_existing(Path::new(&policy.worktree_root))?;
    let cwd = canonical_existing(
        &env::current_dir().map_err(|error| format!("cannot read cwd: {error}"))?,
    )?;
    if cwd != root && !cwd.starts_with(&root) {
        return Err(format!("cwd is outside worktree root: {}", cwd.display()));
    }
    Ok(cwd)
}

fn child_environment(policy: &Policy) -> HashMap<String, String> {
    let allowed: HashSet<String> = policy
        .env_allowlist
        .iter()
        .map(|item| item.to_ascii_uppercase())
        .collect();
    let mut environment = HashMap::new();
    for (key, value) in env::vars() {
        let normalized = key.to_ascii_uppercase();
        if allowed.contains(&normalized) {
            environment.insert(normalized, value);
        }
    }
    for (key, value) in &policy.env_overrides {
        environment.insert(key.to_ascii_uppercase(), value.clone());
    }
    environment.insert("V8_SANDBOX_LEASE_ID".into(), policy.lease_id.clone());
    environment
}

fn base_command(argv: &[String], cwd: &Path, policy: &Policy) -> Result<Command, String> {
    let mut command = Command::new(&argv[0]);
    command
        .args(&argv[1..])
        .current_dir(cwd)
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .env_clear()
        .envs(child_environment(policy));
    Ok(command)
}

#[cfg(unix)]
fn configure_platform(command: &mut Command, policy: &Policy) -> Result<PlatformGuard, String> {
    use std::os::unix::process::CommandExt;
    let memory = policy.limits.memory_bytes;
    let processes = policy.limits.process_count as libc::rlim_t;
    unsafe {
        command.pre_exec(move || {
            if libc::setsid() < 0 {
                return Err(io::Error::last_os_error());
            }
            let address_space = libc::rlimit {
                rlim_cur: memory as libc::rlim_t,
                rlim_max: memory as libc::rlim_t,
            };
            if libc::setrlimit(libc::RLIMIT_AS, &address_space) != 0 {
                return Err(io::Error::last_os_error());
            }
            let process_limit = libc::rlimit {
                rlim_cur: processes,
                rlim_max: processes,
            };
            if libc::setrlimit(libc::RLIMIT_NPROC, &process_limit) != 0 {
                return Err(io::Error::last_os_error());
            }
            #[cfg(target_os = "linux")]
            if libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        });
    }
    Ok(PlatformGuard { pgid: None })
}

#[cfg(unix)]
struct PlatformGuard {
    pgid: Option<i32>,
}

#[cfg(unix)]
impl PlatformGuard {
    fn attach(&mut self, child: &Child) {
        self.pgid = Some(child.id() as i32);
    }

    fn terminate(&self, child: &mut Child) {
        if let Some(pgid) = self.pgid {
            unsafe {
                libc::kill(-pgid, libc::SIGKILL);
            }
        }
        let _ = child.kill();
    }
}

#[cfg(unix)]
impl Drop for PlatformGuard {
    fn drop(&mut self) {
        if let Some(pgid) = self.pgid {
            unsafe {
                // A direct child may have exited after starting background work.
                // The session/process group still belongs to this one lease.
                libc::kill(-pgid, libc::SIGKILL);
            }
        }
    }
}

#[cfg(windows)]
fn configure_platform(_command: &mut Command, policy: &Policy) -> Result<PlatformGuard, String> {
    use std::mem::size_of;
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        JOB_OBJECT_LIMIT_PROCESS_MEMORY,
    };
    use windows_sys::Win32::System::Threading::GetCurrentProcess;

    unsafe {
        let job: HANDLE = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return Err(format!(
                "CreateJobObjectW failed: {}",
                io::Error::last_os_error()
            ));
        }
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | JOB_OBJECT_LIMIT_PROCESS_MEMORY;
        info.BasicLimitInformation.ActiveProcessLimit = policy.limits.process_count;
        info.ProcessMemoryLimit = policy.limits.memory_bytes as usize;
        if SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const _,
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        ) == 0
        {
            let error = io::Error::last_os_error();
            CloseHandle(job);
            return Err(format!("SetInformationJobObject failed: {error}"));
        }
        if AssignProcessToJobObject(job, GetCurrentProcess()) == 0 {
            let error = io::Error::last_os_error();
            CloseHandle(job);
            return Err(format!("AssignProcessToJobObject failed: {error}"));
        }
        Ok(PlatformGuard { job })
    }
}

#[cfg(windows)]
struct PlatformGuard {
    job: windows_sys::Win32::Foundation::HANDLE,
}

#[cfg(windows)]
impl PlatformGuard {
    fn attach(&mut self, _child: &Child) {}

    fn terminate(&self, child: &mut Child) {
        use windows_sys::Win32::System::JobObjects::TerminateJobObject;
        unsafe {
            TerminateJobObject(self.job, 124);
        }
        let _ = child.kill();
    }
}

#[cfg(windows)]
impl Drop for PlatformGuard {
    fn drop(&mut self) {
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.job);
        }
    }
}

fn wait_with_deadline(
    child: &mut Child,
    guard: &PlatformGuard,
    timeout: Duration,
) -> Result<ExitStatus, String> {
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Ok(status),
            Ok(None) if started.elapsed() < timeout => thread::sleep(Duration::from_millis(25)),
            Ok(None) => {
                guard.terminate(child);
                let _ = child.wait();
                return Err("sandbox wall time exceeded".into());
            }
            Err(error) => return Err(format!("failed to wait for child: {error}")),
        }
    }
}

fn exit_code(status: ExitStatus) -> ExitCode {
    match status.code() {
        Some(value) if (0..=255).contains(&value) => ExitCode::from(value as u8),
        Some(_) => ExitCode::from(1),
        None => ExitCode::from(125),
    }
}

fn main() -> ExitCode {
    let (policy_path, argv) = match parse_arguments() {
        Ok(value) => value,
        Err(error) => return fail(error),
    };
    let policy = match load_policy(&policy_path) {
        Ok(value) => value,
        Err(error) => return fail(error),
    };
    let cwd = match validate_working_directory(&policy) {
        Ok(value) => value,
        Err(error) => return fail(error),
    };
    let mut command = match base_command(&argv, &cwd, &policy) {
        Ok(value) => value,
        Err(error) => return fail(error),
    };
    let mut guard = match configure_platform(&mut command, &policy) {
        Ok(value) => value,
        Err(error) => return fail(error),
    };
    let mut child = match command.spawn() {
        Ok(value) => value,
        Err(error) => return fail(format!("cannot spawn child: {error}")),
    };
    guard.attach(&child);
    match wait_with_deadline(
        &mut child,
        &guard,
        Duration::from_secs(policy.limits.wall_time_seconds),
    ) {
        Ok(status) => exit_code(status),
        Err(error) => fail(error),
    }
}
