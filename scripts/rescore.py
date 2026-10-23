"""Re-score every job already in Supabase with the current scoring (same as `python -m jh.rescore`).

Usage:  python scripts/rescore.py [--dry-run] [--rules-only]
Or from GitHub: Actions → "Rescore all jobs" → Run workflow.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jh.rescore import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
