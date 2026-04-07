function broken_pid()
    % This script has a deliberate syntax error: missing ']'
    G = tf(1, [1 2 1); 
    C = pid(1, 1, 0);
    CL = feedback(G*C, 1);
    step(CL);
end
