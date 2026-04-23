"""Fixture module for codegraph Python extractor tests."""

from __future__ import annotations

import os
from pathlib import Path as P


def top_level_helper(x: int) -> int:
    return x + 1


class Animal:
    def speak(self) -> str:
        return "generic sound"


class Dog(Animal):
    def speak(self) -> str:
        os.getenv("IGNORED")
        return self._bark_style()

    def _bark_style(self) -> str:
        return "woof"


def run() -> None:
    d = Dog()
    d.speak()
    top_level_helper(1)
    P(".").resolve()
