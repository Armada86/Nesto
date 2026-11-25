"""Streamlit keeps imported modules in memory. When a new version of the app is deployed while the server process keeps
running, app.py is new but modules such as db.py can still be the old ones ("module 'db' has no attribute ...").
reload_changed() reloads every listed module whose file is newer than the version in memory (or whose version in memory
is unknown): call it before the other local imports of app.py."""
from __future__ import annotations

import importlib
import os
import sys


def reload_changed(names) -> list[str]:
    """Reload the listed local modules that changed on disk since they were loaded; returns the names that were reloaded."""
    done = []
    for name in names:
        mod = sys.modules.get(name)
        path = getattr(mod, "__file__", None)
        if mod is None or not path or not os.path.exists(path):
            continue
        mtime = os.path.getmtime(path)
        if getattr(mod, "_nesto_loaded_mtime", None) is None or mtime > mod._nesto_loaded_mtime:
            mod = importlib.reload(mod)
            mod._nesto_loaded_mtime = mtime
            done.append(name)
    return done
