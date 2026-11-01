# TODO

## Background: what the rewrite removed

The AI rewrite dropped some functionality. For reference:

| Commit | Date | What happened |
|---|---|---|
| `448f641` "test" | 2026-08-23 | The rewrite. Added the new meson plugin, which only accepts template `2.0.1`. |
| `ae1b99c` ":pray:" | 2026-08-24 | Deleted `preset_plugins/meson/impl/version_implementations/` (`VERSION_MAP` with `v1`, `v2_0_1` and `template`). |

To see the old code: `git show ae1b99c^:preset_plugins/meson/impl/version_implementations/v1/__init__.py`
(also look at `v1/impl/{args,cache,clipboard,ide}.py` and `../__init__.py` for `VERSION_MAP`).

---

## Meson plugin

### Bring back version routing
The old `init.py` read `amca_var__meson__version_behaviour` and routed to a matching implementation.
Now `_impl/project.py` accepts `2.0.1` and nothing else.

- [ ] Add a `version → handler` map in `_impl/project.py` or `plugin.py`, replacing `SUPPORTED_TEMPLATE_VERSIONS`.
      A handler parses `meson.build` into a `MesonProject` and can adjust options or steps for that version.
- [ ] A missing `version_behaviour` routes to `v1`, as before. It currently fails with "predates the amca template".
- [ ] Keep `2.0.1` as the current handler. New template versions get their own entry instead of changing the meaning of an old one.
- [ ] Error for an unknown version: list the supported versions and point to `--print-template`. Mostly done already.
- [ ] Optional: bring back `"bunny"` → easter egg.

### Support v1 projects
v1 `meson.build` files have no version variable and use these names instead:

| v1 name | maps to |
|---|---|
| `build_dir_where` | `build_dir` (relative to meson.build) |
| `output_dir` | install dir. Note: v1 also defined it relative to meson.build and then rewrote it to `project_build_root() + '/' + output_dir`, so it ends up being relative to the build dir, same as 2.0.1. Check this against a real v1 file. |
| `output_name` | `executable_name` |

- [ ] v1 parser that fills `MesonProject` from these names.
- [ ] v1-only behaviour, which can live as options set by the v1 handler:
  - [ ] `-m`: copy `cd <exe dir>` + `<exe> <args>` to the clipboard instead of running it (wl-copy → xclip → pbcopy → clip).
  - [ ] the `-nc`, `-ne` and `-ni` flags, mapped onto `-n c`, `-n e`, `-n i`
  - [ ] the v1 source cache was `.sources_cache` in the project root. 2.0.1 uses `.amca_sources_cache` in the plugin dir. Either read the old file once or ignore it; a single extra reconfigure doesn't matter.
  - [ ] v1 `-s` = `setup --wipe` (see below)
- [ ] Update the tests that currently require v1 to fail: `tests/test_plugins.py:185` and `tests/e2e.py:1372`.
- [ ] Bring back a v1 fixture project. The old one is `git show 448f641^:tests/plugins/meson/test2/meson.build`.

### Bring back `-s` = `meson setup --wipe`
Old v1: `-s` ran `meson setup <build> --wipe`, which rebuilds the configuration but keeps the build dir.
Now `-s` means "delete everything, then run the full pipeline", which is much slower.

- [ ] Add a wipe option. Proposal: `-w/--wipe` runs `setup --wipe` instead of skipping setup when the build dir already exists.
      Needs a decision: should `-s` itself become wipe again (as in v1), with full delete moving to `--clean` + pipeline?
      The v1 handler should map `-s` to wipe either way.
- [ ] Wipe must also re-sync IDE configs and re-seed the source cache, like v1 did.

---

## autoscript plugin
Removed in the rewrite (old code: `git show 448f641^:preset_plugins/autoScr/init.py`, `impl/args.py`, `impl/create.py`):

- [ ] `n` / `new` as a positional alias for `--new`.
      Caveat: this hides a script argument literally named `new`. `--` already forwards the rest unchanged, so mention that in the help.
- [ ] `-y/--yes`: create with the platform default (`.sh` / `.ps1`) without asking. Today it only skips the prompt when stdin isn't a TTY.
- [ ] Verbose flag (old: `--verbose_auto_scr`). Make it `-v/--verbose` before `--`, and log the command it runs. The `ctx.log.log` call in `plugin.py` is currently commented out.
- [ ] Old behaviour: if the amca root already had a script, warn and create one in the working dir anyway. Check that the current `--new` matches.

### `.py` auto scripts
Allow `amca_auto_script.py` next to the shell ones, on every OS (it's the one script type that runs on both Linux and Windows).
Today `_impl/scripts.py` only knows `.sh/.bash/.zsh` (POSIX) and `.ps1/.bat/.cmd` (Windows).

- [ ] Add `.py` to `INTERPRETERS` on both platforms, so it's in `CANDIDATES`, the `--new` picker and the "multiple auto scripts" check.
- [ ] Which Python runs it. **Not** `sys.executable`: that's amca's own (pipx/uv) venv, which lacks the project's packages.
      Proposal, first hit wins:
  1. a config setting (e.g. `autoscript.python`) or a `.amca/args/autoscript.args` flag, for anyone who needs to pin it;
  2. the active `$VIRTUAL_ENV`;
  3. a venv in the project root, found by looking for `pyvenv.cfg` in its subdirectories (no fixed `.venv` / `venv` name);
  4. `python3` on PATH (POSIX), or `py -3`, then `python` (Windows).
  - [ ] Log which interpreter was picked with `-v` (see the verbose flag above), and show it in `--dry-run`.
  - [ ] Clear error when none is found, saying how to set one.
- [ ] Template for `--new`: shebang `#!/usr/bin/env python3`, `sys.argv[1:]` handling, and an `if __name__ == "__main__":` block.
- [ ] The extension-less `amca_auto_script` on POSIX already works with a Python shebang. Leave that alone.
- [ ] Tests: run a `.py` script with arguments, venv detection (a fake `pyvenv.cfg`), the "no Python found" error, and `.py` + `.sh` in one dir raising the ambiguity error.
- [ ] Mention it in the plugin docstring, `HELP_TEXT` and the README.

---

## template plugin: more default templates
Defaults live in `plugins/amca_presets/template/defaults/`. Add a folder, then an entry in `index.json`. Ideas:

- [ ] `cpp-project`: same as `c-project`, but `['cpp']`, `cpp_std=c++20`, `src/main.cpp`
- [ ] `c-project-ide`: `c-project` plus `.clangd` (`CompilationDatabase: build`), `.clang-format`, `.vscode/{launch,tasks,settings}.json`. The meson plugin already keeps `launch.json` and `.clangd` in sync. See the old `tests/plugins/meson/test2` (`git show 448f641^:tests/plugins/meson/test2/.clangd` etc.)
- [ ] `autoscript-sh` / `autoscript-ps1`: an `amca_auto_script` starter with argument handling
- [ ] `python-project`: `pyproject.toml`, `src/<pkg>/__init__.py`, `tests/`, `.gitignore`
- [ ] `amca-plugin`: skeleton for a new plugin (`plugin.py`, `_impl/__init__.py`, README), based on `example`
- [ ] Once v1 support is back: optionally a `c-project-v1` for testing old projects

---

## amca core

### Personal plugin aliases
Let a user type `---as` instead of `---autoscript`, for themselves only. The original marker keeps working.

- [ ] Config key `plugins.aliases`: a map `alias → plugin folder name`, e.g. `as=autoscript,ms=meson`.
      It's user config, so it never ships with the plugin and never affects anyone else.
- [ ] `markers.split_argv`: add alias markers to `marker_to_plugin`, resolving to the real name.
      Everything after that (args, `.amca/args/<plugin>.args`, enable state) keys on the real name and needs no change.
- [ ] Validation on `config set`:
  - [ ] an alias can't shadow an installed plugin's name
  - [ ] two aliases can't share a name
  - [ ] the target has to exist (warn, don't fail, since the plugin might be installed later)
  - [ ] the same `_PLUGIN_NAME` rule as markers
- [ ] Show aliases in `amca plugins --markers`, the `UnknownMarker` "did you mean", shell completion
      (`_Session.marker_items` in `cli/complete.py`) and `doctor`.
- [ ] Maybe: aliases for subcommands of a single plugin (`---as` → `---autoscript --new`). Probably not needed.

### Let plugins know they were called by name
**Status: not implemented.** `split_argv` does record a bare marker (`per_plugin.setdefault(plugin, [])`),
and the registry knows `name in args_by_plugin`, but `PluginContext` only gets `args`.
So plugins guess with `bool(ctx.args)`
(`template/plugin.py:38`, `meson/plugin.py`, `autoscript/plugin.py`), which has two bugs:

1. `amca ---template` with no args → `args == []` → `should_load` is False → it never runs, and you get "arguments given but should_load() returned False".
   A bare marker can't open a help or picker screen.
2. `ctx.args` already includes the per-project defaults from `.amca/args/<plugin>.args` (`registry.make_context`),
   so a project with defaults makes `bool(ctx.args)` True **without the marker**. `template` would then run on a plain `amca`, which its docstring says must never happen.

- [ ] Add one field to `PluginContext`: `cli_args: list[str] | None`
  - `None` → the plugin wasn't called by marker (or alias)
  - `[]` → called with nothing after the marker (`amca ---template`)
  - `[...]` → called with args (only what was typed; no `.args` defaults)
  - plus a convenience property `invoked` → `cli_args is not None`
- [ ] **Don't** make `args` itself `None`. Two reasons:
  1. `args` carries the `.amca/args/<plugin>.args` defaults, which also have to apply on a plain `amca`
     (e.g. a project pinning `-j 4` for meson). If it were `None`, there'd be nowhere to put them.
  2. `argparse.parse_args(None)` silently parses **`sys.argv`**, meaning amca's own command line.
     Every plugin that does `parse_args(ctx.args)` (meson, template, ...) would quietly start parsing the wrong argv.
     `args` stays `list[str]` (defaults + typed); the new field is purely additive, so no plugin breaks.
- [ ] Set it in `Registry.make_context`: `cli_args = args_by_plugin.get(name)`. `split_argv` already
      yields `[]` for a bare marker, so nothing changes there. Today `.get(name, [])` throws the `None` case away.
- [ ] The legacy `init.py` adapter keeps getting `args` as before.
- [ ] Switch `template`, `meson` and `autoscript` from `bool(ctx.args)` to `ctx.invoked`.
- [ ] `template`: with a bare `---template`, show the list or a picker instead of nothing.
- [ ] Document both fields in `example/plugin.py` (`--show`) and in the README under "Writing a plugin".
- [ ] Tests: bare marker, defaults file without a marker, and a marker with args.

### Shell completion setup (done 2026-11-01)
Completion now works on every machine without manual steps. On the first interactive run, amca writes the script, and for
bash/zsh also a delimited block in `~/.bashrc` / `~/.zshrc` that loads it. Before this, zsh never loaded the file, and bash
only did with bash-completion installed.
It refreshes itself when the content changes (fingerprint in the state file, not the version number),
remembers aliases, and `--uninstall` stops it coming back. Tests: `tests/test_completion_scripts.py` (drives real bash/zsh).

Left over:
- [ ] macOS bash: Terminal.app starts login shells, which read `~/.bash_profile`, not `~/.bashrc`. Untested.
      Likely fix: on macOS, also check that `.bash_profile` sources `.bashrc`, or write the block there.
- [ ] If amca ever ships as a distro/Homebrew package: install the scripts into the system dirs
      (`share/zsh/site-functions/_amca`, `share/bash-completion/completions/amca`, `share/fish/vendor_completions.d/`)
      and skip the first-run installer when those exist.
- [x] `test_prefix_still_narrows_to_markers` read the real user config. The shell tests now use a throwaway config.
- [ ] e2e `G12` (read-only config dir) fails, and already did before this change.

### Runtime completion, including plugin flags (done 2026-11-01)
Built as our own engine instead of argcomplete: no new dependency, full control over order, aliases and
descriptions, and plugin sub-parsers without private API. `amca __complete` (`src/amca/cli/complete.py`) answers each
tab press from the live parsers. The bash/zsh/fish scripts in `cli/completions.py` are now glue that only relays.
- Order is definition order. Aliases share a line with their command in zsh. Options are offered once `-` is typed,
  or when no positional is left. Used flags and their mutually exclusive partners drop out.
- Hints on argparse actions (`complete.steer(action, ...)`) for config keys/values, plugin names, dirs and commands.
- Plugin args: `Plugin.parser()` hook, else capture from `load()` (patched `parse_known_args`, stdout/stderr silenced,
  time limit `completion.plugin_timeout_ms`), else `Plugin.complete()`. `completion = False` opts out.
  Only the active marker's plugin is imported. `amcapl call <plugin> ...` completes that plugin's args.
- The scripts fall back to `amca` on PATH, then to files, when the generated path is gone.
- Tests: `tests/test_complete_engine.py` (engine) and `tests/test_completion_scripts.py` (bash, real zsh on a pty).

Left over:
- [ ] fish glue is untested here (fish isn't installed on this machine). Install fish and add a test like the zsh ones.
- [ ] fish aliases: their `complete -c <alias>` lines live in `amca.fish`, which fish only loads after the first
      `amca<TAB>`. Write one file per alias name.
- [ ] template plugin: give the `name` positionals a `complete` function that lists the stored templates
      (`apply <TAB>` currently offers files).
- [ ] meson `-n/--skip`: its `choices` are the sorted alias list (`c compile e exec ...`). Showing the step names with
      their short forms as descriptions would read better; that's a meson parser change.
- [ ] Startup cost is ~0.1 s per tab press (vs ~0.02 s for a bare `python -c pass`). Add a test that compares the two
      instead of a fixed ms number, as planned. Lazy-import the command modules in `build_parser` if it grows.
- [ ] Personal plugin aliases (above): add them to `marker_items` once they exist.
- [ ] `plugins --names/--markers` and `config list --keys` are no longer used by the scripts. They stay for user scripts;
      their help text still says "for shell completion".

### Plugin API versioning (later, not urgent)
Goal: a plugin written for amca version A still runs on amca version C, behaving mostly as it did on A.
Partly in place already: the loader accepts the 2.x `init.py` / 5-argument `should_load` shape through a legacy adapter (`plugins/loader.py:119`).

- [ ] Plugins declare a target, e.g. a class attribute `amca_api = "3.0"` (missing → treat it as the version that introduced the field, or legacy if it's `init.py`).
- [ ] amca keeps a constant `API_VERSION` (separate from `__version__`, bumped only when the plugin interface changes) and a list of the API versions it supports.
- [ ] The loader picks an adapter by declared version: it builds the `PluginContext` the plugin expects, maps return values and fills in renamed fields.
      The current legacy adapter becomes the `2.x` entry.
- [ ] Unknown or newer version → clear error (`plugin X needs amca >= Y`), with no traceback.
- [ ] `amcapl list` / `doctor` show each plugin's API version and warn about ones that use deprecated adapters.
- [ ] Rule to write down: interface changes only add things. Removing or renaming needs a new API version plus an adapter.
- [ ] Same pattern as meson's `version_behaviour` routing, so maybe share the "version → handler" helper.
