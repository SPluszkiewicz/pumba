"""Contrat du journal. La campagne ne modifie ce fichier que dans un worktree."""

from __future__ import annotations

from campaign.fixture.ledger import close_day, version


def test_empty():
    assert close_day([]) == 0


def test_two_lines_sum():
    assert close_day([10, 20]) == 30


def test_odd_total_rounds_up():
    assert close_day([1]) == 2


def test_version():
    assert version() == 1
