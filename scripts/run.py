import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
os.environ["PYTHONPATH"] = str(root / "src")
from visor.__main__ import main

main()

