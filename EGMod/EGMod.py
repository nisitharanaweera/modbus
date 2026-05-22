import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import serial.tools.list_ports
import threading
import time
import queue
from datetime import datetime

try:
    from pymodbus.client import ModbusSerialClient
    PYMODBUS_OK = True
except ImportError:
    PYMODBUS_OK = False

def _detect_slave_kwarg():
    """Detect which keyword arg the installed pymodbus uses for the slave/device ID."""
    try:
        import inspect
        sig = inspect.signature(ModbusSerialClient.read_holding_registers)
        for name in ("device_id", "slave", "unit"):
            if name in sig.parameters:
                return name
    except Exception:
        pass
    return "unit"  # very old pymodbus fallback

_SLAVE_KW = _detect_slave_kwarg() if PYMODBUS_OK else "unit"

# ── VS Code Dark+ palette ───────────────────────────────────────────
_BG    = "#1E1E1E"   # editor / main window
_BG2   = "#252526"   # sidebar / panels
_BG3   = "#3C3C3C"   # input fields
_FG    = "#D4D4D4"   # default text
_FG2   = "#858585"   # dimmed / hint text
_ACC   = "#007ACC"   # accent blue
_SEL   = "#264F78"   # selection background
_BORD  = "#474747"   # borders
# cell colours
_CELL_HDR_BG  = "#2D2D2D"
_CELL_HDR_FG  = "#9CDCFE"   # VS Code variable blue
_CELL_VAL_BG  = "#252526"
_CELL_VAL_FG  = "#D4D4D4"
_CELL_WR_BG   = "#3C3C3C"
_CELL_WR_FG   = "#CE9178"   # VS Code string orange

def get_lowest_com(ports):
    def key(p):
        u = p.upper()
        return int(u[3:]) if u.startswith("COM") and u[3:].isdigit() else 99999
    return min(ports, key=key) if ports else ""

def _port_label(p):
    desc = (p.description or "").strip()
    if desc and desc != p.device:
        return f"{p.device}  —  {desc}"
    return p.device

def to_display(value, fmt):
    if fmt == "Hex":    return f"0x{value:04X}"
    elif fmt == "Binary": return f"{value:016b}"
    return str(value)


class SerialMonitor(tk.Toplevel):
    def __init__(self, parent, title="Serial Monitor"):
        super().__init__(parent)
        self.title(title)
        self.geometry("720x500")
        self.minsize(500, 300)
        self._entries = []
        self._q = queue.Queue()
        self._ts_var = tk.BooleanVar(value=True)
        self._fmt_var = tk.StringVar(value="Hex")
        self._rx_buf = bytearray()
        self._rx_ts = None
        self._rx_lock = threading.Lock()
        self._rx_timer = None
        self._build_ui()
        self._poll()

    def _build_ui(self):
        self.configure(bg=_BG)
        bar = ttk.Frame(self, padding=(6, 4, 6, 0))
        bar.pack(fill="x")
        ttk.Checkbutton(bar, text="Timestamp", variable=self._ts_var,
                        command=self._rerender).pack(side="left", padx=(0, 12))
        ttk.Label(bar, text="Format:").pack(side="left", padx=(0, 4))
        for fmt in ("Text", "Hex", "Binary"):
            ttk.Radiobutton(bar, text=fmt, variable=self._fmt_var,
                            value=fmt, command=self._rerender).pack(side="left", padx=(0, 4))
        ttk.Button(bar, text="Clear", command=self._clear, style="Teal.TButton").pack(side="right")
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=4, pady=(4, 0))
        self._txt = scrolledtext.ScrolledText(
            self, font=("Consolas", 9), state="disabled", wrap="none",
            bg=_BG, fg=_FG, insertbackground=_FG,
            selectbackground=_SEL, selectforeground=_FG)
        self._txt.pack(fill="both", expand=True, padx=4, pady=4)
        self._txt.vbar.configure(bg=_BG2, troughcolor=_BG, activebackground="#5A5A5A",
                                 relief="flat", borderwidth=0, highlightthickness=0)
        self._txt.tag_configure("ts",     foreground=_FG2)
        self._txt.tag_configure("tx_dir", foreground="#569CD6", font=("Consolas", 9, "bold"))
        self._txt.tag_configure("tx",     foreground="#9CDCFE")
        self._txt.tag_configure("rx_dir", foreground="#4EC9B0", font=("Consolas", 9, "bold"))
        self._txt.tag_configure("rx",     foreground="#B5CEA8")
        self._txt.tag_configure("info",   foreground="#CE9178")

    def log_tx(self, data):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._q.put(("TX", bytes(data), ts))

    def log_rx(self, data):
        if not data: return
        with self._rx_lock:
            if not self._rx_buf:
                self._rx_ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self._rx_buf.extend(data)
            if self._rx_timer:
                self._rx_timer.cancel()
            t = threading.Timer(0.02, self._flush_rx)
            t.daemon = True
            self._rx_timer = t
            t.start()

    def _flush_rx(self):
        with self._rx_lock:
            if self._rx_buf:
                ts = self._rx_ts or datetime.now().strftime("%H:%M:%S.%f")[:-3]
                self._q.put(("RX", bytes(self._rx_buf), ts))
                self._rx_buf.clear()
                self._rx_ts = None
            self._rx_timer = None

    def log_info(self, msg):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._q.put(("INFO", msg, ts))

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                item = self._q.get_nowait()
                self._entries.append(item)
                self._write_entry(*item)
        except queue.Empty:
            pass
        self.after(10, self._poll)

    def _write_entry(self, direction, data, ts):
        self._txt.config(state="normal")
        if self._ts_var.get():
            self._txt.insert(tk.END, f"[{ts}] ", "ts")
        if direction == "TX":
            self._txt.insert(tk.END, " TX ", "tx_dir")
            self._txt.insert(tk.END, f"  {self._fmt_bytes(data)}\n", "tx")
        elif direction == "RX":
            self._txt.insert(tk.END, " RX ", "rx_dir")
            self._txt.insert(tk.END, f"  {self._fmt_bytes(data)}\n", "rx")
        else:
            self._txt.insert(tk.END, f" \u2139  {data}\n", "info")
        self._txt.see(tk.END)
        self._txt.config(state="disabled")

    def _fmt_bytes(self, data):
        fmt = self._fmt_var.get()
        if isinstance(data, str): return data
        if fmt == "Hex":
            return " ".join(f"{b:02X}" for b in data)
        elif fmt == "Binary":
            return " ".join(f"{b:08b}" for b in data)
        else:
            return "".join(chr(b) if 32 <= b < 127 else f"[{b:02X}]" for b in data)

    def _rerender(self):
        self._txt.config(state="normal")
        self._txt.delete("1.0", tk.END)
        for entry in self._entries:
            self._write_entry(*entry)
        self._txt.config(state="disabled")

    def _clear(self):
        self._entries.clear()
        self._txt.config(state="normal")
        self._txt.delete("1.0", tk.END)
        self._txt.config(state="disabled")


class EGMod:
    def __init__(self, root):
        self.root = root
        self.root.title("EGMod — Modbus RTU Tool")
        try:
            self.root.iconbitmap("icon.ico")
        except Exception:
            pass
        self.root.minsize(820, 560)
        self.client = None
        self.connected = False
        self.connected_port = None
        self._serial_monitor = None
        self._port_map = {}  # label -> device
        self._scan_running = False
        self._orig_serial_read = None
        self._orig_serial_write = None
        self._apply_style()
        self._build_connection_frame()
        self._build_tabs()
        self._start_monitor()

    def _bind_fmt_scroll(self, spin, fmt_cb, lo, hi):
        """Bind mouse-wheel to spin in a format-aware way (Decimal/Hex/Binary)."""
        def _wheel(event):
            step = 1 if event.delta > 0 else -1
            raw = spin.get().strip()
            fmt = fmt_cb.get()
            try:
                if fmt == "Hex":    val = int(raw, 16)
                elif fmt == "Binary": val = int(raw, 2)
                else:               val = int(raw)
            except ValueError:
                val = lo
            val = max(lo, min(hi, val + step))
            if fmt == "Hex":    spin.set(f"{val:X}")
            elif fmt == "Binary": spin.set(f"{val:b}")
            else:               spin.set(str(val))
            return "break"
        spin.bind("<MouseWheel>", _wheel)

    def _apply_style(self):
        self.root.configure(bg=_BG)
        s = ttk.Style()
        s.theme_use("clam")
        # root defaults — cascade to all unstyled widgets
        s.configure(".",
            background=_BG2, foreground=_FG,
            fieldbackground=_BG3, selectbackground=_SEL, selectforeground=_FG,
            bordercolor=_BORD, lightcolor=_BG2, darkcolor=_BG2,
            troughcolor=_BG2, insertcolor=_FG,
            font=("Segoe UI", 10))
        s.configure("TFrame",      background=_BG2)
        s.configure("TLabel",      background=_BG2, foreground=_FG, font=("Segoe UI", 10))
        s.configure("TLabelframe", background=_BG2, bordercolor=_BORD,
                    lightcolor=_BORD, darkcolor=_BORD, relief="groove")
        s.configure("TLabelframe.Label", background=_BG2, foreground=_FG2,
                    font=("Segoe UI", 9))
        s.configure("TEntry",
            fieldbackground=_BG3, foreground=_FG, insertcolor=_FG,
            bordercolor=_BORD, lightcolor=_BORD, darkcolor=_BORD,
            font=("Segoe UI", 10))
        s.configure("TCombobox",
            fieldbackground=_BG3, foreground=_FG, background=_BG2,
            arrowcolor=_FG, bordercolor=_BORD, lightcolor=_BORD, darkcolor=_BORD,
            selectbackground=_SEL, selectforeground=_FG,
            font=("Segoe UI", 10))
        s.map("TCombobox",
            fieldbackground=[("readonly", _BG3), ("disabled", _BG2)],
            foreground=[("disabled", _FG2)],
            selectbackground=[("readonly", _SEL)])
        s.configure("TSpinbox",
            fieldbackground=_BG3, foreground=_FG, background=_BG2,
            arrowcolor=_FG, bordercolor=_BORD, lightcolor=_BORD, darkcolor=_BORD,
            insertcolor=_FG, font=("Segoe UI", 10))
        s.configure("TCheckbutton", background=_BG2, foreground=_FG)
        s.map("TCheckbutton",
            background=[("active", _BG2)],
            indicatorcolor=[("selected", "#24acf2"), ("", _BG3)])
        s.configure("TRadiobutton", background=_BG2, foreground=_FG)
        s.map("TRadiobutton",
            background=[("active", _BG2)],
            indicatorcolor=[("selected", "#24acf2"), ("", _BG3)])
        s.configure("TSeparator", background=_BORD)
        s.configure("TScrollbar",
            background=_BG2, troughcolor=_BG, arrowcolor=_FG2,
            bordercolor=_BG, lightcolor=_BG2, darkcolor=_BG2)
        s.map("TScrollbar", background=[("active", "#5A5A5A")])
        s.configure("TNotebook", background=_BG2, bordercolor=_BORD, tabmargins=[0,0,0,0])
        s.configure("TNotebook.Tab",
            background=_BG2, foreground=_FG2,
            padding=[12, 5], bordercolor=_BORD,
            font=("Segoe UI", 10))
        s.map("TNotebook.Tab",
            background=[("selected", _BG2), ("active", "#2A2D2E")],
            foreground=[("selected", _FG), ("active", _FG)])
        # primary action button
        _BTN = "#24acf2"
        _BTN_HV = "#1a9de0"
        s.configure("Teal.TButton",
            font=("Segoe UI", 10, "bold"), foreground="white",
            background=_BTN, padding=[10, 6], relief="flat",
            borderwidth=0, focusthickness=0,
            bordercolor=_BTN, lightcolor=_BTN, darkcolor=_BTN)
        s.map("Teal.TButton",
            background=[("active", _BTN_HV), ("disabled", "#555555")],
            bordercolor=[("active", _BTN_HV), ("disabled", "#555555")])
        # destructive / disconnect button
        s.configure("Red.TButton",
            font=("Segoe UI", 10, "bold"), foreground="white",
            background="#C53030", padding=[10, 6], relief="flat",
            borderwidth=0, focusthickness=0,
            bordercolor="#C53030", lightcolor="#C53030", darkcolor="#C53030")
        s.map("Red.TButton",
            background=[("active", "#9B2C2C")],
            bordercolor=[("active", "#9B2C2C")])
        # Combobox dropdown listbox (tk.Listbox, not themed by ttk)
        self.root.option_add("*TCombobox*Listbox.background",       _BG3)
        self.root.option_add("*TCombobox*Listbox.foreground",       _FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", _SEL)
        self.root.option_add("*TCombobox*Listbox.selectForeground", _FG)
        self.root.option_add("*TCombobox*Listbox.relief",           "flat")
        self.root.option_add("*TCombobox*Listbox.borderWidth",      "0")
        # Treeview dark theme
        s.configure("Treeview",
            background=_BG2, foreground=_FG, fieldbackground=_BG2,
            bordercolor=_BORD, rowheight=24)
        s.map("Treeview",
            background=[("selected", _SEL)],
            foreground=[("selected", _FG)])
        s.configure("Treeview.Heading",
            background=_BG3, foreground=_FG2, relief="flat",
            bordercolor=_BORD)
        s.map("Treeview.Heading",
            background=[("active", _BG3)])

    def _build_connection_frame(self):
        outer = ttk.LabelFrame(self.root, text="Connection", padding=(10, 6))
        outer.pack(fill="x", padx=10, pady=(10, 0))

        r0 = ttk.Frame(outer)
        r0.pack(fill="x")

        ttk.Label(r0, text="Port:").pack(side="left")
        self.port_cb = ttk.Combobox(r0, width=30, state="readonly")
        self.port_cb.pack(side="left", padx=(2, 4))
        ttk.Button(r0, text="⟳", style="Teal.TButton", width=2,
                   command=self.refresh_ports).pack(side="left", padx=(0, 10))

        ttk.Label(r0, text="Baud:").pack(side="left")
        self.baud_cb = ttk.Combobox(r0, width=7, state="readonly",
            values=["1200","2400","4800","9600","19200","38400","57600","115200"])
        self.baud_cb.set("9600")
        self.baud_cb.pack(side="left", padx=(2, 10))
        self.baud_cb.bind("<<ComboboxSelected>>", lambda e: self._on_param_change())

        ttk.Label(r0, text="Data bits:").pack(side="left")
        self.databits_cb = ttk.Combobox(r0, width=3, state="readonly", values=["5","6","7","8"])
        self.databits_cb.set("8")
        self.databits_cb.pack(side="left", padx=(2, 10))
        self.databits_cb.bind("<<ComboboxSelected>>", lambda e: self._on_param_change())

        ttk.Label(r0, text="Stop bits:").pack(side="left")
        self.stopbits_cb = ttk.Combobox(r0, width=4, state="readonly", values=["1","1.5","2"])
        self.stopbits_cb.set("1")
        self.stopbits_cb.pack(side="left", padx=(2, 10))
        self.stopbits_cb.bind("<<ComboboxSelected>>", lambda e: self._on_param_change())

        ttk.Label(r0, text="Parity:").pack(side="left")
        self.parity_cb = ttk.Combobox(r0, width=4, state="readonly", values=["N","E","O","M","S"])
        self.parity_cb.set("N")
        self.parity_cb.pack(side="left", padx=(2, 10))
        self.parity_cb.bind("<<ComboboxSelected>>", lambda e: self._on_param_change())

        ttk.Label(r0, text="Line end:").pack(side="left")
        self.lineend_cb = ttk.Combobox(r0, width=7, state="readonly",
                                        values=["None","CR","LF","CR+LF"])
        self.lineend_cb.set("None")
        self.lineend_cb.pack(side="left", padx=(2, 10))

        self._adv_visible = False
        self._adv_btn = ttk.Button(r0, text="▶ Advanced", style="Teal.TButton",
                                   command=self._toggle_advanced)
        self._adv_btn.pack(side="left")

        self._adv_frame = ttk.Frame(outer)
        ttk.Label(self._adv_frame, text="DTR:").pack(side="left")
        self.dtr_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self._adv_frame, text="Enable", variable=self.dtr_var).pack(side="left", padx=(2,16))
        ttk.Label(self._adv_frame, text="RTS:").pack(side="left")
        self.rts_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self._adv_frame, text="Enable", variable=self.rts_var).pack(side="left", padx=(2,0))

        r1 = ttk.Frame(outer)
        r1.pack(fill="x", pady=(8, 0))
        self.connect_btn = ttk.Button(r1, text="Connect", style="Teal.TButton",
                                      command=self._toggle_connect)
        self.connect_btn.pack(side="left")
        ttk.Label(r1, text="Status:").pack(side="left", padx=(14, 4))
        self.status_dot = ttk.Label(r1, text="●", foreground="red", font=("Segoe UI", 14))
        self.status_dot.pack(side="left")
        self.status_lbl = ttk.Label(r1, text="Disconnected")
        self.status_lbl.pack(side="left", padx=(4, 0))
        ttk.Button(r1, text="\u238a  Monitor", style="Teal.TButton",
                   command=self._open_monitor).pack(side="right")
        ttk.Separator(r1, orient="vertical").pack(side="right", fill="y", padx=(10, 10))
        self.comms_lbl = ttk.Label(r1, text="\u2014", foreground=_FG2)
        self.comms_lbl.pack(side="right", padx=(4, 0))
        self.comms_dot = ttk.Label(r1, text="\u25cf", foreground=_FG2, font=("Segoe UI", 14))
        self.comms_dot.pack(side="right")
        ttk.Label(r1, text="Comms:").pack(side="right", padx=(10, 4))
        self.refresh_ports()

    def _toggle_advanced(self):
        if self._adv_visible:
            self._adv_frame.pack_forget()
            self._adv_btn.config(text="▶ Advanced")
        else:
            self._adv_frame.pack(fill="x", pady=(4,0))
            self._adv_btn.config(text="▼ Advanced")
        self._adv_visible = not self._adv_visible

    def refresh_ports(self):
        port_info = serial.tools.list_ports.comports()
        self._port_map = {_port_label(p): p.device for p in port_info}
        labels = list(self._port_map.keys())
        devices = list(self._port_map.values())
        current_device = self._port_map.get(self.port_cb.get(), self.port_cb.get())
        self.port_cb["values"] = labels
        if not labels:
            self.port_cb.set(""); return
        best_device = current_device if current_device in devices else get_lowest_com(devices)
        best_label = next((l for l, d in self._port_map.items() if d == best_device), labels[0])
        self.port_cb.set(best_label)

    def _on_param_change(self):
        if self.connected:
            self._disconnect()
            self._connect()

    def _toggle_connect(self):
        self._disconnect() if self.connected else self._connect()

    def _connect(self):
        if not PYMODBUS_OK:
            messagebox.showerror("Missing library", "pymodbus is not installed."); return
        port = self._port_map.get(self.port_cb.get(), self.port_cb.get()).strip()
        if not port:
            messagebox.showwarning("No port", "Select a COM port first."); return
        try:
            stop_map = {"1": 1, "1.5": 1.5, "2": 2}
            kwargs = dict(port=port, baudrate=int(self.baud_cb.get()),
                          bytesize=int(self.databits_cb.get()),
                          stopbits=stop_map.get(self.stopbits_cb.get(), 1),
                          parity=self.parity_cb.get(), timeout=1,
                          retries=0)
            try:
                self.client = ModbusSerialClient(method="rtu", **kwargs)
            except TypeError:
                self.client = ModbusSerialClient(**kwargs)

            if self.client.connect():
                try:
                    ser = self.client.socket
                    if hasattr(ser, "dtr"): ser.dtr = self.dtr_var.get()
                    if hasattr(ser, "rts"): ser.rts = self.rts_var.get()
                except Exception:
                    pass
                self.connected = True
                self.connected_port = port
                self._set_status(True, f"Connected  [{port}]")
                self._set_comms(None)
                self.connect_btn.config(text="Disconnect", style="Red.TButton")
                self._wrap_serial()
                if self._serial_monitor and self._serial_monitor.winfo_exists():
                    self._serial_monitor.log_info(f"Connected to {port}")
            else:
                self._set_status(False, "Connection failed")
        except Exception as e:
            self._set_status(False, f"Error: {e}")

    def _disconnect(self, lost=False):
        if self.client:
            self._unwrap_serial()
            if self._serial_monitor and self._serial_monitor.winfo_exists():
                note = " \u2014 connection lost!" if lost else ""
                self._serial_monitor.log_info(f"Port closed{note}")
            try: self.client.close()
            except Exception: pass
            self.client = None
        self.connected = False
        self.connected_port = None
        self.connect_btn.config(text="Connect", style="Teal.TButton")
        self._set_status(False, "Connection lost!" if lost else "Disconnected")
        self._set_comms(None)
        self._scan_running = False

    def _set_status(self, ok, text=""):
        self.status_dot.config(foreground="green" if ok else "red")
        self.status_lbl.config(text=text)

    def _set_comms(self, ok, text=""):
        if ok is None:
            self.comms_dot.config(foreground=_FG2)
            self.comms_lbl.config(text="\u2014", foreground=_FG2)
        elif ok:
            self.comms_dot.config(foreground="green")
            self.comms_lbl.config(text=text or "OK", foreground="#276749")
        else:
            self.comms_dot.config(foreground="red")
            self.comms_lbl.config(text=text or "Comm Error", foreground="#C53030")

    def _start_monitor(self):
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _monitor_loop(self):
        while True:
            time.sleep(2)
            if not self.connected: continue
            ports = [p.device for p in serial.tools.list_ports.comports()]
            if self.connected_port not in ports:
                self.root.after(0, self._on_port_lost)

    def _on_port_lost(self):
        if not self.connected: return
        port = self.connected_port
        self._disconnect(lost=True)
        messagebox.showwarning("Connection Lost",
                               f"{port} was disconnected unexpectedly.")

    def _open_monitor(self):
        if self._serial_monitor and self._serial_monitor.winfo_exists():
            self._serial_monitor.lift(); return
        title = f"Serial Monitor \u2014 {self.connected_port}" if self.connected else "Serial Monitor"
        self._serial_monitor = SerialMonitor(self.root, title=title)
        self.root.update_idletasks()
        x = self.root.winfo_x() + self.root.winfo_width() + 6
        y = self.root.winfo_y()
        self._serial_monitor.geometry(f"+{x}+{y}")

    def _wrap_serial(self):
        sock = getattr(self.client, "socket", None)
        if not sock or not hasattr(sock, "read") or not hasattr(sock, "write"):
            return
        self._orig_serial_read  = sock.read
        self._orig_serial_write = sock.write
        mon = self

        def _read(size=1):
            data = mon._orig_serial_read(size)
            if data:
                m = mon._serial_monitor
                if m and m.winfo_exists(): m.log_rx(data)
            return data

        def _write(data):
            result = mon._orig_serial_write(data)
            m = mon._serial_monitor
            if m and m.winfo_exists():
                m.log_tx(bytes(data) if not isinstance(data, bytes) else data)
            return result

        sock.read  = _read
        sock.write = _write

    def _unwrap_serial(self):
        if self._orig_serial_read is None: return
        sock = getattr(self.client, "socket", None) if self.client else None
        if sock:
            try:
                sock.read  = self._orig_serial_read
                sock.write = self._orig_serial_write
            except Exception: pass
        self._orig_serial_read  = None
        self._orig_serial_write = None

    def _build_tabs(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=10, pady=10)
        f = ttk.Frame(nb, padding=12)
        nb.add(f, text="Generic")
        self._build_generic_tab(f)
        f2 = ttk.Frame(nb, padding=12)
        nb.add(f2, text="Custom")
        self._build_addr_tab(f2)
        f3 = ttk.Frame(nb, padding=12)
        nb.add(f3, text="Scan")
        self._build_scan_tab(f3)

    def _build_addr_tab(self, frm):
        cfg = ttk.LabelFrame(frm, text="Change Slave Address", padding=12)
        cfg.pack(fill="x")

        def make_vcmd(fmt_cb):
            def _v(action, proposed):
                if action != "1": return True  # allow delete, programmatic, focusout
                if not proposed: return True
                fmt = fmt_cb.get()
                if fmt == "Binary":  return all(c in "01" for c in proposed)
                elif fmt == "Hex":   return all(c in "0123456789abcdefABCDEF" for c in proposed)
                else:                return all(c.isdigit() for c in proposed)
            return (cfg.register(_v), "%d", "%P")

        def reformat(spin, old_fmt, new_fmt):
            raw = spin.get().strip()
            try:
                if old_fmt == "Hex":     val = int(raw, 16)
                elif old_fmt == "Binary": val = int(raw, 2)
                else:                    val = int(raw)
                if new_fmt == "Hex":     spin.set(f"{val:X}")
                elif new_fmt == "Binary": spin.set(f"{val:b}")
                else:                    spin.set(str(val))
            except (ValueError, tk.TclError):
                pass

        # Row 0 — Current Address
        ttk.Label(cfg, text="Current Address:").grid(row=0, column=0, sticky="w", padx=(0,4))
        self._addr_cur_fmt = ttk.Combobox(cfg, values=["Decimal","Hex","Binary"],
                                          width=8, state="readonly")
        self._addr_cur_fmt.set("Decimal")
        self._addr_cur_fmt.grid(row=0, column=2, sticky="w", padx=(4,20))
        self._addr_cur_entry = ttk.Spinbox(cfg, from_=1, to=247, width=9)
        self._addr_cur_entry.set("1")
        self._addr_cur_entry.config(validate="all", validatecommand=make_vcmd(self._addr_cur_fmt))
        self._addr_cur_entry.grid(row=0, column=1, sticky="w")
        _cur_p = ["Decimal"]
        def _on_cur_fmt(e, p=_cur_p):
            old = p[0]; new = self._addr_cur_fmt.get()
            if old != new: reformat(self._addr_cur_entry, old, new)
            p[0] = new
        self._addr_cur_fmt.bind("<<ComboboxSelected>>", _on_cur_fmt)

        # Row 0 — New Address
        ttk.Label(cfg, text="New Address:").grid(row=0, column=3, sticky="w", padx=(0,4))
        self._addr_new_fmt = ttk.Combobox(cfg, values=["Decimal","Hex","Binary"],
                                          width=8, state="readonly")
        self._addr_new_fmt.set("Decimal")
        self._addr_new_fmt.grid(row=0, column=5, sticky="w", padx=(4,0))
        self._addr_new_entry = ttk.Spinbox(cfg, from_=1, to=247, width=9)
        self._addr_new_entry.set("2")
        self._addr_new_entry.config(validate="all", validatecommand=make_vcmd(self._addr_new_fmt))
        self._addr_new_entry.grid(row=0, column=4, sticky="w")
        _new_p = ["Decimal"]
        def _on_new_fmt(e, p=_new_p):
            old = p[0]; new = self._addr_new_fmt.get()
            if old != new: reformat(self._addr_new_entry, old, new)
            p[0] = new
        self._addr_new_fmt.bind("<<ComboboxSelected>>", _on_new_fmt)

        # Row 1 — Address Register
        ttk.Label(cfg, text="Address Register:").grid(row=1, column=0, sticky="w", padx=(0,4), pady=(10,0))
        self._addr_reg_fmt = ttk.Combobox(cfg, values=["Decimal","Hex","Binary"],
                                          width=8, state="readonly")
        self._addr_reg_fmt.set("Decimal")
        self._addr_reg_fmt.grid(row=1, column=2, sticky="w", padx=(4,20), pady=(10,0))
        self._addr_reg_entry = ttk.Spinbox(cfg, from_=0, to=65535, width=9)
        self._addr_reg_entry.set("0")
        self._addr_reg_entry.config(validate="all", validatecommand=make_vcmd(self._addr_reg_fmt))
        self._addr_reg_entry.grid(row=1, column=1, sticky="w", pady=(10,0))
        _reg_p = ["Decimal"]
        def _on_reg_fmt(e, p=_reg_p):
            old = p[0]; new = self._addr_reg_fmt.get()
            if old != new: reformat(self._addr_reg_entry, old, new)
            p[0] = new
        self._addr_reg_fmt.bind("<<ComboboxSelected>>", _on_reg_fmt)
        ttk.Label(cfg, text="(holding register that stores the slave address)",
                  foreground=_FG2).grid(row=1, column=3, columnspan=3,
                  sticky="w", pady=(10,0))

        self._bind_fmt_scroll(self._addr_cur_entry, self._addr_cur_fmt, 1, 247)
        self._bind_fmt_scroll(self._addr_new_entry, self._addr_new_fmt, 1, 247)
        self._bind_fmt_scroll(self._addr_reg_entry, self._addr_reg_fmt, 0, 65535)

        btn_row = ttk.Frame(frm)
        btn_row.pack(fill="x", pady=(12,6))
        self._addr_set_btn = ttk.Button(btn_row, text="Set & Verify",
                                        style="Teal.TButton",
                                        command=self._do_addr_set)
        self._addr_set_btn.pack(side="left", padx=(0,14))
        self._addr_dot = tk.Label(btn_row, text="\u25cf", font=("Segoe UI", 14),
                                  foreground="#CBD5E0", bg=_BG2)
        self._addr_dot.pack(side="left", padx=(0,4))
        self._addr_status = ttk.Label(btn_row, text="Idle", foreground=_FG2)
        self._addr_status.pack(side="left")

        # ── Change Register Value ────────────────────────────────────
        chg = ttk.LabelFrame(frm, text="Change Register Value", padding=12)
        chg.pack(fill="x", pady=(8,0))

        # Slave Address
        ttk.Label(chg, text="Slave Address:").grid(row=0, column=0, sticky="w", padx=(0,4))
        self._chg_slave_fmt = ttk.Combobox(chg, values=["Decimal","Hex"],
                                            width=8, state="readonly")
        self._chg_slave_fmt.set("Decimal")
        self._chg_slave_fmt.grid(row=0, column=2, sticky="w", padx=(4,20))
        self._chg_slave_spin = ttk.Spinbox(chg, from_=1, to=247, width=9)
        self._chg_slave_spin.set("1")
        self._chg_slave_spin.config(validate="all", validatecommand=make_vcmd(self._chg_slave_fmt))
        self._chg_slave_spin.grid(row=0, column=1, sticky="w")
        _cslave_p = ["Decimal"]
        def _on_cslave_fmt(e, p=_cslave_p):
            old = p[0]; new = self._chg_slave_fmt.get()
            if old != new: reformat(self._chg_slave_spin, old, new)
            p[0] = new
        self._chg_slave_fmt.bind("<<ComboboxSelected>>", _on_cslave_fmt)

        # Data Register
        ttk.Label(chg, text="Data Register:").grid(row=0, column=3, sticky="w", padx=(0,4))
        self._chg_reg_fmt = ttk.Combobox(chg, values=["Decimal","Hex"],
                                          width=8, state="readonly")
        self._chg_reg_fmt.set("Decimal")
        self._chg_reg_fmt.grid(row=0, column=5, sticky="w", padx=(4,0))
        self._chg_reg_spin = ttk.Spinbox(chg, from_=0, to=65535, width=9)
        self._chg_reg_spin.set("0")
        self._chg_reg_spin.config(validate="all", validatecommand=make_vcmd(self._chg_reg_fmt))
        self._chg_reg_spin.grid(row=0, column=4, sticky="w")
        _creg_p = ["Decimal"]
        def _on_creg_fmt(e, p=_creg_p):
            old = p[0]; new = self._chg_reg_fmt.get()
            if old != new: reformat(self._chg_reg_spin, old, new)
            p[0] = new
        self._chg_reg_fmt.bind("<<ComboboxSelected>>", _on_creg_fmt)

        # Value
        ttk.Label(chg, text="Value:").grid(row=1, column=0, sticky="w", padx=(0,4), pady=(10,0))
        self._chg_val_fmt = ttk.Combobox(chg, values=["Decimal","Hex"],
                                          width=8, state="readonly")
        self._chg_val_fmt.set("Decimal")
        self._chg_val_fmt.grid(row=1, column=2, sticky="w", padx=(4,20), pady=(10,0))
        self._chg_val_spin = ttk.Spinbox(chg, from_=0, to=65535, width=9)
        self._chg_val_spin.set("0")
        self._chg_val_spin.config(validate="all", validatecommand=make_vcmd(self._chg_val_fmt))
        self._chg_val_spin.grid(row=1, column=1, sticky="w", pady=(10,0))
        _cval_p = ["Decimal"]
        def _on_cval_fmt(e, p=_cval_p):
            old = p[0]; new = self._chg_val_fmt.get()
            if old != new: reformat(self._chg_val_spin, old, new)
            p[0] = new
        self._chg_val_fmt.bind("<<ComboboxSelected>>", _on_cval_fmt)

        self._bind_fmt_scroll(self._chg_slave_spin, self._chg_slave_fmt, 1, 247)
        self._bind_fmt_scroll(self._chg_reg_spin,   self._chg_reg_fmt,   0, 65535)
        self._bind_fmt_scroll(self._chg_val_spin,   self._chg_val_fmt,   0, 65535)

        chg_btn_row = ttk.Frame(frm)
        chg_btn_row.pack(fill="x", pady=(8,0))
        self._chg_btn = ttk.Button(chg_btn_row, text="Write",
                                    style="Teal.TButton",
                                    command=self._do_chg_reg)
        self._chg_btn.pack(side="left", padx=(0,14))
        self._chg_dot = tk.Label(chg_btn_row, text="\u25cf", font=("Segoe UI", 14),
                                  foreground="#CBD5E0", bg=_BG2)
        self._chg_dot.pack(side="left", padx=(0,4))
        self._chg_status = ttk.Label(chg_btn_row, text="Idle", foreground=_FG2)
        self._chg_status.pack(side="left")

        # ── Save Command ─────────────────────────────────────────────
        sav = ttk.LabelFrame(frm, text="Save Command", padding=12)
        sav.pack(fill="x", pady=(8,0))

        # Slave Address
        ttk.Label(sav, text="Slave Address:").grid(row=0, column=0, sticky="w", padx=(0,4))
        self._save_slave_fmt = ttk.Combobox(sav, values=["Decimal","Hex"],
                                            width=8, state="readonly")
        self._save_slave_fmt.set("Decimal")
        self._save_slave_fmt.grid(row=0, column=2, sticky="w", padx=(4,20))
        self._save_slave_spin = ttk.Spinbox(sav, from_=1, to=247, width=9)
        self._save_slave_spin.set("1")
        self._save_slave_spin.config(validate="all", validatecommand=make_vcmd(self._save_slave_fmt))
        self._save_slave_spin.grid(row=0, column=1, sticky="w")
        _sslave_p = ["Decimal"]
        def _on_sslave_fmt(e, p=_sslave_p):
            old = p[0]; new = self._save_slave_fmt.get()
            if old != new: reformat(self._save_slave_spin, old, new)
            p[0] = new
        self._save_slave_fmt.bind("<<ComboboxSelected>>", _on_sslave_fmt)

        # Save Register
        ttk.Label(sav, text="Save Register:").grid(row=0, column=3, sticky="w", padx=(0,4))
        self._save_reg_fmt = ttk.Combobox(sav, values=["Decimal","Hex"],
                                          width=8, state="readonly")
        self._save_reg_fmt.set("Decimal")
        self._save_reg_fmt.grid(row=0, column=5, sticky="w", padx=(4,0))
        self._save_reg_spin = ttk.Spinbox(sav, from_=0, to=65535, width=9)
        self._save_reg_spin.set("0")
        self._save_reg_spin.config(validate="all", validatecommand=make_vcmd(self._save_reg_fmt))
        self._save_reg_spin.grid(row=0, column=4, sticky="w")
        _sreg_p = ["Decimal"]
        def _on_sreg_fmt(e, p=_sreg_p):
            old = p[0]; new = self._save_reg_fmt.get()
            if old != new: reformat(self._save_reg_spin, old, new)
            p[0] = new
        self._save_reg_fmt.bind("<<ComboboxSelected>>", _on_sreg_fmt)

        # Save Value
        ttk.Label(sav, text="Value:").grid(row=1, column=0, sticky="w", padx=(0,4), pady=(10,0))
        self._save_val_fmt = ttk.Combobox(sav, values=["Decimal","Hex"],
                                          width=8, state="readonly")
        self._save_val_fmt.set("Decimal")
        self._save_val_fmt.grid(row=1, column=2, sticky="w", padx=(4,20), pady=(10,0))
        self._save_val_spin = ttk.Spinbox(sav, from_=0, to=65535, width=9)
        self._save_val_spin.set("0")
        self._save_val_spin.config(validate="all", validatecommand=make_vcmd(self._save_val_fmt))
        self._save_val_spin.grid(row=1, column=1, sticky="w", pady=(10,0))
        _sval_p = ["Decimal"]
        def _on_sval_fmt(e, p=_sval_p):
            old = p[0]; new = self._save_val_fmt.get()
            if old != new: reformat(self._save_val_spin, old, new)
            p[0] = new
        self._save_val_fmt.bind("<<ComboboxSelected>>", _on_sval_fmt)

        self._bind_fmt_scroll(self._save_slave_spin, self._save_slave_fmt, 1, 247)
        self._bind_fmt_scroll(self._save_reg_spin,   self._save_reg_fmt,   0, 65535)
        self._bind_fmt_scroll(self._save_val_spin,   self._save_val_fmt,   0, 65535)

        sav_btn_row = ttk.Frame(frm)
        sav_btn_row.pack(fill="x", pady=(8,6))
        self._save_btn = ttk.Button(sav_btn_row, text="Send Save",
                                    style="Teal.TButton",
                                    command=self._do_save_cmd)
        self._save_btn.pack(side="left", padx=(0,14))
        self._save_dot = tk.Label(sav_btn_row, text="\u25cf", font=("Segoe UI", 14),
                                  foreground="#CBD5E0", bg=_BG2)
        self._save_dot.pack(side="left", padx=(0,4))
        self._save_status = ttk.Label(sav_btn_row, text="Idle", foreground=_FG2)
        self._save_status.pack(side="left")

        self._addr_log = scrolledtext.ScrolledText(frm, height=8, font=("Consolas", 9),
            bg=_BG, fg=_FG, insertbackground=_FG,
            selectbackground=_SEL, selectforeground=_FG)
        self._addr_log.pack(fill="both", expand=True, pady=(8,0))
        self._addr_log.vbar.configure(bg=_BG2, troughcolor=_BG, activebackground="#5A5A5A",
                                      relief="flat", borderwidth=0, highlightthickness=0)

    def _do_addr_set(self):
        if not self.connected or not self.client:
            self._addr_log.insert(tk.END, "\u26a0 Not connected\n")
            self._addr_log.see(tk.END); return

        def parse(entry, fmt_cb):
            raw = entry.get().strip()
            fmt = fmt_cb.get()
            if fmt == "Hex":    return int(raw, 16)
            elif fmt == "Binary": return int(raw, 2)
            else:               return int(raw)

        try:
            cur = parse(self._addr_cur_entry, self._addr_cur_fmt)
            new = parse(self._addr_new_entry, self._addr_new_fmt)
            reg = parse(self._addr_reg_entry, self._addr_reg_fmt)
        except ValueError:
            self._addr_log.insert(tk.END, "\u26a0 Invalid input\n")
            self._addr_log.see(tk.END); return

        if not (1 <= cur <= 247):
            self._addr_log.insert(tk.END, f"\u26a0 Current address {cur} out of range (1\u2013247)\n")
            self._addr_log.see(tk.END); return
        if not (1 <= new <= 247):
            self._addr_log.insert(tk.END, f"\u26a0 New address {new} out of range (1\u2013247)\n")
            self._addr_log.see(tk.END); return
        if not (0 <= reg <= 65535):
            self._addr_log.insert(tk.END, f"\u26a0 Register {reg} out of range (0\u201365535)\n")
            self._addr_log.see(tk.END); return
        if cur == new:
            self._addr_log.insert(tk.END, "\u26a0 Current and new address are the same\n")
            self._addr_log.see(tk.END); return

        self._addr_set_btn.config(state="disabled")
        self._addr_dot.config(foreground="#ECC94B")
        self._addr_status.config(text="Writing...", foreground="#744210")
        threading.Thread(target=self._addr_set_worker,
                         args=(cur, new, reg), daemon=True).start()

    def _addr_set_worker(self, cur, new, reg):
        def log(msg):
            self.root.after(0, lambda m=msg: (
                self._addr_log.insert(tk.END, m + "\n"),
                self._addr_log.see(tk.END)))

        def finish(ok, text):
            self.root.after(0, lambda: (
                self._addr_dot.config(foreground="green" if ok else "red"),
                self._addr_status.config(text=text,
                    foreground="#276749" if ok else "#C53030"),
                self._addr_set_btn.config(state="normal")))

        log(f"\u27a4 Writing  slave={cur}  reg={reg}  value={new}")
        try:
            r = self.client.write_register(reg, new, **{_SLAVE_KW: cur})
            if hasattr(r, "isError") and r.isError():
                log(f"\u274c Write error: {r}")
                finish(False, "Write failed"); return
        except Exception as e:
            log(f"\u274c Could not send write command: {e}")
            finish(False, "Write failed"); return

        log(f"\u2705 Write sent \u2014 waiting for device to apply...")
        time.sleep(0.5)

        log(f"\u27a4 Verifying  slave={new}  reg={reg}  count=1")
        try:
            r = self.client.read_holding_registers(reg, count=1, **{_SLAVE_KW: new})
            if not (hasattr(r, "isError") and r.isError()):
                vals = getattr(r, "registers", None)
                if vals and int(vals[0]) == new:
                    log(f"\u2705 Verified! Device at address {new} returned {vals[0]}")
                    finish(True, f"Address set to {new}"); return
                elif vals:
                    log(f"\u26a0 Device responded but returned {vals[0]} (expected {new})")
                    finish(False, "Unexpected response"); return
        except Exception:
            pass

        log(f"\u26a0 No response from address {new} \u2014 device may still have changed")
        finish(False, f"No verify response from {new}")

    def _do_save_cmd(self):
        if not self.connected or not self.client:
            self._addr_log.insert(tk.END, "\u26a0 Not connected\n")
            self._addr_log.see(tk.END); return

        def parse(spin, fmt_cb):
            raw = spin.get().strip()
            fmt = fmt_cb.get()
            if fmt == "Hex": return int(raw, 16)
            else:            return int(raw)

        try:
            slave = parse(self._save_slave_spin, self._save_slave_fmt)
            reg   = parse(self._save_reg_spin,   self._save_reg_fmt)
            val   = parse(self._save_val_spin,   self._save_val_fmt)
        except ValueError:
            self._addr_log.insert(tk.END, "\u26a0 Invalid input\n")
            self._addr_log.see(tk.END); return

        if not (1 <= slave <= 247):
            self._addr_log.insert(tk.END, f"\u26a0 Slave {slave} out of range (1\u2013247)\n")
            self._addr_log.see(tk.END); return
        if not (0 <= reg <= 65535):
            self._addr_log.insert(tk.END, f"\u26a0 Register {reg} out of range (0\u201365535)\n")
            self._addr_log.see(tk.END); return
        self._save_btn.config(state="disabled")
        self._save_dot.config(foreground="#ECC94B")
        self._save_status.config(text="Sending...", foreground="#744210")
        threading.Thread(target=self._save_cmd_worker,
                         args=(slave, reg, val), daemon=True).start()

    def _save_cmd_worker(self, slave, reg, val):
        def log(msg):
            self.root.after(0, lambda m=msg: (
                self._addr_log.insert(tk.END, m + "\n"),
                self._addr_log.see(tk.END)))

        def finish(ok, text):
            self.root.after(0, lambda: (
                self._save_dot.config(foreground="green" if ok else "red"),
                self._save_status.config(text=text,
                    foreground="#276749" if ok else "#C53030"),
                self._save_btn.config(state="normal")))

        log(f"\u27a4 Save  slave={slave}  reg={reg}  value={val}")
        try:
            r = self.client.write_register(reg, val, **{_SLAVE_KW: slave})
            if hasattr(r, "isError") and r.isError():
                log(f"\u274c Save error: {r}")
                finish(False, "Save failed"); return
            log(f"\u2705 Save command sent")
            finish(True, "Saved")
        except Exception as e:
            log(f"\u274c Could not send save command: {e}")
            finish(False, "Save failed")

    def _do_chg_reg(self):
        if not self.connected or not self.client:
            self._addr_log.insert(tk.END, "\u26a0 Not connected\n")
            self._addr_log.see(tk.END); return

        def parse(spin, fmt_cb):
            raw = spin.get().strip()
            fmt = fmt_cb.get()
            if fmt == "Hex": return int(raw, 16)
            else:            return int(raw)

        try:
            slave = parse(self._chg_slave_spin, self._chg_slave_fmt)
            reg   = parse(self._chg_reg_spin,   self._chg_reg_fmt)
            val   = parse(self._chg_val_spin,   self._chg_val_fmt)
        except ValueError:
            self._addr_log.insert(tk.END, "\u26a0 Invalid input\n")
            self._addr_log.see(tk.END); return

        if not (1 <= slave <= 247):
            self._addr_log.insert(tk.END, f"\u26a0 Slave {slave} out of range (1\u2013247)\n")
            self._addr_log.see(tk.END); return
        if not (0 <= reg <= 65535):
            self._addr_log.insert(tk.END, f"\u26a0 Register {reg} out of range (0\u201365535)\n")
            self._addr_log.see(tk.END); return
        if not (0 <= val <= 65535):
            self._addr_log.insert(tk.END, f"\u26a0 Value {val} out of range (0\u201365535)\n")
            self._addr_log.see(tk.END); return

        self._chg_btn.config(state="disabled")
        self._chg_dot.config(foreground="#ECC94B")
        self._chg_status.config(text="Writing...", foreground="#744210")
        threading.Thread(target=self._chg_reg_worker,
                         args=(slave, reg, val), daemon=True).start()

    def _chg_reg_worker(self, slave, reg, val):
        def log(msg):
            self.root.after(0, lambda m=msg: (
                self._addr_log.insert(tk.END, m + "\n"),
                self._addr_log.see(tk.END)))

        def finish(ok, text):
            self.root.after(0, lambda: (
                self._chg_dot.config(foreground="green" if ok else "red"),
                self._chg_status.config(text=text,
                    foreground="#276749" if ok else "#C53030"),
                self._chg_btn.config(state="normal")))

        log(f"\u27a4 Write  slave={slave}  reg={reg}  value={val}")
        try:
            r = self.client.write_register(reg, val, **{_SLAVE_KW: slave})
            if hasattr(r, "isError") and r.isError():
                log(f"\u274c Write error: {r}")
                finish(False, "Write failed"); return
            log(f"\u2705 Register written")
            finish(True, "Written")
        except Exception as e:
            log(f"\u274c Could not write register: {e}")
            finish(False, "Write failed")

    def _build_scan_tab(self, frm):
        # ── Scan Range ───────────────────────────────────────────────────
        rng = ttk.LabelFrame(frm, text="Scan Range", padding=12)
        rng.pack(fill="x")
        ttk.Label(rng, text="From:").grid(row=0, column=0, sticky="w", padx=(0,4))
        self._scan_from_spin = ttk.Spinbox(rng, from_=1, to=247, width=6)
        self._scan_from_spin.set("1")
        self._scan_from_spin.grid(row=0, column=1, sticky="w", padx=(0,20))
        ttk.Label(rng, text="To:").grid(row=0, column=2, sticky="w", padx=(0,4))
        self._scan_to_spin = ttk.Spinbox(rng, from_=1, to=247, width=6)
        self._scan_to_spin.set("247")
        self._scan_to_spin.grid(row=0, column=3, sticky="w", padx=(0,20))
        ttk.Label(rng, text="Timeout per probe (ms):").grid(row=0, column=4, sticky="w", padx=(0,4))
        self._scan_timeout_spin = ttk.Spinbox(rng, from_=100, to=5000, increment=100, width=6)
        self._scan_timeout_spin.set("500")
        self._scan_timeout_spin.grid(row=0, column=5, sticky="w")

        # ── Baud Rates ──────────────────────────────────────────────────
        baud_frm = ttk.LabelFrame(frm, text="Baud Rates", padding=12)
        baud_frm.pack(fill="x", pady=(8,0))
        self._scan_baud_vars = {}
        for i, br in enumerate(["1200","2400","4800","9600","19200","38400","57600","115200"]):
            var = tk.BooleanVar(value=False)
            self._scan_baud_vars[br] = var
            ttk.Checkbutton(baud_frm, text=br, variable=var).grid(row=0, column=i, padx=(0,12), sticky="w")

        # ── Parity ───────────────────────────────────────────────────────
        par_frm = ttk.LabelFrame(frm, text="Parity", padding=12)
        par_frm.pack(fill="x", pady=(8,0))
        self._scan_parity_vars = {}
        for i, (par, lbl) in enumerate([("N","None (N)"),("E","Even (E)"),("O","Odd (O)")]):
            var = tk.BooleanVar(value=False)
            self._scan_parity_vars[par] = var
            ttk.Checkbutton(par_frm, text=lbl, variable=var).grid(row=0, column=i, padx=(0,20), sticky="w")

        # ── Stop Bits ───────────────────────────────────────────────────
        stop_frm = ttk.LabelFrame(frm, text="Stop Bits", padding=12)
        stop_frm.pack(fill="x", pady=(8,0))
        self._scan_stop_vars = {}
        for i, sb in enumerate(["1","1.5","2"]):
            var = tk.BooleanVar(value=False)
            self._scan_stop_vars[sb] = var
            ttk.Checkbutton(stop_frm, text=sb, variable=var).grid(row=0, column=i, padx=(0,20), sticky="w")

        self._scan_pretick()

        # ── Progress ───────────────────────────────────────────────────
        prog_row = ttk.Frame(frm)
        prog_row.pack(fill="x", pady=(12,0))
        self._scan_btn = ttk.Button(prog_row, text="Start Scan", style="Teal.TButton",
                                    command=self._do_scan)
        self._scan_btn.pack(side="left", padx=(0,12))
        self._scan_progress = ttk.Progressbar(prog_row, mode="determinate", length=220)
        self._scan_progress.pack(side="left", padx=(0,10))
        self._scan_prog_lbl = ttk.Label(prog_row, text="", foreground=_FG2)
        self._scan_prog_lbl.pack(side="left")

        # ── Results ────────────────────────────────────────────────────
        res_frm = ttk.Frame(frm)
        res_frm.pack(fill="both", expand=True, pady=(12,0))
        cols = ("addr_dec","addr_hex","baud","parity","stopbits","response")
        self._scan_tree = ttk.Treeview(res_frm, columns=cols, show="headings", height=6)
        for col, hdr, w in [
                ("addr_dec","Addr (dec)",85), ("addr_hex","Addr (hex)",85),
                ("baud","Baud",80), ("parity","Parity",65), ("stopbits","Stop bits",70),
                ("response","Response",220)]:
            self._scan_tree.heading(col, text=hdr)
            self._scan_tree.column(col, width=w, anchor="center")
        self._scan_tree.tag_configure("found", background="#1A3A2A", foreground="#9CD49C")
        tree_vs = ttk.Scrollbar(res_frm, orient="vertical", command=self._scan_tree.yview)
        self._scan_tree.configure(yscrollcommand=tree_vs.set)
        self._scan_tree.pack(side="left", fill="both", expand=True)
        tree_vs.pack(side="right", fill="y")

        # ── Log ────────────────────────────────────────────────────────
        self._scan_log = scrolledtext.ScrolledText(frm, height=6, font=("Consolas", 9),
            bg=_BG, fg=_FG, insertbackground=_FG,
            selectbackground=_SEL, selectforeground=_FG)
        self._scan_log.pack(fill="x", pady=(8,0))
        self._scan_log.vbar.configure(bg=_BG2, troughcolor=_BG, activebackground="#5A5A5A",
                                      relief="flat", borderwidth=0, highlightthickness=0)

    def _scan_pretick(self):
        cur_baud = self.baud_cb.get()
        if cur_baud in self._scan_baud_vars:
            self._scan_baud_vars[cur_baud].set(True)
        cur_parity = self.parity_cb.get()
        if cur_parity in self._scan_parity_vars:
            self._scan_parity_vars[cur_parity].set(True)
        cur_stop = self.stopbits_cb.get()
        if cur_stop in self._scan_stop_vars:
            self._scan_stop_vars[cur_stop].set(True)

    def _scan_log_msg(self, msg):
        self._scan_log.insert(tk.END, msg + "\n")
        self._scan_log.see(tk.END)

    def _do_scan(self):
        if self._scan_running:
            self._scan_running = False
            return
        if not self.connected or not self.client:
            self._scan_log_msg("⚠ Not connected — connect to a port first")
            return
        bauds    = [b for b, v in self._scan_baud_vars.items()   if v.get()]
        parities = [p for p, v in self._scan_parity_vars.items() if v.get()]
        stops    = [s for s, v in self._scan_stop_vars.items()   if v.get()]
        if not bauds or not parities or not stops:
            self._scan_log_msg("⚠ Select at least one option in each group"); return
        try:
            addr_from  = int(self._scan_from_spin.get())
            addr_to    = int(self._scan_to_spin.get())
            timeout_ms = int(self._scan_timeout_spin.get())
        except ValueError:
            self._scan_log_msg("⚠ Invalid scan range or timeout value"); return
        if addr_from > addr_to:
            self._scan_log_msg("⚠ 'From' must be ≤ 'To'"); return
        for row in self._scan_tree.get_children():
            self._scan_tree.delete(row)
        self._scan_log.delete("1.0", tk.END)
        combos = [(b, p, s) for b in bauds for p in parities for s in stops]
        total  = len(combos) * (addr_to - addr_from + 1)
        self._scan_progress.configure(maximum=total, value=0)
        self._scan_prog_lbl.config(text="Starting…", foreground=_FG2)
        self._scan_running = True
        self._scan_btn.config(text="Stop Scan", style="Red.TButton")
        threading.Thread(target=self._scan_worker,
                         args=(combos, addr_from, addr_to, timeout_ms),
                         daemon=True).start()

    def _scan_worker(self, combos, addr_from, addr_to, timeout_ms):
        def log(msg):
            self.root.after(0, lambda m=msg: self._scan_log_msg(m))

        def add_result(addr, baud, parity, stop, response):
            self.root.after(0, lambda: self._scan_tree.insert(
                "", "end",
                values=(str(addr), f"0x{addr:02X}", baud, parity, stop, response),
                tags=("found",)))

        def set_progress(val, label):
            self.root.after(0, lambda: (
                self._scan_progress.configure(value=val),
                self._scan_prog_lbl.config(text=label, foreground=_FG2)))

        orig_port   = self.connected_port
        orig_baud   = self.baud_cb.get()
        orig_parity = self.parity_cb.get()
        orig_stop   = self.stopbits_cb.get()
        stop_map    = {"1": 1, "1.5": 1.5, "2": 2}
        cur_baud, cur_parity, cur_stop = orig_baud, orig_parity, orig_stop
        step = 0
        found_count = 0

        def _reconnect(baud, parity, stop, timeout_s):
            nonlocal cur_baud, cur_parity, cur_stop
            try:
                self._unwrap_serial()
                self.client.close()
                kw = dict(port=orig_port, baudrate=int(baud),
                          bytesize=int(self.databits_cb.get()),
                          stopbits=stop_map.get(stop, 1),
                          parity=parity, timeout=timeout_s, retries=0)
                try:    self.client = ModbusSerialClient(method="rtu", **kw)
                except TypeError: self.client = ModbusSerialClient(**kw)
                if self.client.connect():
                    cur_baud, cur_parity, cur_stop = baud, parity, stop
                    self._wrap_serial()
                    m = self._serial_monitor
                    if m and m.winfo_exists():
                        m.log_info(f"Scan: baud={baud}  parity={parity}  stop={stop}")
                    return True
            except Exception as e:
                log(f"❌ Reconnect error: {e}")
            return False

        for baud, parity, stop in combos:
            if not self._scan_running: break
            if baud != cur_baud or parity != cur_parity or stop != cur_stop:
                log(f"↻ Connecting: baud={baud}  parity={parity}  stop={stop}")
                if not _reconnect(baud, parity, stop, timeout_ms / 1000):
                    log(f"⚠ Skipping baud={baud} par={parity} stop={stop}")
                    step += (addr_to - addr_from + 1)
                    set_progress(step, f"Skipped {baud}/{parity}/{stop}")
                    continue
            log(f"── Scanning: baud={baud}  parity={parity}  stop={stop} ──")
            for addr in range(addr_from, addr_to + 1):
                if not self._scan_running: break
                set_progress(step, f"baud={baud} par={parity} stop={stop}  addr={addr}")
                try:
                    r = self.client.read_holding_registers(0, count=1, **{_SLAVE_KW: addr})
                    if r is not None:
                        found_count += 1
                        if hasattr(r, "isError") and r.isError():
                            resp = f"Exception code {getattr(r, 'exception_code', '?')}"
                        else:
                            regs = getattr(r, "registers", [])
                            resp = f"OK — reg[0]={regs[0] if regs else '?'}"
                        log(f"✅ Found  addr={addr}  {baud}/{parity}/{stop}  → {resp}")
                        add_result(addr, baud, parity, stop, resp)
                except Exception as e:
                    log(f"⚠ addr={addr}: {e}")
                step += 1

        # Restore original connection if settings were changed
        if cur_baud != orig_baud or cur_parity != orig_parity or cur_stop != orig_stop:
            log(f"↻ Restoring: baud={orig_baud}  parity={orig_parity}  stop={orig_stop}")
            if _reconnect(orig_baud, orig_parity, orig_stop, 1.0):
                log("✅ Original connection restored")
            else:
                log("⚠ Could not restore original connection")
                self.root.after(0, lambda: self._disconnect())

        was_stopped = not self._scan_running

        def _done():
            self._scan_running = False
            self._scan_btn.config(text="Start Scan", style="Teal.TButton")
            if was_stopped:
                self._scan_prog_lbl.config(text="Stopped", foreground=_FG2)
            else:
                self._scan_prog_lbl.config(
                    text=f"Done — {found_count} device(s) found",
                    foreground="#276749" if found_count else _FG2)

        self.root.after(0, _done)

    def _build_generic_tab(self, frm):
        ctrl = ttk.Frame(frm)
        ctrl.pack(fill="x")

        ttk.Label(ctrl, text="Slave Address:").grid(row=0, column=0, sticky="w", padx=(0,4))
        self.slave_spin = ttk.Spinbox(ctrl, from_=1, to=247, width=7)
        self.slave_spin.set(1)
        self.slave_spin.grid(row=0, column=1, sticky="w", padx=(0,4))
        self.slave_fmt = ttk.Combobox(ctrl, values=["Decimal","Hex","Binary"], width=8, state="readonly")
        self.slave_fmt.set("Decimal")
        self.slave_fmt.grid(row=0, column=2, sticky="w", padx=(0,20))

        ttk.Label(ctrl, text="Function Code:").grid(row=0, column=3, sticky="w", padx=(0,4))
        self.fc_cb = ttk.Combobox(ctrl, width=32, state="readonly", values=[
            "01 — Read Coils",
            "02 — Read Discrete Inputs",
            "03 — Read Holding Registers",
            "04 — Read Input Registers",
            "05 — Write Single Coil",
            "06 — Write Single Register",
            "15 — Write Multiple Coils",
            "16 — Write Multiple Registers",
        ])
        self.fc_cb.set("03 — Read Holding Registers")
        self.fc_cb.grid(row=0, column=4, sticky="w")
        self.fc_cb.bind("<<ComboboxSelected>>", self._on_fc_change)

        ttk.Label(ctrl, text="Start Address:").grid(row=1, column=0, sticky="w", padx=(0,4), pady=(8,0))
        self.addr_spin = ttk.Spinbox(ctrl, from_=0, to=65535, width=7)
        self.addr_spin.set(0)
        self.addr_spin.grid(row=1, column=1, sticky="w", padx=(0,4), pady=(8,0))
        self.addr_fmt = ttk.Combobox(ctrl, values=["Decimal","Hex","Binary"], width=8, state="readonly")
        self.addr_fmt.set("Decimal")
        self.addr_fmt.grid(row=1, column=2, sticky="w", padx=(0,20), pady=(8,0))

        ttk.Label(ctrl, text="Count:").grid(row=1, column=3, sticky="w", padx=(0,4), pady=(8,0))
        self.count_spin = ttk.Spinbox(ctrl, from_=1, to=125, width=5, command=self._rebuild_cells)
        self.count_spin.set(2)
        self.count_spin.grid(row=1, column=4, sticky="w", padx=(0,20), pady=(8,0))
        self.count_spin.bind("<FocusOut>", lambda e: self._rebuild_cells())
        self.count_spin.bind("<Return>",   lambda e: self._rebuild_cells())

        ttk.Label(ctrl, text="Data Format:").grid(row=1, column=5, sticky="w", padx=(0,4), pady=(8,0))
        self.data_fmt = ttk.Combobox(ctrl, values=["Decimal","Hex","Binary"], width=8, state="readonly")
        self.data_fmt.set("Decimal")
        self.data_fmt.grid(row=1, column=6, sticky="w", pady=(8,0))
        self.data_fmt.bind("<<ComboboxSelected>>", lambda e: self._refresh_cell_display())

        # ── Spin field validation & format conversion ──────────────────
        def _make_spin_vcmd(fmt_cb):
            def _v(action, proposed):
                if action != "1": return True  # allow delete, programmatic, focusout
                if not proposed: return True
                fmt = fmt_cb.get()
                if fmt == "Binary":  return all(c in "01" for c in proposed)
                elif fmt == "Hex":   return all(c in "0123456789abcdefABCDEF" for c in proposed)
                else:                return all(c.isdigit() for c in proposed)
            return (ctrl.register(_v), "%d", "%P")

        def _reformat(spin, old_fmt, new_fmt):
            raw = spin.get().strip()
            try:
                if old_fmt == "Hex":     val = int(raw, 16)
                elif old_fmt == "Binary": val = int(raw, 2)
                else:                    val = int(raw)
                if new_fmt == "Hex":     spin.set(f"{val:X}")
                elif new_fmt == "Binary": spin.set(f"{val:b}")
                else:                    spin.set(str(val))
            except (ValueError, tk.TclError):
                pass

        self.slave_spin.config(validate="all", validatecommand=_make_spin_vcmd(self.slave_fmt))
        _sp = ["Decimal"]
        def _on_sfmt(e, p=_sp):
            old = p[0]; new = self.slave_fmt.get()
            if old != new: _reformat(self.slave_spin, old, new)
            p[0] = new
        self.slave_fmt.bind("<<ComboboxSelected>>", _on_sfmt)

        self.addr_spin.config(validate="key", validatecommand=_make_spin_vcmd(self.addr_fmt))
        _ap = ["Decimal"]
        def _on_afmt(e, p=_ap):
            old = p[0]; new = self.addr_fmt.get()
            if old != new: _reformat(self.addr_spin, old, new)
            p[0] = new
        self.addr_fmt.bind("<<ComboboxSelected>>", _on_afmt)

        self._bind_fmt_scroll(self.slave_spin, self.slave_fmt, 1, 247)
        self._bind_fmt_scroll(self.addr_spin,  self.addr_fmt,  0, 65535)

        count_vcmd = (ctrl.register(lambda a, p: a != "1" or not p or p.isdigit()), "%d", "%P")
        self.count_spin.config(validate="all", validatecommand=count_vcmd)

        # ── Cell write-mode validation ──────────────────────────────────
        def _vcell(proposed):
            if not proposed: return True
            fmt = self.data_fmt.get()
            if fmt == "Binary":  return all(c in "01" for c in proposed)
            elif fmt == "Hex":   return all(c in "0123456789abcdefABCDEF" for c in proposed)
            else:                return all(c.isdigit() for c in proposed)
        self._cell_vcmd = (frm.register(_vcell), "%P")

        self._polling = False
        self._poll_thread = None
        self._reading = False

        btn_row = ttk.Frame(frm)
        btn_row.pack(fill="x", pady=(10,6))

        self._read_btn = ttk.Button(btn_row, text="Read", style="Teal.TButton",
                                    command=self._do_read)
        self._read_btn.pack(side="left", padx=(0,6))

        self._write_btn = ttk.Button(btn_row, text="Write", style="Teal.TButton",
                                     command=self._do_write)
        self._write_btn.pack(side="left", padx=(0,14))
        self._write_btn.config(state="disabled")

        self._poll_btn = ttk.Button(btn_row, text="⟳", style="Teal.TButton",
                                    width=3, command=self._toggle_polling)
        self._poll_btn.pack(side="left", padx=(0,14))

        ttk.Label(btn_row, text="Interval (ms):").pack(side="left", padx=(0,4))
        self.interval_spin = ttk.Spinbox(btn_row, from_=500, to=60000,
                                         increment=500, width=7)
        self.interval_spin.set(2000)
        self.interval_spin.pack(side="left", padx=(0,14))

        ttk.Button(btn_row, text="Clear Cells", style="Teal.TButton",
                   command=self._clear_cells).pack(side="left")

        cell_frame = ttk.LabelFrame(frm, text="Cells", padding=8)
        cell_frame.pack(fill="both", expand=True)
        self._cell_canvas = tk.Canvas(cell_frame, highlightthickness=0, height=180, bg=_BG)
        self._cell_canvas.pack(side="left", fill="both", expand=True)
        vsb = ttk.Scrollbar(cell_frame, orient="vertical", command=self._cell_canvas.yview)
        vsb.pack(side="right", fill="y")
        hsb = ttk.Scrollbar(cell_frame, orient="horizontal", command=self._cell_canvas.xview)
        hsb.pack(side="bottom", fill="x")
        self._cell_canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._cell_inner = ttk.Frame(self._cell_canvas)
        self._cell_canvas.create_window((0,0), window=self._cell_inner, anchor="nw")
        self._cell_inner.bind("<Configure>", lambda e: self._cell_canvas.configure(
            scrollregion=self._cell_canvas.bbox("all")))
        self._cell_labels = []
        self._cell_values = []
        self._rebuild_cells()

        self._log = scrolledtext.ScrolledText(frm, height=5, font=("Consolas", 9),
            bg=_BG, fg=_FG, insertbackground=_FG,
            selectbackground=_SEL, selectforeground=_FG)
        self._log.pack(fill="x", pady=(8,0))
        self._log.vbar.configure(bg=_BG2, troughcolor=_BG, activebackground="#5A5A5A",
                                 relief="flat", borderwidth=0, highlightthickness=0)

    CELLS_PER_ROW = 10

    def _rebuild_cells(self):
        for w in self._cell_inner.winfo_children(): w.destroy()
        self._cell_labels = []
        try: count = int(self.count_spin.get())
        except (ValueError, AttributeError): count = 2
        try:
            afmt = self.addr_fmt.get()
            start = int(self.addr_spin.get(), 16 if afmt == "Hex" else 2 if afmt == "Binary" else 10)
        except (ValueError, AttributeError): start = 0

        try:
            fc = int(self.fc_cb.get().split()[0].strip())
        except (AttributeError, ValueError, IndexError):
            fc = 3
        write_mode = fc in (5, 6, 15, 16)

        C = self.CELLS_PER_ROW
        for i in range(count):
            col = i % C; base_row = (i // C) * 2; addr = start + i
            hdr = tk.Label(self._cell_inner, text=str(addr),
                           font=("Consolas", 8), bg=_CELL_HDR_BG, fg=_CELL_HDR_FG,
                           width=8, anchor="center", relief="flat", pady=2)
            hdr.grid(row=base_row, column=col, padx=1, pady=(2,0), sticky="nsew")
            if write_mode:
                val = tk.Entry(self._cell_inner, font=("Consolas", 10, "bold"),
                               bg=_CELL_WR_BG, fg=_CELL_WR_FG, width=8,
                               insertbackground=_FG, selectbackground=_SEL,
                               justify="center", relief="flat")
                val.insert(0, "0")
            else:
                val = tk.Label(self._cell_inner, text="—",
                               font=("Consolas", 10, "bold"), bg=_CELL_VAL_BG, fg=_CELL_VAL_FG,
                               width=8, anchor="center", relief="flat", pady=4)
            val.grid(row=base_row+1, column=col, padx=1, pady=(0,2), sticky="nsew")
            self._cell_labels.append((hdr, val))

        for c in range(C): self._cell_inner.grid_columnconfigure(c, weight=1)
        self._cell_values = [None] * count
        self._cell_canvas.update_idletasks()
        self._cell_canvas.configure(scrollregion=self._cell_canvas.bbox("all"))

    def _refresh_cell_display(self):
        fmt = self.data_fmt.get()
        for i, (_, val) in enumerate(self._cell_labels):
            if isinstance(val, tk.Entry): continue
            v = self._cell_values[i] if i < len(self._cell_values) else None
            if v is not None: val.config(text=to_display(v, fmt))

    def _clear_cells(self):
        self._cell_values = [None] * len(self._cell_labels)
        for _, val in self._cell_labels:
            if isinstance(val, tk.Entry):
                val.delete(0, tk.END); val.insert(0, "0")
                val.config(bg="#FFFBEB", fg="#744210")
            else:
                val.config(text="—", bg="#EDF2F7")

    def _toggle_polling(self):
        if self._polling:
            self._polling = False
            self._poll_btn.config(text="⟳", style="Teal.TButton")
        else:
            self._polling = True
            self._poll_btn.config(text="⏹", style="Red.TButton")
            self._poll_thread = threading.Thread(
                target=self._poll_loop, daemon=True)
            self._poll_thread.start()

    def _poll_loop(self):
        while self._polling:
            self.root.after(0, self._do_read)
            try:
                interval = int(self.interval_spin.get())
            except ValueError:
                interval = 2000
            time.sleep(max(interval, 500) / 1000)

    def _log_msg(self, msg):
        self._log.insert(tk.END, msg + "\n")
        self._log.see(tk.END)

    def _do_read(self):
        if not self.connected or not self.client:
            self._log_msg("⚠ Not connected"); return
        if self._reading: return
        try:
            sfmt  = self.slave_fmt.get(); afmt = self.addr_fmt.get()
            slave = int(self.slave_spin.get(), 16 if sfmt=="Hex" else 2 if sfmt=="Binary" else 10)
            addr  = int(self.addr_spin.get(),  16 if afmt=="Hex" else 2 if afmt=="Binary" else 10)
            count = int(self.count_spin.get())
        except ValueError:
            self._log_msg("⚠ Invalid slave/address/count"); return

        fc_str = self.fc_cb.get()
        try: fc = int(fc_str.split()[0].strip())
        except ValueError:
            self._log_msg("⚠ Could not parse function code"); return

        fmt = self.data_fmt.get()
        self._rebuild_cells()
        self._reading = True
        threading.Thread(target=self._do_read_io,
                         args=(fc, addr, count, slave, fmt),
                         daemon=True).start()

    def _do_read_io(self, fc, addr, count, slave, fmt):
        try:
            result = self._read(fc, addr, count, slave)
            if result is None:
                self.root.after(0, lambda: self._log_msg("⚠ No response — check wiring, address, baud rate"))
                self.root.after(0, lambda: self._set_comms(False, "No response"))
                return
            if hasattr(result, "isError") and result.isError():
                self.root.after(0, lambda r=result: self._log_msg(f"❌ Modbus error: {r}"))
                self.root.after(0, lambda: self._set_comms(False, "Modbus error"))
                return

            values = getattr(result, "registers", None) or list(getattr(result, "bits", []))
            if not values:
                self.root.after(0, lambda: self._log_msg("⚠ Empty response"))
                return

            vals_snap = list(values[:len(self._cell_labels)])

            def _apply(vals=vals_snap, fmt=fmt, fc=fc, addr=addr, count=count, slave=slave):
                for i, v in enumerate(vals):
                    v_int = int(v); self._cell_values[i] = v_int
                    _, lbl = self._cell_labels[i]
                    lbl.config(text=to_display(v_int, fmt), bg="#093013")
                self._log_msg(f"✅ FC{fc:02d}  addr={addr}  count={count}  slave={slave}")
                self._set_comms(True, "OK")

            self.root.after(0, _apply)
        except Exception as e:
            self.root.after(0, lambda e=e: self._log_msg(f"❌ {e}"))
            self.root.after(0, lambda: self._set_comms(False, "Comm error"))
        finally:
            self._reading = False

    def _read(self, fc, addr, count, slave):
        fns = {1:"read_coils", 2:"read_discrete_inputs",
               3:"read_holding_registers", 4:"read_input_registers"}
        if fc not in fns: return None
        fn = getattr(self.client, fns[fc])
        kw = {_SLAVE_KW: slave}
        try:
            return fn(addr, count=count, **kw)
        except Exception:
            return None

    def _on_fc_change(self, event=None):
        fc_str = self.fc_cb.get()
        try: fc = int(fc_str.split()[0].strip())
        except (ValueError, IndexError): fc = 3
        write_mode = fc in (5, 6, 15, 16)
        single = fc in (5, 6)
        if write_mode:
            if self._polling: self._toggle_polling()
            self._read_btn.config(state="disabled")
            self._write_btn.config(state="normal")
            self._poll_btn.config(state="disabled")
            self.interval_spin.config(state="disabled")
            if single:
                self.count_spin.set(1)
                self.count_spin.config(state="disabled")
            else:
                self.count_spin.config(state="normal")
        else:
            self._read_btn.config(state="normal")
            self._write_btn.config(state="disabled")
            self._poll_btn.config(state="normal")
            self.interval_spin.config(state="normal")
            self.count_spin.config(state="normal")
        self._rebuild_cells()

    def _do_write(self):
        if not self.connected or not self.client:
            self._log_msg("⚠ Not connected"); return
        try:
            sfmt  = self.slave_fmt.get(); afmt = self.addr_fmt.get()
            slave = int(self.slave_spin.get(), 16 if sfmt=="Hex" else 2 if sfmt=="Binary" else 10)
            addr  = int(self.addr_spin.get(),  16 if afmt=="Hex" else 2 if afmt=="Binary" else 10)
        except ValueError:
            self._log_msg("⚠ Invalid slave/address"); return
        fc_str = self.fc_cb.get()
        try: fc = int(fc_str.split()[0].strip())
        except ValueError:
            self._log_msg("⚠ Could not parse function code"); return

        values = []
        for i, (_, widget) in enumerate(self._cell_labels):
            try:
                raw = widget.get().strip()
                v = int(raw, 0)
                if fc in (5, 15):
                    v = bool(v)
                elif not (0 <= v <= 65535):
                    self._log_msg(f"⚠ Cell {i}: value out of range (0–65535)"); return
                values.append(v)
            except (ValueError, AttributeError):
                self._log_msg(f"⚠ Cell {i}: invalid value '{widget.get()}'"); return

        try:
            result = self._write(fc, addr, values, slave)
            if result is None:
                self._log_msg("⚠ Write returned no response")
                self._set_comms(False, "No response"); return
            if hasattr(result, "isError") and result.isError():
                self._log_msg(f"❌ Modbus error: {result}")
                self._set_comms(False, "Modbus error"); return
            for _, widget in self._cell_labels:
                widget.config(bg="#C6F6D5", fg="#276749")
            self._log_msg(f"✅ FC{fc:02d}  addr={addr}  count={len(values)}  slave={slave}  → {values}")
            self._set_comms(True, "OK")
        except Exception as e:
            self._log_msg(f"❌ {e}")
            self._set_comms(False, "Comm error")

    def _write(self, fc, addr, values, slave):
        kw = {_SLAVE_KW: slave}
        try:
            if fc == 5:
                return self.client.write_coil(addr, values[0], **kw)
            elif fc == 6:
                return self.client.write_register(addr, values[0], **kw)
            elif fc == 15:
                return self.client.write_coils(addr, values, **kw)
            elif fc == 16:
                return self.client.write_registers(addr, values, **kw)
        except Exception:
            return None
        return None


if __name__ == "__main__":
    root = tk.Tk()
    app = EGMod(root)
    root.mainloop()