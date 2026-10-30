"""DOSBOX-MCP: Steuerung und Analyse eines DOSBox-X-Forks fuer KI-Agenten."""
from .errors import DmcpError
from .instance import Instance, Mount
from .api import Dosbox
from .batch import run_batch
from .scenario import load_scenario, play_scenario

__all__ = ["DmcpError", "Instance", "Mount", "Dosbox", "run_batch", "load_scenario", "play_scenario"]
