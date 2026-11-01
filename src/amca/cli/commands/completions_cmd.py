"""``amca completions`` — print, install or remove shell completion scripts."""

from __future__ import annotations

import argparse
import sys

from ...core.context import amcaContext
from .. import completions as comp

__all__ = ["handle", "register"]


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "completions",
        help="print, install or remove shell tab-completion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "A wheel cannot register completions at install time, so amca does it\n"
            "on the first interactive run instead: the script goes to your shell's\n"
            "per-user completion directory, and for bash and zsh a delimited block\n"
            "that loads it goes into ~/.bashrc / ~/.zshrc. Both are refreshed when\n"
            "amca is upgraded.\n\n"
            "--uninstall removes both and stops the automatic setup from coming\n"
            f"back; --install turns it on again. Set {comp.ENV_DISABLE}=1 to opt out\n"
            "without uninstalling.\n"
        ),
    )
    parser.add_argument("shell", nargs="?", choices=list(comp.SHELLS), default=None,
                        help="defaults to the shell named in $SHELL")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--install", action="store_true",
                        help="write the script, and the rc block that loads it")
    action.add_argument("--uninstall", action="store_true",
                        help="remove the script and rc block, and stop auto-setup")
    action.add_argument("--status", action="store_true",
                        help="report where completions are, or would be, installed")
    action.add_argument("--where", action="store_true",
                        help="print manual installation instructions")
    parser.add_argument("--no-rc", dest="no_rc", action="store_true",
                        help="with --install, leave the rc file alone (bash/zsh then "
                             "need the printed block added by hand)")
    # The default since the rc block became automatic; kept so old docs work.
    parser.add_argument("--rc", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--command", action="append", default=[], metavar="NAME",
                        help="also complete this name as `amca` — for an alias or "
                             "wrapper (repeatable)")
    parser.add_argument("--pl-command", dest="pl_command", action="append", default=[],
                        metavar="NAME", help="also complete this name as `amcapl`")
    parser.set_defaults(handler=handle)


def _resolve(shell: str | None) -> str | None:
    return shell or comp.detect_shell()


def handle(ctx: amcaContext, args: argparse.Namespace, _: dict[str, list[str]]) -> int:
    if args.status:
        return _status()

    shell = _resolve(args.shell)
    if shell is None:
        print(
            f"amca: name a shell ({', '.join(comp.SHELLS)}); "
            f"$SHELL={_shell_env()!r} was not recognised",
            file=sys.stderr,
        )
        return 2

    state = comp.load_state(ctx.state_dir)

    if args.uninstall:
        removed, edited = comp.uninstall(shell)
        comp.save_state(ctx.state_dir, {**state, "disabled": True})
        if not removed and not edited:
            print(f"nothing installed for {shell}")
        for path in removed:
            print(f"deleted {path}")
        for path in edited:
            print(f"removed the amca block from {path}")
        print("  automatic setup is off now; `amca completions --install` turns it back on")
        if removed or edited:
            print("  restart your shell for it to take effect")
        return 0

    if args.install:
        old_names, old_pl = comp.remembered_names(state)
        names = list(dict.fromkeys([*old_names, *args.command]))
        pl_names = list(dict.fromkeys([*old_pl, *args.pl_command]))
        result = comp.install(shell, patch_rc=not args.no_rc, names=names, pl_names=pl_names)
        if result.note.startswith("could not write"):
            print(f"amca: {result.note}", file=sys.stderr)
            return 1
        comp.save_state(ctx.state_dir, {
            **{k: v for k, v in state.items() if k != "disabled"},
            "names": names, "pl_names": pl_names,
            # Only the shell the automatic installer manages gets a fingerprint;
            # installing for another shell must not mark $SHELL's as done.
            **({"shell": shell, "fingerprint": comp.fingerprint(shell, names=names,
                                                                pl_names=pl_names)}
               if shell == comp.detect_shell() else {}),
        })
        print(f"{'installed' if result.written else 'already up to date'}: {result.path}")
        if result.rc_patched and result.rc_file:
            print(f"added the block that loads it to {result.rc_file}")
        if result.note:
            print(f"\n  {result.note}")
        print(f"\n  restart your shell (or `exec {shell}`) to pick it up")
        return 0

    if args.where:
        print(f"# {shell}\n  {comp.install_hint(shell)}")
        return 0

    if sys.stdout.isatty():
        # Nobody wants 200 lines of shell in their scrollback; they meant to
        # redirect it, or they wanted --install.
        print(f"# amca {shell} completion. To install it for real:", file=sys.stderr)
        print("#   amca completions --install\n", file=sys.stderr)

    print(comp.script_for(shell, names=args.command, pl_names=args.pl_command), end="")
    return 0


def _shell_env() -> str:
    import os

    return os.environ.get("SHELL", "")


def _status() -> int:
    current = comp.detect_shell()
    incomplete = False
    states = {shell: ("active" if comp.is_active(shell)
                      else "installed" if comp.is_installed(shell)
                      else "not installed") for shell in comp.SHELLS}
    width = max(map(len, states.values()))
    shell_width = max(map(len, comp.SHELLS))
    for shell in comp.SHELLS:
        path = comp.target_path(shell)
        mark = " <- your shell" if shell == current else ""
        print(f"  {shell:<{shell_width}} {states[shell]:<{width}} {path}{mark}")
        if states[shell] == "installed":
            incomplete = True
            rc = comp.rc_file(shell)
            why = ("only bash-completion would load it, if installed" if shell == "bash"
                   else f"{rc} does not load it")
            print(f"  {'':<{shell_width}} NOT ACTIVE: {why}.")
            print(f"  {'':<{shell_width}} Fix with:   amca completions {shell} --install")

    print(f"\n  helper used by the scripts: {comp._executable('amca')}")
    if incomplete:
        print("  (a file being 'installed' is not enough for bash and zsh: see above)")
    print("  aliases need their own registration: "
          "amca completions --install --command <alias>")
    return 0
