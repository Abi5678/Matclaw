import requests
import json
import time

payload = {
    "text": "Analyze the file 'broken_pid.m' in my workspace, fix the syntax error, and then run it to show the step response plot.",
    "mode": "exec",
    "history": [],
    "session_id": "test-session-debug"
}

try:
    print("Testing /api/run (Long Horizon Debug)...")
    res = requests.post("http://localhost:8000/api/run", json=payload, timeout=600)
    print("Status:", res.status_code)
    try:
        print(json.dumps(res.json(), indent=2))
    except Exception as e:
        print("Raw output:", res.text)
except Exception as e:
    print("Error:", e)
