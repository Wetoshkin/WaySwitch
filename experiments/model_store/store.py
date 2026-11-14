"""Experimental packed models and ctypes ownership; not used by WaySwitch."""
import ctypes as c
import math
import os
import struct
import threading
import weakref
from pathlib import Path

from wayswitch.detector import LanguageModel

MAX_BYTES = 64 * 1024 * 1024


def _string(text):
    data = text.encode('utf-8')
    if len(data) > 65536:
        raise ValueError('string too large')
    return struct.pack('<I', len(data)) + data


def _strings(words):
    words = sorted(set(words))
    return struct.pack('<I', len(words)) + b''.join(map(_string, words))


def pack_model(model):
    data = b'WSMODEL1' + _string(model.lang) + _string(model.model['alphabet'])
    data += _strings(model.words) + _strings(model.top) + _strings(model._contractions)
    for name in ('tri', 'ctx'):
        rows = sorted(model.model[name].items())
        data += struct.pack('<I', len(rows))
        data += b''.join(_string(key) + struct.pack('<d', value) for key, value in rows)
    return data + struct.pack('<d', model.model['floor'])


def unpack_model(data):
    if len(data) > MAX_BYTES:
        raise ValueError('model too large')
    pos = 0
    def take(n):
        nonlocal pos
        if n > len(data) - pos:
            raise ValueError('truncated model')
        out = data[pos:pos+n]
        pos += n
        return out
    def count():
        n, = struct.unpack('<I', take(4))
        if n > 1_000_000:
            raise ValueError('count too large')
        return n
    def string():
        n = count()
        if n > 65536:
            raise ValueError('string too large')
        return take(n).decode('utf-8')
    def strings():
        return [string() for _ in range(count())]
    def number():
        n, = struct.unpack('<d', take(8))
        if not math.isfinite(n):
            raise ValueError('invalid number')
        return n
    if take(8) != b'WSMODEL1':
        raise ValueError('invalid magic')
    lang, alphabet = string(), string()
    if lang not in ('en', 'ru'):
        raise ValueError('unsupported language')
    words, top, contractions = strings(), strings(), strings()
    model = {'alphabet': alphabet}
    for name in ('tri', 'ctx'):
        rows = [(string(), number()) for _ in range(count())]
        model[name] = dict(rows)
        if len(model[name]) != len(rows):
            raise ValueError('duplicate key')
    model['floor'] = number()
    if pos != len(data):
        raise ValueError('trailing data')
    result = LanguageModel(lang, words, top, model)
    result._contractions = frozenset(contractions)
    return result


_LIB = None
_LIB_LOCK = threading.Lock()
def library():
    global _LIB
    with _LIB_LOCK:
        if _LIB is None:
            lib = c.CDLL(os.environ['WAYSWITCH_MODEL_STORE_LIB'])
            lib.ws_load.argtypes = [c.c_char_p, c.c_size_t]
            lib.ws_load.restype = c.c_void_p
            lib.ws_free.argtypes = [c.c_void_p]
            lib.ws_free.restype = None
            lib.ws_query.argtypes = [c.c_void_p, c.c_uint32, c.c_char_p, c.c_size_t, c.c_uint32]
            lib.ws_query.restype = c.c_int64
            lib.ws_score.argtypes = [c.c_void_p, c.c_char_p, c.c_size_t]
            lib.ws_score.restype = c.c_double
            lib.ws_extend.argtypes = [c.c_void_p, c.c_char_p, c.c_size_t]
            lib.ws_extend.restype = c.c_void_p
            _LIB = lib
    return _LIB


class _Contains:
    def __init__(self, owner, op):
        self.owner, self.op = weakref.ref(owner), op
    def __contains__(self, text):
        owner = self.owner()
        if owner is None:
            raise ValueError('closed model')
        return bool(owner._query(self.op, text))


class NativeModel(LanguageModel):
    def __init__(self, lang, words, top, model):
        reference = LanguageModel(lang, words, top, model)
        data = pack_model(reference)
        self._init(library().ws_load(data, len(data)), lang)

    def _init(self, handle, lang):
        if not handle:
            raise ValueError('native model rejected')
        self._lock = threading.RLock()
        self._handle, self.lang = handle, lang
        self.words, self.top, self.endings = (_Contains(self, op) for op in (1, 3, 4))

    @classmethod
    def from_bytes(cls, data, lang=None):
        if len(data) > MAX_BYTES:
            raise ValueError('model too large')
        if len(data) < 14 or data[:8] != b'WSMODEL1':
            raise ValueError('invalid model header')
        size, = struct.unpack_from('<I', data, 8)
        if size != 2 or data[12:14] not in (b'ru', b'en'):
            raise ValueError('invalid language header')
        actual = data[12:14].decode('ascii')
        if lang is not None and lang != actual:
            raise ValueError('language mismatch')
        result = cls.__new__(cls)
        result._init(library().ws_load(data, len(data)), actual)
        return result

    @classmethod
    def load(cls, lang, data_dir=None, top_n=300):
        if data_dir is None:
            raise ValueError('packed directory required')
        if top_n != 300:
            raise ValueError('prototype packs pin top_n=300')
        if lang not in ('ru', 'en'):
            raise ValueError('unsupported language')
        with (Path(data_dir) / (lang + '.bin')).open('rb') as stream:
            if os.fstat(stream.fileno()).st_size > MAX_BYTES:
                raise ValueError('model too large')
            data = stream.read(MAX_BYTES+1)
        return cls.from_bytes(data, lang)

    def __copy__(self):
        raise TypeError('native model cannot be copied')

    def __deepcopy__(self, memo):
        raise TypeError('native model cannot be copied')

    def _query(self, op, text, limit=100):
        data = text.encode('utf-8')
        if len(data) > 65536:
            raise ValueError('query too large')
        with self._lock:
            if not self._handle:
                raise ValueError('closed model')
            result = library().ws_query(self._handle, op, data, len(data),
                                        max(0, min(limit, 2**32-1)))
        if result < 0:
            raise ValueError('native query rejected')
        return result

    def is_word(self, word):
        low = word.lower()
        if self._query(1, low):
            return True
        return bool(self._query(2, self._lookup_key(low)) if self.lang == 'ru'
                    else self._query(6, low))

    def prefix_count(self, prefix, limit=100):
        return self._query(5, self._lookup_key(prefix), limit)

    def has_prefix(self, prefix):
        return bool(self.prefix_count(prefix, 1))

    def score(self, text):
        data = text.lower().encode('utf-8')
        if len(data) > 65536:
            raise ValueError('query too large')
        with self._lock:
            if not self._handle:
                raise ValueError('closed model')
            value = library().ws_score(self._handle, data, len(data))
        if not math.isfinite(value):
            raise ValueError('native score rejected')
        return value

    def with_extra_words(self, words):
        data = _strings(words)
        with self._lock:
            if not self._handle:
                raise ValueError('closed model')
            result = self.__class__.__new__(self.__class__)
            result._init(library().ws_extend(self._handle, data, len(data)), self.lang)
            return result

    def close(self):
        if hasattr(self, '_lock'):
            with self._lock:
                if self._handle:
                    library().ws_free(self._handle)
                    self._handle = None

    def __del__(self):
        self.close()
