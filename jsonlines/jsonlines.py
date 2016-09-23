"""
jsonlines implementation
"""

import numbers
import io
import json

import six


TYPE_MAPPING = {
    dict: dict,
    list: list,
    str: six.text_type,
    int: six.integer_types,
    float: float,
    numbers.Number: numbers.Number,
    bool: bool,
}


class NonClosingTextIOWrapper(io.TextIOWrapper):
    """
    Text IO wrapper that does not close the underlying stream.
    """
    def __del__(self):
        try:
            self.flush()
            self.detach()
        except Exception:
            pass

    def close(self):
        self.flush()
        self.detach()


class Error(Exception):
    """Base error class."""
    pass


class InvalidLineError(Error, ValueError):
    """
    Error raised when an invalid line is encountered.

    This happens when the line does not contain valid JSON, or if a
    specific data type has been requested, and the line contained a
    different data type.
    """
    def __init__(self, msg, line, lineno):
        self.line = line.rstrip()
        self.lineno = lineno
        super(InvalidLineError, self).__init__(msg)


class ReaderWriterBase(object):
    """
    Base class with shared behaviour for both the reader and writer.
    """

    #: Whether this reader/writer is closed.
    closed = False

    def __init__(self, fp):
        self._fp = self._text_fp = fp
        self._should_close_fp = False
        self.closed = False

    def close(self):
        """
        Close this reader/writer.

        This closes the underlying file if that file has been opened by
        this reader/writer. When an already opened file-like object was
        provided, the caller is responsible for closing it.
        """
        if self.closed:
            return
        self.closed = True
        if self._fp is not self._text_fp:
            self._text_fp.close()
        if self._should_close_fp:
            self._fp.close()

    def __repr__(self):
        return '<jsonlines.{} fp={!r}'.format(
            type(self).__name__,
            self._fp)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


class Reader(ReaderWriterBase):
    """
    Reader for the jsonlines format.

    The `loads` argument can be used to replace the standard json
    decoder. If specified, it must be a callable that accepts a
    (unicode) string and returns the decoded object.

    Instances are iterable and can be used as a context manager.

    :param file-like fp: writable file-like object
    :param callable loads: custom json decoder callable
    """
    def __init__(self, fp, loads=None):
        super(Reader, self).__init__(fp)
        if loads is None:
            loads = json.loads
        self._loads = loads
        self._lineno = 0
        if not isinstance(fp.read(0), six.text_type):
            self._text_fp = NonClosingTextIOWrapper(fp, encoding='utf-8')

    def read(self, type=None, allow_none=False, skip_invalid=False):
        """
        Read and decode a line from the underlying file-like object.

        The optional `type` argument specifies the expected data type.
        Supported types are ``dict``, ``list``, ``str``, ``int``,
        ``float``, ``numbers.Number`` (accepts both integers and
        floats), and ``bool``. When specified, non-conforming lines
        result in :py:exc:`InvalidLineError`.

        By default, input lines containing ``null`` (in JSON) are
        considered invalid, and will cause :py:exc:`InvalidLineError`.
        The `allow_none` argument can be used to change this behaviour,
        in which case ``None`` will be returned instead.
        """
        if type is not None and type not in TYPE_MAPPING:
            raise ValueError("invalid type specified")

        line = self._text_fp.readline()
        if not line:
            raise EOFError
        self._lineno += 1

        try:
            value = self._loads(line)
        except ValueError as orig_exc:
            exc = InvalidLineError(
                "invalid json: {}".format(orig_exc), line, self._lineno)
            six.raise_from(exc, orig_exc)

        if value is None:
            if allow_none:
                return None
            raise InvalidLineError(
                "line contains null value", line, self._lineno)

        if type is not None:
            valid = isinstance(value, TYPE_MAPPING[type])
            if type in (int, numbers.Number):
                valid = valid and not isinstance(value, bool)
            if not valid:
                raise InvalidLineError(
                    "line does not match requested type", line, self._lineno)

        return value

    def iter(self, type=None, allow_none=False, skip_invalid=False):
        """
        Iterate over all lines.

        This is the iterator equivalent to repeatedly calling
        :py:meth:`~Reader.read()`. If no arguments are specified, this
        is the same as directly iterating over this :py:class:`Reader`
        instance.

        See :py:meth:`~Reader.read()` for a description of the `type`
        and `allow_none` arguments. When `skip_invalid` is set to
        ``True``, invalid lines will be silently ignored.
        """
        try:
            while True:
                try:
                    yield self.read(type=type, allow_none=allow_none)
                except InvalidLineError:
                    if not skip_invalid:
                        raise
        except EOFError:
            pass

    def __iter__(self):
        """
        See :py:meth:`~Reader.iter()`.
        """
        return self.iter()


class Writer(ReaderWriterBase):
    """
    Writer for the jsonlines format.

    The `compact` argument can be used to to produce smaller output.

    The `sort` argument can be used to sort keys in json objects, and
    will produce deterministic output.

    For more control, provide a a custom encoder callable using the
    `dumps` argument. The callable must produce (unicode) string output.
    If specified, the `compact` and `sort` arguments will be ignored.

    Instances can be used as a context manager.

    :param file-like fp: writable file-like object
    :param bool compact: whether to use a compact output format
    :param bool sort: whether to sort object keys
    :param callable dumps: custom encoder callable
    :param bool flush: whether to flush the file-like object after
        writing each line
    """
    def __init__(self, fp, compact=False, sort=False, dumps=None, flush=False):
        super(Writer, self).__init__(fp)
        try:
            fp.write(u'')
        except TypeError:
            self._text_fp = NonClosingTextIOWrapper(fp, encoding='utf-8')
        if dumps is None:
            encoder_kwargs = dict(ensure_ascii=False, sort_keys=sort)
            if compact:
                encoder_kwargs.update(separators=(',', ':'))
            dumps = json.JSONEncoder(**encoder_kwargs).encode
        self._dumps = dumps
        self._flush = flush

    def write(self, obj):
        """
        Encode and write a single object.

        :param obj: the object to encode and write
        """
        line = self._dumps(obj)
        written = False
        if six.PY2 and isinstance(line, six.binary_type):
            # On Python 2, the JSON module has the nasty habit of
            # returning either a byte string or unicode string,
            # depending on whether the serialised structure can be
            # encoded using ASCII only. However, text streams (including
            # io.TextIOWrapper) only accept unicode strings. To avoid
            # useless encode/decode overhead, write bytes directly to
            # the file-like object if it was a binary stream.
            if self._fp is not self._text_fp:
                # Original file-like object was wrapped.
                self._fp.write(line)
                self._fp.write(b"\n")
                written = True
            else:
                line = line.decode('utf-8')
        if not written:
            self._text_fp.write(line)
            self._text_fp.write(u"\n")
        if self._flush:
            self._text_fp.flush()

    def write_all(self, iterable):
        """
        Encode and write multiple objects.

        :param iterable: an iterable of objects
        """
        for obj in iterable:
            self.write(obj)


def open(name, mode='r', **kwargs):
    """
    Open a jsonlines file for reading or writing.

    This is a convenience function that opens a file, and wraps it in
    either a :py:class:`Reader` or :py:class:`Writer` instance,
    depending on the specified `mode`.

    Any additional keyword arguments will be passed on to the reader and
    writer: see their documentation for available options.

    The resulting reader or writer must be closed after use by the
    caller, which will also close the opened file.  This can be done by
    calling ``.close()``, but the easiest way to ensure proper resource
    finalisation is to use a ``with`` block (context manager), e.g.

    ::

        with jsonlines.open('out.jsonl', mode='w') as writer:
            writer.write(...)

    :param file-like fp: name of the file to open
    :param str mode: whether to open the file for reading (``r``) or
        writing (``w``).
    :param **kwargs: additional arguments, forwarded to the reader or writer
    """
    if mode not in {'r', 'w'}:
        raise ValueError("'mode' must be either 'r' or 'w'")
    fp = io.open(name, mode=mode + 't', encoding='utf-8')
    if mode == 'r':
        instance = Reader(fp, **kwargs)
    else:
        instance = Writer(fp, **kwargs)
    instance._should_close_fp = True
    return instance
