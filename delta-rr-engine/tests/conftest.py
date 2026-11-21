"""Shared test fixtures."""

import os
import sys

# Ensure src/ is on the path for production code imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
