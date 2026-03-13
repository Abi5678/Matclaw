% MatClaw_Header: PID evaluation template (2nd-order plant, PID in feedback).
% Simulates step response and returns rise time and overshoot for Python.
% In production this can be replaced by a Simulink run or a real model.
function out = pid_eval(Kp, Ki, Kd)
    try
        % Simple 2nd-order plant: 1/(s^2 + 2*0.5*s + 1), PID in feedback
        s = tf('s');
        plant = 1 / (s^2 + s + 1);
        C = pid(Kp, Ki, Kd);
        T = feedback(C * plant, 1);
        [y, t] = step(T, 20);
        % Rise time: time to go from 10% to 90% of final value
        y_final = y(end);
        if abs(y_final) < 1e-6
            out.rise_time = NaN;
            out.overshoot_pct = 0;
            out.stable = false;
            return;
        end
        y_10 = 0.1 * y_final;
        y_90 = 0.9 * y_final;
        i_10 = find(y >= y_10, 1);
        i_90 = find(y >= y_90, 1);
        if isempty(i_10) || isempty(i_90)
            out.rise_time = NaN;
        else
            out.rise_time = double(t(i_90) - t(i_10));
        end
        % Overshoot: (peak - final) / final * 100
        [ymax, imax] = max(y);
        out.overshoot_pct = double((ymax - y_final) / abs(y_final) * 100);
        out.stable = isfinite(out.rise_time) && out.overshoot_pct < 100;
        out.t = t;
        out.y = y;
    catch ME
        out.rise_time = NaN;
        out.overshoot_pct = NaN;
        out.stable = false;
        out.error = ME.message;
        out.t = [];
        out.y = [];
    end
end
