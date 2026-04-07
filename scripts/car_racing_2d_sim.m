%% 2D car racing demo — base MATLAB only
% Bicycle kinematics, slip, ray–segment LIDAR, corridor track (outer/inner polygon).

clear; close all; clc;
rng(42);

%% Track: closed outer / inner polygons (corridor). Units: meters.
outer = [ 0.0, -2.5;  14.0, -2.5;  18.5,  4.0;  14.0, 12.5; ...
          0.0, 12.5;  -4.5,  6.0;  0.0, -2.5 ];
inner = [ 0.9,  0.4;  13.2,  0.4;  16.0,  5.0;  13.2, 10.2; ...
           0.9, 10.2;  -2.5,  6.0;  0.9,  0.4 ];

% Wall segments for LIDAR (both boundaries)
wall_segs = [polygon_segments(outer), polygon_segments(inner)];

% Centerline = midpoints between vertices (same topology)
nV = size(outer, 1) - 1;
ctr = zeros(nV + 1, 2);
for i = 1:nV
    ctr(i, :) = 0.5 * (outer(i, :) + inner(i, :));
end
ctr(nV + 1, :) = ctr(1, :);

%% Vehicle & sim
Lwb = 2.4;                % wheelbase (m)
v0  = 8.0;                % initial speed (m/s)
v_max = 14;
delta_max = deg2rad(28);
dt = 0.04;
t_final = 28;
N = min(ceil(t_final / dt), 900);

num_rays = 17;
ray_angles = linspace(-pi / 2.2, pi / 2.2, num_rays);
lidar_range = 25;

%% State
x = 2.0; y = 1.0; th = deg2rad(5); v = v0;

traj = [x, y];
v_log = v;
t_log = 0;
rays_store = struct('x', {}, 'y', {}, 'xe', {}, 'ye', {});
sample_ix = round(linspace(1, N, 5));

k_slip = 0.035;           % slip: speed loss when steering hard
k_steer = 1.15;         % heading-tracking gain

%% Main loop
off_track = false;
for k = 1:N
    % --- Corridor test (no toolboxes beyond inpolygon)
    in_o = inpolygon(x, y, outer(:, 1), outer(:, 2));
    in_i = inpolygon(x, y, inner(:, 1), inner(:, 2));
    on_track = in_o && ~in_i;
    if ~on_track
        off_track = true;
        break;
    end

    % --- Simple path follower: desired heading from centerline tangent near closest point
    [th_des, ~] = lookahead_heading(ctr, x, y, 4.0);
    e_th = atan2(sin(th_des - th), cos(th_des - th));
    delta = k_steer * e_th;
    delta = max(-delta_max, min(delta_max, delta));

    % --- Bicycle + slip
    beta = atan(tan(delta) * 0.08); % tiny fictitious sideslip for “slip” display
    vx = v * cos(th + beta);
    vy = v * sin(th + beta);
    w = v / Lwb * tan(delta);

    x  = x  + vx * dt;
    y  = y  + vy * dt;
    th = th + w * dt;
    v  = v  - k_slip * abs(delta) * v * dt;
    v  = max(2.0, min(v_max, v));

    traj(end + 1, :) = [x, y]; %#ok<SAGROW>
    v_log(end + 1) = v; %#ok<SAGROW>
    t_log(end + 1) = k * dt; %#ok<SAGROW>

    if ismember(k, sample_ix)
        [rx, ry] = lidar_endpoints(x, y, th, ray_angles, wall_segs, lidar_range);
        rays_store(end + 1).x = x; %#ok<SAGROW>
        rays_store(end).y = y;
        rays_store(end).xe = rx;
        rays_store(end).ye = ry;
    end
end

%% Figure
fig = figure('Color', 'w', 'Position', [80 80 980 720]);

subplot(2, 1, 1);
hold on;
plot(outer(:, 1), outer(:, 2), 'k-', 'LineWidth', 2);
plot(inner(:, 1), inner(:, 2), 'k-', 'LineWidth', 2);
plot(traj(:, 1), traj(:, 2), 'b-', 'LineWidth', 1.4);
scatter(traj(1, 1), traj(1, 2), 40, 'g', 'filled');
scatter(traj(end, 1), traj(end, 2), 40, 'r', 'filled');

cols = lines(numel(rays_store));
for s = 1:numel(rays_store)
    xc = rays_store(s).x; yc = rays_store(s).y;
    for r = 1:numel(ray_angles)
        plot([xc, rays_store(s).xe(r)], [yc, rays_store(s).ye(r)], ...
            '-', 'Color', cols(s, :), 'LineWidth', 0.6);
    end
end
axis equal tight;
grid on;
xlabel('x (m)');
ylabel('y (m)');
title('Track, path, LIDAR rays (sampled times)');
legend({'Outer wall', 'Inner wall', 'Path', 'Start', 'End'}, 'Location', 'eastoutside');

subplot(2, 1, 2);
plot(t_log, v_log, 'k-', 'LineWidth', 1.2);
grid on;
xlabel('Time (s)');
ylabel('Speed (m/s)');
title('Speed profile');
if off_track
    sgtitle(sprintf('Off track at t = %.2f s — shorten sim or tune controller', t_log(end)));
else
    sgtitle('2D racing demo — bicycle model + ray LIDAR (base MATLAB)');
end

% Figure is captured by MatClaw (batch/engine wrappers). For bare MATLAB CLI:
%   saveas(fig, 'car_racing_2d_sim.png', 'png');

%% --- local functions -------------------------------------------------

function S = polygon_segments(P)
    % P: (n+1)x2 with P(end,:) == P(1,:)
    m = size(P, 1) - 1;
    S = zeros(4, m);
    for i = 1:m
        S(1:2, i) = P(i  , :)';
        S(3:4, i) = P(i+1, :)';
    end
end

function t_hit = ray_hit_seg(O, rd, A, B)
    v = (B(:) - A(:));
    AO = A(:) - O(:);
    num_t = AO(1) * v(2) - AO(2) * v(1);
    num_u = AO(1) * rd(2) - AO(2) * rd(1);
    den = rd(1) * v(2) - rd(2) * v(1);
    if abs(den) < 1e-14
        t_hit = inf;
        return;
    end
    t = num_t / den;
    u = num_u / den;
    if (t >= 0) && (u >= 0) && (u <= 1)
        t_hit = t;
    else
        t_hit = inf;
    end
end

function [xe, ye] = lidar_endpoints(xc, yc, th, ray_angles, wall_segs, rng)
    nr = numel(ray_angles);
    xe = zeros(1, nr);
    ye = zeros(1, nr);
    O = [xc; yc];
    for r = 1:nr
        th_ray = th + ray_angles(r);
        rd = [cos(th_ray); sin(th_ray)];
        tmin = rng;
        for k = 1:size(wall_segs, 2)
            A = wall_segs(1:2, k);
            B = wall_segs(3:4, k);
            t = ray_hit_seg(O, rd, A, B);
            if t < tmin
                tmin = t;
            end
        end
        xe(r) = xc + tmin * rd(1);
        ye(r) = yc + tmin * rd(2);
    end
end

function [th_des, iclose] = lookahead_heading(ctr, x, y, L_ahead)
    % Closest vertex then step forward a few segments for tangent
    d2 = sum((ctr(1:end-1, :) - [x, y]) .^ 2, 2);
    [~, ic] = min(d2);
    acc = 0;
    i2 = ic;
    while acc < L_ahead && i2 < size(ctr, 1) - 1
        seg = ctr(i2 + 1, :) - ctr(i2, :);
        acc = acc + hypot(seg(1), seg(2));
        i2 = i2 + 1;
    end
    if i2 >= size(ctr, 1)
        i2 = size(ctr, 1) - 1;
    end
    tang = ctr(i2 + 1, :) - ctr(i2, :);
    th_des = atan2(tang(2), tang(1));
    iclose = ic;
end
