#!/usr/bin/env python3
"""Build a generic reTerminal image with all software pre-installed.

Usage:
    uv run build-image.py

Downloads Raspberry Pi OS Lite Trixie (arm64), expands the image,
installs packages and drivers in a chroot, deploys kiosk files,
and produces a compressed generic image ready for flash.py to
customize with per-device settings.

No secrets or per-device configuration are included in the image.
"""

import hashlib
import lzma
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import requests

# Raspberry Pi OS Lite Trixie 64-bit
IMAGE_URL = "https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2025-12-04/2025-12-04-raspios-trixie-arm64-lite.img.xz"
IMAGE_SHA256_URL = IMAGE_URL + ".sha256"

SCRIPT_DIR = Path(__file__).resolve().parent
KIOSK_DIR = SCRIPT_DIR / "kiosk"
OUTPUT_DIR = SCRIPT_DIR / "output"
TMP_DIR = SCRIPT_DIR / "tmp"

SEEED_REPO = "https://github.com/Seeed-Studio/seeed-linux-dtoverlays"


# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

def check_build_prerequisites() -> None:
    """Verify all required tools are available for image building."""
    print("Checking build prerequisites...")

    required = {
        "qemu-aarch64-static": "Install: sudo apt-get install qemu-user-static",
        "parted": "Install: sudo apt-get install parted",
        "e2fsck": "Install: sudo apt-get install e2fsprogs",
        "resize2fs": "Install: sudo apt-get install e2fsprogs",
        "fdisk": "Install: sudo apt-get install fdisk",
    }

    missing = []
    for tool, hint in required.items():
        if not shutil.which(tool):
            missing.append(f"  {tool} — {hint}")

    # Check binfmt_misc for ARM64
    binfmt_path = Path("/proc/sys/fs/binfmt_misc/qemu-aarch64")
    if not binfmt_path.exists():
        missing.append(
            "  binfmt_misc qemu-aarch64 — "
            "Install: sudo apt-get install qemu-user-static binfmt-support"
        )

    if missing:
        print("ERROR: Missing prerequisites:", file=sys.stderr)
        for m in missing:
            print(m, file=sys.stderr)
        sys.exit(1)

    print("  All prerequisites found")


# ---------------------------------------------------------------------------
# Image download and verification
# ---------------------------------------------------------------------------

def download_image() -> Path:
    """Download the RPi OS image if not already cached."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    xz_path = OUTPUT_DIR / Path(IMAGE_URL).name

    if xz_path.exists():
        print(f"Using cached image: {xz_path}")
        return xz_path

    print(f"Downloading {IMAGE_URL}")
    print("  (this may take a while, ~450 MB)")
    resp = requests.get(IMAGE_URL, stream=True, timeout=60)
    resp.raise_for_status()

    total = int(resp.headers.get("content-length", 0))
    downloaded = 0

    with open(xz_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded * 100 // total
                mb = downloaded // (1024 * 1024)
                total_mb = total // (1024 * 1024)
                print(f"\r  {mb}/{total_mb} MB ({pct}%)", end="", flush=True)

    print()  # newline after progress
    print(f"Downloaded to {xz_path}")
    return xz_path


def verify_checksum(xz_path: Path) -> None:
    """Verify SHA256 checksum of the downloaded image."""
    print(f"Downloading checksum from {IMAGE_SHA256_URL}")
    resp = requests.get(IMAGE_SHA256_URL, timeout=30)
    resp.raise_for_status()

    # Format is: "<hash>  <filename>" or just "<hash>"
    expected_hash = resp.text.strip().split()[0].lower()
    print(f"  Expected: {expected_hash}")

    print(f"Computing SHA256 of {xz_path.name}...")
    sha256 = hashlib.sha256()
    with open(xz_path, "rb") as f:
        while True:
            data = f.read(65536)
            if not data:
                break
            sha256.update(data)

    actual_hash = sha256.hexdigest()
    print(f"  Actual:   {actual_hash}")

    if actual_hash != expected_hash:
        print("ERROR: Checksum mismatch! The download may be corrupt.", file=sys.stderr)
        print(f"  Removing {xz_path}", file=sys.stderr)
        xz_path.unlink()
        sys.exit(1)

    print("  Checksum OK")


def extract_image(xz_path: Path) -> Path:
    """Extract .img.xz to output/reterminal-base.img."""
    img_path = OUTPUT_DIR / "reterminal-base.img"

    if img_path.exists():
        print(f"Removing existing {img_path}")
        img_path.unlink()

    print(f"Extracting {xz_path.name} -> {img_path.name}")
    print("  (this may take a moment)")

    with lzma.open(xz_path) as xz_f:
        with open(img_path, "wb") as img_f:
            shutil.copyfileobj(xz_f, img_f)

    size_mb = img_path.stat().st_size // (1024 * 1024)
    print(f"  Extracted: {img_path} ({size_mb} MB)")
    return img_path


# ---------------------------------------------------------------------------
# Partition handling
# ---------------------------------------------------------------------------

def find_partition_offsets(img_path: Path) -> tuple[int, int]:
    """Find byte offsets of boot (FAT32) and rootfs (Linux) partitions.

    Returns (boot_offset, rootfs_offset) in bytes.
    """
    result = subprocess.run(
        ["fdisk", "-l", str(img_path)],
        capture_output=True, text=True, check=True,
    )

    sector_size = 512
    for line in result.stdout.splitlines():
        m = re.search(r"Sector size.*?:\s*(\d+)\s*bytes", line)
        if m:
            sector_size = int(m.group(1))
            break

    boot_offset = None
    rootfs_offset = None

    for line in result.stdout.splitlines():
        # Skip non-partition lines
        if not line.startswith(str(img_path)):
            continue
        parts = line.split()
        start_sector = int(parts[1])
        offset = start_sector * sector_size

        if "FAT32" in line or " c " in line:
            boot_offset = offset
            print(f"  Boot partition: start sector {start_sector}, offset {offset}")
        elif "Linux" in line:
            rootfs_offset = offset
            print(f"  Root partition: start sector {start_sector}, offset {offset}")

    if boot_offset is None:
        print("ERROR: Could not find FAT32 boot partition", file=sys.stderr)
        print(f"fdisk output:\n{result.stdout}", file=sys.stderr)
        sys.exit(1)
    if rootfs_offset is None:
        print("ERROR: Could not find Linux root partition", file=sys.stderr)
        print(f"fdisk output:\n{result.stdout}", file=sys.stderr)
        sys.exit(1)

    return boot_offset, rootfs_offset


def expand_image(img_path: Path, extra_mb: int = 2048) -> None:
    """Expand the image and its root partition by extra_mb megabytes."""
    print(f"Expanding image by {extra_mb} MB...")

    # Grow the file
    current_size = img_path.stat().st_size
    new_size = current_size + extra_mb * 1024 * 1024
    subprocess.run(
        ["truncate", "-s", str(new_size), str(img_path)],
        check=True,
    )

    # Grow the second partition to fill available space
    subprocess.run(
        ["parted", "-s", str(img_path), "resizepart", "2", "100%"],
        check=True,
    )

    # Set up loop device for the rootfs partition to resize the filesystem
    _, rootfs_offset = find_partition_offsets(img_path)
    # Calculate rootfs size from partition table
    result = subprocess.run(
        ["fdisk", "-l", str(img_path)],
        capture_output=True, text=True, check=True,
    )
    sector_size = 512
    rootfs_sectors = 0
    for line in result.stdout.splitlines():
        m = re.search(r"Sector size.*?:\s*(\d+)\s*bytes", line)
        if m:
            sector_size = int(m.group(1))
        if line.startswith(str(img_path)) and "Linux" in line:
            parts = line.split()
            rootfs_sectors = int(parts[3]) - int(parts[1]) + 1

    rootfs_size = rootfs_sectors * sector_size

    # Set up loop device
    result = subprocess.run(
        [
            "sudo", "losetup", "--find", "--show",
            "--offset", str(rootfs_offset),
            "--sizelimit", str(rootfs_size),
            str(img_path),
        ],
        capture_output=True, text=True, check=True,
    )
    loop_dev = result.stdout.strip()
    print(f"  Loop device: {loop_dev}")

    try:
        # Check and resize filesystem
        subprocess.run(["sudo", "e2fsck", "-f", "-y", loop_dev], check=False)
        subprocess.run(["sudo", "resize2fs", loop_dev], check=True)
        print(f"  Filesystem expanded on {loop_dev}")
    finally:
        subprocess.run(["sudo", "losetup", "-d", loop_dev], check=False)

    new_size_mb = img_path.stat().st_size // (1024 * 1024)
    print(f"  Image size: {new_size_mb} MB")


# ---------------------------------------------------------------------------
# Serial console configuration (GPIO UART only, no USB gadget)
# ---------------------------------------------------------------------------

def configure_serial_consoles(boot_mount: Path) -> None:
    """Enable GPIO UART serial console on the boot partition.

    Configures config.txt and cmdline.txt for GPIO UART (serial0) on
    pins 8/10. USB gadget serial is NOT configured — it doesn't work
    on the reTerminal hardware (see docs/kiosk-setup.md).
    """
    print("  Configuring GPIO serial console...")

    # --- config.txt ---
    config_txt = boot_mount / "config.txt"
    config_content = config_txt.read_text()
    config_modified = False

    # Enable GPIO UART and free PL011 from Bluetooth — must be in [all] section
    all_section_additions = []
    if "enable_uart=1" not in config_content:
        all_section_additions.append("enable_uart=1")
        print("    config.txt: added enable_uart=1")
    if "dtoverlay=disable-bt" not in config_content:
        all_section_additions.append("dtoverlay=disable-bt")
        print("    config.txt: added dtoverlay=disable-bt")
    if all_section_additions:
        additions = "\n".join(all_section_additions) + "\n"
        if "[all]" in config_content:
            config_content = config_content.replace("[all]", "[all]\n" + additions)
        else:
            config_content = config_content.rstrip("\n") + "\n[all]\n" + additions
        config_modified = True

    # Comment out otg_mode=1 (forces host mode, not needed)
    if "otg_mode=1" in config_content and "#" not in config_content.split("otg_mode=1")[0].split("\n")[-1]:
        config_content = config_content.replace(
            "otg_mode=1",
            "# otg_mode=1  # disabled — USB gadget doesn't work on reTerminal",
        )
        config_modified = True
        print("    config.txt: commented out otg_mode=1")

    if config_modified:
        config_txt.write_text(config_content)

    # --- cmdline.txt ---
    cmdline_txt = boot_mount / "cmdline.txt"
    cmdline = cmdline_txt.read_text().strip()
    modified = False

    if "console=serial0" not in cmdline:
        cmdline += " console=serial0,115200"
        modified = True
        print("    cmdline.txt: added console=serial0,115200")

    if modified:
        cmdline_txt.write_text(cmdline + "\n")


# ---------------------------------------------------------------------------
# Kiosk file deployment (no chroot needed)
# ---------------------------------------------------------------------------

def deploy_kiosk_files(rootfs: Path) -> None:
    """Copy kiosk files from kiosk/ to target paths in rootfs."""
    print("  Deploying kiosk files...")

    file_map = {
        # (source, target_dir, target_name, mode)
        "cage-kiosk@.service": ("/etc/systemd/system/", None, 0o644),
        "kiosk-browser.template": ("/usr/local/bin/", "kiosk-browser", 0o755),
        "backlight-manager": ("/usr/local/bin/", None, 0o755),
        "backlight-manager.service": ("/etc/systemd/system/", None, 0o644),
        "power-button-handler": ("/usr/local/bin/", None, 0o755),
        "power-button-handler.service": ("/etc/systemd/system/", None, 0o644),
    }

    for source_name, (target_dir, target_name, mode) in file_map.items():
        src = KIOSK_DIR / source_name
        dest_dir = rootfs / target_dir.lstrip("/")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / (target_name or source_name)
        shutil.copy2(src, dest)
        dest.chmod(mode)
        print(f"    {source_name} -> {target_dir}{target_name or source_name}")

    # Deploy hide-cursor extension directory
    ext_src = KIOSK_DIR / "hide-cursor-extension"
    ext_dest = rootfs / "usr/local/share/kiosk/hide-cursor-extension"
    if ext_dest.exists():
        shutil.rmtree(ext_dest)
    ext_dest.mkdir(parents=True, exist_ok=True)
    for f in ext_src.iterdir():
        dest = ext_dest / f.name
        shutil.copy2(f, dest)
        if f.suffix in (".json", ".css"):
            dest.chmod(0o644)
    print(f"    hide-cursor-extension/ -> /usr/local/share/kiosk/hide-cursor-extension/")


def create_transparent_cursor(rootfs: Path) -> None:
    """Create a transparent cursor theme in the rootfs.

    Writes Xcursor files to /usr/share/icons/transparent/ so the
    cage service can set XCURSOR_THEME=transparent.
    """
    print("  Creating transparent cursor theme...")

    # Xcursor format constants
    XCURSOR_MAGIC = 0x72756358  # "Xcur"
    XCURSOR_IMAGE_TYPE = 0xFFFD0002

    # 1x1 transparent pixel (ARGB = 0x00000000)
    pixel = b'\x00\x00\x00\x00'
    chunk_header_size = 36  # 9 * 4 bytes
    chunk = struct.pack(
        '<IIIIIIIII',
        chunk_header_size, XCURSOR_IMAGE_TYPE, 1, 1,  # header, type, subtype, version
        1, 1, 0, 0, 1,  # width, height, xhot, yhot, delay
    ) + pixel
    toc_entry = struct.pack('<III', XCURSOR_IMAGE_TYPE, 1, 28)  # type, subtype, position
    header = struct.pack('<IIII', XCURSOR_MAGIC, 16, 0x00010000, 1)
    cursor_data = header + toc_entry + chunk

    cursor_names = [
        'default', 'left_ptr', 'arrow', 'top_left_arrow',
        'pointer', 'hand', 'hand1', 'hand2', 'grab', 'grabbing',
        'text', 'xterm', 'ibeam',
        'crosshair', 'cross', 'tcross',
        'wait', 'watch', 'progress', 'left_ptr_watch',
        'move', 'fleur', 'all-scroll',
        'not-allowed', 'forbidden', 'no-drop', 'circle',
        'n-resize', 's-resize', 'e-resize', 'w-resize',
        'ne-resize', 'nw-resize', 'se-resize', 'sw-resize',
        'ns-resize', 'ew-resize', 'nesw-resize', 'nwse-resize',
        'col-resize', 'row-resize',
        'top_side', 'bottom_side', 'left_side', 'right_side',
        'top_left_corner', 'top_right_corner',
        'bottom_left_corner', 'bottom_right_corner',
        'context-menu', 'help', 'question_arrow',
        'copy', 'alias', 'dnd-move', 'dnd-copy', 'dnd-link', 'dnd-none',
        'link', 'pirate', 'sb_h_double_arrow', 'sb_v_double_arrow',
        'size_bdiag', 'size_fdiag', 'size_hor', 'size_ver', 'size_all',
        'split_h', 'split_v', 'zoom-in', 'zoom-out',
        'X_cursor', 'based_arrow_down', 'based_arrow_up',
    ]

    theme_dir = rootfs / "usr/share/icons/transparent"
    cursor_dir = theme_dir / "cursors"
    cursor_dir.mkdir(parents=True, exist_ok=True)

    # Write index.theme
    index_path = theme_dir / "index.theme"
    index_path.write_text(
        "[Icon Theme]\nName=transparent\nComment=Transparent cursor for kiosk\n"
    )

    # Write cursor files
    for name in cursor_names:
        (cursor_dir / name).write_bytes(cursor_data)

    print(f"    Created {len(cursor_names)} transparent cursors in /usr/share/icons/transparent/")


def create_pam_config(rootfs: Path) -> None:
    """Write /etc/pam.d/cage for cage logind session support."""
    print("  Creating PAM config for cage...")
    pam_dir = rootfs / "etc/pam.d"
    pam_dir.mkdir(parents=True, exist_ok=True)
    pam_file = pam_dir / "cage"
    pam_file.write_text(
        "# PAM configuration for cage Wayland compositor\n"
        "auth       sufficient   pam_permit.so\n"
        "account    sufficient   pam_permit.so\n"
        "session    required     pam_unix.so\n"
        "session    optional     pam_systemd.so\n"
    )
    print("    /etc/pam.d/cage written")


def enable_services(rootfs: Path) -> None:
    """Enable/mask systemd services using systemctl --root."""
    print("  Enabling systemd services...")

    enable = [
        "cage-kiosk@tty7",
        "backlight-manager",
        "power-button-handler",
        "seatd",
    ]
    mask = ["serial-getty@ttyGS0"]

    for svc in enable:
        subprocess.run(
            ["systemctl", "--root", str(rootfs), "enable", svc],
            check=True,
        )
        print(f"    Enabled {svc}")

    for svc in mask:
        subprocess.run(
            ["systemctl", "--root", str(rootfs), "mask", svc],
            check=True,
        )
        print(f"    Masked {svc}")


# ---------------------------------------------------------------------------
# Chroot functions
# ---------------------------------------------------------------------------

def find_target_kernel_version(rootfs: Path) -> str:
    """Detect the target kernel version from /lib/modules/ in the rootfs."""
    modules_dir = rootfs / "lib/modules"
    versions = [d.name for d in modules_dir.iterdir() if d.is_dir()]
    if not versions:
        print("ERROR: No kernel versions found in rootfs /lib/modules/", file=sys.stderr)
        sys.exit(1)
    # Take the latest version if multiple
    versions.sort()
    kver = versions[-1]
    print(f"  Target kernel version: {kver}")
    return kver


def setup_chroot(rootfs: Path, boot_mount: Path) -> str:
    """Prepare the rootfs for chroot execution.

    Sets up bind mounts, copies qemu-aarch64-static, resolv.conf,
    policy-rc.d, and uname wrapper. Returns the target kernel version.
    """
    print("  Setting up chroot environment...")

    # Bind-mount boot partition at rootfs/boot/firmware
    boot_target = rootfs / "boot/firmware"
    boot_target.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["sudo", "mount", "--bind", str(boot_mount), str(boot_target)],
        check=True,
    )

    # Bind-mount system directories
    for d in ["proc", "sys", "dev", "dev/pts"]:
        target = rootfs / d
        target.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["sudo", "mount", "--bind", f"/{d}", str(target)],
            check=True,
        )

    # Copy qemu-aarch64-static
    qemu_src = shutil.which("qemu-aarch64-static")
    if qemu_src:
        qemu_dest = rootfs / "usr/bin/qemu-aarch64-static"
        shutil.copy2(qemu_src, qemu_dest)
        print(f"    Copied {qemu_src} -> chroot")

    # Copy resolv.conf (handle systemd-resolved symlink)
    resolv_dest = rootfs / "etc/resolv.conf"
    resolv_src = Path("/etc/resolv.conf")
    # Remove existing (might be a symlink to systemd-resolved)
    if resolv_dest.is_symlink() or resolv_dest.exists():
        resolv_dest.unlink()
    # Read the resolved content from host
    resolv_content = resolv_src.read_text()
    resolv_dest.write_text(resolv_content)
    print("    Copied resolv.conf")

    # Write policy-rc.d to prevent services from starting during install
    policy_path = rootfs / "usr/sbin/policy-rc.d"
    policy_path.write_text("#!/bin/sh\nexit 101\n")
    policy_path.chmod(0o755)
    print("    Installed policy-rc.d (block service starts)")

    # Detect target kernel version and create uname wrapper
    kver = find_target_kernel_version(rootfs)

    # Create uname wrapper that returns the target kernel version
    # Save real uname and replace with wrapper
    real_uname = rootfs / "usr/bin/uname.real"
    fake_uname = rootfs / "usr/bin/uname"
    if not real_uname.exists():
        shutil.copy2(fake_uname, real_uname)
    fake_uname.write_text(
        f'#!/bin/sh\n'
        f'# Wrapper to return target kernel version in chroot\n'
        f'case "$1" in\n'
        f'  -r) echo "{kver}" ;;\n'
        f'  -a) echo "Linux reterminal {kver} #1 SMP aarch64 GNU/Linux" ;;\n'
        f'  *)  exec /usr/bin/uname.real "$@" ;;\n'
        f'esac\n'
    )
    fake_uname.chmod(0o755)
    print(f"    Created uname wrapper (returns {kver})")

    return kver


def teardown_chroot(rootfs: Path) -> None:
    """Clean up chroot environment: restore uname, remove artifacts, unmount."""
    print("  Tearing down chroot environment...")

    # Restore real uname
    real_uname = rootfs / "usr/bin/uname.real"
    fake_uname = rootfs / "usr/bin/uname"
    if real_uname.exists():
        shutil.copy2(real_uname, fake_uname)
        real_uname.unlink()
        print("    Restored real uname")

    # Remove policy-rc.d
    policy_path = rootfs / "usr/sbin/policy-rc.d"
    if policy_path.exists():
        policy_path.unlink()
        print("    Removed policy-rc.d")

    # Remove qemu binary
    qemu_path = rootfs / "usr/bin/qemu-aarch64-static"
    if qemu_path.exists():
        qemu_path.unlink()
        print("    Removed qemu-aarch64-static")

    # Unmount in reverse order
    for d in ["boot/firmware", "dev/pts", "dev", "sys", "proc"]:
        target = rootfs / d
        subprocess.run(["sudo", "umount", str(target)], check=False)

    print("    Unmounted chroot bind mounts")


def run_in_chroot(rootfs: Path, cmd: str, env: dict | None = None) -> None:
    """Run a command inside the chroot."""
    chroot_env = {"DEBIAN_FRONTEND": "noninteractive", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}
    if env:
        chroot_env.update(env)
    env_args = []
    for k, v in chroot_env.items():
        env_args.extend([f"{k}={v}"])

    subprocess.run(
        ["sudo", "chroot", str(rootfs), "env"] + env_args + ["sh", "-c", cmd],
        check=True,
    )


def install_packages(rootfs: Path) -> None:
    """Install required packages inside the chroot."""
    print("  Installing packages in chroot...")

    run_in_chroot(rootfs, "apt-get update")

    packages = [
        "cage", "chromium", "wlr-randr", "seatd", "fonts-noto-color-emoji",
        "git", "build-essential", "dkms", "raspberrypi-kernel-headers",
    ]
    run_in_chroot(
        rootfs,
        f"apt-get install -y --no-install-recommends {' '.join(packages)}",
    )

    # Clean up apt cache to reduce image size
    run_in_chroot(rootfs, "apt-get clean")
    # Remove apt lists
    apt_lists = rootfs / "var/lib/apt/lists"
    if apt_lists.exists():
        for f in apt_lists.iterdir():
            if f.is_file() and f.name != "lock":
                f.unlink()

    print(f"    Installed: {', '.join(packages)}")


# ---------------------------------------------------------------------------
# Seeed driver build
# ---------------------------------------------------------------------------

def build_seeed_drivers(rootfs: Path, kver: str) -> None:
    """Clone, patch, and build Seeed reTerminal display drivers in chroot."""
    print("  Building Seeed display drivers...")

    clone_dest = rootfs / "opt/seeed-linux-dtoverlays"

    # Clone on host (faster, no git needed in chroot)
    if clone_dest.exists():
        shutil.rmtree(clone_dest)
    subprocess.run(
        ["git", "clone", "--depth=1", SEEED_REPO, str(clone_dest)],
        check=True,
    )
    print("    Cloned seeed-linux-dtoverlays")

    # Apply touch rotation patch on host
    touch_panel = clone_dest / "modules/mipi_dsi/touch_panel.c"
    if touch_panel.exists():
        content = touch_panel.read_text()
        old = "\tif(md->tp_point_rotate)\n\t\tx_y_rotate(&input_x, &input_y);"
        new = (
            "\t/* Disabled: cage's wlr-randr 270 transform handles rotation.\n"
            "\t * Enabling this causes double-rotation of touch coordinates.\n"
            "\t * if(md->tp_point_rotate)\n"
            "\t *\tx_y_rotate(&input_x, &input_y);\n"
            "\t */"
        )
        if old in content:
            content = content.replace(old, new)
            touch_panel.write_text(content)
            print("    Applied touch rotation patch")
        elif "/* Disabled: cage" in content:
            print("    Touch rotation patch already applied")
        else:
            print("    WARNING: Could not find x_y_rotate pattern to patch", file=sys.stderr)

    # Run reTerminal.sh --keep-kernel in chroot
    print("    Running reTerminal.sh --keep-kernel (this may take a while)...")
    try:
        run_in_chroot(
            rootfs,
            "cd /opt/seeed-linux-dtoverlays && yes | ./scripts/reTerminal.sh --keep-kernel",
        )
        print("    reTerminal.sh completed successfully")
    except subprocess.CalledProcessError:
        print("    WARNING: reTerminal.sh failed, attempting manual module build...", file=sys.stderr)
        # Fallback: manual module build
        try:
            run_in_chroot(
                rootfs,
                f"cd /opt/seeed-linux-dtoverlays/modules/mipi_dsi && "
                f"make -C /lib/modules/{kver}/build M=$(pwd) modules && "
                f"mkdir -p /lib/modules/{kver}/updates/dkms && "
                f"cp mipi_dsi.ko /lib/modules/{kver}/updates/dkms/ && "
                f"xz /lib/modules/{kver}/updates/dkms/mipi_dsi.ko && "
                f"depmod -a {kver}",
            )
            print("    Manual module build succeeded")
        except subprocess.CalledProcessError as e:
            print(f"    ERROR: Manual module build also failed: {e}", file=sys.stderr)
            sys.exit(1)

    # Verify the module was built
    module_path = rootfs / f"lib/modules/{kver}/updates/dkms/mipi_dsi.ko.xz"
    module_path_uncompressed = rootfs / f"lib/modules/{kver}/updates/dkms/mipi_dsi.ko"
    if module_path.exists():
        print(f"    Verified: mipi_dsi.ko.xz exists in /lib/modules/{kver}/updates/dkms/")
    elif module_path_uncompressed.exists():
        print(f"    Verified: mipi_dsi.ko exists in /lib/modules/{kver}/updates/dkms/")
    else:
        # Check if it ended up somewhere else
        result = subprocess.run(
            ["find", str(rootfs / "lib/modules"), "-name", "mipi_dsi.ko*"],
            capture_output=True, text=True,
        )
        if result.stdout.strip():
            print(f"    Found mipi_dsi module at: {result.stdout.strip()}")
        else:
            print("    WARNING: mipi_dsi module not found after build!", file=sys.stderr)


# ---------------------------------------------------------------------------
# Image configuration orchestrator
# ---------------------------------------------------------------------------

def configure_image(img_path: Path) -> None:
    """Mount the image, deploy software, build drivers, and configure.

    This is the main orchestrator that:
    1. Mounts boot and rootfs partitions
    2. Configures serial consoles (GPIO UART only)
    3. Writes SSH marker file
    4. Deploys kiosk files (no chroot needed)
    5. Creates transparent cursor theme
    6. Creates PAM config for cage
    7. Enables systemd services
    8. Sets up chroot environment
    9. Installs packages in chroot
    10. Builds Seeed display drivers in chroot
    11. Tears down chroot
    12. Unmounts everything
    """
    print("Configuring image...")
    print("=" * 50)

    boot_offset, rootfs_offset = find_partition_offsets(img_path)

    rootfs_mount = TMP_DIR / "rootfs-mount"
    boot_mount = TMP_DIR / "boot-mount"
    rootfs_mount.mkdir(parents=True, exist_ok=True)
    boot_mount.mkdir(parents=True, exist_ok=True)

    try:
        # Mount rootfs
        print(f"Mounting rootfs at {rootfs_mount}")
        subprocess.run(
            [
                "sudo", "mount",
                "-o", f"loop,offset={rootfs_offset}",
                str(img_path), str(rootfs_mount),
            ],
            check=True,
        )

        # Mount boot
        print(f"Mounting boot at {boot_mount}")
        subprocess.run(
            [
                "sudo", "mount",
                "-o", f"loop,offset={boot_offset}",
                str(img_path), str(boot_mount),
            ],
            check=True,
        )

        # Step 1: Configure serial consoles (GPIO UART only)
        configure_serial_consoles(boot_mount)

        # Step 2: Write SSH marker file
        ssh_file = boot_mount / "ssh"
        ssh_file.touch()
        print("  Created /boot/firmware/ssh marker")

        # Step 3: Deploy kiosk files (no chroot needed)
        deploy_kiosk_files(rootfs_mount)

        # Step 4: Create transparent cursor theme
        create_transparent_cursor(rootfs_mount)

        # Step 5: Create PAM config
        create_pam_config(rootfs_mount)

        # Step 6: Enable services
        enable_services(rootfs_mount)

        # Step 7: Set up chroot
        kver = setup_chroot(rootfs_mount, boot_mount)

        try:
            # Step 8: Install packages
            install_packages(rootfs_mount)

            # Step 9: Build Seeed drivers
            build_seeed_drivers(rootfs_mount, kver)
        finally:
            # Step 10: Teardown chroot (always, even on failure)
            teardown_chroot(rootfs_mount)

    finally:
        # Unmount everything
        print("Unmounting partitions...")
        subprocess.run(["sudo", "umount", str(boot_mount)], check=False)
        subprocess.run(["sudo", "umount", str(rootfs_mount)], check=False)

        # Clean up mount directories
        for d in [boot_mount, rootfs_mount]:
            if d.exists():
                try:
                    d.rmdir()
                except OSError:
                    pass
        if TMP_DIR.exists():
            try:
                TMP_DIR.rmdir()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Image compression
# ---------------------------------------------------------------------------

def compress_image(img_path: Path) -> Path:
    """Compress the image with xz and compute SHA256."""
    print("Compressing image...")
    xz_path = img_path.with_suffix(".img.xz")

    if xz_path.exists():
        xz_path.unlink()

    subprocess.run(
        ["xz", "-T0", "-v", str(img_path)],
        check=True,
    )

    # xz removes the original file and creates .xz
    size_mb = xz_path.stat().st_size // (1024 * 1024)
    print(f"  Compressed: {xz_path} ({size_mb} MB)")

    # Compute SHA256
    sha256 = hashlib.sha256()
    with open(xz_path, "rb") as f:
        while True:
            data = f.read(65536)
            if not data:
                break
            sha256.update(data)

    sha_path = xz_path.with_suffix(".xz.sha256")
    sha_path.write_text(f"{sha256.hexdigest()}  {xz_path.name}\n")
    print(f"  Checksum: {sha_path}")

    return xz_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Building generic reTerminal image")
    print("=" * 50)

    # Step 1: Check prerequisites
    check_build_prerequisites()

    # Step 2: Download and verify base image
    xz_path = download_image()
    verify_checksum(xz_path)

    # Step 3: Extract image
    img_path = extract_image(xz_path)

    # Step 4: Expand image for packages
    expand_image(img_path)

    # Step 5: Configure image (mount, install, build drivers)
    configure_image(img_path)

    # Step 6: Compress final image
    xz_output = compress_image(img_path)

    print()
    print("=" * 50)
    print(f"Generic image ready: {xz_output}")
    print(f"Customize and flash with: uv run flash.py <hostname>")


if __name__ == "__main__":
    main()
