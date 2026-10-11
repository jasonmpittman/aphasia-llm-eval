"""Server-rendered shells for the progressively enhanced interface."""

from flask import Blueprint, current_app, render_template

blueprint = Blueprint("web", __name__)


@blueprint.get("/")
def index() -> str:
    """Render model and task selection."""

    return render_template(
        "index.html",
        page_name="select",
        confirmation_threshold=current_app.config["RUN_CONFIRMATION_THRESHOLD"],
    )


@blueprint.get("/runs/<run_id>")
def run_page(run_id: str) -> str:
    """Render the live run and result workspace."""

    return render_template("run.html", page_name="run", run_id=run_id)


@blueprint.get("/history")
def history() -> str:
    """Render locally persisted run history."""

    return render_template("history.html", page_name="history")
