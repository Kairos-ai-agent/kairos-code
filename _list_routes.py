"""Show all registered routes in api.app — using FastAPI's openapi schema."""
from api.app import app
schema = app.openapi()
for path, methods in schema.get("paths", {}).items():
    if "/ask" in path or "/plan" in path:
        for m in methods:
            print(f"  {m.upper():7}  {path}")
