"""
vendored from https://github.com/wolever/gevent_openssl
with additional fixes
"""

import OpenSSL.SSL
from gevent.socket import wait_read, wait_write

_real_connection = OpenSSL.SSL.Connection


class SSLConnection(object):
    """OpenSSL Connection wrapper
    """

    _reverse_mapping = _real_connection._reverse_mapping

    def __init__(self, context, sock):
        self._context = context
        self._sock = sock
        self._connection = _real_connection(context, sock)

    def __getattr__(self, attr):
        return getattr(self._connection, attr)

    def __iowait(self, io_func, *args, **kwargs):
        fd = self._sock.fileno()
        timeout = self._sock.gettimeout()
        while True:
            try:
                return io_func(*args, **kwargs)
            except (OpenSSL.SSL.WantReadError, OpenSSL.SSL.WantX509LookupError):
                wait_read(fd, timeout=timeout)
            except OpenSSL.SSL.WantWriteError:
                wait_write(fd, timeout=timeout)
            except OpenSSL.SSL.SysCallError as e:
                if e.args == (-1, 'Unexpected EOF'):
                    return b''
                raise

    #def accept(self):
    #    sock, addr = self._sock.accept()
    #    return Connection(self._context, sock), addr

    def do_handshake(self):
        return self.__iowait(self._connection.do_handshake)

    #def connect(self, *args, **kwargs):
    #    return self.__iowait(self._connection.connect, *args, **kwargs)

    def send(self, data, flags=0):
        return self.__send(self._connection.send, data, flags)

    def sendall(self, data, flags=0):
        # Note: all of the types supported by OpenSSL's Connection.sendall,
        # basestring, memoryview, and buffer, support len(...) and slicing,
        # so they are safe to use here.

        # pyopenssl doesn't suport sending bytearrays yet
        if isinstance(data, bytearray):
            data = bytes(data)

        while len(data) > 0:
            # cast to bytes
            res = self.send(data, flags)
            data = data[res:]

    def __send(self, send_method, data, flags=0):
        return self.__iowait(send_method, data, flags)

    def recv(self, bufsiz, flags=0):
        pending = self._connection.pending()
        if pending:
            return self._connection.recv(min(pending, bufsiz))
        try:
            return self.__iowait(self._connection.recv, bufsiz, flags)
        except OpenSSL.SSL.ZeroReturnError:
            return b''

    def shutdown(self):
        return self.__iowait(self._connection.shutdown)
