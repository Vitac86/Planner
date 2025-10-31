from datetime import datetime, timedelta
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from helpers.datetime_utils import (
    parse_date_input,
    parse_time_input,
    build_start_datetime,
    snap_minutes,
)


def test_parse_date_input_iso_and_russian():
    assert parse_date_input("2023-12-01").isoformat() == "2023-12-01"
    assert parse_date_input("01.12.2023").isoformat() == "2023-12-01"
def test_parse_time_input_relative_now():
    before = datetime.now()
    result = parse_time_input("сейчас+30")
    after = datetime.now()
    assert result is not None
    minutes_expected = ((before + timedelta(minutes=30)).hour * 60 + (before + timedelta(minutes=30)).minute) % (24 * 60)
    minutes_actual = result.hour * 60 + result.minute
    # allow a 1-minute drift due to processing time
    assert abs(minutes_actual - minutes_expected) <= 1 or abs(minutes_actual - minutes_expected + 24 * 60) <= 1


def test_build_start_datetime_future_auto_rolls_forward():
    now = datetime.now()
    earlier = (now - timedelta(minutes=30)).strftime("%H:%M")
    dt = build_start_datetime(None, earlier, step_minutes=30)
    assert dt is not None
    assert dt >= now
    assert dt.time().minute % 30 == 0


def test_snap_minutes_rounding():
    assert snap_minutes(17, step=15, direction="nearest") == 15
    assert snap_minutes(8, step=15, direction="forward") == 15
    assert snap_minutes(22, step=15, direction="backward") == 15
