% MatClaw_Header: Workspace Auditor v1.1
% Returns workspace summary for Python. ONLY scalar struct fields (no arrays/cells).
% FIX: Python engine rejects struct arrays and cell arrays - use pipe-delimited strings.
function out = workspace_audit()
    try
        % Use evalin('base',...) to see the BASE workspace, not this function's scope
        w = evalin('base', 'whos');
        n = numel(w);
        out.var_count = double(n);
        out.total_bytes = double(sum([w.bytes]));
        % Pipe-delimited strings (no cell arrays)
        if n > 0
            nameStrs = arrayfun(@(i) char(w(i).name), 1:n, 'UniformOutput', false);
            classStrs = arrayfun(@(i) char(w(i).class), 1:n, 'UniformOutput', false);
            byteStrs = arrayfun(@(i) num2str(double(w(i).bytes)), 1:n, 'UniformOutput', false);
            out.variable_names = strjoin(nameStrs, '|');
            out.variable_classes = strjoin(classStrs, '|');
            out.variable_bytes = strjoin(byteStrs, '|');
        else
            out.variable_names = '';
            out.variable_classes = '';
            out.variable_bytes = '';
        end
        try
            lic = license('inuse');
            out.license_info = sprintf('%d product(s) in use', numel(lic));
        catch
            out.license_info = 'unknown';
        end
    catch ME
        out.error = char(ME.message);
        out.var_count = 0;
        out.total_bytes = 0;
        out.variable_names = '';
        out.variable_classes = '';
        out.variable_bytes = '';
        out.license_info = '';
    end
end
