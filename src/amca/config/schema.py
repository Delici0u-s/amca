"""The config schema — one declaration per setting, used for everything.

Every setting is declared exactly once here. Defaults, type coercion,
validation, ``--help`` text, the env-var name, and what ``amca config list``
prints are all derived from this table.

Why this matters: in the old design a setting had to be written out in four
separate places (``s.default(...)`` in config.py, an argparse flag, a branch in
``_apply_overrides``, and a read site). Adding one and forgetting one of the
four produced a key you could edit in JSON that quietly did nothing. Several
existed. Here, a key that is not in this table does not exist, and a key that
is in it is automatically readable, overridable, and listable.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

__all__ = ["SCHEMA", "Field", "FieldKind", "default_editor", "env_name_for"]

FieldKind = Literal["bool", "int", "str", "path", "choice", "list"]


class ConfigError(ValueError):
    """A value was rejected by the schema."""


@dataclass(frozen=True, slots=True)
class Field:
    key: str
    kind: FieldKind
    default: Any
    help: str
    choices: tuple[str, ...] = ()
    validate: Callable[[Any], None] | None = None
    #: Settings the user is not expected to hand-edit (managed by subcommands).
    managed_by: str = ""

    def coerce(self, raw: Any) -> Any:
        """Convert a value from JSON / env / CLI into the declared type.

        Raises ConfigError with a message naming the key, because the most
        common failure is a hand-edited JSON file and the user needs to know
        which line to fix.
        """
        try:
            if self.kind == "bool":
                if isinstance(raw, bool):
                    return raw
                if isinstance(raw, str):
                    low = raw.strip().lower()
                    if low in ("1", "true", "yes", "on"):
                        return True
                    if low in ("0", "false", "no", "off"):
                        return False
                raise ValueError(f"expected a boolean, got {raw!r}")
            if self.kind == "int":
                return int(raw)
            if self.kind in ("str", "path"):
                if not isinstance(raw, (str, os.PathLike)):
                    raise ValueError(f"expected a string, got {raw!r}")
                return str(raw)
            if self.kind == "choice":
                text = str(raw)
                if text not in self.choices:
                    raise ValueError(
                        f"expected one of {', '.join(self.choices)}, got {text!r}"
                    )
                return text
            if self.kind == "list":
                if isinstance(raw, str):
                    return [p for p in (s.strip() for s in raw.split(",")) if p]
                if isinstance(raw, Sequence):
                    return list(raw)
                raise ValueError(f"expected a list, got {raw!r}")
        except ConfigError:
            raise
        except Exception as exc:
            raise ConfigError(f"{self.key}: {exc}") from exc
        raise ConfigError(f"{self.key}: unknown field kind {self.kind!r}")

    def check(self, value: Any) -> None:
        if self.validate is not None:
            self.validate(value)


def env_name_for(key: str) -> str:
    """``plugins.marker_prefix`` -> ``AMCA_PLUGINS_MARKER_PREFIX``."""
    return "AMCA_" + key.upper().replace(".", "_")


def default_editor() -> str:
    return os.environ.get("VISUAL") or os.environ.get("EDITOR") or (
        "notepad" if os.name == "nt" else "nano"
    )


# ── Validators ───────────────────────────────────────────────────────────────

#: Flags amca's own parser owns. A plugin marker that collides with one of
#: these is unreachable, so the prefix is rejected at set-time rather than
#: producing a baffling argparse error later.
RESERVED_FLAGS = frozenset(
    {"-h", "--help", "--version", "--config-dir", "--debug", "--log-level",
     "--log-mode", "--plugin-dir", "--depth", "--editor", "--dry-run"}
)


def _validate_marker_prefix(value: Any) -> None:
    text = str(value)
    if not text:
        raise ConfigError("plugins.marker_prefix: must not be empty")
    if any(c.isspace() for c in text):
        raise ConfigError("plugins.marker_prefix: must not contain whitespace")
    if text in ("-", "--"):
        raise ConfigError(
            "plugins.marker_prefix: '-' and '--' collide with ordinary CLI flags; "
            "use something like '---', '+', or '@'"
        )


def _validate_folder_name(value: Any) -> None:
    text = str(value)
    if not text:
        raise ConfigError("root.folder_name: must not be empty")
    if "/" in text or "\\" in text:
        raise ConfigError(
            f"root.folder_name: must be a single directory name, not a path (got {text!r})"
        )


def _validate_non_negative(value: Any) -> None:
    if int(value) < 0:
        raise ConfigError("must be >= 0")


def _validate_depth(value: Any) -> None:
    if int(value) < 1:
        raise ConfigError("root.search_depth: must be >= 1")


# ── The table ────────────────────────────────────────────────────────────────

_FIELDS: tuple[Field, ...] = (
    Field("core.debug", "bool", False,
          "Print extra diagnostic output."),
    Field("core.greet", "bool", False,
          "Print a greeting on every invocation."),
    Field("core.editor", "str", "",
          "Editor for `amca args`. Empty means $VISUAL, then $EDITOR, then nano."),

    Field("root.folder_name", "str", ".amca",
          "Name of the marker directory that identifies an amca root.",
          validate=_validate_folder_name),
    Field("root.search_depth", "int", 5,
          "How many parent directories to walk while looking for a root.",
          validate=_validate_depth),
    Field("root.ask_to_create", "bool", True,
          "Offer to create a root when none is found (only ever asked on a TTY)."),
    Field("root.ignored_paths", "list", [],
          "Directories where the create-a-root prompt is suppressed.",
          managed_by="amca root ignore"),

    Field("log.mode", "choice", "console",
          "Where log output goes.",
          choices=("console", "file", "both", "silent")),
    Field("log.level", "choice", "INFO",
          "Minimum severity that is emitted.",
          choices=("INFO", "SUCCESS", "WARN", "ERROR", "FATAL")),
    Field("log.prefix", "choice", "none",
          "How much prefix decoration each log line carries.",
          choices=("none", "minimal", "simple", "normal", "verbose")),

    Field("plugins.dir", "path", "",
          "Directory holding installed plugins. Empty means <config dir>/plugins."),
    Field("plugins.enabled", "list", [],
          "Plugins that are allowed to run.",
          managed_by="amcapl enable / amcapl disable"),
    Field("plugins.marker_prefix", "str", "---",
          "Token prefix that routes the following arguments to a plugin, e.g. ---meson.",
          validate=_validate_marker_prefix),
    Field("plugins.on_error", "choice", "continue",
          "What to do when a plugin raises.",
          choices=("continue", "abort")),
    Field("plugins.on_missing", "choice", "warn",
          "What to do when an enabled plugin is not present on disk.",
          choices=("ignore", "warn", "abort")),
    Field("plugins.marker_scope", "choice", "selected",
          "When markers are given: 'selected' runs only the named plugins; "
          "'all' also runs every other applicable plugin.",
          choices=("selected", "all")),
    Field("plugins.announce_loaded", "bool", False,
          "Log a line each time a plugin is selected and run."),
    Field("completion.plugin_timeout_ms", "int", 1000,
          "How long tab completion waits for a plugin to reveal its arguments "
          "before giving up and offering file names. 0 disables plugin argument completion.",
          validate=_validate_non_negative),
    Field("plugins.sources", "list",
          ["github:Delici0u-s/amca@main:plugins/amca_presets"],
          "Where `amcapl install` looks for plugins. 'builtin' means the copies "
          "shipped inside the amca wheel; 'github:owner/repo@ref:path' is remote; "
          "an absolute path is a local directory."),
)

SCHEMA: dict[str, Field] = {f.key: f for f in _FIELDS}

# 'builtin' first: installing a preset should work with no network by default.
SCHEMA["plugins.sources"] = Field(
    "plugins.sources", "list",
    ["builtin", "github:Delici0u-s/amca@main:plugins/amca_presets"],
    SCHEMA["plugins.sources"].help,
)
