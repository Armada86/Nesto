"""The router the G-code is made for, read from the machine definition (post/*.vmid) and the post processor
(post/*.gpp) of the SolidCAM post "Mariani - CR Onsrud 336G18CW7" (FANUC):

  * metric program, absolute mode, work offset G56, tool change "M6 T<n>", length offset "G43 H<n>",
  * programmable C axis (G0 C0. at the start of every tool and at the end),
  * 26 tool positions (BT40), spindle up to 15000 rpm, XY rapid 36000 mm/min, Z rapid 30000 mm/min,
  * coolant: flood M8 / M9, air blast M14,
  * AICC smoothing "G05.1 Q1 R<n>" for 2.5D toolpaths (n = the post's n_SmoothValue, default 7), "G05.1 Q0" at the end,
  * program name in angle brackets after the "%", file extension "anc".
"""
from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET

NAME = "Mariani - CR Onsrud 336G18CW7"
CONTROLLER = "FANUC"
POST_VERSION = "0.4"
WORK_OFFSET = 56            # hard-coded in the post (V0.2)
FILE_EXTENSION = "anc"
MAX_TOOL_NUMBER = 26        # Station_1 MaxPositions
MAX_RPM = 15000.0           # Spindle gear MaxSpin
RAPID_XY = 36000.0          # mm/min
RAPID_Z = 30000.0
MAX_FEED = 30000.0
DEFAULT_SMOOTHING = 7.0     # n_SmoothValue default

_VMID = pathlib.Path(__file__).parent / "post" / "Mariani_CR_Onsrud_336G18CW7.vmid"


def read_machine_file(path=_VMID) -> dict:
    """The limits that are written in the machine definition (used by a test to confirm the constants above)."""
    root = ET.parse(path).getroot()
    out = {"name": root.get("Name"), "controller": root.get("ControllerName")}
    for ax in root.iter("Axis"):
        out["rapid_" + ax.get("Name")] = float(ax.get("Rapid"))
    spin = next(root.iter("Gear"))
    out["max_rpm"] = float(spin.get("MaxSpin"))
    out["tool_positions"] = int(next(d for d in root.iter("Device") if d.get("HolderType")).get("MaxPositions"))
    return out
