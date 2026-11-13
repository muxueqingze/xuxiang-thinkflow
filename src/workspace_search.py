"""Lazy, workspace-local directory enumeration for the file search tools."""

import os
import stat
from itertools import chain
from pathlib import Path
from threading import Event
from typing import Callable, Iterator, NamedTuple

from pathspec import GitIgnoreSpec


class SearchEntry(NamedTuple):
    path: str
    relative: str
    is_dir: bool


IgnoreScopes = tuple[tuple[int, GitIgnoreSpec], ...]


def _with_rules(
    scopes: IgnoreScopes, directory: str, prefix: str, readable: Callable[[str], bool],
) -> IgnoreScopes:
    ignore_path = os.path.join(directory, ".gitignore")
    try:
        mode = os.lstat(ignore_path).st_mode
    except FileNotFoundError:
        return scopes
    if not stat.S_ISREG(mode) or not readable(ignore_path):
        return scopes
    with open(ignore_path, encoding="utf-8", errors="replace") as rules:
        spec = GitIgnoreSpec.from_lines(rules)
    return (*scopes, (len(prefix), spec)) if spec.patterns else scopes


def _ignored(scopes: IgnoreScopes, path: str) -> bool:
    for offset, spec in reversed(scopes):
        decision = spec.check_file(path[offset:]).include
        if decision is not None:
            return decision
    return False


def iter_entries(
    root: str,
    workspace: str,
    recursive: bool,
    stopped: Event,
    readable: Callable[[str], bool],
) -> Iterator[SearchEntry]:
    search_root = Path(root)
    base = Path(workspace)
    if not search_root.is_relative_to(base):
        base = search_root

    prefix = ""
    scopes: IgnoreScopes = ()
    directory = base
    for part in search_root.relative_to(base).parts:
        scopes = _with_rules(scopes, str(directory), prefix, readable)
        directory /= part
        prefix += part + "/"
        if _ignored(scopes, prefix):
            # An explicit search inside an excluded tree starts a new scope.
            scopes = ()

    pending = [(root, prefix, "", scopes)]
    while pending and not stopped.is_set():
        directory, prefix, relative_prefix, scopes = pending.pop()
        scopes = _with_rules(scopes, directory, prefix, readable)
        with os.scandir(directory) as children:
            ordered = sorted(children, key=lambda child: child.name)

        directories, files = [], []
        for child in ordered:
            if stopped.is_set():
                return
            if os.path.normcase(child.name) == ".git":
                continue
            is_dir = child.is_dir()
            name = child.name + ("/" if is_dir else "")
            if _ignored(scopes, prefix + name):
                continue
            if not is_dir and not child.is_file():
                continue
            entry = SearchEntry(child.path, relative_prefix + child.name, is_dir)
            if not recursive:
                yield entry
            elif is_dir:
                directories.append((child, entry))
            else:
                files.append(entry)
        if recursive:
            for entry in chain((entry for _, entry in directories), files):
                if stopped.is_set():
                    return
                yield entry
            for child, entry in reversed(directories):
                if stopped.is_set():
                    return
                if not (child.is_symlink() or child.is_junction()) and readable(entry.path):
                    pending.append((entry.path, prefix + child.name + "/", entry.relative + "/", scopes))
