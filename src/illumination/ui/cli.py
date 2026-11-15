"""Illumination CLI entry point."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from illumination.config.settings import Settings
from illumination.runtime.composition import build_runtime
from illumination.service.errors import IlluminationError
from illumination.types.common import RunStatus
from illumination.types.plan import RunRequest


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command != "run":
        parser.print_help()
        return 2

    try:
        settings = Settings()
        runtime = build_runtime(settings, stub=args.stub, workspace_root=args.out)
        result = asyncio.run(
            runtime.run(
                RunRequest(
                    objective=args.objective,
                    max_steps=args.max_steps,
                )
            )
        )
    except (IlluminationError, ValueError, OSError) as exc:
        print(f"illumination: {exc}", file=sys.stderr)
        return 1

    out_dir: Path = args.out or settings.illumination_workspace_root
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / "result.json"
    result_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(str(result_path))
    return 0 if result.status in {RunStatus.SUCCEEDED, RunStatus.PARTIAL} else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="illumination")
    subparsers = parser.add_subparsers(dest="command")
    run = subparsers.add_parser("run", help="运行一次 harness")
    run.add_argument("--objective", required=True, help="要完成的目标")
    run.add_argument("--out", type=Path, default=None, help="产物目录")
    run.add_argument("--max-steps", type=int, default=8, help="计划步骤上限")
    run.add_argument(
        "--stub",
        action="store_true",
        help="使用离线确定性规划器，不调用外部模型",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
