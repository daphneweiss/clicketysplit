"""Command-line entry point for clicketysplit."""

from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path

from .demo_assets import setup_demo_experiment
from .server import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clicketysplit")
    parser.add_argument("--version", action="store_true")
    sub = parser.add_subparsers(dest="cmd")

    p_serve = sub.add_parser("serve", help="Launch the segmenter")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=5000)
    p_serve.add_argument(
        "--experiment",
        type=Path,
        default=None,
        help="Path to a clicketysplit.json (default: pick one in the GUI)",
    )
    p_serve.add_argument(
        "--open",
        action="store_true",
        help="Open the browser automatically",
    )

    p_demo = sub.add_parser("demo", help="Run with bundled demo data")
    p_demo.add_argument("--port", type=int, default=5000)

    args = parser.parse_args(argv)

    if args.version:
        from . import __version__

        print(__version__)
        return 0

    if args.cmd == "demo":
        exp_path = setup_demo_experiment()  # extracts bundled data to a tmp dir
        return _run(
            host="127.0.0.1",
            port=args.port,
            experiment=exp_path,
            open_browser=True,
        )

    if args.cmd == "serve":
        return _run(
            host=args.host,
            port=args.port,
            experiment=args.experiment,
            open_browser=args.open,
        )

    parser.print_help()
    return 1


def _run(host: str, port: int, experiment: Path | None, open_browser: bool) -> int:
    app = create_app(experiment_path=experiment)
    url = f"http://{host}:{port}"
    print(f"\n  clicketysplit\n  {url}\n")
    if open_browser:
        webbrowser.open(url)
    app.run(host=host, port=port)
    return 0
