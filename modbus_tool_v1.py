import tkinter as tk
from tkinter import ttk, scrolledtext
from pymodbus.client import ModbusSerialClient
import serial.tools.list_ports
import threading
import time
import struct

class ModbusTool:
    def __init__(self, root):
        self.root = root
        self.root.title("Modbus RTU Tool")
        self.client = None
        self.polling = False
        self.build_ui()

    def build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        # General widget styles
        style.configure("TLabel", font=("Segoe UI", 10))
        style.configure("TEntry", font=("Segoe UI", 10))
        style.configure("TCombobox", font=("Segoe UI", 10))

        # Rounded button style with modern teal color
        style.configure("Rounded.TButton",
            font=("Segoe UI", 10, "bold"),
            foreground="white",
            background="#2C7A7B",
            padding=6,
            relief="flat",
            borderwidth=0
        )
        style.map("Rounded.TButton",
            background=[("active", "#319795"), ("disabled", "#A9A9A9")],
            relief=[("pressed", "flat"), ("!pressed", "flat")]
        )

        frm = ttk.Frame(self.root, padding=20)
        frm.grid()

        # Serial Settings
        ttk.Label(frm, text="🔌 Serial Port Settings", font=("Segoe UI", 11, "bold")).grid(column=0, row=0, columnspan=3, pady=(0, 10), sticky="w")

        ttk.Label(frm, text="Port").grid(column=0, row=1, sticky="w")
        ttk.Button(frm, text="🔄", style="Rounded.TButton", width=3, command=self.refresh_ports).grid(column=1, row=1, sticky="e", padx=(0, 5))
        self.port_cb = ttk.Combobox(frm, width=20)
        self.port_cb.grid(column=2, row=1, sticky="e")
        self.refresh_ports()

        ttk.Label(frm, text="Baudrate").grid(column=0, row=2, sticky="w")
        self.baud_cb = ttk.Combobox(frm, values=[1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200], width=20)
        self.baud_cb.set("19200")
        self.baud_cb.grid(column=2, row=2, sticky="e")

        ttk.Label(frm, text="Parity").grid(column=0, row=3, sticky="w")
        self.parity_cb = ttk.Combobox(frm, values=["N", "E", "O"], width=20)
        self.parity_cb.set("E")
        self.parity_cb.grid(column=2, row=3, sticky="e")

        ttk.Label(frm, text="Stop Bits").grid(column=0, row=4, sticky="w")
        self.stop_cb = ttk.Combobox(frm, values=[1, 2], width=20)
        self.stop_cb.set(1)
        self.stop_cb.grid(column=2, row=4, sticky="e")

        ttk.Label(frm, text="Slave ID").grid(column=0, row=5, sticky="w")
        self.slave_id = tk.Entry(frm, width=22)
        self.slave_id.insert(0, "1")
        self.slave_id.grid(column=2, row=5, sticky="e")

        # Modbus Settings
        ttk.Label(frm, text="📟 Modbus Settings", font=("Segoe UI", 11, "bold")).grid(column=0, row=6, columnspan=3, pady=(15, 10), sticky="w")

        ttk.Label(frm, text="Function").grid(column=0, row=7, sticky="w")
        self.func_cb = ttk.Combobox(frm, values=["03 Read Holding", "04 Read Input"], width=20)
        self.func_cb.set("03 Read Holding")
        self.func_cb.grid(column=2, row=7, sticky="e")

        ttk.Label(frm, text="Start Addr").grid(column=0, row=8, sticky="w")
        self.start_addr = tk.Entry(frm, width=22)
        self.start_addr.insert(0, "1000")
        self.start_addr.grid(column=2, row=8, sticky="e")

        ttk.Label(frm, text="Quantity").grid(column=0, row=9, sticky="w")
        self.quantity = tk.Entry(frm, width=22)
        self.quantity.insert(0, "2")
        self.quantity.grid(column=2, row=9, sticky="e")

        # Buttons
        ttk.Button(frm, text="🔗 Connect", style="Rounded.TButton", command=self.connect).grid(column=0, row=10, pady=10)
        ttk.Button(frm, text="📥 Read Once", style="Rounded.TButton", command=self.read_once).grid(column=1, row=10)
        ttk.Button(frm, text="🔁 Start Polling", style="Rounded.TButton", command=self.start_polling).grid(column=0, row=11)
        ttk.Button(frm, text="⏹ Stop Polling", style="Rounded.TButton", command=self.stop_polling).grid(column=1, row=11)

        # Output
        self.output = scrolledtext.ScrolledText(frm, width=60, height=12, font=("Consolas", 10))
        self.output.grid(column=0, row=12, columnspan=3, pady=(15, 0))

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
            self.output.insert(tk.END, "✅ Connected successfully\n")
        else:
            self.output.insert(tk.END, "❌ Connection failed\n")

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
            self.output.insert(tk.END, "⚠️ Not connected\n")
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
                self.output.insert(tk.END, "Unsupported function\n")
                return

            if hasattr(rr, 'registers') and rr.registers:
                value = self.decode_float(rr.registers)
                if value is not None:
                    self.output.insert(tk.END, f"📈 Float value: {value:.3f}\n")
                else:
                    self.output.insert(tk.END, f"📋 Raw registers: {rr.registers}\n")
            else:
                self.output.insert(tk.END, f"⚠️ No data or error: {rr}\n")

        except Exception as e:
            self.output.insert(tk.END, f"❌ Error: {e}\n")

# Run the tool
if __name__ == "__main__":
    root = tk.Tk()
    app = ModbusTool(root)
    root.mainloop()