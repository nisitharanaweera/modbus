import serial
import serial.tools.list_ports
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


def select_com_port(ports):
    """Let the user pick a COM port by number."""
    while True:
        try:
            choice = int(input("\nSelect COM port number: "))
            if 1 <= choice <= len(ports):
                return ports[choice - 1].device
            print(f"Please enter a number between 1 and {len(ports)}.")
        except ValueError:
            print("Invalid input. Enter a number.")


def main():
    ports = list_com_ports()
    if not ports:
        sys.exit(1)

    com_port = select_com_port(ports)
    
    use_increment = input("Use incremental number as message? (y/n) [n]: ").strip().lower()
    incremental = use_increment in ("y", "yes")
    if incremental:
        start_num = input("Enter starting number [1]: ").strip()
        start_num = int(start_num) if start_num else 1
        message = None
    else:
        message = input("Enter message to send: ")
        start_num = None
    
    baudrate = input("Enter baud rate [9600]: ").strip()
    baudrate = int(baudrate) if baudrate else 9600
    interval = input("Enter interval in seconds [1.0]: ").strip()
    interval = float(interval) if interval else 1.0

    print(f"\nOpening {com_port} at {baudrate} baud...")
    try:
        ser = serial.Serial(com_port, baudrate=baudrate, timeout=1)
        if incremental:
            print(f"Connected. Sending incremental numbers (from {start_num}) every {interval}s. Press Ctrl+C to stop.\n")
        else:
            print(f"Connected. Sending \"{message}\" every {interval}s. Press Ctrl+C to stop.\n")
        count = 0
        while True:
            count += 1
            if incremental:
                current_msg = str(start_num + count - 1)
            else:
                current_msg = message
            data = (current_msg + "\n").encode("utf-8")
            ser.write(data)
            print(f"[{count}] Sent: {current_msg}")
            time.sleep(interval)
    except serial.SerialException as e:
        print(f"Serial error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print(f"\nStopped after {count} messages.")
    finally:
        if "ser" in locals() and ser.is_open:
            ser.close()
            print("Port closed.")


if __name__ == "__main__":
    main()
