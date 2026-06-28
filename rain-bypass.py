#!/usr/bin/env python3
"""Legacy entry point for v1 installs. Prefer: python -m rain_bypass"""

from rain_bypass.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
