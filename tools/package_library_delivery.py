"""Stage external libraries/readme beside the exe; optionally publish an atomic zip."""
import argparse
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

root = Path(__file__).resolve().parents[1]
delivery = root / 'dist/library-auto'
parser = argparse.ArgumentParser()
parser.add_argument('--zip', action='store_true')
args = parser.parse_args()
assert len(list(delivery.glob('*.exe'))) == 1, 'Build the exe first'
sources = [(root / name, name) for name in
           ('invalid_customers.txt', 'opened_customers.txt', 'valid_customers.txt')]
sources.append((root / 'docs/delivery-readme.md', 'readme.md'))
for source, name in sources:
    fd, temporary = tempfile.mkstemp(dir=delivery, suffix='.tmp')
    try:
        with source.open('rb') as original, os.fdopen(fd, 'wb') as output:
            shutil.copyfileobj(original, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, delivery / name)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
if args.zip:
    fd, temporary = tempfile.mkstemp(dir=delivery.parent, suffix='.tmp')
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(delivery.iterdir()):
                if file.is_file():
                    archive.write(file, arcname=file.name)
        os.replace(temporary, delivery.parent / 'library-auto.zip')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
for file in sorted(delivery.iterdir()):
    print(file.name, file.stat().st_size)
