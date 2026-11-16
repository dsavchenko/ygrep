"""Build a minimal, position-aware node tree from ruamel.yaml parser events.

We deliberately work at the event level instead of using a loader: loaded
objects lose the position of alias *uses* (an alias becomes the same object as
its anchor), but ygrep reports matches inside aliased content at the alias site.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError
from ruamel.yaml.events import (
    AliasEvent,
    DocumentEndEvent,
    DocumentStartEvent,
    MappingEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
    StreamEndEvent,
    StreamStartEvent,
)


class YamlTreeError(Exception):
    pass


@dataclass(eq=False)
class Scalar:
    text: str
    plain: bool  # plain style and untagged; needed to recognise the `<<` merge key
    line: int  # 1-based
    col: int  # 1-based


@dataclass(eq=False)
class Mapping:
    line: int
    col: int
    pairs: list[tuple[Node, Node]] = field(default_factory=list)


@dataclass(eq=False)
class Sequence:
    line: int
    col: int
    items: list[Node] = field(default_factory=list)


@dataclass(eq=False)
class Alias:
    name: str
    target: Node
    line: int
    col: int


Node = Scalar | Mapping | Sequence | Alias


def parse_documents(text: str) -> list[Node]:
    """Parse all documents of a YAML stream into node trees."""
    events = iter(YAML(typ="rt").parse(text))
    docs: list[Node] = []
    try:
        for ev in events:
            if isinstance(ev, (StreamStartEvent, DocumentEndEvent)):
                continue
            if isinstance(ev, StreamEndEvent):
                break
            if isinstance(ev, DocumentStartEvent):
                # Anchors are scoped to a single document.
                docs.append(_build(next(events), events, {}))
                continue
            raise YamlTreeError(f"unexpected event {type(ev).__name__}")
    except YamlTreeError:
        raise
    except MarkedYAMLError as exc:
        mark = exc.problem_mark or exc.context_mark
        where = f"line {mark.line + 1}, column {mark.column + 1}: " if mark else ""
        raise YamlTreeError(f"{where}{exc.problem or exc.context}") from exc
    except Exception as exc:  # ruamel raises a variety of error types
        raise YamlTreeError(str(exc).splitlines()[0]) from exc
    return docs


def _pos(ev) -> tuple[int, int]:
    return ev.start_mark.line + 1, ev.start_mark.column + 1


def _build(ev, events: Iterator, anchors: dict[str, Node]) -> Node:
    line, col = _pos(ev)
    if isinstance(ev, AliasEvent):
        if ev.anchor not in anchors:
            raise YamlTreeError(f"line {line}: undefined alias *{ev.anchor}")
        return Alias(ev.anchor, anchors[ev.anchor], line, col)
    if isinstance(ev, ScalarEvent):
        node: Node = Scalar(ev.value, bool(ev.implicit and ev.implicit[0]), line, col)
        if ev.anchor:
            anchors[ev.anchor] = node
        return node
    if isinstance(ev, MappingStartEvent):
        node = Mapping(line, col)
        if ev.anchor:  # register before children so recursive aliases resolve
            anchors[ev.anchor] = node
        while not isinstance(nxt := next(events), MappingEndEvent):
            key = _build(nxt, events, anchors)
            node.pairs.append((key, _build(next(events), events, anchors)))
        return node
    if isinstance(ev, SequenceStartEvent):
        node = Sequence(line, col)
        if ev.anchor:
            anchors[ev.anchor] = node
        while not isinstance(nxt := next(events), SequenceEndEvent):
            node.items.append(_build(nxt, events, anchors))
        return node
    raise YamlTreeError(f"line {line}: unexpected event {type(ev).__name__}")
