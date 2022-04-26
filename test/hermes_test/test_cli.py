import typing as t
from unittest import mock

import click

from hermes import cli


def mock_command(name: str) -> t.Tuple[mock.Mock, click.Command]:
    func = mock.Mock()
    return func, click.command(name)(func)


def test_workflow_setup():
    wf = cli.WorkflowCommand()

    wf.add_command(mock_command("spam")[1])
    wf.add_command(mock_command("eggs")[1])

    ctx = click.Context(wf)
    assert wf.list_commands(ctx) == ["spam", "eggs"]


def test_workflow_no_dupes():
    wf = cli.WorkflowCommand()

    wf.add_command(mock_command("spam")[1])
    wf.add_command(mock_command("eggs")[1])
    wf.add_command(mock_command("ham")[1], "spam")

    ctx = click.Context(wf)
    assert wf.list_commands(ctx) == ["spam", "eggs"]
    assert wf.get_command(ctx, "spam").name == "ham"


def test_workflow_invoke():
    wf = cli.WorkflowCommand()
    spam, spam_cmd = mock_command("spam")
    eggs, eggs_cmd = mock_command("eggs")

    wf.add_command(spam)
    wf.add_command(eggs)

    ctx = click.Context(wf)
    wf.invoke_all(ctx)

    spam.assert_called_once()
    eggs.assert_called_once()
