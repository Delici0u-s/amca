"""``amca`` — run the plugins that apply to the current directory."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from ..config.schema import SCHEMA
from ..config.store import ConfigError
from ..core.context import amcaContext
from ..plugins.markers import UnknownMarker, split_argv
from ..plugins.registry import Registry
from . import common
from .commands import args_edit, completions_cmd, config_cmd, doctor, root_cmd, run_cmd
from .complete import offers_markers

__all__ = ["build_parser", "main"]

_GLOB_CHARS = "*?[]{}~"


def _epilog(prefix: str, markers: list[str]) -> str:
    """Build the help epilog from the *effective* configuration.

    The previous version hardcoded '---' and a made-up example. After
    `config set plugins.marker_prefix ?` the help still told you to type
    `---meson`, which is the one thing that no longer worked.
    """
    listed = "  ".join(markers) if markers else "(no plugins installed)"
    example = (
        f"amca {markers[0]} -s" if markers else f"amca {prefix}<plugin> <plugin args>"
    )
    warning = ""
    if any(char in prefix for char in _GLOB_CHARS):
        warning = (
            f"\n  WARNING: the marker prefix {prefix!r} contains shell glob characters.\n"
            f"  You will have to quote every marker (amca '{prefix}meson'), because the\n"
            f"  shell expands it before amca sees it. Consider a prefix like --- or +++.\n"
        )
    return f"""\
plugin passthrough:
  Arguments after a plugin marker go to that plugin, up to the next marker.

      {example}

  Current marker prefix: {prefix!r}
  Markers here:          {listed}
{warning}
  Markers are the prefix plus the plugin's folder name, with underscores
  written as dashes. An unknown marker is an error, not a silently discarded
  argument. A bare -- stops marker parsing, for values starting with the prefix:

      amca config set plugins.marker_prefix -- +++

troubleshooting:
  amca config list --origin   show every setting and which layer it came from
  amca plugins --markers      the exact markers this directory accepts
  amca doctor                 check tools, paths, and plugin health
"""


def build_parser(
    prefix: str = "---", markers: list[str] | None = None
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amca",
        description="Run the build/script pipeline that fits the current directory.",
        epilog=_epilog(prefix, markers or []),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    offers_markers(parser)
    common.add_global_flags(parser, marker_prefix=prefix)
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would run without running it")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    run_parser = sub.add_parser("run", aliases=["r"],
                                help="run applicable plugins (default)")
    run_parser.set_defaults(handler=run_cmd.handle)
    offers_markers(run_parser)

    root_cmd.register(sub)
    args_edit.register(sub)
    config_cmd.register(sub)
    doctor.register(sub)

    plugins_parser = sub.add_parser("plugins", help="list plugins and their markers")
    # Machine-readable forms, for shell completion. One name per line, no
    # decoration, no headers, and never an error exit — a completion function
    # that has to parse a table is a completion function that breaks.
    plugins_parser.add_argument("--names", action="store_true",
                                help="print installed plugin names, one per line")
    plugins_parser.add_argument("--markers", action="store_true",
                                help="print plugin markers, one per line")
    plugins_parser.add_argument("--enabled-only", action="store_true",
                                help="restrict --names/--markers to enabled plugins")
    plugins_parser.set_defaults(handler=_handle_plugins)

    completions_cmd.register(sub)

    return parser


def _handle_plugins(ctx: amcaContext, args: argparse.Namespace, _: dict[str, list[str]]) -> int:
    registry = Registry(ctx)
    prefix = ctx.config.get_str("plugins.marker_prefix")
    entries = registry.entries

    if getattr(args, "names", False) or getattr(args, "markers", False):
        wanted = [e for e in entries if e.enabled or not args.enabled_only]
        for entry in wanted:
            if args.markers:
                print(f"{prefix}{entry.name.replace('_', '-')}")
            else:
                print(entry.name)
        return 0

    if not entries:
        print(f"No plugins installed in {ctx.plugins_dir}")
        print("Install one with:  amcapl install meson")
        return 0
    width = max(len(entry.name) for entry in entries)
    for entry in entries:
        state = "enabled " if entry.enabled else "disabled"
        print(f"  {state}  {entry.name:<{width}}  {prefix}{entry.name.replace('_', '-')}")
    missing = registry.missing_enabled()
    if missing:
        print(f"\n  enabled but not installed: {', '.join(missing)}")
    return 0


def _main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)

    # Tab completion: called by the shell glue on every tab press. Before
    # anything else, so it never logs, prompts or runs the first-run hook.
    if raw[:1] == ["__complete"]:
        from .complete import main as complete_main

        return complete_main(raw[1:])

    # The marker prefix must be known before argv can be split, but it lives in
    # config, which lives behind --config-dir. A hand-rolled scan rather than a
    # throwaway argparse: argparse assigns `--` a special meaning and would
    # print its own bare usage block on a malformed flag, before amca has even
    # decided what the command is.
    pre = common.pre_scan(raw)

    try:
        ctx = amcaContext(config_dir_override=pre.get("config-dir"))
    except ConfigError as exc:
        print(f"amca: {exc}", file=sys.stderr)
        return 2

    prefix = pre.get("marker-prefix") or ctx.config.get_str("plugins.marker_prefix")
    try:
        SCHEMA["plugins.marker_prefix"].check(prefix)
    except ConfigError as exc:
        print(f"amca: {exc}", file=sys.stderr)
        return 2
    registry_names = [entry.name for entry in Registry(ctx).entries]

    try:
        split = split_argv(raw, registry_names, prefix)
    except UnknownMarker as exc:
        print(f"amca: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"amca: {exc}", file=sys.stderr)
        return 2

    parser = build_parser(prefix, [f"{prefix}{n.replace('_', '-')}" for n in registry_names])
    args = parser.parse_args(split.main)

    try:
        common.apply_global_flags(ctx, args)
    except ConfigError as exc:
        print(f"amca: {exc}", file=sys.stderr)
        return 2

    common.first_run_completion_notice(ctx)

    if ctx.config.get_bool("core.greet"):
        print("Hello Master")

    handler = getattr(args, "handler", None)
    if handler is None:
        handler = run_cmd.handle
    return int(handler(ctx, args, split.per_plugin) or 0)


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point. Never lets a bare traceback reach the user."""
    return common.run_entry(_main, argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
