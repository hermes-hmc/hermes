# SPDX-FileCopyrightText: 2020 Caltech
# SPDX-FileCopyrightText: 2023 Forschungszentrum Jülich GmbH
#
# SPDX-License-Identifier: LicenseRef-BSD-Caltech

# SPDX-FileContributor: Tom Morrell
# SPDX-FileContributor: Oliver Bertuch

import copy
from pyld import jsonld

# Original source at https://github.com/caltechlibrary/convert_codemeta/blob/337e39338bcce4f0f201fe03500061ab14b2ae5c/convert_codemeta/validate.py

def validate_codemeta(json: dict) -> bool:
    """Check whether a codemeta json object is valid"""
    try:
        context = json["@context"]
    except:
        print("Not a jsonld file")
        return False
    if context == "https://doi.org/10.5063/schema/codemeta-2.0":
        # Temp replacement for https resolution issues for schema.org
        context = "https://raw.githubusercontent.com/caltechlibrary/convert_codemeta/main/codemeta.jsonld"
        json["@context"] = context

    cp = copy.deepcopy(json)
    # Expand and contract to check mapping
    cp = jsonld.expand(cp)
    cp = jsonld.compact(cp, context)
    keys = cp.keys()
    # Using len because @type elements get returned as type
    same = len(set(keys)) == len(set(json.keys()))
    if not same:
        print("Unsupported terms in codemeta file")
        diff = set(json.keys() - set(keys))
        if "@type" in diff:
            diff.remove("@type")
        print(sorted(diff))
    fail = ":" in keys
    if fail:
        print("Not in schema")
        for k in keys:
            if ":" in k:
                print(k)
    return same and not fail