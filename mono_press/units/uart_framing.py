"""Preserve Linux UART error boundaries instead of treating them as data."""
try:
    import termios
except ImportError:
    termios = None


def frame_error_count(device):
    """Read Linux's UART framing counter without changing the v7 raw input mode."""
    try:
        import array
        import fcntl
        counters = array.array('i', [0] * 20)
        fcntl.ioctl(device.fileno(), 0x545D, counters, True)  # TIOCGICOUNT
        return counters[6]
    except (ImportError, OSError, TypeError, AttributeError, ValueError):
        return None


def mark_input_errors(device):
    if termios is None:
        return False
    attrs = termios.tcgetattr(device.fileno())
    attrs[0] |= termios.PARMRK | termios.INPCK
    attrs[0] &= ~(termios.IGNPAR | termios.IGNBRK | termios.BRKINT | termios.ISTRIP)
    termios.tcsetattr(device.fileno(), termios.TCSANOW, attrs)
    return True


class MarkedInputDecoder:
    def __init__(self):
        self.pending = bytearray()

    def decode(self, chunk):
        """Yield bytes, or None at a corrupt character/break. FF FF is literal FF."""
        self.pending.extend(chunk)
        i = 0
        while i < len(self.pending):
            value = self.pending[i]
            if value != 0xff:
                yield value
                i += 1
                continue
            if i + 1 >= len(self.pending):
                break
            if self.pending[i + 1] == 0xff:
                yield 0xff
                i += 2
            elif self.pending[i + 1] == 0:
                if i + 2 >= len(self.pending):
                    break
                yield None
                i += 3
            else:
                # Malformed marker: discard it and break any partial protocol frame.
                yield None
                i += 1
        del self.pending[:i]
