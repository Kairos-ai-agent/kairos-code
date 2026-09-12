"""Gate Report API — the receipt for a loop, served over HTTP.

    GET /api/projects/{project_id}/gate-report
        ?format=html|md|json   (default html — one self-contained file)
        &lang=en|zh            (default en; the HTML also carries both)
        &session=<loop session>
        &download=1            (Content-Disposition: attachment)

The report is generated on demand from what the loop already persisted
(``loop_rounds`` / ``loop_checkpoints`` / the cost ledger), so there is no
cache to invalidate: open it in a tab to *see* the gate, or add ``download=1``
to attach the HTML to a PR / a Slack thread.

Format notes:
  * ``html`` is a single self-contained document (inline CSS, no CDN, no
    framework) with an in-place EN/中文 toggle — safe to attach to a PR.
  * ``md`` is meant to be pasted into a PR description.
  * ``json`` returns the same payload as ``kairos gate report --format json``
    (stable keys) so CI can assert on ``state`` / ``first_pass`` / ``cost_usd``.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from kairos import gate_report as gate_mod

logger = logging.getLogger(__name__)
router = APIRouter()

LANGS = ("en", "zh")
FORMATS = ("html", "md", "json")


@router.get("/{project_id}/gate-report")
async def get_gate_report(
    project_id: str,
    format: str = Query("html", description="html | md | json"),
    lang: str = Query("en", description="en | zh"),
    session: Optional[str] = Query(None, description="restrict to one loop session"),
    download: bool = Query(False, description="send as a file attachment"),
):
    """Render the Gate Report for one project."""
    fmt = (format or "html").lower()
    if fmt not in FORMATS:
        raise HTTPException(status_code=400,
                            detail=f"format must be one of {FORMATS}, got {format!r}")
    language = (lang or "en").lower()
    if language not in LANGS:
        language = "en"  # report strings fall back to English rather than 400

    project = gate_mod.find_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project not found: {project_id}")

    report = gate_mod.collect(str(project["id"]), session_id=session)

    if fmt == "json":
        return JSONResponse(report.to_dict())

    if fmt == "md":
        payload, media, ext = report.to_markdown(language), "text/markdown; charset=utf-8", "md"
    else:
        payload, media, ext = report.to_html(language), "text/html; charset=utf-8", "html"

    headers = {}
    if download:
        headers["Content-Disposition"] = (
            f'attachment; filename="gate-report-{report.project_id[:8]}.{ext}"'
        )
    return Response(content=payload, media_type=media, headers=headers)
