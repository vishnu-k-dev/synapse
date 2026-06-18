#!/usr/bin/env python3
"""Thin entrypoint: `python run_experiment.py --mode offline --api petstore`."""
import sys

from evalkit.cli import main

if __name__ == "__main__":
    sys.exit(main())
