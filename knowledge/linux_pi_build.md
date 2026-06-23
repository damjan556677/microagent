## Linux 6.8 (Raspberry Pi 4 / arm64) — build & deploy reference

This tree is vanilla **linux-6.8** cross-built for a **bare-metal Raspberry Pi 4** (Cortex-A72,
arm64) with the full eBPF/BTF stack and the **hardware PMU** enabled. Build is LOCAL; the
resulting kernel is booted under **QEMU/KVM on a remote host** (`-cpu host,pmu=on`).

### Toolchain
- `ARCH=arm64`, `CROSS_COMPILE=aarch64-linux-gnu-` (gcc 11.x).
- LOCALVERSION `-ai4pi` (so `uname -r` ≈ `6.8.0-ai4pi`).

### Full build (regenerates .config) — `build-pi4.sh`
1. `make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- defconfig`
2. `./scripts/kconfig/merge_config.sh -m .config pi4-ebpf.cfg`  (Pi-4 + eBPF/PMU fragment)
3. `make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- olddefconfig`
4. `make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j$(nproc) Image dtbs modules`
5. stage modules to `modstage/`; build `tools/perf`.

> The full script **rewrites `.config` from defconfig+fragment every run** — it will discard
> manual `.config` edits. For iterating after a `kconfig`/source change, do an **incremental
> `make Image`** instead (build_linux with reconfigure=false), which preserves `.config`.

### Key config (from `pi4-ebpf.cfg`)
- Boot-critical built-in (no initramfs): `MMC_SDHCI_IPROC`, `BCMGENET`, `BROADCOM_PHY`, `EXT4_FS`,
  `SERIAL_AMBA_PL011_CONSOLE`, `ARCH_BCM2835`, `RASPBERRYPI_FIRMWARE`.
- Hardware PMU: `PERF_EVENTS`, `HW_PERF_EVENTS`, `ARM_PMU`.
- eBPF/tracing: `BPF`, `BPF_SYSCALL`, `BPF_JIT`, `DEBUG_INFO_BTF`, `KPROBES`, `UPROBES`, `FTRACE`.

### Artifacts
- Kernel image: `arch/arm64/boot/Image`
- Device tree: `arch/arm64/boot/dts/broadcom/bcm2711-rpi-4-b.dtb`
- `vmlinux` (with a `.BTF` section), `include/config/kernel.release`, `modstage/lib/modules/...`

### Deploy + boot under QEMU (remote host, e.g. rpi4pmu)
The remote holds `run-vm-customk.sh` + images in `~/qemu-pmu`. Deploy = scp the Image there, then:
```
cd ~/qemu-pmu && KERNEL=<image-name> BG=1 ./run-vm-customk.sh
```
`run-vm-customk.sh` direct-boots the kernel (no bootloader) with:
`-machine virt,gic-version=host -cpu host,pmu=on -enable-kvm -smp 4 -m 2048
 -kernel <image> -append "root=/dev/vda1 ro rootwait earlycon=pl011,0x9000000 console=ttyAMA0 ..."`
against the `noble.img` rootfs; serial → `console.log`; ssh forwarded host `:2222` → guest `:22`.
- Verify: `ssh -p 2222 ubuntu@localhost 'uname -r'` (reached via the host → double hop).
- The virtual PMU (`pmu=on`) exposes PMUv3 in the guest for `perf`/bpftrace `hardware:*` events.

### microagent tool mapping
- `build_linux` → incremental `make` (or reconfigure=true → build-pi4.sh)
- `kconfig` → scripts/config + olddefconfig
- `trace_build` → toolchain + `make -n` (what's actually run)
- `build_index` → cscope/tags/compile_commands.json (for cscope/ctags/clangd)
- `deploy_qemu` → scp Image + launch run-vm-customk.sh + confirm boot
- `ssh_exec` (host/guest), `qemu_console` → run/measure on target, read boot log
