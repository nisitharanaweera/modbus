"""Simple Modbus RTU/TCP reader with configurable parameters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from pymodbus.client import ModbusSerialClient, ModbusTcpClient


@dataclass
class ModbusParams:
    mode: str = "rtu"  # "rtu" or "tcp"
    # Serial settings (RTU)
    port: str = "COM3"
    baudrate: int = 9600
    parity: str = "N"  # "N", "E", "O"
    stopbits: int = 1
    bytesize: int = 8
    # TCP settings
    host: str = "192.168.1.10"
    tcp_port: int = 502
    # Common settings
    unit_id: int = 1
    timeout: float = 1.0


@dataclass
class ReadRequest:
    function: str = "holding"  # "holding", "input", "coil", "discrete"
    address: int = 0
    count: int = 2


def _build_client(params: ModbusParams):
    if params.mode.lower() == "tcp":
        return ModbusTcpClient(
            host=params.host,
            port=params.tcp_port,
            timeout=params.timeout,
        )

    return ModbusSerialClient(
        port=params.port,
        baudrate=params.baudrate,
        parity=params.parity,
        stopbits=params.stopbits,
        bytesize=params.bytesize,
        timeout=params.timeout,
        method="rtu",
    )


def read_modbus(params: ModbusParams, request: ReadRequest) -> Optional[list[int] | list[bool]]:
    client = _build_client(params)
    if not client.connect():
        raise ConnectionError("Unable to connect to Modbus device.")

    try:
        func = request.function.lower()
        if func == "holding":
            result = client.read_holding_registers(
                request.address, request.count, unit=params.unit_id
            )
        elif func == "input":
            result = client.read_input_registers(
                request.address, request.count, unit=params.unit_id
            )
        elif func == "coil":
            result = client.read_coils(
                request.address, request.count, unit=params.unit_id
            )
        elif func == "discrete":
            result = client.read_discrete_inputs(
                request.address, request.count, unit=params.unit_id
            )
        else:
            raise ValueError("Unsupported function: " + request.function)

        if result.isError():
            raise RuntimeError(str(result))

        if func in {"coil", "discrete"}:
            return list(result.bits)
        return list(result.registers)
    finally:
        client.close()


def _run_ui() -> None:
    root = tk.Tk()
    root.title("Modbus Reader")

    main = ttk.Frame(root, padding=12)
    main.grid(row=0, column=0, sticky="nsew")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    for col in range(4):
        main.columnconfigure(col, weight=1)

    mode_var = tk.StringVar(value="rtu")
    port_var = tk.StringVar(value="COM3")
    baud_var = tk.StringVar(value="9600")
    parity_var = tk.StringVar(value="N")
    stopbits_var = tk.StringVar(value="1")
    bytesize_var = tk.StringVar(value="8")
    host_var = tk.StringVar(value="192.168.1.10")
    tcp_port_var = tk.StringVar(value="502")
    unit_id_var = tk.StringVar(value="1")
    timeout_var = tk.StringVar(value="1.0")

    func_var = tk.StringVar(value="holding")
    address_var = tk.StringVar(value="0")
    count_var = tk.StringVar(value="2")

    output_var = tk.StringVar(value="")

    def add_row(label: str, widget: tk.Widget, row: int, col: int = 0, col_span: int = 1) -> None:
        ttk.Label(main, text=label).grid(row=row, column=col, sticky="w", padx=(0, 6), pady=4)
        widget.grid(row=row, column=col + 1, columnspan=col_span, sticky="ew", pady=4)

    ttk.Label(main, text="Connection", font=("Segoe UI", 10, "bold")).grid(
        row=0, column=0, columnspan=4, sticky="w", pady=(0, 6)
    )
    add_row("Mode", ttk.Combobox(main, textvariable=mode_var, values=["rtu", "tcp"], state="readonly"), 1)
    add_row("Port", ttk.Entry(main, textvariable=port_var), 2)
    add_row("Baudrate", ttk.Entry(main, textvariable=baud_var), 3)
    add_row("Parity", ttk.Combobox(main, textvariable=parity_var, values=["N", "E", "O"], state="readonly"), 4)
    add_row("Stopbits", ttk.Entry(main, textvariable=stopbits_var), 5)
    add_row("Bytesize", ttk.Entry(main, textvariable=bytesize_var), 6)
    add_row("Host", ttk.Entry(main, textvariable=host_var), 7)
    add_row("TCP Port", ttk.Entry(main, textvariable=tcp_port_var), 8)
    add_row("Unit ID", ttk.Entry(main, textvariable=unit_id_var), 9)
    add_row("Timeout", ttk.Entry(main, textvariable=timeout_var), 10)

    ttk.Separator(main, orient="horizontal").grid(row=11, column=0, columnspan=4, sticky="ew", pady=8)
    ttk.Label(main, text="Read Request", font=("Segoe UI", 10, "bold")).grid(
        row=12, column=0, columnspan=4, sticky="w", pady=(0, 6)
    )
    add_row(
        "Function",
        ttk.Combobox(main, textvariable=func_var, values=["holding", "input", "coil", "discrete"], state="readonly"),
        13,
    )
    add_row("Address", ttk.Entry(main, textvariable=address_var), 14)
    add_row("Count", ttk.Entry(main, textvariable=count_var), 15)

    ttk.Separator(main, orient="horizontal").grid(row=16, column=0, columnspan=4, sticky="ew", pady=8)
    ttk.Label(main, text="Result").grid(row=17, column=0, sticky="w")
    output = ttk.Entry(main, textvariable=output_var, state="readonly")
    output.grid(row=17, column=1, columnspan=3, sticky="ew")

    def gather_params() -> ModbusParams:
        return ModbusParams(
            mode=mode_var.get().strip(),
            port=port_var.get().strip(),
            baudrate=int(baud_var.get()),
            parity=parity_var.get().strip().upper(),
            stopbits=int(stopbits_var.get()),
            bytesize=int(bytesize_var.get()),
            host=host_var.get().strip(),
            tcp_port=int(tcp_port_var.get()),
            unit_id=int(unit_id_var.get()),
            timeout=float(timeout_var.get()),
        )

    def gather_request() -> ReadRequest:
        return ReadRequest(
            function=func_var.get().strip(),
            address=int(address_var.get()),
            count=int(count_var.get()),
        )

    def run_read() -> None:
        try:
            params = gather_params()
            request = gather_request()
        except ValueError as exc:
            messagebox.showerror("Invalid Input", str(exc))
            return

        def task() -> None:
            try:
                values = read_modbus(params, request)
                output_text = "" if values is None else str(values)
                root.after(0, lambda: output_var.set(output_text))
            except Exception as exc:  # noqa: BLE001 - show in UI
                root.after(0, lambda: messagebox.showerror("Read Failed", str(exc)))

        threading.Thread(target=task, daemon=True).start()

    ttk.Button(main, text="Read", command=run_read).grid(row=18, column=0, sticky="w", pady=(8, 0))
    ttk.Button(main, text="Quit", command=root.destroy).grid(row=18, column=1, sticky="w", pady=(8, 0))

    def on_mode_change(*_args: object) -> None:
        is_tcp = mode_var.get().lower() == "tcp"
        serial_state = "disabled" if is_tcp else "normal"
        tcp_state = "normal" if is_tcp else "disabled"
        for child in main.winfo_children():
            if isinstance(child, ttk.Entry) or isinstance(child, ttk.Combobox):
                name = child.cget("textvariable")
                if name in {str(port_var), str(baud_var), str(parity_var), str(stopbits_var), str(bytesize_var)}:
                    child.configure(state=serial_state)
                if name in {str(host_var), str(tcp_port_var)}:
                    child.configure(state=tcp_state)

    mode_var.trace_add("write", on_mode_change)
    on_mode_change()

    root.mainloop()


if __name__ == "__main__":
    _run_ui()
