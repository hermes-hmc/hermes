# SPDX-FileCopyrightText: 2023 Helmholtz-Zentrum Dresden-Rossendorf (HZDR)
#
# SPDX-License-Identifier: Apache-2.0

# SPDX-FileContributor: David Pape

import click

from hermes.model.context import CodeMetaContext


# TODO: Abstract base class?
class BaseDepositPlugin:
    def __init__(self, click_ctx: click.Context, ctx: CodeMetaContext) -> None:
        self.click_ctx = click_ctx
        self.ctx = ctx

    def __call__(self) -> None:
        # TODO: Decide here which of initial/new/... to run?
        steps = [
            "prepare",
            "map",
            "create_initial_version",
            "create_new_version",
            "update_metadata",
            "delete_artifacts",
            "upload_artifacts",
            "publish",
        ]

        for step in steps:
            getattr(self, step)()

    def prepare(self) -> None:
        pass

    def map(self) -> None:
        pass

    def create_initial_version(self) -> None:
        pass

    def create_new_version(self) -> None:
        pass

    def update_metadata(self) -> None:
        pass

    def delete_artifacts(self) -> None:
        pass

    def upload_artifacts(self) -> None:
        pass

    def publish(self) -> None:
        pass
