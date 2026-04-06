set(0, 'DefaultFigureVisible', 'off');
x = 1:10; plot(x,x);
figs = get(0, 'Children');
exportgraphics(figs(1), 'test_out.png', 'Resolution', 150);
