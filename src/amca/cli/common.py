"""Shared CLI plumbing for both entry points."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from .. import __version__
from ..config.schema import SCHEMA
from ..core.context import amcaContext
from .complete import COMMANDS, DIRS, NOTHING, steer

__all__ = ["add_global_flags", "apply_global_flags", "build_context", "pre_scan", "run_entry"]

#: CLI flag -> config key. Every entry here is applied to the *session* layer
#: only, so a flag can never rewrite the user's config file. Keeping the map
#: as data rather than an if-chain means adding a flag cannot desynchronise
#: from the schema: the key is validated against SCHEMA at import time below.
FLAG_TO_KEY: dict[str, str] = {
    "debug": "core.debug",
    "log_mode": "log.mode",
    "log_level": "log.level",
    "log_prefix": "log.prefix",
    "plugin_dir": "plugins.dir",
    "depth": "root.search_depth",
    "editor": "core.editor",
    "marker_prefix": "plugins.marker_prefix",
    "on_error": "plugins.on_error",
    "on_missing": "plugins.on_missing",
}

for _key in FLAG_TO_KEY.values():
    assert _key in SCHEMA, f"FLAG_TO_KEY references unknown config key {_key}"


def add_global_flags(parser: argparse.ArgumentParser, *, marker_prefix: str = "---") -> None:
    group = parser.add_argument_group(
        "global options",
        "Session-only overrides. These never modify the config file — "
        "use `amca config set` for that.",
    )
    group.add_argument("--version", action="version",
                       version=f"%(prog)s {__version__}")
    steer(group.add_argument("--config-dir", metavar="DIR", default=None,
                             help="use a different config directory for this run"), DIRS)
    group.add_argument("--debug", action="store_true", default=None,
                       help="print extra diagnostics and full tracebacks")
    group.add_argument("--log-mode", dest="log_mode", default=None,
                       choices=SCHEMA["log.mode"].choices)
    group.add_argument("--log-level", dest="log_level", default=None,
                       choices=SCHEMA["log.level"].choices)
    group.add_argument("--log-prefix", dest="log_prefix", default=None,
                       choices=SCHEMA["log.prefix"].choices)
    steer(group.add_argument("--plugin-dir", dest="plugin_dir", metavar="DIR", default=None,
                             help="look for installed plugins here instead"), DIRS)
    group.add_argument("--depth", type=int, metavar="N", default=None,
                       help="how far up to search for an amca root")
    steer(group.add_argument("--editor", metavar="CMD", default=None,
                             help="editor command for this run"), COMMANDS)
    steer(group.add_argument("--marker-prefix", dest="marker_prefix", metavar="PREFIX",
                             default=None,
                             help=f"plugin marker prefix for this run (currently {marker_prefix!r})"),
          NOTHING)
    group.add_argument("--on-error", dest="on_error", default=None,
                       choices=SCHEMA["plugins.on_error"].choices)
    group.add_argument("--on-missing", dest="on_missing", default=None,
                       choices=SCHEMA["plugins.on_missing"].choices)


def build_context(args: argparse.Namespace) -> amcaContext:
    return amcaContext(config_dir_override=getattr(args, "config_dir", None))


def apply_global_flags(ctx: amcaContext, args: argparse.Namespace) -> None:
    """Push flag values into the session layer, then re-sync the logger."""
    for attribute, key in FLAG_TO_KEY.items():
        value = getattr(args, attribute, None)
        if value is None or value is False:
            continue
        ctx.config.set_session(key, value)

    ctx.log.set_mode(ctx.config.get_str("log.mode"))
    ctx.log.set_level(ctx.config.get_str("log.level"))
    ctx.log.set_prefix(ctx.config.get_str("log.prefix"))


def run_entry(main_fn: Callable[[Sequence[str] | None], int], argv: Sequence[str] | None = None) -> int:
    """Wrap a main() so the user never sees a bare traceback for known errors."""
    try:
        return main_fn(argv)
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130
    except BrokenPipeError:
        return 0


def pre_scan(argv: Sequence[str]) -> dict[str, str]:
    """Extract the handful of flags needed before argv can be split.

    Only ``--config-dir`` and ``--marker-prefix`` matter here, and only in the
    leading section before any plugin marker. Both ``--flag value`` and
    ``--flag=value`` are accepted. Anything unrecognised is ignored — the real
    parser reports errors, so this stays silent and side-effect free.

    Complexity: O(len(argv)), single pass.
    """
    wanted = ("config-dir", "marker-prefix")
    found: dict[str, str] = {}
    tokens = list(argv)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        for name in wanted:
            flag = f"--{name}"
            if token == flag and index + 1 < len(tokens):
                found[name] = tokens[index + 1]
                index += 1
                break
            if token.startswith(f"{flag}="):
                found[name] = token.split("=", 1)[1]
                break
        index += 1
    return found


def first_run_completion_notice(ctx: amcaContext) -> None:
    """Install shell completion on the first interactive run, and say so once.

    Placed here rather than in amcaContext because it is a property of being a
    CLI, not of the context: importing amca as a library must never write to
    someone's home directory.
    """
    import sys

    from .completions import maybe_auto_install

    if not (sys.stdout.isatty() and sys.stderr.isatty()):
        return
    try:
        message = maybe_auto_install(ctx.state_dir, interactive=True)
    except Exception:
        return
    if message:
        print(message, file=sys.stderr)
