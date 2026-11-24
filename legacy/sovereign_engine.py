#!/usr/bin/env python3
"""
Sovereign Engine 2 — Main entry point
A cleaned, unified, persistent multi-node sovereignty simulation.
"""

import argparse
import json
import math
import os
import random
import socket
import sys
import threading
import time
from typing import Dict

from audit_manager import AuditManager
from resonance import ResonanceSensor
from visualize import generate_svg


# ---------------------------------------------------------------------------
# Node Agent
# ---------------------------------------------------------------------------
class NodeAgent:
    def __init__(self, master_host: str, master_port: int, node_id: int):
        self.master_host = master_host
        self.master_port = master_port
        self.node_id = node_id
        # Inhale a share of historical sovereignty
        recovered = AuditManager.get_last_total()
        self.sov = recovered / 5.0   # assume ~5 nodes historically
        self.t = 0

    def run(self):
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client.connect((self.master_host, self.master_port))
        except ConnectionRefusedError:
            print(f"[Node {self.node_id}] Master offline — cannot synchronize.")
            return

        try:
            while True:
                data = client.recv(1024).decode()
                if not data:
                    break
                config = json.loads(data)

                if config.get("command") == "shutdown":
                    break

                r = float(config.get("target_r", 0.03))

                # Inductive engine + stealth jitter
                jitter_r = r * random.uniform(0.97, 1.03)
                energy = 100.0 * math.exp(jitter_r * self.t)
                resistance = 0.0005 * (energy ** 1.5)
                infusion = 0.1 * random.uniform(0.88, 1.12)
                gain = infusion - (resistance * 0.01)

                if gain <= 0:
                    self.t = max(0, self.t - random.randint(2, 6))
                    time.sleep(random.uniform(0.9, 2.2))
                else:
                    self.sov += gain
                    self.t += 1

                stats = json.dumps({"sov": self.sov, "energy": energy})
                client.sendall(stats.encode())
                time.sleep(random.uniform(0.7, 1.15))
        except (ConnectionResetError, BrokenPipeError, json.JSONDecodeError):
            pass
        finally:
            client.close()


# ---------------------------------------------------------------------------
# Sovereign Master
# ---------------------------------------------------------------------------
class SovereignMaster:
    def __init__(self, host: str = "127.0.0.1", port: int = 9999, demo: bool = False):
        self.host = host
        self.port = port
        self.demo = demo
        self.node_data: Dict[str, dict] = {}
        self.node_connections: Dict[str, socket.socket] = {}
        self.is_throttled = False
        self.resonance_score = 50.0
        self.resonance_status = "STABILIZING"
        self.sensor = ResonanceSensor()
        self.last_recorded_sov = AuditManager.get_last_total()
        self._running = True
        self._lock = threading.Lock()

        print(f"[*] Vault initialized. Last known Sovereignty: {self.last_recorded_sov:.2f}")

    def handle_node(self, conn: socket.socket, addr):
        node_id = f"{addr[0]}:{addr[1]}"
        with self._lock:
            self.node_data[node_id] = {"sov": 0.0, "energy": 0.0}
            self.node_connections[node_id] = conn
        print(f"[+] Node linked: {node_id}")

        try:
            while self._running:
                with self._lock:
                    total_energy = sum(d.get("energy", 0) for d in self.node_data.values())

                if self.is_throttled:
                    target_r = 0.002
                else:
                    target_r = 0.045

                if self.resonance_score > 75:
                    target_r *= 1.15
                elif self.resonance_score < 35:
                    target_r *= 0.6

                try:
                    conn.sendall(json.dumps({"target_r": target_r}).encode())
                    data = conn.recv(1024).decode()
                    if not data:
                        break
                    with self._lock:
                        self.node_data[node_id] = json.loads(data)
                except (ConnectionResetError, BrokenPipeError, json.JSONDecodeError):
                    break
        finally:
            print(f"[-] Node delinked: {node_id}")
            with self._lock:
                self.node_data.pop(node_id, None)
                self.node_connections.pop(node_id, None)
            try:
                conn.close()
            except Exception:
                pass

    def broadcast(self, cmd: str):
        with self._lock:
            for conn in list(self.node_connections.values()):
                try:
                    conn.sendall(json.dumps({"command": cmd}).encode())
                except Exception:
                    pass

    def dashboard(self):
        last_watchdog = 0.0
        last_resonance = 0.0
        last_save = time.time()
        last_viz = 0.0
        persist_interval = 8 if self.demo else 3600  # seconds

        while self._running:
            now = time.time()

            # FIX: snapshot both node_data AND last_recorded_sov under the
            # same lock acquisition so they're consistent with each other.
            # Previously last_recorded_sov was read outside the lock, leaving
            # a window where a concurrent write from run()'s finally block
            # could produce a stale comparison at the block-sealing trigger.
            with self._lock:
                nodes = dict(self.node_data)
                last_recorded_sov = self.last_recorded_sov

            if nodes:
                session_sov = sum(d.get("sov", 0) for d in nodes.values())
                # Prefer the higher of session or historical baseline
                total_sov = max(session_sov, last_recorded_sov + (session_sov * 0.15))
                total_energy = sum(d.get("energy", 0) for d in nodes.values())

                # Periodic exhalation
                if now - last_save > persist_interval:
                    AuditManager.save_state(total_sov, total_energy)
                    last_save = now
                    if self.demo:
                        print("[*] Demo persistence written.")

                # Resonance every ~8 s
                if now - last_resonance > 8:
                    self.resonance_score, self.resonance_status = self.sensor.analyze()
                    last_resonance = now

                # Watchdog every 30–60 s
                if now - last_watchdog > (20 if self.demo else 60):
                    status = AuditManager.check_environmental_safety()
                    if status == "HIBERNATE":
                        self.broadcast("shutdown")
                        AuditManager.seal_sovereign_block()
                        print("[🔱] HIBERNATION COMPLETE. ENGINE DORMANT.")
                        self._running = False
                        os._exit(0)
                    self.is_throttled = status == "THROTTLE"
                    last_watchdog = now

                # Block sealing triggers — use the local snapshot for the
                # sov_jump comparison, then write the update under the lock.
                entry_count = AuditManager.get_entry_count()
                sov_jump = total_sov - last_recorded_sov
                if entry_count >= (40 if self.demo else 1000) or sov_jump >= (200 if self.demo else 1000):
                    AuditManager.seal_sovereign_block()
                    with self._lock:
                        self.last_recorded_sov = total_sov

                # Occasional SVG snapshot
                if now - last_viz > (25 if self.demo else 120):
                    try:
                        generate_svg()
                    except Exception:
                        pass
                    last_viz = now

                # Live dashboard
                remaining = (
                    max(0, int(persist_interval - (now - last_save)))
                    if self.demo
                    else max(0, int((persist_interval - (now - last_save)) / 60))
                )
                print("\n" + "=" * 42)
                print(f" GLOBAL SOVEREIGNTY: {total_sov:10.2f}")
                print(f" SYSTEM ENERGY:     {total_energy:10.2f}")
                print(f" ACTIVE NODES:      {len(nodes):10d}")
                print(f" RESONANCE:         {self.resonance_score:6.1f}%  [{self.resonance_status}]")
                print(f" LEDGER SIZE:       {entry_count:4d} / {'40' if self.demo else '1000'}")
                if self.is_throttled:
                    print(" [!] SYSTEM THROTTLED — hardware watchdog")
                print(f" NEXT PERSISTENCE:  {remaining:4d} {'sec' if self.demo else 'min'}")
                print("=" * 42)
            else:
                print(f"[IDLE] Searching ledger… (Last: {last_recorded_sov:.2f})", end="\r")

            time.sleep(1.1 if self.demo else 1.0)

    def run(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(12)
        server.settimeout(1.0)

        threading.Thread(target=self.dashboard, daemon=True).start()
        print(f"[*] Sovereign Master listening on {self.host}:{self.port}")

        try:
            while self._running:
                try:
                    conn, addr = server.accept()
                    t = threading.Thread(target=self.handle_node, args=(conn, addr), daemon=True)
                    t.start()
                except socket.timeout:
                    continue
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            self.broadcast("shutdown")
            # Final exhalation — read node_data under the lock for a clean snapshot
            with self._lock:
                if self.node_data:
                    total_sov = sum(d.get("sov", 0) for d in self.node_data.values())
                    total_energy = sum(d.get("energy", 0) for d in self.node_data.values())
                    AuditManager.save_state(total_sov, total_energy)
            AuditManager.seal_sovereign_block()
            try:
                generate_svg()
            except Exception:
                pass
            server.close()
            print("\n[STATUS] Sovereign Engine 2 dormant. State sealed.")


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------
def is_port_ready(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def main():
    parser = argparse.ArgumentParser(description="Sovereign Engine 2")
    parser.add_argument("--nodes", type=int, default=5, help="Number of node agents (default 5)")
    parser.add_argument("--demo", action="store_true", help="Accelerated demo mode")
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args()

    # Clear any stale listener
    if os.name == "posix":
        os.system(f"fuser -k {args.port}/tcp >/dev/null 2>&1")
    time.sleep(0.6)

    print("--- INITIATING SOVEREIGN ENGINE 2 ---")
    print("[CLEANUP] Port cleared.")

    master = SovereignMaster(port=args.port, demo=args.demo)
    master_thread = threading.Thread(target=master.run, daemon=False)
    master_thread.start()

    # Wait for master to bind
    for _ in range(12):
        if is_port_ready(args.port):
            print("[READY] Master is broadcasting.")
            break
        time.sleep(0.4)
    else:
        print("[FATAL] Master failed to bind.")
        sys.exit(1)

    # Deploy nodes
    print(f"[INIT] Deploying {args.nodes} Sovereign Nodes…")
    node_threads = []
    for i in range(args.nodes):
        n = NodeAgent("127.0.0.1", args.port, i)
        t = threading.Thread(target=n.run, daemon=True)
        t.start()
        node_threads.append(t)
        time.sleep(0.25)

    print("--- ENGINE ONLINE | AGGREGATING NETWORK DATA ---\n")

    try:
        master_thread.join()
    except KeyboardInterrupt:
        print("\n[STOP] Neutralizing Chaos Star…")
        master._running = False
        master.broadcast("shutdown")
        time.sleep(1.2)
        print("[STATUS] Sovereign Engine 2 dormant.")


if __name__ == "__main__":
    main()
