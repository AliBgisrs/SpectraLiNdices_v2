"""Stand counting with MobileSAM (lightweight Segment Anything, Zhang et al. 2023).

Two modes, both per plot on an RGB (or false-colour) chip at native resolution:
  prompted  - NDVI local maxima inside the vegetation mask are used as positive point prompts; every
              prompt yields one instance mask. Fast (one image embedding + N tiny decoder calls) and
              robust for row crops. Masks that are duplicates (IoU > 0.5), too small/large or mostly
              soil are dropped. Default.
  automatic - SAM's grid-based automatic mask generator; no NDVI seeds needed. Slower, finds plants the
              peak detector misses but also returns soil clods and leaves, so the same filters apply.

The model (~40 MB) is downloaded once to app/models/mobile_sam.pt. Runs on CUDA when PyTorch sees a
GPU, otherwise on the CPU (about 0.5-2 s per plot). Everything is optional: if torch / mobile_sam are
not installed the method is unavailable and the UI says how to install it.
"""
from __future__ import annotations

import os
import threading
import urllib.request

import numpy as np
from scipy import ndimage

MODEL_URL = "https://github.com/ChaoningZhang/MobileSAM/raw/master/weights/mobile_sam.pt"
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
MODEL_PATH = os.path.join(MODEL_DIR, "mobile_sam.pt")
PIP_SPEC = ["timm", "git+https://github.com/ChaoningZhang/MobileSAM.git"]

_lock = threading.Lock()
_model = None
_device = None


def status():
    """What is installed, for the UI."""
    s = {"torch": False, "mobile_sam": False, "weights": os.path.exists(MODEL_PATH), "cuda": False, "ready": False}
    try:
        import torch
        s["torch"] = True
        s["cuda"] = bool(torch.cuda.is_available())
        s["torch_version"] = torch.__version__
    except Exception:
        pass
    try:
        import mobile_sam  # noqa: F401
        s["mobile_sam"] = True
    except Exception:
        pass
    s["ready"] = s["torch"] and s["mobile_sam"] and s["weights"]
    s["model_path"] = MODEL_PATH
    return s


def install(log=print):
    """pip-install torch (CPU wheel if no CUDA torch present), MobileSAM, and download the weights."""
    import subprocess
    import sys
    st = status()
    if not st["torch"]:
        log("Installing PyTorch (CPU build; for CUDA install torch from pytorch.org first) ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "torch", "torchvision",
                               "--index-url", "https://download.pytorch.org/whl/cpu"])
    if not st["mobile_sam"]:
        log("Installing MobileSAM ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", *PIP_SPEC])
    if not os.path.exists(MODEL_PATH):
        os.makedirs(MODEL_DIR, exist_ok=True)
        log(f"Downloading MobileSAM weights (~40 MB) from {MODEL_URL} ...")
        tmp = MODEL_PATH + ".part"
        urllib.request.urlretrieve(MODEL_URL, tmp)
        os.replace(tmp, MODEL_PATH)
    return status()


def load(device_pref="auto"):
    """Load the model once per process (thread-safe)."""
    global _model, _device
    with _lock:
        if _model is not None:
            return _model, _device
        import torch
        from mobile_sam import sam_model_registry
        if not os.path.exists(MODEL_PATH):
            raise FileNotFoundError("MobileSAM weights missing - use 'Install MobileSAM' in the app")
        dev = "cuda" if (device_pref in ("auto", "cuda") and torch.cuda.is_available()) else "cpu"
        m = sam_model_registry["vit_t"](checkpoint=MODEL_PATH)
        m.to(dev).eval()
        _model, _device = m, dev
        return m, dev


def _ndvi_seeds(ndvi, veg, px, min_spacing_cm):
    from skimage.feature import peak_local_max
    sig = max(1.0, (min_spacing_cm / 100 / px) / 4)
    sm = ndimage.gaussian_filter(np.where(np.isfinite(ndvi), ndvi, 0), sig)
    md = max(1, int(round(min_spacing_cm / 100 / px)))
    pk = peak_local_max(sm, min_distance=md, labels=veg.astype(int), exclude_border=False)
    return pk  # (row, col)


def _dedupe(masks, scores, iou_thr=0.5):
    """Drop masks that overlap a higher-scoring kept mask (IoU or containment)."""
    order = np.argsort(-np.asarray(scores))
    kept = []
    for i in order:
        m = masks[i]
        a = m.sum()
        dup = False
        for j in kept:
            inter = np.logical_and(m, masks[j]).sum()
            if inter / max(1, min(a, masks[j].sum())) > iou_thr:
                dup = True
                break
        if not dup:
            kept.append(i)
    return kept


def count_plot(chip, veg, ndvi, px, mode="prompted", min_spacing_cm=5.0, min_area_cm2=20.0,
               max_area_cm2=None, veg_frac=0.5, device_pref="auto", max_prompts=400):
    """Segment plants in one plot chip (H,W,3 uint8). Returns (count, label_image, info)."""
    import torch
    from mobile_sam import SamAutomaticMaskGenerator, SamPredictor
    model, dev = load(device_pref)
    h, w = veg.shape
    pa_cm2 = px * px * 1e4
    min_px = max(4, int(min_area_cm2 / pa_cm2))
    max_px = int(max_area_cm2 / pa_cm2) if max_area_cm2 else int(0.25 * h * w)
    masks, scores = [], []
    with _lock, torch.inference_mode():
        if mode == "automatic":
            gen = SamAutomaticMaskGenerator(model, points_per_side=24, pred_iou_thresh=0.80, stability_score_thresh=0.85,
                                            min_mask_region_area=min_px)
            for m in gen.generate(chip):
                masks.append(m["segmentation"])
                scores.append(float(m["predicted_iou"]))
            n_prompts = None
        else:
            seeds = _ndvi_seeds(ndvi, veg, px, min_spacing_cm)
            n_prompts = int(len(seeds))
            if n_prompts == 0:
                return 0, np.zeros((h, w), np.int32), {"prompts": 0, "device": dev}
            if n_prompts > max_prompts:  # keep the strongest peaks
                vals = np.where(np.isfinite(ndvi), ndvi, 0)[seeds[:, 0], seeds[:, 1]]
                seeds = seeds[np.argsort(-vals)[:max_prompts]]
            pred = SamPredictor(model)
            pred.set_image(chip)
            # batched point prompts: (N,1,2) in x,y order
            pts = torch.as_tensor(seeds[:, ::-1].copy(), dtype=torch.float, device=dev)[:, None, :]
            pts = pred.transform.apply_coords_torch(pts, chip.shape[:2])
            lab = torch.ones(pts.shape[:2], dtype=torch.int, device=dev)
            B = 64
            for i in range(0, pts.shape[0], B):
                mk, sc, _ = pred.predict_torch(point_coords=pts[i:i + B], point_labels=lab[i:i + B],
                                              multimask_output=True)
                # smallest of the 3 multimask outputs that still covers the seed -> single plant, not the row
                mk, sc = mk.cpu().numpy(), sc.cpu().numpy()
                for k in range(mk.shape[0]):
                    r, c = seeds[i + k]
                    cands = [(mk[k, j].sum(), j) for j in range(mk.shape[1]) if mk[k, j, r, c]]
                    if not cands:
                        continue
                    j = min(cands)[1]
                    masks.append(mk[k, j])
                    scores.append(float(sc[k, j]))
    # filters: size, vegetation fraction, duplicates
    keep = []
    for i, m in enumerate(masks):
        a = int(m.sum())
        if a < min_px or a > max_px:
            continue
        if veg[m].mean() < veg_frac:
            continue
        keep.append(i)
    masks = [masks[i] for i in keep]
    scores = [scores[i] for i in keep]
    kept = _dedupe(masks, scores)
    label = np.zeros((h, w), np.int32)
    for n, i in enumerate(kept, 1):
        label[masks[i] & (label == 0)] = n
    areas = np.array([masks[i].sum() for i in kept], np.float32)
    info = {"prompts": n_prompts, "masks_raw": len(scores), "device": dev, "areas_px": areas,
            "mean_mask_area_cm2": float(areas.mean() * pa_cm2) if len(areas) else np.nan}
    return len(kept), label, info


def overlay(chip, label, out_path, title=""):
    """Save a QC image: chip with mask outlines and count."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from skimage.segmentation import find_boundaries
    h, w = label.shape
    f = Figure(figsize=(max(2.5, 4 * w / max(h, w)), max(2.5, 4 * h / max(h, w))), constrained_layout=True)
    FigureCanvasAgg(f)
    ax = f.add_subplot(111)
    ax.imshow(chip)
    b = find_boundaries(label, mode="outer")
    ov = np.zeros((h, w, 4), float)
    ov[b] = (1, 1, 0, 1)
    ax.imshow(ov)
    ax.set_title(title, fontsize=8)
    ax.set_xticks([])
    ax.set_yticks([])
    f.savefig(out_path, dpi=200, facecolor="white")
    return out_path


def plant_polygons(label, transform, crs, plot_id, px):
    """Vector polygons of every plant mask (one row per instance) in map coordinates."""
    import geopandas as gpd
    from rasterio.features import shapes
    from shapely.geometry import shape
    rows = []
    for geom, val in shapes(label.astype(np.int32), mask=label > 0, transform=transform):
        g = shape(geom)
        rows.append({"PLOTID": str(plot_id), "plant_id": int(val), "area_cm2": float(g.area * 1e4),
                     "cx": g.centroid.x, "cy": g.centroid.y, "geometry": g})
    if not rows:
        return None
    gdf = gpd.GeoDataFrame(rows, crs=crs)
    # one plant can vectorise into several pieces at the pixel level -> dissolve by plant id
    gdf = gdf.dissolve(by=["PLOTID", "plant_id"], aggfunc={"area_cm2": "sum", "cx": "first", "cy": "first"}).reset_index()
    return gdf


def write_label_tif(label, transform, crs, path):
    """Georeferenced plant-ID raster for one plot (0 = no plant, n = plant n)."""
    import rasterio
    os.makedirs(os.path.dirname(path), exist_ok=True)
    prof = dict(driver="GTiff", height=label.shape[0], width=label.shape[1], count=1, dtype="int32", crs=crs,
                transform=transform, nodata=0, compress="deflate")
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(label.astype(np.int32), 1)
        dst.update_tags(DESCRIPTION="MobileSAM plant instances; value = plant id within plot")
    return path
