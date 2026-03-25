import serial
import serial.tools.list_ports
import threading
import time
import sys


def list_com_ports():
    """List all available COM ports."""
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("No COM ports found.")
        return []
    print("\nAvailable COM ports:")
    for i, port in enumerate(ports, 1):
        print(f"  {i}. {port.device} - {port.description}")
    return ports


def select_com_port(ports, label):
    """Let the user pick a COM port by number."""
    while True:
        try:
            choice = int(input(f"\nSelect {label} COM port number: "))
            if 1 <= choice <= len(ports):
                return ports[choice - 1].device
            print(f"Please enter a number between 1 and {len(ports)}.")
        except ValueError:
            print("Invalid input. Enter a number.")


def select_data_format():
    """Let the user choose the data format."""
    print("\nData format:")
    print("  1. Text   (e.g. Hello World)")
    print("  2. Hex    (e.g. 48656C6C6F)")
    print("  3. Binary (e.g. 01001000 01100101)")
    while True:
        try:
            choice = int(input("Select format [1]: ").strip() or "1")
            if choice in (1, 2, 3):
                return {1: "text", 2: "hex", 3: "binary"}[choice]
            print("Please enter 1, 2, or 3.")
        except ValueError:
            print("Invalid input. Enter a number.")


def encode_message(message, fmt):
    """Convert user input to bytes based on the chosen format."""
    if fmt == "text":
        return message.encode("utf-8")
    elif fmt == "hex":
        hex_str = message.replace(" ", "").replace("0x", "").replace(",", "")
        if len(hex_str) % 2 != 0:
            raise ValueError("Hex string must have an even number of characters.")
        return bytes.fromhex(hex_str)
    elif fmt == "binary":
        bits = message.replace(" ", "")
        if len(bits) % 8 != 0:
            raise ValueError("Binary string length must be a multiple of 8.")
        if not all(c in "01" for c in bits):
            raise ValueError("Binary string must only contain 0 and 1.")
        return bytes(int(bits[i:i+8], 2) for i in range(0, len(bits), 8))


def format_received(data, fmt):
    """Format received bytes for display."""
    if fmt == "text":
        return data.decode("utf-8", errors="replace")
    elif fmt == "hex":
        return data.hex().upper()
    elif fmt == "binary":
        return " ".join(f"{b:08b}" for b in data)


def receiver_thread(ser_rx, expected_bytes, fmt, results, stop_event):
    """Read messages from the RX port."""
    msg_len = len(expected_bytes)
    buffer = b""
    while not stop_event.is_set():
        try:
            raw = ser_rx.read(ser_rx.in_waiting or 1)
            if raw:
                buffer += raw
                # For text mode, split on newline; for binary/hex, use fixed length
                if fmt == "text":
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        idx = results["received"]
                        results["received"] += 1
                        if line == expected_bytes.rstrip(b"\n").rstrip():
                            results["matched"] += 1
                        else:
                            results["corrupted"] += 1
                            results["errors"].append(
                                f"  #{idx + 1}: expected '{format_received(expected_bytes.rstrip(b'\\n'), fmt)}', "
                                f"got '{format_received(line, fmt)}'"
                            )
                        print(f"  [RX #{idx + 1}] {format_received(line, fmt)}")
                else:
                    while len(buffer) >= msg_len:
                        chunk = buffer[:msg_len]
                        buffer = buffer[msg_len:]
                        idx = results["received"]
                        results["received"] += 1
                        if chunk == expected_bytes:
                            results["matched"] += 1
                        else:
                            results["corrupted"] += 1
                            results["errors"].append(
                                f"  #{idx + 1}: expected '{format_received(expected_bytes, fmt)}', "
                                f"got '{format_received(chunk, fmt)}'"
                            )
                        print(f"  [RX #{idx + 1}] {format_received(chunk, fmt)}")
        except serial.SerialException:
            break


def run_test(tx_port, rx_port, tx_bytes, msg_bytes, expected_bytes_for_rx,
             fmt, num_messages, baudrate, interval, direction_label=""):
    """Run a single-direction send/receive test. Returns results dict."""
    if direction_label:
        print(f"\n{'#' * 50}")
        print(f"  {direction_label}")
        print(f"{'#' * 50}")

    print(f"\nOpening TX: {tx_port}, RX: {rx_port} at {baudrate} baud...")
    ser_tx = None
    ser_rx = None
    try:
        ser_tx = serial.Serial(tx_port, baudrate=baudrate, timeout=1)
        ser_rx = serial.Serial(rx_port, baudrate=baudrate, timeout=1)
    except serial.SerialException as e:
        print(f"Serial error: {e}")
        if ser_tx and ser_tx.is_open:
            ser_tx.close()
        return None

    results = {
        "received": 0,
        "matched": 0,
        "corrupted": 0,
        "errors": [],
    }
    stop_event = threading.Event()
    rx = threading.Thread(
        target=receiver_thread,
        args=(ser_rx, expected_bytes_for_rx, fmt, results, stop_event),
        daemon=True,
    )
    rx.start()

    print(f"\nSending {num_messages} messages ({fmt} format)...\n")
    for i in range(num_messages):
        ser_tx.write(tx_bytes)
        print(f"[TX #{i + 1}] {format_received(msg_bytes, fmt)}")
        if i < num_messages - 1:
            time.sleep(interval)

    print("\nWaiting for remaining responses...")
    time.sleep(max(2.0, interval * 3))
    stop_event.set()
    rx.join(timeout=3)

    ser_tx.close()
    ser_rx.close()

    return results


def print_results(tx_port, rx_port, baudrate, fmt, msg_bytes, num_messages, results, label=""):
    """Print test summary for one direction."""
    sent = num_messages
    received = results["received"]
    matched = results["matched"]
    corrupted = results["corrupted"]
    lost = sent - received

    print("\n" + "=" * 50)
    if label:
        print(f"  {label}")
    print("  RS-485 COMMUNICATION TEST RESULTS")
    print("=" * 50)
    print(f"  TX port:        {tx_port}")
    print(f"  RX port:        {rx_port}")
    print(f"  Baud rate:      {baudrate}")
    print(f"  Format:         {fmt}")
    print(f"  Message:        {format_received(msg_bytes, fmt)}")
    print(f"  Sent:           {sent}")
    print(f"  Received:       {received}")
    print(f"  Matched:        {matched}")
    print(f"  Corrupted:      {corrupted}")
    print(f"  Lost:           {lost}")
    if sent > 0:
        success_rate = (matched / sent) * 100
        print(f"  Success rate:   {success_rate:.1f}%")
    print("=" * 50)

    if results["errors"]:
        print(f"\nCorruption details (first {min(50, len(results['errors']))}):")
        for err in results["errors"][:50]:
            print(err)

    return matched, sent


def main():
    ports = list_com_ports()
    if not ports:
        sys.exit(1)

    # --- Test direction ---
    print("\nTest direction:")
    print("  1. Single direction (Port A -> Port B)")
    print("  2. Bidirectional   (Port A -> Port B, then Port B -> Port A)")
    dir_choice = input("Select direction [1]: ").strip() or "1"
    bidirectional = dir_choice == "2"

    port_a = select_com_port(ports, "Port A")
    port_b = select_com_port(ports, "Port B")

    if port_a == port_b:
        print("Error: Port A and Port B must be different.")
        sys.exit(1)

    # --- Data format ---
    fmt = select_data_format()

    # --- Message ---
    if fmt == "text":
        message = input("\nEnter text message [Hello there!]: ").strip() or "Hello there!"
    elif fmt == "hex":
        message = input("\nEnter hex string [ABABABABABAB]: ").strip() or "ABABABABABAB"
    elif fmt == "binary":
        message = input("\nEnter binary string [10110100]: ").strip() or "10110100"

    try:
        msg_bytes = encode_message(message, fmt)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    if fmt == "text":
        tx_bytes = msg_bytes + b"\n"
        expected_bytes_for_rx = msg_bytes + b"\n"
    else:
        tx_bytes = msg_bytes
        expected_bytes_for_rx = msg_bytes

    print(f"  Encoded ({len(msg_bytes)} bytes): {format_received(msg_bytes, 'hex')}")

    num_input = input("\nEnter number of messages to send [100]: ").strip()
    num_messages = int(num_input) if num_input else 100

    baud_options = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
    print("\nBaud rate options:")
    for i, b in enumerate(baud_options, 1):
        default_mark = " (default)" if b == 9600 else ""
        print(f"  {i}. {b}{default_mark}")
    baud_choice = input("Select baud rate [4]: ").strip()
    baud_idx = int(baud_choice) - 1 if baud_choice else 3
    if 0 <= baud_idx < len(baud_options):
        baudrate = baud_options[baud_idx]
    else:
        print("Invalid choice, using 9600.")
        baudrate = 9600

    interval = input("Enter interval between messages in seconds [0.02]: ").strip()
    interval = float(interval) if interval else 0.02

    # --- Direction 1: Port A -> Port B ---
    label1 = f"Direction 1: {port_a} -> {port_b}" if bidirectional else ""
    results1 = run_test(port_a, port_b, tx_bytes, msg_bytes, expected_bytes_for_rx,
                        fmt, num_messages, baudrate, interval, label1)
    if results1 is None:
        sys.exit(1)

    print_results(port_a, port_b, baudrate, fmt, msg_bytes, num_messages, results1,
                  label1)

    # --- Direction 2: Port B -> Port A (if bidirectional) ---
    if bidirectional:
        label2 = f"Direction 2: {port_b} -> {port_a}"
        results2 = run_test(port_b, port_a, tx_bytes, msg_bytes, expected_bytes_for_rx,
                            fmt, num_messages, baudrate, interval, label2)
        if results2 is None:
            sys.exit(1)

        print_results(port_b, port_a, baudrate, fmt, msg_bytes, num_messages, results2,
                      label2)

        # --- Combined summary ---
        total_sent = num_messages * 2
        total_matched = results1["matched"] + results2["matched"]
        total_corrupted = results1["corrupted"] + results2["corrupted"]
        total_received = results1["received"] + results2["received"]
        total_lost = total_sent - total_received

        print("\n" + "=" * 50)
        print("  BIDIRECTIONAL COMBINED SUMMARY")
        print("=" * 50)
        print(f"  Total sent:     {total_sent}")
        print(f"  Total received: {total_received}")
        print(f"  Total matched:  {total_matched}")
        print(f"  Total corrupted:{total_corrupted}")
        print(f"  Total lost:     {total_lost}")
        if total_sent > 0:
            print(f"  Overall rate:   {(total_matched / total_sent) * 100:.1f}%")
        print("=" * 50)

    print("\nDone.")


if __name__ == "__main__":
    main()
