"""YAML path rendering (``spec.containers[0].image``) and path-glob matching.

A path is a tuple of segments: ``str`` for mapping keys, ``int`` for sequence
indices. Glob syntax mirrors the rendered form:

* ``*`` / ``?`` / ``[abc]``-style fnmatch wildcards within one key segment
* ``[*]`` any index, ``[3]`` a specific index
* ``**`` any number (including zero) of segments
* ``"a.b"`` a quoted key, matched literally
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import cache

Path = tuple["str | int", ...]

_BARE_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")


def format_path(path: Path) -> str:
    if not path:
        return "."
    out = []
    for seg in path:
        if isinstance(seg, int):
            out.append(f"[{seg}]")
        else:
            key = seg if _BARE_KEY.match(seg) else json.dumps(seg, ensure_ascii=False)
            out.append(f".{key}" if out else key)
    return "".join(out)


class PathGlobError(ValueError):
    pass


@dataclass(frozen=True)
class _Key:
    pattern: str
    literal: bool


@dataclass(frozen=True)
class _Index:
    index: int | None  # None == [*]


class _DoubleStar:
    pass


_DS = _DoubleStar()


def _parse_glob(glob: str) -> tuple:
    segs: list = []
    i, n = 0, len(glob)
    while i < n:
        c = glob[i]
        if c == ".":
            i += 1
        elif c == "[":
            j = glob.find("]", i)
            if j < 0:
                raise PathGlobError(f"unclosed '[' in path glob {glob!r}")
            inner = glob[i + 1 : j].strip()
            if inner == "*":
                segs.append(_Index(None))
            elif inner.isdigit():
                segs.append(_Index(int(inner)))
            else:
                raise PathGlobError(f"bad index [{inner}] in path glob {glob!r}")
            i = j + 1
        elif c == '"':
            try:
                key, end = json.JSONDecoder().raw_decode(glob, i)
            except json.JSONDecodeError as exc:
                raise PathGlobError(f"bad quoted key in path glob {glob!r}") from exc
            segs.append(_Key(key, literal=True))
            i = end
        else:
            m = re.compile(r"[^.\[\"]+").match(glob, i)
            tok = m.group()
            segs.append(_DS if tok == "**" else _Key(tok, literal=False))
            i = m.end()
    return tuple(segs)


class PathGlob:
    def __init__(self, glob: str):
        self.glob = glob
        self._segs = _parse_glob(glob)

    def matches(self, path: Path) -> bool:
        segs = self._segs

        @cache
        def m(pi: int, si: int) -> bool:
            if pi == len(segs):
                return si == len(path)
            p = segs[pi]
            if p is _DS:
                return any(m(pi + 1, k) for k in range(si, len(path) + 1))
            if si == len(path):
                return False
            seg = path[si]
            if isinstance(p, _Index):
                ok = isinstance(seg, int) and (p.index is None or p.index == seg)
            elif isinstance(seg, int):
                ok = False
            else:
                ok = seg == p.pattern if p.literal else fnmatchcase(seg, p.pattern)
            return ok and m(pi + 1, si + 1)

        return m(0, 0)
