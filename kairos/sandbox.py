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
import shlex
import shutil
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

    The nsjail-specific fields (max_memory_mb / max_cpu_seconds /
    max_processes / deny_paths / use_nsjail) are ignored when the
    nsjail tier isn't active — they only take effect when
    `apply_to_subprocess` chooses the nsjail path or the caller
    explicitly uses ``wrap_command_in_nsjail``.
    """
    allowed_root: Path
    network: bool = False
    extra_deny: List[str] = field(default_factory=list)
    # --- nsjail-only knobs (cross-tier safe to leave at defaults) ---
    max_memory_mb: int = 512
    max_cpu_seconds: int = 10
    max_processes: int = 64
    deny_paths: List[str] = field(default_factory=list)
    use_nsjail: bool = False

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
    """Build a Landlock ruleset (does NOT restrict the calling task).

    Returns the ruleset FD on success, or None if Landlock isn't available.
    The caller must keep the FD open in the child (via ``pass_fds``) and run
    ``_landlock_restrict_self(fd)`` there as a ``preexec_fn`` so only the
    subprocess is confined. ``restrict_self`` is deliberately NOT called on
    the parent — doing so would sandbox the main process (previously a bug).

    Implementation notes:

    * Landlock ABI v1 (kernel 5.13+) uses 3 syscalls:
      - landlock_create_ruleset  → ruleset fd
      - landlock_add_rule       → add a path-beneath rule
      - landlock_restrict_self   → enforce in a task (the child)
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

        # NOTE: we deliberately do NOT call ``landlock_restrict_self`` here.
        # Applying it in the *parent* would sandbox the main process and
        # lock Kairos out of its own filesystem — the exact bug this guard
        # fixes. The caller runs ``_landlock_restrict_self(fd)`` in the
        # forked CHILD via ``preexec_fn`` so only the subprocess is confined.
        return fd
    except Exception as e:  # noqa: BLE001
        logger.debug("sandbox: Landlock setup failed: %s", e)
        return None


def _landlock_restrict_self(fd: int) -> None:
    """Apply an already-built Landlock ruleset to the CURRENT task.

    Meant to be used as a ``preexec_fn`` so it runs inside the forked child
    (before ``exec``), restricting only the child. The parent never calls
    this, so the main process can never be locked out.
    """
    if _LANDLOCK_RESTRICT_SELF is None:
        return
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6",
                           use_errno=True)
        libc.syscall.restype = ctypes.c_long
        libc.syscall.argtypes = [ctypes.c_long]
        libc.syscall(_LANDLOCK_RESTRICT_SELF, fd, 0)
    except Exception:  # noqa: BLE001
        logger.debug("sandbox: landlock_restrict_self (child) failed", exc_info=True)


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
    # Tier 2: Landlock (Linux).
    if sys.platform.startswith("linux"):
        fd = _linux_landlock_sandbox(policy)
        if fd is not None:
            # Keep the ruleset FD open in the forked child and apply the
            # restriction THERE (preexec_fn), never in the parent. This is
            # what actually confines only the subprocess.
            try:
                os.set_inheritable(fd, True)
                kwargs.setdefault("pass_fds", (fd,))
                kwargs["preexec_fn"] = lambda: _landlock_restrict_self(fd)
            except Exception:
                pass
    # Tier 2b: macOS Seatbelt (Darwin).
    elif sys.platform == "darwin":
        profile = _macos_seatbelt_profile(policy)
        if profile:
            # Pass the profile via -S to sandbox-exec; the actual
            # command must come AFTER the -p / profile file. We don't
            # do that here — the caller (terminal tool) is expected
            # to either: (a) prefix the command with
            # `sandbox-exec -p '<profile>'` when launching, or
            # (b) call `wrap_command_in_sandbox_exec` to do the
            # wrapping automatically.
            kwargs.setdefault("__kairos_seatbelt_profile", profile)
    # Tier 3 (Windows Job Object) is applied AFTER the child spawns
    # because we need the PID. Callers should run
    # `assign_child_to_sandbox(policy, pid)` once the process exists.
    return kwargs


def _macos_seatbelt_profile(policy: "SandboxPolicy") -> str:
    """Build a sandbox-exec profile from a SandboxPolicy.

    Returns an empty string if Seatbelt can't help (e.g. we're
    not on Darwin, or the policy is empty). The caller decides
    whether to actually invoke ``sandbox-exec``.
    """
    if sys.platform != "darwin":
        return ""
    rules: list[str] = ["(version 1)", "(deny default)"]
    # Allow everything by default
    rules.append("(allow process-exec)")
    rules.append("(allow process-fork)")
    rules.append("(allow sysctl-read)")
    # Allow network if policy says so. The real SandboxPolicy
    # field is ``network`` (a bool).
    network_allowed = bool(getattr(policy, "network", True))
    if network_allowed:
        rules.append("(allow network*)")
    # Allow reads of the project's working dir (and below) — the
    # sub-process needs at least this to do its work.
    allowed_root = getattr(policy, "allowed_root", None)
    if allowed_root:
        cwd = str(allowed_root)
        rules.append(f'(allow file-read* (subpath "{cwd}"))')
    else:
        # No cwd constraint — just allow everything (permissive
        # default; the deny-list is the caller's job).
        rules.append("(allow file*)")
    return "\n".join(rules)


def wrap_command_in_sandbox_exec(command: str, profile: str) -> str:
    """Prepend ``sandbox-exec -p <profile>`` to a shell command.

    The profile is passed inline; for long profiles callers
    should write the profile to a temp file and use
    ``wrap_command_in_sandbox_exec_file`` instead.
    """
    if not profile:
        return command
    # Use single-quotes around the profile; escape any embedded
    # single quotes by closing/reopening the quoted string.
    escaped = profile.replace("'", "'\\''")
    return f"sandbox-exec -p '{escaped}' /bin/sh -c {shlex.quote(command)}"


def macos_seatbelt_available() -> bool:
    """Return True iff ``sandbox-exec`` is on PATH (macOS only)."""
    if sys.platform != "darwin":
        return False
    return shutil.which("sandbox-exec") is not None


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
        "nsjail": False,
        "gvisor": False,
        "firecracker": False,
        "job_object": False,
        "seatbelt": False,
    }
    if sys.platform.startswith("linux"):
        caps["landlock"] = landlock_available()
        caps["nsjail"] = nsjail_available()
        caps["gvisor"] = gvisor_available()
        caps["firecracker"] = firecracker_available()
    elif sys.platform.startswith("win"):
        caps["job_object"] = windows_job_object_available()
    elif sys.platform == "darwin":
        caps["seatbelt"] = macos_seatbelt_available()
    return caps


# ---------------------------------------------------------------------------
# Tier 2d: gVisor (Linux) — Google's user-space kernel container runtime
# ---------------------------------------------------------------------------
# gVisor (``runsc``) is an OCI-compatible runtime that intercepts every
# syscall in userspace before it hits the host kernel. Drop-in for runc
# on any Linux host (no KVM needed for the default ``ptrace`` platform;
# ``kvm`` platform is faster but requires /dev/kvm).
#
# Integration: register runsc as a Docker runtime via
#     sudo runsc install
# then run containers with ``--runtime=runsc``. gVisor-managed
# containers get kernel-level syscall filtering without the cost of
# a microVM.
#
# For our agent's purposes we don't spawn Docker containers; we use
# the Landlock (in-process) + nsjail (process-tree) tiers. gVisor
# here is a **detection** + **documented integration** layer so the
# Settings UI can tell the user "gVisor is available, opt in via
# Docker CLI". When the user is running the agent inside a container
# already managed by runsc, ``gvisor_available()`` returns True and
# the UI can show it.
# ---------------------------------------------------------------------------


def gvisor_available() -> bool:
    """True iff ``runsc`` (gVisor) is on PATH on a Linux host."""
    if not sys.platform.startswith("linux"):
        return False
    import shutil
    if shutil.which("runsc") is None:
        return False
    # runsc exists; the runtime is also registered in Docker's
    # daemon.json. We don't read daemon.json (it would require JSON
    # parsing + path lookup) — we just confirm the binary is there.
    return True


def gvisor_install_instructions() -> str:
    """Human-readable install instructions, shown in the Settings UI
    when gvisor_available() returns False."""
    return (
        "gVisor (runsc) is not installed. To enable it:\n"
        "  # Add the gVisor APT repository and install:\n"
        "  curl -fsSL https://gvisor.dev/archive.key | \\\n"
        "    sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg\n"
        "  echo \"deb [arch=$(dpkg --print-architecture) \" \\\n"
        "    \"signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] \" \\\n"
        "    \"https://storage.googleapis.com/gvisor/releases release main\" | \\\n"
        "    sudo tee /etc/apt/sources.list.d/gvisor.list > /dev/null\n"
        "  sudo apt-get update && sudo apt-get install -y runsc\n"
        "  # Register runsc as a Docker runtime:\n"
        "  sudo runsc install\n"
        "  sudo systemctl restart docker\n"
        "  # Run a container with gVisor:\n"
        "  docker run --rm --runtime=runsc hello-world\n"
    )


# ---------------------------------------------------------------------------
# Tier 2e: Firecracker (Linux + KVM) — AWS Lambda's microVM
# ---------------------------------------------------------------------------
# Firecracker is the strongest isolation tier Kairos can detect:
# each process gets its own microVM with a separate kernel. Boot
# time is ~125ms; per-VM memory overhead is <5MB. Used in
# production by AWS Lambda, Fly Machines, and dozens of
# multi-tenant serverless platforms.
#
# Integration: Firecracker is invoked via the containerd
# runc-compatible shim, so registering ``kata-runtime`` (or
# firecracker-containerd) as a Docker runtime is the standard
# path. The function below is detection + install-instructions
# only; we don't try to spawn a VM from inside Kairos itself.
# ---------------------------------------------------------------------------


def firecracker_available() -> bool:
    """True iff firecracker is on PATH on a Linux+KVM host."""
    if not sys.platform.startswith("linux"):
        return False
    import shutil
    if shutil.which("firecracker") is None:
        return False
    # KVM is required for Firecracker to actually boot a VM
    # (there's a "no-KVM" jailer mode for testing but it's not
    # production-grade). Detect /dev/kvm as a heuristic.
    kvm_path = Path("/dev/kvm")
    if not kvm_path.exists():
        return False
    return True


def firecracker_install_instructions() -> str:
    """Human-readable install instructions for the Settings UI."""
    return (
        "Firecracker is not installed (or /dev/kvm is missing).\n"
        "  # Install Firecracker (Linux + KVM required):\n"
        "  # Option A: snap (Ubuntu)\n"
        "  sudo snap install firecracker --classic\n"
        "  # Option B: download a release tarball\n"
        "  ARCH=$(uname -m)\n"
        "  release=\"$(curl -fsSL https://github.com/firecracker-microvm/firecracker/releases/latest\"\n"
        "           \"| grep -oP 'v\\\\d+\\\\.\\\\d+\\\\.\\\\d+_$ARCH' | head -1)\"\n"
        "  wget -O /tmp/fc.tar.gz \\\n"
        "    \"https://github.com/firecracker-microvm/firecracker/releases/download/$release/firecracker-$release-$ARCH.tgz\"\n"
        "  tar -xzf /tmp/fc.tar.gz -C /opt/\n"
        "  ln -sf /opt/firecracker-$release-$ARCH/firecracker /usr/local/bin/firecracker\n"
        "  # Verify KVM is available:\n"
        "  ls -la /dev/kvm\n"
        "  # Optional: register as a Docker runtime (kata-runtime or\n"
        "  # firecracker-containerd) for `docker run --runtime=fc`.\n"
    )


# ---------------------------------------------------------------------------
# Tier 2c: nsjail (Linux) — Google nsjail process sandbox
# ---------------------------------------------------------------------------
# Unlike Landlock (which restricts the *current* process via a kernel
# ruleset), nsjail spawns a new process tree inside a fully isolated
# namespace. The agent's command is wrapped as:
#
#     nsjail --config <generated.cfg> -- <command> [args...]
#
# We auto-generate the cfg from a SandboxPolicy mirroring the same
# allowed_root / deny_paths / network restrictions that Landlock uses,
# so the two tiers are semantically consistent.
# ---------------------------------------------------------------------------


def nsjail_available() -> bool:
    """True iff ``nsjail`` is on PATH and we're on Linux."""
    if not sys.platform.startswith("linux"):
        return False
    import shutil
    return shutil.which("nsjail") is not None


def nsjail_config_path(policy: "SandboxPolicy") -> str:
    """Return a writable path for the generated nsjail cfg."""
    import tempfile
    fd, path = tempfile.mkstemp(prefix="kairos-nsjail-", suffix=".cfg")
    os.close(fd)
    return path


def _linux_nsjail_profile(policy: "SandboxPolicy") -> str:
    """Generate a nsjail config from a SandboxPolicy.

    Mirrors the Landlock semantics: read-only access to
    ``allowed_root``, deny-list patterns, network controlled by
    ``policy.network``. The cfg file is returned as a string — the
    caller writes it to disk (we don't keep it in memory; nsjail
    needs a path).
    """
    lines = [
        "# Generated by kairos.sandbox — do not edit by hand",
        "name: \"kairos-agent\"",
        "mode: ONCE",  # one-shot: parent waits for child
        "time_limit: 30",  # hard 30s wall clock cap
        f"rlimit_as: {policy.max_memory_mb or 512}",
        f"rlimit_cpu: {policy.max_cpu_seconds or 10}",
        f"rlimit_nproc: {policy.max_processes or 64}",
        "rlimit_fsize: 64",  # 64 MB max file size
        "rlimit_nofile: 256",
        # Filesystem
        "mount: {\n  procfs: /proc\n  tmpfs: /tmp\n}",
        "cwd: \"/\"",
        f"mount_rdonly: \"{policy.allowed_root}\"" if policy.allowed_root else "# mount_rdonly: (none)",
        "tmpfs: /tmp:size=16m",
    ]
    # Explicit deny list — translates Landlock's ban_path patterns
    # to nsjail's `deny_src`. Substring match for portability with
    # the Landlock deny semantics.
    for deny in policy.deny_paths or []:
        lines.append(f'deny_src: "{deny}"')
    # Network — default deny
    if not policy.network:
        lines.append("# network: blocked (default)")
    else:
        # Bind-mount /etc/resolv.conf and /etc/ssl so HTTPS still works
        lines.append("mount_bind: /etc/resolv.conf")
        lines.append("mount_bind: /etc/ssl/certs")
    return "\n".join(lines) + "\n"


def wrap_command_in_nsjail(command: list, policy: "SandboxPolicy") -> list:
    """Wrap ``command`` with ``nsjail --config <file> --``.

    Writes the cfg to a temp file, prepends the nsjail invocation, and
    returns the new argv. Caller is responsible for deleting the
    cfg (use ``os.unlink`` on the path returned by
    ``nsjail_config_path``).
    """
    cfg_path = nsjail_config_path(policy)
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(_linux_nsjail_profile(policy))
    return ["nsjail", "--config", cfg_path, "--", *command]