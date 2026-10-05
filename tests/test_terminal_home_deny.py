"""The deny-list has to catch the home tree however it is spelled.

``rm -rf /`` and ``rm -rf ~`` were covered by a literal-path anchor, so
``rm -rf $HOME`` went straight through — and under full access this regex list
is the *only* thing left between the agent and the user's home directory:
the allow-list, the head deny-list, the cwd lock and the approval step are all
lifted, and ``rm`` is deliberately not on the permanent deny-head set.
"""
from __future__ import annotations

import pytest

from kairos.tools.terminal import TerminalTool


@pytest.fixture
def term(tmp_path):
    return TerminalTool(allowed_cwd=str(tmp_path / "ws"))


# Commands that must never run, whichever way the home tree is named.
HOME_WIPES = [
    "rm -rf $HOME",
    "rm -rf ${HOME}",
    'rm -rf "$HOME"',
    "rm -rf $HOME/",
    "rm -rf $HOME/Documents",
    "rm -rf %USERPROFILE%",
    "rm -rf $USERPROFILE",
    "rm -rf /home",
    "rm -rf /home/",
    "rm -rf /Users/",
    "rm -fr ~",
    "rm -rf /",
    "rm -rf *",
]

# Ordinary cleanup the agent does all day: a fix that blocks these is worse
# than the hole it closes.
STILL_FINE = [
    "rm -rf ./build",
    "rm -rf build/dist",
    "rm -rf $TMPDIR/scratch",
    "rm -rf $HOME_OF_APP/build",
    "rm -f notes.txt",
    "git rm -r --cached web/dist",
]

# Pre-existing policy, not something this change introduced: the ``[/~]``
# anchor already refused *any* absolute path passed to ``rm -rf``. Pinned here
# so a future rewrite of that anchor has to make the decision deliberately.
ALREADY_REFUSED_BY_THE_LITERAL_ANCHOR = [
    "rm -rf /tmp/kairos-scratch",
    "rm -rf /var/tmp/x",
]

RAW_DEVICE_WRITES = [
    ": > /dev/sda",
    "echo x > /dev/sdb",
    "dd if=/dev/zero of=/dev/sda",
    "echo x > /dev/mmcblk0",
]


@pytest.mark.parametrize("command", HOME_WIPES)
def test_home_tree_wipes_are_refused_with_full_access(term, command):
    assert term._is_safe_command_full_access(command) is not None, \
        f"{command!r} slipped through the deny list"


@pytest.mark.parametrize("command", HOME_WIPES)
def test_home_tree_wipes_are_refused_without_full_access(term, command):
    assert term._is_safe_command(command) is not None, \
        f"{command!r} slipped through the deny list"


@pytest.mark.parametrize("command", STILL_FINE)
def test_ordinary_deletes_still_work(term, command):
    assert term._is_safe_command_full_access(command) is None, \
        f"{command!r} was blocked by mistake"


@pytest.mark.parametrize("command", RAW_DEVICE_WRITES)
def test_raw_device_writes_are_refused(term, command):
    assert term._is_safe_command_full_access(command) is not None, \
        f"{command!r} slipped through the deny list"


@pytest.mark.parametrize("command", ALREADY_REFUSED_BY_THE_LITERAL_ANCHOR)
def test_absolute_path_rm_was_already_refused(term, command):
    assert term._is_safe_command_full_access(command) is not None


def test_writing_to_dev_null_is_still_allowed(term):
    """The raw-device rule must not swallow the everyday /dev/null."""
    assert term._is_safe_command_full_access("echo done > /dev/null") is None


def test_a_variable_that_merely_starts_with_home_is_not_the_home_tree(term):
    """``$HOME_OF_APP`` is a different variable; refusing it would be a bug."""
    assert term._is_safe_command_full_access("rm -rf $HOMEBREW_TMP/build") is None


def test_the_deny_list_no_longer_needs_the_colon_idiom(term):
    """``: > /dev/sda`` used to be the only spelling that matched."""
    assert term._is_safe_command_full_access(": > /dev/sda") is not None
    assert term._is_safe_command_full_access("> /dev/sda") is not None
