"""Read-only Git status and patches scoped to the selected workspace."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import subprocess


MAX_DIFF_CHARS = 1_000_000
DIFF_OPTIONS = ("--no-ext-diff", "--no-textconv", "--no-color")


def _git(cwd, *args, allowed=(0,)):
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "--literal-pathspecs", "-c", "core.fsmonitor=false",
             "-c", "core.quotepath=false", *args],
            cwd=cwd, env={**os.environ, "LC_ALL": "C"}, capture_output=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except FileNotFoundError as error:
        raise ValueError("未找到 Git，请检查 Git 是否已加入 PATH。") from error
    except subprocess.TimeoutExpired as error:
        raise ValueError("读取 Git 工作区超时。") from error
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    if result.returncode not in allowed:
        raise ValueError(stderr or f"Git 退出码 {result.returncode}")
    return result.stdout.decode("utf-8", errors="replace"), stderr


def _repo_root(cwd):
    output, error = _git(cwd, "rev-parse", "--show-toplevel", allowed=(0, 128))
    if output:
        return Path(output.removesuffix("\n")).resolve()
    if error.startswith(("fatal: not a git repository", "fatal: this operation must be run in a work tree")):
        return None
    raise ValueError(error)


def _status(cwd, root, *, untracked=True):
    result = {"is_repo": root is not None, "root": str(root) if root else None,
              "branch": None, "staged": [], "unstaged": [], "untracked": []}
    if root is None:
        return result

    scope = PurePosixPath(cwd.relative_to(root).as_posix())
    output, _ = _git(cwd, "status", "--porcelain=v2", "--branch", "-z", "--renames",
                     "--untracked-files=all" if untracked else "--untracked-files=no", "--", ".")
    records = iter(output.split("\0"))
    for record in records:
        if record.startswith("# branch.head "):
            result["branch"] = record.removeprefix("# branch.head ")
        elif record.startswith("? "):
            path = PurePosixPath(record[2:])
            if path.is_relative_to(scope):
                result["untracked"].append({"path": path.relative_to(scope).as_posix(), "status": "?"})
        elif record.startswith(("1 ", "2 ", "u ")):
            kind = record[0]
            fields = record.split(" ", {"1": 8, "2": 9, "u": 10}[kind])
            path = PurePosixPath(fields[-1])
            old = PurePosixPath(next(records)) if kind == "2" else None
            if not path.is_relative_to(scope):
                continue
            relative = path.relative_to(scope).as_posix()
            for group, flag in zip(("staged", "unstaged"), fields[1]):
                if flag != ".":
                    entry = {"path": relative, "status": "U" if kind == "u" else flag}
                    if flag in ("R", "C") and old is not None and old.is_relative_to(scope):
                        entry["old_path"] = old.relative_to(scope).as_posix()
                    result[group].append(entry)
    return result


def git_status(cwd):
    cwd = Path(cwd).resolve()
    return _status(cwd, _repo_root(cwd))


def git_diff(cwd, path, staged=False):
    if type(staged) is not bool:
        raise ValueError("暂存区选项须为布尔值。")
    if not isinstance(path, str) or not path or "\0" in path:
        raise ValueError("文件路径无效。")
    cwd = Path(cwd).resolve()
    relative = Path(path)
    if (relative.anchor or relative == Path(".") or ".." in relative.parts
            or not (cwd / relative).resolve().is_relative_to(cwd)):
        raise ValueError("文件必须位于当前工作区。")
    path = relative.as_posix()
    root = _repo_root(cwd)
    if root is None:
        raise ValueError("当前工作区没有 Git 仓库。")

    status = _status(cwd, root, untracked=not staged)
    if not staged and any(item["path"] == path for item in status["untracked"]):
        patch, _ = _git(cwd, "diff", "--no-index", *DIFF_OPTIONS, "--", "/dev/null", path, allowed=(0, 1))
    else:
        group = status["staged" if staged else "unstaged"]
        entry = next((item for item in group if item["path"] == path), None)
        paths = [entry["old_path"], path] if entry and "old_path" in entry else [path]
        index = ("--cached",) if staged else ()
        patch, _ = _git(cwd, "diff", *index, *DIFF_OPTIONS, "--relative", "--find-renames", "--", *paths)
    return {"path": path, "staged": staged, "diff": patch[:MAX_DIFF_CHARS], "truncated": len(patch) > MAX_DIFF_CHARS}
