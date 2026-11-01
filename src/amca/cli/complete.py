"""Tab completion, answered by amca itself from its live parsers.

The shell scripts in :mod:`amca.cli.completions` are thin glue: on every tab
press they call ``amca __complete <shell> <prog> ...`` and print what comes
back. Everything else happens here, against the same argparse parsers a real
run uses. A new flag, alias or subcommand therefore completes the moment it
exists, and plugin arguments complete from the plugin's own parser.

Pipeline for one tab press:

1. Split the words the way a real run does (:func:`markers.split_argv`, with
   the configured prefix) to find which section the cursor is in: amca's own
   arguments, or a plugin's.
2. Walk that section's parser over the words before the cursor
   (:func:`walk`), tracking subcommands, options waiting for a value and the
   next positional slot.
3. Build an :class:`Answer` for the word under the cursor: groups of
   candidates in definition order, with aliases attached to their command, and
   optionally a request for the shell's own file/directory/command completion.
4. Format it for the shell (:func:`render`).

Plugin parsers come from ``Plugin.parser()``, or are captured by running
``load()`` until its first ``parse_known_args`` call (see :func:`capture_parser`).
Only the plugin whose marker is active is ever imported.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shlex
import sys
import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ..api import Plugin
    from ..core.context import amcaContext
    from ..plugins.registry import Registry

__all__ = [
    "AVAILABLE_PLUGIN",
    "COMMANDS",
    "COMPLETE_ATTR",
    "CONFIG_KEY",
    "CONFIG_VALUE",
    "DIRS",
    "FILES",
    "NOTHING",
    "OFFERS_MARKERS_ATTR",
    "PLUGIN",
    "PLUGIN_ARGS",
    "Answer",
    "Group",
    "Item",
    "answer_for",
    "capture_parser",
    "main",
    "offers_markers",
    "render",
    "steer",
    "walk",
]

#: Attribute on an argparse Action that steers its values: one of the
#: fallback names below, an amca-internal hint, or a callable.
COMPLETE_ATTR = "complete"

#: Attribute on an ArgumentParser: plugin markers are offered at its positional
#: slots (amca's root parser and `run`), not only once a prefix is typed.
OFFERS_MARKERS_ATTR = "offers_markers"

#: What the shell should complete natively, in addition to the candidates.
FILES, DIRS, COMMANDS, NOTHING = "files", "dirs", "commands", "none"
_FALLBACKS = (FILES, DIRS, COMMANDS, NOTHING)

#: amca-internal hints, resolved by _Session.resolve.
CONFIG_KEY = "config_key"
CONFIG_VALUE = "config_value"           # the value of the `key` positional before it
PLUGIN = "plugin"                       # an installed plugin
AVAILABLE_PLUGIN = "available_plugin"   # a plugin a local source offers
PLUGIN_ARGS = "plugin_args"             # REMAINDER: the arguments of the `plugin` positional
_REST = "@rest"


def steer(action: argparse.Action, hint: str | Callable[[str], Iterable[Any]]) -> argparse.Action:
    """Attach a completion hint to *action*; returns it for chaining."""
    setattr(action, COMPLETE_ATTR, hint)
    return action


def offers_markers(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    setattr(parser, OFFERS_MARKERS_ATTR, True)
    return parser

SHELLS = ("bash", "zsh", "fish")


@dataclass(slots=True)
class Item:
    #: The primary name first, then its aliases.
    names: list[str]
    description: str = ""


@dataclass(slots=True)
class Group:
    #: zsh completion tag: commands, markers, options, values.
    tag: str
    label: str
    items: list[Item]


@dataclass(slots=True)
class Answer:
    groups: list[Group] = field(default_factory=list)
    fallback: str = NOTHING
    #: Text in front of the value being completed, e.g. ``--log-mode=``.
    prefix: str = ""

    def add(self, tag: str, label: str, items: Iterable[Item]) -> None:
        listed = list(items)
        if listed:
            self.groups.append(Group(tag, label, listed))

    @property
    def empty(self) -> bool:
        return not self.groups and self.fallback == NOTHING


# ── Walking a parser ─────────────────────────────────────────────────────────

_GREEDY = None          # nargs '*' / '+': take values until the next option
_OPTIONAL = "?"


def _arity(action: argparse.Action) -> int | str | None:
    """How many values an action takes: an int, ``'?'``, greedy (None) or
    REMAINDER."""
    nargs = action.nargs
    if nargs is None:
        return 1
    if isinstance(nargs, int):
        return nargs
    if nargs == argparse.OPTIONAL:
        return _OPTIONAL
    if nargs in (argparse.ZERO_OR_MORE, argparse.ONE_OR_MORE):
        return _GREEDY
    if nargs == argparse.REMAINDER:
        return argparse.REMAINDER
    return 1                      # PARSER ('A...'): a subcommand name, handled by walk()


def _positionals(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    return [a for a in parser._actions if not a.option_strings]


@dataclass(slots=True)
class Walk:
    """Where the cursor sits after the words before it."""

    parser: argparse.ArgumentParser
    #: Option still owed a value at the cursor.
    pending: argparse.Action | None = None
    pending_optional: bool = False
    #: Next positional slot, or None when all are filled.
    positional: argparse.Action | None = None
    #: A REMAINDER positional/option swallowed the rest; these are its words.
    rest: argparse.Action | None = None
    rest_words: list[str] = field(default_factory=list)
    after_dashdash: bool = False
    #: The words could not be parsed (unknown subcommand): offer nothing.
    dead: bool = False
    used: set[int] = field(default_factory=set)
    values: dict[str, list[str]] = field(default_factory=dict)


def walk(parser: argparse.ArgumentParser, words: Sequence[str]) -> Walk:
    """Follow *words* (complete words only) through *parser* and its subparsers.

    Deliberately forgiving: unknown options are skipped, short-option clusters
    are ignored. Completion should keep helping on a line argparse would
    reject.
    """
    state = Walk(parser)
    positionals = _positionals(parser)
    slot = 0
    filled = 0                      # values given to positionals[slot]
    pending_left: int | None = 0

    def record(action: argparse.Action, value: str) -> None:
        state.values.setdefault(action.dest, []).append(value)

    for token in words:
        if state.rest is not None:
            state.rest_words.append(token)
            continue

        if state.pending is not None:
            looks_like_option = token.startswith("-") and token != "-" and not state.after_dashdash
            if looks_like_option and (pending_left is None or state.pending_optional):
                state.pending = None            # an option ends a greedy/optional value list
            else:
                record(state.pending, token)
                if pending_left is not None:
                    pending_left -= 1
                    if pending_left <= 0:
                        state.pending = None
                continue

        if token == "--" and not state.after_dashdash:
            state.after_dashdash = True
            continue

        if token.startswith("-") and len(token) > 1 and not state.after_dashdash:
            name, has_value, value = token.partition("=") if token.startswith("--") else (token, "", "")
            action = state.parser._option_string_actions.get(name)
            if action is None:
                continue
            state.used.add(id(action))
            arity = _arity(action)
            if has_value:
                record(action, value)
                continue
            if arity == 0:
                continue
            if arity == argparse.REMAINDER:
                state.rest = action
                continue
            state.pending = action
            state.pending_optional = arity == _OPTIONAL
            pending_left = arity if isinstance(arity, int) else (1 if arity == _OPTIONAL else None)
            continue

        # A positional word.
        if slot >= len(positionals):
            continue
        action = positionals[slot]
        if isinstance(action, argparse._SubParsersAction):
            chosen = action.choices.get(token)
            if chosen is None:
                state.dead = True
                return state
            record(action, token)
            state.parser = chosen
            state.used = set()
            positionals = _positionals(chosen)
            slot, filled = 0, 0
            continue
        arity = _arity(action)
        if arity == argparse.REMAINDER:
            state.rest = action
            state.rest_words.append(token)
            continue
        record(action, token)
        filled += 1
        if arity is _GREEDY:
            continue
        if arity == _OPTIONAL or (isinstance(arity, int) and filled >= arity):
            slot, filled = slot + 1, 0

    if slot < len(positionals):
        state.positional = positionals[slot]
    return state


# ── Building an answer ───────────────────────────────────────────────────────

#: Resolves amca-internal string hints (config keys, plugin names, ...).
Resolver = Callable[[str, Walk, str], "Answer | None"]


def _help(parser: argparse.ArgumentParser, action: argparse.Action) -> str:
    if not action.help or action.help == argparse.SUPPRESS:
        # Better than a blank: what the option accepts.
        return "{" + "|".join(map(str, action.choices)) + "}" if action.choices else ""
    try:
        text = parser._get_formatter()._expand_help(action)
    except Exception:
        text = action.help
    return " ".join(text.split())


def _option_items(state: Walk) -> list[Item]:
    parser = state.parser
    blocked: set[int] = set()
    for group in parser._mutually_exclusive_groups:
        members = {id(a) for a in group._group_actions}
        if members & state.used:
            blocked |= members - state.used
    # _ExtendAction subclasses _AppendAction, so it is covered.
    repeatable = (argparse._AppendAction, argparse._AppendConstAction, argparse._CountAction)

    items = []
    for action in parser._actions:
        if not action.option_strings or action.help == argparse.SUPPRESS:
            continue
        if id(action) in blocked:
            continue
        if id(action) in state.used and not isinstance(action, repeatable):
            continue
        # Long spelling first: it is the one that says what the option does.
        names = sorted(action.option_strings, key=lambda s: (not s.startswith("--"), s))
        items.append(Item(names, _help(parser, action)))
    return items


def _subcommand_items(action: argparse._SubParsersAction[argparse.ArgumentParser]) -> list[Item]:
    helps = {pseudo.dest: pseudo.help for pseudo in action._choices_actions}
    by_parser: dict[int, Item] = {}
    items = []
    for name, sub in action.choices.items():
        existing = by_parser.get(id(sub))
        if existing is not None:
            existing.names.append(name)
            continue
        help_text = helps.get(name) or ""
        if help_text == argparse.SUPPRESS:
            continue
        item = Item([name], " ".join(str(help_text).split()))
        by_parser[id(sub)] = item
        items.append(item)
    return items


def _label(action: argparse.Action) -> str:
    metavar = action.metavar
    if isinstance(metavar, tuple):
        metavar = metavar[0] if metavar else None
    return str(metavar or action.dest).lower().strip('"')


def _from_callable(hint: Callable[..., Any], current: str) -> list[Item]:
    items = []
    for entry in hint(current) or ():
        if isinstance(entry, tuple):
            items.append(Item([str(entry[0])], str(entry[1]) if len(entry) > 1 else ""))
        else:
            items.append(Item([str(entry)]))
    return items


def values_for(action: argparse.Action, state: Walk, current: str, resolve: Resolver) -> Answer:
    """Candidates for a value of *action*."""
    answer = Answer()
    hint = getattr(action, COMPLETE_ATTR, None)
    if callable(hint):
        try:
            answer.add("values", _label(action), _from_callable(hint, current))
        except Exception:
            answer.fallback = FILES
        return answer
    if isinstance(hint, str):
        if hint in _FALLBACKS:
            answer.fallback = hint
            return answer
        resolved = resolve(hint, state, current)
        return resolved if resolved is not None else answer
    if isinstance(action, argparse._SubParsersAction):
        answer.add("commands", "command", _subcommand_items(action))
        return answer
    if action.choices:
        answer.add("values", _label(action), (Item([str(c)]) for c in action.choices))
        return answer
    if action.type in (int, float):
        return answer
    answer.fallback = FILES
    return answer


def answer_for(state: Walk, current: str, resolve: Resolver) -> Answer:
    """What to offer for *current*, the word under the cursor."""
    if state.dead:
        return Answer()
    if (state.rest is None and state.positional is not None and not state.after_dashdash
            and state.pending is None and _arity(state.positional) == argparse.REMAINDER):
        # argparse hands a REMAINDER slot everything, options included.
        state.rest = state.positional
    if state.rest is not None:
        resolved = resolve(_REST, state, current)
        return resolved if resolved is not None else Answer(fallback=FILES)

    optionish = current.startswith("-") and not state.after_dashdash
    if state.pending is not None and not (state.pending_optional and optionish):
        return values_for(state.pending, state, current, resolve)

    if optionish and current.startswith("--") and "=" in current:
        name, _, value = current.partition("=")
        action = state.parser._option_string_actions.get(name)
        if action is not None and _arity(action) != 0:
            answer = values_for(action, state, value, resolve)
            answer.prefix = f"{name}="
            return answer

    if optionish:
        answer = Answer()
        answer.add("options", "option", _option_items(state))
        return answer

    if state.positional is not None:
        return values_for(state.positional, state, current, resolve)
    # Nothing positional left: options are all that remains.
    answer = Answer()
    if not state.after_dashdash:
        answer.add("options", "option", _option_items(state))
    return answer


# ── Plugin parsers ───────────────────────────────────────────────────────────

class _ParserCaptured(BaseException):
    """Raised from inside a plugin's load() to hand its parser back.

    BaseException, so a plugin's ``except Exception`` cannot swallow it.
    """

    def __init__(self, parser: argparse.ArgumentParser) -> None:
        super().__init__()
        self.parser = parser


@contextlib.contextmanager
def _silenced() -> Iterator[None]:
    """Point fd 1 and 2 at /dev/null, so a plugin's stray print (or a child
    process it starts) cannot end up in the completion output."""
    import io

    sys.stdout.flush()
    sys.stderr.flush()
    streams = (sys.stdout, sys.stderr)
    saved = [os.dup(1), os.dup(2)]
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        # Python-level writes too: sys.stdout may not be fd 1 (embedding, tests).
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        yield
    finally:
        sys.stdout, sys.stderr = streams
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
        for fd in (*saved, devnull):
            os.close(fd)


#: Set when a capture timed out: its thread may still be running plugin code,
#: so main() leaves with os._exit instead of waiting for it.
_ABANDONED = threading.Event()


def capture_parser(plugin: Plugin, ctx: Any, timeout: float) -> argparse.ArgumentParser | None:
    """Run ``plugin.load(ctx)`` until it parses its arguments; return that parser.

    ``ArgumentParser.parse_known_args`` (which ``parse_args`` goes through) is
    patched to raise instead of parsing, so the plugin never gets past that
    line. A plugin that returns, raises, prints or hangs instead yields None.
    """
    original = argparse.ArgumentParser.parse_known_args
    found: list[argparse.ArgumentParser] = []

    def intercept(self: argparse.ArgumentParser, *_: Any, **__: Any) -> Any:
        raise _ParserCaptured(self)

    def run() -> None:
        try:
            plugin.load(ctx)
        except _ParserCaptured as captured:
            found.append(captured.parser)
        except BaseException:  # noqa: S110 - anything else means "no parser"
            pass

    argparse.ArgumentParser.parse_known_args = intercept  # type: ignore[method-assign]
    try:
        with _silenced():
            worker = threading.Thread(target=run, daemon=True, name="amca-complete-capture")
            worker.start()
            worker.join(timeout)
            if worker.is_alive():
                _ABANDONED.set()
    finally:
        argparse.ArgumentParser.parse_known_args = original  # type: ignore[method-assign]
    return found[0] if found else None


# ── Formatting for the shells ────────────────────────────────────────────────

def _clean(text: str) -> str:
    return " ".join(text.replace("\t", " ").split())


def render(answer: Answer, shell: str, current: str) -> str:
    """The text the shell glue reads.

    Line 1 is ``<fallback>\\t<prefix>``. Then, per shell:

    * bash: one candidate per line, already filtered by *current* (bash does
      no matching of its own), aliases included.
    * zsh: ``G\\t<tag>\\t<label>`` starts a group; ``I\\t<value>\\t<display>``
      is a listed entry, ``A\\t<value>`` a hidden alias. Not filtered: zsh
      applies the user's own matcher styles. Display strings are aligned here
      because zsh's _describe would re-sort them.
    * fish: ``<value>\\t<description>``.
    """
    lines = [f"{answer.fallback}\t{answer.prefix}"]
    if shell == "bash":
        typed = current
        for group in answer.groups:
            for item in group.items:
                for name in item.names:
                    value = answer.prefix + name
                    if value.startswith(typed):
                        lines.append(value)
    elif shell == "fish":
        for group in answer.groups:
            for item in group.items:
                for index, name in enumerate(item.names):
                    description = item.description or group.label
                    if index:
                        description = f"{item.names[0]}: {description}"
                    lines.append(f"{answer.prefix}{name}\t{_clean(description)}")
    elif shell == "zsh":
        for group in answer.groups:
            lines.append(f"G\t{group.tag}\t{_clean(group.label)}")
            described = any(item.description or len(item.names) > 1 for item in group.items)
            width = max(len(", ".join(item.names)) for item in group.items)
            for item in group.items:
                display = ""
                if described:
                    left = ", ".join(item.names).ljust(width)
                    display = f"{left}  -- {_clean(item.description)}" if item.description else left
                lines.append(f"I\t{item.names[0]}\t{display}")
                lines.extend(f"A\t{alias}" for alias in item.names[1:])
    else:
        raise ValueError(f"unsupported shell {shell!r}")
    return "\n".join(lines) + "\n"


# ── amca's own parsers, plus everything that needs config or plugins ─────────

class _Session:
    """Config, registry and plugin access for one tab press, created lazily."""

    def __init__(self, prog: str, words: list[str]) -> None:
        from . import common

        self.prog = prog
        self.words = words
        self.pre = common.pre_scan(words)
        self._ctx: amcaContext | None = None
        self._registry: Registry | None = None

    @property
    def ctx(self) -> amcaContext:
        if self._ctx is None:
            from ..core.context import amcaContext

            self._ctx = amcaContext(config_dir_override=self.pre.get("config-dir"))
            self._ctx.log.set_mode("silent")
        return self._ctx

    @property
    def registry(self) -> Registry:
        if self._registry is None:
            from ..plugins.registry import Registry

            self._registry = Registry(self.ctx)
        return self._registry

    @property
    def prefix(self) -> str:
        return self.pre.get("marker-prefix") or self.ctx.config.get_str("plugins.marker_prefix")

    # -- candidates ----------------------------------------------------------

    def marker_items(self) -> list[Item]:
        from ..plugins.markers import normalize

        prefix = self.prefix
        return [
            Item([f"{prefix}{normalize(entry.name)}"],
                 f"{entry.name} plugin" + ("" if entry.enabled else " (disabled)"))
            for entry in self.registry.entries
        ]

    def plugin_items(self) -> list[Item]:
        return [Item([entry.name], "enabled" if entry.enabled else "disabled")
                for entry in self.registry.entries]

    def available_items(self) -> list[Item]:
        from ..plugins.sources import parse_source

        installed = set(self.registry.names())
        seen: dict[str, Item] = {}
        for spec in (str(s) for s in self.ctx.config.get_list("plugins.sources")):
            try:
                source = parse_source(spec)
            except ValueError:
                continue
            if source.remote:
                continue
            try:
                names = source.list_plugins()
            except Exception:  # noqa: S112 - an unreadable source offers nothing
                continue
            for name in names:
                if name not in seen:
                    seen[name] = Item([name], "installed" if name in installed else source.label)
        # Not yet installed first: that is what `install` is usually for.
        return sorted(seen.values(), key=lambda item: item.description == "installed")

    def resolve(self, hint: str, state: Walk, current: str) -> Answer | None:
        from ..config.schema import SCHEMA

        answer = Answer()
        if hint == CONFIG_KEY:
            answer.add("values", "setting",
                       (Item([key], fld.help) for key, fld in SCHEMA.items()))
        elif hint == CONFIG_VALUE:
            key = (state.values.get("key") or [""])[-1]
            fld = SCHEMA.get(key)
            if fld is None:
                return answer
            if fld.choices:
                answer.add("values", key, (Item([c]) for c in fld.choices))
            elif fld.kind == "bool":
                answer.add("values", key, (Item([v]) for v in ("true", "false")))
            elif fld.kind == "path":
                answer.fallback = DIRS
        elif hint == PLUGIN:
            answer.add("values", "plugin", self.plugin_items())
        elif hint == AVAILABLE_PLUGIN:
            answer.add("values", "plugin", self.available_items())
        elif hint == _REST:
            plugin = (state.values.get("plugin") or [""])[-1]
            if state.rest is not None and getattr(state.rest, COMPLETE_ATTR, None) == PLUGIN_ARGS and plugin:
                return self.plugin_answer(plugin, state.rest_words, current)
            return Answer(fallback=FILES)
        else:
            return None
        return answer

    # -- plugin sections -----------------------------------------------------

    def plugin_parser(self, name: str, typed: list[str]) -> tuple[Plugin | None, argparse.ArgumentParser | None]:
        entry = self.registry.get(name)
        if entry is None:
            return None, None
        with _silenced():
            instance = self.registry.instantiate(entry)
        if instance is None or getattr(instance, "completion", True) is False:
            return instance, None
        hook = getattr(instance, "parser", None)
        if callable(hook):
            try:
                with _silenced():
                    parser = hook()
            except Exception:
                parser = None
            if isinstance(parser, argparse.ArgumentParser):
                return instance, parser
        timeout_ms = self.ctx.config.get_int("completion.plugin_timeout_ms")
        if timeout_ms <= 0:
            return instance, None
        return instance, capture_parser(instance, self.plugin_context(typed), timeout_ms / 1000)

    def plugin_context(self, typed: list[str]) -> Any:
        from ..api import PluginContext

        # No root lookup: finding one may offer to create it, and nothing may
        # prompt or write during a tab press.
        return PluginContext(
            args=list(typed),
            working_dir=self.ctx.working_dir_info(),
            root=None,
            plugin_dir=None,
            dirs=self.ctx.dirs,
            log=self.ctx.log,
            dry_run=True,
        )

    def plugin_answer(self, name: str, words: list[str], current: str) -> Answer:
        instance, parser = self.plugin_parser(name, words)
        if parser is None:
            custom = getattr(instance, "complete", None) if instance is not None else None
            if callable(custom):
                try:
                    with _silenced():
                        offered = custom(self.plugin_context(words), [*words, current])
                except Exception:
                    offered = None
                if offered is not None:
                    answer = Answer()
                    answer.add("values", name, _from_callable(lambda _: offered, current))
                    return answer
            return Answer(fallback=FILES)
        return answer_for(walk(parser, words), current, self.resolve)


def _core_parser(session: _Session) -> argparse.ArgumentParser:
    if session.prog == "amcapl":
        from .amcapl_cli import build_parser as build_pl

        return build_pl()
    from .amca_cli import build_parser

    # The real prefix, so help texts that mention it (--marker-prefix) are right.
    return build_parser(session.prefix)


def complete(prog: str, words: list[str], current: str) -> Answer:
    """The answer for *current*, after *words* (which exclude the command name)."""
    session = _Session(prog, [*words, current])
    if prog == "amcapl":
        return answer_for(walk(_core_parser(session), words), current, session.resolve)

    from ..plugins.markers import split_argv

    prefix = session.prefix
    split = split_argv(words, session.registry.names(), prefix, strict=False)
    marker_typed = current.startswith(prefix) and not split.passthrough

    if split.current is not None:
        answer = (Answer() if marker_typed
                  else session.plugin_answer(split.current, split.per_plugin[split.current], current))
    else:
        parser = _core_parser(session)
        state = walk(parser, split.main)
        answer = Answer() if marker_typed else answer_for(state, current, session.resolve)
        at_marker_slot = (
            state.pending is None and state.rest is None and not state.dead
            and not current.startswith("-") and getattr(state.parser, OFFERS_MARKERS_ATTR, False)
        )
        if not (marker_typed or at_marker_slot):
            return answer
    if marker_typed or split.current is None:
        markers = session.marker_items()
        # After subcommands, which are what a bare `amca <TAB>` is mostly
        # after; before options, which are only a fallback here.
        group = Group("markers", "plugin marker", markers)
        if markers:
            at = next((i for i, g in enumerate(answer.groups) if g.tag == "options"),
                      len(answer.groups))
            answer.groups.insert(at, group)
    return answer


# ── Entry point: `amca __complete` ───────────────────────────────────────────

def _split_line(line: str) -> tuple[list[str], str]:
    """Shell words of *line* (up to the cursor), and the partial last word."""
    for closing in ("", '"', "'"):
        try:
            words = shlex.split(line + closing)
            break
        except ValueError:
            continue
    else:
        words = line.split()
    if not line or line[-1].isspace():
        return words, ""
    return words[:-1], (words[-1] if words else "")


USAGE = "usage: amca __complete {bash|zsh|fish} {amca|amcapl} (--line LINE | -- WORDS... CURRENT)"


def main(argv: Sequence[str]) -> int:
    """``amca __complete <shell> <prog> -- <words...> <current>``.

    bash passes ``--line "<line up to the cursor>"`` instead, because its own
    word splitting breaks ``--opt=value`` apart. The command name is included
    in --line and excluded from the word form.

    Never fails loudly: on any internal error it asks the shell for file
    completion, which is what the shell would have done without amca.
    """
    args = list(argv)
    if len(args) < 3 or args[0] not in SHELLS or args[1] not in ("amca", "amcapl"):
        print(USAGE, file=sys.stderr)
        return 2
    shell, prog, mode, rest = args[0], args[1], args[2], args[3:]
    if mode == "--line":
        words, current = _split_line(rest[0] if rest else "")
        words = words[1:]
    elif mode == "--":
        words, current = rest[:-1], (rest[-1] if rest else "")
    else:
        print(USAGE, file=sys.stderr)
        return 2

    _ABANDONED.clear()
    try:
        answer = complete(prog, words, current)
    except BaseException:
        if os.environ.get("AMCA_COMPLETE_DEBUG"):
            raise
        answer = Answer(fallback=FILES)
    sys.stdout.write(render(answer, shell, current))
    sys.stdout.flush()
    if _ABANDONED.is_set():
        os._exit(0)
    return 0
