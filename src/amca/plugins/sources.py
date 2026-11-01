"""Where ``amcapl install`` gets plugins from.

A source is a string:

  ``builtin``                              presets shipped inside the wheel
  ``/abs/path/to/plugins``                 a local directory of plugin folders
  ``github:owner/repo@ref:path/in/repo``   a GitHub tree

``builtin`` is first in the default list so installing a preset needs no
network and no ``requests``. The old default pointed only at a GitHub API URL,
which meant the presets bundled with the program you had just installed were
downloaded from the internet — and failed entirely when rate-limited or
offline.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

__all__ = ["BuiltinSource", "GitHubSource", "LocalSource", "Source", "parse_source"]


class Source:
    """A place plugins can be fetched from."""

    label: str = "?"
    #: True when listing needs the network. Shell completion skips these: a tab
    #: press must never wait on an HTTP request.
    remote: ClassVar[bool] = False

    def list_plugins(self) -> list[str]:
        raise NotImplementedError

    def fetch(self, name: str, destination: Path) -> None:
        raise NotImplementedError


@dataclass(slots=True)
class BuiltinSource(Source):
    label: str = "builtin"

    def _root(self) -> Path:
        """Directory holding the bundled preset plugins.

        `amca_presets` is a separate top-level package (see plugins/ in the
        repo). Imported lazily and by name so that a checkout without it — or
        a future split into standalone distributions — degrades to "no builtin
        source" instead of an ImportError at startup.
        """
        import importlib

        try:
            module = importlib.import_module("amca_presets")
        except ImportError:
            return Path("/nonexistent")
        return Path(module.__file__ or "").parent

    def list_plugins(self) -> list[str]:
        root = self._root()
        if not root.is_dir():
            return []
        return sorted(
            entry.name
            for entry in root.iterdir()
            if entry.is_dir()
            and not entry.name.startswith(("_", "."))
            and any((entry / f).is_file() for f in ("plugin.py", "init.py"))
        )

    def fetch(self, name: str, destination: Path) -> None:
        source = self._root() / name
        if not source.is_dir():
            raise FileNotFoundError(f"builtin plugin {name!r} does not exist")
        shutil.copytree(source, destination, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


@dataclass(slots=True)
class LocalSource(Source):
    path: Path
    label: str = ""

    def __post_init__(self) -> None:
        self.label = str(self.path)

    def list_plugins(self) -> list[str]:
        if not self.path.is_dir():
            return []
        return sorted(
            entry.name
            for entry in self.path.iterdir()
            if entry.is_dir() and any((entry / f).is_file() for f in ("plugin.py", "init.py"))
        )

    def fetch(self, name: str, destination: Path) -> None:
        source = self.path / name
        if not source.is_dir():
            raise FileNotFoundError(f"{source} does not exist")
        shutil.copytree(source, destination, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


@dataclass(slots=True)
class GitHubSource(Source):
    owner: str
    repo: str
    ref: str = "main"
    subpath: str = ""
    label: str = ""
    remote: ClassVar[bool] = True

    def __post_init__(self) -> None:
        self.label = f"github:{self.owner}/{self.repo}@{self.ref}:{self.subpath}"

    def _requests(self) -> Any:
        try:
            import requests
        except ImportError as exc:
            raise RuntimeError(
                "remote plugin sources need the 'requests' package.\n"
                "  install it with:  uv tool install --with requests amca\n"
                "  or drop the github: entry from plugins.sources"
            ) from exc
        return requests

    def _api(self, path: str) -> str:
        path = path.strip("/")
        base = f"https://api.github.com/repos/{self.owner}/{self.repo}/contents"
        return f"{base}/{path}?ref={self.ref}" if path else f"{base}?ref={self.ref}"

    def list_plugins(self) -> list[str]:
        requests = self._requests()
        response = requests.get(self._api(self.subpath), timeout=20)
        response.raise_for_status()
        return sorted(
            item["name"]
            for item in response.json()
            if item.get("type") == "dir" and not item["name"].startswith(("_", "."))
        )

    def fetch(self, name: str, destination: Path) -> None:
        self._download_tree(f"{self.subpath}/{name}".strip("/"), destination)

    def _download_tree(self, repo_path: str, destination: Path) -> None:
        requests = self._requests()
        response = requests.get(self._api(repo_path), timeout=20)
        response.raise_for_status()
        destination.mkdir(parents=True, exist_ok=True)
        for item in response.json():
            target = destination / item["name"]
            if item["type"] == "dir":
                self._download_tree(item["path"], target)
            elif item["type"] == "file":
                blob = requests.get(item["download_url"], timeout=30)
                blob.raise_for_status()
                target.write_bytes(blob.content)


def parse_source(spec: str) -> Source:
    """Turn a config string into a Source. Raises ValueError on nonsense."""
    text = spec.strip()
    if text == "builtin":
        return BuiltinSource()

    if text.startswith("github:"):
        body = text[len("github:"):]
        subpath = ""
        if ":" in body:
            body, subpath = body.split(":", 1)
        ref = "main"
        if "@" in body:
            body, ref = body.rsplit("@", 1)
        if "/" not in body:
            raise ValueError(f"malformed github source: {spec!r} (want owner/repo)")
        owner, repo = body.split("/", 1)
        return GitHubSource(owner, repo, ref, subpath)

    # Legacy: a raw GitHub contents API URL, as written by amca 2.x.
    if text.startswith("https://api.github.com/repos/"):
        rest = text[len("https://api.github.com/repos/"):]
        ref = "main"
        if "?ref=" in rest:
            rest, ref = rest.split("?ref=", 1)
        parts = rest.split("/")
        if len(parts) >= 3 and parts[2] == "contents":
            return GitHubSource(parts[0], parts[1], ref, "/".join(parts[3:]))
        raise ValueError(f"unrecognised GitHub API url: {spec!r}")

    path = Path(text).expanduser()
    if path.is_dir():
        return LocalSource(path.resolve())
    raise ValueError(f"unrecognised plugin source: {spec!r}")
