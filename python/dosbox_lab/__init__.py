# SPDX-License-Identifier: GPL-2.0-or-later
"""dosbox-lab: control and analysis of a DOSBox-X fork for AI agents."""
from .errors import DmcpError
from .instance import Instance, Mount
from .api import Dosbox
from .batch import run_batch
from .scenario import load_scenario, play_scenario

__all__ = ["DmcpError", "Instance", "Mount", "Dosbox", "run_batch", "load_scenario", "play_scenario"]
