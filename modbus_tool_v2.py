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

        header = ttk.Frame(self.root, padding=(10, 8))
        header.pack(fill="x")

        ttk.Button(header, text="🔗 Connect", style="Rounded.TButton", command=self.connect).pack(side="left")
        ttk.Label(header, text="Status:").pack(side="left", padx=(10, 2))
        self.connection_status = ttk.Label(header, text="●", foreground="red")
        self.connection_status.pack(side="left")

        serial_frame = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        serial_frame.pack(fill="x")

        ttk.Label(serial_frame, text="Serial Settings:").grid(row=0, column=0, sticky="w")
        ttk.Label(serial_frame, text="Port").grid(row=0, column=1, sticky="w", padx=(10, 2))
        ttk.Button(serial_frame, text="🔄", style="Rounded.TButton", width=3, command=self.refresh_ports).grid(row=0, column=2, sticky="w")
        self.port_cb_global = ttk.Combobox(serial_frame, width=18)
        self.port_cb_global.grid(row=0, column=3, sticky="w", padx=(2, 10))
        self.refresh_ports()

        ttk.Label(serial_frame, text="Baudrate").grid(row=0, column=4, sticky="w")
        self.baud_cb_global = ttk.Combobox(serial_frame, values=[1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200], width=10)
        self.baud_cb_global.set("9600")
        self.baud_cb_global.grid(row=0, column=5, sticky="w", padx=(2, 10))

        ttk.Label(serial_frame, text="Parity").grid(row=0, column=6, sticky="w")
        self.parity_cb_global = ttk.Combobox(serial_frame, values=["N", "E", "O"], width=5)
        self.parity_cb_global.set("N")
        self.parity_cb_global.grid(row=0, column=7, sticky="w", padx=(2, 10))

        ttk.Label(serial_frame, text="Stop Bits").grid(row=0, column=8, sticky="w")
        self.stop_cb_global = ttk.Combobox(serial_frame, values=[1, 2], width=5)
        self.stop_cb_global.set(1)
        self.stop_cb_global.grid(row=0, column=9, sticky="w")

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

        # Tab 3: REJEE smoke sensor
        self.tab_rejee = ttk.Frame(nb, padding=20)
        nb.add(self.tab_rejee, text="REJEE")
        self.build_rejee_tab(self.tab_rejee)

    # ----------------------------
    # Generic tab (your original UI)
    # ----------------------------
    def build_generic_tab(self, frm):
        ttk.Label(frm, text="📟 Modbus Settings", font=("Segoe UI", 11, "bold")).grid(column=0, row=0, columnspan=3, pady=(0, 10), sticky="w")

        ttk.Label(frm, text="Slave ID").grid(column=0, row=1, sticky="w")
        self.slave_id = tk.Entry(frm, width=22)
        self.slave_id.insert(0, "1")
        self.slave_id.grid(column=2, row=1, sticky="e")

        ttk.Label(frm, text="Function").grid(column=0, row=2, sticky="w")
        self.func_cb = ttk.Combobox(frm, values=["03 Read Holding", "04 Read Input"], width=20)
        self.func_cb.set("03 Read Holding")
        self.func_cb.grid(column=2, row=2, sticky="e")

        ttk.Label(frm, text="Start Addr").grid(column=0, row=3, sticky="w")
        self.start_addr = tk.Entry(frm, width=22)
        self.start_addr.insert(0, "1000")
        self.start_addr.grid(column=2, row=3, sticky="e")

        ttk.Label(frm, text="Quantity").grid(column=0, row=4, sticky="w")
        self.quantity = tk.Entry(frm, width=22)
        self.quantity.insert(0, "2")
        self.quantity.grid(column=2, row=4, sticky="e")

        ttk.Button(frm, text="📥 Read Once", style="Rounded.TButton", command=self.read_once).grid(column=0, row=5)
        ttk.Button(frm, text="🔁 Start Polling", style="Rounded.TButton", command=self.start_polling).grid(column=0, row=6)
        ttk.Button(frm, text="⏹ Stop Polling", style="Rounded.TButton", command=self.stop_polling).grid(column=1, row=6)

        self.output = scrolledtext.ScrolledText(frm, width=60, height=12, font=("Consolas", 10))
        self.output.grid(column=0, row=7, columnspan=3, pady=(15, 0))

    # ----------------------------
    # Sensors tab (Hybrid dropdown)
    # ----------------------------
    def build_sensors_tab(self, frm):
        ttk.Label(frm, text="Sensor Settings", font=("Segoe UI", 11, "bold")).grid(column=0, row=0, columnspan=4, pady=(0, 10), sticky="w")

        ttk.Label(frm, text="Slave ID").grid(column=0, row=1, sticky="w")
        self.slave_id2 = tk.Entry(frm, width=20)
        self.slave_id2.insert(0, "1")
        self.slave_id2.grid(column=1, row=1, sticky="w", padx=(0, 10))

        ttk.Label(frm, text="Sensor").grid(column=2, row=1, sticky="w")
        self.sensor_cb = ttk.Combobox(frm, values=list(SENSOR_CATALOG.keys()), width=22)
        self.sensor_cb.set("CS Instruments FA510")
        self.sensor_cb.grid(column=3, row=1, sticky="w")

        ttk.Button(frm, text="🔁 Start Polling", style="Rounded.TButton", command=self.start_polling_sensor).grid(column=0, row=2, pady=(10, 10), sticky="w")
        ttk.Button(frm, text="⏹ Stop Polling", style="Rounded.TButton", command=self.stop_polling).grid(column=1, row=2, sticky="w")

        # parameter dropdown
        ttk.Label(frm, text="Parameter").grid(column=0, row=3, sticky="w", pady=(10, 0))
        self.param_cb = ttk.Combobox(frm, width=40)
        self.param_cb.grid(column=1, row=3, columnspan=3, sticky="w", pady=(10, 0))
        self.load_sensor_params()

        # display area
        self.sensor_output = scrolledtext.ScrolledText(frm, width=70, height=12, font=("Consolas", 10))
        self.sensor_output.grid(column=0, row=4, columnspan=4, pady=(15, 0), sticky="nsew")

        # Manual read button
        ttk.Button(frm, text="📥 Read Parameter", style="Rounded.TButton", command=self.read_selected_param).grid(column=0, row=5, pady=(10, 0), sticky="w")

        # Grid weights for expansion
        frm.grid_columnconfigure(1, weight=1)
        frm.grid_rowconfigure(4, weight=1)

    # ----------------------------
    # REJEE smoke sensor tab
    # ----------------------------
    def build_rejee_tab(self, frm):
        ttk.Label(frm, text="Manual Address").grid(column=0, row=0, sticky="w")
        self.rejee_manual_addr = tk.Entry(frm, width=20)
        self.rejee_manual_addr.insert(0, "49")
        self.rejee_manual_addr.grid(column=1, row=0, sticky="w", padx=(0, 10))

        # Option 1: test default address
        ttk.Label(frm, text="1. Test Default Address", font=("Segoe UI", 10, "bold")).grid(
            column=0, row=1, columnspan=4, pady=(10, 0), sticky="w"
        )
        ttk.Button(
            frm,
            text="Test 49 (Read 0003/0004)",
            style="Rounded.TButton",
            command=self.rejee_test_default,
        ).grid(column=0, row=2, sticky="w")


        # Option 2: set new address (FC06)
        ttk.Label(frm, text="2. Set New Address", font=("Segoe UI", 10, "bold")).grid(
            column=0, row=3, columnspan=4, pady=(10, 0), sticky="w"
        )
        ttk.Label(frm, text="New Address").grid(column=0, row=4, sticky="w")
        self.rejee_new_addr = tk.Entry(frm, width=20)
        self.rejee_new_addr.insert(0, "49")
        self.rejee_new_addr.grid(column=1, row=4, sticky="w", padx=(0, 10))
        ttk.Button(
            frm,
            text="Write 0004 (FC06)",
            style="Rounded.TButton",
            command=self.rejee_set_address,
        ).grid(column=2, row=4, sticky="w")

        self.rejee_verify_label = ttk.Label(frm, text="")
        self.rejee_verify_label.grid(column=3, row=4, sticky="w")

        # Option 3: manual read
        ttk.Label(frm, text="3. Manual Read", font=("Segoe UI", 10, "bold")).grid(
            column=0, row=5, columnspan=4, pady=(10, 0), sticky="w"
        )
        ttk.Button(
            frm,
            text="Read Alarm/Address",
            style="Rounded.TButton",
            command=self.rejee_manual_read,
        ).grid(column=0, row=6, sticky="w")

        self.rejee_output = scrolledtext.ScrolledText(frm, width=70, height=12, font=("Consolas", 10))
        self.rejee_output.grid(column=0, row=7, columnspan=4, pady=(15, 0), sticky="nsew")

        frm.grid_columnconfigure(1, weight=1)
        frm.grid_rowconfigure(7, weight=1)

    # ----------------------------
    # Port handling
    # ----------------------------
    def refresh_ports(self, target=None):
        ports = [port.device for port in serial.tools.list_ports.comports()]
        combo = target if target else self.port_cb_global
        current = combo.get().strip()
        combo['values'] = ports

        if not ports:
            combo.set("")
            return

        if current in ports:
            combo.set(current)
            return

        combo.set(self._get_lowest_com_port(ports))

    def _get_lowest_com_port(self, ports):
        def com_key(name):
            upper = name.upper()
            if upper.startswith("COM"):
                suffix = upper[3:]
                if suffix.isdigit():
                    return int(suffix)
            return 99999

        return min(ports, key=com_key)

    def _log_error(self, output_widget, prefix, err):
        output_widget.insert(tk.END, f"{prefix}: {err}\n")

    def _set_status(self, ok):
        self.connection_status.config(foreground="green" if ok else "red")

    def _ensure_port_selected(self, combo, output_widget):
        port = combo.get().strip()
        if not port:
            output_widget.insert(tk.END, "⚠️ No COM port selected\n")
            return None
        return port

    def _get_int_entry(self, entry, label, output_widget):
        try:
            return int(entry.get())
        except ValueError:
            output_widget.insert(tk.END, f"⚠️ Invalid {label}\n")
            return None

    def _read_holding_registers(self, address, count, unit_id):
        try:
            return self.client.read_holding_registers(address, count=count, unit=unit_id)
        except TypeError:
            try:
                return self.client.read_holding_registers(address, count=count, slave=unit_id)
            except TypeError:
                try:
                    return self.client.read_holding_registers(address, count=count, unit_id=unit_id)
                except TypeError:
                    return self.client.read_holding_registers(address, count=count)

    def _read_input_registers(self, address, count, unit_id):
        try:
            return self.client.read_input_registers(address, count=count, unit=unit_id)
        except TypeError:
            try:
                return self.client.read_input_registers(address, count=count, slave=unit_id)
            except TypeError:
                try:
                    return self.client.read_input_registers(address, count=count, unit_id=unit_id)
                except TypeError:
                    return self.client.read_input_registers(address, count=count)

    def _write_register(self, address, value, unit_id):
        try:
            return self.client.write_register(address, value, unit=unit_id)
        except TypeError:
            try:
                return self.client.write_register(address, value, slave=unit_id)
            except TypeError:
                try:
                    return self.client.write_register(address, value, unit_id)
                except TypeError:
                    return self.client.write_register(address, value)

    def _create_serial_client(self, port):
        try:
            return ModbusSerialClient(
                method='rtu',
                port=port,
                baudrate=int(self.baud_cb_global.get()),
                parity=self.parity_cb_global.get(),
                stopbits=int(self.stop_cb_global.get()),
                timeout=1
            )
        except TypeError:
            return ModbusSerialClient(
                port=port,
                baudrate=int(self.baud_cb_global.get()),
                parity=self.parity_cb_global.get(),
                stopbits=int(self.stop_cb_global.get()),
                timeout=1
            )

    # ----------------------------
    # Connection
    # ----------------------------
    def connect(self):
        port = self._ensure_port_selected(self.port_cb_global, self.output)
        if not port:
            self._set_status(False)
            return
        try:
            self.client = self._create_serial_client(port)
            if self.client.connect():
                self.output.insert(tk.END, "✅ Connected successfully\n")
                self._set_status(True)
                # Read FA510 word order when applicable (generic page optional)
                self.read_word_order_once()
            else:
                self.output.insert(tk.END, "❌ Connection failed\n")
                self._set_status(False)
        except Exception as e:
            self._log_error(self.output, "❌ Connection error", e)
            self._set_status(False)

    def connect_sensor_tab(self):
        port = self._ensure_port_selected(self.port_cb_global, self.sensor_output)
        if not port:
            self._set_status(False)
            return
        try:
            self.client = self._create_serial_client(port)
            if self.client.connect():
                self.sensor_output.insert(tk.END, "✅ Connected (Sensors tab)\n")
                self._set_status(True)
                self.selected_sensor_key = self.sensor_cb.get()
                # Read word order (important for 32-bit float decoding)
                slave_id = self._get_int_entry(self.slave_id2, "Slave ID", self.sensor_output)
                if slave_id is not None:
                    self.read_word_order_once(slave_id=slave_id)
            else:
                self.sensor_output.insert(tk.END, "❌ Connection failed (Sensors tab)\n")
                self._set_status(False)
        except Exception as e:
            self._log_error(self.sensor_output, "❌ Connection error", e)
            self._set_status(False)

    def read_word_order_once(self, slave_id=None):
        # Attempt to read word-order register if selected sensor supports it
        try:
            key = self.selected_sensor_key or "CS Instruments FA510"
            cfg = SENSOR_CATALOG.get(key)
            if not cfg or not self.client:
                return
            unit = slave_id if slave_id is not None else int(self.slave_id.get())
            # Word order register is UInt16 at address cfg.word_order_reg
            rr = self._read_holding_registers(cfg.word_order_reg, 1, unit)
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
            self._set_status(False)
            return

        unit = self._get_int_entry(self.slave_id, "Slave ID", self.output)
        addr = self._get_int_entry(self.start_addr, "Start Addr", self.output)
        count = self._get_int_entry(self.quantity, "Quantity", self.output)
        if unit is None or addr is None or count is None:
            return
        func = self.func_cb.get()

        try:
            if func.startswith("03"):
                rr = self._read_holding_registers(addr, count, unit)
            elif func.startswith("04"):
                rr = self._read_input_registers(addr, count, unit)
            else:
                self.output.insert(tk.END, "Unsupported function\n")
                return

            if hasattr(rr, 'registers') and rr.registers:
                value = self.decode_float(rr.registers)
                if value is not None and count >= 2:
                    self.output.insert(tk.END, f"📈 Float value: {value:.3f}\n")
                else:
                    self.output.insert(tk.END, f"📋 Raw registers: {rr.registers}\n")
                self._set_status(True)
            else:
                self.output.insert(tk.END, f"⚠️ No data or error: {rr}\n")
                self._set_status(False)

        except Exception as e:
            self._log_error(self.output, "❌ Error", e)
            self._set_status(False)

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
            self._set_status(False)
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

        unit = self._get_int_entry(self.slave_id2, "Slave ID", self.sensor_output)
        if unit is None:
            return
        try:
            # FA510 uses Holding Registers (FC03) for value registers
            rr = self._read_holding_registers(param.addr, param.count, unit)
            if hasattr(rr, 'registers') and rr.registers and len(rr.registers) >= 2:
                if param.dtype == "float":
                    val = self.decode_float(rr.registers)
                    if val is not None:
                        self.sensor_output.insert(tk.END, f"✅ {param.name}: {val:.3f} {param.unit}\n")
                        self._set_status(True)
                    else:
                        self.sensor_output.insert(tk.END, f"⚠️ Decode failed ({param.name}). Raw: {rr.registers}\n")
                        self._set_status(False)
                else:
                    self.sensor_output.insert(tk.END, f"📋 Raw ({param.name}): {rr.registers}\n")
                    self._set_status(True)
            else:
                self.sensor_output.insert(tk.END, f"⚠️ No data ({param.name}) or error: {rr}\n")
                self._set_status(False)
        except Exception as e:
            self._log_error(self.sensor_output, f"❌ Error reading {param.name}", e)
            self._set_status(False)

    # ----------------------------
    # REJEE smoke sensor actions
    # ----------------------------
    def _build_rejee_client(self):
        return self._create_serial_client(self.port_cb_global.get())

    def _ensure_rejee_client(self):
        if self.client and getattr(self.client, "connected", False):
            self._set_status(True)
            return True
        port = self._ensure_port_selected(self.port_cb_global, self.rejee_output)
        if not port:
            self._set_status(False)
            return False
        try:
            self.client = self._build_rejee_client()
            if self.client.connect():
                self._set_status(True)
                return True
            self.rejee_output.insert(tk.END, "Connection failed\n")
            self._set_status(False)
        except Exception as e:
            self._log_error(self.rejee_output, "Connection error", e)
            self._set_status(False)
        return False

    def rejee_test_default(self):
        if not self._ensure_rejee_client():
            return
        self.rejee_read_alarm_and_address(49)

    def rejee_set_address(self):
        if not self._ensure_rejee_client():
            return
        new_addr = self._get_int_entry(self.rejee_new_addr, "New Address", self.rejee_output)
        if new_addr is None:
            return

        # Write new address to register 0004 using FC06
        try:
            wr = self._write_register(4, new_addr, 49)
            if wr.isError():
                self.rejee_output.insert(tk.END, f"Write failed: {wr}\n")
                self.rejee_verify_label.config(text="Not verified", foreground="red")
                return

            # Verify by reading register 0004 from new address
            rr = self._read_holding_registers(4, 1, new_addr)
            if hasattr(rr, 'registers') and rr.registers and rr.registers[0] == new_addr:
                self.rejee_verify_label.config(text="Verified", foreground="green")
                self.rejee_output.insert(tk.END, "Address updated and verified\n")
            else:
                self.rejee_verify_label.config(text="Not verified", foreground="red")
                self.rejee_output.insert(tk.END, f"Verify failed: {rr}\n")
        except Exception as e:
            self.rejee_verify_label.config(text="Not verified", foreground="red")
            self._log_error(self.rejee_output, "Error", e)

    def rejee_manual_read(self):
        if not self._ensure_rejee_client():
            return
        addr = self._get_int_entry(self.rejee_manual_addr, "Manual Address", self.rejee_output)
        if addr is None:
            return
        self.rejee_read_alarm_and_address(addr)

    def rejee_read_alarm_and_address(self, unit_addr):
        try:
            rr = self._read_holding_registers(3, 2, unit_addr)

            if hasattr(rr, 'registers') and rr.registers and len(rr.registers) >= 2:
                alarm_val = rr.registers[0]
                addr_val = rr.registers[1]
                self.rejee_output.insert(tk.END, f"Alarm status (0003): {alarm_val}\n")
                self.rejee_output.insert(tk.END, f"Device address (0004): {addr_val}\n")
                self._set_status(True)
            else:
                self.rejee_output.insert(tk.END, f"Read failed: {rr}\n")
                self._set_status(False)
        except Exception as e:
            self._log_error(self.rejee_output, "Error", e)
            self._set_status(False)


# Run the tool
if __name__ == "__main__":
    root = tk.Tk()
    app = ModbusTool(root)
    root.mainloop()
