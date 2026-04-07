"""Environment probe tool for Big Data Ops Agent.

Collects hardware, OS, network, and service information from a target host
to enable informed architecture decisions. This tool eliminates guesswork
by providing real resource data before any deployment planning.
"""

from __future__ import annotations

import logging
import subprocess
import textwrap

from langchain.tools import ToolRuntime, tool
from langgraph.typing import ContextT

from deerflow.agents.thread_state import ThreadState
from deerflow.bigdata.config.bigdata_config import get_bigdata_config

logger = logging.getLogger(__name__)

# Commands to gather environment data.  Each tuple is (label, command).
_PROBE_COMMANDS: list[tuple[str, str]] = [
    ("Hostname", "hostname -f 2>/dev/null || hostname"),
    ("OS", "cat /etc/os-release 2>/dev/null | grep -E '^(NAME|VERSION)=' | head -2 || uname -a"),
    ("Kernel", "uname -r"),
    ("CPU Cores", "nproc"),
    ("CPU Model", "lscpu 2>/dev/null | grep 'Model name' | head -1 | sed 's/Model name:\\s*//' || echo 'N/A'"),
    ("Total Memory (GB)", "free -g 2>/dev/null | awk '/^Mem:/{print $2}' || echo 'N/A'"),
    ("Available Memory (GB)", "free -g 2>/dev/null | awk '/^Mem:/{print $7}' || echo 'N/A'"),
    ("Disk Usage", "df -h / /data 2>/dev/null | tail -n +2 || df -h / | tail -n +2"),
    ("Java Version", "java -version 2>&1 | head -1 || echo 'Java not installed'"),
    ("Python Version", "python3 --version 2>&1 || python --version 2>&1 || echo 'Python not installed'"),
    ("Current User", "whoami"),
    ("Open Ports", "ss -tlnp 2>/dev/null | tail -20 || netstat -tlnp 2>/dev/null | tail -20 || echo 'Cannot query ports'"),
    ("ulimit (open files)", "ulimit -n 2>/dev/null || echo 'N/A'"),
    ("ulimit (max processes)", "ulimit -u 2>/dev/null || echo 'N/A'"),
    ("Docker Status", "docker --version 2>/dev/null && docker ps --format 'table {{.Names}}\\t{{.Status}}' 2>/dev/null | head -10 || echo 'Docker not installed'"),
]


def _run_local_probe(timeout: int = 60) -> str:
    """Run the probe commands on the local machine."""
    results: list[str] = []
    for label, cmd in _PROBE_COMMANDS:
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = (proc.stdout.strip() or proc.stderr.strip() or "N/A")
            # Truncate overly long output
            if len(output) > 500:
                output = output[:500] + "\n... (truncated)"
            results.append(f"**{label}**: {output}")
        except subprocess.TimeoutExpired:
            results.append(f"**{label}**: (timed out)")
        except Exception as e:
            results.append(f"**{label}**: (error: {e})")
    return "\n".join(results)


def _run_remote_probe(host: str, user: str, port: int, key_file: str | None, password: str | None, timeout: int = 60) -> str:
    """Run the probe commands on a remote host via SSH."""
    # Build the compound command
    compound_parts: list[str] = []
    for label, cmd in _PROBE_COMMANDS:
        # Wrap each command to output a labeled line
        escaped_label = label.replace("'", "'\\''")
        compound_parts.append(f"echo '=== {escaped_label} ===' && ({cmd}) 2>&1")
    compound_cmd = " && ".join(compound_parts)

    ssh_args = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        "-p", str(port),
    ]
    
    use_sshpass = False
    if key_file:
        ssh_args.extend(["-i", key_file])
    elif password:
        # Check if sshpass is installed
        try:
            subprocess.run(["sshpass", "-V"], capture_output=True, check=True)
            use_sshpass = True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return "Error: Password configured but `sshpass` is not installed."

    ssh_args.append(f"{user}@{host}")
    ssh_args.append(compound_cmd)

    if use_sshpass:
        ssh_args = ["sshpass", "-p", password] + ssh_args

    try:
        proc = subprocess.run(
            ssh_args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = proc.stdout.strip()
        if proc.returncode != 0 and proc.stderr.strip():
            output += f"\n\nSSH stderr:\n{proc.stderr.strip()}"

        # Parse the labeled output into a formatted result
        lines = output.split("\n")
        results: list[str] = []
        current_label = None
        current_value: list[str] = []
        for line in lines:
            if line.startswith("=== ") and line.endswith(" ==="):
                if current_label:
                    val = "\n".join(current_value).strip() or "N/A"
                    if len(val) > 500:
                        val = val[:500] + "\n... (truncated)"
                    results.append(f"**{current_label}**: {val}")
                current_label = line[4:-4]
                current_value = []
            else:
                current_value.append(line)
        if current_label:
            val = "\n".join(current_value).strip() or "N/A"
            if len(val) > 500:
                val = val[:500] + "\n... (truncated)"
            results.append(f"**{current_label}**: {val}")

        return "\n".join(results) if results else output

    except subprocess.TimeoutExpired:
        return f"Error: SSH connection to {host} timed out after {timeout}s"
    except Exception as e:
        return f"Error: Failed to probe {host}: {e}"


@tool("env_probe", parse_docstring=True)
def env_probe_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    target: str = "all",
) -> str:
    """Probe target hosts to collect hardware, OS, network, and service information.

    Use this tool BEFORE designing any deployment architecture. It returns
    CPU cores, total/available memory, disk space, open ports, Java version,
    current user, and system limits — everything needed to make informed
    resource allocation decisions.

    Args:
        description: Explain why you are probing this host. ALWAYS PROVIDE THIS PARAMETER FIRST.
        target: Target identifier. Use "all" to probe ALL registered hosts, "local" for the current machine, or a specific host name/IP.
    """
    config = get_bigdata_config()

    if target == "local":
        header = "## Environment Probe: localhost\n"
        return header + _run_local_probe(timeout=config.command_timeout)

    # Allow probing all registered hosts
    if target == "all":
        if not config.hosts:
            return "Error: No hosts are registered in the big data inventory. Tell the user to configure `bigdata.hosts` in config.yaml."
        
        all_results = []
        for host_cfg in config.hosts:
            header = f"\n## Environment Probe: {host_cfg.name} ({host_cfg.host})\n"
            result = _run_remote_probe(
                host=host_cfg.host,
                user=host_cfg.user,
                port=host_cfg.port,
                key_file=host_cfg.key_file,
                password=host_cfg.password,
                timeout=config.command_timeout,
            )
            all_results.append(header + result)
        
        return "\n".join(all_results)


    # Look up the host in the config inventory
    host_cfg = config.get_host(target) or config.get_host_by_ip(target)
    if host_cfg is None:
        allowed = ", ".join(config.host_names) or "(none configured)"
        return textwrap.dedent(f"""\
            Error: Host '{target}' not found in the registered inventory.
            Registered hosts: {allowed}

            To probe this host, add it to the `bigdata.hosts` section in config.yaml first.
            Alternatively, use target="local" to probe the current machine, or "all" to probe all registered hosts.
        """)

    header = f"## Environment Probe: {host_cfg.name} ({host_cfg.host})\n"
    return header + _run_remote_probe(
        host=host_cfg.host,
        user=host_cfg.user,
        port=host_cfg.port,
        key_file=host_cfg.key_file,
        password=host_cfg.password,
        timeout=config.command_timeout,
    )
