"""Load user/generated model directories into the component registry."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_model_dir(path: str | Path) -> list[str]:
    """Import every non-test .py in a directory (registering its models).
    Returns the module names loaded."""
    loaded = []
    path = Path(path)
    for f in sorted(path.glob("*.py")):
        if f.name.startswith(("test_", "_")):
            continue
        name = f"twin_models_{f.stem}"
        if name in sys.modules:
            loaded.append(name)
            continue
        module_spec = importlib.util.spec_from_file_location(name, f)
        mod = importlib.util.module_from_spec(module_spec)
        sys.modules[name] = mod
        module_spec.loader.exec_module(mod)
        loaded.append(name)
    return loaded
