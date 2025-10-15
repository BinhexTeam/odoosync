"""Legacy compatibility layer for ModelSyncer.

The original implementation has been split across sub-packages. This module merely
re-exports the public API so existing imports keep working.
"""

from __future__ import annotations

import netrc as _netrc_module
import odoorpc as _odoorpc_module
import os as _os_module
import warnings

from .connection import OdooInstance
from .core import SyncException, get_logger, set_level
from .models import DEFAULT_EXCLUDED_FIELDS, INTERNAL_RUNTIME_FIELDS, OdooModel
from .sync import ModelSyncer

warnings.warn(
    "`odoosync.ModelSyncer` is deprecated. Import from `odoosync.sync` or the"
    " corresponding sub-packages instead.",
    DeprecationWarning,
    stacklevel=2,
)

logger = get_logger()

# Backward compatibility: expose commonly monkey-patched stdlib modules
netrc = _netrc_module
odoorpc = _odoorpc_module
os = _os_module

__all__ = [
    "ModelSyncer",
    "OdooInstance",
    "OdooModel",
    "SyncException",
    "DEFAULT_EXCLUDED_FIELDS",
    "INTERNAL_RUNTIME_FIELDS",
    "get_logger",
    "set_level",
    "logger",
    "netrc",
    "odoorpc",
    "os",
]
