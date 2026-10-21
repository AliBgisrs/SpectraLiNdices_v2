"""Headless end-to-end run of the pipeline with every module switched on."""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
from engine import pipeline  # noqa: E402

T = os.path.join(ROOT, "tests", "test_plots")
cfg = {
    "input_folder": os.path.join(ROOT, "inputFolder"),
    "output_folder": os.path.join(ROOT, "tests", "out_headless"),
    "boundary": {"path": os.path.join(T, sys.argv[1] if len(sys.argv) > 1 else "plots.shp"), "id_col": "Plot_ID",
                 "group_col": "Treatment", "buffer_m": 0},
    "spectral_pixels": "all",
    "gpu": {"prefer": True},
    "stats": {"enabled": True, "unit": "cells", "cell_m": 0.5, "alpha": 0.05},
    "viz": {"enabled": True, "dpi": 350},
    "rasters": {"enabled": True, "layers": ["NDVI", "PlantHeight_m"]},
    "advanced": {
        "stand_count": {"enabled": True, "method": "blob"},
        "canopy": {"enabled": True},
        "harvest": {"enabled": True, "min_height": 0.10},
        "length": {"enabled": True},
        "correlation": {"enabled": True, "method": "pearson"},
        "thermal": {"enabled": True, "crop": "Barley", "use_weather": True},
        "gdd": {"enabled": True, "crop": "Barley", "sowing_date": "2026-05-05"},
        "field_data": {"enabled": True, "path": os.path.join(T, "field_data.csv"), "join_col": "PlotNumber"},
    },
}
if __name__ == "__main__":  # required: figures render in spawned worker processes
    job = pipeline.Job(cfg)
    t = time.time()
    res = pipeline.run(job)
    print(json.dumps({k: v for k, v in res.items() if k != "figures"}, indent=1, default=str))
    print("figures:", len(res["figures"]), "time", round(time.time() - t, 1))
