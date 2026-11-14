"""Edge-case suite (doc 15 section 12, doc 17 section 11, doc 18 section 7, doc 19 section 6: 200 cases).

`edge_support` is importable from every test here (importlib mode does not add sibling folders to the path)."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
