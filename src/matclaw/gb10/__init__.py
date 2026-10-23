"""
GB10 remote compute backend — offload heavy simulation to an NVIDIA DGX Spark
cluster (GB10: ARM64, Grace CPU, Blackwell GPU, 128 GB unified memory, CUDA 13).

MATLAB has no Linux ARM64 build, so it stays on the user's Mac/x86 host.
This package provides:

- client.py           — SSH-based remote job execution with multi-node support
                        (upload, build, run, download; split work across nodes)
- codegen.py          — GPU Coder / MATLAB Coder path: generate CUDA/C on the host,
                        compile on the GB10, run, return results
- octave_fallback.py  — run plain .m scripts via GNU Octave on the GB10 (CPU)
"""
