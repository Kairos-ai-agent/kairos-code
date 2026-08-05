"""Tool registry for agents.

Tools are instantiated per-project by the Orchestrator (so they share
the project's workspace + message bus). This module exposes the
factories.
"""
from kairos.tools.checkpoint import CheckpointTool, checkpoint_round, list_checkpoints, checkout_checkpoint, ensure_repo
from kairos.tools.file_edit import FileEditReplaceTool, FileEditTool, MultiEditTool
from kairos.tools.file_read import FileReadTool
from kairos.tools.find import FindTool
from kairos.tools.git_tool import GitTool
from kairos.tools.grep_tool import GrepTool
from kairos.tools.subagent import SubagentTool
from kairos.tools.terminal import TerminalTool
from kairos.tools.webfetch import WebFetchTool, WebSearchTool

__all__ = [
    "CheckpointTool",
    "checkpoint_round",
    "list_checkpoints",
    "checkout_checkpoint",
    "ensure_repo",
    "FileEditReplaceTool",
    "FileEditTool",
    "MultiEditTool",
    "FileReadTool",
    "FindTool",
    "GitTool",
    "GrepTool",
    "SubagentTool",
    "TerminalTool",
    "WebFetchTool",
    "WebSearchTool",
]