#!/usr/bin/env python3
"""Check a candidate image with the runner already installed in production.

Run only against a local test Docker daemon. The frozen runner creates disposable,
network-isolated containers without production mounts or credentials.
"""

import argparse
import importlib.util
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    args = parser.parse_args()
    fixture = Path(__file__).parent / "tests/fixtures/pinned_portal_smoke.py"
    spec = importlib.util.spec_from_file_location("pinned_portal_smoke", fixture)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.smoke_image(args.image, "1000:1000", sys.stdout)
    print(f"Installed portal runner {runner.RUNNER_REVISION}: candidate smoke passed.")


if __name__ == "__main__":
    main()
