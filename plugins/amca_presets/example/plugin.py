"""A worked example of the amca plugin API. Copy this directory and edit it.

    amcapl install example        # then: amca ---example --show

Everything a plugin needs is in `amca.api`. There is no vendoring, no
`sys.path` juggling and no base-class boilerplate beyond the two abstract
methods — amca is an installed package, so you just import it.

    ~/.config/amca/plugins/my-plugin/
        plugin.py        <- must define exactly one subclass of Plugin
        _impl/           <- optional; imported as `from ._impl import ...`
                            and namespaced per plugin, so two plugins can both
                            have an `_impl` without colliding

The folder name is what the user types after the marker prefix, with
underscores written as dashes: `my_plugin/` is reached as `---my-plugin`.
"""

from __future__ import annotations

import argparse

from amca.api import Plugin, PluginContext, PluginError
from amca.core import proc


class example(Plugin):
    #: Shown by `amcapl list`. Optional but worth writing.
    description = "worked example — prints its context, runs a command"

    #: Optional. Defaults to the folder name, which is almost always right.
    # name = "example"

    # ── Selection ───────────────────────────────────────────────────────────

    def should_load(self, ctx: PluginContext) -> bool:
        """Does this plugin apply to where the user is standing?

        Called for every enabled plugin on every single invocation, so keep it
        to filesystem lookups. No subprocesses, no network, no writes.

        Use `ctx.dirs` rather than `Path.iterdir()`: it caches, so ten plugins
        asking about the same directory cost one `scandir` between them.
        """
        # Typical real check — "is there a file that means this tool applies?":
        #     return ctx.project_dir_info().has_file("Cargo.toml")
        #
        # `project_dir_info()` is the amca root when there is one, otherwise the
        # working directory, so `amca` works from a subdirectory of a project.
        # For a working-directory-only check use `ctx.working_dir` instead.
        #
        # This example loads whenever it is addressed by name, and never
        # otherwise, so installing it cannot surprise anyone.
        return bool(ctx.args)

    # ── Work ────────────────────────────────────────────────────────────────

    def load(self, ctx: PluginContext) -> int:
        """Do the thing. Return an exit status; 0 or None means success.

        Raise `PluginError` for an expected failure: the user sees the message
        with no traceback. Anything else is caught and reported too, but reads
        like a crash — because it is one.
        """
        # Parse first, before any other work: tab completion relies on it (see
        # `parser()` below). parse_known_args, because everything else is
        # passed on to echo.
        opts, _ = self.parser().parse_known_args(ctx.args)
        if opts.show:
            self._show(ctx)
            return 0

        if opts.fail:
            raise PluginError("this is what a clean failure looks like")

        # Always resolve external tools through proc: it honours
        # AMCA_TOOL_<NAME> overrides and raises a ToolMissing that amca turns
        # into an actionable message instead of a FileNotFoundError.
        try:
            echo = proc.resolve_tool("echo", hint="every system has echo, so this is odd")
        except proc.ToolMissing as exc:
            raise PluginError(str(exc)) from exc

        command = [echo, "example plugin ran with:", *ctx.args]

        # Honour --dry-run: say what would happen, change nothing.
        if ctx.dry_run:
            ctx.log.log(f"[example] would run: {' '.join(command)}")
            return 0

        # proc.call returns the child's exit status; 130 means Ctrl-C, and
        # re-raising lets amca stop the whole pipeline cleanly.
        code = proc.call(command, cwd=str(ctx.project_dir))
        if code == 130:
            raise KeyboardInterrupt
        return code

    # ── Tab completion ──────────────────────────────────────────────────────

    def parser(self) -> argparse.ArgumentParser:
        """The parser `load()` uses. Optional, and cheap on purpose.

        `amca ---example -<TAB>` offers these flags with their help text. amca
        calls this method during a tab press instead of running `load()`.
        Without it, amca would run `load()` with `dry_run=True` and stop it at
        the first `parse_args`/`parse_known_args`, which works too as long as
        parsing comes first.

        Steer an argument's values with a `complete` attribute:
            parser.add_argument("--out").complete = "dirs"
        ("files", "dirs", "commands", "none", or a function
        `(current_word) -> [value or (value, description), ...]`).
        """
        parser = argparse.ArgumentParser(prog="amca ---example", add_help=False)
        parser.add_argument("--show", action="store_true",
                            help="print every PluginContext field and what it is for")
        parser.add_argument("--fail", action="store_true",
                            help="fail cleanly with a PluginError")
        return parser

    # ── Everything the context gives you ────────────────────────────────────

    @staticmethod
    def _show(ctx: PluginContext) -> None:
        log = ctx.log
        log.log(f"args         {ctx.args}")
        log.log(f"working_dir  {ctx.working_dir.path}")
        log.log(f"root         {ctx.root.path if ctx.root else '(none — not in a project)'}")
        log.log(f"project_dir  {ctx.project_dir}   (root if there is one, else working_dir)")
        log.log(f"plugin_dir   {ctx.plugin_dir}   (yours; already created; cache state here)")
        log.log(f"dry_run      {ctx.dry_run}")
        log.log("")
        log.log("files here   " + ", ".join(sorted(ctx.working_dir.files)[:8] or ["(none)"]))
        log.success("log levels: .log/.info, .success, .warn, .error, .fatal(msg, code)")
        log.warning("warnings and errors go to stderr, so stdout stays pipeable")
