"""Viz generator package — the subprocess that turns a topic + brief into
a working self-contained HTML visualization (single index.html + screenshot).

Public surface: cli.main (the subprocess entry point) and cli.build_parser
(exposed for contract tests).
"""

# Lazy import to avoid a RuntimeWarning when running
#   python -m backend.viz_generator.cli
# (Python imports the package __init__ before executing the sub-module, so an
# eager import here causes the cli module to be initialised twice.)


def __getattr__(name: str):
    if name in ("main", "build_parser"):
        from backend.viz_generator import cli as _cli  # noqa: PLC0415
        return getattr(_cli, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["main", "build_parser"]
