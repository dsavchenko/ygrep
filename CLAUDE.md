# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`ygrep` is a grep-like CLI for YAML: it matches a regex against mapping keys and/or scalar values and reports `[file:]line:[#doc:]path: value`. User-facing behaviour is documented in README.md.

## Commands

```sh
uv sync                                   # create .venv with deps
uv run ygrep PATTERN FILE...              # run the CLI
uv run pytest                             # all tests
uv run pytest tests/test_ygrep.py::test_path_glob -q   # single test
uv run ruff check . && uv run ruff format .            # lint + format
```

Note: invoking `ygrep` without file args reads stdin and will block; pass `</dev/null` in scripted checks.

## Architecture

Pipeline: `tree.parse_documents` → `search.iter_entries` → `search.Matcher` → `cli.Printer`.

- **`tree.py`** builds its own node tree (`Scalar`/`Mapping`/`Sequence`/`Alias`) from ruamel.yaml *parser events*, not from a loader. This is intentional: loaded objects lose the position of alias uses, and we must report matches inside aliased content at the alias site. Anchors are per-document; `Alias` keeps a pointer to its target node (recursive anchors are possible, so walkers track `seen` ids). Lines/cols are 1-based. All parse failures are normalised to `YamlTreeError`.
- **`search.py`** flattens a tree into `Entry` records (path, key + location, scalar value + location). `_deref` follows aliases, and the *first* alias encountered pins the reported location for everything beneath it. `<<` merge keys (plain, untagged scalar `<<`) are expanded in place with YAML precedence (explicit keys win, earlier merge sources win). `Matcher` applies scope (keys/values/both), `--path` globs, and the regex (always `re.MULTILINE`); a key match reports the key's line, otherwise the value's line.
- **`paths.py`** owns the path format (`a.b[0]."x.y"`) and `PathGlob`. The glob syntax deliberately mirrors the rendered path format; keep `format_path` and `_parse_glob` in sync when changing either. Globs match the full path (no implicit prefix match).
- **`cli.py`** mirrors grep conventions: `-h` is `--no-filename` (so argparse `add_help=False`, help is `--help`), filenames shown by default only for multiple inputs or directories, exit codes 0/1/2. `main()` takes `argv`, `out`, `err` for testability; tests call it directly rather than via subprocess.
