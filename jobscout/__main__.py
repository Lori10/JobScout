"""CLI entry point: `python -m jobscout run`."""

from __future__ import annotations

import argparse
import logging
import os


def main() -> None:
    parser = argparse.ArgumentParser(prog="jobscout")
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity: DEBUG, INFO, WARNING, ERROR. Default: INFO",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="Fetch, filter, rank, store, and report on jobs")

    serve_parser = subparsers.add_parser("serve", help="Start the dashboard (FastAPI + React) web server")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--config", default="config.yaml")
    serve_parser.add_argument("--profile", default="profile.yaml")
    serve_parser.add_argument("--db-path", default="data/jobscout.db")
    serve_parser.add_argument("--report-path", default="report.html")

    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "run":
        from jobscout.pipeline import run

        run()
    elif args.command == "serve":
        import uvicorn

        os.environ["JOBSCOUT_CONFIG_PATH"] = args.config
        os.environ["JOBSCOUT_PROFILE_PATH"] = args.profile
        os.environ["JOBSCOUT_DB_PATH"] = args.db_path
        os.environ["JOBSCOUT_REPORT_PATH"] = args.report_path
        uvicorn.run(
            "jobscout.web.app:app",
            host=args.host,
            port=args.port,
            log_level=args.log_level.lower(),
        )


if __name__ == "__main__":
    main()
