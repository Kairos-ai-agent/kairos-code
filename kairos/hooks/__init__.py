"""Hook system.

Users can drop Python files into `data/hooks/` that get called at
specific lifecycle events. Each hook file must define one or more of:

    def pre_tool_use(tool_name, arguments, agent_id, project_id) -> dict | None:
        ...
    def post_tool_use(tool_name, arguments, result, agent_id, project_id) -> None:
        ...
    def loop_round(round_no, coder_summary, review, project_id) -> None:
        ...
    def loop_completed(project_id, final_score, total_rounds) -> None:
        ...

A non-None return from `pre_tool_use` REPLACES the arguments — useful
for blocking dangerous tool calls or rewriting them. `post_tool_use` is
fire-and-forget; the agent doesn't wait for it.

Failures in hooks are logged but never fail the agent — hooks are
side-effects, not gates.
"""

from kairos.hooks.runner import HookRunner, get_runner

__all__ = ["HookRunner", "get_runner"]