"""Nudge: a simulated voice-assistant shopping experience over the approval-receipt core."""
import os
import sys

_VENDOR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor")
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)
