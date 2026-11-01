"""Shell completion for ``amca`` and ``amcapl``.

The scripts live here as string constants rather than as data files on disk.
That is deliberate: package data is easy to get wrong (the ``meson.build.template``
was silently dropped from an early wheel by a too-narrow glob), and a completion
script that is missing from an install is worse than none at all.

Everything dynamic — plugin names, markers, config keys — is fetched by calling
amca itself, so completions stay correct when you install a plugin or change
``plugins.marker_prefix`` without regenerating anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

__all__ = [
    "SHELLS",
    "InstallResult",
    "detect_shell",
    "fingerprint",
    "has_rc_block",
    "install",
    "install_hint",
    "is_active",
    "is_installed",
    "load_state",
    "maybe_auto_install",
    "rc_block",
    "rc_file",
    "remembered_names",
    "save_state",
    "script_for",
    "target_path",
    "uninstall",
]

RC_START = "# >>> amca completion >>>"
RC_END = "# <<< amca completion <<<"

SHELLS = ("bash", "zsh", "fish")

_BASH = r"""# amca bash completion.  Install with:
#   amca completions bash > ~/.local/share/bash-completion/completions/amca
#   cp that file to .../completions/amcapl as well

_amca_plugins()  { @AMCA@ plugins --names   2>/dev/null; }
_amca_markers()  { @AMCA@ plugins --markers 2>/dev/null; }
_amca_keys()     { @AMCA@ config list --keys 2>/dev/null; }

_amca_global_opts='--help --version --config-dir --debug --log-mode --log-level
--log-prefix --plugin-dir --depth --editor --marker-prefix --on-error --on-missing'

_amca() {
    local cur prev sub i candidates
    # bash does not clear COMPREPLY between invocations, and the marker branch
    # below appends. Without this, one completion leaks into the next.
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    # Option values are unambiguous: answer and stop.
    case "$prev" in
        --log-mode)    COMPREPLY=($(compgen -W 'console file both silent' -- "$cur")); return;;
        --log-level)   COMPREPLY=($(compgen -W 'INFO SUCCESS WARN ERROR FATAL' -- "$cur")); return;;
        --log-prefix)  COMPREPLY=($(compgen -W 'none minimal simple normal verbose' -- "$cur")); return;;
        --on-error)    COMPREPLY=($(compgen -W 'continue abort' -- "$cur")); return;;
        --on-missing)  COMPREPLY=($(compgen -W 'ignore warn abort' -- "$cur")); return;;
        --config-dir|--plugin-dir) COMPREPLY=($(compgen -d -- "$cur")); return;;
    esac

    sub=''
    for ((i=1; i<COMP_CWORD; i++)); do
        case "${COMP_WORDS[i]}" in
            run|r|new|n|remove|rm|root|args|a|config|doctor|plugins|completions)
                sub="${COMP_WORDS[i]}"; break;;
        esac
    done

    case "$sub" in
        config)
            # Only inside `config` do these words introduce a key.
            case "$prev" in
                get|set|unset|describe)
                    COMPREPLY=($(compgen -W "$(_amca_keys)" -- "$cur")); return;;
            esac
            candidates='list ls get set unset path edit describe migrate --origin --changed --keys';;
        args|a)      candidates="$(_amca_plugins) --show";;
        root)        candidates='show ignore unignore clear-ignored';;
        completions) candidates='bash zsh fish --install --uninstall --status --where --rc --command';;
        plugins)     candidates='--names --markers --enabled-only';;
        *)           candidates='run new remove root args config doctor plugins completions';;
    esac

    if [[ "$cur" == -* ]]; then
        candidates="$candidates $_amca_global_opts --dry-run"
    fi

    # Markers are valid at every position, but they must NOT suppress the
    # ordinary candidates: with an empty $cur every marker matches, which is
    # how `amca <TAB>` came to offer markers and nothing else.
    COMPREPLY=($(compgen -W "$candidates $(_amca_markers)" -- "$cur"))
}
complete -o bashdefault -o default -F _amca @NAMES@

_amcapl() {
    local cur prev
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    case "$prev" in
        enable|e|disable|d|toggle|t|uninstall|u|update|up|call|c)
            COMPREPLY=($(compgen -W "$(_amca_plugins)" -- "$cur")); return;;
        install|i)
            COMPREPLY=($(compgen -W "$(@AMCAPL@ list --available --names 2>/dev/null)" -- "$cur")); return;;
    esac
    if [[ "$cur" == -* ]]; then
        COMPREPLY=($(compgen -W "$_amca_global_opts --available --force --no-enable --source --yes" -- "$cur")); return
    fi
    COMPREPLY=($(compgen -W 'list enable disable toggle install uninstall update call' -- "$cur"))
}
complete -o bashdefault -o default -F _amcapl @PLNAMES@
"""

_ZSH = r"""#compdef @NAMES@ @PLNAMES@
# amca zsh completion.  Install with:
#   amca completions zsh > ~/.local/share/zsh/site-functions/_amca
# (any directory on $fpath works; run `compinit` afterwards)

_amca_plugin_names() { @AMCA@ plugins --names 2>/dev/null; }
_amca_marker_names() { @AMCA@ plugins --markers 2>/dev/null; }
_amca_config_keys()  { @AMCA@ config list --keys 2>/dev/null; }

_amca_global() {
    _arguments -C \
        '--config-dir[config directory for this run]:directory:_files -/' \
        '--debug[extra diagnostics and full tracebacks]' \
        '--log-mode[where log output goes]:mode:(console file both silent)' \
        '--log-level[minimum severity]:level:(INFO SUCCESS WARN ERROR FATAL)' \
        '--log-prefix[prefix decoration]:prefix:(none minimal simple normal verbose)' \
        '--plugin-dir[plugin directory]:directory:_files -/' \
        '--depth[root search depth]:n:' \
        '--editor[editor command]:cmd:_command_names -e' \
        '--marker-prefix[plugin marker prefix]:prefix:' \
        '--on-error[on plugin failure]:policy:(continue abort)' \
        '--on-missing[on missing plugin]:policy:(ignore warn abort)' \
        '--version[print version]' \
        '(-h --help)'{-h,--help}'[show help]'
}

_amca() {
    local -a markers subcommands
    markers=(${(f)"$(_amca_marker_names)"})
    subcommands=(
        'run:run applicable plugins'
        'new:create an amca root here'
        'remove:delete the amca root'
        'root:show or manage root detection'
        'args:edit per-project default arguments'
        'config:inspect or change settings'
        'doctor:check the installation'
        'plugins:list plugins and their markers'
        'completions:print a shell completion script'
    )

    # Markers are offered at every position: everything after one belongs to
    # the plugin, and amca cannot know that plugin's flags.
    if (( ${#markers} )); then
        _describe -t markers 'plugin marker' markers
    fi

    if (( CURRENT == 2 )); then
        _describe -t commands 'amca command' subcommands
        _amca_global
        return
    fi

    case ${words[2]} in
        config)
            if (( CURRENT == 3 )); then
                _values 'action' list ls get set unset path edit describe migrate
            elif [[ ${words[3]} == (get|set|unset|describe) ]] && (( CURRENT == 4 )); then
                local -a keys; keys=(${(f)"$(_amca_config_keys)"})
                _describe -t keys 'config key' keys
            fi
            ;;
        root)
            (( CURRENT == 3 )) && _values 'action' show ignore unignore clear-ignored
            ;;
        args|a)
            local -a plugins; plugins=(${(f)"$(_amca_plugin_names)"})
            _describe -t plugins 'plugin' plugins
            ;;
        completions)
            (( CURRENT == 3 )) && _values 'shell' bash zsh fish
            ;;
        *) _amca_global ;;
    esac
}

_amcapl() {
    local -a subcommands
    subcommands=(
        'list:show installed plugins'
        'enable:allow plugins to run'
        'disable:stop plugins from running'
        'toggle:flip plugins'
        'install:fetch plugins from a source'
        'uninstall:delete plugins from disk'
        'update:re-fetch installed plugins'
        'call:run one plugin directly'
    )
    if (( CURRENT == 2 )); then
        _describe -t commands 'amcapl command' subcommands
        _amca_global
        return
    fi
    case ${words[2]} in
        install|i)
            local -a available
            available=(${(f)"$(@AMCAPL@ list --available --names 2>/dev/null)"})
            _describe -t plugins 'available plugin' available
            ;;
        enable|e|disable|d|toggle|t|uninstall|u|update|up|call|c)
            local -a plugins; plugins=(${(f)"$(_amca_plugin_names)"})
            _describe -t plugins 'plugin' plugins
            ;;
        *) _amca_global ;;
    esac
}

case ${service:-$words[1]} in
    amcapl) _amcapl "$@" ;;
    *)      _amca   "$@" ;;
esac
"""

# fish autoloads completions/<command>.fish lazily, keyed on the command being
# completed. `amcapl` completions living inside amca.fish are therefore never
# read — the file is only sourced when you complete `amca`. Two files.
_FISH_COMMON = r"""function __amca_plugins;  @AMCA@ plugins --names   2>/dev/null; end
function __amca_markers;  @AMCA@ plugins --markers 2>/dev/null; end
function __amca_keys;     @AMCA@ config list --keys 2>/dev/null; end
"""

_FISH = _FISH_COMMON + r"""# amca fish completion -> ~/.config/fish/completions/amca.fish
function __amca_no_sub;   not __fish_seen_subcommand_from run r new n remove rm root args a config doctor plugins completions; end

complete -c amca -f
complete -c amca -n __amca_no_sub -a run         -d 'run applicable plugins'
complete -c amca -n __amca_no_sub -a new         -d 'create an amca root here'
complete -c amca -n __amca_no_sub -a remove      -d 'delete the amca root'
complete -c amca -n __amca_no_sub -a root        -d 'root detection'
complete -c amca -n __amca_no_sub -a args        -d 'per-project default arguments'
complete -c amca -n __amca_no_sub -a config      -d 'inspect or change settings'
complete -c amca -n __amca_no_sub -a doctor      -d 'check the installation'
complete -c amca -n __amca_no_sub -a plugins     -d 'list plugins and markers'
complete -c amca -n __amca_no_sub -a completions -d 'print a completion script'

complete -c amca -a '(__amca_markers)' -d 'plugin marker'
complete -c amca -n '__fish_seen_subcommand_from config; and __fish_seen_subcommand_from get set unset describe' -a '(__amca_keys)'
complete -c amca -n '__fish_seen_subcommand_from config' -a 'list get set unset path edit describe migrate'
complete -c amca -n '__fish_seen_subcommand_from root'   -a 'show ignore unignore clear-ignored'
complete -c amca -n '__fish_seen_subcommand_from args'   -a '(__amca_plugins)'
complete -c amca -n '__fish_seen_subcommand_from completions' -a 'bash zsh fish'

complete -c amca -l log-mode   -x -a 'console file both silent'
complete -c amca -l log-level  -x -a 'INFO SUCCESS WARN ERROR FATAL'
complete -c amca -l log-prefix -x -a 'none minimal simple normal verbose'
complete -c amca -l on-error   -x -a 'continue abort'
complete -c amca -l on-missing -x -a 'ignore warn abort'
complete -c amca -l config-dir -x -a '(__fish_complete_directories)'
complete -c amca -l plugin-dir -x -a '(__fish_complete_directories)'
complete -c amca -l debug   -d 'extra diagnostics'
complete -c amca -l dry-run -d 'show what would run'
"""

_FISH_PL = _FISH_COMMON + r"""# amcapl fish completion -> ~/.config/fish/completions/amcapl.fish
complete -c amcapl -f
complete -c amcapl -n 'not __fish_seen_subcommand_from list enable disable toggle install uninstall update call' \
    -a 'list enable disable toggle install uninstall update call'
complete -c amcapl -n '__fish_seen_subcommand_from install' -a '(@AMCAPL@ list --available --names 2>/dev/null)'
complete -c amcapl -n '__fish_seen_subcommand_from enable disable toggle uninstall update call' -a '(__amca_plugins)'
"""

_SCRIPTS = {"bash": _BASH, "zsh": _ZSH, "fish": _FISH}



def _executable(name: str) -> str:
    """Absolute path to an amca console script, when we can find one.

    The completion script shells out to amca for plugin names, markers and
    config keys. If it calls a bare `amca` that is not on PATH — the normal
    case for a venv or an alias like `a3=/tmp/venv/bin/amca` — every dynamic
    completion silently returns nothing and the whole thing looks broken.
    Baking in the absolute path of the interpreter that generated the script
    makes it work regardless of PATH.
    """
    import shutil
    import sys
    from pathlib import Path as _Path

    sibling = _Path(sys.executable).parent / name
    if sibling.is_file():
        return str(sibling)
    found = shutil.which(name)
    return found or name


def script_for(shell: str, *, names: Sequence[str] = (), pl_names: Sequence[str] = ()) -> str:
    """Render the completion script.

    *names* / *pl_names* add extra command names to register, for people who
    invoke amca through an alias or a wrapper.
    """
    try:
        template = _SCRIPTS[shell]
    except KeyError:
        raise ValueError(
            f"unsupported shell {shell!r} (supported: {', '.join(SHELLS)})"
        ) from None

    # A name may only be bound to one completion function; bash's `complete -F`
    # is last-write-wins and zsh's #compdef would list it twice.
    all_names, all_pl_names = _split_names(names, pl_names)
    if shell == "zsh":
        all_pl_names = _zsh_pl_bindings(all_pl_names)
    return (
        template
        .replace("@AMCAPL@", _executable("amcapl"))
        .replace("@AMCA@", _executable("amca"))
        .replace("@PLNAMES@", " ".join(all_pl_names))
        .replace("@NAMES@", " ".join(all_names))
    )


def _zsh_pl_bindings(pl_names: Sequence[str]) -> list[str]:
    """zsh binds an alias with ``name=service``; the script dispatches on the
    service, so an amcapl alias must carry ``=amcapl`` or it completes as amca."""
    return [n if n == "amcapl" else f"{n}=amcapl" for n in pl_names]


def _sibling_name(shell: str) -> str | None:
    """Second filename this shell autoloads for ``amcapl``, if any.

    zsh is None because a single ``#compdef amca amcapl`` line binds both.
    """
    return {"bash": "amcapl", "fish": "amcapl.fish"}.get(shell)


def _pl_script_for(shell: str, *, names: Sequence[str] = (), pl_names: Sequence[str] = ()) -> str:
    """Content for the amcapl sibling file. Identical to the main script for
    bash (one file declares both `complete -F`), separate for fish."""
    if shell != "fish":
        return script_for(shell, names=names, pl_names=pl_names)
    return (
        _FISH_PL
        .replace("@AMCAPL@", _executable("amcapl"))
        .replace("@AMCA@", _executable("amca"))
    )


def install_hint(shell: str) -> str:
    """Manual steps equivalent to ``amca completions --install``.

    Built from the same paths and block the installer uses, so the two cannot
    drift apart.
    """
    path = target_path(shell)
    steps = [f"mkdir -p {shlex.quote(str(path.parent))}",
             f"amca completions {shell} > {shlex.quote(str(path))}"]
    sibling = _sibling_name(shell)
    if sibling is not None:
        # fish's amcapl file differs from amca's; bash's is the same script.
        steps.append(f"# and the same for amcapl, into {path.with_name(sibling)}")
    block = rc_block(shell)
    rc = rc_file(shell)
    if block and rc:
        steps.append(f"# then add to {rc}:")
        steps.extend(f"#   {line}" for line in block.splitlines())
    steps.append(f"exec {shell}")
    return "\n  ".join(steps)


# ── Automatic installation ───────────────────────────────────────────────────
#
# Why this exists at all: a wheel cannot run code at install time (PEP 427), so
# `pip install amca` physically cannot register a completion script the way an
# RPM or DEB can. Every Python CLI with "automatic" completion is really doing
# one of three things — shipping a distro package, asking you to put a line in
# your rc file, or writing the file itself on first run. amca does the last two
# for you, because only that combination works on every machine:
#
#   * fish reads ~/.config/fish/completions on its own. File only.
#   * bash reads ~/.local/share/bash-completion/completions only when the
#     bash-completion package is installed *and* loaded by the rc file. That is
#     true in most distros' default setup and false on minimal images, macOS and
#     many hand-written rc files. An rc block that sources the file makes it
#     independent of bash-completion; when bash-completion is present too, the
#     function is simply registered once more.
#   * zsh has no per-user directory it reads by default at all. Every user-level
#     install (pipx, uv, cargo, Homebrew) needs an rc line; amca writes it.
#
# The rules we hold ourselves to:
#   * write only into the standard per-user completion directories, and into
#     the rc file only as one delimited block that we own and can replace;
#   * the rc block works wherever it sits, before or after the user's own
#     compinit, and does nothing once the completion file is gone;
#   * say what was done, once; refresh silently when amca is upgraded;
#   * be trivially disabled (AMCA_NO_AUTO_COMPLETION=1) and trivially undone
#     (amca completions --uninstall, which also stops it coming back).

import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path

ENV_DISABLE = "AMCA_NO_AUTO_COMPLETION"

#: Records what the automatic installer last did (shell, amca version, aliases,
#: opt-out), so it acts once per shell and once per amca upgrade.
MARKER_FILENAME = "completion-installed"

_RC_NOTE = "# Managed by amca. Remove with `amca completions --uninstall`."


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / default


def target_path(shell: str) -> Path:
    """Where this shell looks for a per-user completion file."""
    if shell == "bash":
        user_dir = os.environ.get("BASH_COMPLETION_USER_DIR")
        base = Path(user_dir) if user_dir else _xdg("XDG_DATA_HOME", ".local/share") / "bash-completion"
        return base / "completions" / "amca"
    if shell == "zsh":
        return _xdg("XDG_DATA_HOME", ".local/share") / "zsh" / "site-functions" / "_amca"
    if shell == "fish":
        return _xdg("XDG_CONFIG_HOME", ".config") / "fish" / "completions" / "amca.fish"
    raise ValueError(f"unsupported shell {shell!r}")


def rc_file(shell: str) -> Path | None:
    """The rc file an interactive *shell* reads, or None when it needs none."""
    if shell == "zsh":
        zdotdir = os.environ.get("ZDOTDIR")
        return (Path(zdotdir) if zdotdir else Path.home()) / ".zshrc"
    if shell == "bash":
        return Path.home() / ".bashrc"
    return None


def rc_block(shell: str, *, names: Sequence[str] = (), pl_names: Sequence[str] = ()) -> str | None:
    """The delimited block amca keeps in *shell*'s rc file, or None for fish."""
    path = target_path(shell)
    quoted = shlex.quote(str(path))
    if shell == "bash":
        body = f"[[ -r {quoted} ]] && . {quoted}"
    elif shell == "zsh":
        amca_names, amcapl_names = _split_names(names, pl_names)
        # `name=amcapl` sets the service, which is what the script dispatches on.
        bindings = " ".join(map(shlex.quote, [*amca_names, *_zsh_pl_bindings(amcapl_names)]))
        body = (
            f"if [[ -r {quoted} ]]; then\n"
            f"  fpath=({shlex.quote(str(path.parent))} $fpath)\n"
            # Before the user's own compinit this starts the completion system
            # early (theirs then runs again, harmlessly); after it, it is a no-op.
            f"  (( $+functions[compdef] )) || {{ autoload -Uz compinit && compinit -i; }}\n"
            f"  autoload -Uz {path.name} && compdef {path.name} {bindings}\n"
            f"fi"
        )
    else:
        return None
    return f"{RC_START}\n{_RC_NOTE}\n{body}\n{RC_END}\n"


def _split_names(names: Sequence[str], pl_names: Sequence[str]) -> tuple[list[str], list[str]]:
    amca_names = list(dict.fromkeys(["amca", *names]))
    amcapl_names = [n for n in dict.fromkeys(["amcapl", *pl_names]) if n not in amca_names]
    return amca_names, amcapl_names


def detect_shell() -> str | None:
    """Guess the user's shell from $SHELL. None when it is not one we support."""
    name = Path(os.environ.get("SHELL", "")).name
    return name if name in SHELLS else None


def is_installed(shell: str) -> bool:
    try:
        return target_path(shell).is_file()
    except ValueError:
        return False


def has_rc_block(shell: str) -> bool:
    rc = rc_file(shell)
    if rc is None:
        return False
    try:
        return RC_START in rc.read_text(encoding="utf-8")
    except OSError:
        return False


def is_active(shell: str) -> bool:
    """True when a new *shell* will load the completion file.

    fish needs only the file. bash and zsh need amca's rc block; for zsh a
    directory already on an exported ``$FPATH`` also counts. (bash without the
    block may still work through bash-completion, but that cannot be checked
    from outside bash, so it is not claimed.)
    """
    if not is_installed(shell):
        return False
    if rc_file(shell) is None:
        return True
    if has_rc_block(shell):
        return True
    return shell == "zsh" and str(target_path(shell).parent) in os.environ.get("FPATH", "").split(":")


@dataclass(frozen=True, slots=True)
class InstallResult:
    shell: str
    path: Path
    written: bool
    rc_patched: bool = False
    rc_file: Path | None = None
    note: str = ""


def _strip_block(text: str) -> tuple[str, bool]:
    """*text* without amca's block. Returns (text, whether a block was found)."""
    if RC_START not in text or RC_END not in text:
        return text, False
    head, _, rest = text.partition(RC_START)
    _, _, tail = rest.partition(RC_END)
    head = head.rstrip("\n")
    tail = tail.lstrip("\n")
    joined = head + ("\n" if head else "") + tail
    return joined, True


def _write_rc_block(shell: str, block: str) -> tuple[bool, Path | None]:
    """Put *block* into the rc file: replace ours in place, else append.

    Returns (changed, rc path). Replacing in place keeps the block where the
    user may have moved it, and upgrades an older block (the first version
    re-ran compinit) without leaving a second one behind.
    """
    rc = rc_file(shell)
    if rc is None:
        return False, None
    try:
        existing = rc.read_text(encoding="utf-8") if rc.exists() else ""
    except OSError:
        return False, rc
    if block in existing:
        return False, rc
    if RC_START in existing and RC_END in existing:
        head, _, rest = existing.partition(RC_START)
        _, _, tail = rest.partition(RC_END)
        updated = head + block + tail.removeprefix("\n")
    else:
        separator = "" if not existing or existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        updated = existing + separator + block
    try:
        rc.write_text(updated, encoding="utf-8")
    except OSError:
        return False, rc
    return True, rc


def install(
    shell: str,
    *,
    patch_rc: bool = True,
    names: Sequence[str] = (),
    pl_names: Sequence[str] = (),
) -> InstallResult:
    """Write the completion script, and (by default) the rc block that loads it."""
    path = target_path(shell)
    script = script_for(shell, names=names, pl_names=pl_names)
    written = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != script:
            path.write_text(script, encoding="utf-8")
            written = True
        # bash-completion and fish both autoload by command name, so amcapl
        # needs its own file. A copy rather than a symlink: some filesystems
        # and backup tools do not carry symlinks well, and the file is tiny.
        sibling_name = _sibling_name(shell)
        if sibling_name is not None:
            sibling = path.with_name(sibling_name)
            pl_script = _pl_script_for(shell, names=names, pl_names=pl_names)
            if not sibling.exists() or sibling.read_text(encoding="utf-8") != pl_script:
                sibling.write_text(pl_script, encoding="utf-8")
    except OSError as exc:
        return InstallResult(shell, path, False, note=f"could not write: {exc}")

    rc_patched, rc = False, rc_file(shell)
    block = rc_block(shell, names=names, pl_names=pl_names)
    if patch_rc and block is not None:
        rc_patched, rc = _write_rc_block(shell, block)

    note = ""
    if not is_active(shell) and rc is not None:
        note = (
            f"{shell} will not load {path} until {rc} does. Let amca add that:\n"
            f"    amca completions --install\n"
            f"  or add this block yourself:\n"
            + "\n".join(f"    {line}" for line in (block or "").splitlines())
        )
    return InstallResult(shell, path, written, rc_patched, rc, note)


def uninstall(shell: str) -> tuple[list[Path], list[Path]]:
    """Remove the completion file(s) and amca's rc block.

    Returns (deleted files, edited files) — reporting an rc file we merely
    edited as "removed" reads like amca deleted someone's ~/.zshrc.
    """
    removed: list[Path] = []
    edited: list[Path] = []
    path = target_path(shell)
    sibling_name = _sibling_name(shell)
    for candidate in (path, path.with_name(sibling_name) if sibling_name else None):
        if candidate is not None and candidate.exists():
            try:
                candidate.unlink()
                removed.append(candidate)
            except OSError:
                pass

    rc = rc_file(shell)
    if rc is not None:
        try:
            text = rc.read_text(encoding="utf-8")
        except OSError:
            return removed, edited
        stripped, found = _strip_block(text)
        if found:
            try:
                rc.write_text(stripped, encoding="utf-8")
                edited.append(rc)
            except OSError:
                pass
    return removed, edited


# ── Installer state ──────────────────────────────────────────────────────────

def load_state(state_dir: Path) -> dict[str, Any]:
    """What the installer recorded last time. {} when nothing was recorded.

    The first version of the marker held only the shell name as plain text;
    that reads as a state with no fingerprint, so the next run upgrades it.
    """
    try:
        text = (state_dir / MARKER_FILENAME).read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        shell = text.strip()
        return {"shell": shell if shell in SHELLS else None}
    return data if isinstance(data, dict) else {}


def save_state(state_dir: Path, state: dict[str, Any]) -> bool:
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / MARKER_FILENAME).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def fingerprint(shell: str, *, names: Sequence[str] = (), pl_names: Sequence[str] = ()) -> str:
    """Short hash of everything :func:`install` would write for *shell*."""
    import hashlib

    parts = [
        script_for(shell, names=names, pl_names=pl_names),
        _pl_script_for(shell, names=names, pl_names=pl_names),
        rc_block(shell, names=names, pl_names=pl_names) or "",
        str(target_path(shell)),
        str(rc_file(shell)),
    ]
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:16]


def remembered_names(state: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Alias names registered earlier, so a refresh does not drop them."""
    def _list(key: str) -> list[str]:
        value = state.get(key)
        return [str(v) for v in value] if isinstance(value, list) else []
    return _list("names"), _list("pl_names")


def maybe_auto_install(state_dir: Path, *, interactive: bool) -> str | None:
    """Run on every interactive invocation. Returns a message to show, or None.

    Cheap when there is nothing to do (one small file read and rendering a few
    strings). It acts when the shell in $SHELL or the content amca would write
    differs from what was recorded: the first run, a switch to another shell,
    and any upgrade that changes the script or rc block (or moves amca), so
    they never go stale. Keyed on the content, not the version number, so a
    release that forgets a version bump still refreshes. It speaks only when it changed
    something the user should know about (a new file for a new shell, or an rc
    edit). It never acts after `amca completions --uninstall`, and failures are
    silent: completion is a nicety and must not be able to break a run.
    """
    if os.environ.get(ENV_DISABLE) or not interactive:
        return None

    state = load_state(state_dir)
    if state.get("disabled"):
        return None
    shell = detect_shell()
    names, pl_names = remembered_names(state)
    current = fingerprint(shell, names=names, pl_names=pl_names) if shell else None
    if state.get("shell") == shell and state.get("fingerprint") == current and (
        shell is None or is_installed(shell)
    ):
        return None

    new_state = {**state, "shell": shell, "fingerprint": current}
    new_state.pop("version", None)
    if shell is None:
        save_state(state_dir, new_state)
        return None

    first_for_shell = state.get("shell") != shell or not is_installed(shell)
    result = install(shell, names=names, pl_names=pl_names)
    if not save_state(state_dir, new_state):
        return None       # cannot record it, so do not risk repeating the message
    if result.note.startswith("could not write"):
        return None
    if not (result.rc_patched or (first_for_shell and result.written)):
        return None

    message = f"amca: set up {shell} tab-completion in {result.path}"
    if result.rc_patched and result.rc_file:
        message += f"\n  and added a block that loads it to {result.rc_file}"
    message += (
        f"\n  Open a new terminal (or run `exec {shell}`) to use it."
        f"\n  Undo with `amca completions --uninstall`; disable with {ENV_DISABLE}=1."
    )
    return message
