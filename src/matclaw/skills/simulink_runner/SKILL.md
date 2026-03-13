# Simulink Runner Skill

**Trigger:** When the user mentions a `.slx` file, "simulation," "run model," or "simulate [model name]."

## Research Phase

- Verify if the Simulink license is active (`license('test', 'Simulink')`).
- Check if the specified `.slx` file exists in the current path or MATLAB path.
- If the model is not found, suggest searching subdirectories or provide the full path.

## Plan Phase (Load-Compile-Run)

1. **Load:** Use `load_system(model_name)` to load the model into memory.
2. **Compile:** Set parameters (e.g. `StopTime`) via `set_param`.
3. **Run:** Execute `sim(model_name)` and capture `Simulink.SimulationOutput`.

## Implementation Details

- Use `load_system` before `sim` to avoid overhead and ensure the model is compiled.
- Always capture the `Simulink.SimulationOutput` object for scope data and logs.
- Models can be large—allow time for load/compile. Use heartbeat updates for long runs.
- If the model is not found, suggest searching subdirectories or checking the path.

## Inputs

- `model_name`: Name of the .slx model (with or without extension).
- `stop_time`: Simulation stop time in seconds (default: 10.0).

## Outputs

- `status`: "Success" or "Error"
- `stop_time`: Actual stop time used
- `final_state`: "Completed" or error description
- `error_log`: MATLAB error message if failed
