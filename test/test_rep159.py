# Software License Agreement (BSD License)
#
# Copyright (c) 2026, Open Source Robotics Foundation, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#  * Neither the name of Open Source Robotics Foundation, Inc. nor
#    the names of its contributors may be used to endorse or promote
#    products derived from this software without specific prior
#    written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

# REP 159 (distribution extensions): a distribution in format 3 can extend
# parent distributions, as a 'binary_import' (it uses the parent's binaries)
# or a 'source_rebuild' (it rebuilds the parent's packages). The rosdistro
# library merges the parents into the child and records in 'origin_distro'
# which distribution releases each repository.

import logging

import pytest
from rosdistro import get_distribution_file, get_index
from rosdistro.distribution import Distribution
from rosdistro.distribution_file import DistributionFile

from rosinstall_generator import generator
from rosinstall_generator.cli import main
from rosinstall_generator.generator import generate_rosinstall

requires_rep159 = pytest.mark.skipif(
    not hasattr(DistributionFile, 'merge_extends'),
    reason='needs the REP 159 rosdistro (distribution format 3 with extends)')

# package (one per repository) -> its dependencies
PACKAGES = {
    'base_core': [],
    'base_msgs': ['base_core'],
    'child_lib': ['base_msgs'],
    'child_app': ['child_lib', 'base_core'],
}
PARENT_PACKAGES = ['base_core', 'base_msgs']
CHILD_PACKAGES = ['child_app', 'child_lib']


def _release(name):
    return [
        '  %s:' % name,
        '    release:',
        '      tags:',
        '        release: release/base/{package}/{version}',
        '      url: https://example.com/%s-release.git' % name,
        '      version: 1.0.0-1',
        '    source:',
        '      type: git',
        '      url: https://example.com/%s.git' % name,
        '      version: main',
    ]


def _distribution(names, extends=None):
    lines = ['%YAML 1.1', '---']
    if extends:
        lines += [
            'extends:',
            '- distro_name: base',
            '  extension_method: %s' % extends,
        ]
    lines.append('repositories:')
    for name in names:
        lines += _release(name)
    lines += ['type: distribution', 'version: %d' % (3 if extends else 2)]
    return '\n'.join(lines) + '\n'


def _package_xml(name):
    depends = ''.join('  <depend>%s</depend>\n' % d for d in PACKAGES[name])
    return (
        '<?xml version="1.0"?>\n'
        '<package format="2">\n'
        '  <name>%s</name>\n'
        '  <version>1.0.0</version>\n'
        '  <description>%s</description>\n'
        '  <maintainer email="maintainer@example.com">Maintainer</maintainer>\n'
        '  <license>BSD</license>\n'
        '%s'
        '</package>\n') % (name, name, depends)


@pytest.fixture(scope='module')
def index_url(tmp_path_factory):
    path = tmp_path_factory.mktemp('rep159_index')
    distributions = {
        'base': _distribution(PARENT_PACKAGES),
        'bin_child': _distribution(CHILD_PACKAGES, 'binary_import'),
        'src_child': _distribution(CHILD_PACKAGES, 'source_rebuild'),
    }
    index = ['%YAML 1.1', '---', 'distributions:']
    for name, content in distributions.items():
        (path / ('%s.yaml' % name)).write_text(content)
        index += [
            '  %s:' % name,
            '    distribution: [%s.yaml]' % name,
            '    distribution_status: active',
            '    distribution_type: ros2',
            '    python_version: 3',
        ]
    index += ['type: index', 'version: 4']
    (path / 'index.yaml').write_text('\n'.join(index) + '\n')
    return (path / 'index.yaml').as_uri()


@pytest.fixture
def use_distro(index_url, monkeypatch):
    """Make the generator use one distribution of the test index."""
    def use(name):
        dist_file = get_distribution_file(get_index(index_url), name)
        distro = Distribution(
            dist_file,
            manifest_providers=[lambda _dist, _repo, pkg: _package_xml(pkg)])
        monkeypatch.setattr(generator, '_wet_distro', distro)
    return use


def _names(rosinstall_data):
    return {list(entry.values())[0]['local-name'] for entry in rosinstall_data}


def test_parent_distribution_is_unchanged(use_distro):
    use_distro('base')
    data = generate_rosinstall('base', ['base_msgs'], deps=True)
    assert _names(data) == {'base_msgs', 'base_core'}


@requires_rep159
def test_deps_stop_at_binary_import_parent(use_distro):
    use_distro('bin_child')
    data = generate_rosinstall('bin_child', ['child_app'], deps=True)
    assert _names(data) == {'child_app', 'child_lib'}


@requires_rep159
def test_deps_walk_into_source_rebuild_parent(use_distro):
    use_distro('src_child')
    data = generate_rosinstall('src_child', ['child_app'], deps=True)
    assert _names(data) == set(PACKAGES)


@requires_rep159
def test_deps_include_binary_import_parents_on_request(use_distro):
    use_distro('bin_child')
    data = generate_rosinstall(
        'bin_child', ['child_app'], deps=True,
        include_binary_import_parents=True)
    assert _names(data) == set(PACKAGES)


@requires_rep159
def test_named_parent_package_is_kept(use_distro):
    use_distro('bin_child')
    data = generate_rosinstall('bin_child', ['child_lib', 'base_msgs'])
    assert _names(data) == {'child_lib', 'base_msgs'}


@requires_rep159
def test_named_parent_package_keeps_no_dependencies(use_distro, caplog):
    use_distro('bin_child')
    with caplog.at_level(logging.INFO, logger='rosinstall_generator'):
        data = generate_rosinstall('bin_child', ['base_msgs'], deps=True)
    assert _names(data) == {'base_msgs'}
    assert 'without their dependencies: base_msgs' in caplog.text


@requires_rep159
def test_all_selects_own_packages(use_distro):
    use_distro('bin_child')
    assert _names(generate_rosinstall('bin_child', ['ALL'])) == \
        set(CHILD_PACKAGES)
    assert _names(generate_rosinstall(
        'bin_child', ['ALL'], include_binary_import_parents=True)) == \
        set(PACKAGES)


@requires_rep159
def test_repos_all_selects_own_repositories(use_distro):
    use_distro('bin_child')
    data = generate_rosinstall('bin_child', [], repo_names=['ALL'])
    assert _names(data) == set(CHILD_PACKAGES)


@requires_rep159
def test_all_of_source_rebuild_child_keeps_parent_packages(use_distro):
    use_distro('src_child')
    assert _names(generate_rosinstall('src_child', ['ALL'])) == set(PACKAGES)


@requires_rep159
def test_logs_the_parent_packages_left_out(use_distro, caplog):
    use_distro('bin_child')
    with caplog.at_level(logging.INFO, logger='rosinstall_generator'):
        generate_rosinstall('bin_child', ['child_app'], deps=True)
    assert "'bin_child'" in caplog.text
    assert 'binary_import parents (base)' in caplog.text
    assert 'base_core, base_msgs' in caplog.text
    assert '--include-binary-import-parents' in caplog.text


@requires_rep159
def test_cli_option(use_distro, capsys):
    use_distro('bin_child')
    args = ['--rosdistro', 'bin_child', 'child_app', '--deps',
            '--format', 'repos']
    assert main(args) == 0
    out = capsys.readouterr().out
    assert 'child_lib' in out and 'base_core' not in out

    assert main(args + ['--include-binary-import-parents']) == 0
    out = capsys.readouterr().out
    assert 'child_lib' in out and 'base_core' in out
