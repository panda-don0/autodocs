import sys
from pathlib import Path

# The engine runs its scripts directly (python scripts/docs_sync.py), so they
# are plain modules rather than a package; put that folder on the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
