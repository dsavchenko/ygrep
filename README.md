# ygrep

grep for YAML. Searches keys and/or scalar values with a regex and reports each
match with its line number **and its YAML path**.

```console
$ ygrep nginx deploy.yaml
2:defaults.image: nginx:1.25
7:spec.containers[0].image: nginx:1.25
12:spec.notes: first line\nnginx here\n
```

## Install

```sh
uv tool install .      # or: pipx install .
```

## Usage

```
ygrep [options] PATTERN [FILE ...]
```

| Option | Meaning |
|---|---|
| `-k`, `--keys` | match mapping keys only |
| `-V`, `--values` | match scalar values only |
| `-p`, `--path GLOB` | only nodes whose full path matches GLOB (repeatable) |
| `-F` | PATTERN is a literal string |
| `-i` | case-insensitive |
| `-r` | recurse into directories (`*.yaml`, `*.yml`); defaults to `.` |
| `-H` / `-h` | force / suppress file names |
| `-l` / `-c` / `-q` | files with matches / count / quiet |
| `--color auto\|always\|never` | colourise output (`NO_COLOR` respected) |

With no FILE (and no `-r`), stdin is read. Exit status: 0 match, 1 no match, 2 error.

### Output

`[file:]line:[#doc:]path: value`

* Paths: `key.sub[0].leaf`; keys that aren't plain identifiers are JSON-quoted (`env."LOG.LEVEL"`). Root is `.`.
* `#N:` (0-based document index) appears only for multi-document files.
* A key match on a collection prints `path:` with no value.
* Newlines/tabs in values are shown escaped; the regex runs with `MULTILINE`.

### Path globs

`*` matches within one key, `[*]` any index, `[2]` a specific index, `**` any
number of segments, `"a.b"` a literal quoted key. Globs match the *whole* path:
use `spec.**` for a subtree.

```sh
ygrep -p 'spec.**.image' '' deploy.yaml     # every image under spec
ygrep -p 'items[*].name' -V '^web'  list.yaml
```

### Anchors, aliases, merge keys

Content reached through an alias (`*ref`) is searched at the alias's location
and path; `<<` merge keys are expanded (explicit keys override merged ones).
