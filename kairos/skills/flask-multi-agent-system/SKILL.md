---
name: "flask-multi-agent-system"
description: "Build multi-agent collaboration management systems with Flask+SQLite. Covers agent management, task orchestration, workflow engine, inter-agent messaging, LLM integration, and file upload support."
priority: 0.5
version: "1.0.0"
imported-from: "agents"
source-path: "agents/skills/flask-multi-agent-system/SKILL.md"
---
# Flask Multi-Agent System Skill

## When to Use
Building web-based management systems for orchestrating multiple AI agents, including:
- Agent CRUD with roles, capabilities, and status tracking
- Task management with assignment, execution, and retry logic
- Workflow orchestration with multi-step pipelines
- Inter-agent messaging with LLM-powered responses
- File upload and paste support in chat interfaces

## Architecture

```
app.py          # Flask app with REST API
models.py       # SQLAlchemy models (Agent, Task, Workflow, Message, etc.)
templates/      # SPA frontend (single HTML file with embedded CSS/JS)
data/           # SQLite database
uploads/        # Uploaded files
```

## Database Models

### Agent
- name, alias, role (orchestrator/worker/reviewer)
- model, provider, capabilities (JSON)
- status (idle/busy/offline/error)
- Statistics: total_tasks, success_tasks, fail_tasks, avg_latency_ms, total_tokens

### Task
- title, description, priority, status, type
- assigned_agent_id, parent_task_id, workflow_id
- input_data, output_data, error_message
- token_usage, latency_ms, retry_count, max_retries

### Workflow
- name, description, steps (JSON array)
- trigger (manual/cron/webhook/event)
- current_step, run_count

### Message
- sender_id (nullable for user), receiver_id (nullable for broadcast)
- channel, type, content
- For user conversations: sender_id=null, receiver_id=agent_id

## LLM Integration Pattern

```python
def llm_respond():
    with app.app_context():
        # Get recent context
        recent = Message.query.filter_by(channel=channel).limit(10).all()
        context = "\n".join([f"{name}: {m.content}" for m in recent])
        
        # Call hermes CLI
        prompt = f"You are {agent.alias}, role: {agent.role}\n\n{context}\n\nReply concisely."
        result = subprocess.run(['hermes', '-z', prompt], capture_output=True, text=True, timeout=30)
        
        # Save response
        reply = Message(sender_id=agent.id, receiver_id=None, content=result.stdout)
        db.session.add(reply)
        db.session.commit()

threading.Thread(target=llm_respond, daemon=True).start()
```

## File Upload

```python
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.route('/api/upload', methods=['POST'])
def upload_file():
    f = request.files['file']
    safe_name = f"{int(time.time()*1000)}_{f.filename}"
    path = os.path.join(UPLOAD_DIR, safe_name)
    f.save(path)
    return jsonify({'ok': True, 'url': f'/uploads/{safe_name}'})
```

## Chat UI Features
- Custom upward-opening dropdown for agent selection
- File upload button + Ctrl+V paste support
- Auto-resizing textarea
- Waiting state with polling for LLM response
- Message ID tracking to find correct reply

## Key Pitfalls
1. **SQLite schema migration**: Use raw SQL for ALTER TABLE, not ORM
2. **Agent deletion**: Nullify foreign keys before deleting (messages, tasks)
3. **Translation**: Don't bulk replace - will corrupt variable names (see web-ui-localization skill)
4. **LLM timeout**: Set reasonable timeout (30s), show waiting state
5. **Message filtering**: Only show user-agent conversations, not agent-agent messages
