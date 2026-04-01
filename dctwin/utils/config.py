import os
import csv
import sys
import typing
import shutil
from typing import TextIO, Union
from loguru import logger
from pathlib import Path
import datetime


class CFDConfig:
    """CFD configuration"""

    SOLVER_TURBULENCE: bool

    def __init__(self, base_config, base_size: float = 0.18) -> None:
        self.base_config: DCTwinConfig = base_config
        self.geometry_file = Path(os.environ.get("GEOMETRY_FILE", ""))
        self.mesh_dir: Path = Path(os.environ.get("MESH_DIR", ""))
        self.object_mesh_index: Path = Path(
            os.environ.get("OBJECT_MESH_INDEX", "object_mesh_index.json")
        )
        self.case_dir: Path = Path(os.environ.get("CFD_CASE_DIR", ""))
        self.dry_run: bool = False
        self.base_size: float = base_size

        self.file_handler: TextIO = TextIO()
        self.log_handler: csv.DictWriter = csv.DictWriter(
            self.file_handler, fieldnames=["time", "mode", "value"]
        )
        self.PRESERVE_FOAM_LOG = (
            os.environ.get("BACKEND_LOG_PRINT", "true").lower() == "true"
        )
        self.SOLVER_TURBULENCE = (
            os.environ.get("SOLVER_TURBULENCE", "true").lower() == "true"
        )

    def __setattr__(self, __name: str, __value: typing.Any) -> None:
        value = __value
        if __name in ("mesh_dir", "object_mesh_index"):
            value = Path(__value)
        super().__setattr__(__name, value)

    def set_case_dir(self, case_dir: typing.Union[str, Path]) -> None:
        self.case_dir = Path(case_dir)

    @property
    def geometry_dir(self) -> Path:
        return Path(self.case_dir, "constant/triSurface")


class DCTwinConfig:
    """Base configuration"""

    BACKEND_LOG_PRINT: bool

    def __init__(self, env: typing.MutableMapping = os.environ) -> None:
        self._environ = env
        self.LOG_DIR = self._environ.get("LOG_DIR", Path("log").absolute())
        self.case_dir: Path = Path(os.environ.get("OUTPUT_DIR", ""))

        self.BACKEND_LOG_PRINT = (
            self._environ.get("BACKEND_LOG_PRINT", "true").lower() == "true"
        )

        self.cfd = CFDConfig(self)

        self.file_handler: TextIO = TextIO()
        self.log_handler: csv.DictWriter = csv.DictWriter(
            self.file_handler, fieldnames=["time", "mode", "value"]
        )

    def set_log_dir(self, log_dir: typing.Union[str, Path]) -> None:
        self.LOG_DIR = Path(log_dir)


config: DCTwinConfig = DCTwinConfig()
