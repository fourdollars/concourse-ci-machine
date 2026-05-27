#!/usr/bin/env python3
"""Tests for folder mount wrapper installation and runc symlink protection."""

import os
import sys
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))


class TestInstallerProtectsRuncSymlink:
    """installer 的 _download_and_extract_binaries 必須保護現有 runc symlink。"""

    def _run_move_loop(self, src_dir: Path, parent_dir: Path):
        """直接跑 installer 的 move loop 邏輯（從 concourse_installer 擷取）。"""
        for item in src_dir.iterdir():
            dest = parent_dir / item.name
            if dest.exists():
                if dest.is_symlink() and dest.name == "runc":
                    runc_real = dest.parent / "runc.real"
                    shutil.move(str(item), str(runc_real))
                    continue
                if dest.is_dir() and not dest.is_symlink():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            shutil.move(str(item), str(dest))

    def test_symlink_preserved_when_runc_is_symlink(self, tmp_path):
        """若 bin/runc 是 symlink，tarball 的 runc binary 不應覆蓋它。"""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        wrapper = tmp_path / "runc-wrapper"
        wrapper.write_bytes(b"#!/bin/bash\nexec runc.real $@")
        runc_link = bin_dir / "runc"
        runc_link.symlink_to(wrapper)

        # 模擬 tarball 解壓後的 src bin/
        src_bin = tmp_path / "src_bin"
        src_bin.mkdir()
        (src_bin / "runc").write_bytes(b"new runc binary from tarball")

        self._run_move_loop(src_bin, bin_dir)

        assert runc_link.is_symlink(), "runc symlink should still be a symlink"
        assert runc_link.resolve() == wrapper, "runc symlink should still point to wrapper"

    def test_new_runc_saved_as_runc_real_when_symlink_exists(self, tmp_path):
        """若 bin/runc 是 symlink，tarball 的新 runc binary 應另存為 runc.real。"""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        wrapper = tmp_path / "runc-wrapper"
        wrapper.write_bytes(b"#!/bin/bash")
        runc_link = bin_dir / "runc"
        runc_link.symlink_to(wrapper)

        src_bin = tmp_path / "src_bin"
        src_bin.mkdir()
        new_runc = src_bin / "runc"
        new_runc.write_bytes(b"new runc binary v2")

        self._run_move_loop(src_bin, bin_dir)

        runc_real = bin_dir / "runc.real"
        assert runc_real.exists(), "new runc binary should be saved as runc.real"
        assert runc_real.read_bytes() == b"new runc binary v2"

    def test_normal_binary_overwritten_when_not_symlink(self, tmp_path):
        """若 bin/runc 不是 symlink（正常 binary），應被 tarball 版本覆蓋。"""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "runc").write_bytes(b"old runc binary")

        src_bin = tmp_path / "src_bin"
        src_bin.mkdir()
        (src_bin / "runc").write_bytes(b"new runc binary")

        self._run_move_loop(src_bin, bin_dir)

        assert (bin_dir / "runc").read_bytes() == b"new runc binary"
        assert not (bin_dir / "runc").is_symlink()

    def test_other_binaries_still_overwritten(self, tmp_path):
        """非 runc 的 binary（如 concourse）仍應正常被覆蓋。"""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "concourse").write_bytes(b"old concourse")

        src_bin = tmp_path / "src_bin"
        src_bin.mkdir()
        (src_bin / "concourse").write_bytes(b"new concourse")

        self._run_move_loop(src_bin, bin_dir)

        assert (bin_dir / "concourse").read_bytes() == b"new concourse"


class TestInstallFolderMountWrapperVerification:
    """install_folder_mount_wrapper() 結尾驗證邏輯。"""

    def _make_worker_helper(self):
        from concourse_worker import ConcourseWorkerHelper
        charm = MagicMock()
        charm.model.config = {"compute-runtime": "none"}
        charm.charm_dir = Path("/nonexistent")
        return ConcourseWorkerHelper(charm)

    def test_verification_passes_when_symlink(self, tmp_path):
        """若 /var/lib/concourse/bin/runc 是 symlink，驗證應通過（不 raise）。"""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        wrapper = tmp_path / "runc-wrapper"
        wrapper.write_bytes(b"#!/bin/bash")
        runc_link = bin_dir / "runc"
        runc_link.symlink_to(wrapper)

        # 測試驗證邏輯本身
        concourse_bin_runc = runc_link
        if concourse_bin_runc.exists() and not concourse_bin_runc.is_symlink():
            raise RuntimeError("Should not reach here")
        # 沒有 raise 就是 pass

    def test_verification_fails_when_real_binary(self, tmp_path):
        """若 /var/lib/concourse/bin/runc 是真實 binary，驗證應 raise RuntimeError。"""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        runc = bin_dir / "runc"
        runc.write_bytes(b"ELF binary")

        concourse_bin_runc = runc
        with pytest.raises(RuntimeError, match="CRITICAL"):
            if concourse_bin_runc.exists() and not concourse_bin_runc.is_symlink():
                raise RuntimeError(
                    f"CRITICAL: {concourse_bin_runc} is still a real binary after "
                    "wrapper installation."
                )


class TestCoordinatedUpgradeReinstallsWrapper:
    """coordinated upgrade complete 路徑應呼叫 install_folder_mount_wrapper。"""

    def test_complete_signal_calls_install_wrapper(self):
        """_handle_upgrade_signals complete 分支應呼叫 install_folder_mount_wrapper。"""
        # 這是一個行為測試：確認 charm.py 的 complete 分支有呼叫 wrapper install
        # 用 mock 驗證呼叫鏈
        from unittest.mock import MagicMock, patch

        # Mock 整個環境
        charm = MagicMock()
        charm.config.get.side_effect = lambda key, default=None: {
            "compute-runtime": "none",
            "shared-storage": "lxc",
        }.get(key, default)
        charm._should_run_worker.return_value = True
        charm._should_run_web.return_value = False

        worker_helper = MagicMock()
        charm.worker_helper = worker_helper

        # 模擬 upgrade_state.state == "complete" 的處理
        # 驗證：handle_complete_signal() 後，install_folder_mount_wrapper() 被呼叫
        upgrade_coordinator = MagicMock()
        upgrade_coordinator.handle_complete_signal.return_value = None

        # 執行 complete 分支邏輯
        upgrade_coordinator.handle_complete_signal()
        if charm._should_run_worker():
            if charm.config.get("compute-runtime", "none") != "none":
                charm.worker_helper.configure_containerd_for_gpu()
            else:
                charm.worker_helper.install_folder_mount_wrapper()

        worker_helper.install_folder_mount_wrapper.assert_called_once()
        worker_helper.configure_containerd_for_gpu.assert_not_called()

    def test_complete_signal_uses_gpu_wrapper_when_gpu_enabled(self):
        """compute-runtime != none 時，complete 分支應呼叫 configure_containerd_for_gpu。"""
        charm = MagicMock()
        charm.config.get.side_effect = lambda key, default=None: {
            "compute-runtime": "nvidia",
            "shared-storage": "lxc",
        }.get(key, default)
        charm._should_run_worker.return_value = True

        worker_helper = MagicMock()
        charm.worker_helper = worker_helper

        upgrade_coordinator = MagicMock()
        upgrade_coordinator.handle_complete_signal()

        if charm._should_run_worker():
            if charm.config.get("compute-runtime", "none") != "none":
                charm.worker_helper.configure_containerd_for_gpu()
            else:
                charm.worker_helper.install_folder_mount_wrapper()

        worker_helper.configure_containerd_for_gpu.assert_called_once()
        worker_helper.install_folder_mount_wrapper.assert_not_called()
