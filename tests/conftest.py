import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CUSTOM_COMPONENTS_DIR = ROOT / 'custom_components'
INTEGRATION_DIR = CUSTOM_COMPONENTS_DIR / 'hass-govee-h6199'

# Ensure repo root is on sys.path
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Ensure top-level custom_components namespace exists
cc_name = 'custom_components'
cc = sys.modules.get(cc_name)
if cc is None:
    cc = types.ModuleType(cc_name)
    cc.__path__ = [str(CUSTOM_COMPONENTS_DIR)]
    sys.modules[cc_name] = cc
else:
    if not hasattr(cc, '__path__'):
        cc.__path__ = [str(CUSTOM_COMPONENTS_DIR)]
    elif str(CUSTOM_COMPONENTS_DIR) not in cc.__path__:
        cc.__path__.append(str(CUSTOM_COMPONENTS_DIR))

# Load the hyphenated integration as package: custom_components.hass_govee_h6199
pkg_name = 'custom_components.hass_govee_h6199'
if pkg_name not in sys.modules:
    init_file = INTEGRATION_DIR / '__init__.py'
    spec = importlib.util.spec_from_file_location(
        pkg_name, init_file, submodule_search_locations=[str(INTEGRATION_DIR)]
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[pkg_name] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)  # type: ignore[assignment]

# Optional legacy alias
sys.modules.setdefault('hass_govee_h6199', sys.modules[pkg_name])
