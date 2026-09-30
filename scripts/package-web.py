"""Bundle the production SPA into Python package data before building a release."""
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
source = root / 'colons/web/dist'
destination = root / 'colons/backend/colons_api/static'
if not (source / 'index.html').is_file():
    raise SystemExit('Build the web UI before packaging: cd colons/web && npm ci && npm run build')
if destination.exists():
    shutil.rmtree(destination)
shutil.copytree(source, destination)
print('Web UI bundled into colons_api/static')
