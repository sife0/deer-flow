"""Built-in subagent configurations."""

from .bash_agent import BASH_AGENT_CONFIG
from .bigdata_ops_agent import BIGDATA_OPS_CONFIG
from .general_purpose import GENERAL_PURPOSE_CONFIG

__all__ = [
    "GENERAL_PURPOSE_CONFIG",
    "BASH_AGENT_CONFIG",
    "BIGDATA_OPS_CONFIG",
]

# Registry of built-in subagents
BUILTIN_SUBAGENTS = {
    "general-purpose": GENERAL_PURPOSE_CONFIG,
    "bash": BASH_AGENT_CONFIG,
    "bigdata-ops": BIGDATA_OPS_CONFIG,
}
