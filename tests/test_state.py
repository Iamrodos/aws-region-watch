"""Tests for state file management."""

import json
import pytest
from pathlib import Path

from aws_region_watch import (
    get_state_file,
    get_global_state_file,
    load_region_state,
    save_region_state,
)


class TestStateFilePaths:
    """Tests for state file path generation."""

    def test_get_state_file_returns_json_path(self, tmp_state_dir):
        """State file should be named {region}.json."""
        path = get_state_file(tmp_state_dir, "us-east-1")
        assert path == tmp_state_dir / "us-east-1.json"

    def test_get_global_state_file_returns_regions_json(self, tmp_state_dir):
        """Global state file should be named regions.json."""
        path = get_global_state_file(tmp_state_dir)
        assert path == tmp_state_dir / "regions.json"


class TestLoadRegionState:
    """Tests for loading state from disk."""

    def test_returns_empty_dict_when_file_missing(self, tmp_state_dir):
        """Return empty dict when state file doesn't exist."""
        state = load_region_state(tmp_state_dir, "us-east-1")
        assert state == {}

    def test_loads_existing_state_file(self, tmp_state_dir, sample_state):
        """Successfully load valid state file."""
        state_file = tmp_state_dir / "ap-southeast-2.json"
        state_file.write_text(json.dumps(sample_state))

        state = load_region_state(tmp_state_dir, "ap-southeast-2")
        assert state == sample_state

    def test_returns_empty_dict_for_corrupted_json(self, tmp_state_dir):
        """Return empty dict when state file contains invalid JSON."""
        state_file = tmp_state_dir / "us-east-1.json"
        state_file.write_text("{ invalid json }")

        state = load_region_state(tmp_state_dir, "us-east-1")
        assert state == {}


class TestSaveRegionState:
    """Tests for saving state to disk."""

    def test_creates_state_file(self, tmp_state_dir, sample_state):
        """State file is created with correct content."""
        save_region_state(sample_state, tmp_state_dir, "ap-southeast-2")

        state_file = tmp_state_dir / "ap-southeast-2.json"
        assert state_file.exists()

        loaded = json.loads(state_file.read_text())
        assert loaded["region"] == sample_state["region"]
        assert loaded["product"] == sample_state["product"]
        assert "_schema_version" in loaded
        assert "_last_updated" in loaded

    def test_creates_state_directory_if_missing(self, tmp_path, sample_state):
        """State directory is created if it doesn't exist."""
        state_dir = tmp_path / "new_state_dir"
        assert not state_dir.exists()

        save_region_state(sample_state, state_dir, "us-east-1")

        assert state_dir.exists()
        assert (state_dir / "us-east-1.json").exists()

    def test_overwrites_existing_state(self, tmp_state_dir):
        """Existing state file is overwritten."""
        state_file = tmp_state_dir / "us-east-1.json"
        state_file.write_text('{"_schema_version": 1, "old": "data"}')

        new_state = {"new": "data"}
        save_region_state(new_state, tmp_state_dir, "us-east-1")

        loaded = json.loads(state_file.read_text())
        assert loaded["new"] == "data"
        assert "old" not in loaded
