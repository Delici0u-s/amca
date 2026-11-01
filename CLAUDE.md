# amca

A directory-aware CLI (`amca`, plus `amcapl` for plugin management). It runs the plugins that apply to the current
directory. Python ≥ 3.10, argparse, packaged with setuptools. User-facing docs are in `README.md`, the open work in
`TODO.md`.

## Working on TODO items

- `TODO.md` is the backlog. Each item has its context, the decisions already made and file pointers. Read the whole item
  and the files it names before proposing anything. Don't re-open a decision it records ("considered and rejected: …")
  unless the code shows it no longer holds.
- When an item is done, tick it in `TODO.md` and add what's left over under it. Done sections stay in the file as a short
  record (see "Shell completion setup (done …)").
- One TODO item per commit. Commit messages explain the *why*.

## Layout

```
src/amca/
  api.py                 plugin SDK: Plugin, PluginContext, PluginError. Public and stable; only add to it
  cli/amca_cli.py        `amca` entry point: build_parser(), marker split, `__complete` hook
  cli/amcapl_cli.py      `amcapl` entry point (install/enable/call plugins)
  cli/commands/          one module per `amca` subcommand, each with register(sub)
  cli/common.py          global flags (FLAG_TO_KEY -> config keys), pre_scan, first-run hook
  cli/complete.py        tab-completion engine (`amca __complete`): walks the live parsers
  cli/completions.py     completion install: shell glue scripts, rc block, state file
  config/schema.py       every setting, declared once (SCHEMA). A key not in it doesn't exist
  plugins/markers.py     split argv at plugin markers (prefix + folder name)
  plugins/registry.py    discovery, enablement, make_context, run loop
  plugins/loader.py      import plugin.py, legacy 2.x init.py adapter
  plugins/sources.py     where `amcapl install` fetches from (builtin / local dir / github)
plugins/amca_presets/    bundled plugins: meson, autoscript, template, example (reference for plugin authors)
tests/                   pytest suites + tests/e2e.py (own runner, real subprocesses)
```

Installed plugins are *copies* in `~/.config/amca/plugins/`. Editing `plugins/amca_presets/<name>` doesn't change
what the installed amca runs until `amcapl update <name>`.

## Commands

The bundled plugins live in `plugins/`, so the source checkout needs both roots on the path:

```bash
PYTHONPATH=src:plugins python -m pytest -q                        # unit + shell-driven tests, a few seconds
PYTHONPATH=$PWD/src:$PWD/plugins python tests/e2e.py              # e2e matrix (absolute paths: it changes cwd)
PYTHONPATH=$PWD/src:$PWD/plugins python tests/e2e.py -k markers -v
ruff check src tests plugins
mypy                                                              # strict, covers src and plugins
PYTHONPATH=src:plugins python -m amca.cli.amca_cli __complete zsh amca --line "amca config "   # one tab press
```

Known failures that already existed (don't chase them as part of other work):
- e2e `G12` (read-only config dir).
- mypy errors in `plugins/amca_presets/template/` (see below).
- e2e `N13` fails when no `amca` is on PATH, e.g. after `pip uninstall`.

The user installs with `pip install --user -e '.[all,dev]'`. Without `-e`, code changes need a reinstall to reach the
real `amca`.

## Conventions in this codebase

- **No hardcoding** (the user's global rule, applied hard here): values have one home (SCHEMA, a constant, an argparse
  definition) and are read from there. Example: completion reads the real parsers instead of listing flags, and the marker
  prefix always comes from config (`plugins.marker_prefix`), never a literal `---`. Timeouts and limits are settings.
- **Config changes** go through `config/schema.py` (`Field(...)`). Help text, env var, validation and `config list` follow
  from it.
- **CLI flags that map to config** go in `common.FLAG_TO_KEY`. They only change the session layer, never the file.
- **Plugin API**: only add to it (new attributes and optional hooks with safe defaults); never rename or remove. The
  legacy adapter in `loader.py` must keep 2.x plugins working.
- **Plugins must not crash amca**: catch at the plugin boundary, raise `PluginError` for clean messages, honour
  `ctx.dry_run`. `should_load` stays cheap (no subprocess, network or writes).
- **Shell completion**: hint argparse actions with `complete.steer(action, HINT)`. Never write flag lists into
  `completions.py`; the glue there only relays to `amca __complete`. Glue changes alter the fingerprint, and installed
  copies refresh themselves on the next interactive run.
- **Tests**: test at the extremes (long/many/odd input, broken plugins). Shell behaviour gets driven in a real shell
  (`tests/test_completion_scripts.py` has bash and zsh-on-a-pty helpers). Tests use throwaway config/HOME dirs and never
  the developer's real config.
- Comments explain *why* (often the bug that motivated the code). Keep that style; don't delete those explanations.

## Environment notes (this machine)

- The user's real config sets `plugins.marker_prefix` to `ä`, so markers look like `ämeson`. Anything that assumes `---`
  is a bug.
- Shell: zsh. bash is available. fish is **not** installed, so fish paths are untested.
- `plugins/amca_presets/template/`, `a/` and anything else untracked is the user's own work in progress. Ask before
  editing or committing it.
- Anything that could delete or overwrite data (rc files, `~/.config/amca`, installed plugins, `--force`) needs a
  confirmation first. Tests must sandbox `HOME`/`ZDOTDIR`/`AMCA_CONFIG_DIR`/`AMCA_STATE_DIR`.
