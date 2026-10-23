# GB10 Offload — Remote GPU/CPU Simulation on DGX Spark Cluster

## Purpose

Offload heavy simulation workloads (Monte Carlo, ODE solvers, parameter sweeps,
large matrix computations) to an NVIDIA GB10 (DGX Spark) cluster while MATLAB
remains on the user's Mac or x86 host.

MATLAB has **no Linux ARM64 build**, so it cannot run on the GB10 directly.
This skill bridges the gap with three execution paths:

| Path | When to use | Requires |
|------|-------------|----------|
| **GPU Coder** | MATLAB function with coder-compatible types → CUDA on Blackwell GPU | MATLAB GPU Coder on host |
| **Octave** | Plain `.m` scripts (no toolbox deps) → CPU on Grace cores | GNU Octave on GB10 |
| **Binary** | Pre-compiled CUDA/C++ binary → Blackwell GPU | Source + nvcc on GB10 |

## Cluster: Constellation (2-node example)

- **Node 1** `gn100-93da` — 10.100.120.1 (mgmt), 10.100.121.1 (interconnect)
- **Node 2** `spark-3d03.local` — 10.100.120.2 (mgmt), 10.100.121.2 (interconnect)

Multi-node strategies:
- `split` — divide work items (e.g. Monte Carlo batches) evenly across nodes
- `mpi` — launch MPI across both nodes using interconnect IPs for NCCL traffic
- `single` — run everything on the head node only

## When to Use

- User asks to "run on the GB10", "offload to GPU", "run on Spark"
- Simulation is compute-heavy (>1 min on the host)
- Monte Carlo with thousands of iterations
- Parameter sweep across a large grid
- ODE/PDE solver that benefits from GPU acceleration

## Inputs

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `code` | str | yes | MATLAB/Octave code or shell command |
| `mode` | str | no | `"octave"` (default), `"gpu_coder"`, or `"binary"` |
| `function_name` | str | gpu_coder | MATLAB function name for codegen |
| `entry_types` | str | gpu_coder | MATLAB type spec, e.g. `{zeros(1000,1), 0.01}` |
| `source_dir` | str | gpu_coder | Path to .m source directory on host |
| `work_items` | list | no | Items to split across nodes (for Monte Carlo / sweeps) |
| `multi_node` | bool | no | Use all nodes (default: True when work_items given) |

## Outputs

- `success`: bool
- `output`: simulation stdout / results summary
- `artifacts`: list of local file paths (downloaded .mat, .csv, plots)

## RPI Flow

1. **Research**: probe cluster connectivity, check CUDA/Octave availability
2. **Plan**: select execution path (gpu_coder → octave → binary), decide split strategy
3. **Execute**: upload code/data, run remotely, download results

## Constraints

- MATLAB cannot run on the GB10 (no ARM64 Linux build)
- GPU Coder requires a MATLAB license with GPU Coder toolbox on the host
- Octave does not support Simulink or most MATLAB toolboxes
- SSH keys must be configured for passwordless access to cluster nodes
