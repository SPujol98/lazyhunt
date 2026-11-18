"""Slice-2 skeleton smoke tests: package importability and core dependency."""

import pydantic

import cva


def test_cva_package_importable_with_docstring() -> None:
    assert isinstance(cva.__doc__, str) and cva.__doc__


def test_core_dependency_pydantic_importable() -> None:
    assert pydantic.BaseModel is not None
