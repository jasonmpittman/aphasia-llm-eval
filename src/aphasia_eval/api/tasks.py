"""Task discovery API backed by validated task and gold files."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from aphasia_eval.comparison import get_comparator
from aphasia_eval.services.task_loader import load_tasks

blueprint = Blueprint("tasks_api", __name__)


@blueprint.get("/api/tasks")
def list_tasks():
    """Return enabled and disabled tasks with actionable validation errors."""

    results = load_tasks(current_app.config["TASKS_DIR"], current_app.config["GOLD_TASKS_DIR"])
    tasks = []
    for result in results:
        task = result.task
        missing = any("gold file not found" in error for error in result.errors)
        item = {
            "task_id": result.task_id,
            "enabled": result.enabled,
            "gold_status": "found" if result.enabled else "missing" if missing else "invalid",
            "item_count": len(task.gold.items) if task is not None and task.gold else 0,
            "errors": list(result.errors),
        }
        if task is not None:
            item.update(
                {
                    "title": task.config["title"],
                    "category": task.config["category"],
                    "version": task.config["version"],
                    "description": task.config["description"],
                    "headline": task.config["headline"],
                    "view_descriptor": get_comparator(
                        task.config["comparison"]["type"]
                    ).view_descriptor(task.config),
                }
            )
        tasks.append(item)
    return jsonify({"tasks": tasks})
