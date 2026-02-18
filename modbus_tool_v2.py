import tkinter as tk
from tkinter import ttk, scrolledtext
from pymodbus.client import ModbusSerialClient
import serial.tools.list_ports
import threading
import time
import struct

# ----------------------------
# Sensor configurations (extensible)
# ----------------------------
class SensorParam:
    def __init__(self, name, addr, unit, count=2, dtype="float"):
        self.name = name          # e.g., "Temperature [°C]"
        self.addr = addr          # starting Modbus address
        self.unit = unit          # display unit string
        self.count = count        # register count (2 regs for 32-bit float)
        self.dtype = dtype        # "float" for FA510

class SensorConfig:
    def __init__(self, vendor, model, read_fc="03", params=None, word_order_reg=2004, word_order_default="ABCD"):
        self.vendor = vendor
        self.model = model
        self.read_fc = read_fc          # FA510 uses 03 (Read Holding)
        self.params = params or []
        # Word order: 0xABCD (big endian), 0xCDAB (middle endian)
        # Datasheet lists at Modbus Register 2005 (address 2004). We'll read this at connect.
        self.word_order_reg = word_order_reg
        self.word_order_default = word_order_default

# CS Instruments FA510 sensor catalog entries (chapter 14.1)
FA510_PARAMS = [
    SensorParam("Temperature [°C]",           1000, "°C"),
    SensorParam("Temperature [°F]",           1002, "°F"),
    SensorParam("Relative humidity [%]",      1004, "%"),
    SensorParam("Dew point [°Ctd]",           1006, "°Ctd"),
    SensorParam("Dew point [°Ftd]",           1008, "°Ftd"),
    SensorParam("Absolute humidity [g/m³]",   1010, "g/m³"),
    SensorParam("Absolute humidity [mg/m³]",  1012, "mg/m³"),
    SensorParam("Humidity grade [g/kg]",      1014, "g/kg"),
    SensorParam("Vapor ratio [ppm]",          1016, "ppm"),
    SensorParam("Saturation vapor pressure [hPa]", 1018, "hPa"),
    SensorParam("Partial vapor pressure [hPa]",    1020, "hPa"),
    SensorParam("Atmospheric dew point [°Ctd]",    1022, "°Ctd"),
    SensorParam("Atmospheric dew point [°Ftd]",    1024, "°Ftd"),
    SensorParam("Pressure absolute [hPa]",         1026, "hPa"),
    SensorParam("Pressure absolute [bar]",         1028, "bar"),
    SensorParam("Pressure absolute [psi]",         1030, "psi"),
    SensorParam("Pressure relative [hPa]",         1032, "hPa"),
    SensorParam("Pressure relative [bar]",         1034, "bar"),
    SensorParam("Pressure relative [psi]",         1036, "psi"),
]

SENSOR_CATALOG = {
    "CS Instruments FA510": SensorConfig(
        vendor="CS Instruments",
        model="FA510",
        read_fc="03",
        params=FA510_PARAMS,
        word_order_reg=2004,       # Address for "Word Order" (Modbus Register 2005 in doc)
        word_order_default="ABCD"  # Assume big endian unless device says otherwise
    ),
    # Add more sensors here later...
}

# ----------------------------
# Main UI class
# ----------------------------
class ModbusTool:
    def __init__(self, root):
        self.root = root
        self.root.title("Modbus RTU Tool")
        self.client = None
        self.polling = False
        self.word_order = "ABCD"  # default big endian
        self.selected_sensor_key = None
        self.build_ui()

    def build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("TLabel", font=("Segoe UI", 10))
        style.configure("TEntry", font=("Segoe UI", 10))
        style.configure("TCombobox", font=("Segoe UI", 10))
        style.configure("Rounded.TButton",
                        font=("Segoe UI", 10, "bold"),
                        foreground="white",
                        background="#2C7A7B",
                        padding=6,
                        relief="flat",
                        borderwidth=0)
        style.map("Rounded.TButton",
                  background=[("active", "#319795"), ("disabled", "#A9A9A9")],
                  relief=[("pressed", "flat"), ("!pressed", "flat")])

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True)

        # Tab 1: Generic Modbus
        self.tab_generic = ttk.Frame(nb, padding=20)
        nb.add(self.tab_generic, text="Generic Modbus")
        self.build_generic_tab(self.tab_generic)

        # Tab 2: Sensors (Hybrid)
        self.tab_sensors = ttk.Frame(nb, padding=20)
        nb.add(self.tab_sensors, text="Sensors")
        self.build_sensors_tab(self.tab_sensors)

    # ----------------------------
    # Generic tab (your original UI)
    # ----------------------------
    def build_generic_tab(self, frm):
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

        ttk.Button(frm, text="🔗 Connect", style="Rounded.TButton", command=self.connect).grid(column=0, row=10, pady=10)
        ttk.Button(frm, text="📥 Read Once", style="Rounded.TButton", command=self.read_once).grid(column=1, row=10)
        ttk.Button(frm, text="🔁 Start Polling", style="Rounded.TButton", command=self.start_polling).grid(column=0, row=11)
        ttk.Button(frm, text="⏹ Stop Polling", style="Rounded.TButton", command=self.stop_polling).grid(column=1, row=11)

        self.output = scrolledtext.ScrolledText(frm, width=60, height=12, font=("Consolas", 10))
        self.output.grid(column=0, row=12, columnspan=3, pady=(15, 0))

    # ----------------------------
    # Sensors tab (Hybrid dropdown)
    # ----------------------------
    def build_sensors_tab(self, frm):
        # top: serial + sensor selector
        ttk.Label(frm, text="🔌 Serial & Sensor", font=("Segoe UI", 11, "bold")).grid(column=0, row=0, columnspan=4, pady=(0, 10), sticky="w")

        ttk.Label(frm, text="Port").grid(column=0, row=1, sticky="w")
        self.port_cb2 = ttk.Combobox(frm, width=18)
        self.port_cb2.grid(column=1, row=1, sticky="w", padx=(0, 10))
        self.refresh_ports(target=self.port_cb2)

        ttk.Label(frm, text="Baudrate").grid(column=2, row=1, sticky="w")
        self.baud_cb2 = ttk.Combobox(frm, values=[1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200], width=10)
        self.baud_cb2.set("19200")
        self.baud_cb2.grid(column=3, row=1, sticky="w")

        ttk.Label(frm, text="Parity").grid(column=0, row=2, sticky="w")
        self.parity_cb2 = ttk.Combobox(frm, values=["N", "E", "O"], width=18)
        self.parity_cb2.set("E")
        self.parity_cb2.grid(column=1, row=2, sticky="w", padx=(0, 10))

        ttk.Label(frm, text="Stop Bits").grid(column=2, row=2, sticky="w")
        self.stop_cb2 = ttk.Combobox(frm, values=[1, 2], width=10)
        self.stop_cb2.set(1)
        self.stop_cb2.grid(column=3, row=2, sticky="w")

        ttk.Label(frm, text="Slave ID").grid(column=0, row=3, sticky="w")
        self.slave_id2 = tk.Entry(frm, width=20)
        self.slave_id2.insert(0, "1")
        self.slave_id2.grid(column=1, row=3, sticky="w", padx=(0, 10))

        ttk.Label(frm, text="Sensor").grid(column=2, row=3, sticky="w")
        self.sensor_cb = ttk.Combobox(frm, values=list(SENSOR_CATALOG.keys()), width=22)
        self.sensor_cb.set("CS Instruments FA510")
        self.sensor_cb.grid(column=3, row=3, sticky="w")

        ttk.Button(frm, text="🔗 Connect", style="Rounded.TButton", command=self.connect_sensor_tab).grid(column=0, row=4, pady=(10, 10), sticky="w")
        ttk.Button(frm, text="🔁 Start Polling", style="Rounded.TButton", command=self.start_polling_sensor).grid(column=1, row=4, sticky="w")
        ttk.Button(frm, text="⏹ Stop Polling", style="Rounded.TButton", command=self.stop_polling).grid(column=2, row=4, sticky="w")

        # parameter dropdown
        ttk.Label(frm, text="Parameter").grid(column=0, row=5, sticky="w", pady=(10, 0))
        self.param_cb = ttk.Combobox(frm, width=40)
        self.param_cb.grid(column=1, row=5, columnspan=3, sticky="w", pady=(10, 0))
        self.load_sensor_params()

        # display area
        self.sensor_output = scrolledtext.ScrolledText(frm, width=70, height=12, font=("Consolas", 10))
        self.sensor_output.grid(column=0, row=6, columnspan=4, pady=(15, 0), sticky="nsew")

        # Manual read button
        ttk.Button(frm, text="📥 Read Parameter", style="Rounded.TButton", command=self.read_selected_param).grid(column=0, row=7, pady=(10, 0), sticky="w")

        # Grid weights for expansion
        frm.grid_columnconfigure(1, weight=1)
        frm.grid_rowconfigure(6, weight=1)

    # ----------------------------
    # Port handling
    # ----------------------------
    def refresh_ports(self, target=None):
        ports = [port.device for port in serial.tools.list_ports.comports()]
        combo = target if target else self.port_cb
        combo['values'] = ports
        if ports:
            combo.set(ports[0])

    # ----------------------------
    # Connection
    # ----------------------------
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
            # Read FA510 word order when applicable (generic page optional)
            self.read_word_order_once()
        else:
            self.output.insert(tk.END, "❌ Connection failed\n")

    def connect_sensor_tab(self):
        self.client = ModbusSerialClient(
            method='rtu',
            port=self.port_cb2.get(),
            baudrate=int(self.baud_cb2.get()),
            parity=self.parity_cb2.get(),
            stopbits=int(self.stop_cb2.get()),
            timeout=1
        )
        if self.client.connect():
            self.sensor_output.insert(tk.END, "✅ Connected (Sensors tab)\n")
            self.selected_sensor_key = self.sensor_cb.get()
            # Read word order (important for 32-bit float decoding)
            self.read_word_order_once(slave_id=int(self.slave_id2.get()))
        else:
            self.sensor_output.insert(tk.END, "❌ Connection failed (Sensors tab)\n")

    def read_word_order_once(self, slave_id=None):
        # Attempt to read word-order register if selected sensor supports it
        try:
            key = self.selected_sensor_key or "CS Instruments FA510"
            cfg = SENSOR_CATALOG.get(key)
            if not cfg or not self.client:
                return
            unit = slave_id if slave_id is not None else int(self.slave_id.get())
            # Word order register is UInt16 at address cfg.word_order_reg
            rr = self.client.read_holding_registers(cfg.word_order_reg, 1, unit=unit)
            if hasattr(rr, 'registers') and rr.registers:
                val = rr.registers[0]
                # Datasheet: 0xABCD => Big Endian, 0xCDAB => Middle Endian
                if val == 0xABCD:
                    self.word_order = "ABCD"
                elif val == 0xCDAB:
                    self.word_order = "CDAB"
                else:
                    # Fallback to default if unknown
                    self.word_order = cfg.word_order_default
                msg = f"ℹ️ Word order: {self.word_order} (raw 0x{val:04X})\n"
            else:
                self.word_order = cfg.word_order_default
                msg = f"ℹ️ Word order not readable. Using default: {self.word_order}\n"
            # Output to both panes when available
            if hasattr(self, 'sensor_output'):
                self.sensor_output.insert(tk.END, msg)
            if hasattr(self, 'output'):
                self.output.insert(tk.END, msg)
        except Exception as e:
            self.word_order = "ABCD"
            if hasattr(self, 'sensor_output'):
                self.sensor_output.insert(tk.END, f"⚠️ Word order read error: {e}. Using default Big-Endian.\n")
            if hasattr(self, 'output'):
                self.output.insert(tk.END, f"⚠️ Word order read error: {e}. Using default Big-Endian.\n")

    # ----------------------------
    # Generic reading
    # ----------------------------
    def read_once(self):
        self.polling = False
        self.read_modbus_generic()

    def start_polling(self):
        self.polling = True
        threading.Thread(target=self.poll_loop_generic, daemon=True).start()

    def stop_polling(self):
        self.polling = False

    def poll_loop_generic(self):
        while self.polling:
            self.read_modbus_generic()
            time.sleep(2)

    # Decode float considering word order
    def decode_float(self, registers):
        if len(registers) < 2:
            return None
        hi, lo = registers[0], registers[1]
        if self.word_order == "ABCD":
            raw = struct.pack('>HH', hi, lo)
        elif self.word_order == "CDAB":
            raw = struct.pack('>HH', lo, hi)
        else:
            raw = struct.pack('>HH', hi, lo)
        try:
            return struct.unpack('>f', raw)[0]
        except struct.error:
            return None

    def read_modbus_generic(self):
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
                if value is not None and count >= 2:
                    self.output.insert(tk.END, f"📈 Float value: {value:.3f}\n")
                else:
                    self.output.insert(tk.END, f"📋 Raw registers: {rr.registers}\n")
            else:
                self.output.insert(tk.END, f"⚠️ No data or error: {rr}\n")

        except Exception as e:
            self.output.insert(tk.END, f"❌ Error: {e}\n")

    # ----------------------------
    # Sensors tab reading
    # ----------------------------
    def load_sensor_params(self):
        key = self.sensor_cb.get()
        cfg = SENSOR_CATALOG.get(key)
        if not cfg:
            self.param_cb['values'] = []
            return
        names = [p.name for p in cfg.params]
        self.param_cb['values'] = names
        if names:
            self.param_cb.set(names[0])

    def start_polling_sensor(self):
        self.polling = True
        threading.Thread(target=self.poll_loop_sensor, daemon=True).start()

    def poll_loop_sensor(self):
        while self.polling:
            self.read_selected_param()
            time.sleep(2)

    def read_selected_param(self):
        if not self.client:
            self.sensor_output.insert(tk.END, "⚠️ Not connected\n")
            return

        key = self.sensor_cb.get()
        cfg = SENSOR_CATALOG.get(key)
        if not cfg:
            self.sensor_output.insert(tk.END, "⚠️ No sensor config selected\n")
            return

        name = self.param_cb.get()
        param = next((p for p in cfg.params if p.name == name), None)
        if not param:
            self.sensor_output.insert(tk.END, "⚠️ Select a parameter\n")
            return

        unit = int(self.slave_id2.get())
        try:
            # FA510 uses Holding Registers (FC03) for value registers
            rr = self.client.read_holding_registers(param.addr, param.count, unit=unit)
            if hasattr(rr, 'registers') and rr.registers and len(rr.registers) >= 2:
                if param.dtype == "float":
                    val = self.decode_float(rr.registers)
                    if val is not None:
                        self.sensor_output.insert(tk.END, f"✅ {param.name}: {val:.3f} {param.unit}\n")
                    else:
                        self.sensor_output.insert(tk.END, f"⚠️ Decode failed ({param.name}). Raw: {rr.registers}\n")
                else:
                    self.sensor_output.insert(tk.END, f"📋 Raw ({param.name}): {rr.registers}\n")
            else:
                self.sensor_output.insert(tk.END, f"⚠️ No data ({param.name}) or error: {rr}\n")
        except Exception as e:
            self.sensor_output.insert(tk.END, f"❌ Error reading {param.name}: {e}\n")

# Run the tool
if __name__ == "__main__":
    root = tk.Tk()
    app = ModbusTool(root)
    root.mainloop()