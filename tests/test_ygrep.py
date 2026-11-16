import io
import textwrap

import pytest

from ygrep.cli import main
from ygrep.paths import PathGlob, PathGlobError, format_path

DEPLOY = textwrap.dedent(
    """\
    defaults: &defaults
      image: nginx:1.25
      pullPolicy: Always
    spec:
      containers:
        - name: web
          <<: *defaults
          pullPolicy: IfNotPresent
        - name: sidecar
          image: "busybox"
          env: {LOG.LEVEL: debug}
      notes: |
        first line
        nginx here
    """
)


@pytest.fixture
def deploy(tmp_path):
    p = tmp_path / "deploy.yaml"
    p.write_text(DEPLOY)
    return p


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(["--color=never", *map(str, argv)], out=out, err=err)
    return code, out.getvalue().splitlines(), err.getvalue()


def test_value_and_key_matches_with_line_and_path(deploy):
    code, lines, _ = run("nginx", deploy)
    assert code == 0
    assert lines == [
        "2:defaults.image: nginx:1.25",
        "7:spec.containers[0].image: nginx:1.25",  # reported at the alias site
        r"12:spec.notes: first line\nnginx here\n",
    ]


def test_keys_only(deploy):
    _, lines, _ = run("-k", "^image$", deploy)
    assert [line.split(":")[1] for line in lines] == [
        "defaults.image",
        "spec.containers[0].image",
        "spec.containers[1].image",
    ]
    _, lines, _ = run("-k", "nginx", deploy)
    assert lines == []


def test_values_only(deploy):
    code, lines, _ = run("-V", "-i", "policy", deploy)
    assert code == 1 and lines == []
    _, lines, _ = run("-V", "^web$", deploy)
    assert lines == ["6:spec.containers[0].name: web"]


def test_merge_key_explicit_value_wins(deploy):
    _, lines, _ = run("-p", "spec.containers[0].*", "", deploy)
    assert lines == [
        "6:spec.containers[0].name: web",
        "7:spec.containers[0].image: nginx:1.25",
        "8:spec.containers[0].pullPolicy: IfNotPresent",
    ]


def test_path_globs(deploy):
    _, lines, _ = run("-p", "spec.**.image", "", deploy)
    assert len(lines) == 2
    _, lines, _ = run("-p", "**.name", "-p", '**."LOG.LEVEL"', "", deploy)
    assert [line.split(":", 2)[1] for line in lines] == [
        "spec.containers[0].name",
        "spec.containers[1].name",
        'spec.containers[1].env."LOG.LEVEL"',
    ]


def test_fixed_strings(tmp_path):
    p = tmp_path / "a.yaml"
    p.write_text("a: 1.5\nb: 105\n")
    assert run("-F", "1.5", p)[1] == ["1:a: 1.5"]
    assert len(run("1.5", p)[1]) == 2


def test_multi_document_prefix(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("kind: A\n---\nkind: B\n")
    assert run("-V", "B", p)[1] == ["3:#1:kind: B"]


def test_alias_to_scalar_and_recursive_anchor(tmp_path):
    p = tmp_path / "a.yaml"
    p.write_text("a: &x hello\nb: *x\nr: &r [1, *r]\n")
    assert run("hello", p)[1] == ["1:a: hello", "2:b: hello"]
    assert run("^1$", p)[1] == ["3:r[0]: 1"]


def test_recursive_and_filenames(tmp_path, deploy):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.yml").write_text("image: nginx\n")
    (tmp_path / "ignored.txt").write_text("image: nginx\n")
    code, lines, _ = run("-r", "-k", "^image$", "-l", tmp_path)
    assert code == 0
    assert lines == [str(deploy), str(tmp_path / "sub" / "x.yml")]


def test_directory_without_recursive_is_error(tmp_path):
    code, _, err = run("x", tmp_path)
    assert code == 2 and "Is a directory" in err


def test_invalid_yaml(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("a: [1\n")
    code, _, err = run("x", p)
    assert code == 2 and "invalid YAML: line 2" in err


def test_count(deploy):
    assert run("-c", "-k", "image", deploy)[1] == ["3"]


@pytest.mark.parametrize(
    ("path", "text"),
    [
        ((), "."),
        (("a", 0, "b"), "a[0].b"),
        ((0, "a"), "[0].a"),
        (("a.b", "c d", "x"), '"a.b"."c d".x'),
    ],
)
def test_format_path(path, text):
    assert format_path(path) == text


@pytest.mark.parametrize(
    ("glob", "path", "ok"),
    [
        ("a.b", ("a", "b"), True),
        ("a.*", ("a", "b"), True),
        ("a.*", ("a", 0), False),
        ("a[*]", ("a", 0), True),
        ("a[1]", ("a", 0), False),
        ("**.b", ("b",), True),
        ("**.b", ("x", 0, "y", "b"), True),
        ("a.**", ("a",), True),
        ('"a.b"', ("a.b",), True),
        ('"a*"', ("ab",), False),
    ],
)
def test_path_glob(glob, path, ok):
    assert PathGlob(glob).matches(path) is ok


def test_bad_glob():
    with pytest.raises(PathGlobError):
        PathGlob("a[x]")
