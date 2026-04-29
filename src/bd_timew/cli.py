"""Argparse setup and dispatch."""

# Section: get_cli_arguments — main parser + subparsers
# Section: queue subparser parent (--scope, --project-dir, --titles)
# Section: main() — dispatch each args.cmd to the appropriate cmd_* function


def main() -> None:
    """Entry point — parses args and dispatches to module subcommand handlers."""
    raise NotImplementedError("cli.main not yet wired")
