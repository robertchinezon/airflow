# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import annotations

import ast
import itertools
import os
import re
from collections.abc import Iterable
from glob import glob
from pathlib import Path
from typing import Any

from docs.exts.docs_build.docs_builder import ALL_PROVIDER_YAMLS
from docs.exts.docs_build.errors import DocBuildError

ROOT_PROJECT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.realpath(__file__)), os.pardir, os.pardir, os.pardir)
)
ROOT_PACKAGE_DIR = os.path.join(ROOT_PROJECT_DIR, "airflow")
DOCS_DIR = os.path.join(ROOT_PROJECT_DIR, "docs")
PROVIDERS_DIR = os.path.join(ROOT_PROJECT_DIR, "providers")


def find_existing_guide_operator_names(src_dir_pattern: str) -> set[str]:
    """
    Find names of existing operators.
    :return names of existing operators.
    """
    operator_names = set()

    paths = glob(src_dir_pattern, recursive=True)
    for path in paths:
        with open(path) as f:
            operator_names |= set(re.findall(".. _howto/operator:(.+?):", f.read()))

    return operator_names


def extract_ast_class_def_by_name(ast_tree, class_name):
    """
    Extracts class definition by name

    :param ast_tree: AST tree
    :param class_name: name of the class.
    :return: class node found
    """
    for node in ast.walk(ast_tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node

    return None


def _generate_missing_guide_error(path, line_no, operator_name):
    return DocBuildError(
        file_path=path,
        line_no=line_no,
        message=(
            f"Link to the guide is missing in operator's description: {operator_name}.\n"
            f"Please add link to the guide to the description in the following form:\n"
            f"\n"
            f".. seealso::\n"
            f"    For more information on how to use this operator, take a look at the guide:\n"
            f"    :ref:`howto/operator:{operator_name}`\n"
        ),
    )


def check_guide_links_in_operator_descriptions() -> list[DocBuildError]:
    """Check if there are links to guides in operator's descriptions."""
    build_errors = []

    build_errors.extend(
        _check_missing_guide_references(
            operator_names=find_existing_guide_operator_names(
                f"{DOCS_DIR}/apache-airflow/howto/operator/**/*.rst"
            ),
            python_module_paths=itertools.chain(
                glob(f"{ROOT_PACKAGE_DIR}/operators/*.py"),
                glob(f"{ROOT_PACKAGE_DIR}/sensors/*.py"),
            ),
        )
    )

    for provider in ALL_PROVIDER_YAMLS:
        operator_names = {
            *find_existing_guide_operator_names(f"{DOCS_DIR}/{provider['package-name']}/operators/**/*.rst"),
            *find_existing_guide_operator_names(f"{DOCS_DIR}/{provider['package-name']}/operators.rst"),
        }

        # Extract all potential python modules that can contain operators
        python_module_paths = itertools.chain(
            glob(f"{provider['package-dir']}/**/operators/*.py", recursive=True),
            glob(f"{provider['package-dir']}/**/sensors/*.py", recursive=True),
            glob(f"{provider['package-dir']}/**/transfers/*.py", recursive=True),
        )

        build_errors.extend(
            _check_missing_guide_references(
                operator_names=operator_names, python_module_paths=python_module_paths
            )
        )

    return build_errors


def _check_missing_guide_references(operator_names, python_module_paths) -> list[DocBuildError]:
    build_errors = []

    for py_module_path in python_module_paths:
        with open(py_module_path) as f:
            py_content = f.read()

        if "This module is deprecated" in py_content:
            continue
        for existing_operator in operator_names:
            if f"class {existing_operator}" not in py_content:
                continue
            # This is a potential file with necessary class definition.
            # To make sure it's a real Python class definition, we build AST tree
            ast_tree = ast.parse(py_content)
            class_def = extract_ast_class_def_by_name(ast_tree, existing_operator)

            if class_def is None:
                continue

            docstring = ast.get_docstring(class_def)
            if docstring:
                if "This class is deprecated." in docstring:
                    continue

                if f":ref:`howto/operator:{existing_operator}`" in docstring:
                    continue

            build_errors.append(
                _generate_missing_guide_error(py_module_path, class_def.lineno, existing_operator)
            )
    return build_errors


def assert_file_not_contains(
    *, file_path: str, pattern: str, message: str | None = None
) -> DocBuildError | None:
    """
    Asserts that file does not contain the pattern. Return message error if it does.

    :param file_path: file
    :param pattern: pattern
    :param message: message to return
    """
    return _extract_file_content(file_path, message, pattern, False)


def assert_file_contains(*, file_path: str, pattern: str, message: str | None = None) -> DocBuildError | None:
    """
    Asserts that file does contain the pattern. Return message error if it does not.

    :param file_path: file
    :param pattern: pattern
    :param message: message to return
    """
    return _extract_file_content(file_path, message, pattern, True)


def _extract_file_content(file_path: str, message: str | None, pattern: str, expected_contain: bool):
    if not message:
        message = f"Pattern '{pattern}' could not be found in '{file_path}' file."
    with open(file_path, "rb", 0) as doc_file:
        pattern_compiled = re.compile(pattern)
        found = False
        for num, line in enumerate(doc_file, 1):
            line_decode = line.decode()
            result = re.search(pattern_compiled, line_decode)
            if not expected_contain and result:
                return DocBuildError(file_path=file_path, line_no=num, message=message)
            elif expected_contain and result:
                found = True

        if expected_contain and not found:
            return DocBuildError(file_path=file_path, line_no=None, message=message)
    return None


def filter_file_list_by_pattern(file_paths: Iterable[str], pattern: str) -> list[str]:
    """
    Filters file list to those that content matches the pattern
    :param file_paths: file paths to check
    :param pattern: pattern to match
    :return: list of files matching the pattern
    """
    output_paths = []
    pattern_compiled = re.compile(pattern)
    for file_path in file_paths:
        with open(file_path, "rb", 0) as text_file:
            text_file_content = text_file.read().decode()
            if re.findall(pattern_compiled, text_file_content):
                output_paths.append(file_path)
    return output_paths


def find_modules(deprecated_only: bool = False) -> set[str]:
    """
    Finds all modules.
    :param deprecated_only: whether only deprecated modules should be found.
    :return: set of all modules found
    """
    file_paths = glob(f"{ROOT_PACKAGE_DIR}/**/*.py", recursive=True)
    # Exclude __init__.py
    file_paths = [f for f in file_paths if not f.endswith("__init__.py")]
    if deprecated_only:
        file_paths = filter_file_list_by_pattern(file_paths, r"This module is deprecated.")
    # Make path relative
    file_paths = [os.path.relpath(f, ROOT_PROJECT_DIR) for f in file_paths]
    # Convert filename to module
    modules_names = {file_path.rpartition(".")[0].replace("/", ".") for file_path in file_paths}
    return modules_names


def check_exampleinclude_for_example_dags() -> list[DocBuildError]:
    """Checks all exampleincludes for example dags."""
    all_docs_files = glob(f"{DOCS_DIR}/**/*.rst", recursive=True)
    build_errors = []
    for doc_file in all_docs_files:
        build_error = assert_file_not_contains(
            file_path=doc_file,
            pattern=r"literalinclude::.+(?:example_dags|tests/system/)",
            message=(
                "literalinclude directive is prohibited for example dags. \n"
                "You should use the exampleinclude directive to include example dags."
            ),
        )
        if build_error:
            build_errors.append(build_error)
    return build_errors


def check_enforce_code_block() -> list[DocBuildError]:
    """Checks all code:: blocks."""
    all_docs_files = glob(f"{DOCS_DIR}/**/*.rst", recursive=True)
    build_errors = []
    for doc_file in all_docs_files:
        build_error = assert_file_not_contains(
            file_path=doc_file,
            pattern=r"^.. code::",
            message=(
                "We recommend using the code-block directive instead of the code directive. "
                "The code-block directive is more feature-full."
            ),
        )
        if build_error:
            build_errors.append(build_error)
    return build_errors


def find_example_dags(provider_dir):
    system_tests_dir = provider_dir.replace(f"{ROOT_PACKAGE_DIR}/", "")
    yield from glob(f"{provider_dir}/**/*example_dags", recursive=True)
    yield from glob(f"{ROOT_PROJECT_DIR}/tests/system/{system_tests_dir}/*/", recursive=True)


def get_indexfile(provider: dict[str, Any]) -> Path:
    package_name = provider["package-name"]
    provider_id = provider["package-name"].replace("apache-airflow-providers-", "").replace("-", ".")
    candidate = Path(PROVIDERS_DIR).joinpath(*provider_id.split(".")) / "docs" / "index.rst"
    if candidate.exists():
        return candidate
    raise ValueError(f"The index.rst for {package_name} does not exist at {candidate}")


def check_pypi_repository_in_provider_tocs() -> list[DocBuildError]:
    """Checks that each documentation for provider distributions has a link to PyPI files in the TOC."""
    build_errors = []
    for provider in ALL_PROVIDER_YAMLS:
        doc_file_path = get_indexfile(provider)
        expected_text = f"PyPI Repository <https://pypi.org/project/{provider['package-name']}/>"
        build_error = assert_file_contains(
            file_path=doc_file_path.as_posix(),
            pattern=re.escape(expected_text),
            message=(
                f"A link to the PyPI in table of contents is missing. Can you please add it?\n\n"
                f"    {expected_text}"
            ),
        )
        if build_error:
            build_errors.append(build_error)

    return build_errors


def run_all_check(disable_provider_checks: bool = False) -> list[DocBuildError]:
    """Run all checks from this module"""
    general_errors = []
    general_errors.extend(check_guide_links_in_operator_descriptions())
    general_errors.extend(check_enforce_code_block())
    general_errors.extend(check_exampleinclude_for_example_dags())
    if not disable_provider_checks:
        general_errors.extend(check_pypi_repository_in_provider_tocs())
    return general_errors
