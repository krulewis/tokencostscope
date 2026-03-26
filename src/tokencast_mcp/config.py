"""ServerConfig dataclass — runtime configuration for the tokencast MCP server."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ServerConfig:
    """Holds resolved runtime configuration for the tokencast MCP server.

    All paths are absolute. Use :meth:`from_args` to construct from raw CLI
    argument strings. Call :meth:`ensure_dirs` at server startup to create
    required directories on disk.
    """

    calibration_dir: Path
    project_dir: Optional[Path]

    # ------------------------------------------------------------------
    # Derived paths (not fields — computed on demand)
    # ------------------------------------------------------------------

    @property
    def history_path(self) -> Path:
        return self.calibration_dir / "history.jsonl"

    @property
    def factors_path(self) -> Path:
        return self.calibration_dir / "factors.json"

    @property
    def active_estimate_path(self) -> Path:
        return self.calibration_dir / "active-estimate.json"

    @property
    def last_estimate_path(self) -> Path:
        return self.calibration_dir / "last-estimate.md"

    # ------------------------------------------------------------------
    # Constructor
    # ------------------------------------------------------------------

    @classmethod
    def from_args(
        cls,
        calibration_dir: Optional[str],
        project_dir: Optional[str],
    ) -> "ServerConfig":
        """Build a ServerConfig from raw CLI argument strings.

        Args:
            calibration_dir: Raw ``--calibration-dir`` value, or ``None``.
            project_dir: Raw ``--project-dir`` value, or ``None``.

        Returns:
            A fully resolved :class:`ServerConfig`.
        """
        if project_dir is not None:
            resolved_project_dir: Optional[Path] = Path(project_dir).expanduser().resolve()
        else:
            resolved_project_dir = None

        if calibration_dir is not None:
            resolved_calibration_dir = Path(calibration_dir).expanduser().resolve()
        elif resolved_project_dir is not None:
            resolved_calibration_dir = resolved_project_dir / "calibration"
        else:
            resolved_calibration_dir = Path.home() / ".tokencast" / "calibration"

        return cls(
            calibration_dir=resolved_calibration_dir,
            project_dir=resolved_project_dir,
        )

    def ensure_dirs(self) -> None:
        """Create calibration_dir on disk if it does not exist.

        Call this at server startup, not during config construction, so that
        tests can build a ServerConfig without touching the filesystem.
        """
        self.calibration_dir.mkdir(parents=True, exist_ok=True)
