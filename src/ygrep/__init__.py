"""ygrep: grep for YAML, reporting line numbers and YAML paths."""

__version__ = "0.1.0"


def main() -> None:
    from .cli import main as _main

    raise SystemExit(_main())
