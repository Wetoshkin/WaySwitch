from wayswitch import ngram

RU = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
EN = "abcdefghijklmnopqrstuvwxyz"


def _ru_model():
    words = [("привет", 100.0), ("пока", 50.0), ("спасибо", 80.0), ("буква", 20.0),
             ("книга", 30.0), ("это", 90.0), ("который", 40.0)]
    return ngram.train(words, RU)


def test_seen_word_scores_higher_than_garbage():
    m = _ru_model()
    assert ngram.score(m, "привет") > ngram.score(m, "ьоыфжз")


def test_unseen_trigram_uses_context_floor():
    m = _ru_model()
    assert m["floor"] < 0
    assert ngram.score(m, "яяя") <= m["floor"] + 1e-9 or ngram.score(m, "яяя") < -2


def test_score_is_case_insensitive():
    m = _ru_model()
    assert ngram.score(m, "Привет") == ngram.score(m, "привет")


def test_save_load_round_trip(tmp_path):
    m = _ru_model()
    p = tmp_path / "m.json.gz"
    ngram.save(m, p)
    m2 = ngram.load(p)
    assert m2 == m
