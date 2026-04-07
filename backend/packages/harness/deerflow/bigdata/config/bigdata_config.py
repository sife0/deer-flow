"""Pydantic configuration models for Big Data infrastructure.

These models define the shape of the `bigdata` section in config.yaml,
covering host inventory, SSH credentials, and connection defaults.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class HostConfig(BaseModel):
    """A single managed host entry in the big data cluster inventory."""

    name: str = Field(..., description="Logical name for this host (e.g., 'hadoop-master')")
    host: str = Field(..., description="IP address or FQDN")
    user: str = Field(default="root", description="SSH login user")
    port: int = Field(default=22, description="SSH port")
    key_file: str | None = Field(default=None, description="Path to SSH private key file")
    password: str | None = Field(default=None, description="SSH password (alternative to key_file)")
    roles: list[str] = Field(
        default_factory=list,
        description="Optional: Pre-assigned roles. Usually the agent will assign these dynamically during deployment.",
    )

    model_config = ConfigDict(extra="allow")


class BigDataConfig(BaseModel):
    """Top-level configuration for the bigdata section in config.yaml.

    Example YAML:
        bigdata:
          hosts:
            - name: hadoop-1
              host: 192.168.1.10
              user: root
              password: "my-password"  # Or use key_file
          ssh_timeout: 30
          command_timeout: 300
    """

    hosts: list[HostConfig] = Field(default_factory=list, description="Managed host inventory")
    ssh_timeout: int = Field(default=30, description="SSH connection timeout in seconds")
    command_timeout: int = Field(default=300, description="Remote command execution timeout in seconds")

    model_config = ConfigDict(extra="allow")

    def get_host(self, name: str) -> HostConfig | None:
        """Look up a host by its logical name."""
        return next((h for h in self.hosts if h.name == name), None)

    def get_host_by_ip(self, ip: str) -> HostConfig | None:
        """Look up a host by its IP or FQDN."""
        return next((h for h in self.hosts if h.host == ip), None)

    def get_hosts_by_role(self, role: str) -> list[HostConfig]:
        """Return all hosts that have a specific role."""
        return [h for h in self.hosts if role in h.roles]

    @property
    def host_names(self) -> list[str]:
        """Return all registered host names (for allowlist validation)."""
        return [h.name for h in self.hosts]

    @property
    def host_ips(self) -> list[str]:
        """Return all registered host IPs (for allowlist validation)."""
        return [h.host for h in self.hosts]


# ---------------------------------------------------------------------------
# Singleton accessor (mirrors the pattern in deerflow.config.app_config)
# ---------------------------------------------------------------------------

_bigdata_config: BigDataConfig | None = None


def get_bigdata_config() -> BigDataConfig:
    """Load or return the cached BigDataConfig from the app config.

    Falls back to an empty config if the `bigdata` section is absent.
    """
    global _bigdata_config

    if _bigdata_config is not None:
        return _bigdata_config

    try:
        from deerflow.config.app_config import get_app_config

        app_cfg = get_app_config()
        raw = getattr(app_cfg, "bigdata", None)
        if raw is None:
            _bigdata_config = BigDataConfig()
        elif isinstance(raw, dict):
            _bigdata_config = BigDataConfig.model_validate(raw)
        elif isinstance(raw, BigDataConfig):
            _bigdata_config = raw
        else:
            _bigdata_config = BigDataConfig()
    except Exception:
        _bigdata_config = BigDataConfig()

    return _bigdata_config


def reset_bigdata_config() -> None:
    """Clear the cached config (useful for testing)."""
    global _bigdata_config
    _bigdata_config = None
