"""Walk node trees into searchable entries and match them against a pattern."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum

from .paths import Path, PathGlob
from .tree import Alias, Mapping, Node, Scalar, Sequence

Loc = tuple[int, int]


class Scope(Enum):
    BOTH = "both"
    KEYS = "keys"
    VALUES = "values"


@dataclass
class Entry:
    """One addressable spot: a mapping key and/or a scalar value at ``path``.

    For a mapping entry ``path`` ends with the key. ``key`` is None for sequence
    items and a root scalar; ``value`` is None when the value is a collection.
    """

    path: Path
    key: str | None
    key_loc: Loc | None
    value: str | None
    value_loc: Loc | None


@dataclass
class Match:
    doc: int
    line: int
    path: Path
    key: str | None
    value: str | None
    key_spans: list[tuple[int, int]]
    value_spans: list[tuple[int, int]]


def _key_text(node: Node) -> str:
    while isinstance(node, Alias):
        node = node.target
    if isinstance(node, Scalar):
        return node.text
    return "?"  # complex (collection) keys are rare; give them a placeholder segment


def _is_merge_key(node: Node) -> bool:
    return isinstance(node, Scalar) and node.plain and node.text == "<<"


def _deref(node: Node, loc: Loc | None) -> tuple[Node, Loc | None]:
    """Follow aliases; the first alias encountered pins the reported location."""
    while isinstance(node, Alias):
        if loc is None:
            loc = (node.line, node.col)
        node = node.target
    return node, loc


def _merge_sources(value: Node, loc: Loc | None) -> list[tuple[Mapping, Loc | None]] | None:
    node, loc = _deref(value, loc)
    if isinstance(node, Mapping):
        return [(node, loc)]
    if isinstance(node, Sequence):
        out = []
        for item in node.items:
            m, item_loc = _deref(item, loc)
            if not isinstance(m, Mapping):
                return None
            out.append((m, item_loc))
        return out
    return None


def _mapping_entries(mapping: Mapping, loc: Loc | None, seen: frozenset[int]):
    """Yield (key, key_node, value_node, loc) with YAML merge keys (``<<``) expanded.

    Explicit keys win over merged ones; earlier merge sources win over later ones.
    """
    # Entries come out in source order, with merged ones at the position of `<<`.
    entries: list[tuple[bool, tuple]] = []  # (is_merged, entry)
    for k, v in mapping.pairs:
        sources = _merge_sources(v, loc) if _is_merge_key(k) else None
        if sources is None:
            entries.append((False, (_key_text(k), k, v, loc)))
            continue
        for src, src_loc in sources:
            if id(src) not in seen:
                entries.extend((True, e) for e in _mapping_entries(src, src_loc, seen | {id(src)}))
    taken = {e[0] for merged, e in entries if not merged}
    for merged, entry in entries:
        if merged:
            if entry[0] in taken:
                continue
            taken.add(entry[0])
        yield entry


def iter_entries(node: Node) -> Iterator[Entry]:
    yield from _walk(node, (), None, None, frozenset())


def _walk(node: Node, path: Path, key, key_loc, seen: frozenset[int], loc: Loc | None = None):
    node, loc = _deref(node, loc)
    if isinstance(node, Scalar):
        yield Entry(path, key, key_loc, node.text, loc or (node.line, node.col))
        return
    if key is not None:
        yield Entry(path, key, key_loc, None, None)
    if id(node) in seen:  # recursive anchor
        return
    seen = seen | {id(node)}
    if isinstance(node, Mapping):
        for text, k, v, entry_loc in _mapping_entries(node, loc, seen):
            k_loc = entry_loc or (k.line, k.col)
            yield from _walk(v, (*path, text), text, k_loc, seen, entry_loc)
    else:
        for i, item in enumerate(node.items):
            yield from _walk(item, (*path, i), None, None, seen, loc)


class Matcher:
    def __init__(
        self,
        pattern: str,
        *,
        fixed: bool = False,
        ignore_case: bool = False,
        scope: Scope = Scope.BOTH,
        path_globs: list[str] | None = None,
    ):
        flags = re.MULTILINE | (re.IGNORECASE if ignore_case else 0)
        self.regex = re.compile(re.escape(pattern) if fixed else pattern, flags)
        self.scope = scope
        self.globs = [PathGlob(g) for g in path_globs or []]

    def _spans(self, text: str | None) -> list[tuple[int, int]]:
        if text is None:
            return []
        # Empty matches (e.g. pattern '' or '^') still count as hits.
        return [m.span() for m in self.regex.finditer(text)]

    def search(self, doc_index: int, root: Node) -> Iterator[Match]:
        for e in iter_entries(root):
            if self.globs and not any(g.matches(e.path) for g in self.globs):
                continue
            key_spans = self._spans(e.key) if self.scope is not Scope.VALUES else []
            value_spans = self._spans(e.value) if self.scope is not Scope.KEYS else []
            if not key_spans and not value_spans:
                continue
            loc = e.key_loc if key_spans else e.value_loc
            yield Match(doc_index, loc[0], e.path, e.key, e.value, key_spans, value_spans)
