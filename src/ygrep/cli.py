"""Command-line interface: grep-like search over YAML keys and values."""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Iterator
from typing import TextIO

from . import __version__
from .paths import PathGlobError, format_path
from .search import Match, Matcher, Scope
from .tree import YamlTreeError, parse_documents

YAML_SUFFIXES = (".yaml", ".yml")

COLORS = {
    "file": "35",  # magenta
    "line": "32",  # green
    "sep": "36",  # cyan
    "path": "34",  # blue
    "match": "1;31",  # bold red
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ygrep",
        add_help=False,  # free up -h for --no-filename, as in grep
        description="Search YAML files for a pattern in keys and/or values. "
        "Each match is reported with its line number and YAML path.",
    )
    p.add_argument("pattern", help="regular expression (Python syntax) to search for")
    p.add_argument("files", nargs="*", metavar="FILE", help="files or directories ('-' for stdin)")

    scope = p.add_mutually_exclusive_group()
    scope.add_argument(
        "-k", "--keys", action="store_const", dest="scope", const=Scope.KEYS, help="match mapping keys only"
    )
    scope.add_argument(
        "-V",
        "--values",
        action="store_const",
        dest="scope",
        const=Scope.VALUES,
        help="match scalar values only",
    )
    p.set_defaults(scope=Scope.BOTH)

    p.add_argument(
        "-p",
        "--path",
        action="append",
        dest="paths",
        metavar="GLOB",
        help="only consider nodes whose full path matches GLOB "
        "(e.g. 'spec.**.image', 'items[*].name'); repeatable, OR-ed",
    )
    p.add_argument("-F", "--fixed-strings", action="store_true", help="treat PATTERN as a literal string")
    p.add_argument("-i", "--ignore-case", action="store_true", help="case-insensitive pattern match")
    p.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help=f"search directories recursively for {'/'.join(YAML_SUFFIXES)} files",
    )

    fn = p.add_mutually_exclusive_group()
    fn.add_argument(
        "-H",
        "--with-filename",
        action="store_true",
        dest="with_filename",
        default=None,
        help="always print the file name",
    )
    fn.add_argument(
        "-h", "--no-filename", action="store_false", dest="with_filename", help="never print the file name"
    )

    out = p.add_mutually_exclusive_group()
    out.add_argument(
        "-l", "--files-with-matches", action="store_true", help="print only names of matching files"
    )
    out.add_argument("-c", "--count", action="store_true", help="print a count of matches per file")
    out.add_argument("-q", "--quiet", action="store_true", help="print nothing; exit 0 on first match")

    p.add_argument("--color", "--colour", choices=["auto", "always", "never"], default="auto")
    p.add_argument("--help", action="help", help="show this help and exit")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


_DIR_ERROR = "\0dir-error"  # sentinel: a directory was given without -r


def iter_files(args: list[str], recursive: bool, err: TextIO) -> Iterator[str]:
    for arg in args:
        if arg != "-" and os.path.isdir(arg):
            if not recursive:
                print(f"ygrep: {arg}: Is a directory", file=err)
                yield _DIR_ERROR
                continue
            for root, dirs, names in os.walk(arg):
                dirs.sort()
                for name in sorted(names):
                    if name.endswith(YAML_SUFFIXES):
                        yield os.path.join(root, name)
        else:
            yield arg


class Printer:
    def __init__(self, out: TextIO, color: bool):
        self.out = out
        self.color = color

    def c(self, kind: str, text: str) -> str:
        return f"\033[{COLORS[kind]}m{text}\033[0m" if self.color and text else text

    def highlight(self, text: str, spans: list[tuple[int, int]]) -> str:
        parts, pos = [], 0
        for start, end in spans:
            if end > start:
                parts.append(_escape(text[pos:start]))
                parts.append(self.c("match", _escape(text[start:end])))
                pos = end
        parts.append(_escape(text[pos:]))
        return "".join(parts)

    def match(self, filename: str | None, multi_doc: bool, m: Match) -> None:
        sep = self.c("sep", ":")
        prefix = ""
        if filename is not None:
            prefix += self.c("file", filename) + sep
        prefix += self.c("line", str(m.line)) + sep
        if multi_doc:
            prefix += self.c("sep", f"#{m.doc}") + sep

        path = format_path(m.path)
        if m.key_spans and m.key is not None:
            # Re-render the last segment with the matched part highlighted.
            parent = format_path(m.path[:-1])
            last = format_path(m.path[-1:])
            if last == m.key:
                key_hl = self.highlight(m.key, m.key_spans)
            else:  # quoted key: highlight the whole segment
                key_hl = self.c("match", last)
            if parent == ".":
                path_str = key_hl
            else:
                path_str = self.c("path", parent) + self.c("path", ".") + key_hl
        else:
            path_str = self.c("path", path)

        line = prefix + path_str + ":"
        if m.value is not None:
            line += " " + self.highlight(m.value, m.value_spans)
        print(line, file=self.out)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")


def main(argv: list[str] | None = None, out: TextIO | None = None, err: TextIO | None = None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    args = build_parser().parse_args(argv)

    try:
        matcher = Matcher(
            args.pattern,
            fixed=args.fixed_strings,
            ignore_case=args.ignore_case,
            scope=args.scope,
            path_globs=args.paths,
        )
    except re.error as exc:
        print(f"ygrep: invalid pattern: {exc}", file=err)
        return 2
    except PathGlobError as exc:
        print(f"ygrep: {exc}", file=err)
        return 2

    inputs = args.files or (["."] if args.recursive else ["-"])
    with_filename = args.with_filename
    if with_filename is None:
        with_filename = len(inputs) > 1 or any(f != "-" and os.path.isdir(f) for f in inputs)

    if args.color == "always":
        color = True
    elif args.color == "never":
        color = False
    else:
        color = out.isatty() and "NO_COLOR" not in os.environ
    printer = Printer(out, color)

    found = had_error = False
    for path in iter_files(inputs, args.recursive, err):
        if path == _DIR_ERROR:
            had_error = True
            continue
        display = "(standard input)" if path == "-" else path
        try:
            if path == "-":
                text = sys.stdin.read()
            else:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            docs = parse_documents(text)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"ygrep: {path}: {getattr(exc, 'strerror', None) or exc}", file=err)
            had_error = True
            continue
        except YamlTreeError as exc:
            print(f"ygrep: {display}: invalid YAML: {exc}", file=err)
            had_error = True
            continue

        count = 0
        for i, doc in enumerate(docs):
            for m in matcher.search(i, doc):
                count += 1
                found = True
                if args.quiet:
                    return 0
                if args.files_with_matches:
                    break
                if not args.count:
                    printer.match(display if with_filename else None, len(docs) > 1, m)
            if count and args.files_with_matches:
                break

        if args.files_with_matches and count:
            print(printer.c("file", display), file=out)
        elif args.count:
            prefix = printer.c("file", display) + printer.c("sep", ":") if with_filename else ""
            print(f"{prefix}{count}", file=out)

    if had_error:
        return 2
    return 0 if found else 1
