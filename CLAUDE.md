# amca

A directory-aware CLI: `amca` runs the plugins that apply to where you're standing, and `amcapl` manages them.
User-facing docs are in `README.md`, open work in `TODO.md`.

## Philosophy

**One source of truth.** Every value, setting, flag and list has exactly one home, and everything else reads it from
there. Settings are declared once in the config schema; help text, env vars, validation and listings follow from that
declaration. Anything derived (help, completion, docs hints) is generated from the real definitions, never typed out a
second time. If a change means writing the same fact in two places, change the design instead.

**No hardcoding.** Sizes come from content, behaviour from configuration, names from what's actually installed. Don't
assume anything about the user's prefix, paths, shell, plugins or environment. Read it, or make it a setting. Timeouts
and limits are settings too.

**Fail loudly, never silently.** A typo, an unknown name or a broken file is a clear error that says what was expected
and what to do next. It's never quietly dropped or guessed around. A message names the fix (a command to run, a key to
set), not only the problem.

**Plugins can't take amca down.** Plugin code is third-party code: amca catches at the boundary, reports cleanly and
carries on (or aborts, as configured). Plugins get one context object; the API only grows (new fields, optional hooks
with safe defaults) and is never renamed or removed. Old plugins keep working through adapters.

**Respect the user's machine.** Nothing writes outside amca's own directories without saying so, once, and it can be
undone and opted out of. Nothing prompts, writes or hits the network where the user wouldn't expect it (library import,
`--dry-run`, tab completion, non-interactive runs). Offline and local come first, remote second.

**Small dependency surface.** amca must start even when a third-party package is broken or missing. Optional
features degrade with a clear message.

## Conduct

- **Understand before changing.** Read the code involved and the reasoning in its comments. Many explain a past bug, and
  that bug is the reason the code looks the way it does. Keep those explanations; add your own *why* to non-obvious code.
- **TODO.md is the backlog.** Each item has its context, decisions and file pointers. Read the whole item first, and don't
  reopen a recorded decision unless the code shows it no longer holds. When done, tick it and note what's left.
- **Prove it works.** A change isn't done because it compiles or emits output. Exercise the real behaviour: run the
  command, drive the shell, test the failure path. Test at the extremes (empty, long, many, odd characters, broken input,
  misbehaving plugins), so that hidden assumptions fail a test. A fixed bug gets a regression test.
- **Tests never touch the real user environment.** Sandbox `HOME`, `ZDOTDIR`, `AMCA_CONFIG_DIR` and `AMCA_STATE_DIR`.
- **Ask before anything that can lose data**: rc files, config, installed plugins, `--force`, deletes, overwrites.
  Untracked files in the repo may be the user's work in progress, so ask before editing or committing them.
- **Report honestly.** Say what was verified and what wasn't, and name failures that already existed instead of
  folding them into the new work.
- **Commits:** one logical change each, with a message that explains why.

## Working in the repo

```
src/amca/            the application (api.py is the public plugin SDK)
plugins/amca_presets/ bundled plugins, also the reference examples of the plugin API
tests/               pytest suites, plus tests/e2e.py (own runner, real subprocesses)
```

The bundled plugins live in `plugins/`, so a source checkout needs both roots on the path:

```bash
PYTHONPATH=src:plugins python -m pytest -q
PYTHONPATH=$PWD/src:$PWD/plugins python tests/e2e.py     # absolute paths: it changes cwd
ruff check src tests plugins
mypy                                                     # strict; covers src and plugins
```

Installed plugins are copies in the user's config dir. Editing a preset in the repo changes nothing until
`amcapl update <name>`. An `amca` installed without `pip install -e` needs a reinstall to pick up code changes.
