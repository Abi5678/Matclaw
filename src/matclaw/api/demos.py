"""
Curated MATLAB demo snippets used as fallbacks when the LLM fails to
generate code for well-known animation / visualisation requests.
"""

DEMOS: dict[str, str] = {

    "lorenz": """\
% Lorenz Attractor Animation
sigma = 10; rho = 28; beta = 8/3;
dt = 0.005; T = 30; steps = round(T/dt);
x = zeros(1,steps); y = zeros(1,steps); z = zeros(1,steps);
x(1)=0.1; y(1)=0; z(1)=0;
for i = 2:steps
    dx = sigma*(y(i-1)-x(i-1));
    dy = x(i-1)*(rho-z(i-1))-y(i-1);
    dz = x(i-1)*y(i-1)-beta*z(i-1);
    x(i)=x(i-1)+dx*dt; y(i)=y(i-1)+dy*dt; z(i)=z(i-1)+dz*dt;
end
fig = figure('Color','black');
ax = axes('Color','black','XColor','w','YColor','w','ZColor','w');
hold on; view(3); grid on;
trail = 300;
for i = trail:50:steps
    cla(ax);
    idx = max(1,i-trail):i;
    c = (1:numel(idx))'/numel(idx);
    patch(ax,[x(idx),nan],[y(idx),nan],[z(idx),nan],[c;1],...
        'EdgeColor','interp','FaceColor','none','LineWidth',1.2);
    plot3(ax,x(i),y(i),z(i),'wo','MarkerSize',4,'MarkerFaceColor','w');
    title(ax,'Lorenz Attractor','Color','w');
    xlabel(ax,'X'); ylabel(ax,'Y'); zlabel(ax,'Z');
    drawnow;
end
""",

    "mandelbrot": """\
% Mandelbrot Set
N = 512; maxIter = 128;
x = linspace(-2.5, 1, N); y = linspace(-1.2, 1.2, N);
[X,Y] = meshgrid(x,y); C = X + 1i*Y;
Z = zeros(N); M = zeros(N);
for k = 1:maxIter
    mask = abs(Z) <= 2;
    Z(mask) = Z(mask).^2 + C(mask);
    M(mask) = k;
end
figure; imagesc(x, y, M); colormap(hot); colorbar;
title('Mandelbrot Set'); axis equal tight;
""",

    "3d surface": """\
% 3-D Surface: sin(x)*cos(y)
[x,y] = meshgrid(linspace(-2*pi,2*pi,80));
z = sin(x).*cos(y);
figure; surf(x,y,z,'EdgeAlpha',0.15);
colormap(parula); colorbar;
title('3D Surface: sin(x)\\cdotcos(y)');
xlabel('x'); ylabel('y'); zlabel('z');
shading interp;
""",

    "pendulum": """\
% Double Pendulum Animation
g=9.81; L1=1; L2=1; m1=1; m2=1;
dt=0.01; T=10; N=round(T/dt);
th1=pi/2; th2=pi/3; w1=0; w2=0;
fig=figure; axis([-2.5 2.5 -2.5 0.5]); grid on; hold on;
for i=1:N
    d=th2-th1;
    den1=(m1+m2)*L1-m2*L1*cos(d)^2;
    den2=(L2/L1)*den1;
    a1=(m2*L1*w1^2*sin(d)*cos(d)+m2*g*sin(th2)*cos(d)+m2*L2*w2^2*sin(d)-( m1+m2)*g*sin(th1))/den1;
    a2=(-m2*L2*w2^2*sin(d)*cos(d)+(m1+m2)*g*sin(th1)*cos(d)-(m1+m2)*L1*w1^2*sin(d)-(m1+m2)*g*sin(th2))/den2;
    w1=w1+a1*dt; w2=w2+a2*dt; th1=th1+w1*dt; th2=th2+w2*dt;
    x1=L1*sin(th1); y1=-L1*cos(th1);
    x2=x1+L2*sin(th2); y2=y1-L2*cos(th2);
    cla;
    plot([0,x1,x2],[0,y1,y2],'b-o','LineWidth',2,'MarkerSize',8,'MarkerFaceColor','r');
    title('Double Pendulum'); drawnow;
end
""",

    "wave": """\
% Animated Standing Wave
x = linspace(0, 2*pi, 200);
fig = figure;
for t = linspace(0, 4*pi, 120)
    y = sin(x - t) .* exp(-0.05*x);
    plot(x, y, 'b-', 'LineWidth', 2);
    ylim([-1.2 1.2]); grid on;
    title(sprintf('Wave at t = %.2f', t));
    drawnow;
end
""",

    "fft": """\
% FFT Spectrum Analysis
Fs = 1000; T = 1/Fs; L = 1000; t = (0:L-1)*T;
S = 0.7*sin(2*pi*50*t) + sin(2*pi*120*t) + 0.5*randn(1,L);
Y = fft(S); P2 = abs(Y/L); P1 = P2(1:L/2+1);
P1(2:end-1) = 2*P1(2:end-1);
f = Fs*(0:(L/2))/L;
figure;
subplot(2,1,1); plot(t(1:200), S(1:200)); title('Signal'); xlabel('Time (s)'); ylabel('Amplitude');
subplot(2,1,2); plot(f, P1); title('FFT Spectrum'); xlabel('Frequency (Hz)'); ylabel('|P(f)|');
""",

    "bouncing ball": """\
% Bouncing Ball Animation
g = 9.81; h0 = 4; e = 0.8;
dt = 0.02; T = 8; N = round(T/dt);
y = h0; vy = 0;
fig = figure('Color','white');
ax = axes('XLim',[-1 1],'YLim',[0 h0+0.5]);
axis off; hold on;
ground = fill([-1 1 1 -1],[0 0 -0.05 -0.05],[0.3 0.3 0.3]);
ball = plot(0, y, 'o','MarkerSize',20,'MarkerFaceColor',[0.2 0.5 1],'MarkerEdgeColor','none');
title(sprintf('Bouncing Ball  (h_0 = %.0f m)', h0));
for i = 1:N
    vy = vy - g*dt;
    y  = y + vy*dt;
    if y <= 0
        y = 0; vy = -vy*e;
    end
    set(ball,'YData', y);
    drawnow;
end
""",

    "spring mass": """\
% Spring-Mass System Animation
m = 1; k = 10; c = 0.3;
x0 = 1; v0 = 0; dt = 0.02; T = 10; N = round(T/dt);
x = x0; v = v0;
fig = figure('Color','white');
ax = axes('XLim',[-2 2],'YLim',[-0.5 0.5]);
axis off; hold on;
wall = fill([-2 -1.5 -1.5 -2],[-0.5 -0.5 0.5 0.5],[0.4 0.4 0.4]);
spring_line = plot([-1.5 x],[0 0],'b-','LineWidth',2);
mass_patch  = fill([x-0.2 x+0.2 x+0.2 x-0.2],[-0.15 -0.15 0.15 0.15],[0.2 0.5 1]);
title('Spring-Mass System'); xlabel('Position (m)');
for i = 1:N
    a = (-k*x - c*v) / m;
    v = v + a*dt; x = x + v*dt;
    set(spring_line,'XData',[-1.5 x]);
    set(mass_patch,'XData',[x-0.2 x+0.2 x+0.2 x-0.2]);
    drawnow;
end
""",

    "projectile": """\
% Projectile Motion
g = 9.81; v0 = 20; angles = [30 45 60];
colors = {'r','b','g'}; t_max = 2*v0/g*sind(max(angles));
t = linspace(0, t_max, 300);
figure; hold on; grid on;
for i = 1:numel(angles)
    th = angles(i);
    x = v0*cosd(th).*t;
    y = v0*sind(th).*t - 0.5*g.*t.^2;
    y(y<0) = nan;
    plot(x, y, colors{i}, 'LineWidth', 2, 'DisplayName', sprintf('%d deg', th));
end
legend; xlabel('Range (m)'); ylabel('Height (m)'); title('Projectile Motion');
""",

    "fibonacci spiral": """\
% Fibonacci Spiral
n = 10; fib = [1 1];
for i = 3:n, fib(i) = fib(i-1)+fib(i-2); end
figure; hold on; axis equal off;
title('Fibonacci Spiral');
colors = turbo(n);
x0 = 0; y0 = 0;
dirs = [1 0; 0 1; -1 0; 0 -1]; d = 1;
for i = 1:n
    r = fib(i);
    t = linspace((d-1)*pi/2, d*pi/2, 50);
    cx = x0 + dirs(mod(d-1,4)+1,1)*r;
    cy = y0 + dirs(mod(d-1,4)+1,2)*r;
    px = cx + r*cos(t + (pi - d*pi/2));
    py = cy + r*sin(t + (pi - d*pi/2));
    plot(px, py, 'Color', colors(i,:), 'LineWidth', 2);
    x0 = px(end); y0 = py(end);
    d = mod(d, 4) + 1;
end
""",

    "sine wave": """\
% Sine Wave Plot
x = linspace(0, 4*pi, 500);
figure;
plot(x, sin(x), 'b-', 'LineWidth', 2); hold on;
plot(x, cos(x), 'r--', 'LineWidth', 2);
legend('sin(x)', 'cos(x)'); grid on;
xlabel('x (radians)'); ylabel('Amplitude');
title('Sine and Cosine Waves');
""",

    "histogram": """\
% Normal Distribution Histogram
data = randn(1, 5000);
figure;
histogram(data, 50, 'Normalization', 'pdf', 'FaceColor', [0.2 0.5 1], 'EdgeAlpha', 0.3);
hold on;
x = linspace(-4, 4, 200);
plot(x, normpdf(x, 0, 1), 'r-', 'LineWidth', 2);
xlabel('Value'); ylabel('Probability Density');
title('Normal Distribution (N=5000)'); grid on;
legend('Data', 'PDF');
""",

    "scatter plot": """\
% Scatter Plot with Clusters
rng(42);
n = 200;
c1 = randn(n,2) + [2 2];
c2 = randn(n,2) + [-2 2];
c3 = randn(n,2) + [0 -2];
figure; hold on; grid on;
scatter(c1(:,1), c1(:,2), 30, 'r', 'filled', 'DisplayName', 'Cluster 1');
scatter(c2(:,1), c2(:,2), 30, 'b', 'filled', 'DisplayName', 'Cluster 2');
scatter(c3(:,1), c3(:,2), 30, 'g', 'filled', 'DisplayName', 'Cluster 3');
legend; xlabel('X'); ylabel('Y'); title('Scatter Plot with 3 Clusters');
""",

    "circular orbit": """\
% Circular Orbit Animation
theta = 0; omega = 0.05; r = 1;
fig = figure('Color','black');
ax = axes('Color','black','XLim',[-1.5 1.5],'YLim',[-1.5 1.5]);
axis equal off; hold on;
plot(0,0,'yo','MarkerSize',16,'MarkerFaceColor','yellow');
trail = plot(nan,nan,'c-','LineWidth',0.8);
orb   = plot(r,0,'wo','MarkerSize',10,'MarkerFaceColor',[0.4 0.7 1]);
title('Circular Orbit','Color','w');
hist_x = []; hist_y = [];
for i = 1:600
    theta = theta + omega;
    x = r*cos(theta); y = r*sin(theta);
    hist_x(end+1) = x; hist_y(end+1) = y;
    if numel(hist_x) > 150, hist_x = hist_x(end-149:end); hist_y = hist_y(end-149:end); end
    set(trail,'XData',hist_x,'YData',hist_y);
    set(orb,'XData',x,'YData',y);
    drawnow;
end
""",

}


def find_demo(text: str) -> str | None:
    """Return hardcoded MATLAB code for well-known demo requests, or None."""
    lower = text.lower()
    if "lorenz" in lower:
        return DEMOS["lorenz"]
    if "mandelbrot" in lower:
        return DEMOS["mandelbrot"]
    if "3d surface" in lower or ("sin(x)" in lower and "cos(y)" in lower):
        return DEMOS["3d surface"]
    if "double pendulum" in lower or "pendulum" in lower:
        return DEMOS["pendulum"]
    if "standing wave" in lower or ("wave" in lower and "animat" in lower):
        return DEMOS["wave"]
    if "fft" in lower or "spectrum" in lower or "frequency" in lower:
        return DEMOS["fft"]
    if "fibonacci" in lower:
        return DEMOS["fibonacci spiral"]
    if "sine wave" in lower or "sin wave" in lower or ("sin" in lower and "cos" in lower and "plot" in lower):
        return DEMOS["sine wave"]
    if "histogram" in lower or "normal distribution" in lower or "gaussian" in lower:
        return DEMOS["histogram"]
    if "scatter" in lower and ("plot" in lower or "cluster" in lower or "point" in lower):
        return DEMOS["scatter plot"]
    if "bouncing ball" in lower or "bounce" in lower or ("ball" in lower and ("animat" in lower or "fall" in lower or "drop" in lower or "height" in lower)):
        return DEMOS["bouncing ball"]
    if "spring" in lower and ("mass" in lower or "oscil" in lower):
        return DEMOS["spring mass"]
    if "projectile" in lower or ("ball" in lower and "angle" in lower):
        return DEMOS["projectile"]
    if "orbit" in lower or "circular motion" in lower or "planet" in lower:
        return DEMOS["circular orbit"]
    return None
