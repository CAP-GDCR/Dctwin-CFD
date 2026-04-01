"""
Configuration management for CFD cases.
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

# Handle TOML import for different Python versions
try:
    import tomllib  # Python 3.11+
    _USE_TOMLLIB = True
except ImportError:
    import toml  # Fallback for older Python versions
    _USE_TOMLLIB = False


class CaseConfig:
    """
    Manages configuration for a CFD case.
    
    Loads and provides access to:
    - config.toml (workflow settings)
    - room.json (geometry and boundary conditions)
    """
    
    def __init__(self, case_dir: Path, config_file: str = "config.toml"):
        """
        Initialize case configuration.
        
        Args:
            case_dir: Path to the case directory
            config_file: Name of the TOML config file (default: config.toml)
        """
        self.case_dir = Path(case_dir).resolve()
        self.config_path = self.case_dir / config_file
        
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
        
        self._config: Dict[str, Any] = {}
        self._room_config: Optional[Dict[str, Any]] = None
        
        self._load_config()
    
    def _load_config(self) -> None:
        """Load the TOML configuration file."""
        try:
            if _USE_TOMLLIB:
                with open(self.config_path, "rb") as f:
                    self._config = tomllib.load(f)
            else:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self._config = toml.load(f)
        except Exception as e:
            raise ValueError(f"Failed to parse configuration file: {e}")
        
        logger.info(f"Loaded configuration from: {self.config_path}")
    
    def load_room_config(self) -> Dict[str, Any]:
        """Load the room.json configuration file."""
        if self._room_config is not None:
            return self._room_config
        
        room_json_path = self.resolve_path(self.get("paths.room_json", "model/room.json"))
        
        if not room_json_path.exists():
            raise FileNotFoundError(f"Room configuration not found: {room_json_path}")
        
        with open(room_json_path, "r", encoding="utf-8") as f:
            self._room_config = json.load(f)
        
        logger.info(f"Loaded room configuration from: {room_json_path}")
        return self._room_config
    
    @property
    def room_config(self) -> Dict[str, Any]:
        """Get the room configuration, loading if necessary."""
        return self.load_room_config()
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value using dot notation.
        
        Args:
            key: Dot-separated key path (e.g., "mesh.base_size")
            default: Default value if key not found
            
        Returns:
            Configuration value or default
        """
        keys = key.split(".")
        value = self._config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value
    
    def resolve_path(self, path_str: str) -> Path:
        """
        Resolve a path relative to the case directory.
        
        Args:
            path_str: Path string (absolute or relative)
            
        Returns:
            Resolved absolute path
        """
        path = Path(path_str)
        if path.is_absolute():
            return path
        return (self.case_dir / path).resolve()
    
    @property
    def mesh_config(self) -> Dict[str, Any]:
        """Get mesh-related configuration."""
        return self._config.get("mesh", {})
    
    @property
    def simulation_config(self) -> Dict[str, Any]:
        """Get simulation-related configuration."""
        return self._config.get("simulation", {})
    
    @property
    def process_config(self) -> Dict[str, Any]:
        """Get parallel processing configuration."""
        return self._config.get("process", {})
    
    @property
    def output_config(self) -> Dict[str, Any]:
        """Get output configuration."""
        return self._config.get("output", {})
    
    # Convenience properties for common settings
    @property
    def base_size(self) -> float:
        """Background mesh cell size."""
        return self.mesh_config.get("base_size", 0.2)
    
    @property
    def refinement_level(self) -> int:
        """Default surface refinement level."""
        return self.mesh_config.get("refinement_level", 1)
    
    @property
    def generate_mesh(self) -> bool:
        """Whether to generate mesh."""
        return self.mesh_config.get("generate_mesh", True)
    
    @property
    def force_mesh_generation(self) -> bool:
        """Whether to force mesh regeneration."""
        return self.mesh_config.get("force_mesh_generation", False)
    
    @property
    def mesh_cache_dir(self) -> Path:
        """Path to mesh cache directory."""
        return self.resolve_path(self.mesh_config.get("mesh_cache_dir", "Mesh"))
    
    @property
    def room_json_path(self) -> Path:
        """Path to room.json file."""
        return self.resolve_path(self.get("paths.room_json", "model/room.json"))
    
    def extract_sensors(self) -> List[Dict[str, float]]:
        """Extract sensor locations from room configuration."""
        sensors = []
        constructions = self.room_config.get("constructions", {})
        sensors_dict = constructions.get("sensors", {})

        for sensor_id, sensor_data in sensors_dict.items():
            geometry = sensor_data.get("geometry", {})
            location = geometry.get("location", {})
            if location:
                sensors.append({
                    "id": sensor_id,
                    "x": float(location.get("x", 0)),
                    "y": float(location.get("y", 0)),
                    "z": float(location.get("z", 0)),
                })
        return sensors
    
    def format_boundary_conditions(self) -> Dict[str, Any]:
        """Format boundary conditions from room configuration."""
        boundary_conditions = {
            "supply_air_temperatures": {},
            "supply_air_volume_flow_rates": {},
            "server_powers": {},
            "server_volume_flow_rates": {},
        }

        inputs = self.room_config.get("inputs", {})
        constructions = self.room_config.get("constructions", {})

        # ACU boundary conditions
        acus_inputs = inputs.get("acus", {})
        acus_constructions = constructions.get("acus", {})
        
        for acu_id, acu_input in acus_inputs.items():
            boundary_conditions["supply_air_temperatures"][acu_id] = float(
                acu_input.get("supply_air_temperature", 24.0)
            )
            boundary_conditions["supply_air_volume_flow_rates"][acu_id] = float(
                acu_input.get("supply_air_volume_flow_rate", 0.0)
            )

        for acu_id, acu_data in acus_constructions.items():
            if acu_id not in boundary_conditions["supply_air_temperatures"]:
                cooling = acu_data.get("cooling", {})
                operating = cooling.get("operating", {})
                boundary_conditions["supply_air_temperatures"][acu_id] = float(
                    operating.get("supply_air_temperature", 24.0)
                )
                boundary_conditions["supply_air_volume_flow_rates"][acu_id] = float(
                    operating.get("supply_air_volume_flow_rate", 0.0)
                )

        # Server boundary conditions
        servers_inputs = inputs.get("servers", {})
        racks = constructions.get("racks", {})

        for rack_id, rack_data in racks.items():
            rack_constructions = rack_data.get("constructions", {})
            servers = rack_constructions.get("servers", {})
            
            for server_id, server_data in servers.items():
                server_input = servers_inputs.get(server_id, {})
                power = float(server_input.get("power", server_data.get("power", 0.0)))
                flow_rate = float(server_input.get("volume_flow_rate", 
                    server_data.get("cooling", {}).get("operating", {}).get("volume_flow_rate", 0.0)))

                boundary_conditions["server_powers"][server_id] = power
                boundary_conditions["server_volume_flow_rates"][server_id] = flow_rate

        return boundary_conditions
