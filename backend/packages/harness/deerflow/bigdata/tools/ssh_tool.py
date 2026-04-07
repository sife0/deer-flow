"""SSH remote execution tool for Big Data Ops Agent.

Provides secure, allowlisted SSH command execution against registered
hosts in the big data cluster inventory. Contains built-in safety
checks for destructive commands.
"""

from __future__ import annotations

import logging
import re
import subprocess
import textwrap

from langchain.tools import ToolRuntime, tool
from langgraph.typing import ContextT

from deerflow.agents.thread_state import ThreadState
from deerflow.bigdata.config.bigdata_config import get_bigdata_config

logger = logging.getLogger(__name__)

# Patterns for commands that require extra caution
_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"\bhdfs\s+namenode\s+-format\b", "HDFS NameNode format will DESTROY all filesystem metadata"),
    (r"\bmkfs\b", "mkfs will format a filesystem partition"),
    (r"\brm\s+-rf\s+/", "rm -rf / is catastrophically destructive"),
    (r"\bDROP\s+(DATABASE|TABLE|INDEX)\b", "SQL DROP will permanently delete data"),
    (r"\bformat\b.*\b(namenode|datanode|journalnode)\b", "Formatting HDFS nodes destroys metadata"),
]

# Patterns for sensitive output that should be masked
_SENSITIVE_OUTPUT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(password|passwd|secret|token)\s*[=:]\s*\S+", re.IGNORECASE), r"\1=***REDACTED***"),
]


def _check_dangerous_command(command: str) -> str | None:
    """Check if a command matches known dangerous patterns.

    Returns a warning message if dangerous, None otherwise.
    """
    for pattern, warning in _DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return warning
    return None


def _mask_sensitive_output(output: str) -> str:
    """Mask sensitive information in command output."""
    for pattern, replacement in _SENSITIVE_OUTPUT_PATTERNS:
        output = pattern.sub(replacement, output)
    return output


@tool("ssh_exec", parse_docstring=True)
def ssh_exec_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    host: str,
    command: str,
    user: str | None = None,
    port: int | None = None,
) -> str:
    """Execute a command on a remote host via SSH.

    The target host MUST be registered in the `bigdata.hosts` section of
    config.yaml. Unregistered hosts are rejected for security.

    Args:
        description: Explain why you are executing this command. ALWAYS PROVIDE THIS PARAMETER FIRST.
        host: Target host name or IP. Must be in the registered inventory.
        command: The bash command to execute on the remote host.
        user: SSH user override (defaults to the user configured for this host).
        port: SSH port override (defaults to the port configured for this host).
    """
    config = get_bigdata_config()

    # --- Host allowlist validation ---
    host_cfg = config.get_host(host) or config.get_host_by_ip(host)
    if host_cfg is None:
        allowed_names = ", ".join(config.host_names) or "(none configured)"
        allowed_ips = ", ".join(config.host_ips) or "(none configured)"
        return textwrap.dedent(f"""\
            Error: Host '{host}' is NOT in the allowed inventory.

            Allowed host names: {allowed_names}
            Allowed host IPs:   {allowed_ips}

            For security, only pre-registered hosts can be accessed.
            Add the host to `bigdata.hosts` in config.yaml to allow access.
        """)

    # --- Dangerous command check ---
    danger_msg = _check_dangerous_command(command)
    if danger_msg:
        return textwrap.dedent(f"""\
            ⚠️ DANGEROUS COMMAND DETECTED ⚠️

            Command: {command}
            Warning: {danger_msg}

            This command was BLOCKED because it matches a known destructive pattern.
            If you truly need to execute this, use `ask_clarification` to get explicit
            user confirmation first, then retry with a clearly stated justification.
        """)

    # --- Resolve connection parameters ---
    effective_user = user or host_cfg.user
    effective_port = port or host_cfg.port
    effective_host = host_cfg.host  # Always use the IP/FQDN, not the logical name
    key_file = host_cfg.key_file

    # --- Build SSH command ---
    ssh_args = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        "-p", str(effective_port),
    ]

    use_sshpass = False
    password = host_cfg.password
    if key_file:
        ssh_args.extend(["-i", key_file])
    elif password:
        # Check if sshpass is installed
        try:
            subprocess.run(["sshpass", "-V"], capture_output=True, check=True)
            use_sshpass = True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return "Error: Password configured but `sshpass` is not installed. Please install it or use `key_file`."

    ssh_args.append(f"{effective_user}@{effective_host}")
    ssh_args.append(command)

    if use_sshpass:
        ssh_args = ["sshpass", "-p", password] + ssh_args

    logger.info(
        "ssh_exec: host=%s (%s@%s:%d), command=%s",
        host_cfg.name, effective_user, effective_host, effective_port, command[:100],
    )

    try:
        proc = subprocess.run(
            ssh_args,
            capture_output=True,
            text=True,
            timeout=config.command_timeout,
        )

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        # Mask sensitive info
        stdout = _mask_sensitive_output(stdout)
        stderr = _mask_sensitive_output(stderr)

        # Truncate excessively long output
        max_len = 4000
        if len(stdout) > max_len:
            stdout = stdout[:max_len] + "\n... (output truncated)"
        if len(stderr) > max_len:
            stderr = stderr[:max_len] + "\n... (stderr truncated)"

        parts: list[str] = [f"**Host**: {host_cfg.name} ({effective_host})"]
        parts.append(f"**Exit Code**: {proc.returncode}")

        if stdout:
            parts.append(f"**stdout**:\n```\n{stdout}\n```")
        if stderr:
            parts.append(f"**stderr**:\n```\n{stderr}\n```")

        if proc.returncode != 0:
            parts.append("**Status**: ❌ Command failed")
        else:
            parts.append("**Status**: ✅ Success")

        return "\n\n".join(parts)

    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {config.command_timeout}s on host {host_cfg.name}"
    except FileNotFoundError:
        return "Error: SSH client not found. Ensure `openssh-client` is installed."
    except Exception as e:
        return f"Error: SSH execution failed: {e}"
