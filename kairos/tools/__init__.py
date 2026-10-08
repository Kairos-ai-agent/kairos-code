"""Tool registry for agents.

Tools are instantiated per-project by the Orchestrator (so they share
the project's workspace + message bus). This module exposes the
factories.
"""
from kairos.tools.checkpoint import CheckpointTool, checkpoint_round, list_checkpoints, checkout_checkpoint, ensure_repo
from kairos.tools.code_search import CodeSearchTool
from kairos.tools.data_analyze import DataAnalyzeTool
from kairos.tools.doc_read import DocReadTool
from kairos.tools.file_edit import FileEditReplaceTool, FileEditTool, MultiEditTool
from kairos.tools.file_read import FileReadTool
from kairos.tools.find import FindTool
from kairos.tools.git_tool import GitTool
from kairos.tools.grep_tool import GrepTool
from kairos.tools.history_search import HistorySearchTool
from kairos.tools.python_run import PythonRunTool
from kairos.tools.subagent import SubagentTool
from kairos.tools.terminal import TerminalTool
from kairos.tools.webfetch import WebFetchTool, WebSearchTool
from kairos.tools.xlsx_read import XlsxReadTool

__all__ = [
    "CheckpointTool",
    "checkpoint_round",
    "list_checkpoints",
    "checkout_checkpoint",
    "ensure_repo",
    "FileEditReplaceTool",
    "FileEditTool",
    "MultiEditTool",
    "CodeSearchTool",
    "DataAnalyzeTool",
    "DocReadTool",
    "FileReadTool",
    "FindTool",
    "GitTool",
    "GrepTool",
    "HistorySearchTool",
    "PythonRunTool",
    "SubagentTool",
    "TerminalTool",
    "WebFetchTool",
    "WebSearchTool",
    "XlsxReadTool",
]
