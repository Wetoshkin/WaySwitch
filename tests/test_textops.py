"""Транслитерация и число прописью — чистые функции."""

import pytest

from wayswitch.textops import number_to_words, number_words, to_cyrillic, transliterate


@pytest.mark.parametrize("src, dst", [
    ("Привет, мир!", "Privet, mir!"),
    ("Щукин", "Shchukin"),
    ("Юля и Ёжик", "Yulya i Yozhik"),
    ("подъезд", "podezd"),
    ("ЖУК", "ZHUK"),
    ("Хабаровск", "Khabarovsk"),
])
def test_cyrillic_to_latin(src, dst):
    assert transliterate(src) == dst


@pytest.mark.parametrize("src, dst", [
    ("Privet, mir!", "Привет, мир!"),
    ("Yandex", "Яндекс"),
    ("Sergey", "Сергей"),
    ("ty", "ты"),
    ("Shchukin", "Щукин"),
    ("Moskva", "Москва"),
])
def test_latin_to_cyrillic(src, dst):
    assert transliterate(src) == dst


def test_transliterate_nothing_to_do():
    assert transliterate("") is None
    assert transliterate("12345, !") is None


def test_round_trip_common_words():
    for word in ("Privet", "Kniga", "Zhurnal", "Chay"):
        assert transliterate(transliterate(word)) == word
    assert to_cyrillic("Chay") == "Чай"


@pytest.mark.parametrize("n, words", [
    (0, "ноль"),
    (1, "один"),
    (12, "двенадцать"),
    (21, "двадцать один"),
    (101, "сто один"),
    (1000, "одна тысяча"),
    (2002, "две тысячи два"),
    (11000, "одиннадцать тысяч"),
    (21000, "двадцать одна тысяча"),
    (1250, "одна тысяча двести пятьдесят"),
    (5_000_000, "пять миллионов"),
    (2_342_001, "два миллиона триста сорок две тысячи один"),
    (1_000_000_000, "один миллиард"),
    (-15, "минус пятнадцать"),
])
def test_number_to_words(n, words):
    assert number_to_words(n) == words


def test_number_words_from_selection():
    assert number_words("1 250") == "одна тысяча двести пятьдесят"
    assert number_words(" 7 ") == " семь "
    assert number_words("1 000") == "одна тысяча"
    assert number_words("−3") == "минус три"
    assert number_words("12.5") is None
    assert number_words("abc") is None
    assert number_words("１２") is None  # полноширинные цифры — не наше
    assert number_words("1" + "0" * 12) is None
