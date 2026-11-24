"""Strategy components: declared, versioned, registered once and reused everywhere.

Importing this package imports every module in it, so each module's registrations
run. Adding an indicator is one new file in this folder that calls
``register_indicator``; nothing else in the lab needs to change.
"""

import importlib
import pkgutil

from moneylab.components.registry import (
    ComponentDeclaration,
    ParamSpec,
    get_component,
    list_components,
    register_indicator,
)

for _mod in sorted(m.name for m in pkgutil.iter_modules(__path__)):
    if _mod != "registry":
        importlib.import_module(f"{__name__}.{_mod}")

__all__ = [
    "ComponentDeclaration",
    "ParamSpec",
    "get_component",
    "list_components",
    "register_indicator",
]
