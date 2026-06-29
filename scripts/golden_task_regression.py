from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.golden_tasks import DEFAULT_GOLDEN_FIXTURE, run_golden_task_regression


def main() -> int:
    parser = argparse.ArgumentParser(description="Run HAAO golden-task decomposition regression checks.")
    parser.add_argument("--fixture", default=str(DEFAULT_GOLDEN_FIXTURE))
    args = parser.parse_args()
    result = run_golden_task_regression(args.fixture)
    print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
