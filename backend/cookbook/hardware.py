import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GPUInfo:
    name: str
    vram_gb: float
    driver: str = ""
    backend: str = "cuda"
    tier: str = ""                # "" (auto) | "discrete" | "integrated"
    device_index: int = 0         # for HIP_VISIBLE_DEVICES / CUDA_VISIBLE_DEVICES


@dataclass
class ComputeTier:
    """A single memory/compute tier in the hand-off hierarchy.

    Ordered fastest-first: discrete GPU(s) → integrated GPU → system RAM.
    The recommendation engine fills tiers greedily to build a layer-split plan.
    """
    name: str
    memory_gb: float
    backend: str                  # rocm, cuda, vulkan, metal, cpu
    kind: str                     # "discrete" | "integrated" | "ram"
    device_index: int = -1        # -1 for RAM


@dataclass
class SystemInfo:
    os: str = ""
    cpu: str = ""
    cpu_cores: int = 0
    ram_gb: float = 0.0
    gpus: list[GPUInfo] = field(default_factory=list)
    total_vram_gb: float = 0.0           # best single discrete GPU (backward compat)
    is_apple_silicon: bool = False
    has_nvidia: bool = False
    has_amd: bool = False
    in_container: bool = False
    compute_tiers: list[ComputeTier] = field(default_factory=list)
    combined_vram_gb: float = 0.0        # sum of all GPU VRAM (for multi-GPU planning)


def _in_container() -> bool:
    for p in ["/.dockerenv", "/run/.containerenv"]:
        if os.path.exists(p):
            return True
    try:
        with open("/proc/1/cgroup") as f:
            if "docker" in f.read():
                return True
    except OSError:
        pass
    return False


def _run_cmd(cmd: list[str], timeout: int = 10) -> Optional[str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _detect_nvidia() -> list[GPUInfo]:
    out = _run_cmd([
        "nvidia-smi", "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader,nounits"
    ])
    if not out:
        return []

    gpus = []
    for line in out.strip().split("\n"):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            try:
                vram_gb = float(parts[1]) / 1024
                driver = parts[2] if len(parts) > 2 else ""
                gpus.append(GPUInfo(
                    name=parts[0],
                    vram_gb=round(vram_gb, 1),
                    driver=driver,
                    backend="cuda"
                ))
            except ValueError:
                pass
    return gpus


def _detect_amd_linux() -> list[GPUInfo]:
    gpus = []
    drm = "/sys/class/drm"
    if not os.path.exists(drm):
        return gpus

    for entry in sorted(os.listdir(drm)):
        if not entry.startswith("card") or "-" in entry:
            continue
        dev_path = os.path.join(drm, entry, "device")
        vendor_path = os.path.join(dev_path, "vendor")
        if not os.path.exists(vendor_path):
            continue
        try:
            with open(vendor_path) as f:
                vendor = f.read().strip()
        except OSError:
            continue
        if vendor != "0x1002":
            continue

        vram = 0
        for vram_source in [
            os.path.join(dev_path, "mem_info_vram_total"),
            os.path.join(dev_path, "vis_vram_total"),
        ]:
            try:
                with open(vram_source) as f:
                    vram = int(f.read().strip()) / (1024 ** 3)
                    break
            except (OSError, ValueError):
                continue
        if vram == 0:
            continue

        name_path = os.path.join(dev_path, "product_name")
        name = "AMD GPU"
        if os.path.exists(name_path):
            try:
                with open(name_path) as f:
                    name = f.read().strip()
            except OSError:
                pass

        backend = "rocm"
        if not _run_cmd(["which", "rocminfo"]):
            backend = "vulkan"

        gpus.append(GPUInfo(name=name, vram_gb=round(vram, 1), backend=backend))
    return gpus


def _detect_apple_silicon() -> tuple[bool, list[GPUInfo], float]:
    try:
        out = _run_cmd(["sysctl", "-n", "machdep.cpu.brand_string"])
        if not out or "Apple" not in out:
            return False, [], 0.0
    except Exception:
        return False, [], 0.0

    total_ram = 0.0
    try:
        out = _run_cmd(["sysctl", "-n", "hw.memsize"])
        if out:
            total_ram = int(out) / (1024 ** 3)
    except (ValueError, OSError):
        pass

    chip = _run_cmd(["sysctl", "-n", "machdep.cpu.brand_string"]) or "Apple Silicon"

    if total_ram <= 16:
        gpu_budget = total_ram * 0.67
    elif total_ram <= 64:
        gpu_budget = total_ram * 0.75
    else:
        gpu_budget = total_ram * 0.80

    gpus = [GPUInfo(name=chip, vram_gb=round(gpu_budget, 1), backend="metal")]
    return True, gpus, total_ram


def _detect_windows() -> tuple[list[GPUInfo], float, str, int]:
    ps_cmd = r"""
function Get-VramFromVulkan($gpuName) {
    $vkOut = vulkaninfo 2>&1 | Out-String
    if (-not $vkOut) { return $null }
    $gpuBlocks = $vkOut -split '(?=GPU\d+:)'
    foreach ($block in $gpuBlocks) {
        if ($block -match "deviceName\s*=\s*([^\n]+)") {
            $vkName = $matches[1].Trim()
            if ($vkName -ne $gpuName) { continue }
        } else { continue }
        $memIdx = $block.IndexOf('memoryHeaps:')
        if ($memIdx -lt 0) { continue }
        $memSection = $block.Substring($memIdx)
        $heapBlocks = $memSection -split '(?=memoryHeaps\[\d+\]:)'
        $deviceLocalSize = $null
        $firstSize = $null
        foreach ($hb in $heapBlocks) {
            $sizeMatch = [regex]::Match($hb, 'size\s*=\s*(\d+)')
            if (-not $sizeMatch.Success) { continue }
            $hbSize = [long]$sizeMatch.Groups[1].Value
            if ($null -eq $firstSize -and $hbSize -gt 0) { $firstSize = $hbSize }
            if ($hb -match 'DEVICE_LOCAL' -and $hbSize -gt 0) { $deviceLocalSize = $hbSize }
        }
        if ($deviceLocalSize) { return $deviceLocalSize }
        if ($firstSize) { return $firstSize }
    }
    return $null
}

$vram_gpus = @()
$adapters = Get-CimInstance Win32_VideoController
$ram = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor
$idx = 0

foreach ($gpu in $adapters) {
    $name = $gpu.Name
    $driver = $gpu.DriverVersion
    $vram_gb = 0.0

    # Priority 1: nvidia-smi for NVIDIA (100% reliable)
    if ($name -match "(?i)nvidia") {
        $nvOut = nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null
        if ($nvOut) {
            $vram_gb = [math]::Round([double]($nvOut.Trim()) / 1024, 1)
        }
    }

    # Priority 2: vulkaninfo for AMD (correctly reports VRAM when drivers are wrong)
    if ($vram_gb -eq 0 -and $name -match "(?i)radeon|amd|rx|intel") {
        $vkBytes = Get-VramFromVulkan -gpuName $name
        if ($vkBytes -and $vkBytes -gt 0) {
            $vram_gb = [math]::Round($vkBytes / 1GB, 1)
        }
    }

    # Priority 3: Registry HardwareInformation.MemorySize (64-bit, in bytes)
    if ($vram_gb -eq 0) {
        $base = "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        if (Test-Path $base) {
            $subKey = ("000" + $idx)
            $key = "$base\$subKey"
            $memSize = (Get-ItemProperty -Path $key -Name "HardwareInformation.MemorySize" -ErrorAction SilentlyContinue)."HardwareInformation.MemorySize"
            if (-not $memSize) {
                $subs = Get-ChildItem $base
                foreach ($sub in $subs) {
                    $desc = (Get-ItemProperty -Path $sub.PSPath -Name "DriverDesc" -ErrorAction SilentlyContinue).DriverDesc
                    if ($desc -eq $name) {
                        $memSize = (Get-ItemProperty -Path $sub.PSPath -Name "HardwareInformation.MemorySize" -ErrorAction SilentlyContinue)."HardwareInformation.MemorySize"
                        break
                    }
                }
            }
            if ($memSize -and $memSize -gt 0) {
                $vram_gb = [math]::Round($memSize / 1GB, 1)
            }
        }
    }

    # Priority 4: WMI AdapterRAM (32-bit, trucates above 4GB)
    if ($vram_gb -eq 0) {
        $wmiBytes = $gpu.AdapterRAM
        if ($wmiBytes -and $wmiBytes -gt 0) {
            $vram_gb = [math]::Round($wmiBytes / 1GB, 1)
        }
    }

    # Minimum fallback
    if ($vram_gb -eq 0) { $vram_gb = 1.0 }

    $backend = "vulkan"
    if ($name -match "(?i)nvidia") { $backend = "cuda" }
    if ($name -match "(?i)radeon|amd|rx") { $backend = "rocm" }

    $vram_gpus += @{
        Name = $name
        AdapterRAM = $vram_gb
        DriverVersion = $driver
        Backend = $backend
    }
    $idx++
}

$result = @{
    gpu = $vram_gpus | ConvertTo-Json -Compress
    ram = $ram.TotalVisibleMemorySize
    cpu_name = $cpu.Name
    cpu_cores = $cpu.NumberOfCores
}
return $result | ConvertTo-Json -Compress
"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode != 0:
            return [], 0, "", 0
        import json
        data = json.loads(r.stdout.strip())

        ram_gb = round(float(data.get("ram", 0)) / 1048576, 1)
        cpu_name = data.get("cpu_name", "").strip()
        cpu_cores = int(data.get("cpu_cores", 0))

        gpu_raw = data.get("gpu", "[]")
        if isinstance(gpu_raw, str):
            gpu_list = json.loads(gpu_raw) if gpu_raw.startswith("[") else [json.loads(gpu_raw)]
        else:
            gpu_list = gpu_raw if isinstance(gpu_raw, list) else [gpu_raw]

        gpus = []
        for g in gpu_list:
            name = g.get("Name", "Unknown GPU")
            vram_gb = float(g.get("AdapterRAM", 0))
            driver = g.get("DriverVersion", "")
            backend = g.get("Backend", "directx")
            gpus.append(GPUInfo(name=name, vram_gb=vram_gb, driver=driver, backend=backend))

        return gpus, ram_gb, cpu_name, cpu_cores
    except Exception:
        return [], 0, "", 0


# Names that indicate an integrated GPU (shares system RAM).
_IGPU_KEYWORDS = [
    "radeon(tm) graphics", "radeon graphics", "amd radeon graphics",
    "intel(r) uhd", "intel(r) hd", "intel iris",
    "radeon 680m", "radeon 780m", "radeon 760m", "radeon 610m", "radeon 660m",
]


def _classify_gpu(name: str) -> str:
    """Classify a GPU as 'discrete' or 'integrated' from its name."""
    n = name.lower()
    if any(k in n for k in _IGPU_KEYWORDS):
        return "integrated"
    return "discrete"


def build_compute_tiers(gpus: list[GPUInfo], ram_gb: float,
                        is_apple_silicon: bool = False) -> list[ComputeTier]:
    """Build the ordered compute-tier list: discrete GPU(s) → iGPU → RAM.

    On Apple Silicon the GPU and RAM are unified, so there is a single tier.
    Device indices are assigned discrete-first then integrated, matching the
    typical HIP/CUDA device ordering (dGPU = 0, iGPU = 1).
    """
    if is_apple_silicon:
        if gpus:
            gpus[0].device_index = 0
        return [ComputeTier(
            name=gpus[0].name if gpus else "Apple Silicon",
            memory_gb=gpus[0].vram_gb if gpus else ram_gb,
            backend="metal", kind="discrete", device_index=0,
        )] if gpus else []

    discrete = []
    integrated = []
    for gpu in gpus:
        kind = gpu.tier or _classify_gpu(gpu.name)
        (discrete if kind == "discrete" else integrated).append(gpu)

    # Fastest discrete GPU first (most VRAM = primary).
    discrete.sort(key=lambda g: g.vram_gb, reverse=True)

    tiers: list[ComputeTier] = []
    idx = 0
    for gpu in discrete:
        gpu.device_index = idx  # detection paths never set this; the tier order is the source of truth
        tiers.append(ComputeTier(
            name=gpu.name, memory_gb=gpu.vram_gb, backend=gpu.backend,
            kind="discrete", device_index=idx,
        ))
        idx += 1
    for gpu in integrated:
        gpu.device_index = idx
        tiers.append(ComputeTier(
            name=gpu.name, memory_gb=gpu.vram_gb, backend=gpu.backend,
            kind="integrated", device_index=idx,
        ))
        idx += 1

    # RAM is always the slowest fallback tier.
    if ram_gb > 0 and not is_apple_silicon:
        tiers.append(ComputeTier(
            name="System RAM", memory_gb=ram_gb, backend="cpu",
            kind="ram", device_index=-1,
        ))
    return tiers


def detect() -> SystemInfo:
    info = SystemInfo(os=platform.system(), in_container=_in_container())

    if info.os == "Windows":
        gpus, ram_gb, cpu_name, cpu_cores = _detect_windows()
        info.gpus = gpus
        info.ram_gb = ram_gb
        info.cpu = cpu_name
        info.cpu_cores = cpu_cores
        info.has_nvidia = any("nvidia" in g.name.lower() for g in gpus)
        info.has_amd = any("amd" in g.name.lower() for g in gpus)

    elif info.os == "Darwin":
        is_apple, gpus, total_ram = _detect_apple_silicon()
        info.is_apple_silicon = is_apple
        info.gpus = gpus
        if total_ram:
            info.ram_gb = round(total_ram, 1)
        else:
            out = _run_cmd(["sysctl", "-n", "hw.memsize"])
            if out:
                info.ram_gb = round(int(out) / (1024 ** 3), 1)
        out = _run_cmd(["sysctl", "-n", "machdep.cpu.brand_string"])
        info.cpu = out or "Apple Silicon"
        info.cpu_cores = os.cpu_count() or 0

    else:
        nv_gpus = _detect_nvidia()
        amd_gpus = _detect_amd_linux()
        info.gpus = nv_gpus + amd_gpus
        info.has_nvidia = len(nv_gpus) > 0
        info.has_amd = len(amd_gpus) > 0

        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    m = re.match(r"^MemTotal:\s+(\d+)", line)
                    if m:
                        info.ram_gb = round(int(m.group(1)) / 1048576, 1)
                        break
        except OSError:
            pass

        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    m = re.match(r"^model name\s+:\s+(.+)", line)
                    if m:
                        info.cpu = m.group(1).strip()
                        break
        except OSError:
            pass
        info.cpu_cores = os.cpu_count() or 0

    # Classify each GPU as discrete/integrated (stored on GPUInfo for the API).
    for g in info.gpus:
        g.tier = _classify_gpu(g.name)

    # total_vram_gb = best single discrete GPU (backward-compat; what fits in one
    # GPU without the hand-off). combined_vram_gb = all GPUs summed.
    discrete = [g for g in info.gpus if g.tier == "discrete"]
    if discrete:
        info.total_vram_gb = round(max(g.vram_gb for g in discrete), 1)
    elif info.gpus:
        info.total_vram_gb = round(max(g.vram_gb for g in info.gpus), 1)
    else:
        info.total_vram_gb = 0.0

    gpu_vram = [g for g in info.gpus if g.tier in ("discrete", "integrated")]
    info.combined_vram_gb = round(sum(g.vram_gb for g in gpu_vram), 1) if gpu_vram else info.total_vram_gb

    # Build the full compute-tier hierarchy (dGPU → iGPU → RAM).
    info.compute_tiers = build_compute_tiers(info.gpus, info.ram_gb, info.is_apple_silicon)
    return info


def print_system(info: SystemInfo) -> None:
    print(f"OS:          {info.os}")
    if info.in_container:
        print("             (running inside container)")
    print(f"CPU:         {info.cpu}")
    print(f"Cores:       {info.cpu_cores}")
    print(f"RAM:         {info.ram_gb} GB")
    if info.is_apple_silicon:
        print(f"Apple Silicon unified memory (GPU budget): {info.gpus[0].vram_gb} GB" if info.gpus else "")
    elif info.gpus:
        for i, gpu in enumerate(info.gpus):
            kind = gpu.tier
            marker = " (primary)" if i == 0 and len(info.gpus) > 1 else ""
            print(f"GPU {i}:        {gpu.name} ({gpu.vram_gb} GB VRAM, {gpu.backend}, {kind}){marker}")
        print(f"Effective:   {info.total_vram_gb} GB VRAM (best discrete GPU)")
        if info.combined_vram_gb > info.total_vram_gb:
            extra = info.combined_vram_gb - info.total_vram_gb
            print(f"Hand-off:    {info.combined_vram_gb} GB combined GPU VRAM (+{extra:.1f} GB from iGPU)")
    else:
        print("GPU:         None detected (CPU only)")
    if info.compute_tiers and not info.is_apple_silicon and len(info.compute_tiers) > 1:
        print("Compute tiers:")
        for t in info.compute_tiers:
            print(f"  {t.kind:<11} {t.name} ({t.memory_gb} GB, {t.backend})")
    print()
