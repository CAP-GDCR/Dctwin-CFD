"""Custom errors for DCTwin."""


class DCTwinError(Exception):
    pass


class CFDConfigError(DCTwinError):
    pass


class MeshBuildError(OSError):
    pass


class FoamSolveError(OSError):
    pass
