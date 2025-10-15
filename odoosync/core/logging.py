import logging
import sys
from typing import Optional

_LOGGER_NAME = "odoosync"


def _configure_root_logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        handler.setLevel(logging.INFO)
        logger.setLevel(logging.INFO)
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
