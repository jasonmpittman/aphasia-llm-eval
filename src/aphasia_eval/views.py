"""Server-rendered pages."""

from flask import Blueprint, render_template

blueprint = Blueprint("web", __name__)


@blueprint.get("/")
def index() -> str:
    """Render the Milestone 0 placeholder page."""

    return render_template("index.html")
