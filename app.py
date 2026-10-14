"""The Vercel entrypoint: it exports `app`, the public Makan site. See docs/DESIGN.md, Vercel.

Vercel imports this module while it builds, so nothing here may need a key or the network.
"""

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if importlib.util.find_spec("makan") is None:  # the package is not installed: use the source tree
    sys.path.insert(0, str(ROOT / "src"))

from makan.web.public import create_public_app  # noqa: E402

app = create_public_app(os.environ, web_dist=ROOT / "web" / "dist")
