# SPDX-FileCopyrightText: 2022 German Aerospace Center (DLR)
#
# SPDX-License-Identifier: Apache-2.0

# SPDX-FileContributor: Michael Meinel

import pytest

from hermes.commands import cli
from hermes.commands.deposit.error import DepositionUnauthorizedError


def test_hermes_full(capsys):
    with pytest.raises(SystemExit) as se:
        cli.main()
        assert "choose from" in se


def test_hermes_harvest(hermes_env):
    with hermes_env:
        result = hermes_env.run("harvest")

    assert result.returncode == 0


def test_hermes_process(hermes_env):
    with hermes_env:
        result = hermes_env.run("process")

    assert result.returncode == 0


@pytest.mark.skip(
    reason="No clean way at the moment of adding more required options that are parsed by Click, \
                         e.g. files args or options"
)
def test_hermes_with_deposit():
    runner = CliRunner()
    result = runner.invoke(cli.main, args=("--deposit",))

    assert isinstance(result.exception, DepositionUnauthorizedError)


@pytest.mark.skip(
    reason="No clean way at the moment of adding more required options that are parsed by Click, \
                         e.g. files args or options"
)
def test_hermes_with_postprocess():
    runner = CliRunner()
    result = runner.invoke(cli.main, args=("--postprocess",))

    assert not result.exception


def test_hermes_with_path():
    runner = CliRunner()
    result = runner.invoke(cli.main, args=("--path", "./"))

    assert not result.exception


@pytest.mark.skip(
    reason="No clean way at the moment of adding more required options that are parsed by Click, \
                         e.g. files args or options"
)
def test_hermes_with_deposit_and_postprocess():
    runner = CliRunner()
    result = runner.invoke(cli.main, args=("--deposit", "--postprocess"))

    assert isinstance(result.exception, DepositionUnauthorizedError)


@pytest.mark.skip(
    reason="No clean way at the moment of adding more required options that are parsed by Click, \
                         e.g. files args or options"
)
def test_hermes_with_deposit_and_path():
    runner = CliRunner()
    result = runner.invoke(cli.main, args=("--deposit", "--path", "./"))

    assert result.exit_code == 2


def test_hermes_with_path_and_postprocess():
    runner = CliRunner()
    result = runner.invoke(cli.main, args=("--path", "./", "--postprocess"))

    assert result.exit_code == 1


@pytest.mark.skip(
    reason="No clean way at the moment of adding more required options that are parsed by Click, \
                         e.g. files args or options"
)
def test_hermes_with_deposit_and_postprocess_and_path():
    runner = CliRunner()
    result = runner.invoke(
        cli.main, args=("--deposit", "--postprocess", "--path", "./")
    )

    assert isinstance(result.exception, DepositionUnauthorizedError)
