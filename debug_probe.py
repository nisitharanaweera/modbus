"""Quick diagnostic: probe slave 1 at 9600/8N1 and show exactly what pymodbus returns."""
import serial.tools.list_ports
from pymodbus.client import ModbusSerialClient

# Show available ports
ports = [p.device for p in serial.tools.list_ports.comports()]
print("Available ports:", ports)

if not ports:
    print("No serial ports found!")
    exit(1)

port = "COM6"
print(f"\nUsing port: {port}")
print(f"pymodbus version: ", end="")
import pymodbus
print(pymodbus.__version__)

client = ModbusSerialClient(
    port=port,
    baudrate=9600,
    parity="N",
    stopbits=1,
    bytesize=8,
    timeout=1.0,
)

print(f"\nConnecting... ", end="")
ok = client.connect()
print(f"connected={ok}")

if ok:
    import time
    time.sleep(0.1)

    # Try multiple function codes
    for fc_name, method in [
        ("FC3 read_holding_registers", lambda: client.read_holding_registers(0, count=1, device_id=1)),
        ("FC4 read_input_registers",   lambda: client.read_input_registers(0, count=1, device_id=1)),
        ("FC1 read_coils",             lambda: client.read_coils(0, count=1, device_id=1)),
    ]:
        print(f"\n--- {fc_name} ---")
        try:
            rr = method()
            print(f"  type(rr)    = {type(rr).__name__}")
            print(f"  repr(rr)    = {rr!r}")
            print(f"  isError()   = {rr.isError()}")
            if hasattr(rr, 'registers'):
                print(f"  registers   = {rr.registers}")
            if hasattr(rr, 'bits'):
                print(f"  bits        = {rr.bits}")
            if hasattr(rr, 'exception_code'):
                print(f"  exc_code    = {rr.exception_code}")
            if hasattr(rr, 'function_code'):
                print(f"  func_code   = {rr.function_code}")
        except Exception as e:
            print(f"  EXCEPTION: {type(e).__name__}: {e}")

    client.close()

print("\nDone.")
