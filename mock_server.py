import asyncio
import json
import logging
import argparse
import os
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
import math
import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("mock_server")

# Allow setting mode via env var or it will be default
MOCK_MODE = os.environ.get("MOCK_MODE", "hub").lower()

app = FastAPI(title=f"GPU Hot Mock Server ({MOCK_MODE} mode)")

# Mount static files to serve the real frontend
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def index():
    with open("templates/index.html", "r") as f:
        return HTMLResponse(content=f.read())

@app.get("/api/version")
async def api_version():
    return JSONResponse({
        "current": "1.0.0-mock",
        "latest": "1.0.0",
        "update_available": False,
        "release_url": None
    })

@app.get("/api/gpu-data")
async def api_gpu_data():
    return {"gpus": {}, "timestamp": "mock"}

# Mock state
mock_state = {
    "nodes_online": True, # For hub mode to simulate offline/online
}

def generate_system_info(t):
    return {
        'cpu_percent': 30 + 20 * math.sin(t / 10),
        'memory_percent': 50 + 10 * math.cos(t / 15),
        'memory_total_gb': 64.0,
        'memory_used_gb': 32.0 + 6.4 * math.cos(t / 15),
        'memory_available_gb': 32.0 - 6.4 * math.cos(t / 15),
        'cpu_count': 16,
        'timestamp': datetime.now().isoformat(),
        'net_bytes_sent': int(1000000 * t),
        'net_bytes_recv': int(2000000 * t),
    }

def generate_gpu_data(gpu_id, t, base_temp):
    util = 40 + 40 * math.sin(t / 5 + int(gpu_id))
    util = max(0, min(100, util))
    mem_used = 4000 + 2000 * math.sin(t / 8 + int(gpu_id))
    return {
        'index': str(gpu_id),
        'name': f'Mock RTX 4090 - GPU {gpu_id}',
        'uuid': f'GPU-mock-uuid-{gpu_id}',
        'driver_version': '535.104.05',
        'vbios_version': '95.02.20.00.01',
        'temperature': base_temp + 20 * math.sin(t / 10 + int(gpu_id)),
        'temperature_memory': base_temp + 10 + 15 * math.sin(t / 10 + int(gpu_id)),
        'utilization': util,
        'memory_utilization': util * 0.8,
        'memory_used': mem_used,
        'memory_total': 24576,
        'memory_free': 24576 - mem_used,
        'power_draw': 100 + 200 * (util / 100),
        'power_limit': 450,
        'fan_speed': 30 + 50 * (util / 100),
        'clock_graphics': 1500 + 1000 * (util / 100),
        'clock_sm': 1500 + 1000 * (util / 100),
        'clock_memory': 10500,
        'pcie_gen': '4',
        'pcie_gen_max': '4',
        'pcie_width': '16',
        'pcie_width_max': '16',
        'compute_processes_count': 2,
        'graphics_processes_count': 0,
        'timestamp': datetime.now().isoformat(),
        '_fallback_mode': False
    }

def generate_processes(gpu_ids):
    procs = []
    for gid in gpu_ids:
        procs.append({
            'pid': f'100{gid}',
            'name': 'python_mock',
            'gpu_uuid': f'GPU-mock-uuid-{gid}',
            'gpu_id': str(gid),
            'memory': 1024.0
        })
        procs.append({
            'pid': f'101{gid}',
            'name': 'tensorboard',
            'gpu_uuid': f'GPU-mock-uuid-{gid}',
            'gpu_id': str(gid),
            'memory': 256.0
        })
    return procs

def generate_mock_data(t):
    # In hub mode, simulate node offline every 30 seconds for 5 seconds
    if MOCK_MODE == "hub":
        offline_cycle = int(t / 30) % 2
        mock_state["nodes_online"] = False if offline_cycle == 1 and (t % 30) < 5 else True

    if MOCK_MODE == "default":
        return {
            'mode': 'default',
            'node_name': 'mock-local-node',
            'gpus': {
                '0': generate_gpu_data('0', t, 45),
                '1': generate_gpu_data('1', t, 50)
            },
            'processes': generate_processes(['0', '1']),
            'system': generate_system_info(t)
        }
    else:
        # Hub mode
        nodes = {}
        
        # Node 1: Always online, 4 GPUs
        nodes['node-alpha'] = {
            'status': 'online',
            'gpus': {str(i): generate_gpu_data(str(i), t, 40) for i in range(4)},
            'processes': generate_processes([str(i) for i in range(4)]),
            'system': generate_system_info(t),
            'last_update': datetime.now().isoformat()
        }
        
        # Node 2: Flapping online/offline, 2 GPUs
        if mock_state["nodes_online"]:
            nodes['node-beta'] = {
                'status': 'online',
                'gpus': {str(i): generate_gpu_data(str(i), t, 35) for i in range(2)},
                'processes': generate_processes(['0', '1']),
                'system': generate_system_info(t + 100),
                'last_update': datetime.now().isoformat()
            }
        else:
            nodes['node-beta'] = {
                'status': 'offline',
                'gpus': {},
                'processes': [],
                'system': {},
                'last_update': datetime.now().isoformat()
            }
            
        return {
            'mode': 'hub',
            'nodes': nodes,
            'cluster_stats': {
                'total_nodes': 2,
                'online_nodes': 2 if mock_state["nodes_online"] else 1,
                'total_gpus': 6 if mock_state["nodes_online"] else 4
            }
        }

websocket_connections = set()

@app.websocket("/socket.io/")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    websocket_connections.add(websocket)
    logger.info("Mock client connected")
    
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info("Mock client disconnected")
        websocket_connections.discard(websocket)

async def broadcast_loop():
    start_time = time.time()
    while True:
        try:
            t = time.time() - start_time
            data = generate_mock_data(t)
            
            if websocket_connections:
                msg = json.dumps(data)
                disconnected = set()
                for ws in websocket_connections:
                    try:
                        await ws.send_text(msg)
                    except Exception:
                        disconnected.add(ws)
                websocket_connections.difference_update(disconnected)
                
        except Exception as e:
            logger.error(f"Error in broadcast loop: {e}")
            
        await asyncio.sleep(1.0)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(broadcast_loop())
    logger.info(f"Mock broadcast loop started in {MOCK_MODE} mode")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GPU Hot Mock Server")
    parser.add_argument("--mode", choices=["default", "hub"], default="hub", help="Mock mode (default or hub)")
    parser.add_argument("--port", type=int, default=8000, help="Port to run on")
    args = parser.parse_args()
    
    # Override env var if passed via args
    MOCK_MODE = args.mode
    
    logger.info(f"Starting mock server on http://localhost:{args.port} in {MOCK_MODE} mode")
    uvicorn.run(app, host="0.0.0.0", port=args.port)
