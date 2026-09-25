"""Write the parsed governed severity definition as review JSON."""

from __future__ import annotations

import argparse
from pathlib import Path

from .severity_definition import parse_severity_definition, severity_definition_to_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("definition", type=Path)
    parser.add_argument("--ebit", type=float, required=True, help="Preceding-year EBIT in S$ millions (explicit config input).")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    definition = parse_severity_definition(arguments.definition, arguments.ebit)
    result = severity_definition_to_json(definition)
    if arguments.output is None:
        print(result)
        return
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(result + "\n", encoding="utf-8")
    print(arguments.output)


if __name__ == "__main__":
    main()
