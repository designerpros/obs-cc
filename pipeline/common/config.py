"""
Configuration loader for post-stream extraction pipeline
"""
import os
from pathlib import Path
from typing import Dict, Any, Optional
import yaml
from loguru import logger

# Default config path
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "pipeline.yaml"
CONFIG_PATH = Path(os.getenv('PIPELINE_CONFIG_PATH', str(DEFAULT_CONFIG_PATH)))


class Config:
    """Pipeline configuration"""

    _instance: Optional['Config'] = None
    _config: Dict[str, Any] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_config()
        return cls._instance

    def _load_config(self):
        """Load configuration from YAML"""
        if not CONFIG_PATH.exists():
            logger.error(f"Configuration file not found: {CONFIG_PATH}")
            raise FileNotFoundError(f"Configuration file not found: {CONFIG_PATH}")

        try:
            with open(CONFIG_PATH, 'r') as f:
                self._config = yaml.safe_load(f)

            # Validate config is a dictionary
            if not isinstance(self._config, dict):
                raise ValueError("Configuration must be a YAML dictionary")

            # Expand environment variables
            self._expand_env_vars(self._config)

            logger.info(f"Loaded configuration from {CONFIG_PATH}")

        except yaml.YAMLError as e:
            logger.error(f"Invalid YAML in configuration file: {e}")
            raise ValueError(f"Invalid YAML in configuration file: {e}")
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            raise

    def _expand_env_vars(self, config: Any):
        """Recursively expand environment variables in config"""
        if isinstance(config, dict):
            for key, value in config.items():
                if isinstance(value, str) and value.startswith('${') and value.endswith('}'):
                    env_var = value[2:-1]
                    # Handle default values: ${VAR:-default}
                    if ':-' in env_var:
                        var_name, default = env_var.split(':-', 1)
                        config[key] = os.getenv(var_name, default)
                    else:
                        # For ${VAR} without default, log warning if not found
                        env_value = os.getenv(env_var)
                        if env_value is None:
                            logger.warning(
                                f"Environment variable '{env_var}' not set in config path '{key}'"
                            )
                        config[key] = env_value
                elif isinstance(value, (dict, list)):
                    self._expand_env_vars(value)
        elif isinstance(config, list):
            for item in config:
                self._expand_env_vars(item)

    def get(self, path: str, default: Any = None) -> Any:
        """
        Get configuration value by dot-separated path

        Args:
            path: Dot-separated path (e.g., 'ingestion.triggers.file_watcher.enabled')
            default: Default value if path not found

        Returns:
            Configuration value
        """
        keys = path.split('.')
        value = self._config

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value

    def get_all(self) -> Dict[str, Any]:
        """Get entire configuration"""
        return self._config.copy()

    # Convenience methods for common config sections

    @property
    def infrastructure(self) -> Dict[str, Any]:
        """Get infrastructure configuration"""
        return self.get('infrastructure', {})

    @property
    def ingestion(self) -> Dict[str, Any]:
        """Get ingestion configuration"""
        return self.get('ingestion', {})

    @property
    def transcription(self) -> Dict[str, Any]:
        """Get transcription configuration"""
        return self.get('transcription', {})

    @property
    def analysis(self) -> Dict[str, Any]:
        """Get analysis configuration"""
        return self.get('analysis', {})

    @property
    def extraction(self) -> Dict[str, Any]:
        """Get extraction configuration"""
        return self.get('extraction', {})

    @property
    def camera_switching(self) -> Dict[str, Any]:
        """Get camera switching configuration"""
        return self.get('camera_switching', {})

    @property
    def broll(self) -> Dict[str, Any]:
        """Get B-roll configuration"""
        return self.get('broll', {})

    @property
    def virality(self) -> Dict[str, Any]:
        """Get virality engine configuration"""
        return self.get('virality', {})

    @property
    def rendering(self) -> Dict[str, Any]:
        """Get rendering configuration"""
        return self.get('rendering', {})

    @property
    def metadata(self) -> Dict[str, Any]:
        """Get metadata configuration"""
        return self.get('metadata', {})

    @property
    def posting(self) -> Dict[str, Any]:
        """Get posting configuration"""
        return self.get('posting', {})

    @property
    def archival(self) -> Dict[str, Any]:
        """Get archival configuration"""
        return self.get('archival', {})

    @property
    def monitoring(self) -> Dict[str, Any]:
        """Get monitoring configuration"""
        return self.get('monitoring', {})

    @property
    def database_url(self) -> str:
        """Get PostgreSQL connection URL"""
        db = self.get('infrastructure.database', {})
        return (
            f"postgresql+asyncpg://{db.get('user')}:{db.get('password')}@"
            f"{db.get('host')}:{db.get('port')}/{db.get('database')}"
        )

    @property
    def redis_url(self) -> str:
        """Get Redis connection URL"""
        redis = self.get('infrastructure.redis', {})
        password = redis.get('password', '')
        auth = f":{password}@" if password else ""
        return f"redis://{auth}{redis.get('host')}:{redis.get('port')}/{redis.get('db', 0)}"


# Global config instance
config = Config()


def reload_config():
    """Reload configuration"""
    global config
    Config._instance = None
    config = Config()
    logger.info("Configuration reloaded")


logger.info("Configuration utilities loaded")
