"""Skip rule: bulk imports do not get reviewed."""
from scripts import check


def test_no_skip_for_small_change():
    assert check.should_skip(num_files=1, total_diff_bytes=200) is False


def test_no_skip_at_file_threshold():
    assert check.should_skip(num_files=check.SKIP_FILE_THRESHOLD, total_diff_bytes=1000) is False


def test_skip_above_file_threshold():
    assert check.should_skip(num_files=check.SKIP_FILE_THRESHOLD + 1, total_diff_bytes=1000) is True


def test_no_skip_at_byte_threshold():
    assert check.should_skip(num_files=2, total_diff_bytes=check.SKIP_BYTE_THRESHOLD) is False


def test_skip_above_byte_threshold():
    assert check.should_skip(num_files=2, total_diff_bytes=check.SKIP_BYTE_THRESHOLD + 1) is True


def test_skip_when_either_threshold_exceeded():
    assert check.should_skip(num_files=999, total_diff_bytes=10) is True
    assert check.should_skip(num_files=1, total_diff_bytes=10**9) is True
