import logging
import sys
from typing import Iterable, Optional, Set

_LOGGER_NAME = "odoosync"
_MUTED_LEVELS: Set[str] = set()
_MUTE_FILTER: Optional[logging.Filter] = None


def _configure_root_logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        handler.setLevel(logging.INFO)
        logger.setLevel(logging.INFO)
    if _MUTE_FILTER and _MUTE_FILTER not in logger.filters:
        logger.addFilter(_MUTE_FILTER)
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    root_logger = _configure_root_logger()
    if not name:
        return root_logger
    child_name = name if not name.startswith(_LOGGER_NAME) else name[len(_LOGGER_NAME) + 1 :]
    return root_logger.getChild(child_name.lstrip('.'))


def set_level(level: int) -> None:
    logger = _configure_root_logger()
    logger.setLevel(level)
    for handler in logger.handlers:
        handler.setLevel(level)


class _LevelMuteFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        if getattr(record, "odoosync_progress", False):
            return True
        return record.levelname not in _MUTED_LEVELS


def set_muted_levels(levels: Iterable[str]) -> None:
    global _MUTE_FILTER
    _MUTED_LEVELS.clear()
    for level in levels or []:
        if isinstance(level, str) and level.strip():
            _MUTED_LEVELS.add(level.strip().upper())
    if _MUTED_LEVELS:
        if _MUTE_FILTER is None:
            _MUTE_FILTER = _LevelMuteFilter()
    else:
        _MUTE_FILTER = None
    logger = _configure_root_logger()
    # Remove any existing mute filters before re-adding
    logger.filters = [f for f in logger.filters if not isinstance(f, _LevelMuteFilter)]
    if _MUTE_FILTER:
        logger.addFilter(_MUTE_FILTER)
