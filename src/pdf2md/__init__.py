"""pdf2md: Backwards-compatible compatibility layer for docforge."""

import importlib
import sys
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec

import docforge

__version__ = getattr(docforge, "__version__", "0.1.0")


class _AliasLoader(Loader):
    def __init__(self, mod):
        self.mod = mod

    def create_module(self, spec):
        return self.mod

    def exec_module(self, module):
        pass


class _Pdf2mdRedirectFinder(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "pdf2md" or fullname.startswith("pdf2md."):
            target_name = "docforge" + fullname[len("pdf2md"):]
            try:
                mod = importlib.import_module(target_name)
                return ModuleSpec(fullname, _AliasLoader(mod), origin=getattr(mod, "__file__", None))
            except ImportError:
                return None
        return None


if not any(isinstance(f, _Pdf2mdRedirectFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _Pdf2mdRedirectFinder())

for attr in dir(docforge):
    if not attr.startswith("__"):
        globals()[attr] = getattr(docforge, attr)
