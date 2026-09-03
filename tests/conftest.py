import importlib.util
import sys
from pathlib import Path

# ``pllla_bridge`` (plugin root) and the shared fixtures (this dir) without installing anything.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

# Hermes loads this directory as the package ``pllla`` (plugins/pllla/); the
# adapter's relative imports need that name, so register it the same way.
if "pllla" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "pllla", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["pllla"] = _module
    _spec.loader.exec_module(_module)
