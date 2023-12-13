# SPDX-FileCopyrightText: 2022 German Aerospace Center (DLR)
#
# SPDX-License-Identifier: Apache-2.0

# SPDX-FileContributor: Stephan Druskat
# SPDX-FileContributor: Michael Meinel

import pathlib
import json
from ruamel.yaml import YAML

import pytest

import hermes.commands.harvest.cff as harvest


@pytest.fixture
def codemeta():
    return json.loads("""{
      "@context": "https://doi.org/10.5063/schema/codemeta-2.0",
      "@type": "SoftwareSourceCode",
      "author": [
        {
          "@type": "Person",
          "givenName": "Author"
        }
      ],
      "name": "Title"
    }""")


@pytest.fixture
def valid_minimal_cff(tmp_path):
    cff = """\
    cff-version: 1.2.0
    authors:
      - given-names: Author
    message: Message
    title: Title
    """
    yaml = YAML()
    cff_yml = yaml.load(cff)
    cff_file = tmp_path / 'CITATION.cff'
    yaml.dump(cff_yml, cff_file)
    return cff_file


def test_convert_cff_to_codemeta(valid_minimal_cff, codemeta):
    actual_result = harvest._convert_cff_to_codemeta(valid_minimal_cff.read_text())
    assert codemeta == actual_result


def test_get_single_cff(tmp_path):
    assert harvest._get_single_cff(tmp_path) is None
    single_cff = tmp_path / 'CITATION.cff'
    single_cff.touch()
    assert harvest._get_single_cff(tmp_path) == single_cff


def test_validate_success(valid_minimal_cff):
    cff_dict = harvest._load_cff_from_file(valid_minimal_cff.read_text())
    assert harvest._validate(pathlib.Path("foobar"), cff_dict)


def test_validate_fail():
    assert harvest._validate(pathlib.Path("foobar"), {}) is False


# Regression test for https://github.com/hermes-hmc/workflow/issues/112

@pytest.fixture
def codemeta_without_email():
    return {
      "@context": "https://doi.org/10.5063/schema/codemeta-2.0",
      "@type": "SoftwareSourceCode",
      "author": [
        {
          "@type": "Person",
          "familyName": "Author"
        },
        {
          "@type": "Person",
          "familyName": "Second"
        },
        {
          "@type": "Person",
          "familyName": "Third"
        }
      ],
      "name": "Title"
    }


@pytest.fixture
def codemeta_with_email(codemeta_without_email):
    author_emails = {"Author": "email@example.com", "Second": "email2@example.com", "Third": "email3@example.com"}
    for author in codemeta_without_email["author"]:
        author["email"] = author_emails[author["familyName"]]
    return codemeta_without_email


@pytest.fixture
def valid_minimal_cff_with_email(tmp_path):
    cff = """\
    cff-version: 1.2.0
    authors:
      - family-names: Author
        email: email@example.com
      - family-names: Second
        email: email2@example.com
      - family-names: Third
        email: email3@example.com
    message: Message
    title: Title
    """
    yaml = YAML()
    return yaml.load(cff)


def test_convert_cff_to_codemeta_with_email(valid_minimal_cff_with_email, codemeta_with_email):
    actual_result = harvest._patch_author_emails(valid_minimal_cff_with_email, codemeta_with_email)
    assert codemeta_with_email == actual_result
