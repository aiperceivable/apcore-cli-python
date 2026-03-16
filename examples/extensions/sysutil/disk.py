"""system.disk — Get disk usage for a path (macOS/Linux)."""

import os

from pydantic import BaseModel, Field


class Input(BaseModel):
    path: str = Field("/", description="Filesystem path to check (default: /)")


class Output(BaseModel):
    path: str
    total: str
    used: str
    free: str
    percent_used: float


class SystemDisk:
    """Get disk usage statistics for a given path."""

    input_schema = Input
    output_schema = Output
    description = "Get disk usage statistics for a given path"

    def execute(self, inputs, context=None):
        path = inputs.get("path", "/")
        stat = os.statvfs(path)
        total = stat.f_frsize * stat.f_blocks
        free = stat.f_frsize * stat.f_bavail
        used = total - free

        def _fmt(b):
            for unit in ("B", "KB", "MB", "GB", "TB"):
                if b < 1024:
                    return f"{b:.1f} {unit}"
                b /= 1024
            return f"{b:.1f} PB"

        return {
            "path": path,
            "total": _fmt(total),
            "used": _fmt(used),
            "free": _fmt(free),
            "percent_used": round(used / total * 100, 1) if total else 0,
        }
