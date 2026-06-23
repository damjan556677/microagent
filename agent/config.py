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
class Config:
    # LLM
    model: str = "deepseek"
    reasoning_effort: str = "high"
    max_turns: int = 60
    nudge: int = 2
    temperature: float = 0.3
    api_base: str = "https://openrouter.ai/api/v1"
    api_key: str = ""

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
        model=raw.get("model", "deepseek"),
        reasoning_effort=raw.get("reasoning_effort", "high"),
        max_turns=int(raw.get("max_turns", 60)),
        nudge=int(raw.get("nudge", 2)),
        temperature=float(raw.get("temperature", 0.3)),
        api_base=openrouter.get("api_base", "https://openrouter.ai/api/v1"),
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
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
