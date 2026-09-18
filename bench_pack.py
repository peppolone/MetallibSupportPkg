#!/usr/bin/env python3
"""
Benchmark del solo step di packing su ShaderGraph/Espresso.
Non modifica nulla del progetto: usa metal_libraries per unpack/patch,
poi cronometra strategie diverse di packing.
"""
import subprocess, sys, time, shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from metal_libraries.metallib.patch import MetallibPatch

WORK = Path("/tmp/bench")

def run(cmd, **kw):
    t = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return time.time() - t, r

def tool_info():
    for t in ("metallib", "metal-ar", "metal"):
        r = subprocess.run(["/usr/bin/xcrun", t, "--version"], capture_output=True, text=True)
        print(f"--- {t} --version ---\n{(r.stdout or r.stderr).strip()[:400]}")
    r = subprocess.run(["/usr/bin/xcrun", "metallib", "--help"], capture_output=True, text=True)
    print(f"--- metallib --help ---\n{(r.stdout or r.stderr).strip()[:2500]}")

def prepare(metallib_path: Path, tag: str) -> list[str]:
    """Unpack + patch + recompile -> lista di .air pronti da impacchettare."""
    d = WORK / tag
    if d.exists(): shutil.rmtree(d)
    d.mkdir(parents=True)
    p = MetallibPatch()
    airs = []
    for i, (name, data) in enumerate(p._unpack_metallib_to_air(str(metallib_path))):
        f = d / f"{i:05}.air"
        f.write_bytes(data)
        airs.append(str(f))
    print(f"[{tag}] {len(airs)} .air estratti")
    # patch+ricompila come fa la pipeline vera, per avere input realistici
    t = time.time()
    for a in airs:
        p._decompile_air_to_ll(a)
        p._patch_ll(str(Path(a).with_suffix(".ll")))
        p._recompile_ll_to_air(str(Path(a).with_suffix(".ll")))
    print(f"[{tag}] decompile+patch+recompile: {time.time()-t:.1f}s")
    return airs

def bench(tag: str, airs: list[str]):
    out = WORK / f"{tag}.metallib"
    n = len(airs)
    print(f"\n===== {tag}: {n} .air =====", flush=True)

    # 1) curva di scala
    for k in [200, 400, 800, 1200, n]:
        if k > n: continue
        dt, r = run(["/usr/bin/xcrun", "metallib", *airs[:k], "-o", str(out)])
        ok = "ok" if r.returncode == 0 else f"FAIL {r.stderr.strip()[:120]}"
        print(f"  baseline  n={k:5}  {dt:8.1f}s  {ok}", flush=True)

    # 2) metal-ar -> metalar -> metallib
    ar = WORK / f"{tag}.metalar"
    dt_ar, r = run(["/usr/bin/xcrun", "metal-ar", "r", str(ar), *airs])
    if r.returncode != 0:
        print(f"  metal-ar  FAIL {r.stderr.strip()[:200]}", flush=True)
    else:
        dt_lib, r2 = run(["/usr/bin/xcrun", "metallib", str(ar), "-o", str(out)])
        ok = "ok" if r2.returncode == 0 else f"FAIL {r2.stderr.strip()[:120]}"
        print(f"  metal-ar  ar={dt_ar:.1f}s + lib={dt_lib:.1f}s = {dt_ar+dt_lib:8.1f}s  {ok}", flush=True)

    # 3) chunk in .metallib -> merge
    for chunk in (200, 400):
        parts, t0, failed = [], time.time(), False
        for i in range(0, n, chunk):
            pth = WORK / f"{tag}_p{i}.metallib"
            _, r = run(["/usr/bin/xcrun", "metallib", *airs[i:i+chunk], "-o", str(pth)])
            if r.returncode != 0:
                print(f"  chunk{chunk}  parziale FAIL {r.stderr.strip()[:120]}", flush=True); failed = True; break
            parts.append(str(pth))
        if failed: continue
        t_parts = time.time() - t0
        dt_m, r = run(["/usr/bin/xcrun", "metallib", *parts, "-o", str(out)])
        ok = "ok" if r.returncode == 0 else f"MERGE FAIL {r.stderr.strip()[:150]}"
        print(f"  chunk{chunk}  parti={t_parts:.1f}s + merge={dt_m:.1f}s = {t_parts+dt_m:8.1f}s  {ok}", flush=True)

    # 4) chunk in .metalar -> metallib su piu' archivi
    for chunk in (200,):
        ars, t0, failed = [], time.time(), False
        for i in range(0, n, chunk):
            pth = WORK / f"{tag}_a{i}.metalar"
            _, r = run(["/usr/bin/xcrun", "metal-ar", "r", str(pth), *airs[i:i+chunk]])
            if r.returncode != 0:
                print(f"  ar{chunk}     parziale FAIL {r.stderr.strip()[:120]}", flush=True); failed = True; break
            ars.append(str(pth))
        if failed: continue
        t_ar = time.time() - t0
        dt_m, r = run(["/usr/bin/xcrun", "metallib", *ars, "-o", str(out)])
        ok = "ok" if r.returncode == 0 else f"FAIL {r.stderr.strip()[:150]}"
        print(f"  ar{chunk}     archivi={t_ar:.1f}s + lib={dt_m:.1f}s = {t_ar+dt_m:8.1f}s  {ok}", flush=True)

if __name__ == "__main__":
    root = Path(sys.argv[1])
    tool_info()
    targets = {
        "ShaderGraph": "System/Library/PrivateFrameworks/ShaderGraph.framework/Versions/A/Resources/default.metallib",
        "CoreRE":      "System/Library/PrivateFrameworks/CoreRE.framework/Versions/A/Resources/default.metallib",
    }
    for tag, rel in targets.items():
        p = root / rel
        if not p.exists():
            hits = list(root.rglob(f"**/{tag}.framework/**/*.metallib"))
            if not hits:
                print(f"[{tag}] non trovato, salto"); continue
            p = hits[0]
        bench(tag, prepare(p, tag))
