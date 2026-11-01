"""Execute the generated completion scripts, rather than only emitting them.

The existing coverage (e2e N1-N14) checks that a script is produced, is
non-empty, and calls amca by absolute path. Every one of those passed while
`amca <TAB>` offered plugin markers and no subcommands at all, because nothing
ever *ran* the shell function. These tests drive the real completion entry
points in a real shell.

Skipped when the shell is not installed, so CI on a minimal image stays green.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from amca.cli import completions

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not installed")

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def helper(tmp_path_factory: pytest.TempPathFactory) -> str:
    """An `amca` executable running this checkout against a throwaway config
    with meson and autoscript installed and the default marker prefix.

    The scripts call amca by absolute path; this is that path. Nothing here
    reads the developer's real config (a non-default marker prefix there used
    to fail these tests).
    """
    base = tmp_path_factory.mktemp("helper")
    env = {
        "PYTHONPATH": f"{REPO / 'src'}{os.pathsep}{REPO / 'plugins'}",
        "AMCA_CONFIG_DIR": str(base / "config"),
        "AMCA_STATE_DIR": str(base / "state"),
        "AMCA_NO_AUTO_COMPLETION": "1",
    }
    exports = "".join(f"export {key}={shlex.quote(value)}\n" for key, value in env.items())
    wrapper = base / "amca"
    wrapper.write_text(f"#!/bin/sh\n{exports}exec {shlex.quote(sys.executable)} -m amca.cli.amca_cli \"$@\"\n")
    wrapper.chmod(0o755)
    subprocess.run([sys.executable, "-m", "amca.cli.amcapl_cli", "install", "meson", "autoscript",
                    "--source", "builtin"], env={**os.environ, **env}, check=True,
                   capture_output=True)
    return str(wrapper)


@pytest.fixture(scope="module")
def script(helper: str) -> str:
    return completions.script_for("bash", helper=helper)


def _complete(script: str, line: str) -> list[str]:
    """Return what bash's `_amca`/`_amcapl` puts in COMPREPLY for *line*,
    the cursor at its end, as bash itself would call it."""
    func = "_amcapl" if line.split()[0] == "amcapl" else "_amca"
    harness = textwrap.dedent(
        f"""
        {script}
        COMP_LINE={shlex.quote(line)}
        COMP_POINT=${{#COMP_LINE}}
        read -ra COMP_WORDS <<< "$COMP_LINE"
        [[ "$COMP_LINE" == *" " ]] && COMP_WORDS+=("")
        COMP_CWORD=$(( ${{#COMP_WORDS[@]}} - 1 ))
        COMPREPLY=()
        {func}
        printf '%s\\n' "${{COMPREPLY[@]}}"
        """
    )
    out = subprocess.run(
        [BASH, "-c", harness], capture_output=True, text=True, check=True
    )
    return [line for line in out.stdout.splitlines() if line]


def test_script_is_syntactically_valid(script: str) -> None:
    subprocess.run([BASH, "-n"], input=script, text=True, check=True)


def test_bare_command_offers_subcommands_in_order(script: str) -> None:
    got = _complete(script, "amca ")
    commands = [c for c in got if not c.startswith("---")]
    assert commands[:3] == ["run", "r", "new"], "definition order, aliases next to their command"
    assert "doctor" in got and "plugins" in got
    assert "---meson" in got


def test_subcommand_values_are_not_shadowed_by_markers(script: str) -> None:
    got = _complete(script, "amca config ")
    assert "set" in got and "describe" in got
    assert not any(c.startswith("---") for c in got)


def test_prefix_narrows_to_markers(script: str) -> None:
    got = _complete(script, "amca ---")
    assert got == ["---autoscript", "---meson"]


def test_option_values_are_exact(script: str) -> None:
    assert _complete(script, "amca --log-mode ") == ["console", "file", "both", "silent"]


def test_option_equals_value(script: str) -> None:
    """bash breaks words at '='; the glue must still complete the value."""
    assert _complete(script, "amca --log-mode=f") == ["file"]


def test_plugin_flags_complete(script: str) -> None:
    got = _complete(script, "amca ---meson --sk")
    assert got == ["--skip"]
    assert "compile" in _complete(script, "amca ---meson ")


def test_amcapl_call_completes_the_plugins_arguments(script: str) -> None:
    assert _complete(script, "amcapl call meson --cl") == ["--clean", "--clear", "--clear-console"]


def test_compreply_does_not_leak_between_invocations(script: str) -> None:
    harness = textwrap.dedent(
        f"""
        {script}
        COMP_LINE="amca --log-mode "; COMP_POINT=${{#COMP_LINE}}
        COMP_WORDS=(amca --log-mode ""); COMP_CWORD=2; _amca
        COMP_LINE="amca con"; COMP_POINT=${{#COMP_LINE}}
        COMP_WORDS=(amca con);           COMP_CWORD=1; _amca
        printf '%s\\n' "${{COMPREPLY[@]}}"
        """
    )
    out = subprocess.run(
        [BASH, "-c", harness], capture_output=True, text=True, check=True
    )
    assert out.stdout.split() == ["config"]


def test_missing_helper_falls_back_to_files(tmp_path: Path) -> None:
    """amca uninstalled but the script left behind: plain file completion,
    no error text in the terminal."""
    script = completions.script_for("bash", helper=str(tmp_path / "gone"))
    harness = textwrap.dedent(
        f"""
        PATH=/nonexistent
        {script}
        COMP_LINE="amca "; COMP_POINT=5; COMP_WORDS=(amca ""); COMP_CWORD=1
        _amca; echo "status=$?"
        """
    )
    out = subprocess.run([BASH, "-c", harness], capture_output=True, text=True)
    assert "status=0" in out.stdout and out.stderr == ""


def test_fish_ships_a_separate_amcapl_file() -> None:
    """fish autoloads completions/<command>.fish, so amcapl needs its own."""
    assert completions._sibling_name("fish") == "amcapl.fish"
    pl = completions._pl_script_for("fish")
    assert "(__amca_complete amcapl)" in pl
    assert "@AMCA" not in pl, "placeholders must be substituted"


def test_zsh_binds_both_commands_from_one_file() -> None:
    assert completions.script_for("zsh").startswith("#compdef amca amcapl")
    assert completions._sibling_name("zsh") is None


# ── Installation: the rc block and the automatic installer ──────────────────
#
# These run against a throwaway HOME. The shell-driven ones start a real
# interactive bash/zsh there and check that `amca` ended up bound, which is the
# only evidence that matters: a file that exists but is never loaded completes
# nothing (the original zsh bug).

import json
from pathlib import Path

ZSH = shutil.which("zsh")


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "ZDOTDIR", "BASH_COMPLETION_USER_DIR",
                "FPATH", completions.ENV_DISABLE):
        monkeypatch.delenv(var, raising=False)
    return home


def _interactive(shell: str, home: Path, command: str) -> str:
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", ""), "TERM": "dumb"}
    out = subprocess.run([shell, "-ic", command], capture_output=True, text=True,
                         env=env, timeout=30)
    return out.stdout


def test_install_writes_one_block_and_keeps_the_rest(home: Path) -> None:
    rc = home / ".zshrc"
    rc.write_text("export MINE=1\n")
    first = completions.install("zsh")
    second = completions.install("zsh")
    text = rc.read_text()
    assert first.rc_patched and not second.rc_patched
    assert text.count(completions.RC_START) == 1
    assert text.startswith("export MINE=1\n")


def test_an_old_block_is_replaced_in_place(home: Path) -> None:
    rc = home / ".zshrc"
    rc.write_text(f"a=1\n{completions.RC_START}\nfpath=(/old $fpath)\n"
                  f"autoload -Uz compinit && compinit\n{completions.RC_END}\nb=2\n")
    completions.install("zsh")
    text = rc.read_text()
    assert "/old" not in text and text.count(completions.RC_START) == 1
    assert text.index("a=1") < text.index(completions.RC_START) < text.index("b=2")


def test_uninstall_removes_block_and_file(home: Path) -> None:
    rc = home / ".bashrc"
    rc.write_text("alias ll='ls -l'\n")
    completions.install("bash")
    removed, edited = completions.uninstall("bash")
    assert completions.target_path("bash") in removed and rc in edited
    assert rc.read_text() == "alias ll='ls -l'\n"


def test_auto_install_upgrades_the_old_plain_text_marker(home: Path, monkeypatch) -> None:
    """Machines set up before the rc block existed must fix themselves."""
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    state = home / "state"
    state.mkdir()
    (state / completions.MARKER_FILENAME).write_text("zsh\n")
    completions.target_path("zsh").parent.mkdir(parents=True)
    completions.target_path("zsh").write_text(completions.script_for("zsh"))

    message = completions.maybe_auto_install(state, interactive=True)
    assert message and ".zshrc" in message
    assert completions.is_active("zsh")
    assert completions.maybe_auto_install(state, interactive=True) is None


def test_auto_install_refreshes_when_content_changes(home: Path, monkeypatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/bash")
    state = home / "state"
    completions.maybe_auto_install(state, interactive=True)
    target = completions.target_path("bash")
    target.write_text("# stale script from an older amca\n")
    recorded = completions.load_state(state)
    completions.save_state(state, {**recorded, "fingerprint": "older"})

    assert completions.maybe_auto_install(state, interactive=True) is None, "refresh is silent"
    assert target.read_text() == completions.script_for("bash")


def test_auto_install_reinstalls_a_deleted_file(home: Path, monkeypatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/bash")
    state = home / "state"
    completions.maybe_auto_install(state, interactive=True)
    completions.target_path("bash").unlink()
    completions.maybe_auto_install(state, interactive=True)
    assert completions.is_installed("bash")


def test_auto_install_keeps_remembered_aliases(home: Path, monkeypatch) -> None:
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    state = home / "state"
    completions.save_state(state, {"names": ["a3"], "pl_names": ["ap"]})
    completions.maybe_auto_install(state, interactive=True)
    rc = (home / ".zshrc").read_text()
    assert "compdef _amca amca a3 amcapl ap=amcapl" in rc
    assert completions.target_path("zsh").read_text().startswith("#compdef amca a3 amcapl ap=amcapl")


def test_auto_install_respects_opt_out(home: Path, monkeypatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/bash")
    state = home / "state"
    completions.save_state(state, {"disabled": True})
    assert completions.maybe_auto_install(state, interactive=True) is None
    assert not completions.is_installed("bash")
    monkeypatch.setenv(completions.ENV_DISABLE, "1")
    completions.save_state(state, {})
    assert completions.maybe_auto_install(state, interactive=True) is None
    assert not completions.is_installed("bash")


def test_auto_install_ignores_unknown_shells(home: Path, monkeypatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/tcsh")
    state = home / "state"
    assert completions.maybe_auto_install(state, interactive=True) is None
    assert completions.load_state(state).get("shell") is None


def test_bash_block_works_without_bash_completion(home: Path) -> None:
    """Minimal images and macOS have no bash-completion to find the file."""
    completions.install("bash")
    out = _interactive(BASH, home, "complete -p amca amcapl")
    assert "-F _amca amca" in out and "-F _amcapl amcapl" in out


@pytest.mark.skipif(ZSH is None, reason="zsh not installed")
@pytest.mark.parametrize("user_rc", [
    "",                                                  # no completion system at all
    "autoload -Uz compinit && compinit\n",               # block lands after compinit
])
def test_zsh_block_binds_amca(home: Path, user_rc: str) -> None:
    (home / ".zshrc").write_text(user_rc)
    completions.install("zsh", names=["a3"], pl_names=["ap"])
    out = _interactive(ZSH, home, "print -r -- ${_comps[amca]} ${_comps[a3]} ${_comps[amcapl]} "
                                  "${_services[ap]}")
    assert out.split() == ["_amca", "_amca", "_amca", "amcapl"]


@pytest.mark.skipif(ZSH is None, reason="zsh not installed")
def test_resourcing_the_rc_file_drops_a_stale_script(home: Path) -> None:
    """A shell that loaded an older _amca must get the new one from
    `source ~/.zshrc`, not only from a new terminal."""
    completions.install("zsh")
    out = _interactive(ZSH, home, "_amca() { print stale }; source ~/.zshrc; "
                                  "functions _amca | grep -c stale")
    assert out.strip() == "0"


@pytest.mark.skipif(ZSH is None, reason="zsh not installed")
def test_zsh_block_before_users_compinit(home: Path) -> None:
    completions.install("zsh")
    rc = home / ".zshrc"
    rc.write_text(rc.read_text() + "autoload -Uz compinit && compinit\n")
    assert _interactive(ZSH, home, "print -r -- ${_comps[amca]}").split() == ["_amca"]


@pytest.mark.skipif(ZSH is None, reason="zsh not installed")
def test_zsh_block_is_inert_once_amca_is_gone(home: Path) -> None:
    """pip uninstall leaves the rc block behind; it must not break the shell."""
    completions.install("zsh")
    completions.target_path("zsh").unlink()
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", ""), "TERM": "dumb"}
    out = subprocess.run([ZSH, "-ic", "print ok"], capture_output=True, text=True, env=env)
    assert out.stdout.strip() == "ok" and "amca" not in out.stderr


def test_state_file_is_json(home: Path) -> None:
    state = home / "state"
    completions.save_state(state, {"shell": "bash"})
    assert json.loads((state / completions.MARKER_FILENAME).read_text()) == {"shell": "bash"}


# ── zsh, driven through a pseudo-terminal ───────────────────────────────────
#
# zsh completion only runs inside the line editor, so these start a real
# interactive zsh on a pty (zsh's own zpty module), press TAB, and read the
# listing it prints.

_ZPTY = r"""
zmodload zsh/zpty
zpty z env HOME="$HOME" PATH="$PATH" TERM=dumb COLUMNS=200 LINES=200 zsh -f
zpty -w z 'PS1="%% "; fpath=("$FPATH_DIR" $fpath); autoload -Uz compinit; compinit -i -D'
zpty -w z 'zstyle ":completion:*" format "[%d]"; zstyle ":completion:*" group-name ""'
zpty -w -n z "$LINE"$'\t'
out=""
for i in {1..100}; do
  while zpty -r -t z chunk; do out+=$chunk; done
  [[ $out == *"$UNTIL"* ]] && break
  sleep 0.1
done
print -r -- "$out"
zpty -d z
"""


def _zsh_tab(helper: str, tmp_path: Path, line: str, until: str) -> list[str]:
    fpath = tmp_path / "fpath"
    fpath.mkdir()
    (fpath / "_amca").write_text(completions.script_for("zsh", helper=helper))
    env = {**os.environ, "FPATH_DIR": str(fpath), "LINE": line, "UNTIL": until}
    out = subprocess.run([ZSH, "-f", "-c", _ZPTY], capture_output=True, text=True,
                         env=env, timeout=60).stdout
    import re

    text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", out).replace("\r", "")
    return [row.rstrip() for row in text.splitlines() if row.strip()]


@pytest.mark.skipif(ZSH is None, reason="zsh not installed")
def test_zsh_lists_commands_in_order_with_aliases(helper: str, tmp_path: Path) -> None:
    rows = _zsh_tab(helper, tmp_path, "amca ", "[plugin marker]")
    start = rows.index("[command]")
    assert rows[start + 1].split()[:2] == ["run,", "r"], rows
    assert rows[start + 2].split()[:2] == ["new,", "n"], rows
    assert "[plugin marker]" in rows


@pytest.mark.skipif(ZSH is None, reason="zsh not installed")
def test_zsh_completes_plugin_flags(helper: str, tmp_path: Path) -> None:
    rows = _zsh_tab(helper, tmp_path, "amca ---meson -", "--print-template")
    assert any(row.startswith("--skip, -n") for row in rows), rows
