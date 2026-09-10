"""Bounded static shell write targets; no policy, evaluation or filesystem scan."""
from pathlib import Path
import re
import shlex
from typing import Any, Callable, Dict, Optional


def command_mutation_paths(command: str, runtime_context: Optional[Dict[str, Any]], normalize_path: Callable[[str], Optional[Path]], expand_path: Callable[[str], str], *, _depth: int = 0) -> list[Path]:
    """Static targets of common shell writes, not an evaluator or sandbox.

    Preserve quoted text and distinguish copy sources/values from targets.
    Relative paths use the execution cwd; literal directory changes and
    nested shell -Command/-c are bounded. Dynamic script-generated paths
    remain governed by their existing execution/encoded-command checks.
    """
    context = dict(runtime_context or {})
    cwd = normalize_path(context.get("command_cwd") or context.get("cwd") or context.get("workspace_path") or context.get("workspacePath")) or Path.cwd()
    text = re.sub(r"(?i)(?<![\w-])(-literalpath|-path|-filepath|-destination):(?=\S)", r"\1 ", str(command or ""))
    lexer = shlex.shlex(text, posix=False, punctuation_chars=";&|><\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError:
        return []
    targets: list[Path] = []

    def resolve(value: str) -> Optional[Path]:
        text = expand_path(value)
        if not text or text.lower() in {"nul", "/dev/null"} or re.search(r"\$(?!env:|\{env:)|%[^%]+%", text, re.IGNORECASE):
            return None
        path = Path(text).expanduser()
        return normalize_path(str(path if path.is_absolute() else cwd / path))

    def add(value: str) -> None:
        path = resolve(value)
        if path is not None and path not in targets:
            targets.append(path)

    segments: list[list[str]] = [[]]
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token and all(char in ";&|\n" for char in token):
            segments.append([])
        else:
            segments[-1].append(token)
        index += 1
    write_verbs = {"set-content", "add-content", "clear-content", "out-file", "new-item", "sc", "ac", "clc", "ni", "tee", "tee-object", "touch", "truncate"}
    remove_verbs = {"rm", "rmdir", "rd", "del", "erase", "remove-item", "ri", "move", "mv", "move-item", "mi", "rename", "ren", "rename-item"}
    copy_verbs = {"cp", "copy", "copy-item", "cpi", "xcopy", "robocopy", "install", "ditto"}
    for segment in segments:
        if not segment:
            continue
        arguments = []
        index = 0
        while index < len(segment):
            if segment[index] in {">", ">>", "&>", "&>>"} and index + 1 < len(segment):
                add(segment[index + 1])
                index += 2
            else:
                arguments.append(segment[index])
                index += 1
        segment = arguments
        if not segment:
            continue
        name = re.split(r"[\\/]", segment[0].strip("\"'"))[-1].lower().removesuffix(".exe")
        args = segment[1:]
        if name in {"sudo", "doas"} and _depth < 4:
            offset = 0
            while offset < len(args) and args[offset].startswith("-"):
                option = args[offset]
                offset += 2 if option in {"-u", "-g", "-h", "-p", "-C", "--user", "--group"} else 1
            targets.extend(command_mutation_paths(" ".join(args[offset:]), {**context, "command_cwd": str(cwd)}, normalize_path, expand_path, _depth=_depth + 1))
        if name in {"powershell", "pwsh", "cmd", "sh", "bash"} and _depth < 4:
            for offset, arg in enumerate(args):
                if arg.lower() in {"-command", "-c", "/c"}:
                    nested = " ".join(args[offset + 1:])
                    if len(args[offset + 1:]) == 1:
                        nested = nested.strip("\"'")
                    targets.extend(command_mutation_paths(nested, {**context, "command_cwd": str(cwd)}, normalize_path, expand_path, _depth=_depth + 1))
                    break
        if name not in write_verbs | remove_verbs | copy_verbs | {"cd", "chdir", "set-location", "push-location"}:
            continue
        positional: list[str] = []
        named: list[str] = []
        index = 0
        while index < len(args):
            arg = args[index]
            option = arg.lower()
            if option in {"-path", "-literalpath", "-filepath", "-destination"} and index + 1 < len(args):
                if name not in copy_verbs or option == "-destination":
                    named.append(args[index + 1])
                index += 2
                continue
            if option in {"-value", "-encoding", "-itemtype", "-filter", "-include", "-exclude", "-newname", "-s", "--size", "-m", "--mode"} and index + 1 < len(args):
                index += 2
                continue
            if not arg.startswith("-") and not (name in {"del", "erase", "rd", "rmdir", "copy", "xcopy", "robocopy"} and re.fullmatch(r"/[a-z]+", arg, re.IGNORECASE)):
                positional.append(arg)
            index += 1
        if name in {"cd", "chdir", "set-location", "push-location"}:
            destination = (named or positional)[:1]
            if destination:
                cwd = resolve(destination[0]) or cwd
            continue
        if name in copy_verbs:
            values = named or positional[-1:]
        elif name in write_verbs:
            values = named or (positional if name in {"touch", "tee", "truncate"} else positional[:1])
        else:
            values = named + positional
        for value in values:
            add(value)
    return targets
