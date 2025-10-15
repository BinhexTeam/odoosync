import importlib
import sys
import warnings


def _import_legacy_module():
    sys.modules.pop("odoosync.ModelSyncer", None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        legacy = importlib.import_module("odoosync.ModelSyncer")
    return legacy, caught


def test_legacy_module_reexports_modern_api():
    legacy, caught_warnings = _import_legacy_module()

    from odoosync.connection import OdooInstance as NewOdooInstance
    from odoosync.core import SyncException, get_logger
    from odoosync.models import (
        DEFAULT_EXCLUDED_FIELDS as NEW_DEFAULT_EXCLUDED_FIELDS,
        INTERNAL_RUNTIME_FIELDS as NEW_INTERNAL_RUNTIME_FIELDS,
        OdooModel as NewOdooModel,
    )
    from odoosync.sync import ModelSyncer as NewModelSyncer

    assert legacy.ModelSyncer is NewModelSyncer
    assert legacy.OdooInstance is NewOdooInstance
    assert legacy.OdooModel is NewOdooModel
    assert legacy.SyncException is SyncException
    assert legacy.DEFAULT_EXCLUDED_FIELDS is NEW_DEFAULT_EXCLUDED_FIELDS
    assert legacy.INTERNAL_RUNTIME_FIELDS is NEW_INTERNAL_RUNTIME_FIELDS
    assert legacy.get_logger is get_logger
    assert legacy.logger is get_logger()
    assert legacy.netrc is sys.modules["netrc"]
    assert legacy.odoorpc is sys.modules["odoorpc"]

    assert any(issubclass(w.category, DeprecationWarning) for w in caught_warnings)


def test_legacy_module_preserves_set_level():
    legacy, _ = _import_legacy_module()
    from odoosync.core import set_level as new_set_level

    assert legacy.set_level is new_set_level
