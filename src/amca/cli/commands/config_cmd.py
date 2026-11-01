"""``amca config`` — inspect and change settings without hand-editing JSON.

``amca config list --origin`` is the direct answer to "I changed a value and
nothing happened": it prints every key, its effective value, and which layer
that value came from. If you edited the file but an env var or a stale session
flag is winning, or the key you typed is not a real key, this says so.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from ...config.schema import SCHEMA
from ...config.store import ConfigError
from ...core import paths, proc
from ...core.context import amcaContext
from ..complete import CONFIG_KEY, CONFIG_VALUE, DIRS, steer
from ..prompt import confirm

__all__ = ["register"]

_ORIGIN_NOTE = {
    "default": "built-in default",
    "file": "config file",
    "env": "environment variable",
    "session": "command-line flag",
}


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("config", help="inspect or change settings")
    inner = parser.add_subparsers(dest="config_command", metavar="<action>")

    listing = inner.add_parser("list", aliases=["ls"], help="show every setting")
    listing.add_argument("--origin", action="store_true",
                         help="also show which layer each value came from")
    listing.add_argument("--changed", action="store_true",
                         help="only settings that differ from the default")
    listing.add_argument("--json", dest="as_json", action="store_true")
    listing.add_argument("--keys", action="store_true",
                         help="print key names only, one per line (for shell completion)")
    listing.set_defaults(handler=_list)

    getter = inner.add_parser("get", help="print one value")
    steer(getter.add_argument("key"), CONFIG_KEY)
    getter.set_defaults(handler=_get)

    setter = inner.add_parser("set", help="write one value to the config file")
    steer(setter.add_argument("key"), CONFIG_KEY)
    steer(setter.add_argument("value"), CONFIG_VALUE)
    setter.set_defaults(handler=_set)

    unsetter = inner.add_parser("unset", help="remove a value, reverting to the default")
    steer(unsetter.add_argument("key"), CONFIG_KEY)
    unsetter.set_defaults(handler=_unset)

    path = inner.add_parser("path", help="print the config file location")
    path.set_defaults(handler=_path)

    edit = inner.add_parser("edit", help="open the config file in your editor")
    edit.set_defaults(handler=_edit)

    describe = inner.add_parser("describe", help="explain one setting")
    steer(describe.add_argument("key", nargs="?"), CONFIG_KEY)
    describe.set_defaults(handler=_describe)

    migrate = inner.add_parser("migrate", help="import settings from an amca 2.x install")
    steer(migrate.add_argument("--source", default=None, metavar="DIR",
                               help="path to the old amca_config directory"), DIRS)
    migrate.add_argument("--dry-run", action="store_true")
    migrate.add_argument("-y", "--yes", action="store_true")
    migrate.set_defaults(handler=_migrate)

    parser.set_defaults(handler=_list, origin=True, changed=False, as_json=False)


def _list(ctx: amcaContext, args: argparse.Namespace, _: dict[str, list[str]]) -> int:
    if getattr(args, "keys", False):
        for key in SCHEMA:
            print(key)
        return 0

    rows = list(ctx.config.all())
    if getattr(args, "changed", False):
        rows = [row for row in rows if row.origin != "default"]

    if getattr(args, "as_json", False):
        print(json.dumps(
            {row.key: {"value": row.value, "origin": row.origin} for row in rows},
            indent=2, sort_keys=True,
        ))
        return 0

    if not rows:
        print("every setting is at its default")
        return 0

    width = max(len(row.key) for row in rows)
    show_origin = getattr(args, "origin", False)
    for row in rows:
        rendered = json.dumps(row.value) if not isinstance(row.value, str) else row.value
        line = f"  {row.key:<{width}}  {rendered}"
        if show_origin:
            line += f"    [{row.origin}]"
        print(line)

    if show_origin:
        print(f"\n  config file: {ctx.config.path}"
              f"{'' if ctx.config.path.exists() else '  (does not exist yet)'}")
        # The single most confusing failure is editing the wrong file because
        # an environment variable is pointing amca somewhere else.
        env_dir = os.environ.get(paths.ENV_CONFIG_DIR)
        if env_dir:
            print(f"  note: {paths.ENV_CONFIG_DIR}={env_dir} is redirecting the config "
                  f"directory; your default at {paths.default_config_dir()} is not in use")
    if ctx.config.load_error:
        print(f"\n  warning: {ctx.config.load_error}")
    return 0


def _get(ctx: amcaContext, args: argparse.Namespace, _: dict[str, list[str]]) -> int:
    try:
        resolved = ctx.config.resolve(args.key)
    except KeyError:
        return _unknown_key(args.key)
    value = resolved.value
    print(value if isinstance(value, str) else json.dumps(value))
    return 0


def _set(ctx: amcaContext, args: argparse.Namespace, _: dict[str, list[str]]) -> int:
    try:
        ctx.config.set_persistent(args.key, args.value)
    except KeyError:
        return _unknown_key(args.key)
    except ConfigError as exc:
        print(f"amca: {exc}")
        return 2
    ctx.config.save()

    resolved = ctx.config.resolve(args.key)
    print(f"{args.key} = {resolved.value!r}")

    if args.key == "plugins.marker_prefix":
        globby = [c for c in "*?[]{}~" if c in str(resolved.value)]
        if globby:
            print(
                f"  warning: {''.join(globby)} is a shell glob character, so your shell "
                f"expands the marker before amca sees it.\n"
                f"  You would have to quote every marker: amca '{resolved.value}meson'\n"
                f"  Something like ---, +++ or @@ avoids this."
            )
    if resolved.origin != "file":
        # Honest about the thing that used to be invisible: writing the file
        # does nothing if a higher layer is winning.
        print(
            f"  note: the effective value still comes from the "
            f"{_ORIGIN_NOTE[resolved.origin]}, which overrides the config file"
        )
    return 0


def _unset(ctx: amcaContext, args: argparse.Namespace, _: dict[str, list[str]]) -> int:
    try:
        existed = ctx.config.unset_persistent(args.key)
    except KeyError:
        return _unknown_key(args.key)
    ctx.config.save()
    print(f"{args.key}: {'removed' if existed else 'was not set'}"
          f" (now {ctx.config.get(args.key)!r})")
    return 0


def _path(ctx: amcaContext, args: argparse.Namespace, _: dict[str, list[str]]) -> int:
    print(ctx.config.path)
    if not ctx.config.path.exists():
        print("  (does not exist yet — it is created on the first `config set`, "
              "or you can create it by hand)", file=sys.stderr)
    env_dir = os.environ.get(paths.ENV_CONFIG_DIR)
    if env_dir:
        print(f"  (via {paths.ENV_CONFIG_DIR}={env_dir})", file=sys.stderr)
    return 0


def _edit(ctx: amcaContext, args: argparse.Namespace, _: dict[str, list[str]]) -> int:
    if not ctx.config.path.exists():
        ctx.config.path.parent.mkdir(parents=True, exist_ok=True)
        ctx.config.path.write_text("{}\n", encoding="utf-8")
    return proc.call([ctx.editor, str(ctx.config.path)])


def _describe(ctx: amcaContext, args: argparse.Namespace, _: dict[str, list[str]]) -> int:
    keys = [args.key] if args.key else list(SCHEMA)
    if args.key and args.key not in SCHEMA:
        return _unknown_key(args.key)
    from ...config.schema import env_name_for

    for key in keys:
        field = SCHEMA[key]
        print(f"{key}")
        print(f"  {field.help}")
        print(f"  type    : {field.kind}"
              + (f" ({'|'.join(field.choices)})" if field.choices else ""))
        print(f"  default : {field.default!r}")
        print(f"  env var : {env_name_for(key)}")
        if field.managed_by:
            print(f"  managed : normally changed via `{field.managed_by}`")
        print()
    return 0


def _unknown_key(key: str) -> int:
    import difflib

    close = difflib.get_close_matches(key, list(SCHEMA), n=3, cutoff=0.5)
    print(f"amca: unknown config key {key!r}")
    if close:
        print(f"  did you mean: {', '.join(close)}?")
    print("  see all keys with:  amca config list")
    return 2


def _migrate(ctx: amcaContext, args: argparse.Namespace, _: dict[str, list[str]]) -> int:
    from pathlib import Path

    from ...config.migrate import find_legacy_config, plan_migration
    from ...core import paths

    search = [Path(args.source).expanduser()] if args.source else [
        ctx.config_dir,
        paths.default_config_dir(),
        Path.home() / ".config" / "amca",
        Path.home() / ".amca",
    ]
    legacy = find_legacy_config(search)
    if legacy is None:
        print("no amca 2.x configuration found")
        print("  looked in: " + ", ".join(str(p) for p in search))
        print("  point at it explicitly with --source DIR")
        return 1

    plan = plan_migration(legacy)
    print(f"found 2.x config at {plan.source}\n")

    if not plan.changes:
        print("nothing to import — every old value already matches the 3.x default")
    else:
        print("would set:")
        for key, value in sorted(plan.changes.items()):
            print(f"  {key} = {value!r}")
    for note in plan.dropped:
        print(f"\n  dropped  {note}")
    for problem in plan.problems:
        print(f"\n  skipped  {problem}")

    if not plan.changes:
        return 0
    if args.dry_run:
        print("\n(dry run — nothing written)")
        return 0
    if not args.yes and not confirm("\nApply these changes?", default=False):
        print("cancelled")
        return 1

    plan.apply(ctx.config)
    print(f"\nwritten to {ctx.config.path}")
    print("check the result with:  amca config list --origin")
    return 0
