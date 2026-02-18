#!/usr/bin/env python3
"""
Modbus RTU Network Scanner
===========================
A complete tool to scan Modbus RTU networks, discover connected devices,
and list their slave addresses along with working communication parameters.

Features:
  - Fully Automated Mode: sweeps all common baud/parity/stop/bytesize combos.
  - Semi-Automated Mode: user fixes known params; tool sweeps the rest.
  - Desktop GUI built with Tkinter (ttk themed).
  - Export results to CSV.

Dependencies:
  pip install pymodbus pyserial

Usage:
  python mod_scan.py
"""

from __future__ import annotations

import csv
import logging
import platform
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, List, Optional

import serial
import serial.tools.list_ports
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException
from pymodbus.pdu import ExceptionResponse
from pymodbus.exceptions import ModbusIOException

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("mod_scan")

# Suppress noisy pyserial / pymodbus internal error logs that fire when a
# USB-serial adapter rejects an unsupported parameter combination.
logging.getLogger("pymodbus").setLevel(logging.CRITICAL)
logging.getLogger("serial").setLevel(logging.CRITICAL)
logging.getLogger("pyserial").setLevel(logging.CRITICAL)

# ---------------------------------------------------------------------------
# Constants – common Modbus RTU parameters
# ---------------------------------------------------------------------------
COMMON_BAUD_RATES: list[int] = [9600, 19200, 38400, 57600, 115200, 4800, 2400, 1200]
COMMON_PARITIES: list[str] = ["N", "E", "O"]
COMMON_STOP_BITS: list[int] = [1, 2]
COMMON_BYTE_SIZES: list[int] = [8, 7]

SLAVE_ADDR_MIN = 1
SLAVE_ADDR_MAX = 247

PARITY_LABELS = {"N": "None", "E": "Even", "O": "Odd"}
PARITY_REVERSE = {v: k for k, v in PARITY_LABELS.items()}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class CommConfig:
    """A single set of serial communication parameters."""
    port: str
    baudrate: int
    parity: str        # "N", "E", "O"
    stopbits: int
    bytesize: int
    timeout: float = 0.5

    def label(self) -> str:
        return f"{self.baudrate}/{self.bytesize}{self.parity}{self.stopbits}"


@dataclass
class ScanResult:
    """Holds a single positive detection."""
    slave_address: int
    config: CommConfig

    def as_row(self) -> dict:
        return {
            "slave_address": self.slave_address,
            "port": self.config.port,
            "baudrate": self.config.baudrate,
            "parity": self.config.parity,
            "stopbits": self.config.stopbits,
            "bytesize": self.config.bytesize,
        }


@dataclass
class ScanOptions:
    """Options that control a scan session."""
    port: str = ""
    mode: str = "auto"                          # "auto" or "semi"
    baud_rates: list[int] = field(default_factory=lambda: list(COMMON_BAUD_RATES))
    parities: list[str] = field(default_factory=lambda: list(COMMON_PARITIES))
    stop_bits: list[int] = field(default_factory=lambda: list(COMMON_STOP_BITS))
    byte_sizes: list[int] = field(default_factory=lambda: list(COMMON_BYTE_SIZES))
    addr_start: int = SLAVE_ADDR_MIN
    addr_end: int = SLAVE_ADDR_MAX
    timeout: float = 0.5
    function_code: int = 3                      # read holding registers
    register_address: int = 0
    register_count: int = 1

    def build_configs(self) -> list[CommConfig]:
        """Generate all CommConfig permutations from the currently set lists."""
        configs: list[CommConfig] = []
        for baud in self.baud_rates:
            for parity in self.parities:
                for stop in self.stop_bits:
                    for bsize in self.byte_sizes:
                        configs.append(CommConfig(
                            port=self.port,
                            baudrate=baud,
                            parity=parity,
                            stopbits=stop,
                            bytesize=bsize,
                            timeout=self.timeout,
                        ))
        return configs


# ---------------------------------------------------------------------------
# Backend – Scanner engine
# ---------------------------------------------------------------------------
class ModbusScanner:
    """
    Core scanning engine.

    Iterates through communication configurations and slave addresses,
    detecting devices that respond to a Modbus read request.
    """

    def __init__(self, options: ScanOptions):
        self.options = options
        self._stop_event = threading.Event()
        self.results: list[ScanResult] = []

    # -- public API ---------------------------------------------------------

    def stop(self) -> None:
        """Request the running scan to stop."""
        self._stop_event.set()

    @property
    def stopped(self) -> bool:
        return self._stop_event.is_set()

    def scan(
        self,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        on_result: Optional[Callable[[ScanResult], None]] = None,
    ) -> list[ScanResult]:
        """
        Execute the scan.

        Parameters
        ----------
        on_progress : callback(current, total, message)
            Called after every probe attempt.
        on_result : callback(ScanResult)
            Called when a device answers.

        Returns
        -------
        list[ScanResult]
        """
        self.results.clear()
        self._stop_event.clear()

        configs = self.options.build_configs()
        addr_range = range(self.options.addr_start, self.options.addr_end + 1)
        total_probes = len(configs) * len(addr_range)
        current = 0

        for cfg in configs:
            if self.stopped:
                break

            client = self._make_client(cfg)
            connected = False

            try:
                connected = client.connect()
            except (PermissionError, OSError, serial.SerialException) as exc:
                # Driver rejected this parameter set (common on Windows with
                # USB-serial adapters that don't support e.g. 7-bit data).
                logger.debug("Port rejected config %s: %s", cfg.label(), exc)
                current += len(addr_range)
                if on_progress:
                    on_progress(current, total_probes,
                                f"Skip {cfg.label()} – unsupported by adapter")
                continue

            if not connected:
                logger.debug("Could not open port with config %s", cfg.label())
                current += len(addr_range)
                if on_progress:
                    on_progress(current, total_probes, f"Skip {cfg.label()} – port error")
                continue

            # Brief pause to let the serial port and remote device stabilise
            # after opening with new parameters.
            time.sleep(0.05)

            try:
                for addr in addr_range:
                    if self.stopped:
                        break
                    current += 1
                    msg = f"[{cfg.label()}] Probing address {addr}…"
                    logger.debug(msg)

                    if self._probe(client, addr):
                        result = ScanResult(slave_address=addr, config=cfg)
                        self.results.append(result)
                        logger.info("Device found: addr=%d  config=%s", addr, cfg.label())
                        if on_result:
                            on_result(result)

                    if on_progress:
                        on_progress(current, total_probes, msg)

            except Exception as exc:
                logger.warning("Error with config %s: %s", cfg.label(), exc)
                current += len(addr_range)
                if on_progress:
                    on_progress(current, total_probes, f"Error {cfg.label()}: {exc}")
            finally:
                try:
                    client.close()
                except Exception:
                    pass

        return self.results

    # -- internal helpers ---------------------------------------------------

    def _make_client(self, cfg: CommConfig) -> ModbusSerialClient:
        return ModbusSerialClient(
            port=cfg.port,
            baudrate=cfg.baudrate,
            parity=cfg.parity,
            stopbits=cfg.stopbits,
            bytesize=cfg.bytesize,
            timeout=cfg.timeout,
        )

    def _probe(self, client: ModbusSerialClient, address: int) -> bool:
        """Send a single read request and return True if the device answers.

        A device is considered present if it returns *any* response,
        including a Modbus exception response (e.g. illegal function /
        illegal data address).  Only a complete lack of response (timeout /
        IO error) is treated as "no device".
        """
        try:
            fc = self.options.function_code
            reg = self.options.register_address
            cnt = self.options.register_count

            if fc == 1:
                rr = client.read_coils(reg, count=cnt, device_id=address)
            elif fc == 2:
                rr = client.read_discrete_inputs(reg, count=cnt, device_id=address)
            elif fc == 3:
                rr = client.read_holding_registers(reg, count=cnt, device_id=address)
            elif fc == 4:
                rr = client.read_input_registers(reg, count=cnt, device_id=address)
            else:
                rr = client.read_holding_registers(reg, count=cnt, device_id=address)

            # A Modbus *exception response* (e.g. 0x83 illegal function)
            # still proves a device exists at this address.
            if isinstance(rr, ExceptionResponse):
                logger.debug("Device at %d replied with exception code %d",
                             address, rr.exception_code)
                return True

            # A ModbusIOException means no reply at all (timeout).
            if isinstance(rr, ModbusIOException):
                return False

            # rr.isError() can flag both comms errors and exception
            # responses depending on the pymodbus version.  Check for
            # the presence of registers/bits as the definitive test.
            if hasattr(rr, 'registers') or hasattr(rr, 'bits'):
                return True

            # Fallback: if it has an isError() that is False → device ok
            if not rr.isError():
                return True

            return False

        except ModbusIOException:
            return False
        except (ModbusException, OSError, Exception):
            return False


# ---------------------------------------------------------------------------
# CSV export helper
# ---------------------------------------------------------------------------
def export_results_csv(results: list[ScanResult], filepath: str) -> None:
    """Write scan results to a CSV file."""
    fieldnames = ["slave_address", "port", "baudrate", "parity", "stopbits", "bytesize"]
    with open(filepath, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow(r.as_row())
    logger.info("Results exported to %s", filepath)


# ---------------------------------------------------------------------------
# Serial port detection helper
# ---------------------------------------------------------------------------
def list_serial_ports() -> list[str]:
    """Return a sorted list of available serial port names."""
    ports = serial.tools.list_ports.comports()
    return sorted(p.device for p in ports)


# ---------------------------------------------------------------------------
# GUI Application
# ---------------------------------------------------------------------------
class ModbusScannerApp(tk.Tk):
    """Tkinter-based desktop GUI for the Modbus RTU scanner."""

    # Visual constants
    PAD = 8
    WINDOW_MIN_W = 900
    WINDOW_MIN_H = 660

    def __init__(self) -> None:
        super().__init__()
        self.title("Modbus RTU Network Scanner")
        self.minsize(self.WINDOW_MIN_W, self.WINDOW_MIN_H)
        self._center_window(self.WINDOW_MIN_W, self.WINDOW_MIN_H)

        # State
        self._scanner: Optional[ModbusScanner] = None
        self._scan_thread: Optional[threading.Thread] = None
        self._results: list[ScanResult] = []

        # Tkinter variables
        self._var_port = tk.StringVar()
        self._var_mode = tk.StringVar(value="auto")
        self._var_baud = tk.StringVar(value="9600")
        self._var_parity = tk.StringVar(value="None")
        self._var_stop = tk.StringVar(value="1")
        self._var_bytesize = tk.StringVar(value="8")
        self._var_addr_start = tk.StringVar(value="1")
        self._var_addr_end = tk.StringVar(value="247")
        self._var_timeout = tk.StringVar(value="0.5")
        self._var_func_code = tk.StringVar(value="3 – Read Holding Registers")
        self._var_reg_addr = tk.StringVar(value="0")
        self._var_reg_count = tk.StringVar(value="1")

        # Build UI
        self._apply_style()
        self._build_menu()
        self._build_widgets()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- styling ------------------------------------------------------------

    def _apply_style(self) -> None:
        style = ttk.Style(self)
        available = style.theme_names()
        for preferred in ("clam", "vista", "xpnative", "alt"):
            if preferred in available:
                style.theme_use(preferred)
                break

        style.configure("TLabel", padding=2)
        style.configure("TButton", padding=4)
        style.configure("Header.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Status.TLabel", font=("Segoe UI", 9))
        style.configure("Found.TLabel", font=("Segoe UI", 10, "bold"), foreground="#1a7f37")
        style.configure("Treeview", rowheight=24, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    # -- menu ---------------------------------------------------------------

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Export CSV…", command=self._export_csv)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menubar)

    # -- widget construction ------------------------------------------------

    def _build_widgets(self) -> None:
        main = ttk.Frame(self, padding=self.PAD)
        main.pack(fill=tk.BOTH, expand=True)

        # Top: settings panel
        settings_frame = ttk.LabelFrame(main, text="  Scan Settings  ", padding=self.PAD)
        settings_frame.pack(fill=tk.X, pady=(0, self.PAD))
        self._build_settings(settings_frame)

        # Middle: results table
        results_frame = ttk.LabelFrame(main, text="  Results  ", padding=self.PAD)
        results_frame.pack(fill=tk.BOTH, expand=True, pady=(0, self.PAD))
        self._build_results_table(results_frame)

        # Bottom: status bar
        status_frame = ttk.Frame(main)
        status_frame.pack(fill=tk.X)
        self._build_status_bar(status_frame)

    def _build_settings(self, parent: ttk.Frame) -> None:
        # Row 0 – port & mode
        row0 = ttk.Frame(parent)
        row0.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(row0, text="Serial Port:").pack(side=tk.LEFT)
        self._cmb_port = ttk.Combobox(row0, textvariable=self._var_port, width=14, state="readonly")
        self._cmb_port.pack(side=tk.LEFT, padx=(4, 16))
        self._refresh_ports()

        btn_refresh = ttk.Button(row0, text="⟳ Refresh", width=10, command=self._refresh_ports)
        btn_refresh.pack(side=tk.LEFT, padx=(0, 24))

        ttk.Label(row0, text="Mode:").pack(side=tk.LEFT)
        ttk.Radiobutton(row0, text="Fully Automated", variable=self._var_mode, value="auto",
                        command=self._on_mode_change).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Radiobutton(row0, text="Semi-Automated", variable=self._var_mode, value="semi",
                        command=self._on_mode_change).pack(side=tk.LEFT, padx=(0, 24))

        # Scan / Stop buttons
        self._btn_scan = ttk.Button(row0, text="▶  Start Scan", command=self._start_scan)
        self._btn_scan.pack(side=tk.RIGHT, padx=(8, 0))
        self._btn_stop = ttk.Button(row0, text="■  Stop", command=self._stop_scan, state=tk.DISABLED)
        self._btn_stop.pack(side=tk.RIGHT)

        # Row 1 – semi-auto parameters (conditionally enabled)
        self._semi_frame = ttk.Frame(parent)
        self._semi_frame.pack(fill=tk.X, pady=(4, 0))

        # Baud
        ttk.Label(self._semi_frame, text="Baud Rate:").grid(row=0, column=0, sticky=tk.W, padx=(0, 4))
        self._cmb_baud = ttk.Combobox(
            self._semi_frame, textvariable=self._var_baud, width=10,
            values=[str(b) for b in COMMON_BAUD_RATES], state="readonly",
        )
        self._cmb_baud.grid(row=0, column=1, padx=(0, 16))

        # Parity
        ttk.Label(self._semi_frame, text="Parity:").grid(row=0, column=2, sticky=tk.W, padx=(0, 4))
        self._cmb_parity = ttk.Combobox(
            self._semi_frame, textvariable=self._var_parity, width=8,
            values=list(PARITY_LABELS.values()), state="readonly",
        )
        self._cmb_parity.grid(row=0, column=3, padx=(0, 16))

        # Stop bits
        ttk.Label(self._semi_frame, text="Stop Bits:").grid(row=0, column=4, sticky=tk.W, padx=(0, 4))
        self._cmb_stop = ttk.Combobox(
            self._semi_frame, textvariable=self._var_stop, width=5,
            values=["1", "2"], state="readonly",
        )
        self._cmb_stop.grid(row=0, column=5, padx=(0, 16))

        # Byte size
        ttk.Label(self._semi_frame, text="Byte Size:").grid(row=0, column=6, sticky=tk.W, padx=(0, 4))
        self._cmb_bytesize = ttk.Combobox(
            self._semi_frame, textvariable=self._var_bytesize, width=5,
            values=["8", "7"], state="readonly",
        )
        self._cmb_bytesize.grid(row=0, column=7, padx=(0, 16))

        # Row 2 – address range, timeout, function code
        row2 = ttk.Frame(parent)
        row2.pack(fill=tk.X, pady=(8, 0))

        ttk.Label(row2, text="Address Range:").pack(side=tk.LEFT)
        ent_start = ttk.Entry(row2, textvariable=self._var_addr_start, width=6)
        ent_start.pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(row2, text="–").pack(side=tk.LEFT, padx=2)
        ent_end = ttk.Entry(row2, textvariable=self._var_addr_end, width=6)
        ent_end.pack(side=tk.LEFT, padx=(0, 16))

        ttk.Label(row2, text="Timeout (s):").pack(side=tk.LEFT)
        ent_timeout = ttk.Entry(row2, textvariable=self._var_timeout, width=6)
        ent_timeout.pack(side=tk.LEFT, padx=(4, 16))

        ttk.Label(row2, text="Function Code:").pack(side=tk.LEFT)
        self._cmb_fc = ttk.Combobox(
            row2, textvariable=self._var_func_code, width=28, state="readonly",
            values=[
                "1 – Read Coils",
                "2 – Read Discrete Inputs",
                "3 – Read Holding Registers",
                "4 – Read Input Registers",
            ],
        )
        self._cmb_fc.pack(side=tk.LEFT, padx=(4, 16))

        ttk.Label(row2, text="Reg Addr:").pack(side=tk.LEFT)
        ent_reg = ttk.Entry(row2, textvariable=self._var_reg_addr, width=6)
        ent_reg.pack(side=tk.LEFT, padx=(4, 8))

        ttk.Label(row2, text="Count:").pack(side=tk.LEFT)
        ent_cnt = ttk.Entry(row2, textvariable=self._var_reg_count, width=5)
        ent_cnt.pack(side=tk.LEFT, padx=(4, 0))

        # Initial semi state
        self._on_mode_change()

    def _build_results_table(self, parent: ttk.Frame) -> None:
        columns = ("addr", "port", "baud", "parity", "stop", "bytesize")
        self._tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")

        headings = {
            "addr": ("Slave Address", 110),
            "port": ("Port", 90),
            "baud": ("Baud Rate", 90),
            "parity": ("Parity", 70),
            "stop": ("Stop Bits", 75),
            "bytesize": ("Byte Size", 75),
        }
        for col, (heading, width) in headings.items():
            self._tree.heading(col, text=heading)
            self._tree.column(col, width=width, anchor=tk.CENTER)

        vsb = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_status_bar(self, parent: ttk.Frame) -> None:
        self._lbl_status = ttk.Label(parent, text="Ready.", style="Status.TLabel", anchor=tk.W)
        self._lbl_status.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._lbl_found = ttk.Label(parent, text="Found: 0", style="Found.TLabel", anchor=tk.E)
        self._lbl_found.pack(side=tk.RIGHT, padx=(8, 0))

        self._progress = ttk.Progressbar(parent, orient=tk.HORIZONTAL, length=260, mode="determinate")
        self._progress.pack(side=tk.RIGHT, padx=(8, 0))

    # -- port helpers -------------------------------------------------------

    def _refresh_ports(self) -> None:
        ports = list_serial_ports()
        self._cmb_port["values"] = ports
        if ports:
            if self._var_port.get() not in ports:
                self._var_port.set(ports[0])
        else:
            self._var_port.set("")

    # -- mode toggle --------------------------------------------------------

    def _on_mode_change(self) -> None:
        is_semi = self._var_mode.get() == "semi"
        state = "readonly" if is_semi else "disabled"
        for w in (self._cmb_baud, self._cmb_parity, self._cmb_stop, self._cmb_bytesize):
            w.configure(state=state)

    # -- scan lifecycle -----------------------------------------------------

    def _validate_inputs(self) -> Optional[ScanOptions]:
        """Parse & validate GUI inputs into a ScanOptions. Returns None on error."""
        port = self._var_port.get().strip()
        if not port:
            messagebox.showerror("Input Error", "Please select a serial port.")
            return None

        try:
            addr_start = int(self._var_addr_start.get())
            addr_end = int(self._var_addr_end.get())
            if not (1 <= addr_start <= 247 and 1 <= addr_end <= 247 and addr_start <= addr_end):
                raise ValueError
        except ValueError:
            messagebox.showerror("Input Error", "Address range must be integers between 1 and 247.")
            return None

        try:
            timeout = float(self._var_timeout.get())
            if timeout <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Input Error", "Timeout must be a positive number.")
            return None

        try:
            reg_addr = int(self._var_reg_addr.get())
            reg_count = int(self._var_reg_count.get())
            if reg_addr < 0 or reg_count < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Input Error", "Register address must be ≥ 0 and count ≥ 1.")
            return None

        fc_str = self._var_func_code.get()
        fc = int(fc_str.split("–")[0].strip()) if "–" in fc_str else 3

        opts = ScanOptions(
            port=port,
            addr_start=addr_start,
            addr_end=addr_end,
            timeout=timeout,
            function_code=fc,
            register_address=reg_addr,
            register_count=reg_count,
        )

        if self._var_mode.get() == "semi":
            opts.mode = "semi"
            opts.baud_rates = [int(self._var_baud.get())]
            opts.parities = [PARITY_REVERSE.get(self._var_parity.get(), "N")]
            opts.stop_bits = [int(self._var_stop.get())]
            opts.byte_sizes = [int(self._var_bytesize.get())]
        else:
            opts.mode = "auto"

        return opts

    def _start_scan(self) -> None:
        if self._scan_thread and self._scan_thread.is_alive():
            return

        opts = self._validate_inputs()
        if opts is None:
            return

        # Clear previous results
        self._results.clear()
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._lbl_found.config(text="Found: 0")

        configs = opts.build_configs()
        total = len(configs) * (opts.addr_end - opts.addr_start + 1)
        self._progress["maximum"] = total
        self._progress["value"] = 0

        self._btn_scan.config(state=tk.DISABLED)
        self._btn_stop.config(state=tk.NORMAL)
        self._set_status(f"Scanning {opts.port}  ({len(configs)} config(s), "
                         f"addresses {opts.addr_start}–{opts.addr_end})…")

        self._scanner = ModbusScanner(opts)
        self._scan_thread = threading.Thread(target=self._scan_worker, daemon=True)
        self._scan_thread.start()

    def _stop_scan(self) -> None:
        if self._scanner:
            self._scanner.stop()
        self._set_status("Stopping…")

    def _scan_worker(self) -> None:
        """Runs on a background thread."""
        assert self._scanner is not None

        def on_progress(current: int, total: int, msg: str) -> None:
            self.after(0, self._update_progress, current, total, msg)

        def on_result(result: ScanResult) -> None:
            self.after(0, self._add_result, result)

        try:
            self._scanner.scan(on_progress=on_progress, on_result=on_result)
        except Exception as exc:
            self.after(0, lambda: messagebox.showerror("Scan Error", str(exc)))
        finally:
            self.after(0, self._scan_finished)

    def _update_progress(self, current: int, total: int, msg: str) -> None:
        self._progress["value"] = current
        self._set_status(msg)

    def _add_result(self, result: ScanResult) -> None:
        self._results.append(result)
        cfg = result.config
        self._tree.insert("", tk.END, values=(
            result.slave_address, cfg.port, cfg.baudrate,
            PARITY_LABELS.get(cfg.parity, cfg.parity),
            cfg.stopbits, cfg.bytesize,
        ))
        self._lbl_found.config(text=f"Found: {len(self._results)}")
        # Auto-scroll to latest
        children = self._tree.get_children()
        if children:
            self._tree.see(children[-1])

    def _scan_finished(self) -> None:
        stopped = self._scanner.stopped if self._scanner else False
        n = len(self._results)
        if stopped:
            self._set_status(f"Scan stopped by user. {n} device(s) found.")
        else:
            self._set_status(f"Scan complete. {n} device(s) found.")
        self._btn_scan.config(state=tk.NORMAL)
        self._btn_stop.config(state=tk.DISABLED)
        self._progress["value"] = self._progress["maximum"]

    # -- CSV export ---------------------------------------------------------

    def _export_csv(self) -> None:
        if not self._results:
            messagebox.showinfo("Export", "No results to export.")
            return
        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export Results",
        )
        if filepath:
            try:
                export_results_csv(self._results, filepath)
                messagebox.showinfo("Export", f"Results exported to:\n{filepath}")
            except Exception as exc:
                messagebox.showerror("Export Error", str(exc))

    # -- helpers ------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        self._lbl_status.config(text=text)

    def _center_window(self, w: int, h: int) -> None:
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _show_about(self) -> None:
        messagebox.showinfo(
            "About",
            "Modbus RTU Network Scanner\n\n"
            "Scans a Modbus RTU bus to discover connected\n"
            "slave devices and their communication parameters.\n\n"
            "Built with Python · pymodbus · pyserial · Tkinter",
        )

    def _on_close(self) -> None:
        if self._scan_thread and self._scan_thread.is_alive():
            if not messagebox.askokcancel("Quit", "A scan is running. Really quit?"):
                return
            if self._scanner:
                self._scanner.stop()
        self.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    app = ModbusScannerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
