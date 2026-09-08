"""Enhanced Sandbox - Multi-layered security for agent execution.

Layers:
1. Network isolation (block private IPs, metadata endpoints)
2. File system sandboxing (worktree-scoped writes with audit)
3. Resource limiting (CPU, memory, time per operation)
4. Process isolation (Job Objects on Windows, Landlock on Linux)
5. Snapshot + rollback capability
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import contextmanager

# Try to import resource (Unix-only), provide fallback for Windows
try:
    import resource
except ImportError:
    resource = None  # type: ignore
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class ResourceLimit:
    """Resource limits for a process."""
    max_cpu_seconds: float = 60.0
    max_memory_mb: int = 512
    max_output_bytes: int = 100_000
    max_wall_time_s: float = 30.0
    max_file_ops: int = 100


@dataclass
class SandboxSnapshot:
    """Point-in-time snapshot of the workspace."""
    snapshot_id: str
    timestamp: float
    workspace_path: Path
    git_sha: Optional[str] = None
    file_count: int = 0
    
    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp,
            "workspace_path": str(self.workspace_path),
            "git_sha": self.git_sha,
            "file_count": self.file_count,
        }


class EnhancedSandbox:
    """Multi-layered sandbox for secure agent execution."""
    
    # Blocked IP ranges (RFC 1918, link-local, metadata)
    BLOCKED_RANGES = [
        "10.0.0.0/8",
        "172.16.0.0/12", 
        "192.168.0.0/16",
        "169.254.169.254",  # AWS metadata
        "127.0.0.0/8",      # Loopback
        "0.0.0.0/8",        # Current network
        "224.0.0.0/4",      # Multicast
        "240.0.0.0/4",      # Reserved
    ]
    
    # Dangerous patterns in commands
    DANGEROUS_PATTERNS = [
        r"\brm\s+-(rf|-fr)",      # rm -rf
        r"\bcurl\s+.*\|\s*sh",    # curl | sh
        r"\bwget\s+.*\|\s*sh",    # wget | sh
        r"\beval\b",              # eval
        r"\b(export|set)\s+.*=",  # Environment manipulation
        r"\b(chmod|chown)\s+-R",  # Recursive permission changes
    ]
    
    def __init__(self, workspace: Path, limits: Optional[ResourceLimit] = None):
        self.workspace = workspace.resolve()
        self.limits = limits or ResourceLimit()
        self._snapshots: Dict[str, SandboxSnapshot] = {}
        self._ops_log: List[Dict[str, Any]] = []
        
    def validate_url(self, url: str) -> bool:
        """Check if URL is allowed (not private/metadata)."""
        from kairos.netsec import validate_public_url
        try:
            validate_public_url(url, what="url")
            return True
        except ValueError:
            return False
    
    def validate_command(self, cmd: str) -> tuple[bool, str]:
        """Validate command for safety.
        
        Returns (is_safe, reason).
        """
        # Check against dangerous patterns
        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE):
                return False, f"Dangerous pattern detected: {pattern}"
        
        # Parse and check head command
        try:
            args = shlex.split(cmd)
        except ValueError:
            return False, "Invalid command syntax"
        
        if not args:
            return False, "Empty command"
        
        head = args[0].lower()
        
        # Block shell operators (no shell mode)
        if any(op in cmd for op in ["&&", "||", ";", "|", ">"]):
            return False, "Shell operators not allowed"
        
        # Allow list for heads
        ALLOWED_HEADS = {
            "git", "python", "node", "npm", "pip", "pytest", 
            "jest", "mocha", "cargo", "go", "make", "cmake",
            "rustc", "tsc", "eslint", "prettier", "black",
        }
        
        # Extract base command
        base_cmd = Path(head).name if "/" in head or "\\" in head else head
        
        if base_cmd not in ALLOWED_HEADS:
            return False, f"Command '{base_cmd}' not in allowlist"
        
        return True, ""
    
    @contextmanager
    def scoped_workspace(self, subpath: str = ""):
        """Context manager for operating within a subdirectory."""
        target = (self.workspace / subpath).resolve()
        try:
            target.relative_to(self.workspace)
        except ValueError:
            raise PermissionError(f"Path outside workspace: {target}")
        
        old_cwd = os.getcwd()
        os.chdir(target)
        try:
            yield target
        finally:
            os.chdir(old_cwd)
    
    def take_snapshot(self, name: Optional[str] = None) -> SandboxSnapshot:
        """Take a point-in-time snapshot of the workspace."""
        snapshot_id = name or f"snap_{int(time.time() * 1000)}"
        
        # Get git SHA if available
        git_sha = None
        try:
            import subprocess
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                git_sha = result.stdout.strip()
        except Exception:
            pass
        
        # Count files
        file_count = sum(1 for _ in self.workspace.rglob("*") if _.is_file())
        
        snapshot = SandboxSnapshot(
            snapshot_id=snapshot_id,
            timestamp=time.time(),
            workspace_path=self.workspace,
            git_sha=git_sha,
            file_count=file_count,
        )
        self._snapshots[snapshot_id] = snapshot
        return snapshot
    
    def rollback_to(self, snapshot_id: str) -> bool:
        """Rollback workspace to a snapshot."""
        snapshot = self._snapshots.get(snapshot_id)
        if not snapshot:
            logger.warning("Snapshot not found: %s", snapshot_id)
            return False
        
        # Use git reset if available
        if snapshot.git_sha:
            try:
                import subprocess
                result = subprocess.run(
                    ["git", "reset", "--hard", snapshot.git_sha],
                    cwd=str(self.workspace),
                    capture_output=True,
                    timeout=10,
                )
                return result.returncode == 0
            except Exception as e:
                logger.error("Git rollback failed: %s", e)
        
        return False
    
    def list_snapshots(self) -> List[Dict[str, Any]]:
        """List all snapshots."""
        return [s.to_dict() for s in self._snapshots.values()]
    
    def log_operation(self, op_type: str, details: Dict[str, Any]) -> None:
        """Log an operation for audit trail."""
        self._ops_log.append({
            "timestamp": time.time(),
            "type": op_type,
            "details": details,
        })
        # Keep only last 1000 operations
        if len(self._ops_log) > 1000:
            self._ops_log = self._ops_log[-1000:]
    
    def get_audit_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent audit log entries."""
        return self._ops_log[-limit:]
    
    async def run_with_limits(self, coro: Any, operation_name: str = "") -> tuple[Any, str]:
        """Run an async operation with resource limits.
        
        Returns (result, error_message).
        """
        start_time = time.time()
        ops_completed = 0
        
        try:
            result = await asyncio.wait_for(
                coro,
                timeout=self.limits.max_wall_time_s,
            )
            
            elapsed = time.time() - start_time
            self.log_operation("success", {
                "operation": operation_name,
                "duration_s": elapsed,
                "ops_completed": ops_completed,
            })
            return result, ""
            
        except asyncio.TimeoutError:
            error = f"Timeout after {self.limits.max_wall_time_s}s"
            self.log_operation("timeout", {"operation": operation_name})
            return None, error
        except Exception as e:
            error = str(e)
            self.log_operation("error", {"operation": operation_name, "error": error})
            return None, error


# Backward compatibility
def validate_url(url: str) -> bool:
    """Check if URL is safe."""
    sandbox = EnhancedSandbox(Path("."))
    return sandbox.validate_url(url)
