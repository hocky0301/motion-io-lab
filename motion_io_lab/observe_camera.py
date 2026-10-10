"""Name kept for the earlier command line: ``python -m motion_io_lab.observe_camera ...``.

It is the same program as ``python -m motion_io_lab observe`` (see ``observe.py``).
"""
from .observe import main

if __name__ == "__main__":
    raise SystemExit(main())
