"""Seat assignment for shared cockpit: which slave sim sits in the left and right seat.

The master router's SHAREDINFO is the single source of truth
(seat_left_simulator / seat_right_simulator, "NOSIM" when empty); every
other connected sim is an observer. Changes arrive as FRDP
addon=FRANKENROUTER:<version>:SEAT:<LEFT|RIGHT|OBSERVER>:<sim name>
from any router's web UI and are applied by the master (see
rules.py handle_addon_frankenrouter_seat()). Add-ons (frankenpilot,
frankenusb, radio clients) follow SHAREDINFO.

This module is pure logic, unit-tested here (make unittests).
"""

import unittest

NOSIM = "NOSIM"
SEATS = ("LEFT", "RIGHT", "OBSERVER")


def assign(left, right, sim, seat):
    """Return (left, right, evicted, evicted_to) after putting sim in seat.

    left/right: current occupants (NOSIM = empty). seat: LEFT, RIGHT or
    OBSERVER. Rules (user design 2026-11-07):
    - only one sim per pilot seat; a sim sits in at most one seat;
    - taking an occupied pilot seat evicts its occupant, who moves to the
      other pilot seat if that is (now) free, otherwise becomes an
      observer (so two pilots swapping seats is a single click);
    - OBSERVER just takes sim out of any pilot seat.
    evicted is the evicted sim (or None), evicted_to its new seat.
    """
    if seat not in SEATS:
        raise ValueError(f"unknown seat {seat!r}")
    if not sim or sim == NOSIM:
        raise ValueError("no sim name")
    # Leave any seat sim already holds
    if left == sim:
        left = NOSIM
    if right == sim:
        right = NOSIM
    if seat == "OBSERVER":
        return left, right, None, None
    seats = {"LEFT": left, "RIGHT": right}
    other = "RIGHT" if seat == "LEFT" else "LEFT"
    evicted = seats[seat] if seats[seat] != NOSIM else None
    seats[seat] = sim
    evicted_to = None
    if evicted is not None:
        if seats[other] == NOSIM:
            seats[other] = evicted
            evicted_to = other
        else:
            evicted_to = "OBSERVER"
    return seats["LEFT"], seats["RIGHT"], evicted, evicted_to


def seat_of(left, right, sim):
    """Return LEFT, RIGHT or OBSERVER for sim."""
    if sim and sim != NOSIM:
        if sim == left:
            return "LEFT"
        if sim == right:
            return "RIGHT"
    return "OBSERVER"


def vacate_missing(left, right, connected):
    """Return (left, right, vacated) with seats of sims no longer connected emptied."""
    vacated = []
    if left != NOSIM and left not in connected:
        vacated.append(left)
        left = NOSIM
    if right != NOSIM and right not in connected:
        vacated.append(right)
        right = NOSIM
    return left, right, vacated


class TestSeats(unittest.TestCase):
    """Seat assignment rules."""

    def test_take_empty_seat(self):
        """An empty seat is just taken."""
        self.assertEqual(assign(NOSIM, NOSIM, "A", "LEFT"), ("A", NOSIM, None, None))

    def test_evict_to_free_seat(self):
        """Taking an occupied seat moves the occupant to the free other seat."""
        self.assertEqual(assign("A", NOSIM, "B", "LEFT"), ("B", "A", "A", "RIGHT"))

    def test_evict_to_observer(self):
        """With both seats taken, the evicted sim becomes an observer."""
        self.assertEqual(assign("A", "B", "C", "LEFT"), ("C", "B", "A", "OBSERVER"))

    def test_swap(self):
        """The right-seat sim taking the left seat swaps the two pilots."""
        self.assertEqual(assign("A", "B", "B", "LEFT"), ("B", "A", "A", "RIGHT"))

    def test_same_seat_again(self):
        """Taking the seat you already have changes nothing."""
        self.assertEqual(assign("A", "B", "A", "LEFT"), ("A", "B", None, None))

    def test_observer(self):
        """OBSERVER empties the sim's seat."""
        self.assertEqual(assign("A", "B", "B", "OBSERVER"), ("A", NOSIM, None, None))

    def test_move_seat(self):
        """Moving from left to an empty right seat leaves left empty."""
        self.assertEqual(assign("A", NOSIM, "A", "RIGHT"), (NOSIM, "A", None, None))

    def test_bad_input(self):
        """Unknown seats and missing names are refused."""
        with self.assertRaises(ValueError):
            assign(NOSIM, NOSIM, "A", "MIDDLE")
        with self.assertRaises(ValueError):
            assign(NOSIM, NOSIM, NOSIM, "LEFT")

    def test_seat_of(self):
        """seat_of() reports pilot seats and observers."""
        self.assertEqual(seat_of("A", "B", "A"), "LEFT")
        self.assertEqual(seat_of("A", "B", "B"), "RIGHT")
        self.assertEqual(seat_of("A", "B", "C"), "OBSERVER")
        self.assertEqual(seat_of(NOSIM, NOSIM, NOSIM), "OBSERVER")

    def test_vacate_missing(self):
        """Disconnected sims lose their seats; nobody is promoted."""
        self.assertEqual(vacate_missing("A", "B", {"B", "C"}), (NOSIM, "B", ["A"]))
        self.assertEqual(vacate_missing("A", "B", {"A", "B"}), ("A", "B", []))


if __name__ == "__main__":
    unittest.main()
