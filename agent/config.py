"""Configuration: load config.yaml + environment into a typed Config object.

Secrets never live in the YAML — OPENROUTER_API_KEY comes from the environment.
The Config is mutable at runtime (slash-commands /model, /effort, /cd adjust it).
"""
import os
import re
from dataclasses import dataclass, field

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # /amd4/microagent
DEFAULT_CONFIG_PATH = os.path.join(ROOT, "config.yaml")


@dataclass
class SSHConfig:
    host: str = "rpi4pmu"
    user: str = "ubuntu"
    port: int = 22
    guest_ssh_port: int = 2222           # host-forwarded port into the QEMU guest
    remote_qemu_dir: str = "qemu-pmu"    # dir on the remote holding run-vm-*.sh + images

    @property
    def target(self) -> str:
        return f"{self.user}@{self.host}"


@dataclass
class AutonomyConfig:
    gate_disk_flashing: bool = True
    destructive_patterns: list = field(default_factory=list)
    _compiled: list = field(default_factory=list, repr=False)

    def __post_init__(self):
        self.recompile()

    def recompile(self):
        self._compiled = [re.compile(p, re.I) for p in self.destructive_patterns]

    def is_destructive(self, cmd: str) -> str | None:
        """Return the matched pattern (truthy) if `cmd` needs confirmation, else None."""
        if not self.gate_disk_flashing or not cmd:
            return None
        for rx in self._compiled:
            if rx.search(cmd):
                return rx.pattern
        return None


@dataclass
class InternalServer:
    """One internal (vLLM/OpenAI-compatible) model server, keyed in config by its port."""
    alias: str = ""
    model: str = ""        # served model id; "" => auto-detect via /v1/models
    host: str = ""         # "" => use InternalConfig.host (servers may live on other machines)
    decode: str = ""       # "sse" | "json" | "auto"; "" => InternalConfig.decode (hub ports differ)


@dataclass
class InternalAlias:
    """A named selector that pins a specific model on a port (e.g. a thinking variant)."""
    port: int = 0
    model: str = ""        # served model id sent to that port (overrides the port's default)
    host: str = ""
    decode: str = ""


@dataclass
class InternalConfig:
    """Internal model servers. A bare port number (or an alias) selects one of these,
    routing the chat call to http://<host>:<port>/v1 instead of OpenRouter."""
    host: str = "10.123.51.179"
    scheme: str = "http"
    api_key_env: str = ""          # name of an env var holding a key (servers are usually keyless)
    reasoning: bool = False        # these reject OpenRouter's reasoning:{effort} — don't send it
    decode: str = "auto"           # default transport for unlisted ports (stream, then JSON fallback)
    ports: dict = field(default_factory=dict)     # int port -> InternalServer
    aliases: dict = field(default_factory=dict)   # name -> InternalAlias (model variant on a port)
    api_key: str = ""              # resolved from api_key_env at load() time

    def resolve(self, selector: str):
        """(host, port, model, decode) for a bare port number or a configured alias, else None.

        `model` may be "" — the caller then auto-detects it from the server's /v1/models.
        """
        sel = (selector or "").strip()
        if not sel:
            return None
        if sel.isdigit():
            port = int(sel)
            srv = self.ports.get(port)
            return ((srv.host or self.host) if srv else self.host,
                    port,
                    srv.model if srv else "",
                    (srv.decode or self.decode) if srv else self.decode)
        low = sel.lower()
        # named aliases first (they may pin a model variant on an otherwise-default port)
        for name, a in self.aliases.items():
            if name.lower() == low and a.port:
                srv = self.ports.get(a.port)
                host = a.host or (srv.host if srv else "") or self.host
                decode = a.decode or (srv.decode if srv else "") or self.decode
                return (host, a.port, a.model or (srv.model if srv else ""), decode)
        for port, srv in self.ports.items():
            if srv.alias and srv.alias.lower() == low:
                return (srv.host or self.host, port, srv.model, srv.decode or self.decode)
        return None


@dataclass
class Config:
    # LLM
    model: str = "deepseek"
    reasoning_effort: str = "high"
    max_turns: int = 60
    nudge: int = 2
    temperature: float = 0.3
    api_base: str = "https://openrouter.ai/api/v1"   # OpenRouter (the fallback provider)
    api_key: str = ""                                # OpenRouter key, from $OPENROUTER_API_KEY
    internal: "InternalConfig" = field(default_factory=InternalConfig)

    # Kernel tree / build
    linux_src: str = "/amd4/cpu/ebpf/linux-6.8-pi"
    build_script: str = "/amd4/cpu/ebpf/kernel/build-pi4.sh"
    config_fragment: str = "/amd4/cpu/ebpf/kernel/pi4-ebpf.cfg"
    run_scripts_dir: str = "/amd4/cpu/ebpf"
    cross_compile: str = "aarch64-linux-gnu-"
    arch: str = "arm64"
    deploy_image_name: str = "Image-microagent"     # name the Image is given on the remote

    # Remote / safety / TUI
    ssh: SSHConfig = field(default_factory=SSHConfig)
    autonomy: AutonomyConfig = field(default_factory=AutonomyConfig)
    show_thinking: bool = True
    spinner_hz: int = 10
    knowledge_pack: str = "knowledge/linux_pi_build.md"

    # Runtime state (not from YAML)
    root: str = ROOT
    active_dir: str = ""        # cwd tools operate in; defaults to linux_src

    def __post_init__(self):
        if not self.active_dir:
            self.active_dir = self.linux_src

    # --- derived paths -------------------------------------------------------
    @property
    def knowledge_pack_path(self) -> str:
        if os.path.isabs(self.knowledge_pack):
            return self.knowledge_pack
        return os.path.join(self.root, self.knowledge_pack)


def _coerce_ssh(d: dict) -> SSHConfig:
    d = d or {}
    return SSHConfig(
        host=d.get("host", "rpi4pmu"),
        user=d.get("user", "ubuntu"),
        port=int(d.get("port", 22)),
        guest_ssh_port=int(d.get("guest_ssh_port", 2222)),
        remote_qemu_dir=d.get("remote_qemu_dir", "qemu-pmu"),
    )


def _coerce_autonomy(d: dict) -> AutonomyConfig:
    d = d or {}
    return AutonomyConfig(
        gate_disk_flashing=bool(d.get("gate_disk_flashing", True)),
        destructive_patterns=list(d.get("destructive_patterns", [])),
    )


def _coerce_internal(d: dict) -> InternalConfig:
    d = d or {}
    ports = {}
    for k, v in (d.get("ports") or {}).items():
        v = v or {}
        ports[int(k)] = InternalServer(
            alias=v.get("alias", ""),
            model=v.get("model", ""),
            host=v.get("host", ""),
            decode=v.get("decode", ""),
        )
    aliases = {}
    for name, v in (d.get("aliases") or {}).items():
        v = v or {}
        aliases[str(name)] = InternalAlias(
            port=int(v.get("port", 0)),
            model=v.get("model", ""),
            host=v.get("host", ""),
            decode=v.get("decode", ""),
        )
    cfg = InternalConfig(
        host=d.get("host", "10.123.51.179"),
        scheme=d.get("scheme", "http"),
        api_key_env=d.get("api_key_env", ""),
        reasoning=bool(d.get("reasoning", False)),
        decode=d.get("decode", "auto"),
        ports=ports,
        aliases=aliases,
    )
    if cfg.api_key_env:
        cfg.api_key = os.environ.get(cfg.api_key_env, "")
    return cfg


def load(path: str | None = None) -> Config:
    """Load Config from YAML (falling back to dataclass defaults) + environment."""
    path = path or DEFAULT_CONFIG_PATH
    raw: dict = {}
    if os.path.exists(path):
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

    openrouter = raw.get("openrouter") or {}
    tui = raw.get("tui") or {}

    cfg = Config(
        model=str(raw.get("model", "8006")),
        reasoning_effort=raw.get("reasoning_effort", "high"),
        max_turns=int(raw.get("max_turns", 60)),
        nudge=int(raw.get("nudge", 2)),
        temperature=float(raw.get("temperature", 0.3)),
        api_base=openrouter.get("api_base", "https://openrouter.ai/api/v1"),
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        internal=_coerce_internal(raw.get("internal")),
        linux_src=raw.get("linux_src", "/amd4/cpu/ebpf/linux-6.8-pi"),
        build_script=raw.get("build_script", "/amd4/cpu/ebpf/kernel/build-pi4.sh"),
        config_fragment=raw.get("config_fragment", "/amd4/cpu/ebpf/kernel/pi4-ebpf.cfg"),
        run_scripts_dir=raw.get("run_scripts_dir", "/amd4/cpu/ebpf"),
        cross_compile=raw.get("cross_compile", "aarch64-linux-gnu-"),
        arch=raw.get("arch", "arm64"),
        deploy_image_name=raw.get("deploy_image_name", "Image-microagent"),
        ssh=_coerce_ssh(raw.get("ssh")),
        autonomy=_coerce_autonomy(raw.get("autonomy")),
        show_thinking=bool(tui.get("show_thinking", True)),
        spinner_hz=int(tui.get("spinner_hz", 10)),
        knowledge_pack=raw.get("knowledge_pack", "knowledge/linux_pi_build.md"),
    )
    # "max" is an accepted synonym for the highest effort tier OpenRouter exposes.
    if cfg.reasoning_effort.lower() in ("max", "maximum"):
        cfg.reasoning_effort = "high"
    return cfg
