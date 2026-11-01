"""``amca args`` — edit the per-project default arguments for a plugin.

Each enabled plugin can have ``<root>/.amca/args/<plugin>.args``: one argument
per line, ``#`` comments ignored. Those lines are prepended to whatever the
user types after the plugin's marker, so a project can pin ``--buildtype=debug``
without anyone having to remember it.
"""

from __future__ import annotations

import argparse

from ...core import proc
from ...core.context import amcaContext
from ...plugins.argfiles import write_template
from ...plugins.registry import Registry
from ..complete import PLUGIN, steer
from ..prompt import select

__all__ = ["register"]



def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("args", aliases=["a"],
                            help="edit per-project default arguments for a plugin")
    steer(parser.add_argument("plugin", nargs="?", help="plugin name (omit to pick from a list)"),
          PLUGIN)
    parser.add_argument("--show", action="store_true", help="print instead of opening an editor")
    parser.set_defaults(handler=handle)




def handle(ctx: amcaContext, args: argparse.Namespace, _: dict[str, list[str]]) -> int:
    root = ctx.find_root()
    if root is None:
        print("no amca root here — create one with `amca new`")
        return 1

    registry = Registry(ctx)
    available = registry.names()
    if not available:
        print(f"no plugins installed in {ctx.plugins_dir}")
        return 1

    name = args.plugin
    if name is None:
        name = select("Which plugin's arguments?", available)
        if name is None:
            return 1
    if name not in available:
        print(f"amca: no plugin named {name!r} (installed: {', '.join(available)})")
        return 2

    root.args_dir.mkdir(parents=True, exist_ok=True)
    target = root.args_dir / f"{name}.args"

    if args.show:
        if not target.exists():
            print(f"(no argument file at {target})")
            return 0
        print(target.read_text(encoding="utf-8"), end="")
        return 0

    if not target.exists():
        write_template(target, name, ctx.config.get_str("plugins.marker_prefix"))

    return proc.call([ctx.editor, str(target)])
