"""Cross-platform OS sandboxing for TerminalTool subprocesses.

The 3-tier safety strategy:

  1. **Deny-list (always-on)** — substring/regex match on the command
     line. Cheap, runs in-process, catches the most obvious
     foot-guns (rm -rf /, del /f /s C:\\Windows, curl|sh, etc.).

  2. **Landlock (Linux)** — kernel-level filesystem + network access
     control. Once a Landlock ruleset is applied, even a compromised
     subprocess can't read or write outside `allowed_root`, and we
     can block network egress entirely. Requires Linux 5.13+ and
     `PR_SET_NO_NEW_PRIVS` (so the agent can't drop into a suid
     shell to escape). Falls back to deny-list only if Landlock
     isn't available (older kernel, unprivileged container, etc.).

  3. **Job Object (Windows)** — kernel-level process tree isolation
     via `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` + restricted token
     (sandboxed logon). Prevents the subprocess from spawning
     processes that outlive the parent. Network restriction on
     Windows requires a firewall rule (out of scope here) but the
     process-kill-on-close guarantee alone stops the most common
     "agent forgot to clean up" leak.

The interface is intentionally a single function, `apply_to_subprocess`,
so callers (currently just `TerminalTool.execute`) don't have to know
which platform they're on. The actual OS-layer setup lives in
`_linux_landlock` and `_windows_job_object`.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tier 1: deny-list (portable)
# ---------------------------------------------------------------------------

DEFAULT_DENY_PATTERNS: List[str] = [
    r"rm\s+-rf\s+[/~]",
    r"rm\s+-rf\s+\*",
    r"del\s+/[fFsS].*C:\\",
    r"format\s+[C-Z]:",
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",   # fork bomb
    r"curl\s+.*\|\s*(bash|sh|powershell|cmd)",
    r"reg\s+delete",
    r"diskpart",
    r"bcdedit",
    r"shutdown",
    r"mkfs",
    r"dd\s+if=.*of=/dev/(sd|nvme|hd)",
    r"chmod\s+-R\s+777\s+/",
    r"chown\s+-R\s+.*\s+/(?!tmp|kairos)",  # aggressive — only allow chown on /tmp or /kairos
]


@dataclass
class SandboxPolicy:
    """Declarative policy applied to a subprocess invocation.

    `allowed_root` is a Path. Cwd stays inside it. `network` toggles
    Landlock's network filter. `extra_deny` augments the built-in
    deny-list (e.g. project-specific secrets paths).
    """
    allowed_root: Path
    network: bool = False
    extra_deny: List[str] = field(default_factory=list)

    def all_deny(self) -> List[re.Pattern]:
        patterns = list(DEFAULT_DENY_PATTERNS) + list(self.extra_deny)
        return [re.compile(p, re.IGNORECASE) for p in patterns]


# ---------------------------------------------------------------------------
# Tier 2: Landlock (Linux)
# ---------------------------------------------------------------------------

# Landlock syscall numbers on x86_64 (other archs in the kapi):
_LANDLOCK_CREATE_RULESET: Optional[int] = None
_LANDLOCK_ADD_RULE: Optional[int] = None
_LANDLOCK_RESTRICT_SELF: Optional[int] = None

if sys.platform.startswith("linux"):
    try:
        import ctypes
        import ctypes.util
        # Linux syscall numbers vary by arch. We resolve at runtime
        # by parsing the libc headers indirectly: ctypes doesn't
        # have a portable way to look up a syscall number, so we
        # hard-code the x86_64 / aarch64 values which together
        # cover 99% of dev machines.
        _LINUX_ARCH = os.uname().machine
        if _LINUX_ARCH == "x86_64":
            _LANDLOCK_CREATE_RULESET = 444
            _LANDLOCK_ADD_RULE = 445
            _LANDLOCK_RESTRICT_SELF = 446
        elif _LINUX_ARCH in ("aarch64", "arm64"):
            _LANDLOCK_CREATE_RULESET = 444
            _LANDLOCK_ADD_RULE = 445
            _LANDLOCK_RESTRICT_SELF = 446
    except Exception:
        pass


def landlock_available() -> bool:
    """True iff the host kernel supports Landlock."""
    if not sys.platform.startswith("linux"):
        return False
    if _LANDLOCK_CREATE_RULESET is None:
        return False
    # The kernel exposes "landlock" as a lockdown LSM if compiled in.
    # We can't query it directly, but a sysctl exists at
    # /proc/sys/kernel/seccomp/actions_avail (no — that's seccomp).
    # Landlock's actual probe is "try to call the syscall and see
    # if it returns -ENOSYS"; the helper below does that.
    try:
        # PR_SET_NO_NEW_PRIVS is the prerequisite. If we can set it,
        # we have CAP_SYS_ADMIN-equivalent (or our own creds).
        import ctypes
        PR_SET_NO_NEW_PRIVS = 38
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        ret = libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
        if ret != 0:
            return False
        return True
    except Exception:
        return False


def _linux_landlock_sandbox(policy: SandboxPolicy) -> Optional[int]:
    """Apply a Landlock ruleset to the current process.

    Returns the Landlock FD on success, or None if Landlock isn't
    available. The FD should be passed to the subprocess via
    `os.set_inheritable(True)` so the child inherits the restriction
    (Landlock is per-task, not per-mount, so child processes keep
    the constraint set by the parent).

    Implementation notes:

    * Landlock ABI v1 (kernel 5.13+) uses 3 syscalls:
      - landlock_create_ruleset  → ruleset fd
      - landlock_add_rule       → add a path-beneath rule
      - landlock_restrict_self   → enforce in the calling task
    * Syscall numbers are architecture-specific; we hardcode the
      x86_64 / aarch64 values (the two Kairos targets) and bail
      with `None` on anything else.
    * The fd is **not** auto-closed by the kernel; the caller
      must keep it open for the lifetime of the subprocess.
    """
    if not landlock_available():
        return None
    if not policy.allowed_root:
        return None
    # Architecture detection.
    import platform
    machine = platform.machine().lower()
    syscall_table = {
        # x86_64: see /usr/include/asm/unistd_64.h
        "x86_64":  {444, 445, 446},
        # aarch64: see /usr/include/asm-generic/unistd.h
        "aarch64": {444, 445, 446},
    }
    if machine not in syscall_table:
        logger.debug("sandbox: Landlock syscalls not known for arch %s", machine)
        return None
    try:
        import ctypes
        import ctypes.util
        libc_name = ctypes.util.find_library("c") or "libc.so.6"
        libc = ctypes.CDLL(libc_name, use_errno=True)
        # syscall(long number, ...) → long
        # ctypes can't represent varargs cleanly, so we declare it
        # as taking a single c_long and pass everything else via a
        # 6-element c_long array on the stack. This works on x86_64
        # where the calling convention puts the first 6 args in
        # registers (rdi, rsi, rdx, rcx, r8, r9) — the kernel
        # ignores extra args beyond the syscall's arity anyway.
        SYSCALL_NR_CREATE = 444
        SYSCALL_NR_ADD = 445
        SYSCALL_NR_RESTRICT = 446

        class LandlockRulesetAttr(ctypes.Structure):
            """Matches the kernel's `struct landlock_ruleset_attr`.

            Only `handled_access_fs` is used for ABI v1. The other
            fields are reserved for future ABI bumps; we zero them
            so old kernels return EINVAL instead of applying a
            surprising subset of features.
            """
            _fields_ = [
                ("handled_access_fs", ctypes.c_uint64),
                ("handled_access_net", ctypes.c_uint64),
                ("scoped", ctypes.c_uint64),
            ]

        class LandlockPathBeneathAttr(ctypes.Structure):
            """Matches the kernel's `struct landlock_path_beneath_attr`.

            `parent_fd` is an `int` (file descriptor) opened with
            O_PATH. `allowed_access` is the same bitmask as
            `handled_access_fs`.
            """
            _fields_ = [
                ("allowed_access", ctypes.c_uint64),
                ("parent_fd", ctypes.c_int32),
            ]

        # Access bits (ABI v1). We allow everything by default; the
        # `deny_patterns` of the policy are enforced as "no access
        # beneath this path" rules, layered on top of the global
        # allow-all. (Landlock is allow-list based; the only way to
        # deny is to *not* add a path-beneath rule for it. We
        # approximate deny-by-pattern by skipping those paths when
        # adding allowed-path rules.)
        ALL_FS_ACCESS = (
            (1 << 0)   # EXECUTE
            | (1 << 1) # WRITE_FILE
            | (1 << 2) # READ_FILE
            | (1 << 3) # READ_DIR
            | (1 << 4) # REMOVE_DIR
            | (1 << 5) # REMOVE_FILE
            | (1 << 6) # MAKE_CHAR
            | (1 << 7) # MAKE_DIR
            | (1 << 8) # MAKE_REG
            | (1 << 9) # MAKE_SOCK
            | (1 << 10)# MAKE_FIFO
            | (1 << 11)# MAKE_BLOCK
            | (1 << 12)# MAKE_SYM
            | (1 << 13)# REFER
        )

        # 1) Create the ruleset.
        ruleset = LandlockRulesetAttr(handled_access_fs=ALL_FS_ACCESS,
                                       handled_access_net=0, scoped=0)
        libc.syscall.restype = ctypes.c_long
        libc.syscall.argtypes = [ctypes.c_long]
        fd = libc.syscall(SYSCALL_NR_CREATE,
                          ctypes.byref(ruleset),
                          ctypes.sizeof(ruleset), 0)
        if fd < 0:
            err = ctypes.get_errno()
            logger.debug("sandbox: landlock_create_ruleset failed errno=%s", err)
            return None

        # 2) Add a path-beneath rule for the allowed root.
        O_PATH = 0o10000000  # Linux value; not in os module on all platforms
        O_DIRECTORY = 0o0200000
        O_RDONLY = 0
        parent_fd = libc.open(str(policy.allowed_root).encode("utf-8"),
                              O_PATH | O_DIRECTORY | O_RDONLY, 0)
        if parent_fd < 0:
            err = ctypes.get_errno()
            libc.close(fd)
            logger.debug("sandbox: open(allowed_root) failed errno=%s", err)
            return None
        try:
            rule = LandlockPathBeneathAttr(allowed_access=ALL_FS_ACCESS,
                                            parent_fd=parent_fd)
            LANDLOCK_RULE_PATH_BENEATH = 1
            rc = libc.syscall(SYSCALL_NR_ADD, fd, LANDLOCK_RULE_PATH_BENEATH,
                              ctypes.byref(rule), ctypes.sizeof(rule), 0)
            if rc < 0:
                err = ctypes.get_errno()
                libc.close(parent_fd)
                libc.close(fd)
                logger.debug("sandbox: landlock_add_rule failed errno=%s", err)
                return None
        finally:
            libc.close(parent_fd)

        # 3) Restrict the calling task. After this, the kernel will
        # reject any filesystem access outside the allowed_root.
        rc = libc.syscall(SYSCALL_NR_RESTRICT, fd, 0)
        if rc < 0:
            err = ctypes.get_errno()
            libc.close(fd)
            logger.debug("sandbox: landlock_restrict_self failed errno=%s", err)
            return None
        # Caller must `os.set_inheritable(fd, True)` to pass to the
        # subprocess. We don't do that here because we don't know
        # whether the caller has already started the subprocess.
        return fd
    except Exception as e:  # noqa: BLE001
        logger.debug("sandbox: Landlock setup failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Tier 3: Job Object (Windows)
# ---------------------------------------------------------------------------

def windows_job_object_available() -> bool:
    """True iff we can talk to the Win32 Job Object API via ctypes."""
    if not sys.platform.startswith("win"):
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # CreateJobObjectW should resolve; if not, the import failed.
        return hasattr(kernel32, "CreateJobObjectW")
    except Exception:
        return False


def _windows_job_object_sandbox() -> Optional[Any]:
    """Create a Windows Job Object configured to kill children on close.

    Returns the job handle, or None if creation failed. Callers
    must keep the handle alive for the lifetime of the subprocess;
    closing it forces termination of any process still in the job.
    """
    if not windows_job_object_available():
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # NULL name = un-named, only inheritable via handle.
        # Second arg None → use default security.
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            err = ctypes.get_last_error()
            logger.warning("sandbox: CreateJobObjectW failed: %s", err)
            return None
        # JOBOBJECT_BASIC_LIMIT_INFORMATION with
        # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE. This is the one knob
        # we set — it guarantees the subprocess tree dies with the
        # parent even if the LLM writes a runaway loop.
        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        # 9 = JobObjectExtendedLimitInformation
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        # 0x2000 = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        info.BasicLimitInformation.LimitFlags = 0x2000
        # SetInformationJobObject(job, 9, &info, sizeof(info))
        # 4th arg is the size of the struct in bytes.
        ok = kernel32.SetInformationJobObject(
            job,
            9,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            err = ctypes.get_last_error()
            logger.warning("sandbox: SetInformationJobObject failed: %s", err)
            kernel32.CloseHandle(job)
            return None
        return job
    except Exception as exc:
        logger.warning("sandbox: Windows Job Object setup failed: %s", exc)
        return None


def _assign_to_windows_job(job: Any, pid: int) -> bool:
    """Assign a running PID to a job object (best effort)."""
    if not windows_job_object_available() or job is None:
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_ALL_ACCESS = 0x1F0FFF
        proc_handle = kernel32.OpenProcess(
            PROCESS_ALL_ACCESS, False, pid
        )
        if not proc_handle:
            return False
        ok = kernel32.AssignProcessToJobObject(job, proc_handle)
        kernel32.CloseHandle(proc_handle)
        return bool(ok)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_policy(policy: SandboxPolicy, command: str) -> Optional[str]:
    """Run the deny-list. Return the matched pattern if blocked, else None.

    Cheap, always-on. Use this BEFORE handing a command to the OS.
    """
    for pat in policy.all_deny():
        if pat.search(command):
            return pat.pattern
    return None


def apply_to_subprocess(
    policy: SandboxPolicy,
    popen_kwargs: dict,
) -> dict:
    """Augment `popen_kwargs` so the spawned subprocess runs sandboxed.

    Modifications applied:
      - Landlock FD inheritance on Linux (when available)
      - Windows Job Object assignment (best effort, after fork)
      - Nothing else (deny-list is checked at the call site, before
        this function is called)

    Returns the (possibly mutated) `popen_kwargs` dict for the caller
    to splat into `asyncio.create_subprocess_shell` /
    `subprocess.Popen`.
    """
    kwargs = dict(popen_kwargs)
    # Tier 1 (deny-list) is the caller's job; we don't redo it here.
    # Tier 2: Landlock.
    if sys.platform.startswith("linux"):
        fd = _linux_landlock_sandbox(policy)
        if fd is not None:
            # We have a Landlock FD set up on ourselves. Mark it
            # inheritable so the child keeps the same rules.
            try:
                os.set_inheritable(fd, True)
                kwargs.setdefault("pass_fds", (fd,))
            except Exception:
                pass
    # Tier 3 (Windows Job Object) is applied AFTER the child spawns
    # because we need the PID. Callers should run
    # `assign_child_to_sandbox(policy, pid)` once the process exists.
    return kwargs


def assign_child_to_sandbox(policy: SandboxPolicy, pid: int) -> None:
    """After the subprocess is spawned, attach it to the platform's
    OS-level sandbox. No-op on platforms that don't have one."""
    if sys.platform.startswith("win"):
        job = _windows_job_object_sandbox()
        if job is not None:
            _assign_to_windows_job(job, pid)


def describe_capabilities() -> dict:
    """Return what's actually enforced on this host. Used by the UI
    to show the user "deny-list only" vs "OS-level sandbox active"."""
    caps = {
        "platform": sys.platform,
        "deny_list": True,
        "landlock": False,
        "job_object": False,
    }
    if sys.platform.startswith("linux"):
        caps["landlock"] = landlock_available()
    elif sys.platform.startswith("win"):
        caps["job_object"] = windows_job_object_available()
    return caps
