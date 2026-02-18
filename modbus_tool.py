import ttkbootstrap as tb
from ttkbootstrap.constants import *
from pymodbus.client import ModbusSerialClient
import serial.tools.list_ports
import threading
import time
import struct
from tkinter.scrolledtext import ScrolledText

class ModbusTool:
    def __init__(self, root):
        self.root = root
        self.root.title("Modbus RTU Tool")
        self.client = None
        self.polling = False
        self.build_ui()

    def build_ui(self):
        frm = tb.Frame(self.root, padding=20)
        frm.grid()

        # Serial Settings
        tb.Label(frm, text="🔌 Serial Port Settings", font=("Segoe UI", 11, "bold")).grid(column=0, row=0, columnspan=3, pady=(0, 10), sticky="w")

        tb.Label(frm, text="Port").grid(column=0, row=1, sticky="w")
        tb.Button(frm, text="🔄", bootstyle="info-outline", width=3, command=self.refresh_ports).grid(column=1, row=1, sticky="e", padx=(0, 5))
        self.port_cb = tb.Combobox(frm, width=20)
        self.port_cb.grid(column=2, row=1, sticky="e")
        self.refresh_ports()

        tb.Label(frm, text="Baudrate").grid(column=0, row=2, sticky="w")
        self.baud_cb = tb.Combobox(frm, values=[1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200], width=20)
        self.baud_cb.set("19200")
        self.baud_cb.grid(column=2, row=2, sticky="e")

        tb.Label(frm, text="Parity").grid(column=0, row=3, sticky="w")
        self.parity_cb = tb.Combobox(frm, values=["N", "E", "O"], width=20)
        self.parity_cb.set("E")
        self.parity_cb.grid(column=2, row=3, sticky="e")

        tb.Label(frm, text="Stop Bits").grid(column=0, row=4, sticky="w")
        self.stop_cb = tb.Combobox(frm, values=[1, 2], width=20)
        self.stop_cb.set(1)
        self.stop_cb.grid(column=2, row=4, sticky="e")

        tb.Label(frm, text="Slave ID").grid(column=0, row=5, sticky="w")
        self.slave_id = tb.Entry(frm, width=22)
        self.slave_id.insert(0, "1")
        self.slave_id.grid(column=2, row=5, sticky="e")

        # Modbus Read Settings
        tb.Label(frm, text="📟 Modbus Read Settings", font=("Segoe UI", 11, "bold")).grid(column=0, row=6, columnspan=3, pady=(15, 10), sticky="w")

        tb.Label(frm, text="Function").grid(column=0, row=7, sticky="w")
        self.func_cb = tb.Combobox(frm, values=["03 Read Holding", "04 Read Input"], width=20)
        self.func_cb.set("03 Read Holding")
        self.func_cb.grid(column=2, row=7, sticky="e")

        tb.Label(frm, text="Start Addr").grid(column=0, row=8, sticky="w")
        self.start_addr = tb.Entry(frm, width=22)
        self.start_addr.insert(0, "1000")
        self.start_addr.grid(column=2, row=8, sticky="e")

        tb.Label(frm, text="Quantity").grid(column=0, row=9, sticky="w")
        self.quantity = tb.Entry(frm, width=22)
        self.quantity.insert(0, "2")
        self.quantity.grid(column=2, row=9, sticky="e")

        tb.Label(frm, text="Display Format").grid(column=0, row=10, sticky="w")
        self.format_cb = tb.Combobox(frm, values=["Decimal", "Hex", "Binary"], width=20)
        self.format_cb.set("Decimal")
        self.format_cb.grid(column=2, row=10, sticky="e")

        # Coil Write Section
        tb.Label(frm, text="🧲 Write Coil", font=("Segoe UI", 11, "bold")).grid(column=0, row=11, columnspan=3, pady=(15, 10), sticky="w")

        tb.Label(frm, text="Coil Addr").grid(column=0, row=12, sticky="w")
        self.coil_addr = tb.Entry(frm, width=22)
        self.coil_addr.insert(0, "0")
        self.coil_addr.grid(column=2, row=12, sticky="e")

        tb.Label(frm, text="Value").grid(column=0, row=13, sticky="w")
        self.coil_val = tb.Combobox(frm, values=["ON", "OFF"], width=20)
        self.coil_val.set("ON")
        self.coil_val.grid(column=2, row=13, sticky="e")

        tb.Button(frm, text="⚡ Write Coil", bootstyle="secondary", command=self.write_coil).grid(column=1, row=14, pady=(10, 10))

        # Control Buttons
        tb.Button(frm, text="🔗 Connect", bootstyle="success", command=self.connect).grid(column=0, row=15, pady=10)
        tb.Button(frm, text="📥 Read Once", bootstyle="primary", command=self.read_once).grid(column=1, row=15)
        tb.Button(frm, text="🔁 Start Polling", bootstyle="warning", command=self.start_polling).grid(column=0, row=16)
        tb.Button(frm, text="⏹ Stop Polling", bootstyle="danger", command=self.stop_polling).grid(column=1, row=16)

        # Output
        self.output = ScrolledText(frm, width=60, height=12, font=("Consolas", 10))
        self.output.grid(column=0, row=17, columnspan=3, pady=(15, 0))

    def refresh_ports(self):
        ports = [port.device for port in serial.tools.list_ports.comports()]
        self.port_cb['values'] = ports
        if ports:
            self.port_cb.set(ports[0])

    def connect(self):
        self.client = ModbusSerialClient(
            method='rtu',
            port=self.port_cb.get(),
            baudrate=int(self.baud_cb.get()),
            parity=self.parity_cb.get(),
            stopbits=int(self.stop_cb.get()),
            timeout=1
        )
        if self.client.connect():
            self.output.insert("end", "✅ Connected successfully\n")
        else:
            self.output.insert("end", "❌ Connection failed\n")

    def read_once(self):
        self.polling = False
        self.read_modbus()

    def start_polling(self):
        self.polling = True
        threading.Thread(target=self.poll_loop, daemon=True).start()

    def stop_polling(self):
        self.polling = False

    def poll_loop(self):
        while self.polling:
            self.read_modbus()
            time.sleep(2)

        def decode_float(self, registers):
        if len(registers) >= 2:
            raw = struct.pack('>HH', registers[0], registers[1])
            return struct.unpack('>f', raw)[0]
        return None

    def read_modbus(self):
        if not self.client:
            self.output.insert("end", "⚠️ Not connected\n")
            return

        unit = int(self.slave_id.get())
        addr = int(self.start_addr.get())
        count = int(self.quantity.get())
        func = self.func_cb.get()

        try:
            if func.startswith("03"):
                rr = self.client.read_holding_registers(addr, count, unit=unit)
            elif func.startswith("04"):
                rr = self.client.read_input_registers(addr, count, unit=unit)
            else:
                self.output.insert("end", "Unsupported function\n")
                return

            if hasattr(rr, 'registers') and rr.registers:
                regs = rr.registers
                fmt = self.format_cb.get()

                if fmt == "Decimal":
                    self.output.insert("end", f"📋 Decimal: {regs}\n")
                elif fmt == "Hex":
                    hex_vals = [hex(r) for r in regs]
                    self.output.insert("end", f"📋 Hex: {hex_vals}\n")
                elif fmt == "Binary":
                    bin_vals = [bin(r) for r in regs]
                    self.output.insert("end", f"📋 Binary: {bin_vals}\n")

                if len(regs) >= 2:
                    value = self.decode_float(regs)
                    self.output.insert("end", f"📈 Float value: {value:.3f}\n")
            else:
                self.output.insert("end", f"⚠️ No data or error: {rr}\n")

        except Exception as e:
            self.output.insert("end", f"❌ Error: {e}\n")

    def write_coil(self):
        if not self.client:
            self.output.insert("end", "⚠️ Not connected\n")
            return

        try:
            unit = int(self.slave_id.get())
            addr = int(self.coil_addr.get())
            val = self.coil_val.get().strip().upper() == "ON"

            rr = self.client.write_coil(addr, val, unit=unit)
            if rr.isError():
                self.output.insert("end", f"❌ Coil write failed: {rr}\n")
            else:
                self.output.insert("end", f"✅ Coil {addr} set to {'ON' if val else 'OFF'}\n")
        except Exception as e:
            self.output.insert("end", f"❌ Error writing coil: {e}\n")

# Run the tool with a modern theme
if __name__ == "__main__":
    app = tb.Window(themename="flatly")  # Try "darkly", "cyborg", etc.
    ModbusTool(app)
    app.mainloop()