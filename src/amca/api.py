"""The plugin SDK. This module is the public, stable surface for plugin authors.

A plugin is a directory containing ``plugin.py`` (or ``init.py``, still
accepted) that defines one subclass of :class:`Plugin`:

    from amca.api import Plugin, PluginContext

    class hello(Plugin):
        name = "hello"

        def should_load(self, ctx: PluginContext) -> bool:
            return ctx.working_dir.has_file("Makefile")

        def load(self, ctx: PluginContext) -> int:
            ctx.log.log(f"args: {ctx.args}")
            return 0

Two things changed from the previous plugin API, both for the same reason.

First, plugins now ``import amca.api`` instead of importing from a copy of
amca's internals vendored into every plugin directory. Those copies existed
because the old amca was a frozen binary with no importable package: each
preset shipped its own ``amca/logger.py``, ``amca/dirparse.py`` and
``amca/plugin_base.py``, which then drifted from the originals. amca is a real
package now, so a plugin can just import it.

Second, the five positional parameters became one :class:`PluginContext`.
Adding a sixth piece of context used to be a breaking change for every plugin
in existence; now it is a new attribute. Old-style five-argument plugins are
still detected and adapted — see ``amca.plugins.loader``.
"""

from __future__ import annotations

import abc
import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .core import proc
from .core.dirparse import DirInfo, DirParser
from .core.logger import Logger

if TYPE_CHECKING:  # pragma: no cover
    from .core.context import amcaRoot

__all__ = [
    "DirInfo",
    "DirParser",
    "Logger",
    "Plugin",
    "PluginContext",
    "PluginError",
    "proc",
]


class PluginError(RuntimeError):
    """Raise from a plugin to fail cleanly with a message and no traceback."""


@dataclass(frozen=True, slots=True)
class PluginContext:
    """Everything a plugin is given for one invocation."""

    #: Arguments routed to this plugin via its ``---name`` marker.
    args: list[str]
    #: Directory amca was invoked from.
    working_dir: DirInfo
    #: The resolved amca root, or None when the user is outside a project.
    root: amcaRoot | None
    #: This plugin's private per-project directory
    #: (``<root>/.amca/plugins/<name>/``), or None when there is no root.
    #: Created before the plugin runs. Put caches and per-project state here.
    plugin_dir: Path | None
    #: Shared directory reader — use this instead of ``Path.iterdir`` so
    #: repeated lookups within one run stay cached.
    dirs: DirParser
    #: Logger honouring the user's verbosity settings.
    log: Logger
    #: True when the user passed ``--dry-run``. Plugins must print what they
    #: would do and change nothing.
    dry_run: bool = False

    @property
    def project_dir(self) -> Path:
        """Root directory if inside a project, else the working directory."""
        return self.root.path if self.root is not None else self.working_dir.path

    def project_dir_info(self) -> DirInfo:
        return self.dirs.parse_dir(self.project_dir)


class Plugin(abc.ABC):
    """Base class for plugins.

    Subclasses must be constructible with no arguments. amca instantiates the
    class once per invocation, then calls ``should_load`` and, if that returns
    True and the plugin is enabled, ``load``.
    """

    #: Display name. Defaults to the class name when unset. The *folder* name
    #: is what the user types after the marker prefix; this is what appears in
    #: log messages.
    name: str = ""

    #: Optional one-line summary shown by ``amcapl list``.
    description: str = ""

    @abc.abstractmethod
    def should_load(self, ctx: PluginContext) -> bool:
        """Cheap test for whether this plugin applies here.

        Must not run subprocesses, write files, or take noticeable time — it is
        called for every enabled plugin on every invocation.
        """

    @abc.abstractmethod
    def load(self, ctx: PluginContext) -> int | None:
        """Do the work. Return a process exit status; 0 or None means success."""

    # ── Tab completion (all optional) ───────────────────────────────────────
    #
    # amca completes the words after this plugin's marker. It needs the
    # plugin's argparse parser, found in this order:
    #
    #   1. ``parser()``, if it returns one. Cheap and side-effect free: prefer it.
    #   2. Otherwise amca calls ``load()`` with ``dry_run=True`` and stops it at
    #      the first ``parse_args``/``parse_known_args`` call, taking that
    #      parser. So: parse your arguments before doing anything else in
    #      ``load()``.
    #   3. ``complete()``, for plugins that don't use argparse at all.
    #
    # An argument can carry a ``complete`` attribute to steer its values:
    # ``"files"``, ``"dirs"``, ``"commands"``, ``"none"``, or a callable
    # ``(current_word) -> iterable of str or (str, description)``:
    #
    #     parser.add_argument("--out").complete = "dirs"

    #: Set to False if ``load()`` must never run during tab completion.
    completion: bool = True

    def parser(self) -> argparse.ArgumentParser | None:
        """The parser ``load()`` uses for ``ctx.args``, or None."""
        return None

    def complete(self, ctx: PluginContext, words: list[str]) -> list[str | tuple[str, str]] | None:
        """Candidates for the last of *words* (the one being typed), or None.

        Only for plugins without an argparse parser. Return values or
        ``(value, description)`` pairs; amca filters them by what was typed.
        """
        return None
