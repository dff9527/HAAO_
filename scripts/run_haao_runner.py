#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging

from orchestrator.runner_client.config import RunnerClientConfig
from orchestrator.runner_client.daemon import RunnerDaemon


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the HAAO split-plane client runner")
    parser.add_argument("--env-file", default=".env", help="Path to the runner .env file")
    parser.add_argument("--once", action="store_true", help="Run one poll cycle and exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = RunnerClientConfig.from_env_file(args.env_file)
    daemon = RunnerDaemon(config)
    if args.once:
        daemon.run_once()
    else:
        daemon.run_forever()


if __name__ == "__main__":
    main()
