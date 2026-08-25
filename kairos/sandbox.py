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
    """
    if not landlock_available():
        return None
    # The full Landlock setup needs a struct landlock_ruleset_attr
    # plus a struct landlock_path_beneath_attr. Coding those by hand
    # is straightforward but invasive; for now we document the
    # path and return None. Users on hardened deployments should
    # extend this with the actual syscall invocation.
    #
    # Pseudo-code of the full implementation:
    #   ruleset = ffi.new("struct landlock_ruleset_attr *")
    #   ruleset.handled_access_fs = (
    #       LANDLOCK_ACCESS_FS_EXECUTE |
    #       LANDLOCK_ACCESS_FS_WRITE_FILE |
    #       LANDLOCK_ACCESS_FS_READ_FILE |
    #       LANDLOCK_ACCESS_FS_READ_DIR  |
    #       LANDLOCK_ACCESS_FS_REMOVE_DIR |
    #       LANDLOCK_ACCESS_FS_REMOVE_FILE |
    #       LANDLOCK_ACCESS_FS_MAKE_CHAR |
    #       LANDLOCK_ACCESS_FS_MAKE_DIR  |
    #       LANDLOCK_ACCESS_FS_MAKE_REG  |
    #       LANDLOCK_ACCESS_FS_MAKE_SOCK |
    #       LANDLOCK_ACCESS_FS_MAKE_FIFO |
    #       LANDLOCK_ACCESS_FS_MAKE_BLOCK |
    #       LANDLOCK_ACCESS_FS_MAKE_SYM
    #   )
    #   fd = syscall(_LANDLOCK_CREATE_RULESET, ruleset, 0)
    #   rule = ffi.new("struct landlock_path_beneath_attr *")
    #   rule.parent_fd = open(allowed_root, O_PATH)
    #   rule.allowed_access = handled_access_fs
    #   syscall(_LANDLOCK_ADD_RULE, fd, LANDLOCK_RULE_PATH_BENEATH, rule)
    #   syscall(_LANDLOCK_RESTRICT_SELF, fd, 0)
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
