"""CLI entry point: `python -m jobscout run`."""

from __future__ import annotations

import argparse
import logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="jobscout")
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity: DEBUG, INFO, WARNING, ERROR. Default: INFO",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="Fetch, filter, rank, store, and report on jobs")

    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "run":
        from jobscout.pipeline import run

        run()


if __name__ == "__main__":
    main()
