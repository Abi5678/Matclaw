import requests
import json
import time

payload = {
    "text": "Simulate a bouncing ball with gravity g=9.81 and restitution 0.8 for 5 seconds and plot it.",
    "mode": "exec",
    "session_id": "final-test-session"
}

try:
    print("Triggering Bouncing Ball Simulation...")
    res = requests.post("http://localhost:8000/api/run", json=payload, timeout=600)
    print("Status:", res.status_code)
    data = res.json()
    print("Response Plots:", data.get("plots", []))
    if data.get("plots"):
        print("SUCCESS: Plot generated!")
except Exception as e:
    print("Error:", e)
