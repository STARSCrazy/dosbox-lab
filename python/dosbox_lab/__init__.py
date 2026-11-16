# SPDX-License-Identifier: GPL-2.0-or-later
"""dosbox-lab: control and analysis of a DOSBox-X fork for AI agents."""
__version__ = "0.1.0"

from .errors import DmcpError
from .instance import Instance, Mount
from .api import Dosbox
from .batch import run_batch
from .scenario import load_scenario, play_scenario

__all__ = ["__version__", "DmcpError", "Instance", "Mount", "Dosbox", "run_batch", "load_scenario", "play_scenario"]
