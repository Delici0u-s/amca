"""The completion engine (`amca __complete`), tested without a shell.

The shell glue is covered by test_completion_scripts.py; everything that
decides *what* to offer lives in amca.cli.complete and is tested here.
"""

from __future__ import annotations

import argparse
import textwrap
import time
from pathlib import Path

import pytest

from amca.cli import complete as engine
from amca.cli.amca_cli import build_parser
from amca.cli.amcapl_cli import build_parser as build_pl_parser
from amca.core.context import amcaContext


def _names(answer: engine.Answer, tag: str | None = None) -> list[str]:
    return [name for group in answer.groups if tag in (None, group.tag)
            for item in group.items for name in item.names]


# ── Environment: a throwaway config with plugins we control ─────────────────

@pytest.fixture()
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_dir = tmp_path / "config"
    monkeypatch.setenv("AMCA_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("AMCA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    return config_dir


def _plugin(config_dir: Path, name: str, body: str) -> None:
    folder = config_dir / "plugins" / name
    folder.mkdir(parents=True)
    (folder / "plugin.py").write_text(textwrap.dedent(body))


def _set(config_dir: Path, key: str, value: object) -> None:
    ctx = amcaContext(config_dir_override=str(config_dir))
    ctx.config.set_persistent(key, value)
    ctx.config.save()


PARSES_FIRST = """
    import argparse
    from amca.api import Plugin

    class {name}(Plugin):
        def should_load(self, ctx): return True
        def load(self, ctx):
            parser = argparse.ArgumentParser()
            parser.add_argument("--fast", action="store_true", help="go fast")
            parser.add_argument("target", choices=["debug", "release"])
            parser.parse_args(ctx.args)
            raise SystemExit("load() ran past parse_args during completion")
"""


# ── Core parsers ─────────────────────────────────────────────────────────────

def _all_parsers(parser: argparse.ArgumentParser, path: tuple[str, ...] = ()):
    yield path, parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            seen: set[int] = set()
            for name, sub in action.choices.items():
                if id(sub) not in seen:
                    seen.add(id(sub))
                    yield from _all_parsers(sub, (*path, name))


@pytest.mark.parametrize("prog, build", [("amca", build_parser), ("amcapl", build_pl_parser)])
def test_every_flag_of_every_core_parser_completes(config: Path, prog: str, build) -> None:
    """A flag added to any parser completes without touching completion code."""
    for path, parser in _all_parsers(build()):
        expected = {
            option for action in parser._actions
            if action.option_strings and action.help != argparse.SUPPRESS
            for option in action.option_strings
        }
        got = set(_names(engine.complete(prog, list(path), "-")))
        missing = expected - got
        assert not missing, f"{prog} {' '.join(path)}: {sorted(missing)} not offered"


def test_commands_keep_definition_order_and_carry_aliases(config: Path) -> None:
    answer = engine.complete("amca", [], "")
    commands = answer.groups[0]
    assert commands.tag == "commands"
    assert commands.items[0].names == ["run", "r"]
    assert commands.items[1].names == ["new", "n"]
    assert [item.names[0] for item in commands.items][-1] == "completions"


def test_options_only_when_a_dash_is_typed(config: Path) -> None:
    assert not _names(engine.complete("amca", [], ""), "options")
    assert "--debug" in _names(engine.complete("amca", [], "--"), "options")


def test_used_flags_and_mutually_exclusive_ones_drop_out(config: Path) -> None:
    offered = _names(engine.complete("amca", ["completions", "--install"], "-"))
    assert "--install" not in offered
    assert "--uninstall" not in offered, "same mutually exclusive group"
    assert "--rc" not in offered, "help=SUPPRESS stays hidden"
    assert "--no-rc" in offered


def test_option_values(config: Path) -> None:
    assert _names(engine.complete("amca", ["--log-mode"], "")) == ["console", "file", "both", "silent"]
    answer = engine.complete("amca", [], "--log-level=W")
    assert answer.prefix == "--log-level=" and "WARN" in _names(answer)
    assert engine.complete("amca", ["--config-dir"], "").fallback == engine.DIRS
    assert engine.complete("amca", ["--editor"], "").fallback == engine.COMMANDS
    assert engine.complete("amca", ["--depth"], "").empty, "an int takes no file names"


def test_config_keys_and_values(config: Path) -> None:
    keys = _names(engine.complete("amca", ["config", "get"], ""))
    assert "log.mode" in keys and "completion.plugin_timeout_ms" in keys
    assert _names(engine.complete("amca", ["config", "set", "log.mode"], "")) == [
        "console", "file", "both", "silent"]
    assert _names(engine.complete("amca", ["config", "set", "core.debug"], "")) == ["true", "false"]
    assert engine.complete("amca", ["config", "set", "core.editor"], "").empty


def test_unknown_subcommand_offers_nothing(config: Path) -> None:
    assert engine.complete("amca", ["frobnicate"], "").empty


# ── Markers and plugin arguments ─────────────────────────────────────────────

def test_markers_follow_the_configured_prefix(config: Path) -> None:
    _plugin(config, "alpha", PARSES_FIRST.format(name="alpha"))
    _set(config, "plugins.marker_prefix", "@@")
    assert _names(engine.complete("amca", [], "@@")) == ["@@alpha"]
    assert "@@alpha" in _names(engine.complete("amca", [], ""), "markers")
    assert not _names(engine.complete("amca", ["config"], ""), "markers"), \
        "markers only where amca accepts them unprompted"


def test_plugin_arguments_come_from_its_parser(config: Path) -> None:
    _plugin(config, "alpha", PARSES_FIRST.format(name="alpha"))
    assert _names(engine.complete("amca", ["---alpha"], "")) == ["debug", "release"]
    assert "--fast" in _names(engine.complete("amca", ["---alpha"], "-"))
    assert "--fast" not in _names(engine.complete("amca", ["---alpha", "--fast"], "-"))
    # Back in amca's own section after a second marker of another plugin? No:
    # everything after a marker is that plugin's, so amca's flags never show.
    assert "--debug" not in _names(engine.complete("amca", ["---alpha"], "-"))


def test_amcapl_call_completes_plugin_arguments(config: Path) -> None:
    _plugin(config, "alpha", PARSES_FIRST.format(name="alpha"))
    assert "alpha" in _names(engine.complete("amcapl", ["call"], ""))
    assert _names(engine.complete("amcapl", ["call", "alpha"], "")) == ["debug", "release"]
    assert "--fast" in _names(engine.complete("amcapl", ["call", "alpha"], "-"))


def test_parser_hook_is_used_without_running_load(config: Path) -> None:
    _plugin(config, "hooked", """
        import argparse
        from amca.api import Plugin

        class hooked(Plugin):
            def should_load(self, ctx): return True
            def load(self, ctx): raise SystemExit("load() must not run")
            def parser(self):
                parser = argparse.ArgumentParser()
                parser.add_argument("--out").complete = "dirs"
                return parser
    """)
    assert engine.complete("amca", ["---hooked", "--out"], "").fallback == engine.DIRS


def test_complete_hook_for_non_argparse_plugins(config: Path) -> None:
    _plugin(config, "custom", """
        from amca.api import Plugin

        class custom(Plugin):
            def should_load(self, ctx): return True
            def load(self, ctx): return 0
            def complete(self, ctx, words):
                return [("alpha", "first"), "beta"] if words[-1] != "x" else []
    """)
    assert _names(engine.complete("amca", ["---custom"], "")) == ["alpha", "beta"]


@pytest.mark.parametrize("body", [
    "print('noise'); import sys; print('more', file=sys.stderr); return 0",   # prints, never parses
    "raise RuntimeError('broken')",                                           # raises
    "import time; time.sleep(30)",                                            # hangs
    "import argparse; argparse.ArgumentParser().parse_args(['--x'])",         # parses badly? still captured
])
def test_misbehaving_plugins_never_break_completion(config: Path, body: str, capfd) -> None:
    _plugin(config, "bad", f"""
        from amca.api import Plugin

        class bad(Plugin):
            def should_load(self, ctx): return True
            def load(self, ctx):
                {body}
    """)
    _set(config, "completion.plugin_timeout_ms", 300)
    started = time.monotonic()
    answer = engine.complete("amca", ["---bad"], "")
    assert time.monotonic() - started < 5
    assert answer.fallback in (engine.FILES, engine.NOTHING)
    captured = capfd.readouterr()
    assert "noise" not in captured.out and "more" not in captured.err


def test_completion_opt_out_never_runs_load(config: Path, tmp_path: Path) -> None:
    flag = tmp_path / "ran"
    _plugin(config, "shy", f"""
        from pathlib import Path
        from amca.api import Plugin

        class shy(Plugin):
            completion = False
            def should_load(self, ctx): return True
            def load(self, ctx):
                Path({str(flag)!r}).write_text("x")
    """)
    assert engine.complete("amca", ["---shy"], "").fallback == engine.FILES
    assert not flag.exists()


def test_zero_timeout_disables_capture(config: Path) -> None:
    _plugin(config, "alpha", PARSES_FIRST.format(name="alpha"))
    _set(config, "completion.plugin_timeout_ms", 0)
    assert engine.complete("amca", ["---alpha"], "").fallback == engine.FILES


def test_only_the_active_plugin_is_imported(config: Path) -> None:
    _plugin(config, "alpha", PARSES_FIRST.format(name="alpha"))
    _plugin(config, "boom", "raise SystemExit('imported a plugin nobody asked for')\n")
    assert _names(engine.complete("amca", ["---alpha"], "")) == ["debug", "release"]


# ── Rendering and the entry point ────────────────────────────────────────────

def test_render_per_shell() -> None:
    answer = engine.Answer()
    answer.add("commands", "command", [engine.Item(["run", "r"], "run it"), engine.Item(["new"], "")])
    assert engine.render(answer, "bash", "r").splitlines() == ["none\t", "run", "r"]
    zsh = engine.render(answer, "zsh", "").splitlines()
    assert zsh[1] == "G\tcommands\tcommand"
    assert zsh[2] == "I\trun\trun, r  -- run it"
    assert zsh[3] == "A\tr"
    assert zsh[4] == "I\tnew\tnew   "
    fish = engine.render(answer, "fish", "").splitlines()
    assert fish[1:] == ["run\trun it", "r\trun: run it", "new\tcommand"]


def test_split_line_handles_quotes_and_trailing_space() -> None:
    assert engine._split_line("amca config ") == (["amca", "config"], "")
    assert engine._split_line("amca con") == (["amca"], "con")
    assert engine._split_line('amca "con') == (["amca"], "con")


def test_entry_point_never_raises(config: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(engine, "complete", lambda *a: 1 / 0)
    assert engine.main(["bash", "amca", "--line", "amca "]) == 0
    assert capsys.readouterr().out == "files\t\n"
