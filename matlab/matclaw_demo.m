% MatClaw Power Demo
% Demonstrates: PID step response, wave generation, and data analysis
% Run this to see what MatClaw can analyze and optimize autonomously.

fprintf('=== MatClaw Autonomous MATLAB Demo ===\n\n');

%% 1. PID Step Response Simulation
fprintf('[1] Simulating PID step response...\n');
Kp = 1.5; Ki = 0.8; Kd = 0.05;
t = 0:0.01:10;
% Approximate second-order closed-loop response
wn = sqrt(Kp); zeta = (Kd * wn + Ki / (2 * wn)) / 2;
s_response = 1 - exp(-zeta * wn * t) .* cos(sqrt(max(1 - zeta^2, 0)) * wn * t);
rise_time   = t(find(s_response >= 0.9, 1));
overshoot   = (max(s_response) - 1) * 100;
fprintf('   Rise time: %.3f s  |  Overshoot: %.2f%%\n', rise_time, overshoot);

%% 2. Sine Wave Analysis
fprintf('[2] Generating multi-frequency signal...\n');
fs = 1000; T = 1; t_sig = 0:1/fs:T-1/fs;
signal = sin(2*pi*50*t_sig) + 0.5*sin(2*pi*120*t_sig) + 0.1*randn(size(t_sig));
rms_val = rms(signal);
peak    = max(abs(signal));
fprintf('   RMS: %.4f  |  Peak: %.4f\n', rms_val, peak);

%% 3. Matrix Operations
fprintf('[3] Running matrix benchmark...\n');
A = rand(200, 200);
tic;
[U, S, V] = svd(A);
elapsed = toc;
cond_num = S(1,1) / S(end,end);
fprintf('   SVD 200x200 in %.4f s  |  Condition number: %.2f\n', elapsed, cond_num);

%% 4. Summary
fprintf('\n=== Results Summary ===\n');
fprintf('PID:    rise=%.3fs, overshoot=%.2f%%\n', rise_time, overshoot);
fprintf('Signal: rms=%.4f, peak=%.4f\n', rms_val, peak);
fprintf('Matrix: cond=%.2f, svd_time=%.4fs\n', cond_num, elapsed);
fprintf('\nMatClaw demo complete. Ready for autonomous optimization.\n');
