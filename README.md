# amca

**One command that knows what to do in whatever directory you are standing in.**

`amca` is a dispatcher for plugins based on the current directory,
works out which of its plugins apply, and runs them. The behaviour lives in
plugins.

```console
# exaples
$ cd ~/code/renderer     # a C project
$ amca                   # with meson plugin installed and meson.build setup
[setup] … [compile] … [install] … [test] … [run]

$ cd ~/code/scraper      # a shell script
$ amca                   # autoscript plugin installed
[autoscript] sh /home/you/code/scraper/amca_auto_script.sh

```

Same keystroke everywhere. amca decides *which* plugin applies and hands it the
arguments; it has no opinion about what a build is.

- [Install](#install) · [First run](#first-run) · [Two commands](#two-commands) · [Command reference](#command-reference)
- [Plugins](#plugins) · [Writing a plugin](#writing-a-plugin) · [Bundled plugins](#bundled-plugins)
- [Configuration](#configuration) · [Security](#security) · [Shell completion](#shell-completion)
- [Troubleshooting](#when-something-is-wrong) · [Development](#development) · [Platform support](#platform-support)

---

## Requirements

Python 3.10 or newer. Nothing else — amca has **no required runtime
dependencies**, deliberately, so a broken third-party wheel can never stop it
from starting. Individual plugins may have their own.

## Install

```bash
uv tool install --python 3.13 --with 'amca' amca
```

`--python` pins a `uv`-managed interpreter so a distro Python upgrade cannot
break amca. Alternatives:

```bash
pipx install 'amca'
pip install --user 'amca'

# to track main instead of the last release:
uv tool install --python 3.13 --with 'amca' git+https://github.com/Delici0u-s/amca
```

Upgrade with `uv tool upgrade amca` or `pipx reinstall amca`. There is no build
step and nothing to recompile after a system update.

**Uninstall:**

```bash
amca completions --uninstall     # first, while amca can still find its files
uv tool uninstall amca           # or: pipx uninstall amca
rm -rf ~/.config/amca ~/.local/state/amca
```

## Two commands

amca ships two console scripts, split by lifetime:

| | |
|---|---|
| **`amca`** | run plugins, and everything scoped to a project or a single run |
| **`amcapl`** | manage which plugins exist on this machine at all |

They share global flags and configuration. `amcapl` is separate so `amca --help`
stays short and so plugin management cannot be triggered by a stray argument
during a build.

## Command reference

```
amca [global flags] [command] [MARKER plugin-args …]

  (none) / run     run every enabled plugin that applies here
  new              create an amca root (.amca/) in this directory
  remove           delete the amca root
  root             show or manage root detection (show, ignore, unignore, clear-ignored)
  args PLUGIN      edit that plugin's per-project default arguments
  config           inspect or change settings
  plugins          list installed plugins and their markers
  completions      print, install or remove shell tab-completion
  doctor           check paths, config, plugins, tools; non-zero on a problem

amcapl [global flags] <command> [NAME …]

  list                 what is installed, and its marker
  install              fetch and enable
  enable | disable | toggle
  uninstall
  update               re-fetch (use after editing a local plugin source)
  call NAME -- ARGS    run one plugin directly, skipping should_load
```

Global flags: `--config-dir --plugin-dir --depth --editor --marker-prefix
--log-mode --log-level --log-prefix --on-error --on-missing --debug --version`,
plus `--dry-run` on `amca`. **Flags apply to one run and never write to your
config file** — see [Configuration](#configuration).

Omit the names on any `amcapl` command and you get a picker. **In the checkbox,
space selects and enter confirms** — enter on its own selects nothing.

Exit status: `0` success, `1` a plugin or command failed, `2` bad usage or bad
configuration, `130` interrupted.

---

## Plugins

A plugin decides whether it applies (`should_load`) and then does the work
(`load`). amca runs every enabled plugin that applies.

### Passing arguments to a plugin

Everything after a **marker** goes to that plugin, until the next marker:

```bash
amca ---example --show
amca ---autoscript deploy --dry
```

A marker is `plugins.marker_prefix` (default `---`) plus the plugin's folder
name, underscores written as dashes. `amca plugins --markers` prints the exact
strings this directory accepts.

**Naming a plugin runs only that plugin.** With no markers, `amca` runs
everything applicable — the usual case. Set `plugins.marker_scope=all` if you
want a marker to add arguments without narrowing the run.

**An unrecognised marker is an error**, and it suggests the fix:

```console
$ amca ---autoScr
amca: unknown plugin marker '---autoScr'
  did you mean: ---autoscript
  known markers: ---autoscript, ---meson
```

A bare `--` stops marker parsing, for values that begin with the prefix:

```bash
amca config set plugins.marker_prefix ä
```

Avoid shell glob or history characters (`* ? [ ] { } ~ !`) in the prefix — your
shell expands the marker before amca sees it. `config set` warns if you try.

### Per-project default arguments

`.amca/args/<plugin>.args`, one argument per line, `#` for comments. They are
placed before anything you type, so a project can pin a flag while a
command-line flag still overrides it.

```bash
amca args meson        # opens it in $EDITOR
amca args meson --show
```

### Where plugins live

Three different places, easy to conflate:

| | |
|---|---|
| `<config>/plugins/` | **installed** — what actually runs. `amcapl` manages it; `plugins.dir` moves it. |
| `plugins/amca_presets/` in this repo | the three bundled with amca, so `amcapl install` works offline |
| anywhere you like | **yours** |

Your own plugins do not belong in this repo. Add a source:

```bash
amca config set plugins.sources builtin,/home/you/my-amca-plugins
amcapl install cargo
amcapl update cargo            # re-copy after editing
```

While iterating, skip the copy entirely:

```bash
amca --plugin-dir ~/my-amca-plugins ---cargo build
```

## Writing a plugin

A plugin is a directory containing `plugin.py` with exactly one subclass of
`amca.api.Plugin`:

```python
from amca.api import Plugin, PluginContext, PluginError
from amca.core import proc


class cargo(Plugin):
    description = "cargo build/run"

    def should_load(self, ctx: PluginContext) -> bool:
        # Cheap — called for every enabled plugin on every invocation.
        return ctx.project_dir_info().has_file("Cargo.toml")

    def load(self, ctx: PluginContext) -> int:
        if ctx.dry_run:
            ctx.log.log("would run cargo build")
            return 0
        cargo = proc.resolve_tool("cargo", hint="install rustup")
        return proc.call([cargo, "build", *ctx.args], cwd=str(ctx.project_dir))
```

`PluginContext` carries `args`, `working_dir`, `root`, `plugin_dir` (your
private per-project directory, already created), `dirs`, `log` and `dry_run`.

Import `amca.api` directly — amca is an installed package. Do not vendor copies
of its internals. Raise `PluginError` for a clean message with no traceback.
Return an exit status; `0` or `None` means success.

Subdirectories work and are namespaced per plugin, so two plugins can both have
an `_impl/` without colliding:

```
my-plugin/
    plugin.py
    _impl/
        __init__.py
```

**Tab completion of your plugin's arguments** works without extra code if
`load()` parses `ctx.args` with argparse before doing anything else: during a
tab press, amca runs `load()` with `dry_run=True` and stops it at that first
`parse_args`/`parse_known_args`, then completes against the parser it caught.
Nothing after that line runs. To skip running `load()` at all, return the
parser from `parser()` instead:

```python
    def parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser()
        parser.add_argument("--out").complete = "dirs"   # files | dirs | commands | none
        return parser
```

An argument's `complete` attribute can also be a function
`(current_word) -> [value, ...]` or `[(value, description), ...]`, for values
only your plugin knows. Plugins that don't use argparse can define
`complete(self, ctx, words)` instead, and `completion = False` keeps amca from
ever running `load()` for completion. A plugin that hangs or prints during that
capture just gets file-name completion; the wait is capped by
`completion.plugin_timeout_ms`.

Start from the `example` plugin rather than from one of the real presets:

```bash
amcapl install example
amca ---example --show     # prints every PluginContext field and what it is for
```

## Bundled plugins

Three, installed on demand with `amcapl install NAME`. None of them is
privileged; they are ordinary plugins that happen to ship inside the wheel.

| | applies when | does |
|---|---|---|
| `example` | never — call it directly | sixty commented lines to copy: selection, `--dry-run`, `PluginError`, tool resolution, logging |
| `autoscript` | `amca_auto_script.*` is present | runs it, forwarding your arguments |
| `meson` | `meson.build` carries the amca variable block | `setup → reconfigure → compile → install → test → run` |

<details>
<summary><code>autoscript</code> — details</summary>

Runs `amca_auto_script[.sh|.bash|.zsh]` (`.ps1`/`.bat`/`.cmd` on Windows) from
the working directory, falling back to the project root.

```bash
amca ---autoscript --new       # create one (asks which shell)
amca ---autoscript build       # forwarded to the script
amca ---autoscript -- --help   # forward --help verbatim
```
</details>

<details>
<summary><code>meson</code> — details</summary>

Needs `meson`, which needs `ninja` and a compiler.

```bash
amca ---meson                 # whole pipeline
amca ---meson compile         # one step
amca ---meson -s              # clean first
amca ---meson -n t -n e       # skip test and run
amca ---meson -Ab "--buildtype=debug" -Ae "--verbose"
amca ---meson --print-template > meson.build
```

Applies only to a `meson.build` carrying the amca variable block, so it stays
inert in meson projects that are not yours:

```meson
amca_var__meson__version_behaviour = '2.0.1'
amca_var__meson__build_dir         = 'build'
amca_var__meson__executable_name   = 'myapp'
amca_var__meson__install_dir       = '../compiled'   # relative to build_dir
```

`reconfigure` re-runs meson only when the *set* of source files changes —
ninja handles content changes. `.vscode/launch.json` and `.clangd` are kept
pointing at the current build directory.
</details>

---

## Configuration

One JSON file. Four layers, lowest priority first:

```
built-in default  →  config file  →  AMCA_* env var  →  command-line flag
```

**Only `amca config set` writes to disk.** Command-line flags apply to that run
and nothing else.

| | config | state (logs, caches, first-run marker) |
|---|---|---|
| Linux/BSD | `$XDG_CONFIG_HOME/amca` → `~/.config/amca` | `$XDG_STATE_HOME/amca` → `~/.local/state/amca` |
| macOS | `~/Library/Application Support/amca` | `~/Library/Caches/amca` |
| Windows | `%APPDATA%\amca` | `%LOCALAPPDATA%\amca` |

Override with `--config-dir` / `$AMCA_CONFIG_DIR` and `$AMCA_STATE_DIR`.
`amca config path` prints the one actually in use.

```bash
amca config list --origin      # every setting, its value, and which layer won
amca config list --changed
amca config get plugins.marker_prefix
amca config set plugins.marker_prefix '+++'
amca config unset core.editor
amca config describe log.mode  # type, default, env var name
amca config edit
```

`--origin` answers "I changed it and nothing happened": it shows whether an env
var is overriding your edit, whether the key is real (unknown keys are reported,
not ignored), and which file is actually in use.

Hand-editing the JSON is fully supported — `config set` is a convenience.
Nested and dotted keys both work. Every setting has an env var:
`plugins.marker_prefix` → `AMCA_PLUGINS_MARKER_PREFIX`.

<details>
<summary>All settings</summary>

| key | default | |
|---|---|---|
| `core.debug` | `false` | extra diagnostics and full tracebacks |
| `core.greet` | `false` | print a greeting on every invocation |
| `core.editor` | `$VISUAL`/`$EDITOR`/`nano` | editor for `amca args` |
| `root.folder_name` | `.amca` | marker directory identifying a project root |
| `root.search_depth` | `5` | how far up to look for it |
| `root.ask_to_create` | `true` | offer to create one (TTY only) |
| `root.ignored_paths` | `[]` | where not to ask; `amca root ignore` |
| `log.mode` | `console` | `console`, `file`, `both`, `silent` |
| `log.level` | `INFO` | `INFO`, `SUCCESS`, `WARN`, `ERROR`, `FATAL` |
| `log.prefix` | `none` | `none`, `minimal`, `simple`, `normal`, `verbose` |
| `plugins.dir` | `<config>/plugins` | where installed plugins live |
| `plugins.enabled` | `[]` | which may run; `amcapl enable` |
| `plugins.marker_prefix` | `---` | |
| `plugins.marker_scope` | `selected` | `selected` or `all` |
| `plugins.on_error` | `continue` | or `abort` |
| `plugins.on_missing` | `warn` | `ignore`, `warn`, `abort` |
| `plugins.announce_loaded` | `false` | log each plugin as it runs |
| `plugins.sources` | `builtin, github:…` | where `amcapl install` looks |

</details>

### External tools

```bash
export AMCA_TOOL_MESON=/opt/meson/bin/meson
export AMCA_TOOL_NINJA=samu
```

## Security

**A plugin is arbitrary Python that runs as you, with no sandbox.** amca
imports `plugin.py` and calls it; it does not inspect, restrict or review what
that code does. Treat installing a plugin exactly like `curl … | sh`.

Concretely:

- `amcapl install` copies a directory from a **source** and enables it. The
  defaults are `builtin` (inside the wheel you already trust) and a GitHub
  repository. Anything you add to `plugins.sources` — a URL, a local path —
  becomes code that runs on your next `amca`.
- `should_load` runs for **every enabled plugin on every invocation**, before
  you have named anything. An enabled-and-installed plugin therefore executes
  in every directory you type `amca` in, not only where it applies.
- `.amca/args/<plugin>.args` is read from the project directory, so cloning an
  untrusted repository that contains a `.amca/` directory hands that repo
  influence over the flags your plugins receive. Run
  `amca args <plugin> --show` before running amca in a repo you did not write.
- Shell completion shells out to `amca` on every keypress. That is amca itself,
  by absolute path — nothing from the project directory.

`amca doctor` lists every installed plugin and where it came from.

## Shell completion

**Nothing to do.** The first time you run amca in a terminal, it sets up tab
completion for the shell in `$SHELL` and says so once. Open a new terminal and
it works. The setup has two parts:

- the script, in the shell's per-user completion directory
  (`~/.local/share/bash-completion/completions/amca`,
  `~/.local/share/zsh/site-functions/_amca`, `~/.config/fish/completions/amca.fish`);
- for bash and zsh, a delimited `# >>> amca completion >>>` block in
  `~/.bashrc` / `~/.zshrc` that loads it. zsh reads no per-user directory on
  its own, and bash reads that directory only through the bash-completion
  package, which minimal systems, macOS and many custom rc files don't load.
  fish needs no block.

The block works anywhere in the rc file, before or after your own `compinit`,
and does nothing once the script is gone. amca refreshes both parts silently
whenever an upgrade changes them, so they never go stale.

```bash
amca completions --status           # per shell: active, installed or not, which helper?
amca completions --install          # set it up now (or again), for $SHELL or a named shell
amca completions --install --no-rc  # script only; prints the block to add yourself
amca completions --uninstall        # removes script and rc block, and stops auto-setup
amca completions bash > somewhere   # just print it
export AMCA_NO_AUTO_COMPLETION=1    # opt out without uninstalling
```

A wheel cannot run code at install time (PEP 427), so `pip`, `pipx` and `uv
tool` have no way to register completions. Only a distro package can, by
putting the script into `/usr/share/zsh/site-functions` and similar, which
every shell already reads. That's why completion "just works" for programs
from dnf or apt. Every user-level install (pipx, uv, cargo, Homebrew) needs an
rc line instead; amca writes it for you on first run.

bash and fish autoload by command name, so `amcapl` gets its own file alongside
`amca`'s; zsh binds both from a single `#compdef` line.

**Using an alias?** The generated script calls amca by absolute path, so a venv
install off `PATH` still completes — but the alias needs registering:

```bash
amca completions --install --command a3        # an alias for amca
amca completions --install --pl-command ap     # an alias for amcapl
```

Aliases are remembered, so later refreshes keep them.

**What completes:** every command, subcommand, alias and flag, in the order
they're defined (aliases share a line with their command in zsh); option
values and choices; config keys with their descriptions, and the valid values
for `config set <key>`; plugin names and markers; and, after a marker, the
plugin's own flags and values, taken from the plugin's argparse parser (see
[Writing a plugin](#writing-a-plugin)). `amcapl call <plugin>` completes that
plugin's arguments too.

The installed scripts hold no knowledge of amca: each tab press runs
`amca __complete`, which answers from the same parsers a real run uses. A new
flag, plugin or marker prefix completes immediately, without regenerating
anything, at the cost of one short amca start per tab press. Only the plugin
whose marker is active gets loaded.

These print the same lists, for your own scripts:

```bash
amca plugins --names
amca plugins --markers
amca config list --keys
```

---

## When something is wrong

```bash
amca doctor
```

Checks paths, which config layer each non-default setting came from, root
resolution, every plugin's health, external tools, and the subprocess
environment. Exits non-zero when it finds a problem. It is the right thing to
paste into an issue.

<details>
<summary>Upgrading from amca 2.x</summary>

```bash
amca config migrate --dry-run     # show what would be imported
amca config migrate
```

Keys were renamed and several boolean pairs became enums:

| 2.x | 3.x |
|---|---|
| `args.plugin_prefix` | `plugins.marker_prefix` |
| `generic.plugin_path` | `plugins.dir` |
| `generic.exit_on_plugin_error` | `plugins.on_error` = `continue`\|`abort` |
| `generic.exit_on_plugin_not_found` | `plugins.on_missing` = `ignore`\|`warn`\|`abort` |
| `logging.print_loaded` | `plugins.announce_loaded` |
| `extreamly_important.greet_user` | `core.greet` |

Then remove the old install **before** deleting the old checkout — the
uninstaller is the only thing that knows to strip the `# >>> amca PATH >>>`
block from your shell rc:

```bash
python install_uninstall_update.py uninstall --keep-config
```

Existing plugins keep working: the loader detects the old five-argument
`should_load(amca_root_dir, amca_root_plugin_dir, working_dir, dir_parser, args)`
and adapts it. Port them when convenient.

If you upgrade by extracting a release over an old checkout, note that
unzipping does not delete removed files — `amca doctor` will tell you if a
stale `src/amca/presets/` is left behind.

**Why 3.0 exists.** 2.x shipped as a PyInstaller `--onefile` binary rebuilt at
install time. That put a private copy of CPython on `LD_LIBRARY_PATH` for every
child process, so after a distro Python upgrade the system `meson` loaded
amca's stale `libpython` and died with `internal Python C API version
mismatch`. 3.0 is a normal Python package, which makes that class of bug
structurally impossible. Along the way: CLI flags no longer write to your
config file; plugin markers have one definition instead of two disagreeing
ones; root discovery no longer runs at import time; plugins import `amca.api`
instead of vendoring internals; and two plugins can have identically named
submodules without shadowing.
</details>

## Development

```bash
git clone https://github.com/Delici0u-s/amca && cd amca
pip install -e '.[all,dev]'

pytest -q                    # unit tests, under a second
python tests/e2e.py          # end-to-end matrix, real subprocesses
python tests/e2e.py --list   # the matrix without running it
python tests/e2e.py -k markers -v
ruff check src tests plugins
mypy
```

Layout:

```
src/amca/              the application
plugins/amca_presets/  bundled plugins — content, not application code
tests/
```

`tests/test_regressions.py` is one test per defect that shipped in 2.x. Add to
it rather than deleting from it. `tests/e2e.py` runs the real console scripts
against throwaway config and project directories; cases marked `!` exercise a
mistake, hostile argv, or a broken component, and there are more of those than
happy paths. `tests/fixtures/probe/` is a plugin that dumps its whole
`PluginContext` as JSON and can be driven into any failure mode with `PROBE_*`
environment variables. `tests/test_completion_scripts.py` sources the generated
shell scripts and drives the completion functions for real — emitting a script
is not evidence that it completes anything.

## Platform support

| | |
|---|---|
| Linux | fully tested |
| macOS | code paths tested by simulation, not executed |
| Windows | code paths tested by simulation, not executed |

`tests/test_platform.py` patches `os.name` / `sys.platform` and reloads the
affected modules to exercise the non-Linux branches: config directories, the
`.exe` suffix, the autoscript interpreter table, non-POSIX `shlex` splitting,
colour detection, read-only file deletion. That verifies the branches are
correct and reachable — not that Windows behaves as expected.

Windows caveats: plugin folder names are matched case-sensitively, so keep them
lowercase; ANSI colour needs Windows Terminal or a console accepting
`ENABLE_VIRTUAL_TERMINAL_PROCESSING`; `autoscript` does not offer an
extensionless script, since such a file is not executable there.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
