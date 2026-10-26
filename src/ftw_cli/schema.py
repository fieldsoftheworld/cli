"""``ftw schema``: each command's parameters as JSON Schema, for agents."""

import inspect
import types
import typing
from pathlib import Path

from cyclopts import App

SCALARS = {
    str: "string",
    Path: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def commands(app: App, path: tuple[str, ...] = ("ftw",)) -> dict[str, App]:
    """Every runnable command under ``app``, keyed by how it is typed: ``ftw data query``."""
    found = {}
    for name in app:
        if name.startswith("-"):
            continue
        sub = app[name]
        if sub.default_command:
            found[" ".join((*path, name))] = sub
        found |= commands(sub, (*path, name))
    return found


def schema(command: App) -> dict:
    properties, required, positional = {}, [], []
    for arg in command.assemble_argument_collection(parse_docstring=True):
        name = arg.name.lstrip("-")
        prop = _type(arg.hint) | {"description": arg.parameter.help or ""}
        if len(arg.names) > 1:
            prop["aliases"] = list(arg.names)
        if (default := arg.field_info.default) is not inspect.Parameter.empty:
            prop["default"] = str(default) if isinstance(default, Path) else default
        if arg.required:
            required.append(name)
        if arg.index is not None:
            positional.append(name)
        properties[name] = prop
    return {
        "description": command.help,
        "type": "object",
        "properties": properties,
        "required": required,
        "positional": positional,
    }


def _type(hint: object) -> dict:
    args = typing.get_args(hint)
    if typing.get_origin(hint) is typing.Literal:
        return {"type": "string", "enum": list(args)}
    if isinstance(hint, types.UnionType) and type(None) in args:
        (inner,) = [a for a in args if a is not type(None)]
        return {"type": [_type(inner)["type"], "null"]}
    return {"type": SCALARS.get(hint, "string")}
