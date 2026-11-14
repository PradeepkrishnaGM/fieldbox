"""Shared test helpers.

Slow tests (more than about 2 s) are decorated with ``@slow`` and run only
when ``FIELDBOX_SLOW=1`` is set:

    python -m unittest discover -s tests                   # quick
    FIELDBOX_SLOW=1 python -m unittest discover -s tests   # full
"""

import os
import unittest

slow = unittest.skipUnless(os.environ.get("FIELDBOX_SLOW") == "1", "slow")
