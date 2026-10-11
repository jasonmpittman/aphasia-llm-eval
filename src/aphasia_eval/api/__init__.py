"""API blueprints."""

from .exports import blueprint as exports_blueprint
from .health import blueprint as health_blueprint
from .models import blueprint as models_blueprint
from .runs import blueprint as runs_blueprint
from .tasks import blueprint as tasks_blueprint

api_blueprints = (
    health_blueprint,
    models_blueprint,
    tasks_blueprint,
    runs_blueprint,
    exports_blueprint,
)

__all__ = ["api_blueprints"]
