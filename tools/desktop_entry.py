"""Desktop entry point and subprocess modes for the bundled runtime."""

import runpy
import sys

from generate_item_mapping import main as generate
from generate_item_mapping_gui import main as gui


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--generate":
        sys.argv.pop(1)
        raise SystemExit(generate())
    if len(sys.argv) > 2 and sys.argv[1] == "--objmc":
        sys.argv = sys.argv[2:]
        runpy.run_path(sys.argv[0], run_name="__main__")
    else:
        raise SystemExit(gui())
