"""HTTP API for the research lab: FastAPI over ``moneylab.service.Lab``.

The browser dashboard (Next.js static export, ``frontend/``) talks only to this API.
All trading calculations stay in Python; the API returns their results.
"""

from moneylab.api.app import create_app

__all__ = ["create_app"]
