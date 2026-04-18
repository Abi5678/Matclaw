% WEATHER_HEATMAP_OPENMETEO_APR_MAY  Example for MatClaw agentic runs.
% Fetches Apr–May daily max temperature (Open-Meteo archive) and draws a heatmap.
% NYC default; edit lat/lon/year if needed.
%
% Requires: webread allowed by guardrail (HTTPS read-only).

lat = 40.7128;
lon = -74.0060;
year = 2024;
startDate = sprintf('%d-04-01', year);
endDate = sprintf('%d-05-31', year);
base = 'https://archive-api.open-meteo.com/v1/archive';
% timezone must be URL-encoded (e.g. America/New_York -> America%2FNew_York)
q = sprintf( ...
    '%s?latitude=%.4f&longitude=%.4f&start_date=%s&end_date=%s&daily=temperature_2m_max&timezone=America%%2FNew_York', ...
    base, lat, lon, startDate, endDate );

opts = weboptions('ContentType', 'json', 'Timeout', 60);
data = webread(q, opts);

times = data.daily.time;
temps = data.daily.temperature_2m_max;
if iscell(times)
    n = numel(times);
else
    n = length(times);
end

Z = nan(2, 31);
for k = 1:n
    if iscell(times)
        tk = times{k};
    else
        tk = times(k);
    end
    if isstring(tk) || ischar(tk)
        tk = char(tk);
    end
    dk = datetime(tk, 'InputFormat', 'yyyy-MM-dd');
    m = month(dk);
    d = day(dk);
    v = temps(k);
    if isnumeric(v) && m == 4 && d >= 1 && d <= 31
        Z(1, d) = v;
    elseif isnumeric(v) && m == 5 && d >= 1 && d <= 31
        Z(2, d) = v;
    end
end

figure('Color', 'w', 'Position', [100 100 900 400]);
h = heatmap(1:31, {'April', 'May'}, Z);
h.Title = sprintf('Daily max temperature (°C) — Apr–May %d — Open-Meteo', year);
h.XLabel = 'Day of month';
h.YLabel = 'Month';
h.Colormap = parula;
h.MissingDataColor = [0.85 0.85 0.85];
h.GridVisible = 'on';
drawnow;

fprintf('OK: heatmap from Open-Meteo %s .. %s\n', startDate, endDate);
