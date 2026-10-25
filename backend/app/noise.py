"""Seeded host CPU/disk duty-cycle interference (indirect on Docker Desktop)."""

import logging
import multiprocessing as mp
import os
import tempfile
from contextlib import nullcontext
from multiprocessing.process import BaseProcess
from pathlib import Path
from time import perf_counter, sleep
from types import TracebackType

import numpy as np
import psutil

_LOG = logging.getLogger(__name__)


def _worker(level: float, seed: int, path: str | None) -> None:
    rng = np.random.default_rng(seed)
    payload = b"0" * 1024 * 1024
    with open(path, "r+b", buffering=0) if path else nullcontext() as handle:
        while True:
            start = perf_counter()
            busy = min(0.1, level * 0.1 * float(rng.uniform(0.8, 1.2)))
            while perf_counter() - start < busy:
                if handle:
                    if handle.tell() >= 64 * 1024 * 1024:
                        handle.seek(0)
                    handle.write(payload)
                    os.fsync(handle.fileno())
                else:
                    # Busy arithmetic deliberately consumes a host CPU core.
                    sum(i * i for i in range(1000))
            sleep(max(0, 0.1 - (perf_counter() - start)))


class NoiseInjector:
    """Own and reliably stop seeded host processes; level is duty, not latency noise."""

    def __init__(
        self, level: float, cpu_workers: int | None = None, disk: bool = True, seed: int = 0
    ) -> None:
        if not np.isfinite(level) or not 0 <= level <= 1 or seed < 0:
            raise ValueError("Invalid noise duty or seed")
        self.level = level
        self.cpu_workers = (
            max(1, (os.cpu_count() or 2) // 2) if cpu_workers is None else cpu_workers
        )
        if self.cpu_workers < 0:
            raise ValueError("Worker count cannot be negative")
        self.disk, self.seed = disk, seed
        self.processes: list[BaseProcess] = []
        self._temp: tempfile.TemporaryDirectory | None = None
        self.cpu_percent: float | None = None

    def __enter__(self) -> "NoiseInjector":
        if self.processes:
            raise RuntimeError("Noise injector already active")
        psutil.cpu_percent(interval=None)
        if self.level == 0:
            return self
        context = mp.get_context("spawn")
        try:
            paths: list[str | None] = [None] * self.cpu_workers
            if self.disk:
                self._temp = tempfile.TemporaryDirectory(prefix="dpa-noise-")
                disk_path = Path(self._temp.name) / "noise.bin"
                with disk_path.open("wb") as handle:
                    handle.truncate(64 * 1024 * 1024)
                paths.append(str(disk_path))
            for child_seed, path in zip(
                np.random.SeedSequence(self.seed).spawn(len(paths)), paths, strict=True
            ):
                seed = int(child_seed.generate_state(1)[0])
                process = context.Process(
                    target=_worker, args=(self.level, seed, path), daemon=True
                )
                process.start()
                self.processes.append(process)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        for process in self.processes:
            process.terminate()
        for process in self.processes:
            process.join(2)
            if process.is_alive():
                process.kill()
                process.join(2)
            process.close()
        self.processes.clear()
        if self._temp:
            self._temp.cleanup()
            self._temp = None
        self.cpu_percent = psutil.cpu_percent(interval=None)
        _LOG.info(
            "Host CPU load %.1f%% at requested duty %.3f; Docker VM affected indirectly",
            self.cpu_percent,
            self.level,
        )
