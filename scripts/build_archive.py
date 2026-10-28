#!/usr/bin/env python3
"""Build a review ZIP from verified public files; no credentials or deployment."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
import zipfile

from verify_project import ROOT, public_files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='New ZIP outside the project tree')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(ROOT) or output.exists():
        parser.error('Choose a new output path outside the project tree')
    subprocess.run([sys.executable, str(ROOT / 'scripts/verify_project.py')], check=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in public_files():
            name = str(Path('aupu-cloud') / path.relative_to(ROOT))
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 28, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    print('Archive:', output.name)
    print('SHA256:', hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
