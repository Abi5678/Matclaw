import json
import os
import sqlite3
import uuid
from pathlib import Path

# 1. Update Agents Database (JSON)
agents_db = "src/matclaw/agents/agents_db.json"
agents = []
if os.path.exists(agents_db):
    with open(agents_db, "r") as f:
        agents = json.load(f)

new_agents = [
    {
        "id": "drone-sim-agent",
        "name": "Drone Simulator",
        "system_prompt": "You are a MATLAB aerospace expert. Your primary task is to simulate quadcopter drone flight. Output raw trajectories as CSV-style text. Keep the simulation under 10 seconds. Focus strictly on returning X,Y,Z positions over time.",
        "trigger_condition": "When the mission requires generating flight data.",
        "callable_by_others": True,
        "allowed_tools": ["run_matlab"]
    },
    {
        "id": "data-analyst-agent",
        "name": "Data Analyst",
        "system_prompt": "You are a numerical methods expert. Your task is to process upstream flight trajectories. Calculate instantaneous velocities and identify the peak velocity and total distance flown. Summarize findings precisely.",
        "trigger_condition": "When the mission requires quantifying flight performance.",
        "callable_by_others": True,
        "allowed_tools": ["run_matlab", "python_run"]
    },
    {
        "id": "viz-specialist-agent",
        "name": "Viz Specialist",
        "system_prompt": "You are a MATLAB visualization expert. Your task is to create high-quality 3D plots. Use the provided trajectory data AND the specific metrics found by the analyst to annotate the plot. Use view(45,30) and high-contrast colors.",
        "trigger_condition": "When the mission requires professional reporting and plots.",
        "callable_by_others": True,
        "allowed_tools": ["run_matlab"]
    }
]

for new_a in new_agents:
    found = False
    for i, a in enumerate(agents):
        if a["id"] == new_a["id"]:
            agents[i] = new_a
            found = True
            break
    if not found:
        agents.append(new_a)

with open(agents_db, "w") as f:
    json.dump(agents, f, indent=4)
print("Added 3 Drone Swarm Agents.")

# 2. Update Pipelines Database (SQLite)
pipeline_id = "drone-post-processor-pipeline"
nodes = [
    {
        "id": "n1",
        "label": "Flight Simulator",
        "agent_id": "drone-sim-agent",
        "position": {"x": 100, "y": 150},
        "config": {
            "task_description": "Simulate a Quadcopter flying in a wide 3D loop for 10 seconds. Return the raw trajectory.",
            "tool": "run_matlab",
            "code": ""
        }
    },
    {
        "id": "n2",
        "label": "Performance Analyst",
        "agent_id": "data-analyst-agent",
        "position": {"x": 400, "y": 150},
        "config": {
            "task_description": "Analyze the trajectory. Find the Max Velocity and the Total Distance traveled.",
            "tool": "run_matlab",
            "code": ""
        }
    },
    {
        "id": "n3",
        "label": "Data Visualizer",
        "agent_id": "viz-specialist-agent",
        "position": {"x": 700, "y": 150},
        "config": {
            "task_description": "Create a 3D plot of the flight path. Add annotations pointing out the specific Max Velocity and Distance calculated upstream.",
            "tool": "run_matlab",
            "code": ""
        }
    }
]

edges = [
    {"id": "e1-2", "source": "n1", "target": "n2", "source_handle": "data", "target_handle": "data"},
    {"id": "e1-3", "source": "n1", "target": "n3", "source_handle": "data", "target_handle": "data"},
    {"id": "e2-3", "source": "n2", "target": "n3", "source_handle": "data", "target_handle": "data"}
]

db_path = ".matclaw_pipelines.sqlite3"
conn = sqlite3.connect(db_path)
c = conn.cursor()

import time
now = int(time.time() * 1000)

nodes_json = json.dumps(nodes)
edges_json = json.dumps(edges)

c.execute("INSERT OR REPLACE INTO pipelines (id, name, description, nodes_json, edges_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
          (pipeline_id, "Drone Post-Processor", "A 3-stage autonomous pipeline to simulate, analyze, and visualize drone flights.", nodes_json, edges_json, now, now))
conn.commit()
conn.close()

print("Drone pipeline injected into database.")
