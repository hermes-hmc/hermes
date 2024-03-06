# SPDX-FileCopyrightText: 2022 German Aerospace Center (DLR)
#
# SPDX-License-Identifier: Apache-2.0

# SPDX-FileContributor: Michael Meinel

import pytest

from hermes.commands import cli


def test_hermes_full(capsys):
    with pytest.raises(SystemExit) as se:
        cli.main()
        assert "choose from" in se


@pytest.mark.skip(reason="Needs update")
def test_hermes_harvest(hermes_env):
    with hermes_env:
        result = hermes_env.run("harvest")

    assert result.returncode == 0


@pytest.mark.skip(reason="Needs update")
def test_hermes_process(hermes_env):
    with hermes_env:
        result = hermes_env.run("process")

    assert result.returncode == 0
