"""Splitting ``amca`` argv into amca's own arguments and per-plugin payloads.

    amca run ---meson -s ---autoscript --fast

              ^^^^^^^^ marker      ^^^^^^^^^^^^ marker
    amca args: ["run"]
    meson:     ["-s"]
    autoscript:["--fast"]

The previous implementation had two functions that disagreed about what a
marker is. ``split_at_first_plugin_marker`` cut argv at the first token that
merely *started with* the prefix, while ``extract_plugin_args`` then matched
only exact ``prefix + known-plugin-name`` tokens. Anything in between —
a typo like ``---mesn``, a plugin that was installed but disabled, or the
prefix's own value appearing as an argument — split the argv and was then
silently dropped along with every argument after it. No warning, no error, the
build just quietly did the wrong thing.

Here there is one definition of a marker and one pass over argv, and an
unrecognised marker is a hard error that lists what was expected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["END_OF_MARKERS", "VALUE_FLAGS", "MarkerSplit", "UnknownMarker", "split_argv"]

#: A marker is the prefix followed by something shaped like a plugin name.
#: This matters because the strict unknown-marker error must not fire on an
#: ordinary value that merely starts with the prefix — `amca config set
#: plugins.marker_prefix ----` is a legitimate command, and `----` is not a
#: plausible plugin name. Anything matching this shape that names no known
#: plugin is a typo worth reporting; anything else is just an argument.
_PLUGIN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: Everything after this token is passed through verbatim, as in any Unix tool.
END_OF_MARKERS = "--"

#: Global options that take a separate value. The value must be passed through
#: untouched: `--marker-prefix @@` must not classify `@@` as a marker just
#: because it starts with the prefix it is *defining*. This is the exact bug
#: that made the old `--plugin-prefix` unusable, so it gets an explicit list
#: rather than a heuristic.
VALUE_FLAGS: frozenset[str] = frozenset({
    "--config-dir", "--marker-prefix", "--plugin-dir", "--depth", "--editor",
    "--log-mode", "--log-level", "--log-prefix", "--on-error", "--on-missing",
})


class UnknownMarker(ValueError):
    """A token looked like a plugin marker but named no known plugin."""

    def __init__(self, token: str, prefix: str, known: list[str]) -> None:
        import difflib

        self.token = token
        self.known = known
        markers = [f"{prefix}{normalize(name)}" for name in sorted(known)]
        listing = ", ".join(markers) or "(none installed)"

        # Case and separator slips are the common failure — '---autoScr' for
        # '---autoscript'. Matching case-insensitively would hide a real
        # distinction on a case-sensitive filesystem, so suggest instead.
        lowered = {marker.lower(): marker for marker in markers}
        suggestion = lowered.get(token.lower())
        if suggestion is None:
            close = difflib.get_close_matches(token.lower(), list(lowered), n=1, cutoff=0.6)
            suggestion = lowered[close[0]] if close else None
        hint = f"  did you mean: {suggestion}\n" if suggestion else ""

        super().__init__(
            f"unknown plugin marker {token!r}\n"
            f"{hint}"
            f"  known markers: {listing}\n"
            f"  if this was meant for amca itself, put it before the first "
            f"marker, or after a bare -- to stop marker parsing"
        )


@dataclass(slots=True)
class MarkerSplit:
    """Result of one pass over argv."""

    #: Tokens before the first marker — parsed by amca's own argparse.
    main: list[str] = field(default_factory=list)
    #: plugin name -> its argument list. Only names that appeared in argv.
    per_plugin: dict[str, list[str]] = field(default_factory=dict)
    #: The plugin whose section argv ends in, or None for amca's own section.
    #: Tab completion uses this to know whose arguments are being typed.
    current: str | None = None
    #: A bare ``--`` stopped marker scanning.
    passthrough: bool = False


def normalize(name: str) -> str:
    """Plugin folder ``auto_script`` is addressed as ``---auto-script``."""
    return name.replace("_", "-")


def split_argv(
    argv: list[str],
    known_plugins: list[str],
    prefix: str,
    *,
    strict: bool = True,
    value_flags: frozenset[str] = VALUE_FLAGS,
) -> MarkerSplit:
    """Partition *argv* into amca arguments and per-plugin arguments.

    Complexity: O(len(argv)) time, O(#plugins) extra space.

    Args:
        known_plugins: plugin folder names present on disk. Enablement is *not*
            considered here — a marker for an installed-but-disabled plugin is
            recognised and then reported by the caller, which is far clearer
            than pretending the token does not exist.
        strict: raise :class:`UnknownMarker` on an unrecognised marker. Set
            False only where argv is untrusted and best-effort is acceptable.
        value_flags: options whose following token is a value, not a marker.
    """
    if not prefix:
        raise ValueError("marker prefix must not be empty")

    marker_to_plugin = {f"{prefix}{normalize(name)}": name for name in known_plugins}

    result = MarkerSplit()
    current: list[str] | None = None
    expect_value = False
    passthrough = False

    for token in argv:
        target = result.main if current is None else current

        if expect_value:
            # Previous token was an option expecting a value; take this one
            # verbatim whatever it looks like.
            target.append(token)
            expect_value = False
            continue

        if token in value_flags:
            target.append(token)
            expect_value = True
            continue

        if token == END_OF_MARKERS and current is None and not passthrough:
            # Stop marker scanning, but keep the token: argparse needs its own
            # end-of-options marker to accept a value that starts with '-'.
            passthrough = True
            target.append(token)
            continue

        if not passthrough and token.startswith(prefix):
            plugin = marker_to_plugin.get(token)
            if plugin is None:
                remainder = token[len(prefix):]
                if not _PLUGIN_NAME.match(remainder):
                    # Not marker-shaped — an ordinary argument that happens to
                    # begin with the prefix.
                    target.append(token)
                    continue
                if strict:
                    raise UnknownMarker(token, prefix, known_plugins)
                # Non-strict: treat it as an ordinary argument of whatever
                # section we are currently in.
                target.append(token)
                continue
            current = result.per_plugin.setdefault(plugin, [])
            result.current = plugin
            continue

        target.append(token)

    result.passthrough = passthrough
    return result
