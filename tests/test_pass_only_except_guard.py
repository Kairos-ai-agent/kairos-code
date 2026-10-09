"""Source guard: no new ``except`` handler may silently swallow.

An ``except`` whose body is only ``pass`` (or a docstring / ellipsis) discards
the failure with no trace. When it wraps a *side-effecting* call, a real effect
— a message publish, a file write, a save — can silently never happen while the
caller is told it succeeded. That is exactly the ``_orch`` bug in
``api/routes/p2_features.py``: an undefined name raised ``NameError`` into a
bare ``pass``, the publish never ran, and the endpoint still returned
``{"status": "queued"}``.

This guard freezes the *existing* stock of such handlers — many are legitimate
cleanup / optional-dependency probes — in a reason-carrying allowlist, and fails
on any new one so a fresh silent swallow cannot ship unnoticed. Handlers whose
``try`` body calls something in ``_SIDE_EFFECT_VERBS`` are marked ``DEBT`` in
their reason: they hide a real effect and should get a trace instead of an
allowlist entry (the pre-existing ones are known, out-of-scope debt).

Pure source audit — ``ast`` only, no import, no execution.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_SCAN_ROOTS = ("api", "kairos")

# Effects whose failure must never be swallowed silently. A pass-only handler
# wrapping one of these is surfaced as DEBT even when pre-existing.
_SIDE_EFFECT_VERBS = (
    "publish", "save", "write", "send", "start", "emit", "submit", "notify",
    "dispatch", "enqueue", "persist", "commit", "flush", "upload", "record",
)

# token -> reason. token = "<relpath>::<qualname>::<exc>".
#   * Keyed WITHOUT line numbers, so editing above a handler does not rot it.
#   * reason starting with "DEBT:" = the handler hides a side-effecting call;
#     it is frozen only because it predates this guard and lives outside the
#     scope of the change that added the guard. New ones must be fixed, not
#     allowlisted.
#   * any token that no longer matches a handler is reported as stale.
_ALLOWLIST: dict[str, str] = {
    "api/app.py::lifespan::asyncio.CancelledError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "api/app.py::_frontend_dist_dir::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "api/routes/config.py::_probe_post_openai_chat::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "api/routes/config.py::_probe_post_anthropic::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "api/routes/fs.py::_home_roots::OSError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "api/routes/p2_features.py::stream_events.gen::asyncio.CancelledError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "api/routes/p2_features.py::lsp_check::(ValueError,IndexError)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "api/routes/p2_features.py::persist_state_get::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "api/routes/p2_features.py::daemon_status::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "api/routes/p2_features.py::_load_leaderboard::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "api/routes/teams.py::delete_team::OSError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "api/routes/websocket.py::collaboration_ws::WebSocketDisconnect": "DEBT: swallows side-effecting call(s) send_json; pre-existing, needs a trace in a dedicated pass",
    "api/routes/workbench.py::_resolve_project_root::OSError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "api/routes/workbench.py::open_folder::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "api/routes/workbench.py::open_folder::Exception #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "api/routes/workbench.py::open_folder::Exception #3": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "api/routes/workbench.py::get_diff::OSError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "api/routes/workbench.py::get_diff::OSError #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/autonomous_worker.py::AutonomousWorker.stop::(asyncio.CancelledError,Exception)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/autonomous_worker.py::AutonomousWorker._loop::asyncio.TimeoutError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/browser.py::BrowserManager._evict_idle::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/browser.py::BrowserManager._evict_idle::Exception #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/browser.py::BrowserManager.close_project::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/cli.py::run_exec::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/cli.py::run_exec::Exception #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/cli.py::run_exec::OSError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/context_governor.py::_with_content::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/continual_harness.py::HarnessStore.history_tail::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/cost.py::set_log_path::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/cost.py::litellm_cost_callback::Exception": "DEBT: swallows side-effecting call(s) is_recording; pre-existing, needs a trace in a dedicated pass",
    "kairos/daemon.py::DaemonSupervisor.start::Exception": "DEBT: swallows side-effecting call(s) write_text; pre-existing, needs a trace in a dedicated pass",
    "kairos/daemon.py::DaemonSupervisor.stop::(asyncio.CancelledError,Exception)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/daemon.py::DaemonSupervisor._loop::asyncio.TimeoutError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/daemon.py::DaemonSupervisor._heartbeat::Exception": "DEBT: swallows side-effecting call(s) publish; pre-existing, needs a trace in a dedicated pass",
    "kairos/demo.py::_models_config_path::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/demo.py::run_demo::(asyncio.CancelledError,Exception)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/demo.py::run_demo::OSError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/demo.py::run_demo::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/demo.py::run_demo::Exception #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/eval.py::_default_parse_score::ValueError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/extensions_install.py::_atomic_write::OSError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/feishu.py::FeishuEventForwarder.stop::(asyncio.CancelledError,Exception)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/gate_report.py::_ledger_entries::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/gate_report.py::_ledger_entries::Exception #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/har.py::acquire_lock::(OSError,ValueError,IndexError)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/har.py::release_lock::(OSError,ValueError,IndexError)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/hook.py::check_windows_compat::(AttributeError,io.UnsupportedOperation)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/intake.py::_list_tree::OSError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/intake.py::_extract_json::ValueError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/mcp_client.py::StdioMcpClient.close::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/mcp_client.py::StdioMcpClient.close::ProcessLookupError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/mcp_client.py::StdioMcpClient.close::Exception #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/mcp_client.py::StdioMcpClient.close::Exception #3": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/memory_kb.py::MemoryKB._save::OSError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/metrics.py::install_middleware._resolve_template::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/metrics.py::install_middleware._resolve_template::Exception #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/netsec.py::_host_internal::ValueError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/netsec.py::is_safe_config_url::ValueError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/observability.py::Tracer.span::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/observability.py::_OtelSpanAdapter.set_attribute::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/observability.py::_OtelSpanAdapter.set_status::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/observability.py::_OtelSpanAdapter.record_exception::Exception": "DEBT: swallows side-effecting call(s) record_exception; pre-existing, needs a trace in a dedicated pass",
    "kairos/observability.py::_OtelSpanAdapter.add_event::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/observability.py::_OtelLlmAdapter.set_attribute::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/permissions.py::load_policy::ValueError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/sentinel.py::_app_key_store_paths::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/sentinel.py::SentinelAudit._prune::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/skill_search.py::fts5_available::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/skills_watcher.py::SkillsWatcher.stop::asyncio.CancelledError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/skills_watcher.py::SkillsWatcher.stop_sync::RuntimeError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/taint.py::release_tracker::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/task_router.py::scan_workspace::OSError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/task_router.py::scan_workspace::OSError #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/task_router.py::scan_workspace::OSError #3": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/task_router.py::scan_workspace::OSError #4": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tracing.py::TraceRecorder.__del__::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/trend.py::_load_run::(TypeError,ValueError)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/trend.py::_load_run::(TypeError,ValueError) #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/trend.py::_load_run::(TypeError,ValueError) #3": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/updater.py::check_for_update::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/voice.py::WhisperSTTProvider.transcribe::OSError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/voice_offline.py::Pyttsx3TTSProvider.__init__::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/voice_offline.py::Pyttsx3TTSProvider.synthesize::OSError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/voice_offline.py::EspeakTTSProvider.synthesize::OSError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/voice_offline.py::available_providers::ImportError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/voice_offline.py::available_providers::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/wecom.py::_aes_cbc_encrypt::ImportError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/wecom.py::_aes_cbc_decrypt::ImportError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/wecom.py::WeComBot.send_text::(TypeError,ValueError)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/wecom.py::WeComEventForwarder.stop::(asyncio.CancelledError,Exception)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/weixin_ilink.py::WeixinChannel.stop_account::(asyncio.CancelledError,Exception)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/weixin_ilink.py::WeixinChannel.stop_account::(asyncio.CancelledError,Exception) #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/weixin_ilink.py::WeixinChannel.stop_account::ILinkError": "DEBT: swallows side-effecting call(s) notify_stop; pre-existing, needs a trace in a dedicated pass",
    "kairos/worker_identity.py::_write::OSError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/agents/base.py::KairosAgent._resolve_context_window::(TypeError,ValueError)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/agents/agent_parts/chat.py::AgentChatMixin._build_chat_system_prompt::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/agents/agent_parts/llm.py::AgentLLMMixin._stream_complete::(json.JSONDecodeError,TypeError)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/agents/agent_parts/llm.py::AgentLLMMixin._stream_complete::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/bench/harness_eval.py::HarnessTask.score::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/bench/harness_semantic_eval.py::_import_from_file::ValueError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/bench/harness_semantic_eval.py::_v_import::ValueError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/bench/runner.py::BenchmarkRunner._track_tokens::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/core/persistence.py::Persistence._migrate::sqlite3.OperationalError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/core/persistence.py::Persistence._migrate::sqlite3.OperationalError #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/core/persistence.py::Persistence._migrate::sqlite3.OperationalError #3": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/core/orchestrator_parts/lifecycle.py::OrchLifecycleMixin.close::(asyncio.TimeoutError,asyncio.CancelledError,Exception)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/core/orchestrator_parts/lifecycle.py::OrchLifecycleMixin.close::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/core/persistence_parts/projects.py::ProjectStoreMixin.delete_project::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/core/persistence_parts/projects.py::ProjectStoreMixin.delete_project_memory::sqlite3.OperationalError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/core/persistence_parts/projects.py::ProjectStoreMixin.delete_project_memory::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/core/persistence_parts/rounds.py::RoundStoreMixin.delete_loop_rounds::sqlite3.OperationalError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/extensions/market_sources.py::total_count::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/hooks/__init__.py::HookRegistry._force_kill::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/hooks/__init__.py::HookRegistry._force_kill::asyncio.TimeoutError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/hooks/__init__.py::HookRegistry._force_kill::(ProcessLookupError,PermissionError)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/hooks/__init__.py::HookRegistry._force_kill::(asyncio.TimeoutError,Exception)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/learning/reflect.py::maybe_run_reflection::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/learning/reflect.py::_extract_patterns_to_notes::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/learning/reflect.py::_extract_json::(json.JSONDecodeError,ValueError)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/llm/model_router.py::ModelRouter.assign_role_model::RuntimeError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/llm/scripted.py::ScriptedProvider._record_cost::Exception": "DEBT: swallows side-effecting call(s) record_entry; pre-existing, needs a trace in a dedicated pass",
    "kairos/llm/providers/litellm_provider.py::_to_response::(json.JSONDecodeError,TypeError)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/loop/loop_runner.py::_run_precheck::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/loop/loop_runner.py::_objective_signal::(TypeError,ValueError)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/loop/loop_runner.py::_best_of_n_attempts::Exception": "DEBT: swallows side-effecting call(s) publish; pre-existing, needs a trace in a dedicated pass",
    "kairos/loop/loop_runner.py::_maybe_auto_approve_plan::Exception": "DEBT: swallows side-effecting call(s) publish; pre-existing, needs a trace in a dedicated pass",
    "kairos/loop/loop_runner.py::run_loop::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/loop/loop_runner.py::run_loop::Exception #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/loop/loop_runner.py::run_loop::Exception #3": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/loop/loop_runner.py::run_loop::Exception #4": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/loop/precheck.py::_terminate_tree::(asyncio.TimeoutError,ProcessLookupError)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/loop/precheck.py::_has_mypy_config::OSError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/loop/precheck.py::_has_mypy_config::OSError #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/memory/doc_search.py::drop_document::sqlite3.OperationalError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/memory/doc_search.py::drop_project::sqlite3.OperationalError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/memory/doc_search.py::search::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/memory/doc_search.py::search::Exception #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/memory/retrieval.py::assemble_coder_memory::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/memory/retrieval.py::assemble_coder_memory::Exception #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/sandbox/__init__.py::<module>::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/sandbox/__init__.py::apply_to_subprocess::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/skeleton/adapters.py::WorkspaceBinding.restore::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/skeleton/adapters.py::bind_agent_to_workspace::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/skeleton/adapters.py::bind_agent_to_workspace::Exception #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/skeleton/read_tools.py::run_read_tool_loop::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/skeleton/workspaces.py::RepoWorkspace._git_tracked::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/skeleton/workspaces.py::DocSetWorkspace.__init__::OSError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/base.py::BaseTool.__init_subclass__::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/base.py::BaseTool._invalidate_cache::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/base.py::_capability_precheck::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/browser_tool.py::BrowserTool._dispatch::Exception": "DEBT: swallows side-effecting call(s) record_screenshot; pre-existing, needs a trace in a dedicated pass",
    "kairos/tools/checkpoint.py::checkpoint_round::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/code_search.py::build_semble_index._build::Exception": "DEBT: swallows side-effecting call(s) save_index_to_cache; pre-existing, needs a trace in a dedicated pass",
    "kairos/tools/code_search.py::CodeSearchTool._format_location::(ValueError,OSError)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/history_search.py::HistorySearchTool._run::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/python_run.py::PythonRunTool.execute::ProcessLookupError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/python_run.py::PythonRunTool.execute::OSError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/subagent.py::SubagentTool._prune_old_outputs::OSError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/subagent.py::SubagentTool._prune_old_outputs::OSError #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/terminal.py::_console_encodings::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/terminal.py::TerminalTool.execute::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/terminal.py::TerminalTool.execute::Exception #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/terminal.py::TerminalTool.execute::(BrokenPipeError,ConnectionResetError)": "DEBT: swallows side-effecting call(s) write; pre-existing, needs a trace in a dedicated pass",
    "kairos/tools/terminal.py::TerminalTool.execute::Exception #3": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/terminal.py::TerminalTool._stream_process._drain::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/terminal.py::TerminalTool._stream_process::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/terminal.py::TerminalTool._kill_tree::Exception": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/terminal.py::TerminalTool._kill_tree::(ProcessLookupError,PermissionError)": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/terminal.py::TerminalTool._kill_tree::asyncio.TimeoutError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/terminal.py::TerminalTool._kill_tree::(ProcessLookupError,PermissionError) #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/terminal.py::TerminalTool._kill_tree::ProcessLookupError": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/terminal.py::TerminalTool._kill_tree::asyncio.TimeoutError #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/terminal.py::TerminalTool._kill_tree::ProcessLookupError #2": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
    "kairos/tools/terminal.py::TerminalTool._kill_tree::asyncio.TimeoutError #3": "cleanup / optional-probe / cancellation swallow (body has no recognised side effect)",
}


def _is_pass_only(handler: ast.ExceptHandler) -> bool:
    if not handler.body:
        return False
    for stmt in handler.body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # docstring / ellipsis
        return False
    return True


def _exc_repr(handler: ast.ExceptHandler) -> str:
    if handler.type is None:
        return "bare"
    return ast.unparse(handler.type).replace(" ", "")


def _side_effects(stmts) -> list[str]:
    names: list[str] = []
    for stmt in stmts:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                func = node.func
                name = (func.attr if isinstance(func, ast.Attribute)
                        else func.id if isinstance(func, ast.Name) else "")
                if name and any(v in name.lower() for v in _SIDE_EFFECT_VERBS):
                    if name not in names:
                        names.append(name)
    return names


class _Collector(ast.NodeVisitor):
    def __init__(self) -> None:
        self._stack: list[str] = []
        self.records: list[dict] = []

    def _visit_func(self, node) -> None:
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func
    visit_ClassDef = _visit_func

    def visit_Try(self, node) -> None:
        self._handlers(node)
        self.generic_visit(node)

    def visit_TryStar(self, node) -> None:  # except* (3.11+)
        self._handlers(node)
        self.generic_visit(node)

    def _handlers(self, node) -> None:
        for handler in node.handlers:
            if _is_pass_only(handler):
                self.records.append({
                    "qual": ".".join(self._stack) or "<module>",
                    "exc": _exc_repr(handler),
                    "line": handler.lineno,
                    "side": _side_effects(node.body),
                })


def iter_pass_only_handlers(source: str, filename: str) -> list[dict]:
    """Return one record per pass-only ``except`` handler in ``source``.

    Each record: ``rel`` (the filename given), ``qual`` (dotted enclosing
    scope), ``exc`` (canonical exception text), ``line``, ``side`` (side-effect
    call names in the guarded ``try`` body), and ``token``.
    """
    tree = ast.parse(source, filename)
    collector = _Collector()
    collector.visit(tree)
    seen: dict[str, int] = {}
    records: list[dict] = []
    for rec in collector.records:
        base = f"{filename}::{rec['qual']}::{rec['exc']}"
        n = seen.get(base, 0) + 1
        seen[base] = n
        rec["rel"] = filename
        rec["token"] = base if n == 1 else f"{base} #{n}"
        records.append(rec)
    return records


def audit_pass_only_handlers(records, allowlist) -> list[str]:
    """Return violations: un-allowlisted silent handlers + rotted entries."""
    violations: list[str] = []
    used: set[str] = set()
    for rec in records:
        token = rec["token"]
        if token in allowlist:
            used.add(token)
            continue
        tip = ""
        if rec["side"]:
            tip = (" (wraps side-effecting call(s): %s — give it a trace "
                   "(log/raise); do NOT add it to the allowlist)"
                   % ", ".join(rec["side"]))
        violations.append(
            f"{rec['rel']}:{rec['line']}: silent 'except {rec['exc']}'{tip}")
    for token, reason in sorted(allowlist.items()):
        if token not in used:
            violations.append(
                f"stale pass-only-except allowlist entry {token!r} "
                f"({reason!r}) — no matching handler; remove it")
    return violations


def _collect_tree() -> list[dict]:
    records: list[dict] = []
    for root in _SCAN_ROOTS:
        base = REPO_ROOT / root
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            records.extend(
                iter_pass_only_handlers(path.read_text(encoding="utf-8"), rel))
    return records


# ---------------------------------------------------------------------------
# Production assertions
# ---------------------------------------------------------------------------
def test_no_new_silent_except_handlers():
    violations = audit_pass_only_handlers(_collect_tree(), _ALLOWLIST)
    assert violations == [], (
        "a new ``except`` handler swallows silently (body is only pass); if it "
        "wraps a side-effecting call, give it a trace rather than allowlisting "
        "it:\n  " + "\n  ".join(violations)
    )


def test_silent_except_allowlist_does_not_rot():
    """A token that matches no handler must be reported as stale."""
    polluted = dict(_ALLOWLIST)
    polluted["api/app.py::zzz_no_such_scope::Exception"] = "fabricated"
    violations = audit_pass_only_handlers(_collect_tree(), polluted)
    assert any("stale pass-only-except allowlist entry" in v for v in violations), (
        f"the stale-entry check did not fire; got {violations!r}"
    )


# ---------------------------------------------------------------------------
# Hermetic red/green proofs
# ---------------------------------------------------------------------------
def test_guard_flags_a_pass_only_handler_around_a_publish():
    """The p2_features shape, reduced: a silent handler over a publish is
    flagged, and named as side-effecting so it cannot be allowlisted innocently.
    """
    snippet = (
        "async def submit(bus):\n"
        "    try:\n"
        "        await bus.publish(msg)\n"
        "    except Exception:\n"
        "        pass\n"
    )
    records = iter_pass_only_handlers(snippet, "snippet.py")
    assert len(records) == 1, records
    violations = audit_pass_only_handlers(records, {})
    assert len(violations) == 1, violations
    assert "side-effecting" in violations[0] and "publish" in violations[0], violations


def test_guard_ignores_a_handler_that_records_the_failure():
    """A handler with a trace (log/raise) is not a swallow — not flagged."""
    snippet = (
        "def f():\n"
        "    try:\n"
        "        do_work()\n"
        "    except Exception:\n"
        "        log.exception('failed')\n"
    )
    assert iter_pass_only_handlers(snippet, "snippet.py") == []


def test_guard_accepts_a_docstring_only_handler():
    """A handler whose body is just a comment/docstring is still a pass-only
    swallow (comments vanish at parse time); it must be enumerated."""
    snippet = (
        "def f():\n"
        "    try:\n"
        "        do_work()\n"
        "    except Exception:\n"
        "        'intentionally ignored'\n"
    )
    records = iter_pass_only_handlers(snippet, "snippet.py")
    assert len(records) == 1, records
