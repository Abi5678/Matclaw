% CLIMATE_SPIRAL_GISS_GISTEMP  Hawkins-style climate spiral in MATLAB for MatClaw.
%
% Inspired by Ed Hawkins' global temperature spiral (MathWorks blog overview:
% https://blogs.mathworks.com/headlines/2017/06/01/data-visualization-shows-the-global-temperature-change-since-1850/ ).
%
% Data: NASA GISS GISTEMP v4 global land-ocean index (monthly), 0.01 degC vs
% 1951-1980. Re-baselined to the 1880-1900 mean (GISS table starts 1880).
%
% Run via MatClaw run_matlab. Uses drawnow in a loop so the API can assemble a GIF.

dataUrl = 'https://data.giss.nasa.gov/gistemp/tabledata_v4/GLB.Ts+dSST.txt';
opts = weboptions('ContentType', 'text', 'Timeout', 90);
txt = webread(dataUrl, opts);
lines = splitlines(txt);

years = [];
months = [];
anomC = [];
for i = 1:numel(lines)
    ln = strtrim(lines(i));
    if strlength(ln) < 10
        continue
    end
    if startsWith(ln, 'Year') || contains(ln, 'GLOBAL Land-Ocean')
        continue
    end
    toks = regexp(ln, '\S+', 'match');
    if numel(toks) < 14
        continue
    end
    yr = sscanf(toks{1}, '%d');
    if isempty(yr) || yr < 1880 || yr > 2100
        continue
    end
    for m = 1:12
        v = toks{m + 1};
        if strcmp(v, '***') || strcmp(v, '****')
            continue
        end
        tv = sscanf(v, '%d');
        if isempty(tv)
            continue
        end
        years(end + 1, 1) = yr; %#ok<AGROW>
        months(end + 1, 1) = m; %#ok<AGROW>
        anomC(end + 1, 1) = double(tv) / 100.0; %#ok<AGROW>
    end
end

if numel(anomC) < 24
    error('MatClaw:ClimateSpiral:Parse', 'Too few GISS points parsed.');
end

base = years >= 1880 & years <= 1900;
mu0 = mean(anomC(base));
a = anomC - mu0;

N = numel(a);
t = (1:N)';
theta = 2 * pi * (t - 1) / 12;
r0 = 1.15;
gain = 0.11;
r = r0 + gain * a;
[x, y] = pol2cart(theta, r);
cseq = (1:N)' / N;

step = max(3, floor(N / 220));
% Figure visibility is controlled by MatClaw (DefaultFigureVisible); do not force off here.
fig = figure('Color', 'w', 'Position', [80 80 720 720]);
ax = axes(fig, 'Color', 'w');
colormap(ax, parula);
thc = linspace(0, 2 * pi, 360);
ttl = sprintf(['Climate spiral (Hawkins-style) — GISS GISTEMP v4 global\n', ...
    'Monthly anomalies vs 1880-1900 mean; ~%.2f deg radial per 1 degC'], 1 / gain);

for k = 12:step:N
    cla(ax);
    hold(ax, 'on');
    for Tc = [1.0 1.5 2.0]
        rr = r0 + gain * Tc;
        plot(ax, rr * cos(thc), rr * sin(thc), 'Color', [0.85 0.85 0.85], 'LineStyle', '--', 'LineWidth', 0.7);
    end
    scatter(ax, x(1:k), y(1:k), 14, cseq(1:k), 'filled', 'MarkerEdgeColor', 'none');
    axis(ax, 'equal');
    xlim(ax, [-3.2 3.2]);
    ylim(ax, [-3.2 3.2]);
    title(ax, {ttl, sprintf('Through %d-%02d', years(k), months(k))}, 'FontSize', 11);
    xlabel(ax, 'NASA GISS tabledata_v4/GLB.Ts+dSST.txt (webread)', 'FontSize', 8, 'Interpreter', 'none');
    drawnow;
end

fprintf('OK: climate spiral; last %d-%02d; N=%d monthly points\n', years(end), months(end), N);
