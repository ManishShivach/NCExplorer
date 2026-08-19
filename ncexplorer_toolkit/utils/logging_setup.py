# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""One-time process-wide logging configuration.

Everything in NCExplorer logs through the standard :mod:`logging` module and
nothing configures handlers on its own — that is the job of
:func:`configure_logging`, called once from ``main.py`` before the main window
exists.

The design is shaped by three things learned the hard way:

* **Configuration happens exactly once.** An earlier version called
  ``logging.basicConfig()`` from ``QMainWindow.__init__``, which meant handler
  setup was tangled up with widget construction and silently did nothing on the
  second window. The ``_configured`` guard below makes re-entry a no-op instead.
* **Logging must never break startup.** The rotating file handler writes into
  the platform log directory, and a read-only home, a full disk or a sandbox
  can all make that impossible. Every failure path degrades to console-only.
* **DEBUG is for the developer, not the user.** The file handler always records
  DEBUG; the console stays at INFO unless ``NCEXPLORER_DEBUG=1`` is set. That
  environment variable is the switch that makes the generated engine commands
  (see ``core/nc_integration.py``) visible.

Redaction
---------

No log record may name the external processing binary, at any level and on any
sink. :class:`RedactionFilter` is what enforces that, and it is installed on
*handlers* rather than on a logger for a reason worth stating: a filter on a
``Logger`` only sees records logged directly to it and never sees records that
propagated up from a child logger, so a filter on the root would miss almost
everything the application logs. :func:`install_redaction` is therefore applied
to the console handler, the file handler, and the dock handler in
``gui/log_dock.py``.

Rewriting the message alone is not enough. ``%(name)s`` appears in both formats
below and ``%(funcName)s`` in the file format, so a module whose own name
carries the word would stamp it on every line it logs — the record's ``name``
and ``funcName`` are rewritten too, which is much less disruptive than renaming
modules. Tracebacks are rewritten for the same reason, with the consequence that
a redacted traceback names a source file that does not exist under that name;
the module and line number still locate it, and an unredacted traceback would
defeat the guarantee entirely.

Streamed subprocess output is the case that makes a filter the only workable
answer: those lines are produced by another program and contain the word in text
that appears nowhere in this source, so editing string literals could never be
sufficient.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..__version__ import APP_NAME

logger = logging.getLogger(__name__)

#: What a reference to the processing binary becomes in log output. Two forms,
#: changed here and nowhere else: prose reads naturally in a sentence, and the
#: identifier form keeps the shape of a logger name, a function name or a path
#: inside a traceback, where an embedded space would be nonsense.
ENGINE_PROSE = "processing engine"
ENGINE_TOKEN = "engine"

#: Deliberately not anchored on word boundaries: the word also turns up glued
#: into identifiers (``cdo_operator_catalog``, ``log_last_cdo_command``), and a
#: ``\b``-anchored pattern would walk straight past exactly those.
_ENGINE_PATTERN = re.compile("cdo", re.IGNORECASE)

DEBUG_ENV_VAR = "NCEXPLORER_DEBUG"
LOG_FILENAME = "ncexplorer.log"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3

CONSOLE_FORMAT = "%(levelname)s %(name)s: %(message)s"
FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(funcName)s: %(message)s"

# Third-party loggers that are useless at DEBUG and would otherwise bury the
# application's own records — matplotlib's font manager alone emits thousands of
# lines per session.
NOISY_LOGGERS = (
    "matplotlib",
    "matplotlib.font_manager",
    "PIL",
    "fiona",
    "rasterio",
    "urllib3",
    "cartopy",
    "contextily",
    "asyncio",
)

_configured = False
_log_file: Path | None = None


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

def redact_text(text: str, replacement: str = ENGINE_PROSE, *,
                sentence_start: bool = True) -> str:
    """Rewrite every mention of the processing binary in ``text``.

    With ``sentence_start``, a match at position 0 is capitalised, so a message
    that opened with the name still opens with a capital instead of reading as a
    sentence that forgot to start. Identifiers pass ``False``: a logger name is
    not a sentence, and ``Engine`` where ``engine`` belongs looks like a typo.
    """
    def _replace(match: re.Match) -> str:
        if sentence_start and match.start() == 0:
            return replacement[:1].upper() + replacement[1:]
        return replacement

    return _ENGINE_PATTERN.sub(_replace, text)


def _redact_value(value: object, replacement: str, *, sentence_start: bool) -> object:
    """Redact one logging argument, leaving anything unaffected untouched.

    Non-strings are only converted when their rendering actually carries the
    word — an int or a float is handed back as itself, so a ``%d`` in the format
    string still has a number to work with.
    """
    if isinstance(value, str):
        return redact_text(value, replacement, sentence_start=sentence_start)
    rendered = str(value)
    if _ENGINE_PATTERN.search(rendered):
        return redact_text(rendered, replacement, sentence_start=sentence_start)
    return value


class RedactionFilter(logging.Filter):
    """Rewrites records so no sink can print the processing binary's name.

    Installed on handlers, never on a logger — see the module docstring for why
    that distinction decides whether this works at all.

    Mutating the record is intentional: handlers share one record, so the first
    handler to filter it redacts it for the rest too. Every rewrite here is
    idempotent, which is what makes being run several times harmless.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        elif _ENGINE_PATTERN.search(str(record.msg)):
            # A non-string message is rendered with str() at format time, and
            # would carry the word straight through. Substituting the rendered
            # text keeps any %-arguments applicable.
            record.msg = redact_text(str(record.msg))

        # An argument only opens the rendered message when the format string
        # leads with its placeholder — which is exactly the shape the streamed
        # subprocess output is logged in ("%s", line). Anywhere else the
        # argument lands mid-sentence and must not be capitalised.
        leading = isinstance(record.msg, str) and record.msg.startswith("%")
        if isinstance(record.args, dict):
            record.args = {
                key: _redact_value(value, ENGINE_PROSE, sentence_start=leading)
                for key, value in record.args.items()
            }
        elif isinstance(record.args, tuple):
            record.args = tuple(
                _redact_value(value, ENGINE_PROSE, sentence_start=leading and index == 0)
                for index, value in enumerate(record.args)
            )

        # Both formats print %(name)s and the file format prints %(funcName)s,
        # so a module or function whose own name carries the word would leak on
        # every line. The identifier form keeps these looking like identifiers.
        record.name = redact_text(record.name, ENGINE_TOKEN, sentence_start=False)
        record.funcName = redact_text(record.funcName or "", ENGINE_TOKEN,
                                      sentence_start=False)
        record.module = redact_text(record.module or "", ENGINE_TOKEN,
                                    sentence_start=False)
        record.filename = redact_text(record.filename or "", ENGINE_TOKEN,
                                      sentence_start=False)
        record.pathname = redact_text(record.pathname or "", ENGINE_TOKEN,
                                      sentence_start=False)

        # Formatter.format() appends exc_text and stack_info verbatim, and
        # caches exc_text once computed. Formatting the exception here — with
        # logging's own formatter — puts a redacted version in that cache before
        # any handler's formatter gets there.
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_text(record.exc_text, ENGINE_TOKEN,
                                          sentence_start=False)
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info, ENGINE_TOKEN,
                                            sentence_start=False)

        return True


def install_redaction(handler: logging.Handler) -> logging.Handler:
    """Attach the redaction filter to one handler, once. Returns the handler."""
    if not any(isinstance(existing, RedactionFilter) for existing in handler.filters):
        handler.addFilter(RedactionFilter())
    return handler


def log_directory() -> Path:
    """Platform-conventional directory for this application's log files.

    Nothing is created here; see :func:`configure_logging`.
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / APP_NAME
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME / "Logs"
    # Linux and the other Unixes: logs are state, not cache and not config.
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / APP_NAME.lower()


def log_file_path() -> Path | None:
    """Path the file handler is writing to, or None if there is no file handler."""
    return _log_file


def debug_console_requested() -> bool:
    """True when the environment asks for a DEBUG-level console."""
    return os.environ.get(DEBUG_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}


def _install_console_handler(root: logging.Logger, level: int) -> None:
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    install_redaction(handler)
    root.addHandler(handler)


def _install_file_handler(root: logging.Logger) -> Path | None:
    """Add the rotating file handler; return its path, or None if unavailable."""
    directory = log_directory()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / LOG_FILENAME
        handler = RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
    except OSError as exc:
        # Console-only is a working application; a crash here is not.
        logger.warning("Log file in %s is unavailable (%s); logging to console only",
                       directory, exc)
        return None

    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(FILE_FORMAT))
    install_redaction(handler)
    root.addHandler(handler)
    return path


def configure_logging(console_level: int | None = None) -> Path | None:
    """Install the application's log handlers. Safe to call more than once.

    ``console_level`` overrides the default (INFO, or DEBUG when
    ``NCEXPLORER_DEBUG`` is set). Returns the log file path, or None when only
    the console handler could be installed.
    """
    global _configured, _log_file
    if _configured:
        return _log_file

    if console_level is None:
        console_level = logging.DEBUG if debug_console_requested() else logging.INFO

    root = logging.getLogger()
    # The root stays at DEBUG so the file handler sees everything; the console
    # handler's own level is what keeps the terminal quiet.
    root.setLevel(logging.DEBUG)

    _install_console_handler(root, console_level)
    _log_file = _install_file_handler(root)

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    _configured = True
    logger.debug("Logging configured: console=%s file=%s",
                 logging.getLevelName(console_level), _log_file)
    return _log_file
