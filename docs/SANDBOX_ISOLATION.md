# Sandbox Isolation — design & status

Kairos runs untrusted build/test code (`npm test`, `go test`, `pytest`, …)
on behalf of a Coder/Reviewer agent. The terminal tool's allow-list and deny
patterns are *mitigations*, not a security boundary — the real boundary has
to come from the OS. This doc spells out what is enforced today, what is
still open, and how to get to genuine isolation.

## Where the boundary *should* be

A threat model for the agent's terminal:

1. The agent (an LLM) writes or modifies project files *including* build
   config (`package.json` scripts, `Makefile`, `go.mod`), then invokes a
   build/test command.
2. That command executes arbitrary code **by design** (install scripts,
   Makefile recipes, `go test`).
3. That code can read the host filesystem, exfiltrate data, or probe the
   internal network unless the OS confines it.

So the boundary must be **OS-level**, applied to the child process: confine
filesystem access to the project tree, and (ideally) cut network egress.

## What is enforced today

| Layer | Linux | macOS | Windows |
|---|---|---|---|
| argv (no shell) | ✅ create_subprocess_exec | ✅ | ✅ |
| Head allow-list (no interpreter/leak heads) | ✅ | ✅ | ✅ |
| Build-test RCE heads (`npm`/`go`/`make`/…) | 🔒 opt-in (`KAIROS_ENABLE_BUILD_COMMANDS`) | 🔒 opt-in | 🔒 opt-in |
| deny-list (`check_policy`) | ✅ | ✅ | ✅ |
| Filesystem isolation | ⚠️ Landlock v1 via child `preexec_fn` | ❌ | ❌ |
| Network egress denied | ❌ (`handled_access_net=0`) | ❌ | ❌ |
| Process-tree cleanup | partially (new session) | partially | ✅ Job Object (KILL_ON_JOB_CLOSE) |

Notes:
- **Landlock (Linux)** is now wired into the terminal child via `preexec_fn`
  and applies filesystem rules **in the child**, not the parent. It does
  **not** block network egress (`handled_access_net=0`), so a malicious
  `npm install` script can still phone home or probe the LAN even with FS
  confinement.
- **Windows** has **no** filesystem/network confinement: only a Job Object
  that kills the process tree when the parent dies. There is no equivalent
  of Landlock/nsjail available to a Python process without deeper Windows
  primitives (AppContainer / restricted token / Hyper-V), which are out of
  scope here.
- **macOS** Seatbelt is declared but not wrapped (terminal doesn't invoke
  `sandbox-exec`).

## Recommended path

### Linux: nsjail (real isolation)

`kairos.sandbox` already has an nsjail tier (`wrap_command_in_nsjail` +
`_linux_nsjail_profile`). The design that actually closes the gap:

```
nsjail --config >(generated.cfg) -- <argv...>
```

Config that matters:

- `--clone_newnet` → **no network namespace** (blocks egress + LAN probe).
- `--ro` / `mount_rdonly` on the system root, plus a **rw bind-mount** of the
  project directory only.
- `deny_src` for the project's `.git`, `data/settings.json`, and any secret
  paths.
- `time_limit` / `rlimit_as` / `rlimit_cpu` to bound the build.
- `mode: ONCE` so the parent awaits one child.

This gives a "no host read, no network, only the project dir writable"
container for the build/test command. Pair with the deny-list + allow-list
for defense-in-depth.

### Windows: default-disable (not a fake sandbox)

Windows has no clean, portable sandbox primitive for this. Rather than bolt
on a half-sandbox, the pragmatic posture is:

- Keep the **build/test RCE heads off by default** (`npm`/`go`/`make`/… are
  not in the default allow-list; `KAIROS_ENABLE_BUILD_COMMANDS=1` to enable).
- When enabled, the operator accepts that the child can run arbitrary code
  with full host access on Windows. This is a conscious, documented tradeoff.

## Long-term options (out of scope here)

- Run each build/test in a container (gVisor `runsc`, or a native OCI runtime)
  with an empty network namespace and a read-only rootfs.
- A microVM (Firecracker) per task for the strongest isolation.
- Attach `nsjail` for Linux CI; a Windows container/Sandbox for Windows CI.

None of these are verifiable in the current sandboxed/Windows build
environment, so this doc is the design until a runtime can be validated.
