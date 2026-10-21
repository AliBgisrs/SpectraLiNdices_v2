"""Compute-backend detection.

Order of preference: CuPy (CUDA) -> PyTorch CUDA -> PyTorch MPS (Apple) -> NumPy (CPU).
Every heavy array operation in the engine goes through the `Backend` object so the
same code runs on GPU or CPU. Results always come back as NumPy arrays.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess

import numpy as np


def _nvidia_smi():
    exe = shutil.which("nvidia-smi")
    if not exe:
        for cand in (r"C:\Windows\System32\nvidia-smi.exe",
                     r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
            if os.path.exists(cand):
                exe = cand
                break
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total,driver_version",
                              "--format=csv,noheader"], capture_output=True, text=True, timeout=10)
        return [l.strip() for l in out.stdout.splitlines() if l.strip()]
    except Exception:
        return []


def list_system_gpus():
    """Best-effort list of GPUs present (CUDA-capable or not), for display only."""
    gpus = [f"NVIDIA: {g}" for g in _nvidia_smi()]
    if not gpus and platform.system() == "Windows":
        try:
            out = subprocess.run(["wmic", "path", "win32_videocontroller", "get", "name"],
                                 capture_output=True, text=True, timeout=10)
            names = [l.strip() for l in out.stdout.splitlines()[1:] if l.strip()]
            gpus = names
        except Exception:
            pass
    return gpus


class Backend:
    """Thin numpy-compatible facade over cupy / torch / numpy."""

    def __init__(self, prefer_gpu: bool = True, device_index: int = 0):
        self.kind = "numpy"
        self.device_name = "CPU"
        self.devices = []
        self.xp = np
        self._torch = None
        self._dev = None
        self.notes = []
        self.device_index = 0
        if prefer_gpu:
            self._try_cupy(device_index) or self._try_torch(device_index)
        if self.kind == "numpy":
            self.device_name = f"CPU ({os.cpu_count()} logical cores)"

    # ------------------------------------------------------------------ detection
    def _try_cupy(self, idx):
        try:
            import cupy as cp
            n = cp.cuda.runtime.getDeviceCount()
            if n < 1:
                return False
            for i in range(n):
                p = cp.cuda.runtime.getDeviceProperties(i)
                name = p["name"].decode() if isinstance(p["name"], bytes) else p["name"]
                self.devices.append(f"cuda:{i} {name} ({p['totalGlobalMem'] / 2**30:.1f} GB)")
            idx = min(idx, n - 1)
            self.device_index = idx
            cp.cuda.Device(idx).use()
            cp.zeros(4).sum()  # smoke test the context
            self.xp, self.kind = cp, "cupy"
            self.device_name = self.devices[idx]
            return True
        except Exception as e:  # not installed / no driver
            self.notes.append(f"CuPy unavailable: {type(e).__name__}")
            return False

    def _try_torch(self, idx):
        try:
            import torch
        except Exception as e:
            self.notes.append(f"PyTorch unavailable: {type(e).__name__}")
            return False
        try:
            if torch.cuda.is_available():
                n = torch.cuda.device_count()
                for i in range(n):
                    p = torch.cuda.get_device_properties(i)
                    self.devices.append(f"cuda:{i} {p.name} ({p.total_memory / 2**30:.1f} GB)")
                idx = min(idx, n - 1)
                self._dev = torch.device(f"cuda:{idx}")
                self.device_name = self.devices[idx]
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                self._dev = torch.device("mps")
                self.devices.append("mps (Apple GPU)")
                self.device_name = "mps (Apple GPU)"
            else:
                self.notes.append("PyTorch present but no CUDA/MPS device")
                return False
            torch.zeros(4, device=self._dev).sum()
            self._torch, self.kind = torch, "torch"
            return True
        except Exception as e:
            self.notes.append(f"PyTorch GPU init failed: {e}")
            return False

    @property
    def is_gpu(self):
        return self.kind != "numpy"

    def info(self):
        return {"backend": self.kind, "device": self.device_name, "gpu": self.is_gpu,
                "cuda_devices": self.devices, "system_gpus": list_system_gpus(),
                "notes": self.notes}

    # ------------------------------------------------------------------ transfer
    def asarray(self, a):
        if self.kind == "cupy":
            return self.xp.asarray(a, dtype=self.xp.float32)
        if self.kind == "torch":
            return self._torch.as_tensor(np.ascontiguousarray(a, dtype=np.float32), device=self._dev)
        return np.asarray(a, dtype=np.float32)

    def to_numpy(self, a):
        if self.kind == "cupy":
            return self.xp.asnumpy(a)
        if self.kind == "torch":
            return a.detach().cpu().numpy()
        return np.asarray(a)

    # ------------------------------------------------------------------ math namespace
    def math_ns(self):
        """Namespace used to eval index formulas written with `np.` functions."""
        if self.kind == "torch":
            t, dev = self._torch, self._dev

            def T(x):  # scalars -> tensors so numpy-style formulas such as np.maximum(NIR-R, 0) work
                return x if isinstance(x, t.Tensor) else t.as_tensor(x, dtype=t.float32, device=dev)

            class _NS:
                sqrt = staticmethod(lambda x: t.sqrt(T(x)))
                abs = staticmethod(lambda x: t.abs(T(x)))
                log = staticmethod(lambda x: t.log(T(x)))
                exp = staticmethod(lambda x: t.exp(T(x)))
                arctan = staticmethod(lambda x: t.atan(T(x)))
                where = staticmethod(lambda c, a, b: t.where(T(c).bool(), T(a), T(b)))
                maximum = staticmethod(lambda a, b: t.maximum(T(a), T(b)))
                minimum = staticmethod(lambda a, b: t.minimum(T(a), T(b)))
                power = staticmethod(lambda a, b: t.pow(T(a), T(b)))
                pi = float(np.pi)
            return _NS
        return self.xp

    def device_ctx(self):
        """Context selecting this backend's GPU in the *current* thread (CuPy devices are per-thread)."""
        import contextlib
        if self.kind == "cupy":
            return self.xp.cuda.Device(self.device_index)
        if self.kind == "torch" and self._dev.type == "cuda":
            return self._torch.cuda.device(self._dev)
        return contextlib.nullcontext()

    def isfinite(self, a):
        return self._torch.isfinite(a) if self.kind == "torch" else self.xp.isfinite(a)

    def compress(self, a, mask):
        """1-D vector of a[mask]."""
        return a[mask]

    def stats1d(self, v):
        """min, mean, median, max, std (ddof=1), range, count of a 1-D device vector."""
        n = int(v.shape[0]) if self.kind == "torch" else int(v.size)
        if n == 0:
            return dict(min=np.nan, mean=np.nan, median=np.nan, max=np.nan, std=np.nan,
                        range=np.nan, count=0)
        if self.kind == "torch":
            t = self._torch
            v = v.double() if self._dev.type == "cuda" else v
            vs, _ = t.sort(v)
            mn, mx = float(vs[0]), float(vs[-1])
            med = float(vs[(n - 1) // 2] + vs[n // 2]) / 2.0
            mean = float(v.mean())
            std = float(v.std(unbiased=True)) if n > 1 else np.nan
        else:
            xp = self.xp
            mn, mx = float(xp.min(v)), float(xp.max(v))
            med = float(xp.median(v))
            mean = float(xp.mean(v, dtype=xp.float64))
            std = float(xp.std(v, ddof=1, dtype=xp.float64)) if n > 1 else np.nan
        return dict(min=mn, mean=mean, median=med, max=mx, std=std, range=mx - mn, count=n)

    def percentile(self, v, q):
        n = int(v.shape[0]) if self.kind == "torch" else int(v.size)
        if n == 0:
            return [np.nan] * len(q)
        if self.kind == "torch":
            vs, _ = self._torch.sort(v.double() if self._dev.type == "cuda" else v)
            out = []
            for qq in q:  # linear interpolation, same as numpy default
                pos = (n - 1) * qq / 100.0
                lo, hi = int(np.floor(pos)), int(np.ceil(pos))
                out.append(float(vs[lo] + (vs[hi] - vs[lo]) * (pos - lo)))
            return out
        return [float(x) for x in self.xp.percentile(v, q)]

    def free(self):
        if self.kind == "cupy":
            self.xp.get_default_memory_pool().free_all_blocks()
        elif self.kind == "torch" and self._dev.type == "cuda":
            self._torch.cuda.empty_cache()


_BACKENDS = {}


def get_backend(prefer_gpu=True, device_index=0, refresh=False) -> Backend:
    key = (bool(prefer_gpu), int(device_index))
    if refresh or key not in _BACKENDS:
        _BACKENDS[key] = Backend(prefer_gpu=prefer_gpu, device_index=device_index)
    return _BACKENDS[key]
