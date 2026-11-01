"""``amcapl`` — install, enable, and inspect plugins."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from ..config.store import ConfigError
from ..core.context import amcaContext
from ..core.paths import remove_tree
from ..plugins.registry import Registry
from ..plugins.sources import Source, parse_source
from . import common
from .complete import AVAILABLE_PLUGIN, DIRS, PLUGIN, PLUGIN_ARGS, steer
from .prompt import confirm, interactive, multiselect, select

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amcapl",
        description="Manage amca plugins.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "sources:\n"
            "  builtin                            presets shipped with amca (offline)\n"
            "  /path/to/dir                       a local directory of plugins\n"
            "  github:owner/repo@ref:subpath      a GitHub tree (needs `requests`)\n"
            "\n"
            "  configure with:  amca config set plugins.sources builtin,/my/plugins\n"
        ),
    )
    common.add_global_flags(parser)
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    listing = sub.add_parser("list", aliases=["l"], help="show installed plugins")
    listing.add_argument("--available", action="store_true",
                         help="also list what the configured sources offer")
    listing.add_argument("--names", action="store_true",
                         help="print names only, one per line (for shell completion)")
    listing.set_defaults(handler=_list)

    for name, alias, help_text, handler in (
        ("enable", "e", "allow plugin(s) to run", _enable),
        ("disable", "d", "stop plugin(s) from running", _disable),
        ("toggle", "t", "flip plugin(s) between enabled and disabled", _toggle),
    ):
        parser_ = sub.add_parser(name, aliases=[alias], help=help_text)
        steer(parser_.add_argument("plugins", nargs="*", metavar="PLUGIN"), PLUGIN)
        parser_.set_defaults(handler=handler)

    install = sub.add_parser("install", aliases=["i"], help="fetch plugin(s) from a source")
    steer(install.add_argument("plugins", nargs="*", metavar="PLUGIN"), AVAILABLE_PLUGIN)
    steer(install.add_argument("--source", default=None,
                               help="use only this source instead of plugins.sources"), DIRS)
    install.add_argument("--no-enable", action="store_true",
                         help="install without enabling")
    install.add_argument("--force", action="store_true",
                         help="overwrite an already-installed plugin")
    install.set_defaults(handler=_install)

    uninstall = sub.add_parser("uninstall", aliases=["u"], help="delete plugin(s) from disk")
    steer(uninstall.add_argument("plugins", nargs="*", metavar="PLUGIN"), PLUGIN)
    uninstall.add_argument("-y", "--yes", action="store_true")
    uninstall.set_defaults(handler=_uninstall)

    update = sub.add_parser("update", aliases=["up"], help="re-fetch installed plugin(s)")
    steer(update.add_argument("plugins", nargs="*", metavar="PLUGIN",
                              help="omit or pass '*' for everything installed"), PLUGIN)
    update.set_defaults(handler=_update)

    call = sub.add_parser("call", aliases=["c"],
                          help="run one plugin directly, bypassing should_load")
    steer(call.add_argument("plugin", nargs="?"), PLUGIN)
    steer(call.add_argument("args", nargs=argparse.REMAINDER, metavar="ARG"), PLUGIN_ARGS)
    call.set_defaults(handler=_call)

    return parser


# ── Helpers ──────────────────────────────────────────────────────────────────

def _sources(ctx: amcaContext, override: str | None) -> list[Source]:
    specs = [override] if override else [str(s) for s in ctx.config.get_list("plugins.sources")]
    resolved: list[Source] = []
    for spec in specs:
        try:
            resolved.append(parse_source(spec))
        except ValueError as exc:
            ctx.log.warning(f"skipping plugin source: {exc}")
    return resolved


def _pick(ctx: amcaContext, requested: list[str], options: list[str], message: str) -> list[str] | None:
    """Resolve an explicit list, or fall back to an interactive picker."""
    if requested:
        unknown = [name for name in requested if name not in options]
        if unknown:
            print(f"amcapl: not available: {', '.join(unknown)}")
            print(f"  choices: {', '.join(options) or '(none)'}")
            return None
        return requested
    if not options:
        print("nothing to choose from")
        return []
    chosen = multiselect(message, options)
    if chosen is None:
        # Genuine cancellation, or no TTY to ask on.
        if not interactive():
            print(f"amcapl: no plugin named — pass one or more names: {', '.join(options)}")
        return None
    return chosen


def _set_enabled(ctx: amcaContext, names: list[str]) -> None:
    ctx.config.set_persistent("plugins.enabled", sorted(set(names)))
    ctx.config.save()


# ── Commands ─────────────────────────────────────────────────────────────────

def _list(ctx: amcaContext, args: argparse.Namespace, _: dict[str, list[str]]) -> int:
    registry = Registry(ctx)
    entries = registry.entries
    prefix = ctx.config.get_str("plugins.marker_prefix")

    if getattr(args, "names", False):
        names = set(registry.names())
        if getattr(args, "available", False):
            for source in _sources(ctx, getattr(args, "source", None)):
                try:
                    names.update(source.list_plugins())
                except Exception:  # noqa: S112 - a completion helper must never
                    continue       # fail or print noise; an unreachable source
                                   # simply contributes no candidates.
        for name in sorted(names):
            print(name)
        return 0

    print(f"installed in {ctx.plugins_dir}")
    if not entries:
        print("  (none)")
    else:
        width = max(len(entry.name) for entry in entries)
        for entry in entries:
            instance = registry.instantiate(entry)
            state = "enabled " if entry.enabled else "disabled"
            note = entry.error if instance is None else (instance.description or "")
            print(f"  {state}  {entry.name:<{width}}  {prefix}{entry.name.replace('_', '-')}"
                  + (f"   {note}" if note else ""))

    missing = registry.missing_enabled()
    if missing:
        print(f"\nenabled but not installed: {', '.join(missing)}")

    if getattr(args, "available", False):
        print("\navailable from sources")
        installed = set(registry.names())
        for source in _sources(ctx, getattr(args, "source", None)):
            try:
                offered = source.list_plugins()
            except Exception as exc:
                print(f"  {source.label}: unavailable ({exc})")
                continue
            for name in offered:
                mark = "installed" if name in installed else "         "
                print(f"  {mark}  {name}   ({source.label})")
    return 0


def _enable(ctx: amcaContext, args: argparse.Namespace, _: dict[str, list[str]]) -> int:
    registry = Registry(ctx)
    candidates = [name for name in registry.names() if name not in registry.enabled_names()]
    if not args.plugins and not candidates:
        if registry.names():
            print("every installed plugin is already enabled")
        else:
            print(f"no plugins installed in {ctx.plugins_dir}")
            print("  install one with:  amcapl install")
        return 0
    chosen = _pick(ctx, args.plugins, candidates, "Enable which plugins?")
    if chosen is None:
        return 1
    if not chosen:
        print("nothing selected, nothing enabled")
        return 0
    _set_enabled(ctx, registry.enabled_names() + chosen)
    print(f"enabled: {', '.join(chosen)}")
    return 0


def _disable(ctx: amcaContext, args: argparse.Namespace, _: dict[str, list[str]]) -> int:
    registry = Registry(ctx)
    enabled = registry.enabled_names()
    if not args.plugins and not enabled:
        print("no plugins are enabled")
        return 0
    chosen = _pick(ctx, args.plugins, enabled, "Disable which plugins?")
    if chosen is None:
        return 1
    if not chosen:
        print("nothing selected, nothing disabled")
        return 0
    _set_enabled(ctx, [name for name in enabled if name not in chosen])
    print(f"disabled: {', '.join(chosen)}")
    return 0


def _toggle(ctx: amcaContext, args: argparse.Namespace, _: dict[str, list[str]]) -> int:
    registry = Registry(ctx)
    if not args.plugins and not registry.names():
        print(f"no plugins installed in {ctx.plugins_dir}")
        print("  install one with:  amcapl install")
        return 0
    chosen = _pick(ctx, args.plugins, registry.names(), "Toggle which plugins?")
    if chosen is None:
        return 1
    if not chosen:
        print("nothing selected, nothing changed")
        return 0
    enabled = set(registry.enabled_names())
    for name in chosen:
        enabled.symmetric_difference_update({name})
    _set_enabled(ctx, sorted(enabled))
    print(f"enabled: {', '.join(sorted(enabled)) or '(none)'}")
    return 0


def _install(ctx: amcaContext, args: argparse.Namespace, _: dict[str, list[str]]) -> int:
    sources = _sources(ctx, args.source)
    if not sources:
        print("amcapl: no usable plugin sources configured")
        return 2

    offered: dict[str, Source] = {}
    for source in sources:
        try:
            for name in source.list_plugins():
                offered.setdefault(name, source)
        except Exception as exc:
            ctx.log.warning(f"{source.label}: {exc}")

    if not offered:
        print("amcapl: no plugins offered by any configured source")
        return 1

    registry = Registry(ctx)
    installed = set(registry.names())
    # Explicit names are validated against everything on offer; the
    # already-installed filter only narrows the *interactive* picker. Checking
    # explicit names against the narrowed list reported an installed plugin as
    # "not available", which is both wrong and unactionable.
    candidates = sorted(offered) if args.force else sorted(set(offered) - installed)
    chosen = _pick(
        ctx, args.plugins,
        sorted(offered) if args.plugins else (candidates or sorted(offered)),
        "Install which plugins?",
    )
    if chosen is None:
        return 1
    if not chosen:
        print("nothing selected, nothing installed")
        print(f"  available: {', '.join(sorted(offered))}")
        print(f"  or name them directly:  amcapl install {' '.join(sorted(offered))}")
        return 0

    ctx.plugins_dir.mkdir(parents=True, exist_ok=True)
    newly: list[str] = []
    for name in chosen:
        target = ctx.plugins_dir / name
        if target.exists() and not args.force:
            print(f"  {name}: already installed (use --force to overwrite)")
            continue
        if target.exists():
            remove_tree(target)
        try:
            offered[name].fetch(name, target)
        except Exception as exc:
            print(f"  {name}: install failed — {exc}")
            remove_tree(target)
            continue
        print(f"  {name}: installed from {offered[name].label}")
        newly.append(name)

    if not newly and chosen:
        # Everything requested was already present; that is not a failure the
        # caller needs to branch on.
        return 0

    if newly and not args.no_enable:
        _set_enabled(ctx, registry.enabled_names() + newly)
        print(f"enabled: {', '.join(newly)}")
    return 0 if newly else 1


def _uninstall(ctx: amcaContext, args: argparse.Namespace, _: dict[str, list[str]]) -> int:
    registry = Registry(ctx)
    if not args.plugins and not registry.names():
        print(f"no plugins installed in {ctx.plugins_dir}")
        return 0
    chosen = _pick(ctx, args.plugins, registry.names(), "Uninstall which plugins?")
    if chosen is None:
        return 1
    if not chosen:
        print("nothing selected, nothing removed")
        return 0
    if not args.yes and not confirm(f"Delete {', '.join(chosen)} from disk?", default=False):
        print("cancelled")
        return 1
    for name in chosen:
        remove_tree(ctx.plugins_dir / name)
        print(f"  {name}: removed")
    _set_enabled(ctx, [n for n in registry.enabled_names() if n not in chosen])
    return 0


def _update(ctx: amcaContext, args: argparse.Namespace, _: dict[str, list[str]]) -> int:
    registry = Registry(ctx)
    installed = registry.names()
    requested = [] if args.plugins in ([], ["*"]) else args.plugins
    targets = requested or installed
    if not targets:
        print("no plugins installed")
        return 0
    namespace = argparse.Namespace(
        plugins=targets, source=getattr(args, "source", None),
        no_enable=True, force=True,
    )
    return _install(ctx, namespace, {})


def _call(ctx: amcaContext, args: argparse.Namespace, _: dict[str, list[str]]) -> int:
    registry = Registry(ctx)
    name = args.plugin or select("Call which plugin?", registry.names())
    if name is None:
        return 1
    entry = registry.get(name)
    if entry is None:
        print(f"amcapl: no plugin named {name!r} (installed: {', '.join(registry.names())})")
        return 2
    instance = registry.instantiate(entry)
    if instance is None:
        return 1

    forwarded = list(args.args or [])
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]
    context = registry.make_context(name, forwarded, dry_run=False)
    try:
        return int(instance.load(context) or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        ctx.log.error(f"{name}: {type(exc).__name__}: {exc}")
        if ctx.config.get_bool("core.debug"):
            import traceback

            traceback.print_exc()
        return 1


def _main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        ctx = amcaContext(config_dir_override=args.config_dir)
        common.apply_global_flags(ctx, args)
    except ConfigError as exc:
        print(f"amcapl: {exc}", file=sys.stderr)
        return 2

    common.first_run_completion_notice(ctx)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return int(handler(ctx, args, {}) or 0)


def main(argv: Sequence[str] | None = None) -> int:
    return common.run_entry(_main, argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
