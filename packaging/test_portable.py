"""Verify a built portable release; use --online for the strict live-site check."""
import argparse
from pathlib import Path
import subprocess
import sys

from package_release import BUNDLE
from smoke_test import check


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--online', action='store_true', help='also require all 25 sites to respond; not for offline CI')
    args = parser.parse_args()
    check(BUNDLE)
    if args.online:
        subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('test_portable_online.py'))], check=True)
