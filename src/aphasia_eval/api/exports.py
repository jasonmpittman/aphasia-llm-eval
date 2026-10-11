"""Download complete run records in reproducible, human-friendly formats."""

from __future__ import annotations

from io import BytesIO

from flask import Blueprint, request, send_file

from aphasia_eval.extensions import get_database
from aphasia_eval.services.export import export_filename, load_run_export, render_export

from .common import json_error

blueprint = Blueprint("exports_api", __name__)
SUPPORTED_FORMATS = frozenset({"csv", "json", "xlsx", "pdf"})


@blueprint.get("/api/runs/<run_id>/export")
def export_run(run_id: str):
    """Render a persisted run as the requested attachment format."""

    export_format = request.args.get("format", "").lower()
    if export_format not in SUPPORTED_FORMATS:
        return json_error(
            "invalid_format",
            "format must be one of: csv, json, xlsx, pdf.",
            400,
        )
    with get_database().session() as session:
        data = load_run_export(session, run_id)
    if data is None:
        return json_error("not_found", "Run not found.", 404)
    rendered = render_export(data, export_format)
    response = send_file(
        BytesIO(rendered.content),
        mimetype=rendered.mimetype,
        as_attachment=True,
        download_name=export_filename(data, rendered.extension),
        max_age=0,
    )
    response.headers["Cache-Control"] = "no-store"
    return response
