"""Execute the generated completion scripts, rather than only emitting them.

The existing coverage (e2e N1-N14) checks that a script is produced, is
non-empty, and calls amca by absolute path. Every one of those passed while
`amca <TAB>` offered plugin markers and no subcommands at all, because nothing
ever *ran* the shell function. These tests drive the real completion entry
points in a real shell.

Skipped when the shell is not installed, so CI on a minimal image stays green.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap

import pytest

from amca.cli import completions

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not installed")


def _complete(script: str, line: str) -> list[str]:
    """Return the candidates bash's `_amca`/`_amcapl` would offer for *line*.

    A trailing space means the cursor sits on a new, empty word — the case that
    the marker bug turned into "every marker and nothing else".
    """
    func = "_amcapl" if line.split()[0] == "amcapl" else "_amca"
    harness = textwrap.dedent(
        f"""
        {script}
        COMP_WORDS=({line})
        [[ "{line}" == *" " ]] && COMP_WORDS+=("")
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


@pytest.fixture(scope="module")
def script() -> str:
    return completions.script_for("bash")


def test_script_is_syntactically_valid() -> None:
    for shell in ("bash",):
        text = completions.script_for(shell)
        subprocess.run([BASH, "-n"], input=text, text=True, check=True)


def test_bare_command_offers_subcommands(script: str) -> None:
    """`amca <TAB>` must list commands.

    Regression: the marker loop used `[[ "$m" == "$cur"* ]]`, which every
    marker satisfies when $cur is empty, and then returned early.
    """
    got = _complete(script, "amca ")
    assert "config" in got
    assert "doctor" in got
    assert "plugins" in got


def test_subcommand_values_are_not_shadowed_by_markers(script: str) -> None:
    got = _complete(script, "amca config ")
    assert "set" in got
    assert "describe" in got


def test_prefix_still_narrows_to_markers(script: str) -> None:
    got = _complete(script, "amca ---")
    assert got, "markers must still be offered"
    assert all(candidate.startswith("---") for candidate in got)


def test_option_values_are_exact(script: str) -> None:
    assert sorted(_complete(script, "amca --log-mode ")) == [
        "both",
        "console",
        "file",
        "silent",
    ]


def test_compreply_does_not_leak_between_invocations(script: str) -> None:
    """The marker branch appends; bash does not clear COMPREPLY for us."""
    harness = textwrap.dedent(
        f"""
        {script}
        COMP_WORDS=(amca --log-mode ""); COMP_CWORD=2; _amca
        COMP_WORDS=(amca "");            COMP_CWORD=1; _amca
        printf '%s\\n' "${{COMPREPLY[@]}}"
        """
    )
    out = subprocess.run(
        [BASH, "-c", harness], capture_output=True, text=True, check=True
    )
    assert "console" not in out.stdout.split()


def test_falls_back_to_filenames(script: str) -> None:
    """Plugin arguments are usually paths; amca cannot know a plugin's flags."""
    assert "-o default" in script


def test_fish_ships_a_separate_amcapl_file() -> None:
    """fish autoloads completions/<command>.fish, so amcapl needs its own."""
    assert completions._sibling_name("fish") == "amcapl.fish"
    pl = completions._pl_script_for("fish")
    assert "complete -c amcapl" in pl
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
import os
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
