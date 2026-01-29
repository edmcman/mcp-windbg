import subprocess
import threading
import re
import os
import platform
from typing import List, Optional

# Regular expression to detect CDB prompts
PROMPT_REGEX = re.compile(r"^\d+:\d+>\s*$")

# Command marker to reliably detect command completion
COMMAND_MARKER = ".echo COMMAND_COMPLETED_MARKER"
COMMAND_MARKER_PATTERN = re.compile(r"COMMAND_COMPLETED_MARKER")

# Default paths where cdb.exe might be located
DEFAULT_CDB_PATHS = [
    # Traditional Windows SDK locations
    r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe",
    r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x86\cdb.exe",
    r"C:\Program Files\Debugging Tools for Windows (x64)\cdb.exe",
    r"C:\Program Files\Debugging Tools for Windows (x86)\cdb.exe",

    # Microsoft Store WinDbg Preview locations (architecture-specific)
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\cdbX64.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\cdbX86.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\cdbARM64.exe")
]

class CDBError(Exception):
    """Custom exception for CDB-related errors"""
    pass

class CDBSession:
    def __init__(
        self,
        dump_path: Optional[str] = None,
        remote_connection: Optional[str] = None,
        cdb_path: Optional[str] = None,
        symbols_path: Optional[str] = None,
        initial_commands: Optional[List[str]] = None,
        timeout: int = 10,
        verbose: bool = False,
        attach_cmdline: Optional[str] = None,
        additional_args: Optional[List[str]] = None
    ):
        """
        Initialize a new CDB debugging session.

        Args:
            dump_path: Path to the crash dump file (mutually exclusive with remote_connection)
            remote_connection: Remote debugging connection string (e.g., "tcp:Port=5005,Server=192.168.0.100")
            cdb_path: Custom path to cdb.exe. If None, will try to find it automatically
            symbols_path: Custom symbols path. If None, uses default Windows symbols
            initial_commands: List of commands to run when CDB starts
            timeout: Timeout in seconds for waiting for CDB responses
            verbose: Whether to print additional debug information
            attach_cmdline: Optional command-line to run on the remote target (implies -premote)
            additional_args: Additional arguments to pass to cdb.exe

        Raises:
            CDBError: If cdb.exe cannot be found or started
            FileNotFoundError: If the dump file cannot be found
            ValueError: If invalid parameters are provided
        """
        # Validate that exactly one of dump_path or remote_connection is provided
        if not dump_path and not remote_connection:
            raise ValueError("Either dump_path or remote_connection must be provided")
        if dump_path and remote_connection:
            raise ValueError("dump_path and remote_connection are mutually exclusive")

        if dump_path and not os.path.isfile(dump_path):
            raise FileNotFoundError(f"Dump file not found: {dump_path}")

        self.dump_path = dump_path
        self.remote_connection = remote_connection
        self.timeout = timeout
        self.verbose = verbose
        self.attach_cmdline = attach_cmdline

        # Find cdb executable
        self.cdb_path = self._find_cdb_executable(cdb_path)
        if not self.cdb_path:
            raise CDBError("Could not find cdb.exe. Please provide a valid path.")

        # Prepare command args
        cmd_args = [self.cdb_path]

        # Add connection type specific arguments
        if self.dump_path:
            cmd_args.extend(["-z", self.dump_path])
        elif self.remote_connection:
            # If an attach command-line is provided, use -premote and add -a '<cmdline>'
            if self.attach_cmdline:
                cmd_args.extend(["-premote", self.remote_connection, "-a", self.attach_cmdline])
            else:
                cmd_args.extend(["-remote", self.remote_connection])

        # Add symbols path if provided
        if symbols_path:
            cmd_args.extend(["-y", symbols_path])

        # Add any additional arguments
        if additional_args:
            cmd_args.extend(additional_args)

        try:
            self.process = subprocess.Popen(
                cmd_args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
        except Exception as e:
            raise CDBError(f"Failed to start CDB process: {str(e)}")

        self.output_lines = []
        self.current_output_lines = []
        self.last_output_lines = []
        self.lock = threading.Lock()
        self.ready_event = threading.Event()
        self.is_executing = threading.Event()
        self.reader_thread = threading.Thread(target=self._read_output)
        self.reader_thread.daemon = True
        self.reader_thread.start()

        # Wait for CDB to initialize by sending an echo marker
        try:
            self._wait_for_prompt(timeout=self.timeout)
        except CDBError:
            self.shutdown()
            raise CDBError("CDB initialization timed out")

        # Run initial commands if provided
        if initial_commands:
            for cmd in initial_commands:
                self.send_command(cmd)

    def _find_cdb_executable(self, custom_path: Optional[str] = None) -> Optional[str]:
        """Find the cdb.exe executable"""
        if custom_path and os.path.isfile(custom_path):
            return custom_path

        for path in DEFAULT_CDB_PATHS:
            if os.path.isfile(path):
                return path

        return None

    def _read_output(self):
        """Thread function to continuously read CDB output"""
        if not self.process or not self.process.stdout:
            return

        try:
            for line in self.process.stdout:
                line = line.rstrip()
                if self.verbose:
                    print(f"CDB > {line}")

                with self.lock:
                    self.current_output_lines.append(line)
                    # Check if the marker is in this line
                    if COMMAND_MARKER_PATTERN.search(line):
                        # Remove the marker line itself
                        if self.current_output_lines and COMMAND_MARKER_PATTERN.search(self.current_output_lines[-1]):
                            self.current_output_lines.pop()
                        self.output_lines = self.current_output_lines
                        self.current_output_lines = []
                        self.ready_event.set()
                        self.is_executing.clear()
        except (IOError, ValueError) as e:
            if self.verbose:
                print(f"CDB output reader error: {e}")

    def _wait_for_prompt(self, timeout=None):
        """Wait for CDB to be ready for commands by sending a marker"""
        try:
            self.ready_event.clear()
            self.process.stdin.write(f"{COMMAND_MARKER}\n")
            self.process.stdin.flush()

            if not self.ready_event.wait(timeout=timeout or self.timeout):
                raise CDBError(f"Timed out waiting for CDB prompt")
        except IOError as e:
            raise CDBError(f"Failed to communicate with CDB: {str(e)}")

    def send_command(self, command: str, timeout: Optional[int] = None) -> List[str]:
        """
        Send a command to CDB and return the output

        Args:
            command: The command to send
            timeout: Custom timeout for this command (overrides instance timeout)

        Returns:
            List of output lines from CDB

        Raises:
            CDBError: If CDB is not responsive or a command is already executing
        """
        if not self.process:
            raise CDBError("CDB process is not running")

        self.ready_event.clear()
        with self.lock:
            if self.is_executing.is_set():
                raise CDBError("A command is already executing")
            self.is_executing.set()
            self.output_lines = []
            self.current_output_lines = []

        try:
            # Send the command followed by our marker to detect completion
            self.process.stdin.write(f"{command}\n{COMMAND_MARKER}\n")
            self.process.stdin.flush()

            cmd_timeout = timeout or self.timeout
            if not self.ready_event.wait(timeout=cmd_timeout):
                with self.lock:
                    result = self.current_output_lines.copy()
                    self.current_output_lines = []
                    self.last_output_lines = result.copy()
                result.append(f"[Command timed out after {cmd_timeout} seconds]")
                return result

            # Success
            with self.lock:
                result = self.output_lines.copy()
                self.output_lines = []
                self.last_output_lines = result.copy()
                self.is_executing.clear()

            return result
        except IOError as e:
            raise CDBError(f"Failed to send command: {str(e)}")

    def interrupt_command(self) -> tuple[str, List[str]]:
        """Send an interrupt signal to the CDB process."""
        if not self.process or not self.process.stdin:
            raise CDBError("CDB process is not running")

        with self.lock:
            if not self.is_executing.is_set():
                output = self.last_output_lines.copy()
                if output:
                    return "finished", output
                raise CDBError("No command is currently executing")

        try:
            self.process.stdin.write("\x03")  # CTRL+C
            self.process.stdin.flush()
            return "interrupted", []
        except IOError as e:
            raise CDBError(f"Failed to send interrupt: {str(e)}")

    def shutdown(self):
        """Clean up and terminate the CDB process"""
        try:
            if self.process and self.process.poll() is None:
                try:
                    if self.remote_connection:
                        # For remote connections, send CTRL+B to detach
                        self.process.stdin.write("\x02")  # CTRL+B
                        self.process.stdin.flush()
                    else:
                        # For dump files, send 'q' to quit
                        self.process.stdin.write("q\n")
                        self.process.stdin.flush()
                    self.process.wait(timeout=1)
                except Exception:
                    pass

                if self.process.poll() is None:
                    self.process.terminate()
                    self.process.wait(timeout=3)
        except Exception as e:
            if self.verbose:
                print(f"Error during shutdown: {e}")
        finally:
            self.process = None

    def get_session_id(self) -> str:
        """Get a unique identifier for this CDB session."""
        if self.dump_path:
            return os.path.abspath(self.dump_path)
        elif self.remote_connection:
            return f"remote:{self.remote_connection}"
        else:
            raise CDBError("Session has no valid identifier")

    def __enter__(self):
        """Support for context manager protocol"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Clean up when exiting context manager"""
        self.shutdown()
