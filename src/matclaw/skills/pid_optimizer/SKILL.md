# PID Optimizer

## Purpose
Tune PID controller gains (Kp, Ki, Kd) for a Simulink model (or a simulated plant) by running an **Observation Loop**: run simulation → evaluate Rise Time and Overshoot → adjust gains → repeat until the system is stable or max iterations reached.

## When to Use
- User asks to tune PID, optimize gains, or improve step response (rise time, overshoot).
- A Simulink model or a .m plant model is available and can be run from MATLAB.

## Inputs
- `Kp`, `Ki`, `Kd`: initial or current gains (floats).
- `target_rise_time` (optional): desired rise time in seconds.
- `target_overshoot_pct` (optional): max acceptable overshoot in percent.
- `max_iterations` (optional): cap on observation-loop iterations (default 10).

## Outputs
- `gains`: final `{ Kp, Ki, Kd }`.
- `metrics`: `{ rise_time, overshoot_pct }` from the last run.
- `iterations`: number of iterations performed.
- `stable`: whether the last run met targets (or was accepted).

## Constraints
- All MATLAB calls MUST be wrapped in try/except; capture full error stack for the debug agent.
- Use Pydantic models for data passed between Python and MATLAB.
- If modifying a user Simulink model or .m file, create a .bak before writing (No Ghost Files).

## RPI Loop
1. **Research**: Run the current model/simulation with given Kp, Ki, Kd; obtain rise time and overshoot (e.g. via a template that simulates and returns metrics).
2. **Plan**: Compare metrics to targets; decide new Kp, Ki, Kd (e.g. simple heuristic: increase Kp if rise time too slow; add derivative if overshoot high).
3. **Execute**: Set gains in MATLAB (or write to model), run simulation again.

## Observation Loop
- Loop: run simulation → get rise_time, overshoot → if within tolerance, return success; else plan new gains and execute → repeat.
- Stop when: metrics within tolerance, or max_iterations reached, or simulation error.
