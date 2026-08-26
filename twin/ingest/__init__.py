from .kicad import parse_kicad_netlist
from .eagle import parse_eagle_sch
from .bom import merge_bom
from .binding import bind_models


def parse_design(path) -> "BoardIR":  # noqa: F821
    """Dispatch on design-file extension: KiCad .net or Eagle .sch."""
    from pathlib import Path
    suffix = Path(path).suffix.lower()
    if suffix == ".sch":
        return parse_eagle_sch(path)
    return parse_kicad_netlist(path)


__all__ = ["parse_kicad_netlist", "parse_eagle_sch", "parse_design",
           "merge_bom", "bind_models"]
