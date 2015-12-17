# CMSIS-DAP Interface Firmware
# Copyright (c) 2009-2013 ARM Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
from __future__ import absolute_import

import os
import re
import time
import subprocess
import sys
import six
import mbedapi
import mbed_lstools
from intelhex import IntelHex
from pyOCD.board import MbedBoard

TEST_REPO = 'https://developer.mbed.org/users/c1728p9/code/daplink-validation/'

# This prevents the following error message from getting
# displayed on windows if the mbed dismounts unexpectedly
# during a transfer:
#   There is no disk in the drive. Please insert a disk into
#   drive \Device\<Harddiskx>\<rdrive>
if sys.platform.startswith("win"):
    import ctypes
    SEM_FAILCRITICALERRORS = 1
    GetErrorMode = ctypes.windll.kernel32.GetErrorMode
    GetErrorMode.restype = ctypes.c_uint
    GetErrorMode.argtypes = []
    SetErrorMode = ctypes.windll.kernel32.SetErrorMode
    SetErrorMode.restype = ctypes.c_uint
    SetErrorMode.argtypes = [ctypes.c_uint]

    err_mode = GetErrorMode()
    err_mode |= SEM_FAILCRITICALERRORS
    SetErrorMode(err_mode)

board_id_to_build_target = {
    0x0231: 'FRDM-K22F',
    0x1050: 'NXP-LPC800-MAX',
    0x0240: 'FRDM-K64F',
    0x9900: 'Microbit',
    0x1100: 'Nordic-nRF51-DK',
}


def get_all_attached_daplink_boards():
    all_boards = []
    lstools = mbed_lstools.create()
    mbed_list = lstools.list_mbeds()
    for mbed in mbed_list:
        unique_id = mbed['target_id']
        board = DaplinkBoard(unique_id)
        all_boards.append(board)
    return all_boards


def _unique_id_to_host_id(unique_id):
    """Return the chip id unique to the daplink host procesor

    Unique ID has the following fomat
    Board ID - 4 bytes
    Version  - 4 bytes
    Host ID  - Everything else
    """
    return unique_id[8:8 + 32]


def _get_board_endpoints(unique_id):
    """Return a tuple of unique_id, serial_port, mount_point"""
    lstools = mbed_lstools.create()
    mbed_list = lstools.list_mbeds()

    host_id = _unique_id_to_host_id(unique_id)
    for mbed in mbed_list:
        mbed_unique_id = mbed['target_id']
        mbed_serial_port = mbed['serial_port']
        mbed_mount_point = mbed['mount_point']
        mbed_host_id = _unique_id_to_host_id(mbed_unique_id)
        if mbed_host_id == host_id:
            return mbed_unique_id, mbed_serial_port, mbed_mount_point
    return None


class DaplinkBoard:

    MODE_IF = "MODE_IF"
    MODE_BL = "MODE_BL"

    def __init__(self, unique_id):

        self.unique_id = unique_id
        self._update_board_info()
        self._target_firmware_built = False
        self._username = None
        self._password = None

    def get_unique_id(self):
        return self.unique_id

    def get_board_id(self):
        return self.board_id

    def get_serial_port(self):
        return self.serial_port

    def get_mount_point(self):
        return self.mount_point

    def get_connected(self):
        """Check if the board is connected"""
        return os.path.isdir(self.mount_point)

    def get_failure_message(self):
        """Get the failure message from fail.txt

        return None if there there is no failure
        """
        msg = None
        fail_file = os.path.normpath(self.get_mount_point() +
                                     os.sep + 'FAIL.TXT')
        if not self.get_connected():
            raise Exception('Board not connected')
        if os.path.isfile(fail_file):
            with open(fail_file, 'rb') as fail_file_handle:
                msg = fail_file_handle.read()
        return msg

    def get_mode(self):
        """Return either MODE_IF or MODE_BL"""
        if os.path.isfile(self.check_bl_path):
            return self.MODE_BL
        elif os.path.isfile(self.check_if_path):
            return self.MODE_IF
        else:
            raise Exception("Unsupported board - cannot change mode!")

    def get_target_hex_path(self):
        assert self._target_firmware_built
        return self._target_hex_path

    def get_target_bin_path(self):
        assert self._target_firmware_built
        return self._target_bin_path

    def set_mode(self, mode, parent_test):
        """Set the mode to either MODE_IF or MODE_BL"""
        assert mode in (self.MODE_BL, self.MODE_IF)
        test_info = parent_test.create_subtest('set_mode')
        current_mode = self.get_mode()
        if current_mode is mode:
            # No mode change needed
            return

        if mode is self.MODE_BL:
            test_info.info("changing mode IF -> BL")
            # Create file to enter BL mode
            with open(self.start_bl_path, 'wb') as _:
                pass
        elif mode is self.MODE_IF:
            test_info.info("changing mode BL -> IF")
            # Create file to enter BL mode
            with open(self.start_if_path, 'wb') as _:
                pass
        else:
            test_info.warning("Board is in unknown mode")
        self.wait_for_remount(test_info)

        new_mode = self.get_mode()
        if new_mode != mode:
            test_info.failure("Board in wrong mode: %s" % new_mode)
            raise Exception("Could not change board mode")

    def set_build_login(self, username, password):
        assert isinstance(username, six.string_types)
        assert isinstance(password, six.string_types)
        self._username = username
        self._password = password

    def build_target_firmware(self, parent_test):
        """
        Build test firmware for the board

        Login credentials must have been set with set_build_login.
        """
        assert self._username is not None and self._password is not None
        test_info = parent_test.create_subtest('build_target_test_firmware')
        build_name = board_id_to_build_target[self.get_board_id()]
        destdir = 'tmp'
        if not os.path.isdir(destdir):
            os.mkdir(destdir)
        name_base = os.path.normpath("tmp" + os.sep + build_name)
        self._target_hex_path = name_base + '.hex'
        self._target_bin_path = name_base + '.bin'
        # Remove previous build files
        if os.path.isfile(self._target_hex_path):
            os.remove(self._target_hex_path)
        if os.path.isfile(self._target_bin_path):
            os.remove(self._target_bin_path)
        test_info.info('Starting remote build')
        start = time.time()
        built_file = mbedapi.build_repo(self._username, self._password,
                                        TEST_REPO, build_name, destdir)
        stop = time.time()
        test_info.info("Build took %s seconds" % (stop - start))
        extension = os.path.splitext(built_file)[1].lower()
        assert extension == '.hex' or extension == '.bin'
        if extension == '.hex':
            intel_hex = IntelHex(built_file)
            # Only supporting devices with the starting
            # address at 0 currently
            assert intel_hex.minaddr() == 0
            intel_hex.tobinfile(self._target_bin_path)
            os.rename(built_file, self._target_hex_path)
        if extension == '.bin':
            intel_hex = IntelHex()
            intel_hex.loadbin(built_file, offset=0)
            intel_hex.tofile(self._target_hex_path, 'hex')
            os.rename(built_file, self._target_bin_path)
        assert os.path.isfile(self._target_hex_path)
        assert os.path.isfile(self._target_bin_path)
        self._target_firmware_built = True

    def read_target_memory(self, addr, size, resume=True):
        assert self.get_mode() == self.MODE_IF
        with MbedBoard.chooseBoard(board_id=self.get_unique_id()) as board:
            data = board.target.readBlockMemoryUnaligned8(addr, size)
            board.uninit(resume)
        return bytearray(data)

    def test_fs(self, parent_test):
        """Check if the raw filesystem is valid"""
        if sys.platform.startswith("win"):
            test_info = parent_test.create_subtest('test_fs')
            args = ["chkdsk", self.mount_point]
            process = subprocess.Popen(args, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE)
            process.communicate(input='n\r\n')  # Answer no if prompted
            process.wait()
            test_info.info('chkdsk returned %s' % process.returncode)
            if process.returncode != 0:
                test_info.failure('Disk corrupt')
        # TODO - as a future improvement add linux and mac support

    # Tests for the following:
    # 1. Correct files present                -TODO
    # 2. Contents of file are valid ascii
    # 3. Each line ends with \r\n
    # 4. There is no whitespace at the end of the line
    # 5. Each file ends with \r\n
    def test_fs_contents(self, parent_test):
        """Check if the file contents are valid"""
        test_info = parent_test.create_subtest('test_fs_contents')
        non_ascii = r'[^\x20-\x7F\r\n]'
        non_cr_lf = r'\r[^\n]|[^\r]\n'
        trail_white = r'(?:\ \r|\ \n)'
        end_of_file = r'\r\n$'
        files = os.listdir(self.mount_point)
        non_ascii_re = re.compile(non_ascii)
        non_cr_lf_re = re.compile(non_cr_lf)
        trail_white_re = re.compile(trail_white)
        end_of_file_re = re.compile(end_of_file)
        for filename in files:
            filename = os.path.normpath(self.mount_point + os.sep + filename)
            with open(filename, 'rb') as file_handle:
                file_contents = file_handle.read()
            if non_ascii_re.search(file_contents):
                test_info.failure("Non ascii characters in %s" % filename)
            elif non_cr_lf_re.search(file_contents):
                test_info.failure("File has non-standard line endings %s" %
                                  filename)
            elif trail_white_re.search(file_contents):
                test_info.warning("File trailing whitespace %s" %
                                  filename)
            elif end_of_file_re.search(file_contents) is None:
                test_info.warning("No newline at end of file %s" %
                                  filename)
            else:
                test_info.info("File %s valid" % filename)

    def load_interface(self, filepath, parent_test):
        """Load an interface binary or hex"""
        test_info = parent_test.create_subtest('load_interface')
        self.set_mode(self.MODE_BL, test_info)

        filename = os.path.basename(filepath)
        with open(filepath, 'rb') as firmware_file:
            data = firmware_file.read()
        out_file = os.path.normpath(self.mount_point + os.sep + filename)
        start = time.time()
        with open(out_file, 'wb') as firmware_file:
            firmware_file.write(data)
        stop = time.time()
        test_info.info("programming took %s s" % (stop - start))
        self.wait_for_remount(test_info)

    def load_bootloader(self):
        """Load a bootloader binary or hex"""
        raise Exception("Function not implemented")

    def wait_for_remount(self, parent_test, wait_time=15):
        test_info = parent_test.create_subtest('wait_for_remount')
        elapsed = 0
        start = time.time()
        while os.path.isdir(self.mount_point):
            if elapsed > wait_time:
                raise Exception("Dismount timed out")
            time.sleep(0.1)
            elapsed += 0.1
        stop = time.time()
        test_info.info("unmount took %s s" % (stop - start))
        start = time.time()
        while True:
            if self._update_board_info(False):
                if os.path.isdir(self.mount_point):
                    break
            if elapsed > wait_time:
                raise Exception("Mount timed out")
            time.sleep(0.1)
            elapsed += 0.1
        stop = time.time()
        test_info.info("mount took %s s" % (stop - start))

    def _update_board_info(self, exptn_on_fail=True):
        """Update board info

        Update all board information variables that could
        change when remounting or changing modes.
        Note - before this function is set self.unique_id
        must be set.
        """
        endpoints = _get_board_endpoints(self.unique_id)
        if endpoints is None:
            if exptn_on_fail:
                raise Exception("Cound not update board info: %s" %
                                self.unique_id)
            return False
        self.unique_id, self.serial_port, self.mount_point = endpoints
        # Serial port can be missing
        assert self.unique_id is not None
        assert self.mount_point is not None
        self.board_id = int(self.unique_id[0:4], 16)
        self.check_bl_path = os.path.normpath(self.mount_point +
                                              os.sep + 'HELP_FAQ.HTM')
        self.check_if_path = os.path.normpath(self.mount_point +
                                              os.sep + 'MBED.HTM')
        self.start_bl_path = os.path.normpath(self.mount_point +
                                              os.sep + 'START_BL.CFG')
        self.start_if_path = os.path.normpath(self.mount_point +
                                              os.sep + 'START_IF.CFG')
        return True