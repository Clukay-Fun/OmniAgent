"""
Diagnose Windows socketpair issues.
"""

import socket
import sys


print(f"Python version: {sys.version}")
print(f"Platform: {sys.platform}")
print()

# Test 1: basic socket
print("[Test 1] Create basic socket...")
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    print(f"  [OK] Bound to port: {port}")
except Exception as exc:
    print(f"  [FAIL] {exc}")

# Test 2: socketpair
print("\n[Test 2] Create socketpair...")
try:
    a, b = socket.socketpair()
    a.close()
    b.close()
    print("  [OK]")
except Exception as exc:
    print(f"  [FAIL] {exc}")

# Test 3: manual socketpair
print("\n[Test 3] Manual loopback connection...")
try:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    connector = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    connector.connect(("127.0.0.1", port))

    accepted, _ = listener.accept()

    connector.send(b"hello")
    data = accepted.recv(5)

    connector.close()
    accepted.close()
    listener.close()

    if data == b"hello":
        print("  [OK] Loopback data exchange")
    else:
        print(f"  [WARN] Unexpected data: {data}")
except Exception as exc:
    print(f"  [FAIL] {exc}")

print("\n" + "=" * 50)
print("If Test 2 fails, the system socketpair is blocked.")
print("Check: proxy/VPN/antivirus/firewall settings.")
