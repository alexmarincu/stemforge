import time

t0 = time.perf_counter()
import torch  # noqa: F401
from demucs.api import Separator  # noqa: F401

print(f"ready in {time.perf_counter() - t0:.2f}s")
