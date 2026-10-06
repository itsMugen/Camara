"""Allows ``python -m url_shortener``."""

import sys

from url_shortener.cli import main

sys.exit(main())
