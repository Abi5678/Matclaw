import matlab.engine
sessions = matlab.engine.find_matlab()
print(f"Available MATLAB sessions: {sessions}")
