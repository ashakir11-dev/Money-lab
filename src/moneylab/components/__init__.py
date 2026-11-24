"""Strategy components: declared, versioned, registered once and reused everywhere.

Importing this package registers the built-in components.
"""

from moneylab.components import builtin as _builtin  # noqa: F401  (registers components)
from moneylab.components.registry import (
    ComponentDeclaration,
    ParamSpec,
    get_component,
    list_components,
    register_indicator,
)

__all__ = [
    "ComponentDeclaration",
    "ParamSpec",
    "get_component",
    "list_components",
    "register_indicator",
]
