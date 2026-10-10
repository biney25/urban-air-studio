"""Own the API, two workers and frontend; Ctrl+C stops them together."""
from pathlib import Path
import os
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    python = ROOT / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')
    vite = ROOT / 'web/node_modules/vite/bin/vite.js'
    if not python.exists() or not vite.exists():
        sys.exit('Run the setup instructions in README.md first.')
    subprocess.run(['node', str(ROOT / 'web/scripts/cesium-assets.mjs')], cwd=ROOT / 'web', check=True)
    for port in (8000, 5173):
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', port)) == 0:
                sys.exit(f'Port {port} is already used. Stop the previous local service first.')
    log_dir = ROOT / '.urban-air/dev'
    log_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'UA_MODE': 'local', 'PYTHONUNBUFFERED': '1'}
    processes, logs = [], []
    commands = [
        ('api', [str(python), '-m', 'uvicorn', 'api.main:app', '--host', '127.0.0.1', '--port', '8000']),
        ('worker', [str(python), '-m', 'api.worker']),
        ('detailed-worker', [str(python), '-m', 'api.detailed_execution']),
        ('web', ['node', str(vite), '--host', '127.0.0.1', '--port', '5173', '--strictPort']),
    ]
    try:
        for name, command in commands:
            log = (log_dir / f'{name}.log').open('w', encoding='utf-8')
            logs.append(log)
            process = subprocess.Popen(command, cwd=ROOT / 'web' if name == 'web' else ROOT,
                env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            processes.append((name, process))
        print('Urban Air Studio: http://127.0.0.1:5173', flush=True)
        print('Local research preview. Logs: .urban-air/dev. Ctrl+C stops all services.', flush=True)
        while True:
            for name, process in processes:
                if process.poll() is not None:
                    raise RuntimeError(f'{name} exited ({process.returncode}); inspect {log_dir / (name + ".log")}')
            time.sleep(0.5)
    except KeyboardInterrupt:
        print('Stopping local services.', flush=True)
    finally:
        for _, process in processes:
            if process.poll() is None:
                process.terminate()
        for _, process in processes:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for log in logs:
            log.close()


if __name__ == '__main__':
    main()

