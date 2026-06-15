from __future__ import annotations

from pydantic import BaseModel


class Config(BaseModel):
    """Scan configuration.

    Phase 1 stub — holds no fields yet.  Exists so WorkerContext can be
    fully typed and picklable before the real Config is built in Phase 3.
    Phase 3 will replace this with the full validated model (start_dirs,
    exclude_dirs, data_handlers, max_workers, log_file, etc.).
    """
