"""Kernel build / config / deploy / verify tools.

Build is LOCAL (cross-compile); QEMU runs on the configured remote host. Mirrors the
existing scripts: build-pi4.sh (full reconfigure) and run-vm-customk.sh (remote boot
with -cpu host,pmu=on). The guest is reached via a double SSH hop (host -> :2222).
Commands sent to the remote/guest are base64-wrapped so quoting is never an issue.
"""
import base64
import os
import re
import subprocess
import time

from .spec import schema, truncate

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10",
            "-o", "LogLevel=ERROR"]
# inline form for nested (guest) ssh invocations built into remote scripts
_GUEST_SSH_OPTS = ("-o BatchMode=yes -o StrictHostKeyChecking=no "
                   "-o UserKnownHostsFile=/dev/null -o LogLevel=ERROR")
_RELEASE_RE = re.compile(r"^\d+\.\d+\.\d+\S*$")


def _local(cmd, cwd=None, timeout=600, env=None, shell=False):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, errors="replace", env=env, shell=shell)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"(timed out after {timeout}s)"
    except Exception as e:
        return 1, f"(error: {e})"


def _build_env(cfg):
    env = dict(os.environ)
    env["ARCH"] = cfg.arch
    env["CROSS_COMPILE"] = cfg.cross_compile
    return env


def _tail(s, n=40):
    return "\n".join((s or "").splitlines()[-n:])


def _kernel_release(cfg):
    try:
        return open(os.path.join(cfg.linux_src, "include/config/kernel.release")).read().strip()
    except Exception:
        return "?"


def _image_path(cfg):
    return os.path.join(cfg.linux_src, "arch/arm64/boot/Image")


# ---------------------------------------------------------------- build / config
def build_linux(ctx, target: str = "Image", jobs: int = 0, reconfigure: bool = False,
                timeout: int = 3600) -> str:
    """Cross-compile the kernel locally. Incremental `make` by default (preserves .config
    and source edits); reconfigure=True re-runs build-pi4.sh (regenerates .config)."""
    cfg = ctx.cfg
    jobs = jobs or os.cpu_count() or 4
    if not os.path.exists(os.path.join(cfg.linux_src, "Makefile")):
        return f"(error: no kernel tree at {cfg.linux_src})"

    if reconfigure:
        rc, out = _local(["bash", cfg.build_script, str(jobs)], timeout=timeout)
        tag = "build-pi4.sh"
    else:
        rc, out = _local(["make", f"-j{jobs}", f"ARCH={cfg.arch}",
                          f"CROSS_COMPILE={cfg.cross_compile}", *target.split()],
                         cwd=cfg.linux_src, timeout=timeout, env=_build_env(cfg))
        tag = f"make {target}"

    img = _image_path(cfg)
    info = ""
    if os.path.exists(img):
        age = time.time() - os.path.getmtime(img)
        info = (f"\nImage: {img}  ({os.path.getsize(img)//1024} KiB, "
                f"built {age:.0f}s ago)\nkernel release: {_kernel_release(cfg)}")
    if rc != 0:
        return truncate(f"BUILD FAILED ({tag}, rc={rc}). Output tail:\n{_tail(out, 45)}{info}")
    return truncate(f"BUILD OK ({tag}).{info}\n\noutput tail:\n{_tail(out, 12)}")


def kconfig(ctx, op: str, option: str, value: str = "") -> str:
    """Read or change a .config option via scripts/config (then olddefconfig to normalize)."""
    cfg = ctx.cfg
    sc = os.path.join(cfg.linux_src, "scripts/config")
    if not os.path.exists(sc):
        return f"(error: {sc} not found)"
    opt = option if option.startswith("CONFIG_") else "CONFIG_" + option
    if op == "get":
        rc, out = _local([sc, "--file", ".config", "--state", opt], cwd=cfg.linux_src, timeout=30)
        return f"{opt} = {out.strip() or '(undef)'}"
    flag = {"enable": ["--enable", opt], "disable": ["--disable", opt],
            "module": ["--module", opt], "set": ["--set-val", opt, value]}.get(op)
    if flag is None:
        return f"(error: unknown op {op!r}; use get|enable|disable|module|set)"
    rc, out = _local([sc, "--file", ".config", *flag], cwd=cfg.linux_src, timeout=30)
    if rc != 0:
        return f"(error: scripts/config failed: {out.strip()[:200]})"
    rc2, out2 = _local(["make", f"ARCH={cfg.arch}", f"CROSS_COMPILE={cfg.cross_compile}",
                        "olddefconfig"], cwd=cfg.linux_src, timeout=120, env=_build_env(cfg))
    rc3, state = _local([sc, "--file", ".config", "--state", opt], cwd=cfg.linux_src, timeout=30)
    return f"{op} {opt} -> now {state.strip()} (olddefconfig rc={rc2}). Rebuild to apply."


def trace_build(ctx, target: str = "Image") -> str:
    """Show the toolchain + the actual commands Kbuild would run (make -n dry run)."""
    cfg = ctx.cfg
    rc, ver = _local([cfg.cross_compile + "gcc", "--version"], timeout=20)
    rc2, dry = _local(["make", "-n", f"ARCH={cfg.arch}", f"CROSS_COMPILE={cfg.cross_compile}",
                       *target.split()], cwd=cfg.linux_src, timeout=120, env=_build_env(cfg))
    return truncate(f"toolchain: {ver.splitlines()[0] if ver.strip() else '(unknown)'}\n"
                    f"CROSS_COMPILE={cfg.cross_compile}  ARCH={cfg.arch}\n\n"
                    f"`make -n {target}` (dry run):\n{_tail(dry, 60)}")


# ---------------------------------------------------------------- deploy / verify
def _remote(cfg, script, timeout):
    """Run a bash script on the remote host (base64-wrapped to avoid quoting issues)."""
    b64 = base64.b64encode(script.encode()).decode()
    return _local(["ssh", *SSH_OPTS, cfg.ssh.target,
                   f"echo {b64} | base64 -d | bash"], timeout=timeout)


def deploy_qemu(ctx, image: str = "", name: str = "", wait: bool = True, timeout: int = 360) -> str:
    """scp the built Image to the remote and (re)launch QEMU with the vPMU on, then verify boot."""
    cfg = ctx.cfg
    src = image or _image_path(cfg)
    if not os.path.isabs(src):
        src = os.path.join(cfg.linux_src, src)
    if not os.path.exists(src):
        return f"(error: image not found: {src} — build first with build_linux)"
    name = name or cfg.deploy_image_name
    qdir = cfg.ssh.remote_qemu_dir
    gport = cfg.ssh.guest_ssh_port

    rc, out = _local(["scp", *SSH_OPTS, src, f"{cfg.ssh.target}:{qdir}/{name}"], timeout=300)
    if rc != 0:
        return truncate(f"(error: scp to {cfg.ssh.target}:{qdir}/{name} failed:\n{out.strip()[:400]})")

    launch = (f"cd {qdir} && pkill -9 -x qemu-system-aar 2>/dev/null; sleep 2; rm -f qemu.pid; "
              f"KERNEL={name} BG=1 ./run-vm-customk.sh")
    if wait:
        launch += (f"; for i in $(seq 1 40); do ssh -p {gport} {_GUEST_SSH_OPTS} "
                   f"-o ConnectTimeout=5 ubuntu@localhost true 2>/dev/null && break; sleep 3; done; "
                   f"ssh -p {gport} {_GUEST_SSH_OPTS} ubuntu@localhost 'uname -r'")
    rc2, out2 = _remote(cfg, launch, timeout=timeout)
    # find the kernel release among the output lines (ssh notices may trail it)
    rel = next((ln.strip() for ln in reversed(out2.splitlines())
                if _RELEASE_RE.match(ln.strip())), "")
    if wait and not rel:
        return truncate(f"(deploy: scp OK, but boot not confirmed (rc={rc2}). "
                        f"check qemu_console.)\n{_tail(out2, 10)}")
    return (f"DEPLOYED {name} to {cfg.ssh.target}:{qdir} and booted QEMU (pmu=on)."
            + (f" guest uname -r = {rel}" if wait else " (boot not awaited)"))


def ssh_exec(ctx, command: str, where: str = "guest", timeout: int = 120) -> str:
    """Run a command on the remote host (where='host') or inside the QEMU guest (where='guest')."""
    cfg = ctx.cfg
    gport = cfg.ssh.guest_ssh_port
    b64 = base64.b64encode(command.encode()).decode()
    if where == "host":
        rc, out = _local(["ssh", *SSH_OPTS, cfg.ssh.target,
                          f"echo {b64} | base64 -d | bash"], timeout=timeout)
    else:
        inner = (f"ssh -p {gport} {_GUEST_SSH_OPTS} ubuntu@localhost "
                 f"\"echo {b64} | base64 -d | bash\"")
        rc, out = _local(["ssh", *SSH_OPTS, cfg.ssh.target, inner], timeout=timeout)
    return truncate(f"[{where}] exit={rc}\n{out.strip() or '(no output)'}")


def qemu_console(ctx, lines: int = 60) -> str:
    """Read the tail of the remote QEMU serial console log (boot output / panics)."""
    cfg = ctx.cfg
    rc, out = _remote(cfg, f"tail -n {int(lines)} {cfg.ssh.remote_qemu_dir}/console.log",
                      timeout=30)
    return truncate(f"console.log (last {lines}):\n{out.strip() or '(empty)'}")


TOOLS = [
    ("build_linux", build_linux, schema(
        "build_linux",
        "Cross-compile the kernel locally. Incremental `make` by default (keeps .config + edits); "
        "reconfigure=true re-runs build-pi4.sh from defconfig+fragment (discards manual .config edits). "
        "Returns build status + Image info.",
        {"target": {"type": "string", "description": "make target(s), e.g. 'Image' or 'Image dtbs modules'.",
                    "default": "Image"},
         "jobs": {"type": "integer", "description": "parallel jobs (default = nproc).", "default": 0},
         "reconfigure": {"type": "boolean", "description": "full reconfigure via build-pi4.sh.",
                         "default": False}},
        [])),
    ("kconfig", kconfig, schema(
        "kconfig",
        "Read or change a kernel .config option (scripts/config + olddefconfig). "
        "op: get | enable | disable | module | set (set needs value). Rebuild afterward to apply.",
        {"op": {"type": "string", "enum": ["get", "enable", "disable", "module", "set"]},
         "option": {"type": "string", "description": "e.g. ARM_PMU or CONFIG_ARM_PMU."},
         "value": {"type": "string", "description": "value for op=set.", "default": ""}},
        ["op", "option"])),
    ("trace_build", trace_build, schema(
        "trace_build",
        "Show the toolchain and the exact commands Kbuild would run to build the target "
        "(cross-gcc version + `make -n`). Use to explain how the image is built / what tools are used.",
        {"target": {"type": "string", "default": "Image"}})),
    ("deploy_qemu", deploy_qemu, schema(
        "deploy_qemu",
        "scp the freshly built Image to the remote host and (re)launch QEMU with the virtual PMU on "
        "(-cpu host,pmu=on), then verify the guest booted (uname -r). Build first.",
        {"image": {"type": "string", "description": "Image path (default arch/arm64/boot/Image).", "default": ""},
         "name": {"type": "string", "description": "remote image name (default from config).", "default": ""},
         "wait": {"type": "boolean", "description": "wait for + confirm guest boot.", "default": True}})),
    ("ssh_exec", ssh_exec, schema(
        "ssh_exec",
        "Run a shell command on the remote QEMU host (where='host') or inside the QEMU guest "
        "(where='guest', via the host). Use to measure/verify on the target (uname, perf, workloads).",
        {"command": {"type": "string"},
         "where": {"type": "string", "enum": ["guest", "host"], "default": "guest"},
         "timeout": {"type": "integer", "default": 120}},
        ["command"])),
    ("qemu_console", qemu_console, schema(
        "qemu_console", "Read the tail of the remote QEMU serial console log (boot messages, panics).",
        {"lines": {"type": "integer", "default": 60}})),
]
