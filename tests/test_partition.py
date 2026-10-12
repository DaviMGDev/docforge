import pytest
from pdf2md.exceptions import InvalidPageRangeException
from pdf2md.stages.partition import parse_page_selection, partition_into_chunks


def test_parse_page_selection_default_all():
    pages = parse_page_selection(None, 500)
    assert len(pages) == 500
    assert pages[0] == 1
    assert pages[-1] == 500


def test_print_style_ranges():
    # 1-10
    assert parse_page_selection("1-10", 500) == list(range(1, 11))
    # 5
    assert parse_page_selection("5", 500) == [5]
    # 1, 3, 5-7
    assert parse_page_selection("1, 3, 5-7", 500) == [1, 3, 5, 6, 7]
    # 1-200, 401-500
    p = parse_page_selection("1-200, 401-500", 500)
    assert len(p) == 300
    assert p[:5] == [1, 2, 3, 4, 5]
    assert p[-5:] == [496, 497, 498, 499, 500]
    # 10-15, 12-18 deduped
    assert parse_page_selection("10-15, 12-18", 500) == list(range(10, 19))


def test_python_slice_notation():
    # [0:10] -> 1 to 10
    assert parse_page_selection("[0:10]", 500) == list(range(1, 11))
    # [:5] -> 1 to 5
    assert parse_page_selection("[:5]", 500) == list(range(1, 6))
    # [-10:] -> 491 to 500
    assert parse_page_selection("[-10:]", 500) == list(range(491, 501))
    # [0:5] + [9:12] -> 1..5, 10..12
    assert parse_page_selection("[0:5] + [9:12]", 500) == [1, 2, 3, 4, 5, 10, 11, 12]
    # [0:10:2] -> 1, 3, 5, 7, 9
    assert parse_page_selection("[0:10:2]", 500) == [1, 3, 5, 7, 9]


def test_disjoint_ranges_produce_separated_chunks():
    pages = parse_page_selection("1-5, 20-25", 500)
    chunks = partition_into_chunks(pages, max_chunk_size=8)
    assert len(chunks) == 2
    assert chunks[0].page_numbers == [1, 2, 3, 4, 5]
    assert chunks[1].page_numbers == [20, 21, 22, 23, 24, 25]


def test_out_of_bounds_raises_exception():
    with pytest.raises(InvalidPageRangeException):
        parse_page_selection("600-700", 500)

    with pytest.raises(InvalidPageRangeException):
        parse_page_selection("501", 500)


def test_partition_into_chunks_bounded_size():
    pages = list(range(1, 13))
    chunks = partition_into_chunks(pages, max_chunk_size=4)
    assert len(chunks) == 3
    assert chunks[0].page_numbers == [1, 2, 3, 4]
    assert chunks[1].page_numbers == [5, 6, 7, 8]
    assert chunks[2].page_numbers == [9, 10, 11, 12]
