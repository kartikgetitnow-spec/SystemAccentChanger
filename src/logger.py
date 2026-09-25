import inspect
import logging
import sys
import time
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

from loguru import logger

from config import settings

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"


class InterceptHandler(logging.Handler):
    """Redirect stdlib logging records to Loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth -= 1

        logger.opt(depth=depth, exception=record.exc_info).bind(
            module=record.name
        ).log(level, record.getMessage())


def console_format(record: dict) -> str:
    module = record["extra"].get("module", record["name"])
    return (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        f"<cyan>{module}</cyan>:"
        "<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>\n"
    )


def file_format(record: dict) -> str:
    module = record["extra"].get("module", record["name"])
    return (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
        "{level: <8} | "
        f"{module}:"
        "{function}:{line} - "
        "{message}\n"
    )


def setup_logger(
    log_dir: str | Path | None = None,
    log_level: str | None = None,
) -> None:
    """Configure Loguru console + rotating file sinks, intercept stdlib logging."""
    if log_dir:
        target_dir = Path(log_dir)
    else:
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            test_file = LOGS_DIR / ".write_test"
            test_file.touch()
            test_file.unlink(missing_ok=True)
            target_dir = LOGS_DIR
        except (OSError, PermissionError):
            user_log_dir = Path.home() / ".ai_mic" / "logs"
            user_log_dir.mkdir(parents=True, exist_ok=True)
            target_dir = user_log_dir

    target_dir.mkdir(parents=True, exist_ok=True)
    level = (log_level or settings.log_level).upper()

    logger.remove()

    logger.add(sys.stderr, level=level, format=console_format, colorize=True)

    file_pattern = str(target_dir / "ai_mic_{time:YYYY-MM-DD}.log")
    logger.add(
        file_pattern,
        level=level,
        format=file_format,
        rotation="10 MB",
        retention="7 days",
        enqueue=True,
    )

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for name in list(logging.root.manager.loggerDict.keys()):
        lib_logger = logging.getLogger(name)
        lib_logger.handlers = [InterceptHandler()]
        lib_logger.propagate = False


def get_logger(name: str):
    return logger.bind(module=name)


def log_call(_func: Callable | None = None, *, level: str = "DEBUG"):
    """Log entry, exit, duration, and exceptions for sync and async functions."""

    def decorator(fn: Callable) -> Callable:
        func_name = fn.__name__
        module_name = fn.__module__
        bound_logger = logger.bind(module=module_name)

        if inspect.iscoroutinefunction(fn):
            @wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                bound_logger.log(level, f"Entering {func_name}")
                start = time.perf_counter()
                try:
                    result = await fn(*args, **kwargs)
                    bound_logger.log(
                        level,
                        f"Exiting {func_name} (duration: {time.perf_counter() - start:.4f}s)",
                    )
                    return result
                except Exception as exc:
                    bound_logger.error(
                        f"Exception in {func_name} after {time.perf_counter() - start:.4f}s: {exc}"
                    )
                    raise

            return async_wrapper

        @wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            bound_logger.log(level, f"Entering {func_name}")
            start = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
                bound_logger.log(
                    level,
                    f"Exiting {func_name} (duration: {time.perf_counter() - start:.4f}s)",
                )
                return result
            except Exception as exc:
                bound_logger.error(
                    f"Exception in {func_name} after {time.perf_counter() - start:.4f}s: {exc}"
                )
                raise

        return sync_wrapper

    if _func is not None and callable(_func):
        return decorator(_func)
    return decorator


setup_logger()

__all__ = [
    "InterceptHandler",
    "get_logger",
    "log_call",
    "logger",
    "setup_logger",
]