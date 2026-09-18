#!/usr/bin/env python3
"""
Benchmark 2: --split-module / --split-module-without-linking
Cronometra e VERIFICA l'output (n. funzioni, versione AIR, confronto con l'originale).
"""
import subprocess, sys, time, shutil, struct
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from metal_libraries.metallib.patch import MetallibPatch

WORK = Path("/tmp/bench2")

def run(cmd):
    t = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    return time.time() - t, r

def probe(path: Path, p: MetallibPatch, label: str, original_names: list[str]):
    """Riapre il metallib prodotto e verifica struttura + versione AIR."""
    try:
        entries = p._unpack_metallib_to_air(str(path))
    except Exception as e:
        print(f"      VERIFICA {label}: parser FALLITO -> {e}")
        return
    names = [n for n, _ in entries]
    data = path.read_bytes()
    ver = struct.unpack("<HHH", data[4:10])
    # controlla l'AIR nel primo modulo via metal-objdump
    tmp = WORK / "probe.air"
    tmp.write_bytes(entries[0][1])
    r = subprocess.run(["/usr/bin/xcrun", "metal-objdump", "--disassemble", str(tmp)],
                       capture_output=True, text=True)
    air = "?"
    for line in r.stdout.splitlines():
        if "air64_v" in line:
            air = [t for t in line.split('"') if "air64_v" in t]
            air = air[0] if air else line.strip()[:60]
            break
    missing = set(original_names) - set(names)
    print(f"      VERIFICA {label}: {len(names)} funzioni (orig {len(original_names)}, mancanti {len(missing)}), "
          f"header ver={ver}, size={len(data)}, target={air}")

def bench(metallib_path: Path, tag: str):
    d = WORK / tag
    if d.exists(): shutil.rmtree(d)
    d.mkdir(parents=True)
    p = MetallibPatch()

    original = p._unpack_metallib_to_air(str(metallib_path))
    original_names = [n for n, _ in original]
    orig_size = metallib_path.stat().st_size
    print(f"\n===== {tag}: {len(original)} funzioni, originale {orig_size} byte =====", flush=True)

    airs = []
    for i, (name, blob) in enumerate(original):
        f = d / f"{i:05}.air"
        f.write_bytes(blob)
        airs.append(str(f))
    t = time.time()
    for a in airs:
        p._decompile_air_to_ll(a)
        p._patch_ll(str(Path(a).with_suffix(".ll")))
        p._recompile_ll_to_air(str(Path(a).with_suffix(".ll")))
    print(f"  decompile+patch+recompile: {time.time()-t:.1f}s", flush=True)

    for label, extra in [
        ("baseline               ", []),
        ("--split-module         ", ["--split-module"]),
        ("--split-module-no-link ", ["--split-module-without-linking"]),
    ]:
        out = WORK / f"{tag}{label.strip().replace('-','')}.metallib"
        dt, r = run(["/usr/bin/xcrun", "metallib", *extra, *airs, "-o", str(out)])
        if r.returncode != 0:
            print(f"  {label} {dt:8.1f}s  FAIL: {r.stderr.strip()[:200]}", flush=True)
            continue
        print(f"  {label} {dt:8.1f}s  ok  -> {out.stat().st_size} byte", flush=True)
        probe(out, p, label.strip(), original_names)

if __name__ == "__main__":
    root = Path(sys.argv[1])
    WORK.mkdir(parents=True, exist_ok=True)
    for tag in ("ShaderGraph", "CoreRE", "Espresso"):
        hits = list(root.rglob(f"**/{tag}.framework/**/*.metallib"))
        if not hits:
            print(f"[{tag}] non trovato"); continue
        bench(hits[0], tag)
