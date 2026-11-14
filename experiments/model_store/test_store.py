"""Offline contracts for the experimental store; never imports it in the daemon."""
import copy
import gc
import math
import os
import weakref

import pytest

from experiments.model_store.store import NativeModel, pack_model, unpack_model
from wayswitch.detector import LanguageModel

pytestmark = pytest.mark.skipif(not os.environ.get('WAYSWITCH_MODEL_STORE_LIB'),
                                reason='opt-in Rust prototype requires built library')


@pytest.fixture
def models():
    py = LanguageModel.from_words('ru', [('ёлка', 10), ('елки', 5), ('кот', 3)],
                                  'абвгдеёжзийклмнопрстуфхцчшщъыьэюя')
    return py, NativeModel.from_bytes(pack_model(py))


def test_model_queries_and_scores_match_python(models):
    py, native = models
    for word in ('', 'ёлка', 'ЕЛКА', 'ёлки', 'коты', 'абракадабра', 'e\u0301', '😀'):
        assert native.is_word(word) == py.is_word(word)
        assert native.has_prefix(word) == py.has_prefix(word)
        for limit in (-1, 0, 1, 100):
            assert native.prefix_count(word, limit) == py.prefix_count(word, limit)
        assert math.isclose(native.score(word), py.score(word), abs_tol=1e-12)
    restored = unpack_model(pack_model(py))
    assert restored.words == py.words and restored.model == py.model


def test_personal_overlay_keeps_base_immutable(models):
    py, native = models
    other = native.with_extra_words(['новоеслово'])
    assert other.is_word('новоеслово')
    assert not native.is_word('новоеслово')
    assert other.score('кот') == native.score('кот')
    native.close()
    assert other.is_word('ёлка')


def test_malformed_data_and_closed_handle_are_bounded(models):
    py, native = models
    data = pack_model(py)
    for invalid in (b'', b'bad', data[:-1], data + b'x', b'WSMODEL1' + b'\xff'*4):
        with pytest.raises(ValueError):
            NativeModel.from_bytes(invalid)
    native.close()
    native.close()
    with pytest.raises(ValueError):
        native.score('кот')


def test_native_handle_cannot_be_copied(models):
    native = models[1]
    try:
        duplicate = copy.copy(native)
    except TypeError:
        pass
    else:
        duplicate._handle = None
        pytest.fail('copy created a second native owner')
    with pytest.raises(TypeError, match='cannot be copied'):
        copy.deepcopy(native)


def test_old_overlay_released_without_cyclic_gc(models):
    enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(20):
            overlay = models[1].with_extra_words(['новоеслово'])
            view = overlay.words
            reference = weakref.ref(overlay)
            del overlay
            assert reference() is None
            with pytest.raises(ValueError):
                _ = 'кот' in view
    finally:
        if enabled:
            gc.enable()


def test_file_size_and_language_header_are_bounded(models, tmp_path, monkeypatch):
    from experiments.model_store import store
    data = pack_model(models[0])
    with pytest.raises(ValueError, match='language'):
        NativeModel.from_bytes(data, lang='en')
    with pytest.raises(ValueError, match='header'):
        NativeModel.from_bytes(b'WSMODEL1' + b'\xff'*4 + b'ru')
    monkeypatch.setattr(store, 'MAX_BYTES', 32)
    (tmp_path / 'ru.bin').write_bytes(b'x'*64)
    with pytest.raises(ValueError, match='too large'):
        NativeModel.load('ru', tmp_path)


def test_library_is_published_only_after_abi_is_ready(monkeypatch):
    from experiments.model_store import store
    class Function:
        def __setattr__(self, name, value):
            assert store._LIB is None
            object.__setattr__(self, name, value)
    class Library:
        pass
    lib = Library()
    for name in ('ws_load', 'ws_free', 'ws_query', 'ws_score', 'ws_extend'):
        setattr(lib, name, Function())
    monkeypatch.setattr(store, '_LIB', None)
    monkeypatch.setattr(store.c, 'CDLL', lambda _: lib)
    assert store.library() is lib


def test_contractions_and_utf8_errors():
    from experiments.model_store.store import library
    py = LanguageModel.from_words('en', [('dont', 10), ('isnt', 5), ('hello', 3)],
                                  'abcdefghijklmnopqrstuvwxyz')
    native = NativeModel.from_bytes(pack_model(py))
    assert native.lang == 'en'
    for word in ("DON'T", "isn't", "won't", 'hello', 'İ', 'ΟΣ', 'e\u0301'):
        assert native.is_word(word) == py.is_word(word)
        assert native.score(word) == py.score(word)
    other = native.with_extra_words(['wont'])
    assert other.is_word('wont') and not other.is_word("won't")
    assert library().ws_query(native._handle, 1, b'\xff', 1, 1) == -1
    assert math.isnan(library().ws_score(native._handle, b'\xff', 1))
    oversized = b'x'*65537
    assert library().ws_query(native._handle, 1, oversized, len(oversized), 1) == -1
    assert math.isnan(library().ws_score(native._handle, oversized, len(oversized)))
    with pytest.raises(ValueError, match='too large'):
        native.score('x'*65537)
