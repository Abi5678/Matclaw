% MatClaw_Header: Simulink Runner template
% Load-Compile-Run: load_system -> set_param -> sim
% Returns scalar struct for Python engine (no arrays/cells).
function out = simulink_run(model_name, stop_time)
    out = struct();
    out.status = 'Error';
    out.stop_time = 0;
    out.final_state = '';
    out.error_log = '';
    try
        % Normalize: add .slx for load_system; base name for set_param/sim
        model_name = char(model_name);
        if isempty(regexp(model_name, '\.slx$', 'once'))
            load_name = [model_name '.slx'];
        else
            load_name = model_name;
        end
        model_base = regexprep(load_name, '\.slx$', '');
        stop_time = double(stop_time);
        out.stop_time = stop_time;

        % Load
        load_system(load_name);

        % Compile: set StopTime (use base name)
        set_param(model_base, 'StopTime', num2str(stop_time));

        % Run
        sim_out = sim(model_base);
        out.status = 'Success';
        out.final_state = 'Completed';
    catch ME
        out.status = 'Error';
        out.final_state = char(ME.identifier);
        out.error_log = char(ME.message);
    end
end
